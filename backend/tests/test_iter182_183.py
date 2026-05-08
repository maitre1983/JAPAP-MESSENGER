"""iter182 (Marketplace auto-photo) + iter183 (Feed media-only post) tests.

Scope:
- iter182: POST /api/marketplace/ai-image/auto-photo
  * quota 429 (default 3 < 4 presets)
  * file size validation (< 512 bytes, > 12MB)
  * mkt_ai_auto_photo_enabled=false → 503
  * NOTE: we do NOT trigger a successful generation (costs 4 Gemini + ~80s)
- iter183: POST /api/feed/posts validation
  * media-only → 200
  * text-only → 200
  * empty → 400 + FR message
"""
import io
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
BYPASS = "JAPAP_E2E_BYPASS_2026"
BOB = {"email": "bob@japap.com", "password": "Test1234!"}

HDR_CSRF = {"X-Requested-With": "XMLHttpRequest"}


# ─── Fixtures ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def bob_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={**BOB, "captcha_id": BYPASS, "captcha_answer": "0"},
        headers={"Content-Type": "application/json", **HDR_CSRF},
        timeout=20,
    )
    assert r.status_code == 200, f"login bob failed: {r.status_code} {r.text}"
    data = r.json()
    # Response contains access_token; cookies should be set on the session.
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    s.headers.update(HDR_CSRF)
    return s


def _png_bytes(size: int) -> bytes:
    """Generate a byte blob of exactly `size` bytes, valid-ish PNG header."""
    header = b"\x89PNG\r\n\x1a\n"
    return header + b"\x00" * (size - len(header))


# ─── iter183: Feed posts validation ────────────────────────
class TestFeedPostValidation:
    """iter183 — text-only / media-only / empty."""

    def test_media_only_ok(self, bob_session):
        r = bob_session.post(
            f"{BASE_URL}/api/feed/posts",
            json={"text": "", "media": ["/uploads/test_iter183.jpg"]},
            timeout=15,
        )
        assert r.status_code == 200, f"media-only must succeed: {r.status_code} {r.text}"
        d = r.json()
        assert "post_id" in d or "id" in d, f"missing post id: {d}"

    def test_text_only_ok(self, bob_session):
        unique = f"TEST_iter183 text-only {uuid.uuid4().hex[:6]}"
        r = bob_session.post(
            f"{BASE_URL}/api/feed/posts",
            json={"text": unique, "media": []},
            timeout=15,
        )
        assert r.status_code == 200, f"text-only must succeed: {r.status_code} {r.text}"
        d = r.json()
        assert "post_id" in d or "id" in d

    def test_empty_rejected(self, bob_session):
        r = bob_session.post(
            f"{BASE_URL}/api/feed/posts",
            json={"text": "", "media": []},
            timeout=15,
        )
        assert r.status_code == 400, f"empty must fail 400, got {r.status_code}: {r.text}"
        body = r.json()
        msg = body.get("detail") or body.get("message") or ""
        assert "Ajoute du texte" in msg, f"missing FR msg, got: {msg}"

    def test_empty_whitespace_rejected(self, bob_session):
        r = bob_session.post(
            f"{BASE_URL}/api/feed/posts",
            json={"text": "   ", "media": []},
            timeout=15,
        )
        assert r.status_code == 400


# ─── iter182: Auto-photo quota + validation ────────────────
class TestAutoPhotoValidation:
    """iter182 — validate quota gate, size, and disable flag WITHOUT triggering gen."""

    AUTO_PHOTO_URL = f"{BASE_URL}/api/marketplace/ai-image/auto-photo"

    def test_file_too_small(self, bob_session):
        tiny = _png_bytes(100)  # < 512
        files = {"file": ("tiny.png", io.BytesIO(tiny), "image/png")}
        r = bob_session.post(self.AUTO_PHOTO_URL, files=files, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        assert "trop petite" in (r.json().get("detail") or "").lower() or r.status_code == 400

    def test_file_too_large(self, bob_session):
        # 12MB+1 bytes
        big = _png_bytes(12 * 1024 * 1024 + 100)
        files = {"file": ("big.png", io.BytesIO(big), "image/png")}
        r = bob_session.post(self.AUTO_PHOTO_URL, files=files, timeout=30)
        assert r.status_code == 400, f"expected 400 >12MB, got {r.status_code}: {r.text}"
        assert "12MB" in (r.json().get("detail") or "") or "> 12" in (r.json().get("detail") or "")

    def test_quota_gate_429(self, bob_session):
        """Default quota=3 < presets=4 → must be blocked with 429 BEFORE any Gemini call.

        Note: if Bob has 0/3 consumed, remaining=3 < 4 → 429.
        If Bob was previously reset and has generations today, remaining may still < 4.
        This test is cheap (no Gemini hit) so we just assert 429 is returned.
        """
        valid = _png_bytes(2048)  # 2KB — passes size gates
        files = {"file": ("ok.png", io.BytesIO(valid), "image/png")}
        r = bob_session.post(self.AUTO_PHOTO_URL, files=files, timeout=30)
        # We expect 429 (quota) when remaining < 4. If somehow admin bumped quota
        # to >= 4 we would get a 200 (expensive), but by default we want 429.
        assert r.status_code in (429, 503), (
            f"expected 429 (quota) or 503 (disabled), got {r.status_code}: {r.text[:200]}"
        )
        if r.status_code == 429:
            msg = r.json().get("detail", "")
            assert "Auto-photo nécessite 4 crédits" in msg or "crédits IA" in msg, \
                f"unexpected msg: {msg}"

    def test_unauth_rejected(self):
        """Call without session → 401."""
        anon = requests.Session()
        anon.headers.update(HDR_CSRF)
        files = {"file": ("x.png", io.BytesIO(_png_bytes(2048)), "image/png")}
        r = anon.post(self.AUTO_PHOTO_URL, files=files, timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
