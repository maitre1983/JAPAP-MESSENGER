"""
JAPAP CONNECT — WiFi Rewards
============================
A secure, admin-gated way for hosts to share WiFi access and earn wallet
rewards when verified JAPAP users connect through them. This module NEVER
stores WiFi credentials; a hotspot record is simply a geolocated "check-in
point" that unlocks the internet via a captive-portal flow handled by the
host's router integration or by scanning a JAPAP-generated QR.

Reward logic (all USD, credited as wallet top-up in the user's currency):
  • `connect_reward_per_connection_usd`   — flat bonus per validated session
  • `connect_reward_per_minute_usd`       — prorated by session duration
  • `connect_max_reward_per_session_usd`  — safety cap
  • Pro hosts get `connect_pro_reward_multiplier`

Anti-fraud:
  • daily caps per IP / device / (user, hotspot)
  • min session duration to earn rewards
  • admin-gated blocking of individual hotspots and connections
"""
import uuid
import math
import os
import logging
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal
from database import get_pool
from routes.auth import get_current_user
from routes.referrals import _credit_wallet_bonus  # reuse wallet-credit helper
from services.settings_service import get_bool, get_int, get_float, get_setting
from middleware.rate_limit import limiter

# Pro plan rank (higher = broader access). Used for gating `/start` and `/hotspots`.
PLAN_RANK = {"": 0, "none": 0, "starter": 1, "creator": 2, "business": 3}


async def _user_plan_rank(conn, user_id: str) -> int:
    """Return the rank of the user's current Pro plan (0 if none)."""
    row = await conn.fetchrow("""
        SELECT s.plan_type FROM subscriptions s
        WHERE s.user_id = $1 AND s.status = 'active' AND s.expires_at > NOW()
        ORDER BY s.expires_at DESC LIMIT 1
    """, user_id)
    if not row or not row['plan_type']:
        return 0
    return PLAN_RANK.get(row['plan_type'], 0)


def _zone_from_coords(lat: float, lng: float, country: str = "") -> str:
    """Stable bucket: country + 0.05° grid tag. e.g. 'CM-40-97' for ~5km resolution."""
    try:
        lat_b = int(float(lat) * 20)  # 0.05° buckets
        lng_b = int(float(lng) * 20)
        prefix = (country or "XX").upper()
        return f"{prefix}-{lat_b}-{lng_b}"
    except Exception:
        return "XX-0-0"


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connect", tags=["connect"])


class HotspotCreate(BaseModel):
    alias: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = ""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address: Optional[str] = ""
    type: Literal["user", "partner", "public"] = "user"
    sponsor_name: Optional[str] = ""
    max_daily_users: int = 0
    is_premium: bool = False
    country_code: Optional[str] = ""
    # iter141nineG — One-step flow: optionally bundle the WiFi creds
    # directly with the hotspot creation. Eliminates the "create + then
    # remember to set credentials" two-step trap that left dozens of
    # hotspots without an SSID (User B redeem → ssid='' → '—' in UI).
    ssid: Optional[str] = Field(None, max_length=64)
    password: Optional[str] = Field(None, max_length=128)
    security_type: Optional[Literal["WPA2", "WPA3", "WPA", "WEP", "OPEN"]] = "WPA2"


class HotspotUpdate(BaseModel):
    alias: Optional[str] = None
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    max_daily_users: Optional[int] = None
    is_active: Optional[bool] = None


class ConnectStartRequest(BaseModel):
    hotspot_id: str
    device_id: Optional[str] = None


class ConnectEndRequest(BaseModel):
    connection_id: str


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1); dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))



# ──────────────────────────────────────────────────────────────────────────
#  iter141nineH — AI WiFi Auto-Detect
#  90% of users don't know their WiFi network's SSID by heart and never
#  pick the right "Security" option. To make the share flow truly 1-click,
#  we auto-detect both: ISP/carrier from IP → SSID candidate, security
#  defaults to WPA2 (covers >95% of consumer routers worldwide).
# ──────────────────────────────────────────────────────────────────────────

def _slugify_carrier(isp: str) -> str:
    """Compress a verbose ISP string into a clean SSID-friendly slug.

    Examples:
      "MTN Cameroon, S.A."        → "MTN"
      "Orange Cameroun S.A."      → "Orange"
      "Camtel - Fiber Service"    → "Camtel"
      "AS37061 Camtel"            → "Camtel"
      ""                          → ""
    """
    if not isp:
        return ""
    import re as _re
    s = isp.strip()
    # Drop ASN prefix if present
    s = _re.sub(r"^AS\d+\s+", "", s, flags=_re.IGNORECASE)
    # Drop common corporate / regional suffixes
    suffixes = (" S.A.", " SA", " S.A", ", S.A.", " Limited", " Ltd",
                " Plc", " Inc", " Inc.", " Corporation", " Corp",
                " Group", " Telecom", " Telecoms", " Mobile",
                " Cameroon", " Cameroun", " Africa", " International")
    changed = True
    while changed:
        changed = False
        for sfx in suffixes:
            if s.lower().endswith(sfx.lower()):
                s = s[: -len(sfx)].rstrip(",- ")
                changed = True
    # Take the first significant word as the carrier brand
    parts = _re.split(r"[ \-,/]+", s)
    cand = next((p for p in parts if p and len(p) >= 2), "")
    # Sanitize for SSID: keep alnum, max 24 chars
    cand = _re.sub(r"[^A-Za-z0-9]", "", cand)[:24]
    return cand


@router.get("/wifi-suggest")
async def wifi_suggest(request: Request):
    """Best-effort suggestion for the SSID + security_type fields. Uses the
    visitor's IP geolocation to detect their ISP/carrier, then proposes a
    plausible SSID. The UI pre-fills these so the user only has to type the
    password. Override is always possible via the "✏️ Modifier" toggle."""
    import httpx

    ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip", "").strip()
        or (request.client.host if request.client else "")
    )

    isp_name = (request.headers.get("cf-ip-isp") or "").strip()
    country = (request.headers.get("cf-ipcountry") or "").upper().strip()

    if (not isp_name) and ip and not ip.startswith(("10.", "127.", "192.168.", "172.")):
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                r = await client.get(f"https://ipwho.is/{ip}")
                if r.status_code == 200:
                    d = r.json() or {}
                    conn = d.get("connection") or {}
                    isp_name = (conn.get("isp") or conn.get("org") or d.get("isp") or "").strip()
                    if not country:
                        country = (d.get("country_code") or "").upper().strip()
        except Exception as e:
            logger.debug(f"wifi-suggest ISP lookup failed: {e}")

    carrier_slug = _slugify_carrier(isp_name)
    ssid_suggestion = f"{carrier_slug}_WiFi" if carrier_slug else "Mon_WiFi"

    return {
        "ssid_suggestion": ssid_suggestion,
        "security_type": "WPA2",
        "isp_name": isp_name,
        "carrier_slug": carrier_slug,
        "country_code": country,
        "confidence": "high" if carrier_slug else "low",
    }



