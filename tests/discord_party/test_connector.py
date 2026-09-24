import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
from test_state import event, registry

from discord_party.connector import HTTPTransport, PartyClient, read_token, synchronization_reason
from discord_party.runtime import Runtime, SyntheticEngine
from discord_party.state import NotReady, State


def message(mid=100, author=10, *, content='hello', bot=False, nonce=None, channel_id=2, guild_id=1):
    return NS(id=mid, author=NS(id=author,bot=bot), channel=NS(id=channel_id),
              guild=NS(id=guild_id) if guild_id else None, content=content,
              created_at=datetime(2026,9,9,tzinfo=timezone.utc), type=discord.MessageType.default,
              webhook_id=None, nonce=nonce)


class ReasonCodeCase(unittest.TestCase):
    def test_sync_errors_are_bounded_codes(self):
        self.assertEqual(synchronization_reason(NotReady('Missing party permissions')), 'permissions_missing')
        self.assertEqual(synchronization_reason(NotReady('private dynamic detail')), 'not_ready_other')
        self.assertEqual(synchronization_reason(ValueError('secret')), 'unexpected')


class HTTPCase(unittest.IsolatedAsyncioTestCase):
    def transport(self, status=200, data=None):
        response = NS(status=status, json=AsyncMock(return_value=data or {
            'id':'200','author':{'id':'20'},'channel_id':'2','nonce':'123'}))
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=response)
        manager.__aexit__ = AsyncMock(return_value=False)
        session = NS(post=MagicMock(return_value=manager))
        return HTTPTransport(session, 'synthetic-token', registry()), session

    async def test_single_post_has_fixed_nonce_no_mentions_no_embeds(self):
        transport, session = self.transport()
        receipt = await transport.send({'content':'@everyone','nonce':'123'})
        self.assertEqual(receipt['message_id'], '200')
        session.post.assert_called_once()
        kwargs = session.post.call_args.kwargs
        self.assertTrue(kwargs['json']['enforce_nonce'])
        self.assertEqual(kwargs['json']['allowed_mentions'], {'parse':[], 'replied_user':False})
        self.assertEqual(kwargs['json']['flags'], 4)
        self.assertFalse(kwargs['allow_redirects'])

    async def test_rate_limit_does_not_sleep_then_send_after_stop(self):
        transport, session = self.transport(429, {'retry_after':30})
        self.assertIsNone(await transport.send({'content':'probe','nonce':'123'}))
        self.assertIsNone(await transport.send({'content':'probe','nonce':'456'}))
        session.post.assert_called_once()

    async def test_server_failure_never_retries_and_auth_is_not_ready(self):
        for status in (500,502,503,401,403,404):
            transport, session = self.transport(status)
            with self.assertRaises(RuntimeError):
                await transport.send({'content':'probe','nonce':'123'})
            session.post.assert_called_once()


class ConnectorCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name)/'state', registry(), initialize=True)
        self.client = PartyClient(self.state, 'synthetic-token')
        self.client.runtime = Runtime(self.state, SyntheticEngine(), NS(send=AsyncMock()))
        self.client.close = AsyncMock()
        self.client.is_ready = MagicMock(return_value=True)
        self.client.check_permissions = MagicMock()

    async def asyncTearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def history(self, messages):
        async def iterator(**kw):
            subset = messages
            if kw.get('after'):
                subset = [m for m in subset if m.id > kw['after'].id]
            if kw.get('before'):
                subset = [m for m in subset if m.id < kw['before'].id]
            subset = sorted(subset, key=lambda m:m.id, reverse=not kw.get('oldest_first',False))
            for item in subset[:kw['limit']]:
                yield item
        self.client.fetch_channel = AsyncMock(return_value=NS(history=iterator))

    async def test_intents_include_unmentioned_and_peer_messages_exclude_dms(self):
        self.assertTrue(self.client.intents.guilds)
        self.assertTrue(self.client.intents.guild_messages)
        self.assertTrue(self.client.intents.message_content)
        self.assertFalse(self.client.intents.dm_messages)
        self.state.synchronized()
        self.client.syncing = False
        await self.client.on_message(message())
        self.assertEqual(self.state.snapshot()['remaining'], 10)
        await self.client.on_message(message(101,30,bot=True))
        self.assertEqual(len(self.state.context()),2)
        await self.client.on_message(message(102,20,bot=True))
        self.assertEqual(self.state.snapshot()['remaining'],10)
        await self.client.on_message(message(103,channel_id=9))
        await self.client.on_message(message(104,guild_id=None))
        self.assertEqual(len(self.state.context()),3)

    async def test_reconnect_replays_control_before_accepting_new_human(self):
        self.state.synchronized()
        self.state.ingest(event(100))
        self.history([message(100), message(200, content='先停一下'), message(201,30,bot=True)])
        await self.client.synchronize()
        self.assertTrue(self.state.snapshot()['paused'])
        self.assertEqual(self.state.snapshot()['remaining'],0)
        self.assertFalse(self.client.syncing)
        await self.client.on_message(message(300,content='ordinary'))
        self.assertTrue(self.state.snapshot()['paused'])
        self.assertIsNone(self.state.begin())
        await self.client.on_message(message(301,content='繼續聊'))
        self.assertIsNotNone(self.state.begin())

    async def test_buffered_during_sync_is_history_never_fresh_quota(self):
        self.history([message(100)])
        await self.client.on_message(message(200))
        await self.client.synchronize()
        self.assertEqual(self.state.snapshot()['epoch'],'200')
        self.assertEqual(self.state.snapshot()['remaining'],0)
        self.assertIsNone(self.state.begin())

    async def test_unknown_send_found_by_nonce_but_absence_never_retries(self):
        self.state.synchronized()
        self.state.ingest(event(100))
        request = self.state.begin()
        self.state.finish(request,'speak','once')
        row = self.state.submit(request)
        self.state.uncertain(request)
        self.history([message(100)])
        await self.client.synchronize()
        self.assertEqual(len(self.state.unresolved()),1)
        self.history([message(100), message(101,20,bot=True,nonce=row['nonce'])])
        await self.client.synchronize()
        self.assertEqual(self.state.unresolved(),[])
        self.assertEqual(self.state.snapshot()['remaining'],9)

    async def test_unreliable_history_and_disconnect_during_sync_fail_closed(self):
        self.client.fetch_channel = AsyncMock(side_effect=TimeoutError)
        await self.client.synchronize()
        self.assertFalse(self.state.snapshot()['ready'])
        self.client.close.assert_awaited_once()

    async def test_token_must_be_private_and_never_is_loaded_from_repo(self):
        token = Path(self.tmp.name)/'bot.token'
        token.write_text('synthetic-token')
        token.chmod(0o644)
        with self.assertRaises(NotReady):
            read_token(token)
        token.chmod(0o600)
        self.assertEqual(read_token(token),'synthetic-token')
        symlink = token.with_name('linked.token')
        symlink.symlink_to(token)
        with self.assertRaises(NotReady):
            read_token(symlink)

    async def test_effective_permissions_and_other_visible_rooms_are_rejected(self):
        client = PartyClient(self.state, 'synthetic-token')
        guild = MagicMock(spec=discord.Guild)
        guild.id, guild.me = 1, NS(id=20)
        client._connection.user = NS(id=20)
        client._connection._guilds = {1:guild}
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.guild = 2, guild
        perms = discord.Permissions.none()
        perms.view_channel = perms.send_messages = perms.read_message_history = True
        channel.permissions_for.return_value = perms
        guild.channels = [channel]
        client.check_permissions(channel)
        perms.administrator = True
        with self.assertRaises(NotReady):
            client.check_permissions(channel)
        perms.administrator = False
        other = NS(id=3,permissions_for=lambda _:perms)
        guild.channels = [channel,other]
        with self.assertRaises(NotReady):
            client.check_permissions(channel)


if __name__ == '__main__':
    unittest.main()
