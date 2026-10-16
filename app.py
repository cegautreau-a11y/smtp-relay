"""
Flask web application for SMTP Relay management.
Version 4.5.1

Designed and built by Christopher McGrath

PostgreSQL is the only supported database; the database is also the only
configuration store. User accounts are managed exclusively through the
web UI.
"""

import datetime
import json
import logging
import os
import re
import secrets
import threading
import time

import bcrypt

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    jsonify, abort, session,
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)
from flask_wtf.csrf import CSRFProtect
from functools import wraps
from sqlalchemy import func

import crypto_utils
from auth_oauth import create_oauth_blueprint
from models import (
    db, normalize_database_url, User, AllowedDomain, SmtpCredential, EmailLog,
    RelayConfig, EmailQueue, Role, DkimKey,
)

logger = logging.getLogger('smtp_relay')


def _resolve_database_uri(db_cfg: dict) -> str:
    """SQLAlchemy URI for the configured PostgreSQL database.

    database.url (e.g. postgresql+psycopg2://user:pass@host:5432/smtp_relay,
    or the DATABASE_URL environment variable via run.py) is required —
    SQLite is not supported as of v4.3.0.
    """
    url = (db_cfg.get('url') or '').strip()
    if not url:
        raise RuntimeError(
            'No PostgreSQL database configured. Set DATABASE_URL (or the '
            'database.url bootstrap key), e.g. '
            'postgresql+psycopg2://user:pass@host:5432/smtp_relay — '
            'SQLite is not supported as of v4.3.0.')
    return normalize_database_url(url)

# Secret keys that ship as defaults in examples must never guard a session.
_WEAK_SECRET_KEYS = {'change-me', 'changeme', 'change_me', 'secret', 'change me'}

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@+'-]{1,80}$")
# + and ' are legal in email local-parts: Entra accounts use the email as
# their username, so bob+work@corp.com / o'brien@corp.com must be accepted.
_HOSTNAME_RE = re.compile(
    r'^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?'
    r'(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$')
_DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$')

# ── Login rate limiting (in-memory, per-process) ─────────────────
# NOTE: the failure counters below live in this process's memory only.
# That is fine for the single-process design (one Flask app + one SMTP
# server); a multi-worker deployment would need a shared store.
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW = 15 * 60        # failures older than this are forgotten
_LOGIN_LOCKOUT = 15 * 60       # lock lasts this long once triggered
_LOGIN_MAX_KEYS = 10000        # hard cap on tracked throttle keys
_login_failures = {}           # 'ip|username' -> [monotonic timestamps]
_login_lock = threading.Lock()
# Lazily-built bcrypt hash burned on unknown usernames (see login POST).
_dummy_bcrypt_hash = None
# Serializes the admin user-management handlers (toggle / change-role /
# delete): their last-active-admin guard is check-then-act, and two
# concurrent requests could both see "another admin exists", both commit,
# and leave zero active admins — a permanent lockout, since the bootstrap
# admin is only provisioned on an empty table.  gunicorn runs workers=1,
# so a process-wide lock is airtight.
_user_mutation_lock = threading.Lock()


def _login_key(ip, username):
    """Build the throttling key for one client IP + username pair."""
    return f'{ip or "unknown"}|{(username or "").strip().lower()}'


def _prune_login_failures(now):
    """Drop stale failure lists (caller must hold _login_lock)."""
    expired = [k for k, stamps in _login_failures.items()
               if not stamps or now - stamps[-1] >= _LOGIN_WINDOW + _LOGIN_LOCKOUT]
    for k in expired:
        _login_failures.pop(k, None)


def _login_throttled(key):
    """Return True when *key* has accumulated too many recent failures."""
    now = time.monotonic()
    with _login_lock:
        _prune_login_failures(now)
        stamps = [t for t in _login_failures.get(key, []) if now - t < _LOGIN_WINDOW]
        if key in _login_failures:
            # Refresh only existing keys: writing unseen keys here would let
            # an attacker spray arbitrary usernames and grow the dict.
            _login_failures[key] = stamps
        return len(stamps) >= _LOGIN_MAX_FAILURES and now - stamps[-1] < _LOGIN_LOCKOUT


def _record_login_failure(key):
    now = time.monotonic()
    with _login_lock:
        stamps = [t for t in _login_failures.get(key, []) if now - t < _LOGIN_WINDOW]
        stamps.append(now)
        _login_failures[key] = stamps
        # Hard cap: a brute-force spray of unique username/IP keys must not
        # grow memory without bound; evict the oldest-inserted keys.
        if len(_login_failures) > _LOGIN_MAX_KEYS:
            _prune_login_failures(now)
        while len(_login_failures) > _LOGIN_MAX_KEYS:
            _login_failures.pop(next(iter(_login_failures)))


def _clear_login_failures(key):
    with _login_lock:
        _login_failures.pop(key, None)


