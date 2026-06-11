# Quiniela Mundialista 2026 — Scaffold & Tasks para Codex

## Contexto operativo inmediato

El Mundial inicia con estos partidos prioritarios:

- Jueves 11 de junio de 2026
  - México vs Sudáfrica — Grupo A — Estadio Ciudad de México
  - República de Corea vs República Checa — Grupo A — Estadio Guadalajara
- Viernes 12 de junio de 2026
  - Canadá vs Bosnia y Herzegovina — Grupo B — Estadio Toronto
  - Estados Unidos vs Paraguay — Grupo D — Estadio Los Ángeles

Objetivo urgente: implementar Fase 1 y Fase 2 para poder generar predicciones de marcador exacto para los partidos del 11 de junio, usando datos históricos recientes y permitiendo actualización con alineaciones confirmadas 30–60 minutos antes del kickoff.

---

## Principios de implementación

- Proyecto personal, local-first.
- Costo de implementación: $0 MXN si lo construimos entre usuario + ChatGPT/Codex.
- API-Football Free como primera opción.
- Límite operativo: 100 requests/día.
- Usar cache local para no repetir llamadas.
- SQLite como base de datos.
- Scripts simples, sin dashboard.
- Salida mínima: marcador exacto recomendado.
- Soportar captura manual de alineaciones si la API no trae lineups a tiempo.
- No hacer polling constante. Consultar API por día y por partido cerca del kickoff.

---

## Estructura del proyecto

Crear este scaffold:

```text
quiniela-mundial-2026/
  README.md
  .env.example
  .gitignore
  requirements.txt
  pyproject.toml
  data/
    raw/
      calendario_mundial.md
      seleccionados_mundialistas.md
    processed/
      calendar.csv
      rosters.csv
    db/
      quiniela.db
  notebooks/
    01_data_quality.ipynb
    02_model_sanity_checks.ipynb
  outputs/
    predictions/
      predicciones_fase_grupos.csv
      predicciones_2026-06-11.csv
    logs/
      api_usage.csv
      data_quality_report.md
  src/
    quiniela/
      __init__.py
      config.py
      db.py
      logging_utils.py
      name_maps.py
      calendar_parser.py
      roster_parser.py
      api_football_client.py
      cache.py
      historical_loader.py
      lineup_loader.py
      odds_loader.py
      features.py
      ratings.py
      poisson_model.py
      predictor.py
      cli.py
  scripts/
    01_ingest_calendar.py
    02_ingest_rosters.py
    03_init_db.py
    04_fetch_priority_history.py
    05_fetch_today_data.py
    06_predict_match.py
    07_update_after_match.py
    08_report_api_usage.py
  tests/
    test_calendar_parser.py
    test_roster_parser.py
    test_poisson_model.py
```

---

## Dependencias iniciales

Crear `requirements.txt`:

```txt
pandas
numpy
scipy
scikit-learn
requests
python-dotenv
pydantic
typer
rich
unidecode
rapidfuzz
pytest
```

---

## Variables de entorno

Crear `.env.example`:

```bash
API_FOOTBALL_KEY=
API_FOOTBALL_HOST=v3.football.api-sports.io
API_DAILY_LIMIT=100
DB_PATH=data/db/quiniela.db
LOCAL_TIMEZONE=America/Mexico_City
```

---

# Fase 1 — Ingesta, normalización y base de datos

## 1.1 Inicializar proyecto

- [ ] Crear estructura de carpetas.
- [ ] Crear entorno virtual.
- [ ] Instalar dependencias.
- [ ] Crear `.env.example`.
- [ ] Crear `.gitignore`.
- [ ] Crear `README.md` con instrucciones básicas.

Comandos esperados:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 1.2 Copiar archivos base

- [ ] Copiar `calendario_mundial.md` a `data/raw/calendario_mundial.md`.
- [ ] Copiar `seleccionados_mundialistas.md` a `data/raw/seleccionados_mundialistas.md`.
- [ ] No modificar manualmente los archivos raw.
- [ ] Los archivos limpios deben guardarse en `data/processed/`.

---

## 1.3 Parser de calendario

Implementar `src/quiniela/calendar_parser.py`.

Debe:

- [ ] Leer `data/raw/calendario_mundial.md`.
- [ ] Extraer solo fase de grupos.
- [ ] Ignorar secciones de dieciseisavos en adelante.
- [ ] Extraer:
  - fecha
  - hora original ET
  - hora CDMX
  - equipo local
  - equipo visitante
  - grupo
  - estadio
  - matchday aproximado si aplica
- [ ] Convertir horario ET a America/Mexico_City.
- [ ] Guardar `data/processed/calendar.csv`.
- [ ] Preservar nombres originales y nombres normalizados.

Notas:

- En junio, Eastern Time suele estar en UTC-4 y CDMX en UTC-6.
- La conversión esperada general es ET - 2 horas.
- Aun así, usar `zoneinfo` de Python para evitar errores.

Columnas sugeridas:

```csv
match_id,date_et,time_et,datetime_et,date_cdmx,time_cdmx,datetime_cdmx,home_team,away_team,home_team_norm,away_team_norm,group,stadium,stage,status
```

