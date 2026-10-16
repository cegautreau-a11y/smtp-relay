# Linux Docker + Direct Delivery Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the SMTP relay in a Linux Docker container and add a Direct Delivery mode that delivers emails straight to destination MX servers via MX lookup.

**Architecture:** Single Python process (Flask + aiosmtpd + queue processor). Two delivery paths: relay mode (queue → upstream relay) and direct mode (MX lookup → direct MX delivery). Configuration baked into image, overridable via environment variables.

**Tech Stack:** Python 3.12-slim, aiosmtpd, Flask, SQLite, dnspython

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `Dockerfile` | Create | Linux container image |
| `docker-compose.yml` | Create | Deployment config |
| `.env.example` | Create | Env var documentation |
| `requirements.txt` | Modify | Add dnspython |
| `run.py` | Modify | Env var → config.json bridging |
| `models.py` | Modify | delivery_mode in RelayConfig |
| `smtp_server.py` | Modify | Direct delivery via MX lookup |
| `app.py` | Modify | delivery_mode in config UI |
| `templates/config.html` | Modify | Delivery mode toggle |
| `config.json.example` | Modify | delivery_mode field |
| `docs/ARCHITECTURE.md` | Modify | Update architecture docs |
| `CHANGELOG.md` | Modify | Document changes |

---

## Task 1: Add dnspython Dependency

**Files:**
- Modify: `requirements.txt:9`

- [ ] **Step 1: Add dnspython to requirements.txt**

```diff
  aiosmtpd==1.4.6
  Flask==3.1.1
  Flask-Login==0.6.3
  Flask-SQLAlchemy==3.1.1
  Flask-WTF==1.2.2
  SQLAlchemy==2.0.36
  Werkzeug==3.1.3
  WTForms==3.2.1
  bcrypt==4.2.1
+ dnspython>=2.6.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt && git commit -m "deps: add dnspython for MX lookups"
```

---

## Task 2: Create Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Write Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8025 2525

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
```

- [ ] **Step 2: Commit**

```bash
git add Dockerfile && git commit -m "docker: add Dockerfile based on python:3.12-slim"
```

---

## Task 3: Create .env.example

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Write .env.example**

```
# Flask
SECRET_KEY=change-me-to-a-random-secret
WEB_HOST=0.0.0.0
WEB_PORT=8025

# SMTP Listener
SMTP_LISTEN_HOST=0.0.0.0
SMTP_LISTEN_PORT=2525
BANNER_HOSTNAME=relay.local
REQUIRE_AUTH=true
ENABLE_TLS=false
TLS_CERT_PATH=
TLS_KEY_PATH=

# Delivery Mode (relay or direct)
DELIVERY_MODE=relay

# Relay Mode Settings
RELAY_HOST=smtp.example.com
RELAY_PORT=25
RELAY_USE_TLS=false
RELAY_USE_STARTTLS=true
RELAY_AUTH_USER=
RELAY_AUTH_PASSWORD=
RELAY_HELO_HOSTNAME=localhost

# Direct Delivery Settings (used when DELIVERY_MODE=direct)
DIRECT_USE_STARTTLS=false
DIRECT_AUTH_USER=
DIRECT_AUTH_PASSWORD=

# Limits
MAX_MESSAGE_SIZE=26214400
MAX_RECIPIENTS=100
GLOBAL_RATE_LIMIT=1000
ALLOWED_SOURCE_IPS=

# Queue
QUEUE_RETRY_INTERVAL=300
QUEUE_MAX_RETRIES=3

# Logging
LOG_RETENTION_DAYS=30
DEBUG_LOGGING=false

# Database
DATABASE_PATH=smtp_relay.db
```

- [ ] **Step 2: Commit**

```bash
git add .env.example && git commit -m "docker: add .env.example with all env vars documented"
```

---

## Task 4: Create docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write docker-compose.yml**

```yaml
version: "3.9"

