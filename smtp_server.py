"""
Async SMTP Relay Server – Windows-compatible.
Version 3.0.1

Designed and built by Christopher McGrath

No Unix signals, no fork.  Uses only threading + aiosmtpd.
"""

import datetime
import email
import ipaddress
import json
import logging
import os
import smtplib
import ssl
import sys
import threading
import time
from email.utils import parseaddr
from datetime import datetime as dt
import datetime as dt_module

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from models import (
    db, AllowedDomain, SmtpCredential, EmailLog, EmailQueue, RelayConfig
)

logger = logging.getLogger('smtp_relay')

# Debug logging helper functions
def debug_log(message):
    """Log debug message if debug logging is enabled."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        logger.debug(message)

def debug_log_connection(message, host, port):
    """Log connection attempt with host and port."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        logger.debug(f"Connection attempt: {message} {host}:{port}")

def debug_log_starttls(message):
    """Log STARTTLS attempt."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        logger.debug(f"STARTTLS attempt: {message}")

def debug_log_smtp_command(command):
    """Log SMTP command."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        logger.debug(f"SMTP command: {command}")

def debug_log_smtp_response(response):
    """Log SMTP response."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        logger.debug(f"SMTP response: {response}")

def debug_log_message_details(message_id, subject, sender, recipients, queue_id=None):
    """Log message details for tracking."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        msg = (f"Message details - ID: {message_id}, Subject: {subject}, "
               f"Sender: {sender}, Recipients: {recipients}")
        if queue_id is not None:
            msg += f" (queue_id={queue_id})"
        logger.debug(msg)

