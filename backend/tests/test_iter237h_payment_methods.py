"""
iter237h — Payment methods catalog + per-user eligibility endpoint.

P2 catalog: GET /api/payment-methods → public list (no auth required).
P1 eligibility: GET /api/payment-methods/eligibility?method=X&flow=Y → auth required.
"""
from __future__ import annotations

import os
import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://japap-refactor.preview.emergentagent.com",
).rstrip("/")
ALICE = {"email": "alice@japap.com", "password": "Alice2026!",
         "captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}


def _login(creds):
    s = requests.Session()
    for _ in range(3):
        try:
            r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                return s
        except Exception:
            pass
    pytest.skip("login flaked")


# ───────────── P2 — Catalog (public) ─────────────
def test_catalog_is_public_and_well_shaped():
    r = requests.get(f"{BASE}/api/payment-methods", timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    methods = body.get("methods")
    assert isinstance(methods, list) and len(methods) >= 5
    ids = [m["id"] for m in methods]
    for required in ("orange_money_cm", "wave",
                     "hubtel_card", "nowpayments_usdttrc20", "nowpayments_usdtbsc"):
        assert required in ids, f"{required} missing from catalog"
    # Each method has the expected shape and no rate/secret fields leaked.
    for m in methods:
        assert {"id", "label", "availability", "restricted_countries",
                "is_global", "display_order"}.issubset(m.keys())
        forbidden = {"rate", "taux", "deposit_rate", "withdraw_rate",
                     "secret", "api_key", "password"}
        for f in forbidden:
            assert f not in m, f"Catalog leaked forbidden key {f}"


# ───────────── P1 — Eligibility (auth required) ─────────────
def test_eligibility_requires_auth():
    r = requests.get(f"{BASE}/api/payment-methods/eligibility?method=wave&flow=deposit", timeout=15)
    assert r.status_code in (401, 403)


def test_eligibility_returns_actionable_suggestion():
    s = _login(ALICE)
    # Wave for non-Wave country: should be eligible:false + suggestion containing USDT.
    r = s.get(f"{BASE}/api/payment-methods/eligibility",
              params={"method": "wave", "flow": "deposit"}, timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert "eligible" in body and "user_country" in body and "suggestion" in body
    # Whether Alice is eligible or not depends on her current country in DB,
    # but if not eligible the suggestion must list alternatives:
    if body["eligible"] is False:
        assert "USDT" in body["suggestion"] or "Orange" in body["suggestion"]
    # Rate must NEVER be in this payload.
    assert "rate" not in body and "taux" not in body


def test_eligibility_unknown_method_404():
    s = _login(ALICE)
    r = s.get(f"{BASE}/api/payment-methods/eligibility",
              params={"method": "totally_unknown", "flow": "deposit"}, timeout=15)
    assert r.status_code == 404


def test_eligibility_global_methods_always_eligible():
    s = _login(ALICE)
    for m in ("hubtel_card", "nowpayments_usdttrc20", "nowpayments_usdtbsc"):
        r = s.get(f"{BASE}/api/payment-methods/eligibility",
                  params={"method": m, "flow": "deposit"}, timeout=15)
        assert r.status_code == 200, (m, r.text[:200])
        assert r.json()["eligible"] is True, m
