# Agent Tournament Migration

## Objetivo

Esta guia define un flujo para que un agente migre el proyecto desde la
competencia default `world_cup_2026` hacia UEFA Champions League u otra
competencia, sin exponer secretos, sin mezclar datos entre torneos y sin romper
los aliases actuales del Mundial 2026.

El documento es operativo para agentes. No implementa la migracion real ni
autoriza llamadas live por si mismo.

## Principios obligatorios

- La competencia default sigue siendo `fifa_world_cup` / `world_cup_2026`
  hasta que una task explicita cambie el default.
- Toda competencia nueva debe partir de un archivo declarativo en
  `configs/competitions/`.
- Nunca copiar `.env`, API keys, topics ntfy, webhooks Discord, tokens ni dumps
  privados en prompts, docs, handoffs o logs.
- No ejecutar comandos live/API sin permiso explicito del usuario y sin advertir
  cuota, proveedor y fecha/season.
- Mantener outputs historicos como aliases estables mientras consumidores
  externos dependan de `outputs/predictions/index.html` y `today.html`.
- No mezclar datos de competitions/seasons distintas sin `competition_id`,
  `season_id` y namespace de outputs.
- Todo cambio de schema debe incluir backfill, validacion idempotente y rollback.
- Todo cambio operativo debe cerrar con handoff en `docs/handoffs/`.

## Permisos requeridos

### Seguro/offline

Estos permisos suelen ser suficientes para preparar una migracion:

- Leer repo, docs, configs, scripts y tests.
- Escribir docs, tests y configs de ejemplo.
- Ejecutar tests offline con `pytest -q`.
- Ejecutar comandos read-only o cacheados como `doctor`, `report-api-usage`,
  `public-bundle-status` y `notifications-status`.
- Reconstruir outputs desde SQLite local cuando la DB ya existe.

### Requiere aprobacion explicita

Pedir aprobacion antes de:

- Llamar API-Football o cualquier RSS/noticias live.
- Ejecutar backfills historicos o refresh con `--force-refresh`.
- Enviar notificaciones reales con ntfy o Discord.
- Activar un release de modelo.
- Exportar o compartir bundles publicos con datos deportivos enriquecidos.
- Cambiar el default operativo de competencia.
- Instalar o modificar tareas programadas del sistema.

## Estado base esperado

Antes de migrar, verificar:

```powershell
python -m quiniela.cli doctor
pytest -q tests\test_competition_config.py tests\test_multi_tournament_migrations.py
pytest -q tests\test_output_manager.py tests\test_html_report.py
```

La migracion debe apoyarse en:

- `configs/competitions/world_cup_2026.yaml` como default compatible.
- `configs/competitions/champions_league_2026_2027.yaml` como ejemplo no activo.
- `docs/MULTI_TOURNAMENT_DESIGN.md` para entidades y reglas.
- `docs/RUNBOOK_OPERACION.md` para seguridad operativa.
- `README.md` para flujo end-to-end actual.

## Flujo recomendado

### 1. Crear o revisar config de competencia

Para Champions League, empezar por `configs/competitions/champions_league_2026_2027.yaml`
o crear una variante nueva si la season cambia.

Checklist:

- `competition.slug` unico, por ejemplo `uefa_champions_league`.
- `season.slug` unico, por ejemplo `champions_league_2026_2027`.
- `competition_type` correcto: `clubs`.
- `default_timezone` y `season.timezone` definidos.
- `rules.profile` documenta formato de torneo.
- `data_sources` apunta a inputs locales nuevos, no a archivos del Mundial.
- `api_football.enabled` queda `false` hasta confirmar liga/season y permisos.
- `outputs.namespace` no colisiona, por ejemplo `champions-league-2026-2027`.
- `outputs.stable_aliases` debe ser `false` para toda competencia no default.

Validacion offline:

```powershell
pytest -q tests\test_competition_config.py
```

Handoff minimo:

- Config creada o revisada.
- Campos inciertos y fuente de verificacion.
- Decision explicita sobre `stable_aliases`.

### 2. Preparar datos fuente locales

No reutilizar parsers del Mundial si la estructura de calendario/planteles no
calza. Para una nueva competencia, crear inputs separados bajo `data/raw/` y
mantenerlos fuera del flujo default hasta tener parser y tests.

Comandos offline actuales:

```powershell
python scripts\01_ingest_calendar.py --competition world_cup_2026
python scripts\02_ingest_rosters.py --competition world_cup_2026
python scripts\03_init_db.py --competition world_cup_2026
```

Sin selector, los scripts conservan `world_cup_2026` como default. Para una
competencia ejemplo, el CLI carga su YAML y namespace, pero rechaza ingestion si
el parser declarado aun no tiene adaptador.

Handoff minimo:

- Inputs nuevos y origen.
- Parser usado o pendiente.
- Riesgo de colisiones de nombres entre clubes y selecciones.

### 3. Backfill historico

