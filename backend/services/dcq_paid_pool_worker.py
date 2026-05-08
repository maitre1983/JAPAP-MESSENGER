"""
iter237k — Worker IA de génération du pool de questions niveau expert
pour le défi du jour PAYANT.

- Modèle : claude-opus-4-5-20251101 via emergentintegrations + EMERGENT_LLM_KEY
- Fréquence : toutes les 48h, hooké dans quiz_champion_scheduler.loop()
- Catégories (6) × ~10 questions = ~60 par batch
- Validation IA croisée : pour chaque question, on relance Claude pour vérifier
  que l'index correct correspond bien à l'option indiquée → drop si mismatch
  (anti-hallucination basique).
- Health check : si pool actif < 30, régénération d'urgence single-flight.

Strictement additif : ne touche pas au pool gratuit (`quiz_questions`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

POOL_REFRESH_HOURS = int(os.environ.get("DCQ_POOL_REFRESH_HOURS", "48"))
POOL_HEALTH_MIN = int(os.environ.get("DCQ_POOL_HEALTH_MIN", "30"))
POOL_HEALTH_POLL_SECONDS = int(os.environ.get("DCQ_POOL_HEALTH_POLL_SECONDS", "600"))

EXPERT_CATEGORIES = [
    "sciences_avancees",
    "histoire_mondiale",
    "economie_finance",
    "technologie_ia",
    "culture_africaine_approfondie",
    "geopolitique",
]

# iter237y — Multi-language generation: each batch produces one set per
# supported UI language. FR remains the canonical fallback for users whose
# language has no questions yet.
SUPPORTED_LANGS = [
    s.strip().lower()
    for s in (os.environ.get("DCQ_POOL_LANGS", "fr,en").split(","))
    if s.strip()
] or ["fr", "en"]

# Localised category labels used inside the LLM prompt so the questions
# are framed in the target language consistently.
CATEGORY_LABELS = {
    "fr": {
        "sciences_avancees": "Sciences avancées",
        "histoire_mondiale": "Histoire mondiale",
        "economie_finance": "Économie & finance",
        "technologie_ia": "Technologie & IA",
        "culture_africaine_approfondie": "Culture africaine approfondie",
        "geopolitique": "Géopolitique",
    },
    "en": {
        "sciences_avancees": "Advanced sciences",
        "histoire_mondiale": "World history",
        "economie_finance": "Economics & finance",
        "technologie_ia": "Technology & AI",
        "culture_africaine_approfondie": "In-depth African culture",
        "geopolitique": "Geopolitics",
    },
}

QUESTIONS_PER_CATEGORY = int(os.environ.get("DCQ_QUESTIONS_PER_CATEGORY", "10"))

_last_refresh_ts: float = 0.0
_refresh_in_flight: bool = False
_emergency_in_flight: bool = False
_bg_tasks: set[asyncio.Task] = set()
# iter237o — Skip refresh during the first WARMUP_S after process boot so the
# HTTP server can serve traffic before any heavy LLM batch starts.
_BOOT_TS: float | None = None
WARMUP_S = int(os.environ.get("DCQ_POOL_WARMUP_SECONDS", "60"))


def _spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


async def _generate_for_category(category: str, batch_id: int, expires_at: datetime, language: str = "fr") -> list[dict]:
    """Calls Claude Opus 4.5 to produce N expert questions and validates them.

    iter237y — Localised: prompts the LLM to write questions in `language`
    (default FR) and stamps each result with the language so /paid/start can
    serve them filtered by user.preferred_lang.
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.warning("[dcq-pool] EMERGENT_LLM_KEY missing — skip")
        return []

    lang = (language or "fr").lower()[:2]
    cat_label = CATEGORY_LABELS.get(lang, CATEGORY_LABELS["fr"]).get(category, category)

    if lang == "en":
        prompt = (
            f"You are an EXPERT-level quiz question generator for an African app.\n"
            f"Generate exactly {QUESTIONS_PER_CATEGORY} expert-level questions "
            f"on the category '{cat_label}'. These are for a PAID GAME — "
            f"they must be hard, non-trivial, and require real expertise.\n\n"
            f"STRICT JSON format (nothing but this array, no text before/after):\n"
            f"[\n"
            f'  {{"question": "Hard, precise question?",\n'
            f'    "options": ["A","B","C","D"],\n'
            f'    "correct_idx": 0,\n'
            f'    "explanation": "Detailed pedagogical explanation in 2-3 sentences."}}\n'
            f"]\n\n"
            f"Strict rules:\n"
            f"- Expert level only\n"
            f"- Verifiable facts, never opinions\n"
            f"- Explanation >= 30 characters, clear and pedagogical\n"
            f"- Exactly 4 options, correct_idx between 0 and 3\n"
            f"- ENTIRE OUTPUT IN ENGLISH (questions, options and explanations)\n"
            f"- Reply ONLY with valid JSON (no markdown, no explanatory text)"
        )
        system_msg = (
            "You are an assistant that returns ONLY valid JSON. "
            "You NEVER use markdown nor explanatory text around it."
        )
    else:
        prompt = (
            f"Tu es un générateur de questions de quiz EXPERT pour une app africaine.\n"
            f"Génère exactement {QUESTIONS_PER_CATEGORY} questions de niveau expert "
            f"sur la catégorie '{cat_label}'. Ces questions sont pour un JEU PAYANT — "
            f"elles doivent être difficiles, non triviales, nécessitant une vraie "
            f"expertise.\n\n"
            f"Format JSON STRICT (rien d'autre que ce tableau, pas de texte avant/après) :\n"
            f"[\n"
            f'  {{"question": "Question difficile et précise ?",\n'
            f'    "options": ["A","B","C","D"],\n'
            f'    "correct_idx": 0,\n'
            f'    "explanation": "Explication détaillée et pédagogique en 2-3 phrases."}}\n'
            f"]\n\n"
            f"Règles strictes :\n"
            f"- Niveau expert uniquement\n"
            f"- Faits vérifiables, jamais d'opinions\n"
            f"- Explication >= 30 caractères, claire et pédagogique\n"
            f"- 4 options exactement, correct_idx entre 0 et 3\n"
            f"- TOUT LE CONTENU EN FRANÇAIS (question, options et explication)\n"
            f"- Réponds UNIQUEMENT avec le JSON valide (pas de markdown, pas de texte explicatif)"
        )
        system_msg = (
            "Tu es un assistant qui génère exclusivement du JSON valide. "
            "Tu ne fais JAMAIS de markdown ni de texte explicatif autour."
        )

    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"dcq_gen_{lang}_{category}_{batch_id}",
            system_message=system_msg,
        )
        .with_model("anthropic", "claude-opus-4-5-20251101")
    )

    try:
        raw = await chat.send_message(UserMessage(text=prompt))
    except Exception as e:  # noqa: BLE001
        logger.warning("[dcq-pool] Claude call failed (%s): %s", category, e)
        return []

    # Tolerate code-fenced output
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    try:
        items = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001
        logger.warning("[dcq-pool] JSON parse failed (%s): %s", category, e)
        return []

    if not isinstance(items, list):
        return []

    accepted = []
    for q in items:
        if not isinstance(q, dict):
            continue
        question = str(q.get("question") or "").strip()
        opts = q.get("options") or []
        try:
            ci = int(q.get("correct_idx"))
        except Exception:
            continue
        explanation = str(q.get("explanation") or "").strip()
        if not question or len(question) < 10:
            continue
        if not isinstance(opts, list) or len(opts) != 4:
            continue
        if not (0 <= ci <= 3):
            continue
        if len(explanation) < 30:
            continue
        accepted.append({
            "question": question[:500],
            "options": [str(o)[:200] for o in opts],
            "correct_idx": ci,
            "explanation": explanation[:1200],
            "category": category,
            "batch_id": batch_id,
            "expires_at": expires_at,
            "language": lang,
        })

    return accepted


