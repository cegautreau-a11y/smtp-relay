# Configuration Reference

**SMTP Mail Relay v4.5.1**

All settings are stored in the **PostgreSQL database** (`relay_config` table) — the database is the only configuration store. On first boot (empty database) the environment variables seed the values; settings can then be changed at runtime through the web interface under **Configuration**.

Every setting below is listed by its **`relay_config` key name** — the same name used in the database and on the web UI's Configuration page.

---

## Configuration Hierarchy

The application uses a single-layer configuration system:

1. **Database (`relay_config` table)** — THE configuration store, read fresh on every SMTP command and web request
2. **Environment variables** — seed the rows **once**, on first boot into an empty database; afterwards they are ignored

When you save settings in the web interface, they are written to the database. There is no config file anymore.

### Environment Variables Are First-Boot Only

Environment variables (Docker) are applied **once**, on first boot, while the database is still empty: they seed the `relay_config` rows. From then on the database owns the configuration and environment variables are **ignored** (set-but-ignored variables are reported with a startup warning). To re-apply environment variables, empty the database (drop it, or the Docker volume) and start again. See the [Environment variables (Docker)](#environment-variables-docker) section below.

---

## Full Settings Reference

### `web_*` — Web Management Interface

| Key | Type | Default | Description |
|---|---|---|---|
| `web_host` | string | `"0.0.0.0"` | Bind address for the Flask web server. Use `0.0.0.0` for all interfaces or a specific IP to restrict access |
| `web_port` | integer | `8025` | Port number for the web interface. **Inside Docker this is pinned to 8025** to match the container port mapping — remap the host side with `WEB_PORT_HOST` instead |
| `web_secret_key` | string | *(generated)* | Secret key used for session security, CSRF protection, and ENC1 secret encryption. Seeded from `SECRET_KEY` on first boot and persisted in the database; if unset, a random key is **generated and stored in the database on first boot**. A later `SECRET_KEY` change never rotates it (it would invalidate every encrypted secret) — rotate deliberately via the `web_secret_key` row |
| `web_secure_cookies` | boolean | `false` | Set the session cookie's `Secure` flag (cookies are only sent over HTTPS). Enable when serving the web UI behind HTTPS / a TLS-terminating reverse proxy |
| `web_trust_proxy` | boolean | `false` | Trust `X-Forwarded-For` / `X-Forwarded-Proto` from the reverse proxy: the login throttle keys on the right-most XFF hop (the one your proxy appended — client-supplied hops are spoofable) and redirects/OAuth derive `https://` URLs. Enable ONLY when a reverse proxy actually fronts the app; with it off, all clients behind a proxy share the proxy IP for throttling |
| `azure_enabled` | boolean | `false` | Enable Microsoft Entra ID sign-in for the web UI. Requires tenant/client IDs and a client secret; the secret is stored ENC1-encrypted and is write-only in the web UI |
| `azure_tenant_id` | string | `""` | Directory (tenant) ID (GUID) from the Azure app registration |
| `azure_client_id` | string | `""` | Application (client) ID from the Azure app registration |
| `azure_client_secret` | string | `""` | Client secret value. Write-only in the web UI; blank keeps the stored value. ENC1-encrypted at rest (the database holds the envelope). Rotating `web_secret_key` invalidates it — re-enter it |
| `azure_redirect_uri` | string | `""` | OAuth redirect URI registered in Azure (`https://<host>/login/azure/callback`). Set explicitly when the UI is reachable via a proxy or multiple hostnames; empty derives it from the request, which misbehaves behind a TLS-terminating proxy. See [docs/REVERSE_PROXY.md](REVERSE_PROXY.md) |
| `web_threads` | integer | `4` | Number of web request threads (seeded from `WEB_THREADS` on first boot). Feeds the gunicorn thread count on Linux/Docker; ignored by the Flask dev-server fallback on Windows |

**Example (`.env` on first boot):**
```ini
SECRET_KEY=a1b2c3d4e5f6...your-random-string
WEB_HOST=0.0.0.0
WEB_THREADS=4
WEB_SECURE_COOKIES=false
WEB_TRUST_PROXY=false
```

> **Note:** Changes to `web_host`, `web_port`, `web_threads`, `web_secret_key`, `web_secure_cookies`, and `web_trust_proxy` require a full application restart to take effect. All user sessions are invalidated on server restart by design.
>
> **Reverse proxy?** Set `web_secure_cookies=true` + `web_trust_proxy=true` together and configure an exact `azure_redirect_uri` — full walkthrough with nginx/Caddy examples in [docs/REVERSE_PROXY.md](REVERSE_PROXY.md).

---

### `listen_*` — Inbound SMTP Server

These settings control the SMTP server that your applications connect to for sending mail.

| Key | Type | Default | Description |
|---|---|---|---|
| `listen_host` | string | `"0.0.0.0"` | Bind address for the SMTP listener. Set to your server's IP or `0.0.0.0` for all interfaces |
| `listen_port` | integer | `2525` | SMTP listener port. **Inside Docker this is pinned to 2525** to match the container port mapping — remap the host side with `SMTP_PORT_HOST` instead |
| `banner_hostname` | string | `"relay.local"` | Hostname shown in the SMTP `220` greeting. Must be a valid hostname (letters, numbers, dots, hyphens — control characters are rejected at save time) |
| `require_auth` | boolean | `true` | Require SMTP AUTH (LOGIN) before accepting mail. **Strongly recommended**. Toggled live — takes effect on the next message, no restart needed (either direction) |
| `enable_tls` | boolean | `false` | Enable TLS encryption on the SMTP listener |
| `tls_cert_path` | string | `""` | Path to the TLS certificate file (PEM format). Required if `enable_tls` is true |
| `tls_key_path` | string | `""` | Path to the TLS private key file (PEM format). Required if `enable_tls` is true |

**Example (`.env` on first boot):**
```ini
SMTP_LISTEN_HOST=192.168.1.50
SMTP_LISTEN_PORT=2525
BANNER_HOSTNAME=relay.company.local
REQUIRE_AUTH=true
ENABLE_TLS=false
TLS_CERT_PATH=
TLS_KEY_PATH=
```

**Choosing a bind address:**

| Value | Use Case |
|---|---|
| `"0.0.0.0"` | Accept connections from any network interface (production) |
| `"127.0.0.1"` | Accept connections only from the local machine (testing) |
| `"192.168.1.50"` | Accept connections only on a specific network interface |

> **Windows Note:** If binding to `0.0.0.0` fails, the application automatically retries with `127.0.0.1`. SMTP listener changes (host/port/TLS) require an SMTP server restart (use the Restart button on the Dashboard).

---

### `relay_*` — Upstream SMTP Server

These settings control where the relay forwards mail for final delivery (used when `delivery_mode` is `relay`).

| Key | Type | Default | Description |
|---|---|---|---|
| `relay_host` | string | `"localhost"` (DB row default); fresh installs start with **`smtp.example.com`** seeded from the `RELAY_HOST` env default | Hostname of the upstream SMTP server — replace the placeholder with your real relay before going live |
| `relay_port` | integer | `25` | Port of the upstream SMTP server |
| `relay_use_tls` | boolean | `false` | Use implicit TLS (SMTPS) for the connection. Typically used with port 465 |
| `relay_use_starttls` | boolean | `true` | Upgrade the connection to TLS using STARTTLS. Typically used with port 587. If the upstream does not support STARTTLS, delivery falls back to plaintext and a warning is logged |
| `relay_auth_user` | string | `""` | Username for authenticating with the upstream server. Leave empty for no auth |
| `relay_auth_password` | string | `""` | Password for authenticating with the upstream server. Write-only in the web UI (blank keeps the stored value); ENC1-encrypted at rest |
| `relay_helo_hostname` | string | `"localhost"` | Hostname sent in the EHLO/HELO command to the upstream server. Should be a valid FQDN (validated at save time) |

**Example — Microsoft 365 (`.env` on first boot):**
```ini
RELAY_HOST=smtp.office365.com
RELAY_PORT=587
RELAY_USE_TLS=false
RELAY_USE_STARTTLS=true
RELAY_AUTH_USER=relay@company.com
RELAY_AUTH_PASSWORD=app-password-here
RELAY_HELO_HOSTNAME=mailrelay.company.com
```

**Example — On-premises Exchange (no auth):**
```ini
RELAY_HOST=exchange.company.local
RELAY_PORT=25
RELAY_USE_TLS=false
RELAY_USE_STARTTLS=false
RELAY_HELO_HOSTNAME=smtprelay.company.local
```

**TLS vs STARTTLS:**

| Setting | Port | Behaviour |
|---|---|---|
| `relay_use_tls: true` | 465 | Connection is encrypted from the start (SMTPS) |
| `relay_use_starttls: true` | 587 | Connection starts plain, then upgrades to TLS |
| Both `false` | 25 | Plain text connection (use only on trusted networks) |

> **Important:** Do not set both `relay_use_tls` and `relay_use_starttls` to `true`. Use one or the other.

> **Python 3.14 Note:** `relay_helo_hostname` must be a valid FQDN when using STARTTLS. Python 3.14 enforces stricter hostname validation for TLS connections.

---

### `delivery_mode` — Delivery Path

| Key | Type | Default | Description |
|---|---|---|---|
| `delivery_mode` | string | `"relay"` | How outgoing mail is delivered: `relay` sends everything to the upstream server configured under `relay_*`; `direct` resolves the recipient domain's MX records (via DNS) and delivers straight to the destination mail servers, bypassing the upstream relay |

> **Note:** In `direct` mode the machine running the relay must be able to reach destination mail servers on port 25 and have correct reverse DNS / SPF in place, otherwise delivery to major providers will fail. In `direct` mode the `relay_*` settings are not used. Direct delivery tries the recipient domain's MX hosts in preference order (RFC 5321 §5.1) and treats NULL MX / nonexistent domains as permanent failures.

---

### `direct_*` — Direct Delivery Settings

Used when `delivery_mode` is `direct`.

| Key | Type | Default | Description |
|---|---|---|---|
| `direct_use_starttls` | boolean | `false` | Attempt a STARTTLS upgrade on the connection to the destination MX server. If the MX server does not advertise STARTTLS, delivery **silently falls back to plaintext** and a warning is logged |
| `direct_auth_user` | string | `""` | AUTH username for direct delivery (rarely needed; MX servers usually accept anonymous mail) |
| `direct_auth_password` | string | `""` | AUTH password for direct delivery. Write-only in the web UI (blank keeps the stored value); ENC1-encrypted at rest |

> **Web sign-in:** accounts are per-user marked `entra` (Microsoft-only — the users table is the allow-list, matched by Entra object ID then email) or `local` (break-glass username/password, Admin accounts only; the initial `admin` account is always local). Microsoft sign-in uses identity-only OIDC (`openid profile email`); no Microsoft token is stored.
>
> **Secrets handling:** `relay_auth_password`, `direct_auth_password`, and `azure_client_secret` are **write-only in the web UI** (they cannot be viewed after saving) and are stored encrypted — Fernet (AES-128-CBC + HMAC-SHA256) with an `ENC1:` envelope prefix, keyed from `web_secret_key`. Plaintext values found in the database at startup are encrypted automatically on every boot. DKIM private keys are stored with the same `ENC1:` encryption. Rotating `web_secret_key` invalidates previously encrypted passwords and DKIM keys; re-enter passwords in the web UI and regenerate/re-upload DKIM keys afterwards.

---

### `limits` — Rate Limiting & Restrictions

| Key | Type | Default | Description |
|---|---|---|---|
| `max_message_size` | integer | `26214400` | Maximum email size in bytes (default 25 MB) |
| `max_recipients` | integer | `100` | Maximum number of recipients per email |
| `global_rate_limit` | integer | `1000` | Maximum messages **accepted** per rolling hour across all credentials. Every accepted message counts — including ones that later fail to deliver — so throughput is clamped even while an upstream is down |
| `allowed_source_ips` | string | `""` (allow all) | Source IPs allowed to connect. Empty = all IPs allowed. Supports CIDR notation, comma-separated, e.g. `192.168.1.0/24,10.0.0.5/32`. Unparseable entries fail CLOSED (the connection is rejected) |

**Example (`.env` on first boot):**
```ini
MAX_MESSAGE_SIZE=10485760
MAX_RECIPIENTS=50
GLOBAL_RATE_LIMIT=500
ALLOWED_SOURCE_IPS=192.168.1.10,192.168.1.20,10.0.0.5
```

> **Note:** Per-credential rate limits are configured separately in the web interface under **SMTP Credentials**. The global rate limit applies on top of individual credential limits.

---

### `queue_*` — Retry Behaviour

| Key | Type | Default | Description |
|---|---|---|---|
| `queue_retry_interval` | integer | `300` | Base interval between retry attempts (in seconds). Delay grows linearly with the attempt count |
| `queue_max_retries` | integer | `3` | Maximum number of delivery attempts before marking as failed |

**Example (`.env` on first boot):**
```ini
QUEUE_RETRY_INTERVAL=300
QUEUE_MAX_RETRIES=5
```

**Retry timing:** The delay between retries grows linearly — base interval x attempt number. For a base interval of 300 seconds:
- Retry 1: 300 seconds (5 minutes)
- Retry 2: 600 seconds (10 minutes)
- Retry 3: 900 seconds (15 minutes)

**After all retries are exhausted**, the message is marked as **failed** and held in the queue with its full message data intact. Failed messages are **never automatically discarded** — they remain in the system until an administrator takes action from the **Queue** page:

| Action | Scope | Description |
|---|---|---|
| **Retry** | Individual | Requeue a single failed message for another delivery attempt |
| **Retry All Failed** | Bulk | Requeue all failed messages at once for redelivery (atomic — rows already in flight are left alone) |
| **Delete** | Individual | Permanently remove a single failed message (with confirmation prompt in v3.0.1) |
| **Delete All Failed** | Bulk | Permanently remove all failed messages (with confirmation prompt) |

All retry actions reset the retry counter to zero and schedule immediate redelivery.

> **v3.0.1 Note:** Delete actions now prompt for confirmation to prevent accidental deletions. The Queue page also shows an informational notice for actively processing messages explaining why they cannot be cancelled.

---

### `logging` — Application Logging

| Key | Type | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` (env) | string | `INFO` | Log level — from the environment on **every** boot (console logging starts before the database is reachable) |
| `LOG_FILE` (env) | string | `/app/data/relay.log` in Docker; `""` otherwise | Application log file, **rotated (20 MB x 5)** — from the environment on **every** boot. Empty = console (stdout) output only. Keep it under `/app/data` in Docker so it lands on the `smtp-data` volume |
| `log_retention_days` | integer | `30` | Number of days to keep email log entries and sent queue entries in the database (stored in `relay_config`, editable in the web UI) |
| `debug_logging` | boolean | `false` | Detailed DEBUG logging to a separate rotating debug log file (`data/smtp_debug.log`, 50 MB x 5). Owned by the **Configuration page toggle** after first boot (`DEBUG_LOGGING` env only seeds it); when on, the root log level is DEBUG and the SMTP layer logs commands/responses |
| `DEBUG_LOG_FILE` (bootstrap) | string | `data/smtp_debug.log` | Path of the rotating debug log file. Defaults INSIDE the data directory so it survives container recreation. **Bootstrap only** — no web-UI equivalent |
| `DEBUG_LOG_MAX_SIZE` (bootstrap) | integer | `52428800` | Maximum debug log file size in bytes (50 MB) before rotation. **Bootstrap only** |
| `DEBUG_LOG_BACKUP_COUNT` (bootstrap) | integer | `5` | Number of rotated debug log files to keep. **Bootstrap only** |

**Example (`.env`):**
```ini
LOG_FILE=/app/data/relay.log
LOG_RETENTION_DAYS=90
DEBUG_LOGGING=false
```

> **Retention policy:** The automatic cleanup only purges successfully **sent** queue entries after `log_retention_days`. **Failed** queue entries are never automatically deleted — they are retained indefinitely until an administrator manually retries or deletes them from the Queue page. This ensures no undelivered email is ever silently discarded.

---

### `database` — Database Settings

> **PostgreSQL is the only backend (v4.3.0+):** set the `DATABASE_URL` environment variable (read on
> **every** boot — it is connection info, never first-boot-only) to e.g.
> `postgresql+psycopg2://user:pass@host:5432/smtp_relay`. The app refuses to start without it (and
> fails fast with a clear message when the URL is malformed or the server is unreachable).
> The database stores the mail data AND the configuration: web settings live in `relay_config` as
> `web_host`/`web_port`/`web_threads`/`web_secure_cookies`/`web_trust_proxy`, and the secret key is
> persisted as `web_secret_key`. Safe libpq defaults
> (`gssencmode=disable`, `connect_timeout=10`) are applied unless set in the URL; pooled
> connections use pre-ping health checks.

