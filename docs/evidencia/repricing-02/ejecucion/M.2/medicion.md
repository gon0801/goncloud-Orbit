# M.2: mediciones (retencion + contratos)

Fuente: snapshot read-only de `ledger_events` (contabilidad) en
`goncloud`, consultado 2026-10-15. Solo SELECT; nada se escribio.

## Retencion ISR de Mercado Libre: 0.025 (neto)

MeLi SI manda el ISR con `order_id` (1,064/1,064 filas), asi que la tasa
se mide por orden, no pooled: `ISR x 1.16 / venta` (la formula S3 aplica
`isr_tasa` sobre el ingreso NETO, sin IVA).

Dos regimenes exactos al centavo:

- 2025-03..2025-12 (515 ordenes): 0.010001 -> **1.00 % neto**.
- 2026-01..2026-10 (509 ordenes): 0.024998 -> **2.50 % neto**.

Ejemplos: venta 979.11 e ISR 21.10 (2026-10): 21.10x1.16/979.11 =
0.024998. Venta 1,248.00 e ISR 10.76 (2025-03): 0.010001.

**`isr_tasa` de `meli/meli` = 0.025** (regimen vigente 2026). Coherente
con `amazon_mx` (0.025): el negocio esta en 2.5 % en ambas plataformas.

Nota de metodo: el pooled 2026 (13,497.14/655,323.89 = 0.0206) es el
equivalente BRUTO (0.025/1.16 = 0.0216, menos ordenes parciales); usarlo
como `isr_tasa` subestimaria R un 14 %. La primera version de esta
medicion traia ese error; el re-pregunton por orden lo corrigio.

Control Amazon mismo periodo (pooled, Amazon no manda orden):
MX 0.0198, US 0.0212 de bruto. MX bruto-equivale a 0.023 neto (~0.025
con mezcla temporal); mismo orden de magnitud, el metodo es sano.

## Contrato (a): campo2 `fee` solo lo emite MeLi

`dedupe_key` meli: `meli|sale_gross|<orden>|<sku>` y
`meli|fee|<categoria>|<orden>|<sku>`. Distribucion de campo2 por
plataforma (todas las filas):

- amazon: finance 4461, sale_gross 1408, ads 223, shipping_label 25,
  finance2024 21, fee_estimate 1. Nunca `fee`.
- amazon_us: finance 2342, sale_gross 587, shipping_label 545, ads 222,
  fee_estimate 2. Nunca `fee`.
- meli: fee 4179, sale_gross 1050, ads 242, fee_reversal 73, refund 56.

Pin: `clasificar_cargo` mapea identidad `fee` -> `meli_envio` solo con
`platform == "meli"` (doble guarda: identidad + plataforma). Ademas
`fee_type != "shipping_fee"` cae a `otros` antes de mirar la identidad,
asi que referral/isr/tax nunca entran al envio.

## Contrato (b): forma real del `raw_payload` meli

Una venta y un fee reales (sanitizados):

- Claves: `["row", "source"]`; `row` trae SOLO `item_id`
  (`"MLM..."`); largo total 59 caracteres en ambos.
- El fixture del test (`{"row": {"item_id": ...}, "source": ...}`) es
  la forma real, no un invento. `AtribuidorMeli` lee `item_id` de ahi.
