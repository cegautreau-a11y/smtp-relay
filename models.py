"""
Database models for the SMTP Mail Relay.
Version 4.4.4

Designed and built by Christopher McGrath
"""

import datetime
from sqlalchemy import case
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import bcrypt

db = SQLAlchemy()


def normalize_database_url(url: str) -> str:
    """Apply safe libpq defaults to PostgreSQL connection URLs.

    gssencmode=disable avoids the 30-second (sometimes indefinite) GSSAPI
    stall macOS libpq hits on fresh connections, and connect_timeout bounds
    dead-server waits. Values already present in the URL always win, so an
    operator can still opt into GSS encryption explicitly.
    Non-PostgreSQL URLs are returned unchanged.
    """
    if not url or not url.strip().lower().startswith(('postgresql', 'postgres:')):
        return url
    from sqlalchemy.engine import make_url
    u = make_url(url.strip())
    query = dict(u.query)
    if 'gssencmode' not in query:
        query['gssencmode'] = 'disable'
    if 'connect_timeout' not in query:
        query['connect_timeout'] = '10'
    return u.set(query=query).render_as_string(hide_password=False)


# ── Role constants ─────────────────────────────────────────────
class Role:
    """User role hierarchy.  Higher number = more privileges."""
    VIEWER = 'viewer'
    OPERATOR = 'operator'
    ADMIN = 'admin'
    SUPER_ADMIN = 'super_admin'

    ALL = [VIEWER, OPERATOR, ADMIN, SUPER_ADMIN]
    LABELS = {
        VIEWER: 'Viewer',
        OPERATOR: 'Operator',
        ADMIN: 'Admin',
        SUPER_ADMIN: 'Super Admin',
    }
    BADGE_CLASSES = {
        VIEWER: 'badge-viewer',
        OPERATOR: 'badge-operator',
        ADMIN: 'badge-admin',
        SUPER_ADMIN: 'badge-super-admin',
    }
    # Numeric weight for comparison (higher = more powerful)
    WEIGHT = {
        VIEWER: 0,
        OPERATOR: 1,
        ADMIN: 2,
        SUPER_ADMIN: 3,
    }

    @classmethod
    def label(cls, role):
        return cls.LABELS.get(role, role)

    @classmethod
    def badge_class(cls, role):
        return cls.BADGE_CLASSES.get(role, 'badge-viewer')

    @classmethod
    def weight(cls, role):
        return cls.WEIGHT.get(role, -1)

    @classmethod
    def can_manage(cls, actor_role, target_role):
        """Return True if actor_role is allowed to manage target_role."""
        if actor_role == cls.SUPER_ADMIN:
            return True
        if actor_role == cls.ADMIN:
            return cls.weight(target_role) < cls.weight(cls.ADMIN)
        return False

    @classmethod
    def assignable_roles(cls, actor_role):
        """Return the list of roles the actor is allowed to assign."""
        if actor_role == cls.SUPER_ADMIN:
            return cls.ALL[:]
        if actor_role == cls.ADMIN:
            return [cls.VIEWER, cls.OPERATOR]
        return []


