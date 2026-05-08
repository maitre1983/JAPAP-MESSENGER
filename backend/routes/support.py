"""
iter90 — Support module (AI triage + human tickets).

Flow:
  1. User asks a question via POST /api/support/ai-chat
     → Claude Sonnet 4.5 answers instantly using a strict JAPAP-aware system prompt.
  2. If the user is not satisfied, POST /api/support/ticket
     → persists a row in support_tickets, emails ops inbox + acknowledgment to user.

Admin endpoints (admin only):
  GET   /api/support/admin/tickets    — paginated list
  PATCH /api/support/admin/tickets/{id}/status — update status

Categories: account · wallet · kyc · games · technical · other
Statuses:   open · in_progress · resolved · closed
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from services.ops_notifications import (
    notify_support_ticket_to_ops,
    notify_support_ticket_ack_to_user,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/support", tags=["support"])

_EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")

CATEGORIES = ("account", "wallet", "kyc", "games", "technical", "other")
STATUSES = ("open", "in_progress", "resolved", "closed")

_DDL_DONE = False


async def _ensure_ddl(conn) -> None:
    global _DDL_DONE
    if _DDL_DONE:
        return
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
          id          BIGSERIAL PRIMARY KEY,
          ticket_id   VARCHAR(32) NOT NULL UNIQUE,
          user_id     VARCHAR(64) NOT NULL,
          user_email  VARCHAR(255) NOT NULL,
          user_name   VARCHAR(200),
          category    VARCHAR(32) NOT NULL DEFAULT 'other',
          subject     VARCHAR(300) NOT NULL,
          message     TEXT NOT NULL,
          status      VARCHAR(32) NOT NULL DEFAULT 'open',
          ai_tried    BOOLEAN NOT NULL DEFAULT FALSE,
          ai_transcript JSONB,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          resolved_at TIMESTAMPTZ
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_user    ON support_tickets(user_id, created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_status  ON support_tickets(status, created_at DESC)")
    _DDL_DONE = True


# ─── AI Support Chat ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """Tu es **JAPAP Assist**, l'assistant support officiel de JAPAP Messenger (super-app Messaging + Wallet + Jeux + Marketplace + Crypto).

RÔLE : répondre avec précision et action aux questions des utilisateurs pour résoudre 70% des demandes sans agent humain.

TON : chaleureux, direct, professionnel, en français. Pas de formules vagues. Pas de "je ne sais pas" sans alternative.

CONNAISSANCES MÉTIER :
• **Wallet** : comptes en XAF par défaut. Dépôts via USDT TRC20/BEP20 (manuel, hash à fournir) ou Hubtel carte bancaire. Retraits uniquement en USDT (TRC20/BEP20), KYC obligatoire au-dessus du seuil admin, frais variables selon plan Pro. Retrait = en "processing" (auto) ou "pending" (admin validation).
• **Jeux** : Roue de la Fortune (cycle 30 jours, objectif 10 000 points + 25 jours joués + 75% précision quiz ≥ 50 réponses pour débloquer Starter Pro gratuit), Quiz JAPAP (10s/question, 5 questions), Tap Challenge (10s, 1 run/jour, paliers 30/50/80 taps), Challenge d'ami (duel 1v1 quiz ou tap). Points centralisés, clamp souverain (impossible de dépasser 10k avant 25j).
• **KYC** : doc d'identité + selfie requis pour retraits. Validation manuelle admin (24–72h ouvrés).
• **Support dépôt/retrait** : depot@japapmessenger.com avec hash de transaction si >10 min sans crédit.
• **Sécurité** : mot de passe = authentification classique. Superadmin = 2FA email. Sessions avec cookies sécurisés.
• **JAPAP PRO** : abonnement premium, frais retrait réduits, features avancées.

