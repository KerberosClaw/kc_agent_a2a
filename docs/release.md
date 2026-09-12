# Technical preview scope

[Documentation index](index.md) · [Changelog](../CHANGELOG.md) · [Testing](testing.md)

The initial source release is `v0.1.0-alpha.1` (Python package version `0.1.0a1`). It exports mechanisms with generic slots and fictional setup. It does not publish private persona content, private Git history, deployment metadata or production credentials.

Included: scoped note relay, bounded night conversations, Discord Party state/transport, optional room web lookup, encrypted content export, independent summary jobs/rollups, native readback receipts, explicit canonical saves, and approved derived room views.

Known limits:

- Real native execution targets macOS and two fixed Claude/Codex slots. Linux tests cover offline mechanisms only.
- Native CLI hook/output/auth formats are external dependencies. Revalidate on upgrades; no universal version-compatibility claim.
- Party is text-based. Complete image reading, edit/delete propagation, thread support and arbitrary extra agents are not included.
- New human onboarding requires Discord access, explicit approval and the tested migration API; no one-click UI exists.
- Fresh setup is an explicit source/venv workflow. The versioned continuity installer is upgrade-specific. Party auto-start, complete restore automation and multi-host coordination are not qualified here.
- Persona/material review is model-assisted and fallible. Private source access is a trust decision. No perfect-redaction or conversation-quality guarantee.
- No SLA, latency target, model quota bypass or paid subscription entitlement is supplied.

Future work should prioritize native CLI compatibility fixtures, simpler onboarding/recovery and a reviewed adapter interface before broadening agents/platforms. This is a roadmap, not shipped functionality.

Release checklist: offline tests; fresh package/demo/config verification; Markdown target/anchor checks; actual Mermaid rendering; two-layer secret/context scans; independent privacy review before commit; CI on candidate commit; PR review/squash merge; public links, metadata and release tag verification. Never copy private history into the public repository.
