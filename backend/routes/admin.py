import uuid
import logging
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
from typing import Optional
from database import get_pool
from routes.auth import get_current_user, user_to_response, hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(request: Request):
    user = await get_current_user(request)
    if user.get('role') not in ('admin', 'superadmin'):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class AdminUpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_pro: Optional[bool] = None
    role: Optional[str] = None


class AdminEditProfileRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str


class AdminSuspendRequest(BaseModel):
    reason: str = ""
    ban: bool = False  # true = permanent ban, false = soft suspend

class AdminWalletAdjustRequest(BaseModel):
    user_id: str
    amount: float
    notes: str = ""

class AdminUpdateTxRequest(BaseModel):
    status: str
    admin_notes: str = ""


@router.get("/stats")
async def get_stats(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        active_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
        online_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_online = TRUE")
        pro_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_pro = TRUE")
        total_txs = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        pending_txs = await conn.fetchval("SELECT COUNT(*) FROM transactions WHERE status = 'pending'")
        # iter178 — Canonical USD totals (CEO mandate). Filter currency='USD'
        # so legacy XAF rows can never poison the aggregate.
        total_balance_usd = await conn.fetchval(
            "SELECT COALESCE(SUM(balance), 0) FROM wallets WHERE currency = 'USD'")
        non_usd_count = await conn.fetchval(
            "SELECT COUNT(*) FROM wallets WHERE currency IS DISTINCT FROM 'USD'")
        # FX-equivalent (display-only) — XAF default for the FR audience.
        try:
            from services.currency_conversion import usd_to as _usd_to
            total_balance_xaf_eq = str(await _usd_to(total_balance_usd or 0, "XAF"))
        except Exception:
            total_balance_xaf_eq = "0"
        total_balance = total_balance_usd
        total_messages = await conn.fetchval("SELECT COUNT(*) FROM messages")

        # Crowdfunding stats
        cf_total_campaigns = await conn.fetchval("SELECT COUNT(*) FROM campaigns")
        cf_active = await conn.fetchval("SELECT COUNT(*) FROM campaigns WHERE status = 'active'")
        cf_raised = await conn.fetchval("SELECT COALESCE(SUM(raised), 0) FROM campaigns")
        cf_contributions = await conn.fetchval("SELECT COUNT(*) FROM campaign_contributions")
        cf_by_category = await conn.fetch("""
            SELECT category, COUNT(*) AS count, COALESCE(SUM(raised), 0) AS raised
            FROM campaigns GROUP BY category ORDER BY count DESC
        """)

        # Gaming stats
        gm_total_plays = await conn.fetchval("SELECT COUNT(*) FROM game_plays")
        gm_total_rewarded = await conn.fetchval("SELECT COALESCE(SUM(reward), 0) FROM game_plays")
        gm_active_players = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM game_plays WHERE created_at > NOW() - INTERVAL '30 days'"
        )
        gm_by_type = await conn.fetch("""
            SELECT game_type, COUNT(*) AS plays, COALESCE(SUM(reward), 0) AS total_rewards
            FROM game_plays GROUP BY game_type ORDER BY plays DESC
        """)

        # Jobs stats
        jobs_total = await conn.fetchval("SELECT COUNT(*) FROM jobs")
        jobs_open = await conn.fetchval("SELECT COUNT(*) FROM jobs WHERE status = 'open'")
        jobs_applications = await conn.fetchval("SELECT COUNT(*) FROM job_applications")

        # Transport stats
        rides_total = await conn.fetchval("SELECT COUNT(*) FROM ride_requests")
        rides_completed = await conn.fetchval("SELECT COUNT(*) FROM ride_requests WHERE status = 'completed'")
        rides_revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(fare_final), 0) FROM ride_requests WHERE status = 'completed'"
        )
        drivers_count = await conn.fetchval("SELECT COUNT(*) FROM drivers")

        return {
            "total_users": total_users,
            "active_users": active_users,
            "online_users": online_users,
            "pro_users": pro_users,
            "total_transactions": total_txs,
            "pending_transactions": pending_txs,
            "total_balance": str(total_balance),
            "total_balance_usd": str(total_balance_usd),
            "total_balance_xaf_equivalent": total_balance_xaf_eq,
            "non_usd_wallet_count": non_usd_count,
            "currency_canonical": "USD",
            "total_messages": total_messages,
            "crowdfunding": {
                "total_campaigns": cf_total_campaigns,
                "active_campaigns": cf_active,
                "total_raised": str(cf_raised),
                "total_contributions": cf_contributions,
                "by_category": [
                    {"category": r['category'], "count": r['count'], "raised": str(r['raised'])}
                    for r in cf_by_category
                ],
            },
            "gaming": {
                "total_plays": gm_total_plays,
                "total_rewarded": str(gm_total_rewarded),
                "active_players_30d": gm_active_players,
                "by_type": [
                    {"game_type": r['game_type'], "plays": r['plays'], "total_rewards": str(r['total_rewards'])}
                    for r in gm_by_type
                ],
            },
            "jobs": {
                "total": jobs_total,
                "open": jobs_open,
                "applications": jobs_applications,
            },
            "transport": {
                "rides_total": rides_total,
                "rides_completed": rides_completed,
                "revenue_total": str(rides_revenue),
                "drivers": drivers_count,
            },
        }


