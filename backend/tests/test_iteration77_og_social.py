"""Iteration 77 — Open Graph preview + user-to-user follow graph.
Runs against the public preview URL (cookie-auth via /api/auth/login).
"""
import os
import re
import time
import uuid
import pytest
import requests

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001").rstrip("/")
TIMEOUT = 60


# ---------- shared fixtures ----------

def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=TIMEOUT)
    assert r.status_code == 200, f"login failed {email}: {r.status_code} {r.text[:200]}"
    me = s.get(f"{BASE}/api/auth/me", timeout=TIMEOUT).json()
    s.user_id = me["user_id"]
    return s


@pytest.fixture(scope="module")
def bob():
    return _login("bob@japap.com", "Test1234!")


@pytest.fixture(scope="module")
def alice():
    return _login("alice@japap.com", "Test1234!")


@pytest.fixture(scope="module")
def bob_post(bob):
    r = bob.post(f"{BASE}/api/feed/posts",
                 json={"text": f"TEST_iter77 OG post {uuid.uuid4().hex[:8]}", "visibility": "public"},
                 timeout=TIMEOUT)
    assert r.status_code in (200, 201), r.text
    pid = r.json().get("post_id") or r.json().get("id")
    assert pid
    yield pid
    try:
        bob.delete(f"{BASE}/api/feed/posts/{pid}", timeout=TIMEOUT)
    except Exception:
        pass


# ---------- Open Graph ----------

class TestOG:
    def test_og_returns_html_with_og_and_twitter_tags(self, bob_post):
        r = requests.get(f"{BASE}/api/og/post/{bob_post}", timeout=TIMEOUT)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        html = r.text
        for tag in ['property="og:title"', 'property="og:description"', 'property="og:image"',
                    'property="og:url"', 'name="twitter:card"']:
            assert tag in html, f"missing {tag}"
        # meta-refresh redirect to the SPA /post/{id}
        assert re.search(r'http-equiv="refresh"[^>]*url=[^"]*/post/' + bob_post, html)

    def test_og_unknown_id_returns_200_fallback(self):
        r = requests.get(f"{BASE}/api/og/post/does-not-exist-{uuid.uuid4().hex}", timeout=TIMEOUT)
        assert r.status_code == 200
        assert "og:title" in r.text
        assert "introuvable" in r.text.lower() or "unavailable" in r.text.lower() or "JAPAP" in r.text

    def test_og_xss_escaped(self, bob):
        payload = '<script>alert(1)</script>HELLO_XSS'
        r = bob.post(f"{BASE}/api/feed/posts",
                     json={"text": f"TEST_iter77 {payload}", "visibility": "public"},
                     timeout=TIMEOUT)
        pid = r.json().get("post_id") or r.json().get("id")
        try:
            og = requests.get(f"{BASE}/api/og/post/{pid}", timeout=TIMEOUT).text
            # the literal script tag must NOT be rendered inside description
            # Extract the og:description meta tag only
            m = re.search(r'<meta property="og:description" content="([^"]*)"', og)
            assert m, "og:description missing"
            content = m.group(1)
            assert "<script>" not in content
            assert "&lt;script&gt;" in content or "&lt;" in content
            assert "HELLO_XSS" in content
        finally:
            bob.delete(f"{BASE}/api/feed/posts/{pid}", timeout=TIMEOUT)


# ---------- Follow graph ----------

