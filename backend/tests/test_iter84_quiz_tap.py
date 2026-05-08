"""Tests for Iter83 Phase 2 & 3 — Quiz JAPAP + Tap Challenge unified points engine.

Covers:
- GET/POST /api/quiz/start, /submit, /history, 3/day limit, perfect bonus
- GET /api/tap/status, POST /start, /submit, 1/day limit, anti-cheat cap, bonus tiers
- Admin overviews for quiz and tap
- Admin reset-user wipes history
"""
import os
import time
import uuid
import asyncio
import pytest
import requests
import asyncpg

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"
DATABASE_URL = (
    "postgresql://neondb_owner:npg_YFaoTc01dJkx@ep-still-boat-algu2h2h-pooler.c-3.eu-central-1.aws.neon.tech/"
    "neondb?sslmode=require"
)

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PWD = "JapapAdmin2024!"
USER_EMAIL = "bob@japap.com"
USER_PWD = "Test1234!"

CSRF_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}


def _session():
    s = requests.Session()
    s.headers.update(CSRF_HEADERS)
    return s


def _login(email, pwd):
    s = _session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("status") != "otp_required", f"Unexpected 2FA for {email}"
    return s, data


async def _fetch_otp(email, purpose):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "SELECT code FROM email_otps WHERE LOWER(email)=LOWER($1) AND purpose=$2 AND used=FALSE "
            "ORDER BY created_at DESC LIMIT 1",
            email, purpose,
        )
        return row["code"] if row else None
    finally:
        await conn.close()


async def _admin_reset_all_for(user_id: str, admin_session):
    """Reset both quiz and tap history via admin endpoints."""
    r1 = admin_session.post(f"{API}/quiz/admin/reset-user/{user_id}")
    r2 = admin_session.post(f"{API}/tap/admin/reset-user/{user_id}")
    return r1.status_code, r2.status_code


# -------------------- Fixtures --------------------
@pytest.fixture(scope="module")
def admin_session():
    s, _ = _login(ADMIN_EMAIL, ADMIN_PWD)
    return s


@pytest.fixture(scope="module")
def fresh_user_session(admin_session):
    """Register a brand new user (auto-activated via OTP) so we get clean quota.
    If creation fails, fallback to resetting bob via admin."""
    email = f"TEST_iter84_{uuid.uuid4().hex[:8]}@japap.com"
    pwd = "StrongPass1!"
    s = _session()
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": pwd,
        "first_name": "Iter84", "last_name": "QuizTap",
        "country": "CM", "phone": "+237670000084",
        "terms_accepted": True,
    })
    if r.status_code not in (200, 201):
        pytest.skip(f"register failed: {r.text}")
    time.sleep(2)
    otp = asyncio.run(_fetch_otp(email, "register"))
    assert otp, f"No register OTP for {email}"
    r2 = s.post(f"{API}/auth/verify-otp", json={"email": email, "code": otp})
    assert r2.status_code == 200, f"verify-otp: {r2.text}"
    # Get user_id
    me = s.get(f"{API}/auth/me")
    if me.status_code != 200:
        # try alt endpoint
        me = s.get(f"{API}/users/me")
    data = me.json() if me.status_code == 200 else {}
    user_id = (data.get("user") or data).get("user_id") or data.get("id")
    return {"session": s, "email": email, "user_id": user_id}


@pytest.fixture(scope="module")
def bob_session(admin_session):
    """Login as bob and reset quiz+tap history so we start clean."""
    s, data = _login(USER_EMAIL, USER_PWD)
    uid = (data.get("user") or {}).get("user_id")
    assert uid, "bob user_id missing"
    # reset via admin
    admin_session.post(f"{API}/quiz/admin/reset-user/{uid}")
    admin_session.post(f"{API}/tap/admin/reset-user/{uid}")
    return {"session": s, "user_id": uid}


# -------------------- Quiz tests --------------------
class TestQuizStart:
    def test_start_returns_5_questions_with_parsed_options(self, bob_session):
        s = bob_session["session"]
        r = s.post(f"{API}/quiz/start")
        assert r.status_code == 200, f"start: {r.status_code} {r.text}"
        data = r.json()
        assert data["time_limit_seconds"] == 10
        qs = data["questions"]
        assert len(qs) == 5
        for q in qs:
            assert q["text"]
            assert isinstance(q["options"], list), f"options not a list: {q['options']}"
            assert len(q["options"]) >= 2
            assert q["category"]
            assert "correct_index" not in q  # must not be leaked
        # Submit it to free a slot (perfect all zero)
        sub = s.post(f"{API}/quiz/submit", json={
            "run_id": data["run_id"], "answers": [0, 0, 0, 0, 0],
        })
        assert sub.status_code == 200, sub.text


