#!/usr/bin/env python3
"""Local HTTPS reverse proxy for testing Stalwart user clients.

Client --HTTPS--> this proxy --HTTP--> Stalwart HTTP listener.

Generate the local-only certificate with scripts/generate-local-jmap-cert.sh;
see docs/client-setup.md for Sterna/aerc setup. The proxy deliberately ignores
public DNS/hostnames: the only origin it publishes is PUBLIC_ORIGIN
(https://localhost:8443 by default), and the upstream Host is always rewritten
to the configured upstream host.
"""

import json
import os
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

import requests

LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8443"))
# TLS=0 runs plain HTTP. Fine for JMAP bearer-token calls; WebUI sign-in in
# normal mode still needs an HTTPS origin on the Stalwart side.
USE_TLS = os.environ.get("TLS", "1") != "0"
UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
# Override only if Stalwart insists on a particular Host (local tests normally
# don't need this). The proxy always rewrites the client's Host to this value.
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "")
PUBLIC_ORIGIN = os.environ.get(
    "PUBLIC_ORIGIN",
    "https://localhost:8443" if USE_TLS else f"http://localhost:{LISTEN_PORT}",
).rstrip("/")
DISCOVERY_ORIGIN = os.environ.get("DISCOVERY_ORIGIN", "").rstrip("/")
CERT = os.environ.get("CERT", os.path.join(os.path.dirname(__file__), "cert.pem"))
KEY = os.environ.get("KEY", os.path.join(os.path.dirname(__file__), "key.pem"))
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "30"))

# Reuse upstream TCP connections per handler thread. The SSH forward adds
# noticeable latency per connection, so keep-alive makes the browser much
# faster than opening a new forward for every JS/CSS request.
_thread = threading.local()


def upstream_session():
    session = getattr(_thread, "session", None)
    if session is None:
        session = requests.Session()
        _thread.session = session
    return session

UPSTREAM_URL = urlsplit(UPSTREAM)
UPSTREAM_ORIGIN = f"{UPSTREAM_URL.scheme}://{UPSTREAM_URL.netloc}"
PUBLIC_URL = urlsplit(PUBLIC_ORIGIN)
# Stalwart may publish http://localhost:<port> discovery URLs when its public
# URL was set with the wrong scheme. Rewrite that to the HTTPS origin we serve.
PUBLIC_HTTP_ORIGIN = urlunsplit(("http", PUBLIC_URL.netloc, "", "", ""))

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


def rewrite(value):
    """Replace known upstream origins with the local public origin."""
    out = value
    seen = set()
    for source in (UPSTREAM_ORIGIN, DISCOVERY_ORIGIN, PUBLIC_HTTP_ORIGIN):
        if not source or source in seen or source == PUBLIC_ORIGIN:
            continue
        seen.add(source)
        out = out.replace(source, PUBLIC_ORIGIN)
    return out


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LocalJmapProxy/1.0"

    def setup(self):
        # Do the TLS handshake in the handler *thread*, not in the accept loop.
        # Wrapping the listening socket makes one client that connects without
        # speaking TLS block every other connection (including /health).
        ctx = getattr(self.server, "ssl_context", None)
        if ctx is not None:
            try:
                self.request = ctx.wrap_socket(self.request, server_side=True)
            except (ssl.SSLError, OSError):
                self.request.close()
                raise
        super().setup()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body=b"", headers=None, content_type=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        if content_type:
            self.send_header("content-type", content_type)
        for key, value in (headers or {}).items():
            if key.lower() not in ("content-type", "content-length"):
                self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "authorization, content-type")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def _relay(self):
        if self.path == "/health":
            return self._send(200, "ok", content_type="text/plain; charset=utf-8")

        headers = {}
        connection_tokens = {
            token.strip().lower()
            for token in self.headers.get("connection", "").split(",")
            if token.strip()
        }
        for key, value in self.headers.items():
            low = key.lower()
            if low in HOP_BY_HOP or low in connection_tokens:
                continue
            if low == "host" or low.startswith("x-forwarded-") or low.startswith("cf-"):
                continue
            headers[key] = value
        headers["Host"] = UPSTREAM_HOST or UPSTREAM_URL.netloc
        headers["X-Forwarded-Host"] = self.headers.get("host", f"localhost:{LISTEN_PORT}")
        headers["X-Forwarded-Proto"] = "https" if USE_TLS else "http"
        headers["X-Forwarded-Port"] = str(LISTEN_PORT)
        # No X-Forwarded-For: Stalwart rejects loopback values when
        # Http.clientIpSource is xForwardedFor, and local clients are loopback.

        length = self.headers.get("content-length")
        body = self.rfile.read(int(length)) if length else None

        target = UPSTREAM + self.path
        try:
            upstream = upstream_session().request(
                self.command, target, headers=headers, data=body,
                timeout=TIMEOUT, allow_redirects=False,
            )
        except requests.RequestException as exc:
            detail = f"Upstream request failed: {exc}"
            self.log_error(detail)
            return self._send(
                502, json.dumps({"error": detail}),
                content_type="application/json; charset=utf-8",
            )

        resp_headers = {}
        for key, value in upstream.headers.items():
            low = key.lower()
            if low in HOP_BY_HOP or low == "content-length":
                continue
            if low == "location":
                value = rewrite(value)
            resp_headers[key] = value

        content = upstream.content
        path = urlsplit(self.path).path
        ctype = upstream.headers.get("content-type", "")
        if path.startswith("/.well-known/") or path in ("/jmap/session", "/jmap/"):
            if "json" in ctype or "xml" in ctype:
                content = rewrite(content.decode("utf-8", "replace")).encode()

        self._send(upstream.status_code, content, resp_headers, ctype or None)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _relay


class ProxyServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def main():
    server = ProxyServer((LISTEN_HOST, LISTEN_PORT), Proxy)
    scheme = "https" if USE_TLS else "http"
    if USE_TLS:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERT, KEY)
        server.ssl_context = context
    print(
        f"Local JMAP proxy: {scheme}://{LISTEN_HOST}:{LISTEN_PORT} "
        f"-> {UPSTREAM} (public origin {PUBLIC_ORIGIN})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
