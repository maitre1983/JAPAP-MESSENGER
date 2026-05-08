"""
iter83 — Seed script : generate 100 × 5Q quiz sessions via Claude Sonnet 4.5.

Usage :
    # From the admin endpoint :  POST /api/quiz/admin/regenerate-ai
    # Standalone (one-off CLI) :
    cd /app/backend && python -m scripts.seed_quiz_ai

The script is :
    - idempotent on failure (inserts questions first, then builds sessions,
      both steps use transactions)
    - cost-aware (one LLM call per category batch of ~15 questions)
    - schema-validated (output JSON strictly checked before insert)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Any

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

logger = logging.getLogger("seed_quiz_ai")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CATEGORIES = {
    "culture_generale": "culture générale (histoire, géographie, sciences, littérature)",
    "sport": "sport international et africain",
    "economie": "économie mondiale et africaine (marchés, entreprises, finance)",
    "crypto": "cryptomonnaies, blockchain, DeFi, Web3",
    "actualite": "actualité mondiale récente",
    "afrique_monde": "Afrique et monde (politique, culture, personnalités)",
}
QUESTIONS_PER_CATEGORY_BATCH = 15  # ~90 questions across 6 categories

_PROMPT = """Génère un tableau JSON de {n} questions à choix multiples en français, catégorie : {desc}.

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
- 4 options exactement
- correct_index entre 0 et 3
- Pas de questions piégeuses ou ambiguës
- Orthographe française correcte
- Varie les difficultés (60% medium, 25% easy, 15% hard)
- Pas de duplications
"""


async def _generate_for_category(cat_key: str, cat_desc: str, n: int) -> list[dict]:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ["EMERGENT_LLM_KEY"]
    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"seed-quiz-{cat_key}-{random.randint(1000, 9999)}",
            system_message=(
                "Tu es un expert en création de quiz en français. "
                "Retourne STRICTEMENT un tableau JSON, aucun autre texte."
            ),
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    )
    raw = await chat.send_message(UserMessage(text=_PROMPT.format(n=n, desc=cat_desc)))
    # Trim to the JSON array (models sometimes add prose despite instructions)
    raw = raw.strip()
    i, j = raw.find("["), raw.rfind("]")
    if i == -1 or j == -1:
        raise ValueError(f"No JSON array in response for {cat_key}")
    parsed = json.loads(raw[i : j + 1])
    # Validate
    cleaned: list[dict] = []
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
            "category": cat_key,
        })
    logger.info("  %s : %d questions validées / %d reçues", cat_key, len(cleaned), len(parsed))
    return cleaned


async def run_seed(target_sessions: int = 100) -> dict:
    """Populate quiz_questions with ~90 AI questions, then build
    `target_sessions` random sessions of 5 questions each.

    Returns a summary dict for the caller."""
    from database import get_pool
    pool = await get_pool()

    # Ensure schema exists (the endpoint helper is lazy; seed runs standalone)
    async with pool.acquire() as conn:
        from routes.quiz import _DDL as QUIZ_DDL
        for stmt in QUIZ_DDL:
            await conn.execute(stmt)

    # ── Generate the bank ────────────────────────────────────────────────
    all_questions: list[dict] = []
    for cat_key, cat_desc in CATEGORIES.items():
        try:
            batch = await _generate_for_category(cat_key, cat_desc, QUESTIONS_PER_CATEGORY_BATCH)
            all_questions.extend(batch)
        except Exception as e:
            logger.warning("  %s failed : %s", cat_key, e)
    logger.info("Generated %d AI questions total", len(all_questions))
    if len(all_questions) < 30:
        raise RuntimeError("Too few valid questions from AI — aborting seed")

    # ── Insert into DB ───────────────────────────────────────────────────
    inserted_ids: list[int] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for q in all_questions:
                qid = await conn.fetchval(
                    """INSERT INTO quiz_questions
                         (text, options, correct_index, category, difficulty, source, active)
                       VALUES ($1,$2::jsonb,$3,$4,$5,'ai',TRUE) RETURNING id""",
                    q["text"], json.dumps(q["options"]), q["correct_index"],
                    q["category"], q["difficulty"],
                )
                inserted_ids.append(int(qid))
    logger.info("Inserted %d questions", len(inserted_ids))

    # ── Build the sessions ───────────────────────────────────────────────
    async with pool.acquire() as conn:
        # Fetch ALL active questions (AI + any admin ones) so sessions mix them
        active_rows = await conn.fetch(
            "SELECT id, category FROM quiz_questions WHERE active = TRUE"
        )
        if len(active_rows) < 5:
            raise RuntimeError("Bank too small to build sessions")
        by_id = [(int(r["id"]), r["category"]) for r in active_rows]

        # Wipe sessions then rebuild (avoids dup sessions with same ids)
        async with conn.transaction():
            await conn.execute("DELETE FROM quiz_sessions")
            for _ in range(target_sessions):
                # pick 5 random questions, max 2 from same category when possible
                chosen = []
                cats_used: dict[str, int] = {}
                pool_shuffled = list(by_id)
                random.shuffle(pool_shuffled)
                for qid, cat in pool_shuffled:
                    if len(chosen) >= 5:
                        break
                    if cats_used.get(cat, 0) >= 2 and len(chosen) < 4:
                        continue
                    chosen.append((qid, cat))
                    cats_used[cat] = cats_used.get(cat, 0) + 1
                if len(chosen) < 5:
                    # fallback: just pick 5 from pool
                    chosen = pool_shuffled[:5]
                await conn.execute(
                    """INSERT INTO quiz_sessions (question_ids, categories)
                       VALUES ($1::bigint[], $2::text[])""",
                    [c[0] for c in chosen], [c[1] for c in chosen],
                )
    logger.info("Built %d sessions", target_sessions)

    return {
        "questions_generated": len(all_questions),
        "questions_inserted": len(inserted_ids),
        "sessions_built": target_sessions,
    }


if __name__ == "__main__":
    asyncio.run(run_seed())
