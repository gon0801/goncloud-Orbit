# Brief común: cacería de bugs peligrosos en ORBIT 07

## Meta del dueño

ORBIT 07 se implementó para corregir problemas del motor de Amazon Ads (MX y US).
El dueño quiere confirmar que ya no quedan bugs peligrosos en dos frentes:

1. Dinero: pujas, pausas, presupuestos, metas de ACoS, cualquier escritura a Amazon.
2. Funcionamiento del motor: que el ciclo no se caiga, no se trabe, no deje la cola
   atorada, no aplique dos veces, no mezcle shadow con live.

Tu trabajo es intentar demostrar que el código está mal. No es una revisión de estilo.

## Estado real (comprobado hoy, 2026-11-09)

- ORBIT 07 entró a master en el commit `7cf7c9a` (PR #547, squash). El diff completo
  se ve con `git show 7cf7c9a -- <archivo>`.
- Está desplegado y encendido en producción desde el 2026-11-08:
  - `ads_proteccion_venta = proteccion_venta_v1` (M1, global).
  - `ads_producto_economia_<plat> = producto_directo_v1` en amazon_us y amazon_mx (M2).
  - `ads_bid_economia_<plat> = economia_cambio_v1` en amazon_us y amazon_mx (M5/M6).
  - M4 (pausa espera venta reciente del producto) es la política `v3`, default del código.
  - Meta por producto: live. Pausa por producto: shadow en MX y US.
- Por eso un bug aquí ya mueve dinero real.

## Dónde trabajar

- Copia de trabajo de solo lectura en master actual:
  `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt`
  Haz `cd` ahí. No uses `/Users/dn/dev/goncloud-Orbit` (está en otra rama, sin ORBIT 07).
- Plan: `plans/orbit-07-motor-decisiones.md`. Reglas del dominio: `docs/CONTEXTO.md`.
- Triage previo (F1 a F30): `docs/evidencia/orbit-07/cierre-2026-11-08.md`. No repitas un
  hallazgo de ahí salvo que la corrección esté mal o incompleta, y entonces dilo.
- Políticas por mejora: `docs/evidencia/orbit-07/m1/`, `m2/`, `m3m4/`, `m5/`, `m6/`.
- Python para sondas: `/Users/dn/dev/goncloud-Orbit/.venv/bin/python` con
  `PYTHONPATH=<la copia de trabajo>`. El optimizador (`app/optimizer/`) es puro y se
  puede llamar sin base de datos.

## Reglas

- SOLO LECTURA. No edites, no hagas commit, no cambies de rama, no toques producción,
  no hagas ssh. Tu único archivo de salida es el que te indica tu encargo. Las sondas
  que escribas van en la carpeta `scratchpad/orbit07-review/sondas/`.
- En esta copia no existe `graphify-out/graph.json`, así que busca con `rg` directo.
- No corras la batería completa de pytest. En esta Mac las pruebas con base fallan en
  falso (reloj de colima adelantado, sin `setsid`). Prefiere sondas puras con Python.
  Si un hallazgo necesita base, deja el comando escrito y márcalo "no ejecutado".
- Lee el código actual completo de tus archivos, no solo el diff. Sigue cada valor
  hasta donde se convierte en una puja, una pausa o una fila de la cola.
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
- Alcance con la config actual de producción: ¿se dispara hoy? ¿en live o solo en shadow?
- Evidencia: comando exacto y salida, o el camino leído
- Cómo reproducir: comando

## Sospechas sin confirmar
## Zonas revisadas sin hallazgos (una línea cada una, con qué comprobaste)
```

Tu mensaje final de regreso: 10 líneas como máximo, con el conteo por severidad y la
ruta del archivo. El detalle vive en el archivo.
