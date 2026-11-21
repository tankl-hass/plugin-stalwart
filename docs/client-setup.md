# Client-side setup: reading and sending mail as a user

This document covers **user clients** for a Stalwart instance — apps that list mailboxes,
read mail, and send messages. It is distinct from [the delivery bridge](token-contract.md),
which stores raw MIME with a narrow API token.

## 1. Choose a client

| Client | Platform | License | Notes |
| --- | --- | --- | --- |
| **Sterna Mail** | Android (arm64-v8a, Android 8+) | GPLv3 | JMAP-first, push without Google services, multi-account. Tested against Stalwart. [F-Droid](https://f-droid.org/en/packages/app.sterna/) · [source](https://codeberg.org/emon/sterna-mail) |
| **aerc** | Linux/Ubuntu FS (also x86_64) | GPLv3 | Terminal client with a native JMAP backend. `sudo apt install aerc` on Ubuntu arm64. |
| **Bulwark Webmail** | Browser / self-hosted | AGPLv3 | Full webmail (mail, calendar, contacts, files); Docker or static "Lite". [repo](https://github.com/bulwarkmail/webmail) |
| **JMAP Webmail** | Browser / self-hosted | MIT | Lighter full webmail; `JMAP_SERVER_URL` env var. [repo](https://github.com/root-fr/jmap-webmail) |
| Ltt.rs, Gridlink Mail, Flectar Mail, meli, mjmap | Android / cross-platform / TUI / CLI | various | More options listed at [jmap.io/software](https://jmap.io/software/) |

The full index of JMAP implementations is at <https://jmap.io/software/>.

The Stalwart WebUI is for administration, not for the user-client workflow described here.

## 2. Credential rule — do not use the delivery token

The narrow `apiTokenForEmailImport` key described in [token-contract.md](token-contract.md)
grants `jmapEmailGet`, `jmapMailboxGet/Query`, `jmapIdentityGet`,
`jmapEmailSubmissionCreate`, `jmapEmailImport`, `jmapBlobUpload`, but **not**
`jmapEmailQuery`, `jmapEmailSet`, or `jmapMailboxSet`.

A user client calls `Email/query`, `Email/set`, `Mailbox/set`, and `Email/changes`. With the
delivery token, sign-in may succeed but the inbox cannot be listed, marked read, or moved —
`Email/query` returns "forbidden". Use one of:

- the mailbox account's **real password** (recommended; or an app password), or
- a **separate user-scoped API token** with the permissions a normal client needs.

## 3. Local transport (reverse proxy)

The examples below assume a local TLS reverse proxy in front of the Stalwart HTTP listener:

```text
client --HTTPS--> 127.0.0.1:8443 --HTTP--> 127.0.0.1:8080 (Stalwart)
```

Generate the local CA and certificate:

```bash
./scripts/generate-local-jmap-cert.sh
# writes scripts/local-jmap-cert/{ca.crt,cert.pem,key.pem,server.ext,...}
```

SANs are `localhost`, `127.0.0.1`, `::1` only. `mail.<DOMAIN>` is owned by cloud-mail and must
never be pointed at this local proxy or included in its certificate.

Run the helper in this repo ([`scripts/local-jmap-proxy.py`](../scripts/local-jmap-proxy.py)).
It needs Python 3 and `requests` (`sudo apt install python3-requests`, or
`python3 -m pip install requests`):

```bash
CERT=scripts/local-jmap-cert/cert.pem \
KEY=scripts/local-jmap-cert/key.pem \
LISTEN_PORT=8443 \
UPSTREAM=http://127.0.0.1:8080 \
PUBLIC_ORIGIN=https://localhost:8443 \
python3 scripts/local-jmap-proxy.py
```

It listens on `127.0.0.1` by default and rewrites JMAP discovery URLs to `PUBLIC_ORIGIN`.
For access from another device, set `LISTEN_HOST=0.0.0.0`, add a reachable hostname/IP to
`scripts/local-jmap-cert/server.ext`, rebuild the certificate, and use that hostname in the
client URL. Do not rebuild with a production hostname.

Smoke test from the same machine:

```bash
curl --cacert scripts/local-jmap-cert/ca.crt https://localhost:8443/jmap/session
```

## 4. Sterna Mail (Android)

1. Install an arm64-v8a build (F-Droid or the release APK).
2. Add account → email `user@<DOMAIN>` → let autodiscovery fail → **Configure manually**.
3. Protocol: **JMAP**. JMAP server: `https://localhost:8443/jmap/session`
   (Sterna accepts a full session URL in that field).
4. Auth: the account password or a user-scoped token — **not** the delivery token.
5. Trust the local CA on the device; see [android-ca.md](android-ca.md).

`localhost` on Android means the phone. Use `https://localhost:8443/...` only if the proxy runs
on the device itself (e.g. inside an Ubuntu filesystem via proot/chroot). If the proxy stays on
another machine, use the reachable-hostname approach in section 3.

## 5. aerc (Ubuntu FS / arm64)

Trust the CA first:

```bash
sudo cp scripts/local-jmap-cert/ca.crt /usr/local/share/ca-certificates/jmap-local.crt
sudo update-ca-certificates
```

`~/.config/aerc/accounts.conf`:

```ini
[Stalwart]
source   = jmap://user%40<DOMAIN>:PASSWORD@localhost:8443/jmap/session
outgoing = jmap://
from     = User <user@<DOMAIN>>
```

- `jmap://` = Basic auth; `jmap+oauthbearer://` = OAuth bearer.
- URL-encode credentials (`@` → `%40`).
- `outgoing = jmap://` reuses the same connection for sending.
- Use the user password / full token, not the delivery token.

Other local options: `meli` (Rust TUI), `mjmap` (Go CLI), `jmaplib` / `jmapc` (Python).

## 6. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Sign-in OK, inbox empty / actions fail | Delivery token used. Switch to a user password or full-permission token. |
| `unable to get local issuer certificate` | CA not trusted. Import `ca.crt`; on Ubuntu run `update-ca-certificates`. |
| `No subjectAltNames on the certificate match` | Connect with `localhost` / `127.0.0.1`, or add your hostname to `scripts/local-jmap-cert/server.ext` and rebuild. |
| Android app ignores the user CA | Apps targeting API 24+ trust only system CAs. See [android-ca.md](android-ca.md). |
| Connection refused from phone | `localhost` is the phone; run the proxy on the device or use a reachable host and `LISTEN_HOST=0.0.0.0`. |
| Cert expires | Server cert is valid 825 days. Re-run the generation script, restart the proxy, and re-import `ca.crt` (the script regenerates the CA too). |
