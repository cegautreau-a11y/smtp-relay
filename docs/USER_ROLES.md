# User Roles & Permissions

**SMTP Mail Relay v4.5.1**

The SMTP Mail Relay uses a five-tier role-based access control (RBAC) system. Each user is assigned exactly one role that determines what they can see and do in the web interface.

---

## Role Hierarchy

Roles are ordered from least to most privileged:

| Role | Level | Description |
|---|---|---|
| **Viewer** | 0 | Read-only access to the dashboard, email logs, and queue |
| **Queue Manager** | 1 | Can manage the retry queue (retry/delete/flush) — nothing else |
| **Operator** | 2 | Can manage domains, SMTP credentials, DKIM keys, and queue entries |
| **Admin** | 3 | Full access to configuration, user management, and server controls |
| **Super Admin** | 4 | Can manage all users including other Admins. Highest privilege level |

A higher-level role inherits all permissions of the roles below it.

---

## Permission Matrix

| Feature | Viewer | Queue Manager | Operator | Admin | Super Admin |
|---|---|---|---|---|---|
| View dashboard & statistics | ✅ | ✅ | ✅ | ✅ | ✅ |
| View email logs | ✅ | ✅ | ✅ | ✅ | ✅ |
| View email log detail (headers, Message-ID) | ✅ | ✅ | ✅ | ✅ | ✅ |
| View email queue | ✅ | ✅ | ✅ | ✅ | ✅ |
| View allowed domains list | ❌ | ❌ | ✅ | ✅ | ✅ |
| View SMTP credentials list | ❌ | ❌ | ✅ | ✅ | ✅ |
| View DKIM keys | ❌ | ❌ | ✅ | ✅ | ✅ |
| Retry individual failed queue entry | ❌ | ✅ | ✅ | ✅ | ✅ |
| Delete individual queue entry | ❌ | ✅ | ✅ | ✅ | ✅ |
| Retry All Failed (bulk requeue) | ❌ | ✅ | ✅ | ✅ | ✅ |
| Delete All Failed (bulk delete) | ❌ | ✅ | ✅ | ✅ | ✅ |
| Add/edit/delete domains | ❌ | ❌ | ✅ | ✅ | ✅ |
| Add/edit/delete SMTP credentials | ❌ | ❌ | ✅ | ✅ | ✅ |
| Generate/activate/deactivate/delete DKIM keys | ❌ | ❌ | ✅ | ✅ | ✅ |
| Start/stop/restart SMTP server | ❌ | ❌ | ❌ | ✅ | ✅ |
| Edit relay configuration | ❌ | ❌ | ❌ | ✅ | ✅ |
| View user management page | ❌ | ❌ | ❌ | ✅ | ✅ |
| Create new users | ❌ | ❌ | ❌ | ✅ | ✅ |
| Manage Viewer, Queue Manager & Operator users | ❌ | ❌ | ❌ | ✅ | ✅ |
| Manage Admin users | ❌ | ❌ | ❌ | ❌ | ✅ |
| Manage Super Admin users | ❌ | ❌ | ❌ | ❌ | ✅ |
| Change own password & email | ✅ | ✅ | ✅ | ✅ | ✅ |

> **v3.0.1 Note:** Delete actions on queue entries now prompt for confirmation to prevent accidental deletions.

> **Sidebar menu is permission-filtered (v4.5.1):** every signed-in user sees **Dashboard**, **Email Logs**, and **Queue**. The **Allowed Domains / SMTP Credentials / DKIM Keys** section appears for Operator and above (the pages are also blocked below Operator, not just hidden). **Configuration / Users** remain Admin and above.

> **Sign-in methods:** accounts sign in either with **Microsoft 365** (`entra` — the default for new accounts; matched by Entra object ID or email, no password stored) or with a **local break-glass password** (`local` — restricted to Admin and Super Admin accounts; the initial `admin` account is always local). Admins can switch an account's method from the Users page. Password reset applies only to local accounts.
>
> **DKIM Note: DKIM keys are visible to Operator and above (the sidebar section and the page itself are Operator-gated since v4.5.1). Generating, activating, deactivating, and deleting DKIM keys requires the Operator role or higher.

