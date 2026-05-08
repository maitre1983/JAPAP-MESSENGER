import uuid
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional, Literal
from database import get_pool
from routes.auth import get_current_user
from utils.network import client_ip as _client_ip
from services.settings_service import get_bool, get_float, get_int, get_setting
from services.marketplace_email import (
    send_order_received,
    send_order_auto_released,
    send_dispute_opened_seller,
    send_dispute_opened_admin,
    send_dispute_resolved,
)
import os

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


# iter175/176 — Sponsored Boost plans paid via Wallet USD JAPAP.
# All values are LIVE-READ from admin_settings. Frontend gets the catalogue
# via GET /boost/plans. Admin can change any of them through the dashboard
# without a redeploy (CEO mandate: zero hardcode).
async def get_boost_plans() -> dict:
    p24 = Decimal(str(await get_float("mkt_boost_price_24h", 1.0))).quantize(Decimal("0.01"))
    p7  = Decimal(str(await get_float("mkt_boost_price_7d", 5.0))).quantize(Decimal("0.01"))
    ph  = Decimal(str(await get_float("mkt_boost_price_homepage", 10.0))).quantize(Decimal("0.01"))
    hp_days = await get_int("mkt_boost_homepage_days", 30)
    return {
        "basic_24h":    {"price_usd": p24, "days": 1,       "is_homepage": False, "label": "Boost 24h"},
        "standard_7d":  {"price_usd": p7,  "days": 7,       "is_homepage": False, "label": "Boost 7 jours"},
        "homepage_30d": {"price_usd": ph,  "days": hp_days, "is_homepage": True,  "label": f"Vedette Homepage {hp_days}j"},
    }


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL") or "https://japap-refactor.preview.emergentagent.com"


def _hash_ip(ip: str) -> str:
    return hashlib.sha256((ip or "unknown").encode("utf-8")).hexdigest()[:32]


class CreateProductRequest(BaseModel):
    title: str
    description: str = ""
    price: float
    category: str = "general"
    images: list = []
    condition: str = "new"
    location: str = ""

class UpdateProductRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    condition: Optional[str] = None
    location: Optional[str] = None
    images: Optional[list] = None
    status: Optional[str] = None

class AdminModerateRequest(BaseModel):
    status: str   # 'active' | 'offline' | 'deleted'
    reason: Optional[str] = ""

class CreateOrderRequest(BaseModel):
    product_id: str
    notes: str = ""


@router.get("/products")
async def list_products(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50),
                        category: Optional[str] = None, search: Optional[str] = None,
                        price_min: Optional[float] = None, price_max: Optional[float] = None,
                        country: Optional[str] = None, condition: Optional[str] = None,
                        verified_only: bool = Query(False, description="Filter to KYC-verified sellers only (iter174)"),
                        sort: str = Query("smart", regex="^(smart|recent|price_asc|price_desc|top_rated)$")):
    """Advanced list: filters (price/country/condition/category/verified), fuzzy search,
    plus sort modes. `smart` = Pro-boosted → paid boost → recent, which rewards
    subscribers with organic visibility on the marketplace."""
    await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base = ("FROM products p JOIN users u ON p.seller_id = u.user_id "
                "WHERE p.status = 'active'")
        params = []
        if category:
            params.append(category)
            base += f" AND p.category = ${len(params)}"
        if search:
            params.append(f"%{search.lower()}%")
            base += f" AND (LOWER(p.title) LIKE ${len(params)} OR LOWER(p.description) LIKE ${len(params)})"
        if price_min is not None:
            params.append(Decimal(str(price_min)))
            base += f" AND p.price >= ${len(params)}"
        if price_max is not None:
            params.append(Decimal(str(price_max)))
            base += f" AND p.price <= ${len(params)}"
        if country:
            params.append(country.upper()[:2])
            base += f" AND p.country_code = ${len(params)}"
        if condition:
            params.append(condition)
            base += f" AND p.condition = ${len(params)}"
        if verified_only:
            # iter174 — Filter limited to sellers with an approved KYC.
            base += (" AND EXISTS(SELECT 1 FROM kyc_verifications kv "
                     "WHERE kv.user_id = p.seller_id AND kv.status = 'approved')")

        order_map = {
            "recent":     "p.created_at DESC",
            "price_asc":  "p.price ASC, p.created_at DESC",
            "price_desc": "p.price DESC, p.created_at DESC",
            "top_rated":  "p.rating_avg DESC, p.rating_count DESC, p.created_at DESC",
            "smart":      ("(p.is_homepage_featured AND (p.homepage_expires_at IS NULL OR p.homepage_expires_at > NOW())) DESC, "
                           "u.is_pro DESC, "
                           "(p.is_boosted AND (p.boost_expires_at IS NULL OR p.boost_expires_at > NOW())) DESC, "
                           "p.rating_avg DESC, p.created_at DESC"),
        }
        order_sql = order_map.get(sort, order_map["smart"])

        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        q_params = params + [limit, offset]
        rows = await conn.fetch(f"""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username,
                   u.is_verified, u.is_pro,
                   EXISTS(SELECT 1 FROM kyc_verifications kv
                          WHERE kv.user_id = p.seller_id AND kv.status = 'approved')
                          AS seller_kyc_verified
            {base} ORDER BY {order_sql} LIMIT ${len(params)+1} OFFSET ${len(params)+2}
        """, *q_params)
        products = []
        for r in rows:
            p = dict(r)
            p['price'] = str(p['price'])
            p['rating_avg'] = str(p.get('rating_avg') or 0)
            p['created_at'] = p['created_at'].isoformat()
            p['updated_at'] = p['updated_at'].isoformat() if p.get('updated_at') else None
            if p.get('boost_expires_at'):
                p['boost_expires_at'] = p['boost_expires_at'].isoformat()
            # asyncpg returns jsonb as str — coerce so the frontend receives
            # the array rather than a JSON string of the array.
            if isinstance(p.get('images'), str):
                try:
                    import json as _json
                    p['images'] = _json.loads(p['images'] or '[]')
                except Exception:
                    p['images'] = []
            p['is_boost_active'] = bool(
                p.get('is_boosted') and (not p.get('boost_expires_at') or p.get('boost_expires_at'))
            )
            products.append(p)

        # iter180 — Inject sponsored slots every N positions (audience-filtered).
        sponsored_slots = []
        try:
            if await get_bool("ads_enabled", True):
                from routes.ads_console import pick_sponsored_for_user
                slot_every = max(1, await get_int("ads_feed_slot_every", 5))
                # How many slots fit in this page
                max_slots = max(0, len(products) // slot_every)
                if max_slots > 0:
                    current_user = await get_current_user(request)
                    picked = await pick_sponsored_for_user(conn, current_user, limit=max_slots + 3)
                    # Deduplicate slots against each other only (a sponsored slot
                    # may highlight a product that's also organic — that's the whole
                    # point of an ad, à la Amazon/FB Marketplace).
                    seen_products = set()
                    for ad in picked:
                        if len(sponsored_slots) >= max_slots:
                            break
                        pid = ad.get('product_id')
                        if not pid or pid in seen_products:
                            continue
                        seen_products.add(pid)
                        sponsored_slots.append({
                            "position": (len(sponsored_slots) + 1) * slot_every,
                            "campaign_id": ad.get('campaign_id'),
                            "product_id": pid,
                            "title": ad.get('product_title') or ad.get('name') or '',
                            "images": ad.get('product_images') or [],
                            "price": ad.get('product_price') or "0",
                            "currency": ad.get('product_currency') or 'USD',
                            "seller_id": ad.get('product_seller_id'),
                            "sponsored": True,
                            "ad_label": "Sponsorisé",
                        })
        except Exception as _e:  # pragma: no cover — ads must never break the feed
            logger.warning(f"[marketplace] sponsored injection failed: {_e}")
            sponsored_slots = []

        return {"products": products, "total": count, "page": page, "limit": limit,
                "sort": sort, "sponsored_slots": sponsored_slots}


@router.post("/products")
async def create_product(req: CreateProductRequest, request: Request):
    user = await get_current_user(request)
    if req.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be positive")
    pool = await get_pool()
    product_id = f"prod_{uuid.uuid4().hex[:12]}"
    import json
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO products (product_id, seller_id, title, description, price, category, images, condition, location)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, product_id, user['user_id'], req.title, req.description, Decimal(str(req.price)),
           req.category, json.dumps(req.images), req.condition, req.location)

        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details) VALUES ($1, 'create_product', 'marketplace', $2)
        """, user['user_id'], f'{{"product_id": "{product_id}"}}')

        return {"product_id": product_id, "message": "Product created"}


@router.get("/products/{product_id}")
async def get_product(product_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified,
                   u.phone_number, u.country_code AS user_country,
                   EXISTS(SELECT 1 FROM kyc_verifications kv
                          WHERE kv.user_id = p.seller_id AND kv.status = 'approved')
                          AS seller_kyc_verified,
                   (SELECT COUNT(*) FROM orders o
                    WHERE o.seller_id = p.seller_id AND o.status = 'completed')
                          AS seller_completed_sales
            FROM products p JOIN users u ON p.seller_id = u.user_id WHERE p.product_id = $1
        """, product_id)
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")
        if row['status'] == 'deleted':
            raise HTTPException(status_code=404, detail="Produit supprimé")

        # iter175 — Track view (1 view per user OR ip / 30 minutes window)
        # Owner views never count.
        viewer_id = user.get('user_id') if user else None
        is_owner = viewer_id and viewer_id == row['seller_id']
        ip_h = _hash_ip(_client_ip(request))
        if not is_owner:
            recent = await conn.fetchval("""
                SELECT 1 FROM product_views
                WHERE product_id = $1
                  AND ((viewer_id IS NOT NULL AND viewer_id = $2)
                       OR ip_hash = $3)
                  AND viewed_at > NOW() - INTERVAL '30 minutes'
                LIMIT 1
            """, product_id, viewer_id, ip_h)
            if not recent:
                await conn.execute("""
                    INSERT INTO product_views (product_id, viewer_id, ip_hash)
                    VALUES ($1, $2, $3)
                """, product_id, viewer_id, ip_h)
                await conn.execute(
                    "UPDATE products SET views_count = views_count + 1 WHERE product_id = $1",
                    product_id)

        # 24h fresh count
        views_24h = await conn.fetchval("""
            SELECT COUNT(*) FROM product_views
            WHERE product_id = $1 AND viewed_at > NOW() - INTERVAL '24 hours'
        """, product_id) or 0
        unique_viewers_24h = await conn.fetchval("""
            SELECT COUNT(DISTINCT COALESCE(viewer_id, ip_hash)) FROM product_views
            WHERE product_id = $1 AND viewed_at > NOW() - INTERVAL '24 hours'
        """, product_id) or 0

        # Active boost (if any)
        active_boost = await conn.fetchrow("""
            SELECT plan, price_usd, starts_at, expires_at, is_homepage,
                   target_countries, is_global, age_min, age_max
            FROM product_boosts
            WHERE product_id = $1 AND status = 'active' AND expires_at > NOW()
            ORDER BY expires_at DESC LIMIT 1
        """, product_id)

        p = dict(row)
        p['price'] = str(p['price'])
        p['rating_avg'] = str(p.get('rating_avg') or 0)
        p['created_at'] = p['created_at'].isoformat()
        p['updated_at'] = p['updated_at'].isoformat()
        if p.get('boost_expires_at'):
            p['boost_expires_at'] = p['boost_expires_at'].isoformat()
        if p.get('homepage_expires_at'):
            p['homepage_expires_at'] = p['homepage_expires_at'].isoformat()
        # asyncpg returns jsonb as str by default → coerce.
        if isinstance(p.get('images'), str):
            try:
                import json as _json
                p['images'] = _json.loads(p['images'] or '[]')
            except Exception:
                p['images'] = []
        # iter174 — Tier badge: KYC + ≥5 completed sales = "Vendeur vérifié pro".
        sales = int(p.get('seller_completed_sales') or 0)
        p['seller_verified_pro'] = bool(p['seller_kyc_verified'] and sales >= 5)
        # iter175 — 24h freshness
        p['views_24h'] = int(views_24h)
        p['unique_viewers_24h'] = int(unique_viewers_24h)
        p['active_boost'] = (None if not active_boost else {
            "plan": active_boost['plan'],
            "price_usd": str(active_boost['price_usd']),
            "starts_at": active_boost['starts_at'].isoformat(),
            "expires_at": active_boost['expires_at'].isoformat(),
            "is_homepage": bool(active_boost['is_homepage']),
            # iter179 — audience targeting
            "is_global": bool(active_boost.get('is_global', True)) if isinstance(active_boost, dict) else bool(active_boost['is_global']),
            "target_countries": list(active_boost['target_countries'] or []) if active_boost['target_countries'] else [],
            "age_min": active_boost['age_min'],
            "age_max": active_boost['age_max'],
        })
        return p


