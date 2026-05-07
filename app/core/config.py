import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Resolve .env relative to this file's location so it works regardless of the
# working directory from which the process was launched.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ENV_FILE, override=True)


def _int(key: str, default: int) -> int:
    v = os.getenv(key, "").strip()
    return int(v) if v else default


def _float(key: str, default: float) -> float:
    v = os.getenv(key, "").strip()
    return float(v) if v else default


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # 0 means "not configured yet" — bootstrap mode active (local only)
    telegram_allowed_user_id: int = _int("TELEGRAM_ALLOWED_USER_ID", 0)

    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")

    api_football_base_url: str = os.getenv(
        "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
    )
    api_football_key: str = os.getenv("API_FOOTBALL_KEY", "")

    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "America/Bogota")
    default_markets: str = os.getenv("DEFAULT_MARKETS", "1X2,OU25,BTTS")
    max_daily_picks: int = _int("MAX_DAILY_PICKS", 5)
    # edge = model_prob (fair consensus) - implied_prob (1/best_odd).
    # In efficient markets with 7-10% overround, fair probs are ~7pp below raw
    # implied. Edge of 0.0 means the best available odd is at or above consensus
    # fair break-even — a genuinely meaningful threshold. Use 0.05 only if you
    # want to target genuine soft lines (rare in liquid markets).
    min_edge: float = _float("MIN_EDGE", 0.0)
    # confidence_score == model_probability (fair consensus prob). 0.52 means
    # the market consensus considers this outcome a slight majority — the lowest
    # bar that avoids picking against the market. Set higher for more selectivity.
    min_confidence: float = _float("MIN_CONFIDENCE", 0.52)

    # Prematch guard: fixtures that kick off within this many minutes are skipped
    # when generating or displaying picks.
    prematch_min_lead_minutes: int = _int("PREMATCH_MIN_LEAD_MINUTES", 15)

    # Maximum number of picks shown by /top (separate from MAX_DAILY_PICKS cap).
    top_picks_limit: int = _int("TOP_PICKS_LIMIT", 3)

    # Sync configuration
    default_league_ids: str = os.getenv("DEFAULT_LEAGUE_IDS", "")
    default_season: int = _int("DEFAULT_SEASON", 0)
    league_seasons: str = os.getenv("LEAGUE_SEASONS", "")

    # Preferred bookmaker for odds selection (optional).
    # When set, the system favours this bookmaker's odds when available.
    # Use the bookmaker name as returned by /odds/bookmakers (e.g. "Bet365").
    preferred_bookmaker: str = os.getenv("PREFERRED_BOOKMAKER", "")

    # Preferred bookmaker ID (numeric). Takes priority over preferred_bookmaker
    # name when set.  Find the ID from /odds/bookmakers or ref_bookmakers table.
    preferred_bookmaker_id: int = _int("PREFERRED_BOOKMAKER_ID", 0)

    # ── Retención de datos ────────────────────────────────────────────────────
    # Hot tier (TTL corto — 3 días por defecto)
    retention_fixtures_days: int = _int("RETENTION_FIXTURES_DAYS", 3)
    retention_odds_days: int = _int("RETENTION_ODDS_DAYS", 3)
    retention_context_days: int = _int("RETENTION_CONTEXT_DAYS", 3)
    retention_candidates_days: int = _int("RETENTION_CANDIDATES_DAYS", 3)
    # Warm tier
    retention_published_picks_days: int = _int("RETENTION_PUBLISHED_PICKS_DAYS", 45)
    retention_settlement_days: int = _int("RETENTION_SETTLEMENT_DAYS", 90)
    retention_sync_runs_days: int = _int("RETENTION_SYNC_RUNS_DAYS", 14)
    retention_usage_days: int = _int("RETENTION_USAGE_DAYS", 14)
    # Cache tier
    retention_h2h_days: int = _int("RETENTION_H2H_DAYS", 30)
    retention_team_metrics_days: int = _int("RETENTION_TEAM_METRICS_DAYS", 30)
    retention_market_cache_days: int = _int("RETENTION_MARKET_CACHE_DAYS", 30)
    # Cron history
    retention_cron_history_days: int = _int("RETENTION_CRON_HISTORY_DAYS", 14)
    # Archivado local (desactivado por defecto)
    local_archive_enabled: bool = (
        os.getenv("LOCAL_ARCHIVE_ENABLED", "false").lower() == "true"
    )
    local_archive_dir: str = os.getenv("LOCAL_ARCHIVE_DIR", "./data/archive")
    local_archive_before_delete: bool = (
        os.getenv("LOCAL_ARCHIVE_BEFORE_DELETE", "false").lower() == "true"
    )

    # ── Base histórica local (DuckDB) ─────────────────────────────────────────
    local_db_path: str = os.getenv("LOCAL_DB_PATH", "./data/local/football_history.duckdb")

    # ── Value Engine — integración controlada offline→producción ──────────────
    # Modo de operación:
    #   off    — comportamiento actual intacto, no usa DuckDB
    #   shadow — calcula métricas pero no cambia decisiones
    #   assist — filtra y reordena candidatos por value score
    value_engine_mode: str = os.getenv("VALUE_ENGINE_MODE", "off").strip()
    value_engine_enabled: bool = (
        os.getenv("VALUE_ENGINE_ENABLED", "false").strip().lower() == "true"
    )
    value_engine_local_db_path: str = os.getenv(
        "VALUE_ENGINE_LOCAL_DB_PATH", "./data/local/football_history.duckdb"
    ).strip()
    value_engine_min_quality: float = _float("VALUE_ENGINE_MIN_QUALITY", 0.55)
    value_engine_min_edge: float = _float("VALUE_ENGINE_MIN_EDGE", 0.03)
    value_engine_min_ev_adj: float = _float("VALUE_ENGINE_MIN_EV_ADJ", 0.01)
    value_engine_require_odds: bool = (
        os.getenv("VALUE_ENGINE_REQUIRE_ODDS", "true").strip().lower() == "true"
    )
    value_engine_max_picks_per_league: int = _int("VALUE_ENGINE_MAX_PICKS_PER_LEAGUE", 3)
    value_engine_fallback_to_current: bool = (
        os.getenv("VALUE_ENGINE_FALLBACK_TO_CURRENT", "true").strip().lower() == "true"
    )

    # ── Phase 5: Availability-Aware Value Engine ──────────────────────────────
    # Master switch: if false, availability is never loaded and quality_score is
    # never adjusted. Defaults to true so the feature is active once availability
    # data exists in DuckDB, but only penalizes when coverage='known'.
    value_engine_use_availability: bool = (
        os.getenv("VALUE_ENGINE_USE_AVAILABILITY", "true").strip().lower() == "true"
    )
    # Maximum total net penalty applied to quality_score (cap).
    value_engine_availability_max_penalty: float = _float(
        "VALUE_ENGINE_AVAILABILITY_MAX_PENALTY", 0.06
    )
    # Penalty by modeled-team impact level.
    value_engine_availability_high_penalty: float = _float(
        "VALUE_ENGINE_AVAILABILITY_HIGH_PENALTY", 0.06
    )
    value_engine_availability_medium_penalty: float = _float(
        "VALUE_ENGINE_AVAILABILITY_MEDIUM_PENALTY", 0.03
    )
    value_engine_availability_low_penalty: float = _float(
        "VALUE_ENGINE_AVAILABILITY_LOW_PENALTY", 0.01
    )
    # Conservative boost when the OPPONENT of the modeled team has injuries.
    value_engine_availability_opponent_high_boost: float = _float(
        "VALUE_ENGINE_AVAILABILITY_OPPONENT_HIGH_BOOST", 0.02
    )
    value_engine_availability_opponent_medium_boost: float = _float(
        "VALUE_ENGINE_AVAILABILITY_OPPONENT_MEDIUM_BOOST", 0.01
    )
    # If true, candidates where modeled-team impact='high' and quality_after <
    # value_engine_min_quality are hard-rejected by the Value Engine.
    # Off by default — availability is informational, not a gate.
    value_engine_reject_high_availability_risk: bool = (
        os.getenv("VALUE_ENGINE_REJECT_HIGH_AVAILABILITY_RISK", "false").strip().lower() == "true"
    )

    # ── Phase 6: Prematch Intelligence & Odds Movement ───────────────────────
    prematch_intelligence_enabled: bool = (
        os.getenv("PREMATCH_INTELLIGENCE_ENABLED", "true").strip().lower() == "true"
    )
    # Default window for upcoming fixtures (hours before kickoff to include).
    prematch_refresh_hours: int = _int("PREMATCH_REFRESH_HOURS", 6)
    # How close to kickoff (minutes) before we attempt to fetch confirmed lineups.
    prematch_lineups_window_minutes: int = _int("PREMATCH_LINEUPS_WINDOW_MINUTES", 120)
    # API call budget cap for a single prematch sync run.
    prematch_max_requests: int = _int("PREMATCH_MAX_REQUESTS", 500)
    # Implied-probability thresholds for movement strength classification.
    prematch_odds_movement_high: float   = _float("PREMATCH_ODDS_MOVEMENT_HIGH", 0.05)
    prematch_odds_movement_medium: float = _float("PREMATCH_ODDS_MOVEMENT_MEDIUM", 0.025)
    # Value Engine adjustments driven by prematch signals.
    prematch_penalty_high_drift: float   = _float("PREMATCH_PENALTY_HIGH_DRIFT", 0.04)
    prematch_penalty_medium_drift: float = _float("PREMATCH_PENALTY_MEDIUM_DRIFT", 0.02)
    prematch_boost_supporting_move: float = _float("PREMATCH_BOOST_SUPPORTING_MOVE", 0.01)
    # If true, automatically reject picks with high-severity prematch drift.
    prematch_reject_high_risk: bool = (
        os.getenv("PREMATCH_REJECT_HIGH_RISK", "false").strip().lower() == "true"
    )

    # ── Phase 7: Live Monitoring ──────────────────────────────────────────────
    # Master switch — if false, live_monitor_service never runs.
    live_monitor_enabled: bool = (
        os.getenv("LIVE_MONITOR_ENABLED", "true").strip().lower() == "true"
    )
    # How many hours before kickoff to start tracking a fixture.
    live_monitor_hours_before: float = _float("LIVE_MONITOR_HOURS_BEFORE", 0.5)
    # How many hours after kickoff to keep polling (safety net for long matches).
    live_monitor_hours_after: float = _float("LIVE_MONITOR_HOURS_AFTER", 3.0)
    # Maximum API calls per monitor run.
    live_monitor_max_requests: int = _int("LIVE_MONITOR_MAX_REQUESTS", 50)
    # Seconds between polls in --loop mode.
    live_monitor_interval_seconds: int = _int("LIVE_MONITOR_INTERVAL_SECONDS", 60)
    # Minimum match elapsed (minutes) before state-change notifications are sent.
    live_monitor_min_elapsed_notify: int = _int("LIVE_MONITOR_MIN_ELAPSED_NOTIFY", 15)
    # If true, auto-settle finished fixtures during monitor run.
    live_monitor_auto_settle: bool = (
        os.getenv("LIVE_MONITOR_AUTO_SETTLE", "true").strip().lower() == "true"
    )
    # If true, send Telegram push notifications for state changes.
    live_monitor_notify: bool = (
        os.getenv("LIVE_MONITOR_NOTIFY", "false").strip().lower() == "true"
    )

    # ── Phase 8: Smart Parlay Engine ──────────────────────────────────────────
    parlay_engine_enabled: bool = (
        os.getenv("PARLAY_ENGINE_ENABLED", "false").strip().lower() == "true"
    )
    # Maximum legs per parlay (capped at 4 for risk control).
    parlay_max_legs: int = _int("PARLAY_MAX_LEGS", 4)
    # Minimum edge for the whole parlay (joint_prob - implied_prob).
    parlay_min_edge: float = _float("PARLAY_MIN_EDGE", 0.03)
    # Minimum expected value (EV = joint_prob * total_odds - 1).
    parlay_min_ev: float = _float("PARLAY_MIN_EV", 0.02)
    # Minimum average confidence score across legs.
    parlay_min_confidence: float = _float("PARLAY_MIN_CONFIDENCE", 0.55)
    # Maximum allowed correlation score (0=none, 1=full).
    parlay_max_correlation: float = _float("PARLAY_MAX_CORRELATION", 0.45)
    # Maximum allowed risk score.
    parlay_max_risk: float = _float("PARLAY_MAX_RISK", 0.60)
    # Allow two legs from the same fixture (experimental, default off).
    parlay_allow_same_fixture: bool = (
        os.getenv("PARLAY_ALLOW_SAME_FIXTURE", "false").strip().lower() == "true"
    )
    # Maximum picks from the same league in one parlay.
    parlay_max_same_league: int = _int("PARLAY_MAX_SAME_LEAGUE", 2)
    # Maximum recommended parlays to save per day.
    parlay_max_per_day: int = _int("PARLAY_MAX_PER_DAY", 5)
    # Default stake in units for ROI calculations.
    parlay_default_stake_units: float = _float("PARLAY_DEFAULT_STAKE_UNITS", 0.25)

    # ── Phase 9: Conversational AI Router ────────────────────────────────────
    # Master switch — if false, text messages are not processed by the router.
    ai_router_enabled: bool = (
        os.getenv("AI_ROUTER_ENABLED", "false").strip().lower() == "true"
    )
    # Provider: "rules" (no external API) or "openai" (optional).
    ai_router_provider: str = os.getenv("AI_ROUTER_PROVIDER", "rules").strip()
    # External model name (e.g. "gpt-4o-mini"). Only used when provider=openai.
    ai_router_model: str = os.getenv("AI_ROUTER_MODEL", "").strip()
    # Max seconds to wait for external provider response before falling back.
    ai_router_timeout_seconds: int = _int("AI_ROUTER_TIMEOUT_SECONDS", 8)
    # Max tokens for external provider response.
    ai_router_max_tokens: int = _int("AI_ROUTER_MAX_TOKENS", 500)
    # Minimum confidence threshold (below this, ask for clarification).
    ai_router_min_confidence: float = _float("AI_ROUTER_MIN_CONFIDENCE", 0.60)
    # Allow parlay-related routing.
    ai_router_allow_parlay: bool = (
        os.getenv("AI_ROUTER_ALLOW_PARLAY", "true").strip().lower() == "true"
    )
    # Allow live-monitoring routing.
    ai_router_allow_live: bool = (
        os.getenv("AI_ROUTER_ALLOW_LIVE", "true").strip().lower() == "true"
    )
    # Allow free-text explanations (e.g. "¿qué es el edge?").
    ai_router_allow_explanations: bool = (
        os.getenv("AI_ROUTER_ALLOW_EXPLANATIONS", "true").strip().lower() == "true"
    )
    # Log all queries to DuckDB ai_router_logs table.
    ai_router_log_queries: bool = (
        os.getenv("AI_ROUTER_LOG_QUERIES", "true").strip().lower() == "true"
    )
    # Safe mode: always prepend responsible gambling disclaimer to parlay responses.
    ai_router_safe_mode: bool = (
        os.getenv("AI_ROUTER_SAFE_MODE", "true").strip().lower() == "true"
    )

    # ── Phase 10: Scheduler & Proactive Alerts ───────────────────────────────
    # Master switch — if false, no jobs are registered on the JobQueue.
    scheduler_enabled: bool = (
        os.getenv("SCHEDULER_ENABLED", "false").strip().lower() == "true"
    )
    scheduler_timezone: str = os.getenv("SCHEDULER_TIMEZONE", "America/Bogota").strip()
    # Daily sync job
    scheduler_daily_sync_enabled: bool = (
        os.getenv("SCHEDULER_DAILY_SYNC_ENABLED", "true").strip().lower() == "true"
    )
    scheduler_daily_sync_time: str = os.getenv("SCHEDULER_DAILY_SYNC_TIME", "06:30").strip()
    # Prematch refresh job
    scheduler_prematch_enabled: bool = (
        os.getenv("SCHEDULER_PREMATCH_ENABLED", "true").strip().lower() == "true"
    )
    # Comma-separated minutes before kickoff to trigger a prematch refresh (e.g. 180,90,30)
    scheduler_prematch_windows: str = os.getenv("SCHEDULER_PREMATCH_WINDOWS", "180,90,30").strip()
    scheduler_prematch_max_fixtures: int = _int("SCHEDULER_PREMATCH_MAX_FIXTURES", 40)
    # Live monitor job
    scheduler_live_monitor_enabled: bool = (
        os.getenv("SCHEDULER_LIVE_MONITOR_ENABLED", "true").strip().lower() == "true"
    )
    scheduler_live_interval_seconds: int = _int("SCHEDULER_LIVE_INTERVAL_SECONDS", 60)
    # Settlement job
    scheduler_settlement_enabled: bool = (
        os.getenv("SCHEDULER_SETTLEMENT_ENABLED", "true").strip().lower() == "true"
    )
    scheduler_settlement_time: str = os.getenv("SCHEDULER_SETTLEMENT_TIME", "23:00").strip()
    # Daily report job
    scheduler_daily_report_enabled: bool = (
        os.getenv("SCHEDULER_DAILY_REPORT_ENABLED", "true").strip().lower() == "true"
    )
    scheduler_report_time: str = os.getenv("SCHEDULER_REPORT_TIME", "07:00").strip()
    # API budget guard — max API calls the scheduler can use per day
    scheduler_api_budget_daily: int = _int("SCHEDULER_API_BUDGET_DAILY", 50)
    # If true, send Telegram notifications for completed jobs and alerts
    scheduler_notify_alerts: bool = (
        os.getenv("SCHEDULER_NOTIFY_ALERTS", "true").strip().lower() == "true"
    )
    # Phase 11: Player Intelligence scheduler settings
    scheduler_player_stats_enabled: bool = (
        os.getenv("SCHEDULER_PLAYER_STATS_ENABLED", "false").strip().lower() == "true"
    )
    scheduler_player_stats_time: str = os.getenv("SCHEDULER_PLAYER_STATS_TIME", "23:30").strip()
    scheduler_player_stats_lookback_days: int = _int("SCHEDULER_PLAYER_STATS_LOOKBACK_DAYS", 2)
    scheduler_player_signals_enabled: bool = (
        os.getenv("SCHEDULER_PLAYER_SIGNALS_ENABLED", "true").strip().lower() == "true"
    )

    # ── Phase 12: Market Intelligence, Odds History & CLV Engine ─────────────
    market_intelligence_enabled: bool = (
        os.getenv("MARKET_INTELLIGENCE_ENABLED", "false").strip().lower() == "true"
    )
    market_intelligence_use_live_odds: bool = (
        os.getenv("MARKET_INTELLIGENCE_USE_LIVE_ODDS", "false").strip().lower() == "true"
    )
    market_intelligence_min_movement: float = _float("MARKET_INTELLIGENCE_MIN_MOVEMENT", 0.025)
    market_intelligence_clv_neutral_band: float = _float("MARKET_INTELLIGENCE_CLV_NEUTRAL_BAND", 0.005)
    market_intelligence_default_bookmaker_priority: str = os.getenv(
        "MARKET_INTELLIGENCE_DEFAULT_BOOKMAKER_PRIORITY", "8,6,11,1"
    ).strip()
    market_intelligence_max_requests_per_run: int = _int("MARKET_INTELLIGENCE_MAX_REQUESTS_PER_RUN", 500)
    # Scheduler jobs for market intelligence
    scheduler_market_enabled: bool = (
        os.getenv("SCHEDULER_MARKET_ENABLED", "false").strip().lower() == "true"
    )
    scheduler_market_opening_time: str = os.getenv("SCHEDULER_MARKET_OPENING_TIME", "08:00").strip()
    scheduler_market_prematch_hours: int = _int("SCHEDULER_MARKET_PREMATCH_HOURS", 6)
    scheduler_market_closing_minutes: int = _int("SCHEDULER_MARKET_CLOSING_MINUTES", 15)
    scheduler_clv_time: str = os.getenv("SCHEDULER_CLV_TIME", "23:45").strip()

    # ── Phase 13: CLV Learning Loop & Strategy Scoring ───────────────────────
    # Master switch — if false, strategy learning never runs and VE is unaffected.
    strategy_learning_enabled: bool = (
        os.getenv("STRATEGY_LEARNING_ENABLED", "false").strip().lower() == "true"
    )
    # If false (default), strategy metadata is added to VE result but picks are NOT changed.
    # If true, promote/reduce/avoid signals can adjust quality_score within caps below.
    strategy_learning_use_for_selection: bool = (
        os.getenv("STRATEGY_LEARNING_USE_FOR_SELECTION", "false").strip().lower() == "true"
    )
    # Minimum sample_size before a strategy recommendation is trusted for selection.
    strategy_learning_min_sample: int = _int("STRATEGY_LEARNING_MIN_SAMPLE", 50)
    # Strategy score threshold for "promote" recommendation (max boost applied).
    strategy_learning_score_promote: float = _float("STRATEGY_LEARNING_SCORE_PROMOTE", 75.0)
    # Strategy score threshold below which "reduce" recommendation is applied.
    strategy_learning_score_reduce: float = _float("STRATEGY_LEARNING_SCORE_REDUCE", 40.0)
    # Maximum quality_score penalty applied for "reduce"/"avoid" strategies.
    strategy_learning_max_penalty: float = _float("STRATEGY_LEARNING_MAX_PENALTY", 0.08)
    # Maximum quality_score boost applied for "promote" strategies.
    strategy_learning_max_boost: float = _float("STRATEGY_LEARNING_MAX_BOOST", 0.05)
    # Scheduler job settings for strategy learning
    scheduler_strategy_learning_enabled: bool = (
        os.getenv("SCHEDULER_STRATEGY_LEARNING_ENABLED", "false").strip().lower() == "true"
    )
    scheduler_strategy_learning_time: str = os.getenv("SCHEDULER_STRATEGY_LEARNING_TIME", "00:30").strip()
    scheduler_strategy_learning_days: int = _int("SCHEDULER_STRATEGY_LEARNING_DAYS", 30)

    # ── Phase 14: Bankroll, Stake Sizing & Risk Portfolio Engine ─────────────
    # Master switch — if false, bankroll engine never runs.
    bankroll_engine_enabled: bool = (
        os.getenv("BANKROLL_ENGINE_ENABLED", "false").strip().lower() == "true"
    )
    # If false (default), bankroll metadata added but picks NOT changed.
    # If true, can reduce priority of high-risk picks (never increases them).
    bankroll_use_for_selection: bool = (
        os.getenv("BANKROLL_USE_FOR_SELECTION", "false").strip().lower() == "true"
    )
    bankroll_default_units: float = _float("BANKROLL_DEFAULT_UNITS", 100.0)
    bankroll_base_unit_size: float = _float("BANKROLL_BASE_UNIT_SIZE", 1.0)
    bankroll_kelly_fraction: float = _float("BANKROLL_KELLY_FRACTION", 0.25)
    bankroll_max_pick_units: float = _float("BANKROLL_MAX_PICK_UNITS", 1.5)
    bankroll_max_daily_units: float = _float("BANKROLL_MAX_DAILY_UNITS", 5.0)
    bankroll_max_parlay_units: float = _float("BANKROLL_MAX_PARLAY_UNITS", 0.5)
    bankroll_min_edge: float = _float("BANKROLL_MIN_EDGE", 0.02)
    bankroll_min_confidence: float = _float("BANKROLL_MIN_CONFIDENCE", 0.52)
    bankroll_reduce_low_sample: bool = (
        os.getenv("BANKROLL_REDUCE_LOW_SAMPLE", "true").strip().lower() == "true"
    )
    bankroll_block_avoid_strategy: bool = (
        os.getenv("BANKROLL_BLOCK_AVOID_STRATEGY", "true").strip().lower() == "true"
    )
    scheduler_bankroll_enabled: bool = (
        os.getenv("SCHEDULER_BANKROLL_ENABLED", "false").strip().lower() == "true"
    )
    scheduler_bankroll_time: str = os.getenv("SCHEDULER_BANKROLL_TIME", "09:30").strip()

    # ── Política de ingestión histórica (Phase 3) ─────────────────────────────
    # Máximo de temporadas cerradas a conservar por liga en DuckDB.
    history_max_closed_seasons: int = _int("HISTORY_MAX_CLOSED_SEASONS", 4)
    # Si True, la primera vez que se supere el máximo se omite el borrado.
    history_skip_prune_on_first_rollover: bool = (
        os.getenv("HISTORY_SKIP_PRUNE_ON_FIRST_ROLLOVER", "true").lower() == "true"
    )
    # Fracción mínima de fixtures en estado terminal para considerar la temporada cerrada.
    history_min_terminal_fraction: float = _float("HISTORY_MIN_TERMINAL_FRACTION", 0.95)

    def __post_init__(self) -> None:
        # Strip whitespace to catch copy-paste errors (trailing newlines, spaces)
        self.api_football_key = self.api_football_key.strip()
        self.supabase_key = self.supabase_key.strip()
        self.supabase_url = self.supabase_url.strip()
        self.telegram_bot_token = self.telegram_bot_token.strip()

        _placeholder = "<COMPLETAR>"

        def _is_missing(val: str) -> bool:
            return not val or _placeholder in val

        missing: list[str] = []

        if _is_missing(self.telegram_bot_token):
            missing.append("TELEGRAM_BOT_TOKEN")
        if _is_missing(self.supabase_url):
            missing.append("SUPABASE_URL")
        if _is_missing(self.supabase_key):
            missing.append("SUPABASE_KEY")
        if _is_missing(self.api_football_key):
            missing.append("API_FOOTBALL_KEY")

        # TELEGRAM_ALLOWED_USER_ID is only required in production.
        # In local/dev mode a missing value (0) activates bootstrap mode instead
        # of crashing, so the owner can run /id to discover their numeric ID.
        if self.app_env == "production" and not self.telegram_allowed_user_id:
            missing.append("TELEGRAM_ALLOWED_USER_ID")

        if missing:
            raise ValueError(
                f"Variables de entorno requeridas no configuradas: {', '.join(missing)}"
            )

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def is_bootstrap_mode(self) -> bool:
        """True when the owner has not yet set TELEGRAM_ALLOWED_USER_ID."""
        return self.telegram_allowed_user_id == 0

    @property
    def is_local(self) -> bool:
        return self.app_env != "production"

    @property
    def masked_token(self) -> str:
        """Return bot token with the secret part hidden — safe for logs."""
        token = self.telegram_bot_token
        if not token or ":" not in token:
            return "(not set)"
        bot_id, secret = token.split(":", 1)
        return f"{bot_id}:{'*' * min(len(secret), 8)}..."

    @property
    def masked_supabase_key(self) -> str:
        key = self.supabase_key
        if not key:
            return "(not set)"
        return key[:8] + "..." if len(key) > 8 else "***"

    @property
    def masked_api_key(self) -> str:
        key = self.api_football_key
        if not key:
            return "(not set)"
        return "..." + key[-4:] if len(key) >= 4 else "***"

    @property
    def markets_list(self) -> list[str]:
        return [m.strip() for m in self.default_markets.split(",") if m.strip()]

    @property
    def league_ids_list(self) -> list[int]:
        """Parse DEFAULT_LEAGUE_IDS into a list of ints. Empty if not configured."""
        return [
            int(x.strip())
            for x in self.default_league_ids.split(",")
            if x.strip().isdigit()
        ]

    @property
    def league_seasons_map(self) -> dict[int, int]:
        """Parse LEAGUE_SEASONS into a {league_id: season} dict.

        Format: "39:2025,140:2025,253:2026"
        """
        result: dict[int, int] = {}
        for entry in self.league_seasons.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            league_str, season_str = entry.split(":", 1)
            if league_str.strip().isdigit() and season_str.strip().isdigit():
                result[int(league_str.strip())] = int(season_str.strip())
        return result


settings = Settings()
