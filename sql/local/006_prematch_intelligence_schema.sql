-- Phase 6: Prematch Intelligence & Odds Movement
-- Tables for tracking odds movement, fixture alerts, and lineup status.
-- All tables are idempotent (CREATE IF NOT EXISTS / ON CONFLICT DO UPDATE).

CREATE SEQUENCE IF NOT EXISTS seq_prematch_odds_movement START 1;
CREATE TABLE IF NOT EXISTS prematch_odds_movement (
    id                   BIGINT  PRIMARY KEY DEFAULT nextval('seq_prematch_odds_movement'),
    fixture_id           BIGINT,
    provider_fixture_id  BIGINT  NOT NULL,
    league_id            INT,
    market_key           TEXT    NOT NULL,
    selection            TEXT    NOT NULL,
    bookmaker            TEXT    NOT NULL DEFAULT '',
    opening_odds         DOUBLE  NOT NULL,
    current_odds         DOUBLE  NOT NULL,
    best_odds            DOUBLE,
    implied_opening      DOUBLE,
    implied_current      DOUBLE,
    odds_delta           DOUBLE,
    implied_delta        DOUBLE,
    movement_direction   TEXT,
    movement_strength    TEXT,
    captured_at          TEXT    NOT NULL,
    source               TEXT    DEFAULT 'supabase',
    UNIQUE (provider_fixture_id, market_key, selection, bookmaker)
);

CREATE SEQUENCE IF NOT EXISTS seq_prematch_fixture_alerts START 1;
CREATE TABLE IF NOT EXISTS prematch_fixture_alerts (
    id                   BIGINT  PRIMARY KEY DEFAULT nextval('seq_prematch_fixture_alerts'),
    fixture_id           BIGINT,
    provider_fixture_id  BIGINT  NOT NULL,
    league_id            INT,
    alert_type           TEXT    NOT NULL,
    severity             TEXT    NOT NULL DEFAULT 'low',
    title                TEXT    NOT NULL,
    message              TEXT,
    related_market       TEXT    DEFAULT '',
    related_selection    TEXT    DEFAULT '',
    metadata_json        TEXT,
    created_at           TEXT    NOT NULL,
    UNIQUE (provider_fixture_id, alert_type, related_market, related_selection)
);

CREATE SEQUENCE IF NOT EXISTS seq_prematch_lineup_status START 1;
CREATE TABLE IF NOT EXISTS prematch_lineup_status (
    id                   BIGINT  PRIMARY KEY DEFAULT nextval('seq_prematch_lineup_status'),
    fixture_id           BIGINT,
    provider_fixture_id  BIGINT  NOT NULL UNIQUE,
    home_team_id         INT,
    away_team_id         INT,
    home_formation       TEXT,
    away_formation       TEXT,
    lineups_available    BOOLEAN DEFAULT FALSE,
    lineups_confirmed    BOOLEAN DEFAULT FALSE,
    home_missing_count   INT     DEFAULT 0,
    away_missing_count   INT     DEFAULT 0,
    home_impact          TEXT    DEFAULT 'unknown',
    away_impact          TEXT    DEFAULT 'unknown',
    captured_at          TEXT    NOT NULL,
    metadata_json        TEXT
);
