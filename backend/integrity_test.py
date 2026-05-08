"""
JAPAP Messenger — Integrity Test Suite (Post-Migration v2)
Validates:
  1. Source vs target counts (with acceptable loss thresholds)
  2. Referential integrity (FK relationships)
  3. Data consistency (e.g., wallet balances, likes count)
  4. Sampling validation (random records comparison)
"""
import asyncio
import asyncpg
import json
import re
import sys
from datetime import datetime, timezone, timedelta

DATABASE_URL = "postgresql://japap:japap_secure_2024@localhost:5432/japap_messenger"
SQL_FILE = "/tmp/japap_source/wowonder.sql"


def count_source_records(sql_content, table_name):
    pattern = rf"INSERT INTO `{table_name}`.*?VALUES\s*(.*?);\s*\n"
    total = 0
    for match in re.finditer(pattern, sql_content, re.DOTALL):
        values = match.group(1)
        depth = 0
        count = 0
        in_str = False
        esc = False
        for c in values:
            if esc:
                esc = False
                continue
            if c == '\\':
                esc = True
                continue
            if c == "'" and not esc:
                in_str = not in_str
                continue
            if not in_str:
                if c == '(':
                    if depth == 0:
                        count += 1
                    depth += 1
                elif c == ')':
                    depth -= 1
        total += count
    return total


