"""
Groups & Pages — Phase 3 of Feed refonte.
Social groups : private/public communities of users with roles (owner/admin/member).
Social pages  : public pages owned by a user (brand, personality, org).

Endpoints:
  GET    /api/groups                    list (my memberships + public)
  POST   /api/groups                    create
  GET    /api/groups/{gid}              detail
  POST   /api/groups/{gid}/join
  POST   /api/groups/{gid}/leave
  GET    /api/groups/{gid}/members
  GET    /api/groups/{gid}/posts
  POST   /api/groups/{gid}/posts        create group post

  GET    /api/pages
  POST   /api/pages
  GET    /api/pages/{pid}
  POST   /api/pages/{pid}/follow
  POST   /api/pages/{pid}/unfollow
  GET    /api/pages/{pid}/posts
  POST   /api/pages/{pid}/posts
"""
import json as _json
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from database import get_pool
from routes.auth import get_current_user

router = APIRouter(prefix="/api", tags=["groups-pages"])


# ============================================================
# Models
# ============================================================
class GroupCreate(BaseModel):
    name: str
    description: str = ""
    privacy: str = "public"  # public | private
    avatar: str = ""
    cover: str = ""


class PageCreate(BaseModel):
    name: str
    category: str = "other"
    description: str = ""
    avatar: str = ""
    cover: str = ""


class PostInSurface(BaseModel):
    text: str = ""
    media: list = []


