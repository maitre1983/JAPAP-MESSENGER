"""
Admin Stats — Phase 4 of Feed refonte.
Provides KPIs for content, engagement and top creators/posts/reels.

Endpoints (all admin-only):
  GET /api/admin/stats/content     — posts/reels/stories counts + time series
  GET /api/admin/stats/engagement  — likes/comments/shares/tips totals
  GET /api/admin/stats/top         — top posts, top reels, top creators
  GET /api/admin/stats/overview    — compact snapshot for the dashboard hero
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from database import get_pool
from routes.admin import require_admin

router = APIRouter(prefix="/api/admin/stats", tags=["admin-stats"])


PERIODS = {
    "day":   ("day",   timedelta(days=1)),
    "week":  ("day",   timedelta(days=7)),
    "month": ("day",   timedelta(days=30)),
    "year":  ("month", timedelta(days=365)),
}


def _range(period: str):
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail="period invalide (day/week/month/year)")
    trunc, delta = PERIODS[period]
    end = datetime.now(timezone.utc)
    start = end - delta
    return start, end, trunc


@router.get("/overview")
async def overview(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM users) AS total_users,
                (SELECT COUNT(*) FROM posts WHERE type != 'story' OR type IS NULL) AS total_posts,
                (SELECT COUNT(*) FROM posts WHERE type = 'reel') AS total_reels,
                (SELECT COUNT(*) FROM posts WHERE type = 'story') AS total_stories,
                (SELECT COUNT(*) FROM post_likes) AS total_likes,
                (SELECT COUNT(*) FROM post_comments) AS total_comments,
                (SELECT COALESCE(SUM(shares_count), 0) FROM posts) AS total_shares,
                (SELECT COUNT(*) FROM social_groups) AS total_groups,
                (SELECT COUNT(*) FROM social_pages) AS total_pages
        """)
        last_24h = await conn.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '24 hours') AS posts_24h,
                (SELECT COUNT(DISTINCT user_id) FROM posts WHERE created_at > NOW() - INTERVAL '24 hours') AS active_posters_24h,
                (SELECT COUNT(*) FROM post_likes WHERE created_at > NOW() - INTERVAL '24 hours') AS likes_24h,
                (SELECT COUNT(*) FROM post_comments WHERE created_at > NOW() - INTERVAL '24 hours') AS comments_24h
        """)
    return {
        "totals": {k: int(v or 0) for k, v in totals.items()},
        "last_24h": {k: int(v or 0) for k, v in last_24h.items()},
    }


@router.get("/content")
async def content_stats(request: Request,
                        period: str = Query("month"),
                        content_type: Optional[str] = Query(None)):
    """Time series of content creation.
    period: day|week|month|year
    content_type: post|reel|story (or None for all)"""
    await require_admin(request)
    start, end, trunc = _range(period)
    pool = await get_pool()
    async with pool.acquire() as conn:
        params = [start, end]
        where = "WHERE created_at >= $1 AND created_at <= $2"
        if content_type == "post":
            where += " AND (type IS NULL OR type IN ('post', 'share'))"
        elif content_type == "reel":
            where += " AND type = 'reel'"
        elif content_type == "story":
            where += " AND type = 'story'"
        rows = await conn.fetch(f"""
            SELECT DATE_TRUNC('{trunc}', created_at) AS bucket,
                   type,
                   COUNT(*) AS count
            FROM posts {where}
            GROUP BY bucket, type
            ORDER BY bucket ASC
        """, *params)
        breakdown = {}
        for r in rows:
            bucket = r['bucket'].isoformat() if r['bucket'] else None
            t = r['type'] or 'post'
            breakdown.setdefault(bucket, {}).setdefault(t, 0)
            breakdown[bucket][t] = int(r['count'])
        total_posts = await conn.fetchval(f"SELECT COUNT(*) FROM posts {where}", *params)
    return {
        "period": period,
        "trunc": trunc,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": int(total_posts or 0),
        "series": [{"bucket": b, **v} for b, v in sorted(breakdown.items())],
    }


@router.get("/engagement")
async def engagement_stats(request: Request, period: str = Query("month")):
    await require_admin(request)
    start, end, trunc = _range(period)
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM post_likes    WHERE created_at BETWEEN $1 AND $2) AS likes,
                (SELECT COUNT(*) FROM post_comments WHERE created_at BETWEEN $1 AND $2) AS comments,
                (SELECT COALESCE(SUM(shares_count), 0)  FROM posts WHERE created_at BETWEEN $1 AND $2) AS shares,
                (SELECT COALESCE(SUM(tips_total), 0)    FROM posts WHERE created_at BETWEEN $1 AND $2) AS tips_usd,
                (SELECT COUNT(DISTINCT user_id) FROM posts WHERE created_at BETWEEN $1 AND $2) AS active_creators,
                (SELECT COUNT(DISTINCT user_id) FROM post_comments WHERE created_at BETWEEN $1 AND $2) AS active_commenters
        """, start, end)
        series = await conn.fetch(f"""
            SELECT DATE_TRUNC('{trunc}', created_at) AS bucket,
                   COUNT(*) AS likes
            FROM post_likes
            WHERE created_at BETWEEN $1 AND $2
            GROUP BY bucket ORDER BY bucket ASC
        """, start, end)
    return {
        "period": period,
        "totals": {
            "likes": int(totals['likes'] or 0),
            "comments": int(totals['comments'] or 0),
            "shares": int(totals['shares'] or 0),
            "tips_usd": float(totals['tips_usd'] or 0),
            "active_creators": int(totals['active_creators'] or 0),
            "active_commenters": int(totals['active_commenters'] or 0),
        },
        "likes_series": [{"bucket": r['bucket'].isoformat(), "likes": int(r['likes'])} for r in series],
    }