services:
  smtp-relay:
    build: .
    container_name: smtp-relay
    restart: unless-stopped
    ports:
      - "8025:8025"
      - "2525:2525"
    volumes:
      - smtp-data:/app
    environment:
      - SECRET_KEY=${SECRET_KEY:?SECRET_KEY required}
      - WEB_HOST=${WEB_HOST:-0.0.0.0}
      - WEB_PORT=${WEB_PORT:-8025}
      - SMTP_LISTEN_HOST=${SMTP_LISTEN_HOST:-0.0.0.0}
      - SMTP_LISTEN_PORT=${SMTP_LISTEN_PORT:-2525}
      - BANNER_HOSTNAME=${BANNER_HOSTNAME:-relay.local}
      - REQUIRE_AUTH=${REQUIRE_AUTH:-true}
      - ENABLE_TLS=${ENABLE_TLS:-false}
      - TLS_CERT_PATH=${TLS_CERT_PATH:-}
      - TLS_KEY_PATH=${TLS_KEY_PATH:-}
      - DELIVERY_MODE=${DELIVERY_MODE:-relay}
      - RELAY_HOST=${RELAY_HOST:-smtp.example.com}
      - RELAY_PORT=${RELAY_PORT:-25}
      - RELAY_USE_TLS=${RELAY_USE_TLS:-false}
      - RELAY_USE_STARTTLS=${RELAY_USE_STARTTLS:-true}
      - RELAY_AUTH_USER=${RELAY_AUTH_USER:-}
      - RELAY_AUTH_PASSWORD=${RELAY_AUTH_PASSWORD:-}
      - RELAY_HELO_HOSTNAME=${RELAY_HELO_HOSTNAME:-localhost}
      - DIRECT_USE_STARTTLS=${DIRECT_USE_STARTTLS:-false}
      - DIRECT_AUTH_USER=${DIRECT_AUTH_USER:-}
      - DIRECT_AUTH_PASSWORD=${DIRECT_AUTH_PASSWORD:-}
      - MAX_MESSAGE_SIZE=${MAX_MESSAGE_SIZE:-26214400}
      - MAX_RECIPIENTS=${MAX_RECIPIENTS:-100}
      - GLOBAL_RATE_LIMIT=${GLOBAL_RATE_LIMIT:-1000}
      - ALLOWED_SOURCE_IPS=${ALLOWED_SOURCE_IPS:-}
      - QUEUE_RETRY_INTERVAL=${QUEUE_RETRY_INTERVAL:-300}
      - QUEUE_MAX_RETRIES=${QUEUE_MAX_RETRIES:-3}
      - LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-30}
      - DEBUG_LOGGING=${DEBUG_LOGGING:-false}
      - DATABASE_PATH=${DATABASE_PATH:-smtp_relay.db}
    networks:
      - smtp-network

volumes:
  smtp-data:

networks:
  smtp-network:
    driver: bridge
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml && git commit -m "docker: add docker-compose.yml for deployment"
```

---

## Task 5: Update run.py — Env Var → Config Bridge

**Files:**
- Modify: `run.py`

**Changes:** Before loading config.json, check for environment variables and merge them in. Environment variables take precedence over config.json values.

- [ ] **Step 1: Add `_env_to_config()` helper function after the imports section (after line 28)**

```python
def _env_to_config() -> dict:
    """Read environment variables and overlay onto default config.

    Env vars take precedence over config.json values. This allows
    Docker deployments to override any setting at runtime.
    """
    cfg = {
        "version": "3.0.1",
        "web": {
            "host": os.environ.get("WEB_HOST", "0.0.0.0"),
            "port": int(os.environ.get("WEB_PORT", "8025")),
            "secret_key": os.environ.get("SECRET_KEY", ""),
        },
        "smtp_listener": {
            "host": os.environ.get("SMTP_LISTEN_HOST", "0.0.0.0"),
            "port": int(os.environ.get("SMTP_LISTEN_PORT", "2525")),
            "banner_hostname": os.environ.get("BANNER_HOSTNAME", "relay.local"),
            "require_auth": os.environ.get("REQUIRE_AUTH", "true").lower() == "true",
            "enable_tls": os.environ.get("ENABLE_TLS", "false").lower() == "true",
            "tls_cert_path": os.environ.get("TLS_CERT_PATH", ""),
            "tls_key_path": os.environ.get("TLS_KEY_PATH", ""),
        },
        "relay_destination": {
            "host": os.environ.get("RELAY_HOST", "smtp.example.com"),
            "port": int(os.environ.get("RELAY_PORT", "25")),
            "use_tls": os.environ.get("RELAY_USE_TLS", "false").lower() == "true",
            "use_starttls": os.environ.get("RELAY_USE_STARTTLS", "true").lower() == "true",
            "auth_user": os.environ.get("RELAY_AUTH_USER", ""),
            "auth_password": os.environ.get("RELAY_AUTH_PASSWORD", ""),
            "helo_hostname": os.environ.get("RELAY_HELO_HOSTNAME", "localhost"),
        },
        "delivery_mode": {
            "mode": os.environ.get("DELIVERY_MODE", "relay"),
        },
        "direct_delivery": {
            "use_starttls": os.environ.get("DIRECT_USE_STARTTLS", "false").lower() == "true",
            "auth_user": os.environ.get("DIRECT_AUTH_USER", ""),
            "auth_password": os.environ.get("DIRECT_AUTH_PASSWORD", ""),
        },
        "limits": {
            "max_message_size_bytes": int(os.environ.get("MAX_MESSAGE_SIZE", "26214400")),
            "max_recipients_per_message": int(os.environ.get("MAX_RECIPIENTS", "100")),
            "global_rate_limit_per_hour": int(os.environ.get("GLOBAL_RATE_LIMIT", "1000")),
            "allowed_source_ips": _parse_csv(os.environ.get("ALLOWED_SOURCE_IPS", "")),
        },
        "queue": {
            "retry_interval_seconds": int(os.environ.get("QUEUE_RETRY_INTERVAL", "300")),
            "max_retries": int(os.environ.get("QUEUE_MAX_RETRIES", "3")),
        },
        "logging": {
            "level": "INFO",
            "log_file": os.environ.get("LOG_FILE", ""),
            "log_retention_days": int(os.environ.get("LOG_RETENTION_DAYS", "30")),
            "debug_logging": os.environ.get("DEBUG_LOGGING", "false").lower() == "true",
        },
        "database": {
            "path": os.environ.get("DATABASE_PATH", "smtp_relay.db"),
        },
    }
    return cfg


