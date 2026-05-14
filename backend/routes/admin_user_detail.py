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
from database import get_pool

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


# iter240l-gamefix — Game activity rebuilt from `transactions` (source of truth).
# Mapping (verified against prod DB, Feb 2026):
#   quiz_challenge_lock         → quiz play (USD bet locked)
#   quiz_challenge_lock_double  → quiz play (USD bet locked, double mode)
#   quiz_challenge_release      → quiz won (USD prize released to winner)
#   quiz_challenge_refund       → quiz refunded (tie / cancelled — counted as played)
#   quiz_challenge_bonus        → quiz won (PTS bonus, daily / solo)
#   game_bet                    → wheel / spin play (XAF, distinguished via notes)
#   game_reward                 → wheel / spin / quiz win (XAF, distinguished via notes)
#   referral_bonus / send / …   → ignored (not games)
async def _build_game_activity_from_tx(conn, user_id: str) -> dict:
    """Aggregate complete game stats from the `transactions` table.

    Returns a dict with sections for quiz / fortune_wheel / mini_spin / staking,
    plus an `all_transaction_types` debug breakdown that lists every type the
    user is involved in (for admin troubleshooting)."""
    sql = """
        WITH user_tx AS (
            SELECT type, currency, amount, notes, created_at
            FROM transactions
            WHERE (from_user_id = $1 OR to_user_id = $1)
              AND COALESCE(status, 'completed') IN
                  ('completed','success','SUCCESS','COMPLETED','paid','released','done','ok')
        ),
        quiz_plays AS (
            SELECT * FROM user_tx
            WHERE type IN ('quiz_challenge_lock','quiz_challenge_lock_double','quiz_challenge_bonus')
               OR (type = 'game_bet'    AND COALESCE(notes,'') ILIKE '%quiz%')
               OR (type = 'game_reward' AND COALESCE(notes,'') ILIKE '%quiz%')
        ),
        wheel_plays AS (
            SELECT * FROM user_tx
            WHERE (type = 'game_bet'    AND COALESCE(notes,'') ~* '(wheel|roue|fortune)')
               OR (type = 'game_reward' AND COALESCE(notes,'') ~* '(wheel|roue|fortune)')
        ),
        spin_plays AS (
            SELECT * FROM user_tx
            WHERE  type IN ('game_bet','game_reward')
              AND  COALESCE(notes,'') ILIKE '%spin%'
              AND  COALESCE(notes,'') NOT ILIKE '%quiz%'
              AND  COALESCE(notes,'') !~* '(wheel|roue|fortune)'
        )
        SELECT json_build_object(
            'quiz', json_build_object(
                'total_played',
                    (SELECT COUNT(*) FROM quiz_plays
                       WHERE type IN ('quiz_challenge_lock','quiz_challenge_lock_double',
                                      'quiz_challenge_bonus','game_bet')),
                'total_won_usd',
                    (SELECT COALESCE(SUM(amount),0) FROM user_tx
                       WHERE type = 'quiz_challenge_release' AND currency = 'USD'),
                'total_won_pts',
                    (SELECT COALESCE(SUM(amount),0) FROM user_tx
                       WHERE type = 'quiz_challenge_bonus' AND currency = 'PTS'),
                'total_refunded_usd',
                    (SELECT COALESCE(SUM(amount),0) FROM user_tx
                       WHERE type = 'quiz_challenge_refund' AND currency = 'USD'),
                'last_played_at',
                    (SELECT MAX(created_at) FROM quiz_plays)
            ),
            'fortune_wheel', json_build_object(
                'total_played',
                    (SELECT COUNT(*) FROM wheel_plays WHERE type = 'game_bet'),
                'total_won',
                    (SELECT COALESCE(SUM(amount),0) FROM wheel_plays WHERE type = 'game_reward'),
                'last_played_at',
                    (SELECT MAX(created_at) FROM wheel_plays)
            ),
            'mini_spin', json_build_object(
                'total_played',
                    (SELECT COUNT(*) FROM spin_plays WHERE type = 'game_bet'),
                'total_won',
                    (SELECT COALESCE(SUM(amount),0) FROM spin_plays WHERE type = 'game_reward'),
                'last_played_at',
                    (SELECT MAX(created_at) FROM spin_plays)
            ),
            'all_transaction_types', (
                SELECT COALESCE(json_agg(row_to_json(t) ORDER BY t.n DESC), '[]'::json)
                FROM (
                    SELECT type, currency,
                           COUNT(*)::int AS n,
                           COALESCE(SUM(amount),0)::float AS sum_amount,
                           MAX(created_at)                AS last_at
                    FROM user_tx
                    GROUP BY type, currency
                ) t
            )
        ) AS payload
    """
    try:
        payload = await conn.fetchval(sql, user_id)
    except Exception:
        payload = None

    # Staking — still pulled from staking_positions (no tx type for stakes).
    stake_active = await _safe_fetch(conn,
        "SELECT COUNT(*) FROM staking_positions WHERE user_id = $1 AND status = 'active'",
        user_id, default=0) or 0
    stake_total = await _safe_fetch(conn,
        "SELECT COALESCE(SUM(amount_mir), 0) FROM staking_positions WHERE user_id = $1",
        user_id, default=0) or 0

    # asyncpg returns JSON as str sometimes — normalise.
    import json as _json
    if isinstance(payload, str):
        try:
            payload = _json.loads(payload)
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        payload = {
            "quiz":          {"total_played": 0, "total_won_usd": 0, "total_won_pts": 0,
                              "total_refunded_usd": 0, "last_played_at": None},
            "fortune_wheel": {"total_played": 0, "total_won": 0, "last_played_at": None},
            "mini_spin":     {"total_played": 0, "total_won": 0, "last_played_at": None},
            "all_transaction_types": [],
        }

    # Back-compat field expected by older UI versions.
    payload["quiz"]["total_won"] = float(
        (payload["quiz"].get("total_won_usd") or 0)
        + (payload["quiz"].get("total_won_pts") or 0)
    )

    payload["staking"] = {
        "active_stakes": int(stake_active),
        "total_staked":  float(stake_total),
    }
    return payload


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
            "SELECT balance, currency, is_locked FROM wallets WHERE user_id = $1",
            user_id,
        )
        wallet_d = dict(wallet) if wallet else {"balance": 0, "currency": "USD", "is_locked": False}

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
            "SELECT status, created_at AS submitted_at, reviewed_at AS validated_at, rejection_reason "
            "FROM kyc_verifications WHERE user_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            user_id,
        )
        kyc_row = kyc[0] if kyc else {"status": "not_submitted"}

        # iter240l-gamefix — game_activity rebuilt from the `transactions` table
        # (single source of truth). Old tables (quiz_game_history, fortune_wheel_spins,
        # mini_spin_history) are unreliable / partially populated → ignored on purpose.
        game_activity = await _build_game_activity_from_tx(conn, user_id)

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
        "game_activity": game_activity,
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
    import uuid, json as _json
    notif_id = f"notif_{uuid.uuid4().hex[:16]}"
    data_payload = _json.dumps({"from_admin": admin["user_id"], "kind": req.type})
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO notifications (notif_id, user_id, type, title, message, data) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                notif_id, user_id, "admin_message",
                "Message de l'administration", req.message, data_payload,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"notification insert failed: {e}")
    return {"ok": True, "notif_id": notif_id}


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