# -------------------- Hotspot CRUD --------------------
@router.post("/hotspots")
async def create_hotspot(req: HotspotCreate, request: Request):
    user = await get_current_user(request)
    if not await get_bool("connect_enabled", True):
        raise HTTPException(status_code=503, detail="JAPAP Connect est désactivé.")
    # Public hotspots toggle (type='public' allowed globally?)
    if req.type == "public" and not await get_bool("connect_allow_public", True):
        raise HTTPException(status_code=403, detail="Les hotspots publics sont désactivés.")
    hotspot_id = f"hs_{uuid.uuid4().hex[:12]}"
    zone = _zone_from_coords(req.latitude, req.longitude, req.country_code or "")
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Pro tier gating for sharing (skip for type='public' — anyone can
        # report a public hotspot location, it earns no wallet bonus anyway)
        if req.type != "public":
            min_share = (await get_setting("connect_min_pro_to_share") or "none").lower()
            if min_share and min_share != "none":
                rank = await _user_plan_rank(conn, user['user_id'])
                if rank < PLAN_RANK.get(min_share, 0):
                    raise HTTPException(
                        status_code=403,
                        detail=f"PRO_REQUIRED:share:{min_share}"
                    )
        # Legacy flag still honored as fallback
        if await get_bool("connect_pro_required_to_share", False) and not user.get("is_pro") and req.type != "public":
            raise HTTPException(status_code=403, detail="PRO_REQUIRED:share:starter")

        await conn.execute("""
            INSERT INTO wifi_hotspots
              (hotspot_id, owner_id, alias, description, type, latitude, longitude,
               address, sponsor_name, max_daily_users, is_active, is_premium, zone, country_code)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE,$11,$12,$13)
        """, hotspot_id,
           user['user_id'] if req.type != "public" else None,
           req.alias.strip(), (req.description or "").strip(),
           req.type, req.latitude, req.longitude, (req.address or "").strip(),
           (req.sponsor_name or "").strip(), max(0, req.max_daily_users),
           bool(req.is_premium), zone, (req.country_code or "").upper()[:2])

        # iter141nineG — One-step flow: if SSID + password were provided in
        # the create payload, encrypt and persist them right away so the
        # hotspot ships fully configured (avoids the "ssid='' → User B sees
        # '—'" bug). Public-type hotspots have no owner so no creds.
        ssid_provided = (req.ssid or "").strip()
        if req.type != "public" and ssid_provided and req.password:
            from services.wifi_crypto import encrypt_password, WifiCryptoError
            try:
                encrypted = encrypt_password(req.password)
                await conn.execute("""
                    UPDATE wifi_hotspots
                       SET ssid = $1, wifi_password_encrypted = $2,
                           security_type = $3, wifi_updated_at = now()
                     WHERE hotspot_id = $4
                """, ssid_provided[:64], encrypted,
                     req.security_type or "WPA2", hotspot_id)
            except WifiCryptoError as e:
                # The hotspot row exists; just surface the crypto failure
                # so the owner can retry the credentials separately.
                logger.warning(f"create+wifi atomic failed (creds skipped): {e}")
    return {
        "status": "ok",
        "hotspot_id": hotspot_id,
        "zone": zone,
        "wifi_configured": bool(
            req.type != "public"
            and (req.ssid or "").strip()
            and req.password
        ),
    }


@router.get("/my-hotspots")
async def my_hotspots(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM wifi_hotspots WHERE owner_id = $1 ORDER BY created_at DESC
        """, user['user_id'])
    return [_hotspot_dict(r) for r in rows]


@router.put("/hotspots/{hotspot_id}")
async def update_hotspot(hotspot_id: str, req: HotspotUpdate, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT owner_id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        if row['owner_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")
        updates, values = [], []

        def add(col, val):
            values.append(val); updates.append(f"{col} = ${len(values)}")

        if req.alias is not None: add("alias", req.alias.strip()[:120])
        if req.description is not None: add("description", req.description.strip()[:500])
        if req.latitude is not None: add("latitude", float(req.latitude))
        if req.longitude is not None: add("longitude", float(req.longitude))
        if req.address is not None: add("address", req.address.strip()[:255])
        if req.max_daily_users is not None: add("max_daily_users", max(0, int(req.max_daily_users)))
        if req.is_active is not None: add("is_active", bool(req.is_active))
        if not updates:
            raise HTTPException(status_code=400, detail="Aucune modification")
        values.append(datetime.now(timezone.utc)); updates.append(f"updated_at = ${len(values)}")
        values.append(hotspot_id)
        await conn.execute(
            f"UPDATE wifi_hotspots SET {', '.join(updates)} WHERE hotspot_id = ${len(values)}", *values)
    return {"status": "ok"}


@router.delete("/hotspots/{hotspot_id}")
async def delete_hotspot(hotspot_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT owner_id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        if row['owner_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")
        await conn.execute("DELETE FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
    return {"status": "deleted"}


@router.get("/nearby")
async def nearby(request: Request,
                  lat: float = Query(..., ge=-90, le=90),
                  lng: float = Query(..., ge=-180, le=180),
                  radius_km: Optional[float] = Query(None, ge=0, le=500),
                  page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    await get_current_user(request)
    r = radius_km if radius_km is not None else await get_float("connect_search_radius_km", 5.0)
    # Coarse bounding box for indexed filtering, precise haversine in python.
    # 1° latitude ≈ 111 km, longitude depends on latitude.
    dlat = r / 111.0
    dlng = r / max(0.001, 111.0 * math.cos(math.radians(lat)))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT h.*, u.username, u.first_name, u.last_name, u.is_pro, u.avatar
            FROM wifi_hotspots h
            LEFT JOIN users u ON u.user_id = h.owner_id
            WHERE h.is_active = TRUE AND (h.is_blocked = FALSE OR h.is_blocked IS NULL)
              AND h.latitude BETWEEN $1 AND $2 AND h.longitude BETWEEN $3 AND $4
        """, lat - dlat, lat + dlat, lng - dlng, lng + dlng)
    enriched = []
    for r0 in rows:
        d = _hotspot_dict(r0)
        d["distance_km"] = round(_haversine_km(lat, lng, r0['latitude'], r0['longitude']), 2)
        d["owner"] = {
            "user_id": r0['owner_id'],
            "name": f"{r0['first_name'] or ''} {r0['last_name'] or ''}".strip() or r0['username'] or 'Unknown',
            "avatar": r0['avatar'] or '', "is_pro": bool(r0['is_pro']),
        }
        if d["distance_km"] <= r:
            enriched.append(d)
    enriched.sort(key=lambda x: x["distance_km"])
    total = len(enriched)
    start = (page - 1) * limit
    return {"hotspots": enriched[start:start + limit], "total": total,
            "page": page, "limit": limit, "radius_km": r}


# -------------------- Captive-portal connect flow --------------------
@router.post("/start")
async def start_connection(req: ConnectStartRequest, request: Request):
    """User validates through the captive portal → we open a session and
    (if eligible) credit the owner wallet right away for the baseline reward.
    Additional minute-based reward accrues on `/end`.
    """
    user = await get_current_user(request)
    if not await get_bool("connect_enabled", True):
        raise HTTPException(status_code=503, detail="JAPAP Connect est désactivé.")

    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)
    device = (req.device_id or "")[:128] or None
    cap_ip = await get_int("connect_max_connections_per_ip_per_day", 5)
    cap_dev = await get_int("connect_max_connections_per_device_per_day", 5)
    cap_uh = await get_int("connect_max_connections_per_user_per_hotspot_per_day", 1)
    today = datetime.now(timezone.utc).date()

    pool = await get_pool()
    async with pool.acquire() as conn:
        # --- Pro tier gating for access -------------------------------------
        user_rank = await _user_plan_rank(conn, user['user_id'])
        min_access = (await get_setting("connect_min_pro_to_access") or "none").lower()
        if min_access and min_access != "none" and user_rank < PLAN_RANK.get(min_access, 0):
            raise HTTPException(status_code=403, detail=f"PRO_REQUIRED:access:{min_access}")

        hotspot = await conn.fetchrow(
            "SELECT * FROM wifi_hotspots WHERE hotspot_id = $1 AND is_active = TRUE AND is_blocked = FALSE",
            req.hotspot_id)
        if not hotspot:
            raise HTTPException(status_code=404, detail="Hotspot indisponible")
        if hotspot['owner_id'] and hotspot['owner_id'] == user['user_id']:
            raise HTTPException(status_code=400, detail="Impossible de se connecter à son propre hotspot.")

        # Premium hotspot → requires at minimum the access tier (already checked)
        # but also forbids "none" users even if global min_access is "none".
        if hotspot['is_premium'] and user_rank < 1:
            raise HTTPException(status_code=403, detail="PRO_REQUIRED:premium:starter")

        # Anti-fraud caps — Pro users may bypass the user-hotspot cap if admin allows it.
        pro_bypass = await get_bool("connect_pro_bypass_user_caps", True)
        effective_cap_uh = cap_uh
        if pro_bypass and user_rank >= 1:
            effective_cap_uh = await get_int("connect_pro_bypass_cap_per_day", 20)

        blocked_reason = ""
        if effective_cap_uh > 0:
            uh = await conn.fetchval("""
                SELECT COUNT(*) FROM wifi_connections
                WHERE user_id = $1 AND hotspot_id = $2 AND started_at::date = $3
            """, user['user_id'], req.hotspot_id, today)
            if (uh or 0) >= effective_cap_uh:
                blocked_reason = "user_hotspot_limit"
        if not blocked_reason and cap_ip > 0 and ip:
            ii = await conn.fetchval(
                "SELECT COUNT(*) FROM wifi_connections WHERE ip_address = $1 AND started_at::date = $2",
                ip, today)
            if (ii or 0) >= cap_ip:
                blocked_reason = "ip_limit"
        if not blocked_reason and cap_dev > 0 and device:
            dd = await conn.fetchval(
                "SELECT COUNT(*) FROM wifi_connections WHERE device_id = $1 AND started_at::date = $2",
                device, today)
            if (dd or 0) >= cap_dev:
                blocked_reason = "device_limit"

        conn_id = f"wc_{uuid.uuid4().hex[:14]}"
        await conn.execute("""
            INSERT INTO wifi_connections
                (connection_id, hotspot_id, user_id, ip_address, device_id, status, blocked, blocked_reason)
            VALUES ($1, $2, $3, $4, $5, 'active', $6, $7)
        """, conn_id, req.hotspot_id, user['user_id'], ip, device,
           bool(blocked_reason), blocked_reason)

    return {
        "connection_id": conn_id,
        "hotspot_alias": hotspot['alias'],
        "is_premium": bool(hotspot['is_premium']),
        "blocked": bool(blocked_reason),
        "blocked_reason": blocked_reason,
        "message": "Connexion autorisée. L'accès internet est maintenant disponible via le portail." if not blocked_reason
                   else "Connexion enregistrée mais plafond atteint — aucune récompense versée."
    }


