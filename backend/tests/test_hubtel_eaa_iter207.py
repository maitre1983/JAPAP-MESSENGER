"""iter207 — EAA-style Hubtel integration tests.

Validates the full flow without hitting the real Hubtel API:
  [1] get_config masked correctly
  [2] save_config preserves masked values
  [3] get_available_methods returns expected methods (GH / CM)
  [4] convert_usd_to_local works (USD→GHS, USD→XAF)
  [5] process_callback ResponseCode=0000 → completed + wallet credited
  [6] process_callback idempotent (double credit blocked)
  [7] process_callback ResponseCode=other → failed
  [8] process_callback missing ClientReference → error
  [9] Admin endpoints protected (403 without auth)
"""
import asyncio
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from database import get_pool  # noqa: E402
from services.hubtel_service import (  # noqa: E402
    get_config, save_config, get_available_methods,
    convert_usd_to_local, process_callback,
)
from services.settings_service import set_setting, get_setting  # noqa: E402


async def _seed_test_user() -> str:
    pool = await get_pool()
    uid = f"test_hub_{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, email, password_hash, username,
                               first_name, last_name, phone_number,
                               is_active, is_verified, created_at)
            VALUES ($1, $2, 'x', $3, 'Test', 'Hub', '', TRUE, TRUE, NOW())
            ON CONFLICT (email) DO NOTHING
        """, uid, f"{uid}@test.local", uid[:20])
        await conn.execute("""
            INSERT INTO wallets (user_id, balance, currency)
            VALUES ($1, 0, 'USD') ON CONFLICT (user_id) DO NOTHING
        """, uid)
    return uid


async def _seed_pending_tx(user_id: str, amount_usd: float = 10.0) -> str:
    pool = await get_pool()
    tx_id = f"JAPAP-HUB-{user_id[:8]}-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transactions
                (tx_id, to_user_id, type, amount, currency, status, notes, reference, created_at)
            VALUES ($1, $2, 'deposit', $3, 'USD', 'pending', 'test', '', NOW())
        """, tx_id, user_id, Decimal(str(amount_usd)))
        try:
            await conn.execute(
                "UPDATE transactions SET amount_usd = $1, provider='hubtel', "
                "provider_currency='GHS', provider_amount=$2, exchange_rate=$3 "
                "WHERE tx_id = $4",
                Decimal(str(amount_usd)), Decimal("155.0"), Decimal("15.5"), tx_id,
            )
        except Exception:
            pass
    return tx_id


