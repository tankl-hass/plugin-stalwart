# Stalwart API token contract

Status: verified on 2026-11-20 against Stalwart v1.1.1 using a narrow per-account
API key. **Each key is one account's instance, not a shared credential.** Every
cloud-mail account has its own Stalwart user and its own key; the pattern in this
document is what gets repeated per account. No development or test values appear
in this contract.

Only two credential values are needed per account: the API key **name** (an
identifier you choose in Stalwart) and its **secret** (the Bearer token value).
The secret is sealed in cloud-mail and is never written to a repo, config file,
JWT claim or CI variable.

## Credential boundaries

This contract is only for the per-account mirror key. Keep it separate from the
other credentials in the deployment:

| Credential | Scope / storage | Used for |
| --- | --- | --- |
| **A. Mirror key** — `<API_KEY_NAME>` + `<API_KEY_SECRET>` | One narrow `Replace` key per cloud-mail account; secret sealed in D1 | Worker-only JMAP import to `https://jadmin.<DOMAIN>`; optional Stalwart submit |
| **B. Interactive login** — Stalwart user + password or app password | Entered at the client; browser session only, never stored in cloud-mail | Bulwark / JMAP clients through the front door |
| Access policy on `jadmin.<DOMAIN>` | Browser Access session, not a Worker secret | No service token is attached by the optional front door |

- Per-account settings hold only `enabled`, the canonical
  `https://jadmin.<DOMAIN>` URL, the key name and the sealed secret (A).
- Credential B is never synced or injected by cloud-mail. An app password is
  preferable to the primary password because it is independently revocable;
  an OpenID connector is unnecessary for this merge.
- No Access service token is attached to proxied requests. Any caller that
  could reach the Worker would inherit it, so Access on `jadmin.<DOMAIN>` must
  be satisfied by the user, not by the proxy.
- The narrow key cannot list or read normal mail (no `Email/query` or
  `Mailbox/query`), so it can never stand in as a webmail login.

## 1. Credential identity

| Field | Value |
| --- | --- |
| API key name | `<API_KEY_NAME>` — identifies the key in Stalwart, not a secret |
| Secret | `<API_KEY_SECRET>` — copied once from Stalwart, stored sealed in cloud-mail |

The existing secret is preserved; only its permission mode was changed.

Everything else is discovered at runtime and must not be hardcoded:

| Field | Value | Source |
| --- | --- | --- |
| Account | `<USER@DOMAIN>` | session account / username |
| JMAP accountId | `<ACCOUNT_ID>` | `primaryAccounts["urn:ietf:params:jmap:mail"]` |
| ApiKey id | `<API_KEY_ID>` | Stalwart API key id for `<API_KEY_NAME>` |
| Description | `apiTokenForEmailImport` | set when the key is created |
| Previous mode | `Inherit` | WebUI |
| Current mode | `Replace` | WebUI / `x:ApiKey/set` |
| `allowedIps` | `127.0.0.1` | unchanged |

The update was made through `x:ApiKey/set` while the key still had its self-service
`sysApiKeyUpdate` permission from the User role. After the update, the key no
longer has that permission.

## 2. Applied permissions

```json
{
  "@type": "Replace",
  "permissions": {
    "authenticate": true,
    "emailReceive": true,
    "emailSend": true,
    "jmapEmailGet": true,
    "jmapEmailImport": true,
    "jmapBlobUpload": true,
    "jmapMailboxGet": true,
    "jmapMailboxQuery": true,
    "jmapIdentityGet": true,
    "jmapEmailSubmissionCreate": true
  }
}
```

This permission set is the same for every account; only the name and secret differ.

### Why each permission is present

| Permission | Purpose |
| --- | --- |
| `authenticate` | JMAP and WebSocket sign-in |
| `emailReceive` | Accept/store mail for this account |
| `emailSend` | Outbound submission permission |
| `jmapEmailSubmissionCreate` | `EmailSubmission/set` |
| `jmapIdentityGet` | Resolve `identityId` for submission |
| `jmapEmailGet` | Makes the `mail` capability available; supports import/result handling |
| `jmapEmailImport` | Store raw RFC 5322 via `Email/import` |
| `jmapBlobUpload` | Upload raw MIME to `/jmap/upload/{accountId}/` |
| `jmapMailboxGet`, `jmapMailboxQuery` | Resolve the Inbox `mailboxId` |

### Deliberately omitted

```text
impersonate
all sys* management permissions, including sysApiKeyGet / sysApiKeyUpdate
urn:stalwart:jmap management calls
scim*, dav*, imap*, pop3*, sieve*
calendar*, contacts*, filenode*, vacationresponse, quota, metadata
webhook creation or update permissions
```

The bridge is per-account, so `impersonate` is unnecessary. Webhook
self-registration is also excluded because it requires `sysWebHookCreate`; use
the JMAP method response as the storage ack, or have an administrator configure
a webhook separately.

## 3. Verification performed

All checks used the forwarded JMAP HTTP listener at `127.0.0.1:8080`. No TCP
discovery was performed. Repeat the checks with each account's own
`<API_KEY_NAME>` / `<API_KEY_SECRET>` and the ids its session returns.

### Authentication still works

`GET /jmap/session` with `Authorization: Bearer <API_KEY_SECRET>` returned that
account's mail account id and username.

