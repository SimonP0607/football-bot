-- 001: schema base (superseded by 010 en instalaciones nuevas)

CREATE TABLE IF NOT EXISTS bot_users (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_user_id BIGINT      NOT NULL UNIQUE,
    username         TEXT,
    is_active        BOOLEAN     NOT NULL DEFAULT true,
    is_admin         BOOLEAN     NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leagues (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_league_id BIGINT      NOT NULL,
    name               TEXT        NOT NULL,
    country            TEXT,
    season             INTEGER,
    is_active          BOOLEAN     NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS teams (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_team_id BIGINT      NOT NULL,
    name             TEXT        NOT NULL,
    country          TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fixtures (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_fixture_id BIGINT      NOT NULL UNIQUE,
    league_id           BIGINT      REFERENCES leagues(id),
    home_team_id        BIGINT      REFERENCES teams(id),
    away_team_id        BIGINT      REFERENCES teams(id),
    kickoff_at          TIMESTAMPTZ NOT NULL,
    status              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fixture_id  BIGINT      REFERENCES fixtures(id),
    bookmaker   TEXT        NOT NULL,
    market      TEXT        NOT NULL,
    selection   TEXT        NOT NULL,
    odd         NUMERIC(10,4) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS predictions (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fixture_id          BIGINT      REFERENCES fixtures(id),
    market              TEXT        NOT NULL,
    recommended_pick    TEXT        NOT NULL,
    model_probability   NUMERIC(8,4) NOT NULL,
    implied_probability NUMERIC(8,4) NOT NULL,
    edge                NUMERIC(8,4) NOT NULL,
    confidence_score    NUMERIC(8,4) NOT NULL,
    argument_json       JSONB,
    is_publishable      BOOLEAN     NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prediction_results (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_id     BIGINT REFERENCES predictions(id),
    result_status     TEXT,
    profit_loss_units NUMERIC(10,4),
    evaluated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_logs (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    level      TEXT        NOT NULL,
    message    TEXT        NOT NULL,
    context    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
