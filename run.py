#!/usr/bin/env python3
"""
SMTP Mail Relay – Windows Launcher
Version 2.1.0
===================================
Designed and built by Christopher McGrath

Double-click this file or run:   python.exe run.py

On first run it will install the required pip packages automatically.
All settings are read from config.json in the same directory.
User accounts are managed through the web interface only.
"""

# Author: Christopher McGrath
# Version: 2.1.0

import json
import logging
import os
import subprocess
import sys
import threading
import time

# ── resolve paths relative to this script ──────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')
REQUIREMENTS = os.path.join(SCRIPT_DIR, 'requirements.txt')


# ── auto-install dependencies ──────────────────────────────────
def ensure_dependencies():
    """pip-install requirements.txt if any package is missing."""
    try:
        import flask          # noqa
        import aiosmtpd       # noqa
        import bcrypt          # noqa
        import flask_login     # noqa
        import flask_sqlalchemy  # noqa
        return  # everything importable
    except ImportError:
        pass

    print('=' * 60)
    print('  First run – installing dependencies …')
    print('=' * 60)
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'install',
        '--quiet', '-r', REQUIREMENTS,
    ])
    print('  Dependencies installed.\n')


def load_config() -> dict:
    """Load config.json, creating a default one if it doesn't exist."""
    if not os.path.exists(CONFIG_FILE):
        default = {
            "web": {
                "host": "0.0.0.0",
                "port": 8025,
                "secret_key": "CHANGE-ME-to-a-random-secret-string"
            },
            "smtp_listener": {
                "host": "0.0.0.0",
                "port": 2525,
                "banner_hostname": "relay.local",
                "require_auth": True,
                "enable_tls": False,
                "tls_cert_path": "",
                "tls_key_path": ""
            },
            "relay_destination": {
                "host": "smtp.example.com",
                "port": 587,
                "use_tls": False,
                "use_starttls": True,
                "auth_user": "",
                "auth_password": "",
                "helo_hostname": "myserver.example.com"
            },
            "limits": {
                "max_message_size_bytes": 26214400,
                "max_recipients_per_message": 100,
                "global_rate_limit_per_hour": 1000,
                "allowed_source_ips": []
            },
            "queue": {
                "retry_interval_seconds": 300,
                "max_retries": 3
            },
            "logging": {
                "level": "INFO",
                "log_file": "",
                "log_retention_days": 30
            },
            "database": {
                "path": "smtp_relay.db"
            }
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default, f, indent=4)
        print(f'Created default {CONFIG_FILE} – edit it and restart.\n')

    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def setup_logging(cfg: dict):
    log_cfg = cfg.get('logging', {})
    level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
    fmt = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = log_cfg.get('log_file', '')
    if log_file:
        os.makedirs(os.path.dirname(log_file) or '.', exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=level, format=fmt, datefmt='%Y-%m-%d %H:%M:%S',
                        handlers=handlers)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('aiosmtpd').setLevel(logging.INFO)


def main():
    ensure_dependencies()

    cfg = load_config()
    setup_logging(cfg)
    logger = logging.getLogger('smtp_relay')

    # ── import app components (after deps are installed) ───────
    from app import create_app
    from smtp_server import SmtpRelayServer, QueueProcessor

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

    # ── print banner ───────────────────────────────────────────
    web = cfg.get('web', {})
    web_host = web.get('host', '0.0.0.0')
    web_port = web.get('port', 8025)
    smtp_cfg = cfg.get('smtp_listener', {})
    smtp_port = smtp_cfg.get('port', 2525)
    dest = cfg.get('relay_destination', {})

    print()
    print('=' * 60)
    print('  SMTP Mail Relay  v2.1.0')
    print('  Designed and built by Christopher McGrath')
    print('=' * 60)
    print(f'  Web Interface : http://{web_host}:{web_port}')
    print(f'  SMTP Listener : {smtp_cfg.get("host", "0.0.0.0")}:{smtp_port}')
    print(f'  Relay Target  : {dest.get("host", "?")}:{dest.get("port", "?")}')
    print(f'  HELO Hostname : {dest.get("helo_hostname", "localhost")}')
    print(f'  Default Login : admin / admin')
    print('=' * 60)
    print('  Press Ctrl+C to stop.\n')

    # ── run Flask (blocking) ───────────────────────────────────
    try:
        app.run(
            host=web_host,
            port=web_port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
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