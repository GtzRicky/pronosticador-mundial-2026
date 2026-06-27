# Architecture Target - Quiniela Mundial 2026

**Task:** TASK-003 - Arquitectura objetivo hexagonal
**Fecha:** 2026-06-19
**Estado:** Documento objetivo. No crea paquetes ni cambia codigo fuente.

## 1. Proposito

Este documento define la arquitectura hexagonal objetivo para el proyecto
actual. La meta es guiar refactors incrementales sin romper la CLI, scripts,
base SQLite, outputs, modelos ni pruebas existentes.

El sistema actual es funcional y debe conservar compatibilidad durante la
migracion. La arquitectura objetivo no autoriza una reescritura masiva: cada
movimiento futuro debe introducir contratos pequenos, cubrir el comportamiento
con tests y dejar `src/quiniela/db.py` y `src/quiniela/cli.py` como facades
temporales mientras haya consumidores legacy.

## 2. Vista de capas

```text
scripts/*.py, scripts/*.ps1
        |
        v
adapters/inbound
  Typer CLI, parsers Markdown, scheduler/ops
        |
        v
application
  use cases, workflows, commands, queries, policies
        |
        v
ports
  repository/provider/gateway interfaces
        ^
        |
adapters/outbound
  SQLite, API-Football, odds API, RSS/news, ntfy, Discord, filesystem

domain
  entities, value objects, domain services, errors, events

infrastructure
  config, logging, runtime wiring, migrations, system clock
```

El dominio queda en el centro conceptual. La aplicacion orquesta casos de uso
con entidades y puertos. Los adaptadores convierten mundo externo a contratos
internos. Infraestructura arma configuracion, logging y composicion, pero no
contiene reglas de negocio.

## 3. Paquetes objetivo

```text
src/quiniela/
  domain/
    entities/
    value_objects/
    services/
    errors.py
    events.py

  application/
    use_cases/
    commands/
    queries/
    policies/
    workflows/

  ports/
    repositories.py
    football_data_provider.py
    odds_provider.py
    news_provider.py
    notification_gateway.py
    model_registry.py
    clock.py
    lock_manager.py
    output_writer.py

  adapters/
    inbound/
      cli/
      parsers/
      scheduler/
    outbound/
      sqlite/
      api_football/
      rss_news/
      ntfy/
      discord/
      filesystem/

  infrastructure/
    config.py
    logging.py
    migrations/
    runtime.py
```

Estos paquetes deben crearse en tareas posteriores. TASK-003 solo fija sus
responsabilidades y limites.

## 4. Direccion permitida de dependencias

| Desde | Puede importar | No debe importar |
|---|---|---|
| `domain` | Libreria estandar, utilidades puras propias, tipos internos de `domain` | `sqlite3`, `requests`, pandas, sklearn, Typer, Rich, `config`, `db`, `adapters`, proveedores externos |
| `ports` | `domain`, `typing`/`abc`/`protocols` | Implementaciones concretas, `db`, `requests`, Typer, settings |
| `application` | `domain`, `ports`, DTOs/comandos propios | SQLite directo, API-Football directo, ntfy/Discord directo, RSS directo, filesystem directo, Typer |
| `adapters/inbound` | `application`, `ports`, `infrastructure` | Reglas de dominio nuevas o writes directos si existe use case |
| `adapters/outbound` | `domain`, `ports`, `infrastructure`, librerias externas requeridas | Typer, scripts, decisiones de workflow |
| `infrastructure` | Configuracion, logging, factories, adapters concretos para wiring | Reglas de negocio que pertenezcan a dominio/aplicacion |
| Legacy plano | Modulos actuales mientras se migren | Nuevas dependencias que aumenten acoplamiento |

Regla practica: las dependencias apuntan hacia contratos y dominio; las
implementaciones concretas se inyectan desde composition roots.

## 5. Reglas de no contaminacion

El dominio no debe saber que existen:

- SQLite, tablas, cursores, `sqlite3.Row` o migraciones.
- API-Football, endpoints, payloads crudos, headers ni cuotas.
- `requests` o clientes HTTP.
- ntfy, Discord, webhooks o topicos.
- RSS/news providers.
- Windows Task Scheduler.
- Rutas locales, `.env`, archivos pickle, HTML, CSV o JSON de salida.
- Typer, Rich o scripts wrapper.

