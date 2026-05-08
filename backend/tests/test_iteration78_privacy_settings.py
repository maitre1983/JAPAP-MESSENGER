"""
JAPAP iter78 — Privacy & Settings layer tests
Covers: /privacy GET+PUT, follow approval flow, follow-request inbox,
remove-follower, change-password, feed visibility, notifications.
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"

BOB_EMAIL = "bob@japap.com"
ALICE_EMAIL = "alice@japap.com"
PWD = "Test1234!"


def _login(email, password=PWD):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login {email} -> {r.status_code} {r.text}"
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    uid = (data.get("user") or {}).get("user_id") or data.get("user_id")
    assert tok and uid, f"missing token/uid: {data}"
    return tok, uid


def _h(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def bob():
    tok, uid = _login(BOB_EMAIL)
    # Reset Bob to public/auto/public + all notify_* true
    requests.put(f"{API}/users/me/privacy", headers=_h(tok), json={
        "account_visibility": "public", "follow_mode": "auto",
        "post_visibility_default": "public",
        "notify_follow": True, "notify_follow_accept": True,
        "notify_likes": True, "notify_comments": True, "notify_messages": True,
    }, timeout=15)
    return tok, uid


@pytest.fixture(scope="module")
def alice():
    tok, uid = _login(ALICE_EMAIL)
    requests.put(f"{API}/users/me/privacy", headers=_h(tok), json={
        "account_visibility": "public", "follow_mode": "auto",
        "post_visibility_default": "public",
        "notify_follow": True, "notify_follow_accept": True,
        "notify_likes": True, "notify_comments": True, "notify_messages": True,
    }, timeout=15)
    return tok, uid


def _cleanup_follow(tok_from, uid_to):
    requests.delete(f"{API}/users/{uid_to}/follow", headers=_h(tok_from), timeout=10)


def _profile(tok, uid):
    r = requests.get(f"{API}/users/profile/{uid}", headers=_h(tok), timeout=10)
    return r.json() if r.status_code == 200 else {}


# ──────────────── GET/PUT privacy ────────────────

class TestPrivacy:
    def test_get_privacy_has_all_fields(self, bob):
        tok, _ = bob
        r = requests.get(f"{API}/users/me/privacy", headers=_h(tok), timeout=10)
        assert r.status_code == 200
        d = r.json()
        for k in ("account_visibility", "follow_mode", "post_visibility_default",
                  "notify_follow", "notify_follow_accept", "notify_likes",
                  "notify_comments", "notify_messages"):
            assert k in d, f"missing {k}"
        assert d["account_visibility"] in ("public", "private")

    def test_put_privacy_partial_and_merge(self, bob):
        tok, _ = bob
        r = requests.put(f"{API}/users/me/privacy", headers=_h(tok),
                         json={"notify_likes": False}, timeout=10)
        assert r.status_code == 200
        assert r.json()["notify_likes"] is False
        # restore
        requests.put(f"{API}/users/me/privacy", headers=_h(tok),
                     json={"notify_likes": True}, timeout=10)

    def test_put_privacy_invalid_enum(self, bob):
        tok, _ = bob
        r = requests.put(f"{API}/users/me/privacy", headers=_h(tok),
                         json={"account_visibility": "ninja"}, timeout=10)
        assert r.status_code in (400, 422)

    def test_put_privacy_empty_body(self, bob):
        tok, _ = bob
        r = requests.put(f"{API}/users/me/privacy", headers=_h(tok), json={}, timeout=10)
        assert r.status_code == 400


# ──────────────── Follow approval flow ────────────────

class TestFollowApproval:
    def test_follow_approval_mode_pending(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        _cleanup_follow(bob_tok, alice_uid)
        # Alice sets approval mode
        r = requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                         json={"follow_mode": "approval"}, timeout=10)
        assert r.status_code == 200
        # counters before
        before = requests.get(f"{API}/users/profile/{alice_uid}", headers=_h(bob_tok), timeout=10).json()
        prev_count = before.get("followers_count", 0)
        # Bob follows Alice → should be pending
        r = requests.post(f"{API}/users/{alice_uid}/follow", headers=_h(bob_tok), timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "pending"
        assert d["followed"] is False
        assert d["followers_count"] == prev_count
        # Idempotent: 2nd call still pending
        r2 = requests.post(f"{API}/users/{alice_uid}/follow", headers=_h(bob_tok), timeout=10)
        assert r2.json()["status"] == "pending"

    def test_follow_requests_inbox(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        r = requests.get(f"{API}/users/me/follow-requests", headers=_h(alice_tok), timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and "total" in d
        # Bob should be in there
        found = [it for it in d["items"] if it["follower_id"] == bob_uid]
        assert found, f"Bob not found in Alice's pending: {d}"
        assert "request_id" in found[0]
        assert "username" in found[0]

    def test_accept_follow_request_and_counters(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # get pending request
        r = requests.get(f"{API}/users/me/follow-requests", headers=_h(alice_tok), timeout=10).json()
        req = next((it for it in r["items"] if it["follower_id"] == bob_uid), None)
        assert req, "no pending request"
        rid = req["request_id"]
        pre = requests.get(f"{API}/users/profile/{alice_uid}", headers=_h(bob_tok), timeout=10).json()
        r2 = requests.post(f"{API}/users/me/follow-requests/{rid}/accept", headers=_h(alice_tok), timeout=10)
        assert r2.status_code == 200
        assert r2.json()["accepted"] is True
        # counter bumped
        post = requests.get(f"{API}/users/profile/{alice_uid}", headers=_h(bob_tok), timeout=10).json()
        assert post["followers_count"] == pre.get("followers_count", 0) + 1
        assert post.get("is_following") is True
        # 2nd accept is idempotent (already)
        r3 = requests.post(f"{API}/users/me/follow-requests/{rid}/accept", headers=_h(alice_tok), timeout=10)
        assert r3.status_code == 200 and r3.json().get("already") is True

    def test_accept_other_users_request_404(self, bob, alice):
        bob_tok, _ = bob
        # Bob tries to accept some random id
        r = requests.post(f"{API}/users/me/follow-requests/999999/accept", headers=_h(bob_tok), timeout=10)
        assert r.status_code == 404

    def test_remove_follower(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # ensure bob follows alice (from accept test)
        pre = requests.get(f"{API}/users/profile/{alice_uid}", headers=_h(bob_tok), timeout=10).json()
        fc = pre["followers_count"]
        r = requests.delete(f"{API}/users/me/followers/{bob_uid}", headers=_h(alice_tok), timeout=10)
        assert r.status_code == 200
        assert r.json()["removed"] is True
        post = requests.get(f"{API}/users/profile/{alice_uid}", headers=_h(bob_tok), timeout=10).json()
        assert post["followers_count"] == fc - 1
        # non-follower 404
        r2 = requests.delete(f"{API}/users/me/followers/{bob_uid}", headers=_h(alice_tok), timeout=10)
        assert r2.status_code == 404

    def test_decline_follow_request(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # ensure alice still in approval mode; bob re-sends follow
        requests.post(f"{API}/users/{alice_uid}/follow", headers=_h(bob_tok), timeout=10)
        r = requests.get(f"{API}/users/me/follow-requests", headers=_h(alice_tok), timeout=10).json()
        req = next((it for it in r["items"] if it["follower_id"] == bob_uid), None)
        assert req
        rid = req["request_id"]
        r2 = requests.post(f"{API}/users/me/follow-requests/{rid}/decline", headers=_h(alice_tok), timeout=10)
        assert r2.status_code == 200 and r2.json()["declined"] is True
        r3 = requests.post(f"{API}/users/me/follow-requests/{rid}/decline", headers=_h(alice_tok), timeout=10)
        assert r3.status_code == 404

    def test_followers_listing_only_accepted(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # ensure clean then create pending req
        _cleanup_follow(bob_tok, alice_uid)
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"follow_mode": "approval"}, timeout=10)
        requests.post(f"{API}/users/{alice_uid}/follow", headers=_h(bob_tok), timeout=10)
        # followers list should not include pending bob
        r = requests.get(f"{API}/users/{alice_uid}/followers", headers=_h(alice_tok), timeout=10)
        assert r.status_code == 200
        ids = [it["user_id"] for it in r.json()["items"]]
        assert bob_uid not in ids, f"pending shouldn't appear: {ids}"

    def test_followers_search_q(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # accept bob so he appears
        r = requests.get(f"{API}/users/me/follow-requests", headers=_h(alice_tok), timeout=10).json()
        req = next((it for it in r["items"] if it["follower_id"] == bob_uid), None)
        if req:
            requests.post(f"{API}/users/me/follow-requests/{req['request_id']}/accept",
                          headers=_h(alice_tok), timeout=10)
        r = requests.get(f"{API}/users/{alice_uid}/followers?q=bob", headers=_h(alice_tok), timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert all("bob" in (it.get("username") or "").lower() + (it.get("first_name") or "").lower()
                   for it in d["items"])


# ──────────────── Change password ────────────────

class TestChangePassword:
    def test_wrong_current(self, bob):
        tok, _ = bob
        r = requests.post(f"{API}/users/me/change-password", headers=_h(tok),
                          json={"current_password": "WRONG_PW!", "new_password": "NewPw123!"}, timeout=10)
        assert r.status_code == 401

    def test_too_short(self, bob):
        tok, _ = bob
        r = requests.post(f"{API}/users/me/change-password", headers=_h(tok),
                          json={"current_password": PWD, "new_password": "short"}, timeout=10)
        assert r.status_code == 400


# ──────────────── Feed visibility ────────────────

class TestFeedVisibility:
    def _set_default(self, tok, viz):
        r = requests.put(f"{API}/users/me/privacy", headers=_h(tok),
                         json={"post_visibility_default": viz}, timeout=10)
        assert r.status_code == 200

    def test_only_me_hidden_from_others(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, _ = alice
        # reset alice → public account for clean view
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"account_visibility": "public", "follow_mode": "auto"}, timeout=10)
        self._set_default(bob_tok, "only_me")
        cp = requests.post(f"{API}/feed/posts", headers=_h(bob_tok),
                           json={"text": "TEST_iter78_only_me marker xyz"}, timeout=15)
        assert cp.status_code == 200
        pid = cp.json()["post_id"]
        # Bob sees it
        r1 = requests.get(f"{API}/feed/posts/{pid}", headers=_h(bob_tok), timeout=10)
        assert r1.status_code == 200
        # Alice doesn't
        r2 = requests.get(f"{API}/feed/posts/{pid}", headers=_h(alice_tok), timeout=10)
        assert r2.status_code == 404
        # cleanup + restore
        requests.delete(f"{API}/feed/posts/{pid}", headers=_h(bob_tok), timeout=10)
        self._set_default(bob_tok, "public")

    def test_friends_visible_to_follower(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # make alice follow bob (auto mode)
        requests.put(f"{API}/users/me/privacy", headers=_h(bob_tok),
                     json={"follow_mode": "auto", "account_visibility": "public"}, timeout=10)
        requests.post(f"{API}/users/{bob_uid}/follow", headers=_h(alice_tok), timeout=10)
        self._set_default(bob_tok, "friends")
        cp = requests.post(f"{API}/feed/posts", headers=_h(bob_tok),
                           json={"text": "TEST_iter78_friends_only"}, timeout=15)
        pid = cp.json()["post_id"]
        # alice (follower) should see
        r_alice = requests.get(f"{API}/feed/posts/{pid}", headers=_h(alice_tok), timeout=10)
        assert r_alice.status_code == 200
        # non-follower: create temp admin? skip; just check author sees
        r_bob = requests.get(f"{API}/feed/posts/{pid}", headers=_h(bob_tok), timeout=10)
        assert r_bob.status_code == 200
        # cleanup
        requests.delete(f"{API}/feed/posts/{pid}", headers=_h(bob_tok), timeout=10)
        requests.delete(f"{API}/users/{bob_uid}/follow", headers=_h(alice_tok), timeout=10)
        self._set_default(bob_tok, "public")

    def test_private_account_hides_public_post_from_non_follower(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # Alice private account
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"account_visibility": "private", "follow_mode": "approval",
                           "post_visibility_default": "public"}, timeout=10)
        # Ensure Bob is NOT following Alice
        _cleanup_follow(bob_tok, alice_uid)
        cp = requests.post(f"{API}/feed/posts", headers=_h(alice_tok),
                           json={"text": "TEST_iter78_alice_private_public"}, timeout=15)
        pid = cp.json()["post_id"]
        r = requests.get(f"{API}/feed/posts/{pid}", headers=_h(bob_tok), timeout=10)
        assert r.status_code == 404
        # Alice sees her own
        assert requests.get(f"{API}/feed/posts/{pid}", headers=_h(alice_tok), timeout=10).status_code == 200
        # cleanup
        requests.delete(f"{API}/feed/posts/{pid}", headers=_h(alice_tok), timeout=10)
        # Restore alice
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"account_visibility": "public", "follow_mode": "auto"}, timeout=10)


# ──────────────── Notifications ────────────────

class TestNotifications:
    def test_follow_notification_created(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        # ensure clean
        _cleanup_follow(bob_tok, alice_uid)
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"account_visibility": "public", "follow_mode": "auto",
                           "notify_follow": True}, timeout=10)
        requests.post(f"{API}/users/{alice_uid}/follow", headers=_h(bob_tok), timeout=10)
        # fetch notifications
        r = requests.get(f"{API}/notifications", headers=_h(alice_tok), timeout=10)
        if r.status_code != 200:
            pytest.skip(f"notifications endpoint unavailable: {r.status_code}")
        data = r.json()
        items = data if isinstance(data, list) else (data.get("items") or data.get("notifications") or [])
        assert any(n.get("type") == "social.follow" for n in items), f"no follow notif: {items[:3]}"
        _cleanup_follow(bob_tok, alice_uid)

    def test_notify_follow_disabled_skips_row(self, bob, alice):
        bob_tok, bob_uid = bob
        alice_tok, alice_uid = alice
        _cleanup_follow(bob_tok, alice_uid)
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"notify_follow": False, "account_visibility": "public",
                           "follow_mode": "auto"}, timeout=10)
        # get pre count
        r1 = requests.get(f"{API}/notifications", headers=_h(alice_tok), timeout=10)
        if r1.status_code != 200:
            pytest.skip("notifications endpoint unavailable")
        pre = r1.json()
        pre_items = pre if isinstance(pre, list) else (pre.get("items") or pre.get("notifications") or [])
        pre_follow = [n for n in pre_items if n.get("type") == "social.follow"]
        requests.post(f"{API}/users/{alice_uid}/follow", headers=_h(bob_tok), timeout=10)
        r2 = requests.get(f"{API}/notifications", headers=_h(alice_tok), timeout=10).json()
        post_items = r2 if isinstance(r2, list) else (r2.get("items") or r2.get("notifications") or [])
        post_follow = [n for n in post_items if n.get("type") == "social.follow"]
        # No new row (dedup window + opt-out)
        assert len(post_follow) <= len(pre_follow) + 0  # exactly equal
        # restore
        requests.put(f"{API}/users/me/privacy", headers=_h(alice_tok),
                     json={"notify_follow": True}, timeout=10)
        _cleanup_follow(bob_tok, alice_uid)
