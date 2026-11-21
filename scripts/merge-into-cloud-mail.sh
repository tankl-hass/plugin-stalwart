#!/usr/bin/env bash
# Stage this repository as stalwart/ inside a fresh maillab/cloud-mail checkout.
# It only copies files and extends the root .gitignore; workflow and README
# edits are intentionally left for review (see docs/merge.md).
set -euo pipefail

usage() {
  echo "Usage: $0 /path/to/fresh/maillab/cloud-mail" >&2
  exit 1
}

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -eq 1 ]] || usage
[[ -d "$1" ]] || usage
TARGET="$(cd "$1" && pwd)"

if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: $TARGET is not a git checkout" >&2
  exit 1
fi
if [[ ! -d "$TARGET/mail-worker" || ! -d "$TARGET/mail-vue" ]]; then
  echo "error: $TARGET does not look like maillab/cloud-mail" >&2
  exit 1
fi
if [[ -e "$TARGET/stalwart" ]]; then
  echo "error: $TARGET/stalwart already exists; refusing to overwrite" >&2
  exit 1
fi

echo "Source: $SRC"
echo "Target: $TARGET/stalwart"

mapfile -t FILES < <(cd "$SRC" && git ls-files --cached --others --exclude-standard)
COUNT=0
for file in "${FILES[@]}"; do
  case "$file" in
    .git/*|.github/*|docs/*|scripts/*) continue ;;
  esac
  mkdir -p "$TARGET/stalwart/$(dirname "$file")"
  cp "$SRC/$file" "$TARGET/stalwart/$file"
  COUNT=$((COUNT + 1))
done
echo "Copied $COUNT files."

IGNORE="$TARGET/.gitignore"
[[ -f "$IGNORE" ]] || : > "$IGNORE"
for rule in '.dev.vars' '.env' 'coverage/' 'wrangler.deploy.toml'; do
  if ! grep -qxF "$rule" "$IGNORE"; then
    printf '%s\n' "$rule" >> "$IGNORE"
    echo "Added .gitignore rule: $rule"
  fi
done

cat <<'EOF'

Next steps (manual, see docs/merge.md):
  1. git -C <target> status --short     # confirm mail-worker/ and mail-vue/ are clean
  2. Add stalwart/** to on.push.paths in .github/workflows/deploy-cloudflare.yml
  3. Add a separate npm-based deploy job; use STALWART_DOMAIN (plain string),
     not the JSON-array DOMAIN used by cloud-mail.
  4. Add a short link to stalwart/README.md in the root README(s).
  5. cd stalwart && npm ci && npm run check && wrangler deploy --dry-run
EOF
