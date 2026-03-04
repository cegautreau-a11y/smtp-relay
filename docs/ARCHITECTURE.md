# Architecture Overview

**SMTP Mail Relay v2.1.0**

This document describes the system design, component interactions, and technical decisions behind the SMTP Mail Relay.

---

## System Diagram

```
                    ┌─────────────────────────────────────────────┐
                    │            SMTP Mail Relay v2.1.0            │
                    │                                             │
  Applications      │  ┌──────────────┐    ┌──────────────────┐  │     Upstream
  & Devices         │  │  SMTP Server │    │  Queue Processor  │  │     SMTP Server
       │            │  │  (aiosmtpd)  │───▶│  (background)     │──│──▶  (Exchange,
       │  SMTP      │  │  port 2525   │    │  retry + deliver  │  │     O365, etc.)
       └───────────▶│  └──────────────┘    └──────────────────┘  │
                    │         │                     │             │
                    │         ▼                     ▼             │
                    │  ┌─────────────────────────────────────────┐│
                    │  │         SQLite Database                 ││
                    │  │  ┌─────────┐ ┌──────┐ ┌──────────┐     ││
                    │  │  │Email Log│ │Queue │ │Config    │     ││
                    │  │  │+Headers │ │      │ │Users     │     ││
                    │  │  │         │ │      │ │Domains   │     ││
                    │  │  │         │ │      │ │Credentials│     ││
                    │  │  └─────────┘ └──────┘ └──────────┘     ││
                    │  └─────────────────────────────────────────┘│
                    │         ▲                                   │
                    │         │                                   │
                    │  ┌──────────────┐                           │
   Administrators   │  │  Flask Web   │                           │
       │  HTTP      │  │  Interface   │                           │
       └───────────▶│  │  port 8025   │                           │
                    │  └──────────────┘                           │
                    └─────────────────────────────────────────────┘
```

---

## Components

### 1. Entry Point — `run.py`

The application launcher handles:
- **Auto-dependency installation** — Checks for required packages and runs `pip install` if any are missing
- **Configuration loading** — Reads `config.json` and passes it to the Flask app factory
- **Logging setup** — Configures Python logging based on config settings
- **Component startup** — Creates and starts the SMTP server, queue processor, and Flask web server
- **Graceful shutdown** — Catches `Ctrl+C` and cleanly stops all components

The launcher is designed for Windows: no Unix signals, no forking, no daemon mode. It runs as a foreground process (or via NSSM/Task Scheduler for service mode).

### 2. Web Interface — `app.py`

A Flask application created via the `create_app()` factory pattern. Key responsibilities:

- **Authentication** — Flask-Login with bcrypt password hashing, non-permanent sessions
- **Role-based access control** — Decorator-based permission checks (`@admin_required`, `@operator_required`)
- **Dashboard** — Real-time statistics, 7-day volume chart (Chart.js), recent email list
- **Configuration management** — Two-layer config (database + JSON file), reload and save-to-file capabilities
- **CRUD operations** — Users, domains, SMTP credentials, queue management
- **SMTP server control** — Start, stop, restart the SMTP listener from the web UI
- **API endpoints** — `/api/stats`, `/api/logs/recent`, and `/api/logs/<id>/detail` for AJAX updates and email detail retrieval
- **Queue management** — Individual and bulk retry/delete operations for failed messages
- **Database migration** — Automatic schema migration for role column and raw_headers column on startup
- **Context processor** — Injects `smtp_running` status and `Role` class into all templates

### 3. SMTP Server — `smtp_server.py`

Built on `aiosmtpd`, the async SMTP library. Contains four classes:

#### `RelayAuthenticator`
Validates SMTP AUTH LOGIN credentials against the `smtp_credentials` database table. Supports per-credential enable/disable.

#### `RelayHandler`
Processes the SMTP transaction lifecycle:
- **EHLO** — Records client hostname
- **MAIL FROM** — Enforces authentication, IP allowlist, domain allowlist, per-credential rate limits, and global rate limits
- **RCPT TO** — Enforces maximum recipient count
- **DATA** — Validates message size, extracts email headers, creates log and queue entries, spawns a delivery thread

