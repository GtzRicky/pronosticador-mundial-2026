# Runbook de Operacion

## Objetivo

Este runbook describe la operacion diaria del proyecto sin cambiar codigo ni asumir acceso a secretos. Debe usarse junto con `README.md`, `docs/API_POLICY.md`, `docs/RUNBOOK_INCIDENTES.md` y los handoffs vigentes.

## Selector de competencia

- Los wrappers operativos relevantes aceptan `--competition` y `--season`.
- Sin selector se usa `world_cup_2026`, conservando automatizaciones existentes.
- `--competition` acepta un nombre de `configs/competitions/` o una ruta YAML.
- `--season` valida la season del YAML; no selecciona otra implicitamente.
- Outputs, logs y bundles no default usan namespace propio y no sobrescriben
  aliases raiz del Mundial.

Validacion offline:

```powershell
python scripts\22_rebuild_outputs.py --help
python -m quiniela.cli doctor --format json --competition champions_league_2026_2027
```

Las configs ejemplo tienen API deshabilitada. No habilitar fetch live,
`--force-refresh` ni envios reales sin aprobacion explicita.

## Principios

- No exponer `.env`, tokens, webhooks ni topics privados.
- Preferir modo offline o cacheado para diagnostico.
- Usar APIs externas solo cuando el comando lo requiere.
- Publicar artefactos mediante escrituras atomicas y aliases estables.
- Registrar incidentes y decisiones en docs antes de automatizar nuevos pasos.

## Preparacion local

1. Confirmar Python 3.11 o superior.
2. Crear y activar `.venv`.
3. Instalar dependencias e instalar el repo en modo editable.
4. Revisar configuracion sin imprimir secretos.
5. Inicializar DB si el flujo lo requiere.
6. Ejecutar `pytest -q` antes de publicar cambios operativos.

Setup oficial para Windows/PowerShell:

```powershell
py -3.11 --version
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install -r requirements.txt
python -m pip install -e .
python -m quiniela.cli --help
```

Si `py` no refresca una instalacion por usuario, abrir una shell nueva o usar:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m venv .venv
```

Los instaladores de Task Scheduler prefieren automaticamente
`.\.venv\Scripts\python.exe`.

Chequeo read-only recomendado:

```powershell
python -m quiniela.cli doctor
python -m quiniela.cli doctor --format json
```

El doctor no consume API, no repara automaticamente y no imprime secretos.

## Operacion diaria

Flujo recomendado:

1. Actualizar calendario, plantillas o datos fuente segun disponibilidad.
2. Refrescar cache/API respetando `docs/API_POLICY.md`.
3. Recalcular predicciones.
4. Reconstruir outputs estables.
5. Revisar `data_quality.md`, `model_performance.md` y estado de automatizacion.
6. Despachar notificaciones solo si los canales estan configurados.
7. Verificar bundle publico antes de compartirlo.

## Matchday

Antes del partido:

- Confirmar que `matches.datetime_cdmx` y equipos normalizados son correctos.
- Verificar freshness de lineups, odds y predicciones.
- Revisar cola `notification_deliveries` para estados abiertos.
- Evitar reintentos manuales si la ventana ya expiro.

Durante la ventana de notificaciones:

- Ejecutar ciclo de notificaciones.
- Revisar estados `sent`, `retry`, `waiting_prediction`, `failed` y `expired`.
- No modificar payloads historicos ya enviados.

Despues del partido:

- Actualizar resultados reales.
- Ejecutar evaluaciones.
- Revisar drift, errores de datos y model performance.

## Publicacion de outputs

Outputs estables esperados:

- `predictions_latest.csv`
- `predictions_latest.json`
- `predictions_history.csv`
- `predictions_history.json`
- `data_quality.md`
- `model_performance.md`

Reglas:

- `outputs/` completo es artefacto local generado y no se incluye en commits o
  PRs del codigo fuente.
- No forzar `git add -f outputs/`.
- Para demos o entregas, regenerar y publicar fuera del repositorio como
  artefacto separado, con aprobacion humana.
- Un `public bundle` sanitizado elimina secretos operativos, pero no concede
  permiso de redistribucion de datos deportivos; revisar licencia y terminos
  antes de compartirlo.
- `data/db/`, `data/processed/model_artifacts/`, `.env`, `.venv` y metadata
  `*.egg-info/` tambien permanecen locales.
- No borrar aliases estables sin migracion documentada.
- No publicar archivos parciales.
- Si se introduce namespace multi-torneo, mantener alias Mundial 2026 durante la transicion.

Preflight de commit/PR:

```powershell
git status --short
git ls-files outputs data/db data/processed/model_artifacts .env
git check-ignore -v .env data/db/quiniela.db outputs/predictions/index.html
rg "\.env|API_FOOTBALL_KEY|DISCORD|NTFY|webhook|token" . -g "!data/**" -g "!outputs/**" -g "!.venv/**"
```

El segundo comando debe quedar vacio. Las coincidencias del ultimo comando
deben ser nombres de configuracion, placeholders o fakes de tests, nunca valores
reales.

## Modelos

Antes de activar un modelo:

- Completar `docs/MODEL_CARD_TEMPLATE.md`.
- Registrar dataset hash y ventana temporal.
- Comparar contra baseline.
- Definir rollback.
- Verificar que `model_training_runs` y `model_releases` reflejen el estado esperado si aplica.

## Notificaciones

Antes de activar canales:

- Validar configuracion requerida para ntfy o Discord sin imprimir secretos.
- Probar envio aislado si el entorno lo permite.
- Revisar deduplicacion por partido, ventana y canal.
- Confirmar que los reintentos no cruzan kickoff.

## Recuperacion rapida

Si falla API externa:

- Consultar `docs/API_POLICY.md`.
- Usar cache si esta dentro de TTL operativo.
- Registrar endpoint, modo degradado y ventana afectada.

Si fallan notificaciones:

- Consultar `docs/RUNBOOK_INCIDENTES.md`.
- Marcar canal afectado.
- Revisar `last_http_status`, `error_code` y `next_attempt_at`.

Si fallan outputs:

- Re-ejecutar reconstruccion de outputs.
- Verificar permisos y espacio en disco.
- No publicar directorios con archivos incompletos.

## Validacion minima

Antes de cerrar una task operativa:

```powershell
pytest -q
```

Cuando la task modifica docs solamente, ejecutar tambien las validaciones especificas de la task.
