# Changelog

All notable changes to the SMTP Mail Relay project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 5.0.1 — Test Suite Unshipped (2026-11-11)

### Changed
- **The end-to-end test suite is no longer part of the published repository.** `tests/` is removed from
  the tree and `.gitignore`d: the suite lives on in the maintainer's local environment, where it continues
  to gate every release. The container image is unaffected — `.dockerignore` already excluded `tests`.
  (tests/, .gitignore, README.md)

### Upgrade (v5.0.0 → v5.0.1)
Pull and rebuild/restart. No schema changes and no application-code changes. Suite: 189 checks green.

## 5.0.0 — Documentation & Deliverability (2026-11-11)

### Changed — Documentation
- **README: new DNS & Deliverability section.** The four publish-before-you-send requirements in one place: a fully resolvable A-record server hostname (matching `banner_hostname` / `relay_helo_hostname`, with reverse-DNS recommended), an SPF record authorizing the sending IP, DKIM configured in the interface (DKIM Keys page: generate with domain + selector → activate → publish the displayed `<selector>._domainkey.<domain>` TXT record), and the required DMARC record at the **root domain** of the sender — regardless of whether the server hostname is the root or a subdomain. (README.md)
- **README aligned with the Linux/Docker-only platform.** The Windows bare-metal quick start, the NSSM/Task Scheduler service section, and the Windows requirements line are removed; a Linux-without-Docker path (`run.py` serves gunicorn when installed) replaces them. The feature list now covers per-recipient delivery tracking, sender failure reports, the live SMTP status chip, and `log_retention_days`. (README.md)

### Fixed
- **Real LAN subnet removed from shipped examples.** The IP-allowlist docstring examples and the Configuration-page allowed-source-IPs hint shipped with a production-range address (`10.55.61.x`); both now use generic documentation ranges (`192.168.1.x`, `10.0.0.x`). (smtp_server.py, templates/config.html)

### Upgrade (v4.9.2 → v5.0.0)
Pull and rebuild/restart. No schema changes; documentation-only release plus the example-string fix — SMTP, web, and queue behavior is unchanged from v4.9.2. Suite: 189 checks green.

## 4.9.2 — Quiet Retention Cleanup (2026-11-07)

### Fixed
- **The retention cleanup logged a SQLAlchemy warning on every 30 s tick.** The stale-log purge passed a `Subquery` into `IN()`, which SQLAlchemy auto-coerces with a `SAWarning` each tick — harmless but constant log noise. The query now uses explicit `select()` constructs (same SQL, no warning). (smtp_server.py)

### Added
- **First test coverage for the retention cleanup** (`cleanup_old`): stale `sent` queue rows purge, `failed` rows and the logs they reference are retained forever, stale unreferenced logs purge together with their per-recipient rows. (tests/e2e_smoke.py)

### Upgrade (v4.9.1 → v4.9.2)
Pull and rebuild/restart. No schema changes. Suite: SUITE_COUNT checks green.

## 4.9.1 — Listener Starts Post-Fork (2026-11-07)

### Fixed
- **The SMTP listener could never be restarted from the dashboard under gunicorn.** `run.py` started the listener in the main process *before* gunicorn forked the web worker — and `fork()` duplicates every open FD. The worker therefore held an inherited copy of the listening socket: closing the master's copy (a queued Stop/Restart) could never free the port, so every re-bind failed with `EADDRINUSE` while nothing accepted connections (the listening socket existed with no loop serving it) — SMTP down until the container restarted. The listener and queue processor now start **inside the worker** via gunicorn's `post_fork` hook: with workers=1 that one process hosts web + listener + queue, no socket duplication exists, dashboard Stop/Start/Restart apply instantly in the same process, and a killed worker respawns with a fresh listener automatically. (run.py)
- **`TZ` is now passed through the compose files** to the container (every-boot env; default UTC) — without it the `.env` value never reached the app and the daily statistics stayed on UTC. (docker-compose.yml, docker-compose.portainer.yml)

### Upgrade (v4.9.0 → v4.9.1)
Pull and rebuild/restart. No schema changes. Suite: 187 checks green × 3 consecutive (1 unrelated timing flake observed once under back-to-back runs, not reproducible in 3 logged reruns).

## 4.9.0 — Cross-Process Server Control (2026-11-07)

### Added
- **Stop / Start / Restart work correctly under gunicorn.** The dashboard buttons executed in the web worker process, which operates on a fork-inherited dead copy of the listener rather than the process that actually owns it — Stop could silently miss the real listener and Restart re-bound in the wrong process. Commands now travel through the database (the same message bus as everything else): a worker that doesn't own the listener queues a `smtp_command` row (`start` / `stop` / `restart`), and the owner process applies it on its next 30 s tick — consumed before applying, so a failing command can never loop. Deployments where the web process owns the listener keep instant execution. The UI flashes that the command is queued; the truthful status chip reflects reality as it changes. (app.py, smtp_server.py, run.py)

### Changed
- **The `0.0.0.0 → 127.0.0.1` bind fallback is gone entirely** (it was already Windows-gated since v4.8.2; the product does not run on Windows in any form). Bind failures on the listener now always log and raise. (smtp_server.py)

### Upgrade (v4.8.3 → v4.9.0)
Pull and rebuild/restart. No schema changes. The `smtp_command` relay_config row is runtime-only (created on first queued command). Suite: 186 checks green (7 new: command consume/apply, same-process instant paths, cross-process queueing).

## 4.8.3 — Truthful SMTP Status (2026-11-07)

### Fixed
- **The dashboard said "SMTP Running" for a dead listener.** The queue tick refreshed the `_smtp_heartbeat` liveness row unconditionally — even after the SMTP listener died (e.g. a failed restart) — and forked gunicorn web workers trust that heartbeat for `is_running`, so the status chip, `/api/stats`, and every page render reported Running indefinitely. The heartbeat is now written only while the listener thread is genuinely alive; a dead listener goes stale within ~90 s and the UI reports Stopped. (smtp_server.py, run.py)
- **The Start/Stop buttons and the status text could disagree.** The header buttons rendered once at page load while the status chip re-drives every 15 s from `/api/stats`; one stale render left button=Start beside status=SMTP Running. The poller now flips the buttons together with the status text. (templates/dashboard.html, templates/base.html)

### Upgrade (v4.8.2 → v4.8.3)
Pull and rebuild/restart. No schema or configuration changes. Suite: 179 checks green (7 new: heartbeat gating, worker-probe staleness, rendered status/buttons + poller flip).

## 4.8.2 — Failure-Report Subject, Credential Editing, Deterministic Restart (2026-11-07)

### Fixed
- **Failure-report subjects no longer arrive mangled.** The report's subject embedded an em-dash, forcing RFC 2047 encoding that `Header.encode()` folds mid-word across multiple encoded-words (`…your_mess?= / =?…age?=`) — exactly the mangling seen in delivered reports. The report subject is now pure ASCII (`Delivery report - …`), so it always rides the wire verbatim on one line; the (defensive) encoding branch stays for non-ASCII input. (smtp_server.py)
- **Restart SMTP Server no longer fails with "address already in use".** The aiosmtpd controller releases its listening socket asynchronously, and `restart()`'s fixed 1-second sleep raced that release — the rebind hit `EADDRINUSE`, and the Windows-heritage fallback then silently bound the listener to `127.0.0.1`, which no Docker port mapping forwards (the server looked "running" while SMTP was dead). `restart()` now polls until the old port is actually free (bounded 15 s), and the `0.0.0.0 → 127.0.0.1` fallback applies **on Windows only** — on Linux/Docker a bind failure raises and the UI flashes the error instead of quietly killing inbound mail. (smtp_server.py)

