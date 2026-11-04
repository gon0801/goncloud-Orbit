# D1: la previa de R6 usa crudos (bosquejo) y el prototipo usa pesados

Fecha: 2026-10-10. Owner M.1. Estado: decisión del lead pendiente; el código
implementa el bosquejo literal y la divergencia queda documentada aquí.

## Qué se observa

La reproducción s1 (`s1_niveles_v3.txt` vs `s1_esperado.txt`) difiere en 2 de
820 casos. El bloque fijado por el plan (`amazon_mx | recortes`: 6 recortes de
405, en 4 hojas) cuadra exacto. Las diferencias están en otros dos bloques:

- MX subidas: 40 `subir:bajo_target` / 9 `mantener:azar_lo_explica` en vez de
  41 / 8. El caso es la hoja 2457 el 2026-09-07 (gasto 115.45).
- US recortes: 44 `mantener:azar_lo_explica` / 10 `mantener:esperando_precio_medido`
  en vez de 43 / 11. El caso es la hoja 5565 el 2026-09-18 (gasto 137.39).

## Causa

R6 frena la subida con una previa cuenta → resto del ad group (K = 1). El
bosquejo la define dos veces con enteros sin pesos:

- `bosquejo.py:79`: `pedidos_crudos`, "la usan ... la previa".
- `bosquejo.py:368`: "la previa (cuenta → resto del ad group, K = 1, enteros
  sin pesos) FRENA la subida".

El prototipo (`politica.py: previa`) usa pesados (float) por comodidad. Con
inputs distintos, `p_bajo` cruza el umbral 0.70 en direcciones opuestas:

| Caso | p_bajo prototipo (pesados) | p_bajo niveles_v3 (crudos) |
|---|---|---|
| 2457 2026-09-07 | 0.7109 → subir | 0.6866 → azar_lo_explica |
| 5565 2026-09-18 | 0.7008 → subir → R14 | 0.6780 → azar_lo_explica |

Todos los demás números (p_sobre ×3, acos_hoy, cpc, estado de grupo) coinciden
a 1e-15 entre ambas implementaciones: la previa es la única divergencia. No se
tocó ninguna constante (procedimiento del plan para conteos que no cuadran).

## Decisión tomada

Se implementa el bosquejo literal (previa con crudos, `_previa` en
`app/optimizer/politica.py`). Razones:

1. El diseño lo dice dos veces y sin ambigüedad.
2. El plan solo fija el bloque MX-recortes de s1, que cuadra.
3. El procedimiento del plan ante un conteo distinto es anotar y avisar, no
   reescribir el diseño para igualar al espejo.
4. "La política" del DoD dorado es niveles_v3: el fixture trae sus 107 casos
   de acción; los 2 divergentes caen en "el resto" y se excluyen de la muestra
   con esta nota (ver `procedencia.divergentes_excluidos_D1` en el fixture).

## Alternativa para el lead (una línea)

Si el lead quiere igualar al prototipo: cambiar `_previa` a pesados
(`pedidos_pesados`/`clics_pesados`/`venta_pesada` de cuenta y resto) y
regenerar el fixture. Con eso s1 cuadra en los 820 casos (verificado: solo la
previa difiere). Costo: contradecir `bosquejo.py:79` y `:368`.

## Fallo del lead (2026-10-10)

Se queda el bosquejo literal (crudos). El bosquejo lo fija dos veces y sin
ambiguedad (`:79`, `:368`); la guia M.1 manda construir desde sus bloques y su
Comprueba solo fija el bloque MX-recortes (6 de 405, 4 hojas), que cuadra
exacto, y ordena anotar+avisar ante otro conteo distinto, no reescribir el
diseno para igualar al espejo. La tabla R6 no discrimina (dice "contando en
contra la conversion", sin pesos). Verificado por el lead: el diff s1 son
exactamente esos 2 casos; la exclusion va pineada en
`procedencia.divergentes_excluidos_D1` del fixture dorado; la dorada trae los
107 casos de accion de niveles_v3. Si el dueno quiere pesados, es una linea
en `_previa` + regenerar el fixture (costo: contradecir el diseno aprobado).
