# Architecture Audit - Quiniela Mundial 2026

**Task:** TASK-001 - Auditoria read-only del repositorio
**Fecha:** 2026-06-19
**Alcance:** Inventario de modulos, scripts, tests, acoplamientos y brechas contra arquitectura hexagonal.
**Estado:** Read-only audit. No se modifico codigo fuente.

## 1. Resumen

El proyecto esta funcionalmente avanzado y cuenta con una suite offline robusta. La arquitectura actual es un paquete Python plano en `src/quiniela`, con scripts wrapper en `scripts/` y una CLI Typer central en `src/quiniela/cli.py`.

La brecha principal no es funcional. La brecha es de separacion de responsabilidades: dominio, aplicacion, puertos, adaptadores outbound, adaptadores inbound, persistencia, scheduler, notificaciones, outputs y model governance viven mezclados en modulos funcionales grandes.

La migracion recomendada debe ser incremental, compatible y protegida por tests. No conviene mover `db.py`, `historical_loader.py`, `notifications.py` o `player_evidence.py` completos en una sola task.

## 2. Estado estructural

### Paquetes actuales

No existen todavia los paquetes objetivo:

```text
src/quiniela/domain/
src/quiniela/application/
src/quiniela/ports/
src/quiniela/adapters/
src/quiniela/infrastructure/
```

El codigo vive actualmente en:

```text
src/quiniela/*.py
```

### Tooling

`pyproject.toml` declara:

- build backend con setuptools;
- `requires-python = ">=3.11"`;
- configuracion de pytest con `pythonpath = ["src"]`.

No hay configuracion actual para:

- ruff;
- ruff format;
- mypy;
- pytest-cov;
- pre-commit;
- tests de layering.

### Entorno observado

En TASK-000 el entorno activo reporto Python 3.9.7 aunque el proyecto declara Python 3.11+. Este punto debe tratarse como riesgo operativo antes de endurecer typing/tooling.

## 3. Inventario de modulos

| Modulo | Lineas | Responsabilidad real | Capa implicita actual | Acoplamientos principales |
|---|---:|---|---|---|
| `db.py` | 1816 | Schema SQLite, migraciones, seed, cache API, repositorios implicitos, automation runs, snapshots, predictions, releases | Persistencia + repositorios + migraciones | `sqlite3`, pandas, settings, name maps |
| `html_report.py` | 1419 | Queries, transformacion y render HTML | Output adapter + queries + presentacion | `sqlite3`, `get_connection`, HTML string building |
| `notifications.py` | 1212 | Outbox, scheduling, templates, ntfy, Discord, retry, test messages | Application + notification adapters + repositories | `sqlite3`, `requests`, settings |
| `player_evidence.py` | 1158 | Snapshots, targets, entrenamiento, gates, releases, model governance | Application + domain rules + persistence + filesystem | `sqlite3`, sklearn, pickle, filesystem |
| `historical_loader.py` | 935 | Backfill, refresh, API-Football, validacion de fixtures, almacenamiento | Use cases + outbound adapter + repositories | `APIFootballClient`, `db.py`, pandas |
| `player_model.py` | 789 | Features individuales, resolucion de jugadores, entrenamiento Poisson v1, impactos | Domain/application + persistence + ML | `sqlite3`, sklearn, pickle, db writes |
| `output_manager.py` | 728 | CSV/JSON/HTML outputs, evaluacion, logs, cleanup | Application + output adapter + reporting | `sqlite3`, filesystem, html_report |
| `odds_loader.py` | 563 | Fetch odds, normalizacion, devig, consenso, ajuste de matriz | Application + outbound adapter + domain math | `APIFootballClient`, `sqlite3`, numpy |
| `web_lineup_fallback.py` | 520 | RSS/news fetch, HTML text extraction, lineup estimate | Outbound news adapter + application fallback | `requests`, `sqlite3`, RSS/XML |
| `cli.py` | 453 | Typer CLI, wiring de comandos, apertura DB | Inbound adapter + composition root parcial | Typer, Rich, `get_connection`, modulos internos |
| `outcome_model.py` | 453 | Logit 1-X-2, validation metrics, report artifact | ML/domain service + filesystem | sklearn, pandas, `sqlite3` |
| `matchday.py` | 344 | Planificador de jornada, windows prepartido, recovery, orchestration | Application workflow + scheduler logic | `get_connection`, Predictor, historical_loader |
| `features.py` | 299 | Features por equipo/partido, odds adjustment, lineup strength | Domain/application feature service | DB queries, player_model |
| `predictor.py` | 248 | Prediccion, feature assembly, Poisson, Logit, odds, persistence | Application service + domain + repository use | `get_connection`, db writes, odds_loader |
| `public_bundle.py` | 180 | Export/import bundle sanitizado | Output/filesystem adapter + persistence | `sqlite3`, zipfile, shutil |
| `name_maps.py` | 172 | Normalizacion de equipos, aliases, mojibake | Domain utility | unidecode |
| `api_football_client.py` | 152 | Cliente cache-first, cuota, retry, HTTP | Outbound adapter + cache policy | `requests`, `db.py`, settings |
| `config.py` | 116 | Settings desde `.env`, rutas y flags | Infrastructure config | dotenv, os, pathlib |
| `roster_parser.py` | 108 | Parser de convocatorias Markdown | Inbound parser/application input | pandas, name_maps |
| `calendar_parser.py` | 96 | Parser calendario Markdown y zona horaria | Inbound parser/application input | pandas, settings, name_maps |
| `poisson_model.py` | 81 | Matriz Poisson y score exacto | Domain/model service | numpy, scipy, player feature constants |
| `ratings.py` | 52 | Rating simple de equipos | Domain/model service | pandas |
| `logging_utils.py` | 12 | Logger setup | Infrastructure | logging |
| `cache.py` | 9 | JSON canonical y hash de parametros | Infrastructure utility | hashlib/json |
| `lineup_loader.py` | 4 | Wrapper a `fetch_today_data` para lineups | Legacy convenience | historical_loader |
| `__init__.py` | 3 | Version | Package metadata | ninguno |

