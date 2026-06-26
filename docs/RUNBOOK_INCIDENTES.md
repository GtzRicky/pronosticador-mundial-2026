# Runbook de Incidentes - Quiniela Mundial 2026

**Task:** TASK-007 - Diseno de state machine y taxonomia de errores
**Fecha:** 2026-06-20
**Estado:** Runbook inicial documental. No ejecuta acciones live.

## 1. Proposito

Este runbook traduce la taxonomia de errores a acciones operativas seguras. La
prioridad es no mezclar datos incorrectos, no perder trazabilidad y no detener
predicciones por fallos externos degradables.

Documentos relacionados:

- `docs/RUNBOOK_OPERACION.md`: operacion diaria y matchday.
- `docs/DATA_CONTRACTS.md`: contratos de tablas, outputs y modelos.
- `docs/NOTIFICATION_CONTRACTS.md`: cola, payloads, estados y reintentos.
- `docs/API_POLICY.md`: TTL, cuota, degraded mode y circuit breaker.

## 2. Reglas generales

- No publicar secretos ni copiar `.env` en tickets, logs o handoffs.
- No modificar `data/db/quiniela.db` manualmente sin backup.
- Preferir cache y outputs existentes antes de forzar llamadas live.
- Si hay mismatch de fixture o equipo, bloquear ese dato antes que guardarlo.
- Si falla notificacion, no detener prediccion.
- Si falla modelo candidato, conservar release activo anterior.

## 3. Comandos seguros de diagnostico

```powershell
pytest -q
python scripts\05_fetch_today_data.py --help
python scripts\15_run_matchday.py --help
python scripts\25_dispatch_notifications.py --help
git status --short
```

Comandos live o con efectos externos requieren decision explicita porque pueden
consumir cuota, escribir DB, regenerar outputs o enviar notificaciones.

## 4. Incidentes por categoria

| Categoria | Sintoma | Accion inmediata | Validacion | Escalamiento |
|---|---|---|---|---|
| API transient | timeout, 408, 5xx, red intermitente | Reintentar segun policy; usar cache si existe. | `tests\test_api_football_client.py` | Si persiste, registrar endpoint/modo. |
| Quota/auth | 429, limite diario, API key ausente/invalida | No forzar refresh; operar degradado/offline. | Revisar `api_usage` via report. | Ajustar plan/key fuera del repo. |
| Schema changed | payload sin campos esperados | Bloquear mapper afectado; crear fixture sanitizado. | Test nuevo/fallido de provider. | Task de adapter/API policy. |
| Fixture mismatch | equipo desconocido o par no coincide | No guardar fixture; registrar `retrieval_issue`. | `tests\test_historical_loader_strict.py` | Revisar aliases/calendario. |
| Lineup unavailable | sin XI oficial cerca kickoff | Usar estimated/inferred/fallback web si permitido. | `tests\test_web_lineup_fallback.py` | Revisar fuente/proveedor. |
| Automation lock | run_key queda running | Usar recovery con `--dry-run` antes de `--apply`. | `tests\test_db_automation.py` | Revisar Task Scheduler y logs. |
| Notification failure | ntfy/Discord timeout/4xx/5xx | Mantener prediccion; retry si transient; expirar si permanente. | `tests\test_notifications.py` | Revisar canal sin exponer secreto. |
| Evidence incomplete | no hay snapshot/stats/lineups suficientes | Marcar no elegible; no entrenar con datos incompletos. | `tests\test_player_evidence_db.py` | Revisar cobertura fixture. |
| Model gate failed | candidato no mejora o empeora metricas | No activar; conservar champion. | `tests\test_player_evidence_model.py` | Revisar model card/dataset. |
| Output failure | CSV/JSON/HTML no se escribe | Regenerar outputs despues de resolver causa filesystem/DB. | `tests\test_output_manager.py`, `tests\test_html_report.py` | Revisar permisos/rutas. |

## 5. Idempotencia y rollback operativo

- Automation: `run_key` evita duplicados; stale recovery marca fallos antes de
  reclamar de nuevo.
- Snapshots: no sobrescribir snapshots existentes de la misma ventana/source.
- Notifications: dedupe por ventana/hash/canal; no reenviar manualmente sin
  revisar outbox.
- Predictions: history conserva todas las corridas; latest se reconstruye desde
  DB.
- Model releases: activar solo releases aprobados; rollback conserva release
  anterior.

## 6. Errores y comportamiento esperado

| Error | Retry | Bloquea | Degrada | Registro esperado |
|---|---|---|---|---|
| `TransientApiError` | si | no con cache | si | api usage/log |
| `ApiQuotaExceeded` | no hasta reset | live fetch | si | api usage/runbook |
| `ApiAuthError` | no | live fetch | si | runbook sin secret |
| `ApiSchemaChanged` | no | endpoint | si | retrieval issue/test |
| `ApiDataMismatch` | no | fixture candidato | no usar dato | retrieval issue |
| `LineupUnavailable` | si cerca kickoff | no | si | prediction source |
| `OfficialResultUnavailable` | si | no | no | match pending |
| `PredictionNotEvaluable` | no | no | no | output/history |
| `ModelGateFailed` | no | release | no | model report |
| `NotificationTransientError` | si | no | no | delivery attempts |
| `NotificationPermanentError` | no | no | no | delivery failed/expired |
| `AutomationLockError` | si recovery | run_key | no | automation_runs |

## 7. Politica operativa API

Referencia detallada: `docs/API_POLICY.md`.

### 7.1 Antes de forzar refresh

1. Revisar si la accion puede operar con cache.
2. Revisar cuota disponible con reportes locales, sin imprimir API key.
3. Confirmar modo adecuado: `hourly`, `pre_match`, `post_status`, `lineups` o
   `full`.
4. Evitar `full` cerca de multiples kickoffs si la reserva critica esta baja.

### 7.2 Respuesta por incidente API

| Incidente API | Accion segura | Accion a evitar |
|---|---|---|
| API key ausente | Operar offline/degradado; documentar `missing_api_key`. | Pegar `.env` o key en handoff. |
| 429/cuota agotada | Detener live fetch no critico; usar cache/stale. | Reintentos agresivos. |
| 408/5xx/red | Retry con backoff; cache/stale si falla. | Marcar datos como frescos si son stale. |
| Schema changed | Abrir incidente de adapter con fixture sanitizado. | Guardar payload ambiguo como valido. |
| Fixture mismatch | Bloquear fixture candidato y registrar `retrieval_issue`. | Asociar fixture por coincidencia parcial. |
| Odds no disponibles | Continuar Poisson/Logit; marcar odds degradado. | Inventar consenso sin mercado. |

### 7.3 Modos seguros

- `post_status`: minimo viable para saber si hay resultado.
- `lineups`: foco en XI oficial sin odds/stats.
- `pre_match`: lineups + odds + fixtures; sin stats pesados.
- `hourly`: fixtures/status/lineups/odds.
- `full`: usar con margen de cuota o final nuevo.
