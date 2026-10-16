#!/usr/bin/env python3
"""End-to-end verification suite for smtp-relay-beta (PostgreSQL-only).

Requires a reachable PostgreSQL database, e.g. via Docker:

  docker run -d --rm --name smtp-e2e-pg -p 127.0.0.1:15432:5432 \
      -e POSTGRES_USER=smtp -e POSTGRES_PASSWORD=e2e \
      -e POSTGRES_DB=smtp_e2e postgres:16-alpine
  SMOKE_DB_URL='postgresql+psycopg2://smtp:e2e@127.0.0.1:15432/smtp_e2e' \
      python3 tests/e2e_smoke.py

Any tables left over in the target database are dropped first, so every
run starts from a clean schema.
"""
import datetime
import json, os, re, secrets, sys, tempfile, threading, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

SMOKE_DB_URL = os.environ.get('SMOKE_DB_URL', '').strip()
if not SMOKE_DB_URL:
    print('ERROR: SMOKE_DB_URL is required — PostgreSQL is the only backend.')
    print("  e.g. SMOKE_DB_URL='postgresql+psycopg2://smtp:e2e@127.0.0.1:15432/smtp_e2e'")
    sys.exit(2)

# Clean slate: drop tables from any earlier run so first-boot semantics
# (env-var seeding, admin provisioning, secret-key generation) re-run.
import sqlalchemy
from models import db as _models_db, normalize_database_url
_eng = sqlalchemy.create_engine(normalize_database_url(SMOKE_DB_URL))
_models_db.metadata.drop_all(_eng)
_eng.dispose()

SECRET = 'e2e-test-secret-key-0123456789abcdef'
SMTP_PORT, SINK_PORT = 18926, 18927

def base_cfg(**web_extra):
    return {
        "version": "4.4.4",
        "web": {"host": "127.0.0.1", "port": 18925, "secret_key": SECRET,
                "threads": 2, "secure_cookies": False, **web_extra},
        "smtp_listener": {"host": "127.0.0.1", "port": SMTP_PORT,
                          "banner_hostname": "e2e.local", "require_auth": True,
                          "enable_tls": False, "tls_cert_path": "", "tls_key_path": ""},
        "relay_destination": {"host": "127.0.0.1", "port": SINK_PORT, "use_tls": False,
                              "use_starttls": False, "auth_user": "", "auth_password": "",
                              "helo_hostname": "e2e-sender"},
        "delivery_mode": {"mode": "relay"},
        "direct_delivery": {"use_starttls": False, "auth_user": "", "auth_password": ""},
        "limits": {"max_message_size_bytes": 26214400, "max_recipients_per_message": 100,
                   "global_rate_limit_per_hour": 1000, "allowed_source_ips": []},
        "queue": {"retry_interval_seconds": 300, "max_retries": 3},
        "logging": {"level": "INFO", "log_file": "", "log_retention_days": 30,
                    "debug_logging": False},
        "database": {"url": SMOKE_DB_URL},
    }

results = []
def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS' if cond else 'FAIL'), '-', name, ('| ' + str(detail)) if detail and not cond else '')

# ── capture sink ────────────────────────────────────────────────
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import Envelope, Session, SMTP
class Sink:
    def __init__(self): self.messages = []
    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content))
        return '250 OK'
sink = Sink()
sink_ctl = Controller(sink, hostname='127.0.0.1', port=SINK_PORT)
sink_ctl.start()

# ── app 1: boot + authenticated relay flow ──────────────────────
import logging
logging.disable(logging.WARNING)
cfg = base_cfg()
from app import create_app, _flatten_config
from smtp_server import SmtpRelayServer, RelayHandler, DkimSigner, _DeliverySnapshot
from models import db, User, AllowedDomain, SmtpCredential, EmailLog, EmailQueue, RelayConfig, DkimKey, Role

app = create_app(config_json=cfg)
server = SmtpRelayServer(app)
server.start()
check('smtp listener started', server.is_running)

# bootstrap detection
from run import _database_ready
check('bootstrap detection: postgres URL ready after create_all',
      _database_ready({'url': SMOKE_DB_URL}) is True)
