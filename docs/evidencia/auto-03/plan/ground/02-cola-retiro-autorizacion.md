# Ground 2: cola de cortes, veto, cuota y retiro de product ads (explorador, master 5adf1be0, 2026-11-04)

## Overview
- `apply_cola` es el único encolador de cortes. Solo acepta los kinds `pause`, `negative` y `harvest`, y el schema lo amarra: CHECK `apply_queue_solo_cortes`, `familia` GENERATED y mapa de cuota. Hoy una pausa solo se ejecuta sobre hojas `keyword`/`product_target`. Una pausa de `product_ad` cae en shadow o se descarta.
- `retiro_anuncios` pausa y repone product ads por su propio camino, `archivar` → `PUT /sp/productAds`. No toca `apply_queue`, `apply_attempt`, `decision_application`, la cuota ni el veto.
- `ejecuta_retiro`/`repone` exigen `go: str` no vacío. Ya existe un llamador automático: el vigía de impulso reusa el `go_literal` humano guardado al lanzar el impulso.
- Ningún camino automático pausa hoy un `product_ad` por decisión del ciclo. Solo están la UI de la fábrica (manual) y el vigía de impulso.
- Última migración en master: `0074_jev_ads03_bloqueo.sql` (más su `_reversa`). En origin/master existe además `0075_meli_sku_alias.sql`; el siguiente número libre es 0076.

## 1. `app/apply_cola.py`
- **Firma.** `encola_cortes(conn, aplicador, cycle_id, *, modo_envelope, ahora) -> ResumenEncolado` (app/apply_cola.py:478-485). Lee las decisiones del ciclo con `kind IN ('pause','negative','harvest')` (:148-153), así que un kind nuevo no se encolaría.
- **Payload.** Una pausa sobre `keyword`/`product_target` lleva `{keywordId|targetId, state: PAUSED}` (:454-461, :513-522). Una pausa sobre otra entidad recibe `payload={}` (:523-526).
- **Modo, re-resuelto por decisión.** Lo resuelve `_modo_efectivo_corte` (:422-451). Las hojas delegan en `Aplicador.modo_efectivo`. Cualquier otra entidad pasa por `_SQL_GRUPO_VIVO`, que filtra `kind='ad_group'` (:270-275). Un `product_ad` no lo pasa y queda **shadow**.
- **Inserción.** `vence_el = ahora + 48h` (`VENTANA_VETO`, :108, :530). Si choca con el único parcial, la decisión queda viva: el choque va al resumen y, en ciclos live, a `decision_sin_aplicar` con `choque_clave` (:562-577).
- **Clave de efecto.** Es `(platform, ad_entity_id, familia, search_term)`. El índice `apply_queue_clave_efecto_en_vuelo` es NULLS NOT DISTINCT sobre filas no terminales (migrations/0002_apply.sql:106-109). `familia` es GENERATED: `pause`→`entity_cut`, `negative|harvest`→`term_cut`, y si no, NULL, lo que viola el NOT NULL (0002:55-59, 85-92).
- **Skip del ciclo por clave.** Lo hacen `claves_bloqueadas`/`skip_por_clave` (apply_cola.py:140-146, 359-382), llamados desde cycle.py:2445; los motivos se cuentan en cycle.py:1681 y :1882. Una fila vetada solo bloquea mientras `vence_el > ahora`.
- **Veto durable.** `POST /veto` (app/api_write.py:261-312): `vence_el = now() + dias`, con `dias` 30 por defecto (`VENCIMIENTO_VETO_DEFAULT_DIAS`, api_write.py:75; `CuerpoVeto`, :152-155, rango 1..365). Exige `app_admin` por trigger. Se pueden vetar filas en `pending_veto` y `released`; una en `applying` responde 409. Al vencer, el motor re-propone con una fila nueva (docs/APPLY.md:103-112).
- **Liberación** (`libera_vencidos`, :1073-1236). Toma solo filas live: `pending_veto` vencidas más `released` (:172-178). El orden es:
  1. Liberación atómica (:1141-1145).
  2. Re-check del modo: si no es live, descarta con `modo_no_live` (:1148-1156).
  3. Revalidación PRE-claim (`_revalida`, :901-925): gate de ancestros, después `_revalida_pause`.
  4. Cobro de cuota con `consume_quota_y_sello(conn, platform, fila.kind)` (:1202).
  5. Claim atómico `released→applying` (:193-197, :1213).
  6. `_ejecuta_pause`/`_ejecuta_negative`.
