-- 015_model_governance_schema.sql
-- Phase 15: Model Governance, Experiment Lab & Safe Activation Control
-- All idempotent — safe to run multiple times.

-- ── experiment_registry ───────────────────────────────────────────────────────
-- Defines each experiment variant being tracked.
CREATE TABLE IF NOT EXISTS experiment_registry (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    updated_at          TIMESTAMP DEFAULT current_timestamp,
    experiment_key      VARCHAR NOT NULL,         -- e.g. 'variant_strategy_learning'
    variant_name        VARCHAR NOT NULL,          -- human-readable label
    module              VARCHAR NOT NULL,          -- value_engine|strategy_learning|bankroll|market_clv|parlay
    description         VARCHAR,
    status              VARCHAR DEFAULT 'active',  -- active|paused|archived
    started_at          TIMESTAMP,
    ended_at            TIMESTAMP,
    min_sample          INTEGER DEFAULT 100,
    config_json         VARCHAR                    -- JSON overrides applied during experiment
);

CREATE SEQUENCE IF NOT EXISTS experiment_registry_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_er_experiment_key
    ON experiment_registry(experiment_key);

CREATE INDEX IF NOT EXISTS idx_er_module     ON experiment_registry(module);
CREATE INDEX IF NOT EXISTS idx_er_status     ON experiment_registry(status);
CREATE INDEX IF NOT EXISTS idx_er_created_at ON experiment_registry(created_at);