def debug_log_exception(exc, context):
    """Log exception with context."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        logger.debug(f"Exception in {context}: {exc}", exc_info=True)

def debug_log_timing(start_time, context):
    """Log timing information."""
    if getattr(logger, 'isEnabledFor', lambda level: False)(logging.DEBUG):
        elapsed = time.time() - start_time
        logger.debug(f"{context} took {elapsed:.3f} seconds")


# ── IP Allowlist Helper Functions ────────────────────────────────
def _parse_allowed_networks(allowed_csv: str) -> list:
    """Parse comma-separated list of IPs/CIDR networks.
    
    Supports:
    - Single IPs: 10.55.61.5
    - CIDR notation: 10.55.61.0/24
    
    Returns list of ipaddress.IPv4Network objects.
    """
    if not allowed_csv:
        return []
    
    networks = []
    for item in allowed_csv.split(','):
        item = item.strip()
        if not item:
            continue
        try:
            # Try as CIDR first
            if '/' in item:
                networks.append(ipaddress.IPv4Network(item, strict=False))
            else:
                # Single IP - convert to /32 network
                networks.append(ipaddress.IPv4Network(f"{item}/32", strict=False))
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as e:
            logger.warning("Invalid IP/CIDR in allowlist: %s - %s", item, e)
    return networks


def _is_ip_allowed(peer_ip: str, allowed_csv: str) -> bool:
    """Check if peer IP is in any of the allowed networks.
    
    Args:
        peer_ip: The IP address of the connecting client.
        allowed_csv: Comma-separated list of IPs or CIDR networks.
        
    Returns:
        True if the IP is allowed, False otherwise.
    """
    networks = _parse_allowed_networks(allowed_csv)
    if not networks:
        return True  # No restrictions
    
    try:
        ip = ipaddress.IPv4Address(peer_ip)
        return any(ip in network for network in networks)
    except ipaddress.AddressValueError:
        logger.warning("Invalid peer IP: %s", peer_ip)
        return False


# ── Authenticator ──────────────────────────────────────────────
class RelayAuthenticator:
    """SMTP authentication handler for validating client credentials.
    
    This class is called by aiosmtpd when a client attempts to authenticate
    using SMTP AUTH. It validates the username and password against the
    SmtpCredential database records.
    
    Attributes:
        app: The Flask application instance for database access.
    """
    
    def __init__(self, app):
        """Initialize the authenticator with the Flask app.
        
        Args:
            app: The Flask application instance.
        """
        self.app = app

    def __call__(self, server, session, envelope, mechanism, auth_data):
        """Handle SMTP AUTH login attempt.
        
        Called by aiosmtpd when a client authenticates. Extracts credentials
        from the auth_data, validates them against the database, and returns
        an AuthResult indicating success or failure.
        
        Args:
            server: The SMTP server instance.
            session: The SMTP session object.
            envelope: The SMTP envelope.
            mechanism: The authentication mechanism (e.g., 'LOGIN', 'PLAIN').
            auth_data: The authentication credentials (LoginPassword instance).
            
        Returns:
            AuthResult: Success or failure with optional message.
        """
        with self.app.app_context():
            try:
                if isinstance(auth_data, LoginPassword):
                    username = (auth_data.login.decode('utf-8')
                                if isinstance(auth_data.login, bytes)
                                else auth_data.login)
                    password = (auth_data.password.decode('utf-8')
                                if isinstance(auth_data.password, bytes)
                                else auth_data.password)
                else:
                    logger.warning("Unsupported auth mechanism: %s", mechanism)
                    return AuthResult(success=False, handled=False)

                cred = SmtpCredential.query.filter_by(
                    username=username, is_active=True
                ).first()

                if cred and cred.check_password(password):
                    logger.info("SMTP AUTH OK: %s", username)
                    session.auth_data = {
                        'username': username,
                        'credential_id': cred.id,
                    }
                    return AuthResult(success=True)

                logger.warning("SMTP AUTH FAIL: %s", username)
                return AuthResult(success=False, handled=False)
            except Exception as exc:
                logger.error("Auth error: %s", exc)
                return AuthResult(success=False, handled=False)


# ── Handler ────────────────────────────────────────────────────
class RelayHandler:
    """SMTP message handler for processing incoming emails.
    
    This handler implements the aiosmtpd protocol handlers to process
    incoming SMTP connections, validate senders/recipients, enforce
    rate limits, and queue messages for delivery.
    
    Attributes:
        app: The Flask application instance for database access.
    """
    
    def __init__(self, app):
        """Initialize the handler with the Flask app.
        
        Args:
            app: The Flask application instance.
        """
        self.app = app

    async def handle_EHLO(self, server, session, envelope, hostname, responses):
        """Handle the EHLO SMTP command.
        
        Args:
            server: The SMTP server instance.
            session: The SMTP session object.
            envelope: The SMTP envelope.
            hostname: The hostname provided by the client.
            responses: List of EHLO response values.
            
        Returns:
            The modified responses list.
        """
        session.host_name = hostname
        return responses

    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        """Handle the MAIL FROM SMTP command.
        
        Validates the sender address and enforces:
        - Authentication requirements
        - IP allowlist restrictions
        - Domain allowlist restrictions
        - Per-credential rate limits
        - Global rate limits
        
        Args:
            server: The SMTP server instance.
            session: The SMTP session object.
            envelope: The SMTP envelope.
            address: The sender email address.
            mail_options: Additional MAIL command options.
            
        Returns:
            SMTP response code and message.
        """
        with self.app.app_context():
            # Auth check - allow unauthenticated relay if no credentials exist
            require_auth = RelayConfig.get_bool('require_auth', True)
            if require_auth:
                # Check if any active credentials exist
                has_credentials = SmtpCredential.query.filter_by(is_active=True).count() > 0
                if has_credentials:
                    # Credentials exist, require authentication
                    if not getattr(session, 'auth_data', None):
                        return '530 5.7.0 Authentication required'
                # If no credentials exist, allow unauthenticated relay (open relay)

            # IP allowlist (supports CIDR notation)
            allowed_csv = RelayConfig.get('allowed_source_ips', '')
            if allowed_csv:
                peer_ip = session.peer[0] if session.peer else None
                if peer_ip and not _is_ip_allowed(peer_ip, allowed_csv):
                    logger.warning("Rejected IP: %s", peer_ip)
                    return '550 5.7.1 Connection from your IP is not allowed'

            # Domain allowlist
            _, sender_addr = parseaddr(address)
            if sender_addr and '@' in sender_addr:
                domain = sender_addr.split('@')[1].lower()
                rows = AllowedDomain.query.filter_by(is_active=True).all()
                if rows:
                    if domain not in [r.domain.lower() for r in rows]:
                        logger.warning("Rejected domain: %s", domain)
                        return f'550 5.7.1 Sender domain {domain} is not authorized'

            # Per-credential rate limit
            auth = getattr(session, 'auth_data', None)
            if auth:
                cred = SmtpCredential.query.get(auth['credential_id'])
                if cred and not cred.check_rate_limit():
                    logger.warning("Rate limit hit: %s", cred.username)
                    return '450 4.7.1 Rate limit exceeded, try again later'

            # Global rate limit
            limit = RelayConfig.get_int('global_rate_limit', 1000)
            since = dt_module.datetime.utcnow() - dt_module.timedelta(hours=1)
            recent = EmailLog.query.filter(
                EmailLog.timestamp >= since,
                EmailLog.status.in_(['sent', 'queued']),
            ).count()
            if recent >= limit:
                logger.warning("Global rate limit exceeded")
                return '450 4.7.1 Server rate limit exceeded'

            envelope.mail_from = address
            return '250 OK'

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """Handle the RCPT TO SMTP command.
        
        Validates recipient count doesn't exceed configured limit.
        
        Args:
            server: The SMTP server instance.
            session: The SMTP session object.
            envelope: The SMTP envelope.
            address: The recipient email address.
            rcpt_options: Additional RCPT command options.
            
        Returns:
            SMTP response code and message.
        """
        with self.app.app_context():
            cap = RelayConfig.get_int('max_recipients', 100)
            if len(envelope.rcpt_tos) >= cap:
                return f'452 4.5.3 Too many recipients (max {cap})'
            envelope.rcpt_tos.append(address)
            return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        """Handle the DATA SMTP command.
        
        Receives the email content, validates size limits, logs the message,
        and queues it for delivery to the relay destination.
        
        Args:
            server: The SMTP server instance.
            session: The SMTP session object.
            envelope: The SMTP envelope containing message content.
            
        Returns:
            SMTP response code and message.
        """
        with self.app.app_context():
            try:
                max_sz = RelayConfig.get_int('max_message_size', 26214400)
                if len(envelope.content) > max_sz:
                    return f'552 5.3.4 Message too large (max {max_sz} bytes)'

                msg = email.message_from_bytes(envelope.content)
                subject = msg.get('Subject', '(no subject)')
                message_id = msg.get('Message-ID', '')

                # Extract all email headers as a single text block
                raw_headers = ''
                try:
                    header_lines = []
                    for key, value in msg.items():
                        header_lines.append(f'{key}: {value}')
                    raw_headers = '\n'.join(header_lines)
                except Exception as hdr_exc:
                    logger.warning("Failed to extract headers: %s", hdr_exc)

                smtp_user = None
                auth = getattr(session, 'auth_data', None)
                if auth:
                    smtp_user = auth.get('username')

                peer_ip = session.peer[0] if session.peer else 'unknown'

                log_entry = EmailLog(
                    sender=envelope.mail_from,
                    recipients=json.dumps(envelope.rcpt_tos),
                    subject=subject,
                    size_bytes=len(envelope.content),
                    status='queued',
                    smtp_credential=smtp_user,
                    source_ip=peer_ip,
                    relay_server=RelayConfig.get('relay_host', 'localhost'),
                    message_id=message_id,
                    raw_headers=raw_headers,
                )
                db.session.add(log_entry)
                db.session.flush()

                q = EmailQueue(
                    sender=envelope.mail_from,
                    recipients=json.dumps(envelope.rcpt_tos),
                    raw_message=envelope.content,
                    status='queued',
                    log_id=log_entry.id,
                )
                db.session.add(q)

                if auth:
                    cred = SmtpCredential.query.get(auth['credential_id'])
                    if cred:
                        cred.increment_send_count()

                db.session.commit()
                logger.info("Queued: %s -> %s (%s)",
                            envelope.mail_from, envelope.rcpt_tos, subject)

                # Fire-and-forget delivery thread
                threading.Thread(
                    target=self._deliver, args=(q.id,), daemon=True
                ).start()

                return '250 Message accepted for delivery'
            except Exception as exc:
                logger.error("DATA error: %s", exc, exc_info=True)
                db.session.rollback()
                return '451 4.3.0 Internal server error'

    # ── delivery worker (runs in a thread) ─────────────────────
    def _deliver(self, queue_id):
        """Deliver a queued email to the relay destination.

        This method runs in a separate thread. It connects to the configured
        relay server and delivers the queued email. Handles retries on failure
        and updates the queue/log status accordingly.

        Args:
            queue_id: The ID of the EmailQueue entry to deliver.
        """
        with self.app.app_context():
            try:
                q = EmailQueue.query.get(queue_id)
                if not q or q.status != 'queued':
                    return
                q.status = 'processing'
                db.session.commit()

                host = RelayConfig.get('relay_host', 'localhost')
                port = RelayConfig.get_int('relay_port', 25)
                use_tls = RelayConfig.get_bool('relay_use_tls', False)
                use_starttls = RelayConfig.get_bool('relay_use_starttls', False)
                auth_user = RelayConfig.get('relay_auth_user', '').strip()
                auth_pass = RelayConfig.get('relay_auth_password', '').strip()
                helo_name = RelayConfig.get('relay_helo_hostname', 'localhost')

                recipients = json.loads(q.recipients)

                # Log message details for tracking
                log_entry = EmailLog.query.get(q.log_id) if q.log_id else None
                message_id = log_entry.message_id if log_entry else None
                subject = log_entry.subject if log_entry else '(unknown)'
                debug_log_message_details(
                    message_id, subject, q.sender, recipients,
                    queue_id=queue_id
                )

                logger.info(
                    "Delivering queue #%s: %s -> %s via %s:%s "
                    "(tls=%s starttls=%s auth_user=%s helo=%s)",
                    queue_id, q.sender, recipients, host, port,
                    use_tls, use_starttls,
                    auth_user if auth_user else '(none)', helo_name,
                )

                # Build the connection and deliver.
                conn = None
                start_time = time.time()
                try:
                    if use_tls:
                        ctx = ssl.create_default_context()
                        debug_log_connection(
                            "Connecting via SMTP_SSL",
                            host, port
                        )
                        conn = smtplib.SMTP_SSL(
                            host, port,
                            local_hostname=helo_name, context=ctx, timeout=30
                        )
                    else:
                        debug_log_connection(
                            "Connecting via SMTP",
                            host, port
                        )
                        conn = smtplib.SMTP(
                            host, port,
                            local_hostname=helo_name, timeout=30
                        )

                    # Ensure smtplib knows the remote hostname (Python 3.14+
                    # requires this for STARTTLS server-name verification).
                    if not getattr(conn, '_host', ''):
                        conn._host = host

                    conn.ehlo(helo_name)
                    debug_log_smtp_command(f"EHLO {helo_name} [queue_id={queue_id}]")

                    # Optionally upgrade to STARTTLS
                    if use_starttls and not use_tls:
                        try:
                            ctx = ssl.create_default_context()
                            debug_log_starttls(f"Upgrading to STARTTLS [queue_id={queue_id}]")
                            conn.starttls(context=ctx)
                            conn.ehlo(helo_name)
                            debug_log_smtp_command(f"EHLO after STARTTLS [queue_id={queue_id}]")
                        except smtplib.SMTPNotSupportedError:
                            logger.warning("STARTTLS not supported by %s", host)
                            debug_log(f"STARTTLS not supported by server [queue_id={queue_id}]")

                    # Optionally authenticate (skip if credentials are empty)
                    if auth_user and auth_pass:
                        try:
                            conn.login(auth_user, auth_pass)
                            debug_log_smtp_command(f"AUTH LOGIN {auth_user} [queue_id={queue_id}]")
                        except smtplib.SMTPException as auth_exc:
                            logger.warning("Authentication failed: %s", auth_exc)
                            debug_log_exception(auth_exc, f"SMTP authentication [queue_id={queue_id}]")
                            raise

                    # Send the email
                    conn.sendmail(q.sender, recipients, q.raw_message)
                    debug_log_smtp_command(f"SENDMAIL to {len(recipients)} recipients [queue_id={queue_id}]")

                    q.status = 'sent'
                    if log_entry:
                        log_entry.status = 'sent'
                        log_entry.status_message = f'Delivered via {host}:{port}'
                        log_entry.retry_count = q.retry_count
                    db.session.commit()
                    logger.info("Delivered queue #%s via %s:%s", queue_id, host, port)
                    debug_log_timing(start_time, f"Delivery completed [host={host} port={port}]")
                finally:
                    if conn is not None:
                        try:
                            conn.quit()
                            debug_log_smtp_command(f"QUIT [queue_id={queue_id}]")
                        except Exception:
                            pass

            except Exception as exc:
                logger.error("Delivery failed #%s: %s", queue_id, exc,
                             exc_info=True)
                debug_log_exception(exc, f"Delivery failed [queue_id={queue_id}]")
                try:
                    q = EmailQueue.query.get(queue_id)
                    if q:
                        max_r = RelayConfig.get_int('queue_max_retries', 3)
                        interval = RelayConfig.get_int('queue_retry_interval', 300)
                        q.retry_count += 1
                        q.last_error = str(exc)
                        if q.retry_count >= max_r:
                            q.status = 'failed'
                            if log_entry:
                                log_entry.status = 'failed'
                                log_entry.status_message = (
                                    f'Failed after {max_r} attempts: {exc}')
                                log_entry.retry_count = q.retry_count
                        else:
                            q.status = 'queued'
                            q.next_retry_at = (
                                dt_module.datetime.utcnow()
                                + dt_module.timedelta(seconds=interval * q.retry_count)
                            )
                        db.session.commit()
                except Exception as inner:
                    logger.error("Queue status update error: %s", inner)
                    debug_log_exception(inner, f"Queue status update [queue_id={queue_id}]")
                    db.session.rollback()


# ── Server wrapper ─────────────────────────────────────────────
class SmtpRelayServer:
    """SMTP relay server manager.
    
    This class wraps the aiosmtpd Controller to provide a simpler interface
    for starting, stopping, and checking the status of the SMTP server.
    It handles configuration loading and Windows-specific fallback behavior.
    
    Attributes:
        app: The Flask application instance.
        controller: The aiosmtpd Controller instance.
        _running: Flag indicating if the server is currently running.
    """
    
    def __init__(self, app):
        """Initialize the SMTP server with the Flask app.
        
        Args:
            app: The Flask application instance.
        """
        self.app = app
        self.controller = None
        self._running = False

    def start(self):
        """Start the SMTP relay server.
        
        Loads configuration from the database, sets up TLS if enabled,
        and starts listening for incoming SMTP connections.
        On Windows, if binding to 0.0.0.0 fails, falls back to 127.0.0.1.
        """
        if self._running:
            logger.warning("SMTP server already running")
            return

        with self.app.app_context():
            host = RelayConfig.get('listen_host', '0.0.0.0')
            port = RelayConfig.get_int('listen_port', 2525)
            banner = RelayConfig.get('banner_hostname', 'relay.local')
            require_auth = RelayConfig.get_bool('require_auth', True)
            max_sz = RelayConfig.get_int('max_message_size', 26214400)
            enable_tls = RelayConfig.get_bool('enable_tls', False)

        handler = RelayHandler(self.app)
        authenticator = RelayAuthenticator(self.app) if require_auth else None

        tls_ctx = None
        if enable_tls:
            with self.app.app_context():
                cert = RelayConfig.get('tls_cert_path', '')
                key = RelayConfig.get('tls_key_path', '')
            if cert and key:
                tls_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                tls_ctx.load_cert_chain(cert, key)
            else:
                logger.warning("TLS enabled but cert/key paths are empty – "
                               "listener will run without TLS")

        kw = dict(
            handler=handler,
            hostname=host,
            port=port,
            ident=f'SMTP Relay {banner}',
            data_size_limit=max_sz,
        )
        if authenticator:
            kw['authenticator'] = authenticator
            kw['auth_required'] = require_auth
            kw['auth_require_tls'] = False
        if tls_ctx:
            kw['tls_context'] = tls_ctx

        # Try to start; if 0.0.0.0 fails on Windows, fall back to 127.0.0.1
        try:
            self.controller = Controller(**kw)
            self.controller.start()
            self._running = True
            logger.info("SMTP server listening on %s:%s", host, port)
        except OSError as exc:
            if host == '0.0.0.0':
                logger.warning(
                    "Bind to 0.0.0.0:%s failed (%s), retrying with 127.0.0.1",
                    port, exc,
                )
                kw['hostname'] = '127.0.0.1'
                self.controller = Controller(**kw)
                self.controller.start()
                self._running = True
                logger.info("SMTP server listening on 127.0.0.1:%s (fallback)", port)
            else:
                raise

    def stop(self):
        """Stop the SMTP relay server.
        
        Stops the aiosmtpd controller and updates the running flag.
        """
        if self.controller and self._running:
            try:
                self.controller.stop()
            except Exception as exc:
                logger.error("Stop error: %s", exc)
            self._running = False
            logger.info("SMTP server stopped")

    @property
    def is_running(self):
        """Check if the SMTP server is actually running.
        
        Returns:
            True if the server is running, False otherwise.
        """
        if not self._running or self.controller is None:
            return False
        # Try to verify the controller thread is still alive.
        # aiosmtpd uses _thread in some versions, _thread in others.
        for attr in ('_thread', 'thread'):
            thread = getattr(self.controller, attr, None)
            if thread is not None:
                if not thread.is_alive():
                    logger.warning("SMTP controller thread died unexpectedly")
                    self._running = False
                    return False
                return True  # thread found and alive
        # Could not find thread attribute — fall back to flag only
        return self._running

    def restart(self):
        """Restart the SMTP relay server.
        
        Stops the server, waits 1 second, then starts it again.
        """
        logger.info("Restarting SMTP server …")
        self.stop()
        time.sleep(1)
        self.start()


# ── Queue processor (background thread) ───────────────────────
class QueueProcessor:
    """Background processor for retrying failed email deliveries.
    
    This class runs a background thread that periodically checks for
    queued emails that need to be retried and dispatches delivery
    workers for each.
    
    Attributes:
        app: The Flask application instance.
        _running: Flag indicating if the processor is active.
        _thread: The background thread instance.
    """
    
    def __init__(self, app):
        """Initialize the queue processor with the Flask app.
        
        Args:
            app: The Flask application instance.
        """
        self.app = app
        self._running = False
        self._thread = None

    def start(self):
        """Start the queue processor background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Queue processor started")

    def stop(self):
        """Stop the queue processor background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Queue processor stopped")

    def _loop(self):
        """Main loop that periodically checks for pending queue entries.
        
        Runs every 30 seconds, looking for queued messages that are
        due for retry.
        """
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.error("Queue tick error: %s", exc)
            time.sleep(30)

    def _tick(self):
        """Pick up queued messages and dispatch each one in its own thread.

        Previously _deliver() was called directly here, which meant a 30-second
        SMTP connect timeout would block the queue thread and hold a SQLite
        connection open for the entire duration.  Spawning a daemon thread per
        message keeps the queue loop responsive and releases the DB connection
        immediately after the IDs are fetched.
        """
        with self.app.app_context():
            now = dt_module.datetime.utcnow()
            pending = (
                EmailQueue.query
                .filter(EmailQueue.status == 'queued',
                        EmailQueue.next_retry_at <= now)
                .order_by(EmailQueue.next_retry_at)
                .limit(10)
                .all()
            )
            ids = [entry.id for entry in pending]

        handler = RelayHandler(self.app)
        for queue_id in ids:
            threading.Thread(
                target=handler._deliver, args=(queue_id,), daemon=True
            ).start()

    def cleanup_old(self):
        """Remove old log and queue entries based on retention settings.
        
        Deletes sent queue entries and email logs older than the configured
        retention period. Failed queue entries are retained until manually
        handled by an administrator.
        """
        with self.app.app_context():
            days = RelayConfig.get_int('log_retention_days', 30)
            cutoff = dt_module.datetime.utcnow() - dt_module.timedelta(days=days)
            # Only purge 'sent' queue entries — failed entries are retained
            # until an administrator manually retries or deletes them.
            dq = EmailQueue.query.filter(
                EmailQueue.status == 'sent',
                EmailQueue.created_at < cutoff,
            ).delete(synchronize_session=False)
            dl = EmailLog.query.filter(
                EmailLog.timestamp < cutoff,
            ).delete(synchronize_session=False)
            if dq or dl:
                db.session.commit()
                logger.info("Cleaned %s queue + %s log entries "
                            "(failed entries retained)", dq, dl)
