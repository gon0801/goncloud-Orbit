# Panel M.3: margen meli/meli (resolver + CotizadorMeli + siembra + 0073)

## Composicion

- Muse A (workflow panel-m3-estimacion): 12 findings, VEREDICTO BLOQUEADA
  (1 bloqueante: `listing_fee` vs `listing_fee_amount` real).
- Muse B (workflow panel-m3-estimacion): 11 findings (F1-F11),
  VEREDICTO APROBADA con fixes.
- GLM 5.3 (opencode coding-cli): 10 findings (4 warnings + 6 nits),
  VEREDICTO APROBADA. Verifico por su cuenta: equivalencia min-pct==P*
  (algebra + numero), reversa == 0028 verbatim (diff), dinero OK.
- GLM 5.2 (opencode coding-cli): 9 findings (1 critico + 2 warnings +
  6 nits) + 10 respuestas profundas, VEREDICTO implicito APROBADA con
  fixes (el critico coincide con el bloqueante de A).

## Consenso (>=2 revisores) -> aplicado

1. [A-BLOQ + B-F3 + 5.3-2 + 5.2-F1] El parse leia `listing_fee`
   (inexistente; default 0 fail-open) y la sonda trae
   `listing_fee_amount` + `currency_id`. Aplicado: parse de
   `listing_fee_amount` exigido (ausente ->
   `fee_contrato_incompatible`), `currency_id` validada contra la
   oferta, mocks con la forma real + caso con listing != 0. Tests:
   `test_cotiza_con_fijo_y_publicacion`,
   `test_listing_fee_ausente_o_moneda_distinta_es_incompatible`.
   Mutantes m32, m33.
2. [A-ALTA + B-F1 + 5.3-3 + 5.2-F4] `fee_contrato_incompatible` /
   `fee_error` viajaban dinamicos por `error_code` hasta `motivos`,
   fuera de `MOTIVOS_ESTIMACION` (el motor degrada a
   `estimacion_motivo_desconocido`); el candado no escaneaba
   `meli/estimacion`. Aplicado: colapso a `fee_incompatible` en
   `_validar_fee_compatible` + modulo en el candado. Test:
   `test_fee_error_colapsa_a_vocabulario_cerrado`. Mutante m28.
3. [A-BAJA + B-F2 + 5.3-1 + 5.2-F2] Fail-closed despues de abrir el
   run -> fila huerfana. Aplicado: listings + validacion ANTES de
   `_SQL_ABRIR_RUN` + assert `count(ingest_run) == 0`. Extension
   ronda 2 (5.2-F2 segunda mitad): si el ctor lazy de Amazon falla a
   mitad del loop, se sella `ok=false` y se relanza (unico escape
   sin guard; lo demas ya es per-item). Tests: `..._sin_cliente...`,
   `test_ctor_amazon_falla_sella_run_sin_huerfano`. Mutantes m29, m35.
4. [A-BAJA + B-F6 + 5.2-Q1] `min()` sobre `contribucion_pct` a 4dp
   empata distinto que P* real. Aplicado MAS ALLA del pedido: manda
   por (C+L) exactos (`_carga_de`), empate por SKU. Test:
   `test_empate_cercano_manda_carga_exacta` (ambos 0.3658 a 4dp,
   manda el de mayor carga). Mutantes m14, m27.
5. [A-BAJA + 5.2-F6 + ronda-2] `currency: "MXN"` sin validar. Aplicado:
   igualdad contra `oferta.price_currency` + detalles con la moneda
   validada. Ronda 2: al probar USD aparecio mezcla silenciosa (la
   muestra tomaba MXN como USD) -> universo MXN-only: el resolver
   rechaza otra moneda con `universo_no_soportado`. Tests:
   `test_listing_fee_ausente_o_moneda_distinta...`,
   `test_oferta_usd_da_universo_no_soportado`. Mutantes m33, m38.
