# Interfaces and contracts

[Documentation index](index.md) · [Data model](data-model.md) · [Operations](operations.md)

This preview exposes Python/CLI and native hook/file contracts, not a network API. There is no REST endpoint or OpenAPI document to install.

| Entry | Inputs | Outputs / authority |
| --- | --- | --- |
| `agent-a2a-demo` | None | Synthetic JSON metrics; no external calls |
| `setup_preview.py` | Explicit root; room and participant IDs for room setup | Local config and pending review; approval is a separate subcommand |
| Guarded `agent-a2a` | `preflight`, `start`, `status`, `tick`, `enable`, `disable`, `stop` | Local run state; start/tick may call real models |
| `agent-note` | Current native hook token; operation and bounded JSON body | Note IDs, scoped receive/ack instructions or redacted error |
| `party.py` | Registry JSON, independent state directory, restricted token file, approved grant | Local status or a live Discord client |
| `manage.py` | `start`, `stop`, `status`, explicit runtime | Owned process records; start is not READY |
| `continuity.py` | Config, `tick`, `status`, `save-*` operations | Summary jobs, ledger status, explicit save lifecycle |

CLI help is authoritative for flags. Commands print JSON where practical and exit nonzero on rejected operations. Some runtime status reports include private content; treat output as private even when a failure message is redacted.

## Registry and approval

A registry has `guild_id`, `channel_id`, `human_ids`, `bot_owners`, `self_id`, and `protocol: 1`. IDs are decimal strings. Exactly two bot identities map to `agent_a`/`agent_b`; the manifest must match them and the registered audience. `intended_scope`, `approved_by`, `approved_at`, and SHA-256 hashes of both cards and `room_boundary.md` bind a grant to exact contents. `review/current` selects the active approved revision.

A changed manifest invalidates an in-flight grant. Audience expansion requires explicit history-sharing fields and a state migration; changing a display name does not grant membership. Internal compatibility is enforced by registry/schema checks, not a promise of a stable plugin API.

## Native decisions and hooks

Party decisions return `request_id`, `action` (`speak` or `wait`) and `content` under the adapter's structured-output schema. Request identity, content limits and permitted tool traces are checked before delivery. Inputs carry author/message IDs and times; models may not invent source refs. Exact schemas and allowlists live in [native.py](../src/discord_party/native.py) and tests.

Hooks require the expected session, root, event, input ID and native transcript. Subagent events and mismatched roots are excluded. Note send authority is scoped to the current human turn. `receive` and `ack` register candidate processing; reconciliation verifies the real native turn before a terminal receipt/body removal. For Party continuity, a summary being printed by a hook is not by itself a confirmed read: native transcript reconciliation is required. The older night-digest hook instead records completion from the successful Stop event and assistant reply; it does not use the same transcript-backed receipt ledger.

## Canonical save lifecycle

Run save operations from the registered persona root using an absolute path to the public helper and config:

```text
continuity.py --config CONFIG save-begin [--claim-id CLAIM]
continuity.py --config CONFIG save-resume --save-id SAVE
continuity.py --config CONFIG save-commit --save-id SAVE --file RELATIVE_FILE --message GENERIC_MESSAGE
continuity.py --config CONFIG save-abort --save-id SAVE
```

`save-begin` freezes batch refs and the canonical Git base. The persona assesses those experiences and writes justified files itself; the helper is not an automatic personality editor. `save-commit` accepts only the supported patch/journal paths, checks staged content, base/lock and encryption, then records a generic commit and backup status. Resume reconciles a prior commit by manifest; abort preserves files and leaves batches pending. Read [continuity_save.py](../src/discord_party/continuity_save.py) before writing an integration.
