"""iter120 — Quiz Phase 2 UX backend validation.

Tests (run with `python test_iter120_quiz_phase2.py`):
  Admin config:
    - GET  /api/admin/games/quiz returns quiz_auto_advance_delays_ms
    - PUT  /api/admin/games/quiz updates persistently
    - PUT  validation: rejects [200], [3500], [], "not_a_list"
  User flow (Bob):
    - POST /api/quiz/start returns auto_advance_delays_ms (and full payload)
    - POST /api/quiz/answer returns {correct, question_idx} and is stateless
    - POST /api/quiz/answer cross-user → 403
    - POST /api/quiz/answer after submit → 409
    - POST /api/quiz/answer with question_idx=10 → 400
    - POST /api/quiz/answer after timer expiry → 410
    - POST /api/quiz/submit still works after /answer calls (5 answers)
    - Daily limit guard: 4th /start → 429
  Regressions:
    - Wheel /api/wheel/status, /api/wheel/spin
    - Tap /api/tap/start, /api/tap/submit
    - Payment force-verify 404 on missing tx, 403 on cross-user
"""
from __future__ import annotations
import os, sys, time, asyncio, json, traceback
import requests
import asyncpg
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
TURNSTILE = "JAPAP_E2E_BYPASS_2026"

ADMIN_EMAIL = "emileparfait2003@gmail.com"
ADMIN_PASSWORD = "Gerard0103@"
BOB_EMAIL = "bob@japap.com"
BOB_PASSWORD = "Test1234!"
ALICE_EMAIL = "alice@japap.com"
ALICE_PASSWORD = "Alice2026!"

# ── Result tracking ──
results = []
def report(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append({"name": name, "ok": ok, "detail": detail})

# ── Helpers ──
async def _fetch_otp(email: str, purpose: str) -> str:
    c = await asyncpg.connect(DATABASE_URL)
    try:
        for _ in range(20):
            row = await c.fetchrow(
                "SELECT code FROM email_otps WHERE email=$1 AND purpose=$2 AND used=FALSE "
                "ORDER BY created_at DESC LIMIT 1", email, purpose)
            if row:
                return row["code"]
            await asyncio.sleep(0.5)
    finally:
        await c.close()
    raise RuntimeError(f"OTP not found for {email}/{purpose}")

def _login_admin() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
                     "turnstile_token": TURNSTILE}, timeout=60)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    if (body.get("status") == "otp_required" or body.get("requires_2fa")
            or body.get("two_factor_required") or "challenge_id" in body):
        time.sleep(2)
        code = asyncio.new_event_loop().run_until_complete(
            _fetch_otp(ADMIN_EMAIL, "login_2fa"))
        last = None
        for path in ("/api/auth/verify-2fa", "/api/auth/login/verify-otp",
                     "/api/auth/login/2fa", "/api/auth/2fa/verify"):
            payload = {"email": ADMIN_EMAIL, "code": code, "otp": code,
                       "turnstile_token": TURNSTILE}
            if "challenge_id" in body:
                payload["challenge_id"] = body["challenge_id"]
            r2 = s.post(f"{BASE_URL}{path}", json=payload, timeout=30)
            last = r2
            if r2.status_code == 200:
                return s
        raise AssertionError(f"2FA failed: {last.status_code} {last.text[:200]}")
    return s

def _login(email: str, pw: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": email, "password": pw,
                     "turnstile_token": TURNSTILE}, timeout=30)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text[:200]}"
    # Ensure csrf cookie
    s.get(f"{BASE_URL}/api/auth/me", timeout=15)
    return s

def _csrf(s: requests.Session) -> dict:
    return {"X-CSRF-Token": s.cookies.get("csrf_token") or "",
            "Content-Type": "application/json"}

# ── Cleanup user state ──
async def _wipe_quiz(uid: str):
    c = await asyncpg.connect(DATABASE_URL)
    try:
        await c.execute("DELETE FROM quiz_user_runs WHERE user_id=$1", uid)
    finally:
        await c.close()

async def _get_uid(email: str) -> str:
    c = await asyncpg.connect(DATABASE_URL)
    try:
        r = await c.fetchrow("SELECT user_id FROM users WHERE email=$1", email)
        return r["user_id"] if r else ""
    finally:
        await c.close()

