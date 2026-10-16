#!/usr/bin/env python3
"""
SMTP Mail Relay – Launcher
Version 4.4.4
===================================
Designed and built by Christopher McGrath

Double-click this file or run:   python.exe run.py

On first run it will install the required pip packages automatically.
PostgreSQL is the only supported database: DATABASE_URL must be set.
Environment variables seed the configuration on first boot; from then on
the database owns the configuration. User accounts are managed through
the web interface only.

The web interface is served by gunicorn (production WSGI server) on
Linux/Docker; on Windows the Flask development server is used instead.
"""

import logging
import os
import subprocess
import sys

# ── resolve paths relative to this script ──────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

REQUIREMENTS = os.path.join(SCRIPT_DIR, 'requirements.txt')


def _parse_csv(value: str) -> list:
    """Parse comma-separated string into list, stripping whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_int(name: str, default: str) -> int:
    """Integer env var with a named failure instead of a bare ValueError.

    A typo like WEB_PORT=80o25 used to raise an unlabeled ValueError
    before logging existed; now it says exactly which variable is bad.
    """
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError:
        print('=' * 60)
        print(f'  ERROR: environment variable {name} must be a whole '
              f'number (got {raw!r}).')
        print('=' * 60)
        sys.exit(1)


def _env_to_config() -> dict:
    """Read environment variables into the bootstrap config.

    Bootstrap only: this mapping is applied ONCE, on first boot, while the
    database is not initialized yet (see load_config). The values seed the
    relay_config table; from then on the database owns the configuration
    and environment variables are ignored. Empty the database (or the
    Docker volume) to re-apply environment variables.
    """
    cfg = {
        "version": "4.4.4",
        "web": {
            "host": os.environ.get("WEB_HOST", "0.0.0.0"),
            "port": _env_int("WEB_PORT", "8025"),
            "secret_key": os.environ.get("SECRET_KEY", ""),
            "threads": _env_int("WEB_THREADS", "4"),
            "secure_cookies": (
                os.environ.get("WEB_SECURE_COOKIES", "false").strip().lower()
                == "true"
            ),
            "trust_proxy": (
                os.environ.get("WEB_TRUST_PROXY", "false").strip().lower()
                == "true"
            ),
        },
        "smtp_listener": {
            "host": os.environ.get("SMTP_LISTEN_HOST", "0.0.0.0"),
            "port": _env_int("SMTP_LISTEN_PORT", "2525"),
            "banner_hostname": os.environ.get("BANNER_HOSTNAME", "relay.local"),
            "require_auth": os.environ.get("REQUIRE_AUTH", "true").lower() == "true",
            "enable_tls": os.environ.get("ENABLE_TLS", "false").lower() == "true",
            "tls_cert_path": os.environ.get("TLS_CERT_PATH", ""),
            "tls_key_path": os.environ.get("TLS_KEY_PATH", ""),
        },
        "relay_destination": {
            "host": os.environ.get("RELAY_HOST", "smtp.example.com"),
            "port": _env_int("RELAY_PORT", "25"),
            "use_tls": os.environ.get("RELAY_USE_TLS", "false").lower() == "true",
            "use_starttls": (
                os.environ.get("RELAY_USE_STARTTLS", "true").lower() == "true"
            ),
            "auth_user": os.environ.get("RELAY_AUTH_USER", ""),
            "auth_password": os.environ.get("RELAY_AUTH_PASSWORD", ""),
            "helo_hostname": os.environ.get("RELAY_HELO_HOSTNAME", "localhost"),
        },
        "delivery_mode": {
            "mode": os.environ.get("DELIVERY_MODE", "relay"),
        },
        "direct_delivery": {
            "use_starttls": (
                os.environ.get("DIRECT_USE_STARTTLS", "false").lower() == "true"
            ),
            "auth_user": os.environ.get("DIRECT_AUTH_USER", ""),
            "auth_password": os.environ.get("DIRECT_AUTH_PASSWORD", ""),
        },
        "limits": {
            "max_message_size_bytes": (
                _env_int("MAX_MESSAGE_SIZE", "26214400")
            ),
            "max_recipients_per_message": (
                _env_int("MAX_RECIPIENTS", "100")
            ),
            "global_rate_limit_per_hour": (
                _env_int("GLOBAL_RATE_LIMIT", "1000")
            ),
            "allowed_source_ips": _parse_csv(os.environ.get("ALLOWED_SOURCE_IPS", "")),
        },
        "queue": {
            "retry_interval_seconds": (
                _env_int("QUEUE_RETRY_INTERVAL", "300")
            ),
            "max_retries": _env_int("QUEUE_MAX_RETRIES", "3"),
        },
        "logging": {
            "level": "INFO",
            "log_file": os.environ.get("LOG_FILE", ""),
            "log_retention_days": _env_int("LOG_RETENTION_DAYS", "30"),
            "debug_logging": os.environ.get("DEBUG_LOGGING", "false").lower() == "true",
        },
        "database": {
            "url": os.environ.get("DATABASE_URL", ""),
        },
    }
    return cfg


# ── first-boot env-var awareness ───────────────────────────────
# relay_config key -> environment variable name. DATABASE_URL is
# intentionally absent: it is connection info read on EVERY boot (see
# load_config), so it is never "ignored". SECRET_KEY is also absent: it is
# consumed by _resolve_secret_key_db on every boot. LOG_FILE is read from
# the environment on EVERY boot to configure logging before the database
# is reachable — never "ignored". DEBUG_LOGGING and LOG_RETENTION_DAYS
# only SEED their relay_config rows (first boot); afterwards the database
# owns them like every other setting.
_ENV_CONFIG_KEYS = {
    'web_host': 'WEB_HOST',
    'web_port': 'WEB_PORT',
    'web_threads': 'WEB_THREADS',
    'web_secure_cookies': 'WEB_SECURE_COOKIES',
    'web_trust_proxy': 'WEB_TRUST_PROXY',
    'listen_host': 'SMTP_LISTEN_HOST',
    'listen_port': 'SMTP_LISTEN_PORT',
    'banner_hostname': 'BANNER_HOSTNAME',
    'require_auth': 'REQUIRE_AUTH',
    'enable_tls': 'ENABLE_TLS',
    'tls_cert_path': 'TLS_CERT_PATH',
    'tls_key_path': 'TLS_KEY_PATH',
    'relay_host': 'RELAY_HOST',
    'relay_port': 'RELAY_PORT',
    'relay_use_tls': 'RELAY_USE_TLS',
    'relay_use_starttls': 'RELAY_USE_STARTTLS',
    'relay_auth_user': 'RELAY_AUTH_USER',
    'relay_auth_password': 'RELAY_AUTH_PASSWORD',
    'relay_helo_hostname': 'RELAY_HELO_HOSTNAME',
    'delivery_mode': 'DELIVERY_MODE',
    'direct_use_starttls': 'DIRECT_USE_STARTTLS',
    'direct_auth_user': 'DIRECT_AUTH_USER',
    'direct_auth_password': 'DIRECT_AUTH_PASSWORD',
    'max_message_size': 'MAX_MESSAGE_SIZE',
    'max_recipients': 'MAX_RECIPIENTS',
    'global_rate_limit': 'GLOBAL_RATE_LIMIT',
    'allowed_source_ips': 'ALLOWED_SOURCE_IPS',
    'queue_retry_interval': 'QUEUE_RETRY_INTERVAL',
    'queue_max_retries': 'QUEUE_MAX_RETRIES',
    'debug_logging': 'DEBUG_LOGGING',
    'log_retention_days': 'LOG_RETENTION_DAYS',
}

# relay_config keys stored as 'true'/'false' strings.
_BOOL_CONFIG_KEYS = {
    'require_auth', 'enable_tls', 'relay_use_tls', 'relay_use_starttls',
    'direct_use_starttls', 'web_secure_cookies', 'web_trust_proxy',
    'debug_logging',
}


def _env_config_value(key: str) -> str:
    """Environment value for one relay_config key, normalized for comparison."""
    raw = os.environ.get(_ENV_CONFIG_KEYS[key], '')
    if key in _BOOL_CONFIG_KEYS:
        return 'true' if str(raw).strip().lower() == 'true' else 'false'
    if key == 'allowed_source_ips':
        return ','.join(p.strip() for p in raw.split(',') if p.strip())
    return str(raw)


def _normalize_value(value):
    """Normalise values so env-style and database-style configs compare equal."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple)):
        return ','.join(str(item).strip() for item in value)
    return str(value)


