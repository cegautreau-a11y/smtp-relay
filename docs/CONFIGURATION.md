# Configuration Reference

**SMTP Mail Relay v3.0.2**

All settings are stored in `config.json` in the project root directory. The application reads this file on startup and seeds the values into the database. Settings can also be changed at runtime through the web interface under **Configuration**.

---

## Configuration Hierarchy

The application uses a two-layer configuration system:

1. **config.json** — File-based defaults, read on startup
2. **Database (RelayConfig table)** — Runtime overrides set through the web UI

When you save settings in the web interface, they are written to both the database and `config.json`. You can also reload `config.json` from disk at any time using the **Reload config.json** button on the Configuration page.

---

## Full Settings Reference

### `web` — Web Management Interface

| Key | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"0.0.0.0"` | Bind address for the Flask web server. Use `0.0.0.0` for all interfaces or a specific IP to restrict access |
| `port` | integer | `8025` | Port number for the web interface |
| `secret_key` | string | *(must be set)* | Secret key used for session security and CSRF protection. **Must be changed from the default** |

**Example:**
```json
"web": {
    "host": "0.0.0.0",
    "port": 8025,
    "secret_key": "a1b2c3d4e5f6...your-random-string"
}
```

> **Note:** Changes to `web.host`, `web.port`, and `web.secret_key` require a full application restart to take effect. All user sessions are invalidated on server restart by design.

---

### `smtp_listener` — Inbound SMTP Server

These settings control the SMTP server that your applications connect to for sending mail.

| Key | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"0.0.0.0"` | Bind address for the SMTP listener. Set to your server's IP or `0.0.0.0` for all interfaces |
| `port` | integer | `2525` | SMTP listener port. Use `2525` to avoid conflicts with existing mail servers on port 25 |
| `banner_hostname` | string | `"relay.local"` | Hostname displayed in the SMTP banner when clients connect |
| `require_auth` | boolean | `true` | Require SMTP AUTH (LOGIN) before accepting mail. **Strongly recommended** |
| `enable_tls` | boolean | `false` | Enable TLS encryption on the SMTP listener |
| `tls_cert_path` | string | `""` | Path to the TLS certificate file (PEM format). Required if `enable_tls` is true |
| `tls_key_path` | string | `""` | Path to the TLS private key file (PEM format). Required if `enable_tls` is true |

**Example:**
```json
"smtp_listener": {
    "host": "192.168.1.50",
    "port": 2525,
    "banner_hostname": "relay.company.local",
    "require_auth": true,
    "enable_tls": false,
    "tls_cert_path": "",
    "tls_key_path": ""
}
```

**Choosing a bind address:**

| Value | Use Case |
|---|---|
| `"0.0.0.0"` | Accept connections from any network interface (production) |
| `"127.0.0.1"` | Accept connections only from the local machine (testing) |
| `"192.168.1.50"` | Accept connections only on a specific network interface |

> **Windows Note:** If binding to `0.0.0.0` fails, the application automatically retries with `127.0.0.1`. SMTP listener changes require an SMTP server restart (use the Restart button on the Dashboard).

---

### `relay_destination` — Upstream SMTP Server

These settings control where the relay forwards mail for final delivery.

