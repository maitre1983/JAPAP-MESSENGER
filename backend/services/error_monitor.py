"""
AI Error Monitor — iter108 Phase 3.
====================================

Centralised error collection + grouping + admin dashboard.

Schema:
  error_events
    id (bigserial PK), occurred_at, source ('frontend'|'backend'),
    module (e.g. 'wallet.deposit', 'quiz.start', ...), severity ('low'|'medium'|'high'|'critical'),
    signature (sha256 hash of normalised message → grouping key),
    message (FR-ready human-readable),
    stack TEXT (FE: react stack, BE: traceback truncated),
    user_id (optional), url, user_agent, http_status, request_id

  error_groups
    signature PK, first_seen, last_seen, occurrences, severity,
    module, message_sample, status ('open'|'investigating'|'fixed'|'ignored'),
    ai_suggestion (JSON: {summary, root_cause, fix_hint}),
    affected_users (int)

The grouping signature is deterministic: sha256(module || normalise(message))
where normalise() strips dynamic parts (UUIDs, ride_id, IDs, query strings).

Admin endpoints:
  POST /api/errors/report    — public (rate-limited): FE captures + posts here
  GET  /api/admin/errors     — list + filter + paginate
  POST /api/admin/errors/{signature}/{action}    — action ∈ {investigate, fix, ignore, reopen}
  POST /api/admin/errors/{signature}/ai-suggest  — Claude Sonnet 4.5 RCA + fix-hint
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

VALID_SEVERITIES = ("low", "medium", "high", "critical")
VALID_STATUSES = ("open", "investigating", "fixed", "ignored")
VALID_SOURCES = ("frontend", "backend")

# Patterns to scrub before signature hashing — keeps semantically equivalent
# messages clustered into a single group.
_SCRUB_PATTERNS = [
    (re.compile(r"\b[a-f0-9-]{32,}\b"), "<HASH>"),                # hex hashes / uuids
    (re.compile(r"\b[a-zA-Z]+_[a-z0-9]{8,16}\b"), "<ID>"),       # ride_xxx / user_xxx / tx_xxx
    (re.compile(r"\d+\.\d+\.\d+\.\d+"), "<IP>"),                  # IPv4
    (re.compile(r"https?://[^\s\"']+"), "<URL>"),                 # absolute URLs
    (re.compile(r"\b\d{6,}\b"), "<NUM>"),                         # long ints (timestamps)
    (re.compile(r"line \d+"), "line <N>"),                        # line numbers
]


def _scrub(message: str) -> str:
    out = message or ""
    for rx, repl in _SCRUB_PATTERNS:
        out = rx.sub(repl, out)
    return out.strip()[:1000]


def compute_signature(module: str, message: str) -> str:
    """Deterministic 16-char hex signature used as the grouping key."""
    raw = f"{(module or 'unknown').strip().lower()}::{_scrub(message)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def ensure_errors_ddl(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS error_groups (
            signature       VARCHAR(32) PRIMARY KEY,
            first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            occurrences     BIGINT NOT NULL DEFAULT 1,
            source          VARCHAR(16) NOT NULL DEFAULT 'backend',
            module          VARCHAR(80) NOT NULL DEFAULT 'unknown',
            severity        VARCHAR(16) NOT NULL DEFAULT 'medium',
            status          VARCHAR(20) NOT NULL DEFAULT 'open',
            message_sample  TEXT NOT NULL DEFAULT '',
            ai_suggestion   JSONB,
            affected_users  INTEGER NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS error_events (
            id              BIGSERIAL PRIMARY KEY,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source          VARCHAR(16) NOT NULL DEFAULT 'backend',
            module          VARCHAR(80) NOT NULL DEFAULT 'unknown',
            signature       VARCHAR(32) NOT NULL,
            severity        VARCHAR(16) NOT NULL DEFAULT 'medium',
            message         TEXT NOT NULL,
            stack           TEXT NOT NULL DEFAULT '',
            user_id         VARCHAR(64),
            url             VARCHAR(500) NOT NULL DEFAULT '',
            user_agent      VARCHAR(255) NOT NULL DEFAULT '',
            http_status     INTEGER,
            request_id      VARCHAR(64) NOT NULL DEFAULT ''
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_error_events_sig "
        "ON error_events (signature, occurred_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_error_groups_status_last "
        "ON error_groups (status, last_seen DESC)"
    )


async def record_error(
    conn,
    *,
    source: str,
    module: str,
    message: str,
    stack: str = "",
    severity: str = "medium",
    user_id: Optional[str] = None,
    url: str = "",
    user_agent: str = "",
    http_status: Optional[int] = None,
    request_id: str = "",
) -> dict:
    """Atomically record an error event and upsert its parent group.
    Returns the {signature, group_id, status} of the recorded group.
    Best-effort — never raises (caller should not depend on success).
    """
    src = source if source in VALID_SOURCES else "backend"
    sev = severity if severity in VALID_SEVERITIES else "medium"
    mod = (module or "unknown").strip()[:80] or "unknown"
    msg = (message or "").strip()[:2000]
    if not msg:
        msg = "<empty error message>"
    sig = compute_signature(mod, msg)
    try:
        async with conn.transaction():
            await ensure_errors_ddl(conn)
            await conn.execute(
                """INSERT INTO error_events
                       (source, module, signature, severity, message, stack,
                        user_id, url, user_agent, http_status, request_id)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                src, mod, sig, sev, msg, (stack or "")[:8000],
                user_id, (url or "")[:500], (user_agent or "")[:255],
                http_status, request_id[:64],
            )
            # Upsert group — bump occurrences + last_seen + recompute affected_users
            current = await conn.fetchrow(
                "SELECT signature, status, severity FROM error_groups WHERE signature=$1 FOR UPDATE",
                sig,
            )
            if current:
                # Auto-reopen 'fixed' groups when they recur
                new_status = "open" if current["status"] == "fixed" else current["status"]
                # Severity stays the worst-of seen so far (low<medium<high<critical)
                rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
                new_sev = current["severity"] if rank.get(sev, 1) <= rank.get(current["severity"], 1) else sev
                await conn.execute(
                    """UPDATE error_groups
                          SET last_seen = NOW(),
                              occurrences = occurrences + 1,
                              status = $2,
                              severity = $3,
                              updated_at = NOW(),
                              affected_users = (
                                SELECT COUNT(DISTINCT user_id)::int FROM error_events
                                  WHERE signature = $1 AND user_id IS NOT NULL
                              )
                          WHERE signature = $1""",
                    sig, new_status, new_sev,
                )
            else:
                await conn.execute(
                    """INSERT INTO error_groups
                          (signature, source, module, severity, message_sample, affected_users)
                          VALUES ($1,$2,$3,$4,$5,$6)""",
                    sig, src, mod, sev, msg[:500], 1 if user_id else 0,
                )
        return {"signature": sig, "status": "ok"}
    except Exception as e:
        logger.warning("error_monitor.record_error failed (signature=%s): %s", sig, e)
        return {"signature": sig, "status": "failed", "error": str(e)[:200]}


