"""
JAPAP — KYC Verification (iter172 P0 overhaul)
==============================================
Hardened KYC submission + admin review flow:
  • Dynamic recto/verso based on document type (CNI / Permis = 2 photos,
    Passeport = 1 photo)
  • Auto-compression to JPEG ≤1024px / quality 78 (preview cap 480px / Q70)
  • Hybrid AI + heuristic pre-validation (services.kyc_ai_validator)
  • Admin returns full URLs + AI risk score + alerts (NO auto-approve)
"""
import base64
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from database import get_pool
from routes.auth import get_current_user, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kyc", tags=["kyc"])

_UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
_UPLOAD_DIR.mkdir(exist_ok=True)

_ALLOWED_ID_TYPES = {"passport", "national_id", "drivers_license"}
# Document types that require both recto and verso pictures.
_DUAL_SIDE_TYPES = {"national_id", "drivers_license"}
_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_MAX_IMAGE_SIZE = 12 * 1024 * 1024  # 12MB raw upload — compressed below

# Compression target — full version (admin zoom).
_COMPRESS_MAX_WIDTH = 1024
_COMPRESS_QUALITY = 78
# Preview thumbnail (admin grid).
_PREVIEW_MAX_WIDTH = 480
_PREVIEW_QUALITY = 72


class RejectRequest(BaseModel):
    reason: str


def _ensure_schema_columns(conn) -> None:
    """Idempotent ALTER TABLE for iter172 fields. Migration file is the
    canonical source but this guard keeps the worker safe across deploys."""


_columns_ensured = False


async def _ensure_iter172_columns():
    """Idempotent schema guard. Runs ALTER TABLE IF NOT EXISTS once per
    process — the result is cached in a module-level flag so hot admin
    endpoints don't pay the cost on every request. The canonical call
    site is the FastAPI `startup` hook in server.py (iter214)."""
    global _columns_ensured
    if _columns_ensured:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "id_back_photo_url VARCHAR(1000)")
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "preview_id_url VARCHAR(1000)")
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "preview_id_back_url VARCHAR(1000)")
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "preview_selfie_url VARCHAR(1000)")
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "ai_risk_score VARCHAR(16)")
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "ai_alerts JSONB DEFAULT '[]'::jsonb")
        await conn.execute(
            "ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS "
            "ai_payload JSONB")
        # iter214 — durable DB-backed storage for KYC images (bytea).
        # Rationale: local disk on ephemeral Kubernetes pods loses files
        # on every rotation/restart, leaving the admin UI showing "Image
        # indisponible" and breaking legal traceability. We now persist
        # the bytes alongside the metadata so images stay readable
        # forever regardless of which pod serves them.
        for col in ("id_photo_bytes", "id_back_photo_bytes", "selfie_bytes",
                    "preview_id_bytes", "preview_id_back_bytes", "preview_selfie_bytes"):
            await conn.execute(
                f"ALTER TABLE kyc_verifications ADD COLUMN IF NOT EXISTS {col} BYTEA")
    _columns_ensured = True


def _compress_to_jpeg(raw: bytes, *, max_width: int, quality: int) -> bytes:
    """Open ANY supported image and re-emit as JPEG with the given
    max width + quality. EXIF rotation is honoured. Returns the encoded
    bytes. Raises HTTPException on unreadable input.
    """
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)  # honour camera rotation
        if img.mode not in ("RGB",):
            img = img.convert("RGB")
        # Resize keeping aspect — only if larger than max_width.
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[kyc] image compression failed: {e}")
        raise HTTPException(status_code=400, detail="Image illisible ou corrompue")


