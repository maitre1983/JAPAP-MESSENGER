"""iter122 — Quiz Phase 2: Mode Apprentissage (`quiz_show_correct_after_wrong`).

Validates:
 (1) GET  /api/admin/games/quiz exposes new key (default false) and still
     keeps quiz_auto_advance_delays_ms (regression).
 (2) PUT  /api/admin/games/quiz toggles it true and re-GET confirms.
 (3) PUT  validation: bool|None field — pydantic v2 may accept truthy strings
     or 422; either way the toggle path stays sane.
 (4) /quiz/answer returns correct_option (int 0-3) ONLY for wrong answer
     when the toggle is ON; the index maps to the actually-correct displayed
     option (verified through options_order permutation in DB).
 (5) /quiz/answer returns correct_option=null when the user is correct.
 (6) /quiz/answer returns correct_option=null when toggle is OFF.
 (7) Regression: brute-force lock still 409 with learning mode ON.
 (8) Regression: /quiz/submit still scores correctly after multiple reveals.
 (9) Regression: quiz_auto_advance_delays_ms default present.

Run: python /app/backend/tests/test_iter122_quiz_learning_mode.py
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
                     "turnstile_token": TURNSTILE}, timeout=120)
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
                s.get(f"{BASE_URL}/api/auth/me", timeout=15)
                return s
        raise AssertionError(f"2FA failed: {last.status_code} {last.text[:200]}")
    s.get(f"{BASE_URL}/api/auth/me", timeout=15)
    return s


def _login(email: str, pw: str) -> requests.Session:
    s = requests.Session()
    last = None
    for attempt in range(6):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login",
                       json={"email": email, "password": pw,
                             "turnstile_token": TURNSTILE}, timeout=60)
            if r.status_code in (502, 503, 504):
                last = f"{r.status_code}"
                time.sleep(4)
                continue
            assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
            s.get(f"{BASE_URL}/api/auth/me", timeout=15)
            return s
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last = e
            time.sleep(3)
    raise AssertionError(f"login {email} failed: {last}")


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


async def _run_dbinfo(rid: str):
    """Return (options_order_list, session_id) for run."""
    c = await asyncpg.connect(DATABASE_URL)
    try:
        r = await c.fetchrow(
            "SELECT options_order, session_id FROM quiz_user_runs WHERE id=$1", rid)
        if not r:
            return None, None
        raw = r["options_order"]
        if isinstance(raw, str):
            try:
                perms = json.loads(raw)
            except Exception:
                perms = []
        else:
            perms = raw or []
        return perms, r["session_id"]
    finally:
        await c.close()


async def _question_correct_indices(session_id: str):
    """Return list of correct_index per question_idx."""
    c = await asyncpg.connect(DATABASE_URL)
    try:
        s = await c.fetchrow("SELECT question_ids FROM quiz_sessions WHERE id=$1", session_id)
        order = list(s["question_ids"])
        out = []
        for qid in order:
            row = await c.fetchrow(
                "SELECT correct_index FROM quiz_questions WHERE id=$1", int(qid))
            out.append(int(row["correct_index"]))
        return out, order
    finally:
        await c.close()


def _set_toggle(admin: requests.Session, value) -> requests.Response:
    return admin.put(f"{BASE_URL}/api/admin/games/quiz",
                     json={"quiz_show_correct_after_wrong": value},
                     headers=_csrf(admin), timeout=15)


def run():
    print("\n=== iter122 Quiz learning-mode tests ===\n")

    print("[setup] admin login (2FA)")
    admin = None
    for attempt in range(4):
        try:
            admin = _login_admin()
            break
        except Exception as e:
            print(f"  admin login attempt {attempt+1} failed: {str(e)[:120]}")
            time.sleep(5)
    if admin is None:
        report("admin login", False, "all retries failed")
        _summary(); return
    report("admin login", True)

    # Ensure clean baseline: toggle OFF
    _set_toggle(admin, False)

    # ── 1. GET exposes new key, default reflects current (we just set false)
    print("\n[1] GET /api/admin/games/quiz exposes quiz_show_correct_after_wrong + auto_advance_delays_ms")
    r = admin.get(f"{BASE_URL}/api/admin/games/quiz", timeout=15)
    cfg = r.json().get("config", {}) if r.ok else {}
    defaults = r.json().get("defaults", {}) if r.ok else {}
    has_key = "quiz_show_correct_after_wrong" in cfg
    is_bool = isinstance(cfg.get("quiz_show_correct_after_wrong"), bool)
    default_false = defaults.get("quiz_show_correct_after_wrong") is False
    has_delays = isinstance(cfg.get("quiz_auto_advance_delays_ms"), list) and len(cfg.get("quiz_auto_advance_delays_ms", [])) >= 1
    report("config has quiz_show_correct_after_wrong (bool)", has_key and is_bool,
           f"value={cfg.get('quiz_show_correct_after_wrong')}")
    report("DEFAULTS: quiz_show_correct_after_wrong = False", default_false,
           f"defaults.value={defaults.get('quiz_show_correct_after_wrong')}")
    report("regression: quiz_auto_advance_delays_ms still present", has_delays,
           f"delays={cfg.get('quiz_auto_advance_delays_ms')}")
    delays_default = cfg.get("quiz_auto_advance_delays_ms") == [900, 800, 700, 550, 400]
    report("delays match documented default [900,800,700,550,400] (info)", delays_default,
           f"got={cfg.get('quiz_auto_advance_delays_ms')}")

    # ── 2. PUT toggle ON, re-GET confirms
    print("\n[2] PUT toggle ON + re-GET confirms persistence")
    r = _set_toggle(admin, True)
    ok = r.status_code == 200 and r.json().get("config", {}).get("quiz_show_correct_after_wrong") is True
    report("PUT {quiz_show_correct_after_wrong: true} returns 200 with true", ok,
           f"{r.status_code} body={r.text[:200]}")
    r2 = admin.get(f"{BASE_URL}/api/admin/games/quiz", timeout=15)
    persisted = r2.json().get("config", {}).get("quiz_show_correct_after_wrong") is True
    report("re-GET confirms toggle=true persisted", persisted,
           f"value={r2.json().get('config',{}).get('quiz_show_correct_after_wrong')}")

    # ── 3. PUT validation: non-bool string
    print("\n[3] PUT validation: non-bool 'maybe'")
    r = _set_toggle(admin, "maybe")
    # acceptable outcomes: 200 (coerced to true) OR 422 (pydantic strict reject)
    body_val = None
    if r.status_code == 200:
        body_val = r.json().get("config", {}).get("quiz_show_correct_after_wrong")
    accepted_truthy = (r.status_code == 200 and body_val is True)
    rejected_strict = r.status_code in (400, 422)
    report("PUT 'maybe' is either coerced→true OR strictly rejected (422)",
           accepted_truthy or rejected_strict,
           f"{r.status_code} body_val={body_val} (Pydantic v2 default = strict bool)")

    # Ensure final state is True for the next phase
    _set_toggle(admin, True)
    time.sleep(1.2)  # cache TTL is 60s but set_setting() invalidates cache

    # ── 4. Bob: start a run with toggle ON
    print("\n[4] Bob /quiz/start (toggle=ON)")
    bob = _login(BOB_EMAIL, BOB_PASSWORD)
    bob_uid = asyncio.new_event_loop().run_until_complete(_get_uid(BOB_EMAIL))
    asyncio.new_event_loop().run_until_complete(_wipe_quiz(bob_uid))

    r = bob.post(f"{BASE_URL}/api/quiz/start", json={}, headers=_csrf(bob), timeout=30)
    if r.status_code != 200:
        report("/quiz/start", False, f"{r.status_code} {r.text[:200]}")
        _summary(); return
    payload = r.json()
    rid = payload["run_id"]
    questions = payload["questions"]
    report("/quiz/start 200 + 5 questions", len(questions) == 5, f"got {len(questions)}")

    # Resolve DB truth: original correct_index per qidx + options_order permutation
    perms, sid = asyncio.new_event_loop().run_until_complete(_run_dbinfo(rid))
    correct_orig, qids = asyncio.new_event_loop().run_until_complete(
        _question_correct_indices(sid))
    # displayed_correct_idx[qidx] = perm.index(correct_orig[qidx])
    displayed_correct = [perms[i].index(correct_orig[i]) for i in range(5)]
    print(f"  DB truth: displayed_correct_per_q = {displayed_correct}")

    # ── 5. q0 WRONG answer + toggle ON → response.correct_option is the displayed-correct
    print("\n[5] q0 WRONG with toggle ON → correct_option points to the actual correct")
    cidx = displayed_correct[0]
    wrong_pick = (cidx + 1) % 4  # any non-correct displayed option
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": wrong_pick},
                 headers=_csrf(bob), timeout=15)
    body = r.json() if r.ok else {}
    is_wrong = body.get("correct") is False
    co = body.get("correct_option")
    ok_co = isinstance(co, int) and 0 <= co <= 3 and co == cidx
    # Also verify by indexing into the served `questions[0].options` it's the real answer text
    served_correct_text = questions[0]["options"][cidx]
    response_correct_text = questions[0]["options"][co] if isinstance(co, int) and 0 <= co <= 3 else None
    same_text = served_correct_text == response_correct_text
    report("WRONG + ON: response.correct=false", is_wrong, f"body={body}")
    report("WRONG + ON: response.correct_option == displayed_correct_idx", ok_co,
           f"co={co} expected_displayed={cidx}")
    report("WRONG + ON: questions[0].options[co] equals real answer", same_text,
           f"served={served_correct_text!r} response={response_correct_text!r}")

    # ── 6. q1 CORRECT answer + toggle ON → correct_option is null
    print("\n[6] q1 CORRECT with toggle ON → correct_option=null")
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 1, "selected_option": displayed_correct[1]},
                 headers=_csrf(bob), timeout=15)
    body = r.json() if r.ok else {}
    is_correct = body.get("correct") is True
    co_null = body.get("correct_option") is None
    report("CORRECT + ON: response.correct=true", is_correct, f"body={body}")
    report("CORRECT + ON: correct_option is null (no leak)", co_null, f"co={body.get('correct_option')}")

    # ── 7. Regression: brute-force lock still works with toggle ON
    print("\n[7] Brute-force lock still 409 with toggle ON")
    # Same q0 with a DIFFERENT option than what we already locked (wrong_pick)
    second_pick = (wrong_pick + 1) % 4
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": second_pick},
                 headers=_csrf(bob), timeout=15)
    detail = ""
    try: detail = r.json().get("detail", "")
    except Exception: detail = r.text[:200]
    locked = r.status_code == 409 and detail.startswith("Cette question a déjà été révélée")
    report("brute-force same q0 different opt → 409", locked,
           f"{r.status_code} detail={detail!r}")

    # Same option idempotent → 200 (correct_option must reflect SAME value as first call: cidx)
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": wrong_pick},
                 headers=_csrf(bob), timeout=15)
    body = r.json() if r.ok else {}
    idem = (r.status_code == 200 and body.get("correct") is False
            and body.get("correct_option") == cidx)
    report("idempotent same wrong opt → 200 with correct_option preserved", idem,
           f"{r.status_code} body={body}")

    # ── 8. Toggle OFF + reveal a wrong answer → correct_option must be null
    print("\n[8] Toggle OFF: WRONG answer no longer leaks correct_option")
    _set_toggle(admin, False)
    time.sleep(1.5)
    # Use q2 (fresh, not yet revealed). Pick a wrong option
    wrong_pick_q2 = (displayed_correct[2] + 1) % 4
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 2, "selected_option": wrong_pick_q2},
                 headers=_csrf(bob), timeout=15)
    body = r.json() if r.ok else {}
    off_wrong_no_leak = (r.status_code == 200 and body.get("correct") is False
                        and body.get("correct_option") is None)
    report("OFF + WRONG → correct_option=null (default behavior)", off_wrong_no_leak,
           f"{r.status_code} body={body}")

    # Also OFF + CORRECT
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 3, "selected_option": displayed_correct[3]},
                 headers=_csrf(bob), timeout=15)
    body = r.json() if r.ok else {}
    off_corr_null = (r.status_code == 200 and body.get("correct") is True
                     and body.get("correct_option") is None)
    report("OFF + CORRECT → correct_option=null", off_corr_null, f"body={body}")

    # ── 9. Regression: /quiz/submit still scores correctly after multiple reveals
    print("\n[9] /quiz/submit after mixed reveals across all 5 questions")
    # We already have reveals for q0 (wrong_pick), q1 (correct), q2 (wrong_pick_q2),
    # q3 (correct). We must submit answers consistent with locks (or 409 will fire
    # if /submit also enforces lock — it doesn't, but we send our locked picks).
    submit_answers = [
        wrong_pick,                # q0 wrong
        displayed_correct[1],      # q1 correct
        wrong_pick_q2,             # q2 wrong
        displayed_correct[3],      # q3 correct
        displayed_correct[4],      # q4 not yet revealed → submit correct
    ]
    r = bob.post(f"{BASE_URL}/api/quiz/submit",
                 json={"run_id": rid, "answers": submit_answers},
                 headers=_csrf(bob), timeout=20)
    sj = r.json() if r.ok else {}
    expected_correct = 3  # q1, q3, q4
    submit_ok = (r.status_code == 200 and sj.get("correct_count") == expected_correct
                 and "points_awarded" in sj and "accuracy" in sj)
    report(f"/quiz/submit 200 with correct_count=={expected_correct}", submit_ok,
           f"{r.status_code} keys={list(sj.keys())[:8]} cc={sj.get('correct_count')}")

    # ── 10. After submit /answer → 409 'Run déjà soumis' regardless of toggle
    _set_toggle(admin, True)  # toggle on; lock still must beat learning mode
    time.sleep(1.2)
    r = bob.post(f"{BASE_URL}/api/quiz/answer",
                 json={"run_id": rid, "question_idx": 0, "selected_option": wrong_pick},
                 headers=_csrf(bob), timeout=15)
    detail = ""
    try: detail = r.json().get("detail", "")
    except Exception: detail = r.text[:200]
    report("after submit /answer → 409 'Run déjà soumis'",
           r.status_code == 409 and "Run déjà soumis" in detail,
           f"{r.status_code} detail={detail!r}")

    # ── 11. Final regression: GET still has full iter120 fields
    print("\n[11] Final regression sweep")
    r = admin.get(f"{BASE_URL}/api/admin/games/quiz", timeout=15)
    cfg = r.json().get("config", {}) if r.ok else {}
    expected_fields = [
        "quiz_auto_advance_delays_ms",
        "quiz_show_correct_after_wrong",
    ]
    missing = [k for k in expected_fields if k not in cfg]
    report("GET config still has all required keys", not missing, f"missing={missing}")

    # Cleanup: restore default OFF and wipe Bob's runs
    _set_toggle(admin, False)
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
    with open("/app/test_reports/pytest/iter122_results.xml", "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<testsuite name="iter122_quiz_learning_mode" tests="{total}" failures="{total - passed}">\n')
        for r in results:
            safe = (r["detail"] or "")[:200].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            f.write(f'  <testcase classname="iter122" name="{r["name"]}">\n')
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
