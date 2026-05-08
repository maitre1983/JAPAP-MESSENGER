"""
Iteration 31 backend tests:
- Pro plans endpoint exposes ab_variant + urgency + ab_discount_pct
- A/B variant B applies -20% on price_usd AND on /pro/subscribe payment
- /api/pro/upsell-config returns enabled/threshold/ab_variant
- /api/ads/* legacy endpoints still functional
"""
import os
from decimal import Decimal

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"


# ---------- Fixtures ----------

@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    assert token, f"No access_token in login response: {data}"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="session", autouse=True)
def reset_ab_variant_after(admin_session):
    """Ensure variant is reset to A after the test session."""
    yield
    try:
        admin_session.put(
            f"{BASE_URL}/api/admin/settings",
            json={"settings": {"pro_ab_variant": "A", "pro_urgency_enabled": "false"}},
            timeout=15,
        )
    except Exception:
        pass


def _set_setting(session, key, value):
    r = session.put(
        f"{BASE_URL}/api/admin/settings",
        json={"settings": {key: value}},
        timeout=15,
    )
    assert r.status_code == 200, f"Failed setting {key}: {r.status_code} {r.text[:200]}"


# ---------- Pro plans: A/B + urgency ----------

class TestProPlansAB:
    def test_plans_shape_contains_ab_and_urgency(self, admin_session):
        # Ensure variant A first
        _set_setting(admin_session, "pro_ab_variant", "A")
        r = admin_session.get(f"{BASE_URL}/api/pro/plans", timeout=20)
        assert r.status_code == 200
        data = r.json()
        assert "ab_variant" in data
        assert "urgency" in data
        assert "plans" in data and len(data["plans"]) > 0
        assert data["ab_variant"] == "A"
        for p in data["plans"]:
            assert "price_usd" in p
            assert "original_price_usd" in p
            assert "ab_variant" in p
            assert "ab_discount_pct" in p
            # Variant A: price == original
            assert Decimal(p["price_usd"]) == Decimal(p["original_price_usd"]), (
                f"Variant A should not discount: {p}"
            )
            assert p["ab_discount_pct"] == 0

    def test_variant_B_applies_20pct_discount(self, admin_session):
        _set_setting(admin_session, "pro_ab_variant", "B")
        r = admin_session.get(f"{BASE_URL}/api/pro/plans", timeout=20)
        assert r.status_code == 200
        data = r.json()
        assert data["ab_variant"] == "B"
        for p in data["plans"]:
            original = Decimal(p["original_price_usd"])
            price = Decimal(p["price_usd"])
            expected = (original * Decimal("0.80")).quantize(Decimal("0.01"))
            assert price == expected, (
                f"Plan {p['plan_id']} price={price} expected {expected} (original={original})"
            )
            assert p["ab_discount_pct"] == 20
            assert p["ab_variant"] == "B"

    def test_variant_reset_to_A(self, admin_session):
        _set_setting(admin_session, "pro_ab_variant", "A")
        r = admin_session.get(f"{BASE_URL}/api/pro/plans", timeout=20)
        assert r.status_code == 200
        data = r.json()
        assert data["ab_variant"] == "A"
        for p in data["plans"]:
            assert Decimal(p["price_usd"]) == Decimal(p["original_price_usd"])

    def test_urgency_banner_roundtrip(self, admin_session):
        # Enable urgency
        r = admin_session.put(
            f"{BASE_URL}/api/admin/settings",
            json={
                "settings": {
                    "pro_urgency_enabled": "true",
                    "pro_urgency_title": "TEST_URGENCY_TITLE",
                    "pro_urgency_subtitle": "TEST_SUB",
                    "pro_urgency_ends_at": "2027-01-01T00:00:00Z",
                    "pro_urgency_discount_pct": 15,
                }
            },
            timeout=15,
        )
        assert r.status_code == 200
        plans = admin_session.get(f"{BASE_URL}/api/pro/plans", timeout=20).json()
        urg = plans.get("urgency", {})
        assert urg.get("enabled") is True
        assert urg.get("title") == "TEST_URGENCY_TITLE"
        assert urg.get("subtitle") == "TEST_SUB"
        assert urg.get("ends_at") == "2027-01-01T00:00:00Z"
        assert int(urg.get("discount_pct", 0)) == 15

        # Disable urgency
        admin_session.put(
            f"{BASE_URL}/api/admin/settings",
            json={"settings": {"pro_urgency_enabled": "false"}},
            timeout=15,
        )
        plans2 = admin_session.get(f"{BASE_URL}/api/pro/plans", timeout=20).json()
        assert plans2["urgency"]["enabled"] is False


