# SMTP Mail Relay

A full-featured SMTP mail relay server with a modern web management interface. Runs on **Windows Server** or **Linux/Docker** with just **Python 3.10+** and a **PostgreSQL** database — PostgreSQL is the only supported backend (v4.3.0+).

---

## Quick Start

```powershell
# 1. Open terminal in the folder
cd C:\path\to\smtp-relay

# 2. Point the relay at your PostgreSQL server
$env:DATABASE_URL = "postgresql+psycopg2://smtp:password@localhost:5432/smtp_relay"

# 3. Run the launcher
python.exe run.py
```

On first run, dependencies are installed automatically. On **first boot** the application creates the initial `admin` account and generates a **random password**, which is printed once to the console/log (look for the `WARNING` line in the first-boot output). Navigate to **http://localhost:8025** and log in as `admin` with that generated password.

> ⚠️ **The initial admin password is shown only once — save it and change it after first login!**

### Web Server

On **Linux and Docker** the web interface is automatically served by **gunicorn** (production WSGI server, single worker, threaded — `WEB_THREADS` request threads, default `4`). On **Windows** gunicorn is not available, so the app falls back to the Flask development server and logs a warning (`gunicorn not available on this platform …`) — acceptable for single-server use, but put it behind a reverse proxy (IIS, nginx) for serious production traffic.

---

## Features

### SMTP Relay Engine
- **Async SMTP server** (aiosmtpd) — high performance, pure Python
- **Configurable HELO hostname** — control your server identity for better deliverability
- **Domain allowlist** — restrict which sender domains can relay
- **SMTP AUTH** (LOGIN) — require client authentication
- **Per-credential rate limiting** — control sending limits per user
- **Email queue** with automatic retry and linear backoff (retry interval x attempt count)
- **Failed email retention** — failed messages held until manually retried or deleted
- **TLS/STARTTLS support** — for both listener and upstream connections
- **IP allowlist** — restrict connecting source IPs
- **Configurable limits** — message size, recipients, global rate
- **DKIM signing** — per-domain RSA-2048 keys, signed at delivery time, DNS TXT record shown for publication
- **Direct delivery mode** — deliver straight to each recipient domain's MX hosts without an upstream relay
- **Microsoft 365 sign-in** — optional Entra ID OIDC for the web UI; local break-glass admin login always available

### Web Management Interface
- **Dashboard** — real-time stats, 7-day chart, recent emails, server controls
- **Configuration** — edit all relay settings from the browser
- **Allowed Domains** — manage permitted sender domains
- **SMTP Credentials** — create auth accounts with per-credential rate limits
- **Users** — role-based access control (Super Admin, Admin, Operator, Viewer)
- **Email Logs** — searchable, filterable, paginated with full header detail view
- **Queue** — view pending/failed, retry individual or all failed, delete with confirmation
- **Profile** — change password and email
- **Server Control** — start/stop/restart SMTP from dashboard
- **Version badge** — version number displayed in sidebar footer

### Web Interface Redesign (v3.0.0)
- **Complete modern UI** — cleaner design with improved visual hierarchy
- **Enhanced color palette** — refined indigo/blue primary colors with better contrast
- **Improved sidebar** — smoother hover states and active indicator bar
- **Better stat cards** — larger values with gradient icon backgrounds
- **Refined dark mode** — improved contrast and color balance
- **Enhanced animations** — smoother page transitions and hover effects
- **Improved tables** — better zebra striping and hover states
- **Button refinements** — better hover states and visual feedback

### Queue UX Improvements (v3.0.1)
- **Queue cancellation confirmations** — delete buttons prompt for confirmation to prevent accidental deletions
- **Processing section notice** — informational alert explaining why actively processing messages cannot be cancelled

### Reliability
- **No page hanging** — delivery threads release DB before network calls
- **Connection pooling** — pooled PostgreSQL connections with pre-ping health checks and bounded waits
- **Python 3.14 compatible** — dependencies are checked at startup and installed if missing
- **Automatic migrations** — schema updates on upgrade

---

## Configuration

