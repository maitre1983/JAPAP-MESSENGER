"""
iter125 — Phase 3.B Quiz Champion paid mode + escrow.
Custom Python runner (pytest broken locally with web3 plugin).
Emits JUnit XML to /app/test_reports/pytest/iter125_results.xml.
"""
from __future__ import annotations
import os, sys, time, json, asyncio, traceback
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import requests, asyncpg
from xml.sax.saxutils import escape as xesc
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

BASE = os.environ.get('REACT_APP_BACKEND_URL', 'https://japap-refactor.preview.emergentagent.com').rstrip('/')
DB_URL = os.environ['DATABASE_URL']
TURNSTILE = os.environ.get('TURNSTILE_TEST_BYPASS_TOKEN', 'JAPAP_E2E_BYPASS_2026')

ALICE_EMAIL, ALICE_PWD = 'alice@japap.com', 'Alice2026!'
BOB_EMAIL, BOB_PWD     = 'bob@japap.com',   'Test1234!'
SUPER_EMAIL, SUPER_PWD = 'emileparfait2003@gmail.com', 'Gerard0103@'

results = []

def log_test(name, ok, info=""):
    results.append((name, ok, info))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {info[:200]}")

async def db():
    return await asyncpg.connect(DB_URL)

def login(session: requests.Session, email, pwd):
    # CSRF first
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

async def login_super(session: requests.Session):
    session.get(f"{BASE}/api/auth/me")
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf
    r = session.post(f"{BASE}/api/auth/login",
                     json={"email": SUPER_EMAIL, "password": SUPER_PWD, "turnstile_token": TURNSTILE})
    if r.status_code != 200:
        return False, r.text
    # Need 2FA OTP
    if r.json().get("requires_2fa") or "otp" in r.text.lower():
        await asyncio.sleep(1)
        c = await db()
        try:
            row = await c.fetchrow(
                "SELECT code FROM email_otps WHERE email=$1 AND purpose='login_2fa' AND used=FALSE ORDER BY created_at DESC LIMIT 1",
                SUPER_EMAIL,
            )
        finally:
            await c.close()
        if not row:
            return False, "no OTP found"
        csrf = session.cookies.get("csrf_token")
        if csrf:
            session.headers["X-CSRF-Token"] = csrf
        r2 = session.post(f"{BASE}/api/auth/verify-2fa",
                          json={"email": SUPER_EMAIL, "code": row["code"]})
        if r2.status_code != 200:
            return False, f"2fa: {r2.text}"
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf
    return True, "ok"

