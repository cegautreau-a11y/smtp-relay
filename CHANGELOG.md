# Changelog

All notable changes to the SMTP Mail Relay project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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