class User(UserMixin, db.Model):
    """Web interface user accounts."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default=Role.VIEWER, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    # Sign-in method: 'local' (username/password, break-glass admins) or
    # 'entra' (Microsoft 365 OIDC). Existing rows migrate to 'local' so an
    # upgrade never locks anyone out; new accounts default to Entra in the UI.
    auth_source = db.Column(db.String(20), default='local', nullable=False)
    entra_oid = db.Column(db.String(64), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    @property
    def is_active(self):
        return self.is_active_user

    @property
    def role_label(self):
        return Role.label(self.role)

    @property
    def role_badge_class(self):
        return Role.badge_class(self.role)

    def has_role(self, minimum_role):
        """Check if user has at least the given role level."""
        return Role.weight(self.role) >= Role.weight(minimum_role)

    def can_manage_user(self, target_user):
        """Check if this user can manage (edit/delete) the target user."""
        if self.id == target_user.id:
            return False  # can't manage yourself (except profile)
        return Role.can_manage(self.role, target_user.role)

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(
            password.encode('utf-8'),
            self.password_hash.encode('utf-8')
        )

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'


class AllowedDomain(db.Model):
    """Domains allowed to send through the relay."""
    __tablename__ = 'allowed_domains'

    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    created_by = db.Column(db.String(80), nullable=True)

    def __repr__(self):
        return f'<AllowedDomain {self.domain}>'


class SmtpCredential(db.Model):
    """SMTP authentication credentials for clients connecting to this relay."""
    __tablename__ = 'smtp_credentials'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    max_sends_per_hour = db.Column(db.Integer, default=100, nullable=False)
    sends_this_hour = db.Column(db.Integer, default=0, nullable=False)
    hour_reset_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    created_by = db.Column(db.String(80), nullable=True)

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(
            password.encode('utf-8'),
            self.password_hash.encode('utf-8')
        )

    def check_rate_limit(self) -> bool:
        """True when this credential may send another message this hour.

        The hourly-window reset is applied ATOMICALLY (single guarded
        UPDATE) and committed immediately: the old in-place mutation was
        never committed on the MAIL path (the reset silently vanished
        with the request's session) and the check-then-act pattern let
        concurrent sessions all pass at ``sends == max - 1``.
        """
        now = datetime.datetime.utcnow()
        window_elapsed = (SmtpCredential.hour_reset_at.is_(None)) | (
            SmtpCredential.hour_reset_at <= now)
        SmtpCredential.query.filter(
            SmtpCredential.id == self.id,
            window_elapsed,
        ).update(
            {'sends_this_hour': 0,
             'hour_reset_at': now + datetime.timedelta(hours=1)},
            synchronize_session=False,
        )
        db.session.commit()
        db.session.expire(self)   # re-read the atomically-updated counter
        return self.sends_this_hour < self.max_sends_per_hour

    def increment_send_count(self):
        """Atomically bump the hourly counter (window-reset aware).

        One UPDATE with a CASE keeps reset-and-increment together, so N
        concurrent deliveries produce N real increments instead of N
        read-modify-write races that all write the same stale value.
        """
        now = datetime.datetime.utcnow()
        window_elapsed = (SmtpCredential.hour_reset_at.is_(None)) | (
            SmtpCredential.hour_reset_at <= now)
        SmtpCredential.query.filter(
            SmtpCredential.id == self.id,
        ).update(
            {
                'sends_this_hour': case(
                    (window_elapsed, 1),
                    else_=SmtpCredential.sends_this_hour + 1,
                ),
                'hour_reset_at': case(
                    (window_elapsed, now + datetime.timedelta(hours=1)),
                    else_=SmtpCredential.hour_reset_at,
                ),
            },
            synchronize_session=False,
        )
        db.session.commit()

    def __repr__(self):
        return f'<SmtpCredential {self.username}>'


class EmailLog(db.Model):
    """Log of all emails processed by the relay."""
    __tablename__ = 'email_logs'

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    sender = db.Column(db.String(255), nullable=False, index=True)
    recipients = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(500), nullable=True)
    size_bytes = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), nullable=False, index=True)
    status_message = db.Column(db.Text, nullable=True)
    smtp_credential = db.Column(db.String(120), nullable=True)
    source_ip = db.Column(db.String(45), nullable=True)
    relay_server = db.Column(db.String(255), nullable=True)
    retry_count = db.Column(db.Integer, default=0)
    message_id = db.Column(db.String(255), nullable=True)
    raw_headers = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<EmailLog {self.id} {self.status}>'


class RelayConfig(db.Model):
    """Relay server configuration stored in database.

    The database is THE configuration store: on a fresh database the
    environment variables seed the rows once, after which the web UI
    overrides them at runtime and the database remains the single source
    of truth (read fresh per SMTP command).
    """
    __tablename__ = 'relay_config'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow,
                           onupdate=datetime.datetime.utcnow)

    DEFAULTS = {
        'relay_host':            ('localhost',  'Destination relay server hostname'),
        'relay_port':            ('25',         'Destination relay server port'),
        'relay_use_tls':         ('false',      'Use implicit TLS to destination relay'),
        'relay_use_starttls':    ('true',       'Use STARTTLS to destination relay'),
        'relay_auth_user':       ('',           'Auth username for destination relay (empty = no auth)'),
        'relay_auth_password':   ('',           'Auth password for destination relay (empty = no auth)'),
        'delivery_mode':         ('relay',      'Delivery mode: relay or direct'),
        'direct_use_starttls':   ('false',      'Use STARTTLS for direct delivery'),
        'direct_auth_user':      ('',           'AUTH user for direct delivery'),
        'direct_auth_password':  ('',           'AUTH password for direct delivery'),
        'relay_helo_hostname':   ('localhost',  'HELO/EHLO hostname used when sending to destination'),
        'listen_host':           ('0.0.0.0',    'SMTP listener bind address'),
        'listen_port':           ('2525',       'SMTP listener port'),
        'banner_hostname':       ('relay.local','Hostname shown in the SMTP banner'),
        'require_auth':          ('true',       'Require SMTP AUTH from clients'),
        'max_message_size':      ('26214400',   'Max message size in bytes'),
        'max_recipients':        ('100',        'Max recipients per message'),
        'global_rate_limit':     ('1000',       'Max emails per hour (global)'),
        'enable_tls':            ('false',      'Enable TLS on the listener'),
        'tls_cert_path':         ('',           'Path to TLS certificate'),
        'tls_key_path':          ('',           'Path to TLS private key'),
        'allowed_source_ips':    ('',           'Comma-separated allowed source IPs'),
        'log_retention_days':    ('30',         'Days to keep email logs'),
        'queue_retry_interval':  ('300',        'Seconds between retries'),
        'queue_max_retries':     ('3',          'Max delivery attempts (initial send + retries)'),
        'debug_logging':         ('false',      'Enable detailed debug logging'),
        'web_host':              ('0.0.0.0',    'Web UI bind address (restart required)'),
        'web_port':              ('8025',       'Web UI port (restart required)'),
        'web_threads':           ('4',          'Web UI worker threads (restart required)'),
        'web_secure_cookies':    ('false',      'Set the session cookie Secure flag (restart required)'),
        'web_trust_proxy':       ('false',      'Key the login throttle on X-Forwarded-For (enable only behind a trusted proxy)'),
        'azure_enabled':         ('false',      'Enable Microsoft Entra ID sign-in for the web UI'),
        'azure_tenant_id':       ('',           'Microsoft Entra tenant ID (GUID)'),
        'azure_client_id':       ('',           'Microsoft Entra app registration (client) ID'),
        'azure_client_secret':   ('',           'Microsoft Entra client secret (stored ENC1-encrypted)'),
        'azure_redirect_uri':    ('',           'OAuth redirect URI registered in Azure (empty = derived from the request)'),
    }

    # ── helpers ────────────────────────────────────────────────
    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        if row:
            return row.value
        if key in cls.DEFAULTS:
            return cls.DEFAULTS[key][0]
        return default

    @classmethod
    def get_int(cls, key, default=0):
        try:
            return int(cls.get(key))
        except (TypeError, ValueError):
            return default

    @classmethod
    def get_bool(cls, key, default=False):
        val = cls.get(key)
        if val is None:
            return default
        return str(val).lower() in ('true', '1', 'yes', 'on')

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = str(value)
        else:
            desc = cls.DEFAULTS.get(key, ('', ''))[1]
            row = cls(key=key, value=str(value), description=desc)
            db.session.add(row)
        db.session.commit()

    @classmethod
    def initialize_defaults(cls):
        for key, (value, description) in cls.DEFAULTS.items():
            if not cls.query.filter_by(key=key).first():
                db.session.add(cls(key=key, value=value, description=description))
        db.session.commit()

    @classmethod
    def set_many(cls, values: dict):
        """Upsert multiple keys with a SINGLE commit.

        The old per-key commit (cls.set in a loop) left the config
        half-updated when anything failed mid-loop; one commit makes a
        config save all-or-nothing.
        """
        keys = list(values.keys())
        rows = {r.key: r for r in cls.query.filter(cls.key.in_(keys)).all()} if keys else {}
        for key, raw_value in values.items():
            if isinstance(raw_value, bool):
                value = 'true' if raw_value else 'false'
            else:
                value = str(raw_value)
            row = rows.get(key)
            if row:
                row.value = value
            else:
                desc = cls.DEFAULTS.get(key, ('', ''))[1]
                db.session.add(cls(key=key, value=value, description=desc))
        db.session.commit()

    @classmethod
    def load_from_dict(cls, flat: dict):
        """Merge a flat {key: value} dict into the database (one commit)."""
        cls.set_many(flat)

    def __repr__(self):
        return f'<RelayConfig {self.key}={self.value}>'


class DkimKey(db.Model):
    """DKIM signing keys for domains.

    Stores RSA key pairs for DKIM signing. Each domain can have
    multiple keys with different selectors, but only one can be active.
    """
    __tablename__ = 'dkim_keys'

    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), nullable=False)
    selector = db.Column(db.String(63), nullable=False)
    private_key = db.Column(db.Text, nullable=False)
    public_key = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    created_by = db.Column(db.String(120), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('domain', 'selector', name='uix_domain_selector'),
    )

    @classmethod
    def get_active_key(cls, domain: str) -> 'DkimKey | None':
        """Get the active DKIM key for a domain."""
        return cls.query.filter_by(domain=domain.lower(), is_active=True).first()

    def get_private_key_pem(self, secret_key) -> str | None:
        """Return the usable PEM private key, decrypting ENC1 envelopes.

        Keys written by v3.0.2+ are stored Fernet-encrypted (ENC1:) keyed
        from web.secret_key; legacy rows hold plaintext PEM and pass
        through unchanged (the startup migration encrypts them).
        """
        from crypto_utils import decrypt_secret
        return decrypt_secret(self.private_key, secret_key)

    def get_dns_record(self) -> str:
        """Return the DNS TXT record value for this DKIM key."""
        return f'v=DKIM1; k=rsa; p={self.public_key}'


class EmailQueue(db.Model):
    """Queue for emails pending delivery or retry."""
    __tablename__ = 'email_queue'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    next_retry_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    retry_count = db.Column(db.Integer, default=0)
    sender = db.Column(db.String(255), nullable=False)
    recipients = db.Column(db.Text, nullable=False)
    raw_message = db.Column(db.LargeBinary, nullable=False)
    status = db.Column(db.String(20), default='queued', index=True)
    last_error = db.Column(db.Text, nullable=True)
    log_id = db.Column(db.Integer, db.ForeignKey('email_logs.id'),
                       nullable=True, index=True)

    def __repr__(self):
        return f'<EmailQueue {self.id} {self.status}>'
