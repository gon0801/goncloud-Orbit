# G. Motor de precios (repricing): resultado

Copia revisada: `scratchpad/orbit07-wt` en `3c841f4`. Sondas en
`scratchpad/orbit07-review/sondas/G_*.py` (salida en el `.txt` del mismo nombre).
Comando común: `cd <orbit07-wt> && PYTHONPATH=$PWD /Users/dn/dev/goncloud-Orbit/.venv/bin/python <sonda>`.
Config de producción usada en las sondas: la de `migrations/0054_precio02_base.sql:1291-1296`
y `docs/evidencia/repricing-01/D.0/salidas/20260919-150630/05-settings.json`
(escalón 0.10, cooldown 7 días, fusible 0.50 con mínimo 10, salto de insumo 0.05, fracción 0.30, corte 5).

## Hallazgos confirmados

### H1. El fusible y el freno por insumo sistémico se sueltan solos al día siguiente, sin que el dueño apriete «Soltar»
- Severidad: ALTO
- Frente: dinero
- Dónde: `app/precio/corrida.py:953-960` (`_SQL_MEDIR_PREVIA` toma la última decisión anterior SIN excluir las retenidas), `app/precio/corrida.py:1071-1097` (arma `Anterior` y los pares del insumo contra esa decisión), `app/precio/compuerta.py:127-141` (`es_nueva`: si la anterior fue `subir`/`bajar` con el mismo `goal_id`, no es nueva), `app/precio/corrida.py:1157` (retener solo inserta `precio_retencion`, no `precio_cambio`), `app/precio/fuentes.py:372-380` (el cooldown sale solo de `precio_cambio`).
- Escenario: Día 1: un costo se carga 20 % arriba en 91 de 158 productos FBA (o el dueño sube goals en bloque). La compuerta retiene todo (`insumo_sistemico` o `movimiento_masivo`) y el aviso dice «no se aplicó nada». Nadie suelta. Día 2: como lo retenido no dejó `precio_cambio`, no hay cooldown y las 91 vuelven a salir `subir` (o `bajar`) con el mismo goal. Su «decisión anterior» es la `subir` retenida de ayer, así que `es_nueva` da `False` → 0 intenciones nuevas. El costo malo ya está en la decisión de ayer, así que tampoco hay salto de insumo. Veredicto: `Abierta`, y el motor aplica las 91 en live, sin aviso de compuerta. Lo esperado: lo retenido sigue retenido hasta que el dueño lo suelte. Lo dicen el diseño (`docs/superpowers/specs/2026-10-08-repricing-02-design.md:118-133`, «el dueño aprieta Soltar»), `docs/CONTEXTO.md:290-292` (cambios masivos de precio >50 productos requieren confirmación) y el propio docstring de `app/precio/liberaciones.py:64-66`, que solo concede «deja de contar» a lo SOLTADO.
- Alcance: se dispara con el cron diario (13:10 + repasos) y la config actual, en todo universo en live (MX FBA/FBM, US, MeLi). Basta un evento masivo: insumo malo o ajuste de goals en bloque. No está declarado como residual (G8, en el diseño:653 y :536, cubre solo la «segunda ola» de cambios YA aplicados). Ninguna prueba cubre el día 2 sin soltar: `tests/test_precio_corrida.py:4392` solo prueba lo soltado.
- Evidencia: sonda ejecutada, `sondas/G_fusible_dia2.py`:
  ```
  DIA 1: Retenida(causa='insumo_sistemico', medidas=158, nuevas=91, insumo='costo', detalle='costo salto en 91 de 158 comparables (salto 0.05, frac 0.30)')
  DIA 2: Abierta(medidas=158, nuevas=0)
  es_nueva(Anterior('subir', None, 7), goal_id=7) = False
  ```
  Leí línea por línea el camino del día 2: `_decidir_pendientes` → `cambios_previos` sin fila → sin cooldown → `subir` → `_medir_compuerta` → `_aplicar_pendientes` sin retención → `cambios.aplicar`.