### Added
- **SMTP credentials can be edited after creation.** Operators can change a credential's `Max Sends per Hour` and description from the Credentials page (per-row Edit modal, `POST /credentials/<id>/edit`, Operator+ required). Set-at-creation for the rate already existed; editing was missing entirely. (app.py, templates/credentials.html)

### Upgrade (v4.8.1 → v4.8.2)
Pull and rebuild/restart. No schema changes, no configuration changes. Suite: 172 checks green (14 new: restart rebind + no-fallback refusal + credential editing + verbatim-ASCII report subject).

## 4.8.1 — Local-Midnight Daily Statistics (2026-11-07)

### Fixed
- **Sent Today / Failed Today reset at the server's local midnight, not UTC's.** The dashboard cards, `/api/stats`, and the 7-day chart computed "today" from UTC, so outside UTC the counters rolled over at the wrong wall-clock time (7:00 PM EST on a UTC container). Day boundaries now follow the `TZ` environment variable (IANA name, read on every boot, default UTC) with DST-correct conversions, and the 7-day chart buckets outcomes by local calendar date — previously PostgreSQL's `date()` grouped by UTC date, splitting evening deliveries onto the next UTC day. Set e.g. `TZ=America/Toronto` in the stack environment to reset at local midnight. (app.py, .env.example, docs/CONFIGURATION.md)

### Changed
- **Dependency added:** `tzdata==2026.6` so named `TZ` zones resolve inside the slim Docker image, on Windows, and anywhere system zone data is absent. (requirements.txt)

### Upgrade (v4.8.0 → v4.8.1)
Pull and rebuild/restart. No schema changes. To make the daily counters reset at YOUR midnight, set `TZ` to your IANA timezone in the stack environment (e.g. `TZ=America/Toronto`); unset, behavior stays UTC as before. Suite: 158 checks green (8 new checks covering the timezone boundary).

## 4.8.0 — Hardening Release: Full Debug-Audit Fixes (2026-11-06)

### Added
- **Password changes and admin resets now cut off other sessions.** A per-user `session_epoch` column (added automatically) is bumped on every password change/reset; the session loader rejects cookies carrying a stale epoch. A hijacked session no longer survives the victim changing their password — or an admin resetting it. (models.py, app.py, auth_oauth.py)
- **`LOG_LEVEL` is now real.** It was documented as an every-boot env var but never read; the launcher now applies it on every boot, alongside newly wired `DEBUG_LOG_FILE` / `DEBUG_LOG_MAX_SIZE` / `DEBUG_LOG_BACKUP_COUNT` (previously documented but hard-coded). (run.py, docs/CONFIGURATION.md)
- **Secrets beginning with a literal `ENC1:` are no longer bricked.** A password whose own text starts with the envelope prefix used to be mistaken for an encrypted envelope forever — undecryptable, silently dropping upstream AUTH. Such values are now stored escaped under an `ENC1LIT:` marker and decrypt back verbatim. (crypto_utils.py, app.py)

### Fixed
- **The `send_failure_reports` toggle never saved.** The Configuration page checkbox was rendered and read from the browser, but the save loop never persisted it — unticking it flashed "Configuration saved" while failure reports kept flowing. The toggle now works. (app.py)
- **A first-boot crash no longer silently discards operator environment configuration.** Bootstrap readiness keyed on the `users` table, which `create_all` creates before seeding finishes; a crash mid-first-boot made the next boot skip env seeding and backfill factory defaults (relay → `localhost:25`) while warning that the operator's variables were "ignored". The `bootstrap_complete` marker now gates readiness, so a partial first boot re-runs the (default-aware, idempotent) seed path. (run.py)
- **`LOG_RETENTION_DAYS` no longer re-applies itself on every boot.** The env value rode into every boot and re-wrote the database row while it still held the default, and the ignored-env warning falsely claimed the variable was ignored. The stored row now wins like every other setting. (run.py)
- **Failure reports are date-correct, MIME-clean, and can no longer wedge the queue.** The report's `Date:` header was skewed by the host timezone (naive-UTC timestamp bug) and its `Subject:` carried raw UTF-8 headers that strict MTAs mangle; worse, one report-insert failure rolled back the mandatory recipient bookkeeping and stranded the row `processing` until restart. Bookkeeping now commits first and the report runs decoupled — a report failure is a logged warning, nothing more. (smtp_server.py)
- **The failure-path final transition can no longer clobber a manual requeue**, and a message whose every recipient delivered now finalizes the queue row as `sent` (it used to be marked `failed`, so a manual retry re-sent to everyone). Both retry routes refuse rows with nothing left to deliver. (smtp_server.py, app.py)
- **Mixed-domain direct deliveries attribute errors correctly** — each pending recipient carries its own domain's failure text instead of the last domain's — and recipients with legal >255-character addresses now pair against their delivery outcomes instead of being mislabeled. (smtp_server.py)
- **Retry-all no longer wipes failure reasons of rows it skipped**, `/queue/<id>/delete` refuses in-flight (`processing`) rows the UI already claimed cannot be cancelled, and the backfill migration commits in chunks instead of one unbounded transaction. (app.py)
- **Login leaks nothing about registered accounts.** Password sign-in against an existing Microsoft-365-only account returned a distinct "signs in with Microsoft" hint — a username-enumeration oracle. Every failure now returns the same generic message (the Microsoft button is on the page for anyone who needs it), and the Microsoft callback clears the session before sign-in, matching the local-login fixation defense. (app.py, auth_oauth.py, docs/ARCHITECTURE.md)
- **The Azure client-secret "Stored" hint can now actually appear**, and the dashboard's structurally always-zero "Exception" metric and chart series are gone (exceptions live at message level — filter `/logs`). (app.py, templates/)

### Changed
- **Dependency bumps:** Flask 3.1.1 → 3.1.3 and Werkzeug 3.1.3 → 3.1.9, clearing open advisories against the previous pins (Vary: Cookie caching; `safe_join` Windows device names). (requirements.txt)
- **Documented the rate limiter's deliberate shape:** enforced at `MAIL FROM`, metered at `DATA`; concurrent in-flight messages at the window boundary may slightly over-admit. (docs/CONFIGURATION.md)

### Upgrade (v4.7.0 → v4.8.0)
Pull and rebuild/restart. The upgrade adds one column (`users.session_epoch` — automatic, non-destructive). No configuration changes, **but**: `LOG_LEVEL` and `DEBUG_LOG_*` now genuinely take effect from the environment on every boot — review any values sitting in your `.env` that previously did nothing; and if an upstream/direct password or Azure client secret begins with the literal text `ENC1:`, re-enter it once in the web UI (the old behavior permanently misread it). Suite: 150 checks green (51 new checks covering every fix in this release).

