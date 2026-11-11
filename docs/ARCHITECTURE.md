# Architecture Overview

**SMTP Mail Relay v5.0.1**

This document describes the system design, component interactions, and technical decisions behind the SMTP Mail Relay.

---

## System Diagram

```
┌─────────────────────────────────────────────┐
                     │            SMTP Mail Relay v5.0.1            │
                     │                                             │
   Applications      │  ┌──────────────┐    ┌──────────────────┐  │     Upstream
   & Devices         │  │  SMTP Server │    │  Queue Processor  │  │     SMTP Server
        │            │  │  (aiosmtpd)  │───▶│  (background)     │──│──▶  (Exchange,
        │  SMTP      │  │  port 2525   │    │  retry + deliver  │  │     O365, etc.)
        └───────────▶│  └──────────────┘    └──────────────────┘  │
                     │         │                     │             │
                     │         ▼                     ▼             │
                     │  ┌─────────────────────────────────────┐   │
                     │  │      PostgreSQL Database             │   │
                     │  │  ┌─────────┐ ┌──────┐ ┌──────────┐  │   │
                     │  │  │Email Log│ │Queue │ │Config    │  │   │
                     │  │  │+Headers │ │      │ │Users     │  │   │
                     │  │  │+Msg-ID  │ │      │ │Domains   │  │   │
                     │  │  │         │ │      │ │Credentials│  │   │
                     │  │  └─────────┘ └──────┘ └──────────┘  │   │
                     │  └─────────────────────────────────────┘   │
                     │         ▲                                   │
                     │         │                                   │
                     │  ┌──────────────┐                           │
                     │  │  Flask Web   │                           │
   Administrators   │  │  Interface   │                           │
        │  HTTP      │  │  port 8025   │                           │
        └───────────▶│  └──────────────┘                           │
                     └─────────────────────────────────────────────┘

                     ┌─────────────────────────────────────────────┐
                     │           Delivery Path Options              │
                     │                                             │
                     │   [Relay Path]  ────▶ Upstream SMTP Server   │
                     │        │                                     │
                     │        ▼                                     │
                     │   MX Lookup ────▶ Direct delivery to MX     │
                     │                                             │
                     └─────────────────────────────────────────────┘
```

---

## Components

### 1. Entry Point — `run.py`

The application launcher handles:
- **Dependency management** — Checks whether the required packages can be imported at startup and runs `pip install -r requirements.txt` only when something is missing. Dependencies are never force-upgraded on an already-working installation.
- **Configuration loading** — Bootstrap semantics: `DATABASE_URL` is required and read on every boot. On first boot (empty database) environment variables seed the `relay_config` rows; on every later boot the database owns the configuration and environment variables are ignored. Set-but-ignored variables are reported with a startup warning (compared against the live database values). See `load_config()` and `_warn_ignored_env_vars()` in `run.py`.
- **Logging setup** — Configures Python logging based on config settings
- **Component startup** — Creates the SMTP server and queue processor objects, then starts them INSIDE the gunicorn worker via the `post_fork` hook. Starting the listener before the fork is forbidden: `fork()` duplicates the listening socket FD into the worker, and the inherited copy keeps the port bound forever (EADDRINUSE on every restart, nothing accepting — the v4.9.0 prod incident)
- **Graceful shutdown** — Catches `Ctrl+C` and cleanly stops all components

The launcher targets Linux/Docker with gunicorn (single worker; the worker hosts web + SMTP listener + queue processor). A Flask dev-server fallback exists for environments without gunicorn.

### 2. Web Interface — `app.py`

A Flask application created via the `create_app()` factory pattern. Key responsibilities:

- **Authentication** — Flask-Login with bcrypt password hashing, non-permanent sessions
- **Role-based access control** — Decorator-based permission checks (`@admin_required`, `@operator_required`)
- **Dashboard** — Real-time statistics, 7-day volume chart (Chart.js), recent email list
- **Configuration management** — Single-layer configuration store in the database (`relay_config`), edited through the web UI
- **CRUD operations** — Users, domains, SMTP credentials, queue management
- **SMTP server control** — Start, stop, restart the SMTP listener from the web UI
- **API endpoints** — `/api/stats`, `/api/logs/recent`, and `/api/logs/<id>/detail` for AJAX updates and email detail retrieval
- **Queue management** — Individual and bulk retry/delete operations for failed messages, with confirmation prompts (v3.0.1)
- **Database migration** — Automatic schema migration on startup via `_migrate_schema()` and `_migrate_roles()`
- **Context processor** — Injects `smtp_running` status, `Role` class, and version number into all templates (v3.0.0)
- **Connection pooling** — Pool timeout bounds request waits (a 500 instead of an indefinite spin); `pool_pre_ping` replaces connections dropped server-side by PostgreSQL

