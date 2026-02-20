-- Migration 001: Add balatro_strategies table + FK on balatro_runs
-- Goal: Strategy numbering, seed reproduction, strategy detail pages

BEGIN;

-- Strategy definitions
CREATE TABLE IF NOT EXISTS balatro_strategies (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL UNIQUE,          -- e.g. "STR-001"
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

-- Add strategy_id FK to balatro_runs (nullable — existing runs have no strategy yet)
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
