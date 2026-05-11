"""Phase 9: Conversational AI Router Service.

Classifies free-text messages into intents, extracts entities, routes to
the appropriate bot handler, and persists logs + user context in DuckDB.

Usage:
    result = classify_message("dame una combinada conservadora")
    # result = {"intent": "parlay", "confidence": 0.90, "args": {"parlay_legs": 2}, ...}
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Intent contract ────────────────────────────────────────────────────────────

_VALID_INTENTS = {
    "top_picks", "today_picks", "fixture_analysis", "team_lookup",
    "player_lookup", "league_list", "system_status", "value_metrics",
    "results", "performance", "parlay", "parlay_risk", "live",
    "follow_fixture", "help", "unknown",
    # Phase 11: Player Intelligence
    "player_stats", "player_props", "hot_players", "team_players", "player_form",
    # Phase 12: Market Intelligence
    "market_summary", "fixture_market", "clv_summary", "odds_movement", "bookmaker_coverage",
    # Phase 13: Strategy Learning
    "strategy_summary", "best_strategies", "weak_strategies",
    "strategy_detail", "why_pick_strategy", "strategy_learning_status",
    # Phase 14: Bankroll & Risk
    "bankroll_summary", "risk_summary", "stake_question",
    "exposure_question", "correlation_question", "bankroll_help",
    # Phase 15: Model Governance
    "governance_summary", "experiment_summary", "activation_readiness",
    "model_comparison", "why_not_activate", "safe_mode_help",
}

_EMPTY_ARGS = {
    "fixture_id": None,
    "team_query": None,
    "player_query": None,
    "parlay_legs": None,
    "risk_level": None,
    "days": None,
}

_SAFETY_TRIGGERS = [
    "apuesta todo", "all in", "pon todo", "mete todo", "apuesta fuerte",
    "hipoteca", "prestamo para apostar", "presta para apostar",
]

_SAFE_NOTE = (
    "⚠️ Los picks son informativos. Nunca apuestes más de lo que puedas perder. "
    "El sistema es una herramienta de análisis, no un consejo financiero."
)


def _make_result(
    intent: str,
    confidence: float,
    args: dict | None = None,
    needs_clarification: bool = False,
    clarification_question: str | None = None,
    safety_note: str | None = None,
) -> dict:
    return {
        "intent": intent,
        "confidence": confidence,
        "args": {**_EMPTY_ARGS, **(args or {})},
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "safety_note": safety_note,
    }


# ── Text normalization ─────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace."""
    return " ".join(text.lower().split())


def _has(text: str, *words: str) -> bool:
    """True if any word appears in text (whole word or substring match)."""
    return any(w in text for w in words)


def _extract_number(text: str) -> int | None:
    """Extract the first integer found in text (potential fixture_id)."""
    m = re.search(r"\b(\d{5,10})\b", text)
    if m:
        return int(m.group(1))
    return None


def _extract_days(text: str) -> int | None:
    """Extract 'ayer' → 1, 'semana' → 7, or explicit N días."""
    if "ayer" in text:
        return 1
    if "semana" in text:
        return 7
    if "mes" in text:
        return 30
    m = re.search(r"(\d+)\s*d[íi]as?", text)
    if m:
        return int(m.group(1))
    return None


