"""Install scoped user-level hooks outside the read-only persona repositories."""
import argparse
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

os.umask(0o077)
h=Path.home();repo=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime',type=Path,default=h/'Library/Application Support/kc-agent-a2a')
r=parser.parse_args().runtime.resolve()
sys.path.insert(0,str(repo/'src'))
from a2a.storage import atomic_write,encode

config=r/'digest-hook-config.json'
value={'runtime':str(r),'content':json.loads((r/'config.json').read_text())['content'],'sessions':{},
       'interactive_roots':{a: v['root'] for a, v in json.loads((r/'config.json').read_text())['personas'].items()}}
native_config=json.loads((r/'config.json').read_text())
if native_config.get('continuity_root'):value['continuity_root']=native_config['continuity_root']
if config.exists() and json.loads(config.read_text()) != value:raise SystemExit('Existing readback config differs; preserve state and inspect before updating')
atomic_write(config,encode(value))
script=r/'digest-hook.py'
atomic_write(script,'import sys\nsys.dont_write_bytecode=True\nsys.path.insert(0,'+repr(str(repo/'src'))+')\nfrom a2a.digest_hook import main\nsys.argv=[sys.argv[0],'+repr(str(config))+']\nmain()\n')
command=shlex.quote(sys.executable)+' '+shlex.quote(str(script))
backup=r/'settings-before-readback';backup.mkdir(exist_ok=True,mode=0o700)
p=h/'.claude/settings.json';d=json.loads(p.read_text()) if p.exists() else {}
if p.exists() and not (backup/'claude-settings.json').exists():shutil.copy2(p,backup/'claude-settings.json')
for event in ['SessionStart','UserPromptSubmit','Stop']:
    groups=d.setdefault('hooks',{}).setdefault(event,[])
    if not any(x.get('command')==command for g in groups for x in g.get('hooks',[])):
        groups.append({'hooks':[{'type':'command','command':command,'timeout':15}]})
atomic_write(p,json.dumps(d,ensure_ascii=False,indent=2)+'\n')
p=h/'.codex/config.toml';s=p.read_text() if p.exists() else ''
if not (backup/'codex-config.toml').exists():atomic_write(backup/'codex-config.toml',s)
start='# BEGIN agent-a2a relationship readback';end='# END agent-a2a relationship readback'
if start in s:s=s[:s.index(start)]+s[s.index(end)+len(end):]
blocks=[start]
for event in ['SessionStart','UserPromptSubmit','Stop']:
    blocks += [f'[[hooks.{event}]]',f'[[hooks.{event}.hooks]]','type = "command"',
               'command = '+json.dumps(command),'timeout = 15']
    if event!='Stop':blocks.append('additionalContextLimit = 16000')
    blocks.append('')
blocks.append(end)
atomic_write(p,s.rstrip()+'\n\n'+'\n'.join(blocks)+'\n')
print(encode({'installed':True,'persona_repos_modified':False,'claude_restart':False,'codex_hook_trust':'review-required'}))
