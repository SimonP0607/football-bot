# Setup — football-bot v1

Guía de configuración completa para dejar el bot operativo desde cero.

---

## Requisitos

- Python 3.11 o superior (el proyecto fue desarrollado con Python 3.13)
- Cuenta en [Supabase](https://supabase.com) (plan gratuito es suficiente para v1)
- Cuenta en [API-Football](https://www.api-football.com) (plan gratuito: 100 req/día)
- Una cuenta de Telegram y acceso a [@BotFather](https://t.me/BotFather)

---

## Paso 1 — Entorno Python

```bash
# Crear y activar virtualenv
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (CMD)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

---

## Paso 2 — Configurar .env

```bash
# Copia la plantilla
cp .env.example .env
```

Abre `.env` y completa **todos** los campos marcados con `<COMPLETAR>`.
Ver detalles de cada variable más abajo.

---

## Paso 3 — Crear el bot en BotFather

1. Abre Telegram → busca **@BotFather**
2. Envía `/newbot`
3. Elige un nombre para el bot (ejemplo: `Football Picks Bot`)
4. Elige un username (debe terminar en `bot`, ejemplo: `mipicks_bot`)
5. BotFather te da el **token** — cópialo en `.env` como `TELEGRAM_BOT_TOKEN`

### Encontrar tu Telegram user ID

Necesitas tu ID numérico (un número entero, no tu @username) para `TELEGRAM_ALLOWED_USER_ID`.

**Opción A** (recomendada):
1. Completa `.env` con el token pero deja `TELEGRAM_ALLOWED_USER_ID=0`
2. Arranca el bot: `python run.py`
3. Abre Telegram → escribe `/id` al bot
4. El bot te responde con tu ID numérico
5. Cópialo en `.env` y reinicia el bot

**Opción B** (sin arrancar el bot):
- Manda cualquier mensaje al bot → los logs muestran `user_id=XXXXXXXX` en el WARNING de "Acceso no autorizado"

---

## Paso 4 — Supabase

### 4.1 Crear proyecto
1. Ve a [supabase.com](https://supabase.com) → New Project
2. Guarda el **Project URL** y la **service_role key** en `.env`

> ⚠️ Usa la `service_role` key, **NO la `anon` key**.
> La encuentras en: Project Settings → API → `service_role` (secret)

### 4.2 Aplicar migraciones

Las migraciones se aplican manualmente en el **SQL Editor** de Supabase.

1. Abre tu proyecto en Supabase → **SQL Editor** → New query
2. Copia y pega el contenido de `sql/migrations/001_init.sql` → **Run**
3. Copia y pega el contenido de `sql/migrations/002_constraints_and_indexes.sql` → **Run**

> Las sentencias usan `IF NOT EXISTS` y son idempotentes — pueden correrse varias veces sin daño.

### 4.3 Verificar conexión

```bash
python scripts/check_supabase.py
```

Salida esperada:
```
=== check_supabase.py ===

[ 1 ] Variables de entorno
  ✓ SUPABASE_URL = https://xxxx.supabase.co...
  ✓ SUPABASE_KEY = eyJhbGci...

[ 2 ] Conexión y tablas
  ✓ bot_users              (filas encontradas en muestra: 0)
  ✓ leagues                (filas encontradas en muestra: 0)
  ...

✅  Supabase OK — el bot puede conectar correctamente.
```

### 4.4 Insertar usuario autorizado (opcional)

El bot usa `TELEGRAM_ALLOWED_USER_ID` desde `.env` para autorizar acceso.
La tabla `bot_users` existe para uso futuro (multi-usuario, auditoría).

Si quieres pre-poblarla manualmente:

```sql
INSERT INTO bot_users (telegram_user_id, username, is_admin)
VALUES (TU_USER_ID_AQUI, 'tu_username', true);
```

---

## Paso 5 — API-Football

### 5.1 Obtener API key
1. Regístrate en [api-football.com](https://www.api-football.com)
2. Ve al Dashboard → **Credenciales** → copia la API Key
3. Pégala en `.env` como `API_FOOTBALL_KEY`

### 5.2 Configurar ligas y temporada
Busca los IDs de las ligas que quieres seguir en la documentación de API-Football:
- `39` = Premier League
- `140` = La Liga
- `253` = Liga BetPlay (Colombia)
- `135` = Serie A

```env
DEFAULT_LEAGUE_IDS=39,140
DEFAULT_SEASON=2025
```

### 5.3 Verificar conexión

```bash
python scripts/check_api_football.py --league 39 --season 2025
```

Salida esperada:
```
=== check_api_football.py ===

[ 1 ] Variables de entorno
  ✓ API_FOOTBALL_BASE_URL = https://v3.football.api-sports.io
  ✓ API_FOOTBALL_KEY = abc12345...

[ 2 ] Conexión y credenciales (/status)
  ✓ Conectado correctamente
    Plan:              Free
    Suscripción activa: True
    Requests hoy:      3 / 100

[ 3 ] Datos de ejemplo (fixtures + odds)
  Consultando fixtures — league=39, season=2025, date=2025-01-15
  ✓ Fixtures encontrados hoy: 4

✅  API-Football OK — credenciales válidas.
```

---

## Paso 6 — Primer sync

```bash
# Con defaults de .env
python scripts/sync_today.py

# O con valores explícitos
python scripts/sync_today.py --leagues 39,140 --season 2025
```

El script:
1. Fetcha fixtures del día de API-Football → los guarda en Supabase
2. Fetcha odds de cada fixture → los guarda en `odds_snapshots`
3. Corre el pipeline de predicciones → guarda picks en `predictions`

---

## Paso 7 — Arrancar el bot

```bash
python run.py
```

El bot:
1. Valida las variables de entorno (falla rápido si faltan)
2. Registra los comandos en Telegram (`/hoy`, `/top`, `/estado`, `/id`)
3. Verifica la conexión a Supabase
4. Empieza a escuchar mensajes

### Probar localmente

Abre Telegram → busca tu bot → envía:
- `/start` → debe responder con la lista de comandos
- `/id` → te muestra tu user ID
- `/estado` → muestra el estado actual (fixtures, odds, picks)
- `/hoy` → muestra picks del día (si hay sync previo con picks publicables)

---

## Referencia rápida de comandos

```bash
# Verificar Supabase
python scripts/check_supabase.py

# Verificar API-Football
python scripts/check_api_football.py --league 39 --season 2025

# Sync manual
python scripts/sync_today.py

# Arrancar bot
python run.py

# Correr tests
python -m pytest tests/ -v
```

---

## Variables de entorno — referencia

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Sí | Token de BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | Sí | Tu Telegram user ID numérico |
| `SUPABASE_URL` | Sí | URL del proyecto Supabase |
| `SUPABASE_KEY` | Sí | service_role key de Supabase |
| `API_FOOTBALL_KEY` | Sí | API key de api-football.com |
| `API_FOOTBALL_BASE_URL` | No | URL base (no cambiar) |
| `DEFAULT_TIMEZONE` | No | Zona horaria (default: America/Bogota) |
| `DEFAULT_MARKETS` | No | Mercados (default: 1X2,OU25,BTTS) |
| `MAX_DAILY_PICKS` | No | Máx. picks/día (default: 3) |
| `MIN_EDGE` | No | Edge mínimo (default: 0.05 = 5%) |
| `MIN_CONFIDENCE` | No | Confianza mínima (default: 0.60 = 60%) |
| `DEFAULT_LEAGUE_IDS` | Para sync | IDs de ligas separados por coma |
| `DEFAULT_SEASON` | Para sync | Año de temporada (ej. 2025) |
| `APP_ENV` | No | local \| production (default: local) |
| `LOG_LEVEL` | No | DEBUG \| INFO \| WARNING (default: INFO) |

---

## Notas de seguridad

- El archivo `.env` está en `.gitignore` — nunca lo subas al repo
- Usa la `service_role` key de Supabase, no la `anon` key
- El acceso al bot es exclusivo: solo `TELEGRAM_ALLOWED_USER_ID` puede usarlo
- Los logs no imprimen keys ni tokens

---

## Limitaciones del plan gratuito de API-Football

- 100 requests/día
- Cada `sync_today.py` consume: 1 req (fixtures) + 1 req por fixture (odds) + 1 req (/status en check)
- Con 5 ligas y 4 fixtures cada una: ~20 requests por sync
- Suficiente para 1 sync diario con varias ligas

---

## Modelo de predicción v1

El modelo usa **consenso de cuotas** (no ML, no Poisson):

1. Recopila odds de todos los bookmakers disponibles para cada mercado
2. Por bookmaker: calcula probabilidades justas eliminando el margen (overround)
3. Promedia las probabilidades justas → `model_probability`
4. Compara contra la mejor cuota disponible → `edge`
5. Publica el pick si: `edge ≥ MIN_EDGE` Y `model_probability ≥ MIN_CONFIDENCE`

> **v1.1 planeado:** modelo Poisson con historial de goles, auto-evaluación de resultados post-partido.
