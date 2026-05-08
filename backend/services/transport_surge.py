"""
Transport JAPAP — Dynamic Surge Pricing Engine (Phase D / iter105).

Five-layer pricing multiplier on top of the static (base + per_km × distance)
grid:

  1. TIME BAND      — peak hours, off-peak, night safety uplift
  2. DEMAND/SUPPLY  — active-rides-vs-online-drivers ratio in the H3 cell
                       (and 1 ring of neighbours)
  3. URBAN ZONE     — historical pickup density in the cell (90-day rolling)
  4. TRAFFIC        — proxy from driver GPS pings (avg speed in cell, last 60 min)
  5. VEHICLE        — premium uplift on top of everything else

Each layer is independently togglable from `surge_config`. The final
multiplier is clamped to [1.0, max_surge_per_country]. Every applied
multiplier is logged in `surge_history` for audit + future ML training.

Storage:
  • surge_config   — one row per country (idempotent upsert)
  • surge_history  — append-only audit (one row per /request)
  • ride_requests.surge_multiplier + surge_label — applied at request time

The /estimate endpoint returns a fare RANGE (low=current, high=current×1.2)
plus a `congestion_label` ("Forte demande" or "") so the rider sees what
they're paying without exposing the exact multiplier.
"""
from __future__ import annotations
import logging
import uuid
import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# Default config (per country override possible)
DEFAULT_SURGE_CONFIG = {
    "enabled": True,
    "max_surge": 2.0,                 # absolute cap
    # Time bands (UTC hours by default — admin can shift if local zone needed)
    "time_enabled": True,
    "peak_morning_start": 7,
    "peak_morning_end": 9,
    "peak_evening_start": 17,
    "peak_evening_end": 20,
    "night_start": 22,                # 22h → 5h
    "night_end": 5,
    "peak_uplift": 0.30,              # +30 % at peak
    "night_uplift": 0.20,             # +20 % at night for driver safety
    # Demand / supply
    "demand_enabled": True,
    "ds_high_ratio": 2.0,             # >=2 rides per online driver = high demand
    "ds_high_uplift": 0.40,
    "ds_med_ratio": 1.0,              # >=1 = moderate
    "ds_med_uplift": 0.15,
    # Urban zone (pickups in cell over 90 days)
    "urban_enabled": True,
    "urban_threshold": 80,            # >=80 pickups in 90d = urban hot zone
    "urban_uplift": 0.10,
    # Traffic — proxy speed in cell over last 60 min
    "traffic_enabled": True,
    "traffic_slow_kmh": 15,           # avg speed below = congestion
    "traffic_slow_uplift": 0.20,
    "traffic_med_kmh": 25,
    "traffic_med_uplift": 0.10,
    # Vehicle uplift
    "premium_uplift": 0.10,           # +10 % final on premium
    # Display
    "label_threshold": 1.20,          # ≥1.20 → show "Forte demande"
}

CONGESTION_LABEL = "Forte demande"