@router.get("/users")
async def list_users(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), search: Optional[str] = None):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        if search:
            count = await conn.fetchval("""
                SELECT COUNT(*) FROM users WHERE LOWER(username) LIKE $1 OR LOWER(email) LIKE $1 
                OR LOWER(first_name) LIKE $1 OR LOWER(last_name) LIKE $1
            """, f"%{search.lower()}%")
            rows = await conn.fetch("""
                SELECT * FROM users WHERE LOWER(username) LIKE $1 OR LOWER(email) LIKE $1
                OR LOWER(first_name) LIKE $1 OR LOWER(last_name) LIKE $1
                ORDER BY created_at DESC LIMIT $2 OFFSET $3
            """, f"%{search.lower()}%", limit, offset)
        else:
            count = await conn.fetchval("SELECT COUNT(*) FROM users")
            rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2", limit, offset)
        
        users = []
        for row in rows:
            u = user_to_response(dict(row))
            wallet = await conn.fetchrow("SELECT balance, currency FROM wallets WHERE user_id = $1", row['user_id'])
            if wallet:
                u['wallet_balance'] = str(wallet['balance'])
                u['wallet_currency'] = wallet['currency']
            users.append(u)
        
        return {"users": users, "total": count, "page": page, "limit": limit}


@router.get("/users-by-balance")
async def list_users_by_balance(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None, description="email | username | phone_number (substring)"),
    country: Optional[str] = Query(None, description="ISO-3166 alpha-2 country code"),
    status: Optional[str] = Query(None, description="active | suspended"),
    legacy: Optional[str] = Query(None, description="legacy | new"),
    min_balance: Optional[float] = Query(None, ge=0),
    export: Optional[str] = Query(None, description="csv to download"),
):
    """iter154 — Admin view: users sorted by wallet balance (DESC).

    Real `wallets.balance` join — no mocked data. Used for financial
    oversight (top-balance accounts, freeze/anti-fraud audits).

    Filters:
      • search: substring match on email / username / phone_number
      • country: exact match on `users.country_code`
      • status: 'active' (is_active=TRUE) | 'suspended' (is_active=FALSE)
      • legacy: 'legacy' (legacy_id IS NOT NULL) | 'new' (legacy_id IS NULL)
      • min_balance: only show wallets with balance >= N (USD-equivalent)

    `export=csv` streams a CSV (no pagination) for accounting reconciliation.
    """
    await require_admin(request)
    pool = await get_pool()

    # Build dynamic WHERE — every clause appended only when the filter is set.
    where = []
    params: list = []
    if search:
        params.append(f"%{search.strip().lower()}%")
        idx = len(params)
        where.append(
            f"(LOWER(u.email) LIKE ${idx} OR LOWER(u.username) LIKE ${idx} "
            f"OR LOWER(COALESCE(u.phone_number,'')) LIKE ${idx})"
        )
    if country:
        params.append(country.strip().upper()[:2])
        where.append(f"UPPER(COALESCE(u.country_code, u.country)) = ${len(params)}")
    if status == "active":
        where.append("u.is_active = TRUE")
    elif status == "suspended":
        where.append("u.is_active = FALSE")
    if legacy == "legacy":
        where.append("u.legacy_id IS NOT NULL")
    elif legacy == "new":
        where.append("u.legacy_id IS NULL")
    if min_balance is not None:
        params.append(Decimal(str(min_balance)))
        where.append(f"COALESCE(w.balance, 0) >= ${len(params)}")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    base_from = """
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.user_id
    """

    # CSV export — no pagination, full result set (capped at 10k for safety).
    if (export or "").lower() == "csv":
        params_export = list(params)
        params_export.append(10000)
        sql = f"""
            SELECT u.user_id, u.username, u.email, u.phone_number,
                   COALESCE(u.country_code, u.country, '') AS country,
                   COALESCE(w.balance, 0) AS balance,
                   COALESCE(w.currency, '') AS currency,
                   u.is_active, u.is_legacy_account, u.legacy_id,
                   u.created_at, u.last_seen
            {base_from}
            {where_sql}
            ORDER BY COALESCE(w.balance, 0) DESC, u.created_at DESC
            LIMIT ${len(params_export)}
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params_export)

        def _stream():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "user_id", "username", "email", "phone", "country",
                "wallet_balance", "currency", "status", "is_legacy",
                "legacy_id", "created_at", "last_seen",
            ])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
            for r in rows:
                writer.writerow([
                    r["user_id"], r["username"] or "", r["email"] or "",
                    r["phone_number"] or "",
                    r["country"] or "",
                    f"{r['balance']:.2f}",
                    r["currency"] or "",
                    "active" if r["is_active"] else "suspended",
                    "true" if r["is_legacy_account"] else "false",
                    r["legacy_id"] if r["legacy_id"] is not None else "",
                    r["created_at"].isoformat() if r["created_at"] else "",
                    r["last_seen"].isoformat() if r["last_seen"] else "",
                ])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(
            _stream(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="japap_users_by_balance_{ts}.csv"'},
        )

    # Paginated JSON response.
    offset = (page - 1) * limit
    params_paged = list(params)
    params_paged.extend([limit, offset])
    list_sql = f"""
        SELECT u.user_id, u.username, u.email, u.phone_number,
               u.first_name, u.last_name,
               COALESCE(u.country_code, u.country, '') AS country,
               u.role, u.is_active, u.is_legacy_account, u.legacy_id,
               u.created_at, u.last_seen,
               COALESCE(w.balance, 0) AS balance,
               COALESCE(w.currency, '') AS currency
        {base_from}
        {where_sql}
        ORDER BY COALESCE(w.balance, 0) DESC, u.created_at DESC
        LIMIT ${len(params_paged) - 1} OFFSET ${len(params_paged)}
    """
    count_sql = f"SELECT COUNT(*) {base_from} {where_sql}"

    async with pool.acquire() as conn:
        total = await conn.fetchval(count_sql, *params)
        rows = await conn.fetch(list_sql, *params_paged)
        # Sum of balances for the filtered set (for the dashboard header).
        sum_sql = f"SELECT COALESCE(SUM(w.balance), 0) {base_from} {where_sql}"
        total_balance = await conn.fetchval(sum_sql, *params)

    users = []
    for r in rows:
        users.append({
            "user_id": r["user_id"],
            "username": r["username"] or "",
            "email": r["email"] or "",
            "phone_number": r["phone_number"] or "",
            "first_name": r["first_name"] or "",
            "last_name": r["last_name"] or "",
            "country": r["country"] or "",
            "role": r["role"] or "user",
            "is_active": bool(r["is_active"]),
            "is_legacy": bool(r["is_legacy_account"]),
            "balance": str(r["balance"]),
            "currency": r["currency"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        })
    return {
        "users": users,
        "total": int(total or 0),
        "page": page,
        "limit": limit,
        "total_balance": str(total_balance or 0),
        "filters": {
            "search": search, "country": country, "status": status,
            "legacy": legacy, "min_balance": min_balance,
        },
    }


@router.put("/users/{user_id}")
async def admin_update_user(user_id: str, req: AdminUpdateUserRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    updates = {}
    for field in ['is_active', 'is_verified', 'is_pro', 'role']:
        val = getattr(req, field, None)
        if val is not None:
            updates[field] = val
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    updates['updated_at'] = datetime.now(timezone.utc)
    set_clause = ", ".join([f"{k} = ${i+1}" for i, k in enumerate(updates.keys())])
    values = list(updates.values()) + [user_id]
    
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET {set_clause} WHERE user_id = ${len(values)}", *values)
        
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_update_user', 'users', $2)
        """, admin['user_id'], f'{{"target": "{user_id}", "updates": "{updates}"}}')
        
        updated = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return user_to_response(dict(updated))