def _parse_csv(value: str) -> list:
    """Parse comma-separated string into list, stripping whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
```

- [ ] **Step 2: Update `load_config()` to merge env vars over config.json (after creating default, merge env overrides)**

Find the `load_config` function and modify it to merge env var values on top of config.json:

```python
def load_config() -> dict:
    """Load config.json, then overlay env vars for Docker overrides."""
    cfg = _load_config_file()
    env_cfg = _env_to_config()
    return _merge_configs(cfg, env_cfg)
```

Add `_merge_configs()` and `_load_config_file()` helper functions:

```python
def _load_config_file() -> dict:
    """Load config.json from disk if it exists."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def _merge_configs(base: dict, overlay: dict) -> dict:
    """Deep-merge overlay dict into base dict. Overlay None/empty values are skipped."""
    result = dict(base)
    for key, val in overlay.items():
        if val is None or val == "":
            continue
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _merge_configs(result[key], val)
        else:
            result[key] = val
    return result
```

- [ ] **Step 3: Commit**

```bash
git add run.py && git commit -m "feat: add env var override bridge for Docker deployment"
```

---

## Task 6: Update config.json.example — Add delivery_mode

**Files:**
- Modify: `config.json.example`

- [ ] **Step 1: Add delivery_mode section to config.json.example**

Add after the `relay_destination` block:

```json
    "delivery_mode": {
        "mode": "relay"
    },
    "direct_delivery": {
        "use_starttls": false,
        "auth_user": "",
        "auth_password": ""
    },
```

Also update the `logging` section to remove Windows paths:

```json
    "logging": {
        "level": "INFO",
        "log_file": "",
        "log_retention_days": 30,
        "debug_logging": false
    },
