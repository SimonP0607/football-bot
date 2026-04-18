-- seed_tracked_competitions.sql
-- Run AFTER Phase B (sync_reference.py --phase b) for all leagues.
-- Safe to re-run: ON CONFLICT DO UPDATE refreshes tier and priority.
--
-- sync_tier values:
--   tier_1_daily    = full enrichment every day (top domestic + major continental)
--   tier_2_matchday = full enrichment only on matchdays (cups + secondary leagues)
--   tier_3_light    = fixtures + odds only, no standings/stats (qualifying rounds, minor cups)
--
-- priority: 1 = highest (processed first in reports), 10 = lowest

-- ── Helper macro ─────────────────────────────────────────────────────────────
-- Each block: find the current competition_season for a provider_league_id,
-- then upsert into tracked_competitions.

-- ═══════════════════════════════════════════════════════════════════════════
-- TIER 1 — Full sync every day
-- ═══════════════════════════════════════════════════════════════════════════

-- 2 · UEFA Champions League
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'UEFA Champions League'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 2 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 3 · UEFA Europa League
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'UEFA Europa League'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 3 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 39 · Premier League (England)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'Premier League'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 39 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 61 · Ligue 1 (France)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'Ligue 1'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 61 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 71 · Brasileirao Serie A (Brazil)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'Brasileirao Serie A'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 71 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 78 · Bundesliga (Germany)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'Bundesliga'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 78 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 88 · Eredivisie (Netherlands)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 2, 'tier_1_daily', 'Eredivisie'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 88 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 2, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 94 · Primeira Liga (Portugal)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 2, 'tier_1_daily', 'Primeira Liga'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 94 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 2, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 98 · J1 League (Japan)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 2, 'tier_1_daily', 'J1 League'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 98 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 2, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 128 · Liga Profesional Argentina
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 2, 'tier_1_daily', 'Liga Profesional Argentina'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 128 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 2, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 135 · Serie A (Italy)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'Serie A'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 135 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 140 · La Liga (Spain)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 1, 'tier_1_daily', 'La Liga'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 140 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 1, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 40 · Championship (England)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 2, 'tier_1_daily', 'EFL Championship'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 40 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 2, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 144 · Jupiler Pro League (Belgium)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 3, 'tier_1_daily', 'Jupiler Pro League'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 144 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 3, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- 239 · Primera A (Colombia)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 2, 'tier_1_daily', 'Primera A Colombia'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 239 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 2, sync_tier = 'tier_1_daily',
    notes = EXCLUDED.notes;

-- ═══════════════════════════════════════════════════════════════════════════
-- TIER 2 — Full enrichment on matchdays only
-- ═══════════════════════════════════════════════════════════════════════════

-- 11 · CONMEBOL Sudamericana
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'CONMEBOL Sudamericana'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 11 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 13 · CONMEBOL Libertadores
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 3, 'tier_2_matchday', 'CONMEBOL Libertadores'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 13 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 3, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 16 · CONCACAF Champions League
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'CONCACAF Champions League'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 16 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 17 · AFC Champions League Elite
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'AFC Champions League Elite'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 17 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 22 · CONCACAF Gold Cup
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'CONCACAF Gold Cup'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 22 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 32 · World Cup Qualification Europe
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'WC Qualification Europe'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 32 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 34 · World Cup Qualification South America
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'WC Qualification CONMEBOL'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 34 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 45 · FA Cup (England)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'FA Cup'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 45 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 62 · Ligue 2 (France)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'Ligue 2'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 62 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 66 · Coupe de France
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Coupe de France'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 66 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 73 · Copa Do Brasil
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'Copa Do Brasil'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 73 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 79 · 2. Bundesliga (Germany)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', '2. Bundesliga'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 79 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 81 · DFB Pokal (Germany)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'DFB Pokal'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 81 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 103 · Eliteserien (Norway)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Eliteserien'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 103 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 106 · Ekstraklasa (Poland)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Ekstraklasa'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 106 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 113 · Allsvenskan (Sweden)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Allsvenskan'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 113 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 119 · Superliga (Denmark)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Superliga Denmark'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 119 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 130 · Copa Argentina
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Copa Argentina'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 130 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 137 · Coppa Italia
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'Coppa Italia'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 137 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 141 · Segunda División (Spain)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'Segunda Division'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 141 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 143 · Copa del Rey (Spain)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 4, 'tier_2_matchday', 'Copa del Rey'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 143 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 4, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- 241 · Copa Colombia
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 5, 'tier_2_matchday', 'Copa Colombia'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 241 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 5, sync_tier = 'tier_2_matchday',
    notes = EXCLUDED.notes;

-- ═══════════════════════════════════════════════════════════════════════════
-- TIER 3 — Fixtures + odds only (minimal requests)
-- ═══════════════════════════════════════════════════════════════════════════

-- 29 · World Cup Qualification Africa
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'WC Qualification Africa'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 29 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 30 · World Cup Qualification Asia
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'WC Qualification Asia'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 30 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 31 · World Cup Qualification CONCACAF
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'WC Qualification CONCACAF'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 31 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 95 · Segunda Liga (Portugal)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 6, 'tier_3_light', 'Segunda Liga Portugal'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 95 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 6, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 96 · Taca de Portugal
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'Taca de Portugal'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 96 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 108 · Polish Cup
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'Polish Cup'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 108 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 115 · Svenska Cupen (Sweden)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'Svenska Cupen'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 115 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 121 · DBU Pokalen (Denmark)
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'DBU Pokalen'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 121 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;

-- 147 · Belgian Cup
INSERT INTO tracked_competitions
    (competition_season_id, is_active, market_winner, market_btts, market_ou25,
     market_corners_ou, priority, sync_tier, notes)
SELECT cs.id, true, true, true, true, false, 7, 'tier_3_light', 'Belgian Cup'
FROM competition_seasons cs JOIN competitions c ON c.id = cs.competition_id
WHERE c.provider_league_id = 147 AND cs.current = true
ON CONFLICT (competition_season_id) DO UPDATE SET
    is_active = true, priority = 7, sync_tier = 'tier_3_light',
    notes = EXCLUDED.notes;