---

## Role Descriptions

### Viewer
Read-only access. Viewers can monitor the system — watching the dashboard, browsing email logs, checking the queue, and viewing DKIM keys — but cannot make any changes. Suitable for monitoring staff or stakeholders who need visibility without the ability to modify anything.

### Queue Manager
All Viewer permissions plus full control of the retry queue: retrying individual failed messages, bulk-retrying all failed messages, and deleting failed entries (individually or in bulk, with confirmation). Queue Managers cannot touch anything else — no domains, no SMTP credentials, no DKIM keys, no configuration, no user management, and those menu sections are hidden for them. Suitable for helpdesk/operations staff whose only job is un-sticking stuck mail.

### Operator
All Queue Manager permissions plus the ability to manage the day-to-day operation of the relay. Operators can add and remove allowed domains, create and manage SMTP credentials, and generate and manage DKIM keys. Operators cannot change relay configuration, restart the SMTP server, or manage user accounts.

### Admin
All Operator permissions plus full control over the relay configuration, SMTP server lifecycle (start/stop/restart), and user management for Viewer, Queue Manager, and Operator accounts. Admins can create new users and assign them those roles. Admins cannot manage other Admin or Super Admin accounts.

### Super Admin
All Admin permissions plus the ability to manage Admin and Super Admin accounts. Super Admins have unrestricted access to every feature in the system. The initial `admin` account created on first run is a Super Admin.

---

## Role Assignment Rules

Roles can only be assigned by users with sufficient privileges. This prevents privilege escalation.

### Who Can Assign What

| Actor Role | Can Create/Assign |
|---|---|
| **Super Admin** | Viewer, Queue Manager, Operator, Admin, Super Admin |
| **Admin** | Viewer, Queue Manager, Operator |
| **Operator** | *(cannot manage users)* |
| **Queue Manager** | *(cannot manage users)* |
| **Viewer** | *(cannot manage users)* |

### User Management Rules

- **You cannot change your own role.** Another admin must do it.
- **You cannot disable or delete yourself.**
- **Admins cannot manage other Admins** — only Super Admins can.
- **Admins can manage Viewers, Queue Managers, and Operators** — enable, disable, delete, change role, reset password.
- **Super Admins can manage everyone** — including other Super Admins (except themselves).

---

## Default Account

On first run, the application creates an initial account:

| Field | Value |
|---|---|
| Username | `admin` |
| Password | Generated randomly on first boot and printed **once** to the console/log (`WARNING` line) |
| Role | **Super Admin** |

> **Important:** Save the generated password when it is printed — it is not shown again. Change it immediately after first login via the **Profile** page.

---

## Database Migration

When upgrading from a version without roles, the application automatically migrates existing accounts:

| Old Field | New Role |
|---|---|
| `is_admin = true` | **Super Admin** |
| `is_admin = false` | **Viewer** |

The migration runs automatically on startup. No manual database changes are required.

---

## Sidebar Navigation by Role

The sidebar menu adapts based on the user's role:

**All roles see:**
- Dashboard
- Email Logs
- Queue
- Allowed Domains
- SMTP Credentials
- DKIM Keys (view only — changes require Operator; the sidebar entry is not role-gated)

**Admin and Super Admin also see:**
- Configuration
- Users

**Version badge (v3.0.0):** The sidebar footer displays the current version number, making it easy to verify which release is running.

---

## Role Badges

Each role is displayed with a colour-coded badge throughout the interface:

| Role | Badge Colour |
|---|---|
| Viewer | Grey |
| Operator | Blue |
| Admin | Amber |
| Super Admin | Red |

Badges appear in the sidebar footer, user management table, and profile page.