RÈGLES D'OR :
1. Réponds TOUJOURS en français, sauf si l'utilisateur écrit explicitement dans une autre langue.
2. Sois concis : 3–6 phrases max par réponse, sauf si étapes nécessaires (liste numérotée).
3. Propose TOUJOURS une action concrète : page à visiter, champ à remplir, document à fournir, support à contacter.
4. Si la question sort du périmètre JAPAP (météo, blagues, politique…), redirige poliment vers le support.
5. Si tu ne peux VRAIMENT pas aider (cas spécifique à un utilisateur, demande de remboursement, escalation), termine par : "Pour un traitement personnalisé, vous pouvez cliquer sur \\"Contacter un agent\\" ci-dessous."
6. JAMAIS inventer des montants, frais, délais, ou URLs. Si inconnu → rediriger vers /support agent.
7. JAMAIS demander le mot de passe ou le code 2FA de l'utilisateur.

FORMAT RÉPONSE : texte simple, éventuellement listes `• ` pour étapes. Pas de markdown lourd.
"""


class AIChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AIChatRequest(BaseModel):
    messages: list[AIChatMessage] = Field(..., min_length=1, max_length=20)


@router.post("/ai-chat")
async def ai_chat(req: AIChatRequest, request: Request):
    """Stream-style chat with JAPAP Assist. Expects full conversation history
    in `messages` (we re-send on every turn — simple & stateless)."""
    user = await get_current_user(request)
    if not _EMERGENT_KEY:
        raise HTTPException(status_code=503, detail="Assistant IA indisponible — clé non configurée.")
    # Last user message must exist
    last_user = next((m for m in reversed(req.messages) if m.role == "user"), None)
    if not last_user or not last_user.content.strip():
        raise HTTPException(status_code=400, detail="Message utilisateur manquant.")
    if len(last_user.content) > 2000:
        raise HTTPException(status_code=400, detail="Message trop long (max 2000 caractères).")

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        # Stateless: we replay all previous messages as a concatenated context
        # because LlmChat session_id gives us server-side memory but here we
        # want the *frontend* to own the history (user can refresh the page).
        history_txt = ""
        if len(req.messages) > 1:
            history_txt = "\n\n--- Historique de la conversation ---\n"
            for m in req.messages[:-1]:
                tag = "Utilisateur" if m.role == "user" else "Assistant"
                history_txt += f"{tag}: {m.content}\n"
            history_txt += "--- Fin historique ---\n\n"
        prompt = f"{history_txt}Utilisateur: {last_user.content}"
        session_id = f"support_{user['user_id']}_{uuid.uuid4().hex[:8]}"
        chat = LlmChat(
            api_key=_EMERGENT_KEY,
            session_id=session_id,
            system_message=SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        reply = await chat.send_message(UserMessage(text=last_user.content if not history_txt else prompt))
        text = (reply or "").strip()
        if not text:
            raise HTTPException(status_code=502, detail="Réponse IA vide.")
        # Heuristic: does the reply invite escalation?
        suggests_agent = ("contacter un agent" in text.lower()
                          or "agent humain" in text.lower()
                          or "support humain" in text.lower())
        # Log conversation (fire-and-forget) — analytics FAQ + future training
        await _log_ai_turn(
            user_id=user['user_id'],
            user_email=user.get('email', ''),
            session_id=session_id,
            user_message=last_user.content,
            assistant_reply=text,
            suggests_human_agent=suggests_agent,
            history_length=len(req.messages),
        )
        return {
            "reply": text,
            "suggests_human_agent": suggests_agent,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("support ai-chat error: %s", e)
        raise HTTPException(status_code=502, detail=f"Erreur IA : {e}")


async def _log_ai_turn(
    *, user_id: str, user_email: str, session_id: str,
    user_message: str, assistant_reply: str,
    suggests_human_agent: bool, history_length: int,
) -> None:
    """Persist every AI turn in support_ai_conversations for analytics.
    Non-blocking: any failure is logged and swallowed (the user's reply
    is already sent back at this point)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS support_ai_conversations (
                  id          BIGSERIAL PRIMARY KEY,
                  user_id     VARCHAR(64) NOT NULL,
                  user_email  VARCHAR(255),
                  session_id  VARCHAR(128) NOT NULL,
                  user_message       TEXT NOT NULL,
                  assistant_reply    TEXT NOT NULL,
                  suggests_human_agent BOOLEAN NOT NULL DEFAULT FALSE,
                  history_length INT NOT NULL DEFAULT 1,
                  escalated_to_ticket_id VARCHAR(32),
                  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )""")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_support_ai_user ON support_ai_conversations(user_id, created_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_support_ai_session ON support_ai_conversations(session_id, created_at DESC)"
            )
            await conn.execute(
                """INSERT INTO support_ai_conversations
                   (user_id, user_email, session_id, user_message, assistant_reply,
                    suggests_human_agent, history_length)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                user_id, user_email, session_id,
                user_message[:4000], assistant_reply[:4000],
                bool(suggests_human_agent), int(history_length),
            )
    except Exception as e:
        logger.warning("support AI conv log failed: %s", e)


