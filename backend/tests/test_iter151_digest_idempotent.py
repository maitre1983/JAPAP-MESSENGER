"""
iter151 — Tests for the persistent once-per-day guard on Payment Health
digest. Verifies that:
  • The first send_daily_digest() call inserts a row keyed on today's date.
  • A second concurrent / sequential call with force=False returns
    {sent: False, skipped: True, reason: "already_sent_today"} without
    re-sending the email.
  • force=True bypasses the guard and updates the existing row.
  • DIGEST_HOUR_UTC default is 18.

Email send is monkey-patched to a recording stub so the test never
actually hits the SMTP/Resend backend.
"""
import asyncio
import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

# Make the `app/backend` package importable when the suite is run from
# any cwd inside the repo.
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from services import payment_health  # noqa: E402
from services import payment_verify_retry_worker as worker  # noqa: E402


def test_digest_hour_default_is_18_utc():
    """CEO request iter151 — default daily slot is 18:00 UTC, not 08:00."""
    # Reload to defeat any prior monkey-patching of the env var.
    assert worker.DIGEST_HOUR_UTC == 18, (
        f"DIGEST_HOUR_UTC must default to 18, got {worker.DIGEST_HOUR_UTC}"
    )


def test_send_daily_digest_signature_accepts_force_and_worker_id():
    """The function exposes the new kwargs introduced in iter151."""
    import inspect
    sig = inspect.signature(payment_health.send_daily_digest)
    assert "force" in sig.parameters
    assert "worker_id" in sig.parameters
    # Defaults must keep the legacy "no-arg" call site working.
    assert sig.parameters["force"].default is False
    assert sig.parameters["worker_id"].default == ""


@pytest.mark.asyncio
async def test_send_daily_digest_idempotent(monkeypatch):
    """Two consecutive calls on the same day → second one is skipped."""
    # Prepare: clean today's row + stub the email sender so we don't
    # actually send any mail.
    from database import get_pool
    pool = await get_pool()
    today = date.today()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM payment_health_digests WHERE digest_date = $1", today
        )

    sent_calls = []

    async def fake_send_email(to, subject, html, text):
        sent_calls.append({"to": to, "subject": subject})
        return True

    monkeypatch.setattr("services.email_service.send_email", fake_send_email)

    # First call — should send + claim today's slot.
    r1 = await payment_health.send_daily_digest(force=False, worker_id="test1")
    assert r1.get("sent") is True, f"first call should send: {r1}"
    assert len(sent_calls) == 1

    # Second call — must be skipped.
    r2 = await payment_health.send_daily_digest(force=False, worker_id="test2")
    assert r2.get("sent") is False
    assert r2.get("skipped") is True
    assert r2.get("reason") == "already_sent_today"
    assert len(sent_calls) == 1, "no second email should be sent"

    # force=True — should re-send even though row exists.
    r3 = await payment_health.send_daily_digest(force=True, worker_id="test_force")
    assert r3.get("sent") is True
    # `skipped` is None or False on a force send.
    assert not r3.get("skipped")
    assert len(sent_calls) == 2

    # Verify single row exists per day (UPSERT not INSERT new row).
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT digest_date, status, worker_id FROM payment_health_digests "
            "WHERE digest_date = $1",
            today,
        )
    assert len(rows) == 1, "only ONE row per digest_date"
    assert rows[0]["status"] == "sent"


@pytest.mark.asyncio
async def test_concurrent_dispatch_yields_one_send(monkeypatch):
    """Spawn 5 simultaneous send_daily_digest tasks → exactly one
    actually sends; the other four are skipped with reason='race_lost'
    or 'already_sent_today'."""
    from database import get_pool
    pool = await get_pool()
    today = date.today()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM payment_health_digests WHERE digest_date = $1", today
        )

    sent_calls = []

    async def fake_send_email(to, subject, html, text):
        # Simulate a 50ms email send so concurrent claims have time to race.
        await asyncio.sleep(0.05)
        sent_calls.append(to)
        return True

    monkeypatch.setattr("services.email_service.send_email", fake_send_email)

    results = await asyncio.gather(*[
        payment_health.send_daily_digest(force=False, worker_id=f"w{i}")
        for i in range(5)
    ])
    successes = [r for r in results if r.get("sent")]
    skipped = [r for r in results if r.get("skipped")]
    assert len(successes) == 1, f"exactly one actual send, got {len(successes)} ({results})"
    assert len(skipped) == 4
    assert len(sent_calls) == 1
