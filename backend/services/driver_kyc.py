"""
JAPAP — Transport Driver KYC service.

Manages the strict driver registration flow (iter97 Phase 1):

    User → submits KYC form (personal info + license + ID + selfie)
         → backend stores driver row with status='pending_review'
         → admin reviews documents → approves / rejects / suspends
         → only 'approved' drivers can go online and accept rides.

This module ONLY owns the KYC schema + state transitions. The ride flow
itself stays in routes/transport.py for backward compat — but every
write-action (online, accept, complete) MUST call `require_approved_driver()`
defined here before mutating state.
"""
import logging

logger = logging.getLogger(__name__)

# Allowed KYC states (machine-readable). Mirrors the human-readable French
# labels used in admin UI: pending=Examen, approved=Validé, rejected=Refusé,
# suspended=Suspendu.
DRIVER_KYC_PENDING = "pending_review"
DRIVER_KYC_APPROVED = "approved"
DRIVER_KYC_REJECTED = "rejected"
DRIVER_KYC_SUSPENDED = "suspended"

DRIVER_KYC_STATES = {
    DRIVER_KYC_PENDING, DRIVER_KYC_APPROVED, DRIVER_KYC_REJECTED, DRIVER_KYC_SUSPENDED,
}


_DRIVER_KYC_DDL = [
    # iter97 — additional KYC columns. Idempotent ALTER so existing rows stay.
    """ALTER TABLE drivers
         ADD COLUMN IF NOT EXISTS personal_phone TEXT,
         ADD COLUMN IF NOT EXISTS license_number TEXT,
         ADD COLUMN IF NOT EXISTS license_issue_date DATE,
         ADD COLUMN IF NOT EXISTS license_image_url TEXT,
         ADD COLUMN IF NOT EXISTS id_card_image_url TEXT,
         ADD COLUMN IF NOT EXISTS selfie_with_license_url TEXT,
         ADD COLUMN IF NOT EXISTS country_code TEXT,
         ADD COLUMN IF NOT EXISTS kyc_status TEXT DEFAULT 'pending_review',
         ADD COLUMN IF NOT EXISTS kyc_submitted_at TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS kyc_reviewed_at TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS kyc_reviewed_by TEXT,
         ADD COLUMN IF NOT EXISTS kyc_rejection_reason TEXT
    """,
    # iter97 — schema-drift fix: ride_requests.vehicle_type was referenced by
    # /api/transport/available but the column was never added when the field
    # was first introduced. Self-heal here so the bootstrap is idempotent.
    """ALTER TABLE ride_requests
         ADD COLUMN IF NOT EXISTS vehicle_type TEXT DEFAULT 'standard'
    """,
    """CREATE TABLE IF NOT EXISTS driver_kyc_decisions (
         id BIGSERIAL PRIMARY KEY,
         driver_id TEXT NOT NULL,
         user_id TEXT NOT NULL,
         decision TEXT NOT NULL,
         reason TEXT,
         decided_by TEXT NOT NULL,
         decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    """CREATE INDEX IF NOT EXISTS idx_drivers_kyc_status
         ON drivers (kyc_status)""",
    """CREATE INDEX IF NOT EXISTS idx_driver_kyc_decisions_driver
         ON driver_kyc_decisions (driver_id, decided_at DESC)""",
]

_ddl_done = False


async def ensure_driver_kyc_ddl(conn) -> None:
    """Idempotent DDL bootstrap. Called once per process from the first
    /api/transport/driver/* endpoint hit. Safe to call concurrently."""
    global _ddl_done
    if _ddl_done:
        return
    for stmt in _DRIVER_KYC_DDL:
        await conn.execute(stmt)
    _ddl_done = True


def driver_to_public(drv: dict) -> dict:
    """Strip raw DB columns into a JSON-safe payload for the user-facing
    /driver/me response. Sensitive image URLs are kept (the driver owns them)."""
    if not drv:
        return {"is_driver": False}
    return {
        "is_driver": True,
        "driver_id": drv.get("driver_id"),
        "kyc_status": drv.get("kyc_status") or "pending_review",
        "kyc_rejection_reason": drv.get("kyc_rejection_reason") or "",
        "kyc_submitted_at": drv["kyc_submitted_at"].isoformat() if drv.get("kyc_submitted_at") else None,
        "kyc_reviewed_at": drv["kyc_reviewed_at"].isoformat() if drv.get("kyc_reviewed_at") else None,
        "vehicle_model": drv.get("vehicle_model") or "",
        "vehicle_plate": drv.get("vehicle_plate") or "",
        "vehicle_type": drv.get("vehicle_type") or "standard",
        "personal_phone": drv.get("personal_phone") or "",
        "emergency_contact_phone": drv.get("emergency_contact_phone") or "",
        "emergency_contact_name": drv.get("emergency_contact_name") or "",
        "license_number": drv.get("license_number") or "",
        "license_issue_date": (
            drv["license_issue_date"].isoformat() if drv.get("license_issue_date") else None
        ),
        "license_image_url": drv.get("license_image_url") or "",
        "id_card_image_url": drv.get("id_card_image_url") or "",
        "selfie_with_license_url": drv.get("selfie_with_license_url") or "",
        "country_code": drv.get("country_code") or "",
        "is_online": bool(drv.get("is_online")),
        "rating": str(drv.get("rating") or "5.00"),
        "total_rides": int(drv.get("total_rides") or 0),
        "status": drv.get("status") or "active",
    }