| Key | Type | Default | Description |
|---|---|---|---|
| `DATABASE_URL` (env) | string | *(required)* | Full SQLAlchemy URL for PostgreSQL (`postgresql+psycopg2://...`). Read on every boot |

> **Note:** The schema is created automatically on first boot with all required tables. PostgreSQL handles concurrency server-side — pooled connections use pre-ping so dropped server-side connections are replaced transparently. An internal `bootstrap_complete` marker row is written once first-boot seeding finishes; a crash mid-first-boot re-runs the (idempotent, default-aware) seeding on the next boot instead of silently dropping the environment configuration.

---

## Environment Variables (Docker)

When running via `docker-compose.yml`, the container is configured through environment variables (see `.env.example` for the full list with defaults — `SECRET_KEY`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `WEB_THREADS`, `RELAY_*`, `MAX_RECIPIENTS`, `GLOBAL_RATE_LIMIT`, `ALLOWED_SOURCE_IPS`, `QUEUE_*`, `LOG_*`, and so on). The **in-container** web/SMTP ports are fixed at 8025/2525 to match the port mappings; remap the host side with `WEB_PORT_HOST` / `SMTP_PORT_HOST`.

**These variables apply on FIRST BOOT only:**

- On first boot (while the database is still empty), env vars seed the `relay_config` rows.
- Afterwards the database owns the configuration — changing env vars in `docker-compose.yml` or `.env` and restarting the container **will not** change the effective configuration. Set-but-ignored variables are reported with a startup warning in the container log.
- To re-apply environment variables, empty the database — or the whole volume with `docker compose down -v` — and start again.