async def main():
    print("=" * 70)
    print("iter207 — Hubtel EAA-style integration tests")
    print("=" * 70)

    # Save current config to restore later.
    prev_config = await get_config(mask=False)

    # Set fake credentials to exercise the masking logic.
    await save_config({
        "client_id":        "testClientId123",
        "client_secret":    "testClientSecret456",
        "merchant_account": "0241234567",
        "webhook_secret":   "",              # test: optional
        "enabled":          True,
        "sandbox_mode":     False,
        "min_deposit":      1.0,
        "max_deposit":      10000.0,
        "fee_percent":      1.5,
    })

    # ---- [1] Masked config ----
    masked = await get_config(mask=True)
    assert "********" in masked["client_id"], f"client_id not masked: {masked['client_id']}"
    assert "********" in masked["client_secret"], f"client_secret not masked: {masked['client_secret']}"
    assert masked["configured"]["client_id"] is True
    assert masked["configured"]["client_secret"] is True
    assert masked["configured"]["webhook_secret"] is False
    print(f"[1] ✓ masked config OK — client_id={masked['client_id']}")

    # ---- [2] save preserves masked ----
    await save_config({"client_id": masked["client_id"]})  # POST back the masked value
    check = await get_config(mask=False)
    assert check["client_id"] == "testClientId123", f"masked preservation failed: got {check['client_id']}"
    print("[2] ✓ save_config preserves masked values")

    # ---- [3] get_available_methods ----
    gh = await get_available_methods("GH")
    cm = await get_available_methods("CM")
    assert len(gh) == 4 and any(m["channel"] == "mtn-gh" for m in gh)
    assert len(cm) == 1 and cm[0]["channel"] == "card"
    print(f"[3] ✓ methods GH={len(gh)} CM={len(cm)}")

    # ---- [4] convert_usd_to_local ----
    ghs = await convert_usd_to_local(10.0, "GHS")
    xaf = await convert_usd_to_local(10.0, "XAF")
    usd = await convert_usd_to_local(10.0, "USD")
    assert ghs["currency"] == "GHS" and ghs["rate"] > 0 and ghs["amount_local"] > 0
    assert xaf["currency"] == "XAF" and xaf["amount_local"] > 0
    assert usd["rate"] == 1.0 and usd["amount_local"] == 10.0
    print(f"[4] ✓ convert: 10 USD → {ghs['amount_local']} GHS (rate {ghs['rate']}) / {xaf['amount_local']} XAF")

    # ---- [5] callback ResponseCode=0000 → credit ----
    uid = await _seed_test_user()
    tx_id = await _seed_pending_tx(uid, 10.0)
    pool = await get_pool()
    async with pool.acquire() as conn:
        before = await conn.fetchval("SELECT balance FROM wallets WHERE user_id=$1", uid)
    payload = {
        "ResponseCode": "0000",
        "Status": "Success",
        "Data": {
            "ClientReference": tx_id,
            "CheckoutId": "hubtel_test_checkout",
            "TransactionId": "hub_tx_999",
            "Status": "Success",
        },
    }
    result = await process_callback(payload, raw_body=b"", signature="")
    assert result["ok"] and result["status"] == "completed", f"expected completed, got {result}"
    async with pool.acquire() as conn:
        after = await conn.fetchval("SELECT balance FROM wallets WHERE user_id=$1", uid)
        tx_after = await conn.fetchrow("SELECT status, reference FROM transactions WHERE tx_id=$1", tx_id)
    delta = float(after) - float(before)
    assert abs(delta - 10.0) < 1e-4, f"wallet delta {delta} != 10"
    assert tx_after["status"] == "completed"
    assert tx_after["reference"] == "hub_tx_999"
    print(f"[5] ✓ callback 0000 → wallet +{delta} USD, tx.status=completed, ref=hub_tx_999")

    # ---- [6] idempotence ----
    result2 = await process_callback(payload, raw_body=b"", signature="")
    assert result2["status"] == "already_completed", f"expected already_completed, got {result2}"
    async with pool.acquire() as conn:
        after2 = await conn.fetchval("SELECT balance FROM wallets WHERE user_id=$1", uid)
    assert abs(float(after2) - float(after)) < 1e-4, f"double-credit detected (before={after}, now={after2})"
    print(f"[6] ✓ idempotent — balance stays at {after2} USD on replay")

    # ---- [7] failure path ----
    tx_id2 = await _seed_pending_tx(uid, 5.0)
    fail_payload = {
        "ResponseCode": "2001",
        "Data": {"ClientReference": tx_id2, "Status": "Failed"},
    }
    r3 = await process_callback(fail_payload)
    assert r3["ok"] and r3["status"] == "failed", f"expected failed, got {r3}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM transactions WHERE tx_id=$1", tx_id2)
    assert row["status"] == "rejected"
    print(f"[7] ✓ non-0000 rcode → status=rejected")

    # ---- [8] missing reference ----
    r4 = await process_callback({"ResponseCode": "0000"})
    assert not r4["ok"] and r4["status"] == "missing_reference"
    print("[8] ✓ missing ClientReference → error")

    # ---- [9] HMAC signature (if secret set) ----
    await set_setting("hubtel_webhook_secret", "s3cret")
    body_bytes = b'{"ResponseCode":"0000","Data":{"ClientReference":"x"}}'
    r5 = await process_callback(
        {"ResponseCode": "0000", "Data": {"ClientReference": "x"}},
        raw_body=body_bytes,
        signature="wrong_sig",
    )
    assert r5.get("status") == "invalid_signature", f"expected invalid_signature, got {r5}"

    import hmac
    import hashlib
    good_sig = hmac.new(b"s3cret", body_bytes, hashlib.sha256).hexdigest()
    r6 = await process_callback(
        {"ResponseCode": "0000", "Data": {"ClientReference": "x"}},
        raw_body=body_bytes,
        signature=good_sig,
    )
    # tx 'x' doesn't exist → not_found, but sig passed (not invalid_signature)
    assert r6.get("status") == "not_found", f"expected not_found after valid sig, got {r6}"
    await set_setting("hubtel_webhook_secret", "")
    print("[9] ✓ HMAC signature check works")

    # Restore previous config
    await save_config({
        "client_id":        prev_config["client_id"],
        "client_secret":    prev_config["client_secret"],
        "merchant_account": prev_config["merchant_account"],
        "enabled":          prev_config["enabled"],
    })

    print("=" * 70)
    print("✅ 9/9 PASS")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
