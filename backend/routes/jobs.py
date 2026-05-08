"""
JAPAP Messenger — Jobs on-demand Module
Catégories: tech, sales, marketing, logistics, services, craft, other
Types: full_time, part_time, contract, internship, freelance
Wallet: frais publication 500 XAF (futur), gratuit phase 1
"""
import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

CATEGORIES = ["tech", "sales", "marketing", "logistics", "services", "craft", "other",
              "design", "writing", "video", "translation", "consulting",
              "housing", "vehicles", "goods", "announcement",
              # iter166 — scholarships pseudo-categories
              "engineering", "medicine", "business", "arts", "science", "law"]
TYPES = ["full_time", "part_time", "contract", "internship", "freelance"]
OFFER_TYPES = ["job", "mission", "annonce", "scholarship"]
LEVELS_OF_STUDY = ["bac", "licence", "master", "phd", "postdoc", "other"]


class CreateJobRequest(BaseModel):
    title: str
    description: str = ""
    category: str = "other"
    type: str = "full_time"
    location: str = ""
    salary_min: float = 0
    salary_max: float = 0
    salary_usd: Optional[float] = None  # iter166 — canonical USD salary
    remote: bool = False
    offer_type: str = "job"
    budget_usd: float = 0
    deadline: Optional[str] = None
    # iter166 — premium fields
    company_name: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    # Scholarship-specific
    university_name: Optional[str] = None
    country_of_study: Optional[str] = None
    level_of_study: Optional[str] = None
    field_of_study: Optional[str] = None
    application_url: Optional[str] = None


class UpdateJobRequest(BaseModel):
    """Editable fields. All optional — only sent values are written."""
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    type: Optional[str] = None
    location: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_usd: Optional[float] = None
    remote: Optional[bool] = None
    budget_usd: Optional[float] = None
    deadline: Optional[str] = None
    company_name: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    university_name: Optional[str] = None
    country_of_study: Optional[str] = None
    level_of_study: Optional[str] = None
    field_of_study: Optional[str] = None
    application_url: Optional[str] = None
    is_active: Optional[bool] = None
    status: Optional[str] = None  # 'open' | 'closed'


class ApplyJobRequest(BaseModel):
    cover_letter: str = ""


@router.get("/categories")
async def list_categories():
    return [
        {"id": "tech", "name": "Tech / IT", "color": "#4A90E2"},
        {"id": "sales", "name": "Commerce", "color": "#10B981"},
        {"id": "marketing", "name": "Marketing", "color": "#E01C2E"},
        {"id": "logistics", "name": "Logistique", "color": "#F59E0B"},
        {"id": "services", "name": "Services", "color": "#9333EA"},
        {"id": "craft", "name": "Artisanat", "color": "#DC2626"},
        {"id": "design", "name": "Design", "color": "#EC4899"},
        {"id": "writing", "name": "Rédaction", "color": "#8B5CF6"},
        {"id": "video", "name": "Vidéo / Photo", "color": "#0EA5E9"},
        {"id": "translation", "name": "Traduction", "color": "#14B8A6"},
        {"id": "consulting", "name": "Conseil", "color": "#7C3AED"},
        {"id": "housing", "name": "Logement", "color": "#F59E0B"},
        {"id": "vehicles", "name": "Véhicules", "color": "#6366F1"},
        {"id": "goods", "name": "Objets", "color": "#64748B"},
        {"id": "announcement", "name": "Annonces", "color": "#6B7280"},
        {"id": "other", "name": "Autres", "color": "#6B7280"},
    ]


