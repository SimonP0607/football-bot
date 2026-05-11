"""Phase 9: Conversational handler for free-text Telegram messages.

Captures text messages that are NOT commands, classifies them using the
AI router service, and dispatches to the appropriate existing handler.

Activated only when AI_ROUTER_ENABLED=true in .env.
Falls back silently when disabled.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html
from app.core.config import settings

logger = logging.getLogger(__name__)

_CLARIFICATION_FOOTER = "\n\nO usa /menu para ver todas las opciones."
_UNKNOWN_TEXT = (
    "No entendí tu mensaje. Prueba escribir:\n"
    "· <i>\"dame picks\"</i>\n"
    "· <i>\"combinada conservadora\"</i>\n"
    "· <i>\"estado del sistema\"</i>\n"
    "· <i>\"analiza Fluminense\"</i>\n\n"
    "O usa /ayuda para ver todos los comandos."
)


@require_auth
async def conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages using the AI router."""
    if not settings.ai_router_enabled:
        return  # feature disabled — silently ignore

    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    if not text:
        return

    user_id = update.effective_user.id
    logger.info("conversation: user_id=%s text=%r", user_id, text[:80])

    # Load DuckDB connection (best-effort, don't block on failure)
    conn = None
    user_ctx = None
    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        conn = get_local_db()
        init_schema(conn)
        from app.services.ai_router_service import get_user_context
        user_ctx = get_user_context(conn, user_id)
    except Exception as exc:
        logger.debug("conversation: DuckDB unavailable — %s", exc)

    # Classify the message
    from app.services.ai_router_service import classify_message
    result = classify_message(text, user_ctx)

    intent     = result["intent"]
    confidence = result["confidence"]
    args       = result["args"]
    safety     = result.get("safety_note")
    needs_cl   = result.get("needs_clarification", False)
    cl_question = result.get("clarification_question")

    logger.info("conversation: intent=%s conf=%.2f user_id=%s", intent, confidence, user_id)

    # Send safety note immediately if present
    if safety and settings.ai_router_safe_mode:
        await message.reply_html(safety)

    # Below confidence threshold → ask for clarification
    if confidence < settings.ai_router_min_confidence or needs_cl:
        q = cl_question or "¿Puedes ser más específico?"
        await message.reply_html(q + _CLARIFICATION_FOOTER)
        _log(conn, user_id, text, intent, confidence, "clarification", args)
        return

    # Dispatch to the appropriate handler
    handler_target = "unknown"
    try:
        handler_target = await _route(intent, args, update, context)
    except Exception as exc:
        logger.error("conversation: routing error intent=%s — %s", intent, exc, exc_info=True)
        await send_html(message, "Error al procesar tu solicitud. Usa /estado para diagnosticar.")
        _log(conn, user_id, text, intent, confidence, "error", args, error=str(exc))
        return

    # Persist log and update user context
    _log(conn, user_id, text, intent, confidence, handler_target, args)
    _update_ctx(conn, user_id, intent, args)


