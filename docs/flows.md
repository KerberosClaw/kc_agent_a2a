# Delivery and continuity flows

[Documentation index](index.md) · [Interfaces](interfaces.md)

```mermaid
sequenceDiagram
    participant Human
    participant State as Party ledger
    participant Model as Native worker
    participant Discord
    Human->>State: Registered message
    State->>State: Deduplicate, update epoch, reset permitted quota
    State->>Model: Reserve one bounded decision
    Human->>State: New message or pause
    State->>State: Advance epoch or pause
    Model-->>State: Structured speak or wait
    State->>State: Recheck epoch, grant, quota and pause
    alt Still authorized
        State->>Discord: Persisted outbox send
        Discord-->>State: Confirmed message ID
    else Stale or uncertain
        State->>State: Discard or hold without blind resend
    end
```

An ordinary bot-to-bot message never replenishes quota. A human interruption invalidates an in-flight decision so the next decision can follow the new topic. Unknown delivery is held for reconciliation; a process restart does not grant fresh spending.

```mermaid
sequenceDiagram
    participant Chat as Accepted room history
    participant Ledger as Continuity SQLite
    participant Job as Independent summary session
    participant Main as Main persona session
    participant Git as Canonical private Git
    Chat->>Ledger: Append source identity and revision
    Ledger->>Job: Frozen uncovered segment and lease token
    Job-->>Ledger: Attributed structured summary
    Ledger->>Ledger: Validate refs, publish atomically
    Ledger->>Job: Bounded rollup of pending batches when needed
    Main->>Ledger: Claim available summary context
    Main-->>Ledger: Native transcript receipt confirms reading
    Main->>Ledger: Begin explicit save and freeze batch refs
    Main->>Git: Write only assessed patch or journal files
    Git-->>Ledger: Checked encrypted commit
    Ledger->>Ledger: Mark those batch refs assessed
```

Cutting by a last timestamp alone can miss late events. Source identity is `channel_id/message_id` plus a revision fingerprint; per-persona coverage tracks exactly what a batch included. Default segment triggers are 20 minutes idle, two hours maximum age, 60 messages, or the 24,000-character bound. The database transaction freezes inputs; no model or Git call holds that transaction open.

Leases and tokens reject stale workers. A rollup can summarize pending segments before the main persona visits; original segments remain the provenance source. `received` means a native turn consumed a batch; `assessed` means an explicit canonical save considered it. Neither is inferred from a job saying it succeeded.

Edit/deletion revisions can be ingested by the ledger, but the preview Discord connector does not promise complete edit/delete event ingestion. A missing record in a partial history fetch is not a deletion. See [release limitations](release.md).