@router.get("/list")
async def list_jobs(request: Request, category: Optional[str] = None, search: Optional[str] = None,
                    offer_type: Optional[str] = None, remote: Optional[bool] = None,
                    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50)):
    await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base = "FROM jobs j JOIN users u ON j.poster_id = u.user_id WHERE j.status = 'open'"
        params = []
        if category and category in CATEGORIES:
            params.append(category)
            base += f" AND j.category = ${len(params)}"
        if search:
            params.append(f"%{search.lower()}%")
            base += f" AND (LOWER(j.title) LIKE ${len(params)} OR LOWER(j.description) LIKE ${len(params)})"
        if offer_type and offer_type in OFFER_TYPES:
            params.append(offer_type)
            base += f" AND j.offer_type = ${len(params)}"
        if remote is True:
            base += " AND j.remote = TRUE"
        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        q = params + [limit, offset]
        rows = await conn.fetch(f"""
            SELECT j.*, u.first_name, u.last_name, u.avatar, u.username, u.is_pro
            {base} ORDER BY j.created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}
        """, *q)
        jobs = []
        for r in rows:
            d = dict(r)
            d['salary_min'] = str(d['salary_min'])
            d['salary_max'] = str(d['salary_max'])
            d['budget_usd'] = str(d.get('budget_usd') or 0)
            d['offer_type'] = d.get('offer_type') or 'job'
            if d.get('deadline'):
                d['deadline'] = d['deadline'].isoformat()
            d['created_at'] = d['created_at'].isoformat()
            d['updated_at'] = d['updated_at'].isoformat() if d.get('updated_at') else None
            jobs.append(d)
        return {"jobs": jobs, "total": count, "page": page, "limit": limit}


@router.get("/levels")
async def list_levels():
    """iter166 — Education levels for scholarship offers."""
    return [
        {"id": "bac", "name": "Baccalauréat"},
        {"id": "licence", "name": "Licence"},
        {"id": "master", "name": "Master"},
        {"id": "phd", "name": "Doctorat / PhD"},
        {"id": "postdoc", "name": "Postdoctorat"},
        {"id": "other", "name": "Autres"},
    ]


@router.post("/create")
async def create_job(req: CreateJobRequest, request: Request):
    user = await get_current_user(request)
    if req.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Catégorie invalide")
    # `type` only required for job/mission. Scholarships ignore it.
    if req.offer_type in ("job", "mission") and req.type not in TYPES:
        raise HTTPException(status_code=400, detail="Type d'emploi invalide")
    if req.offer_type not in OFFER_TYPES:
        raise HTTPException(status_code=400, detail="offer_type invalide (job|mission|annonce|scholarship)")
    if req.offer_type == "scholarship":
        if not (req.university_name or "").strip():
            raise HTTPException(status_code=400, detail="university_name requis pour une bourse")
        if req.level_of_study and req.level_of_study not in LEVELS_OF_STUDY:
            raise HTTPException(status_code=400, detail="level_of_study invalide")
    if req.salary_max and req.salary_min and req.salary_max < req.salary_min:
        raise HTTPException(status_code=400, detail="Salaire max < min")
    deadline = None
    if req.deadline:
        try:
            deadline = datetime.fromisoformat(req.deadline.replace("Z", "+00:00")).date()
        except Exception:
            raise HTTPException(status_code=400, detail="deadline invalide (ISO 8601)")
    pool = await get_pool()
    async with pool.acquire() as conn:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        await conn.execute("""
            INSERT INTO jobs (job_id, poster_id, title, description, category, type, location,
                salary_min, salary_max, salary_usd, remote, offer_type, budget_usd, deadline,
                company_name, website, contact_email, contact_phone,
                university_name, country_of_study, level_of_study, field_of_study, application_url)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
        """, job_id, user['user_id'], req.title.strip()[:255], req.description, req.category,
             req.type, (req.location or "")[:255],
             Decimal(str(req.salary_min or 0)), Decimal(str(req.salary_max or 0)),
             Decimal(str(req.salary_usd)) if req.salary_usd is not None else None,
             req.remote, req.offer_type, Decimal(str(req.budget_usd or 0)), deadline,
             (req.company_name or None), (req.website or None),
             (req.contact_email or None), (req.contact_phone or None),
             (req.university_name or None), (req.country_of_study or None),
             (req.level_of_study or None), (req.field_of_study or None),
             (req.application_url or None))
        label = {"job": "Offre d'emploi", "mission": "Mission freelance",
                 "annonce": "Annonce", "scholarship": "Bourse d'études"}[req.offer_type]
        return {"job_id": job_id, "message": f"{label} publiée"}