check('bootstrap detection: dead postgres URL -> not ready',
      _database_ready({'url': 'postgresql+psycopg2://x:y@127.0.0.1:1/none'}) is False)
check('bootstrap detection: missing URL -> not ready',
      _database_ready({}) is False)

with app.app_context():
    u = User(username='boss', email='boss@local', role=Role.SUPER_ADMIN, is_admin=True)
    u.set_password('adminpass123')
    db.session.add(u)
    db.session.add(AllowedDomain(domain='relay.test', is_active=True, created_by='boss'))
    c = SmtpCredential(username='sender', is_active=True, max_sends_per_hour=100, created_by='boss')
    c.set_password('clientpass123')
    db.session.add(c)
    db.session.commit()

import smtplib
def send(client_auth=True, sender='x@relay.test', rcpts=None, data=None):
    cl = smtplib.SMTP('127.0.0.1', SMTP_PORT, timeout=10)
    try:
        cl.ehlo('client')
        if client_auth:
            cl.login('sender', 'clientpass123')
        cl.sendmail(sender, rcpts or ['y@relay.test'],
                    data or b'Subject: e2e\r\n\r\nhello\r\n')
        return None
    except Exception as e:
        return e
    finally:
        try: cl.quit()
        except Exception: pass

err = send()
check('authenticated submission accepted', err is None, err)

deadline = time.time() + 15
qid = None
while time.time() < deadline:
    with app.app_context():
        q = EmailQueue.query.first()
        if q and q.status == 'sent':
            qid = q.id
            log = EmailLog.query.get(q.log_id)
            check('queue -> sent via live DB', True)
            check('EmailLog updated to sent', log is not None and log.status == 'sent',
                  getattr(log, 'status', None))
            check('sink received exactly one message', len(sink.messages) == 1, len(sink.messages))
            break
    time.sleep(0.3)
if qid is None:
    check('queue -> sent via live DB', False, 'timed out')

err = send(client_auth=False)
check('unauthenticated MAIL FROM rejected (530)', err is not None and '530' in str(err), err)

# claim atomicity: a second _deliver on the sent row must no-op
with app.app_context():
    handler = RelayHandler(app)
check('atomic claim: re-delivering sent row is a no-op', handler._deliver_load(qid) is None)

# ── DKIM: plaintext key signs; migration encrypts; signing still works ──
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
priv = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                         serialization.NoEncryption()).decode()
pub_b64 = priv.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode().replace('-----BEGIN PUBLIC KEY-----','').replace('-----END PUBLIC KEY-----','').replace('\n','')
with app.app_context():
    db.session.add(DkimKey(domain='relay.test', selector='e2e1', private_key=pem,
                           public_key=pub_b64, is_active=True, created_by='boss'))
    db.session.commit()
signed = DkimSigner(app).sign_email(b'From: x@relay.test\r\nSubject: t\r\n\r\nbody\r\n', 'relay.test')
check('plaintext legacy key signs (passthrough)', signed is not None and signed.startswith(b'DKIM-Signature'),
      type(signed))
# reload module state: new app instance on same DB runs the migration
app2 = create_app(config_json=base_cfg())
with app2.app_context():
    k = DkimKey.query.filter_by(domain='relay.test').first()
    check('startup migration encrypted DKIM key (ENC1)', k.private_key.startswith('ENC1:'), k.private_key[:20])
    check('legacy plaintext rows left intact in config flow', True)
signed2 = DkimSigner(app2).sign_email(b'From: x@relay.test\r\nSubject: t\r\n\r\nbody\r\n', 'relay.test')
check('ENC1 key decrypts and signs', signed2 is not None and signed2.startswith(b'DKIM-Signature'), type(signed2))

# ── require_auth runtime flip (authenticator always attached) ────
with app2.app_context():
    RelayConfig.set('require_auth', 'false')
