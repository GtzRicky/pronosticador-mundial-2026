# Model Card Template

Use este template para cualquier modelo que afecte predicciones, ranking de riesgo o decisiones operativas. La model card debe versionarse junto con el cambio que activa o evalua el modelo.

## Identificacion

- Nombre del modelo:
- Version:
- Fecha:
- Responsable:
- Estado: experimental | shadow | activo | retirado
- Codigo o modulo principal:
- Handoff relacionado:

## Proposito

- Caso de uso:
- Usuarios o consumidores:
- Decisiones que puede informar:
- Decisiones que no debe tomar:

## Datos

- Fuentes usadas:
- Ventana temporal:
- Competicion o competiciones:
- Season o seasons:
- Dataset hash:
- Politica de dataset hash:
- Criterios de inclusion:
- Criterios de exclusion:
- Datos sensibles o restringidos:

### Politica de dataset hash

El `dataset_hash` debe ser determinista y recalculable desde entradas canonicas
ordenadas de forma estable. Para releases de evidencia de jugadores, el hash
actual se calcula desde `match_id`, `kickoff_at`, `source_kind`, `home_goals` y
`away_goals` de los partidos elegibles. Si se agregan columnas de competencia,
features nuevas o targets nuevos, la model card debe declarar si el hash cambia
y por que.

Reglas:

- Ordenar filas por tiempo y llave estable antes de hashear.
- Usar JSON canonico con keys ordenadas.
- No incluir paths locales, timestamps de ejecucion ni secretos.
- Guardar el hash en `model_training_runs.dataset_hash`,
  `model_releases.dataset_hash` y metadata del release.
- Si un dataset ya fue procesado, no reentrenar silenciosamente con el mismo
  hash.

## Features e inputs

- Inputs obligatorios:
- Inputs opcionales:
- Value objects relevantes:
- Defaults:
- Comportamiento ante datos faltantes:
- Feature schema hash:
- Politica de cambios de features:

### Feature schema hash

El `feature_schema_hash` identifica la lista ordenada de features, tipos
esperados, defaults y transformaciones. Debe cambiar cuando se agregue, retire,
renombre o cambie la semantica de una feature. Mientras no exista columna
dedicada, se registra en `metadata.json`, reportes y model card.

Reglas:

- Separar features colectivas, odds, lineup/player impact e individuales.
- Declarar defaults para datos faltantes (`0.0`, `None`, fallback Poisson,
  etc.).
- Declarar si la feature es global, especifica de competencia o especifica de
  participante.
- No comparar releases con schemas incompatibles sin una nota de migracion.

## Outputs

- Outputs principales:
- Outputs secundarios:
- Formato de persistencia:
- Formato de exposicion en reportes o API:
- Campos de explicabilidad:

## Contratos afectados

- Tablas leidas:
- Tablas escritas:
- Archivos de salida:
- Campos nuevos o modificados:
- Compatibilidad hacia atras:
- Contrato de notificaciones afectado:

## Metodologia

- Tipo de modelo:
- Baseline comparado:
- Parametros clave:
- Estrategia de calibracion:
- Estrategia de ensamble:
- Semilla o reproducibilidad:

### Niveles de aprendizaje

**Nivel 1 - Baseline controlado**

- Poisson v1, Logit `1-X-2` y reglas deterministas sin activacion automatica
  de candidatos nuevos.
- Uso recomendado: bootstrap, smoke tests, fallback y rollback.
- Requisito de activacion: suite offline verde y model card basica.

**Nivel 2 - Champion/challenger**

- Un champion activo sirve predicciones canonicas; challengers se entrenan y
  evaluan con splits temporales.
- Uso actual: `player_evidence.py` registra training runs, releases,
  candidate/rejected/active/archived y rollback a release anterior.
- Requisito de activacion: no regresion en metricas primarias, mejora minima
  documentada y artefactos reproducibles.

**Nivel 3 - Aprendizaje odds-aware y multi-torneo**

