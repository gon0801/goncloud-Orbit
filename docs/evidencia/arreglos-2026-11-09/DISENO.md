# Diseño de arreglos — altos y medios de la revisión del 2026-11-09

Solo diseño. Nada de esto está implementado ni desplegado. Base de código: master `3c841f4`.

- Lista de hallazgos: `HALLAZGOS.md` (27 renglones: 4 altos y 23 medios).
- Detalle por frente: `<frente>/SINTESIS.md`. Firmas y seudocódigo completos: `<frente>/opus/DISENO.md`
  y `<frente>/sonnet/DISENO.md` (la síntesis dice cuál es la base de cada unidad).
- Método: dos diseños independientes por frente (opus y sonnet), elegí base e injertos y comprobé contra
  el código las afirmaciones de las que depende cada elección. Sin juez cruzado. La fase de
  entendimiento reutilizó los diez reportes de la revisión del mismo día.

## 0. Urgente, sin código

La decisión 2869 (cola 26) aplicaría en vivo un negative EXACT «wedding arras» en el ad group 4012 de
Estados Unidos, donde «wedding arras» es keyword EXACT propia. Vence el **2026-11-10 08:40:02 UTC**.
Se rechaza en la pantalla Cortes («Rechazar» → «Confirmar rechazo»).

## 1. Unidades, en el orden propuesto

«En vivo» dice si cambia lo que Orbit hace hoy con dinero real al desplegar.

| # | Unidad | Hallazgos | Sev | Qué cambia | En vivo | Depende de |
|---|---|---|---|---|---|---|
| 1 | AD-U1 | E-S1 | ALTO | El motor deja de proponer negative o harvest sobre un término que ya es keyword EXACT del mismo ad group | sí, quita un daño | veto de la 2869 |
| 2 | R2 | E-S1 | — | Revertir los negatives 2321 y 2322 (aplicados el 16-sep) con la ruta de reversa que ya existe | sí | 1 |
| 3 | AE-U1 | I-H1 | ALTO | Un tope diario imposible se rechaza al guardarlo, en vez de tumbar la fase de aplicar en cada ciclo | no | — |
| 4 | PI-U1 | G-H1, G-H3 | ALTO | Un día con freno masivo sin soltar ya no entra a la medida, así el freno no se suelta solo; ISR y fee variable dejan de contar como «insumo raro» | no hoy (precios en shadow) | — |
| 5 | PI-U3 | H-H1 | ALTO | Un costo corregido ya no congela el SKU ni deja sin evaluar a todos; un cierre masivo de costos aborta y avisa | sí, datos de costos | decisión 9 |
| 6 | OP-H3 | H-H3 | MEDIO | El vigilante de SP-API avisa cuando la corrida falló, no solo cuando faltó | no | — |
| 7 | AD-U2 | E-H2 | MEDIO | Revertir una subida ya no deja un «piso» que bloquea recortes 90 días | sí | — |
| 8 | AD-U5 | D-H1 | MEDIO | Apagar la protección de venta descarta las pausas en espera en vez de aplicarlas | solo al apagar | — |
| 9 | AE-U2 | E-H1 | MEDIO | La guarda de «estructura vieja» mira la última sincronización real, no una escritura del propio motor | solo si el sync muere 48 h | — |
| 10 | AE-U3 | E-H3, F-H1 | MEDIO | Una puja con resultado dudoso espera a confirmarse; se quita el reenvío automático de pujas viejas | sí | decisión 4 |
| 11 | PI-U2 | J-H3 | MEDIO | Si el insumo del día llegó tarde, la corrida de precios espera a la siguiente en vez de gastar la decisión del día | no hoy (shadow) | 4 (mismo archivo) |
| 12 | OP-J4 | J-H4 | MEDIO | El despliegue espera a que no haya un ciclo corriendo antes de recrear el contenedor | no | — |
| 13 | OP-J1 | J-H1 | MEDIO | El cron de Orbit vive en un archivo del repo con instalador que nunca quita los avisos | no | — |
| 14 | AD-U3 | A-H1, A-H2 | MEDIO | Una venta reciente conocida detiene la pausa aunque falte el dato de un día o la hoja haya dejado de recibir filas | sí | 8 |
| 15 | AD-U7 | C-H4 | MEDIO | No se sube la puja de una hoja que está en espera de venta | sí | 14 |
| 16 | AE-U4 | I-H2 | MEDIO | Un impulso cuyo grupo pausaste se cierra limpio; el vigía deja de fallar cada día | sí | — |
| 17 | PI-U4 | G-H2 | MEDIO | Las publicaciones de MeLi con variaciones sí reciben su precio | sí, cuando precios esté en vivo | 4 |
| 18 | OP-H2 | H-H2 | MEDIO | El ciclo y el vigía reintentan a las 10:40, 13:40 y 16:40 UTC si las métricas llegaron tarde, y avisan si al final no llegaron | sí, horarios | 10 y 13 |
| 19 | AD-U6a | C-H1, C-H3 | MEDIO | El freno por un cambio caduca el día 31 y se invalida si hubo negatives o harvests en el mismo ad group | sí | — |
| 20 | AD-U6b | C-H2 | MEDIO | Juicio v2: mínimo de clics para juzgar y freno aunque la semana previa haya tenido 0 ventas | se enciende por plataforma | 19 |
| 21 | AD-U8 | B-H1 | MEDIO | Una keyword que pierde la meta de su producto pasa a la de su familia en ese ciclo, sin brincar a la de plataforma | sí | decisión 12 |
| 22 | AD-U4 | A-H3 | MEDIO | La pausa de producto no ignora un día reciente sin medir | no (shadow) | — |
| 23 | AE-U5 | I-H4 | MEDIO | Tope máximo para cifras de dinero tecleadas a mano | sí, solo rechaza entradas | decisión 5 |
| 24 | PI-U5 | H-H4 | MEDIO | Corregir el producto de un ASIN mueve sus ventas pasadas al producto correcto, con rastro | sí, datos del ledger | — |

