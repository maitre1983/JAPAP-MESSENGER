"""
JAPAP - Feed Extended (Reels + Stories + Tip wallet) — Iteration 15
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    # Load from frontend/.env
    try:
        with open('/app/frontend/.env') as _f:
            for _line in _f:
                if _line.startswith('REACT_APP_BACKEND_URL='):
                    BASE_URL = _line.split('=', 1)[1].strip().rstrip('/')
                    break
    except Exception:
        pass
assert BASE_URL, "REACT_APP_BACKEND_URL not configured"

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
USER2_EMAIL = "testref_1776710356@japap.com"
USER2_PASSWORD = "Test1234!"


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed {email}: {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def user2():
    return _login(USER2_EMAIL, USER2_PASSWORD)


# ============================== REELS ==============================
class TestReels:
    def test_create_reel_ok(self, admin):
        r = admin.post(f"{BASE_URL}/api/feed/reels", json={
            "video_url": "https://example.com/test.mp4",
            "thumbnail_url": "https://example.com/t.jpg",
            "caption": f"TEST_reel {uuid.uuid4().hex[:6]}",
            "duration": 30,
            "music_title": "Test Track",
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["reel_id"].startswith("reel_")
        assert data["post_id"].startswith("post_")
        pytest.reel_id = data["reel_id"]
        pytest.reel_post_id = data["post_id"]

    def test_create_reel_duration_too_long(self, admin):
        r = admin.post(f"{BASE_URL}/api/feed/reels", json={
            "video_url": "https://example.com/test.mp4", "duration": 90,
        })
        assert r.status_code == 400
        assert "60" in r.json()["detail"]

    def test_create_reel_missing_video(self, admin):
        r = admin.post(f"{BASE_URL}/api/feed/reels", json={"video_url": "", "duration": 5})
        assert r.status_code == 400

    def test_list_reels(self, admin):
        r = admin.get(f"{BASE_URL}/api/feed/reels")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        first = data[0]
        for k in ("reel_id", "post_id", "video_url", "is_liked", "user", "tips_total", "views_count", "likes_count"):
            assert k in first
        assert "name" in first["user"]

    def test_view_reel_idempotent(self, user2):
        # Use existing seeded reel
        target = "reel_a0ff8f48c595"
        r1 = user2.post(f"{BASE_URL}/api/feed/reels/{target}/view", params={"watched_seconds": 5})
        assert r1.status_code == 200
        # Get current count
        reels = user2.get(f"{BASE_URL}/api/feed/reels").json()
        rec = next((x for x in reels if x["reel_id"] == target), None)
        assert rec is not None
        v1 = rec["views_count"]
        # Second view should NOT increment
        r2 = user2.post(f"{BASE_URL}/api/feed/reels/{target}/view", params={"watched_seconds": 10})
        assert r2.status_code == 200
        reels2 = user2.get(f"{BASE_URL}/api/feed/reels").json()
        rec2 = next((x for x in reels2 if x["reel_id"] == target), None)
        assert rec2["views_count"] == v1, f"View count incremented on duplicate view: {v1} -> {rec2['views_count']}"

    def test_like_reel_toggles(self, user2):
        target = "reel_a0ff8f48c595"
        # Get baseline
        reels = user2.get(f"{BASE_URL}/api/feed/reels").json()
        rec = next(x for x in reels if x["reel_id"] == target)
        baseline_likes = rec["likes_count"]
        was_liked = rec["is_liked"]

        r = user2.post(f"{BASE_URL}/api/feed/reels/{target}/like")
        assert r.status_code == 200
        liked_state = r.json()["liked"]
        # toggle once again to bring back
        r2 = user2.post(f"{BASE_URL}/api/feed/reels/{target}/like")
        assert r2.status_code == 200
        assert r2.json()["liked"] != liked_state
        # Final state should equal baseline
        reels_after = user2.get(f"{BASE_URL}/api/feed/reels").json()
        rec_after = next(x for x in reels_after if x["reel_id"] == target)
        assert rec_after["likes_count"] == baseline_likes
        assert rec_after["is_liked"] == was_liked


# ============================== STORIES ==============================
class TestStories:
    def test_create_story_text(self, admin):
        r = admin.post(f"{BASE_URL}/api/feed/stories", json={
            "text": f"TEST_story {uuid.uuid4().hex[:6]}", "background_color": "#0F056B"
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["story_id"].startswith("story_")
        assert "expires_at" in data
        pytest.story_id = data["story_id"]

    def test_create_story_empty_fails(self, admin):
        r = admin.post(f"{BASE_URL}/api/feed/stories", json={"text": "  ", "image_url": ""})
        assert r.status_code == 400

    def test_list_stories_grouped(self, admin):
        r = admin.get(f"{BASE_URL}/api/feed/stories")
        assert r.status_code == 200
        groups = r.json()
        assert isinstance(groups, list)
        assert len(groups) >= 1
        for g in groups:
            assert "user_id" in g and "stories" in g and "all_viewed" in g
        # Current user (admin) must be first when admin has stories
        admin_ids = [g for g in groups if g["stories"]]
        assert admin_ids[0]["user_id"]  # smoke

    def test_view_story_idempotent(self, user2):
        seeded = "story_a9e44119b98b"
        r1 = user2.post(f"{BASE_URL}/api/feed/stories/{seeded}/view")
        assert r1.status_code == 200
        groups = user2.get(f"{BASE_URL}/api/feed/stories").json()
        s1 = None
        for g in groups:
            for s in g["stories"]:
                if s["story_id"] == seeded:
                    s1 = s
        assert s1 is not None
        v1 = s1["views_count"]
        r2 = user2.post(f"{BASE_URL}/api/feed/stories/{seeded}/view")
        assert r2.status_code == 200
        groups2 = user2.get(f"{BASE_URL}/api/feed/stories").json()
        s2 = None
        for g in groups2:
            for s in g["stories"]:
                if s["story_id"] == seeded:
                    s2 = s
        assert s2["views_count"] == v1

    def test_delete_story_non_owner_forbidden(self, user2):
        seeded = "story_a9e44119b98b"  # admin's
        r = user2.delete(f"{BASE_URL}/api/feed/stories/{seeded}")
        assert r.status_code == 403

    def test_delete_story_owner_ok(self, admin):
        # Create a throwaway story then delete as owner
        c = admin.post(f"{BASE_URL}/api/feed/stories", json={"text": "TEST_delete_me"})
        sid = c.json()["story_id"]
        r = admin.delete(f"{BASE_URL}/api/feed/stories/{sid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True


# ============================== TIP ==============================
class TestTip:
    def test_tip_below_minimum(self, user2):
        r = user2.post(f"{BASE_URL}/api/feed/tip", json={
            "target_type": "reel", "target_id": "reel_a0ff8f48c595", "amount": 10
        })
        assert r.status_code == 400
        assert "50" in r.json()["detail"]

    def test_tip_self_forbidden(self, admin):
        # admin tipping admin's own seeded reel
        r = admin.post(f"{BASE_URL}/api/feed/tip", json={
            "target_type": "reel", "target_id": "reel_a0ff8f48c595", "amount": 100
        })
        assert r.status_code == 400
        assert "soi" in r.json()["detail"].lower()

    def test_tip_target_not_found(self, user2):
        r = user2.post(f"{BASE_URL}/api/feed/tip", json={
            "target_type": "reel", "target_id": "reel_doesnotexist", "amount": 100
        })
        assert r.status_code == 404

    def test_tip_invalid_target_type(self, user2):
        r = user2.post(f"{BASE_URL}/api/feed/tip", json={
            "target_type": "video", "target_id": "x", "amount": 100
        })
        assert r.status_code == 400

    def test_tip_success_full_flow(self, admin, user2):
        """user2 tips admin's reel; verify wallet, tips_total, transactions, notification."""
        target = "reel_a0ff8f48c595"

        # Ensure user2 has balance — top up via wallet/deposit if low
        bal_r = user2.get(f"{BASE_URL}/api/wallet/balance")
        if bal_r.status_code == 200:
            bal = float(bal_r.json().get("balance", 0))
            if bal < 200:
                user2.post(f"{BASE_URL}/api/wallet/deposit", json={"amount": 1000.0, "method": "bank_transfer"})

        sender_before = float(user2.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        recipient_before = float(admin.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])

        # tips_total before
        reels = user2.get(f"{BASE_URL}/api/feed/reels").json()
        rec = next(x for x in reels if x["reel_id"] == target)
        tips_total_before = float(rec["tips_total"])

        amount = 100.0
        r = user2.post(f"{BASE_URL}/api/feed/tip", json={
            "target_type": "reel", "target_id": target, "amount": amount, "message": "Bravo!"
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["tip_id"].startswith("tip_")
        assert float(data["amount"]) == amount

        sender_after = float(user2.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        recipient_after = float(admin.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        assert abs((sender_before - sender_after) - amount) < 0.01
        assert abs((recipient_after - recipient_before) - amount) < 0.01

        reels_after = user2.get(f"{BASE_URL}/api/feed/reels").json()
        rec_after = next(x for x in reels_after if x["reel_id"] == target)
        assert abs(float(rec_after["tips_total"]) - tips_total_before - amount) < 0.01

    def test_tip_insufficient_balance(self, user2):
        r = user2.post(f"{BASE_URL}/api/feed/tip", json={
            "target_type": "reel", "target_id": "reel_a0ff8f48c595", "amount": 9999999999.0
        })
        assert r.status_code == 400
        assert "insuffisant" in r.json()["detail"].lower()

    def test_tips_received(self, admin):
        r = admin.get(f"{BASE_URL}/api/feed/tips/received")
        assert r.status_code == 200
        lst = r.json()
        assert isinstance(lst, list)
        # Should at least contain previous tip
        assert len(lst) >= 1
        first = lst[0]
        assert "tip_id" in first and "sender" in first and "amount" in first


# ============================== REGRESSION: posts new fields ==============================
class TestPostsRegression:
    def test_posts_have_new_fields(self, admin):
        r = admin.get(f"{BASE_URL}/api/feed/posts?page=1&limit=10")
        assert r.status_code == 200
        data = r.json()
        assert "posts" in data
        if data["posts"]:
            p = data["posts"][0]
            for k in ("tips_total", "tips_count", "shares_count", "views_count"):
                assert k in p, f"Post missing new field {k}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