#### `SmtpRelayServer`
Wraps the aiosmtpd `Controller` with:
- Start/stop/restart lifecycle management
- Automatic fallback from `0.0.0.0` to `127.0.0.1` on Windows bind failures
- Thread-aware `is_running` property that checks the controller thread health
- TLS context setup when listener TLS is enabled

#### `QueueProcessor`
Background thread that runs every 30 seconds:
- Finds queued messages where `next_retry_at` has passed
- Attempts redelivery using `RelayHandler._deliver()`
- Handles log retention cleanup — only purges successfully sent queue entries; **failed entries are retained indefinitely** until an administrator manually retries or deletes them

### 4. Database Models — `models.py`

SQLAlchemy models with SQLite backend:

| Model | Table | Purpose |
|---|---|---|
| `Role` | *(class, not a table)* | Role constants, labels, weights, permission logic |
| `User` | `users` | Web interface accounts with role-based permissions |
| `AllowedDomain` | `allowed_domains` | Sender domain allowlist |
| `SmtpCredential` | `smtp_credentials` | SMTP AUTH credentials with per-credential rate limits |
| `EmailLog` | `email_logs` | Audit trail of all processed messages including raw email headers |
| `EmailQueue` | `email_queue` | Messages pending delivery or retry |
| `RelayConfig` | `relay_config` | Runtime configuration key-value store |

#### EmailLog Schema (v2.0)

| Column | Type | Description |
|---|---|---|
| `id` | Integer (PK) | Auto-increment primary key |
| `timestamp` | DateTime | When the message was received |
| `sender` | String(255) | Envelope sender address |
| `recipients` | Text | JSON array of recipient addresses |
| `subject` | String(500) | Email subject line |
| `size_bytes` | Integer | Message size in bytes |
| `status` | String(20) | Current status: queued, sent, failed, rejected |
| `status_message` | Text | Delivery result or error message |
| `smtp_credential` | String(120) | SMTP credential username used |
| `source_ip` | String(45) | Connecting client IP address |
| `relay_server` | String(255) | Upstream relay server hostname |
| `retry_count` | Integer | Number of delivery attempts |
| `message_id` | String(255) | Email Message-ID header value |
| `raw_headers` | Text | Full raw email headers (v2.0) |

### 5. Templates — `templates/`

Jinja2 templates with a shared `base.html` layout:

| Template | Description |
|---|---|
| `base.html` | Main layout: sidebar navigation, header with SMTP status, flash messages, auto-refresh JS |
| `login.html` | Authentication page |
| `dashboard.html` | Statistics grid, 7-day chart, recent emails table |
| `logs.html` | Searchable email log with pagination and detail modal for viewing full email headers |
| `queue.html` | Queued, processing, and failed message tables with individual and bulk retry/delete actions |
| `domains.html` | Domain allowlist management |
| `credentials.html` | SMTP credential management |
| `config.html` | Configuration editor with reload/save buttons |
| `users.html` | User management with role assignment |
| `profile.html` | Self-service password change and account details |
| `error.html` | Error page for authenticated users |
| `error_standalone.html` | Error page for unauthenticated users (avoids template crash loops) |

### 6. Static Assets — `static/`

| File | Description |
|---|---|
| `css/style.css` | Complete responsive stylesheet with role badge styles, dark sidebar, card layouts, log detail modal styles |

External CDN resources:
- **Font Awesome 6.5** — Icons
- **Inter** — Google Fonts
- **Chart.js 4.4** — Dashboard chart (loaded only on dashboard page)

---

## API Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/stats` | GET | Required | Dashboard statistics (sent/failed today, queue count, SMTP status) |
| `/api/logs/recent` | GET | Required | Last 20 email log entries as JSON |
| `/api/logs/<id>/detail` | GET | Required | Full detail for a single email log entry including raw headers |

---

## Mail Flow

### Inbound (Application → Relay)

```
1. Application connects to relay on port 2525
2. EHLO exchange
3. AUTH LOGIN (if require_auth is enabled)
   → Validated against smtp_credentials table
4. MAIL FROM: sender@domain.com
   → IP allowlist check
   → Domain allowlist check
   → Per-credential rate limit check
   → Global rate limit check
5. RCPT TO: recipient@example.com
   → Max recipients check
6. DATA (message body)
   → Max message size check
   → Email headers extracted and stored
   → EmailLog entry created (status: queued)
   → EmailQueue entry created
   → Delivery thread spawned
7. 250 Message accepted for delivery
```

