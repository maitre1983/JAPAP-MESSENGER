"""iter121 — Quiz Phase 2: brute-force lock-in on POST /api/quiz/answer.

Validates the new `revealed_options` JSONB column (added by _DDL in
/app/backend/routes/quiz.py) which locks the FIRST selected_option per
(run_id, question_idx). Subsequent calls with a DIFFERENT option must
return 409 with detail starting "Cette question a déjà été révélée".
Calling with the SAME option must remain idempotent (200 OK).

Run with: python /app/backend/tests/test_iter121_quiz_brute_force_lock.py
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

results = []
def report(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append({"name": name, "ok": ok, "detail": detail})


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
    last_err = None
    for attempt in range(6):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login",
                       json={"email": email, "password": pw,
                             "turnstile_token": TURNSTILE}, timeout=90)
            if r.status_code in (502, 503, 504):
                last_err = f"{r.status_code} {r.text[:80]}"
                print(f"  login attempt {attempt+1}: {r.status_code}, retrying…")
                time.sleep(5)
                continue
            assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text[:200]}"
            s.get(f"{BASE_URL}/api/auth/me", timeout=30)
            return s
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            print(f"  login attempt {attempt+1} timed out, retrying…")
            time.sleep(3)
    raise AssertionError(f"login {email} failed after retries: {last_err}")


def _csrf(s: requests.Session) -> dict:
    return {"X-CSRF-Token": s.cookies.get("csrf_token") or "",
            "Content-Type": "application/json"}


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


async def _get_revealed(rid: str):
    c = await asyncpg.connect(DATABASE_URL)
    try:
        r = await c.fetchrow("SELECT revealed_options FROM quiz_user_runs WHERE id=$1", rid)
        if not r:
            return None
        raw = r["revealed_options"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return None
        return raw
    finally:
        await c.close()


def run():
    print("\n=== iter121 Quiz brute-force lock-in tests ===\n")

    # Sanity: column exists (auto-applied by DDL on backend startup)
    print("[0] Verify revealed_options column exists")
    async def _col_exists():
        c = await asyncpg.connect(DATABASE_URL)
        try:
            row = await c.fetchrow(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='quiz_user_runs' AND column_name='revealed_options'")
            return row is not None
        finally:
            await c.close()
    col_ok = asyncio.new_event_loop().run_until_complete(_col_exists())
    report("revealed_options column exists in quiz_user_runs", col_ok)
    if not col_ok:
        _summary(); return

    # Login Bob
    print("\n[setup] Login Bob & wipe quiz runs")
    bob = _login(BOB_EMAIL, BOB_PASSWORD)
    bob_uid = asyncio.new_event_loop().run_until_complete(_get_uid(BOB_EMAIL))
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))
    report("Bob login + wipe", bool(bob_uid))

    # Start a run
    print("\n[1] /quiz/start")
    r = bob.post(f"{BASE_URL}/api/quiz/start", json={}, headers=_csrf(bob), timeout=30)
    if r.status_code != 200:
        report("/quiz/start", False, f"{r.status_code} {r.text[:200]}")
        _summary(); return
    payload = r.json()
    rid = payload["run_id"]
    qcount = len(payload.get("questions", []))
    report("/quiz/start 200 + 5 questions", qcount == 5, f"got {qcount}")

    # ── 2. First /answer call: q0 opt=0 → 200 with {correct, question_idx}
    print("\n[2] First /answer q=0 opt=0 → 200")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": 0},
                 headers=_csrf(bob), timeout=15)
    body = r.json() if r.ok else {}
    ok = (r.status_code == 200 and "correct" in body and isinstance(body["correct"], bool)
          and body.get("question_idx") == 0)
    report("first reveal q0 opt0 → 200 with {correct,question_idx}", ok,
           f"{r.status_code} body={body}")
    first_correct = body.get("correct")

    # ── 3. SAME call again → 200 idempotent (same correct value)
    print("\n[3] SAME /answer q=0 opt=0 again → 200 idempotent")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": 0},
                 headers=_csrf(bob), timeout=15)
    body2 = r.json() if r.ok else {}
    ok = r.status_code == 200 and body2.get("correct") == first_correct and body2.get("question_idx") == 0
    report("idempotent same opt → 200 same correct", ok,
           f"{r.status_code} body={body2}")

    # ── 4. Different selected_option (1, 2, 3) → all 409
    print("\n[4] DIFFERENT options on q=0 → 409 each")
    for opt in (1, 2, 3):
        r = bob.post(f"{BASE_URL}/api/quiz/answer",
                     json={"run_id": rid, "question_idx": 0, "selected_option": opt},
                     headers=_csrf(bob), timeout=15)
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:200]
        ok = r.status_code == 409 and detail.startswith("Cette question a déjà été révélée")
        report(f"different opt={opt} → 409 with French detail", ok,
               f"{r.status_code} detail={detail!r}")

    # ── 5. Different question_idx (q=1) is independent → 200
    print("\n[5] Different question_idx q=1 opt=2 → 200 (independent lock)")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 1, "selected_option": 2},
                 headers=_csrf(bob), timeout=15)
    body3 = r.json() if r.ok else {}
    ok = r.status_code == 200 and "correct" in body3 and body3.get("question_idx") == 1
    report("q=1 reveal independent → 200", ok, f"{r.status_code} body={body3}")

    # ── 5b. Calling q=1 with different opt → 409
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 1, "selected_option": 0},
                 headers=_csrf(bob), timeout=15)
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text[:200]
    ok = r.status_code == 409 and detail.startswith("Cette question a déjà été révélée")
    report("q=1 then different opt → 409", ok, f"{r.status_code} detail={detail!r}")

    # ── 6. DB inspection: revealed_options dict {"0":0, "1":2}
    print("\n[6] DB revealed_options dict shape")
    revealed = asyncio.new_event_loop().run_until_complete(_get_revealed(rid))
    ok = isinstance(revealed, dict) and revealed.get("0") == 0 and revealed.get("1") == 2
    report("revealed_options DB has {'0':0,'1':2}", ok, f"got={revealed}")

    # ── 7. /quiz/submit still works after multiple reveals
    print("\n[7] /quiz/submit after reveals → 200")
    r = bob.post(f"{BASE_URL}/api/quiz/submit",
                 json={"run_id": rid, "answers": [0, 2, 0, 1, 3]},
                 headers=_csrf(bob), timeout=20)
    sj = r.json() if r.ok else {}
    ok = r.status_code == 200 and "correct_count" in sj and "points_awarded" in sj
    report("/quiz/submit ok after reveals", ok,
           f"{r.status_code} keys={list(sj.keys())[:8]}")

    # ── 8. Any /answer after submit → 409 'Run déjà soumis'
    print("\n[8] /answer after submit → 409 'Run déjà soumis'")
    for case in [(0, 0), (2, 1), (3, 3)]:
        r = bob.post(f"{BASE_URL}/api/quiz/answer",
                     json={"run_id": rid, "question_idx": case[0], "selected_option": case[1]},
                     headers=_csrf(bob), timeout=15)
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:200]
        ok = r.status_code == 409 and "Run déjà soumis" in detail
        report(f"after submit q={case[0]} opt={case[1]} → 409 'Run déjà soumis'", ok,
               f"{r.status_code} detail={detail!r}")

    # ── 9. Regression: GET /api/admin/games/quiz still has quiz_auto_advance_delays_ms
    print("\n[9] Regression: admin GET /api/admin/games/quiz includes quiz_auto_advance_delays_ms")
    try:
        admin = _login_admin()
        r = admin.get(f"{BASE_URL}/api/admin/games/quiz", timeout=15)
        cfg = r.json().get("config", {}) if r.ok else {}
        delays = cfg.get("quiz_auto_advance_delays_ms")
        ok = r.status_code == 200 and isinstance(delays, list) and len(delays) >= 1
        report("admin config has quiz_auto_advance_delays_ms list", ok,
               f"{r.status_code} delays={delays}")
        # default check (only informational — admin may have changed it)
        is_default = delays == [900, 800, 700, 550, 400]
        report("delays match documented default [900,800,700,550,400]", is_default,
               f"got={delays} (informational)")
    except Exception as e:
        report("admin login (2FA) for regression", False, str(e)[:200])

    # Cleanup
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))

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
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    with open("/app/test_reports/pytest/iter121_results.xml", "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<testsuite name="iter121_quiz_brute_force_lock" tests="{total}" failures="{total - passed}">\n')
        for r in results:
            safe = (r["detail"] or "")[:200].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            f.write(f'  <testcase classname="iter121" name="{r["name"]}">\n')
            if not r["ok"]:
                f.write(f'    <failure message="{safe}"/>\n')
            f.write('  </testcase>\n')
        f.write('</testsuite>\n')


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        _summary()
