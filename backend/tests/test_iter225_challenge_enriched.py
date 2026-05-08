"""iter225 — Tests for the enriched GET /api/quiz/champion/challenges/{cid}.

Verifies the response now contains:
  • commission_pct (so the player B can compute his net winnings)
  • viewer_balance + viewer_currency (USD canonical balance check)
  • challenger / champion sub-objects (name + avatar_url)

These are unit tests against the function logic; full E2E lives in the
fork testing agent run.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.asyncio
async def test_enriched_challenge_response_contract(monkeypatch):
    """The /challenges/{cid} payload must include commission_pct,
    viewer_balance, viewer_currency and player metadata."""
    from routes import quiz_champion as qc
    from datetime import datetime, timezone, timedelta

    fake_user = {"user_id": "u_champ", "role": "user"}
    monkeypatch.setattr(qc, "get_current_user", lambda r: _async_return(fake_user))

    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def execute(self, *a, **kw): return None

        async def fetchrow(self, query, *args):
            if "FROM quiz_champion_challenges" in query:
                return {
                    "challenge_id":         "qcc_test",
                    "challenger_user_id":   "u_chal",
                    "champion_user_id":     "u_champ",
                    "country_code":         "CM",
                    "session_id":           1,
                    "mode":                 "paid",
                    "stake_amount":         50,
                    "stake_currency":       "USD",
                    "status":               "pending",
                    "challenger_score":     None,
                    "champion_score":       None,
                    "winner_user_id":       None,
                    "challenger_run_id":    None,
                    "champion_run_id":      None,
                    "created_at":           datetime.now(timezone.utc),
                    "expires_at":           datetime.now(timezone.utc) + timedelta(hours=24),
                    "completed_at":         None,
                }
            if "FROM users" in query:
                uid = args[0]
                return {
                    "user_id":     uid,
                    "first_name":  "Bob" if uid == "u_chal" else "Daf",
                    "last_name":   None,
                    "username":    f"user_{uid}",
                    "avatar":      "https://cdn.japapmessenger.com/avatar.png",
                }
            if "FROM wallets" in query:
                return {"balance": 81000.0, "currency": "USD"}
            return None

    class _Pool:
        def acquire(self): return _Conn()

    async def fake_pool():
        return _Pool()

    monkeypatch.setattr(qc, "get_pool", fake_pool)
    async def fake_ddl(c): return None
    monkeypatch.setattr(qc, "ensure_ddl", fake_ddl)
    async def fake_cfg():
        return {"quiz_challenge_commission_pct": 15}
    monkeypatch.setattr(qc, "get_quiz_config", fake_cfg)

    payload = await qc.get_challenge("qcc_test", request=None)

    # --- Contract assertions
    assert payload["challenge_id"] == "qcc_test"
    assert payload["role"] == "champion"
    assert payload["stake_amount"] == 50.0
    assert payload["stake_currency"] == "USD"
    assert payload["commission_pct"] == 15
    assert payload["viewer_balance"] == 81000.0
    assert payload["viewer_currency"] == "USD"
    assert payload["challenger"]["name"] == "Bob"
    assert payload["champion"]["name"] == "Daf"
    assert "avatar_url" in payload["challenger"]


def _async_return(value):
    async def _inner(*a, **kw):
        return value
    return _inner()


# Note: get_quiz_config is awaitable in real code; the monkeypatch above wraps
# it as a coroutine. We assert by calling .get_challenge() directly, which is
# already async.
