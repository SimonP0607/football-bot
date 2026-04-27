-- Phase 6: Value Engine schema.
-- Idempotent: CREATE IF NOT EXISTS throughout.
-- Apply with: python scripts/init_local_db.py

-- ── Sequences ─────────────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS seq_calibration  START 1;
CREATE SEQUENCE IF NOT EXISTS seq_shadow_picks START 1;

-- ── calibration_registry ──────────────────────────────────────────────────────
-- Stores learned probability calibrators per (market, entity_type, scope).
--
-- Scope hierarchy (most specific first):
--   provider_league_id IS NOT NULL                            -> league-specific
--   provider_league_id IS NULL, competition_type IS NOT NULL  -> competition-type
--   provider_league_id IS NULL, competition_type IS NULL      -> entity-type global
--
-- params_json: JSON with "bins" list [{center, n, raw_rate, calibrated}...]
-- method: 'histogram_isotonic' (PAVA applied over histogram bins)

CREATE TABLE IF NOT EXISTS calibration_registry (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_calibration'),
    trained_at          TIMESTAMPTZ DEFAULT current_timestamp,
    market_key          VARCHAR NOT NULL,
    entity_type         VARCHAR NOT NULL,
    competition_type    VARCHAR,
    provider_league_id  INTEGER,
    method              VARCHAR DEFAULT 'histogram_isotonic',
    n_train             INTEGER,
    ece_before          DOUBLE,
    ece_after           DOUBLE,
    logloss_before      DOUBLE,
    logloss_after       DOUBLE,
    params_json         VARCHAR
);

CREATE INDEX IF NOT EXISTS calib_registry_lookup_idx
    ON calibration_registry (market_key, entity_type, provider_league_id);

-- ── market_quality_summary ────────────────────────────────────────────────────
-- Per-league / market / season quality metrics, populated during calibration.
-- eligible_flag gates the selection engine: markets with poor calibration are
-- excluded as pick candidates regardless of probability.
--
-- drift_score: temporal drift across seasons (NULL until multi-season data exists)

CREATE TABLE IF NOT EXISTS market_quality_summary (
    provider_league_id  INTEGER NOT NULL,
    market_key          VARCHAR NOT NULL,
    entity_type         VARCHAR NOT NULL DEFAULT 'club',
    competition_type    VARCHAR,
    season              INTEGER NOT NULL,
    n                   INTEGER,
    logloss             DOUBLE,
    brier               DOUBLE,
    ece                 DOUBLE,
    sharpness           DOUBLE,
    hit_rate            DOUBLE,
    drift_score         DOUBLE,
    eligible_flag       BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (provider_league_id, market_key, season)
);

-- ── shadow_value_picks ────────────────────────────────────────────────────────
-- Persists shadow mode value engine output for audit and eventual grading.
-- Odds-dependent columns (p_mkt, edge, ev, ev_adj, offered_odds) are NULL in
-- Phase 5 — populated once historical odds are backfilled in Phase 5.5.
--
-- decision_status: 'selected' | 'rejected_cap' | 'rejected_filter'

CREATE TABLE IF NOT EXISTS shadow_value_picks (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_shadow_picks'),
    run_date            VARCHAR NOT NULL,
    fixture_id          BIGINT,
    provider_league_id  INTEGER,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    p_raw               DOUBLE,
    p_cal               DOUBLE,
    p_mkt               DOUBLE,
    p_adj               DOUBLE,
    fair_odds           DOUBLE,
    offered_odds        DOUBLE,
    edge                DOUBLE,
    ev                  DOUBLE,
    ev_adj              DOUBLE,
    w_rel               DOUBLE,
    risk_score          DOUBLE,
    quality_score       DOUBLE,
    decision_status     VARCHAR
);

CREATE INDEX IF NOT EXISTS shadow_picks_date_idx
    ON shadow_value_picks (run_date, provider_league_id);

-- ── Fase B: out-of-sample validation columns ──────────────────────────────────
-- Added after initial release. ALTER TABLE ... IF NOT EXISTS is idempotent.

ALTER TABLE calibration_registry ADD COLUMN IF NOT EXISTS n_val       INTEGER;
ALTER TABLE calibration_registry ADD COLUMN IF NOT EXISTS val_ece     DOUBLE;
ALTER TABLE calibration_registry ADD COLUMN IF NOT EXISTS val_brier   DOUBLE;
ALTER TABLE calibration_registry ADD COLUMN IF NOT EXISTS val_logloss DOUBLE;

-- ── Fase C: shadow pick grading columns ───────────────────────────────────────
-- Populated by settle_shadow_picks() after fixture scores are known.

ALTER TABLE shadow_value_picks ADD COLUMN IF NOT EXISTS actual_outcome VARCHAR;
ALTER TABLE shadow_value_picks ADD COLUMN IF NOT EXISTS model_correct  BOOLEAN;
ALTER TABLE shadow_value_picks ADD COLUMN IF NOT EXISTS graded_at      VARCHAR;
