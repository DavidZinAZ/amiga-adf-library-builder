"""Regression tests for the REAL Hasheous fetch path (issue #12).

These exercise the actual outbound fetch plumbing in ``hasheous.py`` that the
synthetic ``resolve=False`` + injected-opener harness does NOT cover:

* Defect 1 (HIGH): SSRF pivot via redirect following. The hardened opener must
  NOT follow 3xx; a redirect to a private/loopback/link-local host must never
  be fetched on a second hop.
* Defect 2 (MEDIUM): response-size bound enforced only AFTER a full read. The
  real opener must stream and abort BEFORE the whole body buffers.
* Defect 3 (MEDIUM): ambient proxy honored by default opener. The hardened
  opener must IGNORE ``HTTP_PROXY``/``HTTPS_PROXY``.

Strategy: NO external network is touched. We run real localhost HTTP stubs and
patch ``socket.getaddrinfo`` so a SENTINEL hostname resolves to a public-looking
address (so ``metadata.guard_url(resolve=True)`` PASSES on the original URL),
then patch ``socket.socket`` to translate that address back to loopback and to
RAISE on any other non-loopback connect. This proves the real fetch path runs
while guaranteeing zero external egress.

The harness is identical to ``tests/test_playmatch_real_fetch.py`` except the
provider under test is swapped to ``HasheousProvider``.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.request
from pathlib import Path

import pytest

from amiga_adf_library_builder import hasheous as hs

# Sentinel hostname that the test DNS map resolves to a public IP so the SSRF
# guard (which refuses 127/10/172.16/192.168/169.254) lets the ORIGINAL url
# through, while the patched socket keeps the bytes on loopback.
SENTINEL_HOST = "realhasheous.test"
# Public-looking (TEST-NET-3, documented non-routable) IP used as the fake
# resolve result for the sentinel host.
_SENTINEL_IP = "203.0.113.9"
# A different fake proxy IP (not translated to loopback) used to prove the
# ambient proxy is ignored.
_PROXY_IP = "203.0.113.50"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Configurable stub: subclass sets ``body`` / ``status`` / ``location``."""

    body = b'{"found": false}'
    status = 200
    location = None
    # Per-server counters populated by the test.
    requests = 0
    bytes_written = 0

    def do_GET(self):  # noqa: N802 (stdlib casing)
        type(self).requests += 1
        # Honor a configured redirect without following it (the client must not).
        if self.location is not None:
            self.send_response(302)
            self.send_header("Location", self.location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = self.body
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        # Write in chunks so an early client abort (Defect 2) surfaces as a
        # broken pipe rather than a full buffered write.
        written = 0
        view = memoryview(payload)
        try:
            for start in range(0, len(payload), 4096):
                chunk = bytes(view[start:start + 4096])
                self.wfile.write(chunk)
                self.wfile.flush()
                written += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            type(self).bytes_written += written

    def log_message(self, format, *args):  # silence stderr noise
        return


def _make_server(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture
def real_fetch_env(monkeypatch):
    """Spin up real localhost stubs and sandbox egress to loopback only.

    Returns ``(server_a, server_b, port_a)``. ``server_a`` is the primary
    endpoint reached via the SENTINEL hostname; ``server_b`` is the
    'never-fetched' target for redirect/SSRF assertions.
    """

    class _HandlerA(_Handler):
        pass

    class _HandlerB(_Handler):
        pass

    server_a = _make_server(_HandlerA)
    server_b = _make_server(_HandlerB)
    port_a = server_a.server_address[1]
    port_b = server_b.server_address[1]

    connect_targets: list[tuple] = []

    class _EgressSocket(socket.socket):
        def connect(self, address):
            host = address[0]
            port = address[1]
            connect_targets.append((host, port))
            if host == _SENTINEL_IP:
                # Translate the fake public IP back to loopback.
                host = "127.0.0.1"
            if host == "127.0.0.1" or host.startswith("127.") or host == "::1":
                return super().connect((host, port))
            raise OSError(f"egress blocked in test: would connect to {host}:{port}")

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        if host == SENTINEL_HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_SENTINEL_IP, port or 80))]
        if host in ("203.0.113.50", "realproxy.test"):
            # The fake proxy host resolves somewhere that is NOT loopback.
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PROXY_IP, port or 3128))]
        # Any other host would be a real DNS lookup -> must not happen offline.
        raise OSError(f"unexpected DNS resolution in offline test: {host!r}")

    monkeypatch.setattr(socket, "socket", _EgressSocket)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setenv("NO_PROXY", "*")

    yield {
        "server_a": server_a,
        "server_b": server_b,
        "handler_a": _HandlerA,
        "handler_b": _HandlerB,
        "port_a": port_a,
        "port_b": port_b,
        "connect_targets": connect_targets,
    }

    server_a.shutdown()
    server_b.shutdown()


def _real_provider(base_url: str, cache: Path):
    """Build a provider that uses the REAL default opener (resolve=True)."""
    cfg = hs.HasheousConfig.from_dict({"enabled": True, "base_url": base_url})
    assert cfg.enabled is True
    prov = hs.HasheousProvider(cfg, cache)  # no opener -> real fetch, resolve=True
    prov.discover()
    return prov


# --- Defect 3 (structural): hardened opener construction ---------------------

