"""Iteration 65 — AI template generation + system seeds.

Covers:
  • 5 system templates seeded at boot (including Migration 1.0 → 4.0)
  • `cmp_sys_migration_draft` campaign exists in status=draft
  • POST /templates/generate-ai returns correct shape via Claude Sonnet 4.5
  • Non-admin 403 on AI endpoint
  • Empty goal → 422
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


def test_system_templates_seeded(admin):
    r = admin.get(f"{BASE_URL}/api/admin/messaging/templates", timeout=10)
    assert r.status_code == 200
    ids = {t["template_id"] for t in r.json()["items"]}
    expected = {
        "tpl_sys_migration_1_to_4", "tpl_sys_welcome",
        "tpl_sys_inactive_reactivation", "tpl_sys_pro_upgrade",
        "tpl_sys_referral_motivation",
    }
    assert expected.issubset(ids), f"Missing seeded templates: {expected - ids}"


def test_migration_draft_campaign_seeded(admin):
    r = admin.get(f"{BASE_URL}/api/admin/messaging/campaigns/cmp_sys_migration_draft",
                  timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "draft"
    assert d["template_id"] == "tpl_sys_migration_1_to_4"
    assert d["segment_id"] == "seg_legacy_migrated"
    # Placeholders still present (should only resolve at send time)
    assert "{{first_name}}" in d["subject"]


def test_ai_endpoint_forbidden_for_non_admin():
    s = _login(BOB)
    r = s.post(f"{BASE_URL}/api/admin/messaging/templates/generate-ai",
               json={"goal": "test"}, timeout=15)
    assert r.status_code == 403


def test_ai_endpoint_empty_goal_422(admin):
    r = admin.post(f"{BASE_URL}/api/admin/messaging/templates/generate-ai",
                   json={"goal": "x"}, timeout=15)  # min_length=3
    assert r.status_code == 422


@pytest.mark.timeout(45)
def test_ai_generate_template_shape(admin):
    """Claude Sonnet 4.5 returns a template with all 5 required fields."""
    r = admin.post(
        f"{BASE_URL}/api/admin/messaging/templates/generate-ai",
        json={
            "goal": "Welcome new users with a friendly intro to JAPAP features",
            "audience": "new signups",
            "tone": "warm",
            "language": "fr",
            "cta_type": "primary action",
        },
        timeout=45,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    tpl = body["template"]
    for k in ("subject", "preview_text", "body_html", "body_text", "cta_label"):
        assert k in tpl and tpl[k], f"Missing/empty field: {k}"
    assert len(tpl["subject"]) <= 200
    assert len(tpl["preview_text"]) <= 160
    # ai_prompt echoed back for traceability
    assert body["ai_prompt"]["goal"].startswith("Welcome")
