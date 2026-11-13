# Hallazgos de la revisión del 2026-11-12 y a qué plan va cada uno

Código revisado: master `a4644aad`, el mismo que corría en producción ese día (desplegado `eb3906b9`, sin diferencias en `app/`).
Reportes de origen, uno por área, en esta carpeta: `politica.md`, `ciclo.md`, `apply.md`, `ingesta.md`, `precio.md`, `fabrica.md`, `estimacion.md` y `web.md`. Cada falla trae su impacto, dónde está, su prueba y qué debería pasar.
Pruebas de reproducción: `pruebas/<área>/`. Se corren desde la raíz del repo con `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/`. En `a4644aad` fallan 64 y pasa 1 (un control). No viven en `tests/` porque fallan a propósito. Cada bloque de dinero, de datos o de una falla ya ocurrida promueve la suya; las demás se quedan aquí como registro y el bloque se comprueba corriendo lo real.

La revisión reportó 54 fallas distintas: 45 con prueba que falla y 9 comprobadas con una lectura de producción. La falla 5 de `ciclo.md` es la misma que la 1 de `politica.md`. Al diseñar los arreglos, una resultó no ser falla (la 3 de `politica.md`; su fila dice por qué): quedan 53.

Planes:
- **ARREGLOS 02** (`plans/arreglos-02.md`): lo que mueve dinero hoy. Va antes de TEMPORADA 01.
- **ARREGLOS 03** (`plans/arreglos-03.md`): datos y precios. En paralelo con TEMPORADA 01.
- **ARREGLOS 04** (`plans/arreglos-04.md`): pantallas, fábrica y bloqueos. En paralelo con TEMPORADA 01.
- **ARREGLOS 05** (por escribir): ciclo, escritura a Amazon, Ajustes, el tope de pujas y el borrado de modos que producción no usa. Se escribe cuando TEMPORADA 01 termine, porque ese plan reescribe `app/cycle.py`, `app/apply.py`, `app/apply_cola.py` y Ajustes. Sus hallazgos y pruebas ya están aquí.

## Política de pujas (`politica.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | El piso aprendido se compara con la puja sin redondear y Amazon recibe justo el piso. Pasó en las keywords 4934, 5336, 4924 y 5895 de Estados Unidos. | ARREGLOS 02, B |
| 2 | `docs/CONTEXTO.md` pide 10 días para invertir una puja y el código pide 20 clics o 14 días. | ARREGLOS 02, A (decisión del dueño: manda la del código) |
| 3 | «Heredar del ad group» recorta con el ACoS de la propia keyword cuando el grupo es casi solo ella. | No es falla. Con el margen corregido por ARREGLOS 02 D, las tres keywords que cambiarían (decisiones 2865, 2867 y 2902) tienen ACoS sobre el equilibrio: pierden dinero y el recorte es correcto. Queda como pregunta de regla para el dueño (diseño de ARREGLOS 02, sección C1). |
| 4 | Un ajuste de campaña cuenta como subida. Latente: hoy no hay ajustes. | ARREGLOS 02, C |

## Ciclo del optimizador (`ciclo.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | Un error de Amazon en una sola puja frena las demás y la liberación de cortes. | ARREGLOS 05 |
| 2 | Si la fase de escritura falla una vez, el ciclo no se reintenta ese día. | ARREGLOS 05 |
| 3 | Si el proceso muere a mitad del ciclo, el siguiente horario también se pierde. | ARREGLOS 05 |
| 4 | El ciclo decide antes de conciliar con Amazon. | ARREGLOS 05 |
| 5 | La misma que la 1 de `politica.md`. | ARREGLOS 02, B |

