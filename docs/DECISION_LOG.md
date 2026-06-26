# Decision Log

Este log registra decisiones arquitectonicas y operativas estables. Los handoffs contienen detalle por task; este archivo conserva el hilo transversal.

| ID | Fecha | Decision | Motivo | Estado |
| --- | --- | --- | --- | --- |
| D-001 | 2026-06-20 | Usar handoffs como continuidad primaria cuando MCP memory no esta disponible. | El servidor `agent-memory-server` puede fallar; repo, tests y docs son autoridad. | Activa |
| D-002 | 2026-06-20 | Mantener `src/quiniela/db.py` como facade temporal durante la migracion hexagonal. | Reduce blast radius y mantiene scripts existentes. | Activa |
| D-003 | 2026-06-20 | No mover CLIs ni scripts estables hasta que existan puertos/adapters equivalentes. | Evita romper automatizaciones y Task Scheduler. | Activa |
| D-004 | 2026-06-20 | Separar estado operacional de partidos y estado de releases de modelo. | Tienen ciclos de vida y rollback distintos. | Activa |
| D-005 | 2026-06-20 | Tratar cache API como freshness/resiliencia, no como fuente canonica. | Permite degradacion segura sin ocultar datos vencidos. | Activa |
| D-006 | 2026-06-20 | Mantener odds-aware como auxiliar hasta model card y gates de evaluacion. | Las cuotas pueden mejorar prediccion, pero requieren calibracion y control de sesgos. | Activa |
| D-007 | 2026-06-20 | Mundial 2026 sera competition/season default en la migracion multi-torneo. | Garantiza compatibilidad hacia atras. | Activa |
| D-008 | 2026-06-20 | Los outputs namespaced futuros deben conservar aliases estables actuales. | Protege consumidores existentes. | Activa |
| D-009 | 2026-06-20 | Ruff se configurara de forma incremental, sin formateo masivo. | El repo necesita adopcion gradual sin ruido de refactor. | Propuesta por TASK-012 |
