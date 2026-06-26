# Odds-Aware Market Model Design

## Estado actual

El sistema ya ingiere cuotas pre-partido en `src/quiniela/odds_loader.py` y las normaliza en dos niveles:

- `odds_market_snapshots`: cuotas por bookmaker, mercado, linea y seleccion, con cuota decimal e implied probability.
- `odds_market_consensus`: consenso por partido, mercado, linea y seleccion.

La conversion vigente hace un primer ajuste por bookmaker y mercado, dividiendo cada implied probability entre la suma del grupo, y luego calcula medianas por seleccion para construir el consenso. El consenso vuelve a normalizarse por mercado. El metodo queda registrado como `bookmaker_devig_then_market_renormalized`.

El predictor usa las cuotas como ajuste auxiliar:

- `Predictor.predict_match` conserva Poisson/Logit como fuente base.
- `odds_adjusted_score_prediction` genera una prediccion exacta odds-aware cuando hay consenso suficiente.
- `html_report.py` muestra consenso, prediccion odds-aware y diferencias modelo vs mercado.

Este diseno formaliza el siguiente paso sin convertir odds-aware en fuente canonica todavia.

## Objetivo

Definir un modelo `OddsAwareMarketModel` que combine distribuciones internas con informacion de mercado de forma auditable, calibrada y degradable. El modelo debe producir una distribucion de marcador y probabilidades agregadas, no solo un marcador puntual.

## Entidad de dominio propuesta

`OddsAwareMarketModel` representa una politica de ajuste de predicciones con consenso de mercado.

Entradas:

- `ScoreMatrix`: matriz base de probabilidades exact-score generada por Poisson, Logit u otro modelo interno.
- `MarketConsensus`: consenso normalizado por mercado.
- `OddsFreshness`: antiguedad y ventana temporal respecto al kickoff.
- `MarketCoverage`: mercados disponibles y completitud por seleccion.
- `BookmakerReliability`: confianza por bookmaker o fuente.
- `MatchMetadata`: equipos, kickoff, estadio, neutralidad y etapa cuando exista.

Salidas:

- `score_matrix`: matriz exact-score calibrada.
- `result_probabilities`: probabilidades 1-X-2 derivadas de la matriz.
- `totals_probabilities`: probabilidades over/under derivadas.
- `btts_probability`: probabilidad ambos anotan.
- `recommended_score`: marcador puntual derivado de la matriz.
- `confidence`: confianza operacional del modelo.
- `degradation_reasons`: lista de motivos si el modelo opera con menor peso o no se aplica.
- `model_version`: version semantica del algoritmo.
- `explanation`: resumen corto de mercados usados y restricciones principales.

## Value objects

### `MarketProbability`

Probabilidad de una seleccion dentro de un mercado ya normalizado.

Campos:

- `market_key`: identificador estable, por ejemplo `match_winner`, `btts`, `goals_over_under`.
- `line_key`: linea normalizada, por ejemplo `2.5` o `None`.
- `selection_key`: seleccion normalizada, por ejemplo `home`, `draw`, `away`, `yes`, `no`, `over`, `under`.
- `probability`: numero entre 0 y 1.
- `source_count`: numero de bookmakers o snapshots que contribuyen.
- `quality_flags`: flags no bloqueantes, como `stale`, `thin_market`, `line_mismatch`.

Invariantes:

- Las probabilidades de un mismo `(market_key, line_key)` deben sumar 1 cuando el mercado esta completo.
- No se persisten cuotas crudas fuera de snapshots; el consenso usa probabilidades normalizadas.

### `Overround`

Medida del margen implicito antes de normalizar.

Campos:

- `market_key`
- `line_key`
- `bookmaker_id`
- `raw_probability_sum`
- `margin`
- `method`

Invariantes:

- `raw_probability_sum >= 1` en mercados binarios o n-way validos, salvo datos defectuosos.
- Margenes extremos deben marcar el snapshot como sospechoso, no romper el pipeline.

### `OddsSnapshot`

Observacion inmutable de cuotas publicadas por una fuente.

Campos:

- `match_id`
- `bookmaker_id`
- `market_key`
- `line_key`
- `selection_key`
- `decimal_odd`
- `implied_probability`
- `source_update`
- `fetched_at`
- `source_json`

Invariantes:

- `decimal_odd > 1`.
- `implied_probability = 1 / decimal_odd`.
- `source_update` viene de la API si existe; `fetched_at` viene del sistema.

### `MarketConsensus`

Agregado usado por modelos y reportes.

Campos:

- `match_id`
- `market_key`
- `line_key`
- `probabilities`
- `bookmaker_count`
- `freshness_score`
- `coverage_score`
- `reliability_score`
- `consensus_method`
- `created_at`

Invariantes:

- El consenso no debe mezclar lineas incompatibles.
- Si falta una seleccion requerida, el mercado se considera incompleto y queda fuera del ajuste fuerte.

## Conversion de cuotas a probabilidades justas

### Baseline: conversion multiplicativa

