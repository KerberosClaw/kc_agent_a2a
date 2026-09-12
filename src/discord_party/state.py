"""Durable per-bot accounting. All transitions are synchronous SQLite transactions.

The caller owns one event loop and one State for the lifetime of the connector.
Never hold a transaction over network or model I/O.
"""
import fcntl
import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class NotReady(RuntimeError):
    pass


def snowflake(value: str) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError('IDs must be decimal strings')
    number = int(value)
    if not 0 < number < 2**64 or str(number) != value:
        raise ValueError('Invalid Discord ID')
    return number


@dataclass(frozen=True)
class Registry:
    guild_id: str
    channel_id: str
    human_ids: tuple[str, ...]
    bot_owners: dict[str, str]
    self_id: str
    protocol: int = 1

    def __post_init__(self):
        ids = [self.guild_id, self.channel_id, self.self_id, *self.human_ids,
               *self.bot_owners, *self.bot_owners.values()]
        for value in ids:
            snowflake(value)
        if (self.protocol != 1 or not 1 <= len(self.human_ids) <= 2
                or len(set(self.human_ids)) != len(self.human_ids) or len(self.bot_owners) != 2
                or self.self_id not in self.bot_owners
                or set(self.human_ids) & self.bot_owners.keys()
                or not set(self.bot_owners.values()) <= set(self.human_ids)):
            raise ValueError('Pilot requires one or two distinct humans and two registered bots')

    def canonical(self):
        return json.dumps(asdict(self), sort_keys=True)


@dataclass(frozen=True)
class Event:
    message_id: str
    guild_id: str
    channel_id: str
    author_id: str
    content: str
    created_at: str
    received_at: str
    bot: bool = False
    webhook_id: str | None = None
    message_type: int = 0
    kind: str = 'create'

    def role(self, registry: Registry):
        if (self.guild_id != registry.guild_id or self.channel_id != registry.channel_id
                or self.webhook_id is not None or self.message_type not in (0, 19)
                or self.kind != 'create' or not self.content.strip()):
            return None
        snowflake(self.message_id)
        if self.bot and self.author_id in registry.bot_owners:
            return 'self' if self.author_id == registry.self_id else 'peer'
        if not self.bot and self.author_id in registry.human_ids:
            return 'human'
        return None


SCHEMA = '''
CREATE TABLE config (registry TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE state (
 id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT, remaining INTEGER NOT NULL CHECK(remaining BETWEEN 0 AND 10),
 paused INTEGER NOT NULL, control_id TEXT, participation TEXT NOT NULL,
 ready INTEGER NOT NULL, armed INTEGER NOT NULL, grant_version TEXT NOT NULL,
 cursor TEXT, last_sent REAL NOT NULL DEFAULT 0);
CREATE TABLE events (
 channel_id TEXT NOT NULL, message_id TEXT NOT NULL, author_id TEXT NOT NULL,
 created_at TEXT NOT NULL, received_at TEXT NOT NULL, role TEXT NOT NULL,
 kind TEXT NOT NULL, content TEXT NOT NULL, historical INTEGER NOT NULL,
 attempted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(channel_id,message_id));
CREATE TABLE attempts (
 request_id TEXT PRIMARY KEY, epoch TEXT NOT NULL, source_id TEXT NOT NULL,
 grant_version TEXT NOT NULL, status TEXT NOT NULL);
CREATE UNIQUE INDEX one_generation ON attempts((1)) WHERE status='generating';
CREATE TABLE outbox (
 request_id TEXT PRIMARY KEY REFERENCES attempts(request_id), epoch TEXT NOT NULL,
 nonce TEXT UNIQUE NOT NULL, content TEXT NOT NULL, closing INTEGER NOT NULL,
 status TEXT NOT NULL, message_id TEXT UNIQUE, counted INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX one_reservation ON outbox((1)) WHERE status IN ('queued','submitted','unknown');
'''


