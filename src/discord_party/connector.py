"""discord.py Gateway plus single-attempt HTTP delivery and an injected engine."""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord

from .runtime import Runtime, SyntheticEngine
from .state import Event, NotReady, State

log = logging.getLogger('discord_party')


def synchronization_reason(error):
    """Return a bounded reason code without logging Discord content or credentials."""
    known = {
        'Discord authorization or channel unavailable': 'discord_authorization',
        'Pinned rules could not be checked': 'room_rules_unavailable',
        'Owner-pinned room rules required before native pilot': 'room_rules_missing',
        'Bot identity or text channel mismatch': 'identity_or_channel',
        'Pilot bot must belong only to the dedicated social server': 'guild_scope',
        'Bot membership unavailable': 'membership',
        'Missing party permissions': 'permissions_missing',
        'Excessive bot permissions': 'permissions_excessive',
        'Bot can see an unregistered channel': 'channel_scope',
        'History gap exceeds catch-up limit; manual reconciliation needed': 'history_gap',
        'Disconnected during history reconciliation': 'disconnected_during_history',
    }
    if isinstance(error, NotReady):
        return known.get(str(error), 'not_ready_other')
    if isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError)):
        return 'discord_api_or_network'
    return 'unexpected'


def to_event(message):
    return Event(str(message.id), str(message.guild.id) if message.guild else '',
                 str(message.channel.id), str(message.author.id), message.content,
                 message.created_at.isoformat(), datetime.now(timezone.utc).isoformat(),
                 bot=message.author.bot, webhook_id=str(message.webhook_id) if message.webhook_id else None,
                 message_type=message.type.value)