@router.get("/wallet/alerts")
async def admin_wallet_alerts(
    request: Request,
    limit: int = 50,
    unread_only: bool = False,
):
    """List the most recent admin alerts (wallet anomalies).

    When `unread_only=true`, returns only rows where `acknowledged_at IS NULL`.
    The response also exposes `unread_count` so the UI can render a badge.
    """
    await require_admin(request)
    limit = max(1, min(int(limit), 500))
    pool = await get_pool()
    async with pool.acquire() as conn:
        from services.admin_alerts import _ensure_ddl as _a_ddl
        await _a_ddl(conn)
        # Backfill ack column if missing (idempotent)
        await conn.execute("ALTER TABLE admin_alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE admin_alerts ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(64)")
        unread_count = await conn.fetchval(
            "SELECT COUNT(*) FROM admin_alerts WHERE acknowledged_at IS NULL"
        )
        if unread_only:
            rows = await conn.fetch(
                """SELECT id, kind, alert_key, title, body, url, created_at, push_sent, acknowledged_at
                   FROM admin_alerts
                   WHERE acknowledged_at IS NULL
                   ORDER BY created_at DESC LIMIT $1""",
                limit,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, kind, alert_key, title, body, url, created_at, push_sent, acknowledged_at
                   FROM admin_alerts
                   ORDER BY created_at DESC LIMIT $1""",
                limit,
            )
    return {
        "unread_count": int(unread_count or 0),
        "items": [
            {
                "id": int(r["id"]),
                "kind": r["kind"],
                "alert_key": r["alert_key"],
                "title": r["title"],
                "body": r["body"],
                "url": r["url"],
                "push_sent": bool(r["push_sent"]),
                "acknowledged_at": r["acknowledged_at"].isoformat() if r["acknowledged_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            } for r in rows
        ],
    }


@router.post("/wallet/alerts/{alert_id}/ack")
async def admin_wallet_alerts_ack(alert_id: int, request: Request):
    """Mark a single alert as acknowledged."""
    user = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE admin_alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE admin_alerts ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(64)")
        row = await conn.fetchrow(
            """UPDATE admin_alerts SET acknowledged_at = NOW(), acknowledged_by = $1
               WHERE id = $2 AND acknowledged_at IS NULL
               RETURNING id""",
            user.get("user_id") if isinstance(user, dict) else None,
            alert_id,
        )
    return {"id": alert_id, "ok": bool(row)}


@router.post("/wallet/alerts/ack-all")
async def admin_wallet_alerts_ack_all(request: Request):
    """Mark all pending alerts as acknowledged."""
    user = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE admin_alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE admin_alerts ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(64)")
        n = await conn.fetchval(
            """WITH upd AS (
                 UPDATE admin_alerts SET acknowledged_at = NOW(), acknowledged_by = $1
                 WHERE acknowledged_at IS NULL RETURNING id
               ) SELECT COUNT(*) FROM upd""",
            user.get("user_id") if isinstance(user, dict) else None,
        )
    return {"acknowledged": int(n or 0)}


@router.get("/wallet/overview")
async def admin_wallet_overview(request: Request, days: int = 30):
    """Global wallet observability — balances, volumes, funnels, anomalies.

    Consumed by the admin "Wallet overview" dashboard.
    """
    from decimal import Decimal as _D
    await require_admin(request)
    days = max(1, min(int(days), 365))
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ── totals ─────────────────────────────────────────────────────────
        balances = await conn.fetchrow(
            """SELECT COUNT(*)               AS accounts,
                      COALESCE(SUM(balance),0)::numeric AS total_balance,
                      COALESCE(AVG(balance),0)::numeric AS avg_balance,
                      COALESCE(MAX(balance),0)::numeric AS max_balance,
                      COUNT(*) FILTER (WHERE balance > 0) AS funded,
                      COUNT(*) FILTER (WHERE is_locked)   AS locked
               FROM wallets"""
        )
        # ── volumes (by type, over window) ─────────────────────────────────
        vols = await conn.fetch(
            """SELECT type,
                      COUNT(*) AS n,
                      COALESCE(SUM(amount),0)::numeric AS total,
                      COUNT(*) FILTER (WHERE status='completed') AS completed,
                      COUNT(*) FILTER (WHERE status='pending')   AS pending,
                      COUNT(*) FILTER (WHERE status='cancelled') AS cancelled
               FROM transactions
               WHERE created_at >= NOW() - ($1 || ' days')::interval
               GROUP BY type ORDER BY total DESC""",
            str(days),
        )
        # ── daily timeseries (volume in/out) ───────────────────────────────
        ts = await conn.fetch(
            """SELECT created_at::date AS day,
                      COUNT(*) AS n,
                      COALESCE(SUM(amount) FILTER (WHERE type IN ('deposit','admin_credit','game_reward','receive')),0)::numeric AS inflow,
                      COALESCE(SUM(amount) FILTER (WHERE type IN ('withdrawal','admin_debit','send','game_bet')),0)::numeric AS outflow
               FROM transactions
               WHERE created_at >= NOW() - ($1 || ' days')::interval
                 AND status = 'completed'
               GROUP BY 1 ORDER BY 1""",
            str(days),
        )
        # ── top funded accounts ────────────────────────────────────────────
        top = await conn.fetch(
            """SELECT w.user_id, w.balance, u.first_name, u.last_name, u.email, u.avatar, u.is_pro
               FROM wallets w
               LEFT JOIN users u ON u.user_id = w.user_id
               ORDER BY w.balance DESC LIMIT 10"""
        )
        # ── anomalies: large withdrawals, stuck pending > 24h, repeated send spam ─
        large_withdraws = await conn.fetch(
            """SELECT tx_id, from_user_id, amount, status, created_at
               FROM transactions
               WHERE type='withdrawal'
                 AND created_at >= NOW() - ($1 || ' days')::interval
                 AND amount > 500
               ORDER BY amount DESC LIMIT 10""",
            str(days),
        )
        stuck_pending = await conn.fetch(
            """SELECT tx_id, type, from_user_id, to_user_id, amount, created_at
               FROM transactions
               WHERE status='pending'
                 AND created_at < NOW() - INTERVAL '24 hours'
               ORDER BY created_at ASC LIMIT 10"""
        )
        send_spammers = await conn.fetch(
            """SELECT from_user_id, COUNT(*) AS n, COALESCE(SUM(amount),0)::numeric AS total
               FROM transactions
               WHERE type='send'
                 AND created_at >= NOW() - INTERVAL '1 hour'
               GROUP BY from_user_id HAVING COUNT(*) >= 10
               ORDER BY n DESC LIMIT 10"""
        )
        # ── engagement points totals (from wheel_spins, authoritative) ─────
        pts = await conn.fetchrow(
            """SELECT COUNT(*) AS spins,
                      COUNT(DISTINCT user_id) AS players,
                      COALESCE(SUM(points_awarded),0) AS total_points,
                      COALESCE(SUM(points_awarded) FILTER (WHERE source='wheel'),0) AS pts_wheel,
                      COALESCE(SUM(points_awarded) FILTER (WHERE source='quiz'),0)  AS pts_quiz,
                      COALESCE(SUM(points_awarded) FILTER (WHERE source='tap'),0)   AS pts_tap
               FROM wheel_spins
               WHERE spin_at >= NOW() - ($1 || ' days')::interval""",
            str(days),
        )
    return {
        "window_days": days,
        "balances": {
            "accounts":      int(balances["accounts"] or 0),
            "total_balance": float(balances["total_balance"] or 0),
            "avg_balance":   float(balances["avg_balance"] or 0),
            "max_balance":   float(balances["max_balance"] or 0),
            "funded":        int(balances["funded"] or 0),
            "locked":        int(balances["locked"] or 0),
        },
        "volumes_by_type": [
            {
                "type": v["type"],
                "count": int(v["n"]),
                "total": float(v["total"] or 0),
                "completed": int(v["completed"] or 0),
                "pending":   int(v["pending"] or 0),
                "cancelled": int(v["cancelled"] or 0),
            } for v in vols
        ],
        "timeseries": [
            {
                "day": t["day"].isoformat(),
                "count": int(t["n"]),
                "inflow":  float(t["inflow"] or 0),
                "outflow": float(t["outflow"] or 0),
            } for t in ts
        ],
        "top_funded": [
            {
                "user_id": r["user_id"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or r["user_id"],
                "email": r["email"], "avatar": r["avatar"], "is_pro": bool(r["is_pro"]),
                "balance": float(r["balance"] or 0),
            } for r in top
        ],
        "anomalies": {
            "large_withdrawals": [
                {"tx_id": r["tx_id"], "user_id": r["from_user_id"],
                 "amount": float(r["amount"]), "status": r["status"],
                 "at": r["created_at"].isoformat() if r["created_at"] else None}
                for r in large_withdraws
            ],
            "stuck_pending_over_24h": [
                {"tx_id": r["tx_id"], "type": r["type"],
                 "from": r["from_user_id"], "to": r["to_user_id"],
                 "amount": float(r["amount"]),
                 "at": r["created_at"].isoformat() if r["created_at"] else None}
                for r in stuck_pending
            ],
            "send_spam_last_1h": [
                {"user_id": r["from_user_id"], "count": int(r["n"]),
                 "total": float(r["total"] or 0)}
                for r in send_spammers
            ],
        },
        "engagement_points": {
            "total_points":     int(pts["total_points"] or 0),
            "total_spins":      int(pts["spins"] or 0),
            "unique_players":   int(pts["players"] or 0),
            "by_source": {
                "wheel": int(pts["pts_wheel"] or 0),
                "quiz":  int(pts["pts_quiz"] or 0),
                "tap":   int(pts["pts_tap"] or 0),
            },
        },
    }


@router.get("/marketplace/revenue-summary")
async def admin_marketplace_revenue_summary(request: Request, days: int = 30):
    """iter178 — Compact widget for the admin footer (CEO request).
    Returns Marketplace-only revenue split (commissions + boosts) in USD
    canonical, over the last N days."""
    await require_admin(request)
    days = max(1, min(int(days), 365))
    pool = await get_pool()
    async with pool.acquire() as conn:
        comm = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount),0)::numeric AS total, COUNT(*) AS n
               FROM transactions
               WHERE type='marketplace_commission' AND status='completed'
                 AND currency='USD'
                 AND created_at > NOW() - ($1 || ' days')::interval""",
            str(days))
        boost = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount),0)::numeric AS total, COUNT(*) AS n
               FROM transactions
               WHERE type='product_boost' AND status='completed'
                 AND currency='USD'
                 AND created_at > NOW() - ($1 || ' days')::interval""",
            str(days))
        active_disputes = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE escrow_status='disputed'")
        active_holds = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE escrow_status='held'")
        total_held_usd = await conn.fetchval(
            "SELECT COALESCE(SUM(amount),0)::numeric FROM orders WHERE escrow_status='held'")
    total_usd = (comm['total'] or 0) + (boost['total'] or 0)
    return {
        "window_days": days,
        "currency_canonical": "USD",
        "commissions_usd": str(comm['total']),
        "commissions_count": int(comm['n']),
        "boosts_usd": str(boost['total']),
        "boosts_count": int(boost['n']),
        "total_usd": str(total_usd),
        "active_disputes": int(active_disputes or 0),
        "active_holds": int(active_holds or 0),
        "total_held_usd": str(total_held_usd or 0),
    }


# ──────────────────────────────────────────────────────────────────────────
#  REVENUE DASHBOARD — iter91 (monetization phase 1)
#  Aggregates every source of revenue the platform collects:
#    • wallet fees (send + withdraw)     → transactions rows type IN ('fee_send')
#                                          OR fee column on 'withdrawal' rows
#    • deposits (gross inflow)           → type='deposit' status='completed'
#    • subscriptions / PRO               → subscriptions + transactions type='subscription'
#    • marketplace commissions (future)  → type='commission' (placeholder)
# ──────────────────────────────────────────────────────────────────────────

@router.get("/revenue/overview")
async def admin_revenue_overview(request: Request, days: int = 30):
    await require_admin(request)
    days = max(1, min(int(days), 365))
    pool = await get_pool()
    async with pool.acquire() as conn:
        # --- Revenue by source (in the window) ---------------------------
        # SEND fees (from fee_send shadow rows)
        send_fees = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount),0)::numeric AS total,
                      COUNT(*) AS n
               FROM transactions
               WHERE type='fee_send' AND status='completed'
                 AND created_at > NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        # WITHDRAW fees (fee column on withdrawal rows)
        wd_fees = await conn.fetchrow(
            """SELECT COALESCE(SUM(fee),0)::numeric AS total,
                      COUNT(*) AS n
               FROM transactions
               WHERE type='withdrawal' AND fee > 0
                 AND status IN ('completed','processing')
                 AND created_at > NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        # DEPOSITS completed (gross inflow; not revenue per se but business KPI)
        dep = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount),0)::numeric AS total,
                      COUNT(*) AS n
               FROM transactions
               WHERE type='deposit' AND status='completed'
                 AND created_at > NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        # PRO subscriptions revenue
        try:
            pro = await conn.fetchrow(
                """SELECT COALESCE(SUM(paid_amount_usd),0)::numeric AS total,
                          COUNT(*) AS n
                   FROM subscriptions
                   WHERE status='active' AND is_trial = FALSE
                     AND starts_at > NOW() - ($1 || ' days')::interval""",
                str(days),
            )
            by_plan = await conn.fetch(
                """SELECT plan_type,
                          COUNT(*) AS n,
                          COALESCE(SUM(paid_amount_usd),0)::numeric AS total_usd
                   FROM subscriptions
                   WHERE status='active' AND is_trial = FALSE
                     AND starts_at > NOW() - ($1 || ' days')::interval
                   GROUP BY plan_type ORDER BY total_usd DESC""",
                str(days),
            )
            trials = await conn.fetchval(
                """SELECT COUNT(*) FROM subscriptions
                   WHERE is_trial = TRUE
                     AND starts_at > NOW() - ($1 || ' days')::interval""",
                str(days),
            )
            mrr_usd = await conn.fetchval(
                """SELECT COALESCE(SUM(
                     CASE WHEN duration_days > 0 THEN (paid_amount_usd * 30.0) / duration_days ELSE 0 END
                   ),0)::numeric
                   FROM subscriptions
                   WHERE status='active' AND is_trial = FALSE
                     AND expires_at > NOW()""",
            )
        except Exception:
            pro = {"total": 0, "n": 0}
            by_plan = []
            trials = 0
            mrr_usd = 0
        # TIMESERIES per day per source
        series = await conn.fetch(
            """SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                      COALESCE(SUM(CASE WHEN type='fee_send' THEN amount ELSE 0 END),0)::numeric AS fee_send,
                      COALESCE(SUM(CASE WHEN type='withdrawal' THEN fee ELSE 0 END),0)::numeric  AS fee_withdraw,
                      COALESCE(SUM(CASE WHEN type='subscription' THEN amount ELSE 0 END),0)::numeric AS subs
               FROM transactions
               WHERE created_at > NOW() - ($1 || ' days')::interval
                 AND (type IN ('fee_send','subscription')
                      OR (type='withdrawal' AND fee > 0))
               GROUP BY 1 ORDER BY 1""",
            str(days),
        )
        # TOP payers
        top_payers = await conn.fetch(
            """SELECT t.user_id,
                      u.email, u.first_name, u.last_name, u.is_pro,
                      COALESCE(SUM(t.contrib),0)::numeric AS total
               FROM (
                 SELECT from_user_id AS user_id,
                        (CASE WHEN type='fee_send' THEN amount
                              WHEN type='withdrawal' THEN fee
                              WHEN type='subscription' THEN amount
                              ELSE 0 END)::numeric AS contrib
                 FROM transactions
                 WHERE created_at > NOW() - ($1 || ' days')::interval
                   AND (type IN ('fee_send','subscription')
                        OR (type='withdrawal' AND fee > 0))
                   AND from_user_id IS NOT NULL
               ) t
               LEFT JOIN users u ON u.user_id = t.user_id
               GROUP BY t.user_id, u.email, u.first_name, u.last_name, u.is_pro
               ORDER BY total DESC LIMIT 10""",
            str(days),
        )
    send_fees_total = float((send_fees or {}).get("total", 0) or 0)
    wd_fees_total = float((wd_fees or {}).get("total", 0) or 0)
    pro_total = float((pro or {}).get("total", 0) or 0)
    grand_total = send_fees_total + wd_fees_total + pro_total
    return {
        "window_days": days,
        "kpis": {
            "total_revenue_usd": round(grand_total, 4),
            "send_fees_usd":     round(send_fees_total, 4),
            "withdraw_fees_usd": round(wd_fees_total, 4),
            "subscription_usd":  round(pro_total, 4),
            "deposits_gross_usd": float((dep or {}).get("total", 0) or 0),
            "mrr_usd": round(float(mrr_usd or 0), 2),
            "active_trials": int(trials or 0),
            "counts": {
                "send_fees":  int((send_fees or {}).get("n", 0) or 0),
                "withdraw_fees": int((wd_fees or {}).get("n", 0) or 0),
                "subscriptions": int((pro or {}).get("n", 0) or 0),
                "deposits":   int((dep or {}).get("n", 0) or 0),
            },
        },
        "by_plan": [
            {
                "plan_type": p["plan_type"],
                "count": int(p["n"]),
                "revenue_usd": float(p["total_usd"] or 0),
            } for p in by_plan
        ],
        "timeseries": [
            {
                "day": r["day"],
                "fee_send":     float(r["fee_send"] or 0),
                "fee_withdraw": float(r["fee_withdraw"] or 0),
                "subscription": float(r["subs"] or 0),
                "total": float((r["fee_send"] or 0) + (r["fee_withdraw"] or 0) + (r["subs"] or 0)),
            } for r in series
        ],
        "top_payers": [
            {
                "user_id": r["user_id"],
                "name": (f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or "—"),
                "email": r["email"], "is_pro": bool(r["is_pro"]),
                "total_usd": float(r["total"] or 0),
            } for r in top_payers
        ],
    }


