-- 008_parlay_engine_schema.sql
-- Phase 8: Smart Parlay Engine tables
-- All idempotent — safe to run multiple times (CREATE IF NOT EXISTS).
-- Uses TIMESTAMP (not TIMESTAMPTZ) for DuckDB compatibility without pytz.

-- One row per parlay combination (identified by parlay_key).
-- parlay_key = "{date}_{sorted pick_candidate_ids}"
CREATE TABLE IF NOT EXISTS parlay_candidates (
    parlay_key           VARCHAR PRIMARY KEY,
    created_at           TIMESTAMP DEFAULT current_timestamp,
    date                 VARCHAR NOT NULL,
    parlay_type          VARCHAR,          -- 'conservadora' | 'balanceada' | 'agresiva'
    legs_count           INTEGER NOT NULL,
    total_odds           DOUBLE,
    joint_probability    DOUBLE,
    implied_probability  DOUBLE,
    edge                 DOUBLE,
    ev                   DOUBLE,
    risk_score           DOUBLE,
    correlation_score    DOUBLE,
    confidence_score     DOUBLE,
    recommendation_status VARCHAR,
    rejection_reason     VARCHAR,
    metadata_json        VARCHAR           -- JSON blob with extra context
);

-- Individual legs of each parlay.
-- leg_id = "{parlay_key}_{leg_order}"
CREATE TABLE IF NOT EXISTS parlay_legs (
    leg_id               VARCHAR PRIMARY KEY,
    parlay_key           VARCHAR NOT NULL,
    pick_candidate_id    INTEGER,
    fixture_id           INTEGER,
    provider_fixture_id  BIGINT,
    league_id            INTEGER,
    market_key           VARCHAR,
    selection            VARCHAR,
    odds                 DOUBLE,
    p_model              DOUBLE,
    p_cal                DOUBLE,
    edge                 DOUBLE,
    quality_score        DOUBLE,
    leg_order            INTEGER,
    metadata_json        VARCHAR
);

-- Settlement results (one row per parlay, idempotent on parlay_key).
CREATE TABLE IF NOT EXISTS parlay_results (
    parlay_key           VARCHAR PRIMARY KEY,
    result_status        VARCHAR,          -- 'win' | 'loss' | 'void' | 'pending'
    legs_won             INTEGER DEFAULT 0,
    legs_lost            INTEGER DEFAULT 0,
    legs_void            INTEGER DEFAULT 0,
    stake_units          DOUBLE DEFAULT 0.25,
    profit_units         DOUBLE,
    roi                  DOUBLE,
    settled_at           TIMESTAMP,
    metadata_json        VARCHAR
);

-- Configurable risk rules (pre-seeded, advisory only).
CREATE TABLE IF NOT EXISTS parlay_risk_rules (
    rule_key             VARCHAR PRIMARY KEY,
    rule_name            VARCHAR,
    is_active            BOOLEAN DEFAULT TRUE,
    severity             VARCHAR,          -- 'high' | 'medium' | 'low'
    config_json          VARCHAR,
    created_at           TIMESTAMP DEFAULT current_timestamp
);

-- Seed default risk rules (idempotent)
INSERT OR IGNORE INTO parlay_risk_rules (rule_key, rule_name, is_active, severity, config_json)
VALUES
    ('no_same_fixture',    'No picks del mismo fixture',    TRUE, 'high',   '{"penalty": 0.80}'),
    ('no_same_team',       'No picks del mismo equipo',     TRUE, 'high',   '{"penalty": 0.40}'),
    ('max_same_league',    'Max picks por liga',            TRUE, 'medium', '{"max": 2, "penalty": 0.08}'),
    ('over_btts_yes',      'Over + BTTS Yes mismo partido', TRUE, 'high',   '{"penalty": 0.30}'),
    ('under_btts_no',      'Under + BTTS No mismo partido', TRUE, 'high',   '{"penalty": 0.30}'),
    ('home_over_same',     'Gana local + Over mismo partido',TRUE,'medium', '{"penalty": 0.20}'),
    ('away_heavy',         'Mas del 50% selecciones Away',  TRUE, 'low',    '{"penalty": 0.10}'),
    ('min_edge',           'Edge minimo por leg',           TRUE, 'high',   '{"min_edge": 0.0}'),
    ('min_odds',           'Cuota minima por leg',          TRUE, 'high',   '{"min_odds": 1.0}');