def _read_stored_config(url: str) -> dict:
    """Read the relay_config table into a flat {key: value} dict.

    Uses a short-lived engine (same pattern as _database_ready) because
    this runs before the app — and therefore the shared engine — exists.
    Keys with no row fall back to RelayConfig.DEFAULTS so the comparison
    reflects what the app would actually use.
    """
    import sqlalchemy
    from models import RelayConfig, normalize_database_url
    engine = sqlalchemy.create_engine(normalize_database_url(url))
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            rows = conn.execute(text('SELECT key, value FROM relay_config')).all()
    except Exception:
        return {}
    finally:
        engine.dispose()
    stored = {k: v for k, v in rows}
    for key, (default, _desc) in RelayConfig.DEFAULTS.items():
        stored.setdefault(key, default)
    return stored


def _warn_ignored_env_vars(logger) -> None:
    """Warn when set env vars are ignored because the database is initialized.

    Environment variables seed relay_config on first boot only (see
    load_config). Afterwards the database owns the configuration. Compare
    each set variable against the stored value and log ONE warning listing
    the variables that differ and are therefore ignored, so operators know
    the documented way to re-apply them.
    """
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if not url or not _database_ready({'url': url}):
        return  # first boot (or database unreachable): env vars are applied
    if not any(name in os.environ for name in _ENV_CONFIG_KEYS.values()):
        return  # no relevant env vars set

    stored = _read_stored_config(url)

    def _stored_equals_env(key: str) -> bool:
        """True when the stored relay_config value still matches the env var.

        Secret keys are stored ENC1-encrypted, so the plaintext env value
        must be compared against the DECRYPTED stored value — comparing
        plaintext to the envelope produced a permanent false 'ignored'
        warning that trained operators to ignore the warning entirely.
        """
        stored_value = _normalize_value(stored.get(key))
        env_norm = _normalize_value(_env_config_value(key))
        if stored_value == env_norm:
            return True
        if stored_value.startswith('ENC1:'):
            from crypto_utils import decrypt_secret
            # The app encrypts with the resolved web.secret_key (stored row
            # wins over the env SECRET_KEY) — mirror that here.
            secret = (stored.get('web_secret_key')
                      or os.environ.get('SECRET_KEY', ''))
            try:
                plain = decrypt_secret(stored_value, secret)
            except Exception:
                return False
            return plain is not None and _normalize_value(plain) == env_norm
        return False

    ignored = sorted(
        var_name
        for key, var_name in _ENV_CONFIG_KEYS.items()
        if var_name in os.environ
        and not _stored_equals_env(key)
    )
    if ignored:
        logger.warning(
            'Environment variables set but ignored (the database owns the '
            'configuration after first boot): %s — empty the database to '
            're-apply environment variables.',
            ', '.join(ignored),
        )


