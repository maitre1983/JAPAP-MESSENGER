"""
iter230 RETEST — End-to-end P2P quiz challenge flow + wallet history validation.

Flow under test:
    Alice -> POST /quiz/champion/challenge/open  (paid, USD stake)
    Alice -> POST /quiz/champion/challenge/{cid}/play
    Alice -> POST /quiz/champion/challenge/{cid}/submit  (status -> challenger_played)
    Bob   -> POST /quiz/champion/challenge/{cid}/claim   (locks Bob stake, status -> challenger_played)
    Bob   -> POST /quiz/champion/challenge/{cid}/play
    Bob   -> POST /quiz/champion/challenge/{cid}/submit  (auto-resolves -> completed)
    Both  -> GET  /wallet/transactions  (verify lock + release/refund rows for {cid})
    Both  -> GET  /wallet/balance       (verify lock then settlement)
    Public-> GET  /quiz/champion/challenge/public/{cid}
    OG    -> GET  /og/challenge/{cid} with WhatsApp UA -> og:title/og:image/og:description
"""

from __future__ import annotations

import os
import time
import re
from decimal import Decimal

import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://japap-refactor.preview.emergentagent.com",
).rstrip("/")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **CAPTCHA}
BOB = {"email": "bob@japap.com", "password": "Test1234!", **CAPTCHA}


def _login(creds):
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    last = None
    for _ in range(3):
        try:
            r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                me = s.get(f"{BASE}/api/auth/me", timeout=30)
                if me.status_code == 200:
                    s._user = me.json()  # type: ignore[attr-defined]
                return s
            last = (r.status_code, r.text[:300])
        except Exception as e:
            last = ("exc", str(e))
        time.sleep(3)
    pytest.skip(f"Login failed for {creds['email']}: {last}")


@pytest.fixture(scope="module")
def alice():
    return _login(ALICE)


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


# ───────────── helpers
def _balance_decimal(sess) -> Decimal:
    r = sess.get(f"{BASE}/api/wallet/balance", timeout=30)
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    # Backend canonical USD balance is `balance_usd`; keep fallback for legacy.
    val = j.get("balance_usd") or j.get("balance") or "0"
    return Decimal(str(val))


def _all_transactions(sess, limit_pages: int = 5):
    """Collect up to limit_pages * 20 transactions for matching."""
    out = []
    for p in range(1, limit_pages + 1):
        r = sess.get(f"{BASE}/api/wallet/transactions?page={p}&limit=20", timeout=30)
        if r.status_code != 200:
            break
        j = r.json()
        out.extend(j.get("transactions") or [])
        if len(out) >= (j.get("total") or 0):
            break
    return out


