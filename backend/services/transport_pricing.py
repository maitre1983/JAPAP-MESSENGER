"""
Transport JAPAP — Pricing Grid (Phase B / iter102).

Per-country, per-currency, per-vehicle-type tariff rows. Each row can be in
one of four states:

  • proposed  : freshly minted (e.g. by Claude Sonnet 4.5) — needs admin review
  • active    : the live rate applied by /api/transport/estimate + /request
  • archived  : was active, replaced by a newer validated row
  • rejected  : admin manually discarded; kept for audit

Only one row may be `active` per (country_code, vehicle_type) tuple — enforced
by a partial unique index. When a new proposal is validated, the previously
active row for the same tuple is auto-archived inside the same transaction.
"""
import os
import uuid
import json
import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


PRICING_PROPOSED = "proposed"
PRICING_ACTIVE = "active"
PRICING_ARCHIVED = "archived"
PRICING_REJECTED = "rejected"
PRICING_STATUSES = (PRICING_PROPOSED, PRICING_ACTIVE, PRICING_ARCHIVED, PRICING_REJECTED)
__all__ = [
    "PRICING_PROPOSED", "PRICING_ACTIVE", "PRICING_ARCHIVED", "PRICING_REJECTED",
    "PRICING_STATUSES",
    "ensure_pricing_ddl", "get_active_grid", "ai_propose_pricing", "pricing_to_dict",
]

VEHICLE_TYPES = ("standard", "premium")


async def ensure_pricing_ddl(conn):
    """Idempotent self-heal DDL for pricing_grid."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pricing_grid (
            pricing_id     VARCHAR(64) PRIMARY KEY,
            country_code   VARCHAR(2)  NOT NULL,
            country_name   VARCHAR(80) NOT NULL DEFAULT '',
            currency       VARCHAR(8)  NOT NULL,
            vehicle_type   VARCHAR(16) NOT NULL,
            base_fare      NUMERIC(12,2) NOT NULL,
            per_km         NUMERIC(12,2) NOT NULL,
            status         VARCHAR(16) NOT NULL DEFAULT 'proposed',
            source         VARCHAR(16) NOT NULL DEFAULT 'manual',
            ai_rationale   TEXT NOT NULL DEFAULT '',
            proposed_by    VARCHAR(64) NOT NULL DEFAULT '',
            validated_by   VARCHAR(64),
            validated_at   TIMESTAMPTZ,
            rejected_reason TEXT NOT NULL DEFAULT '',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (status IN ('proposed','active','archived','rejected')),
            CHECK (vehicle_type IN ('standard','premium')),
            CHECK (base_fare >= 0 AND per_km >= 0)
        )
    """)
    # Only one active row per (country, vehicle_type)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_pricing_active_per_country_vt
            ON pricing_grid (country_code, vehicle_type)
            WHERE status = 'active'
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pricing_country_status "
        "ON pricing_grid (country_code, status, created_at DESC)"
    )


def pricing_to_dict(r) -> dict:
    """asyncpg row → JSON-safe dict."""
    if r is None:
        return None
    d = dict(r)
    return {
        "pricing_id": d["pricing_id"],
        "country_code": d["country_code"],
        "country_name": d.get("country_name", ""),
        "currency": d["currency"],
        "vehicle_type": d["vehicle_type"],
        "base_fare": str(d["base_fare"]),
        "per_km": str(d["per_km"]),
        "status": d["status"],
        "source": d.get("source", "manual"),
        "ai_rationale": d.get("ai_rationale", ""),
        "proposed_by": d.get("proposed_by", ""),
        "validated_by": d.get("validated_by") or "",
        "validated_at": d["validated_at"].isoformat() if d.get("validated_at") else None,
        "rejected_reason": d.get("rejected_reason", ""),
        "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
        "updated_at": d["updated_at"].isoformat() if d.get("updated_at") else None,
    }


async def get_active_grid(conn, country_code: str, vehicle_type: str) -> Optional[dict]:
    """Return the live tariff row for (country_code, vehicle_type), or None
    if none has been validated yet (in which case the caller should fall back
    to its own hard-coded defaults — XAF base for backward compat).
    """
    if not country_code or not vehicle_type:
        return None
    row = await conn.fetchrow(
        """SELECT * FROM pricing_grid
             WHERE country_code = $1 AND vehicle_type = $2 AND status = 'active'
             LIMIT 1""",
        country_code.upper()[:2], vehicle_type,
    )
    return pricing_to_dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────
