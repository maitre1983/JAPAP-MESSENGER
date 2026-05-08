"""
iter234 — Translate French quiz questions to English via Claude.
=================================================================
One-shot, idempotent, batch-safe. Source FR rows are NEVER mutated;
new `language='en'` rows are inserted and linked via `source_question_id`.

Run:
    cd /app/backend && python3 migrations/iter234_translate_quiz_to_en.py

Re-run is safe: rows already translated (matched by source_question_id)
are skipped on the next pass.

Throttle:
    Sequential per-batch (10 questions at a time) to keep Claude latency
    bounded and avoid blocking the event loop. The script itself runs
    OUTSIDE the API process so it doesn't impact preview availability.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sys
from typing import List

import asyncpg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("iter234_translate")

BATCH = 10
LIMIT_PER_RUN = int(os.environ.get("ITER234_LIMIT", "200"))


_PROMPT = """Tu es un traducteur professionnel pour JAPAP, app sociale africaine.
Traduis ces questions de quiz du français vers l'anglais.
Ton: jeune, engageant, audience africaine internationale.

Règles strictes :
- Conserver EXACTEMENT le même `correct_index` (ne PAS réordonner les options)
- Variables comme {{name}}, {{amount}} restent identiques
- Réponse: tableau JSON DANS LE MÊME ORDRE que l'entrée. Chaque objet :
  {{"text": "...", "options": ["...","...","...","..."]}}
- Pas de markdown, pas de wrapper, juste le JSON

Questions :
{batch}
"""


async def _translate_batch(items: list[dict]) -> list[dict | None]:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY missing")
    chat = (LlmChat(api_key=api_key, session_id=f"iter234-{items[0]['id']}",
                    system_message="Tu es un traducteur français→anglais précis.")
            .with_model("anthropic", "claude-sonnet-4-5-20250929"))
    payload = [{"text": q["text"], "options": q["options"],
                "correct_index": q["correct_index"]} for q in items]
    raw = await chat.send_message(UserMessage(text=_PROMPT.format(
        batch=json.dumps(payload, ensure_ascii=False, indent=2),
    )))
    raw = (raw or "").strip()
    i, j = raw.find("["), raw.rfind("]")
    if i == -1 or j == -1:
        log.warning("[iter234] no JSON in response")
        return [None] * len(items)
    parsed = json.loads(raw[i:j + 1])
    out: list[dict | None] = []
    for k, src in enumerate(items):
        if k >= len(parsed) or not isinstance(parsed[k], dict):
            out.append(None)
            continue
        t = parsed[k]
        opts = t.get("options")
        text = t.get("text")
        if not isinstance(opts, list) or len(opts) != len(src["options"]) \
           or not isinstance(text, str) or not text.strip():
            out.append(None)
            continue
        out.append({"text": text.strip(), "options": opts})
    return out


async def run() -> None:
    dsn = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """SELECT id, text, options, correct_index, category, difficulty
                 FROM quiz_questions q
                WHERE q.language = 'fr' AND q.active = TRUE AND q.obsolete = FALSE
                  AND NOT EXISTS (
                      SELECT 1 FROM quiz_questions q2
                       WHERE q2.language = 'en' AND q2.source_question_id = q.id
                  )
                ORDER BY q.id
                LIMIT $1""",
            LIMIT_PER_RUN,
        )
        if not rows:
            log.info("Nothing to translate.")
            return
        log.info("Translating %d questions in batches of %d…", len(rows), BATCH)
        items = [
            {"id": r["id"],
             "text": r["text"],
             "options": (json.loads(r["options"]) if isinstance(r["options"], str)
                         else r["options"]),
             "correct_index": r["correct_index"],
             "category": r["category"],
             "difficulty": r["difficulty"]}
            for r in rows
        ]
        ok = 0
        fail = 0
        for start in range(0, len(items), BATCH):
            chunk = items[start:start + BATCH]
            try:
                translated = await _translate_batch(chunk)
            except Exception as e:  # noqa: BLE001
                log.warning("[iter234] batch %d failed: %s", start, e)
                fail += len(chunk)
                continue
            for src, tr in zip(chunk, translated):
                if not tr:
                    fail += 1
                    continue
                try:
                    await conn.execute(
                        """INSERT INTO quiz_questions
                              (text, options, correct_index, category, difficulty,
                               source, active, obsolete, language, source_question_id, created_at)
                           VALUES ($1, $2::jsonb, $3, $4, $5, 'ai', TRUE, FALSE,
                                   'en', $6, NOW())""",
                        tr["text"], json.dumps(tr["options"]),
                        src["correct_index"], src["category"],
                        src["difficulty"], src["id"],
                    )
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("[iter234] insert id=%s failed: %s", src["id"], e)
                    fail += 1
            log.info("[iter234] progress %d/%d ok=%d fail=%d",
                     start + len(chunk), len(items), ok, fail)
        log.info("[iter234] done — inserted=%d failed=%d", ok, fail)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