El backfill historico es live/API salvo que la cache ya contenga todas las
respuestas. Debe ejecutarse por competencia, season y participantes verificados.

Comandos relevantes:

```powershell
python scripts\04_fetch_priority_history.py --teams "Equipo A,Equipo B" --dry-run --competition COMPETITION
python scripts\05_fetch_today_data.py --date YYYY-MM-DD --dry-run --competition COMPETITION
python scripts\29_fetch_odds.py --date YYYY-MM-DD --dry-run --competition COMPETITION
python scripts\30_build_odds_consensus.py --date YYYY-MM-DD --competition COMPETITION
```

Reglas:

- Ejecutar primero `--dry-run` cuando exista.
- Una config con `api_football.enabled: false` bloquea fetch live incluso con
  selector; habilitarla requiere revision y aprobacion.
- Confirmar `api_football.league_id` y `api_football.season` contra el proveedor
  antes de consumir cuota.
- Registrar cuota esperada y cuota observada con `report-api-usage`.
- No usar `--force-refresh` sin razon documentada.
- Para clubes, validar cobertura de planteles, lineups, lesiones y odds; no
  asumir que equivale al Mundial.
- Toda fila nueva debe tener `competition_id`, `season_id` y llaves idempotentes.

Validaciones:

```powershell
python scripts\08_report_api_usage.py
pytest -q tests\test_api_football_provider.py tests\test_historical_loader_strict.py tests\test_odds_loader.py
```

Handoff minimo:

- Endpoints consultados.
- Parametros de liga/season.
- Cuota consumida.
- Datos faltantes o degradados.
- Rollback: backup de DB o queries de limpieza por `season_id`.

### 4. Generar predicciones por competencia

No mezclar predicciones entre torneos. El agente debe comprobar que la consulta
de partidos, features y outputs filtra por `season_id` antes de habilitar una
competencia no default.

Comandos actuales:

```powershell
python scripts\06_predict_match.py --date YYYY-MM-DD --competition COMPETITION
python scripts\06_predict_match.py --home "Local" --away "Visitante" --competition COMPETITION
python scripts\23_evaluate_predictions.py --competition COMPETITION
```

Reglas:

- `--season` valida que la season coincida con el YAML seleccionado.
- Los outputs no default se escriben bajo
  `outputs/predictions/<namespace>/`; solo Mundial 2026 actualiza aliases raiz.
- Guardar `prediction_context`, `window_label`, `data_freshness_at`,
  `audit_degradation_reasons_json` y `not_evaluable_reason`.
- Si faltan odds o lineups, registrar degradacion en vez de inventar senales.

Validaciones:

```powershell
pytest -q tests\test_prediction_auditability.py tests\test_output_manager.py
```

Handoff minimo:

- Partidos predichos.
- Freshness de datos.
- Degradaciones.
- Comportamiento de canonica pre-kickoff.

### 5. Entrenar modelos por competencia

Los modelos del Mundial no deben activarse automaticamente para Champions
League. Pueden servir como baseline tecnico, pero las metricas de clubes deben
medirse por separado.

Comandos:

```powershell
python scripts\13_train_player_model.py --min-matches 20
python scripts\14_train_outcome_model.py --min-matches 40 --min-per-class 8
python scripts\18_train_player_evidence.py --reconstruct-history
python scripts\19_evaluate_model_release.py --release-id RELEASE_ID
```

Reglas:

- Dataset hash, ventana temporal y `season_id` deben quedar documentados.
- Separar metricas globales de metricas especificas de competencia.
- Para clubes, revisar si features de seleccion nacional siguen teniendo sentido.
- Completar model card antes de proponer activacion.
- No activar si hay menos cobertura que el minimo acordado o si empeora gates.

Handoff minimo:

- Dataset, hash, rango temporal.
- Artefactos creados.
- Comparacion contra baseline.
- Decision de no activar o candidato recomendado.

### 6. Activar modelo por competencia

Activar un release cambia comportamiento operativo; requiere aprobacion.

Comando:

```powershell
python scripts\20_activate_model_release.py --release-id RELEASE_ID
```

Reglas:

- Validar que el release corresponde a la competencia/season esperada.
- Registrar rollback: release previo activo, artefactos y comando/documento para
  restaurarlo.
- Si el schema todavia no soporta activacion por competencia, no activar para
  Champions League; dejarlo como candidato evaluado.

Validaciones:

```powershell
python scripts\22_rebuild_outputs.py
pytest -q tests\test_player_evidence_release.py tests\test_output_manager.py
```

Handoff minimo:

- Release activado o retenido.
- Motivo.
- Rollback.
- Efecto esperado en outputs.

### 7. Reconstruir outputs namespaced

Para una competencia nueva, publicar bajo namespace y conservar aliases del
Mundial como default.

Comandos:

```powershell
python scripts\22_rebuild_outputs.py
python scripts\12_render_html_report.py --date YYYY-MM-DD
python scripts\24_cleanup_obsolete_outputs.py --dry-run
```

