# Cierre del review de Orbit: diseño y evidencia

Fecha: 15-nov-2026 (Vancouver). Base: `ff03ed61f7a847ab190b1695c2efe8d785baa364`.
Esta entrega diseña los arreglos; **no los implementa ni declara aprobada la app**.

- [Orden completo de implementación](../../../plans/ORDEN-IMPLEMENTACION.md).
- [ARREGLOS 05](../../../plans/arreglos-05.md), [diseño elegido](DISENO.md) y [boceto documental](diseno/boceto.py).
- [Cobertura por hallazgo](COBERTURA.md) y [aceptación reproducible](ACEPTACION.md).
- [Inventario de los planes anteriores](inventario/inventario.md), con [detalle estructurado](inventario/inventario.json).

La revisión vigente está en `revision/`: 31 casos, 17 fallos de comportamiento
correspondientes a 14 hallazgos y 14 controles verdes; sin errores de fixture ni
omisiones en la corrida final. Tres hallazgos originales ya están corregidos.
El caso S1 demuestra una fuga condicional con una clave ficticia, no un incidente
real. D1 es una nota de contrato; D2 ya está corregido.

La CI de la misma base pasó 5996 pruebas y secretos:
[Quality del commit auditado](https://github.com/gon0801/goncloud-Orbit/actions/runs/43781301318).
Los diez recorridos básicos de UI también pasaron. Estas comprobaciones no
detectaban los casos adversos que siguen abiertos. Los logs preservan los fallos
iniciales de entorno y sus correcciones; solo `acceptance.xml` es la corrida final.

`revision/` conserva los bytes de los artefactos originales y sus hashes. Sus rutas
`/private/tmp`, `/repo` y `/evidence` describen el entorno histórico, no dependencias
del plan. `ACEPTACION.md` explica cómo ejecutarlos desde un checkout nuevo y cómo
promover las regresiones al suite. Los candidatos y el inventario son fotos de
trabajo anteriores a esta síntesis: su reparto tentativo no sustituye a
`COBERTURA.md`, los planes actualizados ni `DISENO.md`.

En esta tarea de diseño sí se consultó producción **solo en lectura**:
`base/consulta-*.sql`, `base/*-prod.txt` y `base/huella-*.json`. A las
01:43 UTC del 16-nov había 31 propuestas shadow pendientes de veto, nueve goals
live y topes de 1,000,000; precios seguía en shadow efectivo. Reposición estaba
vacía y no había negatives con resultado `ok:reconciliado`. Eso no invalida las
reproducciones ni permite imponer otros invariantes sin medirlos.

Los 202 archivos de runtime coinciden entre esta base y el contenedor de producción
(huella conjunta `e13a6f0f34002079079fddd623952ad8f1d3b84c41c9d917efcf781e3a597967`).
Esto acredita presencia del código, no todas las migraciones, crons, actas o efectos
externos. No se activaron modos, enviaron mensajes ni ejecutaron mutaciones remotas.

`diseno/candidato-a/` y `diseno/candidato-b/` conservan las dos propuestas
independientes solicitadas por `pstack:architect` mediante `pstack:arena`.
Usaron agentes del modelo disponible: no se ejecutó un jurado de otros proveedores.
La elección y las diferencias resueltas constan en el diseño, sin atribuir a esa
comparación una independencia entre proveedores que no tuvo.