def _extract_team_query(text: str) -> str | None:
    """Extract team name following trigger words."""
    patterns = [
        r"analiza[r]?\s+([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+)",
        r"equipo\s+([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+)",
        r"partido\s+(?:de\s+|del\s+)?([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+)",
        r"sigue\s+(?:a\s+)?([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+?)(?:\s+\d|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(",.?!")
            if name and len(name) > 2:
                return name
    return None


def _extract_player_query(text: str) -> str | None:
    """Extract player name following trigger words."""
    patterns = [
        r"jugador\s+([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+)",
        r"cómo\s+(?:viene|está|juega)\s+([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+?)(?:\s+en\s|$|\?)",
        r"como\s+(?:viene|esta|juega)\s+([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+?)(?:\s+en\s|$|\?)",
        r"stats?\s+(?:de\s+)?([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+)",
        r"forma\s+(?:de\s+)?([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s\-\.]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(",.?!")
            if name and len(name) > 2:
                return name
    return None


# ── Safety check ──────────────────────────────────────────────────────────────


def _check_safety(text: str) -> str | None:
    for trigger in _SAFETY_TRIGGERS:
        if trigger in text:
            return _SAFE_NOTE
    return None


# ── Rules engine ──────────────────────────────────────────────────────────────


def classify_with_rules(text: str, user_context: dict | None = None) -> dict:
    """Pure keyword-based intent classifier. No external API required.

    Returns a result dict matching the standard intent contract.
    """
    t = _norm(text)
    safety = _check_safety(t)

    # ── Parlay sub-intents first (more specific) ───────────────────────────────
    if _has(t, "riesgo") and _has(t, "combinada", "parlay", "múltiple", "multiple", "acumulador"):
        return _make_result("parlay_risk", 0.92, safety_note=safety)

    if _has(t, "combinada", "parlay", "múltiple", "multiple", "acumulador", "combo"):
        legs = None
        risk = None
        if _has(t, "conservadora", "segura", "2 leg", "doble"):
            legs, risk = 2, "conservadora"
            conf = 0.95
        elif _has(t, "balanceada", "balanceado", "moderada", "moderado", "3 leg", "triple"):
            legs, risk = 3, "balanceada"
            conf = 0.95
        elif _has(t, "agresiva", "arriesgada", "4 leg", "cuádruple", "cuadruple"):
            legs, risk = 4, "agresiva"
            conf = 0.95
        else:
            conf = 0.85
        return _make_result("parlay", conf,
                            args={"parlay_legs": legs, "risk_level": risk},
                            safety_note=safety)

    # ── Follow fixture ─────────────────────────────────────────────────────────
    if _has(t, "sigue", "seguir", "seguimiento", "track") and _extract_number(t):
        fid = _extract_number(t)
        return _make_result("follow_fixture", 0.95, args={"fixture_id": fid})

    # ── Live ───────────────────────────────────────────────────────────────────
    if _has(t, "en vivo", "live", "tiempo real", "jugando ahora", "partidos vivos"):
        return _make_result("live", 0.93, safety_note=safety)

    # ── Performance ───────────────────────────────────────────────────────────
    # Strategy-scoped ROI queries (liga/estrategia + mejor/peor + roi) go to strategy intents
    if _has(t, "roi") and _has(t, "liga", "ligas", "estrategia", "estrategias") and _has(t, "mejor", "mejores", "top"):
        return _make_result("best_strategies", 0.90)
    if _has(t, "rendimiento", "roi", "hit rate", "yield", "rentabilidad", "estadísticas", "estadisticas"):
        days = _extract_days(t)
        return _make_result("performance", 0.93, args={"days": days})

    # ── Results ───────────────────────────────────────────────────────────────
    if _has(t, "resultado", "liquidado", "resuelto", "ganó", "perdió", "gano", "perdio",
             "aciertos", "fallos", "picks ganados"):
        days = _extract_days(t)
        return _make_result("results", 0.92, args={"days": days})

    # ── System status ─────────────────────────────────────────────────────────
    if _has(t, "estado del sistema", "system status", "health", "api status",
             "salud del sistema", "cómo está el sistema", "como esta el sistema"):
        return _make_result("system_status", 0.95)
    if _has(t, "estado", "sistema") and _has(t, "api", "base de datos", "bd", "db", "sync"):
        return _make_result("system_status", 0.85)

    # ── Value metrics ─────────────────────────────────────────────────────────
    if _has(t, "value engine", "valor", "edge", "ev ", "expected value", "calidad del modelo",
             "calibración", "calibracion"):
        return _make_result("value_metrics", 0.88)

    # ── League list ───────────────────────────────────────────────────────────
    if _has(t, "liga", "ligas", "competición", "competicion", "competencias", "torneos",
             "activ") and _has(t, "qué", "que", "cuáles", "cuales", "muéstrame", "muestrame",
                               "ver", "lista"):
        return _make_result("league_list", 0.90)

    # ── Phase 12: Market Intelligence (before fixture_analysis to avoid "partido" collision) ──

    # clv_summary: CLV performance report
    if _has(t, "clv", "closing line value", "línea de cierre", "linea de cierre"):
        days = _extract_days(t)
        return _make_result("clv_summary", 0.95, args={"days": days})

    # fixture_market: market data for a specific fixture
    if _has(t, "mercado", "cuotas", "momios", "odds") and _extract_number(t):
        fid = _extract_number(t)
        return _make_result("fixture_market", 0.92, args={"fixture_id": fid})

    # odds_movement: steam, drift, reverse line
    if _has(t, "steam", "drift", "reverse line", "movimiento de cuotas",
             "movimiento de momios", "sharps", "sharp money",
             "cuotas bajaron", "cuotas subieron", "momios bajaron", "momios subieron"):
        fid = _extract_number(t)
        return _make_result("odds_movement", 0.90, args={"fixture_id": fid})

    # market_summary: general market overview
    if _has(t, "mercado", "inteligencia de mercado") and not _extract_number(t):
        return _make_result("market_summary", 0.85)

    # bookmaker_coverage: which bookmakers
    if _has(t, "bookmaker", "casa de apuestas", "casas de apuestas", "bet365",
             "pinnacle", "betfair", "1xbet", "cobertura de casas"):
        return _make_result("bookmaker_coverage", 0.88)

    # ── Phase 15: Model Governance (highest priority, before bankroll) ──────────

    # safe_mode_help: what is governance / safe mode / shadow mode
    if _has(t, "gobernanza", "governance", "como funciona la gobernanza",
             "que es la gobernanza", "modo shadow", "shadow mode", "modo seguro"):
        return _make_result("safe_mode_help", 0.95)
    if _has(t, "ayuda") and _has(t, "gobernanza", "experimentos", "activacion"):
        return _make_result("safe_mode_help", 0.90)

    # governance_summary: show governance dashboard
    if _has(t, "gobernanza") and _has(t, "resumen", "estado", "ver", "mostrar", "como esta",
                                       "dashboard", "informe"):
        return _make_result("governance_summary", 0.95)
    if _has(t, "gobernanza") and not _has(t, "ayuda", "help", "como funciona"):
        return _make_result("governance_summary", 0.85)

    # experiment_summary: show experiment results
    if _has(t, "experimentos", "experiment", "variantes", "variants") and _has(
        t, "resultado", "resultado", "ver", "mostrar", "estado", "resumen"
    ):
        return _make_result("experiment_summary", 0.95)
    if _has(t, "variant_strategy", "variant_bankroll", "variant_market", "variant_parlay",
             "baseline_current", "experimento activo"):
        return _make_result("experiment_summary", 0.92)

    # activation_readiness: is module ready to activate?
    if _has(t, "listo para activar", "puedo activar", "se puede activar",
             "readiness", "activation readiness", "preparado para activar"):
        return _make_result("activation_readiness", 0.95)
    if _has(t, "activar") and _has(t, "modulo", "módulo", "motor", "engine", "cuando"):
        return _make_result("activation_readiness", 0.90)

    # model_comparison: compare modules / variants
    if _has(t, "comparar modulos", "comparar variantes", "cual es mejor",
             "mejor variante", "diferencia entre variantes", "model comparison"):
        return _make_result("model_comparison", 0.92)
    if _has(t, "comparar") and _has(t, "strategy learning", "bankroll", "market clv", "parlay"):
        return _make_result("model_comparison", 0.88)

    # why_not_activate: explain why a module is not ready
    if _has(t, "por que no se activa", "por que no activa", "why not activate",
             "que le falta", "que necesita para activar", "que falta para activar"):
        return _make_result("why_not_activate", 0.95)
    if _has(t, "bloqueado") and _has(t, "activar", "modulo", "motor"):
        return _make_result("why_not_activate", 0.88)

    # ── Phase 14: Bankroll & Risk (before fixture_analysis) ──────────────────

    # bankroll_help: how bankroll engine works
    if _has(t, "bankroll engine", "motor de bankroll", "como funciona el bankroll",
             "que es el bankroll", "bankroll system"):
        return _make_result("bankroll_help", 0.95)
    if _has(t, "ayuda") and _has(t, "bankroll", "riesgo del portafolio", "stake engine"):
        return _make_result("bankroll_help", 0.90)

    # bankroll_summary: current bankroll status
    if _has(t, "bankroll") and _has(t, "como esta", "cuanto tengo", "estado", "saldo",
                                     "resumen", "cuanto hay", "mi bankroll", "ver bankroll"):
        return _make_result("bankroll_summary", 0.95)
    if _has(t, "bankroll") and not _has(t, "ayuda", "help", "como funciona"):
        return _make_result("bankroll_summary", 0.85)

    # risk_summary: portfolio risk / portfolio score
    if _has(t, "riesgo del portafolio", "riesgo del portfolio",
             "portafolio de riesgo", "portfolio de riesgo",
             "nivel de riesgo del portafolio", "portfolio score"):
        return _make_result("risk_summary", 0.95)
    if _has(t, "portafolio", "portfolio") and _has(t, "riesgo", "score", "nivel"):
        return _make_result("risk_summary", 0.90)

    # stake_question: how much to stake on a pick
    if _has(t, "cuanto deberia apostar", "cuanto apostar", "cuanto poner",
             "stake recomendado", "que stake", "stake recomiendan",
             "recomendacion de apuesta", "stake para"):
        return _make_result("stake_question", 0.95, safety_note=safety)
    if _has(t, "stake") and _has(t, "recomend", "cuanto", "para este", "pick"):
        return _make_result("stake_question", 0.88, safety_note=safety)

    # exposure_question: unit exposure by league/market/team
    if _has(t, "exposicion", "exposición", "exposure") and _has(
        t, "liga", "mercado", "equipo", "tengo", "cuanta", "por"
    ):
        return _make_result("exposure_question", 0.93)
    if _has(t, "exposicion por liga", "exposicion por mercado", "exposicion por equipo",
             "exposicion total"):
        return _make_result("exposure_question", 0.95)

    # correlation_question: correlated picks detection
    if _has(t, "correlacionados", "correlacion", "picks correlados",
             "hay correlacion", "picks correlacionados", "correlacion entre picks"):
        return _make_result("correlation_question", 0.95)
    if _has(t, "correlacion") and _has(t, "picks", "apuestas", "hoy", "actuales"):
        return _make_result("correlation_question", 0.88)

    # ── Phase 13: Strategy Learning (before fixture_analysis) ─────────────────

    # strategy_learning_status: feature status / config
    if _has(t, "aprendizaje estrategico", "strategy learning", "aprendizaje activo",
             "aprendizaje desactivado", "aprendizaje del bot", "como va el aprendizaje",
             "estado del aprendizaje"):
        return _make_result("strategy_learning_status", 0.95)
    if _has(t, "aprendizaje") and _has(t, "bot", "sistema", "estado", "activo", "va"):
        return _make_result("strategy_learning_status", 0.88)

    # best_strategies: top performing strategies (including league ROI queries)
    if _has(t, "estrategia", "estrategias", "liga", "ligas") and _has(
        t, "mejor", "mejores", "top", "fuerte", "fuertes", "promote", "mejor roi", "mas roi"
    ):
        return _make_result("best_strategies", 0.93)

    # weak_strategies: underperforming strategies
    if _has(t, "estrategia", "estrategias") and _has(t, "peor", "peores", "debil",
                                                      "debiles", "peligrosa", "avoid",
                                                      "reduce", "evitar"):
        return _make_result("weak_strategies", 0.93)

    # strategy_detail: specific strategy key
    if _has(t, "estrategia") and _has(t, "detalle", "clave", "key", "especifica", "especifico"):
        return _make_result("strategy_detail", 0.88)

    # why_pick_strategy: explain why a pick fits a strategy
    if _has(t, "por que", "porque", "why") and _has(t, "estrategia", "pick", "seleccion"):
        return _make_result("why_pick_strategy", 0.85)

    # strategy_summary: general strategy learning overview
    if _has(t, "estrategia", "estrategias") and not _has(t, "liga", "equipo", "jugador"):
        return _make_result("strategy_summary", 0.85)

    # ── Fixture / Team analysis ────────────────────────────────────────────────
    if _has(t, "analiza", "análisis", "analisis") or (
        _has(t, "partido", "fixture", "match") and not _has(t, "en vivo", "live")
    ):
        fid = _extract_number(t)
        team = _extract_team_query(t)
        if fid:
            return _make_result("fixture_analysis", 0.92, args={"fixture_id": fid})
        if team:
            return _make_result("fixture_analysis", 0.85, args={"team_query": team})
        return _make_result("fixture_analysis", 0.70,
                            needs_clarification=True,
                            clarification_question="¿Cuál equipo o fixture quieres analizar? Escribe el nombre del equipo o el ID del partido.")

    # ── Team lookup ───────────────────────────────────────────────────────────
    if _has(t, "equipo", "club", "team"):
        team = _extract_team_query(t) or text.strip()
        return _make_result("team_lookup", 0.88, args={"team_query": team})

    # ── Phase 11: Player Intelligence intents ─────────────────────────────────

    # player_props: signals for a match ("can score", "shots", "cards today")
    if _has(t, "prop", "señal", "señales") and _has(t, "jugador", "player", "partido", "fixture"):
        fid = _extract_number(t)
        team = _extract_team_query(t)
        return _make_result("player_props", 0.90, args={"fixture_id": fid, "team_query": team})
    if _has(t, "puede anotar", "puede meter", "puede rematar", "tiros hoy",
             "tarjetas probable", "jugadores clave") and _has(t, "partido", "hoy", "jugado"):
        fid = _extract_number(t)
        return _make_result("player_props", 0.87, args={"fixture_id": fid})

    # hot_players: trend / in-form players
    if _has(t, "caliente", "en racha", "en forma", "jugadores calientes",
             "playerhot", "hot player", "mejor forma", "mejor tendencia"):
        league_id = _extract_number(t)
        return _make_result("hot_players", 0.92, args={"fixture_id": league_id})

    # player_form: recent form for a specific player
    if _has(t, "forma reciente", "últimos partidos de", "tendencia de",
             "cómo ha jugado", "como ha jugado", "rendimiento de"):
        player = _extract_player_query(text) or text.strip()
        return _make_result("player_form", 0.90, args={"player_query": player})

    # player_stats: full stats profile for a player
    if _has(t, "cómo viene", "como viene", "cómo está", "como esta") and len(t) > 10:
        player = _extract_player_query(text)
        if player:
            return _make_result("player_stats", 0.92, args={"player_query": player})
    if _has(t, "estadísticas de", "estadisticas de", "stats de") and not _has(t, "equipo", "liga"):
        player = _extract_player_query(text) or text.strip()
        return _make_result("player_stats", 0.88, args={"player_query": player})

    # team_players: players for a team
    if _has(t, "jugadores de", "plantilla de", "quiénes son", "quienes son") and \
            _has(t, "jugador", "jugadores", "plantilla", "equipo"):
        team = _extract_team_query(text)
        return _make_result("team_players", 0.88, args={"team_query": team})

    # ── Player lookup ──────────────────────────────────────────────────────────
    if _has(t, "jugador", "player", "futbolista"):
        player = _extract_player_query(t) or text.strip()
        return _make_result("player_lookup", 0.88, args={"player_query": player})

    # ── Today picks ───────────────────────────────────────────────────────────
    if _has(t, "hoy") and _has(t, "pick", "recomienda", "sugiere", "publicados", "oficial"):
        return _make_result("today_picks", 0.93, safety_note=safety)

    # ── Top picks ─────────────────────────────────────────────────────────────
    if _has(t, "top", "mejor", "mejores", "recomienda", "recomiéndame", "sugerir",
             "dame picks", "dame el pick", "qué hay hoy", "dame todo"):
        return _make_result("top_picks", 0.90, safety_note=safety)
    if _has(t, "pick", "selección", "seleccion", "apuesta") and not _has(t, "sin", "no hay"):
        return _make_result("top_picks", 0.78, safety_note=safety)

    # ── Help ──────────────────────────────────────────────────────────────────
    if _has(t, "ayuda", "help", "comandos", "qué puedes", "que puedes", "cómo funciona",
             "como funciona", "instrucciones", "cómo usar", "como usar"):
        return _make_result("help", 0.93)

    # ── Unknown / too short ────────────────────────────────────────────────────
    if len(t) < 4:
        return _make_result("unknown", 0.10)

    # Fallback: check if it's likely a team/player name (capitalized unknown)
    words = text.strip().split()
    if 1 <= len(words) <= 4 and all(w[0].isupper() for w in words if w):
        return _make_result("team_lookup", 0.65, args={"team_query": text.strip()})

    return _make_result(
        "unknown", 0.30,
        needs_clarification=True,
        clarification_question="No entendí tu mensaje. Prueba: /top, /parlay, /estado, /ayuda o escribe el nombre de un equipo.",
    )


# ── Main classify function ────────────────────────────────────────────────────


def classify_message(text: str, user_context: dict | None = None) -> dict:
    """Classify a free-text message into an intent.

    Tries the external provider first (if configured), falls back to rules.
    """
    from app.services.ai_provider import classify_with_external_provider

    try:
        result = classify_with_external_provider(text)
        if result is not None:
            logger.debug("ai_router: used external provider for %r → %s", text[:40], result["intent"])
            return result
    except Exception as exc:
        logger.debug("ai_router: external provider error — %s", exc)

    return classify_with_rules(text, user_context)


# ── Entity resolution ──────────────────────────────────────────────────────────


def resolve_entities_from_text(text: str) -> dict:
    """Extract structured entities from text.

    Returns dict with keys: fixture_id, team_query, player_query, days.
    """
    t = _norm(text)
    return {
        "fixture_id":   _extract_number(t),
        "team_query":   _extract_team_query(text),
        "player_query": _extract_player_query(t),
        "days":         _extract_days(t),
    }


# ── DuckDB log + context ──────────────────────────────────────────────────────


def save_router_log(
    conn,
    user_id: int,
    raw_message: str,
    intent: str,
    confidence: float,
    handler_target: str,
    args: dict,
    response_status: str = "ok",
    error_message: str | None = None,
    provider: str = "rules",
) -> None:
    """Persist a router decision to ai_router_logs."""
    try:
        conn.execute(
            """
            INSERT INTO ai_router_logs
                (id, user_id, raw_message, detected_intent, confidence,
                 handler_target, args_json, response_status, error_message, provider)
            VALUES (nextval('ai_router_logs_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                user_id,
                raw_message[:500],
                intent,
                confidence,
                handler_target,
                json.dumps(args),
                response_status,
                error_message,
                provider,
            ],
        )
    except Exception as exc:
        logger.debug("save_router_log: failed — %s", exc)


def get_user_context(conn, user_id: int) -> dict | None:
    """Retrieve the user's conversation context from DuckDB."""
    try:
        row = conn.execute(
            "SELECT * FROM ai_user_context WHERE user_id = ?", [user_id]
        ).fetchone()
        if not row:
            return None
        cols = [
            "user_id", "last_intent", "last_fixture_id", "last_team_id",
            "last_player_id", "last_parlay_id", "last_market_key",
            "preferred_risk_level", "preferred_leagues_json", "updated_at", "metadata_json",
        ]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.debug("get_user_context: %s", exc)
        return None


def update_user_context(conn, user_id: int, data: dict) -> None:
    """Upsert user conversation context."""
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO ai_user_context
                (user_id, last_intent, last_fixture_id, last_team_id,
                 last_player_id, last_parlay_id, last_market_key,
                 preferred_risk_level, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                last_intent           = excluded.last_intent,
                last_fixture_id       = COALESCE(excluded.last_fixture_id, last_fixture_id),
                last_team_id          = COALESCE(excluded.last_team_id, last_team_id),
                last_player_id        = COALESCE(excluded.last_player_id, last_player_id),
                last_parlay_id        = COALESCE(excluded.last_parlay_id, last_parlay_id),
                last_market_key       = COALESCE(excluded.last_market_key, last_market_key),
                preferred_risk_level  = COALESCE(excluded.preferred_risk_level, preferred_risk_level),
                updated_at            = excluded.updated_at
            """,
            [
                user_id,
                data.get("last_intent"),
                data.get("last_fixture_id"),
                data.get("last_team_id"),
                data.get("last_player_id"),
                data.get("last_parlay_id"),
                data.get("last_market_key"),
                data.get("preferred_risk_level"),
                now,
            ],
        )
    except Exception as exc:
        logger.debug("update_user_context: %s", exc)


def get_router_status(conn) -> dict:
    """Return a status summary dict for /estado integration."""
    from app.core.config import settings
    try:
        total_logs = conn.execute("SELECT COUNT(*) FROM ai_router_logs").fetchone()[0]
        last_intent = conn.execute(
            "SELECT detected_intent FROM ai_router_logs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        unknown_count = conn.execute(
            "SELECT COUNT(*) FROM ai_router_logs WHERE detected_intent = 'unknown'"
        ).fetchone()[0]
        return {
            "enabled":       settings.ai_router_enabled,
            "provider":      settings.ai_router_provider,
            "total_queries": int(total_logs or 0),
            "last_intent":   last_intent[0] if last_intent else None,
            "unknown_count": int(unknown_count or 0),
        }
    except Exception as exc:
        return {
            "enabled":  settings.ai_router_enabled,
            "provider": settings.ai_router_provider,
            "error":    str(exc),
        }
