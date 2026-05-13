import asyncpg
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

pool = None

async def get_pool():
    """Returns the global asyncpg pool, recreating it if closed.

    iter237o — Some workers/tests can leave the pool in a closed/closing
    state (especially with uvicorn --reload). Without this guard, any
    subsequent acquire() raises 'pool is closing' indefinitely. We detect
    that state via asyncpg's private flags and rebuild transparently.
    """
    global pool
    # Detect a stale/closed pool and reset so we can recreate it fresh.
    if pool is not None:
        try:
            if getattr(pool, "_closed", False) or getattr(pool, "_closing", False):
                pool = None
        except Exception:  # noqa: BLE001 — defensive
            pool = None
    if pool is None:
        # statement_cache_size=0 is required for Neon's PgBouncer-style pooler —
        # without it, any schema change (e.g. ADD COLUMN at boot migrations) can
        # trigger `asyncpg.exceptions.InvalidCachedStatementError` on already-
        # prepared statements held by other connections in the pool.
        pool = await asyncpg.create_pool(
            os.environ['DATABASE_URL'],
            min_size=2,
            max_size=10,
            statement_cache_size=0,
        )
    return pool

async def init_db():
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) UNIQUE NOT NULL,
                username VARCHAR(64) UNIQUE,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255),
                first_name VARCHAR(100) NOT NULL DEFAULT '',
                last_name VARCHAR(100) NOT NULL DEFAULT '',
                phone_number VARCHAR(32) DEFAULT '',
                avatar VARCHAR(500) DEFAULT '',
                cover VARCHAR(500) DEFAULT '',
                about TEXT DEFAULT '',
                gender VARCHAR(20) DEFAULT '',
                birthday VARCHAR(50) DEFAULT '',
                country VARCHAR(100) DEFAULT '',
                language VARCHAR(50) DEFAULT 'en',
                role VARCHAR(20) NOT NULL DEFAULT 'user',
                is_pro BOOLEAN DEFAULT FALSE,
                pro_type INTEGER DEFAULT 0,
                pro_expires_at TIMESTAMPTZ,
                is_verified BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                is_online BOOLEAN DEFAULT FALSE,
                last_seen TIMESTAMPTZ,
                social_login BOOLEAN DEFAULT FALSE,
                google_id VARCHAR(255),
                legacy_id INTEGER,
                terms_accepted BOOLEAN DEFAULT FALSE,
                terms_accepted_at TIMESTAMPTZ,
                referral_code VARCHAR(32) UNIQUE,
                referred_by VARCHAR(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS wallets (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) UNIQUE NOT NULL REFERENCES users(user_id),
                balance DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                is_locked BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                tx_id VARCHAR(64) UNIQUE NOT NULL,
                from_user_id VARCHAR(64) REFERENCES users(user_id),
                to_user_id VARCHAR(64) REFERENCES users(user_id),
                type VARCHAR(30) NOT NULL,
                amount DECIMAL(15,2) NOT NULL,
                fee DECIMAL(15,2) DEFAULT 0.00,
                currency VARCHAR(10) DEFAULT 'USD',
                status VARCHAR(20) NOT NULL DEFAULT 'completed',
                notes TEXT DEFAULT '',
                admin_notes TEXT DEFAULT '',
                reference VARCHAR(255) DEFAULT '',
                legacy_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- iter141nine — Payment Requests ("Demander à recevoir"). A user
            -- creates a payment request, gets a shareable link/QR/WhatsApp
            -- prefilled message; the payer lands on /pay/<request_id>, logs
            -- in if needed, then fulfills via the existing send_money flow.
            CREATE TABLE IF NOT EXISTS payment_requests (
                id SERIAL PRIMARY KEY,
                request_id VARCHAR(40) UNIQUE NOT NULL,
                requester_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                amount DECIMAL(15,2) NOT NULL CHECK (amount > 0),
                currency VARCHAR(10) NOT NULL DEFAULT 'XAF',
                note TEXT DEFAULT '',
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                fulfilled_tx_id VARCHAR(64) REFERENCES transactions(tx_id),
                fulfilled_by VARCHAR(64) REFERENCES users(user_id),
                fulfilled_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_payment_requests_requester
                ON payment_requests(requester_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_payment_requests_status
                ON payment_requests(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(64) UNIQUE NOT NULL,
                type VARCHAR(20) NOT NULL DEFAULT 'direct',
                title VARCHAR(255) DEFAULT '',
                created_by VARCHAR(64) REFERENCES users(user_id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS conversation_participants (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(64) NOT NULL REFERENCES conversations(conv_id),
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_read_at TIMESTAMPTZ,
                is_muted BOOLEAN DEFAULT FALSE,
                UNIQUE(conv_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                msg_id VARCHAR(64) UNIQUE NOT NULL,
                conv_id VARCHAR(64) NOT NULL REFERENCES conversations(conv_id),
                sender_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                text TEXT DEFAULT '',
                media VARCHAR(500) DEFAULT '',
                reply_to VARCHAR(64),
                is_forwarded BOOLEAN DEFAULT FALSE,
                status VARCHAR(20) DEFAULT 'sent',
                legacy_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                contact_user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(user_id, contact_user_id)
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                plan_type VARCHAR(50) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                currency VARCHAR(10) DEFAULT 'XAF',
                status VARCHAR(20) DEFAULT 'active',
                starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ,
                legacy_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- ============== JAPAP PRO PLANS ==============
            CREATE TABLE IF NOT EXISTS pro_plans (
                id SERIAL PRIMARY KEY,
                plan_id VARCHAR(32) UNIQUE NOT NULL,
                name VARCHAR(64) NOT NULL,
                tagline VARCHAR(255) DEFAULT '',
                price_usd DECIMAL(10,2) NOT NULL,
                duration_days INTEGER NOT NULL DEFAULT 30,
                features JSONB NOT NULL DEFAULT '[]'::jsonb,
                limits JSONB NOT NULL DEFAULT '{}'::jsonb,
                trial_eligible BOOLEAN DEFAULT TRUE,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_pro_plans_active ON pro_plans(is_active);

            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                notif_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                type VARCHAR(50) NOT NULL,
                title VARCHAR(255) DEFAULT '',
                message TEXT DEFAULT '',
                data JSONB DEFAULT '{}',
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64),
                action VARCHAR(100) NOT NULL,
                resource VARCHAR(100) DEFAULT '',
                details JSONB DEFAULT '{}',
                ip_address VARCHAR(100) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id SERIAL PRIMARY KEY,
                identifier VARCHAR(255) NOT NULL,
                attempts INTEGER DEFAULT 1,
                last_attempt TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                locked_until TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                token VARCHAR(255) UNIQUE NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                session_token VARCHAR(255) UNIQUE NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS admin_settings (
                id SERIAL PRIMARY KEY,
                key VARCHAR(100) UNIQUE NOT NULL,
                value TEXT DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_from ON transactions(from_user_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_to ON transactions(to_user_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type);
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id);
            CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
            CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_id);
            CREATE INDEX IF NOT EXISTS idx_conv_participants ON conversation_participants(user_id);
            CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(identifier);

            CREATE TABLE IF NOT EXISTS message_reactions (
                id SERIAL PRIMARY KEY,
                msg_id VARCHAR(64) NOT NULL REFERENCES messages(msg_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                emoji VARCHAR(16) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(msg_id, user_id, emoji)
            );
            CREATE INDEX IF NOT EXISTS idx_msg_reactions_msg ON message_reactions(msg_id);

            CREATE TABLE IF NOT EXISTS message_translations (
                id SERIAL PRIMARY KEY,
                msg_id VARCHAR(64) NOT NULL REFERENCES messages(msg_id) ON DELETE CASCADE,
                target_lang VARCHAR(8) NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                detected_lang VARCHAR(8) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(msg_id, target_lang)
            );
            CREATE INDEX IF NOT EXISTS idx_msg_trans_msg ON message_translations(msg_id);

            -- ============== FEED / POSTS ==============
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                post_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                text TEXT DEFAULT '',
                media JSONB DEFAULT '[]'::jsonb,
                type VARCHAR(20) DEFAULT 'post',
                visibility VARCHAR(20) DEFAULT 'public',
                likes_count INTEGER DEFAULT 0,
                comments_count INTEGER DEFAULT 0,
                shares_count INTEGER DEFAULT 0,
                tips_count INTEGER DEFAULT 0,
                tips_total DECIMAL(15,2) DEFAULT 0,
                is_pinned BOOLEAN DEFAULT FALSE,
                legacy_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_posts_type ON posts(type);

            CREATE TABLE IF NOT EXISTS post_likes (
                id SERIAL PRIMARY KEY,
                post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(post_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_post_likes_post ON post_likes(post_id);

            CREATE TABLE IF NOT EXISTS post_comments (
                id SERIAL PRIMARY KEY,
                comment_id VARCHAR(64) UNIQUE NOT NULL,
                post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_post_comments_post ON post_comments(post_id);

            -- ============== REELS / STORIES / TIPS ==============
            CREATE TABLE IF NOT EXISTS reels (
                id SERIAL PRIMARY KEY,
                reel_id VARCHAR(64) UNIQUE NOT NULL,
                post_id VARCHAR(64) REFERENCES posts(post_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                video_url VARCHAR(1000) NOT NULL,
                thumbnail_url VARCHAR(1000) DEFAULT '',
                caption TEXT DEFAULT '',
                duration INTEGER DEFAULT 0,
                music_title VARCHAR(255) DEFAULT '',
                views_count INTEGER DEFAULT 0,
                likes_count INTEGER DEFAULT 0,
                comments_count INTEGER DEFAULT 0,
                tips_total DECIMAL(15,2) DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_reels_user ON reels(user_id);
            CREATE INDEX IF NOT EXISTS idx_reels_created ON reels(created_at DESC);

            CREATE TABLE IF NOT EXISTS stories (
                id SERIAL PRIMARY KEY,
                story_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                image_url VARCHAR(1000) DEFAULT '',
                text TEXT DEFAULT '',
                background_color VARCHAR(32) DEFAULT '#0F056B',
                views_count INTEGER DEFAULT 0,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_stories_user ON stories(user_id);
            CREATE INDEX IF NOT EXISTS idx_stories_expires ON stories(expires_at);

            CREATE TABLE IF NOT EXISTS tips (
                id SERIAL PRIMARY KEY,
                tip_id VARCHAR(64) UNIQUE NOT NULL,
                sender_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                recipient_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                target_type VARCHAR(20) NOT NULL,
                target_id VARCHAR(64) NOT NULL,
                amount DECIMAL(15,2) NOT NULL,
                message VARCHAR(500) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_tips_recipient ON tips(recipient_id);
            CREATE INDEX IF NOT EXISTS idx_tips_target ON tips(target_type, target_id);

            -- ============== CALLS ==============
            CREATE TABLE IF NOT EXISTS calls (
                id SERIAL PRIMARY KEY,
                call_id VARCHAR(64) UNIQUE NOT NULL,
                caller_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                callee_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
                type VARCHAR(20) NOT NULL DEFAULT 'audio',
                status VARCHAR(20) NOT NULL DEFAULT 'initiated',
                duration INTEGER DEFAULT 0,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id);
            CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_id);

            -- ============== EMAIL OTP ==============
            CREATE TABLE IF NOT EXISTS email_otps (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                code VARCHAR(16) NOT NULL,
                purpose VARCHAR(32) NOT NULL DEFAULT 'register',
                attempts INTEGER DEFAULT 0,
                used BOOLEAN DEFAULT FALSE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_email_otps_email ON email_otps(email);
            CREATE INDEX IF NOT EXISTS idx_email_otps_expires ON email_otps(expires_at);

            -- ============== CURRENCY RATES ==============
            CREATE TABLE IF NOT EXISTS currency_rates (
                id SERIAL PRIMARY KEY,
                code VARCHAR(8) UNIQUE NOT NULL,
                rate_vs_usd DECIMAL(20,8) NOT NULL,
                source VARCHAR(64) DEFAULT 'fallback',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_currency_rates_code ON currency_rates(code);

            -- ============== KYC VERIFICATIONS ==============
            CREATE TABLE IF NOT EXISTS kyc_verifications (
                id SERIAL PRIMARY KEY,
                kyc_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                full_name VARCHAR(255) NOT NULL,
                id_type VARCHAR(32) NOT NULL,
                id_number VARCHAR(128) NOT NULL,
                id_photo_url VARCHAR(1000) NOT NULL,
                selfie_url VARCHAR(1000) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                rejection_reason TEXT,
                reviewed_by VARCHAR(64),
                reviewed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_kyc_user ON kyc_verifications(user_id);
            CREATE INDEX IF NOT EXISTS idx_kyc_status ON kyc_verifications(status);

            -- ============== GAMES ==============
            CREATE TABLE IF NOT EXISTS game_plays (
                id SERIAL PRIMARY KEY,
                play_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                game_type VARCHAR(32) NOT NULL,
                score INTEGER DEFAULT 0,
                reward DECIMAL(15,2) DEFAULT 0,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_game_plays_user ON game_plays(user_id);
            CREATE INDEX IF NOT EXISTS idx_game_plays_type ON game_plays(game_type);
            CREATE INDEX IF NOT EXISTS idx_game_plays_created ON game_plays(created_at);

            -- ============== CROWDFUNDING ==============
            CREATE TABLE IF NOT EXISTS campaigns (
                id SERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) UNIQUE NOT NULL,
                creator_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT DEFAULT '',
                category VARCHAR(64) DEFAULT 'other',
                goal DECIMAL(15,2) NOT NULL DEFAULT 0,
                raised DECIMAL(15,2) NOT NULL DEFAULT 0,
                image_url VARCHAR(1000) DEFAULT '',
                status VARCHAR(20) DEFAULT 'active',
                ends_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS campaign_contributions (
                id SERIAL PRIMARY KEY,
                contribution_id VARCHAR(64) UNIQUE NOT NULL,
                campaign_id VARCHAR(64) NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                amount DECIMAL(15,2) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- ============== JOBS ==============
            CREATE TABLE IF NOT EXISTS jobs (
                id SERIAL PRIMARY KEY,
                job_id VARCHAR(64) UNIQUE NOT NULL,
                poster_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT DEFAULT '',
                status VARCHAR(20) DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS job_applications (
                id SERIAL PRIMARY KEY,
                application_id VARCHAR(64) UNIQUE NOT NULL,
                job_id VARCHAR(64) NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                applicant_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- ============== TRANSPORT ==============
            CREATE TABLE IF NOT EXISTS drivers (
                id SERIAL PRIMARY KEY,
                driver_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                vehicle_type VARCHAR(50) DEFAULT 'car',
                is_online BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS ride_requests (
                id SERIAL PRIMARY KEY,
                ride_id VARCHAR(64) UNIQUE NOT NULL,
                rider_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                driver_id VARCHAR(64) REFERENCES drivers(driver_id),
                status VARCHAR(20) DEFAULT 'pending',
                fare_estimate DECIMAL(15,2) DEFAULT 0,
                fare_final DECIMAL(15,2) DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- ============== MARKETPLACE ==============
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                product_id VARCHAR(64) UNIQUE NOT NULL,
                seller_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                title VARCHAR(200) NOT NULL,
                description TEXT DEFAULT '',
                price DECIMAL(15,2) NOT NULL DEFAULT 0,
                currency VARCHAR(10) DEFAULT 'USD',
                category VARCHAR(64) DEFAULT 'general',
                images JSONB DEFAULT '[]'::jsonb,
                condition VARCHAR(20) DEFAULT 'new',
                location VARCHAR(120) DEFAULT '',
                country_code VARCHAR(2) DEFAULT '',
                variants JSONB DEFAULT '[]'::jsonb,     -- [{name:'size', options:['S','M','L']}, ...]
                stock INTEGER DEFAULT -1,               -- -1 = unlimited
                is_boosted BOOLEAN DEFAULT FALSE,
                boost_expires_at TIMESTAMPTZ,
                views_count INTEGER DEFAULT 0,
                favourites_count INTEGER DEFAULT 0,
                rating_avg DECIMAL(3,2) DEFAULT 0,
                rating_count INTEGER DEFAULT 0,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_products_seller ON products(seller_id);
            CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category);
            CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
            CREATE INDEX IF NOT EXISTS idx_products_country ON products(country_code);
            CREATE INDEX IF NOT EXISTS idx_products_boost ON products(is_boosted);

            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                order_id VARCHAR(64) UNIQUE NOT NULL,
                product_id VARCHAR(64) NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                buyer_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                seller_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                amount DECIMAL(15,2) NOT NULL DEFAULT 0,
                currency VARCHAR(10) DEFAULT 'USD',
                fee DECIMAL(15,2) NOT NULL DEFAULT 0,
                coupon_code VARCHAR(40) DEFAULT '',
                discount DECIMAL(15,2) DEFAULT 0,
                notes TEXT DEFAULT '',
                status VARCHAR(20) DEFAULT 'pending',     -- 'pending' | 'completed' | 'cancelled'
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id);
            CREATE INDEX IF NOT EXISTS idx_orders_seller ON orders(seller_id);
            CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);

            CREATE TABLE IF NOT EXISTS product_reviews (
                id SERIAL PRIMARY KEY,
                review_id VARCHAR(64) UNIQUE NOT NULL,
                product_id VARCHAR(64) NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                author_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                rating INTEGER NOT NULL,      -- 1-5
                comment TEXT DEFAULT '',
                order_id VARCHAR(64),          -- must have bought it
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(product_id, author_id)
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_product ON product_reviews(product_id);

            CREATE TABLE IF NOT EXISTS product_favourites (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                product_id VARCHAR(64) NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(user_id, product_id)
            );

            -- iter175 — Marketplace 24h view counter (anti-spam: 1 view / user OR ip / 30min)
            CREATE TABLE IF NOT EXISTS product_views (
                id SERIAL PRIMARY KEY,
                product_id VARCHAR(64) NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                viewer_id VARCHAR(64),               -- nullable for anonymous IPs
                ip_hash VARCHAR(64) NOT NULL DEFAULT '',
                viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_product_views_pid_time ON product_views(product_id, viewed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_product_views_dedup ON product_views(product_id, viewer_id, ip_hash, viewed_at DESC);

            -- iter175 — Marketplace Sponsored Boosts (paid via Wallet USD JAPAP)
            CREATE TABLE IF NOT EXISTS product_boosts (
                id SERIAL PRIMARY KEY,
                boost_id VARCHAR(64) UNIQUE NOT NULL,
                product_id VARCHAR(64) NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                seller_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                plan VARCHAR(20) NOT NULL,           -- 'basic_24h' | 'standard_7d' | 'homepage_30d'
                price_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
                tx_id VARCHAR(64),                   -- canonical transactions.tx_id
                starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                is_homepage BOOLEAN NOT NULL DEFAULT FALSE,
                status VARCHAR(20) NOT NULL DEFAULT 'active',  -- 'active' | 'expired' | 'refunded'
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_product_boosts_active ON product_boosts(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_product_boosts_pid ON product_boosts(product_id);

            -- iter176 — Marketplace Escrow ledger (auditable, rollback-safe)
            -- Each row = one financial movement tied to an order_id.
            -- entry_type:
            --   'hold'           : buyer paid, funds in escrow (PENDING)
            --   'release_seller' : escrow → seller wallet (- commission)
            --   'commission'     : escrow → japap_treasury (commission cut)
            --   'refund_buyer'   : escrow → buyer wallet (full)
            --   'split_seller'   : on partial admin resolution
            --   'split_buyer'    : on partial admin resolution
            CREATE TABLE IF NOT EXISTS marketplace_escrow_ledger (
                id SERIAL PRIMARY KEY,
                ledger_id VARCHAR(64) UNIQUE NOT NULL,
                order_id VARCHAR(64) NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
                entry_type VARCHAR(32) NOT NULL,
                from_account VARCHAR(64) NOT NULL,    -- 'buyer:<uid>' | 'escrow' | 'japap_treasury'
                to_account VARCHAR(64) NOT NULL,
                amount_usd DECIMAL(15,2) NOT NULL,
                tx_id VARCHAR(64),                    -- canonical transactions.tx_id when applicable
                notes TEXT DEFAULT '',
                created_by VARCHAR(64),               -- user_id of the actor (admin for resolutions)
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_escrow_ledger_order ON marketplace_escrow_ledger(order_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_escrow_ledger_type ON marketplace_escrow_ledger(entry_type, created_at DESC);

            -- iter180 — Ads Campaigns (régie publicitaire interne, Wallet USD only)
            CREATE TABLE IF NOT EXISTS ads_campaigns (
                id SERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                product_id VARCHAR(64) REFERENCES products(product_id) ON DELETE SET NULL,
                name VARCHAR(120) NOT NULL DEFAULT 'Campagne',
                budget_usd DECIMAL(15,2) NOT NULL,
                daily_budget_usd DECIMAL(15,2),
                spent_usd DECIMAL(15,4) NOT NULL DEFAULT 0,
                cpm_rate DECIMAL(10,4),
                cpc_rate DECIMAL(10,4),
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                start_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                end_date TIMESTAMPTZ NOT NULL,
                is_global BOOLEAN NOT NULL DEFAULT TRUE,
                target_countries TEXT[],
                age_min SMALLINT,
                age_max SMALLINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ads_camp_status ON ads_campaigns(status, end_date);
            CREATE INDEX IF NOT EXISTS idx_ads_camp_user ON ads_campaigns(user_id);

            CREATE TABLE IF NOT EXISTS ads_impressions (
                id BIGSERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) NOT NULL REFERENCES ads_campaigns(campaign_id) ON DELETE CASCADE,
                user_id VARCHAR(64),
                ip_hash VARCHAR(64) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ads_imp_camp_time ON ads_impressions(campaign_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS ads_clicks (
                id BIGSERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) NOT NULL REFERENCES ads_campaigns(campaign_id) ON DELETE CASCADE,
                user_id VARCHAR(64),
                ip_hash VARCHAR(64) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ads_clk_camp_time ON ads_clicks(campaign_id, created_at DESC);

            -- iter180 — Ensure spent_usd precision (4 decimals for CPM accuracy)
            DO $ads_iter180$ BEGIN
                ALTER TABLE ads_campaigns ALTER COLUMN spent_usd TYPE DECIMAL(15,4);
            EXCEPTION WHEN OTHERS THEN NULL; END $ads_iter180$;

            -- iter181 — Marketplace AI Image Usage (daily quota tracking)
            CREATE TABLE IF NOT EXISTS marketplace_ai_image_usage (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                day_key VARCHAR(10) NOT NULL,          -- 'YYYY-MM-DD' UTC
                kind VARCHAR(24) NOT NULL,             -- 'generate'|'enhance'|'bg_swap'
                count INTEGER NOT NULL DEFAULT 1,
                last_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(user_id, day_key, kind)
            );
            CREATE INDEX IF NOT EXISTS idx_mkt_ai_usage_user_day
              ON marketplace_ai_image_usage(user_id, day_key);

            -- iter185 — Viral share loop (UTM-based attribution + reward)
            CREATE TABLE IF NOT EXISTS viral_share_events (
                id SERIAL PRIMARY KEY,
                sharer_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                entity_type VARCHAR(24) NOT NULL,    -- 'product' | 'post' | 'user'
                entity_id VARCHAR(64) NOT NULL,
                ip_hash VARCHAR(64),                  -- SHA-256 hex of IP (for anti-fraud)
                ua_hash VARCHAR(64),
                visitor_user_id VARCHAR(64),          -- optional, if logged in
                day_key VARCHAR(10) NOT NULL,         -- YYYY-MM-DD UTC
                rewarded BOOLEAN NOT NULL DEFAULT FALSE,
                points_awarded INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_viral_share_sharer_day
              ON viral_share_events(sharer_id, day_key);
            CREATE INDEX IF NOT EXISTS idx_viral_share_dedup
              ON viral_share_events(sharer_id, ip_hash, entity_type, entity_id, created_at DESC);

            -- iter186 — Milestones already notified (idempotence)
            CREATE TABLE IF NOT EXISTS viral_milestones_reached (
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                threshold INTEGER NOT NULL,
                reached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, threshold)
            );

            -- iter188 — Marketplace buyer intent (intent → conversation → vente loop)
            CREATE TABLE IF NOT EXISTS marketplace_buyer_intents (
                id SERIAL PRIMARY KEY,
                buyer_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                seller_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                product_id VARCHAR(64) NOT NULL,
                conv_id VARCHAR(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_buyer_intents_buyer_prod_time
              ON marketplace_buyer_intents(buyer_id, product_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_buyer_intents_seller_time
              ON marketplace_buyer_intents(seller_id, created_at DESC);

            -- iter189 — Seller reminders idempotence (15min push / 24h email digest)
            CREATE TABLE IF NOT EXISTS buyer_intents_reminders_sent (
                intent_id INTEGER NOT NULL REFERENCES marketplace_buyer_intents(id) ON DELETE CASCADE,
                kind VARCHAR(24) NOT NULL,            -- 'push_15min' | 'email_24h'
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (intent_id, kind)
            );

            CREATE TABLE IF NOT EXISTS product_coupons (
                id SERIAL PRIMARY KEY,
                coupon_id VARCHAR(64) UNIQUE NOT NULL,
                seller_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                code VARCHAR(40) NOT NULL,
                discount_pct INTEGER NOT NULL DEFAULT 0,
                discount_flat_usd DECIMAL(15,4) DEFAULT 0,
                scope VARCHAR(20) DEFAULT 'all',         -- 'all' | 'product' | 'category'
                scope_value VARCHAR(120) DEFAULT '',
                max_uses INTEGER DEFAULT 0,              -- 0 = unlimited
                uses_count INTEGER DEFAULT 0,
                valid_until TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(seller_id, code)
            );

            -- ============== JAPAP ADS (sponsored posts/reels/products) ==============
            CREATE TABLE IF NOT EXISTS ad_campaigns (
                id SERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) UNIQUE NOT NULL,
                owner_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                target_type VARCHAR(20) NOT NULL,        -- 'post' | 'reel' | 'product' | 'banner'
                target_id VARCHAR(64),                   -- post_id / reel_id / product_id (nullable for banner)
                title VARCHAR(200) DEFAULT '',
                image_url VARCHAR(500) DEFAULT '',
                cta_url VARCHAR(500) DEFAULT '',
                budget_usd DECIMAL(15,4) NOT NULL DEFAULT 0,
                spent_usd DECIMAL(15,4) DEFAULT 0,
                cpm_usd DECIMAL(15,4) DEFAULT 1.0,       -- cost per 1000 impressions
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                status VARCHAR(20) DEFAULT 'pending',    -- 'pending' | 'approved' | 'running' | 'paused' | 'ended' | 'rejected'
                country_code VARCHAR(2) DEFAULT '',
                start_at TIMESTAMPTZ,
                end_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ads_status ON ad_campaigns(status);
            CREATE INDEX IF NOT EXISTS idx_ads_owner ON ad_campaigns(owner_id);

            CREATE TABLE IF NOT EXISTS ad_events (
                id SERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) NOT NULL REFERENCES ad_campaigns(campaign_id) ON DELETE CASCADE,
                user_id VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                event_type VARCHAR(16) NOT NULL,          -- 'impression' | 'click'
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ad_events_campaign ON ad_events(campaign_id);

            -- ============== REFERRALS ==============
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                referred_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                status VARCHAR(20) DEFAULT 'pending',
                reward_amount DECIMAL(15,2) DEFAULT 0,
                activated_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(referrer_id, referred_id)
            );

            -- ============== REFERRAL REWARDS LOG ==============
            CREATE TABLE IF NOT EXISTS referral_rewards_log (
                id SERIAL PRIMARY KEY,
                referral_id INTEGER REFERENCES referrals(id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                role VARCHAR(16) NOT NULL,            -- 'referrer' | 'referee' | 'tier'
                reward_type VARCHAR(20) NOT NULL,     -- 'wallet' | 'pro'
                amount_usd DECIMAL(15,4) DEFAULT 0,
                amount_local DECIMAL(15,2) DEFAULT 0,
                currency VARCHAR(10) DEFAULT 'USD',
                details JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ref_log_user ON referral_rewards_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_ref_log_created ON referral_rewards_log(created_at);

            -- ============== JAPAP CONNECT — WiFi Rewards ==============
            CREATE TABLE IF NOT EXISTS wifi_hotspots (
                id SERIAL PRIMARY KEY,
                hotspot_id VARCHAR(64) UNIQUE NOT NULL,
                owner_id VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                alias VARCHAR(120) NOT NULL,
                description VARCHAR(500) DEFAULT '',
                type VARCHAR(20) NOT NULL DEFAULT 'user',
                latitude DOUBLE PRECISION NOT NULL,
                longitude DOUBLE PRECISION NOT NULL,
                address VARCHAR(255) DEFAULT '',
                sponsor_name VARCHAR(120) DEFAULT '',
                is_sponsored BOOLEAN DEFAULT FALSE,
                max_daily_users INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                is_blocked BOOLEAN DEFAULT FALSE,
                blocked_reason VARCHAR(255) DEFAULT '',
                total_connections INTEGER DEFAULT 0,
                total_unique_users INTEGER DEFAULT 0,
                total_rewarded_usd DECIMAL(15,4) DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_hotspots_owner ON wifi_hotspots(owner_id);
            CREATE INDEX IF NOT EXISTS idx_hotspots_active ON wifi_hotspots(is_active, is_blocked);

            CREATE TABLE IF NOT EXISTS wifi_connections (
                id SERIAL PRIMARY KEY,
                connection_id VARCHAR(64) UNIQUE NOT NULL,
                hotspot_id VARCHAR(64) NOT NULL REFERENCES wifi_hotspots(hotspot_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                ip_address VARCHAR(64),
                device_id VARCHAR(128),
                duration_seconds INTEGER DEFAULT 0,
                reward_usd DECIMAL(15,4) DEFAULT 0,
                reward_local DECIMAL(15,2) DEFAULT 0,
                reward_currency VARCHAR(10) DEFAULT 'USD',
                status VARCHAR(20) DEFAULT 'active',
                blocked BOOLEAN DEFAULT FALSE,
                blocked_reason VARCHAR(255) DEFAULT '',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_wifi_conn_hotspot ON wifi_connections(hotspot_id);
            CREATE INDEX IF NOT EXISTS idx_wifi_conn_user ON wifi_connections(user_id);
            CREATE INDEX IF NOT EXISTS idx_wifi_conn_created ON wifi_connections(started_at);
        ''')

        # Idempotent migrations for legacy deployments
        for stmt in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(32)",
            # iter175 — Marketplace homepage featured + last boost plan
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_homepage_featured BOOLEAN DEFAULT FALSE",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS homepage_expires_at TIMESTAMPTZ",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS last_boost_plan VARCHAR(20)",
            # iter179 — Audience targeting on boost (zero-hardcode, per-boost row)
            "ALTER TABLE product_boosts ADD COLUMN IF NOT EXISTS target_countries TEXT[]",
            "ALTER TABLE product_boosts ADD COLUMN IF NOT EXISTS is_global BOOLEAN DEFAULT TRUE",
            "ALTER TABLE product_boosts ADD COLUMN IF NOT EXISTS age_min SMALLINT",
            "ALTER TABLE product_boosts ADD COLUMN IF NOT EXISTS age_max SMALLINT",
            # iter176 — Marketplace Escrow lifecycle columns
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS escrow_status VARCHAR(20) DEFAULT 'pending'",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS auto_release_at TIMESTAMPTZ",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS dispute_reason TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS dispute_opened_at TIMESTAMPTZ",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS dispute_resolved_at TIMESTAMPTZ",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS commission_pct NUMERIC(5,2) DEFAULT 2",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS released_at TIMESTAMPTZ",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by VARCHAR(64)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_lang VARCHAR(8)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_currency VARCHAR(3)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS country_code VARCHAR(2)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_trial BOOLEAN DEFAULT FALSE",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'wallet'",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN DEFAULT FALSE",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS original_amount_usd DECIMAL(10,2)",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS paid_amount_usd DECIMAL(10,2)",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS discount_pct INTEGER DEFAULT 0",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS duration_days INTEGER DEFAULT 30",
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE",
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS zone VARCHAR(80) DEFAULT ''",
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS country_code VARCHAR(2) DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS connect_points INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_connect_owner_id VARCHAR(64)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_connect_at TIMESTAMPTZ",
            "CREATE INDEX IF NOT EXISTS idx_hotspots_zone ON wifi_hotspots(zone)",
            "CREATE INDEX IF NOT EXISTS idx_hotspots_country ON wifi_hotspots(country_code)",
            "CREATE INDEX IF NOT EXISTS idx_hotspots_premium ON wifi_hotspots(is_premium)",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64)",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS device_id VARCHAR(128)",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_given BOOLEAN DEFAULT FALSE",
            # iter141nineF — Pay-as-you-Tip presets per user (Pro creators
            # can configure suggested tip amounts shown as quick-tap chips
            # on their posts/reels). Defaults: enabled with 100/500/1000 XAF.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS tip_enabled BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS tip_presets JSONB NOT NULL DEFAULT '[100, 500, 1000]'::jsonb",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS tip_message TEXT NOT NULL DEFAULT ''",

            # iter142 — Crowdfunding viral (votes-based) schema
            """CREATE TABLE IF NOT EXISTS crowdfunding_cycles (
                id SERIAL PRIMARY KEY,
                cycle_id VARCHAR(40) UNIQUE NOT NULL,
                cycle_number INT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                threshold_projects INT NOT NULL DEFAULT 50,
                votes_to_win INT NOT NULL DEFAULT 100,
                reward_amount NUMERIC(15,2) NOT NULL DEFAULT 50000,
                reward_currency VARCHAR(10) NOT NULL DEFAULT 'XAF',
                votes_open BOOLEAN NOT NULL DEFAULT FALSE,
                votes_opened_at TIMESTAMPTZ,
                winner_project_id VARCHAR(40),
                winner_user_id VARCHAR(64),
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at TIMESTAMPTZ,
                created_by_admin VARCHAR(64),
                notes TEXT DEFAULT ''
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_cycles_status ON crowdfunding_cycles(status, started_at DESC)",
            """CREATE TABLE IF NOT EXISTS crowdfunding_projects (
                id SERIAL PRIMARY KEY,
                project_id VARCHAR(40) UNIQUE NOT NULL,
                slug VARCHAR(120) UNIQUE NOT NULL,
                cycle_id VARCHAR(40) NOT NULL REFERENCES crowdfunding_cycles(cycle_id),
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                title VARCHAR(160) NOT NULL,
                description TEXT NOT NULL,
                objective TEXT DEFAULT '',
                category VARCHAR(40) NOT NULL DEFAULT 'community',
                image_url VARCHAR(500) DEFAULT '',
                country_code VARCHAR(4) DEFAULT '',
                duration_days INT NOT NULL DEFAULT 30,
                ends_at TIMESTAMPTZ NOT NULL,
                votes_count INT NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                won_at TIMESTAMPTZ,
                reward_tx_id VARCHAR(64),
                moderation_reason TEXT DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS moderation_reason TEXT DEFAULT ''",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            """CREATE UNIQUE INDEX IF NOT EXISTS uniq_cf_user_active_per_cycle
                 ON crowdfunding_projects (user_id, cycle_id)
                 WHERE status IN ('active','winner')""",
            "CREATE INDEX IF NOT EXISTS idx_cf_projects_cycle ON crowdfunding_projects(cycle_id, votes_count DESC)",
            "CREATE INDEX IF NOT EXISTS idx_cf_projects_country ON crowdfunding_projects(cycle_id, country_code, votes_count DESC)",
            """CREATE TABLE IF NOT EXISTS crowdfunding_votes (
                id SERIAL PRIMARY KEY,
                project_id VARCHAR(40) NOT NULL REFERENCES crowdfunding_projects(project_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                cycle_id VARCHAR(40) NOT NULL,
                ip_hash VARCHAR(64) DEFAULT '',
                user_agent_hash VARCHAR(64) DEFAULT '',
                country_code VARCHAR(4) DEFAULT '',
                voted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, project_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_votes_user ON crowdfunding_votes(user_id, voted_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_cf_votes_ip ON crowdfunding_votes(ip_hash, voted_at DESC) WHERE ip_hash <> ''",
            # iter142D — P3 engagement IA engine
            """CREATE TABLE IF NOT EXISTS crowdfunding_behavior_events (
                event_id BIGSERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                project_id VARCHAR(40),
                cycle_id VARCHAR(40),
                event_type VARCHAR(32) NOT NULL,
                rank_before INT,
                rank_after INT,
                time_spent INT DEFAULT 0,
                source VARCHAR(32) DEFAULT 'direct',
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_behavior_user_ts ON crowdfunding_behavior_events(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_cf_behavior_project_ts ON crowdfunding_behavior_events(project_id, created_at DESC) WHERE project_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_cf_behavior_event_ts ON crowdfunding_behavior_events(event_type, created_at DESC)",
            """CREATE TABLE IF NOT EXISTS crowdfunding_message_performance (
                message_id VARCHAR(64) PRIMARY KEY,
                state VARCHAR(16) NOT NULL,
                variant_text TEXT NOT NULL,
                shown_count INT DEFAULT 0,
                clicked_count INT DEFAULT 0,
                dismissed_count INT DEFAULT 0,
                shared_count INT DEFAULT 0,
                last_shown_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_msg_perf_state ON crowdfunding_message_performance(state, shown_count DESC)",
            """CREATE TABLE IF NOT EXISTS crowdfunding_engagement_state (
                user_id VARCHAR(64) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                state VARCHAR(16) NOT NULL DEFAULT 'cold',
                ui_mode VARCHAR(16) NOT NULL DEFAULT 'calm',
                engagement_score INT DEFAULT 0,
                last_message_id VARCHAR(64),
                last_message_at TIMESTAMPTZ,
                cooldown_until TIMESTAMPTZ,
                computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            # iter169 — Crowdfunding viral loop (P1) — recruiter attribution
            """CREATE TABLE IF NOT EXISTS crowdfunding_invite_visits (
                id BIGSERIAL PRIMARY KEY,
                cycle_id VARCHAR(40) NOT NULL,
                inviter_id VARCHAR(64) NOT NULL,
                project_slug VARCHAR(120) NOT NULL,
                visitor_user_id VARCHAR(64),
                ip_hash VARCHAR(64) NOT NULL,
                user_agent_hash VARCHAR(64) NOT NULL DEFAULT '',
                utm_source VARCHAR(40),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_invite_visits_cycle_inviter ON crowdfunding_invite_visits(cycle_id, inviter_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_cf_invite_visits_visitor ON crowdfunding_invite_visits(cycle_id, visitor_user_id) WHERE visitor_user_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_cf_invite_visits_ip ON crowdfunding_invite_visits(cycle_id, ip_hash, created_at DESC)",
            """CREATE TABLE IF NOT EXISTS crowdfunding_recruit_credits (
                id BIGSERIAL PRIMARY KEY,
                cycle_id VARCHAR(40) NOT NULL,
                inviter_id VARCHAR(64) NOT NULL,
                recruit_user_id VARCHAR(64) NOT NULL,
                vote_id INT,
                project_id VARCHAR(40) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (cycle_id, inviter_id, recruit_user_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_recruit_credits_cycle_inviter ON crowdfunding_recruit_credits(cycle_id, inviter_id, created_at DESC)",
            """CREATE TABLE IF NOT EXISTS crowdfunding_recruiter_badges (
                id BIGSERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                cycle_id VARCHAR(40) NOT NULL,
                tier VARCHAR(16) NOT NULL,
                recruits_count INT NOT NULL,
                awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, cycle_id, tier)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_recruiter_badges_user ON crowdfunding_recruiter_badges(user_id, awarded_at DESC)",
            # ─────────────────────────────────────────────────────────
            # iter239w — Crowdfunding refonte logique de victoire + admin
            # ─────────────────────────────────────────────────────────
            # Cycles : durée + alias minimum_votes_required (généré, lit
            # automatiquement votes_to_win pour zéro régression).
            # ─────────────────────────────────────────────────────────
            # iter239x — Membres du Jury (anciens gagnants)
            # ─────────────────────────────────────────────────────────
            # Vote weight sur chaque vote (default 1, > 1 pour les jurés)
            "ALTER TABLE crowdfunding_votes ADD COLUMN IF NOT EXISTS vote_weight INT NOT NULL DEFAULT 1",
            # Statut juré persisté (1 row par grant ; un user peut avoir N grants au fil des cycles)
            """CREATE TABLE IF NOT EXISTS crowdfunding_jury_members (
                jury_id            VARCHAR(40) PRIMARY KEY,
                user_id            VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                awarded_cycle_id   VARCHAR(40),
                awarded_cycle_number INT,
                total_wins_at_grant INT NOT NULL DEFAULT 1,
                granted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at_cycle_number INT,                   -- NULL = permanent (limited durée admin)
                revoked_at         TIMESTAMPTZ,
                revoked_by         VARCHAR(64),
                revoke_reason      TEXT,
                certificate_url    VARCHAR(500)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cf_jury_user ON crowdfunding_jury_members(user_id, revoked_at)",
            "CREATE INDEX IF NOT EXISTS idx_cf_jury_active ON crowdfunding_jury_members(user_id) WHERE revoked_at IS NULL",
            # ─────────────────────────────────────────────────────────
            # iter239x — Fin migrations
            # ─────────────────────────────────────────────────────────
            "ALTER TABLE crowdfunding_cycles ADD COLUMN IF NOT EXISTS duration_days INT DEFAULT 30",
            """DO $cf239w_cycles$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_name='crowdfunding_cycles'
                       AND column_name='minimum_votes_required'
                ) THEN
                    ALTER TABLE crowdfunding_cycles
                      ADD COLUMN minimum_votes_required INT
                      GENERATED ALWAYS AS (votes_to_win) STORED;
                END IF;
            END $cf239w_cycles$""",
            # Projets : traçabilité acceptation conditions + suspension admin
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS terms_version VARCHAR(10) DEFAULT 'v1'",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS suspended_by VARCHAR(64)",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS suspension_reason TEXT",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
            "ALTER TABLE crowdfunding_projects ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(64)",
            # Index pour le worker de clôture automatique
            "CREATE INDEX IF NOT EXISTS idx_cf_cycles_active_ends ON crowdfunding_cycles(status, ended_at) WHERE status='active'",
            # iter239w — pour les listes admin filtrées par statut
            "CREATE INDEX IF NOT EXISTS idx_cf_projects_status_cycle ON crowdfunding_projects(cycle_id, status, votes_count DESC)",

            # Iter 46 — Messenger Sprint A group support
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''",
            "ALTER TABLE conversation_participants ADD COLUMN IF NOT EXISTS role VARCHAR(16) DEFAULT 'member'",
            # Iter 47 — Forward traceability (click 'Transféré' → jump to original)
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS forwarded_from_msg_id VARCHAR(64)",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS forwarded_from_conv_id VARCHAR(64)",
            # Iter 48 — Forward chain transparency (A → B → C depth counter)
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS forward_depth INTEGER DEFAULT 0",
            # Iter 50 — Sprint B/C/D scaffolding (LiveKit calls, recordings, AI summaries)
            """CREATE TABLE IF NOT EXISTS call_sessions (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(64) UNIQUE NOT NULL,
                call_id VARCHAR(64),                   -- legacy calls.call_id link (1-1)
                conv_id VARCHAR(64),                   -- for group calls
                room_name VARCHAR(128) NOT NULL,       -- LiveKit room identifier
                mode VARCHAR(16) NOT NULL DEFAULT 'audio',  -- audio | video
                kind VARCHAR(16) NOT NULL DEFAULT 'p2p',    -- p2p | group
                host_user_id VARCHAR(64) NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'ringing',  -- ringing|live|ended|failed
                started_at TIMESTAMPTZ DEFAULT now(),
                ended_at TIMESTAMPTZ,
                duration_sec INTEGER DEFAULT 0,
                max_participants INTEGER DEFAULT 12,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_call_sessions_conv ON call_sessions(conv_id)",
            "CREATE INDEX IF NOT EXISTS idx_call_sessions_host ON call_sessions(host_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_call_sessions_status ON call_sessions(status)",
            """CREATE TABLE IF NOT EXISTS call_participants (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(64) REFERENCES call_sessions(session_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL,
                joined_at TIMESTAMPTZ DEFAULT now(),
                left_at TIMESTAMPTZ,
                role VARCHAR(16) DEFAULT 'member',      -- host | member
                UNIQUE(session_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS call_recordings (
                id SERIAL PRIMARY KEY,
                recording_id VARCHAR(64) UNIQUE NOT NULL,
                session_id VARCHAR(64) REFERENCES call_sessions(session_id) ON DELETE CASCADE,
                egress_id VARCHAR(128),                 -- LiveKit egress id
                status VARCHAR(24) DEFAULT 'starting',  -- starting|active|finalizing|ready|failed
                storage_provider VARCHAR(32) DEFAULT 'r2',  -- r2 | s3
                storage_bucket VARCHAR(128),
                storage_key VARCHAR(512),
                public_url VARCHAR(512),
                file_size_bytes BIGINT,
                duration_sec INTEGER DEFAULT 0,
                mime_type VARCHAR(64) DEFAULT 'audio/mp4',
                started_at TIMESTAMPTZ DEFAULT now(),
                finalized_at TIMESTAMPTZ,
                error_msg TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_call_recordings_session ON call_recordings(session_id)",
            """CREATE TABLE IF NOT EXISTS call_summaries (
                id SERIAL PRIMARY KEY,
                summary_id VARCHAR(64) UNIQUE NOT NULL,
                session_id VARCHAR(64) REFERENCES call_sessions(session_id) ON DELETE CASCADE,
                recording_id VARCHAR(64) REFERENCES call_recordings(recording_id) ON DELETE SET NULL,
                transcript TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                key_points JSONB DEFAULT '[]'::jsonb,
                decisions JSONB DEFAULT '[]'::jsonb,
                action_items JSONB DEFAULT '[]'::jsonb,
                language VARCHAR(8) DEFAULT 'fr',
                model VARCHAR(64) DEFAULT '',
                status VARCHAR(24) DEFAULT 'pending',   -- pending|transcribing|summarizing|ready|failed
                created_at TIMESTAMPTZ DEFAULT now(),
                completed_at TIMESTAMPTZ,
                error_msg TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_call_summaries_session ON call_summaries(session_id)",
            # Iter 58 — Connect v2 (Hybrid Gate Model: QR dynamic + Fernet-encrypted WiFi password)
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS ssid VARCHAR(64) DEFAULT ''",
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS wifi_password_encrypted TEXT",
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS security_type VARCHAR(16) DEFAULT 'WPA2'",
            "ALTER TABLE wifi_hotspots ADD COLUMN IF NOT EXISTS wifi_updated_at TIMESTAMPTZ",
            """CREATE TABLE IF NOT EXISTS wifi_access_tokens (
                id SERIAL PRIMARY KEY,
                token_id VARCHAR(64) UNIQUE NOT NULL,
                hotspot_id VARCHAR(64) NOT NULL REFERENCES wifi_hotspots(hotspot_id) ON DELETE CASCADE,
                nonce VARCHAR(64) UNIQUE NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_by_user_id VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                consumed_at TIMESTAMPTZ,
                created_by VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_wifi_tokens_nonce ON wifi_access_tokens(nonce)",
            "CREATE INDEX IF NOT EXISTS idx_wifi_tokens_expires ON wifi_access_tokens(expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_wifi_tokens_hotspot ON wifi_access_tokens(hotspot_id)",
            "ALTER TABLE wifi_connections ADD COLUMN IF NOT EXISTS access_token_id VARCHAR(64)",
            "ALTER TABLE wifi_connections ADD COLUMN IF NOT EXISTS password_reveals INTEGER DEFAULT 0",
            # Iter 61 — Force-logout on password reset (admin action):
            # every token minted before this timestamp is rejected by get_current_user.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ",
            # Iter 59 — Structured messages (call summary, polls, etc.)
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS message_type VARCHAR(32) DEFAULT 'text'",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS structured_data JSONB DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS call_session_id VARCHAR(64)",
            "CREATE INDEX IF NOT EXISTS idx_messages_call_session ON messages(call_session_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(message_type)",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_type VARCHAR(32)",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS referrer_bonus_usd DECIMAL(15,4) DEFAULT 0",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS referee_bonus_usd DECIMAL(15,4) DEFAULT 0",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS blocked BOOLEAN DEFAULT FALSE",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS blocked_reason VARCHAR(255)",
            "CREATE INDEX IF NOT EXISTS idx_referrals_ip ON referrals(ip_address)",
            "CREATE INDEX IF NOT EXISTS idx_referrals_device ON referrals(device_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL",
            # Transport — KYC chauffeur + contact d'urgence
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS vehicle_model VARCHAR(100) DEFAULT ''",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS vehicle_plate VARCHAR(30) DEFAULT ''",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS rating DECIMAL(3,2) DEFAULT 5.00",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS total_rides INTEGER DEFAULT 0",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR(32) DEFAULT ''",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR(80) DEFAULT ''",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending'",
            "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS suspended_reason VARCHAR(255) DEFAULT ''",
            "ALTER TABLE ride_requests ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ",
            "ALTER TABLE ride_requests ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
            "ALTER TABLE ride_requests ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ",
            # Jobs extension — Offres (mission freelance, petites annonces)
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_type VARCHAR(24) DEFAULT 'job'",  # 'job' | 'mission' | 'annonce'
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS category VARCHAR(64) DEFAULT 'other'",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS location VARCHAR(120) DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_min DECIMAL(15,2) DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_max DECIMAL(15,2) DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'USD'",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS type VARCHAR(32) DEFAULT 'full_time'",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS remote BOOLEAN DEFAULT FALSE",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS budget_usd DECIMAL(15,2) DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS deadline DATE",
            "CREATE INDEX IF NOT EXISTS idx_jobs_offer_type ON jobs(offer_type)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category)",
            # ============== GROUPS & PAGES (iter38) ==============
            """CREATE TABLE IF NOT EXISTS social_groups (
                id SERIAL PRIMARY KEY,
                group_id VARCHAR(64) UNIQUE NOT NULL,
                owner_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                name VARCHAR(120) NOT NULL,
                description TEXT DEFAULT '',
                avatar TEXT DEFAULT '',
                cover TEXT DEFAULT '',
                privacy VARCHAR(20) DEFAULT 'public',
                members_count INTEGER DEFAULT 1,
                posts_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_social_groups_owner ON social_groups(owner_id)",
            """CREATE TABLE IF NOT EXISTS group_members (
                id SERIAL PRIMARY KEY,
                group_id VARCHAR(64) NOT NULL REFERENCES social_groups(group_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                role VARCHAR(20) DEFAULT 'member',
                joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(group_id, user_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)",
            """CREATE TABLE IF NOT EXISTS social_pages (
                id SERIAL PRIMARY KEY,
                page_id VARCHAR(64) UNIQUE NOT NULL,
                owner_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                name VARCHAR(120) NOT NULL,
                category VARCHAR(64) DEFAULT 'other',
                description TEXT DEFAULT '',
                avatar TEXT DEFAULT '',
                cover TEXT DEFAULT '',
                followers_count INTEGER DEFAULT 0,
                posts_count INTEGER DEFAULT 0,
                verified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_social_pages_owner ON social_pages(owner_id)",
            """CREATE TABLE IF NOT EXISTS page_followers (
                id SERIAL PRIMARY KEY,
                page_id VARCHAR(64) NOT NULL REFERENCES social_pages(page_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                followed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(page_id, user_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_page_followers_user ON page_followers(user_id)",
            # Target group/page on posts (share delivery in Phase 3)
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS target_group_id VARCHAR(64) REFERENCES social_groups(group_id) ON DELETE SET NULL",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS target_page_id VARCHAR(64) REFERENCES social_pages(page_id) ON DELETE SET NULL",
            "CREATE INDEX IF NOT EXISTS idx_posts_target_group ON posts(target_group_id)",
            "CREATE INDEX IF NOT EXISTS idx_posts_target_page ON posts(target_page_id)",
            # ============== QUICK EMOJI REACTIONS (iter38 Phase 5) ==============
            """CREATE TABLE IF NOT EXISTS post_reactions (
                id SERIAL PRIMARY KEY,
                post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                emoji VARCHAR(8) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(post_id, user_id, emoji)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_post_reactions_post ON post_reactions(post_id)",
            # Story views tracking — referenced by feed_extended.py (fix iter38)
            """CREATE TABLE IF NOT EXISTS story_views (
                id SERIAL PRIMARY KEY,
                story_id VARCHAR(64) NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(story_id, user_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_story_views_story ON story_views(story_id)",
            "CREATE INDEX IF NOT EXISTS idx_story_views_user ON story_views(user_id)",
            # ============== ONBOARDING + ANALYTICS (iter40) ==============
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ",
            """CREATE TABLE IF NOT EXISTS analytics_events (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                name VARCHAR(80) NOT NULL,
                props JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_analytics_name ON analytics_events(name)",
            "CREATE INDEX IF NOT EXISTS idx_analytics_user ON analytics_events(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at DESC)",
            # ============== ITER 64 — ADMIN MESSAGING CENTER ==============
            # Email subscription + anti-spam tracking on users
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_subscribed BOOLEAN DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_unsubscribed_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_email_sent_at TIMESTAMPTZ",
            # ITER 66 migration — force password reset flow
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_via VARCHAR(32)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS migration_pending BOOLEAN DEFAULT FALSE",
            # iter141ter — explicit legacy/migration flags (replaces fragile
            # `migration_pending` check that conflated new and legacy users).
            # `is_legacy_account` is the canonical "this user came from JAPAP 1.0"
            # boolean; `migration_completed` flips to TRUE once the user has
            # set a fresh password on JAPAP 4.0. New (post-launch) accounts
            # default to `is_legacy_account=FALSE, migration_completed=TRUE`
            # so they NEVER trigger the migration prompt.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_legacy_account BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS migration_completed BOOLEAN DEFAULT TRUE",
            # Backfill from existing data (idempotent).
            "UPDATE users SET is_legacy_account = TRUE WHERE legacy_id IS NOT NULL AND is_legacy_account = FALSE",
            "UPDATE users SET migration_completed = FALSE WHERE migration_pending = TRUE AND migration_completed = TRUE",
            "UPDATE users SET migration_completed = TRUE WHERE migration_pending = FALSE AND legacy_id IS NULL AND migration_completed = FALSE",
            "CREATE INDEX IF NOT EXISTS idx_users_legacy_pending ON users(is_legacy_account) WHERE is_legacy_account = TRUE",
            # ============== ITER 77 — SOCIAL LAYER (cover, follows, counts) ==============
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_image VARCHAR(512)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_position_y SMALLINT DEFAULT 50",
            # iter92 — smart image pipeline (thumbs + mobile cover)
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_thumb VARCHAR(512)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_image_mobile VARCHAR(512)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS followers_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS following_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS posts_count INTEGER DEFAULT 0",
            """CREATE TABLE IF NOT EXISTS user_follows (
                id SERIAL PRIMARY KEY,
                follower_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                followed_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (follower_id, followed_id),
                CHECK (follower_id <> followed_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_user_follows_follower ON user_follows(follower_id)",
            "CREATE INDEX IF NOT EXISTS idx_user_follows_followed ON user_follows(followed_id)",
            # Backfill posts_count once (cheap UPDATE; safe to re-run — no-op if identical).
            # Subsequent changes are kept in sync via the /api/feed/posts create/delete hooks.
            """UPDATE users u SET posts_count = sub.cnt FROM (
                SELECT user_id, COUNT(*) AS cnt FROM posts
                WHERE visibility = 'public' GROUP BY user_id
            ) sub WHERE u.user_id = sub.user_id AND u.posts_count IS DISTINCT FROM sub.cnt""",
            # ============== ITER 78 — PRIVACY LAYER (Facebook-style) ==============
            # Account-level visibility: 'public' means anyone can view the
            # profile + posts; 'private' hides posts from non-followers and
            # forces follow requests to go through approval.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS account_visibility VARCHAR(16) DEFAULT 'public'",
            # Follow mode independent of account_visibility — can be set to
            # 'approval' even on a public account if the user wants control
            # (e.g. a "semi-public" influencer).
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS follow_mode VARCHAR(16) DEFAULT 'auto'",
            # Default visibility applied to new posts when the creator does
            # not specify one explicitly (future per-post-override friendly).
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS post_visibility_default VARCHAR(16) DEFAULT 'public'",
            # Per-event notification opt-ins. All default to TRUE so existing
            # users keep getting the engagement loop; user can turn them off
            # individually from Settings → Notifications.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_follow BOOLEAN DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_follow_accept BOOLEAN DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_likes BOOLEAN DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_comments BOOLEAN DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_messages BOOLEAN DEFAULT TRUE",
            # Follow rows now carry a status: 'accepted' is the same as iter77,
            # 'pending' is used when the target runs follow_mode='approval'.
            # Legacy rows keep the default 'accepted' so iter77 data migrates
            # transparently. We don't count 'pending' rows in followers_count.
            "ALTER TABLE user_follows ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'accepted'",
            "CREATE INDEX IF NOT EXISTS idx_user_follows_pending ON user_follows(followed_id) WHERE status = 'pending'",
            """CREATE TABLE IF NOT EXISTS email_templates (
                id SERIAL PRIMARY KEY,
                template_id VARCHAR(64) UNIQUE NOT NULL,
                name VARCHAR(120) NOT NULL,
                language VARCHAR(8) DEFAULT 'fr',
                subject VARCHAR(200) NOT NULL,
                preview_text VARCHAR(160) DEFAULT '',
                body_html TEXT NOT NULL,
                body_text TEXT DEFAULT '',
                cta_label VARCHAR(80) DEFAULT '',
                cta_url VARCHAR(500) DEFAULT '',
                category VARCHAR(32) DEFAULT 'custom',
                source VARCHAR(16) DEFAULT 'manual',
                ai_prompt JSONB DEFAULT NULL,
                created_by VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_email_templates_category ON email_templates(category)",
            """CREATE TABLE IF NOT EXISTS email_segments (
                id SERIAL PRIMARY KEY,
                segment_id VARCHAR(64) UNIQUE NOT NULL,
                name VARCHAR(120) NOT NULL,
                description TEXT DEFAULT '',
                rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                is_system BOOLEAN DEFAULT FALSE,
                estimated_count INTEGER DEFAULT 0,
                estimated_at TIMESTAMPTZ,
                created_by VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS email_campaigns (
                id SERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) UNIQUE NOT NULL,
                name VARCHAR(120) NOT NULL,
                status VARCHAR(24) DEFAULT 'draft',
                template_id VARCHAR(64) REFERENCES email_templates(template_id) ON DELETE SET NULL,
                subject VARCHAR(200) NOT NULL,
                preview_text VARCHAR(160) DEFAULT '',
                body_html TEXT NOT NULL,
                body_text TEXT DEFAULT '',
                cta_label VARCHAR(80) DEFAULT '',
                cta_url VARCHAR(500) DEFAULT '',
                language VARCHAR(8) DEFAULT 'fr',
                segment_id VARCHAR(64) REFERENCES email_segments(segment_id) ON DELETE SET NULL,
                segment_rules_snapshot JSONB DEFAULT '[]'::jsonb,
                individual_user_ids JSONB DEFAULT NULL,
                scheduled_at TIMESTAMPTZ,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                sent_count INTEGER DEFAULT 0,
                delivered_count INTEGER DEFAULT 0,
                opened_count INTEGER DEFAULT 0,
                clicked_count INTEGER DEFAULT 0,
                bounced_count INTEGER DEFAULT 0,
                unsub_count INTEGER DEFAULT 0,
                created_by VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                confirmed_by VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_email_campaigns_status ON email_campaigns(status)",
            "CREATE INDEX IF NOT EXISTS idx_email_campaigns_created ON email_campaigns(created_at DESC)",
            """CREATE TABLE IF NOT EXISTS email_send_queue (
                id SERIAL PRIMARY KEY,
                campaign_id VARCHAR(64) REFERENCES email_campaigns(campaign_id) ON DELETE CASCADE,
                recipient_user_id VARCHAR(64) REFERENCES users(user_id) ON DELETE CASCADE,
                recipient_email VARCHAR(255) NOT NULL,
                rendered_subject TEXT NOT NULL,
                rendered_html TEXT NOT NULL,
                rendered_text TEXT DEFAULT '',
                status VARCHAR(16) DEFAULT 'pending',
                locked_by VARCHAR(64),
                locked_at TIMESTAMPTZ,
                attempt_count INTEGER DEFAULT 0,
                error_msg TEXT,
                provider_message_id VARCHAR(128),
                sent_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            # Allow external recipients (not backed by a users row) for individual targeting.
            "ALTER TABLE email_send_queue ALTER COLUMN recipient_user_id DROP NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_send_queue_status_locked ON email_send_queue(status, locked_at)",
            "CREATE INDEX IF NOT EXISTS idx_send_queue_campaign ON email_send_queue(campaign_id)",
            # Dedup guard : 1 send par user par campagne max
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_send_queue_unique_user_campaign ON email_send_queue(campaign_id, recipient_user_id)",
            """CREATE TABLE IF NOT EXISTS email_logs (
                id SERIAL PRIMARY KEY,
                log_id VARCHAR(64) UNIQUE NOT NULL,
                campaign_id VARCHAR(64),
                template_id VARCHAR(64),
                user_id VARCHAR(64),
                email VARCHAR(255),
                event VARCHAR(24) NOT NULL,
                provider_message_id VARCHAR(128),
                url TEXT,
                ip_address VARCHAR(64),
                user_agent TEXT,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_email_logs_campaign_event ON email_logs(campaign_id, event)",
            "CREATE INDEX IF NOT EXISTS idx_email_logs_user_event ON email_logs(user_id, event)",
            "CREATE INDEX IF NOT EXISTS idx_email_logs_provider_msg ON email_logs(provider_message_id)",
            """CREATE TABLE IF NOT EXISTS email_automations (
                id SERIAL PRIMARY KEY,
                automation_id VARCHAR(64) UNIQUE NOT NULL,
                name VARCHAR(120) NOT NULL,
                trigger_type VARCHAR(32) NOT NULL,
                trigger_config JSONB DEFAULT '{}'::jsonb,
                template_id VARCHAR(64) REFERENCES email_templates(template_id) ON DELETE SET NULL,
                segment_id VARCHAR(64) REFERENCES email_segments(segment_id) ON DELETE SET NULL,
                delay_minutes INTEGER DEFAULT 0,
                is_enabled BOOLEAN DEFAULT FALSE,
                last_run_at TIMESTAMPTZ,
                sent_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS email_automation_runs (
                automation_id VARCHAR(64) NOT NULL,
                user_id VARCHAR(64) NOT NULL,
                ran_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (automation_id, user_id)
            )""",
            # iter67 — Crypto Staking module (MIR token, BSC, off-chain signature-based)
            """CREATE TABLE IF NOT EXISTS staking_plans (
                id SERIAL PRIMARY KEY,
                plan_id VARCHAR(32) UNIQUE NOT NULL,
                name VARCHAR(120) NOT NULL,
                duration_months INTEGER NOT NULL,
                apy_bps INTEGER NOT NULL DEFAULT 400,
                early_withdrawal_fee_bps INTEGER NOT NULL DEFAULT 1500,
                min_stake_mir NUMERIC(24,8) NOT NULL DEFAULT 10,
                max_stake_mir NUMERIC(24,8),
                marketing_copy TEXT DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """ALTER TABLE staking_plans ADD COLUMN IF NOT EXISTS max_stake_mir NUMERIC(24,8)""",
            """CREATE TABLE IF NOT EXISTS staking_wallets (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                wallet_address VARCHAR(64) NOT NULL,
                chain_id INTEGER NOT NULL DEFAULT 56,
                signature TEXT,
                signed_message TEXT,
                connected_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_staking_wallets_address ON staking_wallets(LOWER(wallet_address))""",
            """CREATE TABLE IF NOT EXISTS staking_positions (
                id SERIAL PRIMARY KEY,
                position_id VARCHAR(32) UNIQUE NOT NULL,
                user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                plan_id VARCHAR(32) NOT NULL,
                wallet_address VARCHAR(64) NOT NULL,
                amount_mir NUMERIC(24,8) NOT NULL,
                apy_bps_snapshot INTEGER NOT NULL,
                early_fee_bps_snapshot INTEGER NOT NULL,
                signature TEXT,
                signed_message TEXT,
                staked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                unlocks_at TIMESTAMPTZ NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'active',
                withdrawn_at TIMESTAMPTZ,
                rewards_paid_mir NUMERIC(24,8) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE INDEX IF NOT EXISTS idx_staking_positions_user ON staking_positions(user_id, status)""",
            """CREATE INDEX IF NOT EXISTS idx_staking_positions_wallet ON staking_positions(LOWER(wallet_address))""",
            """CREATE TABLE IF NOT EXISTS staking_balances (
                user_id VARCHAR(64) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                mir_balance NUMERIC(24,8) NOT NULL DEFAULT 0,
                total_staked_mir NUMERIC(24,8) NOT NULL DEFAULT 0,
                earned_mir NUMERIC(24,8) NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            # Iter 83 — Superadmin role + granular admin sub-roles + audit trail
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_sub_roles JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS force_password_change BOOLEAN NOT NULL DEFAULT FALSE",
            "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
            """CREATE TABLE IF NOT EXISTS admin_audit_log (
                id BIGSERIAL PRIMARY KEY,
                actor_id VARCHAR(64) REFERENCES users(user_id) ON DELETE SET NULL,
                actor_email VARCHAR(255),
                action VARCHAR(64) NOT NULL,
                target_id VARCHAR(64),
                target_email VARCHAR(255),
                metadata JSONB DEFAULT '{}'::jsonb,
                ip_address VARCHAR(64),
                user_agent VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_admin_audit_actor ON admin_audit_log(actor_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_admin_audit_action ON admin_audit_log(action, created_at DESC)",
        ]:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"Migration '{stmt[:60]}...' failed: {e}")
        logger.info("Database schema initialized")

async def close_db():
    global pool
    if pool:
        await pool.close()
        pool = None
