#!/usr/bin/env python3
"""Watch Discord Party, recover once after a new boot, and alert on Discord."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from discord_party.installation import watchdog_label
AGENTS = ('agent_a', 'agent_b')

def utc_now(): return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name('.' + path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.chmod(0o600); temp.replace(path)

def run_json(argv, *, timeout=45, payload=None):
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout,
                            input=json.dumps(payload) if payload is not None else None)
    if result.returncode:
        raise RuntimeError(Path(str(argv[0])).name + ' exited nonzero')
    return json.loads(result.stdout)

def boot_id():
    result = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, timeout=5)
    if result.returncode: raise RuntimeError('boot time unavailable')
    return result.stdout.strip()

def execution(runtime):
    current = runtime / 'current'
    if current.exists() or current.is_symlink():
        release = current.resolve(strict=True)
        return release, current / '.venv/bin/python', current
    release = Path(__file__).resolve().parents[2]
    return release, Path(sys.executable), release


def health(runtime):
    release, python, _ = execution(runtime)
    processes = run_json([python, release / 'scripts/discord_party/manage.py', 'status', '--runtime', runtime])
    bots = {}
    for agent in AGENTS:
        try:
            bots[agent] = run_json([python, release / 'scripts/discord_party/party.py', 'status',
                '--config', runtime / 'config' / f'{agent}.json', '--state', runtime / 'state' / agent])
        except Exception:
            bots[agent] = {'ready': 0, 'status_error': True}
    running = {name: bool(row.get('running')) for name, row in processes.items()}
    healthy = all(running.get(name) for name in ('agent_a','agent_b','archive')) and all(
        bots[name].get('ready') == 1 for name in AGENTS)
    return {'healthy': healthy, 'processes': running, 'bots': bots, 'release': str(release)}

def incident_signature(report):
    dead = sorted(name for name, running in report['processes'].items() if not running)
    unready = sorted(name for name, row in report['bots'].items() if row.get('ready') != 1)
    return 'dead=' + ','.join(dead) + ';unready=' + ','.join(unready)

def notify(notifier, source, status, message, incident, event_id):
    payload = run_json([notifier], payload={'source': source, 'status': status,
        'message': message, 'incident': incident, 'event_id': event_id}, timeout=55)
    if (not isinstance(payload, dict) or payload.get('delivered') is not True
            or not isinstance(payload.get('message_id'), str) or not payload['message_id'].strip()):
        raise RuntimeError('notification lacks confirmed delivery receipt')
    return payload['message_id']

def manager_start(runtime):
    release, python, _ = execution(runtime)
    return run_json([python, release / 'scripts/discord_party/manage.py',
                     'start', '--runtime', runtime])

def tick(runtime, notifier, source, *, current_boot=None, wait_seconds=12):
    runtime = Path(runtime).resolve(); state_path = runtime / 'watchdog/state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    current_boot = current_boot or boot_id(); report = health(runtime)
    all_dead = not any(report['processes'].values()); action = 'checked'
    if all_dead and state.get('boot_id') != current_boot:
        action = 'boot_start_attempted'
        try:
            manager_start(runtime); time.sleep(wait_seconds); report = health(runtime)
        except Exception:
            report = health(runtime)
    if report['healthy']:
        previous = state.get('incident')
        if previous and state.get('notified'):
            event_id = hashlib.sha256((current_boot + previous + ':recovered').encode()).hexdigest()
            try:
                state['recovery_message_id'] = notify(notifier, source, 'recovered',
                    'Discord Party 已恢復，Agent A、Agent B與封存程序均通過檢查。',
                    'discord-party-' + hashlib.sha256((current_boot + previous).encode()).hexdigest()[:16], event_id)
            except Exception: pass
        state.update(boot_id=current_boot, incident=None, notified=False, healthy_at=utc_now(), last_report=report)
        atomic_json(state_path, state)
        return {'healthy': True, 'action': action, 'report': report}
    signature = incident_signature(report)
    if signature != state.get('incident'):
        state.update(incident=signature, notified=False, incident_at=utc_now())
    if not state.get('notified'):
        digest = hashlib.sha256((current_boot + signature).encode()).hexdigest()
        try:
            state['message_id'] = notify(notifier, source, 'needs-input',
                'Discord Party 異常，已停止自動重啟以避免循環。請操作者檢查。' + signature,
                'discord-party-' + digest[:16], digest)
            state['notified'] = True
        except Exception as error:
            state['notify_error'] = type(error).__name__
    state.update(boot_id=current_boot, checked_at=utc_now(), last_report=report)
    atomic_json(state_path, state)
    return {'healthy': False, 'action': action, 'incident': signature,
            'notified': state.get('notified', False), 'report': report}

def install(runtime, notifier, source):
    runtime = Path(runtime).resolve(); label = watchdog_label(runtime)
    plist = Path.home() / 'Library/LaunchAgents' / f'{label}.plist'
    _, python, entry = execution(runtime)
    (runtime / 'logs').mkdir(parents=True, exist_ok=True, mode=0o700)
    if plist.exists(): raise RuntimeError('Watchdog already installed; inspect before replacement')
    args = [python, entry / 'scripts/discord_party/watchdog.py',
            'tick', '--runtime', runtime, '--notifier', notifier, '--source', source]
    data = {'Label': label, 'ProgramArguments': [str(a) for a in args], 'RunAtLoad': True,
        'StartInterval': 300, 'ThrottleInterval': 60, 'WorkingDirectory': str(runtime),
        'StandardOutPath': str(runtime/'logs/watchdog.log'), 'StandardErrorPath': str(runtime/'logs/watchdog.err'),
        'EnvironmentVariables': {
            'HOME': str(Path.home()), 'USER': os.environ.get('USER', ''), 'LANG': 'C.UTF-8',
            'PATH': str(Path.home() / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        }, 'Umask':0o077}
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(data)); plist.chmod(0o600)
    subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)], check=True)
    return {'installed':label, 'plist':str(plist), 'interval_seconds':300}

def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    for command in ('tick','install'):
        child=sub.add_parser(command); child.add_argument('--runtime',type=Path,required=True)
        child.add_argument('--notifier',type=Path,required=True); child.add_argument('--source',default='agent-a2a')
    args=parser.parse_args(); os.umask(0o077)
    if args.command == 'install': result=install(args.runtime,args.notifier,args.source)
    else:
        with (args.runtime/'watchdog.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); result=tick(args.runtime,args.notifier,args.source)
    print(json.dumps(result,indent=2))

if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('PARTY_WATCHDOG_FAILED '+type(error).__name__,file=sys.stderr); sys.exit(1)