```

- [ ] **Step 2: Commit**

```bash
git add config.json.example && git commit -m "config: add delivery_mode and direct_delivery sections"
```

---

## Task 7: Update models.py — Add delivery_mode to RelayConfig

**Files:**
- Modify: `models.py`

**Changes:** Add `delivery_mode` and `direct_*` keys to `RelayConfig.DEFAULTS` so the web UI and SMTP server can read them.

- [ ] **Step 1: Find the RelayConfig class and update DEFAULTS dict**

Locate the `RelayConfig` class with its `DEFAULTS` dict (around line 140-170 of models.py). Add these entries:

```python
"delivery_mode": ("relay", "Delivery mode: relay or direct"),
"direct_use_starttls": ("false", "Use STARTTLS for direct delivery"),
"direct_auth_user": ("", "AUTH user for direct delivery"),
"direct_auth_password": ("", "AUTH password for direct delivery"),
```

- [ ] **Step 2: Commit**

```bash
git add models.py && git commit -m "config: add delivery_mode and direct delivery settings to RelayConfig"
```

---

## Task 8: Update smtp_server.py — Direct Delivery MX Lookup

**Files:**
- Modify: `smtp_server.py`

**Changes:** The `_deliver()` method already receives the queue entry and relay config. It needs to check `delivery_mode` and either deliver to `relay_host` (relay mode) or do an MX lookup + deliver to the MX (direct mode).

- [ ] **Step 1: Add `_resolve_mx()` helper function after the debug logging functions (after line 79)**

```python
def _resolve_mx(domain: str) -> tuple[str, int] | None:
    """Resolve MX record for a domain.

    Returns:
        Tuple of (hostname, port) for the highest-priority MX, or None if resolution fails.

    Falls back to the domain itself on port 25 if no MX record exists.
    """
    try:
        import dns.resolver
        import dns.exception
    except ImportError:
        logger.warning("dnspython not available, falling back to direct delivery")
        return (domain, 25)

    try:
        answers = dns.resolver.resolve(domain, 'MX')
        if answers:
            mx_records = sorted(
                [(r.preference, str(r.exchange).rstrip('.')) for r in answers],
                key=lambda x: x[0]
            )
            return (mx_records[0][1], 25)
    except dns.exception.DNSException as exc:
        logger.debug("MX lookup failed for %s: %s, trying A record", domain, exc)

    try:
        import dns.resolver
        import dns.exception
        answers = dns.resolver.resolve(domain, 'A')
        if answers:
            return (str(answers[0]), 25)
    except dns.exception.DNSException:
        pass

    logger.warning("No MX or A record found for %s, using domain as mx host", domain)
    return (domain, 25)
```

- [ ] **Step 2: Update `_deliver()` method to branch based on delivery_mode**

In `_deliver()` method (around line 436 in smtp_server.py), find where it reads `host = RelayConfig.get('relay_host', 'localhost')`. Replace the delivery logic with a branch:

After fetching config values (around line 436-443), add:

```python
delivery_mode = RelayConfig.get('delivery_mode', 'relay')

if delivery_mode == 'direct' and recipients:
            recipient_domains = list(set([r.split('@')[1].lower() for r in recipients if '@' in r]))
            if recipient_domains:
                primary_domain = recipient_domains[0]
                mx_info = _resolve_mx(primary_domain)
                if mx_info:
                    host, _ = mx_info
                    port = RelayConfig.get_int('relay_port', 25)
                    logger.info("Direct delivery to %s for domain %s", host, primary_domain)
                else:
                    host = primary_domain
                    port = 25
            else:
                host = RelayConfig.get('relay_host', 'localhost')
                port = RelayConfig.get_int('relay_port', 25)
else:
            host = RelayConfig.get('relay_host', 'localhost')
            port = RelayConfig.get_int('relay_port', 25)
```

Then update the AUTH block to conditionally use direct delivery auth if in direct mode:

Find the auth block (around lines 508-515) and modify to check `delivery_mode`:

```python
if delivery_mode == 'direct':
            direct_user = RelayConfig.get('direct_auth_user', '').strip()
            direct_pass = RelayConfig.get('direct_auth_password', '').strip()
            if direct_user and direct_pass:
                try:
                    conn.login(direct_user, direct_pass)
                    debug_log_command(f"AUTH LOGIN (direct) {direct_user} [queue_id={queue_id}]")
                except smtplib.SMTPException as auth_exc:
                    logger.warning("Direct delivery auth failed: %s", auth_exc)
                    raise
elif auth_user and auth_pass:
            try:
                conn.login(auth_user, auth_pass)
                debug_log_command(f"AUTH LOGIN {auth_user} [queue_id={queue_id}]")
            except smtplib.SMTPException as auth_exc:
                logger.warning("Authentication failed: %s", auth_exc)
                raise
