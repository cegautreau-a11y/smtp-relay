# SMTP Mail Relay v2.1.0

A full-featured SMTP mail relay server with a modern web management interface.
Designed to run on **Windows Server** with just **Python 3.11** — no other
software required.

---

## Quick Start (Windows)

### 1. Extract the zip and open a terminal in the folder

```
cd C:\path\to\smtp-relay
```

### 2. Run the launcher

```
python.exe run.py
```

On the **first run** it will automatically `pip install` all dependencies.
A default `config.json` will be created if one doesn't exist.

### 3. Open the web interface

Navigate to **http://localhost:8025** and log in:

| Field    | Value   |
|----------|---------|
| Username | `admin` |
| Password | `admin` |

> **⚠️  Change the default password immediately after first login!**

### 4. Configure

1. Edit `config.json` in a text editor **before** starting, or
2. Use the **Configuration** page in the web UI at runtime.

---

## Configuration — config.json

All settings live in a single `config.json` file next to `run.py`.
User accounts are **not** stored in this file — they are managed
exclusively through the web interface.

```jsonc
{
    "web": {
        "host": "0.0.0.0",          // Web UI bind address
        "port": 8025,                // Web UI port
        "secret_key": "CHANGE-ME"   // Random secret for sessions
    },
    "smtp_listener": {
        "host": "0.0.0.0",          // SMTP bind address
        "port": 2525,                // SMTP listen port
        "banner_hostname": "relay.local",  // 220 banner name
        "require_auth": true,        // Require SMTP AUTH
        "enable_tls": false,         // TLS on listener
        "tls_cert_path": "",         // Path to cert.pem
        "tls_key_path": ""           // Path to key.pem
    },
    "relay_destination": {
        "host": "smtp.example.com",  // Upstream SMTP server
        "port": 587,                 // Upstream port
        "use_tls": false,            // Implicit TLS (port 465)
        "use_starttls": true,        // STARTTLS (port 587)
        "auth_user": "",             // Upstream login
        "auth_password": "",         // Upstream password
        "helo_hostname": "myserver.example.com"
        // ↑ The server name used in HELO/EHLO when sending.
        //   Should be a valid FQDN for best deliverability.
    },
    "limits": {
        "max_message_size_bytes": 26214400,
        "max_recipients_per_message": 100,
        "global_rate_limit_per_hour": 1000,
        "allowed_source_ips": []     // Empty = allow all
    },
    "queue": {
        "retry_interval_seconds": 300,
        "max_retries": 3
    },
    "logging": {
        "level": "INFO",             // DEBUG, INFO, WARNING, ERROR
        "log_file": "",              // Empty = console only
        "log_retention_days": 30
    },
    "database": {
        "path": "smtp_relay.db"      // SQLite file path
    }
}
```

### Key settings explained

| Setting | Where | Purpose |
|---------|-------|---------|
| `web.port` | config.json | Port the web management UI listens on |
| `smtp_listener.port` | config.json | Port the SMTP relay listens on for incoming mail |
| `relay_destination.host` | config.json + Web UI | Upstream server to forward mail to |
| `relay_destination.helo_hostname` | config.json + Web UI | **Server name** used in SMTP HELO/EHLO when sending |
| `smtp_listener.banner_hostname` | config.json + Web UI | Name shown in the 220 banner to connecting clients |
| `smtp_listener.require_auth` | config.json + Web UI | Whether clients must authenticate |
| Allowed domains | Web UI only | Which sender domains can use the relay |
| SMTP credentials | Web UI only | Auth accounts for connecting mail clients |
| Web users | Web UI only | Login accounts for the management interface |

---

## Features

### SMTP Relay Engine
- Async SMTP server (aiosmtpd) — high performance, pure Python
- **Configurable sending server name** (HELO/EHLO hostname)
- Configurable destination relay (Gmail, O365, SendGrid, Postfix, etc.)
- Domain allowlist — restrict which sender domains can relay
- SMTP AUTH (LOGIN / PLAIN) — require client authentication
- Per-credential and global rate limiting
- Email queue with automatic retry and exponential backoff
- **Failed email retention** — messages that fail after all retries are held in the queue until manually retried or deleted; never silently discarded
- TLS / STARTTLS support (listener and upstream)
- IP allowlist — restrict connecting source IPs
- Configurable message size and recipient limits

### Web Management Interface
- **Dashboard** — real-time stats, 7-day chart, recent emails, server controls
- **Configuration** — edit all relay settings from the browser
- **Allowed Domains** — add / enable / disable / delete sender domains
- **SMTP Credentials** — create auth accounts with per-credential rate limits
- **Users** — create admin and regular accounts for the web UI
- **Email Logs** — searchable, filterable, paginated log viewer with full email header detail view
- **Queue** — view pending / failed deliveries, retry individual or all failed messages, delete individual or all failed
- **Profile** — change your own password and email
- **Server Control** — start / stop / restart SMTP from the dashboard
- Responsive design — works on desktop, tablet, mobile

