"""iter237k — Backend regression tests for Daily Challenge PAID mode.

Coverage:
  - 5/5 pays +50% (server-controlled barème, wallet credit correct)
  - 0/5 deducts -85% (refund clamped, wallet final correct)
  - Second start same day → HTTP 409
  - Stake below min → HTTP 400
  - Stake above max → HTTP 400
  - Insufficient balance → HTTP 402
  - Admin toggle off → user start 403
  - Reveal endpoint returns correct_idx + is_correct
  - Free mode endpoint /status still works (regression)

Run via direct importable functions (pytest plugin auto-discovery is broken
in this environment due to web3 — see iter237j helper).
"""
from __future__ import annotations

import os
import json
import asyncio
import urllib.request
import urllib.error

API = os.environ.get("API_BASE", "http://0.0.0.0:8001")
ALICE = ("alice@japap.com", "Alice2026!")
ADMIN = ("admin@japap.com", "JapapAdmin2024!")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}


def _http(method: str, path: str, *, token: str | None = None, json_body=None):
    url = f"{API}{path}"
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def _login(email: str, pwd: str):
    code, data = _http("POST", "/api/auth/login",
                        json_body={"email": email, "password": pwd, **CAPTCHA})
    assert code == 200, f"login failed {code}: {data}"
    return data["access_token"], data["user"]["user_id"]


async def _reset_alice(uid: str, balance: float = 100.0) -> None:
    import asyncpg, os
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    db_url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            "INSERT INTO wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = $2",
            uid, balance,
        )
        await conn.execute(
            "DELETE FROM daily_challenge_paid_sessions "
            "WHERE user_id = $1 AND date_played = CURRENT_DATE",
            uid,
        )
    finally:
        await conn.close()


async def _balance(uid: str) -> float:
    import asyncpg, os
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        return float(await conn.fetchval(
            "SELECT balance FROM wallets WHERE user_id=$1", uid) or 0)
    finally:
        await conn.close()


async def _correct(qids: list[int]) -> list[int]:
    import asyncpg, os
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        rows = await conn.fetch(
            "SELECT id, correct_idx FROM daily_challenge_expert_pool "
            "WHERE id = ANY($1::bigint[])", qids,
        )
        by_id = {int(r["id"]): int(r["correct_idx"]) for r in rows}
        return [by_id[q] for q in qids]
    finally:
        await conn.close()


# ── Tests ─────────────────────────────────────────────────────────────────


def test_5_5_wins_50pct():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, start = _http("POST", "/api/quiz/daily-challenge/paid/start",
                          token=tok, json_body={"stake_usd": 4.0})
    assert code == 200, start
    qids = [q["id"] for q in start["questions"]]
    answers = asyncio.run(_correct(qids))
    code, sub = _http("POST", "/api/quiz/daily-challenge/paid/submit",
                       token=tok, json_body={
                           "session_id": start["session_id"], "answers": answers,
                       })
    assert code == 200, sub
    assert sub["score"] == 5
    assert abs(sub["result_pct"] - 50.0) < 0.01
    assert abs(sub["amount_delta_usd"] - 2.0) < 0.01  # +50% of 4.0
    bal = asyncio.run(_balance(uid))
    # 100 - 4 (debit) + 6 (refund stake+win) = 102
    assert abs(bal - 102.0) < 0.01, bal


def test_0_5_loses_85pct():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, start = _http("POST", "/api/quiz/daily-challenge/paid/start",
                          token=tok, json_body={"stake_usd": 10.0})
    assert code == 200, start
    qids = [q["id"] for q in start["questions"]]
    correct = asyncio.run(_correct(qids))
    wrong = [(c + 1) % 4 for c in correct]
    code, sub = _http("POST", "/api/quiz/daily-challenge/paid/submit",
                       token=tok, json_body={
                           "session_id": start["session_id"], "answers": wrong,
                       })
    assert code == 200, sub
    assert sub["score"] == 0
    assert abs(sub["result_pct"] + 85.0) < 0.01
    assert abs(sub["amount_delta_usd"] + 8.5) < 0.01
    bal = asyncio.run(_balance(uid))
    # 100 - 10 (debit) + 1.5 (refund clamped, stake+delta = 10-8.5 = 1.5) = 91.5
    assert abs(bal - 91.5) < 0.01, bal


