# Cotejo documental PR 523 — 2026-11-04

Se revisaron los comentarios documentales contra el codigo, sin cambios de comportamiento.

| Hallazgo | Adjudicacion y comprobacion |
|---|---|
| Sexto argumento de cascada en candidato A | Valido como ambiguedad documental: `app/optimizer/goals.py:232` admite cinco argumentos. Se conserva el boceto historico y se declara extension propuesta; la sintesis propone `cascada_target_acos_con_procedencia` con `target_margen_producto`. La variante vigente en `goals.py:341` aun no lo admite. La migracion debe cubrir procedencia y negativos. |
| Firma de reposicion abreviada | Valido: `app/retiro_anuncios.py:423` exige conexion, `retiro_ids` y `go`. Se aclaran retiros sellados propios y exclusion por `retiro-ad:<id>`. El codigo vigente usa esa llave y documenta que una intencion pendiente no tiene retiro sellado. |
| Reposicion de ARCHIVED | Se precisa el limite del diseno: `_repone_uno` hoy recrea. `requiere_recreacion` es una guarda propuesta para AUTO-03 antes del camino de recreacion, no una respuesta implementada. Requiere autorizacion distinta para crear. |
| Fuentes sin codigo inline | Valido: rutas y simbolos de la revision de alertas se delimitan con backticks. |
| Bloques de cita separados | Valido: se agrega `>` a la linea vacia entre la nota de estado y el bloque siguiente de ORBIT 05 y FABRICA 01. |

Evidencia reutilizada: diseno sintetizado `docs/superpowers/specs/2026-10-24-decisiones-producto-design.md`, secciones de uso y reversa. El candidato A sigue siendo una alternativa historica, no se presenta como decision adoptada.

Verificacion local: enlace relativo nuevo resuelto a archivo existente; `git diff --check` sin errores; `pre-commit run --files` sobre los cuatro documentos pasa gitleaks y conflictos. No se agregaron pruebas de texto ni se ejecuto la suite para estos cambios documentales. No se hicieron commits, pushes ni escrituras de produccion.
