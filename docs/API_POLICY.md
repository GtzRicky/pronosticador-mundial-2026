# API Policy - Quiniela Mundial 2026

**Task:** TASK-008 - Diseno de API resiliente y politica de cuota
**Fecha:** 2026-06-20
**Estado:** Politica documental. No cambia `APIFootballClient` ni agrega tablas.

## 1. Proposito

Formalizar cache, TTL, endpoint budgets, critical reserve, circuit breaker,
raw payload audit y degradacion para API-Football antes de extraer el puerto
`FootballDataProvider`.

El comportamiento actual ya es cache-first, soporta `dry_run`, respuesta vacia
si falta API key, retry para errores transitorios y control de limite diario.
Esta politica define el contrato objetivo sin modificar el cliente.

## 2. Estado actual observado

`APIFootballClient`:

- consulta `api_cache` antes de red si no hay `force_refresh`;
- borra cache puntual con `force_refresh`;
- registra uso en `api_usage`;
- devuelve respuesta vacia para `dry_run`;
- devuelve respuesta vacia si falta `API_FOOTBALL_KEY`;
- bloquea cuando `count_api_requests_today >= API_DAILY_LIMIT`;
- reintenta `requests.RequestException` y HTTP `408`, `500`, `502`, `503`,
  `504`;
- eleva `APILimitReachedError` con HTTP `429`;
- persiste payload exitoso en cache.

`fetch_today_data`:

- valida fixtures contra calendario local antes de guardar candidatos;
- usa modos `full`, `hourly`, `pre_match`, `post_status`, `lineups`;
- usa `API_CRITICAL_RESERVE` para decidir si trae stats/eventos pesados;
- genera `RetrievalValidationError` con `issues` ante mismatch.

## 3. Endpoints inventariados

| Endpoint | Uso actual | Criticidad |
|---|---|---|
| `/teams` | Resolver selecciones/equipos a IDs del proveedor. | Media |
| `/fixtures` | Fixtures por fecha, fixture por id, historial por equipo. | Alta |
| `/fixtures/lineups` | XI oficial y lineups por fixture. | Alta cerca de kickoff |
| `/fixtures/statistics` | Estadisticas de equipo por fixture. | Media/alta postpartido |
| `/fixtures/events` | Eventos del partido. | Media/alta postpartido |
| `/fixtures/players` | Estadisticas por jugador del fixture. | Alta para evidence |
| `/players` | Perfil/estadisticas de jugadores. | Media |
| `/players/seasons` | Temporadas disponibles por jugador. | Baja/media |
| `/odds` | Odds por fecha o fixture. | Media/alta prepartido |
| `/odds/bookmakers` | Catalogo de bookmakers. | Baja |
| `/odds/bets` | Catalogo de mercados. | Baja |

## 4. RateLimitPolicy

Entradas:

- `api_daily_limit`
- `api_critical_reserve`
- `used_requests_today`
- `mode`
- `endpoint`
- `time_to_kickoff`
- `cache_available`

Reglas:

1. Nunca consumir la reserva critica para endpoints no criticos.
2. La reserva critica se protege para `T-15`, `T-5`, `T-1` y resultado final.
3. Si `used_requests_today >= api_daily_limit`, bloquear live fetch y operar
   con cache/degradacion.
4. Si falta API key, no intentar red y devolver respuesta degradada controlada.
5. Cache hit no consume presupuesto live, pero si se registra en auditoria.
6. `force_refresh` no puede saltarse limite diario ni auth.

## 5. EndpointBudgetPolicy por modo

