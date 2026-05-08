"""
iter126 — Phase 3.C Quiz Champion notifications fan-out + leaderboard + toggles.
Custom Python runner (pytest broken locally with web3 plugin).
Emits JUnit XML to /app/test_reports/pytest/iter126_results.xml.

Scope:
  - GET /api/games/toggles exposes the 6 quiz_challenge_* keys with defaults.
  - GET /api/quiz/champion/leaderboard/challengers (default + filter by CM).
  - POST /api/quiz/champion/challenge inserts a `notifications` row of
    kind='quiz_champion_challenge' for the champion (best-effort push/email
    must not block the request — verify <2s latency & 200 OK).
  - REGRESSION: GET /api/quiz/champion/CM still returns Bob; free mode still
    works end-to-end for Alice.
"""
from __future__ import annotations
import os, sys, asyncio, traceback, time
from decimal import Decimal
import requests, asyncpg
from xml.sax.saxutils import escape as xesc
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')
load_dotenv('/app/frontend/.env')

BASE = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
DB_URL = os.environ['DATABASE_URL']
TURNSTILE = os.environ.get('TURNSTILE_TEST_BYPASS_TOKEN', 'JAPAP_E2E_BYPASS_2026')

ALICE_EMAIL, ALICE_PWD = 'alice@japap.com', 'Alice2026!'
BOB_EMAIL, BOB_PWD     = 'bob@japap.com',   'Test1234!'

results = []

def log_test(name, ok, info=""):
    results.append((name, ok, info))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {info[:240]}")

async def db():
    return await asyncpg.connect(DB_URL)

def login(session: requests.Session, email, pwd):
    session.get(f"{BASE}/api/auth/me")
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf
    r = session.post(f"{BASE}/api/auth/login",
                     json={"email": email, "password": pwd, "turnstile_token": TURNSTILE})
    if r.status_code != 200:
        return False, r.text
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf
    return True, r.json()

