# Hexagonal Migration Plan - Incremental and Reversible

**Task:** TASK-004 - Plan de migracion hexagonal incremental
**Fecha:** 2026-06-19
**Estado:** Plan expandido. No implementa codigo ni migraciones de DB.

## 1. Proposito

Este documento convierte el mapa de dependencias de TASK-002 y la arquitectura
objetivo de TASK-003 en una ruta incremental. La meta es solidificar el proyecto
sin romper los flujos ya verdes, conservando CLI, scripts, SQLite, outputs y
tests como contratos publicos durante la transicion.

La migracion debe avanzar en pasos pequenos:

1. Definir contratos minimos.
2. Crear skeleton solo cuando tenga contratos o tests utiles.
3. Extraer repositorios pequenos.
4. Introducir use cases facade que deleguen al legacy.
5. Extraer adaptadores concretos.
6. Endurecer layering cuando existan paquetes reales.

Este plan no autoriza reescrituras masivas ni movimientos completos de
`db.py`, `matchday.py`, `predictor.py`, `notifications.py`,
`player_evidence.py` o `html_report.py` en una sola task.

## 2. Contexto inspeccionado

TASK-004 se basa en:

- `docs/ARCHITECTURE_AUDIT.md`
- `docs/ARCHITECTURE_TARGET.md`
- `docs/handoffs/TASK-003.md`
- `src/quiniela/db.py`
- `src/quiniela/cli.py`
- `src/quiniela/matchday.py`
- `src/quiniela/predictor.py`
- `tests/*.py`

Hallazgos directos relevantes:

- `db.get_connection()` crea directorios, abre SQLite, configura
  `row_factory`, aplica `PRAGMA busy_timeout` y ejecuta schema/migraciones.
- `db.py` tambien persiste predicciones, snapshots, automation runs, cache,
  releases y multiples familias de datos.
- `cli.py` abre conexiones y llama modulos concretos; aun asi, los scripts son
  wrappers limpios y deben conservarse.
- `matchday.py` ya inyecta varias callables para tests, pero sigue reclamando
  locks, refrescando, prediciendo, capturando snapshots, regenerando outputs y
  despachando notificaciones en un workflow legacy.
- `predictor.py` abre su propia conexion, arma features, aplica Poisson/Logit,
  odds-aware auxiliar, inserta predicciones e impactos.
- Los tests existentes protegen automation, matchday, outputs, snapshots,
  releases, notifications, odds, API client, strict fixture matching, bundle y
  HTML. Deben usarse como characterization tests antes de mover comportamiento.

## 3. Principios de migracion

- Mantener `scripts/XX_*.py` y `python -m quiniela.cli` como interfaz publica.
- Mantener `src/quiniela/db.py` como facade legacy hasta que cada consumidor se
  migre explicitamente.
- Crear paquetes nuevos solo con contratos, tests o adapters usados.
- Excluir legacy plano de tests estrictos de layering al inicio.
- No cambiar schema o migraciones SQLite dentro de tasks documentales.
- No cambiar nombres ni formato de outputs sin task especifica.
- Cada extraccion debe tener validacion enfocada y rollback claro.
- Preferir facades que delegan al legacy antes de reimplementar logica.

## 4. Compatibilidad temporal

Durante toda la migracion inicial:

```text
scripts/*.py
  -> src/quiniela/cli.py
    -> application/use_cases/* facade nuevo, cuando exista
      -> servicio legacy actual
        -> db.py facade temporal
```

Reglas practicas:

- `cli.py` puede seguir importando modulos legacy hasta que el use case
  equivalente exista y tenga tests.
- `db.py` debe conservar `get_connection`, `fetch_dataframe`,
  `insert_prediction_rows`, `claim_automation_run`, `finish_automation_run`,
  `recover_stale_automation_runs`, `insert_pre_match_snapshot` y helpers
  publicos usados por tests.
- Los adapters SQLite nuevos pueden llamar funciones de `db.py` al principio,
  pero deben encapsular esa deuda detras de interfaces.
- Las nuevas capas no deben importar Typer, Rich, `requests`, `sqlite3` ni
  providers concretos salvo en adapters.

## 5. Preflight obligatorio para cada task de refactor

Antes de editar codigo en cualquier task futura:

1. Leer el handoff anterior y este plan.
2. Ejecutar `git status --short`.
3. Leer los tests que cubren el modulo a tocar.
4. Ejecutar los tests enfocados antes del cambio.
5. Hacer un cambio pequeno.
6. Ejecutar los tests enfocados despues del cambio.
7. Ejecutar `pytest -q` si el cambio cruza modulo, persistencia o outputs.
8. Crear/actualizar handoff con comandos, resultados, riesgos y rollback.

Si el test enfocado falla antes del cambio, no refactorizar: registrar el
estado, aislar causa y pedir decision o crear task de estabilizacion.

