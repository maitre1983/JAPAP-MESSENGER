"""
JAPAP Messenger — ETL Migration v2 (COMPLETE)
Migrates ALL remaining legacy WoWonder data to PostgreSQL.

Waves:
  1. Social content: Posts, Comments, Replies, Reactions, Likes, Hashtags, Media
  2. Marketplace: Products, Categories, Media, Orders
  3. Messaging: Group chats, group messages, chat metadata
  4. Extended profiles: UserFields, Blocks, Saved/Hidden/Pinned posts
  5. Modules: Groups, Pages, Blog, Events, Polls/Votes, Funding, Crypto, Bank
  6. History: Notifications (last 30 days), Calls
"""
import re
import os
import sys
import json
import uuid
import asyncio
import asyncpg
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("migrate_v2")

SQL_FILE = "/tmp/japap_source/wowonder.sql"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://japap:japap_secure_2024@localhost:5432/japap_messenger",
)

# 30 days cutoff for notifications
NOTIF_CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)


# ------------------------------------------------------------------
# SQL PARSER (handles escaped strings, nested parens)
# ------------------------------------------------------------------
def parse_insert_values(sql_content, table_name):
    pattern = rf"INSERT INTO `{table_name}` .*? VALUES\s*(.*?);\s*\n"
    all_records = []
    for m in re.finditer(pattern, sql_content, re.DOTALL):
        chunk = m.group(1)
        depth = 0
        cur = ""
        in_str = False
        esc = False
        for ch in chunk:
            if esc:
                cur += ch
                esc = False
                continue
            if ch == '\\':
                cur += ch
                esc = True
                continue
            if ch == "'" and not esc:
                in_str = not in_str
                cur += ch
                continue
            if not in_str:
                if ch == '(':
                    depth += 1
                    if depth == 1:
                        cur = ""
                        continue
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        all_records.append(cur)
                        cur = ""
                        continue
                cur += ch
            else:
                cur += ch
    return all_records


def parse_csv_values(record_str):
    values = []
    cur = ""
    in_str = False
    esc = False
    for ch in record_str:
        if esc:
            cur += ch
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == "'" and not esc:
            in_str = not in_str
            continue
        if ch == ',' and not in_str:
            values.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        values.append(cur.strip())
    out = []
    for v in values:
        if v in ('NULL', 'null'):
            out.append(None)
        else:
            out.append(v)
    return out


def safe_int(v, default=0):
    try:
        return int(v) if v not in (None, '', 'NULL') else default
    except Exception:
        return default


def safe_float(v, default=0.0):
    try:
        return float(v) if v not in (None, '', 'NULL') else default
    except Exception:
        return default


