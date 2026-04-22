-- 012: pick_results — settlement table for tracking wins/losses/ROI
-- Run ONCE in Supabase SQL Editor after applying 010 and 011.
-- Safe to re-run (uses IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS pick_results (
    id                BIGSERIAL     PRIMARY KEY,
    pick_candidate_id BIGINT        NOT NULL REFERENCES pick_candidates(id) ON DELETE CASCADE,
    fixture_id        BIGINT        NOT NULL REFERENCES fixtures(id),
    market_key        TEXT          NOT NULL,
    selection         TEXT          NOT NULL,
    -- odd at the time the pick was generated (from argument_json.best_odd)
    odd_taken         NUMERIC(6,2)  NOT NULL,
    result_status     TEXT          NOT NULL DEFAULT 'pending'
                      CHECK (result_status IN ('pending', 'win', 'loss', 'void')),
    settled_at        TIMESTAMPTZ,
    -- profit in units (stake = 1):
    --   win  → odd_taken - 1
    --   loss → -1
    --   void → 0
    profit_units      NUMERIC(8,4),
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pick_results_candidate_uq UNIQUE (pick_candidate_id)
);

CREATE INDEX IF NOT EXISTS pick_results_fixture_idx  ON pick_results (fixture_id);
CREATE INDEX IF NOT EXISTS pick_results_status_idx   ON pick_results (result_status);
CREATE INDEX IF NOT EXISTS pick_results_created_idx  ON pick_results (created_at DESC);