async def reset_state():
    c = await db()
    try:
        await c.execute("UPDATE wallets SET balance=20000 WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.execute("UPDATE wallets SET balance=50000 WHERE user_id=(SELECT user_id FROM users WHERE email='bob@japap.com')")
        await c.execute("UPDATE wallets SET currency='XAF' WHERE user_id IN (SELECT user_id FROM users WHERE email IN ('alice@japap.com','bob@japap.com'))")
        # Cancel any open challenges between Alice & Bob
        await c.execute("""UPDATE quiz_champion_challenges SET status='cancelled'
                           WHERE status IN ('pending','accepted','challenger_played','champion_played')
                             AND challenger_user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')""")
        # Make sure Bob is current CM champion
        bob = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        await c.execute("""INSERT INTO quiz_country_champions (country_code,user_id,promoted_at,source,refusal_count_consecutive)
                           VALUES ('CM',$1,NOW(),'admin',0)
                           ON CONFLICT (country_code) DO UPDATE SET user_id=$1, promoted_at=NOW(),
                             refusal_count_consecutive=0, demoted_at=NULL, demoted_reason=NULL""", bob)
    finally:
        await c.close()

async def get_balances():
    c = await db()
    try:
        a = await c.fetchval("SELECT balance FROM wallets WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        b = await c.fetchval("SELECT balance FROM wallets WHERE user_id=(SELECT user_id FROM users WHERE email='bob@japap.com')")
        return Decimal(str(a)), Decimal(str(b))
    finally:
        await c.close()

async def count_tx(challenge_id, tx_type):
    c = await db()
    try:
        return await c.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE type=$1 AND notes LIKE $2",
            tx_type, f"%{challenge_id}%",
        )
    finally:
        await c.close()

async def set_paid_enabled(admin_session, val: bool):
    r = admin_session.put(f"{BASE}/api/admin/games/quiz",
                          json={"quiz_challenge_paid_enabled": val})
    return r.status_code == 200

# ==================== TESTS ====================

async def main():
    admin_s = requests.Session()
    ok, info = await login_super(admin_s)
    if not ok:
        log_test("admin_login", False, info); write_junit(); sys.exit(1)
    log_test("admin_login", True)

    # Test 1: GET /api/admin/games/quiz returns all 7 new keys
    r = admin_s.get(f"{BASE}/api/admin/games/quiz")
    cfg = r.json()["config"]
    new_keys = ["quiz_challenge_paid_enabled","quiz_challenge_commission_pct",
                "quiz_challenge_stake_min","quiz_challenge_stake_max",
                "quiz_challenge_refund_on_expiry","quiz_challenge_challenger_bonus_points",
                "quiz_challenge_expiry_hours"]
    missing = [k for k in new_keys if k not in cfg]
    log_test("settings_GET_has_7_new_keys", not missing, f"missing={missing}")

    # Test 2: PUT each new setting persists
    test_updates = {"quiz_challenge_commission_pct": 12, "quiz_challenge_stake_min": 2,
                    "quiz_challenge_stake_max": 5000, "quiz_challenge_expiry_hours": 48,
                    "quiz_challenge_challenger_bonus_points": 75,
                    "quiz_challenge_refund_on_expiry": False}
    r = admin_s.put(f"{BASE}/api/admin/games/quiz", json=test_updates)
    persisted = r.json().get("config", {}) if r.status_code == 200 else {}
    all_ok = all(persisted.get(k) == v for k, v in test_updates.items())
    log_test("settings_PUT_persists", all_ok, f"got={[(k,persisted.get(k)) for k in test_updates]}")

    # Restore defaults for further tests
    admin_s.put(f"{BASE}/api/admin/games/quiz",
                json={"quiz_challenge_commission_pct":10,"quiz_challenge_stake_min":1,
                      "quiz_challenge_stake_max":10000,"quiz_challenge_expiry_hours":24,
                      "quiz_challenge_challenger_bonus_points":50,
                      "quiz_challenge_refund_on_expiry":True})

    # Test 3: validation bounds
    r = admin_s.put(f"{BASE}/api/admin/games/quiz", json={"quiz_challenge_commission_pct":75})
    log_test("settings_validation_commission_pct_above50", r.status_code == 400, f"{r.status_code}")
    r = admin_s.put(f"{BASE}/api/admin/games/quiz", json={"quiz_challenge_expiry_hours":200})
    log_test("settings_validation_expiry_above168", r.status_code == 400, f"{r.status_code}")
    r = admin_s.put(f"{BASE}/api/admin/games/quiz", json={"quiz_challenge_challenger_bonus_points":2000})
    log_test("settings_validation_bonus_above1000", r.status_code == 400, f"{r.status_code}")

    # Reset wallets
    await reset_state()
    await set_paid_enabled(admin_s, True)

    alice_s = requests.Session()
    ok, info = login(alice_s, ALICE_EMAIL, ALICE_PWD)
    log_test("alice_login", ok, str(info)[:100])
    if not ok: return write_junit()

    bob_s = requests.Session()
    ok, info = login(bob_s, BOB_EMAIL, BOB_PWD)
    log_test("bob_login", ok, str(info)[:100])
    if not ok: return write_junit()

    # Test 4: paid disabled → 503
    await set_paid_enabled(admin_s, False)
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":500})
    log_test("paid_disabled_returns_503", r.status_code == 503, f"{r.status_code}: {r.text[:120]}")
    await set_paid_enabled(admin_s, True)

    # Test 5: stake below min → 400
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":0})
    log_test("stake_below_min_400", r.status_code == 400 and "minimale" in r.text.lower(), f"{r.status_code}: {r.text[:120]}")

    # Test 6: stake above max → 400
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":99999})
    log_test("stake_above_max_400", r.status_code == 400 and "maximale" in r.text.lower(), f"{r.status_code}: {r.text[:120]}")

    # Test 7: Insufficient balance → 402, wallet unchanged
    a_before, _ = await get_balances()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":99999999})
    # max bound is 10000 → it's blocked at max validation; use a value ≤ max but > balance
    # Set higher max temporarily
    admin_s.put(f"{BASE}/api/admin/games/quiz", json={"quiz_challenge_stake_max":100000})
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":99999})
    a_after, _ = await get_balances()
    log_test("insufficient_balance_402", r.status_code == 402, f"{r.status_code}: {r.text[:120]}")
    log_test("insufficient_balance_atomic_no_debit", a_before == a_after, f"{a_before} == {a_after}")
    admin_s.put(f"{BASE}/api/admin/games/quiz", json={"quiz_challenge_stake_max":10000})

    # Test 8: Successful create + lock
    a_before, b_before = await get_balances()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":500})
    ok = r.status_code == 200
    cid = r.json().get("challenge_id") if ok else None
    log_test("paid_create_success", ok, f"{r.status_code}: cid={cid}")
    a_after, b_after = await get_balances()
    log_test("paid_create_alice_debited_500", a_before - a_after == Decimal("500.00"), f"diff={a_before-a_after}")
    log_test("paid_create_bob_unchanged", b_before == b_after, f"diff={b_before-b_after}")
    locks = await count_tx(cid, "quiz_challenge_lock")
    log_test("paid_create_one_lock_tx", locks == 1, f"locks={locks}")

    # Verify challenge has escrow_locked=true
    c = await db()
    try:
        row = await c.fetchrow("SELECT escrow_locked,stake_amount,stake_currency,commission_pct FROM quiz_champion_challenges WHERE challenge_id=$1", cid)
        log_test("paid_create_escrow_locked_flag", bool(row["escrow_locked"]) and Decimal(str(row["stake_amount"]))==Decimal("500") and row["stake_currency"]=="XAF", str(dict(row)))
    finally:
        await c.close()

    # Test 9: Bob accepts → second lock for Bob
    b_before, _ = await get_balances()  # but use bob index
    a_b, b_b = await get_balances()
    r = bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/accept")
    log_test("paid_accept_200", r.status_code == 200, f"{r.status_code}: {r.text[:120]}")
    a_a, b_a = await get_balances()
    log_test("paid_accept_bob_debited_500", b_b - b_a == Decimal("500.00"), f"diff={b_b-b_a}")
    locks = await count_tx(cid, "quiz_challenge_lock")
    log_test("paid_accept_two_lock_tx", locks == 2, f"locks={locks}")

    # Test 10: race condition on /accept — already accepted → second click fails
    r2 = bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/accept")
    log_test("paid_accept_idempotent_409", r2.status_code == 409, f"{r2.status_code}")
    locks_after = await count_tx(cid, "quiz_challenge_lock")
    log_test("paid_accept_no_double_debit", locks_after == 2, f"locks={locks_after}")

    # Test 11: Both play. Alice wins 5/5 vs Bob 0/5
    # Alice plays
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/play")
    if r.status_code != 200:
        log_test("alice_play", False, f"{r.status_code}: {r.text[:120]}")
        return write_junit()
    qa = r.json()["questions"]
    # Compute Alice's correct answers using DB
    cdb = await db()
    try:
        run = await cdb.fetchrow("SELECT challenger_run_id, session_id FROM quiz_champion_challenges WHERE challenge_id=$1", cid)
        run_a = await cdb.fetchrow("SELECT options_order FROM quiz_user_runs WHERE id=$1", run["challenger_run_id"])
        sess = await cdb.fetchrow("SELECT question_ids FROM quiz_sessions WHERE id=$1", run["session_id"])
        qrows = await cdb.fetch("SELECT id, correct_index FROM quiz_questions WHERE id=ANY($1::bigint[])", list(sess["question_ids"]))
        cmap = {int(x["id"]): int(x["correct_index"]) for x in qrows}
        perms_a = run_a["options_order"]
        if isinstance(perms_a, str):
            perms_a = json.loads(perms_a)
        ans_alice = []
        for i, qid in enumerate(sess["question_ids"]):
            orig = cmap[int(qid)]
            perm = perms_a[i]
            ans_alice.append(perm.index(orig))
    finally:
        await cdb.close()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/submit", json={"answers": ans_alice})
    log_test("alice_submit_5_correct", r.status_code == 200 and r.json()["your_correct"] == 5, f"{r.status_code}: {r.text[:120]}")

    # Test 12: Alice double submit → 409
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/submit", json={"answers": ans_alice})
    log_test("alice_double_submit_409", r.status_code == 409, f"{r.status_code}")

    # Bob plays + submits all wrong
    r = bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/play")
    log_test("bob_play", r.status_code == 200, f"{r.status_code}")
    cdb = await db()
    try:
        run = await cdb.fetchrow("SELECT champion_run_id, session_id FROM quiz_champion_challenges WHERE challenge_id=$1", cid)
        run_b = await cdb.fetchrow("SELECT options_order FROM quiz_user_runs WHERE id=$1", run["champion_run_id"])
        perms_b = run_b["options_order"]
        if isinstance(perms_b, str):
            perms_b = json.loads(perms_b)
        sess = await cdb.fetchrow("SELECT question_ids FROM quiz_sessions WHERE id=$1", run["session_id"])
        qrows = await cdb.fetch("SELECT id, correct_index FROM quiz_questions WHERE id=ANY($1::bigint[])", list(sess["question_ids"]))
        cmap = {int(x["id"]): int(x["correct_index"]) for x in qrows}
        ans_bob = []
        for i, qid in enumerate(sess["question_ids"]):
            orig = cmap[int(qid)]
            perm = perms_b[i]
            wrong = next(j for j in range(4) if perm[j] != orig)
            ans_bob.append(wrong)
    finally:
        await cdb.close()
    a_pre, b_pre = await get_balances()
    r = bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid}/submit", json={"answers": ans_bob})
    resolved = r.json().get("resolved") if r.status_code == 200 else None
    log_test("bob_submit_resolves", bool(resolved) and resolved.get("winner_user_id"), f"resolved={resolved}")

    a_post, b_post = await get_balances()
    # Pot = 1000, commission 10% = 100, payout 900 → Alice +900
    log_test("winner_alice_credited_900", a_post - a_pre == Decimal("900.00"), f"diff={a_post-a_pre}")
    log_test("loser_bob_unchanged_post_resolve", b_post == b_pre, f"diff={b_post-b_pre}")

    # Verify ledger rows
    rel = await count_tx(cid, "quiz_challenge_release")
    com = await count_tx(cid, "quiz_challenge_commission")
    log_test("release_tx_exists", rel == 1, f"rel={rel}")
    log_test("commission_tx_exists", com == 1, f"com={com}")

    # Verify amounts in transactions
    cdb = await db()
    try:
        rel_row = await cdb.fetchrow("SELECT to_user_id, amount FROM transactions WHERE type='quiz_challenge_release' AND notes LIKE $1", f"%{cid}%")
        com_row = await cdb.fetchrow("SELECT to_user_id, amount FROM transactions WHERE type='quiz_challenge_commission' AND notes LIKE $1", f"%{cid}%")
        alice_id = await cdb.fetchval("SELECT user_id FROM users WHERE email='alice@japap.com'")
        log_test("release_amount_900_to_alice", rel_row["to_user_id"]==alice_id and Decimal(str(rel_row["amount"]))==Decimal("900.00"), str(dict(rel_row)))
        log_test("commission_amount_100_no_user", com_row["to_user_id"] is None and Decimal(str(com_row["amount"]))==Decimal("100.00"), str(dict(com_row)))
    finally:
        await cdb.close()

    # Test 13: REFUSE flow + bonus
    await reset_state()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":300})
    cid2 = r.json().get("challenge_id")
    log_test("refuse_flow_create", r.status_code == 200, f"{r.status_code}: cid={cid2}")
    a_pre, b_pre = await get_balances()
    r = bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid2}/refuse")
    body = r.json() if r.status_code == 200 else {}
    log_test("refuse_200", r.status_code == 200, f"{r.status_code}: {r.text[:120]}")
    log_test("refuse_returns_refund_info", bool(body.get("refund") and body["refund"].get("tx_id")), str(body.get("refund")))
    log_test("refuse_returns_bonus_info", bool(body.get("bonus") and body["bonus"].get("points") == 50), str(body.get("bonus")))
    a_post, b_post = await get_balances()
    log_test("refuse_alice_refunded_300", a_post - a_pre == Decimal("300.00"), f"diff={a_post-a_pre}")
    log_test("refuse_bob_unchanged", b_post == b_pre, f"diff={b_post-b_pre}")
    refund_n = await count_tx(cid2, "quiz_challenge_refund")
    bonus_n  = await count_tx(cid2, "quiz_challenge_bonus")
    log_test("refuse_refund_tx_exists", refund_n == 1, f"n={refund_n}")
    log_test("refuse_bonus_tx_exists", bonus_n == 1, f"n={bonus_n}")

    # Test 14: TIE flow (both 0/5)
    await reset_state()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":200})
    cid3 = r.json()["challenge_id"]
    bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid3}/accept")
    a_pre, b_pre = await get_balances()
    # Both play, both wrong (0/5 each = tie at 0)
    # But spec says "tie (challenger==champion, both > 0)" — let's force both 5/5
    for s, role_email in [(alice_s, "alice@japap.com"), (bob_s, "bob@japap.com")]:
        s.post(f"{BASE}/api/quiz/champion/challenge/{cid3}/play")
    cdb = await db()
    try:
        ch = await cdb.fetchrow("SELECT challenger_run_id, champion_run_id, session_id FROM quiz_champion_challenges WHERE challenge_id=$1", cid3)
        sess = await cdb.fetchrow("SELECT question_ids FROM quiz_sessions WHERE id=$1", ch["session_id"])
        qrows = await cdb.fetch("SELECT id, correct_index FROM quiz_questions WHERE id=ANY($1::bigint[])", list(sess["question_ids"]))
        cmap = {int(x["id"]): int(x["correct_index"]) for x in qrows}
        async def correct_ans(run_id):
            r_ = await cdb.fetchrow("SELECT options_order FROM quiz_user_runs WHERE id=$1", run_id)
            perms = r_["options_order"]
            if isinstance(perms, str): perms = json.loads(perms)
            return [perms[i].index(cmap[int(qid)]) for i, qid in enumerate(sess["question_ids"])]
        ans_a = await correct_ans(ch["challenger_run_id"])
        ans_b = await correct_ans(ch["champion_run_id"])
    finally:
        await cdb.close()
    alice_s.post(f"{BASE}/api/quiz/champion/challenge/{cid3}/submit", json={"answers": ans_a})
    r = bob_s.post(f"{BASE}/api/quiz/champion/challenge/{cid3}/submit", json={"answers": ans_b})
    resolved = r.json().get("resolved") if r.status_code == 200 else None
    log_test("tie_no_winner", resolved and resolved.get("winner_user_id") is None, str(resolved))
    a_post, b_post = await get_balances()
    log_test("tie_alice_refunded_full", a_post - a_pre == Decimal("200.00"), f"diff={a_post-a_pre}")
    log_test("tie_bob_refunded_full", b_post - b_pre == Decimal("200.00"), f"diff={b_post-b_pre}")
    refund_n = await count_tx(cid3, "quiz_challenge_refund")
    com_n    = await count_tx(cid3, "quiz_challenge_commission")
    rel_n    = await count_tx(cid3, "quiz_challenge_release")
    log_test("tie_two_refund_rows", refund_n == 2, f"n={refund_n}")
    log_test("tie_no_commission", com_n == 0, f"n={com_n}")
    log_test("tie_no_release", rel_n == 0, f"n={rel_n}")

    # Test 15: race-condition simulation on /accept: send 2 parallel
    await reset_state()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":400})
    cid4 = r.json()["challenge_id"]
    import concurrent.futures
    def do_accept():
        s = requests.Session(); login(s, BOB_EMAIL, BOB_PWD)
        return s.post(f"{BASE}/api/quiz/champion/challenge/{cid4}/accept")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(do_accept) for _ in range(2)]
        codes = sorted([f.result().status_code for f in futs])
    log_test("race_accept_one_200_one_409", codes == [200, 409] or codes == [200, 200] and False, f"codes={codes}")
    locks_n = await count_tx(cid4, "quiz_challenge_lock")
    # Should be 2 = 1 alice (create) + 1 bob (single accept)
    log_test("race_accept_only_one_bob_lock", locks_n == 2, f"locks={locks_n}")

    # Test 16: admin/expire-stale
    await reset_state()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"paid","stake_amount":250})
    cid5 = r.json()["challenge_id"]
    cdb = await db()
    try:
        await cdb.execute("UPDATE quiz_champion_challenges SET expires_at=NOW() - INTERVAL '1 hour' WHERE challenge_id=$1", cid5)
    finally:
        await cdb.close()
    a_pre, _ = await get_balances()
    r = admin_s.post(f"{BASE}/api/quiz/champion/admin/expire-stale")
    log_test("admin_expire_stale_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    a_post, _ = await get_balances()
    log_test("expire_alice_refunded_250", a_post - a_pre == Decimal("250.00"), f"diff={a_post-a_pre}")
    cdb = await db()
    try:
        st = await cdb.fetchval("SELECT status FROM quiz_champion_challenges WHERE challenge_id=$1", cid5)
        log_test("expired_status_set", st == "expired", f"status={st}")
    finally:
        await cdb.close()

    # Test 17: REGRESSION free mode
    await reset_state()
    r = alice_s.post(f"{BASE}/api/quiz/champion/challenge",
                     json={"country_code":"CM","mode":"free"})
    a_pre, b_pre = await get_balances()
    log_test("free_mode_create_200", r.status_code == 200, f"{r.status_code}")
    a_post, b_post = await get_balances()
    log_test("free_mode_no_wallet_movement", a_pre == a_post and b_pre == b_post, f"a={a_pre}->{a_post} b={b_pre}->{b_post}")
    cid6 = r.json()["challenge_id"]
    locks = await count_tx(cid6, "quiz_challenge_lock")
    log_test("free_mode_no_lock_tx", locks == 0, f"locks={locks}")

    write_junit()


def write_junit():
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    n = len(results); fails = sum(1 for _,ok,_ in results if not ok)
    body = "\n".join(
        f'<testcase classname="iter125" name="{xesc(name)}">' +
        (f'<failure>{xesc(info)}</failure>' if not ok else '') + '</testcase>'
        for name, ok, info in results
    )
    xml = f'<?xml version="1.0"?><testsuite name="iter125" tests="{n}" failures="{fails}">{body}</testsuite>'
    with open("/app/test_reports/pytest/iter125_results.xml","w") as f:
        f.write(xml)
    print(f"\n=== {n - fails}/{n} passed, {fails} failures ===")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        write_junit()