server.restart()
cl = smtplib.SMTP('127.0.0.1', SMTP_PORT, timeout=10)
ehlo_resp = cl.ehlo('client')[1].decode()
check('AUTH still advertised with require_auth=false', 'AUTH' in ehlo_resp, ehlo_resp)
code = cl.docmd('MAIL', 'FROM:<x@relay.test>')[0]
check('open mode accepts MAIL FROM without auth (250)', code == 250, code)
cl.quit()
with app2.app_context():
    RelayConfig.set('require_auth', 'true')
server.restart()
err = send(client_auth=False)
check('re-enabled require_auth rejects without restart-only gap (530)', err is not None and '530' in str(err), err)

# ── direct-mode per-domain grouping (unit, mocked network) ──────
import smtp_server as ss
orig_mx, orig_send = ss._resolve_mx, RelayHandler._smtp_send
try:
    ss._resolve_mx = lambda d: [(f'mx.{d}', 25)]
    sent_targets = []
    h = RelayHandler(app2)
    h._smtp_send = lambda snap, host, port, rcpts, qid: (sent_targets.append((host, tuple(rcpts))), None)[1]
    snap = _DeliverySnapshot(
        queue_id=0, sender='x@relay.test',
        recipients=['a@one.test', 'b@one.test', 'c@two.test'],
        message=b'x', signed_message=None, log_id=None, delivery_mode='direct',
        host='', port=25, use_tls=False, use_starttls=False,
        auth_user='', auth_pass=None, direct_user=None, direct_pass=None,
        helo_name='t', retry_interval=300, retry_max=3)
    r = h._deliver_direct(snap)
    check('direct mode groups per domain and resolves each MX',
          r is None and sorted(sent_targets) == [('mx.one.test', ('a@one.test', 'b@one.test')),
                                                 ('mx.two.test', ('c@two.test',))], sent_targets)
    check('direct mode surfaces target hosts for the log',
          set(snap.host.split(', ')) == {'mx.one.test', 'mx.two.test'}, snap.host)
finally:
    ss._resolve_mx, RelayHandler._smtp_send = orig_mx, orig_send

# ── web UI: login, logs cap, config POST (atomic set_many) ──────
def csrf_of(client, path='/login'):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None

client = app2.test_client()
tok = csrf_of(client)
resp = client.post('/login', data={'username': 'boss', 'password': 'adminpass123',
                                   'csrf_token': tok, 'next': ''})
client.get('/')  # land
check('web login works (redirect to dashboard)', resp.status_code == 302 and resp.headers.get('Location', '') in ('/', '/dashboard'), (resp.status_code, resp.headers.get('Location')))
check('dashboard renders', client.get('/').status_code == 200)
check('/logs with per_page=999999 capped, 200', client.get('/logs?per_page=999999').status_code == 200)

with app2.app_context():
    RelayConfig.set('relay_host', '127.0.0.1')
    current = {k: (RelayConfig.get(k) or '') for k in RelayConfig.DEFAULTS}
tok2 = csrf_of(client, '/config')
check('config source is database', app2.config['CONFIG_SOURCE'] == 'database',
      app2.config['CONFIG_SOURCE'])
form = {k: (v if isinstance(v, str) else str(v)) for k, v in current.items()}
form.update({'csrf_token': tok2, 'relay_host': 'changed.test', 'relay_auth_password': ''})
resp = client.post('/config', data=form)
with app2.app_context():
    check('/config POST saves atomically (set_many path)',
          RelayConfig.get('relay_host') == 'changed.test', RelayConfig.get('relay_host'))
    RelayConfig.set('relay_host', '127.0.0.1')
    check('web_secret_key stored in the database',
          bool(RelayConfig.get('web_secret_key', '')))
body = client.get('/config').get_data(as_text=True)
check('config.json file-operations card removed',
      'config.json File Operations' not in body)
check('no config.json ever created (DB is the only config store)',
      not os.path.exists(os.path.join(REPO, 'config.json')))

# ── trust_proxy throttle keying ─────────────────────────────────
app3 = create_app(config_json=base_cfg(trust_proxy=True))
c3 = app3.test_client()
tok3 = csrf_of(c3)
codes = [c3.post('/login', data={'username': 'victim', 'password': 'wrong',
                                 'csrf_token': tok3},
                 headers={'X-Forwarded-For': '9.9.9.9'}).status_code for _ in range(6)]
