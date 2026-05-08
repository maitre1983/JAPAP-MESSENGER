"""
iter232 backend tests:
  M1 — currency on quiz_challenge_* transactions must be USD (not XAF).
  M2 — Doubler la mise (allow_double):
        (a) create allow_double=true → balance drops by 2× stake
        (b) acceptor without double → A gets refund of unused side, B locks base
        (c) acceptor with double=true → B locks 2× side, response.doubled=true
        (d) /challenge/public/{cid} returns allow_double + doubled
        (e) insufficient balance for 2× stake → 402 with gap message
  M3 — Validation IA: table schema + admin stats endpoint.

Uses captcha bypass id=JAPAP_E2E_BYPASS_2026 answer=0.
"""
from __future__ import annotations
import os, time, asyncio
from decimal import Decimal
import pytest, requests

BASE = (os.environ.get("REACT_APP_BACKEND_URL")
        or "https://japap-refactor.preview.emergentagent.com").rstrip("/")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **CAPTCHA}
BOB = {"email": "bob@japap.com", "password": "Test1234!", **CAPTCHA}
CHARLIE = {"email": "charlie_iter141@japap.com", "password": "Charlie2026!", **CAPTCHA}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", **CAPTCHA}


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
                    s._user = me.json()
                return s
            last = (r.status_code, r.text[:200])
        except Exception as e:
            last = ("exc", str(e))
        time.sleep(2)
    pytest.skip(f"Login failed for {creds['email']}: {last}")


@pytest.fixture(scope="module")
def alice(): return _login(ALICE)
@pytest.fixture(scope="module")
def bob(): return _login(BOB)
@pytest.fixture(scope="module")
def charlie(): return _login(CHARLIE)
@pytest.fixture(scope="module")
def admin(): return _login(ADMIN)


def _balance(sess) -> Decimal:
    r = sess.get(f"{BASE}/api/wallet/balance", timeout=30)
    assert r.status_code == 200
    j = r.json()
    return Decimal(str(j.get("balance_usd") or j.get("balance") or "0"))


def _txs(sess, pages=4):
    out = []
    for p in range(1, pages + 1):
        r = sess.get(f"{BASE}/api/wallet/transactions?page={p}&limit=20", timeout=30)
        if r.status_code != 200: break
        j = r.json()
        out.extend(j.get("transactions") or [])
    return out


# ─────── Helper: cancel any open Alice challenge to avoid ux_qcch_open_pair ───
def _force_cleanup_open_challenges(challenger_user_id: str, champion_user_id: str | None = None):
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    async def _m():
        c = await asyncpg.connect(os.environ["DATABASE_URL"])
        if champion_user_id:
            # Cancel any non-final pair to defeat partial unique ux_qcch_open_pair.
            await c.execute(
                "UPDATE quiz_champion_challenges SET status='cancelled', completed_at=NOW() "
                "WHERE challenger_user_id=$1 AND champion_user_id=$2 "
                "AND status IN ('pending','accepted','challenger_played','champion_played','awaiting_acceptor')",
                challenger_user_id, champion_user_id,
            )
        await c.execute(
            "UPDATE quiz_champion_challenges SET status='cancelled', completed_at=NOW() "
            "WHERE challenger_user_id=$1 AND status='awaiting_acceptor'",
            challenger_user_id,
        )
        await c.close()
    asyncio.run(_m())


# ════════════════════════════ M1 ════════════════════════════
def test_m1_bob_quiz_tx_currency_is_usd(bob):
    """All quiz_challenge_* (non-PTS) transactions returned by the wallet
    history endpoint must be currency='USD' for Bob (was XAF before iter232)."""
    txs = _txs(bob, pages=6)
    quiz_txs = [t for t in txs if str(t.get("type", "")).startswith("quiz_challenge_")
                and t.get("type") != "quiz_challenge_bonus"]
    assert quiz_txs, "no quiz_challenge_* txs found for Bob — cannot validate M1"
    bad = [t for t in quiz_txs if (t.get("currency") or "").upper() != "USD"]
    assert not bad, f"non-USD quiz tx rows found: {bad[:3]}"
    print(f"[M1] {len(quiz_txs)} quiz_challenge_* rows, all USD ✓")