## 4.7.0 — Failure Reports to Senders & Live Dashboard Counts (2026-11-06)

### Added
- **Plain-English failure reports emailed to the sender.** When a message finalizes with undeliverable recipients — all configured retry attempts exhausted, or a permanent rejection that can never succeed — the relay emails the original sender a report from `postmaster@<banner hostname>`: delivered/failed counts plus one plain-English line per failed address ("the destination mail server refused this address (550 …)", "the domain … has no mail server — the domain may be misspelled or no longer exists", "the retry attempts did not succeed"). Reports travel through the normal queue (DKIM-signed, per-recipient recorded), carry `Auto-Submitted: auto-generated` so they can never trigger further reports, list at most 50 addresses with an "and N more" tail, and respect a new **send_failure_reports** toggle (Configuration page, default on). (smtp_server.py, models.py, templates/config.html, docs/CONFIGURATION.md)

### Fixed
- **Dashboard cards lagged behind reality.** Per-recipient stats (Sent Today, Failed Today, This Hour, Queued, 7-day chart) were anchored on the message's *ingest* date — a message received Nov 4 and delivered Nov 6 counted under Nov 4, so a day of live deliveries could show "0 sent". All counts now anchor on each recipient's **outcome time** (`email_recipients.updated_at`): deliveries and failures count on the day they happened, and the card poller refreshes every 15 s instead of 30 s. (app.py, templates/dashboard.html)
- **The HELO/EHLO "Sending Server Name" field was invisible in Direct mode.** The field — used in BOTH delivery modes, and the cause of "Invalid HELO name" rejections from strict receivers — lived inside the relay-only settings card that the page hides for direct delivery. It now sits in its own always-visible "Sending Identity" card. (templates/config.html)
- **Manual retries could strand failed recipients.** Retry bookkeeping narrowed the queue row to the last attempt's pending set, so a manual Retry re-attempted only that subset — earlier failed recipients were reset to pending but excluded from every future attempt. Both Retry routes now rebuild the queue's recipient list from all undelivered rows before requeueing. (app.py)
- **Direct-mode transient failures retried forever.** Per-domain outcome bookkeeping decided finality solely by "are any recipients still pending", ignoring `queue_max_retries` — a domain stuck on transient failures (e.g. TLS certificate mismatches) rescheduled hourly without end. Outcome-pending retries are now capped by the configured retry budget like every other failure; budget-exhausted recipients finalize as failed carrying their own transient reason. (smtp_server.py)

### Upgrade (v4.6.1 → v4.7.0)
Pull and rebuild/restart. No configuration changes; the new `send_failure_reports` key defaults to on (first-boot seed fills it on existing databases like every default-aware key).

## 4.6.1 — Direct-Mode Per-Domain Isolation (2026-11-06)