@router.post("/wallet/adjust")
async def admin_adjust_wallet(req: AdminWalletAdjustRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", req.user_id)
            if not wallet:
                raise HTTPException(status_code=404, detail="Wallet not found")
            
            amount = Decimal(str(req.amount))
            await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                               amount, datetime.now(timezone.utc), req.user_id)
            
            tx_id = f"adj_{uuid.uuid4().hex[:16]}"
            tx_type = 'admin_credit' if amount > 0 else 'admin_debit'
            await conn.execute("""
                INSERT INTO transactions (tx_id, to_user_id, type, amount, status, notes, admin_notes)
                VALUES ($1, $2, $3, $4, 'completed', $5, $6)
            """, tx_id, req.user_id, tx_type, abs(amount), req.notes, f"Adjusted by admin {admin['user_id']}")
            
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'admin_wallet_adjust', 'wallet', $2)
            """, admin['user_id'], f'{{"target": "{req.user_id}", "amount": "{amount}", "tx_id": "{tx_id}"}}')
            
            new_balance = await conn.fetchval("SELECT balance FROM wallets WHERE user_id = $1", req.user_id)
            return {"message": "Wallet adjusted", "tx_id": tx_id, "new_balance": str(new_balance)}


@router.get("/transactions")
async def admin_list_transactions(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), 
                                   status: Optional[str] = None, type: Optional[str] = None,
                                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                                   user_id: Optional[str] = None):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base = "FROM transactions WHERE 1=1"
        params = []
        if status:
            params.append(status)
            base += f" AND status = ${len(params)}"
        if type:
            params.append(type)
            base += f" AND type = ${len(params)}"
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            except ValueError:
                dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params.append(dt_from); base += f" AND created_at >= ${len(params)}"
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            except ValueError:
                dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params.append(dt_to); base += f" AND created_at <= ${len(params)}"
        if user_id:
            params.append(user_id); base += f" AND (from_user_id = ${len(params)} OR to_user_id = ${len(params)})"

        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        params_q = params + [limit, offset]
        rows = await conn.fetch(f"SELECT * {base} ORDER BY created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}", *params_q)

        # Aggregate volume summary (respects filters except pagination)
        volume = await conn.fetchval(f"SELECT COALESCE(SUM(amount), 0) {base}", *params)

        txs = []
        for row in rows:
            tx = dict(row)
            tx['amount'] = str(tx['amount'])
            tx['fee'] = str(tx['fee'])
            tx['created_at'] = tx['created_at'].isoformat()
            txs.append(tx)

        return {"transactions": txs, "total": count, "page": page, "limit": limit,
                "volume_total": str(volume)}


@router.put("/transactions/{tx_id}")
async def admin_update_transaction(tx_id: str, req: AdminUpdateTxRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow("SELECT * FROM transactions WHERE tx_id = $1", tx_id)
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        await conn.execute("UPDATE transactions SET status = $1, admin_notes = $2 WHERE tx_id = $3",
                           req.status, req.admin_notes, tx_id)
        
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_update_tx', 'transactions', $2)
        """, admin['user_id'], f'{{"tx_id": "{tx_id}", "status": "{req.status}"}}')
        
        return {"message": "Transaction updated"}