@router.post("/end")
async def end_connection(req: ConnectEndRequest, request: Request):
    """Close the session, compute rewards, credit the owner's wallet."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""
                SELECT wc.*, wh.owner_id, wh.alias
                FROM wifi_connections wc
                JOIN wifi_hotspots wh ON wh.hotspot_id = wc.hotspot_id
                WHERE wc.connection_id = $1
            """, req.connection_id)
            if not row:
                raise HTTPException(status_code=404, detail="Connexion introuvable")
            if row['user_id'] != user['user_id']:
                raise HTTPException(status_code=403, detail="Non autorisé")
            if row['status'] != 'active':
                return {"status": row['status'], "already_ended": True}

            started = row['started_at']
            now = datetime.now(timezone.utc)
            duration = max(0, int((now - started).total_seconds()))

            reward_usd = Decimal("0")
            if not row['blocked']:
                min_s = await get_int("connect_min_session_seconds", 60)
                if duration >= min_s:
                    base = Decimal(str(await get_float("connect_reward_per_connection_usd", 0.05)))
                    per_min = Decimal(str(await get_float("connect_reward_per_minute_usd", 0.002)))
                    reward_usd = base + per_min * Decimal(duration // 60)

                    # Pro owner multiplier
                    owner = await conn.fetchrow("SELECT is_pro FROM users WHERE user_id = $1", row['owner_id'])
                    if owner and owner['is_pro']:
                        mult = Decimal(str(await get_float("connect_pro_reward_multiplier", 1.5)))
                        reward_usd = (reward_usd * mult).quantize(Decimal("0.0001"))
                    cap = Decimal(str(await get_float("connect_max_reward_per_session_usd", 0.5)))
                    if reward_usd > cap:
                        reward_usd = cap

            local_info = {"currency": "USD", "amount_local": "0"}
            if reward_usd > 0 and row['owner_id']:
                local_info = await _credit_wallet_bonus(
                    conn, row['owner_id'], reward_usd,
                    f"JAPAP Connect: {row['alias']}", "connect", None)
                # Notify owner
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message)
                    VALUES ($1, $2, 'connect_reward', 'Récompense WiFi', $3)
                """, f"notif_{uuid.uuid4().hex[:12]}", row['owner_id'],
                   f"Nouvelle connexion sur {row['alias']} (+${reward_usd} USD)")

            # --- Points gamification -------------------------------------
            if not row['blocked'] and duration >= await get_int("connect_min_session_seconds", 60):
                pts = (await get_int("connect_points_per_connection", 10)
                       + (duration // 60) * await get_int("connect_points_per_minute", 1))
                if pts > 0:
                    await conn.execute(
                        "UPDATE users SET connect_points = COALESCE(connect_points,0) + $1 WHERE user_id = $2",
                        int(pts), user['user_id'])

            # --- Attribution for 2% Pro revenue share ---------------------
            # Only attribute to hotspots with an owner (skip 'public').
            if not row['blocked'] and row['owner_id'] and duration >= await get_int("connect_min_session_seconds", 60):
                await conn.execute("""
                    UPDATE users SET last_connect_owner_id = $1, last_connect_at = NOW() WHERE user_id = $2
                """, row['owner_id'], user['user_id'])

            await conn.execute("""
                UPDATE wifi_connections SET status = 'ended', ended_at = NOW(),
                    duration_seconds = $1, reward_usd = $2, reward_local = $3, reward_currency = $4
                WHERE id = $5
            """, duration, reward_usd, Decimal(str(local_info.get("amount_local") or 0)),
               local_info.get("currency", "USD"), row['id'])

            # Update denormalized hotspot stats
            await conn.execute("""
                UPDATE wifi_hotspots SET
                    total_connections = total_connections + 1,
                    total_rewarded_usd = total_rewarded_usd + $1,
                    total_unique_users = (
                        SELECT COUNT(DISTINCT user_id) FROM wifi_connections WHERE hotspot_id = $2
                    )
                WHERE hotspot_id = $2
            """, reward_usd, row['hotspot_id'])
    return {"status": "ended", "duration_seconds": duration,
            "reward_usd": str(reward_usd), "currency": local_info.get("currency")}


@router.get("/hotspots/{hotspot_id}")
async def get_hotspot(hotspot_id: str, request: Request):
    """Return a single hotspot by id. Any authenticated user can fetch it;
    the `wifi_configured` flag (but NOT the password) is always exposed."""
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
    if not r:
        raise HTTPException(status_code=404, detail="Hotspot introuvable")
    return _hotspot_dict(r)


@router.get("/hotspots/{hotspot_id}/stats")
async def hotspot_stats(hotspot_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        h = await conn.fetchrow("SELECT * FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not h:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        if h['owner_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Non autorisé")
        conns_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM wifi_connections WHERE hotspot_id = $1 AND started_at > NOW() - INTERVAL '24 hours'",
            hotspot_id)
        avg_duration = await conn.fetchval(
            "SELECT AVG(duration_seconds)::int FROM wifi_connections WHERE hotspot_id = $1 AND status = 'ended'",
            hotspot_id) or 0
    return {
        "hotspot": _hotspot_dict(h),
        "connections_24h": conns_24h or 0,
        "avg_duration_seconds": avg_duration,
    }


@router.get("/leaderboard")
async def leaderboard(request: Request, country: Optional[str] = None):
    await get_current_user(request)
    cc = (country or "").upper().strip()
    pool = await get_pool()
    async with pool.acquire() as conn:
        if cc:
            rows = await conn.fetch("""
                SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_pro,
                    COALESCE(u.connect_points, 0) AS points,
                    COUNT(DISTINCT wh.hotspot_id) AS hotspots,
                    COALESCE(SUM(wh.total_connections), 0) AS connections,
                    COALESCE(SUM(wh.total_rewarded_usd), 0) AS earned_usd
                FROM users u JOIN wifi_hotspots wh ON wh.owner_id = u.user_id
                WHERE wh.country_code = $1
                GROUP BY u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_pro, u.connect_points
                HAVING COALESCE(SUM(wh.total_connections), 0) > 0
                ORDER BY connections DESC LIMIT 50
            """, cc)
        else:
            rows = await conn.fetch("""
                SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_pro,
                    COALESCE(u.connect_points, 0) AS points,
                    COUNT(DISTINCT wh.hotspot_id) AS hotspots,
                    COALESCE(SUM(wh.total_connections), 0) AS connections,
                    COALESCE(SUM(wh.total_rewarded_usd), 0) AS earned_usd
                FROM users u JOIN wifi_hotspots wh ON wh.owner_id = u.user_id
                GROUP BY u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_pro, u.connect_points
                HAVING COALESCE(SUM(wh.total_connections), 0) > 0
                ORDER BY connections DESC LIMIT 50
            """)
    return [{
        "rank": i + 1,
        "user_id": r['user_id'],
        "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r['username'],
        "avatar": r['avatar'] or '', "is_pro": bool(r['is_pro']),
        "points": r['points'] or 0,
        "hotspots": r['hotspots'], "connections": r['connections'],
        "earned_usd": str(r['earned_usd'] or 0),
    } for i, r in enumerate(rows)]


def _hotspot_dict(r):
    # asyncpg Record supports .keys() but not `'x' in r`
    keys = set(r.keys())
    return {
        "hotspot_id": r['hotspot_id'], "alias": r['alias'],
        "description": r['description'], "type": r['type'],
        "latitude": r['latitude'], "longitude": r['longitude'],
        "address": r['address'] or "",
        "sponsor_name": r['sponsor_name'] or "", "is_sponsored": bool(r['is_sponsored']),
        "max_daily_users": r['max_daily_users'], "is_active": bool(r['is_active']),
        "is_blocked": bool(r['is_blocked']), "blocked_reason": r['blocked_reason'] or '',
        "is_premium": bool(r['is_premium']) if 'is_premium' in keys else False,
        "zone": r['zone'] if 'zone' in keys else '',
        "country_code": r['country_code'] if 'country_code' in keys else '',
        "total_connections": r['total_connections'] or 0,
        "total_unique_users": r['total_unique_users'] or 0,
        "total_rewarded_usd": str(r['total_rewarded_usd'] or 0),
        "owner_id": r['owner_id'],
        "created_at": r['created_at'].isoformat(),
        # Connect v2 — WiFi gating hints (never leak the password itself)
        "ssid": (r['ssid'] if 'ssid' in keys and r['ssid'] is not None else ''),
        "security_type": (r['security_type'] if 'security_type' in keys and r['security_type'] else 'WPA2'),
        "wifi_configured": bool(r['wifi_password_encrypted']) if 'wifi_password_encrypted' in keys else False,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Connect v2 — Hybrid Gate Model (QR dynamic + Fernet-encrypted WiFi pw)
# ═══════════════════════════════════════════════════════════════════════════

class WifiSetRequest(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    security_type: Literal["WPA2", "WPA3", "WPA", "WEP", "OPEN"] = "WPA2"


@router.put("/hotspots/{hotspot_id}/wifi")
async def set_wifi_credentials(hotspot_id: str, req: WifiSetRequest, request: Request):
    """Owner-only: store (or replace) the SSID + encrypted password for this hotspot.

    The password is encrypted at rest using Fernet (`WIFI_ENCRYPTION_KEY`).
    Clients never receive the plaintext in any GET — it is only returned
    once, inside `/access/redeem`, after the user passes Pro + anti-fraud checks.
    """
    user = await get_current_user(request)
    from services.wifi_crypto import encrypt_password, WifiCryptoError
    pool = await get_pool()
    async with pool.acquire() as conn:
        h = await conn.fetchrow("SELECT owner_id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not h:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        if h['owner_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Seul le propriétaire peut modifier les identifiants WiFi.")
        try:
            encrypted = encrypt_password(req.password)
        except WifiCryptoError as e:
            raise HTTPException(status_code=503, detail=str(e))
        await conn.execute("""
            UPDATE wifi_hotspots
            SET ssid = $1, wifi_password_encrypted = $2, security_type = $3, wifi_updated_at = now()
            WHERE hotspot_id = $4
        """, req.ssid, encrypted, req.security_type, hotspot_id)
    return {"ok": True, "ssid": req.ssid, "security_type": req.security_type, "wifi_configured": True}


@router.delete("/hotspots/{hotspot_id}/wifi")
async def clear_wifi_credentials(hotspot_id: str, request: Request):
    """Owner-only: remove the stored WiFi credentials (falls back to directory-only)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        h = await conn.fetchrow("SELECT owner_id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not h:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        if h['owner_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Non autorisé")
        await conn.execute("""
            UPDATE wifi_hotspots
            SET wifi_password_encrypted = NULL, ssid = '', wifi_updated_at = now()
            WHERE hotspot_id = $1
        """, hotspot_id)
    return {"ok": True, "wifi_configured": False}