# ─── Ticket creation ──────────────────────────────────────────────────────

class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=300)
    message: str = Field(..., min_length=10, max_length=4000)
    category: Literal["account", "wallet", "kyc", "games", "technical", "other"] = "other"
    ai_transcript: Optional[list[AIChatMessage]] = None  # frontend can attach the full AI chat


@router.post("/ticket")
async def create_ticket(req: CreateTicketRequest, request: Request):
    user = await get_current_user(request)
    ticket_id = f"SUP-{uuid.uuid4().hex[:8].upper()}"
    user_name = user.get("name") or user.get("username") or user.get("email", "")

    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        ai_transcript_json = json.dumps([m.model_dump() for m in (req.ai_transcript or [])]) if req.ai_transcript else None
        await conn.execute(
            """INSERT INTO support_tickets
               (ticket_id, user_id, user_email, user_name, category, subject, message, ai_tried, ai_transcript)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)""",
            ticket_id, user['user_id'], user.get('email', ''), user_name,
            req.category, req.subject, req.message,
            bool(req.ai_transcript),
            ai_transcript_json,
        )
        await conn.execute(
            """INSERT INTO audit_logs (user_id, action, resource, details)
               VALUES ($1,'support_ticket_created','support',$2)""",
            user['user_id'],
            f'{{"ticket_id":"{ticket_id}","category":"{req.category}"}}',
        )

    # Fire both emails (fire-and-forget)
    notify_support_ticket_to_ops(
        ticket_id=ticket_id,
        user_email=user.get('email', ''),
        user_name=user_name,
        category=req.category,
        subject=req.subject,
        message=req.message,
        ai_tried=bool(req.ai_transcript),
    )
    notify_support_ticket_ack_to_user(
        to_email=user.get('email', ''),
        user_name=user_name,
        ticket_id=ticket_id,
        subject=req.subject,
    )
    return {
        "ticket_id": ticket_id,
        "status": "open",
        "message": "Votre ticket a été enregistré. Un email de confirmation a été envoyé. Notre équipe vous répondra rapidement.",
    }


