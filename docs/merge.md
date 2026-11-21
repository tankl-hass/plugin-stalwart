# Merge scaffold: this repo → latest `maillab/cloud-mail`

Goal: add a `stalwart/` package to upstream `maillab/cloud-mail` **without
touching** `mail-worker/` or `mail-vue/`. Target base: current `main` of
`https://github.com/maillab/cloud-mail.git` (checked on 2026-11-19, commit
`64f9d2c`; re-check before opening the PR).

## Resulting tree

```text
maillab/cloud-mail/
├─ mail-worker/                 # untouched
├─ mail-vue/                    # untouched
├─ stalwart/                    # NEW — the contents of this repo root
│  ├─ src/index.ts
│  ├─ test/index.test.ts
│  ├─ package.json
│  ├─ package-lock.json
│  ├─ tsconfig.json
│  ├─ wrangler.toml
│  ├─ wrangler-action.toml
│  ├─ .dev.vars.example
│  ├─ README.md
│  └─ tunnel/config.example.yml
├─ .github/workflows/deploy-cloudflare.yml   # extended with a separate job
├─ .gitignore                   # extended
└─ README.md / README-en.md     # extended with a short section
```

Do **not** copy `.git/`, `.github/`, `docs/`, `scripts/`, `node_modules/`,
`dist/`, `.wrangler/`, `.dev.vars`, or `wrangler.deploy.toml`.

## Steps

1. Fetch a fresh upstream checkout and branch from its current `main`:

   ```sh
   git clone https://github.com/maillab/cloud-mail.git
   cd cloud-mail
   git checkout -b merge/stalwart-front-door origin/main
   ```

2. Run the scaffold helper from this repository:

   ```sh
   /path/to/plugin-stalwart/scripts/merge-into-cloud-mail.sh /path/to/cloud-mail
   ```

   It copies the tracked package files into `stalwart/` and appends the missing
   ignore rules to the root `.gitignore`. Review the diff before committing.

3. Extend `.github/workflows/deploy-cloudflare.yml`:

   - add `stalwart/**` to `on.push.paths`;
   - add a **separate** deploy job for `stalwart/` using `npm ci` (upstream's
     main app uses `pnpm`; do not mix them);
   - give it its own domain secret. Upstream's `DOMAIN` is a **JSON array**
     like `["example.com"]`, while this Worker's template expects a plain
     string. Use `STALWART_DOMAIN` (plain `example.com`) rather than feeding the
     array into `wrangler-action.toml`.

   The job added on `merge/stalwart-front-door` (adapt secrets to upstream's
   style). The deploy is skipped when `STALWART_DOMAIN` is absent, so upstream
   does not need to configure anything for an optional component:

   ```yaml
   Deploy-stalwart-front-door:
     name: Optional Stalwart front door
     runs-on: ubuntu-latest
     permissions:
       contents: read
     env:
       STALWART_DOMAIN: ${{ secrets.STALWART_DOMAIN }}
       CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
       CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
     steps:
       - uses: actions/checkout@v4
       - uses: actions/setup-node@v4
         with:
           node-version: "20"
           cache: "npm"
           cache-dependency-path: stalwart/package-lock.json
       - run: npm ci
         working-directory: stalwart
       - run: npm run check
         working-directory: stalwart
       - name: 🔎 Check optional domain secret
         run: |
           if [ -z "$STALWART_DOMAIN" ]; then
             echo "::warning::STALWART_DOMAIN is not set; skipping the optional Stalwart front door deploy. The tunnel at jadmin.<DOMAIN> is unaffected."
           fi
       - name: 🛠 Render deploy config
         if: env.STALWART_DOMAIN != ''
         working-directory: stalwart
         run: sed "s|\${DOMAIN}|${STALWART_DOMAIN}|g" wrangler-action.toml > wrangler.deploy.toml
       - name: 🚀 Deploy optional front door
         if: env.STALWART_DOMAIN != ''
         uses: cloudflare/wrangler-action@v3
         with:
           apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
           accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
           workingDirectory: stalwart
           command: deploy -c wrangler.deploy.toml
   ```

   The template declares no route and `workers_dev = false`, so the tunnel-only
   setup keeps working and the proxy has no public hostname.
   Render `wrangler.deploy.toml` inside `stalwart/`: Wrangler resolves the
   relative `main = "src/index.ts"` against the config file's directory.

4. Add a short section to the root `README.md` / `README-en.md` pointing at
   `stalwart/README.md` and stating that cloud-mail remains primary and the
   Stalwart Worker is optional. Do not duplicate the whole runbook.

5. Validate:

   ```sh
   cd stalwart
   npm ci
   npm run check
   npx wrangler deploy --dry-run --outdir /tmp/stalwart-dist
   sed 's|\${DOMAIN}|example.com|g' wrangler-action.toml > wrangler.deploy.toml
   npx wrangler deploy --dry-run -c wrangler.deploy.toml --outdir /tmp/stalwart-dist
   cd ..
   git diff --stat   # mail-worker/ and mail-vue/ must be absent
   ```

6. Open the PR against `maillab/cloud-mail` `main`. Keep the first PR to the
   package + workflow + docs only. Stalwart delivery inside `mail-worker/`,
   bearer-token settings, and tunnel-triggered flush belong to a later PR.

## Deferred on purpose

These are **not** part of this merge:

- Cloud-mail Stalwart delivery / JMAP import code.
- Bearer-token settings instead of master user/password.
- WebSocket-triggered delivery flush.
- `mail-worker/` and `mail-vue/` changes of any kind.
