# Reverse Proxy Setup (web UI behind nginx / Caddy / Cloudflare)

The management UI (port **8025** by default) can sit behind a TLS-terminating
reverse proxy. The SMTP listener (port **2525**) is a raw TCP protocol port —
**never** route it through an HTTP proxy; publish or firewall it directly.

```
Internet ──HTTPS──▶ Reverse proxy ──HTTP──▶ smtp-relay :8025
                       (TLS)                  (SMTP :2525 stays direct)
```

## The three settings that matter

Behind an HTTPS proxy, set **all three** of these (the first two as
environment variables on first boot, or any time via the Configuration page;
the third on the Configuration page):

| Setting | Value | Why |
|---|---|---|
| `WEB_SECURE_COOKIES` (config key `web_secure_cookies`) | `true` | Session cookies get the `Secure` flag so they are never sent over plain HTTP. |
| `WEB_TRUST_PROXY` (config key `web_trust_proxy`) | `true` | Rewrites the client IP from the right-most `X-Forwarded-For` hop and the request scheme from `X-Forwarded-Proto`. Without it every visitor shares the proxy's IP (the login throttle would lock ALL users out together after 5 failed attempts) and redirects/OAuth derive `http://` URLs. |
| `azure_redirect_uri` (Configuration page, Microsoft 365 section) | `https://relay.example.com/login/azure/callback` | Microsoft **exact-matches** the redirect URI against your Azure app registration. When empty, the callback URL is derived from the request — behind a proxy that yields the internal `http://host:8025` form and sign-in fails with a cryptic AADSTS redirect-URI error. |

Restart the app after changing `web_secure_cookies` / `web_trust_proxy`
(listener + web settings apply at startup).

## X-Forwarded-For: the proxy MUST append

The app trusts exactly **one** proxy hop: with `web_trust_proxy` enabled it
keys the login throttle on the **right-most** `X-Forwarded-For` entry — the
one your own proxy appended — because everything left of it is
client-controlled and spoofable. That is correct only if your proxy
**appends** the client IP (default for nginx's
`proxy_add_x_forwarded_for`, Caddy's `reverse_proxy`, and Cloudflare). Do not
configure the proxy to pass the inbound `X-Forwarded-For` header through
unchanged, and do not put two appending proxies in a row (a CDN in front of
nginx): with two hops the right-most entry would be your outer proxy's view,
not the client. If you must chain, only the LAST appending hop may be
trusted — key off the value YOUR closest proxy appended by terminating the
chain at one trusted proxy.

## nginx

```nginx
server {
    listen 443 ssl;
    server_name relay.example.com;

    ssl_certificate     /etc/letsencrypt/live/relay.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/relay.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8025;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        # The UI polls /api/stats every 15-30 s; no websockets are used.
        proxy_read_timeout 60s;
    }
}
```

In Docker the app port is fixed at **8025 inside the container**. To keep
plain HTTP off the network, publish the web port on loopback only:

```yaml
ports:
  - "127.0.0.1:8025:8025"     # web UI: proxied, TLS-terminated
  - "${SMTP_PORT_HOST:-2525}:2525"   # SMTP: direct TCP publish / firewall
```

(If nginx itself runs in a container on the same Docker network, proxy to
`http://smtp-relay:8025` instead of a published port and drop the web port
mapping entirely.)

## Caddy

```
relay.example.com {
    reverse_proxy 127.0.0.1:8025
}
```

Caddy supplies automatic HTTPS and sets `X-Forwarded-For` and
`X-Forwarded-Proto` correctly (appending, as the app expects).

## Health checks

The container healthcheck probes `http://127.0.0.1:<port>/login` from
INSIDE the container and is unaffected by the proxy. If you add an external
monitor, point it at the proxy's HTTPS URL — `/login` is the
unauthenticated canary.

## Checklist

- [ ] `WEB_SECURE_COOKIES=true`
- [ ] `WEB_TRUST_PROXY=true` (proxy appends to `X-Forwarded-For`)
- [ ] `azure_redirect_uri` = exact `https://…/login/azure/callback`
      registered in the Azure app (only if Microsoft sign-in is enabled)
- [ ] Web port published on `127.0.0.1` (or not published at all when the
      proxy shares the Docker network)
- [ ] SMTP port published directly / firewalled to your LAN
- [ ] App restarted after the config changes
