# B-D: revision en Amazon de los bloqueos aplicados

Bloque B-D de `plans/arreglos-04.md` (filas B-D.1, B-D.2 y B-D.3), con el diseno
`docs/evidencia/arreglos-04/DISENO-bloqueos.md`, seccion «Revision en Amazon de los
bloqueos aplicados». B-B y B-C ya estaban desplegados y verificados cuando corrio este
bloque: el codigo del contenedor `orbit-app-1` coincide byte a byte con
`app/bloqueo_dueno_efecto.py` y `app/apply_harvest.py` de esta rama.

## B-D.1 Inventario (solo lectura)

Comando:

```
bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-D/lee_amazon.py \
  > docs/evidencia/arreglos-04/B-D/antes.txt
```

Salida `0`. `lee_amazon.py` lee la base con `ORBIT_DSN_READ` en transaccion `read_only`
(`REPEATABLE READ`) y Amazon con `AdsClient`, el cliente que rechaza PUT, PATCH y DELETE.
La lista de negatives se pide con el lector estricto de B-C
(`bloqueo_dueno_efecto._lista_o` sobre `list_sellado`, que exige contenedor valido,
elementos validos, filtro `adGroupIdFilter` preservado en cada pagina y fin de
paginacion comprobado), contra los perfiles sellados de `perfiles_aceptados` (MX y US
exactos). Una lectura fallida se reintenta una vez y, si sigue fallando, sale como
`LECTURA_*` y queda fuera de cualquier reparacion.

Recibo final: `FIN|25|23|0`. Son los 48 veredictos `aprobado` cuyo ultimo paso durable
era `aplicado` (25 MX, 23 US) y cero `reversa_fallida`. No hubo lecturas fallidas,
duplicados ni plataformas fuera de MX/US.

**Resultado por estado.** Los 48 negativos que la pantalla da por bloqueados estan
`ENABLED` en Amazon. Cero `PAUSED`, cero `ARCHIVED`, cero `AUSENTE`, cero `LECTURA_*`.
Es decir, cero candidatos a reparacion: el bloque acepta cero confirmado.

`revisar.txt` deja la razon por veredicto (`amazon_enabled`, uno por linea, por ID).
Cinco de esas claves no tienen senal vigente `ajena` hoy y quedan igual de anotadas:

| plataforma | veredicto | ad group | negative |
| --- | --- | --- | --- |
| amazon_mx | `f50144f1-cd94-4ae0-832d-81116910eabf` | 339 | 232828929400885 |
| amazon_mx | `647b94c2-3d9a-4692-b213-3fe7471c60f3` | 297 | 142525138340630 |
| amazon_mx | `4d98a31a-e80c-4e45-a320-3abb844a1ea9` | 329 | 270062276549753 |
| amazon_us | `4d235dee-ab00-41d3-8e06-6bcbb281088e` | 4004 | 72739732222717 |
| amazon_us | `3d6aeb92-410f-48aa-a77f-c59af2898b00` | 4014 | 15661696303776 |

No se tocan: en Amazon el termino ya esta negado. La senal es asunto de JEV, no del
bloqueo, y queda listada para que David la revise.

## B-D.2 Reparacion

Preparacion y verificacion en laboratorio antes de ejecutar:
`PYTHONPATH=.:tests uv run --frozen --python 3.12 python docs/evidencia/arreglos-04/B-D/laboratorio.py`
sale `0` con seis casos (`laboratorio.txt`). Usa el ledger en memoria y el transporte
simulado del arnes de B-B con el codigo real de `quitar`/`aprobar` y sus precondiciones
de reparacion:

- A: origen `PAUSED` confirmado -> `revertido` y reemplazo `aplicado` ENABLED.
- A2: reanudar el mismo origen reusa el reemplazo, sin borrado ni POST nuevos.
- B: `ENABLED` entre inventario y accion -> `estado_cambio`, cero borrados.
- C: otro `ENABLED` del mismo termino -> `otro_enabled`, cero borrados.
- D: borrado sin relectura concluyente -> `pendiente`, un solo envio y cero POST.
- E: tabla de clasificacion del inventario (ENABLED, sin senal, otro ENABLED,
  `LECTURA_*`, estado desconocido, ARCHIVED, AUSENTE).

Ejecucion en produccion:

```
bash docs/evidencia/arreglos-04/B-D/repara.sh
```

`reparacion.txt`:

```
INVENTARIO|25|23|0
CANDIDATOS|0
FIN_REPARACION|0|48|0
```

Cero candidatos, cero reparados, cero pendientes y 48 omitidos documentados en
`revisar.txt` con su clave y su razon. El camino de reparacion quedo implementado y
probado, pero no se ejecuto ninguna mutacion en Amazon: no habia nada confirmado por
Amazon que reparar.

