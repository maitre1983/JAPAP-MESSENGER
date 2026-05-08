"""
iter146 — Trusted Device service.

Goal: stop logging users out of their primary device every 7 days.

Logic:
    1. Each successful password login increments a per-(user, device fingerprint)
       counter in `trusted_devices`.
    2. As soon as the counter reaches 2 the device is marked `is_trusted = TRUE`.
       The login endpoint then issues a long-lived refresh token (90 days)
       instead of the default 7 days, so the user stays signed in until they
       explicitly logout from this device.
    3. The fingerprint is built from `(ip, user_agent)` reusing
       `device_fingerprint()` from `security_service` for consistency with the
       existing new-device detection.

Anti-abuse safeguards:
    * The counter is per-user — stealing a fingerprint string buys nothing
      without a valid password.
    * On user logout we DO NOT delete the row (so "trusted" status survives a
      voluntary disconnect). We only revoke the JTI.
    * On password reset / account compromise the caller can drop ALL trusted
      devices for the user via `untrust_all()`.
    * Stale rows (>180d unused) are eligible for cleanup but we keep them for
      audit; a future cron can prune.
"""
from __future__ import annotations

import logging
from typing import Optional

from database import get_pool
from services.security_service import device_fingerprint

logger = logging.getLogger(__name__)

TRUSTED_THRESHOLD = 2  # 2 successful logins → trusted
TRUSTED_REFRESH_TTL_DAYS = 90
DEFAULT_REFRESH_TTL_DAYS = 7


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS trusted_devices (
        id BIGSERIAL PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        fingerprint VARCHAR(64) NOT NULL,
        successful_logins_count INTEGER NOT NULL DEFAULT 0,
        is_trusted BOOLEAN NOT NULL DEFAULT FALSE,
        last_ip VARCHAR(64),
        last_user_agent VARCHAR(512),
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        trusted_at TIMESTAMPTZ,
        UNIQUE (user_id, fingerprint)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trusted_devices_user ON trusted_devices(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_trusted_devices_fp   ON trusted_devices(fingerprint)",
]


async def ensure_trusted_devices_table() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in _DDL:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"trusted_devices DDL failed: {e}")


async def record_successful_login(user_id: str, ip: str, ua: str) -> dict:
    """Bump the counter for (user_id, fingerprint(ip, ua)) and decide whether
    this device should be considered trusted from now on.

    Returns:
        {
            "fingerprint": "<sha256[:32]>",
            "is_trusted": bool,             # True if device is trusted *after*
                                            # this login
            "successful_logins_count": int, # post-increment
            "newly_trusted": bool,          # True only on the transition login
        }
    """
    fp = device_fingerprint(ip, ua)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Upsert; capture the old `is_trusted` to detect the transition.
        row = await conn.fetchrow(
            """
            INSERT INTO trusted_devices (user_id, fingerprint, successful_logins_count,
                                          is_trusted, last_ip, last_user_agent,
                                          last_seen_at)
                 VALUES ($1, $2, 1, FALSE, $3, $4, NOW())
            ON CONFLICT (user_id, fingerprint) DO UPDATE
                SET successful_logins_count = trusted_devices.successful_logins_count + 1,
                    last_ip                  = EXCLUDED.last_ip,
                    last_user_agent          = EXCLUDED.last_user_agent,
                    last_seen_at             = NOW(),
                    is_trusted = CASE
                        WHEN trusted_devices.successful_logins_count + 1 >= $5
                        THEN TRUE
                        ELSE trusted_devices.is_trusted
                    END,
                    trusted_at = CASE
                        WHEN trusted_devices.is_trusted = FALSE
                         AND trusted_devices.successful_logins_count + 1 >= $5
                        THEN NOW()
                        ELSE trusted_devices.trusted_at
                    END
            RETURNING successful_logins_count, is_trusted, trusted_at
            """,
            user_id, fp, ip, (ua or "")[:512], TRUSTED_THRESHOLD,
        )
    count = int(row["successful_logins_count"])
    is_trusted = bool(row["is_trusted"])
    # Transition: counter just hit threshold AND we have a fresh trusted_at.
    newly_trusted = is_trusted and count == TRUSTED_THRESHOLD
    return {
        "fingerprint": fp,
        "is_trusted": is_trusted,
        "successful_logins_count": count,
        "newly_trusted": newly_trusted,
    }


async def get_refresh_ttl_days(user_id: str, ip: str, ua: str) -> int:
    """Return the refresh-token TTL (days) we should issue for this device.

    Used by `/api/auth/refresh` to keep extending sessions on trusted devices
    without requiring a fresh password each rotation.
    """
    fp = device_fingerprint(ip, ua)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_trusted FROM trusted_devices WHERE user_id = $1 AND fingerprint = $2",
            user_id, fp,
        )
    if row and bool(row["is_trusted"]):
        return TRUSTED_REFRESH_TTL_DAYS
    return DEFAULT_REFRESH_TTL_DAYS


async def list_trusted_devices(user_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT fingerprint, successful_logins_count, is_trusted,
                      last_ip, last_user_agent, first_seen_at, last_seen_at,
                      trusted_at
                 FROM trusted_devices
                WHERE user_id = $1
                ORDER BY last_seen_at DESC""",
            user_id,
        )
    out = []
    for r in rows:
        out.append({
            "fingerprint": r["fingerprint"],
            "successful_logins_count": int(r["successful_logins_count"]),
            "is_trusted": bool(r["is_trusted"]),
            "last_ip": r["last_ip"],
            "last_user_agent": r["last_user_agent"],
            "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
            "trusted_at": r["trusted_at"].isoformat() if r["trusted_at"] else None,
        })
    return out


async def untrust_device(user_id: str, fingerprint: str) -> bool:
    """User-initiated 'this is not my device' — drop trust + reset counter."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE trusted_devices
                  SET is_trusted = FALSE,
                      successful_logins_count = 0,
                      trusted_at = NULL
                WHERE user_id = $1 AND fingerprint = $2""",
            user_id, fingerprint,
        )
    return result.endswith(" 1") if isinstance(result, str) else False


async def untrust_all(user_id: str) -> int:
    """Force a reset on ALL of a user's devices — used after a password reset
    or any compromise event so the next login starts the trust counter from 0
    again."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE trusted_devices
                  SET is_trusted = FALSE,
                      successful_logins_count = 0,
                      trusted_at = NULL
                WHERE user_id = $1""",
            user_id,
        )
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except Exception:
        return 0
