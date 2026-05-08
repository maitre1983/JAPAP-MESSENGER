"""iter123 — Quiz Champion par Pays (Phase 3.A FREE mode).

Tests the full Champion + Challenge flow end-to-end, using:
  - Alice as the challenger
  - Bob as the champion (re-seeded as CM champion via admin API)
  - SuperAdmin for admin-only endpoints

Run: python /app/backend/tests/test_iter123_quiz_champion.py
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

results = []
def report(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail[:300]}" if detail else ""))
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
        for path in ("/api/auth/verify-2fa", "/api/auth/login/verify-otp",
                     "/api/auth/login/2fa", "/api/auth/2fa/verify"):
            payload = {"email": ADMIN_EMAIL, "code": code, "otp": code,
                       "turnstile_token": TURNSTILE}
            if "challenge_id" in body:
                payload["challenge_id"] = body["challenge_id"]
            r2 = s.post(f"{BASE_URL}{path}", json=payload, timeout=30)
            if r2.status_code == 200:
                break
    s.get(f"{BASE_URL}/api/auth/me", timeout=15)
    return s

def _login(email: str, pw: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": email, "password": pw,
                     "turnstile_token": TURNSTILE}, timeout=30)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text[:200]}"
    s.get(f"{BASE_URL}/api/auth/me", timeout=15)
    return s

def _csrf(s: requests.Session) -> dict:
    return {"X-CSRF-Token": s.cookies.get("csrf_token") or "",
            "Content-Type": "application/json"}

async def _get_uid(email: str) -> str:
    c = await asyncpg.connect(DATABASE_URL)
    try:
        r = await c.fetchrow("SELECT user_id FROM users WHERE email=$1", email)
        return r["user_id"] if r else ""
    finally:
        await c.close()

async def _wipe_open_challenges(challenger_uid: str, champion_uid: str):
    c = await asyncpg.connect(DATABASE_URL)
    try:
        await c.execute(
            """DELETE FROM quiz_champion_challenges
                WHERE challenger_user_id = $1 AND champion_user_id = $2""",
            challenger_uid, champion_uid)
    finally:
        await c.close()

async def _get_options_order(run_id: int):
    c = await asyncpg.connect(DATABASE_URL)
    try:
        r = await c.fetchrow("SELECT options_order FROM quiz_user_runs WHERE id=$1", run_id)
        if not r:
            return None
        v = r["options_order"]
        if isinstance(v, str):
            try: return json.loads(v)
            except Exception: return None
        return v
    finally:
        await c.close()

async def _get_correct_indices(session_id: int):
    """Return ordered list of correct_index per question_id in session."""
    c = await asyncpg.connect(DATABASE_URL)
    try:
        s = await c.fetchrow("SELECT question_ids FROM quiz_sessions WHERE id=$1", session_id)
        qs = await c.fetch(
            "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
            list(s["question_ids"]))
        m = {int(r["id"]): int(r["correct_index"]) for r in qs}
        return [m[int(qid)] for qid in s["question_ids"]]
    finally:
        await c.close()

def _build_correct_answers(perms, originals):
    """Given options_order (list of perms) and original correct indices,
    return the answer index in the SHUFFLED order for each question."""
    answers = []
    for i, oc in enumerate(originals):
        perm = perms[i] if i < len(perms) else [0,1,2,3]
        # perm[given_shuffled_idx] = original_idx → invert
        try:
            answers.append(perm.index(oc))
        except ValueError:
            answers.append(0)
    return answers

def _seed_bob_as_cm_champion(admin: requests.Session, bob_uid: str):
    r = admin.post(f"{BASE_URL}/api/quiz/champion/admin/CM/set",
                   json={"user_id": bob_uid}, headers=_csrf(admin), timeout=30)
    assert r.status_code == 200, f"seed champion failed: {r.status_code} {r.text[:200]}"

def _summary():
    n_pass = sum(1 for r in results if r["ok"])
    n_fail = len(results) - n_pass
    print(f"\n=== iter123 SUMMARY: {n_pass}/{len(results)} passed, {n_fail} failed ===")
    if n_fail:
        print("\nFailures:")
        for r in results:
            if not r["ok"]:
                print(f"  • {r['name']} — {r['detail'][:200]}")
    # JUnit XML
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    with open("/app/test_reports/pytest/iter123_results.xml", "w") as f:
        f.write('<?xml version="1.0"?>\n')
        f.write(f'<testsuite name="iter123_quiz_champion" tests="{len(results)}" failures="{n_fail}">\n')
        for r in results:
            f.write(f'  <testcase name="{r["name"]}">')
            if not r["ok"]:
                f.write(f'<failure>{r["detail"][:500]}</failure>')
            f.write('</testcase>\n')
        f.write('</testsuite>\n')

def run():
    print(f"\n=== iter123 Quiz Champion (Phase 3.A) — BASE={BASE_URL} ===\n")
    print("[setup] Logging in…")
    try:
        admin = _login_admin()
        report("admin login (2FA)", True)
    except Exception as e:
        report("admin login (2FA)", False, str(e)[:300]); _summary(); return
    bob = _login(BOB_EMAIL, BOB_PASSWORD)
    alice = _login(ALICE_EMAIL, ALICE_PASSWORD)
    bob_uid = asyncio.new_event_loop().run_until_complete(_get_uid(BOB_EMAIL))
    alice_uid = asyncio.new_event_loop().run_until_complete(_get_uid(ALICE_EMAIL))

    # Re-seed Bob as CM champion (cleans demoted state)
    print("\n[setup] Re-seeding Bob as CM champion (admin_set)…")
    try:
        _seed_bob_as_cm_champion(admin, bob_uid); report("admin/CM/set seeds Bob", True)
    except Exception as e:
        report("admin/CM/set seeds Bob", False, str(e)[:300]); _summary(); return

    # Wipe any old open challenges between Alice→Bob and Bob→Alice (regression-safe)
    asyncio.new_event_loop().run_until_complete(_wipe_open_challenges(alice_uid, bob_uid))
    asyncio.new_event_loop().run_until_complete(_wipe_open_challenges(bob_uid, alice_uid))

    # ── 1. GET /api/quiz/champion/{country_code} ──
    print("\n[1] GET champion read")
    r = requests.get(f"{BASE_URL}/api/quiz/champion/CM", timeout=15)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    has_keys = ok and all(k in body for k in ("user_id", "user", "refusal_count_consecutive",
                                              "refusal_count_30d", "source"))
    has_user_keys = ok and all(k in body.get("user", {}) for k in ("first_name","last_name","username","avatar","is_pro"))
    report("GET /champion/CM returns 200 with full payload", ok and has_keys and has_user_keys,
           f"status={r.status_code} body={r.text[:250]}")
    report("source = 'admin_set' after admin/set", ok and body.get("source") == "admin_set",
           f"source={body.get('source')}")

    # 404 for unknown country (use ZZ, no champion)
    r = requests.get(f"{BASE_URL}/api/quiz/champion/ZZ", timeout=15)
    report("GET /champion/ZZ returns 404 when no champion", r.status_code == 404,
           f"status={r.status_code}")

    # 400 validation
    for code in ("X", "XYZ", "12"):
        r = requests.get(f"{BASE_URL}/api/quiz/champion/{code}", timeout=15)
        report(f"GET /champion/{code!r} returns 400", r.status_code == 400,
               f"status={r.status_code} body={r.text[:120]}")

    # ── 2. Admin set/demote/promote-all ──
    print("\n[2] Admin endpoints")
    # Non-admin admin/set → 403
    r = bob.post(f"{BASE_URL}/api/quiz/champion/admin/CM/set",
                 json={"user_id": bob_uid}, headers=_csrf(bob), timeout=15)
    report("admin/set as non-admin → 403", r.status_code == 403, f"status={r.status_code}")

    # Admin promote-all (idempotent)
    r1 = admin.post(f"{BASE_URL}/api/quiz/champion/admin/promote-all",
                    headers=_csrf(admin), timeout=30)
    r2 = admin.post(f"{BASE_URL}/api/quiz/champion/admin/promote-all",
                    headers=_csrf(admin), timeout=30)
    ok = r1.status_code == 200 and r2.status_code == 200
    body1 = r1.json() if r1.ok else {}
    has_struct = all(k in body1 for k in ("countries_evaluated", "promoted", "unchanged", "demoted"))
    report("promote-all returns full struct", ok and has_struct,
           f"status1={r1.status_code} status2={r2.status_code} keys={list(body1.keys())}")
    report("promote-all idempotent (2nd call works)", ok)

    # Re-seed Bob (promote-all may have replaced champion based on real points data)
    _seed_bob_as_cm_champion(admin, bob_uid)

    # Admin list
    r = admin.get(f"{BASE_URL}/api/quiz/champion/admin/list", timeout=15)
    ok = r.status_code == 200 and "items" in r.json()
    has_30d = ok and any("refusal_count_30d" in it for it in r.json().get("items", []))
    report("GET /admin/list returns items with refusal_count_30d", ok and has_30d,
           f"status={r.status_code} n={len(r.json().get('items',[])) if r.ok else 0}")

    # ── 3. Challenge create errors ──
    print("\n[3] Challenge: create validation errors")
    # Champion challenges himself
    r = bob.post(f"{BASE_URL}/api/quiz/champion/challenge",
                 json={"country_code": "CM", "mode": "free"},
                 headers=_csrf(bob), timeout=15)
    report("self-challenge → 400", r.status_code == 400 and "défier" in r.text.lower(),
           f"status={r.status_code} body={r.text[:200]}")

    # paid mode → 501
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge",
                   json={"country_code": "CM", "mode": "paid"},
                   headers=_csrf(alice), timeout=15)
    report("mode=paid → 501", r.status_code == 501,
           f"status={r.status_code} body={r.text[:200]}")

    # No champion in country → 404
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge",
                   json={"country_code": "ZZ", "mode": "free"},
                   headers=_csrf(alice), timeout=15)
    report("no champion in country → 404", r.status_code == 404,
           f"status={r.status_code} body={r.text[:200]}")

    # ── 4. Create challenge as Alice ──
    print("\n[4] Alice creates challenge against Bob")
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge",
                   json={"country_code": "CM", "mode": "free"},
                   headers=_csrf(alice), timeout=15)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    cid = body.get("challenge_id")
    report("create challenge → 200 + challenge_id", ok and bool(cid) and body.get("status") == "pending"
           and body.get("country_code") == "CM" and "expires_at" in body,
           f"status={r.status_code} body={r.text[:300]}")

    # Duplicate open challenge → 409
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge",
                   json={"country_code": "CM", "mode": "free"},
                   headers=_csrf(alice), timeout=15)
    report("duplicate open challenge → 409", r.status_code == 409,
           f"status={r.status_code} body={r.text[:200]}")

    if not cid:
        _summary(); return

    # ── 5. Accept/refuse access control ──
    print("\n[5] Accept/refuse access control")
    # Non-champion accept → 403
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/accept",
                   headers=_csrf(alice), timeout=15)
    report("non-champion accept → 403", r.status_code == 403,
           f"status={r.status_code}")

    # Champion accepts
    r = bob.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/accept",
                 headers=_csrf(bob), timeout=15)
    ok = r.status_code == 200 and r.json().get("status") == "accepted"
    report("champion accept → 200, status=accepted", ok,
           f"status={r.status_code} body={r.text[:200]}")

    # Already-accepted → 409
    r = bob.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/accept",
                 headers=_csrf(bob), timeout=15)
    report("re-accept → 409", r.status_code == 409, f"status={r.status_code}")

    # ── 6. Play / shuffle independence ──
    print("\n[6] Play: shared session, independent shuffles")
    r_alice = alice.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/play",
                         headers=_csrf(alice), timeout=20)
    ok_a = r_alice.status_code == 200
    p_a = r_alice.json() if ok_a else {}
    keys_required = ("run_id","session_id","time_limit_seconds","timer_mode",
                     "timer_per_question_seconds","auto_advance_enabled","questions")
    has_all = ok_a and all(k in p_a for k in keys_required)
    has_5q = ok_a and len(p_a.get("questions",[])) == 5
    no_correct_leak = ok_a and not any("correct_index" in q for q in p_a.get("questions", []))
    q_struct_ok = ok_a and all(set(q.keys()) >= {"id","text","options","category"}
                               and len(q["options"]) == 4
                               for q in p_a.get("questions", []))
    report("Alice /play 200 with full payload", ok_a and has_all and has_5q and q_struct_ok,
           f"status={r_alice.status_code} body={r_alice.text[:200]}")
    report("/play does NOT leak correct_index", ok_a and no_correct_leak)
    report("timer_per_question_seconds=15", ok_a and p_a.get("timer_per_question_seconds") == 15,
           f"got {p_a.get('timer_per_question_seconds')}")

    # Alice plays again → 409 (already played)
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/play",
                   headers=_csrf(alice), timeout=15)
    report("Alice double /play → 409", r.status_code == 409, f"status={r.status_code}")

    # Bob plays
    r_bob = bob.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/play",
                     headers=_csrf(bob), timeout=20)
    ok_b = r_bob.status_code == 200
    p_b = r_bob.json() if ok_b else {}
    report("Bob /play 200", ok_b, f"status={r_bob.status_code} body={r_bob.text[:200]}")

    # Verify shuffles differ via DB options_order
    perms_a = asyncio.new_event_loop().run_until_complete(_get_options_order(p_a["run_id"])) if ok_a else None
    perms_b = asyncio.new_event_loop().run_until_complete(_get_options_order(p_b["run_id"])) if ok_b else None
    differs = bool(perms_a and perms_b and perms_a != perms_b)
    report("options_order shuffled INDEPENDENTLY per side (DB-verified)", differs,
           f"alice_perms={perms_a} bob_perms={perms_b}")

    # ── 7. Submit non-participant 403 ──
    print("\n[7] Submit access + scoring")
    r = admin.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/submit",
                   json={"answers":[0,0,0,0,0]}, headers=_csrf(admin), timeout=15)
    report("non-participant submit → 403", r.status_code == 403,
           f"status={r.status_code} body={r.text[:200]}")

    # Build correct answers using DB
    sid = int(p_a["session_id"])
    originals = asyncio.new_event_loop().run_until_complete(_get_correct_indices(sid))
    alice_answers = _build_correct_answers(perms_a, originals)  # Alice all correct → 5
    bob_answers = _build_correct_answers(perms_b, originals)
    bob_answers[0] = (bob_answers[0] + 1) % 4  # Bob misses 1 → 4

    # Alice submit
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/submit",
                   json={"answers": alice_answers}, headers=_csrf(alice), timeout=15)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    report("Alice submit 5/5 → 200", ok and body.get("your_correct") == 5,
           f"status={r.status_code} body={r.text[:300]}")

    # Alice double submit → 409
    r = alice.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/submit",
                   json={"answers": alice_answers}, headers=_csrf(alice), timeout=15)
    report("Alice double submit → 409", r.status_code == 409,
           f"status={r.status_code}")

    # Bob submit (4/5) → completes challenge
    r = bob.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/submit",
                 json={"answers": bob_answers}, headers=_csrf(bob), timeout=15)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    res = body.get("resolved") or {}
    report("Bob submit completes challenge", ok and bool(res),
           f"status={r.status_code} body={r.text[:300]}")
    report("winner = Alice (5 > 4)", ok and res.get("winner_user_id") == alice_uid,
           f"resolved={res}")
    report("scores recorded correctly", ok and res.get("challenger_score") == 5
           and res.get("champion_score") == 4, f"resolved={res}")

    # ── 8. GET /challenges/me + /challenges/{cid} ──
    print("\n[8] List + detail")
    r = alice.get(f"{BASE_URL}/api/quiz/champion/challenges/me", timeout=15)
    ok = r.status_code == 200 and any(it["challenge_id"] == cid and it["role"] == "challenger"
                                      for it in r.json().get("items", []))
    report("/challenges/me Alice sees challenge as 'challenger'", ok,
           f"status={r.status_code} items={len(r.json().get('items',[])) if r.ok else 0}")
    r = bob.get(f"{BASE_URL}/api/quiz/champion/challenges/me?status=completed", timeout=15)
    ok = r.status_code == 200 and any(it["challenge_id"] == cid and it["role"] == "champion"
                                      for it in r.json().get("items", []))
    report("/challenges/me Bob sees challenge as 'champion' (filter status=completed)", ok)

    r = alice.get(f"{BASE_URL}/api/quiz/champion/challenges/{cid}", timeout=15)
    ok = r.status_code == 200 and r.json().get("status") == "completed" and r.json().get("can_play") is False
    report("/challenges/{cid} for participant ok, can_play=false (completed)", ok,
           f"body={r.text[:200]}")

    # Non-participant non-admin → 403 (we don't have a 3rd test user; skip if unable)
    # Instead: just check admin can view → 200
    r = admin.get(f"{BASE_URL}/api/quiz/champion/challenges/{cid}", timeout=15)
    report("/challenges/{cid} admin can view (200)", r.status_code == 200, f"status={r.status_code}")

    # ── 9. Admin challenges list filters ──
    print("\n[9] Admin /admin/challenges filters")
    r = admin.get(f"{BASE_URL}/api/quiz/champion/admin/challenges?status=completed&country_code=CM&limit=10", timeout=15)
    ok = r.status_code == 200 and isinstance(r.json().get("items"), list)
    report("admin/challenges with filters → 200", ok, f"status={r.status_code}")

    # ── 10. Refusal flow / demotion ──
    print("\n[10] Refusal flow → demotion at 5th refuse")
    # Re-seed Bob (resets counters)
    _seed_bob_as_cm_champion(admin, bob_uid)
    asyncio.new_event_loop().run_until_complete(_wipe_open_challenges(alice_uid, bob_uid))

    refuse_outcomes = []
    for i in range(1, 6):
        rc = alice.post(f"{BASE_URL}/api/quiz/champion/challenge",
                        json={"country_code": "CM", "mode": "free"},
                        headers=_csrf(alice), timeout=15)
        if rc.status_code != 200:
            report(f"refuse-cycle #{i} create challenge", False,
                   f"status={rc.status_code} body={rc.text[:200]}")
            break
        cid_i = rc.json()["challenge_id"]
        rr = bob.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid_i}/refuse",
                      headers=_csrf(bob), timeout=15)
        ok = rr.status_code == 200
        body = rr.json() if ok else {}
        refuse_outcomes.append(body)
        report(f"refuse #{i} → 200 with outcome", ok and "consecutive" in body
               and "rolling_30d" in body and "demoted" in body,
               f"status={rr.status_code} body={rr.text[:200]}")
        if i < 5:
            ok2 = body.get("consecutive") == i and body.get("demoted") is False
            report(f"refuse #{i}: consecutive={i}, demoted=false", ok2,
                   f"got consecutive={body.get('consecutive')} demoted={body.get('demoted')}")
        else:
            ok2 = body.get("consecutive") == 5 and body.get("demoted") is True \
                  and body.get("reason") in ("consecutive_refusals", "rolling_30d_refusals")
            report("refuse #5: demoted=true, reason set", ok2, f"body={body}")

    # GET /champion/CM → 404
    r = requests.get(f"{BASE_URL}/api/quiz/champion/CM", timeout=15)
    report("After 5 refuses, GET /champion/CM → 404", r.status_code == 404,
           f"status={r.status_code} body={r.text[:200]}")

    # admin/list with include_demoted=true shows Bob
    r = admin.get(f"{BASE_URL}/api/quiz/champion/admin/list?include_demoted=true", timeout=15)
    ok = r.status_code == 200 and any(it["country_code"] == "CM" and it["user_id"] == bob_uid
                                      and it["demoted_at"] is not None
                                      for it in r.json().get("items", []))
    report("admin/list?include_demoted=true shows demoted Bob/CM", ok,
           f"status={r.status_code}")

    # ── 11. admin/{cc}/demote endpoint ──
    print("\n[11] admin/{cc}/demote endpoint")
    _seed_bob_as_cm_champion(admin, bob_uid)
    r = admin.post(f"{BASE_URL}/api/quiz/champion/admin/CM/demote",
                   json={"reason": "manual_test"}, headers=_csrf(admin), timeout=15)
    ok = r.status_code == 200
    report("admin/CM/demote → 200", ok, f"status={r.status_code} body={r.text[:200]}")
    r = requests.get(f"{BASE_URL}/api/quiz/champion/CM", timeout=15)
    report("After admin demote, GET /champion/CM → 404", r.status_code == 404,
           f"status={r.status_code}")

    # ── 12. Regression: existing /api/quiz/start, /answer, /submit work ──
    print("\n[12] Regression: vanilla /api/quiz still works for Bob")
    r = bob.post(f"{BASE_URL}/api/quiz/start", json={}, headers=_csrf(bob), timeout=20)
    ok = r.status_code == 200 and len(r.json().get("questions", [])) == 5
    report("/api/quiz/start works for Bob", ok, f"status={r.status_code} body={r.text[:200]}")

    _summary()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        traceback.print_exc()
        report("UNCAUGHT EXCEPTION", False, str(e)[:300])
        _summary()
        sys.exit(1)
    n_fail = sum(1 for r in results if not r["ok"])
    sys.exit(0 if n_fail == 0 else 1)