## 4. Scripts y CLI

Los scripts Python son wrappers del CLI Typer. Todos insertan el comando correspondiente con `sys.argv.insert(1, "<command>")` y agregan `src` al path local.

| Script | Comando CLI |
|---|---|
| `01_ingest_calendar.py` | `ingest-calendar` |
| `02_ingest_rosters.py` | `ingest-rosters` |
| `03_init_db.py` | `init-db` |
| `04_fetch_priority_history.py` | `fetch-history` |
| `05_fetch_today_data.py` | `fetch-today` |
| `06_predict_match.py` | `predict` |
| `07_update_after_match.py` | `update-after-match` |
| `08_report_api_usage.py` | `report-api-usage` |
| `09_export_public_bundle.py` | `export-public-bundle` |
| `10_import_public_bundle.py` | `import-public-bundle` |
| `11_public_bundle_status.py` | `public-bundle-status` |
| `12_render_html_report.py` | `render-html-report` |
| `13_train_player_model.py` | `train-player-model` |
| `14_train_outcome_model.py` | `train-outcome-model` |
| `15_run_matchday.py` | `run-matchday` |
| `16_capture_pre_match_snapshot.py` | `capture-pre-match-snapshot` |
| `17_build_player_targets.py` | `build-player-targets` |
| `18_train_player_evidence.py` | `train-player-evidence` |
| `19_evaluate_model_release.py` | `evaluate-model-release` |
| `20_activate_model_release.py` | `activate-model-release` |
| `21_fetch_web_lineup_fallback.py` | `fetch-web-lineup-fallback` |
| `22_rebuild_outputs.py` | `rebuild-outputs` |
| `23_evaluate_predictions.py` | `evaluate-predictions` |
| `24_cleanup_obsolete_outputs.py` | `cleanup-obsolete-outputs` |
| `25_dispatch_notifications.py` | `dispatch-notifications` |
| `26_test_notifications.py` | `test-notifications` |
| `27_run_notification_cycle.py` | `run-notification-cycle` |
| `28_recover_automation_runs.py` | `recover-automation-runs` |
| `29_fetch_odds.py` | `fetch-odds` |
| `30_build_odds_consensus.py` | `build-odds-consensus` |
| `31_send_lineup_test_notifications.py` | `send-lineup-test-notifications` |

