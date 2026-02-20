-- Balatro Run Viewer - Database Schema
-- Target: Neon PostgreSQL (neondb)
-- Prefix: balatro_ to avoid conflicts with existing tables

BEGIN;

-- Core run data
CREATE TABLE IF NOT EXISTS balatro_runs (
    id SERIAL PRIMARY KEY,
    seed VARCHAR(20),
    deck VARCHAR(50) NOT NULL DEFAULT 'Red Deck',
    stake VARCHAR(20) NOT NULL DEFAULT 'White',
    final_ante INTEGER NOT NULL DEFAULT 1,
    final_score BIGINT,
    won BOOLEAN NOT NULL DEFAULT FALSE,
    endless_ante INTEGER,
    joker_count INTEGER DEFAULT 0,
    notes TEXT,
    played_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Joker lineup per run (order matters in Balatro!)
CREATE TABLE IF NOT EXISTS balatro_jokers (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES balatro_runs(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 1),
    edition VARCHAR(20),  -- NULL, foil, holographic, polychrome
    eternal BOOLEAN DEFAULT FALSE,
    perishable BOOLEAN DEFAULT FALSE,
    rental BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_balatro_jokers_run ON balatro_jokers(run_id);

-- Round-by-round results
CREATE TABLE IF NOT EXISTS balatro_rounds (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES balatro_runs(id) ON DELETE CASCADE,
    ante INTEGER NOT NULL,
    blind_type VARCHAR(10) NOT NULL CHECK (blind_type IN ('small', 'big', 'boss')),
    boss_name VARCHAR(50),
    target_score BIGINT,
    best_hand_score BIGINT,
    hands_played INTEGER,
    discards_used INTEGER,
    skipped BOOLEAN DEFAULT FALSE,
    money_after INTEGER
);

CREATE INDEX idx_balatro_rounds_run ON balatro_rounds(run_id);
CREATE INDEX idx_balatro_rounds_ante ON balatro_rounds(run_id, ante);

-- Screenshot metadata
CREATE TABLE IF NOT EXISTS balatro_screenshots (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES balatro_runs(id) ON DELETE CASCADE,
    round_id INTEGER REFERENCES balatro_rounds(id) ON DELETE SET NULL,
    filename VARCHAR(255) NOT NULL,
    original_name VARCHAR(255),
    caption TEXT,
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_balatro_screenshots_run ON balatro_screenshots(run_id);

-- Tags collected from skipping blinds
CREATE TABLE IF NOT EXISTS balatro_tags (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES balatro_runs(id) ON DELETE CASCADE,
    ante INTEGER NOT NULL,
    name VARCHAR(50) NOT NULL
);

CREATE INDEX idx_balatro_tags_run ON balatro_tags(run_id);

-- Strategy definitions (for strategy numbering, seed reproduction, detail pages)
CREATE TABLE IF NOT EXISTS balatro_strategies (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL UNIQUE,          -- auto-generated: STR-001, STR-002, ...
    name VARCHAR(100) NOT NULL,                -- e.g. "Pairs Build"
    build_type VARCHAR(30),                    -- pairs, flush, straight, high_card, etc.
    description TEXT,                           -- detailed strategy description
    key_jokers JSONB DEFAULT '[]'::jsonb,      -- ["Mime", "Baron", ...] key jokers
    priority_hands JSONB DEFAULT '[]'::jsonb,  -- ["Two Pair", "Full House", ...] target hands
    shop_rules TEXT,                            -- shop decision guidelines
    seed_notes TEXT,                            -- notes on seed-specific behavior
    tags JSONB DEFAULT '[]'::jsonb,            -- ["aggressive", "economy", "late-game"]
    win_rate NUMERIC(5,2),                     -- cached win rate %
    avg_ante NUMERIC(4,1),                     -- cached avg final ante
    run_count INTEGER DEFAULT 0,               -- cached run count
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_balatro_strategies_code ON balatro_strategies(code);
CREATE INDEX idx_balatro_strategies_build ON balatro_strategies(build_type);

-- FK: link runs to strategies
ALTER TABLE balatro_runs
    ADD COLUMN IF NOT EXISTS strategy_id INTEGER REFERENCES balatro_strategies(id) ON DELETE SET NULL;

CREATE INDEX idx_balatro_runs_strategy ON balatro_runs(strategy_id);

-- Auto-generate strategy codes: STR-001, STR-002, ...
CREATE OR REPLACE FUNCTION generate_strategy_code()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.code IS NULL OR NEW.code = '' THEN
        NEW.code := 'STR-' || LPAD(NEW.id::text, 3, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_strategy_code
    BEFORE INSERT ON balatro_strategies
    FOR EACH ROW
    EXECUTE FUNCTION generate_strategy_code();

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_strategy_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_strategy_updated
    BEFORE UPDATE ON balatro_strategies
    FOR EACH ROW
    EXECUTE FUNCTION update_strategy_timestamp();

COMMIT;
