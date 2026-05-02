"""Inline keyboard builders for Phase 9 interactive UX.

All keyboards return InlineKeyboardMarkup instances.
Callback data strings follow the pattern: "<prefix>:<action>[:<id>]"
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Full main menu — used by /menu and /start."""
    return InlineKeyboardMarkup([
        [_btn("📊 Top picks", "menu:top"),    _btn("📅 Picks de hoy", "menu:hoy")],
        [_btn("🎯 Parlay",    "menu:parlay"), _btn("🔴 Live",         "menu:live")],
        [_btn("📈 Rendimiento","menu:rendimiento"), _btn("🏆 Resultados", "menu:resultados")],
        [_btn("💡 Valor",     "menu:valor"),  _btn("⚙️ Estado",       "menu:estado")],
        [_btn("🏟 Ligas",     "menu:ligas"),  _btn("❓ Ayuda",        "menu:ayuda")],
    ])


def top_actions_keyboard() -> InlineKeyboardMarkup:
    """Quick actions after /top."""
    return InlineKeyboardMarkup([
        [_btn("🎯 Ver parlay",    "menu:parlay"), _btn("💡 Ver valor",    "menu:valor")],
        [_btn("🏆 Resultados",   "menu:resultados"), _btn("🔄 Actualizar", "menu:top")],
    ])


def parlay_keyboard() -> InlineKeyboardMarkup:
    """Parlay type selector."""
    return InlineKeyboardMarkup([
        [_btn("🛡 Conservadora (2)", "parlay:2"),
         _btn("⚖️ Balanceada (3)",   "parlay:3"),
         _btn("🔥 Agresiva (4)",     "parlay:4")],
        [_btn("⚠️ Reglas de riesgo", "parlay:risk"),
         _btn("📈 Rendimiento",      "menu:rendimiento")],
    ])


def fixture_actions_keyboard(fixture_id: int | str) -> InlineKeyboardMarkup:
    """Actions for a specific fixture."""
    fid = str(fixture_id)
    return InlineKeyboardMarkup([
        [_btn("🔴 Seguir en vivo",      f"fixture:follow:{fid}"),
         _btn("📊 Detalle",             f"fixture:detail:{fid}")],
        [_btn("⬅️ Menú",               "menu:back")],
    ])


def live_actions_keyboard(fixture_id: int | str | None = None) -> InlineKeyboardMarkup:
    """Live monitoring actions."""
    rows = [[_btn("🔄 Refrescar", "live:refresh")]]
    if fixture_id:
        rows.append([_btn(f"📊 Detalle fixture", f"fixture:detail:{fixture_id}")])
    rows.append([_btn("⬅️ Menú", "menu:back")])
    return InlineKeyboardMarkup(rows)


def pagination_keyboard(prefix: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Previous / Next pagination buttons."""
    buttons: list[InlineKeyboardButton] = []
    if page > 1:
        buttons.append(_btn("◀️ Anterior", f"{prefix}:{page - 1}"))
    if page < total_pages:
        buttons.append(_btn("Siguiente ▶️", f"{prefix}:{page + 1}"))
    return InlineKeyboardMarkup([buttons] if buttons else [])


def back_home_keyboard() -> InlineKeyboardMarkup:
    """Simple back-to-menu button."""
    return InlineKeyboardMarkup([[_btn("⬅️ Menú principal", "menu:back")]])
