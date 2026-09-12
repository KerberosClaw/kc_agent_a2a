"""Only terminal snapshots and rendered conversations may enter the content repository."""
import subprocess
from pathlib import Path

from .storage import BoundaryError, atomic_write, encode


def git(root,*args):
    try:
        p=subprocess.run(['git','-C',str(root),*args],capture_output=True,timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise BoundaryError('content repository Git operation timed out: '+args[0]) from exc
    if p.returncode:raise BoundaryError('content repository Git operation failed: '+args[0])
    return p.stdout


def verify_repository(root):
    root=Path(root).resolve()
    if not (root/'.git').is_dir():raise BoundaryError('content repository must be a standalone checkout')
    if git(root,'status','--porcelain').strip():raise BoundaryError('content repository is not clean')
    attrs=git(root,'check-attr','filter','--','runs/probe/snapshot.json').decode().strip()
    if not attrs.endswith(': git-crypt'):raise BoundaryError('content encryption filter is missing')
    if not (root/'.git/git-crypt/keys/default').is_file():raise BoundaryError('content encryption is not unlocked')
    return root


def save_snapshot(root, snapshot):
    root=verify_repository(root)
    import uuid
    run=str(uuid.UUID(snapshot['run_id']))
    folder=root/'runs'/run
    files=[folder/'snapshot.json',folder/'conversation.md']
    title = '# 私人悄悄話' if snapshot.get('mode') == 'nightly' else '# 私人試跑對話'
    text=[title,'',f'Run: {run}','']
    for msg in snapshot['messages']:
        text += ['**'+msg['author']+'**','',msg['reply'],'']
    atomic_write(files[0],encode(snapshot)+'\n');atomic_write(files[1],'\n'.join(text))
    relative=[str(p.relative_to(root)) for p in files]
    git(root,'add','--',*relative)
    staged=git(root,'diff','--cached','--name-only','-z').decode().strip('\0').split('\0')
    if set(staged)!=set(relative):raise BoundaryError('unexpected content files staged')
    for path in relative:
        blob=git(root,'show',':'+path)
        if not blob.startswith(b'\x00GITCRYPT\x00'):raise BoundaryError('plaintext content blocked before commit')
    git(root,'commit','-m','data: snapshot')
    commit=git(root,'rev-parse','HEAD').decode().strip()
    backed_up=False
    try:
        git(root,'push','origin','main')
        remote=git(root,'ls-remote','origin','refs/heads/main').decode().split()[0]
        backed_up=remote==commit
    except (BoundaryError,IndexError):pass
    return {'local_commit':commit,'backed_up':backed_up,'conversation_path':str(files[1])}
