"""
JAPAP Messenger — Transport Module (Taxi on-demand)
Flow: rider request → drivers see it → one accepts → en_route → started → finished
                                                                 ↘ cancelled (only before started)
Pricing: 500 XAF base + 200 XAF/km (standard) or 350 XAF/km (premium)
Commission plateforme: 15% sur course

iter97 (Phase 1) — strict driver KYC
iter98 (Phase 2) — full ride lifecycle + GPS validation + H3 indexing for
                   demand heatmap. New states `en_route` and `started` between
                   `accepted` and `completed`. Geocode every pickup/dropoff into
                   an H3 r9 cell so the admin can later visualise demand zones.
"""
import os
import jwt as _jwt
import uuid
import logging
import math
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field, validator
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from services.settings_service import get_bool
from services.driver_kyc import (
    ensure_driver_kyc_ddl, driver_to_public,
    DRIVER_KYC_PENDING, DRIVER_KYC_APPROVED, DRIVER_KYC_REJECTED, DRIVER_KYC_SUSPENDED,
)
from services.transport_geo import (
    ensure_ride_geo_ddl, is_valid_coord, h3_cell, haversine_km as _haversine_km_fn,
    h3 as _h3,
)
from services.transport_pricing import (
    ensure_pricing_ddl, get_active_grid, ai_propose_pricing, pricing_to_dict,
    PRICING_PROPOSED, PRICING_ACTIVE, PRICING_ARCHIVED, PRICING_REJECTED,
    PRICING_STATUSES,
)
from services.transport_surge import (
    ensure_surge_ddl, get_surge_config, upsert_surge_config,
    compute_surge, log_surge_application, DEFAULT_SURGE_CONFIG,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/transport", tags=["transport"])

BASE_FARE = Decimal("500")
RATE_STANDARD = Decimal("200")
RATE_PREMIUM = Decimal("350")
COMMISSION = Decimal("0.15")
VEHICLE_TYPES = ["standard", "premium"]


class RequestRideRequest(BaseModel):
    pickup_address: str
    dropoff_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    vehicle_type: str = "standard"
    notes: str = ""


class RegisterDriverRequest(BaseModel):
    # iter97 — strict KYC schema. ALL fields required, validated by Pydantic.
    vehicle_model: str = Field(..., min_length=2, max_length=100)
    vehicle_plate: str = Field(..., min_length=3, max_length=30)
    vehicle_type: str = "standard"
    # Personal info
    personal_phone: str = Field(..., min_length=6, max_length=32)
    emergency_contact_name: str = Field(..., min_length=2, max_length=80)
    emergency_contact_phone: str = Field(..., min_length=6, max_length=32)
    # Driving license
    license_number: str = Field(..., min_length=3, max_length=50)
    license_issue_date: date
    # Document URLs (uploaded via /api/upload/image?kind=driver_doc beforehand)
    license_image_url: str = Field(..., min_length=5, max_length=500)
    id_card_image_url: str = Field(..., min_length=5, max_length=500)
    selfie_with_license_url: str = Field(..., min_length=5, max_length=500)
    # Country (optional — auto-detected on backend if missing)
    country_code: Optional[str] = None


def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = la2 - la1
    dlng = lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def estimate_fare(distance_km: float, vehicle_type: str,
                  grid: Optional[dict] = None) -> Decimal:
    """Compute the fare for the given distance + vehicle_type. If `grid` is
    provided (active pricing_grid row), use its base_fare/per_km; otherwise
    fall back to the historic XAF defaults.
    """
    if grid:
        base = Decimal(str(grid["base_fare"]))
        rate = Decimal(str(grid["per_km"]))
    else:
        base = BASE_FARE
        rate = RATE_PREMIUM if vehicle_type == "premium" else RATE_STANDARD
    fare = base + (Decimal(str(round(distance_km, 2))) * rate)
    return fare.quantize(Decimal("1"))


async def _resolve_pricing(conn, user_id: str, vehicle_type: str) -> tuple[Optional[dict], str]:
    """Look up the active grid for the user's country (if any). Returns
    (grid_dict_or_None, currency_label) — the currency falls back to 'XAF'
    when no grid is active."""
    country = await conn.fetchval(
        "SELECT country_code FROM users WHERE user_id = $1", user_id
    )
    if not country:
        return None, "XAF"
    grid = await get_active_grid(conn, country, vehicle_type)
    return grid, (grid["currency"] if grid else "XAF")


@router.get("/estimate")
async def estimate(request: Request,
                   pickup_lat: float, pickup_lng: float,
                   dropoff_lat: float, dropoff_lng: float,
                   vehicle_type: str = "standard"):
    user = await get_current_user(request)
    if vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=400, detail="Type de véhicule invalide")
    distance = haversine_km(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    pickup_h3 = h3_cell(pickup_lat, pickup_lng) if is_valid_coord(pickup_lat, pickup_lng) else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        await ensure_surge_ddl(conn)
        grid, currency = await _resolve_pricing(conn, user["user_id"], vehicle_type)
        country = await conn.fetchval(
            "SELECT country_code FROM users WHERE user_id = $1", user["user_id"]
        )
        surge = await compute_surge(
            conn, country_code=country or "",
            h3_cell=pickup_h3, vehicle_type=vehicle_type,
        )
    base_fare = estimate_fare(distance, vehicle_type, grid)
    # Low = current surge; High = current × 1.2 to account for possible
    # demand fluctuation between estimate and booking (user hesitation window).
    fare_low = (base_fare * Decimal(str(surge["multiplier"]))).quantize(Decimal("1"))
    fare_high_mult = min(surge["multiplier"] * 1.2, float(surge["config_used"].get("max_surge", 2.0)))
    fare_high = (base_fare * Decimal(str(fare_high_mult))).quantize(Decimal("1"))
    base_used = grid["base_fare"] if grid else str(BASE_FARE)
    rate_used = grid["per_km"] if grid else str(
        RATE_PREMIUM if vehicle_type == "premium" else RATE_STANDARD
    )
    return {
        "distance_km": round(distance, 2),
        "fare_estimated": str(fare_low),          # Backward compat — applied multiplier
        "fare_low": str(fare_low),
        "fare_high": str(fare_high),
        "vehicle_type": vehicle_type,
        "currency": currency,
        "pricing_source": "grid" if grid else "default_xaf",
        "surge_label": surge["label"],
        "surge_applied": surge["multiplier"] > 1.0,
        "breakdown": {
            "base": str(base_used),
            "per_km": str(rate_used),
        },
    }


@router.post("/request")
async def request_ride(req: RequestRideRequest, request: Request):
    user = await get_current_user(request)
    if req.vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=400, detail="Type de véhicule invalide")
    # iter98 — strict GPS validation. Reject NaN / Inf / out-of-range / too-close.
    for lat, lng, label in [
        (req.pickup_lat, req.pickup_lng, "ramassage"),
        (req.dropoff_lat, req.dropoff_lng, "destination"),
    ]:
        if not is_valid_coord(lat, lng):
            raise HTTPException(
                status_code=400,
                detail=f"Coordonnées GPS invalides pour le point de {label}.",
            )
    distance = haversine_km(req.pickup_lat, req.pickup_lng, req.dropoff_lat, req.dropoff_lng)
    if distance < 0.1:
        raise HTTPException(
            status_code=400,
            detail="Trajet trop court (minimum 100 m entre départ et destination).",
        )
    if distance > 500:
        raise HTTPException(
            status_code=400,
            detail="Trajet trop long pour le service taxi (>500 km). Utilisez un autre transport.",
        )

    # H3 r9 cells (~174m edge) for the future demand heatmap.
    pickup_h3 = h3_cell(req.pickup_lat, req.pickup_lng)
    dropoff_h3 = h3_cell(req.dropoff_lat, req.dropoff_lng)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ride_geo_ddl(conn)
        await ensure_pricing_ddl(conn)
        await ensure_surge_ddl(conn)
        # Apply the active pricing grid for the rider's country if one has
        # been validated by an admin; otherwise the historic XAF defaults.
        grid, currency = await _resolve_pricing(conn, user['user_id'], req.vehicle_type)
        base_fare = estimate_fare(distance, req.vehicle_type, grid)
        # Phase D (iter105) — compute + snapshot the surge multiplier at
        # request time so the rider is charged exactly what they confirmed.
        country = await conn.fetchval(
            "SELECT country_code FROM users WHERE user_id = $1", user['user_id']
        )
        surge = await compute_surge(
            conn, country_code=country or "",
            h3_cell=pickup_h3, vehicle_type=req.vehicle_type,
        )
        final_fare = (base_fare * Decimal(str(surge["multiplier"]))).quantize(Decimal("1"))
        # Check wallet can cover final (surge-applied) fare
        wallet = await conn.fetchrow("SELECT balance FROM wallets WHERE user_id = $1", user['user_id'])
        if not wallet or wallet['balance'] < final_fare:
            raise HTTPException(status_code=400, detail=f"Solde insuffisant (nécessaire: {final_fare} {currency})")

        # Reject duplicate active rides per rider — UX guard against double-tap
        # of the booking button. A user cannot have 2 rides in {pending, accepted,
        # en_route, started} at the same time.
        active = await conn.fetchval(
            """SELECT 1 FROM ride_requests
                 WHERE rider_id = $1
                   AND status IN ('pending','accepted','en_route','started')
                 LIMIT 1""",
            user['user_id'],
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="Vous avez déjà une course en cours. Terminez-la avant d'en créer une nouvelle.",
            )

        ride_id = f"ride_{uuid.uuid4().hex[:12]}"
        try:
            await conn.execute("""
                INSERT INTO ride_requests (ride_id, rider_id, pickup_address, dropoff_address,
                    pickup_lat, pickup_lng, dropoff_lat, dropoff_lng, distance_km,
                    fare_estimated, vehicle_type, notes, pickup_h3, dropoff_h3,
                    surge_multiplier, surge_label)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            """, ride_id, user['user_id'], req.pickup_address[:500], req.dropoff_address[:500],
                 req.pickup_lat, req.pickup_lng, req.dropoff_lat, req.dropoff_lng,
                 Decimal(str(round(distance, 2))), final_fare, req.vehicle_type, req.notes[:500],
                 pickup_h3, dropoff_h3,
                 Decimal(str(surge["multiplier"])), surge["label"])
        except Exception as e:
            # iter99 — partial unique index on (rider_id) WHERE status IN active
            # set will fire here on a race condition. Surface it as 409.
            if "uq_ride_requests_rider_active" in str(e):
                raise HTTPException(
                    status_code=409,
                    detail="Vous avez déjà une course en cours. Terminez-la avant d'en créer une nouvelle.",
                )
            raise
        # Persist surge breakdown for audit (best-effort, never raises).
        await log_surge_application(
            conn, ride_id=ride_id, country_code=country or "",
            h3_cell=pickup_h3 or "", vehicle_type=req.vehicle_type,
            surge=surge, base_fare=base_fare, final_fare=final_fare,
        )
        return {
            "ride_id": ride_id,
            "distance_km": round(distance, 2),
            "fare_estimated": str(final_fare),
            "base_fare": str(base_fare),
            "surge_multiplier": str(surge["multiplier"]),
            "surge_label": surge["label"],
            "status": "pending",
            "pickup_h3": pickup_h3,
            "dropoff_h3": dropoff_h3,
        }