- **`_revalida_pause`** (:726-844). Si `apply._identidad` no está en `apply._KINDS_DECISORAS`, que solo tiene `keyword` y `product_target` (app/apply.py:1186-1189), devuelve `entidad_no_viva`. Luego lee el estado fresco por LIST, aplica la gracia de reactivación de 7d y el `pause` propio verificado (:207-218), y re-decide con `motor_bid.decide_pause` usando evidencia anclada al reloj de liberación.
- **Cuota.** La clave del motor es `ads_optimizer:<p>:<kind>` y la de config `ads_apply_cap_<p>_<kind>`. `KINDS_QUOTA = ("bid","pause","negative","harvest")` (apply.py:307-317). El mapa vive en el SQL de `apply_cap_de_config`: 0002:461-468, redefinido en migrations/0039_precio.sql:1150-1162. Un kind sin mapeo no crea fila del día y no deja cobrar (fail-closed).
- **Cierre de la fila.**
  - `_ejecuta_pause` (:948-997) escribe `apply_attempt` tipo `normal` con `quota_cobrada=True` y commitea antes del HTTP. Luego llama `cliente.pausar_keyword|pausar_target`, lee por LIST y sella el ledger.
  - `_confirma_resumen` hace upsert en `decision_application` (verify_ok; `applied_cycle_id` solo si verifica, apply.py:1131-1145) y deja `apply_queue` en `applied|failed`.
  - Un 5xx ambiguo sube como excepción y deja la fila en `applying`. La reconcilia `_reconcilia_pauses` (app/apply_harvest_reconciliacion.py:247-292), que también marca `fallo:entidad_sin_identidad` cuando el kind no está en `_KINDS_DECISORAS`.
- **Llamada del ciclo.** TX4: `encola_cortes` y, solo si es live, `libera_vencidos` (app/cycle.py:2551-2575). El ciclo corre como `ORBIT_DSN_DECIDE`/`app_decide` (app/cli.py:88, 487).

## 2. `app/retiro_anuncios.py`
- **`ejecuta_retiro(conn, plan: PlanRetiro, *, go: str, impulso_id=None, escritor=None, actor=None) -> int`** (:265-343).
  - Solo valida que `go` no esté vacío (:285-286); el actor por defecto es `go`.
  - `motivo=='impulso'` exige `impulso_id` y viceversa.
  - `MotivoRetiro = Literal["impulso","sin_pedidos","subfamilia"]` (:26, validado en `planea_retiro`, :127).
  - Toma advisory locks de sesión `retiro-ad:<ad_entity.id interno>` con `hashtext` (:250-263, :312).
  - Hace una lectura previa global vía `archivar.leer_anuncios` (:337).
  - Por anuncio: intención antes del PUT en `anuncio_retiro_intencion` (:177-202). Después del readback, `pausado` o `ya_estaba` sellan `anuncio_retiro` y cierran la intención (:393-396). `fallo` o `no_existe` cierran sin retiro. `sin_confirmar` o una excepción dejan la intención abierta (:386-410).
  - Si hubo algún fallido lanza `RuntimeError("retiro no confirmado (N registrados)…")` (:341-342). No devuelve un resultado por anuncio.
- **`planea_retiro(conn, plataforma, *, listing_ids, motivo, conservar_grupo_id)`** (:116-143). Selecciona product ads `ENABLED` o con intención abierta, de esos listings y fuera del grupo a conservar. Excluye ad groups con un impulso abierto (:64-87). La huella sha256 del plan está en :55-61.
- **`repone(conn, *, retiro_ids, go: str, escritor=None, actor=None) -> int`** (:423-488). Mismo literal `go`, mismas llaves `retiro-ad:<id>`. `_repone_uno` (:491-521) decide así:
  - PAUSED con previo PAUSED → solo cierra.
  - PAUSED → reactiva el mismo adId.
  - ENABLED con previo PAUSED → re-pausa.
  - **ARCHIVED → recrea** con `archivar.reponer_anuncios`, que hace `POST /sp/productAds` (:513-518).
  - Inserta en `anuncio_reposicion` (:479-484). Si alguno falla, lanza `RuntimeError` parcial.
