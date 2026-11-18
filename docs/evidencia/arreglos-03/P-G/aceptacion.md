# ARREGLOS 03 P-G — Aceptación: la cotización de MeLi conserva su evidencia (TN07)

- Worktree: `C:\Users\ehven\.ao\data\worktrees\goncloud-orbit\goncloud-orbit-30` (rama `ao/arreglos03-pc/pg`).
- SHA base (previo al cambio, = origin/master): `7d40b4f...` («ARREGLOS 03 P-F ... (#649)»).
- Sin migraciones nuevas (el arnés aplicó 88 migraciones, última `0091_arreglos03_sin_precio_cap.sql`).
- Arnés TN: PG16 desechable `127.0.0.1:55449` (contenedor `orbit-review-current-pg`, usuario `orbit`), roles compartidos ya presentes, bajo el candado `~/.ao/data/locks/orbit-tn` tomado y liberado en cada corrida.

## Qué cambió

- `app/meli/precio_mercado.py::_cotizacion_de_arbol_meli` gana `estimada_en`: la fecha de la
  fuente viaja original (nunca `now()` para rejuvenecer), el arbol entero queda en
  `Fees.detalle`, y sin fecha o sin desglose no hay exito (`fee_error:fee_sin_evidencia`,
  error explicito del puerto; `_persistir_cotizacion` escribe `error`, nunca un success sin
  evidencia — su guarda no se aflojó ni se tocó).
- `MercadoMeli.cotizar` pasa `resultado.fees_estimated_at` (la fecha que estampó el
  cotizador, sin otra llamada al reloj) y convierte un arbol ilegible en error explicito
  (`fee_error:fee_detalle_ilegible`) en vez de excepción.
- `MercadoMeli.cotizacion_guardada`: el SELECT de cache lee `fees_estimated_at` (la columna
  real confirmada) y pasa el valor ORIGINAL al constructor; las validaciones de precio,
  moneda y oferta de la fila no cambian.
- `Importe`, `Fees` y `puerto.Cotizacion` se mantienen (`Fees.detalle` ya existía con
  `()` por omisión); `Fees.impuesto_pendiente` y el tratamiento del arbol conservan el
  contrato vigente; la sospecha IVA/fee queda en 05. Ninguna habilitación: precios global
  shadow sigue dominando universos live.

## Rojo previo (SHA `7d40b4f`, cambios guardados con stash)

    CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs \
      docs/evidencia/arreglos-05/revision/test_accept_pricing.py -k "test_07"
    -> 2 failed, 5 deselected (corriente y persistida)
       FAILED test_07_corrida_meli_conserva_fecha_y_detalle[corriente]:
         assert fila and fila[0] is not None (la corrida persistia success SIN fecha)
       FAILED test_07_corrida_meli_conserva_fecha_y_detalle[persistida]:
         AssertionError: 07: el readback descarta la fecha valida
         (None == datetime(...22:30:11...) — cotizacion_guardada devolvia estimada_en=None
          y Fees.detalle=() aunque la fila guardada traia fecha y arbol)

## Fresca y cache (TN07, corrida completa contra PG desechable con MockTransport)

Ambas variantes recorren `correr -> insumos reales -> decidir -> cotizar_y_decidir ->
CotizadorMeli real -> INSERT PG`:

- **corriente**: la corrida cotiza en vivo; la fila persistida queda con
  `fees_estimated_at` (la que estampó el cotizador) y `fee_details` con el arbol
  (SaleFee/SaleFeeFixed/ListingFee); el readback la devuelve igual.
- **persistida**: se siembra la cotizacion valida (cotizador real) y la corrida la
  REUTILIZA sin otro GET (`assert not calls`); el readback conserva fecha y arbol.
- La asercion de cierre del caso: `cot.estimada_en == fila[0]`, `cot.fees.detalle` no
  vacio y `sum(final_fee) == cot.fees.total.valor`.

## Acepta (con el cambio, cabeza del PR)

P-G.1:

    CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs \
      docs/evidencia/arreglos-05/revision/test_accept_pricing.py -k "test_07"
    -> 2 passed, 5 deselected (EXIT=0; candado tomado y liberado)

P-G.2 (con los controles):

    CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs \
      docs/evidencia/arreglos-05/revision/test_accept_pricing.py -k "test_07 or test_08 or test_D2"
    -> 4 passed, 3 deselected (EXIT=0): test_07 corriente+persistida, test_08 (unidades
       reales: variaciones con precio, sin producto y reversa) y test_D2 (retencion de
       ayer sin liberar no abre hoy) verdes; cero skips

P-G.3 (vecinos):

    CI=1 PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs \
      tests/test_precio02_meli_cotizador.py tests/test_precio_corrida.py
    -> 151 passed (13 + 138), cero skips
    (y tests/test_precio02_mercado_meli.py + test_precio02_mercado.py: 53 passed)

    test -s docs/evidencia/arreglos-03/P-G/aceptacion.md  -> este archivo

Pruebas nuevas (rojo en `7d40b4f` por la firma vieja del constructor):

- `test_arreglos03_pg_fecha_original_y_desglose_viajan_completos`: fresca y cache por el
  mismo constructor; la fecha vieja (tres dias atras) se devuelve tal cual y el arbol
  completo en `Fees.detalle` con su suma igual al total.
- `test_arreglos03_pg_sin_fecha_o_suma_incoherente_no_hay_exito`: sin fecha o sin
  desglose -> `fee_error:fee_sin_evidencia` con `fees=None`; suma != total ->
  `fee_error:fee_no_concilia` como hoy.

## Notas

- Amazon no está en el alcance de P-G (owner MeLi); su constructor ya conservaba fecha y
  detalle en fresca.
- prueba-real del bloque (la misma del arnés, contra el despliegue): la corre el lead tras
  integrar; en práctica no hay escrituras reales y el arnés ya cubre el recorrido.

## DoD del plan: los seis casos TN en el mismo HEAD (esta cabeza)

    CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs \
      docs/evidencia/arreglos-05/revision/test_accept_ingest.py \
      -k "test_05 or test_06 or test_14 or test_S1"
    -> 4 passed, 2 deselected (EXIT=0; candado tomado y liberado)

Con los 2×`test_07` de arriba (misma cabeza): **6 de 6 casos TN verdes en el HEAD del PR**
(TN05 `test_05`, TN06 `test_06`, TN14 `test_14`, TNS1 `test_S1`, TN07 corriente y
persistida), controles `test_08` y `test_D2` verdes, cero skips.
