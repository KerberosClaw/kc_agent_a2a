"""Scoped external hooks. Only explicitly registered persona sessions receive digests."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import sqlite3
import uuid
from pathlib import Path

from .digests import recent,context
from .storage import Store,atomic_write,encode


def registered_native(config,session,cwd):
    """Enroll only native interactive persona identities, never exec/background jobs."""
    roots=config.get('interactive_roots',{})
    agent=next((a for a,p in roots.items() if Path(p).resolve()==Path(cwd).resolve()),None)
    if not agent:return None
    home=Path.home()
    if agent=='agent_a':
        for p in (home/'.claude/sessions').glob('*.json'):
            try:d=json.loads(p.read_text())
            except (OSError,ValueError):continue
            if d.get('sessionId')==session and d.get('kind')=='interactive' and d.get('cwd')==cwd:
                return {'agent':agent,'cwd':cwd}
    elif agent=='agent_b':
        p=home/'.codex/state_5.sqlite'
        if p.exists():
            db=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,timeout=1)
            try:
                row=db.execute('SELECT cwd,source,archived FROM threads WHERE id=?',(session,)).fetchone()
                if row and row[0]==cwd and row[1] in ('cli','vscode') and not row[2]:return {'agent':agent,'cwd':cwd}
            finally:db.close()
    return None


def handle(config,event):
    root=Path(config['runtime']);session=event.get('session_id','');kind=event.get('hook_event_name')
    try:uuid.UUID(session)
    except (ValueError,TypeError,AttributeError):return None
    cwd=event.get('cwd')
    if not isinstance(cwd,str):return None
    entry=config.get('sessions',{}).get(session) or registered_native(config,session,cwd)
    if not entry or Path(cwd).resolve()!=Path(entry['cwd']).resolve():return None
    if event.get('agent_id') or event.get('agent_type'):return None
    if kind not in ('SessionStart','UserPromptSubmit','Stop'):return None
    agent=entry['agent'];state=Store(root/'digest-receipts')
    try:
        state.db.executescript('''
        CREATE TABLE IF NOT EXISTS digest_seen(session TEXT,run TEXT,PRIMARY KEY(session,run));
        CREATE TABLE IF NOT EXISTS digest_pending(session TEXT PRIMARY KEY,turn TEXT,runs TEXT);
        ''')
        if kind=='Stop':
            # A hook invocation alone is not a read receipt. Require a completed assistant reply.
            if not event.get('last_assistant_message') or event.get('stop_hook_active'):return None
            with state.transaction() as db:
                p=db.execute('SELECT turn,runs FROM digest_pending WHERE session=?',(session,)).fetchone()
                if p and (not event.get('turn_id') or p['turn']==event['turn_id'] or p['turn']=='startup'):
                    for run in json.loads(p['runs']):db.execute('INSERT OR IGNORE INTO digest_seen VALUES(?,?)',(session,run))
                    db.execute('DELETE FROM digest_pending WHERE session=?',(session,))
            return None
        items=recent(Path(config['content']))
        seen={r[0] for r in state.db.execute('SELECT run FROM digest_seen WHERE session=?',(session,))}
        # A context reset needs the relationship context again, even in the same native session.
        reset=kind=='SessionStart' and event.get('source') in ('compact','clear','resume','startup')
        selected=items if reset else [r for r in items if r['run_id'] not in seen]
        if not selected:return None
        turn=event.get('turn_id') or ('startup' if kind=='SessionStart' else hashlib.sha256(str(event.get('prompt','')).encode()).hexdigest())
        with state.transaction() as db:
            db.execute('INSERT OR REPLACE INTO digest_pending VALUES(?,?,?)',(session,turn,encode([r['run_id'] for r in selected])))
        # No raw prompt or digest is logged by the hook; only the delivery state lives locally.
        return {'hookSpecificOutput':{'hookEventName':kind,'additionalContext':context(selected,agent)}}
    finally:state.close()


def main():
    os.umask(0o077)
    config=Path(sys.argv[1])
    if not config.exists():return
    event=json.load(sys.stdin);settings=json.loads(config.read_text())
    result=handle(settings,event)
    if settings.get('continuity_root'):
        party=None
        try:
            from discord_party.continuity_hook import handle as party_handle
            party=party_handle(settings,event)
        except Exception as error:
            # A failed new readback must not suppress the existing night digest.
            print('PARTY_READBACK_NOT_READY '+type(error).__name__,file=sys.stderr)
        if party:
            if result:
                result['hookSpecificOutput']['additionalContext'] += '\n'+party['hookSpecificOutput']['additionalContext']
            else:result=party
    if result:print(encode(result))


if __name__=='__main__':main()
