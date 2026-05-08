"""Tests for Iter85 — Anti-exploitation (quiz shuffle + tap suspicious) & Virality (leaderboard + share-card).

Covers:
- POST /api/quiz/start : per-run option shuffling, options_order permutations stored
- POST /api/quiz/submit : unshuffle via perm mapping; correct_by_question returns displayed indices
- POST /api/tap/submit : suspicious flagging tiers (>=100 taps → suspicious=true)
- GET  /api/tap/admin/overview : suspicious_runs + p95_taps fields
- GET  /api/tap/admin/suspicious : flagged runs with ip_address & user_agent
- GET  /api/quiz/admin/overview : avg_points_per_run, avg_duration_seconds, timed_out_runs
- GET  /api/engagement/leaderboard/weekly + /all (shape)
- GET  /api/engagement/share-card.png (>20KB, PNG) + /share-card-public.png
"""
import os
import time
import uuid
import asyncio
from collections import Counter

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
    return s, r.json()


def _reset(admin_s, uid):
    admin_s.post(f"{API}/quiz/admin/reset-user/{uid}")
    admin_s.post(f"{API}/tap/admin/reset-user/{uid}")


@pytest.fixture(scope="module")
def admin_session():
    s, _ = _login(ADMIN_EMAIL, ADMIN_PWD)
    return s


@pytest.fixture(scope="module")
def bob(admin_session):
    s, data = _login(USER_EMAIL, USER_PWD)
    uid = (data.get("user") or {}).get("user_id")
    assert uid, "bob user_id missing"
    _reset(admin_session, uid)
    return {"session": s, "user_id": uid}


# ════════════════════════════════════════════════════
#  QUIZ — Shuffling
# ════════════════════════════════════════════════════
class TestQuizShuffle:
    def test_options_order_persisted_in_run(self, bob, admin_session):
        """Start a run; verify options_order has 5 permutations of [0,1,2,3] stored."""
        _reset(admin_session, bob["user_id"])
        s = bob["session"]
        r = s.post(f"{API}/quiz/start")
        assert r.status_code == 200, r.text
        run = r.json()
        run_id = run["run_id"]
        assert len(run["questions"]) == 5

        async def _fetch_perms():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT options_order FROM quiz_user_runs WHERE id=$1", run_id
                )
                return row["options_order"] if row else None
            finally:
                await conn.close()

        perms = asyncio.run(_fetch_perms())
        assert perms is not None, "options_order not stored"
        # options_order may be stored as JSON string or list-of-lists
        import json
        if isinstance(perms, str):
            perms = json.loads(perms)
        assert len(perms) == 5, f"Expected 5 perms, got {len(perms)}"
        for p in perms:
            assert sorted(p) == [0, 1, 2, 3], f"Invalid perm: {p}"
        # submit to clear the slot
        s.post(f"{API}/quiz/submit", json={"run_id": run_id, "answers": [0]*5})

    def test_shuffle_distribution_across_runs(self, bob, admin_session):
        """Run 3 starts; verify at least some permutation variety (not always identity)."""
        all_first_perms = []
        for _ in range(3):
            _reset(admin_session, bob["user_id"])
            s = bob["session"]
            r = s.post(f"{API}/quiz/start").json()
            rid = r["run_id"]

            async def _fetch(rid=rid):
                conn = await asyncpg.connect(DATABASE_URL)
                try:
                    row = await conn.fetchrow(
                        "SELECT options_order FROM quiz_user_runs WHERE id=$1", rid
                    )
                    import json
                    p = row["options_order"]
                    return json.loads(p) if isinstance(p, str) else p
                finally:
                    await conn.close()

            perms = asyncio.run(_fetch())
            all_first_perms.extend(perms)
            s.post(f"{API}/quiz/submit", json={"run_id": rid, "answers": [0]*5})
        # Not all 15 perms should be identical [0,1,2,3]
        identity_count = sum(1 for p in all_first_perms if list(p) == [0, 1, 2, 3])
        assert identity_count < len(all_first_perms), \
            f"All perms are identity [0,1,2,3] — shuffle is NOT working ({identity_count}/{len(all_first_perms)})"

    def test_submit_scores_correctly_with_shuffled_options(self, bob, admin_session):
        """Fetch original correct_index, translate via perm → displayed index, submit, expect perfect."""
        _reset(admin_session, bob["user_id"])
        s = bob["session"]
        r = s.post(f"{API}/quiz/start").json()
        run_id = r["run_id"]
        session_id = r["session_id"]

        async def _get_correct_originals_and_perms():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                sess = await conn.fetchrow(
                    "SELECT question_ids FROM quiz_sessions WHERE id=$1", session_id
                )
                qids = list(sess["question_ids"])
                rows = await conn.fetch(
                    "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
                    qids,
                )
                mp = {int(x["id"]): int(x["correct_index"]) for x in rows}
                run_row = await conn.fetchrow(
                    "SELECT options_order FROM quiz_user_runs WHERE id=$1", run_id
                )
                import json
                perms = run_row["options_order"]
                if isinstance(perms, str):
                    perms = json.loads(perms)
                return [mp[q] for q in qids], perms
            finally:
                await conn.close()

        originals, perms = asyncio.run(_get_correct_originals_and_perms())
        # Displayed index = position of original in perm (perm[displayed] = original)
        displayed = [list(perms[i]).index(originals[i]) for i in range(5)]
        sub = s.post(f"{API}/quiz/submit", json={"run_id": run_id, "answers": displayed})
        assert sub.status_code == 200, sub.text
        d = sub.json()
        assert d["correct_count"] == 5, f"expected 5, got {d['correct_count']}"
        assert d["perfect"] is True
        # correct_by_question must return DISPLAYED indices
        assert d["correct_by_question"] == displayed, \
            f"correct_by_question should be displayed indices. got {d['correct_by_question']} expected {displayed}"


