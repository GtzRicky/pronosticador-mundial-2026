# Coding Standards - Quiniela Mundial 2026

**Task:** TASK-006 - Estandares de codigo y quality gates
**Fecha:** 2026-06-20
**Estado:** Reglas incrementales. Ruff y mypy configurados en modo gradual.

## 1. Proposito

Este documento define reglas de codigo para evolucionar el proyecto hacia una
arquitectura hexagonal sin exigir una limpieza masiva del legacy. Las reglas
estrictas aplican primero a codigo nuevo y a paquetes hexagonales futuros.

El paquete plano actual sigue siendo compatible mientras se migra por task,
pero no debe acumular nuevas dependencias cruzadas si existe una alternativa
por puerto, use case o adapter.

## 2. Estado actual

- Python declarado en `pyproject.toml`: `>=3.11`.
- Configuracion actual de tooling: pytest con `testpaths = ["tests"]` y
  `pythonpath = ["src"]`.
- Dependencias actuales en `requirements.txt`: pandas, numpy, scipy,
  scikit-learn, requests, python-dotenv, pydantic, typer, rich, unidecode,
  rapidfuzz y pytest.
- Ruff esta configurado de forma incremental en `pyproject.toml`.
- Mypy esta configurado con alcance inicial de bajo riesgo.
- No hay configuracion activa para ruff format, pytest-cov ni pre-commit.
- El legacy plano usa `sqlite3`, `requests`, `datetime.now()`, pandas,
  sklearn, filesystem y Typer en modulos que se migraran gradualmente.

## 3. Politica de Python

- Version objetivo: Python 3.11+.
- Nuevas features de lenguaje deben ser compatibles con Python 3.11.
- Antes de endurecer mypy/ruff, normalizar el entorno local para que `python
  --version` cumpla `>=3.11`.
- No introducir dependencias nuevas sin task explicita y justificacion.

## 4. Reglas de capas para codigo nuevo

### Dominio

Permitido:

- Libreria estandar.
- Dataclasses, enums, value objects, errores y servicios puros.
- Utilidades puras aprobadas que no dependan de infraestructura.

Prohibido:

- `sqlite3`, `requests`, Typer, Rich, pandas, sklearn.
- `quiniela.db`, `quiniela.config`, `quiniela.adapters.*`.
- Acceso a filesystem, `.env`, red, notificaciones o Task Scheduler.
- `datetime.now()` directo. Recibir tiempo como argumento o via `Clock`.

### Puertos

Permitido:

- Protocols/ABCs y tipos de dominio.
- DTOs internos pequenos y estables.

Prohibido:

- Implementaciones concretas.
- Imports de adapters, `db.py`, `requests`, Typer o settings.

### Aplicacion

Permitido:

- `domain` y `ports`.
- Casos de uso, commands, queries, workflows y policies.
- Tipos de entrada/salida propios.

Prohibido:

- Abrir conexiones SQLite.
- Importar `APIFootballClient`, ntfy/Discord concretos o RSS concreto.
- Escribir archivos directamente.
- Leer `.env`.
- `datetime.now()` directo en logica nueva.

### Adaptadores

Permitido:

- Librerias externas necesarias: `sqlite3`, `requests`, Typer, Rich, pandas,
  sklearn, filesystem.
- Mappers entre DTOs externos y contratos internos.

Reglas:

- Adaptadores outbound conocen proveedores; dominio y aplicacion no.
- Adaptadores inbound convierten argumentos de usuario a commands/use cases.
- Los scripts wrapper no deben contener logica de negocio.

## 5. Reglas de persistencia y datos

- Codigo nuevo no debe escribir SQLite directamente fuera de repositories o
  adapters SQLite.
- `db.py` puede seguir como facade legacy hasta que una task lo migre.
- Nuevos writes deben declarar idempotencia: unique key, conflict policy o
  idempotency key.
- Predicciones post-kickoff deben marcarse como no evaluables para metricas
  prepartido.
- Snapshots prepartido deben permanecer inmutables.
- Payloads externos no deben cruzar capas como `dict` crudo si ya existe DTO o
  mapper.

## 6. Reglas de tiempo

- Codigo nuevo debe recibir `Clock`, `now` o timestamp explicito.
- Usar UTC para registros internos y conservar timezone local solo para
  interfaz/operacion.
- Ventanas prepartido deben usar timestamps timezone-aware.
- No comparar strings de fecha si puede usarse `datetime`/`Timestamp`
  normalizado.

## 7. Reglas de errores

- Codigo nuevo debe preferir errores tipados de dominio/aplicacion.
- Errores operativos deben declarar si:
  - se reintentan;
  - bloquean el flujo;
  - degradan la prediccion;
  - generan `retrieval_issue`;
  - requieren runbook.
- No ocultar fallos de datos que puedan mezclar fixtures incorrectos.
- Fallos de notificacion no deben detener predicciones.

## 8. Estilo de codigo