def test_second_attempt_same_day_409():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, _ = _http("POST", "/api/quiz/daily-challenge/paid/start",
                     token=tok, json_body={"stake_usd": 1.0})
    assert code == 200
    code2, _ = _http("POST", "/api/quiz/daily-challenge/paid/start",
                      token=tok, json_body={"stake_usd": 1.0})
    assert code2 == 409


def test_stake_below_min_400():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, _ = _http("POST", "/api/quiz/daily-challenge/paid/start",
                     token=tok, json_body={"stake_usd": 0.05})
    assert code == 400


def test_stake_above_max_400():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, _ = _http("POST", "/api/quiz/daily-challenge/paid/start",
                     token=tok, json_body={"stake_usd": 5000})
    assert code == 400


def test_insufficient_balance_402():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 0.5))
    code, _ = _http("POST", "/api/quiz/daily-challenge/paid/start",
                     token=tok, json_body={"stake_usd": 10.0})
    assert code == 402


def test_admin_disable_blocks_user_403():
    tok_admin, _ = _login(*ADMIN)
    tok_user, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, _ = _http(
        "PUT", "/api/admin/daily-challenge/paid/config",
        token=tok_admin, json_body={"DCQ_PAID_ENABLED": False},
    )
    assert code == 200
    try:
        code, _ = _http(
            "POST", "/api/quiz/daily-challenge/paid/start",
            token=tok_user, json_body={"stake_usd": 1.0},
        )
        assert code == 403
    finally:
        # Always re-enable to keep the env in a usable state
        _http("PUT", "/api/admin/daily-challenge/paid/config",
              token=tok_admin, json_body={"DCQ_PAID_ENABLED": True})


def test_reveal_returns_correct_idx():
    tok, uid = _login(*ALICE)
    asyncio.run(_reset_alice(uid, 100.0))
    code, start = _http("POST", "/api/quiz/daily-challenge/paid/start",
                          token=tok, json_body={"stake_usd": 1.0})
    assert code == 200
    qid = start["questions"][0]["id"]
    code, rev = _http("POST", "/api/quiz/daily-challenge/paid/reveal",
                       token=tok, json_body={
                           "session_id": start["session_id"],
                           "question_id": qid, "user_answer": 0,
                       })
    assert code == 200, rev
    assert rev["question_id"] == qid
    assert 0 <= rev["correct_idx"] <= 3
    assert isinstance(rev["is_correct"], bool)


def test_free_mode_still_works():
    """Regression: free daily challenge endpoint must remain unchanged."""
    tok, _ = _login(*ALICE)
    code, status = _http("GET", "/api/quiz/daily-challenge/status", token=tok)
    assert code == 200
    assert "available" in status
    assert "enabled" in status


# ── iter237l — Profile-completion redemption (anti-tilt +5%) ─────────────


async def _set_profile(uid: str, *, complete: bool) -> None:
    """Toggle Alice's profile fields to simulate complete/incomplete state."""
    import asyncpg, os
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        if complete:
            await conn.execute(
                "UPDATE users SET avatar=$1, about=$2, birthday=$3, gender=$4, "
                "country_code=$5, phone_number=$6, paid_redemption_unlocked_at=NULL, "
                "paid_redemption_used_at=NULL WHERE user_id=$7",
                "/uploads/files/test_avatar.jpg",
                "Bonjour je suis Alice et j adore les quiz dexpert pour gagner gros",
                "1995-03-15", "F", "CM", "+237600000000", uid,
            )
        else:
            await conn.execute(
                "UPDATE users SET avatar=NULL, about=NULL, "
                "paid_redemption_unlocked_at=NULL, paid_redemption_used_at=NULL "
                "WHERE user_id=$1",
                uid,
            )
    finally:
        await conn.close()


