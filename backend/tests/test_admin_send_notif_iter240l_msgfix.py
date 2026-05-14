"""iter240l-msgfix — Admin send-notification endpoint tests.

Covers:
- 200 OK with valid message → row in `notifications` + audit row in `admin_user_notes`
- 422 when message is empty/whitespace-only
- 404 when target user doesn't exist
- 401/403 when no admin auth
- Regression: other admin actions (notes, restrict/unrestrict, reset-game-limits, detail) still work
"""
import os
import time
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
            ).rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
BYPASS = "JAPAP_E2E_BYPASS_2026"
USER_JMG = "usr_mig_3d84c1905fe5"
USER_NOTEXIST = "usr_does_not_exist_xyz"


@pytest.fixture(scope="module", autouse=True)
def _warmup():
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
    tok = r.json().get("access_token") or r.json().get("token")
    if not tok:
        pytest.skip("No token in login response")
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ─── send-notification ────────────────────────────────────────────────────
def test_send_notification_unauth():
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/send-notification",
        json={"message": "no auth", "type": "info"}, timeout=60,
    )
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}: {r.text[:200]}"


def test_send_notification_ok_creates_row_and_audit(admin_headers):
    unique_msg = f"TEST iter240l-msgfix OK {int(time.time())}"
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/send-notification",
        headers=admin_headers,
        json={"message": unique_msg, "type": "info"},
        timeout=60,
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data.get("ok") is True
    notif_id = data.get("notif_id")
    assert isinstance(notif_id, str) and notif_id.startswith("notif_"), f"bad notif_id={notif_id}"

    # Verify persistence: detail returns notes (incl. the auto-log).
    detail = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail",
                          headers=admin_headers, timeout=60)
    assert detail.status_code == 200
    notes = detail.json().get("notes") or []
    found = any(unique_msg in (n.get("note") or "") and "[MESSAGE ENVOYÉ" in (n.get("note") or "")
                for n in notes)
    assert found, f"audit log not found in admin_user_notes. recent notes: {[n.get('note','')[:80] for n in notes[:5]]}"


def test_send_notification_empty_message_422(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/send-notification",
        headers=admin_headers,
        json={"message": "   ", "type": "info"},
        timeout=60,
    )
    # Pydantic min_length=1 may catch the literal empty case as 422.
    # The handler also returns 422 with detail="Message vide" for whitespace-only.
    assert r.status_code == 422, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail")
    # detail can be a string OR a list (Pydantic validation list). Accept either,
    # but our handler returns the literal "Message vide".
    if isinstance(detail, str):
        assert detail == "Message vide", f"unexpected detail: {detail}"


def test_send_notification_user_not_found_404(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_NOTEXIST}/send-notification",
        headers=admin_headers,
        json={"message": "ping", "type": "info"},
        timeout=60,
    )
    assert r.status_code == 404, f"got {r.status_code}: {r.text[:200]}"
    assert r.json().get("detail") == "Utilisateur introuvable"


# ─── Regression: other admin actions still work ───────────────────────────
def test_regression_add_and_list_notes(admin_headers):
    unique_note = f"TEST regression note {int(time.time())}"
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/notes",
        headers=admin_headers, json={"note": unique_note}, timeout=60,
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json().get("ok") is True

    r2 = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/notes",
                      headers=admin_headers, timeout=60)
    assert r2.status_code == 200
    assert any(unique_note == (n.get("note") or "") for n in r2.json())


def test_regression_restrict_then_unrestrict(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/restrict",
        headers=admin_headers,
        json={"type": "posts", "reason": "TEST iter240l-msgfix regression",
              "duration_days": 1},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json().get("ok") is True

    r2 = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/unrestrict",
        headers=admin_headers,
        json={"type": "posts", "reason": "TEST cleanup"},
        timeout=60,
    )
    assert r2.status_code == 200, r2.text[:200]
    assert r2.json().get("ok") is True


def test_regression_reset_game_limits(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/users/{USER_JMG}/reset-game-limits",
        headers=admin_headers, json={}, timeout=60,
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_regression_detail_game_activity_still_works(admin_headers):
    # iter240l-gamefix regression check
    r = requests.get(f"{BASE_URL}/api/admin/users/{USER_JMG}/detail",
                     headers=admin_headers, timeout=60)
    assert r.status_code == 200
    body = r.json()
    ga = body.get("game_activity") or {}
    assert "quiz" in ga and "fortune_wheel" in ga and "mini_spin" in ga
    assert "staking" in ga
    quiz = ga["quiz"]
    assert quiz.get("total_played", 0) >= 2
