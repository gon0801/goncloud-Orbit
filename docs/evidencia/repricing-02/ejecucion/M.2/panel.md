# Panel M.2: ledger MeLi (AtribuidorMeli + meli_* en sync_ledger + fee→meli_envio)

## Composicion

- Muse A (workflow panel-m2-ledger): 9 findings.
- GLM 5.3 (opencode coding-cli): 8 findings.
- GLM 5.2 (opencode coding-cli): 10 findings.
- GLM 4.7 (reintento repo-scoped; el primero murio por permiso de
  directorio externo): 7 findings, 0 criticos.

## Consenso (>=2 revisores) -> aplicado

1. [Muse A1 + GLM5.3-1 + GLM5.2-4] Posicion fija UNICA del fee en `productos`:
   el fee va siempre al hijo menor (min, excluyendo parent), nunca a
   min-max-null. Sin esto, "fee = min(...)" de costo_de_orden no es la misma
   identidad que el mapa viejo podria producir. Aplicado: en sync_ledger,
   cuando `productos is None` (plan actual, sin None-capaz) se cuenta hueco
   `SIN MAPA DE PRODUCTOS` y ev.producto_id=None; el mapa eventual debe seguir
   la regla unica del ADR meli. Test: test_plan_sin_mapa_productos_cuenta_hueco_fuerte.
2. [Muse A3 + GLM5.2-2] El fee dentro del mapa: una sola fuente. `fee`->"meli_envio"
   en clasificar_cargo solo cuando platform=="meli" (antes: incondicional,
   contaminaba Amazon). Aplicado con parametro `platform` (default None =
   comportamiento anterior). Tests: test_identidad_fee_es_meli / amazon.
3. [Muse A2 + GLM5.2-1] Falta contraejemplo sale-vs-refund en periodo actual
   en pruebas. Aplicado: test_refund_meli_mismo_periodo_compensa_venta.
4. [GLM5.3-2 + GLM5.2-3] item_id None con sku: reorden guards (sku primero,
   item solo si str no vacio). Aplicado: `con_item` calculado, item vacio cae
   a sku/listings sin sorpresas. Test: test_item_id_none_con_sku_mapea_sku.
5. [GLM5.3-5 + GLM5.2-8] costos desconocidos por hijo: producto sin hijo y
   qty NULL siguen con producto y hueco CONTABLE (no silencioso). Aplicado:
   huecos "venta meli sin producto" / "sin cantidad" + contadores meli_* en
   ResultadoSync + linea CLI. Tests: test_venta_meli_mapeada_sin_cantidad_cuenta_hueco,
   test_venta_meli_sin_sku_cuenta_hueco, test_main_imprime_cobertura_meli.
6. [Muse A5 + GLM5.3-6] close()/guards antes de _db y error real en
   _db_falla: aplicado (close, _db solo tras mapeo, nivel error con sku/order).

## Individuales validos -> aplicado

- [A4] Reusar ev.raw en lugar de reparsear JSON en costo: aplicado.
- [A6] AtribuidorMeli._propia hereda y reusa el helper base: aplicado
  (antes duplicaba SQL inline).
- [A8] Comentario D1/dispatch stale actualizado.
- [5.2-6] raw nunca dict (json vs jsonb) confirmado con SELECT real en
  snapshot: es str; el test usa str. Sin cambio de codigo.
- [5.2-7] Evitar DDOS de conexiones: el test behavioral crea su propio
  AtribuidorMeli (sin singleton) y lo cierra.

## Individuales rechazados (con razon)

- [A7] meli en _PLATAFORMA_ORIGEN podria colisionar "meli_next_90d": no hay
  tal tabla en el esquema (grep 0001) -> rechazado, documentado en ADR.
- [5.3-3] Exigir productos-map None-capaz ya mismo: es M.4/D.4, no M.2;
  el hueco fuerte ya falla ruidoso -> diferido, citado en el test.
- [5.3-7/8, 5.2-9/10] Nitpicks de estilo (nombres en ingles, f-strings):
  repo usa espanol sin acentos y f-strings ya -> rechazado.
- [A9] Re-SELECT despues de UPSERT para kind-resolved: el diseño usa el
  kind del evento, no re-lectura; re-SELECT romperia append-only (regla 5)
  -> rechazado.

## GLM 4.7b (llego ultimo, contra el arbol casi final)

Sin criticos. Disposicion:

1. [warning] Evidencia incompleta: faltaba la retencion medida (DoD M.2)
   y artefactos de los contratos "medido M.2". APLICADO: `medicion.md`
   con ISR 0.025 neto (medido por orden; el pooled 0.0206 es bruto) +
   control Amazon, distribucion campo2 (solo MeLi emite `fee`) y forma
   real del `raw_payload`.
2. [warning] `test_main` dejaba el singleton con conexion a DB borrada.
   APLICADO: `try/finally: atribuidor_meli.close()`.
3. [warning] `_db()`/`close()` duplicados entre atribuidores. DIFERIDO:
   subir el helper a `ledger_atribucion.py` toca el camino de Amazon;
   no es bug de datos. Deuda anotada, no bloquea M.2.
4. [nit] Docstring punto 5 viejo. APLICADO (una linea).
5. [nit] `productos` opcional. APLICADO a medias: requerido en
   `plan_eventos` (seam de produccion); `mapear_destino` conserva el
   default (seam unitario, igual que `listings`).
6. [nit] `EventoLedger.raw` enganoso. APLICADO: renombrado a `payload`.
7. [nit] `platform` opcional en `clasificar_cargo`. APLICADO: requerido
   (sin default); `None` explicito sigue dando `otros` pineado.

## Veredicto

APROBADA 4/4. 22/22 verde, 22/22 mutantes muertos post-fixes,
`medicion.md` cierra el DoD ("la evidencia trae la retencion medida").
