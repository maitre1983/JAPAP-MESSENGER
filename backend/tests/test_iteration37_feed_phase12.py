"""
Iteration 37 — Feed Phase 1+2: post management (edit/delete/pin/share)
+ AI Assistant /api/ai/improve-text (Claude Sonnet 4.5 via Emergent LLM Key).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

BOB = {"email": "bob@japap.com", "password": "Test1234!"}
ALICE = {"email": "alice@japap.com", "password": "Test1234!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=creds, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Login failed for {creds['email']}: {r.status_code} {r.text[:120]}")
    return s


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


@pytest.fixture(scope="module")
def alice():
    return _login(ALICE)


@pytest.fixture(scope="module")
def bob_post(bob):
    r = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter37 hello world", "media": []}, timeout=20)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["text"] == "TEST_iter37 hello world"
    assert "post_id" in p
    return p


# ---------------- Feed CRUD ----------------

class TestFeedRegression:
    def test_create_post(self, bob):
        r = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter37 regression", "media": []})
        assert r.status_code == 200
        assert r.json()["text"] == "TEST_iter37 regression"

    def test_feed_smart(self, bob):
        r = bob.get(f"{API}/feed/posts?sort=smart&limit=10")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d.get("posts"), list)
        assert d["sort"] == "smart"

    def test_feed_recent(self, bob):
        r = bob.get(f"{API}/feed/posts?sort=recent&limit=10")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d.get("posts"), list)
        assert d["sort"] == "recent"

    def test_like_and_comment(self, bob, bob_post):
        pid = bob_post["post_id"]
        r = bob.post(f"{API}/feed/posts/{pid}/like")
        assert r.status_code == 200
        assert "liked" in r.json()
        # comment
        r = bob.post(f"{API}/feed/posts/{pid}/comments", json={"text": "TEST_iter37 nice"})
        assert r.status_code == 200
        r = bob.get(f"{API}/feed/posts/{pid}/comments")
        assert r.status_code == 200
        assert any(c["text"] == "TEST_iter37 nice" for c in r.json())


# ---------------- Edit ----------------

class TestPostEdit:
    def test_owner_can_edit(self, bob, bob_post):
        pid = bob_post["post_id"]
        r = bob.put(f"{API}/feed/posts/{pid}", json={"text": "TEST_iter37 edited"})
        assert r.status_code == 200, r.text
        assert r.json()["text"] == "TEST_iter37 edited"

    def test_non_owner_403(self, alice, bob_post):
        pid = bob_post["post_id"]
        r = alice.put(f"{API}/feed/posts/{pid}", json={"text": "hacked"})
        assert r.status_code == 403

    def test_unknown_404(self, bob):
        r = bob.put(f"{API}/feed/posts/post_doesnotexist", json={"text": "x"})
        assert r.status_code == 404


# ---------------- Pin ----------------

class TestPostPin:
    def test_owner_can_toggle(self, bob, bob_post):
        pid = bob_post["post_id"]
        r = bob.post(f"{API}/feed/posts/{pid}/pin")
        assert r.status_code == 200
        s1 = r.json()["is_pinned"]
        r2 = bob.post(f"{API}/feed/posts/{pid}/pin")
        assert r2.status_code == 200
        assert r2.json()["is_pinned"] != s1

    def test_non_owner_403(self, alice, bob_post):
        r = alice.post(f"{API}/feed/posts/{bob_post['post_id']}/pin")
        assert r.status_code == 403

    def test_unknown_404(self, bob):
        r = bob.post(f"{API}/feed/posts/post_zzznope/pin")
        assert r.status_code == 404


# ---------------- Share ----------------

class TestPostShare:
    def test_share_to_feed_creates_new_post(self, alice, bob_post):
        pid = bob_post["post_id"]
        r = alice.post(f"{API}/feed/posts/{pid}/share", json={"target": "feed", "caption": "hello"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["shared"] is True
        assert d["target"] == "feed"
        assert d.get("new_post_id", "").startswith("post_")

    @pytest.mark.parametrize("target", ["group", "page", "dm"])
    def test_share_to_other_targets(self, alice, bob_post, target):
        r = alice.post(
            f"{API}/feed/posts/{bob_post['post_id']}/share",
            json={"target": target, "target_id": "tgt_xyz", "caption": "h"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["shared"] is True
        assert d["target"] == target
        assert d.get("target_id") == "tgt_xyz"
        assert "note" in d
        assert "new_post_id" not in d

    def test_share_invalid_target(self, alice, bob_post):
        r = alice.post(
            f"{API}/feed/posts/{bob_post['post_id']}/share",
            json={"target": "twitter", "caption": ""},
        )
        assert r.status_code in (400, 422)

    def test_share_increments_counter(self, alice, bob):
        # Create fresh post, share once, verify shares_count==1 in feed payload
        cr = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter37 share-counter", "media": []})
        pid = cr.json()["post_id"]
        sr = alice.post(f"{API}/feed/posts/{pid}/share", json={"target": "feed", "caption": ""})
        assert sr.status_code == 200
        feed = bob.get(f"{API}/feed/posts?sort=recent&limit=50").json()["posts"]
        match = next((p for p in feed if p["post_id"] == pid), None)
        assert match is not None
        assert (match.get("shares_count") or 0) >= 1


# ---------------- Delete (last so we don't break other tests on bob_post) ----------------

class TestPostDelete:
    def test_non_owner_403(self, alice, bob):
        cr = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter37 del-403", "media": []})
        pid = cr.json()["post_id"]
        r = alice.delete(f"{API}/feed/posts/{pid}")
        assert r.status_code == 403

    def test_owner_can_delete(self, bob):
        cr = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter37 del-ok", "media": []})
        pid = cr.json()["post_id"]
        r = bob.delete(f"{API}/feed/posts/{pid}")
        assert r.status_code == 200
        assert r.json().get("deleted") is True

    def test_delete_unknown_404(self, bob):
        r = bob.delete(f"{API}/feed/posts/post_nonexistentxyz")
        assert r.status_code == 404


# ---------------- AI improve-text ----------------

class TestAIImproveText:
    def test_requires_auth(self):
        r = requests.post(f"{API}/ai/improve-text", json={"text": "hi", "action": "correct"}, timeout=15)
        assert r.status_code == 401

    def test_empty_text_400(self, bob):
        r = bob.post(f"{API}/ai/improve-text", json={"text": "   ", "action": "correct"})
        assert r.status_code == 400

    def test_correct(self, bob):
        r = bob.post(
            f"{API}/ai/improve-text",
            json={"text": "je part au marche", "action": "correct"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["action"] == "correct"
        assert isinstance(d["improved"], str) and len(d["improved"]) > 0
        improved = d["improved"].lower()
        # Expect at least one of the corrections to appear
        assert ("pars" in improved) or ("marché" in improved) or ("je " in improved)

    def test_generate(self, bob):
        r = bob.post(
            f"{API}/ai/improve-text",
            json={"text": "café au Cameroun", "action": "generate"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["action"] == "generate"
        assert len(d["improved"]) > 0

    @pytest.mark.parametrize("action", ["suggest", "rephrase"])
    def test_suggest_rephrase(self, bob, action):
        r = bob.post(
            f"{API}/ai/improve-text",
            json={"text": "Aujourd'hui j'ai mangé du ndolé c'était trop bon", "action": action},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["action"] == action
        assert isinstance(d["improved"], str) and len(d["improved"]) > 0