- Cómo reproducir: `PYTHONPATH=$PWD .venv/bin/python sondas/G_fusible_dia2.py`. Con base (no ejecutado): sembrar 12 unidades FBA con el mismo goal, poner decisiones `subir` de ayer y dejar que `_medir_compuerta` + `_aplicar_pendientes` las retengan; luego insertar decisiones `subir` de hoy con el mismo `goal_id`, sin liberar, y llamar `_medir_compuerta(conn, mercado, hoy, motor, corrida)`. Da `Abierta` y `_aplicar_pendientes` escribe.

### H2. Mercado Libre: el motor nunca escribe precio en publicaciones con variantes (33 de 45 goals live), y cada intento cuenta como error de escritura
- Severidad: MEDIO
- Frente: motor (el comportamiento pedido no ocurre; falla cerrada, sin pérdida de dinero)
- Dónde: `app/precio/cambios.py:173-184` (`_unidad_de_listing` arma un solo `Miembro(product_id, sku, None)`: variante siempre `None`), usada por `aplicar` en `:368` y por la reversa en `:486`; `app/meli/precio_mercado.py:605-611` (variaciones vivas menos variantes de la unidad = todas «huérfanas» → `ValueError`, sin PUT). En cambio, `tools/precio_sonda.py:96-111` sí carga las variantes de `listing_miembro`: por eso la sonda X.4 pasó y el camino real no.
- Escenario: un goal live de MeLi sobre una publicación con variaciones decide `subir` o `bajar`. Si `precio_vivo == p_actual`, `aplicar` hace INSERT+COMMIT de la fila `pendiente`. Luego `escribir` levanta `ValueError("variaciones vivas sin mapear ... sin PUT")`, `_escribir_y_sellar` (`cambios.py:286-293`) sella `error`, y la corrida cuenta un error para el corte (`corrida.py:1294-1301`). Como el readback ve el precio sin mover, el cambio queda `sin_efecto` (`fuentes.py:397-404`), no consume cooldown y se reintenta cada día. Al tercer día seguido entra `frenado(api_error)` (`corrida.py:184-221`) hasta que haya un goal nuevo. Si en cambio el vivo no coincide, salta por `precio_vivo_distinto`. En los dos casos nunca escribe. Efectos laterales:
  (a) cinco publicaciones con variantes seguidas por `listing_id` abren el corte, y el resto de lo pendiente de esa corrida queda retenido `corte_errores`, incluidas publicaciones de un solo producto. Se reintentan 2 h después, pero lo que no alcance a las 23:10 se pierde ese día («nunca una decisión de ayer»). Depende de cómo estén repartidos los ids: no medido.
  (b) `error_code` puede nombrar la ruta de OTRA publicación, porque `_ruta_error` se fija después del chequeo (`precio_mercado.py:612`) y `cambios.py:287` lee el valor que quedó de la escritura anterior.
  Lo esperado: el diseño acepta que «la publicación con variantes se gobierne por su variante de menor margen» (`spec:542-543`), o sea, que se escriba.
- Alcance: cron de MeLi (13:10 + repasos), `meli/meli` en live. Según `docs/CHAT-CONTEXT.md:18`, 33 de los 45 goals live de MeLi son publicaciones con variantes (dato del 2026-10-17; el actual no lo medí).
- Evidencia: sonda ejecutada, `sondas/G_meli_variantes.py` (lector y escritor falsos, sin red):
  ```
  unidad de aplicar: (Miembro(product_id=55, seller_sku='SKU-A', variante=None),)
  sin variaciones -> PUTs: [('MLM123', [], Decimal('550.00'))]
  con variaciones (unidad de aplicar) -> ValueError: variaciones vivas sin mapear (MLM123): 111,222; sin PUT
  PUTs: []
  con variaciones (unidad con miembros) -> PUTs: [('MLM123', ['111', '222'], Decimal('550.00'))]
  tras 5 items con variaciones seguidos: corte.abierto = True
  ```
- Cómo reproducir: `PYTHONPATH=$PWD .venv/bin/python sondas/G_meli_variantes.py`. En producción, solo lectura (no ejecutado): `SELECT error_code, count(*) FROM precio_cambio WHERE platform='meli' AND estado='error' GROUP BY 1;` y `SELECT motivo, count(*) FROM precio_decision WHERE platform='meli' AND resultado='frenado' GROUP BY 1;`.