async def list_groups(conn, *, status: str = "", severity: str = "",
                      module: str = "", source: str = "",
                      since_days: int = 30, limit: int = 100, offset: int = 0):
    await ensure_errors_ddl(conn)
    clauses = ["last_seen >= $1"]
    params: list = [datetime.now(timezone.utc) - timedelta(days=since_days)]
    if status:
        if status not in VALID_STATUSES:
            raise ValueError("status invalide")
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    if severity:
        if severity not in VALID_SEVERITIES:
            raise ValueError("severity invalide")
        params.append(severity)
        clauses.append(f"severity = ${len(params)}")
    if module:
        params.append(f"%{module}%")
        clauses.append(f"module ILIKE ${len(params)}")
    if source:
        if source not in VALID_SOURCES:
            raise ValueError("source invalide")
        params.append(source)
        clauses.append(f"source = ${len(params)}")
    params.append(limit)
    params.append(offset)
    rows = await conn.fetch(
        f"""SELECT * FROM error_groups
              WHERE {' AND '.join(clauses)}
              ORDER BY (CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) DESC,
                       last_seen DESC
              LIMIT ${len(params)-1} OFFSET ${len(params)}""",
        *params,
    )
    summary = await conn.fetchrow(
        """SELECT
              COUNT(*) FILTER (WHERE status = 'open')::int          AS open_count,
              COUNT(*) FILTER (WHERE status = 'investigating')::int AS investigating_count,
              COUNT(*) FILTER (WHERE status = 'fixed')::int         AS fixed_count,
              COUNT(*) FILTER (WHERE status = 'ignored')::int       AS ignored_count,
              COALESCE(SUM(occurrences) FILTER (WHERE status='open'), 0)::bigint AS open_occurrences,
              COALESCE(SUM(affected_users) FILTER (WHERE status='open'), 0)::int AS open_affected
              FROM error_groups
              WHERE last_seen >= $1""",
        datetime.now(timezone.utc) - timedelta(days=since_days),
    )
    return {
        "items": [_group_to_dict(r) for r in rows],
        "summary": dict(summary) if summary else {},
        "limit": limit,
        "offset": offset,
    }