### Scripts PowerShell

| Script | Responsabilidad | Acoplamiento operativo |
|---|---|---|
| `install_matchday_task.ps1` | Instala tarea `Quiniela Mundial 2026 Matchday` | Windows Task Scheduler, Python local o `.venv` |
| `install_notification_task.ps1` | Instala tarea `Quiniela Mundial 2026 Notifications` | Windows Task Scheduler, Python local o `.venv` |
| `uninstall_matchday_task.ps1` | Elimina tarea matchday | Windows Task Scheduler |

La separacion script -> CLI es buena. El problema no esta en los wrappers; esta en que `cli.py` conoce demasiados modulos concretos y abre conexiones SQLite directamente.

## 5. Inventario SQLite

`db.py` crea al menos estas tablas:

```text
teams
players
matches
api_cache
api_usage
historical_matches
historical_lineups
lineup_estimates
historical_team_stats
historical_player_stats
fixture_player_stats
player_season_stats
odds_snapshots
odds_market_snapshots
odds_market_consensus
odds_model_predictions
predictions
actual_results
prediction_player_impacts
prediction_evaluations
automation_runs
notification_deliveries
pre_match_snapshots
pre_match_player_snapshots
player_match_targets
player_evidence_evaluations
player_prediction_evaluations
model_training_runs
model_releases
```

Responsabilidades mezcladas en `db.py`:

- conexion y PRAGMA;
- schema inicial;
- migraciones ligeras;
- seed desde CSV procesados;
- cache API;
- uso de API;
- catastro de equipos/jugadores;
- almacenamiento de payloads deportivos;
- predicciones e impactos;
- automation locks;
- snapshots prepartido;
- targets/evaluaciones de jugadores;
- model releases.

`db.py` debe partirse por familias de repositorios, pero solo despues de crear puertos y tests de compatibilidad.

## 6. Dependencias externas directas

### SQLite

Uso directo o tipos `sqlite3` aparecen en:

- `db.py`
- `html_report.py`
- `notifications.py`
- `odds_loader.py`
- `outcome_model.py`
- `output_manager.py`
- `player_evidence.py`
- `player_model.py`
- `public_bundle.py`
- `web_lineup_fallback.py`

Ademas, muchos modulos importan `get_connection` desde `db.py`, incluyendo `cli.py`, `predictor.py`, `matchday.py`, `html_report.py`, `output_manager.py`, `notifications.py`, `odds_loader.py`, `public_bundle.py` y `web_lineup_fallback.py`.

### HTTP / requests

Uso directo de `requests` aparece en:

- `api_football_client.py` para API-Football;
- `notifications.py` para ntfy/Discord;
- `web_lineup_fallback.py` para RSS/news articles;
- tests con fakes de requests.

En arquitectura objetivo, estos usos deben quedar en adaptadores outbound.

### API-Football

`APIFootballClient` es usado directamente por:

- `historical_loader.py`;
- `odds_loader.py`;
- tests que monkeypatchean el cliente.

La politica cache-first ya vive centralizada en el cliente, pero no existe todavia un puerto `FootballDataProvider`.

### Filesystem

Uso de `Path`, escritura de reportes o artefactos aparece en:

- `config.py`;
- parsers;
- `player_model.py`;
- `outcome_model.py`;
- `player_evidence.py`;
- `output_manager.py`;
- `html_report.py`;
- `public_bundle.py`;
- scripts wrappers.

En la arquitectura objetivo, escritura de outputs, bundles, model registry y reports deben pasar por adaptadores o servicios de infraestructura.

### Notificaciones

`notifications.py` combina:

- outbox en SQLite;
- reglas de scheduling;
- render de payloads;
- adapters ntfy/Discord;
- retry/expiration;
- test messages.

Los datos sensibles se leen desde settings; los tests verifican que no se persistan secretos. Aun asi, la capa necesita separarse en `NotificationGateway`, repositorio de deliveries y use cases.

### Task Scheduler

Task Scheduler solo aparece en scripts PowerShell. Es una buena frontera operativa, pero falta formalizarlo como adapter/instruccion de ops. No debe entrar a dominio ni aplicacion.

## 7. Tests actuales

La suite offline cubre areas importantes:

| Test | Foco |
|---|---|
| `test_api_football_client.py` | retry, cache/client behavior |
| `test_calendar_parser.py` | calendario |
| `test_db_automation.py` | migraciones, automation runs |
| `test_historical_loader.py` | reportes y resultados finalizados |
| `test_historical_loader_strict.py` | validacion estricta de fixtures |
| `test_html_report.py` | render HTML, lineups, odds |
| `test_matchday.py` | planning, idempotencia, degraded outputs |
| `test_notifications.py` | outbox, retry, ntfy/Discord, dedupe |
| `test_odds_loader.py` | odds, consenso y ajuste de matriz |
| `test_outcome_model.py` | Logit e hibridacion |
| `test_output_manager.py` | latest/history, JSON, cleanup, logs |
| `test_player_evidence_db.py` | snapshots inmutables, targets, release activation |
| `test_player_evidence_model.py` | temporal splits, gates, rollback |
| `test_player_model.py` | identidad de jugadores, entrenamiento vacio |
| `test_poisson_model.py` | score Poisson no negativo |
| `test_public_bundle.py` | export/import sanitizado |
| `test_roster_parser.py` | roster parser y normalizacion |
| `test_web_lineup_fallback.py` | fallback RSS auditable |

Brechas de testing:

- no hay tests de layering;
- no hay tests de contratos hexagonales porque no existen paquetes;
- no hay coverage configurado;
- no hay mypy/ruff/pre-commit configurados;
- no hay tests especificos de doctor CLI porque aun no existe.

## 8. Acoplamientos y riesgos principales

### A1 - `db.py` como modulo central multi-responsabilidad

Riesgo: cualquier cambio en schema, migrations, cache, predictions, automation o releases toca el mismo archivo. Esto eleva conflicto entre agentes y dificulta rollback parcial.

Recomendacion: extraer primero conexion/migraciones y repositorios de lectura de bajo riesgo. Mantener wrappers legacy en `db.py` durante la transicion.

### A2 - `cli.py` como composition root y capa de aplicacion mezcladas

Riesgo: los comandos abren conexiones y llaman funciones concretas. Esto hace dificil reemplazar implementaciones por use cases.

Recomendacion: mantener CLI estable, pero empezar a delegar a use cases facade cuando existan.

### A3 - Proveedor externo y persistencia mezclados en loaders

`historical_loader.py` y `odds_loader.py` conocen API-Football, SQLite, reglas de validacion y transformaciones.

Recomendacion: crear puertos `FootballDataProvider`, `OddsProvider` y repositorios antes de migrar consumidores.

### A4 - Dominio implicito sin tipos dedicados

Conceptos como match, prediction, snapshot, release, odds consensus y data quality existen, pero viajan como `dict`, `sqlite3.Row`, pandas rows y JSON strings.

Recomendacion: introducir value objects pequenos y DTOs externos de forma gradual. No intentar tipar todo el pipeline pandas en una sola task.

### A5 - Notificaciones mezclan gateway, plantilla y outbox

`notifications.py` ya tiene buen comportamiento y tests, pero combina demasiadas capas.

Recomendacion: primero documentar `NotificationGateway` y `NotificationDeliveryRepository`; luego extraer adapters ntfy/Discord sin cambiar delivery semantics.

### A6 - Outputs consultan DB y renderizan en el mismo modulo

`html_report.py` contiene queries, transformaciones y HTML string building.

Recomendacion: separar query/projection de render solo despues de proteger HTML con tests snapshot o asserts especificos.

### A7 - Tiempo del sistema usado directamente

Hay usos de `datetime.now()` en `historical_loader.py`, `html_report.py`, `matchday.py`, `notifications.py`, `odds_loader.py`, `output_manager.py`, `player_evidence.py`, `predictor.py`, `public_bundle.py` y `web_lineup_fallback.py`.

Recomendacion: crear puerto `Clock` para codigo nuevo y migrar llamadas sensibles por workflow.

### A8 - Entorno Python inconsistente

El proyecto declara Python 3.11+, pero el shell observado usa Python 3.9.7. La suite paso, pero tooling moderno puede fallar o producir resultados distintos.