@router.get("/my/postings")
async def my_postings(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM jobs WHERE poster_id = $1 ORDER BY created_at DESC
        """, user['user_id'])
        return [
            {**dict(r),
             'salary_min': str(r['salary_min']),
             'salary_max': str(r['salary_max']),
             'created_at': r['created_at'].isoformat(),
             'updated_at': r['updated_at'].isoformat()}
            for r in rows
        ]


@router.get("/my/applications")
async def my_applications(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT ja.*, j.title AS job_title, j.category, j.type, j.location
            FROM job_applications ja JOIN jobs j ON ja.job_id = j.job_id
            WHERE ja.applicant_id = $1 ORDER BY ja.created_at DESC
        """, user['user_id'])
        return [
            {
                "app_id": r['app_id'],
                "job_id": r['job_id'],
                "job_title": r['job_title'],
                "category": r['category'],
                "type": r['type'],
                "location": r['location'],
                "status": r['status'],
                "created_at": r['created_at'].isoformat(),
            }
            for r in rows
        ]


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # iter166 — increment views_count BEFORE reading the row so the
        # returned views_count reflects the current visit. Idempotent
        # per (job_id, user_id) via the job_views unique key.
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS job_views (
                    job_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(64) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (job_id, user_id)
                )
            """)
            inserted = await conn.execute(
                "INSERT INTO job_views (job_id, user_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                job_id, user['user_id'])
            if inserted.endswith(" 1"):
                await conn.execute(
                    "UPDATE jobs SET views_count = COALESCE(views_count,0) + 1 WHERE job_id = $1",
                    job_id)
        except Exception as _e:
            logger.warning(f"job view increment failed: {_e}")

        row = await conn.fetchrow("""
            SELECT j.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro
            FROM jobs j JOIN users u ON j.poster_id = u.user_id WHERE j.job_id = $1
        """, job_id)
        if not row:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        d = dict(row)
        d['salary_min'] = str(d['salary_min']) if d.get('salary_min') is not None else "0"
        d['salary_max'] = str(d['salary_max']) if d.get('salary_max') is not None else "0"
        d['salary_usd'] = str(d['salary_usd']) if d.get('salary_usd') is not None else None
        d['budget_usd'] = str(d.get('budget_usd') or 0)
        d['offer_type'] = d.get('offer_type') or 'job'
        if d.get('deadline'):
            d['deadline'] = d['deadline'].isoformat()
        d['created_at'] = d['created_at'].isoformat()
        d['updated_at'] = d['updated_at'].isoformat() if d.get('updated_at') else None
        # Has current user applied / liked?
        applied = await conn.fetchval(
            "SELECT 1 FROM job_applications WHERE job_id = $1 AND applicant_id = $2",
            job_id, user['user_id'])
        d['has_applied'] = bool(applied)
        liked = await conn.fetchval(
            "SELECT 1 FROM job_likes WHERE job_id = $1 AND user_id = $2",
            job_id, user['user_id'])
        d['has_liked'] = bool(liked)
        d['is_owner'] = row['poster_id'] == user['user_id']
        # iter166 — auto-convert salary to viewer's display currency.
        try:
            from services.currency_conversion import user_display_currency, usd_to
            display_ccy = await user_display_currency(
                user['user_id'], fallback="USD", request=request,
            )
            usd_amount = float(d['salary_usd']) if d.get('salary_usd') else None
            d['display_currency'] = display_ccy
            if usd_amount and display_ccy != "USD":
                d['salary_local'] = str(await usd_to(Decimal(str(usd_amount)), display_ccy, rounding=0))
            else:
                d['salary_local'] = d.get('salary_usd')
        except Exception as _e:
            logger.debug(f"salary conversion skipped: {_e}")
            d['display_currency'] = 'USD'
            d['salary_local'] = d.get('salary_usd')
        return d


@router.post("/{job_id}/like")
async def toggle_like(job_id: str, request: Request):
    """iter166 — Toggle like on a job offer. Returns new state + count."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM jobs WHERE job_id = $1", job_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        existing = await conn.fetchval(
            "SELECT 1 FROM job_likes WHERE job_id = $1 AND user_id = $2",
            job_id, user['user_id'])
        async with conn.transaction():
            if existing:
                await conn.execute(
                    "DELETE FROM job_likes WHERE job_id = $1 AND user_id = $2",
                    job_id, user['user_id'])
                await conn.execute(
                    "UPDATE jobs SET likes_count = GREATEST(likes_count - 1, 0) WHERE job_id = $1",
                    job_id)
                liked = False
            else:
                await conn.execute(
                    "INSERT INTO job_likes (job_id, user_id) VALUES ($1, $2)",
                    job_id, user['user_id'])
                await conn.execute(
                    "UPDATE jobs SET likes_count = COALESCE(likes_count,0) + 1 WHERE job_id = $1",
                    job_id)
                liked = True
        count = await conn.fetchval(
            "SELECT likes_count FROM jobs WHERE job_id = $1", job_id)
        return {"liked": liked, "likes_count": count or 0}


