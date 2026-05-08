"""iter233 — backend tests for DoublersLeaderboard + admin validation-stats + challenge detail payload."""
import os
import requests

BASE = os.environ.get('REACT_APP_BACKEND_URL', 'https://japap-refactor.preview.emergentagent.com').rstrip('/')
BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
TO = 90  # long timeout, cold-start can be ~60s


def _login(email, pwd):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": pwd, **BYPASS}, timeout=TO)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    return s


# ---- Mission 2 : leaderboard public endpoint ----
def test_leaderboard_doublers_public_no_auth():
    r = requests.get(f"{BASE}/api/quiz/champion/leaderboard/doublers?limit=10", timeout=TO)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("window_days") == 30
    leaders = data.get("leaders", [])
    assert isinstance(leaders, list)
    assert len(leaders) >= 1, "expected at least 1 leader (Daf Unity)"
    top = leaders[0]
    for k in ("user_id", "name", "wins_doubled", "total_won_usd"):
        assert k in top, f"missing {k}"
    assert top["wins_doubled"] >= 1
    assert top["total_won_usd"] >= 1


def test_leaderboard_doublers_limit_param():
    r = requests.get(f"{BASE}/api/quiz/champion/leaderboard/doublers?limit=1", timeout=TO)
    assert r.status_code == 200
    assert len(r.json().get("leaders", [])) <= 1


# ---- Mission 1 : admin validation-stats ----
def test_admin_validation_stats_requires_admin():
    r = requests.get(f"{BASE}/api/admin/games/quiz/validation-stats?days=30", timeout=TO)
    # unauthenticated must not leak the data
    assert r.status_code in (401, 403), r.status_code


def test_admin_validation_stats_shape():
    s = _login("admin@japap.com", "JapapAdmin2024!")
    r = s.get(f"{BASE}/api/admin/games/quiz/validation-stats?days=30", timeout=TO)
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("generated", "accepted", "rejected", "accept_rate_pct", "avg_confidence"):
        assert k in d, f"missing {k}"
    assert 0 <= d["accept_rate_pct"] <= 100


# ---- Mission 2 : challenge detail shape (allow_double + doubled) ----
def test_challenge_detail_exposes_doubled_flags():
    s = _login("mirtoken2022@gmail.com", "Daf2026!")
    cid = "qcc_721f6044166045e8"
    r = s.get(f"{BASE}/api/quiz/champion/challenges/{cid}", timeout=TO)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    d = r.json()
    # per review_request files_of_reference: get_challenge now returns allow_double + doubled
    assert "doubled" in d, f"challenge detail missing 'doubled' flag: keys={list(d.keys())}"
    assert d.get("doubled") is True
    assert "allow_double" in d, f"challenge detail missing 'allow_double' flag: keys={list(d.keys())}"
    assert d.get("winner_user_id") == "user_9ad70ec54565"