### 3. SMTP Server — `smtp_server.py`

Built on `aiosmtpd`, the async SMTP library. Contains four main classes (plus the DKIM signer and the delivery-snapshot/error helpers):

#### `RelayAuthenticator`
Validates SMTP AUTH LOGIN credentials against the `smtp_credentials` database table. Supports per-credential enable/disable.

#### `RelayHandler`
Processes the SMTP transaction lifecycle:
- **EHLO** — Records client hostname
- **MAIL FROM** — Enforces authentication, IP allowlist, domain allowlist, per-credential rate limits, and global rate limits
- **RCPT TO** — Enforces maximum recipient count
- **DATA** — Validates message size, extracts all email headers and Message-ID, creates log and queue entries, commits the DB session, then spawns a delivery thread

#### `SmtpRelayServer`
Wraps the aiosmtpd `Controller` with:
- Start/stop/restart lifecycle management; `restart()` waits (bounded poll) for the old listener's port to actually free before rebinding — a blind sleep raced the async socket release and failed with `EADDRINUSE`
- Binds the configured host/port; bind failures **raise** — there is no loopback fallback (removed in v4.9.0: a silent `127.0.0.1` rebind breaks the Docker port mapping while the server looks "running"; the product is Linux/Docker only)
- Thread-aware `is_running`: the owning process probes the controller thread; forked gunicorn workers infer liveness from the `_smtp_heartbeat` row, which the queue tick refreshes **only while the listener thread is alive** (a dead listener goes stale ≤90 s and the UI reports Stopped)
- TLS context setup when listener TLS is enabled

#### `QueueProcessor`
Background thread that runs every 30 seconds:
- Finds queued messages where `next_retry_at` has passed
- Collects the pending queue IDs inside an app context, then releases the context before spawning any threads
- Spawns a separate daemon delivery thread per message — it does **not** call `_deliver()` synchronously, so the queue loop never blocks on network I/O
- Handles log retention cleanup — only purges successfully sent queue entries; **failed entries are retained indefinitely** until an administrator manually retries or deletes them

#### Delivery Threads
Each message is delivered in its own daemon thread (`threading.Thread`), started either by the `DATA` handler after the message is accepted or by the `QueueProcessor` when a retry is due. Delivery runs in **three phases** so that no database session is ever held across network I/O:

1. **Read + snapshot** — one short-lived app context loads the `EmailQueue` row (skipping silently if it is missing or no longer `queued`), marks it `processing`, creates the pending per-recipient rows if ingest hasn't already, and snapshots every plain value the SMTP phase needs: sender, recipients, raw message bytes, the decrypted upstream credentials (relay and direct — decryption needs the app secret key), relay/direct host, port and TLS flags (including MX resolution for direct mode), HELO hostname, delivery mode, retry settings (`queue_retry_interval`, `queue_max_retries`), and the DKIM-signed message bytes (signing runs here because the `DkimKey` lookup needs the app context). The DB session is then explicitly removed — **before any socket is opened**. Only plain Python values cross this boundary; no ORM objects.
2. **SMTP exchange** — pure `smtplib` work with no app context and no DB session: connect (implicit TLS and/or STARTTLS per the flags), EHLO, AUTH when usable credentials are configured, SENDMAIL. A bounded connection+send transcript is appended to the snapshot as the exchange proceeds. Direct mode resolves MX **per recipient domain, isolated per domain**: a domain that cannot be resolved (NULL MX / nonexistent) or whose upstream rejects authoritatively fails only that domain's recipients; a transient failure leaves only that domain's recipients pending for retry. Exceptions are captured into a result (success flag + error) rather than written to the DB mid-flight.
3. **Write** — a fresh app context reloads the queue row by id (if it was deleted or requeued via the web UI during the SMTP exchange, the outcome is logged and skipped gracefully), then records the outcome **per recipient**: every still-pending `EmailRecipient` row becomes `sent` (with the transcript on its `attempt_log`) when the upstream accepted DATA for all recipients; a partial RCPT refusal immediately marks the accepted addresses `sent` and leaves the refused ones `pending` with their own RCPT refusal line as the retry narrows the queue row to them. Direct-mode outcomes (`_DeliveryOutcomes`) carry the same split per domain: recipients on undeliverable domains or hard-rejected upstreams fail immediately with their reason, delivered domains are marked `sent`, and only transiently-failed domains retry. The message-level `EmailLog` status is derived from the recipient rows — all sent → `sent`, all failed → `failed`, a mix → `exception`. On failure the retry policy applies: `retry_count += 1`, `next_retry_at = now + interval × retry_count` (linear backoff), final `failed`/`exception` once `retry_count >= max_retries`.

