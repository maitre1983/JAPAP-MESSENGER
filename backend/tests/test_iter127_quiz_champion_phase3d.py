"""
iter127 — Phase 3.D Quiz Champion: Stabilisation + Viralisation + Automatisation.

Scope:
  - GET /api/quiz/champion/admin/kpis (superadmin) → ledger-sourced KPIs.
  - GET /admin/kpis as non-admin → 403.
  - Leaderboard `paid` earnings now match SUM(amount) of `quiz_challenge_release`
    rows (no more pot×0.9 approximation).
  - `_lazy_expire` idempotency: artificially expire a paid challenge, call
    expire-stale TWICE, verify only ONE refund row per stake side.
  - Race-condition guard on accept: two simultaneous accepts → exactly one 200,
    other 409, wallet debited exactly ONCE.
  - Refund idempotency: expire-stale on already-expired+refunded challenge → no
    new refund row, status stays 'expired'.
  - Scheduler smoke: assert import/wiring + log line `[QuizChampionScheduler ...
    loop started]` appears in supervisor backend log.
  - Admin demote → GET /champion/CM = 404.
  - Admin set re-promotes Bob, refusal_count_consecutive resets to 0.
  - REGRESSION: Alice→Bob paid challenge happy path.
  - Frontend smoke: verify `challenge-share-whatsapp` testid is in source bundle.

Custom Python runner (pytest broken locally with web3 plugin).
Emits JUnit XML to /app/test_reports/pytest/iter127_results.xml.
"""
from __future__ import annotations
import os, sys, asyncio, traceback, time, threading
from decimal import Decimal
import requests, asyncpg
from xml.sax.saxutils import escape as xesc
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')
load_dotenv('/app/frontend/.env')

BASE = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
DB_URL = os.environ['DATABASE_URL']
TURNSTILE = os.environ.get('TURNSTILE_TEST_BYPASS_TOKEN', 'JAPAP_E2E_BYPASS_2026')

ALICE = ('alice@japap.com', 'Alice2026!')
BOB   = ('bob@japap.com',   'Test1234!')
ADMIN = ('admin@japap.com', 'JapapAdmin2024!')

results = []


def log_test(name, ok, info=""):
    results.append((name, ok, info))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {str(info)[:300]}")


async def db():
    return await asyncpg.connect(DB_URL)


def refresh_csrf(session: requests.Session):
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf


def post(session: requests.Session, url, **kw):
    refresh_csrf(session)
    return session.post(url, **kw)


def login(session: requests.Session, email, pwd):
    session.get(f"{BASE}/api/auth/me")
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf
    r = session.post(f"{BASE}/api/auth/login",
                     json={"email": email, "password": pwd, "turnstile_token": TURNSTILE})
    if r.status_code != 200:
        return False, f"{r.status_code}: {r.text[:200]}"
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


# ─────────────────────────────────────────────────────────────────────
# Suite
# ─────────────────────────────────────────────────────────────────────

