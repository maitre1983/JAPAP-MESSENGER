"""
iter83 — Engagement Leaderboard (combined Wheel + Quiz + Tap).

Provides :
  - GET  /api/engagement/leaderboard/weekly       → top 10 this ISO week
  - GET  /api/engagement/leaderboard/all          → top 10 all-time / cycle
  - GET  /api/engagement/share-card.png           → 1080×1080 PNG for sharing
                                                      (user's own card)
  - GET  /api/engagement/share-card-public.png    → same but for any user
                                                      (safe metadata only)

The leaderboard is computed server-side from `wheel_spins` (which stores
all points transactions incl. quiz/tap via the unified points_service).
"""
from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/engagement", tags=["engagement"])


# ══════════════════════════════════════════════════════════════════════════
#  Leaderboard
# ══════════════════════════════════════════════════════════════════════════

async def _top_players(conn, *, since, limit: int = 10):
    """Return the top `limit` players by total points awarded since `since`.

    Breaks down contributions by source (wheel / quiz / tap / admin)."""
    rows = await conn.fetch(
        """SELECT s.user_id,
                  COALESCE(SUM(s.points_awarded), 0)                        AS points_total,
                  COALESCE(SUM(s.points_awarded) FILTER (WHERE s.source='wheel'), 0) AS points_wheel,
                  COALESCE(SUM(s.points_awarded) FILTER (WHERE s.source='quiz'),  0) AS points_quiz,
                  COALESCE(SUM(s.points_awarded) FILTER (WHERE s.source='tap'),   0) AS points_tap,
                  COUNT(DISTINCT s.spin_date)                                AS days_played,
                  u.first_name, u.last_name, u.email, u.avatar, u.is_pro,
                  c.points_cycle, c.days_played_count,
                  c.quiz_answers_correct, c.quiz_answers_total
           FROM wheel_spins s
           LEFT JOIN users u ON u.user_id = s.user_id
           LEFT JOIN wheel_cycles c ON c.user_id = s.user_id AND c.reward_status = 'in_progress'
           WHERE s.spin_at >= $1
           GROUP BY s.user_id, u.first_name, u.last_name, u.email, u.avatar, u.is_pro,
                    c.points_cycle, c.days_played_count,
                    c.quiz_answers_correct, c.quiz_answers_total
           ORDER BY points_total DESC
           LIMIT $2""",
        since, limit,
    )
    out = []
    for i, r in enumerate(rows):
        qtotal = int(r["quiz_answers_total"] or 0)
        qcorrect = int(r["quiz_answers_correct"] or 0)
        out.append({
            "rank": i + 1,
            "user_id": r["user_id"],
            "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or r["user_id"],
            "avatar": r["avatar"],
            "is_pro": bool(r["is_pro"]),
            "points_total": int(r["points_total"] or 0),
            "points_wheel": int(r["points_wheel"] or 0),
            "points_quiz": int(r["points_quiz"] or 0),
            "points_tap": int(r["points_tap"] or 0),
            "days_played": int(r["days_played"] or 0),
            "cycle_points": int(r["points_cycle"] or 0),
            "cycle_days": int(r["days_played_count"] or 0),
            "quiz_accuracy": round(qcorrect / qtotal, 2) if qtotal else 0.0,
        })
    return out


def _start_of_week_utc() -> datetime:
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/leaderboard/weekly")
async def leaderboard_weekly(request: Request, limit: int = 10):
    """Top players of the current ISO week (Monday 00:00 UTC → now)."""
    limit = max(1, min(int(limit), 50))
    pool = await get_pool()
    async with pool.acquire() as pool_conn:
        top = await _top_players(pool_conn, since=_start_of_week_utc(), limit=limit)

    # Include the current user's rank even if not in top 10 (nice UX)
    me_rank = None
    try:
        me = await get_current_user(request)
        if me:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """WITH scores AS (
                         SELECT user_id, SUM(points_awarded) AS pts
                         FROM wheel_spins
                         WHERE spin_at >= $1
                         GROUP BY user_id
                       )
                       SELECT pts, (SELECT COUNT(*)+1 FROM scores WHERE pts > s.pts) AS rank
                       FROM scores s WHERE user_id = $2""",
                    _start_of_week_utc(), me["user_id"],
                )
                if row:
                    me_rank = {"rank": int(row["rank"]), "points_total": int(row["pts"] or 0),
                               "user_id": me["user_id"]}
    except Exception:
        pass
    return {
        "period": "week",
        "starts_at": _start_of_week_utc().isoformat(),
        "top": top,
        "me": me_rank,
    }


