# Panel M.4: adaptador + cliente escritura MeLi

## Composicion

- Muse A (workflow panel-m4-escritura, enfasis escritura
  fail-closed): VEREDICTO APROBADA, sin bloqueantes, 3 observaciones
  no bloqueantes.
- Muse B (workflow panel-m4-escritura, enfasis mudanzas):
  VEREDICTO preservan comportamiento (byte-identico), 7 falsos
  negativos en los candados + 1 mutante vivo + supervivientes.

## Enfasis A -> aplicado

1. [A-OBS catalogo] JOIN contra catalogo: intencional INNER, no
   LEFT: M.1 escribe obs + listing + miembros en una sola
   transaccion (`_aplica_plan` atomico); una obs sin listing es
   corrupcion y no debe leerse. Aplicado: documentado en el
   docstring del modulo (dueno: confirmar criterio).
2. [A-OBS fee] `_detalle_fee_meli` no envolvia lo corrupto.
   Aplicado: envuelve `KeyError`/`InvalidOperation`/`ValueError`/
   `TypeError` en `ValueError` (`detalle de fee ...`), espejo de
   Amazon. Tests: `test_cotizacion_guardada_corrupta_da_value_error`
   (+ `test_cotizar_parte_fees_meli`). Mutante m33.
3. [A-OBS cambios.py] Prefijo PATCH cosmetico. No toca
   comportamiento; se deja como esta.

## Enfasis B -> aplicado

1. [B-VIVO] `import app.spapi.client` en el adaptador pasaba los
   candados (mutante vivo). Aplicado:
   `test_meli_no_importa_spapi` + candado de direccion; 105/105
   architecture en verde.
2. [B-FN x7] Candados endurecidos mas alla de lo literal: evasiones
   PUT (`requests.put`, `httpx.put`, `getattr`), alias
   (`from app.meli import write_client`), inventario cerrado de
   metodos de `ClienteMeli`, sonda no-`False` + `**dict` con
   distincion origen (`tools/precio_sonda.py`) vs reenvio puro
   (solo `meli/precio_mercado.py`), cobertura `tools/`.
3. [B-ALLOW] Allowlist 1398 de `estimacion_repository`: tamano
   correcto, con razon, y repara un HEAD rojo. Se queda.
4. [B-SUP] Supervivientes cubiertos con tests + mutantes:
   - ramas negativas de disponibilidad (stock 0 -> `no_disponible`):
     asserts en `test_historia_meli_disponibilidad_y_visitas`,
     mutante m34.
   - B1 `except Exception` + re-levanta `psycopg.Error`:
     `test_insumos_fallo_interno_no_tumba_lote`,
     `test_insumos_error_db_relevanta`, mutantes m35, m36.
   - `_direccion` sin movimiento -> `None`:
     `test_direccion_sin_movimiento_es_none`, mutante m37.
   - identidad de `oferta_para_cotizar`:
     `test_oferta_para_cotizar_devuelve_campos`, mutante m38.

## Brechas residuales aceptadas (fuera de alcance)

`__import__`/`importlib`, falso positivo en `TYPE_CHECKING`, sonda
por `**variable`, debilitamiento del guard de `ClienteMeli`.

## Adenda CodeRabbit (CR-2b, cierre seccion 5, PR #456)

`escribir` mandaba solo los miembros sin verificar el vivo: una
edicion intradia (variacion creada tras el catalogo) se borraria en
el primer PUT. APLICADO (fondo del motivo `variantes_sin_mapear`
del resolver): `escribir` lee el item vivo y frena con `ValueError`
si trae extras (cero PUT; la corrida lo cuenta B1). Pre-chequeo de
sello por `MeliWriteClient.exigir_sello()` inyectado (cero red sin
sello; sin importar el modulo por el candado del importador unico;
sin parametro `sonda` por el candado de reenvio). +2 tests,
mutantes m39/m40; m2/m3 re-anclados.