# ── auto-install dependencies ──────────────────────────────────
def ensure_dependencies():
    """pip-install requirements.txt if any package is missing.

    Checks every module the app can import — the old five-package check
    let dnspython/dkimpy/gunicorn go missing silently, degrading direct
    delivery, DKIM signing, and the production web server without any
    error at startup.
    """
    required = (
        'flask', 'flask_login', 'flask_sqlalchemy', 'flask_wtf',
        'sqlalchemy', 'werkzeug', 'wtforms', 'bcrypt', 'aiosmtpd',
        'cryptography', 'dkim', 'dns', 'gunicorn', 'msal', 'psycopg2',
    )
    missing = []
    for module in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if not missing:
        return  # everything importable

    print('=' * 60)
    print('  First run – installing dependencies …')
    if missing:
        print(f'  Missing modules: {", ".join(missing)}')
    print('=' * 60)
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'install',
        '--quiet', '-r', REQUIREMENTS,
    ])
    print('  Dependencies installed.\n')


def _database_ready(db_cfg: dict) -> bool:
    """True when the configured PostgreSQL database exists and is initialized.

    Connects and checks for the users table — there is no file to look for.
    """
    url = (db_cfg.get('url') or '').strip()
    if not url:
        return False
    try:
        import sqlalchemy
        from models import normalize_database_url
        engine = sqlalchemy.create_engine(normalize_database_url(url))
        try:
            from sqlalchemy import inspect
            return inspect(engine).has_table('users')
        finally:
            engine.dispose()
    except Exception:
        return False  # unreachable / not initialized yet