**Comprobacion de cero escrituras (independiente del driver).** Dos lecturas con
`lee_prod.sh` antes y despues del bloque:

- `select count(*), max(created_at) from bloqueo_veredicto` -> `48|2026-11-12 04:44:37`.
- `select count(*), max(created_at) from bloqueo_efecto` -> `96|2026-11-12 05:17:02`.
- `select count(*) from bloqueo_efecto where created_at::date = current_date` -> `0`.

Ninguna fila del ledger es de hoy: el bloque no creo veredictos ni efectos.

**Camino de reparacion (lo que haria con un candidato real).** Por cada candidato, en
serie por plataforma, grupo, termino y veredicto: concilia el ledger
(`_aprobado`, `_id_del_ledger`, `_ultimo`, `reconciliar` si hay intencion), comprueba
la senal vigente con `_vigente_ajena` y `_misma_clave`, relee el grupo completo y
compara estado, identidad (grupo, texto, `NEGATIVE_EXACT`) y otro `ENABLED`; solo
entonces llama `quitar` con `PrecondicionQuitar` (que vuelve a releer y comparar dentro
del candado) y, tras `revertido`, `aprobar` con `PrecondicionAprobar`, que exige senal,
pausa, interruptor, grupo y lista completa y no adopta un `ENABLED` ajeno. El id del
reemplazo sale del ledger del veredicto nuevo y se relee ENABLED en el mismo grupo.
Cualquier resultado incierto deja la clave `pendiente` sin reintentar la mutacion.

## B-D.3 Lectura final

```
bash docs/evidencia/arreglos-04/B-D/comprueba.sh > docs/evidencia/arreglos-04/B-D/despues.txt
```

Salida `0`. El comprobador es de solo lectura: vuelve a correr `lee_amazon.py` y saca el
ledger `aprobado`/`quitado` con `lee_prod.sh` (rol de lectura). Contrasta, por
plataforma y veredicto, el inventario inicial, el final, `reparacion.txt` y el ledger.
Cierra `COMPRUEBA|ok|mx=25|us=23|reversas=0`.

Comprueba, y falla si no se cumple:

- los recibos `FIN` de ambos inventarios cuadran con sus filas;
- cada veredicto inicial esta reparado o documentado con su clave exacta y su razon;
- cada origen reparado tiene `revertido`, un veredicto nuevo enlazado a la senal con
  efecto `aplicado` y su id `ENABLED` releido en Amazon (aunque ya no aparezca entre
  los vivos);
- una clave ilegible al inicio que ahora aparezca `PAUSED`, `ARCHIVED` o `AUSENTE`
  tiene su linea de relectura y se deja intacta;
- no hay vivos nuevos sin reparacion registrada, ni `reversa_fallida` nueva sin
  registro, ni intentos pendientes.

**El comprobador falla cuando debe.** Fixtures sinteticos, sin base ni red:
`comprueba-rojo.txt` retira un origen con el reemplazo solo en `intencion` y sale `1`
con `reemplazo sin aplicado` y `reemplazo no releido en Amazon`; `comprueba-verde.txt`
con el mismo escenario y el reemplazo `aplicado`/`ENABLED` sale `0`. Un retiro sin
reemplazo confirmado impide cerrar.

## Pruebas y verificacion

- `uv run --frozen ruff check docs/evidencia/arreglos-04/B-D/` y
  `ruff format --check`: limpio.
- Suite completa: `21 failed, 3326 passed, 2630 skipped`. Los mismos 21 fallan en
  `master` `3d8a35a2` (diff de la lista `FAILED` vacio entre las dos ramas). Incluyen
  cinco pruebas `test_arreglos04_bb_*` y `test_presupuesto_de_tamano_por_modulo`
  (`app/ledger.py` en 1000 lineas sin entrada en la allowlist), que ya fallan en master
  en un entorno sin Postgres local o por el presupuesto de tamano. B-D no cambia codigo
  de `app/`.
- Sin secretos: los archivos solo llevan ids de veredicto, `ad group`, ids de negative
  de Amazon y estados.

## Reversa

B-D no despliega codigo ni cambia el esquema. La reversa de una reparacion confirmada es
`quitar` de su **nuevo veredicto propio** por el camino normal de la pantalla, que
conserva el registro del anterior; hoy no hay ningun reemplazo creado por este bloque.

## Riesgo conocido y fuera de alcance

Un «aprobar» que adopta un `ENABLED` ajeno y un «quitar» posterior que lo archiva sigue
abierto para ARREGLOS 05 (`DISENO-bloqueos.md`, «Lo que no se resolvio»). B-D no entra
en ese camino: salta las claves con otro `ENABLED`, y con cero candidatos no adopto
ninguno.
