#!/usr/bin/env python3
"""Install a verified continuity release; preserve Party ledger and existing main sessions.

Only a clean committed checkout is accepted. This installs the new readback helper
at the existing trusted hook command path, not a new main-persona session or hook
trust record. The new periodic worker is registered separately with --start-worker.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from a2a.storage import atomic_write
from discord_party.native import Grant
from discord_party.sharing import load_view
from discord_party.state import Registry, NotReady


def command(argv):
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise NotReady('Install command failed: ' + Path(str(argv[0])).name)
    return result.stdout


def pointer(path, target):
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    temp.symlink_to(target); temp.replace(path)


def install(config_path, review, *, source=None):
    config_path = Path(config_path).resolve(); config = json.loads(config_path.read_text())
    runtime = Path(config['party_runtime']); source = Path(source or Path(__file__).resolve().parents[2])
    if command(['git', '-C', source, 'status', '--porcelain']).strip():
        raise NotReady('Commit and verify the release before installation')
    version = command(['git', '-C', source, 'rev-parse', 'HEAD']).strip()
    release = runtime / 'releases' / version
    if release.exists():
        raise NotReady('Release already exists; inspect before retrying installation')
    review = Path(review).resolve(strict=True)
    if review.parent != runtime / 'review':
        raise NotReady('Review is not in this runtime')
    for agent in ('agent_a', 'agent_b'):
        raw = json.loads((runtime / 'config' / (agent + '.json')).read_text()); raw['human_ids'] = tuple(raw['human_ids'])
        grant = Grant(review, agent, Registry(**raw))
        if not load_view(runtime / 'sharing' / agent, grant):
            raise NotReady('Both derived persona views must pass review before deployment')
    old_release = (runtime / 'current').resolve(strict=True)
    old_review = (runtime / 'review/current').resolve(strict=True)
    python = old_release / '.venv/bin/python'
    release.mkdir(parents=True, mode=0o700)
    tracked = command(['git', '-C', source, 'ls-files', '-z']).strip('\0').split('\0')
    for name in tracked:
        if not name.startswith(('src/a2a/', 'src/discord_party/', 'scripts/discord_party/')):
            continue
        src = source / name; target = release / name
        if src.is_symlink():
            raise NotReady('Release source symlink blocked')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(src, target); target.chmod(0o600)
        if hashlib.sha256(src.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            raise NotReady('Release copy mismatch')
    (release / '.venv').symlink_to((old_release / '.venv').resolve(strict=True))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = runtime / 'backups' / ('continuity-' + stamp); backup.mkdir(parents=True, mode=0o700)
    hook_root = Path(config['runtime'])
    hook, hook_config = hook_root / 'digest-hook.py', hook_root / 'digest-hook-config.json'
    shutil.copy2(hook, backup / 'digest-hook.py'); shutil.copy2(hook_config, backup / 'digest-hook-config.json')
    shutil.copytree(runtime / 'config', backup / 'config')
    before = {}
    for agent in ('agent_a', 'agent_b'):
        path = runtime / 'state' / agent / 'party.sqlite3'
        db = sqlite3.connect(path); target = sqlite3.connect(backup / (agent + '.sqlite3'))
        db.backup(target); target.close()
        before[agent] = {'events': db.execute('SELECT count(*) FROM events').fetchone()[0],
                         'outbox': db.execute('SELECT count(*) FROM outbox').fetchone()[0]}
        db.close()
    record = {'version': version, 'previous_release': str(old_release), 'previous_review': str(old_review),
              'review': str(review), 'backup': str(backup), 'before': before, 'complete': False}
    atomic_write(backup / 'deployment.json', json.dumps(record, indent=2))
    manager = old_release / 'scripts/discord_party/manage.py'
    command([python, manager, 'stop', '--runtime', runtime])
    try:
        pointer(runtime / 'current', release)
        pointer(runtime / 'review/current', review)
        wrapper = ("import sys\nsys.dont_write_bytecode=True\nsys.path.insert(0," + repr(str(release / 'src')) + ")\n"
                   "from a2a.digest_hook import main\nsys.argv=[sys.argv[0]," + repr(str(hook_config)) + "]\nmain()\n")
        atomic_write(hook, wrapper)
        hc = json.loads(hook_config.read_text()); hc['continuity_root'] = config['continuity_root']
        atomic_write(hook_config, json.dumps(hc, indent=2))
        binary = Path.home() / '.local/bin/agent-continuity'
        import shlex
        atomic_write(binary, '#!/bin/sh\nexec ' + ' '.join(shlex.quote(str(p)) for p in
                     [runtime / 'current/.venv/bin/python', runtime / 'current/scripts/discord_party/continuity.py', '--config', config_path]) + ' "$@"\n')
        binary.chmod(0o700)
        command([release / '.venv/bin/python', release / 'scripts/discord_party/manage.py', 'start', '--runtime', runtime])
        record['complete'] = True
        atomic_write(runtime / 'evidence/continuity_deployment.json', json.dumps(record, indent=2))
        return record
    except BaseException:
        # Stop only newly started Party processes. Existing Claude tmux/night/poke
        # are never touched, and state DBs are never replaced with old snapshots.
        try:
            command([release / '.venv/bin/python', release / 'scripts/discord_party/manage.py', 'stop', '--runtime', runtime])
        finally:
            pointer(runtime / 'current', old_release); pointer(runtime / 'review/current', old_review)
            atomic_write(hook, (backup / 'digest-hook.py').read_text())
            atomic_write(hook_config, (backup / 'digest-hook-config.json').read_text())
            command([python, manager, 'start', '--runtime', runtime])
        raise


def start_worker(config_path):
    config = json.loads(Path(config_path).read_text()); runtime = Path(config['party_runtime'])
    name = 'io.github.kerberosclaw.agent-continuity'
    plist = Path.home() / 'Library/LaunchAgents' / (name + '.plist')
    if plist.exists():
        raise NotReady('Worker already registered; inspect instead of replacing it')
    args = [runtime / 'current/.venv/bin/python', runtime / 'current/scripts/discord_party/continuity.py',
            '--config', Path(config_path).resolve(), 'tick']
    data = {'Label': name, 'ProgramArguments': [str(a) for a in args], 'RunAtLoad': True, 'StartInterval': 300,
            'ThrottleInterval': 60, 'WorkingDirectory': str(runtime),
            'StandardOutPath': str(runtime / 'logs/continuity.log'), 'StandardErrorPath': str(runtime / 'logs/continuity.err'),
            'EnvironmentVariables': {k: os.environ[k] for k in ('HOME', 'USER', 'PATH', 'LANG') if k in os.environ},
            'Umask': 0o077}
    atomic_write(plist, plistlib.dumps(data).decode())
    command(['/bin/launchctl', 'bootstrap', 'gui/' + str(os.getuid()), plist])
    command(['/bin/launchctl', 'print', 'gui/' + str(os.getuid()) + '/' + name])
    return {'worker_registered': name, 'plist': str(plist), 'interval_seconds': 300}


if __name__ == '__main__':
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, type=Path); p.add_argument('--review', type=Path)
    p.add_argument('--start-worker', action='store_true'); args = p.parse_args()
    try:
        result = start_worker(args.config) if args.start_worker else install(args.config, args.review)
        print(json.dumps(result, indent=2))
    except Exception as error:
        print('CONTINUITY_INSTALL_FAILED ' + type(error).__name__, file=sys.stderr); sys.exit(1)