@router.put("/products/{product_id}")
async def update_product(product_id: str, req: UpdateProductRequest, request: Request):
    """Vendor edit. Status is whitelisted to 'active'/'offline' — vendors
    cannot mark 'deleted' from here (the dedicated DELETE endpoint does
    the soft-delete)."""
    user = await get_current_user(request)
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT * FROM products WHERE product_id = $1 AND seller_id = $2 AND status != 'deleted'",
            product_id, user['user_id'])
        if not product:
            raise HTTPException(status_code=404, detail="Product not found or not yours")
        updates = {}
        for f in ['title', 'description', 'price', 'category', 'condition', 'location']:
            v = getattr(req, f, None)
            if v is not None:
                updates[f] = Decimal(str(v)) if f == 'price' else v
        if req.images is not None:
            if not isinstance(req.images, list) or len(req.images) > 5:
                raise HTTPException(status_code=400, detail="1 a 5 images max")
            updates['images'] = _json.dumps(req.images)
        if req.status is not None:
            if req.status not in ('active', 'offline'):
                raise HTTPException(
                    status_code=400,
                    detail="Vendeur peut seulement basculer entre 'active' et 'offline'")
            updates['status'] = req.status
        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")
        updates['updated_at'] = datetime.now(timezone.utc)
        clause = ", ".join([f"{k} = ${i+1}" for i, k in enumerate(updates.keys())])
        vals = list(updates.values()) + [product_id]
        await conn.execute(f"UPDATE products SET {clause} WHERE product_id = ${len(vals)}", *vals)
        return {"message": "Product updated"}


