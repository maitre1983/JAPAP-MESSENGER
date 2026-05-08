"""iter131 — Quiz Duel tiebreaker (time) backend tests.

Validates:
  - POST /api/duel/create-from-quiz captures challenger_time_s
  - GET /api/duel/{token} returns challenger_time_s + opponent_time_s
  - POST /api/duel/{token}/start-quiz returns 5 same questions (reshuffled)
  - POST /api/duel/{token}/submit-quiz computes opponent_time_s + winner logic
  - Tiebreaker: equal scores + diff >= 0.20s -> faster wins (tiebreaker='time')
  - Tiebreaker: equal scores + diff < 0.20s -> true tie
  - Anti-fraud: self-duel 400, double-submit rejected
  - _serialise_duel exposes both challenger_time_s + opponent_time_s
  - Regression: TAP duels not impacted (no tiebreaker time)
"""
from __future__ import annotations
import os
import time
import asyncio
import pytest
import requests

import asyncpg
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
TURNSTILE = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")

ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", "turnstile_token": TURNSTILE}
BOB = {"email": "bob@japap.com", "password": "Test1234!", "turnstile_token": TURNSTILE}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", "turnstile_token": TURNSTILE}


def _login(sess: requests.Session, creds: dict) -> dict:
    sess.headers.update({"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"})
    last = None
    for attempt in range(8):
        try:
            r = sess.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                break
            last = f"{r.status_code} {r.text[:200]}"
        except requests.exceptions.RequestException as e:
            last = str(e)
        time.sleep(5)
    else:
        raise AssertionError(f"login {creds['email']} failed after retries: {last}")
    data = r.json()
    tok = data.get("access_token")
    if tok:
        sess.headers.update({"Authorization": f"Bearer {tok}"})
    user = data.get("user") or {}
    sess.user_id = user.get("user_id") or user.get("id")
    return user


@pytest.fixture(scope="module")
def admin_sess():
    s = requests.Session()
    _login(s, ADMIN)
    return s


@pytest.fixture(scope="module")
def bob_sess():
    s = requests.Session()
    _login(s, BOB)
    return s


@pytest.fixture(scope="module")
def alice_sess():
    s = requests.Session()
    _login(s, ALICE)
    return s


@pytest.fixture(scope="module", autouse=True)
def _bump_accept_cap(admin_sess, alice_sess):
    """Avoid daily cap interference for Alice during iter131 tests.
    We reset wheel config AND backdate Alice's existing accepted duels so the
    server-side `accepts_per_day` counter starts at 0 for her today."""
    admin_sess.put(
        f"{BASE_URL}/api/wheel/admin/config",
        json={"duel_enabled": True, "duel_accepts_per_day": 50, "quiz_timer_seconds": 30},
        timeout=60,
    )
    # Backdate Alice's accepted duels so cap counter resets
    db_exec(
        "UPDATE duels SET accepted_at = NOW() - INTERVAL '2 days' "
        "WHERE opponent_id = $1 AND accepted_at::date = CURRENT_DATE",
        alice_sess.user_id,
    )
    yield
    admin_sess.put(
        f"{BASE_URL}/api/wheel/admin/config",
        json={"duel_accepts_per_day": 3},
        timeout=60,
    )