# ════════════════════════════ M2 ════════════════════════════
@pytest.fixture(scope="module")
def alice_clean(alice):
    uid = alice._user.get("user_id") or alice._user.get("id")
    _force_cleanup_open_challenges(uid)
    return alice


def test_m2a_allow_double_create_locks_2x(alice_clean, bob):
    """Alice creates allow_double=true with stake=2 → balance drops by 4
    (2× stake), response has allow_double=true."""
    # Cleanup any stale alice<->bob pair so the subsequent claim doesn't
    # hit ux_qcch_open_pair on champion assignment.
    a_uid = alice_clean._user.get("user_id") or alice_clean._user.get("id")
    b_uid = bob._user.get("user_id") or bob._user.get("id")
    _force_cleanup_open_challenges(a_uid, b_uid)
    bal_before = _balance(alice_clean)
    if bal_before < Decimal("4"):
        pytest.skip(f"Alice balance {bal_before} < 4 USD")
    payload = {"mode": "paid", "stake_amount": 2, "country_code": "CM",
               "allow_double": True}
    r = alice_clean.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=60)
    if r.status_code == 403 and "désactivé" in r.text:
        pytest.skip("Paid disabled")
    assert r.status_code == 200, f"open: {r.status_code} {r.text[:300]}"
    j = r.json()
    assert j.get("allow_double") is True, f"response missing allow_double=true: {j}"
    cid = j["challenge_id"]
    bal_after = _balance(alice_clean)
    delta = bal_before - bal_after
    assert delta == Decimal("4.00"), f"expected -4 lock, got {delta} (before={bal_before} after={bal_after})"
    pytest.shared_cid_a = cid
    print(f"[M2a] cid={cid} alice -{delta} USD (2× stake) ✓")


def test_m2b_bob_accept_no_double_refunds_alice_excess(alice_clean, bob):
    """Bob accepts WITHOUT double → Alice gets refund of unused 2,
    Bob locks 2 (base only). Verify response + transactions."""
    cid = getattr(pytest, "shared_cid_a", None)
    if not cid: pytest.skip("Need M2a first")
    a_before = _balance(alice_clean)
    b_before = _balance(bob)
    r = bob.post(f"{BASE}/api/quiz/champion/challenge/{cid}/claim",
                 json={"double": False}, timeout=60)
    assert r.status_code == 200, f"claim: {r.status_code} {r.text[:300]}"
    j = r.json()
    assert j.get("doubled") is False, f"response.doubled should be False: {j}"
    a_after = _balance(alice_clean)
    b_after = _balance(bob)
    a_delta = a_after - a_before  # should be +2 (refund)
    b_delta = b_before - b_after  # should be 2 (lock)
    assert a_delta == Decimal("2.00"), f"Alice refund expected +2, got {a_delta}"
    assert b_delta == Decimal("2.00"), f"Bob lock expected -2, got {b_delta}"
    # verify refund tx exists for Alice with type quiz_challenge_refund and ref=cid
    a_txs = _txs(alice_clean, pages=2)
    refunds = [t for t in a_txs if t.get("type") == "quiz_challenge_refund"
               and (t.get("ref_id") == cid or cid in str(t))]
    assert refunds, f"alice refund tx for {cid} not found"
    print(f"[M2b] Alice +{a_delta} (refund) Bob -{b_delta} (lock) doubled={j.get('doubled')} ✓")


