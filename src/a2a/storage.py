from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path


class BoundaryError(RuntimeError):
    pass


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / "state.sqlite3", isolation_level=None, timeout=10)
        os.chmod(self.root / "state.sqlite3", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise BoundaryError("unsupported database version")
        if version == 1:
            # Keep every run and foreign-key identity; only remove the obsolete day UNIQUE.
            self.db.execute("PRAGMA foreign_keys=OFF")
            self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE runs_v2(id TEXT PRIMARY KEY,window TEXT UNIQUE NOT NULL,day TEXT NOT NULL,
              status TEXT NOT NULL,stop_reason TEXT,stop INTEGER NOT NULL DEFAULT 0);
            INSERT INTO runs_v2 SELECT * FROM runs;
            DROP TABLE runs;
            ALTER TABLE runs_v2 RENAME TO runs;
            PRAGMA user_version=2;
            COMMIT;
            """)
            self.db.execute("PRAGMA foreign_keys=ON")
            if self.db.execute("PRAGMA foreign_key_check").fetchone():
                raise BoundaryError("ledger migration foreign-key failure")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS notes(
          id TEXT PRIMARY KEY, origin TEXT UNIQUE NOT NULL, courier TEXT NOT NULL,
          recipient TEXT NOT NULL, status TEXT NOT NULL, session TEXT, turn TEXT,
          claim TEXT, injected INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS runs(
          id TEXT PRIMARY KEY, window TEXT UNIQUE NOT NULL, day TEXT NOT NULL,
          status TEXT NOT NULL, stop_reason TEXT, stop INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS attempts(
          id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), agent TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('chat','prepare','retry')),
          day TEXT NOT NULL, parent TEXT REFERENCES attempts(id), status TEXT NOT NULL,
          result TEXT, UNIQUE(parent));
        CREATE TABLE IF NOT EXISTS messages(
          request TEXT PRIMARY KEY REFERENCES attempts(id), run TEXT NOT NULL,
          agent TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
          UNIQUE(run, ordinal));
        CREATE TABLE IF NOT EXISTS cursors(agent TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS exports(
          run TEXT PRIMARY KEY REFERENCES runs(id), payload TEXT NOT NULL,
          exported INTEGER NOT NULL DEFAULT 0);
        PRAGMA user_version=2;
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @contextmanager
    def coordinator_lock(self):
        with (self.root / "coordinator.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BoundaryError("coordinator already running") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def close(self):
        self.db.close()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
