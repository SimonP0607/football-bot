create table if not exists bot_users (
    id bigint generated always as identity primary key,
    telegram_user_id bigint not null unique,
    username text,
    is_active boolean not null default true,
    is_admin boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists leagues (
    id bigint generated always as identity primary key,
    provider_league_id bigint not null,
    name text not null,
    country text,
    season integer,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists teams (
    id bigint generated always as identity primary key,
    provider_team_id bigint not null,
    name text not null,
    country text,
    created_at timestamptz not null default now()
);

create table if not exists fixtures (
    id bigint generated always as identity primary key,
    provider_fixture_id bigint not null unique,
    league_id bigint references leagues(id),
    home_team_id bigint references teams(id),
    away_team_id bigint references teams(id),
    kickoff_at timestamptz not null,
    status text,
    created_at timestamptz not null default now()
);

create table if not exists odds_snapshots (
    id bigint generated always as identity primary key,
    fixture_id bigint references fixtures(id),
    bookmaker text not null,
    market text not null,
    selection text not null,
    odd numeric(10,4) not null,
    captured_at timestamptz not null default now()
);

create table if not exists predictions (
    id bigint generated always as identity primary key,
    fixture_id bigint references fixtures(id),
    market text not null,
    recommended_pick text not null,
    model_probability numeric(8,4) not null,
    implied_probability numeric(8,4) not null,
    edge numeric(8,4) not null,
    confidence_score numeric(8,4) not null,
    argument_json jsonb,
    is_publishable boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists prediction_results (
    id bigint generated always as identity primary key,
    prediction_id bigint references predictions(id),
    result_status text,
    profit_loss_units numeric(10,4),
    evaluated_at timestamptz
);

create table if not exists bot_logs (
    id bigint generated always as identity primary key,
    level text not null,
    message text not null,
    context jsonb,
    created_at timestamptz not null default now()
);