## 6. Etapas de migracion

### Etapa 0 - Baseline y guardrails documentales

**Objetivo:** preservar comportamiento actual y cerrar gaps de documentacion
antes de mover codigo.

**Acciones:**

- Mantener `ARCHITECTURE_AUDIT.md`, `ARCHITECTURE_TARGET.md` y este plan como
  referencia.
- Completar reglas de handoff, coding standards y testing strategy.
- Documentar comandos oficiales y tolerancias legacy.

**Validacion:**

```powershell
pytest -q
python scripts\05_fetch_today_data.py --help
python scripts\15_run_matchday.py --help
```

**Rollback:** revertir documentos de la etapa.

### Etapa 1 - Skeleton con contratos minimos

**Objetivo:** crear estructura hexagonal con valor inmediato, sin mover logica.

**Acciones:**

- Crear `domain`, `application`, `ports`, `adapters`, `infrastructure` con
  `__init__.py`.
- Crear contratos pequenos: `Clock`, IDs tipados, errores base y puertos de
  repositorio iniciales.
- Agregar tests de imports y layering solo para paquetes nuevos.

**Validacion:**

```powershell
pytest -q tests\test_architecture_layering.py
pytest -q
```

**Rollback:** eliminar paquetes nuevos y test de layering. No toca legacy.

**Riesgo:** crear paquetes vacios. Mitigacion: cada paquete debe incluir al
menos un contrato, tipo o test usado por la etapa.

### Etapa 2 - Clock y tiempo inyectable para codigo nuevo

**Objetivo:** evitar nuevos usos directos de `datetime.now()` en workflows
extraidos.

**Acciones:**

- Crear `ports/clock.py` con `Clock`.
- Crear `infrastructure/runtime.py` o adapter simple `SystemClock`.
- Usar fake clock en tests nuevos.
- No migrar todo el legacy en esta etapa.

**Validacion:**

```powershell
pytest -q tests\test_matchday.py tests\test_notifications.py
pytest -q
```

**Rollback:** revertir archivos de clock y tests nuevos.

### Etapa 3 - SQLite connection y migrations adapter compatible

**Objetivo:** separar apertura de conexion y migraciones sin cambiar semantica.

**Acciones:**

- Crear `adapters/outbound/sqlite/connection.py` con una funcion equivalente a
  `get_connection`.
- Crear `adapters/outbound/sqlite/migrations.py` o wrapper de schema.
- Cambiar `db.get_connection()` para delegar al adapter solo despues de tests.
- Mantener firma y comportamiento: directorio, timeout, `row_factory`,
  `PRAGMA busy_timeout`, schema idempotente.

**Validacion antes y despues:**

```powershell
pytest -q tests\test_db_automation.py tests\test_player_evidence_db.py
pytest -q tests\test_output_manager.py tests\test_public_bundle.py
pytest -q
```

**Rollback:** revertir adapter y restaurar `db.get_connection()` original.

**No hacer:** partir todas las funciones de `db.py`.

### Etapa 4 - Repositorios pequenos de lectura

**Objetivo:** introducir repositories con bajo riesgo antes de writes criticos.

**Primeros candidatos:**

- `SQLiteMatchRepository` para `find_by_date`, `find_by_id`,
  `find_relevant_for_matchday`.
- `SQLitePredictionReadRepository` o projections de lectura para latest/history.
- `SQLiteOddsReadRepository` para resumen de consenso por fixture.

**Validacion:**

```powershell
pytest -q tests\test_matchday.py tests\test_output_manager.py tests\test_html_report.py
pytest -q tests\test_odds_loader.py
pytest -q
```

**Rollback:** revertir repositories y volver a `fetch_dataframe` directo.

**Nota:** mantener queries legacy como referencia durante una task; borrar solo
cuando los tests prueben equivalencia.

### Etapa 5 - Repositorios de escritura acotados

**Objetivo:** encapsular writes con contratos que ya tienen tests fuertes.

**Orden recomendado:**

1. `AutomationRunRepository` para `claim_automation_run`,
   `finish_automation_run`, `recover_stale_automation_runs`.
2. `PredictionRepository` para `insert_prediction_rows` y append-only impacts.
3. `SnapshotRepository` para `insert_pre_match_snapshot` y
   `get_latest_pre_match_snapshot`.
4. `ModelReleaseRepository` para registrar/activar releases.

**Validacion:**

```powershell
pytest -q tests\test_db_automation.py
pytest -q tests\test_output_manager.py
pytest -q tests\test_player_evidence_db.py tests\test_player_evidence_model.py
pytest -q
```

**Rollback:** dejar funciones legacy en `db.py` como source of truth y revertir
adapters/repositories.

