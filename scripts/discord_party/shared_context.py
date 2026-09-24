#!/usr/bin/env python3
"""Backfill or inspect approved context using the existing continuity worker lock.

No chat sending, persona saves or scheduler changes. Tick uses native models.
"""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from discord_party.archive import collect
from discord_party.continuity import Continuity, encode
from discord_party.continuity_worker import export
from discord_party.shared_context import refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--max-documents', type=int, choices=range(1, 31), default=3)
    parser.add_argument('command', choices=('tick', 'status'))
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    runtime = Path(config['party_runtime'])
    if args.command == 'status':
        path = runtime / 'shared-context/state.json'
        state = json.loads(path.read_text()) if path.exists() else {'sources': {}}
        result = {}
        for scope, rows in state['sources'].items():
            counts = {}
            for row in rows.values():
                counts[row['status']] = counts.get(row['status'], 0) + 1
            result[scope] = counts
        print(encode(result)); return
    with (Path(config['continuity_root']) / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, grants = collect(runtime, (runtime / 'review/current').resolve(strict=True))
        result = asyncio.run(refresh(config, grants, max_documents=args.max_documents))
        state = Continuity(config['continuity_root'])
        try:
            export(state, config)
        finally:
            state.close()
        print(encode(result))
        if result['status'] == 'failed':
            sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('SHARED_CONTEXT_NOT_READY ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