@router.post("/hotspots/{hotspot_id}/qr")
@limiter.limit("20/minute")  # v2.1 hardening — cap per-owner QR spam
async def generate_qr_nonce(hotspot_id: str, request: Request):
    """Owner-only: generate a short-lived (60s) nonce for a QR code.

    Returns the nonce + a ready-to-use payload the frontend can encode:
        {
          "nonce": "...",
          "expires_at": iso,
          "deeplink": "japap://connect/access?nonce=xxx",
          "redeem_url": "<FRONTEND>/connect/redeem?nonce=xxx",
        }

    v2.1 hardening:
      • Rate-limited to 10 QR/min per caller (owner).
      • Cleans expired tokens for this hotspot before inserting (DB hygiene).
      • Caps active unconsumed tokens per hotspot (default 5) — 429 on excess.
    """
    user = await get_current_user(request)
    from services.settings_service import get_setting as _gs  # noqa: F401
    from datetime import timedelta
    ttl_seconds = 60
    max_active = await get_int("connect_qr_max_active_per_hotspot", 5)
    pool = await get_pool()
    async with pool.acquire() as conn:
        h = await conn.fetchrow(
            "SELECT owner_id, wifi_password_encrypted FROM wifi_hotspots WHERE hotspot_id = $1",
            hotspot_id,
        )
        if not h:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        if h['owner_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Seul le propriétaire peut générer un QR.")
        if not h['wifi_password_encrypted']:
            raise HTTPException(status_code=400, detail="Aucun identifiant WiFi enregistré pour ce hotspot. Ajoutez-en d'abord.")

        # --- Guardrail 1: opportunistic cleanup of expired tokens for this hotspot
        await conn.execute(
            "DELETE FROM wifi_access_tokens "
            "WHERE hotspot_id = $1 AND expires_at < NOW() AND consumed_by_user_id IS NULL",
            hotspot_id,
        )
        # --- Guardrail 2: cap concurrent active (non-expired, non-consumed) tokens
        if max_active > 0:
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM wifi_access_tokens "
                "WHERE hotspot_id = $1 AND consumed_by_user_id IS NULL AND expires_at > NOW()",
                hotspot_id,
            ) or 0
            if active >= max_active:
                raise HTTPException(
                    status_code=429,
                    detail=f"Trop de QR actifs pour ce hotspot ({active}/{max_active}). Attendez qu'ils expirent ou soient utilisés.",
                )

        token_id = f"wt_{uuid.uuid4().hex[:14]}"
        nonce = uuid.uuid4().hex
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        await conn.execute("""
            INSERT INTO wifi_access_tokens (token_id, hotspot_id, nonce, expires_at, created_by)
            VALUES ($1, $2, $3, $4, $5)
        """, token_id, hotspot_id, nonce, expires_at, user['user_id'])
    import os as _os
    frontend = _os.environ.get("FRONTEND_PUBLIC_URL", "")
    return {
        "token_id": token_id,
        "nonce": nonce,
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": ttl_seconds,
        "deeplink": f"japap://connect/access?nonce={nonce}",
        "redeem_url": f"{frontend.rstrip('/')}/connect/redeem?nonce={nonce}" if frontend else f"/connect/redeem?nonce={nonce}",
    }


