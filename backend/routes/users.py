import uuid
import io
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user, user_to_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    about: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    avatar: Optional[str] = None
    avatar_thumb: Optional[str] = None
    cover_image: Optional[str] = None
    cover_image_mobile: Optional[str] = None
    cover_position_y: Optional[int] = None  # 0-100 percent, for drag repositioning


@router.get("/profile/{user_id}")
async def get_profile(user_id: str, request: Request):
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        wallet = await conn.fetchrow("SELECT balance, currency FROM wallets WHERE user_id = $1", user_id)
        # iter173 — Trust badge: Identity verified comes ONLY from an approved
        # KYC submission, NOT from the generic `is_verified` flag (which is
        # also set for email-verified users / superadmin seed). A separate
        # boolean lets the UI render a distinct "✅ Identité vérifiée" badge
        # on the profile, marketplace listings and post author chips —
        # boosting seller trust without conflating signals.
        kyc_verified = bool(await conn.fetchval(
            "SELECT 1 FROM kyc_verifications WHERE user_id = $1 "
            "AND status = 'approved' LIMIT 1", user_id))
        resp = user_to_response(dict(user))
        resp['kyc_verified'] = kyc_verified
        if wallet:
            resp['wallet_balance'] = str(wallet['balance'])
            resp['wallet_currency'] = wallet['currency']
        # Surface the full social layer: cached counts (O(1)) + the viewer's
        # is_following flag (O(1) index lookup) so the UI can render follow
        # buttons without an extra round-trip.
        resp['followers_count'] = user['followers_count'] or 0
        resp['following_count'] = user['following_count'] or 0
        resp['posts_count'] = user['posts_count'] or 0
        resp['cover_image'] = user['cover_image']
        resp['cover_position_y'] = user['cover_position_y'] if user['cover_position_y'] is not None else 50
        if viewer['user_id'] == user_id:
            resp['is_following'] = False
            resp['is_self'] = True
        else:
            is_following = await conn.fetchval(
                "SELECT 1 FROM user_follows WHERE follower_id = $1 AND followed_id = $2",
                viewer['user_id'], user_id,
            )
            resp['is_following'] = bool(is_following)
            resp['is_self'] = False
        return resp


@router.put("/profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    updates = {}
    for field in ['first_name', 'last_name', 'phone_number', 'about', 'gender',
                  'birthday', 'country', 'language', 'avatar', 'avatar_thumb',
                  'cover_image', 'cover_image_mobile', 'cover_position_y']:
        val = getattr(req, field, None)
        if val is not None:
            updates[field] = val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Clamp cover positioning to a sane 0-100 percent band so a stale client
    # can't poison the DB.
    if 'cover_position_y' in updates:
        try:
            updates['cover_position_y'] = max(0, min(100, int(updates['cover_position_y'])))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="cover_position_y must be an integer 0–100")

    updates['updated_at'] = datetime.now(timezone.utc)
    set_clause = ", ".join([f"{k} = ${i+1}" for i, k in enumerate(updates.keys())])
    values = list(updates.values()) + [user['user_id']]

    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET {set_clause} WHERE user_id = ${len(values)}", *values)
        updated = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user['user_id'])
        return user_to_response(dict(updated))


@router.get("/search")
async def search_users(request: Request, q: str = Query(..., min_length=1)):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        results = await conn.fetch("""
            SELECT user_id, username, email, first_name, last_name, avatar, is_online, is_pro, is_verified
            FROM users WHERE user_id != $1 AND is_active = TRUE AND (
                LOWER(username) LIKE $2 OR LOWER(first_name) LIKE $2 OR LOWER(last_name) LIKE $2 OR LOWER(email) LIKE $2
            ) LIMIT 20
        """, user['user_id'], f"%{q.lower()}%")
        return [dict(r) for r in results]


