"""iter240k — Admin "User Detail" panel endpoints.

ADDITIVE — never touches existing /api/admin/users endpoints. Mounted under
the same /api/admin/users prefix with new sub-routes:

  GET  /api/admin/users/{user_id}/detail            full dossier
  GET  /api/admin/users/{user_id}/notes             admin internal notes
  POST /api/admin/users/{user_id}/notes             add note
  POST /api/admin/users/{user_id}/restrict          add restriction
  POST /api/admin/users/{user_id}/unrestrict        lift restriction
  POST /api/admin/users/{user_id}/reset-game-limits remove per-day caps
  POST /api/admin/users/{user_id}/send-notification ping the user in-app

Payment providers (Hubtel/Paystack/USDT/Wave/MoMo) are NOT TOUCHED.
"""
from datetime import datetime, timezone
from typing import Optional, List, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from db import get_pool

router = APIRouter(prefix="/api/admin/users", tags=["admin-user-detail"])


async def _require_admin(request: Request):
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin only.")
    return user


# ─── Models ──────────────────────────────────────────────────────────────
class RestrictRequest(BaseModel):
    type: Literal["games", "wallet", "posts", "all"]
    reason: Optional[str] = Field(None, max_length=500)
    duration_days: Optional[int] = Field(None, ge=1, le=3650)


class UnrestrictRequest(BaseModel):
    type: Optional[Literal["games", "wallet", "posts", "all"]] = None
    reason: Optional[str] = Field(None, max_length=500)


class NoteRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)


class NotificationRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    type: Literal["info", "warning", "success"] = "info"


# ─── Helpers ─────────────────────────────────────────────────────────────
async def _safe_fetch(conn, sql: str, *args, default=None):
    """Run a query and gracefully return `default` if the table doesn't exist."""
    try:
        return await conn.fetchval(sql, *args)
    except Exception:
        return default


async def _safe_fetch_rows(conn, sql: str, *args) -> List[dict]:
    try:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]
    except Exception:
        return []


