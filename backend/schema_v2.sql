-- ================================================================
-- JAPAP Messenger — Schema v2 (Complete Migration Phase 2)
-- Creates tables to receive ALL remaining legacy data from WoWonder
-- ================================================================

-- ---------- SOCIAL GROUPS (Wo_Groups) ----------
CREATE TABLE IF NOT EXISTS social_groups (
    id SERIAL PRIMARY KEY,
    group_id VARCHAR(64) UNIQUE NOT NULL,
    owner_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    name VARCHAR(255) NOT NULL,
    title VARCHAR(255) DEFAULT '',
    about TEXT DEFAULT '',
    avatar VARCHAR(500) DEFAULT '',
    cover VARCHAR(500) DEFAULT '',
    category VARCHAR(100) DEFAULT 'general',
    privacy VARCHAR(20) DEFAULT 'public',
    join_privacy VARCHAR(20) DEFAULT 'open',
    active BOOLEAN DEFAULT TRUE,
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_social_groups_owner ON social_groups(owner_id);
CREATE INDEX IF NOT EXISTS idx_social_groups_legacy ON social_groups(legacy_id);

CREATE TABLE IF NOT EXISTS social_group_members (
    id SERIAL PRIMARY KEY,
    group_id VARCHAR(64) NOT NULL REFERENCES social_groups(group_id),
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    role VARCHAR(20) DEFAULT 'member',
    active BOOLEAN DEFAULT TRUE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_sgm_group ON social_group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_sgm_user ON social_group_members(user_id);

-- ---------- PAGES (Wo_Pages) ----------
CREATE TABLE IF NOT EXISTS pages (
    id SERIAL PRIMARY KEY,
    page_id VARCHAR(64) UNIQUE NOT NULL,
    owner_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    name VARCHAR(255) NOT NULL,
    title VARCHAR(255) DEFAULT '',
    description TEXT DEFAULT '',
    avatar VARCHAR(500) DEFAULT '',
    cover VARCHAR(500) DEFAULT '',
    category VARCHAR(100) DEFAULT 'general',
    website VARCHAR(500) DEFAULT '',
    phone VARCHAR(64) DEFAULT '',
    address VARCHAR(500) DEFAULT '',
    company VARCHAR(255) DEFAULT '',
    verified BOOLEAN DEFAULT FALSE,
    active BOOLEAN DEFAULT TRUE,
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pages_owner ON pages(owner_id);
CREATE INDEX IF NOT EXISTS idx_pages_legacy ON pages(legacy_id);

CREATE TABLE IF NOT EXISTS page_likes (
    id SERIAL PRIMARY KEY,
    page_id VARCHAR(64) NOT NULL REFERENCES pages(page_id),
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(page_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_page_likes_page ON page_likes(page_id);

-- ---------- BLOG (Wo_Blog) ----------
CREATE TABLE IF NOT EXISTS blog_articles (
    id SERIAL PRIMARY KEY,
    article_id VARCHAR(64) UNIQUE NOT NULL,
    author_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    title VARCHAR(500) NOT NULL,
    description TEXT DEFAULT '',
    content TEXT DEFAULT '',
    thumbnail VARCHAR(500) DEFAULT '',
    category VARCHAR(100) DEFAULT 'general',
    tags TEXT DEFAULT '',
    views INTEGER DEFAULT 0,
    shared INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_blog_author ON blog_articles(author_id);

-- ---------- EVENTS (Wo_Events) ----------
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    poster_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    name VARCHAR(500) NOT NULL,
    description TEXT DEFAULT '',
    location VARCHAR(500) DEFAULT '',
    cover VARCHAR(500) DEFAULT '',
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_poster ON events(poster_id);

-- ---------- HASHTAGS (Wo_Hashtags) ----------
CREATE TABLE IF NOT EXISTS hashtags (
    id SERIAL PRIMARY KEY,
    tag VARCHAR(255) UNIQUE NOT NULL,
    use_count INTEGER DEFAULT 0,
    last_trend_at TIMESTAMPTZ,
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hashtags_tag ON hashtags(tag);

CREATE TABLE IF NOT EXISTS post_hashtags (
    id SERIAL PRIMARY KEY,
    post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    tag VARCHAR(255) NOT NULL,
    UNIQUE(post_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_post_hashtags_tag ON post_hashtags(tag);

-- ---------- POLLS (Wo_Polls + Wo_Votes) ----------
CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    poll_id VARCHAR(64) UNIQUE NOT NULL,
    post_id VARCHAR(64),
    question TEXT NOT NULL,
    options JSONB DEFAULT '[]'::jsonb,
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS poll_votes (
    id SERIAL PRIMARY KEY,
    poll_id VARCHAR(64) NOT NULL REFERENCES polls(poll_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    option_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(poll_id, user_id)
);

-- ---------- USER BLOCKS (Wo_Blocks) ----------
CREATE TABLE IF NOT EXISTS user_blocks (
    id SERIAL PRIMARY KEY,
    blocker_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    blocked_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(blocker_id, blocked_id)
);
CREATE INDEX IF NOT EXISTS idx_user_blocks_blocker ON user_blocks(blocker_id);

-- ---------- SAVED / HIDDEN / PINNED POSTS ----------
CREATE TABLE IF NOT EXISTS saved_posts (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, post_id)
);
CREATE INDEX IF NOT EXISTS idx_saved_posts_user ON saved_posts(user_id);

CREATE TABLE IF NOT EXISTS hidden_posts (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, post_id)
);

CREATE TABLE IF NOT EXISTS pinned_posts (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    post_id VARCHAR(64) NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, post_id)
);

-- ---------- MEDIA LIBRARY (Wo_UploadedMedia + Wo_Albums_Media) ----------
CREATE TABLE IF NOT EXISTS media_library (
    id SERIAL PRIMARY KEY,
    media_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64) REFERENCES users(user_id),
    post_id VARCHAR(64),
    filename VARCHAR(1000) NOT NULL,
    storage VARCHAR(50) DEFAULT 'local',
    type VARCHAR(30) DEFAULT 'image',
    album_name VARCHAR(255) DEFAULT '',
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_media_lib_user ON media_library(user_id);
CREATE INDEX IF NOT EXISTS idx_media_lib_post ON media_library(post_id);

-- ---------- USER EXTENDED FIELDS ----------
CREATE TABLE IF NOT EXISTS user_fields (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id),
    field_key VARCHAR(100) NOT NULL,
    field_value TEXT DEFAULT '',
    UNIQUE(user_id, field_key)
);
CREATE INDEX IF NOT EXISTS idx_user_fields_user ON user_fields(user_id);

-- ---------- PRODUCT MEDIA & CATEGORIES ----------
CREATE TABLE IF NOT EXISTS product_categories (
    id SERIAL PRIMARY KEY,
    category_key VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    legacy_id INTEGER
);

-- ---------- CRYPTO PAYMENTS / BANK RECEIPTS ----------
CREATE TABLE IF NOT EXISTS crypto_payments (
    id SERIAL PRIMARY KEY,
    payment_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64) REFERENCES users(user_id),
    currency VARCHAR(20) DEFAULT 'USD',
    amount NUMERIC(15,4) DEFAULT 0,
    coin VARCHAR(20) DEFAULT '',
    status VARCHAR(30) DEFAULT 'completed',
    hash TEXT DEFAULT '',
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bank_receipts (
    id SERIAL PRIMARY KEY,
    receipt_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64) REFERENCES users(user_id),
    amount NUMERIC(15,2) DEFAULT 0,
    method VARCHAR(50) DEFAULT '',
    status VARCHAR(30) DEFAULT 'pending',
    legacy_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exchange_countries (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(10) NOT NULL,
    country_name VARCHAR(255) NOT NULL,
    currency VARCHAR(20) DEFAULT '',
    rate NUMERIC(15,6) DEFAULT 1,
    active BOOLEAN DEFAULT TRUE,
    legacy_id INTEGER
);

CREATE TABLE IF NOT EXISTS withdraw_countries (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(10) NOT NULL,
    country_name VARCHAR(255) NOT NULL,
    method VARCHAR(50) DEFAULT '',
    active BOOLEAN DEFAULT TRUE,
    legacy_id INTEGER
);

-- Add legacy_id indexes for posts, products, notifications, orders if missing
CREATE INDEX IF NOT EXISTS idx_posts_legacy ON posts(post_id);
CREATE INDEX IF NOT EXISTS idx_products_legacy ON products(product_id);
CREATE INDEX IF NOT EXISTS idx_orders_legacy ON orders(order_id);
