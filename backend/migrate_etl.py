"""
JAPAP Messenger - ETL Migration Script
Migrates data from WoWonder MySQL dump (wowonder.sql) to PostgreSQL
"""
import re
import os
import sys
import asyncio
import asyncpg
import hashlib
import uuid
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SQL_FILE = "/tmp/japap_source/wowonder.sql"
DATABASE_URL = "postgresql://japap:japap_secure_2024@localhost:5432/japap_messenger"


def parse_insert_values(sql_content, table_name):
    """Parse INSERT INTO statements and extract tuples of values."""
    pattern = rf"INSERT INTO `{table_name}` .*? VALUES\s*(.*?);\s*$"
    matches = re.findall(pattern, sql_content, re.MULTILINE | re.DOTALL)
    
    all_records = []
    for match in matches:
        # Split on ),( to get individual records
        # Handle nested parentheses and strings with commas
        depth = 0
        current = ""
        in_string = False
        escape_next = False
        
        for char in match:
            if escape_next:
                current += char
                escape_next = False
                continue
            if char == '\\':
                current += char
                escape_next = True
                continue
            if char == "'" and not escape_next:
                in_string = not in_string
                current += char
                continue
            if not in_string:
                if char == '(':
                    depth += 1
                    if depth == 1:
                        current = ""
                        continue
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        all_records.append(current)
                        current = ""
                        continue
                current += char
            else:
                current += char
    
    return all_records


def parse_csv_values(record_str):
    """Parse a single record's CSV values, handling quoted strings."""
    values = []
    current = ""
    in_string = False
    escape_next = False
    
    for char in record_str:
        if escape_next:
            current += char
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == "'" and not escape_next:
            in_string = not in_string
            continue
        if char == ',' and not in_string:
            values.append(current.strip())
            current = ""
            continue
        current += char
    
    if current.strip():
        values.append(current.strip())
    
    # Clean values
    cleaned = []
    for v in values:
        if v == 'NULL' or v == 'null':
            cleaned.append(None)
        else:
            cleaned.append(v)
    return cleaned