### Outbound (Relay → Upstream)

```
1. Delivery thread connects to upstream SMTP server
2. If use_tls: SMTP_SSL connection
   If use_starttls: SMTP connection → STARTTLS upgrade
3. EHLO with configured helo_hostname
4. AUTH LOGIN (if relay auth credentials configured)
5. SENDMAIL with original envelope
6. On success:
   → Queue entry status → sent
   → Log entry status → sent
7. On failure:
   → Increment retry count
   → If retries < max_retries: reschedule (status → queued)
   → If retries >= max_retries: mark as failed (status → failed)
   → Failed entries are retained in the queue with their raw message
     data intact — they are never automatically discarded
   → Administrators can manually retry or delete from the Queue page
```

---

## Security Design

### Authentication
- Web interface uses Flask-Login with bcrypt-hashed passwords
- SMTP uses separate credentials (also bcrypt-hashed) from the `smtp_credentials` table
- Sessions are non-permanent and expire on browser close
- A random secret key is generated on each server restart, invalidating all existing sessions

### Authorization
- Four-tier RBAC with privilege escalation prevention
- Role checks enforced at both the route level (decorators) and template level (conditional rendering)
- Users cannot escalate their own privileges or manage users at or above their role level

### Input Validation
- Domain allowlist restricts sender addresses
- IP allowlist restricts connecting clients
- Per-credential and global rate limits prevent abuse
- Message size limits prevent resource exhaustion
- CSRF protection via Flask-WTF

---

## Database Migrations

The application performs automatic schema migrations on startup to ensure compatibility with older databases:

| Migration | Added In | Description |
|---|---|---|
| `_migrate_roles()` | v1.0.0 | Adds `role` column to `users` table, sets existing admins to `super_admin` |
| `_migrate_raw_headers()` | v2.0.0 | Adds `raw_headers` column to `email_logs` table for storing email headers |

### Queue Retention Policy (v2.0.1)

The automatic cleanup process (`cleanup_old()`) only purges **successfully sent** queue entries after the configured `log_retention_days` period. Failed queue entries are **never automatically deleted** — they remain in the system with their full `raw_message` data until an administrator takes explicit action (retry or delete) via the Queue page in the web interface. This ensures no undelivered email is ever silently discarded.

Migrations are idempotent — they check for the column's existence before attempting to add it, so they are safe to run on already-migrated databases.

---

## Technology Stack

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.11 — 3.14 |
| Web Framework | Flask | 3.1.x |
| SMTP Server | aiosmtpd | 1.4.x |
| Database | SQLite via SQLAlchemy | 2.0.x |
| Authentication | Flask-Login + bcrypt | 0.6.x / 4.2.x |
| Frontend | HTML5 + CSS3 + Vanilla JS | — |
| Charts | Chart.js | 4.4.x |
| Icons | Font Awesome | 6.5.x |
| Font | Inter (Google Fonts) | — |

---

## Design Decisions

### Why SQLite?
SQLite requires zero configuration, no separate database server, and stores everything in a single file. For a mail relay handling up to a few thousand messages per hour, SQLite provides more than adequate performance. The database file can be easily backed up by copying a single file.

### Why aiosmtpd?
aiosmtpd is the modern replacement for the deprecated `smtpd` module in Python's standard library. It provides an async SMTP server with built-in support for AUTH, TLS, and size limits. The `Controller` class runs the async event loop in a separate thread, making it compatible with Flask's synchronous model.

### Why No Separate Worker Process?
The relay uses threads instead of separate processes to keep deployment simple on Windows. The SMTP server, queue processor, and web interface all run in a single Python process. This avoids the complexity of inter-process communication and makes the application easy to run as a Windows service.

### Why Two-Layer Configuration?
The config.json file provides a version-controllable, human-readable configuration baseline. The database layer allows runtime changes without restarting the application. This dual approach gives administrators flexibility while maintaining a clear configuration source of truth.

### Why Store Raw Headers? (v2.0)
Email headers contain critical diagnostic information — routing paths, authentication results, content types, mailer identification, and timestamps. Storing them at relay time provides a complete audit trail for troubleshooting delivery issues, verifying sender authenticity, and diagnosing formatting problems without needing access to the original sending application.