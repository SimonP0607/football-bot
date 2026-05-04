-- 011_player_intelligence_schema.sql
-- Phase 11: Player Intelligence & Props Signals
-- All idempotent — safe to run multiple times.

-- ── player_fixture_stats ──────────────────────────────────────────────────────
-- Raw statistics per player per fixture, as returned by /fixtures/players.
CREATE TABLE IF NOT EXISTS player_fixture_stats (
    id                   BIGINT PRIMARY KEY,
    provider_fixture_id  BIGINT NOT NULL,
    fixture_id           BIGINT,
    league_id            BIGINT,
    season               INTEGER,
    team_id              BIGINT,
    player_id            BIGINT NOT NULL,
    player_name          VARCHAR,
    team_name            VARCHAR,
    position             VARCHAR,
    minutes              INTEGER,
    rating               FLOAT,
    captain              BOOLEAN DEFAULT false,
    substitute           BOOLEAN DEFAULT false,
    offsides             INTEGER DEFAULT 0,
    shots_total          INTEGER DEFAULT 0,
    shots_on             INTEGER DEFAULT 0,
    goals_total          INTEGER DEFAULT 0,
    goals_conceded       INTEGER DEFAULT 0,
    assists              INTEGER DEFAULT 0,
    saves                INTEGER DEFAULT 0,
    passes_total         INTEGER DEFAULT 0,
    passes_key           INTEGER DEFAULT 0,
    passes_accuracy      FLOAT,
    tackles_total        INTEGER DEFAULT 0,
    tackles_blocks       INTEGER DEFAULT 0,
    tackles_interceptions INTEGER DEFAULT 0,
    duels_total          INTEGER DEFAULT 0,
    duels_won            INTEGER DEFAULT 0,
    dribbles_attempts    INTEGER DEFAULT 0,
    dribbles_success     INTEGER DEFAULT 0,
    fouls_drawn          INTEGER DEFAULT 0,
    fouls_committed      INTEGER DEFAULT 0,
    cards_yellow         INTEGER DEFAULT 0,
    cards_red            INTEGER DEFAULT 0,
    penalty_won          INTEGER DEFAULT 0,
    penalty_committed    INTEGER DEFAULT 0,
    penalty_scored       INTEGER DEFAULT 0,
    penalty_missed       INTEGER DEFAULT 0,
    penalty_saved        INTEGER DEFAULT 0,
    source               VARCHAR DEFAULT 'api_football',
    synced_at            TIMESTAMP DEFAULT current_timestamp,
    raw_json             VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS player_fixture_stats_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pfs_player_fixture
    ON player_fixture_stats(player_id, provider_fixture_id);

CREATE INDEX IF NOT EXISTS idx_pfs_provider_fixture ON player_fixture_stats(provider_fixture_id);
CREATE INDEX IF NOT EXISTS idx_pfs_fixture_id       ON player_fixture_stats(fixture_id);
CREATE INDEX IF NOT EXISTS idx_pfs_player_id        ON player_fixture_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pfs_team_id          ON player_fixture_stats(team_id);
CREATE INDEX IF NOT EXISTS idx_pfs_league_season    ON player_fixture_stats(league_id, season);


-- ── player_season_profiles ────────────────────────────────────────────────────
-- Aggregated stats per player per team/league/season.
CREATE TABLE IF NOT EXISTS player_season_profiles (
    id             BIGINT PRIMARY KEY,
    player_id      BIGINT NOT NULL,
    player_name    VARCHAR,
    team_id        BIGINT,
    team_name      VARCHAR,
    league_id      BIGINT,
    season         INTEGER,
    position       VARCHAR,
    appearances    INTEGER DEFAULT 0,
    starts         INTEGER DEFAULT 0,
    total_minutes  INTEGER DEFAULT 0,
    avg_minutes    FLOAT,
    goals          INTEGER DEFAULT 0,
    assists        INTEGER DEFAULT 0,
    shots_total    INTEGER DEFAULT 0,
    shots_on       INTEGER DEFAULT 0,
    shots_on_rate  FLOAT,
    key_passes     INTEGER DEFAULT 0,
    fouls_drawn    INTEGER DEFAULT 0,
    fouls_committed INTEGER DEFAULT 0,
    yellow_cards   INTEGER DEFAULT 0,
    red_cards      INTEGER DEFAULT 0,
    avg_rating     FLOAT,
    last_updated   TIMESTAMP DEFAULT current_timestamp,
    raw_json       VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS player_season_profiles_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_psp_player_team_league_season
    ON player_season_profiles(player_id, team_id, league_id, season);

CREATE INDEX IF NOT EXISTS idx_psp_player_id   ON player_season_profiles(player_id);
CREATE INDEX IF NOT EXISTS idx_psp_team_id     ON player_season_profiles(team_id);
CREATE INDEX IF NOT EXISTS idx_psp_league      ON player_season_profiles(league_id, season);


-- ── player_recent_form ────────────────────────────────────────────────────────
-- Rolling-window form stats for players.
CREATE TABLE IF NOT EXISTS player_recent_form (
    id              BIGINT PRIMARY KEY,
    player_id       BIGINT NOT NULL,
    team_id         BIGINT,
    league_id       BIGINT,
    season          INTEGER,
    window_size     INTEGER DEFAULT 5,
    matches_count   INTEGER DEFAULT 0,
    avg_minutes     FLOAT,
    goals           INTEGER DEFAULT 0,
    assists         INTEGER DEFAULT 0,
    shots_total     INTEGER DEFAULT 0,
    shots_on        INTEGER DEFAULT 0,
    key_passes      INTEGER DEFAULT 0,
    fouls_drawn     INTEGER DEFAULT 0,
    fouls_committed INTEGER DEFAULT 0,
    cards_total     INTEGER DEFAULT 0,
    avg_rating      FLOAT,
    trend_label     VARCHAR,  -- 'hot'|'stable'|'declining'|'low_minutes'|'insufficient_data'
    updated_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE SEQUENCE IF NOT EXISTS player_recent_form_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_prf_player_team_league_window
    ON player_recent_form(player_id, team_id, league_id, season, window_size);

CREATE INDEX IF NOT EXISTS idx_prf_player_id ON player_recent_form(player_id);
CREATE INDEX IF NOT EXISTS idx_prf_team_id   ON player_recent_form(team_id);


-- ── player_prop_signals ───────────────────────────────────────────────────────
-- Informational prop-style signals for upcoming fixtures.
-- NOT official picks. Never displayed as betting recommendations.
CREATE TABLE IF NOT EXISTS player_prop_signals (
    id                   BIGINT PRIMARY KEY,
    created_at           TIMESTAMP DEFAULT current_timestamp,
    fixture_id           BIGINT,
    provider_fixture_id  BIGINT,
    league_id            BIGINT,
    season               INTEGER,
    team_id              BIGINT,
    player_id            BIGINT NOT NULL,
    player_name          VARCHAR,
    market_key           VARCHAR,  -- 'player_goal_signal' etc.
    signal_type          VARCHAR,  -- 'prop_signal'
    confidence_score     FLOAT,
    trend_score          FLOAT,
    availability_score   FLOAT,
    minutes_projection   FLOAT,
    reason_text          VARCHAR,
    risk_text            VARCHAR,
    status               VARCHAR DEFAULT 'observed',  -- 'observed'|'recommended'|'no_data'|'high_risk'
    metadata_json        VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS player_prop_signals_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pps_player_fixture_market
    ON player_prop_signals(player_id, provider_fixture_id, market_key);

CREATE INDEX IF NOT EXISTS idx_pps_fixture_id          ON player_prop_signals(fixture_id);
CREATE INDEX IF NOT EXISTS idx_pps_provider_fixture_id ON player_prop_signals(provider_fixture_id);
CREATE INDEX IF NOT EXISTS idx_pps_player_id           ON player_prop_signals(player_id);
CREATE INDEX IF NOT EXISTS idx_pps_team_id             ON player_prop_signals(team_id);
CREATE INDEX IF NOT EXISTS idx_pps_market_key          ON player_prop_signals(market_key);
CREATE INDEX IF NOT EXISTS idx_pps_created_at          ON player_prop_signals(created_at);
CREATE INDEX IF NOT EXISTS idx_pps_league_season       ON player_prop_signals(league_id, season);
