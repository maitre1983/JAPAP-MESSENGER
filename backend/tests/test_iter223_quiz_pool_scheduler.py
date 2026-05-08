"""iter223 — Tests for the pool refresh / health helpers in
services.quiz_champion_scheduler.

These tests focus on the deterministic, side-effect-light contract of the
two new functions :
  • _pool_refresh_tick — debounces itself on POOL_REFRESH_HOURS (~ 48h)
  • _pool_health_tick  — only triggers an emergency batch when active
                         questions < POOL_HEALTH_MIN (default 30)

The Claude API is monkey-patched so these tests run offline.
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.quiz_champion_scheduler as sched  # noqa: E402


@pytest.mark.asyncio
async def test_pool_refresh_debounces_within_window(monkeypatch):
    """Calling _pool_refresh_tick twice in a row must only run once."""
    calls = {"n": 0}

    async def fake_generate(conn, total=100):
        calls["n"] += 1
        return {"inserted": total, "skipped": 0, "by_category": {"africa": total}}

    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class _Pool:
        def acquire(self): return _Conn()

    async def fake_pool():
        return _Pool()

    monkeypatch.setattr("database.get_pool", fake_pool)
    monkeypatch.setattr("services.quiz_ai_generator.generate_with_distribution",
                        fake_generate)
    # Reset module state.
    sched._last_pool_refresh_ts = 0.0
    await sched._pool_refresh_tick()
    await sched._pool_refresh_tick()  # debounced — should NOT regenerate
    assert calls["n"] == 1, f"expected 1 call, got {calls['n']}"


@pytest.mark.asyncio
async def test_pool_health_skips_when_pool_healthy(monkeypatch):
    """When count >= POOL_HEALTH_MIN, the helper must not trigger emergency."""
    triggered = {"n": 0}

    async def fake_generate(conn, total=100):
        triggered["n"] += 1
        return {"inserted": total, "skipped": 0, "by_category": {}}

    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def fetchval(self, *a, **kw): return sched.POOL_HEALTH_MIN + 50

    class _Pool:
        def acquire(self): return _Conn()

    async def fake_pool():
        return _Pool()

    monkeypatch.setattr("database.get_pool", fake_pool)
    monkeypatch.setattr("services.quiz_ai_generator.generate_with_distribution",
                        fake_generate)
    sched._last_pool_health_ts = 0.0
    sched._emergency_in_flight = False
    await sched._pool_health_tick()
    assert triggered["n"] == 0


@pytest.mark.asyncio
async def test_pool_health_triggers_emergency_when_low(monkeypatch):
    """When count < POOL_HEALTH_MIN, helper must run an emergency batch."""
    triggered = {"n": 0}

    async def fake_generate(conn, total=100):
        triggered["n"] += 1
        return {"inserted": total, "skipped": 0, "by_category": {}}

    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def fetchval(self, *a, **kw): return 5  # well below POOL_HEALTH_MIN

    class _Pool:
        def acquire(self): return _Conn()

    async def fake_pool():
        return _Pool()

    monkeypatch.setattr("database.get_pool", fake_pool)
    monkeypatch.setattr("services.quiz_ai_generator.generate_with_distribution",
                        fake_generate)
    sched._last_pool_health_ts = 0.0
    sched._last_pool_refresh_ts = 0.0
    sched._emergency_in_flight = False
    await sched._pool_health_tick()
    assert triggered["n"] == 1