@router.delete("/products/{product_id}")
async def delete_product(product_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow("SELECT * FROM products WHERE product_id = $1 AND seller_id = $2", product_id, user['user_id'])
        if not product:
            raise HTTPException(status_code=404, detail="Product not found or not yours")
        await conn.execute("UPDATE products SET status = 'deleted' WHERE product_id = $1", product_id)
        return {"message": "Product deleted"}


# iter174 — Marketplace product image upload (1-5, auto-compressed)
import asyncio
import io
import json as _json_mod
from pathlib import Path as _Path
from fastapi import UploadFile, File, Form

_PRODUCT_UPLOAD_DIR = _Path(__file__).parent.parent / "uploads"
_PRODUCT_UPLOAD_DIR.mkdir(exist_ok=True)
_PRODUCT_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_PRODUCT_MAX_BYTES = 12 * 1024 * 1024
_PRODUCT_MAX_PER_REQUEST = 5


def _compress_product_image(raw: bytes, *, max_width: int, quality: int) -> bytes:
    """Open ANY supported image, honour EXIF rotation, resize keeping
    aspect, and re-emit as JPEG. Same pattern as KYC overhaul."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


@router.post("/products/upload-images")
async def upload_product_images(request: Request,
                                  files: list[UploadFile] = File(...)):
    """Upload up to 5 images at once and return their URLs in 3 sizes.

    Sizes:
      - thumb  : ≤ 480px width, q72  (grid card)
      - full   : ≤ 1024px width, q78 (gallery)
      - hd     : ≤ 2048px width, q82 (zoom)

    Returns: {images: [{thumb, full, hd}, ...]}
    """
    await get_current_user(request)
    if not files or len(files) > _PRODUCT_MAX_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"1 à {_PRODUCT_MAX_PER_REQUEST} images par requête")
    out = []
    for f in files:
        ext = _Path(f.filename or "img.jpg").suffix.lower() or ".jpg"
        if ext not in _PRODUCT_ALLOWED_EXTS:
            raise HTTPException(
                status_code=400, detail=f"Format non supporté: {ext}")
        raw = await f.read()
        if len(raw) > _PRODUCT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Image > 12MB")
        if len(raw) < 512:
            raise HTTPException(status_code=400, detail="Image trop petite")
        try:
            thumb = _compress_product_image(raw, max_width=480, quality=72)
            full = _compress_product_image(raw, max_width=1024, quality=78)
            hd = _compress_product_image(raw, max_width=2048, quality=82)
        except Exception as e:
            logger.warning(f"[mkt-upload] compression failed: {e}")
            raise HTTPException(status_code=400, detail="Image illisible")
        nonce = uuid.uuid4().hex[:14]
        names = {"thumb": f"prod_{nonce}_thumb.jpg",
                 "full":  f"prod_{nonce}.jpg",
                 "hd":    f"prod_{nonce}_hd.jpg"}
        with open(_PRODUCT_UPLOAD_DIR / names["thumb"], "wb") as h:
            h.write(thumb)
        with open(_PRODUCT_UPLOAD_DIR / names["full"], "wb") as h:
            h.write(full)
        with open(_PRODUCT_UPLOAD_DIR / names["hd"], "wb") as h:
            h.write(hd)
        out.append({
            "thumb": f"/api/upload/files/{names['thumb']}",
            "full":  f"/api/upload/files/{names['full']}",
            "hd":    f"/api/upload/files/{names['hd']}",
        })
    return {"images": out}


# ─────────────────────── iter181 — AI Image (Nano Banana) ───────────────────────
async def _mkt_ai_quota_state(conn, user_id: str) -> dict:
    """Returns {enabled, quota, used_today, remaining, day_key}."""
    from datetime import datetime as _dt, timezone as _tz
    enabled = await get_bool("mkt_ai_images_enabled", True)
    quota = max(0, await get_int("mkt_ai_images_daily_quota", 3))
    day_key = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    row = await conn.fetchrow("""
        SELECT COALESCE(SUM(count), 0) AS total
        FROM marketplace_ai_image_usage
        WHERE user_id = $1 AND day_key = $2
    """, user_id, day_key)
    used = int(row["total"] if row and row["total"] is not None else 0)
    return {
        "enabled": bool(enabled),
        "quota": quota,
        "used_today": used,
        "remaining": max(0, quota - used),
        "day_key": day_key,
    }


async def _mkt_ai_bump_quota(conn, user_id: str, kind: str) -> None:
    from datetime import datetime as _dt, timezone as _tz
    day_key = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    await conn.execute("""
        INSERT INTO marketplace_ai_image_usage (user_id, day_key, kind, count, last_at)
        VALUES ($1, $2, $3, 1, NOW())
        ON CONFLICT (user_id, day_key, kind)
        DO UPDATE SET count = marketplace_ai_image_usage.count + 1, last_at = NOW()
    """, user_id, day_key, kind)


def _persist_ai_image_triple(raw: bytes) -> dict:
    """Run the same 3-size pipeline as the manual uploader, return urls."""
    thumb = _compress_product_image(raw, max_width=480, quality=72)
    full = _compress_product_image(raw, max_width=1024, quality=78)
    hd = _compress_product_image(raw, max_width=2048, quality=82)
    nonce = uuid.uuid4().hex[:14]
    names = {"thumb": f"prod_ai_{nonce}_thumb.jpg",
             "full":  f"prod_ai_{nonce}.jpg",
             "hd":    f"prod_ai_{nonce}_hd.jpg"}
    with open(_PRODUCT_UPLOAD_DIR / names["thumb"], "wb") as h:
        h.write(thumb)
    with open(_PRODUCT_UPLOAD_DIR / names["full"], "wb") as h:
        h.write(full)
    with open(_PRODUCT_UPLOAD_DIR / names["hd"], "wb") as h:
        h.write(hd)
    return {
        "thumb": f"/api/upload/files/{names['thumb']}",
        "full":  f"/api/upload/files/{names['full']}",
        "hd":    f"/api/upload/files/{names['hd']}",
    }


class AIGenerateRequest(BaseModel):
    prompt: str


@router.get("/ai-image/quota")
async def ai_image_quota(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        state = await _mkt_ai_quota_state(conn, user["user_id"])
    # Expose configured bg presets for frontend
    raw_presets = (await get_setting("mkt_ai_bg_presets", "") or "").strip()
    presets = [p.strip() for p in raw_presets.split(",") if p.strip()] or \
              ["studio_white", "studio_black", "lifestyle", "outdoor", "luxury", "marble"]
    state["bg_presets"] = presets
    return state


@router.post("/ai-image/generate")
async def ai_image_generate(req: AIGenerateRequest, request: Request):
    """Text-to-image — generate a fresh product shot from a prompt."""
    user = await get_current_user(request)
    from services.marketplace_ai_image import generate_from_prompt, AIImageError
    pool = await get_pool()
    async with pool.acquire() as conn:
        state = await _mkt_ai_quota_state(conn, user["user_id"])
        if not state["enabled"]:
            raise HTTPException(status_code=503, detail="La génération IA est désactivée.")
        if state["remaining"] <= 0:
            raise HTTPException(
                status_code=429,
                detail=f"Quota IA atteint ({state['used_today']}/{state['quota']}). Reviens demain.")
    try:
        raw = await generate_from_prompt(req.prompt)
    except AIImageError as e:
        raise HTTPException(status_code=502, detail=str(e))
    urls = _persist_ai_image_triple(raw)
    async with pool.acquire() as conn:
        await _mkt_ai_bump_quota(conn, user["user_id"], "generate")
        state = await _mkt_ai_quota_state(conn, user["user_id"])
    return {"image": urls, "quota": state, "mode": "generate"}


@router.post("/ai-image/enhance")
async def ai_image_enhance(request: Request,
                             file: UploadFile = File(...),
                             instruction: str = Form("")):
    """Image-to-image — enhance an existing photo (clean bg, lighting)."""
    user = await get_current_user(request)
    from services.marketplace_ai_image import enhance_image, AIImageError
    raw_in = await file.read()
    if len(raw_in) > _PRODUCT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image > 12MB")
    if len(raw_in) < 512:
        raise HTTPException(status_code=400, detail="Image trop petite")
    pool = await get_pool()
    async with pool.acquire() as conn:
        state = await _mkt_ai_quota_state(conn, user["user_id"])
        if not state["enabled"]:
            raise HTTPException(status_code=503, detail="L'amélioration IA est désactivée.")
        if state["remaining"] <= 0:
            raise HTTPException(
                status_code=429,
                detail=f"Quota IA atteint ({state['used_today']}/{state['quota']}). Reviens demain.")
    try:
        raw = await enhance_image(raw_in, instruction or "")
    except AIImageError as e:
        raise HTTPException(status_code=502, detail=str(e))
    urls = _persist_ai_image_triple(raw)
    async with pool.acquire() as conn:
        await _mkt_ai_bump_quota(conn, user["user_id"], "enhance")
        state = await _mkt_ai_quota_state(conn, user["user_id"])
    return {"image": urls, "quota": state, "mode": "enhance"}


@router.post("/ai-image/bg-swap")
async def ai_image_bg_swap(request: Request,
                             file: UploadFile = File(...),
                             preset: str = Form(""),
                             custom_scene: str = Form("")):
    """Image-to-image — replace background with a preset or custom scene."""
    user = await get_current_user(request)
    from services.marketplace_ai_image import bg_swap, DEFAULT_BG_PRESETS, AIImageError
    raw_in = await file.read()
    if len(raw_in) > _PRODUCT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image > 12MB")
    if len(raw_in) < 512:
        raise HTTPException(status_code=400, detail="Image trop petite")
    # Resolve scene: preset takes priority, fall back to custom.
    scene_text = ""
    preset = (preset or "").strip().lower()
    if preset and preset in DEFAULT_BG_PRESETS:
        scene_text = DEFAULT_BG_PRESETS[preset]
    elif custom_scene and custom_scene.strip():
        scene_text = custom_scene.strip()[:200]
    else:
        raise HTTPException(status_code=400, detail="Choisis un preset ou décris la scène.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        state = await _mkt_ai_quota_state(conn, user["user_id"])
        if not state["enabled"]:
            raise HTTPException(status_code=503, detail="Le background swap IA est désactivé.")
        if state["remaining"] <= 0:
            raise HTTPException(
                status_code=429,
                detail=f"Quota IA atteint ({state['used_today']}/{state['quota']}). Reviens demain.")
    try:
        raw = await bg_swap(raw_in, scene_text)
    except AIImageError as e:
        raise HTTPException(status_code=502, detail=str(e))
    urls = _persist_ai_image_triple(raw)
    async with pool.acquire() as conn:
        await _mkt_ai_bump_quota(conn, user["user_id"], "bg_swap")
        state = await _mkt_ai_quota_state(conn, user["user_id"])
    return {"image": urls, "quota": state, "mode": "bg_swap", "preset": preset or None}


@router.post("/ai-image/auto-photo")
async def ai_image_auto_photo(request: Request, file: UploadFile = File(...)):
    """iter182 — Pro auto-photographie : 1 photo brute → N variations en parallèle.
    Génère N variations de fond depuis `mkt_ai_auto_photo_presets`, fan-out asyncio.gather.
    Quota déduit = 1 par variation réussie (les échecs ne consomment rien).
    Retourne `{variations: [{preset, image, error?}, ...], quota}`."""
    user = await get_current_user(request)
    from services.marketplace_ai_image import bg_swap, DEFAULT_BG_PRESETS, AIImageError
    raw_in = await file.read()
    if len(raw_in) > _PRODUCT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image > 12MB")
    if len(raw_in) < 512:
        raise HTTPException(status_code=400, detail="Image trop petite")
    if not await get_bool("mkt_ai_auto_photo_enabled", True):
        raise HTTPException(status_code=503, detail="Mode auto-photographie désactivé.")

    raw_presets = (await get_setting("mkt_ai_auto_photo_presets", "") or "").strip()
    presets = [p.strip().lower() for p in raw_presets.split(",")
               if p.strip() and p.strip().lower() in DEFAULT_BG_PRESETS]
    if not presets:
        presets = ["studio_white", "lifestyle", "marble", "luxury"]
    presets = presets[:6]

    pool = await get_pool()
    async with pool.acquire() as conn:
        state = await _mkt_ai_quota_state(conn, user["user_id"])
        if not state["enabled"]:
            raise HTTPException(status_code=503, detail="La génération IA est désactivée.")
        if state["remaining"] < len(presets):
            raise HTTPException(
                status_code=429,
                detail=(f"Auto-photo nécessite {len(presets)} crédits IA. "
                        f"Restants : {state['remaining']}/{state['quota']}."))

    async def _one(preset: str):
        try:
            scene = DEFAULT_BG_PRESETS[preset]
            raw = await bg_swap(raw_in, scene)
            return {"preset": preset, "image": _persist_ai_image_triple(raw), "ok": True}
        except AIImageError as e:
            return {"preset": preset, "ok": False, "error": str(e)[:200]}
        except Exception as e:  # pragma: no cover
            return {"preset": preset, "ok": False, "error": f"{type(e).__name__}"}

    # Fan-out parallel — Nano Banana handles concurrent requests fine
    results = await asyncio.gather(*[_one(p) for p in presets])

    # Charge quota only for successful ones
    successes = sum(1 for r in results if r["ok"])
    if successes > 0:
        async with pool.acquire() as conn:
            for _ in range(successes):
                await _mkt_ai_bump_quota(conn, user["user_id"], "auto_photo")
            state = await _mkt_ai_quota_state(conn, user["user_id"])

    return {"variations": results, "quota": state, "mode": "auto_photo",
            "presets_used": presets, "success_count": successes}
# ─────────────────────── end iter181/182 ───────────────────────


# ─────────────────────── iter187 — Contact seller (marketplace → messenger) ──
class ContactSellerRequest(BaseModel):
    product_id: str


@router.post("/products/contact-seller")
async def contact_seller(req: ContactSellerRequest, request: Request):
    """Crée ou réutilise une conversation directe avec le vendeur d'un produit
    et y envoie un message d'intérêt prédéfini (titre + prix + lien produit).
    Anti-self-contact, anti-doublon (réutilise la conv existante)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow("""
            SELECT product_id, seller_id, title, price, currency, status
            FROM products WHERE product_id = $1
        """, req.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit introuvable")
        if product["seller_id"] == user["user_id"]:
            raise HTTPException(status_code=400, detail="Tu ne peux pas te contacter toi-même.")
        if product["status"] != "active":
            raise HTTPException(status_code=400, detail="Ce produit n'est plus disponible.")

        # Reuse existing helper — handles dedup natively
        from routes.messaging import get_or_create_conversation
        conv_id = await get_or_create_conversation(conn, user["user_id"], product["seller_id"])

        # Build the auto-message in French (CEO spec)
        from services.seo_slug import product_canonical_url
        link = product_canonical_url(product["product_id"], product["title"])
        currency = product.get("currency") or "USD"
        try:
            price_str = f"{float(product['price']):.2f} {currency}"
        except Exception:
            price_str = f"{product['price']} {currency}"
        text = (
            f"Bonjour, je suis intéressé(e) par votre produit : "
            f"{product['title']}\n"
            f"Prix : {price_str}\n"
            f"Lien : {link}"
        )

        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        await conn.execute("""
            INSERT INTO messages (msg_id, conv_id, sender_id, text, media, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, 'sent')
        """, msg_id, conv_id, user["user_id"], text, "[]")
        await conn.execute(
            "UPDATE conversations SET updated_at = NOW() WHERE conv_id = $1", conv_id)

        # iter188 — "intent → conversation → vente" loop (Vinted playbook).
        # Push au vendeur UNIQUEMENT sur première intention (1er message de
        # CET acheteur sur CE produit). Cooldown 6h pour éviter le spam si
        # l'acheteur clique plusieurs fois.
        is_first_intent = await conn.fetchval("""
            SELECT NOT EXISTS (
                SELECT 1 FROM marketplace_buyer_intents
                WHERE buyer_id = $1 AND product_id = $2
                  AND created_at > NOW() - INTERVAL '6 hours'
            )
        """, user["user_id"], product["product_id"])

        if is_first_intent:
            await conn.execute("""
                INSERT INTO marketplace_buyer_intents
                  (buyer_id, seller_id, product_id, conv_id)
                VALUES ($1, $2, $3, $4)
            """, user["user_id"], product["seller_id"],
                 product["product_id"], conv_id)

    # Fire seller-nudge push outside the conn block (best-effort, non-blocking)
    if is_first_intent:
        try:
            from services.notifications import send_social_notification
            buyer_name = (user.get("first_name") or user.get("username")
                          or "Un acheteur")[:40]
            await send_social_notification(
                event_type="marketplace_buyer_intent",
                actor=user,
                target_user_id=product["seller_id"],
                title=f"🛍️ {buyer_name} s'intéresse à ton produit",
                body=(f"« {product['title']} » — Réponds en moins de 5 min "
                      f"pour booster ton taux de conversion de +30%."),
                deep_link=f"/chat?conv={conv_id}",
                extra_data={
                    "product_id": product["product_id"],
                    "buyer_id": user["user_id"],
                    "conv_id": conv_id,
                },
            )
        except Exception as e:  # pragma: no cover
            logger.warning(f"[marketplace] seller-nudge push failed: {e}")

    return {
        "ok": True,
        "conv_id": conv_id,
        "message_id": msg_id,
        "seller_id": product["seller_id"],
        "product_id": product["product_id"],
        "preview": text,
    }
# ─────────────────────── end iter187 ───────────────────────



# iter174 — Admin moderation: ONLINE / OFFLINE / DELETED
@router.post("/admin/products/{product_id}/moderate")
async def admin_moderate_product(product_id: str, req: AdminModerateRequest,
                                   request: Request):
    """Admin can flip status to active / offline / deleted. Logged in
    audit_logs. Vendor's own status is preserved unless admin chose
    'deleted' which is irreversible."""
    from routes.auth import require_admin
    admin = await require_admin(request)
    if req.status not in ("active", "offline", "deleted"):
        raise HTTPException(
            status_code=400,
            detail="status doit être 'active' | 'offline' | 'deleted'")
    pool = await get_pool()
    async with pool.acquire() as conn:
        prod = await conn.fetchrow(
            "SELECT product_id, status, seller_id FROM products WHERE product_id = $1",
            product_id)
        if not prod:
            raise HTTPException(status_code=404, detail="Produit introuvable")
        await conn.execute(
            "UPDATE products SET status = $1, updated_at = NOW() WHERE product_id = $2",
            req.status, product_id)
        try:
            await conn.execute(
                "INSERT INTO audit_logs (user_id, action, resource, details) "
                "VALUES ($1, 'admin_moderate_product', 'marketplace', $2)",
                admin['user_id'],
                _json_mod.dumps({"product_id": product_id,
                                  "from_status": prod['status'],
                                  "to_status": req.status,
                                  "reason": (req.reason or "")[:300]}),
            )
        except Exception as e:
            logger.warning(f"[mkt-admin] audit log failed: {e}")
        # Notify the seller
        try:
            await conn.execute(
                "INSERT INTO notifications (notif_id, user_id, type, title, message) "
                "VALUES ($1, $2, 'marketplace_moderation', $3, $4)",
                f"notif_{uuid.uuid4().hex[:12]}", prod['seller_id'],
                {
                    'active':   "Produit remis en ligne",
                    'offline':  "Produit mis hors-ligne par un modérateur",
                    'deleted':  "Produit supprimé par un modérateur",
                }[req.status],
                (f"Votre produit a été marqué '{req.status}'. "
                 f"{('Motif : ' + req.reason) if req.reason else ''}"))
        except Exception as e:
            logger.warning(f"[mkt-admin] notif failed: {e}")
    return {"product_id": product_id, "status": req.status}


@router.get("/admin/products")
async def admin_list_products(request: Request,
                                status: Optional[str] = None,
                                page: int = Query(1, ge=1),
                                limit: int = Query(50, ge=1, le=200)):
    """Admin moderation queue — see all products incl. offline/deleted."""
    from routes.auth import require_admin
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        params: list = []
        where = "WHERE 1=1"
        if status and status in ("active", "offline", "deleted"):
            params.append(status)
            where += f" AND p.status = ${len(params)}"
        params.append(limit)
        params.append((page - 1) * limit)
        rows = await conn.fetch(f"""
            SELECT p.product_id, p.title, p.price, p.status, p.images,
                   p.created_at, p.seller_id, u.email, u.first_name,
                   EXISTS(SELECT 1 FROM kyc_verifications kv
                          WHERE kv.user_id = p.seller_id AND kv.status='approved')
                          AS seller_kyc_verified
            FROM products p JOIN users u ON u.user_id = p.seller_id
            {where} ORDER BY p.created_at DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}
        """, *params)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM products p {where}", *params[:-2])
        items = []
        for r in rows:
            d = dict(r)
            d['price'] = str(d['price'])
            d['created_at'] = d['created_at'].isoformat()
            items.append(d)
        return {"items": items, "total": int(total or 0),
                "page": page, "limit": limit}


@router.post("/orders")
async def create_order(req: CreateOrderRequest, request: Request):
    """iter176 — Buy via Wallet USD JAPAP escrow.

    CEO rules:
      • Buyer pays the EXACT product price (no buyer-side fee).
      • Funds held in JAPAP escrow (status='pending', escrow_status='held').
      • On confirm/auto-release: seller receives `amount × (1 - commission_pct/100)`,
        japap_treasury receives `amount × commission_pct/100`.
      • Commission% + auto-release days are LIVE-READ from admin_settings.
      • All movements logged in `marketplace_escrow_ledger` (auditable).
      • NO external PSP. Wallet only.
    """
    user = await get_current_user(request)
    if not await get_bool("mkt_escrow_enabled", True):
        raise HTTPException(status_code=503, detail="Le paiement escrow Marketplace est temporairement désactivé.")
    commission_pct = Decimal(str(await get_float("mkt_escrow_commission_percent", 2.0)))
    auto_release_days = max(1, await get_int("mkt_escrow_auto_release_days", 7))
    treasury_account = (await get_setting("mkt_escrow_treasury_account") or "japap_treasury").strip() or "japap_treasury"

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            product = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id = $1 AND status = 'active' FOR UPDATE",
                req.product_id)
            if not product:
                raise HTTPException(status_code=404, detail="Produit indisponible")
            if product['seller_id'] == user['user_id']:
                raise HTTPException(status_code=400, detail="Vous ne pouvez pas acheter votre propre produit")

            amount = Decimal(str(product['price']))
            fee = (amount * commission_pct / Decimal(100)).quantize(Decimal("0.01"))

            wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not wallet:
                raise HTTPException(status_code=404, detail="Wallet introuvable")
            if wallet['is_locked']:
                raise HTTPException(status_code=403, detail="Wallet verrouillé")
            if Decimal(str(wallet['balance'])) < amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"INSUFFICIENT_BALANCE — Solde insuffisant ({amount} USD requis). Recharge ton wallet pour acheter.")

            # Debit buyer (USD canonical)
            now = datetime.now(timezone.utc)
            auto_release_at = now + timedelta(days=auto_release_days)
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                amount, now, user['user_id'])

            order_id = f"ord_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO orders
                  (order_id, product_id, buyer_id, seller_id, amount, fee, status, notes,
                   escrow_status, auto_release_at, commission_pct)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7, 'held', $8, $9)
            """, order_id, req.product_id, user['user_id'], product['seller_id'],
               amount, fee, req.notes, auto_release_at, commission_pct)

            # Canonical wallet ledger row (escrow status)
            tx_id = f"mkt_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO transactions
                  (tx_id, from_user_id, to_user_id, type, amount, fee, currency, status, notes, reference)
                VALUES ($1, $2, $3, 'marketplace_purchase', $4, $5, 'USD', 'escrow', $6, $7)
            """, tx_id, user['user_id'], product['seller_id'], amount, fee,
               f"Escrow achat: {product['title'][:120]}", order_id)

            # Escrow ledger entry
            await conn.execute("""
                INSERT INTO marketplace_escrow_ledger
                  (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, tx_id, notes, created_by)
                VALUES ($1, $2, 'hold', $3, 'escrow', $4, $5, $6, $7)
            """, f"led_{uuid.uuid4().hex[:12]}", order_id,
               f"buyer:{user['user_id']}", amount, tx_id,
               f"Hold {amount} USD until release/refund", user['user_id'])

            # Audit + notify seller
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'marketplace_escrow_hold', 'marketplace', $2)
            """, user['user_id'],
               f'{{"order_id":"{order_id}","product_id":"{req.product_id}","amount":"{amount}","fee":"{fee}","auto_release_at":"{auto_release_at.isoformat()}"}}')

            try:
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message)
                    VALUES ($1, $2, 'order_received', '🛒 Nouvelle commande', $3)
                """, f"notif_{uuid.uuid4().hex[:12]}", product['seller_id'],
                   f"{user.get('first_name') or 'Un acheteur'} a acheté \"{product['title']}\" — {amount} USD en escrow. "
                   f"L'argent sera libéré après confirmation ou {auto_release_days} jours.")
            except Exception as e:
                logger.warning(f"[mkt-escrow] notif failed: {e}")

    # ── iter177 — async email post-commit (best-effort)
    try:
        async with pool.acquire() as conn:
            seller = await conn.fetchrow(
                "SELECT email, first_name FROM users WHERE user_id = $1",
                product['seller_id'])
        if seller and seller['email']:
            await send_order_received(
                seller['email'],
                seller_first_name=seller['first_name'] or '',
                buyer_first_name=user.get('first_name') or '',
                product_title=product['title'],
                amount_usd=str(amount),
                auto_release_days=auto_release_days,
                order_id=order_id,
                app_url=_frontend_url(),
            )
    except Exception as e:
        logger.warning(f"[mkt-escrow-email] order_received send failed: {e}")

    return {
        "order_id": order_id,
        "tx_id": tx_id,
        "amount_usd": str(amount),
        "fee_usd": str(fee),
        "commission_pct": str(commission_pct),
        "auto_release_at": auto_release_at.isoformat(),
        "auto_release_days": auto_release_days,
        "treasury_account": treasury_account,
        "escrow_status": "held",
        "message": f"🔒 Paiement sécurisé — fonds bloqués jusqu'à confirmation ou libération automatique le {auto_release_at.strftime('%d/%m/%Y')}.",
    }


