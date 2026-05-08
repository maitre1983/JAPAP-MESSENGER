"""iter237o re-test — backend stability after asyncpg pool auto-recreate fix.

Validates:
- /api/health returns 200 in <3s on 10 consecutive calls spaced 30s apart
- Alice login + /api/legal/status with captcha bypass
- 4 legal endpoints (status, accept-cgu, accept-cgje, accept-privacy) still work
"""
from __future__ import annotations

import os
import time
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break
BYPASS = "JAPAP_E2E_BYPASS_2026"


def _csrf(s: requests.Session) -> dict:
    tok = s.cookies.get("csrf_token") or ""
    return {"X-CSRF-Token": tok} if tok else {}


def test_health_stability_10x_30s():
    """10 consecutive /api/health calls spaced 30s apart, all must be 200 in <3s."""
    results = []
    for i in range(10):
        t0 = time.time()
        try:
            r = requests.get(f"{BASE_URL}/api/health", timeout=5)
            dt = time.time() - t0
            results.append((i + 1, r.status_code, round(dt, 3)))
            print(f"  [{i+1}/10] status={r.status_code} latency={dt:.3f}s")
            assert r.status_code == 200, f"call #{i+1}: HTTP {r.status_code}"
            assert dt < 3.0, f"call #{i+1}: latency {dt:.2f}s exceeds 3s"
        except Exception as e:
            dt = time.time() - t0
            results.append((i + 1, "ERR", round(dt, 3)))
            print(f"  [{i+1}/10] ERROR after {dt:.2f}s: {e}")
            raise
        if i < 9:
            time.sleep(30)
    print(f"\nAll 10 calls succeeded. Results: {results}")


def test_alice_login_and_legal_status():
    s = requests.Session()
    s.get(f"{BASE_URL}/api/health", timeout=10)
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={
            "email": "alice@japap.com",
            "password": "Alice2026!",
            "captcha_id": BYPASS,
            "captcha_answer": "0",
        },
        headers=_csrf(s),
        timeout=15,
    )
    assert r.status_code == 200, f"Alice login failed: {r.status_code} {r.text[:200]}"
    print(f"  Alice login: OK")

    # /api/legal/status
    rs = s.get(f"{BASE_URL}/api/legal/status", timeout=10)
    assert rs.status_code == 200, rs.text
    j = rs.json()
    for k in ("cgu_accepted_at", "cgje_accepted_at", "privacy_accepted_at"):
        assert k in j, f"Missing field {k}"
    print(f"  /api/legal/status payload keys: {list(j.keys())}")

    h = _csrf(s)
    for ep in ("accept-cgu", "accept-cgje", "accept-privacy"):
        rr = s.post(f"{BASE_URL}/api/legal/{ep}", headers=h, timeout=10)
        assert rr.status_code == 200, f"{ep} -> {rr.status_code}: {rr.text[:200]}"
        body = rr.json()
        assert body.get("accepted_at"), f"{ep} missing accepted_at"
        print(f"  POST /api/legal/{ep}: OK accepted_at={body.get('accepted_at')}")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
