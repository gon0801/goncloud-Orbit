# ARREGLOS 04 — cierre

Resultados del SHA integrado y desplegado, con los seis casos de aceptación TN,
sus controles y el estado de los 21 bloques.

- SHA integrado: `f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683` (merge de #680, el
  último bloque, sobre `1fb9ff6d`).
- SHA desplegado y verificado en producción: el mismo
  `f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683`.
- Fecha del despliegue y de esta corrida: 2026-11-19, 18:53 America/Vancouver.
- Este PR es documental: no despliega, no aplica migraciones, no toca
  interruptores y no abre TEMPORADA 01.
- Cero escrituras reales en Amazon y sin secretos. El transporte de escritura
  del banco va interceptado.

## Servidor

Comprobado con `ssh goncloud` después del `checklist.sh`:

```
sha desplegado: f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683
health: {"status":"ok"}
--- ultimo.env
MOTOR_SHA=f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683
ESTADO=guardas
SHA_PREVIO=1fb9ff6ddf099e54a181867cc9823693a7eef521
CODIGO=predeploy-20261120-025331
ESTADO=desplegado
ESTADO=verificado
```

El `productos.js` servido por `/static/js/productos.js` trae los marcadores de
W-P (`envioPendiente` y `mismaSeleccion`, 6 coincidencias), así que la pantalla
en producción es la de este SHA.

Despliegue con los scripts originales de `tools/despliegue/` bajo Bash 5.2, desde
un worktree limpio del merge (`git diff f7441de f5dc86d5` vacío). Ensayo de solo
lectura con `SHA_PREVIO=1fb9ff6d`, guardas libres, cero migraciones pendientes y
ninguna clave de interruptores cambiada. `desplegar.sh` salió 0 y `checklist.sh`
salió 0 con `CHECKLIST VERDE f5dc86d5...`. ARREGLOS 04 no trae migraciones
propias, así que este despliegue no tocó base ni modos.

## Los ocho casos, sobre el SHA integrado

Corridos desde un checkout limpio de `f5dc86d5`
(`git worktree add --detach`), con PG16 desechable en
`127.0.0.1:55449/postgres` y Chromium real. El runner reporta
`sha_reportado: f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683` e importa `app.main`,
`app.pantalla_ruido` y `app.ui_metricas` de ese mismo checkout (`harness.json`).

```bash
bash docs/evidencia/arreglos-05/comprobar.sh T writes "test_02" -rA
bash docs/evidencia/arreglos-05/comprobar.sh T ui "test_04 or test_15 or test_16 or test_control" -rA
```

Salidas completas en `tn02-writes.log` y `tn04-15-16-ui.log`.

| Caso | Hallazgo | Owner | Resultado |
| --- | --- | --- | --- |
| lista incompleta `missing` no autoriza repost | TN02 | B-C | PASSED |
| lista incompleta `truncated` no autoriza repost | TN02 | B-C | PASSED |
| retiro no envía el plan borrado tras fallar la previa nueva | TN04 | W-P | PASSED |
| impulso no envía el plan borrado tras fallar la previa nueva | TN04 | W-P | PASSED |
| ruido no publica ACoS con gasto desconocido | TN15 | W-E | PASSED |
| resumen no afirma ausencia de ventas que la API reporta | TN16 | W-D | PASSED |
| contrato del resumen conserva desconocido y CSP | control | W-D | PASSED |
| query y form no sustituyen el header de autenticación | control | API | PASSED |

Total: 2 + 6 pruebas en verde, sin skips, sin xfail y sin errores de fixture. La
matriz del plan queda cumplida por sus dueños: TN02 en B-C, TN04 en W-P, TN15 en
W-E y TN16 en W-D, con los dos controles de desconocido/CSP y autenticación en el
mismo HEAD.

## W-D.5, comparación después del desplegar

Medida por este cierre sobre el SHA desplegado `f5dc86d5`, que contiene W-D, con
los comandos del anexo A del diseño. El detalle completo, con la ventana y el
corte que declara la API, está en
`docs/evidencia/arreglos-04/W-D/comparacion-origen.txt`.

- `comparison` de `amazon_mx` a 7 días: `desde` `2026-11-05`, `hasta`
  `2026-11-11`, `observado_hasta` `2026-11-13T03:00:48.064623+00:00`,
  `totals.ad_revenue` `41337.5200`.
