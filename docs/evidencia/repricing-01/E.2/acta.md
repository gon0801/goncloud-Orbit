# REPRICING 01 E.2 — acta del envio medido

## Parte 1: ventana, minimo y ausencia de historia

**Decision del dueno:** `180 dias` (mensaje de la sesion del 30-sep-2026,
registrado el 2026-10-01 UTC). La ventana de historial del envio medido FBM
sera de **180 dias**. Esta decision no activa goals ni cambia precios.

La medicion del 17-sep-2026 en `E.1/medicion.md` encontro los siguientes
productos con al menos seis ordenes con envio:

| Ventana | Amazon MX | Amazon US | Historia real disponible |
| --- | ---: | ---: | ---: |
| 90 dias | 7 | 9 | 90 dias |
| **180 dias** | **14** | **17** | **180 dias** |
| 365 dias | 17 | 22 | 287 dias |

La ventana elegida amplia la cobertura frente a 90 dias sin tomar toda la
historia disponible como si fuese una tarifa vigente. Estas son cifras de la
medicion del 17-sep, no un readback de produccion del dia de encendido.

Se mantienen las reglas de S10: **entra con seis ordenes y sale al caer a
tres**; con cuatro o cinco permanece si ya habia entrado. Sin historia
suficiente, `envio_sin_historia`: no se evalua al producto, no se escribe
precio y el motivo queda visible en cobertura. Nunca se sustituye el costo
faltante por cero. En seis ventanas medidas, esta histeresis redujo las
salidas de 9 a 2 productos MX y de 10 a 3 US frente al corte seco.

La ventana efectiva termina antes del dia de evaluacion segun el rezago de
**ingesta**. Su p90 medido fue 4.2 dias MX y 13 dias US. La configuracion
actual tiene una sola clave `precio_envio_rezago_dias`, por lo que el corte
propuesto es **13 dias para ambas plataformas**, el mayor p90 medido. La
medicion de E.1 no reprodujo el antiguo p90 de 57–59 dias de **emision** que
figuraba en S10. Antes de activar FBM se actualizara el readback y se
verificara que el rezago siga siendo valido.

## Parte 2: pendiente

El valor monetario del costo y el ingreso por envio esperan E.0a, E.0b y E.1
sobre el ledger corregido. Los pares `LabmanLabelPurchase`/`shipping_label`
y `ShippingHB` tienen la aclaracion del dueno de contar componentes distintos
(`E.0/decision-dueno.md`), pero siguen sin conciliacion documental. No se
configura ni enciende el motor FBM a partir de esta acta parcial.