### Email Header Details (v2.0)
- Click the **Details** button on any email log entry to view a rich modal dialog
- **Message Info** — status, timestamp, subject, sender, recipients, size, Message-ID
- **Relay Info** — SMTP credential used, source IP, relay server, retry count
- **Email Headers** — full raw email headers (From, To, Date, MIME-Version, Content-Type, X-Mailer, etc.) displayed in a dark-themed scrollable code block
- Headers are captured and stored automatically when the relay processes each message
- Modal supports Escape key and overlay click to dismiss

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  SMTP Mail Relay v2.1.0                  │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐  │
│  │  SMTP Server  │───▶│  Email Queue  │───▶│  Upstream │  │
│  │  (aiosmtpd)   │    │  (SQLite)     │    │  Relay    │  │
│  │  Port 2525    │    │  + Retry      │    │  Server   │  │
│  └──────────────┘    └──────────────┘    └───────────┘  │
│         │                    │                           │
│         ▼                    ▼                           │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │  Auth Check   │    │  Email Logs   │                  │
│  │  Domain Check │    │  (SQLite)     │                  │
│  │  Rate Limit   │    │  + Headers    │                  │
│  │  IP Filter    │    └──────────────┘                   │
│  └──────────────┘           │                           │
│                              ▼                           │
│                    ┌──────────────────┐                  │
│                    │  Web Interface    │                  │
│                    │  (Flask)          │                  │
│                    │  Port 8025        │                  │
│                    └──────────────────┘                  │
└─────────────────────────────────────────────────────────┘
```

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
    smtp.login("your-smtp-credential", "your-password")
    smtp.send_message(msg)
```

### Node.js (Nodemailer)

```javascript
const nodemailer = require("nodemailer");
const transporter = nodemailer.createTransport({
    host: "your-server-ip",
    port: 2525,
    auth: { user: "your-smtp-credential", pass: "your-password" },
});
transporter.sendMail({
    from: "sender@yourdomain.com",
    to: "recipient@example.com",
    subject: "Test",
    text: "Hello from the relay!",
});
```

### WordPress / PHP / Generic

| Setting  | Value                         |
|----------|-------------------------------|
| Server   | Your server IP or hostname    |
| Port     | `2525` (configurable)         |
| Auth     | LOGIN / PLAIN                 |
| Username | Your SMTP credential username |
| Password | Your SMTP credential password |
| TLS      | Optional (if configured)      |

---

## Running as a Windows Service

To keep the relay running after you log out, you can use **NSSM**
(Non-Sucking Service Manager) or the built-in `sc` command:

```
nssm install SmtpRelay "C:\Python311\python.exe" "C:\smtp-relay\run.py"
nssm start SmtpRelay
```

Or simply run it in a persistent terminal / scheduled task.

---

## File Structure

```
smtp-relay\
├── run.py                  ← Double-click to start
├── config.json             ← All settings (edit this)
├── requirements.txt        ← Python packages
├── app.py                  ← Flask web application
├── models.py               ← Database models
├── smtp_server.py          ← SMTP relay engine
├── smtp_relay.db           ← SQLite database (created on first run)
├── README.md               ← This file
├── CHANGELOG.md            ← Version history
├── LICENSE                 ← MIT License
├── docs\
│   ├── ARCHITECTURE.md     ← System design overview
│   ├── CONFIGURATION.md    ← Full config.json reference
│   ├── INSTALLATION.md     ← Step-by-step setup guide
│   └── USER_ROLES.md       ← Role-based permissions guide
├── static\
│   └── css\
│       └── style.css
└── templates\
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── config.html
    ├── domains.html
    ├── credentials.html
    ├── users.html
    ├── logs.html
    ├── queue.html
    ├── profile.html
    ├── error.html
    └── error_standalone.html
```

---

## Upgrading

### From v1.0 to v2.1.0

1. Replace all source files with the v2.1.0 versions
2. Keep your existing `config.json` and `smtp_relay.db` — they are fully compatible
3. Start the application — the database is automatically migrated on startup
4. The new `raw_headers` column is added to `email_logs` automatically
5. Existing log entries will show "No headers available" in the detail modal; new emails will capture full headers
6. Failed queue entries are now retained until manually retried or deleted — they are no longer auto-purged
7. New "Retry All Failed" button available on the Queue page

No manual database changes are required.

### From v2.0.x to v2.1.0

1. Replace all source files with the v2.1.0 versions
2. No database or config changes required
3. New "Retry All Failed" button on the Queue page lets you requeue all failed messages at once

---

## Security Recommendations

1. **Change the default admin password** immediately
2. **Change `secret_key`** in config.json to a long random string
3. **Enable `require_auth`** to prevent open relay
4. **Add allowed domains** to restrict sender addresses
5. **Set `allowed_source_ips`** if only known hosts should connect
6. **Use TLS** for both listener and upstream connections
7. **Set rate limits** on SMTP credentials
8. Run behind a reverse proxy with HTTPS for the web UI in production
9. Review email logs regularly for suspicious activity

---

## Requirements

- **Python 3.11+** (Windows or Linux)
- No other software needed — all dependencies install via pip automatically

---

## License

MIT License — free for personal and commercial use.