@router.get("/my-tickets")
async def my_tickets(request: Request, limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT ticket_id, category, subject, status, created_at, updated_at, resolved_at
               FROM support_tickets WHERE user_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            user['user_id'], limit,
        )
    return {"items": [
        {
            "ticket_id": r["ticket_id"], "category": r["category"], "subject": r["subject"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        } for r in rows
    ]}


# ─── Admin endpoints ──────────────────────────────────────────────────────

async def _require_admin(user):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/admin/tickets")
async def admin_list_tickets(
    request: Request,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user = await get_current_user(request)
    await _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        if status and status in STATUSES:
            rows = await conn.fetch(
                """SELECT * FROM support_tickets WHERE status = $1
                   ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
                status, limit, offset,
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM support_tickets WHERE status = $1", status)
        else:
            rows = await conn.fetch(
                """SELECT * FROM support_tickets ORDER BY created_at DESC LIMIT $1 OFFSET $2""",
                limit, offset,
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM support_tickets")
    return {
        "total": int(total or 0),
        "items": [
            {
                "ticket_id": r["ticket_id"], "user_id": r["user_id"],
                "user_email": r["user_email"], "user_name": r["user_name"],
                "category": r["category"], "subject": r["subject"], "message": r["message"],
                "status": r["status"], "ai_tried": r["ai_tried"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
            } for r in rows
        ],
    }


class UpdateTicketStatus(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"]


@router.get("/admin/ai-analytics")
async def admin_ai_analytics(request: Request, days: int = Query(30, ge=1, le=365)):
    """Aggregate AI support conversations: volume, escalation rate,
    top sessions, latest unresolved turns.
    """
    user = await get_current_user(request)
    await _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS support_ai_conversations (
          id BIGSERIAL PRIMARY KEY,
          user_id VARCHAR(64) NOT NULL,
          user_email VARCHAR(255),
          session_id VARCHAR(128) NOT NULL,
          user_message TEXT NOT NULL,
          assistant_reply TEXT NOT NULL,
          suggests_human_agent BOOLEAN NOT NULL DEFAULT FALSE,
          history_length INT NOT NULL DEFAULT 1,
          escalated_to_ticket_id VARCHAR(32),
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        totals = await conn.fetchrow(
            """SELECT COUNT(*) AS total_turns,
                     COUNT(DISTINCT session_id) AS sessions,
                     COUNT(DISTINCT user_id) AS unique_users,
                     COUNT(*) FILTER (WHERE suggests_human_agent) AS escalation_hints,
                     COUNT(*) FILTER (WHERE escalated_to_ticket_id IS NOT NULL) AS actual_escalations
               FROM support_ai_conversations
               WHERE created_at > NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        # Timeseries
        series = await conn.fetch(
            """SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                      COUNT(*) AS turns,
                      COUNT(DISTINCT session_id) AS sessions,
                      COUNT(*) FILTER (WHERE escalated_to_ticket_id IS NOT NULL) AS escalated
               FROM support_ai_conversations
               WHERE created_at > NOW() - ($1 || ' days')::interval
               GROUP BY 1 ORDER BY 1""",
            str(days),
        )
        # Recent turns
        recent = await conn.fetch(
            """SELECT user_email, user_message, assistant_reply, suggests_human_agent,
                      escalated_to_ticket_id, created_at
               FROM support_ai_conversations
               WHERE created_at > NOW() - ($1 || ' days')::interval
               ORDER BY created_at DESC LIMIT 20""",
            str(days),
        )
    t = dict(totals or {})
    total_turns = int(t.get('total_turns', 0) or 0)
    return {
        "window_days": days,
        "kpis": {
            "total_turns": total_turns,
            "sessions": int(t.get('sessions', 0) or 0),
            "unique_users": int(t.get('unique_users', 0) or 0),
            "escalation_hints": int(t.get('escalation_hints', 0) or 0),
            "actual_escalations": int(t.get('actual_escalations', 0) or 0),
            "escalation_rate": (round(100.0 * (int(t.get('actual_escalations', 0) or 0)) / total_turns, 2)
                                if total_turns else 0.0),
        },
        "timeseries": [
            {"day": r["day"], "turns": int(r["turns"]),
             "sessions": int(r["sessions"]), "escalated": int(r["escalated"])}
            for r in series
        ],
        "recent_turns": [
            {
                "user_email": r["user_email"],
                "user_message": (r["user_message"] or "")[:400],
                "assistant_reply": (r["assistant_reply"] or "")[:400],
                "suggests_human_agent": bool(r["suggests_human_agent"]),
                "escalated_to_ticket_id": r["escalated_to_ticket_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            } for r in recent
        ],
    }

@router.patch("/admin/tickets/{ticket_id}/status")
async def admin_update_status(ticket_id: str, req: UpdateTicketStatus, request: Request):
    user = await get_current_user(request)
    await _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        resolved_expr = (
            "resolved_at = NOW()" if req.status in ("resolved", "closed") else "resolved_at = resolved_at"
        )
        row = await conn.fetchrow(
            f"""UPDATE support_tickets SET status = $1, updated_at = NOW(), {resolved_expr}
                WHERE ticket_id = $2 RETURNING ticket_id, status""",
            req.status, ticket_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        await conn.execute(
            """INSERT INTO audit_logs (user_id, action, resource, details)
               VALUES ($1,'support_ticket_status','support',$2)""",
            user['user_id'],
            f'{{"ticket_id":"{ticket_id}","new_status":"{req.status}"}}',
        )
    return {"ticket_id": row["ticket_id"], "status": row["status"]}