- **`reconcilia_intenciones`** (:534-646) es el paso diario. Con Amazon en PAUSED **sella el retiro** usando la reversa guardada antes del PUT (:599-617). Con ENABLED reintenta el PUT. Con ARCHIVED o inexistente cierra sin retiro. Ante incierto la intención sigue abierta.
- **Tablas.**
  - `anuncio_retiro` y `anuncio_reposicion` (migrations/0066_bids02_retiro_anuncios.sql:22-50). Son append-only por trigger y admiten un solo retiro abierto por anuncio (trigger, :57-74). Los CHECK de `motivo` y `modo` ('pausa','archivo') están en :38-40.
  - `anuncio_retiro_intencion` (migrations/0069_bids02_retiro_intencion.sql:21-47): una pendiente por anuncio y solo se puede cerrar.
  - Ninguna tiene columna `decision_id`.
  - Grants: solo `app_admin`; **`REVOKE SELECT … FROM app_decide`**, comprobado por un DO-block (0066:98-117; 0069:95-110).
- **Llamadores.**
  - UI de la fábrica: `POST /retiro/ejecutar`, con `go` igual a la confirmación tecleada `"SACAR {n} ANUNCIOS"` y check de huella (app/api_fabrica.py:684-708). `POST /retiro/reponer`, con `"REGRESAR {n} ANUNCIOS"` (:722-745). Ambos con `ORBIT_DSN_ADMIN`.
  - Vigía de impulso: `ejecuta_retiro(..., go=go, actor="impulso-vigia")`, donde `go` sale de `impulso.go_literal` (app/impulso_io.py:870-877, 934, 1037-1057). También llama `reconcilia_intenciones` (:1150). Lo invoca app/cli_bids.py:111-128 con `ORBIT_DSN_ADMIN`.
- **Pausar frente a recrear.** El retiro solo pausa: si el anuncio está ARCHIVED, `ejecuta_retiro` lo marca como fallo (sin intención previa) o cierra la intención (:354-373). La recreación solo existe en `repone`, de forma implícita cuando lee ARCHIVED.

## 3. Apply de PAUSE hoy
- Ruta: `cycle._procesa_decisora` decide `pause` sobre una hoja, respetando cooldown (cycle.py:1750-1790). Después `encola_cortes` → `libera_vencidos` → `_ejecuta_pause`, que llama `AdsWriteClient.pausar_keyword|pausar_target` (app/ads/write.py:358-376 → `_cambiar_estado`, :650-680).
- Allowlist default-deny `MUTATION_REQUEST_TYPES` (write.py:93-128). Ya incluye `PUT /sp/productAds`, `POST /sp/productAds` y `/sp/productAds/delete`; `cambiar_estado_product_ad` está en :548-580.
- Candado de imports: solo `app/apply.py`, `tools/smoke_apply.py`, `app/ads/archivar.py` y `app/bloqueo_dueno_efecto.py` pueden importar `app.ads.write` (tests/test_architecture.py:321-345). `apply_cola` no lo importa: recibe el cliente del Aplicador (:940-945).
- **No existe pausa automática de `product_ad`.** `_KINDS_DECISORAS`, `_payload_pause`, `_revalida_pause` y `_reconcilia_pauses` solo conocen `keyword`/`product_target`. Las únicas pausas de product ad son las de `retiro_anuncios` (fábrica manual e impulso).

## 4. Vocabulario e invariantes
- **Enums.** `decision_kind` = `('bid','budget','pause','resume','negative','harvest')` (migrations/0001_initial.sql:40). `ad_entity_kind` vale `campaign`, `ad_group`, `keyword`, `product_target` y `placement` (0001:38), más `product_ad` (migrations/0004_ad_entity_kind_product_ad.sql:20).
- **CHECKs en `decision` con kinds explícitos.**
  - `decision_termino_coherente` (0001:874-876) cubre dos grupos: `negative`/`harvest`, y `bid`/`budget`/`pause`/`resume`. Un kind fuera de ambos lo viola.
  - `decision_valor_con_moneda` (0001:883-884).
  - Únicos parciales `decision_unica_entidad_ciclo` (`kind IN ('bid','budget','pause','resume')`) y `decision_unica_termino_ciclo` (0001:897-903).
- **Trigger `decision_madurez_corte`** (0001:933-966). Para `kind IN ('pause','negative','harvest')` exige `window_end <= (decided_at AT TIME ZONE 'UTC')::date - 10`. No fue redefinido después.
- **Otros CHECK y vocabularios.**
  - `apply_queue`: `apply_queue_solo_cortes` y `familia_valida` (0002:85-92); `apply_attempt.tipo` (0002:291-300).
  - `decision_sin_aplicar.motivo`: migrations/0045_sin_aplicar_choque_clave.sql:31-40.
  - Mapa de cuota: SQL de `apply_cap_de_config` (0039:1150-1162) más `KINDS_QUOTA` (apply.py:307).
