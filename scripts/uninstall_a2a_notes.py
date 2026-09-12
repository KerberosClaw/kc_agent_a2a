"""Remove only this installation's note hooks; keep mailbox and receipts for recovery."""
import argparse
import json
import os
import shlex
import sys
from pathlib import Path

os.umask(0o077)
h = Path.home();repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo/'src'))
from a2a.storage import atomic_write

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--helper', type=Path, default=h/'.local/bin/agent-note')
args = parser.parse_args()
command = shlex.quote(str(args.helper.absolute()))+' hook'
p = h/'.claude/settings.json';d = json.loads(p.read_text())
for event, groups in d.get('hooks', {}).items():
    kept = []
    for group in groups:
        copy = dict(group);copy['hooks'] = [x for x in group.get('hooks', []) if x.get('command') != command]
        if copy['hooks']:kept.append(copy)
    d['hooks'][event] = kept
atomic_write(p, json.dumps(d, ensure_ascii=False, indent=2)+'\n')
p = h/'.codex/config.toml';s = p.read_text();start = '# BEGIN agent-a2a note mailbox';end = '# END agent-a2a note mailbox'
if start in s:
    s = s[:s.index(start)] + s[s.index(end)+len(end):]
atomic_write(p, s)
print(json.dumps({'note_hooks_removed': True, 'mailbox_preserved': True, 'digest_hooks_preserved': True}))