**Riesgo:** alterar idempotencia o inmutabilidad. Mitigacion: tests antes y
despues, y equivalencia fila por fila en DB temporal.

### Etapa 6 - `GeneratePredictionUseCase` facade

**Objetivo:** hacer que CLI y matchday puedan invocar un use case sin cambiar
resultados.

**Acciones:**

- Crear `application/use_cases/generate_prediction.py`.
- El use case puede delegar inicialmente a `Predictor`.
- Definir input minimo: `date` o `home/away`, `prediction_context`,
  `window_label`.
- Definir output tipado o dataclass simple con dataframe/path si todavia se
  conserva pandas en borde legacy.
- Cambiar `cli.py` solo para `predict` si los tests y scripts siguen iguales.

**Validacion:**

```powershell
python scripts\06_predict_match.py --help
pytest -q tests\test_output_manager.py tests\test_html_report.py
pytest -q tests\test_matchday.py
pytest -q
```

**Rollback:** revertir use case y cambio de CLI; `Predictor` queda intacto.

**No hacer:** reescribir Poisson, Logit, odds-aware o feature assembly.

### Etapa 7 - `RefreshMatchdayUseCase` / workflow facade

**Objetivo:** encapsular `MatchdayRunner` como workflow de aplicacion sin
romper scheduler.

**Acciones:**

- Crear facade que delegue a `MatchdayRunner`.
- Extraer planificacion pura (`plan_matchday_actions`) como servicio reusable
  o conservarla temporalmente si moverla agrega riesgo.
- Recibir `Clock`, `AutomationRunRepository`, provider/use cases cuando existan.
- Mantener `python scripts\15_run_matchday.py --help` y comportamiento actual.

**Validacion:**

```powershell
python scripts\15_run_matchday.py --help
pytest -q tests\test_matchday.py tests\test_notifications.py
pytest -q tests\test_db_automation.py tests\test_output_manager.py
pytest -q
```

**Rollback:** revertir facade y wiring; `MatchdayRunner` sigue siendo entrada.

### Etapa 8 - Provider ports para API, odds y news

**Objetivo:** aislar proveedores externos antes de resiliencia avanzada.

**Acciones:**

- Crear `FootballDataProvider` y adapter sobre `APIFootballClient`.
- Crear `OddsProvider` para odds si no queda cubierto por el mismo provider.
- Crear `NewsProvider` para RSS/news fallback.
- Primero adaptar tests con fakes; despues migrar consumidores.

**Validacion:**

```powershell
pytest -q tests\test_api_football_client.py tests\test_historical_loader_strict.py
pytest -q tests\test_odds_loader.py tests\test_web_lineup_fallback.py
pytest -q
```

**Rollback:** consumers vuelven a importar `APIFootballClient`/RSS legacy.

**No hacer:** cambiar politicas de quota, TTL o retry sin `API_POLICY.md` y
tests dedicados.

### Etapa 9 - Notification outbox y gateways

**Objetivo:** separar outbox, templates y gateways sin perder dedupe/retry.

**Acciones:**

- Crear `NotificationDeliveryRepository`.
- Crear `NotificationGateway` y adapters ntfy/Discord.
- Mantener `notifications.py` como facade si ayuda al rollback.
- Prohibir persistir topicos ntfy o webhooks.

**Validacion:**

```powershell
pytest -q tests\test_notifications.py tests\test_matchday.py
python scripts\25_dispatch_notifications.py --help
pytest -q
```

**Rollback:** revertir gateways/repository y volver a `notifications.py`.

### Etapa 10 - Output projections y filesystem writer

**Objetivo:** separar consultas/proyecciones de render/escritura sin romper
CSV/JSON/HTML.

**Acciones:**

- Crear read projections para predictions latest/history.
- Crear `OutputWriter` para escritura atomica.
- Mantener nombres de archivos actuales.
- Dividir `html_report.py` solo despues de reforzar asserts o snapshots.

**Validacion:**

```powershell
pytest -q tests\test_output_manager.py tests\test_html_report.py
python scripts\22_rebuild_outputs.py --help
pytest -q
```

**Rollback:** revertir projections/writer; `output_manager.py` y
`html_report.py` quedan como facades.

### Etapa 11 - Odds-aware, evidence y multi-torneo

**Objetivo:** migrar subsistemas de mayor dominio despues de tener contratos.

**Orden recomendado:**

1. Odds value objects y consensus use case.
2. Snapshot/model release repositories.
3. Model registry y model cards.
4. Competition/Season config read-only.
5. Migraciones multi-torneo compatibles.

**Validacion:**

```powershell
pytest -q tests\test_odds_loader.py tests\test_player_evidence_db.py tests\test_player_evidence_model.py
pytest -q tests\test_output_manager.py tests\test_public_bundle.py
pytest -q
```

