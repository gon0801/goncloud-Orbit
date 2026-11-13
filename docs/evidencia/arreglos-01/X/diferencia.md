# Bloque X - diferencia entre las cifras del plan y los datos medidos

## Lo que dicen el plan y el diseno

- `plans/arreglos-01.md`, fila X.2: "planea_ajuste con un presupuesto de 5000
  MXN lanza ValueError".
- Fila X.4: la prueba real "rechaza 5000 MXN de presupuesto y acepta el
  presupuesto mas alto de hoy".
- `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`, seccion
  `## I-H4`, Uso primero: el ejemplo del mensaje usa un tope de 2000 MXN.

Las tres cifras eran ilustrativas: cuando se escribio el plan no se habia
medido el maximo real (la SINTESIS lo deja como riesgo: "falta medir el
presupuesto diario mas alto de hoy por moneda"). La tabla de presupuesto
vigente (`ads_campana_config_observation`, 0061) llego despues.

## Lo que dicen los datos

`docs/evidencia/arreglos-01/X/presupuestos.txt` (lee_prod.sh, solo lectura,
2026-11-13T13:16:24Z) da el maximo de las campanas ENABLED:

- MXN: 5353.78 -> tope 10708.
- USD: 26.22 -> tope 53.

Con ese tope, 5000 MXN **no** esta fuera de los topes: rechazarlo violaria la
regla de X.1 ("el tope es el doble del mas alto") y el aviso del bloque ("un
presupuesto diario no pasa del doble del mas alto que tienes hoy"). La cifra
mas alta de hoy por encima de 5000 es justamente la que la prueba real debe
aceptar.

## Forma final

- El tope de presupuesto diario es 10708 MXN y 53 USD; el de puja es 10 USD y
  180 MXN, como manda la decision 5 del dueno.
- Las pruebas de dinero usan valores por encima del tope medido (20000 MXN,
  10708.01 MXN, 100 USD, 53.01 USD) y aceptan el maximo medido (5353.78 MXN,
  26.22 USD), no 5000.
- `docs/evidencia/arreglos-01/X/prueba.py` comprueba los bordes exactos en
  produccion.
- El comportamiento, el aviso y el texto del mensaje dicen lo mismo: el tope
  es el doble del maximo de hoy, medido y guardado en `presupuestos.txt`.