### Capability visibility narrowed

`accountCapabilities` now contains:

```text
urn:ietf:params:jmap:mail
urn:ietf:params:jmap:submission
urn:ietf:params:jmap:websocket
urn:ietf:params:jmap:principals
urn:ietf:params:jmap:principals:availability
urn:ietf:params:jmap:mail:share
urn:ietf:params:jmap:emailpush
urn:stalwart:jmap
```

It no longer contains `calendars`, `contacts`, `filenode`, `sieve`,
`vacationresponse`, `quota`, `blob`, `metadata`, and others.

The top-level `capabilities` object still lists server-wide capabilities. That is
normal; the per-account set is the effective set.

`urn:stalwart:jmap` is advertised for every account and does **not** imply
management access.

### Management access is gone

```json
["error",{"type":"forbidden","description":"You are not authorized to perform this action"},"0"]
```

This was the response to `x:ApiKey/get` after narrowing.

### Required mail reads still work

Stalwart v1.1.1 requires an explicit `accountId` on JMAP methods:

```json
{"using":["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail","urn:ietf:params:jmap:submission"],"methodCalls":[
  ["Mailbox/query",{"accountId":"<ACCOUNT_ID>","filter":{"role":"inbox"}},"0"],
  ["Identity/get",{"accountId":"<ACCOUNT_ID>"},"1"]
]}
```

Result shape (ids come from that account's session):

```text
Mailbox/query role=inbox → id "<INBOX_ID>"
Identity/get             → id "<IDENTITY_ID>"
```

No message was uploaded, imported, or sent during verification.

## 4. JMAP call contract for the later feature

Replace `<ACCOUNT_ID>` with the id from that account's session and `<INBOX_ID>`
with the id from its `Mailbox/query` result. These ids are account-specific and
may change after a reinstall.

1. `GET /jmap/session`
   Assert `accounts`, `mail`, `submission`, and `websocket` for the account.

2. `POST /jmap/upload/<ACCOUNT_ID>`
   Upload the raw RFC 5322 message. Use the returned real `blobId`.
   Do **not** use a same-request creation id such as `#u0`: Stalwart v1.1.1 rejects that with `Invalid blob id`.

3. `Mailbox/query` with `accountId: "<ACCOUNT_ID>"` and `filter: {"role":"inbox"}`, then `Mailbox/get`
   Resolve the Inbox id at startup so a reinstall does not break the bridge.

4. `Email/import` with `accountId: "<ACCOUNT_ID>"`, `blobId`, `mailboxIds: {"<INBOX_ID>": true}`, `receivedAt`, and keywords.
   Treat the `Email/import` response as the storage ack.

5. `Identity/get` with `accountId: "<ACCOUNT_ID>"`
   Use the returned identity id for submission.

6. `EmailSubmission/set` with `accountId: "<ACCOUNT_ID>"`, `emailId`, and `identityId`.
   Stalwart's MTA route then relays outbound mail through Cloudflare SMTP.

7. WebSocket liveness
   Brief authenticated open/ping/close against `/jmap/ws`, with backoff. Do not keep one permanent outbound socket per account in a normal Worker request.

The `urn:ietf:params:jmap:blob` account capability is absent because
`jmapBlobGet` was not granted. This is intentional: use the HTTP upload endpoint,
not the `Blob/get` method.

## 5. Failure reference

| Failure | Meaning / action |
| --- | --- |
| `x:Account/query → forbidden` | Expected. The key has no `sysAccountQuery`; do not add admin permissions. |
| `x:ApiKey/get → forbidden` | Expected after narrowing. |
| `unknownMethod` for mail methods | Check that `urn:ietf:params:jmap:mail` is in `using` and that `jmapEmailGet` is still granted. |
| `invalidArguments: The "accountId" property is required` | Stalwart v1.1.1 strict validation; always pass the discovered `accountId`. |
| `Invalid blob id` with `#u0` | Upload in a separate HTTP request first, then use the returned real `blobId`. |
| `forbidden` on webhook creation | Expected; webhook management requires `sysWebHookCreate`. |
| Missing `blob` capability | Expected; use `/jmap/upload/{accountId}/`. |

## 6. Secret storage in cloud-mail

Do not store any account's secret in plaintext in `setting`, `wrangler.toml`,
GitHub Actions, or a JWT claim. The API key **name** is not secret and can live
with the account settings; only the **secret** is sealed.

Recommended derivation:

```text
encKey = HKDF-SHA256(
  ikm  = env.jwt_secret,
  salt = "cloud-mail/stalwart-token/v1",
  info = accountId
)

stored = AES-256-GCM(encKey, stalwartToken)
```

Keep only `{ version, iv, tag, ciphertext }`. `JWT_SECRET` remains a signing
secret; deriving a separate encryption key avoids reusing it directly.

## 7. Rotation / further narrowing

A key cannot update itself anymore because `sysApiKeyUpdate` was removed. To
change any account's key:

1. Reset that credential to `Inherit` in the WebUI at `https://jadmin.<DOMAIN>/admin`.
2. Re-apply a narrower `Replace` set.
3. Re-run the session and mail-read checks above with that account's name and secret.

The secret itself does not need to rotate when only the permission set changes.