La aplicacion tampoco debe depender de implementaciones concretas. Puede hablar
con `FootballDataProvider`, `MatchRepository`, `NotificationGateway` o
`OutputWriter`, pero no con `APIFootballClient`, `get_connection()` o
`NtfyAdapter` directamente.

## 6. Dominio objetivo inicial

### Entidades

| Entidad | Responsabilidad |
|---|---|
| `Competition` | Define una competencia soportada, proveedor, reglas y alcance operativo. |
| `Season` | Agrupa una edicion temporal de una competencia. |
| `TournamentStage` | Describe fase, grupo, ronda o ventana competitiva. |
| `Team` / `Participant` | Representa seleccion o club participante con identidad normalizada. |
| `Player` | Representa identidad deportiva normalizada y datos estables. |
| `Squad` | Conjunto de jugadores elegibles para una competencia/temporada. |
| `Match` | Partido programado o finalizado con equipos, kickoff, sede y estado. |
| `Lineup` | Alineacion oficial, estimada o inferida con fuente y confianza. |
| `Prediction` | Pronostico trazable, pre-kickoff o no evaluable, con modelo usado. |
| `ScoreMatrix` | Distribucion de marcadores exactos y derivaciones `1-X-2`. |
| `OddsSnapshot` | Observacion de mercado versionada por fixture, bookmaker y frescura. |
| `MarketConsensus` | Probabilidades normalizadas de mercado por seleccion/linea. |
| `ModelRelease` | Release activo/candidato/rechazado con metadatos, gates y rollback. |
| `NotificationIntent` | Intencion de comunicar un evento sin detalles del gateway. |

### Value objects

| Value object | Proposito |
|---|---|
| `MatchId`, `TeamId`, `PlayerId`, `PredictionId`, `ModelReleaseId` | IDs tipados para no cruzar strings opacos sin contexto. |
| `CompetitionId`, `SeasonId`, `ProviderFixtureId` | Separan identidad interna de identidad de proveedor. |
| `MatchWindow` | Ventanas `T-60`, `T-30`, `T-15`, `T-5`, `T-1`, postpartido. |
| `MatchState` | Estado explicito de ciclo de vida del partido. |
| `DataFreshness` | Edad, origen y vencimiento operativo de datos. |
| `DataQualityScore` | Calidad/completitud de la informacion usada. |
| `LineupSource` | `official`, `estimated`, `inferred`, `missing`. |
| `DegradationReason` | Motivos por los que una prediccion opera degradada. |
| `MarketProbability` | Probabilidad implicita limpia y normalizada. |
| `Overround` | Margen del bookmaker antes/despues de normalizacion. |
| `BookmakerId` | Identidad estable del bookmaker. |
| `IdempotencyKey` | Llave de no duplicacion para workflows, snapshots y notificaciones. |

## 7. Casos de uso objetivo

| Caso de uso | Responsabilidad | Entrada publica actual |
|---|---|---|
| `IngestCalendarUseCase` | Parsear calendario y persistir partidos. | `ingest-calendar` |
| `IngestRostersUseCase` | Parsear convocatorias y persistir jugadores. | `ingest-rosters` |
| `RefreshMatchdayUseCase` | Refrescar fixtures, estados, lineups, odds y outputs por modo. | `fetch-today`, `run-matchday` |
| `CapturePreMatchSnapshotUseCase` | Guardar snapshot inmutable por ventana. | `capture-pre-match-snapshot` |
| `GeneratePredictionUseCase` | Generar predicciones y persistir trazabilidad. | `predict` |
| `UpdateAfterMatchUseCase` | Sincronizar resultado final y datos postpartido. | `update-after-match` |
| `EvaluatePredictionsUseCase` | Evaluar predicciones pre-kickoff canonicas. | `evaluate-predictions` |
| `TrainCandidateModelUseCase` | Entrenar candidato con validacion temporal. | `train-player-evidence` |
| `ActivateModelReleaseUseCase` | Activar release aprobado o conservar champion. | `activate-model-release` |
| `DispatchNotificationsUseCase` | Despachar outbox con retry sin detener prediccion. | `dispatch-notifications` |
| `RecoverAutomationRunsUseCase` | Recuperar corridas atoradas por lock. | `recover-automation-runs` |
| `RebuildOutputsUseCase` | Regenerar CSV/JSON/HTML y logs. | `rebuild-outputs` |
| `FetchOddsUseCase` | Obtener odds cache-first por fecha/fixture. | `fetch-odds` |
| `BuildOddsConsensusUseCase` | Normalizar mercados y construir consenso. | `build-odds-consensus` |
| `GenerateOddsAwarePredictionUseCase` | Producir prediccion odds-aware formal. | Futuro |
| `InitializeCompetitionUseCase` | Inicializar competencia default o nueva. | Futuro |

