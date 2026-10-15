# Deploy FloodOps to Vercel

## Preview deployment

This repository is configured for Vercel's Python Functions and static hosting:

- `web/` is the static output directory.
- `/api/*` is rewritten to the Python function in `api/index.py`.
- The function reuses the existing application and repository layers.
- No build command or third-party Python packages are required.

Import this repository into Vercel with the project root set to the repository root and the framework preset set to **Other**. Keep the output directory as `web` (already set in `vercel.json`). Deploy a preview, then open `/api/state` on the deployed domain to confirm the API is responding.

## Persistence limitation

The Vercel adapter puts SQLite at `/tmp/floodops.sqlite3` so the demo can initialize in a function instance. `/tmp` is temporary instance storage: it can be cleared when an instance stops, and separate instances do not share it. As a result, simulation changes on a Vercel deployment are not durable or consistent across requests, visitors, or deployments. Treat this as a UI/API preview only.

Before using hosted state for final development or collaboration, replace SQLite with a managed PostgreSQL database (for example, a Vercel Marketplace Postgres provider) and put its connection string in a server-only environment variable. The current repository adapter is SQLite-specific; setting `DATABASE_URL` alone will not enable PostgreSQL. Keep production credentials out of source control and configure them in Vercel Project Settings for each environment.

## Local development

Local runs still use `data/floodops.sqlite3` unless `FLOODOPS_DB_PATH` is set:

```sh
python3 server.py
```

The Vercel Function adapter sets `FLOODOPS_DB_PATH=/tmp/floodops.sqlite3` only inside the hosted function.
