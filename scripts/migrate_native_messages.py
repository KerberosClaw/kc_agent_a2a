#!/usr/bin/env python3
"""Remove only legacy mailbox hooks and archive local data, without restarting sessions."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from a2a.storage import atomic_write

# Already-loaded engines may retain the old hook until their next normal reload.
# This shim reads/writes no mail. All operational commands fail explicitly.
RETIRED_HELPER = '#!'+sys.executable+'\n'+"""import sys
if sys.argv[1:] == ['hook']:
    raise SystemExit(0)
print('Legacy notes retired; use agent-message.', file=sys.stderr)
raise SystemExit(2)
"""
START = '# BEGIN agent-a2a note mailbox'
END = '# END agent-a2a note mailbox'


def without_hooks(value, command):
    result = copy.deepcopy(value)
    for event, groups in list(result.get('hooks', {}).items()):
        # Codex also stores trust/options under hooks; these are not event groups.
        if not isinstance(groups,list) or any(not isinstance(g,dict) for g in groups):continue
        kept = []
        for group in groups:
            hooks = [h for h in group.get('hooks', []) if h.get('command') != command]
            if hooks:
                kept.append(dict(group, hooks=hooks))
        if kept:
            result['hooks'][event] = kept
        else:
            del result['hooks'][event]
    return result


def commands(value):
    for groups in value.get('hooks',{}).values():
        if not isinstance(groups,list):continue
        for group in groups:
            if isinstance(group,dict):
                yield from group.get('hooks',[])


def active_helpers(helper):
    result = subprocess.run(['/bin/ps','-axo','pid=,command='],text=True,capture_output=True,check=True)
    found = []
    for row in result.stdout.splitlines():
        pid, _, args = row.strip().partition(' ')
        try:parts = shlex.split(args)
        except ValueError:continue
        if str(helper) in parts or 'a2a.note_runtime' in parts:
            if int(pid) != os.getpid():found.append(int(pid))
    return found


def plan(home, runtime):
    root = runtime
    config = root/'note-config.json'
    settings = json.loads(config.read_text())
    mail = Path(settings['mail_root'])
    helper = Path(settings['helper'])
    if not mail.is_absolute() or not helper.is_absolute():raise ValueError('explicit legacy paths required')
    for path in [helper,config,mail]:
        if not path.exists() or path.is_symlink():raise ValueError('legacy path absent or symlinked')
    settings = json.loads(config.read_text())
    if mail == runtime or not mail.resolve().is_relative_to(runtime.resolve()) or helper == config:
        raise ValueError('mailbox must be inside this installation runtime')
    if settings.get('mail_root') != str(mail) or settings.get('helper') != str(helper):
        raise ValueError('legacy configuration does not match the exact local installation')
    if (mail/'.git').exists() or any(p.is_symlink() for p in mail.rglob('*')):
        raise ValueError('mailbox must be private local storage without symlinks or Git')
    command = shlex.quote(str(helper))+' hook'
    claude = home/'.claude/settings.json'
    codex = home/'.codex/config.toml'
    if 'a2a.note_runtime' not in helper.read_text():raise ValueError('legacy helper identity changed')
    if any(p.resolve().is_relative_to(mail.resolve()) for p in [helper,config,claude,codex]):raise ValueError('overlapping mailbox paths')
    if any(p.is_symlink() for p in [claude,codex]):raise ValueError('symlinked settings')
    original = {p:p.read_bytes() for p in [claude,codex,helper,config]}
    claude_value = json.loads(original[claude])
    updated_claude = without_hooks(claude_value,command)
    text = original[codex].decode()
    if text.count(START) != 1 or text.count(END) != 1:raise ValueError('exact managed Codex note block missing')
    a = text.index(START);b = text.index(END,a)+len(END)
    updated_codex = text[:a]+text[b:]
    expected = without_hooks(tomllib.loads(text),command)
    if tomllib.loads(updated_codex) != expected:
        raise ValueError('managed block contains non-note settings; do not remove it')
    changed = {claude:json.dumps(updated_claude,ensure_ascii=False,indent=2)+'\n',codex:updated_codex}
    if any('agent-note' in h.get('command','') for d in [updated_claude,expected] for h in commands(d)):
        raise ValueError('unrecognized legacy hook remains; inspect before retirement')
    return helper,config,mail,original,changed


def retire(home, archive, apply=False, runtime=None):
    runtime=runtime or home/'Library/Application Support/kc-agent-a2a'
    helper,config,mail,original,changed = plan(home, runtime)
    if active_helpers(helper):raise ValueError('legacy helper is running; do not interrupt it')
    if any((p/'.git').exists() for p in [archive,*archive.parents]):raise ValueError('archive must be outside Git')
    if archive.exists() or archive.is_symlink():raise ValueError('archive already exists; inspect prior outcome')
    archive_root=runtime/'retired-notes'
    if archive.resolve().is_relative_to(mail.resolve()):raise ValueError('archive cannot be inside mailbox')
    for p in [runtime,archive_root,*[a for a in archive.parents if a.is_relative_to(runtime)]]:
        if p.is_symlink():raise ValueError('symlinked archive ancestor')
    if not archive.resolve().is_relative_to(archive_root.resolve()):
        raise ValueError('unsafe archive destination')
    if not apply:
        count=sum(h.get('command')==shlex.quote(str(helper))+' hook'
                  for d in [json.loads(original[home/'.claude/settings.json']),tomllib.loads(original[home/'.codex/config.toml'].decode())]
                  for h in commands(d))
        return {'mode':'dry-run','hooks_to_remove':count,'archive':str(archive),'mailbox_files':len(list(mail.rglob('*.json')))}
    lock_path = mail/'.state/coordinator.lock'
    with lock_path.open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Recheck after acquiring the existing lock; never take over another writer.
        if active_helpers(helper):raise ValueError('legacy helper started during planning')
        if any(p.read_bytes()!=value for p,value in original.items()):raise ValueError('settings changed during planning')
        archive.mkdir(mode=0o700,parents=True)
        backups = archive/'before';backups.mkdir(mode=0o700)
        modes = {p:p.stat().st_mode & 0o777 for p in original}
        for p,data in original.items():
            out = backups/('claude-settings.json' if p.name=='settings.json' else 'codex-config.toml' if p.name=='config.toml' else p.name)
            out.write_bytes(data);out.chmod(0o600)
        hashes = {str(p.relative_to(mail)):hashlib.sha256(p.read_bytes()).hexdigest() for p in mail.rglob('*') if p.is_file()}
        moved_mail = moved_config = False
        try:
            for p,text in changed.items():atomic_write(p,text)
            atomic_write(helper,RETIRED_HELPER);helper.chmod(0o700)
            mail.rename(archive/'agent_mail');moved_mail=True
            config.rename(archive/'note-config.json');moved_config=True
            archived = archive/'agent_mail'
            actual = {str(p.relative_to(archived)):hashlib.sha256(p.read_bytes()).hexdigest() for p in archived.rglob('*') if p.is_file()}
            if actual != hashes:raise ValueError('archived mailbox differs; restore before completing')
            manifest = {'status':'retired','archive':str(archive),'mailbox_hashes':hashes,
                        'before_sha256':{str(p):hashlib.sha256(data).hexdigest() for p,data in original.items()},
                        'before_modes':{str(p):mode for p,mode in modes.items()},
                        'hooks_removed':True,'compatibility_hook_is_noop':True,'sessions_restarted':False}
            atomic_write(archive/'retirement.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
            return manifest
        except BaseException:
            if moved_config:(archive/'note-config.json').rename(config)
            if moved_mail:(archive/'agent_mail').rename(mail)
            for p,data in original.items():atomic_write(p,data.decode());p.chmod(modes[p])
            raise


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.runtime.is_symlink():raise ValueError('symlinked runtime')
    print(json.dumps(retire(Path.home(),args.archive,args.apply,args.runtime.resolve()),ensure_ascii=False))


if __name__=='__main__':main()