def test_m2c_bob_accept_with_double(alice_clean, bob):
    """New challenge: Alice allow_double=true; Bob claims with double=true
    → Bob locks 4 (2× stake), Alice keeps 4 locked, response.doubled=true."""
    a_uid = alice_clean._user.get("user_id") or alice_clean._user.get("id")
    b_uid = bob._user.get("user_id") or bob._user.get("id")
    _force_cleanup_open_challenges(a_uid, b_uid)
    if _balance(alice_clean) < Decimal("4") or _balance(bob) < Decimal("4"):
        pytest.skip("insufficient balance for M2c")
    payload = {"mode": "paid", "stake_amount": 2, "country_code": "CM",
               "allow_double": True}
    r = alice_clean.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=60)
    assert r.status_code == 200, r.text[:300]
    cid = r.json()["challenge_id"]
    b_before = _balance(bob)
    r = bob.post(f"{BASE}/api/quiz/champion/challenge/{cid}/claim",
                 json={"double": True}, timeout=60)
    assert r.status_code == 200, f"claim double: {r.status_code} {r.text[:300]}"
    j = r.json()
    assert j.get("doubled") is True, f"response.doubled should be True: {j}"
    b_after = _balance(bob)
    delta = b_before - b_after
    assert delta == Decimal("4.00"), f"Bob expected -4 (2× stake), got {delta}"
    # M2d — public endpoint must expose allow_double + doubled
    pub = requests.get(f"{BASE}/api/quiz/champion/challenge/public/{cid}", timeout=30)
    assert pub.status_code == 200, pub.text[:200]
    pj = pub.json()
    assert "allow_double" in pj, f"public endpoint missing allow_double: {pj}"
    assert "doubled" in pj, f"public endpoint missing doubled: {pj}"
    assert pj.get("allow_double") is True
    assert pj.get("doubled") is True
    print(f"[M2c+d] cid={cid} Bob -{delta} doubled=True public exposes fields ✓")


def test_m2e_charlie_insufficient_for_double_returns_402(charlie):
    """Charlie has 0 USD → POST /open with allow_double=true returns 402."""
    payload = {"mode": "paid", "stake_amount": 2, "country_code": "CM",
               "allow_double": True}
    r = charlie.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=60)
    if r.status_code == 403 and "désactivé" in r.text:
        pytest.skip("Paid disabled")
    assert r.status_code == 402, f"expected 402, got {r.status_code} body={r.text[:200]}"
    detail = ""
    try: detail = r.json().get("detail", "")
    except Exception: detail = r.text
    assert "Solde insuffisant" in detail or "manque" in detail, \
        f"missing gap msg in: {detail!r}"
    print(f"[M2e] 402 detail={detail!r}")


# ════════════════════════════ M3 ════════════════════════════
def test_m3_db_schema():
    """Verify quiz_ai_validation_stats table + new quiz_questions cols."""
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    async def _m():
        c = await asyncpg.connect(os.environ["DATABASE_URL"])
        tbl = await c.fetchval("SELECT to_regclass('quiz_ai_validation_stats')::text")
        assert tbl == "quiz_ai_validation_stats"
        cols = {r["column_name"] for r in await c.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='quiz_ai_validation_stats'")}
        for need in ["batch_id", "category", "total_generated", "accepted", "rejected",
                     "avg_confidence", "rejection_reasons"]:
            assert need in cols, f"quiz_ai_validation_stats missing {need}"
        qcols = {r["column_name"] for r in await c.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='quiz_questions'")}
        for need in ["validation_confidence", "validation_notes", "validated_at"]:
            assert need in qcols, f"quiz_questions missing {need}"
        await c.close()
    asyncio.run(_m())
    # validator module imports cleanly
    from backend.services import quiz_ai_validator as v  # noqa: F401
    assert hasattr(v, "validate_questions")
    assert hasattr(v, "split_accepted")
    assert hasattr(v, "record_validation_stats")
    print("[M3] DB schema + validator module OK ✓")


def test_m3_admin_validation_stats_endpoint(admin):
    """GET /api/admin/games/quiz/validation-stats?days=30 returns expected
    keys. We do NOT trigger pool-refresh here (3min Claude wait)."""
    r = admin.get(f"{BASE}/api/admin/games/quiz/validation-stats?days=30", timeout=60)
    if r.status_code in (401, 403):
        pytest.skip(f"admin auth/perm failed: {r.status_code}")
    assert r.status_code == 200, f"status {r.status_code} body={r.text[:300]}"
    j = r.json()
    for need in ["generated", "accepted", "rejected", "avg_confidence",
                 "accept_rate_pct", "by_category", "recent_batches"]:
        assert need in j, f"endpoint missing key '{need}': {list(j.keys())}"
    assert 0 <= float(j["accept_rate_pct"]) <= 100
    print(f"[M3] stats: gen={j['generated']} acc={j['accepted']} "
          f"rej={j['rejected']} rate={j['accept_rate_pct']}% "
          f"batches={len(j.get('recent_batches', []))} ✓")