@router.get("/leaderboard/all")
async def leaderboard_all(request: Request, limit: int = 10):
    """Top players of the last 30 days (rolling)."""
    limit = max(1, min(int(limit), 50))
    since = datetime.now(timezone.utc) - timedelta(days=30)
    pool = await get_pool()
    async with pool.acquire() as conn:
        top = await _top_players(conn, since=since, limit=limit)
    return {"period": "30d", "starts_at": since.isoformat(), "top": top}


# ══════════════════════════════════════════════════════════════════════════
#  Share card (backend-rendered PNG)
# ══════════════════════════════════════════════════════════════════════════

class _ShareData(BaseModel):
    user_id: str
    name: str
    avatar: Optional[str] = None
    rank: int
    points_total: int
    points_quiz: int
    points_tap: int
    points_wheel: int
    cycle_points: int
    cycle_days: int


async def _collect_share_data(conn, user_id: str) -> _ShareData:
    since = _start_of_week_utc()
    row = await conn.fetchrow(
        """WITH scores AS (
             SELECT user_id, SUM(points_awarded) AS pts,
                    SUM(points_awarded) FILTER (WHERE source='quiz') AS quiz,
                    SUM(points_awarded) FILTER (WHERE source='tap')  AS tap,
                    SUM(points_awarded) FILTER (WHERE source='wheel') AS wheel
             FROM wheel_spins
             WHERE spin_at >= $1
             GROUP BY user_id
           )
           SELECT s.pts, s.quiz, s.tap, s.wheel,
                  (SELECT COUNT(*)+1 FROM scores WHERE pts > s.pts) AS rank,
                  u.first_name, u.last_name, u.email, u.avatar,
                  c.points_cycle, c.days_played_count
           FROM scores s
           LEFT JOIN users u ON u.user_id = s.user_id
           LEFT JOIN wheel_cycles c ON c.user_id = s.user_id AND c.reward_status = 'in_progress'
           WHERE s.user_id = $2""",
        since, user_id,
    )
    if not row:
        # Unranked — still allow a card with zeros (prevents failures before first play)
        u = await conn.fetchrow(
            "SELECT first_name, last_name, email, avatar FROM users WHERE user_id = $1",
            user_id,
        )
        if not u:
            raise HTTPException(status_code=404, detail="Joueur introuvable")
        return _ShareData(
            user_id=user_id,
            name=f"{u['first_name'] or ''} {u['last_name'] or ''}".strip() or u["email"] or user_id,
            avatar=u["avatar"], rank=999, points_total=0, points_quiz=0,
            points_tap=0, points_wheel=0, cycle_points=0, cycle_days=0,
        )
    return _ShareData(
        user_id=user_id,
        name=f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or row["email"] or user_id,
        avatar=row["avatar"],
        rank=int(row["rank"]),
        points_total=int(row["pts"] or 0),
        points_quiz=int(row["quiz"] or 0),
        points_tap=int(row["tap"] or 0),
        points_wheel=int(row["wheel"] or 0),
        cycle_points=int(row["points_cycle"] or 0),
        cycle_days=int(row["days_played_count"] or 0),
    )