@router.get("/audit-logs")
async def get_audit_logs(request: Request, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM audit_logs")
        rows = await conn.fetch("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT $1 OFFSET $2", limit, offset)
        logs = []
        for row in rows:
            l = dict(row)
            l['created_at'] = l['created_at'].isoformat()
            logs.append(l)
        return {"logs": logs, "total": count, "page": page, "limit": limit}


@router.put("/users/{user_id}/profile")
async def admin_edit_profile(user_id: str, req: AdminEditProfileRequest, request: Request):
    """Edit core user profile fields: email, phone, username, first/last name."""
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id, email, username FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        updates, values = [], []

        def add(field, value):
            values.append(value)
            updates.append(f"{field} = ${len(values)}")

        if req.email is not None and req.email.lower() != (target['email'] or '').lower():
            new_email = req.email.lower().strip()
            dup = await conn.fetchrow("SELECT 1 FROM users WHERE email = $1 AND user_id <> $2", new_email, user_id)
            if dup:
                raise HTTPException(status_code=400, detail="Email already in use")
            add("email", new_email)
        if req.username is not None and req.username != (target['username'] or ''):
            new_username = req.username.strip()[:64]
            dup = await conn.fetchrow("SELECT 1 FROM users WHERE username = $1 AND user_id <> $2", new_username, user_id)
            if dup:
                raise HTTPException(status_code=400, detail="Username already in use")
            add("username", new_username)
        if req.phone_number is not None:
            add("phone_number", req.phone_number.strip()[:32])
        if req.first_name is not None:
            add("first_name", req.first_name.strip()[:100])
        if req.last_name is not None:
            add("last_name", req.last_name.strip()[:100])

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        values.append(datetime.now(timezone.utc))
        updates.append(f"updated_at = ${len(values)}")
        values.append(user_id)
        sql = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ${len(values)}"
        await conn.execute(sql, *values)

        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_edit_profile', 'users', $2)
        """, admin['user_id'], f'{{"target": "{user_id}"}}')

        updated = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return user_to_response(dict(updated))


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(user_id: str, req: AdminResetPasswordRequest, request: Request):
    """Admin-initiated password reset. Logs out all sessions."""
    admin = await require_admin(request)
    if len(req.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    new_hash = hash_password(req.new_password)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        await conn.execute("UPDATE users SET password_hash = $1, password_changed_at = NOW(), updated_at = NOW() WHERE user_id = $2",
                           new_hash, user_id)
        await conn.execute("DELETE FROM user_sessions WHERE user_id = $1", user_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_reset_password', 'users', $2)
        """, admin['user_id'], f'{{"target": "{user_id}"}}')
    return {"status": "ok", "message": "Password reset. User must log in again."}


