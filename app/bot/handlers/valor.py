"""Handler for /valor — value engine metrics from the last sync run."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.utils import send_html
from app.core.config import settings

logger = logging.getLogger(__name__)

# Must match STATUS_* constants in value_engine_live_adapter.py
_VE_SELECTED   = "value_selected"
_VE_SKIPPED    = "value_skipped_engine_off"

# Display labels for each VE rejection status
_STATUS_LABELS: dict[str, str] = {
    "value_selected":                    "Pasaron VE",
    "value_rejected_low_quality":        "Calidad insuficiente (quality_score)",
    "value_rejected_low_edge":           "Edge VE insuficiente",
    "value_rejected_missing_odds":       "Sin cuotas ofrecidas",
    "value_rejected_missing_history":    "Sin historial en DuckDB",
    "value_rejected_missing_calibrator": "Sin calibrador en DuckDB",
    "value_error_fallback":              "Error en evaluación",
}


@require_auth
async def valor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/valor — Show value engine config and metrics from the last sync."""
    logger.info("/valor solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    try:
        text = _build_valor_text()
    except Exception as exc:
        logger.error("/valor: error — %s", exc, exc_info=True)
        text = "Error al obtener métricas del value engine. Usa /estado para diagnosticar."

    await send_html(update.message, text)


def _build_valor_text() -> str:
    from app.data.repositories.sync_runs_repo import get_last_sync_run
    from app.data.repositories import fixture_repo, prediction_repo

    lines: list[str] = ["<b>Value Engine — Métricas</b>", ""]

    enabled = settings.value_engine_enabled
    mode = settings.value_engine_mode if enabled else "off"

    if not enabled or mode == "off":
        lines.append("Motor: <b>INACTIVO</b>")
        lines.append("")
        lines.append("Para activar, agrega a .env:")
        lines.append("  <code>VALUE_ENGINE_ENABLED=true</code>")
        lines.append("  <code>VALUE_ENGINE_MODE=shadow</code>  (o assist)")
        return "\n".join(lines)

    # ── Configuración activa ───────────────────────────────────────────────────
    lines.append(f"Modo: <b>{mode}</b>")
    lines.append(f"Umbral edge VE: <b>{settings.value_engine_min_edge * 100:.1f}%</b>")
    lines.append(f"Umbral calidad: <b>{settings.value_engine_min_quality * 100:.0f}%</b>")
    lines.append(f"Umbral EV adj:  <b>{settings.value_engine_min_ev_adj:.3f}</b>")
    lines.append(
        f"Fallback modelo: <b>{'sí' if settings.value_engine_fallback_to_current else 'no'}</b>"
    )
    lines.append("")

    # ── Último sync ───────────────────────────────────────────────────────────
    last_run = get_last_sync_run()
    if not last_run:
        lines.append("<b>Sin registro de sync</b>")
        lines.append("Ejecuta: <code>python scripts/sync_today.py</code>")
        return "\n".join(lines)

    started = (last_run.get("started_at") or "")[:16].replace("T", " ")
    status = last_run.get("status", "?")
    summary = last_run.get("summary_json") or {}

    status_icon = "OK" if status == "completed" else ("..." if status == "running" else "ERR")
    lines.append(f"<b>Último sync</b>  {started} — {status_icon}")

    fixtures_fetched   = summary.get("fixtures_fetched", "—")
    candidates_gen     = summary.get("candidates_generated", "—")
    publishable_saved  = summary.get("publishable_saved", "—")
    ve_evaluated       = summary.get("value_engine_evaluated")
    ve_selected        = summary.get("value_engine_selected")
    ve_after_cap       = summary.get("value_engine_after_cap")
    ve_pf_rejected     = summary.get("value_engine_pickfilter_rejected")

    lines.append(f"  Fixtures procesados:       <b>{fixtures_fetched}</b>")
    lines.append(f"  Candidatos (modelo):       <b>{candidates_gen}</b>")
    lines.append(f"  Picks oficiales guardados: <b>{publishable_saved}</b>")

    if ve_evaluated is not None:
        lines.append("")
        lines.append("  <b>Value Engine (etapas):</b>")
        lines.append(f"    Evaluados por VE:          <b>{ve_evaluated}</b>")
        if ve_selected is not None:
            lines.append(f"    Pasaron VE (p_cal/q/ev):   <b>{ve_selected}</b>")
        if ve_after_cap is not None and ve_after_cap != ve_selected:
            lines.append(f"    Tras cap por liga:         <b>{ve_after_cap}</b>")
        if ve_pf_rejected is not None and ve_pf_rejected > 0:
            lines.append(
                f"    Rechazados por modelo cuotas: <b>{ve_pf_rejected}</b>"
            )
            lines.append(
                "    <i>(VE dice valor; modelo de cuotas dice edge/conf insuficiente)</i>"
            )

    if last_run.get("error_message"):
        lines.append(f"  Error: {last_run['error_message'][:80]}")

    # ── Candidatos hoy en BD (live) ───────────────────────────────────────────
    lines.append("")
    try:
        all_fixtures = fixture_repo.get_fixtures_today()
        fixture_ids = [f["id"] for f in all_fixtures]
        if not fixture_ids:
            lines.append("<i>Sin fixtures para hoy en BD — ejecuta sync_today.py</i>")
            return "\n".join(lines)

        candidates_today = prediction_repo.get_candidates_for_fixtures(fixture_ids)
        if not candidates_today:
            lines.append("<i>Sin candidatos en BD para hoy</i>")
            return "\n".join(lines)

        # Aggregate
        status_counts: dict[str, int] = {}
        total_publishable = 0
        ve_sel_publishable = 0
        has_ve_data = False

        for c in candidates_today:
            if c.get("is_publishable"):
                total_publishable += 1

            ve_status = c.get("value_engine_status")
            if ve_status and ve_status != _VE_SKIPPED:
                has_ve_data = True
                status_counts[ve_status] = status_counts.get(ve_status, 0) + 1
                if ve_status == _VE_SELECTED and c.get("is_publishable"):
                    ve_sel_publishable += 1

        total = len(candidates_today)
        lines.append(
            f"<b>Candidatos hoy en BD</b>  "
            f"(total: {total} · publicables: {total_publishable})"
        )

        if not has_ve_data:
            lines.append(
                "  Sin datos VE en pick_candidates.\n"
                "  Aplica <code>sql/migrations/014_value_engine_candidates.sql</code> "
                "en Supabase si la migración aún no está aplicada."
            )
            return "\n".join(lines)

        # VE-selected row with publishable breakdown
        ve_sel_count = status_counts.get(_VE_SELECTED, 0)
        if ve_sel_count:
            ve_sel_rejected = ve_sel_count - ve_sel_publishable
            pub_str = f"{ve_sel_publishable} publicables" if ve_sel_publishable else "ninguno publicable"
            lines.append(
                f"  Pasaron VE:                <b>{ve_sel_count}</b>  ({pub_str})"
            )
            if ve_sel_rejected:
                lines.append(
                    f"    → {ve_sel_rejected} rechazados por el modelo de cuotas"
                )

        # Remaining statuses
        shown = {_VE_SELECTED}
        for label_key, label in _STATUS_LABELS.items():
            if label_key in shown:
                continue
            count = status_counts.get(label_key, 0)
            if count:
                lines.append(f"  {label}: <b>{count}</b>")
        # Catch-all for any unknown statuses
        for k, v in status_counts.items():
            if k not in _STATUS_LABELS and k != _VE_SKIPPED:
                lines.append(f"  {k}: <b>{v}</b>")

    except Exception as exc:
        logger.debug("/valor: error leyendo candidatos hoy: %s", exc)
        lines.append("<i>No se pudieron leer los candidatos de hoy</i>")

    return "\n".join(lines)