@router.put("/{job_id}")
async def update_job(job_id: str, req: UpdateJobRequest, request: Request):
    """iter166 — Owner-only edit. Only fields explicitly provided (non-None)
    are written. Status changes ('open'/'closed') and is_active toggles use
    the same endpoint."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT poster_id FROM jobs WHERE job_id = $1", job_id)
        if not row:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        if row['poster_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")

        updates = req.model_dump(exclude_unset=True)
        if 'category' in updates and updates['category'] not in CATEGORIES:
            raise HTTPException(status_code=400, detail="Catégorie invalide")
        if 'type' in updates and updates['type'] not in TYPES:
            raise HTTPException(status_code=400, detail="Type invalide")
        if 'level_of_study' in updates and updates['level_of_study'] and updates['level_of_study'] not in LEVELS_OF_STUDY:
            raise HTTPException(status_code=400, detail="level_of_study invalide")
        if 'status' in updates and updates['status'] not in ('open', 'closed'):
            raise HTTPException(status_code=400, detail="status invalide")
        if 'deadline' in updates and updates['deadline']:
            try:
                updates['deadline'] = datetime.fromisoformat(
                    updates['deadline'].replace("Z", "+00:00")).date()
            except Exception:
                raise HTTPException(status_code=400, detail="deadline invalide")
        # Decimal coercion
        for k in ('salary_min', 'salary_max', 'salary_usd', 'budget_usd'):
            if k in updates and updates[k] is not None:
                updates[k] = Decimal(str(updates[k]))

        if not updates:
            return {"updated": False}
        cols = list(updates.keys())
        sets = ", ".join(f"{c} = ${i+1}" for i, c in enumerate(cols))
        params = list(updates.values()) + [datetime.now(timezone.utc), job_id]
        await conn.execute(
            f"UPDATE jobs SET {sets}, updated_at = ${len(cols)+1} WHERE job_id = ${len(cols)+2}",
            *params)
        return {"updated": True, "fields": cols}


@router.delete("/{job_id}")
async def delete_job(job_id: str, request: Request):
    """iter166 — Owner deletes their offer (hard delete + cascade to
    applications via FK). Admins use a separate admin endpoint."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT poster_id FROM jobs WHERE job_id = $1", job_id)
        if not row:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        if row['poster_id'] != user['user_id'] and not user.get('is_admin'):
            raise HTTPException(status_code=403, detail="Non autorisé")
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM job_applications WHERE job_id = $1", job_id)
            await conn.execute("DELETE FROM job_likes WHERE job_id = $1", job_id)
            await conn.execute(
                "DELETE FROM job_views WHERE job_id = $1", job_id)
            await conn.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
        return {"deleted": True}


