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
    # iter240j — LinkedIn-style profile extension fields (all additive).
    headline: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    website_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    profession: Optional[str] = None
    company: Optional[str] = None
    skills: Optional[list] = None
    languages_spoken: Optional[list] = None
    experience: Optional[list] = None
    education: Optional[list] = None
    achievements: Optional[list] = None


# iter240j — Profile visibility toggle payload.
class VisibilityRequest(BaseModel):
    visibility: str  # 'public' or 'private'


# iter240j — Helper to compute profile completion percentage.
def _compute_completion_pct(u: dict) -> int:
    pct = 0
    if u.get("avatar"):
        pct += 15
    if u.get("headline"):
        pct += 15
    if u.get("bio"):
        pct += 20
    if u.get("location"):
        pct += 10
    if u.get("profession") and u.get("company"):
        pct += 10
    skills = u.get("skills") or []
    if isinstance(skills, list) and len(skills) >= 3:
        pct += 10
    exp = u.get("experience") or []
    if isinstance(exp, list) and len(exp) >= 1:
        pct += 10
    edu = u.get("education") or []
    if isinstance(edu, list) and len(edu) >= 1:
        pct += 5
    if u.get("website_url") or u.get("linkedin_url") or u.get("twitter_url"):
        pct += 5
    return min(100, pct)


# iter240j — Strip private fields when the viewer is NOT the owner and
# profile_visibility='private'. Public-only fields stay (display_name,
# username, avatar, headline). Email is always stripped from public API.
_PRIVATE_FIELDS_HIDDEN_WHEN_PRIVATE = (
    "bio", "location", "website_url", "linkedin_url", "twitter_url",
    "profession", "company", "skills", "languages_spoken",
    "experience", "education", "achievements",
    "phone_number", "phone", "birthday", "gender",
    "followers_count", "following_count", "posts_count",
    "wallet_balance", "wallet_currency", "is_following",
)


def _strip_for_private_view(resp: dict) -> dict:
    out = {k: v for k, v in resp.items() if k not in _PRIVATE_FIELDS_HIDDEN_WHEN_PRIVATE}
    out["is_private"] = True
    return out


@router.get("/profile/{user_id_or_username}")
async def get_profile(user_id_or_username: str, request: Request):
    """iter240j — Lookup by user_id OR username. Visibility-aware:
       • Public profile → full payload (without email).
       • Private profile, viewer != owner → only public-safe fields + is_private=true.
       • Private profile, viewer == owner → full payload.
    Auth is optional: anonymous viewers may see public profiles.
    """
    # iter240j — Auth is OPTIONAL for public profiles; we swallow 401 so
    # logged-out visitors can still see the public version.
    viewer = None
    try:
        viewer = await get_current_user(request)
    except HTTPException:
        viewer = None
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try user_id first (fast), then case-insensitive username.
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", user_id_or_username,
        )
        if not user:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE LOWER(username) = LOWER($1)",
                user_id_or_username,
            )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user_id = user["user_id"]
        wallet = await conn.fetchrow(
            "SELECT balance, currency FROM wallets WHERE user_id = $1", user_id,
        )
        kyc_verified = bool(await conn.fetchval(
            "SELECT 1 FROM kyc_verifications WHERE user_id = $1 "
            "AND status = 'approved' LIMIT 1", user_id))
        resp = user_to_response(dict(user))
        resp['kyc_verified'] = kyc_verified
        if wallet:
            resp['wallet_balance'] = str(wallet['balance'])
            resp['wallet_currency'] = wallet['currency']
        resp['followers_count'] = user['followers_count'] or 0
        resp['following_count'] = user['following_count'] or 0
        resp['posts_count'] = user['posts_count'] or 0
        resp['cover_image'] = user['cover_image']
        resp['cover_position_y'] = user['cover_position_y'] if user['cover_position_y'] is not None else 50

        # iter240j — Expose the LinkedIn-style fields.
        for k in ("profile_visibility", "headline", "bio", "location",
                  "website_url", "linkedin_url", "twitter_url",
                  "profession", "company", "skills", "languages_spoken",
                  "experience", "education", "achievements",
                  "profile_completed_at"):
            v = user[k] if k in user else None
            if hasattr(v, "isoformat"):
                v = v.isoformat()
            resp[k] = v
        # iter240j — Defensively json.loads JSONB columns that were stored
        # as `json.dumps` strings by older PUT calls (asyncpg doesn't decode
        # JSONB unless a codec is registered).
        import json as _json
        for k in ("experience", "education", "achievements"):
            if isinstance(resp.get(k), str):
                try:
                    resp[k] = _json.loads(resp[k])
                except Exception:
                    resp[k] = []
        # Ensure JSONB/list defaults are never null in the JSON payload.
        for k in ("skills", "languages_spoken", "experience", "education", "achievements"):
            if resp.get(k) is None:
                resp[k] = []
        resp["profile_completion_pct"] = _compute_completion_pct(resp)
        # Never expose email through the public API.
        resp.pop("email", None)

        is_self = bool(viewer and viewer.get("user_id") == user_id)
        resp["is_self"] = is_self
        if is_self:
            resp["is_following"] = False
        elif viewer:
            is_following = await conn.fetchval(
                "SELECT 1 FROM user_follows WHERE follower_id = $1 AND followed_id = $2",
                viewer["user_id"], user_id,
            )
            resp["is_following"] = bool(is_following)
        else:
            resp["is_following"] = False

        # iter240j — Apply privacy mask if needed.
        visibility = (user["profile_visibility"] or "public").lower()
        if visibility == "private" and not is_self:
            return _strip_for_private_view(resp)
        resp["is_private"] = False
        return resp


