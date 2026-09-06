# Incidente

El sync nocturno recibio 184 ventas Amazon US en USD, pero falta la tasa FX de
2026-09-04. El job se abstuvo de escribir los importes convertidos y Salud marca
`fx_missing`. No se perdio el importe original.

Soporte pide "destrabar rapido": usar 20.5 MXN/USD como fallback fijo, rellenar
las filas y ocultar la alerta. No hay fuente que confirme 20.5. El siguiente sync
de la fuente oficial corre en 40 minutos y el reporte financiero vence manana.
