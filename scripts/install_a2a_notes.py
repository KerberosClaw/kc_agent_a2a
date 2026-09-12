"""Install scoped note hooks alongside digest hooks, without touching persona sources or tmux."""
import argparse
import json
import os
import shlex
import shutil
import sys
import tomllib
from pathlib import Path

if sys.platform != 'darwin':
    raise SystemExit('macOS required')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--update-helper-only', action='store_true',
                    help='Update an existing version-pinned helper without rewriting hooks or settings')
parser.add_argument('--runtime', type=Path, default=Path.home()/'Library/Application Support/kc-agent-a2a')
parser.add_argument('--helper', type=Path, default=Path.home()/'.local/bin/agent-note')
args = parser.parse_args()
os.umask(0o077)
h = Path.home()
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo/'src'))
from a2a.storage import atomic_write

runtime = args.runtime.resolve()
if not (runtime/'config.json').exists():
    raise SystemExit('install the independent runtime first')
root = runtime/'mailbox'
if root.is_symlink() or (root/'.git').exists():
    raise SystemExit('mailbox must be private local storage outside Git')
root.mkdir(mode=0o700, exist_ok=True)
root.chmod(0o700)
helper = args.helper.absolute()
config = runtime/'note-config.json'
value = {'mail_root': str(root), 'helper': str(helper), 'sessions': {},
         'interactive_roots': {a: v['root'] for a, v in json.loads((runtime/'config.json').read_text())['personas'].items()}}
if config.exists():
    prior = json.loads(config.read_text())
    same_paths = all(Path(prior.get(k, '')).resolve() == Path(value[k]).resolve() for k in ('mail_root', 'helper'))
    if not same_paths or prior.get('interactive_roots') != value['interactive_roots']:
        raise SystemExit('existing note configuration differs; inspect before replacing')
command = shlex.quote(str(helper))+' hook'
if args.update_helper_only:
    if not config.exists() or not helper.exists():
        raise SystemExit('helper-only update requires an existing installation')
    for engine, path, events in [
        ('claude', h/'.claude/settings.json', ['UserPromptSubmit', 'Stop', 'StopFailure']),
        ('codex', h/'.codex/config.toml', ['UserPromptSubmit', 'Stop', 'Interrupt']),
    ]:
        settings = json.loads(path.read_text()) if engine == 'claude' else tomllib.loads(path.read_text())
        for event in events:
            count = sum(x.get('command') == command for group in settings.get('hooks', {}).get(event, []) for x in group.get('hooks', []))
            if count != 1:
                raise SystemExit('existing note hooks differ; helper-only update aborted')
elif not config.exists():
    atomic_write(config, json.dumps(value, indent=2)+'\n')
if helper.exists() and 'a2a.note_runtime' not in helper.read_text():
    raise SystemExit('helper path already occupied')
atomic_write(helper, '#!'+sys.executable+'\nimport os,sys\nos.umask(0o077)\nsys.dont_write_bytecode=True\n'
             +'sys.path.insert(0,'+repr(str(repo/'src'))+')\n'
             +'from a2a.note_runtime import main\nsys.argv=[sys.argv[0],"--config",'+repr(str(config))+',*sys.argv[1:]]\nmain()\n')
helper.chmod(0o700)
if args.update_helper_only:
    print(json.dumps({'updated': str(helper), 'source': str(repo), 'settings_rewritten': False,
                      'persona_writes': False, 'tmux_restart': False}))
    raise SystemExit(0)
backup = runtime/'settings-before-notes'
backup.mkdir(mode=0o700, exist_ok=True)
p = h/'.claude/settings.json'
d = json.loads(p.read_text()) if p.exists() else {}
if p.exists() and not (backup/'claude-settings.json').exists():
    shutil.copy2(p, backup/'claude-settings.json')
for event in ['UserPromptSubmit', 'Stop', 'StopFailure']:
    groups = d.setdefault('hooks', {}).setdefault(event, [])
    if not any(x.get('command') == command for g in groups for x in g.get('hooks', [])):
        groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]})
atomic_write(p, json.dumps(d, ensure_ascii=False, indent=2)+'\n')
p = h/'.codex/config.toml'
s = p.read_text() if p.exists() else ''
if not (backup/'codex-config.toml').exists():
    atomic_write(backup/'codex-config.toml', s)
start = '# BEGIN agent-a2a note mailbox'
end = '# END agent-a2a note mailbox'
if start in s:
    s = s[:s.index(start)] + s[s.index(end)+len(end):]
blocks = [start]
for event in ['UserPromptSubmit', 'Stop', 'Interrupt']:
    blocks += [f'[[hooks.{event}]]', f'[[hooks.{event}.hooks]]', 'type = "command"',
               'command = '+json.dumps(command), 'timeout = '+('3' if event == 'Interrupt' else '10')]
    if event == 'UserPromptSubmit':
        blocks.append('additionalContextLimit = 4000')
    blocks.append('')
blocks.append(end)
atomic_write(p, s.rstrip()+'\n\n'+'\n'.join(blocks)+'\n')
print(json.dumps({'installed': str(helper), 'mailbox': str(root), 'persona_writes': False,
                  'tmux_restart': False, 'codex_hooks': 'native review required'}))