def _render_card_png(data: _ShareData, avatar_bytes: Optional[bytes] = None) -> bytes:
    """Render a 1080×1080 PNG using Pillow. Self-contained (no web fonts)."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), "#0F056B")
    draw = ImageDraw.Draw(img, "RGBA")

    # Radial-ish background using concentric layered ellipses
    for radius, color in [
        (1400, (42, 14, 149, 255)),
        (1000, (28, 11, 138, 255)),
        (700,  (15, 5, 107, 255)),
    ]:
        draw.ellipse(
            (W // 2 - radius, H // 2 - radius, W // 2 + radius, H // 2 + radius),
            fill=color,
        )

    # Fonts (fallback to default if missing)
    def _font(size: int):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for f in candidates:
            if os.path.exists(f):
                try:
                    return ImageFont.truetype(f, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    f_brand    = _font(60)
    f_week     = _font(30)
    f_rank_num = _font(180)
    f_rank_lbl = _font(32)
    f_points   = _font(120)
    f_plabel   = _font(30)
    f_name     = _font(54)
    f_break_v  = _font(44)
    f_break_l  = _font(28)
    f_footer   = _font(26)

    # Header — JAPAP brand + week badge
    draw.rectangle((0, 0, W, 120), fill=(0, 0, 0, 110))
    draw.text((50, 30), "JAPAP ENGAGEMENT", font=f_brand, fill=(255, 215, 0))
    today = datetime.now(timezone.utc)
    iso_year, iso_week, _ = today.isocalendar()
    draw.text((W - 360, 50),
              f"Semaine {iso_week}/{iso_year}",
              font=f_week, fill=(167, 139, 250))

    # Left column : rank + label
    # Gold halo behind rank
    draw.ellipse((30, 160, 440, 570), fill=(255, 215, 0, 30))
    rank_str = f"#{data.rank}" if 0 < data.rank < 999 else "—"
    # Use textbbox for precise positioning of the rank number
    try:
        bbox = draw.textbbox((0, 0), rank_str, font=f_rank_num)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = 260
    draw.text(((460 - tw) // 2 + 10, 230), rank_str, font=f_rank_num, fill=(255, 215, 0))
    draw.text((90, 500), "rang de la semaine", font=f_rank_lbl, fill=(255, 255, 255, 210))

    # Right column : avatar + name
    avatar_size = 260
    ax = 640
    ay = 210
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)

    avatar_img = None
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
            avatar_img = avatar_img.resize((avatar_size, avatar_size))
        except Exception:
            avatar_img = None
    if avatar_img:
        img.paste(avatar_img, (ax, ay), mask)
    else:
        initials_bg = Image.new("RGB", (avatar_size, avatar_size), (139, 92, 246))
        img.paste(initials_bg, (ax, ay), mask)
        initials = (data.name or "?")[0].upper()
        draw.text((ax + avatar_size // 2 - 50, ay + avatar_size // 2 - 80),
                  initials, font=_font(140), fill=(255, 255, 255))
    # Gold ring
    draw.ellipse((ax - 8, ay - 8, ax + avatar_size + 8, ay + avatar_size + 8),
                 outline=(255, 215, 0), width=6)

    # Player name — centered under avatar
    display_name = data.name if len(data.name) <= 18 else data.name[:16] + "…"
    try:
        nb = draw.textbbox((0, 0), display_name, font=f_name)
        nw = nb[2] - nb[0]
    except Exception:
        nw = len(display_name) * 28
    draw.text((ax + (avatar_size - nw) // 2, ay + avatar_size + 20),
              display_name, font=f_name, fill=(255, 255, 255))

    # Gold points banner
    banner_y = 620
    banner_h = 180
    draw.rectangle((50, banner_y, W - 50, banner_y + banner_h),
                   fill=(255, 215, 0))
    total_str = f"{data.points_total:,}".replace(",", " ")
    try:
        tb = draw.textbbox((0, 0), total_str, font=f_points)
        ptw = tb[2] - tb[0]
    except Exception:
        ptw = 300
    draw.text(((W - ptw) // 2, banner_y + 18),
              total_str, font=f_points, fill=(15, 5, 107))
    sub = "points cumulés cette semaine"
    try:
        sb = draw.textbbox((0, 0), sub, font=f_plabel)
        sw = sb[2] - sb[0]
    except Exception:
        sw = len(sub) * 16
    draw.text(((W - sw) // 2, banner_y + banner_h - 40),
              sub, font=f_plabel, fill=(15, 5, 107))

    # Breakdown : Roue / Quiz / Tap
    y = 830
    breakdown = [
        ("Roue", data.points_wheel, (255, 215, 0)),
        ("Quiz", data.points_quiz, (167, 139, 250)),
        ("Tap",  data.points_tap,  (224, 28, 46)),
    ]
    col_w = (W - 160) // 3
    for i, (label, pts, col) in enumerate(breakdown):
        cx = 60 + i * (col_w + 20)
        # Card background
        draw.rounded_rectangle(
            (cx, y, cx + col_w, y + 120), radius=22,
            fill=(255, 255, 255, 22), outline=(col[0], col[1], col[2], 140), width=2,
        )
        val_str = f"+{pts}"
        try:
            vb = draw.textbbox((0, 0), val_str, font=f_break_v)
            vw = vb[2] - vb[0]
        except Exception:
            vw = len(val_str) * 26
        draw.text((cx + (col_w - vw) // 2, y + 16),
                  val_str, font=f_break_v, fill=col)
        try:
            lb = draw.textbbox((0, 0), label, font=f_break_l)
            lw = lb[2] - lb[0]
        except Exception:
            lw = len(label) * 14
        draw.text((cx + (col_w - lw) // 2, y + 72),
                  label, font=f_break_l, fill=(255, 255, 255, 220))

    # Cycle progress
    y2 = 1000
    cycle_pct = min(100, int(data.cycle_points / 100))     # 10 000 = 100 %
    days_pct = min(100, int(data.cycle_days * 4))          # 25 days = 100 %
    draw.text(
        (60, y2 - 28),
        f"Starter Pro : {data.cycle_points:,}/10 000 pts · {data.cycle_days}/25 jours".replace(",", " "),
        font=f_footer, fill=(255, 255, 255, 220),
    )
    bar_w = W - 120
    filled = int(bar_w * (min(cycle_pct, days_pct) / 100))
    draw.rounded_rectangle((60, y2, 60 + bar_w, y2 + 18), radius=9,
                           fill=(255, 255, 255, 45))
    if filled > 0:
        draw.rounded_rectangle((60, y2, 60 + filled, y2 + 18), radius=9,
                               fill=(255, 215, 0))

    # Footer CTA — iter168: brand-correct domain via centralised helper
    from utils.public_url import short_domain
    draw.text(
        (60, H - 50),
        f"{short_domain()} · Jouez. Gagnez. Débloquez le Starter Pro.",
        font=f_footer, fill=(167, 139, 250),
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _load_avatar_bytes(avatar_url: str) -> Optional[bytes]:
    """Fetch a local avatar file from /api/upload/files/… into bytes. External
    URLs are ignored to avoid SSRF."""
    if not avatar_url or not isinstance(avatar_url, str):
        return None
    if not avatar_url.startswith("/api/upload/files/"):
        return None
    filename = avatar_url.rsplit("/", 1)[-1]
    local_path = os.path.join("/app/backend/uploads", filename)
    try:
        with open(local_path, "rb") as f:
            return f.read()
    except Exception:
        return None


@router.get("/share-card.png")
async def share_card_png(request: Request):
    """Render the current user's weekly share card as a PNG."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await _collect_share_data(conn, user["user_id"])
    avatar_bytes = await _load_avatar_bytes(data.avatar or "")
    png = _render_card_png(data, avatar_bytes)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=60"})


@router.get("/share-card-public.png")
async def share_card_public_png(user_id: str):
    """Render a share card for `user_id` without auth. Safe metadata only."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await _collect_share_data(conn, user_id)
    avatar_bytes = await _load_avatar_bytes(data.avatar or "")
    png = _render_card_png(data, avatar_bytes)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=120"})
