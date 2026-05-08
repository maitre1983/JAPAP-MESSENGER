"""
iter107 — Backend tests for:
- Admin Quiz/Tap config endpoints (GET/PUT /api/admin/games/{quiz,tap})
- Quiz + Tap integration with admin_settings (enabled, sessions/day, timer, thresholds)
- Unified games leaderboard with global/country segmentation + me ranks
- Referrals leaderboard with global/country segmentation + me ranks

Credentials (see /app/memory/test_credentials.md):
  Admin: admin@japap.com / JapapAdmin2024!
  Bob:   bob@japap.com   / Test1234!
  Alice: alice@japap.com / Alice2026!
"""
from __future__ import annotations
import os
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
import requests
from dotenv import load_dotenv as _load_env

_load_env("/app/frontend/.env")
_load_env("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL missing"
JWT_SECRET = os.environ.get("JWT_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

ADMIN_EMAIL = "admin@japap.com"
BOB_EMAIL = "bob@japap.com"
ALICE_EMAIL = "alice@japap.com"


def _mint_token(user_id: str, email: str, minutes: int = 120) -> str:
    import jwt
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "email": email, "type": "access",
         "iat": int(now.timestamp()), "exp": now + timedelta(minutes=minutes)},
        JWT_SECRET, algorithm="HS256",
    )


async def _mint_for_email(email: str) -> tuple[str, str]:
    import asyncpg
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        u = await conn.fetchrow("SELECT user_id, email FROM users WHERE email=$1", email)
        if not u:
            return "", ""
        return u["user_id"], _mint_token(u["user_id"], u["email"])
    finally:
        await conn.close()


def _login(email: str, _pwd: str | None = None) -> requests.Session:
    uid, tok = asyncio.run(_mint_for_email(email))
    if not tok:
        pytest.skip(f"user {email} not in DB")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    s.user_id = uid  # type: ignore[attr-defined]
    return s


@pytest.fixture(scope="module")
def admin_sess():
    return _login(ADMIN_EMAIL)


@pytest.fixture(scope="module")
def bob_sess():
    return _login(BOB_EMAIL)


@pytest.fixture(scope="module")
def alice_sess():
    return _login(ALICE_EMAIL)


@pytest.fixture(scope="module")
def anon_sess():
    return requests.Session()


# -------------------------- Admin config — Quiz --------------------------

