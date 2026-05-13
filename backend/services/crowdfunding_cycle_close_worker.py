"""
JAPAP — Crowdfunding cycle auto-close worker (iter239w)
=======================================================

Vérifie périodiquement les cycles `active` dont `ended_at < NOW()` et les
clôture en désignant le projet avec le plus de votes (parmi ceux ayant
atteint `minimum_votes_required`/`votes_to_win`) comme gagnant, OU en
les marquant `completed` sans gagnant si personne n'a atteint le minimum.

Conforme au CEO :
  • Pas de victoire avant `ended_at`
  • Tout configurable depuis l'admin (intervalle via env, défaut 60s)
  • Best-effort : exceptions catchées, jamais ne crash le scheduler
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Intervalle entre deux passes (configurable sans redéploiement via env).
_INTERVAL_SECONDS = int(os.environ.get("CROWDFUNDING_CYCLE_CLOSE_INTERVAL", "60"))


async def _scan_and_close_once() -> int:
    """Scanne les cycles expirés et les clôture un par un.
    Renvoie le nombre de cycles clôturés sur cette passe."""
    from database import get_pool
    from routes.crowdfunding import close_cycle_and_determine_winner

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT cycle_id FROM crowdfunding_cycles
                WHERE status = 'active'
                  AND ended_at IS NOT NULL
                  AND ended_at < NOW()"""
        )

    if not rows:
        return 0

    closed = 0
    for r in rows:
        cycle_id = r["cycle_id"]
        try:
            result = await close_cycle_and_determine_winner(cycle_id)
            logger.info(f"[cf_cycle_close] cycle={cycle_id} result={result.get('result')}")
            closed += 1
        except Exception as e:
            logger.error(f"[cf_cycle_close] cycle={cycle_id} failed: {e}", exc_info=True)
    return closed


async def _loop():
    """Boucle infinie — démarrée via server.py asyncio.create_task()."""
    logger.info(f"[cf_cycle_close] worker started (interval={_INTERVAL_SECONDS}s)")
    # Petit décalage initial pour laisser le DB warmup + autres workers démarrer.
    await asyncio.sleep(15)
    while True:
        try:
            n = await _scan_and_close_once()
            if n:
                logger.warning(f"[cf_cycle_close] {n} cycle(s) auto-closed at {datetime.now(timezone.utc).isoformat()}")
        except Exception as e:
            logger.error(f"[cf_cycle_close] scan failed: {e}", exc_info=True)
        await asyncio.sleep(_INTERVAL_SECONDS)
