# Verification guide

[Documentation index](index.md) · [Release scope](release.md)

```bash
python -m pip install --require-hashes -r scripts/discord_party/requirements.lock
python scripts/run_tests.py
python scripts/check_docs.py
PYTHONPATH=src python -m a2a.demo
```

The runner discovers each independent test root and reports counts/failures/skips. Tests use temporary directories, synthetic identities/messages and fake native engines. Git-crypt tests use temporary local repositories; install git-crypt to avoid missing dependency failures. The macOS guard test needs macOS; Linux CI does not certify native isolation.

| Contract | Evidence |
| --- | --- |
| Note authority, receipt, body removal | A2A core and note runtime tests |
| Day windows, stop, quota, backup failure | Nightly, trial, backup tests |
| Restart deduplication, unknown delivery, stale decisions | Party state/runtime/connector tests |
| Grants, membership, history limits, token storage | Native gate, membership, pilot, installation tests |
| Summary race fencing, coverage, receipts, save reconciliation | Continuity and canonical save tests |
| Curation provenance and rejected publication | Sharing tests |
| Generic setup and upgrades preserve state | Public setup, note installation, manager and continuity install tests |

CI runs offline tests, the demo and link checks on Linux and macOS. Local release verification also builds/installs the Python package in a fresh venv and renders Mermaid blocks. The link checker validates repository targets/anchors; it does not make network calls or prove that Mermaid renders, so rendering is a separate release check.

Live authentication, Discord permissions, actual hook trust/receipts, model tool trace formats, optional search and real conversation quality require operator acceptance in an isolated room. Public CI has no live credentials. Passing offline tests does not certify those external systems.

For a useful bug report, include version, OS/native CLI versions, failing command with local paths/IDs removed, expected/observed state and a minimal fictional reproduction. Never attach a token, actual persona, transcript, database or raw provider response.