# ════════════════════════════════════════════════════
#  QUIZ admin — new metrics
# ════════════════════════════════════════════════════
class TestQuizAdminMetrics:
    def test_overview_has_new_fields(self, admin_session):
        r = admin_session.get(f"{API}/quiz/admin/overview", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("avg_points_per_run", "avg_duration_seconds", "timed_out_runs"):
            assert k in d, f"missing key: {k}"


# ════════════════════════════════════════════════════
#  TAP — Suspicious flagging
# ════════════════════════════════════════════════════
class TestTapSuspicious:
    def test_25_taps_not_suspicious(self, bob, admin_session):
        _reset(admin_session, bob["user_id"])
        s = bob["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        d = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 25}).json()
        assert d.get("suspicious") is False, f"25 taps flagged suspicious: {d}"
        assert d.get("cheated") is False

    def test_105_taps_suspicious_not_cheated(self, bob, admin_session):
        _reset(admin_session, bob["user_id"])
        s = bob["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        d = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 105}).json()
        assert d.get("suspicious") is True, f"105 taps NOT suspicious: {d}"
        assert d.get("cheated") is False, f"105<=120 cap should NOT be cheated: {d}"
        assert d["taps_valid"] == 105
        assert d["bonus_awarded"] == 50  # tier >=80

    def test_150_taps_suspicious_and_cheated(self, bob, admin_session):
        _reset(admin_session, bob["user_id"])
        s = bob["session"]
        rid = s.post(f"{API}/tap/start").json()["run_id"]
        d = s.post(f"{API}/tap/submit", json={"run_id": rid, "taps": 150}).json()
        assert d["taps_raw"] == 150
        assert d["taps_valid"] == 120
        assert d.get("cheated") is True
        assert d.get("suspicious") is True
        assert d["bonus_awarded"] == 50


class TestTapAdminNew:
    def test_overview_has_suspicious_and_p95(self, admin_session):
        r = admin_session.get(f"{API}/tap/admin/overview", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "suspicious_runs" in d
        assert "p95_taps" in d

    def test_suspicious_endpoint_returns_flagged(self, admin_session):
        r = admin_session.get(f"{API}/tap/admin/suspicious", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        items = d.get("items") if isinstance(d, dict) else d
        assert isinstance(items, list)
        # If any items exist, check shape
        if items:
            sample = items[0]
            for k in ("ip_address", "user_agent"):
                assert k in sample, f"missing {k}"

    def test_suspicious_blocked_for_non_admin(self, bob):
        r = bob["session"].get(f"{API}/tap/admin/suspicious")
        assert r.status_code in (401, 403)


# ════════════════════════════════════════════════════
#  Engagement leaderboard + share card
# ════════════════════════════════════════════════════
class TestEngagementLeaderboard:
    def test_weekly_shape(self, bob):
        r = bob["session"].get(f"{API}/engagement/leaderboard/weekly")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["period"] == "week"
        assert "top" in d
        assert "me" in d  # key present, may be None
        if d["top"]:
            p = d["top"][0]
            for k in ("rank", "user_id", "name", "points_total",
                      "points_wheel", "points_quiz", "points_tap",
                      "days_played", "cycle_points", "quiz_accuracy"):
                assert k in p, f"missing {k} in top entry"

    def test_all_returns_30d(self):
        # unauth allowed? test as user
        s, _ = _login(USER_EMAIL, USER_PWD)
        r = s.get(f"{API}/engagement/leaderboard/all")
        assert r.status_code == 200
        d = r.json()
        assert d.get("period") == "30d"
        assert "top" in d


class TestShareCard:
    def test_share_card_png_authenticated(self, bob):
        r = bob["session"].get(f"{API}/engagement/share-card.png")
        assert r.status_code == 200, r.text[:500]
        assert "image/png" in r.headers.get("Content-Type", "").lower()
        assert len(r.content) > 20000, f"PNG too small: {len(r.content)}"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_share_card_public_png(self, bob):
        s = _session()  # fresh, no auth
        r = s.get(f"{API}/engagement/share-card-public.png",
                  params={"user_id": bob["user_id"]})
        assert r.status_code == 200, r.text[:500]
        assert "image/png" in r.headers.get("Content-Type", "").lower()
        assert len(r.content) > 20000
