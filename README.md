# SMTP Mail Relay

A lightweight, self-hosted SMTP mail relay with a full web management interface. Built for Windows Server with Python, it accepts mail from your applications and forwards it to an upstream SMTP server (Exchange, Microsoft 365, Google Workspace, Amazon SES, etc.).

**Designed and built by Christopher McGrath**

---

## Features

- **SMTP Relay Server** — Async inbound listener (aiosmtpd) that accepts mail and forwards to your upstream server
- **Web Dashboard** — Real-time statistics, 7-day volume chart, recent email activity, live SMTP status indicator
- **Role-Based Access Control** — Four-tier permission system: Super Admin, Admin, Operator, Viewer
- **SMTP Authentication** — Per-credential auth with bcrypt hashing and individual rate limits
- **Sender Domain Allowlist** — Restrict which sender domains can use the relay
- **Email Queue** — Automatic retry with configurable intervals for failed deliveries
- **Full Audit Trail** — Searchable email logs with status tracking for every message
- **Live Configuration** — Edit settings through the web UI; saved to both database and config.json
- **TLS/STARTTLS** — Optional TLS on the listener and STARTTLS for upstream connections
- **IP Allowlisting** — Restrict which source IPs can connect
- **Auto-Dependency Install** — First-run automatic `pip install` of all requirements
- **Windows Native** — No Unix signals or fork; runs as a foreground process or Windows service
- **Python 3.11–3.14** — Tested and compatible with the latest Python releases

---

## Quick Start

### 1. Clone the Repository

```powershell
git clone https://github.com/cegautreau-a11y/smtp-relay.git
cd smtp-relay
```

### 2. Create Your Configuration

```powershell
copy config.json.example config.json
```

Open `config.json` in a text editor and set the three required values:

#### a) Secret Key

Generate a random secret key:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste it into `config.json`:

```json
"web": {
    "host": "0.0.0.0",
    "port": 8025,
    "secret_key": "your-generated-64-char-hex-string"
}
```

#### b) SMTP Listener Host

Set the bind address for the SMTP server. Use `0.0.0.0` to accept connections from any interface, or your server's specific IP:

```json
"smtp_listener": {
    "host": "0.0.0.0",
    "port": 2525,
    "banner_hostname": "relay.yourdomain.com",
    "require_auth": true
}
```

#### c) Relay Destination

Configure your upstream SMTP server:

```json
"relay_destination": {
    "host": "smtp.office365.com",
    "port": 587,
    "use_tls": false,
    "use_starttls": true,
    "auth_user": "relay@yourdomain.com",
    "auth_password": "your-app-password",
    "helo_hostname": "mailrelay.yourdomain.com"
}
```

### 3. Run

```powershell
python run.py
```

On first run, dependencies are installed automatically. The startup banner shows all connection details:

```
============================================================
  SMTP Mail Relay
  Designed and built by Christopher McGrath
============================================================
  Web Interface : http://0.0.0.0:8025
  SMTP Listener : 0.0.0.0:2525
  Relay Target  : smtp.office365.com:587
  HELO Hostname : mailrelay.yourdomain.com
  Default Login : admin / admin
============================================================
```

### 4. Log In

Open `http://YOUR-SERVER-IP:8025` in your browser.

- **Username:** `admin`
- **Password:** `admin`

> ⚠️ **Change the default password immediately** via the Profile page.

---

## Common Upstream Configurations

| Provider | Host | Port | TLS | STARTTLS |
|---|---|---|---|---|
| Microsoft 365 | `smtp.office365.com` | 587 | No | Yes |
| Google Workspace | `smtp-relay.gmail.com` | 587 | No | Yes |
| Amazon SES | `email-smtp.us-east-1.amazonaws.com` | 587 | No | Yes |
| On-premises Exchange | `mail.yourdomain.com` | 25 | No | Optional |

---

## User Roles

| Role | Access Level |
|---|---|
| **Viewer** | Read-only dashboard, logs, and queue |
| **Operator** | Manage domains, SMTP credentials, and queue |
| **Admin** | Full configuration, user management, server controls |
| **Super Admin** | Manage all users including other Admins |

See [docs/USER_ROLES.md](docs/USER_ROLES.md) for the full permission matrix.

---

## Project Structure

```
smtp-relay/
├── run.py                  # Application launcher (entry point)
├── app.py                  # Flask web application & routes
├── models.py               # Database models & Role definitions
├── smtp_server.py          # SMTP server, handler & queue processor
├── config.json.example     # Configuration template (copy to config.json)
├── requirements.txt        # Python dependencies
├── templates/
│   ├── base.html           # Shared layout with sidebar & header
│   ├── login.html          # Authentication page
│   ├── dashboard.html      # Statistics & charts
│   ├── logs.html           # Email log viewer
│   ├── queue.html          # Queue management
│   ├── domains.html        # Domain allowlist
│   ├── credentials.html    # SMTP credential management
│   ├── config.html         # Configuration editor
│   ├── users.html          # User management
│   ├── profile.html        # User profile & password change
│   ├── error.html          # Error page (authenticated)
│   └── error_standalone.html  # Error page (unauthenticated)
├── static/
│   └── css/
│       └── style.css       # Complete responsive stylesheet
├── docs/
│   ├── INSTALLATION.md     # Detailed setup guide
│   ├── CONFIGURATION.md    # Full config.json reference
│   ├── USER_ROLES.md       # Role permissions documentation
│   └── ARCHITECTURE.md     # System design overview
├── CHANGELOG.md            # Version history
├── CONTRIBUTING.md         # Contributor guidelines
├── LICENSE                 # MIT License
└── .gitignore
```

---

## Running as a Windows Service

For production, use [NSSM](https://nssm.cc/) to run the relay as a Windows service:

```powershell
nssm install SMTPRelay "C:\Python314\python.exe" "C:\smtp-relay\run.py"
nssm set SMTPRelay AppDirectory "C:\smtp-relay"
nssm start SMTPRelay
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed service setup instructions.

---

## Firewall Configuration

Open the required ports in Windows Firewall:

```powershell
netsh advfirewall firewall add rule name="SMTP Relay - SMTP" dir=in action=allow protocol=tcp localport=2525
netsh advfirewall firewall add rule name="SMTP Relay - Web" dir=in action=allow protocol=tcp localport=8025
```

---

## Documentation

| Document | Description |
|---|---|
| [Installation Guide](docs/INSTALLATION.md) | Step-by-step setup, firewall, service configuration, troubleshooting |
| [Configuration Reference](docs/CONFIGURATION.md) | Complete config.json documentation with examples |
| [User Roles](docs/USER_ROLES.md) | Role hierarchy, permission matrix, assignment rules |
| [Architecture](docs/ARCHITECTURE.md) | System design, mail flow, component overview, technology stack |
| [Changelog](CHANGELOG.md) | Version history and release notes |

---

## Requirements

- **Python 3.11+** (tested through 3.14)
- **Windows Server 2016+** (also works on Windows 10/11 and Linux)
- No external database server required (uses SQLite)

### Python Dependencies

| Package | Purpose |
|---|---|
| Flask 3.1 | Web framework |
| Flask-Login 0.6 | Session authentication |
| Flask-SQLAlchemy 3.1 | Database ORM |
| aiosmtpd 1.4 | Async SMTP server |
| bcrypt 4.2 | Password hashing |
| Werkzeug 3.1 | WSGI utilities |
| SQLAlchemy 2.0 | Database toolkit |

All dependencies are installed automatically on first run, or manually with:

```powershell
pip install -r requirements.txt
```

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Author

**Christopher McGrath**