# ============================================================
# GROUPS
# ============================================================
@router.get("/groups")
async def list_groups(request: Request,
                      mine: bool = Query(False),
                      q: Optional[str] = Query(None),
                      limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        params = []
        where = "WHERE 1=1"
        if mine:
            where += " AND g.group_id IN (SELECT group_id FROM group_members WHERE user_id = $1)"
            params.append(user['user_id'])
        else:
            where += " AND g.privacy = 'public'"
        if q:
            params.append(f"%{q}%")
            where += f" AND g.name ILIKE ${len(params)}"
        params.append(limit)
        rows = await conn.fetch(f"""
            SELECT g.*, EXISTS(
                SELECT 1 FROM group_members m WHERE m.group_id = g.group_id AND m.user_id = '{user['user_id']}'
            ) AS is_member
            FROM social_groups g
            {where}
            ORDER BY g.members_count DESC, g.created_at DESC
            LIMIT ${len(params)}
        """, *params)
        items = []
        for r in rows:
            d = dict(r)
            d['created_at'] = d['created_at'].isoformat()
            items.append(d)
    return {"items": items}


# NOTE: specific routes /suggestions and /join-all MUST stay above /{gid}
# so FastAPI's path matcher hits them first instead of treating
# "suggestions" as a group_id path parameter.
@router.get("/groups/suggestions")
async def group_suggestions(request: Request, limit: int = Query(5, ge=1, le=10)):
    """Returns relevant group suggestions for the onboarding modal.

    Ranking: country match > language match > generic activity (members, posts).
    Already-joined groups are excluded."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        me = await conn.fetchrow(
            "SELECT country, language FROM users WHERE user_id = $1", user['user_id']
        )
        country = (me['country'] or '').strip() if me else ''
        language = (me['language'] or '').strip() if me else ''
        rows = await conn.fetch("""
            SELECT g.*,
                   EXISTS(SELECT 1 FROM group_members m
                          WHERE m.group_id=g.group_id AND m.user_id=$1) AS is_member,
                   CASE
                     WHEN $2 <> '' AND (g.name ILIKE '%' || $2 || '%' OR g.description ILIKE '%' || $2 || '%') THEN 3
                     WHEN $3 <> '' AND (g.name ILIKE '%' || $3 || '%' OR g.description ILIKE '%' || $3 || '%') THEN 2
                     ELSE 1
                   END AS relevance
            FROM social_groups g
            WHERE g.privacy = 'public'
              AND NOT EXISTS(SELECT 1 FROM group_members m
                             WHERE m.group_id=g.group_id AND m.user_id=$1)
            ORDER BY relevance DESC, g.members_count DESC, g.posts_count DESC, g.created_at DESC
            LIMIT $4
        """, user['user_id'], country, language, limit)
    return {
        "items": [{
            "group_id": r['group_id'],
            "name": r['name'],
            "description": r['description'],
            "avatar": r['avatar'],
            "members_count": r['members_count'],
            "posts_count": r['posts_count'],
            "is_member": r['is_member'],
        } for r in rows],
        "country": country,
        "language": language,
    }


@router.post("/groups/join-all")
async def groups_join_all(request: Request):
    """Joins the user to up to 5 most-active public groups. Idempotent per group."""
    user = await get_current_user(request)
    pool = await get_pool()
    joined = []
    async with pool.acquire() as conn:
        suggestions = await conn.fetch("""
            SELECT g.group_id FROM social_groups g
            WHERE g.privacy = 'public'
              AND NOT EXISTS(SELECT 1 FROM group_members m
                             WHERE m.group_id=g.group_id AND m.user_id=$1)
            ORDER BY g.members_count DESC, g.posts_count DESC LIMIT 5
        """, user['user_id'])
        for row in suggestions:
            gid = row['group_id']
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO group_members (group_id, user_id, role) VALUES ($1, $2, 'member') "
                    "ON CONFLICT DO NOTHING",
                    gid, user['user_id'],
                )
                await conn.execute(
                    "UPDATE social_groups SET members_count = members_count + 1 WHERE group_id=$1", gid,
                )
            joined.append(gid)
    return {"joined_count": len(joined), "group_ids": joined}


@router.post("/groups")
async def create_group(req: GroupCreate, request: Request):
    user = await get_current_user(request)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Nom du groupe requis")
    if req.privacy not in ("public", "private"):
        raise HTTPException(status_code=400, detail="privacy doit être 'public' ou 'private'")
    gid = f"grp_{uuid.uuid4().hex[:12]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO social_groups (group_id, owner_id, name, description, privacy, avatar, cover)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                gid, user['user_id'], req.name.strip(), req.description, req.privacy, req.avatar, req.cover,
            )
            await conn.execute(
                "INSERT INTO group_members (group_id, user_id, role) VALUES ($1, $2, 'owner')",
                gid, user['user_id'],
            )
    return {"group_id": gid, "name": req.name}


@router.get("/groups/{gid}")
async def group_detail(gid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT g.*, EXISTS(SELECT 1 FROM group_members m WHERE m.group_id=g.group_id AND m.user_id=$2) AS is_member,
                   (SELECT role FROM group_members m WHERE m.group_id=g.group_id AND m.user_id=$2) AS my_role
            FROM social_groups g WHERE g.group_id=$1
        """, gid, user['user_id'])
        if not row:
            raise HTTPException(status_code=404, detail="Groupe introuvable")
        d = dict(row)
        d['created_at'] = d['created_at'].isoformat()
    return d


@router.post("/groups/{gid}/join")
async def group_join(gid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT * FROM social_groups WHERE group_id=$1", gid)
        if not g:
            raise HTTPException(status_code=404, detail="Groupe introuvable")
        exists = await conn.fetchval(
            "SELECT 1 FROM group_members WHERE group_id=$1 AND user_id=$2", gid, user['user_id']
        )
        if exists:
            return {"joined": False, "already": True}
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO group_members (group_id, user_id, role) VALUES ($1, $2, 'member')",
                gid, user['user_id'],
            )
            await conn.execute(
                "UPDATE social_groups SET members_count = members_count + 1 WHERE group_id=$1", gid,
            )
    return {"joined": True}