| Modo | Endpoints permitidos | Endpoints evitados | Regla de cuota |
|---|---|---|---|
| `full` | `/fixtures`, lineups, statistics, events, players, odds, player seasons cuando hay margen. | Ninguno si hay cuota suficiente. | Puede consumir mas, pero no baja de `API_CRITICAL_RESERVE` para no criticos. |
| `hourly` | `/fixtures`, lineups, odds. | Stats/eventos/player stats pesados salvo final nuevo. | Mantener fresco sin trabajo pesado. |
| `pre_match` | `/fixtures`, `/fixtures/lineups`, `/odds`; fallback web fuera de API-Football si aplica. | Stats/eventos/player stats pesados. | Proteger T-15/T-5/T-1; preferir cache si cuota baja. |
| `post_status` | `/fixtures` para status/resultado. | Lineups/odds/stats pesados salvo final confirmado y con margen. | Budget liviano recurrente por slot. |
| `lineups` | `/fixtures/lineups` y fixture minimo si hace falta resolver. | Odds/stats/events/players. | Alta prioridad cerca de kickoff. |

## 6. TTL objetivo por endpoint

| Endpoint / consulta | TTL objetivo | Stale permitido | Notas |
|---|---:|---:|---|
| `/teams` search | 30 dias | si | IDs estables; invalidar manual si mismatch. |
| `/players/seasons` | 7 dias | si | No es critico en ventana prepartido. |
| `/players` season/profile | 24 horas a 7 dias | si | Segun cobertura y torneo. |
| `/fixtures` futuro | 1 a 6 horas | si | Programacion puede cambiar; refresh horario basta. |
| `/fixtures` dia partido | 5 a 15 minutos | si con etiqueta stale | Critico para status/kickoff. |
| `/fixtures/lineups` lejos kickoff | 15 a 30 minutos | si | Antes de T-60 no gastar agresivo. |
| `/fixtures/lineups` T-30 a T-1 | 1 a 5 minutos | si, pero marcar degradacion | Proteger T-15/T-5/T-1. |
| `/fixtures/events` live/final | 5 a 15 minutos | si | Postpartido puede reintentar. |
| `/fixtures/statistics` final | permanente si completo | si | Revalidar solo si incompleto. |
| `/fixtures/players` final | permanente si completo | si | Evidence depende de completitud. |
| `/odds` | 15 a 60 minutos | si con freshness | Frescura pesa mas cerca del kickoff. |
| `/odds/bookmakers`, `/odds/bets` | 30 dias | si | Catalogos. |

TTL no implica borrar cache inmediatamente. Define cuando una respuesta es
fresca, stale usable o requiere revalidacion si hay presupuesto.

## 7. Circuit breaker

Circuit breaker objetivo por endpoint/provider:

- `closed`: llamadas permitidas.
- `open`: llamadas live bloqueadas temporalmente; usar cache/stale/degradacion.
- `half_open`: permitir una llamada de prueba con budget bajo.

Abrir circuito cuando:

- multiples timeouts/5xx consecutivos por endpoint;
- schema changed confirmado;
- auth failure global;
- cuota agotada;
- latencia excede timeout operacional repetidamente.

Cerrar circuito cuando:

- llamada half-open exitosa;
- se alcanza ventana de reset de cuota;
- se corrige API key/config;
- se actualizan mappers/tests ante schema changed.

El circuito no debe impedir usar cache valida o stale etiquetada.

## 8. Stale-while-revalidate y degradacion

Reglas:

- Si cache esta fresca, usar cache.
- Si cache esta stale y endpoint no critico, usar stale y registrar freshness.
- Si cache esta stale y endpoint critico, intentar revalidar si hay budget; si
  falla, usar stale con `degradation_reason`.
- Si no hay cache y no hay API key/cuota, devolver vacio controlado y marcar
  degradacion.
- Nunca mezclar fixture candidato con calendario si strict validation falla.

Degradation reasons recomendadas:

- `missing_api_key`
- `quota_exceeded`
- `stale_cache_used`
- `provider_unavailable`
- `lineup_unavailable`
- `odds_unavailable`
- `fixture_validation_failed`
- `schema_changed`

## 9. Raw payload audit

Objetivo futuro:

- Guardar hash SHA-256 canonical del payload bruto relevante.
- Registrar endpoint, params hash, fetched_at, status_code, cache_hit,
  provider, schema_version observada y payload_hash.