## Escritura a Amazon y cortes (`apply.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | Orbit pisa la puja que el dueño cambió a mano, y la reversa regresa a un valor que nunca existió. | TEMPORADA 01, E (lee la puja antes de escribirla). La reversa se revisa en ARREGLOS 05. |
| 2 | Hay escrituras a Amazon que no pasan por el cliente protegido. | ARREGLOS 05 |
| 3 | Archivar anuncios por terminal no deja registro y reponer dos veces duplica. | ARREGLOS 04, B-A |
| 4 | Un bloqueo sobre un negative pausado se da por aplicado. | ARREGLOS 04, B-B y B-D |
| 5 | Una práctica de veto vencida congela la keyword para siempre. | ARREGLOS 05 |
| 6 | No hay freno global: los topes están en 1,000,000. | ARREGLOS 05 (decisión del dueño: tope real de unas 150 pujas por ciclo, sin contar la entrada ni el regreso de temporada) |
| 7 | Un negative confirmado por la conciliación no se puede revertir desde Orbit. | ARREGLOS 05 |
| 8 | «Quitar» un bloqueo con un error de Amazon lo deja «vivo» para siempre. | ARREGLOS 04, B-B |
| 9 | El bloqueo no lee el rechazo por ítem de Amazon. | ARREGLOS 04, B-B |
| 10 | Una lectura incompleta de Amazon se toma como «no existe». | ARREGLOS 04, B-C |
| 11 | `archiva_inertes --reponer` crea keywords sin intención guardada. | ARREGLOS 05 (la herramienta no se borra) |
| 12 | El valor decidido no es el que recibe Amazon; y la sonda sella «ok» un rechazo por ítem. | ARREGLOS 02, B (primera parte) y ARREGLOS 04, B-B (segunda) |

## Ingesta y dinero base (`ingesta.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | El «ayer» de Amazon Estados Unidos se guarda incompleto en invierno. | ARREGLOS 03, D-E |
| 2 | Mercado Libre Ads guarda el día en curso como dato. | ARREGLOS 03, D-F |
| 3 | Un search term partido en filas puede quedar con 0 pedidos. | ARREGLOS 03, D-G |
| 4 | Las ventas de Estados Unidos pierden su importe en dólares. | ARREGLOS 03, D-I |
| 5 | Las devoluciones de Mercado Libre llegan sin etiqueta y dejan vacío su margen. | ARREGLOS 03, D-H |

## Precios (`precio.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | Un cambio con error de red se salta los 7 días y queda como «error» para siempre. | ARREGLOS 03, P-C |
| 2 | Un error de base en una publicación tumba la corrida de toda la plataforma, sin aviso. | ARREGLOS 03, P-B |
| 3 | El fusible no ve la ola del día en que precios regresa a en vivo. | ARREGLOS 03, P-D |
| 4 | Pueden moverse más de 50 precios en un día sin confirmación. | ARREGLOS 03, P-D (decisión del dueño) |
| 5 | El historial guarda la hora de arranque de la corrida, no la real. | ARREGLOS 03, P-C |

La sospecha S2 (en Mercado Libre una variante sin existencias fija el precio) es la decisión 4 del dueño. Su bloque está escrito y en espera de que el dueño la confirme con lo que se midió al diseñarla (`docs/evidencia/arreglos-03/PENDIENTE-P-E.md`).

## Fábrica y catálogo (`fabrica.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | El retiro «sin pedidos» no comprueba que el producto no vendió. | ARREGLOS 04, W-M |
| 2 | Las semillas de un producto salen de ventas de otros productos. | En espera de decisión del dueño (`docs/evidencia/arreglos-04/PENDIENTE-W-K.md`) |
| 3 | La exact nace sin negative cruzado. | ARREGLOS 04, W-L |
| 4 | Pausar un lote interrumpido deja viva una campaña con id conocido. | ARREGLOS 04, W-J |
| 5 | La lista de candidatos suma clics de otro SKU del mismo ASIN. | ARREGLOS 04, W-M |