class TestAdminQuizConfig:
    def test_get_requires_admin(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/admin/games/quiz", timeout=20)
        assert r.status_code == 403, r.text

    def test_get_admin_ok(self, admin_sess):
        r = admin_sess.get(f"{BASE_URL}/api/admin/games/quiz", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "config" in body and "defaults" in body and "bounds" in body
        cfg = body["config"]
        for k in ("quiz_enabled", "quiz_sessions_per_day", "quiz_timer_seconds",
                  "quiz_points_per_correct", "quiz_perfect_bonus", "quiz_session_size"):
            assert k in cfg
        assert isinstance(cfg["quiz_enabled"], bool)
        assert isinstance(cfg["quiz_sessions_per_day"], int)
        b = body["bounds"]
        assert b["quiz_sessions_per_day"] == [1, 50] or tuple(b["quiz_sessions_per_day"]) == (1, 50)
        assert b["quiz_timer_seconds"] == [5, 60] or tuple(b["quiz_timer_seconds"]) == (5, 60)

    def test_put_rejects_non_admin(self, bob_sess):
        r = bob_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                         json={"quiz_sessions_per_day": 5}, timeout=20)
        assert r.status_code == 403

    def test_put_out_of_bounds(self, admin_sess):
        r = admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"quiz_sessions_per_day": 999}, timeout=20)
        assert r.status_code == 400, r.text
        detail = (r.json().get("detail") or "").lower()
        assert "entre" in detail and "1" in detail and "50" in detail

    def test_put_unknown_key_rejected(self, admin_sess):
        # Pydantic strips unknown fields by default → empty payload → 400
        r = admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"totally_fake_key": 42}, timeout=20)
        assert r.status_code == 400

    def test_put_partial_update_and_persistence(self, admin_sess):
        # GET baseline
        r0 = admin_sess.get(f"{BASE_URL}/api/admin/games/quiz", timeout=20)
        original = r0.json()["config"]
        try:
            new_val = 7 if original["quiz_sessions_per_day"] != 7 else 6
            r = admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                               json={"quiz_sessions_per_day": new_val}, timeout=20)
            assert r.status_code == 200, r.text
            cfg = r.json()["config"]
            assert cfg["quiz_sessions_per_day"] == new_val
            # Verify persistence via GET
            r2 = admin_sess.get(f"{BASE_URL}/api/admin/games/quiz", timeout=20)
            assert r2.json()["config"]["quiz_sessions_per_day"] == new_val
        finally:
            # Revert to original
            admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"quiz_sessions_per_day": int(original["quiz_sessions_per_day"])},
                           timeout=20)

    def test_put_bool_roundtrip(self, admin_sess):
        r0 = admin_sess.get(f"{BASE_URL}/api/admin/games/quiz", timeout=20)
        original_enabled = r0.json()["config"]["quiz_enabled"]
        try:
            r = admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                               json={"quiz_enabled": False}, timeout=20)
            assert r.status_code == 200
            assert r.json()["config"]["quiz_enabled"] is False
            r2 = admin_sess.get(f"{BASE_URL}/api/admin/games/quiz", timeout=20)
            assert r2.json()["config"]["quiz_enabled"] is False
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"quiz_enabled": bool(original_enabled)}, timeout=20)


# -------------------------- Admin config — Tap --------------------------

class TestAdminTapConfig:
    def test_get_admin_ok(self, admin_sess):
        r = admin_sess.get(f"{BASE_URL}/api/admin/games/tap", timeout=20)
        assert r.status_code == 200
        body = r.json()
        cfg = body["config"]
        for k in ("tap_enabled", "tap_sessions_per_day", "tap_duration_seconds",
                  "tap_max_taps_per_second", "tap_reward_thresholds"):
            assert k in cfg
        assert isinstance(cfg["tap_reward_thresholds"], list)
        for t in cfg["tap_reward_thresholds"]:
            assert "taps" in t and "reward" in t

    def test_get_requires_admin(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/admin/games/tap", timeout=20)
        assert r.status_code == 403

    def test_put_thresholds_malformed_rejected(self, admin_sess):
        # Missing 'reward' on one entry
        r = admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                           json={"tap_reward_thresholds": [{"taps": 40}]}, timeout=20)
        assert r.status_code == 400, r.text

    def test_put_thresholds_must_be_list(self, admin_sess):
        r = admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                           json={"tap_reward_thresholds": {"taps": 10, "reward": 1}}, timeout=20)
        # Pydantic will reject non-list for the list type (422) OR our handler (400).
        assert r.status_code in (400, 422)

    def test_put_thresholds_ok_and_revert(self, admin_sess):
        r0 = admin_sess.get(f"{BASE_URL}/api/admin/games/tap", timeout=20)
        original_thr = r0.json()["config"]["tap_reward_thresholds"]
        try:
            new_thr = [{"taps": 40, "reward": 2}, {"taps": 90, "reward": 6}]
            r = admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                               json={"tap_reward_thresholds": new_thr}, timeout=20)
            assert r.status_code == 200, r.text
            got = r.json()["config"]["tap_reward_thresholds"]
            # Sorted asc by taps per service contract
            assert got[0]["taps"] == 40 and got[0]["reward"] == 2
            assert got[-1]["taps"] == 90 and got[-1]["reward"] == 6
            r2 = admin_sess.get(f"{BASE_URL}/api/admin/games/tap", timeout=20)
            got2 = r2.json()["config"]["tap_reward_thresholds"]
            assert got2[0]["taps"] == 40 and got2[-1]["taps"] == 90
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                           json={"tap_reward_thresholds": original_thr}, timeout=20)

    def test_put_out_of_bounds_tap(self, admin_sess):
        r = admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                           json={"tap_sessions_per_day": 9999}, timeout=20)
        assert r.status_code == 400
        assert "entre" in (r.json().get("detail") or "").lower()


