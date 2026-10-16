"""
Microsoft Entra ID (Azure AD) OIDC sign-in for the SMTP relay web UI.
Version 4.5.1

Designed and built by Christopher McGrath

Identity-only OIDC, ported from the newprojectwith365auth framework:
scopes are openid/profile/email — the Microsoft token identifies the
human and is NEVER stored; the session holds only the local user id.
Authorization is the relay's own users table: a valid Microsoft identity
establishes a session ONLY when it matches a registered, active account
(matched first by Entra object id, then by email among Entra accounts).

Break-glass: accounts with auth_source='local' keep username/password
sign-in through the normal /login route. Local accounts are admin-only
by convention (enforced in the users routes).
"""

import logging
import secrets
import datetime

import msal
from flask import Blueprint, redirect, url_for, flash, request, session
from flask_login import login_user, current_user
from sqlalchemy import func

from crypto_utils import decrypt_secret
from models import db, User, RelayConfig

logger = logging.getLogger('smtp_relay')

# Identity-only: no Graph scopes, nothing operational, no consent prompt.
# MSAL reserves 'openid', 'profile' and 'offline_access' — passing any of
# them raises ValueError("You cannot use any scope value that is reserved")
# inside _decorate_scope, BEFORE Microsoft is ever contacted.  The library
# unions the reserved scopes into both the authorize URL and the token
# exchange automatically, so we only ask for the one optional claim scope
# we actually need (the email claim for JIT account matching).
LOGIN_SCOPES = ['email']


def _build_msal_app(tenant_id, client_id, client_secret):
    """Build a ConfidentialClientApplication for the configured tenant."""
    return msal.ConfidentialClientApplication(
        client_id,
        authority=f'https://login.microsoftonline.com/{tenant_id}',
        client_credential=client_secret,
    )


# Indirection point for tests: monkeypatch to mock token redemption.
msal_app_factory = _build_msal_app