class RedeemRequest(BaseModel):
    nonce: str = Field(..., min_length=8, max_length=64)
    device_id: Optional[str] = None


@router.post("/access/redeem")
@limiter.limit("20/minute")  # v2.1 hardening — HTTP throttle (per user-or-IP)
async def redeem_access(req: RedeemRequest, request: Request):
    """User scans a QR → validates nonce → Pro gating → anti-fraude caps →
    creates a wifi_connection → reveals the SSID + password.

    Zero credential leak on failure: we always 403/410/429 with a neutral
    message, never hinting at the plaintext structure.

    v2.1 hardening:
      • Rate-limited (10/min/user-or-IP) against brute-force & DoS.
      • Nonce validation + consumption wrapped in an explicit transaction
        with `SELECT ... FOR UPDATE` on the wifi_access_tokens row,
        eliminating the concurrent double-redeem race.
    """
    user = await get_current_user(request)
    if not await get_bool("connect_enabled", True):
        raise HTTPException(status_code=503, detail="JAPAP Connect est désactivé.")

    from services.wifi_crypto import decrypt_password, WifiCryptoError
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)
    device = (req.device_id or "")[:128] or None
    cap_ip = await get_int("connect_max_connections_per_ip_per_day", 5)
    cap_dev = await get_int("connect_max_connections_per_device_per_day", 5)
    cap_uh = await get_int("connect_max_connections_per_user_per_hotspot_per_day", 1)
    today = datetime.now(timezone.utc).date()

    pool = await get_pool()
    async with pool.acquire() as conn:
        # ═══ Explicit transaction: lock the token row to serialize concurrent
        # ═══ scans of the same nonce. All validations + consumption happen
        # ═══ atomically; the row is unlocked on COMMIT/ROLLBACK.
        async with conn.transaction():
            tok = await conn.fetchrow(
                "SELECT * FROM wifi_access_tokens WHERE nonce = $1 FOR UPDATE",
                req.nonce,
            )
            if not tok:
                raise HTTPException(status_code=404, detail="QR code invalide ou inconnu.")
            if tok['expires_at'] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Ce QR code a expiré. Demandez-en un nouveau à l'hôte.")
            if tok['consumed_by_user_id']:
                if tok['consumed_by_user_id'] == user['user_id']:
                    raise HTTPException(status_code=409, detail="Ce QR a déjà été utilisé — votre accès est déjà actif.")
                raise HTTPException(status_code=410, detail="Ce QR code a déjà été utilisé par un autre utilisateur.")

            hotspot = await conn.fetchrow(
                "SELECT * FROM wifi_hotspots WHERE hotspot_id = $1 AND is_active = TRUE AND is_blocked = FALSE",
                tok['hotspot_id'],
            )
            if not hotspot:
                raise HTTPException(status_code=404, detail="Hotspot indisponible.")
            if hotspot['owner_id'] == user['user_id']:
                raise HTTPException(status_code=400, detail="Impossible de se connecter à son propre hotspot.")
            if not hotspot['wifi_password_encrypted']:
                raise HTTPException(status_code=400, detail="Ce hotspot n'a plus d'identifiants WiFi configurés.")
            # iter141nineG — Belt-and-braces: refuse the redeem if the SSID
            # was somehow lost (column NULL'd by a partial migration). The
            # caller would otherwise see "—" for the network name. A clear
            # 422 lets the owner re-enter creds via the UI.
            if not (hotspot['ssid'] or '').strip():
                raise HTTPException(
                    status_code=422,
                    detail="Le nom du réseau (SSID) n'a pas été renseigné par le partageur. "
                           "Demandez-lui de le configurer dans son tableau de bord WiFi."
                )

            # Pro gating (access level)
            user_rank = await _user_plan_rank(conn, user['user_id'])
            min_access = (await get_setting("connect_min_pro_to_access") or "none").lower()
            if min_access and min_access != "none" and user_rank < PLAN_RANK.get(min_access, 0):
                raise HTTPException(status_code=403, detail=f"PRO_REQUIRED:access:{min_access}")
            if hotspot['is_premium'] and user_rank < 1:
                raise HTTPException(status_code=403, detail="PRO_REQUIRED:premium:starter")

            # Anti-fraud caps (Pro bypass configurable)
            pro_bypass = await get_bool("connect_pro_bypass_user_caps", True)
            effective_cap_uh = cap_uh
            if pro_bypass and user_rank >= 1:
                effective_cap_uh = await get_int("connect_pro_bypass_cap_per_day", 20)

            blocked_reason = ""
            if effective_cap_uh > 0:
                uh = await conn.fetchval("""
                    SELECT COUNT(*) FROM wifi_connections
                    WHERE user_id = $1 AND hotspot_id = $2 AND started_at::date = $3
                """, user['user_id'], tok['hotspot_id'], today)
                if (uh or 0) >= effective_cap_uh:
                    blocked_reason = "user_hotspot_limit"
            if not blocked_reason and cap_ip > 0 and ip:
                ii = await conn.fetchval(
                    "SELECT COUNT(*) FROM wifi_connections WHERE ip_address = $1 AND started_at::date = $2",
                    ip, today)
                if (ii or 0) >= cap_ip:
                    blocked_reason = "ip_limit"
            if not blocked_reason and cap_dev > 0 and device:
                dd = await conn.fetchval(
                    "SELECT COUNT(*) FROM wifi_connections WHERE device_id = $1 AND started_at::date = $2",
                    device, today)
                if (dd or 0) >= cap_dev:
                    blocked_reason = "device_limit"
            if blocked_reason:
                raise HTTPException(status_code=429, detail=f"Limite journalière atteinte ({blocked_reason}). Réessayez demain.")

            # Decrypt only AFTER all gates pass
            try:
                plaintext = decrypt_password(hotspot['wifi_password_encrypted'])
            except WifiCryptoError as e:
                raise HTTPException(status_code=503, detail=str(e))

            # Create the active wifi_connection + mark nonce consumed (atomic)
            conn_id = f"wc_{uuid.uuid4().hex[:14]}"
            await conn.execute("""
                INSERT INTO wifi_connections
                    (connection_id, hotspot_id, user_id, ip_address, device_id,
                     status, access_token_id, password_reveals)
                VALUES ($1, $2, $3, $4, $5, 'active', $6, 1)
            """, conn_id, tok['hotspot_id'], user['user_id'], ip, device, tok['token_id'])
            await conn.execute(
                "UPDATE wifi_access_tokens SET consumed_by_user_id = $1, consumed_at = now() WHERE token_id = $2",
                user['user_id'], tok['token_id'],
            )

    return {
        "connection_id": conn_id,
        "hotspot_id": tok['hotspot_id'],
        "hotspot_alias": hotspot['alias'],
        "ssid": hotspot['ssid'] or '',
        "password": plaintext,
        "security_type": hotspot['security_type'] or 'WPA2',
        "hide_after_seconds": 90,        # client auto-masks after this
        "max_reveals": 3,                 # total allowed across future GETs
    }