Because the session is released before the network phase, a slow or unresponsive upstream can no longer pin a database connection for the ~30-second connect timeout; dashboard and API requests stay responsive while deliveries are in flight.

**Failure reports (v4.7.0):** when a message finalizes with undeliverable recipients (retries exhausted or permanent rejection), the engine enqueues a plain-English report back to the original sender from `postmaster@<banner hostname>` — one line per failed address with a human-readable reason. Reports travel through the normal queue and carry `Auto-Submitted: auto-generated`, so they never trigger reports of their own (toggle: `send_failure_reports`, on by default).

**Dashboard statistics (v4.7.0):** all recipient-based counts (Sent Today, Failed Today, This Hour, Queued, the 7-day chart) are anchored on `email_recipients.updated_at` — the moment each outcome happened — not on message ingest time, so a message received yesterday and delivered today counts in today's numbers. Since v4.8.1 the day boundary itself is **local midnight in the server's timezone** (`TZ` env, IANA name, default UTC — read on every boot): the counters reset when the operator's day rolls over, and the 7-day chart buckets outcomes by local calendar date (aggregated in Python; a SQL `date()` grouping would split evening outcomes onto the next UTC day).

### 4. Database Models — `models.py`

SQLAlchemy models with PostgreSQL backend:

| Model | Table | Purpose |
|---|---|---|
| `Role` | *(class, not a table)* | Role constants, labels, weights, permission logic |
| `User` | `users` | Web interface accounts with role-based permissions |
| `AllowedDomain` | `allowed_domains` | Sender domain allowlist |
| `SmtpCredential` | `smtp_credentials` | SMTP AUTH credentials with per-credential rate limits |
| `EmailLog` | `email_logs` | Audit trail of all processed messages including raw email headers |
| `EmailRecipient` | `email_recipients` | Per-recipient delivery outcome for one message (status, per-address reason, last attempt transcript) — added v4.6.0 |
| `EmailQueue` | `email_queue` | Messages pending delivery or retry |
| `RelayConfig` | `relay_config` | Runtime configuration key-value store |

#### EmailLog Schema

| Column | Type | Description |
|---|---|---|
| `id` | Integer (PK) | Auto-increment primary key |
| `timestamp` | DateTime | When the message was received |
| `sender` | String(255) | Envelope sender address |
| `recipients` | Text | JSON array of recipient addresses |
| `subject` | String(500) | Email subject line (stored RFC 2047-decoded since v4.6.0; `raw_headers` keeps the original encoding) |
| `size_bytes` | Integer | Message size in bytes |
| `status` | String(20) | Derived status: queued, sent, failed, or `exception` (partial delivery — some recipients delivered, some failed; v4.6.0). Queue rows additionally pass through `processing` while a delivery thread holds them |
| `status_message` | Text | Delivery result or error message |
| `smtp_credential` | String(120) | SMTP credential username used |
| `source_ip` | String(45) | Connecting client IP address |
| `relay_server` | String(255) | Upstream relay server hostname |
| `retry_count` | Integer | Number of delivery attempts |
| `message_id` | String(255) | Email Message-ID header value (added v2.2.0 migration) |
| `raw_headers` | Text | Full raw email headers (added v2.0.0 migration) |

#### EmailRecipient Schema (v4.6.0)

| Column | Type | Description |
|---|---|---|
| `id` | Integer (PK) | Auto-increment primary key |
| `email_log_id` | Integer (FK → email_logs) | The message this recipient belongs to |
| `address` | String(255) | Envelope recipient address |
| `status` | String(20) | `pending` → `sent` / `failed` as delivery outcomes arrive |
| `status_message` | Text | Why THIS address is in this state (e.g. the upstream RCPT refusal line) |
| `attempt_log` | Text | Bounded connection+send transcript of the last delivery attempt for this address |
| `created_at` / `updated_at` | DateTime | Row timestamps |

