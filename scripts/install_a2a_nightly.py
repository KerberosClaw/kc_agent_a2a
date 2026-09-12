"""Install the application's single Mini scheduler; keep model work outside live sessions."""
import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

if sys.platform!='darwin':raise SystemExit('macOS required')
os.umask(0o077);h=Path.home()
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--launcher',type=Path,required=True)
args=parser.parse_args();r=args.runtime.resolve();launcher=args.launcher.resolve()
state=json.loads(subprocess.check_output([str(launcher),'status'],text=True))
if state.get('worker_running'):raise SystemExit('worker active; do not replace schedule')
p=h/'Library/LaunchAgents/com.local.agent-a2a-nightly.plist'
if p.exists():raise SystemExit('scheduler already exists; inspect before updating')
data={'Label':'com.local.agent-a2a-nightly','ProgramArguments':[str(launcher),'tick'],
      'WorkingDirectory':str(r),'StartInterval':300,'RunAtLoad':True,
      'EnvironmentVariables':{'HOME':str(h),'USER':h.name,'PATH':str(h/'.local/bin')+':/opt/homebrew/bin:/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
      'StandardOutPath':str(r/'nightly.log'),'StandardErrorPath':str(r/'nightly.err')}
p.write_bytes(plistlib.dumps(data));p.chmod(0o600)
subprocess.run(['launchctl','bootstrap','gui/'+str(os.getuid()),str(p)],check=True)
print(json.dumps({'installed':str(p),'interval_seconds':300,'live_sessions_restarted':False}))