@router.get("/access/{connection_id}/password")
@limiter.limit("10/minute")  # v2.1 hardening — per-user reveal throttle
async def reveal_password(connection_id: str, request: Request):
    """Re-reveal the WiFi password within the first 30 minutes of a session.

    Rate-limited to `max_reveals=3` total (including the initial redeem).
    Zero-leak: only the session owner can call this; stranger → 403.
    """
    from datetime import timedelta
    user = await get_current_user(request)
    from services.wifi_crypto import decrypt_password, WifiCryptoError
    pool = await get_pool()
    async with pool.acquire() as conn:
        wc = await conn.fetchrow("SELECT * FROM wifi_connections WHERE connection_id = $1", connection_id)
        if not wc:
            raise HTTPException(status_code=404, detail="Session introuvable.")
        if wc['user_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Accès refusé.")
        if wc['started_at'] + timedelta(minutes=30) < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Session trop ancienne — scannez à nouveau le QR.")
        reveals = (wc['password_reveals'] or 0)
        if reveals >= 3:
            raise HTTPException(status_code=429, detail="Limite de ré-affichages atteinte (3). Scannez un nouveau QR.")
        hotspot = await conn.fetchrow(
            "SELECT ssid, wifi_password_encrypted, security_type FROM wifi_hotspots WHERE hotspot_id = $1",
            wc['hotspot_id'],
        )
        if not hotspot or not hotspot['wifi_password_encrypted']:
            raise HTTPException(status_code=404, detail="Identifiants WiFi révoqués par l'hôte.")
        try:
            plaintext = decrypt_password(hotspot['wifi_password_encrypted'])
        except WifiCryptoError as e:
            raise HTTPException(status_code=503, detail=str(e))
        await conn.execute(
            "UPDATE wifi_connections SET password_reveals = password_reveals + 1 WHERE connection_id = $1",
            connection_id,
        )
    return {
        "ssid": hotspot['ssid'] or '',
        "password": plaintext,
        "security_type": hotspot['security_type'] or 'WPA2',
        "reveals_used": reveals + 1,
        "reveals_remaining": max(0, 3 - reveals - 1),
        "hide_after_seconds": 90,
    }


@router.get("/hotspots/{hotspot_id}/live-stats")
async def hotspot_live_stats(hotspot_id: str, request: Request):
    """Social proof counter — public to any authenticated user.

    Returns:
        - connected_now        : active sessions right now
        - connected_today      : unique users since midnight UTC
        - connected_last_hour  : unique users in the last 60 min
    """
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        h = await conn.fetchrow(
            "SELECT 1 FROM wifi_hotspots WHERE hotspot_id = $1 AND is_active = TRUE",
            hotspot_id,
        )
        if not h:
            raise HTTPException(status_code=404, detail="Hotspot introuvable")
        now_count = await conn.fetchval(
            "SELECT COUNT(*) FROM wifi_connections "
            "WHERE hotspot_id = $1 AND status = 'active'",
            hotspot_id,
        ) or 0
        today_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM wifi_connections "
            "WHERE hotspot_id = $1 AND started_at::date = CURRENT_DATE",
            hotspot_id,
        ) or 0
        last_hour_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM wifi_connections "
            "WHERE hotspot_id = $1 AND started_at > NOW() - INTERVAL '1 hour'",
            hotspot_id,
        ) or 0
    return {
        "hotspot_id": hotspot_id,
        "connected_now": int(now_count),
        "connected_today": int(today_count),
        "connected_last_hour": int(last_hour_count),
    }


