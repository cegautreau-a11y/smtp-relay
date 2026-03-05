# User Roles & Permissions

**SMTP Mail Relay v2.2.0**

The SMTP Mail Relay uses a four-tier role-based access control (RBAC) system. Each user is assigned exactly one role that determines what they can see and do in the web interface.

---

## Role Hierarchy

Roles are ordered from least to most privileged:

| Role | Level | Description |
|---|---|---|
| **Viewer** | 0 | Read-only access to the dashboard, email logs, and queue |
| **Operator** | 1 | Can manage domains, SMTP credentials, and queue entries |
| **Admin** | 2 | Full access to configuration, user management, and server controls |
| **Super Admin** | 3 | Can manage all users including other Admins. Highest privilege level |

A higher-level role inherits all permissions of the roles below it.

---

## Permission Matrix

| Feature | Viewer | Operator | Admin | Super Admin |
|---|---|---|---|---|
| View dashboard & statistics | ✅ | ✅ | ✅ | ✅ |
| View email logs | ✅ | ✅ | ✅ | ✅ |
| View email log detail (headers, Message-ID) | ✅ | ✅ | ✅ | ✅ |
| View email queue | ✅ | ✅ | ✅ | ✅ |
| View allowed domains list | ✅ | ✅ | ✅ | ✅ |
| View SMTP credentials list | ✅ | ✅ | ✅ | ✅ |
| Add/edit/delete domains | ❌ | ✅ | ✅ | ✅ |
| Add/edit/delete SMTP credentials | ❌ | ✅ | ✅ | ✅ |
| Retry individual failed queue entry | ❌ | ✅ | ✅ | ✅ |
| Delete individual queue entry | ❌ | ✅ | ✅ | ✅ |
| Retry All Failed (bulk requeue) | ❌ | ✅ | ✅ | ✅ |
| Delete All Failed (bulk delete) | ❌ | ✅ | ✅ | ✅ |
| Start/stop/restart SMTP server | ❌ | ❌ | ✅ | ✅ |
| Edit relay configuration | ❌ | ❌ | ✅ | ✅ |
| Reload/save config.json | ❌ | ❌ | ✅ | ✅ |
| View user management page | ❌ | ❌ | ✅ | ✅ |
| Create new users | ❌ | ❌ | ✅ | ✅ |
| Manage Viewer & Operator users | ❌ | ❌ | ✅ | ✅ |
| Manage Admin users | ❌ | ❌ | ❌ | ✅ |
| Manage Super Admin users | ❌ | ❌ | ❌ | ✅ |
| Change own password & email | ✅ | ✅ | ✅ | ✅ |

---

## Role Descriptions

### Viewer
Read-only access. Viewers can monitor the system — watching the dashboard, browsing email logs, and checking the queue — but cannot make any changes. Suitable for monitoring staff or stakeholders who need visibility without the ability to modify anything.

### Operator
All Viewer permissions plus the ability to manage the day-to-day operation of the relay. Operators can add and remove allowed domains, create and manage SMTP credentials, and handle the failed email queue — including retrying individual messages, retrying all failed messages at once, or permanently deleting failed entries. Operators cannot change relay configuration, restart the SMTP server, or manage user accounts.

### Admin
All Operator permissions plus full control over the relay configuration, SMTP server lifecycle (start/stop/restart), and user management for Viewer and Operator accounts. Admins can create new users and assign them Viewer or Operator roles. Admins cannot manage other Admin or Super Admin accounts.

### Super Admin
All Admin permissions plus the ability to manage Admin and Super Admin accounts. Super Admins have unrestricted access to every feature in the system. The default `admin` account created on first run is a Super Admin.

---

## Role Assignment Rules

Roles can only be assigned by users with sufficient privileges. This prevents privilege escalation.

### Who Can Assign What

| Actor Role | Can Create/Assign |
|---|---|
| **Super Admin** | Viewer, Operator, Admin, Super Admin |
| **Admin** | Viewer, Operator |
| **Operator** | *(cannot manage users)* |
| **Viewer** | *(cannot manage users)* |

### User Management Rules

- **You cannot change your own role.** Another admin must do it.
- **You cannot disable or delete yourself.**
- **Admins cannot manage other Admins** — only Super Admins can.
- **Admins can manage Viewers and Operators** — enable, disable, delete, change role, reset password.
- **Super Admins can manage everyone** — including other Super Admins (except themselves).

---

## Default Account

On first run, the application creates a default account:

| Field | Value |
|---|---|
| Username | `admin` |
| Password | `admin` |
| Role | **Super Admin** |

> **Important:** Change the default password immediately after first login via the **Profile** page.

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

**Admin and Super Admin also see:**
- Configuration
- Users

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