Reglas:

- No borrar `outputs/predictions/index.html` ni `today.html`.
- Verificar `outputs/predictions/<namespace>/`.
- CSV/JSON deben conservar `competition_id`, `season_id`, freshness, release y
  campos de auditoria disponibles.
- HTML debe mostrar modelo, release, odds-aware, degradaciones y freshness.

Validaciones:

```powershell
pytest -q tests\test_output_manager.py tests\test_html_report.py
```

Handoff minimo:

- Archivos generados.
- Namespace usado.
- Aliases estables conservados.
- Riesgos de consumidores externos.

### 8. Probar notificaciones

Las pruebas de notificacion pueden ser dry-run/offline o live segun canal. No
enviar mensajes reales sin aprobacion.

Comandos seguros/read-only:

```powershell
python scripts\25_dispatch_notifications.py --help
python -m quiniela.cli notifications-status
python -m quiniela.cli dry-run-notifications
python -m quiniela.cli retry-failed-notifications --dry-run
python -m quiniela.cli explain-notification --id 1
```

Comandos live o con efectos:

```powershell
python scripts\26_test_notifications.py --channel ntfy
python scripts\26_test_notifications.py --channel discord
python scripts\31_send_lineup_test_notifications.py --date YYYY-MM-DD --send
python scripts\25_dispatch_notifications.py
python -m quiniela.cli retry-failed-notifications --apply
```

Reglas:

- No imprimir topic ntfy ni webhook Discord.
- No reintentar notificaciones expiradas.
- Verificar `dedupe_key`, `expires_at`, `payload_hash` y canal antes de enviar.
- Para nueva competencia, confirmar que el texto no dice Mundial si no aplica.

Validaciones:

```powershell
pytest -q tests\test_notifications.py
```

Handoff minimo:

- Canales probados.
- Modo dry-run o live.
- IDs de entregas si aplica, sin payload secreto.
- Errores y proxima accion.

### 9. Bundle publico y licencias

Exportar un bundle puede redistribuir datos deportivos. Requiere revision de
licencia y acuerdo del proveedor.

Comandos:

```powershell
python scripts\09_export_public_bundle.py --no-archive
python scripts\11_public_bundle_status.py
python scripts\10_import_public_bundle.py --bundle-path outputs\bundles\quiniela_public_bundle.zip
```

Reglas:

- No incluir `.env`, `api_cache`, `api_usage` ni `notification_deliveries`.
- Revisar si datos de UEFA/API-Football permiten redistribucion.
- Indicar si el bundle es interno, demo o publicable.

## Matriz rapida de comandos

| Comando | Tipo | Efecto externo |
| --- | --- | --- |
| `doctor` | offline/read-only | No API, no secretos |
| `ingest-calendar`, `ingest-rosters`, `init-db` | local write | Escribe SQLite/processed |
| `fetch-history`, `fetch-today`, `fetch-odds` | live/API salvo dry-run/cache | Consume cuota |
| `build-odds-consensus` | local write | No API |
| `predict`, `evaluate-predictions` | local write | No API esperada |
| `train-*`, `evaluate-model-release` | local write | No API esperada |
| `activate-model-release` | local write critico | Cambia modelo activo |
| `rebuild-outputs`, `render-html-report` | local write | Publica outputs |
| `test-notifications`, `dispatch-notifications` | live si canales activos | Puede enviar mensajes |
| `dry-run-notifications`, `notifications-status`, `explain-notification` | offline/read-only | No envia |
| `export-public-bundle` | local write/publicacion | Revisar licencia |
| `install_*_task.ps1` | sistema operativo | Modifica Task Scheduler |

## Handoff por etapa

Cada etapa debe cerrar con un handoff o actualizar uno existente:

- Config: decision de namespace, default y parser.
- Backfill: endpoints, cuota, cobertura y rollback.
- Prediccion: partidos, freshness y degradaciones.
- Modelo: dataset hash, metricas y gate.
- Activacion: release activo, rollback y aprobacion.
- Outputs: archivos, namespace y aliases.
- Notificaciones: dry-run/live, canales y dedupe.
- Bundle: alcance, sanitizacion y licencia.

El handoff nunca debe depender de memoria externa. Debe permitir que el
siguiente agente continue solo con repo, docs, tests y comandos.

## Rollback general

Para docs/configs:

- Revertir el documento o config nueva.

Para DB:

- Restaurar backup previo o limpiar por `competition_id`/`season_id` con script
  revisado y aprobado.

Para outputs:

- Reejecutar `python scripts\22_rebuild_outputs.py` para regenerar aliases.
- No eliminar namespaces hasta confirmar que no hay consumidores.

Para modelos:

- Reactivar release previo documentado o mantener fallback `v1`.

Para notificaciones:

- Desactivar `NOTIFICATIONS_ENABLED`.
- No borrar historial enviado; marcar fallos/reintentos de forma auditada.
