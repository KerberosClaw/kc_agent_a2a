from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .storage import BoundaryError, Store, atomic_write, encode

AGENTS = frozenset({"agent_a", "agent_b"})


class Mailbox:
    """No transport or LLM calls. Receipt inputs must come from a trusted native adapter."""

    def __init__(self, store: Store, mail_root: Path | None = None, on_event=None):
        self.store = store
        self.mail_root = Path(mail_root) if mail_root is not None else store.root / "mail"
        self.on_event = on_event or (lambda note, event: None)

    def path(self, row, folder):
        return self.mail_root / row["recipient"] / folder / (row["id"] + ".json")

    def send(self, *, courier, recipient, source_session, source_input, body, index=0):
        if courier not in AGENTS or recipient not in AGENTS or courier == recipient:
            raise BoundaryError("unknown or same recipient")
        if not source_session or not source_input or not isinstance(index, int) or index < 0:
            raise BoundaryError("missing trusted source identity")
        if not isinstance(body, str) or not body.strip() or len(body) > 4000:
            raise BoundaryError("note must contain 1..4000 characters")
        origin = encode([source_session, source_input, recipient, index])
        note_id = str(uuid.uuid5(uuid.NAMESPACE_URL, origin))
        with self.store.transaction() as db:
            old = db.execute("SELECT id FROM notes WHERE origin=?", (origin,)).fetchone()
            if old:
                return old["id"]
            row = {"id": note_id, "recipient": recipient}
            atomic_write(self.path(row, "new"), encode({"id": note_id, "author": "user",
                         "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                         "origin_session_id": source_session, "origin_input_id": source_input,
                         "courier": courier, "recipient": recipient, "body": body}))
            db.execute("INSERT INTO notes(id,origin,courier,recipient,status) VALUES(?,?,?,?,?)",
                       (note_id, origin, courier, recipient, "new"))
            self.on_event(note_id, 'created')
        return note_id

    def claim(self, recipient, session, turn):
        if recipient not in AGENTS or not session or not turn:
            raise BoundaryError("invalid receiver identity")
        out, size = [], 0
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM notes WHERE recipient=? AND session=? AND turn=? AND status IN ('claimed','delivered')", (recipient, session, turn)).fetchone():
                return []
            rows = db.execute("SELECT * FROM notes WHERE recipient=? AND status='new' ORDER BY rowid",
                              (recipient,)).fetchall()
            for row in rows:
                new, claimed = self.path(row, "new"), self.path(row, "claimed")
                # A rename can survive a process crash while its DB transaction rolls back.
                source = new if new.exists() else claimed
                packet = json.loads(source.read_text())
                if packet["id"] != row["id"] or packet["recipient"] != recipient:
                    raise BoundaryError("mail file identity mismatch")
                if len(out) == 5 or size + len(packet["body"]) > 12000:
                    break
                claimed.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if source == new:
                    new.replace(claimed)
                claim_id = str(uuid.uuid4())
                db.execute("UPDATE notes SET status='claimed',session=?,turn=?,claim=?,injected=0 WHERE id=?",
                           (session, turn, claim_id, row["id"]))
                packet["claim_id"] = claim_id
                out.append(packet)
                size += len(packet["body"])
        return out

    def injected(self, note_id, claim_id):
        with self.store.transaction() as db:
            changed = db.execute("UPDATE notes SET status='delivered',injected=1 "
                                 "WHERE id=? AND claim=? AND status='claimed'", (note_id, claim_id)).rowcount
            if not changed:
                raise BoundaryError("claim is no longer deliverable")
            self.on_event(note_id, 'injected')

    def reconcile(self, *, note_id, claim_id, session, turn, status, ack_ids, assistant_final):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row or (row["claim"], row["session"], row["turn"]) != (claim_id, session, turn):
                return False
            if row["status"] in ("acknowledged", "burned"):
                return True
            if row["status"] != "delivered" or not row["injected"]:
                return False
            if status != "success" or note_id not in ack_ids or not isinstance(assistant_final, str) or not assistant_final.strip():
                return False
            db.execute("UPDATE notes SET status='acknowledged' WHERE id=?", (note_id,))
            self.on_event(note_id, 'received')
        self.cleanup(note_id)
        return True

    def cleanup(self, note_id):
        row = self.store.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row and row["status"] in ("acknowledged", "cancelled"):
            # Receipt commits first. A failed unlink must never lead to redelivery.
            self.path(row, "claimed").unlink(missing_ok=True)
            self.path(row, "new").unlink(missing_ok=True)
            with self.store.transaction() as db:
                db.execute("UPDATE notes SET status='burned' WHERE id=? AND status='acknowledged'", (note_id,))

    def retry_failed(self, note_id, claim_id, *, native_status):
        if native_status not in ("failed", "aborted"):
            raise BoundaryError("unknown or live native turn cannot be retried")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM notes WHERE id=? AND claim=?", (note_id, claim_id)).fetchone()
            if not row or row["status"] not in ("claimed", "delivered"):
                raise BoundaryError("not a retryable claim")
            self.path(row, "new").parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            claimed = self.path(row, "claimed")
            if claimed.exists():
                claimed.replace(self.path(row, "new"))
            db.execute("UPDATE notes SET status='new',claim=NULL,session=NULL,turn=NULL,injected=0 WHERE id=?",
                       (note_id,))
            self.on_event(note_id, 'retry_available')

    def cancel(self, note_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row or row["status"] not in ("new", "claimed") or row["injected"]:
                return False
            db.execute("UPDATE notes SET status='cancelled' WHERE id=?", (note_id,))
            self.on_event(note_id, 'cancelled')
        self.cleanup(note_id)
        return True

    def status(self, note_id):
        row = self.store.db.execute("SELECT status FROM notes WHERE id=?", (note_id,)).fetchone()
        return row["status"] if row else "unknown"