Scripts y CLI deben limitarse a transformar argumentos, llamar casos de uso y
presentar resultados.

## 8. Puertos iniciales

### Repositorios

| Puerto | Operaciones iniciales esperadas |
|---|---|
| `MatchRepository` | Buscar por fecha, fixture, equipos, estado; guardar metadata validada. |
| `TeamRepository` | Resolver equipo normalizado e identidad de proveedor. |
| `PlayerRepository` | Resolver identidad de jugador y snapshots de estadisticas. |
| `PredictionRepository` | Guardar predicciones, impactos, historial, evaluaciones. |
| `SnapshotRepository` | Insertar snapshots prepartido inmutables y consultar ultimo snapshot. |
| `OddsRepository` | Guardar snapshots/mercados/consensos y leer resumen por fixture. |
| `ModelReleaseRepository` | Registrar, evaluar, activar y consultar release activo. |
| `NotificationDeliveryRepository` | Encolar, reclamar, actualizar intentos y evitar duplicados. |
| `AutomationRunRepository` | Reclamar/terminar/recover locks idempotentes. |
| `CompetitionRepository` | Leer competencia/temporada activa y configs futuras. |

### Proveedores y gateways

| Puerto | Adaptador objetivo |
|---|---|
| `FootballDataProvider` | `APIFootballProvider` sobre `APIFootballClient` actual. |
| `OddsProvider` | `APIFootballOddsProvider` o proveedor alternativo futuro. |
| `NewsProvider` | `GoogleNewsRssProvider`, `BingNewsRssProvider`. |
| `NotificationGateway` | `NtfyNotificationGateway`, `DiscordNotificationGateway`. |
| `OutputWriter` | `FileSystemOutputWriter` para CSV/JSON/HTML/logs. |
| `ModelRegistry` | `FileSystemModelRegistry` para pickle, releases y reports. |
| `Clock` | `SystemClock`, fake clock en tests. |
| `LockManager` | SQLite automation runs al inicio; otro lock futuro si hiciera falta. |
| `BundleWriter` | Export/import sanitizado del bundle publico. |

## 9. Adaptadores objetivo

| Adaptador | Contiene | No contiene |
|---|---|---|
| `adapters/outbound/sqlite` | Connection, PRAGMA, migrations, repositories SQLite, row mappers. | Reglas de prediccion, gates, templates de notificacion. |
| `adapters/outbound/api_football` | Endpoints, headers, payload DTOs, cache/quota policy adapter. | Reglas de dominio o writes directos fuera de repositorios. |
| `adapters/outbound/rss_news` | RSS queries, descarga limitada, parseo HTML visible. | Decidir si una prediccion es valida. |
| `adapters/outbound/ntfy` | POST a ntfy y traduccion de errores. | Construccion de templates de negocio. |
| `adapters/outbound/discord` | POST webhook y traduccion de errores. | Persistir secretos o dedupe keys. |
| `adapters/outbound/filesystem` | Escritura atomica de outputs, bundles y artefactos. | Calculo de features/modelos. |
| `adapters/inbound/cli` | Typer commands, conversion de opciones a commands/queries. | Queries SQL directos o reglas de negocio. |
| `adapters/inbound/parsers` | Markdown/calendar/roster parsing. | Persistencia concreta. |
| `adapters/inbound/scheduler` | Windows Task Scheduler como disparador operacional. | Decidir ventanas ni idempotencia. |

## 10. Mapeo de modulos actuales a destino tentativo

