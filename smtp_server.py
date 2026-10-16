"""
Async SMTP Relay Server – Windows-compatible.
Version 4.4.4

Designed and built by Christopher McGrath

No Unix signals, no fork.  Uses only threading + aiosmtpd.
"""

import email
import ipaddress
import json
import logging
import os
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass
from email.utils import parseaddr
import datetime as dt_module

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword


class OutlookCompatController(Controller):
    """Controller whose AUTH LOGIN challenges are the canonical SASL form.

    aiosmtpd's default LOGIN challenges are base64("User Name\\x00") and
    base64("Password\\x00") — wrong wording plus a trailing NUL byte.
    Outlook's SASL LOGIN client only answers the canonical
    base64("Username:") / base64("Password:") challenges (RFC-Murchison
    draft-murchison-sasl-login); on the default ones it sends nothing
    after the 334 and the session hangs — the client shows "connection
    interrupted" while the log just stops at '<< challenge:'.
    """

    def factory(self):
        server = super().factory()
        server.AuthLoginUsernameChallenge = 'Username:'
        server.AuthLoginPasswordChallenge = 'Password:'
        return server

from crypto_utils import decrypt_secret
from models import (
    db, AllowedDomain, SmtpCredential, EmailLog, EmailQueue, RelayConfig, DkimKey
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
                logger.warning(
                    "DKIM: no active key for sender domain %s, "
                    "message sent unsigned", sender_domain
                )
                return None

            try:
                # private_key may be an ENC1 envelope (v3.0.2+ stores keys
                # encrypted at rest); legacy plaintext PEM passes through.
                pem = dkim_key.get_private_key_pem(self.app.secret_key)
                if not pem:
                    logger.error(
                        "DKIM: could not decrypt private key for %s "
                        "(secret_key changed?) — message sent unsigned",
                        sender_domain,
                    )
                    return None
                import dkim
            except ImportError:
                logger.warning("dkimpy not available, skipping DKIM signing")
                return None

            try:
                sig = dkim.sign(
                    message=email_content,
                    selector=dkim_key.selector.encode('utf-8'),
                    domain=dkim_key.domain.encode('utf-8'),
                    privkey=pem.encode('utf-8'),
                    identity=(f'@{dkim_key.domain}'.encode()),
                    # relaxed/relaxed: whitespace-normalized headers AND
                    # body survive benign transport munging (line-ending
                    # normalization, trailing-blank-line changes).  The
                    # dkimpy default is relaxed/simple, whose body canon
                    # breaks validation whenever any hop touches bytes.
                    canonicalize=(b'relaxed', b'relaxed'),
                )
                if not sig.endswith(b'\r\n'):
                    sig += b'\r\n'
                # sig is a DKIM-Signature *header* block; it must sit in the
                # header section (before the first blank line), so prepend it.
                signed_email = sig + email_content
                logger.debug(
                    "DKIM signed email for %s (selector: %s)",
                    dkim_key.domain, dkim_key.selector
                )
                return signed_email
            except Exception as exc:
                logger.error("DKIM signing failed for %s: %s", sender_domain, exc)
                return None


def _resolve_mx(domain: str) -> list[tuple[str, int]] | None:
    """Resolve mail exchanger candidates for a domain.

    Returns:
        Ordered list of (hostname, port) candidates, highest preference
        first (RFC 5321 §5.1), or None when the domain can never accept
        mail (RFC 7505 NULL MX, or the domain does not exist) — those
        failures are permanent and must not be retried.

    A *transient* DNS failure (resolver timeout / no nameservers
    reachable) yields ``[(domain, 25)]`` so the delivery attempt still
    happens and the normal retry/backoff path applies.  A missing MX but
    present A record yields the A target (RFC 5321 implicit MX).
    """
    try:
        import dns.resolver
        import dns.exception
    except ImportError:
        logger.warning("dnspython not available, falling back to direct delivery")
        return [(domain, 25)]

    # Transient resolver trouble: don't classify the domain as dead, just
    # fall through to an attempt against the domain itself.
    _TRANSIENT_DNS = (dns.exception.Timeout,
                      getattr(dns.resolver, 'LifetimeTimeout', dns.exception.Timeout),
                      dns.resolver.NoNameservers)

    try:
        answers = dns.resolver.resolve(domain, 'MX')
    except _TRANSIENT_DNS as exc:
        logger.warning("MX lookup for %s failed transiently (%s); "
                       "will try the domain itself", domain, exc)
        return [(domain, 25)]
    except dns.exception.DNSException:
        # NXDOMAIN / NoAnswer — no MX commitment; try the implicit A record.
        try:
            a_answers = dns.resolver.resolve(domain, 'A')
        except _TRANSIENT_DNS as exc:
            logger.warning("A lookup for %s failed transiently (%s); "
                           "will try the domain itself", domain, exc)
            return [(domain, 25)]
        except dns.exception.DNSException:
            logger.warning("Domain %s does not exist (no MX, no A) — "
                           "not deliverable", domain)
            return None
        return [(str(a_answers[0]), 25)]

    if answers:
        mx_records = sorted(
            [(r.preference, str(r.exchange).rstrip('.')) for r in answers],
            key=lambda x: x[0]
        )
        # RFC 7505 NULL MX (exchange '.') — the domain accepts no mail.
        if mx_records[0][1] == '':
            logger.info("Domain %s has NULL MX (RFC 7505) — not deliverable", domain)
            return None
        # Deduplicate hosts (some zones repeat an exchange at several
        # preferences) while keeping preference order, and cap the list so
        # a pathological zone cannot make one message open dozens of
        # connections.
        candidates: list[tuple[str, int]] = []
        for _pref, host in mx_records:
            if host and (host, 25) not in candidates:
                candidates.append((host, 25))
            if len(candidates) >= 5:
                break
        if candidates:
            return candidates

    logger.warning("Domain %s published an empty MX set, trying A record", domain)
    try:
        a_answers = dns.resolver.resolve(domain, 'A')
        if a_answers:
            return [(str(a_answers[0]), 25)]
    except _TRANSIENT_DNS as exc:
        logger.warning("A lookup for %s failed transiently (%s); "
                       "will try the domain itself", domain, exc)
        return [(domain, 25)]
    except dns.exception.DNSException:
        pass

    logger.warning("No usable MX or A record for %s, using domain as mx host", domain)
    return [(domain, 25)]


class _PermanentDeliveryError(Exception):
    """Delivery can never succeed (NULL MX, hard 5xx, auth rejection).

    Phase 3 treats these as final 'failed' without consuming retry slots.
    """


class _PartialDeliveryError(Exception):
    """The upstream refused SOME recipients at RCPT (others accepted).

    Carries the ``refused`` dict from smtplib's sendmail() so phase 3 can
    requeue only the undelivered recipients instead of the whole message.
    """

    def __init__(self, refused: dict):
        super().__init__(f'{len(refused)} recipient(s) refused: {refused}')
        self.refused = refused


def _is_permanent_smtp_error(exc: BaseException) -> bool:
    """True when retrying the same message can never succeed.

    Covers hard 5xx responses (SMTPResponseException and its subclasses —
    SMTPPermanentError, SMTPSenderRefused, SMTPDataError …), rejected
    authentication, all-refused recipients, and our own permanent markers.
    """
    if isinstance(exc, _PermanentDeliveryError):
        return True
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return True
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        try:
            return all(int(code) >= 500
                       for code, _msg in exc.recipients.values())
        except Exception:
            return False
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code >= 500
    return False


def _is_transient_delivery_error(exc: BaseException) -> bool:
    """True when a DIFFERENT mail exchanger might still accept the message.

    Connection-level failures (refused, timed out, dropped) and 4xx
    responses say "this host, right now" — the next-lower-priority MX is
    worth trying (RFC 5321 §5.1).  Authoritative 5xx verdicts and our
    permanent markers are not worth a backup MX: the primary's rejection
    of the address/sender is the domain's answer.
    """
    if _is_permanent_smtp_error(exc):
        return False
    if isinstance(exc, (OSError, smtplib.SMTPServerDisconnected,
                        smtplib.SMTPConnectError)):
        # socket.timeout/ConnectionRefusedError/gaierror are OSErrors.
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code < 500
    return False


# ── bounded delivery dispatch ─────────────────────────────────────
# Cap on concurrently RUNNING delivery threads.  Both dispatch paths
# (immediate-at-DATA and the QueueProcessor tick) funnel through
# RelayHandler._spawn_delivery; when every slot is busy the message
# simply stays 'queued' and the 30-second tick picks it up.  A burst
# can therefore no longer herd an unbounded number of 30-second-timeout
# sockets against the upstream at once.
_DELIVERY_SLOTS = threading.BoundedSemaphore(10)


# ── IP Allowlist Helper Functions ────────────────────────────────
def _parse_allowed_networks(allowed_csv: str) -> list:
    """Parse comma-separated list of IPs/CIDR networks.

    Supports:
    - Single IPs: 10.55.61.5
    - CIDR notation: 10.55.61.0/24
    - IPv6 addresses and networks: 2001:db8::/32, ::1

    Returns list of ipaddress.IPv4Network / IPv6Network objects.
    """
    if not allowed_csv:
        return []

    networks = []
    for raw_item in allowed_csv.split(','):
        item = raw_item.strip()
        if not item:
            continue
        try:
            # ip_network handles bare IPs (as /32 or /128) and CIDR forms,
            # for both address families.
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError as e:
            logger.warning("Invalid IP/CIDR in allowlist: %s - %s", item, e)
    return networks


def _is_ip_allowed(peer_ip: str, allowed_csv: str) -> bool:
    """Check if peer IP is in any of the allowed networks.

    Args:
        peer_ip: The IP address of the connecting client.
        allowed_csv: Comma-separated list of IPs or CIDR networks.

    Returns:
        True if the IP is allowed, False otherwise.  Fails CLOSED when the
        allowlist is non-empty but none of its entries parse — a typo must
        never silently disable the allowlist.
    """
    networks = _parse_allowed_networks(allowed_csv)
    if not networks:
        return not (allowed_csv or '').strip()

    try:
        ip = ipaddress.ip_address(peer_ip)
    except ValueError:
        logger.warning("Invalid peer IP: %s", peer_ip)
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        # IPv4-mapped peers (::ffff:10.0.0.5) compare against v4 rules.
        ip = ip.ipv4_mapped
    return any(ip in network for network in networks)


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
                    # aiosmtpd 1.4.x overwrites session.auth_data with
                    # auth_result.auth_data, so the dict must ride on the
                    # result or every subsequent MAIL FROM gets a 530.
                    return AuthResult(success=True, auth_data=session.auth_data)

                logger.warning("SMTP AUTH FAIL: %s", username)
                return AuthResult(success=False, handled=False)
            except Exception as exc:
                logger.error("Auth error: %s", exc)
                # Infrastructure failure (DB down, timeout) — do NOT report
                # a permanent 535: clients would cache/disable the credential
                # for the wrong reason. Offer a temporary failure instead.
                # handled must stay False: aiosmtpd only pushes auth_result
                # itself when handled is False — with handled=True it drops
                # the message on the floor and the client hangs forever
                # waiting for a reply to AUTH that never comes.
                return AuthResult(
                    success=False, handled=False,
                    message='451 4.3.0 Temporary server error, try again later')


# ── Delivery snapshot (phase 1 → phase 2 boundary) ────────────
@dataclass
class _DeliverySnapshot:
    """Plain-value snapshot produced by phase 1 of ``_deliver()``.

    Everything the SMTP (network) phase needs is copied out of the ORM
    objects here, so the database session can be released before any
    socket I/O.  Only plain Python values cross the phase boundary —
    never ORM instances.
    """
    queue_id: int
    sender: str
    recipients: list
    message: bytes                  # exact bytes to send (DKIM-signed when signing succeeded)
    signed_message: bytes | None    # DKIM-signed bytes, or None when sent unsigned
    log_id: int | None
    delivery_mode: str
    host: str
    port: int
    use_tls: bool
    use_starttls: bool
    auth_user: str
    auth_pass: str | None
    direct_user: str | None
    direct_pass: str | None
    helo_name: str
    retry_interval: int
    retry_max: int


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
            # Auth check
            require_auth = RelayConfig.get_bool('require_auth', True)
            if require_auth and not getattr(session, 'auth_data', None):
                return '530 5.7.0 Authentication required'

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

            # Global rate limit — count EVERY message accepted in the past
            # hour, not just the ones that succeeded.  During an upstream
            # outage (messages failing) is exactly when throughput must be
            # clamped; counting only sent/queued would let a client hammer
            # a dead relay at full speed.
            limit = RelayConfig.get_int('global_rate_limit', 1000)
            since = dt_module.datetime.utcnow() - dt_module.timedelta(hours=1)
            recent = EmailLog.query.filter(
                EmailLog.timestamp >= since,
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
            # Basic recipient syntax gate: a RCPT without an '@domain' can
            # never be delivered (direct mode raises on it, relay mode
            # sends a doomed envelope) — reject it here with a proper 553
            # instead of accepting it and burning three retries later.
            _, rcpt_addr = parseaddr(address)
            if not rcpt_addr or '@' not in rcpt_addr \
                    or not rcpt_addr.split('@')[-1]:
                return '553 5.1.3 Bad recipient address syntax'
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
                # Truncate to the EmailLog column lengths: an overlong
                # header must not turn every delivery attempt of this
                # message into a 451-retry loop.
                subject = (str(msg.get('Subject', '') or '')[:500]
                           or '(no subject)')
                message_id = str(msg.get('Message-ID', '') or '')[:255]

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
                    sender=(envelope.mail_from or '')[:255],
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

                # Fire-and-forget delivery — bounded.  Thread.start() must
                # NOT be allowed to turn into a 451 here: the queue row is
                # already committed (a durable promise to deliver), and a
                # 451 would make a compliant client resend the message —
                # then the tick would deliver BOTH copies.  If the spawn
                # fails (or all slots are busy) the committed row stays
                # 'queued' and the 30 s tick delivers it instead.
                try:
                    self._spawn_delivery(q.id)
                except Exception as spawn_exc:
                    logger.error(
                        "Could not spawn delivery thread for queue #%s (%s); "
                        "the queue tick will deliver it", q.id, spawn_exc)

                return '250 Message accepted for delivery'
            except Exception as exc:
                logger.error("DATA error: %s", exc, exc_info=True)
                db.session.rollback()
                return '451 4.3.0 Internal server error'

    # ── delivery worker (runs in a thread) ─────────────────────
    def _spawn_delivery(self, queue_id) -> bool:
        """Start a delivery thread for *queue_id* if a slot is free.

        Returns True when the thread was started, False when all
        _DELIVERY_SLOTS are busy — the caller should leave the row
        'queued' and let the queue tick retry it later.
        """
        def _run():
            try:
                self._deliver(queue_id)
            finally:
                _DELIVERY_SLOTS.release()

        if not _DELIVERY_SLOTS.acquire(blocking=False):
            return False
        try:
            threading.Thread(target=_run, daemon=True).start()
        except Exception:
            _DELIVERY_SLOTS.release()
            raise
        return True

    def _deliver(self, queue_id):
        """Deliver a queued email to the relay destination.

        This method runs in a separate thread and is split into three
        phases so that no database session is ever held across network
        I/O (the original cause of the v2.2.0 "page hang" incident):

          Phase 1 (read)    — one short app context: load the queue row,
                              mark it 'processing', and snapshot every
                              plain value the SMTP phase needs (including
                              decrypted upstream credentials and the
                              DKIM-signed message bytes).  The DB session
                              is removed before any socket work.
          Phase 2 (network) — pure smtplib work with no app context and
                              no database session.
          Phase 3 (write)   — a fresh app context reloads the queue row
                              and records 'sent' or schedules the retry.

        Args:
            queue_id: The ID of the EmailQueue entry to deliver.
        """
        # ── Phase 1: read state and snapshot plain values ──
        try:
            snap = self._deliver_load(queue_id)
        except Exception as exc:
            # Config/decryption/read failures behave like delivery
            # failures: log them and schedule the retry.  Retry settings
            # could not be snapshotted, so fall back to the defaults.
            logger.warning(
                "Could not snapshot retry policy for queue #%s (%s); "
                "using defaults interval=300s max=3", queue_id, exc)
            self._deliver_record_failure(
                queue_id, exc,
                log_id=None, signed_message=None,
                retry_interval=300, retry_max=3,
            )
            return
        if snap is None:
            return

        # ── Phase 2: SMTP exchange — no app context, no DB session ──
        start_time = time.time()
        error = self._deliver_smtp(snap)

        # ── Phase 3: record the outcome in a fresh app context ──
        self._deliver_finalize(snap, error, start_time)

    def _deliver_load(self, queue_id):
        """Phase 1 of ``_deliver``: read state and snapshot plain values.

        Opens a single short-lived app context, marks the queue entry
        'processing', and copies everything the SMTP phase needs out of
        the ORM objects.  The database session is removed before
        returning, so no session is held across the network phase.

        Returns:
            A _DeliverySnapshot, or None when the queue entry is missing
            or no longer in 'queued' state.
        """
        with self.app.app_context():
            # Atomically claim the row: UPDATE ... WHERE status='queued'
            # matches exactly once, so the QueueProcessor tick and a
            # DATA-time delivery thread can never both deliver the same
            # message (the old check-then-set had a double-delivery race).
            claimed = (
                EmailQueue.query
                .filter(EmailQueue.id == queue_id,
                        EmailQueue.status == 'queued')
                .update({'status': 'processing'},
                        synchronize_session=False)
            )
            db.session.commit()
            if not claimed:
                return None
            q = EmailQueue.query.get(queue_id)
            if not q:
                return None

            delivery_mode = RelayConfig.get('delivery_mode', 'relay')
            host = RelayConfig.get('relay_host', 'localhost')
            port = RelayConfig.get_int('relay_port', 25)
            use_tls = RelayConfig.get_bool('relay_use_tls', False)
            use_starttls = RelayConfig.get_bool('relay_use_starttls', False)
            if delivery_mode == 'direct':
                # Direct delivery resolves its own MX targets and has a
                # dedicated STARTTLS setting; implicit TLS (SMTP_SSL) is
                # never used in direct mode.  MX resolution happens in
                # phase 2, PER RECIPIENT DOMAIN — DNS can be slow and must
                # not run while this DB session is open (3-phase rule), and
                # a mixed-domain message must go to each domain's own MX.
                use_tls = False
                use_starttls = RelayConfig.get_bool(
                    'direct_use_starttls', False)
                host = ''
                port = 25
            else:
                host = RelayConfig.get('relay_host', 'localhost')
                port = RelayConfig.get_int('relay_port', 25)
            auth_user = RelayConfig.get('relay_auth_user', '').strip()
            auth_pass = decrypt_secret(
                RelayConfig.get('relay_auth_password', '') or '',
                self.app.secret_key,
            )
            if auth_user and not auth_pass:
                logger.warning(
                    "No usable upstream password configured for relay "
                    "auth user %s; skipping AUTH", auth_user)
            helo_name = RelayConfig.get('relay_helo_hostname', 'localhost')

            # Direct-mode credentials are decrypted here, while the app
            # context and secret key are available (only in direct mode,
            # matching the previous behaviour of reading them at AUTH
            # time in direct deliveries only).
            direct_user = None
            direct_pass = None
            if delivery_mode == 'direct':
                direct_user = RelayConfig.get('direct_auth_user', '').strip()
                direct_pass = decrypt_secret(
                    RelayConfig.get('direct_auth_password', '') or '',
                    self.app.secret_key,
                )

            recipients = json.loads(q.recipients)

            # Log message details for tracking
            log_entry = EmailLog.query.get(q.log_id) if q.log_id else None
            message_id = log_entry.message_id if log_entry else None
            subject = log_entry.subject if log_entry else '(unknown)'
            debug_log_message_details(
                message_id, subject, q.sender, recipients,
                queue_id=queue_id
            )

            if delivery_mode == 'direct':
                target_desc = 'recipient-domain MX (resolved in phase 2)'
            else:
                target_desc = f'{host}:{port}'
            logger.info(
                "Delivering queue #%s: %s -> %s via %s "
                "(tls=%s starttls=%s auth_user=%s helo=%s)",
                queue_id, q.sender, recipients, target_desc,
                use_tls, use_starttls,
                auth_user if auth_user else '(none)', helo_name,
            )

            # DKIM signing (needs the app context for the DkimKey lookup)
            sender_domain = q.sender.split('@')[1].lower() if q.sender and '@' in q.sender else None
            signed_message = None
            if sender_domain:
                signer = DkimSigner(self.app)
                signed_content = signer.sign_email(q.raw_message, sender_domain)
                if signed_content:
                    signed_message = signed_content
                    debug_log(f"DKIM signed message for queue #{queue_id}")

            # Retry policy snapshot (phase 3 uses these plain values)
            retry_interval = RelayConfig.get_int('queue_retry_interval', 300)
            retry_max = RelayConfig.get_int('queue_max_retries', 3)

            snapshot = _DeliverySnapshot(
                queue_id=queue_id,
                sender=q.sender,
                recipients=list(recipients),
                message=(signed_message if signed_message is not None
                         else q.raw_message),
                signed_message=signed_message,
                log_id=q.log_id,
                delivery_mode=delivery_mode,
                host=host,
                port=port,
                use_tls=use_tls,
                use_starttls=use_starttls,
                auth_user=auth_user,
                auth_pass=auth_pass,
                direct_user=direct_user,
                direct_pass=direct_pass,
                helo_name=helo_name,
                retry_interval=retry_interval,
                retry_max=retry_max,
            )

            # Release this thread's DB session NOW — before any socket
            # work in phase 2 — instead of relying on the app-context
            # teardown.  Only plain values survive on the snapshot.
            db.session.remove()
            return snapshot

    def _deliver_smtp(self, snap):
        """Phase 2 of ``_deliver``: the SMTP exchange.

        Pure smtplib work driven only by the plain values carried on the
        snapshot — no Flask app context and no database session exist
        here.  Any exception is captured and returned so phase 3 can
        record the outcome.

        Direct mode resolves one MX target PER recipient domain and sends
        to each in turn (before v3.0.2 every recipient was sent to
        whichever single domain sorted first, so mixed-domain direct
        delivery did not work).

        Returns:
            None on success, otherwise the exception that failed the
            delivery.
        """
        if snap.delivery_mode == 'direct':
            return self._deliver_direct(snap)
        return self._smtp_send(snap, snap.host, snap.port,
                               snap.recipients, snap.queue_id)

    def _deliver_direct(self, snap):
        """Direct-mode phase 2: resolve MX per recipient domain, then send.

        All targets are resolved BEFORE anything is sent, so a resolution
        failure fails the attempt cleanly with nothing delivered (the
        message retries as usual).  A send failure on a later target after
        an earlier target already accepted its copy surfaces as an error
        and the whole message retries — the same all-or-nothing semantics
        the relay path has always had.
        """
        groups = {}   # domain -> [recipients]
        for rcpt in snap.recipients:
            domain = rcpt.split('@')[1].lower() if '@' in rcpt else ''
            groups.setdefault(domain, []).append(rcpt)

        targets = []  # ([hosts], recipients)
        for domain, rcpts in groups.items():
            if not domain:
                error = _PermanentDeliveryError(
                    f'recipient without domain: {rcpts[0]!r}')
                logger.error("Direct delivery: %s", error)
                return error
            hosts = _resolve_mx(domain)
            if hosts is None:
                # RFC 7505 NULL MX or nonexistent domain — retrying can
                # never succeed. Fail the attempt permanently.
                logger.error("Direct delivery: %s has no mail exchanger; "
                             "not retryable", domain)
                return _PermanentDeliveryError(
                    f'domain {domain} has no mail exchanger '
                    f'(NULL MX or does not exist)')
            targets.append((hosts, rcpts))

        # Surface the candidate target hosts in phase 3's EmailLog line.
        snap.host = ', '.join(sorted(
            {host for hosts, _ in targets for host, _port in hosts}))

        for hosts, rcpts in targets:
            last_error: BaseException | None = None
            for host, port in hosts:
                logger.info("Direct delivery for %s via %s:%s",
                            ', '.join(rcpts), host, port)
                error = self._smtp_send(snap, host, port, rcpts, snap.queue_id)
                if error is None:
                    break
                last_error = error
                if not _is_transient_delivery_error(error):
                    # Authoritative rejection — a backup MX would just
                    # relay the same verdict (or defer it in its queue).
                    break
                logger.warning(
                    "Direct delivery via %s failed transiently (%s); "
                    "trying next MX for this domain", host, error)
            if last_error is not None:
                # Every candidate failed (or the last failure was
                # authoritative) — hand the final error to phase 3.
                return last_error
        return None

    def _smtp_send(self, snap, host, port, recipients, queue_id):
        """One SMTP exchange to host:port for the given recipients.

        Driven only by snapshot values — no app context, no DB session.
        Returns None on success, otherwise the exception that failed the
        delivery.
        """
        conn = None
        try:
            if snap.use_tls:
                ctx = ssl.create_default_context()
                debug_log_connection(
                    "Connecting via SMTP_SSL",
                    host, port
                )
                conn = smtplib.SMTP_SSL(
                    host, port,
                    local_hostname=snap.helo_name, context=ctx, timeout=30
                )
            else:
                debug_log_connection(
                    "Connecting via SMTP",
                    host, port
                )
                conn = smtplib.SMTP(
                    host, port,
                    local_hostname=snap.helo_name, timeout=30
                )

            # Ensure smtplib knows the remote hostname (Python 3.14+
            # requires this for STARTTLS server-name verification).
            if not getattr(conn, '_host', ''):
                conn._host = host

            conn.ehlo(snap.helo_name)
            debug_log_smtp_command(f"EHLO {snap.helo_name} [queue_id={queue_id}]")

            # Optionally upgrade to STARTTLS
            if snap.use_starttls and not snap.use_tls:
                try:
                    ctx = ssl.create_default_context()
                    debug_log_starttls(f"Upgrading to STARTTLS [queue_id={queue_id}]")
                    conn.starttls(context=ctx)
                    conn.ehlo(snap.helo_name)
                    debug_log_smtp_command(f"EHLO after STARTTLS [queue_id={queue_id}]")
                except smtplib.SMTPNotSupportedError:
                    auth_requested = bool(
                        (snap.direct_user and snap.direct_pass)
                        if snap.delivery_mode == 'direct'
                        else (snap.auth_user and snap.auth_pass))
                    if auth_requested:
                        # Refuse to send credentials over a plaintext
                        # session — that would leak them to anyone on the
                        # path. Permanent failure: fix the target host or
                        # explicitly disable use_starttls.
                        logger.error(
                            "STARTTLS required for AUTH but %s does not "
                            "support it — refusing to authenticate in "
                            "cleartext [queue_id=%s]", host, queue_id)
                        return smtplib.SMTPAuthenticationError(
                            538, b'538 5.7.11 Encryption required for '
                                 b'requested authentication mechanism')
                    logger.warning(
                        "STARTTLS not supported by %s — continuing "
                        "UNENCRYPTED (no AUTH credentials in use)",
                        host)
                    debug_log(f"STARTTLS not supported by server [queue_id={queue_id}]")

            # Optionally authenticate (skip if credentials are empty)
            if snap.delivery_mode == 'direct':
                if snap.direct_user and not snap.direct_pass:
                    logger.warning(
                        "No usable upstream password configured for "
                        "direct auth user %s; skipping AUTH", snap.direct_user)
                if snap.direct_user and snap.direct_pass:
                    try:
                        conn.login(snap.direct_user, snap.direct_pass)
                        debug_log_smtp_command(f"AUTH LOGIN (direct) {snap.direct_user} [queue_id={queue_id}]")
                    except smtplib.SMTPException as auth_exc:
                        logger.warning("Direct delivery auth failed: %s", auth_exc)
                        raise
            elif snap.auth_user and snap.auth_pass:
                try:
                    conn.login(snap.auth_user, snap.auth_pass)
                    debug_log_smtp_command(f"AUTH LOGIN {snap.auth_user} [queue_id={queue_id}]")
                except smtplib.SMTPException as auth_exc:
                    logger.warning("Authentication failed: %s", auth_exc)
                    debug_log_exception(auth_exc, f"SMTP authentication [queue_id={queue_id}]")
                    raise

            # Send the email.  sendmail() does NOT raise when the server
            # refuses only SOME recipients at RCPT — it returns a dict of
            # the refused ones (the accepted recipients already received
            # the message). Surface that as a partial failure so phase 3
            # requeues only the undelivered recipients.
            # Declare BODY=8BITMIME only when the message actually carries
            # non-ASCII bytes AND the target advertised the feature: a
            # strict receiver that dropped 8-bit bodies on an undeclared
            # message would otherwise 5xx the DATA and burn all retries.
            mail_options = []
            if (not snap.message.isascii()
                    and '8bitmime' in conn.esmtp_features):
                mail_options.append('BODY=8BITMIME')
            senderrs = conn.sendmail(snap.sender, recipients, snap.message,
                                     mail_options=mail_options)
            if senderrs:
                logger.warning(
                    "Partial delivery for queue #%s via %s: %d of %d "
                    "recipients refused: %s",
                    queue_id, host, len(senderrs), len(recipients), senderrs)
                raise _PartialDeliveryError(senderrs)
            debug_log_smtp_command(f"SENDMAIL to {len(recipients)} recipients [queue_id={queue_id}]")
            return None
        except Exception as exc:
            return exc
        finally:
            if conn is not None:
                try:
                    conn.quit()
                    debug_log_smtp_command(f"QUIT [queue_id={queue_id}]")
                except Exception:
                    # Session already broken — release the socket instead
                    # of leaving it to the garbage collector.
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _deliver_finalize(self, snap, error, start_time):
        """Phase 3 of ``_deliver``: record the outcome in a fresh app context.

        The queue row is reloaded fresh by id — it may have been deleted
        or requeued via the web UI while the SMTP phase was running; a
        vanished row is logged and skipped gracefully.
        """
        if error is not None:
            self._deliver_record_failure(
                snap.queue_id, error,
                log_id=snap.log_id,
                signed_message=snap.signed_message,
                retry_interval=snap.retry_interval,
                retry_max=snap.retry_max,
            )
            return

        with self.app.app_context():
            try:
                # Guarded finalize: only a row we still hold as 'processing'
                # may be flipped to 'sent'. If an admin requeued it (or it
                # was deleted) while the SMTP phase ran, our result is
                # stale — writing it anyway would clobber the newer state
                # and can double-deliver.
                claimed = (
                    EmailQueue.query
                    .filter(EmailQueue.id == snap.queue_id,
                            EmailQueue.status == 'processing')
                    .update({'status': 'sent'}, synchronize_session=False)
                )
                if not claimed:
                    logger.warning(
                        "Queue #%s no longer 'processing' when the delivery "
                        "result arrived (requeued or deleted meanwhile); "
                        "skipping status update", snap.queue_id)
                    db.session.rollback()
                    return
                retry_count = db.session.query(
                    EmailQueue.retry_count).filter(
                    EmailQueue.id == snap.queue_id).scalar() or 0
                log_entry = (EmailLog.query.get(snap.log_id)
                             if snap.log_id else None)
                if log_entry:
                    log_entry.status = 'sent'
                    log_entry.status_message = f'Delivered via {snap.host}:{snap.port}'
                    log_entry.retry_count = retry_count
                db.session.commit()
            except Exception as exc:
                logger.error("Queue status update error: %s", exc)
                debug_log_exception(
                    exc, f"Queue status update [queue_id={snap.queue_id}]")
                db.session.rollback()
                return
            logger.info("Delivered queue #%s via %s:%s",
                        snap.queue_id, snap.host, snap.port)
            debug_log_timing(
                start_time,
                f"Delivery completed [host={snap.host} port={snap.port}]")

    def _deliver_record_failure(self, queue_id, exc, log_id,
                                signed_message, retry_interval, retry_max):
        """Record a failed delivery attempt and schedule the retry.

        Mirrors the historical single-session failure handling: linear
        backoff (``interval * retry_count``), status 'failed' once
        ``retry_count`` reaches the configured maximum, and the EmailLog
        updated on the final failure.  Runs in its own fresh app
        context; a queue row deleted in the meantime is logged and
        skipped.
        """
        logger.error("Delivery failed #%s: %s", queue_id, exc,
                     exc_info=True)
        debug_log_exception(exc, f"Delivery failed [queue_id={queue_id}]")
        with self.app.app_context():
            try:
                # Atomic claim: only a row still held as 'processing' gets
                # its retry bookkeeping. If an admin requeued (or deleted)
                # the row while this attempt was in flight, our result is
                # stale — skip instead of clobbering the newer state.
                claimed = (
                    EmailQueue.query
                    .filter(EmailQueue.id == queue_id,
                            EmailQueue.status == 'processing')
                    .update({'retry_count': EmailQueue.retry_count + 1,
                             'last_error': str(exc)[:500]},
                            synchronize_session=False)
                )
                db.session.commit()
                if not claimed:
                    logger.warning(
                        "Queue #%s no longer 'processing' when the failure "
                        "was recorded (requeued or deleted meanwhile); "
                        "skipping retry update", queue_id)
                    return
                retry_count = db.session.query(
                    EmailQueue.retry_count).filter(
                    EmailQueue.id == queue_id).scalar() or 0
                permanent = _is_permanent_smtp_error(exc)

                # Partial RCPT refusal: only the refused recipients are
                # still undelivered — narrow the row to them so the retry
                # does not re-send to recipients that already accepted.
                refused = getattr(exc, 'refused', None)
                if refused:
                    try:
                        remaining = json.loads(
                            db.session.query(EmailQueue.recipients)
                            .filter(EmailQueue.id == queue_id).scalar()
                            or '[]')
                        kept = [r for r in remaining if r in refused]
                        if kept and len(kept) < len(remaining):
                            EmailQueue.query.filter(
                                EmailQueue.id == queue_id).update(
                                {'recipients': json.dumps(kept)},
                                synchronize_session=False)
                            logger.info(
                                "Queue #%s: retrying only the %d refused "
                                "recipient(s); %d already delivered",
                                queue_id, len(kept),
                                len(remaining) - len(kept))
                    except Exception as narrow_exc:
                        logger.warning(
                            "Queue #%s: could not narrow recipients for "
                            "retry: %s", queue_id, narrow_exc)

                if permanent or retry_count >= retry_max:
                    if permanent:
                        reason = f'Permanently rejected by upstream: {exc}'
                    else:
                        reason = f'Failed after {retry_max} attempts: {exc}'
                    EmailQueue.query.filter(
                        EmailQueue.id == queue_id).update(
                        {'status': 'failed'}, synchronize_session=False)
                    log_entry = (EmailLog.query.get(log_id)
                                 if log_id else None)
                    if log_entry:
                        log_entry.status = 'failed'
                        log_entry.status_message = reason[:500]
                        log_entry.retry_count = retry_count
                else:
                    EmailQueue.query.filter(
                        EmailQueue.id == queue_id).update(
                        {'status': 'queued',
                         'next_retry_at': (
                             dt_module.datetime.utcnow()
                             + dt_module.timedelta(
                                 seconds=retry_interval * retry_count))},
                        synchronize_session=False)
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
        # PID of the process that started the listener. Under gunicorn the
        # web worker is a FORK of the process that owns the SMTP listener —
        # threads do not survive fork, so the worker's copy of this object
        # must never be started/stopped/probed as if it were the real one.
        self._owner_pid = None

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
            max_sz = RelayConfig.get_int('max_message_size', 26214400)
            enable_tls = RelayConfig.get_bool('enable_tls', False)

        handler = RelayHandler(self.app)
        # ALWAYS attach the authenticator, but NEVER let the framework
        # itself enforce auth (auth_required=False): aiosmtpd pushes its
        # own 530 BEFORE handle_MAIL runs whenever the constructor-time
        # flag is true, so an operator disabling require_auth via the web
        # UI would find clients still blocked until a listener restart.
        # handle_MAIL re-reads require_auth from live config on every
        # MAIL FROM, which makes BOTH directions take effect immediately.
        # (Without a successful MAIL FROM no RCPT/DATA is reachable, so
        # enforcement is still airtight.)
        authenticator = RelayAuthenticator(self.app)
        self._owner_pid = os.getpid()

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
            authenticator=authenticator,
            auth_required=False,
            auth_require_tls=False,
        )
        if tls_ctx:
            kw['tls_context'] = tls_ctx

        # Try to start; if 0.0.0.0 fails on Windows, fall back to 127.0.0.1
        try:
            self.controller = OutlookCompatController(**kw)
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
                self.controller = OutlookCompatController(**kw)
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

        In the process that started the listener this is a definitive
        thread probe.  In a forked gunicorn worker the thread copy is dead
        by construction, so liveness is inferred from the master process's
        heartbeat (refreshed every 30 s by the QueueProcessor).
        """
        if getattr(self, '_owner_pid', None) != os.getpid():
            # Forked web worker: our copy never owned the listener thread.
            return self._master_heartbeat_fresh()
        if not self._running or self.controller is None:
            return False
        # Try to verify the controller thread is still alive.
        # aiosmtpd uses _thread in some versions, thread in others.
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

    def _master_heartbeat_fresh(self):
        """True when the owning process's SMTP heartbeat is recent.

        The QueueProcessor's loop thread only exists in the process that
        owns the SMTP listener, so its 30 s heartbeat doubles as an SMTP
        liveness signal for forked web workers.
        """
        try:
            with self.app.app_context():
                raw = RelayConfig.get('_smtp_heartbeat', '')
            if not raw:
                return False
            beat = dt_module.datetime.fromisoformat(raw)
            age = (dt_module.datetime.utcnow() - beat).total_seconds()
            return 0 <= age < 90  # three missed ticks = down
        except Exception:
            return False

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
        self._stop_event = threading.Event()

    def start(self):
        """Start the queue processor background thread."""
        if self._running:
            return
        self._requeue_orphaned_processing()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Queue processor started")

    def _requeue_orphaned_processing(self):
        """Requeue rows stranded in 'processing' by an earlier shutdown.

        Delivery threads are daemons with no join: any deploy, crash, or
        SIGTERM mid-delivery left the in-flight rows 'processing'
        forever — the tick only picks up 'queued', the guarded finalize
        only writes rows still 'processing', and nothing else ever
        touched that state, so the message vanished from the pipeline
        (the client had already been told 250).  At THIS moment no
        delivery thread exists in this process, so reclaiming them is
        safe.  (In the pathological case of an old process still
        delivering the same row, its guarded finalize refuses the stale
        write — the worst case is one duplicate, the same exposure as a
        client resending after a connection drop.)
        """
        with self.app.app_context():
            try:
                n = EmailQueue.query.filter(
                    EmailQueue.status == 'processing',
                ).update(
                    {'status': 'queued',
                     'next_retry_at': dt_module.datetime.utcnow()},
                    synchronize_session=False)
                db.session.commit()
            except Exception as exc:
                logger.error("Could not requeue orphaned 'processing' "
                             "queue rows: %s", exc)
                db.session.rollback()
                return
        if n:
            logger.warning("Requeued %d message(s) stranded in 'processing' "
                           "by a previous shutdown", n)

    def stop(self):
        """Stop the queue processor background thread."""
        self._running = False
        self._stop_event.set()  # interrupt the inter-tick sleep promptly
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Queue processor stopped")

    def _loop(self):
        """Main loop that periodically checks for pending queue entries.

        Runs every 30 seconds, looking for queued messages that are
        due for retry, and applies log/queue retention.
        """
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error("Queue tick error: %s", exc)
            try:
                self.cleanup_old()
            except Exception as exc:
                logger.error("Retention cleanup error: %s", exc)
            # Event-based wait: stop() ends the wait immediately instead of
            # after up to a full 30 s sleep.
            self._stop_event.wait(30)

    def _tick(self):
        """Pick up queued messages and dispatch each one in its own thread.

        Previously _deliver() was called directly here, which meant a 30-second
        SMTP connect timeout would block the queue thread and hold a database
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
            # Liveness heartbeat for forked gunicorn web workers: this loop
            # thread only exists in the process that owns the SMTP
            # listener, so a fresh heartbeat means the master is relaying.
            try:
                RelayConfig.set('_smtp_heartbeat', now.isoformat())
            except Exception as exc:
                logger.warning("Could not write SMTP heartbeat: %s", exc)

        handler = RelayHandler(self.app)
        for queue_id in ids:
            if not handler._spawn_delivery(queue_id):
                # All delivery slots are busy — leave the remaining rows
                # 'queued' for the next tick instead of stacking threads.
                break

    def cleanup_old(self):
        """Remove old log and queue entries based on retention settings.

        Deletes sent queue entries and email logs older than the configured
        retention period. Failed queue entries are retained until manually
        handled by an administrator. Logs still referenced by a retained
        queue row are kept too (EmailQueue.log_id is a real FK — deleting
        them would violate the constraint on PostgreSQL and abort cleanup).
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
                ~EmailLog.id.in_(
                    db.session.query(EmailQueue.log_id)
                    .filter(EmailQueue.log_id.isnot(None))
                ),
            ).delete(synchronize_session=False)
            if dq or dl:
                db.session.commit()
                logger.info("Cleaned %s queue + %s log entries "
                            "(failed entries and their logs retained)", dq, dl)