# Convenience shortcuts
def admin_required(f):
    """Require Admin or Super Admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role(Role.ADMIN):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def operator_required(f):
    """Require Operator, Admin, or Super Admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role(Role.OPERATOR):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def queue_manager_required(f):
    """Require Queue Manager, Operator, Admin, or Super Admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role(Role.QUEUE_MANAGER):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def _safe_redirect_target(target):
    """Return *target* only when it is a safe same-origin relative path.

    Anything else (absolute URLs, protocol-relative '//', backslash tricks
    like '/\\evil.example' or '\\evil.example' — browsers normalize those
    to '//' — and non-strings) returns None so the caller can fall back to
    a known-good endpoint.  This closes the open redirect via ?next=.
    """
    if not isinstance(target, str) or not target:
        return None
    if not target.startswith('/'):
        return None
    if re.match(r'^[\\/][\\/]', target):
        return None
    if re.search(r'[\x00-\x1f\x7f]', target):
        # Browsers strip tab/CR/LF while parsing URLs, so a target like
        # '/\t/evil.com' would resolve as protocol-relative '//evil.com'.
        return None
    return target


def _parse_rate_limit(raw):
    """Parse a max_sends_per_hour form value; None when invalid.

    Accepts whole numbers between 1 and 1000000 only.
    """
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < 1 or value > 1000000:
        return None
    return value


def _config_bool(value, default=False):
    """Normalize a bootstrap-config boolean (real bool or 'true'/'false' string)
    to the 'true'/'false' string form used in the DB and templates."""
    if value is None:
        value = default
    if isinstance(value, str):
        return 'true' if value.strip().lower() in ('true', '1', 'yes', 'on') else 'false'
    return 'true' if value else 'false'


def _config_password(value):
    """Pass a stored password value through, mapping the legacy
    '[ENCRYPTED]' placeholder (written by older versions) to ''."""
    if value == '[ENCRYPTED]':
        logger.warning(
            'the bootstrap config contains a legacy "[ENCRYPTED]" password placeholder; '
            'treating as empty. Re-enter the password in the web UI.'
        )
        return ''
    return value


def _flatten_config(cfg: dict) -> dict:
    """Turn the nested bootstrap config into the flat key->value map used by
    RelayConfig in the database.  Booleans are normalized to 'true'/'false'
    strings so the DB and templates never see raw Python bools."""
    flat = {}
    smtp = cfg.get('smtp_listener', {})
    flat['listen_host']       = smtp.get('host', '0.0.0.0')
    flat['listen_port']       = str(smtp.get('port', 2525))
    flat['banner_hostname']   = smtp.get('banner_hostname', 'relay.local')
    flat['require_auth']      = _config_bool(smtp.get('require_auth', True))
    flat['enable_tls']        = _config_bool(smtp.get('enable_tls', False))
    flat['tls_cert_path']     = smtp.get('tls_cert_path', '')
    flat['tls_key_path']      = smtp.get('tls_key_path', '')

    dest = cfg.get('relay_destination', {})
    flat['relay_host']          = dest.get('host', 'localhost')
    flat['relay_port']          = str(dest.get('port', 25))
    flat['relay_use_tls']       = _config_bool(dest.get('use_tls', False))
    flat['relay_use_starttls']  = _config_bool(dest.get('use_starttls', True))
    flat['relay_auth_user']     = dest.get('auth_user', '')
    flat['relay_auth_password'] = _config_password(dest.get('auth_password', ''))
    flat['relay_helo_hostname'] = dest.get('helo_hostname', 'localhost')

    delivery = cfg.get('delivery_mode', {})
    flat['delivery_mode'] = delivery.get('mode', 'relay')

    direct = cfg.get('direct_delivery', {})
    flat['direct_use_starttls'] = _config_bool(direct.get('use_starttls', False))
    flat['direct_auth_user'] = direct.get('auth_user', '')
    flat['direct_auth_password'] = _config_password(direct.get('auth_password', ''))

    lim = cfg.get('limits', {})
    flat['max_message_size']  = str(lim.get('max_message_size_bytes', 26214400))
    flat['max_recipients']    = str(lim.get('max_recipients_per_message', 100))
    flat['global_rate_limit'] = str(lim.get('global_rate_limit_per_hour', 1000))
    ips = lim.get('allowed_source_ips', [])
    flat['allowed_source_ips'] = ','.join(ips) if isinstance(ips, list) else str(ips)

    q = cfg.get('queue', {})
    flat['queue_retry_interval'] = str(q.get('retry_interval_seconds', 300))
    flat['queue_max_retries']    = str(q.get('max_retries', 3))

    log = cfg.get('logging', {})
    flat['log_retention_days'] = str(log.get('log_retention_days', 30))
    flat['debug_logging']      = _config_bool(log.get('debug_logging', False))

    az = cfg.get('azure', {})
    flat['azure_enabled']       = _config_bool(az.get('enabled', False))
    flat['azure_tenant_id']     = az.get('tenant_id', '')
    flat['azure_client_id']     = az.get('client_id', '')
    flat['azure_client_secret'] = _config_password(az.get('client_secret', ''))
    flat['azure_redirect_uri']  = az.get('redirect_uri', '')

    web = cfg.get('web', {})
    flat['web_host']           = str(web.get('host', '0.0.0.0'))
    flat['web_port']           = str(web.get('port', 8025))
    flat['web_threads']        = str(web.get('threads', 4))
    flat['web_secure_cookies'] = _config_bool(web.get('secure_cookies', False))
    flat['web_trust_proxy']    = _config_bool(web.get('trust_proxy', False))

    return flat


def create_app(config_json: dict | None = None):
    """Create the Flask application.

    Parameters
    ----------
    config_json : dict, optional
        Bootstrap configuration (from run.py's environment mapping).
        database.url MUST hold the PostgreSQL URL; on a fresh database
        the values seed the RelayConfig table so the SMTP server and
        web UI pick them up.
    """
    app = Flask(__name__)

    cfg = config_json or {}
    db_cfg = cfg.get('database', {})

    # Resolved from the database inside the startup block below.
    secret_key = secrets.token_hex(16)   # placeholder until then

    db_uri = _resolve_database_uri(db_cfg)

    # The secret key above is a placeholder replaced by
    # _resolve_secret_key_db() once the database is reachable.
    app.config['SECRET_KEY'] = secret_key
    app.config['CONFIG_SOURCE'] = 'database'
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # pool_timeout caps how long a request waits for a pooled connection
    # and returns a 500 instead of spinning forever; pool_pre_ping guards
    # long-lived PostgreSQL connections against server-side drops.
    engine_options = {
        'pool_timeout': 10,
        'pool_recycle': 60,
        'pool_pre_ping': True,
    }
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options
    app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=8)
    app.config['SESSION_PERMANENT'] = False          # session cookie dies when browser closes
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Secure-cookies and trust-proxy come from the database (resolved in
    # the startup block below); the app is often reached over plain HTTP
    # on a LAN, so both default to off.
    app.config['SESSION_COOKIE_SECURE'] = False
    app.config['REMEMBER_COOKIE_DURATION'] = datetime.timedelta(hours=0)  # disable "remember me"
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    # Reject absurd request bodies (no file uploads exist; forms are small).
    # SMTP message size is enforced independently by the engine's
    # max_message_size check, so this cap only guards the web forms.
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

    # Behind a reverse proxy every request shares the proxy's IP, which
    # would make the login throttle lock out ALL users together.  When the
    # deployment opts in via the database (web_trust_proxy), the throttle
    # keys on the RIGHT-MOST X-Forwarded-For hop (the hop the trusted proxy
    # appended — see the login POST below) and werkzeug's ProxyFix rewrites
    # the request scheme from X-Forwarded-Proto.  Keep this OFF unless a
    # proxy is actually in front — clients can spoof the header.
    trust_proxy = False   # resolved from the database in the startup block

    db.init_app(app)
    CSRFProtect(app)
    login_mgr = LoginManager()
    login_mgr.init_app(app)
    login_mgr.login_view = 'login'
    login_mgr.login_message_category = 'warning'

    # Microsoft Entra ID sign-in (identity-only OIDC; the local users
    # table is the allow-list).  Its routes fall back to /login with a
    # flash when azure sign-in is not enabled/configured.
    app.register_blueprint(create_oauth_blueprint(app))

    @login_mgr.user_loader
    def load_user(uid):
        # db.session.get is the modern replacement for the legacy
        # Query.get(), which SQLAlchemy 2.x deprecates.
        return db.session.get(User, int(uid))

    @app.template_filter('recipients')
    def recipients_filter(value):
        """Parse the recipients JSON column into a list for templates.

        Returns [] for None, malformed JSON, or JSON that is not a list;
        the parsed list otherwise.
        """
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    @app.after_request
    def set_security_headers(response):
        """Add baseline security headers to every response."""
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response

    with app.app_context():
        db.create_all()
        _migrate_roles()
        _migrate_schema()
        _migrate_oauth()
        secret_key = _resolve_secret_key_db()
        app.config['SECRET_KEY'] = secret_key
        RelayConfig.initialize_defaults()
        # On a fresh database the environment-derived bootstrap config
        # seeds the rows (default-aware).  Seed only keys the database has
        # not customized: a row that is missing or still holds its default
        # value may be filled, but a value already changed (e.g. via the
        # web UI) must win, so a restart can never silently revert it.
        if cfg:
            flat = _flatten_config(cfg)
            existing = {row.key: row.value for row in RelayConfig.query.all()}
            fresh = {
                k: v for k, v in flat.items()
                if existing.get(k) is None
                or existing.get(k) == RelayConfig.DEFAULTS.get(k, ('',))[0]
            }
            RelayConfig.load_from_dict(fresh)
        # Runs on EVERY boot (not just first boot): it scans relay_config
        # for plaintext secrets and encrypts them in place, so a crash
        # between the env seed and this point heals on the next boot.
        _protect_upstream_passwords_on_startup(cfg, secret_key)
        _protect_dkim_keys_on_startup(secret_key)
        _ensure_admin_exists()
        # First-boot completion marker: run.py's _database_ready() only
        # treats the database as initialized once this row exists.  A crash
        # anywhere above (create_all → secret key → defaults → env seed →
        # ENC1 protection) leaves no marker, so the next boot re-runs the
        # whole first-boot path — which is default-aware and idempotent —
        # instead of silently discarding the operator's env configuration.
        RelayConfig.set('bootstrap_complete', 'true')
        # Web settings live in the database: apply them now that it is
        # reachable (web_host/port/threads are read again at serve time,
        # so all of them stay restart-required).
        app.config['SESSION_COOKIE_SECURE'] = RelayConfig.get_bool(
            'web_secure_cookies', False)
        trust_proxy = RelayConfig.get_bool('web_trust_proxy', False)
        if trust_proxy:
            # Rewrite remote_addr (right-most X-Forwarded-For hop, i.e. the
            # one added by the single trusted proxy) and the request scheme
            # (X-Forwarded-Proto), so the login throttle keys on real client
            # IPs and url_for(_external=True)/OAuth redirects emit https.
            # x_host stays 0 — X-Forwarded-Host is never trusted.
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    @app.context_processor
    def inject_globals():
        """Make smtp_running and Role available in every template."""
        s = getattr(app, '_smtp_server', None)
        running = False
        if s is not None:
            try:
                running = s.is_running
            except Exception:
                running = False
        return dict(smtp_running=running, Role=Role)

    # ── Auth ───────────────────────────────────────────────────
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Handle user login.

        GET: Display the login form.
        POST: Validate credentials and log the user in.
        """
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        azure_ready = _azure_signin_ready()
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            if trust_proxy:
                # Key on the RIGHT-MOST X-Forwarded-For hop: the entry the
                # trusted proxy appended is the only attacker-unspoofable
                # one. The left-most hop is client-controlled, so keying on
                # it let an attacker rotate the throttle key per request.
                forwarded = request.headers.get('X-Forwarded-For', '')
                client_ip = (forwarded.split(',')[-1].strip()
                             if forwarded else request.remote_addr)
            else:
                client_ip = request.remote_addr
            key = _login_key(client_ip, username)
            if _login_throttled(key):
                flash('Too many login attempts. Try again later.', 'danger')
                return render_template('login.html',
                                       azure_enabled=azure_ready), 429
            user = User.query.filter_by(username=username).first()
            if user is not None and user.auth_source != 'local':
                # Microsoft-only account: password sign-in is not an option.
                _record_login_failure(key)
                flash('This account signs in with Microsoft 365. Use the '
                      '"Sign in with Microsoft" button above.', 'warning')
                return render_template('login.html', azure_enabled=azure_ready)
            if user is None:
                # Burn one bcrypt comparison so a nonexistent username costs
                # the same as a wrong password: real accounts pay ~100 ms of
                # bcrypt here, and without this the instant reply for an
                # unknown username leaks which usernames exist.
                global _dummy_bcrypt_hash
                if _dummy_bcrypt_hash is None:
                    _dummy_bcrypt_hash = bcrypt.hashpw(
                        b'timing-equalizer', bcrypt.gensalt(rounds=10))
                bcrypt.checkpw(b'invalid-password', _dummy_bcrypt_hash)
            if user and user.check_password(request.form.get('password', '')) and user.is_active:
                user.last_login = datetime.datetime.utcnow()
                db.session.commit()
                session.clear()  # prevent session fixation on login
                login_user(user, remember=False)
                _clear_login_failures(key)
                target = (_safe_redirect_target(request.form.get('next'))
                          or _safe_redirect_target(request.args.get('next'))
                          or url_for('dashboard'))
                return redirect(target)
            _record_login_failure(key)
            flash('Invalid username or password.', 'danger')
        return render_template('login.html', azure_enabled=azure_ready)

    def _azure_signin_ready():
        """True when Microsoft sign-in is enabled AND fully configured."""
        if not RelayConfig.get_bool('azure_enabled', False):
            return False
        return bool((RelayConfig.get('azure_tenant_id', '') or '').strip()
                    and (RelayConfig.get('azure_client_id', '') or '').strip()
                    and (RelayConfig.get('azure_client_secret', '') or '').strip())

    @app.route('/logout', methods=['POST'])
    @login_required
    def logout():
        """Log out the current user (POST-only so links/preview fetches
        cannot log the user out; templates must use a form + CSRF token)."""
        logout_user()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    # ── Dashboard ──────────────────────────────────────────────
    @app.route('/')
    @login_required
    def dashboard():
        """Display the main dashboard with statistics and recent activity.

        Shows:
        - Total sent/failed/queued emails
        - Today's and hourly statistics
        - Active domains and credentials count
        - Recent email logs
        """
        now = datetime.datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago = now - datetime.timedelta(hours=1)
        seven_days_ago = today - datetime.timedelta(days=6)

        # Aggregated queries instead of ~23 per-request COUNTs (the old
        # version issued one COUNT per stat card and two per chart day).
        daily_rows = (
            db.session.query(
                func.date(EmailLog.timestamp).label('day'),
                EmailLog.status,
                func.count(EmailLog.id),
            )
            .filter(EmailLog.timestamp >= seven_days_ago)
            .group_by('day', EmailLog.status)
            .all()
        )
        daily_map = {}
        for day, status, count in daily_rows:
            # func.date() returns datetime.date objects on PostgreSQL (it
            # returned strings on SQLite pre-v4.3.0). Normalize to the same
            # 'YYYY-MM-DD' string the lookups below use — keying the map by
            # raw date objects made every lookup miss and the dashboard
            # show 0 sent/failed today and an empty 7-day chart.
            key = str(day)
            daily_map.setdefault(key, {}).setdefault(status, 0)
            daily_map[key][status] += count

        totals = dict(
            db.session.query(EmailLog.status, func.count(EmailLog.id))
            .group_by(EmailLog.status)
            .all()
        )
        hour_rows = (
            db.session.query(EmailLog.status, func.count(EmailLog.id))
            .filter(EmailLog.timestamp >= hour_ago)
            .group_by(EmailLog.status)
            .all()
        )
        hour_counts = dict(hour_rows)

        stats = dict(
            total_sent=totals.get('sent', 0),
            total_failed=totals.get('failed', 0),
            total_queued=EmailQueue.query.filter_by(status='queued').count(),
            sent_today=daily_map.get(today.strftime('%Y-%m-%d'), {}).get('sent', 0),
            failed_today=daily_map.get(today.strftime('%Y-%m-%d'), {}).get('failed', 0),
            sent_this_hour=hour_counts.get('sent', 0),
            active_domains=AllowedDomain.query.filter_by(is_active=True).count(),
            active_credentials=SmtpCredential.query.filter_by(is_active=True).count(),
            total_users=User.query.count(),
        )

        daily = []
        for i in range(6, -1, -1):
            d = (now - datetime.timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_key = d.strftime('%Y-%m-%d')
            day_counts = daily_map.get(day_key, {})
            daily.append(dict(
                date=d.strftime('%b %d'),
                sent=day_counts.get('sent', 0),
                failed=day_counts.get('failed', 0),
            ))

        recent = EmailLog.query.order_by(EmailLog.timestamp.desc()).limit(20).all()

        return render_template('dashboard.html',
                               stats=stats, daily_stats=daily,
                               recent_logs=recent, json=json)

    # ── Configuration ──────────────────────────────────────────
    @app.route('/config', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def config():
        """Display and update relay configuration.

        GET: Show the configuration form with current values.
        POST: Update configuration values in the database.
        """
        if request.method == 'POST':
            # Validate numeric/enum fields before touching the database:
            # stored garbage would only surface later as silent get_int()
            # fallbacks (e.g. a typo'd port becoming 0 after restart).
            int_ranges = {
                'relay_port':           (1, 65535),
                'listen_port':          (1, 65535),
                'web_port':             (1, 65535),
                'web_threads':          (1, 64),
                'max_recipients':       (1, 10000),
                'global_rate_limit':    (1, 1000000),
                'log_retention_days':   (0, 3650),
                'queue_retry_interval': (1, 86400),
                'queue_max_retries':    (0, 100),
                'max_message_size':     (1024, 104857600),
            }
            for k, (lo, hi) in int_ranges.items():
                raw = (request.form.get(k) or '').strip()
                try:
                    iv = int(raw)
                except ValueError:
                    flash(f'{k} must be a whole number.', 'danger')
                    return redirect(url_for('config'))
                if iv < lo or iv > hi:
                    flash(f'{k} must be between {lo} and {hi}.', 'danger')
                    return redirect(url_for('config'))
            if request.form.get('delivery_mode') not in ('relay', 'direct'):
                flash('Delivery mode must be "relay" or "direct".', 'danger')
                return redirect(url_for('config'))
            # Protocol-facing hostnames: CR/LF here would inject into the
            # SMTP dialogue (the 220 banner line / the EHLO command line).
            for hk in ('banner_hostname', 'relay_helo_hostname'):
                hv = (request.form.get(hk) or '').strip()
                if hv and (len(hv) > 255 or not _HOSTNAME_RE.match(hv)):
                    flash(f'{hk} must be a hostname (letters, numbers, '
                          'dots, hyphens; max 255).', 'danger')
                    return redirect(url_for('config'))
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
                'azure_enabled', 'azure_tenant_id', 'azure_client_id',
                'azure_client_secret', 'azure_redirect_uri',
                'web_host', 'web_port', 'web_threads', 'web_secure_cookies',
                'web_trust_proxy',
            ]
            bools = {'relay_use_tls', 'relay_use_starttls', 'require_auth', 'enable_tls', 'debug_logging', 'direct_use_starttls', 'azure_enabled', 'web_secure_cookies', 'web_trust_proxy'}
            updates = {}
            for k in keys:
                if k in bools:
                    v = 'true' if request.form.get(k) else 'false'
                elif k in ('banner_hostname', 'relay_helo_hostname'):
                    v = (request.form.get(k) or '').strip()
                else:
                    v = request.form.get(k, '')
                if k in ('relay_auth_password', 'direct_auth_password', 'azure_client_secret'):
                    # Write-only fields: an empty submission keeps the stored
                    # value; a new value is encrypted before it is stored.
                    if not v:
                        continue
                    v = crypto_utils.encrypt_secret(v, secret_key)
                updates[k] = v
            # Single commit — the old per-key commit loop could leave a
            # half-updated config if anything failed mid-save.
            RelayConfig.set_many(updates)

            if (updates.get('azure_enabled') == 'true'
                    and not (updates.get('azure_redirect_uri') or '').strip()):
                # url_for(_external=True) can only build the callback from
                # the request's Host/scheme — behind a TLS-terminating proxy
                # (or any multi-hostname access) that mismatches the URI
                # registered in Azure and sign-in fails with a cryptic
                # AADSTS redirect error.
                flash('Microsoft sign-in is enabled without an explicit '
                      'Azure redirect URI. Behind a reverse proxy (or any '
                      'access via more than one hostname), set '
                      'azure_redirect_uri to the exact https callback URL '
                      'registered in your Azure app, or sign-in will fail '
                      'with a redirect-URI mismatch.', 'warning')

            flash(
                'Configuration saved.  Relay/sending changes take effect '
                'on the next message.  Listener and web changes require '
                'an application restart.',
                'success',
            )
            return redirect(url_for('config'))

        # Current values from the database (the only config store).
        configs = {k: RelayConfig.get(k) for k in RelayConfig.DEFAULTS}

        # Password fields are write-only: never send stored values to the template.
        configs.pop('relay_auth_password', None)
        configs.pop('direct_auth_password', None)
        configs.pop('azure_client_secret', None)

        return render_template('config.html', configs=configs)

    # ── Domains ────────────────────────────────────────────────
    @app.route('/domains')
    @login_required
    @operator_required
    def domains():
        """Display list of allowed sender domains."""
        return render_template('domains.html',
                               domains=AllowedDomain.query.order_by(AllowedDomain.domain).all())

    @app.route('/domains/add', methods=['POST'])
    @login_required
    @operator_required
    def add_domain():
        """Add a new allowed sender domain."""
        d = request.form.get('domain', '').strip().lower()
        if not d:
            flash('Domain is required.', 'danger')
            return redirect(url_for('domains'))
        if len(d) > 255 or not _DOMAIN_RE.match(d):
            flash('Invalid domain name. Use a hostname like example.com '
                  '(letters, numbers, hyphens, dots).', 'danger')
            return redirect(url_for('domains'))
        if AllowedDomain.query.filter_by(domain=d).first():
            flash(f'Domain {d} already exists.', 'warning')
            return redirect(url_for('domains'))
        description = request.form.get('description', '').strip()
        if len(description) > 500:
            flash('Description is limited to 500 characters.', 'danger')
            return redirect(url_for('domains'))
        db.session.add(AllowedDomain(domain=d,
                                     description=description,
                                     is_active=True, created_by=current_user.username))
        db.session.commit()
        flash(f'Domain {d} added.', 'success')
        return redirect(url_for('domains'))

    @app.route('/domains/<int:did>/toggle', methods=['POST'])
    @login_required
    @operator_required
    def toggle_domain(did):
        """Enable or disable an allowed domain."""
        d = AllowedDomain.query.get_or_404(did)
        d.is_active = not d.is_active
        db.session.commit()
        flash(f'Domain {d.domain} {"enabled" if d.is_active else "disabled"}.', 'success')
        return redirect(url_for('domains'))

    @app.route('/domains/<int:did>/delete', methods=['POST'])
    @login_required
    @operator_required
    def delete_domain(did):
        """Delete an allowed domain."""
        d = AllowedDomain.query.get_or_404(did)
        name = d.domain
        db.session.delete(d)
        db.session.commit()
        flash(f'Domain {name} deleted.', 'success')
        return redirect(url_for('domains'))

    # ── DKIM Keys ───────────────────────────────────────────────
    @app.route('/dkim')
    @login_required
    @operator_required
    def dkim_keys():
        """Display DKIM keys management page (Operator and above)."""
        keys = DkimKey.query.order_by(DkimKey.domain, DkimKey.created_at.desc()).all()
        return render_template('dkim.html', keys=keys)

    @app.route('/dkim/generate', methods=['POST'])
    @login_required
    @operator_required
    def generate_dkim_key():
        """Generate a new DKIM key pair for a domain."""
        domain = request.form.get('domain', '').strip().lower()
        selector = request.form.get('selector', '').strip()

        if not domain or not selector:
            flash('Domain and selector are required.', 'danger')
            return redirect(url_for('dkim_keys'))

        if len(domain) > 255 or not _DOMAIN_RE.match(domain):
            flash('Invalid domain name. Use a hostname like example.com '
                  '(letters, numbers, hyphens, dots).', 'danger')
            return redirect(url_for('dkim_keys'))

        # RFC 8616 selector grammar: letters/numbers/hyphens, must not
        # start or end with a hyphen, max 63 (it becomes part of a DNS
        # label).  isalnum() alone is Unicode-aware and would accept
        # 'café' or 'ключ', which cannot form a publishable DNS record.
        if (len(selector) > 63
                or not re.fullmatch(
                    r'[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?', selector)):
            flash('Invalid selector. Use 1-63 letters, numbers, or '
                  'hyphens (no leading/trailing hyphen).', 'danger')
            return redirect(url_for('dkim_keys'))

        if DkimKey.query.filter_by(domain=domain, selector=selector).first():
            flash(f'Key for {domain} with selector {selector} already exists.', 'warning')
            return redirect(url_for('dkim_keys'))

        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend

            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            public_key_pem = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            public_key_b64 = public_key_pem.decode('utf-8').replace(
                '-----BEGIN PUBLIC KEY-----', ''
            ).replace('-----END PUBLIC KEY-----', '').replace('\n', '')

            dkim_key = DkimKey(
                domain=domain,
                selector=selector,
                # Encrypt the private key at rest (ENC1, keyed from
                # web.secret_key) — same protection as upstream passwords.
                private_key=crypto_utils.encrypt_secret(
                    private_pem.decode('utf-8'), secret_key),
                public_key=public_key_b64,
                is_active=False,
                created_by=current_user.username,
            )
            db.session.add(dkim_key)
            db.session.commit()
            flash(f'DKIM key generated for {domain} (selector: {selector}).', 'success')
        except Exception as exc:
            flash(f'Key generation failed: {exc}', 'danger')
            logger.error('DKIM key generation failed: %s', exc)

        return redirect(url_for('dkim_keys'))

    @app.route('/dkim/<int:kid>/activate', methods=['POST'])
    @login_required
    @operator_required
    def activate_dkim_key(kid):
        """Activate a DKIM key (deactivates others for the same domain)."""
        key = DkimKey.query.get_or_404(kid)
        DkimKey.query.filter_by(domain=key.domain).update({'is_active': False})
        key.is_active = True
        db.session.commit()
        flash(f'Key for {key.domain} (selector: {key.selector}) activated.', 'success')
        return redirect(url_for('dkim_keys'))

    @app.route('/dkim/<int:kid>/deactivate', methods=['POST'])
    @login_required
    @operator_required
    def deactivate_dkim_key(kid):
        """Deactivate a DKIM key (deactivates others for the same domain)."""
        key = DkimKey.query.get_or_404(kid)
        key.is_active = False
        db.session.commit()
        flash(f'Key for {key.domain} (selector: {key.selector}) deactivated.', 'success')
        return redirect(url_for('dkim_keys'))

    @app.route('/dkim/<int:kid>/delete', methods=['POST'])
    @login_required
    @operator_required
    def delete_dkim_key(kid):
        """Delete a DKIM key."""
        key = DkimKey.query.get_or_404(kid)
        name = f'{key.domain}/{key.selector}'
        db.session.delete(key)
        db.session.commit()
        flash(f'Key {name} deleted.', 'success')
        return redirect(url_for('dkim_keys'))

    # ── SMTP Credentials ──────────────────────────────────────
    @app.route('/credentials')
    @login_required
    @operator_required
    def credentials():
        """Display list of SMTP client credentials."""
        return render_template('credentials.html',
                               credentials=SmtpCredential.query.order_by(SmtpCredential.username).all())

    @app.route('/credentials/add', methods=['POST'])
    @login_required
    @operator_required
    def add_credential():
        """Add new SMTP client credentials."""
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        if not u or not p:
            flash('Username and password are required.', 'danger')
            return redirect(url_for('credentials'))
        if not _USERNAME_RE.match(u):
            flash('Username may only contain letters, numbers, and . _ @ + \' - '
                  '(max 80 characters).', 'danger')
            return redirect(url_for('credentials'))
        if len(p) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('credentials'))
        rate_raw = request.form.get('max_sends_per_hour')
        rate = 100 if rate_raw in (None, '') else _parse_rate_limit(rate_raw)
        if rate is None:
            flash('Rate limit must be a whole number between 1 and 1000000', 'danger')
            return redirect(url_for('credentials'))
        if SmtpCredential.query.filter_by(username=u).first():
            flash(f'Credential {u} already exists.', 'warning')
            return redirect(url_for('credentials'))
        description = request.form.get('description', '').strip()
        if len(description) > 500:
            flash('Description is limited to 500 characters.', 'danger')
            return redirect(url_for('credentials'))
        c = SmtpCredential(username=u,
                           description=description,
                           is_active=True,
                           max_sends_per_hour=rate,
                           created_by=current_user.username)
        c.set_password(p)
        db.session.add(c)
        db.session.commit()
        flash(f'Credential {u} created.', 'success')
        return redirect(url_for('credentials'))

    @app.route('/credentials/<int:cid>/toggle', methods=['POST'])
    @login_required
    @operator_required
    def toggle_credential(cid):
        """Enable or disable an SMTP credential."""
        c = SmtpCredential.query.get_or_404(cid)
        c.is_active = not c.is_active
        db.session.commit()
        flash(f'Credential {c.username} {"enabled" if c.is_active else "disabled"}.', 'success')
        return redirect(url_for('credentials'))

    @app.route('/credentials/<int:cid>/delete', methods=['POST'])
    @login_required
    @operator_required
    def delete_credential(cid):
        """Delete an SMTP credential."""
        c = SmtpCredential.query.get_or_404(cid)
        name = c.username
        db.session.delete(c)
        db.session.commit()
        flash(f'Credential {name} deleted.', 'success')
        return redirect(url_for('credentials'))

    @app.route('/credentials/<int:cid>/reset-password', methods=['POST'])
    @login_required
    @operator_required
    def reset_credential_password(cid):
        """Reset password for an SMTP credential."""
        c = SmtpCredential.query.get_or_404(cid)
        pw = request.form.get('new_password', '')
        if not pw:
            flash('Password is required.', 'danger')
            return redirect(url_for('credentials'))
        if len(pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('credentials'))
        c.set_password(pw)
        db.session.commit()
        flash(f'Password reset for {c.username}.', 'success')
        return redirect(url_for('credentials'))

    # ── Users ──────────────────────────────────────────────────
    @app.route('/users')
    @login_required
    @admin_required
    def users():
        """Display list of web interface users."""
        return render_template('users.html',
                               users=User.query.order_by(User.username).all())

    @app.route('/users/add', methods=['POST'])
    @login_required
    @admin_required
    def add_user():
        """Create a new web interface user.

        auth_source selects the sign-in method: 'entra' (Microsoft 365 —
        the default; the user signs in with their Microsoft account, no
        password is stored) or 'local' (username/password break-glass,
        restricted to Admin+ accounts).
        """
        e = request.form.get('email', '').strip().lower()
        p = request.form.get('password', '')
        r = request.form.get('role', Role.VIEWER)
        auth_source = request.form.get('auth_source', 'entra')
        if auth_source not in ('local', 'entra'):
            auth_source = 'entra'
        if not e:
            flash('Email is required.', 'danger')
            return redirect(url_for('users'))
        if '@' not in e or '.' not in e.split('@')[-1] or len(e) > 80:
            # 80, not the email column's 120: Entra accounts use the email
            # as their username and users.username is VARCHAR(80) — a longer
            # address would pass validation and then fail the INSERT.
            flash('Enter a valid email address (max 80 characters).', 'danger')
            return redirect(url_for('users'))
        u = request.form.get('username', '').strip()
        if auth_source == 'entra':
            u = e  # Entra accounts are identified by their email address
        if auth_source == 'local':
            if not u or not p:
                flash('Local accounts need a username and password.', 'danger')
                return redirect(url_for('users'))
            if r not in (Role.ADMIN, Role.SUPER_ADMIN):
                flash('Local break-glass accounts must be Admin or Super '
                      'Admin (Microsoft sign-in is the normal path).', 'danger')
                return redirect(url_for('users'))
        if not _USERNAME_RE.match(u):
            flash('Username may only contain letters, numbers, and . _ @ - '
                  '(max 80 characters).', 'danger')
            return redirect(url_for('users'))
        if auth_source == 'local' and len(p) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('users'))
        if User.query.filter_by(username=u).first():
            flash(f'Username {u} exists.', 'warning')
            return redirect(url_for('users'))
        if User.query.filter(func.lower(User.email) == func.lower(e)).first():
            flash(f'Email {e} in use.', 'warning')
            return redirect(url_for('users'))
        # Prevent role escalation
        allowed = Role.assignable_roles(current_user.role)
        if r not in allowed:
            flash(f'You cannot assign the {Role.label(r)} role.', 'danger')
            return redirect(url_for('users'))
        user = User(username=u, email=e, role=r, auth_source=auth_source,
                    is_admin=(r in (Role.ADMIN, Role.SUPER_ADMIN)))
        # Entra accounts get an unusable random password: sign-in happens
        # through Microsoft, and password login is impossible by design.
        user.set_password(p if auth_source == 'local'
                          else secrets.token_urlsafe(32))
        db.session.add(user)
        db.session.commit()
        if auth_source == 'entra':
            flash(f'User {e} created (Microsoft sign-in). They sign in with '
                  f'their Microsoft account and the Sign in with Microsoft '
                  f'button.', 'success')
        else:
            flash(f'User {u} created as {Role.label(r)} (local '
                  f'break-glass sign-in).', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/toggle-auth-source', methods=['POST'])
    @login_required
    @admin_required
    def toggle_user_auth_source(uid):
        """Switch a user between Microsoft sign-in and local break-glass."""
        u = User.query.get_or_404(uid)
        if u.id == current_user.id:
            flash('You cannot change your own sign-in method.', 'danger')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u):
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        if u.auth_source == 'local' and u.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            flash('Local break-glass sign-in is restricted to Admin '
                  'accounts; this user cannot switch.', 'danger')
            return redirect(url_for('users'))
        if u.auth_source == 'local':
            u.auth_source = 'entra'
            u.set_password(secrets.token_urlsafe(32))  # unusable
            flash(f'{u.username} now signs in with Microsoft 365.', 'success')
        else:
            if u.role not in (Role.ADMIN, Role.SUPER_ADMIN):
                flash('Local break-glass sign-in is restricted to Admin '
                      'accounts.', 'danger')
                return redirect(url_for('users'))
            u.auth_source = 'local'
            u.set_password(secrets.token_urlsafe(32))
            flash(f'{u.username} switched to local sign-in — the password '
                  f'was cleared. Use Reset to set one.', 'warning')
        db.session.commit()
        return redirect(url_for('users'))

    def _is_last_active_admin(u):
        """True when *u* is the only active Admin/Super Admin account left.

        Used to block disabling/deleting the final admin, which would lock
        out all user management (bootstrap only fires on an empty table).
        """
        if not (u.role in (Role.ADMIN, Role.SUPER_ADMIN) and u.is_active_user):
            return False
        others = User.query.filter(
            User.id != u.id,
            User.is_active_user.is_(True),
            User.role.in_((Role.ADMIN, Role.SUPER_ADMIN)),
        ).count()
        return others == 0

    @app.route('/users/<int:uid>/toggle', methods=['POST'])
    @login_required
    @admin_required
    def toggle_user(uid):
        """Enable or disable a user account."""
        u = User.query.get_or_404(uid)
        if u.id == current_user.id:
            flash('Cannot disable yourself.', 'danger')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u):
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        with _user_mutation_lock:
            if u.is_active_user and _is_last_active_admin(u):
                flash('Cannot disable the last active admin account.', 'danger')
                return redirect(url_for('users'))
            u.is_active_user = not u.is_active_user
            db.session.commit()
        flash(f'User {u.username} {"enabled" if u.is_active_user else "disabled"}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/change-role', methods=['POST'])
    @login_required
    @admin_required
    def change_user_role(uid):
        """Change a user's role."""
        u = User.query.get_or_404(uid)
        new_role = request.form.get('role', '')
        if u.id == current_user.id:
            flash('Cannot change your own role.', 'danger')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u):
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        allowed = Role.assignable_roles(current_user.role)
        if new_role not in allowed:
            flash(f'You cannot assign the {Role.label(new_role)} role.', 'danger')
            return redirect(url_for('users'))
        if u.auth_source == 'local' and new_role not in (Role.ADMIN, Role.SUPER_ADMIN):
            # Keep the local⇒Admin+ invariant the other routes enforce: a
            # local (password) account is break-glass and must stay admin.
            flash('Local break-glass accounts must stay Admin or Super '
                  'Admin. Switch the account to Microsoft sign-in first.',
                  'danger')
            return redirect(url_for('users'))
        with _user_mutation_lock:
            demoting = (u.role in (Role.ADMIN, Role.SUPER_ADMIN)
                        and new_role not in (Role.ADMIN, Role.SUPER_ADMIN))
            if demoting and _is_last_active_admin(u):
                flash('Cannot demote the last active admin account.', 'danger')
                return redirect(url_for('users'))
            u.role = new_role
            u.is_admin = (new_role in (Role.ADMIN, Role.SUPER_ADMIN))
            db.session.commit()
        flash(f'User {u.username} is now {Role.label(new_role)}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/reset-password', methods=['POST'])
    @login_required
    @admin_required
    def reset_user_password(uid):
        """Reset a user's password."""
        u = User.query.get_or_404(uid)
        if u.auth_source != 'local':
            flash(f'{u.username} signs in with Microsoft 365 — there is no '
                  f'local password to reset. Switch their sign-in method '
                  f'first if needed.', 'warning')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u) and u.id != current_user.id:
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        pw = request.form.get('new_password', '')
        if not pw:
            flash('Password is required.', 'danger')
            return redirect(url_for('users'))
        if len(pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('users'))
        u.set_password(pw)
        db.session.commit()
        flash(f'Password reset for {u.username}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/delete', methods=['POST'])
    @login_required
    @admin_required
    def delete_user(uid):
        """Delete a user account."""
        u = User.query.get_or_404(uid)
        if u.id == current_user.id:
            flash('Cannot delete yourself.', 'danger')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u):
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        with _user_mutation_lock:
            if _is_last_active_admin(u):
                flash('Cannot delete the last active admin account.', 'danger')
                return redirect(url_for('users'))
            name = u.username
            db.session.delete(u)
            db.session.commit()
        flash(f'User {name} deleted.', 'success')
        return redirect(url_for('users'))

    # ── Logs ───────────────────────────────────────────────────
    @app.route('/logs')
    @login_required
    def logs():
        """Display email logs with filtering and pagination.

        Query parameters:
        - page: Page number (default 1)
        - per_page: Items per page (default 50)
        - status: Filter by status (sent/failed/queued)
        - sender: Filter by sender email
        - search: Search in sender, recipients, or subject
        """
        page = max(request.args.get('page', 1, type=int), 1)
        # Cap per_page: unbounded values from the query string let one
        # request render the whole table (memory/CPU spike).
        per_page = min(max(request.args.get('per_page', 50, type=int), 1), 200)
        status_f = request.args.get('status', '')
        sender_f = request.args.get('sender', '')
        search = request.args.get('search', '')

        q = EmailLog.query
        if status_f:
            q = q.filter_by(status=status_f)
        if sender_f:
            q = q.filter(EmailLog.sender.ilike(f'%{sender_f}%'))
        if search:
            q = q.filter(db.or_(
                EmailLog.sender.ilike(f'%{search}%'),
                EmailLog.recipients.ilike(f'%{search}%'),
                EmailLog.subject.ilike(f'%{search}%'),
            ))

        pag = q.order_by(EmailLog.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False)

        return render_template('logs.html', logs=pag.items, pagination=pag,
                               status_filter=status_f, sender_filter=sender_f,
                               search=search, json=json)

    # ── Queue ──────────────────────────────────────────────────
    @app.route('/queue')
    @login_required
    def queue():
        """Display the email queue with queued, processing, and failed messages."""
        # Defer raw_message: the page never displays the stored bytes, and
        # deferred loading keeps a few thousand retained rows (failed rows
        # are kept forever by design) from dragging every LargeBinary blob
        # into memory on each page view.
        return render_template('queue.html',
            queued=EmailQueue.query.options(db.defer(EmailQueue.raw_message)).filter_by(status='queued').order_by(EmailQueue.next_retry_at).all(),
            processing=EmailQueue.query.options(db.defer(EmailQueue.raw_message)).filter_by(status='processing').all(),
            failed=EmailQueue.query.options(db.defer(EmailQueue.raw_message)).filter_by(status='failed').order_by(EmailQueue.created_at.desc()).all(),
            json=json)

    @app.route('/queue/<int:qid>/retry', methods=['POST'])
    @login_required
    @queue_manager_required
    def retry_queue(qid):
        """Manually retry a failed queue entry."""
        now = datetime.datetime.utcnow()
        # Atomic guard: only a 'failed' row may be requeued. Requeuing a row
        # a delivery thread currently holds as 'processing' would let two
        # senders race on the same message (double delivery).
        updated = EmailQueue.query.filter(
            EmailQueue.id == qid,
            EmailQueue.status == 'failed',
        ).update(
            {'status': 'queued', 'retry_count': 0, 'next_retry_at': now},
            synchronize_session=False,
        )
        if not updated:
            flash('Only failed entries can be requeued — this entry may '
                  'already be queued, in flight, or delivered.', 'warning')
            return redirect(url_for('queue'))
        log_id = db.session.query(EmailQueue.log_id).filter(
            EmailQueue.id == qid).scalar()
        if log_id:
            log_entry = EmailLog.query.get(log_id)
            if log_entry:
                log_entry.status = 'queued'
                log_entry.status_message = 'Manually requeued'
        db.session.commit()
        flash('Requeued.', 'success')
        return redirect(url_for('queue'))

    @app.route('/queue/<int:qid>/delete', methods=['POST'])
    @login_required
    @queue_manager_required
    def delete_queue(qid):
        """Delete a queue entry."""
        db.session.delete(EmailQueue.query.get_or_404(qid))
        db.session.commit()
        flash('Deleted.', 'success')
        return redirect(url_for('queue'))

    @app.route('/queue/retry-all', methods=['POST'])
    @login_required
    @queue_manager_required
    def retry_all_failed():
        """Retry all failed queue entries."""
        now = datetime.datetime.utcnow()
        # Atomic guard — mirror retry_queue: collect the candidate ids
        # first, then bulk-update only rows still 'failed'. The old
        # read-modify-write could clobber a row the delivery worker
        # claimed (or that another admin requeued) between SELECT and
        # commit, resurrecting an already-sent message for redelivery.
        failed = (db.session.query(EmailQueue.id, EmailQueue.log_id)
                  .filter(EmailQueue.status == 'failed').all())
        if not failed:
            flash('No failed messages to requeue.', 'info')
            return redirect(url_for('queue'))
        ids = [r.id for r in failed]
        log_ids = [r.log_id for r in failed if r.log_id]
        updated = EmailQueue.query.filter(
            EmailQueue.id.in_(ids),
            EmailQueue.status == 'failed',
        ).update(
            {'status': 'queued', 'retry_count': 0, 'next_retry_at': now},
            synchronize_session=False,
        )
        for log_id in log_ids:
            log = EmailLog.query.get(log_id)
            if log and log.status == 'failed':
                log.status = 'queued'
                log.status_message = 'Manually requeued (retry all)'
        db.session.commit()
        flash(f'Requeued {updated} failed message{"s" if updated != 1 else ""} for delivery.', 'success')
        return redirect(url_for('queue'))

    @app.route('/queue/flush', methods=['POST'])
    @login_required
    @queue_manager_required
    def flush_queue():
        """Permanently delete all failed queue entries."""
        n = EmailQueue.query.filter(EmailQueue.status.in_(['failed'])).delete(synchronize_session=False)
        db.session.commit()
        flash(f'Deleted {n} failed entr{"ies" if n != 1 else "y"} permanently.', 'success')
        return redirect(url_for('queue'))

    # ── Server control ─────────────────────────────────────────
    @app.route('/server/restart', methods=['POST'])
    @login_required
    @admin_required
    def restart_server():
        """Restart the SMTP server."""
        s = getattr(app, '_smtp_server', None)
        if s:
            try:
                s.restart()
                flash('SMTP server restarted.', 'success')
            except Exception as exc:
                flash(f'Restart error: {exc}', 'danger')
        else:
            flash('SMTP server component is not attached to this web '
                  'process (started outside run.py?).', 'danger')
        return redirect(url_for('dashboard'))

    @app.route('/server/stop', methods=['POST'])
    @login_required
    @admin_required
    def stop_server():
        """Stop the SMTP server."""
        s = getattr(app, '_smtp_server', None)
        if s:
            s.stop()
            flash('SMTP server stopped.', 'warning')
        else:
            flash('SMTP server component is not attached to this web '
                  'process (started outside run.py?).', 'danger')
        return redirect(url_for('dashboard'))

    @app.route('/server/start', methods=['POST'])
    @login_required
    @admin_required
    def start_server():
        """Start the SMTP server."""
        s = getattr(app, '_smtp_server', None)
        if s:
            try:
                s.start()
                flash('SMTP server started.', 'success')
            except Exception as exc:
                flash(f'Start error: {exc}', 'danger')
        else:
            flash('SMTP server component is not attached to this web '
                  'process (started outside run.py?).', 'danger')
        return redirect(url_for('dashboard'))

    # ── API ────────────────────────────────────────────────────
    @app.route('/api/stats')
    @login_required
    def api_stats():
        """Get current statistics as JSON.

        Returns:
        - sent_today: Emails sent today
        - failed_today: Emails failed today
        - sent_this_hour: Emails sent this hour
        - queued: Number of queued messages
        - smtp_running: Whether SMTP server is running
        """
        # Evaluate smtp_running ONCE, safely, up front: a broken server
        # handle must degrade to running=False, never to a 500.
        s = getattr(app, '_smtp_server', None)
        running = False
        if s is not None:
            try:
                running = bool(s.is_running)
            except Exception:
                running = False
        now = datetime.datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago = now - datetime.timedelta(hours=1)
        try:
            stats = dict(
                sent_today=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= today).count(),
                failed_today=EmailLog.query.filter(EmailLog.status == 'failed', EmailLog.timestamp >= today).count(),
                sent_this_hour=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= hour_ago).count(),
                queued=EmailQueue.query.filter_by(status='queued').count(),
                smtp_running=running,
            )
            return jsonify(**stats)
        except Exception as exc:
            logger.warning('api/stats query failed: %s', exc)
            return jsonify(
                sent_today=0, failed_today=0, sent_this_hour=0,
                queued=0, smtp_running=running,
                error=True,
            ), 200  # return 200 so the JS poller doesn't treat it as a hard failure

    @app.route('/api/logs/recent')
    @login_required
    def api_recent_logs():
        """Get recent email logs as JSON (last 20 entries)."""
        rows = EmailLog.query.order_by(EmailLog.timestamp.desc()).limit(20).all()
        return jsonify([dict(
            id=r.id,
            timestamp=r.timestamp.isoformat() if r.timestamp else '',
            sender=r.sender, recipients=r.recipients,
            subject=r.subject, status=r.status,
            status_message=r.status_message,
        ) for r in rows])

    @app.route('/api/logs/<int:log_id>/detail')
    @login_required
    def api_log_detail(log_id):
        """Get detailed information about a specific log entry."""
        log = EmailLog.query.get_or_404(log_id)
        recips = []
        try:
            recips = json.loads(log.recipients) if log.recipients else []
        except (json.JSONDecodeError, TypeError):
            recips = [log.recipients] if log.recipients else []
        return jsonify(
            id=log.id,
            timestamp=log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '',
            sender=log.sender,
            recipients=recips,
            subject=log.subject or '(no subject)',
            size_bytes=log.size_bytes or 0,
            status=log.status,
            status_message=log.status_message or '',
            smtp_credential=log.smtp_credential or '',
            source_ip=log.source_ip or '',
            relay_server=log.relay_server or '',
            message_id=log.message_id or '',
            retry_count=log.retry_count or 0,
            raw_headers=log.raw_headers or '',
        )

    # ── Profile ────────────────────────────────────────────────
    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        """User profile page for changing password and email.

        GET: Display profile form.
        POST: Update password or email based on action.
        """
        if request.method == 'POST':
            act = request.form.get('action')
            if act == 'change_password':
                cur = request.form.get('current_password', '')
                new = request.form.get('new_password', '')
                cfm = request.form.get('confirm_password', '')
                if not current_user.check_password(cur):
                    flash('Current password incorrect.', 'danger')
                elif new != cfm:
                    flash('Passwords do not match.', 'danger')
                elif len(new) < 8:
                    flash('Min 8 characters.', 'danger')
                else:
                    current_user.set_password(new)
                    db.session.commit()
                    flash('Password changed.', 'success')
            elif act == 'update_email':
                em = request.form.get('email', '').strip().lower()
                if em and em != current_user.email:
                    if ('@' not in em or '.' not in em.split('@')[-1]
                            or len(em) > 120):
                        flash('Enter a valid email address.', 'danger')
                    elif User.query.filter(func.lower(User.email) == func.lower(em)).first():
                        flash('Email in use.', 'danger')
                    else:
                        current_user.email = em
                        db.session.commit()
                        flash('Email updated.', 'success')
            return redirect(url_for('profile'))
        return render_template('profile.html')

    # ── Errors ─────────────────────────────────────────────────
    def _render_error(code, message):
        """Render error page; use standalone template if user is not logged in."""
        if current_user.is_authenticated:
            return render_template('error.html', code=code, message=message), code
        return render_template('error_standalone.html', code=code, message=message), code

    @app.errorhandler(403)
    def err403(e):
        return _render_error(403, 'Access Denied')

    @app.errorhandler(404)
    def err404(e):
        return _render_error(404, 'Page Not Found')

    @app.errorhandler(400)
    def err400(e):
        """CSRF failures (expired/missing token) arrive as 400 — give them
        the friendly error page instead of Werkzeug's raw HTML."""
        msg = str(getattr(e, 'description', '') or 'Bad Request')
        if 'CSRF' in msg:
            msg = ('Your session expired or the form was stale — go back, '
                   'reload the page, and try again.')
        return _render_error(400, msg)

    @app.errorhandler(500)
    def err500(e):
        try:
            return _render_error(500, 'Internal Server Error')
        except Exception:
            return '<h1>500 Internal Server Error</h1>', 500

    return app


