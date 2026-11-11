# Env Var Bootstrap Pattern — Design Specification

**Date:** 2026-04-23
**Author:** Christopher McGrath
**Status:** Draft

---

## Overview

Change the configuration loading behavior so that environment variables are used only on the first run to bootstrap the system, then the database takes over as the sole source of truth for all subsequent runs.

---

## Goals

1. Environment variables set the initial `config.json` on first start
2. `config.json` seeds the database on first start
3. On all subsequent starts, the database is the only source of truth
4. Environment variables are completely ignored after first start
5. Database stored in a Docker volume for persistence across restarts

---

## Design

### Bootstrapping Flow

```
Container starts
    │
    ▼
Does smtp_relay.db exist?
    │
    ├─── NO ────────────────────────────────────────────────────────
    │    │
    │    ▼
    │    Read env vars → _env_to_config()
    │    │
    │    ▼
    │    Write config.json to disk
    │    │
    │    ▼
    │    create_app() → seeds DB from config.json
    │    │
    │    ▼
    │    App starts with DB as authority
    │
    └─── YES ──────────────────────────────────────────────────────
         │
         ▼
    create_app() → loads from DB only
    │
    ▼
App starts — DB is sole authority
```

### Key Behaviors

| Scenario | What Happens |
|---|---|
| Fresh container, no DB | Env vars → config.json → DB |
| Container restart (DB exists) | DB only, env vars ignored |
| DB deleted, env vars set | Env vars → config.json → DB (re-bootstrap) |
| config.json edited manually | Ignored if DB exists |

### Files Changed

| File | Change |
|---|---|
| `run.py` | `load_config()` checks DB existence; only uses env vars if DB doesn't exist |

### No Changes To

- `config.json.example` — still documents all env vars
- `docker-compose.yml` — volume mount unchanged
- `app.py` — DB loading logic unchanged
- `models.py` — RelayConfig unchanged

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-23 | Env vars only on first boot | Clean separation: Docker sets initial config, then DB is authoritative |
| 2026-04-23 | DB existence = bootstrap signal | Simple, reliable, no extra flags needed |
| 2026-04-23 | config.json written once, then ignored | Matches user requirement: env vars → config.json → DB, then DB only |
