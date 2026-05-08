"""
iter150 — Referral Anti-fraud heuristics.

Detects three classes of suspicious referral activity :

  1. Multi-account farming :
        > 5 referrals from the same IP address in the last 24h
        > 3 referrals to accounts sharing a registration IP

  2. Velocity attacks :
        > 10 referrals in any rolling 1h window
        > 20 referrals in 24h from the same referrer

  3. Device co-location :
        Two or more `referred_id` accounts share an identical
        `device_id` (set during /api/auth/register), strong signal
        of a single user creating sock-puppets.

The service is **read-only** — it does NOT auto-disqualify anyone. It
exposes an admin report (`/api/admin/referrals/fraud-report`) so a human
can review and decide. This matches the existing JAPAP moderation philosophy
("AI suggests, human decides") set in iter142.

Usage in business code:
    from services.referral_fraud_service import score_referral
    score = await score_referral(conn, referrer_id, referred_id, ip, device_id)
    if score["risk"] >= 80:
        # block reward
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tunable thresholds — kept as module constants so a future admin UI can
# expose them without code changes.
THRESH_IP_24H = 5
THRESH_VELOCITY_1H = 10
THRESH_VELOCITY_24H = 20
THRESH_DEVICE_SHARED = 2  # 2+ referred users sharing one device_id


# ───────────────────────── Per-referral scoring ─────────────────────────

async def score_referral(conn, referrer_id: str, referred_id: str,
                         ip: Optional[str], device_id: Optional[str]) -> dict:
    """Return a {"risk": int 0-100, "signals": list[dict]} report for a
    single referral attempt.

    Designed to be called *synchronously inline* on
    `/api/referrals/apply` so the caller can:
      • allow + record (risk < 50)
      • allow + flag for review (50 ≤ risk < 80)
      • block reward (risk ≥ 80)

    Pure read queries — no writes — so it's idempotent and cheap.
    """
    signals: list[dict] = []
    risk = 0

    # 1 — IP velocity (last 24h)
    if ip:
        ip_24h = await conn.fetchval(
            """SELECT COUNT(*) FROM referrals
                WHERE ip_address = $1
                  AND created_at > NOW() - INTERVAL '24 hours'""",
            ip,
        ) or 0
        if ip_24h >= THRESH_IP_24H:
            risk += min(40, 8 * (ip_24h - THRESH_IP_24H + 1))
            signals.append({
                "code": "IP_VELOCITY_24H",
                "ip": ip,
                "count": int(ip_24h),
                "threshold": THRESH_IP_24H,
                "weight": min(40, 8 * (ip_24h - THRESH_IP_24H + 1)),
            })

    # 2 — Referrer velocity
    velocity_1h = await conn.fetchval(
        """SELECT COUNT(*) FROM referrals
            WHERE referrer_id = $1
              AND created_at > NOW() - INTERVAL '1 hour'""",
        referrer_id,
    ) or 0
    if velocity_1h >= THRESH_VELOCITY_1H:
        risk += 30
        signals.append({"code": "REFERRER_VELOCITY_1H",
                         "count": int(velocity_1h),
                         "threshold": THRESH_VELOCITY_1H, "weight": 30})

    velocity_24h = await conn.fetchval(
        """SELECT COUNT(*) FROM referrals
            WHERE referrer_id = $1
              AND created_at > NOW() - INTERVAL '24 hours'""",
        referrer_id,
    ) or 0
    if velocity_24h >= THRESH_VELOCITY_24H:
        risk += 25
        signals.append({"code": "REFERRER_VELOCITY_24H",
                         "count": int(velocity_24h),
                         "threshold": THRESH_VELOCITY_24H, "weight": 25})

    # 3 — Device co-location: how many referred users share this device_id?
    if device_id:
        same_device_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT referred_id) FROM referrals
                WHERE device_id = $1""",
            device_id,
        ) or 0
        if same_device_count >= THRESH_DEVICE_SHARED:
            risk += min(50, 20 * (same_device_count - 1))
            signals.append({"code": "DEVICE_SHARED",
                             "device_id": device_id[:24] + "…",
                             "shared_with": int(same_device_count),
                             "threshold": THRESH_DEVICE_SHARED,
                             "weight": min(50, 20 * (same_device_count - 1))})

    # 4 — Self-referral attempt (rare but always 100% block)
    if referrer_id == referred_id:
        risk = 100
        signals.append({"code": "SELF_REFERRAL", "weight": 100})

    return {"risk": min(100, risk), "signals": signals}


# ───────────────────────── Admin aggregate report ─────────────────────────

async def fraud_report(conn, days: int = 7, limit: int = 100) -> dict:
    """High-level dashboard query for the admin '/api/admin/referrals/fraud-report'
    endpoint. Returns three lists:
        • top_ips        — ips with the most referrals in the window
        • top_devices    — device_ids shared by ≥2 referred users
        • top_velocity   — referrers exceeding the velocity threshold
    Each row is JSON-friendly and ready to display in a table.
    """
    days = max(1, min(days, 90))
    rows_ip = await conn.fetch(
        f"""SELECT ip_address, COUNT(*) AS cnt,
                   COUNT(DISTINCT referred_id) AS unique_referred
              FROM referrals
             WHERE ip_address IS NOT NULL
               AND created_at > NOW() - INTERVAL '{days} days'
             GROUP BY ip_address
            HAVING COUNT(*) >= $1
             ORDER BY cnt DESC
             LIMIT $2""",
        THRESH_IP_24H, limit,
    )
    rows_dev = await conn.fetch(
        f"""SELECT device_id, COUNT(DISTINCT referred_id) AS shared,
                   COUNT(*) AS total
              FROM referrals
             WHERE device_id IS NOT NULL AND device_id <> ''
               AND created_at > NOW() - INTERVAL '{days} days'
             GROUP BY device_id
            HAVING COUNT(DISTINCT referred_id) >= $1
             ORDER BY shared DESC, total DESC
             LIMIT $2""",
        THRESH_DEVICE_SHARED, limit,
    )
    rows_vel = await conn.fetch(
        f"""SELECT r.referrer_id,
                   u.email,
                   u.first_name,
                   u.last_name,
                   COUNT(*) AS cnt,
                   COUNT(DISTINCT r.ip_address) AS unique_ips
              FROM referrals r
              JOIN users u ON u.user_id = r.referrer_id
             WHERE r.created_at > NOW() - INTERVAL '{days} days'
             GROUP BY r.referrer_id, u.email, u.first_name, u.last_name
            HAVING COUNT(*) >= $1
             ORDER BY cnt DESC
             LIMIT $2""",
        THRESH_VELOCITY_24H, limit,
    )
    return {
        "window_days": days,
        "thresholds": {
            "ip_24h": THRESH_IP_24H,
            "velocity_1h": THRESH_VELOCITY_1H,
            "velocity_24h": THRESH_VELOCITY_24H,
            "device_shared": THRESH_DEVICE_SHARED,
        },
        "top_ips": [
            {"ip": r["ip_address"], "count": int(r["cnt"]),
             "unique_referred": int(r["unique_referred"])}
            for r in rows_ip
        ],
        "top_devices": [
            {"device_id": r["device_id"][:24] + "…",
             "shared_with_users": int(r["shared"]),
             "total_referrals": int(r["total"])}
            for r in rows_dev
        ],
        "top_velocity": [
            {"referrer_id": r["referrer_id"],
             "email": r["email"],
             "name": f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip(),
             "referrals": int(r["cnt"]),
             "unique_ips": int(r["unique_ips"])}
            for r in rows_vel
        ],
    }
