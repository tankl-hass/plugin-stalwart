#!/usr/bin/env bash
# Generate a local CA + TLS certificate for a JMAP reverse proxy.
# SANs: localhost, 127.0.0.1, ::1 (local access only).
# Production hostnames such as mail.<DOMAIN> belong to cloud-mail and are NOT included.
# Run from anywhere; all files are written next to this script.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$DIR/local-jmap-cert"
mkdir -p "$OUT"
cd "$OUT"

# 1. Local CA (import ca.crt into the client's trust store)
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
  -keyout ca.key -out ca.crt \
  -subj "/C=US/O=Local JMAP/OU=Dev/CN=Local JMAP Dev CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash"

# 2. Server key + CSR
openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout server.key -out server.csr \
  -subj "/C=US/O=Local JMAP/OU=Dev/CN=localhost"

# 3. Extensions / SANs (local only)
cat > server.ext <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
IP.1=127.0.0.1
IP.2=::1
EOF

# 4. Sign the server certificate with the local CA
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -sha256 -extfile server.ext

# 5. Files the proxy loads
cat server.crt ca.crt > fullchain.crt
cp fullchain.crt cert.pem
cp server.key key.pem
chmod 600 ca.key server.key key.pem

# 6. Verify and report the system-store filename for Android
openssl verify -CAfile ca.crt server.crt
echo "--- SANs ---"
openssl x509 -in server.crt -noout -ext subjectAltName
echo "--- Android system CA filename ---"
printf '%s.0\n' "$(openssl x509 -subject_hash_old -in ca.crt | head -1)"
echo "Files written to $OUT"