@router.get("/available")
async def list_available_rides(request: Request):
    """Drivers see pending rides waiting for acceptance."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Must be registered AND KYC-approved driver
        drv = await conn.fetchrow("SELECT * FROM drivers WHERE user_id = $1", user['user_id'])
        if not drv:
            raise HTTPException(status_code=403, detail="Vous devez vous enregistrer comme chauffeur")
        if (drv.get("kyc_status") or "pending_review") != DRIVER_KYC_APPROVED:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Votre profil chauffeur n'est pas encore validé. "
                    "Vous ne pouvez pas voir les courses tant que l'administration "
                    "n'a pas approuvé votre dossier KYC."
                ),
            )
        rows = await conn.fetch("""
            SELECT r.*, u.first_name, u.last_name, u.avatar
            FROM ride_requests r JOIN users u ON r.rider_id = u.user_id
            WHERE r.status = 'pending' AND r.vehicle_type = $1
            ORDER BY r.created_at ASC LIMIT 20
        """, drv['vehicle_type'])
        return [
            {
                "ride_id": r['ride_id'],
                "pickup_address": r['pickup_address'],
                "dropoff_address": r['dropoff_address'],
                "distance_km": str(r['distance_km']),
                "fare_estimated": str(r['fare_estimated']),
                "vehicle_type": r['vehicle_type'],
                "notes": r['notes'],
                "rider": {
                    "name": f"{r['first_name']} {r['last_name']}".strip(),
                    "avatar": r['avatar'] or '',
                },
                "created_at": r['created_at'].isoformat(),
            }
            for r in rows
        ]


@router.post("/{ride_id}/accept")
async def accept_ride(ride_id: str, request: Request):
    driver = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            drv = await conn.fetchrow("SELECT * FROM drivers WHERE user_id = $1", driver['user_id'])
            if not drv:
                raise HTTPException(status_code=403, detail="Vous devez être chauffeur")
            if (drv.get("kyc_status") or "pending_review") != DRIVER_KYC_APPROVED:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Votre profil chauffeur n'est pas encore validé. "
                        "Vous ne pouvez pas accepter de course tant que "
                        "l'administration n'a pas approuvé votre dossier KYC."
                    ),
                )
            ride = await conn.fetchrow("SELECT * FROM ride_requests WHERE ride_id = $1 FOR UPDATE", ride_id)
            if not ride or ride['status'] != 'pending':
                raise HTTPException(status_code=400, detail="Course indisponible")
            if ride['rider_id'] == driver['user_id']:
                raise HTTPException(status_code=400, detail="Impossible d'accepter sa propre course")
            await conn.execute("""
                UPDATE ride_requests SET driver_id = $1, status = 'accepted', accepted_at = $2
                WHERE ride_id = $3
            """, driver['user_id'], datetime.now(timezone.utc), ride_id)
            # Notify rider
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'ride_accepted', 'Chauffeur en route', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", ride['rider_id'],
                 f"{driver['first_name']} arrive. Véhicule: {drv['vehicle_model']} ({drv['vehicle_plate']})")
            return {"message": "Course acceptée", "ride_id": ride_id, "status": "accepted"}


# ══════════════════════════════════════════════════════════════════════════
#  RIDE LIFECYCLE — en_route → started → finished (iter98 Phase 2)
# ══════════════════════════════════════════════════════════════════════════
class DriverPositionRequest(BaseModel):
    """Optional GPS ping the driver app sends with each lifecycle transition."""
    driver_lat: Optional[float] = None
    driver_lng: Optional[float] = None


async def _load_ride_for_driver(conn, ride_id: str, driver_user_id: str, *, expected: tuple):
    """Helper: lock the ride row and check ownership + state. expected is the
    set of statuses that allow this transition. Raises HTTPException."""
    ride = await conn.fetchrow(
        "SELECT * FROM ride_requests WHERE ride_id = $1 FOR UPDATE", ride_id,
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Course introuvable")
    if ride['driver_id'] != driver_user_id:
        raise HTTPException(status_code=403, detail="Cette course ne vous est pas attribuée.")
    if ride['status'] not in expected:
        raise HTTPException(
            status_code=409,
            detail=f"Transition impossible depuis l'état '{ride['status']}'.",
        )
    return ride


def _maybe_position_args(req: DriverPositionRequest):
    """Return (lat, lng, ts) tuple if both coords are provided & valid. Else
    (None, None, None) so SQL can leave the columns untouched."""
    if req.driver_lat is None or req.driver_lng is None:
        return (None, None, None)
    if not is_valid_coord(req.driver_lat, req.driver_lng):
        raise HTTPException(status_code=400, detail="Position GPS chauffeur invalide.")
    return (float(req.driver_lat), float(req.driver_lng), datetime.now(timezone.utc))


@router.post("/{ride_id}/en-route")
async def driver_en_route(ride_id: str, req: DriverPositionRequest, request: Request):
    """Driver picks up the assigned ride and starts heading to the rider."""
    driver = await get_current_user(request)
    lat, lng, ts = _maybe_position_args(req)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ride = await _load_ride_for_driver(
                conn, ride_id, driver['user_id'], expected=('accepted',),
            )
            await conn.execute(
                """UPDATE ride_requests
                     SET status='en_route', en_route_at=$1,
                         driver_lat=COALESCE($2, driver_lat),
                         driver_lng=COALESCE($3, driver_lng),
                         driver_position_at=COALESCE($4, driver_position_at)
                     WHERE ride_id=$5""",
                datetime.now(timezone.utc), lat, lng, ts, ride_id,
            )
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message)
                     VALUES ($1, $2, 'ride_en_route', 'Chauffeur en route',
                             'Votre chauffeur est en route vers vous.')""",
                f"notif_{uuid.uuid4().hex[:12]}", ride['rider_id'],
            )
    return {"ride_id": ride_id, "status": "en_route"}


@router.post("/{ride_id}/start")
async def driver_start_ride(ride_id: str, req: DriverPositionRequest, request: Request):
    """Driver has picked up the rider — actual ride begins."""
    driver = await get_current_user(request)
    lat, lng, ts = _maybe_position_args(req)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ride = await _load_ride_for_driver(
                conn, ride_id, driver['user_id'], expected=('en_route', 'accepted'),
            )
            await conn.execute(
                """UPDATE ride_requests
                     SET status='started', started_at=$1,
                         driver_lat=COALESCE($2, driver_lat),
                         driver_lng=COALESCE($3, driver_lng),
                         driver_position_at=COALESCE($4, driver_position_at)
                     WHERE ride_id=$5""",
                datetime.now(timezone.utc), lat, lng, ts, ride_id,
            )
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message)
                     VALUES ($1, $2, 'ride_started', 'Course démarrée',
                             'Votre course a commencé. Bonne route !')""",
                f"notif_{uuid.uuid4().hex[:12]}", ride['rider_id'],
            )
    return {"ride_id": ride_id, "status": "started"}


@router.post("/{ride_id}/position")
async def driver_position_ping(ride_id: str, req: DriverPositionRequest, request: Request):
    """Lightweight GPS ping during en_route or started — used by tracking link."""
    driver = await get_current_user(request)
    if req.driver_lat is None or req.driver_lng is None:
        raise HTTPException(status_code=400, detail="driver_lat et driver_lng sont requis.")
    if not is_valid_coord(req.driver_lat, req.driver_lng):
        raise HTTPException(status_code=400, detail="Position GPS chauffeur invalide.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        ride = await conn.fetchrow(
            """SELECT driver_id, status FROM ride_requests WHERE ride_id = $1""", ride_id,
        )
        if not ride:
            raise HTTPException(status_code=404, detail="Course introuvable")
        if ride['driver_id'] != driver['user_id']:
            raise HTTPException(status_code=403, detail="Cette course ne vous est pas attribuée.")
        if ride['status'] not in ('en_route', 'started'):
            raise HTTPException(status_code=409, detail="Course pas en cours.")
        await conn.execute(
            """UPDATE ride_requests
                 SET driver_lat=$1, driver_lng=$2, driver_position_at=$3
                 WHERE ride_id=$4""",
            float(req.driver_lat), float(req.driver_lng),
            datetime.now(timezone.utc), ride_id,
        )
    return {"ok": True}


@router.post("/{ride_id}/complete")
async def complete_ride(ride_id: str, request: Request):
    """Driver marks ride as completed → payment from rider wallet → net to driver.

    iter98: only allowed from 'started'. A ride still in 'accepted' or 'en_route'
    cannot be completed — the rider must have been picked up first.
    """
    driver = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ride = await conn.fetchrow("SELECT * FROM ride_requests WHERE ride_id = $1 FOR UPDATE", ride_id)
            if not ride:
                raise HTTPException(status_code=404, detail="Course introuvable")
            if ride['status'] != 'started':
                raise HTTPException(
                    status_code=409,
                    detail=f"Impossible de terminer une course en statut '{ride['status']}'. "
                           f"La course doit avoir été démarrée.",
                )
            if ride['driver_id'] != driver['user_id']:
                raise HTTPException(status_code=403, detail="Seul le chauffeur peut terminer la course")
            fare = ride['fare_estimated']
            rider_wallet = await conn.fetchrow("SELECT balance FROM wallets WHERE user_id = $1 FOR UPDATE", ride['rider_id'])
            if not rider_wallet or rider_wallet['balance'] < fare:
                raise HTTPException(status_code=400, detail="Solde passager insuffisant")
            fee = (fare * COMMISSION).quantize(Decimal("0.01"))
            net = fare - fee
            await conn.execute("UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                               fare, datetime.now(timezone.utc), ride['rider_id'])
            await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                               net, datetime.now(timezone.utc), driver['user_id'])
            await conn.execute("""
                UPDATE ride_requests SET status = 'completed', completed_at = $1, fare_final = $2
                WHERE ride_id = $3
            """, datetime.now(timezone.utc), fare, ride_id)
            await conn.execute("UPDATE drivers SET total_rides = total_rides + 1 WHERE user_id = $1",
                               driver['user_id'])
            tx_id = f"rd_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, fee, status, notes, reference)
                VALUES ($1, $2, $3, 'ride_payment', $4, $5, 'completed', $6, $7)
            """, tx_id, ride['rider_id'], driver['user_id'], fare, fee,
                 f"Course {ride['pickup_address'][:60]} → {ride['dropoff_address'][:60]}", ride_id)
            # Notify rider
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'ride_completed', 'Course terminée', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", ride['rider_id'],
                 f"Course terminée. Facturé: {fare} XAF.")
            return {"message": "Course terminée", "fare": str(fare), "fee": str(fee), "net_driver": str(net)}