### H3. Un movimiento de precio legítimo dispara un falso «insumo sistémico» al día siguiente y retiene todo el universo con una causa falsa
- Severidad: MEDIO
- Frente: motor
- Dónde: `app/precio/corrida.py:986-988` (vigila `retencion` = `r_valor` y `fee_variable`); `app/precio/corrida.py:540-541` (`r_valor` es el ISR en dinero = tasa × ingreso, proporcional al precio); `migrations/0054_precio02_base.sql:171-173` (`fee_variable` = «fee que escala con el precio»); `app/precio/compuerta.py:144-155` (salto relativo > 5 %).
- Escenario: ayer se aplicó +10 % (el escalón de producción) a 60 de 158 FBA. Eso pasa el fusible (60 < 79), o pasa porque el dueño soltó un fusible: es justo el ejemplo del diseño, 91 de 158. Hoy esas 60 están en `mantener(cooldown)` pero se miden igual (tienen `m_actual`). Su ISR y su fee variable «saltaron» 10 % contra la decisión de ayer, aunque ningún insumo cambió: lo que cambió fue el precio que el propio motor puso. 60 > 0.30 × 158 → `Retenida(insumo_sistemico, insumo='retencion')`. Todo el universo queda retenido ese día y el aviso culpa a la retención. Se repite el día siguiente a cada ola de 7 días mientras dure el ajuste. Lo esperado: un insumo vigilado debe ser algo que el motor no controla (costo, envío, tasa de cambio, fee fijo), no un importe que depende del precio.
- Alcance: cron diario con la config actual, en todo universo donde una ola aplicada mueva ≥ 30 % (≥ 10 productos). No pierde dinero (retiene de más), pero da avisos falsos que acostumbran al dueño a soltar sin mirar. Con H1, la retención falsa se abre sola al otro día.
- Evidencia: sonda ejecutada, `sondas/G_deriva_falsa.py`:
  ```
  Deriva(canal='fba', insumo='costo', comparables=158, con_salto=0)
  Deriva(canal='fba', insumo='retencion', comparables=158, con_salto=60)
  Deriva(canal='fba', insumo='fee_variable', comparables=158, con_salto=60)
  veredicto del dia siguiente: Retenida(causa='insumo_sistemico', medidas=158, nuevas=15, insumo='retencion', detalle='retencion salto en 60 de 158 comparables (salto 0.05, frac 0.30)')
  ```
- Cómo reproducir: `PYTHONPATH=$PWD .venv/bin/python sondas/G_deriva_falsa.py`. En producción, solo lectura (no ejecutado): `SELECT c.fecha, m.canal, m.causa, m.insumo, m.detalle FROM precio_compuerta_medicion m JOIN precio_corrida c ON c.id=m.corrida_id WHERE m.insumo IN ('retencion','fee_variable') ORDER BY 1 DESC;`.

## Sospechas sin confirmar
1. **Umbral de envío gratis de Mercado Libre (299 MXN) sin modelar.** No hay manejo de ese umbral en `app/meli`, `app/precio` ni `app/envio_muestras.py` (lo busqué). Si una publicación de menos de 299 sube y lo cruza, el vendedor empieza a pagar el envío, pero la L del escenario sale de muestras de 90 días y no cambia: el margen real cae mientras el motor cree que está en el goal. Para confirmar: saber si la cuenta paga envío bajo 299 y comparar la L de las muestras de productos cerca de 299 antes y después de cruzarlo.
2. **Promociones activas de Mercado Libre.** No hay lectura de `original_price` ni de promociones. Si el precio observado es el de promoción, el motor decide sobre él y manda PUT del precio base. No sé si Mercado Libre rechaza el cambio o termina la promoción. Para confirmar: documentación de MeLi y una lectura de `meli_publicacion_observation` de un item con promoción activa.
3. **La racha de la señal de ventas no mira la fecha.** `racha_por_lid` toma la última decisión anterior sin importar de qué día es (`app/spapi/precio_mercado.py:381-390`, `app/meli/precio_mercado.py:295-304`). Días sin fila (global apagado, goal cerrado) no cortan la racha, así que `perdiendo` (que lleva a `bajar`) puede salir con menos de 3 días seguidos de señal. Para confirmar: una consulta en producción de huecos de días en `precio_decision` por listing con racha mayor que 0.

