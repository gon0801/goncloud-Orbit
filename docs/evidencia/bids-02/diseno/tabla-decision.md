# Tabla de decisión de la política de bid `niveles_v3`

Esta tabla es la política efectiva completa. Se lee de arriba hacia abajo y gana la primera regla que aplica.
Es la misma lista, con los mismos números de regla, que el docstring de `decide` en `bosquejo.py`.

Antes de todo: si la clave `ads_bid_politica_<plataforma>` está ausente, el motor no mueve bids en esa
plataforma y cada hoja se cuenta con motivo `politica_apagada`. PAUSE, negative y harvest siguen.

Antes de esta tabla siguen corriendo, sin cambios, los filtros de siempre: campaña con goal habilitado,
campaña y ad group encendidos, hoja encendida, hoja sin veto pendiente y hoja con impresiones en los últimos
14 días. Una hoja nueva sin ninguna impresión no llega a la tabla: se salta como inerte, igual que hoy.

## Palabras que usa la tabla

| Palabra | Qué significa |
|---|---|
| Ventana madura | Los días D−90 a D−10. Se parte en dos tramos: los 41 días recientes pesan 1 y los 40 anteriores pesan 1/2. |
| Pedidos pesados | Pedidos del tramo reciente más la mitad de los del tramo antiguo. Si salen en medios se redondean en contra del movimiento: hacia arriba para recortar y hacia abajo para subir. |
| ACoS al bid de hoy | CPC al bid vigente entre conversión por ticket de la ventana madura. El CPC sale de la escalera de precio (abajo). |
| Equilibrio | El margen neto antes de publicidad de la plataforma (MX 41.43 %, US 31.49 %). Arriba de ese ACoS cada venta pierde dinero. |
| Gasto para concluir | 350 MXN o 36 USD. Es el gasto sin venta que hace falta para concluir algo, y es también el tope de aprendizaje de un impulso. |
| El ad group sangra | Con 3 pedidos o más, su ACoS pesado supera 1.15 × target. Con cero pedidos, ya gastó el gasto para concluir. Solo cuentan sus hojas encendidas. |
| El ad group cumple | Con 3 pedidos o más, su ACoS pesado es igual o menor al target. |
| Razón de tráfico | Impresiones por día después del último cambio entre impresiones por día de los 7 días previos. |
| Seguro | Probabilidad de 0.80 o más para recortar y de 0.70 o más para subir (las confianzas que ya existen en `/settings`). |

## La tabla