| Modulo actual | Destino tentativo | Estrategia |
|---|---|---|
| `db.py` | `adapters/outbound/sqlite/*` + repositories | Mantener como facade hasta extraer conexion, migraciones y repositorios. |
| `api_football_client.py` | `adapters/outbound/api_football` | Envolver primero con `FootballDataProvider`; no cambiar cache/quota de golpe. |
| `historical_loader.py` | `application/use_cases/refresh_matchday.py` + provider/repositories | Partir por facades de use case y provider fake en tests. |
| `matchday.py` | `application/workflows/matchday.py` | Recibir `Clock`, repositorios, lock manager y casos de uso. |
| `predictor.py` | `application/use_cases/generate_prediction.py` + domain services | Delegar inicialmente al predictor legacy. |
| `features.py` | `domain/services` + `application/queries` | Separar calculo puro de consultas DB. |
| `poisson_model.py`, `ratings.py` | `domain/services` | Mantener puros y reducir dependencia a `player_model`. |
| `outcome_model.py`, `player_model.py` | `domain/services` + `application` + `ModelRegistry` | Separar entrenamiento, feature reads y artefactos. |
| `player_evidence.py` | `application/use_cases` + repositories + `ModelRegistry` | Extraer snapshots/releases antes de entrenamiento. |
| `odds_loader.py` | `application/use_cases` + `domain/value_objects` + `OddsProvider` | Formalizar devig, consenso y modelo odds-aware. |
| `notifications.py` | `application/use_cases/dispatch_notifications.py` + gateways + repository | Separar outbox, templates y adapters sin cambiar dedupe/retry. |
| `output_manager.py` | `application/use_cases/rebuild_outputs.py` + filesystem adapter | Extraer proyecciones antes de tocar render. |
| `html_report.py` | `adapters/outbound/filesystem/html_renderer.py` + projections | Proteger con tests de HTML antes de dividir. |
| `web_lineup_fallback.py` | `adapters/outbound/rss_news` + lineup estimate use case | Aislar HTTP/RSS de seleccion de starters. |
| `public_bundle.py` | `adapters/outbound/filesystem` + read repositories | Conservar sanitizacion y manifest como contrato. |
| `calendar_parser.py`, `roster_parser.py` | `adapters/inbound/parsers` + ingest use cases | Parsers devuelven DTOs; use cases persisten. |
| `cli.py` | `adapters/inbound/cli` + composition root | Mantener comandos estables y delegar gradualmente. |
| `config.py`, `logging_utils.py`, `cache.py` | `infrastructure` | Evitar importarlos desde dominio. |
| `name_maps.py` | `domain/services` o `domain/value_objects` | Generalizar a competition-aware names en multi-torneo. |

## 11. Contratos minimos por introducir primero

Los contratos iniciales deben ser chicos y equivalentes al comportamiento
existente:

1. `Clock.now()` y `Clock.now_utc()` para codigo nuevo.
2. `MatchRepository.find_by_date(date)` y `find_by_id(match_id)`.
3. `PredictionRepository.save_batch(predictions)` preservando historial.
4. `SnapshotRepository.insert_pre_match_snapshot(...)` preservando inmutabilidad.
5. `FootballDataProvider.get(endpoint, params, force_refresh=False, dry_run=False)`.
6. `OddsRepository.load_consensus(fixture_id)` y `save_market_snapshots(...)`.
7. `NotificationGateway.send(payload)` devolviendo resultado tipado sin secretos.
8. `NotificationDeliveryRepository.claim_due(now)` y `mark_attempt(...)`.
9. `ModelRegistry.load_active()` y `register_candidate(...)`.
10. `OutputWriter.write_text_atomic(path, content)` y `write_dataframe_atomic(...)`.

No deben definirse todos los campos finales de cada entidad antes de extraer
casos reales. Primero contratos minimos, despues refinamiento.

## 12. Compatibilidad temporal con paquete plano

Durante la transicion:

- `scripts/XX_*.py` deben seguir funcionando como wrappers publicos.
- `src/quiniela/cli.py` puede seguir viviendo plano y delegar a use cases
  conforme existan.
- `src/quiniela/db.py` debe conservar funciones publicas usadas por tests y
  modulos legacy.
- Los outputs actuales en `outputs/predictions/` conservan nombres estables.
- La base SQLite existente debe migrar idempotentemente sin perder datos.
- Los tests legacy siguen siendo fuente de verdad del comportamiento actual.
- Las reglas estrictas de layering aplican primero a paquetes nuevos, no a todo
  el legacy plano.

Un patron seguro para refactors futuros es:

```text
CLI/script existente
  -> use case nuevo
    -> servicio legacy actual
      -> db.py facade temporal
```

Luego se reemplazan dependencias internas una por una por puertos/adaptadores.

## 13. Decisiones arquitectonicas registradas

### Decision: Dominio puro antes de repositories completos

