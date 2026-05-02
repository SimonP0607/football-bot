-- 004: entity catalog — teams, players, squads
-- All tables are idempotent (CREATE TABLE IF NOT EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS).
-- team_identity.provider_team_id matches the team_id used throughout history tables.

CREATE SEQUENCE IF NOT EXISTS seq_team_membership START 1;
CREATE SEQUENCE IF NOT EXISTS seq_squad_membership START 1;
CREATE SEQUENCE IF NOT EXISTS seq_entity_sync_run START 1;

-- Full team identity (clubs + national teams)
CREATE TABLE IF NOT EXISTS team_identity (
    provider_team_id  BIGINT      PRIMARY KEY,
    name              VARCHAR     NOT NULL,
    code              VARCHAR,
    country           VARCHAR,
    is_national       BOOLEAN     NOT NULL DEFAULT FALSE,
    logo              VARCHAR,
    founded           INTEGER,
    venue_id          BIGINT,
    venue_name        VARCHAR,
    venue_city        VARCHAR,
    venue_capacity    INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

-- Which teams appeared in which competitions each season
-- A club can appear in both its domestic league and a continental cup — both rows are valid.
CREATE TABLE IF NOT EXISTS team_season_membership (
    id                BIGINT      PRIMARY KEY DEFAULT nextval('seq_team_membership'),
    provider_team_id  BIGINT      NOT NULL,
    provider_league_id INTEGER     NOT NULL,
    season            INTEGER     NOT NULL,
    competition_type  VARCHAR,    -- domestic_league | cup | continental | international
    scope             VARCHAR     NOT NULL DEFAULT 'club',  -- club | national_team
    country           VARCHAR,
    source            VARCHAR,    -- api | manual
    created_at        TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS team_membership_uq
    ON team_season_membership (provider_team_id, provider_league_id, season);

-- Basic player identity (populated from /players/squads or /players)
CREATE TABLE IF NOT EXISTS player_identity (
    provider_player_id BIGINT     PRIMARY KEY,
    name               VARCHAR    NOT NULL,
    firstname          VARCHAR,
    lastname           VARCHAR,
    age                INTEGER,
    birth_date         DATE,
    birth_place        VARCHAR,
    birth_country      VARCHAR,
    nationality        VARCHAR,
    height             VARCHAR,
    weight             VARCHAR,
    injured            BOOLEAN    DEFAULT FALSE,
    photo              VARCHAR,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

-- Player-team-season association (squad membership)
CREATE TABLE IF NOT EXISTS squad_membership (
    id                 BIGINT     PRIMARY KEY DEFAULT nextval('seq_squad_membership'),
    provider_player_id BIGINT     NOT NULL,
    provider_team_id   BIGINT     NOT NULL,
    season             INTEGER,
    provider_league_id INTEGER,
    position           VARCHAR,
    number             INTEGER,
    source             VARCHAR,   -- squads_endpoint | players_endpoint
    created_at         TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS squad_membership_uq
    ON squad_membership (provider_player_id, provider_team_id, season, provider_league_id);

-- Audit trail for entity sync operations
CREATE TABLE IF NOT EXISTS entity_sync_runs (
    id                 BIGINT     PRIMARY KEY DEFAULT nextval('seq_entity_sync_run'),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    finished_at        TIMESTAMPTZ,
    mode               VARCHAR,   -- dry_run | execute
    leagues_requested  INTEGER    DEFAULT 0,
    teams_synced       INTEGER    DEFAULT 0,
    memberships_synced INTEGER    DEFAULT 0,
    players_synced     INTEGER    DEFAULT 0,
    squads_synced      INTEGER    DEFAULT 0,
    api_calls          INTEGER    DEFAULT 0,
    status             VARCHAR    DEFAULT 'running',  -- running | completed | failed
    error_message      VARCHAR
);