@router.post("/{ride_id}/cancel")
async def cancel_ride(ride_id: str, request: Request):
    """Cancel a ride. Allowed from 'pending', 'accepted', 'en_route', or
    'arriving' only. Once the ride has started, the driver must complete
    it (no refund).

    iter133 — Distinct cancellation status:
      • 'cancelled'             → cancelled by the rider
      • 'cancelled_by_driver'   → cancelled by the assigned driver
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ride = await conn.fetchrow(
                "SELECT * FROM ride_requests WHERE ride_id = $1 FOR UPDATE", ride_id,
            )
            if not ride:
                raise HTTPException(status_code=404, detail="Course introuvable")
            is_rider  = ride['rider_id']  == user['user_id']
            is_driver = ride['driver_id'] == user['user_id']
            if not (is_rider or is_driver):
                raise HTTPException(status_code=403, detail="Non autorisé")
            if ride['status'] not in ('pending', 'accepted', 'en_route', 'arriving'):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Course non annulable (statut: {ride['status']}). "
                        "Une course démarrée doit être terminée par le chauffeur."
                    ),
                )
            new_status = 'cancelled_by_driver' if is_driver else 'cancelled'
            # iter133 — Rider cancel timer: enforce admin-configured min wait
            # AFTER a driver has accepted (you can always cancel pending).
            if is_rider and ride['status'] in ('accepted', 'en_route', 'arriving'):
                from services.settings_service import get_setting as _get_setting
                try:
                    grace_s = int(await _get_setting("transport_rider_cancel_after_seconds") or 0)
                except (TypeError, ValueError):
                    grace_s = 0
                if grace_s > 0 and ride['accepted_at']:
                    elapsed = (
                        datetime.now(timezone.utc) - ride['accepted_at']
                    ).total_seconds()
                    if elapsed < grace_s:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Patientez encore {int(grace_s - elapsed)}s avant "
                                f"d'annuler. Votre chauffeur arrive."
                            ),
                        )
            await conn.execute(
                "UPDATE ride_requests SET status = $1, cancelled_at = $2 WHERE ride_id = $3",
                new_status, datetime.now(timezone.utc), ride_id,
            )
            # Notify the other party (best-effort).
            other_user = ride['driver_id'] if is_rider else ride['rider_id']
            if other_user:
                who = "passager" if is_rider else "chauffeur"
                await conn.execute(
                    """INSERT INTO notifications (notif_id, user_id, type, title, message)
                         VALUES ($1, $2, 'ride_cancelled', 'Course annulée', $3)""",
                    f"notif_{uuid.uuid4().hex[:12]}", other_user,
                    f"La course a été annulée par le {who}.",
                )
            return {"message": "Course annulée", "status": new_status}


@router.get("/{ride_id}")
async def ride_detail(ride_id: str, request: Request):
    """Return ride details + driver position. Used by both rider and driver.

    iter133 — Includes rider/driver phone numbers + avatars so the driver
    can contact the rider after accepting (Call / WhatsApp), and vice versa
    once a driver is assigned. Authorization: rider OR assigned driver only.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        ride = await conn.fetchrow(
            """SELECT r.*,
                      u.first_name  AS rider_first,
                      u.last_name   AS rider_last,
                      u.phone_number AS rider_phone,
                      u.avatar       AS rider_avatar,
                      du.first_name AS driver_first,
                      du.last_name  AS driver_last,
                      du.phone_number AS driver_phone,
                      du.avatar       AS driver_avatar,
                      d.vehicle_model, d.vehicle_plate, d.rating
                 FROM ride_requests r
                 JOIN users u ON r.rider_id = u.user_id
                 LEFT JOIN users du ON r.driver_id = du.user_id
                 LEFT JOIN drivers d ON r.driver_id = d.user_id
                 WHERE r.ride_id = $1""",
            ride_id,
        )
        if not ride:
            raise HTTPException(status_code=404, detail="Course introuvable")
        if ride['rider_id'] != user['user_id'] and ride['driver_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")
    return _ride_to_dict(ride)


@router.get("/{ride_id}/tracking")
async def ride_tracking(ride_id: str, request: Request):
    """iter133 — Lightweight polling endpoint for the rider's UI to refresh
    every 3-5s. Returns driver position + computed ETA + distance to pickup
    (or to dropoff after the ride started). 200 even when no driver yet
    (eta=null) to avoid frontend error toasts on `pending`.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        ride = await conn.fetchrow(
            """SELECT ride_id, rider_id, driver_id, status,
                      pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                      driver_lat, driver_lng, driver_position_at,
                      created_at, accepted_at
                 FROM ride_requests WHERE ride_id = $1""",
            ride_id,
        )
        if not ride:
            raise HTTPException(status_code=404, detail="Course introuvable")
        if ride['rider_id'] != user['user_id'] and ride['driver_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")

    out = {
        "ride_id": ride_id,
        "status": ride["status"],
        "driver_lat": float(ride["driver_lat"]) if ride.get("driver_lat") is not None else None,
        "driver_lng": float(ride["driver_lng"]) if ride.get("driver_lng") is not None else None,
        "driver_position_at": ride["driver_position_at"].isoformat()
            if ride.get("driver_position_at") else None,
        "eta_seconds": None,
        "distance_meters": None,
        "stage": "pickup" if ride["status"] in ("accepted", "en_route", "arriving") else (
                 "trip"   if ride["status"] == "started" else None),
    }
    # Compute distance + ETA only when the driver has a known position.
    if ride.get("driver_lat") is None or ride.get("driver_lng") is None:
        return out
    target_lat, target_lng = (
        (ride["pickup_lat"], ride["pickup_lng"])
        if ride["status"] in ("accepted", "en_route", "arriving")
        else (ride["dropoff_lat"], ride["dropoff_lng"])
    )
    if target_lat is None or target_lng is None:
        return out
    km = haversine_km(
        float(ride["driver_lat"]), float(ride["driver_lng"]),
        float(target_lat), float(target_lng),
    )
    meters = int(round(km * 1000))
    # Average urban speed assumption 25 km/h ⇒ 6.94 m/s. Keep deliberately
    # conservative — actual ETA is updated as driver position changes.
    eta_s = max(0, int(round((km / 25.0) * 3600)))
    out["distance_meters"] = meters
    out["eta_seconds"] = eta_s
    return out


def _ride_to_dict(r) -> dict:
    """Map an asyncpg ride row to the JSON public payload."""
    d = dict(r)
    return {
        "ride_id": d["ride_id"],
        "status": d["status"],
        "rider_id": d["rider_id"],
        "driver_id": d.get("driver_id"),
        "pickup_address": d.get("pickup_address"),
        "dropoff_address": d.get("dropoff_address"),
        "pickup_lat": d.get("pickup_lat"),
        "pickup_lng": d.get("pickup_lng"),
        "dropoff_lat": d.get("dropoff_lat"),
        "dropoff_lng": d.get("dropoff_lng"),
        "pickup_h3": d.get("pickup_h3"),
        "dropoff_h3": d.get("dropoff_h3"),
        "distance_km": str(d["distance_km"]) if d.get("distance_km") is not None else None,
        "fare_estimated": str(d["fare_estimated"]) if d.get("fare_estimated") is not None else None,
        "fare_final": str(d["fare_final"]) if d.get("fare_final") is not None else None,
        "vehicle_type": d.get("vehicle_type"),
        "notes": d.get("notes"),
        "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
        "accepted_at": d["accepted_at"].isoformat() if d.get("accepted_at") else None,
        "en_route_at": d["en_route_at"].isoformat() if d.get("en_route_at") else None,
        "started_at": d["started_at"].isoformat() if d.get("started_at") else None,
        "completed_at": d["completed_at"].isoformat() if d.get("completed_at") else None,
        "cancelled_at": d["cancelled_at"].isoformat() if d.get("cancelled_at") else None,
        "driver_position": (
            {
                "lat": d["driver_lat"], "lng": d["driver_lng"],
                "at": d["driver_position_at"].isoformat() if d.get("driver_position_at") else None,
            }
            if d.get("driver_lat") is not None else None
        ),
        # iter133 — Rider details visible to the driver after accept (and to
        # the rider themselves). Phone number is intentionally exposed here
        # so the driver can call/WhatsApp the rider during pickup. The
        # caller is already authenticated AND must be the assigned driver
        # OR the rider — enforced in `ride_detail`.
        "rider": {
            "name": f"{(d.get('rider_first') or '').strip()} {(d.get('rider_last') or '').strip()}".strip(),
            "phone": d.get("rider_phone") or "",
            "avatar": d.get("rider_avatar") or "",
        },
        "driver": (
            {
                "name": f"{(d.get('driver_first') or '').strip()} {(d.get('driver_last') or '').strip()}".strip(),
                "phone": d.get("driver_phone") or "",
                "avatar": d.get("driver_avatar") or "",
                "vehicle_model": d.get("vehicle_model"),
                "vehicle_plate": d.get("vehicle_plate"),
                "rating": str(d.get("rating") or "5.00"),
            } if d.get("driver_id") else None
        ),
    }


@router.get("/my-rides")
async def my_rides(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM ride_requests WHERE rider_id = $1 OR driver_id = $1
            ORDER BY created_at DESC LIMIT 50
        """, user['user_id'])
        out = []
        for r in rows:
            d = dict(r)
            for k in ['distance_km', 'fare_estimated']:
                d[k] = str(d[k])
            d['fare_final'] = str(d['fare_final']) if d['fare_final'] is not None else None
            for k in ['pickup_lat', 'pickup_lng', 'dropoff_lat', 'dropoff_lng']:
                d[k] = str(d[k]) if d[k] is not None else None
            for k in ['created_at', 'accepted_at', 'completed_at', 'cancelled_at']:
                d[k] = d[k].isoformat() if d[k] else None
            d['role'] = 'rider' if r['rider_id'] == user['user_id'] else 'driver'
            out.append(d)
        return out