async def _validate_question(q: dict) -> bool:
    """Anti-hallucination guard: re-prompt Claude to confirm the chosen
    option index actually answers the question. Lightweight verification —
    we only drop questions where Claude itself disagrees with its own
    correct_idx. Network failure → keep (best-effort).

    iter237y — Validation prompt is localised to match the question's language
    so the LLM stays in the same language frame as generation.
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return True
    options_str = "\n".join(f"{i}: {opt}" for i, opt in enumerate(q["options"]))
    lang = (q.get("language") or "fr").lower()[:2]
    if lang == "en":
        prompt = (
            f"Question: {q['question']}\n\n"
            f"Options:\n{options_str}\n\n"
            f"What is the index (0, 1, 2 or 3) of the SINGLE correct answer? "
            f"Reply with ONE digit only, nothing else."
        )
        system_msg = "You reply with a single digit between 0 and 3."
    else:
        prompt = (
            f"Question : {q['question']}\n\n"
            f"Options :\n{options_str}\n\n"
            f"Quel est l'index (0, 1, 2 ou 3) de la SEULE bonne réponse ? "
            f"Réponds par UN SEUL chiffre, rien d'autre."
        )
        system_msg = "Tu réponds par un seul chiffre entre 0 et 3."
    try:
        chat = (
            LlmChat(
                api_key=api_key,
                session_id=f"dcq_val_{lang}_{q.get('batch_id', 0)}",
                system_message=system_msg,
            )
            .with_model("anthropic", "claude-opus-4-5-20251101")
        )
        out = await chat.send_message(UserMessage(text=prompt))
        m = re.search(r"[0-3]", out or "")
        if not m:
            return True
        return int(m.group(0)) == int(q["correct_idx"])
    except Exception as e:  # noqa: BLE001
        logger.warning("[dcq-pool] validation failed (kept by default): %s", e)
        return True


async def _refresh_pool(reason: str = "scheduled") -> dict[str, int]:
    """Generate one full batch covering all expert categories, validate,
    insert. Returns summary."""
    global _last_refresh_ts
    started = asyncio.get_event_loop().time()
    inserted = 0
    rejected = 0
    failed_cats: list[str] = []

    from database import get_pool
    pool = await get_pool()
    expires = datetime.now(timezone.utc) + timedelta(hours=POOL_REFRESH_HOURS * 2)

    async with pool.acquire() as conn:
        # Ensure DDL exists (the routes module also does, but the worker may
        # run before any user hit the API).
        from routes.dcq_paid import _DDL  # type: ignore
        for stmt in _DDL:
            await conn.execute(stmt)
        # Désactiver les anciennes
        await conn.execute(
            "UPDATE daily_challenge_expert_pool SET active=FALSE "
            "WHERE active=TRUE AND expires_at < NOW()"
        )
        batch_id = await conn.fetchval(
            "SELECT COALESCE(MAX(batch_id),0)+1 FROM daily_challenge_expert_pool"
        ) or 1

        for category in EXPERT_CATEGORIES:
            for lang in SUPPORTED_LANGS:
                try:
                    items = await _generate_for_category(category, batch_id, expires, language=lang)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[dcq-pool] category %s lang=%s failed: %s", category, lang, e)
                    failed_cats.append(f"{category}/{lang}")
                    continue
                if not items:
                    failed_cats.append(f"{category}/{lang}")
                    continue
                for q in items:
                    ok = await _validate_question(q)
                    if not ok:
                        rejected += 1
                        continue
                    try:
                        await conn.execute(
                            """INSERT INTO daily_challenge_expert_pool
                                 (question, options, correct_idx, explanation,
                                  difficulty, category, language, batch_id, expires_at)
                               VALUES ($1, $2, $3, $4, 5, $5, $6, $7, $8)""",
                            q["question"], json.dumps(q["options"]), q["correct_idx"],
                            q["explanation"], q["category"], q["language"],
                            q["batch_id"], q["expires_at"],
                        )
                        inserted += 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[dcq-pool] insert failed: %s", e)
                        rejected += 1

    elapsed = asyncio.get_event_loop().time() - started
    _last_refresh_ts = asyncio.get_event_loop().time()
    summary = {
        "inserted": inserted,
        "rejected": rejected,
        "failed_categories": failed_cats,
        "elapsed_s": round(elapsed, 1),
        "batch_id": batch_id,
        "reason": reason,
    }
    logger.info("[dcq-pool] refresh done: %s", summary)
    return summary


async def _refresh_bg(reason: str = "scheduled") -> None:
    global _refresh_in_flight
    if _refresh_in_flight:
        return
    _refresh_in_flight = True
    try:
        await _refresh_pool(reason=reason)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("[dcq-pool] refresh background error: %s", e)
    finally:
        _refresh_in_flight = False


async def _emergency_bg() -> None:
    global _emergency_in_flight, _last_refresh_ts
    if _emergency_in_flight:
        return
    _emergency_in_flight = True
    try:
        await _refresh_pool(reason="emergency")
        _last_refresh_ts = asyncio.get_event_loop().time()
    except Exception as e:  # noqa: BLE001
        logger.warning("[dcq-pool] emergency error: %s", e)
    finally:
        _emergency_in_flight = False


def schedule_emergency_refresh() -> None:
    """Public helper called from the start endpoint when pool is starving."""
    if _emergency_in_flight:
        return
    _spawn(_emergency_bg())


# ── Hooks called from quiz_champion_scheduler.loop() ─────────────────────
async def refresh_tick() -> None:
    """Called every loop iteration. Triggers a refresh if 48h elapsed and
    no refresh currently in flight. Skipped during the first WARMUP_S after
    process boot so the HTTP server is responsive before heavy batches."""
    global _BOOT_TS
    now = asyncio.get_event_loop().time()
    if _BOOT_TS is None:
        _BOOT_TS = now
    if now - _BOOT_TS < WARMUP_S:
        return
    if now - _last_refresh_ts < POOL_REFRESH_HOURS * 3600:
        return
    if _refresh_in_flight:
        return
    logger.info("[dcq-pool] dispatching scheduled refresh")
    _spawn(_refresh_bg("scheduled"))


_last_health_ts: float = 0.0


async def health_tick() -> None:
    global _last_health_ts
    now = asyncio.get_event_loop().time()
    if now - _last_health_ts < POOL_HEALTH_POLL_SECONDS:
        return
    _last_health_ts = now
    if _emergency_in_flight or _refresh_in_flight:
        return
    try:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM daily_challenge_expert_pool "
                "WHERE active=TRUE AND expires_at > NOW()"
            )
        if count is not None and count < POOL_HEALTH_MIN:
            logger.warning(
                "[dcq-pool-health] active=%d below min=%d — emergency",
                count, POOL_HEALTH_MIN,
            )
            _spawn(_emergency_bg())
    except Exception as e:  # noqa: BLE001
        logger.warning("[dcq-pool-health] error: %s", e)


__all__ = ["refresh_tick", "health_tick", "schedule_emergency_refresh"]