```

- [ ] **Step 3: Commit**

```bash
git add smtp_server.py && git commit -m "feat: add direct delivery mode with MX lookup"
```

---

## Task 9: Update app.py — Flatten and Persist delivery_mode Config

**Files:**
- Modify: `app.py`

**Changes:** Update `_flatten_config()` to include `delivery_mode`, `direct_use_starttls`, `direct_auth_user`, `direct_auth_password`. Update the config POST handler to handle new fields. Update config template rendering to include delivery_mode.

- [ ] **Step 1: Update `_flatten_config()` to handle new config sections**

Find `_flatten_config()` (around line 71-108 in app.py). Add `delivery_mode` and `direct_delivery` handling after the existing relay destination block:

```python
    delivery = cfg.get('delivery_mode', {})
    flat['delivery_mode'] = delivery.get('mode', 'relay')

    direct = cfg.get('direct_delivery', {})
    flat['direct_use_starttls'] = 'true' if direct.get('use_starttls', False) else 'false'
    flat['direct_auth_user'] = direct.get('auth_user', '')
    flat['direct_auth_password'] = direct.get('auth_password', '')
```

- [ ] **Step 2: Update `_write_db_config_to_file()` to persist delivery_mode**

Find `_write_db_config_to_file()` (around line 266-313). Add persistence for the new sections:

After the `relay_destination` write block, add:

```python
    cfg.setdefault('delivery_mode', {})
    cfg['delivery_mode']['mode'] = RelayConfig.get('delivery_mode', 'relay')

    cfg.setdefault('direct_delivery', {})
    cfg['direct_delivery']['use_starttls'] = RelayConfig.get_bool('direct_use_starttls', False)
    cfg['direct_delivery']['auth_user'] = RelayConfig.get('direct_auth_user', '')
    cfg['direct_delivery']['auth_password'] = RelayConfig.get('direct_auth_password', '')
```

- [ ] **Step 3: Update config route POST handler to handle new keys**

Find the config POST handler (around line 325-343). Add `delivery_mode`, `direct_use_starttls`, `direct_auth_user`, `direct_auth_password` to the `keys` list:

```python
keys = [
    'relay_host', 'relay_port', 'relay_use_tls', 'relay_use_starttls',
    'relay_auth_user', 'relay_auth_password', 'relay_helo_hostname',
    'listen_host', 'listen_port', 'banner_hostname', 'require_auth',
    'max_message_size', 'max_recipients', 'global_rate_limit',
    'enable_tls', 'tls_cert_path', 'tls_key_path',
    'allowed_source_ips', 'log_retention_days',
    'queue_retry_interval', 'queue_max_retries',
    'debug_logging', 'delivery_mode',
    'direct_use_starttls', 'direct_auth_user', 'direct_auth_password',
]
```

Add `delivery_mode`, `direct_use_starttls` to the `bools` set:

```python
bools = {'relay_use_tls', 'relay_use_starttls', 'require_auth', 'enable_tls', 'debug_logging', 'direct_use_starttls'}
```

- [ ] **Step 4: Commit**

```bash
git add app.py && git commit -m "feat: wire delivery_mode and direct delivery settings into config UI"
```

---

## Task 10: Update templates/config.html — Add Delivery Mode Toggle

**Files:**
- Modify: `templates/config.html`

**Changes:** Add a dropdown toggle for delivery mode. Show/hide relay vs direct delivery fields based on selection.

- [ ] **Step 1: Read the current config.html to understand the structure]

Use an explore agent to read `templates/config.html` and find the existing form structure for relay_destination fields.

- [ ] **Step 2: Add delivery mode toggle above the relay destination fields**

Add this HTML block where the relay destination section begins:

```html
<div class="card mb-3">
    <div class="card-header">Delivery Mode</div>
    <div class="card-body">
        <div class="mb-3">
            <label for="delivery_mode" class="form-label">Mode</label>
            <select class="form-select" id="delivery_mode" name="delivery_mode">
                <option value="relay" {{ 'selected' if configs.get('delivery_mode') == 'relay' else '' }}>Relay (forward to upstream SMTP)</option>
                <option value="direct" {{ 'selected' if configs.get('delivery_mode') == 'direct' else '' }}>Direct (MX lookup, deliver straight to destination)</option>
            </select>
        </div>
    </div>