def _database_reachable(url: str) -> tuple[bool, str]:
    """Can PostgreSQL answer a trivial query? (schema not required)

    Distinguishes 'first boot on an empty database' from 'the configured
    server is down / the URL is garbage' — treating the latter as first
    boot made the process die later with a raw traceback deep inside
    create_app instead of failing fast with a clear message.

    Returns (True, '') when reachable, otherwise (False, underlying-error)
    so the startup message can show WHY (refused / DNS / auth), not just
    that the server is unreachable.
    """
    try:
        import sqlalchemy
        from models import normalize_database_url
        engine = sqlalchemy.create_engine(normalize_database_url(url))
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            return True, ''
        finally:
            engine.dispose()
    except Exception as exc:
        reason = str(exc).strip() or exc.__class__.__name__
        return False, reason


def load_config() -> dict:
    """Load configuration (first-boot bootstrap semantics).

    - If the database is NOT initialized (first boot): build the config
      from environment variables and return it; the values seed the
      relay_config table when the app starts.
    - If the database IS initialized: the database owns the configuration.
      Environment variables are ignored after first boot — set-but-ignored
      variables are reported by _warn_ignored_env_vars(). Empty the
      database (or the Docker volume) to re-apply environment variables.

    DATABASE_URL is connection info and is read on EVERY boot. PostgreSQL
    is the only supported backend: a missing DATABASE_URL is fatal.
    """
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        print('=' * 60)
        print('  ERROR: PostgreSQL is required, but no DATABASE_URL is set.')
        print('  Set DATABASE_URL, e.g.:')
        print('    postgresql+psycopg2://user:pass@host:5432/smtp_relay')
        print('  SQLite is not supported as of v4.3.0.')
        print('=' * 60)
        sys.exit(1)

    # Fail fast on a malformed URL — left uncaught it surfaced as a raw
    # SQLAlchemy traceback deep inside create_app.
    from models import normalize_database_url
    try:
        normalize_database_url(url)
    except Exception as exc:
        print('=' * 60)
        print('  ERROR: DATABASE_URL is not a valid PostgreSQL URL.')
        print(f'  {exc}')
        print('  Expected e.g. postgresql+psycopg2://user:pass@host:5432/smtp_relay')
        print('=' * 60)
        sys.exit(1)

    env_cfg = _env_to_config()
    if _database_ready({'url': url}):
        # Logging comes from the environment (it is applied before the
        # database is reachable); everything else lives in relay_config.
        return {'database': {'url': url}, 'logging': env_cfg['logging']}
    reachable, detail = _database_reachable(url)
    if not reachable:
        print('=' * 60)
        print('  ERROR: cannot reach PostgreSQL at the configured DATABASE_URL.')
        print(f'  Underlying error: {detail}')
        print('  Checklist: is the postgres container running (docker ps),')
        print('  on the SAME Docker network as the app, and does the')
        print('  host/port/user/password in DATABASE_URL match it?')
        print('  (A server that is down is NOT the same as first boot —')
        print('  first boot requires an EMPTY database that accepts')
        print('  connections. Fix the connection and start again.)')
        print('=' * 60)
        sys.exit(1)
    # First boot of the database: environment variables seed it.
    return env_cfg


