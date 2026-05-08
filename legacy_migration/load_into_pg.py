"""
Load legacy users extracted by extract_users.py into the JAPAP Postgres DB.

Semantics:
  • Each user is inserted with:
      - user_id        = `usr_mig_<sha1(email)[:12]>`  (stable + re-runnable)
      - email          = lowercased legacy email
      - username       = legacy username (or derived from email local-part)
      - first_name     = legacy first_name (or "" → renderer falls back to "Utilisateur")
      - last_name      = legacy last_name (or "")
      - password_hash  = sentinel bcrypt-shape never matchable  → forces password reset
      - migration_pending = TRUE                                → login flow returns 403 MIGRATION_RESET_REQUIRED
      - created_via    = "migration_v2_sql_dump"
      - legacy_id      = 1 so the existing `seg_legacy_migrated` segment picks it up
      - email_subscribed = TRUE
      - is_active      = TRUE
      - is_verified    = FALSE (they'll verify on reset)
  • Idempotent: uses ON CONFLICT (email) DO NOTHING so re-running adds 0 rows.
  • Invalid emails are skipped silently.
  • Admin email addresses already present (e.g. admin@japap.com) are never overwritten.
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

EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)


def sentinel_hash(email: str) -> str:
    """Bcrypt-shape sentinel — bcrypt.checkpw will never return True for this."""
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:22]
    return f"$2b$12$migration_pending_{digest}"


async def main(dry_run: bool = False, limit: int = 0):
    url = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    created = skipped_exists = invalid = 0
    async with pool.acquire() as conn:
        with SRC.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if limit and created + skipped_exists >= limit:
                    break
                try:
                    rec = json.loads(line)
                except Exception:
                    invalid += 1
                    continue
                email = (rec.get("email") or "").strip().lower()
                if not email or not EMAIL_RE.match(email):
                    invalid += 1
                    continue
                # Skip if an admin or any active account with that email already exists
                existing = await conn.fetchrow(
                    "SELECT user_id, role FROM users WHERE email = $1", email
                )
                if existing:
                    skipped_exists += 1
                    continue
                uid_digest = hashlib.sha1(email.encode()).hexdigest()[:12]
                user_id = f"usr_mig_{uid_digest}"
                username = (rec.get("username") or "").strip()
                if not username:
                    username = email.split("@", 1)[0][:30]
                # Keep usernames simple (alnum + underscore), avoid @ and spaces
                username = re.sub(r"[^A-Za-z0-9_]+", "_", username)[:32] or "user"
                # Make username unique by suffixing with short digest if collision
                clash = await conn.fetchval(
                    "SELECT 1 FROM users WHERE username = $1", username
                )
                if clash:
                    username = f"{username[:24]}_{uid_digest[:6]}"
                fname = (rec.get("first_name") or "").strip()[:60]
                lname = (rec.get("last_name") or "").strip()[:60]
                if dry_run:
                    created += 1
                    continue
                try:
                    await conn.execute(
                        """
                        INSERT INTO users (
                            user_id, username, email, password_hash,
                            first_name, last_name,
                            role, is_active, is_verified,
                            email_subscribed, migration_pending,
                            created_via, legacy_id,
                            language
                        ) VALUES (
                            $1, $2, $3, $4,
                            $5, $6,
                            'user', TRUE, FALSE,
                            TRUE, TRUE,
                            'migration_v2_sql_dump', 1,
                            'fr'
                        )
                        ON CONFLICT (email) DO NOTHING
                        """,
                        user_id, username, email, sentinel_hash(email),
                        fname, lname,
                    )
                    created += 1
                except Exception as e:
                    print(f"[line {line_num}] insert failed for {email}: {e}",
                          file=sys.stderr)
                    invalid += 1
                if created and created % 1000 == 0:
                    print(f"  … {created} users inserted so far", file=sys.stderr)
        # Refresh seg_legacy_migrated estimated_count
        n_mig = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE is_active=TRUE AND legacy_id IS NOT NULL "
            "AND email IS NOT NULL AND email <> '' "
            "AND (email_subscribed = TRUE OR email_subscribed IS NULL)"
        )
        await conn.execute(
            "UPDATE email_segments SET estimated_count = $1, estimated_at = NOW() "
            "WHERE segment_id = 'seg_legacy_migrated'",
            n_mig,
        )
    await pool.close()
    print("======= Migration v2 (SQL dump) =======")
    print(f" Created                 : {created}")
    print(f" Skipped (already exist) : {skipped_exists}")
    print(f" Invalid / skipped rows  : {invalid}")
    print(f" seg_legacy_migrated now : {n_mig} users")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    limit_flag = [a for a in sys.argv if a.startswith("--limit=")]
    limit = int(limit_flag[0].split("=")[1]) if limit_flag else 0
    asyncio.run(main(dry_run=dry, limit=limit))
