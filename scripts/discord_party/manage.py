#!/usr/bin/env python3
"""SSH-friendly pilot start/stop/status; never resets either bot's state."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from discord_party.native import write_private


def now():
    return datetime.now(timezone.utc).isoformat()


def alive(record):
    if not record or record.get('status') == 'stopped':
        return False
    process = subprocess.run(['ps', '-p', str(record['pid']), '-o', 'command='], capture_output=True, text=True)
    return process.returncode == 0 and ' '.join(record['argv']) == process.stdout.strip()


def manage(runtime, command):
    runtime = Path(runtime).resolve()
    release = Path(__file__).resolve().parents[2]
    (runtime / 'run').mkdir(exist_ok=True, mode=0o700)
    names = ('agent_a', 'agent_b', 'archive')
    records = {}
    for name in names:
        path = runtime / 'run' / (name + '.json')
        records[name] = json.loads(path.read_text()) if path.exists() else None
    if command == 'status':
        return {name: {'running': alive(record), 'pid': record.get('pid') if record else None}
                for name, record in records.items()}
    if command == 'stop':
        for name, record in records.items():
            if alive(record):
                os.kill(record['pid'], signal.SIGINT)
        deadline = time.monotonic() + 25
        while any(alive(r) for r in records.values()) and time.monotonic() < deadline:
            time.sleep(0.1)
        if any(alive(r) for r in records.values()):
            raise RuntimeError('Processes did not stop; verify before escalation')
        for name, record in records.items():
            if record:
                record.update(status='stopped', stopped_at=now())
                write_private(runtime / 'run' / (name + '.json'), json.dumps(record, indent=2))
        return {'stopped': True}
    if any(alive(record) for record in records.values()):
        raise RuntimeError('Already running; use status or stop before restart')
    grant = runtime / 'review/current'
    grant = grant.resolve(strict=True)
    if grant.parent != (runtime / 'review').resolve() or not grant.is_dir():
        raise RuntimeError('Invalid active review directory')
    config = json.loads((runtime / 'manager.json').read_text())
    for name in names:
        if name == 'archive':
            argv = [sys.executable, str(release / 'scripts/discord_party/archive.py'),
                    '--runtime', str(runtime), '--grant', str(grant),
                    '--existing-writer-lock', config['existing_writer_lock'],
                    '--content', config['content'], '--watch']
        else:
            argv = [sys.executable, str(release / 'scripts/discord_party/party.py'), 'run-native',
                    '--config', str(runtime / 'config' / (name + '.json')),
                    '--state', str(runtime / 'state' / name),
                    '--token-file', str(runtime / 'secrets' / (name + '.token')),
                    '--grant', str(grant), '--agent', name,
                    '--native-work', str(runtime / 'workers' / name),
                    '--memory', str(runtime / 'memory' / (name + '.json')),
                    '--experience-memory', str(runtime / 'experiences' / (name + '.json'))]
        if name != 'archive' and (runtime / 'sharing' / name / 'current.json').exists():
            argv += ['--shared-view', str(runtime / 'sharing' / name)]
        log = runtime / 'logs' / (name + '.log')
        with log.open('a') as output:
            os.fchmod(output.fileno(), 0o600)
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                       start_new_session=True)
        record = {'pid': process.pid, 'argv': argv, 'started_at': now(), 'status': 'running'}
        write_private(runtime / 'run' / (name + '.json'), json.dumps(record, indent=2))
    return {'started': list(names), 'note': 'Use status and local READY evidence; start alone is not readiness.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start', 'stop', 'status'))
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    with (args.runtime / 'manage.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(manage(args.runtime, args.command), indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('PARTY_NOT_READY ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
