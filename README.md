# SMTP Mail Relay

A full-featured SMTP mail relay server with a modern web management interface. Designed to run on **Windows Server** with just **Python 3.9+** — no other software required.

---

## Quick Start

```powershell
# 1. Open terminal in the folder
cd C:\path\to\smtp-relay

# 2. Run the launcher
python.exe run.py
```

On first run, dependencies are installed automatically. Navigate to **http://localhost:8025** and log in:

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin` |

> ⚠️ **Change the default password immediately after first login!**

---

## Features

### SMTP Relay Engine
- **Async SMTP server** (aiosmtpd) — high performance, pure Python
- **Configurable HELO hostname** — control your server identity for better deliverability
- **Domain allowlist** — restrict which sender domains can relay
- **SMTP AUTH** (LOGIN/PLAIN) — require client authentication
- **Per-credential rate limiting** — control sending limits per user
- **Email queue** with automatic retry and exponential backoff
- **Failed email retention** — failed messages held until manually retried or deleted
- **TLS/STARTTLS support** — for both listener and upstream connections
- **IP allowlist** — restrict connecting source IPs
- **Configurable limits** — message size, recipients, global rate

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
- **SQLite busy timeout** — 20-second timeout prevents indefinite hangs
- **Python 3.14 compatible** — packages auto-upgraded on startup
- **Automatic migrations** — schema updates on upgrade

---

## Configuration

All settings in `config.json`. Key options:

```jsonc
{
    "web": { "port": 8025, "secret_key": "CHANGE-ME" },
    "smtp_listener": { "port": 2525, "require_auth": true },
    "relay_destination": {
        "host": "smtp.example.com", "port": 587,
        "use_starttls": true, "helo_hostname": "mail.example.com"
    }
}
```

> 📖 See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the complete reference.

### Web UI Settings
These are managed in the web interface (not in config.json):
- Allowed domains
- SMTP credentials
- Web users and roles

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 SMTP Mail Relay v3.0.1                   │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │  SMTP Server │───▶│  Email Queue │───▶│  Upstream │ │
│  │  (aiosmtpd)  │    │  (SQLite)    │    │  Relay    │ │
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

```
nssm install SmtpRelay "C:\Python311\python.exe" "C:\smtp-relay\run.py"
nssm start SmtpRelay
```

Or use Task Scheduler with "Run whether user is logged on or not". See [docs/INSTALLATION.md](docs/INSTALLATION.md) for full instructions.

---

## File Structure

```
smtp-relay\
├── run.py                  # Entry point
├── config.json             # All settings
├── requirements.txt        # Python packages
├── app.py                  # Flask web application
├── models.py               # Database models
├── smtp_server.py          # SMTP relay engine
├── smtp_relay.db           # SQLite database
├── README.md               # This file
├── CHANGELOG.md            # Version history
├── LICENSE                 # MIT License
├── docs\
│   ├── ARCHITECTURE.md     # System design
│   ├── CONFIGURATION.md    # Full config reference
│   ├── INSTALLATION.md     # Setup guide
│   └── USER_ROLES.md      # RBAC guide
├── static\css\style.css
└── templates\              # Jinja2 templates
```

---

## Upgrading

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
| Port already in use | Change port in config.json |
| Can't connect from other machines | Check firewall rules; ensure host is `0.0.0.0` |
| STARTTLS errors | Set `helo_hostname` to a valid FQDN |
| Authentication required | Create credentials in web UI |
| Page hangs / slow responses | Upgrade to v2.2.0 or later |
| Message-ID blank in logs | Upgrade to v2.2.0 or later |

---

## Security Recommendations

1. **Change the default admin password** immediately
2. **Change `secret_key`** to a random string
3. **Enable `require_auth`** to prevent open relay
4. **Add allowed domains** to restrict senders
5. **Set `allowed_source_ips`** if only known hosts connect
6. **Use TLS** where possible
7. Run behind a reverse proxy with HTTPS in production

---

## Requirements

- **Python 3.9+** (3.14 supported)
- Windows Server 2016+ or Windows 10/11 (also runs on Linux)
- No other software needed

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Step-by-step setup guide |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Complete config.json reference |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design overview |
| [docs/USER_ROLES.md](docs/USER_ROLES.md) | Role-based permissions |

---

## License

MIT License — free for personal and commercial use.
