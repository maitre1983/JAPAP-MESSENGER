"""
Transport pricing — weekly AI proposal cron.

Once per ISO week (Monday UTC), iterate over every active (country,
vehicle_type) tuple and ask Claude Sonnet 4.5 for an updated tariff
proposal. Each fresh proposal is inserted into pricing_grid with
status='proposed' so the admin can validate or reject it from the
existing Tarification panel.

Disabled by default. Enable via `pricing_ai_weekly_enabled = TRUE` in
admin_settings. Tracked in `pricing_ai_last_run_iso_week` to avoid
double-runs (format: 'YYYY-Www', e.g. '2026-W17').
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


def _iso_week_key(dt: datetime) -> str:
    """Return 'YYYY-Www' (e.g. '2026-W17') for the given UTC datetime."""
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


WEEKLY_CONTEXT_TEMPLATE = (
    "Mise à jour hebdomadaire — semaine ISO {iso_week}. "
    "Tiens compte de la dynamique économique récente : "
    "inflation, prix de l'essence à la pompe, fluctuation des devises. "
    "Si rien n'a changé significativement, conserve la grille précédente."
)


async def run_weekly_ai_proposal_batch(force: bool = False) -> dict:
    """Execute the weekly batch. Returns a summary dict.

    Args:
      force: When True, bypass the iso-week dedup tracker. Used by the
             manual admin trigger /admin/pricing/run-ai-batch.
    """
    from database import get_pool
    from services.settings_service import get_bool, get_setting, set_setting
    from services.transport_pricing import (
        ensure_pricing_ddl, ai_propose_pricing,
    )
    from services.admin_alerts import raise_alert

    pool = await get_pool()
    if not force:
        enabled = await get_bool("pricing_ai_weekly_enabled", False)
        if not enabled:
            return {"skipped": True, "reason": "disabled"}

    now = datetime.now(timezone.utc)
    week_key = _iso_week_key(now)
    if not force:
        last = await get_setting("pricing_ai_last_run_iso_week", "")
        if last == week_key:
            return {"skipped": True, "reason": "already_ran_this_week", "week": week_key}

    proposals_created = 0
    failures: list[dict] = []
    targets: list[dict] = []

    async with pool.acquire() as conn:
        await ensure_pricing_ddl(conn)
        # Find every distinct (country, vehicle_type) currently active.
        # If no row is active for a given pair the batch skips it — admins
        # can always seed from the manual UI first.
        rows = await conn.fetch(
            """SELECT DISTINCT ON (country_code, vehicle_type)
                       country_code, country_name, currency, vehicle_type
                  FROM pricing_grid
                 WHERE status = 'active'
                 ORDER BY country_code, vehicle_type, created_at DESC"""
        )
        targets = [dict(r) for r in rows]

    if not targets:
        # Don't stamp the iso-week tracker when there's nothing to do —
        # otherwise an admin who seeds the very first grid mid-week would
        # have to wait until next Monday for the cron to pick it up.
        return {
            "skipped": True, "reason": "no_active_grid",
            "week": week_key, "proposals_created": 0,
        }

    for t in targets:
        try:
            ctx = WEEKLY_CONTEXT_TEMPLATE.format(iso_week=week_key)
            proposal = await ai_propose_pricing(
                country_code=t["country_code"],
                country_name=t["country_name"] or "",
                currency=t["currency"],
                vehicle_type=t["vehicle_type"],
                extra_context=ctx,
            )
            pricing_id = f"pg_{uuid.uuid4().hex[:12]}"
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO pricing_grid
                          (pricing_id, country_code, country_name, currency, vehicle_type,
                           base_fare, per_km, status, source, ai_rationale, proposed_by)
                          VALUES ($1,$2,$3,$4,$5,$6,$7,'proposed','ai',$8,'cron_weekly')""",
                    pricing_id, t["country_code"], t["country_name"] or "",
                    proposal["currency"], t["vehicle_type"],
                    Decimal(str(proposal["base_fare"])),
                    Decimal(str(proposal["per_km"])),
                    proposal["rationale"],
                )
            proposals_created += 1
        except Exception as e:
            logger.warning(
                "pricing_weekly: proposal failed for %s/%s: %s",
                t["country_code"], t["vehicle_type"], e,
            )
            failures.append({
                "country": t["country_code"], "vehicle": t["vehicle_type"],
                "error": str(e)[:200],
            })

    if not force:
        await set_setting("pricing_ai_last_run_iso_week", week_key)

    # Notify admins (fire-and-forget). Idempotent within the iso-week.
    if proposals_created > 0:
        await raise_alert(
            kind="pricing.weekly_batch",
            title=f"{proposals_created} nouveaux tarifs proposés cette semaine",
            body=(
                f"Claude a généré {proposals_created} mise(s) à jour de tarifs "
                f"pour la semaine {week_key}. "
                + (f"{len(failures)} échec(s)." if failures else "")
                + " À valider dans Admin → Transport → Tarification."
            ),
            url="/admin",
            alert_key=f"pricing.weekly_batch:{week_key}",
            window_minutes=60 * 24 * 7,  # 1 week dedup
        )
    elif failures:
        # Every proposal failed → surface silent breakage to Ops.
        await raise_alert(
            kind="pricing.weekly_batch_failure",
            title="Échec du batch tarifaire hebdomadaire",
            body=(
                f"Aucun tarif proposé pour la semaine {week_key}. "
                f"{len(failures)} erreur(s) IA (vérifiez EMERGENT_LLM_KEY + quota Claude)."
            ),
            url="/admin",
            alert_key=f"pricing.weekly_batch_failure:{week_key}",
            window_minutes=60 * 24 * 7,
        )

    logger.info(
        "pricing_weekly: week=%s targets=%d created=%d failures=%d force=%s",
        week_key, len(targets), proposals_created, len(failures), force,
    )
    return {
        "skipped": False,
        "week": week_key,
        "targets": len(targets),
        "proposals_created": proposals_created,
        "failures": failures,
        "force": force,
    }
