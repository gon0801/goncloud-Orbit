# D.2 y D.3: caminos nuevos y como se decide cada uno

Base: `7ef47542f5ab421359f049583b1bc7db95662079` (`master` con BASE, A, B y C).
Rama `arreglos-05/d`. Contrato: seccion «D-E. Escritor unico y practicas
vencidas» de [DISENO.md](../DISENO.md), solo la parte D, y «Integracion con
TEMPORADA y migraciones».

## Dos formas y sintesis

**Forma A, transporte con operaciones tipadas y el dueno traduce.** `app/ads/write.py` gana
operaciones nuevas (`crear_campana`, `crear_ad_group`, `crear_target_asin`,
`crear_keyword`, `cambiar_estado_campana`) con argumentos nombrados y montos
`Decimal`; cada dueno traduce su payload semantico a esa llamada.

**Forma B, transporte que recibe el cuerpo ya armado.** `app/ads/write.py`
gana un `crear(recurso, cuerpo)` con enum cerrado de recurso y validacion de
llaves por recurso; el dueno conserva el objeto exacto que ya guarda en su
ledger y lo manda tal cual.

Screening (reglas de diseno del repo, `AGENTS.md` 4 y 5):

- A duplica la construccion del cuerpo: el payload del ledger de la fabrica y
  el objeto que arma el cliente. Se controla con una prueba de igualdad
  cuerpo/ledger, pero la deriva queda posible.
- B deja el cuerpo en un solo lugar (el que se guarda es el que viaja) y
  valida el contrato con llaves cerradas; a cambio, el cliente aceptaria un
  `dict` y perderia la verificacion de moneda `Decimal` en la frontera.
- Ambas dejan igual de sellados perfil, metodo y ruta: la allowlist
  `MUTATION_REQUEST_TYPES`, el scope de la instancia y el default-deny no
  cambian.

Sintesis elegida: **A con la igualdad cuerpo/ledger probada**. El cliente es
la capa HTTP y ya construye cuerpos tipados en sus trece mutaciones; meter un
`dict` reenviado rompe esa unica forma y abre la puerta a un cuerpo
semi-arbitrario, que es justo lo que D.2 prohibe. La deriva se cierra con
`test_arreglos05_d_fabrica_cuerpo_igual_al_ledger`, que compara, paso por
paso, el JSON enviado contra el `request_payload` guardado antes del HTTP.
Los montos viajan como `Decimal` hasta el cliente, que los cuantiza a dos
decimales una sola vez (`_bid_wire`/`monto`).

## Operaciones nuevas del cliente protegido

Todas en `app/ads/write.py`, subclase de `AdsClient`, con el scope sellado en
la instancia y sin parametro de perfil ni de plataforma:

| Operacion | Metodo y ruta | Vendor sellado | Contenedor | Valida antes del HTTP |
|---|---|---|---|---|
| `crear_campana` | `POST /sp/campaigns` | `application/vnd.spcampaign.v3+json` | `campaigns` | nombre no vacio, `targetingType` en `{MANUAL, AUTO}`, `state` en `{ENABLED, PAUSED, PROPOSED}`, presupuesto `Decimal > 0`, moneda de la plataforma, fecha ISO |
| `crear_ad_group` | `POST /sp/adGroups` | `application/vnd.spadgroup.v3+json` | `adGroups` | nombre no vacio, `state` en el enum, `defaultBid` `Decimal > 0`, moneda |
| `crear_product_ad` (ya existia) | `POST /sp/productAds` | `application/vnd.spproductad.v3+json` | `productAds` | `state` en `{ENABLED, PAUSED, PROPOSED}` |
| `crear_keyword` (renombra `crear_keyword_exacta`) | `POST /sp/keywords` | `application/vnd.spkeyword.v3+json` | `keywords` | `matchType` en `{EXACT, PHRASE, BROAD}`, `state` en `{ENABLED, PAUSED, PROPOSED}`, bid `Decimal`, moneda |
| `crear_target_asin` | `POST /sp/targets` | `application/vnd.sptargetingclause.v3+json` | `targetingClauses` | `expressionType` `MANUAL`, `state` en el enum, bid `Decimal > 0`, moneda, ASIN no vacio |
| `crear_negative_exacto` (ya existia) | `POST /sp/negativeKeywords` | `application/vnd.spnegativekeyword.v3+json` | `negativeKeywords` | termino no vacio |
| `cambiar_estado_campana` | `PUT /sp/campaigns` | `application/vnd.spcampaign.v3+json` | `campaigns` | `state` en `{ENABLED, PAUSED}`; nunca `ARCHIVED` ni `PROPOSED` |

Allowlist: se suman `("POST", "/sp/campaigns")`, `("POST", "/sp/adGroups")` y
`("POST", "/sp/targets")`, con el vendor de las sondas ya selladas (2026-09-09,
creacion real de la fabrica; 2026-08-26, probe 2.5). El contenedor de
`/sp/adGroups` se suma a `MUTATION_CONTAINERS`. `crear_ad_group` lleva
`campaignId` en el cuerpo (el ad group cuelga de su campana). Nada mas: el
cliente de lectura no cambia y no aparece un `request` generico.

El mapa `fabrica_plan.VENDOR_POR_PATH` se conserva como vocabulario del plan, y
`test_arreglos05_d_vendor_de_la_fabrica_y_del_cliente_es_el_mismo` pinea que no
diverja del cliente (regla 2: un numero, una fuente).

