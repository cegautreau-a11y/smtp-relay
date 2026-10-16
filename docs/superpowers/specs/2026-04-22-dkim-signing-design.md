# DKIM Signing — Design Specification

**Date:** 2026-04-22
**Author:** Christopher McGrath
**Status:** Draft

---

## Overview

Add DKIM signing to emails sent through the SMTP relay. Emails are signed at delivery time (just before sending to MX) so that DKIM key/selector configuration changes take effect on the next message without needing to re-queue.

---

## Goals

1. Support multiple domains, each with its own DKIM key pair
2. Store keys in the SQLite database
3. Semi-automated DNS: UI shows the exact TXT record to add to DNS
4. Per-domain configurable selector
5. Manual key rotation only (on-demand via UI button)
6. Sign in both relay mode and direct delivery mode

---

## Design

### 1. Database Models

#### `DkimKey` Table

| Column | Type | Description |
|---|---|---|
| `id` | Integer (PK) | Auto-increment primary key |
| `domain` | String(255) | Domain name (e.g., `example.com`) |
| `selector` | String(63) | DKIM selector (e.g., `mail`, `2026`) |
| `private_key` | Text | PEM-encoded RSA private key |
| `public_key` | Text | PEM-encoded RSA public key |
| `is_active` | Boolean | Whether this key is currently used for signing |
| `created_at` | DateTime | When the key was generated |
| `created_by` | String(120) | User who created the key |

**Constraints:**
- Unique: (domain, selector) — no duplicate selector per domain
- is_active enforced at application level (only one active per domain)

---

### 2. DKIM Signing Service

#### `DkimSigner` Class (`smtp_server.py`)

Responsibilities:
- Sign email content using RFC 6376 (canonicalization: simple for headers, relaxed for body)
- Use `dkimpy` library for cryptographic operations
- Return signed message with `DKIM-Signature` header added

#### Signing Flow

1. Before `_deliver()` sends via SMTP, check if sender domain has active DKIM key
2. If DKIM key exists for sender domain:
   - Extract From: header domain from email
   - Sign the email (headers + body)
   - Add `DKIM-Signature` header
3. Deliver signed email to MX

#### DNS Record Format

For a domain `example.com` with selector `mail`:
```
mail._domainkey.example.com.  IN  TXT  "v=DKIM1; k=rsa; p=<public_key_base64>..."
```

The public key is stored in the DB and displayed in the UI in this exact format.

---

### 3. Configuration / Storage

- Keys stored in SQLite (`dkim_keys` table)
- Private keys are PEM-encoded text
- No external files needed (keys survive container restarts via volume mount)
- Volume mount: `- smtp-data:/app` (from docker-compose.yml) — database and keys persist

---

### 4. Dependencies

New dependency:
- `dkimpy>=1.0.0` — Pure Python DKIM signing and verification

Updated `requirements.txt`:
```
dkimpy>=1.0.0
```

---

### 5. API / UI

#### DKIM Management Page (`/dkim`)

Accessible to Operator+ roles. Shows:
- List of DKIM keys per domain
- For each key: domain, selector, status (active/inactive), created date
- "Generate New Key" button per domain
- "Activate" button to set a key as active
- DNS instructions + TXT record display for each active key

#### DNS Record Display

When a key is active, show:

```
Record Type: TXT
Name: mail._domainkey.example.com
Value: v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA...
```

With instructions to:
1. Log into your DNS provider
2. Add a new TXT record with the values above
3. Wait for DNS propagation (can take up to 24 hours)
4. Click "Verify" to confirm the record is published

---

### 6. File Structure

```
smtp-relay-beta/
├── smtp_server.py        # Add DkimSigner class, signing in _deliver
├── models.py             # Add DkimKey model
├── app.py                # Add /dkim routes
├── templates/
│   └── dkim.html         # DKIM management UI
├── requirements.txt      # Add dkimpy
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-22-dkim-signing-design.md
```

---

### 7. Implementation Approach

**Sign at delivery time (Approach B):**
- Email stored as-is in queue (original content)
- When `_deliver()` is ready to send, it checks for DKIM key matching sender domain
- If key exists: sign the email, deliver signed version
- If key doesn't exist: deliver original (no signing)

This means config changes (new key, new selector) take effect on the next message automatically.

---

### 8. Security Considerations

- Private keys stored in SQLite — database file should be protected
- Private keys never displayed in UI (only public key shown for DNS)
- No API to export private keys
- Container `smtp-data` volume should have restricted permissions

---

### 9. Out of Scope

- DKIM verification of incoming emails
- Automatic key rotation
- DNS API integration
- Multiple active keys per domain (only one active at a time)

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-22 | Sign at delivery time (Approach B) | Config changes take effect next message; simpler architecture |
| 2026-04-22 | Database storage for keys | Matches existing storage pattern; survives container restarts |
| 2026-04-22 | Semi-automated DNS display | User adds TXT record manually but UI provides exact values |
| 2026-04-22 | Configurable selector per domain | User requested flexibility beyond default |
| 2026-04-22 | Manual key rotation only | Simpler to implement; user can rotate on-demand |
| 2026-04-22 | Use dkimpy library | Pure Python; no C extension compilation needed |