Metodo inicial:

1. Convertir cada cuota decimal a implied probability.
2. Agrupar por bookmaker, mercado y linea.
3. Dividir cada probabilidad entre la suma del grupo.
4. Agregar por seleccion con mediana.
5. Renormalizar el mercado agregado.

Ventajas:

- Simple, determinista e idempotente.
- Explicable en reportes y model cards.
- Adecuado como baseline operativo.

Limitaciones:

- Distribuye el margen de forma proporcional.
- Puede sesgar favoritos cuando el margen no es uniforme.
- No modela comportamiento diferencial por bookmaker.

### Alternativa: power method

El power method ajusta las probabilidades con un exponente comun para que la suma normalizada cierre en 1. Es util cuando se quiere controlar mejor la compresion de favoritos y longshots.

Uso recomendado:

- Evaluacion offline contra el baseline multiplicativo.
- Activacion solo si mejora log-loss, Brier y calibracion por buckets.

### Alternativa: Shin

El metodo Shin modela margen asociado a informacion privilegiada o asimetrica. Puede ser util en mercados liquidos, pero requiere cuidado porque puede ser inestable con pocos bookmakers o mercados incompletos.

Uso recomendado:

- Experimento documentado, no default inicial.
- Exigir cobertura minima y monitoreo por competicion.

## Freshness

`OddsFreshness` mide que tan cerca esta el consenso de la hora de kickoff y de la ultima actualizacion del mercado.

Reglas iniciales propuestas:

- `fresh`: `source_update` menor o igual a 6 horas antes del kickoff.
- `usable`: `source_update` menor o igual a 24 horas antes del kickoff.
- `stale`: mas de 24 horas o sin `source_update` confiable.

Degradacion:

- Si todas las fuentes estan `stale`, odds-aware no debe reemplazar la prediccion base.
- Si hay mezcla de fuentes fresh y stale, el consenso puede ponderar por freshness.

## Coverage

`MarketCoverage` mide disponibilidad y completitud.

Mercados minimos:

- `match_winner`: requerido para ajuste 1-X-2 fuerte.
- `btts`: opcional, usado para redistribuir exact-score.
- `goals_over_under` con linea cercana a 2.5: opcional, usado para total de goles.
- `exact_score`: opcional, usado como regularizador, nunca como unica fuente.

Reglas iniciales:

- `full`: match winner, BTTS y O/U 2.5 disponibles.
- `partial`: match winner disponible y al menos un mercado secundario.
- `thin`: solo match winner o mercados incompletos.
- `missing`: sin mercados confiables.

## Bookmaker reliability

`BookmakerReliability` evita que fuentes ruidosas dominen el consenso.

Factores:

- Cobertura historica de mercados.
- Frecuencia de actualizacion.
- Margenes extremos o inconsistentes.
- Error historico contra cierre de mercado, cuando exista.
- Incidencias operativas registradas por API.

Regla inicial:

- Mientras no exista scoring historico, todos los bookmakers validados tienen peso 1.
- Bookmakers con datos incompletos o margenes fuera de umbral pueden quedar con peso reducido o excluidos del mercado afectado.

## Ensamble con modelos internos

El modelo odds-aware debe ser un ajuste sobre distribuciones internas, no una sustitucion opaca.

### Contrato `PredictionEnsemble`

`PredictionEnsemble` es el contrato final de combinacion entre modelos internos y mercado. No cambia la
prediccion canonica por si solo; define como una implementacion futura debe calcular, auditar y evaluar la
mezcla.

Entradas requeridas:

- `poisson_score_matrix`: matriz exact-score normalizada generada desde lambdas Poisson.
- `logit_result_probabilities`: probabilidades `home`, `draw`, `away` del modelo Logit, o `None` si el
  artefacto no esta disponible.
- `odds_aware_score_matrix`: matriz exact-score ajustada por mercado, o `None` si no hay consenso usable.
- `market_quality`: freshness, coverage, bookmaker_count, overround y reliability por mercado usado.
- `prediction_context`: ventana operativa (`manual`, `t-60`, `t-30`, `t-15`, `t-5`, `t-1`, `hourly`).
- `model_release`: release activo de modelos internos cuando exista.

Salidas requeridas:

- `score_matrix`: matriz final normalizada.
- `result_probabilities`: probabilidades `home`, `draw`, `away` derivadas de `score_matrix`.
- `recommended_score`: marcador exacto con mayor probabilidad.
- `component_weights`: pesos efectivos por componente.
- `configured_weights`: pesos base antes de degradacion.
- `degradation_reasons`: razones por las que un componente tuvo peso reducido o cero.
- `ensemble_version`: version semantica del contrato/algoritmo.
- `component_versions`: versiones de Poisson, Logit, odds-aware, devig y release activo.

### Pesos versionables

Los pesos iniciales deben vivir en configuracion versionada o model card, no hardcodeados en scripts. El
primer perfil recomendado es `prediction_ensemble_v1`.

Peso inicial sugerido:

- Poisson/base score matrix: 45%.
- Logit/result model: 20%.
- Market consensus: 35%.

Peso cerca del kickoff con mercados frescos y completos:

- Poisson/base score matrix: 35%.
- Logit/result model: 15%.
- Market consensus: 50%.

El peso de mercado debe reducirse automaticamente cuando haya poca cobertura, cuotas stale, pocos bookmakers o mercados con overround extremo.

Reglas de degradacion iniciales:

- Si `odds_aware_score_matrix` no existe, el peso de mercado efectivo es `0` y se redistribuye entre Poisson
  y Logit segun sus pesos relativos.
- Si Logit no esta entrenado o esta marcado como preliminar sin gate aprobado, su peso puede reducirse a `0`
  y Poisson absorbe el peso interno.
- Si `market_quality.coverage` es `thin`, el peso de mercado no debe superar `20%`.
- Si `market_quality.freshness` es `stale`, el peso de mercado no debe superar `10%`.
- Si hay overround extremo, markets incompletos o menos de dos bookmakers confiables, registrar la razon y
  tratar mercado como referencia debil.

### Auditoria por prediccion

Cada prediccion que use el ensemble debe guardar suficientes datos para reconstruir la mezcla sin depender de
memoria conversacional ni logs volatiles.

Campos recomendados:

- `ensemble_version`.
- `ensemble_weights_json`: pesos configurados, pesos efectivos y perfil usado.
- `ensemble_components_json`: versiones de Poisson, Logit, odds-aware, devig y release activo.
- `ensemble_quality_json`: freshness, coverage, bookmaker_count, overround y reliability agregados.
- `audit_degradation_reasons_json`: debe incluir razones del ensemble ademas de razones de datos existentes.

Mientras no existan columnas dedicadas, estos campos pueden vivir dentro de `source_json` y
`audit_odds_source_json`; una migracion futura puede promoverlos a columnas explicitas.

### Evaluacion contra componentes individuales

El ensemble solo puede volverse canonico despues de evaluarse contra los componentes individuales en la misma
ventana temporal y con el mismo conjunto de partidos elegibles.

Comparaciones minimas:

- Poisson puro.
- Poisson + Logit hibrido actual.
- Odds-aware como referencia shadow.
- `PredictionEnsemble` con perfil `prediction_ensemble_v1`.

Metricas minimas:

- Log-loss `1-X-2`.
- Brier score `1-X-2`.
- Calibration error por buckets.
- MAE de goles esperados.
- Exact-score hit rate como metrica secundaria.
- Tasa de abstencion o degradacion por falta de mercado fresco.

Rollback:

- Mantener Poisson + Logit como canonico hasta gate aprobado.
- Si el ensemble empeora log-loss o calibracion en segmentos criticos, desactivar el perfil y conservar solo
  auditoria shadow.
- Nunca borrar outputs ni snapshots generados por el perfil desactivado; registrar motivo en model card.

## Evaluacion y model card

Cada version odds-aware debe tener model card con:

- Ventana de datos evaluada.
- Mercados usados.
- Metodo de devig.
- Pesos de ensamble.
- Metricas contra baseline sin cuotas.
- Segmentos por competicion, etapa, favorito/no favorito y localia.
- Riesgos conocidos.
- Criterio de rollback.

Metricas minimas:

- Log-loss para 1-X-2.
- Brier score para 1-X-2, BTTS y O/U.
- Calibration error por buckets.
- Exact-score hit rate, solo como metrica secundaria.
- MAE de goles esperados si se derivan lambdas.

Gates de promocion:

- No degradar log-loss global contra baseline.
- Mejorar o empatar Brier en mercados principales.
- No empeorar calibracion en favoritos fuertes.
- Mantener tasa de abstencion/degradacion documentada.

## Persistencia futura

La persistencia actual es suficiente para snapshots y consenso. Para entrenar/evaluar versiones futuras se recomienda agregar, en una task posterior:

- `odds_model_runs`: parametros, version, ventanas y dataset hash.
- `odds_model_evaluations`: metricas por segmento.
- `bookmaker_reliability_scores`: pesos efectivos y razones.
- `market_quality_events`: incidencias de cobertura, freshness y overround.

No se propone migracion de schema en esta task.

## Rollback

Rollback operativo:

- Desactivar el uso de odds-aware en `Predictor` mediante flag futuro.
- Mantener consenso visible en reportes como informacion, sin ajustar marcador.
- Volver al modelo Poisson/Logit base.

Rollback de datos:

- No borrar snapshots ni consenso.
- Registrar la version desactivada y motivo en model card o decision log.

## Riesgos

- Las cuotas pueden reflejar informacion de lesiones o alineaciones antes que el modelo, pero tambien pueden amplificar sesgos de mercado.
- Mercados con poca liquidez pueden dar falsa precision.
- Exact-score es ruidoso y no debe guiar el ajuste completo sin mercados agregados.
- Diferentes competiciones pueden requerir calibraciones distintas.