</div>
```

- [ ] **Step 3: Wrap relay destination and direct delivery fields in toggle containers**

Wrap the existing relay destination section in a `<div id="relay-settings">` and add a new `<div id="direct-settings">` that shows/hides based on mode.

JavaScript for toggle (add before `</body>` or in a script block):

```javascript
document.addEventListener('DOMContentLoaded', function() {
    const modeSelect = document.getElementById('delivery_mode');
    const relaySettings = document.getElementById('relay-settings');
    const directSettings = document.getElementById('direct-settings');

    function updateVisibility() {
        const mode = modeSelect.value;
        if (relaySettings) relaySettings.style.display = mode === 'relay' ? '' : 'none';
        if (directSettings) directSettings.style.display = mode === 'direct' ? '' : 'none';
    }

    if (modeSelect) {
        modeSelect.addEventListener('change', updateVisibility);
        updateVisibility();
    }
});
```

- [ ] **Step 4: Add direct delivery settings section**

Add after the relay settings div close:

```html
<div class="card mb-3" id="direct-settings" style="display: none;">
    <div class="card-header">Direct Delivery Settings</div>
    <div class="card-body">
        <p class="text-muted small">Used when Delivery Mode is set to "Direct". Email is delivered straight to destination MX servers via MX lookup.</p>
        <div class="mb-3">
            <label for="direct_use_starttls" class="form-label">Use STARTTLS</label>
            <select class="form-select" id="direct_use_starttls" name="direct_use_starttls">
                <option value="true" {{ 'selected' if configs.get('direct_use_starttls') == 'true' else '' }}>Yes</option>
                <option value="false" {{ 'selected' if configs.get('direct_use_starttls') == 'false' else '' }}>No</option>
            </select>
        </div>
        <div class="mb-3">
            <label for="direct_auth_user" class="form-label">AUTH User</label>
            <input type="text" class="form-control" id="direct_auth_user" name="direct_auth_user" value="{{ configs.get('direct_auth_user', '') }}">
        </div>
        <div class="mb-3">
            <label for="direct_auth_password" class="form-label">AUTH Password</label>
            <input type="password" class="form-control" id="direct_auth_password" name="direct_auth_password" value="{{ configs.get('direct_auth_password', '') }}">
        </div>
    </div>
</div>
```

- [ ] **Step 5: Commit**

```bash
git add templates/config.html && git commit -m "ui: add delivery mode toggle to config page"
```

---

## Task 11: Update Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`, `CHANGELOG.md`

- [ ] **Step 1: Update ARCHITECTURE.md**

- Update system diagram to show two delivery paths (relay and direct)
- Add `dnspython` to technology stack
- Update config section to reflect env var override pattern
- Add direct delivery section to mail flow

- [ ] **Step 2: Update CHANGELOG.md**

Add entry under a new "Changes (2026-04-22)" header:

```markdown
## Changes (2026-04-22)

### Feature
| Issue | Fix | Files |
| Add Linux Docker support | Containerized application with python:3.12-slim base image | Dockerfile, docker-compose.yml, .env.example |
| Add Direct Delivery mode | MX lookup delivery bypassing upstream relay | smtp_server.py, models.py, app.py, templates/config.html |
| Add env var config override | All settings overridable via environment variables | run.py |
```

- [ ] **Step 3: Commit**

```bash
git add docs/ARCHITECTURE.md CHANGELOG.md && git commit -m "docs: update architecture and changelog"
```

---

## Verification

After all tasks complete:

1. **Docker build test:**
   ```bash
   docker build -t smtp-relay:test /home/cegautreau/smtp-relay-beta
   docker run --rm -e SECRET_KEY=test-secret -p 8025:8025 -p 2525:2525 smtp-relay:test
   ```
   Verify containers start and web interface is accessible.

2. **Config env var test:**
   ```bash
   docker run --rm -e DELIVERY_MODE=direct -e RELAY_HOST=test.example.com smtp-relay:test python -c "import run; print(run._env_to_config())"
   ```
   Verify output shows `delivery_mode: direct` and env overrides.

3. **MX lookup test (inside container):**
   ```bash
   docker run --rm smtp-relay:test python -c "from smtp_server import _resolve_mx; print(_resolve_mx('gmail.com'))"
   ```
   Verify it returns a valid MX hostname.

4. **Push to beta repo:**
   ```bash
   git push beta main
   ```

---

## Spec Coverage Check

| Spec Requirement | Task(s) |
|---|---|
| Linux Docker container | Task 2, 3, 4 |
| Env vars overridable | Task 5 |
| Direct delivery mode | Task 7, 8, 9, 10 |
| Web UI toggle | Task 10 |
| dnspython dependency | Task 1 |
| Config baked into image | Tasks 2, 3, 4 |
| Update docs | Task 11 |

**Placeholder scan:** None found.
**Type consistency:** `delivery_mode` used consistently across all tasks.
