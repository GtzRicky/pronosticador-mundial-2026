# Handoff - TASK-XXX

**Task:** TASK-XXX - <titulo>
**Fecha:** YYYY-MM-DD
**Agente/modelo usado:** <modelo>
**Branch:** <branch>
**Tipo:** docs | audit | architecture | refactor | feature | test | migration | ops
**Estado:** done | partial | blocked

## 0. Reglas de continuidad

- Nombre del archivo: `docs/handoffs/TASK-XXX.md`.
- Usa sufijo solo si el backlog ya lo pide, por ejemplo `TASK-000-PLAN.md`.
- El handoff debe escribirse al finalizar una task, al quedar parcial o al bloquearse.
- La memoria externa, incluido `agent-memory-server`, es advisory. El siguiente agente debe poder continuar solo con repo, handoffs, tests y comandos.
- No dependas de memoria conversacional para recuperar decisiones, comandos o riesgos.
- No pegues logs enormes: resume resultados y conserva comandos exactos.

## 1. Objetivo de la task

Describe en 2-5 lineas que debia lograr esta task y que parte del backlog cubre.

Incluye explicitamente:

- alcance incluido;
- fuera de alcance importante;
- dependencia principal satisfecha.

## 2. Contexto leido

Archivos inspeccionados:

- ...

Documentos consultados:

- ...

Comandos de inspeccion usados:

```powershell
...
```

Notas:

- Para tasks docs/architecture, lista documentos fuente y modulos inspeccionados.
- Para refactor/feature/test/migration/ops, lista tambien tests y comandos que protegen el comportamiento.

## 3. Cambios realizados

- ...

Indica si los cambios fueron:

- documentales solamente;
- codigo sin cambio de comportamiento;
- codigo con cambio funcional;
- tests;
- migracion/schema;
- operacion/scripts.

## 4. Archivos modificados

- ...

Si hubo archivos generados o outputs, indica si son esperados y si deben commitearse.

## 5. Decisiones tomadas

- Decision:
  - Razon:
  - Alternativas consideradas:
  - Impacto:
  - Requiere ADR o revision: si | no

Regla:

- Las decisiones arquitectonicas nuevas deben quedar aqui y, si afectan contratos o comportamiento publico, deben recomendar ADR o revision.

## 6. Validaciones ejecutadas

```powershell
...
```

Resultado:

```text
...
```

Incluye:

- comandos exactos;
- resultado resumido;
- fallos y si fueron resueltos;
- validaciones no ejecutadas y razon.

## 7. Riesgos o dudas pendientes

- ...

Clasifica cuando aplique:

- Riesgo tecnico:
- Riesgo operativo:
- Riesgo de datos:
- Riesgo de seguridad:
- Duda de producto/arquitectura:

## 8. Errores encontrados y resolucion

- Error:
- Causa:
- Solucion:
- Pendiente:

Si no hubo errores:

- No hubo errores nuevos durante la task.

## 9. Proximo paso recomendado

- TASK-...

Explica en una linea por que es el siguiente paso natural.

## 10. Instrucciones de recuperacion

Si la siguiente sesion pierde contexto, debe:

1. Leer este handoff.
2. Leer el backlog o plan vigente si existe en el entorno local.
3. Leer los documentos citados en "Contexto leido".
4. Ejecutar los comandos de validacion listados o sus equivalentes seguros.
5. Revisar `git status --short`.
6. Continuar desde el proximo paso recomendado o desde la task indicada por el usuario.

## 11. Seguridad y secretos

- No registrar API keys, `.env`, topicos ntfy, webhooks Discord ni tokens.
- No pegar dumps completos de base de datos.
- No incluir payloads externos largos si contienen datos sensibles o sujetos a licencia.
- No registrar URLs privadas de webhooks ni topicos de notificacion.
- Si se inspeccionan bundles o outputs publicables, mencionar la politica de licencia/datos sin copiar datos sensibles.

## 12. Estado final por tipo de task

Completa solo los puntos que apliquen:

- Docs/architecture: documentos creados o actualizados, secciones principales y rollback documental.
- Refactor: comportamiento protegido, tests antes/despues y facade legacy conservada.
- Feature: flujo usuario afectado, tests y compatibilidad.
- Test: cobertura agregada, fallos esperados y alcance de fixtures.
- Migration: DB temporal usada, idempotencia, backup requerido si aplica.
- Ops: comandos seguros/offline vs live, efectos externos y rollback operativo.