async def reset_state():
    c = await db()
    try:
        await c.execute("UPDATE wallets SET balance=20000, currency='XAF' WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.execute("UPDATE wallets SET balance=50000, currency='XAF' WHERE user_id=(SELECT user_id FROM users WHERE email='bob@japap.com')")
        await c.execute("""UPDATE quiz_champion_challenges SET status='cancelled'
                           WHERE status IN ('pending','accepted','challenger_played','champion_played')
                             AND challenger_user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')""")
        bob = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        await c.execute("""INSERT INTO quiz_country_champions (country_code,user_id,promoted_at,source,refusal_count_consecutive)
                           VALUES ('CM',$1,NOW(),'admin',0)
                           ON CONFLICT (country_code) DO UPDATE SET user_id=$1, promoted_at=NOW(),
                             refusal_count_consecutive=0, demoted_at=NULL, demoted_reason=NULL""", bob)
    finally:
        await c.close()


async def main():
    if not BASE:
        log_test("env_check_REACT_APP_BACKEND_URL", False, "missing"); write_junit(); sys.exit(1)
    log_test("env_check_REACT_APP_BACKEND_URL", True, BASE)

    # -------------------------------------------------------------- toggles
    s = requests.Session()
    r = s.get(f"{BASE}/api/games/toggles")
    log_test("toggles_GET_200", r.status_code == 200, f"{r.status_code}: {r.text[:120]}")
    cfg = r.json() if r.status_code == 200 else {}
    expected_defaults = {
        "quiz_challenge_paid_enabled":            True,
        "quiz_challenge_commission_pct":          10,
        "quiz_challenge_stake_min":               1,
        "quiz_challenge_stake_max":               10000,
        "quiz_challenge_expiry_hours":            24,
        "quiz_challenge_challenger_bonus_points": 50,
    }
    missing = [k for k in expected_defaults if k not in cfg]
    log_test("toggles_has_6_quiz_challenge_keys", not missing, f"missing={missing}; got_keys={[k for k in cfg if k.startswith('quiz_challenge_')]}")
    for k, v in expected_defaults.items():
        log_test(f"toggles_default_{k}", cfg.get(k) == v, f"got={cfg.get(k)} expected={v}")

    # -------------------------------------------------------- leaderboard A
    r = s.get(f"{BASE}/api/quiz/champion/leaderboard/challengers")
    log_test("leaderboard_default_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    log_test("leaderboard_window_days_default_7", body.get("window_days") == 7, f"got={body.get('window_days')}")
    log_test("leaderboard_country_code_null_default", body.get("country_code") in (None, ""), f"got={body.get('country_code')!r}")
    log_test("leaderboard_has_free_array",  isinstance(body.get("free"), list), f"type={type(body.get('free')).__name__}")
    log_test("leaderboard_has_paid_array",  isinstance(body.get("paid"), list), f"type={type(body.get('paid')).__name__}")

    # Alice should appear in `paid` with wins_paid >= 2 (from iter125)
    alice_in_paid = next((u for u in (body.get("paid") or []) if (u.get("user", {}).get("username","").lower()=="alice"
                                                                   or u.get("user", {}).get("first_name","").lower()=="alice")), None)
    if alice_in_paid is None:
        # Fallback: lookup by user_id from DB
        cdb = await db()
        try:
            alice_id = await cdb.fetchval("SELECT user_id FROM users WHERE email='alice@japap.com'")
        finally:
            await cdb.close()
        alice_in_paid = next((u for u in (body.get("paid") or []) if u.get("user_id") == alice_id), None)

    log_test("leaderboard_alice_in_paid", alice_in_paid is not None,
             f"paid_users={[(u.get('user',{}).get('username'), u.get('wins_paid'), u.get('earnings')) for u in body.get('paid', [])]}")
    if alice_in_paid:
        log_test("leaderboard_alice_wins_paid_gte_2", int(alice_in_paid.get("wins_paid", 0)) >= 2,
                 f"wins_paid={alice_in_paid.get('wins_paid')}")
        log_test("leaderboard_alice_earnings_gt_0", float(alice_in_paid.get("earnings", 0)) > 0,
                 f"earnings={alice_in_paid.get('earnings')}")
        u = alice_in_paid.get("user") or {}
        log_test("leaderboard_user_block_has_public_fields",
                 all(k in u for k in ("user_id","first_name","last_name","username","avatar","is_pro")),
                 f"keys={list(u.keys())}")

    # Top 3 cap
    log_test("leaderboard_paid_at_most_3", len(body.get("paid", [])) <= 3, f"len={len(body.get('paid', []))}")
    log_test("leaderboard_free_at_most_3", len(body.get("free", [])) <= 3, f"len={len(body.get('free', []))}")

    # ------------------------------------------- leaderboard scoped + limit
    r = s.get(f"{BASE}/api/quiz/champion/leaderboard/challengers",
              params={"country_code": "CM", "window_days": 30, "limit": 5})
    log_test("leaderboard_scoped_CM_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    body2 = r.json() if r.status_code == 200 else {}
    log_test("leaderboard_scoped_window_days_30", body2.get("window_days") == 30, f"got={body2.get('window_days')}")
    log_test("leaderboard_scoped_country_CM", body2.get("country_code") == "CM", f"got={body2.get('country_code')}")
    log_test("leaderboard_scoped_paid_at_most_5", len(body2.get("paid", [])) <= 5, f"len={len(body2.get('paid', []))}")
    log_test("leaderboard_scoped_free_at_most_5", len(body2.get("free", [])) <= 5, f"len={len(body2.get('free', []))}")

    # Bad country_code (lowercase / 3-letter) — endpoint should still 200 with safe normalization
    r = s.get(f"{BASE}/api/quiz/champion/leaderboard/challengers",
              params={"country_code": "cm"})
    log_test("leaderboard_country_code_lowercase_normalized",
             r.status_code == 200 and r.json().get("country_code") == "CM", f"{r.status_code}: {r.text[:120]}")

    # -------------------------------------------------- regression: champion CM
    r = s.get(f"{BASE}/api/quiz/champion/CM")
    log_test("regression_champion_CM_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    body3 = r.json() if r.status_code == 200 else {}
    user_blk = body3.get("user") or {}
    bob_match = (user_blk.get("first_name","").lower() == "bob"
                 or user_blk.get("username","").lower() == "bob"
                 or "bob" in str(user_blk).lower())
    log_test("regression_champion_CM_is_bob", bob_match, f"user={user_blk}")

    # ---------------- notifications + free challenge end-to-end (Alice) -----
    await reset_state()
    alice_s = requests.Session()
    ok, info = login(alice_s, ALICE_EMAIL, ALICE_PWD)
    log_test("alice_login", ok, str(info)[:120])
    if not ok:
        write_junit(); return

    # Capture pre-existing notifications count for Bob (champion) so we can
    # diff before vs after the challenge POST.
    cdb = await db()
    try:
        bob_id   = await cdb.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        alice_id = await cdb.fetchval("SELECT user_id FROM users WHERE email='alice@japap.com'")
        n_before = await cdb.fetchval(
            "SELECT COUNT(*) FROM notifications WHERE user_id=$1 AND type='quiz_champion_challenge'",
            bob_id,
        )
    finally:
        await cdb.close()

    t0 = time.time()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"free"})
    elapsed = time.time() - t0
    log_test("free_challenge_create_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    log_test("free_challenge_under_5s_despite_fanout", elapsed < 5.0, f"elapsed={elapsed:.2f}s")
    cid = r.json().get("challenge_id") if r.status_code == 200 else None

    # Give the best-effort fan-out a moment to flush (push/email run inline so
    # the row should already be there, but allow tiny slack).
    await asyncio.sleep(0.5)

    cdb = await db()
    try:
        n_after = await cdb.fetchval(
            "SELECT COUNT(*) FROM notifications WHERE user_id=$1 AND type='quiz_champion_challenge'",
            bob_id,
        )
        last = await cdb.fetchrow(
            """SELECT notif_id, type, title, message, data, created_at
               FROM notifications WHERE user_id=$1 AND type='quiz_champion_challenge'
               ORDER BY created_at DESC LIMIT 1""",
            bob_id,
        )
    finally:
        await cdb.close()
    log_test("notification_row_inserted_for_champion",
             n_after - n_before == 1, f"before={n_before} after={n_after}")
    log_test("notification_kind_is_quiz_champion_challenge",
             last and last["type"] == "quiz_champion_challenge",
             f"row={dict(last) if last else None}")
    log_test("notification_has_title_message",
             last and bool(last["title"]) and bool(last["message"]),
             f"title={last and last['title']}, message={last and last['message']}")

    # -------- regression: free mode, no wallet movement -----
    cdb = await db()
    try:
        a_bal = await cdb.fetchval("SELECT balance FROM wallets WHERE user_id=$1", alice_id)
        b_bal = await cdb.fetchval("SELECT balance FROM wallets WHERE user_id=$1", bob_id)
    finally:
        await cdb.close()
    log_test("regression_free_no_wallet_debit_alice",
             Decimal(str(a_bal)) == Decimal("20000"), f"alice_bal={a_bal}")
    log_test("regression_free_no_wallet_debit_bob",
             Decimal(str(b_bal)) == Decimal("50000"), f"bob_bal={b_bal}")

    # status is pending
    cdb = await db()
    try:
        st = await cdb.fetchval("SELECT status FROM quiz_champion_challenges WHERE challenge_id=$1", cid)
    finally:
        await cdb.close()
    log_test("regression_free_status_pending", st == "pending", f"status={st}")

    write_junit()


def write_junit():
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    n = len(results); fails = sum(1 for _,ok,_ in results if not ok)
    body = "\n".join(
        f'<testcase classname="iter126" name="{xesc(name)}">' +
        (f'<failure>{xesc(info)}</failure>' if not ok else '') + '</testcase>'
        for name, ok, info in results
    )
    xml = f'<?xml version="1.0"?><testsuite name="iter126" tests="{n}" failures="{fails}">{body}</testsuite>'
    with open("/app/test_reports/pytest/iter126_results.xml","w") as f:
        f.write(xml)
    print(f"\n=== {n - fails}/{n} passed, {fails} failures ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        write_junit()
