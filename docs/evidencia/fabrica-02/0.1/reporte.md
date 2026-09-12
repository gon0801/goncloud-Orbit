# FABRICA 02 / 0.1 — Sonda de product targeting (evidencia)

Fecha: 2026-09-12 UTC (reloj del server y de la base). Ejecuta: el dueño, con
go literal en el momento (nada pre-autorizado). Base del plan: `plans/fabrica-02.md`
v1.1 (`origin/master` `3190074`). Herramienta: `tools/smoke_apply.py` tal cual
está en master (md5 `2b5c36d6a5cf9155a8260b85d368d40b`, verificado local y
dentro del contenedor antes de correr).

## Veredicto

**PT acepta negative keywords por texto: SÍ.** Por la regla sellada del spec §7
(«si el POST se acepta, las 4 hermanas»), la **decisión 10 queda en 4 hermanas**:
`auto_discovery`, `category_broad`, `category_phrase` y `product_targeting`.

## Alcance de esta corrida (decisión del dueño 2026-09-12)

El 0.1 del plan tenía dos mitades. Se corrió **solo la (i)**, la que bloqueaba
el diseño:

- **(i) Pregunta de PT — HECHA.** Una corrida contra la campaña
  `70314694808265`.
- **(ii) Ensayo de la reversa en orden con ids reales — DIFERIDA**, no omitida.
  Razón: `tools/smoke_apply.py` crea y archiva **en la misma corrida**, por
  campaña; correrla tres veces da tres ciclos independientes, no «creo tres,
  los sostengo y los borro en orden». Hacer el orden literal hoy exigía romper
  la invariante de neto-cero de la herramienta o salirse de ella. Y el orden
  que se quiere ensayar es el de `reversa_harvest_completo`, que **todavía no
  existe**: lo construye A.3. Se ensaya donde tiene sentido: A.3 con simulador
  (DoD (g): secuencia de ids `[keyword, h1, h2, h3, origen]`) y D.3 en vivo con
  `tools/reversa_harvest.py`.

## Ceremonia (config, append-only)

`config_version` **17**, label `FABRICA 02 / 0.1 sonda product_targeting
(autorizado por el dueno; base config 16)`. Se construyó copiando los settings
vigentes y **agregando** las dos claves (sembrar solo las nuevas habría apagado
los `ads_apply_cap_*` del día):

```sql
INSERT INTO config_version (label, settings)
SELECT '<label>', settings || jsonb_build_object(
         'ads_smoke_campaign_amazon_mx', '70314694808265',
         'ads_smoke_auth', '<token efimero>')
  FROM config_version ORDER BY id DESC LIMIT 1;
```

Verificado antes de correr: 16 claves resultantes (14 vigentes + 2),
`ads_apply_cap_amazon_mx_harvest = 5` y `ads_optimizer_mode = live` intactos,
`ads_smoke_auth` presente con 32 caracteres (coincide con el archivo del token,
que es lo que compara `compare_digest`).

Token efímero: generado **en el server** (`/dev/urandom`, 32 caracteres, `600`,
uid root), copiado al contenedor por **archivo** (`600`, uid 10001) y verificado
byte a byte contra el del host (`cmp`). Jamás viajó por argv ni por el historial.

## La corrida

```
ssh goncloud 'docker exec orbit-app-1 sh -c "ORBIT_SMOKE_AUTH=\$(cat /tmp/smoke_token) \
  PYTHONPATH=/app python /tmp/smoke_apply.py --forma negative \
  --platform amazon_mx --acepto-mutacion-real"'
```