async def migrate():
    logger.info("=== JAPAP MESSENGER ETL MIGRATION ===")
    logger.info(f"Reading SQL file: {SQL_FILE}")
    
    with open(SQL_FILE, 'r', encoding='utf-8', errors='replace') as f:
        sql_content = f.read()
    
    logger.info(f"SQL file loaded: {len(sql_content)} bytes")
    
    conn = await asyncpg.connect(DATABASE_URL)
    
    try:
        # ===== PHASE 1: EXTRACT & TRANSFORM USERS =====
        logger.info("\n--- PHASE 1: MIGRATING USERS ---")
        user_records = parse_insert_values(sql_content, 'Wo_Users')
        logger.info(f"Found {len(user_records)} user record groups")
        
        user_count = 0
        user_id_map = {}  # old_id -> new_user_id
        
        for record_str in user_records:
            values = parse_csv_values(record_str)
            if len(values) < 10:
                continue
            
            try:
                old_id = int(values[0]) if values[0] else None
                if not old_id:
                    continue
                
                username = (values[1] or f'user_{old_id}')[:64]
                email = (values[2] or f'user{old_id}@japap.local')[:255]
                password_hash = values[3] or ''
                first_name = (values[4] or '')[:100]
                last_name = (values[5] or '')[:100]
                avatar = (values[6] or '')[:500]
                cover = (values[7] or '')[:500]
                about = values[14] if len(values) > 14 else ''
                gender = (values[16] or '')[:20] if len(values) > 16 else ''
                birthday = (values[17] or '')[:50] if len(values) > 17 else ''
                language = (values[32] or 'en')[:50] if len(values) > 32 else 'en'
                phone_number = (values[79] or '')[:32] if len(values) > 79 else ''
                
                # Status fields
                is_active = values[69] == '1' if len(values) > 69 else True
                is_admin = values[70] == '1' if len(values) > 70 else False
                is_verified = values[46] == '1' if len(values) > 46 else False
                is_pro = values[81] == '1' if len(values) > 81 else False
                pro_type = int(values[83]) if len(values) > 83 and values[83] and values[83].isdigit() else 0
                
                wallet_balance = values[98] if len(values) > 98 else '0'
                try:
                    wallet_balance = float(wallet_balance or 0)
                except:
                    wallet_balance = 0.0
                
                social_login = values[99] == '1' if len(values) > 99 else False
                
                new_user_id = f"legacy_{old_id}"
                user_id_map[old_id] = new_user_id
                
                role = 'admin' if is_admin else 'user'
                
                # Check if already migrated
                existing = await conn.fetchrow("SELECT user_id FROM users WHERE legacy_id = $1 OR user_id = $2", old_id, new_user_id)
                if existing:
                    user_count += 1
                    continue
                
                # Check email uniqueness
                email_exists = await conn.fetchrow("SELECT user_id FROM users WHERE email = $1", email.lower())
                if email_exists:
                    email = f"legacy{old_id}_{email}"
                
                # Check username uniqueness
                uname_exists = await conn.fetchrow("SELECT user_id FROM users WHERE username = $1", username)
                if uname_exists:
                    username = f"{username}_{old_id}"
                
                await conn.execute("""
                    INSERT INTO users (user_id, username, email, password_hash, first_name, last_name, 
                    avatar, cover, about, gender, birthday, language, phone_number,
                    role, is_active, is_verified, is_pro, pro_type, social_login, legacy_id, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
                    ON CONFLICT (user_id) DO NOTHING
                """, new_user_id, username[:64], email.lower()[:255], password_hash, first_name, last_name,
                   avatar, cover, about or '', gender, birthday, language, phone_number,
                   role, is_active, is_verified, is_pro, pro_type, social_login, old_id,
                   datetime.now(timezone.utc))
                
                # Create wallet
                await conn.execute("""
                    INSERT INTO wallets (user_id, balance) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING
                """, new_user_id, wallet_balance)
                
                user_count += 1
                
            except Exception as e:
                logger.warning(f"  User record error: {e}")
                continue
        
        logger.info(f"  Migrated {user_count} users")
        
        # ===== PHASE 2: MIGRATE TRANSACTIONS =====
        logger.info("\n--- PHASE 2: MIGRATING TRANSACTIONS ---")
        tx_records = parse_insert_values(sql_content, 'Wo_Payment_Transactions')
        tx_count = 0
        
        for record_str in tx_records:
            values = parse_csv_values(record_str)
            if len(values) < 5:
                continue
            try:
                old_id = int(values[0]) if values[0] else None
                userid = int(values[1]) if values[1] else None
                kind = values[2] or 'UNKNOWN'
                amount = float(values[3]) if values[3] else 0
                tx_dt = values[4] or ''
                notes = values[5] if len(values) > 5 else ''
                transfer_to = int(values[7]) if len(values) > 7 and values[7] and values[7].isdigit() else None
                fee = float(values[8]) if len(values) > 8 and values[8] else 0
                
                from_uid = user_id_map.get(userid)
                to_uid = user_id_map.get(transfer_to) if transfer_to else None
                
                if not from_uid:
                    continue
                
                tx_id = f"leg_tx_{old_id}"
                tx_type = 'send' if kind == 'SENT' else 'deposit' if kind in ('RECEIVED', 'WALLET') else 'subscription' if kind == 'PRO' else kind.lower()
                
                # For SENT type, from is sender and we need to figure out to
                if kind == 'SENT':
                    actual_from = from_uid
                    actual_to = to_uid
                elif kind == 'RECEIVED':
                    actual_from = to_uid
                    actual_to = from_uid
                    tx_type = 'send'
                else:
                    actual_from = None
                    actual_to = from_uid
                
                existing = await conn.fetchrow("SELECT id FROM transactions WHERE legacy_id = $1", old_id)
                if existing:
                    tx_count += 1
                    continue
                
                await conn.execute("""
                    INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, fee, status, notes, legacy_id, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, 'completed', $7, $8, $9)
                    ON CONFLICT (tx_id) DO NOTHING
                """, tx_id, actual_from, actual_to, tx_type, abs(amount), fee, notes or '',
                   old_id, datetime.now(timezone.utc))
                
                tx_count += 1
            except Exception as e:
                logger.warning(f"  Transaction error: {e}")
                continue
        
        logger.info(f"  Migrated {tx_count} transactions")
        
        # ===== PHASE 3: MIGRATE PAYMENTS (Subscriptions) =====
        logger.info("\n--- PHASE 3: MIGRATING PAYMENTS/SUBSCRIPTIONS ---")
        pay_records = parse_insert_values(sql_content, 'Wo_Payments')
        pay_count = 0
        
        for record_str in pay_records:
            values = parse_csv_values(record_str)
            if len(values) < 5:
                continue
            try:
                old_id = int(values[0]) if values[0] else None
                userid = int(values[1]) if values[1] else None
                amount = float(values[2]) if values[2] else 0
                plan_type = values[3] or 'basic'
                
                uid = user_id_map.get(userid)
                if not uid:
                    continue
                
                existing = await conn.fetchrow("SELECT id FROM subscriptions WHERE legacy_id = $1", old_id)
                if existing:
                    pay_count += 1
                    continue
                
                await conn.execute("""
                    INSERT INTO subscriptions (user_id, plan_type, price, status, legacy_id, created_at)
                    VALUES ($1, $2, $3, 'expired', $4, $5)
                    ON CONFLICT DO NOTHING
                """, uid, plan_type, amount, old_id, datetime.now(timezone.utc))
                
                pay_count += 1
            except Exception as e:
                logger.warning(f"  Payment error: {e}")
                continue
        
        logger.info(f"  Migrated {pay_count} subscription records")
        
        # ===== PHASE 4: MIGRATE MESSAGES & CONVERSATIONS =====
        logger.info("\n--- PHASE 4: MIGRATING MESSAGES ---")
        msg_records = parse_insert_values(sql_content, 'Wo_Messages')
        msg_count = 0
        conv_cache = {}
        
        for record_str in msg_records:
            values = parse_csv_values(record_str)
            if len(values) < 6:
                continue
            try:
                old_id = int(values[0]) if values[0] else None
                from_id = int(values[1]) if values[1] else None
                group_id = int(values[2]) if values[2] else 0
                page_id = int(values[3]) if values[3] else 0
                to_id = int(values[4]) if values[4] else None
                text = values[5] or ''
                media = (values[6] or '')[:500] if len(values) > 6 else ''
                time_val = int(values[8]) if len(values) > 8 and values[8] and values[8].isdigit() else 0
                seen = int(values[9]) if len(values) > 9 and values[9] and values[9].isdigit() else 0
                
                sender_uid = user_id_map.get(from_id)
                receiver_uid = user_id_map.get(to_id)
                
                if not sender_uid or not receiver_uid:
                    continue
                if group_id > 0 or page_id > 0:
                    continue  # Skip group/page messages for now
                
                # Get or create conversation
                conv_key = tuple(sorted([sender_uid, receiver_uid]))
                if conv_key not in conv_cache:
                    existing_conv = await conn.fetchrow("""
                        SELECT c.conv_id FROM conversations c
                        JOIN conversation_participants cp1 ON c.conv_id = cp1.conv_id AND cp1.user_id = $1
                        JOIN conversation_participants cp2 ON c.conv_id = cp2.conv_id AND cp2.user_id = $2
                        WHERE c.type = 'direct' LIMIT 1
                    """, sender_uid, receiver_uid)
                    
                    if existing_conv:
                        conv_cache[conv_key] = existing_conv['conv_id']
                    else:
                        conv_id = f"lconv_{uuid.uuid4().hex[:10]}"
                        await conn.execute("INSERT INTO conversations (conv_id, type, created_by) VALUES ($1, 'direct', $2)", conv_id, sender_uid)
                        await conn.execute("INSERT INTO conversation_participants (conv_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", conv_id, sender_uid)
                        await conn.execute("INSERT INTO conversation_participants (conv_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", conv_id, receiver_uid)
                        conv_cache[conv_key] = conv_id
                
                conv_id = conv_cache[conv_key]
                msg_id = f"lmsg_{old_id}"
                status = 'seen' if seen > 0 else 'sent'
                
                created_at = datetime.fromtimestamp(time_val, tz=timezone.utc) if time_val > 0 else datetime.now(timezone.utc)
                
                existing = await conn.fetchrow("SELECT id FROM messages WHERE legacy_id = $1", old_id)
                if existing:
                    msg_count += 1
                    continue
                
                await conn.execute("""
                    INSERT INTO messages (msg_id, conv_id, sender_id, text, media, status, legacy_id, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (msg_id) DO NOTHING
                """, msg_id, conv_id, sender_uid, text, media, status, old_id, created_at)
                
                msg_count += 1
            except Exception as e:
                logger.warning(f"  Message error: {e}")
                continue
        
        logger.info(f"  Migrated {msg_count} messages, {len(conv_cache)} conversations")
        
        # ===== PHASE 5: MIGRATE CONTACTS (Followers) =====
        logger.info("\n--- PHASE 5: MIGRATING CONTACTS ---")
        follower_records = parse_insert_values(sql_content, 'Wo_Followers')
        contact_count = 0
        
        for record_str in follower_records:
            values = parse_csv_values(record_str)
            if len(values) < 3:
                continue
            try:
                following_id = int(values[1]) if values[1] else None
                follower_id = int(values[2]) if values[2] else None
                
                uid1 = user_id_map.get(following_id)
                uid2 = user_id_map.get(follower_id)
                
                if not uid1 or not uid2 or uid1 == uid2:
                    continue
                
                await conn.execute("""
                    INSERT INTO contacts (user_id, contact_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING
                """, uid2, uid1)
                
                contact_count += 1
            except Exception as e:
                continue
        
        logger.info(f"  Migrated {contact_count} contact relations")
        
        # ===== PHASE 6: VERIFICATION =====
        logger.info("\n--- PHASE 6: VERIFICATION ---")
        
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_wallets = await conn.fetchval("SELECT COUNT(*) FROM wallets")
        total_txs = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        total_msgs = await conn.fetchval("SELECT COUNT(*) FROM messages")
        total_convs = await conn.fetchval("SELECT COUNT(*) FROM conversations")
        total_contacts = await conn.fetchval("SELECT COUNT(*) FROM contacts")
        total_subs = await conn.fetchval("SELECT COUNT(*) FROM subscriptions")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM wallets")
        legacy_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE legacy_id IS NOT NULL")
        
        logger.info(f"  Total users:          {total_users} (legacy: {legacy_users})")
        logger.info(f"  Total wallets:        {total_wallets}")
        logger.info(f"  Total transactions:   {total_txs}")
        logger.info(f"  Total messages:       {total_msgs}")
        logger.info(f"  Total conversations:  {total_convs}")
        logger.info(f"  Total contacts:       {total_contacts}")
        logger.info(f"  Total subscriptions:  {total_subs}")
        logger.info(f"  Total wallet balance: {total_balance}")
        
        logger.info("\n=== MIGRATION COMPLETE ===")
        
        return {
            "users": total_users,
            "legacy_users": legacy_users,
            "wallets": total_wallets,
            "transactions": total_txs,
            "messages": total_msgs,
            "conversations": total_convs,
            "contacts": total_contacts,
            "subscriptions": total_subs,
            "total_balance": str(total_balance)
        }
        
    finally:
        await conn.close()


if __name__ == "__main__":
    result = asyncio.run(migrate())
    print(json.dumps(result, indent=2))
