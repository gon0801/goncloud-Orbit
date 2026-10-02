# REPRICING 01 D.2 — encendido MX FBA: decisión A (sombra) del 2026-10-01

Go del dueño para D.2 el 2026-09-30. Antes de encender, el lead leyó
producción con `orbit_read` (scripts en `lecturas/`, todos de solo lectura
salvo `repricing-sombra-fba-escribe.sh`, que corrió el dueño con `!`).

## Lo que se encontró

- Goals 6/7/8 (1213, 1284, 1295) en `shadow` desde el 20-sep: `mantener
  en_tolerancia` desde el 26-sep (margen a < 0.5 pts del goal) y **cero ventas
  en el ledger** (`u15 = u60 = 0`, sin `ultima_venta`). Encenderlos no movería
  nada ni mediría nada.
- `precio_cambio`: solo las 6 filas de A.4. Config 21 con las 20 claves
  `precio_*`. Cron 13:10 UTC vivo (crontab de `gon`; el ssh entra como `root`).
- Ledger MX cubierto al 2026-09-29; 291 de 293 ventas de 75 días con
  `product_id` (mapeo sano), 84 productos. Solo el producto 1621 (listing
  1204) llega a `u60 >= 20`.
- **Lo que más vende en MX es FBM**: 1204, 1200, 1206, 1142, 1133, 1150 sin
  inventario FBA y sin `estimacion_escenario` (el canal FBM llega con E.3).
- **Ningún FBA llega a `u60 >= 20`** (máximo 13). Con `precio_u60_min = 20` la
  señal sale `sin_dato`: el motor puede `subir` (no depende de la señal) pero
  no `bajar` ni frenar por `perdiendo_tras_subida` (`app/precio/reglas.py`).
- Escenarios MX cada 6 h: `disponible` a las 12 y 18 UTC, `desactualizada` a
  las 0 y 6 UTC (costo del día); `precio_goal.py` usa el último `disponible`.
- **Para E.3**: el ledger cuenta ventas por producto, no por canal. 1256, 1260
  y 1265 (FBA) tienen gemelo FBM (1142, 1133, 1150): su `u15/u60` mezcla los
  dos canales.

## Decisión del dueño (2026-10-01): A

Sombra ahora sobre los FBA que sí venden; FBM primero. D.2 `live` se retoma
cuando exista el canal FBM (E.0a → E.3) o cuando FBA tenga volumen. Las
opciones descartadas: encender `live` sin señal de ventas (B) y bajar
`precio_u60_min` (C).

## Sombra sembrada

Dry-run del lead 06:56 UTC; escritura del dueño con `!` 06:59 UTC (el
clasificador negó la escritura remota al lead). Sin go literal (sombra).

| goal | listing | producto | goal % | margen % | P actual | P* | huella |
|---:|---:|---:|---:|---:|---:|---:|---|
| 9 | 1256 | 207 | 52.00 | 49.25 | 1069.20 | 1165.60 | `603e659968cc2575` |
| 10 | 1260 | 203 | 55.50 | 52.58 | 1188.00 | 1316.69 | `2fd673f02db39230` |
| 11 | 1265 | 185 | 49.50 | 46.50 | 1188.00 | 1295.86 | `a3fc6d0a616953cf` |

Readback: 6 goals vigentes `shadow` (6-11), 0 `live`, `precio_cambio` = 6.

## Pendiente

Leer ~5 corridas de la sombra desde el 2026-10-01 13:10 UTC (esperado:
`subir` virtual con escalón ≤ 10 %).
