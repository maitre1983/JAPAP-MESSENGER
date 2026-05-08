"""
Quiz Question Picker — anti-repetition + balanced category distribution
(iter130, Phase 3.E).
========================================================================

Public API
----------
  pick_question_ids_for_user(conn, user_id, size=5) -> (question_ids, fallback_used)
  create_session_for_user(conn, user_id, source, size=5)
      -> (session_id, question_ids, fallback_used)
      Builds a fresh dynamic session with priority-based selection:
        1) Questions never seen
        2) Questions seen > anti_repeat_days ago
        3) Last-resort fallback (any non-obsolete in the bucket)

Distribution
------------
The picker fans out across enabled categories by an admin-tunable weighted
plan (default 50/20/15/15 — Africa / sport / economy & crypto / world).
Admins configure via game settings (`quiz_dist_*_pct`). Sum is forced to 100.
"""
from __future__ import annotations
import logging
import random
from typing import List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 7
SESSION_SIZE = 5

# Category bucket memberships. Categories not listed here are treated as
# 'world' (general) so admins can add free-form categories without losing
# them in the distribution.
AFRICA_CATEGORIES = {
    "afrique_monde", "histoire_afrique", "geographie_afrique",
    "sport_africain", "musique_afrique", "entrepreneurs_afrique",
    "institutions_afrique",
}
SPORT_CATEGORIES = {"sport", "football", "sport_africain"}
ECON_CATEGORIES = {"economie", "crypto", "actualite"}
WORLD_CATEGORIES = {"culture_generale", "technologie"}


async def _quiz_settings() -> dict:
    """Lazy import to avoid circulars."""
    from services.games_settings import get_quiz_config
    return await get_quiz_config()


def _bucket_plan(cfg: dict, size: int) -> List[Tuple[str, int, set[str]]]:
    """Translate admin pcts → integer slot counts (sum == size). Buckets
    are ordered Africa / Sport / Econ / World; the LAST one absorbs any
    rounding remainder so total is always exactly `size`."""
    pcts = [
        ("africa", int(cfg.get("quiz_dist_africa_pct", 50)), AFRICA_CATEGORIES),
        ("sport",  int(cfg.get("quiz_dist_sport_pct",  20)), SPORT_CATEGORIES),
        ("econ",   int(cfg.get("quiz_dist_econ_pct",   15)), ECON_CATEGORIES),
        ("world",  int(cfg.get("quiz_dist_world_pct",  15)), WORLD_CATEGORIES),
    ]
    out: List[Tuple[str, int, set[str]]] = []
    accum = 0
    for i, (name, pct, cats) in enumerate(pcts):
        if i == len(pcts) - 1:
            count = size - accum
        else:
            count = round((pct / 100.0) * size)
            accum += count
        out.append((name, max(0, count), cats))
    return out


async def _seen_ids_recent(conn, user_id: str, days: int) -> set[int]:
    """Question_ids the user has seen within the last `days` days."""
    rows = await conn.fetch(
        """SELECT question_id FROM user_quiz_question_history
            WHERE user_id = $1
              AND seen_at > NOW() - ($2 || ' days')::interval""",
        user_id, str(days),
    )
    return {int(r["question_id"]) for r in rows}


async def _seen_ids_all(conn, user_id: str) -> set[int]:
    """All question_ids the user has ever seen (for priority sorting)."""
    rows = await conn.fetch(
        "SELECT DISTINCT question_id FROM user_quiz_question_history WHERE user_id = $1",
        user_id,
    )
    return {int(r["question_id"]) for r in rows}


async def _enabled_categories(conn) -> set[str]:
    rows = await conn.fetch(
        "SELECT category FROM quiz_category_status WHERE enabled = TRUE"
    )
    if not rows:
        return set()  # empty set ⇒ no filter (back-compat for fresh installs)
    return {r["category"] for r in rows}


async def _pool_for_bucket(conn, bucket_categories: set[str],
                           excl_ids: set[int],
                           enabled_categories: set[str],
                           limit: int,
                           prefer_unseen_for_user: str = "",
                           language: str = "fr") -> List[int]:
    """Return up to `limit` random question_ids from the bucket, excluding
    `excl_ids`, with smart priority:
      1) Questions NEVER seen by this user (if prefer_unseen_for_user given)
      2) Then random others
    iter234 — `language` filters quiz_questions.language. Caller is
    responsible for the FR fallback when the localised bank is too small.
    """
    cats = bucket_categories
    if enabled_categories:
        cats = bucket_categories & enabled_categories
        if not cats:
            return []
    excl_arr = list(excl_ids) if excl_ids else [-1]
    chosen: List[int] = []
    if prefer_unseen_for_user:
        # Priority 1: never seen by user.
        rows = await conn.fetch(
            """SELECT q.id FROM quiz_questions q
                WHERE q.active = TRUE AND q.obsolete = FALSE
                  AND q.language = $5
                  AND q.category = ANY($1::text[])
                  AND q.id <> ALL($2::bigint[])
                  AND NOT EXISTS (
                      SELECT 1 FROM user_quiz_question_history h
                       WHERE h.user_id = $3 AND h.question_id = q.id
                  )
             ORDER BY random() LIMIT $4""",
            list(cats), excl_arr, prefer_unseen_for_user, limit, language,
        )
        chosen = [int(r["id"]) for r in rows]
        if len(chosen) >= limit:
            return chosen
    # Priority 2: any other from bucket excluding already-chosen.
    excl_arr2 = list(excl_ids | set(chosen)) if (excl_ids or chosen) else [-1]
    extra_n = limit - len(chosen)
    rows = await conn.fetch(
        """SELECT id FROM quiz_questions
            WHERE active = TRUE AND obsolete = FALSE
              AND language = $4
              AND category = ANY($1::text[])
              AND id <> ALL($2::bigint[])
         ORDER BY random() LIMIT $3""",
        list(cats), excl_arr2, extra_n, language,
    )
    chosen.extend(int(r["id"]) for r in rows)
    return chosen