| Key | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"smtp.example.com"` | Hostname of the upstream SMTP server |
| `port` | integer | `587` | Port of the upstream SMTP server |
| `use_tls` | boolean | `false` | Use implicit TLS (SMTPS) for the connection. Typically used with port 465 |
| `use_starttls` | boolean | `true` | Upgrade the connection to TLS using STARTTLS. Typically used with port 587 |
| `auth_user` | string | `""` | Username for authenticating with the upstream server. Leave empty for no auth |
| `auth_password` | string | `""` | Password for authenticating with the upstream server |
| `helo_hostname` | string | `"myserver.example.com"` | Hostname sent in the EHLO/HELO command to the upstream server. Should be a valid FQDN |

**Example — Microsoft 365:**
```json
"relay_destination": {
    "host": "smtp.office365.com",
    "port": 587,
    "use_tls": false,
    "use_starttls": true,
    "auth_user": "relay@company.com",
    "auth_password": "app-password-here",
    "helo_hostname": "mailrelay.company.com"
}
```

**Example — On-premises Exchange (no auth):**
```json
"relay_destination": {
    "host": "exchange.company.local",
    "port": 25,
    "use_tls": false,
    "use_starttls": false,
    "auth_user": "",
    "auth_password": "",
    "helo_hostname": "smtprelay.company.local"
}
```

**TLS vs STARTTLS:**

| Setting | Port | Behaviour |
|---|---|---|
| `use_tls: true` | 465 | Connection is encrypted from the start (SMTPS) |
| `use_starttls: true` | 587 | Connection starts plain, then upgrades to TLS |
| Both `false` | 25 | Plain text connection (use only on trusted networks) |

> **Important:** Do not set both `use_tls` and `use_starttls` to `true`. Use one or the other.

> **Python 3.14 Note:** `helo_hostname` must be a valid FQDN when using STARTTLS. Python 3.14 enforces stricter hostname validation for TLS connections.

---

### `limits` — Rate Limiting & Restrictions

| Key | Type | Default | Description |
|---|---|---|---|
| `max_message_size_bytes` | integer | `26214400` | Maximum email size in bytes (default 25 MB) |
| `max_recipients_per_message` | integer | `100` | Maximum number of recipients per email |
| `global_rate_limit_per_hour` | integer | `1000` | Maximum emails per hour across all credentials |
| `allowed_source_ips` | array | `[]` | List of IP addresses allowed to connect. Empty = all IPs allowed |

**Example:**
```json
"limits": {
    "max_message_size_bytes": 10485760,
    "max_recipients_per_message": 50,
    "global_rate_limit_per_hour": 500,
    "allowed_source_ips": ["192.168.1.10", "192.168.1.20", "10.0.0.5"]
}
```

> **Note:** Per-credential rate limits are configured separately in the web interface under **SMTP Credentials**. The global rate limit applies on top of individual credential limits.

---

### `queue` — Retry Behaviour

| Key | Type | Default | Description |
|---|---|---|---|
| `retry_interval_seconds` | integer | `300` | Base interval between retry attempts (in seconds). Actual delay increases with each retry |
| `max_retries` | integer | `3` | Maximum number of delivery attempts before marking as failed |

**Example:**
```json
"queue": {
    "retry_interval_seconds": 300,
    "max_retries": 5
}
```

**Retry timing:** The delay between retries increases with each attempt. For a base interval of 300 seconds:
- Retry 1: 300 seconds (5 minutes)
- Retry 2: 600 seconds (10 minutes)
- Retry 3: 900 seconds (15 minutes)

**After all retries are exhausted**, the message is marked as **failed** and held in the queue with its full message data intact. Failed messages are **never automatically discarded** — they remain in the system until an administrator takes action from the **Queue** page:

| Action | Scope | Description |
|---|---|---|
| **Retry** | Individual | Requeue a single failed message for another delivery attempt |
| **Retry All Failed** | Bulk | Requeue all failed messages at once for redelivery |
| **Delete** | Individual | Permanently remove a single failed message (with confirmation prompt in v3.0.2) |
| **Delete All Failed** | Bulk | Permanently remove all failed messages (with confirmation prompt) |

All retry actions reset the retry counter to zero and schedule immediate redelivery.

> **v3.0.2 Note:** Delete actions now prompt for confirmation to prevent accidental deletions. The Queue page also shows an informational notice for actively processing messages explaining why they cannot be cancelled.

---

### `logging` — Application Logging

| Key | Type | Default | Description |
|---|---|---|---|
| `level` | string | `"INFO"` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `log_file` | string | `""` | Path to a log file. Empty = console output only |
| `log_retention_days` | integer | `30` | Number of days to keep email log entries and sent queue entries in the database |

**Example:**
```json
"logging": {
    "level": "INFO",
    "log_file": "logs/smtp_relay.log",
    "log_retention_days": 90
}
```

> **Retention policy:** The automatic cleanup only purges successfully **sent** queue entries after `log_retention_days`. **Failed** queue entries are never automatically deleted — they are retained indefinitely until an administrator manually retries or deletes them from the Queue page. This ensures no undelivered email is ever silently discarded.

---

### `database` — Database Settings

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | string | `"smtp_relay.db"` | Path to the SQLite database file (relative to the project directory) |

**Example:**
```json
"database": {
    "path": "data/smtp_relay.db"
}
```

> **Note:** The directory will be created automatically if it doesn't exist. The database is created on first run with all required tables. A 20-second busy timeout is configured on the database connection to prevent web requests from hanging if a brief write lock contention occurs.

---

## Runtime Configuration via Web UI

Most settings under `smtp_listener`, `relay_destination`, `limits`, `queue`, and `logging` can be changed at runtime through the web interface:

1. Navigate to **Configuration** in the sidebar (requires Admin role)
2. Edit the desired settings
3. Click **Save** — changes are applied immediately and written to both the database and `config.json`

**Additional web UI buttons:**
- **Reload config.json** — Re-reads `config.json` from disk and overwrites database values
- **Save to file** — Writes current database values back to `config.json`

> **Note:** Changes to `web.host`, `web.port`, and `web.secret_key` require a full application restart to take effect. SMTP listener changes require an SMTP server restart (use the Restart button on the Dashboard).
