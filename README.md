# arthouse-ops

An n8n automation that unifies ArtHouse Studio's WordPress form submissions into
a single Google Sheet with a Looker Studio dashboard on top, plus a Claude LLM
classifier that categorizes Contact Us messages.

This is the initial project scaffold. The full master guide, node-by-node flow,
and the nine-step setup walkthrough are added on the `feat/dashboard-mvp` branch.

## Quick map

- `docker-compose.yml` runs n8n locally in Docker.
- `.env.example` lists every configuration value you need to provide.
- `workflows/` holds the workflow and credential definitions.
- `scripts/` holds the import and run helpers.
- `docs/` holds the step-by-step setup guides.

See `.env.example` to get started.