@router.post("/driver/register")
async def register_driver(req: RegisterDriverRequest, request: Request):
    """Submit (or update) a driver KYC application.

    Strict rules (iter97):
      • Status starts at 'pending_review'.
      • A driver already 'approved' can update vehicle info but NOT documents
        (any change to license_number / images resets status to 'pending_review').
      • A driver 'rejected' can resubmit (status goes back to 'pending_review').
      • A driver 'suspended' must contact support — registration update blocked.
    """
    user = await get_current_user(request)
    if req.vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=400, detail="Type véhicule invalide")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)

        # Auto-detect country from user profile if frontend didn't pass one.
        country = (req.country_code or "").strip().upper()[:2] or None
        if not country:
            country = await conn.fetchval(
                "SELECT country_code FROM users WHERE user_id = $1", user["user_id"]
            )

        existing = await conn.fetchrow(
            "SELECT * FROM drivers WHERE user_id = $1", user["user_id"]
        )
        now = datetime.now(timezone.utc)

        if existing and existing["kyc_status"] == DRIVER_KYC_SUSPENDED:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Compte chauffeur suspendu. Contactez le support pour "
                    "rouvrir l'accès."
                ),
            )

        # Determine the new KYC status:
        # • New driver → pending_review
        # • Re-submission after rejection → pending_review
        # • Already approved with NO doc/license change → keep approved
        # • Already approved with doc/license change → reset to pending_review
        new_status = DRIVER_KYC_PENDING
        docs_changed = True
        if existing and existing["kyc_status"] == DRIVER_KYC_APPROVED:
            same_license = (
                (existing.get("license_number") or "") == req.license_number.strip()
                and existing.get("license_image_url") == req.license_image_url
                and existing.get("id_card_image_url") == req.id_card_image_url
                and existing.get("selfie_with_license_url") == req.selfie_with_license_url
            )
            if same_license:
                new_status = DRIVER_KYC_APPROVED
                docs_changed = False

        if existing:
            await conn.execute(
                """UPDATE drivers
                   SET vehicle_model=$1, vehicle_plate=$2, vehicle_type=$3,
                       personal_phone=$4,
                       emergency_contact_phone=$5, emergency_contact_name=$6,
                       license_number=$7, license_issue_date=$8,
                       license_image_url=$9, id_card_image_url=$10,
                       selfie_with_license_url=$11, country_code=$12,
                       kyc_status=$13, kyc_submitted_at=$14,
                       kyc_rejection_reason=NULL,
                       kyc_reviewed_at=CASE WHEN $13 = 'approved' THEN kyc_reviewed_at ELSE NULL END,
                       kyc_reviewed_by=CASE WHEN $13 = 'approved' THEN kyc_reviewed_by ELSE NULL END
                   WHERE user_id = $15""",
                req.vehicle_model.strip()[:100], req.vehicle_plate.strip().upper()[:30],
                req.vehicle_type, req.personal_phone.strip()[:32],
                req.emergency_contact_phone.strip()[:32],
                req.emergency_contact_name.strip()[:80],
                req.license_number.strip()[:50], req.license_issue_date,
                req.license_image_url, req.id_card_image_url,
                req.selfie_with_license_url, country,
                new_status, now if docs_changed else existing["kyc_submitted_at"],
                user["user_id"],
            )
            return {
                "message": (
                    "Profil chauffeur mis à jour. En attente d'examen administrateur."
                    if new_status == DRIVER_KYC_PENDING else
                    "Profil mis à jour."
                ),
                "kyc_status": new_status,
                "driver_id": existing["driver_id"],
            }

        driver_id = f"drv_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """INSERT INTO drivers
                 (driver_id, user_id, vehicle_model, vehicle_plate, vehicle_type,
                  personal_phone, emergency_contact_phone, emergency_contact_name,
                  license_number, license_issue_date,
                  license_image_url, id_card_image_url, selfie_with_license_url,
                  country_code, kyc_status, kyc_submitted_at, status)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,'pending')""",
            driver_id, user["user_id"],
            req.vehicle_model.strip()[:100], req.vehicle_plate.strip().upper()[:30],
            req.vehicle_type, req.personal_phone.strip()[:32],
            req.emergency_contact_phone.strip()[:32],
            req.emergency_contact_name.strip()[:80],
            req.license_number.strip()[:50], req.license_issue_date,
            req.license_image_url, req.id_card_image_url,
            req.selfie_with_license_url, country,
            DRIVER_KYC_PENDING, now,
        )
        return {
            "message": (
                "Inscription chauffeur soumise. Vous recevrez une notification "
                "dès qu'un administrateur aura validé vos documents."
            ),
            "kyc_status": DRIVER_KYC_PENDING,
            "driver_id": driver_id,
        }


@router.get("/driver/me")
async def driver_me(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        drv = await conn.fetchrow("SELECT * FROM drivers WHERE user_id = $1", user["user_id"])
    return driver_to_public(dict(drv) if drv else None)


@router.post("/driver/online")
async def driver_set_online(request: Request, online: bool = True):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        drv = await conn.fetchrow(
            "SELECT kyc_status FROM drivers WHERE user_id = $1", user["user_id"]
        )
        if not drv:
            raise HTTPException(status_code=403, detail="Vous n'êtes pas chauffeur.")
        if drv["kyc_status"] != DRIVER_KYC_APPROVED:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Votre profil chauffeur doit être validé par un administrateur "
                    "avant de pouvoir aller en ligne."
                ),
            )
        await conn.execute(
            "UPDATE drivers SET is_online = $1 WHERE user_id = $2", online, user["user_id"]
        )
        return {"is_online": online}



# ══════════════════════════════════════════════════════════════════════════
#  ADMIN — Driver KYC Validation (iter97)
# ══════════════════════════════════════════════════════════════════════════
async def _require_admin(request: Request):
    """Re-uses the same admin-role check as other admin routers."""
    user = await get_current_user(request)
    if (user.get("role") or "").lower() not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs.")
    return user


class DriverDecisionRequest(BaseModel):
    reason: Optional[str] = ""


@router.get("/admin/drivers")
async def admin_list_drivers(
    request: Request,
    status: str = Query("pending_review", pattern="^(pending_review|approved|rejected|suspended|all)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List drivers filtered by KYC status. Default = pending_review (admin queue)."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        if status == "all":
            where, params = "", []
        else:
            where, params = "WHERE d.kyc_status = $1", [status]
        rows = await conn.fetch(
            f"""SELECT d.*, u.email, u.first_name, u.last_name, u.avatar, u.country_code AS user_country
                  FROM drivers d
                  JOIN users u ON d.user_id = u.user_id
                  {where}
                  ORDER BY COALESCE(d.kyc_submitted_at, d.created_at) DESC
                  LIMIT {int(limit)} OFFSET {int(offset)}""",
            *params,
        )
        counts_rows = await conn.fetch(
            """SELECT COALESCE(kyc_status, 'pending_review') AS s, COUNT(*) AS c
                 FROM drivers GROUP BY s"""
        )
        counts = {r["s"]: int(r["c"]) for r in counts_rows}

    items = []
    for r in rows:
        d = driver_to_public(dict(r))
        d["user"] = {
            "user_id": r["user_id"],
            "email": r["email"],
            "name": f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip(),
            "avatar": r["avatar"] or "",
            "country_code": r["user_country"] or "",
        }
        items.append(d)
    return {
        "items": items,
        "total": sum(counts.values()),
        "counts": {
            "pending_review": counts.get("pending_review", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "suspended": counts.get("suspended", 0),
        },
    }


@router.get("/admin/drivers/{driver_id}")
async def admin_driver_detail(driver_id: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        await ensure_reviews_ddl(conn)
        d = await conn.fetchrow(
            """SELECT d.*, u.email, u.first_name, u.last_name, u.avatar
                 FROM drivers d JOIN users u ON d.user_id = u.user_id
                 WHERE d.driver_id = $1""",
            driver_id,
        )
        if not d:
            raise HTTPException(status_code=404, detail="Chauffeur introuvable")
        history = await conn.fetch(
            """SELECT decision, reason, decided_by, decided_at
                 FROM driver_kyc_decisions
                 WHERE driver_id = $1 ORDER BY decided_at DESC LIMIT 50""",
            driver_id,
        )
        # Last 5 reviews + histogram for the admin overview
        reviews = await conn.fetch(
            """SELECT r.rating, r.comment, r.created_at, u.first_name AS rider_first
                 FROM ride_reviews r
                 JOIN users u ON r.rider_id = u.user_id
                 WHERE r.driver_id = $1
                 ORDER BY r.created_at DESC LIMIT 5""",
            d["user_id"],
        )
        hist_rows = await conn.fetch(
            """SELECT rating, COUNT(*)::int AS n
                 FROM ride_reviews WHERE driver_id = $1
                 GROUP BY rating""",
            d["user_id"],
        )
    out = driver_to_public(dict(d))
    out["user"] = {
        "user_id": d["user_id"],
        "email": d["email"],
        "name": f"{(d['first_name'] or '').strip()} {(d['last_name'] or '').strip()}".strip(),
        "avatar": d["avatar"] or "",
    }
    out["history"] = [
        {
            "decision": h["decision"],
            "reason": h["reason"] or "",
            "decided_by": h["decided_by"],
            "decided_at": h["decided_at"].isoformat() if h["decided_at"] else None,
        }
        for h in history
    ]
    histogram = {str(s): 0 for s in (1, 2, 3, 4, 5)}
    for h in hist_rows:
        histogram[str(h["rating"])] = int(h["n"])
    out["reviews"] = {
        "summary": {
            "average": str(d["rating"] or "5.00"),
            "total": int(d.get("total_reviews") or 0),
            "histogram": histogram,
        },
        "recent": [
            {
                "rating": r["rating"],
                "comment": r["comment"] or "",
                "created_at": r["created_at"].isoformat(),
                "rider_first_name": (r["rider_first"] or "").strip() or "Passager",
            }
            for r in reviews
        ],
    }
    return out


async def _record_decision(conn, *, driver_id: str, user_id: str,
                           decision: str, reason: str, admin_id: str,
                           new_status: str, set_reason_in_drivers: bool):
    """Apply the new kyc_status + log the decision in driver_kyc_decisions.
    Centralised so approve/reject/suspend all use the same write pattern."""
    now = datetime.now(timezone.utc)
    if set_reason_in_drivers:
        await conn.execute(
            """UPDATE drivers SET kyc_status=$1, kyc_reviewed_at=$2,
                                  kyc_reviewed_by=$3, kyc_rejection_reason=$4
                 WHERE driver_id=$5""",
            new_status, now, admin_id, reason or None, driver_id,
        )
    else:
        await conn.execute(
            """UPDATE drivers SET kyc_status=$1, kyc_reviewed_at=$2,
                                  kyc_reviewed_by=$3, kyc_rejection_reason=NULL
                 WHERE driver_id=$4""",
            new_status, now, admin_id, driver_id,
        )
    await conn.execute(
        """INSERT INTO driver_kyc_decisions
             (driver_id, user_id, decision, reason, decided_by)
             VALUES ($1,$2,$3,$4,$5)""",
        driver_id, user_id, decision, reason or None, admin_id,
    )
    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
    if decision == "approved":
        title = "✅ Compte chauffeur validé"
        msg = "Votre dossier KYC a été approuvé. Vous pouvez maintenant aller en ligne et accepter des courses."
    elif decision == "rejected":
        title = "❌ Dossier KYC refusé"
        msg = (reason or "Votre dossier nécessite des corrections.") + " Vous pouvez resoumettre à tout moment."
    else:
        title = "⛔ Compte chauffeur suspendu"
        msg = (reason or "Votre activité chauffeur est temporairement suspendue.") + " Contactez le support."
    await conn.execute(
        """INSERT INTO notifications (notif_id, user_id, type, title, message)
             VALUES ($1, $2, $3, $4, $5)""",
        notif_id, user_id, f"driver_kyc_{decision}", title, msg,
    )


@router.post("/admin/drivers/{driver_id}/approve")
async def admin_approve_driver(driver_id: str, req: DriverDecisionRequest, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        async with conn.transaction():
            d = await conn.fetchrow(
                "SELECT user_id, kyc_status FROM drivers WHERE driver_id = $1 FOR UPDATE",
                driver_id,
            )
            if not d:
                raise HTTPException(status_code=404, detail="Chauffeur introuvable")
            if d["kyc_status"] == DRIVER_KYC_APPROVED:
                return {"message": "Déjà approuvé", "kyc_status": DRIVER_KYC_APPROVED}
            await _record_decision(
                conn, driver_id=driver_id, user_id=d["user_id"],
                decision="approved", reason=(req.reason or "").strip()[:500],
                admin_id=admin["user_id"], new_status=DRIVER_KYC_APPROVED,
                set_reason_in_drivers=False,
            )
            await conn.execute(
                "UPDATE drivers SET status='active' WHERE driver_id=$1", driver_id,
            )
    return {"message": "Chauffeur approuvé", "kyc_status": DRIVER_KYC_APPROVED}


@router.post("/admin/drivers/{driver_id}/reject")
async def admin_reject_driver(driver_id: str, req: DriverDecisionRequest, request: Request):
    admin = await _require_admin(request)
    reason = (req.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(
            status_code=400,
            detail="Une raison (≥ 5 caractères) est obligatoire pour refuser un dossier.",
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        async with conn.transaction():
            d = await conn.fetchrow(
                "SELECT user_id FROM drivers WHERE driver_id = $1 FOR UPDATE", driver_id
            )
            if not d:
                raise HTTPException(status_code=404, detail="Chauffeur introuvable")
            await _record_decision(
                conn, driver_id=driver_id, user_id=d["user_id"],
                decision="rejected", reason=reason[:500],
                admin_id=admin["user_id"], new_status=DRIVER_KYC_REJECTED,
                set_reason_in_drivers=True,
            )
            await conn.execute(
                "UPDATE drivers SET is_online=FALSE, status='inactive' WHERE driver_id=$1",
                driver_id,
            )
    return {"message": "Chauffeur refusé", "kyc_status": DRIVER_KYC_REJECTED}


@router.post("/admin/drivers/{driver_id}/suspend")
async def admin_suspend_driver(driver_id: str, req: DriverDecisionRequest, request: Request):
    admin = await _require_admin(request)
    reason = (req.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(
            status_code=400,
            detail="Une raison (≥ 5 caractères) est obligatoire pour suspendre un chauffeur.",
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        async with conn.transaction():
            d = await conn.fetchrow(
                "SELECT user_id FROM drivers WHERE driver_id = $1 FOR UPDATE", driver_id
            )
            if not d:
                raise HTTPException(status_code=404, detail="Chauffeur introuvable")
            await _record_decision(
                conn, driver_id=driver_id, user_id=d["user_id"],
                decision="suspended", reason=reason[:500],
                admin_id=admin["user_id"], new_status=DRIVER_KYC_SUSPENDED,
                set_reason_in_drivers=True,
            )
            await conn.execute(
                "UPDATE drivers SET is_online=FALSE, status='inactive' WHERE driver_id=$1",
                driver_id,
            )
    return {"message": "Chauffeur suspendu", "kyc_status": DRIVER_KYC_SUSPENDED}



# ══════════════════════════════════════════════════════════════════════════
#  PHASE C — Admin Transport Overview Dashboard (P1.3)
# ══════════════════════════════════════════════════════════════════════════
@router.get("/admin/overview")
async def admin_transport_overview(
    request: Request,
    days: int = Query(30, ge=1, le=365),
):
    """Operational KPI dashboard for the Transport vertical.

    Returns:
      • drivers      : counts by KYC status + online count
      • rides        : totals broken down by status over the window
      • revenue      : gross fares + JAPAP commission accumulated
      • timeseries   : daily rides + commission for a chart
      • top_drivers  : 10 highest earning + 10 lowest rated (>= 3 reviews)
    """
    await _require_admin(request)
    pool = await get_pool()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        await ensure_driver_kyc_ddl(conn)
        await ensure_reviews_ddl(conn)
        # Drivers — counts by kyc_status + online
        kyc_counts_rows = await conn.fetch(
            "SELECT kyc_status, COUNT(*)::int AS n FROM drivers GROUP BY kyc_status"
        )
        kyc_counts = {
            "pending_review": 0, "approved": 0, "rejected": 0, "suspended": 0,
        }
        for r in kyc_counts_rows:
            kyc_counts[r["kyc_status"] or "pending_review"] = int(r["n"])
        online_count = await conn.fetchval(
            "SELECT COUNT(*)::int FROM drivers WHERE is_online = TRUE AND kyc_status='approved'"
        ) or 0

        # Rides over the window — counts by status
        ride_status_rows = await conn.fetch(
            """SELECT status, COUNT(*)::int AS n FROM ride_requests
                 WHERE created_at >= $1 GROUP BY status""",
            since,
        )
        ride_counts = {"pending": 0, "accepted": 0, "en_route": 0,
                       "started": 0, "completed": 0, "cancelled": 0}
        for r in ride_status_rows:
            ride_counts[r["status"]] = int(r["n"])
        total_rides = sum(ride_counts.values())

        # Revenue — gross + commission. We use fare_final on completed rides
        # only (the rider was actually charged). Commission == 15% of fare.
        rev = await conn.fetchrow(
            """SELECT
                  COALESCE(SUM(fare_final), 0)::numeric(14,2) AS gross,
                  COALESCE(SUM(fare_final * 0.15), 0)::numeric(14,2) AS commission
                 FROM ride_requests
                 WHERE status='completed' AND completed_at >= $1""",
            since,
        )

        # Daily timeseries — completed rides + commission per day
        ts_rows = await conn.fetch(
            """SELECT DATE_TRUNC('day', completed_at) AS d,
                       COUNT(*)::int AS rides,
                       COALESCE(SUM(fare_final), 0)::numeric(14,2) AS gross,
                       COALESCE(SUM(fare_final * 0.15), 0)::numeric(14,2) AS commission
                 FROM ride_requests
                 WHERE status='completed' AND completed_at >= $1
                 GROUP BY 1 ORDER BY 1""",
            since,
        )

        # Top earners — most completed rides + their gross + ratings.
        top_earners = await conn.fetch(
            """SELECT d.driver_id, d.user_id, d.rating, d.total_reviews, d.total_rides,
                       u.first_name, u.last_name, u.avatar,
                       COALESCE(SUM(r.fare_final), 0)::numeric(14,2) AS earnings,
                       COUNT(r.ride_id)::int AS completed
                 FROM drivers d
                 JOIN users u ON d.user_id = u.user_id
                 LEFT JOIN ride_requests r
                   ON r.driver_id = d.user_id
                  AND r.status = 'completed'
                  AND r.completed_at >= $1
                 WHERE d.kyc_status = 'approved'
                 GROUP BY d.driver_id, d.user_id, d.rating, d.total_reviews,
                          d.total_rides, u.first_name, u.last_name, u.avatar
                 ORDER BY earnings DESC NULLS LAST
                 LIMIT 10""",
            since,
        )

        # Worst rated — only drivers with at least 3 reviews to filter noise.
        bottom_rated = await conn.fetch(
            """SELECT d.driver_id, d.user_id, d.rating, d.total_reviews, d.total_rides,
                       u.first_name, u.last_name, u.avatar
                 FROM drivers d
                 JOIN users u ON d.user_id = u.user_id
                 WHERE d.kyc_status = 'approved' AND d.total_reviews >= 3
                 ORDER BY d.rating ASC, d.total_reviews DESC
                 LIMIT 10""",
        )

    def _driver_row(r, with_earnings=False):
        out = {
            "driver_id": r["driver_id"],
            "user_id": r["user_id"],
            "name": f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip(),
            "avatar": r["avatar"] or "",
            "rating": str(r["rating"] or "5.00"),
            "total_reviews": int(r["total_reviews"] or 0),
            "total_rides": int(r["total_rides"] or 0),
        }
        if with_earnings:
            out["earnings"] = str(r["earnings"] or "0")
            out["completed_window"] = int(r["completed"] or 0)
        return out

    return {
        "window_days": days,
        "drivers": {
            "total": sum(kyc_counts.values()),
            "by_kyc_status": kyc_counts,
            "online": int(online_count),
        },
        "rides": {
            "total": total_rides,
            "by_status": ride_counts,
        },
        "revenue": {
            "gross": str(rev["gross"] or "0"),
            "commission": str(rev["commission"] or "0"),
            "currency": "XAF",  # mixed; UI may add a note. Phase B per-country WIP.
        },
        "timeseries": [
            {
                "day": ts["d"].date().isoformat(),
                "rides": int(ts["rides"]),
                "gross": str(ts["gross"] or "0"),
                "commission": str(ts["commission"] or "0"),
            }
            for ts in ts_rows
        ],
        "top_earners": [_driver_row(r, with_earnings=True) for r in top_earners],
        "bottom_rated": [_driver_row(r) for r in bottom_rated],
    }



# ══════════════════════════════════════════════════════════════════════════
#  ADMIN — Demand heatmap (iter98 Phase 2 enhancement)
# ══════════════════════════════════════════════════════════════════════════
@router.get("/admin/heatmap")
async def admin_demand_heatmap(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    kind: str = Query("pickup", pattern="^(pickup|dropoff)$"),
    limit: int = Query(500, ge=1, le=5000),
):
    """Aggregate ride requests by H3 cell to feed a future heatmap UI.

    Returns the top `limit` busiest cells over the last `days` days, with the
    cell's center lat/lng (computed from H3) and the request count. Frontend
    can plot these as circles on a Mapbox/Leaflet layer with radius = log(count).

    Caller can switch between origin (pickup) and destination (dropoff) heat
    by toggling `kind`. Used by ops to spot driver shortages and feed Phase 5
    surge-pricing inputs.
    """
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ride_geo_ddl(conn)
        col = "pickup_h3" if kind == "pickup" else "dropoff_h3"
        rows = await conn.fetch(
            f"""SELECT {col} AS cell, COUNT(*) AS n
                  FROM ride_requests
                  WHERE {col} IS NOT NULL
                    AND created_at >= NOW() - ($1::INT || ' days')::INTERVAL
                  GROUP BY {col}
                  ORDER BY n DESC
                  LIMIT $2""",
            days, limit,
        )
    cells = []
    for r in rows:
        cell = r["cell"]
        try:
            lat, lng = _h3.cell_to_latlng(cell)
        except Exception as e:
            logger.warning("Invalid H3 cell '%s' skipped in heatmap: %s", cell, e)
            continue
        cells.append({
            "cell": cell,
            "count": int(r["n"]),
            "lat": lat,
            "lng": lng,
        })
    return {"days": days, "kind": kind, "cells": cells, "total": len(cells)}



# ══════════════════════════════════════════════════════════════════════════
#  PHASE A — Driver Rating & Review System (P1.1)
# ══════════════════════════════════════════════════════════════════════════
#
# Riders rate the driver on a 1-5 stars scale + optional comment, ONLY after
# the ride status flips to 'completed'. Each ride yields at most one review
# (UNIQUE(ride_id)). On submit, drivers.rating is recomputed as the rolling
# average across all reviews for that driver, and drivers.total_reviews is
# atomically updated.
# ══════════════════════════════════════════════════════════════════════════
class RideReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=500)


async def ensure_reviews_ddl(conn):
    """Self-heal DDL for ride_reviews + drivers.total_reviews. Idempotent."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ride_reviews (
            review_id      VARCHAR(64) PRIMARY KEY,
            ride_id        VARCHAR(64) NOT NULL UNIQUE,
            driver_id      VARCHAR(64) NOT NULL,
            rider_id       VARCHAR(64) NOT NULL,
            rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            comment        TEXT NOT NULL DEFAULT '',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ride_reviews_driver "
        "ON ride_reviews (driver_id, created_at DESC)"
    )
    await conn.execute(
        "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS total_reviews INTEGER NOT NULL DEFAULT 0"
    )


