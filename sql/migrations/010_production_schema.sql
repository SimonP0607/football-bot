-- 010: production schema rebuild (3-layer architecture)
-- Run ONCE on a fresh Supabase project. Drops all old tables except bot_users.

-- ── Step 1: Drop old tables (reverse dependency order) ───────────────────────

DROP TABLE IF EXISTS market_availability_cache  CASCADE;
DROP TABLE IF EXISTS h2h_cache                  CASCADE;
DROP TABLE IF EXISTS team_competition_metrics   CASCADE;
DROP TABLE IF EXISTS published_picks            CASCADE;
DROP TABLE IF EXISTS pick_candidates            CASCADE;
DROP TABLE IF EXISTS prediction_results         CASCADE;
DROP TABLE IF EXISTS predictions                CASCADE;
DROP TABLE IF EXISTS odds_snapshots             CASCADE;
DROP TABLE IF EXISTS fixture_contexts           CASCADE;
DROP TABLE IF EXISTS fixtures                   CASCADE;
DROP TABLE IF EXISTS tracked_competitions       CASCADE;
DROP TABLE IF EXISTS competition_seasons        CASCADE;
DROP TABLE IF EXISTS competitions               CASCADE;
DROP TABLE IF EXISTS teams                      CASCADE;
DROP TABLE IF EXISTS venues                     CASCADE;
DROP TABLE IF EXISTS leagues                    CASCADE;
DROP TABLE IF EXISTS ref_bet_types              CASCADE;
DROP TABLE IF EXISTS ref_bookmakers             CASCADE;
DROP TABLE IF EXISTS api_usage_snapshots        CASCADE;
DROP TABLE IF EXISTS api_sync_runs              CASCADE;
DROP TABLE IF EXISTS bot_logs                   CASCADE;


-- ── Step 2: Layer A — Persistent catalog ─────────────────────────────────────

CREATE TABLE ref_bookmakers (
    id                    BIGSERIAL    PRIMARY KEY,
    provider_bookmaker_id INTEGER      NOT NULL,
    name                  TEXT         NOT NULL,
    is_active             BOOLEAN      NOT NULL DEFAULT true,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ref_bookmakers_provider_id_uq UNIQUE (provider_bookmaker_id)
);

CREATE TABLE ref_bet_types (
    id              BIGSERIAL    PRIMARY KEY,
    provider_bet_id INTEGER      NOT NULL,
    name            TEXT         NOT NULL,
    scope           TEXT         NOT NULL DEFAULT 'prematch',
    market_key      TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ref_bet_types_provider_scope_uq UNIQUE (provider_bet_id, scope)
);

CREATE TABLE competitions (
    id                 BIGSERIAL    PRIMARY KEY,
    provider_league_id INTEGER      NOT NULL,
    name               TEXT         NOT NULL,
    country            TEXT,
    type               TEXT,
    logo_url           TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT competitions_provider_id_uq UNIQUE (provider_league_id)
);

CREATE TABLE teams (
    id               BIGSERIAL    PRIMARY KEY,
    provider_team_id BIGINT       NOT NULL,
    name             TEXT         NOT NULL,
    country          TEXT,
    logo_url         TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT teams_provider_id_uq UNIQUE (provider_team_id)
);

CREATE TABLE venues (
    id                BIGSERIAL    PRIMARY KEY,
    provider_venue_id INTEGER      NOT NULL,
    name              TEXT         NOT NULL,
    city              TEXT,
    country           TEXT,
    capacity          INTEGER,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT venues_provider_id_uq UNIQUE (provider_venue_id)
);

CREATE TABLE competition_seasons (
    id             BIGSERIAL    PRIMARY KEY,
    competition_id BIGINT       NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
    season         INTEGER      NOT NULL,
    current        BOOLEAN      NOT NULL DEFAULT false,
    is_active      BOOLEAN      NOT NULL DEFAULT false,
    coverage       JSONB,
    season_start   DATE,
    season_end     DATE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT competition_seasons_comp_season_uq UNIQUE (competition_id, season)
);

CREATE TABLE tracked_competitions (
    id                    BIGSERIAL   PRIMARY KEY,
    competition_season_id BIGINT      NOT NULL REFERENCES competition_seasons(id) ON DELETE CASCADE,
    is_active             BOOLEAN     NOT NULL DEFAULT true,
    market_winner         BOOLEAN     NOT NULL DEFAULT true,
    market_btts           BOOLEAN     NOT NULL DEFAULT true,
    market_ou25           BOOLEAN     NOT NULL DEFAULT true,
    market_corners_ou     BOOLEAN     NOT NULL DEFAULT false,
    priority              SMALLINT    NOT NULL DEFAULT 5,
    notes                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tracked_competitions_season_uq UNIQUE (competition_season_id)
);


-- ── Step 3: Layer B — Hot storage ────────────────────────────────────────────

CREATE TABLE fixtures (
    id                  BIGSERIAL    PRIMARY KEY,
    provider_fixture_id BIGINT       NOT NULL,
    league_id           BIGINT       NOT NULL REFERENCES competition_seasons(id),
    home_team_id        BIGINT       NOT NULL REFERENCES teams(id),
    away_team_id        BIGINT       NOT NULL REFERENCES teams(id),
    venue_id            BIGINT       REFERENCES venues(id),
    kickoff_at          TIMESTAMPTZ  NOT NULL,
    status              TEXT         NOT NULL DEFAULT 'NS',
    status_short        TEXT,
    status_long         TEXT,
    elapsed             SMALLINT,
    timezone            TEXT,
    date_local          DATE,
    last_api_update     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT fixtures_provider_id_uq UNIQUE (provider_fixture_id)
);

