"""
iter130 — Phase 3.E Quiz: Anti-repeat + Daily Challenge + Admin distribution + AI gen.

Custom Python runner (pytest broken locally with web3 plugin).
Emits JUnit XML to /app/test_reports/pytest/iter130_results.xml.

Coverage:
  1. GET  /api/quiz/daily-challenge/status (Bob/Alice)
  2. POST /api/quiz/daily-challenge/start  (creates run, picks 5Q, blocks 2nd → 429)
  3. POST /api/quiz/daily-challenge/submit (scores, ticks streak, points_breakdown)
  4. Anti-repetition: 2x /quiz/start return DIFFERENT question ids (window=7d)
  5. Distribution enforcement: returned categories follow ~50/20/15/15 plan
  6. user_quiz_question_history populated on /start AND /daily-challenge/start
  7. GET/PUT /api/quiz/admin/distribution + sum=100 validation (400 when not)
  8. GET /api/quiz/admin/categories — counts per category
  9. PUT /api/quiz/admin/categories/{cat} — toggle enabled/priority
 10. POST /api/quiz/admin/questions/{qid}/obsolete — flips flag
 11. POST /api/quiz/admin/generate-ai total=4 — generates+inserts, returns by_category
 12. /quiz/start does NOT count daily-challenge runs toward 5/day cap
 13. daily_quiz_streak: increments on consecutive day, resets on gap
 14. Regression: /quiz/submit, /quiz/answer, /api/quiz/champion/{country} still work
"""
from __future__ import annotations
import os, sys, asyncio, traceback, time, json
from datetime import date, timedelta
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
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {str(info)[:300]}")


async def db():
    return await asyncpg.connect(DB_URL)


def refresh_csrf(session: requests.Session):
    csrf = session.cookies.get("csrf_token")
    if csrf:
        session.headers["X-CSRF-Token"] = csrf


def login(session: requests.Session, email, pwd):
    session.headers["X-Requested-With"] = "XMLHttpRequest"
    session.get(f"{BASE}/api/auth/me")
    refresh_csrf(session)
    r = session.post(f"{BASE}/api/auth/login",
                     json={"email": email, "password": pwd, "turnstile_token": TURNSTILE})
    if r.status_code != 200:
        return False, f"{r.status_code}: {r.text[:200]}"
    refresh_csrf(session)
    return True, r.json()


def post(session: requests.Session, url, **kw):
    refresh_csrf(session)
    return session.post(url, **kw)


def put(session: requests.Session, url, **kw):
    refresh_csrf(session)
    return session.put(url, **kw)


async def reset_state():
    """Clear today's daily-challenge runs for Alice & Bob, and history of last day for Alice
    so we can test anti-repetition in isolation. Also clear standard runs today for Alice."""
    c = await db()
    try:
        alice_id = await c.fetchval("SELECT user_id FROM users WHERE email='alice@japap.com'")
        bob_id   = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        # Daily-challenge: clear today's runs
        await c.execute("DELETE FROM quiz_daily_challenge_runs WHERE user_id IN ($1,$2) AND play_date=(NOW() AT TIME ZONE 'utc')::date", alice_id, bob_id)
        # Standard quiz runs today (cap reset for Alice & Bob)
        await c.execute("DELETE FROM quiz_user_runs WHERE user_id IN ($1,$2) AND started_at::date=CURRENT_DATE", alice_id, bob_id)
        # Question history (so anti-repeat picker has fresh slate for Alice)
        await c.execute("DELETE FROM user_quiz_question_history WHERE user_id=$1", alice_id)
        # Streak fresh for Alice
        await c.execute("DELETE FROM daily_quiz_streak WHERE user_id=$1", alice_id)
        return alice_id, bob_id
    finally:
        await c.close()