@router.post("/{ride_id}/review")
async def submit_ride_review(ride_id: str, req: RideReviewRequest, request: Request):
    """Rider posts a 1-5 stars + comment for a completed ride. Idempotent
    by ride_id (returns 409 if a review already exists). Re-computes the
    driver's rolling average rating + total_reviews atomically."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_reviews_ddl(conn)
        async with conn.transaction():
            ride = await conn.fetchrow(
                "SELECT rider_id, driver_id, status FROM ride_requests WHERE ride_id = $1",
                ride_id,
            )
            if not ride:
                raise HTTPException(status_code=404, detail="Course introuvable")
            if ride["rider_id"] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Seul le passager peut noter la course")
            if ride["status"] != "completed":
                raise HTTPException(
                    status_code=409,
                    detail="La course doit être terminée avant de pouvoir être notée.",
                )
            if not ride["driver_id"]:
                raise HTTPException(status_code=400, detail="Aucun chauffeur associé à la course")
            existing = await conn.fetchval(
                "SELECT review_id FROM ride_reviews WHERE ride_id = $1", ride_id
            )
            if existing:
                raise HTTPException(status_code=409, detail="Cette course a déjà été notée")
            review_id = f"rev_{uuid.uuid4().hex[:12]}"
            try:
                await conn.execute(
                    """INSERT INTO ride_reviews
                           (review_id, ride_id, driver_id, rider_id, rating, comment)
                           VALUES ($1, $2, $3, $4, $5, $6)""",
                    review_id, ride_id, ride["driver_id"], user["user_id"],
                    req.rating, (req.comment or "").strip(),
                )
            except Exception as e:
                # Convert the UNIQUE(ride_id) race-condition into a clean 409
                # instead of leaking a 500 to the rider on concurrent double-taps.
                if "ride_reviews" in str(e) and "duplicate" in str(e).lower():
                    raise HTTPException(status_code=409, detail="Cette course a déjà été notée")
                raise
            # Recompute the driver's rolling average + total_reviews from
            # the single source of truth (the reviews table itself).
            agg = await conn.fetchrow(
                """SELECT COUNT(*)::int AS n, AVG(rating)::numeric(3,2) AS avg
                       FROM ride_reviews WHERE driver_id = $1""",
                ride["driver_id"],
            )
            new_avg = agg["avg"] if agg and agg["avg"] is not None else Decimal("5.00")
            new_total = int(agg["n"] or 0)
            await conn.execute(
                "UPDATE drivers SET rating = $1, total_reviews = $2 WHERE user_id = $3",
                new_avg, new_total, ride["driver_id"],
            )
            # Notify the driver
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message)
                       VALUES ($1, $2, 'driver_review', $3, $4)""",
                f"notif_{uuid.uuid4().hex[:12]}", ride["driver_id"],
                "Nouvelle évaluation reçue",
                f"Vous avez reçu une note de {req.rating}/5 sur votre dernière course.",
            )
    return {
        "review_id": review_id,
        "rating": req.rating,
        "comment": req.comment.strip(),
        "driver_avg_rating": str(new_avg),
        "driver_total_reviews": new_total,
    }


