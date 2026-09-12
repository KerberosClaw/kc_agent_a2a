"""Incremental external experiences. Publication, reading and saving are separate.

SQLite is the local delivery ledger; published documents are immutable and exported
to the encrypted social repository. No transaction spans a model or Git call.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .state import NotReady


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def decode_result(text):
    # Some native structured-output serializers emit literal newlines in the
    # inner JSON string. Accept those, not arbitrary non-whitespace controls.
    value = json.loads(text, strict=False)
    def check(item):
        if isinstance(item, str) and any(ord(c) < 32 and c not in '\n\r\t' for c in item):
            raise NotReady('Invalid control character in native result')
        if isinstance(item, dict):
            for k, v in item.items(): check(k); check(v)
        elif isinstance(item, list):
            for v in item: check(v)
    check(value)
    return value


def agent_name(agent):
    if agent not in ('agent_a', 'agent_b'):
        raise NotReady('Unknown continuity persona')
    return agent


class Continuity:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / 'continuity.sqlite3'
        if path.is_symlink():
            raise NotReady('Continuity state cannot be a symlink')
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sources(
          seq INTEGER PRIMARY KEY, source_key TEXT NOT NULL, revision TEXT NOT NULL,
          body TEXT NOT NULL, observed REAL NOT NULL, UNIQUE(source_key,revision));
        CREATE TABLE IF NOT EXISTS batches(
          id TEXT PRIMARY KEY, agent TEXT NOT NULL, grant_version TEXT NOT NULL,
          kind TEXT NOT NULL, refs TEXT NOT NULL, status TEXT NOT NULL,
          token TEXT, lease REAL NOT NULL, body TEXT, created REAL NOT NULL,
          error TEXT, failures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS coverage(
          agent TEXT, seq INTEGER REFERENCES sources(seq), batch TEXT REFERENCES batches(id),
          PRIMARY KEY(agent,seq));
        CREATE TABLE IF NOT EXISTS received(
          agent TEXT, batch TEXT REFERENCES batches(id), session TEXT, turn TEXT, at REAL,
          PRIMARY KEY(agent,batch));
        CREATE TABLE IF NOT EXISTS seen(
          agent TEXT, session TEXT, batch TEXT REFERENCES batches(id), PRIMARY KEY(agent,session,batch));
        CREATE TABLE IF NOT EXISTS claims(
          id TEXT PRIMARY KEY, agent TEXT, session TEXT, turn TEXT, refs TEXT, body TEXT,
          state TEXT, created REAL, UNIQUE(agent,session,turn));
        CREATE TABLE IF NOT EXISTS saves(
          id TEXT PRIMARY KEY, agent TEXT, refs TEXT, base TEXT, status TEXT, commit_id TEXT,
          created REAL, manifest TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_save ON saves(agent) WHERE status='open';
        CREATE TABLE IF NOT EXISTS assessed(
          agent TEXT, batch TEXT REFERENCES batches(id), save TEXT REFERENCES saves(id),
          PRIMARY KEY(agent,batch));
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self.db
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def ingest(self, messages, *, now=None):
        """Append revisions, including late arrivals. Never advance by timestamp alone.

        Upstream may explicitly supply a corrected/deleted record. Missing records
        in a partial export are NOT interpreted as deletion.
        """
        now = time.time() if now is None else now
        count = 0
        with self.transaction() as db:
            for message in messages:
                required = ('channel_id', 'message_id', 'author_id', 'created_at', 'content')
                if any(not isinstance(message.get(k), str) for k in required):
                    raise NotReady('Invalid attributed source')
                if datetime.fromisoformat(message['created_at']).utcoffset() is None:
                    raise NotReady('Source timestamp needs timezone')
                if len(message['content']) > 20000:
                    raise NotReady('Oversized source')
                # Only source data; never propagate arbitrary upstream control fields.
                body = {k: message[k] for k in required}
                body['deleted'] = message.get('deleted') is True
                key = body['channel_id'] + '/' + body['message_id']
                revision = fingerprint(body)
                prior = db.execute('SELECT seq FROM sources WHERE source_key=? ORDER BY seq DESC LIMIT 1', (key,)).fetchone()
                body['supersedes_seq'] = prior['seq'] if prior else None
                count += db.execute('INSERT OR IGNORE INTO sources(source_key,revision,body,observed) VALUES(?,?,?,?)',
                                    (key, revision, encode(body), now)).rowcount
        return count

    def reserve(self, agent, grant_version, *, now=None, force=False, idle=1200,
                max_age=7200, max_messages=60, max_chars=24000):
        """Claim one immutable input range; lease/token fence crashed generators."""
        agent_name(agent)
        now = time.time() if now is None else now
        with self.transaction() as db:
            old = db.execute("SELECT * FROM batches WHERE agent=? AND kind='segment' AND status!='ready' ORDER BY created LIMIT 1", (agent,)).fetchone()
            if old:
                if old['lease'] > now:
                    return None
                if old['grant_version'] != grant_version:
                    db.execute('DELETE FROM batches WHERE id=?', (old['id'],))
                else:
                    token = str(uuid.uuid4())
                    db.execute("UPDATE batches SET token=?,lease=?,status='generating',error=NULL WHERE id=?",
                               (token, now + 600, old['id']))
                    return self.job(old['id'])
            rows = db.execute('SELECT * FROM sources WHERE seq NOT IN (SELECT seq FROM coverage WHERE agent=?) ORDER BY seq LIMIT ?',
                              (agent, max_messages)).fetchall()
            if not rows:
                return None
            all_newest = db.execute('SELECT max(observed) FROM sources').fetchone()[0]
            selected, size = [], 0
            for row in rows:
                if selected and size + len(row['body']) > max_chars:
                    break
                selected.append(row['seq']); size += len(row['body'])
            if not (force or now - all_newest >= idle or now - rows[0]['observed'] >= max_age
                    or len(rows) == max_messages or len(selected) < len(rows)):
                return None
            bid = fingerprint([agent, grant_version, 'segment', selected])
            token = str(uuid.uuid4())
            db.execute('INSERT INTO batches(id,agent,grant_version,kind,refs,status,token,lease,created) VALUES(?,?,?,?,?,?,?,?,?)',
                       (bid, agent, grant_version, 'segment', encode(selected), 'generating', token, now + 600, now))
            return self.job(bid)

    def job(self, bid):
        row = dict(self.db.execute('SELECT * FROM batches WHERE id=?', (bid,)).fetchone())
        row['refs'] = json.loads(row['refs'])
        if row['kind'] == 'segment':
            row['inputs'] = [dict(self.db.execute('SELECT * FROM sources WHERE seq=?', (seq,)).fetchone()) for seq in row['refs']]
            for source in row['inputs']:
                source['body'] = json.loads(source['body'])
        else:
            row['inputs'] = [json.loads(self.db.execute('SELECT body FROM batches WHERE id=?', (ref,)).fetchone()[0]) for ref in row['refs']]
        return row

    def fail(self, job, error, *, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            # Exception class only. No credentials, prompt or transcript in diagnostic.
            db.execute("UPDATE batches SET status='failed',failures=failures+1,error=?,lease=? WHERE id=? AND token=? AND status='generating'",
                       (type(error).__name__, now + 3600, job['id'], job['token']))

    def publish(self, job, summary, *, now=None):
        now = time.time() if now is None else now
        validate_summary(summary, job)
        document = {'id': job['id'], 'agent': job['agent'], 'grant_version': job['grant_version'],
                    'kind': job['kind'], 'refs': job['refs'], 'summary': summary, 'created': now}
        if job['kind'] == 'segment':
            document['sources'] = [{'seq': s['seq'], 'source_key': s['source_key'], 'revision': s['revision'],
                                    'author_id': s['body']['author_id'], 'created_at': s['body']['created_at'],
                                    'supersedes_seq': s['body'].get('supersedes_seq'),
                                    'deleted': s['body']['deleted']} for s in job['inputs']]
        with self.transaction() as db:
            row = db.execute('SELECT status,token,lease FROM batches WHERE id=?', (job['id'],)).fetchone()
            if not row or row['status'] != 'generating' or row['token'] != job['token'] or row['lease'] < now:
                raise NotReady('Expired summary generation')
            if job['kind'] == 'rollup' and self.unread(job['agent']) != job['refs']:
                db.execute('DELETE FROM batches WHERE id=?', (job['id'],))
                return False  # consumer advanced while model was running
            db.execute("UPDATE batches SET status='ready',body=?,error=NULL WHERE id=?", (encode(document), job['id']))
            if job['kind'] == 'segment':
                for seq in job['refs']:
                    db.execute('INSERT INTO coverage VALUES(?,?,?)', (job['agent'], seq, job['id']))
        return True

    def unread(self, agent):
        return [r[0] for r in self.db.execute("SELECT id FROM batches WHERE agent=? AND kind='segment' AND status='ready' AND id NOT IN (SELECT batch FROM received WHERE agent=?) ORDER BY created,id", (agent, agent))]

    def reserve_rollup(self, agent, grant_version, *, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            refs = self.unread(agent)
            if len(refs) < 2:
                return None
            # Bounded rollup: if the unread backlog exceeds one model input, deliver
            # original segments in pages instead of silently omitting older history.
            bodies = [db.execute('SELECT body FROM batches WHERE id=?', (ref,)).fetchone()[0] for ref in refs]
            if sum(len(b) for b in bodies) > 60000:
                return None
            bid = fingerprint([agent, grant_version, 'rollup', refs])
            old = db.execute('SELECT status,lease FROM batches WHERE id=?', (bid,)).fetchone()
            if old and (old['status'] == 'ready' or old['lease'] > now):
                return None
            token = str(uuid.uuid4())
            db.execute('INSERT OR REPLACE INTO batches(id,agent,grant_version,kind,refs,status,token,lease,created) VALUES(?,?,?,?,?,?,?,?,?)',
                       (bid, agent, grant_version, 'rollup', encode(refs), 'generating', token, now + 600, now))
            return self.job(bid)

    def claim(self, agent, session, turn, *, reset=False, now=None):
        agent_name(agent)
        now = time.time() if now is None else now
        with self.transaction() as db:
            prior = db.execute('SELECT * FROM claims WHERE agent=? AND session=? AND turn=?', (agent, session, turn)).fetchone()
            if prior:
                return json.loads(prior['body']) if prior['state'] == 'pending' else None
            if reset:
                db.execute('DELETE FROM seen WHERE agent=? AND session=?', (agent, session))
            # Continue paginated context reload on later turns too. Globally read
            # is not necessarily present in this new/compacted main context.
            refs = [r[0] for r in db.execute("""SELECT id FROM batches WHERE agent=? AND kind='segment' AND status='ready'
                AND (id NOT IN (SELECT batch FROM received WHERE agent=?) OR
                    (id NOT IN (SELECT batch FROM assessed WHERE agent=?) AND
                     id NOT IN (SELECT batch FROM seen WHERE agent=? AND session=?)))
                ORDER BY created,id""", (agent, agent, agent, agent, session))]
            if not refs:
                return None
            roll = db.execute("SELECT body FROM batches WHERE agent=? AND kind='rollup' AND status='ready' AND refs=? ORDER BY created DESC LIMIT 1", (agent, encode(refs))).fetchone()
            docs, chosen, size = [], [], 0
            if roll:
                docs = [json.loads(roll[0])]; chosen = refs
            else:
                for ref in refs:
                    raw = db.execute('SELECT body FROM batches WHERE id=?', (ref,)).fetchone()[0]
                    if docs and size + len(raw) > 12000:
                        break
                    size += len(raw); chosen.append(ref); docs.append(json.loads(raw))
            # Claims contain a frozen copy. A later rollup never mutates this payload.
            cid = str(uuid.uuid4())
            body = {'claim_id': cid, 'agent': agent, 'batch_ids': chosen, 'documents': docs,
                    'remaining_batches': len(refs) - len(chosen), 'context_reload': reset}
            db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,?,?)',
                       (cid, agent, session, turn, encode(chosen), encode(body), 'pending', now))
            return body

    def acknowledge(self, agent, session, turn, *, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            claim = db.execute("SELECT * FROM claims WHERE agent=? AND session=? AND turn=? AND state='pending'", (agent, session, turn)).fetchone()
            if not claim:
                return False
            for bid in json.loads(claim['refs']):
                db.execute('INSERT OR IGNORE INTO received VALUES(?,?,?,?,?)', (agent, bid, session, turn, now))
                db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?)', (agent, session, bid))
            db.execute("UPDATE claims SET state='acknowledged' WHERE id=?", (claim['id'],))
            return True

    def pending_save(self, agent):
        return [json.loads(r[0]) for r in self.db.execute('SELECT b.body FROM received r JOIN batches b ON b.id=r.batch WHERE r.agent=? AND r.batch NOT IN (SELECT batch FROM assessed WHERE agent=?) ORDER BY b.created,b.id', (agent, agent))]

    def status(self):
        return {'source_revisions': self.db.execute('SELECT count(*) FROM sources').fetchone()[0],
                'personas': {a: {'unread_batches': len(self.unread(a)), 'received_not_saved': len(self.pending_save(a)),
                                'jobs': [dict(r) for r in self.db.execute("SELECT kind,status,count(*) AS count FROM batches WHERE agent=? GROUP BY kind,status", (a,))]}
                             for a in ('agent_a', 'agent_b')}}


def validate_summary(summary, job):
    """Require attributable observations; semantic faithfulness also needs evaluation."""
    if (not isinstance(summary, dict) or set(summary) != {'overview', 'observations'}
            or not isinstance(summary['overview'], str) or len(summary['overview']) > 700
            or not isinstance(summary['observations'], list) or len(summary['observations']) > 12):
        raise NotReady('Invalid summary shape')
    allowed = {str(ref) for ref in job['refs']}
    for observation in summary['observations']:
        if (not isinstance(observation, dict) or set(observation) != {'kind', 'text', 'refs'}
                or observation['kind'] not in ('said', 'joke', 'hypothetical', 'disagreement', 'correction', 'uncertain')
                or not isinstance(observation['text'], str) or not observation['text'].strip()
                or len(observation['text']) > 400 or not isinstance(observation['refs'], list)
                or not observation['refs'] or any(ref not in allowed for ref in observation['refs'])):
            raise NotReady('Invalid summary provenance')
    if len(encode(summary)) > 4500:
        raise NotReady('Summary exceeds budget')
