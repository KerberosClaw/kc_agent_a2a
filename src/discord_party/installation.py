"""Host-local bot registration prevents a second state path from resetting quota."""
import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .state import NotReady, Registry


def outside_repository(path: Path):
    resolved = path.expanduser().resolve()
    if any((parent/'.git').exists() for parent in (resolved, *resolved.parents)):
        raise NotReady('Runtime state and token files must be outside repositories')
    return resolved


@contextmanager
def bind_bot(root: Path, registry: Registry, state_path: Path, *, initialize=False):
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = root / f'{registry.self_id}.json'
    with (root / f'{registry.self_id}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = {'state':str(state_path.resolve()), 'registry':registry.canonical()}
        if initialize:
            # A stale registration is intentionally retained after failed/crashed init.
            # Recover the existing installation explicitly; never quietly clear the ledger.
            with path.open('x') as handle:
                path.chmod(0o600)
                json.dump(binding, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        elif not path.is_file() or json.loads(path.read_text()) != binding:
            raise NotReady('Bot installation missing or changed; restore registration and state')
        yield


def read_status(root: Path, registry: Registry):
    dbpath = root/'party.sqlite3'
    if dbpath.is_symlink() or not dbpath.is_file():
        raise NotReady('State missing')
    db = sqlite3.connect(f'file:{dbpath}?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        if db.execute('SELECT registry FROM config').fetchone()[0] != registry.canonical():
            raise NotReady('Registry mismatch')
        return dict(db.execute('SELECT * FROM state WHERE id=1').fetchone())
    finally:
        db.close()


def watchdog_label(runtime):
    """Scope launchd ownership to this installation, not another user's service."""
    import hashlib
    return 'org.agent-a2a.party-watchdog.' + hashlib.sha256(str(Path(runtime).resolve()).encode()).hexdigest()[:12]
