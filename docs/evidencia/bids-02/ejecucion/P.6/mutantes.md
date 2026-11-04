# Mutantes de P.6 y de R13-F

Tabla del revisor de la seccion de busquedas con el cambio que
cada mutante hace y su primer veredicto sobre el codigo de la
epoca. Tras R13-F y R15 los veintidos ids de la fila R13-F
quedan muertos o con su motivo escrito en la evidencia de
R13-F. P4 rehecho con efecto real muere por contar textos y
no filas. G1c muere por guarda de texto porque el empate no
se puede sembrar, ya que la llave primaria incluye la hora
de observacion.

| id | que cambia | primer veredicto |
|---|---|---|
| A00 | BASE sin mutar | SOBREVIVE |
| O1 | OBLIGATORIO: un ASIN propio se esconde | MUERE |
| O2 | OBLIGATORIO: la palabra suma solo busquedas sin pedido | MUERE |
| O3 | OBLIGATORIO: cuenta campana apagada (sin JOIN de estado) | MUERE |
| O3b | JOIN de estado sin filtrar ENABLED | MUERE |
| O3c | filtra el estado del ad group y no el de la campana | MUERE |
| G1 | sin DISTINCT ON | MUERE |
| G1b | se queda con la observacion mas vieja | MUERE |
| G1c | sin desempate por source_report_id | SOBREVIVE |
| G2 | sin filtro de plataforma | MUERE |
| G3 | None pasa a 0 en el resumen | MUERE |
| G3a | SQL: un dia con cost NULL se suma como 0 | MUERE |
| G3b | SQL: un dia con clicks NULL se suma como 0 | SOBREVIVE |
| G3c | SQL: un dia con orders NULL se suma como 0 | SOBREVIVE |
| G3d | resumen: pedidos None cuenta como fila completa con 0 | MUERE |
| G4 | vacias fuera antes de armar las de dos | MUERE |
| G4b | la vacia sale sola | MUERE |
| G4c | la palabra de dos letras sale sola | MUERE |
| V1 | hasta = hoy - 2 | MUERE |
| V2 | desde = hasta - 89 | MUERE |
| V3 | desde = hasta - 91 | MUERE |
| U1 | ASIN entra solo por encima del minimo | MUERE |
| U2 | palabra entra solo por encima del minimo | SOBREVIVE |
| U3 | ASIN a medio camino desde un tercio | SOBREVIVE |
| U4 | palabra a medio camino desde un tercio | SOBREVIVE |
| U5 | ASIN con pedido entra | MUERE |
| U6 | ASIN a medio camino cuenta todo lo que no llego | MUERE |
| U7 | palabra a medio camino cuenta todo lo que no llego | SOBREVIVE |
| R1 | visto cuenta todo el gasto | MUERE |
| R2 | azar sin elevar a los clics | MUERE |
| R3 | sin pedidos igual da referencia | MUERE |
| R4 | redondeo bancario | SOBREVIVE |
| R5 | la referencia deja fuera los ASIN | SOBREVIVE |
| P1 | ASIN sin unir mayusculas | MUERE |
| P2 | campanas sin ordenar | MUERE |
| P3 | cuatro ejemplos | SOBREVIVE |
| P4 | busquedas cuenta filas y no textos | SOBREVIVE |
| P5 | ASIN ordenados de menor a mayor gasto | SOBREVIVE |
| P6 | palabras ordenadas de menor a mayor gasto | SOBREVIVE |
| P7 | ejemplos con el de menos gasto primero | SOBREVIVE |
| P8 | los ASIN entran a las palabras | MUERE |
| P9 | las palabras entran a los ASIN | MUERE |
| L1 | settings mas viejos y no los vigentes | SOBREVIVE |
| L2 | ASIN propio solo del mismo mercado | MUERE |
| L3 | ASIN propio sin pasar a mayusculas | SOBREVIVE |
| L4 | campana sin nombre revienta | MUERE |
| L5 | es_asin siempre falso | MUERE |
| L6 | moneda fija MXN | SOBREVIVE |
| L7 | minimo fijo 350 | MUERE |
| L8 | ventana con borde final abierto | MUERE |
| L9 | ventana con borde inicial abierto | MUERE |
| W1 | la API ya no rechaza un mercado ajeno | MUERE |
| W2 | la pagina marca todo ASIN como propio | MUERE |
| W3 | la pagina nunca marca el ASIN propio | MUERE |
| W4 | ejemplos sin escapar | MUERE |
| W5 | campanas sin escapar | SOBREVIVE |
| W6 | palabra sin escapar | SOBREVIVE |
| W7 | la pagina trae un boton | MUERE |
| W8 | la pagina trae un formulario | MUERE |
| W9 | sin referencia igual pinta porcentajes | MUERE |
| W10 | la pagina no pinta los conteos de medio camino | SOBREVIVE |
| W11 | la pagina no pinta el gasto de la palabra | SOBREVIVE |
| W12 | el modulo escribe en la cola | MUERE |
| W13 | ASIN sin escapar | SOBREVIVE |

La comparacion con el control de esta pantalla quedo en
amazon_mx y amazon_us con ventana del 2026-07-06 al
2026-10-04 y NUMEROS OK en los dos mercados.
