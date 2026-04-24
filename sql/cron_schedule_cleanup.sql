-- Cron nocturno de retención para football-bot (pg_cron)
-- Ejecutar en: Supabase → SQL Editor
--
-- Zona horaria: Colombia (America/Bogota) = UTC-5
-- Horario elegido: 03:10 Bogota → 08:10 UTC
--
-- Prerequisito: migración 013_retention_cleanup.sql debe estar aplicada.
-- pg_cron debe estar habilitado en Extensions (Supabase Pro o configuración manual).

-- ── Ver extensiones disponibles (verificar pg_cron) ──────────────────────────
-- SELECT * FROM pg_extension WHERE extname = 'pg_cron';

-- ── Job nocturno de cleanup ───────────────────────────────────────────────────

SELECT cron.schedule(
    'football-bot-cleanup-nightly',
    '10 8 * * *',
    $$
    SELECT tbl, rows_deleted
    FROM cleanup_retention(
        3,    -- p_fixtures_days
        3,    -- p_odds_days
        3,    -- p_context_days
        3,    -- p_candidates_days
        45,   -- p_published_picks_days
        90,   -- p_settlement_days
        14,   -- p_sync_runs_days
        14,   -- p_usage_days
        30,   -- p_h2h_days
        30,   -- p_team_metrics_days
        30,   -- p_market_cache_days
        14    -- p_cron_history_days
    )
    $$
);

-- ── Job de ANALYZE post-cleanup (opcional) ────────────────────────────────────
-- Actualiza estadísticas del planner tras borrados masivos.
-- Corre 10 minutos después del cleanup (08:20 UTC = 03:20 Bogota).
-- Supabase Free activa autovacuum automáticamente — esto es opcional.

-- SELECT cron.schedule(
--     'football-bot-analyze-nightly',
--     '20 8 * * *',
--     $$
--     ANALYZE odds_snapshots;
--     ANALYZE fixture_contexts;
--     ANALYZE fixtures;
--     ANALYZE pick_candidates;
--     $$
-- );

-- ── Gestión de jobs ───────────────────────────────────────────────────────────

-- Ver todos los jobs activos:
-- SELECT jobid, jobname, schedule, command, active FROM cron.job ORDER BY jobid;

-- Ver historial de ejecuciones recientes:
-- SELECT jobid, job_pid, database, start_time, end_time, status, return_message
-- FROM cron.job_run_details
-- ORDER BY start_time DESC
-- LIMIT 20;

-- Desactivar un job (sin eliminarlo):
-- UPDATE cron.job SET active = false WHERE jobname = 'football-bot-cleanup-nightly';

-- Eliminar job definitivamente:
-- SELECT cron.unschedule('football-bot-cleanup-nightly');

-- ── Verificar manualmente sin cron ───────────────────────────────────────────

-- Preview (dry-run):
-- SELECT * FROM cleanup_retention_preview();

-- Ejecución real:
-- SELECT * FROM cleanup_retention();