## Zonas revisadas sin hallazgos (una línea cada una, con qué comprobaste)
- **Precio absurdo (reglas/objetivo):** sonda `G_precio_absurdo.py`. Un costo 100× alto o un goal imposible dan `goal_inalcanzable(precio_mayor_al_doble)` antes de cotizar. El denominador ≤ 0 da `margen_imposible`. Todo va en Decimal a 2 decimales (`techo/piso_centavo`) y solo MXN/USD. Un costo 100× bajo con `perdiendo` baja solo 10 % por cambio, con 7 días entre cambios (500 → 450): el único piso es el margen del goal y no hay tope histórico. Es un dato malo que viene de antes del motor (el costo se valida positivo y fresco en `estimacion_insumos.py:502-530`).
- **Piso y techo:** goal acotado a 0.10–0.60 por config y por CHECK de base (`0039_precio.sql:74`). El escalón de 10 % se aplica en `reglas.py:418-419` y `:509-510`. Hay un solo camino que escribe `precio_cambio` real: corrida → `cambios.aplicar` (busqué con `rg INSERT INTO precio_cambio`).
- **Escalera cada 2 h:** una decisión por listing y día (`corrida.py:797-804` + UNIQUE + trigger UTC en `0039:303`). Los repasos solo aplican lo pendiente. `aplicar` compara el vivo contra `p_actual` y exige que no haya cambio abierto (`cambios.py:372-400`). El cooldown de 7 días cuenta pendientes, enviados y no confirmados reales.
- **Candado:** el claim atómico con TTL y el advisory de sesión por plataforma (`corrida.py:156-162`) protegen aunque venza el TTL. La reversa comparte el candado (`cambios.py:648-655`).
- **Fuentes atrasadas:** el motor exige escenario de hoy y oferta de ≤ 6 h (`estimacion_reader.py:123-132`), tasa de cambio con antigüedad máxima, ledger cubierto hasta hace ≤ 3 días o `ledger_hueco` (`ventas.py:160-162`), precio observado hoy y divergencia ≤ 1 %. Un insumo faltante nunca se vuelve cero: sale `no_evaluado`. Conocido y aceptado: una decisión al día hace que una estimación tardía pierda el día de esa plataforma (`plans/claridad-01.md:99`).
- **Escritura:** orden INSERT+COMMIT → PATCH → sello. Las huérfanas se resuelven contra el vivo. Amazon solo cuenta como aceptado con 2xx + submissionId + ACCEPTED (`precio_write.py:200-212`). Mercado Libre: 401 sin refresh y un solo reintento en 429 (PUT idempotente). Un ASIN con varios SKU propios se rechaza antes del motor (`identidad_ambigua`, `estimacion_insumos.py:345-368`). El escritor de Amazon está atado a su plataforma (`precio_mercado.py:279-283`). La documentación de Amazon vía Context7 confirma que `replace` en `purchasable_offer` no borra lo omitido: se conservan el precio mínimo y máximo de Seller Central.
- **Shadow contra live:** shadow nunca llama a `aplicar` (`corrida.py:1260-1281`). `aplicar` rechaza todo modo que no sea live (`cambios.py:361-365`). Modo y goal se releen antes de cada escritura, y con config ilegible no escribe (`corrida.py:1126-1144`, `:1244-1255`).
- **Reversa:** vuelve a `precio_antes`, que es el vivo leído al aplicar (`cambios.py:409-427`, `:553`). Exige que el vivo sea igual a `precio_despues`, pide huella y go, y se niega si el universo está en live.
- **API de goals:** token antes de abrir conexión, porcentaje finito ≥ 1 con 2 decimales, banda no confirmable para valores a mano (`siembra.py:500`), CHECK de base, go literal para live, tope de 500 y huella (`api_precios_write.py`, `goals_write.py:112-165`).