Razon: El repo ya funciona, pero los conceptos cruzan como `dict`, rows y JSON.
Introducir value objects pequenos reduce confusion sin mover `db.py` completo.

Impacto: Las primeras tareas tecnicas deben crear IDs, errores y puertos
minimos antes de migrar persistencia.

### Decision: `db.py` permanece como facade temporal

Razon: `db.py` es usado por prediccion, outputs, notifications, odds, evidence,
bundle, CLI y tests. Extraerlo entero seria un cambio de alto riesgo.

Impacto: Los adaptadores SQLite nuevos deben coexistir con funciones legacy
hasta que cada consumidor migre.

### Decision: CLI y scripts son interfaz publica

Razon: Los scripts wrapper son el modo operativo documentado y funcionan como
contrato para agentes y tareas Windows.

Impacto: La migracion debe cambiar implementaciones por debajo, no nombres de
comandos ni opciones sin una task dedicada.

### Decision: Adaptadores conocen proveedores, aplicacion conoce puertos

Razon: API-Football, ntfy, Discord, RSS y filesystem son detalles externos.

Impacto: Nuevos casos de uso no deben importar `APIFootballClient`, `requests`,
`NtfyAdapter`, `DiscordAdapter` ni `get_connection()`.

## 14. Riesgos de sobre-diseno

- Definir entidades completas antes de tener casos de uso extraidos puede crear
  modelos teoricos que no calzan con la DB real.
- Duplicar logica de odds, snapshots o notificaciones puede cambiar resultados
  aunque la suite pase parcialmente.
- Tests de layering aplicados al legacy completo bloquearian la migracion.
- Crear paquetes vacios sin contracts usados agrega ruido; cada paquete debe
  aparecer junto con un contrato o test util.
- Mover `html_report.py` o `player_evidence.py` completo en una sola task
  elevaria el costo de rollback.

## 15. Criterios de aceptacion de esta arquitectura objetivo

- Las capas y responsabilidades quedan claras.
- La direccion de dependencias prohibe que dominio/aplicacion conozcan
  proveedores concretos.
- Scripts y CLI quedan definidos como adaptadores inbound.
- SQLite, API-Football, ntfy, Discord, RSS y filesystem quedan como adaptadores
  outbound o infraestructura.
- Hay una lista inicial de entidades, value objects, puertos y adaptadores.
- Hay compatibilidad temporal explicita con el paquete plano actual.

## 16. State machine de partido

**Task:** TASK-007 - Diseno de state machine y taxonomia de errores
**Estado:** Diseno documental. No implementa enums, excepciones ni migraciones.

### 16.1 Estados de partido

Los estados describen el ciclo de vida operativo del partido. No deben mezclarse
con estados de `ModelRelease`, aunque algunos triggers puedan disparar
entrenamiento o evaluacion de modelos despues de finalizar.

| Estado | Significado | Fuente actual aproximada |
|---|---|---|
| `SCHEDULED` | Partido existe en calendario local, sin refresh live validado. | `matches` cargado por calendario. |
| `DATA_REFRESHED` | Fixtures/status/metadata del dia fueron validados contra calendario. | `fetch_today_data`, strict matching. |
| `PREMATCH_T60_CAPTURED` | Ventana T-60 ejecutada, prediccion y snapshot si aplica. | `run_key=pre:<match_id>:t-60`. |
| `PREMATCH_T30_CAPTURED` | Ventana T-30 ejecutada; puede activar fallback web si no hay XI oficial. | `run_key=pre:<match_id>:t-30`. |
| `PREMATCH_T15_CAPTURED` | Ventana T-15 ejecutada; notificacion prepartido elegible. | `run_key=pre:<match_id>:t-15`. |
| `PREMATCH_T5_CAPTURED` | Ventana T-5 ejecutada; ultima notificacion prepartido normal. | `run_key=pre:<match_id>:t-5`. |
| `PREMATCH_T1_CAPTURED` | Ventana T-1 ejecutada; ultimo snapshot pre-kickoff. | `run_key=pre:<match_id>:t-1`. |
| `LINEUP_OFFICIAL_CONFIRMED` | XI oficial completo detectado para equipo o partido. | `historical_lineups`, notification official lineup. |
| `KICKED_OFF` | Kickoff alcanzado; nuevas predicciones ya no son evaluables prepartido. | comparacion `now >= datetime_cdmx`. |
| `LIVE` | Partido en curso o estado live del proveedor. | `status` live API-Football. |
| `FINISHED_PENDING_STATS` | Resultado final detectado, faltan eventos/stats/evidence completa. | `sync_finished_results_for_date`. |
| `FINISHED_COMPLETE` | Resultado y datos postpartido suficientes para targets/evidence. | `finalize_player_evidence`. |
| `EVALUATED` | Prediccion canonica pre-kickoff evaluada contra resultado. | `prediction_evaluations`. |