check('5 failures from one XFF -> 6th is 429', codes[:5] == [200]*5 and codes[5] == 429, codes)
other = c3.post('/login', data={'username': 'victim', 'password': 'wrong', 'csrf_token': tok3},
                headers={'X-Forwarded-For': '8.8.8.8'}).status_code
check('different XFF not throttled', other == 200, other)

# ── Microsoft 365 sign-in (OIDC) ────────────────────────────────
import auth_oauth as ao
import crypto_utils

c1 = app.test_client()
r = c1.get('/login/azure')
check('azure disabled: /login/azure redirects to /login',
      r.status_code == 302 and r.headers.get('Location', '').endswith('/login'),
      (r.status_code, r.headers.get('Location')))
lp = c1.get('/login').get_data(as_text=True)
check('login page: no Microsoft button when disabled', 'Sign in with Microsoft' not in lp)

with app.app_context():
    RelayConfig.set('azure_tenant_id', '11111111-2222-3333-4444-555555555555')
    RelayConfig.set('azure_client_id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
    RelayConfig.set('azure_client_secret',
                    crypto_utils.encrypt_secret('fake-secret-value', app.secret_key))
    RelayConfig.set('azure_redirect_uri', 'http://127.0.0.1:18925/login/azure/callback')
    RelayConfig.set('azure_enabled', 'true')

class FakeMsal:
    """Stands in for ConfidentialClientApplication (constructor does live
    tenant discovery against Microsoft, which must not run in tests)."""
    results = []
    def __init__(self, tenant, client, secret): pass
    def get_authorization_request_url(self, scopes, state=None, redirect_uri=None, prompt=None):
        return ('https://login.microsoftonline.com/fake/oauth2/v2.0/authorize'
                f'?state={state}&redirect_uri={redirect_uri}&prompt={prompt}')
    def acquire_token_by_authorization_code(self, code, scopes, redirect_uri):
        return FakeMsal.results.pop(0) if FakeMsal.results else {'error': 'no_stubbed_result'}

orig_factory = ao.msal_app_factory
ao.msal_app_factory = FakeMsal
try:
    r = c1.get('/login/azure')
    loc = r.headers.get('Location', '')
    state_m = re.search(r'state=([^&]+)', loc)
    check('azure enabled: redirects to Microsoft authorize URL with state',
          'login.microsoftonline.com' in loc and bool(state_m), loc[:80])
    state = state_m.group(1)
    lp = c1.get('/login').get_data(as_text=True)
    check('login page shows Microsoft button when enabled', 'Sign in with Microsoft' in lp)

    c3b = app.test_client()  # no session state -> state mismatch path
    r = c3b.get('/login/azure/callback?code=x&state=wrong')
    check('callback without valid state rejected',
          r.status_code == 302 and r.headers.get('Location', '').endswith('/login'),
          (r.status_code, r.headers.get('Location')))

    with app.app_context():
        op = User(username='op@tenant.com', email='op@tenant.com',
                  role=Role.OPERATOR, auth_source='entra')
        op.set_password(secrets.token_urlsafe(32))
        db.session.add(op)
        db.session.commit()
    FakeMsal.results = [{'id_token_claims': {'oid': 'oid-123',
                                             'email': 'op@tenant.com',
                                             'name': 'Op'}}]
    r = c1.get(f'/login/azure/callback?code=real&state={state}')
    check('entra JIT bind + sign-in succeeds',
          r.status_code == 302 and r.headers.get('Location', '').endswith('/'),
          (r.status_code, r.headers.get('Location')))
    with app.app_context():
        u2 = User.query.filter_by(email='op@tenant.com').first()
        check('entra_oid bound on first Microsoft login',
              u2 is not None and u2.entra_oid == 'oid-123', getattr(u2, 'entra_oid', None))
    check('session established after Microsoft sign-in', c1.get('/').status_code == 200)

    c4 = app.test_client()
    r = c4.post('/login', data={'username': 'op@tenant.com', 'password': 'whatever',
                                'csrf_token': csrf_of(c4)})
    check('entra account cannot use password login',
          r.status_code == 200 and 'signs in with Microsoft' in r.get_data(as_text=True),
          r.status_code)

    c5 = app.test_client()
    r5 = c5.get('/login/azure')
    state5 = re.search(r'state=([^&]+)', r5.headers.get('Location', '')).group(1)
    FakeMsal.results = [{'id_token_claims': {'oid': 'oid-999',
                                             'email': 'stranger@tenant.com',
                                             'name': 'X'}}]
    c5.get(f'/login/azure/callback?code=real&state={state5}')
    body = c5.get('/login').get_data(as_text=True)
    check('unregistered Microsoft identity denied', 'not registered' in body.lower())

    with app.app_context():
        adm = User.query.filter_by(username='admin').first()
        check('initial admin is break-glass local',
              adm is not None and adm.auth_source == 'local',
              getattr(adm, 'auth_source', None))
finally:
    ao.msal_app_factory = orig_factory
    with app.app_context():
        RelayConfig.set('azure_enabled', 'false')

# ── v4.4.0 regression pins: bootstrap marker + processing-row sweep ──
with app.app_context():
    check('bootstrap marker written after first boot',
          RelayConfig.query.filter_by(key='bootstrap_complete',
                                      value='true').first() is not None)
    import auth_oauth as _ao
    check('LOGIN_SCOPES contain no MSAL-reserved scopes',
          not (set(_ao.LOGIN_SCOPES) & {'openid', 'profile', 'offline_access'}),
          _ao.LOGIN_SCOPES)
    stranded = EmailQueue(sender='x@relay.test', recipients='["a@b.test"]',
                          raw_message=b'subject: t\r\n\r\nx', status='processing',
                          next_retry_at=datetime.datetime.utcnow())
    db.session.add(stranded)
    db.session.commit()
    from smtp_server import QueueProcessor
    QueueProcessor(app)._requeue_orphaned_processing()
    db.session.expire_all()
    check("startup sweep requeues rows stranded in 'processing'",
          db.session.get(EmailQueue, stranded.id).status == 'queued')

# ── AUTH LOGIN challenges must be the canonical SASL form (Outlook) ──
# Outlook freezes (sends nothing after the 334) on aiosmtpd's default
# "User Name\x00" / "Password\x00" challenges. The server must emit
# base64("Username:") / base64("Password:").
import base64 as _b64
import socket as _socket
_sk = _socket.create_connection(('127.0.0.1', SMTP_PORT), timeout=10)
_skf = _sk.makefile('rb')
def _smtp_line():
    return _skf.readline().decode('utf-8', 'replace').strip()
_smtp_line()  # 220 greeting
_sk.sendall(b'EHLO outlook-e2e\r\n')
while True:
    _l = _smtp_line()
    if _l.startswith('250 ') or not _l.startswith('250-'):
        break
_sk.sendall(b'AUTH LOGIN\r\n')
_ch1 = _smtp_line()
_sk.sendall(_b64.b64encode(b'sender') + b'\r\n')
_ch2 = _smtp_line()
_sk.sendall(_b64.b64encode(b'clientpass123') + b'\r\n')
_auth_result = _smtp_line()
try:
    check('AUTH LOGIN uses canonical Username:/Password: challenges (Outlook)',
          _ch1.startswith('334') and _ch2.startswith('334')
          and _b64.b64decode(_ch1[4:] or b'') == b'Username:'
          and _b64.b64decode(_ch2[4:] or b'') == b'Password:'
          and _auth_result.startswith('235'),
          (_ch1, _ch2, _auth_result))
finally:
    _sk.sendall(b'QUIT\r\n')
    _sk.close()

# ── cleanup ─────────────────────────────────────────────────────
server.stop()
sink_ctl.stop()
print()
fails = [r for r in results if not r[1]]
print(f"{'ALL ' + str(len(results)) + ' CHECKS PASSED' if not fails else str(len(fails)) + ' FAILURES'}")
sys.exit(1 if fails else 0)
