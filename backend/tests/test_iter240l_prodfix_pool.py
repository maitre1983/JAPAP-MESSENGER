"""iter240l-prodfix — DB pool sizing & timeout regression.

Locks down the configuration that fixed the 15/05/2026 P0 outage where
`max_size=10` + no `command_timeout` + no acquire timeout caused every
DB-bound route (login, forgot-password, …) to hang forever once concurrent
traffic exhausted the pool. /api/health stayed green so monitoring missed
the outage — hence the new /api/health/db readiness probe.
"""
import asyncio
import os
import sys

import asyncpg
import httpx
import pytest

sys.path.insert(0, "/app/backend")

# Load /app/backend/.env so DATABASE_URL is available to get_pool().
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

from database import get_pool  # noqa: E402


@pytest.mark.asyncio
async def test_pool_is_sized_for_production_traffic():
    """Pool must hold enough connections to absorb a real burst."""
    pool = await get_pool()
    assert pool.get_max_size() >= 50, (
        f"pool.max_size={pool.get_max_size()} is too small — production "
        "burst-of-30-concurrent-logins will deadlock."
    )
    assert pool.get_min_size() >= 5, "warm pool baseline too low"


@pytest.mark.asyncio
async def test_pool_command_timeout_is_set():
    """Every query has a hard ceiling, so a stuck Neon backend cannot
    indefinitely hold a slot (was the actual prod failure mode)."""
    pool = await get_pool()
    # asyncpg stores the per-pool command_timeout on the connect kwargs.
    timeout = getattr(pool, "_command_timeout", None) or pool._connect_kwargs.get(
        "command_timeout"
    )  # type: ignore[attr-defined]
    assert timeout is not None and 5 <= timeout <= 30, (
        f"command_timeout={timeout!r} — must be set (5-30s window)."
    )


@pytest.mark.asyncio
async def test_pool_recycles_idle_connections():
    """Neon serverless suspends idle conns; we must recycle below that."""
    pool = await get_pool()
    lifetime = getattr(pool, "_max_inactive_connection_lifetime", None)
    assert lifetime is not None and lifetime <= 300, (
        f"max_inactive_connection_lifetime={lifetime!r} — too long, will "
        "hand out stale Neon-suspended connections."
    )


@pytest.mark.asyncio
async def test_health_db_endpoint_pings_real_db():
    """The new /api/health/db must actually exercise the pool."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("http://localhost:8001/api/health/db")
    assert r.status_code == 200, f"health/db returned {r.status_code}: {r.text}"
    payload = r.json()
    assert payload["status"] == "healthy"
    # db_ms must be > 0 — proves a real round-trip happened (and < 5s
    # otherwise the readiness probe is itself too slow).
    assert 0 < payload["db_ms"] < 5000, payload
    pool = payload.get("pool", {})
    assert pool.get("max_size", 0) >= 50, f"pool stats wrong: {pool}"


@pytest.mark.asyncio
async def test_concurrent_logins_do_not_exhaust_pool():
    """30 parallel logins must all complete in <10s — the exact symptom
    that broke prod when max_size was 10."""
    async def one():
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "http://localhost:8001/api/auth/login",
                json={
                    "email": f"nope-{os.urandom(4).hex()}@test.com",
                    "password": "x",
                    "captcha_id": "JAPAP_E2E_BYPASS_2026",
                    "captcha_answer": "0",
                },
            )
        return r.status_code, r.elapsed.total_seconds()

    results = await asyncio.gather(*[one() for _ in range(30)])
    timeouts = [r for r in results if r[0] >= 500 or r[1] > 9]
    assert not timeouts, f"{len(timeouts)}/30 requests hit timeout/5xx: {timeouts[:3]}"
    # All non-existent emails → 401 expected.
    bad_statuses = [r for r in results if r[0] != 401]
    assert not bad_statuses, f"expected all 401, got: {bad_statuses[:3]}"