### Fixed
- **One dead recipient domain no longer fails the whole message in direct mode.** Direct delivery resolved every target domain BEFORE anything was sent, so a single domain with no mail exchanger (NULL MX / nonexistent domain — a typo'd address in a big blast) permanently failed the ENTIRE message: a 551-recipient mailing across dozens of domains died because of one dead domain, and the retry burned slots on an attempt that could never succeed. Delivery is now isolated per recipient domain: healthy domains deliver as normal while a dead domain's recipients fail immediately with the MX reason; a domain whose upstream rejects authoritatively (hard 5xx) fails only its own recipients with the upstream verdict; and a transient failure retries only the affected domain's recipients — already-delivered domains are narrowed out of the retry and can never be re-sent (previously a later-domain failure re-delivered to earlier domains on retry). Message status still derives per recipient: mixed outcomes show EXCEPTION with the delivered/failed counts. (smtp_server.py, tests/e2e_smoke.py — 89 checks)

### Upgrade (v4.6.0 → v4.6.1)
Pull and rebuild/restart. No configuration changes; no data migration.

## 4.6.0 — Per-Recipient Delivery Records & EXCEPTION Status (2026-11-06)

### Added
- **Per-recipient delivery tracking.** A new `email_recipients` table stores one row per envelope recipient (`pending` → `sent`/`failed`), with the recipient's own status message (e.g. the upstream `RCPT refused: 550 …` line) and a bounded connection+send transcript (`attempt_log`) of the last delivery attempt. Outcomes are written per recipient by the delivery phase-3 writers; a partial refusal marks the accepted addresses `sent` immediately, and only the refused ones retry.
- **EXCEPTION message status.** A message delivered to some recipients but refused by others now shows `exception` — not `failed` — with a status message like `Partial delivery: 3 of 5 recipients delivered; 2 failed — RCPT refused: 550 …`. All-delivered stays `sent`; all-failed stays `failed`. The Logs page filter and status badges cover the new state.
- **Per-recipient Email Detail.** The Email Detail modal gains a "Recipient Delivery" section listing every address with its status badge, its own status message, and an expandable "Connection & send log" transcript per address.
- **Failed Deliveries lists each failed recipient.** The Queue page's Failed Deliveries section shows a sub-row per failed recipient — the TO column is the specific address that failed and the Error column carries that address's own reason.
- **Recipient popup on Dashboard and Logs.** The `+N more` indicator in the To column is now clickable and opens a popup listing every address the message was sent to.

### Changed
- **Dashboard and stat-card counts are per recipient, not per message.** Sent Today, Failed Today, This Hour, Queued, and the 7-day volume chart (now with an Exception series) count individual recipient emails — a message with 5 recipients counts as 5, and a partially delivered message counts on both sides of the ledger. The 15s live poller serves the same recipient-based numbers.
- **Error columns wrap instead of truncating** on the Queue page (Pending Delivery "Last Error" and Failed Deliveries "Error"), with the full text in the cell tooltip.

### Fixed
- **Subjects stored as RFC 2047 encoded-words displayed as `=?us-ascii?Q?…?=` soup.** Subjects are now decoded at ingest for storage/display (raw headers keep the original encoding verbatim), so long or non-ASCII subjects read as their real text everywhere in the UI. The relay never altered message bytes in transit — this was purely a display/storage defect.
- **The e2e config-form test flipped every boolean setting to true** by submitting `'false'` strings as text fields (bool keys are checkbox-coerced by form-value truthiness) — latent since v4.3.0 and only exposed by the new post-config delivery checks; the form now omits checkbox keys exactly like the real UI.

### Upgrade (v4.5.2 → v4.6.0)
Pull and rebuild/restart. **No configuration changes.** On the first boot after the upgrade, an automatic idempotent migration (`_migrate_email_recipients`) backfills per-recipient rows for the existing history: every message logged before v4.6.0 gets one row per parsed address, inheriting the message-level verdict (which specific address failed is unknowable for pre-upgrade history). Mail delivered after the upgrade gets true per-recipient outcomes from the actual SMTP responses.

## 4.5.2 — Sends/Hour Column Shows the Live Window (2026-10-17)

### Fixed
- **The SMTP Credentials page's SENDS/HOUR column no longer shows stale counts.** The per-credential hourly limiter is a fixed-window counter that resets lazily — only when that credential's next message arrives over SMTP — so once the hour elapsed with no new mail, the UI kept displaying the last window's count (e.g. `7/100`) indefinitely, looking like old sends were never subtracted. The page now displays what the limiter would actually enforce at this moment: after `hour_reset_at` has passed, the column reads `0/max` until the credential sends again. Enforcement is unchanged — MAIL FROM still returns `450 4.7.1` when the window's count is exhausted, and accepted messages still increment the counter. Regression check seeds an elapsed window and asserts the page renders 0 instead of the stale count (app.py, templates/credentials.html, tests/e2e_smoke.py — 58 checks).

### Upgrade (v4.5.1 → v4.5.2)
Pull and rebuild/restart. No configuration changes.

## 4.5.0 — Queue Manager Role (2026-10-15)

### Added
- **New "Queue Manager" role** between Viewer and Operator. Queue Managers get full control of the retry queue — retry individual entries, Retry All Failed, delete entries, Delete All Failed — and nothing else: no domains, no SMTP credentials, no DKIM keys, no configuration, no user management. Built for helpdesk/operations staff whose only job is un-sticking stuck mail. Admins (and Super Admins) can assign the role from User Management; a green `badge-queue-manager` shows in the sidebar and user lists. Weight order is now viewer 0 < queue_manager 1 < operator 2 < admin 3 < super_admin 4 (roles are stored by name, so existing accounts are unaffected). E2E suite verifies the role can run queue actions and is rejected on domains/credentials/configuration (models.py, app.py `queue_manager_required`, templates/queue.html, tests/e2e_smoke.py — 49 checks).

### Upgrade (v4.4.x → v4.5.0)
Pull and rebuild/restart. No configuration changes; existing users keep their roles.

---

## 4.5.1 — Permission-Filtered Sidebar (2026-10-15)

### Changed
- **The sidebar now shows only what the user's role can actually use.** Every signed-in user sees **Dashboard, Email Logs, and Queue**. The **Allowed Domains / SMTP Credentials / DKIM Keys** section appears for Operator and above, and those three pages are now blocked below Operator server-side (previously every signed-in role could open them read-only) — so a hidden menu item is genuinely inaccessible. **Configuration / Users** remain Admin and above (templates/base.html, app.py).

### Upgrade (v4.5.0 → v4.5.1)
Pull and rebuild/restart. No configuration changes. Note: Viewer and Queue Manager accounts lose read-only access to the domains/credentials/DKIM pages — intended, per the menu-permissions model.

---

## 4.4.4 — Fix Outlook Hanging During AUTH LOGIN (2026-10-15)

### Fixed
- **Outlook (and other strict SASL LOGIN clients) froze at authentication.** aiosmtpd's default AUTH LOGIN challenges are `base64("User Name\x00")` and `base64("Password\x00")` — wrong wording plus a trailing NUL byte. Outlook's SASL LOGIN client only answers the canonical `base64("Username:")` / `base64("Password:")` challenges; on the defaults it sends nothing after the 334, the session hangs until the client times out ("connection to the server was interrupted"), and the log just stops at `<< challenge:`. The listener now emits the canonical challenges (an `OutlookCompatController` factory override — no behavior change for smtplib/Thunderbird/other clients, which accept either form). A regression check decodes the live 334 challenges and pins them to the canonical strings (smtp_server.py, tests/e2e_smoke.py — 44 checks).

### Upgrade (v4.4.3 → v4.4.4)
Pull and rebuild/restart. Required for Outlook clients; no configuration changes.

---

## 4.4.3 — Logging Actually Lands in the Data Folder (2026-10-15)

### Fixed
- **The Configuration page's Debug Logging toggle did nothing at all.** The `debug_logging` row was written by the config form but never read back by anything — the debug file (`data/smtp_debug.log`) was only created when the `DEBUG_LOGGING` env var was set, and the SMTP layer's debug helpers never leveled up. The toggle is now authoritative once the database is initialized (DB-wins, like every other setting): flipping it in the UI and restarting enables DEBUG level and the rotating debug log (run.py, with the env var demoted to a first-boot seed). `DEBUG_LOGGING` / `LOG_RETENTION_DAYS` joined the set-but-ignored env-var warning map to match.
- **Docker deployments now log to the data folder by default.** `LOG_FILE` defaults to `/app/data/relay.log` on the `smtp-data` volume (was: stdout only, nothing persisted), and the app log is **rotated (20 MB x 5)** instead of growing unbounded. Set `LOG_FILE=` (empty) to return to stdout-only. `docker compose logs` continues to work — stdout is still attached (run.py, docker-compose.yml, docker-compose.portainer.yml, .env.example).

### Upgrade (v4.4.2 → v4.4.3)
Pull and rebuild/restart. After the restart you'll find `relay.log` in the data volume; enable Debug Logging on the Configuration page for `smtp_debug.log`. No configuration changes required.

---

## 4.4.2 — Fix Microsoft Sign-In (2026-10-15)

### Fixed
- **Microsoft sign-in crashed before contacting Microsoft.** `LOGIN_SCOPES` included `openid` and `profile`, which MSAL reserves and raises `ValueError: You cannot use any scope value that is reserved` on the very first call (`get_authorization_request_url`) — the Microsoft button 500'd without ever reaching Azure. MSAL unions the reserved scopes into the flow itself; the app now requests only `['email']` (the optional claim used for JIT account matching). `oid`/`preferred_username` claims are unaffected, and no Entra app-registration permissions are needed (unchanged). A regression check pins the scope list against MSAL's reserved set (auth_oauth.py, tests/e2e_smoke.py).

### Upgrade (v4.4.0 / v4.4.1 → v4.4.2)
Pull and rebuild/restart. Required for Microsoft sign-in to work; no configuration changes.

---

## 4.4.1 — Startup DB Error Shows the Underlying Cause (2026-10-15)

### Changed
- The startup "cannot reach PostgreSQL" error now prints the **underlying driver error** (`_database_reachable` returns it; run.py `load_config` displays it) — connection refused, DNS failure, and `FATAL: password authentication failed` are very different fixes that the old message lumped together. Adds a container/network checklist to the same message (run.py).

### Upgrade (v4.4.0 → v4.4.1)
Pull and restart. Diagnostics only; no behavior change on healthy deployments.

---

## 4.4.0 — Bug-Fix & Hardening Pass (2026-10-15)

Full debug review of every module (web layer, SMTP engine, models, launcher, OIDC, crypto, templates, deploy packaging). 25+ defects fixed; all 42 e2e checks green.

### Fixed — SMTP engine (smtp_server.py)
- **Restart no longer loses in-flight messages.** Rows stranded in `'processing'` by a deploy/crash (delivery threads are daemons with no join) were unreachable by every recovery path — the queue processor now requeues them on startup. The client already got `250`, so the message must still go out.
- **Direct delivery tries the recipient domain's MX hosts in preference order** (RFC 5321 §5.1) — only the highest-priority MX was ever attempted, so a down primary meant deliverable mail failed after 3 retries while the backup MX was healthy. Transient failures (connect refused/timeout, 4xx) advance to the next MX; authoritative 5xx stop the list. NXDOMAIN (nonexistent domain) is now classified as a **permanent** failure instead of burning three doomed retries.
- **AUTH infrastructure failures no longer hang clients.** The `451` temporary-failure reply from the DB-error path was silently dropped (`handled=True` without a self-pushed response) — the client waited forever for a reply to AUTH that never came (verified against aiosmtpd 1.4.6).
- **Disabling `require_auth` now takes effect live.** aiosmtpd pushed its own 530 before `handle_MAIL` ran whenever the listener was constructed with auth enforcement on, so the web-UI toggle did nothing until a listener restart. Enforcement is now solely the live-config check in `handle_MAIL` (both directions immediate).
- **`BODY=8BITMIME` is declared** to upstreams that advertise it when the message actually carries 8-bit bytes — undeclared 8-bit bodies were rejected by strict receivers and burned all retries.
- **DKIM signatures use `relaxed/relaxed` canonicalization** (dkimpy's default was `relaxed/simple`, which breaks validation whenever any transport hop normalizes line endings or trailing blank lines).
- **Recipient syntax is validated at RCPT** (`553` for addresses without a domain) instead of accepting garbage that could never deliver; "recipient without domain" is classified as a permanent failure.
- **The global rate limit counts every accepted message** in the rolling hour (previously only sent/queued — a client hammering a dead upstream was never throttled, exactly when throughput needed clamping).
- **Delivery concurrency is bounded** (10 slots). Both dispatch paths (immediate-at-DATA and the 30 s tick) share a semaphore; a burst can no longer herd unbounded 30-second-timeout sockets at the upstream. A `Thread.start()` failure after the durable queue commit now reports `250` (the tick delivers it) instead of `451` — which made compliant clients resend and double-deliver.
- Per-credential hourly rate limiting is now **atomic** (single guarded UPDATE for the window reset and the increment) — concurrent sessions could overshoot the cap, and the MAIL-path window reset was never committed.

### Fixed — Web UI (app.py, templates)
- **Dashboard daily stats work again on PostgreSQL**: `func.date()` returns `datetime.date` objects (it returned strings on SQLite, pre-v4.3.0) and the lookups used `'%Y-%m-%d'` strings that never matched — sent/failed-today and the 7-day chart rendered zeros forever.
- **"Retry All Failed" is atomic** — the old read-modify-write could clobber a row the delivery worker claimed between SELECT and commit, resurrecting an already-sent message for redelivery.
- **Log-detail modal works** — `openLogDetail._seq` was never initialized (`++undefined` → NaN; `NaN !== NaN` rejected every response), so the modal stuck on "Loading…" forever.
- **The last active admin cannot be locked out**: toggle/role/delete handlers are serialized (process-wide lock — workers=1) and role changes now check the last-admin guard too.
- Login is timing-equalized for unknown usernames (a dummy bcrypt comparison), and the profile "Change Password" card is hidden for Microsoft-only accounts where it could never succeed. The dead "switch sign-in method" button for non-admin Entra users is hidden. Chart.js init is guarded so a blocked CDN no longer kills the live stat poller; dark-mode no longer flashes light on every page load.
- Validation hardening: descriptions capped at their column size (500 chars — an over-long value crashed the page with a raw 500), DKIM selectors validated per RFC 8616 grammar (63 chars, ASCII — `isalnum()` accepted `café`), `banner_hostname`/`relay_helo_hostname` validated as hostnames (CRLF there would inject into the SMTP dialogue), usernames accept `+` and `'` (legal in Entra UPNs like `bob+work@corp.com`), emails stored lowercased.
- Saving config now warns when Microsoft sign-in is enabled without an explicit `azure_redirect_uri` (the derived callback URL breaks behind a reverse proxy).
- Dead code removed (unreachable `debug_logging` branch, redundant re-read); stale comments corrected.

### Fixed — Bootstrap & config (run.py, models.py, crypto_utils.py)
- **A crash mid-first-boot can no longer silently discard the operator's environment configuration.** The "database is initialized" check only looked for the `users` table, which is created *before* the env seed runs — a crash in that window made every later boot skip seeding. A `bootstrap_complete` marker is now written after seeding completes; without it, the (idempotent, default-aware) first-boot path re-runs.
- **ENC1 secrets are protected on every boot** (previously only when legacy bootstrap sections were present — which happens exactly once), so a crash between seed and encryption, or a legacy plaintext row, heals on the next startup instead of staying plaintext forever.
- The set-but-ignored env-var warning now decrypts ENC1-stored secrets before comparing — previously `RELAY_AUTH_PASSWORD` permanently warned "set but ignored" even when the stored envelope was exactly that env value.
- `WEB_SECURE_COOKIES` / `WEB_TRUST_PROXY` joined the ignored-env warning map (security-relevant vars never warned).
- Malformed `DATABASE_URL` and unreachable PostgreSQL fail fast with a clear message instead of being misread as "first boot" and dying later with a raw traceback; bad integer env vars (`WEB_PORT=80o25`) name the offending variable.
- Passwords starting with `$2` are no longer treated as unusable legacy bcrypt hashes (the full `$2a/$2b/$2y$…` shape is required), and legacy plaintext passwords pass through unstripped (trailing whitespace is legal in a password).
- `debug_logging` default output moved into the persisted data directory (`data/smtp_debug.log`) so debug logs survive container recreation.
- `email_queue.log_id` is indexed (added to the model and created idempotently on existing databases) — retention deletes on `email_logs` forced a sequential scan of the queue table.

### Fixed — OIDC (auth_oauth.py)
- `/login/azure` and the callback are now throttled like password login (failures were recorded but the throttle was never consulted — the endpoint was unthrottled).
- Cancelling at Microsoft (`error=access_denied` with a valid state) shows a neutral "sign-in was cancelled" message and records no failure — previously it was treated as tampering and counted toward lockout, so a user could lock themselves out by cancelling five times.

### Changed — Deployment
- **`docker-compose.yml`: the postgres service is on the app's network.** It silently landed on Compose's `default` network while the app joined `smtp-network` — the stack could never boot (`postgres` unresolvable). The Portainer file was already correct.
- **In-container ports are pinned** (`WEB_PORT=8025`, `SMTP_LISTEN_PORT=2525`): the port mappings forward to fixed container ports, so an operator-set `WEB_PORT` made the service unreachable while the healthcheck still reported healthy. Remap the host side with `WEB_PORT_HOST` / `SMTP_PORT_HOST`.
- `stop_grace_period: 30s` on the app service — Docker's 10 s default SIGKILLed gunicorn mid-graceful-stop.
- All 15 requirements are **exact-pinned** (5 were open-ended minimums — non-reproducible builds); pinned to the versions the test suite runs against.
- The Docker image no longer ships gcc (~200 MB unused — every dependency has cp312 wheels); docs/tests are excluded from the build context.

### Docs
- New **[docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md)**: nginx + Caddy examples, the `web_secure_cookies` + `web_trust_proxy` + `azure_redirect_uri` trio, and the X-Forwarded-For appending requirement.
- `docs/CONFIGURATION.md` re-keyed to the real flat `relay_config` names (`web_host`, `relay_host`, … — it still showed config.json-era nested keys as current) with `.env` examples; `relay_host`'s effective first-boot default (`smtp.example.com`) documented; README's false "config.json migrates automatically" claim corrected; USER_ROLES/INSTALLATION/ARCHITECTURE drift fixed (DKIM sidebar visibility, clone path, banner sample, EmailLog statuses, class count, gunicorn row); new "Known Limitations" section in ARCHITECTURE.md.

### Upgrade (v4.3.0 → v4.4.0)
Replace the source files (or `docker compose up -d --build`) and restart — no configuration changes required. The `email_queue.log_id` index and any ENC1-able plaintext secrets are migrated automatically on first boot of the new version.

---

## 4.3.0 — PostgreSQL-Only + Database-Only Configuration (2026-10-15)

### Breaking changes
- **SQLite removed.** PostgreSQL is the only supported database. `DATABASE_URL` is **required** — the app refuses to start without it (run.py, app.py). The SQLite WAL/busy-timeout pragma machinery is gone (models.py); pooled PostgreSQL connections with `pool_pre_ping` handle dropped server-side connections.
- **config.json removed.** The database is the ONLY configuration store: on a fresh database the environment variables seed `relay_config` once, and from then on the web UI owns everything. Deleted: the config.json secret-key bootstrap, the DB→file mirror, the `/config/reload` and `/config/save-to-file` routes, the "config.json File Operations" card, the one-time legacy config.json adoption, `CONFIG_PATH`, and `DATABASE_PATH` (run.py, app.py, templates/config.html).
- **migrate_to_postgres.py deleted** — production is already on PostgreSQL; there is no in-repo upgrade path from SQLite (v4.2.1).

### Added
- **`web_trust_proxy` setting** — `trust_proxy` was previously config.json-only and would have been lost with the file; it is now a proper database key (Config page / `WEB_TRUST_PROXY` env seed). The login throttle keys on `X-Forwarded-For` only when enabled (models.py, app.py, run.py).
- **`WEB_SECURE_COOKIES` env var** — seeds `web_secure_cookies` on first boot. `SESSION_COOKIE_SECURE`/`trust_proxy` are now applied from the database at startup (previously inert in database mode) (run.py, app.py).

### Changed
- Startup fails fast with a clear message when `DATABASE_URL` is missing (run.py `load_config`).
- The set-but-ignored env-var warning now compares against the **live `relay_config` values** (previously compared against config.json, which does not exist in database mode) via a short-lived engine read (run.py `_read_stored_config`).
- `SECRET_KEY` seeds `web_secret_key` in the database on first boot; the stored key always wins afterwards (unchanged behaviour, legacy-config candidate removed) (app.py `_resolve_secret_key_db`).
- Docker compose files ship an enabled `postgres:16-alpine` service with a healthcheck and wire `DATABASE_URL` to it by default; `POSTGRES_PASSWORD` joins `SECRET_KEY` as a required stack variable. `pg-data` is the stateful volume; `smtp-data` now only holds an optional `LOG_FILE` (docker-compose.yml, docker-compose.portainer.yml, .env.example).
- The e2e smoke suite runs against PostgreSQL only: `SMOKE_DB_URL` is required and tables are dropped for a clean schema every run (tests/e2e_smoke.py).
- Docs rewritten for the PostgreSQL-only, database-only configuration model; web settings are documented by their `relay_config` key names (`web_host`, `web_trust_proxy`, …) (README.md, docs/).

### Upgrade (v4.2.1 → v4.3.0)
Set `DATABASE_URL` (it was optional in v4.2.1), replace the source files, start. All data stays in PostgreSQL. **Nothing migrates from `config.json`** — v4.3.0 does not read that file: v4.2.1 *database-mode* installs already hold their web settings and `web_secret_key` in PostgreSQL and carry over untouched; SQLite + config.json installs have no in-repo migration path (see the breaking changes above).

---

## 4.2.1 — PostgreSQL Support + Database-Backed Configuration (2026-10-15)

### Added
- **PostgreSQL backend** — set `DATABASE_URL` (environment; read on every boot, overrides `database.url` in config.json) to e.g. `postgresql+psycopg2://user:pass@host:5432/smtp_relay`. SQLite remains the default. Safe libpq defaults are applied automatically (`gssencmode=disable`, `connect_timeout=10`) unless set explicitly in the URL. Hand-rolled migrations now use the dialect-aware SQLAlchemy inspector; `psycopg2-binary` added to requirements (models.py, app.py, run.py, requirements.txt).
- **migrate_to_postgres.py** — one-shot data migration: copies all seven tables preserving IDs, aligns PostgreSQL identity sequences, verifies row counts, refuses a non-empty target unless `--force`. Works on raw SQLAlchemy Core (never boots the app, so nothing seeds the target). `python migrate_to_postgres.py --sqlite smtp_relay.db --postgres postgresql+psycopg2://...` (migrate_to_postgres.py).
- **Database-backed configuration** — when `DATABASE_URL`/`database.url` is set, the database is THE config store: web settings move into `relay_config` as `web_host`/`web_port`/`web_threads`/`web_secure_cookies`, the secret key is persisted as `web_secret_key`, `/config` writes only the database, and the config.json File Operations card is hidden. On a fresh database a legacy `config.json` is adopted exactly once (values default-aware + `web.secret_key`) so a move from file mode loses nothing; afterwards the file is ignored entirely. Classic config.json mode remains the default for SQLite (app.py, run.py, templates/config.html).
- Docker compose files pass `DATABASE_URL` through and include a commented `postgres:16-alpine` service; `.env.example` documents it (docker-compose.yml, docker-compose.portainer.yml, .env.example).

### Changed
- `DATABASE_URL` joins `DATABASE_PATH` as connection info read on EVERY boot (it can never be "first-boot only" — the app could not find its database otherwise) (run.py).
- First-boot detection is backend-aware: SQLite uses the file-exists signal; server backends connect and check for the `users` table (run.py `_database_ready`).

### Fixed
- PostgreSQL connections could stall 30+ seconds (or indefinitely) during libpq's GSSAPI negotiation, notably on macOS — `gssencmode=disable` is now applied by default (models.py `normalize_database_url`).

---

## 3.1.0 — Microsoft 365 Sign-in (2026-10-15)

### Added
- **Microsoft Entra ID sign-in for the web UI** — identity-only OIDC (`openid profile email`, ported from the newprojectwith365auth framework). Users sign in with the "Sign in with Microsoft" button; the local users table is the allow-list (matched first by Entra object ID, then by email among Entra accounts; the object ID is bound on first login). No Microsoft token is ever stored — the session holds only the local user id (auth_oauth.py).
- **Sign-in methods per account** — new `users.auth_source` column (`entra` = Microsoft-only, `local` = break-glass username/password). New accounts default to Microsoft sign-in; a break-glass local account must be Admin or Super Admin, enforced server-side. Local sign-in for Microsoft-only accounts is rejected with a pointer to the Microsoft button (app.py).
- **Break-glass local admin** — the initial `admin` account always keeps local password sign-in; local sign-in stays reachable on the login page ("Local administrator sign-in"). Sign-in method is switchable per user on the Users page (self-switch blocked; local restricted to admins; passwords cleared on switch — set a new one via Reset) (app.py, templates).
- **Azure configuration surface** — new `azure` config section: `enabled`, `tenant_id`, `client_id`, `client_secret` (write-only, stored ENC1-encrypted and auto-encrypted from config.json on startup like the relay passwords), and an explicit `redirect_uri` override for reverse-proxy/multi-hostname deployments. Web-UI section under Administration → Configuration (models.py, app.py, templates/config.html, config.json.example).
- `msal>=1.28.0` dependency; `ensure_dependencies` now checks it (requirements.txt, run.py).

### Notes
- Upgrading databases: all existing accounts migrate to `auth_source='local'` (idempotent `_migrate_oauth()`), so the upgrade can never lock anyone out. Move accounts to Microsoft sign-in from the Users page once Azure is configured.
- The login throttle now also applies to Microsoft sign-in failures (keyed on IP + login hint).

---

## 3.0.2 — Security & Reliability Hardening (2026-10-15)

Full audit of the codebase (static analysis + dynamic testing of the web UI and SMTP engine against mock servers). Fixes below are verified by automated end-to-end tests.

### Fixed — Critical
- **Authenticated SMTP submission was impossible.** `RelayAuthenticator` returned `AuthResult(success=True)` without `auth_data`, so aiosmtpd overwrote `session.auth_data` and every authenticated `MAIL FROM` was rejected with `530`. Authenticated submission, per-credential rate limiting and credential attribution all work again (smtp_server.py).
- **DKIM signatures were delivered inside the message body.** The signature block was appended after the body instead of prepended to the header section, so receiving servers saw no signature and `dkim.verify()` failed. Signatures are now prepended and verify correctly; unsigned deliveries (no active key for the sender domain) now log a warning instead of failing silently (smtp_server.py).
- **Upstream relay passwords were destroyed by bcrypt "encryption".** The one-way hash was stored and then replayed as the upstream AUTH password (guaranteed `535`), and config.json was rewritten with a literal `[ENCRYPTED]` placeholder. Upstream/direct auth passwords are now stored with reversible Fernet encryption (`ENC1:` envelopes in `crypto_utils.py`, keyed from the web secret_key), are write-only in the web UI, and legacy corrupt values are detected and reported (app.py, models.py, smtp_server.py, crypto_utils.py).
- **Direct-mode delivery crashed with `NameError: debug_log_command`** whenever direct-delivery auth was configured; failures were misreported as delivery errors. Fixed the call name (smtp_server.py).
- **Pre-2.2.0 databases were never migrated to `message_id`**, breaking all inbound mail with `OperationalError` on old databases. `_migrate_schema()` now adds both `raw_headers` and `message_id` idempotently (app.py).
- **Malformed `recipients` data crashed the web UI** — a non-list JSON value in `email_logs.recipients` returned 500 for `/`, `/logs` and `/queue` until the row was removed. A `recipients` template filter now tolerates any stored value (app.py + templates).

### Fixed — High
- **Open redirect on login** — `?next=` is validated (same-host relative URLs only) and is now carried through the login form (app.py, templates/login.html).
- **Weak/default `secret_key`** — missing or known-weak values are replaced with a generated key that is persisted to config.json; sessions can no longer be forged with the example value (app.py, run.py).
- **Well-known default credentials** — the initial `admin` account now gets a randomly generated password, logged once at first boot (app.py, run.py).
- **Docker env changes silently ignored after first boot** — behavior kept by design but now loud: startup logs exactly which environment variables differ from the stored config and how to re-apply them; compose volume moved from `/app` to `/app/data` so image rebuilds are no longer shadowed by the volume (run.py, docker-compose.yml, Dockerfile).
- **Direct delivery ignored `direct_use_starttls`** (it reused the relay flag) and could wrongly apply implicit TLS to MX hosts (smtp_server.py).
- **`max_sends_per_hour` accepted garbage** (`'abc'` → 500; `-5` → permanently rate-limited credential). Bounded integer parsing with a clear error (app.py).
- **Stored XSS via names interpolated into inline JS strings** in users/credentials/domains templates — handlers now read `data-*` attributes; input charset validation added server-side (app.py, templates).
- **`.dockerignore` added** — image no longer ships `.git/`, the host venv, caches or databases.
- **SQLAlchemy pin raised to 2.0.37** to match the documented minimum (requirements.txt).

### Added
- Login throttling (5 failures / 15 min per IP+username, per process) (app.py).
- Security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` (app.py).
- Session cookie hardening: `SameSite=Lax`, opt-in `Secure` via `web.secure_cookies` (app.py, config.json.example).
- Minimum 8-character password policy on all password paths (app.py).
- Validation for allowed domains (hostname syntax, ≤255 chars), usernames, and case-insensitive email uniqueness (app.py).
- SRI `integrity` attributes for the Font Awesome and Chart.js CDN assets (templates).
- Accessibility: dialog semantics/labels/Escape handling/focus for all modals, aria-labels on icon-only controls, `prefers-reduced-motion` support, keyboard focus outlines (templates, static/css/style.css).
- DKIM permission matrix rows in USER_ROLES.md; DKIM page viewable by all roles, changes still operator+ (app.py, templates/base.html, docs/USER_ROLES.md).

### Changed
- `/config` no longer echoes relay/direct auth passwords into the HTML; blank field = keep current value (app.py, templates/config.html).
- `/logout` is POST-only with CSRF (app.py, templates/base.html).
- Database config is no longer re-seeded from config.json on every boot for values already changed at runtime (app.py).
- SQLite busy timeout raised to the documented 20 s (models.py, app.py).
- `relay_use_starttls` default aligned with docs/env (`true`) (models.py).
- `/api/stats` no longer 500s if the SMTP server status check itself fails (app.py).
- Dashboard chart data passed as JSON via `|tojson`; dead "rejected/rate_limited" UI statuses and counter removed; `sender` filter restored to the logs page (app.py, templates).
- Dead code removed (unused `role_required` decorator, unreachable open-relay branch, unused imports); whitespace/lint cleanup across all Python files (ruff/pyflakes clean).
- Non-root `appuser` in the Docker image with `/app/data` data directory (Dockerfile).
- **Production WSGI server** — gunicorn 26.2.0 replaces the Flask dev server on Linux/Docker (single worker, `gthread`, `WEB_THREADS` env, default 4; pooled DB connections disposed pre-fork); Windows and missing-gunicorn cases fall back to the dev server with a warning (run.py, requirements.txt, .env.example, docker-compose.yml).
- **Three-phase delivery** — delivery threads now release the DB session before any network I/O: short read/snapshot context (incl. DKIM signing and credential decryption) → pure smtplib phase → fresh-context write; a vanished/requeued row no longer wedges the worker (smtp_server.py).
- **Inter font self-hosted** — variable woff2 served from `/static/fonts/`, removing the Google Fonts CDN dependency (SRI-impossible); font stacks unchanged (static/, templates).
- models.py line endings normalized to LF (no content changes).
- **Portainer/Docker deployment support** — `CONFIG_PATH` env override moves config.json into the persistent volume (generated secret_key and ENC1-encrypted passwords now survive container recreation); overridable host ports (`WEB_PORT_HOST`/`SMTP_PORT_HOST`); service `healthcheck` (python-based, Portainer shows Healthy); `container_name` removed for Portainer stack compatibility; new step-by-step guide in `docs/PORTAINER.md` covering both flows: build-it-yourself + web editor (`docker-compose.portainer.yml`, no git access needed in Portainer) or repository stack.

### Fixed — second review pass (same release)
- **Direct delivery sent every recipient to one MX** — a mixed-domain message went to whichever recipient domain sorted first (nondeterministic `set` ordering), so cross-domain direct delivery never worked. MX targets are now resolved per recipient domain in the network phase, and all targets resolve before anything is sent (smtp_server.py).
- **Double-delivery race in the queue** — the QueueProcessor tick and the DATA-time delivery thread both did a non-atomic check-then-mark, so one message could be delivered twice under load. Queue rows are now claimed atomically (`UPDATE … WHERE status='queued'`) (smtp_server.py).
- **Enabling `require_auth` at runtime locked out AUTH entirely** — a listener started with `require_auth=false` had no authenticator attached, so flipping the setting in the web UI 530'd every MAIL FROM with no AUTH command advertised until restart. The authenticator is now always attached; enforcement stays live-config-driven (smtp_server.py).
- **DKIM private keys were stored in plaintext** — `dkim_keys.private_key` now holds an `ENC1:` Fernet envelope keyed from `web.secret_key`, with an idempotent startup migration for existing rows; signing decrypts transparently and legacy plaintext passes through (app.py, models.py, smtp_server.py).
- **Weak DKIM domain validation** — `/dkim/generate` used a loose isalnum check; it now enforces the same hostname syntax as allowed domains (app.py).
- **Login throttle broke behind a reverse proxy** — all clients shared the proxy IP, so one bad actor locked everyone out. New optional `web.trust_proxy` config keys the throttle on `X-Forwarded-For` when a proxy is actually in front (app.py, config.json.example, docs/CONFIGURATION.md).
- **config.json read-modify-write races** — the three writers (secret-key bootstrap, DB→file mirror, ENC1 startup migration) now serialize on a process-wide lock (app.py).
- **Config saves were not atomic** — `/config` POST committed each of ~27 keys separately; a mid-save failure left a half-updated config. New `RelayConfig.set_many()` upserts all keys in one commit (models.py, app.py).
- **`ensure_dependencies` checked only 5 of 13 requirements** — missing dnspython/dkimpy/gunicorn silently degraded direct delivery, DKIM signing, and the production web server. All requirement modules are now checked and the missing list is printed (run.py).
- **Startup banner could lie** — it printed SMTP listener/relay values from config.json, which the web UI may have changed since first boot; it now reads the live database config and shows the delivery mode (run.py).
- **Unbounded `per_page` on `/logs`** — one request could render the entire table; now capped at 200 (app.py).
- **Dashboard issued ~23 COUNT queries per view** — aggregated into 4 grouped queries (app.py).
- **Server start/stop/restart silently did nothing** when the SMTP component was not attached (app built outside run.py); they now flash an explicit error (app.py).
- **Deprecated `Query.get()`** replaced with `db.session.get()` in the Flask-Login user loader (app.py).
- **Duplicate Escape-key handler** removed from the logs page (base.html's global handler already covers it); the status-pill updater no longer relies on fragile `childNodes` indexing (templates).
- Removed a stray Windows `Zone.Identifier` junk file from the repo root; added `.gitignore` (config.json, databases, caches, venv) — `.dockerignore` existed but the repo had no git-side hygiene. New `tests/e2e_smoke.py` covers the relay end-to-end (auth submission → queue → delivery to a live sink, DKIM migration + signing, require_auth flips, direct-mode MX grouping, login throttling, config save) — the project previously had no tests at all.

### Documentation
- README/INSTALLATION: Python requirement corrected to 3.10+, first-boot generated admin password, linear retry backoff wording, dependency-check behavior, new Docker section.
- ARCHITECTURE: bootstrap-only env-var semantics, accurate delivery-thread description, 20 s busy timeout, corrected stack.
- CONFIGURATION: added `delivery_mode`, `direct_delivery`, `web.secure_cookies`, debug-log keys, Docker env-var section; corrected relay defaults.
- config.json.example aligned with real defaults (`require_auth: true`, port 2525, limits 100/1000).

---


## Changes (2026-04-22)

### Feature
| Issue | Fix | Files |
| Add Linux Docker support | Containerized application with python:3.12-slim base image | Dockerfile, docker-compose.yml, .env.example |
| Add Direct Delivery mode | MX lookup delivery bypassing upstream relay | smtp_server.py, models.py, app.py, templates/config.html |
| Add env var config override | All settings overridable via environment variables | run.py |
| Add DKIM signing | Per-domain DKIM keys with signing at delivery time | models.py, smtp_server.py, app.py, templates/dkim.html |

---

## [3.0.1] — 2026-03-18

### Added
- **Queue cancellation confirmations** — Delete buttons for queued messages now prompt for confirmation to prevent accidental deletions
- **Processing section notice** — Added informational alert explaining why actively processing messages cannot be cancelled

### Changed
- **Version bump** — All source files updated to v3.0.1

---

## [3.0.0] — 2026-03-18

### Added
- **Version badge in web interface** — Version number now displayed in the sidebar footer of all pages
- **Complete web interface redesign** — Modern, cleaner UI with improved visual hierarchy

### Changed
- **Version bump** — All source files updated to v3.0.0
- **Enhanced color palette** — More refined indigo/blue primary colors with better contrast
- **Improved sidebar** — Smoother hover states and active indicator bar
- **Better stat cards** — Larger values with gradient icon backgrounds
- **Refined dark mode** — Improved contrast and color balance for dark theme
- **Enhanced animations** — Smoother page transitions and hover effects
- **Improved tables** — Better zebra striping and hover states
- **Button refinements** — Better hover states and visual feedback

---

## [2.2.0] — 2025-06-03

### Fixed
- **Page hanging / server not responding** — Delivery threads no longer hold the SQLite database session open during the SMTP connection (which could block for up to 30 seconds). The DB session is now closed before the network connection is made and a fresh session is opened to write the result. The queue processor (`_tick`) also now spawns threads instead of calling `_deliver` synchronously, preventing the background thread from blocking web requests.
- **SQLite busy timeout** — Added a 20-second busy timeout to the SQLAlchemy engine so that web requests waiting for a brief DB lock return an error instead of hanging indefinitely.
- **Message-ID always blank** — The database migration was only adding the `raw_headers` column but not `message_id`. The migration function (`_migrate_schema`) now also adds `message_id` to existing databases, so the field is correctly populated for all new messages.

### Changed
- **Email Headers panel** — The headers block in the log detail modal now shows a field count, has a taller scrollable area (420 px), and displays a styled scrollbar. When no headers are available a descriptive message is shown instead of an empty section.
- **Log detail modal** — Modal is slightly wider (780 px) and the body now scrolls independently while the header and footer remain fixed, making it easier to read long header lists.
- **Migration function renamed** — `_migrate_raw_headers()` renamed to `_migrate_schema()` to reflect that it now manages all schema migrations in one place.
- **Version bump** — All source files updated to v2.2.0.

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
