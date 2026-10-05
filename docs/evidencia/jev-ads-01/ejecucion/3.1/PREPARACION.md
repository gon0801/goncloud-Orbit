# 3.1 — Preparación del piloto (lo que falta es de David)

> El piloto ya corrió el 2026-10-04: ver `REPORTE.md` y `../3.2/ACTA.md`.
> Esta hoja queda como la preparación con la que se planeó.

El código está desplegado y apagado (2.3). Ninguna tarea automática llama a
Jev. El piloto 3.1 lo corre una persona, con fichas aprobadas, casos
etiquetados y un presupuesto fijado. Este archivo deja todo listo para eso;
no envía nada a TypeSafe.

## 1. Qué decide David

1. **El alcance del primer lote.** Los universos de Amazon son grandes: cada
   par término × producto anunciado es una llamada.
2. **Qué productos llevan ficha.** Una ficha es la descripción aprobada de un
   producto (hechos con su fuente y lo que no se sabe). Sin ficha, el
   producto queda como "ficha faltante" y no se consulta.
3. **El presupuesto en llamadas por lote** (`--presupuesto`).
4. **La etiqueta humana de cada caso**, para medir aciertos y desacuerdos.

## 2. Denominadores reales (prod, solo lectura, 2026-10-04)

Decisiones negative y harvest de los últimos 30 días con datos maduros
(`window_end` de al menos 10 días):

| Mercado | Uso | Decisiones | Términos | Productos en el grupo de origen |
| --- | --- | ---: | ---: | ---: |
| MX | harvest | 5 | 5 | 208 |
| US | harvest | 1 | 1 | 16 |
| US | negativo | 4 | 2 | 91 |

Lectura para el presupuesto:

- **Revisar los 5 harvest de MX** cuesta hasta 5 × 208 = 1,040 pares,
  más sus destinos, y pide hasta 208 fichas aprobadas.
- **Los 4 negativos de US** son 2 términos × 91 productos = 182 pares, con 91
  fichas. Es el lote más chico que cubre un uso completo.
- **Un plan de fábrica** (`evaluar-plan`) tiene un universo exhaustivo y
  pequeño: los productos del plan. Es el lote más barato para empezar.

La consulta que lista los productos de cada decisión candidata (para saber
qué fichas aprobar) está en `fichas-candidatas.sql`. Se corre como
`orbit_read`:

    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X' < docs/evidencia/jev-ads-01/ejecucion/3.1/fichas-candidatas.sql

## 3. Costo estimado

En el prototipo con TypeSafe real (`docs/evidencia/jev-ads-01/prototipo/`),
cada par usó unos 534 tokens de entrada y 52 de salida. El precio en USD por
token de TypeSafe no está en el repo: el costo en USD queda **desconocido**
hasta el primer lote. El reporte de 3.1 lo calcula con el `usage` real
guardado en `jev_par_evento.usage`.

## 4. Comandos (dentro de `orbit-app-1`, en seco primero)

Registrar una ficha (en seco por omisión; `--aplicar` escribe):

    docker exec -it orbit-app-1 python -m tools.jev_fichas registrar \
        --producto-id <id> --plataforma amazon_us --listings <ids> \
        --hechos /ruta/hechos.json --aprobador david \
        --observado-at <fecha ISO> --revisar-antes-de <fecha ISO>

Revisar una decisión: primero en seco, para ver cuántos pares pagaría;
después con `--aplicar`. La misma `--solicitud` retoma sin pagar dos veces.

    docker exec -it orbit-app-1 python -m tools.jev_ads evaluar \
        --decision-id <id> --solicitud <uuid> --presupuesto <n>
    docker exec -it orbit-app-1 python -m tools.jev_ads evaluar \
        --decision-id <id> --solicitud <uuid> --presupuesto <n> --aplicar

Revisar un plan de fábrica (la misma solicitud del preview):

    docker exec -it orbit-app-1 python -m tools.jev_ads evaluar-plan \
        --plan /ruta/solicitud.json --solicitud <uuid> --presupuesto <n>

Para `--aplicar` hace falta la clave en `<ORBIT_SECRETS_DIR>/typesafe.json`.
Sin ella el lote corre apagado: estados visibles y cero llamadas.

Salidas: 0 completo o seco; 1 error; 2 configuración; 3 presupuesto agotado
(se retoma con la misma `--solicitud`).

## 5. Plantilla de etiquetado

`plantilla-etiquetado.csv`: una fila por par revisado. La persona llena
`etiqueta_humana` sin ver la respuesta de Jev; el reporte compara después.

| Columna | Contenido |
| --- | --- |
| `mercado` | `amazon_mx` o `amazon_us` |
| `uso` | `negativo`, `harvest` o `semilla` |
| `sujeto` | `decision:<id>` o `plan:<huella>` |
| `termino` | el término literal |
| `producto_id` | el producto del censo |
| `etiqueta_humana` | `satisface`, `no_satisface` o `informacion_insuficiente` |
| `nota` | opcional |
