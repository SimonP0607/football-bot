# Setup — football-bot v1

Guía de configuración completa para dejar el bot operativo desde cero.

---

## Requisitos

- Python 3.11 o superior (el proyecto fue desarrollado con Python 3.13)
- Cuenta en [Supabase](https://supabase.com) (plan gratuito es suficiente para v1)
- Cuenta en [API-Football](https://www.api-football.com) (plan gratuito: 100 req/día)
- Una cuenta de Telegram y acceso a [@BotFather](https://t.me/BotFather)

---

## Orden de bootstrap (primer arranque)

> Sigue exactamente este orden. Cada paso depende del anterior.

```
1.  Configurar .env
2.  Arrancar bot en modo bootstrap → obtener Telegram user ID
3.  Aplicar migraciones SQL en Supabase
4.  Refrescar schema cache (si hace falta)
5.  Verificar Supabase  →  python scripts/check_supabase.py
6.  Verificar API-Football  →  python scripts/check_api_football.py
7.  Sincronizar datos  →  python scripts/sync_today.py
8.  Arrancar bot definitivo  →  python run.py
9.  Probar  /estado  /hoy  /top  en Telegram
```

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
cp .env.example .env
```

Abre `.env` y completa todos los campos marcados con `<COMPLETAR>`:

| Variable | De dónde obtenerla |
|----------|-------------------|
| `TELEGRAM_BOT_TOKEN` | @BotFather → /newbot |
| `SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase → Project Settings → API → service_role (secret) |
| `API_FOOTBALL_KEY` | api-football.com → Dashboard → Credenciales |
| `DEFAULT_LEAGUE_IDS` | IDs de ligas a seguir (ej. `39,140`) |

**Deja `TELEGRAM_ALLOWED_USER_ID=0` por ahora** — el bot arrancará en modo bootstrap para ayudarte a descubrir tu ID numérico.

> ⚠️ Usa la `service_role` key de Supabase, **NO la `anon` key**.

---

## Paso 3 — Crear el bot en BotFather

1. Abre Telegram → busca **@BotFather**
2. Envía `/newbot`
3. Elige nombre y username (debe terminar en `bot`)
4. Copia el token → ponlo en `.env` como `TELEGRAM_BOT_TOKEN`

---

## Paso 4 — Obtener tu Telegram user ID

**Opción A — con el bot en modo bootstrap:**
```bash
python run.py
# El bot arranca con un banner de MODO BOOTSTRAP en los logs
# Abre Telegram → envía /id al bot → copia el número
# Actualiza .env: TELEGRAM_ALLOWED_USER_ID=<número>
# Ctrl+C → python run.py   (reinicia ya configurado)
```

**Opción B — sin arrancar el bot:**
```bash
python scripts/get_telegram_user_id.py
# Muestra user IDs de mensajes recientes al bot
```

---

## Paso 5 — Supabase: crear proyecto

1. Ve a [supabase.com](https://supabase.com) → New Project
2. Guarda el **Project URL** y la **service_role key** en `.env`
3. Ejecuta el script de guía de bootstrap de DB:

```bash
python scripts/bootstrap_database.py
```

Este script:
- Verifica conexión
- Detecta si faltan tablas
- **Imprime el SQL completo** para copiar en el SQL Editor
- Indica exactamente qué hacer en cada fase

---

## Paso 6 — Aplicar migraciones SQL

Las migraciones deben aplicarse **manualmente** en el SQL Editor de Supabase
(la API REST no permite ejecutar DDL directamente).

1. Abre tu proyecto en Supabase → **SQL Editor** → New query
2. Copia y pega el contenido de `sql/migrations/001_init.sql` → **Run**
3. Crea otra query y pega `sql/migrations/002_constraints_and_indexes.sql` → **Run**

> Las sentencias usan `IF NOT EXISTS` y son idempotentes — pueden correrse varias veces sin daño.

### Verificar que las tablas existen

```bash
python scripts/check_supabase.py
```

Salida esperada cuando todo está bien:
```
=== check_supabase.py ===

[ 1 ] Variables de entorno
  ✓ SUPABASE_URL = https://xxxx.supabase.co...
  ✓ SUPABASE_KEY = eyJhbGci...

[ 2 ] Conexión y tablas
  ✓ bot_users              (filas encontradas en muestra: 0)
  ✓ leagues                (filas encontradas en muestra: 0)
  ✓ teams                  (filas encontradas en muestra: 0)
  ✓ fixtures               (filas encontradas en muestra: 0)
  ✓ odds_snapshots         (filas encontradas en muestra: 0)
  ✓ predictions            (filas encontradas en muestra: 0)
  ✓ prediction_results     (filas encontradas en muestra: 0)
  ...

✅  Supabase OK — el bot puede conectar correctamente.
```

Si ves errores `PGRST205` o "table not found":
- Las migraciones no se aplicaron correctamente
- Verifica que ejecutaste ambos archivos SQL en el SQL Editor
- Recarga la página de Supabase (a veces el schema cache tarda unos segundos)

---

## Paso 7 — Verificar API-Football

```bash
python scripts/check_api_football.py --league 39 --season 2025
```

Salida esperada:
```
[ 2 ] Conexión y credenciales (/status)
  ✓ Conectado correctamente
    Plan:              Free
    Requests hoy:      3 / 100

[ 3 ] Datos de ejemplo (fixtures + odds)
  ✓ Fixtures encontrados hoy: 4
```

### Configurar ligas y temporada

Busca los IDs de las ligas que quieres seguir:
- `39`  = Premier League
- `140` = La Liga
- `253` = Liga BetPlay (Colombia)
- `135` = Serie A
- `78`  = Bundesliga
- `61`  = Ligue 1

```env
DEFAULT_LEAGUE_IDS=39,140
DEFAULT_SEASON=2025
```

---

## Paso 8 — Primer sync de datos

```bash
python scripts/sync_today.py
```

El script:
1. Fetcha fixtures del día de API-Football → los guarda en Supabase
2. Fetcha odds de cada fixture → los guarda en `odds_snapshots`
3. Corre el pipeline de predicciones → guarda picks en `predictions`

---

## Paso 9 — Arrancar el bot

```bash
python run.py
```

El bot:
1. Valida las variables de entorno (falla rápido si faltan)
2. Registra los comandos en Telegram
3. Verifica el schema de la DB (log `DB_NOT_INITIALIZED` si falta)
4. Empieza a escuchar mensajes

### Probar localmente

Abre Telegram → busca tu bot → envía:
- `/estado` → **empieza siempre aquí** — muestra Telegram OK, Supabase OK, schema aplicado, conteos del día
- `/id` → te muestra tu user ID (sin autenticación)
- `/start` → lista de comandos
- `/hoy` → picks del día (requiere sync previo)
- `/top` → top picks por confianza

---

## Qué muestra /estado en cada fase del bootstrap

### Fase 1 — Migraciones no aplicadas
```
Estado del sistema

📡 Telegram:              OK
🔌 Supabase:              OK
🗄️  Schema:                NO APLICADO ✗
   └ Faltantes: fixtures  predictions  ...

Aplica las migraciones en Supabase → SQL Editor:
  sql/migrations/001_init.sql
  sql/migrations/002_constraints_and_indexes.sql
```

### Fase 2 — Schema OK pero sin datos
```
Estado del sistema

📡 Telegram:              OK
🔌 Supabase:              OK
🗄️  Schema:                aplicado ✓

📅 Fixtures hoy:          0
💹 Cuotas almacenadas:    0
🎯 Picks publicables:     0

Sin datos para hoy. Ejecuta:
  python scripts/sync_today.py
```

### Fase 3 — Todo operativo
```
Estado del sistema

📡 Telegram:              OK
🔌 Supabase:              OK
🗄️  Schema:                aplicado ✓

📅 Fixtures hoy:          12
💹 Cuotas almacenadas:    450
🎯 Picks publicables:     3
```

---

## Referencia rápida de comandos

```bash
# Bootstrap DB — guía completa + SQL para copiar
python scripts/bootstrap_database.py

# Verificar Supabase (tablas + conteos)
python scripts/check_supabase.py

# Verificar API-Football
python scripts/check_api_football.py --league 39 --season 2025

# Descubrir tu Telegram user ID
python scripts/get_telegram_user_id.py

# Sincronizar fixtures y odds de hoy
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
| `TELEGRAM_ALLOWED_USER_ID` | No (local) | Tu Telegram user ID numérico; `0` activa modo bootstrap |
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

## Diagnóstico de errores frecuentes

### `PGRST205 — Could not find the table 'public.fixtures'`
**Causa:** Las migraciones SQL no se aplicaron.  
**Solución:**
1. `python scripts/bootstrap_database.py` → imprime el SQL completo
2. Copia y ejecuta ambos archivos en Supabase → SQL Editor
3. `python scripts/check_supabase.py` → verifica

### Bot arranca en MODO BOOTSTRAP
**Causa:** `TELEGRAM_ALLOWED_USER_ID` es 0 o no está configurado.  
**Solución:** Envía `/id` al bot → copia el número → ponlo en `.env` → reinicia.

### `ValueError: Variables de entorno requeridas no configuradas`
**Causa:** Falta `TELEGRAM_BOT_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY`, o `API_FOOTBALL_KEY`.  
**Solución:** Revisa `.env` y completa todos los valores `<COMPLETAR>`.

### `/hoy` y `/top` muestran "La base de datos no está inicializada"
**Causa:** Tablas faltantes.  
**Solución:** Aplica las migraciones (ver arriba). El bot auto-detecta una vez aplicadas — no necesita reiniciarse.

### `/hoy` responde "No hay picks publicables para hoy"
**Causa:** No se ha corrido `sync_today.py` o no hay partidos hoy.  
**Solución:** `python scripts/sync_today.py`

---

## Notas de seguridad

- El archivo `.env` está en `.gitignore` — nunca lo subas al repo
- Usa la `service_role` key de Supabase, no la `anon` key
- El acceso al bot es exclusivo: solo `TELEGRAM_ALLOWED_USER_ID` puede usarlo
- Los logs nunca imprimen keys ni tokens en claro (siempre enmascarados)

---

## Modelo de predicción v1

El modelo usa **consenso de cuotas** (no ML, no Poisson):

1. Recopila odds de todos los bookmakers disponibles para cada mercado
2. Por bookmaker: calcula probabilidades justas eliminando el margen (overround)
3. Promedia las probabilidades justas → `model_probability`
4. Compara contra la mejor cuota disponible → `edge`
5. Publica el pick si: `edge ≥ MIN_EDGE` Y `model_probability ≥ MIN_CONFIDENCE`

> **v1.1 planeado:** modelo Poisson con historial de goles, auto-evaluación de resultados post-partido.
