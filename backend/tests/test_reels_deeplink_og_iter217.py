"""iter217 — Reels OG deep-link viral preview tests."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
EMAIL = "bob@japap.com"
PASSWORD = "Test1234!"
SAMPLE_REEL = "reel_b86b57f1c4a8"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={
        "email": EMAIL,
        "password": PASSWORD,
        "captcha_id": "JAPAP_E2E_BYPASS_2026",
        "captcha_answer": "0",
    }, timeout=60)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if isinstance(token, dict):
        token = token.get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def unauth_session():
    s = requests.Session()
    return s


# ============ OG endpoint (public, no auth) ============
class TestOgReelPreview:
    def test_og_reel_valid_returns_200_html_with_all_meta(self, unauth_session):
        r = unauth_session.get(f"{BASE_URL}/api/og/reel/{SAMPLE_REEL}", timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert "text/html" in r.headers.get("content-type", "").lower()
        body = r.text
        required = [
            'property="og:type" content="video.other"',
            'property="og:title"',
            'property="og:description"',
            'property="og:image"',
            'property="og:video"',
            'property="og:video:secure_url"',
            'property="og:video:type" content="video/mp4"',
            'name="twitter:card" content="player"',
            'name="twitter:player:stream"',
        ]
        for tag in required:
            assert tag in body, f"Missing meta tag: {tag}"

    def test_og_reel_unknown_returns_200_fallback(self, unauth_session):
        r = unauth_session.get(f"{BASE_URL}/api/og/reel/reel_DOES_NOT_EXIST_iter217", timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert "JAPAP — Reel introuvable" in r.text

    def test_og_reel_contains_redirect_mechanisms(self, unauth_session):
        r = unauth_session.get(f"{BASE_URL}/api/og/reel/{SAMPLE_REEL}", timeout=30)
        assert r.status_code == 200
        body = r.text
        assert 'http-equiv="refresh"' in body
        assert f"/reels/{SAMPLE_REEL}" in body
        assert "window.location.replace" in body

    def test_og_reel_works_without_auth_header(self, unauth_session):
        """Scrapers never authenticate."""
        assert "Authorization" not in unauth_session.headers
        r = unauth_session.get(f"{BASE_URL}/api/og/reel/{SAMPLE_REEL}", timeout=30)
        assert r.status_code == 200

    def test_og_reel_absolute_https_video_url(self, unauth_session):
        """Twitter player requires an https absolute URL for og:video."""
        r = unauth_session.get(f"{BASE_URL}/api/og/reel/{SAMPLE_REEL}", timeout=30)
        body = r.text
        # Find og:video content
        import re
        m = re.search(r'property="og:video" content="([^"]+)"', body)
        assert m is not None
        url = m.group(1)
        assert url.startswith("https://"), f"og:video not https-absolute: {url}"


# ============ GET /api/feed/reels/{reel_id} (authed single-reel fetch) ============
class TestFeedSingleReel:
    def test_get_single_reel_valid(self, session):
        r = session.get(f"{BASE_URL}/api/feed/reels/{SAMPLE_REEL}", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["reel_id"] == SAMPLE_REEL
        assert "video_url" in data and data["video_url"]
        assert "user" in data
        assert "username" in data["user"]
        assert "shares_count" in data
        assert isinstance(data["shares_count"], int)
        assert "likes_count" in data
        assert "caption" in data

    def test_get_single_reel_unknown_returns_404(self, session):
        r = session.get(f"{BASE_URL}/api/feed/reels/reel_DOES_NOT_EXIST_iter217", timeout=30)
        assert r.status_code == 404
        body = r.json()
        detail = body.get("detail", "")
        assert "introuvable" in detail.lower() or "not found" in detail.lower()

    def test_get_single_reel_without_auth_returns_401(self, unauth_session):
        r = unauth_session.get(f"{BASE_URL}/api/feed/reels/{SAMPLE_REEL}", timeout=30)
        assert r.status_code == 401, f"expected 401, got {r.status_code}"