async def _save_kyc_image(file: UploadFile, prefix: str) -> tuple[str, str, bytes, bytes]:
    """Read upload, compress to (full, preview) JPEGs, persist both.

    Returns: (full_url, preview_url, full_jpeg_bytes, preview_jpeg_bytes)
    The bytes are ALSO persisted to the DB (iter214) so images stay
    readable across pod rotations — local disk is just a warm cache.
    """
    ext = Path(file.filename or "img.jpg").suffix.lower() or ".jpg"
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400, detail=f"Format non supporté: {ext}")
    raw = await file.read()
    if len(raw) > _MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=400, detail="Image trop volumineuse (max 12MB)")
    if len(raw) < 512:
        raise HTTPException(status_code=400, detail="Image trop petite")

    full = _compress_to_jpeg(
        raw, max_width=_COMPRESS_MAX_WIDTH, quality=_COMPRESS_QUALITY)
    preview = _compress_to_jpeg(
        raw, max_width=_PREVIEW_MAX_WIDTH, quality=_PREVIEW_QUALITY)

    nonce = uuid.uuid4().hex[:16]
    full_name = f"{prefix}_{nonce}.jpg"
    preview_name = f"{prefix}_{nonce}_preview.jpg"
    with open(_UPLOAD_DIR / full_name, "wb") as f:
        f.write(full)
    with open(_UPLOAD_DIR / preview_name, "wb") as fp:
        fp.write(preview)
    return (f"/api/upload/files/{full_name}",
            f"/api/upload/files/{preview_name}",
            full, preview)


def _kyc_row_to_public(row) -> dict:
    """Sanitised view returned to the user themselves (never to admins)."""
    return {
        "kyc_id": row["kyc_id"],
        "status": row["status"],
        "full_name": row["full_name"],
        "id_type": row["id_type"],
        "id_number_masked":
            (row["id_number"][:2] + "•" * max(0, len(row["id_number"]) - 4) + row["id_number"][-2:])
            if row["id_number"] else "",
        "rejection_reason": row["rejection_reason"] or "",
        "created_at": row["created_at"].isoformat(),
        "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
    }


@router.get("/status")
async def get_my_kyc_status(request: Request):
    """Return current user's most recent KYC submission (if any)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM kyc_verifications WHERE user_id = $1
            ORDER BY created_at DESC LIMIT 1
        """, user['user_id'])
    if not row:
        return {"status": "none"}
    return _kyc_row_to_public(row)


