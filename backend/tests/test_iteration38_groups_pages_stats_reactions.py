"""
Iteration 38 — Phase 3 (Groups + Pages with real share delivery),
Phase 4 (Admin Stats) and Phase 5 (Emoji reactions).
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

BOB = {"email": "bob@japap.com", "password": "Test1234!"}
ALICE = {"email": "alice@japap.com", "password": "Test1234!"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


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
def admin():
    return _login(ADMIN)


# Persistent shared resources across tests
_state = {}


# =========================================================================
# GROUPS
# =========================================================================
class TestGroups:
    def test_create_group_public(self, bob):
        suffix = uuid.uuid4().hex[:6]
        r = bob.post(f"{API}/groups", json={
            "name": f"TEST_iter38 group pub {suffix}",
            "description": "test",
            "privacy": "public",
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["group_id"].startswith("grp_")
        _state["pub_gid"] = d["group_id"]

    def test_create_group_private(self, bob):
        r = bob.post(f"{API}/groups", json={
            "name": "TEST_iter38 group priv",
            "privacy": "private",
        })
        assert r.status_code == 200
        _state["priv_gid"] = r.json()["group_id"]

    def test_create_group_empty_name_400(self, bob):
        r = bob.post(f"{API}/groups", json={"name": "   ", "privacy": "public"})
        assert r.status_code == 400

    def test_create_group_invalid_privacy_400(self, bob):
        r = bob.post(f"{API}/groups", json={"name": "TEST_iter38 bad", "privacy": "secret"})
        assert r.status_code == 400

    def test_list_groups(self, bob):
        r = bob.get(f"{API}/groups?limit=50")
        assert r.status_code == 200
        items = r.json()["items"]
        assert isinstance(items, list)
        # owner sees is_member True for owned public group
        own = next((g for g in items if g["group_id"] == _state["pub_gid"]), None)
        assert own is not None
        assert own["is_member"] is True

    def test_list_groups_mine(self, bob):
        r = bob.get(f"{API}/groups?mine=true&limit=50")
        assert r.status_code == 200
        items = r.json()["items"]
        gids = {g["group_id"] for g in items}
        assert _state["pub_gid"] in gids
        assert _state["priv_gid"] in gids

    def test_group_detail(self, bob):
        gid = _state["pub_gid"]
        r = bob.get(f"{API}/groups/{gid}")
        assert r.status_code == 200
        d = r.json()
        assert d["group_id"] == gid
        assert d["is_member"] is True
        assert d["my_role"] == "owner"

    def test_group_detail_unknown_404(self, bob):
        r = bob.get(f"{API}/groups/grp_doesnotexist")
        assert r.status_code == 404

    def test_join_group_idempotent(self, alice):
        gid = _state["pub_gid"]
        r1 = alice.post(f"{API}/groups/{gid}/join")
        assert r1.status_code == 200
        d1 = r1.json()
        # First call may return joined=True OR already=True (depending on prior state)
        assert ("joined" in d1) or ("already" in d1)
        r2 = alice.post(f"{API}/groups/{gid}/join")
        assert r2.status_code == 200
        assert r2.json().get("already") is True

    def test_owner_cannot_leave(self, bob):
        r = bob.post(f"{API}/groups/{_state['pub_gid']}/leave")
        assert r.status_code == 400

    def test_member_can_leave_then_rejoin(self, alice):
        gid = _state["pub_gid"]
        r = alice.post(f"{API}/groups/{gid}/leave")
        assert r.status_code == 200
        assert r.json().get("left") is True
        # rejoin for further tests
        rj = alice.post(f"{API}/groups/{gid}/join")
        assert rj.status_code == 200

    def test_members_list(self, bob):
        gid = _state["pub_gid"]
        r = bob.get(f"{API}/groups/{gid}/members")
        assert r.status_code == 200
        items = r.json()["items"]
        roles = [m["role"] for m in items]
        assert "owner" in roles
        # alice should be a member
        assert any(m["role"] == "member" for m in items)

    def test_create_group_post_as_member(self, alice):
        gid = _state["pub_gid"]
        r = alice.post(f"{API}/groups/{gid}/posts", json={"text": "TEST_iter38 hello group"})
        assert r.status_code == 200, r.text
        _state["group_post_id"] = r.json()["post_id"]

    def test_create_group_post_non_member_403(self, bob):
        # Create a fresh private group with NO members other than owner; alice not in it; have alice attempt
        # Use the priv group (only bob is member). Alice tries to post.
        gid = _state["priv_gid"]
        s = _login(ALICE)
        r = s.post(f"{API}/groups/{gid}/posts", json={"text": "x"})
        assert r.status_code == 403

    def test_public_group_posts_visible_to_non_members(self, admin):
        gid = _state["pub_gid"]
        r = admin.get(f"{API}/groups/{gid}/posts")
        assert r.status_code == 200
        assert isinstance(r.json()["items"], list)

    def test_private_group_posts_403_for_outsider(self, alice):
        gid = _state["priv_gid"]
        r = alice.get(f"{API}/groups/{gid}/posts")
        assert r.status_code == 403

    def test_private_group_posts_ok_for_owner(self, bob):
        gid = _state["priv_gid"]
        r = bob.get(f"{API}/groups/{gid}/posts")
        assert r.status_code == 200


# =========================================================================
# PAGES
# =========================================================================
class TestPages:
    def test_create_page(self, bob):
        suffix = uuid.uuid4().hex[:6]
        r = bob.post(f"{API}/pages", json={
            "name": f"TEST_iter38 page {suffix}",
            "category": "brand",
            "description": "x",
        })
        assert r.status_code == 200, r.text
        _state["pid"] = r.json()["page_id"]

    def test_create_page_empty_name_400(self, bob):
        r = bob.post(f"{API}/pages", json={"name": "  "})
        assert r.status_code == 400

    def test_list_pages(self, alice):
        r = alice.get(f"{API}/pages?limit=50")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(p["page_id"] == _state["pid"] for p in items)

    def test_page_detail_owner(self, bob):
        r = bob.get(f"{API}/pages/{_state['pid']}")
        assert r.status_code == 200
        d = r.json()
        assert d["is_owner"] is True

    def test_page_detail_unknown_404(self, bob):
        r = bob.get(f"{API}/pages/pg_nope")
        assert r.status_code == 404

    def test_follow_unfollow(self, alice):
        pid = _state["pid"]
        r = alice.post(f"{API}/pages/{pid}/follow")
        assert r.status_code == 200
        # idempotent
        r2 = alice.post(f"{API}/pages/{pid}/follow")
        assert r2.status_code == 200
        assert r2.json().get("already") is True
        # unfollow
        u = alice.post(f"{API}/pages/{pid}/unfollow")
        assert u.status_code == 200

    def test_owner_can_post(self, bob):
        pid = _state["pid"]
        r = bob.post(f"{API}/pages/{pid}/posts", json={"text": "TEST_iter38 page post"})
        assert r.status_code == 200, r.text
        _state["page_post_id"] = r.json()["post_id"]

    def test_non_owner_cannot_post_403(self, alice):
        pid = _state["pid"]
        r = alice.post(f"{API}/pages/{pid}/posts", json={"text": "nope"})
        assert r.status_code == 403

    def test_page_posts_listing(self, alice):
        pid = _state["pid"]
        r = alice.get(f"{API}/pages/{pid}/posts")
        assert r.status_code == 200
        assert isinstance(r.json()["items"], list)


# =========================================================================
# SHARE delivery to groups/pages
# =========================================================================
class TestShareDelivery:
    @pytest.fixture(autouse=True)
    def _ensure_post(self, bob):
        if "src_post_id" not in _state:
            cr = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter38 src", "media": []})
            _state["src_post_id"] = cr.json()["post_id"]

    def test_share_to_group_member_ok(self, alice):
        gid = _state["pub_gid"]
        r = alice.post(
            f"{API}/feed/posts/{_state['src_post_id']}/share",
            json={"target": "group", "target_id": gid, "caption": "via share"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["shared"] is True
        assert d["target"] == "group"
        assert d.get("new_post_id", "").startswith("post_")

    def test_share_to_group_non_member_403(self, alice):
        # alice is not a member of priv_gid (only bob)
        gid = _state["priv_gid"]
        r = alice.post(
            f"{API}/feed/posts/{_state['src_post_id']}/share",
            json={"target": "group", "target_id": gid, "caption": ""},
        )
        assert r.status_code == 403

    def test_share_to_page_owner_ok(self, bob):
        r = bob.post(
            f"{API}/feed/posts/{_state['src_post_id']}/share",
            json={"target": "page", "target_id": _state["pid"], "caption": "ours"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["target"] == "page"

    def test_share_to_page_non_owner_403(self, alice):
        r = alice.post(
            f"{API}/feed/posts/{_state['src_post_id']}/share",
            json={"target": "page", "target_id": _state["pid"], "caption": ""},
        )
        assert r.status_code == 403

    def test_share_to_unknown_page_404(self, bob):
        r = bob.post(
            f"{API}/feed/posts/{_state['src_post_id']}/share",
            json={"target": "page", "target_id": "pg_unknownxxx", "caption": ""},
        )
        assert r.status_code == 404


# =========================================================================
# REACTIONS
# =========================================================================
class TestReactions:
    @pytest.fixture(autouse=True)
    def _ensure_post(self, bob):
        if "react_post_id" not in _state:
            cr = bob.post(f"{API}/feed/posts", json={"text": "TEST_iter38 react", "media": []})
            _state["react_post_id"] = cr.json()["post_id"]

    def test_react_emoji(self, alice):
        pid = _state["react_post_id"]
        r = alice.post(f"{API}/feed/posts/{pid}/react", json={"emoji": "🔥"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["reacted"] is True
        assert d["counts"].get("🔥", 0) >= 1

    def test_react_replaces_previous(self, alice):
        pid = _state["react_post_id"]
        r = alice.post(f"{API}/feed/posts/{pid}/react", json={"emoji": "❤️"})
        assert r.status_code == 200
        d = r.json()
        # Old emoji should now have 0 (or absent) for alice -> the only previous reactor
        assert d["counts"].get("❤️", 0) >= 1
        assert d["counts"].get("🔥", 0) == 0  # alice's 🔥 removed

    def test_unsupported_emoji_400(self, alice):
        pid = _state["react_post_id"]
        r = alice.post(f"{API}/feed/posts/{pid}/react", json={"emoji": "💩"})
        assert r.status_code == 400

    def test_get_reactions(self, alice):
        pid = _state["react_post_id"]
        r = alice.get(f"{API}/feed/posts/{pid}/reactions")
        assert r.status_code == 200
        d = r.json()
        assert "counts" in d
        assert d["my_emoji"] in ("❤️", "🔥", "😂", "😮", "😢", "👏", None)

    def test_delete_reaction(self, alice):
        pid = _state["react_post_id"]
        r = alice.delete(f"{API}/feed/posts/{pid}/react")
        assert r.status_code == 200
        # verify
        g = alice.get(f"{API}/feed/posts/{pid}/reactions").json()
        assert g["my_emoji"] is None


# =========================================================================
# ADMIN STATS
# =========================================================================
class TestAdminStats:
    def test_overview_admin_only(self, bob):
        r = bob.get(f"{API}/admin/stats/overview")
        assert r.status_code in (401, 403)

    def test_overview(self, admin):
        r = admin.get(f"{API}/admin/stats/overview")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "totals" in d and "last_24h" in d
        for key in ["total_users", "total_posts", "total_reels", "total_stories",
                    "total_likes", "total_comments", "total_shares",
                    "total_groups", "total_pages"]:
            assert key in d["totals"], f"missing {key}"

    @pytest.mark.parametrize("period", ["day", "week", "month", "year"])
    def test_content_periods(self, admin, period):
        r = admin.get(f"{API}/admin/stats/content?period={period}")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "series" in d and "total" in d

    def test_content_invalid_period_400(self, admin):
        r = admin.get(f"{API}/admin/stats/content?period=bogus")
        assert r.status_code == 400

    def test_engagement(self, admin):
        r = admin.get(f"{API}/admin/stats/engagement?period=month")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["likes", "comments", "shares", "tips_usd", "active_creators", "active_commenters"]:
            assert k in d["totals"]
        assert isinstance(d["likes_series"], list)

    def test_top(self, admin):
        r = admin.get(f"{API}/admin/stats/top?period=month&limit=5")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["top_posts", "top_reels", "top_creators"]:
            assert k in d
            assert isinstance(d[k], list)