def test_redemption_unlocked_when_profile_complete():
    tok, uid = _login(*ALICE)
    asyncio.run(_set_profile(uid, complete=True))
    code, status = _http("GET", "/api/quiz/daily-challenge/paid/redemption",
                          token=tok)
    assert code == 200, status
    assert status["profile_complete"] is True
    assert status["available"] is True
    assert status["unlocked_at"] is not None
    assert status["used_at"] is None
    assert abs(status["bonus_pct"] - 5.0) < 0.01


def test_redemption_blocked_when_profile_incomplete():
    tok, uid = _login(*ALICE)
    asyncio.run(_set_profile(uid, complete=False))
    code, status = _http("GET", "/api/quiz/daily-challenge/paid/redemption",
                          token=tok)
    assert code == 200, status
    assert status["profile_complete"] is False
    assert status["available"] is False
    assert "avatar" in status["missing"]
    assert "about" in status["missing"]


def test_use_bonus_rejected_when_not_eligible():
    tok, uid = _login(*ALICE)
    asyncio.run(_set_profile(uid, complete=False))
    asyncio.run(_reset_alice(uid, 100.0))
    code, body = _http("POST", "/api/quiz/daily-challenge/paid/start",
                        token=tok, json_body={"stake_usd": 1.0,
                                                "use_bonus": True})
    assert code == 403, body


def test_bonus_reduces_loss_and_is_consumed():
    """Full anti-tilt flow: complete profile, request bonus on a losing run,
    expect -85% to become -80% AND used_at to be set."""
    tok, uid = _login(*ALICE)
    asyncio.run(_set_profile(uid, complete=True))
    asyncio.run(_reset_alice(uid, 100.0))
    # Start with use_bonus=true
    code, start = _http("POST", "/api/quiz/daily-challenge/paid/start",
                          token=tok, json_body={"stake_usd": 10.0,
                                                  "use_bonus": True})
    assert code == 200, start
    assert start["bonus_active"] is True
    qids = [q["id"] for q in start["questions"]]
    correct = asyncio.run(_correct(qids))
    wrong = [(c + 1) % 4 for c in correct]
    code, sub = _http("POST", "/api/quiz/daily-challenge/paid/submit",
                       token=tok, json_body={
                           "session_id": start["session_id"], "answers": wrong,
                       })
    assert code == 200, sub
    assert sub["score"] == 0
    # -85% baseline + 5pp bonus = -80%
    assert abs(sub["result_pct"] + 80.0) < 0.01, sub
    assert abs(sub["amount_delta_usd"] + 8.0) < 0.01
    assert sub["bonus_active"] is True
    assert abs(sub["bonus_applied_pct"] - 5.0) < 0.01
    assert sub["bonus_consumed"] is True
    # /redemption should now flag used_at
    code, status = _http("GET", "/api/quiz/daily-challenge/paid/redemption",
                          token=tok)
    assert code == 200
    assert status["used_at"] is not None
    assert status["available"] is False


def test_bonus_not_consumed_on_win():
    """If the user wins 5/5 with bonus_active, the bonus must NOT be consumed
    (anti-tilt is loss-only). Ensure used_at remains null."""
    tok, uid = _login(*ALICE)
    # Reset profile + clear redemption used_at
    asyncio.run(_set_profile(uid, complete=True))
    asyncio.run(_reset_alice(uid, 100.0))
    code, start = _http("POST", "/api/quiz/daily-challenge/paid/start",
                          token=tok, json_body={"stake_usd": 4.0,
                                                  "use_bonus": True})
    assert code == 200, start
    qids = [q["id"] for q in start["questions"]]
    correct = asyncio.run(_correct(qids))
    code, sub = _http("POST", "/api/quiz/daily-challenge/paid/submit",
                       token=tok, json_body={
                           "session_id": start["session_id"], "answers": correct,
                       })
    assert code == 200, sub
    assert sub["score"] == 5
    assert sub["bonus_active"] is True
    assert abs(sub["bonus_applied_pct"]) < 0.01  # bonus didn't apply on win
    assert sub["bonus_consumed"] is False
    code, status = _http("GET", "/api/quiz/daily-challenge/paid/redemption",
                          token=tok)
    assert status["used_at"] is None
    assert status["available"] is True


if __name__ == "__main__":
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(0 if failed == 0 else 1)
