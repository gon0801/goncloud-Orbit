# Brief común: cacería de bugs peligrosos en toda la app Orbit

## Meta del dueño

El dueño quiere confirmar que la app Orbit completa (no solo ORBIT 07) ya no tiene
bugs peligrosos en dos frentes:

1. Dinero: pujas, pausas, presupuestos, metas de ACoS, precios de venta, costos,
   tipo de cambio, cualquier escritura a Amazon, Mercado Libre, Shopify u Odoo.
2. Funcionamiento del motor: que los ciclos y corridas no se caigan, no se traben,
   no dejen colas atoradas, no apliquen dos veces, no mezclen shadow con live, y que
   una falla no pase en silencio.

Tu trabajo es intentar demostrar que el código está mal. No es una revisión de estilo.

## Estado real (comprobado hoy, 2026-11-09)

- Orbit corre en producción en el servidor goncloud (contenedor orbit-app-1), con cron
  diario. El motor de Ads de Amazon MX y US está en live. El motor de precios corre
  cada 2 horas para amazon_mx, amazon_us y meli. Asume que el código que lees mueve
  dinero real salvo que compruebes que su interruptor está apagado por default.
- Master actual es `3c841f4`. Otros cuatro revisores ya cubren el diff de ORBIT 07
  (commit `7cf7c9a`: protección por venta reciente, economía de producto, economía del
  último cambio de puja). No repitas ese diff. Tú cubres el resto de tu zona.
- El cron de producción (referencia): `docs/DEPLOY.md` y
  `docs/evidencia/ingesta-01/ejecucion/D.1/crontab-antes.txt`.

## Dónde trabajar

- Copia de trabajo de solo lectura en master actual:
  `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt`
  Haz `cd` ahí. No uses `/Users/dn/dev/goncloud-Orbit` (está en otra rama, atrasada).
- Reglas del dominio y trampas conocidas: `docs/CONTEXTO.md` (léelo primero, sobre todo
  "Reglas de diseño innegociables" y "Trampas del dominio"). Una regla innegociable que
  el código viola es un hallazgo.
- Residuales ya aceptados por el dueño: búscalos en `plans/` y `docs/` de tu zona
  (rg: residual, deuda, aceptado). No los reportes como nuevos; si uno es peligroso,
  dilo como tal y cita dónde se aceptó.
- Python para sondas: `/Users/dn/dev/goncloud-Orbit/.venv/bin/python` con
  `PYTHONPATH=<la copia de trabajo>`.

## Reglas

- SOLO LECTURA. No edites, no hagas commit, no cambies de rama, no toques producción,
  no hagas ssh. Tu único archivo de salida es el que te indica tu encargo. Las sondas
  que escribas van en la carpeta `scratchpad/orbit07-review/sondas/`.
- En esta copia no existe `graphify-out/graph.json`, así que busca con `rg` directo.
- No corras la batería completa de pytest. En esta Mac las pruebas con base fallan en
  falso (reloj de colima adelantado, sin `setsid`). Prefiere sondas puras con Python.
  Si un hallazgo necesita base, deja el comando escrito y márcalo "no ejecutado".
- Tu zona es grande. Prioriza: primero el camino que termina en una escritura externa
  o en un número de dinero, de punta a punta; después lo demás. Sigue cada valor hasta
  donde se convierte en una puja, un precio, un presupuesto, una pausa o una fila de cola.
- Términos de dominio en inglés sin traducir: harvest, keyword, ad group, negative, bid.

## Qué cuenta como hallazgo

Solo reporta lo que cumpla las tres condiciones:

1. Toca dinero o el funcionamiento del motor (ver la meta).
2. Tiene un escenario concreto: qué datos o estado de entrada, qué hace el código,
   qué debería hacer. Con `archivo:línea` de la copia de trabajo.
3. Lo comprobaste: sonda ejecutada con su salida, o lectura línea por línea de todo el
   camino. Di cuál de las dos. Si es sospecha sin comprobar, va en una sección aparte
   "Sospechas sin confirmar", máximo 5, con lo que faltaría para confirmarla.

No reportes: estilo, nombres, documentación, falta de pruebas, deuda técnica,
rendimiento sin riesgo de timeout del ciclo.

Si una zona está bien, dilo en una línea con lo que comprobaste. "No encontré nada"
con evidencia vale tanto como un hallazgo. No infles la lista.

## Formato del archivo de salida (en español)

```
# <ángulo>: resultado

## Hallazgos confirmados
### H1. <título de una línea>
- Severidad: CRÍTICO (pierde dinero o tumba el ciclo hoy) | ALTO | MEDIO
- Frente: dinero | motor
- Dónde: archivo:línea
- Escenario: entrada → qué pasa → qué debería pasar
- Alcance: ¿se dispara con el cron y los defaults actuales? ¿en live o solo en shadow o a mano?
- Evidencia: comando exacto y salida, o el camino leído
- Cómo reproducir: comando

## Sospechas sin confirmar
## Zonas revisadas sin hallazgos (una línea cada una, con qué comprobaste)
```

Tu mensaje final de regreso: 10 líneas como máximo, con el conteo por severidad y la
ruta del archivo. El detalle vive en el archivo.