@router.get("/contacts")
async def get_contacts(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        contacts = await conn.fetch("""
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_online, u.is_pro, u.is_verified, u.last_seen
            FROM contacts c JOIN users u ON c.contact_user_id = u.user_id
            WHERE c.user_id = $1 AND c.status = 'active'
            ORDER BY u.is_online DESC, u.first_name ASC
        """, user['user_id'])
        return [dict(c) for c in contacts]


@router.post("/contacts/{contact_user_id}")
async def add_contact(contact_user_id: str, request: Request):
    user = await get_current_user(request)
    if contact_user_id == user['user_id']:
        raise HTTPException(status_code=400, detail="Cannot add yourself")
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1 AND is_active = TRUE", contact_user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        existing = await conn.fetchrow("SELECT id FROM contacts WHERE user_id = $1 AND contact_user_id = $2", user['user_id'], contact_user_id)
        if existing:
            return {"message": "Already in contacts"}
        await conn.execute("INSERT INTO contacts (user_id, contact_user_id) VALUES ($1, $2)", user['user_id'], contact_user_id)
        await conn.execute("INSERT INTO contacts (user_id, contact_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", contact_user_id, user['user_id'])
        return {"message": "Contact added"}


@router.delete("/contacts/{contact_user_id}")
async def remove_contact(contact_user_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM contacts WHERE user_id = $1 AND contact_user_id = $2", user['user_id'], contact_user_id)
        return {"message": "Contact removed"}



# ──────────────────────────────────────────────────────────────────────────
#  QR Code — iter91 (monetization phase 1)
#  Allows any user to be "scanned" for P2P wallet transfers.
# ──────────────────────────────────────────────────────────────────────────

@router.get("/me/qr-payload")
async def my_qr_payload(request: Request):
    """Return the JSON payload that another JAPAP user's camera will decode.

    Structure:
      {"t":"japap.pay","v":1,"uid":"user_xxx","name":"…","ccy":"XAF"}

    The QR image itself is served by GET /api/users/me/qr-code.png so the
    client never has to render QR code locally (fewer deps on the FE).
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        w = await conn.fetchrow("SELECT currency FROM wallets WHERE user_id = $1", user['user_id'])
    name = (
        (user.get("first_name", "") or "").strip() + " " +
        (user.get("last_name", "") or "").strip()
    ).strip() or user.get("username") or user.get("email", "")
    return {
        "t": "japap.pay",
        "v": 1,
        "uid": user['user_id'],
        "name": name[:64],
        "ccy": (w["currency"] if w else "XAF"),
    }


@router.get("/me/qr-code.png")
async def my_qr_code_png(request: Request):
    """Render the user's QR payload as a 512×512 PNG (Pillow)."""
    import json as _json
    payload = await my_qr_payload(request)
    body = _json.dumps(payload, separators=(",", ":"))
    try:
        import qrcode
        img = qrcode.make(body, box_size=12, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except Exception as e:
        logger.warning("QR render failed: %s", e)
        raise HTTPException(status_code=500, detail="QR unavailable")


@router.post("/resolve-qr")
async def resolve_qr(request: Request, body: dict):
    """Validate a decoded QR payload and return a lightweight profile
    suitable for pre-filling the send-money form."""
    await get_current_user(request)
    try:
        if not isinstance(body, dict) or body.get("t") != "japap.pay" or not body.get("uid"):
            raise HTTPException(status_code=400, detail="QR code JAPAP invalide.")
        uid = str(body["uid"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="QR code JAPAP invalide.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT user_id, first_name, last_name, username, avatar, is_pro
               FROM users WHERE user_id = $1""",
            uid,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    full_name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    return {
        "user_id": row["user_id"],
        "name": full_name or row["username"] or "",
        "username": row["username"],
        "avatar": row["avatar"],
        "is_pro": bool(row["is_pro"]),
    }



# ──────────────────────────────────────────────────────────────────────────
#  iter141nineF — Pay-as-you-Tip preferences
#  A creator (Pro or otherwise) configures the suggested tip amounts shown
#  as quick-tap chips on their posts/reels. Combined with the OG-rich share
#  URLs, every public post becomes a frictionless tip-jar.
# ──────────────────────────────────────────────────────────────────────────

class UpdateTipSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    presets: Optional[list] = None  # list of positive ints, max 6 amounts
    message: Optional[str] = None


@router.get("/{user_id}/tip-settings")
async def get_user_tip_settings(user_id: str):
    """PUBLIC — anyone visiting a post needs to know the author's preset
    chips. Only the small subset (enabled, presets, message) is returned."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT tip_enabled, tip_presets, tip_message, is_pro
                 FROM users WHERE user_id = $1""",
            user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    presets = row["tip_presets"]
    if isinstance(presets, str):
        import json as _json
        try:
            presets = _json.loads(presets)
        except Exception:
            presets = [100, 500, 1000]
    return {
        "user_id": user_id,
        "enabled": bool(row["tip_enabled"]),
        "presets": presets if isinstance(presets, list) else [100, 500, 1000],
        "message": row["tip_message"] or "",
        "is_pro": bool(row["is_pro"]),
    }


@router.put("/me/tip-settings")
async def update_my_tip_settings(req: UpdateTipSettingsRequest, request: Request):
    """Update the calling user's tip presets / on-off / thank-you message.
    Validation: presets must be positive ints (50..1_000_000), max 6 chips,
    de-duplicated, sorted ascending. Empty list means "default" — we keep
    the column non-empty so the UI always has something to render."""
    user = await get_current_user(request)
    fields, args = [], []
    if req.enabled is not None:
        fields.append(f"tip_enabled = ${len(args)+1}")
        args.append(bool(req.enabled))
    if req.presets is not None:
        cleaned = []
        for v in req.presets:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 50 <= n <= 1_000_000 and n not in cleaned:
                cleaned.append(n)
        if not cleaned:
            cleaned = [100, 500, 1000]
        cleaned = sorted(cleaned)[:6]
        import json as _json
        fields.append(f"tip_presets = ${len(args)+1}::jsonb")
        args.append(_json.dumps(cleaned))
    if req.message is not None:
        fields.append(f"tip_message = ${len(args)+1}")
        args.append((req.message or "").strip()[:280])
    if not fields:
        raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour.")
    args.append(user["user_id"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE user_id = ${len(args)}",
            *args,
        )
    # Return the fresh value for instant UI sync
    return await get_user_tip_settings(user["user_id"])



# ── iter150 — Profile Photo Gallery ─────────────────────────────────
@router.get("/{user_id}/photo-gallery")
async def photo_gallery(user_id: str, request: Request,
                        limit: int = Query(48, ge=1, le=120)):
    """Aggregate a user's filtered photos across `posts` + `stories`.

    Drives the new "Mini-portfolio" grid on each profile page (iter150).
    Each entry surfaces:
        • image_url   — direct CDN/upload URL to render
        • source      — "post" | "story" (for click-through routing)
        • source_id   — post_id / story_id for navigation
        • filter_preset — preset id (mono / sepia / vintage / ...) the
          author baked at upload time. Frontend uses it to render a
          small badge on the corner of each thumbnail.
        • created_at  — ISO timestamp
    """
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            "SELECT user_id, account_visibility FROM users WHERE user_id = $1",
            user_id,
        )
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable")

        # Privacy: private accounts only expose their gallery to followers
        # or to themselves. Re-uses the existing `account_visibility` column.
        is_self = (viewer["user_id"] == target["user_id"])
        if not is_self and (target.get("account_visibility") == "private"):
            is_follower = await conn.fetchval(
                "SELECT 1 FROM user_follows WHERE follower_id = $1 AND followed_id = $2 AND status = 'accepted'",
                viewer["user_id"], target["user_id"],
            )
            if not is_follower:
                return {"items": [], "total": 0, "private": True}

        # Pull photo posts (posts where media JSON contains at least one
        # `image` entry) and stories that have an `image_url`. We prefer a
        # UNION ALL keyed on created_at so pagination stays cheap.
        rows = await conn.fetch(
            """
            SELECT
                'post'                                    AS source,
                p.post_id                                 AS source_id,
                COALESCE(
                    (SELECT (m->>'url') FROM jsonb_array_elements(p.media) AS m
                     WHERE COALESCE(m->>'type','') IN ('image','photo','') LIMIT 1),
                    ''
                )                                         AS image_url,
                p.filter_preset                           AS filter_preset,
                p.created_at                              AS created_at
              FROM posts p
             WHERE p.user_id = $1
               AND p.visibility = 'public'
               AND jsonb_array_length(p.media) > 0
            UNION ALL
            SELECT
                'story', s.story_id, s.image_url,
                s.filter_preset, s.created_at
              FROM stories s
             WHERE s.user_id = $1
               AND s.image_url <> ''
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id, limit,
        )
    items = []
    for r in rows:
        url = (r["image_url"] or "").strip()
        if not url:
            continue
        items.append({
            "source": r["source"],
            "source_id": r["source_id"],
            "image_url": url,
            "filter_preset": r["filter_preset"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return {"items": items, "total": len(items), "private": False}
