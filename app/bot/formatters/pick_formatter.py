"""Telegram message formatters for picks and status summaries.

All output uses HTML parse mode to avoid MarkdownV2 escaping issues.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def _kickoff_str(kickoff_at: str) -> str:
    """Convert ISO kickoff string to a readable local time."""
    try:
        tz = ZoneInfo(settings.default_timezone)
        dt = datetime.fromisoformat(kickoff_at).astimezone(tz)
        return dt.strftime("%H:%M")
    except Exception:
        return kickoff_at


def _pct(value: float) -> str:
    """Format a probability as a percentage string."""
    return f"{value * 100:.1f}%"


def _market_label(market: str) -> str:
    labels = {"1X2": "Resultado 1X2", "OU25": "Más/Menos 2.5 goles", "BTTS": "Ambos anotan"}
    return labels.get(market, market)


def _selection_label(market: str, selection: str) -> str:
    labels: dict[tuple[str, str], str] = {
        ("1X2", "Home"): "Local",
        ("1X2", "Draw"): "Empate",
        ("1X2", "Away"): "Visitante",
        ("OU25", "Over 2.5"): "Más de 2.5",
        ("OU25", "Under 2.5"): "Menos de 2.5",
        ("BTTS", "Yes"): "Sí anotan ambos",
        ("BTTS", "No"): "No anotan ambos",
    }
    return labels.get((market, selection), selection)


def format_picks(
    predictions: list[dict],
    fixtures: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str:
    """Build the HTML message shown by /hoy and /top.

    Args:
        predictions: Rows from ``prediction_repo``.
        fixtures: Rows from ``fixture_repo.get_fixtures_today``.
        team_names: Mapping of internal team_id → team name.
        league_names: Mapping of internal league_id → league name.

    Returns:
        HTML-formatted string ready for ``reply_text(..., parse_mode='HTML')``.
    """
    if not predictions:
        return "No hay picks publicables para hoy."

    fixture_by_id = {f["id"]: f for f in fixtures}
    lines: list[str] = []

    for pred in predictions:
        fix = fixture_by_id.get(pred["fixture_id"])
        if not fix:
            continue

        home = team_names.get(fix["home_team_id"], "Local")
        away = team_names.get(fix["away_team_id"], "Visitante")
        league = league_names.get(fix["league_id"], "")
        kickoff = _kickoff_str(fix["kickoff_at"])

        market_lbl = _market_label(pred["market"])
        pick_lbl = _selection_label(pred["market"], pred["recommended_pick"])
        edge_pct = _pct(pred["edge"])
        conf_pct = _pct(pred["confidence_score"])
        impl_pct = _pct(pred["implied_probability"])
        best_odd = pred["argument_json"].get("best_odd", "?")
        best_bk = pred["argument_json"].get("best_bookmaker", "")

        block = (
            f"<b>{home} vs {away}</b>\n"
            f"⚽ {league} · {kickoff}\n"
            f"\n"
            f"📊 <b>{market_lbl} — {pick_lbl}</b>\n"
            f"📈 Edge: <b>+{edge_pct}</b>  |  Confianza: <b>{conf_pct}</b>\n"
            f"💰 Mejor cuota: <b>{best_odd}</b>"
            + (f" ({best_bk})" if best_bk else "")
            + f"\n"
            f"📋 Prob. justa: {conf_pct} → Implícita: {impl_pct}"
        )
        lines.append(block)

    header = f"<b>Picks del día</b> ({len(predictions)})\n"
    separator = "\n\n" + "─" * 30 + "\n\n"
    return header + separator.join(lines)


def format_estado(estado: dict) -> str:
    """Build the HTML message shown by /estado."""
    return (
        "<b>Estado del bot</b>\n\n"
        f"📅 Fixtures hoy: <b>{estado['fixtures_today']}</b>\n"
        f"💹 Cuotas almacenadas: <b>{estado['odds_rows']}</b>\n"
        f"🎯 Picks publicables: <b>{estado['picks_today']}</b>"
    )