- Modulos: `snake_case.py`.
- Clases: `PascalCase`.
- Funciones: verbo + objeto en `snake_case` cuando aplique.
- Constantes: `UPPER_SNAKE_CASE`.
- Enums: `PascalCase` y valores `lower_snake_case`.
- Casos de uso: `<Verb><Noun>UseCase`.
- Puertos: nombre de capacidad, por ejemplo `MatchRepository`.
- Adaptadores: proveedor + capacidad, por ejemplo `SQLiteMatchRepository`.
- Type hints obligatorios en codigo nuevo.
- Comentarios solo cuando reduzcan ambiguedad real.
- Evitar funciones nuevas largas; si pasan 40-60 lineas, justificar o partir
  por responsabilidad.

## 9. Ruff y format

Politica incremental:

1. Mantener `ruff check .` como guardrail minimo para errores obvios.
2. Aplicar reglas mas amplias primero a paquetes nuevos.
3. Evitar reformateo masivo del legacy en la misma task que cambia logica.
4. Cuando se active `ruff format`, hacerlo en task separada y con diff
   mecanico.

Reglas activas en TASK-012:

- `E9`: errores de sintaxis/indentacion de pycodestyle.
- `F63`: comparaciones invalidas de Pyflakes.
- `F7`: errores de flujo/control detectados por Pyflakes.
- `F82`: nombres indefinidos.

Reglas diferidas:

- `E` completo y `W`: estilo pycodestyle.
- `F` completo: limpieza general de imports/variables.
- `I`: orden de imports.
- `UP`, `B`, `SIM`, `C4`, `RUF`: modernizacion y simplificacion.
- `ruff format`: formato mecanico.

Exclusiones activas:

- `data`
- `outputs`

Notas:

- `line-length = 120` evita ruido mientras se estabiliza el legacy.
- No agregar `# noqa` nuevo sin explicar por que el falso positivo es aceptable.

## 10. Mypy

Politica gradual:

- Mypy no debe activarse estricto para todo el repo de golpe.
- Alcance inicial activo:
  - `src/quiniela/cache.py`;
  - `src/quiniela/poisson_model.py`;
  - `src/quiniela/ratings.py`;
  - `src/quiniela/name_maps.py`;
  - paquetes nuevos `domain`, `application`, `ports`, `adapters` e
    `infrastructure`.
- Legacy plano puede quedar en modo permissive mientras migra.
- Any explicito debe tener razon si cruza contratos.
- DTOs externos pueden ser tolerantes, pero mappers deben validar campos
  requeridos antes de entrar a aplicacion.
- El comando oficial es `mypy src\quiniela`; el `exclude` de `pyproject.toml`
  limita el alcance mientras se reduce deuda legacy.
- No usar `ignore_errors = true` global ni `# type: ignore` sin motivo local.
- Para ampliar cobertura, agregar primero tests o contratos de capa y luego
  ajustar el `exclude`.

## 11. Coverage y pre-commit

pytest-cov:

- Introducir primero como reporte informativo.
- No fallar el build por porcentaje global hasta estabilizar paquetes nuevos.
- Usar umbrales por paquete nuevo antes que umbral global legacy.

pre-commit:

- Activar en task dedicada.
- Hooks iniciales sugeridos: trailing whitespace, end-of-file, ruff check,
  ruff format cuando se apruebe.
- No incluir hooks que requieran red o secretos.

## 12. Comandos oficiales

Base actual:

```powershell
ruff check .
mypy src\quiniela
pytest -q
python scripts\05_fetch_today_data.py --help
python scripts\15_run_matchday.py --help
$env:PYTHONPATH='src'; python -m quiniela.cli --help
```

Por superficie:

```powershell
pytest -q tests\test_db_automation.py
pytest -q tests\test_matchday.py tests\test_notifications.py
pytest -q tests\test_output_manager.py tests\test_html_report.py
pytest -q tests\test_api_football_client.py tests\test_historical_loader_strict.py
pytest -q tests\test_odds_loader.py
pytest -q tests\test_player_evidence_db.py tests\test_player_evidence_model.py
```

Futuros, solo cuando se configuren:

```powershell
ruff format --check src tests
pytest --cov=quiniela --cov-report=term-missing
pre-commit run --all-files
```

## 13. Tolerancias temporales para legacy

Se permite temporalmente:

- `sqlite3.Connection` como parametro en modulos legacy.
- `get_connection()` desde `cli.py` y servicios legacy.
- `requests` en `api_football_client.py`, `notifications.py` y
  `web_lineup_fallback.py`.
- pandas/sklearn en modelos y outputs legacy.
- `datetime.now()` en legacy hasta que exista `Clock` y una task de migracion.

No se permite en codigo nuevo:

- Nuevo acceso directo a red fuera de adapters.
- Nuevos writes directos a SQLite fuera de repositories/adapters.
- Nuevos comandos CLI con logica de negocio embebida.
- Nuevos secretos en codigo, docs, tests, outputs o handoffs.