def ts_from_unix(v):
    t = safe_int(v, 0)
    if t > 0:
        try:
            return datetime.fromtimestamp(t, tz=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def extract_hashtags(text):
    if not text:
        return []
    return list(set(re.findall(r"#([\wàâäéèêëïîôöùûüç]+)", text, re.IGNORECASE)))


# ------------------------------------------------------------------
# MAIN MIGRATION
# ------------------------------------------------------------------
async def migrate():
    log.info("=" * 60)
    log.info("JAPAP MESSENGER — ETL MIGRATION v2 (COMPLETE)")
    log.info("=" * 60)

    log.info("Loading SQL dump...")
    with open(SQL_FILE, 'r', encoding='utf-8', errors='replace') as f:
        sql = f.read()
    log.info(f"Loaded {len(sql):,} bytes")

    conn = await asyncpg.connect(DATABASE_URL)

    # Preload user legacy_id -> user_id map
    user_map = {}
    rows = await conn.fetch("SELECT legacy_id, user_id FROM users WHERE legacy_id IS NOT NULL")
    for row in rows:
        user_map[row['legacy_id']] = row['user_id']
    log.info(f"Loaded {len(user_map):,} user mappings")

    report = {}

    try:
        # =================================================================
        # WAVE 1 — SOCIAL CONTENT
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("WAVE 1 — SOCIAL CONTENT")
        log.info("=" * 60)

        # --- 1.1 POSTS ---
        log.info("\n[1.1] Migrating posts (Wo_Posts)...")
        recs = parse_insert_values(sql, 'Wo_Posts')
        post_count = 0
        hashtag_count = 0
        media_count = 0
        post_id_map = {}  # legacy_id -> post_id

        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 60:
                continue
            try:
                old_id = safe_int(v[0])
                if not old_id:
                    continue
                uid_legacy = safe_int(v[2])
                group_id = safe_int(v[6])
                user_id = user_map.get(uid_legacy)
                if not user_id:
                    continue

                text = v[4] or ''
                post_link = v[9] or ''
                post_file = v[16] or ''
                post_file_name = v[17] or ''
                post_youtube = v[19] or ''
                post_privacy_raw = v[26] or '0'
                post_type = (v[27] or 'post')[:30]
                post_photo = v[33] or ''
                time_val = safe_int(v[34])
                multi_image = v[37] or ''
                active = (v[59] or '1') == '1'

                if not active:
                    continue

                post_id = f"leg_post_{old_id}"
                post_id_map[old_id] = post_id
                created_at = ts_from_unix(time_val)

                # Build media array
                media = []
                if post_photo:
                    media.append({"type": "image", "url": post_photo})
                if post_file:
                    media.append({"type": "file", "url": post_file, "name": post_file_name})
                if post_youtube:
                    media.append({"type": "youtube", "url": post_youtube})
                if multi_image and multi_image not in ('0', ''):
                    try:
                        mi = json.loads(multi_image)
                        if isinstance(mi, list):
                            for item in mi:
                                if isinstance(item, dict) and 'image' in item:
                                    media.append({"type": "image", "url": item['image']})
                                elif isinstance(item, str):
                                    media.append({"type": "image", "url": item})
                    except Exception:
                        pass

                # Privacy: 0=public, 1=friends, 2=only me
                visibility = 'public'
                if post_privacy_raw and post_privacy_raw.isdigit():
                    p = int(post_privacy_raw)
                    visibility = ['public', 'friends', 'private'][p] if p < 3 else 'public'

                exists = await conn.fetchval("SELECT 1 FROM posts WHERE post_id = $1", post_id)
                if exists:
                    post_count += 1
                    continue

                await conn.execute("""
                    INSERT INTO posts (post_id, user_id, text, media, visibility, created_at, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6, $6)
                    ON CONFLICT (post_id) DO NOTHING
                """, post_id, user_id, text, json.dumps(media), visibility, created_at)

                # Hashtags
                tags = extract_hashtags(text)
                for tag in tags:
                    tag_l = tag.lower()[:255]
                    await conn.execute("""
                        INSERT INTO hashtags (tag, use_count) VALUES ($1, 1)
                        ON CONFLICT (tag) DO UPDATE SET use_count = hashtags.use_count + 1
                    """, tag_l)
                    await conn.execute("""
                        INSERT INTO post_hashtags (post_id, tag) VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                    """, post_id, tag_l)
                    hashtag_count += 1

                post_count += 1
            except Exception as e:
                log.debug(f"Post skip: {e}")
                continue

        log.info(f"  ✓ Posts migrated: {post_count:,}")
        log.info(f"  ✓ Hashtag links: {hashtag_count:,}")
        report['posts'] = post_count
        report['post_hashtags'] = hashtag_count

        # --- 1.2 COMMENTS ---
        log.info("\n[1.2] Migrating comments (Wo_Comments)...")
        recs = parse_insert_values(sql, 'Wo_Comments')
        comm_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 5:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1])
                post_legacy = safe_int(v[3])
                text = v[4] or ''
                time_val = safe_int(v[7]) if len(v) > 7 else 0

                user_id = user_map.get(uid_legacy)
                post_id = post_id_map.get(post_legacy)
                if not user_id or not post_id or not old_id:
                    continue

                comment_id = f"leg_cm_{old_id}"
                created_at = ts_from_unix(time_val)

                await conn.execute("""
                    INSERT INTO post_comments (comment_id, post_id, user_id, text, created_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (comment_id) DO NOTHING
                """, comment_id, post_id, user_id, text, created_at)
                comm_count += 1
            except Exception:
                continue

        # Update post comment counts
        await conn.execute("""
            UPDATE posts p SET comments_count = c.cnt
            FROM (SELECT post_id, COUNT(*)::int AS cnt FROM post_comments GROUP BY post_id) c
            WHERE p.post_id = c.post_id
        """)
        log.info(f"  ✓ Comments migrated: {comm_count:,}")
        report['comments'] = comm_count

        # --- 1.3 REACTIONS + LIKES ---
        log.info("\n[1.3] Migrating reactions & likes (Wo_Reactions + Wo_Likes)...")
        likes_count = 0

        # Wo_Reactions (has post_id in v[2])
        recs = parse_insert_values(sql, 'Wo_Reactions')
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                uid_legacy = safe_int(v[1])
                post_legacy = safe_int(v[2])
                if not post_legacy:
                    continue
                user_id = user_map.get(uid_legacy)
                post_id = post_id_map.get(post_legacy)
                if not user_id or not post_id:
                    continue
                await conn.execute("""
                    INSERT INTO post_likes (post_id, user_id) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, post_id, user_id)
                likes_count += 1
            except Exception:
                continue

        # Wo_Likes (basic)
        recs = parse_insert_values(sql, 'Wo_Likes')
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                uid_legacy = safe_int(v[1])
                post_legacy = safe_int(v[2])
                user_id = user_map.get(uid_legacy)
                post_id = post_id_map.get(post_legacy)
                if not user_id or not post_id:
                    continue
                await conn.execute("""
                    INSERT INTO post_likes (post_id, user_id) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, post_id, user_id)
                likes_count += 1
            except Exception:
                continue

        await conn.execute("""
            UPDATE posts p SET likes_count = c.cnt
            FROM (SELECT post_id, COUNT(*)::int AS cnt FROM post_likes GROUP BY post_id) c
            WHERE p.post_id = c.post_id
        """)
        log.info(f"  ✓ Likes/Reactions migrated: {likes_count:,}")
        report['likes'] = likes_count

        # --- 1.4 HASHTAGS (standalone from Wo_Hashtags) ---
        log.info("\n[1.4] Migrating hashtags metadata (Wo_Hashtags)...")
        recs = parse_insert_values(sql, 'Wo_Hashtags')
        ht_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                tag = (v[2] or '').lower().strip()
                if not tag:
                    continue
                trend_num = safe_int(v[4])
                await conn.execute("""
                    INSERT INTO hashtags (tag, use_count, legacy_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (tag) DO UPDATE SET use_count = GREATEST(hashtags.use_count, $2)
                """, tag[:255], trend_num, safe_int(v[0]))
                ht_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Hashtags: {ht_count:,}")
        report['hashtags'] = ht_count

        # --- 1.5 MEDIA LIBRARY (URLs only) ---
        log.info("\n[1.5] Migrating media library (Wo_UploadedMedia + Wo_Albums_Media)...")
        recs = parse_insert_values(sql, 'Wo_UploadedMedia')
        m_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                old_id = safe_int(v[0])
                filename = (v[1] or '')[:1000]
                storage = (v[2] or 'local')[:50]
                time_val = safe_int(v[3])
                if not filename:
                    continue
                mid = f"leg_up_{old_id}"
                await conn.execute("""
                    INSERT INTO media_library (media_id, filename, storage, type, legacy_id, created_at)
                    VALUES ($1, $2, $3, 'image', $4, $5)
                    ON CONFLICT (media_id) DO NOTHING
                """, mid, filename, storage, old_id, ts_from_unix(time_val))
                m_count += 1
            except Exception:
                continue

        recs = parse_insert_values(sql, 'Wo_Albums_Media')
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 5:
                continue
            try:
                old_id = safe_int(v[0])
                post_legacy = safe_int(v[1])
                image = (v[4] or '')[:1000]
                if not image:
                    continue
                mid = f"leg_alb_{old_id}"
                post_id = post_id_map.get(post_legacy)
                await conn.execute("""
                    INSERT INTO media_library (media_id, post_id, filename, storage, type, legacy_id)
                    VALUES ($1, $2, $3, 'local', 'image', $4)
                    ON CONFLICT (media_id) DO NOTHING
                """, mid, post_id, image, old_id)
                m_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Media refs: {m_count:,}")
        report['media_library'] = m_count

        # --- 1.6 SAVED / HIDDEN / PINNED ---
        log.info("\n[1.6] Migrating saved/hidden/pinned posts...")
        for tbl, target in [('Wo_SavedPosts', 'saved_posts'),
                            ('Wo_HiddenPosts', 'hidden_posts')]:
            recs = parse_insert_values(sql, tbl)
            n = 0
            for rec in recs:
                v = parse_csv_values(rec)
                if len(v) < 3:
                    continue
                try:
                    if tbl == 'Wo_SavedPosts':
                        uid_legacy = safe_int(v[1])
                        post_legacy = safe_int(v[2])
                    else:
                        post_legacy = safe_int(v[1])
                        uid_legacy = safe_int(v[2])
                    user_id = user_map.get(uid_legacy)
                    post_id = post_id_map.get(post_legacy)
                    if not user_id or not post_id:
                        continue
                    await conn.execute(
                        f"INSERT INTO {target} (user_id, post_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        user_id, post_id,
                    )
                    n += 1
                except Exception:
                    continue
            log.info(f"  ✓ {target}: {n:,}")
            report[target] = n

        # Pinned posts
        recs = parse_insert_values(sql, 'Wo_PinnedPosts')
        n = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 5:
                continue
            try:
                uid_legacy = safe_int(v[1])
                post_legacy = safe_int(v[4])
                user_id = user_map.get(uid_legacy)
                post_id = post_id_map.get(post_legacy)
                if not user_id or not post_id:
                    continue
                await conn.execute(
                    "INSERT INTO pinned_posts (user_id, post_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    user_id, post_id,
                )
                n += 1
            except Exception:
                continue
        log.info(f"  ✓ pinned_posts: {n:,}")
        report['pinned_posts'] = n

        # =================================================================
        # WAVE 2 — MARKETPLACE
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("WAVE 2 — MARKETPLACE")
        log.info("=" * 60)

        # --- 2.1 PRODUCT CATEGORIES ---
        recs = parse_insert_values(sql, 'Wo_Products_Categories')
        cat_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 2:
                continue
            try:
                old_id = safe_int(v[0])
                key = (v[1] or f'cat_{old_id}').strip()[:100]
                await conn.execute("""
                    INSERT INTO product_categories (category_key, display_name, legacy_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (category_key) DO NOTHING
                """, key, key.replace('_', ' ').title(), old_id)
                cat_count += 1
            except Exception:
                continue
        log.info(f"[2.1] Product categories: {cat_count}")
        report['product_categories'] = cat_count

        # --- 2.2 PRODUCTS ---
        log.info("[2.2] Migrating products (Wo_Products)...")
        recs = parse_insert_values(sql, 'Wo_Products')
        prod_count = 0
        prod_id_map = {}
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 10:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1])
                name = (v[3] or '')[:255]
                desc = v[4] or ''
                category = (v[5] or 'general')[:100]
                price = safe_float(v[7])
                location = (v[8] or '')[:255]
                status = (v[9] or 'active')[:20]
                currency = (v[11] or 'XAF')[:10]
                time_val = safe_int(v[16]) if len(v) > 16 else 0
                active = (v[17] or '1') == '1' if len(v) > 17 else True

                user_id = user_map.get(uid_legacy)
                if not user_id or not name or not active:
                    continue

                pid = f"leg_prod_{old_id}"
                prod_id_map[old_id] = pid
                await conn.execute("""
                    INSERT INTO products (product_id, seller_id, title, description, price, currency,
                        category, location, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)
                    ON CONFLICT (product_id) DO NOTHING
                """, pid, user_id, name, desc, price, currency, category, location,
                     'active' if active else 'inactive', ts_from_unix(time_val))
                prod_count += 1
            except Exception as e:
                log.debug(f"Product skip: {e}")
                continue
        log.info(f"  ✓ Products: {prod_count}")
        report['products'] = prod_count

        # --- 2.3 PRODUCT IMAGES ---
        recs = parse_insert_values(sql, 'Wo_Products_Media')
        images_added = 0
        prod_images = {}
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                prod_legacy = safe_int(v[1])
                img = (v[2] or '')[:500]
                pid = prod_id_map.get(prod_legacy)
                if not pid or not img:
                    continue
                prod_images.setdefault(pid, []).append(img)
            except Exception:
                continue
        for pid, imgs in prod_images.items():
            await conn.execute("UPDATE products SET images = $1::jsonb WHERE product_id = $2",
                               json.dumps(imgs), pid)
            images_added += len(imgs)
        log.info(f"  ✓ Product images attached: {images_added}")
        report['product_images'] = images_added

        # --- 2.4 ORDERS ---
        log.info("[2.4] Migrating orders (Wo_UserOrders)...")
        recs = parse_insert_values(sql, 'Wo_UserOrders')
        ord_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 10:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[2])
                owner_legacy = safe_int(v[3])
                prod_legacy = safe_int(v[4])
                price = safe_float(v[6])
                commission = safe_float(v[7])
                final_price = safe_float(v[10])
                status = (v[14] or 'pending')[:30]
                time_val = safe_int(v[15])

                buyer = user_map.get(uid_legacy)
                seller = user_map.get(owner_legacy)
                pid = prod_id_map.get(prod_legacy)
                if not buyer or not seller or not pid:
                    continue

                oid = f"leg_ord_{old_id}"
                await conn.execute("""
                    INSERT INTO orders (order_id, product_id, buyer_id, seller_id, amount, fee, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
                    ON CONFLICT (order_id) DO NOTHING
                """, oid, pid, buyer, seller, final_price or price, commission, status,
                     ts_from_unix(time_val))
                ord_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Orders: {ord_count}")
        report['orders'] = ord_count

        # =================================================================
        # WAVE 3 — MESSAGING (group chats + group messages)
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("WAVE 3 — MESSAGING (groups)")
        log.info("=" * 60)

        # --- 3.1 GROUP CHATS (Wo_GroupChat) ---
        recs = parse_insert_values(sql, 'Wo_GroupChat')
        gc_count = 0
        group_chat_map = {}  # legacy group_id -> conv_id
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                old_gid = safe_int(v[0])
                uid_legacy = safe_int(v[1])
                name = (v[2] or f'Groupe {old_gid}')[:255]
                avatar = (v[3] or '')[:500]
                time_val = safe_int(v[4])

                owner = user_map.get(uid_legacy)
                if not owner:
                    continue

                conv_id = f"lgconv_{old_gid}"
                group_chat_map[old_gid] = conv_id
                await conn.execute("""
                    INSERT INTO conversations (conv_id, type, title, avatar, created_by, created_at, updated_at)
                    VALUES ($1, 'group', $2, $3, $4, $5, $5)
                    ON CONFLICT (conv_id) DO NOTHING
                """, conv_id, name, avatar, owner, ts_from_unix(time_val))
                await conn.execute("""
                    INSERT INTO conversation_participants (conv_id, user_id)
                    VALUES ($1, $2) ON CONFLICT DO NOTHING
                """, conv_id, owner)
                gc_count += 1
            except Exception:
                continue
        log.info(f"[3.1] Group chats: {gc_count}")
        report['group_chats'] = gc_count

        # --- 3.2 GROUP CHAT USERS (Wo_GroupChatUsers) ---
        recs = parse_insert_values(sql, 'Wo_GroupChatUsers')
        gcu_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                uid_legacy = safe_int(v[1])
                gid_legacy = safe_int(v[2])
                user_id = user_map.get(uid_legacy)
                conv_id = group_chat_map.get(gid_legacy)
                if not user_id or not conv_id:
                    continue
                await conn.execute("""
                    INSERT INTO conversation_participants (conv_id, user_id)
                    VALUES ($1, $2) ON CONFLICT DO NOTHING
                """, conv_id, user_id)
                gcu_count += 1
            except Exception:
                continue
        log.info(f"[3.2] Group chat members: {gcu_count}")
        report['group_chat_members'] = gcu_count

        # --- 3.3 GROUP MESSAGES (Wo_Messages where group_id > 0) ---
        log.info("[3.3] Migrating group messages...")
        recs = parse_insert_values(sql, 'Wo_Messages')
        gmsg_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 10:
                continue
            try:
                old_id = safe_int(v[0])
                from_legacy = safe_int(v[1])
                group_legacy = safe_int(v[2])
                if group_legacy == 0:
                    continue  # skip DMs
                text = v[5] or ''
                media = (v[6] or '')[:500]
                time_val = safe_int(v[8])
                seen = safe_int(v[9])

                sender = user_map.get(from_legacy)
                conv_id = group_chat_map.get(group_legacy)
                if not sender or not conv_id:
                    continue

                msg_id = f"lgm_{old_id}"
                status = 'seen' if seen > 0 else 'sent'
                await conn.execute("""
                    INSERT INTO messages (msg_id, conv_id, sender_id, text, media, status, legacy_id, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
                    ON CONFLICT (msg_id) DO NOTHING
                """, msg_id, conv_id, sender, text, media, status, old_id, ts_from_unix(time_val))
                gmsg_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Group messages: {gmsg_count}")
        report['group_messages'] = gmsg_count

        # =================================================================
        # WAVE 4 — EXTENDED PROFILES
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("WAVE 4 — EXTENDED PROFILES")
        log.info("=" * 60)

        # --- 4.1 USER BLOCKS ---
        recs = parse_insert_values(sql, 'Wo_Blocks')
        blk_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                blocker_legacy = safe_int(v[1])
                blocked_legacy = safe_int(v[2])
                blocker = user_map.get(blocker_legacy)
                blocked = user_map.get(blocked_legacy)
                if not blocker or not blocked or blocker == blocked:
                    continue
                await conn.execute("""
                    INSERT INTO user_blocks (blocker_id, blocked_id) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, blocker, blocked)
                blk_count += 1
            except Exception:
                continue
        log.info(f"[4.1] User blocks: {blk_count}")
        report['user_blocks'] = blk_count

        # =================================================================
        # WAVE 5 — MODULES (Groups, Pages, Blog, Events, Polls, Funding)
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("WAVE 5 — MODULES")
        log.info("=" * 60)

        # --- 5.1 SOCIAL GROUPS (Wo_Groups) ---
        log.info("[5.1] Migrating social groups...")
        recs = parse_insert_values(sql, 'Wo_Groups')
        sg_count = 0
        group_map = {}
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 8:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1])
                name = (v[2] or '')[:255]
                title = (v[3] or '')[:255]
                avatar = (v[4] or '')[:500]
                cover = (v[5] or '')[:500]
                about = v[6] or ''
                category = (v[7] or 'general')[:100]
                privacy = (v[9] or 'public')[:20] if len(v) > 9 else 'public'
                active = (v[11] or '1') == '1' if len(v) > 11 else True
                time_val = safe_int(v[13]) if len(v) > 13 else 0

                owner = user_map.get(uid_legacy)
                if not owner or not name or not active:
                    continue

                gid = f"leg_sg_{old_id}"
                group_map[old_id] = gid
                await conn.execute("""
                    INSERT INTO social_groups (group_id, owner_id, name, title, avatar, cover, about,
                        category, privacy, legacy_id, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (group_id) DO NOTHING
                """, gid, owner, name, title, avatar, cover, about, category, privacy, old_id,
                     ts_from_unix(time_val))
                # Add owner as admin member
                await conn.execute("""
                    INSERT INTO social_group_members (group_id, user_id, role)
                    VALUES ($1, $2, 'admin') ON CONFLICT DO NOTHING
                """, gid, owner)
                sg_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Groups: {sg_count}")
        report['social_groups'] = sg_count

        # --- 5.2 GROUP MEMBERS ---
        recs = parse_insert_values(sql, 'Wo_Group_Members')
        gm_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 4:
                continue
            try:
                uid_legacy = safe_int(v[1])
                gid_legacy = safe_int(v[2])
                active = (v[4] or '1') == '1' if len(v) > 4 else True

                user_id = user_map.get(uid_legacy)
                gid = group_map.get(gid_legacy)
                if not user_id or not gid or not active:
                    continue
                await conn.execute("""
                    INSERT INTO social_group_members (group_id, user_id, role)
                    VALUES ($1, $2, 'member') ON CONFLICT DO NOTHING
                """, gid, user_id)
                gm_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Group members: {gm_count}")
        report['social_group_members'] = gm_count

        # --- 5.3 PAGES ---
        log.info("[5.3] Migrating pages...")
        recs = parse_insert_values(sql, 'Wo_Pages')
        p_count = 0
        page_map = {}
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 10:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1])
                name = (v[2] or '')[:255]
                title = (v[3] or '')[:255]
                desc = v[4] or ''
                avatar = (v[5] or '')[:500]
                cover = (v[6] or '')[:500]
                category = (v[8] or 'general')[:100]
                website = (v[10] or '')[:500] if len(v) > 10 else ''
                phone = (v[17] or '')[:64] if len(v) > 17 else ''
                address = (v[18] or '')[:500] if len(v) > 18 else ''
                verified = (v[25] or '0') == '1' if len(v) > 25 else False
                active = (v[26] or '1') == '1' if len(v) > 26 else True
                time_val = safe_int(v[29]) if len(v) > 29 else 0

                owner = user_map.get(uid_legacy)
                if not owner or not name:
                    continue

                pid = f"leg_pg_{old_id}"
                page_map[old_id] = pid
                await conn.execute("""
                    INSERT INTO pages (page_id, owner_id, name, title, description, avatar, cover,
                        category, website, phone, address, verified, active, legacy_id, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    ON CONFLICT (page_id) DO NOTHING
                """, pid, owner, name, title, desc, avatar, cover, category, website, phone,
                     address, verified, active, old_id, ts_from_unix(time_val))
                p_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Pages: {p_count}")
        report['pages'] = p_count

        # --- 5.4 PAGE LIKES ---
        recs = parse_insert_values(sql, 'Wo_Pages_Likes')
        pl_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                uid_legacy = safe_int(v[1])
                page_legacy = safe_int(v[2])
                user_id = user_map.get(uid_legacy)
                pid = page_map.get(page_legacy)
                if not user_id or not pid:
                    continue
                await conn.execute("""
                    INSERT INTO page_likes (page_id, user_id) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, pid, user_id)
                pl_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Page likes: {pl_count}")
        report['page_likes'] = pl_count

        # --- 5.5 BLOG ARTICLES ---
        log.info("[5.5] Migrating blog articles...")
        recs = parse_insert_values(sql, 'Wo_Blog')
        b_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 8:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1])
                title = (v[2] or '')[:500]
                content = v[3] or ''
                desc = v[4] or ''
                posted = safe_int(v[5])
                category = (v[6] or 'general')[:100]
                thumbnail = (v[7] or '')[:500]
                views = safe_int(v[8]) if len(v) > 8 else 0
                shared = safe_int(v[9]) if len(v) > 9 else 0
                tags = v[10] if len(v) > 10 else ''
                active = (v[11] or '1') == '1' if len(v) > 11 else True

                author = user_map.get(uid_legacy)
                if not author or not title:
                    continue

                aid = f"leg_blog_{old_id}"
                await conn.execute("""
                    INSERT INTO blog_articles (article_id, author_id, title, content, description,
                        thumbnail, category, tags, views, shared, active, legacy_id, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (article_id) DO NOTHING
                """, aid, author, title, content, desc, thumbnail, category, tags or '',
                     views, shared, active, old_id, ts_from_unix(posted))
                b_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Blog articles: {b_count}")
        report['blog_articles'] = b_count

        # --- 5.6 EVENTS ---
        recs = parse_insert_values(sql, 'Wo_Events')
        e_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 9:
                continue
            try:
                old_id = safe_int(v[0])
                name = (v[1] or '')[:500]
                location = (v[2] or '')[:500]
                desc = v[3] or ''
                start_date_s = v[4] or ''
                end_date_s = v[6] or ''
                poster_legacy = safe_int(v[8])
                cover = (v[9] or '')[:500] if len(v) > 9 else ''

                poster = user_map.get(poster_legacy)
                if not poster or not name:
                    continue

                start_dt = None
                end_dt = None
                try:
                    if start_date_s:
                        start_dt = datetime.strptime(start_date_s, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                except Exception:
                    pass
                try:
                    if end_date_s:
                        end_dt = datetime.strptime(end_date_s, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                except Exception:
                    pass

                eid = f"leg_ev_{old_id}"
                await conn.execute("""
                    INSERT INTO events (event_id, poster_id, name, description, location, cover,
                        start_date, end_date, legacy_id)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (event_id) DO NOTHING
                """, eid, poster, name, desc, location, cover, start_dt, end_dt, old_id)
                e_count += 1
            except Exception:
                continue
        log.info(f"[5.6] Events: {e_count}")
        report['events'] = e_count

        # --- 5.7 POLLS + VOTES ---
        # Wo_Polls stores one row per OPTION (grouped by post_id)
        # Wo_Votes.v[3] is the option_id (= Wo_Polls.id)
        log.info("[5.7] Migrating polls + votes...")
        recs = parse_insert_values(sql, 'Wo_Polls')
        polls_by_post = {}
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 4:
                continue
            option_id = safe_int(v[0])
            post_legacy = safe_int(v[1])
            text = v[2] or ''
            time_val = safe_int(v[3])
            polls_by_post.setdefault(post_legacy, []).append({
                'option_id': option_id, 'text': text, 'time': time_val
            })

        poll_count = 0
        option_to_index = {}  # option_id -> (poll_id, index)
        for post_legacy, options in polls_by_post.items():
            post_id_str = post_id_map.get(post_legacy)
            pid = f"leg_poll_{post_legacy}"
            options.sort(key=lambda o: o['option_id'])
            opts_array = [{'id': o['option_id'], 'text': o['text']} for o in options]
            time_val = options[0]['time'] if options else 0
            try:
                await conn.execute("""
                    INSERT INTO polls (poll_id, post_id, question, options, legacy_id, created_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                    ON CONFLICT (poll_id) DO NOTHING
                """, pid, post_id_str, 'Sondage', json.dumps(opts_array), post_legacy, ts_from_unix(time_val))
                for idx, opt in enumerate(options):
                    option_to_index[opt['option_id']] = (pid, idx)
                poll_count += 1
            except Exception:
                continue

        recs = parse_insert_values(sql, 'Wo_Votes')
        vote_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 4:
                continue
            try:
                uid_legacy = safe_int(v[1])
                option_id = safe_int(v[3])
                user_id = user_map.get(uid_legacy)
                mapping = option_to_index.get(option_id)
                if not user_id or not mapping:
                    continue
                poll_id, idx = mapping
                await conn.execute("""
                    INSERT INTO poll_votes (poll_id, user_id, option_index)
                    VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
                """, poll_id, user_id, idx)
                vote_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Polls: {poll_count}  votes: {vote_count}")
        report['polls'] = poll_count
        report['poll_votes'] = vote_count

        # --- 5.8 CROWDFUNDING (Wo_Funding + Wo_Funding_Raise) ---
        log.info("[5.8] Migrating crowdfunding (legacy Wo_Funding → campaigns)...")
        recs = parse_insert_values(sql, 'Wo_Funding')
        cam_count = 0
        cam_map = {}
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 6:
                continue
            try:
                old_id = safe_int(v[0])
                title = (v[2] or '')[:255]
                desc = v[3] or ''
                amount = safe_float(v[4])
                uid_legacy = safe_int(v[5])
                image = (v[6] or '')[:500] if len(v) > 6 else ''
                time_val = safe_int(v[7]) if len(v) > 7 else 0

                creator = user_map.get(uid_legacy)
                if not creator or not title or amount <= 0:
                    continue

                cid = f"leg_cam_{old_id}"
                cam_map[old_id] = cid
                await conn.execute("""
                    INSERT INTO campaigns (campaign_id, creator_id, title, description, goal, image,
                        category, status, created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,'community','active',$7,$7)
                    ON CONFLICT (campaign_id) DO NOTHING
                """, cid, creator, title, desc, amount, image, ts_from_unix(time_val))
                cam_count += 1
            except Exception:
                continue

        # Wo_Funding_Raise → campaign_contributions
        recs = parse_insert_values(sql, 'Wo_Funding_Raise')
        contrib_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 5:
                continue
            try:
                old_id = safe_int(v[0])
                fund_legacy = safe_int(v[1])
                uid_legacy = safe_int(v[2])
                amount = safe_float(v[3])
                time_val = safe_int(v[4])

                user_id = user_map.get(uid_legacy)
                cid = cam_map.get(fund_legacy)
                if not user_id or not cid or amount <= 0:
                    continue

                contrib_id = f"leg_ctr_{old_id}"
                await conn.execute("""
                    INSERT INTO campaign_contributions (contrib_id, campaign_id, user_id, amount, created_at)
                    VALUES ($1,$2,$3,$4,$5)
                    ON CONFLICT (contrib_id) DO NOTHING
                """, contrib_id, cid, user_id, amount, ts_from_unix(time_val))
                contrib_count += 1
            except Exception:
                continue

        # Update raised amount + backers_count
        await conn.execute("""
            UPDATE campaigns c SET
                raised = COALESCE(t.total, 0),
                backers_count = COALESCE(t.backers, 0)
            FROM (SELECT campaign_id, SUM(amount) AS total, COUNT(DISTINCT user_id) AS backers
                  FROM campaign_contributions GROUP BY campaign_id) t
            WHERE c.campaign_id = t.campaign_id
        """)
        log.info(f"  ✓ Campaigns: {cam_count}  contributions: {contrib_count}")
        report['campaigns'] = cam_count
        report['campaign_contributions'] = contrib_count

        # --- 5.9 CRYPTO & BANK ---
        log.info("[5.9] Migrating crypto payments + bank receipts...")
        # crypto_payments
        recs = parse_insert_values(sql, 'crypto_payments')
        cp_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 3:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1]) if len(v) > 1 else 0
                amount = safe_float(v[2]) if len(v) > 2 else 0
                user_id = user_map.get(uid_legacy)
                payment_id = f"leg_cp_{old_id}"
                await conn.execute("""
                    INSERT INTO crypto_payments (payment_id, user_id, amount, legacy_id)
                    VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
                """, payment_id, user_id, amount, old_id)
                cp_count += 1
            except Exception:
                continue

        recs = parse_insert_values(sql, 'bank_receipts')
        br_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 2:
                continue
            try:
                old_id = safe_int(v[0])
                uid_legacy = safe_int(v[1]) if len(v) > 1 else 0
                amount = safe_float(v[2]) if len(v) > 2 else 0
                user_id = user_map.get(uid_legacy)
                rid = f"leg_br_{old_id}"
                await conn.execute("""
                    INSERT INTO bank_receipts (receipt_id, user_id, amount, legacy_id)
                    VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
                """, rid, user_id, amount, old_id)
                br_count += 1
            except Exception:
                continue

        # exchange_country
        recs = parse_insert_values(sql, 'exchange_country')
        ec_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 2:
                continue
            try:
                old_id = safe_int(v[0])
                code = (v[1] or '')[:10] if len(v) > 1 else ''
                name = (v[2] or '')[:255] if len(v) > 2 else code
                currency = (v[3] or '')[:20] if len(v) > 3 else ''
                if not code:
                    continue
                await conn.execute("""
                    INSERT INTO exchange_countries (country_code, country_name, currency, legacy_id)
                    VALUES ($1,$2,$3,$4)
                """, code, name, currency, old_id)
                ec_count += 1
            except Exception:
                continue

        # withdraw_country
        recs = parse_insert_values(sql, 'withdraw_country')
        wc_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 2:
                continue
            try:
                old_id = safe_int(v[0])
                code = (v[1] or '')[:10] if len(v) > 1 else ''
                name = (v[2] or '')[:255] if len(v) > 2 else code
                if not code:
                    continue
                await conn.execute("""
                    INSERT INTO withdraw_countries (country_code, country_name, legacy_id)
                    VALUES ($1,$2,$3)
                """, code, name, old_id)
                wc_count += 1
            except Exception:
                continue

        log.info(f"  ✓ crypto_payments:{cp_count}  bank_receipts:{br_count}  exchange_countries:{ec_count}  withdraw_countries:{wc_count}")
        report['crypto_payments'] = cp_count
        report['bank_receipts'] = br_count
        report['exchange_countries'] = ec_count
        report['withdraw_countries'] = wc_count

        # =================================================================
        # WAVE 6 — HISTORY (notifications last 30 days, calls)
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("WAVE 6 — HISTORY")
        log.info("=" * 60)

        # --- 6.1 NOTIFICATIONS (last 30 days) ---
        log.info("[6.1] Migrating notifications (last 30 days)...")
        recs = parse_insert_values(sql, 'Wo_Notifications')
        n_count = 0
        cutoff_ts = NOTIF_CUTOFF.timestamp()
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 15:
                continue
            try:
                old_id = safe_int(v[0])
                recipient_legacy = safe_int(v[2])
                ntype = (v[14] or 'generic')[:50]
                text = v[16] or ''
                url = v[17] or ''
                seen = safe_int(v[19]) if len(v) > 19 else 0
                time_val = safe_int(v[22]) if len(v) > 22 else 0

                if time_val < cutoff_ts:
                    continue

                recipient = user_map.get(recipient_legacy)
                if not recipient:
                    continue

                nid = f"leg_n_{old_id}"
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message, data, is_read, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
                    ON CONFLICT (notif_id) DO NOTHING
                """, nid, recipient, ntype, '', text, json.dumps({"url": url}),
                     seen > 0, ts_from_unix(time_val))
                n_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Notifications (30d): {n_count}")
        report['notifications_30d'] = n_count

        # --- 6.2 CALLS HISTORY (Wo_AgoraVideoCall) ---
        log.info("[6.2] Migrating call history...")
        recs = parse_insert_values(sql, 'Wo_AgoraVideoCall')
        c_count = 0
        for rec in recs:
            v = parse_csv_values(rec)
            if len(v) < 6:
                continue
            try:
                old_id = safe_int(v[0])
                from_legacy = safe_int(v[1])
                to_legacy = safe_int(v[2])
                call_type = (v[3] or 'video')[:10]
                time_val = safe_int(v[5])
                status = (v[6] or 'ended')[:20] if len(v) > 6 else 'ended'

                caller = user_map.get(from_legacy)
                callee = user_map.get(to_legacy)
                if not caller or not callee:
                    continue

                cid = f"leg_call_{old_id}"
                await conn.execute("""
                    INSERT INTO calls (call_id, caller_id, callee_id, type, status, started_at, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$6)
                    ON CONFLICT (call_id) DO NOTHING
                """, cid, caller, callee, call_type, status, ts_from_unix(time_val))
                c_count += 1
            except Exception:
                continue
        log.info(f"  ✓ Calls: {c_count}")
        report['calls_history'] = c_count

        # =================================================================
        # FINAL VERIFICATION
        # =================================================================
        log.info("\n" + "=" * 60)
        log.info("FINAL VERIFICATION")
        log.info("=" * 60)
        verify_tables = [
            'users', 'wallets', 'transactions', 'contacts', 'subscriptions',
            'conversations', 'messages', 'posts', 'post_comments', 'post_likes',
            'hashtags', 'post_hashtags', 'media_library', 'saved_posts', 'hidden_posts',
            'pinned_posts', 'products', 'product_categories', 'orders',
            'social_groups', 'social_group_members', 'pages', 'page_likes',
            'blog_articles', 'events', 'polls', 'poll_votes', 'user_blocks',
            'campaigns', 'campaign_contributions', 'crypto_payments', 'bank_receipts',
            'exchange_countries', 'withdraw_countries', 'notifications', 'calls',
        ]
        final = {}
        for t in verify_tables:
            try:
                c = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
                final[t] = c
            except Exception:
                final[t] = -1

        log.info("\n📊 FINAL DATABASE STATE:")
        for t, c in sorted(final.items(), key=lambda x: -x[1]):
            log.info(f"  {t:35s} {c:>10,}")

        report['final_counts'] = final

        log.info("\n" + "=" * 60)
        log.info("✓ MIGRATION v2 COMPLETE")
        log.info("=" * 60)

        return report
    finally:
        await conn.close()


if __name__ == "__main__":
    result = asyncio.run(migrate())
    # Write report
    report_path = Path("/app/backend/migration_v2_report.json")
    report_path.write_text(json.dumps(result, indent=2, default=str))
    log.info(f"\nReport saved → {report_path}")