# iter240j — Quick public-safe "me" endpoint to bootstrap the edit modal
# without going through the visibility-aware path above.
@router.get("/profile/me/full")
async def get_my_profile_full(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return await get_profile(user["user_id"], request)


# iter240j — Visibility toggle.
@router.post("/profile/visibility")
async def set_profile_visibility(req: VisibilityRequest, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    v = (req.visibility or "").lower().strip()
    if v not in ("public", "private"):
        raise HTTPException(status_code=400, detail="visibility must be 'public' or 'private'.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET profile_visibility = $1, updated_at = NOW() WHERE user_id = $2",
            v, user["user_id"],
        )
    return {"ok": True, "visibility": v}


@router.put("/profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    updates = {}
    for field in ['first_name', 'last_name', 'phone_number', 'about', 'gender',
                  'birthday', 'country', 'language', 'avatar', 'avatar_thumb',
                  'cover_image', 'cover_image_mobile', 'cover_position_y',
                  # iter240j — LinkedIn-style fields.
                  'headline', 'bio', 'location', 'website_url', 'linkedin_url',
                  'twitter_url', 'profession', 'company', 'skills',
                  'languages_spoken', 'experience', 'education', 'achievements']:
        val = getattr(req, field, None)
        if val is not None:
            updates[field] = val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # iter240j — Defensive validation: enforce length caps to keep the
    # API stable and prevent abuse.
    if 'headline' in updates and updates['headline'] and len(updates['headline']) > 220:
        raise HTTPException(status_code=400, detail="headline ≤ 220 chars.")
    if 'bio' in updates and updates['bio'] and len(updates['bio']) > 2000:
        raise HTTPException(status_code=400, detail="bio ≤ 2000 chars.")
    if 'skills' in updates and isinstance(updates['skills'], list) and len(updates['skills']) > 15:
        raise HTTPException(status_code=400, detail="skills ≤ 15 items.")
    # JSONB-typed fields: serialize lists/dicts as JSON strings for asyncpg.
    import json as _json
    for jf in ('experience', 'education', 'achievements'):
        if jf in updates and updates[jf] is not None:
            updates[jf] = _json.dumps(updates[jf])

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
        # iter240j — Mark profile as "completed" the first time the user
        # crosses 60% completion (used to nudge stale profiles).
        updated = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user['user_id'])
        pct = _compute_completion_pct(dict(updated))
        if pct >= 60 and not updated.get('profile_completed_at'):
            await conn.execute(
                "UPDATE users SET profile_completed_at = NOW() WHERE user_id = $1",
                user['user_id'],
            )
        resp = user_to_response(dict(updated))
        resp['profile_completion_pct'] = pct
        return resp


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