Estados de modelo (`MODEL_CANDIDATE_READY`, `MODEL_RELEASE_REJECTED`,
`MODEL_RELEASE_ACTIVATED`) pertenecen a una state machine de model governance y
no al estado del partido.

### 16.2 Transiciones clave

| From | To | Trigger | Preconditions | Side effects | Retry policy | Idempotency key | Failure mode |
|---|---|---|---|---|---|---|---|
| `SCHEDULED` | `DATA_REFRESHED` | `fetch_today_data(date, mode)` | Calendario local tiene partido; fixture API valida equipos esperados. | Actualiza fixture/status/API id; escribe payloads permitidos por modo; regenera outputs. | Retry si API transient; bloquear si mismatch de fixture candidato. | `daily:<date>` o `hourly:<date-hour>` | `RetrievalValidationError` genera `retrieval_issue`; no mezclar fixture incorrecto. |
| `DATA_REFRESHED` | `PREMATCH_T60_CAPTURED` | `run-matchday` en T-60 vencido | Partido no terminal; `now < kickoff`. | Refresh `pre_match`, prediccion, snapshot, outputs, notificaciones. | Reintentar por automation recovery si corrida queda running. | `pre:<match_id>:t-60` | Fallo registra automation run failed y degraded outputs. |
| `DATA_REFRESHED`/`PREMATCH_T60_CAPTURED` | `PREMATCH_T30_CAPTURED` | `run-matchday` en T-30 vencido | Partido no terminal; `now < kickoff`. | Igual T-60; puede usar fallback web si no hay XI oficial. | Igual automation. | `pre:<match_id>:t-30` | Si fallback falla, degradar lineup, no detener prediccion. |
| `PREMATCH_T30_CAPTURED` | `PREMATCH_T15_CAPTURED` | `run-matchday` en T-15 vencido | Partido no terminal; `now < kickoff`. | Prediccion/snapshot; notificacion T-15 elegible. | Retry notification independiente. | `pre:<match_id>:t-15` y notification `dedupe_key`. | Notificacion fallida no falla matchday. |
| `PREMATCH_T15_CAPTURED` | `PREMATCH_T5_CAPTURED` | `run-matchday` en T-5 vencido | Partido no terminal; `now < kickoff`. | Prediccion/snapshot; notificacion T-5 elegible. | Retry hasta kickoff/expiration. | `pre:<match_id>:t-5` y notification `dedupe_key`. | Supersede de ventanas pendientes evita duplicados. |
| `PREMATCH_T5_CAPTURED` | `PREMATCH_T1_CAPTURED` | `run-matchday` en T-1 vencido | Partido no terminal; `now < kickoff`. | Ultimo snapshot pre-kickoff y outputs. | Automation recovery. | `pre:<match_id>:t-1` | Si falla, conservar ultimo snapshot valido. |
| Any prematch | `LINEUP_OFFICIAL_CONFIRMED` | Refresh detecta startXI oficial completo. | XI de 11 jugadores para equipo; lineup hash distinto o no enviado. | Encola notificacion oficial por equipo; actualiza lineup source. | Retry notification por canal. | `official_lineup:<match_id>:<team_norm>:<lineup_hash>` | Dedupe evita repetir misma alineacion; cambio de hash crea nueva entrega. |
| Any prematch | `KICKED_OFF` | `now >= kickoff` | Hora local/UTC timezone-aware. | Nuevas predicciones quedan `is_pre_kickoff=0`; no se evaluan como prematch. | No aplica. | `kickoff:<match_id>` | Riesgo de reloj; usar `Clock` en codigo nuevo. |
| `KICKED_OFF` | `LIVE` | Estado live API o ventana post-kickoff. | Fixture validado. | Puede refrescar status liviano. | Retry transient dentro de budget. | `live:<match_id>:<slot>` | Degradar si API no disponible. |
| `LIVE`/`KICKED_OFF` | `FINISHED_PENDING_STATS` | `sync_finished_results_for_date` detecta final nuevo. | Resultado final (`FT`, `AET`, `PEN`) sin actual_result previo. | Inserta resultado; dispara final refresh si es nuevo. | Retry post_status cada slot. | `post:<match_id>:<slot>` | Si faltan stats, resultado se conserva y evidence espera. |
| `FINISHED_PENDING_STATS` | `FINISHED_COMPLETE` | `finalize_player_evidence` completa targets. | Resultado, snapshot pre-kickoff, lineups/stats suficientes. | Inserta targets idempotentes, puede disparar training si hay datos. | Retry manual/automation. | `evidence:<match_id>` | Si incompleto, registrar no elegible sin activar modelo. |
| `FINISHED_COMPLETE` | `EVALUATED` | `evaluate_predictions` | Existe prediccion pre-kickoff canonica y resultado. | Inserta evaluation, marca canonica por match. | Idempotente por prediction/match. | `evaluation:<match_id>:<prediction_id>` | Predicciones post-kickoff no evaluables. |

