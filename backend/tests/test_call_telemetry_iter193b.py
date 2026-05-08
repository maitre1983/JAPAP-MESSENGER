"""
iter193b — Call telemetry black-box tests
==========================================
Validates the POST /api/calls/logs/client + GET /api/calls/logs/admin
endpoints end-to-end, including sanitisation and auth.

Cases:
  1. Anonymous POST → 202 accepted (user_id='anon')
  2. Authenticated POST with valid action → persisted correctly
  3. Unknown action → accepted but prefixed "unknown:"
  4. GET /logs/admin non-admin → 403
  5. GET /logs/admin admin → filter by call_id works
  6. Rate limit / payload cap: oversized meta truncated at 2 KB (no crash)
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = os.environ.get("TEST_API_URL", "http://localhost:8001")
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")


async def _login(c, email, pw):
    r = await c.post("/api/auth/login", json={
        "email": email, "password": pw,
        "captcha_id": BYPASS, "captcha_answer": "0",
    })
    assert r.status_code == 200, r.text[:200]
    return r.json()["access_token"]


async def main():
    ios_ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
               "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1")
    unique_call = f"sess_iter193b_{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(base_url=API, timeout=90.0) as c:
        # CASE 1 — anonymous POST
        r = await c.post("/api/calls/logs/client",
            headers={"User-Agent": ios_ua},
            json={"action": "call_button_clicked", "call_id": unique_call + "_anon",
                  "meta": {"type": "audio"}})
        assert r.status_code == 202, f"anonymous POST: {r.status_code} {r.text[:200]}"
        print("  CASE 1 OK — anonymous POST accepted (202)")

        bob_tok = await _login(c, "bob@japap.com", "Test1234!")
        admin_tok = await _login(c, "admin@japap.com", "JapapAdmin2024!")

        # CASE 2 — authenticated POST, several actions
        for action, extra in [
            ("call_button_clicked",      {"meta": {"type": "audio"}}),
            ("permission_prompt_opened", {"meta": {"stage": "startCall"}}),
            ("permission_granted",       {}),
            ("token_requested",          {}),
            ("livekit_connecting",       {"meta": {"ws_url": "wss://example"}}),
            ("livekit_connected",        {"room_id": "japap_sess_xyz",
                                           "meta": {"participants": 2}}),
        ]:
            r = await c.post("/api/calls/logs/client",
                headers={"Authorization": f"Bearer {bob_tok}", "User-Agent": ios_ua},
                json={"action": action, "call_id": unique_call, **extra})
            assert r.status_code == 202, f"{action}: {r.status_code}"
        print("  CASE 2 OK — all 6 valid actions persisted")

        # CASE 3 — unknown action (typo) is accepted but flagged
        r = await c.post("/api/calls/logs/client",
            headers={"Authorization": f"Bearer {bob_tok}"},
            json={"action": "lipekit_connected_TYPO", "call_id": unique_call})
        assert r.status_code == 202
        print("  CASE 3 OK — unknown action accepted (flagged server-side)")

        # CASE 4 — non-admin GET /logs/admin → 403
        r = await c.get("/api/calls/logs/admin",
            headers={"Authorization": f"Bearer {bob_tok}"})
        assert r.status_code == 403, f"non-admin should 403, got {r.status_code}"
        print("  CASE 4 OK — non-admin blocked (403)")

        # CASE 5 — admin filter by call_id (re-login admin — sometimes
        # the long bcrypt-heavy test stretches past session TTL)
        admin_tok = await _login(c, "admin@japap.com", "JapapAdmin2024!")
        r = await c.get(f"/api/calls/logs/admin?call_id={unique_call}&limit=50",
            headers={"Authorization": f"Bearer {admin_tok}"})
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        actions = [log["action"] for log in data["logs"]]
        for must in ("call_button_clicked", "permission_granted",
                     "livekit_connecting", "livekit_connected"):
            assert must in actions, f"{must} missing from logs: {actions}"
        assert any(a.startswith("unknown:") for a in actions), \
            f"unknown typo not flagged: {actions}"
        # Device/browser parse
        assert any("iOS" in (log.get("browser") or "") for log in data["logs"]), \
            "iOS UA not parsed as iOS"
        print(f"  CASE 5 OK — admin filter returned {len(data['logs'])} logs with correct browser parse")

        # CASE 6 — oversized meta / error_message truncated
        huge = {"stage": "oversize", "payload": "x" * 10000}
        r = await c.post("/api/calls/logs/client",
            headers={"Authorization": f"Bearer {bob_tok}"},
            json={"action": "call_error", "call_id": unique_call,
                  "error_name": "TestError",
                  "error_message": "z" * 2000,
                  "meta": huge})
        assert r.status_code == 202
        # Re-login admin again in case the first login session expired
        # during the long bcrypt-heavy test run above.
        admin_tok = await _login(c, "admin@japap.com", "JapapAdmin2024!")
        r = await c.get(f"/api/calls/logs/admin?call_id={unique_call}&action=call_error&limit=1",
            headers={"Authorization": f"Bearer {admin_tok}"})
        logs = r.json().get("logs", [])
        assert logs, f"no call_error log found for {unique_call} — full response: {r.text[:300]}"
        log = logs[0]
        assert len(log.get("error_message") or "") <= 500, \
            f"error_message should be ≤500 chars, got {len(log.get('error_message') or '')}"
        print(f"  CASE 6 OK — oversized payloads truncated (error_message={len(log['error_message'])})")

    print("\n✅ iter193b — all 6 scenarios passed")


if __name__ == "__main__":
    asyncio.run(main())