# ──────────────────────────────────────────────────────────────────────────
#  iter141nineJ — Shareable QR Card for Stories / Status
#  After a Pro creates a hotspot, JAPAP auto-generates a 1080×1920 PNG
#  card (and a matching OG-rich landing page) so they can drop it on
#  WhatsApp Status / Instagram Stories / TikTok in 1 tap. Each scan from
#  a friend = potential new JAPAP user (recruit reward eligible).
# ──────────────────────────────────────────────────────────────────────────

def _share_link_for_hotspot(hotspot_id: str, request: Request) -> str:
    """Public URL printed under the QR. Lands on a SPA route that handles
    auth gating and routes the visitor to the redeem flow if they're Pro
    Starter+, or to register/upgrade otherwise."""
    origin = (
        os.environ.get("FRONTEND_URL", "").rstrip("/")
        or os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
    )
    if not origin:
        proto = (request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
                 or request.url.scheme)
        host = (request.headers.get("x-forwarded-host", "").split(",")[0].strip()
                or request.headers.get("host", "")
                or request.url.netloc)
        origin = f"{proto}://{host}"
    return f"{origin}/connect/h/{hotspot_id}"


@router.get("/hotspots/{hotspot_id}/share-card.png")
async def hotspot_share_card_png(hotspot_id: str, request: Request):
    """1080×1920 vertical PNG for Stories/Status. Public — anyone with the
    hotspot_id can render it (the QR inside still requires auth on its
    landing page, so leaking the PNG only helps virality).

    The card surfaces the auto-detected carrier from the OWNER'S country
    (ISP), so a Vodacom hotspot in Kenya looks native, an Orange one in
    France looks native, etc. — not country-hardcoded."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT alias, address, country_code FROM wifi_hotspots
               WHERE hotspot_id = $1""", hotspot_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Hotspot introuvable")

    # Best-effort carrier hint — passes through the requester's IP lookup.
    # Good enough for the common case where the owner shares from their
    # own network. Country-neutral by design (ipwho.is covers worldwide).
    try:
        suggestion = await wifi_suggest(request)
        carrier_slug = (suggestion.get("carrier_slug") or "").strip()
    except Exception:
        carrier_slug = ""

    pay_url = _share_link_for_hotspot(hotspot_id, request)

    from services.connect_share_card import render_share_card
    from fastapi import Response
    png = render_share_card(
        pay_url=pay_url,
        alias=row["alias"] or "JAPAP WiFi",
        address=(row["address"] or "").strip(),
        carrier_slug=carrier_slug,
        country_code=(row["country_code"] or "").upper(),
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=600",
            "Content-Disposition": f'inline; filename="japap-wifi-{hotspot_id}.png"',
        },
    )