| # | Condición | Acción | Motivo guardado |
|---|---|---|---|
| R0 | Cero pedidos, clics y gasto sobre los umbrales de pausa, con datos de 10 días de madurez (regla de hoy, sin cambios). | PAUSE con veto de 48 horas | `pause_umbral` |
| R1 | El último cambio fue un recorte del motor, la hoja vendía (1 pedido o más en los 90 días previos), tenía volumen (1,000 impresiones o 20 clics en 7 días), ya hay 4 días posteriores y la razón de tráfico es menor a 0.30. | Regresar al bid anterior al recorte | `regreso_por_desplome` |
| R2 | Han pasado menos de 7 días desde el último cambio de bid, sea del motor o del dueño, o desde un ajuste por ubicación que el dueño hizo en la campaña de la hoja. | No mover | `esperando_efecto` |
| R3 | Tiene pedidos y es seguro que su ACoS al bid de hoy supera el equilibrio. | Recorte de 12 %. De 25 % si además es seguro que supera 1.35 × target. | `pierde_dinero`, `pierde_dinero_fuerte` |
| R4 | Tiene pedidos, no cae en R3, su ACoS al bid de hoy supera el target y su ad group sangra. | Recorte de 12 %, nunca de 25 % | `grupo_sangra_vendedora` |
| R5 | Tiene pedidos, no cae en R3 ni en R4, y es seguro que supera 1.15 × target. | No mover | `vende_dentro_del_margen` |
| R6 | Tiene pedidos y es seguro que su ACoS al bid de hoy está debajo de 0.85 × target, contando en contra la conversión de su ad group y de la cuenta. | Subida de 15 % | `bajo_target` |
| R7 | Tiene pedidos y ninguna de las anteriores. | No mover | `azar_lo_explica` |
| R8 | No tiene pedidos en la ventana madura, pero ya se ve un pedido en los últimos 9 días. | No mover | `venta_reciente` |
| R9 | No tiene pedidos y su gasto en la ventana madura llegó al gasto para concluir. | Recorte de 12 %. De 25 % si llegó al doble. | `gasto_sin_venta`, `gasto_sin_venta_doble` |
| R10 | No tiene pedidos ni gasto en la ventana madura. | No mover | `sin_gasto` |
| R11 | No tiene pedidos, gastó entre un cuarto y una vez el gasto para concluir y su ad group sangra. | Recorte de 12 % | `grupo_sangra` |
| R11b | No tiene pedidos, gastó menos de un cuarto del gasto para concluir (87.50 MXN, 9 USD) y su ad group sangra. | No mover | `hoja_delgada_sin_dinero_que_mover` |
| R12 | No tiene pedidos, gastó menos que el gasto para concluir y su ad group cumple. | No mover | `grupo_cumple` |
| R13 | No tiene pedidos, gastó menos que el gasto para concluir y su ad group no tiene veredicto. | No mover | `sin_evidencia` |
| R14 | La acción elegida va en dirección contraria al último cambio, y todavía no hay 20 clics al bid nuevo ni han pasado 14 días. | No mover | `esperando_precio_medido` |
| R15 | La acción elegida repite la dirección del último cambio y no hay 20 clics nuevos desde ese cambio. | No mover | `sin_clics_nuevos` |
| R16 | Sería otro recorte y la razón de tráfico tras el recorte anterior fue menor a 0.70. O sería otra subida y la razón tras la subida anterior fue menor a 1.10. | No mover | `recorte_costo_trafico`, `subida_sin_trafico` |
| R17 | Sería un recorte que deja el bid en o por debajo de un bid desde el que ya hubo que regresar (por desplome o porque el dueño lo regresó). | No mover | `piso_aprendido` |
| R18 | Cualquier dato que la regla necesita viene desconocido, o no hay forma de conocer el CPC. | No mover | `dato_faltante`, `sin_precio` |
| R19 | El movimiento elegido no cabe entre el piso y el techo de bid, o mueve menos de un centavo (reglas de hoy, sin cambios). | No mover | `rango_bloquea_ajuste`, `delta_bajo_umbral` |

Escalera de precio (de dónde sale el CPC al bid de hoy), en orden:

1. Si ya hay 20 clics pagados después del último cambio, el CPC medido en esos clics.
2. Si hubo cambio y todavía no hay 20 clics, el CPC de antes multiplicado por bid nuevo entre bid anterior.
3. Si no hubo cambio en 90 días, el CPC de los últimos 30 días legibles.
4. Si no hay clics recientes, el CPC de la ventana madura.

## Los casos de poca data, uno por uno

