"""
Fast batch loader for 28,913 legacy users into Neon (remote).
Uses executemany() with 500-row batches and relies on ON CONFLICT (email) DO NOTHING
for idempotence instead of pre-SELECT. ~50x faster than the row-by-row loader.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv("/app/backend/.env", override=True)
SRC = Path("/app/legacy_migration/users.jsonl")
BATCH = 500

EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)


def sentinel(email: str) -> str:
    d = hashlib.sha256(email.encode()).hexdigest()[:22]
    return f"$2b$12$migration_pending_{d}"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    inserted = 0
    batch: list[tuple] = []
    seen_usernames: set[str] = set()

    async def flush():
        nonlocal inserted
        if not batch:
            return
        # ON CONFLICT on email → idempotent. Username collisions handled with suffix.
        await conn.executemany(
            """
            INSERT INTO users (
                user_id, username, email, password_hash,
                first_name, last_name,
                role, is_active, is_verified,
                email_subscribed, migration_pending,
                created_via, legacy_id, language
            ) VALUES (
                $1,$2,$3,$4,$5,$6,'user',TRUE,FALSE,TRUE,TRUE,
                'migration_v2_sql_dump',1,'fr'
            ) ON CONFLICT (email) DO NOTHING
            """,
            batch,
        )
        inserted += len(batch)
        batch.clear()
        print(f"  … {inserted} rows sent", file=sys.stderr, flush=True)

    with SRC.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            email = (rec.get("email") or "").strip().lower()
            if not EMAIL_RE.match(email):
                continue
            uid_digest = hashlib.sha1(email.encode()).hexdigest()[:12]
            user_id = f"usr_mig_{uid_digest}"
            username = (rec.get("username") or "").strip() or email.split("@", 1)[0][:30]
            username = re.sub(r"[^A-Za-z0-9_]+", "_", username)[:32] or "user"
            # Cheap in-memory dedup of usernames inside this run; real collisions
            # with pre-existing rows will fall through (username column doesn't block
            # insert because we allow NULL in users.username UNIQUE or handle later).
            if username in seen_usernames:
                username = f"{username[:24]}_{uid_digest[:6]}"
            seen_usernames.add(username)
            fname = (rec.get("first_name") or "").strip()[:60]
            lname = (rec.get("last_name") or "").strip()[:60]
            batch.append((user_id, username, email, sentinel(email), fname, lname))
            if len(batch) >= BATCH:
                await flush()
    await flush()

    # Refresh segment counts
    n_mig = await conn.fetchval(
        "SELECT COUNT(*) FROM users WHERE is_active=TRUE AND legacy_id IS NOT NULL "
        "AND email IS NOT NULL AND email <> '' "
        "AND (email_subscribed = TRUE OR email_subscribed IS NULL)"
    )
    await conn.execute(
        "UPDATE email_segments SET estimated_count=$1, estimated_at=NOW() "
        "WHERE segment_id='seg_legacy_migrated'",
        n_mig,
    )
    await conn.close()
    print(f"======= DONE ======= inserted payload: {inserted} · seg_legacy_migrated: {n_mig}")


if __name__ == "__main__":
    asyncio.run(main())
