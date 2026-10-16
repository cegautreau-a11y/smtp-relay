# DKIM Signing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DKIM signing to emails sent through the SMTP relay. Emails are signed at delivery time with per-domain configurable selectors and keys stored in SQLite.

**Architecture:** Sign at delivery time in `_deliver()`. Check sender domain → look up active DKIM key → sign email → deliver signed version. Key changes take effect on next message automatically.

**Tech Stack:** Python 3.12, dkimpy, SQLite, Flask

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `requirements.txt` | Modify | Add dkimpy |
| `models.py` | Modify | Add DkimKey model |
| `smtp_server.py` | Modify | Add DkimSigner class, signing in _deliver |
| `app.py` | Modify | Add /dkim routes |
| `templates/dkim.html` | Create | DKIM management UI |
| `templates/base.html` | Modify | Add DKIM nav link |
| `CHANGELOG.md` | Modify | Document changes |

---

## Task 1: Add dkimpy Dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dkimpy to requirements.txt**

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
  dnspython>=2.6.0
+ dkimpy>=1.0.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt && git commit -m "deps: add dkimpy for DKIM signing"
```

---

## Task 2: Add DkimKey Model to models.py

**Files:**
- Modify: `models.py`

**Changes:** Add `DkimKey` model with domain, selector, private_key, public_key, is_active, created_at, created_by fields.

- [ ] **Step 1: Read models.py to understand existing model patterns**

Use an explore agent to read `/home/cegautreau/smtp-relay-beta/models.py` and understand how other models (e.g., SmtpCredential, AllowedDomain) are structured.

- [ ] **Step 2: Add DkimKey model after the RelayConfig class**

Find where RelayConfig is defined. Add the DkimKey model after it:

```python
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
    created_at = db.Column(db.DateTime, default=dt_module.datetime.utcnow, nullable=False)
    created_by = db.Column(db.String(120), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('domain', 'selector', name='uix_domain_selector'),
    )

    @classmethod
    def get_active_key(cls, domain: str) -> 'DkimKey | None':
        """Get the active DKIM key for a domain."""
        return cls.query.filter_by(domain=domain.lower(), is_active=True).first()

    def get_dns_record(self) -> str:
        """Return the DNS TXT record value for this DKIM key."""
        return f'v=DKIM1; k=rsa; p={self.public_key}'
```

- [ ] **Step 3: Commit**

```bash
git add models.py && git commit -m "model: add DkimKey model for DKIM signing keys"
```

---

## Task 3: Add DkimSigner Class to smtp_server.py

**Files:**
- Modify: `smtp_server.py`

**Changes:** Add a `DkimSigner` class that wraps dkimpy for signing emails. Add signing call in `_deliver()` before sending.

- [ ] **Step 1: Add DkimSigner class after the debug logging helpers (after line 79)**

```python
class DkimSigner:
    """DKIM email signer using dkimpy.

    Signs emails at delivery time using the domain's active DKIM key.
    """

    def __init__(self, app):
        """Initialize the signer with the Flask app.

        Args:
            app: The Flask application instance.
        """
        self.app = app

    def sign_email(self, email_content: bytes, sender_domain: str) -> bytes | None:
        """Sign an email with DKIM.

        Args:
            email_content: Raw email bytes.
            sender_domain: The sender's domain (e.g., example.com).

        Returns:
            Signed email bytes with DKIM-Signature header added,
            or None if no active key exists for the domain.
        """
        with self.app.app_context():
            dkim_key = DkimKey.get_active_key(sender_domain)
            if not dkim_key:
                return None

            try:
                import dkim
            except ImportError:
                logger.warning("dkimpy not available, skipping DKIM signing")
                return None

            try:
                sig = dkim.sign(
                    message=email_content,
                    selector=dkim_key.selector.encode('utf-8'),
                    domain=dkim_key.domain.encode('utf-8'),
                    privkey=dkim_key.private_key.encode('utf-8'),
                    identity=(f'@{dkim_key.domain}'.encode('utf-8')),
                )
                signed_email = email_content + b'\r\n' + sig
                logger.debug(
                    "DKIM signed email for %s (selector: %s)",
                    dkim_key.domain, dkim_key.selector
                )
                return signed_email
            except Exception as exc:
                logger.error("DKIM signing failed for %s: %s", sender_domain, exc)
                return None