def test_opener_has_no_redirect_handler_and_clears_proxy():
    """The hardened opener must NOT auto-follow redirects."""
    op = hs._build_no_redirect_opener(timeout=1.0)
    instances = []
    for registry in op.__dict__.values():
        if isinstance(registry, list):
            instances.extend(registry)
        elif isinstance(registry, dict):
            for value in registry.values():
                if isinstance(value, list):
                    instances.extend(value)
                else:
                    instances.append(value)
    type_names = [type(h).__name__ for h in instances]
    assert any(h == "_NoRedirectHandler" for h in type_names)
    assert "HTTPRedirectHandler" not in type_names


# --- Defect 1 (HIGH): SSRF pivot via redirect -------------------------------

def test_real_fetch_no_redirect_to_private_host(real_fetch_env, tmp_path):
    """A 302 to a link-local/loopback host must never be fetched (second hop)."""
    env = real_fetch_env
    env["handler_a"].location = f"http://169.254.169.254:{env['port_b']}/latest/meta-data/"
    env["handler_a"].status = 302

    base_url = f"http://{SENTINEL_HOST}:{env['port_a']}/v1"
    group = _group("Redirect Game", sha256="d" * 64)
    prov = _real_provider(base_url, tmp_path / "cache")
    res = prov.resolve(group, sha256="d" * 64)

    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    # CRITICAL: the redirect target (server B) was NEVER fetched.
    assert env["handler_b"].requests == 0
    # And the only connect target was our translated loopback sentinel.
    assert all(t[0] == _SENTINEL_IP for t in env["connect_targets"])


def test_real_fetch_redirect_to_loopback_never_followed(real_fetch_env, tmp_path):
    """Same pivot guard when the redirect points at a different loopback port."""
    env = real_fetch_env
    env["handler_a"].location = f"http://127.0.0.1:{env['port_b']}/secret"
    env["handler_a"].status = 302

    base_url = f"http://{SENTINEL_HOST}:{env['port_a']}/v1"
    group = _group("Loopback Redirect", sha256="e" * 64)
    prov = _real_provider(base_url, tmp_path / "cache")
    res = prov.resolve(group, sha256="e" * 64)

    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    assert env["handler_b"].requests == 0


# --- Defect 2 (MEDIUM): response-size bound before full read (DoS) ---------

def test_real_fetch_oversize_aborts_before_full_body(real_fetch_env, tmp_path):
    """A body larger than max_response_bytes must abort early, not buffer GBs."""
    env = real_fetch_env
    total = 2_000_000
    env["handler_a"].body = b'{"found": true, "provider_id": "HS-BIG"}' + b"x" * (total - 40)
    env["handler_a"].status = 200
    env["handler_a"].bytes_written = 0

    cfg = hs.HasheousConfig.from_dict(
        {"enabled": True, "base_url": f"http://{SENTINEL_HOST}:{env['port_a']}/v1",
         "max_response_bytes": 1000}
    )
    prov = hs.HasheousProvider(cfg, tmp_path / "cache")
    prov.discover()
    group = _group("Oversize Game", sha256="f" * 64)
    res = prov.resolve(group, sha256="f" * 64)

    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    # The server must NOT have written the full body: the client aborted early.
    assert env["handler_a"].bytes_written < total


def test_real_fetch_success_within_bound(real_fetch_env, tmp_path):
    """A normal within-bound JSON response still resolves correctly."""
    env = real_fetch_env
    env["handler_a"].body = (
        b'{"found": true, "provider_id": "HS-REAL", "title": "Real Game"}'
    )
    env["handler_a"].status = 200

    base_url = f"http://{SENTINEL_HOST}:{env['port_a']}/v1"
    group = _group("Real Game", sha256="a" * 64)
    prov = _real_provider(base_url, tmp_path / "cache")
    res = prov.resolve(group, sha256="a" * 64)

    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert res.provider_id == "HS-REAL"


# --- Defect 3 (MEDIUM): ambient proxy ignored -------------------------------

def test_real_fetch_ignores_ambient_proxy(real_fetch_env, monkeypatch, tmp_path):
    """HTTP_PROXY/HTTPS_PROXY must not route the Hasheous request."""
    env = real_fetch_env
    env["handler_a"].body = b'{"found": true, "provider_id": "HS-NOPROXY"}'
    env["handler_a"].status = 200

    monkeypatch.setenv("HTTP_PROXY", f"http://{_PROXY_IP}:3128")
    monkeypatch.setenv("HTTPS_PROXY", f"http://{_PROXY_IP}:3128")

    base_url = f"http://{SENTINEL_HOST}:{env['port_a']}/v1"
    group = _group("Proxy Game", sha256="b" * 64)
    prov = _real_provider(base_url, tmp_path / "cache")
    res = prov.resolve(group, sha256="b" * 64)

    assert res.found is True
    assert res.provider_id == "HS-NOPROXY"
    # The fake proxy IP must never have been a connect target.
    assert all(t[0] != _PROXY_IP for t in env["connect_targets"])


# --- helpers ----------------------------------------------------------------

def _group(title: str, *, sha256: str):
    from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup

    rec = ParsedRecord(source_filename=f"{title}.adf", ext="adf", title=title)
    group = ReleaseGroup(
        release_key=title.lower(),
        title=title,
        edition=None,
        group=None,
        chipset=None,
        language=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=[rec],
        disks=[rec],
    )
    group.sha256 = sha256  # type: ignore[attr-defined]
    return group
