# Multi-Tournament Design

## Estado actual

El proyecto esta optimizado para Mundial 2026:

- `calendar_parser.py` parsea markdown con grupos A-L y etiquetas en espanol.
- `roster_parser.py` parsea plantillas de selecciones nacionales.
- `name_maps.py` contiene `TEAM_SPECS` fijo para equipos nacionales.
- `db.py` no tiene `competition_id` ni `season_id` en entidades principales.
- `output_manager.py` publica artefactos con nombres estables bajo `outputs/`.

Este diseno permite evolucionar a multiples competiciones sin romper el flujo actual.

## Objetivo

Introducir una capa de dominio multi-torneo basada en configuracion, con compatibilidad total para Mundial 2026 como competicion default.

## Entidades propuestas

### `Competition`

Representa una competicion recurrente o producto deportivo.

Campos:

- `competition_id`
- `slug`
- `name`
- `sport`
- `organizer`
- `competition_type`: national_teams | clubs | mixed
- `default_timezone`
- `rules_profile`

Ejemplos: `fifa_world_cup`, `uefa_champions_league`, `liga_mx`.

### `Season`

Instancia temporal de una competicion.

Campos:

- `season_id`
- `competition_id`
- `slug`
- `name`
- `start_date`
- `end_date`
- `timezone`
- `status`

Ejemplos: `fifa_world_cup_2026`, `ucl_2026_2027`.

### `TournamentStage`

Fase de competencia.

Campos:

- `stage_id`
- `season_id`
- `stage_key`
- `name`
- `stage_type`: group | league | knockout | playoff
- `order_index`
- `rules_profile`

### `Participant`

Equipo participante en una season.

Campos:

- `participant_id`
- `season_id`
- `team_id`
- `display_name`
- `short_name`
- `group_key`
- `seed`
- `active`

`team_id` debe representar la identidad estable del equipo, mientras `participant_id` representa su participacion en una season.

### `Squad`

Lista de jugadores asociada a un participante y ventana temporal.

Campos:

- `squad_id`
- `participant_id`
- `source`
- `as_of_date`
- `status`
- `players`

En clubes, un jugador puede cambiar de equipo entre seasons. En selecciones, puede cambiar la convocatoria sin cambiar el equipo nacional.

### `CompetitionRules`

Politica configurable para interpretacion y validacion.

Campos:

- `points_win`
- `points_draw`
- `points_loss`
- `extra_time_policy`
- `penalties_policy`
- `squad_size`
- `substitution_rules`
- `home_away_policy`
- `neutral_venue_policy`
- `tiebreakers`

## Configuracion YAML

La configuracion inicial existe en `configs/competitions/` y es leida por
`quiniela.infrastructure.competition_config` sin llamadas de red ni dependencias externas de YAML. El parser
acepta un subconjunto deliberadamente pequeno: mapas anidados, strings, enteros, booleanos y fechas ISO.

Archivos iniciales:

- `world_cup_2026.yaml`: default operativo del proyecto.
- `champions_league_2026_2027.yaml`: ejemplo documentado, no activo.
- `liga_mx_apertura_2026.yaml`: ejemplo documentado, no activo.

Contrato:

```yaml
competition:
  slug: fifa_world_cup
  name: FIFA World Cup
  sport: football
  organizer: FIFA
  competition_type: national_teams
  default_timezone: America/Mexico_City

season:
  slug: world_cup_2026
  name: FIFA World Cup 2026
  start_date: 2026-06-11
  end_date: 2026-07-19
  timezone: America/Mexico_City
  status: planned

rules:
  profile: fifa_world_cup_group_knockout
  points_win: 3
  points_draw: 1
  points_loss: 0
  neutral_venue_policy: required
  extra_time_policy: knockout_only
  penalties_policy: knockout_only

data_sources:
  calendar:
    parser: world_cup_markdown
    input: data/raw/calendario_mundial.md
  rosters:
    parser: national_team_markdown
    input: data/raw/seleccionados_mundialistas.md

api_football:
  enabled: true
  league_id: 1
  season: 2026

outputs:
  namespace: world-cup-2026
  stable_aliases: true
```

