# Installation Guide

This guide walks you through installing and running the SMTP Mail Relay v4.4.4 on a Windows Server.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Operating System** | Windows Server 2016 or later (also works on Windows 10/11 for testing) |
| **Python** | 3.10 or later (3.14 supported). Download from [python.org](https://www.python.org/downloads/) |
| **PostgreSQL** | 12 or later — **required**. The relay does not support any other database |
| **Network** | The server must be reachable on the SMTP listener port (default 2525) from your applications |
| **Firewall** | Open the SMTP listener port and web interface port in Windows Firewall |

> **Tip:** During Python installation, check **"Add Python to PATH"** and **"Install for all users"**.

> **Python 3.14 users:** The launcher checks all dependencies on every startup and installs anything missing — no manual steps are required.

---

## Step 1 — Download the Project

### Option A: Clone from GitHub
```powershell
git clone https://github.com/cegautreau-a11y/smtp-relay-beta.git
cd smtp-relay-beta
```

### Option B: Download ZIP
Download the repository as a ZIP file from GitHub, extract it, and open a terminal in the extracted folder.

---

## Step 2 — Create the Database and Bootstrap Configuration

### 2a. PostgreSQL Database (Required)

Create a database and a login on your PostgreSQL server (any 12+ server):

```sql
CREATE USER smtp WITH PASSWORD 'choose-a-strong-password';
CREATE DATABASE smtp_relay OWNER smtp;
```

Then point the relay at it via the `DATABASE_URL` environment variable — the
app refuses to start without it:

```powershell
$env:DATABASE_URL = "postgresql+psycopg2://smtp:your-password@localhost:5432/smtp_relay"
```

On first boot (empty database) the app creates the schema, the initial
`admin` account, and seeds the configuration below.

### 2b. Secret Key

The secret key secures session cookies, CSRF tokens, and the ENC1 encryption
of stored passwords/DKIM keys. Set it via the `SECRET_KEY` environment
variable **before first boot** — it is then stored in the database and kept
forever (a later env change never rotates it, by design). If you leave it
unset, a strong random key is generated and stored on first boot.

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
$env:SECRET_KEY = "paste-your-64-character-hex-string-here"
```

> **Security Note:** Always set a unique random value in production. All user sessions are invalidated on server restart by design.

### 2c. SMTP Listener Host

Set `SMTP_LISTEN_HOST` to control which network interface the SMTP server binds to:

| Value | Meaning |
|---|---|
| `"0.0.0.0"` | Listen on all network interfaces (recommended for production) |
| `"127.0.0.1"` | Listen on localhost only (for testing, or if apps run on the same server) |
| `"192.168.1.50"` | Listen on a specific IP address |

```powershell
$env:SMTP_LISTEN_HOST = "0.0.0.0"
```

> **Windows Note:** If binding to `0.0.0.0` fails (WinError 10049), the application automatically falls back to `127.0.0.1`. Check your network adapter configuration if this happens.

### 2d. Relay Destination

Configure where the relay forwards mail via environment variables (all
optional — defaults apply; everything can also be edited later in the web
UI's Configuration page). This is your upstream SMTP server (e.g., Microsoft
365, Google Workspace, on-premises Exchange):

```powershell
$env:RELAY_HOST = "smtp.office365.com"
$env:RELAY_PORT = "587"
$env:RELAY_USE_STARTTLS = "true"
$env:RELAY_AUTH_USER = "relay@yourdomain.com"
$env:RELAY_AUTH_PASSWORD = "your-app-password"
$env:RELAY_HELO_HOSTNAME = "mailrelay.yourdomain.com"
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

### Optional: Microsoft 365 Sign-in (Azure Entra ID)

The web UI can sign users in with their Microsoft accounts (identity-only
OIDC — no Microsoft permissions or tokens are stored). Local password
sign-in always remains available for break-glass admin accounts.

1. In the [Azure portal](https://portal.azure.com) → **Microsoft Entra ID** →
   **App registrations** → **New registration**.
2. Name it (e.g. "SMTP Relay"), supported account type **single tenant**.
3. Add a **Web** redirect URI: `https://<your-host>/login/azure/callback`
   (use the exact host users type in the browser; add one per hostname, or
   set `azure.redirect_uri` in Configuration to pin one).
4. Copy the **Application (client) ID** and **Directory (tenant) ID** from
   the app overview.
5. **Certificates & secrets** → new client secret — copy the **Value**
   immediately (shown once).
6. In the relay web UI: **Administration → Configuration → Microsoft 365
   Sign-in**, paste the three values, save, then tick **Enable Microsoft
   sign-in**.
7. Create user accounts (**User Management**) using each person's
   Microsoft sign-in email. They then sign in with the **Sign in with
   Microsoft** button; the `admin` account keeps local password sign-in.

> No API permissions are required — only `openid profile email`, which
> needs no admin consent. If a sign-in fails with a redirect-uri error,
> the URI registered in Azure must match the one the app uses exactly
> (scheme, host, port, path).

### PostgreSQL (Required, v4.3.0+)

PostgreSQL is the **only** supported database: mail data and the
configuration both live there.

1. Create a database (any PostgreSQL 12+ server) and set `DATABASE_URL`
   (see Step 2a).
2. The app creates/verifies the schema on first boot.
3. Connection hardening is automatic: safe libpq defaults
   (`gssencmode=disable`, `connect_timeout=10`) are applied unless set
   explicitly in the URL, and pooled connections use pre-ping health
   checks.

## Step 3 — Install Dependencies

Dependencies are checked on each run and installed automatically if missing. To install them manually:

```powershell
pip install -r requirements.txt
```

This installs:
- **Flask** — Web framework
- **Flask-Login** — Session authentication
- **Flask-SQLAlchemy** — Database ORM
- **SQLAlchemy** — Database engine (pinned to 2.0.37)
- **aiosmtpd** — Async SMTP server
- **bcrypt** — Password hashing
- **gunicorn** — Production WSGI server for the web interface (Linux/Docker; unused on Windows)

> **Note:** `requirements.txt` uses exact version pins (`==`), so every installation gets the same tested package versions.

---

## Step 4 — Run the Application

```powershell
python run.py
```

On first run, the application will:
1. Check dependencies and install any that are missing
2. Refuse to start if `DATABASE_URL` is not set
3. Create the schema in PostgreSQL (first boot) and seed the configuration from environment variables
4. Create the initial admin account and print the generated random password to the console/log (a `WARNING` log line)
5. Start the SMTP listener
6. Start the web interface

You will see a startup banner:
```
============================================================
  SMTP Mail Relay  v4.4.4
  Designed and built by Christopher McGrath
============================================================
  Web Interface : http://0.0.0.0:8025
  SMTP Listener : 0.0.0.0:2525
  Delivery Mode : relay
  Relay Target  : smtp.office365.com:587
  HELO Hostname : mailrelay.yourdomain.com
  Admin Login   : initial credentials logged at first boot (see WARNING)
============================================================
  Press Ctrl+C to stop.
```

> **Web server:** On **Linux/Docker** the web interface is served by **gunicorn** (production WSGI server, single worker, `WEB_THREADS` request threads — default `4`). On **Windows** the app falls back to the Flask development server and logs a `WARNING` (`gunicorn not available on this platform`) — the SMTP engine and queue behave identically either way.

---

## Step 5 — First Login

1. Open your browser and navigate to `http://YOUR-SERVER-IP:8025`
2. Log in with the initial credentials:
   - **Username:** `admin`
   - **Password:** the random password generated on first boot — it was printed **once** to the console/log at startup (look for the `WARNING` line)
3. **Immediately change the initial password** via the Profile page (click the gear icon in the sidebar) — the generated password is not shown again
4. The initial admin account is created as a **Super Admin**

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

For production, run the relay as a Windows service so it starts automatically on boot. (On Windows the web interface runs on the Flask development server — gunicorn is Linux/Docker-only; the SMTP engine and queue are unaffected.)

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

## Step 8 — Run with Docker (Optional)

Alternatively, run the relay with the provided `docker-compose.yml`:

```bash
docker compose up -d --build
```

Docker-specific behaviour to be aware of:

- **Environment variables are first-boot only.** The variables in `docker-compose.yml` / `.env` seed the database once, while it is still empty. Afterwards the database owns the configuration and env changes are ignored. Empty the database — or the volume (`docker compose down -v`) — to re-apply env vars.
- **Data lives in the `pg-data` volume** (the in-stack `postgres:16-alpine` service). The `smtp-data` volume at `/app/data` only holds an optional `LOG_FILE`.
- **Upgrading the image** requires recreating the container (`docker compose up -d --build`) but **not** the volume — keep `pg-data` so your data, configuration, and secret key survive the upgrade.
- **Web server:** inside the container (Linux) the web interface is served by gunicorn; `WEB_THREADS` controls its request threads (default `4`).

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
- Version badge in the sidebar footer shows **v4.4.4**

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `WinError 10048` — Port already in use | Another process is using the port. Change the port in the web UI (Configuration — restart required) or stop the conflicting process |
| `WinError 10049` — Address not valid | The bind address doesn't exist on this machine. Use `0.0.0.0` or `127.0.0.1` |
| SMTP shows "Stopped" after start | Check the logs for bind errors. Verify the port is not blocked |
| Can't connect from other machines | Check Windows Firewall rules. Verify `listen_host` is not `127.0.0.1` |
| STARTTLS errors | Ensure `relay_helo_hostname` is set to a valid FQDN |
| "Authentication required" | Create SMTP credentials in the web interface under **SMTP Credentials** |
| Emails stuck in queue | Check the `relay_*` settings. View error details in the **Queue** page |
| Page hangs / server not responding | Fixed in v2.2.0. Ensure you are running v2.2.0 or later |
| `ImportError` or crash on startup | Run `pip install -r requirements.txt` manually |
| Initial admin password unknown | It was printed once to the first-boot console/log (`WARNING` line). If lost, empty the database (drop and recreate it) and restart to generate a new one (this resets all data) |
| Message-ID always blank in logs | Fixed in v2.2.0. The `message_id` column is added automatically on startup |

---

## Upgrading

### From v2.2.0 or later to v3.0.1

1. Stop the running application (`Ctrl+C` or stop the Windows service)
2. Replace all source files (`app.py`, `models.py`, `smtp_server.py`, `run.py`, `templates/`, `static/`, `requirements.txt`) with the v3.0.1 versions
3. Keep your existing `config.json` and `smtp_relay.db` — they are fully compatible
4. Start the application with `python run.py`
5. The database is automatically migrated if needed — no manual changes required

**What's new in v3.0.x:**
- Complete web interface redesign (v3.0.0) with modern UI
- Version badge displayed in sidebar footer
- Enhanced color palette and improved dark mode
- Better stat cards, tables, and animations
- Queue delete confirmations (v3.0.1) to prevent accidental deletions
- Processing section notice (v3.0.1) explaining why active messages cannot be cancelled

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

- [Configuration Reference](CONFIGURATION.md) — Full documentation of all configuration settings
- [User Roles](USER_ROLES.md) — Understanding the role-based permission system
- [Architecture](ARCHITECTURE.md) — System design and component overview