class State:
    def __init__(self, root: Path, registry: Registry, *, initialize=False):
        self.root = Path(root)
        self.registry = registry
        if self.root.is_symlink():
            raise NotReady('State directory cannot be a symlink')
        if initialize:
            # Explicit first installation only; never recreate a missing DB in an existing directory.
            self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.lock = None
        self.db = None
        try:
            self.lock = (self.root / 'instance.lock').open('a')
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            dbpath = self.root / 'party.sqlite3'
            if dbpath.is_symlink() or (not initialize and not dbpath.is_file()):
                raise NotReady('State missing: restore and reconcile; do not initialize again')
            self.db = sqlite3.connect(dbpath if initialize else f'file:{dbpath}?mode=rw', uri=not initialize)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA synchronous=FULL')
            if initialize:
                self.db.executescript(SCHEMA)
                with self.db:
                    self.db.execute('INSERT INTO config VALUES (?,1)', (registry.canonical(),))
                    self.db.execute("INSERT INTO state VALUES (1,NULL,0,0,NULL,'listening',0,0,'synthetic-v1',NULL,0)")
                dbpath.chmod(0o600)
            if (self.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                    or tuple(self.db.execute('SELECT registry,version FROM config').fetchone()) != (registry.canonical(), 1)):
                raise NotReady('State corrupt or registry changed; restore/reconcile explicitly')
            self.disconnected()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def add_human(self, registry: Registry):
        """Explicit maintenance migration under the instance lock; never resets a ledger.

        The installer must also update its config and host binding while stopped.
        Any incomplete migration remains fail-closed at the existing startup checks.
        """
        before, after = asdict(self.registry), asdict(registry)
        old_humans, new_humans = before.pop('human_ids'), after.pop('human_ids')
        if (before != after or len(new_humans) != len(old_humans) + 1
                or not set(old_humans) < set(new_humans)):
            raise NotReady('Only one additional human in the existing room is allowed')
        with self.db:
            self._invalidate()
            self.db.execute('UPDATE state SET ready=0,armed=0')
            self.db.execute('UPDATE config SET registry=?', (registry.canonical(),))
        self.registry = registry

    def snapshot(self):
        return dict(self.db.execute('SELECT * FROM state WHERE id=1').fetchone())

    def _invalidate(self):
        self.db.execute("UPDATE attempts SET status='cancelled' WHERE status='generating'")
        self.db.execute("UPDATE outbox SET status='cancelled' WHERE status='queued'")
        self.db.execute('UPDATE events SET attempted=1')

    def disconnected(self):
        with self.db:
            self._invalidate()
            self.db.execute('UPDATE state SET ready=0,armed=0')
            self.db.execute("UPDATE outbox SET status='unknown' WHERE status='submitted'")

    def synchronized(self):
        # Caller must first retrieve complete history through a captured boundary.
        with self.db:
            self.db.execute('UPDATE state SET ready=1,armed=0')
            self.db.execute('UPDATE events SET attempted=1')

    def set_grant(self, version):
        """Invalidate pending work on authorization change without refreshing quota."""
        if not isinstance(version, str) or not version:
            raise NotReady('Missing grant version')
        with self.db:
            if self.snapshot()['grant_version'] != version:
                self._invalidate()
                self.db.execute('UPDATE state SET grant_version=?,armed=0', (version,))

    def ingest(self, event: Event, *, historical=False):
        role = event.role(self.registry)
        if role is None:
            return False
        with self.db:
            inserted = self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,0)', (
                event.channel_id, event.message_id, event.author_id, event.created_at,
                event.received_at, role, event.kind, event.content, int(historical))).rowcount
            if not inserted:
                return False
            state = self.snapshot()
            mid = snowflake(event.message_id)
            if state['cursor'] is None or mid > int(state['cursor']):
                self.db.execute('UPDATE state SET cursor=?', (event.message_id,))
            fresh = state['epoch'] is None or mid > int(state['epoch'])
            live = not historical and bool(state['ready'])
            if role == 'human' and fresh:
                self._invalidate()
                # Historical messages recover control but never replenish spendable quota.
                self.db.execute("UPDATE state SET epoch=?,remaining=?,armed=?,participation='listening'",
                                (event.message_id, 10 if live else 0, int(live)))
                if event.content in ('先停一下', '繼續聊'):
                    self.db.execute('UPDATE state SET paused=?,control_id=?',
                                    (int(event.content == '先停一下'), event.message_id))
            trigger = (live and fresh and role in ('human', 'peer'))
            self.db.execute('UPDATE events SET attempted=? WHERE channel_id=? AND message_id=?',
                            (int(not trigger), event.channel_id, event.message_id))
            return trigger

    def unresolved(self):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM outbox WHERE status IN ('submitted','unknown')")]

    def can_decide(self):
        s = self.snapshot()
        return bool(s['ready'] and s['armed'] and not s['paused'] and s['remaining'] > 0
                    and s['participation'] != 'closed'
                    and not self.db.execute("SELECT 1 FROM outbox WHERE status IN ('queued','submitted','unknown')").fetchone())

    def begin(self):
        with self.db:
            if (not self.can_decide() or self.db.execute(
                    "SELECT 1 FROM attempts WHERE status='generating'").fetchone()):
                return None
            pending = self.db.execute("SELECT message_id FROM events WHERE attempted=0 AND role IN ('human','peer')").fetchall()
            if not pending:
                return None
            source = max((row[0] for row in pending), key=int)
            s = self.snapshot()
            request = uuid.uuid4().hex
            self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?)',
                            (request, s['epoch'], source, s['grant_version'], 'generating'))
            self.db.execute('UPDATE events SET attempted=1')
            self.db.execute("UPDATE state SET participation='active'")
            return request

    def valid_attempt(self, request):
        a = self.db.execute('SELECT * FROM attempts WHERE request_id=?', (request,)).fetchone()
        s = self.snapshot()
        return bool(a and a['epoch'] == s['epoch'] and a['grant_version'] == s['grant_version']
                    and s['ready'] and s['armed'] and not s['paused'] and s['remaining'] > 0
                    and s['participation'] != 'closed')

    def finish(self, request, action, content=''):
        with self.db:
            a = self.db.execute('SELECT * FROM attempts WHERE request_id=?', (request,)).fetchone()
            if not a or a['status'] != 'generating':
                return False
            valid = (action in ('speak', 'pass', 'close') and isinstance(content, str)
                     and len(content) <= 1000 and (action != 'speak' or bool(content.strip()))
                     and (action != 'pass' or not content))
            if not valid or not self.valid_attempt(request):
                self.db.execute("UPDATE attempts SET status='discarded' WHERE request_id=?", (request,))
                return False
            self.db.execute("UPDATE attempts SET status='decided' WHERE request_id=?", (request,))
            if action == 'pass' or not content.strip():
                self.db.execute('UPDATE state SET participation=?', ('closed' if action == 'close' else 'listening',))
                return True
            nonce = str(int.from_bytes(hashlib.sha256(request.encode()).digest()[:8], 'big'))
            self.db.execute('INSERT INTO outbox VALUES (?,?,?,?,?,?,NULL,0)',
                            (request, a['epoch'], nonce, content, int(action == 'close'), 'queued'))
            return True

    def submit(self, request):
        # Call immediately before the HTTP request; no intervening await.
        with self.db:
            row = self.db.execute('SELECT * FROM outbox WHERE request_id=?', (request,)).fetchone()
            if not row or row['status'] != 'queued':
                return None
            if not self.valid_attempt(request):
                self.db.execute("UPDATE outbox SET status='cancelled' WHERE request_id=?", (request,))
                return None
            self.db.execute("UPDATE outbox SET status='submitted' WHERE request_id=?", (request,))
            return dict(row)

    def uncertain(self, request):
        with self.db:
            self.db.execute("UPDATE outbox SET status='unknown' WHERE request_id=? AND status='submitted'", (request,))

    def rejected(self, request):
        # Only use for an authoritative rejection before acceptance, never a timeout/5xx.
        with self.db:
            self.db.execute("UPDATE outbox SET status='failed' WHERE request_id=? AND status='submitted'", (request,))

    def delivered(self, request, *, message_id, author_id, channel_id, nonce):
        snowflake(message_id)
        with self.db:
            row = self.db.execute('SELECT * FROM outbox WHERE request_id=?', (request,)).fetchone()
            if (not row or row['status'] not in ('submitted', 'unknown', 'delivered')
                    or author_id != self.registry.self_id or channel_id != self.registry.channel_id
                    or str(nonce) != row['nonce']):
                return False
            if row['counted']:
                return row['message_id'] == message_id
            self.db.execute("UPDATE outbox SET status='delivered',message_id=?,counted=1 WHERE request_id=?", (message_id, request))
            self.db.execute('UPDATE state SET last_sent=?', (datetime.now(timezone.utc).timestamp(),))
            if self.snapshot()['epoch'] == row['epoch']:
                self.db.execute('UPDATE state SET remaining=remaining-1,participation=?',
                                ('closed' if row['closing'] else 'listening',))
            return True

    def context(self, *, limit=40, max_chars=12000):
        rows = [dict(row) for row in self.db.execute(
            'SELECT message_id,author_id,created_at,role,content FROM events ORDER BY length(message_id) DESC,message_id DESC LIMIT ?', (limit,))]
        result, size = [], 0
        for row in rows:
            row['content'] = row['content'][:2000]
            size += len(row['content'])
            if size > max_chars:
                break
            result.append(row)
        return list(reversed(result))