# ───────────────────────────── ESCROW HELPERS ─────────────────────────────
async def _release_escrow(conn, order_id: str, *, actor_id: str, reason: str = "") -> dict:
    """Move escrow funds: net → seller wallet, commission → treasury (ledger only).
    Idempotent: safe to call multiple times — only acts on 'held' orders."""
    order = await conn.fetchrow(
        "SELECT * FROM orders WHERE order_id = $1 FOR UPDATE", order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")
    if order['escrow_status'] != 'held':
        return {"already": order['escrow_status'], "order_id": order_id}

    amount = Decimal(str(order['amount']))
    fee = Decimal(str(order['fee']))
    net = (amount - fee).quantize(Decimal("0.01"))
    treasury_account = (await get_setting("mkt_escrow_treasury_account") or "japap_treasury").strip() or "japap_treasury"
    now = datetime.now(timezone.utc)

    # Credit seller wallet (USD canonical)
    await conn.execute(
        "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
        net, now, order['seller_id'])

    # Canonical ledger row : seller credit
    tx_seller = f"mkt_rel_{uuid.uuid4().hex[:10]}"
    await conn.execute("""
        INSERT INTO transactions
          (tx_id, from_user_id, to_user_id, type, amount, currency, status, notes, reference)
        VALUES ($1, $2, $3, 'marketplace_release', $4, 'USD', 'completed', $5, $6)
    """, tx_seller, order['buyer_id'], order['seller_id'], net,
       "Release escrow — net après commission JAPAP", order_id)

    # Canonical ledger row : treasury commission
    tx_fee = None
    if fee > 0:
        tx_fee = f"mkt_fee_{uuid.uuid4().hex[:10]}"
        await conn.execute("""
            INSERT INTO transactions
              (tx_id, from_user_id, type, amount, currency, status, notes, reference)
            VALUES ($1, $2, 'marketplace_commission', $3, 'USD', 'completed', $4, $5)
        """, tx_fee, order['buyer_id'], fee,
           f"Commission JAPAP {order['commission_pct']}% — order {order_id}", order_id)

    # Escrow ledger entries
    await conn.execute("""
        INSERT INTO marketplace_escrow_ledger
          (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, tx_id, notes, created_by)
        VALUES ($1, $2, 'release_seller', 'escrow', $3, $4, $5, $6, $7)
    """, f"led_{uuid.uuid4().hex[:12]}", order_id,
       f"seller:{order['seller_id']}", net, tx_seller,
       reason or "Buyer confirmation / auto-release", actor_id)

    if fee > 0:
        await conn.execute("""
            INSERT INTO marketplace_escrow_ledger
              (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, tx_id, notes, created_by)
            VALUES ($1, $2, 'commission', 'escrow', $3, $4, $5, $6, $7)
        """, f"led_{uuid.uuid4().hex[:12]}", order_id, treasury_account, fee, tx_fee,
           f"JAPAP commission {order['commission_pct']}%", actor_id)

    await conn.execute("""
        UPDATE orders SET status = 'completed', escrow_status = 'released',
                          released_at = $1, updated_at = $1
        WHERE order_id = $2
    """, now, order_id)
    await conn.execute(
        "UPDATE transactions SET status = 'completed' WHERE reference = $1 AND type = 'marketplace_purchase'",
        order_id)
    return {"order_id": order_id, "released": True, "net_seller": str(net),
            "commission": str(fee), "treasury_account": treasury_account}


async def _refund_escrow(conn, order_id: str, *, actor_id: str, reason: str = "") -> dict:
    """Refund the buyer the FULL escrowed amount (no commission cut).
    Idempotent. Used by admin dispute resolution OR a future auto-refund flow."""
    order = await conn.fetchrow(
        "SELECT * FROM orders WHERE order_id = $1 FOR UPDATE", order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")
    if order['escrow_status'] not in ('held', 'disputed'):
        return {"already": order['escrow_status'], "order_id": order_id}

    amount = Decimal(str(order['amount']))
    now = datetime.now(timezone.utc)

    await conn.execute(
        "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
        amount, now, order['buyer_id'])

    tx_id = f"mkt_ref_{uuid.uuid4().hex[:10]}"
    await conn.execute("""
        INSERT INTO transactions
          (tx_id, to_user_id, type, amount, currency, status, notes, reference)
        VALUES ($1, $2, 'marketplace_refund', $3, 'USD', 'completed', $4, $5)
    """, tx_id, order['buyer_id'], amount,
       f"Refund escrow — order {order_id} — {reason[:120]}", order_id)

    await conn.execute("""
        INSERT INTO marketplace_escrow_ledger
          (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, tx_id, notes, created_by)
        VALUES ($1, $2, 'refund_buyer', 'escrow', $3, $4, $5, $6, $7)
    """, f"led_{uuid.uuid4().hex[:12]}", order_id,
       f"buyer:{order['buyer_id']}", amount, tx_id, reason or "Admin refund", actor_id)

    await conn.execute("""
        UPDATE orders SET status = 'refunded', escrow_status = 'refunded',
                          refunded_at = $1, dispute_resolved_at = $1, updated_at = $1
        WHERE order_id = $2
    """, now, order_id)
    await conn.execute(
        "UPDATE transactions SET status = 'cancelled' WHERE reference = $1 AND type = 'marketplace_purchase'",
        order_id)
    return {"order_id": order_id, "refunded": True, "amount_usd": str(amount)}


# ───────────────────────────── ESCROW PUBLIC API ──────────────────────────
@router.put("/orders/{order_id}/confirm")
async def confirm_order(order_id: str, request: Request):
    """Buyer confirms receipt — releases escrow (net to seller, commission to treasury)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT * FROM orders WHERE order_id = $1 AND buyer_id = $2",
                order_id, user['user_id'])
            if not order:
                raise HTTPException(status_code=404, detail="Commande introuvable")
            if order['escrow_status'] == 'disputed':
                raise HTTPException(status_code=409, detail="Commande en litige — confirmation bloquée le temps de la résolution.")
            if order['escrow_status'] != 'held':
                return {"already": order['escrow_status'], "message": "Commande déjà clôturée."}
            res = await _release_escrow(conn, order_id, actor_id=user['user_id'],
                                          reason="Buyer confirmed receipt")

            # Notify seller
            try:
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message)
                    VALUES ($1, $2, 'order_released', '✅ Commande confirmée', $3)
                """, f"notif_{uuid.uuid4().hex[:12]}", order['seller_id'],
                   f"L'acheteur a confirmé la réception. {res['net_seller']} USD viennent d'être crédités sur ton wallet (commission JAPAP {order['commission_pct']}%).")
            except Exception:
                pass
    return {**res, "message": "💸 Paiement libéré au vendeur."}


class DisputeRequest(BaseModel):
    reason: str


@router.post("/orders/{order_id}/dispute")
async def open_dispute(order_id: str, req: DisputeRequest, request: Request):
    """Buyer opens a dispute. Blocks auto-release. Admin must adjudicate."""
    user = await get_current_user(request)
    if not await get_bool("mkt_escrow_dispute_enabled", True):
        raise HTTPException(status_code=503, detail="Les litiges sont temporairement désactivés.")
    reason = (req.reason or "").strip()[:1000]
    if len(reason) < 10:
        raise HTTPException(status_code=400, detail="Motif requis (min 10 caractères)")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT * FROM orders WHERE order_id = $1 AND buyer_id = $2 FOR UPDATE",
                order_id, user['user_id'])
            if not order:
                raise HTTPException(status_code=404, detail="Commande introuvable")
            if order['escrow_status'] != 'held':
                raise HTTPException(status_code=409, detail=f"Commande en statut '{order['escrow_status']}' — litige impossible.")
            now = datetime.now(timezone.utc)
            await conn.execute("""
                UPDATE orders SET escrow_status = 'disputed', status = 'disputed',
                                  dispute_reason = $1, dispute_opened_at = $2, updated_at = $2
                WHERE order_id = $3
            """, reason, now, order_id)
            await conn.execute("""
                INSERT INTO marketplace_escrow_ledger
                  (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, notes, created_by)
                VALUES ($1, $2, 'dispute_opened', $3, 'escrow', $4, $5, $6)
            """, f"led_{uuid.uuid4().hex[:12]}", order_id,
               f"buyer:{user['user_id']}", Decimal(str(order['amount'])),
               f"Dispute: {reason[:200]}", user['user_id'])
            try:
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message)
                    VALUES ($1, $2, 'order_disputed', '⚖️ Litige ouvert', $3)
                """, f"notif_{uuid.uuid4().hex[:12]}", order['seller_id'],
                   f"L'acheteur a ouvert un litige sur \"{order_id}\". Notre équipe va intervenir.")
            except Exception:
                pass
    # ── iter177 — emails post-commit
    try:
        async with pool.acquire() as conn:
            seller_u = await conn.fetchrow(
                "SELECT email, first_name FROM users WHERE user_id=$1", order['seller_id'])
            buyer_u = await conn.fetchrow(
                "SELECT email FROM users WHERE user_id=$1", user['user_id'])
            prod_title = await conn.fetchval(
                "SELECT title FROM products WHERE product_id=$1", order['product_id']) or 'Produit'
        if seller_u and seller_u['email']:
            await send_dispute_opened_seller(
                seller_u['email'],
                seller_first_name=seller_u['first_name'] or '',
                product_title=prod_title, reason=reason,
                order_id=order_id, app_url=_frontend_url())
        admin_email = os.environ.get("ADMIN_NOTIF_EMAIL", "admin@japap.com")
        await send_dispute_opened_admin(
            admin_email,
            buyer_email=(buyer_u['email'] if buyer_u else 'unknown'),
            seller_email=(seller_u['email'] if seller_u else 'unknown'),
            product_title=prod_title, reason=reason,
            order_id=order_id, amount_usd=str(order['amount']),
            app_url=_frontend_url())
    except Exception as e:
        logger.warning(f"[mkt-escrow-email] dispute_opened failed: {e}")
    return {"order_id": order_id, "escrow_status": "disputed",
            "message": "⚖️ Litige enregistré. Notre équipe va trancher sous 48h."}


class AdminResolveRequest(BaseModel):
    decision: Literal["release_seller", "refund_buyer", "split"]
    seller_share_usd: Optional[float] = None  # required if decision='split'
    notes: str = ""


@router.post("/admin/orders/{order_id}/resolve")
async def admin_resolve_dispute(order_id: str, req: AdminResolveRequest, request: Request):
    """Admin adjudicates a disputed order.

    decisions:
      - 'release_seller' : release escrow to seller (commission applied)
      - 'refund_buyer'   : full refund, no commission
      - 'split'          : seller gets seller_share_usd (after commission on
                           that share), buyer gets the remainder.
    """
    from routes.auth import require_admin
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT * FROM orders WHERE order_id = $1 FOR UPDATE", order_id)
            if not order:
                raise HTTPException(status_code=404, detail="Commande introuvable")
            if order['escrow_status'] not in ('held', 'disputed'):
                raise HTTPException(status_code=409, detail=f"Statut '{order['escrow_status']}' — résolution impossible.")
            note_full = f"Admin resolve [{req.decision}] — {req.notes or ''}".strip()

            if req.decision == "release_seller":
                res = await _release_escrow(conn, order_id, actor_id=admin['user_id'],
                                             reason=note_full)
            elif req.decision == "refund_buyer":
                res = await _refund_escrow(conn, order_id, actor_id=admin['user_id'],
                                            reason=note_full)
            elif req.decision == "split":
                if req.seller_share_usd is None or req.seller_share_usd < 0:
                    raise HTTPException(status_code=400, detail="seller_share_usd requis (>=0)")
                amount = Decimal(str(order['amount']))
                seller_share = Decimal(str(req.seller_share_usd)).quantize(Decimal("0.01"))
                if seller_share > amount:
                    raise HTTPException(status_code=400, detail="seller_share_usd > amount")
                buyer_share = (amount - seller_share).quantize(Decimal("0.01"))
                commission_pct = Decimal(str(order['commission_pct']))
                seller_commission = (seller_share * commission_pct / Decimal(100)).quantize(Decimal("0.01"))
                seller_net = (seller_share - seller_commission).quantize(Decimal("0.01"))
                treasury_account = (await get_setting("mkt_escrow_treasury_account") or "japap_treasury").strip() or "japap_treasury"
                now = datetime.now(timezone.utc)

                # Credit seller and buyer
                if seller_net > 0:
                    await conn.execute(
                        "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                        seller_net, now, order['seller_id'])
                if buyer_share > 0:
                    await conn.execute(
                        "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                        buyer_share, now, order['buyer_id'])

                # Tx rows
                if seller_net > 0:
                    tx_s = f"mkt_split_s_{uuid.uuid4().hex[:8]}"
                    await conn.execute("""
                        INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, currency, status, notes, reference)
                        VALUES ($1, $2, $3, 'marketplace_split_seller', $4, 'USD', 'completed', $5, $6)
                    """, tx_s, order['buyer_id'], order['seller_id'], seller_net,
                       "Split admin — seller share net", order_id)
                if buyer_share > 0:
                    tx_b = f"mkt_split_b_{uuid.uuid4().hex[:8]}"
                    await conn.execute("""
                        INSERT INTO transactions (tx_id, to_user_id, type, amount, currency, status, notes, reference)
                        VALUES ($1, $2, 'marketplace_split_buyer', $3, 'USD', 'completed', $4, $5)
                    """, tx_b, order['buyer_id'], buyer_share,
                       "Split admin — buyer refund", order_id)
                if seller_commission > 0:
                    tx_f = f"mkt_split_f_{uuid.uuid4().hex[:8]}"
                    await conn.execute("""
                        INSERT INTO transactions (tx_id, from_user_id, type, amount, currency, status, notes, reference)
                        VALUES ($1, $2, 'marketplace_commission', $3, 'USD', 'completed', $4, $5)
                    """, tx_f, order['buyer_id'], seller_commission,
                       "JAPAP commission on split", order_id)

                # Escrow ledger entries
                if seller_net > 0:
                    await conn.execute("""
                        INSERT INTO marketplace_escrow_ledger
                          (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, notes, created_by)
                        VALUES ($1, $2, 'split_seller', 'escrow', $3, $4, $5, $6)
                    """, f"led_{uuid.uuid4().hex[:12]}", order_id,
                       f"seller:{order['seller_id']}", seller_net, note_full, admin['user_id'])
                if buyer_share > 0:
                    await conn.execute("""
                        INSERT INTO marketplace_escrow_ledger
                          (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, notes, created_by)
                        VALUES ($1, $2, 'split_buyer', 'escrow', $3, $4, $5, $6)
                    """, f"led_{uuid.uuid4().hex[:12]}", order_id,
                       f"buyer:{order['buyer_id']}", buyer_share, note_full, admin['user_id'])
                if seller_commission > 0:
                    await conn.execute("""
                        INSERT INTO marketplace_escrow_ledger
                          (ledger_id, order_id, entry_type, from_account, to_account, amount_usd, notes, created_by)
                        VALUES ($1, $2, 'commission', 'escrow', $3, $4, $5, $6)
                    """, f"led_{uuid.uuid4().hex[:12]}", order_id,
                       treasury_account, seller_commission, note_full, admin['user_id'])

                await conn.execute("""
                    UPDATE orders SET status = 'completed', escrow_status = 'split',
                                      released_at = $1, refunded_at = $1, dispute_resolved_at = $1, updated_at = $1
                    WHERE order_id = $2
                """, now, order_id)
                res = {
                    "order_id": order_id, "split": True,
                    "seller_net": str(seller_net),
                    "buyer_refund": str(buyer_share),
                    "commission": str(seller_commission),
                }

            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'marketplace_admin_resolve', 'marketplace', $2)
            """, admin['user_id'],
               '{"order_id":"%s","decision":"%s","notes":%s}' % (
                   order_id, req.decision,
                   '"' + (req.notes or "").replace('"', '\\"')[:300] + '"'))

    # ── iter177 — emails post-commit (best-effort, both parties)
    try:
        async with pool.acquire() as conn:
            seller_u = await conn.fetchrow(
                "SELECT email, first_name FROM users WHERE user_id=$1", order['seller_id'])
            buyer_u = await conn.fetchrow(
                "SELECT email, first_name FROM users WHERE user_id=$1", order['buyer_id'])
            prod_title = await conn.fetchval(
                "SELECT title FROM products WHERE product_id=$1", order['product_id']) or 'Produit'
        breakdown = {
            "seller_net": res.get("net_seller") or res.get("seller_net") or "0.00",
            "buyer_refund": res.get("amount_usd") if req.decision == "refund_buyer" else (res.get("buyer_refund") or "0.00"),
            "commission": res.get("commission") or "0.00",
        }
        for u, role in ((seller_u, "seller"), (buyer_u, "buyer")):
            if u and u['email']:
                try:
                    await send_dispute_resolved(
                        u['email'], first_name=u['first_name'] or '',
                        role=role, decision=req.decision,
                        product_title=prod_title,
                        amount_usd=str(order['amount']),
                        breakdown=breakdown, notes=req.notes or '',
                        order_id=order_id, app_url=_frontend_url())
                except Exception as e:
                    logger.warning(f"[mkt-escrow-email] resolved {role} failed: {e}")
    except Exception as e:
        logger.warning(f"[mkt-escrow-email] resolved fan-out failed: {e}")

    return {**res, "decision": req.decision, "message": "Litige résolu."}


