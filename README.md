<div align="center">

# SMTP Mail Relay

**A complete SMTP server *and* mail relay — with a modern web management interface.**

Accept mail on your own SMTP listener, deliver it directly to recipient MX hosts,
or forward it through an upstream smarthost — DKIM-signed, queued, retried, and
fully logged. One Python process. One PostgreSQL database. Managed entirely from the browser.

![Version](https://img.shields.io/badge/version-4.5.1-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-12%2B-336791?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/docker--compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

> [!TIP]
> **Not just a relay.** Despite the name, this is a **fully functional SMTP server** in its own right:
> any mail client or application connects to port **2525**, authenticates, and sends — and the server
> can deliver the mail itself, straight to each recipient domain's MX hosts, with **no upstream
> provider required**.

## Contents

- [Overview](#overview)
- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Connecting Applications](#connecting-applications)
- [User Roles](#user-roles)
- [Deployment](#deployment)
- [Upgrading](#upgrading)
- [Troubleshooting](#troubleshooting)
- [Security Recommendations](#security-recommendations)
- [Requirements](#requirements)
- [Documentation](#documentation)
- [Project Layout](#project-layout)
- [License](#license)

---

## Overview

SMTP Mail Relay is a self-hosted mail platform that runs as a **single Python process** alongside a
**PostgreSQL** database. It ships three cooperating components:

| Component | What it does |
|---|---|
| **SMTP server** (aiosmtpd, port 2525) | Accepts mail from your applications and devices: SMTP AUTH (LOGIN/PLAIN), STARTTLS, size/recipient limits, IP + sender-domain allowlists, per-credential and global rate limits |
| **Delivery engine + queue** (PostgreSQL) | Signs with DKIM, then delivers via your upstream smarthost (**relay mode**) or directly to each recipient domain's MX hosts (**direct mode**), retrying transient failures with linear backoff |
| **Web management UI** (Flask, port 8025) | Dashboard, live configuration, domains, credentials, DKIM keys, users & roles, searchable logs, and full queue control |

There are no separate services to babysit — the SMTP listener, queue processor, and web UI all run
in one process, and PostgreSQL is both the message bus and the configuration store.

### Two delivery modes

| Mode | How mail leaves the server | Choose this when |
|---|---|---|
| **Relay** (default) | Everything is forwarded to one upstream smarthost (Microsoft 365, SendGrid, your ISP's relay…), with AUTH and STARTTLS/implicit TLS | You have a mail provider and want it to handle reputation, SPF, and downstream routing |
| **Direct** | Per recipient domain: MX records are resolved, sorted by preference, and tried in order (failover across up to 5 hosts) — no upstream provider needed | You want true self-hosted delivery straight to the internet, domain by domain |

Both modes sign outgoing mail with **DKIM** (per-domain RSA-2048 keys, `relaxed/relaxed`) before it
leaves the server, and every message is logged with full headers.

---

## Features

### SMTP Server
- **Async, high-performance listener** (aiosmtpd) — SMTPUTF8 and 8BITMIME advertised
- **SMTP AUTH (LOGIN and PLAIN)** — bcrypt-hashed credentials managed in the web UI, with per-credential send limits
- **Outlook-compatible AUTH LOGIN** — canonical SASL challenges, so strict clients (Outlook) authenticate reliably
- **STARTTLS support** on the listener — point it at your own PEM certificate/key
- **Live security controls** — `require_auth` is re-read from the database on every `MAIL FROM`; no restart needed
- **IP allowlist** — restrict connecting source IPs (CIDR, IPv4 + IPv6, fails closed on bad entries)
- **Sender-domain allowlist** — restrict which sender domains may hand off mail
- **Configurable limits** — message size (25 MB default), recipients per message (100), global hourly rate (1000)
- **Configurable identity** — HELO/banner hostname for better deliverability

### Delivery & Queue
- **Relay mode** — one upstream smarthost, with authentication and STARTTLS or implicit TLS
- **Direct mode** — deliver straight to each recipient domain's MX hosts, in preference order, with automatic failover; NULL MX and nonexistent domains fail permanently, never pointlessly retried
- **DKIM signing at delivery time** — per-domain RSA-2048 keys, encrypted at rest, DNS TXT record shown in the UI for publication
- **Durable queue** — PostgreSQL-backed, with automatic retry and linear backoff (interval × attempt count, 3 attempts by default)
- **Failed-mail retention** — failed messages are held until manually retried or deleted
- **Restart-safe** — messages stranded mid-delivery by a restart are automatically requeued on boot
- **Bounded delivery concurrency** — 10 parallel delivery slots keep the process healthy under load

### Web Management Interface
- **Dashboard** — real-time stats, 7-day chart, recent emails, server start/stop/restart controls
- **Configuration page** — edit every setting from the browser; changes apply without touching files
- **Allowed Domains / SMTP Credentials / DKIM Keys** — full CRUD, Operator and above
- **User management** — five-tier role-based access control (see [User Roles](#user-roles))
- **Email logs** — searchable, filterable, paginated, with a full header detail view
- **Queue manager** — view pending/failed, retry individually or all at once, delete with confirmation
- **Microsoft 365 sign-in** — optional Entra ID (OIDC) for the web UI; a local break-glass admin login is always available
- **Permission-filtered sidebar** — each user sees only what their role can actually use

### Reliability & Operations
- **Single-process design** — one thing to run, monitor, and restart; no IPC to break
- **No page hangs** — delivery threads release their database session before any network I/O
- **Connection pooling** — pooled PostgreSQL connections with pre-ping health checks and bounded waits
- **Rotating logs** — size-rotated app log and optional debug log on the data volume
- **Health-checked containers** — the compose stack ships with a web healthcheck (`/login` probe); Portainer stack supported
- **Automatic schema migrations** — the database upgrades itself on start

---

## Quick Start

### Docker Compose (recommended)

```bash
git clone https://github.com/cegautreau-a11y/smtp-relay-beta.git
cd smtp-relay-beta
cp .env.example .env
# Edit .env — SECRET_KEY and POSTGRES_PASSWORD are required
docker compose up -d --build
```

Then open **http://localhost:8025**.
The stack includes a `postgres:16-alpine` service with a healthcheck; all state lives in the
`pg-data` volume. To change ports, remap the **host** side with `WEB_PORT_HOST` / `SMTP_PORT_HOST`
— the in-container ports (8025 / 2525) are pinned.

### Windows (bare metal)

```powershell
# 1. Open a terminal in the project folder
cd C:\path\to\smtp-relay

# 2. Point the app at your PostgreSQL server
$env:DATABASE_URL = "postgresql+psycopg2://smtp:password@localhost:5432/smtp_relay"

# 3. Run the launcher (installs missing dependencies automatically)
python.exe run.py
```

On **Linux without Docker**, run the same way with `python3 run.py` — but note the project is
packaged and supported for Linux via Docker (gunicorn serves the web UI there; on Windows the Flask
development server is used with a logged warning — fine for single-server use, but put it behind a
reverse proxy for serious production traffic).

### First boot

The application creates the schema, then creates the initial **`admin`** account (Super Admin) and
generates a **random password**, printed **once** to the console/log (look for the `WARNING` line).
Log in at `http://localhost:8025` as `admin` with that password.

> [!WARNING]
> **The initial admin password is shown only once — save it and change it after first login!**

---

## Configuration

**The database is the configuration store.** Environment variables seed it on **first boot** into an
empty database; after that the database owns the configuration and edits happen in the web UI.
To re-apply environment variables, empty the database. Three are read on **every** boot:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | **Required.** e.g. `postgresql+psycopg2://smtp:password@localhost:5432/smtp_relay` |
| `SECRET_KEY` | Resolved every boot (the stored key wins; a changed env value logs a warning and never rotates) |
| `LOG_FILE` | Log destination (empty = stdout only) |

Notable first-boot seeds (see [.env.example](.env.example) for the full list):

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | random | Session/CSRF key **and** the encryption root for stored secrets (upstream passwords, DKIM keys) |
| `SMTP_LISTEN_PORT` | `2525` | SMTP listener port |
| `WEB_PORT` | `8025` | Web UI port |
| `REQUIRE_AUTH` | `true` | Require SMTP AUTH before accepting mail |
| `DELIVERY_MODE` | `relay` | `relay` (smarthost) or `direct` (recipient MX) |
| `RELAY_HOST` / `RELAY_PORT` | `smtp.example.com` / `25` | Upstream smarthost for relay mode |
| `MAX_MESSAGE_SIZE` | `26214400` | Max message size in bytes (25 MB) |
| `GLOBAL_RATE_LIMIT` | `1000` | Messages per hour, all credentials |
| `QUEUE_RETRY_INTERVAL` / `QUEUE_MAX_RETRIES` | `300` / `3` | Queue retry policy |

Everything else — domains, credentials, DKIM, users — is managed in the web interface.
The complete reference lives in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## Architecture

```mermaid
flowchart LR
    A["Your applications<br/>(SMTP clients)"] -->|"SMTP :2525<br/>AUTH · STARTTLS"| B["SMTP Server<br/>(aiosmtpd)"]
    B --> C["Checks<br/>auth · domains · IPs<br/>rate limits"]
    C --> Q[("Queue<br/>PostgreSQL<br/>retry + backoff")]
    Q --> D["DKIM<br/>signing"]
    D -->|"relay mode"| R["Upstream<br/>smarthost"]
    D -->|"direct mode"| M["Recipient<br/>MX hosts"]
    Q -.- L["Email logs<br/>(PostgreSQL)"]
    W["Web UI (Flask)<br/>:8025"] --- PG[("PostgreSQL")]
    B --- PG
    Q --- PG
```

**Key design facts:**

- **One process, one database.** Web UI, SMTP engine, and queue processor share PostgreSQL — the
  database *is* the message bus; there is no inter-process communication. PostgreSQL 12+ is the only
  supported backend.
- **DB-wins configuration.** Environment variables seed `relay_config` once on first boot; the
  database is runtime truth afterwards, read fresh per SMTP command.
- **3-phase delivery.** Each message is snapshotted in a short database context, the session is
  released *before* any network I/O, and results are written back in a fresh context — the web UI
  never hangs behind a slow upstream.
- **Secrets are encrypted at rest.** Upstream/direct auth passwords and DKIM private keys are stored
  as Fernet-encrypted envelopes keyed from `web_secret_key`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design.

---

## Connecting Applications

Point any SMTP client at the server — host, port `2525`, and an SMTP credential created in the web UI.

### Python (smtplib)

```python
import smtplib
from email.mime.text import MIMEText

msg = MIMEText("Hello from the relay!")
msg["Subject"] = "Test Email"
msg["From"] = "sender@yourdomain.com"
msg["To"] = "recipient@example.com"

with smtplib.SMTP("your-server-ip", 2525) as smtp:
    smtp.login("your-credential", "your-password")
    smtp.send_message(msg)
```

### Node.js (Nodemailer)

```javascript
const nodemailer = require("nodemailer");
const transporter = nodemailer.createTransport({
    host: "your-server-ip",
    port: 2525,
    auth: { user: "your-credential", pass: "your-password" },
});
transporter.sendMail({
    from: "sender@yourdomain.com",
    to: "recipient@example.com",
    subject: "Test",
    text: "Hello from the relay!",
});
```

### Generic / WordPress / PHP

| Setting | Value |
|---------|-------|
| Server | Your server IP |
| Port | `2525` |
| Auth | LOGIN or PLAIN |
| Username | Your SMTP credential |
| Password | Your credential password |
| TLS | Optional STARTTLS (if configured) |

---

## User Roles

Five tiers, least to most privileged. Higher roles inherit everything below them.

| Role | Can do |
|---|---|
| **Viewer** | Read-only: dashboard, email logs, queue |
| **Queue Manager** | Everything above + full retry-queue control: retry entries, Retry All Failed, delete entries, Delete All Failed — nothing else |
| **Operator** | Everything above + manage allowed domains, SMTP credentials, and DKIM keys |
| **Admin** | Everything above + edit configuration, start/stop/restart the SMTP server, manage non-admin users |
| **Super Admin** | Everything above + manage Admin and Super Admin accounts (the initial `admin` account) |

Details: [docs/USER_ROLES.md](docs/USER_ROLES.md).

---

## Deployment

| Target | Guide |
|---|---|
| Docker Compose | [docs/INSTALLATION.md](docs/INSTALLATION.md) (Step 8) — stack includes PostgreSQL |
| Portainer | [docs/PORTAINER.md](docs/PORTAINER.md) — web-editor or repository method |
| Windows service | Below — NSSM or Task Scheduler |
| Reverse proxy (HTTPS) | [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md) — nginx, Caddy, Cloudflare |

### Running as a Windows Service

Use **NSSM** (Non-Sucking Service Manager):

```bat
nssm install SmtpRelay "C:\Python314\python.exe" "C:\smtp-relay\run.py"
nssm start SmtpRelay
```

Or use Task Scheduler with **"Run whether user is logged on or not"**.
Full instructions: [docs/INSTALLATION.md](docs/INSTALLATION.md).

> [!NOTE]
> On Windows the web UI runs on the Flask development server (gunicorn is Linux/Docker-only);
> the SMTP engine and queue are unaffected.

### Behind a reverse proxy

Run the UI behind HTTPS and set the trio in the web UI: `web_secure_cookies=true`,
`web_trust_proxy=true`, and an exact `azure_redirect_uri` (required for Microsoft sign-in).
Step-by-step: [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md).

---

## Upgrading

### From v4.4.x to v4.5.1

Pull and rebuild/restart (`docker compose up -d --build` or replace the source files). No
configuration changes; existing users keep their roles. Recent highlights:

- **v4.5.1** — the sidebar shows only what each role can use; the domains/credentials/DKIM pages are
  blocked server-side below Operator (Viewer and Queue Manager accounts lose their read-only view — intended)
- **v4.5.0** — new **Queue Manager** role for helpdesk staff who only un-stick stuck mail
- **v4.4.4** — fixed Outlook hanging during AUTH LOGIN
- **v4.4.3** — the app log lands on the data volume (`/app/data/relay.log`) and the Debug Logging
  toggle actually works
- **v4.4.2** — fixed Microsoft sign-in (MSAL scope validation)

### From v4.2.1 to v4.3.0 (breaking)

1. Replace all source files with v4.3.0+ versions
2. Set `DATABASE_URL` — it is now **required**; PostgreSQL is the **only** backend
3. Start the application — the database keeps all data and becomes the only configuration store
4. `config.json`, `DATABASE_PATH`, and `CONFIG_PATH` are gone. SQLite installs have **no** in-repo
   migration path — move to PostgreSQL with external tooling before upgrading

### Older releases

See the [CHANGELOG](CHANGELOG.md) for the full history.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Startup error: PostgreSQL required | Set `DATABASE_URL` to your PostgreSQL server |
| Docker healthcheck failing | Don't change the in-container `WEB_PORT` — remap the host side with `WEB_PORT_HOST` |
| Port already in use | Change the port in the web UI (Configuration — restart required) |
| Can't connect from other machines | Check firewall rules; ensure the host binding is `0.0.0.0` |
| STARTTLS errors | Set `banner_hostname` (`BANNER_HOSTNAME`) to a valid FQDN |
| Authentication required (SMTP) | Create a credential in the web UI (Credentials page) |
| Outlook freezes when sending | Upgrade to v4.4.4+ (canonical AUTH LOGIN challenges) |
| Entra ID sign-in fails | Set an exact `azure_redirect_uri` and restart — see [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md) |

---

## Security Recommendations

1. **Change the initial admin password** immediately (it is printed to the console/log on first boot)
2. **Set a strong, long random `SECRET_KEY`** (it seeds the database-stored secret key on first boot and encrypts stored secrets)
3. **Keep `require_auth` on** to prevent open-relay abuse (it's on by default)
4. **Add allowed domains** to restrict which senders may relay
5. **Set `allowed_source_ips`** if only known hosts connect (bad entries fail closed)
6. **Use TLS** — STARTTLS on the listener, and STARTTLS/implicit TLS toward upstreams
7. **Run the web UI behind a reverse proxy with HTTPS** in production — see [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md)

---

## Requirements

- **Python 3.10+** (3.14 supported; the Docker image uses 3.12-slim)
- **PostgreSQL 12+** — the only supported database
- Windows Server 2016+ / Windows 10/11, or Linux via Docker

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Step-by-step setup guide |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Complete configuration reference |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design overview |
| [docs/USER_ROLES.md](docs/USER_ROLES.md) | Role-based permissions |
| [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md) | HTTPS / reverse proxy setup |
| [docs/PORTAINER.md](docs/PORTAINER.md) | Deploy on Docker via Portainer |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

---

## Project Layout

```text
smtp-relay/
├── run.py                       # Entry point & bootstrap
├── app.py                       # Flask web application
├── smtp_server.py               # SMTP server & delivery engine
├── models.py                    # Database models
├── auth_oauth.py                # Microsoft Entra ID (OIDC) sign-in
├── crypto_utils.py              # Fernet secret encryption (ENC1)
├── requirements.txt             # Pinned Python dependencies
├── Dockerfile                   # Container image (python:3.12-slim)
├── docker-compose.yml           # Docker deployment (includes postgres)
├── docker-compose.portainer.yml # Portainer variant (no build:)
├── .env.example                 # Environment variable template
├── tests/e2e_smoke.py           # End-to-end smoke suite
├── docs/
│   ├── ARCHITECTURE.md          # System design
│   ├── CONFIGURATION.md         # Full config reference
│   ├── INSTALLATION.md          # Setup guide
│   ├── USER_ROLES.md            # RBAC guide
│   ├── REVERSE_PROXY.md         # HTTPS / reverse proxy
│   └── PORTAINER.md             # Portainer deployment
├── static/css/style.css         # Stylesheet (self-hosted Inter font)
└── templates/                   # Jinja2 templates
```

---

## License

[MIT](LICENSE) — free for personal and commercial use.