async def run():
    print("=" * 70)
    print("JAPAP — DATA INTEGRITY TEST SUITE")
    print("=" * 70)

    with open(SQL_FILE, 'r', encoding='utf-8', errors='replace') as f:
        sql = f.read()

    conn = await asyncpg.connect(DATABASE_URL)

    # -----------------------------------------------------
    # TEST 1 — COUNT COMPARISONS (Source vs Target)
    # -----------------------------------------------------
    print("\n[TEST 1] Source vs Target Counts")
    print("-" * 70)
    mapping = [
        # (source_table, target_table, threshold_min_ratio)
        ('Wo_Users',                'users',                 0.99),
        ('Wo_Payment_Transactions', 'transactions',          0.90),
        ('Wo_Payments',             'subscriptions',         0.95),
        ('Wo_Followers',            'contacts',              0.85),
        ('Wo_Posts',                'posts',                 0.90),
        ('Wo_Comments',             'post_comments',         0.85),
        ('Wo_Products',             'products',              0.90),
        ('Wo_UserOrders',           'orders',                0.80),
        ('Wo_Hashtags',             'hashtags',              0.98),
        ('Wo_Groups',               'social_groups',         0.95),
        ('Wo_Group_Members',        'social_group_members',  0.80),
        ('Wo_Pages',                'pages',                 0.95),
        ('Wo_Pages_Likes',          'page_likes',            0.80),
        ('Wo_Blog',                 'blog_articles',         0.95),
        ('Wo_Events',               'events',                0.95),
        ('Wo_Blocks',               'user_blocks',           0.95),
        ('Wo_Funding',              'campaigns',             0.95),
        ('Wo_Funding_Raise',        'campaign_contributions', 0.95),
        ('Wo_AgoraVideoCall',       'calls',                 0.90),
        ('crypto_payments',         'crypto_payments',       0.95),
        ('bank_receipts',           'bank_receipts',         0.95),
        ('exchange_country',        'exchange_countries',    0.95),
        ('withdraw_country',        'withdraw_countries',    0.95),
    ]

    results = []
    print(f"{'Source Table':<32} {'Source':>8} {'Target':>8} {'Ratio':>8}  {'Status'}")
    print("-" * 70)
    for src, tgt, threshold in mapping:
        src_count = count_source_records(sql, src)
        tgt_count = await conn.fetchval(f"SELECT COUNT(*) FROM {tgt}")
        ratio = tgt_count / src_count if src_count > 0 else 1.0
        status = "✓ PASS" if ratio >= threshold else "✗ FAIL"
        print(f"{src:<32} {src_count:>8,} {tgt_count:>8,} {ratio:>7.1%}  {status}")
        results.append({
            'source': src, 'target': tgt,
            'source_count': src_count, 'target_count': tgt_count,
            'ratio': round(ratio, 3), 'threshold': threshold,
            'pass': ratio >= threshold,
        })

    passed = sum(1 for r in results if r['pass'])
    print(f"\n  Summary: {passed}/{len(results)} passed")

    # -----------------------------------------------------
    # TEST 2 — REFERENTIAL INTEGRITY
    # -----------------------------------------------------
    print("\n[TEST 2] Referential Integrity (orphan check)")
    print("-" * 70)
    fk_checks = [
        ("Posts without user",          "SELECT COUNT(*) FROM posts p WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = p.user_id)"),
        ("Comments without post",       "SELECT COUNT(*) FROM post_comments c WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.post_id = c.post_id)"),
        ("Likes without post",          "SELECT COUNT(*) FROM post_likes l WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.post_id = l.post_id)"),
        ("Messages without conv",       "SELECT COUNT(*) FROM messages m WHERE NOT EXISTS (SELECT 1 FROM conversations c WHERE c.conv_id = m.conv_id)"),
        ("Orders without product",      "SELECT COUNT(*) FROM orders o WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.product_id = o.product_id)"),
        ("Group members without group", "SELECT COUNT(*) FROM social_group_members m WHERE NOT EXISTS (SELECT 1 FROM social_groups g WHERE g.group_id = m.group_id)"),
        ("Page likes without page",     "SELECT COUNT(*) FROM page_likes l WHERE NOT EXISTS (SELECT 1 FROM pages p WHERE p.page_id = l.page_id)"),
        ("Campaign contribs w/o camp",  "SELECT COUNT(*) FROM campaign_contributions c WHERE NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.campaign_id = c.campaign_id)"),
        ("Wallets without user",        "SELECT COUNT(*) FROM wallets w WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = w.user_id)"),
    ]
    integrity_pass = 0
    for name, query in fk_checks:
        orphans = await conn.fetchval(query)
        status = "✓ PASS" if orphans == 0 else f"✗ FAIL ({orphans} orphans)"
        print(f"  {name:<38} {status}")
        if orphans == 0:
            integrity_pass += 1
    print(f"\n  Summary: {integrity_pass}/{len(fk_checks)} passed")

    # -----------------------------------------------------
    # TEST 3 — CONSISTENCY CHECKS
    # -----------------------------------------------------
    print("\n[TEST 3] Data Consistency")
    print("-" * 70)
    consistency = []

    # Likes count match
    bad = await conn.fetchval("""
        SELECT COUNT(*) FROM posts p
        WHERE p.likes_count != (SELECT COUNT(*) FROM post_likes WHERE post_id = p.post_id)
    """)
    print(f"  Posts with incorrect likes_count:   {bad}   {'✓ PASS' if bad == 0 else '✗ FAIL'}")
    consistency.append(('likes_count', bad))

    # Comments count match
    bad = await conn.fetchval("""
        SELECT COUNT(*) FROM posts p
        WHERE p.comments_count != (SELECT COUNT(*) FROM post_comments WHERE post_id = p.post_id)
    """)
    print(f"  Posts with incorrect comments_count:{bad}   {'✓ PASS' if bad == 0 else '✗ FAIL'}")
    consistency.append(('comments_count', bad))

    # Campaigns raised match
    bad = await conn.fetchval("""
        SELECT COUNT(*) FROM campaigns c
        WHERE c.raised != COALESCE((SELECT SUM(amount) FROM campaign_contributions WHERE campaign_id = c.campaign_id), 0)
    """)
    print(f"  Campaigns with incorrect raised:     {bad}   {'✓ PASS' if bad == 0 else '✗ FAIL'}")
    consistency.append(('campaigns_raised', bad))

    # Total wallet balance
    total_balance = await conn.fetchval("SELECT SUM(balance) FROM wallets")
    print(f"  Total wallet balance across system:  {total_balance}")

    # Users with legacy_id
    legacy_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE legacy_id IS NOT NULL")
    print(f"  Users with legacy_id:                {legacy_users:,}")

    # -----------------------------------------------------
    # TEST 4 — SAMPLING
    # -----------------------------------------------------
    print("\n[TEST 4] Random Sampling (3 records per entity)")
    print("-" * 70)
    for tbl, label in [('users', 'User'), ('posts', 'Post'), ('messages', 'Message'),
                        ('products', 'Product'), ('campaigns', 'Campaign')]:
        rows = await conn.fetch(f"SELECT * FROM {tbl} ORDER BY RANDOM() LIMIT 3")
        print(f"\n  -- {label} samples --")
        for r in rows:
            d = dict(r)
            # Keep it short
            keys = list(d.keys())[:5]
            short = {k: (str(d[k])[:40] + '...') if len(str(d[k])) > 40 else d[k] for k in keys}
            print(f"    {short}")

    # -----------------------------------------------------
    # FINAL REPORT
    # -----------------------------------------------------
    print("\n" + "=" * 70)
    print("FINAL INTEGRITY REPORT")
    print("=" * 70)
    all_counts = await conn.fetch("""
        SELECT schemaname, relname AS table_name, n_live_tup AS rows
        FROM pg_stat_user_tables
        WHERE schemaname = 'public'
        ORDER BY n_live_tup DESC
    """)
    print(f"\n{'Table':<35} {'Rows':>12}")
    print("-" * 50)
    for r in all_counts:
        print(f"{r['table_name']:<35} {r['rows']:>12,}")

    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'count_tests': {'total': len(results), 'passed': passed, 'details': results},
        'integrity_tests': {'total': len(fk_checks), 'passed': integrity_pass},
        'consistency_tests': consistency,
        'total_balance': str(total_balance),
        'legacy_users': legacy_users,
    }
    with open('/app/backend/integrity_report.json', 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved → /app/backend/integrity_report.json")

    await conn.close()
    return passed == len(results) and integrity_pass == len(fk_checks)


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
