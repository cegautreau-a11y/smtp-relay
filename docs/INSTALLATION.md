# Installation Guide

This guide walks you through installing and running the SMTP Mail Relay v3.0.2 on a Windows Server.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Operating System** | Windows Server 2016 or later (also works on Windows 10/11 for testing) |
| **Python** | 3.9 or later (3.14 supported). Download from [python.org](https://www.python.org/downloads/) |
| **Network** | The server must be reachable on the SMTP listener port (default 2525) from your applications |
| **Firewall** | Open the SMTP listener port and web interface port in Windows Firewall |

> **Tip:** During Python installation, check **"Add Python to PATH"** and **"Install for all users"**.

> **Python 3.14 users:** The launcher automatically upgrades all packages to versions compatible with Python 3.14 on every startup. No manual steps are required.

---

## Step 1 — Download the Project

### Option A: Clone from GitHub
```powershell
git clone https://github.com/cegautreau-a11y/smtp-relay.git
cd smtp-relay
```

### Option B: Download ZIP
Download the repository as a ZIP file from GitHub, extract it, and open a terminal in the extracted folder.

---

## Step 2 — Create Your Configuration File

The application reads all settings from `config.json`. A documented template is provided.

```powershell
copy config.json.example config.json
```

Open `config.json` in a text editor (Notepad, VS Code, etc.) and configure the **three critical settings** before first run:

### 2a. Secret Key (Required)

The `secret_key` secures session cookies and CSRF tokens. **You must change this from the default.**

Generate a random key using Python:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output into your `config.json`:
```json
"web": {
    "host": "0.0.0.0",
    "port": 8025,
    "secret_key": "paste-your-64-character-hex-string-here"
}
```

> **Security Note:** Always set `secret_key` to a unique random value in production. All user sessions are invalidated on server restart by design.

### 2b. SMTP Listener Host (Required)

Set the `host` under `smtp_listener` to control which network interface the SMTP server binds to:

| Value | Meaning |
|---|---|
| `"0.0.0.0"` | Listen on all network interfaces (recommended for production) |
| `"127.0.0.1"` | Listen on localhost only (for testing, or if apps run on the same server) |
| `"192.168.1.50"` | Listen on a specific IP address |

```json
"smtp_listener": {
    "host": "0.0.0.0",
    "port": 2525,
    "banner_hostname": "relay.yourdomain.com",
    "require_auth": true,
    "enable_tls": false,
    "tls_cert_path": "",
    "tls_key_path": ""
}
```

> **Windows Note:** If binding to `0.0.0.0` fails (WinError 10049), the application automatically falls back to `127.0.0.1`. Check your network adapter configuration if this happens.

### 2c. Relay Destination (Required)

Configure where the relay forwards mail. This is your upstream SMTP server (e.g., Microsoft 365, Google Workspace, on-premises Exchange):

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

**Common upstream configurations:**

| Provider | Host | Port | TLS | STARTTLS | Auth Required |
|---|---|---|---|---|---|
| Microsoft 365 | `smtp.office365.com` | 587 | No | Yes | Yes |
| Google Workspace | `smtp-relay.gmail.com` | 587 | No | Yes | Yes |
| Amazon SES | `email-smtp.us-east-1.amazonaws.com` | 587 | No | Yes | Yes |
| On-premises Exchange | `mail.yourdomain.com` | 25 | No | Optional | Optional |
| Direct delivery (no relay) | `mx.recipient.com` | 25 | No | Optional | No |

---

## Step 3 — Install Dependencies

Dependencies are installed and upgraded automatically on every run. To install them manually:

```powershell
pip install --upgrade -r requirements.txt
```

This installs:
- **Flask** — Web framework
- **Flask-Login** — Session authentication
- **Flask-SQLAlchemy** — Database ORM
- **SQLAlchemy** — Database engine (2.0.37+ required for Python 3.14)
- **aiosmtpd** — Async SMTP server
- **bcrypt** — Password hashing

> **Note:** `requirements.txt` uses minimum version constraints (`>=`) rather than pinned versions. This ensures pip always installs a version compatible with your Python installation, including Python 3.14.

---

## Step 4 — Run the Application

```powershell
python run.py
```

On first run, the application will:
1. Upgrade pip packages to compatible versions automatically
2. Create the SQLite database (`smtp_relay.db`)
3. Create a default admin account
4. Start the SMTP listener
5. Start the web interface

You will see a startup banner:
```
============================================================
  SMTP Mail Relay  v3.0.2
  Designed and built by Christopher McGrath
============================================================
  Web Interface : http://0.0.0.0:8025
  SMTP Listener : 0.0.0.0:2525
  Relay Target  : smtp.office365.com:587
  HELO Hostname : mailrelay.yourdomain.com
  Default Login : admin / admin
============================================================
  Press Ctrl+C to stop.
```

---

## Step 5 — First Login

1. Open your browser and navigate to `http://YOUR-SERVER-IP:8025`
2. Log in with the default credentials:
   - **Username:** `admin`
   - **Password:** `admin`
3. **Immediately change the default password** via the Profile page (click the gear icon in the sidebar)
4. The default admin account is created as a **Super Admin**

---

## Step 6 — Configure Firewall

Open the required ports in Windows Firewall:

```powershell
# Allow inbound SMTP connections (from your applications)
netsh advfirewall firewall add rule name="SMTP Relay - SMTP" dir=in action=allow protocol=tcp localport=2525

# Allow inbound web management (from admin workstations)
netsh advfirewall firewall add rule name="SMTP Relay - Web" dir=in action=allow protocol=tcp localport=8025
```

> **Security:** Consider restricting the web interface port to specific admin IP addresses using the `remoteip` parameter.

---

## Step 7 — Run as a Windows Service (Optional)

For production, run the relay as a Windows service so it starts automatically on boot.

### Using NSSM (Non-Sucking Service Manager)

1. Download [NSSM](https://nssm.cc/download) and extract it
2. Open an elevated command prompt and run:
   ```powershell
   nssm install SMTPRelay
   ```
3. In the NSSM dialog:
   - **Path:** `C:\Python314\python.exe` (your Python path)
   - **Startup directory:** `C:\smtp-relay` (your project path)
   - **Arguments:** `run.py`
4. Click **Install service**
5. Start the service:
   ```powershell
   nssm start SMTPRelay
   ```

### Using Task Scheduler

1. Open Task Scheduler
2. Create a new task:
   - **Trigger:** At system startup
   - **Action:** Start a program
   - **Program:** `python.exe`
   - **Arguments:** `C:\smtp-relay\run.py`
   - **Start in:** `C:\smtp-relay`
3. Under **Settings**, check "Run whether user is logged on or not"

---

## Verifying the Installation

### Test SMTP Connectivity

From another machine or PowerShell on the server:

```powershell
# Quick telnet test
Test-NetConnection -ComputerName YOUR-SERVER-IP -Port 2525

# Send a test email using Python
python -c "
import smtplib
s = smtplib.SMTP('YOUR-SERVER-IP', 2525)
s.ehlo()
s.login('your-smtp-credential', 'your-password')
s.sendmail('sender@yourdomain.com', 'test@example.com', 'Subject: Test\n\nHello from SMTP Relay!')
s.quit()
print('Sent!')
"
```

### Check the Dashboard

Open the web interface and verify:
- SMTP status shows **Running** (green indicator in the header)
- The test email appears in **Email Logs**
- Statistics update on the **Dashboard**
- Version badge in the sidebar footer shows **v3.0.2**

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `WinError 10048` — Port already in use | Another process is using the port. Change the port in `config.json` or stop the conflicting process |
| `WinError 10049` — Address not valid | The bind address doesn't exist on this machine. Use `0.0.0.0` or `127.0.0.1` |
| SMTP shows "Stopped" after start | Check the logs for bind errors. Verify the port is not blocked |
| Can't connect from other machines | Check Windows Firewall rules. Verify `smtp_listener.host` is not `127.0.0.1` |
| STARTTLS errors | Ensure `relay_destination.helo_hostname` is set to a valid FQDN |
| "Authentication required" | Create SMTP credentials in the web interface under **SMTP Credentials** |
| Emails stuck in queue | Check **relay_destination** settings. View error details in the **Queue** page |
| Page hangs / server not responding | Fixed in v2.2.0. Ensure you are running v2.2.0 or later |
| `ImportError` or crash on startup | Run `pip install --upgrade -r requirements.txt` manually. Python 3.14 requires SQLAlchemy 2.0.37+ |
| Message-ID always blank in logs | Fixed in v2.2.0. The `message_id` column is added automatically on startup |

---

## Upgrading

### From v2.2.0 or later to v3.0.2

1. Stop the running application (`Ctrl+C` or stop the Windows service)
2. Replace all source files (`app.py`, `models.py`, `smtp_server.py`, `run.py`, `templates/`, `static/`, `requirements.txt`) with the v3.0.2 versions
3. Keep your existing `config.json` and `smtp_relay.db` — they are fully compatible
4. Start the application with `python run.py`
5. The database is automatically migrated if needed — no manual changes required

**What's new in v3.0.x:**
- Complete web interface redesign (v3.0.0) with modern UI
- Version badge displayed in sidebar footer
- Enhanced color palette and improved dark mode
- Better stat cards, tables, and animations
- Queue delete confirmations (v3.0.2) to prevent accidental deletions
- Processing section notice (v3.0.2) explaining why active messages cannot be cancelled

### From any version to v2.2.0

1. Stop the running application (`Ctrl+C` or stop the Windows service)
2. Replace all source files (`app.py`, `models.py`, `smtp_server.py`, `run.py`, `templates/`, `static/`, `requirements.txt`) with the v2.2.0 versions
3. Keep your existing `config.json` and `smtp_relay.db` — they are fully compatible
4. Start the application with `python run.py`
5. The database is automatically migrated on startup — `raw_headers` and `message_id` columns are added to `email_logs`
6. Existing log entries will show no headers in the detail modal; all new emails will capture full headers
7. Failed queue entries are now retained until manually retried or deleted — they are no longer auto-purged

No manual database changes are required. The migration is safe and idempotent.

---

## Next Steps

- [Configuration Reference](CONFIGURATION.md) — Full documentation of all config.json settings
- [User Roles](USER_ROLES.md) — Understanding the role-based permission system
- [Architecture](ARCHITECTURE.md) — System design and component overview