@router.get("/{ride_id}/review")
async def get_ride_review(ride_id: str, request: Request):
    """Returns the existing review for a ride (or null) + whether the
    current user is allowed to submit one. Used by the rider modal to
    decide whether to auto-pop after the ride flips to completed."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_reviews_ddl(conn)
        ride = await conn.fetchrow(
            "SELECT rider_id, driver_id, status FROM ride_requests WHERE ride_id = $1",
            ride_id,
        )
        if not ride:
            raise HTTPException(status_code=404, detail="Course introuvable")
        if user["user_id"] not in (ride["rider_id"], ride["driver_id"]):
            raise HTTPException(status_code=403, detail="Non autorisé")
        row = await conn.fetchrow(
            """SELECT review_id, rating, comment, created_at
                   FROM ride_reviews WHERE ride_id = $1""",
            ride_id,
        )
    return {
        "ride_id": ride_id,
        "ride_status": ride["status"],
        "can_submit": (
            ride["status"] == "completed"
            and ride["rider_id"] == user["user_id"]
            and row is None
        ),
        "review": (
            {
                "review_id": row["review_id"],
                "rating": row["rating"],
                "comment": row["comment"],
                "created_at": row["created_at"].isoformat(),
            } if row else None
        ),
    }


@router.get("/driver/{driver_id}/reviews")
async def driver_reviews(
    driver_id: str, request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Paginated list of public reviews for a driver. Reveals rider first
    name only. `summary` block carries the rolling average + count + 1-5 star
    histogram suitable for an admin or driver-profile page."""
    await get_current_user(request)  # auth required, no role gating
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_reviews_ddl(conn)
        d = await conn.fetchrow(
            "SELECT user_id, rating, total_reviews FROM drivers WHERE driver_id = $1",
            driver_id,
        )
        if not d:
            raise HTTPException(status_code=404, detail="Chauffeur introuvable")
        rows = await conn.fetch(
            """SELECT r.review_id, r.rating, r.comment, r.created_at,
                       u.first_name AS rider_first
                  FROM ride_reviews r
                  JOIN users u ON r.rider_id = u.user_id
                 WHERE r.driver_id = $1
                 ORDER BY r.created_at DESC
                 LIMIT $2 OFFSET $3""",
            d["user_id"], limit, offset,
        )
        # Histogram (always returns 5 buckets even if some are empty)
        hist_rows = await conn.fetch(
            """SELECT rating, COUNT(*)::int AS n
                  FROM ride_reviews WHERE driver_id = $1
                 GROUP BY rating""",
            d["user_id"],
        )
    histogram = {str(s): 0 for s in (1, 2, 3, 4, 5)}
    for h in hist_rows:
        histogram[str(h["rating"])] = int(h["n"])
    return {
        "driver_id": driver_id,
        "summary": {
            "average": str(d["rating"] or "5.00"),
            "total": int(d["total_reviews"] or 0),
            "histogram": histogram,
        },
        "items": [
            {
                "review_id": r["review_id"],
                "rating": r["rating"],
                "comment": r["comment"],
                "created_at": r["created_at"].isoformat(),
                "rider_first_name": (r["rider_first"] or "").strip() or "Passager",
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }



# ══════════════════════════════════════════════════════════════════════════
#  PHASE B — AI Pricing Grid (P1.2)
# ══════════════════════════════════════════════════════════════════════════
#
# Admin proposes a tariff (manual or via Claude Sonnet 4.5), then validates
# (or rejects) it. Validated rows become the live `active` tariff for
# (country_code, vehicle_type) and feed /api/transport/estimate + /request.
# A partial unique index guarantees only one `active` row per tuple.
# ══════════════════════════════════════════════════════════════════════════
class PricingProposeRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2)
    country_name: str = Field("", max_length=80)
    currency: str = Field(..., min_length=2, max_length=8)
    vehicle_type: str = Field("standard")
    extra_context: str = Field("", max_length=300)


class PricingManualRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2)
    country_name: str = Field("", max_length=80)
    currency: str = Field(..., min_length=2, max_length=8)
    vehicle_type: str = Field("standard")
    base_fare: float = Field(..., ge=0)
    per_km: float = Field(..., ge=0)
    rationale: str = Field("", max_length=500)