6. [A-BAJA + B-F7 + 5.3-8] Canonica de miembros sin fx/validacion del
   no-mandante. Justificado + docstring corregido (decia "meli no
   usa FX", falso): si la tasa mueve la cuenta cambia
   `contribucion` (mismo Decimal de la fila) y re-siembra; si no la
   mueve la fila visible es identica. Tests: `..._fx_ausente`,
   `test_miembro_sin_costo_ni_muestra...`. Mutantes m37, m39.
7. [B-F10 + 5.3-9] Sin test de reversa 0073 ni Amazon feliz.
   Aplicado: `test_0073_amazon_feliz_pasa` +
   `test_0073_reversa_restaura_0028` (conductual: tras la reversa,
   costo-de-miembro vuelve a rechazarse y Amazon pasa; mas assert
   de cuerpo sin `miembro_product_id`). Mutante m36 (sobre la
   reversa). Verificado ademas: solo 0028/0073 tocan la funcion.
8. [A-MEDIA + B-F1] Candado AST no incluia `meli/estimacion`.
   Aplicado (ver punto 2).
9. [A-BAJA + B-F11] Huecos: transicion negativa multi-corrida,
   `oferta_futura` via resolver, miembro con producto sin
   insumos, union multi-motivo. Aplicado: 4 tests + m31.

## Individuales validos -> aplicado

- [B-F4] Outcome-fee construido fuera del try per-item -> dentro.
  Test: `test_fee_malformado_se_cuenta_no_tumba`. Mutante m30.
- [B-F5] Huella negativa (IO) fuera del try -> dentro (verificado en
  codigo; sin test dedicado: inyectar fallo PG ahi no discrimina
  nada mas que el `except`).
- [B-F8] Supuesto `sale_fee_amount` incluye fijo sin evidencia M.0
  (solo fijo 0): citado en el codigo + pinneado por
  `test_cotiza_con_fijo_y_publicacion`. PENDIENTE para D.4:
  verificar contra categoria con fijo > 0 antes de operar P*.
- [B-F9] e2e fee-error hasta la fila: `test_fee_error_colapsa...`.
- [5.2-F5] `_decimal` cuantizaba >4dp en silencio -> rechaza como
  Amazon ("excede 4 decimales"). Test + mutante m34.
- [5.2-F7] `_contar_transicion_negativa` sin llamadores -> borrada.
- [5.2-F8] Comentario 0073 "INSERT/UPDATE" -> INSERT (el trigger es
  BEFORE INSERT, 0028:607).
- [5.3-4] FX por miembro sin prueba -> `test_miembro_usd_hacia_venta...`
  (fx_ausente honesto: el job solo publica (USD, MXN), sin inversa)
  + regla MXN-only (punto 5). La conversion por miembro queda
  inalcanzable por diseno del job, no por M.3.
- [5.3-10] Vista sin filas -> `costo_ausente` sin SKU: docstring en
  la siembra (difiere de Amazon a proposito).
- [A-0073] Trigger no exigia miembro en disponible -> exige
  (0073:52, defensa en profundidad).
- [A-partir] `SaleFee > 0` estricto: restriccion de diseno
  documentada en el docstring (espejo Amazon), no bug.

## Individuales rechazados (con razon)

- [A-moneda] Guard `moneda is None`: imposible por CHECK
  `meli_pub_obs_precio_con_moneda` (el INSERT revienta; probado).
  Guard removido, CHECK documentado.
- [5.2-F3] Dinero por float (`resp.json`): transparente en el dominio
  NUMERIC(14,4) — repr corto de Python, <=14 digitos siempre hacen
  round-trip en float64 — y `_decimal` valida finito/rango/4dp.
  Cambiar `_request` (compartido con reputacion) arriesga
  `Decimal + float` en ese modulo. 5.3-10 concuerda. Sin cambio.
- [5.3-5] Validar `gross_amount == sale_fee_amount`: semantica
  desconocida (M.0 solo fixed=0); rechazar inventaria
  incompatibilidades. El total es robusto en ambos casos. Sin cambio.
- [5.3-6] Variable 0 con fijo > 0 -> motivo enganoso: documentado
  (ver A-partir). Sin cambio de codigo.
- [5.3-7] Check `universo_de is None` "muerto": se queda (1 linea
  fail-closed, ahora con carga: valida moneda). Skew catalogo vs
  estimacion: mitigado por orden del cron (diseno-vs-trigger.md).
- [5.2-F9] Carrera huella (M.1 inserta entre lecturas): ruido
  acotado autorreparable; cron serie, sin concurrencia. Sin cambio.
- [5.2-Q8] "0073 no ejercita al sembrador": cubierto — la suite
  escenario corre sobre la cadena con 0073 y el trigger rechaza
  siembra mala; los tests 0073 cubren sus ramas.
- [m39-original] Mutante `motivos=[]` en costo: equivalente —
  `calcular` re-deriva `costo_ausente`/`fx_ausente` (backstop,
  venta.py:304/340). Redisenado a la unica via sin backstop
  (logistica via `mot_in`).

## Veredicto

APROBADA 4/4 tras fixes. La BLOQUEADA de A quedo resuelta en la
misma ronda (clave real + fail-closed + mocks + m32). DoD1-3 del
plan (disponible reproducible a mano, motivos por ausente, cuenta
del que manda) demostrados por tests + 39/39 mutantes.

## Adenda CodeRabbit (cierre seccion 5, PR #456)

- [CR-1 Major] Ingesta sin cliente MeLi tumbaba Amazon (raise
  global) contra su propio aviso "solo Amazon". APLICADO: listings
  meli a skip visible `meli_sin_cliente`, Amazon sigue, run con
  skip_reason. Jubila `test_ingesta_meli_sin_cliente_falla_cerrado`
  (+2 tests, mutantes m42/m43; retirados m19/m29 del raise viejo).
- [CR-2a Major] Resolver no frenaba obs con variantes sin mapear
  (el PUT las borraria). APLICADO: motivo `variantes_sin_mapear`
  (registrado en `MOTIVOS_ESTIMACION`) -> sin oferta, sin
  escenario, sin goal, sin escritura (+2 tests, mutantes m40/m41).
- Tercera afirmacion del risk summary ("multi-variante nunca
  completa") REFUTADA: sim X.4 listing 11 multi-miembro con
  escenario `disponible` sembrado (plan-simulacion.json).
