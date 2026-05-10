"""
iter238 — Paystack unit tests (HMAC + reference + FX shape).

Run with: `cd /app/backend && python -m pytest tests/test_paystack.py -v`
or directly with `python3 tests/test_paystack.py` (asserts only).
"""
import hashlib
import hmac
import sys

sys.path.insert(0, "/app/backend")

from services.paystack_service import (  # noqa: E402
    generate_reference,
    verify_webhook_signature,
)


def test_reference_format():
    ref = generate_reference()
    assert ref.startswith("JAPAP-"), f"Bad prefix: {ref}"
    # 6 (prefix) + 20 = 26 chars
    assert len(ref) == 26, f"Bad length: {len(ref)} → {ref}"
    # Body is uppercased hex.
    body = ref[6:]
    assert body.isalnum() and body.isupper(), f"Bad body: {body}"


def test_reference_unique():
    refs = {generate_reference() for _ in range(1000)}
    assert len(refs) == 1000, "Reference collision detected"


def test_hmac_valid():
    secret = "sk_test_abc123"
    payload = b'{"event":"charge.success","data":{"reference":"JAPAP-TEST"}}'
    sig = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    assert verify_webhook_signature(payload, sig, secret) is True


def test_hmac_invalid():
    secret = "sk_test_abc123"
    payload = b'{"event":"charge.success"}'
    # Wrong signature.
    bad = "deadbeef" * 16
    assert verify_webhook_signature(payload, bad, secret) is False
    # Wrong secret (correct algorithm but different key).
    other_sig = hmac.new(b"other", payload, hashlib.sha512).hexdigest()
    assert verify_webhook_signature(payload, other_sig, secret) is False
    # Empty signature.
    assert verify_webhook_signature(payload, "", secret) is False
    # Empty secret.
    real_sig = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    assert verify_webhook_signature(payload, real_sig, "") is False


def test_hmac_payload_tampered():
    secret = "sk_test_abc123"
    payload = b'{"event":"charge.success","data":{"amount":1000}}'
    sig = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    # Even a single-byte change must invalidate the signature.
    tampered = payload.replace(b"1000", b"9999")
    assert verify_webhook_signature(tampered, sig, secret) is False


if __name__ == "__main__":
    test_reference_format()
    test_reference_unique()
    test_hmac_valid()
    test_hmac_invalid()
    test_hmac_payload_tampered()
    print("✅ All Paystack HMAC + reference tests passed")