### 16.3 Prevenciones explicitas

- Snapshots duplicados: `pre_match_snapshots` usa llave logica
  `match_id + window_label + source_kind`; inserts deben ser `INSERT OR IGNORE`.
- Predicciones post-kickoff: `is_pre_kickoff=0` y `generated_at_utc >= kickoff`
  excluyen evaluacion prepartido.
- Notificaciones repetidas: outbox usa `dedupe_key`, ventana, kickoff y hash de
  alineacion para dedupe/supersede.
- Refresh pesado innecesario: `full` queda reservado para daily/final refresh;
  `hourly`, `pre_match`, `post_status` y `lineups` tienen alcance menor.
- Entrenamiento con datos incompletos: evidence requiere resultado, snapshot,
  lineups y stats suficientes antes de targets/release.
- Fixtures incorrectos: strict matching genera `retrieval_issue` y bloquea el
  flujo candidato antes de almacenar datos ajenos.

### 16.4 Taxonomia de errores

| Error | Retry | Bloquea flujo | Degrada prediccion | `retrieval_issue` | Runbook | Notas |
|---|---|---|---|---|---|---|
| `QuinielaError` | depende | depende | depende | depende | si | Base comun futura. |
| `TransientApiError` | si | no si hay cache valida | si | opcional | si | HTTP 408/5xx/red. |
| `ApiQuotaExceeded` | no hasta reset | bloquea live fetch | si | no | si | Proteger reserva critica. |
| `ApiAuthError` | no | bloquea live fetch | si | no | si | API key ausente o invalida. |
| `ApiSchemaChanged` | no automatico | bloquea endpoint afectado | si | si | si | Requiere fixture/test y mapper. |
| `ApiDataMismatch` | no | bloquea fixture candidato | no usar dato | si | si | No mezclar partidos. |
| `FixtureResolutionError` | manual | bloquea match afectado | si | si | si | Equipo/fixture no resuelto. |
| `LineupUnavailable` | retry cerca kickoff | no | si | opcional | si | Puede usar fallback web. |
| `OfficialResultUnavailable` | si post_status | no | no | opcional | si | Mantener pendiente. |
| `DataQualityError` | depende | puede bloquear | si | si | si | Datos incompletos o contradictorios. |
| `PredictionNotEvaluable` | no | no | no | no | no | Estado esperado para post-kickoff. |
| `ModelGateFailed` | no | bloquea activacion | no | no | si | Conservar champion. |
| `NotificationTransientError` | si | no | no | no | si | Nunca detiene prediccion. |
| `NotificationPermanentError` | no | no | no | no | si | Marcar failed/expired. |
| `AutomationLockError` | si recovery | bloquea run_key | no | no | si | `recover_stale_automation_runs`. |
| `CompetitionConfigError` | no | bloquea competencia | si | no | si | Futuro multi-torneo. |
| `OddsMarketUnavailable` | retry/stale | no | si | opcional | si | Continuar sin odds-aware. |
| `OddsOverroundError` | no para mercado | no | si | opcional | si | Excluir mercado/bookmaker. |
| `OddsConsensusError` | depende | no | si | opcional | si | Usar Poisson/Logit base. |
| `ModelReleaseError` | manual | bloquea release | no | no | si | Rollback a release anterior. |
