# Notification Contracts

## Objetivo

Definir el contrato de cola, payloads, estados y reintentos para notificaciones de alineaciones/predicciones. Este documento no agrega canales ni cambia implementacion.

## Canales actuales

- `ntfy`
- `discord`

Los secretos y URLs privadas deben vivir en configuracion local, nunca en docs ni handoffs.

## Identidad de entrega

La tabla `notification_deliveries` usa una restriccion unica por:

- `match_id`
- `kickoff_at`
- `window_label`
- `channel`

Esto evita duplicados por ventana y canal. Adicionalmente, la migracion agrega
`dedupe_key` explicito derivado de:

- `notification_type`
- `match_id`
- `kickoff_at`
- `window_label`
- `channel`
- `team_norm` cuando aplica

`dedupe_key` tiene indice unico parcial cuando no es `NULL` y permite auditar
duplicados sin exponer payloads.

## Ventanas

`window_label` identifica el momento operacional.

Ejemplos:

- `t-60`
- `t-30`
- `t-5`

La entrega debe expirar si el reintento cae despues del kickoff.

## Estados

Estados abiertos:

- `pending`: entrega planificada.
- `waiting_prediction`: falta prediccion compatible con alineacion actual.
- `retry`: hubo fallo retryable.
- `sending`: entrega tomada por dispatcher.

Estados cerrados:

- `sent`: envio confirmado por canal.
- `failed`: fallo no retryable o reintentos agotados.
- `expired`: la ventana ya no es valida.
- `superseded`: otra alineacion o payload dejo obsoleta la entrega.

## Payload minimo

Campos esperados:

- `title`
- `message` o equivalente de canal.
- `match_id`
- `window_label`
- `kickoff`
- `poisson`
- `hybrid`
- `outcomes`
- `lineups`
- `freshness`
- `models`

Discord puede transformar estos campos en embeds. Ntfy puede mapear `title`, `message` y `priority`.

## Reintentos

Reglas actuales:

- HTTP 408, 429 y 5xx son retryable.
- `Retry-After` debe respetarse cuando el canal lo envia.
- Si el siguiente intento cae despues del kickoff, la entrega expira.
- Errores de red son retryable.

Campos de auditoria:

- `attempt_count`
- `next_attempt_at`
- `last_attempt_at`
- `last_http_status`
- `error_code`
- `max_attempts`
- `payload_hash`
- `expires_at`
- `last_error_message`
- `channel_priority`
- `template_version`

Comandos operativos:

- `notifications-status`: resume conteos por estado/canal/tipo y muestra filas recientes.
- `dry-run-notifications`: lista entregas vencidas que se enviarian sin mutar la outbox.
- `retry-failed-notifications --dry-run`: lista fallos reintentables antes de `expires_at`.
- `retry-failed-notifications --apply`: pasa fallos vigentes a `retry` con `next_attempt_at` actual.
- `explain-notification --id ID`: explica una entrega sin imprimir secretos de canal.

## Freshness y lineups

La notificacion debe preferir alineacion oficial cuando exista.

Si cambia la alineacion oficial:

- Entregas abiertas obsoletas pasan a `superseded`.
- Se agenda una entrega nueva si la ventana sigue vigente.

Si falta prediccion para la alineacion oficial:

- La entrega puede pasar a `waiting_prediction`.
- Debe reintentarse dentro de la ventana.

## Contrato de canal

Cada adapter debe devolver:

- `success`
- `status_code`
- `retryable`
- `retry_after_seconds`
- `error_code`

El dispatcher no debe conocer detalles internos del canal mas alla de ese contrato.

## Campos futuros recomendados

- `season_id`

`season_id` ya existe en el schema multi-torneo; su uso operativo queda para
queries por competencia en tasks futuras.

## Rollback operacional

Si un canal falla:

- Desactivar el canal afectado en configuracion.
- Mantener el otro canal si esta sano.
- No borrar filas historicas.
- Registrar incidente en `docs/RUNBOOK_INCIDENTES.md` o handoff.
