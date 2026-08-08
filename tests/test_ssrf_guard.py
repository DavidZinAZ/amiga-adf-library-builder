"""Offline unit tests for the SSRF / private-range URL guard.

These tests must NEVER perform a real network call. ``guard_url`` is unit-tested
directly with literals and with ``resolve=False`` (no DNS). The integration tests
use a fake ``opener`` so the production code path is exercised up to the fetch
boundary without leaving the host.

No internal host paths, no internal hostnames, no private IP literals in this file.
"""

from __future__ import annotations

import pytest

from amiga_adf_library_builder.metadata import UnsafeUrlError, guard_url


# --------------------------------------------------------------------------- #
# guard_url — accept public literals / hostnames                               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    "https://en.wikipedia.org/w/api.php?format=json",
    "http://example.com/foo",
    "https://93.184.216.34/path",          # public IPv4 literal, allowed
    "https://[2606:2800:220:1:248:1893:25c8:1946]/",  # public IPv6 literal
    "https://public.example.net/cover.jpg",
])
def test_guard_url_accepts_public(url):
    # resolve=False: no DNS; public literals/hostnames must pass.
    guard_url(url, resolve=False)


@pytest.mark.parametrize("url,label", [
    ("http://127.0.0.1/secret", "loopback v4"),
    ("https://127.0.0.53/dns", "loopback v4 (resolver)"),
    ("http://10.0.0.5/internal", "RFC1918 10/8"),
    ("http://172.16.5.5/internal", "RFC1918 172.16/12"),
    ("http://172.31.255.255/internal", "RFC1918 172.31/12 upper"),
    ("http://192.168.1.1/router", "RFC1918 192.168/16"),
    ("http://169.254.169.254/latest/meta-data", "cloud link-local"),
    ("http://[::1]/loopback6", "IPv6 loopback"),
    ("http://[fe80::1]/linklocal", "IPv6 link-local"),
    ("http://[fc00::1]/ula", "IPv6 ULA"),
    ("http://[fd12:3456::1]/ula2", "IPv6 ULA (fd00::/8)"),
    ("http://[::ffff:127.0.0.1]/mapped", "IPv4-mapped IPv6 loopback"),
    ("http://[::ffff:10.0.0.1]/mapped-rfc1918", "IPv4-mapped IPv6 RFC1918"),
])
def test_guard_url_rejects_private_literals(url, label):
    with pytest.raises(UnsafeUrlError):
        guard_url(url, resolve=False)


# --------------------------------------------------------------------------- #
# guard_url — malformed / non-http schemes                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    "ftp://127.0.0.1/x",
    "file:///etc/passwd",
    "gopher://10.0.0.1/x",
    "javascript:alert(1)",
    "http:///no-host",
    "not-a-url",
])
def test_guard_url_rejects_bad_scheme_or_host(url):
    with pytest.raises(UnsafeUrlError):
        guard_url(url, resolve=False)


# --------------------------------------------------------------------------- #
# guard_url — DNS resolution path (only when resolve=True)                     #
# --------------------------------------------------------------------------- #

