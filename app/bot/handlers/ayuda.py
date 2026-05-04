"""Handler for /ayuda — comprehensive help explaining the bot."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.ui.keyboard import main_menu_keyboard
from app.bot.utils import send_html

logger = logging.getLogger(__name__)

_AYUDA_TEXT = """\
<b>Ayuda — Football Picks Bot</b>

Este bot analiza partidos de fútbol usando un modelo estadístico (Poisson + Elo) \
calibrado con datos históricos. <b>Los picks son informativos. No son consejos de inversión.</b>

<b>Comandos principales</b>
/hoy — picks publicables del día (filtro estricto)
/top — mejores candidatos por calidad (incluye observados)
/partido &lt;equipo o ID&gt; — análisis completo de un partido
/parlay — combinadas recomendadas (2/3/4 legs)
/valor — métricas del Value Engine
/resultados — picks liquidados (win/loss/void)
/rendimiento — ROI, hit rate, yield
/ligas — ligas activas y su cobertura
/live — picks en juego ahora mismo
/seguimiento &lt;id&gt; — seguimiento de un partido concreto
/equipo &lt;nombre&gt; — buscar equipo en catálogo
/jugador &lt;nombre&gt; — buscar jugador
/jugadorstats &lt;nombre&gt; — estadísticas y forma reciente de un jugador
/props &lt;fixture_id&gt; — señales de jugadores para un partido (no son apuestas)
/playerhot [liga|goles|tiros|tarjetas] — jugadores en mejor forma reciente
/estado — salud del sistema (BD, API, sync)
/menu — menú con botones
/ayuda — esta pantalla

<b>Modo conversacional</b>
También puedes escribir en lenguaje natural:
· "Dame picks de hoy"
· "Dame una combinada conservadora"
· "Analiza Fluminense"
· "Cómo va el rendimiento"
· "Partidos en vivo"
· "Estado del sistema"
· "Sigue el partido 1535267"
· "Cómo viene Salah"
· "Jugadores calientes de Premier"
· "Prop signals del partido 1060362"

<b>Cómo interpretar las métricas</b>
· <b>Edge</b> — diferencia entre probabilidad del modelo y probabilidad implícita de la cuota. \
Positivo = cuota subvalorada según el modelo.
· <b>EV</b> — valor esperado. Positivo indica que la cuota paga más de lo que el modelo predice.
· <b>Confianza</b> — probabilidad que asigna el modelo a esa selección.
· <b>Riesgo (parlay)</b> — combinación de correlación entre legs + varianza. Menor es mejor.

<b>Tipos de pick</b>
· <b>Oficial</b> — pasa todos los filtros de calidad, edge y value engine.
· <b>Observado</b> — edge positivo pero no supera el umbral de publicación.
· <b>Sin valor</b> — rechazado por edge insuficiente o calidad baja.

<b>Tipos de parlay</b>
· 🛡 Conservadora — 2 legs (menor riesgo)
· ⚖️ Balanceada — 3 legs (equilibrio)
· 🔥 Agresiva — 4 legs (mayor cuota, más riesgo)

<b>Advertencia responsable</b>
Los pronósticos son herramientas de análisis. El resultado de un partido \
nunca es 100% predecible. Gestiona el riesgo y nunca apuestes más de lo que \
puedas perder.
"""


@require_auth
async def ayuda_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ayuda — Show comprehensive help."""
    logger.info("/ayuda solicitado por user_id=%s", update.effective_user.id)
    await update.message.reply_html(
        _AYUDA_TEXT,
        reply_markup=main_menu_keyboard(),
    )