| Caso | Qué regla lo atiende | Qué pasa | Qué pasaba antes |
|---|---|---|---|
| Hoja nueva sin impresiones | Filtro de inerte, antes de la tabla | Nada. No se decide. | Igual. |
| Hoja nueva con impresiones y sin clics | R10 | Nada. | Nada hasta juntar 7 días con fila; después, recorte de 12 % con el primer clic sin venta. |
| Hoja de un impulso mientras aprende | Su goal está en modo `shadow` | El motor decide y guarda, y no aplica nada. | No existía. |
| Hoja con 3 clics sin venta, ad group sano | R12 | Nada. | Recorte de 12 % cada 8 días hasta el piso de bid. |
| Hoja con 3 clics sin venta, ad group sin veredicto | R13 | Nada. | Recorte de 12 % cada 8 días. |
| Hoja con 3 clics sin venta (unos 15 MXN), ad group que sangra | R11b | Nada: recortarla no mueve gasto y la deja sin volver a juntar datos. | Recorte de 12 % cada 8 días. |
| Hoja sin venta con 130 MXN de gasto, ad group que sangra | R11, y después R15 | Un solo recorte de 12 %. No hay segundo hasta que junte 20 clics nuevos. | Recorte de 12 % cada 8 días. |
| Hoja sin venta que ya gastó 350 MXN o 36 USD | R9 | Recorte de 12 % (25 % al doble). Antes de llegar ahí suele pausarse por R0. | Recorte de 25 % solo si el grupo tenía umbral propio. |
| Vendedora sobre el target y bajo el margen, ad group sano (caso 2963) | R5 o R7 | Nada. Con su ventana de 90 días pesada, 2963 marca 14.7 % contra un target de 20.72 %. | Cinco recortes, de 9.74 a 3.73 MXN, y perdió su tráfico. |
| Vendedora sobre el target y bajo el margen, ad group que sangra (caso 4924) | R4, y después R16 o R1 | Un recorte de 12 %. El siguiente solo si conservó 70 % de su tráfico y juntó 20 clics. Si el tráfico cae a menos de 30 %, regresa. | Cinco recortes, de 0.40 a 0.11 USD, y perdió su tráfico. |
| Vendedora que pierde dinero con certeza | R3 | Recorte de 12 % o de 25 %. | Recorte de 25 %. |
| Vendedora bajo el target con evidencia | R6, y después R16 | Subida de 15 %. Otra subida solo si la anterior trajo tráfico. | Subida de 15 % solo con 3 pedidos en 30 días. |
| Vendedora con 1 pedido y pocos clics | R7 | Nada: un solo pedido no vence a la conversión de su ad group. | Nada (pedía 3 pedidos). |
| Grupo en sangría en US | R4 y R11 | En el mes simulado se recortan 17 hojas que cargan 72 % del gasto de US, con 22 recortes de 12 %. | 138 recortes aplicados en 44 hojas. |
| Abstención | R5, R7, R8, R10, R12, R13, R18 | Nunca recorta. No existe regreso a otra política. | Con `evidencia_v2` encendida, la abstención regresaba a las bandas viejas. |
| Recorte que desploma el tráfico | R1 | Regresa al bid anterior al quinto día y aprende ese piso (R17). | Seguía recortando sobre la misma ventana. |
| Keyword que el dueño regresó desde la pantalla | R2, R15, R17 | Queda protegida: espera 7 días, exige 20 clics nuevos para recortar y no baja del bid que la dañó. | El motor la volvía a recortar al día siguiente. |

## Reglas nuevas: su número y de dónde sale

