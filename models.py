"""
Database models for the SMTP Mail Relay.

Designed and built by Christopher McGrath
"""

# Author: Christopher McGrath

import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import bcrypt

db = SQLAlchemy()


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

    def check_rate_limit(self):
        now = datetime.datetime.utcnow()
        if self.hour_reset_at is None or now >= self.hour_reset_at:
            self.sends_this_hour = 0
            self.hour_reset_at = now + datetime.timedelta(hours=1)
        return self.sends_this_hour < self.max_sends_per_hour

    def increment_send_count(self):
        now = datetime.datetime.utcnow()
        if self.hour_reset_at is None or now >= self.hour_reset_at:
            self.sends_this_hour = 1
            self.hour_reset_at = now + datetime.timedelta(hours=1)
        else:
            self.sends_this_hour += 1

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

    def __repr__(self):
        return f'<EmailLog {self.id} {self.status}>'


class RelayConfig(db.Model):
    """Relay server configuration stored in database.

    Values are seeded from config.json on startup.  The web UI can
    override them at runtime; the overrides live in the database and
    take precedence until the next time config.json is re-loaded.
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
        'relay_use_starttls':    ('false',      'Use STARTTLS to destination relay'),
        'relay_auth_user':       ('',           'Auth username for destination relay (empty = no auth)'),
        'relay_auth_password':   ('',           'Auth password for destination relay (empty = no auth)'),
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
        'queue_max_retries':     ('3',          'Max delivery retry attempts'),
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
    def load_from_dict(cls, flat: dict):
        """Merge a flat {key: value} dict into the database."""
        for key, value in flat.items():
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            cls.set(key, str(value))

    def __repr__(self):
        return f'<RelayConfig {self.key}={self.value}>'


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
    log_id = db.Column(db.Integer, db.ForeignKey('email_logs.id'), nullable=True)

    def __repr__(self):
        return f'<EmailQueue {self.id} {self.status}>'