# Installation Guide

This guide walks you through installing and running the SMTP Mail Relay on a Windows Server.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Operating System** | Windows Server 2016 or later (also works on Windows 10/11 for testing) |
| **Python** | 3.11 or later (3.14 supported). Download from [python.org](https://www.python.org/downloads/) |
| **Network** | The server must be reachable on the SMTP listener port (default 2525) from your applications |
| **Firewall** | Open the SMTP listener port and web interface port in Windows Firewall |

> **Tip:** During Python installation, check **"Add Python to PATH"** and **"Install for all users"**.

---

## Step 1 — Download the Project

### Option A: Clone from GitHub
```powershell
git clone https://github.com/YOUR-USERNAME/smtp-relay.git
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

> **Security Note:** A random secret key is generated on each server restart for session security. The `secret_key` in config.json is used as additional entropy. Always set it to a unique random value in production.

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

Dependencies are installed automatically on first run. To install them manually:

```powershell
pip install -r requirements.txt
```

This installs:
- **Flask** — Web framework
- **Flask-Login** — Session authentication
- **Flask-SQLAlchemy** — Database ORM
- **aiosmtpd** — Async SMTP server
- **bcrypt** — Password hashing

---

## Step 4 — Run the Application

```powershell
python run.py
```

On first run, the application will:
1. Install any missing pip packages automatically
2. Create the SQLite database (`smtp_relay.db`)
3. Create a default admin account
4. Start the SMTP listener
5. Start the web interface

You will see a startup banner:
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

For production, you should run the relay as a Windows service so it starts automatically on boot.

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

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `WinError 10048` — Port already in use | Another process is using the port. Change the port in `config.json` or stop the conflicting process |
| `WinError 10049` — Address not valid | The bind address doesn't exist on this machine. Use `0.0.0.0` or `127.0.0.1` |
| SMTP shows "Stopped" after start | Check the logs for bind errors. Verify the port is not blocked |
| Can't connect from other machines | Check Windows Firewall rules. Verify `smtp_listener.host` is not `127.0.0.1` |
| STARTTLS errors | Ensure `relay_destination.helo_hostname` is set. Python 3.14 requires valid hostnames for TLS |
| "Authentication required" | Create SMTP credentials in the web interface under **SMTP Credentials** |
| Emails stuck in queue | Check **relay_destination** settings. View error details in the **Queue** page |

---

## Next Steps

- [Configuration Reference](CONFIGURATION.md) — Full documentation of all config.json settings
- [User Roles](USER_ROLES.md) — Understanding the role-based permission system
- [Architecture](ARCHITECTURE.md) — System design and component overview