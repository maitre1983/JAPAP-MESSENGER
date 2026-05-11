"""
iter239b — Hubtel MoMo credentials bootstrap (STRICTLY ADDITIVE).

Copies the four Hubtel MoMo credentials from environment variables to the
`admin_settings` table on boot, **only when the DB row is empty**. This
gives admins a working default on first deploy without leaking the .env
into the source-of-truth (which remains `admin_settings`).

Idempotent: re-runs are silent no-ops once the keys are populated.
Logs are intentionally minimal (level=info) so the boot output stays
clean — and credentials are NEVER printed.
"""
from __future__ import annotations

import logging
import os

from services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)

# (env var name, admin_settings key)
_PAIRS = [
    ("HUBTEL_API_ID",              "hubtel_api_id"),
    ("HUBTEL_API_KEY",             "hubtel_api_key"),
    ("HUBTEL_COLLECTION_ACCOUNT",  "hubtel_collection_account"),
    ("HUBTEL_DISBURSEMENT_ACCOUNT","hubtel_disbursement_account"),
    ("HUBTEL_CALLBACK_BASE_URL",   "hubtel_callback_base_url"),
]


async def init_hubtel_settings() -> dict:
    """Seed missing admin_settings rows from env. Returns a small dict
    describing which keys were copied vs left as-is, useful for diagnostics
    but never includes the actual values."""
    seeded: list[str] = []
    kept:   list[str] = []
    for env_var, db_key in _PAIRS:
        current = await get_setting(db_key)
        if current and current.strip():
            kept.append(db_key)
            continue
        env_val = os.environ.get(env_var, "").strip()
        if not env_val:
            continue
        await set_setting(db_key, env_val)
        seeded.append(db_key)
    if seeded:
        logger.info("[hubtel-bootstrap] seeded admin_settings keys from env: %s",
                    ", ".join(seeded))
    return {"seeded": seeded, "kept": kept}


__all__ = ["init_hubtel_settings"]