def _resolve_secret_key_db() -> str:
    """Secret key for the database-backed configuration.

    The key is STORED in the database (relay_config.web_secret_key) and
    from then on always wins — even if SECRET_KEY env later differs —
    because rotating it invalidates every ENC1 secret. On a FRESH
    database the SECRET_KEY environment variable seeds it; if that is
    missing or weak, a random key is generated.
    """
    stored = RelayConfig.get('web_secret_key', '')
    if stored:
        env_key = (os.environ.get('SECRET_KEY') or '').strip()
        if env_key and env_key != stored:
            logger.warning('SECRET_KEY env differs from the stored key — '
                           'keeping the stored key (sessions and ENC1 '
                           'secrets depend on it). Update web_secret_key '
                           'in the database to rotate deliberately.')
        return stored

    env_key = (os.environ.get('SECRET_KEY') or '').strip()
    if env_key and env_key.lower() not in _WEAK_SECRET_KEYS:
        RelayConfig.set('web_secret_key', env_key)
        logger.warning('stored the secret_key in the database '
                       '(from SECRET_KEY env)')
        return env_key

    new_key = secrets.token_hex(32)
    RelayConfig.set('web_secret_key', new_key)
    logger.warning('generated a new secret_key and stored it in the database')
    return new_key


def _migrate_schema():
    """Idempotently add missing email_logs columns to existing databases.

    Databases created before v2.2.0 lack raw_headers and message_id; without
    this migration every inbound message fails when the SMTP server tries to
    record those fields.  Uses the dialect-agnostic SQLAlchemy inspector +
    ALTER TABLE so it is safe to run on every boot.
    """
    from sqlalchemy import inspect as sa_inspect
    columns = {c['name'] for c in sa_inspect(db.engine).get_columns('email_logs')}
    if 'raw_headers' not in columns:
        logger.info('Migrating email_logs table: adding raw_headers column …')
        db.session.execute(
            db.text("ALTER TABLE email_logs ADD COLUMN raw_headers TEXT")
        )
        db.session.commit()
        logger.info('Migration complete: raw_headers column added to email_logs')
    if 'message_id' not in columns:
        logger.info('Migrating email_logs table: adding message_id column …')
        db.session.execute(
            db.text("ALTER TABLE email_logs ADD COLUMN message_id VARCHAR(255)")
        )
        db.session.commit()
        logger.info('Migration complete: message_id column added to email_logs')
    # email_queue.log_id had no index before v4.4.0.  PostgreSQL does not
    # auto-index FK columns, so every retention delete on email_logs (and
    # any queue↔log join) forced a sequential scan of email_queue.  Run on
    # every boot; IF NOT EXISTS makes it idempotent, and the name matches
    # what create_all generates for fresh databases from index=True.
    db.session.execute(db.text(
        'CREATE INDEX IF NOT EXISTS ix_email_queue_log_id '
        'ON email_queue (log_id)'))
    db.session.commit()


