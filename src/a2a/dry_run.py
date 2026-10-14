"""Executable night-chat walkthrough. All identities and dialogue are synthetic."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .adapters import SyntheticAdapter
from .coordinator import Coordinator
from .storage import Store, atomic_write, encode


def walkthrough(directory: Path):
    store = Store(directory / "runtime")
    try:
        coord, adapter = Coordinator(store), SyntheticAdapter()
        with store.coordinator_lock():
            run = coord.start("synthetic-window", "2026-09-07")
            for agent in ("agent_a", "agent_b"):
                attempt = coord.reserve(run, agent, "prepare", "2026-09-07")
                coord.dispatched(attempt)
                coord.save_result(attempt, adapter.call({"request_id": attempt, "purpose": "prepare", "own_material": ["synthetic"]}))
                coord.accept(attempt)
            previous = None
            for index, agent in enumerate(("agent_a", "agent_b", "agent_a", "agent_b")):
                attempt = coord.reserve(run, agent, "chat", "2026-09-07")
                coord.dispatched(attempt)
                coord.save_result(attempt, adapter.call({"request_id": attempt, "purpose": "chat", "reply_to": previous, "close": index == 3}))
                coord.accept(attempt)
                previous = attempt
                if coord.should_end(run):
                    break
            coord.finish(run, "closure", {"agent_a": "event-m-1", "agent_b": "event-z-1"})
            exported = coord.export(run, directory / "synthetic-social")
        report = {"mode": "synthetic-dry-run", "real_model_calls": 0, "native_deployment_ready": False,

                  "whisper": json.loads(exported.read_text()),
                  "native_gates": ["Telegram/app real input and receipt", "complete persona plus tool/hook isolation on every resume",
                                   "native transcript parsers", "subscription authentication and scheduling on macOS"]}
        return report
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="a2a-dryrun-") as tmp:
        report = walkthrough(Path(tmp))
    if args.report:
        atomic_write(args.report, encode(report) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
