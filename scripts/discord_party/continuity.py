#!/usr/bin/env python3
"""Private continuity worker and canonical-save helper; never sends chat messages."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from a2a.storage import atomic_write
from discord_party.continuity import Continuity, encode
from discord_party.continuity_worker import tick
from discord_party.continuity_save import begin, commit_save, get_save, reconcile
from discord_party.state import NotReady


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('command', choices=('tick', 'status', 'save-begin', 'save-resume', 'save-commit', 'save-abort'))
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--save-id')
    parser.add_argument('--claim-id')
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--message')
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    root = Path(config['continuity_root']); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.command == 'tick':
        with (root / 'worker.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                result = asyncio.run(tick(config, force=args.force))
                failed_jobs = any(j['status'] == 'failed' for a in result['status']['personas'].values() for j in a['jobs'])
                healthy = not result['errors'] and not failed_jobs and all(v['status'] == 'published' for v in result['sharing'].values())
                atomic_write(root / 'health.json', encode({'ok': healthy, 'result': result}))
                print(encode(result))
            except Exception as error:
                atomic_write(root / 'health.json', encode({'ok': False, 'error': type(error).__name__}))
                raise
        return
    state = Continuity(root)
    try:
        if args.command == 'status':
            print(encode(state.status())); return
        cwd = Path.cwd().resolve()
        agent = next((a for a, p in config['interactive_roots'].items() if Path(p).resolve() == cwd), None)
        if not agent:
            raise NotReady('Run save helper from its registered canonical persona repository')
        if args.command == 'save-begin':
            result = begin(state, cwd, agent, claim_id=args.claim_id, config=config)
        elif args.command == 'save-commit':
            result = commit_save(state, cwd, agent, args.save_id, args.file, args.message or '')
        else:
            reconcile(state, cwd, agent)
            row = get_save(state, args.save_id, agent)
            if args.command == 'save-resume':
                refs = json.loads(row['refs'])
                result = {'save': dict(row), 'documents': [json.loads(state.db.execute('SELECT body FROM batches WHERE id=?', (bid,)).fetchone()[0]) for bid in refs]}
            else:
                if row['status'] != 'open':
                    raise NotReady('Only an uncommitted save may be aborted')
                state.db.execute("UPDATE saves SET status='aborted' WHERE id=? AND status='open'", (row['id'],))
                result = {'aborted': row['id'], 'files_preserved': True, 'batches_still_pending': True}
        print(encode(result))
    finally:
        state.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not put private exception arguments in daemon logs.
        print('CONTINUITY_NOT_READY ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