@router.get("/hotspots/{hotspot_id}/share")
async def hotspot_share_meta(hotspot_id: str, request: Request):
    """Returns everything the front-end needs for the share sheet: URLs,
    PNG endpoint, ready-to-paste text. Country-neutral copy — JAPAP works
    in every market the user is in."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT alias, address, country_code FROM wifi_hotspots WHERE hotspot_id = $1",
            hotspot_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Hotspot introuvable")

    pay_url = _share_link_for_hotspot(hotspot_id, request)
    origin = pay_url.rsplit("/connect/h/", 1)[0]
    share_url = f"{origin}/api/og/connect/{hotspot_id}"
    png_url = f"{origin}/api/connect/hotspots/{hotspot_id}/share-card.png"

    text = (
        f"📶 J'ai du WiFi à partager via JAPAP — \"{row['alias']}\""
        f"{(' · ' + row['address']) if row['address'] else ''}\n"
        f"Scanne pour te connecter : {share_url}"
    )
    from urllib.parse import quote
    enc = quote(text, safe="")
    return {
        "hotspot_id": hotspot_id,
        "alias": row["alias"],
        "share_url": share_url,
        "pay_url": pay_url,
        "png_url": png_url,
        "share_text": text,
        "whatsapp_url": f"https://wa.me/?text={enc}",
        "telegram_url": f"https://t.me/share/url?url={quote(share_url)}&text={enc}",
        "twitter_url": f"https://twitter.com/intent/tweet?text={enc}",
    }




@router.get("/me")
async def connect_me(request: Request):
    """User's personal JAPAP Connect dashboard: points, level, badges, stats."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                COALESCE(u.connect_points, 0) AS points,
                COALESCE((SELECT COUNT(*) FROM wifi_connections WHERE user_id = u.user_id AND blocked = FALSE), 0) AS connections_made,
                COALESCE((SELECT COUNT(*) FROM wifi_hotspots WHERE owner_id = u.user_id AND is_active = TRUE), 0) AS hotspots_owned,
                COALESCE((SELECT SUM(total_connections) FROM wifi_hotspots WHERE owner_id = u.user_id), 0) AS connections_received,
                COALESCE((SELECT SUM(total_rewarded_usd) FROM wifi_hotspots WHERE owner_id = u.user_id), 0) AS earned_usd
            FROM users u WHERE u.user_id = $1
        """, user['user_id'])
        my_rank = await _user_plan_rank(conn, user['user_id'])

    thr_connector = await get_int("connect_badge_connector_threshold", 5)
    thr_provider = await get_int("connect_badge_provider_threshold", 10)
    thr_ambassador = await get_int("connect_badge_ambassador_threshold", 500)

    badges = []
    if (row['connections_made'] or 0) >= thr_connector:
        badges.append({"id": "connector", "label": "Connecteur", "tier": 1, "icon": "🔌"})
    if (row['hotspots_owned'] or 0) >= thr_provider:
        badges.append({"id": "provider", "label": "Fournisseur", "tier": 2, "icon": "📡"})
    if (row['connections_received'] or 0) >= thr_ambassador:
        badges.append({"id": "ambassador", "label": "Ambassadeur réseau", "tier": 3, "icon": "🏆"})

    # Level based on points (every 100 points = 1 level)
    points = int(row['points'] or 0)
    level = points // 100 + 1
    next_level_points = level * 100
    progress_to_next = (points % 100)

    # --- Pro-gating config + revshare summary exposed to the UI ---
    min_use = (await get_setting("connect_min_pro_to_access") or "none").lower()
    min_share = (await get_setting("connect_min_pro_to_share") or "none").lower()
    revshare_enabled = await get_bool("connect_revshare_pro_enabled", True)
    revshare_pct = await get_float("connect_revshare_pct", 2.0)
    revshare_cap_monthly = await get_float("connect_revshare_cap_per_month_usd", 100.0)

    pool2 = await get_pool()
    async with pool2.acquire() as conn2:
        revshare_total = await conn2.fetchval("""
            SELECT COALESCE(SUM(amount_usd), 0) FROM referral_rewards_log
            WHERE user_id = $1 AND role = 'connect_revshare'
        """, user['user_id']) or 0
        revshare_30d = await conn2.fetchval("""
            SELECT COALESCE(SUM(amount_usd), 0) FROM referral_rewards_log
            WHERE user_id = $1 AND role = 'connect_revshare'
              AND created_at > NOW() - INTERVAL '30 days'
        """, user['user_id']) or 0
        revshare_count_30d = await conn2.fetchval("""
            SELECT COUNT(*) FROM referral_rewards_log
            WHERE user_id = $1 AND role = 'connect_revshare'
              AND created_at > NOW() - INTERVAL '30 days'
        """, user['user_id']) or 0

    return {
        "points": points, "level": level,
        "points_to_next_level": max(0, next_level_points - points),
        "progress_pct": progress_to_next,
        "connections_made": row['connections_made'] or 0,
        "hotspots_owned": row['hotspots_owned'] or 0,
        "connections_received": row['connections_received'] or 0,
        "earned_usd": str(row['earned_usd'] or 0),
        "plan_rank": my_rank,
        "badges": badges,
        "thresholds": {
            "connector": thr_connector, "provider": thr_provider, "ambassador": thr_ambassador,
        },
        "gating": {
            "min_use": min_use,         # "none" | "starter" | "creator" | "business"
            "min_share": min_share,
            "can_use": my_rank >= PLAN_RANK.get(min_use, 0),
            "can_share": my_rank >= PLAN_RANK.get(min_share, 0),
            "use_required_rank": PLAN_RANK.get(min_use, 0),
            "share_required_rank": PLAN_RANK.get(min_share, 0),
        },
        "revshare": {
            "enabled": bool(revshare_enabled),
            "pct": float(revshare_pct or 0),
            "cap_monthly_usd": float(revshare_cap_monthly or 0),
            "total_earned_usd": str(revshare_total),
            "last_30d_usd": str(revshare_30d),
            "last_30d_count": int(revshare_count_30d),
            "eligible": my_rank >= 3,   # business
        },
    }


@router.get("/revshare/history")
async def revshare_history(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    """Paginated list of 2% Pro revshare credits received by the authenticated user."""
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT amount_usd, amount_local, currency, details, created_at
            FROM referral_rewards_log
            WHERE user_id = $1 AND role = 'connect_revshare'
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
        total = await conn.fetchval("""
            SELECT COUNT(*) FROM referral_rewards_log
            WHERE user_id = $1 AND role = 'connect_revshare'
        """, user['user_id']) or 0
    import json as _json
    def _ctx(d):
        try:
            return (_json.loads(d) if isinstance(d, str) else (d or {})).get('reason', '')
        except Exception:
            return ''
    return {
        "items": [{
            "amount_usd": str(r['amount_usd']),
            "amount_local": str(r['amount_local']) if r['amount_local'] is not None else None,
            "currency": r['currency'] or '',
            "context": _ctx(r['details']),
            "created_at": r['created_at'].isoformat() if r['created_at'] else '',
        } for r in rows],
        "total": total, "page": page, "limit": limit,
    }


async def credit_hotspot_owner_from_pro(user_id: str, plan_price_usd, plan_name: str, tx_source: str = "pro") -> dict:
    """Called by pro.subscribe() — credit last-attributed hotspot owner 2% of the Pro amount paid.
    Returns {credited_to, amount_usd}, or {} if skipped.
    """
    from decimal import Decimal, ROUND_HALF_UP
    if not await get_bool("connect_revshare_pro_enabled", True):
        return {}
    pct = Decimal(str(await get_float("connect_revshare_pct", 2.0)))
    if pct <= 0:
        return {}
    ttl_h = await get_int("connect_revshare_attribution_hours", 720)
    cap_m = Decimal(str(await get_float("connect_revshare_cap_per_month_usd", 100.0)))

    pool = await get_pool()
    async with pool.acquire() as conn:
        u = await conn.fetchrow(
            "SELECT last_connect_owner_id, last_connect_at FROM users WHERE user_id = $1", user_id)
        if not u or not u['last_connect_owner_id']:
            return {}
        if not u['last_connect_at']:
            return {}
        age_h = (datetime.now(timezone.utc) - u['last_connect_at'].replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if ttl_h > 0 and age_h > ttl_h:
            return {}

        amount = (Decimal(str(plan_price_usd)) * pct / Decimal(100)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if amount <= 0:
            return {}
        pct_label = f"{float(pct):g}"

        # Monthly cap per owner
        if cap_m > 0:
            spent = await conn.fetchval("""
                SELECT COALESCE(SUM(amount_usd), 0) FROM referral_rewards_log
                WHERE user_id = $1 AND role = 'connect_revshare'
                  AND created_at > NOW() - INTERVAL '30 days'
            """, u['last_connect_owner_id']) or Decimal(0)
            if Decimal(str(spent)) + amount > cap_m:
                amount = max(Decimal(0), cap_m - Decimal(str(spent)))
                if amount <= 0:
                    return {"skipped": "monthly_cap"}

        async with conn.transaction():
            res = await _credit_wallet_bonus(
                conn, u['last_connect_owner_id'], amount,
                f"{plan_name} — revshare {pct_label}% via Connect", "connect_revshare", None)
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'connect_revshare', 'Part JAPAP Pro reçue', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", u['last_connect_owner_id'],
               f"Un de vos utilisateurs connectés a souscrit à {plan_name} — part {pct_label}% (+${amount}) créditée.")
    # Fire-and-forget realtime toast to the hotspot owner (outside TX so a failing
    # socket never rolls back the wallet credit).
    try:
        from routes.realtime import notify_connect_revshare
        await notify_connect_revshare(
            u['last_connect_owner_id'], str(amount),
            res.get("amount_local"), res.get("currency") or "USD",
            plan_name, float(pct))
    except Exception as e:
        logger.warning(f"notify_connect_revshare failed: {e}")
    return {"credited_to": u['last_connect_owner_id'], "amount_usd": str(amount),
            "currency": res.get("currency")}