Docker-specific notes:

- `WEB_THREADS` (default `4`) sets the number of web request threads: it feeds the gunicorn thread count on Linux/Docker and is ignored by the Flask dev-server fallback on Windows.
- `DATABASE_URL` (and `POSTGRES_PASSWORD` for the in-stack `postgres` service) are **connection info** and are read on EVERY boot — the stack wires `DATABASE_URL` to the in-stack PostgreSQL by default; override it to target an external server.
- `LOG_FILE` defaults to `/app/data/relay.log` (rotated 20 MB x 5): the app logs to stdout AND the file, so logs survive recreation on the `smtp-data` volume and still show in `docker compose logs`. Set `LOG_FILE=` (empty) for stdout-only.
- `DEBUG_LOGGING` (default false) only seeds the Debug Logging toggle on first boot — afterwards the **Configuration page owns it**. When on, the app writes `data/smtp_debug.log` (rotated 50 MB x 5) and logs at DEBUG level.
- Upgrading the image requires recreating the container (`docker compose up -d --build`) but **not** deleting the volume.

---

## Runtime Configuration via Web UI

Most settings under `listen_*`, `relay_*`, `delivery_mode`, `direct_*`, `limits`, `queue_*`, and logging retention can be changed at runtime through the web interface:

1. Navigate to **Configuration** in the sidebar (requires Admin role)
2. Edit the desired settings
3. Click **Save** — changes are written to the database (the only config store)

> **Note:** Changes to `web_host`, `web_port`, `web_threads`, `web_secure_cookies`, and `web_trust_proxy` require a full application restart to take effect. SMTP listener changes (host/port/TLS) require an SMTP server restart (use the Restart button on the Dashboard). Relay/destination changes apply on the next message; `require_auth` applies immediately in both directions.