async def _route(intent: str, args: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Dispatch intent to the correct handler. Returns handler_target string."""
    from app.bot.handlers.top import top_handler
    from app.bot.handlers.hoy import hoy_handler
    from app.bot.handlers.estado import estado_handler
    from app.bot.handlers.rendimiento import rendimiento_handler
    from app.bot.handlers.resultados import resultados_handler
    from app.bot.handlers.valor import valor_handler
    from app.bot.handlers.live import live_handler
    from app.bot.handlers.ligas import ligas_handler
    from app.bot.handlers.parlay import parlay_handler
    from app.bot.handlers.ayuda import ayuda_handler

    if intent == "top_picks":
        context.args = []
        await top_handler(update, context)
        return "top_handler"

    elif intent == "today_picks":
        context.args = []
        await hoy_handler(update, context)
        return "hoy_handler"

    elif intent == "system_status":
        context.args = []
        await estado_handler(update, context)
        return "estado_handler"

    elif intent == "performance":
        context.args = []
        await rendimiento_handler(update, context)
        return "rendimiento_handler"

    elif intent == "results":
        context.args = []
        await resultados_handler(update, context)
        return "resultados_handler"

    elif intent == "value_metrics":
        context.args = []
        await valor_handler(update, context)
        return "valor_handler"

    elif intent == "live":
        if not settings.ai_router_allow_live:
            await update.message.reply_html("El módulo live no está habilitado.")
            return "live_disabled"
        context.args = []
        await live_handler(update, context)
        return "live_handler"

    elif intent == "follow_fixture":
        fid = args.get("fixture_id")
        if fid:
            from app.bot.handlers.seguimiento import seguimiento_handler
            context.args = [str(fid)]
            await seguimiento_handler(update, context)
            return "seguimiento_handler"
        else:
            await update.message.reply_html(
                "¿Qué fixture quieres seguir? Escribe el ID del partido. "
                "Ejemplo: <code>sigue 1535267</code>"
            )
            return "clarification_follow"

    elif intent == "league_list":
        context.args = []
        await ligas_handler(update, context)
        return "ligas_handler"

    elif intent == "parlay":
        if not settings.ai_router_allow_parlay:
            await update.message.reply_html("El motor de parlays no está habilitado.")
            return "parlay_disabled"
        legs = args.get("parlay_legs")
        context.args = [str(legs)] if legs else []
        await parlay_handler(update, context)
        return "parlay_handler"

    elif intent == "parlay_risk":
        context.args = ["riesgo"]
        await parlay_handler(update, context)
        return "parlay_risk_handler"

    elif intent == "fixture_analysis":
        fid = args.get("fixture_id")
        team = args.get("team_query")
        if fid:
            from app.bot.handlers.partido import partido_handler
            context.args = [str(fid)]
            await partido_handler(update, context)
            return "partido_handler"
        elif team:
            from app.bot.handlers.equipo import equipo_handler
            context.args = team.split()
            await equipo_handler(update, context)
            return "equipo_handler"
        else:
            await update.message.reply_html(
                "¿Qué equipo o partido quieres analizar? "
                "Escribe el nombre del equipo o usa <code>/partido &lt;ID&gt;</code>."
            )
            return "clarification_analysis"

    elif intent == "team_lookup":
        from app.bot.handlers.equipo import equipo_handler
        team = args.get("team_query") or ""
        context.args = team.split() if team else []
        await equipo_handler(update, context)
        return "equipo_handler"

    elif intent == "player_lookup":
        from app.bot.handlers.equipo import jugador_handler
        player = args.get("player_query") or ""
        context.args = player.split() if player else []
        await jugador_handler(update, context)
        return "jugador_handler"

    elif intent == "player_stats":
        from app.bot.handlers.jugador_stats import jugadorstats_handler
        player = args.get("player_query") or ""
        context.args = player.split() if player else []
        await jugadorstats_handler(update, context)
        return "jugadorstats_handler"

    elif intent == "player_form":
        from app.bot.handlers.jugador_stats import jugadorstats_handler
        player = args.get("player_query") or ""
        context.args = player.split() if player else []
        await jugadorstats_handler(update, context)
        return "jugadorstats_handler"

    elif intent == "player_props":
        from app.bot.handlers.jugador_stats import props_handler
        fid = args.get("fixture_id")
        context.args = [str(fid)] if fid else []
        await props_handler(update, context)
        return "props_handler"

    elif intent == "hot_players":
        from app.bot.handlers.jugador_stats import playerhot_handler
        fid = args.get("fixture_id")  # re-used for league_id
        context.args = [str(fid)] if fid else []
        await playerhot_handler(update, context)
        return "playerhot_handler"

    elif intent == "team_players":
        from app.bot.handlers.equipo import equipo_handler
        team = args.get("team_query") or ""
        context.args = team.split() if team else []
        await equipo_handler(update, context)
        return "equipo_handler"

    elif intent == "fixture_market":
        from app.bot.handlers.mercado import mercado_handler
        fid = args.get("fixture_id")
        context.args = [str(fid)] if fid else []
        await mercado_handler(update, context)
        return "mercado_handler"

    elif intent == "clv_summary":
        from app.bot.handlers.mercado import clv_handler
        days = args.get("days")
        context.args = [str(days)] if days else []
        await clv_handler(update, context)
        return "clv_handler"

    elif intent in ("market_summary", "odds_movement", "bookmaker_coverage"):
        from app.bot.handlers.mercado import mercado_handler
        fid = args.get("fixture_id")
        context.args = [str(fid)] if fid else []
        await mercado_handler(update, context)
        return "mercado_handler"

    elif intent in ("strategy_summary", "strategy_learning_status", "why_pick_strategy"):
        from app.bot.handlers.estrategias import estrategias_handler
        context.args = []
        await estrategias_handler(update, context)
        return "estrategias_handler"

    elif intent == "best_strategies":
        from app.bot.handlers.estrategias import estrategias_handler
        context.args = ["mejor"]
        await estrategias_handler(update, context)
        return "estrategias_handler_best"

    elif intent == "weak_strategies":
        from app.bot.handlers.estrategias import estrategias_handler
        context.args = ["peor"]
        await estrategias_handler(update, context)
        return "estrategias_handler_worst"

    elif intent == "strategy_detail":
        from app.bot.handlers.estrategias import estrategias_handler
        context.args = []
        await estrategias_handler(update, context)
        return "estrategias_handler"

    elif intent == "bankroll_summary":
        from app.bot.handlers.bankroll import bankroll_handler
        await bankroll_handler(update, context)
        return "bankroll_handler"

    elif intent in ("risk_summary", "exposure_question", "correlation_question"):
        from app.bot.handlers.bankroll import riesgo_handler
        await riesgo_handler(update, context)
        return "riesgo_handler"

    elif intent in ("stake_question", "bankroll_help"):
        from app.bot.handlers.bankroll import stake_handler
        await stake_handler(update, context)
        return "stake_handler"

    elif intent in ("governance_summary", "experiment_summary", "model_comparison"):
        from app.bot.handlers.gobernanza import gobernanza_handler
        await gobernanza_handler(update, context)
        return "gobernanza_handler"

    elif intent == "experiment_summary":
        from app.bot.handlers.gobernanza import experimentos_handler
        await experimentos_handler(update, context)
        return "experimentos_handler"

    elif intent in ("activation_readiness", "why_not_activate"):
        from app.bot.handlers.gobernanza import activar_handler
        await activar_handler(update, context)
        return "activar_handler"

    elif intent in ("safe_mode_help",):
        from app.bot.handlers.gobernanza import gobernanza_handler
        await gobernanza_handler(update, context)
        return "gobernanza_handler"

    elif intent == "help":
        context.args = []
        await ayuda_handler(update, context)
        return "ayuda_handler"

    else:  # unknown
        await update.message.reply_html(_UNKNOWN_TEXT)
        return "unknown"


def _log(conn, user_id: int, text: str, intent: str, confidence: float,
         handler_target: str, args: dict, error: str | None = None) -> None:
    if conn is None or not settings.ai_router_log_queries:
        return
    try:
        from app.services.ai_router_service import save_router_log
        save_router_log(
            conn, user_id, text, intent, confidence, handler_target, args,
            response_status="ok" if not error else "error",
            error_message=error,
            provider=settings.ai_router_provider,
        )
    except Exception as exc:
        logger.debug("conversation: log failed — %s", exc)


def _update_ctx(conn, user_id: int, intent: str, args: dict) -> None:
    if conn is None:
        return
    try:
        from app.services.ai_router_service import update_user_context
        update_user_context(conn, user_id, {
            "last_intent":    intent,
            "last_fixture_id": args.get("fixture_id"),
            "preferred_risk_level": args.get("risk_level"),
        })
    except Exception as exc:
        logger.debug("conversation: context update failed — %s", exc)