The **database is the configuration store**. Environment variables (or the bootstrap config) seed it on **first boot** into an empty database; afterwards the database owns the configuration and edits happen in the web UI. To re-apply environment variables, empty the database.

The one variable read on **every** boot is connection info:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | **Required.** e.g. `postgresql+psycopg2://smtp:password@localhost:5432/smtp_relay` |

Other variables (`WEB_PORT`, `RELAY_HOST`, `REQUIRE_AUTH`, …) are first-boot seeds — see [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the complete reference.

### Web UI Settings
These are managed in the web interface:
- Allowed domains
- SMTP credentials
- Web users and roles

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 SMTP Mail Relay v4.5.1                   │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │  SMTP Server │───▶│  Email Queue │───▶│  Upstream │ │
│  │  (aiosmtpd)  │    │(PostgreSQL)  │    │  Relay    │ │
│  │  Port 2525   │    │  + Retry     │    │  Server   │ │
│  └──────────────┘    └──────────────┘    └───────────┘ │
│         │                    │                           │
│         ▼                    ▼                           │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │  Auth/Domain │    │  Email Logs  │                   │
│  │  Rate Limit  │    │  + Headers  │                   │
│  └──────────────┘    └──────────────┘                   │
│                              ▼                           │
│                    ┌──────────────────┐                 │
│                    │  Web Interface   │                 │
│                    │  (Flask)         │                 │
│                    │  Port 8025       │                 │
│                    └──────────────────┘                 │
└─────────────────────────────────────────────────────────┘
```

All components run in a single Python process — no separate services.

---

## Connecting Applications

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
| Auth | LOGIN / PLAIN |
| Username | Your SMTP credential |
| Password | Your credential password |
| TLS | Optional (if configured) |

---

## Running as a Windows Service

Use **NSSM** (Non-Sucking Service Manager):

> **Note:** On Windows the web interface runs on the Flask development server (gunicorn is Linux/Docker-only); the SMTP engine and queue are unaffected.

```
nssm install SmtpRelay "C:\Python314\python.exe" "C:\smtp-relay\run.py"
nssm start SmtpRelay
```

Or use Task Scheduler with "Run whether user is logged on or not". See [docs/INSTALLATION.md](docs/INSTALLATION.md) for full instructions.

---

## File Structure

```
smtp-relay\
├── run.py                  # Entry point
├── requirements.txt        # Python packages
├── app.py                  # Flask web application
├── crypto_utils.py         # Fernet ENC1 secret encryption
├── models.py               # Database models
├── smtp_server.py          # SMTP relay engine
├── Dockerfile              # Container image (python:3.12-slim)
├── docker-compose.yml      # Docker deployment (includes postgres service)
├── docker-compose.portainer.yml  # Portainer variant (no build:)
├── README.md               # This file
├── CHANGELOG.md            # Version history
├── LICENSE                 # MIT License
├── docs\
│   ├── ARCHITECTURE.md     # System design
│   ├── CONFIGURATION.md    # Full config reference
│   ├── INSTALLATION.md     # Setup guide
│   ├── USER_ROLES.md       # RBAC guide
│   └── PORTAINER.md        # Portainer deployment
├── static\css\style.css    # Stylesheet (+ self-hosted Inter font)
└── templates\              # Jinja2 templates
```

---

## Upgrading

### From v4.2.1 to v4.3.0

1. Replace all source files with v4.3.0 versions
2. Set `DATABASE_URL` — it is now **required** (in v4.2.1 it was optional)
3. Start the application — the database keeps all data **and** is now the only configuration store. **Nothing migrates from `config.json`**: v4.3.0 does not read that file at all. Installs already on v4.2.1 *database mode* already hold their web settings and `web_secret_key` in PostgreSQL and carry over untouched; SQLite + `config.json` installs have **no** in-repo migration path (re-enter settings after the move — see the next line)
4. `config.json`, `DATABASE_PATH`, and `CONFIG_PATH` are gone; SQLite installs have **no** in-repo migration path — move to PostgreSQL with external tooling before upgrading

### From v4.3.0 to v4.4.0

Replace the source files (or `docker compose up -d --build`) and restart — no configuration changes required. Highlights: direct delivery now fails over across the recipient domain's MX hosts, queue rows stranded in `processing` by a restart are requeued on boot, the dashboard daily chart works again on PostgreSQL, and several web-UI/security fixes — see the [CHANGELOG](CHANGELOG.md). If you plan to put the web UI behind a reverse proxy, see [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md).

### From v4.4.x to v4.5.0

Pull and rebuild/restart. Adds the **Queue Manager** role (retry queue only — see [docs/USER_ROLES.md](docs/USER_ROLES.md)); Admins can assign it in User Management. v4.4.2 is **required for Microsoft sign-in** (v4.4.0's scope list tripped an MSAL validation error before Microsoft was ever contacted). v4.4.3 makes logging work as expected: the app log defaults to `/app/data/relay.log` (rotated 20 MB x 5) on the `smtp-data` volume, and the Configuration page's **Debug Logging** toggle now actually works (it previously did nothing — the `debug_logging` setting was never read back; when enabled it writes `data/smtp_debug.log`). Set `LOG_FILE=` (empty) to return to stdout-only. v4.4.4 fixes Outlook and other strict SASL clients hanging during AUTH LOGIN (the server now sends the canonical `Username:` / `Password:` challenges). Existing users keep their roles. See the [CHANGELOG](CHANGELOG.md).

### From v2.2.0 or later to v3.0.1

1. Replace all source files with v3.0.1 versions
2. Keep your existing `config.json` and `smtp_relay.db`
3. Start the application — database is auto-migrated if needed
4. No manual changes required

**What's new in v3.0.x:**
- Complete web interface redesign with modern UI
- Version badge displayed in sidebar footer
- Enhanced color palette and improved dark mode
- Better stat cards, tables, and animations
- Queue delete confirmations to prevent accidental deletions
- Processing section notice for active messages

**What's new in v3.0.1:**
- Queue cancellation confirmations
- Processing section notice

### From any version to v2.2.0

1. Replace all source files with v2.2.0 versions
2. Keep your existing `config.json` and `smtp_relay.db`
3. Start the application — database is auto-migrated
4. No manual changes required

**What's new in v2.2.0:**
- Fixed page hanging issues (delivery threads release DB before network calls)
- `message_id` column added to email logs
- Python 3.14 compatibility

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Startup error: PostgreSQL required | Set `DATABASE_URL` to your PostgreSQL server |
| Port already in use | Change the port in the web UI (Configuration — restart required) |
| Can't connect from other machines | Check firewall rules; ensure host is `0.0.0.0` |
| STARTTLS errors | Set `helo_hostname` to a valid FQDN |
| Authentication required | Create credentials in web UI |
| Page hangs / slow responses | Upgrade to v2.2.0 or later |
| Message-ID blank in logs | Upgrade to v2.2.0 or later |

---

## Security Recommendations

1. **Change the initial admin password** immediately (it is printed to the console/log on first boot)
2. **Set a strong `SECRET_KEY`** environment variable (it seeds the database-stored secret key on first boot)
3. **Enable `require_auth`** to prevent open relay
4. **Add allowed domains** to restrict senders
5. **Set `allowed_source_ips`** if only known hosts connect
6. **Use TLS** where possible
7. Run behind a reverse proxy with HTTPS in production — setup guide: [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md) (`web_secure_cookies=true` + `web_trust_proxy=true` + an exact `azure_redirect_uri`)

---

## Requirements

- **Python 3.10+** (3.14 supported)
- **PostgreSQL 12+** (the only supported database)
- Windows Server 2016+ / Windows 10/11, or Linux/Docker

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Step-by-step setup guide |
| [docs/PORTAINER.md](docs/PORTAINER.md) | Deploy on Docker via Portainer |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Complete configuration reference |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design overview |
| [docs/USER_ROLES.md](docs/USER_ROLES.md) | Role-based permissions |

---

## License

MIT License — free for personal and commercial use.
