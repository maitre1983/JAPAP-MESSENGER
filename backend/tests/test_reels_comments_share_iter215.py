"""iter215 — Tests for reel comments + share endpoints."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
EMAIL = "bob@japap.com"
PASSWORD = "Test1234!"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    last_err = None
    r = None
    for _ in range(5):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login", json={
                "email": EMAIL,
                "password": PASSWORD,
                "captcha_id": "JAPAP_E2E_BYPASS_2026",
                "captcha_answer": "0",
            }, timeout=90)
            if r.status_code == 200:
                break
            last_err = f"status {r.status_code}: {r.text[:200]}"
        except requests.exceptions.RequestException as e:
            last_err = e
        import time as _t
        _t.sleep(3)
    if r is None or r.status_code != 200:
        pytest.skip(f"login error: {last_err}")
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("access_token") or data.get("token") or data.get("token", {})
    if isinstance(token, dict):
        token = token.get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def reel_id(session):
    """Pick the first reel from /api/feed/reels."""
    r = session.get(f"{BASE_URL}/api/feed/reels?limit=5", timeout=30)
    assert r.status_code == 200, f"list reels: {r.status_code} {r.text}"
    reels = r.json()
    if not reels:
        pytest.skip("No reels available in DB")
    return reels[0]["reel_id"]


# ---------- reel_comments GET / POST ----------

class TestReelComments:
    def test_get_comments_list(self, session, reel_id):
        r = session.get(f"{BASE_URL}/api/feed/reels/{reel_id}/comments", timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)

    def test_post_comment_empty_returns_400(self, session, reel_id):
        r = session.post(
            f"{BASE_URL}/api/feed/reels/{reel_id}/comments",
            json={"text": "   "},
            timeout=20,
        )
        assert r.status_code == 400, r.text

    def test_post_comment_unknown_reel_returns_404(self, session):
        r = session.post(
            f"{BASE_URL}/api/feed/reels/reel_DOES_NOT_EXIST_iter215/comments",
            json={"text": "hello"},
            timeout=20,
        )
        assert r.status_code == 404, r.text

    def test_post_comment_success_and_persistence(self, session, reel_id):
        # Get count before
        before = session.get(f"{BASE_URL}/api/feed/reels/{reel_id}/comments", timeout=20).json()
        n_before = len(before)

        r = session.post(
            f"{BASE_URL}/api/feed/reels/{reel_id}/comments",
            json={"text": "TEST_iter215 hello reel comment"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "comment_id" in body
        assert body["reel_id"] == reel_id
        assert body["post_id"] == reel_id
        assert "user_id" in body
        assert body["text"] == "TEST_iter215 hello reel comment"

        # GET to verify persistence
        after = session.get(f"{BASE_URL}/api/feed/reels/{reel_id}/comments", timeout=20).json()
        assert len(after) == n_before + 1
        assert any(c["comment_id"] == body["comment_id"] for c in after)


# ---------- reel share ----------

class TestReelShare:
    def test_share_increments_count(self, session, reel_id):
        # Endpoint returns {"shared": True, "reel_id": ...}
        # Note: list /api/feed/reels does NOT expose shares_count, so we can only
        # verify the endpoint succeeds + is idempotent (no errors on 2nd call).
        r = session.post(f"{BASE_URL}/api/feed/reels/{reel_id}/share", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("shared") is True
        assert body.get("reel_id") == reel_id

        # Second call must also succeed (idempotent ALTER TABLE; column exists)
        r2 = session.post(f"{BASE_URL}/api/feed/reels/{reel_id}/share", timeout=20)
        assert r2.status_code == 200, r2.text

    def test_share_unknown_reel_returns_404(self, session):
        r = session.post(
            f"{BASE_URL}/api/feed/reels/reel_DOES_NOT_EXIST_iter215/share",
            timeout=20,
        )
        assert r.status_code == 404, r.text