@router.post("/admin/orders/auto-release/run")
async def admin_auto_release_run(request: Request):
    """Cron-friendly endpoint: release every order whose `auto_release_at` has
    passed AND `escrow_status='held'`. Disputes are skipped — admin must
    resolve them. Returns counts. Idempotent."""
    from routes.auth import require_admin
    admin = await require_admin(request)
    return await sweep_auto_release(actor_id=admin['user_id'])


# ───────────────────────────── PUBLIC HELPERS ─────────────────────────────
async def sweep_auto_release(*, actor_id: str = "system") -> dict:
    """Library function — used by /admin/orders/auto-release/run AND boot/cron."""
    pool = await get_pool()
    released = 0
    errors = 0
    async with pool.acquire() as conn:
        ids = [r['order_id'] for r in await conn.fetch("""
            SELECT order_id FROM orders
            WHERE escrow_status = 'held' AND auto_release_at IS NOT NULL AND auto_release_at <= NOW()
            LIMIT 200
        """)]
    for oid in ids:
        try:
            seller_row = None
            order_row = None
            async with pool.acquire() as conn:
                async with conn.transaction():
                    res = await _release_escrow(conn, oid, actor_id=actor_id,
                                                  reason="Auto-release after silence window")
                    # Notify seller (in-app)
                    try:
                        seller_id = await conn.fetchval("SELECT seller_id FROM orders WHERE order_id=$1", oid)
                        if seller_id:
                            await conn.execute("""
                                INSERT INTO notifications (notif_id, user_id, type, title, message)
                                VALUES ($1, $2, 'order_auto_released', '✅ Libération automatique', $3)
                            """, f"notif_{uuid.uuid4().hex[:12]}", seller_id,
                               f"La commande {oid} a été libérée automatiquement (acheteur silencieux). {res.get('net_seller')} USD crédités.")
                            seller_row = await conn.fetchrow(
                                "SELECT email, first_name FROM users WHERE user_id=$1", seller_id)
                            order_row = await conn.fetchrow(
                                "SELECT title FROM products p JOIN orders o ON o.product_id = p.product_id "
                                "WHERE o.order_id=$1", oid)
                    except Exception:
                        pass
                    released += 1
            # Email post-commit (best-effort)
            try:
                if seller_row and seller_row['email'] and order_row:
                    await send_order_auto_released(
                        seller_row['email'],
                        seller_first_name=seller_row['first_name'] or '',
                        product_title=order_row['title'],
                        net_usd=res.get('net_seller', '0.00'),
                        commission_usd=res.get('commission', '0.00'),
                        commission_pct=str(res.get('commission_pct', '2')),
                        order_id=oid,
                        app_url=_frontend_url(),
                    )
            except Exception as e:
                logger.warning(f"[mkt-escrow-email] auto_released failed: {e}")
        except Exception as e:
            logger.warning(f"[auto-release] {oid} skipped: {e}")
            errors += 1
    return {"released": released, "errors": errors, "candidates": len(ids)}


