# Changelog

All notable changes to the SMTP Mail Relay project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [2.2.0] — 2025-06-04

### Fixed
- **Page hang / browser spinner on refresh** — Resolved the root causes of the web interface hanging or spinning indefinitely when refreshing a page while background email delivery was in progress. Four separate issues were addressed together:

  1. **SQLite WAL mode enabled** — SQLite now runs in Write-Ahead Logging (WAL) journal mode. WAL allows concurrent reads while a write is in progress, eliminating the database lock contention that caused web requests to block behind background delivery threads. A `busy_timeout` of 8 seconds and `synchronous=NORMAL` are also set on every connection.

  2. **Queue processor decoupled from delivery** — The `QueueProcessor._tick()` method previously called `_deliver()` directly and synchronously, meaning a 30-second SMTP connect timeout would hold a SQLite connection open for the full duration and block the queue thread. Delivery is now always dispatched in a daemon thread, matching the behaviour of the immediate post-DATA delivery path. The DB connection is released as soon as the pending IDs are fetched.

  3. **SQLAlchemy connection pool hardened** — Added `pool_timeout=10`, `pool_recycle=60`, and `pool_pre_ping=True` to the SQLAlchemy engine options. Web requests now fail fast with a 500 error rather than waiting indefinitely for a pool slot, and stale connections are recycled automatically.

  4. **`/api/stats` made fault-tolerant** — The stats API endpoint (polled every 15 seconds by the base layout) is now wrapped in a try/except. If a DB query fails or times out it returns a graceful JSON response with `error: true` and zeroed counters rather than hanging or returning a 500, which previously caused the browser to queue up multiple stalled requests.

### Changed
- **Dashboard stat cards auto-refresh** — The incomplete `// Auto-refresh stats every 30 seconds` comment on the dashboard has been replaced with a working implementation. The four live stat cards (Sent Today, Failed Today, This Hour, Queued) now update every 30 seconds via the `/api/stats` endpoint without requiring a full page reload.
- **Version bump** — All source files updated to v2.2.0.

### Technical Details
- `models.py` — Added `@event.listens_for(Engine, "connect")` listener that sets `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=8000`, and `PRAGMA synchronous=NORMAL` on every new SQLite connection.
- `smtp_server.py` — `QueueProcessor._tick()` now collects pending queue IDs within a scoped `app_context`, closes the context, then spawns a `threading.Thread` per ID rather than calling `_deliver()` inline.
- `app.py` — Added `SQLALCHEMY_ENGINE_OPTIONS` with `connect_args`, `pool_timeout`, `pool_recycle`, and `pool_pre_ping`. Wrapped `/api/stats` DB queries in try/except with a safe fallback response.
- `templates/dashboard.html` — Added `id` attributes to the four live stat card `<div class="stat-value">` elements; implemented the 30-second `setInterval` fetch loop to update them.

---

## [2.1.0] — 2025-06-03

### Added
- **Retry All Failed** — New button on the Queue page to requeue all failed messages for delivery in a single action. Resets retry counts and immediately schedules them for the next delivery attempt.
- **Individual retry confirmations** — Delete buttons on individual failed entries now prompt for confirmation before permanently removing the message.

### Changed
- **"Flush Failed" renamed to "Delete All Failed"** — Clearer labelling to distinguish the destructive delete action from the new retry action. Confirmation prompt now warns that deletion is permanent and cannot be undone.
- **Failed queue display** — Removed the 50-entry limit on the Failed Deliveries table; all failed messages are now shown so none are hidden from view.
- **Improved flash messages** — Retry and delete operations now show accurate counts with proper pluralisation.
- **Version bump** — All source files updated to v2.1.0

### Technical Details
- `app.py` — New `/queue/retry-all` route requeues all failed `EmailQueue` entries, resets `retry_count` to 0, sets `next_retry_at` to now, and updates associated `EmailLog` entries to `queued` status
- `app.py` — Removed `.limit(50)` from failed queue query in the `queue()` route
- `templates/queue.html` — Added "Retry All Failed" button with confirmation, renamed "Flush Failed" to "Delete All Failed" with stronger warning, added delete confirmation on individual entries

