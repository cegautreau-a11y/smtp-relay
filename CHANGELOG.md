# Changelog

All notable changes to the SMTP Mail Relay project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.0.0] — 2025-03-03

### Added
- **SMTP Relay Server** — Async inbound SMTP listener powered by aiosmtpd, accepts mail from applications and forwards to an upstream relay
- **Web Management Interface** — Full Flask-based dashboard for monitoring and configuration
- **Role-Based Access Control** — Four-tier user role system (Super Admin, Admin, Operator, Viewer) with granular permissions
- **SMTP Authentication** — Per-credential auth with bcrypt password hashing and individual rate limits
- **Sender Domain Allowlist** — Restrict which sender domains are permitted through the relay
- **Email Queue** — Automatic retry with configurable intervals and max attempts for failed deliveries
- **Email Logging** — Full audit trail of all messages processed with status tracking
- **Dashboard** — Real-time statistics, 7-day volume chart, recent email activity
- **Live Configuration** — Edit relay settings through the web UI; changes saved to both database and config.json
- **TLS/STARTTLS Support** — Optional TLS on the listener and STARTTLS for upstream relay connections
- **IP Allowlisting** — Restrict which source IPs can connect to the relay
- **Global Rate Limiting** — Server-wide hourly send limit
- **Auto-Dependency Install** — First-run automatic pip install of all requirements
- **Windows Compatibility** — Designed for Windows Server; no Unix signals or fork required
- **Python 3.14 Support** — Compatible with Python 3.11 through 3.14 (handles stricter SSL in 3.14)
- **Session Security** — Sessions expire on server restart and browser close
- **Database Migration** — Automatic schema migration when upgrading from older versions