```

- [ ] **Step 2: Modify `_deliver()` to sign before sending**

In `_deliver()` method, after fetching config values and before connecting to SMTP, add DKIM signing:

Find this section in `_deliver()`:
```python
                logger.info(
                    "Delivering queue #%s: %s -> %s via %s:%s "
                    "(tls=%s starttls=%s auth_user=%s helo=%s)",
                    queue_id, q.sender, recipients, host, port,
                    use_tls, use_starttls,
                    auth_user if auth_user else '(none)', helo_name,
                )
```

Add after it:
```python
                # DKIM signing
                if sender_domain := (q.sender.split('@')[1].lower() if q.sender and '@' in q.sender else None):
                    signer = DkimSigner(self.app)
                    signed_content = signer.sign_email(q.raw_message, sender_domain)
                    if signed_content:
                        q.raw_message = signed_content
                        logger.debug("DKIM signed message for queue #%s", queue_id)
```

Note: `q.raw_message` is updated so the signed content is sent. The original in queue entry remains unchanged for debugging purposes.

- [ ] **Step 3: Commit**

```bash
git add smtp_server.py && git commit -m "feat: add DkimSigner and DKIM signing in _deliver"
```

---

## Task 4: Add DKIM Routes to app.py

**Files:**
- Modify: `app.py`

**Changes:** Add `/dkim` routes for listing, generating, activating, and deleting DKIM keys.

- [ ] **Step 1: Add DKIM routes after the domains routes**

Find where the domains routes end (around line 492 in app.py). Add these routes after `delete_domain`:

```python
    # ── DKIM Keys ───────────────────────────────────────────────
    @app.route('/dkim')
    @login_required
    @operator_required
    def dkim_keys():
        """Display DKIM keys management page."""
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

        if not domain.replace('.', '').replace('-', '').isalnum():
            flash('Invalid domain name.', 'danger')
            return redirect(url_for('dkim_keys'))

        if not selector.replace('-', '').isalnum():
            flash('Invalid selector. Use only letters, numbers, and hyphens.', 'danger')
            return redirect(url_for('dkim_keys'))

        if DkimKey.query.filter_by(domain=domain, selector=selector).first():
            flash(f'Key for {domain} with selector {selector} already exists.', 'warning')
            return redirect(url_for('dkim_keys'))

        try:
            import dkim
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
            public_key = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )

            dkim_key = DkimKey(
                domain=domain,
                selector=selector,
                private_key=private_pem.decode('utf-8'),
                public_key=public_key.decode('utf-8').replace(
                    '-----BEGIN PUBLIC KEY-----', ''
                ).replace('-----END PUBLIC KEY-----', '').replace('\n', ''),
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
        """Deactivate a DKIM key."""
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
```

- [ ] **Step 2: Add DkimKey to imports**

Find the models import in app.py (around line 27-30). Add `DkimKey` to the import:

```python
from models import (
    db, User, AllowedDomain, SmtpCredential, EmailLog,
    RelayConfig, EmailQueue, Role, DkimKey,
)
```

- [ ] **Step 3: Add cryptography dependency to requirements.txt**

The DKIM key generation uses `cryptography` library. Add it:

```diff
+ dkimpy>=1.0.0
+ cryptography>=42.0.0
```

- [ ] **Step 4: Commit**

```bash
git add app.py requirements.txt && git commit -m "feat: add DKIM key management routes"
```

---

## Task 5: Create DKIM Management Template

**Files:**
- Create: `templates/dkim.html`

- [ ] **Step 1: Create dkim.html**

```html
{% extends "base.html" %}

{% block title %}DKIM Keys{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-3">
        {% include "sidebar.html" or omit %}
    </div>
    <div class="col-md-9">
        <h2 class="mb-4">DKIM Keys</h2>

        {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
        {% for category, message in messages %}
        <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
            {{ message }}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
        {% endfor %}
        {% endif %}
        {% endwith %}

        <div class="card mb-4">
            <div class="card-header">Generate New DKIM Key</div>
            <div class="card-body">
                <form method="POST" action="{{ url_for('generate_dkim_key') }}" class="row g-3">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <div class="col-md-4">
                        <label for="domain" class="form-label">Domain</label>
                        <input type="text" class="form-control" id="domain" name="domain" placeholder="example.com" required>
                    </div>
                    <div class="col-md-3">
                        <label for="selector" class="form-label">Selector</label>
                        <input type="text" class="form-control" id="selector" name="selector" placeholder="mail" required>
                    </div>
                    <div class="col-md-3 d-flex align-items-end">
                        <button type="submit" class="btn btn-primary">Generate Key Pair</button>
                    </div>
                </form>
                <small class="text-muted">Selector defines DNS record: <code>&lt;selector&gt;._domainkey.&lt;domain&gt;</code></small>
            </div>
        </div>

        <div class="card">
            <div class="card-header">DKIM Keys</div>
            <div class="card-body table-responsive">
                {% if not keys %}
                <p class="text-muted">No DKIM keys yet. Generate one above.</p>
                {% else %}
                <table class="table table-hover">
                    <thead>
                        <tr>
                            <th>Domain</th>
                            <th>Selector</th>
                            <th>Status</th>
                            <th>Created</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for key in keys %}
                        <tr>
                            <td><code>{{ key.domain }}</code></td>
                            <td><code>{{ key.selector }}</code></td>
                            <td>
                                {% if key.is_active %}
                                <span class="badge bg-success">Active</span>
                                {% else %}
                                <span class="badge bg-secondary">Inactive</span>
                                {% endif %}
                            </td>
                            <td>{{ key.created_at.strftime('%Y-%m-%d') }}</td>
                            <td>
                                <div class="btn-group btn-group-sm">
                                    {% if not key.is_active %}
                                    <form method="POST" action="{{ url_for('activate_dkim_key', kid=key.id) }}" class="d-inline">
                                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                                        <button type="submit" class="btn btn-outline-success btn-sm">Activate</button>
                                    </form>
                                    {% else %}
                                    <form method="POST" action="{{ url_for('deactivate_dkim_key', kid=key.id) }}" class="d-inline">
                                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                                        <button type="submit" class="btn btn-outline-warning btn-sm">Deactivate</button>
                                    </form>
                                    {% endif %}
                                    <form method="POST" action="{{ url_for('delete_dkim_key', kid=key.id) }}" class="d-inline" onsubmit="return confirm('Delete this key?')">
                                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                                        <button type="submit" class="btn btn-outline-danger btn-sm">Delete</button>
                                    </form>
                                </div>
                            </td>
                        </tr>
                        {% if key.is_active %}
                        <tr class="table-light">
                            <td colspan="5">
                                <strong>DNS TXT Record:</strong>
                                <div class="mt-2 mb-1 small text-muted">Add this TXT record to your DNS provider:</div>
                                <div class="bg-dark text-white rounded p-3 font-monospace small">
                                    <div><strong>Name:</strong> {{ key.selector }}._domainkey.{{ key.domain }}</div>
                                    <div><strong>Type:</strong> TXT</div>
                                    <div><strong>Value:</strong> v=DKIM1; k=rsa; p={{ key.public_key }}</div>
                                </div>
                            </td>
                        </tr>
                        {% endif %}
                        {% endfor %}
                    </tbody>
                </table>
                {% endif %}
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/dkim.html && git commit -m "ui: add DKIM key management page"
```

---

## Task 6: Update CHANGELOG.md

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add DKIM signing entry to CHANGELOG.md**

Read the current CHANGELOG.md header. Add entry:

```markdown
## Changes (2026-04-22)

### Feature
| Issue | Fix | Files |
| Add Linux Docker support | Containerized application with python:3.12-slim base image | Dockerfile, docker-compose.yml, .env.example |
| Add Direct Delivery mode | MX lookup delivery bypassing upstream relay | smtp_server.py, models.py, app.py, templates/config.html |
| Add env var config override | All settings overridable via environment variables | run.py |
| Add DKIM signing | Per-domain DKIM keys with signing at delivery time | models.py, smtp_server.py, app.py, templates/dkim.html |
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md && git commit -m "docs: add DKIM signing to changelog"
```

---

## Verification

After all tasks complete:

1. **Docker build test:**
   ```bash
   docker build -t smtp-relay:test /home/cegautreau/smtp-relay-beta
   ```

2. **Key generation test:**
   ```bash
   docker run --rm -e SECRET_KEY=test-secret -p 8025:8025 -p 2525:2525 smtp-relay:test
   ```
   Then navigate to /dkim, generate a key, verify DNS record display.

3. **Push to beta repo:**
   ```bash
   git push beta main
   ```

---

## Spec Coverage Check

| Spec Requirement | Task(s) |
|---|---|
| Multiple domains, each with own key | Task 2, 3, 4, 5 |
| Keys in database | Task 2 |
| Semi-automated DNS display | Task 5 |
| Configurable selector per domain | Task 4, 5 |
| Manual key rotation | Task 4, 5 |
| Sign in both delivery modes | Task 3 |
| DKIM management UI | Task 5 |

**Placeholder scan:** None found.
**Type consistency:** `DkimKey` model used consistently across all tasks.
