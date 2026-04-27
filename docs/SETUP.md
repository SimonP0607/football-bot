# Setup — football-bot

Bot privado de picks pre-match con API-Football Pro + Supabase + Telegram.

---

## Requisitos

- Python 3.11+
- Cuenta [Supabase](https://supabase.com)
- Cuenta [API-Football](https://www.api-football.com) — **plan Pro o superior**
- Telegram + [@BotFather](https://t.me/BotFather)

---

## Arquitectura de base de datos

| Capa | Tablas principales | Retención |
|------|--------------------|-----------|
| A — Catálogo | `competitions`, `competition_seasons`, `tracked_competitions`, `teams`, `ref_bookmakers`, `ref_bet_types` | Permanente |
| B — Hot | `fixtures`, `fixture_contexts`, `odds_snapshots`, `pick_candidates`, `published_picks`, `api_sync_runs`, `api_usage_snapshots` | 7–14 días (odds/contextos) |
| C — Analítico | `team_competition_metrics`, `h2h_cache`, `market_availability_cache` | Recomputado |

**Relación clave:** `fixtures.league_id → competition_seasons.id` (no al ID de `competitions`).

---

## Niveles de sync (sync_tier)

| Tier | Valor | Comportamiento |
|------|-------|----------------|
| 1 | `tier_1_daily` | Standings + enrichment completo todos los días |
| 2 | `tier_2_matchday` | Enrichment completo solo cuando hay fixtures |
| 3 | `tier_3_light` | Solo fixtures + odds (sin standings, stats, injuries) |

---

## Instalación inicial

```bash
# 1. Clonar y configurar entorno
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 2. Configurar variables
cp .env.example .env
# Editar .env con tus credenciales
```

---

## Base de datos (Supabase)

En **Supabase → SQL Editor**, ejecutar en orden (instalación limpia):

```sql
-- 1. Schema completo (tablas, FKs, índices)
sql/migrations/010_production_schema.sql

-- 2. Columna sync_tier en tracked_competitions
sql/migrations/011_sync_tier.sql

-- 3. Tabla pick_results (settlement / ROI)
sql/migrations/012_settlement.sql

-- 4. Funciones de retención (cleanup_retention, cleanup_retention_preview)
sql/migrations/013_retention_cleanup.sql

-- 5. Seed: configurar qué ligas rastrear y con qué tier
sql/seed_tracked_competitions.sql
```

> **Nota:** 010 usa `DROP … IF EXISTS` antes de crear — aplícala solo en instalaciones limpias.
> 011, 012 y 013 son incrementales y seguros de re-ejecutar.

---

## Flujo de ejecución desde cero

### Paso 1 — Phase A + B: datos de referencia y coverage

```bash
# Configura DEFAULT_LEAGUE_IDS en .env con todos los IDs que quieras rastrear
# tier_1: 2,3,39,40,61,71,78,88,94,98,128,135,140,144,239
# tier_2: 11,13,16,17,22,32,34,45,62,66,73,79,81,103,106,113,119,130,137,141,143,241
# tier_3: 29,30,31,95,96,108,115,121,147

python scripts/sync_reference.py
```

Esto llama `/leagues?current=true`, guarda coverage en `competition_seasons`,
y popula `ref_bookmakers` y `ref_bet_types`.

### Paso 2 — Seed: configurar qué ligas rastrear y con qué tier

En **Supabase → SQL Editor** (si no lo corriste en la instalación inicial):

```sql
sql/seed_tracked_competitions.sql
```

Solo funciona para ligas que ya pasaron por Phase B. Idempotente — se puede re-ejecutar.

### Paso 3 — Phase C: sync diario

```bash
python scripts/sync_today.py
```

Lee `tracked_competitions` para saber qué ligas sincronizar. Aplica tier-awareness:
- T1: standings + enrichment completo
- T2: equal a T1 solo si hay fixtures
- T3: fixtures + odds, sin enrichment

### Paso 4 — Bot

```bash
python -m app.main
```

Enviar `/id` para descubrir el Telegram user ID, luego configurar `TELEGRAM_ALLOWED_USER_ID` en `.env`.

### Paso 5 — Phase D (opcional, prematch)

```bash
# Correr 60-90 min antes del kickoff
python scripts/sync_prematch.py --window 90
```

---

## Comandos del bot

| Comando | Descripción |
|---------|-------------|
| `/start` | Menú de ayuda |
| `/ligas` | Ligas activas, tier, coverage |
| `/hoy` | Picks publicables del día |
| `/top` | Top 5 picks por confianza |
| `/partido <id o equipo>` | Análisis completo de un partido |
| `/estado` | Estado del sistema, cuota API, último sync |

---

## Flujo de ejecución diario (producción)

```
07:00  sync_reference.py --phase b   (opcional, 1× por semana)
08:00  sync_today.py                 (Phase C: fixtures + odds + picks)
12:00  sync_today.py                 (re-sync si hubo cambios de odds)
T-90m  sync_prematch.py --window 90  (odds frescos + lineups)
```

---

## Base histórica local (DuckDB)

Capa analítica local **completamente separada** de Supabase. No afecta el flujo operativo diario.

### Separación de capas

| Capa | Motor | Propósito |
|------|-------|-----------|
| **Operativa** | Supabase | Fixtures del día, odds, picks activos (TTL corto) |
| **Histórica** | DuckDB local | Temporadas completas, backtesting, métricas de ROI |

### Tablas del schema histórico

| Tabla | Contenido |
|-------|-----------|
| `fixtures_history` | Partidos terminados con resultado final |
| `standings_history` | Snapshots de clasificaciones por fecha |
| `team_stats_history` | Stats agregadas por equipo y temporada |
| `odds_history` | Mejores cuotas al momento de generar el pick |
| `published_picks_history` | Picks publicados en Telegram |
| `pick_results_history` | Resultados (win/loss/void) y profit |
| `backtest_runs` | Metadatos de cada ejecución de backtest |
| `backtest_metrics` | Resultados por pick en cada backtest |

### Inicialización (una vez)

```bash
# Crea el archivo DuckDB y aplica el schema (idempotente)
python scripts/init_local_db.py

# Verificar que todas las tablas existen y las operaciones básicas funcionan
python scripts/test_local_db.py
```

La base se crea en `./data/local/football_history.duckdb` (configurable con `LOCAL_DB_PATH` en `.env`).

### Uso desde Python

```python
from app.data.local.duckdb_client import get_local_db, init_schema
from app.data.local import history_repo as repo

conn = get_local_db()
init_schema(conn)  # idempotente — seguro llamar siempre

# Insertar un fixture histórico
repo.insert_fixture(conn, {
    "id": 123,                      # mirrors Supabase fixtures.id
    "provider_fixture_id": 1060001,
    "provider_league_id": 39,
    "season": 2025,
    "home_team_id": 33,
    "away_team_id": 34,
    "kickoff_at": "2025-04-15T20:00:00+00:00",
    "status_short": "FT",
    "goals_home": 2,
    "goals_away": 1,
})

# Consultar ROI global
summary = repo.get_roi_summary(conn)
print(summary)  # {"wins": 10, "losses": 5, "profit_units": 4.3, "roi_pct": 28.7}

# Consultar ROI por mercado
by_market = repo.get_roi_by_market(conn)

# Crear un backtest
run_id = repo.create_backtest_run(conn, "v1_test", leagues=[39, 140], seasons=[2025])
repo.insert_backtest_metric(conn, run_id, {...})
repo.finish_backtest_run(conn, run_id, total_picks=50, total_profit_units=8.5, roi_pct=17.0)
```

### Consultas analíticas directas

```python
# Consulta ad-hoc con SQL
conn = get_local_db()
df = conn.execute("""
    SELECT league_name, COUNT(*) AS picks, SUM(profit_units) AS profit
    FROM pick_results_history r
    JOIN fixtures_history f ON f.id = r.fixture_history_id
    WHERE result_status IN ('win','loss','void')
    GROUP BY league_name ORDER BY profit DESC
""").df()  # retorna pandas DataFrame
print(df)
```

### Fase 3 — Ingestión histórica desde Supabase

Detecta temporadas cerradas en Supabase y las archiva en DuckDB local.

#### Señal de temporada cerrada (dual, ambas deben cumplirse)

| Signal | Fuente | Descripción |
|--------|--------|-------------|
| `competition_seasons.current = false` | Supabase | API-Football marcó la temporada como no activa |
| Fracción terminal ≥ `HISTORY_MIN_TERMINAL_FRACTION` | `fixtures.status_short` | Al menos el 95 % de los fixtures en estado FT / AET / PEN / AWD / Canc / WO |

Una liga con `current=false` pero con fixtures en curso (< 95 % terminados) **no** entra al histórico hasta que todos terminen.

#### Política de ventana móvil (por liga)

```
max = HISTORY_MAX_CLOSED_SEASONS   (default: 4)
```

- Se conservan como máximo `max` temporadas cerradas por liga.
- **Excepción primera vez**: la primera transición que superaría el límite omite el borrado (flag `first_rollover_done` en DuckDB).
- A partir de la segunda, cada temporada nueva que entra expulsa la más antigua.

El flag `first_rollover_done` se guarda en `history_metadata` (DuckDB) — no en `.env`.

#### Comandos

```powershell
# Preview: muestra qué entraría al histórico sin escribir nada
python scripts/sync_historical.py --dry-run

# Archivar todas las temporadas cerradas elegibles
python scripts/sync_historical.py

# Archivar solo una liga
python scripts/sync_historical.py --league 39

# Archivar liga + temporada específica
python scripts/sync_historical.py --league 39 --season 2024
```

#### Variables de entorno (`.env`)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `HISTORY_MAX_CLOSED_SEASONS` | `4` | Máx. temporadas cerradas por liga en DuckDB |
| `HISTORY_SKIP_PRUNE_ON_FIRST_ROLLOVER` | `true` | Omitir borrado en la primera transición |
| `HISTORY_MIN_TERMINAL_FRACTION` | `0.95` | Fracción mínima de fixtures terminados |

#### Eventos de log

| Evento | Cuándo |
|--------|--------|
| `[eligible_for_history]` | Liga/temporada detectada como cerrada |
| `[inserted_history]` | Datos archivados en DuckDB |
| `[skipped_prune_first_rollover]` | Primera excepción consumida |
| `[pruned_oldest_history]` | Temporada más antigua eliminada |
| `[nothing_to_archive]` | No hay temporadas cerradas elegibles |

### Fase 4 — Backfill histórico desde API-Football

Importa fixtures, standings y estadísticas de equipos para una temporada cerrada
directamente desde API-Football hacia DuckDB local. No escribe en Supabase.

#### Guardia de temporada actual

Antes de escribir cualquier dato, el script llama a
`/leagues?id=LEAGUE_ID&season=SEASON`. Si la API devuelve `current=True`, el
backfill se cancela y no se escribe nada.

#### Flujo de llamadas API

```
1. /leagues?id=&season=        → valida current=False, extrae coverage
2. /fixtures?league=&season=   → todos los fixtures de la temporada (1 respuesta)
3. /standings?league=&season=  → tabla de clasificación (si coverage.standings)
4. /teams/statistics × N       → stats por equipo único (N ≈ 10-30 por liga)

Con --with-per-fixture (opcional, caro):
5. /fixtures/statistics × F    → estadísticas por partido (si coverage)
6. /injuries × F               → bajas por partido (si coverage)
7. /predictions × F            → predicciones por partido (si coverage)
   Donde F = número de fixtures terminados.
```

#### Presupuesto de llamadas API

| Modo | Llamadas por temporada | Plan free (100/día) |
|------|----------------------|---------------------|
| Por defecto | ~13-33 | OK para varias ligas |
| Con `--with-per-fixture` | +3×F (F ≈ 380 PL) | ~1140 extra = 12 días |

Usa `--limit-fixtures N` para acotar el costo durante pruebas.

#### Comandos

```powershell
# Dry-run: muestra que se importaria sin tocar DuckDB
python scripts/backfill_history_api.py --league 39 --season 2024 --dry-run

# Backfill real de una temporada cerrada
python scripts/backfill_history_api.py --league 39 --season 2024

# Prueba rapida: solo los primeros 5 fixtures
python scripts/backfill_history_api.py --league 39 --season 2024 --limit-fixtures 5

# Con llamadas por fixture (injuries/predictions/stats)
python scripts/backfill_history_api.py --league 39 --season 2024 --with-per-fixture --limit-fixtures 10
```

#### Idempotencia

Todos los inserts usan `INSERT OR IGNORE`. Re-ejecutar el mismo comando no
duplica registros. La constraint de unicidad por tabla:

| Tabla | Constraint única |
|-------|-----------------|
| `fixtures_history` | `provider_fixture_id` |
| `standings_history` | `(provider_league_id, season, team_id, snapshot_date)` |
| `team_stats_history` | `(provider_league_id, season, team_id, snapshot_date)` |

#### Mapeo de datos

| Columna DuckDB | Fuente API-Football |
|----------------|---------------------|
| `fixtures_history.id` | `fixture.id` (provider ID, no Supabase) |
| `fixtures_history.goals_home` | `goals.home` |
| `standings_history.won` | `all.win` |
| `standings_history.snapshot_date` | `season_end` del /leagues o `{season}-07-31` |
| `team_stats_history.losses` | `fixtures.loses.total` (typo en API) |
| `team_stats_history.raw_stats` | Respuesta completa de /teams/statistics (JSON) |

#### Eventos de log

| Evento | Cuándo |
|--------|--------|
| `[validating_league_season]` | Inicio de validación |
| `[coverage_checked]` | Coverage leída de API |
| `[fixtures_fetched]` | Fixtures recibidos de API |
| `[standings_saved]` | Standings insertados en DuckDB |
| `[stats_saved]` | Stats de equipo insertados |
| `[skipped_no_coverage]` | Endpoint omitido por coverage=false |
| `[skipped_current_season]` | Temporada aún activa, cancelado |
| `[injuries_saved]` | Injuries fetched (no persistidas en esta fase) |
| `[predictions_saved]` | Predictions fetched (no persistidas en esta fase) |
| `[completed_backfill]` | Resumen final |

### Próximas fases

- **Fase 5**: Backtesting del modelo con datos históricos reales
- **Fase 6**: Dashboard de métricas de rendimiento

---

## Retención de datos

### Tablas permanentes (nunca se borran)

| Tabla | Razón |
|-------|-------|
| `competitions` | Catálogo de ligas |
| `competition_seasons` | Temporadas por liga |
| `teams` | Catálogo de equipos |
| `venues` | Estadios |
| `ref_bookmakers` | Referencia de bookmakers |
| `ref_bet_types` | Referencia de mercados |
| `tracked_competitions` | Configuración de sync por tier |
| `bot_users` | Usuarios autorizados del bot |

### Tablas operativas con TTL

| Tabla | TTL por defecto | Variable de entorno |
|-------|-----------------|---------------------|
| `fixtures` | 3 días | `RETENTION_FIXTURES_DAYS` |
| `odds_snapshots` | 3 días | `RETENTION_ODDS_DAYS` |
| `fixture_contexts` | 3 días | `RETENTION_CONTEXT_DAYS` |
| `pick_candidates` | 3 días (solo huérfanos) | `RETENTION_CANDIDATES_DAYS` |
| `published_picks` | 45 días | `RETENTION_PUBLISHED_PICKS_DAYS` |
| `pick_results` | 90 días (resueltos) | `RETENTION_SETTLEMENT_DAYS` |
| `api_sync_runs` | 14 días | `RETENTION_SYNC_RUNS_DAYS` |
| `api_usage_snapshots` | 14 días | `RETENTION_USAGE_DAYS` |
| `h2h_cache` | 30 días | `RETENTION_H2H_DAYS` |
| `team_competition_metrics` | 30 días | `RETENTION_TEAM_METRICS_DAYS` |
| `market_availability_cache` | 30 días | `RETENTION_MARKET_CACHE_DAYS` |
| `cron.job_run_details` | 14 días | `RETENTION_CRON_HISTORY_DAYS` |

> `pick_candidates` solo se borran cuando ya no tienen `published_picks` ni `pick_results` asociados.
> `pick_results` con `result_status = 'pending'` nunca se borran automáticamente.

### Frecuencia de limpieza

El cron nocturno corre una vez al día a las **03:10 Bogota (08:10 UTC)**.
No hay resets durante `sync_today.py` ni `sync_reference.py`.

---

### Pasos para activar la retención

#### 1. Aplicar migración 013

En **Supabase → SQL Editor**:

```sql
-- Copiar y pegar el contenido de:
sql/migrations/013_retention_cleanup.sql
```

Crea las funciones `cleanup_retention()` y `cleanup_retention_preview()`.

#### 2. Script manual

```bash
# Preview: ver cuántas filas se eliminarían (sin tocar nada)
python scripts/cleanup.py --dry-run

# Limpieza real
python scripts/cleanup.py
```

Los valores de TTL se leen automáticamente de `.env`.

#### 3. Cron nocturno (Supabase SQL Editor)

```sql
-- Copiar y pegar el contenido de:
sql/cron_schedule_cleanup.sql
```

Crea el job `football-bot-cleanup-nightly` que corre a las 08:10 UTC.

Verificar que el job está activo:
```sql
SELECT jobid, jobname, schedule, active FROM cron.job;
```

> pg_cron requiere que la extensión esté habilitada en tu proyecto Supabase.
> En Supabase Free, pg_cron puede no estar disponible — usa el script manual con Task Scheduler o cron local.

#### 4. Archivado local (opcional)

Exporta filas antiguas a Parquet en tu PC antes de borrarlas de Supabase.

```bash
# Instalar dependencia
pip install duckdb

# Activar en .env
LOCAL_ARCHIVE_ENABLED=true
LOCAL_ARCHIVE_DIR=./data/archive
LOCAL_ARCHIVE_BEFORE_DELETE=false   # true = borra de Supabase tras archivar

# Preview
python scripts/archive_local.py --dry-run

# Archivar
python scripts/archive_local.py
```

Archivos generados: `data/archive/<tabla>/<YYYY-MM-DD>.parquet`

---

### Qué revisar si Supabase sigue creciendo

1. Verificar que la migración 013 está aplicada:
   ```sql
   SELECT routine_name FROM information_schema.routines
   WHERE routine_name IN ('cleanup_retention', 'cleanup_retention_preview');
   ```

2. Ver cuántas filas hay por tabla:
   ```sql
   SELECT 'odds_snapshots', COUNT(*) FROM odds_snapshots
   UNION ALL SELECT 'fixture_contexts', COUNT(*) FROM fixture_contexts
   UNION ALL SELECT 'fixtures', COUNT(*) FROM fixtures
   UNION ALL SELECT 'pick_candidates', COUNT(*) FROM pick_candidates;
   ```

3. Revisar historial del cron:
   ```sql
   SELECT start_time, end_time, status, return_message
   FROM cron.job_run_details ORDER BY start_time DESC LIMIT 10;
   ```

4. Forzar cleanup manual:
   ```sql
   SELECT * FROM cleanup_retention();
   ```

---

### Borrar filas vs. reclamar espacio vs. VACUUM

| Operación | Qué hace | Cuándo |
|-----------|----------|--------|
| `DELETE` (cleanup) | Marca filas como borradas; el espacio **no** se libera inmediatamente | En cada cleanup |
| `VACUUM` | Reclama el espacio marcado; no bloquea lecturas | Automático en Supabase Free (autovacuum) |
| `VACUUM FULL` | Reescribe la tabla y devuelve espacio al SO; bloquea la tabla | Solo en emergencias, con cuidado |
| `ANALYZE` | Actualiza estadísticas del query planner | Automático o post-cleanup masivo |

En Supabase Free, **autovacuum está activo** — no necesitas correr `VACUUM` manualmente.
Después de borrados masivos, el autovacuum recupera el espacio en los siguientes minutos.

---

## Diagnóstico rápido

### Primer comando a ejecutar

Antes de cualquier otra cosa, verifica que las dos integraciones externas funcionen:

```bash
python scripts/check_supabase.py       # Verifica Supabase + schema
python scripts/check_api_football.py   # Verifica API-Football + credenciales
```

---

### Error: `Error/Missing application key`

Este error lo devuelve API-Football cuando la key es inválida, está vacía o falta en el header.

**Pasos para diagnosticar:**

1. Verifica que `.env` existe en la raíz del proyecto (al lado de `requirements.txt`):
   ```bash
   ls .env
   ```

2. Verifica que `API_FOOTBALL_KEY` tiene un valor real (no `<COMPLETAR>`):
   ```bash
   python -c "from dotenv import load_dotenv; load_dotenv(); import os; k=os.getenv('API_FOOTBALL_KEY',''); print('OK:', k[-4:] if len(k)>=4 else 'VACÍA')"
   ```

3. Ejecuta el check oficial:
   ```bash
   python scripts/check_api_football.py
   ```
   Debe mostrar `✓ Conectado correctamente`. Si muestra `✗`, la key en `.env` no es válida.

4. Si la key parece correcta pero falla: copia la key directamente desde
   [dashboard.api-football.com](https://dashboard.api-football.com) y pégala de nuevo en `.env`.
   Asegúrate de que no tenga espacios al inicio ni al final.

---

### Cómo validar que `.env` cargó correctamente

```bash
python -c "
from app.core.config import settings
print('API key (últimos 4):', settings.masked_api_key)
print('Supabase URL:', settings.supabase_url[:30] + '...')
print('Timezone:', settings.default_timezone)
"
```

Si aparece `(not set)` para algún campo, el `.env` no tiene esa variable o tiene el placeholder `<COMPLETAR>`.

---

### Cómo probar API-Football manualmente

```bash
# Sin fixtures de ejemplo (solo credenciales):
python scripts/check_api_football.py

# Con fixtures de una liga específica:
python scripts/check_api_football.py --league 39 --season 2025
```

---

### Cómo probar Supabase

```bash
python scripts/check_supabase.py
```

Verifica conexión, tablas requeridas y muestra conteos actuales.

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| `Error/Missing application key` | `API_FOOTBALL_KEY` inválida o vacía | Ver sección Diagnóstico arriba |
| `/hoy` sin picks | No se corrió Phase C o no hay odds | `sync_today.py` |
| Phase C: "tracked_competitions vacío" | No se corrió el seed | Ejecutar `seed_tracked_competitions.sql` |
| Phase B: "no hay ligas" | `DEFAULT_LEAGUE_IDS` vacío | Configurar en `.env` |
| Odds sin picks | Edge < `MIN_EDGE` o confianza < `MIN_CONFIDENCE` | Revisar thresholds en `.env` |
| `/estado` muestra schema faltante | Migración 010 no aplicada | Ejecutar en Supabase SQL Editor |
| `42P10` en `upsert_bet_types` | `on_conflict` usaba solo `provider_bet_id`; la constraint es `(provider_bet_id, scope)` | Corregido en código — sin migración necesaria |