-- ── experiment_pick_assignments ───────────────────────────────────────────────
-- Links each pick to one or more experiment variants for shadow comparison.
CREATE TABLE IF NOT EXISTS experiment_pick_assignments (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    pick_candidate_id   BIGINT NOT NULL,
    fixture_id          BIGINT,
    experiment_key      VARCHAR NOT NULL,
    market_key          VARCHAR,
    league_id           BIGINT,
    assigned_date       DATE,
    shadow_edge         FLOAT,
    shadow_confidence   FLOAT,
    shadow_quality      FLOAT,
    shadow_selected     BOOLEAN DEFAULT false,
    metadata_json       VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS experiment_pick_assignments_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_epa_unique
    ON experiment_pick_assignments(pick_candidate_id, experiment_key);

CREATE INDEX IF NOT EXISTS idx_epa_experiment_key ON experiment_pick_assignments(experiment_key);
CREATE INDEX IF NOT EXISTS idx_epa_fixture_id     ON experiment_pick_assignments(fixture_id);
CREATE INDEX IF NOT EXISTS idx_epa_assigned_date  ON experiment_pick_assignments(assigned_date);
CREATE INDEX IF NOT EXISTS idx_epa_league_id      ON experiment_pick_assignments(league_id);


-- ── experiment_results ────────────────────────────────────────────────────────
-- Aggregated rolling results per experiment variant.
CREATE TABLE IF NOT EXISTS experiment_results (
    id                      BIGINT PRIMARY KEY,
    created_at              TIMESTAMP DEFAULT current_timestamp,
    updated_at              TIMESTAMP DEFAULT current_timestamp,
    experiment_key          VARCHAR NOT NULL,
    days_window             INTEGER DEFAULT 30,
    sample_size             INTEGER DEFAULT 0,
    wins                    INTEGER DEFAULT 0,
    losses                  INTEGER DEFAULT 0,
    voids                   INTEGER DEFAULT 0,
    hit_rate                FLOAT,
    roi                     FLOAT,
    avg_edge                FLOAT,
    avg_clv_percent         FLOAT,
    clv_beat_rate           FLOAT,
    max_drawdown            FLOAT,
    confidence_interval_low FLOAT,
    confidence_interval_high FLOAT,
    lift_vs_baseline        FLOAT,       -- ROI delta vs baseline_current
    baseline_roi            FLOAT,
    baseline_sample         INTEGER,
    gates_passed            INTEGER DEFAULT 0,
    gates_total             INTEGER DEFAULT 0,
    recommendation          VARCHAR DEFAULT 'DO_NOT_ACTIVATE',
    notes_json              VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS experiment_results_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_exr_unique
    ON experiment_results(experiment_key, days_window);

CREATE INDEX IF NOT EXISTS idx_exr_experiment_key  ON experiment_results(experiment_key);
CREATE INDEX IF NOT EXISTS idx_exr_recommendation  ON experiment_results(recommendation);
CREATE INDEX IF NOT EXISTS idx_exr_updated_at      ON experiment_results(updated_at);


-- ── model_decision_audit ──────────────────────────────────────────────────────
-- Per-pick record of what each module recommended vs what the pipeline decided.
CREATE TABLE IF NOT EXISTS model_decision_audit (
    id                      BIGINT PRIMARY KEY,
    created_at              TIMESTAMP DEFAULT current_timestamp,
    pick_candidate_id       BIGINT,
    published_pick_id       BIGINT,
    fixture_id              BIGINT,
    market_key              VARCHAR,
    league_id               BIGINT,
    assigned_date           DATE,
    -- Official pipeline decision
    official_selected       BOOLEAN,
    official_edge           FLOAT,
    official_quality        FLOAT,
    -- Value engine module
    ve_selected             BOOLEAN,
    ve_edge                 FLOAT,
    ve_quality              FLOAT,
    -- Strategy learning module
    sl_recommendation       VARCHAR,     -- promote|reduce|avoid|monitor|insufficient_sample
    sl_strategy_score       FLOAT,
    -- Bankroll module
    br_recommended_units    FLOAT,
    br_risk_label           VARCHAR,
    br_rejected             BOOLEAN,
    -- Market intelligence module
    mi_signal               VARCHAR,
    mi_clv_percent          FLOAT,
    -- Final decision details
    final_selected          BOOLEAN,
    decision_changed        BOOLEAN DEFAULT false,
    change_reason           VARCHAR,
    safety_blocked          BOOLEAN DEFAULT false,
    metadata_json           VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS model_decision_audit_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mda_pick_candidate
    ON model_decision_audit(pick_candidate_id);

CREATE INDEX IF NOT EXISTS idx_mda_published_pick  ON model_decision_audit(published_pick_id);
CREATE INDEX IF NOT EXISTS idx_mda_fixture_id      ON model_decision_audit(fixture_id);
CREATE INDEX IF NOT EXISTS idx_mda_assigned_date   ON model_decision_audit(assigned_date);
CREATE INDEX IF NOT EXISTS idx_mda_league_id       ON model_decision_audit(league_id);
CREATE INDEX IF NOT EXISTS idx_mda_decision_changed ON model_decision_audit(decision_changed);
CREATE INDEX IF NOT EXISTS idx_mda_created_at      ON model_decision_audit(created_at);


-- ── activation_recommendations ────────────────────────────────────────────────
-- Governance engine's formal recommendation per module.
CREATE TABLE IF NOT EXISTS activation_recommendations (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    updated_at          TIMESTAMP DEFAULT current_timestamp,
    module              VARCHAR NOT NULL,   -- strategy_learning|bankroll|market_clv|parlay|live_monitoring
    recommendation      VARCHAR NOT NULL,   -- DO_NOT_ACTIVATE|OBSERVE_MORE|SAFE_TO_TEST_SHADOW|SAFE_TO_TEST_ASSIST|SAFE_TO_USE_FOR_SELECTION
    sample_size         INTEGER DEFAULT 0,
    gates_passed        INTEGER DEFAULT 0,
    gates_total         INTEGER DEFAULT 0,
    gate_details_json   VARCHAR,            -- JSON: {gate_name: passed_bool, ...}
    roi                 FLOAT,
    lift_vs_baseline    FLOAT,
    drawdown            FLOAT,
    clv_avg             FLOAT,
    blocking_reason     VARCHAR,            -- why NOT activatable (if any)
    notes               VARCHAR,
    valid_until         TIMESTAMP
);

CREATE SEQUENCE IF NOT EXISTS activation_recommendations_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ar_module
    ON activation_recommendations(module);

CREATE INDEX IF NOT EXISTS idx_ar_recommendation ON activation_recommendations(recommendation);
CREATE INDEX IF NOT EXISTS idx_ar_updated_at     ON activation_recommendations(updated_at);
