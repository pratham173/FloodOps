# Architecture decisions

## Scope

The first increment is a functional, local single-user simulation platform. The supplied brief defines a broad system; deployment credentials and a hosted database are not present in this workspace. We use Python's standard-library HTTP server and SQLite as a runnable local persistence adapter. The domain API is intentionally independent from HTTP and SQL. The browser is a small accessible ES-module application. This avoids claiming that unconfigured Supabase services are active.

## Layers

- **Presentation:** `web/` modules and CSS. Fetches typed-by-contract JSON; no dispatch decisions.
- **Application/API:** `server.py`. Parses and validates requests; commits use cases; returns consistent JSON errors.
- **Domain:** `domain/engine.py`. Graph pathfinding, scoring, centralized dispatch, deterministic negotiation, disruptions, and safety audit.
- **Infrastructure:** `infrastructure/repository.py`, `migrations/001_initial.sql`. SQLite access and transaction boundaries.

## State and persistence

Normalized records are scoped to a project and simulation session. A single synthetic project/session is provisioned at first startup. Event IDs and idempotency keys make retry behavior explicit. Assignment history is retained as status transitions in assignment rows. SQLite uses foreign keys, WAL, and immediate transactions for assignment mutations. This increment checks shelter occupancy against configured capacity but does not yet implement incoming shelter reservations. A production hosted deployment requires replacing this adapter with PostgreSQL transactions/RLS and adding verified authentication before exposing non-loopback access.

## Simulation semantics

Graph coordinates are fictional grid units, not geographic coordinates. Dijkstra uses positive integer edge costs. Closed, prohibited, or unsafe edges cannot participate. Central dispatch sorts feasible incident/resource pairs by documented priority, travel cost, then stable IDs. Negotiation creates deterministic scored bids, resolves each incident by score and stable resource ID, and records rejected proposals. A deterministic tick counter—not wall time—controls event processing.

## Security boundary

This local increment is bound to loopback and has no multi-user authentication. It must not be exposed to a network or used for real response coordination. Before any hosted deployment, implement Supabase Auth, per-project RLS, trusted server execution, rate limiting, and security integration tests.
