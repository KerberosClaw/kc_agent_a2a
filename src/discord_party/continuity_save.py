"""Freeze external batches and commit one canonical save, with crash reconciliation."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from a2a.social import git
from a2a.storage import atomic_write
from .continuity import agent_name, encode, fingerprint
from .state import NotReady


def canonical(root, agent):
    root = Path(root).resolve()
    agent_name(agent)
    settings = root / 'persona.json'
    baseline = agent + '_testament.md'
    if settings.exists():
        if settings.is_symlink():raise NotReady('Canonical manifest cannot be a symlink')
        baseline = json.loads(settings.read_text())['baseline']
        if not isinstance(baseline, str) or Path(baseline).name != baseline:
            raise NotReady('Canonical baseline must be a local filename')
    files = ([settings] if settings.exists() else []) + [root / baseline, *sorted((root / 'patches').glob('*.md'))]
    if agent == 'agent_a' and (root / 'voice_spec.md').is_file():
        files.append(root / 'voice_spec.md')
    values = {}
    for file in files:
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root):
            raise NotReady('Canonical source missing or escaped')
        values[str(file.relative_to(root))] = file.read_text()
    if sum(len(v) for v in values.values()) > 100000:
        raise NotReady('Canonical view needs deliberate consolidation')
    return {'version': fingerprint(values), 'files': values}


@contextmanager
def repo_lock(root):
    path = git(root, 'rev-parse', '--absolute-git-dir').decode().strip()
    with (Path(path) / 'continuity-save.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def begin(state, root, agent, *, claim_id=None, config=None):
    root = Path(root).resolve()
    with repo_lock(root):
        reconcile(state, root, agent)
        if git(root, 'diff', '--cached', '--name-only').strip():
            raise NotReady('Finish existing staged changes before saving')
        snapshot = canonical(root, agent)
        version = snapshot['version']
        base = git(root, 'rev-parse', 'HEAD').decode().strip()
        active_claim = None
        if claim_id:
            from .continuity_hook import roots
            from a2a.note_native import history
            active_claim = state.db.execute("SELECT c.*,n.engine,n.transcript FROM claims c JOIN native_claims n ON n.claim=c.id WHERE c.id=? AND c.agent=? AND c.state='pending'", (claim_id, agent)).fetchone()
            if not active_claim or not config:
                raise NotReady('Unknown current-turn experience claim')
            turns, current = history(active_claim['transcript'], active_claim['engine'], active_claim['session'], roots(config, active_claim['engine']))
            proof = turns.get(active_claim['turn'], {})
            if current != active_claim['turn'] or not proof.get('human') or proof.get('status') != 'active':
                raise NotReady('Experience claim is not in a current human turn')
        with state.transaction() as db:
            if db.execute("SELECT 1 FROM saves WHERE agent=? AND status='open'", (agent,)).fetchone():
                raise NotReady('Another save is open; resume that save before starting a new one')
            documents = state.pending_save(agent)
            if active_claim:
                known = {d['id'] for d in documents}
                for bid in json.loads(active_claim['refs']):
                    if bid not in known and not db.execute('SELECT 1 FROM assessed WHERE agent=? AND batch=?', (agent, bid)).fetchone():
                        documents.append(json.loads(db.execute('SELECT body FROM batches WHERE id=?', (bid,)).fetchone()[0]))
            sid = str(uuid.uuid4())
            manifest = {'save_id': sid, 'agent': agent, 'batch_ids': [d['id'] for d in documents],
                        'base_commit': base, 'canonical_before': version, 'created': time.time(),
                        'canonical_files_before': {p: fingerprint(text) for p, text in snapshot['files'].items()},
                        'active_claim': ({'id': active_claim['id'], 'session': active_claim['session'], 'turn': active_claim['turn'],
                                          'refs': json.loads(active_claim['refs'])} if active_claim else None),
                        'source_ids': sorted({s['source_key'] + '@' + s['revision'] for d in documents for s in d.get('sources', [])})}
            db.execute('INSERT INTO saves VALUES(?,?,?,?,?,?,?,?)',
                       (sid, agent, encode(manifest['batch_ids']), base, 'open', None, manifest['created'], encode(manifest)))
        return {'save_id': sid, 'manifest': manifest, 'documents': documents,
                'instruction': '先讀完這批、基線與全部現行 patches，才寫 journal／必要的新 patch；沒有漂移不建 patch。完成後用同一 save_id commit。'}


def get_save(state, sid, agent):
    try:
        uuid.UUID(sid)
    except (ValueError, TypeError):
        raise NotReady('Invalid save ID') from None
    row = state.db.execute('SELECT * FROM saves WHERE id=? AND agent=?', (sid, agent)).fetchone()
    if not row:
        raise NotReady('Unknown save for this persona')
    return row


def mark_committed(state, row, commit):
    with state.transaction() as db:
        claim = json.loads(row['manifest']).get('active_claim')
        if claim:
            # Explicit assessment of a proven active-turn claim + its local commit
            # is stronger evidence than merely firing a Stop hook.
            for bid in claim['refs']:
                db.execute('INSERT OR IGNORE INTO received VALUES(?,?,?,?,?)',
                           (row['agent'], bid, claim['session'], claim['turn'], time.time()))
            db.execute("UPDATE claims SET state='acknowledged' WHERE id=?", (claim['id'],))
        for bid in json.loads(row['refs']):
            db.execute('INSERT OR IGNORE INTO assessed VALUES(?,?,?)', (row['agent'], bid, row['id']))
        db.execute("UPDATE saves SET status='committed',commit_id=? WHERE id=?", (commit, row['id']))


def reconcile(state, root, agent):
    """The immutable private manifest is evidence after commit-before-ack crashes."""
    for row in state.db.execute("SELECT * FROM saves WHERE agent=? AND status='open'", (agent,)).fetchall():
        path = 'continuity/saves/' + row['id'] + '.json'
        result = subprocess.run(['git', '-C', str(root), 'log', '--format=%H', row['base'] + '..HEAD', '--', path],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise NotReady('Cannot reconcile save history')
        for commit in result.stdout.splitlines():
            # git show emits encrypted blobs. Use the configured decrypt filter in
            # memory, never put a decrypted transcript or key in command arguments.
            raw = git(root, 'show', commit + ':' + path)
            if not raw.startswith(b'\x00GITCRYPT\x00'):
                raise NotReady('Unencrypted save manifest')
            gitcrypt = git(root, 'config', '--get', 'filter.git-crypt.smudge').decode().strip()
            import shlex
            p = subprocess.run(shlex.split(gitcrypt), input=raw, capture_output=True, cwd=root, timeout=10)
            if p.returncode or json.loads(p.stdout) != json.loads(row['manifest']):
                raise NotReady('Save manifest does not match frozen assessment')
            mark_committed(state, row, commit)
            break


def backup_commit(root, commit):
    """A later pushed main commit may already include this exact save."""
    try:
        git(root, 'push', 'origin', 'main')
        remote = git(root, 'ls-remote', 'origin', 'refs/heads/main').decode().split()[0]
        git(root, 'merge-base', '--is-ancestor', commit, remote)
        return True
    except Exception:
        return False


def commit_save(state, root, agent, sid, files, message):
    """Caller writes meaning; helper owns exact staging, local commit and accounting."""
    root = Path(root).resolve()
    if not re.fullmatch(r'(?:patch: \d{8} session\d+|docs: journal \d{8})', message):
        raise NotReady('Use a generic save commit message')
    with repo_lock(root):
        reconcile(state, root, agent)
        row = get_save(state, sid, agent)
        if row['status'] == 'committed':
            return {'local_commit': row['commit_id'], 'already_committed': True,
                    'backed_up': backup_commit(root, row['commit_id']),
                    'checkpoint_may_clear': True, 'shared_view_needs_sync': True}
        if row['status'] != 'open' or git(root, 'rev-parse', 'HEAD').decode().strip() != row['base']:
            raise NotReady('Canonical repository changed; rebase the save deliberately')
        selected = []
        for name in files:
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or name not in [str(relative)]:
                raise NotReady('Invalid save path')
            allowed = (relative.parent in (Path('journal'), Path('patches')) and relative.suffix == '.md')
            if not allowed:
                raise NotReady('Save helper only accepts journal and current patches')
            path = root / relative
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise NotReady('Save file missing or escaped')
            if relative.parent == Path('patches') and len(path.read_text().splitlines()) > 20:
                raise NotReady('Personality patch exceeds 20 lines')
            selected.append(name)
        if not any(Path(n).parent == Path('journal') for n in selected):
            raise NotReady('Formal save requires a journal entry')
        frozen = json.loads(row['manifest'])['canonical_files_before']
        current = {p: fingerprint(text) for p, text in canonical(root, agent)['files'].items()}
        changed = {p for p in frozen.keys() | current.keys() if frozen.get(p) != current.get(p)}
        if changed - set(selected):
            raise NotReady('Another writer changed canonical inputs during save')
        if any(p in frozen and p.startswith('patches/') for p in changed):
            raise NotReady('Existing persona patches are immutable; write a new correction')
        manifest_path = 'continuity/saves/' + sid + '.json'
        selected.append(manifest_path)
        staged = git(root, 'diff', '--cached', '--name-only', '-z').decode().strip('\0').split('\0')
        if set(staged) - {'', *selected}:
            raise NotReady('Unrelated staged changes block save')
        atomic_write(root / manifest_path, row['manifest'] + '\n')
        git(root, 'add', '--', *selected)
        staged = git(root, 'diff', '--cached', '--name-only', '-z').decode().strip('\0').split('\0')
        if set(staged) - set(selected) or manifest_path not in staged:
            raise NotReady('Save staging mismatch')
        for path in staged:
            if not git(root, 'show', ':' + path).startswith(b'\x00GITCRYPT\x00'):
                raise NotReady('Plaintext persona save blocked')
        git(root, 'commit', '-m', message)
        commit = git(root, 'rev-parse', 'HEAD').decode().strip()
        mark_committed(state, row, commit)
    # Local commit is the completion boundary. Network failure cannot erase it.
    backed_up = backup_commit(root, commit)
    return {'local_commit': commit, 'already_committed': False, 'backed_up': backed_up,
            'checkpoint_may_clear': True, 'shared_view_needs_sync': True}