Rows are created `pending` at DATA ingest; a startup migration backfills one row per address for every message logged before v4.6.0, carrying the message-level status forward (which specific address failed is unknowable for pre-upgrade history, so each recipient inherits the message verdict).

### 5. Templates — `templates/`

Jinja2 templates with a shared `base.html` layout:

| Template | Description |
|---|---|
| `base.html` | Main layout: sidebar navigation with version badge (v3.0.0), header with SMTP status, flash messages, auto-refresh JS |
| `login.html` | Authentication page |
| `dashboard.html` | Statistics grid, 7-day chart, recent emails table |
| `logs.html` | Searchable email log with pagination and detail modal for viewing full email headers and Message-ID |
| `queue.html` | Queued, processing, and failed message tables with confirmation prompts for delete actions (v3.0.1) and informational notice for processing messages |
| `domains.html` | Domain allowlist management |
| `credentials.html` | SMTP credential management |
| `config.html` | Configuration editor (database-backed settings) |
| `dkim.html` | DKIM key management (generate/view DNS records/delete) |
| `users.html` | User management with role assignment |
| `profile.html` | Self-service password change and account details |
| `error.html` | Error page for authenticated users |
| `error_standalone.html` | Error page for unauthenticated users (avoids template crash loops) |

### 6. Static Assets — `static/`

| File | Description |
|---|---|
| `css/style.css` | Complete responsive stylesheet including redesigned UI (v3.0.0) with enhanced color palette, improved dark mode, better stat cards, smoother animations, improved tables and buttons, role badge styles, dark sidebar, card layouts, log detail modal, scrollable headers block with styled scrollbar |

External CDN resources:
- **Font Awesome 6.5** — Icons
- **Inter** — self-hosted from `/static/fonts/inter-variable.woff2` (no CDN)
- **Chart.js 4.4** — Dashboard chart (loaded only on dashboard page)

---

## API Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/stats` | GET | Required | Dashboard statistics (sent/failed today, queue count, SMTP status) |
| `/api/logs/recent` | GET | Required | Last 20 email log entries as JSON |
| `/api/logs/<id>/detail` | GET | Required | Full detail for a single email log entry including raw headers and Message-ID |

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
   → Email headers extracted and stored (including Message-ID)
   → EmailLog entry created (status: queued)
   → EmailQueue entry created
   → DB session committed and closed
   → Delivery thread spawned
7. 250 Message accepted for delivery
```

### Outbound (Relay → Upstream)

```
1. Delivery thread — phase 1, read + snapshot (one short app context;
   the DB session is removed at the end of the phase — see
   "Delivery Threads" above)
   → Reads the queue entry and relay config
   → Marks status as 'processing'
   → Snapshots plain values (credentials decrypted, DKIM-signed bytes)
     and releases the DB session before any network work

2. Delivery thread — phase 2, SMTP exchange (no app context, no DB session)
   → If use_tls: SMTP_SSL connection
   → If use_starttls: SMTP connection → STARTTLS upgrade
     (falls back to plaintext delivery, with a logged warning,
     if the upstream does not advertise STARTTLS)
   → EHLO with configured helo_hostname
   → AUTH LOGIN (if relay auth credentials configured)
   → SENDMAIL with original envelope

3. Delivery thread — phase 3, a fresh app context records the result
   → Reloads the queue entry by id (skips gracefully if it was deleted
     or requeued via the web UI during the SMTP exchange)
   → On success:
      Queue entry status → sent
      Log entry status → sent
   → On failure:
      Increment retry count
      If retries < max_retries: reschedule (status → queued)
      If retries >= max_retries: mark as failed (status → failed)
      Failed entries are retained in the queue with their raw message
      data intact — they are never automatically discarded
      Administrators can manually retry or delete from the Queue page
```

### Direct Delivery Mode

When `delivery_mode` is set to `direct` in the configuration, the relay performs MX record lookups to deliver mail directly to the destination mail servers, bypassing the upstream relay entirely.

```
1. Delivery thread — phase 1, read + snapshot (one short app context;
   the DB session is removed at the end of the phase — see
   "Delivery Threads" above)
   → Reads queue entry and relay config, marks status as 'processing'
   → Resolves MX candidates for the destination domain
     (dnspython; ordered list, highest preference first per RFC 5321
     §5.1 — NULL MX and nonexistent domains are permanent failures;
     transient DNS errors fall back to the domain itself on port 25)
   → Snapshots plain values (direct credentials decrypted) and
     releases the DB session before any network work