# ───────────── E2E flow
@pytest.fixture(scope="module")
def e2e_state(alice, bob):
    """Drive the full P2P challenge to completion. Yields a dict of recorded facts."""
    state: dict = {}

    # 1) snapshot balances
    alice_balance_before = _balance_decimal(alice)
    bob_balance_before = _balance_decimal(bob)
    state["alice_balance_before"] = alice_balance_before
    state["bob_balance_before"] = bob_balance_before

    # 2) Alice opens a paid USD challenge.
    #    Backend pulls stake_currency from Alice's wallet currency (USD here)
    #    so we just send stake_amount (default minimum is 1 USD).
    payload = {"mode": "paid", "stake_amount": 1, "country_code": "CM"}
    r = alice.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=120)
    if r.status_code == 403 and "désactivé" in r.text:
        pytest.skip("Paid challenges disabled in admin settings.")
    assert r.status_code == 200, f"open failed: {r.status_code} {r.text[:300]}"
    j = r.json()
    cid = j["challenge_id"]
    state["cid"] = cid
    state["stake_currency"] = j["stake_currency"]
    state["stake_amount"] = Decimal(str(j["stake_amount"]))
    print(f"[E2E] cid={cid} stake={j['stake_amount']} {j['stake_currency']}")

    # 3) Alice's balance dropped by stake.
    alice_after_lock = _balance_decimal(alice)
    state["alice_after_lock"] = alice_after_lock
    drop = alice_balance_before - alice_after_lock
    # tolerate FX/USD canonical rounding within 0.01
    assert drop >= state["stake_amount"] - Decimal("0.01"), (
        f"Alice balance did not drop by stake. before={alice_balance_before} "
        f"after={alice_after_lock} stake={state['stake_amount']}"
    )

    # 4) Alice plays
    r = alice.post(f"{BASE}/api/quiz/champion/challenge/{cid}/play", timeout=60)
    assert r.status_code == 200, f"alice /play: {r.status_code} {r.text[:300]}"
    state["alice_play"] = r.json()
    # Alice submits zero-vector answers (any 5 ints; scoring is server-side).
    r = alice.post(
        f"{BASE}/api/quiz/champion/challenge/{cid}/submit",
        json={"answers": [0, 0, 0, 0, 0]},
        timeout=60,
    )
    assert r.status_code == 200, f"alice /submit: {r.status_code} {r.text[:300]}"
    j = r.json()
    state["alice_submit"] = j
    assert j["resolved"] is None, "Should not be resolved yet — Bob hasn't played."

    # 5) Status should now be challenger_played.
    r = alice.get(f"{BASE}/api/quiz/champion/challenges/{cid}", timeout=30)
    if r.status_code == 200:
        st = r.json().get("status")
        assert st in ("challenger_played", "awaiting_acceptor"), f"unexpected status {st}"

    # 6) Bob claims.
    r = bob.post(f"{BASE}/api/quiz/champion/challenge/{cid}/claim", timeout=60)
    assert r.status_code == 200, f"bob /claim: {r.status_code} {r.text[:300]}"

    bob_after_lock = _balance_decimal(bob)
    state["bob_after_lock"] = bob_after_lock
    bob_drop = bob_balance_before - bob_after_lock
    assert bob_drop >= state["stake_amount"] - Decimal("0.01"), (
        f"Bob balance did not drop by stake. before={bob_balance_before} "
        f"after={bob_after_lock} stake={state['stake_amount']}"
    )

    # 7) Bob plays.
    r = bob.post(f"{BASE}/api/quiz/champion/challenge/{cid}/play", timeout=60)
    assert r.status_code == 200, f"bob /play: {r.status_code} {r.text[:300]}"
    bob_play = r.json()
    state["bob_play"] = bob_play

    # 8) Bob submits — let's pick answers that should differ from Alice's (1 instead of 0).
    r = bob.post(
        f"{BASE}/api/quiz/champion/challenge/{cid}/submit",
        json={"answers": [1, 1, 1, 1, 1]},
        timeout=60,
    )
    assert r.status_code == 200, f"bob /submit: {r.status_code} {r.text[:300]}"
    j = r.json()
    state["bob_submit"] = j
    assert j["resolved"] is not None, "Challenge should auto-resolve after both submit."
    state["resolved"] = j["resolved"]

    # Final balances post-resolution
    state["alice_balance_after"] = _balance_decimal(alice)
    state["bob_balance_after"] = _balance_decimal(bob)

    return state


def test_e2e_challenge_completes_with_winner_or_tie(e2e_state):
    res = e2e_state["resolved"]
    assert "winner_user_id" in res or "challenger_score" in res
    assert "challenger_score" in res and "champion_score" in res
    print(
        f"[E2E] scores A={res['challenger_score']} B={res['champion_score']} "
        f"winner={res.get('winner_user_id')}"
    )


def test_e2e_alice_has_lock_transaction(alice, e2e_state):
    cid = e2e_state["cid"]
    txs = _all_transactions(alice, limit_pages=8)
    matching = [t for t in txs if t.get("type") == "quiz_challenge_lock"
                and (t.get("ref_id") == cid or cid in str(t))]
    assert matching, (
        f"Alice has no quiz_challenge_lock referencing {cid}. "
        f"Total tx scanned={len(txs)}."
    )
    print(f"[E2E] Alice lock row: {matching[0]}")