Paralelismo (inferido de los archivos que toca cada frente): operación (OP) y precios-insumos (PI)
no comparten archivos con Ads. Los dos frentes de Ads (AD, AE) comparten `app/cycle.py`, `app/apply.py`
y `app/apply_cola.py`, así que van en serie entre sí.

Cada unidad lleva una sola prueba nueva, dentro de la regla de los 4 casos. Está nombrada en la síntesis de su frente.

## 2. Reglas que valen para todas las unidades

- Toda migración nueva (AE-U4, PI-U3, PI-U5) lleva la etiqueta del plan en el nombre, el encabezado
  `-- sonda-aplicada:` y su reversa. El patrón de `migraciones_de` (`tools/despliegue/comun.sh:138-150`)
  se amplía en la misma unidad. Si no, el despliegue no la aplica y no avisa.
- Un cambio a una función de decisión del motor de Ads lleva marcador congelado o una consulta previa
  al despliegue que dé 0 filas. Hoy las de AD-U2 y AD-U3 dan 0; se repiten el día del despliegue.
- Las pruebas de Orbit se corren en Linux, no en la Mac.

## 3. Decisiones del dueño (tomadas el 2026-11-09)

David aceptó las 18 recomendaciones. El plan que las implementa es `plans/arreglos-01.md`
(implementa `deepseek_flash`, revisa `opus`). Ningún bloque del plan espera otro permiso.

| # | Unidad | Decisión | Bloque del plan |
|---|---|---|---|
| 1 | urgente | La 2869 (cola 26) se rechaza | A.1 |
| 2 | R2 | Los negatives 2321 y 2322 se revierten si siguen puestos | C |
| 3 | AD-U1 | También se bloquea el harvest de un término que ya es keyword EXACT de su ad group | B |
| 4 | AE-U3 | Se quita el reenvío automático de pujas dudosas. Cambia la decisión ADV-04 y una fila de `docs/APPLY.md` | K |
| 5 | AE-U5 | Cifras máximas a mano: puja USD 10 / MXN 180; presupuesto diario = el doble del más alto de hoy, que se mide primero | X |
| 6 | AE-U5 | Meta manual de ACoS solo entre 10 y 45 % | X |
| 7 | AE-U4 | Un impulso cuyo grupo pausaste se da por terminado | Q |
| 8 | AE | El impulso pausado con su propio botón va como unidad aparte, después. Primero se reproduce | Z |
| 9 | PI-U3 | Un costo nuevo entra al día siguiente, no el mismo día. Cambian tres letras previas (`plans/orbit-06.md:233-236`) | F |
| 10 | PI-U3 | Una corrida que cerraría más del 10 % de los SKU aborta y avisa | F |
| 11 | PI-U2 | Se espera el insumo del día hasta las 21:00 UTC antes de decidir sin él | L |
| 12 | AD-U8 | La familia aplica su propio valor en el mismo ciclo; con ledger atrasado converge en vez de saltar | V |
| 13 | AD-U6a | El freno por un cambio dura 14 días después de madurar (caduca el día 31) | T |
| 14 | AD-U7 | No se sube la puja mientras una pausa espera venta reciente | P |
| 15 | AD-U2 | No se agrega «techo aprendido» después de revertir una subida | H |
| 16 | PI-U5 | Las ventas que hoy no tienen producto (775 medidas) quedan fuera; se decide aparte | Y |
| 17 | OP-H2 | Reintentos a las 10:40, 13:40 y 16:40 UTC | S |
| 18 | OP-J1 | `ingest diario` y `spapi-diario.sh` siguen sin aviso propio; `cron-avisa` sigue viviendo solo en el servidor | N |

Ajustes al pasar el diseño a plan:

- El patrón de `migraciones_de` se amplía una sola vez, en el bloque A, no en cada unidad con migración.
- El despliegue instala el cron del repo (`cron_orbit.sh instala` dentro de `desplegar.sh`), igual que hoy
  instala el del vigía. El archivo se siembra con lo que hay en el servidor, así la primera instalación no cambia nada.
- La reversa de R2 incluye la cola 26 si alcanzó a aplicarse antes del rechazo.
- Las migraciones quedan numeradas: PI-U3 `0083`, AE-U4 `0084`, PI-U5 `0085`.

## 4. Lo que no quedó comprobado

- Si los negatives 2321 y 2322 siguen vivos en Amazon.
- #561 (espera al bridge) se prueba solo con la corrida de precios del 10-nov 13:10 UTC.
- El presupuesto diario más alto de hoy por moneda (decisión 5).
- Cómo responde MeLi cuando las variaciones de una publicación tienen precios distintos (PI-U4).
- `converge_al_destino(None, ...)` en AD-U8 (inferido: cae a plataforma como hoy).
- Que ningún otro llamador de `cycle` lea el código de salida 75 (OP-H2, inferido que no).
- J-H4 se leyó, no se simuló.
