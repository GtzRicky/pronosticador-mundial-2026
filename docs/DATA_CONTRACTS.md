# Data Contracts

## Objetivo

Este documento describe contratos de datos actuales y deseados para evitar cambios silenciosos entre parsers, DB, modelos, reportes y outputs.

## Reglas generales

- Las identidades normalizadas deben ser estables.
- Los timestamps deben declarar zona o almacenarse en UTC cuando representen eventos operativos.
- `source_json` conserva payload original o resumen suficiente para auditoria.
- Los campos derivados deben poder recalcularse desde fuentes persistidas.
- Los contratos futuros multi-torneo deben agregar `competition_id` y `season_id` sin romper Mundial 2026.

## Teams

Tabla actual: `teams`.

Campos clave:

- `team_norm`: identidad normalizada.
- `display_name`: nombre visible.
- `group_name`: grupo Mundial 2026.

Contrato:

- `team_norm` es la llave de integracion actual.
- `display_name` no debe usarse como llave.
- En multi-torneo, `team_norm` debe migrar a catalogo global y `Participant` debe representar la season.

## Players

Tabla actual: `players`.

Campos clave:

- `team_norm`
- `player_name`
- `position`
- `source`

Contrato:

- El nombre de jugador es texto operacional, no identidad global garantizada.
- Para clubes y multiples seasons se requiere identidad de jugador separada de squad.

## Matches

Tabla actual: `matches`.

Campos clave:

- `match_id`
- `home_team_norm`
- `away_team_norm`
- `datetime_cdmx`
- `stage`
- `status`
- `home_score`
- `away_score`

Contrato:

- `match_id` es la identidad local del partido.
- `datetime_cdmx` es la referencia visible del proyecto actual.
- Estados terminales no deben recibir notificaciones nuevas.
- En multi-torneo se agregara `season_id` y posiblemente `stage_id`.

## API cache y usage

Tablas actuales: `api_cache`, `api_usage`.

Contrato:

- La cache es un mecanismo de freshness y resiliencia, no una fuente canonica permanente.
- `api_usage` debe permitir auditoria de endpoint, fecha y modo.
- TTL y degraded mode se documentan en `docs/API_POLICY.md`.

## Odds

Tablas actuales:

- `odds_market_snapshots`
- `odds_market_consensus`

Contrato de snapshots:

- Una fila representa una observacion de bookmaker, mercado, linea y seleccion.
- `decimal_odd > 1`.
- `implied_probability = 1 / decimal_odd`.
- `source_update` viene de la fuente si existe.

Contrato de consenso:

- Una fila representa una seleccion normalizada dentro de mercado/linea.
- Las probabilidades de cada mercado completo deben sumar 1.
- `overround_method` debe registrar la estrategia usada.
- El consenso no debe mezclar lineas incompatibles.

## Predictions

Tabla actual: `predictions`.

Contrato:

- Cada prediccion debe guardar modelo/version o fuente suficiente para auditoria.
- `source_json` puede incluir detalles de Poisson, Logit, odds-aware y consenso.
- La prediccion odds-aware no es canonica hasta que una model card la active.

Campos de auditabilidad:

- `data_freshness_at`: timestamp de freshness mas reciente usado por la prediccion.
- `audit_snapshot_id`: referencia opcional a `pre_match_snapshots.id` cuando existe un snapshot previo.
- `audit_lineup_sources_json`: objeto JSON con fuentes de alineacion por lado, por ejemplo `home` y `away`.
- `audit_odds_source_json`: objeto JSON con fixture, version odds-aware, disponibilidad de consenso y freshness.
- `audit_degradation_reasons_json`: arreglo JSON de razones de degradacion; default `[]`.
- `not_evaluable_reason`: razon por la que una prediccion no debe entrar a evaluacion prepartido.

Reglas:

- Predicciones creadas en o despues del kickoff deben persistir `is_pre_kickoff = 0` y
  `not_evaluable_reason = 'post_kickoff_prediction'`.
- `audit_degradation_reasons_json` debe ser JSON valido incluso cuando no haya degradaciones.
- Los exports CSV/JSON/HTML deben seguir tolerando filas legacy con `source_json` malformado.
- Los campos auditables no cambian el modelo predictivo ni la seleccion canonica por si solos.

Migracion idempotente:

- Agregar columnas faltantes con `ALTER TABLE ... ADD COLUMN` usando defaults compatibles.
- No reescribir predicciones historicas salvo backfill minimo de `generated_at_utc` e
  `is_pre_kickoff` ya existente.
- En writes nuevos, derivar defaults auditables en el repositorio para conservar compatibilidad
  con callers legacy.

Contrato futuro de ensemble:

- `ensemble_version`: version del algoritmo de mezcla.
- `ensemble_weights_json`: pesos configurados y pesos efectivos por componente.
- `ensemble_components_json`: versiones de Poisson, Logit, odds-aware, devig y release activo.
- `ensemble_quality_json`: resumen de freshness, coverage, bookmaker_count, overround y reliability.

Reglas:

- Los pesos efectivos deben sumar `1.0` despues de degradacion y redistribucion.
- Las senales individuales no deben ocultarse: los componentes y sus pesos deben quedar auditables por
  prediccion.
- Hasta que exista migracion dedicada, estos campos pueden serializarse en `source_json` y
  `audit_odds_source_json`.
- La prediccion canonica no debe cambiar a ensemble sin model card, evaluacion temporal y rollback aprobado.

## Evaluations

Tablas actuales:

- `prediction_evaluations`
- `player_evidence_evaluations`
- `player_prediction_evaluations`

Contrato:

- Las evaluaciones deben apuntar a predicciones, partidos o releases concretos.
- Las metricas deben persistirse como JSON canonico cuando haya multiples dimensiones.
- No mezclar evaluaciones de modelos activos y shadow sin etiqueta.

## Model training y releases

Tablas actuales:

- `model_training_runs`
- `model_releases`

Contrato:

- `dataset_hash` identifica la ventana y datos usados.
- `status` debe reflejar ejecucion real, no solo intencion.
- Una release activa debe tener metadata, metricas y rollback documentados.

## Notifications

Tabla actual: `notification_deliveries`.

Contrato:

- La identidad operacional actual es partido, kickoff, ventana y canal.
- `payload_json` debe representar el payload enviado o intentado.
- Estados abiertos: `pending`, `waiting_prediction`, `retry`, `sending`.
- Estados cerrados: `sent`, `failed`, `expired`, `superseded`.

Detalle en `docs/NOTIFICATION_CONTRACTS.md`.

## Outputs

Contrato:

- Los archivos estables deben conservar nombre y formato.
- La escritura debe ser atomica.
- Los consumidores no deben depender de archivos temporales.
- En multi-torneo, los outputs namespaced deben convivir con aliases actuales.

## Cambios futuros requeridos

- Agregar `competition_id` y `season_id`.
- Separar `team` global de `participant`.
- Crear identidad de jugador robusta para squads de clubes.
- Persistir model cards o referencias a model cards junto a releases.