2. Delivery thread — phase 2, SMTP exchange (no app context, no DB session)
   → SMTP connection to the resolved MX hosts, in preference order:
     a transient failure (connect refused/timeout, 4xx) advances to
     the next MX host; an authoritative 5xx stops the list
   → STARTTLS upgrade when direct_use_starttls is enabled (silently
     falls back to plaintext, with a logged warning, if the MX server
     does not support STARTTLS)
   → EHLO with configured helo_hostname
   → AUTH LOGIN (if direct auth credentials configured)
   → SENDMAIL with original envelope (BODY=8BITMIME declared when the
     message is 8-bit and the target advertises the feature)

3. Delivery thread — phase 3, a fresh app context records the result
   → On success:
      Queue entry status → sent
      Log entry status → sent
   → On failure:
      Increment retry count
      If retries < max_retries: reschedule (status → queued)
      If retries >= max_retries: mark as failed (status → failed)
```

---

## Security Design

### Authentication
- Web interface uses Flask-Login. Accounts sign in either with **Microsoft Entra ID** (identity-only OIDC via `auth_oauth.py`, `msal`; the local users table is the allow-list — matched by Entra object ID then email; no Microsoft token is stored, the session holds only the local user id) or with a **local break-glass password** (bcrypt-hashed; Admin accounts only; the initial `admin` account is always local). The `users.auth_source` column selects the method per account
- Azure client secrets are stored ENC1-encrypted (same scheme as relay passwords); `azure.redirect_uri` pins the OAuth callback for proxy deployments
- SMTP uses separate credentials (also bcrypt-hashed) from the `smtp_credentials` table
- Sessions are non-permanent and expire on browser close
- All sessions are invalidated on server restart

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

**Storage backend (v4.3.0+):** PostgreSQL is the only backend, selected by the required
`DATABASE_URL`. The `relay_config` table is the only configuration store
(web settings included, secret key persisted as `web_secret_key`).

The application performs automatic schema migrations on startup to ensure compatibility with older databases. All migrations are idempotent — they check for column existence before attempting to add it, so they are safe to run on every startup.

| Function | Migration | Added In | Description |
|---|---|---|---|
| `_migrate_roles()` | `users.role` | v1.0.0 | Adds `role` column to `users` table; sets existing admins to `super_admin` |
| `_migrate_schema()` | `email_logs.raw_headers` | v2.0.0 | Adds `raw_headers` TEXT column for storing full email headers |
| `_migrate_schema()` | `email_logs.message_id` | v2.2.0 | Adds `message_id` VARCHAR(255) column; fixes Message-ID always showing blank on upgraded databases |
| `_migrate_email_recipients()` | `email_recipients` | v4.6.0 | Backfills one per-recipient row per parsed address for every log row that has none (anti-join, idempotent, heals on every boot) |

### Queue Retention Policy

The automatic cleanup process (`cleanup_old()`) only purges **successfully sent** queue entries after the configured `log_retention_days` period. Failed queue entries are **never automatically deleted** — they remain in the system with their full `raw_message` data until an administrator takes explicit action (retry or delete) via the Queue page. This ensures no undelivered email is ever silently discarded. Per-recipient rows (`email_recipients`, v4.6.0) are purged together with the log rows they belong to.

---

## Technology Stack

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.10 — 3.14 |
| Web Framework | Flask | 3.1.x |
| WSGI Server | gunicorn (Linux/Docker; workers=1, threaded) | 26.2.0 |
| SMTP Server | aiosmtpd | 1.4.x |
| Database | PostgreSQL via SQLAlchemy + psycopg2 | 2.0.37+ |
| Authentication | Flask-Login + bcrypt | 0.6.x / 4.2.x |
| DNS Resolution | dnspython | 2.x |
| Frontend | HTML5 + CSS3 + Vanilla JS | — |
| Charts | Chart.js | 4.4.x |
| Icons | Font Awesome | 6.5.x |
| Font | Inter (self-hosted woff2) | — |

---

## Known Limitations

Documented trade-offs in the current design (v5.0.1) — not bugs, but good to know before operating the relay:

- **ENC1 envelopes use an unsalted SHA-256 key derivation.** Fernet itself is used correctly (random IV, encrypt-then-MAC), but a weak human-chosen `SECRET_KEY`/`web_secret_key` could be brute-forced offline from a database dump, decrypting every stored secret at once. Use a long random key (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`). A salted/stretching `ENC2:` envelope format is the planned fix.
- **The SMTP handler hooks run synchronously on the aiosmtpd event-loop thread.** A very large message parse or a slow database briefly delays responses on other concurrent SMTP connections (RFC 5321 command timeouts are minutes — this is a latency issue under load, not a correctness one).
- **SMTPUTF8 is not enabled.** MAIL FROM with the `SMTPUTF8` parameter is rejected at ingest (`501`); 8-bit MIME content is fully supported (and declared to capable upstreams), but non-ASCII *envelope* addresses are not.
- **The login throttle is in-process.** Fine under the enforced `workers=1`; a shared store would be required if workers were ever raised.
- **JIT binding by email is by design:** the first Microsoft sign-in with an email matching an `entra` user's address binds that Entra object ID to the account. If a Microsoft mailbox is reassigned to a different person, the new owner could inherit the local account — remove/re-add users in User Management when mailboxes change hands.

