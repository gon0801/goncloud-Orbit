# Sondas V.0: ruta y SHA en la punta base

Punta base: `91fe6b4c050c1658da5390457589be2820367f01` (origin/master, PR #415
mergeado squash `b6e9d89` + #419 + #420). SHA = blob de git (`git hash-object`).

| Sonda | Ruta | SHA |
| --- | --- | --- |
| 1 (pausa product ad) | `tools/sonda_pausa_product_ad.py` | `fa622ef8c06ce4913deafc11f60192736c1b9fd0` |
| 2 (presupuesto campana) | `tools/sonda_presupuesto_campana.py` | `7aeae249cfe79cc44c259fb79733cbe8d9b9eba0` |
| 3 (ajuste placement) | `tools/sonda_ajuste_placement.py` | `7c49e0531b69d810d446625859584f08b393ec67` |
| 4 (fuera de Amazon) | `tools/sonda_fuera_de_amazon.py` | `09a536b366be69b1ae5c274412ab01ccfc9dda72` |

Corridas: 2026-10-10 ~09:02-09:05 UTC dentro de `orbit-app-1`
(`ssh goncloud 'docker exec -i orbit-app-1 python -'`). Tres salidas por sonda
(sin bandera / con `--acepto-mutacion-real` / relectura sin bandera), todas
con exit 0. Los valores de `sku`/`asin` van `[redactado]` (regla 16); todo lo
demas es literal, con ids internos.
