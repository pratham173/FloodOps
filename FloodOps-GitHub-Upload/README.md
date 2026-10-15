# FloodOps

FloodOps is a synthetic flood response simulation and research tool. All locations and scenarios are fictional; this is not an emergency dispatch system.

## Run locally

Requirements: Python 3.11+ (standard library only).

```sh
python3 server.py
```

Open http://127.0.0.1:8000. State is stored in `data/floodops.sqlite3`. Use **Reset scenario** to restore the seeded synthetic scenario. The API binds to loopback by default.

## Deploy to Vercel

The repository includes a Vercel configuration for static frontend hosting and a Python Function API. See [VERCEL.md](VERCEL.md) for import settings and the hosted storage limitation. The SQLite-backed hosted preview is ephemeral; connect a managed PostgreSQL database and migrate the repository adapter before relying on persistent hosted state.

## Architecture and first milestone

```text
Browser (semantic HTML, CSS, ES modules)
  └─ JSON API /api/* (server.py; validation and application use cases)
       └─ domain/engine.py (pure graph, dispatch, disruption, audit logic)
       └─ infrastructure/repository.py (SQLite transactions and persistence)
            └─ data/floodops.sqlite3 (versioned SQL schema)
```

Module boundaries: `domain/` contains deterministic decisions and invariants; `infrastructure/` owns SQL and transactions; `server.py` validates API inputs; `web/` renders server-backed state. Business rules do not live in browser handlers.

### Data model

Profiles → projects → sessions → (nodes, edges, neighborhoods, incidents, resources, shelters, events, assignments, bids, audits, snapshots). Sessions own scenario state. Assignments reference incidents and resources and have unique active-incident and active-resource indexes. Event IDs and idempotency keys support safe event retries. Assignment transitions remain visible in history. Shelter occupancy is constrained to capacity; incoming reservations are not yet implemented. The local build uses SQLite to run without external setup; the schema keeps relational constraints and is structured for a later PostgreSQL migration.

### Invariants

- An assignment requires a pending/verified incident, available non-failed capable resource, and a feasible path over open, safe edges.
- One active assignment per resource and incident.
- Shelter occupancy never exceeds capacity; incoming reservations are not implemented yet.
- Simulation ticks process events in deterministic `(tick, sequence, id)` order, once per event ID.
- Disruption handling records before/after decisions, audits resulting state, and leaves infeasible requests unresolved.
- Same scenario, seed, and algorithm produce the same initial report confidence values and domain decisions.

### Acceptance criteria for this increment

- A fresh start seeds a clearly labeled synthetic session and persists it to SQLite.
- API values are read from the database; dashboard, incidents, network, and decision ledger reflect the same session.
- User can pause/resume/step, choose a real dispatch strategy, inject disruptions, and reset the selected scenario.
- Dijkstra excludes closed or unsafe links; capacity, resource availability, and assignment invariants are checked server-side.
- The simultaneous three-disruption event is processed by domain logic and produces an audit record.
- All important actions return explicit errors on invalid input and persist their decisions.

## Implementation status

This increment covers the local persistent foundation and core deterministic simulation workflow. It uses Python and SQLite with a semantic HTML/CSS/JavaScript client because the workspace has no configured Supabase project and no frontend package toolchain. This is a documented implementation alternative, not the prescribed React/Supabase production stack.

Still incomplete: hosted Supabase/PostgreSQL, account lifecycle and row-level security, collaborative project membership, shelter reservations and redirection, incident edit/resolve workflows, report reconciliation, exhaustive event audit explanations, scenario comparisons and fairness analytics, CSV export, reverse historical replay, and full end-to-end/browser security verification. The local demo is intentionally single-user and loopback-only. These are follow-on milestones, not represented as completed controls.

## Tests

Run `python3 -m unittest discover -s tests -v`.