def test_guard_url_resolve_true_accepts_public_hostname(monkeypatch):
    import amiga_adf_library_builder.metadata as md

    def fake_getaddrinfo(host, port, *a, **k):
        # socket.getaddrinfo returns a list of 5-tuples:
        # (family, type, proto, canonname, sockaddr); sockaddr[0] is the IP.
        return [(md.socket.AF_INET, md.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    guard_url("https://api.rawg.io/games", resolve=True)


# Regression: a hostname may resolve to several addresses. The guard must
# inspect EVERY address and reject the URL if ANY resolved address is private,
# even when the first resolved address is public. This closes the bypass where
# only infos[0] was previously checked.
@pytest.mark.parametrize("addresses", [
    [("93.184.216.34", 0), ("10.0.0.9", 0)],          # public first, RFC1918 second
    [("2606:2800:220:1:248:1893:25c8:1946", 0), ("127.0.0.1", 0)],  # public v6 first, loopback second
    [("93.184.216.34", 0), ("192.168.1.1", 0)],       # public first, RFC1918 192.168 second
    [("198.51.100.10", 0), ("::1", 0)],               # public first, IPv6 loopback second
])
def test_guard_url_resolve_true_rejects_any_private_address(monkeypatch, addresses):
    import amiga_adf_library_builder.metadata as md

    def fake_getaddrinfo(host, port, *a, **k):
        return [
            (md.socket.AF_INET, md.socket.SOCK_STREAM, 6, "", addr)
            if ":" not in addr[0] else
            (md.socket.AF_INET6, md.socket.SOCK_STREAM, 6, "", addr)
            for addr in addresses
        ]
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        guard_url("https://multi.example/cover.jpg", resolve=True)


# Positive counterpart: all resolved addresses public (including dual-stack)
# must pass.
def test_guard_url_resolve_true_accepts_all_public_addresses(monkeypatch):
    import amiga_adf_library_builder.metadata as md

    def fake_getaddrinfo(host, port, *a, **k):
        return [
            (md.socket.AF_INET, md.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (md.socket.AF_INET6, md.socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 0)),
        ]
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    guard_url("https://dualstack.example/cover.jpg", resolve=True)


def test_guard_url_resolve_true_rejects_private_first_address(monkeypatch):
    import amiga_adf_library_builder.metadata as md

    def fake_getaddrinfo(host, port, *a, **k):
        return [(md.socket.AF_INET, md.socket.SOCK_STREAM, 6, "", ("10.0.0.9", 0))]
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        guard_url("https://internal.example/cover.jpg", resolve=True)


def test_guard_url_resolve_true_rejects_localhost(monkeypatch):
    import amiga_adf_library_builder.metadata as md

    # When DNS resolution is enabled, "localhost" resolving to loopback must be
    # rejected even though it is not an IP literal.
    def fake_getaddrinfo(host, port, *a, **k):
        return [(md.socket.AF_INET, md.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        guard_url("http://localhost/secret", resolve=True)


def test_guard_url_resolve_true_raises_on_resolution_failure(monkeypatch):
    import amiga_adf_library_builder.metadata as md

    def fake_getaddrinfo(host, port, *a, **k):
        raise OSError("no route to host")
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        guard_url("https://does-not-exist.example/cover.jpg", resolve=True)


# Offline: resolve=False leaves hostnames untouched (no DNS lookup happens).
def test_guard_url_resolve_false_skips_hostname(monkeypatch):
    import amiga_adf_library_builder.metadata as md

    called = {"dns": False}

    def fake_getaddrinfo(host, port, *a, **k):
        called["dns"] = True
        raise AssertionError("getaddrinfo must not be called when resolve=False")
    monkeypatch.setattr(md.socket, "getaddrinfo", fake_getaddrinfo)
    # Should pass without touching DNS.
    guard_url("https://en.wikipedia.org/w/api.php", resolve=False)
    assert called["dns"] is False


# --------------------------------------------------------------------------- #
# Integration: guard blocks the real fetch boundary without network            #
# --------------------------------------------------------------------------- #

class _FakeOpener:
    """Records whether it was called. Must NOT be called in the rejection cases."""

    def __init__(self):
        self.called = False

    def __call__(self, request, timeout=0):
        self.called = True
        raise AssertionError("fake opener must not be reached when guard rejects")


def test_json_get_blocked_before_fetch():
    from amiga_adf_library_builder.metadata import _json_get

    opener = _FakeOpener()
    with pytest.raises(UnsafeUrlError):
        _json_get("http://169.254.169.254/latest/meta-data", opener=opener)
    assert opener.called is False


def test_text_get_blocked_before_fetch():
    from amiga_adf_library_builder.metadata import _text_get

    opener = _FakeOpener()
    with pytest.raises(UnsafeUrlError):
        _text_get("http://127.0.0.1/secret", opener=opener)
    assert opener.called is False


def test_download_artwork_blocked_before_fetch():
    from amiga_adf_library_builder.enrich import _download_artwork
    from amiga_adf_library_builder.metadata import MetadataRecord, UnsafeUrlError

    record = MetadataRecord(canonical_title="Test Game", artwork_url="http://10.0.0.5/internal.jpg")
    # No network call must occur (guard runs before urllib.request.urlopen).
    with pytest.raises(UnsafeUrlError):
        _download_artwork(record, "/tmp/should-not-be-used", "Test Game", timeout=1)
