# F.4: adaptador de Amazon para FBM/US y sonda de escritura

Fila **F.4** de `plans/repricing-02.md`, seccion 4. `historia_unidad` en
`app/spapi/precio_mercado.py` (la consume S.7), serie MX+US del CLI
(fijada con prueba) y `tools/precio_sonda.py` (generica sobre `Mercado`).

## Decisiones

- FBM sale solo del estado del listing (BUYABLE); el inventario no
  cuenta (R01: 34/34 sin existencias no eran BUYABLE). FBA exige probar
  las dos (todo BUYABLE y todo stock > 0); canal sin dato va en
  estricto (regla FBA).
- `no_disponible` pide prueba en contra (stock <= 0 o no-BUYABLE); sin
  prueba ni a favor ni en contra es `sin_observacion` (conservador
  r3-K5, como la senal de hoy).
- Ventas atribuidas de `v_precio_venta_unidad`; lo del producto sin
  unidad decidible se suma en `sin_atribuir` (cantidades, no filas) y
  NO abre dia (no es de esta unidad).
- `dias` cubre `[hoy-75, hoy]` (76 dias, uno por dia observado);
  `primera_venta` no se acota (igual que hoy).
- Trafico: observacion mas reciente por `metric_date` (`ORDER BY` en la
  consulta, no suerte del heap); `None` se conserva, nunca cero
  inventado. Buy box igual, de `spapi_price_observation`.
- `Lectura.historia` (`None` sin cuenta): la arma F.4, la consume S.7.
  La senal sigue por producto hasta S.7.
- La sonda sin `--acepto-mutacion-real` imprime el plan y sale 0; con
  `--go` exige el flag (si no, aborta: dry-run enganoso). La reversa no
  aceptada se grita (el precio QUEDA movido) y sale 1.

## Comprueba

`comprobar.sh`: historia FBM local + `main` real de la sonda con
mercado falso. Salida en `comprobar-salida.txt`:

- FBM: dia BUYABLE con stock 0 sale `disponible` (3 unidades, trafico
  200/9); dia DISCONTINUED sale `no_disponible`.
- Sonda dry-run: plan `116.00 -> 116.01 -> 116.00`, rc=0, 0 escrituras.
- Sonda go: escrituras `[116.01, 116.00]`, rc=0.

El comando de la guia contra red (`--platform amazon_us
--listing-id <id>`) corre en X.2/X.3 (`sonda.sh`), con clientes reales.

## Pruebas

- `tests/test_precio02_mercado.py` (+0020 en ORDEN02B): H1 FBM ignora
  inventario, H2 FBA exige stock+BUYABLE, H3 ventas de la vista y
  `sin_atribuir`, H4 trafico y buy box, H5 ventana 76 dias, H6
  `insumos` adjunta historia.
- `tests/test_precio_corrida.py`: H7 serie MX+US (fija conducta de 0.b).
- `tests/test_precio_sonda.py`: H8 dry-run no escribe, H9 go mueve un
  centavo y revierte, H10 listing inexistente/de otra plataforma.
- `tests/test_arq_precio_f.py`: H11 la sonda no importa adaptadores
  (+ control con import sembrado).

Bateria: mercado 14 + sonda 3 + arq_f 6 + cli_precio 11, en verde.
