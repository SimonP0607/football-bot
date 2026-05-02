-- 009_ai_router_schema.sql
-- Phase 9: Conversational AI Router tables
-- All idempotent — safe to run multiple times (CREATE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS ai_router_logs (
    id              INTEGER PRIMARY KEY,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    user_id         BIGINT,
    raw_message     VARCHAR,
    detected_intent VARCHAR,
    confidence      DOUBLE,
    handler_target  VARCHAR,
    args_json       VARCHAR,
    response_status VARCHAR,
    error_message   VARCHAR,
    provider        VARCHAR DEFAULT 'rules',
    metadata_json   VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS ai_router_logs_seq START 1;

CREATE TABLE IF NOT EXISTS ai_user_context (
    user_id                BIGINT PRIMARY KEY,
    last_intent            VARCHAR,
    last_fixture_id        BIGINT,
    last_team_id           INTEGER,
    last_player_id         INTEGER,
    last_parlay_id         VARCHAR,
    last_market_key        VARCHAR,
    preferred_risk_level   VARCHAR,
    preferred_leagues_json VARCHAR,
    updated_at             TIMESTAMP DEFAULT current_timestamp,
    metadata_json          VARCHAR
);

CREATE TABLE IF NOT EXISTS ai_intent_examples (
    id           INTEGER PRIMARY KEY,
    intent_key   VARCHAR NOT NULL,
    example_text VARCHAR NOT NULL,
    language     VARCHAR DEFAULT 'es',
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT current_timestamp
);

CREATE SEQUENCE IF NOT EXISTS ai_intent_examples_seq START 1;

-- Seed example phrases for Spanish (idempotent via OR IGNORE)
INSERT OR IGNORE INTO ai_intent_examples (id, intent_key, example_text, language)
VALUES
    (1,  'top_picks',        'dame picks',                     'es'),
    (2,  'top_picks',        'qué recomiendas hoy',            'es'),
    (3,  'top_picks',        'mejores picks del día',          'es'),
    (4,  'today_picks',      'picks de hoy',                   'es'),
    (5,  'today_picks',      'picks oficiales de hoy',         'es'),
    (6,  'parlay',           'dame una combinada conservadora','es'),
    (7,  'parlay',           'combinada agresiva',             'es'),
    (8,  'parlay',           'arma un parlay balanceado',      'es'),
    (9,  'fixture_analysis', 'analiza bolivar',                'es'),
    (10, 'fixture_analysis', 'partido manchester united',      'es'),
    (11, 'system_status',    'estado del sistema',             'es'),
    (12, 'system_status',    'cómo está la api',               'es'),
    (13, 'performance',      'cómo va el rendimiento',         'es'),
    (14, 'performance',      'roi del sistema',                'es'),
    (15, 'results',          'qué pasó con los resultados',    'es'),
    (16, 'live',             'partidos en vivo',               'es'),
    (17, 'live',             'muéstrame picks live',           'es'),
    (18, 'follow_fixture',   'sigue el partido 1535267',       'es'),
    (19, 'league_list',      'qué ligas están activas',        'es'),
    (20, 'value_metrics',    'valor del value engine',         'es');