def _migrate_roles():
    """Migrate existing databases: add 'role' column if missing,
    and set roles based on the old is_admin flag."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    columns = [c['name'] for c in inspector.get_columns('users')]
    if 'role' not in columns:
        logger.info('Migrating users table: adding role column …')
        db.session.execute(
            db.text("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'viewer' NOT NULL")
        )
        db.session.commit()
        logger.info('Migration complete: role column added to users')
    # Backfill admin roles from the legacy is_admin flag — on EVERY boot,
    # so a database broken by the old "is_admin = 1" comparison (invalid on
    # PostgreSQL, where boolean ≠ integer) self-heals instead of leaving
    # every admin silently demoted to viewer.
    db.session.execute(
        db.text("UPDATE users SET role = 'super_admin' "
                "WHERE is_admin AND role NOT IN ('admin', 'super_admin')")
    )
    db.session.commit()


def _migrate_oauth():
    """Idempotently add Microsoft sign-in columns to the users table.

    v3.0.2 and earlier databases only had password sign-in.  Every
    existing account migrates to auth_source='local' so the upgrade can
    never lock anyone out; accounts are moved to Entra sign-in explicitly
    (Users page) or created as Entra accounts from then on.
    """
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    columns = [c['name'] for c in inspector.get_columns('users')]
    changed = False
    if 'auth_source' not in columns:
        logger.info('Migrating users table: adding auth_source column …')
        db.session.execute(
            db.text("ALTER TABLE users ADD COLUMN auth_source VARCHAR(20) "
                    "DEFAULT 'local' NOT NULL")
        )
        changed = True
    if 'entra_oid' not in columns:
        logger.info('Migrating users table: adding entra_oid column …')
        db.session.execute(
            db.text("ALTER TABLE users ADD COLUMN entra_oid VARCHAR(64)")
        )
        changed = True
    if changed:
        db.session.commit()
        logger.info('Microsoft sign-in migration complete: existing '
                    'accounts keep local sign-in')


def _ensure_admin_exists():
    """Create the initial admin user with a random password if none exist."""
    if User.query.count() == 0:
        password = secrets.token_urlsafe(10)
        a = User(username='admin', email='admin@localhost',
                 role=Role.SUPER_ADMIN, is_admin=True,
                 auth_source='local')  # break-glass account
        a.set_password(password)
        db.session.add(a)
        db.session.commit()
        logger.warning(
            "Created initial admin user 'admin' with password: %s "
            "— change it after first login.", password
        )


def _protect_upstream_passwords_on_startup(cfg: dict, secret_key: str):
    """Encrypt upstream secrets in relay_config (idempotent, every boot).

    Scans the three reversibly-encrypted config keys (relay/direct auth
    passwords, Azure client secret) and encrypts any PLAINTEXT value into
    an ENC1 envelope in place.  This used to run only when the bootstrap
    config carried the legacy config.json sections — which happens once,
    on first boot — so a crash after the env seed but before this call
    left the upstream password plaintext in the database FOREVER (no
    later boot ever revisited it).  Scanning instead of section-matching
    closes that window and also catches legacy plaintext rows from
    pre-ENC1 upgrades.

    Values already encrypted (``ENC1:``), the legacy '[ENCRYPTED]'
    placeholder, and legacy bcrypt hashes (unrecoverable one-ways) are
    left untouched.  Encryption is strictly in place, so the DB-wins
    rule (never overwrite a customized database value with env) holds.
    """
    del cfg  # legacy parameter, kept for call compatibility
    for key in ('relay_auth_password', 'direct_auth_password',
                'azure_client_secret'):
        stored = RelayConfig.get(key, '') or ''
        if (not stored
                or stored.startswith(crypto_utils.ENC_PREFIX)
                or stored == crypto_utils.LEGACY_PLACEHOLDER
                or crypto_utils.BCRYPT_SHAPE_RE.match(stored)):
            continue  # already protected, placeholder, or unusable legacy hash

        try:
            encrypted = crypto_utils.encrypt_secret(stored, secret_key)
        except Exception as exc:
            logger.error('Failed to encrypt %s: %s', key, exc)
            continue
        RelayConfig.set(key, encrypted)
        logger.info('%s encrypted (ENC1) and stored in the database', key)


def _protect_dkim_keys_on_startup(secret_key: str):
    """Encrypt plaintext DKIM private keys at rest (one-time migration).

    v3.0.1 and earlier stored DKIM private keys as plaintext PEM in the
    dkim_keys table.  v3.0.2 stores them as ENC1 envelopes keyed from
    web.secret_key (same scheme as upstream passwords).  This migration is
    idempotent: rows already holding an ENC1 envelope are left alone, and
    decrypt_secret passes legacy plaintext through unchanged for signing.
    """
    try:
        plain_keys = [k for k in DkimKey.query.all()
                      if not (k.private_key or '').startswith(crypto_utils.ENC_PREFIX)]
        if not plain_keys:
            return
        for key in plain_keys:
            key.private_key = crypto_utils.encrypt_secret(
                key.private_key, secret_key)
        db.session.commit()
        logger.info('Encrypted %s DKIM private key(s) at rest (ENC1)',
                    len(plain_keys))
    except Exception as exc:
        db.session.rollback()
        logger.error('DKIM key encryption migration failed: %s', exc)
