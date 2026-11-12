# Bloque Z - causa: un impulso pausado con su propio botón se queda atorado

## Se reproduce

Sí. La prueba con base `tests/test_impulso_io.py::test_arreglos01_z_impulso_pausado_por_dueno_cierra_limpio`
siembra un impulso vivo con el lote en `pausado` y `pausado_por='dueno'` (lo que deja el botón
«pausar impulso»), y métricas que no alcanzan ningún veredicto final: 19 días con ingesta, 5,000
impresiones, 40 clics, 0 pedidos y 300 MXN de gasto con tope de 1,000 MXN (umbral de gasto 950).

En el código de Q (commit 5396e61, antes de este bloque) la corrida del vigía devuelve:

```
VeredictoImpulso(estado='en_curso', final=False, accion='seguir')
```

y guarda una lectura NO final. El impulso sigue en `_SQL_VIVOS`, se vuelve a leer todos los días y
el producto queda bloqueado para un impulso nuevo. La prueba falla en
`tests/test_impulso_io.py:2180` con `estado: 'en_curso' != 'desarmado'`.

## Causa

1. El botón propio `POST /api/fabrica/impulso/{id}/pausar` llama a
   `PausadorFabrica(conn, actor="dueno").pausa(lote)` (`app/api_fabrica.py:741`). Eso pasa por
   `pausar_lote` -> `_cambia_estado_lote` (`tools/fabrica_campanas.py:1782-1784` y `:1718-1779`),
   que deja `fabrica_lote.estado='pausado'` y `pausado_por='dueno'`; no lo desarma.
2. Con las campañas detenidas el gasto deja de crecer. Si el impulso ya juntó al menos 2,000
   impresiones y 20 clics sin venta y su gasto quedó bajo `tope - 2 * diario`, `veredicto_impulso`
   cae en el último caso y devuelve `en_curso/seguir` para siempre
   (`app/impulso.py:197`; el mismo caso que la sección I-H2 cita como `app/impulso.py:187` en su
   versión).
3. El arreglo de Q solo cerraba el lote `desarmado`
   (`app/impulso_io.py:981` en 5396e61). Un lote `pausado` entraba en la rama de pausa y solo
   verificaba Amazon con `pausa_huerfanas` (`app/impulso_io.py:984-996`), pero con veredicto
   `en_curso` ni siquiera llegaba ahí: guardaba `hecho='nada'` y lectura no final.
4. `_SQL_VIVOS` (`app/impulso_io.py:870-877`) solo excluye impulsos con lectura final, y
   `_falla_si_hay_vivo` (`:710-720`) usa `_SQL_VIVO_PRODUCTO` (`:457`): mientras no haya lectura
   final, el impulso sigue vivo y bloquea el reimpulso del producto.

## Arreglo

En `_vigila_uno`, un lote en `pausado` cuyo `pausado_por` es `dueno` cierra con
`VEREDICTO_DESARMADO` (`desarmado`, final, `pausar`) cuando el veredicto de métricas no es
`graduar`, igual que Q hace con el lote `desarmado`. La rama de pausa existente verifica Amazon con
`pausa_huerfanas` y guarda `hecho='pausado'`; la lectura final saca al impulso de `_SQL_VIVOS` y el
producto puede volver a impulsarse. No se agregó un valor nuevo al vocabulario, así que no hay
migración.

Se conserva el caso del vigía (`pausado_por='impulso-vigia'`, que ya trae un veredicto final) y la
precedencia de `graduar` (si vendió, los goals pasan a live sin reencender, como en Q).

## Qué se probó

- Rojo por comportamiento en el código de Q: `estado='en_curso'` en la lectura, sin lectura final.
- Verde con el arreglo: una sola lectura `desarmado/final/pausado`, `pausa_huerfanas` llamado,
  `pausa()` no llamado y una segunda corrida del vigía ya no ve el impulso.
