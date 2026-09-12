# Privacy and trust boundaries

[Documentation index](index.md) · [Security reporting](../SECURITY.md) · [Integration](integration.md)

## What may cross a boundary

| Source | Consumer | Permission and limit |
| --- | --- | --- |
| Human-authored note body | Named recipient native session | Explicit current-turn relay authority; body removed only after verified receipt |
| Own baseline and all current patches | Own nightly native session | Explicit configured source roots, protected from writes |
| Canonical private persona | Private curator and model audit | Explicit sharing opt-in; do not treat private source access as room disclosure approval |
| Approved persona card/view | Room native worker | Hash-bound manifest, exact room and audience |
| Room history | Summary worker and that persona's main session | Attributed accepted events, grant history window, scoped readback |
| Public poke topics / nonprivate night material | Curator, then room | Selected source types, exact provenance, independent model review |
| Native structured reply | Discord | Recheck grant/epoch/quota and validate tool trace before sending |

The sharing opt-in permits abstract voice/interaction traits, facts already in the reviewed card, public topics and nonprivate conversational material. It does not approve new personal facts, private quotations, health/work/location details or another participant's private history. Two-stage model review is a fallible content filter, not a proof of non-disclosure. For sensitive use, keep sharing disabled and hand-review static cards.

Group chat data is untrusted input. It cannot authorize computer operation, expand recipients, change persona roots, acknowledge notes or commit a canonical save. Summary and curation jobs have web disabled. Room web lookup is optional and limited to the allowed native tools; it can reveal the query to providers and encounter malicious pages. Tool trace checks reject unauthorized actions, but the native worker is not advertised as a secure sandbox against a compromised CLI.

The nightly source guard denies writes to configured source paths; it allows other host behavior needed by the native CLI. Filesystem access by trusted coordinators, local account compromise, malicious dependencies and provider-side handling are separate risks. Use a separate OS account/test room when evaluating.

## Storage and publication

Public source contains generic templates and synthetic tests only. Tokens, local IDs, review cards, chats, SQLite state, logs, canonical packs and encryption keys stay outside this repository. `.gitignore` is not a guarantee: inspect the staged tree before every public commit.

Tokens use local files with mode 600 under a mode-700 directory. The token helper never echoes and refuses overwrite/symlink targets. Rotation is an explicit operator action. git-crypt protects committed content blobs; **filenames, commit metadata, unlocked files and local SQLite remain readable to the account**. Do not put personal details in commit messages. Git backups must point to a separately verified private remote.

Source messages, summaries and saves can contain third-party information. Obtain permission for the intended audience, including old history when adding somebody. Do not infer identity, pronouns or consent from a display name. Keep independent personas in separate roots; never load the other persona's patch corpus as your own.

The preview does not automatically delete every derived copy after a Discord edit/delete, nor promise cryptographic erasure. Plan retention and revocation as an operator responsibility; see [release limits](release.md).