| Regla | Número | De dónde sale |
|---|---|---|
| R1, días para la guarda | 4 días posteriores | Prototipo A4: con 4 días la guarda dispara en 20 % de los recortes contra 6 % de variación natural en agosto. Con 7 días da 18 % contra 5 %. Cuatro días es lo más pronto que el dato permite (día 5 tras el recorte). |
| R1, umbral de desplome | 0.30 | Prototipo P2 de la base y A4. |
| R1, filtro de volumen | 1,000 impresiones o 20 clics en 7 días | P2: sin el filtro la guarda no distingue (27 % contra 21 %). |
| R2 | 7 días | Es el cooldown de hoy (`goals.COOLDOWN`) con otro nombre. No es un número nuevo. |
| R3, equilibrio | Margen de la plataforma | La misma medición que ya produce el target (`v_target_margen_plataforma`). Si el target no vino del margen, el equilibrio es desconocido y R3 no dispara. |
| R3 a R7, confianzas | 0.80 y 0.70 | Las que ya existen en `/settings` para `evidencia_v2`. |
| R3 a R7, bandas | 1.35, 1.15 y 0.85 | Las de hoy, sin tocar. |
| R4 y R11, ad group que sangra | 1.15 × target con 3 pedidos, o cero pedidos con el gasto para concluir | Regla de grupo aprobada por el dueño el 2026-10-09 y medida en P1. |
| R4, solo 12 % | Paso de 12 % | A4: tras un paso de 12 % el tráfico se desploma en 1 de 19 casos (5 %, igual que sin motor). Tras uno de 25 %, en 9 de 32 (28 %). |
| R6, la previa frena la subida | 1 pedido prestado del nivel de arriba | Es el pliegue de `evidencia_v2` (K = 1). Se conserva solo para subir. Sin él, 1 pedido en 17 clics subía el bid (prototipo, hoja 2875). |
| R9 | 350 MXN y 36 USD | Decisión del dueño del 2026-10-09. Equivale a 1.6 veces lo que el target permite gastar por pedido: con ese gasto una hoja en el target ve cero pedidos 20 % de las veces (runner-b lo derivó en 347.6 MXN y 36.0 USD). |
| R11 y R11b, materialidad | Un cuarto del gasto para concluir | Prototipo `p_c2` de runner-c: en un grupo que sangra, las hojas por debajo de ese gasto cargan de 0.4 % a 2 % del gasto del grupo. |
| R14, salida por tiempo | 14 días | Elegido: dos veces los días de efecto. No está medido. Va como pregunta abierta. |
| R15 | 20 clics | Es el mínimo de `evidencia_v2` (`MIN_CLICS_CPC`), aplicado a repetir dirección en vez de a poder opinar. |
| R16, recorte que no costó tráfico | 0.70 | P2: el cuartil bajo de la variación natural es 0.69. A4: 26 % de las hojas cae debajo de 0.70 sin motor, contra 61 % tras un recorte. |
| R16, subida que trajo tráfico | 1.10 | A7: 67 % de las subidas de MX y 75 % de las de US pasan 1.10, contra 30 % sin motor. |
| R17 | Sin número | Se deriva de la historia de bids de la hoja. |
| Pesos de la ventana | 1 y 1/2, 41 y 40 días | A5: los dos tramos predicen mejor que 90 días planos (correlación 0.57 contra 0.39, error 0.91 contra 1.04 puntos; 21 ad groups). |
| Escalera de precio, paso 2 | CPC × bid nuevo ÷ bid anterior | A4: error mediano de 6 %. El CPC del resto del ad group erra 26 % y el CPC de antes sin tocar erra 27 %. |

## Qué responde la tabla a las dudas abiertas

- **Cómo se detiene el recorte de una hoja que vende bajo el margen (duda 1).** No con un tope acumulado. Se
  detiene por tres hechos medidos, en este orden: sus 90 días pesados no demuestran que esté arriba del target
  (R5, R7), su ad group no sangra (R4 no aplica) y, si aun así recibe un recorte heredado, el siguiente exige
  que el anterior haya conservado el tráfico (R16) y el desplome se regresa (R1).
- **Qué hacer en MX (duda 2).** En MX el bid mueve volumen, no precio: una subida de 15 % trae 23 % más
  impresiones (A7) y un recorte de 12 % quita 20 % (A4), mientras el CPC cambia 7 %. Por eso un recorte en MX
  solo se da a lo que debe perder volumen (R3, R9, R11) o al grupo que sangra (R4), siempre de 12 %, y se juzga
  por su efecto antes de repetirlo.
- **Qué sustituye a los 20 clics al bid vigente (duda 3).** El CPC escalado por el cambio de bid, hasta que
  haya 20 clics medidos. Los 20 clics dejan de ser una condición para opinar y pasan a ser la condición para
  repetir dirección.
- **Dónde actúa la regla de grupo (duda 4).** Sobre las vendedoras cuyo dato propio no concluye (R4), que es
  donde está el dinero: en US 95 % del gasto de los grupos que sangran está en hojas con pedidos o con el gasto
  para concluir, y las hojas de pocos clics suman 5 % (A4). R11 se conserva porque el dueño la aprobó, y R15 la
  limita a un recorte por hoja.
