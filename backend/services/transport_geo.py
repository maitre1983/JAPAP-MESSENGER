"""
JAPAP Transport — Geolocation helpers (iter98 Phase 2).

Light wrapper around h3-py to:
  • Validate GPS coordinates.
  • Compute the H3 cell (resolution 9 ≈ 174m edge) for any (lat, lng) point.
    Resolution 9 gives ~1.4 km² cells — perfect for ride-hailing demand
    heatmaps in mid-density city zones (Yaoundé, Douala, Abidjan...).
  • Expose a small DDL bootstrap so callers can self-heal the ride_requests
    schema with pickup_h3, dropoff_h3 columns.

Why H3:
  - Hexagonal grid avoids the corner-distortion problem of square grids.
  - Single TEXT column = trivial GROUP BY for heatmap aggregation.
  - Compatible with PostGIS later (Uber's H3 has an official PG ext).
"""
import math
import logging
from typing import Optional

import h3

logger = logging.getLogger(__name__)

H3_RESOLUTION = 9   # ~174m edge length; ~1.42 km² area per cell
EARTH_RADIUS_KM = 6371.0


def is_valid_coord(lat: Optional[float], lng: Optional[float]) -> bool:
    """Reject NaN / infinite / out-of-range coordinates."""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return False
    if math.isnan(lat_f) or math.isnan(lng_f):
        return False
    if math.isinf(lat_f) or math.isinf(lng_f):
        return False
    if not (-90.0 <= lat_f <= 90.0):
        return False
    if not (-180.0 <= lng_f <= 180.0):
        return False
    return True


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def h3_cell(lat: float, lng: float, resolution: int = H3_RESOLUTION) -> str:
    """Return the H3 cell index for (lat, lng) at the given resolution.
    Caller must validate coords first via is_valid_coord()."""
    return h3.latlng_to_cell(float(lat), float(lng), resolution)


# ── DDL bootstrap (idempotent) ──────────────────────────────────────────
_RIDE_GEO_DDL = [
    # iter98 — full schema self-heal so the existing DB matches what
    # routes/transport.py inserts. The legacy table was missing many of these.
    """ALTER TABLE ride_requests
         ADD COLUMN IF NOT EXISTS pickup_address TEXT,
         ADD COLUMN IF NOT EXISTS dropoff_address TEXT,
         ADD COLUMN IF NOT EXISTS pickup_lat DOUBLE PRECISION,
         ADD COLUMN IF NOT EXISTS pickup_lng DOUBLE PRECISION,
         ADD COLUMN IF NOT EXISTS dropoff_lat DOUBLE PRECISION,
         ADD COLUMN IF NOT EXISTS dropoff_lng DOUBLE PRECISION,
         ADD COLUMN IF NOT EXISTS distance_km NUMERIC(8,2),
         ADD COLUMN IF NOT EXISTS fare_estimated NUMERIC(12,2),
         ADD COLUMN IF NOT EXISTS notes TEXT,
         ADD COLUMN IF NOT EXISTS pickup_h3 TEXT,
         ADD COLUMN IF NOT EXISTS dropoff_h3 TEXT,
         ADD COLUMN IF NOT EXISTS en_route_at TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS driver_lat DOUBLE PRECISION,
         ADD COLUMN IF NOT EXISTS driver_lng DOUBLE PRECISION,
         ADD COLUMN IF NOT EXISTS driver_position_at TIMESTAMPTZ
    """,
    # iter98 — drop the legacy FK that pointed driver_id to drivers.driver_id
    # while the application code stored users.user_id there. We keep the column
    # name and the implicit relation through users(user_id) instead — drivers
    # already FK to users, so the chain is preserved.
    """ALTER TABLE ride_requests
         DROP CONSTRAINT IF EXISTS ride_requests_driver_id_fkey
    """,
    # Backfill fare_estimated from the older `fare_estimate` column (if it
    # exists) so analytics queries don't get NULL after the column rename.
    """UPDATE ride_requests
         SET fare_estimated = fare_estimate
         WHERE fare_estimated IS NULL AND fare_estimate IS NOT NULL
    """,
    """CREATE INDEX IF NOT EXISTS idx_ride_pickup_h3
         ON ride_requests (pickup_h3)
         WHERE pickup_h3 IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_ride_status_created
         ON ride_requests (status, created_at DESC)""",
    # iter99 — bullet-proof active-ride uniqueness. Two concurrent POST /request
    # from the same rider can race past the application-level check; this
    # partial unique index makes the second INSERT fail with 23505 (handled
    # gracefully in the route).
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_ride_requests_rider_active
         ON ride_requests (rider_id)
         WHERE status IN ('pending','accepted','en_route','started')""",
]

_ddl_done = False


async def ensure_ride_geo_ddl(conn) -> None:
    """Idempotent. Backfill h3 cells for any existing rows lacking them."""
    global _ddl_done
    if _ddl_done:
        return
    for stmt in _RIDE_GEO_DDL:
        await conn.execute(stmt)

    # One-shot backfill for legacy rows: compute h3 for any pickup/dropoff
    # coords that don't yet have a cell. Cheap because the table is small
    # at launch time. Re-runs are no-ops (WHERE clause filters them out).
    legacy = await conn.fetch(
        """SELECT ride_id, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng
             FROM ride_requests
             WHERE (pickup_h3 IS NULL AND pickup_lat IS NOT NULL)
                OR (dropoff_h3 IS NULL AND dropoff_lat IS NOT NULL)
             LIMIT 10000"""
    )
    for r in legacy:
        ph = h3_cell(r["pickup_lat"], r["pickup_lng"]) if r["pickup_lat"] is not None else None
        dh = h3_cell(r["dropoff_lat"], r["dropoff_lng"]) if r["dropoff_lat"] is not None else None
        await conn.execute(
            "UPDATE ride_requests SET pickup_h3=$1, dropoff_h3=$2 WHERE ride_id=$3",
            ph, dh, r["ride_id"],
        )
    if legacy:
        logger.info("Backfilled H3 cells for %d legacy rides", len(legacy))
    _ddl_done = True
