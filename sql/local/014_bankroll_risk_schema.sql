-- Phase 14: Bankroll, Stake Sizing & Risk Portfolio Engine
-- All tables are idempotent (IF NOT EXISTS). Safe to run multiple times.

CREATE SEQUENCE IF NOT EXISTS bankroll_profiles_seq START 1;
CREATE SEQUENCE IF NOT EXISTS stake_recommendations_seq START 1;
CREATE SEQUENCE IF NOT EXISTS portfolio_risk_snapshots_seq START 1;
CREATE SEQUENCE IF NOT EXISTS risk_events_seq START 1;

-- Active bankroll management profile (one active at a time)
CREATE TABLE IF NOT EXISTS bankroll_profiles (
    id                       BIGINT  PRIMARY KEY DEFAULT nextval('bankroll_profiles_seq'),
    created_at               TIMESTAMP DEFAULT now(),
    updated_at               TIMESTAMP DEFAULT now(),
    profile_name             VARCHAR NOT NULL,
    bankroll_units           FLOAT   NOT NULL DEFAULT 100.0,
    base_unit_size           FLOAT   NOT NULL DEFAULT 1.0,
    max_daily_risk_units     FLOAT   NOT NULL DEFAULT 5.0,
    max_pick_risk_units      FLOAT   NOT NULL DEFAULT 1.5,
    max_parlay_risk_units    FLOAT   NOT NULL DEFAULT 0.5,
    max_same_league_units    FLOAT   NOT NULL DEFAULT 3.0,
    max_same_market_units    FLOAT   NOT NULL DEFAULT 3.0,
    max_same_team_units      FLOAT   NOT NULL DEFAULT 2.0,
    kelly_fraction           FLOAT   NOT NULL DEFAULT 0.25,
    min_edge_for_stake       FLOAT   NOT NULL DEFAULT 0.02,
    min_confidence_for_stake FLOAT   NOT NULL DEFAULT 0.52,
    is_active                BOOLEAN NOT NULL DEFAULT true,
    metadata_json            VARCHAR,
    UNIQUE (profile_name)
);

-- Per-pick stake recommendation (one row per pick_candidate_id)
CREATE TABLE IF NOT EXISTS stake_recommendations (
    id                      BIGINT PRIMARY KEY DEFAULT nextval('stake_recommendations_seq'),
    created_at              TIMESTAMP DEFAULT now(),
    pick_candidate_id       BIGINT,
    published_pick_id       BIGINT,
    fixture_id              BIGINT,
    provider_fixture_id     BIGINT,
    market_key              VARCHAR,
    selection               VARCHAR,
    league_id               BIGINT,
    team_home_id            BIGINT,
    team_away_id            BIGINT,
    odds                    FLOAT,
    p_model                 FLOAT,
    edge                    FLOAT,
    ev                      FLOAT,
    ev_adj                  FLOAT,
    strategy_score          FLOAT,
    strategy_recommendation VARCHAR,
    clv_percent             FLOAT,
    risk_score              FLOAT,
    correlation_score       FLOAT,
    kelly_full              FLOAT,
    kelly_fractional        FLOAT,
    recommended_units       FLOAT,
    stake_label             VARCHAR,
    rejection_reason        VARCHAR,
    metadata_json           VARCHAR,
    UNIQUE (pick_candidate_id)
);

-- Daily portfolio risk snapshot
CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
    id                      BIGINT PRIMARY KEY DEFAULT nextval('portfolio_risk_snapshots_seq'),
    created_at              TIMESTAMP DEFAULT now(),
    snapshot_date           DATE    NOT NULL,
    total_picks             INTEGER DEFAULT 0,
    total_recommended_units FLOAT   DEFAULT 0.0,
    total_daily_risk_units  FLOAT   DEFAULT 0.0,
    exposure_by_league_json VARCHAR,
    exposure_by_market_json VARCHAR,
    exposure_by_team_json   VARCHAR,
    correlated_groups_json  VARCHAR,
    portfolio_score         FLOAT,
    risk_level              VARCHAR,
    warnings_json           VARCHAR,
    metadata_json           VARCHAR,
    UNIQUE (snapshot_date)
);

-- Risk events log (over-exposure warnings, limits hit, etc.)
CREATE TABLE IF NOT EXISTS risk_events (
    id            BIGINT PRIMARY KEY DEFAULT nextval('risk_events_seq'),
    created_at    TIMESTAMP DEFAULT now(),
    event_type    VARCHAR NOT NULL,
    severity      VARCHAR NOT NULL DEFAULT 'low',
    entity_type   VARCHAR,
    entity_id     VARCHAR,
    message       VARCHAR,
    metadata_json VARCHAR
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_stake_rec_pick_candidate   ON stake_recommendations (pick_candidate_id);
CREATE INDEX IF NOT EXISTS idx_stake_rec_published_pick   ON stake_recommendations (published_pick_id);
CREATE INDEX IF NOT EXISTS idx_stake_rec_fixture          ON stake_recommendations (fixture_id);
CREATE INDEX IF NOT EXISTS idx_stake_rec_league           ON stake_recommendations (league_id);
CREATE INDEX IF NOT EXISTS idx_stake_rec_market           ON stake_recommendations (market_key);
CREATE INDEX IF NOT EXISTS idx_stake_rec_strategy_score   ON stake_recommendations (strategy_score);
CREATE INDEX IF NOT EXISTS idx_portfolio_date             ON portfolio_risk_snapshots (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_risk_level       ON portfolio_risk_snapshots (risk_level);
CREATE INDEX IF NOT EXISTS idx_risk_events_type           ON risk_events (event_type);
CREATE INDEX IF NOT EXISTS idx_risk_events_severity       ON risk_events (severity);