def _serialize(obj):
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# ─── Endpoints ───────────────────────────────────────────────────────────
@router.get("/{user_id}/detail")
async def get_user_detail(user_id: str, request: Request):
    """Return everything the admin needs in one round-trip."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        u = dict(user)
        u.pop("password_hash", None)
        u.pop("totp_secret", None)

        wallet = await conn.fetchrow(
            "SELECT balance, currency, total_earned, total_withdrawn FROM wallets WHERE user_id = $1",
            user_id,
        )
        wallet_d = dict(wallet) if wallet else {"balance": 0, "currency": "USD"}

        transactions = await _safe_fetch_rows(
            conn,
            "SELECT tx_id, type, amount, currency, status, created_at, notes "
            "FROM transactions WHERE from_user_id = $1 OR to_user_id = $1 "
            "ORDER BY created_at DESC LIMIT 20",
            user_id,
        )

        # KYC
        kyc = await _safe_fetch_rows(
            conn,
            "SELECT status, submitted_at, validated_at, rejection_reason "
            "FROM kyc_verifications WHERE user_id = $1 "
            "ORDER BY submitted_at DESC LIMIT 1",
            user_id,
        )
        kyc_row = kyc[0] if kyc else {"status": "not_submitted"}

        # Game activity — graceful if tables don't exist
        quiz_total = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM quiz_game_history WHERE user_id = $1", user_id, default=0) or 0
        quiz_won = await _safe_fetch(conn,
            "SELECT COALESCE(SUM(prize_amount), 0) FROM quiz_game_history WHERE user_id = $1", user_id, default=0) or 0
        quiz_last = await _safe_fetch(conn,
            "SELECT MAX(created_at) FROM quiz_game_history WHERE user_id = $1", user_id)

        wheel_total = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM fortune_wheel_spins WHERE user_id = $1", user_id, default=0) or 0
        wheel_won = await _safe_fetch(conn,
            "SELECT COALESCE(SUM(prize_amount), 0) FROM fortune_wheel_spins WHERE user_id = $1", user_id, default=0) or 0
        wheel_last = await _safe_fetch(conn,
            "SELECT MAX(created_at) FROM fortune_wheel_spins WHERE user_id = $1", user_id)

        spin_total = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM mini_spin_history WHERE user_id = $1", user_id, default=0) or 0
        spin_won = await _safe_fetch(conn,
            "SELECT COALESCE(SUM(prize_amount), 0) FROM mini_spin_history WHERE user_id = $1", user_id, default=0) or 0
        spin_last = await _safe_fetch(conn,
            "SELECT MAX(created_at) FROM mini_spin_history WHERE user_id = $1", user_id)

        stake_active = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM staking_positions WHERE user_id = $1 AND status = 'active'",
            user_id, default=0) or 0
        stake_total = await _safe_fetch(conn,
            "SELECT COALESCE(SUM(amount), 0) FROM staking_positions WHERE user_id = $1",
            user_id, default=0) or 0

        # Restrictions
        restrictions = await _safe_fetch_rows(
            conn,
            "SELECT * FROM user_restrictions WHERE user_id = $1 ORDER BY created_at DESC",
            user_id,
        )

        # Posts
        posts_total = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM posts WHERE user_id = $1", user_id, default=0) or 0
        posts_likes = await _safe_fetch(conn,
            "SELECT COALESCE(SUM(likes_count), 0) FROM posts WHERE user_id = $1", user_id, default=0) or 0
        posts_last = await _safe_fetch(conn,
            "SELECT MAX(created_at) FROM posts WHERE user_id = $1", user_id)

        # Crowdfunding
        cf_projects = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM crowdfunding_projects WHERE user_id = $1 AND status NOT IN ('deleted','cancelled')",
            user_id, default=0) or 0
        cf_votes = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM crowdfunding_votes WHERE user_id = $1", user_id, default=0) or 0

        # Login history
        logins = await _safe_fetch_rows(
            conn,
            "SELECT ip_address, created_at, user_agent FROM login_history "
            "WHERE user_id = $1 ORDER BY created_at DESC LIMIT 5",
            user_id,
        )

        # Flags / reports
        flags = await _safe_fetch_rows(
            conn,
            "SELECT id, reason, created_at, status FROM user_reports "
            "WHERE reported_user_id = $1 ORDER BY created_at DESC LIMIT 10",
            user_id,
        )

        # Referrals
        ref_count = await _safe_fetch(conn,
            "SELECT COUNT(*) FROM users WHERE referred_by = $1", user_id, default=0) or 0
        ref_commissions = await _safe_fetch(conn,
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE to_user_id = $1 AND type LIKE 'referral%'",
            user_id, default=0) or 0

        # Notes (admin)
        notes = await _safe_fetch_rows(
            conn,
            "SELECT n.id, n.note, n.created_at, n.admin_id, "
            "  u.first_name AS admin_first_name, u.last_name AS admin_last_name "
            "FROM admin_user_notes n LEFT JOIN users u ON u.user_id = n.admin_id "
            "WHERE n.user_id = $1 ORDER BY n.created_at DESC",
            user_id,
        )

    return _serialize({
        "user": u,
        "kyc": kyc_row,
        "wallet": wallet_d,
        "transactions": transactions,
        "game_activity": {
            "quiz":          {"total_played": int(quiz_total), "total_won": float(quiz_won), "last_played_at": quiz_last},
            "fortune_wheel": {"total_played": int(wheel_total), "total_won": float(wheel_won), "last_played_at": wheel_last},
            "mini_spin":     {"total_played": int(spin_total), "total_won": float(spin_won), "last_played_at": spin_last},
            "staking":       {"active_stakes": int(stake_active), "total_staked": float(stake_total)},
        },
        "restrictions": restrictions,
        "posts":        {"total_posts": int(posts_total), "total_likes_received": int(posts_likes), "last_post_at": posts_last},
        "crowdfunding": {"projects_submitted": int(cf_projects), "votes_cast": int(cf_votes)},
        "login_history": logins,
        "flags":         flags,
        "referrals":    {"total_referred": int(ref_count), "total_commission": float(ref_commissions)},
        "notes":         notes,
    })


@router.post("/{user_id}/restrict")
async def add_restriction(user_id: str, req: RestrictRequest, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    expires_at = None
    if req.duration_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=req.duration_days)
    async with pool.acquire() as conn:
        rid = await conn.fetchval(
            "INSERT INTO user_restrictions (user_id, restriction_type, reason, created_by, expires_at) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            user_id, req.type, req.reason or "", admin["user_id"], expires_at,
        )
    return {"ok": True, "id": rid, "expires_at": expires_at.isoformat() if expires_at else None}


@router.post("/{user_id}/unrestrict")
async def lift_restriction(user_id: str, req: UnrestrictRequest, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if req.type:
            n = await conn.execute(
                "UPDATE user_restrictions SET lifted_at = NOW(), lifted_by = $1, lifted_reason = $2 "
                "WHERE user_id = $3 AND restriction_type = $4 AND lifted_at IS NULL",
                admin["user_id"], req.reason or "", user_id, req.type,
            )
        else:
            n = await conn.execute(
                "UPDATE user_restrictions SET lifted_at = NOW(), lifted_by = $1, lifted_reason = $2 "
                "WHERE user_id = $3 AND lifted_at IS NULL",
                admin["user_id"], req.reason or "", user_id,
            )
    return {"ok": True, "lifted": n.split()[-1] if isinstance(n, str) else 0}


@router.post("/{user_id}/reset-game-limits")
async def reset_game_limits(user_id: str, request: Request):
    """Reset per-day caps. Implementation graceful when tables vary across envs."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try every plausible table — never fail.
        for tbl in (
            "quiz_daily_limits", "fortune_wheel_daily_limits",
            "mini_spin_daily_limits", "game_daily_limits",
        ):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE user_id = $1", user_id)
            except Exception:
                pass
    return {"ok": True}


@router.post("/{user_id}/send-notification")
async def send_notification(user_id: str, req: NotificationRequest, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO notifications (user_id, type, title, body, created_by) "
                "VALUES ($1, $2, $3, $4, $5)",
                user_id, req.type, "Message de l'administration", req.message, admin["user_id"],
            )
        except Exception:
            # Schema variant — best-effort fallback.
            try:
                await conn.execute(
                    "INSERT INTO notifications (user_id, message, type) VALUES ($1, $2, $3)",
                    user_id, req.message, req.type,
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"notification insert failed: {e}")
    return {"ok": True}


@router.get("/{user_id}/notes")
async def list_notes(user_id: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT n.id, n.note, n.created_at, n.admin_id, "
            "  u.first_name AS admin_first_name, u.last_name AS admin_last_name "
            "FROM admin_user_notes n LEFT JOIN users u ON u.user_id = n.admin_id "
            "WHERE n.user_id = $1 ORDER BY n.created_at DESC",
            user_id,
        )
    return _serialize([dict(r) for r in rows])


@router.post("/{user_id}/notes")
async def add_note(user_id: str, req: NoteRequest, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        nid = await conn.fetchval(
            "INSERT INTO admin_user_notes (user_id, admin_id, note) VALUES ($1, $2, $3) RETURNING id",
            user_id, admin["user_id"], req.note,
        )
    return {"ok": True, "id": nid}
