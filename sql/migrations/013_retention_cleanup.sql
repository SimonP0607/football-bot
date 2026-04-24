-- 013: retention cleanup functions
-- Creates cleanup_retention() and cleanup_retention_preview() for Supabase Free.
-- Run ONCE in Supabase SQL Editor after applying 012.
-- Safe to re-run (CREATE OR REPLACE).

-- ── cleanup_retention ─────────────────────────────────────────────────────────
-- Deletes stale rows from all hot/warm/cache tables in dependency-safe order.
-- Returns one row per table with the count of deleted rows.
-- Never touches: competitions, competition_seasons, teams, venues,
--                ref_bookmakers, ref_bet_types, tracked_competitions, bot_users.

CREATE OR REPLACE FUNCTION cleanup_retention(
    p_fixtures_days        INT DEFAULT 3,
    p_odds_days            INT DEFAULT 3,
    p_context_days         INT DEFAULT 3,
    p_candidates_days      INT DEFAULT 3,
    p_published_picks_days INT DEFAULT 45,
    p_settlement_days      INT DEFAULT 90,
    p_sync_runs_days       INT DEFAULT 14,
    p_usage_days           INT DEFAULT 14,
    p_h2h_days             INT DEFAULT 30,
    p_team_metrics_days    INT DEFAULT 30,
    p_market_cache_days    INT DEFAULT 30,
    p_cron_history_days    INT DEFAULT 14
)
RETURNS TABLE (tbl TEXT, rows_deleted BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows   BIGINT;
    v_cutoff TIMESTAMPTZ;
BEGIN

    -- 1. pick_results: settled rows only — never delete pending
    v_cutoff := NOW() - (p_settlement_days || ' days')::INTERVAL;
    DELETE FROM pick_results
    WHERE result_status <> 'pending'
      AND created_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'pick_results'::TEXT, v_rows;

    -- 2. published_picks
    v_cutoff := NOW() - (p_published_picks_days || ' days')::INTERVAL;
    DELETE FROM published_picks
    WHERE published_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'published_picks'::TEXT, v_rows;

    -- 3. pick_candidates: only orphans (no remaining published_picks or pick_results)
    v_cutoff := NOW() - (p_candidates_days || ' days')::INTERVAL;
    DELETE FROM pick_candidates pc
    WHERE pc.created_at < v_cutoff
      AND NOT EXISTS (
          SELECT 1 FROM published_picks pp WHERE pp.pick_candidate_id = pc.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM pick_results pr WHERE pr.pick_candidate_id = pc.id
      );
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'pick_candidates'::TEXT, v_rows;

    -- 4. fixture_contexts
    v_cutoff := NOW() - (p_context_days || ' days')::INTERVAL;
    DELETE FROM fixture_contexts
    WHERE updated_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'fixture_contexts'::TEXT, v_rows;

    -- 5. odds_snapshots
    v_cutoff := NOW() - (p_odds_days || ' days')::INTERVAL;
    DELETE FROM odds_snapshots
    WHERE last_update < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'odds_snapshots'::TEXT, v_rows;

    -- 6. fixtures: only if no pick_candidates reference them (safe for picks history)
    v_cutoff := NOW() - (p_fixtures_days || ' days')::INTERVAL;
    DELETE FROM fixtures f
    WHERE f.kickoff_at < v_cutoff
      AND NOT EXISTS (
          SELECT 1 FROM pick_candidates pc WHERE pc.fixture_id = f.id
      );
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'fixtures'::TEXT, v_rows;

    -- 7. api_usage_snapshots (before api_sync_runs — FK dependency order)
    v_cutoff := NOW() - (p_usage_days || ' days')::INTERVAL;
    DELETE FROM api_usage_snapshots
    WHERE captured_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'api_usage_snapshots'::TEXT, v_rows;

    -- 8. api_sync_runs (only if no usage_snapshots remain — avoids FK violation)
    v_cutoff := NOW() - (p_sync_runs_days || ' days')::INTERVAL;
    DELETE FROM api_sync_runs asr
    WHERE asr.started_at < v_cutoff
      AND asr.status <> 'running'
      AND NOT EXISTS (
          SELECT 1 FROM api_usage_snapshots aus WHERE aus.sync_run_id = asr.id
      );
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'api_sync_runs'::TEXT, v_rows;

    -- 9. h2h_cache
    v_cutoff := NOW() - (p_h2h_days || ' days')::INTERVAL;
    DELETE FROM h2h_cache
    WHERE last_computed_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'h2h_cache'::TEXT, v_rows;

    -- 10. team_competition_metrics
    v_cutoff := NOW() - (p_team_metrics_days || ' days')::INTERVAL;
    DELETE FROM team_competition_metrics
    WHERE updated_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'team_competition_metrics'::TEXT, v_rows;

    -- 11. market_availability_cache
    v_cutoff := NOW() - (p_market_cache_days || ' days')::INTERVAL;
    DELETE FROM market_availability_cache
    WHERE checked_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN QUERY SELECT 'market_availability_cache'::TEXT, v_rows;

    -- 12. cron.job_run_details (optional — skipped if pg_cron is not installed)
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'cron' AND table_name = 'job_run_details'
    ) THEN
        v_cutoff := NOW() - (p_cron_history_days || ' days')::INTERVAL;
        EXECUTE 'DELETE FROM cron.job_run_details WHERE end_time < $1' USING v_cutoff;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        RETURN QUERY SELECT 'cron.job_run_details'::TEXT, v_rows;
    END IF;