Recomendacion: normalizar entorno antes de mypy/ruff/pre-commit estrictos.

## 9. Brechas contra arquitectura hexagonal objetivo

| Objetivo | Estado actual | Brecha |
|---|---|---|
| Dominio sin SQLite | Muchos servicios aceptan `sqlite3.Connection` o `sqlite3.Row` | Crear entidades/value objects y repositorios |
| Dominio sin API-Football | `historical_loader.py` y `odds_loader.py` consumen cliente directo | Crear `FootballDataProvider` y `OddsProvider` |
| CLI solo invoca casos de uso | CLI abre DB y llama implementaciones concretas | Crear use cases facade |
| Adaptadores outbound aislados | HTTP vive en API client, notifications y web fallback, pero sin paquete adapters | Crear paquete adapters/outbound |
| Scheduler solo despierta casos de uso | PowerShell despierta scripts; `matchday.py` contiene workflow concreto | Formalizar use case y scheduler adapter |
| Predicciones auditables por contrato | Hay datos auditables, pero sin contrato central | Crear `DATA_CONTRACTS.md` y tipos |
| Odds-aware como modelo formal | Hay odds consensus y ajuste, pero en `odds_loader.py` | Definir modelo/puertos/gates |
| Multi-torneo | Nombres, inputs y outputs centrados en Mundial 2026 | Crear Competition/Season config y migracion compatible |
| Quality gates de codigo | Solo pytest | Agregar ruff/mypy/cov/pre-commit graduales |

## 10. Orden recomendado de extraccion

1. Documentar arquitectura objetivo y plan incremental.
2. Configurar quality gates graduales sin reformateo masivo.
3. Crear paquetes hexagonales vacios/minimos.
4. Crear `domain/errors.py` y value objects de IDs.
5. Crear puertos `Clock`, `MatchRepository`, `PredictionRepository`.
6. Extraer conexion/migraciones SQLite manteniendo `db.py` como facade.
7. Extraer repositorios de lectura de bajo riesgo.
8. Extraer repositorios de prediccion/snapshot con tests de inmutabilidad.
9. Crear use cases facade para `GeneratePrediction` y `RefreshMatchday`.
10. Extraer providers HTTP y notification gateways despues de contratos.

## 11. Comandos usados para esta auditoria

```powershell
rg --files src tests scripts
rg "^(def|class) " src/quiniela tests -n
rg "^(from|import) " src/quiniela -n
rg "sqlite3|requests|get_connection|APIFootballClient|datetime\.now|datetime\(|Path\(|NTFY|DISCORD|webhook|Task Scheduler|schtasks|Start-Process|powershell" src/quiniela scripts tests -n
Get-ChildItem -LiteralPath src\quiniela -Filter *.py | Select-Object Name,@{Name='Lines';Expression={(Get-Content -LiteralPath $_.FullName | Measure-Object -Line).Lines}} | Sort-Object Lines -Descending | Format-Table -AutoSize
Get-ChildItem -LiteralPath scripts -Filter *.py | Sort-Object Name | ForEach-Object { ... }
Get-ChildItem -LiteralPath scripts -Filter *.ps1 | Sort-Object Name | ForEach-Object { ... }
rg "CREATE TABLE IF NOT EXISTS" src/quiniela/db.py -n
```

## 12. Conclusion

El sistema esta bien protegido para su etapa actual: la suite offline cubre los flujos mas delicados y los scripts wrapper son simples. El mayor valor ahora viene de crear limites estables, no de reescribir.

La migracion debe preservar las APIs publicas actuales (`scripts/XX_*.py`, `python -m quiniela.cli`, outputs y DB existente) mientras se introducen puertos y adaptadores. La primera extraccion tecnica segura despues de la documentacion es conexion/migraciones SQLite como wrapper compatible, seguida de repositorios pequenos y use cases facade.

## 13. TASK-002 dependency map

El mapa de dependencias y la politica temporal de limites de capas quedaron documentados en `docs/HEXAGONAL_MIGRATION_PLAN.md`. Esta auditoria permanece como fotografia del estado actual; el plan de migracion usa esa fotografia para clasificar modulos, marcar dependencias que deben pasar por puertos y definir imports permitidos, transitorios y prohibidos para codigo nuevo.