La configuracion describe fuentes, reglas, proveedor y outputs. No debe contener secretos, tokens, API keys,
topicos ni webhooks. Las configs de ejemplo quedan `enabled: false` para API-Football y `stable_aliases:
false` para evitar que se confundan con el default del Mundial.

## Namespacing de datos

### Base de datos

Migracion futura por fases:

1. Crear tablas `competitions`, `seasons`, `competition_rules`, `participants`, `squads`.
2. Crear una season default `fifa_world_cup_2026`.
3. Backfill de `teams`, `players`, `matches`, `predictions`, `odds_*`, `notifications` hacia la season default.
4. Agregar `competition_id` y `season_id` en tablas operativas.
5. Mantener vistas o compatibilidad para scripts existentes.

Regla de compatibilidad:

- Si no se pasa competition/season, el sistema usa Mundial 2026.
- Ningun script actual debe requerir argumentos nuevos durante la transicion.

### Archivos de salida

Estructura futura:

```text
outputs/
  world-cup-2026/
    predictions/
    reports/
    public/
  shared/
    logs/
```

Compatibilidad:

- Mantener aliases actuales como `outputs/predictions_latest.*`.
- Publicar nuevos artefactos namespaced antes de mover consumidores.

### Cache y APIs

La cache debe incluir contexto de competicion cuando el endpoint dependa de torneo, liga o temporada. Endpoints globales pueden seguir compartidos.

Ejemplos:

- Global: metadata de paises, timezone, health checks.
- Especifico: fixtures, standings, squads, injuries, odds por liga/season.

## Aprendizaje global vs especifico

### Global

Puede compartirse entre competiciones:

- Confiabilidad de API y retry policy.
- Bookmaker reliability inicial.
- Normalizacion de odds y overround.
- Calibracion general de mercados liquidos.
- Parseo y validacion generica de fechas.
- Observabilidad y contratos de notificacion.

### Especifico de competicion

Debe calibrarse por season o competition:

- Fuerza de equipos.
- Localia y neutralidad.
- Ritmo de goles.
- Reglas de prorroga/penales.
- Convocatorias y minutos de jugadores.
- Cobertura de fuentes.
- Pesos de ensamble.

### Especifico de participante

Debe quedar atado a `participant_id` cuando dependa de una season:

- Plantel activo.
- Lesiones y suspensiones.
- Forma reciente.
- Entrenador y cambios tacticos.

## Cambios esperados por modulo

`calendar_parser.py`:

- Extraer parser World Cup como estrategia.
- Recibir `CompetitionConfig`.
- Emitir `season_id`, `stage_key`, `participant` normalizado.

`roster_parser.py`:

- Separar parser de markdown nacional de contrato de squad.
- Permitir squads por clubes y selecciones.

`name_maps.py`:

- Mover `TEAM_SPECS` a catalogo o config.
- Mantener aliases por competicion para resolver colisiones.

`db.py`:

- Mantener facade temporal.
- Agregar migraciones compatibles por default season.

`output_manager.py`:

- Aceptar namespace opcional.
- Conservar nombres estables existentes como aliases.

## Agentes y migracion

Un documento operativo para agentes puede crearse cuando se implemente la primera migracion real. Por ahora, las reglas minimas son:

- No introducir competiciones nuevas sin config declarativa.
- No cambiar el default Mundial 2026.
- No mezclar datos de seasons en tablas o archivos sin namespace.
- Todo cambio de schema debe incluir backfill y rollback.

## Riesgos

- Colisiones de nombres entre clubes y selecciones.
- Diferentes reglas de torneo pueden cambiar evaluaciones y standings.
- Salidas namespaced pueden romper consumidores si se eliminan aliases.
- Modelos entrenados para Mundial pueden no generalizar a ligas de clubes.

## Rollback

Mientras sea solo diseno:

- Revertir este documento y su handoff.

En implementacion futura:

- Mantener `season_id` nullable hasta backfill verificado.
- Conservar scripts sin argumentos nuevos.
- Mantener aliases de salida.
- Feature flag para activar namespace por competicion.
