"""
Flask web application for SMTP Relay management.

Designed and built by Christopher McGrath

Loads settings from config.json (passed in via create_app).
User accounts are managed exclusively through the web UI.
"""

# Author: Christopher McGrath

import datetime
import json
import logging
import os
import secrets

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    jsonify, abort,
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)
from functools import wraps

from models import (
    db, User, AllowedDomain, SmtpCredential, EmailLog,
    RelayConfig, EmailQueue, Role,
)

logger = logging.getLogger('smtp_relay')


# ── Role-based decorators ──────────────────────────────────────
def role_required(minimum_role):
    """Decorator: require the current user to have at least *minimum_role*."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_role(minimum_role):
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


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


def _flatten_config(cfg: dict) -> dict:
    """Turn the nested config.json into the flat key->value map used by
    RelayConfig in the database."""
    flat = {}
    smtp = cfg.get('smtp_listener', {})
    flat['listen_host']       = smtp.get('host', '0.0.0.0')
    flat['listen_port']       = str(smtp.get('port', 2525))
    flat['banner_hostname']   = smtp.get('banner_hostname', 'relay.local')
    flat['require_auth']      = smtp.get('require_auth', True)
    flat['enable_tls']        = smtp.get('enable_tls', False)
    flat['tls_cert_path']     = smtp.get('tls_cert_path', '')
    flat['tls_key_path']      = smtp.get('tls_key_path', '')

    dest = cfg.get('relay_destination', {})
    flat['relay_host']          = dest.get('host', 'localhost')
    flat['relay_port']          = str(dest.get('port', 25))
    flat['relay_use_tls']       = dest.get('use_tls', False)
    flat['relay_use_starttls']  = dest.get('use_starttls', False)
    flat['relay_auth_user']     = dest.get('auth_user', '')
    flat['relay_auth_password'] = dest.get('auth_password', '')
    flat['relay_helo_hostname'] = dest.get('helo_hostname', 'localhost')

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

    return flat


def create_app(config_json: dict | None = None):
    """Create the Flask application.

    Parameters
    ----------
    config_json : dict, optional
        The parsed contents of config.json.  When supplied the values
        are seeded into the RelayConfig database table so the SMTP
        server and web UI pick them up.
    """
    app = Flask(__name__)

    cfg = config_json or {}
    web = cfg.get('web', {})
    db_cfg = cfg.get('database', {})

    db_path = db_cfg.get('path', 'smtp_relay.db')
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)

    # Always generate a fresh secret key so all sessions are invalidated
    # on server restart.  The config.json key is used as extra entropy.
    cfg_key = web.get('secret_key', '')
    app.config['SECRET_KEY'] = secrets.token_hex(32) + cfg_key
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=8)
    app.config['SESSION_PERMANENT'] = False          # session cookie dies when browser closes
    app.config['REMEMBER_COOKIE_DURATION'] = datetime.timedelta(hours=0)  # disable "remember me"
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True

    app.relay_config_json = cfg

    db.init_app(app)
    login_mgr = LoginManager()
    login_mgr.init_app(app)
    login_mgr.login_view = 'login'
    login_mgr.login_message_category = 'warning'

    @login_mgr.user_loader
    def load_user(uid):
        return User.query.get(int(uid))

    with app.app_context():
        db.create_all()
        _migrate_roles()
        RelayConfig.initialize_defaults()
        if cfg:
            RelayConfig.load_from_dict(_flatten_config(cfg))
        _ensure_admin_exists()

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
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            user = User.query.filter_by(
                username=request.form.get('username', '').strip()
            ).first()
            if user and user.check_password(request.form.get('password', '')) and user.is_active:
                user.last_login = datetime.datetime.utcnow()
                db.session.commit()
                login_user(user, remember=False)
                return redirect(request.args.get('next') or url_for('dashboard'))
            flash('Invalid username or password.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    # ── Dashboard ──────────────────────────────────────────────
    @app.route('/')
    @login_required
    def dashboard():
        now = datetime.datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago = now - datetime.timedelta(hours=1)

        stats = dict(
            total_sent=EmailLog.query.filter_by(status='sent').count(),
            total_failed=EmailLog.query.filter_by(status='failed').count(),
            total_rejected=EmailLog.query.filter_by(status='rejected').count(),
            total_queued=EmailQueue.query.filter_by(status='queued').count(),
            sent_today=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= today).count(),
            failed_today=EmailLog.query.filter(EmailLog.status == 'failed', EmailLog.timestamp >= today).count(),
            sent_this_hour=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= hour_ago).count(),
            active_domains=AllowedDomain.query.filter_by(is_active=True).count(),
            active_credentials=SmtpCredential.query.filter_by(is_active=True).count(),
            total_users=User.query.count(),
        )

        daily = []
        for i in range(6, -1, -1):
            d = (now - datetime.timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            nd = d + datetime.timedelta(days=1)
            daily.append(dict(
                date=d.strftime('%b %d'),
                sent=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= d, EmailLog.timestamp < nd).count(),
                failed=EmailLog.query.filter(EmailLog.status == 'failed', EmailLog.timestamp >= d, EmailLog.timestamp < nd).count(),
            ))

        recent = EmailLog.query.order_by(EmailLog.timestamp.desc()).limit(20).all()

        return render_template('dashboard.html',
                               stats=stats, daily_stats=json.dumps(daily),
                               recent_logs=recent, json=json)

    # ── Configuration ──────────────────────────────────────────
    def _write_db_config_to_file():
        """Write the current DB configuration back to config.json on disk."""
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'config.json'
        )
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                cfg = json.load(f)
        else:
            cfg = {}

        cfg.setdefault('smtp_listener', {})
        cfg['smtp_listener']['host'] = RelayConfig.get('listen_host', '0.0.0.0')
        cfg['smtp_listener']['port'] = RelayConfig.get_int('listen_port', 2525)
        cfg['smtp_listener']['banner_hostname'] = RelayConfig.get('banner_hostname', 'relay.local')
        cfg['smtp_listener']['require_auth'] = RelayConfig.get_bool('require_auth', True)
        cfg['smtp_listener']['enable_tls'] = RelayConfig.get_bool('enable_tls', False)
        cfg['smtp_listener']['tls_cert_path'] = RelayConfig.get('tls_cert_path', '')
        cfg['smtp_listener']['tls_key_path'] = RelayConfig.get('tls_key_path', '')

        cfg.setdefault('relay_destination', {})
        cfg['relay_destination']['host'] = RelayConfig.get('relay_host', 'localhost')
        cfg['relay_destination']['port'] = RelayConfig.get_int('relay_port', 25)
        cfg['relay_destination']['use_tls'] = RelayConfig.get_bool('relay_use_tls', False)
        cfg['relay_destination']['use_starttls'] = RelayConfig.get_bool('relay_use_starttls', False)
        cfg['relay_destination']['auth_user'] = RelayConfig.get('relay_auth_user', '')
        cfg['relay_destination']['auth_password'] = RelayConfig.get('relay_auth_password', '')
        cfg['relay_destination']['helo_hostname'] = RelayConfig.get('relay_helo_hostname', 'localhost')

        cfg.setdefault('limits', {})
        cfg['limits']['max_message_size_bytes'] = RelayConfig.get_int('max_message_size', 26214400)
        cfg['limits']['max_recipients_per_message'] = RelayConfig.get_int('max_recipients', 100)
        cfg['limits']['global_rate_limit_per_hour'] = RelayConfig.get_int('global_rate_limit', 1000)
        ips_str = RelayConfig.get('allowed_source_ips', '')
        cfg['limits']['allowed_source_ips'] = [ip.strip() for ip in ips_str.split(',') if ip.strip()] if ips_str else []

        cfg.setdefault('queue', {})
        cfg['queue']['retry_interval_seconds'] = RelayConfig.get_int('queue_retry_interval', 300)
        cfg['queue']['max_retries'] = RelayConfig.get_int('queue_max_retries', 3)

        cfg.setdefault('logging', {})
        cfg['logging']['log_retention_days'] = RelayConfig.get_int('log_retention_days', 30)

        with open(config_path, 'w') as f:
            json.dump(cfg, f, indent=4)

        logger.info('Config written to config.json')

    @app.route('/config', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def config():
        if request.method == 'POST':
            keys = [
                'relay_host', 'relay_port', 'relay_use_tls', 'relay_use_starttls',
                'relay_auth_user', 'relay_auth_password', 'relay_helo_hostname',
                'listen_host', 'listen_port', 'banner_hostname', 'require_auth',
                'max_message_size', 'max_recipients', 'global_rate_limit',
                'enable_tls', 'tls_cert_path', 'tls_key_path',
                'allowed_source_ips', 'log_retention_days',
                'queue_retry_interval', 'queue_max_retries',
            ]
            bools = {'relay_use_tls', 'relay_use_starttls', 'require_auth', 'enable_tls'}
            for k in keys:
                if k in bools:
                    v = 'true' if request.form.get(k) else 'false'
                else:
                    v = request.form.get(k, '')
                RelayConfig.set(k, v)

            # Also persist to config.json on disk
            try:
                _write_db_config_to_file()
            except Exception as exc:
                logger.error('Failed to write config.json: %s', exc)
                flash(f'Settings saved to active config but failed to write config.json: {exc}', 'warning')
                return redirect(url_for('config'))

            flash(
                'Configuration saved to active config and config.json.  '
                'Relay/sending changes take effect on the next message.  '
                'Listener changes require an SMTP server restart.',
                'success',
            )
            return redirect(url_for('config'))

        configs = {k: RelayConfig.get(k) for k in RelayConfig.DEFAULTS}
        return render_template('config.html', configs=configs)

    # ── Reload config.json ─────────────────────────────────────
    @app.route('/config/reload', methods=['POST'])
    @login_required
    @admin_required
    def reload_config():
        """Re-read config.json from disk and merge into the active DB config."""
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'config.json'
            )
            if not os.path.exists(config_path):
                flash('config.json not found on disk.', 'danger')
                return redirect(url_for('config'))

            with open(config_path, 'r') as f:
                fresh = json.load(f)

            flat = _flatten_config(fresh)
            RelayConfig.load_from_dict(flat)
            app.relay_config_json = fresh

            flash(
                'Configuration reloaded from config.json successfully.  '
                'Relay/sending changes take effect on the next message.  '
                'Listener changes (host/port/TLS) require an SMTP server restart.',
                'success',
            )
            logger.info('Config reloaded from config.json by %s', current_user.username)
        except json.JSONDecodeError as exc:
            flash(f'config.json has invalid JSON: {exc}', 'danger')
            logger.error('Config reload JSON error: %s', exc)
        except Exception as exc:
            flash(f'Failed to reload config: {exc}', 'danger')
            logger.error('Config reload error: %s', exc)
        return redirect(url_for('config'))

    # ── Save config to file ────────────────────────────────────
    @app.route('/config/save-to-file', methods=['POST'])
    @login_required
    @admin_required
    def save_config_to_file():
        """Write the current DB configuration back to config.json on disk."""
        try:
            _write_db_config_to_file()
            flash('Current configuration saved to config.json on disk.', 'success')
            logger.info('Config saved to config.json by %s', current_user.username)
        except Exception as exc:
            flash(f'Failed to save config to file: {exc}', 'danger')
            logger.error('Config save-to-file error: %s', exc)
        return redirect(url_for('config'))

    # ── Domains ────────────────────────────────────────────────
    @app.route('/domains')
    @login_required
    def domains():
        return render_template('domains.html',
                               domains=AllowedDomain.query.order_by(AllowedDomain.domain).all())

    @app.route('/domains/add', methods=['POST'])
    @login_required
    @operator_required
    def add_domain():
        d = request.form.get('domain', '').strip().lower()
        if not d:
            flash('Domain is required.', 'danger')
            return redirect(url_for('domains'))
        if AllowedDomain.query.filter_by(domain=d).first():
            flash(f'Domain {d} already exists.', 'warning')
            return redirect(url_for('domains'))
        db.session.add(AllowedDomain(domain=d,
                                     description=request.form.get('description', '').strip(),
                                     is_active=True, created_by=current_user.username))
        db.session.commit()
        flash(f'Domain {d} added.', 'success')
        return redirect(url_for('domains'))

    @app.route('/domains/<int:did>/toggle', methods=['POST'])
    @login_required
    @operator_required
    def toggle_domain(did):
        d = AllowedDomain.query.get_or_404(did)
        d.is_active = not d.is_active
        db.session.commit()
        flash(f'Domain {d.domain} {"enabled" if d.is_active else "disabled"}.', 'success')
        return redirect(url_for('domains'))

    @app.route('/domains/<int:did>/delete', methods=['POST'])
    @login_required
    @operator_required
    def delete_domain(did):
        d = AllowedDomain.query.get_or_404(did)
        name = d.domain
        db.session.delete(d)
        db.session.commit()
        flash(f'Domain {name} deleted.', 'success')
        return redirect(url_for('domains'))

    # ── SMTP Credentials ──────────────────────────────────────
    @app.route('/credentials')
    @login_required
    def credentials():
        return render_template('credentials.html',
                               credentials=SmtpCredential.query.order_by(SmtpCredential.username).all())

    @app.route('/credentials/add', methods=['POST'])
    @login_required
    @operator_required
    def add_credential():
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        if not u or not p:
            flash('Username and password are required.', 'danger')
            return redirect(url_for('credentials'))
        if SmtpCredential.query.filter_by(username=u).first():
            flash(f'Credential {u} already exists.', 'warning')
            return redirect(url_for('credentials'))
        c = SmtpCredential(username=u,
                           description=request.form.get('description', '').strip(),
                           is_active=True,
                           max_sends_per_hour=int(request.form.get('max_sends_per_hour', 100)),
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
        c = SmtpCredential.query.get_or_404(cid)
        c.is_active = not c.is_active
        db.session.commit()
        flash(f'Credential {c.username} {"enabled" if c.is_active else "disabled"}.', 'success')
        return redirect(url_for('credentials'))

    @app.route('/credentials/<int:cid>/delete', methods=['POST'])
    @login_required
    @operator_required
    def delete_credential(cid):
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
        c = SmtpCredential.query.get_or_404(cid)
        pw = request.form.get('new_password', '')
        if not pw:
            flash('Password is required.', 'danger')
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
        return render_template('users.html',
                               users=User.query.order_by(User.username).all())

    @app.route('/users/add', methods=['POST'])
    @login_required
    @admin_required
    def add_user():
        u = request.form.get('username', '').strip()
        e = request.form.get('email', '').strip()
        p = request.form.get('password', '')
        r = request.form.get('role', Role.VIEWER)
        if not u or not e or not p:
            flash('All fields are required.', 'danger')
            return redirect(url_for('users'))
        if User.query.filter_by(username=u).first():
            flash(f'Username {u} exists.', 'warning')
            return redirect(url_for('users'))
        if User.query.filter_by(email=e).first():
            flash(f'Email {e} in use.', 'warning')
            return redirect(url_for('users'))
        # Prevent role escalation
        allowed = Role.assignable_roles(current_user.role)
        if r not in allowed:
            flash(f'You cannot assign the {Role.label(r)} role.', 'danger')
            return redirect(url_for('users'))
        user = User(username=u, email=e, role=r,
                    is_admin=(r in (Role.ADMIN, Role.SUPER_ADMIN)))
        user.set_password(p)
        db.session.add(user)
        db.session.commit()
        flash(f'User {u} created as {Role.label(r)}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/toggle', methods=['POST'])
    @login_required
    @admin_required
    def toggle_user(uid):
        u = User.query.get_or_404(uid)
        if u.id == current_user.id:
            flash('Cannot disable yourself.', 'danger')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u):
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        u.is_active_user = not u.is_active_user
        db.session.commit()
        flash(f'User {u.username} {"enabled" if u.is_active_user else "disabled"}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/change-role', methods=['POST'])
    @login_required
    @admin_required
    def change_user_role(uid):
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
        u.role = new_role
        u.is_admin = (new_role in (Role.ADMIN, Role.SUPER_ADMIN))
        db.session.commit()
        flash(f'User {u.username} is now {Role.label(new_role)}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/reset-password', methods=['POST'])
    @login_required
    @admin_required
    def reset_user_password(uid):
        u = User.query.get_or_404(uid)
        if not current_user.can_manage_user(u) and u.id != current_user.id:
            flash(f'You cannot manage {u.role_label} users.', 'danger')
            return redirect(url_for('users'))
        pw = request.form.get('new_password', '')
        if not pw:
            flash('Password is required.', 'danger')
            return redirect(url_for('users'))
        u.set_password(pw)
        db.session.commit()
        flash(f'Password reset for {u.username}.', 'success')
        return redirect(url_for('users'))

    @app.route('/users/<int:uid>/delete', methods=['POST'])
    @login_required
    @admin_required
    def delete_user(uid):
        u = User.query.get_or_404(uid)
        if u.id == current_user.id:
            flash('Cannot delete yourself.', 'danger')
            return redirect(url_for('users'))
        if not current_user.can_manage_user(u):
            flash(f'You cannot manage {u.role_label} users.', 'danger')
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
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
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
        return render_template('queue.html',
            queued=EmailQueue.query.filter_by(status='queued').order_by(EmailQueue.next_retry_at).all(),
            processing=EmailQueue.query.filter_by(status='processing').all(),
            failed=EmailQueue.query.filter_by(status='failed').order_by(EmailQueue.created_at.desc()).limit(50).all(),
            json=json)

    @app.route('/queue/<int:qid>/retry', methods=['POST'])
    @login_required
    @operator_required
    def retry_queue(qid):
        e = EmailQueue.query.get_or_404(qid)
        e.status = 'queued'
        e.retry_count = 0
        e.next_retry_at = datetime.datetime.utcnow()
        if e.log_id:
            l = EmailLog.query.get(e.log_id)
            if l:
                l.status = 'queued'
                l.status_message = 'Manually requeued'
        db.session.commit()
        flash('Requeued.', 'success')
        return redirect(url_for('queue'))

    @app.route('/queue/<int:qid>/delete', methods=['POST'])
    @login_required
    @operator_required
    def delete_queue(qid):
        db.session.delete(EmailQueue.query.get_or_404(qid))
        db.session.commit()
        flash('Deleted.', 'success')
        return redirect(url_for('queue'))

    @app.route('/queue/flush', methods=['POST'])
    @login_required
    @operator_required
    def flush_queue():
        n = EmailQueue.query.filter(EmailQueue.status.in_(['failed'])).delete(synchronize_session=False)
        db.session.commit()
        flash(f'Flushed {n} failed entries.', 'success')
        return redirect(url_for('queue'))

    # ── Server control ─────────────────────────────────────────
    @app.route('/server/restart', methods=['POST'])
    @login_required
    @admin_required
    def restart_server():
        s = getattr(app, '_smtp_server', None)
        if s:
            try:
                s.restart()
                flash('SMTP server restarted.', 'success')
            except Exception as exc:
                flash(f'Restart error: {exc}', 'danger')
        return redirect(url_for('dashboard'))

    @app.route('/server/stop', methods=['POST'])
    @login_required
    @admin_required
    def stop_server():
        s = getattr(app, '_smtp_server', None)
        if s:
            s.stop()
            flash('SMTP server stopped.', 'warning')
        return redirect(url_for('dashboard'))

    @app.route('/server/start', methods=['POST'])
    @login_required
    @admin_required
    def start_server():
        s = getattr(app, '_smtp_server', None)
        if s:
            try:
                s.start()
                flash('SMTP server started.', 'success')
            except Exception as exc:
                flash(f'Start error: {exc}', 'danger')
        return redirect(url_for('dashboard'))

    # ── API ────────────────────────────────────────────────────
    @app.route('/api/stats')
    @login_required
    def api_stats():
        now = datetime.datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago = now - datetime.timedelta(hours=1)
        s = getattr(app, '_smtp_server', None)
        return jsonify(
            sent_today=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= today).count(),
            failed_today=EmailLog.query.filter(EmailLog.status == 'failed', EmailLog.timestamp >= today).count(),
            sent_this_hour=EmailLog.query.filter(EmailLog.status == 'sent', EmailLog.timestamp >= hour_ago).count(),
            queued=EmailQueue.query.filter_by(status='queued').count(),
            smtp_running=s.is_running if s else False,
        )

    @app.route('/api/logs/recent')
    @login_required
    def api_recent_logs():
        rows = EmailLog.query.order_by(EmailLog.timestamp.desc()).limit(20).all()
        return jsonify([dict(
            id=r.id,
            timestamp=r.timestamp.isoformat() if r.timestamp else '',
            sender=r.sender, recipients=r.recipients,
            subject=r.subject, status=r.status,
            status_message=r.status_message,
        ) for r in rows])

    # ── Profile ────────────────────────────────────────────────
    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
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
                em = request.form.get('email', '').strip()
                if em and em != current_user.email:
                    if User.query.filter_by(email=em).first():
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

    @app.errorhandler(500)
    def err500(e):
        return _render_error(500, 'Internal Server Error')

    return app


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
        # Set existing admins to super_admin
        db.session.execute(
            db.text("UPDATE users SET role = 'super_admin' WHERE is_admin = 1")
        )
        db.session.commit()
        logger.info('Migration complete: existing admins set to super_admin')


def _ensure_admin_exists():
    if User.query.count() == 0:
        a = User(username='admin', email='admin@localhost',
                 role=Role.SUPER_ADMIN, is_admin=True)
        a.set_password('admin')
        db.session.add(a)
        db.session.commit()
        logger.info("Created default admin user (admin / admin)")