END $$;


-- ── cleanup_retention_preview ─────────────────────────────────────────────────
-- Same parameters as cleanup_retention() but counts rows instead of deleting.
-- Use for dry-run previews — no data is modified.

CREATE OR REPLACE FUNCTION cleanup_retention_preview(
    p_fixtures_days        INT DEFAULT 3,
    p_odds_days            INT DEFAULT 3,
    p_context_days         INT DEFAULT 3,
    p_candidates_days      INT DEFAULT 3,
    p_published_picks_days INT DEFAULT 45,
    p_settlement_days      INT DEFAULT 90,
    p_sync_runs_days       INT DEFAULT 14,
    p_usage_days           INT DEFAULT 14,
    p_h2h_days             INT DEFAULT 30,
    p_team_metrics_days    INT DEFAULT 30,
    p_market_cache_days    INT DEFAULT 30,
    p_cron_history_days    INT DEFAULT 14
)
RETURNS TABLE (tbl TEXT, rows_to_delete BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    v_count  BIGINT;
    v_cutoff TIMESTAMPTZ;
BEGIN

    v_cutoff := NOW() - (p_settlement_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM pick_results
    WHERE result_status <> 'pending' AND created_at < v_cutoff;
    RETURN QUERY SELECT 'pick_results'::TEXT, v_count;

    v_cutoff := NOW() - (p_published_picks_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM published_picks WHERE published_at < v_cutoff;
    RETURN QUERY SELECT 'published_picks'::TEXT, v_count;

    v_cutoff := NOW() - (p_candidates_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM pick_candidates pc
    WHERE pc.created_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM published_picks pp WHERE pp.pick_candidate_id = pc.id)
      AND NOT EXISTS (SELECT 1 FROM pick_results pr WHERE pr.pick_candidate_id = pc.id);
    RETURN QUERY SELECT 'pick_candidates'::TEXT, v_count;

    v_cutoff := NOW() - (p_context_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM fixture_contexts WHERE updated_at < v_cutoff;
    RETURN QUERY SELECT 'fixture_contexts'::TEXT, v_count;

    v_cutoff := NOW() - (p_odds_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM odds_snapshots WHERE last_update < v_cutoff;
    RETURN QUERY SELECT 'odds_snapshots'::TEXT, v_count;

    v_cutoff := NOW() - (p_fixtures_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM fixtures f
    WHERE f.kickoff_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM pick_candidates pc WHERE pc.fixture_id = f.id);
    RETURN QUERY SELECT 'fixtures'::TEXT, v_count;

    v_cutoff := NOW() - (p_usage_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM api_usage_snapshots WHERE captured_at < v_cutoff;
    RETURN QUERY SELECT 'api_usage_snapshots'::TEXT, v_count;

    v_cutoff := NOW() - (p_sync_runs_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM api_sync_runs asr
    WHERE asr.started_at < v_cutoff
      AND asr.status <> 'running'
      AND NOT EXISTS (SELECT 1 FROM api_usage_snapshots aus WHERE aus.sync_run_id = asr.id);
    RETURN QUERY SELECT 'api_sync_runs'::TEXT, v_count;

    v_cutoff := NOW() - (p_h2h_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM h2h_cache WHERE last_computed_at < v_cutoff;
    RETURN QUERY SELECT 'h2h_cache'::TEXT, v_count;

    v_cutoff := NOW() - (p_team_metrics_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM team_competition_metrics WHERE updated_at < v_cutoff;
    RETURN QUERY SELECT 'team_competition_metrics'::TEXT, v_count;

    v_cutoff := NOW() - (p_market_cache_days || ' days')::INTERVAL;
    SELECT COUNT(*) INTO v_count FROM market_availability_cache WHERE checked_at < v_cutoff;
    RETURN QUERY SELECT 'market_availability_cache'::TEXT, v_count;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'cron' AND table_name = 'job_run_details'
    ) THEN
        v_cutoff := NOW() - (p_cron_history_days || ' days')::INTERVAL;
        EXECUTE 'SELECT COUNT(*) FROM cron.job_run_details WHERE end_time < $1'
        INTO v_count USING v_cutoff;
        RETURN QUERY SELECT 'cron.job_run_details'::TEXT, v_count;
    END IF;

END $$;
