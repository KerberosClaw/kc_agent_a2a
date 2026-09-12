"""One trusted writer exports attributed room records and bounded extractive memories."""
from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3

from a2a.social import git, verify_repository
from .native import Grant, write_private
from .state import NotReady, Registry


@contextmanager
def repository_lock(content, existing_writer_lock):
    """Serialize with the already deployed night-chat writer without modifying it."""
    path = Path(existing_writer_lock)
    if path.is_symlink() or not path.is_file():
        raise NotReady('Existing writer lock is missing')
    with path.open('r') as existing, (Path(content) / '.git/kc-content.lock').open('a') as local:
        fcntl.flock(existing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(local, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def collect(runtime, grant_root):
    runtime = Path(runtime)
    events, grants = {}, {}
    for agent in ('agent_a', 'agent_b'):
        config = json.loads((runtime / 'config' / (agent + '.json')).read_text())
        config['human_ids'] = tuple(config['human_ids'])
        reg = Registry(**config)
        grant = Grant(grant_root, agent, reg)
        grants[agent] = grant
        db = sqlite3.connect(f'file:{runtime / "state" / agent / "party.sqlite3"}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            if db.execute('SELECT registry FROM config').fetchone()[0] != reg.canonical():
                raise NotReady('Archive registry mismatch')
            for row in db.execute('SELECT channel_id,message_id,author_id,created_at,content FROM events'):
                msg = dict(row)
                key = msg['message_id']
                if key in events and events[key] != msg:
                    raise NotReady('Conflicting room event snapshots')
                events[key] = msg
        finally:
            db.close()
    if grants['agent_a'].version != grants['agent_b'].version:
        raise NotReady('Archive grant mismatch')
    return sorted(events.values(), key=lambda m: int(m['message_id'])), grants


def save_room(runtime, content, grant_root, existing_writer_lock):
    """No drafts/native stdout. Model workers never receive the content repo path."""
    runtime, content = Path(runtime), Path(content)
    messages, grants = collect(runtime, grant_root)
    reg = grants['agent_a'].registry
    folder = Path('discord') / reg.guild_id / reg.channel_id
    # Extractive memory preserves author, date and source; no invented model summary.
    memories = {}
    for agent, grant in grants.items():
        accepted = grant.filter_context(messages)
        excerpts, size = [], 0
        for msg in reversed(accepted):
            copy = dict(msg);copy['content'] = copy['content'][:500]
            size += len(copy['content'])
            if size > 6000 or len(excerpts) >= 30:
                break
            excerpts.append(copy)
        memories[agent] = {'kind': 'attributed_room_excerpts', 'agent': agent,
                           'self_id': grant.registry.self_id, 'grant_version': grant.version,
                           'messages': list(reversed(excerpts))}
    files = {folder / 'messages.jsonl': ''.join(json.dumps(m, ensure_ascii=False) + '\n' for m in messages)}
    files.update({folder / (a + '.memory.json'): json.dumps(m, ensure_ascii=False, indent=2) + '\n'
                  for a, m in memories.items()})
    prior_path = runtime / 'evidence/archive.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    if (prior.get('backed_up') and prior.get('grant_version') == grants['agent_a'].version
            and all((content / p).is_file() and (content / p).read_text() == v for p, v in files.items())
            and all((runtime / 'memory' / (agent + '.json')).is_file()
                    and json.loads((runtime / 'memory' / (agent + '.json')).read_text()) == memory
                    for agent, memory in memories.items())):
        return prior
    with repository_lock(content, existing_writer_lock):
        verify_repository(content)
        relative = []
        for path, value in files.items():
            target = content / path
            if not target.exists() or target.read_text() != value:
                write_private(target, value);relative.append(str(path))
        if relative:
            git(content, 'add', '--', *relative)
            staged = git(content, 'diff', '--cached', '--name-only', '-z').decode().strip('\0').split('\0')
            if set(staged) != set(relative):
                raise NotReady('Unexpected archive staging')
            for path in relative:
                if not git(content, 'show', ':' + path).startswith(b'\x00GITCRYPT\x00'):
                    raise NotReady('Plaintext room archive blocked')
            git(content, 'commit', '-m', 'data: room snapshot')
        commit = git(content, 'rev-parse', 'HEAD').decode().strip()
        if relative or prior.get('commit') != commit or not prior.get('backed_up'):
            git(content, 'push', 'origin', 'main')
            remote = git(content, 'ls-remote', 'origin', 'refs/heads/main').decode().split()[0]
            if remote != commit:
                raise NotReady('Room backup not confirmed')
        # Copies expose only each worker's bounded memory, never the whole repo.
        for agent, memory in memories.items():
            target = runtime / 'memory' / (agent + '.json')
            pending = target.with_suffix('.tmp')
            write_private(pending, json.dumps(memory, ensure_ascii=False, indent=2) + '\n')
            pending.replace(target)
    evidence = {'saved_at': datetime.now(timezone.utc).isoformat(), 'message_count': len(messages),
                'commit': commit, 'backed_up': True, 'grant_version': grants['agent_a'].version}
    write_private(runtime / 'evidence/archive.json', json.dumps(evidence, indent=2))
    return evidence