@router.post("/groups/{gid}/leave")
async def group_leave(gid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT owner_id FROM social_groups WHERE group_id=$1", gid)
        if not g:
            raise HTTPException(status_code=404, detail="Groupe introuvable")
        if g['owner_id'] == user['user_id']:
            raise HTTPException(status_code=400, detail="Le propriétaire ne peut pas quitter son groupe")
        async with conn.transaction():
            res = await conn.execute(
                "DELETE FROM group_members WHERE group_id=$1 AND user_id=$2", gid, user['user_id'],
            )
            if res.endswith(" 0"):
                return {"left": False}
            await conn.execute(
                "UPDATE social_groups SET members_count = GREATEST(0, members_count - 1) WHERE group_id=$1", gid,
            )
    return {"left": True}


@router.get("/groups/{gid}/members")
async def group_members(gid: str, request: Request, limit: int = Query(50, ge=1, le=200)):
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.role, m.joined_at, u.user_id, u.first_name, u.last_name, u.avatar, u.username
            FROM group_members m JOIN users u ON u.user_id = m.user_id
            WHERE m.group_id=$1 ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, m.joined_at
            LIMIT $2
        """, gid, limit)
        return {"items": [{**dict(r), "joined_at": r["joined_at"].isoformat()} for r in rows]}


@router.get("/groups/{gid}/posts")
async def group_posts(gid: str, request: Request, limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Verify membership for private groups
        g = await conn.fetchrow("SELECT privacy FROM social_groups WHERE group_id=$1", gid)
        if not g:
            raise HTTPException(status_code=404, detail="Groupe introuvable")
        if g['privacy'] == 'private':
            is_member = await conn.fetchval(
                "SELECT 1 FROM group_members WHERE group_id=$1 AND user_id=$2", gid, user['user_id']
            )
            if not is_member:
                raise HTTPException(status_code=403, detail="Accès réservé aux membres")
        rows = await conn.fetch("""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username,
                   EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id=p.post_id AND pl.user_id=$2) AS is_liked
            FROM posts p JOIN users u ON u.user_id=p.user_id
            WHERE p.target_group_id=$1
            ORDER BY p.created_at DESC LIMIT $3
        """, gid, user['user_id'], limit)
    return {"items": [_normalize_post(dict(r)) for r in rows]}


@router.post("/groups/{gid}/posts")
async def group_post_create(gid: str, req: PostInSurface, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        is_member = await conn.fetchval(
            "SELECT 1 FROM group_members WHERE group_id=$1 AND user_id=$2", gid, user['user_id']
        )
        if not is_member:
            raise HTTPException(status_code=403, detail="Rejoignez le groupe pour publier")
        pid = f"post_{uuid.uuid4().hex[:12]}"
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO posts (post_id, user_id, text, media, type, visibility, target_group_id)
                   VALUES ($1, $2, $3, $4::jsonb, 'post', 'group', $5)""",
                pid, user['user_id'], req.text, _json.dumps(req.media or []), gid,
            )
            await conn.execute(
                "UPDATE social_groups SET posts_count = posts_count + 1 WHERE group_id=$1", gid,
            )
    return {"post_id": pid, "group_id": gid}


