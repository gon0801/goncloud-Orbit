# Lectura de la elegibilidad AUTO-03 (A.3)

Corrida el 2026-11-05 contra producción (goncloud, `orbit-db-1`) con el rol de
lectura (`ORBIT_DSN_READ`), en transacción `BEGIN TRANSACTION READ ONLY` con
UTC fijado y `ON_ERROR_STOP=1`. Comando (vercabecera de
`docs/evidencia/auto-03/plan/elegibilidad.sql`); salida verbatim en
`elegibilidad.txt`, una fila `plataforma|etapa|n` por etapa.

## Embudo medido

| Etapa | amazon_mx | amazon_us |
|---|---|---|
| (1) Productos con margen >0 y ledger ≤3 días | 7 | 3 |
| (2) De esos, con product ads no archivados con listing | 7 | 3 |
| (3) Grupos exclusivos | 6 | 0 |
| (3) Hojas ENABLED bajo grupos exclusivos | 104 | 0 |
| (4) Campañas con goal resuelto enabled+live, target nulo | 5 | 0 |
| (4) Campañas con goal resuelto enabled+live, con target | 0 | 0 |
| (5) Productos con margen y halo 0 en ventana S7 | 0 | 0 |
| (6) Candidatos a pausa (`sustituto_sin_d7`) | 0 | 3 |
| (6) Anuncios S4 de los candidatos | 0 | 29 |
| (8) `ad_ids` jsonb lista de ids | t | t |
| (8) Hay `written` del reporte de productos | t | t |
| (8) Hay filas con `advertised_sku` ≠ `seller_sku` | t | t |
| (8) `first_seen_at` no nulo en product ads | t | t |
| (8) `min(metric_date)` de productos | 2026-08-08 | 2026-08-08 |

Las etapas que no aparecen para una plataforma son cero (el GROUP BY no
produce fila sin elementos).

## Lecturas

1. **La meta por producto no alcanza a nadie hoy.** Los 7 productos MX y 3 US
   con margen positivo tienen halo > 0 en la ventana S7
   `[D-90, max(metric_date)-3]` (verificado por producto: p. ej. MX
   `s_tot=60,882` frente a `s_sku=31,953`), así que la etapa 5 sale cero y no
   hay grupo apto para meta. En US además no hay ningún ad group exclusivo.
   Coincide con la advertencia de la spec: hoy podrían no existir hojas
   elegibles aunque haya productos con margen. La palanca es atribucional, no
   de código.
2. **S1 hoy no deja fuera nada.** Las 5 campañas MX de grupos exclusivos con
   goal resuelto enabled+live tienen todas target nulo; ningún goal de
   campaña tiene target (coherente con la medición del plan del 2026-11-05).
   Si alguien pusiera un target, esas hojas pasarían al goal y el peldaño de
   producto quedaría pisado: `limpia_target` es la reversa.
3. **La pausa con los límites por omisión no atrapa nada en MX y a 3
   productos en US.** Con `sustituto_sin_d7`, clics ≥ 100 y gasto ≥ 500 MXN /
   40 USD en `[D-60, D-30)`, MX no tiene ningún candidato (coherente con la
   lectura del 5-nov: mediana 29 MXN, máximo 251.73 MXN) y US tiene 3, con 29
   anuncios S4. Es la evidencia que motivó C.7 (límites editables con
   simulación).
4. **El sustituto es un techo optimista.** Antes de D.7 no hay rango pedido
   guardado: una corrida manual con `--fecha` habría hecho pasar fechas sin
   fila por cero. Los números de candidatos son un máximo hasta que D.7 guarde
   `fecha_desde/fecha_hasta/cobertura_integra`; después la pausa solo podrá
   actuar sobre cobertura probada.
5. **El impedimento `sku_distinto_en_asin` (S5) es real**: existen filas cuyo
   `advertised_sku` difiere del `seller_sku` del listing del ASIN. El plan ya
   lo trata como impedimento; la ingesta y el resolutor deben abstenerse ante
   esas filas.
6. **Los hechos que el plan da por ciertos se sostienen**: `ad_ids` es un
   jsonb array de ids (S4 puede usar pertenencia), hay `written` del reporte
   de productos, `first_seen_at` está poblado, y el dato Ads por producto
   empieza el 2026-08-08 (igual que la lectura de diseño del 25-oct).
7. **Contraste con la lectura del 25-oct** (spec §«Lo que existe»): mismos 7
   MX y 3 US con margen individual positivo; nada cambió en la base de
   margen.

## Qué promete cada interruptor si se enciende hoy

- `ads_producto_meta_*`: cero hojas alcanzables (sin productos con halo 0);
  construido, inerte hasta que la atribución lo permita. La pantalla y
  `notes.productos` lo mostrarán cada ciclo.
- `ads_producto_pausa_*`: cero anuncios MX, 29 anuncios US sujetos a los
  límites que David guarde y a la cobertura real de D.7 (hoy sustituto).
  Cero es un resultado válido.

## Reproducción

```
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); \
  docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1' \
  < docs/evidencia/auto-03/plan/elegibilidad.sql \
  > docs/evidencia/auto-03/plan/elegibilidad.txt
```