async def ensure_surge_ddl(conn):
    """Idempotent self-heal of surge_config + surge_history + ride_requests cols."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS surge_config (
            country_code   VARCHAR(2) PRIMARY KEY,
            config_json    JSONB NOT NULL,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by     VARCHAR(64) NOT NULL DEFAULT ''
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS surge_history (
            id             BIGSERIAL PRIMARY KEY,
            ride_id        VARCHAR(64),
            country_code   VARCHAR(2) NOT NULL,
            h3_cell        VARCHAR(20) NOT NULL,
            vehicle_type   VARCHAR(16) NOT NULL,
            applied_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            time_band      VARCHAR(20) NOT NULL DEFAULT 'normal',
            demand_supply_ratio NUMERIC(8,3),
            urban_score    INTEGER NOT NULL DEFAULT 0,
            traffic_kmh    NUMERIC(6,2),
            time_factor    NUMERIC(5,3) NOT NULL DEFAULT 0,
            demand_factor  NUMERIC(5,3) NOT NULL DEFAULT 0,
            urban_factor   NUMERIC(5,3) NOT NULL DEFAULT 0,
            traffic_factor NUMERIC(5,3) NOT NULL DEFAULT 0,
            vehicle_factor NUMERIC(5,3) NOT NULL DEFAULT 0,
            raw_multiplier NUMERIC(6,3) NOT NULL,
            final_multiplier NUMERIC(6,3) NOT NULL,
            base_fare      NUMERIC(12,2),
            final_fare     NUMERIC(12,2)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_surge_history_country_at "
        "ON surge_history (country_code, applied_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_surge_history_cell "
        "ON surge_history (h3_cell, applied_at DESC)"
    )
    await conn.execute(
        "ALTER TABLE ride_requests ADD COLUMN IF NOT EXISTS "
        "surge_multiplier NUMERIC(5,3) NOT NULL DEFAULT 1.000"
    )
    await conn.execute(
        "ALTER TABLE ride_requests ADD COLUMN IF NOT EXISTS "
        "surge_label VARCHAR(40) NOT NULL DEFAULT ''"
    )


async def get_surge_config(conn, country_code: str) -> dict:
    """Return the active config for a country merged with the defaults.
    Missing keys fall back to DEFAULT_SURGE_CONFIG."""
    row = await conn.fetchrow(
        "SELECT config_json FROM surge_config WHERE country_code = $1",
        (country_code or "").upper()[:2],
    )
    cfg = dict(DEFAULT_SURGE_CONFIG)
    if row:
        stored = row["config_json"]
        # asyncpg may return JSONB as string OR dict depending on the codec.
        if isinstance(stored, str):
            import json as _json
            try:
                stored = _json.loads(stored)
            except Exception:
                stored = {}
        if isinstance(stored, dict):
            cfg.update(stored)
    return cfg


async def upsert_surge_config(conn, country_code: str, config: dict, admin_id: str):
    import json as _json
    cc = (country_code or "").upper()[:2]
    await conn.execute(
        """INSERT INTO surge_config (country_code, config_json, updated_by)
              VALUES ($1, $2::jsonb, $3)
              ON CONFLICT (country_code) DO UPDATE
                  SET config_json = EXCLUDED.config_json,
                      updated_at = NOW(),
                      updated_by = EXCLUDED.updated_by""",
        cc, _json.dumps(config), admin_id,
    )


# ─────────────────────────────────────────────────────────────────────────
# Per-layer factor computation
# ─────────────────────────────────────────────────────────────────────────
def _time_factor(cfg: dict, ts: datetime) -> tuple[float, str]:
    """Return (uplift, band_label) for the given UTC timestamp."""
    if not cfg.get("time_enabled", True):
        return 0.0, "normal"
    h = ts.astimezone(timezone.utc).hour
    # Night band (wraps midnight)
    n_start = int(cfg.get("night_start", 22))
    n_end = int(cfg.get("night_end", 5))
    in_night = (h >= n_start) or (h < n_end)
    if in_night:
        return float(cfg.get("night_uplift", 0.20)), "night"
    # Peak windows
    pm_s, pm_e = int(cfg.get("peak_morning_start", 7)), int(cfg.get("peak_morning_end", 9))
    pe_s, pe_e = int(cfg.get("peak_evening_start", 17)), int(cfg.get("peak_evening_end", 20))
    if pm_s <= h < pm_e:
        return float(cfg.get("peak_uplift", 0.30)), "peak_morning"
    if pe_s <= h < pe_e:
        return float(cfg.get("peak_uplift", 0.30)), "peak_evening"
    return 0.0, "normal"


async def _demand_supply_factor(conn, cfg: dict, h3_cell: str) -> tuple[float, Optional[float]]:
    """Compute demand/supply ratio in the cell + adjacent cells (1 ring)."""
    if not cfg.get("demand_enabled", True) or not h3_cell:
        return 0.0, None
    # 1 ring of neighbours via H3 grid_disk(k=1) — fall back to single cell
    # when h3 helper is unavailable.
    try:
        from services.transport_geo import h3 as _h3
        cells = list(_h3.grid_disk(h3_cell, 1)) if _h3 else [h3_cell]
    except Exception:
        cells = [h3_cell]
    if not cells:
        cells = [h3_cell]
    # Active rides in cells (last 5 min, status pending/accepted/en_route)
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    active_rides = await conn.fetchval(
        """SELECT COUNT(*)::int FROM ride_requests
              WHERE pickup_h3 = ANY($1)
                AND status IN ('pending','accepted','en_route')
                AND created_at >= $2""",
        cells, since,
    ) or 0
    # Empty city edge case: 0 demand + 0 supply = no surge (don't punish
    # a rider booking at 3am when nobody else is riding either).
    if active_rides == 0:
        return 0.0, 0.0
    # Online drivers — H3 cell of last position. Fall back to total online if
    # we don't have driver geolocation columns (we use ride_requests.driver_lat
    # snapshots from active rides — best-effort).
    online_drivers = await conn.fetchval(
        "SELECT COUNT(*)::int FROM drivers WHERE is_online=TRUE AND kyc_status='approved'"
    ) or 0
    if online_drivers == 0:
        # Some demand but no supply → max demand uplift (driver-friendly).
        return float(cfg.get("ds_high_uplift", 0.40)), float(active_rides)
    ratio = active_rides / online_drivers
    if ratio >= float(cfg.get("ds_high_ratio", 2.0)):
        return float(cfg.get("ds_high_uplift", 0.40)), ratio
    if ratio >= float(cfg.get("ds_med_ratio", 1.0)):
        return float(cfg.get("ds_med_uplift", 0.15)), ratio
    return 0.0, ratio


async def _urban_factor(conn, cfg: dict, h3_cell: str) -> tuple[float, int]:
    """Cell-level historical demand → urban-zone uplift."""
    if not cfg.get("urban_enabled", True) or not h3_cell:
        return 0.0, 0
    since = datetime.now(timezone.utc) - timedelta(days=90)
    cnt = await conn.fetchval(
        """SELECT COUNT(*)::int FROM ride_requests
              WHERE pickup_h3 = $1 AND created_at >= $2""",
        h3_cell, since,
    ) or 0
    if cnt >= int(cfg.get("urban_threshold", 80)):
        return float(cfg.get("urban_uplift", 0.10)), cnt
    return 0.0, cnt


async def _traffic_factor(conn, cfg: dict, h3_cell: str) -> tuple[float, Optional[float]]:
    """Proxy speed: average distance covered between successive driver
    position pings in the cell over the last 60 minutes. We can't easily
    compute differential speeds from the schema; a robust proxy is the
    completion-time per km on rides recently finished in the cell.
    """
    if not cfg.get("traffic_enabled", True) or not h3_cell:
        return 0.0, None
    since = datetime.now(timezone.utc) - timedelta(minutes=60)
    # Average minutes per km on completed rides starting in this cell
    row = await conn.fetchrow(
        """SELECT AVG(
                  EXTRACT(EPOCH FROM (completed_at - started_at)) / 60.0
                  / NULLIF(distance_km, 0)
              ) AS minutes_per_km,
              COUNT(*)::int AS n
              FROM ride_requests
              WHERE pickup_h3 = $1 AND status = 'completed'
                AND completed_at >= $2 AND distance_km > 0.5""",
        h3_cell, since,
    )
    if not row or not row["n"] or row["minutes_per_km"] is None:
        return 0.0, None
    minutes_per_km = float(row["minutes_per_km"])
    if minutes_per_km <= 0:
        return 0.0, None
    speed_kmh = 60.0 / minutes_per_km
    if speed_kmh <= float(cfg.get("traffic_slow_kmh", 15)):
        return float(cfg.get("traffic_slow_uplift", 0.20)), speed_kmh
    if speed_kmh <= float(cfg.get("traffic_med_kmh", 25)):
        return float(cfg.get("traffic_med_uplift", 0.10)), speed_kmh
    return 0.0, speed_kmh


def _vehicle_factor(cfg: dict, vehicle_type: str) -> float:
    if vehicle_type == "premium":
        return float(cfg.get("premium_uplift", 0.10))
    return 0.0


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────
async def compute_surge(conn, *, country_code: str, h3_cell: Optional[str],
                        vehicle_type: str, ts: Optional[datetime] = None) -> dict:
    """Return the full surge breakdown for the given (country, cell, vehicle)
    at the given timestamp. Used by /estimate (preview) and /request (apply).
    """
    cfg = await get_surge_config(conn, country_code)
    if not cfg.get("enabled", True) or not h3_cell:
        return {
            "multiplier": 1.0, "raw_multiplier": 1.0, "label": "",
            "factors": {"time": 0.0, "demand": 0.0, "urban": 0.0,
                        "traffic": 0.0, "vehicle": 0.0},
            "details": {"time_band": "disabled" if not cfg.get("enabled", True) else "normal",
                        "demand_supply_ratio": None, "urban_score": 0,
                        "traffic_kmh": None},
            "config_used": cfg,
        }
    if ts is None:
        ts = datetime.now(timezone.utc)
    time_uplift, time_band = _time_factor(cfg, ts)
    demand_uplift, ds_ratio = await _demand_supply_factor(conn, cfg, h3_cell)
    urban_uplift, urban_score = await _urban_factor(conn, cfg, h3_cell)
    traffic_uplift, traffic_speed = await _traffic_factor(conn, cfg, h3_cell)
    vehicle_uplift = _vehicle_factor(cfg, vehicle_type)
    raw = 1.0 + time_uplift + demand_uplift + urban_uplift + traffic_uplift + vehicle_uplift
    cap = float(cfg.get("max_surge", 2.0))
    final = min(max(raw, 1.0), cap)
    label = CONGESTION_LABEL if final >= float(cfg.get("label_threshold", 1.20)) else ""
    return {
        "multiplier": round(final, 3),
        "raw_multiplier": round(raw, 3),
        "label": label,
        "factors": {
            "time": round(time_uplift, 3),
            "demand": round(demand_uplift, 3),
            "urban": round(urban_uplift, 3),
            "traffic": round(traffic_uplift, 3),
            "vehicle": round(vehicle_uplift, 3),
        },
        "details": {
            "time_band": time_band,
            "demand_supply_ratio": ds_ratio,
            "urban_score": urban_score,
            "traffic_kmh": traffic_speed,
        },
        "config_used": {k: cfg[k] for k in cfg if k in ("enabled",) or k.startswith(
            ("max_", "peak_", "night_", "ds_", "urban_", "traffic_", "premium_", "label_"))},
    }


async def log_surge_application(conn, *, ride_id: str, country_code: str,
                                h3_cell: str, vehicle_type: str,
                                surge: dict, base_fare: Decimal,
                                final_fare: Decimal):
    """Append a surge_history row. Best-effort — never raises."""
    try:
        f = surge["factors"]
        d = surge["details"]
        await conn.execute(
            """INSERT INTO surge_history
                  (ride_id, country_code, h3_cell, vehicle_type, time_band,
                   demand_supply_ratio, urban_score, traffic_kmh,
                   time_factor, demand_factor, urban_factor, traffic_factor,
                   vehicle_factor, raw_multiplier, final_multiplier,
                   base_fare, final_fare)
                  VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)""",
            ride_id, country_code, h3_cell, vehicle_type, d["time_band"],
            (Decimal(str(d["demand_supply_ratio"])) if d["demand_supply_ratio"] is not None else None),
            int(d["urban_score"] or 0),
            (Decimal(str(d["traffic_kmh"])) if d["traffic_kmh"] is not None else None),
            Decimal(str(f["time"])), Decimal(str(f["demand"])),
            Decimal(str(f["urban"])), Decimal(str(f["traffic"])),
            Decimal(str(f["vehicle"])),
            Decimal(str(surge["raw_multiplier"])),
            Decimal(str(surge["multiplier"])),
            base_fare, final_fare,
        )
    except Exception as e:
        logger.warning("surge_history insert failed: %s", e)


__all__ = [
    "ensure_surge_ddl", "get_surge_config", "upsert_surge_config",
    "compute_surge", "log_surge_application",
    "DEFAULT_SURGE_CONFIG", "CONGESTION_LABEL",
]