@router.get("/orders")
async def list_orders(request: Request, role: str = Query("buyer", pattern="^(buyer|seller)$")):
    """List my orders (escrow-aware): buyer view OR seller view.
    Returns escrow_status, auto_release_at, dispute fields."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        field = "buyer_id" if role == "buyer" else "seller_id"
        rows = await conn.fetch(f"""
            SELECT o.*, p.title as product_title, p.images as product_images
            FROM orders o JOIN products p ON o.product_id = p.product_id
            WHERE o.{field} = $1 ORDER BY o.created_at DESC
        """, user['user_id'])
        out = []
        for r in rows:
            o = dict(r)
            o['amount'] = str(o['amount'])
            o['fee'] = str(o['fee'])
            o['commission_pct'] = str(o.get('commission_pct') or 0)
            o['created_at'] = o['created_at'].isoformat()
            o['updated_at'] = o['updated_at'].isoformat() if o.get('updated_at') else None
            for k in ('auto_release_at', 'dispute_opened_at', 'dispute_resolved_at',
                      'released_at', 'refunded_at'):
                if o.get(k):
                    o[k] = o[k].isoformat()
            out.append(o)
        return out


@router.get("/orders/{order_id}/ledger")
async def order_ledger(order_id: str, request: Request):
    """Buyer / seller / admin can view the escrow movements of their order
    (full audit trail)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        order = await conn.fetchrow("SELECT * FROM orders WHERE order_id = $1", order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Commande introuvable")
        is_party = user['user_id'] in (order['buyer_id'], order['seller_id'])
        is_admin = user.get('role') in ('admin', 'superadmin')
        if not (is_party or is_admin):
            raise HTTPException(status_code=403, detail="Accès non autorisé")
        rows = await conn.fetch("""
            SELECT ledger_id, entry_type, from_account, to_account, amount_usd,
                   tx_id, notes, created_by, created_at
            FROM marketplace_escrow_ledger
            WHERE order_id = $1 ORDER BY created_at ASC
        """, order_id)
    return {
        "order_id": order_id,
        "escrow_status": order['escrow_status'],
        "amount_usd": str(order['amount']),
        "commission_pct": str(order.get('commission_pct') or 0),
        "fee_usd": str(order['fee']),
        "auto_release_at": order['auto_release_at'].isoformat() if order.get('auto_release_at') else None,
        "ledger": [{
            "ledger_id": r['ledger_id'], "entry_type": r['entry_type'],
            "from_account": r['from_account'], "to_account": r['to_account'],
            "amount_usd": str(r['amount_usd']), "tx_id": r['tx_id'],
            "notes": r['notes'], "created_by": r['created_by'],
            "created_at": r['created_at'].isoformat(),
        } for r in rows],
    }


@router.get("/admin/orders/disputes")
async def admin_list_disputes(request: Request,
                                 limit: int = Query(50, ge=1, le=200)):
    """Admin queue of disputes to resolve."""
    from routes.auth import require_admin
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT o.*, p.title AS product_title, p.images AS product_images,
                   bu.email AS buyer_email, su.email AS seller_email
            FROM orders o
            JOIN products p ON o.product_id = p.product_id
            JOIN users bu ON bu.user_id = o.buyer_id
            JOIN users su ON su.user_id = o.seller_id
            WHERE o.escrow_status = 'disputed'
            ORDER BY o.dispute_opened_at DESC NULLS LAST
            LIMIT $1
        """, limit)
    items = []
    for r in rows:
        d = dict(r)
        d['amount'] = str(d['amount'])
        d['fee'] = str(d['fee'])
        d['commission_pct'] = str(d.get('commission_pct') or 0)
        d['created_at'] = d['created_at'].isoformat()
        if d.get('dispute_opened_at'):
            d['dispute_opened_at'] = d['dispute_opened_at'].isoformat()
        if d.get('auto_release_at'):
            d['auto_release_at'] = d['auto_release_at'].isoformat()
        items.append(d)
    return {"items": items, "total": len(items)}


@router.get("/categories")
async def get_categories(request: Request):
    await get_current_user(request)
    return [
        {"id": "electronics", "name": "Electronique", "icon": "devices"},
        {"id": "clothing", "name": "Vetements", "icon": "tshirt"},
        {"id": "food", "name": "Alimentation", "icon": "hamburger"},
        {"id": "home", "name": "Maison", "icon": "house"},
        {"id": "beauty", "name": "Beaute", "icon": "sparkle"},
        {"id": "sports", "name": "Sports", "icon": "basketball"},
        {"id": "vehicles", "name": "Vehicules", "icon": "car"},
        {"id": "services", "name": "Services", "icon": "wrench"},
        {"id": "general", "name": "Autres", "icon": "package"},
    ]



# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — Reviews / Favourites / Coupons / Boost / Seller dashboard
# ═══════════════════════════════════════════════════════════════════════════
class CreateReviewRequest(BaseModel):
    rating: int                   # 1-5
    comment: str = ""


class CreateCouponRequest(BaseModel):
    code: str
    discount_pct: int = 0
    discount_flat_usd: float = 0
    scope: str = "all"            # 'all' | 'product' | 'category'
    scope_value: str = ""
    max_uses: int = 0             # 0 = illimité
    valid_until: Optional[str] = None  # ISO datetime


class BoostProductRequest(BaseModel):
    product_id: str
    days: int = 7                  # 1-30


# ---------- Reviews ----------
@router.post("/products/{product_id}/reviews")
async def create_review(product_id: str, req: CreateReviewRequest, request: Request):
    """Any authenticated buyer who successfully confirmed an order on this product
    may leave a 1-5★ review with a comment. One review per (product, author)."""
    user = await get_current_user(request)
    if req.rating < 1 or req.rating > 5:
        raise HTTPException(status_code=400, detail="Note entre 1 et 5")
    pool = await get_pool()
    async with pool.acquire() as conn:
        # must have a completed order
        has_order = await conn.fetchval("""
            SELECT 1 FROM orders
            WHERE product_id = $1 AND buyer_id = $2 AND status = 'completed' LIMIT 1
        """, product_id, user['user_id'])
        if not has_order:
            raise HTTPException(status_code=403, detail="ORDER_REQUIRED — Seuls les acheteurs confirmés peuvent noter ce produit.")

        review_id = f"rev_{uuid.uuid4().hex[:12]}"
        try:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO product_reviews (review_id, product_id, author_id, rating, comment)
                    VALUES ($1, $2, $3, $4, $5)
                """, review_id, product_id, user['user_id'], req.rating, (req.comment or "").strip()[:1000])
                # refresh rolling aggregates
                agg = await conn.fetchrow("""
                    SELECT AVG(rating)::numeric(3,2) AS avg, COUNT(*) AS cnt
                    FROM product_reviews WHERE product_id = $1
                """, product_id)
                await conn.execute("""
                    UPDATE products SET rating_avg = $1, rating_count = $2, updated_at = NOW()
                    WHERE product_id = $3
                """, agg['avg'], agg['cnt'], product_id)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(status_code=409, detail="Vous avez déjà noté ce produit.")
            raise
    return {"review_id": review_id, "message": "Avis publié"}


@router.get("/products/{product_id}/reviews")
async def list_reviews(product_id: str, request: Request,
                        page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50)):
    await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.*, u.first_name, u.last_name, u.username, u.avatar, u.is_pro
            FROM product_reviews r JOIN users u ON r.author_id = u.user_id
            WHERE r.product_id = $1 ORDER BY r.created_at DESC
            LIMIT $2 OFFSET $3
        """, product_id, limit, offset)
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM product_reviews WHERE product_id = $1", product_id)
    return {
        "items": [{
            "review_id": r['review_id'],
            "rating": r['rating'],
            "comment": r['comment'],
            "author": {
                "user_id": r['author_id'],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r['username'],
                "avatar": r['avatar'] or '', "is_pro": bool(r['is_pro']),
            },
            "created_at": r['created_at'].isoformat(),
        } for r in rows],
        "total": total, "page": page, "limit": limit,
    }


# ---------- Favourites / Wishlist ----------
@router.post("/products/{product_id}/favourite")
async def toggle_favourite(product_id: str, request: Request):
    """Toggle favourite on/off. Returns {favourited: bool, count: int}."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existed = await conn.fetchval("""
                DELETE FROM product_favourites
                WHERE user_id = $1 AND product_id = $2 RETURNING 1
            """, user['user_id'], product_id)
            if existed:
                await conn.execute(
                    "UPDATE products SET favourites_count = GREATEST(favourites_count - 1, 0) WHERE product_id = $1",
                    product_id)
                favourited = False
            else:
                await conn.execute("""
                    INSERT INTO product_favourites (user_id, product_id) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, user['user_id'], product_id)
                await conn.execute(
                    "UPDATE products SET favourites_count = favourites_count + 1 WHERE product_id = $1",
                    product_id)
                favourited = True
            count = await conn.fetchval(
                "SELECT favourites_count FROM products WHERE product_id = $1", product_id) or 0
    return {"favourited": favourited, "count": int(count)}


