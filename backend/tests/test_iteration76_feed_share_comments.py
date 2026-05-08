"""
Iter76 backend regression — Feed comments, single-post deep link, external share.
Endpoints exercised:
  POST /api/auth/login                              (cookie-jar)
  GET  /api/feed/posts                              (smoke / pick a post)
  GET  /api/feed/posts/{id}                         (NEW deep-link endpoint)
  GET  /api/feed/posts/nonexistent                  (404)
  POST /api/feed/posts/{id}/comments                (comment + count++)
  GET  /api/feed/posts/{id}/comments                (persistence)
  POST /api/feed/posts/{id}/share  target=external  caption=whatsapp/facebook/copy_link
  POST /api/feed/posts/{id}/share  target=external  no caption (channel=unknown)
  POST /api/feed/posts/{id}/share  target=invalid   (400)
  POST /api/feed/posts/{id}/like                    (regression toggle)
"""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PWD = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
BOB_PWD = "Test1234!"


def _login(email, pwd):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pwd}, timeout=60)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def bob():
    return _login(BOB_EMAIL, BOB_PWD)


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PWD)


@pytest.fixture(scope="module")
def post_id(bob):
    """Create a fresh post owned by bob so tests are self-contained."""
    text = f"TEST iter76 post {uuid.uuid4().hex[:6]}"
    r = bob.post(f"{BASE_URL}/api/feed/posts", json={"text": text, "media": []}, timeout=20)
    assert r.status_code == 200, r.text
    pid = r.json().get("post_id")
    assert pid
    return pid


# ---------------------------------------------------------------- single post
class TestSinglePost:
    def test_get_single_post_shape(self, bob, post_id):
        r = bob.get(f"{BASE_URL}/api/feed/posts/{post_id}", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for key in ["post_id", "user_id", "text", "created_at", "updated_at",
                    "likes_count", "comments_count", "shares_count", "is_liked",
                    "first_name", "last_name"]:
            assert key in d, f"missing {key} in {d.keys()}"
        assert d["post_id"] == post_id
        assert isinstance(d["is_liked"], bool)

    def test_get_single_post_404(self, bob):
        r = bob.get(f"{BASE_URL}/api/feed/posts/nonexistent_xyz_404", timeout=10)
        assert r.status_code == 404


# ------------------------------------------------------------------ comments
class TestComments:
    def test_create_comment_then_get_and_count_increments(self, bob, post_id):
        before = bob.get(f"{BASE_URL}/api/feed/posts/{post_id}", timeout=10).json()
        before_count = before.get("comments_count", 0) or 0

        text = f"TEST_iter76 comment {uuid.uuid4().hex[:6]}"
        r = bob.post(f"{BASE_URL}/api/feed/posts/{post_id}/comments",
                     json={"text": text}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("text") == text
        assert body.get("comment_id", "").startswith("cmt_")

        # GET to verify persistence
        rg = bob.get(f"{BASE_URL}/api/feed/posts/{post_id}/comments", timeout=10)
        assert rg.status_code == 200
        comments = rg.json()
        assert any(c.get("comment_id") == body["comment_id"] for c in comments)

        # counter incremented
        after = bob.get(f"{BASE_URL}/api/feed/posts/{post_id}", timeout=10).json()
        assert after.get("comments_count", 0) == before_count + 1


# ------------------------------------------------------------------- sharing
class TestExternalShare:
    @pytest.mark.parametrize("channel", ["whatsapp", "facebook", "copy_link"])
    def test_share_external_with_channel(self, bob, post_id, channel):
        r = bob.post(f"{BASE_URL}/api/feed/posts/{post_id}/share",
                     json={"target": "external", "caption": channel}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d == {"shared": True, "target": "external", "channel": channel}

    def test_share_external_no_caption_unknown(self, bob, post_id):
        r = bob.post(f"{BASE_URL}/api/feed/posts/{post_id}/share",
                     json={"target": "external"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("channel") == "unknown"

    def test_share_invalid_target_400(self, bob, post_id):
        r = bob.post(f"{BASE_URL}/api/feed/posts/{post_id}/share",
                     json={"target": "totally_invalid_target", "caption": ""}, timeout=15)
        assert r.status_code == 400

    def test_share_external_nonexistent_post_404(self, bob):
        r = bob.post(f"{BASE_URL}/api/feed/posts/nonexistent_zzz/share",
                     json={"target": "external", "caption": "whatsapp"}, timeout=15)
        assert r.status_code == 404


# ----------------------------------------------------------------- regression
class TestRegression:
    def test_like_toggle(self, bob, post_id):
        r1 = bob.post(f"{BASE_URL}/api/feed/posts/{post_id}/like", timeout=10)
        assert r1.status_code == 200
        s1 = r1.json().get("liked")
        assert isinstance(s1, bool)
        r2 = bob.post(f"{BASE_URL}/api/feed/posts/{post_id}/like", timeout=10)
        assert r2.status_code == 200
        assert r2.json().get("liked") == (not s1)

    def test_feed_listing_smoke(self, bob):
        r = bob.get(f"{BASE_URL}/api/feed/posts?page=1&limit=5", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "posts" in d and isinstance(d["posts"], list)
