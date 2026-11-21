# plugin-stalwart

An **add-on** for a working [maillab/cloud-mail](https://github.com/maillab/cloud-mail)
deployment. It publishes a private, LAN-hosted **Stalwart** WebUI through a
Cloudflare Tunnel on a subdomain of the **same** `<DOMAIN>` cloud-mail already
owns, and gives Stalwart an outbound path through Cloudflare Email Service.

**cloud-mail remains the primary mail system on `<DOMAIN>`.** Nothing in this
repository changes cloud-mail's MX, Email Routing, `email()` handler, webmail
host, D1/R2/KV, send path, or Worker code. This repo is additive and easy to
roll back by deleting one DNS record and one tunnel route.

## Design at a glance

```text
                         Cloudflare zone <DOMAIN>
  +----------------------------------------------------------------------+
  | MX @          --> Email Routing --> cloud-mail Worker email()         |
  |                                     (UNCHANGED)                      |
  | mail.<DOMAIN> --> cloud-mail webmail (UNCHANGED)                      |
  | cloud-mail   --> CF Email binding / Resend (UNCHANGED)                |
  |                                                                      |
  | jadmin.<DOMAIN> --> cloudflared tunnel --> Stalwart 127.0.0.1:8080    |
  |                       ^                        | MTA relay            |
  |                       | OPTIONAL Worker,        +-> smtp.mx.cloudflare.net:465
  |                       | no route, transparent                        |
  |                       +-- JMAP clients / Bulwark Lite (manual)        |
  +----------------------------------------------------------------------+
                                       private LAN, no inbound ports
```

Hostname ownership:

| Hostname | Owner | Served by |
| --- | --- | --- |
| `<DOMAIN>` (base) | cloud-mail | MX + Email Routing |
| `mail.<DOMAIN>` | cloud-mail | cloud-mail Worker custom domain |
| `jadmin.<DOMAIN>` | this add-on | Cloudflare Tunnel directly — **no Worker route** |
| front door Worker | this add-on (optional) | Script only — **no route**, `workers_dev = false`, no public hostname |

### Credential boundaries

There are exactly two credential sets per account, plus one optional edge
secret. Keep them separate; they are not interchangeable.

| Credential | Scope / storage | Used for |
| --- | --- | --- |
| **A. Mirror key**: `<API_KEY_NAME>` + `<API_KEY_SECRET>` | One narrow `Replace` key per cloud-mail account; secret sealed in D1 | Worker-only JMAP import to `https://jadmin.<DOMAIN>`; optional Stalwart submit |
| **B. Interactive login**: Stalwart user + password or app password | Entered at the client; browser session only, never stored in cloud-mail | Bulwark / JMAP clients through the front door |
| Access policy on `jadmin.<DOMAIN>` | Normal user session / Access login at the browser | No Access service token is attached to proxied requests |

- Per-account settings hold **only** `enabled`, the canonical
  `https://jadmin.<DOMAIN>` URL, the key name and the sealed secret (A). The
  contract is in [`docs/token-contract.md`](docs/token-contract.md).
- Credential B is never synced or injected by cloud-mail. An app password is
  preferable to the primary password because it is independently revocable;
  an OpenID connector is unnecessary for this merge.
- No Access service token is attached to proxied requests. Any caller that
  could reach the Worker would inherit it, so the proxy deliberately does not
  satisfy an Access policy on `jadmin.<DOMAIN>`; users sign in normally.
- Outbound relay keeps its own Cloudflare Email Sending token on the Stalwart
  host. Never reuse A, B or the edge secret there.

## What is in this repo

| Path | Purpose |
| --- | --- |
| `src/index.ts` | Optional, transparent reverse-proxy Worker (no route, no workers.dev) |
| `test/index.test.ts` | Unit tests for the proxy |
| `wrangler.toml` | Local dev config |
| `wrangler-action.toml` | Deploy template; `${DOMAIN}` is injected by Actions |
| `.github/workflows/` | CI and optional Worker deploy |
| [`.github/actions/deploy-stalwart/`](.github/actions/deploy-stalwart/action.yml) | Reusable, version-pinned action that co-deploys the front door and webmail assets |
| [`webmail/`](webmail/) | Optional manually deployable Bulwark Lite static assets (see §9) |
| `tunnel/config.example.yml` | `cloudflared` ingress for `jadmin.<DOMAIN>` |
| [`docs/merge.md`](docs/merge.md) | How to land this as `stalwart/` in latest `maillab/cloud-mail` |
| [`docs/token-contract.md`](docs/token-contract.md) | Narrow Stalwart credential used by the delivery bridge |
| [`docs/client-setup.md`](docs/client-setup.md) | Client apps for reading/sending mail as a user |
| [`docs/android-ca.md`](docs/android-ca.md) | Android trust anchor and rooted-device CA install |
| [`docs/blobstore-migration.md`](docs/blobstore-migration.md) | Stalwart v1.1.1 SQLite `Default` → `FileSystem` blob migration |
| [`scripts/merge-into-cloud-mail.sh`](scripts/merge-into-cloud-mail.sh) | Scaffold helper for that merge |
| [`scripts/migrate-sqlite-blobs-to-filesystem.py`](scripts/migrate-sqlite-blobs-to-filesystem.py) | Copy SQLite blobs into the FileSystem layout |
| [`scripts/local-jmap-proxy.py`](scripts/local-jmap-proxy.py) | Local HTTPS proxy for testing user clients against Stalwart |
| [`scripts/generate-local-jmap-cert.sh`](scripts/generate-local-jmap-cert.sh) | Local-only CA + TLS cert for the JMAP reverse proxy |

## Red lines

1. **Do not touch cloud-mail.** No MX change, no `mail.<DOMAIN>` change, no
   Worker edits, no new binding. Verify cloud-mail first, add this second.
2. **No inbound port on the Stalwart host.** The HTTP listener binds
   `127.0.0.1:8080` only; only outbound TCP 465 is needed.
3. **Never route `jadmin.<DOMAIN>` to the Worker.** The tunnel serves it.
   Routing it to the Worker creates a fetch loop.
4. **`STALWART_PUBLIC_URL` must exactly equal the URL users type**, normally
   `https://jadmin.<DOMAIN>`. WebUI sign-in requires HTTPS and a matching
   public URL. This Worker does not rewrite discovery documents.
5. **The Worker never injects Stalwart credentials.** There is no
   `UPSTREAM_TOKEN`; client `Authorization` headers are passed to Stalwart
   unchanged. No Access service token is injected either — see §4.

## 1. Checkpoint: cloud-mail first

Before adding anything, confirm the existing system is healthy:

- [ ] Webmail login works at `mail.<DOMAIN>`.
- [ ] One external email can be sent.
- [ ] One external email can be received.
- [ ] MX still points to Cloudflare Email Routing, not to Stalwart.

If any of these fail, fix cloud-mail before continuing. Everything below is
independent of it.

## 2. Publish the Stalwart WebUI through the tunnel

### DNS

Create one proxied CNAME (usually done by `cloudflared tunnel route dns`):

```text
jadmin.<DOMAIN>   CNAME   <tunnel-id>.cfargotunnel.com   (proxied)
```

Do not create an A record or an MX on `jadmin.<DOMAIN>`.

### Stalwart

```bash
STALWART_PUBLIC_URL=https://jadmin.<DOMAIN>
```

- HTTP listener: bind `127.0.0.1:8080`, `implicitTls = false` — Cloudflare
  terminates TLS.
- Leave `proxyTrustedNetworks` and `overrideProxyTrustedNetworks` empty.
  Adding loopback there requires PROXY v2 and breaks plain HTTP from
  `cloudflared`.
- Add `jadmin.<DOMAIN>` to Stalwart's trusted/service hostnames so the WebUI
  sign-in is accepted as first-party.
- Restart Stalwart after environment changes.

### cloudflared

See [`tunnel/config.example.yml`](tunnel/config.example.yml). The ingress is a
single host:

```yaml
ingress:
  - hostname: jadmin.<DOMAIN>
    service: http://127.0.0.1:8080
    originRequest:
      httpHostHeader: jadmin.<DOMAIN>
  - service: http_status:404
```

Either a remotely-managed tunnel (dashboard token + `cloudflared service
install`) or a locally-managed config file works.

### Access

Put Cloudflare Access **only** in front of `jadmin.<DOMAIN>` if you want an
extra login layer. Never apply that policy to `mail.<DOMAIN>`.

If JMAP clients connect directly to `jadmin.<DOMAIN>`, they must satisfy the
Access policy too (browser Access session, or leave that path outside Access).
The optional front-door Worker does **not** carry a service token.

Verify:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://jadmin.<DOMAIN>/admin
# 200, or an Access redirect
```

## 3. Outbound mail: Stalwart → Cloudflare Email Service

Stalwart's MTA relays external mail to Cloudflare's authenticated SMTP
submission endpoint. This is the same pipeline cloud-mail already uses, so no
new sending system is introduced.

| Setting | Value |
| --- | --- |
| Host | `smtp.mx.cloudflare.net` |
| Port | `465` |
| Security | Implicit TLS (SMTPS), **not** STARTTLS on 587 |
| SMTP AUTH | `PLAIN` or `LOGIN` |
| Username | literal `api_token` |
| Password | Cloudflare API token with **Email Sending: Edit** |

Prerequisites:

- Enable **Email Service → Email Sending** on the Cloudflare account.
- Onboard `<DOMAIN>` under Email Sending. This is separate from Email Routing;
  MX records and Email Sending DKIM records coexist.
- Create a dedicated API token with only **Email Sending: Edit**. Store it on
  the Stalwart host as an environment variable, e.g.
  `CLOUDFLARE_EMAIL_TOKEN`. Do not commit it and do not put it in GitHub
  Actions for this repo.

Stalwart outbound route (WebUI: Settings → MTA → Outbound → Routes). Field
names follow Stalwart's object model; adapt to your installed version:

```json
{
  "@type": "Relay",
  "name": "cloudflare",
  "host": "smtp.mx.cloudflare.net",
  "port": 465,
  "protocol": "smtp",
  "implicitTls": true,
  "authUsername": "api_token",
  "authSecret": {
    "@type": "EnvironmentVariable",
    "variableName": "CLOUDFLARE_EMAIL_TOKEN"
  }
}
```

Outbound strategy: keep local delivery local, send everything else through
that route.

```text
if is_local_domain(rcpt_domain) then "local" else "cloudflare"
```

In Stalwart's expression syntax this is a `route` strategy with a match on
`is_local_domain(rcpt_domain)` returning `local`, otherwise `cloudflare`.

Operational notes:

- Cloud-mail and Stalwart share the account's sending quota and suppression
  list. Sending from both is fine, but watch volume.
- Let Cloudflare apply DKIM/ARC for submitted mail, or keep Stalwart signing —
  do not publish conflicting DKIM selectors without checking the dashboard.
- Cloudflare SMTP limits: 25 MiB message size, 50 recipients per session.
- Sender addresses (`MAIL FROM`) must be on an onboarded domain or submission
  fails with `550 5.7.1`.
- Only outbound TCP 465 needs to leave the LAN host.

**Rollback:** remove the relay route. cloud-mail's send path is untouched, so
it keeps working.

## 4. Optional Worker front door

The tunnel alone is enough for WebUI and JMAP. This Worker is an optional,
generic transparent proxy. It declares no route and sets `workers_dev = false`,
so deploying it does not expose any public hostname.

Properties of this proxy:

- Transparent: method, path, query, body, and client credentials pass through.
- No discovery rewriting and no URL substitution. Stalwart's own public URLs
  are returned unchanged, so `https://jadmin.<DOMAIN>` remains canonical. If
  discovery shows another origin, fix `STALWART_PUBLIC_URL` on Stalwart, not
  here.
- Forwards WebSocket upgrades (including `Sec-WebSocket-Protocol`), strips
  hop-by-hop headers, drops spoofed `X-Forwarded-*` / `CF-*` headers, and
  answers `/health` and `/healthz`.
- Does not inspect or replace `Authorization`. Never add a shared bearer token
  here; a token on an unauthenticated edge path would let anonymous callers
  act as that account.
- No Access service token is attached to upstream requests. If Access protects
  `jadmin.<DOMAIN>`, users authenticate themselves; the Worker does not bypass
  that policy.

### Deploy through the Action

The deploy workflow publishes the script but keeps it routeless. Set the
`DOMAIN` secret to a **plain base domain** such as `example.com` (not the
`jadmin.` host); the workflow renders `UPSTREAM_URL = "jadmin.example.com"`.
When merged into cloud-mail, the equivalent job uses `STALWART_DOMAIN` instead,
because cloud-mail's own `DOMAIN` is a JSON array. If Cloudflare Access protects
`jadmin.<DOMAIN>`, let users sign in through Access; the Worker never carries a
service token that could bypass it.

For cloud-mail, prefer the version-pinned reusable action in [§9.1](#91-co-deploy-through-the-pinned-action):
it deploys this Worker and the static webmail assets together, still with no
route for either.

### Manual deploy

```sh
npm ci && npm run check
BASE_DOMAIN=example.com   # plain base domain, no scheme and no `jadmin.` host
sed "s|\${DOMAIN}|${BASE_DOMAIN}|g" wrangler-action.toml > wrangler.deploy.toml
npx wrangler deploy -c wrangler.deploy.toml
```

Local check:

```sh
cp .dev.vars.example .dev.vars   # set UPSTREAM_URL
npm run dev
curl -sS http://localhost:8787/health   # ok
```

### Notes from live Stalwart checks

- `/health` returned `ok`, and the JMAP session and discovery documents came
  back with the origin configured by `STALWART_PUBLIC_URL`. No proxy rewriting
  was needed — keep it that way.
- `/jmap/ws` upgraded to HTTP 101 with `Sec-WebSocket-Protocol: jmap`. A proxy
  that cannot relay the upgrade will break push silently; this Worker handles
  it, a plain HTTP-only proxy does not.
- The narrow per-account mirror key returned `forbidden` for `Email/query` and
  management calls. That is the intended shape: it cannot replace a user login
  at the front door, and the Worker must never inject it.
- When a static client is served from the same origin as the proxy, these paths
  must reach Stalwart unchanged: `/.well-known/jmap`, `/jmap/*` (including
  `/jmap/upload/{accountId}` and `/jmap/ws`), `/api/auth`, `/auth/token` and
  `/auth/register`. An `/api/auth` 400 before registration is a normal client
  probe, not a failure.
- Keep the upload flow as two steps: `POST /jmap/upload/{accountId}` returns a
  real `blobId`, then `Email/import` uses that id.

## 5. Verification and rollback

After each step, re-run the cloud-mail checkpoint from section 1.

| Check | Expectation |
| --- | --- |
| `https://jadmin.<DOMAIN>/admin` | 200 or Access redirect |
| Stalwart JMAP `/.well-known/jmap` | `apiUrl` starts with `https://jadmin.<DOMAIN>` |
| Stalwart outbound test message | accepted by CF SMTP (`250 2.0.0 Ok`), appears in Email Sending logs |
| `mail.<DOMAIN>` login, send, receive | unchanged |
| Cloudflare routes | no route for `jadmin.<DOMAIN>` |

Rollback, in order: remove the Stalwart relay route → remove the
`jadmin.<DOMAIN>` DNS record / tunnel route. No cloud-mail state is changed.

## 6. Merging into latest `maillab/cloud-mail`

This repository is staged as the `stalwart/` package for a later upstream PR.
See [`docs/merge.md`](docs/merge.md) and run
[`scripts/merge-into-cloud-mail.sh`](scripts/merge-into-cloud-mail.sh) against a
fresh checkout of `maillab/cloud-mail` main. The `mail-worker/` and `mail-vue/`
trees stay clean in that PR. The narrow Stalwart credential contract used by the
later delivery feature is recorded in
[`docs/token-contract.md`](docs/token-contract.md).

## 9. Optional webmail: Bulwark Lite (static assets only)

[`webmail/`](webmail/) holds the pinned static client: `config.example.json`,
`deploy.sh`, and an asset-only `wrangler.toml` with **no `main`**. `deploy.sh`
downloads Bulwark Lite **1.21.3**, verifies SHA-256
`3cbccac8d9b37104829e884be8c28814a1b095ca089e068168ed456d9cca0c6b`, renders
`jmapServerUrl = https://jadmin.<DOMAIN>`, pins the CSP `connect-src` to that
single origin, then renders `wrangler-action.toml` into a gitignored
`wrangler.deploy.toml` with a **Custom Domain `jmap.<DOMAIN>`** and deploys with
`--name`. Cloudflare creates the DNS record and certificate; `jmap` must not
already have a CNAME. No tunnel route is created and JMAP stays at
`https://jadmin.<DOMAIN>` — `jmap.<DOMAIN>` serves only the webmail UI.

### 9.1 Co-deploy through the pinned Action

The reusable action in
[`.github/actions/deploy-stalwart/`](.github/actions/deploy-stalwart/action.yml)
deploys the routeless front door and the asset-only webmail together. Pin it to
a release tag from cloud-mail:

```yaml
- uses: tankl-hass/plugin-stalwart/.github/actions/deploy-stalwart@v1.0.0
  with:
    cloudflare_api_token: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    cloudflare_account_id: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    base_domain: ${{ secrets.STALWART_DOMAIN }}
```

It stays a no-op until `STALWART_DOMAIN` (a plain base domain, not cloud-mail's
JSON-array `DOMAIN`) is set; the webmail is then published at
`https://jmap.<STALWART_DOMAIN>`. The Access inputs are optional Worker secrets
and are passed only when both are present; they are never Stalwart credentials
or per-account settings. Set cloud-mail's `STALWART_WEBMAIL_URL` to that same
`https://jmap.<DOMAIN>` value so the in-app entry points at it.

### 9.2 Manual deploy

```sh
cd webmail
DOMAIN=<DOMAIN> ./deploy.sh                  # download, verify, unpack, render config
# review dist/config.json, dist/_headers and wrangler.deploy.toml
DOMAIN=<DOMAIN> ./deploy.sh --deploy         # prepare, then deploy on jmap.<DOMAIN>
```

The browser needs `usePermissiveCors = true` on Stalwart 1.1.1, or a same-origin
reverse proxy. Sign in with a normal Stalwart user account — never the narrow
per-account mirror key.

## Local development

```sh
npm install
cp .dev.vars.example .dev.vars    # set UPSTREAM_URL
npm run dev                        # http://localhost:8787
npm run check                      # typecheck + tests
```

## Configuration reference

| Name | Kind | Description |
| --- | --- | --- |
| `DOMAIN` | Action secret | Base domain, plain string in this repo; injected into the deploy template |
| `STALWART_DOMAIN` | Action secret/var | Same plain base domain after merging into cloud-mail; do not use cloud-mail's JSON-array `DOMAIN` there |
| `UPSTREAM_URL` | derived var / local secret | Tunnel hostname, e.g. `jadmin.example.com`; bare hostname means HTTPS:443 |
| `workers_dev` | config | `false` for the front door so it has no public hostname |
| `PATH_PREFIX` | var | Optional prefix stripped before forwarding |
| `CORS_ORIGIN` | var | Optional `Access-Control-Allow-Origin` value |
| `STALWART_PUBLIC_URL` | Stalwart env | Must be exactly `https://jadmin.<DOMAIN>` |
| `CLOUDFLARE_EMAIL_TOKEN` | Stalwart host env | API token with Email Sending: Edit, used as SMTP password |

## License

MIT — see [LICENSE](LICENSE).