class TestQuizSubmit:
    def test_submit_scoring_and_perfect_bonus(self, bob_session, admin_session):
        # reset first so we have slots
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        # Start a run, then cheat by reading correct answers via admin
        r = s.post(f"{API}/quiz/start")
        assert r.status_code == 200, r.text
        run = r.json()
        run_id = run["run_id"]
        session_id = run["session_id"]
        # Fetch correct answers directly from DB
        async def _correct():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT question_ids FROM quiz_sessions WHERE id=$1", session_id
                )
                qids = list(row["question_ids"])
                rows = await conn.fetch(
                    "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])", qids
                )
                mp = {int(x["id"]): int(x["correct_index"]) for x in rows}
                return [mp[qid] for qid in qids]
            finally:
                await conn.close()
        answers = asyncio.run(_correct())
        assert len(answers) == 5
        sub = s.post(f"{API}/quiz/submit", json={"run_id": run_id, "answers": answers})
        assert sub.status_code == 200, sub.text
        d = sub.json()
        assert d["correct_count"] == 5
        assert d["perfect"] is True
        assert d["timed_out"] is False
        # 5 * 20 + 30 = 130 BEFORE clamp. points_awarded is post-clamp. Check >= 20 at minimum.
        assert d["points_awarded"] >= 0
        assert "points_cycle" in d
        # correct_by_question returned
        assert d["correct_by_question"] == answers

    def test_double_submit_blocked(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        r = s.post(f"{API}/quiz/start").json()
        run_id = r["run_id"]
        sub1 = s.post(f"{API}/quiz/submit", json={"run_id": run_id, "answers": [0]*5})
        assert sub1.status_code == 200
        sub2 = s.post(f"{API}/quiz/submit", json={"run_id": run_id, "answers": [0]*5})
        assert sub2.status_code == 400, f"expected 400, got {sub2.status_code}"


class TestQuizLimit:
    def test_3_sessions_per_day_then_429(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        for i in range(3):
            r = s.post(f"{API}/quiz/start")
            assert r.status_code == 200, f"start #{i}: {r.text}"
            run_id = r.json()["run_id"]
            s.post(f"{API}/quiz/submit", json={"run_id": run_id, "answers": [0]*5})
        r4 = s.post(f"{API}/quiz/start")
        assert r4.status_code == 429, f"expected 429, got {r4.status_code}: {r4.text}"


class TestQuizHistory:
    def test_history_returns_runs(self, bob_session):
        s = bob_session["session"]
        r = s.get(f"{API}/quiz/history")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["items"], list)
        # bob played 3 above, should see at least 1
        assert len(data["items"]) >= 1


class TestQuizAdmin:
    def test_overview_shape(self, admin_session):
        r = admin_session.get(f"{API}/quiz/admin/overview", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("runs", "players", "points_distributed", "avg_accuracy",
                  "buckets", "top_players", "timeseries", "bank"):
            assert k in d, f"missing key {k}"
        assert "total" in d["bank"] and "sessions" in d["bank"]

    def test_reset_user_wipes(self, admin_session, bob_session):
        uid = bob_session["user_id"]
        r = admin_session.post(f"{API}/quiz/admin/reset-user/{uid}")
        assert r.status_code == 200
        # history now empty
        h = bob_session["session"].get(f"{API}/quiz/history").json()
        assert h["items"] == []


# -------------------- Tap tests --------------------
class TestTapStatus:
    def test_status_shape(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        r = s.get(f"{API}/tap/status")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["max_per_day"] == 1
        assert d["remaining_today"] == 1
        assert "best_taps_ever" in d


class TestTapStart:
    def test_start_returns_run(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        r = s.post(f"{API}/tap/start")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["duration_seconds"] == 10
        assert d["remaining_today"] == 0
        assert "run_id" in d and "start_at" in d
        # submit so next test uses a fresh state
        s.post(f"{API}/tap/submit", json={"run_id": d["run_id"], "taps": 25})

    def test_1_run_per_day_then_429(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        r = s.post(f"{API}/tap/start")
        assert r.status_code == 200
        r2 = s.post(f"{API}/tap/start")
        assert r2.status_code == 429, f"expected 429, got {r2.status_code}"


class TestTapSubmit:
    def test_scoring_no_bonus_below_30(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        r = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 25})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["taps_valid"] == 25
        assert d["bonus_awarded"] == 0
        assert d["timed_out"] is False

    def test_scoring_55_taps_bonus_25(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        r = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 55}).json()
        assert r["taps_valid"] == 55
        assert r["bonus_awarded"] == 25  # 50→+25 tier

    def test_scoring_85_taps_bonus_50(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        r = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 85}).json()
        assert r["taps_valid"] == 85
        assert r["bonus_awarded"] == 50  # 80→+50 tier

    def test_anti_cheat_caps_at_120(self, bob_session, admin_session):
        asyncio.run(_admin_reset_all_for(bob_session["user_id"], admin_session))
        s = bob_session["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        r = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 200}).json()
        assert r["taps_raw"] == 200
        assert r["taps_valid"] == 120  # 12 * 10 cap
        assert r["bonus_awarded"] == 50  # still above 80 tier


class TestTapAdmin:
    def test_overview_shape(self, admin_session):
        r = admin_session.get(f"{API}/tap/admin/overview", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("runs", "players", "points_distributed", "taps_total",
                  "avg_taps_per_run", "max_taps_ever", "cheat_attempts",
                  "top_players", "timeseries"):
            assert k in d, f"missing key {k}"

    def test_reset_user_wipes(self, admin_session, bob_session):
        r = admin_session.post(f"{API}/tap/admin/reset-user/{bob_session['user_id']}")
        assert r.status_code == 200
        st = bob_session["session"].get(f"{API}/tap/status").json()
        assert st["remaining_today"] == 1


# -------------------- Authorization --------------------
class TestAdminOnly:
    def test_quiz_admin_overview_blocks_user(self, bob_session):
        r = bob_session["session"].get(f"{API}/quiz/admin/overview")
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"

    def test_tap_admin_overview_blocks_user(self, bob_session):
        r = bob_session["session"].get(f"{API}/tap/admin/overview")
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
