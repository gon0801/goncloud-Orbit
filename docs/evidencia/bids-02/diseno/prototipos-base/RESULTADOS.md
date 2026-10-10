# Prototipos sobre datos reales (2026-10-09)

Datos: `datos/` (extracto de producción de solo lectura; ver `datos/LEEME.md`, más `hojas_dia.csv` con
métricas diarias por hoja desde 2026-08-01). El motor aplica en vivo desde 2026-09-02. Cada script se corre
desde esta carpeta con `python3 <script>`. Son prototipos de análisis, no código del repo.

## P1. La regla del carril 1 rejugada sobre los recortes decididos en vivo (`p1_regla_carril1.py`)

Regla probada: una hoja sin pedidos y con gasto menor al umbral (350 MXN, 36 USD) no tiene evidencia propia.
Entonces manda su ad group a 90 días: se recorta si el ACoS del grupo supera 1.15 × target con 3 pedidos o
más, o si el grupo no vende y ya gastó el umbral; en cualquier otro caso no se recorta. Los recortes con
pedidos se dejan como están.

| | MX (target 20.72) | US (target 20.79) | US (target 28.34) |
|---|---|---|---|
| Recortes decididos 2026-09-02..10-09 | 405 | 346 | 346 |
| Sin evidencia propia, grupo mejor que target: no recorta | 259 | 37 | 85 |
| Sin evidencia propia, grupo entre target y 1.15× o sin datos: no recorta | 108 | 152 | 124 |
| Sin evidencia propia, grupo en sangría: recorta | 14 | 68 | 48 |
| Sin pedidos y gasto ≥ umbral: recorta | 2 | 11 | 11 |
| Con pedidos (banda actual) | 22 | 78 | 78 |
| Quedan | 38 (9 %) | 157 (45 %) | 137 (40 %) |

Lectura: la regla protege MX y no congela US. Duda abierta para el diseño: recortar una hoja de 3 clics
porque su grupo sangra casi no mueve gasto; el gasto está en las hojas grandes.

## P2. Qué le pasa al tráfico tras un recorte (`p2_desplome.py`, `p2b_…`, `p2c_desplome_base_agosto.py`)

Medida: impresiones de los 7 días posteriores al recorte aplicado entre las de los 7 previos. Base de
comparación limpia: las mismas hojas activas en agosto, cuando el motor no aplicaba (P2c). El control de
septiembre (P2 y P2b) quedó contaminado por campañas pausadas y no se usa.

Hojas activas hoy con 1,000 impresiones o más en los 7 días previos:

| | n | p10 | p25 | mediana | < 0.3 | < 0.4 |
|---|---|---|---|---|---|---|
| Agosto, sin motor | 139 | 0.39 | 0.69 | 0.91 | 6 % | 10 % |
| Tras recorte aplicado | 48 | 0.08 | 0.38 | 0.60 | 19 % | 25 % |
| Paso de −12 % | 24 | | | 0.70 | | 17 % |
| Paso de −25 % | 24 | | | 0.45 | | 33 % |

Lectura: el tráfico responde más que proporcional al bid. Un paso de −25 % deja la mitad de las
impresiones. Una guarda de desplome con umbral 0.3 sobre hojas con volumen dispara en 19 % de los recortes
contra 6 % de variación natural. Sin el filtro de volumen la guarda no distingue (27 % contra 21 %).
De los recortes aplicados a hojas con 2 pedidos o más en los 28 días previos, 28 fueron de −25 % y 11 de −12 %.
Casos con venta que se desplomaron: hojas 2963, 2871 (MX) y 5347, 5890, 4924, 5894, 4919 (US).

## P3. CPC contra bid (`p3_cpc_y_bid.py`)

| | n | bid | CPC | CPC/bid antes | CPC/bid después |
|---|---|---|---|---|---|
| Paso de −12 % | 23 | ×0.88 | ×0.94 (p25 0.83, p75 1.00) | 0.47 | 0.53 |
| Paso de −25 % | 33 | ×0.75 | ×0.76 (p25 0.73, p75 0.80) | 1.30 | 1.32 |