@router.post("/users/{user_id}/suspend")
async def admin_suspend_user(user_id: str, req: AdminSuspendRequest, request: Request):
    """Suspend (ban=false) or permanently ban (ban=true) a user."""
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target['user_id'] == admin['user_id']:
            raise HTTPException(status_code=400, detail="Cannot suspend yourself")
        await conn.execute(
            "UPDATE users SET is_active = FALSE, is_online = FALSE, updated_at = NOW() WHERE user_id = $1",
            user_id
        )
        await conn.execute("DELETE FROM user_sessions WHERE user_id = $1", user_id)
        label = "banned" if req.ban else "suspended"
        reason = (req.reason or "").strip()[:500]
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, $2, 'users', $3)
        """, admin['user_id'], f'admin_{label}', f'{{"target": "{user_id}", "reason": "{reason}"}}')
        try:
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, $3, $4, $5)
            """, f"notif_{uuid.uuid4().hex[:12]}", user_id, f"account_{label}",
               "Account suspended" if not req.ban else "Account banned",
               reason or ("Your account has been suspended by an administrator." if not req.ban else "Your account has been permanently banned."))
        except Exception:
            pass
    return {"status": "ok", "action": label}


@router.post("/users/{user_id}/reactivate")
async def admin_reactivate_user(user_id: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        await conn.execute("UPDATE users SET is_active = TRUE, updated_at = NOW() WHERE user_id = $1", user_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_reactivate', 'users', $2)
        """, admin['user_id'], f'{{"target": "{user_id}"}}')
    return {"status": "ok"}


@router.get("/users/{user_id}/transactions")
async def admin_user_transactions(user_id: str, request: Request,
                                   page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM transactions WHERE from_user_id = $1 OR to_user_id = $1
        """, user_id)
        rows = await conn.fetch("""
            SELECT * FROM transactions WHERE from_user_id = $1 OR to_user_id = $1
            ORDER BY created_at DESC LIMIT $2 OFFSET $3
        """, user_id, limit, offset)
        txs = []
        for row in rows:
            tx = dict(row)
            tx['amount'] = str(tx['amount'])
            tx['fee'] = str(tx['fee'])
            tx['created_at'] = tx['created_at'].isoformat()
            txs.append(tx)
        return {"transactions": txs, "total": count, "page": page, "limit": limit}


@router.get("/transactions/export")
async def admin_export_transactions(request: Request,
                                     status: Optional[str] = None, type: Optional[str] = None,
                                     date_from: Optional[str] = None, date_to: Optional[str] = None):
    """CSV export, supports filters. Max 10k rows for safety."""
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        base = "FROM transactions WHERE 1=1"
        params: list = []
        if status:
            params.append(status); base += f" AND status = ${len(params)}"
        if type:
            params.append(type); base += f" AND type = ${len(params)}"
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            except ValueError:
                dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params.append(dt_from); base += f" AND created_at >= ${len(params)}"
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            except ValueError:
                dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params.append(dt_to); base += f" AND created_at <= ${len(params)}"
        rows = await conn.fetch(f"SELECT * {base} ORDER BY created_at DESC LIMIT 10000", *params)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["tx_id", "type", "status", "from_user_id", "to_user_id", "amount",
                     "fee", "currency", "notes", "reference", "created_at"])
    for r in rows:
        writer.writerow([
            r['tx_id'], r['type'], r['status'], r['from_user_id'] or '', r['to_user_id'] or '',
            str(r['amount']), str(r['fee']), r['currency'],
            (r['notes'] or '').replace('\n', ' '), r['reference'] or '',
            r['created_at'].isoformat()
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=japap_transactions_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


@router.get("/kyc/pending-count")
async def kyc_pending_count(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM kyc_verifications WHERE status = 'pending'")
    return {"pending": count or 0}


@router.get("/games/stats")
async def admin_games_stats(request: Request):
    """Detailed stats for the JAPAP Spin admin tab."""
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        plays = await conn.fetchval("SELECT COUNT(*) FROM game_plays WHERE game_type = 'spin'")
        plays_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM game_plays WHERE game_type = 'spin' AND created_at > NOW() - INTERVAL '24 hours'")
        total_rewarded = await conn.fetchval(
            "SELECT COALESCE(SUM(reward), 0) FROM game_plays WHERE game_type = 'spin'")
        active_players = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM game_plays WHERE game_type = 'spin' AND created_at > NOW() - INTERVAL '30 days'")
        top = await conn.fetch("""
            SELECT u.user_id, u.first_name, u.last_name, u.username, u.avatar,
                   SUM(gp.reward) AS total_won, COUNT(gp.id) AS plays
            FROM game_plays gp JOIN users u ON gp.user_id = u.user_id
            WHERE gp.game_type = 'spin'
            GROUP BY u.user_id, u.first_name, u.last_name, u.username, u.avatar
            ORDER BY total_won DESC LIMIT 10
        """)
    return {
        "plays_total": plays or 0,
        "plays_24h": plays_24h or 0,
        "total_rewarded_xaf": str(total_rewarded or 0),
        "active_players_30d": active_players or 0,
        "top_winners": [
            {"user_id": r['user_id'], "name": f"{r['first_name']} {r['last_name']}".strip() or r['username'],
             "avatar": r['avatar'] or "", "total_won": str(r['total_won']), "plays": r['plays']}
            for r in top
        ],
    }


@router.get("/notifications")
async def get_all_notifications(request: Request, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM notifications WHERE user_id = $1", user['user_id'])
        rows = await conn.fetch("""
            SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
        notifs = []
        for row in rows:
            n = dict(row)
            n['created_at'] = n['created_at'].isoformat()
            notifs.append(n)
        return {"notifications": notifs, "total": count, "page": page, "limit": limit}


# ╔══════════════════════════════════════════════════════════════════╗
# ║ iter168 — Public URL audit (anti-regression on email domains)    ║
# ║ Detects any outgoing/clicked URL pointing to a non-canonical     ║
# ║ JAPAP host (japap.app, *.preview.emergentagent.com on prod, …).   ║
# ╚══════════════════════════════════════════════════════════════════╝
@router.get("/public-url-audit")
async def public_url_audit(request: Request,
                           days: int = Query(30, ge=1, le=365),
                           limit: int = Query(50, ge=1, le=500)):
    """Scan email logs and source files for non-canonical JAPAP URLs.

    iter168.2 — Logic extracted to `services/public_url_audit_service.py`
    so the weekly cron worker can reuse it without duplicating SQL/grep
    code.
    """
    await require_admin(request)
    from services.public_url_audit_service import run_audit
    return await run_audit(days=days, limit=limit)


@router.post("/public-url-audit/send-alert")
async def public_url_audit_send_alert(request: Request, force: bool = False):
    """iter168.2 — Manually trigger the weekly audit + email superadmins.

    `force=true` bypasses both the once-per-week idempotency lock AND the
    'skip-when-clean' rule. Used to verify the email pipeline end-to-end."""
    await require_admin(request)
    from services.public_url_audit_worker import run_alert_pass
    return await run_alert_pass(force=bool(force))


@router.post("/crowdfunding/recruit-reminders/run")
async def crowdfunding_run_recruit_reminders(request: Request, force: bool = False):
    """iter170 — Manually run the Crowdfunding recruiter reminder cron.

    Sends `visited_no_vote` and `voted_no_share` reminder emails for the
    active cycle. Idempotent per (user, cycle, kind) — re-runs never spam
    the same user. `force` is reserved (no bypass of per-user lock)."""
    await require_admin(request)
    from services.crowdfunding_recruit_remind_worker import run_reminder_pass
    return await run_reminder_pass(force=bool(force))

