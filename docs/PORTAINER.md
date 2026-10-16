# Deploying on Docker via Portainer

This guide deploys SMTP Mail Relay as a **Portainer Stack** with persistent
storage. Written for Portainer CE/BE 2.x (Community Edition is sufficient).

Two ways to get the image running — pick one:

- **Method A — build it yourself, deploy via the web editor (recommended;
  no git access needed in Portainer):** download/clone the repo, `docker
  build` it on the Docker host, then deploy a stack from the **web editor**
  using the pre-built image.
- **Method B — let Portainer clone and build:** deploy the stack directly
  from the git repository.

---

## Design (both methods)

| Concern | Design |
|---|---|
| Web UI | port **8025** inside the container; host side overridable with `WEB_PORT_HOST` |
| SMTP | port **2525** inside the container; host side overridable with `SMTP_PORT_HOST` |
| Persistence | named volume `pg-data` — the PostgreSQL database (mail log, queue, users, configuration) lives there; `smtp-data` at `/app/data` holds an optional `LOG_FILE` |
| Health | built-in `healthcheck` (`GET /login` via python) → Portainer shows **Running / Healthy** |
| App server | gunicorn (production WSGI) automatically; `WEB_THREADS` controls request threads |
| Config model | env vars seed the **database** on **first boot only**; afterwards the database owns the configuration (change settings in the web UI) |

The secret key — which also encrypts the upstream relay/direct passwords —
is stored **in the database** (`web_secret_key`), so it **survives container
recreation and image upgrades** as long as the `pg-data` volume is kept.

---

## Method A — build it yourself, deploy via web editor

### 1. Get the code onto the Docker host

Do this on the machine that runs Docker/Portainer — images live on that
daemon, so an image built on your laptop won't appear on the server (move
images between machines with `docker save`/`docker load` or a registry):

```bash
git clone https://github.com/cegautreau-a11y/smtp-relay-beta.git
cd smtp-relay-beta
# …or download the ZIP from the GitHub repo page and unpack it instead
```

### 2. Build the image

```bash
docker build -t smtp-relay:latest .
```

The tag `smtp-relay:latest` matters — the stack file references it. Verify
with `docker images smtp-relay`.

### 3. Create the stack in Portainer

1. Portainer → **Stacks** → **+ Add stack**.
2. **Build method:** *Web editor*.
3. Paste the contents of [`docker-compose.portainer.yml`](../docker-compose.portainer.yml)
   from the repo — it is the standard compose file minus the `build:` line,
   so no build context is needed.
4. Open **Environment variables** and add **at minimum**:

   | Variable | Value | Notes |
   |---|---|---|
   | `SECRET_KEY` | a long random string | **required** — the stack fails to deploy without it (by design). Generate one with `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `POSTGRES_PASSWORD` | a long random string | **required** — password for the in-stack `postgres` service; `DATABASE_URL` is wired to it automatically |

   Optional variables (defaults are in the pasted file; full list in
   `.env.example`): `WEB_PORT_HOST`, `SMTP_PORT_HOST` (change when the host
   ports are taken), `RELAY_HOST`, `RELAY_PORT`, `RELAY_AUTH_USER`,
   `RELAY_AUTH_PASSWORD`, `DELIVERY_MODE`, `SMTP_LISTEN_PORT`,
   `BANNER_HOSTNAME`, `WEB_THREADS`, `DATABASE_URL` (override to target an
   external PostgreSQL server instead of the in-stack one).

   > **These variables apply on FIRST BOOT only.** They seed the database;
   > from then on the web UI owns the configuration. To re-apply changed env
   > vars later: delete the stack *with its volumes* and redeploy.

5. **Deploy the stack.** It uses the image you built — no git access needed.

### 4. First login

1. Stack → the container → **Logs**: note the
   `Created initial admin user 'admin' with password: …` WARNING — this is the
   only time the generated password is shown.
2. Browse to `http://<host>:8025` (or your `WEB_PORT_HOST` value) and log in
   with `admin` + that password. **Change it immediately** under Profile.