- Dos regímenes. Donde el CPC pagado está muy por debajo del bid (típico de MX: mediana CPC/bid 0.41 en las
  hojas con 20 clics recientes), bajar el bid casi no baja el CPC y sí quita impresiones. Donde el CPC está en
  el bid o arriba (típico de US: mediana 1.08, p90 1.83, CPC mayor que el bid en 5 de 6 hojas), el CPC baja
  1 a 1 con el bid.
- CPC mayor que el bid solo es posible con ajustes de placement o puja dinámica al alza en la campaña. Orbit
  no guarda esa configuración.
- CPC de la hoja contra CPC del resto de su ad group (hojas con 20 clics o más en 30 días): mediana 0.94 en
  ambos países; dentro de ±30 % en 67 % (MX) y 58 % (US); p10 a p90 de 0.6 a 1.3 (MX) y 0.65 a 1.64 (US).
  El CPC del grupo sirve de sustituto con un error típico de ±40 %.

## P6. Candidatos a impulsar (`p6_candidatos_impulso.py`)

| | MX | US |
|---|---|---|
| Productos con ventas o anuncios | 249 | 119 |
| Con anuncio activo | 249 | 89 |
| Sin anuncio activo y 2 pedidos o más jul–sep | 0 | 0 |
| Con anuncio activo, 2 pedidos o más jul–sep (cualquier canal) y menos de 20 clics de ads desde 2026-08-03 | 10 | 5 |
| Con anuncio activo y 0 pedidos por cualquier canal desde 2025-11 | 90 | 34 |

El "53 productos con venta orgánica sin anuncio" de `glm/B-critica.md` es un error de su script (comparó
product_id contra ASIN). No usarlo.

## Tipo de campaña, solo lo activo hoy, 90 días (2026-07-06 a 2026-10-04)

| Tipo | MX gasto | MX pedidos | MX conv | MX ACoS | US gasto | US pedidos | US conv | US ACoS |
|---|---|---|---|---|---|---|---|---|
| Exact | 42 % | 51 % | 3.36 % | 11.7 % | 10 % | 11 % | 3.41 % | 27.6 % |
| Phrase | 18 % | 13 % | 1.64 % | 15.7 % | 31 % | 26 % | 0.90 % | 40.1 % |
| Broad | 14 % | 14 % | 2.46 % | 13.0 % | 11 % | 9 % | 0.94 % | 42.9 % |
| Automática | 16 % | 11 % | 1.36 % | 20.7 % | 39 % | 40 % | 1.18 % | 29.1 % |
| Product targeting | 10 % | 11 % | 2.39 % | 10.9 % | 8 % | 14 % | 1.70 % | 20.0 % |
| Total activo | 38,201 MXN | 274 | | 13.3 % | 3,286 USD | 96 | | 31.6 % |

US apagado hoy en la misma ventana: 3,084 USD, 38 pedidos, ACoS 69.4 % (exact apagado: 103.5 %).

## Otros hechos medidos hoy

- Ventana (GLM A, `glm/scripts-a/p2_5_estabilidad.py`): predice mejor 60 días (Spearman 0.50), luego peso
  decreciente con vida media de 30 días (0.46), 90 días (0.39), 120 o toda la historia (0.29). n = 21 ad groups.
- Del 2026-09-25 al 2026-09-29 no se aplicó ninguna decisión en ningún país (motivo `modo_no_live`); el
  2026-09-30 se aplicaron 45, el tope diario.
- Caso 2963 (MX, exact): 5 recortes de 9.74 a 3.73 MXN; ACoS de ventana 26 % contra target 20.7 % y margen
  cercano a 41 %; vendía cerca de 1,000 MXN por semana y quedó en 1 clic.
