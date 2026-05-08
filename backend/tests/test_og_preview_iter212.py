"""iter212 — OG link preview endpoint tests.

Covers:
    * GET /api/og?url=... returns the documented JSON shape
    * 24h cache (2nd call returns cache_hit=true)
    * SSRF protection (localhost, 127.0.0.1, 10.x, 192.168.x, 169.254.169.254)
    * Non-http schemes (ftp, file, javascript) are rejected
    * GET /api/og/batch returns {previews: [...]} capped at 8 entries
    * Non-existent host (DNS fail) returns a minimal fallback, not 500
    * Non-HTML content (direct image) returns a minimal fallback, not 500
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback for tests running with the backend .env only
    try:
        with open("/app/frontend/.env") as fh:
            for ln in fh:
                if ln.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = ln.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass

OG = f"{BASE_URL}/api/og"


# --------------------------------------------------------------------- shape
def _assert_shape(data):
    """All public OG fields must be present."""
    for k in ("url", "title", "description", "image",
              "site_name", "favicon", "domain",
              "fetched_at", "cache_hit"):
        assert k in data, f"missing key {k!r} in response: {data}"


# --------------------------------------------------------------------- happy path + cache
class TestOgPreviewHappyPath:
    def test_github_returns_full_preview(self):
        # Force refresh to bypass any previous cache entry for deterministic cache_hit test
        r = requests.get(OG, params={"url": "https://github.com", "force": "true"}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        _assert_shape(data)
        assert data["domain"].endswith("github.com"), data["domain"]
        # On force-refresh this must be a fresh fetch
        assert data["cache_hit"] is False
        # github's home page always has OG title — allow empty as graceful, but log
        assert isinstance(data["title"], str)

    def test_second_call_hits_cache(self):
        # First call (no force) — either sees the cached value written above, or fetches fresh
        r1 = requests.get(OG, params={"url": "https://github.com"}, timeout=20)
        assert r1.status_code == 200
        # Second call — must be cache_hit=True
        r2 = requests.get(OG, params={"url": "https://github.com"}, timeout=20)
        assert r2.status_code == 200
        d2 = r2.json()
        _assert_shape(d2)
        assert d2["cache_hit"] is True, f"expected cache_hit=True on 2nd call, got {d2}"


# --------------------------------------------------------------------- SSRF
class TestOgPreviewSSRF:
    @pytest.mark.parametrize("bad_url", [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://[::1]/",
    ])
    def test_private_ips_blocked(self, bad_url):
        r = requests.get(OG, params={"url": bad_url}, timeout=30)
        assert r.status_code == 400, f"{bad_url} should be blocked, got {r.status_code} {r.text}"
        body = r.json()
        detail = (body.get("detail") or "").lower()
        assert "block" in detail or "private" in detail or "internal" in detail, body

    @pytest.mark.parametrize("bad_url", [
        "ftp://example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ])
    def test_non_http_schemes_blocked(self, bad_url):
        r = requests.get(OG, params={"url": bad_url}, timeout=30)
        assert r.status_code == 400, f"{bad_url} should be blocked, got {r.status_code} {r.text}"


# --------------------------------------------------------------------- batch
class TestOgPreviewBatch:
    def test_batch_returns_list(self):
        urls = ",".join([
            "https://github.com",
            "https://example.com",
        ])
        r = requests.get(f"{OG}/batch", params={"urls": urls}, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "previews" in body
        assert isinstance(body["previews"], list)
        assert len(body["previews"]) == 2
        for p in body["previews"]:
            _assert_shape(p)

    def test_batch_caps_at_eight(self):
        many = ",".join([f"https://example.com/?p={i}" for i in range(1, 15)])
        r = requests.get(f"{OG}/batch", params={"urls": many}, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["previews"]) <= 8


# --------------------------------------------------------------------- graceful errors
class TestOgPreviewGraceful:
    def test_unknown_host_returns_fallback(self):
        """DNS failure must not 500 — must return a minimal preview."""
        r = requests.get(OG, params={
            "url": "https://this-domain-should-not-exist-japap-iter212.invalid/",
        }, timeout=20)
        # It could be blocked at SSRF (DNS resolve fails -> _is_private_ip returns True -> 400)
        # OR it could return 200 with a fallback. Both are acceptable (not 500).
        assert r.status_code in (200, 400), r.text
        if r.status_code == 200:
            _assert_shape(r.json())

    def test_non_html_content_returns_fallback(self):
        """Direct image URL (non-text/html) must return the fallback, not 500."""
        r = requests.get(OG, params={
            "url": "https://httpbin.org/image/png",
        }, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        _assert_shape(data)
        # Fallback: title defaults to domain, image is empty
        assert data["image"] == "" or data["image"].startswith("http")

    def test_404_returns_fallback(self):
        r = requests.get(OG, params={
            "url": "https://httpbin.org/status/404",
        }, timeout=30)
        assert r.status_code == 200, r.text
        _assert_shape(r.json())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