3. Configure the relay in the web UI: allowed sender domains, SMTP
   credentials for your sending applications, and the upstream relay
   destination (Configuration page). See `docs/CONFIGURATION.md`.

### 5. Updating (Method A)

```bash
cd smtp-relay-beta
git pull     # …or download the new ZIP and unpack over the old tree
docker build -t smtp-relay:latest .
```

Portainer → Stacks → the stack → **Update the stack** (no env changes
needed) — this recreates the container on the new image. Data is safe: the
database lives in the `pg-data` volume.

---

## Method B — let Portainer clone and build (alternative)

1. **Stacks → + Add stack → Build method: Repository.**
2. Repository URL / branch (`main`), compose path `docker-compose.yml`
   (note: that file has `build: .` — with it, the web editor cannot be used
   because the editor has no build context).
3. **Authentication:** for the private repo, a GitHub PAT with read access
   (username + token in the stack's authentication fields).
4. Add the `SECRET_KEY` environment variable as in Method A, then deploy.
   Portainer clones, builds the image, and starts the container.

Updating: **Pull and redeploy** in the stack view (rebuilds with the new
code).

---

## Verify health

- Stack detail shows the container as **Running (Healthy)** once the
  healthcheck passes (allow ~25 s start period).
- Quick SMTP check from any machine, replacing credentials/domains as
  configured: `docs/INSTALLATION.md` → *Verifying the Installation*.

## Backups

Everything stateful is in the `pg-data` volume — the PostgreSQL database
(mail log, queue, users, configuration, secret key). Back it up like any
Docker volume, e.g. with `pg_dump`:

```bash
docker run --rm -v <stackname>_pg-data:/var/lib/postgresql/data alpine true  # volume exists?
docker exec <stackname>-postgres-1 pg_dump -U smtp smtp_relay > smtp_relay_backup.sql
```

(Restore into a fresh `pg-data` volume with `psql < smtp_relay_backup.sql`
before first start.)

## Exposing SMTP on port 25 (optional)

Applications connect to `host:2525` by default. To receive on the standard
SMTP port instead, set `SMTP_PORT_HOST=25` in the stack's environment
variables **before first deployment** (port mappings are compose-level, not
web-UI settings). Make sure no MTA already binds port 25 on the host.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Stack fails immediately: `SECRET_KEY required` | The `SECRET_KEY` environment variable is not set in the stack — add it and redeploy (fail-fast by design) |
| Stack fails immediately: `POSTGRES_PASSWORD required` | The `POSTGRES_PASSWORD` environment variable is not set in the stack — add it and redeploy (fail-fast by design) |
| `image not found` on deploy (Method A) | Image built on a different daemon or under another tag — build on the Portainer host with `docker build -t smtp-relay:latest .` |
| Deploy error around `build` | A web-editor stack was given a compose containing `build:` (no build context) — use `docker-compose.portainer.yml` (Method A) or the *Repository* method (Method B) |
| Clone fails on private repo (Method B) | Missing/wrong PAT or expired credential — fix under the stack's *Authentication* |
| Container unhealthy | Check **Logs**: gunicorn not up yet (wait for start period), the database is unreachable, or the web port is already bound inside the container (`WEB_PORT` changed without `WEB_PORT_HOST`) |
| Port conflict on deploy | Host port taken — set `WEB_PORT_HOST` / `SMTP_PORT_HOST` and redeploy |
| Env var change had no effect | Expected: env vars are first-boot-only. Either change the setting in the web UI, or delete the stack *with volumes* and redeploy |
| Lost the generated admin password | It is shown once in the first-boot container logs. If the logs are gone: delete the stack **with the `pg-data` volume** and redeploy — a new admin password is generated, but ALL data (users, logs, queue, settings) is reset |
| Web UI unreachable after config edits | `web_host`/`web_port` are database settings (Configuration page, restart required); keep them at `0.0.0.0` / `8025` in Docker or the mapped ports break |
