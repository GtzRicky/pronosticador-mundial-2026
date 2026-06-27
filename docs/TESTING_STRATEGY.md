# Testing Strategy - Quiniela Mundial 2026

**Task:** TASK-006 - Estandares de codigo y quality gates
**Fecha:** 2026-06-20
**Estado:** Estrategia incremental. Coverage y pre-commit configurados sin umbral bloqueante.

## 1. Proposito

La suite offline actual es el contrato de comportamiento durante la migracion
hexagonal. Este documento define como usarla, como agregar tests nuevos y como
endurecer quality gates sin bloquear el legacy en una sola task.

## 2. Estado actual

- Comando base: `pytest -q`.
- Suite observada despues de TASK-038: 156 tests offline.
- Pytest conserva `pythonpath = ["src"]` como compatibilidad interna, pero el
  entorno oficial instala el proyecto en modo editable para que tests, CLI y
  wrappers compartan la misma resolucion de paquete.
- Los tests actuales cubren parsers, DB/automation, API client, strict fixture
  matching, matchday, notifications, odds, outputs, HTML, public bundle,
  Poisson, Logit y evidencia de jugadores.
- Hay guardrails AST de layering para `domain`, `ports` y `application`.
- Coverage esta configurado como reporte informativo, sin `fail-under`.
- Pre-commit esta configurado con hooks locales no destructivos.

## 3. Piramide de pruebas objetivo

1. Tests puros de dominio: value objects, state machine, odds conversion,
   scoring, errores tipados.
2. Tests de aplicacion: use cases con repositories/providers/gateways fakes.
3. Tests de adapters: SQLite temporal, API fixtures/fakes, notification fake
   sessions, filesystem temporal.
4. Tests de workflows: matchday, prediccion, outputs, evidence, notifications.
5. Tests de arquitectura: imports prohibidos para paquetes nuevos.

Los tests live contra API real no pertenecen a la suite base; deben ser
comandos ops/manuales y proteger cuota/secretos.

## 4. Suite base