- **`KINDS_DECISION`** es un `Literal` definido en **`app/api.py:65`**; `app/api_dashboard.py:72` lo importa.
- **Roles.** `app_decide` tiene INSERT y algunas columnas UPDATE en `apply_queue`/`apply_attempt` y `applied_cycle_id` de `decision_application`. `app_admin` tiene veto y descarte de filas shadow (0002:678-712).

## 5. Reversas existentes
- **PAUSE de keyword o target.** `POST /reversa/pause` (api_write.py:380-390) → `apply.reversa_manual(tipo="pause", queue_id)` (apply.py:1720-1738). Exige la fila en `applied` y bloquea con `ReversaYaHecha` si ya se revirtió. Llama `apply_cola.reversa_pause` (:1244-1298), que escribe `apply_attempt` tipo `reversa` con `quota_cobrada=False`, reanuda, lee por LIST y sella `ok` solo si lee ENABLED. No toca `decision_application` ni limpia el cooldown.
- **Retiro de product ad.** `repone` inserta en `anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, actor)`. No pasa por el ledger `apply_attempt`.

## Gotchas para AUTO-03
1. **Un kind nuevo `pause_product_ad` rompe varias cosas a la vez.**
   - `familia` GENERATED da NULL y choca con el NOT NULL, además de `apply_queue_solo_cortes`.
   - `decision_termino_coherente` rechaza el INSERT en `decision`.
   - `decision_madurez_corte` y `decision_unica_entidad_ciclo` no lo cubrirían.
   - `_SQL_CORTES_CICLO`, `_revalida` (que cae a harvest para cualquier kind ajeno, :925), `libera_vencidos` (rama `else` → `_ejecuta_negative`, :1218-1219), `_SQL_PAUSE_PROPIO` y `KINDS_QUOTA`/`apply_cap_de_config` filtran por literal.
   - Con kind `pause`, la cuota se cobra por `fila.kind`. "Una sola reserva en la cuota de pausas" exige mapear el kind nuevo a `ads_apply_cap_<p>_pause`.
2. **El perímetro es una regla escrita.** "Nada nuevo se cuelga de la cola sin decisión del dueño" (docs/APPLY.md:58-62; comentario 0002:41-45).
3. **Roles.** El ciclo (`app_decide`) tiene **revocado SELECT** sobre `anuncio_retiro*` y no tiene INSERT. Todo el retiro corre como `app_admin`. Vincular intención y attempt "antes del HTTP" desde la cola exige migrar grants.
4. **La intención no tiene `decision_id`.** El CHECK de `motivo` no incluye `producto_sin_ventas` (0066:38, 0069:40) y el `Literal` de :26 tampoco.
5. **`ejecuta_retiro` ya acepta un `go` que no tecleó nadie en el momento.** El vigía pasa el `go_literal` guardado del impulso (impulso_io.py:934, 1053). La spec dice que no se simula autorización con una cadena fija; el código ya lo hace con un literal reusado.
6. **Autoría.** La spec dice que observar PAUSED sin evidencia de autoría no habilita reposición propia. El código sella `anuncio_retiro` también con `ya_estaba` (:393) y en la reconciliación cuando lee PAUSED (:599-617), usando la reversa ENABLED previa. Así `repone` reactivaría un anuncio pausado por otro. No hay estado "adoptado" ni "incierto".
7. **`requiere_recreacion` no existe.** `repone` recrea en silencio con `POST /sp/productAds` cuando lee ARCHIVED (:513-518).
8. **No hay resultado parcial estructurado.** `ejecuta_retiro`/`repone` lanzan `RuntimeError` con un conteo en el texto; no hay un 4/5 por anuncio.
9. **Dos mecanismos de exclusión distintos.** La cola usa transiciones UPDATE…WHERE estado y el índice único. El retiro usa advisory locks de sesión `retiro-ad:<id>`. Hoy no comparten ningún candado, y las reversas de pause se apoyan en `apply_attempt` mientras las de retiro se apoyan en `anuncio_reposicion`.
10. **Tamaño de módulo.** `apply_cola.py` tiene 1345 líneas, sobre `MAX_LINEAS_MODULO=900` (tests/test_architecture.py:46). Está en `ALLOWLIST_TAMANO` con la nota "partir la ejecución por fila la próxima vez que se toque en grande" (:131-140).
11. **Exclusión de impulso.** `planea_retiro` ya excluye ad groups de impulsos abiertos (:81-85). El retiro no revisa modo live, goal, veto ni alcance: eso hoy solo lo hace la cola.