@router.get("/favourites")
async def my_favourites(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.*, u.username, u.first_name, u.last_name, u.is_pro
            FROM product_favourites f
            JOIN products p ON p.product_id = f.product_id
            JOIN users u ON u.user_id = p.seller_id
            WHERE f.user_id = $1 ORDER BY f.created_at DESC
            LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
    return {"items": [{
        "product_id": r['product_id'], "title": r['title'],
        "price": str(r['price']), "images": r['images'],
        "rating_avg": str(r['rating_avg'] or 0), "rating_count": r['rating_count'],
        "seller_name": (f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r['username']),
        "is_pro_seller": bool(r['is_pro']),
    } for r in rows]}


# ---------- Coupons ----------
@router.post("/coupons")
async def create_coupon(req: CreateCouponRequest, request: Request):
    user = await get_current_user(request)
    code = (req.code or "").strip().upper()
    if not code or len(code) > 40:
        raise HTTPException(status_code=400, detail="Code invalide (1-40 caractères)")
    if req.discount_pct < 0 or req.discount_pct > 100:
        raise HTTPException(status_code=400, detail="Remise % entre 0 et 100")
    valid_until = None
    if req.valid_until:
        try:
            valid_until = datetime.fromisoformat(req.valid_until.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="valid_until invalide (ISO 8601)")
    coupon_id = f"cpn_{uuid.uuid4().hex[:12]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO product_coupons (coupon_id, seller_id, code, discount_pct, discount_flat_usd,
                    scope, scope_value, max_uses, valid_until)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """, coupon_id, user['user_id'], code, req.discount_pct,
               Decimal(str(req.discount_flat_usd or 0)), req.scope, req.scope_value or "",
               max(0, req.max_uses), valid_until)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(status_code=409, detail="Code déjà utilisé par votre boutique.")
            raise
    return {"coupon_id": coupon_id, "code": code, "message": "Coupon créé"}


@router.get("/coupons/mine")
async def my_coupons(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM product_coupons WHERE seller_id = $1 ORDER BY created_at DESC
        """, user['user_id'])
    return [{
        "coupon_id": r['coupon_id'], "code": r['code'],
        "discount_pct": r['discount_pct'],
        "discount_flat_usd": str(r['discount_flat_usd']),
        "scope": r['scope'], "scope_value": r['scope_value'],
        "max_uses": r['max_uses'], "uses_count": r['uses_count'],
        "valid_until": r['valid_until'].isoformat() if r['valid_until'] else None,
        "is_active": bool(r['is_active']),
        "created_at": r['created_at'].isoformat(),
    } for r in rows]


@router.delete("/coupons/{coupon_id}")
async def delete_coupon(coupon_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT seller_id FROM product_coupons WHERE coupon_id = $1", coupon_id)
        if not row:
            raise HTTPException(status_code=404, detail="Coupon introuvable")
        if row['seller_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")
        await conn.execute("DELETE FROM product_coupons WHERE coupon_id = $1", coupon_id)
    return {"status": "deleted"}


@router.post("/coupons/validate")
async def validate_coupon(request: Request, code: str, product_id: str):
    """Buyer-side: check whether a code applies to a product and returns the
    effective discount. Does NOT consume a use — consumption happens on order
    creation (future work)."""
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT product_id, seller_id, price, category FROM products WHERE product_id = $1", product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit introuvable")
        coupon = await conn.fetchrow("""
            SELECT * FROM product_coupons
            WHERE seller_id = $1 AND code = $2 AND is_active = TRUE
        """, product['seller_id'], code.upper().strip())
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon invalide ou expiré")
    if coupon['valid_until'] and coupon['valid_until'] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Coupon expiré")
    if coupon['max_uses'] > 0 and coupon['uses_count'] >= coupon['max_uses']:
        raise HTTPException(status_code=410, detail="Coupon épuisé")
    if coupon['scope'] == 'product' and coupon['scope_value'] != product_id:
        raise HTTPException(status_code=400, detail="Coupon non applicable à ce produit")
    if coupon['scope'] == 'category' and coupon['scope_value'] != product['category']:
        raise HTTPException(status_code=400, detail="Coupon non applicable à cette catégorie")

    price = Decimal(str(product['price']))
    discount = Decimal("0")
    if coupon['discount_pct'] > 0:
        discount += (price * Decimal(coupon['discount_pct']) / Decimal(100))
    if coupon['discount_flat_usd'] and coupon['discount_flat_usd'] > 0:
        discount += Decimal(str(coupon['discount_flat_usd']))
    final = max(price - discount, Decimal("0"))
    return {
        "code": coupon['code'],
        "original_price": str(price),
        "discount": str(discount.quantize(Decimal("0.01"))),
        "final_price": str(final.quantize(Decimal("0.01"))),
        "discount_pct": coupon['discount_pct'],
        "discount_flat_usd": str(coupon['discount_flat_usd']),
    }


# ═══════════════════════════════════════════════════════════════════════════
# iter175 — Sponsored Boosts (Wallet USD only) + Featured + Expiry
# ═══════════════════════════════════════════════════════════════════════════
class BoostWalletRequest(BaseModel):
    plan: Literal["basic_24h", "standard_7d", "homepage_30d"]
    # iter179 — Audience targeting (optional, defaults = global / all ages)
    target_countries: Optional[list[str]] = None  # ISO2 codes
    is_global: bool = True
    age_min: Optional[int] = None
    age_max: Optional[int] = None


@router.get("/boost/plans")
async def boost_plans_catalog(request: Request):
    """Public catalogue (auth required) of paid boost plans. Single source of
    truth for both backend pricing and frontend modal. **Live-read from
    admin_settings** — admin can flip price/duration without redeploy."""
    await get_current_user(request)
    plans = await get_boost_plans()
    enabled = await get_bool("mkt_boost_enabled", True)
    return {
        "currency": "USD",
        "enabled": enabled,
        "plans": [
            {"plan": k, "label": v["label"],
             "price_usd": str(v["price_usd"]), "days": v["days"],
             "is_homepage": v["is_homepage"]}
            for k, v in plans.items()
        ],
    }


@router.post("/products/{product_id}/boost-wallet")
async def boost_product_via_wallet(product_id: str, req: BoostWalletRequest, request: Request):
    """Pay a sponsored boost from the canonical USD JAPAP wallet.

    Rules (CEO):
      • Owner only.
      • Wallet balance must cover the plan price (USD canonical).
      • Atomic transaction: balance debit + product flag + boost row + tx ledger.
      • Duration extends from MAX(now, current expiry) so renewals stack.
      • Homepage plan also flips `is_homepage_featured`.
      • NO external PSP. Wallet only.
      • Prices/durations LIVE-READ from admin_settings (zero hardcode).
    """
    user = await get_current_user(request)
    if not await get_bool("mkt_boost_enabled", True):
        raise HTTPException(status_code=503, detail="Les boosts marketplace sont temporairement désactivés.")
    plans = await get_boost_plans()
    plan_def = plans.get(req.plan)
    if not plan_def:
        raise HTTPException(status_code=400, detail="Plan invalide")
    price = plan_def["price_usd"]
    days = plan_def["days"]
    is_homepage = plan_def["is_homepage"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prod = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id = $1 FOR UPDATE", product_id)
            if not prod:
                raise HTTPException(status_code=404, detail="Produit introuvable")
            if prod['seller_id'] != user['user_id']:
                raise HTTPException(status_code=403, detail="Non autorisé — vous n'êtes pas le vendeur de ce produit")
            if prod['status'] == 'deleted':
                raise HTTPException(status_code=400, detail="Produit supprimé — boost impossible")

            # Wallet (USD canonical since iter158)
            wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not wallet:
                raise HTTPException(status_code=404, detail="Wallet introuvable")
            if wallet['is_locked']:
                raise HTTPException(status_code=403, detail="Wallet verrouillé")
            if Decimal(str(wallet['balance'])) < price:
                raise HTTPException(
                    status_code=400,
                    detail=f"INSUFFICIENT_BALANCE — Solde insuffisant. {price} USD requis, recharge ton wallet pour booster.")

            now = datetime.now(timezone.utc)
            # Extend from current boost expiry if still active
            base = prod['boost_expires_at'] if (
                prod['is_boosted'] and prod['boost_expires_at'] and prod['boost_expires_at'] > now
            ) else now
            new_expiry = base + timedelta(days=days)

            # Homepage tracking is independent (only set for homepage_30d)
            new_homepage = bool(prod['is_homepage_featured'] and prod['homepage_expires_at'] and prod['homepage_expires_at'] > now)
            new_homepage_expiry = prod['homepage_expires_at'] if new_homepage else None
            if is_homepage:
                hp_base = new_homepage_expiry if new_homepage_expiry and new_homepage_expiry > now else now
                new_homepage_expiry = hp_base + timedelta(days=days)
                new_homepage = True

            # Debit wallet (USD canonical)
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = NOW() WHERE user_id = $2",
                price, user['user_id'])

            tx_id = f"boost_{uuid.uuid4().hex[:12]}"
            boost_id = f"bst_{uuid.uuid4().hex[:12]}"
            # iter179 — sanitize targeting per admin settings
            targeting_on = await get_bool("targeting_enabled", True)
            allow_ctry = await get_bool("allow_country_filter", True) and targeting_on
            allow_age = await get_bool("allow_age_filter", True) and targeting_on
            ctries = []
            if allow_ctry and req.target_countries and not req.is_global:
                ctries = list({(c or "").upper().strip()[:2]
                               for c in req.target_countries if c})[:50]
                ctries = [c for c in ctries if len(c) == 2]
            is_global_b = bool(req.is_global) or not ctries
            amin = int(req.age_min) if (allow_age and req.age_min is not None) else None
            amax = int(req.age_max) if (allow_age and req.age_max is not None) else None
            if amin is not None and amin < 13:
                amin = 13
            if amax is not None and amax > 99:
                amax = 99
            if amin is not None and amax is not None and amin > amax:
                amin, amax = amax, amin
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, type, amount, currency, status, notes, reference)
                VALUES ($1, $2, 'product_boost', $3, 'USD', 'completed', $4, $5)
            """, tx_id, user['user_id'], price,
               f"Boost Marketplace · {plan_def['label']} · {prod['title'][:80]}",
               product_id)

            await conn.execute("""
                INSERT INTO product_boosts
                  (boost_id, product_id, seller_id, plan, price_usd, tx_id,
                   starts_at, expires_at, is_homepage, status,
                   target_countries, is_global, age_min, age_max)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'active',
                        $10, $11, $12, $13)
            """, boost_id, product_id, user['user_id'], req.plan, price, tx_id,
               now, new_expiry, is_homepage,
               ctries or None, is_global_b, amin, amax)

            await conn.execute("""
                UPDATE products SET
                    is_boosted = TRUE,
                    boost_expires_at = $1,
                    is_homepage_featured = $2,
                    homepage_expires_at = $3,
                    last_boost_plan = $4,
                    updated_at = NOW()
                WHERE product_id = $5
            """, new_expiry, new_homepage, new_homepage_expiry, req.plan, product_id)

            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'marketplace_boost', 'marketplace',
                        $2)
            """, user['user_id'],
               f'{{"product_id":"{product_id}","plan":"{req.plan}","price_usd":"{price}","boost_id":"{boost_id}","tx_id":"{tx_id}"}}')

            try:
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message)
                    VALUES ($1, $2, 'product_boost_active', $3, $4)
                """, f"notif_{uuid.uuid4().hex[:12]}", user['user_id'],
                   f"🚀 Boost activé — {plan_def['label']}",
                   f"Ton produit \"{prod['title']}\" est boosté jusqu'au {new_expiry.strftime('%d/%m/%Y %H:%M UTC')}. Coût : {price} USD prélevé du wallet.")
            except Exception as e:
                logger.warning(f"[mkt-boost] notif failed: {e}")

    return {
        "boost_id": boost_id,
        "tx_id": tx_id,
        "plan": req.plan,
        "price_usd": str(price),
        "expires_at": new_expiry.isoformat(),
        "is_homepage": is_homepage,
        "homepage_expires_at": new_homepage_expiry.isoformat() if new_homepage_expiry else None,
        "message": f"{plan_def['label']} activé ✅",
    }


@router.get("/featured")
async def list_featured_products(request: Request,
                                   limit: int = Query(12, ge=1, le=30)):
    """iter179 — Homepage 'Produits Vedettes' filtered by audience targeting.
    A homepage-boosted product is shown to user U only if:
      • boost.is_global is TRUE, OR
      • boost.target_countries contains U.country_code, OR
      • U has no country (graceful fallback — never block visibility entirely)
      • AND boost age range matches U's age (or U has no birthday → fallback)
    """
    user = await get_current_user(request)
    user_country = (user.get("country_code") or user.get("country") or "").upper().strip() or None
    # compute user age (whole years) — None if unknown
    user_age = None
    bd = user.get("birthday")
    if bd:
        try:
            from datetime import date as _date, datetime as _dt
            if isinstance(bd, str):
                bd = _dt.fromisoformat(bd[:10]).date()
            elif hasattr(bd, 'date'):
                bd = bd.date()
            today = _date.today()
            user_age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except Exception:
            user_age = None
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username,
                   u.is_verified, u.is_pro,
                   EXISTS(SELECT 1 FROM kyc_verifications kv
                          WHERE kv.user_id = p.seller_id AND kv.status = 'approved')
                          AS seller_kyc_verified,
                   pb.target_countries, pb.is_global, pb.age_min, pb.age_max
            FROM products p
            JOIN users u ON p.seller_id = u.user_id
            LEFT JOIN LATERAL (
                SELECT target_countries, is_global, age_min, age_max
                FROM product_boosts
                WHERE product_id = p.product_id
                  AND status = 'active'
                  AND is_homepage = TRUE
                  AND expires_at > NOW()
                ORDER BY expires_at DESC LIMIT 1
            ) pb ON TRUE
            WHERE p.status = 'active'
              AND p.is_homepage_featured = TRUE
              AND (p.homepage_expires_at IS NULL OR p.homepage_expires_at > NOW())
              -- Country filter (with graceful fallback)
              AND (
                pb.is_global IS NULL OR pb.is_global = TRUE
                OR pb.target_countries IS NULL
                OR cardinality(pb.target_countries) = 0
                OR $2::text IS NULL
                OR $2::text = ANY(pb.target_countries)
              )
              -- Age filter (with graceful fallback)
              AND (
                $3::int IS NULL
                OR (pb.age_min IS NULL OR $3::int >= pb.age_min)
              )
              AND (
                $3::int IS NULL
                OR (pb.age_max IS NULL OR $3::int <= pb.age_max)
              )
            ORDER BY p.homepage_expires_at DESC NULLS LAST, p.updated_at DESC
            LIMIT $1
        """, limit, user_country, user_age)
        items = []
        for r in rows:
            p = dict(r)
            p['price'] = str(p['price'])
            p['rating_avg'] = str(p.get('rating_avg') or 0)
            p['created_at'] = p['created_at'].isoformat()
            p['updated_at'] = p['updated_at'].isoformat() if p.get('updated_at') else None
            if p.get('homepage_expires_at'):
                p['homepage_expires_at'] = p['homepage_expires_at'].isoformat()
            if p.get('boost_expires_at'):
                p['boost_expires_at'] = p['boost_expires_at'].isoformat()
            if isinstance(p.get('images'), str):
                try:
                    import json as _json
                    p['images'] = _json.loads(p['images'] or '[]')
                except Exception:
                    p['images'] = []
            items.append(p)

        # iter180 — Add up to 2 sponsored slots on homepage featured (audience-filtered).
        sponsored_slots = []
        try:
            if await get_bool("ads_enabled", True):
                from routes.ads_console import pick_sponsored_for_user
                picked = await pick_sponsored_for_user(conn, user, limit=4)
                seen = set()
                for ad in picked:
                    if len(sponsored_slots) >= 2:
                        break
                    pid = ad.get('product_id')
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    sponsored_slots.append({
                        "campaign_id": ad.get('campaign_id'),
                        "product_id": pid,
                        "title": ad.get('product_title') or '',
                        "images": ad.get('product_images') or [],
                        "price": ad.get('product_price') or "0",
                        "currency": ad.get('product_currency') or 'USD',
                        "seller_id": ad.get('product_seller_id'),
                        "sponsored": True,
                        "ad_label": "Sponsorisé",
                    })
        except Exception as _e:  # pragma: no cover
            logger.warning(f"[featured] sponsored injection failed: {_e}")
            sponsored_slots = []

        return {"items": items, "total": len(items), "sponsored_slots": sponsored_slots}


