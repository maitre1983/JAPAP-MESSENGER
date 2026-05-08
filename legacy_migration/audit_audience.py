"""
Audience cleanliness audit — dry-run against Neon. Reports how many of the
28 913 migrated users would pass the messaging cleanliness filter, and how
many would be dropped (and why). Zero writes, zero sends.
"""
import asyncio, os
from dotenv import load_dotenv
load_dotenv("/app/backend/.env", override=True)
import asyncpg
import sys
sys.path.insert(0, "/app/backend")
from routes.admin_messaging import _is_clean_email, _SUSPICIOUS_DOMAINS


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        "SELECT user_id, email, migration_pending, is_active FROM users "
        "WHERE is_active=TRUE AND legacy_id IS NOT NULL"
    )
    print(f"Auditing {len(rows)} migrated users...")
    stats = {"ok": 0, "invalid_format": 0, "disposable_domain": 0,
             "no_reply_address": 0, "too_long": 0, "missing": 0,
             "duplicate": 0}
    seen = set()
    samples = {k: [] for k in stats}
    for r in rows:
        em = (r["email"] or "").strip().lower()
        ok, reason = _is_clean_email(em)
        if not ok:
            stats[reason] += 1
            if len(samples[reason]) < 5:
                samples[reason].append(em)
            continue
        if em in seen:
            stats["duplicate"] += 1
            if len(samples["duplicate"]) < 5:
                samples["duplicate"].append(em)
            continue
        seen.add(em)
        stats["ok"] += 1
    total = len(rows)
    print("═══════════════════ AUDIENCE CLEANLINESS AUDIT ═══════════════════")
    print(f"Total migrated users         : {total}")
    print(f"  ✅ Clean / sendable        : {stats['ok']}   ({stats['ok']/total*100:.1f}%)")
    print(f"  ❌ Invalid format          : {stats['invalid_format']}")
    print(f"  ❌ Disposable domain       : {stats['disposable_domain']}")
    print(f"  ❌ No-reply / postmaster   : {stats['no_reply_address']}")
    print(f"  ❌ Too long                : {stats['too_long']}")
    print(f"  ❌ Missing                 : {stats['missing']}")
    print(f"  ❌ Duplicate (in batch)    : {stats['duplicate']}")
    total_dropped = total - stats['ok']
    print(f"Would drop                   : {total_dropped}")
    print(f"Would send to                : {stats['ok']}")
    print()
    print("── Samples of dropped rows ──")
    for k, v in samples.items():
        if k != "ok" and v:
            print(f"  {k}: {v}")
    # Gmail coverage
    gmail_ok = sum(1 for em in seen if em.endswith("@gmail.com"))
    print(f"\n@gmail.com clean addresses: {gmail_ok}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