class HTTPTransport:
    def __init__(self, session, token, registry):
        self.session, self.token, self.registry = session, token, registry
        self.retry_at = 0.0

    async def send(self, row):
        # discord.py's generic HTTP path retries some errors internally. Chat sends use a
        # single explicit POST so a delayed retry cannot bypass a newly received stop.
        if asyncio.get_running_loop().time() < self.retry_at:
            return None
        payload = {'content': row['content'], 'nonce': row['nonce'], 'enforce_nonce': True,
                   'allowed_mentions': {'parse': [], 'replied_user': False}, 'flags': 4}
        url = f'https://discord.com/api/v10/channels/{self.registry.channel_id}/messages'
        async with self.session.post(url, json=payload,
                                     headers={'Authorization': f'Bot {self.token}'},
                                     timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as response:
            if response.status == 429:
                data = await response.json()
                self.retry_at = asyncio.get_running_loop().time() + max(1, float(data['retry_after']))
                return None
            if response.status in (400, 401, 403, 404):
                if response.status in (401, 403, 404):
                    raise NotReady('Discord authorization or channel unavailable')
                return None
            if response.status != 200:
                raise RuntimeError('Delivery outcome unknown')
            data = await response.json()
            return {'message_id': str(data['id']), 'author_id': str(data['author']['id']),
                        'channel_id': str(data['channel_id']), 'nonce': str(data.get('nonce', ''))}


class PartyClient(discord.Client):
    def __init__(self, state: State, token: str, *, engine=None):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=100)
        self.party_state, self.party_token = state, token
        self.engine = engine or SyntheticEngine()
        self.runtime = None
        self.http_session = None
        self.sync_lock = asyncio.Lock()
        self.syncing = True
        self.buffer = []
        self.connection_version = 0

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        self.runtime = Runtime(self.party_state, self.engine,
                               HTTPTransport(self.http_session, self.party_token, self.party_state.registry))
        self.runtime.start()

    async def on_disconnect(self):
        self.connection_version += 1
        self.syncing = True
        self.buffer.clear()
        self.runtime.disconnect()

    async def on_ready(self):
        await self.synchronize()

    async def on_resumed(self):
        await self.synchronize()

    async def check_room_rules(self):
        if isinstance(self.engine, SyntheticEngine):
            return
        reg = self.party_state.registry
        async with self.http_session.get(
                f'https://discord.com/api/v10/channels/{reg.channel_id}/pins',
                headers={'Authorization': f'Bot {self.party_token}'},
                timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as response:
            if response.status != 200:
                raise NotReady('Pinned rules could not be checked')
            data = await response.json()
        messages = data if isinstance(data, list) else [item['message'] for item in data.get('items', [])]
        if not any(str(m.get('author', {}).get('id')) in reg.human_ids
                   and all(text in m.get('content', '') for text in ('先停一下', '繼續聊', '10', '文字'))
                   for m in messages):
            raise NotReady('Owner-pinned room rules required before native pilot')

    def check_permissions(self, channel):
        registry = self.party_state.registry
        if (not self.user or str(self.user.id) != registry.self_id
                or not isinstance(channel, discord.TextChannel)
                or str(channel.guild.id) != registry.guild_id):
            raise NotReady('Bot identity or text channel mismatch')
        if {str(g.id) for g in self.guilds} != {registry.guild_id}:
            raise NotReady('Pilot bot must belong only to the dedicated social server')
        member = channel.guild.me
        if member is None:
            raise NotReady('Bot membership unavailable')
        perms = channel.permissions_for(member)
        needed = ('view_channel', 'send_messages', 'read_message_history')
        if not all(getattr(perms, name) for name in needed):
            raise NotReady('Missing party permissions')
        # Effective permissions include every role; Administrator bypasses channel denies.
        if any(getattr(perms, name) for name in (
                'administrator', 'manage_guild', 'manage_roles', 'manage_channels',
                'manage_messages', 'mention_everyone', 'attach_files', 'connect', 'speak')):
            raise NotReady('Excessive bot permissions')
        if any(c.id != channel.id and c.permissions_for(member).view_channel for c in channel.guild.channels):
            raise NotReady('Bot can see an unregistered channel')

    async def synchronize(self):
        async with self.sync_lock:
            version = self.connection_version
            self.syncing = True
            self.runtime.disconnect()
            try:
                channel = await self.fetch_channel(int(self.party_state.registry.channel_id))
                self.check_permissions(channel)
                await self.check_room_rules()
                # Capture a server history boundary. Paginate every missed message, including
                # controls. Bounded catch-up fails closed instead of silently skipping a gap.
                latest = [m async for m in channel.history(limit=1)]
                boundary = latest[0].id if latest else None
                cursor = self.party_state.snapshot()['cursor']
                if boundary and (cursor is None or boundary > int(cursor)):
                    count = 0
                    async for message in channel.history(limit=10001, oldest_first=True,
                            after=discord.Object(id=int(cursor)) if cursor else None,
                            before=discord.Object(id=boundary+1)):
                        count += 1
                        if count > 10000:
                            raise NotReady('History gap exceeds catch-up limit; manual reconciliation needed')
                        self.reconcile(message)
                        self.runtime.receive(to_event(message), historical=True)
                # Unknown sends can precede the cursor. Search a bounded recent window by
                # exact own author + nonce only. Absence is NOT proof of non-delivery.
                if self.party_state.unresolved():
                    async for message in channel.history(limit=1000):
                        self.reconcile(message)
                if version != self.connection_version or not self.is_ready():
                    raise NotReady('Disconnected during history reconciliation')
                for message in sorted(self.buffer, key=lambda m: m.id):
                    self.reconcile(message)
                    self.runtime.receive(to_event(message), historical=True)
                self.buffer.clear()
                self.party_state.synchronized()
                self.syncing = False
                log.info('%s_READY awaiting new human message; unknown_sends=%d',
                         'SYNTHETIC' if isinstance(self.engine, SyntheticEngine) else 'NATIVE',
                         len(self.party_state.unresolved()))
            except Exception as error:  # noqa: BLE001 - fail closed; log only bounded reason code
                self.runtime.disconnect()
                log.error('NOT_READY reason=%s', synchronization_reason(error))
                # Stop this new connector only. No model/service/token details in the log.
                await self.close()

    def reconcile(self, message):
        if (message.webhook_id is not None or not message.author.bot or not message.guild
                or str(message.guild.id) != self.party_state.registry.guild_id):
            return
        for row in self.party_state.unresolved():
            self.party_state.delivered(row['request_id'], message_id=str(message.id),
                author_id=str(message.author.id), channel_id=str(message.channel.id), nonce=str(message.nonce))

    async def on_message(self, message):
        registry = self.party_state.registry
        if (not message.guild or str(message.guild.id) != registry.guild_id
                or str(message.channel.id) != registry.channel_id):
            return
        if self.syncing:
            if len(self.buffer) >= 10000:
                self.runtime.disconnect()
                await self.close()
            else:
                self.buffer.append(message)
            return
        self.reconcile(message)
        self.runtime.receive(to_event(message))

    async def on_guild_channel_update(self, before, after):
        # Permissions can change while connected. Recheck all effective channel permissions.
        await self.synchronize()

    async def on_guild_role_update(self, before, after):
        await self.synchronize()

    async def close(self):
        if self.runtime:
            await self.runtime.stop()
        if self.http_session:
            await self.http_session.close()
        await super().close()


def read_token(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise NotReady('Bot token must be a private regular file (chmod 600)')
    token = path.read_text().strip()
    if not token or any(c.isspace() for c in token):
        raise NotReady('Invalid token file')
    return token