## Estimación, JEV y reputación (`estimacion.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | La comisión de Amazon se cuenta dos veces en el libro, en México y en Estados Unidos, y baja la meta de ACoS. La revisión la midió solo en México; el diseño de ARREGLOS 02 la midió también en Estados Unidos. | ARREGLOS 02, D |
| 2 | El plan de metas de precio nunca calcula el precio objetivo de Mercado Libre. | ARREGLOS 03, P-F |
| 3 | La estimación dice «ok» aunque no haya cotizado nada. | ARREGLOS 03, D-J |
| 4 | JEV cierra como éxito sin juzgar nada y el vigía no avisa. | ARREGLOS 03, D-J |
| 5 | La lectura de reputación estuvo apagada 19 días sin aviso. | ARREGLOS 03, D-K |
| 6 | Una caída de nivel de la cuenta puede perderse sin alerta. | ARREGLOS 03, D-L |
| 7 | Una alerta crítica puede no llegar nunca al Telegram. | ARREGLOS 03, D-K |
| 8 | La pantalla muestra preguntas que ya no existen. | ARREGLOS 03, D-L |
| 9 | Si falta la lista de reclamos se guarda «0 reclamos». | ARREGLOS 03, D-L |

## API, pantallas, configuración y avisos (`web.md`)

| Falla | Qué pasa | Plan y bloque |
|---|---|---|
| 1 | La meta de ACoS y el techo de puja se guardan sin rango. | Fábrica: ARREGLOS 01, X. Goals: ARREGLOS 04, W-I. Ajustes: ARREGLOS 05. |
| 2 | Un ciclo corrido a mano con fecha futura se salta el veto y la madurez. | ARREGLOS 04, W-H |
| 3 | El Resumen dice «las ventas bajaron» con días que aún maduran. | ARREGLOS 04, W-D |
| 4 | Campañas y Resumen dan gastos distintos para los mismos 30 días. | ARREGLOS 04, W-C |
| 5 | «Búsquedas sin venta» usa 3 días y el motor exige 10. | ARREGLOS 04, W-E |
| 6 | El tablero de ruido mete días inmaduros. | ARREGLOS 04, W-E |
| 7 | Los avisos de cortes de práctica prometen aplicarse y llega uno por corte. | ARREGLOS 04, W-G |
| 8 | Un error al guardar se ve como «Error: [object Object]». | ARREGLOS 04, W-F |
| 9 | Un tope escrito como «1,000» se tira en silencio. | ARREGLOS 05 |
| 10 | La tarjeta dice «Sin ventas» cuando faltan datos. | ARREGLOS 04, W-D |

## Decisiones del dueño (2026-11-12)

1. Invertir una puja pide 20 clics o 14 días; se borra el párrafo viejo de 10 días. ARREGLOS 02, A.
2. El IVA dentro del margen se le pregunta al contador. Ningún plan lo toca todavía.
3. Tope real de unas 150 pujas por ciclo (ARREGLOS 05) y confirmación arriba de 50 cambios de precio en un día (ARREGLOS 03, P-D).
4. En Mercado Libre las variantes sin existencias no fijan el precio. En espera de que el dueño la confirme: hoy 14 publicaciones tienen solo 2 unidades de la variante que fija su precio y, al venderse, su precio objetivo bajaría entre 8 y 28 % (`docs/evidencia/arreglos-03/PENDIENTE-P-E.md`).
5. Se borra el código que producción no usa. Fuera del ciclo: ARREGLOS 03 (D-A a D-C y P-A) y ARREGLOS 04 (W-A, W-B y B-A). Quitar las dos corridas de estimación que no sirven no entró: obliga a tocar el crontab de root, donde vive el respaldo diario. Los modos del ciclo: ARREGLOS 05.

## Lo que la revisión propone y no es una falla

Cada reporte termina con «Estructura: movimientos que borran complejidad». Los que son la forma de un arreglo ya van en su bloque (un solo reloj de «día cerrado», el redondeo dentro de la política, un solo lector de listas). Los demás (un solo paso de escritura con registro, un solo lector y escritor de reportes, partir `app/cycle.py`) quedan para un plan de estructura posterior a ARREGLOS 05.
