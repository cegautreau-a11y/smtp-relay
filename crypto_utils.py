"""Symmetric encryption helpers for secrets stored in the database.

The relay needs the upstream password *as plaintext* to authenticate against
the upstream server, so it cannot be stored as a bcrypt hash (one-way).
Values are instead encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using a
key derived from the app's SECRET_KEY. The ``ENC1:`` envelope prefix marks
encrypted values.

A stored secret whose own plaintext begins with the literal ``ENC1:`` prefix
would otherwise be mistaken for an envelope forever (undecryptable). Such
values are escaped under the ``ENC1LIT:`` marker and decrypt back verbatim
(see ``encrypt_secret``/``decrypt_secret``).

Note: rotating SECRET_KEY invalidates previously encrypted values; the
affected passwords must be re-entered in the web UI.
"""
import base64
import hashlib
import logging
import re

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENC_PREFIX = 'ENC1:'
# Escape marker for secrets whose plaintext itself starts with 'ENC1:'.
# Cannot collide with ENC_PREFIX: an ENC1: envelope always has ':' at
# index 4, this has 'L' — and Fernet tokens are base64url (never ':').
ENC_LITERAL_PREFIX = 'ENC1LIT:'

# Legacy markers written by v3.0.1 and earlier.
LEGACY_PLACEHOLDER = '[ENCRYPTED]'
# A REAL legacy bcrypt hash ($2a$/$2b$/$2y$ + cost + '$').  A bare
# startswith('$2') also caught legitimate passwords that merely begin
# with '$2' and permanently disabled them.
BCRYPT_SHAPE_RE = re.compile(r'^\$2[aby]\$\d{2}\$')

# Kept for backwards compatibility with older imports.
_LEGACY_BCRYPT_PREFIX = '$2'
_LEGACY_PLACEHOLDER = LEGACY_PLACEHOLDER


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    """Encrypt *plaintext* into an ``ENC1:...`` envelope string.

    Plaintext that itself begins with the ``ENC1:`` prefix is stored under
    the ``ENC1LIT:`` marker instead so it is never mistaken for an envelope.
    """
    if not plaintext:
        return ''
    token = _fernet(secret_key).encrypt(plaintext.encode('utf-8'))
    prefix = (ENC_LITERAL_PREFIX if plaintext.startswith(ENC_PREFIX)
              else ENC_PREFIX)
    return prefix + token.decode('ascii')


def decrypt_secret(value: str, secret_key: str):
    """Return the usable plaintext for *value*.

    - ``''``              -> ``''``
    - ``ENC1LIT:...``     -> decrypted plaintext, returned VERBATIM (even
      when it starts with ``ENC1:``)
    - ``ENC1:...``        -> decrypted plaintext (``None`` if the key changed
      or the value is corrupt)
    - ``$2...``           -> ``None`` (legacy bcrypt corruption, unrecoverable)
    - ``[ENCRYPTED]``     -> ``''`` (legacy placeholder written by old versions)
    - anything else       -> returned unchanged (legacy plaintext)
    """
    if not value:
        return ''
    probe = value.strip()
    if probe == _LEGACY_PLACEHOLDER:
        logger.warning(
            'Config contains legacy "[ENCRYPTED]" placeholder; treating as '
            'empty. Re-enter the password in the web UI.'
        )
        return ''
    if BCRYPT_SHAPE_RE.match(probe):
        logger.error(
            'Stored password is a legacy bcrypt hash (one-way) and cannot be '
            'used for upstream AUTH. Re-enter the password in the web UI.'
        )
        return None
    if probe.startswith(ENC_LITERAL_PREFIX):
        # Escaped literal secret: decrypt and return VERBATIM — the
        # plaintext may itself start with 'ENC1:' and must not be
        # treated as an envelope.
        try:
            token = probe[len(ENC_LITERAL_PREFIX):].encode('ascii')
            return _fernet(secret_key).decrypt(token).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeEncodeError):
            logger.error(
                'Stored password could not be decrypted (SECRET_KEY changed '
                'or value corrupt). Re-enter it in the web UI.'
            )
            return None
    if probe.startswith(ENC_PREFIX):
        try:
            token = probe[len(ENC_PREFIX):].encode('ascii')
            return _fernet(secret_key).decrypt(token).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeEncodeError):
            logger.error(
                'Stored password could not be decrypted (SECRET_KEY changed '
                'or value corrupt). Re-enter it in the web UI.'
            )
            return None
    # Legacy plaintext passes through UNSTRIPPED: leading/trailing
    # whitespace can be part of a real password (markers above were
    # matched against a stripped probe so robustness is preserved).
    return value


def looks_protected(value: str) -> bool:
    """True when *value* carries either encrypted-at-rest marker.

    Protection loops must skip BOTH markers: re-encrypting an
    ENC1LIT-escaped secret would double-envelope it and make the real
    plaintext unreachable.
    """
    if not value:
        return False
    return value.startswith(ENC_PREFIX) or value.startswith(ENC_LITERAL_PREFIX)
