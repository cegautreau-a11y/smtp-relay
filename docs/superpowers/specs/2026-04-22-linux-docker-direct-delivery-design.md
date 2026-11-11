# SMTP Relay — Linux Docker + Direct Delivery Mode

**Date:** 2026-04-22
**Author:** Christopher McGrath
**Status:** Draft

---

## Overview

Migrate the SMTP Mail Relay from Windows to Linux Docker and add a Direct Delivery mode where emails are delivered straight to destination MX servers via MX lookup, bypassing the upstream relay.

---

## Goals

1. Run the application in a Linux Docker container
2. All configuration overridable via environment variables (with defaults baked into image)
3. Add a Direct Delivery mode switchable via the web UI config page
4. Maintain backward compatibility with existing relay mode

---

## Design

### 1. Docker Setup

#### Base Image
- `python:3.12-slim` (Debian-based, official Python image)

#### Files
- `Dockerfile` — builds the application image
- `docker-compose.yml` — full deployment with environment variables
- `.env.example` — all available env vars with descriptions

#### Startup Behavior
- Entry point: `run.py` (unchanged logic, respects env var overrides)
- No requirement for Windows-specific paths
- Logs written to stdout (Docker default) — no file paths needed

#### Exposed Ports
- `8025` — Web interface
- `2525` — SMTP listener

---

### 2. Configuration Layer

#### Environment Variables (with Defaults)

| Env Var | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Flask secret key |
| `WEB_HOST` | `0.0.0.0` | Web interface bind host |
| `WEB_PORT` | `8025` | Web interface port |
| `SMTP_LISTEN_HOST` | `0.0.0.0` | SMTP listener bind host |
| `SMTP_LISTEN_PORT` | `2525` | SMTP listener port |
| `BANNER_HOSTNAME` | `relay.local` | SMTP banner hostname |
| `REQUIRE_AUTH` | `true` | Require SMTP AUTH |
| `ENABLE_TLS` | `false` | Enable TLS on listener |
| `TLS_CERT_PATH` | *(empty)* | TLS cert path |
| `TLS_KEY_PATH` | *(empty)* | TLS key path |
| `DELIVERY_MODE` | `relay` | `relay` or `direct` |
| `RELAY_HOST` | `smtp.example.com` | Upstream relay hostname |
| `RELAY_PORT` | `25` | Upstream relay port |
| `RELAY_USE_TLS` | `false` | Use SMTP_SSL for relay |
| `RELAY_USE_STARTTLS` | `true` | Use STARTTLS for relay |
| `RELAY_AUTH_USER` | *(empty)* | Relay AUTH username |
| `RELAY_AUTH_PASSWORD` | *(empty)* | Relay AUTH password |
| `RELAY_HELO_HOSTNAME` | `localhost` | HELO hostname for relay |
| `MAX_MESSAGE_SIZE` | `26214400` | Max message size in bytes |
| `MAX_RECIPIENTS` | `100` | Max recipients per message |
| `GLOBAL_RATE_LIMIT` | `1000` | Global rate limit per hour |
| `ALLOWED_SOURCE_IPS` | *(empty)* | Comma-separated IP/CIDR allowlist |
| `QUEUE_RETRY_INTERVAL` | `300` | Seconds between retries |
| `QUEUE_MAX_RETRIES` | `3` | Max retry attempts |
| `LOG_RETENTION_DAYS` | `30` | Days to retain logs |
| `DEBUG_LOGGING` | `false` | Enable debug logging |
| `DATABASE_PATH` | `smtp_relay.db` | Path to SQLite database |

#### Config File
- `config.json` baked into image at build time with default values
- Runtime: env vars override JSON values when set
- Web UI persists changes to `config.json` on disk

---

### 3. Delivery Mode Architecture

#### Mode: Relay (Existing Behavior)
```
Client → SMTP Listener (2525) → Queue → Relay Server → Upstream SMTP
```

#### Mode: Direct (New Behavior)
```
Client → SMTP Listener (2525) → MX Lookup → Direct MX → Queue (retry only on failure)
```

#### Direct Delivery Implementation

**MX Lookup:**
- Use `dns.resolver` from `dnspython` library
- Fallback: built-in socket fallback if dnspython unavailable
- Query: MX record for destination domain
- Sort by priority (lowest number = highest priority)

**Connection:**
- Connect to MX host on port 25 (default) or 587 if STARTTLS
- No authentication by default (direct delivery has no relay credentials)
- Optional: configurable direct delivery AUTH in case direct delivery requires it

**Retry Logic:**
- Reuses existing queue system for failed direct deliveries
- Failed direct deliveries marked as `failed` after `max_retries`
- Separate stats for relay vs direct delivery in logs

---

### 4. Database Schema Changes

No schema changes required. `RelayConfig` stores:

| Key | Description |
|---|---|
| `delivery_mode` | `relay` or `direct` |

---

### 5. Web UI Changes

#### Config Page
- Dropdown toggle: **Delivery Mode** — `Relay` / `Direct`
- When `Relay` selected: show relay destination fields
- When `Direct` selected: show direct delivery fields (if any)

#### Stats / Dashboard
- Existing stats remain unchanged
- No visual distinction between relay and direct delivery initially

---

### 6. File Structure

```
smtp-relay-beta/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
├── run.py
├── app.py
├── smtp_server.py
├── models.py
├── config.json.example
├── templates/
├── static/
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-22-linux-docker-direct-delivery-design.md
```

---

### 7. Dependencies

New dependency:
- `dnspython>=2.6.0` — for MX lookups

Updated `requirements.txt`:
```
Flask==3.1.1
Flask-Login==0.6.3
Flask-SQLAlchemy==3.1.1
Flask-WTF==1.2.2
SQLAlchemy==2.0.36
Werkzeug==3.1.3
WTForms==3.2.1
bcrypt==4.2.1
aiosmtpd==1.4.6
dnspython>=2.6.0
```

---

## Implementation Notes

### Direct Delivery Without DNS
- If MX lookup fails, fall back to treating the destination domain as the MX itself (A record lookup)
- Log warning about degraded delivery mode

### Security Considerations
- Direct delivery on port 25 is standard SMTP behavior
- No relay auth credentials should be required for direct mode
- Rate limits still apply regardless of mode

### Docker Networking
- Container needs UDP port 53 open for DNS queries
- No special network configuration required beyond bridge mode

---

## Out of Scope

- Mailbox storage (full mail server mode)
- IMAP/POP3 access
- Multiple domain hosting
- Web UI rebranding or theme changes

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-22 | Use `python:3.12-slim` base image | Official, well-maintained, reasonable size |
| 2026-04-22 | Env vars override config.json | Standard 12-factor app pattern for Docker deployments |
| 2026-04-22 | Minimal approach (Option A) for direct delivery | Reuses existing queue system; faster to implement; same retry logic |
| 2026-04-22 | Web UI toggle for mode switching | User-friendly; no restart required |