def setup_logging(cfg: dict):
    from logging.handlers import RotatingFileHandler

    log_cfg = cfg.get('logging', {})
    level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
    fmt = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = log_cfg.get('log_file', '')
    if log_file:
        os.makedirs(os.path.dirname(log_file) or '.', exist_ok=True)
        # Rotated so a default-on file log can never fill the data volume.
        handlers.append(RotatingFileHandler(
            log_file, maxBytes=20 * 1024 * 1024, backupCount=5))

    # Setup debug logging if enabled
    debug_cfg = cfg.get('logging', {})
    if debug_cfg.get('debug_logging', False):
        level = logging.DEBUG
        try:
            from logging.handlers import RotatingFileHandler
            debug_log_file = debug_cfg.get(
                'debug_log_file',
                # Default INSIDE the persisted data directory: a bare
                # relative path lands in /app (not in the smtp-data
                # volume) and silently vanishes on container recreation.
                os.path.join('data', 'smtp_debug.log'))
            debug_log_max_size = debug_cfg.get('debug_log_max_size', 52428800)
            debug_log_backup_count = debug_cfg.get('debug_log_backup_count', 5)

            os.makedirs(os.path.dirname(debug_log_file) or '.', exist_ok=True)
            debug_handler = RotatingFileHandler(
                debug_log_file,
                maxBytes=debug_log_max_size,
                backupCount=debug_log_backup_count
            )
            debug_handler.setLevel(logging.DEBUG)
            debug_formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s [DEBUG]'
            )
            debug_handler.setFormatter(debug_formatter)
            handlers.append(debug_handler)
            print(f'Debug logging enabled: {debug_log_file}')
        except Exception as exc:
            print(f'Debug logging setup failed: {exc}')

    logging.basicConfig(level=level, format=fmt, datefmt='%Y-%m-%d %H:%M:%S',
                        handlers=handlers)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('aiosmtpd').setLevel(logging.INFO)


def _serve(app) -> None:
    """Serve the Flask app (blocking).

    Linux/Docker with gunicorn installed: production WSGI server via
    gunicorn's Python API. Exactly ONE worker process — the SMTP listener
    and the queue processor live in this same process, so extra workers
    would double-bind the SMTP port and duplicate queue processing. The
    single 'gthread' worker handles WEB_THREADS concurrent requests.

    Windows, or gunicorn unavailable: Flask development server fallback.
    """
    logger = logging.getLogger('smtp_relay')
    # Web interface settings live in the database.
    from models import RelayConfig
    with app.app_context():
        host = RelayConfig.get('web_host', '0.0.0.0')
        port = RelayConfig.get_int('web_port', 8025)
        threads = RelayConfig.get_int('web_threads', 4)

    use_gunicorn = os.name != 'nt'
    if use_gunicorn:
        try:
            import gunicorn  # noqa: F401
        except ImportError:
            use_gunicorn = False

    if not use_gunicorn:
        logger.warning(
            'gunicorn not available on this platform - using the Flask '
            'development server (not recommended for production)'
        )
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
        return

    from gunicorn.app.base import BaseApplication

    def _pre_fork(server, worker):
        # Runs in this (master) process just before the web worker is
        # forked. Close pooled DB connections so the forked worker does
        # not inherit database handles owned by the master — the SMTP
        # listener and queue processor keep running here and simply
        # open fresh connections as needed.
        try:
            with app.app_context():
                ext = app.extensions.get('sqlalchemy')
                engines = list(getattr(ext, 'engines', {}).values())
        except Exception:
            return
        for engine in engines:
            try:
                engine.dispose()
            except Exception:
                pass

    class _RelayApplication(BaseApplication):
        """Minimal gunicorn application wrapping the already-built app."""

        def load_config(self):
            self.cfg.set('bind', f'{host}:{port}')
            # MUST stay a single worker: the SMTP listener socket and the
            # queue processor live in this process — extra workers would
            # double-bind the SMTP port and run duplicate queue processors.
            self.cfg.set('workers', 1)
            self.cfg.set('worker_class', 'gthread')
            self.cfg.set('threads', threads)
            self.cfg.set('timeout', 120)
            self.cfg.set('accesslog', '-')
            self.cfg.set('errorlog', '-')
            self.cfg.set('pre_fork', _pre_fork)

        def load(self):
            return app

    logger.info(
        'Serving web interface via gunicorn '
        '(workers=1, worker_class=gthread, threads=%d) on %s:%s',
        threads, host, port,
    )
    _RelayApplication().run()


