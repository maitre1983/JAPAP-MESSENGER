"""
Quiz AI Generator — iter130, Phase 3.E.
========================================
Generates fresh quiz questions via Claude Sonnet 4.5 (Emergent LLM Key)
respecting the admin-configured distribution. Inserts directly into
`quiz_questions` so the dynamic picker can serve them on next /start.

Categories use the bucket memberships from `quiz_question_picker.py`:
  - africa  → afrique_monde
  - sport   → sport
  - econ    → economie | crypto | actualite (round-robin)
  - world   → culture_generale

Distribution defaults: 50/20/15/15 (Africa/Sport/Econ/World).
Admin config: quiz_dist_*_pct in admin_settings.
"""
from __future__ import annotations
import json
import logging
import os
import random
from typing import Any, List

logger = logging.getLogger(__name__)

# Map our 4 buckets → primary category to insert under.
BUCKET_TO_CATEGORY = {
    "africa": "afrique_monde",
    "sport":  "sport",
    "econ":   "economie",   # rotates with crypto/actualite below
    "world":  "culture_generale",
}

ECON_ROTATION = ["economie", "crypto", "actualite"]

CATEGORY_DESCRIPTIONS = {
    "afrique_monde": (
        "Afrique et monde : pays africains, capitales, drapeaux, dirigeants, "
        "personnalités, histoire, géographie, institutions (UA, CEDEAO, CEMAC), "
        "musique, sport africain. Privilégie le savoir factuel récent."
    ),
    "sport": (
        "sport mondial et africain : football (CAN, Mondial, joueurs), "
        "basket, athlétisme, palmarès, records, clubs phares."
    ),
    "economie": (
        "économie mondiale et africaine : PIB, monnaies (XAF, XOF, NGN), "
        "matières premières, entreprises, finances publiques, ZLECAf."
    ),
    "crypto": (
        "cryptomonnaies, blockchain, DeFi, Web3 : Bitcoin, Ethereum, Solana, "
        "wallets, halving, staking, NFT — explique simplement."
    ),
    "actualite": (
        "actualité mondiale et africaine récente — événements marquants, "
        "élections, conflits, sciences, climat. Évite le très volatile."
    ),
    "culture_generale": (
        "culture générale : histoire, géographie mondiale, sciences, arts, "
        "littérature, cinéma, technologie. Niveau adulte cultivé."
    ),
}

_PROMPT_TPL = """Génère un tableau JSON de {n} questions à choix multiples en français.
Catégorie : {desc}

Format STRICT — tableau JSON uniquement, pas de texte avant ou après :
[
  {{
    "text": "La question claire en français, ≤140 caractères",
    "options": ["option A", "option B", "option C", "option D"],
    "correct_index": 0,
    "difficulty": "easy" | "medium" | "hard"
  }},
  ...
]

Contraintes :
- 4 options exactement, fait-vérifiables
- correct_index entre 0 et 3
- Pas de questions piégeuses ou ambiguës
- Orthographe française correcte
- Varie les difficultés (60% medium, 25% easy, 15% hard)
- Pas de duplications
- Contenu adapté à un public adulte africain francophone
"""


def _bucket_plan(cfg: dict, total: int) -> dict[str, int]:
    """Translate admin pcts → integer question counts per bucket. The last
    bucket absorbs any rounding remainder so sum == total."""
    pcts = [
        ("africa", int(cfg.get("quiz_dist_africa_pct", 50))),
        ("sport",  int(cfg.get("quiz_dist_sport_pct",  20))),
        ("econ",   int(cfg.get("quiz_dist_econ_pct",   15))),
        ("world",  int(cfg.get("quiz_dist_world_pct",  15))),
    ]
    out: dict[str, int] = {}
    accum = 0
    for i, (name, pct) in enumerate(pcts):
        if i == len(pcts) - 1:
            out[name] = max(0, total - accum)
        else:
            n = round((pct / 100.0) * total)
            out[name] = max(0, n)
            accum += n
    return out