def _group_to_dict(r) -> dict:
    sug = r["ai_suggestion"]
    if isinstance(sug, str):
        try:
            sug = json.loads(sug)
        except Exception:
            sug = None
    return {
        "signature": r["signature"],
        "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
        "last_seen":  r["last_seen"].isoformat() if r["last_seen"] else None,
        "occurrences": int(r["occurrences"] or 0),
        "source": r["source"],
        "module": r["module"],
        "severity": r["severity"],
        "status": r["status"],
        "message_sample": r["message_sample"] or "",
        "ai_suggestion": sug,
        "affected_users": int(r["affected_users"] or 0),
    }


async def update_group_status(conn, signature: str, status: str, admin_id: str) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"Statut invalide. Choisir parmi {VALID_STATUSES}.")
    await ensure_errors_ddl(conn)
    row = await conn.fetchrow(
        """UPDATE error_groups SET status = $2, updated_at = NOW()
              WHERE signature = $1 RETURNING *""",
        signature, status,
    )
    if not row:
        return None
    return _group_to_dict(row)


_AI_SYSTEM_PROMPT = (
    "Tu es un expert FullStack JAPAP (FastAPI + React). Pour chaque erreur "
    "fournie tu dois retourner un JSON STRICT avec les clés : "
    "summary (1 phrase FR), root_cause (2-3 phrases FR), fix_hint (3-5 puces "
    "FR actionnables, focus code path concret), urgency ('low'|'medium'|'high'|'critical'). "
    "Aucun texte hors JSON."
)


async def ai_suggest_fix(conn, signature: str) -> dict:
    """Ask Claude Sonnet 4.5 for a root-cause + fix-hint summary based on the
    last 5 events of the group. Stores the result in `error_groups.ai_suggestion`.
    """
    import os as _os
    api_key = _os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY manquante — IA indisponible.")
    await ensure_errors_ddl(conn)
    grp = await conn.fetchrow(
        "SELECT * FROM error_groups WHERE signature = $1", signature,
    )
    if not grp:
        raise ValueError("Groupe d'erreurs introuvable.")
    events = await conn.fetch(
        """SELECT module, message, stack, http_status, url, occurred_at
              FROM error_events WHERE signature = $1
              ORDER BY occurred_at DESC LIMIT 5""",
        signature,
    )
    payload_lines = [
        f"Module: {grp['module']}",
        f"Source: {grp['source']}",
        f"Severity: {grp['severity']}",
        f"Occurrences: {grp['occurrences']}",
        f"Affected users: {grp['affected_users']}",
        f"Sample message: {grp['message_sample']}",
        "\nDerniers événements (jusqu'à 5):",
    ]
    for e in events:
        payload_lines.append(
            f"- [{e['occurred_at'].isoformat()}] HTTP {e['http_status'] or '-'} "
            f"@ {e['url'] or '-'} :: {e['message'][:300]}"
        )
        if e["stack"]:
            payload_lines.append(f"  stack (extrait) : {e['stack'][:600]}")
    user_prompt = "\n".join(payload_lines)

    from emergentintegrations.llm.chat import LlmChat, UserMessage  # lazy import
    chat = LlmChat(
        api_key=api_key,
        session_id=f"err_{signature}_{uuid.uuid4().hex[:6]}",
        system_message=_AI_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    raw = await chat.send_message(UserMessage(text=user_prompt))
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.removeprefix("json").lstrip()
    if text.startswith("```"):
        text = text.strip("`").strip()
    try:
        suggestion = json.loads(text)
    except json.JSONDecodeError:
        suggestion = {"summary": text[:500], "root_cause": "", "fix_hint": [], "urgency": "medium"}
    # Sanity check + store
    suggestion["generated_at"] = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE error_groups SET ai_suggestion = $1::jsonb, updated_at = NOW() WHERE signature = $2",
        json.dumps(suggestion), signature,
    )
    return suggestion


__all__ = [
    "ensure_errors_ddl", "record_error",
    "list_groups", "update_group_status", "ai_suggest_fix",
    "compute_signature", "VALID_SEVERITIES", "VALID_STATUSES", "VALID_SOURCES",
]