@router.post("/submit")
async def submit_kyc(
    request: Request,
    full_name: str = Form(...),
    id_type: str = Form(...),
    id_number: str = Form(...),
    id_photo: UploadFile = File(...),
    selfie: UploadFile = File(...),
    id_back_photo: Optional[UploadFile] = File(None),
):
    """Submit a new KYC. Recto required for all types; verso required only
    for `national_id` and `drivers_license`. Auto-compresses inputs and
    runs an AI pre-validation pass (informational only — admin still has
    final say)."""
    user = await get_current_user(request)
    await _ensure_iter172_columns()

    id_type = (id_type or "").lower().strip()
    if id_type not in _ALLOWED_ID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Type de pièce invalide. Autorisés: {sorted(_ALLOWED_ID_TYPES)}")
    full_name = (full_name or "").strip()[:255]
    id_number = (id_number or "").strip()[:128]
    if len(full_name) < 3 or len(id_number) < 3:
        raise HTTPException(
            status_code=400,
            detail="Nom complet et numéro de pièce obligatoires")

    if id_type in _DUAL_SIDE_TYPES and id_back_photo is None:
        raise HTTPException(
            status_code=400,
            detail="Le verso de la pièce est requis pour ce type de document")

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT status FROM kyc_verifications WHERE user_id = $1 "
            "ORDER BY created_at DESC LIMIT 1", user['user_id'])
        if existing and existing['status'] == 'approved':
            raise HTTPException(status_code=400, detail="Vous êtes déjà vérifié.")
        if existing and existing['status'] == 'pending':
            raise HTTPException(status_code=400, detail="Une soumission est déjà en cours d'examen.")

    id_url, id_preview, id_bytes, id_preview_bytes = await _save_kyc_image(id_photo, "kyc_id")
    selfie_url, selfie_preview, selfie_bytes, selfie_preview_bytes = await _save_kyc_image(selfie, "kyc_selfie")
    id_back_url = None
    id_back_preview = None
    id_back_bytes = None
    id_back_preview_bytes = None
    if id_back_photo:
        id_back_url, id_back_preview, id_back_bytes, id_back_preview_bytes = await _save_kyc_image(
            id_back_photo, "kyc_id_back")

    # AI pre-validation — never blocks submission. Result is informational.
    ai_payload: dict = {}
    risk = "medium"
    alerts: list = []
    try:
        from services.kyc_ai_validator import analyze_kyc
        audit = await analyze_kyc(
            id_image_bytes=id_bytes, selfie_bytes=selfie_bytes,
            id_back_bytes=id_back_bytes, id_type=id_type,
        )
        ai_payload = audit
        risk = audit.get("risk_score", "medium")
        alerts = audit.get("alerts", [])
    except Exception as e:
        logger.warning(f"[kyc] AI audit failed (non-blocking): {e}")
        alerts = ["Pré-validation IA indisponible"]

    kyc_id = f"kyc_{uuid.uuid4().hex[:16]}"
    import json as _json
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO kyc_verifications
                (kyc_id, user_id, full_name, id_type, id_number,
                 id_photo_url, id_back_photo_url, selfie_url,
                 preview_id_url, preview_id_back_url, preview_selfie_url,
                 ai_risk_score, ai_alerts, ai_payload, status,
                 id_photo_bytes, id_back_photo_bytes, selfie_bytes,
                 preview_id_bytes, preview_id_back_bytes, preview_selfie_bytes)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,'pending',
                    $15,$16,$17,$18,$19,$20)
        """, kyc_id, user['user_id'], full_name, id_type, id_number,
            id_url, id_back_url, selfie_url,
            id_preview, id_back_preview, selfie_preview,
            risk, _json.dumps(alerts), _json.dumps(ai_payload),
            id_bytes, id_back_bytes, selfie_bytes,
            id_preview_bytes, id_back_preview_bytes, selfie_preview_bytes)

    logger.info(f"KYC {kyc_id} received (user={user['user_id']}, "
                f"type={id_type}, risk={risk}, alerts={len(alerts)})")
    return {
        "kyc_id": kyc_id, "status": "pending",
        "ai_risk_score": risk, "ai_alerts": alerts,
        "message": "Soumission reçue — examen sous 24h",
    }


# ============== ADMIN ENDPOINTS ==============
@router.get("/admin/pending")
async def list_pending_kyc(request: Request):
    """Admin: list all pending KYC submissions for review with AI hints."""
    await require_admin(request)
    await _ensure_iter172_columns()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT k.*, u.email, u.username, u.phone_number, u.country_code
            FROM kyc_verifications k
            JOIN users u ON k.user_id = u.user_id
            WHERE k.status = 'pending'
            ORDER BY k.created_at ASC
        """)

    import json as _json

    def _coerce_json(v, default):
        if v is None:
            return default
        if isinstance(v, (list, dict)):
            return v
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return default
        return default

    return {
        "submissions": [{
            "kyc_id": r['kyc_id'],
            "user_id": r['user_id'],
            "email": r['email'],
            "username": r['username'],
            "phone_number": r['phone_number'] or '',
            "country_code": r['country_code'] or '',
            "full_name": r['full_name'],
            "id_type": r['id_type'],
            "id_number": r['id_number'],
            # iter214 — durable DB-backed image URLs (cross-pod safe).
            "id_photo_url":      f"/api/kyc/admin/{r['kyc_id']}/image/id",
            "id_back_photo_url": f"/api/kyc/admin/{r['kyc_id']}/image/id_back",
            "selfie_url":        f"/api/kyc/admin/{r['kyc_id']}/image/selfie",
            "preview_id_url":      f"/api/kyc/admin/{r['kyc_id']}/image/id?preview=true",
            "preview_id_back_url": f"/api/kyc/admin/{r['kyc_id']}/image/id_back?preview=true",
            "preview_selfie_url":  f"/api/kyc/admin/{r['kyc_id']}/image/selfie?preview=true",
            # Legacy disk URLs — kept for debugging / direct access.
            "legacy_id_photo_url":      r['id_photo_url'],
            "legacy_id_back_photo_url": r['id_back_photo_url'],
            "legacy_selfie_url":        r['selfie_url'],
            "ai_risk_score": r['ai_risk_score'] or 'unknown',
            "ai_alerts": _coerce_json(r['ai_alerts'], []),
            "ai_payload": _coerce_json(r['ai_payload'], {}),
            "created_at": r['created_at'].isoformat(),
        } for r in rows]
    }