- Archivar payload bruto solo si politica de licencia/espacio lo permite.
- Para payloads no archivados, conservar hash y resumen suficiente para
  auditoria.

Sin nuevas tablas en TASK-008. Implementaciones futuras pueden usar:

- extension de `api_cache`;
- tabla `raw_payload_audit`;
- archivos bajo `data/raw/api_football/` con manifest sanitizado.

No incluir API keys ni headers sensibles en payload audit.

## 10. Retrieval issues

`retrieval_issues` deben generarse cuando un dato externo no puede usarse de
forma segura:

| Reason | Bloquea almacenamiento | Degrada prediccion | Ejemplo |
|---|---|---|---|
| `unknown_candidate_team_name` | si | si | Equipo candidato no normalizable. |
| `scheduled_team_mismatch` | si | si | Fixture API no coincide con calendario. |
| `duplicate_candidate_fixture` | si | si | Dos fixtures compiten por mismo partido. |
| `duplicate_scheduled_pair` | si | si | Calendario local ambiguo. |
| `schema_changed` | endpoint afectado | si | Campo requerido ausente/cambio de tipo. |
| `lineup_unavailable` | no necesariamente | si | Sin XI oficial cerca kickoff. |
| `odds_unavailable` | no | si | Mercado no disponible o stale. |

Los issues deben aparecer en automation failure details cuando bloquean un
flujo y en reportes de calidad cuando degradan prediccion.

## 11. Manejo de errores

| Error | Comportamiento |
|---|---|
| `TransientApiError` | Retry con backoff/jitter; cache/stale si falla. |
| `ApiQuotaExceeded` | Bloquear live fetch; preservar reserva; degradar. |
| `ApiAuthError` | No reintentar; operar offline/degradado; revisar `.env` sin imprimir secretos. |
| `ApiSchemaChanged` | Abrir circuito endpoint; crear fixture/test; no guardar dato ambiguo. |
| `ApiDataMismatch` | Bloquear fixture candidato; registrar `retrieval_issue`. |
| `FixtureResolutionError` | No mezclar IDs; reintentar solo tras aliases/config. |
| `LineupUnavailable` | Usar estimated/inferred/fallback; marcar source. |
| `OddsMarketUnavailable` | Continuar Poisson/Logit; odds-aware degradado. |

## 12. Politica por ventana prepartido

| Ventana | Prioridad API | Reglas |
|---|---|---|
| T-60 | Media | Refresh pre_match; no usar fallback web; no gastar stats pesados. |
| T-30 | Alta | Lineups y odds; fallback web permitido si no hay XI oficial. |
| T-15 | Critica | Proteger reserva; lineups frescas; notificacion T-15. |
| T-5 | Critica | Lineups/odds frescas si budget; no stats pesados. |
| T-1 | Critica | Ultimo snapshot; usar stale etiquetado si provider falla. |

`API_CRITICAL_RESERVE` debe dimensionarse para cubrir al menos lineups/status de
partidos cercanos y resultado final. La task futura de implementacion debe
convertir esto en calculo por calendario, no constante ciega.

## 13. Tests que protegen la politica actual

- `tests/test_api_football_client.py`: retry ante error transitorio.
- `tests/test_historical_loader_strict.py`: validation estricta y
  `RetrievalValidationError`.
- `tests/test_matchday.py`: degradacion de outputs ante fallo y ventanas.
- `tests/test_notifications.py`: notificaciones no detienen matchday.
- `tests/test_odds_loader.py`: consenso/odds-aware auxiliar.

Validacion documental de esta task:

```powershell
pytest -q tests\test_api_football_client.py tests\test_historical_loader_strict.py
python scripts\05_fetch_today_data.py --help
```

## 14. Rollback

Esta task es documental. Rollback:

- Revertir `docs/API_POLICY.md`.
- Revertir la seccion API agregada a `docs/RUNBOOK_INCIDENTES.md`.
- Mantener cliente y DB sin cambios.