- Pesos de ensemble, calibracion por competencia y comparacion contra mercado.
- Uso esperado: shadow primero; canonico solo despues de gates por competencia.
- Requisito de activacion: evaluacion por season/competition, model card
  completa, monitoreo de drift y rollback operativo probado.

### Replay determinista

Cada release debe poder reconstruirse desde:

- DB o bundle con datos fuente.
- `dataset_hash`.
- `feature_schema_hash`.
- lista de partidos/snapshots elegibles;
- version de codigo o handoff asociado;
- parametros, thresholds y semilla cuando aplique.

El replay no debe depender de hora local salvo que el cutoff este fijado en la
metadata. Los comandos live/API no son parte del replay obligatorio.

## Evaluacion

- Split de entrenamiento/evaluacion:
- Metricas primarias:
- Metricas secundarias:
- Segmentos evaluados:
- Resultado contra baseline:
- Incertidumbre o intervalos:

### Evaluacion contra odds y mercado

Cuando existan odds suficientes, evaluar tambien contra el mercado:

- Log-loss `1-X-2` del modelo vs consenso `match_winner`.
- Brier `1-X-2` del modelo vs resultado real y contra baseline de mercado.
- Calibration error por buckets de favorito/no favorito.
- Segmentos por coverage (`full`, `partial`, `thin`, `missing`) y freshness
  (`fresh`, `usable`, `stale`).
- Diferencia modelo vs mercado y razon de degradacion si odds-aware queda en
  shadow.

Si no hay cobertura de mercado suficiente, la model card debe decirlo
explicitamente y no presentar metricas de mercado como gate obligatorio.

## Gates de activacion

- Gate 1:
- Gate 2:
- Gate 3:
- Umbral de abstencion o degradacion:
- Aprobador:

### Gates minimos por competencia

Los gates se evaluan por `competition_id` y `season_id` cuando esas columnas
existan. Mundial 2026 es el default hasta que una migracion posterior active
otra competencia.

Gates actuales para player evidence:

- No empeorar `goal_mae`.
- No empeorar `goal_poisson_deviance`.
- No empeorar `log_loss`.
- No empeorar `brier_score`.
- No empeorar `calibration_error`.
- Mejorar al menos 2% `goal_poisson_deviance` o `log_loss`.
- Si hay 10 o mas partidos live, repetir gate en el segmento `live`; si no se
  puede validar, no promover automaticamente.

Gates futuros para odds-aware/ensemble:

- No degradar log-loss global contra Poisson + Logit canonico.
- No empeorar calibracion en favoritos fuertes.
- No promover si coverage/freshness de mercado no alcanza el minimo de la
  competencia.
- Mantener tasa de abstencion/degradacion dentro del rango documentado.

## Aprobacion operativa

- Handoff revisado:
- Decision log actualizado:
- Rollback probado:
- Validaciones ejecutadas:
- Fecha maxima de re-evaluacion:

## Limitaciones

- Sesgos conocidos:
- Casos donde no aplica:
- Dependencias externas:
- Riesgos de drift:
- Riesgos operativos:

## Monitoreo

- Metricas online:
- Alertas:
- Frecuencia de revision:
- Tablas o archivos revisados:
- Owner operativo:

## Rollback

- Trigger de rollback:
- Comando o cambio de configuracion:
- Datos a conservar:
- Comunicacion requerida:
- Validacion post-rollback:

### Rollback champion/challenger

- Conservar el release anterior en `model_releases` y artefactos de
  `data/processed/model_artifacts/releases/`.
- Si falla la escritura de `active.json`, restaurar el release activo anterior.
- Un release rechazado no debe borrar `metadata.json`, `report.md` ni
  diagnosticos; quedan para auditoria.
- Post-rollback, ejecutar tests de evidencia y una prediccion smoke con el
  champion activo.

## Historial

| Fecha | Version | Cambio | Resultado | Referencia |
| --- | --- | --- | --- | --- |
| | | | | |
