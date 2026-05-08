"""
JAPAP — User privacy & notification preferences (iter78)
==========================================================
Single endpoint to update every privacy toggle in one PUT, plus a GET that
exposes the current settings (profile endpoint is generic and does not
expose these as a group).
"""
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timezone

from database import get_pool
from routes.auth import get_current_user

router = APIRouter(prefix="/api/users", tags=["privacy"])


ACCOUNT_VISIBILITY = ('public', 'private')
FOLLOW_MODE = ('auto', 'approval')
POST_VISIBILITY = ('public', 'friends', 'only_me')


class PrivacyUpdate(BaseModel):
    account_visibility: Optional[Literal['public', 'private']] = None
    follow_mode: Optional[Literal['auto', 'approval']] = None
    post_visibility_default: Optional[Literal['public', 'friends', 'only_me']] = None
    notify_follow: Optional[bool] = None
    notify_follow_accept: Optional[bool] = None
    notify_likes: Optional[bool] = None
    notify_comments: Optional[bool] = None
    notify_messages: Optional[bool] = None


@router.get("/me/privacy")
async def get_my_privacy(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT account_visibility, follow_mode, post_visibility_default,
                      notify_follow, notify_follow_accept, notify_likes,
                      notify_comments, notify_messages
               FROM users WHERE user_id = $1""",
            user['user_id'],
        )
        return dict(row) if row else {}


@router.put("/me/privacy")
async def update_my_privacy(req: PrivacyUpdate, request: Request):
    user = await get_current_user(request)
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No privacy fields provided")

    # Defensive: treat invalid values as 400 (Pydantic Literal already does,
    # but a misspelled Enum value would otherwise land as 500 on the DB).
    if 'account_visibility' in updates and updates['account_visibility'] not in ACCOUNT_VISIBILITY:
        raise HTTPException(status_code=400, detail="account_visibility must be 'public' or 'private'")
    if 'follow_mode' in updates and updates['follow_mode'] not in FOLLOW_MODE:
        raise HTTPException(status_code=400, detail="follow_mode must be 'auto' or 'approval'")
    if 'post_visibility_default' in updates and updates['post_visibility_default'] not in POST_VISIBILITY:
        raise HTTPException(status_code=400, detail="post_visibility_default must be 'public', 'friends' or 'only_me'")

    # When an account flips public → private we don't retroactively convert
    # existing accepted follows to pending — that would surprise users. The
    # switch only affects *future* follow attempts.
    updates['updated_at'] = datetime.now(timezone.utc)
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(updates.keys()))
    values = list(updates.values()) + [user['user_id']]

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {set_clause} WHERE user_id = ${len(values)}",
            *values,
        )
        row = await conn.fetchrow(
            """SELECT account_visibility, follow_mode, post_visibility_default,
                      notify_follow, notify_follow_accept, notify_likes,
                      notify_comments, notify_messages
               FROM users WHERE user_id = $1""",
            user['user_id'],
        )
    return dict(row)


# ════════════════════════════ Change password ════════════════════════════

class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/change-password")
async def change_password(req: PasswordChange, request: Request):
    from routes.auth import hash_password, verify_password
    user = await get_current_user(request)
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT password_hash FROM users WHERE user_id = $1", user['user_id'])
        if not row or not verify_password(req.current_password, row['password_hash']):
            raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect")
        new_hash = hash_password(req.new_password)
        await conn.execute(
            """UPDATE users SET password_hash = $1, password_changed_at = NOW(),
                                 updated_at = NOW() WHERE user_id = $2""",
            new_hash, user['user_id'],
        )
    return {"changed": True}