def test_e2e_bob_has_lock_transaction(bob, e2e_state):
    cid = e2e_state["cid"]
    txs = _all_transactions(bob, limit_pages=8)
    matching = [t for t in txs if t.get("type") == "quiz_challenge_lock"
                and (t.get("ref_id") == cid or cid in str(t))]
    assert matching, (
        f"Bob has no quiz_challenge_lock referencing {cid}. "
        f"Total tx scanned={len(txs)}."
    )
    print(f"[E2E] Bob lock row: {matching[0]}")


def test_e2e_release_or_refund_for_winner(alice, bob, e2e_state):
    cid = e2e_state["cid"]
    res = e2e_state["resolved"]
    winner = res.get("winner_user_id")

    # If tied → both should have a refund row.
    # If win → winner should have quiz_challenge_release.
    a_user = (alice._user.get("user_id") or alice._user.get("id"))  # type: ignore[attr-defined]
    b_user = (bob._user.get("user_id") or bob._user.get("id"))      # type: ignore[attr-defined]

    a_txs = _all_transactions(alice, limit_pages=8)
    b_txs = _all_transactions(bob, limit_pages=8)

    def _find(txs, types):
        return [t for t in txs
                if t.get("type") in types and (t.get("ref_id") == cid or cid in str(t))]

    if winner is None:
        a_ref = _find(a_txs, {"quiz_challenge_refund"})
        b_ref = _find(b_txs, {"quiz_challenge_refund"})
        assert a_ref, f"Tie but Alice has no refund for {cid}"
        assert b_ref, f"Tie but Bob has no refund for {cid}"
    else:
        if winner == a_user:
            release = _find(a_txs, {"quiz_challenge_release"})
            assert release, f"Alice won but no release row for {cid}"
        elif winner == b_user:
            release = _find(b_txs, {"quiz_challenge_release"})
            assert release, f"Bob won but no release row for {cid}"
        else:
            pytest.fail(f"Winner uid {winner} matches neither alice {a_user} nor bob {b_user}")


# ───────────── public landing + OG
def test_public_challenge_endpoint_no_auth(e2e_state):
    cid = e2e_state["cid"]
    r = requests.get(f"{BASE}/api/quiz/champion/challenge/public/{cid}", timeout=30)
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    assert j["challenge_id"] == cid
    assert "stake_amount" in j and "stake_currency" in j
    assert "challenger_name" in j
    print(f"[PUBLIC] {j}")


def test_og_challenge_html_for_whatsapp(e2e_state):
    cid = e2e_state["cid"]
    r = requests.get(
        f"{BASE}/api/og/challenge/{cid}",
        headers={"User-Agent": "WhatsApp/2.23.0"},
        timeout=30,
    )
    assert r.status_code == 200, r.text[:200]
    html = r.text
    # og:title / og:description / og:image
    assert re.search(r'property="og:title"', html), "missing og:title"
    assert re.search(r'property="og:description"', html), "missing og:description"
    assert re.search(r'property="og:image"', html), "missing og:image"
    # stake amount should be referenced in title or description
    stake_str = str(int(e2e_state["stake_amount"])) if e2e_state["stake_amount"] == int(e2e_state["stake_amount"]) else str(e2e_state["stake_amount"])
    assert (stake_str in html), f"stake {stake_str} not in OG html"


# ───────────── P1 wallet retest (the original Bob bug)
def test_p1_bob_wallet_returns_many_transactions(bob):
    """Ensures backend still returns Bob's full history regardless of UI race."""
    r = bob.get(f"{BASE}/api/wallet/transactions?page=1&limit=20", timeout=30)
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    assert isinstance(j.get("transactions"), list)
    assert (j.get("total") or 0) >= 20, f"Expected >=20 transactions for Bob, got {j.get('total')}"
    assert len(j["transactions"]) >= 20 or j["total"] < 20
    types = {t.get("type") for t in j["transactions"]}
    print(f"[P1] Bob total={j.get('total')} types_on_page1={types}")
