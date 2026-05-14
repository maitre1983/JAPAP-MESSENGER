"""iter240l-gamefix — Admin user detail (game_activity from transactions) tests."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
BASE_URL = BASE_URL.rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
BYPASS = "JAPAP_E2E_BYPASS_2026"
USER_JMG = "usr_mig_3d84c1905fe5"   # @JMGHOMSI
USER_BALE = "user_7d622b644231"     # @balebasimb04da


@pytest.fixture(scope="module", autouse=True)
def _warmup():
    # Cold-start can take 30s+ on the preview URL.
    for _ in range(3):
        try:
            requests.get(f"{BASE_URL}/api/health", timeout=60)
            break
        except Exception:
            continue


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
              "captcha_id": BYPASS, "captcha_answer": "0"},
        timeout=60,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    if not tok:
        pytest.skip(f"No token in login response: {list(data.keys())}")
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ─── Admin auth gating ────────────────────────────────────────────────────
def test_detail_requires_auth():
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail", timeout=60)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


def test_detail_admin_ok(admin_headers):
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail",
                     headers=admin_headers, timeout=60)
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"


# ─── Schema: game_activity keys ───────────────────────────────────────────
def test_game_activity_schema(admin_headers):
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail",
                     headers=admin_headers, timeout=60)
    assert r.status_code == 200
    ga = r.json().get("game_activity")
    assert isinstance(ga, dict)
    # quiz keys
    quiz = ga.get("quiz") or {}
    for k in ("total_played", "total_won_usd", "total_won_pts",
              "total_refunded_usd", "last_played_at"):
        assert k in quiz, f"quiz missing key {k}"
    # fortune_wheel
    fw = ga.get("fortune_wheel") or {}
    for k in ("total_played", "total_won", "last_played_at"):
        assert k in fw, f"fortune_wheel missing key {k}"
    # mini_spin
    ms = ga.get("mini_spin") or {}
    for k in ("total_played", "total_won", "last_played_at"):
        assert k in ms, f"mini_spin missing key {k}"
    # staking
    st = ga.get("staking") or {}
    for k in ("active_stakes", "total_staked"):
        assert k in st, f"staking missing key {k}"
    # debug list
    att = ga.get("all_transaction_types")
    assert isinstance(att, list), "all_transaction_types must be a list"
    for elem in att:
        for k in ("type", "currency", "n", "sum_amount", "last_at"):
            assert k in elem, f"all_transaction_types item missing {k}"


# ─── Business expectations ────────────────────────────────────────────────
def test_jmghomsi_quiz_thresholds(admin_headers):
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail",
                     headers=admin_headers, timeout=60)
    assert r.status_code == 200
    quiz = r.json()["game_activity"]["quiz"]
    assert quiz["total_played"] >= 2, f"played={quiz['total_played']}"
    assert float(quiz["total_won_pts"]) >= 100, f"won_pts={quiz['total_won_pts']}"


def test_balebasimb_quiz_thresholds(admin_headers):
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_BALE}/detail",
                     headers=admin_headers, timeout=60)
    assert r.status_code == 200, r.text[:300]
    quiz = r.json()["game_activity"]["quiz"]
    assert quiz["total_played"] >= 4, f"played={quiz['total_played']}"
    assert float(quiz["total_won_usd"]) >= 18, f"won_usd={quiz['total_won_usd']}"
    assert float(quiz["total_won_pts"]) >= 100, f"won_pts={quiz['total_won_pts']}"


# ─── Other dossier sections still present (no regression) ─────────────────
def test_no_regression_dossier_sections(admin_headers):
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail",
                     headers=admin_headers, timeout=60)
    assert r.status_code == 200
    body = r.json()
    for key in ("user", "kyc", "wallet", "transactions", "game_activity",
                "restrictions", "posts", "crowdfunding", "login_history",
                "flags", "referrals", "notes"):
        assert key in body, f"missing top-level key {key}"
    assert isinstance(body["transactions"], list)
    assert isinstance(body["restrictions"], list)
    assert isinstance(body["notes"], list)
    assert isinstance(body["wallet"], dict)
    assert "balance" in body["wallet"]
