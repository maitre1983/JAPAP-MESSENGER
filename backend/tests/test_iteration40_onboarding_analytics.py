"""
Iteration 40 — Groups Onboarding + Analytics + infra regression.

Scope (per review_request):
  - GET  /api/groups/suggestions (ranking, limit validation, country/language)
  - POST /api/groups/join-all (atomic, idempotent)
  - POST /api/auth/onboarding/complete (idempotent)
  - GET  /api/auth/me exposes onboarding_completed
  - POST /api/auth/login response includes user.onboarding_completed
  - POST /api/analytics/event (auth required, name ≤80)
  - GET  /api/analytics/summary (admin-only, top_events / days)
  - Regression: /groups, /pages, /admin/stats/overview, /feed/stories, /feed
"""
import os
import subprocess
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
BOB = {"email": "bob@japap.com", "password": "Test1234!"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


def _reset_bob_onboarding():
    subprocess.run(
        ["sudo", "-u", "postgres", "psql", "japap_messenger", "-c",
         "UPDATE users SET onboarding_completed_at=NULL WHERE email='bob@japap.com';"],
        check=False, capture_output=True,
    )


def _leave_bob_groups():
    subprocess.run(
        ["sudo", "-u", "postgres", "psql", "japap_messenger", "-c",
         "DELETE FROM group_members WHERE user_id IN (SELECT user_id FROM users WHERE email='bob@japap.com');"],
        check=False, capture_output=True,
    )


@pytest.fixture(scope="module")
def bob_session():
    s = requests.Session()
    _reset_bob_onboarding()
    _leave_bob_groups()
    r = s.post(f"{BASE_URL}/api/auth/login", json=BOB, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()


# =====================================================================
# AUTH: onboarding flag in login + /me + onboarding/complete
# =====================================================================
class TestAuthOnboarding:
    def test_login_response_contains_onboarding_flag(self, bob_session):
        _, payload = bob_session
        assert "user" in payload and "onboarding_completed" in payload["user"], payload
        assert payload["user"]["onboarding_completed"] is False

    def test_me_onboarding_flag_false_initially(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/auth/me", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json().get("onboarding_completed") is False

    def test_onboarding_complete_sets_flag(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/auth/onboarding/complete", json={}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json().get("onboarding_completed") is True
        r2 = s.get(f"{BASE_URL}/api/auth/me", timeout=10)
        assert r2.json().get("onboarding_completed") is True

    def test_onboarding_complete_is_idempotent(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/auth/onboarding/complete", json={}, timeout=10)
        assert r.status_code == 200
        assert r.json().get("onboarding_completed") is True


# =====================================================================
# GROUPS: suggestions + join-all
# =====================================================================
class TestGroupsOnboarding:
    def test_suggestions_returns_items_and_country_language(self, bob_session):
        # Reset bob so no memberships bias the list
        _leave_bob_groups()
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/groups/suggestions?limit=5", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "items" in data and "country" in data and "language" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) <= 5
        # 5 seeded public groups should surface for bob
        assert len(data["items"]) == 5
        for it in data["items"]:
            assert it["is_member"] is False
            for k in ("group_id", "name", "members_count", "posts_count"):
                assert k in it

    def test_suggestions_limit_validation(self, bob_session):
        s, _ = bob_session
        r0 = s.get(f"{BASE_URL}/api/groups/suggestions?limit=0", timeout=10)
        assert r0.status_code == 422, r0.text
        r11 = s.get(f"{BASE_URL}/api/groups/suggestions?limit=11", timeout=10)
        assert r11.status_code == 422, r11.text

    def test_join_all_atomic_and_idempotent(self, bob_session):
        _leave_bob_groups()
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/groups/join-all", json={}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "joined_count" in data and "group_ids" in data
        assert data["joined_count"] == 5
        assert len(data["group_ids"]) == 5

        # Idempotent second call
        r2 = s.post(f"{BASE_URL}/api/groups/join-all", json={}, timeout=15)
        assert r2.status_code == 200, r2.text
        assert r2.json().get("joined_count") == 0
        assert r2.json().get("group_ids") == []

        # Suggestions should now exclude joined groups
        r3 = s.get(f"{BASE_URL}/api/groups/suggestions?limit=5", timeout=10)
        assert r3.status_code == 200
        assert r3.json().get("items") == []

    def test_suggestions_requires_auth(self):
        s = requests.Session()
        r = s.get(f"{BASE_URL}/api/groups/suggestions?limit=5", timeout=10)
        assert r.status_code == 401


# =====================================================================
# ANALYTICS
# =====================================================================
class TestAnalytics:
    def test_event_requires_auth(self):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/analytics/event",
                   json={"name": "TEST_unauth"}, timeout=10)
        assert r.status_code == 401

    def test_event_post_ok(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/analytics/event",
                   json={"name": "TEST_iter40_view", "props": {"src": "pytest"}},
                   timeout=10)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    def test_event_name_too_long_returns_400(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/analytics/event",
                   json={"name": "x" * 81}, timeout=10)
        assert r.status_code == 400, r.text

    def test_event_empty_name_returns_422_or_400(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/analytics/event", json={}, timeout=10)
        # pydantic v2 -> 422; our custom check -> 400
        assert r.status_code in (400, 422), r.text

    def test_summary_admin_only(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/analytics/summary", timeout=10)
        assert r.status_code in (401, 403), r.text

    def test_summary_admin_ok(self, admin_session, bob_session):
        # Seed at least one event by bob (already done above)
        b, _ = bob_session
        b.post(f"{BASE_URL}/api/analytics/event",
               json={"name": "TEST_iter40_view2"}, timeout=10)
        s, _ = admin_session
        r = s.get(f"{BASE_URL}/api/analytics/summary", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "top_events" in body
        assert isinstance(body["top_events"], list)

    def test_summary_with_name_filter(self, admin_session):
        s, _ = admin_session
        r = s.get(f"{BASE_URL}/api/analytics/summary?name=TEST_iter40_view", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body.get("name") == "TEST_iter40_view"
        assert "days" in body and isinstance(body["days"], list)


# =====================================================================
# REGRESSION — infra recovery
# =====================================================================
class TestRegression:
    def test_groups_list_ok(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/groups", timeout=10)
        assert r.status_code == 200
        assert "items" in r.json()

    def test_pages_list_ok(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/pages", timeout=10)
        assert r.status_code == 200
        assert "items" in r.json()

    def test_admin_stats_overview_ok(self, admin_session):
        s, _ = admin_session
        r = s.get(f"{BASE_URL}/api/admin/stats/overview", timeout=15)
        assert r.status_code == 200, r.text

    def test_feed_stories_returns_200(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/feed/stories", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        # iter39 bug fix — must not 500 on empty story_views
        assert isinstance(body, (list, dict))

    def test_feed_timeline_or_posts_ok(self, bob_session):
        s, _ = bob_session
        # Try common feed endpoints; must at least not 500
        for path in ("/api/posts/feed", "/api/feed/timeline", "/api/posts"):
            r = s.get(f"{BASE_URL}{path}", timeout=15)
            if r.status_code != 404:
                assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
                return
        pytest.skip("No feed endpoint matched (not in Sprint 39 scope)")