CREATE TABLE fixture_contexts (
    id           BIGSERIAL    PRIMARY KEY,
    fixture_id   BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    context_json JSONB,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT fixture_contexts_fixture_uq UNIQUE (fixture_id)
);

CREATE TABLE odds_snapshots (
    id             BIGSERIAL     PRIMARY KEY,
    fixture_id     BIGINT        NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    bookmaker_id   INTEGER,
    bookmaker_name TEXT          NOT NULL,
    bet_id         INTEGER,
    market_key     TEXT          NOT NULL,
    selection      TEXT          NOT NULL,
    odd            NUMERIC(8,4)  NOT NULL,
    scope          TEXT          NOT NULL DEFAULT 'prematch',
    last_update    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT odds_snapshots_uq UNIQUE (fixture_id, bookmaker_name, market_key, selection, scope)
);

CREATE TABLE pick_candidates (
    id                  BIGSERIAL     PRIMARY KEY,
    fixture_id          BIGINT        NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    market_key          TEXT          NOT NULL,
    selection           TEXT          NOT NULL,
    model_probability   NUMERIC(6,4)  NOT NULL,
    implied_probability NUMERIC(6,4)  NOT NULL,
    edge                NUMERIC(6,4)  NOT NULL,
    confidence_score    NUMERIC(6,4)  NOT NULL,
    argument_json       JSONB,
    is_publishable      BOOLEAN       NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pick_candidates_fixture_market_uq UNIQUE (fixture_id, market_key, selection)
);

CREATE TABLE published_picks (
    id                BIGSERIAL    PRIMARY KEY,
    pick_candidate_id BIGINT       NOT NULL REFERENCES pick_candidates(id) ON DELETE CASCADE,
    published_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    channel           TEXT,
    message_text      TEXT,
    CONSTRAINT published_picks_candidate_channel_uq UNIQUE (pick_candidate_id, channel)
);

CREATE TABLE api_sync_runs (
    id               BIGSERIAL    PRIMARY KEY,
    phase            TEXT         NOT NULL,
    status           TEXT         NOT NULL DEFAULT 'running',
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    api_calls_made   INTEGER      NOT NULL DEFAULT 0,
    fixtures_synced  INTEGER      NOT NULL DEFAULT 0,
    leagues_synced   INTEGER      NOT NULL DEFAULT 0,
    odds_rows_synced INTEGER      NOT NULL DEFAULT 0,
    summary_json     JSONB,
    error_message    TEXT
);

CREATE TABLE api_usage_snapshots (
    id                  BIGSERIAL    PRIMARY KEY,
    sync_run_id         BIGINT       REFERENCES api_sync_runs(id),
    endpoint            TEXT,
    requests_limit      INTEGER,
    requests_remaining  INTEGER,
    minute_limit        INTEGER,
    minute_remaining    INTEGER,
    captured_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);


-- ── Step 4: Layer C — Analytical cache ───────────────────────────────────────

CREATE TABLE team_competition_metrics (
    id                    BIGSERIAL     PRIMARY KEY,
    team_id               BIGINT        NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    competition_season_id BIGINT        NOT NULL REFERENCES competition_seasons(id) ON DELETE CASCADE,
    form                  TEXT,
    avg_goals_scored      NUMERIC(4,2),
    avg_goals_conceded    NUMERIC(4,2),
    clean_sheets_pct      NUMERIC(5,2),
    raw_stats             JSONB,
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT team_metrics_team_season_uq UNIQUE (team_id, competition_season_id)
);

CREATE TABLE h2h_cache (
    id               BIGSERIAL    PRIMARY KEY,
    team_a_id        BIGINT       NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    team_b_id        BIGINT       NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    fixtures_json    JSONB,
    last_computed_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT h2h_cache_teams_uq UNIQUE (team_a_id, team_b_id)
);

CREATE TABLE market_availability_cache (
    id                    BIGSERIAL    PRIMARY KEY,
    competition_season_id BIGINT       NOT NULL REFERENCES competition_seasons(id) ON DELETE CASCADE,
    market_key            TEXT         NOT NULL,
    bookmaker_id          INTEGER      NOT NULL,
    is_available          BOOLEAN      NOT NULL DEFAULT false,
    sample_size           INTEGER      NOT NULL DEFAULT 0,
    checked_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT market_avail_season_market_bk_uq UNIQUE (competition_season_id, market_key, bookmaker_id)
);


-- ── Step 5: Indexes ───────────────────────────────────────────────────────────

CREATE INDEX idx_competition_seasons_current
    ON competition_seasons (competition_id, season)
    WHERE current = true;

CREATE INDEX idx_competition_seasons_active
    ON competition_seasons (is_active)
    WHERE is_active = true;

CREATE INDEX idx_fixtures_kickoff_at
    ON fixtures (kickoff_at);

CREATE INDEX idx_fixtures_league_kickoff
    ON fixtures (league_id, kickoff_at);

CREATE INDEX idx_fixtures_home_team
    ON fixtures (home_team_id);

CREATE INDEX idx_fixtures_away_team
    ON fixtures (away_team_id);

CREATE INDEX idx_odds_fixture_market
    ON odds_snapshots (fixture_id, market_key);

CREATE INDEX idx_odds_fixture_scope
    ON odds_snapshots (fixture_id, scope);

CREATE INDEX idx_pick_candidates_publishable
    ON pick_candidates (fixture_id, is_publishable)
    WHERE is_publishable = true;

CREATE INDEX idx_api_sync_runs_phase_started
    ON api_sync_runs (phase, started_at DESC);

CREATE INDEX idx_team_metrics_lookup
    ON team_competition_metrics (team_id, competition_season_id);