def run():
    print("\n=== iter120 Quiz Phase 2 backend tests ===\n")

    # Login admin & users
    print("[setup] Logging in admin (2FA)…")
    try:
        admin = _login_admin()
        admin.get(f"{BASE_URL}/api/auth/me", timeout=15)
        report("admin login (2FA)", True)
    except Exception as e:
        report("admin login (2FA)", False, str(e)[:200])
        return

    print("[setup] Logging in Bob & Alice…")
    bob = _login(BOB_EMAIL, BOB_PASSWORD)
    alice = _login(ALICE_EMAIL, ALICE_PASSWORD)

    bob_uid = asyncio.new_event_loop().run_until_complete(_get_uid(BOB_EMAIL))
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))

    # ── Admin GET /api/admin/games/quiz ──
    print("\n[1] Admin GET quiz config")
    r = admin.get(f"{BASE_URL}/api/admin/games/quiz", timeout=15)
    ok = r.status_code == 200 and "quiz_auto_advance_delays_ms" in r.json().get("config", {})
    report("GET /admin/games/quiz returns quiz_auto_advance_delays_ms", ok,
           f"status={r.status_code} delays={r.json().get('config',{}).get('quiz_auto_advance_delays_ms') if r.ok else r.text[:120]}")

    # ── PUT update persistently ──
    print("\n[2] PUT update quiz_auto_advance_delays_ms")
    new_delays = [1200, 1000, 800, 600, 400]
    r = admin.put(f"{BASE_URL}/api/admin/games/quiz",
                  json={"quiz_auto_advance_delays_ms": new_delays},
                  headers=_csrf(admin), timeout=15)
    ok = r.status_code == 200 and r.json().get("config", {}).get("quiz_auto_advance_delays_ms") == new_delays
    report("PUT quiz_auto_advance_delays_ms persists", ok, f"{r.status_code} {r.text[:120]}")

    r2 = admin.get(f"{BASE_URL}/api/admin/games/quiz", timeout=15)
    ok2 = r2.json().get("config", {}).get("quiz_auto_advance_delays_ms") == new_delays
    report("Re-GET shows persisted delays", ok2)

    # ── Validation: reject invalid bodies ──
    print("\n[3] PUT validation rejects bad inputs")
    cases = [
        ("[200] (below 300)", {"quiz_auto_advance_delays_ms": [200]}),
        ("[3500] (above 3000)", {"quiz_auto_advance_delays_ms": [3500]}),
        ("[] (empty)", {"quiz_auto_advance_delays_ms": []}),
        ('"not_a_list"', {"quiz_auto_advance_delays_ms": "not_a_list"}),
    ]
    for label, body in cases:
        r = admin.put(f"{BASE_URL}/api/admin/games/quiz",
                      json=body, headers=_csrf(admin), timeout=15)
        ok = r.status_code in (400, 422)
        report(f"reject {label}", ok, f"got {r.status_code}: {r.text[:100]}")

    # Restore default delays
    admin.put(f"{BASE_URL}/api/admin/games/quiz",
              json={"quiz_auto_advance_delays_ms": [900, 800, 700, 550, 400]},
              headers=_csrf(admin), timeout=15)

    # ── Bob: /quiz/start payload ──
    print("\n[4] Bob /quiz/start payload")
    r = bob.post(f"{BASE_URL}/api/quiz/start", json={},
                 headers=_csrf(bob), timeout=30)
    ok = r.status_code == 200
    payload = r.json() if ok else {}
    keys = ["run_id", "session_id", "questions", "auto_advance_delays_ms",
            "auto_advance_delay_ms", "auto_advance_enabled",
            "timer_mode", "timer_per_question_seconds", "time_limit_seconds"]
    missing = [k for k in keys if k not in payload]
    has5 = ok and len(payload.get("questions", [])) == 5
    has_delays = ok and isinstance(payload.get("auto_advance_delays_ms"), list) and len(payload["auto_advance_delays_ms"]) >= 1
    report("/quiz/start 200 & 5 questions", ok and has5, f"status={r.status_code} qs={len(payload.get('questions',[]))} body={r.text[:200]}")
    report("/quiz/start contains all required keys", ok and not missing, f"missing={missing}")
    report("/quiz/start auto_advance_delays_ms is list", has_delays,
           f"delays={payload.get('auto_advance_delays_ms')}")

    if not ok:
        print("Cannot proceed without /start"); _summary(); return

    run1 = payload
    rid = run1["run_id"]

    # ── /quiz/answer happy path + statelessness ──
    print("\n[5] Bob /quiz/answer reveal")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": 0},
                 headers=_csrf(bob), timeout=15)
    ok = r.status_code == 200 and "correct" in r.json() and r.json().get("question_idx") == 0
    report("/quiz/answer returns {correct, question_idx}", ok, f"{r.status_code} {r.text[:150]}")

    # Call /answer multiple times to verify no mutation
    for i in range(5):
        bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": i, "selected_option": (i % 4)},
                 headers=_csrf(bob), timeout=15)

    # Verify DB state unchanged (submitted_at IS NULL, answers IS NULL)
    async def _check_unmutated():
        c = await asyncpg.connect(DATABASE_URL)
        try:
            r = await c.fetchrow("SELECT submitted_at, answers, correct_count FROM quiz_user_runs WHERE id=$1", rid)
            return r["submitted_at"] is None and r["answers"] is None and r["correct_count"] is None
        finally:
            await c.close()
    unmutated = asyncio.new_event_loop().run_until_complete(_check_unmutated())
    report("/quiz/answer does NOT mutate DB state", unmutated)

    # ── Cross-user 403 ──
    print("\n[6] /quiz/answer cross-user → 403")
    r = alice.post(f"{BASE_URL}/api/quiz/answer",
                   json={"run_id": rid, "question_idx": 0, "selected_option": 0},
                   headers=_csrf(alice), timeout=15)
    report("Alice on Bob's run → 403", r.status_code == 403, f"{r.status_code} {r.text[:120]}")

    # ── question_idx out-of-range ──
    print("\n[7] /quiz/answer out-of-range")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 10, "selected_option": 0},
                 headers=_csrf(bob), timeout=15)
    # ge=0,le=19 in pydantic → 422. Endpoint also raises 400 if >= len(order).
    report("question_idx=10 → 400 or 422", r.status_code in (400, 422), f"{r.status_code} {r.text[:120]}")

    # ── /submit completes successfully (run still works after /answer) ──
    print("\n[8] /quiz/submit after /answer calls")
    r = bob.post(f"{BASE_URL}/api/quiz/submit",
                 json={"run_id": rid, "answers": [0, 1, 2, 3, 0]},
                 headers=_csrf(bob), timeout=20)
    sj = r.json() if r.ok else {}
    ok = r.status_code == 200 and "correct_count" in sj and "points_awarded" in sj \
         and "perfect" in sj and "accuracy" in sj and "correct_by_question" in sj
    report("/quiz/submit ok with full schema", ok, f"{r.status_code} keys={list(sj.keys())[:8]}")

    # ── /quiz/answer after submit → 409 ──
    print("\n[9] /quiz/answer after submit → 409")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": 0},
                 headers=_csrf(bob), timeout=15)
    report("/quiz/answer after submit → 409", r.status_code == 409, f"{r.status_code} {r.text[:120]}")

    # ── 410 expiry test (manipulate started_at directly to avoid waiting 70s) ──
    print("\n[10] /quiz/answer after expiry → 410")
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))  # reset day count partly
    r = bob.post(f"{BASE_URL}/api/quiz/start", json={}, headers=_csrf(bob), timeout=30)
    if r.status_code != 200:
        report("/quiz/start (for expiry test)", False, f"{r.status_code} {r.text[:150]}")
    else:
        rid_exp = r.json()["run_id"]
        time_limit = r.json()["time_limit_seconds"]
        # Backdate started_at so elapsed > time_limit + grace (10s)
        async def _backdate():
            c = await asyncpg.connect(DATABASE_URL)
            try:
                await c.execute(
                    "UPDATE quiz_user_runs SET started_at = NOW() - ($1 * INTERVAL '1 second') WHERE id=$2",
                    time_limit + 30, rid_exp)
            finally:
                await c.close()
        asyncio.new_event_loop().run_until_complete(_backdate())
        r = bob.post(f"{BASE_URL}/api/quiz/answer",
                     json={"run_id": rid_exp, "question_idx": 0, "selected_option": 0},
                     headers=_csrf(bob), timeout=15)
        report("/quiz/answer after expiry → 410", r.status_code == 410, f"{r.status_code} {r.text[:120]}")

    # ── Daily limit guard: 4th /start → 429 ──
    print("\n[11] Daily limit guard")
    # Force quiz_sessions_per_day=3 (admin may have left it higher)
    admin.put(f"{BASE_URL}/api/admin/games/quiz",
              json={"quiz_sessions_per_day": 3},
              headers=_csrf(admin), timeout=15)
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))
    counts = []
    for i in range(4):
        r = bob.post(f"{BASE_URL}/api/quiz/start", json={}, headers=_csrf(bob), timeout=30)
        counts.append(r.status_code)
        if r.status_code == 200:
            rid_n = r.json()["run_id"]
            # submit so the run counts (started_at::date is the criterion regardless,
            # but we submit to be tidy). Actually count is on COUNT(*) of started_at::date,
            # so even unsubmitted counts. Leave them open.
    ok = counts[0] == 200 and counts[1] == 200 and counts[2] == 200 and counts[3] == 429
    report(f"3 starts ok, 4th=429 (got {counts})", ok)
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))

    # ── REGRESSION: wheel ──
    print("\n[12] Regression — Wheel of Fortune")
    r = bob.get(f"{BASE_URL}/api/wheel/status", timeout=15)
    report("GET /api/wheel/status", r.status_code == 200, f"{r.status_code}")
    r = bob.post(f"{BASE_URL}/api/wheel/spin", json={}, headers=_csrf(bob), timeout=30)
    report("POST /api/wheel/spin", r.status_code in (200, 400, 429), f"{r.status_code} {r.text[:120]}")

    # ── REGRESSION: tap ──
    print("\n[13] Regression — Tap challenge")
    r = bob.post(f"{BASE_URL}/api/tap/start", json={}, headers=_csrf(bob), timeout=15)
    tap_started = r.status_code == 200
    report("POST /api/tap/start", tap_started or r.status_code == 429, f"{r.status_code} {r.text[:140]}")
    if tap_started:
        tap_id = r.json().get("run_id") or r.json().get("tap_id")
        if tap_id:
            time.sleep(0.5)
            r2 = bob.post(f"{BASE_URL}/api/tap/submit",
                          json={"run_id": tap_id, "tap_count": 60, "client_taps": 60, "taps": 60},
                          headers=_csrf(bob), timeout=15)
            report("POST /api/tap/submit", r2.status_code in (200, 400), f"{r2.status_code} {r2.text[:140]}")

    # ── REGRESSION: payment force-verify ──
    print("\n[14] Regression — Payment force-verify")
    r = bob.post(f"{BASE_URL}/api/wallet/deposit/nonexistent_tx_zzz/force-verify",
                 json={}, headers=_csrf(bob), timeout=15)
    report("force-verify missing tx → 404", r.status_code == 404, f"{r.status_code} {r.text[:120]}")

    # Cross-user: pick any tx that belongs to alice if exists, else skip
    async def _alice_tx():
        c = await asyncpg.connect(DATABASE_URL)
        try:
            r = await c.fetchrow(
                "SELECT tx_id FROM transactions WHERE to_user_id=(SELECT user_id FROM users WHERE email=$1) AND type='deposit' ORDER BY created_at DESC LIMIT 1",
                ALICE_EMAIL)
            return r["tx_id"] if r else None
        except Exception as e:
            print(f"  query err: {e}")
            return None
        finally:
            await c.close()
    atx = asyncio.new_event_loop().run_until_complete(_alice_tx())
    if atx:
        r = bob.post(f"{BASE_URL}/api/wallet/deposit/{atx}/force-verify",
                     json={}, headers=_csrf(bob), timeout=15)
        report("force-verify cross-user → 403", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
    else:
        print("  [SKIP] No Alice payment_transactions row to test 403 — using alternate path")
        # Try to seed a dummy alice tx
        async def _seed_tx():
            c = await asyncpg.connect(DATABASE_URL)
            try:
                aid = await c.fetchval("SELECT user_id FROM users WHERE email=$1", ALICE_EMAIL)
                tx_id = f"TEST_iter120_{int(time.time())}"
                await c.execute(
                    "INSERT INTO transactions (tx_id, type, status, amount, to_user_id, reference, created_at) "
                    "VALUES ($1, 'deposit', 'pending', 100, $2, 'TEST', NOW())",
                    tx_id, aid)
                return tx_id
            except Exception as e:
                print(f"  seed err: {e}")
                return None
            finally:
                await c.close()
        seeded = asyncio.new_event_loop().run_until_complete(_seed_tx())
        if seeded:
            r = bob.post(f"{BASE_URL}/api/wallet/deposit/{seeded}/force-verify",
                         json={}, headers=_csrf(bob), timeout=15)
            report("force-verify cross-user → 403", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
        else:
            report("force-verify cross-user → 403", False, "could not seed alice tx (table missing or insert failed) — skipped")

    _summary()


def _summary():
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    print(f"\n=== Summary: {passed}/{total} passed ===")
    failed = [r for r in results if not r["ok"]]
    if failed:
        print("Failures:")
        for f in failed:
            print(f"  - {f['name']}: {f['detail']}")
    # Write JUnit XML
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    with open("/app/test_reports/pytest/iter120_results.xml", "w") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<testsuite name="iter120_quiz_phase2" tests="{total}" failures="{total - passed}">\n')
        for r in results:
            f.write(f'  <testcase classname="iter120" name="{r["name"]}">\n')
            if not r["ok"]:
                f.write(f'    <failure message="{(r["detail"] or "")[:200].replace(chr(34),"&quot;")}"/>\n')
            f.write(f'  </testcase>\n')
        f.write('</testsuite>\n')

if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        _summary()