class PricingRejectRequest(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/admin/pricing/ai-propose")
async def admin_pricing_ai_propose(req: PricingProposeRequest, request: Request):
    """Ask Claude Sonnet 4.5 to propose a tariff row. The result is inserted
    in `proposed` status — the admin still has to call /validate to make it
    live. Idempotency is intentional weak: every call mints a fresh proposal
    so the admin can compare multiple AI suggestions side-by-side."""
    admin = await _require_admin(request)
    if req.vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=400, detail="Type de véhicule invalide")
    cc = req.country_code.upper()[:2]
    try:
        proposal = await ai_propose_pricing(
            country_code=cc,
            country_name=req.country_name or "",
            currency=req.currency.upper(),
            vehicle_type=req.vehicle_type,
            extra_context=req.extra_context,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        pricing_id = f"pg_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """INSERT INTO pricing_grid
                  (pricing_id, country_code, country_name, currency, vehicle_type,
                   base_fare, per_km, status, source, ai_rationale, proposed_by)
                  VALUES ($1,$2,$3,$4,$5,$6,$7,'proposed','ai',$8,$9)""",
            pricing_id, cc, req.country_name or "", proposal["currency"],
            req.vehicle_type, proposal["base_fare"], proposal["per_km"],
            proposal["rationale"], admin["user_id"],
        )
        row = await conn.fetchrow("SELECT * FROM pricing_grid WHERE pricing_id = $1", pricing_id)
    return pricing_to_dict(row)


@router.post("/admin/pricing/manual")
async def admin_pricing_manual(req: PricingManualRequest, request: Request):
    """Admin manually inserts a tariff row in `proposed` status (skipping the
    AI). Useful when the admin already has a known-good tariff."""
    admin = await _require_admin(request)
    if req.vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=400, detail="Type de véhicule invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        pricing_id = f"pg_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """INSERT INTO pricing_grid
                  (pricing_id, country_code, country_name, currency, vehicle_type,
                   base_fare, per_km, status, source, ai_rationale, proposed_by)
                  VALUES ($1,$2,$3,$4,$5,$6,$7,'proposed','manual',$8,$9)""",
            pricing_id, req.country_code.upper()[:2], req.country_name or "",
            req.currency.upper(), req.vehicle_type,
            Decimal(str(req.base_fare)), Decimal(str(req.per_km)),
            req.rationale, admin["user_id"],
        )
        row = await conn.fetchrow("SELECT * FROM pricing_grid WHERE pricing_id = $1", pricing_id)
    return pricing_to_dict(row)


@router.get("/admin/pricing")
async def admin_pricing_list(
    request: Request,
    country: str = Query("", max_length=2),
    status: str = Query("", max_length=16),
    vehicle_type: str = Query("", max_length=16),
):
    """Filterable list. Empty filter = return everything (latest first)."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        clauses = []
        params = []
        if country:
            params.append(country.upper()[:2])
            clauses.append(f"country_code = ${len(params)}")
        if status:
            if status not in PRICING_STATUSES:
                raise HTTPException(status_code=400, detail="Statut invalide")
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if vehicle_type:
            if vehicle_type not in VEHICLE_TYPES:
                raise HTTPException(status_code=400, detail="Type de véhicule invalide")
            params.append(vehicle_type)
            clauses.append(f"vehicle_type = ${len(params)}")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await conn.fetch(
            f"SELECT * FROM pricing_grid {where} ORDER BY created_at DESC LIMIT 200",
            *params,
        )
        # Per-status counts for the admin dashboard sidebar
        counts_rows = await conn.fetch(
            "SELECT status, COUNT(*)::int AS n FROM pricing_grid GROUP BY status"
        )
    counts = {s: 0 for s in PRICING_STATUSES}
    for c in counts_rows:
        counts[c["status"]] = int(c["n"])
    return {
        "items": [pricing_to_dict(r) for r in rows],
        "counts": counts,
        "total": len(rows),
    }


@router.post("/admin/pricing/{pricing_id}/validate")
async def admin_pricing_validate(pricing_id: str, request: Request):
    """Promote a `proposed` row to `active` and auto-archive the previous
    active row for the same (country_code, vehicle_type), if any."""
    admin = await _require_admin(request)
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM pricing_grid WHERE pricing_id = $1 FOR UPDATE",
                pricing_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Tarif introuvable")
            if row["status"] not in (PRICING_PROPOSED,):
                raise HTTPException(
                    status_code=409,
                    detail=f"Impossible de valider depuis le statut '{row['status']}'.",
                )
            # Archive the previous active row (if any) for the same tuple.
            await conn.execute(
                """UPDATE pricing_grid SET status='archived', updated_at=$1
                       WHERE country_code=$2 AND vehicle_type=$3 AND status='active'""",
                now, row["country_code"], row["vehicle_type"],
            )
            try:
                await conn.execute(
                    """UPDATE pricing_grid
                           SET status='active', validated_by=$1, validated_at=$2, updated_at=$2
                           WHERE pricing_id=$3""",
                    admin["user_id"], now, pricing_id,
                )
            except Exception as e:
                # Concurrent admin validating another proposal for the same
                # (country, vehicle_type) — partial unique index fires here.
                # Surface as a clean 409 instead of leaking a raw 500.
                if "ux_pricing_active_per_country_vt" in str(e):
                    raise HTTPException(
                        status_code=409,
                        detail="Validation simultanée détectée. Réessayez.",
                    )
                raise
            updated = await conn.fetchrow(
                "SELECT * FROM pricing_grid WHERE pricing_id = $1", pricing_id
            )
    return pricing_to_dict(updated)


@router.post("/admin/pricing/{pricing_id}/reject")
async def admin_pricing_reject(pricing_id: str, req: PricingRejectRequest,
                               request: Request):
    """Mark a `proposed` row as `rejected`. Reason is optional but logged."""
    admin = await _require_admin(request)
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        row = await conn.fetchrow(
            "SELECT status FROM pricing_grid WHERE pricing_id = $1", pricing_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Tarif introuvable")
        if row["status"] != PRICING_PROPOSED:
            raise HTTPException(
                status_code=409,
                detail="Seuls les tarifs 'proposed' peuvent être rejetés.",
            )
        await conn.execute(
            """UPDATE pricing_grid
                   SET status='rejected', rejected_reason=$1, validated_by=$2, validated_at=$3, updated_at=$3
                   WHERE pricing_id=$4""",
            (req.reason or "")[:300], admin["user_id"], now, pricing_id,
        )
        updated = await conn.fetchrow(
            "SELECT * FROM pricing_grid WHERE pricing_id = $1", pricing_id
        )
    return pricing_to_dict(updated)


@router.get("/admin/pricing/{pricing_id}")
async def admin_pricing_detail(pricing_id: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        row = await conn.fetchrow(
            "SELECT * FROM pricing_grid WHERE pricing_id = $1", pricing_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tarif introuvable")
    return pricing_to_dict(row)


@router.post("/admin/pricing/run-ai-batch")
async def admin_pricing_run_ai_batch(request: Request,
                                     force: bool = Query(True)):
    """Manually fire the weekly AI proposal cron right now. By default
    `force=True` bypasses the iso-week dedup tracker so the admin can
    trigger a fresh batch on demand. Returns the same summary dict as
    the scheduled job."""
    await _require_admin(request)
    from services.transport_pricing_cron import run_weekly_ai_proposal_batch
    result = await run_weekly_ai_proposal_batch(force=force)
    return result


# ─────────────────────────────────────────────────────────────────────────
# 1-click bulk validate of the latest cron_weekly batch
# ─────────────────────────────────────────────────────────────────────────
async def _latest_cron_batch_proposals(conn) -> list[dict]:
    """Return the still-`proposed` rows generated by the most recent
    cron_weekly batch. We anchor on the max created_at among
    `proposed_by='cron_weekly'` and pick everything from the same UTC day
    (cron creates them in a single tight loop)."""
    anchor = await conn.fetchval(
        """SELECT MAX(created_at) FROM pricing_grid
              WHERE proposed_by = 'cron_weekly' AND status = 'proposed'"""
    )
    if anchor is None:
        return []
    rows = await conn.fetch(
        """SELECT * FROM pricing_grid
              WHERE proposed_by = 'cron_weekly' AND status = 'proposed'
                AND created_at >= $1::timestamptz - INTERVAL '6 hours'
              ORDER BY created_at ASC""",
        anchor,
    )
    return [dict(r) for r in rows]


@router.get("/admin/pricing/cron-batch/preview")
async def admin_pricing_cron_batch_preview(request: Request):
    """Preview which proposals would be validated by the 1-click button.
    Returns the list + a count, plus the iso-week of the anchor row."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        rows = await _latest_cron_batch_proposals(conn)
    items = [pricing_to_dict(r) for r in rows]
    week = ""
    if rows:
        anchor = rows[-1]["created_at"]  # latest in the batch
        iso_year, iso_week, _ = anchor.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
    return {"count": len(items), "week": week, "items": items}


class CronBatchValidateRequest(BaseModel):
    confirm: bool = False


@router.post("/admin/pricing/cron-batch/validate-all")
async def admin_pricing_cron_batch_validate_all(req: CronBatchValidateRequest,
                                                request: Request):
    """1-click: validate every still-proposed cron_weekly row of the latest
    batch. Each validated row archives any previously-active row for the
    same (country, vehicle_type), exactly like the per-row /validate
    endpoint. Returns {validated_count, skipped_count, conflicts_count,
    validated, skipped, conflicts}.

    Mandatory `confirm=true` body. Disabled (404 nothing-to-do) when no
    cron_weekly proposals exist.
    """
    admin = await _require_admin(request)
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation explicite requise (confirm=true).",
        )
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    validated: list[str] = []
    skipped: list[dict] = []
    conflicts: list[dict] = []
    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        rows = await _latest_cron_batch_proposals(conn)
        if not rows:
            raise HTTPException(
                status_code=404,
                detail="Aucune proposition cron à valider.",
            )
        # Validate sequentially inside individual transactions so a
        # conflicting row (concurrent validate from another admin) doesn't
        # roll back the whole batch.
        for r in rows:
            pid = r["pricing_id"]
            try:
                async with conn.transaction():
                    cur = await conn.fetchrow(
                        "SELECT status FROM pricing_grid WHERE pricing_id=$1 FOR UPDATE",
                        pid,
                    )
                    if not cur or cur["status"] != PRICING_PROPOSED:
                        skipped.append({"pricing_id": pid, "reason": "no_longer_proposed"})
                        continue
                    await conn.execute(
                        """UPDATE pricing_grid SET status='archived', updated_at=$1
                              WHERE country_code=$2 AND vehicle_type=$3 AND status='active'""",
                        now, r["country_code"], r["vehicle_type"],
                    )
                    await conn.execute(
                        """UPDATE pricing_grid
                              SET status='active', validated_by=$1,
                                  validated_at=$2, updated_at=$2
                              WHERE pricing_id=$3""",
                        admin["user_id"], now, pid,
                    )
                    validated.append(pid)
            except Exception as e:
                # Concurrent admin validating the same (country, vehicle_type)
                # — partial unique index fires here. Surface as a clean
                # `conflict` entry rather than a server error.
                import asyncpg as _apg
                if isinstance(e, _apg.UniqueViolationError):
                    conflicts.append({"pricing_id": pid, "reason": "concurrent_validation"})
                else:
                    logger.exception(
                        "cron_batch_validate_all: unexpected error on %s", pid,
                    )
                    conflicts.append({"pricing_id": pid, "reason": "internal_error"})

    # Audit log (best-effort, never raises).
    from routes.auth import log_admin_action
    await log_admin_action(
        actor_id=admin["user_id"],
        actor_email=admin.get("email", ""),
        action="transport.pricing.cron_batch_validate_all",
        metadata={
            "validated_count": len(validated),
            "skipped_count": len(skipped),
            "conflicts_count": len(conflicts),
            "validated_ids": validated,
        },
    )

    return {
        "validated_count": len(validated),
        "skipped_count": len(skipped),
        "conflicts_count": len(conflicts),
        "validated": validated,
        "skipped": skipped,
        "conflicts": conflicts,
    }


# ══════════════════════════════════════════════════════════════════════════
#  PHASE D — Dynamic Surge Pricing Engine (iter105)
# ══════════════════════════════════════════════════════════════════════════
class SurgeConfigUpdateRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2)
    config: dict  # Partial or full — merged with defaults on read

    @validator("config")
    def _sanity(cls, v):
        # max_surge must be >= 1.0 (floor to base fare). A 0.8 value would
        # silently cap multipliers below the base price, confusing operators.
        if "max_surge" in v:
            try:
                if float(v["max_surge"]) < 1.0:
                    raise ValueError("max_surge doit être >= 1.0")
            except (TypeError, ValueError) as e:
                raise ValueError(f"max_surge invalide: {e}")
        # Uplift values should be non-negative (we don't discount; cap below
        # at 1.0 via the clamp). Warn early instead of at runtime.
        for k in ("peak_uplift", "night_uplift", "ds_high_uplift", "ds_med_uplift",
                  "urban_uplift", "traffic_slow_uplift", "traffic_med_uplift",
                  "premium_uplift"):
            if k in v:
                try:
                    if float(v[k]) < 0:
                        raise ValueError(f"{k} doit être >= 0")
                except (TypeError, ValueError) as e:
                    raise ValueError(f"{k} invalide: {e}")
        return v


@router.get("/admin/surge/config")
async def admin_surge_get_config(country: str = Query(..., min_length=2, max_length=2),
                                 request: Request = None):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_surge_ddl(conn)
        cfg = await get_surge_config(conn, country)
        meta = await conn.fetchrow(
            "SELECT updated_at, updated_by FROM surge_config WHERE country_code = $1",
            country.upper()[:2],
        )
    return {
        "country_code": country.upper()[:2],
        "config": cfg,
        "defaults": DEFAULT_SURGE_CONFIG,
        "updated_at": meta["updated_at"].isoformat() if meta else None,
        "updated_by": meta["updated_by"] if meta else "",
    }


@router.put("/admin/surge/config")
async def admin_surge_put_config(req: SurgeConfigUpdateRequest, request: Request):
    admin = await _require_admin(request)
    if not isinstance(req.config, dict):
        raise HTTPException(status_code=400, detail="config doit être un objet JSON")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_surge_ddl(conn)
        # Merge with existing (so partial patches work) before upserting.
        current = await get_surge_config(conn, req.country_code)
        merged = {**current, **req.config}
        await upsert_surge_config(conn, req.country_code, merged, admin["user_id"])
    from routes.auth import log_admin_action
    await log_admin_action(
        actor_id=admin["user_id"],
        actor_email=admin.get("email", ""),
        action="transport.surge.config_update",
        metadata={"country_code": req.country_code.upper()[:2],
                  "keys_updated": sorted(req.config.keys())},
    )
    return {"status": "ok", "country_code": req.country_code.upper()[:2],
            "config": merged}


@router.get("/admin/surge/preview")
async def admin_surge_preview(
    request: Request,
    country: str = Query(..., min_length=2, max_length=2),
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    vehicle_type: str = Query("standard"),
):
    """Simulate the surge multiplier for a given (country, coords, vehicle)
    RIGHT NOW. Used by the admin UI to preview the live engine before
    pushing config changes."""
    await _require_admin(request)
    if vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=400, detail="Type de véhicule invalide")
    if not is_valid_coord(pickup_lat, pickup_lng):
        raise HTTPException(status_code=400, detail="Coordonnées GPS invalides")
    cell = h3_cell(pickup_lat, pickup_lng)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_surge_ddl(conn)
        surge = await compute_surge(
            conn, country_code=country,
            h3_cell=cell, vehicle_type=vehicle_type,
        )
    return {"h3_cell": cell, "country_code": country.upper()[:2], "vehicle_type": vehicle_type, **surge}


@router.get("/admin/surge/history")
async def admin_surge_history(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    country: str = Query("", max_length=2),
    min_multiplier: float = Query(1.0, ge=1.0, le=5.0),
    limit: int = Query(200, ge=1, le=1000),
):
    """Audit trail of every applied surge. Filterable by country + minimum
    multiplier (e.g. only show surged rides where x >= 1.2)."""
    await _require_admin(request)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_surge_ddl(conn)
        clauses = ["applied_at >= $1", "final_multiplier >= $2"]
        params = [since, Decimal(str(min_multiplier))]
        if country:
            params.append(country.upper()[:2])
            clauses.append(f"country_code = ${len(params)}")
        sql = (
            "SELECT id, ride_id, country_code, h3_cell, vehicle_type, applied_at, "
            "       time_band, demand_supply_ratio, urban_score, traffic_kmh, "
            "       time_factor, demand_factor, urban_factor, traffic_factor, "
            "       vehicle_factor, raw_multiplier, final_multiplier, "
            "       base_fare, final_fare "
            "FROM surge_history WHERE " + " AND ".join(clauses) +
            f" ORDER BY applied_at DESC LIMIT {limit}"
        )
        rows = await conn.fetch(sql, *params)
        # Aggregate summary in the same window
        agg = await conn.fetchrow(
            """SELECT COUNT(*)::int AS total,
                      AVG(final_multiplier)::numeric(6,3) AS avg_mult,
                      MAX(final_multiplier)::numeric(6,3) AS max_mult,
                      SUM(CASE WHEN final_multiplier > 1.0 THEN 1 ELSE 0 END)::int AS surged,
                      SUM(final_fare - base_fare)::numeric(14,2) AS extra_revenue
                 FROM surge_history
                 WHERE applied_at >= $1 {extra}""".format(
                extra=(" AND country_code = $2" if country else "")
            ),
            since, *([country.upper()[:2]] if country else []),
        )
    return {
        "days": days,
        "country_filter": country.upper()[:2] if country else "",
        "min_multiplier": min_multiplier,
        "summary": {
            "total": int(agg["total"] or 0),
            "surged": int(agg["surged"] or 0),
            "avg_multiplier": str(agg["avg_mult"] or "1.000"),
            "max_multiplier": str(agg["max_mult"] or "1.000"),
            "extra_revenue": str(agg["extra_revenue"] or "0"),
        },
        "items": [
            {
                "id": r["id"],
                "ride_id": r["ride_id"],
                "country_code": r["country_code"],
                "h3_cell": r["h3_cell"],
                "vehicle_type": r["vehicle_type"],
                "applied_at": r["applied_at"].isoformat(),
                "time_band": r["time_band"],
                "demand_supply_ratio": str(r["demand_supply_ratio"]) if r["demand_supply_ratio"] is not None else None,
                "urban_score": int(r["urban_score"] or 0),
                "traffic_kmh": str(r["traffic_kmh"]) if r["traffic_kmh"] is not None else None,
                "factors": {
                    "time": str(r["time_factor"]),
                    "demand": str(r["demand_factor"]),
                    "urban": str(r["urban_factor"]),
                    "traffic": str(r["traffic_factor"]),
                    "vehicle": str(r["vehicle_factor"]),
                },
                "raw_multiplier": str(r["raw_multiplier"]),
                "final_multiplier": str(r["final_multiplier"]),
                "base_fare": str(r["base_fare"]) if r["base_fare"] is not None else None,
                "final_fare": str(r["final_fare"]) if r["final_fare"] is not None else None,
            } for r in rows
        ],
    }



# ══════════════════════════════════════════════════════════════════════════
#  PHASE 3 — Public share link (JWT-signed, 12h TTL)
# ══════════════════════════════════════════════════════════════════════════
#
# Lets the rider share a read-only tracking URL via WhatsApp / SMS so
# friends and family can follow the ride in real-time without an account.
# The token is a short-lived JWT signed with JWT_SECRET. It encodes only
# the ride_id + an expiration; the public endpoint then refetches the
# ride and returns a SANITISED payload (no rider full name, no exact
# pickup address, no payment details).
# ══════════════════════════════════════════════════════════════════════════
_RIDE_SHARE_TTL_HOURS = 12
_RIDE_SHARE_AUD = "japap.ride.share"


def _ride_share_secret() -> str:
    s = os.environ.get("JWT_SECRET")
    if not s:
        raise HTTPException(status_code=500, detail="Server misconfigured: JWT_SECRET missing")
    return s


@router.post("/{ride_id}/share")
async def create_ride_share_link(ride_id: str, request: Request):
    """Mint a 12h signed link the rider (or assigned driver) can share."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        ride = await conn.fetchrow(
            "SELECT rider_id, driver_id, status FROM ride_requests WHERE ride_id = $1",
            ride_id,
        )
    if not ride:
        raise HTTPException(status_code=404, detail="Course introuvable")
    if ride["rider_id"] != user["user_id"] and ride["driver_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Non autorisé")
    if ride["status"] in ("completed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail="La course est déjà terminée — un partage ne servirait à rien.",
        )

    now = datetime.now(timezone.utc)
    payload = {
        "rid": ride_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=_RIDE_SHARE_TTL_HOURS)).timestamp()),
        "aud": _RIDE_SHARE_AUD,
    }
    token = _jwt.encode(payload, _ride_share_secret(), algorithm="HS256")
    # Build the public share URL. We prefer (in order):
    #  1. PUBLIC_APP_URL env (explicitly set in prod, e.g. https://japapmessenger.com)
    #  2. The Origin / Referer header of the calling client (correct on preview)
    #  3. The request's own scheme+host (Kubernetes ingress maps /track to the SPA)
    #  4. Final fallback to the prod domain.
    base = os.environ.get("PUBLIC_APP_URL")
    if not base:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            try:
                from urllib.parse import urlparse
                p = urlparse(origin)
                if p.scheme and p.netloc:
                    base = f"{p.scheme}://{p.netloc}"
            except Exception:
                base = None
    if not base:
        base = f"{request.url.scheme}://{request.url.netloc}" if request.url.netloc else "https://japapmessenger.com"
    base = base.rstrip("/")
    return {
        "token": token,
        "url": f"{base}/track/{token}",
        "expires_at": datetime.fromtimestamp(payload["exp"], tz=timezone.utc).isoformat(),
        "ttl_hours": _RIDE_SHARE_TTL_HOURS,
    }


