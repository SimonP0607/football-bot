-- 013_strategy_learning_schema.sql
-- Phase 13: CLV Learning Loop & Strategy Scoring
-- All idempotent — safe to run multiple times.

-- ── strategy_profiles ─────────────────────────────────────────────────────────
-- Aggregated performance profile per strategy key (market+scope+buckets).
CREATE TABLE IF NOT EXISTS strategy_profiles (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    updated_at          TIMESTAMP DEFAULT current_timestamp,
    strategy_key        VARCHAR NOT NULL,
    market_key          VARCHAR,
    league_id           BIGINT,
    league_name         VARCHAR,
    scope               VARCHAR DEFAULT 'global',  -- 'global' or 'league_NNN'
    odds_bucket         VARCHAR,
    confidence_bucket   VARCHAR,
    edge_bucket         VARCHAR,
    clv_bucket          VARCHAR,
    sample_size         INTEGER DEFAULT 0,
    wins                INTEGER DEFAULT 0,
    losses              INTEGER DEFAULT 0,
    voids               INTEGER DEFAULT 0,
    hit_rate            FLOAT,
    roi                 FLOAT,
    avg_profit          FLOAT,
    avg_clv_percent     FLOAT,
    clv_beat_rate       FLOAT,
    avg_edge            FLOAT,
    avg_quality_score   FLOAT,
    avg_confidence      FLOAT,
    stability_score     FLOAT,
    strategy_score      FLOAT,
    recommendation      VARCHAR DEFAULT 'insufficient_sample',
    metadata_json       VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS strategy_profiles_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sp_strategy_key
    ON strategy_profiles(strategy_key);

CREATE INDEX IF NOT EXISTS idx_sp_market_key       ON strategy_profiles(market_key);
CREATE INDEX IF NOT EXISTS idx_sp_league_id        ON strategy_profiles(league_id);
CREATE INDEX IF NOT EXISTS idx_sp_sample_size      ON strategy_profiles(sample_size);
CREATE INDEX IF NOT EXISTS idx_sp_strategy_score   ON strategy_profiles(strategy_score);
CREATE INDEX IF NOT EXISTS idx_sp_recommendation   ON strategy_profiles(recommendation);


-- ── strategy_learning_runs ────────────────────────────────────────────────────
-- Log of each time the learning pipeline was executed.
CREATE TABLE IF NOT EXISTS strategy_learning_runs (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    run_key             VARCHAR NOT NULL,
    days                INTEGER,
    total_picks         INTEGER DEFAULT 0,
    profiles_created    INTEGER DEFAULT 0,
    profiles_updated    INTEGER DEFAULT 0,
    best_strategy_key   VARCHAR,
    worst_strategy_key  VARCHAR,
    notes_json          VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS strategy_learning_runs_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_slr_run_key ON strategy_learning_runs(run_key);
CREATE INDEX IF NOT EXISTS idx_slr_created_at     ON strategy_learning_runs(created_at);


-- ── strategy_adjustments ─────────────────────────────────────────────────────
-- Recommended adjustments derived from strategy profiles.
-- Not applied automatically — require explicit activation.
CREATE TABLE IF NOT EXISTS strategy_adjustments (
    id                      BIGINT PRIMARY KEY,
    created_at              TIMESTAMP DEFAULT current_timestamp,
    strategy_key            VARCHAR NOT NULL,
    market_key              VARCHAR,
    league_id               BIGINT,
    adjustment_type         VARCHAR,  -- promote|reduce|avoid|monitor|request_sample|tune_edge|tune_conf|reduce_parlay
    adjustment_value        FLOAT,
    reason                  VARCHAR,
    evidence_sample_size    INTEGER,
    evidence_roi            FLOAT,
    evidence_clv            FLOAT,
    status                  VARCHAR DEFAULT 'pending',  -- pending|applied|rejected|expired
    applied_at              TIMESTAMP,
    metadata_json           VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS strategy_adjustments_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sa_unique
    ON strategy_adjustments(strategy_key, adjustment_type);

CREATE INDEX IF NOT EXISTS idx_sa_market_key       ON strategy_adjustments(market_key);
CREATE INDEX IF NOT EXISTS idx_sa_league_id        ON strategy_adjustments(league_id);
CREATE INDEX IF NOT EXISTS idx_sa_status           ON strategy_adjustments(status);
CREATE INDEX IF NOT EXISTS idx_sa_created_at       ON strategy_adjustments(created_at);


-- ── pick_learning_annotations ─────────────────────────────────────────────────
-- Per-pick annotation linking settlement, CLV, and strategy context.
CREATE TABLE IF NOT EXISTS pick_learning_annotations (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    pick_candidate_id   BIGINT,
    published_pick_id   BIGINT,
    fixture_id          BIGINT,
    strategy_key        VARCHAR,
    market_key          VARCHAR,
    league_id           BIGINT,
    pick_odds           FLOAT,
    pick_edge           FLOAT,
    pick_confidence     FLOAT,
    odds_bucket         VARCHAR,
    confidence_bucket   VARCHAR,
    edge_bucket         VARCHAR,
    clv_bucket          VARCHAR,
    result_status       VARCHAR,  -- win|loss|void|pending
    profit              FLOAT,
    clv_percent         FLOAT,
    beat_closing_line   BOOLEAN,
    learning_label      VARCHAR DEFAULT 'no_data',  -- strong_positive|positive|neutral|weak|negative|no_data
    metadata_json       VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS pick_learning_annotations_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pla_pick_candidate
    ON pick_learning_annotations(pick_candidate_id);

CREATE INDEX IF NOT EXISTS idx_pla_published_pick  ON pick_learning_annotations(published_pick_id);
CREATE INDEX IF NOT EXISTS idx_pla_fixture_id      ON pick_learning_annotations(fixture_id);
CREATE INDEX IF NOT EXISTS idx_pla_strategy_key    ON pick_learning_annotations(strategy_key);
CREATE INDEX IF NOT EXISTS idx_pla_market_key      ON pick_learning_annotations(market_key);
CREATE INDEX IF NOT EXISTS idx_pla_league_id       ON pick_learning_annotations(league_id);
CREATE INDEX IF NOT EXISTS idx_pla_result_status   ON pick_learning_annotations(result_status);
CREATE INDEX IF NOT EXISTS idx_pla_learning_label  ON pick_learning_annotations(learning_label);
CREATE INDEX IF NOT EXISTS idx_pla_created_at      ON pick_learning_annotations(created_at);
