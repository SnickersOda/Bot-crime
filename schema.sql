-- ═══════════════════════════════════════════════════
--  CriminalCity RPG — PostgreSQL Schema
--  Run once on a fresh Railway PostgreSQL instance
-- ═══════════════════════════════════════════════════

-- Users (core game state)
CREATE TABLE IF NOT EXISTS users (
    user_id       BIGINT PRIMARY KEY,
    username      TEXT,
    full_name     TEXT,
    cash          INTEGER NOT NULL DEFAULT 500,
    xp            INTEGER NOT NULL DEFAULT 0,
    level         INTEGER NOT NULL DEFAULT 1,
    wins          INTEGER NOT NULL DEFAULT 0,
    losses        INTEGER NOT NULL DEFAULT 0,
    robberies     INTEGER NOT NULL DEFAULT 0,
    season_xp     INTEGER NOT NULL DEFAULT 0,
    gang_id       INTEGER,
    in_jail_until BIGINT  NOT NULL DEFAULT 0,
    is_banned     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    BIGINT  NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Game items catalogue
CREATE TABLE IF NOT EXISTS items (
    item_id     TEXT PRIMARY KEY,
    name        TEXT    NOT NULL,
    price       INTEGER NOT NULL,
    pvp_bonus   INTEGER NOT NULL DEFAULT 0,
    def_bonus   INTEGER NOT NULL DEFAULT 0,
    rob_bonus   FLOAT   NOT NULL DEFAULT 0.0,
    description TEXT
);

-- Player inventory (many-to-many, unique per player)
CREATE TABLE IF NOT EXISTS inventory (
    id      SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    item_id TEXT   NOT NULL,
    UNIQUE (user_id, item_id)
);

-- Gangs
CREATE TABLE IF NOT EXISTS gangs (
    id         SERIAL PRIMARY KEY,
    name       TEXT   UNIQUE NOT NULL,
    owner_id   BIGINT NOT NULL,
    bank       INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT  NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Gang membership
CREATE TABLE IF NOT EXISTS gang_members (
    gang_id INTEGER NOT NULL REFERENCES gangs(id) ON DELETE CASCADE,
    user_id BIGINT  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role    TEXT    NOT NULL DEFAULT 'member',   -- 'owner' | 'member'
    PRIMARY KEY (gang_id, user_id)
);

-- Action cooldowns (keyed by user + action name)
CREATE TABLE IF NOT EXISTS cooldowns (
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    action  TEXT   NOT NULL,
    expires BIGINT NOT NULL,
    PRIMARY KEY (user_id, action)
);

-- Bans log
CREATE TABLE IF NOT EXISTS bans (
    user_id   BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    reason    TEXT,
    banned_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
    banned_by BIGINT
);

-- Season tracking
CREATE TABLE IF NOT EXISTS season (
    id         SERIAL PRIMARY KEY,
    number     INTEGER NOT NULL DEFAULT 1,
    started_at BIGINT  NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Insert first season if none exist
INSERT INTO season (number) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM season);

-- Indices for hot paths
CREATE INDEX IF NOT EXISTS idx_users_gang    ON users (gang_id);
CREATE INDEX IF NOT EXISTS idx_users_banned  ON users (is_banned);
CREATE INDEX IF NOT EXISTS idx_cooldowns_uid ON cooldowns (user_id);
CREATE INDEX IF NOT EXISTS idx_inv_uid       ON inventory (user_id);

-- Businesses
CREATE TABLE IF NOT EXISTS businesses (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    biz_id       TEXT    NOT NULL,
    level        INTEGER NOT NULL DEFAULT 1,
    last_collect BIGINT  NOT NULL DEFAULT 0,
    UNIQUE (user_id, biz_id)
);

CREATE INDEX IF NOT EXISTS idx_businesses_uid ON businesses (user_id);

-- Casino stats per player
CREATE TABLE IF NOT EXISTS casino_stats (
    user_id      BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    total_won    INTEGER NOT NULL DEFAULT 0,
    total_lost   INTEGER NOT NULL DEFAULT 0,
    games_played INTEGER NOT NULL DEFAULT 0
);
