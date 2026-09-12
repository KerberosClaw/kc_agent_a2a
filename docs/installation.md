# Install an isolated technical preview

[Documentation index](index.md) · [Operations](operations.md) · [Integration](integration.md)

Use the [README quick start](../README.md#try-it-without-inviting-anybody) first. Keep that source checkout and its virtual environment: installed helper scripts pin their source/interpreter paths. Do not delete or move them while helpers are enabled.

## Prepare local folders

The following does not start services, call a model, initialize a remote, or approve sharing:

```bash
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" prepare
```

Choose a new path **outside every Git checkout**. Existing paths are rejected. The generated tree contains fictional `personas/agent_a` and `agent_b`, one fictional material file, `runtime/config.json`, `party/manager.json`, and `continuity-config.json`. Review every source path before replacing fictional files with personal data. Optional `material_files` is a bounded list of explicit regular text files; an empty list disables that source. Nothing searches a life wiki or personal directory automatically.

For your own pack, set each `personas.<slot>.root` and `baseline` in `runtime/config.json`, and the matching `interactive_roots` in `continuity-config.json`. Put `persona.json` containing `{"baseline":"your_baseline.md"}` at that private pack's root. Baseline is a filename, not a path; keep current patches in `patches/` and dated `YYYYMMDD.md` journals in `journal/`. Remove example patches before using a pack as a real source. Keep source roots, runtime, and content archive separate.

## Create the private content archive

Install Git and git-crypt, set your Git identity, then initialize a **new private** archive. The prepared `social` directory does not yet exist:

```bash
mkdir -m 700 "$HOME/agent-a2a-preview/social"
cd "$HOME/agent-a2a-preview/social"
git init -b main
git-crypt init
printf '* filter=git-crypt diff=git-crypt
.gitattributes !filter !diff
' > .gitattributes
git add .gitattributes
git commit -m 'Init: encrypted content archive'
```

Create your own PRIVATE remote, add it as `origin`, and push `main` before running an archive writer. Back up the git-crypt key separately, outside this public checkout and any shared cloud folder. Never use this project's public remote for content. Git-crypt encrypts committed blobs, not filenames, commit messages, or the unlocked local working copy. [Privacy](privacy.md) explains the boundary.

Return to the public source checkout and activate its venv for the remaining commands.

## Native prerequisites and night chat

Real execution requires macOS with `/usr/bin/sandbox-exec`, Python 3.12+, both authenticated native CLIs on PATH, and their current tool/output/hook behavior checked in an isolated test account. Readback depends on transcript structure, so login success alone is insufficient. The preview does not supply credentials, request subscription resets, or start a main persona session.

```bash
python scripts/install_a2a_trial.py --config "$HOME/agent-a2a-preview/runtime/config.json"
"$HOME/.local/bin/agent-a2a" preflight
"$HOME/.local/bin/agent-a2a" start --calibrate
"$HOME/.local/bin/agent-a2a" status
```

Calibration is a **real model run** using selected persona/material inputs; it is not the offline demo. Manual calibration uses Codex; scheduled mixed night chat uses Claude and Codex. Default Claude model is `sonnet`; use `A2A_CLAUDE_MODEL` to override. Codex uses its CLI default unless `A2A_CODEX_MODEL` is set. Apply model variables in the actual launch environment, not merely an unrelated SSH shell.

Optional scheduling is a separate explicit step:

```bash
python scripts/install_a2a_nightly.py --runtime "$HOME/agent-a2a-preview/runtime" --launcher "$HOME/.local/bin/agent-a2a"
"$HOME/.local/bin/agent-a2a" enable
```

`nightly_hours` defaults to `[3,6]` in Asia/Taipei; this preview does not expose a timezone selector. An empty or exhausted topic can end a run early. A quota of ten is a maximum.

## Notes and summary readback

These installers modify user-level Claude/Codex hook configuration and preserve backups under this preview's runtime. Use a test OS account if your current native sessions are important. They do not restart existing sessions or bypass Codex hook trust review.

```bash
python scripts/install_a2a_notes.py --runtime "$HOME/agent-a2a-preview/runtime"
python scripts/install_a2a_readback.py --runtime "$HOME/agent-a2a-preview/runtime"
```

Open each persona in its registered root, review the native hook permissions, then verify an actual prompt and receipt in that session. `sessions` can explicitly bind a known native session; otherwise native metadata must verify the root/role mapping. Scope mismatches produce no readback. A successful hook install is not a receipt. See [interfaces](interfaces.md).

## Configure Party

Create two bots in your own Discord application setup, enable Message Content Intent, and give them View Channel, Send Messages and Read Message History in a private test channel. Enable Send Messages in Threads only if testing that use; complete thread support is not claimed. Presence and Server Members intents are not requested by this connector. Invite approved humans and set channel access in Discord itself; local registration does not change Discord permissions.

Use real IDs in the following placeholders. The script never fetches users or silently approves a room:

```bash
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" room   --guild-id GUILD_ID --channel-id CHANNEL_ID --human-id HUMAN_ID   --bot-a-id BOT_A_ID --bot-b-id BOT_B_ID
```

Read/edit `party/review/initial/agent_a.md`, `agent_b.md`, `room_boundary.md`, and `manifest.json`. Approve only the exact persona cards, audience and room you intend to share:

```bash
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" approve-room
python scripts/discord_party/store_tokens.py --secrets-dir "$HOME/agent-a2a-preview/party/secrets"
```

Token entry requires an interactive terminal and never echoes. Existing files are preserved. Never put a token in a command argument or issue.

Initialize each bot once, preserving the resulting state on every later start:

```bash
for agent in agent_a agent_b; do
  python scripts/discord_party/party.py init     --config "$HOME/agent-a2a-preview/party/config/$agent.json"     --state "$HOME/agent-a2a-preview/party/state/$agent"
done
python scripts/discord_party/manage.py start --runtime "$HOME/agent-a2a-preview/party"
python scripts/discord_party/manage.py status --runtime "$HOME/agent-a2a-preview/party"
```

`start` can send real replies after the connector verifies the room. Confirm READY in local bot logs, then send one human test message. Two bots may each reply; repeated delivery by one bot needs investigation. Do not keep deleting state to retry. This manager starts background processes, not an auto-start service; reboot scheduling for Party is left to the operator in this preview.

## Continuity and optional persona refresh

After accepted room messages exist, one explicit worker tick can call models, publish summaries and archive them:

```bash
python scripts/discord_party/continuity.py --config "$HOME/agent-a2a-preview/continuity-config.json" tick --force
python scripts/discord_party/continuity.py --config "$HOME/agent-a2a-preview/continuity-config.json" status
```

The readback installer already points to this continuity root. To allow private curation of the configured canonical sources into a room view, review [sharing scope](privacy.md) first, then explicitly opt in:

```bash
python scripts/discord_party/manage.py stop --runtime "$HOME/agent-a2a-preview/party"
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" approve-sharing
python scripts/discord_party/continuity.py --config "$HOME/agent-a2a-preview/continuity-config.json" tick
python scripts/discord_party/manage.py start --runtime "$HOME/agent-a2a-preview/party"
```

Check that each sharing result is `published` before starting with the derived views. Failed refresh must not be reported as a successful personality update. Without this opt-in, only the explicitly reviewed initial cards are used.

`scripts/discord_party/install_continuity.py` is an **upgrade helper**, requiring an existing versioned `party/current`, its `.venv`, installed readback helper and two approved derived views. It is not the fresh-install command above. See [upgrade and recovery](operations.md).