def _public_ride_payload(d: dict) -> dict:
    """Sanitised ride view for the public tracking page.

    Hides everything that could be used to identify or scam the rider:
      • rider full name → first name only
      • exact pickup address → city/zone if available, else generic label
      • dropoff still shown (this is what the family wants to see)
      • driver name + vehicle plate ARE shown (transparency / trust)
      • payment / fare hidden (private)
    """
    rider_name = (d.get("rider_first") or "").strip() or "Passager"
    return {
        "ride_id": d["ride_id"],
        "status": d["status"],
        "vehicle_type": d.get("vehicle_type"),
        "rider_first_name": rider_name,
        "pickup_address": d.get("pickup_address") or "",
        "dropoff_address": d.get("dropoff_address") or "",
        "pickup_lat": d.get("pickup_lat"),
        "pickup_lng": d.get("pickup_lng"),
        "dropoff_lat": d.get("dropoff_lat"),
        "dropoff_lng": d.get("dropoff_lng"),
        "distance_km": str(d["distance_km"]) if d.get("distance_km") is not None else None,
        "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
        "accepted_at": d["accepted_at"].isoformat() if d.get("accepted_at") else None,
        "en_route_at": d["en_route_at"].isoformat() if d.get("en_route_at") else None,
        "started_at": d["started_at"].isoformat() if d.get("started_at") else None,
        "completed_at": d["completed_at"].isoformat() if d.get("completed_at") else None,
        "cancelled_at": d["cancelled_at"].isoformat() if d.get("cancelled_at") else None,
        "driver_position": (
            {
                "lat": d["driver_lat"], "lng": d["driver_lng"],
                "at": d["driver_position_at"].isoformat() if d.get("driver_position_at") else None,
            } if d.get("driver_lat") is not None else None
        ),
        "driver": (
            {
                "first_name": (d.get("driver_first") or "").strip(),
                "vehicle_model": d.get("vehicle_model"),
                "vehicle_plate": d.get("vehicle_plate"),
                "rating": str(d.get("rating") or "5.00"),
            } if d.get("driver_id") else None
        ),
    }


@router.get("/share/{token}")
async def public_ride_view(token: str):
    """No-auth read-only view of a shared ride. Validates the JWT and returns
    a sanitised payload. Used by /track/{token} on the frontend."""
    try:
        decoded = _jwt.decode(
            token, _ride_share_secret(),
            algorithms=["HS256"],
            audience=_RIDE_SHARE_AUD,
        )
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=410, detail="Ce lien de partage a expiré.")
    except _jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Lien de partage invalide.")
    rid = decoded.get("rid")
    if not rid:
        raise HTTPException(status_code=400, detail="Lien de partage invalide.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT r.*, u.first_name AS rider_first,
                      du.first_name AS driver_first,
                      d.vehicle_model, d.vehicle_plate, d.rating
                 FROM ride_requests r
                 JOIN users u ON r.rider_id = u.user_id
                 LEFT JOIN users du ON r.driver_id = du.user_id
                 LEFT JOIN drivers d ON r.driver_id = d.user_id
                 WHERE r.ride_id = $1""",
            rid,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Course introuvable")
    return _public_ride_payload(dict(row))