Preparar primero el entorno soportado:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
python --version
```

Ejecutar siempre antes de cerrar una task que toque codigo:

```powershell
pytest -q
```

No usar `PYTHONPATH=src` como setup operativo. Puede servir como rollback
temporal, pero el gate oficial incluye:

```powershell
python -m quiniela.cli --help
```

Para tasks documentales, ejecutar la validacion indicada por el backlog. Si la
task solo edita docs, `pytest -q` sigue siendo el smoke test base del hito.

## 5. Tests por superficie

| Superficie | Tests recomendados |
|---|---|
| DB schema, migrations, locks | `tests\test_db_automation.py` |
| Matchday workflow | `tests\test_matchday.py` |
| Notifications/outbox | `tests\test_notifications.py` |
| API client | `tests\test_api_football_client.py` |
| Strict fixture matching | `tests\test_historical_loader_strict.py` |
| Outputs latest/history/logs | `tests\test_output_manager.py` |
| HTML report | `tests\test_html_report.py` |
| Odds consensus/model | `tests\test_odds_loader.py` |
| Evidence snapshots/releases | `tests\test_player_evidence_db.py`, `tests\test_player_evidence_model.py` |
| Player model | `tests\test_player_model.py` |
| Outcome model | `tests\test_outcome_model.py` |
| Public bundle | `tests\test_public_bundle.py` |
| Parsers/names | `tests\test_calendar_parser.py`, `tests\test_roster_parser.py` |
| Web lineup fallback | `tests\test_web_lineup_fallback.py` |
| Multi-torneo DB | `tests\test_multi_tournament_migrations.py` |

## 6. Preflight para refactors

Antes de mover comportamiento:

1. Leer tests existentes de la superficie.
2. Ejecutar tests enfocados antes del cambio.
3. Si fallan, no refactorizar hasta registrar causa.
4. Agregar characterization test si el comportamiento no esta cubierto.
5. Hacer un cambio pequeno.
6. Ejecutar tests enfocados despues.
7. Ejecutar `pytest -q` si el cambio toca persistencia, CLI, workflow,
   outputs o contratos.

## 7. Tests para capas hexagonales

Los paquetes nuevos ya tienen guardrail en `tests/test_architecture_layering.py`.

Reglas iniciales:

- `domain` no importa `sqlite3`, `requests`, Typer, Rich, pandas, sklearn,
  `quiniela.db`, `quiniela.adapters` ni `quiniela.infrastructure`.
- `ports` no importa adapters ni `quiniela.db`.
- `application` no importa adapters concretos, `requests`, `sqlite3`, Typer ni
  Rich.
- Legacy plano queda excluido al inicio.

No hay excepciones SQLite en `application`. `BuildOddsConsensusUseCase` usa
`OddsConsensusGateway` y `DoctorUseCase` usa `DoctorHealthRepository`; las
conexiones, queries read-only y errores concretos permanecen en adapters
outbound SQLite.

Ejecutar:

```powershell
pytest -q tests\test_architecture_layering.py
```

## 8. Politica de fixtures y datos

- Usar DB temporal por test con `tmp_path`.
- No tocar `data/db/quiniela.db` en tests automatizados.
- No usar `.env` real ni secretos.
- Fakes de API deben reproducir estructura minima del proveedor.
- Payloads externos largos deben vivir como fixtures sanitizados si se vuelven
  necesarios.
- Tests de bundle deben validar sanitizacion de tablas sensibles.

## 9. Politica offline/live

Suite offline:

- No consume API-Football.
- No envia ntfy ni Discord reales.
- No requiere Task Scheduler.
- No depende de reloj real salvo tests explicitamente parametrizados.

Comandos live/manuales:

- Deben documentar consumo de cuota.
- Deben requerir `.env` configurado.
- Deben evitar imprimir secretos.
- No son gate obligatorio para PRs/refactors.

## 10. Coverage gradual

Coverage activo desde TASK-014:

1. Reportar coverage sin fail-under.
2. Aplicar umbrales por paquetes nuevos en tasks futuras.
3. No exigir porcentaje global mientras el legacy plano siga concentrado.
4. Subir umbrales solo con evidencia de estabilidad.

Comando oficial:

```powershell
pytest --cov=quiniela --cov-report=term-missing
```

## 10.1 Pre-commit

Pre-commit activo desde TASK-014 con hooks locales:

- `ruff check .`
- `mypy src/quiniela`
- `check-yaml`
- `check-toml`

No se activa `ruff format`, `end-of-file-fixer` ni hooks de whitespace en esta etapa para evitar reescrituras mecanicas fuera de alcance.

Comando oficial:

```powershell
pre-commit run --all-files
```

## 11. Criterios para agregar tests

Agregar tests cuando:

- se crea un puerto, use case o adapter nuevo;
- se mueve una query o write SQLite;
- se cambia idempotencia, retry, cache, outputs o notificaciones;
- se altera prediccion, snapshot, evaluacion o releases;
- se agrega error tipado o state transition.

No duplicar tests si una suite existente ya cubre el contrato; preferir asserts
de equivalencia durante refactors.

## 12. Validaciones oficiales por tipo de task

Docs/architecture:

```powershell
Test-Path <documento>
pytest -q
```

Refactor persistence:

```powershell
pytest -q tests\test_db_automation.py tests\test_output_manager.py tests\test_player_evidence_db.py
pytest -q
```

Refactor matchday:

```powershell
pytest -q tests\test_matchday.py tests\test_notifications.py tests\test_db_automation.py
pytest -q
```

API/provider:

```powershell
pytest -q tests\test_api_football_client.py tests\test_historical_loader_strict.py
python scripts\05_fetch_today_data.py --help
```

Outputs:

```powershell
pytest -q tests\test_output_manager.py tests\test_html_report.py
python scripts\22_rebuild_outputs.py --help
```

Model cards y aprendizaje controlado:

```powershell
pytest -q tests\test_player_evidence_model.py tests\test_player_evidence_db.py
```

Validar que:

- los splits temporales no filtren futuro hacia entrenamiento;
- los gates rechacen cualquier regresion de MAE, devianza, log-loss, Brier o
  calibracion;
- un candidato rechazado no reemplace al champion;
- un fallo de manifiesto restaure el release activo anterior;
- los hashes de dataset y schema queden documentados en model card o metadata.

Multi-torneo persistence:

```powershell
pytest -q tests\test_multi_tournament_migrations.py
```

Validar que:

- `competitions`, `seasons` y `competition_participants` existan;
- Mundial 2026 se siembre como default;
- las tablas operativas tengan `competition_id` y `season_id`;
- bases legacy migren sin perder filas;
- public bundle y outputs sigan funcionando con aliases actuales.