# ============================================================
# PAGES
# ============================================================
@router.get("/pages")
async def list_pages(request: Request,
                     mine: bool = Query(False),
                     q: Optional[str] = None,
                     limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        params = []
        where = "WHERE 1=1"
        if mine:
            where += " AND (p.owner_id=$1 OR p.page_id IN (SELECT page_id FROM page_followers WHERE user_id=$1))"
            params.append(user['user_id'])
        if q:
            params.append(f"%{q}%")
            where += f" AND p.name ILIKE ${len(params)}"
        params.append(limit)
        rows = await conn.fetch(f"""
            SELECT p.*, EXISTS(
                SELECT 1 FROM page_followers f WHERE f.page_id=p.page_id AND f.user_id='{user['user_id']}'
            ) AS is_following
            FROM social_pages p {where}
            ORDER BY p.followers_count DESC, p.created_at DESC
            LIMIT ${len(params)}
        """, *params)
    return {"items": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.post("/pages")
async def create_page(req: PageCreate, request: Request):
    user = await get_current_user(request)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Nom de la page requis")
    pid = f"pg_{uuid.uuid4().hex[:12]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO social_pages (page_id, owner_id, name, category, description, avatar, cover)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            pid, user['user_id'], req.name.strip(), req.category, req.description, req.avatar, req.cover,
        )
    return {"page_id": pid, "name": req.name}


@router.get("/pages/{pid}")
async def page_detail(pid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT p.*, EXISTS(SELECT 1 FROM page_followers f WHERE f.page_id=p.page_id AND f.user_id=$2) AS is_following,
                   (p.owner_id = $2) AS is_owner
            FROM social_pages p WHERE p.page_id=$1
        """, pid, user['user_id'])
        if not row:
            raise HTTPException(status_code=404, detail="Page introuvable")
        d = dict(row)
        d['created_at'] = d['created_at'].isoformat()
    return d


@router.post("/pages/{pid}/follow")
async def page_follow(pid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM social_pages WHERE page_id=$1", pid)
        if not exists:
            raise HTTPException(status_code=404, detail="Page introuvable")
        already = await conn.fetchval(
            "SELECT 1 FROM page_followers WHERE page_id=$1 AND user_id=$2", pid, user['user_id'],
        )
        if already:
            return {"followed": False, "already": True}
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO page_followers (page_id, user_id) VALUES ($1, $2)", pid, user['user_id'],
            )
            await conn.execute(
                "UPDATE social_pages SET followers_count = followers_count + 1 WHERE page_id=$1", pid,
            )
    return {"followed": True}


@router.post("/pages/{pid}/unfollow")
async def page_unfollow(pid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.execute(
                "DELETE FROM page_followers WHERE page_id=$1 AND user_id=$2", pid, user['user_id'],
            )
            if res.endswith(" 0"):
                return {"followed": False}
            await conn.execute(
                "UPDATE social_pages SET followers_count = GREATEST(0, followers_count - 1) WHERE page_id=$1", pid,
            )
    return {"followed": False}


@router.get("/pages/{pid}/posts")
async def page_posts(pid: str, request: Request, limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username,
                   EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id=p.post_id AND pl.user_id=$2) AS is_liked
            FROM posts p JOIN users u ON u.user_id=p.user_id
            WHERE p.target_page_id=$1 ORDER BY p.created_at DESC LIMIT $3
        """, pid, user['user_id'], limit)
    return {"items": [_normalize_post(dict(r)) for r in rows]}


@router.post("/pages/{pid}/posts")
async def page_post_create(pid: str, req: PostInSurface, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        page = await conn.fetchrow("SELECT owner_id FROM social_pages WHERE page_id=$1", pid)
        if not page:
            raise HTTPException(status_code=404, detail="Page introuvable")
        if page['owner_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Seul le propriétaire peut publier sur cette page")
        pid_post = f"post_{uuid.uuid4().hex[:12]}"
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO posts (post_id, user_id, text, media, type, visibility, target_page_id)
                   VALUES ($1, $2, $3, $4::jsonb, 'post', 'page', $5)""",
                pid_post, user['user_id'], req.text, _json.dumps(req.media or []), pid,
            )
            await conn.execute(
                "UPDATE social_pages SET posts_count = posts_count + 1 WHERE page_id=$1", pid,
            )
    return {"post_id": pid_post, "page_id": pid}


# ============================================================
# Helpers
# ============================================================
def _normalize_post(d: dict) -> dict:
    from decimal import Decimal
    if d.get('created_at') and hasattr(d['created_at'], 'isoformat'):
        d['created_at'] = d['created_at'].isoformat()
    if d.get('updated_at') and hasattr(d['updated_at'], 'isoformat'):
        d['updated_at'] = d['updated_at'].isoformat()
    if isinstance(d.get('media'), str):
        try:
            d['media'] = _json.loads(d['media'])
        except Exception:
            d['media'] = []
    if isinstance(d.get('tips_total'), Decimal):
        d['tips_total'] = str(d['tips_total'])
    return d
