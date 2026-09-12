from __future__ import annotations

import json
import uuid
from pathlib import Path

from .notes import AGENTS
from .storage import BoundaryError, Store, atomic_write, encode

LIMITS = {"chat": 10, "prepare": 1, "retry": 2}
MOVES = {"new_experience", "new_view", "question", "affect", "closure", "noop"}


class Coordinator:
    def __init__(self, store: Store):
        self.store = store

    def start(self, window, day, *, calibration=False):
        with self.store.transaction() as db:
            if calibration and not window.startswith('calibration:'):
                raise BoundaryError('calibration requires an explicit calibration window')
            if db.execute("SELECT 1 FROM runs WHERE window=? OR (?=0 AND day=?)", (window, int(calibration), day)).fetchone():
                raise BoundaryError("window or day already has a run")
            run_id = str(uuid.uuid4())
            db.execute("INSERT INTO runs(id,window,day,status) VALUES(?,?,?,'active')", (run_id, window, day))
            return run_id

    def reserve(self, run_id, agent, kind, day, parent=None):
        if agent not in AGENTS or kind not in LIMITS:
            raise BoundaryError("invalid attempt")
        with self.store.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] != "active" or run["stop"]:
                raise BoundaryError("run not active")
            if db.execute("SELECT 1 FROM attempts WHERE run=? AND status IN ('reserved','running','result_saved','unknown')",
                          (run_id,)).fetchone():
                raise BoundaryError("previous attempt needs reconciliation")
            if day != run["day"]:
                raise BoundaryError("attempt day must match its run")
            if kind == "retry":
                old = db.execute("SELECT * FROM attempts WHERE id=?", (parent,)).fetchone()
                if not old or old["run"] != run_id or old["agent"] != agent or old["kind"] == "retry" or old["status"] != "failed":
                    raise BoundaryError("only a confirmed failed original attempt may retry")
                if db.execute("SELECT 1 FROM attempts WHERE parent=?", (parent,)).fetchone():
                    raise BoundaryError("request already retried")
            elif parent is not None:
                raise BoundaryError("parent only allowed for retries")
            for clause, value in (("run", run_id), ("day", day)):
                if clause == 'day' and run['window'].startswith('calibration:'):
                    continue  # Explicit operator calibration is separately accounted, never nightly work.
                count = db.execute(f"SELECT count(*) FROM attempts WHERE {clause}=? AND agent=? AND kind=?",
                                   (value, agent, kind)).fetchone()[0]
                if count >= LIMITS[kind]:
                    raise BoundaryError(f"{kind} budget exhausted")
            attempt = str(uuid.uuid4())
            db.execute("INSERT INTO attempts(id,run,agent,kind,day,parent,status) VALUES(?,?,?,?,?,?,'reserved')",
                       (attempt, run_id, agent, kind, day, parent))
            return attempt

    def dispatched(self, attempt):
        with self.store.transaction() as db:
            if not db.execute("UPDATE attempts SET status='running' WHERE id=? AND status='reserved'", (attempt,)).rowcount:
                raise BoundaryError("attempt already dispatched")

    def fail(self, attempt, *, confirmed):
        with self.store.transaction() as db:
            if not db.execute("UPDATE attempts SET status=? WHERE id=? AND status IN ('reserved','running')",
                              ("failed" if confirmed else "unknown", attempt)).rowcount:
                raise BoundaryError("attempt is not in flight")

    def purpose(self, row):
        if row["kind"] != "retry":
            return row["kind"]
        return self.store.db.execute("SELECT kind FROM attempts WHERE id=?", (row["parent"],)).fetchone()[0]

    def save_result(self, attempt, result):
        row = self.store.db.execute("SELECT * FROM attempts WHERE id=?", (attempt,)).fetchone()
        if not row or row["status"] != "running":
            raise BoundaryError("result has no running request")
        if not isinstance(result, dict) or result.get("request_id") != attempt or result.get("native_status") != "success":
            raise BoundaryError("invalid result identity or native completion")
        purpose = self.purpose(row)
        if purpose == "prepare":
            if type(result.get("has_topic")) is not bool or not isinstance(result.get("own_summary"), str) or len(result["own_summary"]) > 2000:
                raise BoundaryError("invalid preparation result")
        else:
            if (not isinstance(result.get("reply"), str) or not result["reply"].strip() or len(result["reply"]) > 1200
                    or result.get("move") not in MOVES or type(result.get("wants_reply")) is not bool):
                raise BoundaryError("invalid chat result")
            digest = result.get("digest_candidate")
            if not isinstance(digest, dict) or not isinstance(digest.get("summary"), str) or len(digest["summary"]) > 400:
                raise BoundaryError("invalid digest")
            refs = digest.get("shared_message_ids")
            allowed = {r[0] for r in self.store.db.execute("SELECT request FROM messages WHERE run=?", (row["run"],))}
            allowed.add(attempt)
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in allowed for ref in refs):
                raise BoundaryError("digest contains foreign message references")
            last = self.store.db.execute("SELECT request FROM messages WHERE run=? ORDER BY ordinal DESC LIMIT 1", (row["run"],)).fetchone()
            if result.get("reply_to") != (last[0] if last else None):
                raise BoundaryError("reply is not to the last accepted message")
        with self.store.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (row["run"],)).fetchone()
            if run["stop"] or run["status"] != "active":
                db.execute("UPDATE attempts SET status='cancelled' WHERE id=?", (attempt,))
                return False
            db.execute("UPDATE attempts SET status='result_saved',result=? WHERE id=?", (encode(result), attempt))
        return True

    def accept(self, attempt):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt,)).fetchone()
            if row["status"] == "accepted":
                return json.loads(row["result"])
            if row["status"] != "result_saved":
                raise BoundaryError("no complete durable result to accept")
            run = db.execute("SELECT * FROM runs WHERE id=?", (row["run"],)).fetchone()
            if run["stop"] or run["status"] != "active":
                db.execute("UPDATE attempts SET status='cancelled' WHERE id=?", (attempt,))
                raise BoundaryError("late result after stop")
            result = json.loads(row["result"])
            if self.purpose(row) == "chat":
                ordinal = db.execute("SELECT count(*) FROM messages WHERE run=?", (row["run"],)).fetchone()[0]
                db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (attempt, row["run"], row["agent"], ordinal, row["result"]))
            db.execute("UPDATE attempts SET status='accepted' WHERE id=?", (attempt,))
            return result

    def stop(self, run_id):
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET stop=1 WHERE id=?", (run_id,))

    def should_end(self, run_id):
        rows = self.store.db.execute("SELECT payload FROM messages WHERE run=? ORDER BY ordinal DESC LIMIT 2", (run_id,)).fetchall()
        if not rows:
            return False
        recent = [json.loads(row[0]) for row in rows]
        return not recent[0]["wants_reply"] or (len(recent) == 2 and all(r["move"] == "noop" for r in recent))

    def finish(self, run_id, reason, candidate_cursors=None, metadata=None):
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM exports WHERE run=?", (run_id,)).fetchone():
                return
            rows = db.execute("SELECT * FROM messages WHERE run=? ORDER BY ordinal", (run_id,)).fetchall()
            attempts = db.execute("SELECT * FROM attempts WHERE run=?", (run_id,)).fetchall()
            prepared = {r["agent"] for r in attempts if r["status"] == "accepted" and self.purpose(r) == "prepare"}
            no_topic = reason == "skipped_no_topic" and prepared == AGENTS and all(
                not json.loads(r["result"])["has_topic"] for r in attempts if r["status"] == "accepted" and self.purpose(r) == "prepare")
            eligible = (prepared & {r["agent"] for r in rows}) | (prepared if no_topic else set())
            digests = {agent: None for agent in AGENTS}
            messages = []
            for row in rows:
                value = json.loads(row["payload"])
                digests[row["agent"]] = {"author": row["agent"], **value["digest_candidate"]}
                messages.append({"author": row["agent"], "id": row["request"], "reply": value["reply"], "reply_to": value["reply_to"]})
            for agent, cursor in (candidate_cursors or {}).items():
                if agent in eligible:
                    db.execute("INSERT INTO cursors VALUES(?,?) ON CONFLICT(agent) DO UPDATE SET value=excluded.value", (agent, cursor))
            ledger = {agent: {kind: sum(r["agent"] == agent and r["kind"] == kind for r in attempts) for kind in LIMITS} for agent in sorted(AGENTS)}
            payload = {"run_id": run_id, "stop_reason": reason, "digests": digests, "messages": messages, "ledger": ledger}
            if metadata:
                if set(metadata)-{'started_at','ended_at','mode','night_label'}:raise BoundaryError('invalid run metadata')
                payload.update(metadata)
            db.execute("UPDATE runs SET status='terminal',stop_reason=? WHERE id=?", (reason, run_id))
            db.execute("INSERT INTO exports(run,payload) VALUES(?,?)", (run_id, encode(payload)))

    def export(self, run_id, directory: Path):
        row = self.store.db.execute("SELECT * FROM exports WHERE run=?", (run_id,)).fetchone()
        if not row:
            raise BoundaryError("no committed terminal snapshot")
        # One immutable export per run: replaying an export never appends duplicates.
        path = directory / run_id / "snapshot.json"
        atomic_write(path, row["payload"])
        with self.store.transaction() as db:
            db.execute("UPDATE exports SET exported=1 WHERE run=?", (run_id,))
        return path