Criterio de aceptación:

- [ ] Deben existir 72 partidos de fase de grupos.
- [ ] Los partidos del 11 de junio deben quedar:
  - México vs Sudáfrica
  - República de Corea vs República Checa
- [ ] Los partidos del 12 de junio deben quedar:
  - Canadá vs Bosnia y Herzegovina
  - Estados Unidos vs Paraguay

---

## 1.4 Parser de convocados

Implementar `src/quiniela/roster_parser.py`.

Debe:

- [ ] Leer `data/raw/seleccionados_mundialistas.md`.
- [ ] Extraer selecciones.
- [ ] Extraer jugadores por posición:
  - portero/arquero
  - defensa
  - mediocampista
  - delantero
- [ ] Extraer club cuando venga entre paréntesis.
- [ ] Extraer entrenador.
- [ ] Guardar `data/processed/rosters.csv`.

Columnas sugeridas:

```csv
team,team_norm,player,player_norm,position_group,club,coach,is_active
```

Criterio de aceptación:

- [ ] Deben existir jugadores para México, Sudáfrica, República de Corea y República Checa.
- [ ] Debe mapear "República de Corea" con "South Korea" / "Corea del Sur".
- [ ] Debe mapear "República Checa" con "Czech Republic" / "Chequia".

---

## 1.5 Normalización de nombres

Implementar `src/quiniela/name_maps.py`.

Debe incluir un diccionario inicial:

```python
TEAM_NAME_MAP = {
    "México": "mexico",
    "Mexico": "mexico",
    "Sudáfrica": "south_africa",
    "South Africa": "south_africa",
    "República de Corea": "south_korea",
    "Corea del Sur": "south_korea",
    "South Korea": "south_korea",
    "República Checa": "czech_republic",
    "Chequia": "czech_republic",
    "Czech Republic": "czech_republic",
    "Canadá": "canada",
    "Canada": "canada",
    "Bosnia y Herzegovina": "bosnia_herzegovina",
    "Bosnia & Herzegovina": "bosnia_herzegovina",
    "Estados Unidos": "usa",
    "USA": "usa",
    "United States": "usa",
    "Paraguay": "paraguay"
}
```

También implementar:

- [ ] `normalize_text(value: str) -> str`
- [ ] `normalize_team_name(value: str) -> str`
- [ ] fallback con `unidecode` + lower + replace spaces.

---

## 1.6 Base de datos SQLite

Implementar `src/quiniela/db.py` y `scripts/03_init_db.py`.

Tablas mínimas:

```sql
teams
players
matches
api_cache
api_usage
historical_matches
historical_lineups
historical_team_stats
historical_player_stats
odds_snapshots
predictions
actual_results
```

Criterio de aceptación:

- [ ] `python scripts/03_init_db.py` crea `data/db/quiniela.db`.
- [ ] `matches` contiene los 72 partidos de fase de grupos.
- [ ] `players` contiene convocados de todas las selecciones disponibles.
- [ ] `api_usage` puede registrar cada request.

---

## 1.7 Cliente API-Football con cache

Implementar `src/quiniela/api_football_client.py` y `src/quiniela/cache.py`.

Debe:

- [ ] Leer API key desde `.env`.
- [ ] Registrar cada request en `api_usage`.
- [ ] Respetar `API_DAILY_LIMIT`.
- [ ] Cachear respuesta por endpoint + params.
- [ ] No repetir llamadas si ya existen en cache.
- [ ] Permitir modo `--dry-run`.

Funciones sugeridas:

```python
get_fixtures(...)
get_fixture_by_id(...)
get_fixture_lineups(fixture_id)
get_fixture_statistics(fixture_id)
get_fixture_events(fixture_id)
get_odds(fixture_id)
get_team_fixtures(team_id, from_date, to_date)
```

Criterio de aceptación:

- [ ] Si el cache existe, no consume request.
- [ ] Si se llega a 100 requests en el día, aborta con mensaje claro.
- [ ] Cada respuesta se guarda en `api_cache`.

---

# Fase 2 — Backfill urgente + modelo predictivo inicial

## 2.1 Selecciones prioritarias para 11 de junio

Prioridad absoluta:

- [ ] México
- [ ] Sudáfrica
- [ ] República de Corea / Corea del Sur
- [ ] República Checa / Chequia

Crear script `scripts/04_fetch_priority_history.py`.

Debe:

- [ ] Buscar IDs de equipos en API-Football.
- [ ] Obtener partidos recientes de los últimos 6 meses.
- [ ] Guardar resultados.
- [ ] Guardar lineups históricas si existen.
- [ ] Guardar estadísticas del partido si existen.
- [ ] Guardar odds históricas/pre-match si existen.
- [ ] Respetar límite de 100 requests/día.
- [ ] Crear reporte de faltantes.

Estrategia de requests urgente:

1. Obtener team IDs de las 4 selecciones.
2. Obtener fixtures recientes por selección.
3. Elegir máximo 5–8 partidos recientes por selección.
4. Para cada fixture elegido, pedir:
   - resultado/eventos
   - lineups
   - estadísticas