**La barra invertida de `\$(cat …)` no es cosmética** (hallazgo CodeRabbit en
el PR #255, sobre una transcripción previa de esta misma línea que la omitía):
sin ella, la shell del server expande el token ANTES del `docker exec` y el
valor queda en el `argv` del contenedor, visible en `ps` y `/proc`. Con ella,
el `$(cat …)` viaja literal y lo expande la shell de adentro, que ya tiene el
archivo `600`. La corrida real llevó la barra — es el mismo escapado que manda
el runbook de `tools/smoke_apply.py:64`.

Log crudo: `out/smoke-0.1-fabrica02.log` (no se commitea; `out/` en
`.gitignore`). Perfil `3850003733258937`, plataforma `amazon_mx`.

| paso | resultado |
|---|---|
| `estado_inicial` | ad group `187855388248650`, término basura `zzsmokeprobe20260912061635` |
| `http_create` | **207**, `error: []`, `success: [{index: 0, negativeKeywordId: 45705293970881}]` |
| `readback_create` | `hallado: true`, id `45705293970881` (identidad: ad group + texto + `NEGATIVE_EXACT`, ignorando `ARCHIVED`) |
| `http_delete` | **207**, `error: []`, mismo id — el «delete» v3 **archiva** |
| `readback_final` | `ausente: true` → **neto cero** |

`ok: true`, `neto_cero: true`, `exit: 0`, `rc=0`.

El ad group no se eligió a dedo: la campaña `70314694808265` tiene **exactamente
un** ad group, así que `primer_ad_group_de_campana` resuelve al correcto. Por eso
**no hizo falta tocar la herramienta** — el plan preveía agregarle un flag de ad
group y resultó innecesario.

## Ledger (`apply_attempt`, verificado en la base)

| id | tipo | decision_id | quota_cobrada | resultado | request_payload |
|---|---|---|---|---|---|
| 154 | `probe` | `null` | `false` | `ok`, sellada | `{adGroupId: 187855388248650, campaignId: 70314694808265, keywordText: zzsmokeprobe20260912061635, matchType: NEGATIVE_EXACT, state: ENABLED}` |
| 155 | `probe` | `null` | `false` | `ok`, sellada | `{negativeKeywordIdFilter: {include: [45705293970881]}}` |

Ambas con su `ack` real guardado. La 154 nació **antes** del HTTP (intención
durable pre-HTTP) y se selló con el ack: es el rastro que pide el módulo apply.
Cero quota cobrada, `decision_id` nulo — no es una decisión del motor.

## Shapes re-confirmados

Los tres que la herramienta pinea siguen vigentes contra respuestas reales de
hoy: `matchType` es el enum UPPER del recurso (`NEGATIVE_EXACT`); el ack 207
trae `success`/`error` anidados por recurso y el id vive en el primer `success`;
y el borrado real es `POST /sp/negativeKeywords/delete` con
`{negativeKeywordIdFilter: {include: [id]}}`, que **archiva**.

## Cierre

- Contenedor: `/tmp/smoke_apply.py` y `/tmp/smoke_token` borrados; token del
  host borrado.
- `config_version` **18**, label `FABRICA 02 / 0.1 cierre: retira claves de
  smoke`: fila nueva **sin** `ads_smoke_auth` ni `ads_smoke_campaign_amazon_mx`
  (append-only: quitar = fila nueva). Verificado tras el cierre:
  `auth_presente = f`, `campana_presente = f`, **14 claves** exactas, y los
  valores de negocio intactos (`ads_optimizer_mode = live`,
  `ads_apply_cap_amazon_mx_harvest = 5`, `ads_apply_cap_amazon_mx_negative = 15`,
  `fabrica.creacion = v2`). La 17 conserva las claves en el historial: es una
  tabla append-only y eso es el rastro, no una fuga — el token es de un solo
  uso y ya está muerto (residual declarado en APPLY.md 11d).
- Contenedor y host verificados vacíos: ni `/tmp/smoke_apply.py` ni
  `/tmp/smoke_token` existen en ninguno de los dos.
- Cero negativos vivos del probe al cerrar (`readback_final: ausente`).

## Residuales declarados

1. **Aceptado ≠ efectivo.** El spec suponía que PT rechazaría el negativo por
   texto porque ahí se niega por ASIN/marca (`negativeTargets`). Amazon lo
   aceptó, le dio id y lo listó — eso responde la pregunta del **contrato de la
   API**, que era la que bloqueaba el diseño. Lo que esta sonda **no** puede
   responder es si ese negativo *suprime* algo en un ad group que targetea
   ASINs y no términos de búsqueda: probablemente sea inerte. Se sigue la regla
   sellada del spec (4 hermanas) y queda anotado para que nadie se pregunte
   después por qué PT lleva una keyword negativa. Si algún día se mide y resulta
   inerte, quitar PT de las hermanas es una decisión de una línea.
2. **Campaña viva, no sacrificable.** La herramienta se escribió para una
   campaña de descarte; aquí se usó contra una campaña real del grupo
   `kit_arras`. Durante ~2 s (entre `http_create` y `http_delete`) esa campaña
   tuvo bloqueado el término `zzsmokeprobe20260912061635`, que nadie busca. El
   término basura lleva prefijo `zz` + timestamp justo para eso.
3. **El id archivado queda en la cuenta.** `borrar_negative` archiva, no borra:
   `45705293970881` existe con `state=ARCHIVED` para siempre. Es inocuo — la
   identidad del motor salta los `ARCHIVED` (`app/apply_harvest.py:474-475`) —
   y es el mismo residuo que dejó el probe 2.5 de ORBIT 04.