def main():
    ensure_dependencies()

    cfg = load_config()

    # The Configuration page owns the Debug Logging toggle once the
    # database is initialized (DB-wins, like every other setting): the
    # DEBUG_LOGGING env var only seeds the row on first boot.  Without
    # this overlay the checkbox was decorative — nothing in the codebase
    # read the row, so the UI toggle never created data/smtp_debug.log
    # and the SMTP-layer debug helpers never leveled up.
    _db_url = (cfg.get('database') or {}).get('url') or ''
    if _db_url and _database_ready({'url': _db_url}):
        stored = _read_stored_config(_db_url)
        cfg.setdefault('logging', {})['debug_logging'] = (
            str(stored.get('debug_logging', 'false')).strip().lower() == 'true')

    setup_logging(cfg)
    logger = logging.getLogger('smtp_relay')
    _warn_ignored_env_vars(logger)

    # ── import app components (after deps are installed) ───────
    from app import create_app
    from smtp_server import QueueProcessor, SmtpRelayServer

    app = create_app(config_json=cfg)

    # ── start SMTP server + queue processor ────────────────────
    smtp_server = SmtpRelayServer(app)
    app._smtp_server = smtp_server

    queue_proc = QueueProcessor(app)
    app._queue_processor = queue_proc

    try:
        smtp_server.start()
        queue_proc.start()
    except Exception as exc:
        logger.error('Failed to start SMTP server: %s', exc)
        logger.info('Web interface will still be available.')

    # ── print banner (web settings from the database; the SMTP listener
    # and relay target always come from the LIVE database config) ──────
    from models import RelayConfig
    with app.app_context():
        web_host = RelayConfig.get('web_host', '0.0.0.0')
        web_port = RelayConfig.get_int('web_port', 8025)
        listen_host = RelayConfig.get('listen_host', '0.0.0.0')
        listen_port = RelayConfig.get_int('listen_port', 2525)
        relay_host = RelayConfig.get('relay_host', '?')
        relay_port = RelayConfig.get_int('relay_port', 25)
        helo = RelayConfig.get('relay_helo_hostname', 'localhost')
        mode = RelayConfig.get('delivery_mode', 'relay')

    print()
    print('=' * 60)
    print('  SMTP Mail Relay  v4.4.4')
    print('  Designed and built by Christopher McGrath')
    print('=' * 60)
    print(f'  Web Interface : http://{web_host}:{web_port}')
    print(f'  SMTP Listener : {listen_host}:{listen_port}')
    print(f'  Delivery Mode : {mode}')
    if mode == 'direct':
        print('  Relay Target  : recipient-domain MX (direct delivery)')
    else:
        print(f'  Relay Target  : {relay_host}:{relay_port}')
    print(f'  HELO Hostname : {helo}')
    print('  Admin Login   : initial credentials logged at first boot (see WARNING)')
    print('=' * 60)
    print('  Press Ctrl+C to stop.\n')

    # ── serve web interface (blocking) ─────────────────────────
    try:
        _serve(app)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info('Shutting down …')
        if smtp_server.is_running:
            smtp_server.stop()
        queue_proc.stop()
        logger.info('Goodbye.')


if __name__ == '__main__':
    main()