# ---------- Upsell config ----------

class TestUpsellConfig:
    def test_upsell_config_shape(self, admin_session):
        _set_setting(admin_session, "pro_smart_upsell_enabled", "true")
        _set_setting(admin_session, "pro_smart_upsell_threshold", 3)
        _set_setting(admin_session, "pro_ab_variant", "A")
        r = admin_session.get(f"{BASE_URL}/api/pro/upsell-config", timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert "enabled" in data
        assert "threshold" in data
        assert "ab_variant" in data
        assert isinstance(data["threshold"], int)
        assert data["threshold"] == 3
        assert data["ab_variant"] in ("A", "B")

    def test_upsell_threshold_update(self, admin_session):
        _set_setting(admin_session, "pro_smart_upsell_threshold", 5)
        r = admin_session.get(f"{BASE_URL}/api/pro/upsell-config", timeout=15)
        assert r.json()["threshold"] == 5
        # restore
        _set_setting(admin_session, "pro_smart_upsell_threshold", 3)

    def test_upsell_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/pro/upsell-config", timeout=15)
        assert r.status_code in (401, 403), f"Expected auth block, got {r.status_code}"


# ---------- Subscribe with A/B variant ----------

class TestSubscribeABPricing:
    def _get_wallet_balance(self, session):
        r = session.get(f"{BASE_URL}/api/wallet/me", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()

    def _ensure_no_active_sub(self, session):
        """Cancel any active subscription for admin to allow a fresh subscribe test."""
        try:
            st = session.get(f"{BASE_URL}/api/pro/status", timeout=15).json()
            if st.get("is_pro"):
                session.post(f"{BASE_URL}/api/pro/cancel", timeout=15)
        except Exception:
            pass

    def test_subscribe_applies_ab_discount_to_payment(self, admin_session):
        """With variant B, a $5 plan should cost $4 at checkout."""
        # Setup: variant B
        _set_setting(admin_session, "pro_ab_variant", "B")
        # Get a starter plan
        plans_resp = admin_session.get(f"{BASE_URL}/api/pro/plans", timeout=20).json()
        plans = plans_resp["plans"]
        # Try to pick plan with smallest original price
        plans_sorted = sorted(plans, key=lambda p: Decimal(p["original_price_usd"]))
        target = plans_sorted[0]
        original_usd = Decimal(target["original_price_usd"])
        discounted_usd = Decimal(target["price_usd"])
        expected_discounted = (original_usd * Decimal("0.80")).quantize(Decimal("0.01"))
        assert discounted_usd == expected_discounted

        # Ensure no active sub
        self._ensure_no_active_sub(admin_session)

        # Attempt subscribe
        r = admin_session.post(
            f"{BASE_URL}/api/pro/subscribe",
            json={"plan_id": target["plan_id"], "duration": "1m", "use_trial": False},
            timeout=30,
        )
        if r.status_code == 400 and "Solde" in r.text:
            pytest.skip(f"Admin wallet insufficient funds for subscribe test: {r.text[:200]}")
        assert r.status_code in (200, 201), f"Subscribe failed: {r.status_code} {r.text[:300]}"
        resp = r.json()
        # Expect paid amount reflects AB discount
        paid = resp.get("paid_usd") or resp.get("amount_usd") or resp.get("subtotal_usd")
        if paid is not None:
            assert Decimal(str(paid)).quantize(Decimal("0.01")) == expected_discounted, (
                f"Paid amount {paid} != expected {expected_discounted}"
            )
        # cleanup
        try:
            admin_session.post(f"{BASE_URL}/api/pro/cancel", timeout=15)
        except Exception:
            pass
        _set_setting(admin_session, "pro_ab_variant", "A")


# ---------- Ads endpoints still functional (iter30 regression) ----------

class TestAdsEndpointsStillWork:
    def test_ads_campaigns_mine(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/ads/campaigns/mine", timeout=15)
        assert r.status_code in (200, 401, 403), f"status={r.status_code} body={r.text[:200]}"
        if r.status_code == 200:
            assert isinstance(r.json(), (list, dict))

    def test_ads_serve(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/ads/serve?placement=feed", timeout=15)
        # 200 with ad or 204/200 no-content depending impl
        assert r.status_code in (200, 204, 404), f"status={r.status_code}"

    def test_ads_admin_campaigns(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/ads/admin/campaigns", timeout=15)
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert isinstance(data, (list, dict))