@router.get("/top")
async def top_stats(request: Request, period: str = Query("month"), limit: int = Query(10, ge=1, le=50)):
    """Top-N posts, reels and creators within the period."""
    await require_admin(request)
    start, end, _ = _range(period)
    pool = await get_pool()
    async with pool.acquire() as conn:
        top_posts = await conn.fetch("""
            SELECT p.post_id, p.text, p.type, p.likes_count, p.comments_count, p.shares_count, p.tips_total,
                   u.user_id, u.first_name, u.last_name, u.username, u.avatar
            FROM posts p JOIN users u ON u.user_id=p.user_id
            WHERE p.created_at BETWEEN $1 AND $2 AND (p.type IS NULL OR p.type IN ('post','share'))
            ORDER BY (p.likes_count * 1 + p.comments_count * 2 + p.shares_count * 3) DESC
            LIMIT $3
        """, start, end, limit)
        top_reels = await conn.fetch("""
            SELECT p.post_id, p.text, p.likes_count, p.comments_count, p.shares_count, p.tips_total,
                   u.user_id, u.first_name, u.last_name, u.username, u.avatar
            FROM posts p JOIN users u ON u.user_id=p.user_id
            WHERE p.created_at BETWEEN $1 AND $2 AND p.type='reel'
            ORDER BY (p.likes_count * 1 + p.comments_count * 2 + p.shares_count * 3) DESC
            LIMIT $3
        """, start, end, limit)
        top_creators = await conn.fetch("""
            SELECT u.user_id, u.first_name, u.last_name, u.username, u.avatar, u.is_pro,
                   COUNT(p.id) AS posts,
                   COALESCE(SUM(p.likes_count), 0) AS total_likes,
                   COALESCE(SUM(p.comments_count), 0) AS total_comments,
                   COALESCE(SUM(p.tips_total), 0) AS total_tips_usd
            FROM users u LEFT JOIN posts p
              ON p.user_id = u.user_id AND p.created_at BETWEEN $1 AND $2
            GROUP BY u.user_id, u.first_name, u.last_name, u.username, u.avatar, u.is_pro
            HAVING COUNT(p.id) > 0
            ORDER BY (COALESCE(SUM(p.likes_count),0) + COALESCE(SUM(p.comments_count),0)*2) DESC
            LIMIT $3
        """, start, end, limit)
        def _norm(rows):
            out = []
            for r in rows:
                d = dict(r)
                for k in ('tips_total', 'total_tips_usd'):
                    if k in d and d[k] is not None:
                        d[k] = float(d[k])
                out.append(d)
            return out
        return {
            "period": period,
            "top_posts": _norm(top_posts),
            "top_reels": _norm(top_reels),
            "top_creators": _norm(top_creators),
        }