---

## Design Decisions

### Why PostgreSQL-Only?
A mail relay is stateful infrastructure: the queue, logs, credentials, and configuration all need a real database server with concurrent readers and writers, reliable durability, and operational tooling (backups via `pg_dump`, monitoring, managed services). Supporting a second embedded backend doubled every storage code path for little gain, so v4.3.0 removed it — PostgreSQL is the single, supported storage engine. Safe libpq defaults (`gssencmode=disable`, `connect_timeout=10`) are applied automatically, and pooled connections with pre-ping keep long-lived deployments healthy.

### Why aiosmtpd?
aiosmtpd is the modern replacement for the deprecated `smtpd` module in Python's standard library. It provides an async SMTP server with built-in support for AUTH, TLS, and size limits. The `Controller` class runs the async event loop in a separate thread, making it compatible with Flask's synchronous model.

### Why No Separate Worker Process?
The relay uses threads instead of separate processes to keep deployment simple. The SMTP server, queue processor, and web interface all run in a single Python process. This avoids the complexity of inter-process communication and makes the application easy to run as a service.

### Why Database-Only Configuration?
The database is the one place the relay already reads on every SMTP command and web request, so making it the only configuration store gives a single, unambiguous source of truth — no file mirror to keep in sync, no two modes to reason about. The secret key lives in the same database, so ENC1-encrypted secrets survive any container replacement as long as the data volume is kept.

### Why Bootstrap-Only Environment Variables?
Environment variables (e.g., `RELAY_HOST`, `WEB_PORT`) seed the `relay_config` rows on **first boot** — while the database is still empty. From then on the database owns the configuration and environment variables are ignored, so there is a single, unambiguous source of truth for what the relay is actually running with (web UI edits are never silently reverted by an env var on the next restart). A startup warning lists any env vars that differ from the stored configuration; empty the database (or the Docker volume) to re-apply environment variables from scratch.

### Why Store Raw Headers?
Email headers contain critical diagnostic information — routing paths, authentication results, content types, mailer identification, and timestamps. Storing them at relay time provides a complete audit trail for troubleshooting delivery issues, verifying sender authenticity, and diagnosing formatting problems without needing access to the original sending application.

### Why Per-Message Delivery Threads?
Earlier versions called `_deliver()` synchronously from the queue-processor loop, so a single 30-second SMTP connect timeout blocked the entire queue and held a database connection open for the duration. Today every message is delivered in its own daemon thread, which keeps the queue loop, SMTP listener, and web interface responsive. The delivery itself is split into three phases — snapshot in a short-lived app context, SMTP exchange with no session at all, result write in a fresh context (see "Delivery Threads") — so a slow upstream never pins a database connection during network I/O.

### Why Queue Delete Confirmations (v3.0.1)?
Accidental deletions of queued messages can result in lost emails. The confirmation prompts ensure administrators explicitly acknowledge the destructive nature of delete actions before they take effect.

### Why Version Badge (v3.0.0)?
The version badge in the sidebar footer provides quick visibility into which release is running, making it easier for administrators to verify they have the expected version deployed and reference the correct documentation.