---

## [2.0.1] — 2025-06-03

### Fixed
- **Failed emails are now retained indefinitely** — Queue entries that fail after all retry attempts are no longer automatically purged by the log retention cleanup. They remain in the Failed Deliveries queue until an administrator manually retries or deletes them via the web interface.

### Changed
- **Queue cleanup logic** — The `cleanup_old()` function in the queue processor now only purges successfully `sent` queue entries after the configured retention period. Previously, both `sent` and `failed` entries were purged, which silently discarded undelivered messages.
- **Version bump** — All source files updated to v2.0.1

### Technical Details
- `smtp_server.py` — Changed `EmailQueue.status.in_(['sent', 'failed'])` to `EmailQueue.status == 'sent'` in `QueueProcessor.cleanup_old()`
- Failed queue entries retain their `raw_message` data so they can be retried at any time from the Queue page

---

## [2.0.0] — 2025-06-03

### Added
- **Email Header Details** — Clicking the Details button on the Email Logs page now opens a full modal dialog displaying complete message metadata and raw email headers
- **Raw Headers Storage** — All email headers (From, To, Subject, Date, Message-ID, MIME-Version, Content-Type, X-Mailer, etc.) are now captured and stored when the relay processes a message
- **Log Detail API** — New `/api/logs/<id>/detail` endpoint returns full email log metadata including raw headers as JSON
- **Detail Modal** — Rich modal UI on the Email Logs page with three sections: Message Info, Relay Info, and Email Headers displayed in a dark-themed scrollable code block
- **Keyboard & Click-to-Close** — Detail modal supports Escape key and overlay click to dismiss

### Changed
- **Email Logs Details Button** — Replaced the basic browser `alert()` popup with a proper modal dialog fetching full details via the API
- **Database Schema** — Added `raw_headers` (TEXT, nullable) column to the `email_logs` table
- **Automatic Migration** — Existing databases are automatically migrated on startup to add the `raw_headers` column; no manual steps required
- **Version Bump** — All source files updated to v2.0.0

### Technical Details
- `models.py` — Added `raw_headers` column to `EmailLog` model
- `smtp_server.py` — Extracts all email headers in `handle_DATA` and stores them in the log entry
- `app.py` — Added `_migrate_raw_headers()` auto-migration and `/api/logs/<id>/detail` API endpoint
- `templates/logs.html` — New detail modal with JavaScript fetch, HTML escaping, and structured layout
- `static/css/style.css` — Added styles for `.log-detail-grid`, `.detail-table`, `.raw-headers-block`

---

## [1.0.0] — 2025-03-03

### Added
- **SMTP Relay Server** — Async inbound SMTP listener powered by aiosmtpd, accepts mail from applications and forwards to an upstream relay
- **Web Management Interface** — Full Flask-based dashboard for monitoring and configuration
- **Role-Based Access Control** — Four-tier user role system (Super Admin, Admin, Operator, Viewer) with granular permissions
- **SMTP Authentication** — Per-credential auth with bcrypt password hashing and individual rate limits
- **Sender Domain Allowlist** — Restrict which sender domains are permitted through the relay
- **Email Queue** — Automatic retry with configurable intervals and max attempts for failed deliveries
- **Email Logging** — Full audit trail of all messages processed with status tracking
- **Dashboard** — Real-time statistics, 7-day volume chart, recent email activity
- **Live Configuration** — Edit relay settings through the web UI; changes saved to both database and config.json
- **TLS/STARTTLS Support** — Optional TLS on the listener and STARTTLS for upstream relay connections
- **IP Allowlisting** — Restrict which source IPs can connect to the relay
- **Global Rate Limiting** — Server-wide hourly send limit
- **Auto-Dependency Install** — First-run automatic pip install of all requirements
- **Windows Compatibility** — Designed for Windows Server; no Unix signals or fork required
- **Python 3.14 Support** — Compatible with Python 3.11 through 3.14 (handles stricter SSL in 3.14)
- **Session Security** — Sessions expire on server restart and browser close
- **Database Migration** — Automatic schema migration when upgrading from older versions