# -------------------------- Quiz integration --------------------------

class TestQuizIntegration:
    def test_start_respects_enabled_flag(self, admin_sess, bob_sess):
        # Disable quiz
        admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                       json={"quiz_enabled": False}, timeout=20)
        try:
            r = bob_sess.post(f"{BASE_URL}/api/quiz/start", timeout=20)
            # Backend may return 503 from ensure_game_enabled (which reads quiz_enabled)
            # or from our cfg gate. Both are acceptable.
            assert r.status_code == 503, f"Expected 503 when disabled, got {r.status_code}: {r.text[:200]}"
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"quiz_enabled": True}, timeout=20)

    def test_start_returns_admin_timer(self, admin_sess, bob_sess):
        # set timer to 15s
        admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                       json={"quiz_timer_seconds": 15}, timeout=20)
        try:
            # Flush bob's runs to ensure he can start
            admin_sess.post(f"{BASE_URL}/api/quiz/admin/reset-user/{self._get_uid(bob_sess)}", timeout=20)
            r = bob_sess.post(f"{BASE_URL}/api/quiz/start", timeout=30)
            if r.status_code == 503 and "session" in (r.json().get("detail") or "").lower():
                pytest.skip("No quiz sessions seeded in the DB")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["time_limit_seconds"] == 15
            assert len(body["questions"]) == 5
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"quiz_timer_seconds": 10}, timeout=20)

    def test_start_respects_sessions_per_day_limit(self, admin_sess, bob_sess):
        uid = self._get_uid(bob_sess)
        # Set limit to 1
        admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                       json={"quiz_sessions_per_day": 1}, timeout=20)
        try:
            admin_sess.post(f"{BASE_URL}/api/quiz/admin/reset-user/{uid}", timeout=20)
            r1 = bob_sess.post(f"{BASE_URL}/api/quiz/start", timeout=30)
            if r1.status_code == 503 and "session" in (r1.json().get("detail") or "").lower():
                pytest.skip("No quiz sessions seeded in the DB")
            assert r1.status_code == 200, r1.text
            r2 = bob_sess.post(f"{BASE_URL}/api/quiz/start", timeout=20)
            assert r2.status_code == 429, r2.text
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/quiz",
                           json={"quiz_sessions_per_day": 3}, timeout=20)
            admin_sess.post(f"{BASE_URL}/api/quiz/admin/reset-user/{uid}", timeout=20)

    @staticmethod
    def _get_uid(sess: requests.Session) -> str:
        uid = getattr(sess, "user_id", "") or ""
        if not uid:
            pytest.skip("cannot resolve uid for session")
        return uid


# -------------------------- Tap integration --------------------------