async def _llm_generate(category: str, n: int, model: str = "claude-sonnet-4-5-20250929") -> List[dict]:
    """One LLM call → list of validated questions for the category."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY missing in env")
    desc = CATEGORY_DESCRIPTIONS.get(category, category)
    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"quiz-gen-{category}-{random.randint(1000, 99999)}",
            system_message=(
                "Tu es un expert en création de quiz français pour un public "
                "africain. Retourne STRICTEMENT un tableau JSON, aucun autre texte."
            ),
        ).with_model("anthropic", model)
    )
    raw = await chat.send_message(UserMessage(text=_PROMPT_TPL.format(n=n, desc=desc)))
    raw = (raw or "").strip()
    i, j = raw.find("["), raw.rfind("]")
    if i == -1 or j == -1:
        raise ValueError(f"No JSON array in response for {category}")
    try:
        parsed = json.loads(raw[i:j + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for {category}: {e}") from e
    cleaned: List[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        options = item.get("options")
        correct = item.get("correct_index")
        difficulty = item.get("difficulty", "medium")
        if (
            not text
            or not isinstance(options, list) or len(options) != 4
            or not isinstance(correct, int) or not 0 <= correct <= 3
            or difficulty not in ("easy", "medium", "hard")
        ):
            continue
        cleaned.append({
            "text": text[:500],
            "options": [str(o).strip()[:200] for o in options],
            "correct_index": correct,
            "difficulty": difficulty,
            "category": category,
        })
    logger.info("[quiz-gen] %s : %d/%d validées", category, len(cleaned), len(parsed))
    return cleaned


async def generate_with_distribution(conn, total: int = 100) -> dict:
    """Generate `total` questions distributed by admin config and INSERT
    them directly. Returns a summary {by_category, inserted, skipped}.

    iter232 — Each generated batch passes through Claude validator
    (services.quiz_ai_validator.validate_questions). Questions with
    confidence < CONFIDENCE_MIN are dropped and counted in `rejected`.

    Caller may run this in a transaction or not — each insert is atomic.
    """
    from services.games_settings import get_quiz_config
    from services.quiz_ai_validator import (
        validate_questions, split_accepted, record_validation_stats,
    )
    cfg = await get_quiz_config()
    plan = _bucket_plan(cfg, total)

    inserted_total = 0
    skipped_total = 0
    rejected_total = 0  # iter232 — questions dropped by validator
    by_category: dict[str, int] = {}

    async def _gen_validate_insert(category: str, n: int) -> None:
        nonlocal inserted_total, skipped_total, rejected_total
        try:
            qs = await _llm_generate(category, n)
        except Exception as e:
            logger.warning("[quiz-gen] %s failed: %s", category, e)
            skipped_total += n
            return
        if not qs:
            skipped_total += n
            return
        # iter232 — validation pass.
        await validate_questions(qs)
        accepted, rejected = split_accepted(qs)
        await record_validation_stats(
            conn, category=category, total_generated=len(qs),
            accepted=accepted, rejected=rejected,
        )
        ins = await _insert(conn, accepted)
        by_category[category] = by_category.get(category, 0) + ins
        inserted_total += ins
        rejected_total += len(rejected)
        skipped_total += max(0, n - ins - len(rejected))

    # Africa & World single-category buckets.
    for bucket in ("africa", "world"):
        n = plan.get(bucket, 0)
        if n > 0:
            await _gen_validate_insert(BUCKET_TO_CATEGORY[bucket], n)

    # Sport bucket.
    n_sport = plan.get("sport", 0)
    if n_sport > 0:
        await _gen_validate_insert("sport", n_sport)

    # Econ bucket → split across 3 sub-categories proportionally.
    n_econ = plan.get("econ", 0)
    if n_econ > 0:
        share = n_econ // len(ECON_ROTATION)
        rem = n_econ - share * len(ECON_ROTATION)
        for i, sub in enumerate(ECON_ROTATION):
            count = share + (1 if i < rem else 0)
            if count > 0:
                await _gen_validate_insert(sub, count)

    return {
        "requested_total": total,
        "plan": plan,
        "by_category": by_category,
        "inserted": inserted_total,
        "skipped": skipped_total,
        "rejected": rejected_total,  # iter232
    }


async def _insert(conn, questions: List[dict]) -> int:
    """Insert questions into quiz_questions, returns number inserted.
    iter232 — Each question must carry `_validation`={confidence,notes}.
    The fields are persisted alongside the question for audit; callers
    are expected to have already filtered low-confidence rows out via
    services.quiz_ai_validator.split_accepted()."""
    n = 0
    for q in questions:
        try:
            v = q.get("_validation") or {}
            await conn.execute(
                """INSERT INTO quiz_questions
                     (text, options, correct_index, category, difficulty,
                      source, active, obsolete,
                      validation_confidence, validation_notes, validated_at)
                   VALUES ($1, $2::jsonb, $3, $4, $5, 'ai', TRUE, FALSE,
                           $6, $7, NOW())""",
                q["text"], json.dumps(q["options"]), q["correct_index"],
                q["category"], q["difficulty"],
                int(v.get("confidence", 0)) or None, v.get("notes") or None,
            )
            n += 1
        except Exception as e:
            logger.warning("[quiz-gen] insert skip: %s", e)
    return n


__all__ = ["generate_with_distribution", "_bucket_plan", "BUCKET_TO_CATEGORY"]