5. Saltar odds históricas si compromete el límite diario.

Criterio de aceptación:

- [ ] Tener al menos 5 partidos recientes por selección si la API los ofrece.
- [ ] Si no hay 5, guardar lo que exista y marcar faltantes.
- [ ] No superar 100 requests.

---

## 2.2 Features mínimas

Implementar `src/quiniela/features.py`.

Features por equipo:

- [ ] goles a favor promedio últimos N partidos.
- [ ] goles en contra promedio últimos N partidos.
- [ ] diferencia de goles promedio.
- [ ] porcentaje de victorias.
- [ ] clean sheets.
- [ ] rating simple de forma.
- [ ] ajuste por localía/sede.
- [ ] fuerza del XI si hay alineación disponible.
- [ ] ajuste de odds si hay odds.

Features por jugador, versión mínima:

- [ ] titularidad reciente.
- [ ] minutos recientes si existen.
- [ ] goles/asistencias si existen.
- [ ] posición.

Criterio de aceptación:

- [ ] Generar un dataframe por partido con features para local y visitante.
- [ ] Soportar ausencia de datos con defaults razonables.

---

## 2.3 Rating inicial

Implementar `src/quiniela/ratings.py`.

Versión mínima:

- [ ] Rating base por selección.
- [ ] Ajuste por resultados recientes.
- [ ] Ajuste por fuerza del rival cuando sea posible.
- [ ] Default para equipos con poca data.

Regla simple inicial:

```text
team_strength = base_rating
              + recent_form_score
              + goal_difference_score
              + lineup_score
              + odds_score
```

Criterio de aceptación:

- [ ] Cada selección prioritaria tiene `team_strength`.
- [ ] El rating se puede recalcular después de cada partido.

---

## 2.4 Modelo Poisson inicial

Implementar `src/quiniela/poisson_model.py`.

Debe:

- [ ] Estimar lambda de goles para local.
- [ ] Estimar lambda de goles para visitante.
- [ ] Generar probabilidades para marcadores 0-0 a 5-5.
- [ ] Seleccionar el marcador con mayor probabilidad.
- [ ] Evitar lambdas extremas.
- [ ] Ajustar hacia marcadores mundialistas comunes: 0-0, 1-0, 1-1, 2-0, 2-1.

Criterio de aceptación:

- [ ] `predict_score(home, away)` devuelve `(home_goals, away_goals, probability)`.
- [ ] Nunca devuelve valores negativos.
- [ ] Output simple: `México 1-1 Sudáfrica`.

---

## 2.5 Predictor CLI

Implementar `src/quiniela/predictor.py` y `scripts/06_predict_match.py`.

Debe aceptar:

```bash
python scripts/06_predict_match.py --date 2026-06-11
python scripts/06_predict_match.py --home "México" --away "Sudáfrica"
```

Debe devolver:

```text
México 1-1 Sudáfrica
República de Corea 1-1 República Checa
```

También guardar en:

```text
outputs/predictions/predicciones_2026-06-11.csv
```

Columnas:

```csv
datetime_cdmx,group,home_team,away_team,predicted_score,model_version,generated_at
```

---

## 2.6 Operación del 11 de junio

Secuencia urgente:

```bash
python scripts/01_ingest_calendar.py
python scripts/02_ingest_rosters.py
python scripts/03_init_db.py
python scripts/04_fetch_priority_history.py --teams "México,Sudáfrica,República de Corea,República Checa"
python scripts/05_fetch_today_data.py --date 2026-06-11
python scripts/06_predict_match.py --date 2026-06-11
```

Antes de cada partido:

```bash
python scripts/05_fetch_today_data.py --date 2026-06-11 --lineups-only
python scripts/06_predict_match.py --home "México" --away "Sudáfrica"
```

Después del partido:

```bash
python scripts/07_update_after_match.py --home "México" --away "Sudáfrica"
```

---

## 2.7 Operación del 12 de junio

Después de generar predicciones del 11, preparar:

- [ ] Canadá
- [ ] Bosnia y Herzegovina
- [ ] Estados Unidos
- [ ] Paraguay

Comando:

```bash
python scripts/04_fetch_priority_history.py --teams "Canadá,Bosnia y Herzegovina,Estados Unidos,Paraguay"
python scripts/05_fetch_today_data.py --date 2026-06-12
python scripts/06_predict_match.py --date 2026-06-12
```

---

# Definition of Done urgente

La Fase 1 + Fase 2 quedan listas cuando:

- [ ] Se puede cargar el calendario corregido.
- [ ] Se puede cargar la lista de convocados.
- [ ] Se crea `quiniela.db`.
- [ ] Se respeta el límite de 100 requests/día.
- [ ] Se descargan datos históricos para las 4 selecciones del 11 de junio.
- [ ] Se generan predicciones para:
  - México vs Sudáfrica
  - República de Corea vs República Checa
- [ ] Se genera un CSV en `outputs/predictions/`.
- [ ] Si no hay API key, el sistema corre con datos manuales/defaults y avisa qué falta.

---