@router.post("/{job_id}/apply")
async def apply_job(job_id: str, req: ApplyJobRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        job = await conn.fetchrow("SELECT * FROM jobs WHERE job_id = $1 AND status = 'open'", job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Offre indisponible")
        if job['poster_id'] == user['user_id']:
            raise HTTPException(status_code=400, detail="Vous ne pouvez pas postuler à votre propre offre")
        exists = await conn.fetchval(
            "SELECT 1 FROM job_applications WHERE job_id = $1 AND applicant_id = $2",
            job_id, user['user_id'])
        if exists:
            raise HTTPException(status_code=400, detail="Vous avez déjà postulé")
        app_id = f"app_{uuid.uuid4().hex[:12]}"
        await conn.execute("""
            INSERT INTO job_applications (app_id, job_id, applicant_id, cover_letter)
            VALUES ($1, $2, $3, $4)
        """, app_id, job_id, user['user_id'], req.cover_letter[:2000])
        await conn.execute("UPDATE jobs SET applications_count = applications_count + 1 WHERE job_id = $1", job_id)
        # Notify poster
        name = f"{user['first_name']} {user['last_name']}".strip() or user['username']
        await conn.execute("""
            INSERT INTO notifications (notif_id, user_id, type, title, message)
            VALUES ($1, $2, 'job_application', 'Nouvelle candidature', $3)
        """, f"notif_{uuid.uuid4().hex[:12]}", job['poster_id'],
             f"{name} a postulé à votre offre \"{job['title']}\"")
        return {"app_id": app_id, "message": "Candidature envoyée"}


@router.get("/{job_id}/applications")
async def list_applications(job_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        job = await conn.fetchrow("SELECT * FROM jobs WHERE job_id = $1", job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        if job['poster_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Non autorisé")
        rows = await conn.fetch("""
            SELECT ja.*, u.first_name, u.last_name, u.avatar, u.username, u.email
            FROM job_applications ja JOIN users u ON ja.applicant_id = u.user_id
            WHERE ja.job_id = $1 ORDER BY ja.created_at DESC
        """, job_id)
        return [
            {
                "app_id": r['app_id'],
                "applicant": {
                    "user_id": r['applicant_id'],
                    "name": f"{r['first_name']} {r['last_name']}".strip() or r['username'],
                    "avatar": r['avatar'] or '',
                    "email": r['email'],
                },
                "cover_letter": r['cover_letter'],
                "status": r['status'],
                "created_at": r['created_at'].isoformat(),
            }
            for r in rows
        ]



# ╔══════════════════════════════════════════════════════════════════╗
# ║ iter167 — Weekly scholarship digest (admin + user prefs)         ║
# ╚══════════════════════════════════════════════════════════════════╝

class DigestPrefRequest(BaseModel):
    enabled: bool
    preferred_study_level: Optional[str] = None


@router.put("/me/scholarship-digest")
async def update_digest_pref(req: DigestPrefRequest, request: Request):
    """User opt-in/out of the weekly scholarship digest + optional level
    filter (so only relevant levels are surfaced first in the email)."""
    user = await get_current_user(request)
    if req.preferred_study_level is not None and req.preferred_study_level and req.preferred_study_level not in LEVELS_OF_STUDY:
        raise HTTPException(status_code=400, detail="preferred_study_level invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users SET notify_scholarship_digest = $1,
                                preferred_study_level = $2
               WHERE user_id = $3""",
            bool(req.enabled),
            (req.preferred_study_level or None),
            user['user_id'])
    return {"enabled": req.enabled,
            "preferred_study_level": req.preferred_study_level}


@router.get("/me/scholarship-digest")
async def get_digest_pref(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT notify_scholarship_digest, preferred_study_level "
            "FROM users WHERE user_id = $1", user['user_id'])
    return {
        "enabled": bool(row and row['notify_scholarship_digest']),
        "preferred_study_level": row['preferred_study_level'] if row else None,
    }


@router.post("/admin/scholarship-digest/send")
async def admin_send_digest_now(request: Request, force: bool = False):
    """Admin-only — trigger the weekly digest pass immediately. Used for
    testing or to recover from a missed schedule. `force=True` bypasses
    the per-user idempotency lock so the same recipient can be re-emailed."""
    user = await get_current_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin uniquement")
    from services.scholarship_digest_worker import send_weekly_digest
    stats = await send_weekly_digest(force=bool(force))
    return stats
