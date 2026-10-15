# M.2: mediciones (retencion + contratos)

Fuente: snapshot read-only de `ledger_events` (contabilidad) en
`goncloud`, consultado 2026-10-15. Solo SELECT; nada se escribio.

## Retencion ISR de Mercado Libre: 0.0206

Pooled 2026-01-01..2026-10-15, MXN (unica moneda en `meli`):

- `sale_gross`: 655,323.89 (535 filas)
- `isr_withheld` (abs): 13,497.14 (549 filas)
- **Tasa: 13,497.14 / 655,323.89 = 0.0206** -> `isr_tasa` de `meli/meli` (M.3).

Estabilidad mensual (tasa por mes): 01: 0.0216, 02: 0.0219, 03: 0.0208,
04: 0.0209, 05: 0.0212, 06: 0.0205, 07: 0.0197, 08: 0.0216, 09: 0.0189,
10: 0.0205. Rango 0.0189-0.0219: el pooled es representativo.

Control Amazon mismo periodo: MX 29,913.22/1,509,163.21 = 0.0198,
US 27,918.43/1,316,178.79 = 0.0212, pooled 0.0205. Mismo orden de
magnitud: el metodo mide la politica real (~2 %), no ruido.

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
