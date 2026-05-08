"""
Security services — iter82 hardening.

Centralises:
  • Refresh-token rotation (JTI-based) + revocation list
  • Active-session tracking (device, IP, user agent)
  • Security event log (login from new IP/device, logout-all, etc.)
  • Helpers the auth routes call.

All tables are created idempotently at boot by `ensure_security_tables()`.
"""
from __future__ import annotations
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from database import get_pool

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
#  SCHEMA
# ════════════════════════════════════════════════════════════════════════

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS revoked_refresh_tokens (
        jti VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(32) NOT NULL,
        revoked_at TIMESTAMPTZ DEFAULT NOW(),
        reason VARCHAR(64) DEFAULT 'rotated',
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_revoked_rt_user ON revoked_refresh_tokens(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_revoked_rt_exp  ON revoked_refresh_tokens(expires_at)",
    """
    CREATE TABLE IF NOT EXISTS active_sessions (
        session_id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(32) NOT NULL,
        jti VARCHAR(64) NOT NULL,
        ip_address VARCHAR(64),
        user_agent VARCHAR(512),
        device_fingerprint VARCHAR(64),
        created_at TIMESTAMPTZ DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        revoked BOOLEAN DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_active_sessions_user ON active_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_active_sessions_jti  ON active_sessions(jti)",
    """
    CREATE TABLE IF NOT EXISTS security_events (
        id BIGSERIAL PRIMARY KEY,
        user_id VARCHAR(32),
        event_type VARCHAR(48) NOT NULL,
        severity VARCHAR(16) DEFAULT 'info',
        ip_address VARCHAR(64),
        user_agent VARCHAR(512),
        details JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sec_events_user   ON security_events(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_sec_events_type   ON security_events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_sec_events_time   ON security_events(created_at DESC)",
    # iter82 — TOTP seed column for future 2FA feature. Null = 2FA off.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE",
]


async def ensure_security_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in _DDL:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"security DDL failed: {e} — stmt={stmt[:60]}")


# ════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════

def new_jti() -> str:
    return secrets.token_urlsafe(24)


def device_fingerprint(ip: str, user_agent: str) -> str:
    """Stable non-PII hash of (ip + user agent) — used to detect a
    login from a genuinely new device vs the same user switching tabs."""
    raw = f"{ip or ''}|{user_agent or ''}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:32]


# ---------- Refresh token revocation ----------

async def is_jti_revoked(jti: str) -> bool:
    if not jti:
        return True
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM revoked_refresh_tokens WHERE jti = $1", jti
        )
    return row is not None


async def revoke_jti(jti: str, user_id: str, reason: str = "rotated",
                     ttl_days: int = 8) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO revoked_refresh_tokens (jti, user_id, reason, expires_at)
               VALUES ($1, $2, $3, $4) ON CONFLICT (jti) DO NOTHING""",
            jti, user_id, reason,
            datetime.now(timezone.utc) + timedelta(days=ttl_days),
        )


async def revoke_all_user_jtis(user_id: str, reason: str = "logout_all") -> int:
    """Mark every active session of this user as revoked + log each JTI
    as revoked so even cached refresh tokens become unusable."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT jti FROM active_sessions WHERE user_id = $1 AND revoked = FALSE",
            user_id,
        )
        count = 0
        for r in rows:
            await conn.execute(
                """INSERT INTO revoked_refresh_tokens (jti, user_id, reason, expires_at)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (jti) DO UPDATE SET reason = EXCLUDED.reason""",
                r["jti"], user_id, reason,
                datetime.now(timezone.utc) + timedelta(days=8),
            )
            count += 1
        await conn.execute(
            "UPDATE active_sessions SET revoked = TRUE WHERE user_id = $1", user_id,
        )
        # Also bump password_changed_at so already-issued access_tokens are
        # rejected by the force-logout guard in auth.get_current_user.
        await conn.execute(
            "UPDATE users SET password_changed_at = NOW() WHERE user_id = $1",
            user_id,
        )
    return count


# ---------- Active sessions ----------

async def upsert_active_session(user_id: str, jti: str, ip: str, ua: str,
                                 ttl_days: int = 7) -> str:
    sid = uuid.uuid4().hex
    fp = device_fingerprint(ip, ua)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO active_sessions (session_id, user_id, jti, ip_address,
                                            user_agent, device_fingerprint, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            sid, user_id, jti, ip, ua[:512], fp,
            datetime.now(timezone.utc) + timedelta(days=ttl_days),
        )
    return sid


async def rotate_session_jti(old_jti: str, new_jti: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE active_sessions SET jti = $1, last_seen_at = NOW()
               WHERE jti = $2 AND revoked = FALSE""",
            new_jti, old_jti,
        )


async def list_active_sessions(user_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT session_id, ip_address, user_agent, device_fingerprint,
                      created_at, last_seen_at, expires_at, revoked
               FROM active_sessions
               WHERE user_id = $1 AND revoked = FALSE
                 AND expires_at > NOW()
               ORDER BY last_seen_at DESC""",
            user_id,
        )
    out = []
    for r in rows:
        out.append({
            "session_id": r["session_id"],
            "ip_address": r["ip_address"],
            "user_agent": r["user_agent"],
            "device_fingerprint": r["device_fingerprint"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        })
    return out


async def revoke_session(session_id: str, user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT jti FROM active_sessions WHERE session_id = $1 AND user_id = $2",
            session_id, user_id,
        )
        if not row:
            return False
        await conn.execute(
            "UPDATE active_sessions SET revoked = TRUE WHERE session_id = $1",
            session_id,
        )
        await revoke_jti(row["jti"], user_id, reason="user_revoked_session")
    return True


# ---------- Security events (suspicious activity detection) ----------

async def log_security_event(user_id: Optional[str], event_type: str, *,
                              severity: str = "info", ip: str = "",
                              ua: str = "", details: Optional[dict] = None) -> None:
    import json as _json
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO security_events
                   (user_id, event_type, severity, ip_address, user_agent, details)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
                user_id, event_type, severity, ip, (ua or "")[:512],
                _json.dumps(details or {}),
            )
    except Exception as e:
        logger.warning(f"security event log failed ({event_type}): {e}")


async def detect_new_device(user_id: str, ip: str, ua: str) -> tuple[bool, str]:
    """Return (is_new, fingerprint) — is_new = True if this user has never
    authenticated from this device fingerprint before."""
    fp = device_fingerprint(ip, ua)
    pool = await get_pool()
    async with pool.acquire() as conn:
        seen = await conn.fetchval(
            """SELECT 1 FROM active_sessions
               WHERE user_id = $1 AND device_fingerprint = $2
               LIMIT 1""",
            user_id, fp,
        )
    return (seen is None, fp)


async def recent_security_events(user_id: str, limit: int = 50) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT event_type, severity, ip_address, user_agent, details, created_at
               FROM security_events
               WHERE user_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            user_id, limit,
        )
    return [
        {
            "event_type": r["event_type"],
            "severity": r["severity"],
            "ip_address": r["ip_address"],
            "user_agent": r["user_agent"],
            "details": r["details"] or {},
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
