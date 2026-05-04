"""Handler for /start — welcome message and command overview."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.ui.keyboard import main_menu_keyboard

logger = logging.getLogger(__name__)

_WELCOME = """\
⚽ <b>Football Picks Bot</b>

Pronósticos basados en modelo estadístico Poisson + Elo + Value Engine calibrado.

<b>Comandos principales</b>

/hoy — picks publicables del día (oficial)
/top — mejores candidatos por calidad, con sección de observados
/partido &lt;id o equipo&gt; — análisis completo de un partido concreto
/ligas — ligas activas por tier (paginado: /ligas 1 · /ligas 2 · /ligas 3)
/valor — métricas del Value Engine del último sync
/resultados — últimos picks resueltos (win/loss/void + profit)
/rendimiento — ROI, hit rate y yield por período y mercado
/estado — salud del sistema: Supabase, API-Football, último sync
/live — picks en juego ahora mismo (estado live)
/seguimiento &lt;id&gt; — seguimiento detallado de un partido
/parlay — parlays recomendados del día (combinadas)
/equipo &lt;nombre o id&gt; — buscar equipo en el catálogo
/jugador &lt;nombre&gt; — buscar jugador en el catálogo
/jugadorstats &lt;nombre&gt; — estadísticas y forma reciente de un jugador
/props &lt;fixture_id&gt; — señales de jugadores para un partido
/playerhot — jugadores en mejor forma reciente
/mercado &lt;fixture_id&gt; — closing lines y señales de movimiento de cuotas
/clv [días] — reporte de Closing Line Value de los picks
/alertas — alertas proactivas y estado del scheduler
/menu — menú con botones interactivos
/ayuda — ayuda completa y guía de uso

<b>Modo conversacional</b>

También puedes escribirme en lenguaje natural:
<i>"Dame picks de hoy" · "Combinada conservadora" · "Estado del sistema"</i>

<b>Notas</b>

· Los picks son informativos. No son consejos de inversión.
· El Value Engine (VE) evalúa valor calibrado con probabilidades DuckDB.
  Cuando el VE y el modelo de cuotas divergen, prevalece el modelo de cuotas.
· /top muestra picks oficiales; si no hay, muestra candidatos en observación.
"""


@require_auth
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Comando /start desde user_id=%s", update.effective_user.id)
    await update.message.reply_html(
        _WELCOME,
        reply_markup=main_menu_keyboard(),
    )