**Rollback:** revertir cada sub-etapa de forma independiente. No mezclar odds,
evidence y multi-torneo en una sola PR/task.

## 7. Matriz de tests por superficie

| Superficie | Tests minimos |
|---|---|
| Conexion/schema/migraciones | `tests\test_db_automation.py`, `tests\test_public_bundle.py` |
| Automation runs | `tests\test_db_automation.py`, `tests\test_matchday.py` |
| Matchday workflow | `tests\test_matchday.py`, `tests\test_notifications.py`, `tests\test_output_manager.py` |
| Prediction persistence | `tests\test_output_manager.py`, `tests\test_html_report.py`, `tests\test_matchday.py` |
| Snapshots/evidence/releases | `tests\test_player_evidence_db.py`, `tests\test_player_evidence_model.py` |
| API-Football provider | `tests\test_api_football_client.py`, `tests\test_historical_loader_strict.py` |
| Odds | `tests\test_odds_loader.py`, `tests\test_html_report.py` |
| Notifications | `tests\test_notifications.py`, `tests\test_matchday.py` |
| Outputs/HTML | `tests\test_output_manager.py`, `tests\test_html_report.py` |
| Public bundle | `tests\test_public_bundle.py`, `tests\test_output_manager.py` |

Siempre que una task toque mas de una superficie, ejecutar tambien:

```powershell
pytest -q
```

## 8. Extraction seams priorizados

| Prioridad | Seam | Por que es buen corte | Riesgo principal |
|---:|---|---|---|
| 1 | `Clock` para codigo nuevo | No cambia comportamiento legacy | Migrar demasiado pronto todo `datetime.now()` |
| 2 | SQLite connection adapter | Centraliza apertura sin cambiar queries | Romper migraciones idempotentes |
| 3 | `AutomationRunRepository` | Tests fuertes e idempotencia clara | Duplicar o perder locks |
| 4 | `PredictionRepository` facade | Permite `GeneratePredictionUseCase` | Cambiar historial/latest |
| 5 | `SnapshotRepository` | Contrato de inmutabilidad bien testeado | Romper seleccion latest before kickoff |
| 6 | `GeneratePredictionUseCase` | CLI puede delegar sin cambiar modelo | Mezclar reimplementacion de predictor |
| 7 | `RefreshMatchdayUseCase` | Encapsula scheduler workflow | Alterar orden de side effects |
| 8 | Provider ports | Habilita resiliencia API posterior | Cambiar semantica cache-first |
| 9 | Notification gateways | Separa secretos y delivery | Cambiar retry/dedupe |
| 10 | Output projections | Reduce tamano de HTML/output modules | Romper formatos estables |

## 9. Decisiones que requieren ADR o revision explicita

- Mover `db.py` completo o eliminarlo como facade.
- Cambiar nombres/opciones de CLI o scripts.
- Cambiar schema SQLite o agregar migraciones multi-torneo.
- Cambiar reglas de prediccion canonica pre-kickoff.
- Cambiar politica de API quota/cache/retry.
- Cambiar semantica de notifications retry/dedupe/expiration.
- Cambiar nombres o ubicacion de outputs estables.
- Activar odds-aware formal como modelo canonico o ensemble.
- Endurecer tests de layering contra legacy plano.

## 10. Rollback general

Cada etapa debe poder revertirse con git sin restaurar bases reales. Para tasks
que toquen DB en el futuro:

- Usar DB temporal en tests.
- No probar migraciones nuevas contra `data/db/quiniela.db` sin backup.
- Mantener funciones legacy hasta que la etapa siguiente demuestre equivalencia.
- Si un adapter nuevo falla, revertir wiring y dejar el modulo legacy activo.
- Si un output cambia inesperadamente, revertir writer/projection y regenerar
  outputs solo con comandos documentados.

## 11. Orden recomendado de proximas tasks tecnicas

Despues de cerrar las tareas documentales P0:

1. Crear standards/testing docs para fijar gates incrementales.
2. Crear skeleton hexagonal con IDs, errores y `Clock`.
3. Agregar tests de layering limitados a paquetes nuevos.
4. Extraer connection/migrations adapter compatible.
5. Extraer `AutomationRunRepository`.
6. Extraer `PredictionRepository` facade.
7. Crear `GeneratePredictionUseCase` delegando a `Predictor`.
8. Crear `RefreshMatchdayUseCase` delegando a `MatchdayRunner`.
9. Introducir provider ports para API/odds/news.
10. Separar notifications y outputs.

## 12. Criterios de aceptacion de este plan

- Evita mover todo `db.py` en una sola task.
- Conserva CLI/scripts durante la transicion.
- Cada etapa tiene validacion y rollback.
- Prioriza cortes de bajo riesgo con tests existentes.
- Declara decisiones que requieren ADR o revision.
- No crea codigo, paquetes ni migraciones de DB.