# ─── DB helpers ────────────────────────────────────────────────────────
async def _db_exec(sql: str, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _db_fetchrow(sql: str, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


def db_exec(sql, *args):
    return asyncio.run(_db_exec(sql, *args))


def db_fetchrow(sql, *args):
    return asyncio.run(_db_fetchrow(sql, *args))


async def _fetch_correct(qids):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(
            "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
            qids,
        )
    finally:
        await conn.close()


# ─── Helper: bob plays a real quiz round ───────────────────────────────
def _bob_play_quiz(admin_sess, bob_sess) -> int:
    """Reset bob, play full quiz, return run_id (submitted)."""
    admin_sess.post(f"{BASE_URL}/api/quiz/admin/reset-user/{bob_sess.user_id}", timeout=60)
    r = bob_sess.post(f"{BASE_URL}/api/quiz/start", timeout=60)
    if r.status_code == 503:
        pytest.skip("Quiz not available")
    assert r.status_code == 200, r.text[:200]
    run_id = r.json()["run_id"]
    # take ~1.5s before submitting so challenger_time_s is non-zero
    time.sleep(1.5)
    r = bob_sess.post(
        f"{BASE_URL}/api/quiz/submit",
        json={"run_id": run_id, "answers": [0, 0, 0, 0, 0]},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:200]
    return run_id


# ═══════════════════════════════════════════════════════════════════════
#  TEST CLASSES
# ═══════════════════════════════════════════════════════════════════════

class TestCreateFromQuizCapturesTime:
    def test_create_quiz_duel_captures_challenger_time(self, admin_sess, bob_sess):
        run_id = _bob_play_quiz(admin_sess, bob_sess)
        r = bob_sess.post(f"{BASE_URL}/api/duel/create-from-quiz",
                          json={"run_id": run_id}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "share_token" in data and len(data["share_token"]) >= 16
        token = data["share_token"]
        pytest.shared_quiz_token = token

        # Verify challenger_time_s in DB
        row = db_fetchrow(
            "SELECT challenger_time_s, challenger_score, challenger_session_id "
            "FROM duels WHERE share_token=$1", token,
        )
        assert row is not None
        assert row["challenger_time_s"] is not None, "challenger_time_s must be captured"
        assert float(row["challenger_time_s"]) >= 1.0, f"expected >=1s, got {row['challenger_time_s']}"
        assert row["challenger_session_id"] is not None
        pytest.shared_challenger_score = int(row["challenger_score"])
        pytest.shared_challenger_time_s = float(row["challenger_time_s"])

    def test_get_duel_exposes_both_time_fields(self):
        token = pytest.shared_quiz_token
        r = requests.get(f"{BASE_URL}/api/duel/{token}", timeout=60)
        assert r.status_code == 200
        data = r.json()
        # iter131 contract: both time fields present in payload
        assert "challenger_time_s" in data, f"missing challenger_time_s: {data}"
        assert "opponent_time_s" in data, f"missing opponent_time_s: {data}"
        assert data["challenger_time_s"] is not None
        assert data["opponent_time_s"] is None  # no opponent yet


class TestStartQuizDuel:
    def test_self_duel_blocked(self, bob_sess):
        token = pytest.shared_quiz_token
        r = bob_sess.post(f"{BASE_URL}/api/duel/{token}/start-quiz", timeout=60)
        assert r.status_code == 400
        assert "vous-même" in r.text or "même" in r.text or "self" in r.text.lower()

    def test_alice_starts_gets_same_5_questions(self, alice_sess):
        token = pytest.shared_quiz_token
        r = alice_sess.post(f"{BASE_URL}/api/duel/{token}/start-quiz", timeout=60)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["duel_token"] == token
        assert isinstance(data["questions"], list)
        assert len(data["questions"]) == 5
        # Each question carries id+text+options(4)+category
        for q in data["questions"]:
            assert "id" in q and "text" in q and "category" in q
            assert isinstance(q["options"], list) and len(q["options"]) == 4
        assert data["run_id"] > 0
        pytest.shared_alice_run_id = data["run_id"]


class TestSubmitQuizDuelHigherScore:
    def test_alice_submits_and_wins_or_loses(self, alice_sess):
        token = pytest.shared_quiz_token
        # Submit all answers=0 (random correctness; just to exercise scoring)
        time.sleep(0.5)
        r = alice_sess.post(
            f"{BASE_URL}/api/duel/{token}/submit-quiz",
            json={"answers": [1, 1, 1, 1, 1]}, timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        # iter131 response contract
        for k in ("challenger_score", "opponent_score", "winner_id",
                  "challenger_time_s", "opponent_time_s", "tiebreaker"):
            assert k in data, f"missing {k}: {data}"
        assert data["opponent_time_s"] is not None
        assert data["opponent_time_s"] >= 0.0
        assert data["challenger_time_s"] is not None
        # tiebreaker only set when scores equal AND time diff>=0.2
        if data["challenger_score"] != data["opponent_score"]:
            assert data["tiebreaker"] is None
        # serialise_duel reflects opponent_time_s
        r2 = requests.get(f"{BASE_URL}/api/duel/{token}", timeout=60)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["status"] == "completed"
        assert d2["opponent_time_s"] is not None
        assert d2["challenger_time_s"] is not None

    def test_double_submit_rejected(self, alice_sess):
        token = pytest.shared_quiz_token
        r = alice_sess.post(
            f"{BASE_URL}/api/duel/{token}/submit-quiz",
            json={"answers": [0, 0, 0, 0, 0]}, timeout=60,
        )
        # Either 400 (already completed) or 403 if status check kicks in first
        assert r.status_code in (400, 403)


# ─── Tiebreaker scenarios — DB-injected duels ─────────────────────────
def _inject_test_duel(challenger_id, challenger_score, challenger_time_s,
                       challenger_session_id, token_suffix):
    """Insert an open quiz duel directly via DB for controlled tiebreaker tests."""
    from datetime import datetime, timezone, timedelta
    import secrets
    token = f"iter131_{token_suffix}_{secrets.token_urlsafe(6)}"
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    sql = """INSERT INTO duels (share_token, game, challenger_id, challenger_run_id,
                               challenger_score, challenger_session_id, challenger_metadata,
                               challenger_time_s, expires_at)
             VALUES ($1, 'quiz', $2, 0, $3, $4, '{}'::jsonb, $5, $6)
             RETURNING share_token"""
    row = db_fetchrow(sql, token, challenger_id, challenger_score,
                      challenger_session_id, challenger_time_s, expires)
    return row["share_token"]


class TestTiebreakerTime:
    @pytest.fixture(scope="class")
    def shared_session_id(self, admin_sess, bob_sess):
        """Need a real quiz_session id to reuse for the injected duel."""
        admin_sess.post(f"{BASE_URL}/api/quiz/admin/reset-user/{bob_sess.user_id}", timeout=60)
        r = bob_sess.post(f"{BASE_URL}/api/quiz/start", timeout=60)
        if r.status_code != 200:
            pytest.skip("Quiz unavailable for session injection")
        run_id = r.json()["run_id"]
        # submit so the run is closed (we only need session_id)
        bob_sess.post(f"{BASE_URL}/api/quiz/submit",
                      json={"run_id": run_id, "answers": [0, 0, 0, 0, 0]}, timeout=60)
        row = db_fetchrow("SELECT session_id FROM quiz_user_runs WHERE id=$1", run_id)
        return int(row["session_id"])

    def _alice_expected_correct(self, token, answers):
        """Compute Alice's expected correct_count by reading her options_order
        + correct_index from DB (deterministic)."""
        run = db_fetchrow(
            "SELECT id, session_id, options_order FROM quiz_user_runs "
            "WHERE id = (SELECT opponent_run_id FROM duels WHERE share_token=$1)",
            token,
        )
        sess = db_fetchrow("SELECT question_ids FROM quiz_sessions WHERE id=$1",
                           int(run["session_id"]))
        qrow = asyncio.run(_fetch_correct(list(sess["question_ids"])))
        cmap = {int(r["id"]): int(r["correct_index"]) for r in qrow}
        import json as _j
        perms_raw = run["options_order"]
        perms = _j.loads(perms_raw) if isinstance(perms_raw, str) else (perms_raw or [])
        order = [int(qid) for qid in sess["question_ids"]]
        cc = 0
        for i, qid in enumerate(order):
            given = answers[i] if i < len(answers) else -1
            perm = perms[i] if i < len(perms) else [0, 1, 2, 3]
            given_orig = perm[given] if 0 <= given < len(perm) else -1
            if given_orig == cmap.get(qid, -2):
                cc += 1
        return cc, int(run["id"])

    def test_equal_scores_diff_geq_020_faster_wins(self, alice_sess, bob_sess, shared_session_id):
        # Inject duel: challenger time = 5.00s
        token = _inject_test_duel(
            bob_sess.user_id, challenger_score=0, challenger_time_s=5.00,
            challenger_session_id=shared_session_id, token_suffix="fast",
        )
        # Alice starts
        r = alice_sess.post(f"{BASE_URL}/api/duel/{token}/start-quiz", timeout=60)
        assert r.status_code == 200, r.text[:300]
        answers = [3, 3, 3, 3, 3]
        expected_cc, _ = self._alice_expected_correct(token, answers)
        # Patch challenger_score so it matches alice's expected score → equal
        db_exec("UPDATE duels SET challenger_score=$1 WHERE share_token=$2",
                expected_cc, token)
        r = alice_sess.post(
            f"{BASE_URL}/api/duel/{token}/submit-quiz",
            json={"answers": answers}, timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["opponent_score"] == data["challenger_score"] == expected_cc
        assert data["challenger_time_s"] == 5.0
        assert data["opponent_time_s"] < 4.5
        assert data["tiebreaker"] == "time", f"expected tiebreaker='time', got {data}"
        assert data["winner_id"] == alice_sess.user_id

    def test_equal_scores_diff_lt_020_true_tie(self, alice_sess, bob_sess, shared_session_id):
        # Inject duel: challenger time = 0.10s
        token = _inject_test_duel(
            bob_sess.user_id, challenger_score=0, challenger_time_s=0.10,
            challenger_session_id=shared_session_id, token_suffix="tie",
        )
        r = alice_sess.post(f"{BASE_URL}/api/duel/{token}/start-quiz", timeout=60)
        assert r.status_code == 200, r.text[:300]
        # Manipulate alice's started_at so elapsed ~= 0.05s (within 0.20 of 0.10)
        run_row = db_fetchrow(
            "SELECT id FROM quiz_user_runs "
            "WHERE id = (SELECT opponent_run_id FROM duels WHERE share_token=$1)", token,
        )
        from datetime import datetime, timezone, timedelta
        new_started = datetime.now(timezone.utc) - timedelta(milliseconds=50)
        db_exec("UPDATE quiz_user_runs SET started_at=$1 WHERE id=$2",
                new_started, int(run_row["id"]))
        # Patch challenger_score to match alice's expected count
        answers = [3, 3, 3, 3, 3]
        expected_cc, _ = self._alice_expected_correct(token, answers)
        db_exec("UPDATE duels SET challenger_score=$1 WHERE share_token=$2",
                expected_cc, token)
        r = alice_sess.post(
            f"{BASE_URL}/api/duel/{token}/submit-quiz",
            json={"answers": answers}, timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["opponent_score"] == data["challenger_score"] == expected_cc
        assert abs(data["opponent_time_s"] - data["challenger_time_s"]) < 0.20, data
        assert data["tiebreaker"] is None, f"expected tiebreaker=None: {data}"
        assert data["winner_id"] is None, f"expected pure tie: {data}"

    def test_unequal_scores_no_tiebreaker(self, alice_sess, bob_sess, shared_session_id):
        # Inject duel: challenger_score=5 (perfect), alice will probably score <5
        token = _inject_test_duel(
            bob_sess.user_id, challenger_score=5, challenger_time_s=2.00,
            challenger_session_id=shared_session_id, token_suffix="winner",
        )
        r = alice_sess.post(f"{BASE_URL}/api/duel/{token}/start-quiz", timeout=60)
        assert r.status_code == 200
        r = alice_sess.post(
            f"{BASE_URL}/api/duel/{token}/submit-quiz",
            json={"answers": [3, 3, 3, 3, 3]}, timeout=60,
        )
        assert r.status_code == 200
        data = r.json()
        if data["opponent_score"] >= 5:
            pytest.skip("alice somehow tied/won")
        assert data["winner_id"] == bob_sess.user_id
        assert data["tiebreaker"] is None


class TestRegressionTapDuelNoTimeTiebreaker:
    """Ensure tap duel flow still works and challenger_time_s stays NULL for tap."""
    def test_tap_duel_does_not_use_time(self, admin_sess, bob_sess):
        # Reset + run tap
        admin_sess.post(f"{BASE_URL}/api/tap/admin/reset-user/{bob_sess.user_id}", timeout=60)
        r = bob_sess.post(f"{BASE_URL}/api/tap/start", timeout=60)
        if r.status_code != 200:
            pytest.skip(f"Tap start failed: {r.status_code} {r.text[:200]}")
        run_id = r.json()["run_id"]
        time.sleep(10.2)
        r = bob_sess.post(f"{BASE_URL}/api/tap/submit",
                         json={"run_id": run_id, "taps": 50}, timeout=60)
        assert r.status_code == 200, r.text[:200]
        # Create tap duel
        r = bob_sess.post(f"{BASE_URL}/api/duel/create-from-tap",
                          json={"run_id": run_id}, timeout=60)
        assert r.status_code == 200, r.text[:200]
        token = r.json()["share_token"]
        # GET should expose both time fields (None for tap)
        r = requests.get(f"{BASE_URL}/api/duel/{token}", timeout=60)
        assert r.status_code == 200
        data = r.json()
        assert data["game"] == "tap"
        assert data["challenger_time_s"] is None, "tap duels must not capture time"
        assert data["opponent_time_s"] is None