- La misma ventana y el mismo corte con `lee_prod.sh`, solo `amazon_mx`:
  `41337.5200`.

`docs/evidencia/arreglos-04/W-D/comparacion.txt` guarda las dos líneas que su
`Acepta` compara como `Decimal`, y ese comando sale 0. Las dos lecturas fueron
de solo lectura, una por la API y otra por `orbit_read`. La cifra también
aparece en `DISENO.md` como total de una consulta histórica distinta; lo que
acredita esta fila es que la consulta usó la ventana y el `observado_hasta` que
la respuesta declara, y eso queda registrado en el archivo de origen.

La medición original que dejó la sesión de W-D se conserva aparte, en
`W-D/comparacion-sesion61.txt`. No declara SHA, ventana ni corte, así que no
acredita la fila y no se le atribuye esta corrida. Lo medido aquí es
`f5dc86d5`, no el SHA histórico de aquel archivo.

## Los 21 bloques

| Bloque | PR | Merge | Evidencia |
| --- | --- | --- | --- |
| BASE | #656 | `8c58d56` | `BASE/rebase.md` |
| W-A | #659 | `effd46c` | `W-A/{prueba.py,sin-uso.txt}` |
| W-B | #660 | `0e3c6a4` | `W-B/{prueba.py,sin-uso.txt}` |
| W-C | #668 | `08e178b` | `W-C/prueba.py` |
| W-D | #677 | `21ccca2` | `W-D/{prueba.py,aceptacion-tn.md,comparacion.txt}` |
| W-E | #675 | `54f0d98` | `W-E/{prueba.py,aceptacion-tn.md}` |
| W-F | #670 | `a50cce0` | `W-F/prueba.py` |
| W-G | #658 | `480a5a3` | `W-G/prueba.py` |
| W-H | #657 | `c56cdfe` | `W-H/prueba.py` |
| W-I | #663 | `e42a4a0` | `W-I/prueba.py` |
| W-J | #671 | `2b1b6f9` | `W-J/prueba.py` |
| W-K | #674 | `a967874` | sus `Acepta` son greps del texto en el PR |
| W-L | #672 | `30c9a67` | `W-L/prueba.py` |
| W-M | #673 | `8d1f143` | `W-M/{prueba.py,diferencia.md}` |
| W-N | #679 | `1fb9ff6` | `W-N/prueba.py` |
| W-O | #665 | `b540cc8` | `W-O/{prueba.py,vocabulario.txt}` |
| W-P | #680 | `f5dc86d` | `W-P/aceptacion.md` |
| B-A | #666 | `24aa36f` | `B-A/sin-uso.txt` |
| B-B | #676 | `3d8a35a` | `B-B/{prueba.py,cortes.png}` |
| B-C | #661 | `c522eef` | `B-C/{prueba.py,aceptacion-tn.md,mide_paginas.py,paginas.txt}` |
| B-D | #678 | `b30b891` | `B-D/` (reparación, revisión y comprobador) |

Los 21 están integrados en `f5dc86d5` y ese SHA está desplegado y verificado, así
que los 21 están en producción. La aceptación de cada bloque vive en su carpeta
y se verificó en su PR; este cierre vuelve a correr los ocho casos TN y los
controles, que son los que el plan exige juntos en el mismo HEAD.

## Qué queda fuera de este cierre

- TEMPORADA 01 no se abre aquí. Es la siguiente entrega de la cola conservadora,
  con su propio plan.
- ARREGLOS 05 verificará estos hallazgos al cerrar Orbit. Este PR solo deja la
  evidencia congelada del SHA entregado.
- No se re-despliega nada por este PR documental.

## Cómo reproducir

```bash
git worktree add --detach /tmp/cierre f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683
cd /tmp/cierre
# ORBIT_TEST_DSN ya viene configurado en el entorno apuntando al PG16 desechable
# del laboratorio: 127.0.0.1, puerto 55449, base postgres. El lanzador valida el
# destino antes de conectarse y rechaza cualquier otro.
export ORBIT_CHROMIUM='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh \
  T writes "test_02" -rA
uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh \
  T ui "test_04 or test_15 or test_16 or test_control" -rA
```

El lanzador rechaza cualquier DSN que no sea el PG16 desechable del laboratorio.
Las corridas van en serie y escriben en bases `thermo_*_current` que el propio
runner crea y elimina.
