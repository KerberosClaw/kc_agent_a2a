# Contributing

Start at [docs/index.md](docs/index.md), then read the contract and tests for the component you change. Use Python 3.12+, fictional fixtures and temporary state. Keep docs in Markdown and diagrams in Mermaid; update the navigation links with each new page. English and Traditional Chinese READMEs must describe the same behavior.

Run [the verification commands](docs/testing.md). Changes to grants, audience, delivery, receipts or canonical saves need failure/recovery tests, not only a happy-path screenshot. Do not add personal anecdotes to prompts or make a quota a mandatory number of replies.

Use a topic branch and pull request. Describe the resulting behavior, validation and remaining limits. Do not rewrite shared history to remove ordinary attribution. Never commit runtime files or credentials; follow [SECURITY.md](SECURITY.md) for suspected exposure.

The stable boundary in this preview is documented behavior, not every internal Python signature. Propose compatibility-affecting changes before silently migrating existing state. Preserve ledgers and unrelated dirty files; don't fix an upgrade by resetting a user's state.
