# 3.1 — Reporte del piloto de Jev (2026-10-04)

Piloto manual con fichas aprobadas por David y búsquedas reales de su cuenta.
Código desplegado: `25cebe7`. Modelo: `jev-1.13.0`. Cada lote lo autorizó y lo
corrió David (`tools/jev_ads.py ... --aplicar`); ningún término se envió sin
ficha aprobada.

Dataset: `dataset-piloto.csv` (648 filas; sha256
`210e5ac9cecde696a17a7290fe136156146a9dfc1e1f2894e6e95a2442712ecb`). Una fila
por par búsqueda-producto: etiqueta humana, respuesta de Jev, confianza,
duración y tokens. No lleva nombres ni SKUs del catálogo.

## Resultados por lote

| Lote | Mercado | Uso | Qué se probó | Pares | Aciertos | Abstenciones | Desacuerdos | Fallos |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | US | negativos | 3 decisiones `negative` reales (2 búsquedas, 22 y 91 productos) | 135 | 135 | 0 | 0 | 0 |
| 2 | US | semillas de grupo | 12 búsquedas sin venta, 16 productos | 192 | 150 | 17 | 25 | 0 |
| 3 | US | semillas de grupo | Las 3 que fallaron, con fichas completas | 48 | 28 | 4 | 15 | 1 |
| 4 | US | semillas de grupo | Regla del dueño en la ficha (monedas sin boda) | 64 | 64 | 0 | 0 | 0 |
| 5 | MX | semillas de grupo | 14 búsquedas reales, 11 productos | 154 | 116 | 6 | 30 | 2 |
| 6 | MX | semillas de grupo | Las 5 que fallaron, con 4 reglas del dueño | 55 | 55 | 0 | 0 | 0 |
| Total | | | | 648 | 548 | 27 | 70 | 3 |

"Abstención" es `informacion_insuficiente`. "Fallo" es una respuesta inválida
del proveedor (probabilidades que no suman 1): quedó como `fallo_proveedor`,
nunca como evidencia.

## Latencia y costo

- Latencia mediana por llamada: entre 253 y 270 ms según el lote.
- `usage` real: 643,945 tokens de entrada y 33,930 de salida (unos 994 y 52
  por par).
- Costo en USD: **desconocido**. La tarifa de TypeSafe no está en el repo; se
  calcula con ese `usage`.

## Qué mostró

1. Con la ficha completa, Jev acierta: "sí" a las búsquedas del producto,
   "no" a las ajenas, y separa por atributo (color) dentro de un grupo.
2. En la primera pasada acertó cerca del 75% en los dos mercados (lotes 2 y
   5). Los desacuerdos tuvieron dos causas: datos que la ficha no traía
   (material, que el set no incluye lazo) e intención del comprador que no es
   literal ("silver coins", "cofre para arras", marca de otro).
3. Cada causa se corrigió escribiendo el dato o la regla en la ficha, sin
   cambiar el sistema: los lotes 4 y 6 repitieron las búsquedas que fallaban y
   salieron 64 de 64 y 55 de 55.
4. Un "no" puede ser el consejo útil: "arras de boda personalizadas" no
   corresponde a sets que no se personalizan; la búsqueda pertenece a otra
   campaña.
5. En un grupo de Amazon el resultado de grupo nunca es "ninguno
   corresponde": el censo no es exhaustivo (`universo_desconocido`). El
   detalle por par sí queda guardado.

## Límites declarados (desviaciones del DoD de 3.1)

- **Usos.** Se midieron negativos (decisiones reales) y búsquedas contra
  grupos. No se midió un destino de harvest (el de MX tiene 208 productos) ni
  un plan de fábrica. Pasan al plan `jev-ads-02`.
- **Orden Choice.** No se midió la sensibilidad al orden de opciones.
- **Etiquetas.** En el lote 1 las puso el dueño para todo el lote. En los
  lotes 2 a 6 las propuso el lead y el dueño las confirmó o corrigió por
  búsqueda; no fue etiquetado a ciegas par por par.
- **Una etiqueta se corrigió después.** En el lote 5, "arras de boda
  personalizadas" se etiquetó "sí"; el dueño aclaró que solo los sets personalizados se
  personalizan, y en el lote 6 la etiqueta correcta fue "no". El dataset
  conserva la etiqueta usada en cada lote.
- **Muestra.** Un solo tipo de producto (sets de arras), 102 productos (91 en US y 11 en MX) y 31
  búsquedas distintas.
