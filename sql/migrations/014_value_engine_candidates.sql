-- 014: Value Engine metrics columns for pick_candidates
-- Incremental migration — adds nullable columns only; never drops or modifies existing columns.
-- Apply via Supabase SQL Editor (not via migration runner — runs once, idempotent).
--
-- Before enabling VALUE_ENGINE_MODE=shadow or assist, run this migration in Supabase.
-- The bot will continue to work without these columns (they are never read by existing code).

ALTER TABLE pick_candidates
    ADD COLUMN IF NOT EXISTS value_engine_status    TEXT,
    ADD COLUMN IF NOT EXISTS value_p_cal            NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS value_fair_odds        NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS value_p_mkt            NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS value_edge             NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS value_ev               NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS value_ev_adj           NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS value_w_rel            NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS value_quality_score    NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS value_rejection_reason TEXT,
    ADD COLUMN IF NOT EXISTS value_engine_meta      JSONB,
    ADD COLUMN IF NOT EXISTS value_engine_created_at TIMESTAMPTZ DEFAULT now();

-- Optional index for querying by value engine status (useful for reporting)
CREATE INDEX IF NOT EXISTS idx_pick_candidates_ve_status
    ON pick_candidates (value_engine_status)
    WHERE value_engine_status IS NOT NULL;