# AI proposal generator (Claude Sonnet 4.5 via Emergent LLM key)
# ─────────────────────────────────────────────────────────────────────────
_AI_SYSTEM_PROMPT = """Tu es un économiste spécialisé dans la tarification du transport urbain en Afrique et dans le monde. Pour chaque demande, tu proposes une grille tarifaire RÉALISTE et JUSTE pour un service de taxi à la demande type Uber/Bolt/Yango.

CONTRAINTES STRICTES :
1. Ta réponse DOIT être un JSON valide, sans aucun texte avant ou après. Format exact :
   {
     "base_fare": <number>,
     "per_km": <number>,
     "currency": "<code ISO 4217>",
     "rationale": "<3-5 phrases en français expliquant les choix>"
   }
2. Les montants sont exprimés en UNITÉS de la devise locale (pas en centimes).
3. Tiens compte du coût réel de la vie locale, prix de l'essence, salaire moyen, présence de concurrents.
4. Pour le segment 'premium', applique une majoration de 50-80% par rapport au standard.
5. Inclus une commission JAPAP de 15% déjà absorbée par la grille (le chauffeur garde ~85%).
6. Les valeurs doivent être ARRONDIES à un palier psychologiquement acceptable (ex: 500 XAF, 5 EUR, 1000 NGN).

Exemples valides :
  Cameroun (XAF) standard : base_fare=500, per_km=200
  Cameroun (XAF) premium  : base_fare=800, per_km=350
  France (EUR) standard   : base_fare=4, per_km=1.5
  Nigeria (NGN) standard  : base_fare=800, per_km=250
"""


async def ai_propose_pricing(country_code: str, country_name: str,
                             currency: str, vehicle_type: str,
                             extra_context: str = "") -> dict:
    """Call Claude Sonnet 4.5 (via emergentintegrations) to propose a tariff
    row for the given (country, currency, vehicle_type). Returns a dict
    {base_fare, per_km, currency, rationale} on success.

    Raises RuntimeError if the LLM is unavailable or produced an invalid
    response (caller turns this into HTTP 503/502).
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY non configurée — IA indisponible.")

    from emergentintegrations.llm.chat import LlmChat, UserMessage  # lazy import
    user_prompt = (
        f"Pays : {country_name or country_code} (code {country_code})\n"
        f"Devise : {currency}\n"
        f"Type de véhicule : {vehicle_type}\n"
    )
    if extra_context:
        user_prompt += f"Contexte additionnel : {extra_context}\n"
    user_prompt += "\nPropose la grille tarifaire (JSON uniquement)."

    session_id = f"pricing_{uuid.uuid4().hex[:8]}"
    chat = LlmChat(
        api_key=api_key,
        session_id=session_id,
        system_message=_AI_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    raw = await chat.send_message(UserMessage(text=user_prompt))
    text = (raw or "").strip()
    # Strip markdown fences if Claude wrapped them despite instructions.
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        # Remove a leading "json" language tag without using lstrip("json")
        # which would also eat any string starting with j/s/o/n.
        text = text.removeprefix("json").lstrip()
    if text.startswith("```"):
        text = text.strip("`").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("ai_propose_pricing: invalid JSON from Claude: %s", text[:300])
        raise RuntimeError(f"L'IA a retourné un JSON invalide: {e}")

    base = data.get("base_fare")
    per_km = data.get("per_km")
    if not isinstance(base, (int, float)) or not isinstance(per_km, (int, float)):
        raise RuntimeError("Champs base_fare/per_km manquants ou non numériques")
    if base < 0 or per_km < 0:
        raise RuntimeError("Valeurs négatives interdites")
    return {
        "base_fare": Decimal(str(base)),
        "per_km": Decimal(str(per_km)),
        "currency": (data.get("currency") or currency or "XAF").upper()[:8],
        "rationale": str(data.get("rationale") or "").strip(),
    }