## Ack canonico y juicio por item

`app/ads/sp_wire.py` gana dos funciones y sigue siendo el unico parser:

- `ack_de(respuesta)` devuelve `{"status", "cuerpo", "texto"}` con el cuerpo
  JSON tal cual (lista, `null` o dict) y el texto saneado. Es el ack que
  guardan los tres ledgers (`fabrica_lote_paso`, `keyword_archivo_manual`,
  `keyword_reposicion_intento`).
- `ack_de_error(exc)` convierte un `AdsApiError` del cliente en ese mismo ack:
  `status` del error o `"excepcion"` si no hubo respuesta, `cuerpo` con el
  snippet saneado, `texto` igual. Un error de red o un 5xx quedan inciertos;
  un 4xx queda como rechazo deterministico con su status.

El juicio por item NO se mueve: cada dueno decide con `sp_wire`
(`errores_de_ack` para dict, `errores_put` para respuesta, `id_de_ack` para el
id). El juicio del ledger de la fabrica (`ack_ok`, `errores_207`, `id_creado`)
se movio de `fabrica_plan` a `fabrica_motor` y ahora delega en `sp_wire`: el
plan queda PURO (candado `test_fabrica_plan_es_puro`) y una sola gramatica del
207 juzga los acks. Un 207 con un error por item nunca sella `applied`.

## Camino por camino

1. **Fabrica, creacion (campaign, ad group, product ad, keyword, target,
   negative).** `mutar` valida la ceremonia (`--acepto-mutacion-real`, `--go`,
   `--huella`, `--esperado`), construye UN `AdsWriteClient` con
   `modo_confirmado="live"` **despues** de esa autorizacion, y arma `_Ctx` con
   ese escritor y el lector existente. `_ejecuta_paso` inserta `fabrica_lote_paso`
   (`planeado`, commit) antes del HTTP, llama la operacion especifica, guarda
   ID+ack con commit antes del LIST, relee y sella `applied`/`failed`. Reversa:
   `--desarmar` (pausa) y `_revierte_parcial` en el camino impulso.
2. **Fabrica, estado de campana (activar, pausar, reactivar, rollback).**
   `_aplica_estado_campana_con_ledger` y `_pausa_una` ya insertan el paso antes
   del PUT; `_put_estado_campana` pasa a
   `cambiar_estado_campana(external, estado)` del escritor. `reactiva_lote`
   (graduacion de la fabrica) hereda el mismo camino. `_revierte_parcial` no
   tiene fila propia de ledger: es la compensacion de un lote failed y queda en
   `fabrica_lote.detalle`; ahora si pasa por el guard del cliente. Limite
   anotado, no cambio de protocolo.
3. **Fabrica, consumidores (API, impulso, vigia).** `api_fabrica` e
   `impulso_preview.PausadorFabrica` dejan de armar `httpx.Client` y
   `_token_lwa`: piden el escritor por plataforma y siguen insertando su paso
   antes de mutar. `impulso_vigia` no cambia: usa el pausador y `preparar_escritor`.
4. **Archivo manual de inertes.** `_archivar` inserta `keyword_archivo_manual`
   (`planeado`) antes del DELETE, llama `borrar_keyword` y sella con el
   readback `ARCHIVED`. Reversa: `--reponer`.
5. **Reposicion (bloque C) y su reversa.** El protocolo de C no cambia: `_reponer`
   y `_revertir` solo cambian de donde sale el callable `enviar`/`eliminar`.
   Ahora traducen el payload durable de C a `crear_keyword`/`borrar_keyword` del
   escritor por plataforma y devuelven el ack canonico. La intencion
   (`keyword_reposicion_intento`), el ACK antes del readback, la lectura
   estricta y el sello siguen en `app/ads/reposicion.py`. La tabla de
   `app/apply_estados.py` sigue decidiendo: D no agrega estados, eventos ni
   casillas.
6. **Retiro/impulso y reactivacion de anuncios.** Ya usan `AdsWriteClient`. El
   unico cambio es que `preparar_escritor(platform, modo_confirmado=...)` deja
   de inventar `"live"`: cada llamador pasa el modo que su propia autorizacion
   probo (decision/humana/accion del dueno) y un modo distinto revienta en el
   constructor antes de cualquier HTTP.

## Casos que recorre cada camino antes de empujar

1. ok por item, 207 con error parcial, 4xx deterministico, 5xx o red sin saber
   si aplico, 401/403 que aborta la fase.
2. Perfil ajeno, metodo ajeno o ruta fuera de la allowlist: denegados antes del
   HTTP (la prueba espia cuenta cero requests, ni el token LWA).
3. Caida antes y despues de cada escritura: el paso/intento queda en su estado y
   la fila siguiente concilia.
4. Dos procesos sobre la misma entidad: el dueno vencido no sella (C lo prueba
   con dos conexiones; la fabrica con el lock de lote y el sello por `id`).
5. Segundo intento de algo ya confirmado: cero HTTP (C y la fabrica por
   `estado`/`readback`).
6. `preparar_escritor` no fabrica `live`: sin argumento no compila y `shadow`
   revienta antes de construir.

## Fuera de D

No hay migracion: D no agrega tablas, columnas ni indices. Cuotas (H), indice
shadow (E), meta base (F), sello temporal (G), Ajustes (I) y retiro de modos (J)
siguen intactos.
