"""
Iter 82 — Admin Messaging Batch Scale + Referral Tiers Editor
Regression tests. Run with :
    cd /app/backend && pytest tests/test_iter82_batch_tiers.py -q
"""
import json
import pytest
import httpx

BASE = "http://localhost:8001"
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


async def _login_admin():
    async with httpx.AsyncClient(base_url=BASE, follow_redirects=True) as c:
        r = await c.post("/api/auth/login", json=ADMIN)
        assert r.status_code == 200, r.text
        cookies = {k: v for k, v in c.cookies.items()}
        return cookies


@pytest.mark.asyncio
async def test_batch_status_returns_safe_mode():
    cookies = await _login_admin()
    async with httpx.AsyncClient(base_url=BASE, cookies=cookies) as c:
        r = await c.get("/api/admin/messaging/batch/status")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "queue" in data and "settings" in data
        assert set(data["settings"].keys()) >= {
            "real_send_enabled", "max_audience_per_campaign",
            "worker_rate_per_minute", "batch_size",
        }
        # Safe mode must be OFF by default after iter82 migration
        assert data["settings"]["real_send_enabled"] is False


@pytest.mark.asyncio
async def test_batch_settings_update_roundtrip():
    cookies = await _login_admin()
    async with httpx.AsyncClient(base_url=BASE, cookies=cookies) as c:
        # Update two values, check roundtrip
        r = await c.put("/api/admin/messaging/batch/settings",
                        json={"worker_rate_per_minute": 75, "batch_size": 30})
        assert r.status_code == 200, r.text
        r2 = await c.get("/api/admin/messaging/batch/status")
        s = r2.json()["settings"]
        assert s["worker_rate_per_minute"] == 75
        assert s["batch_size"] == 30


@pytest.mark.asyncio
async def test_batch_settings_rejects_empty_payload():
    cookies = await _login_admin()
    async with httpx.AsyncClient(base_url=BASE, cookies=cookies) as c:
        r = await c.put("/api/admin/messaging/batch/settings", json={})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_safe_mode_toggle_roundtrip_audited():
    cookies = await _login_admin()
    async with httpx.AsyncClient(base_url=BASE, cookies=cookies) as c:
        # Enable then disable
        await c.put("/api/admin/messaging/batch/settings",
                    json={"real_send_enabled": True})
        s = (await c.get("/api/admin/messaging/batch/status")).json()["settings"]
        assert s["real_send_enabled"] is True
        await c.put("/api/admin/messaging/batch/settings",
                    json={"real_send_enabled": False})
        s = (await c.get("/api/admin/messaging/batch/status")).json()["settings"]
        assert s["real_send_enabled"] is False


@pytest.mark.asyncio
async def test_referral_tiers_json_editable_via_settings():
    cookies = await _login_admin()
    new_tiers = [
        {"count": 5, "reward_type": "pro", "reward_value": 60, "label": "2 mois Pro"},
        {"count": 15, "reward_type": "wallet", "reward_value": 10, "label": "10 USD"},
    ]
    async with httpx.AsyncClient(base_url=BASE, cookies=cookies) as c:
        r = await c.put("/api/admin/settings/referral_tiers_json",
                        json={"value": json.dumps(new_tiers)})
        assert r.status_code == 200, r.text
        # User-side /api/referrals/config must reflect the change
        cfg = (await c.get("/api/referrals/config")).json()
        assert cfg["tiers"] == new_tiers


@pytest.mark.asyncio
async def test_requeue_failed_returns_count():
    cookies = await _login_admin()
    async with httpx.AsyncClient(base_url=BASE, cookies=cookies) as c:
        r = await c.post("/api/admin/messaging/batch/requeue-failed")
        assert r.status_code == 200, r.text
        assert "requeued" in r.json()