class TestFollow:
    def test_self_follow_400(self, bob):
        r = bob.post(f"{BASE}/api/users/{bob.user_id}/follow", timeout=TIMEOUT)
        assert r.status_code == 400

    def test_follow_idempotent_and_counters(self, bob, alice):
        # reset state
        bob.delete(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        alice.delete(f"{BASE}/api/users/{bob.user_id}/follow", timeout=TIMEOUT)

        prof_alice_before = bob.get(f"{BASE}/api/users/profile/{alice.user_id}", timeout=TIMEOUT).json()
        f0 = int(prof_alice_before.get("followers_count") or 0)

        # 1st follow
        r1 = bob.post(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        assert r1.status_code == 200, r1.text
        assert r1.json()["followed"] is True
        # 2nd follow (idempotent)
        r2 = bob.post(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        assert r2.status_code == 200
        assert r2.json()["followed"] is True

        prof_alice = bob.get(f"{BASE}/api/users/profile/{alice.user_id}", timeout=TIMEOUT).json()
        assert int(prof_alice["followers_count"]) == f0 + 1
        assert prof_alice["is_following"] is True

        prof_bob = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert int(prof_bob["following_count"]) >= 1
        assert prof_bob["is_self"] is True

    def test_unfollow_idempotent(self, bob, alice):
        # ensure followed first
        bob.post(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        r1 = bob.delete(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        assert r1.status_code == 200
        assert r1.json()["followed"] is False
        # 2nd unfollow no-op
        r2 = bob.delete(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        assert r2.status_code == 200
        assert r2.json()["followed"] is False

    def test_followers_following_lists_shape(self, bob, alice):
        bob.post(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        r = bob.get(f"{BASE}/api/users/{alice.user_id}/followers?limit=10&offset=0", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert set(["items", "total", "limit", "offset"]).issubset(data.keys())
        assert data["limit"] == 10 and data["offset"] == 0
        # bob should appear as a follower of alice with is_self flag for himself viewing
        bob_row = next((i for i in data["items"] if i["user_id"] == bob.user_id), None)
        assert bob_row is not None
        assert bob_row["is_self"] is True

        r2 = bob.get(f"{BASE}/api/users/{bob.user_id}/following?limit=10&offset=0", timeout=TIMEOUT)
        assert r2.status_code == 200
        d2 = r2.json()
        assert any(i["user_id"] == alice.user_id for i in d2["items"])
        # alice row from bob's viewpoint: is_following=True
        alice_row = next(i for i in d2["items"] if i["user_id"] == alice.user_id)
        assert alice_row["is_following"] is True
        # cleanup
        bob.delete(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)

    def test_invalid_user_404(self, bob):
        r = bob.get(f"{BASE}/api/users/does-not-exist-xyz/followers", timeout=TIMEOUT)
        assert r.status_code == 404

    def test_follow_roundtrip(self, bob, alice):
        bob.delete(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        alice.delete(f"{BASE}/api/users/{bob.user_id}/follow", timeout=TIMEOUT)

        bob.post(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        alice.post(f"{BASE}/api/users/{bob.user_id}/follow", timeout=TIMEOUT)

        pa = bob.get(f"{BASE}/api/users/profile/{alice.user_id}", timeout=TIMEOUT).json()
        pb = alice.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert pa["is_following"] is True
        assert pb["is_following"] is True
        assert int(pa["followers_count"]) >= 1 and int(pa["following_count"]) >= 1

        bob.delete(f"{BASE}/api/users/{alice.user_id}/follow", timeout=TIMEOUT)
        alice.delete(f"{BASE}/api/users/{bob.user_id}/follow", timeout=TIMEOUT)

        pa2 = bob.get(f"{BASE}/api/users/profile/{alice.user_id}", timeout=TIMEOUT).json()
        assert pa2["is_following"] is False


# ---------- Profile update (cover_image + cover_position_y clamp) ----------

class TestProfileUpdate:
    def test_profile_has_new_fields(self, bob):
        r = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        for f in ["followers_count", "following_count", "posts_count",
                  "cover_image", "cover_position_y", "is_following", "is_self"]:
            assert f in data, f"missing profile field {f}"

    def test_cover_position_y_clamp_high(self, bob):
        r = bob.put(f"{BASE}/api/users/profile",
                    json={"cover_position_y": 999}, timeout=TIMEOUT)
        assert r.status_code == 200
        prof = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert int(prof["cover_position_y"]) == 100

    def test_cover_position_y_clamp_low(self, bob):
        r = bob.put(f"{BASE}/api/users/profile",
                    json={"cover_position_y": -5}, timeout=TIMEOUT)
        assert r.status_code == 200
        prof = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert int(prof["cover_position_y"]) == 0

    def test_cover_image_set(self, bob):
        url = "/api/upload/test_cover.png"
        r = bob.put(f"{BASE}/api/users/profile",
                    json={"cover_image": url, "cover_position_y": 50}, timeout=TIMEOUT)
        assert r.status_code == 200
        prof = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert prof["cover_image"] == url
        assert int(prof["cover_position_y"]) == 50

    def test_invalid_payload_type(self, bob):
        r = bob.put(f"{BASE}/api/users/profile",
                    json={"cover_position_y": "not-a-number"}, timeout=TIMEOUT)
        assert r.status_code in (400, 422)


# ---------- posts_count auto-maintenance ----------

class TestPostsCount:
    def test_posts_count_increments_and_decrements(self, bob):
        before = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        c0 = int(before["posts_count"])

        r = bob.post(f"{BASE}/api/feed/posts",
                     json={"text": f"TEST_iter77 count {uuid.uuid4().hex[:6]}", "visibility": "public"},
                     timeout=TIMEOUT)
        pid = r.json().get("post_id") or r.json().get("id")
        mid = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert int(mid["posts_count"]) == c0 + 1

        dr = bob.delete(f"{BASE}/api/feed/posts/{pid}", timeout=TIMEOUT)
        assert dr.status_code in (200, 204)
        after = bob.get(f"{BASE}/api/users/profile/{bob.user_id}", timeout=TIMEOUT).json()
        assert int(after["posts_count"]) == c0


# ---------- Regression: iter76 share + geo + auth ----------

class TestRegressions:
    def test_geo_detect(self):
        r = requests.get(f"{BASE}/api/geo/detect", timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert any(k in d for k in ("language", "currency", "country", "country_code", "suggested_lang"))

    def test_feed_listing(self, bob):
        r = bob.get(f"{BASE}/api/feed/posts?limit=5", timeout=TIMEOUT)
        assert r.status_code == 200

    def test_share_external(self, bob, bob_post):
        r = bob.post(f"{BASE}/api/feed/posts/{bob_post}/share",
                     json={"target": "external", "caption": "whatsapp"}, timeout=TIMEOUT)
        assert r.status_code in (200, 201)
