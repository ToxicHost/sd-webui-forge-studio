# Upstream Sync Agent Prompt

Create a dedicated upstream integration branch and follow `docs/05_UPSTREAM_SYNC_PLAYBOOK.md`.

Do not merge directly into `main` or `develop`.

Required output:

- old/new upstream commits;
- merge command;
- conflict classification;
- behavior changes in model loading, processing, memory, scripts, preview, or APIs;
- patch inventory changes;
- tests performed;
- same-session performance control;
- completed upstream sync report;
- merge recommendation.

Do not resolve conflicts by simply preferring ours or theirs. Explain each behavioral resolution.
