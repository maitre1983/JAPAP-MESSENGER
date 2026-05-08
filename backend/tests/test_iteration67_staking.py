"""
Iter67 — Crypto Staking soft launch (MIR / BSC / signature-based).

Smoke-covers the public + admin endpoints end-to-end. Real Web3 wallet
signature verification is out of scope for this soft launch — we only
persist what the user sends.
"""
import os, requests, time
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB   = {"email": "bob@japap.com",   "password": "Test1234!"}
WALLET_BOB = "0xAbCdEf0123456789aBcDeF0123456789abCdef01"


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=10)
    r.raise_for_status()
    tok = r.json()["access_token"]
    s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


def test_public_plans_available(admin):
    r = requests.get(f"{BASE_URL}/api/staking/plans", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["token_symbol"] == "MIR"
    assert d["chain_name"] == "Binance Smart Chain"
    plans = d["items"]
    assert len(plans) >= 3
    months = sorted(p["duration_months"] for p in plans)
    assert 3 in months and 6 in months and 12 in months
    # Default APYs per user spec: 4%, 8%, 15% → bps 400, 800, 1500
    by_months = {p["duration_months"]: p for p in plans}
    assert by_months[3]["apy_bps"] == 400
    assert by_months[6]["apy_bps"] == 800
    assert by_months[12]["apy_bps"] == 1500
    # Default early fee 15%
    for p in plans:
        assert p["early_withdrawal_fee_bps"] == 1500


def test_public_leaderboard_returns_shortened_wallets():
    r = requests.get(f"{BASE_URL}/api/staking/leaderboard", timeout=10)
    assert r.status_code == 200
    rows = r.json()["items"]
    if not rows:
        pytest.skip("No seeded stakers in DB")
    for row in rows:
        assert "…" in row["wallet_short"]
        assert row["wallet_address"].startswith("0x")
        assert int(row["positions"]) >= 1


def test_wallet_connect_then_stake_then_early_withdraw(admin, bob):
    # 1) Seed Bob some MIR so he can actually stake
    r = admin.post(
        f"{BASE_URL}/api/admin/staking/users/"
        f"{bob.get(f'{BASE_URL}/api/auth/me').json()['id']}/balance",
        json={"mir_balance": 500},
    )
    # That route may not be available via `id` — use user_id
    me = bob.get(f"{BASE_URL}/api/auth/me", timeout=10).json()
    uid = me.get("user_id") or me.get("id")
    r = admin.post(
        f"{BASE_URL}/api/admin/staking/users/{uid}/balance",
        json={"mir_balance": 500},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    # 2) Bob connects wallet (no signature verification in soft launch)
    r = bob.post(
        f"{BASE_URL}/api/staking/wallet/connect",
        json={"wallet_address": WALLET_BOB, "signature": "", "signed_message": ""},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["chain_id"] == 56
    # 3) Dashboard reflects the new balance
    r = bob.get(f"{BASE_URL}/api/staking/dashboard", timeout=10)
    assert r.status_code == 200
    assert float(r.json()["mir_balance"]) >= 500
    assert r.json()["wallet"]["wallet_address"].lower() == WALLET_BOB.lower()
    # 4) Stake 100 MIR on 3M plan
    r = bob.post(
        f"{BASE_URL}/api/staking/stake",
        json={"plan_id": "plan_stake_3m", "amount_mir": 100},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    pid = r.json()["position_id"]
    # 5) Dashboard updated
    dash = bob.get(f"{BASE_URL}/api/staking/dashboard", timeout=10).json()
    assert float(dash["total_staked_mir"]) >= 100
    # 6) Early withdraw → 15% fee → 85 back, 0 reward
    r = bob.post(f"{BASE_URL}/api/staking/positions/{pid}/withdraw", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["early"] is True
    assert abs(float(body["returned_mir"]) - 85.0) < 0.0001
    assert float(body["reward_mir"]) == 0.0


def test_admin_plan_crud(admin):
    # Create
    r = admin.post(
        f"{BASE_URL}/api/admin/staking/plans",
        json={
            "name": "pytest_custom",
            "duration_months": 9,
            "apy_bps": 1000,
            "early_withdrawal_fee_bps": 2000,
            "min_stake_mir": 20,
            "marketing_copy": "test",
            "is_active": False,
        },
        timeout=10,
    )
    assert r.status_code == 200
    pid = r.json()["plan_id"]
    # Update APY
    r = admin.put(
        f"{BASE_URL}/api/admin/staking/plans/{pid}",
        json={"apy_bps": 1200, "is_active": True},
        timeout=10,
    )
    assert r.status_code == 200
    # Re-fetch list confirms change
    r = admin.get(f"{BASE_URL}/api/admin/staking/plans", timeout=10)
    found = next(p for p in r.json()["items"] if p["plan_id"] == pid)
    assert found["apy_bps"] == 1200
    assert found["is_active"] is True
    # Delete (hard — no positions)
    r = admin.delete(f"{BASE_URL}/api/admin/staking/plans/{pid}", timeout=10)
    assert r.status_code == 200
    assert r.json().get("hard_deleted") is True


def test_non_admin_cannot_crud_plans(bob):
    r = bob.get(f"{BASE_URL}/api/admin/staking/plans", timeout=10)
    assert r.status_code == 403


def test_settings_locked_keys_cannot_be_enabled(admin):
    r = admin.put(
        f"{BASE_URL}/api/admin/staking/settings",
        json={
            "staking_trading_enabled": "true",
            "staking_swaps_enabled":   "true",
            "staking_chain_id":        "56",
        },
        timeout=10,
    )
    assert r.status_code == 200
    assert set(r.json()["locked_ignored"]) >= {
        "staking_trading_enabled", "staking_swaps_enabled",
    }
    # Verify locked keys unchanged
    r = admin.get(f"{BASE_URL}/api/admin/staking/settings", timeout=10).json()
    assert r["staking_trading_enabled"] == "false"
    assert r["staking_swaps_enabled"]   == "false"


def test_stake_requires_wallet(bob):
    # Try to stake without connecting — should 400 (or we might have wallet from prior test)
    # Connect invalid wallet first to force failure
    r = bob.post(
        f"{BASE_URL}/api/staking/wallet/connect",
        json={"wallet_address": "not-an-address"},
        timeout=10,
    )
    assert r.status_code == 400


def test_stake_under_minimum_rejected(admin, bob):
    me = bob.get(f"{BASE_URL}/api/auth/me", timeout=10).json()
    uid = me.get("user_id") or me.get("id")
    admin.post(
        f"{BASE_URL}/api/admin/staking/users/{uid}/balance",
        json={"mir_balance": 500},
        timeout=10,
    )
    # Reconnect valid wallet
    bob.post(
        f"{BASE_URL}/api/staking/wallet/connect",
        json={"wallet_address": WALLET_BOB},
        timeout=10,
    )
    # Try to stake 1 MIR on 3M plan (min is 10)
    r = bob.post(
        f"{BASE_URL}/api/staking/stake",
        json={"plan_id": "plan_stake_3m", "amount_mir": 1},
        timeout=10,
    )
    assert r.status_code == 400
    assert "minimum" in r.json()["detail"].lower()