@router.post("/admin/{kyc_id}/approve")
async def approve_kyc(kyc_id: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT k.user_id, k.status, u.email, u.first_name "
            "FROM kyc_verifications k JOIN users u ON u.user_id = k.user_id "
            "WHERE k.kyc_id = $1", kyc_id)
        if not row:
            raise HTTPException(status_code=404, detail="KYC introuvable")
        if row['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail=f"Approbation impossible — statut actuel: {row['status']}")
        await conn.execute("""
            UPDATE kyc_verifications SET status = 'approved',
                   reviewed_by = $1, reviewed_at = $2 WHERE kyc_id = $3
        """, admin['user_id'], datetime.now(timezone.utc), kyc_id)
        await conn.execute("UPDATE users SET is_verified = TRUE WHERE user_id = $1",
                            row['user_id'])
        try:
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'kyc_approved', 'Identité vérifiée',
                        'Votre KYC a été approuvé. Vous pouvez désormais effectuer des retraits depuis votre wallet.')
            """, f"notif_{uuid.uuid4().hex[:12]}", row['user_id'])
        except Exception as e:
            logger.warning(f"notif create failed: {e}")
    # iter173 — Send the approval email outside the txn so DB success is
    # never coupled to email delivery.
    try:
        from services.kyc_email import send_kyc_approved_email
        await send_kyc_approved_email(
            to=row['email'], first_name=row['first_name'] or '')
    except Exception as e:
        logger.warning(f"[kyc] approval email failed: {e}")
    return {"status": "approved", "kyc_id": kyc_id}


@router.post("/admin/{kyc_id}/reject")
async def reject_kyc(kyc_id: str, req: RejectRequest, request: Request):
    admin = await require_admin(request)
    reason = (req.reason or "").strip()[:500]
    if not reason:
        raise HTTPException(status_code=400, detail="Motif de rejet obligatoire")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT k.user_id, k.status, u.email, u.first_name "
            "FROM kyc_verifications k JOIN users u ON u.user_id = k.user_id "
            "WHERE k.kyc_id = $1", kyc_id)
        if not row:
            raise HTTPException(status_code=404, detail="KYC introuvable")
        if row['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail=f"Rejet impossible — statut actuel: {row['status']}")
        await conn.execute("""
            UPDATE kyc_verifications SET status = 'rejected', rejection_reason = $1,
                   reviewed_by = $2, reviewed_at = $3 WHERE kyc_id = $4
        """, reason, admin['user_id'], datetime.now(timezone.utc), kyc_id)
        try:
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'kyc_rejected', 'Vérification d''identité refusée', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", row['user_id'],
                f"Votre KYC a été refusé. Motif : {reason}")
        except Exception as e:
            logger.warning(f"notif create failed: {e}")
    try:
        from services.kyc_email import send_kyc_rejected_email
        await send_kyc_rejected_email(
            to=row['email'], first_name=row['first_name'] or '', reason=reason)
    except Exception as e:
        logger.warning(f"[kyc] rejection email failed: {e}")
    return {"status": "rejected", "kyc_id": kyc_id}


# Helper for other modules (e.g., wallet.py) to gate withdrawals.
async def is_user_kyc_approved(conn, user_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM kyc_verifications WHERE user_id = $1 "
        "AND status = 'approved' LIMIT 1", user_id)
    return bool(row)


# ═════════════════════════════════════════════════════════════════════
# iter214 — DB-backed image serving + audit trail
# ═════════════════════════════════════════════════════════════════════
_VARIANT_COLS = {
    # variant key → (full column, preview column)
    "id":      ("id_photo_bytes",      "preview_id_bytes"),
    "id_back": ("id_back_photo_bytes", "preview_id_back_bytes"),
    "selfie":  ("selfie_bytes",        "preview_selfie_bytes"),
}


@router.get("/admin/{kyc_id}/image/{variant}")
async def admin_kyc_image(
    kyc_id: str, variant: str, request: Request, preview: bool = False,
):
    """iter214 — Serve KYC images from the DB (bytea) so they stay
    readable after pod rotations. Falls back to the local disk file (if
    present) — disk is just a warm cache.
    """
    from fastapi.responses import Response as FResponse
    await require_admin(request)
    if variant not in _VARIANT_COLS:
        raise HTTPException(status_code=400, detail="Variant inconnu")
    full_col, preview_col = _VARIANT_COLS[variant]
    col = preview_col if preview else full_col
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {col} AS bytes FROM kyc_verifications WHERE kyc_id = $1",
            kyc_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="KYC introuvable")
    blob = row["bytes"]
    if not blob:
        # iter239q — Last-chance fallback before declaring the file lost:
        # try the legacy disk path stored in `*_url` columns. These point
        # to `/api/upload/files/<name>` which maps to the local
        # `backend/uploads/` directory. If the pod still has the file (or
        # it was migrated to R2), serve it directly.
        legacy_col = {
            "id":      "id_photo_url",
            "id_back": "id_back_photo_url",
            "selfie":  "selfie_url",
        }[variant]
        async with pool.acquire() as conn:
            legacy_row = await conn.fetchrow(
                f"SELECT {legacy_col} AS u FROM kyc_verifications WHERE kyc_id = $1",
                kyc_id,
            )
        legacy_url = (legacy_row or {}).get("u") if legacy_row else None
        if legacy_url and legacy_url.startswith("/api/upload/files/"):
            filename = legacy_url.rsplit("/", 1)[-1]
            local_path = _UPLOAD_DIR / filename
            if local_path.exists() and local_path.is_file():
                try:
                    return FResponse(
                        content=local_path.read_bytes(),
                        media_type="image/jpeg",
                        headers={
                            "Cache-Control": "private, max-age=3600",
                            "X-Content-Type-Options": "nosniff",
                            "X-Japap-Source": "legacy-disk",
                        },
                    )
                except Exception as _e:  # noqa: BLE001
                    logger.warning("[kyc-legacy-disk] read failed %s: %s",
                                   local_path, _e)
        # Legacy records submitted before iter214 have no DB bytes.
        # Local disk files for these usually vanished with pod rotation
        # so returning 410 Gone is the honest answer — the frontend
        # renders a "Dossier antérieur à iter214, fichiers perdus" card.
        raise HTTPException(
            status_code=410,
            detail="Fichier indisponible (soumission antérieure à iter214 — "
                   "fichiers perdus lors d'une rotation de pod Kubernetes)")
    return FResponse(
        content=bytes(blob),
        media_type="image/jpeg",
        headers={
            # Cache aggressively — the bytes are immutable per kyc_id/variant.
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/admin/history")
async def kyc_admin_history(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
):
    """iter214 — Archived KYC decisions (approved + rejected) for legal
    traceability. Supports filtering by status (`approved` / `rejected` /
    empty = both) and a fuzzy search on name / email / username.
    """
    await require_admin(request)
    await _ensure_iter172_columns()
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 20), 100))
    offset = (page - 1) * limit

    where = ["k.status IN ('approved','rejected')"]
    args: list = []
    if status in ("approved", "rejected"):
        where.append(f"k.status = ${len(args)+1}")
        args.append(status)
    if search:
        args.append(f"%{search.strip().lower()}%")
        idx = len(args)
        where.append(
            f"(LOWER(k.full_name) LIKE ${idx} OR LOWER(u.email) LIKE ${idx} "
            f"OR LOWER(u.username) LIKE ${idx})"
        )
    where_sql = " AND ".join(where)

    pool = await get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            f"SELECT COUNT(*) AS n FROM kyc_verifications k "
            f"JOIN users u ON u.user_id = k.user_id WHERE {where_sql}",
            *args,
        )
        args2 = list(args) + [limit, offset]
        rows = await conn.fetch(
            f"""
            SELECT k.kyc_id, k.user_id, k.full_name, k.id_type, k.id_number,
                   k.status, k.rejection_reason, k.reviewed_by, k.reviewed_at,
                   k.ai_risk_score, k.created_at,
                   u.email, u.username, u.phone_number, u.country_code,
                   admin_u.email AS reviewer_email
            FROM kyc_verifications k
            JOIN users u ON u.user_id = k.user_id
            LEFT JOIN users admin_u ON admin_u.user_id = k.reviewed_by
            WHERE {where_sql}
            ORDER BY COALESCE(k.reviewed_at, k.created_at) DESC
            LIMIT ${len(args)+1} OFFSET ${len(args)+2}
            """,
            *args2,
        )

    return {
        "total": total_row["n"],
        "page": page,
        "limit": limit,
        "items": [{
            "kyc_id": r["kyc_id"],
            "user_id": r["user_id"],
            "full_name": r["full_name"],
            "email": r["email"],
            "username": r["username"],
            "phone_number": r["phone_number"] or "",
            "country_code": r["country_code"] or "",
            "id_type": r["id_type"],
            "id_number": r["id_number"],
            "status": r["status"],
            "rejection_reason": r["rejection_reason"] or "",
            "reviewer_email": r["reviewer_email"] or "",
            "ai_risk_score": r["ai_risk_score"] or "unknown",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
        } for r in rows],
    }


@router.get("/admin/{kyc_id}")
async def kyc_admin_detail(kyc_id: str, request: Request):
    """iter214 — Full detail view for a single KYC (used by the history
    modal to show archived dossiers with their images).
    """
    await require_admin(request)
    await _ensure_iter172_columns()
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT k.*, u.email, u.username, u.phone_number, u.country_code,
                   admin_u.email AS reviewer_email
            FROM kyc_verifications k
            JOIN users u ON u.user_id = k.user_id
            LEFT JOIN users admin_u ON admin_u.user_id = k.reviewed_by
            WHERE k.kyc_id = $1
            """, kyc_id,
        )
    if not r:
        raise HTTPException(status_code=404, detail="KYC introuvable")

    import json as _json

    def _coerce_json(v, default):
        if v is None:
            return default
        if isinstance(v, (list, dict)):
            return v
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return default
        return default

    # We advertise the durable DB-backed image URL so the frontend uses
    # it by default. The legacy /api/upload/files/... URL is still
    # returned for transitional compatibility / direct-link debugging.
    base = f"/api/kyc/admin/{kyc_id}/image"
    return {
        "kyc_id": r["kyc_id"],
        "user_id": r["user_id"],
        "full_name": r["full_name"],
        "email": r["email"],
        "username": r["username"],
        "phone_number": r["phone_number"] or "",
        "country_code": r["country_code"] or "",
        "id_type": r["id_type"],
        "id_number": r["id_number"],
        "status": r["status"],
        "rejection_reason": r["rejection_reason"] or "",
        "reviewer_email": r["reviewer_email"] or "",
        "ai_risk_score": r["ai_risk_score"] or "unknown",
        "ai_alerts": _coerce_json(r["ai_alerts"], []),
        "ai_payload": _coerce_json(r["ai_payload"], {}),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
        # Durable DB-backed URLs (iter214)
        "id_photo_url":      f"{base}/id",
        "id_back_photo_url": f"{base}/id_back",
        "selfie_url":        f"{base}/selfie",
        "preview_id_url":      f"{base}/id?preview=true",
        "preview_id_back_url": f"{base}/id_back?preview=true",
        "preview_selfie_url":  f"{base}/selfie?preview=true",
        # Legacy disk-based URLs (best-effort, may 404)
        "legacy_id_photo_url":      r["id_photo_url"],
        "legacy_id_back_photo_url": r["id_back_photo_url"],
        "legacy_selfie_url":        r["selfie_url"],
    }
