#!/usr/bin/env python3
"""Single trusted archive writer; never starts a model or sends Discord messages."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from discord_party.archive import save_room


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--content', type=Path, required=True)
    parser.add_argument('--grant', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--existing-writer-lock', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    with (args.runtime / 'archive.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                result = save_room(args.runtime, args.content, args.grant, args.existing_writer_lock)
                print(json.dumps(result), flush=True)
            except Exception as error:
                print('ARCHIVE_NOT_READY ' + type(error).__name__, file=sys.stderr, flush=True)
                if not args.watch:
                    raise
            if not args.watch:
                break
            time.sleep(30)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print('ARCHIVE_FAILED ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