def create_oauth_blueprint(app):
    """Create the Microsoft sign-in blueprint bound to the Flask app."""
    bp = Blueprint('auth_oauth', __name__)

    def _azure_settings():
        """Return (tenant_id, client_id, secret, redirect_uri) when the
        Microsoft sign-in is enabled AND fully configured, else None.

        The client secret is stored ENC1-encrypted (same scheme as the
        upstream relay passwords) and is decrypted here, per request.
        A rotated web.secret_key therefore disables Microsoft sign-in
        until the secret is re-entered — the same failure mode as the
        upstream credentials, by design.
        """
        if not RelayConfig.get_bool('azure_enabled', False):
            return None
        tenant_id = (RelayConfig.get('azure_tenant_id', '') or '').strip()
        client_id = (RelayConfig.get('azure_client_id', '') or '').strip()
        redirect_uri = (RelayConfig.get('azure_redirect_uri', '') or '').strip()
        if not tenant_id or not client_id:
            return None
        secret = decrypt_secret(
            RelayConfig.get('azure_client_secret', '') or '', app.secret_key)
        if not secret:
            logger.warning(
                'Microsoft sign-in is enabled but the client secret could '
                'not be decrypted (web.secret_key rotated?) — re-enter it '
                'in Configuration.')
            return None
        return tenant_id, client_id, secret, redirect_uri

    def _redirect_uri(settings):
        """Callback URL for the Azure app registration.

        An explicit azure_redirect_uri is the reliable option behind a
        reverse proxy or multi-origin access (LAN IP + public hostname):
        deriving it from the request reproduces exactly the
        state_mismatch class of bugs the 365auth framework documents.
        """
        explicit = settings[3]
        if explicit:
            return explicit
        return url_for('auth_oauth.callback', _external=True)

    def _throttle_key(email):
        from app import _login_key
        return _login_key(request.remote_addr, email or '')

    @bp.route('/login/azure')
    def start():
        """Begin the OIDC authorization-code flow."""
        from app import _login_throttled
        if _login_throttled(_throttle_key(None)):
            flash('Too many sign-in attempts. Try again later.', 'danger')
            return redirect(url_for('login'))
        settings = _azure_settings()
        if not settings:
            flash('Microsoft sign-in is not enabled or not fully '
                  'configured.', 'warning')
            return redirect(url_for('login'))

        state = secrets.token_urlsafe(32)
        session['azure_state'] = state
        session['azure_redirect_uri'] = _redirect_uri(settings)
        tenant_id, client_id, secret, _ = settings
        auth_url = msal_app_factory(
            tenant_id, client_id, secret).get_authorization_request_url(
            LOGIN_SCOPES,
            state=state,
            redirect_uri=session['azure_redirect_uri'],
            prompt='select_account',  # never a consent prompt for OIDC scopes
        )
        return redirect(auth_url)

    @bp.route('/login/azure/callback')
    def callback():
        """Redeem the code, resolve the LOCAL user, establish the session."""
        settings = _azure_settings()
        if not settings:
            return redirect(url_for('login'))

        code = request.args.get('code', '')
        state = request.args.get('state', '')
        expected_state = session.pop('azure_state', None)
        expected_uri = session.pop('azure_redirect_uri', None) \
            or _redirect_uri(settings)
        email_hint = request.args.get('login_hint', '')

        from app import _login_throttled
        if _login_throttled(_throttle_key(email_hint)):
            flash('Too many sign-in attempts. Try again later.', 'danger')
            return redirect(url_for('login'))

        if (request.args.get('error', '')
                and expected_state and state == expected_state):
            # A VALID-state error is Microsoft reporting the flow itself —
            # almost always the user clicking Cancel (error=access_denied).
            # That is not tampering: neutral message, no throttle failure.
            # (Recording it as a failure let a user lock themselves out by
            # cancelling sign-in five times, and blamed them for tampering.)
            logger.info('Microsoft sign-in returned error=%s (ip=%s)',
                        request.args.get('error'), request.remote_addr)
            flash('Microsoft sign-in was cancelled.', 'info')
            return redirect(url_for('login'))

        if not code or not expected_state or state != expected_state:
            from app import _record_login_failure
            _record_login_failure(_throttle_key(email_hint))
            logger.warning('Microsoft sign-in state mismatch (ip=%s)',
                           request.remote_addr)
            flash('Sign-in could not be verified (stale or tampered '
                  'response). Please try again.', 'danger')
            return redirect(url_for('login'))

        tenant_id, client_id, secret, _ = settings
        result = msal_app_factory(
            tenant_id, client_id, secret).acquire_token_by_authorization_code(
            code, scopes=LOGIN_SCOPES, redirect_uri=expected_uri)

        if 'error' in result:
            from app import _record_login_failure
            _record_login_failure(_throttle_key(email_hint))
            logger.error('Microsoft token redemption failed: %s: %s',
                         result.get('error'),
                         result.get('error_description', ''))
            flash('Microsoft sign-in failed. Please try again.', 'danger')
            return redirect(url_for('login'))

        claims = result.get('id_token_claims', {})
        oid = claims.get('oid') or ''
        email = (claims.get('email')
                 or claims.get('preferred_username') or '').lower()
        if not oid or not email:
            logger.error('Microsoft response missing oid/email claims')
            flash('Microsoft did not return a usable identity. Sign-in '
                  'rejected.', 'danger')
            return redirect(url_for('login'))

        # Authorization gate: the local users table IS the allow-list.
        user = User.query.filter_by(entra_oid=oid).first()
        if user is None and email:
            user = User.query.filter(
                func.lower(User.email) == email,
                User.auth_source == 'entra',
            ).first()
            if user is not None:
                user.entra_oid = oid  # first Microsoft login: bind the id
                logger.info('Bound Entra OID to account %s', user.username)

        if user is None or not user.is_active:
            logger.warning('Microsoft sign-in denied for %s: account not '
                           'registered or inactive', email)
            flash('This Microsoft account is not registered for this '
                  'application. Ask an administrator to add it in User '
                  'Management.', 'danger')
            return redirect(url_for('login'))

        user.last_login = datetime.datetime.utcnow()
        db.session.commit()
        login_user(user, remember=False)
        from app import _clear_login_failures
        _clear_login_failures(_throttle_key(email))
        flash(f'Signed in as {user.username}.', 'success')
        return redirect(url_for('dashboard'))

    return bp
