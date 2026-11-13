# Diferencia con el diseño (D.2): v_ledger_cargo y los cargos sin source_event_id

El diseño (`docs/evidencia/arreglos-02/DISENO.md`, sección `## D`, «Forma
elegida») trae el SQL de `v_ledger_cargo` con dos conjuntos que leen
`source_event_id` directo:

```sql
AND NOT (
      split_part(l.source_event_id, '|', 2) = 'fee_estimate'
      AND EXISTS (...
                  AND split_part(r.source_event_id, '|', 2) <> 'fee_estimate'))
```

La migración entregada los envuelve en `coalesce(..., '')`. Es la única
diferencia con el SQL exacto del diseño; la regla que el diseño declara no
cambia: **se ignora la estimada, y solo la estimada, cuando ya llegó la real
del mismo pedido.**

Por qué. En SQL de tres valores, `split_part(NULL, '|', 2) = 'fee_estimate'`
da `NULL`, no `FALSE`. Un cargo CON `order_id` pero SIN `source_event_id` es
un caso esperado del libro, no un accidente: `0001_initial.sql` le da su
propio índice de dedupe (`ledger_dedupe_con_orden`, «un re-sync de fees con
orden pero sin event id»). Con el texto del diseño, ese cargo caía en
`NULL AND EXISTS(real)` = `NULL`, y `NOT NULL` tampoco es verdadero: la fila
se descartaba en silencio de las cuatro vistas de margen siempre que existiera
una comisión real del mismo pedido, plataforma, kind y fee_type. También al
revés: una real sin `source_event_id` no contaba como real (`NULL <> ...` da
`NULL`) y su estimada quedaba duplicada. Con el `coalesce`, el `l` solo se
ignora cuando su id lo identifica como `fee_estimate`, y el `r` cuenta como
real salvo que su id diga `fee_estimate`.

Hallazgo F1 de la revisión del 2026-11-13 sobre `06c727f`, etiquetado
[comportamiento]. Reproducido en rojo con
`test_arreglos02_d_el_cargo_sin_source_event_id_no_se_descarta`
(`tests/test_arreglos02_cargos.py`): un fee de −10 sin `source_event_id` con
la real de O1 presente daba −250 en las cuatro vistas (el cargo desaparecía);
con el arreglo da −260. Medido en producción el mismo día, solo lectura:
cero cargos `fee/refund/withholding` con `source_event_id` NULL, así que las
cifras de referencia de `antes.txt` (45.50 y 28.99) y el `despues.sh` del D.4
no cambian.