@router.post("/admin/boosts/sweep-expired")
async def admin_sweep_expired_boosts(request: Request):
    """Admin / cron endpoint: flag expired boosts. Idempotent. Returns count
    of products flipped. Safe to call from a worker every 5 minutes."""
    from routes.auth import require_admin
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Expire product flags
        prods_expired = await conn.fetchval("""
            WITH expired AS (
                UPDATE products
                SET is_boosted = FALSE, updated_at = NOW()
                WHERE is_boosted = TRUE
                  AND boost_expires_at IS NOT NULL
                  AND boost_expires_at <= NOW()
                RETURNING 1
            )
            SELECT COUNT(*) FROM expired
        """) or 0
        homepage_expired = await conn.fetchval("""
            WITH expired AS (
                UPDATE products
                SET is_homepage_featured = FALSE, updated_at = NOW()
                WHERE is_homepage_featured = TRUE
                  AND homepage_expires_at IS NOT NULL
                  AND homepage_expires_at <= NOW()
                RETURNING 1
            )
            SELECT COUNT(*) FROM expired
        """) or 0
        boosts_expired = await conn.fetchval("""
            WITH expired AS (
                UPDATE product_boosts SET status = 'expired'
                WHERE status = 'active' AND expires_at <= NOW()
                RETURNING 1
            )
            SELECT COUNT(*) FROM expired
        """) or 0
    return {
        "products_unboosted": int(prods_expired),
        "homepage_cleared": int(homepage_expired),
        "boost_rows_expired": int(boosts_expired),
    }


# ---------- Public sweep helper (boot-time + worker) ----------
async def sweep_expired_boosts() -> dict:
    """Library function used by the boot-time hook and any scheduled worker.
    Same behaviour as /admin/boosts/sweep-expired but no auth — caller is
    trusted (in-process)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        a = await conn.fetchval("""
            WITH e AS (UPDATE products SET is_boosted = FALSE, updated_at = NOW()
                       WHERE is_boosted = TRUE AND boost_expires_at IS NOT NULL AND boost_expires_at <= NOW()
                       RETURNING 1) SELECT COUNT(*) FROM e
        """) or 0
        b = await conn.fetchval("""
            WITH e AS (UPDATE products SET is_homepage_featured = FALSE, updated_at = NOW()
                       WHERE is_homepage_featured = TRUE AND homepage_expires_at IS NOT NULL AND homepage_expires_at <= NOW()
                       RETURNING 1) SELECT COUNT(*) FROM e
        """) or 0
        c = await conn.fetchval("""
            WITH e AS (UPDATE product_boosts SET status = 'expired'
                       WHERE status = 'active' AND expires_at <= NOW() RETURNING 1)
            SELECT COUNT(*) FROM e
        """) or 0
    return {"products_unboosted": int(a), "homepage_cleared": int(b), "boost_rows_expired": int(c)}
@router.post("/products/boost")
async def boost_product(req: BoostProductRequest, request: Request):
    """Pro sellers can boost one of their products to the top of `smart` sort
    for N days (max 30). Pro status checked live via subscriptions table."""
    user = await get_current_user(request)
    if req.days < 1 or req.days > 30:
        raise HTTPException(status_code=400, detail="Durée entre 1 et 30 jours")
    pool = await get_pool()
    async with pool.acquire() as conn:
        prod = await conn.fetchrow("SELECT seller_id FROM products WHERE product_id = $1", req.product_id)
        if not prod:
            raise HTTPException(status_code=404, detail="Produit introuvable")
        if prod['seller_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")
        is_pro = await conn.fetchval("""
            SELECT 1 FROM subscriptions
            WHERE user_id = $1 AND status = 'active' AND expires_at > NOW() LIMIT 1
        """, user['user_id'])
        if not is_pro:
            raise HTTPException(status_code=403, detail="PRO_REQUIRED:boost — Abonnez-vous JAPAP Pro pour booster vos produits.")
        from datetime import timedelta
        expires = datetime.now(timezone.utc) + timedelta(days=req.days)
        await conn.execute("""
            UPDATE products SET is_boosted = TRUE, boost_expires_at = $1, updated_at = NOW()
            WHERE product_id = $2
        """, expires, req.product_id)
    return {"boost_expires_at": expires.isoformat(), "days": req.days, "message": f"Produit boosté {req.days} jours"}


# ---------- Seller Dashboard ----------
@router.get("/dashboard/me")
async def seller_dashboard(request: Request):
    """Aggregated seller stats: orders, revenue, top products, avg rating,
    conversion (orders / product views), last 30 days activity."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        products_count = await conn.fetchval(
            "SELECT COUNT(*) FROM products WHERE seller_id = $1 AND status = 'active'", user['user_id']) or 0
        all_time = await conn.fetchrow("""
            SELECT COUNT(o.order_id) AS orders,
                   COALESCE(SUM(CASE WHEN o.status = 'completed' THEN o.amount - o.fee ELSE 0 END), 0) AS revenue,
                   COALESCE(SUM(p.views_count), 0) AS total_views
            FROM products p LEFT JOIN orders o ON o.product_id = p.product_id AND o.seller_id = p.seller_id
            WHERE p.seller_id = $1
        """, user['user_id'])
        last_30d = await conn.fetchrow("""
            SELECT COUNT(o.order_id) AS orders,
                   COALESCE(SUM(CASE WHEN o.status = 'completed' THEN o.amount - o.fee ELSE 0 END), 0) AS revenue
            FROM orders o WHERE o.seller_id = $1 AND o.created_at > NOW() - INTERVAL '30 days'
        """, user['user_id'])
        top = await conn.fetch("""
            SELECT p.product_id, p.title, p.price, p.images, p.views_count, p.rating_avg, p.rating_count,
                   COUNT(o.order_id) FILTER (WHERE o.status = 'completed') AS sales
            FROM products p LEFT JOIN orders o ON o.product_id = p.product_id
            WHERE p.seller_id = $1
            GROUP BY p.product_id, p.title, p.price, p.images, p.views_count, p.rating_avg, p.rating_count
            ORDER BY sales DESC, p.views_count DESC LIMIT 5
        """, user['user_id'])
        avg_rating = await conn.fetchval("""
            SELECT AVG(rating_avg) FROM products WHERE seller_id = $1 AND rating_count > 0
        """, user['user_id'])
    total_views = int(all_time['total_views'] or 0)
    total_orders = int(all_time['orders'] or 0)
    conversion = round((total_orders / total_views * 100), 2) if total_views > 0 else 0.0
    return {
        "products_count": products_count,
        "all_time": {
            "orders": total_orders,
            "revenue_local": str(all_time['revenue'] or 0),
            "total_views": total_views,
            "conversion_pct": conversion,
        },
        "last_30d": {
            "orders": int(last_30d['orders'] or 0),
            "revenue_local": str(last_30d['revenue'] or 0),
        },
        "avg_rating": str(avg_rating or 0),
        "top_products": [{
            "product_id": t['product_id'], "title": t['title'],
            "price": str(t['price']), "images": t['images'],
            "views": int(t['views_count'] or 0),
            "sales": int(t['sales'] or 0),
            "rating_avg": str(t['rating_avg'] or 0),
            "rating_count": int(t['rating_count'] or 0),
        } for t in top],
    }