async def main():
    if not BASE:
        log_test("env_check_REACT_APP_BACKEND_URL", False, "missing"); write_junit(); sys.exit(1)
    log_test("env_check_REACT_APP_BACKEND_URL", True, BASE)

    await reset_state()

    # ─── Admin login ───────────────────────────────────────────────
    admin_s = requests.Session()
    ok, info = login(admin_s, *ADMIN)
    log_test("admin_login", ok, info)
    if not ok:
        write_junit(); return

    # ─── Anonymous user → 403/401 on /admin/kpis ──────────────────
    anon_s = requests.Session()
    r = anon_s.get(f"{BASE}/api/quiz/champion/admin/kpis?window_days=30")
    log_test("admin_kpis_anon_unauthorized", r.status_code in (401, 403),
             f"{r.status_code}: {r.text[:120]}")

    # ─── Authenticated NON-admin (Alice) → 403 ────────────────────
    alice_s = requests.Session()
    ok_a, info_a = login(alice_s, *ALICE)
    log_test("alice_login", ok_a, info_a)
    r = alice_s.get(f"{BASE}/api/quiz/champion/admin/kpis?window_days=30")
    log_test("admin_kpis_non_admin_403", r.status_code == 403,
             f"{r.status_code}: {r.text[:120]}")

    # ─── Admin → 200 with ledger-sourced fields ───────────────────
    r = admin_s.get(f"{BASE}/api/quiz/champion/admin/kpis?window_days=30")
    log_test("admin_kpis_admin_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    kpis = r.json() if r.status_code == 200 else {}
    expected_keys = {"window_days","challenges_total","by_mode","gmv","revenue_japap",
                     "refunds_total","engagement_bonus_pts","active_champions",
                     "top_countries","top_refusers"}
    log_test("admin_kpis_has_all_fields",
             expected_keys.issubset(kpis.keys()),
             f"missing={expected_keys - set(kpis.keys())}")
    log_test("admin_kpis_window_days_30", kpis.get("window_days") == 30,
             f"got={kpis.get('window_days')}")
    log_test("admin_kpis_top_countries_is_list",
             isinstance(kpis.get("top_countries"), list), str(type(kpis.get("top_countries"))))
    log_test("admin_kpis_top_refusers_is_list",
             isinstance(kpis.get("top_refusers"), list), str(type(kpis.get("top_refusers"))))

    # ─── Verify GMV/Revenue/Refunds match LEDGER directly ─────────
    cdb = await db()
    try:
        ledger_revenue = await cdb.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM transactions "
            "WHERE type='quiz_challenge_commission' AND created_at > NOW() - INTERVAL '30 days'")
        ledger_refunds = await cdb.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM transactions "
            "WHERE type='quiz_challenge_refund' AND created_at > NOW() - INTERVAL '30 days'")
        ledger_bonus = await cdb.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM transactions "
            "WHERE type='quiz_challenge_bonus' AND created_at > NOW() - INTERVAL '30 days'")
        ledger_gmv = await cdb.fetchval(
            "SELECT COALESCE(SUM(stake_amount*2),0) FROM quiz_champion_challenges "
            "WHERE mode='paid' AND status='completed' AND completed_at > NOW() - INTERVAL '30 days'")
    finally:
        await cdb.close()
    log_test("admin_kpis_revenue_matches_ledger",
             abs(float(kpis.get("revenue_japap", 0)) - float(ledger_revenue)) < 0.01,
             f"api={kpis.get('revenue_japap')} ledger={ledger_revenue}")
    log_test("admin_kpis_refunds_matches_ledger",
             abs(float(kpis.get("refunds_total", 0)) - float(ledger_refunds)) < 0.01,
             f"api={kpis.get('refunds_total')} ledger={ledger_refunds}")
    log_test("admin_kpis_bonus_matches_ledger",
             abs(float(kpis.get("engagement_bonus_pts", 0)) - float(ledger_bonus)) < 0.01,
             f"api={kpis.get('engagement_bonus_pts')} ledger={ledger_bonus}")
    log_test("admin_kpis_gmv_matches_challenges_table",
             abs(float(kpis.get("gmv", 0)) - float(ledger_gmv)) < 0.01,
             f"api={kpis.get('gmv')} sql={ledger_gmv}")

    # ─── Leaderboard earnings now sourced from `quiz_challenge_release` ─
    r = admin_s.get(f"{BASE}/api/quiz/champion/leaderboard/challengers?window_days=30")
    log_test("leaderboard_challengers_200", r.status_code == 200, f"{r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    paid = body.get("paid", []) or []
    cdb = await db()
    try:
        # For each paid leader, verify earnings == SUM(quiz_challenge_release) for that user_id
        # joined via challenges.escrow_payout_tx_id over the same window.
        for u in paid:
            uid = u.get("user_id")
            ledger_earned = await cdb.fetchval(
                """SELECT COALESCE(SUM(t.amount), 0) FROM transactions t
                     JOIN quiz_champion_challenges c ON c.escrow_payout_tx_id = t.tx_id
                    WHERE t.type='quiz_challenge_release' AND t.to_user_id=$1
                      AND c.completed_at > NOW() - INTERVAL '30 days'""", uid)
            log_test(f"leaderboard_earnings_ledger_match_{uid[:6]}",
                     abs(float(u.get("earnings", 0)) - float(ledger_earned)) < 0.01,
                     f"api={u.get('earnings')} ledger={ledger_earned}")
    finally:
        await cdb.close()

    # ─── Idempotent expire-stale ──────────────────────────────────
    # Create a paid pending challenge, force expires_at to past, run expire-stale twice.
    await reset_state()
    alice_s2 = requests.Session(); login(alice_s2, *ALICE)
    r = post(alice_s2, f"{BASE}/api/quiz/champion/challenge",
                      json={"country_code":"CM","mode":"paid","stake_amount":500})
    log_test("expire_test_create_challenge", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    cid = r.json().get("challenge_id") if r.status_code == 200 else None
    if cid:
        cdb = await db()
        try:
            await cdb.execute("UPDATE quiz_champion_challenges SET expires_at=NOW() - INTERVAL '1 hour' WHERE challenge_id=$1", cid)
            refunds_before = await cdb.fetchval(
                "SELECT COUNT(*) FROM transactions WHERE type='quiz_challenge_refund' AND notes LIKE $1", f'%{cid}%')
        finally:
            await cdb.close()
        # First expire-stale call
        r1 = post(admin_s, f"{BASE}/api/quiz/champion/admin/expire-stale")
        log_test("expire_stale_first_call_200", r1.status_code == 200, f"{r1.status_code}: {r1.text[:200]}")
        await asyncio.sleep(0.3)
        cdb = await db()
        try:
            refunds_after_1 = await cdb.fetchval(
                "SELECT COUNT(*) FROM transactions WHERE type='quiz_challenge_refund' AND notes LIKE $1", f'%{cid}%')
            status_after_1 = await cdb.fetchval(
                "SELECT status FROM quiz_champion_challenges WHERE challenge_id=$1", cid)
        finally:
            await cdb.close()
        log_test("expire_stale_status_expired_after_first",
                 status_after_1 == "expired", f"status={status_after_1}")
        log_test("expire_stale_one_refund_per_stake_side_after_first",
                 (refunds_after_1 - refunds_before) == 1,
                 f"before={refunds_before} after={refunds_after_1} (challenger only since pending)")
        # Second expire-stale call (idempotency)
        r2 = post(admin_s, f"{BASE}/api/quiz/champion/admin/expire-stale")
        log_test("expire_stale_second_call_200", r2.status_code == 200, f"{r2.status_code}")
        await asyncio.sleep(0.3)
        cdb = await db()
        try:
            refunds_after_2 = await cdb.fetchval(
                "SELECT COUNT(*) FROM transactions WHERE type='quiz_challenge_refund' AND notes LIKE $1", f'%{cid}%')
            status_after_2 = await cdb.fetchval(
                "SELECT status FROM quiz_champion_challenges WHERE challenge_id=$1", cid)
        finally:
            await cdb.close()
        log_test("expire_stale_idempotent_no_extra_refund",
                 refunds_after_2 == refunds_after_1,
                 f"after_1st={refunds_after_1} after_2nd={refunds_after_2}")
        log_test("expire_stale_status_stays_expired",
                 status_after_2 == "expired", f"status={status_after_2}")

    # ─── Race condition: two simultaneous accepts → 1×200 + 1×409 ──
    await reset_state()
    alice_s3 = requests.Session(); login(alice_s3, *ALICE)
    r = post(alice_s3, f"{BASE}/api/quiz/champion/challenge",
                      json={"country_code":"CM","mode":"paid","stake_amount":500})
    log_test("race_test_create_challenge", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    cid_race = r.json().get("challenge_id") if r.status_code == 200 else None
    if cid_race:
        bob_s_a = requests.Session(); login(bob_s_a, *BOB)
        bob_s_b = requests.Session(); login(bob_s_b, *BOB)
        results_race = {}

        def fire(label, sess):
            try:
                rr = sess.post(f"{BASE}/api/quiz/champion/challenge/{cid_race}/accept", timeout=15)
                results_race[label] = (rr.status_code, rr.text[:120])
            except Exception as e:
                results_race[label] = (-1, str(e))

        t1 = threading.Thread(target=fire, args=("A", bob_s_a))
        t2 = threading.Thread(target=fire, args=("B", bob_s_b))
        t1.start(); t2.start(); t1.join(); t2.join()
        codes = sorted([results_race["A"][0], results_race["B"][0]])
        log_test("race_accept_one_200_one_409",
                 codes == [200, 409], f"codes={codes} details={results_race}")

        # Verify wallet debited exactly ONCE for Bob (50000 - 500 = 49500)
        cdb = await db()
        try:
            bob_bal = await cdb.fetchval("SELECT balance FROM wallets WHERE user_id=(SELECT user_id FROM users WHERE email='bob@japap.com')")
            n_lock_for_bob = await cdb.fetchval(
                """SELECT COUNT(*) FROM transactions
                    WHERE type='quiz_challenge_lock' AND notes LIKE $1
                      AND from_user_id=(SELECT user_id FROM users WHERE email='bob@japap.com')""", f'%{cid_race}%')
        finally:
            await cdb.close()
        log_test("race_bob_wallet_debited_once",
                 Decimal(str(bob_bal)) == Decimal("49500"),
                 f"bob_bal={bob_bal} (expected 49500)")
        log_test("race_only_one_lock_tx_for_bob",
                 n_lock_for_bob == 1, f"lock_count={n_lock_for_bob}")

    # ─── Scheduler smoke: log line in supervisor backend.err.log ──
    try:
        with open("/var/log/supervisor/backend.err.log") as f:
            log_content = f.read()
    except Exception:
        log_content = ""
    log_test("scheduler_loop_started_log_present",
             "[QuizChampionScheduler" in log_content and "loop started" in log_content,
             f"len(log)={len(log_content)}")
    log_test("scheduler_promote_tick_log_present",
             "[quiz-champion-promote]" in log_content,
             "promote tick log line absent" if "[quiz-champion-promote]" not in log_content else "ok")

    # ─── Admin demote/set ──────────────────────────────────────────
    # demote
    r = post(admin_s, f"{BASE}/api/quiz/champion/admin/CM/demote",
                     json={"reason":"test_iter127"})
    log_test("admin_demote_CM_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    r = admin_s.get(f"{BASE}/api/quiz/champion/CM")
    log_test("get_champion_CM_after_demote_404", r.status_code == 404,
             f"{r.status_code}: {r.text[:200]}")

    # set Bob back
    cdb = await db()
    try:
        bob_id = await cdb.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        # Pre-set refusal_count_consecutive to a non-zero value to verify reset
        await cdb.execute("UPDATE quiz_country_champions SET refusal_count_consecutive=3 WHERE country_code='CM'")
    finally:
        await cdb.close()
    r = post(admin_s, f"{BASE}/api/quiz/champion/admin/CM/set", json={"user_id": bob_id})
    log_test("admin_set_CM_bob_200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    cdb = await db()
    try:
        row = await cdb.fetchrow("SELECT user_id, demoted_at, refusal_count_consecutive FROM quiz_country_champions WHERE country_code='CM'")
    finally:
        await cdb.close()
    log_test("admin_set_repromotes_bob",
             row and row["user_id"] == bob_id and row["demoted_at"] is None,
             f"row={dict(row) if row else None}")
    log_test("admin_set_resets_refusal_count_to_0",
             row and int(row["refusal_count_consecutive"] or 0) == 0,
             f"refusal_count={row and row['refusal_count_consecutive']}")

    # ─── Idempotent /admin/promote-all ────────────────────────────
    r1 = post(admin_s, f"{BASE}/api/quiz/champion/admin/promote-all")
    log_test("promote_all_first_call_200", r1.status_code == 200, f"{r1.status_code}")
    r2 = post(admin_s, f"{BASE}/api/quiz/champion/admin/promote-all")
    log_test("promote_all_second_call_200", r2.status_code == 200, f"{r2.status_code}")
    if r1.status_code == 200 and r2.status_code == 200:
        b1, b2 = r1.json(), r2.json()
        # Idempotent = the second call must NOT introduce any new promotion (the
        # state has converged after the first call).
        log_test("promote_all_idempotent_no_extra_promotion_on_2nd",
                 len(b2.get("promoted", [])) == 0,
                 f"first_promoted={b1.get('promoted')} second_promoted={b2.get('promoted')}")

    # ─── Frontend smoke: testid string in source ──────────────────
    try:
        with open("/app/frontend/src/components/games/ChallengeShare.jsx") as f:
            src = f.read()
        log_test("frontend_challenge_share_whatsapp_testid",
                 "challenge-share-whatsapp" in src, "missing testid")
        log_test("frontend_challenge_share_telegram_testid",
                 "challenge-share-telegram" in src, "missing testid")
        log_test("frontend_challenge_share_copy_testid",
                 "challenge-share-copy" in src, "missing testid")
        log_test("frontend_deep_link_to_country",
                 "/games/quiz/champion/" in src, "missing deep-link path")
    except Exception as e:
        log_test("frontend_source_readable", False, str(e))

    write_junit()


def write_junit():
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    n = len(results); fails = sum(1 for _,ok,_ in results if not ok)
    body = "\n".join(
        f'<testcase classname="iter127" name="{xesc(name)}">' +
        (f'<failure>{xesc(str(info))}</failure>' if not ok else '') + '</testcase>'
        for name, ok, info in results
    )
    xml = f'<?xml version="1.0"?><testsuite name="iter127" tests="{n}" failures="{fails}">{body}</testsuite>'
    with open("/app/test_reports/pytest/iter127_results.xml","w") as f:
        f.write(xml)
    print(f"\n=== {n - fails}/{n} passed, {fails} failures ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        write_junit()