class TestTapIntegration:
    def test_start_respects_enabled_flag(self, admin_sess, bob_sess):
        admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                       json={"tap_enabled": False}, timeout=20)
        try:
            r = bob_sess.post(f"{BASE_URL}/api/tap/start", timeout=20)
            assert r.status_code == 503
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                           json={"tap_enabled": True}, timeout=20)

    def test_start_returns_admin_duration(self, admin_sess, alice_sess):
        admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                       json={"tap_duration_seconds": 15}, timeout=20)
        try:
            r = alice_sess.post(f"{BASE_URL}/api/tap/start", timeout=20)
            if r.status_code == 429:
                pytest.skip("Alice already played tap today — not flushing (no admin reset endpoint safe)")
            assert r.status_code == 200, r.text
            assert r.json()["duration_seconds"] == 15
        finally:
            admin_sess.put(f"{BASE_URL}/api/admin/games/tap",
                           json={"tap_duration_seconds": 10}, timeout=20)

    def test_submit_caps_and_flags_cheated(self, admin_sess, bob_sess):
        # Reset bob's tap history via admin endpoint
        uid = TestQuizIntegration._get_uid(bob_sess)
        admin_sess.post(f"{BASE_URL}/api/tap/admin/reset-user/{uid}", timeout=20)
        # Configure: duration=10, cap=12/s (default), thresholds custom
        admin_sess.put(f"{BASE_URL}/api/admin/games/tap", json={
            "tap_duration_seconds": 10,
            "tap_max_taps_per_second": 12,
            "tap_reward_thresholds": [
                {"taps": 30, "reward": 2},
                {"taps": 60, "reward": 5},
                {"taps": 100, "reward": 10},
            ],
        }, timeout=20)
        try:
            rs = bob_sess.post(f"{BASE_URL}/api/tap/start", timeout=20)
            assert rs.status_code == 200, rs.text
            run_id = rs.json()["run_id"]
            # Submit 9999 → ceiling is 120 (12*10), cheated=True, matches tier 100 → bonus 10
            rsub = bob_sess.post(f"{BASE_URL}/api/tap/submit",
                                 json={"run_id": run_id, "taps": 9999}, timeout=20)
            assert rsub.status_code == 200, rsub.text
            body = rsub.json()
            assert body["taps_raw"] == 9999
            assert body["taps_valid"] == 120, body
            assert body["cheated"] is True
            assert body["bonus_awarded"] == 10, body
        finally:
            admin_sess.post(f"{BASE_URL}/api/tap/admin/reset-user/{uid}", timeout=20)


# -------------------------- Games leaderboard --------------------------

class TestGamesLeaderboard:
    def test_requires_auth(self, anon_sess):
        r = anon_sess.get(f"{BASE_URL}/api/games/leaderboard", timeout=20)
        assert r.status_code in (401, 403)

    def test_default_scope_global(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/games/leaderboard", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("scope") == "global"
        assert "items" in body and isinstance(body["items"], list)
        assert "me" in body

    def test_scope_country_defaults_to_user_country(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/games/leaderboard?scope=country", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("scope") == "country"
        # country should be populated if user has a country_code
        # (allowed to be empty if CM has no country, but 'me' should expose the code)
        assert "me" in body
        me = body["me"] or {}
        # me structure has rank_global and rank_country
        assert "rank_global" in me or "rank" in me
        assert "country_code" in me

    def test_game_filter_quiz(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/games/leaderboard?game=quiz&period=7d", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("game") == "quiz"
        assert body.get("period") == "7d"

    def test_items_shape(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/games/leaderboard?limit=5", timeout=20)
        assert r.status_code == 200
        items = r.json().get("items", [])
        for it in items:
            for k in ("rank", "user_id", "name", "total", "plays", "country_code"):
                assert k in it, f"missing key {k} in {it}"


# -------------------------- Referrals leaderboard --------------------------

class TestReferralsLeaderboard:
    def test_requires_auth(self, anon_sess):
        r = anon_sess.get(f"{BASE_URL}/api/referrals/leaderboard", timeout=20)
        assert r.status_code in (401, 403)

    def test_default_global(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/referrals/leaderboard", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "enabled" in body
        assert "leaders" in body
        assert body.get("scope", "global") == "global"
        assert "me" in body

    def test_scope_country_default_country(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/referrals/leaderboard?scope=country", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("scope") == "country"
        assert "me" in body
        me = body["me"] or {}
        assert "rank_global" in me or "rank" in me
        assert "country_code" in me

    def test_me_dual_rank(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/referrals/leaderboard?scope=global", timeout=20)
        body = r.json()
        me = body.get("me") or {}
        # Both rank_global and rank_country should be present
        assert "rank_global" in me
        assert "rank_country" in me