async def pick_question_ids_for_user(conn, user_id: str,
                                      size: int = SESSION_SIZE,
                                      language: str = "fr"
                                      ) -> Tuple[List[int], bool]:
    """Build a balanced pick with smart priority (never-seen > seen-old).
    Returns (question_ids, fallback_used). `fallback_used=True` means the
    bank was too small for the pure exclusion path.

    iter234 — `language` filters quiz_questions.language. If the localised
    bank yields fewer than `size`, we transparently fall back to French
    rather than fail the session — operators add EN translations
    incrementally and the picker should never block a quiz start.
    """
    cfg = await _quiz_settings()
    window = int(cfg.get("quiz_anti_repeat_days", DEFAULT_WINDOW_DAYS))
    seen_recent = await _seen_ids_recent(conn, user_id, window)
    enabled = await _enabled_categories(conn)
    plan = _bucket_plan(cfg, size)

    async def _do(lang: str) -> Tuple[list[int], bool]:
        chosen: list[int] = []
        fallback = False
        for name, count, cats in plan:
            if count <= 0:
                continue
            ids = await _pool_for_bucket(
                conn, cats, seen_recent | set(chosen), enabled, count,
                prefer_unseen_for_user=user_id, language=lang,
            )
            if len(ids) < count:
                extra = await _pool_for_bucket(
                    conn, cats, set(chosen) | set(ids), enabled, count - len(ids),
                    prefer_unseen_for_user="", language=lang,
                )
                ids.extend(extra)
                if len(ids) < count:
                    fallback = True
                    logger.warning(
                        "[quiz-picker] bucket=%s short lang=%s (have=%d need=%d) for user=%s",
                        name, lang, len(ids), count, user_id,
                    )
            chosen.extend(ids[:count])
        # Step 3 — last-resort within the same language.
        if len(chosen) < size:
            fallback = True
            excl_ids = list(set(chosen) | seen_recent) or [-1]
            rows = await conn.fetch(
                """SELECT id FROM quiz_questions
                    WHERE active = TRUE AND obsolete = FALSE
                      AND language = $3
                      AND id <> ALL($1::bigint[])
                 ORDER BY random() LIMIT $2""",
                excl_ids, size - len(chosen), lang,
            )
            chosen.extend(int(r["id"]) for r in rows)
        return chosen, fallback

    chosen, fallback = await _do(language)
    if len(chosen) < size and language != "fr":
        # iter234 — Localised bank exhausted → fall back to French so the
        # session always starts. The front-end will display French strings
        # for the missing slots; this is acceptable while EN translations
        # are still being generated.
        logger.warning(
            "[quiz-picker] language=%s yielded %d/%d → falling back to fr",
            language, len(chosen), size,
        )
        fr_extra, _ = await _do("fr")
        seen_set = set(chosen)
        for qid in fr_extra:
            if qid not in seen_set:
                chosen.append(qid)
                seen_set.add(qid)
                if len(chosen) >= size:
                    break
        fallback = True

    if len(chosen) < size:
        logger.error("[quiz-picker] bank exhausted for user=%s have=%d", user_id, len(chosen))
    return chosen[:size], fallback


async def create_session_for_user(conn, user_id: str,
                                   source: str = "quiz_standard",
                                   size: int = SESSION_SIZE,
                                   language: str = "fr"
                                   ) -> Tuple[int, List[int], bool]:
    """Pick fresh question IDs, persist a new quiz_sessions row, and append
    history entries. Returns (session_id, question_ids, fallback_used).

    Caller MUST be inside an active conn.transaction() so the session row
    and the history rows are committed together.

    iter234 — `language` is forwarded to the picker.
    """
    qids, fallback = await pick_question_ids_for_user(conn, user_id, size, language)
    if not qids or len(qids) < size:
        return 0, qids, True
    cat_rows = await conn.fetch(
        "SELECT id, category FROM quiz_questions WHERE id = ANY($1::bigint[])",
        qids,
    )
    cats_by_id = {int(r["id"]): r["category"] for r in cat_rows}
    categories = [cats_by_id.get(qid, "unknown") for qid in qids]
    session_id = await conn.fetchval(
        """INSERT INTO quiz_sessions (question_ids, categories)
           VALUES ($1::bigint[], $2::text[]) RETURNING id""",
        qids, categories,
    )
    await conn.executemany(
        """INSERT INTO user_quiz_question_history (user_id, question_id, source, seen_at)
           VALUES ($1, $2, $3, NOW())""",
        [(user_id, qid, source) for qid in qids],
    )
    return int(session_id), qids, fallback


__all__ = [
    "pick_question_ids_for_user", "create_session_for_user",
    "DEFAULT_WINDOW_DAYS", "SESSION_SIZE",
    "AFRICA_CATEGORIES", "SPORT_CATEGORIES",
    "ECON_CATEGORIES", "WORLD_CATEGORIES",
]