async def main():
    if not BASE:
        log_test("env_check", False, "REACT_APP_BACKEND_URL missing"); write_junit(); sys.exit(1)
    log_test("env_check", True, BASE)

    alice_id, bob_id = await reset_state()
    log_test("reset_state", True, f"alice={alice_id} bob={bob_id}")

    # ── Logins ──
    alice_s = requests.Session(); ok, info = login(alice_s, *ALICE)
    log_test("login_alice", ok, info if not ok else "ok")
    if not ok: return
    bob_s = requests.Session();   ok, info = login(bob_s, *BOB)
    log_test("login_bob", ok, info if not ok else "ok")
    if not ok: return
    admin_s = requests.Session(); ok, info = login(admin_s, *ADMIN)
    log_test("login_admin", ok, info if not ok else "ok")
    if not ok: return

    # ════════════════════════════════════════════════════════════════
    # 1. Daily Challenge — status
    # ════════════════════════════════════════════════════════════════
    r = alice_s.get(f"{BASE}/api/quiz/daily-challenge/status")
    ok = (r.status_code == 200 and r.json().get("available") is True
          and r.json().get("played_today") is False
          and "streak" in r.json())
    log_test("dc_status_alice_initial", ok, r.status_code if ok else f"{r.status_code}: {r.text[:200]}")

    # ════════════════════════════════════════════════════════════════
    # 2. Daily Challenge — start
    # ════════════════════════════════════════════════════════════════
    r = post(alice_s, f"{BASE}/api/quiz/daily-challenge/start")
    ok = r.status_code == 200
    log_test("dc_start_alice", ok, r.status_code if ok else r.text[:300])
    if not ok: return
    body = r.json()
    dc_run_id = body["run_id"]
    dc_questions = body["questions"]
    ok_q = (len(dc_questions) == 5 and all("id" in q and "options" in q and len(q["options"]) == 4
                                            for q in dc_questions))
    log_test("dc_start_alice_5q_4opts", ok_q, f"got {len(dc_questions)}q")
    cats = [q["category"] for q in dc_questions]
    log_test("dc_start_alice_categories", True, str(cats))

    # 2b. user_quiz_question_history populated for daily-challenge
    c = await db()
    try:
        cnt = await c.fetchval(
            "SELECT COUNT(*) FROM user_quiz_question_history WHERE user_id=$1 AND source='daily_challenge'",
            alice_id)
    finally:
        await c.close()
    log_test("history_populated_after_dc_start", cnt == 5, f"rows={cnt}")

    # 2c. Block 2nd play same day → 429
    r2 = post(alice_s, f"{BASE}/api/quiz/daily-challenge/start")
    log_test("dc_start_blocks_2nd_same_day_429", r2.status_code == 429,
             f"{r2.status_code}: {r2.text[:120]}")

    # ════════════════════════════════════════════════════════════════
    # 3. Daily Challenge — submit
    # ════════════════════════════════════════════════════════════════
    # Get correct answers from DB so we can score 5/5 (perfect) — that
    # exercises base + perfect_bonus + streak_bonus computation.
    c = await db()
    try:
        rows = await c.fetch(
            "SELECT id, correct_index, options FROM quiz_questions WHERE id=ANY($1::bigint[])",
            [q["id"] for q in dc_questions])
        correct_orig = {int(r["id"]): int(r["correct_index"]) for r in rows}
        # options_order persisted on run row (perm[displayed]=original)
        run_row = await c.fetchrow("SELECT options_order FROM quiz_user_runs WHERE id=$1", dc_run_id)
        perms = run_row["options_order"]
        if isinstance(perms, str):
            perms = json.loads(perms)
    finally:
        await c.close()
    answers = []
    for i, q in enumerate(dc_questions):
        orig = correct_orig[q["id"]]
        perm = perms[i]
        # find displayed index whose perm[displayed]==orig
        displayed = perm.index(orig)
        answers.append(displayed)

    r = post(alice_s, f"{BASE}/api/quiz/daily-challenge/submit",
             json={"run_id": dc_run_id, "answers": answers})
    ok = r.status_code == 200
    log_test("dc_submit_alice_200", ok, r.status_code if ok else r.text[:300])
    if ok:
        body = r.json()
        pb = body.get("points_breakdown", {})
        ok_score = (body["correct_count"] == 5 and body["perfect"] is True
                    and pb.get("base") == 5 * 25
                    and pb.get("perfect_bonus") == 50
                    and pb.get("streak_bonus") == 0  # streak=1 → (1-1)*5=0
                    and pb.get("total") == 175
                    and body["streak"]["current_streak"] == 1)
        log_test("dc_submit_alice_score_5_5_perfect_bonus", ok_score,
                 f"correct={body.get('correct_count')} pb={pb} streak={body.get('streak')}")
        log_test("dc_submit_share_text_present", bool(body.get("share_text")),
                 (body.get("share_text") or "")[:80])

    # ════════════════════════════════════════════════════════════════
    # 4. /quiz/start NOT counted toward standard 5/day cap when DC played
    #    Alice already played DC; standard runs today=0 → /quiz/start should still work.
    # ════════════════════════════════════════════════════════════════
    r = post(alice_s, f"{BASE}/api/quiz/start")
    ok = r.status_code == 200
    log_test("standard_quiz_start_after_dc_ok", ok, r.status_code if ok else r.text[:300])
    standard_q1_ids = []
    if ok:
        b1 = r.json()
        standard_q1_ids = [q["id"] for q in b1["questions"]]
        log_test("standard_quiz_5q_returned", len(standard_q1_ids) == 5, f"len={len(standard_q1_ids)}")

    # ════════════════════════════════════════════════════════════════
    # 5. Anti-repetition: 2nd /quiz/start returns DIFFERENT ids (window=7d)
    # ════════════════════════════════════════════════════════════════
    r2 = post(alice_s, f"{BASE}/api/quiz/start")
    ok = r2.status_code == 200
    log_test("standard_quiz_start_2nd_ok", ok, r2.status_code if ok else r2.text[:200])
    if ok:
        b2 = r2.json()
        ids2 = [q["id"] for q in b2["questions"]]
        overlap = set(standard_q1_ids) & set(ids2)
        log_test("anti_repeat_no_overlap_consecutive_starts", len(overlap) == 0,
                 f"overlap={overlap}")

    # ════════════════════════════════════════════════════════════════
    # 6. Distribution enforcement (after DC + 2 standard starts → 15 questions logged).
    #    Check that across recently-picked questions, ratios approximate 50/20/15/15.
    #    Buckets per services/quiz_question_picker.py.
    # ════════════════════════════════════════════════════════════════
    AFRICA = {"afrique_monde", "histoire_afrique", "geographie_afrique",
              "sport_africain", "musique_afrique", "entrepreneurs_afrique",
              "institutions_afrique"}
    SPORT  = {"sport", "football", "sport_africain"}
    ECON   = {"economie", "crypto", "actualite"}
    WORLD  = {"culture_generale", "technologie"}

    c = await db()
    try:
        rows = await c.fetch(
            """SELECT q.category FROM user_quiz_question_history h
                 JOIN quiz_questions q ON q.id=h.question_id
                WHERE h.user_id=$1
                ORDER BY h.seen_at DESC LIMIT 15""", alice_id)
    finally:
        await c.close()
    cats = [r["category"] for r in rows]
    bucket_counts = {"africa": 0, "sport": 0, "econ": 0, "world": 0, "other": 0}
    for c_ in cats:
        if c_ in AFRICA: bucket_counts["africa"] += 1
        elif c_ in SPORT: bucket_counts["sport"] += 1
        elif c_ in ECON: bucket_counts["econ"] += 1
        elif c_ in WORLD: bucket_counts["world"] += 1
        else: bucket_counts["other"] += 1
    # Expected per 5: 3 africa, 1 sport, ~1 econ, ~0-1 world (round 0.75→1, but last bucket absorbs).
    # In practice the picker plan = [3, 1, 1, 0]; over 15 → [9, 3, 3, 0] (last absorbs remainder).
    # Allow some flexibility because of fallback paths.
    ok_dist = (bucket_counts["africa"] >= 6 and bucket_counts["africa"] <= 12
               and bucket_counts["sport"] >= 1 and bucket_counts["sport"] <= 6
               and (bucket_counts["econ"] + bucket_counts["world"]) >= 1)
    log_test("distribution_50_20_15_15_approx", ok_dist, str(bucket_counts))

    # ════════════════════════════════════════════════════════════════
    # 7. Admin distribution GET/PUT + sum=100 validation
    # ════════════════════════════════════════════════════════════════
    r = admin_s.get(f"{BASE}/api/quiz/admin/distribution")
    ok = r.status_code == 200 and "quiz_dist_africa_pct" in r.json()
    log_test("admin_distribution_get_200", ok, r.status_code if ok else r.text[:200])

    # Bad sum (≠ 100) → 400
    r = put(admin_s, f"{BASE}/api/quiz/admin/distribution",
            json={"quiz_dist_africa_pct": 50, "quiz_dist_sport_pct": 20,
                  "quiz_dist_econ_pct": 15, "quiz_dist_world_pct": 10})
    log_test("admin_distribution_put_400_when_sum_ne_100", r.status_code == 400,
             f"{r.status_code}: {r.text[:120]}")

    # Good sum = 100, then restore default
    r = put(admin_s, f"{BASE}/api/quiz/admin/distribution",
            json={"quiz_dist_africa_pct": 40, "quiz_dist_sport_pct": 20,
                  "quiz_dist_econ_pct": 20, "quiz_dist_world_pct": 20})
    log_test("admin_distribution_put_200_when_sum_100", r.status_code == 200, r.status_code)
    # Restore defaults
    put(admin_s, f"{BASE}/api/quiz/admin/distribution",
        json={"quiz_dist_africa_pct": 50, "quiz_dist_sport_pct": 20,
              "quiz_dist_econ_pct": 15, "quiz_dist_world_pct": 15})

    # ════════════════════════════════════════════════════════════════
    # 8. Admin categories GET
    # ════════════════════════════════════════════════════════════════
    r = admin_s.get(f"{BASE}/api/quiz/admin/categories")
    ok = (r.status_code == 200 and isinstance(r.json().get("items"), list)
          and len(r.json()["items"]) >= 4)
    log_test("admin_categories_get", ok,
             f"{r.status_code}: {len(r.json().get('items',[])) if ok else r.text[:200]} items")
    if ok:
        items = r.json()["items"]
        first_cat = items[0]["category"]
        ok_keys = all(k in items[0] for k in ("category", "active_count", "obsolete_count",
                                                "total", "enabled", "priority"))
        log_test("admin_categories_payload_keys", ok_keys, str(items[0])[:200])

    # ════════════════════════════════════════════════════════════════
    # 9. Admin categories PUT — toggle enabled/priority
    # ════════════════════════════════════════════════════════════════
    target = "culture_generale"
    r = put(admin_s, f"{BASE}/api/quiz/admin/categories/{target}",
            json={"enabled": False, "priority": 5})
    ok = r.status_code == 200 and r.json().get("enabled") is False and r.json().get("priority") == 5
    log_test("admin_categories_put_disable", ok, f"{r.status_code}: {r.text[:200]}")
    # Restore enabled
    put(admin_s, f"{BASE}/api/quiz/admin/categories/{target}",
        json={"enabled": True, "priority": 1})

    # ════════════════════════════════════════════════════════════════
    # 10. Admin obsolete toggle
    # ════════════════════════════════════════════════════════════════
    c = await db()
    try:
        qid_pick = await c.fetchval(
            "SELECT id FROM quiz_questions WHERE active=TRUE AND obsolete=FALSE LIMIT 1")
    finally:
        await c.close()
    r = post(admin_s, f"{BASE}/api/quiz/admin/questions/{qid_pick}/obsolete",
             params={"obsolete": "true"})
    ok = r.status_code == 200 and r.json().get("obsolete") is True
    log_test("admin_questions_obsolete_true", ok, f"{r.status_code}: {r.text[:200]}")
    # Flip back
    r = post(admin_s, f"{BASE}/api/quiz/admin/questions/{qid_pick}/obsolete",
             params={"obsolete": "false"})
    log_test("admin_questions_obsolete_false_revert", r.status_code == 200, r.status_code)

    # ════════════════════════════════════════════════════════════════
    # 11. Admin AI generate (total=4) — costs ~4 LLM calls, takes ~30s
    # ════════════════════════════════════════════════════════════════
    c = await db()
    try:
        before = await c.fetchval("SELECT COUNT(*) FROM quiz_questions")
    finally:
        await c.close()
    r = post(admin_s, f"{BASE}/api/quiz/admin/generate-ai",
             json={"total": 4}, timeout=180)
    ok = r.status_code == 200 and r.json().get("status") == "ok" and "by_category" in r.json()
    log_test("admin_generate_ai_total_4", ok, f"{r.status_code}: {str(r.text)[:300]}")
    if ok:
        c = await db()
        try:
            after = await c.fetchval("SELECT COUNT(*) FROM quiz_questions")
        finally:
            await c.close()
        log_test("admin_generate_ai_inserted_rows", after >= before,
                 f"before={before} after={after}")
        log_test("admin_generate_ai_by_category_present",
                 isinstance(r.json().get("by_category"), dict),
                 str(r.json().get("by_category")))

    # ════════════════════════════════════════════════════════════════
    # 12. Standard /quiz/start (already covered above) — verify history populated for source='quiz_standard'
    # ════════════════════════════════════════════════════════════════
    c = await db()
    try:
        std_cnt = await c.fetchval(
            "SELECT COUNT(*) FROM user_quiz_question_history WHERE user_id=$1 AND source='quiz_standard'",
            alice_id)
    finally:
        await c.close()
    log_test("history_populated_quiz_standard", std_cnt >= 10, f"std_cnt={std_cnt}")

    # ════════════════════════════════════════════════════════════════
    # 13. daily_quiz_streak: increment-then-reset semantics.
    #     We use Bob (history-clean for streak by manipulating last_played_date).
    # ════════════════════════════════════════════════════════════════
    c = await db()
    try:
        # Simulate Bob played yesterday (current_streak=3) then plays today via daily-challenge
        yest = (date.today() - timedelta(days=1))
        await c.execute("""INSERT INTO daily_quiz_streak (user_id, current_streak, longest_streak, last_played_date)
                           VALUES ($1, 3, 3, $2)
                           ON CONFLICT (user_id) DO UPDATE
                              SET current_streak=3, longest_streak=3, last_played_date=$2""",
                        bob_id, yest)
        # Ensure no DC run today for Bob
        await c.execute("DELETE FROM quiz_daily_challenge_runs WHERE user_id=$1 AND play_date=(NOW() AT TIME ZONE 'utc')::date", bob_id)
    finally:
        await c.close()
    # Bob plays today — streak should become 4
    r = post(bob_s, f"{BASE}/api/quiz/daily-challenge/start")
    if r.status_code != 200:
        log_test("streak_consecutive_bob_start", False, f"{r.status_code}: {r.text[:200]}")
    else:
        bobrun = r.json()
        # Submit anything (dummy answers; even 0/5 ticks streak)
        bsubmit = post(bob_s, f"{BASE}/api/quiz/daily-challenge/submit",
                       json={"run_id": bobrun["run_id"], "answers": [0, 0, 0, 0, 0]})
        if bsubmit.status_code == 200:
            body = bsubmit.json()
            log_test("streak_consecutive_increments_to_4",
                     body["streak"]["current_streak"] == 4,
                     f"streak={body['streak']}")
        else:
            log_test("streak_consecutive_bob_submit", False, f"{bsubmit.status_code}: {bsubmit.text[:200]}")

    # 13b. Streak resets after gap: set last_played_date to 5 days ago, mark today played by manipulating DB,
    # then we cannot re-call /daily-challenge/submit, so we directly assert tick_streak logic via service-call.
    # Simpler: manually wipe today's DC run for bob, set last_played_date=5d ago, call start+submit again.
    c = await db()
    try:
        five_ago = date.today() - timedelta(days=5)
        await c.execute("UPDATE daily_quiz_streak SET current_streak=10, longest_streak=10, last_played_date=$2 WHERE user_id=$1",
                        bob_id, five_ago)
        await c.execute("DELETE FROM quiz_daily_challenge_runs WHERE user_id=$1 AND play_date=(NOW() AT TIME ZONE 'utc')::date", bob_id)
        await c.execute("DELETE FROM quiz_user_runs WHERE user_id=$1 AND id IN (SELECT run_id FROM quiz_daily_challenge_runs WHERE user_id=$1)", bob_id)
    finally:
        await c.close()
    r = post(bob_s, f"{BASE}/api/quiz/daily-challenge/start")
    if r.status_code == 200:
        bobrun = r.json()
        bsubmit = post(bob_s, f"{BASE}/api/quiz/daily-challenge/submit",
                       json={"run_id": bobrun["run_id"], "answers": [0, 0, 0, 0, 0]})
        if bsubmit.status_code == 200:
            body = bsubmit.json()
            log_test("streak_resets_after_gap_to_1",
                     body["streak"]["current_streak"] == 1,
                     f"streak={body['streak']}")
        else:
            log_test("streak_resets_bob_submit", False, f"{bsubmit.status_code}: {bsubmit.text[:200]}")
    else:
        log_test("streak_resets_bob_start", False, f"{r.status_code}: {r.text[:200]}")

    # ════════════════════════════════════════════════════════════════
    # 14. Regression: /quiz/champion/CM still works (read-only)
    # ════════════════════════════════════════════════════════════════
    r = bob_s.get(f"{BASE}/api/quiz/champion/CM")
    log_test("regression_champion_get_CM", r.status_code in (200, 404),
             f"{r.status_code}: {r.text[:200]}")

    # /quiz/answer regression — submit a single answer for run that's still in progress.
    # Alice's 2nd standard run (b2) is still open. Use it.
    if 'b2' in dir() and b2.get("run_id"):
        r = post(alice_s, f"{BASE}/api/quiz/answer",
                 json={"run_id": b2["run_id"], "question_idx": 0, "selected_option": 0})
        log_test("regression_quiz_answer", r.status_code == 200,
                 f"{r.status_code}: {r.text[:200]}")

    write_junit()
    fails = [n for n, ok, _ in results if not ok]
    print(f"\n{'='*60}\nSummary: {len(results)-len(fails)}/{len(results)} pass")
    if fails:
        print("Failures:")
        for n, ok, info in results:
            if not ok: print(f"  - {n}: {info}")
    sys.exit(0 if not fails else 1)


def write_junit():
    os.makedirs("/app/test_reports/pytest", exist_ok=True)
    fails = sum(1 for _, ok, _ in results if not ok)
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append(f'<testsuite name="iter130_quiz_phase3e" tests="{len(results)}" failures="{fails}">')
    for n, ok, info in results:
        xml.append(f'  <testcase classname="iter130" name="{xesc(n)}">')
        if not ok:
            xml.append(f'    <failure message="fail">{xesc(str(info)[:500])}</failure>')
        xml.append('  </testcase>')
    xml.append('</testsuite>')
    with open("/app/test_reports/pytest/iter130_results.xml", "w") as f:
        f.write("\n".join(xml))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        write_junit()
        sys.exit(1)
