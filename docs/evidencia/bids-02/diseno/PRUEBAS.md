# Pruebas de lo que faltaba (2026-10-09)

Todo fue de solo lectura: documentación de Amazon, consultas a la base de producción con el rol de lectura y
el cliente de solo lectura de Orbit contra Amazon (listar campañas y pedir reportes). No se escribió nada en
Amazon ni en la base, y no se tocó código del repo. Scripts y salidas en
`~/.claude/jobs/e819773e/tmp/pruebas/` (copiados a `pruebas/`).

## Prueba 1. Cuánto se pasa Amazon de un presupuesto diario

- Documentación (`release-notes/ads-api-v0`, `guides/account-management/average-daily-budget`): Amazon puede
  gastar en un día hasta 100 % más que el presupuesto diario promedio, usando lo que no gastó en días
  anteriores del mes. El total del mes nunca pasa del presupuesto diario por los días del mes. Existe una
  opción de cuenta para bajar ese extra a 25 %.
- Medido en la cuenta (gasto diario por campaña desde 2026-08-01 contra el presupuesto de hoy): en MX hubo 32
  días-campaña arriba del presupuesto y 10 arriba de 125 %, con máximo de 1.62 veces. En US hubo 65 y 20, con
  máximo de 2.12 veces. Límite de la medición: usa el presupuesto de hoy, que pudo ser otro en esas fechas.
- Consecuencia para el diseño: un día puede costar el doble del presupuesto diario. El impulso pasa de
  presupuesto diario igual al tope entre 7 a tope entre 14, y el vigía pausa cuando faltan dos presupuestos
  diarios para el tope. Corregido en `bosquejo.py` y `design.md`.

## Prueba 2. El reporte por placement con datos diarios

- Amazon lo aceptó en vivo: `spCampaigns`, `groupBy ["campaign","campaignPlacement"]`, `timeUnit DAILY`, con
  `date`, `placementClassification`, impresiones, clics, gasto, pedidos, venta, nombre, estado y presupuesto de
  campaña. 31 fechas distintas, 2,164 filas en MX y 1,112 en US.
- Lo único que rechazó: la columna `topOfSearchImpressionShare` cuando se agrupa por campaña y placement.
- Valores de placement vistos: `Top of Search on-Amazon`, `Detail Page on-Amazon`, `Other on-Amazon`,
  `Off Amazon`.
- El reporte trae `campaignBudgetAmount` por fecha, así que también sirve de historia de presupuestos.

Resultado del reporte, solo campañas activas, 2026-09-06 a 2026-10-06:

| MX | % del gasto | Pedidos | CPC | Conversión | ACoS |
|---|---|---|---|---|---|
| Top of Search | 39.4 % | 64 | 3.52 | 2.79 % | 12.4 % |
| Detail Page | 33.5 % | 33 | 5.55 | 2.66 % | 20.1 % |
| Other on-Amazon | 18.6 % | 27 | 3.64 | 2.57 % | 12.4 % |
| Off Amazon | 8.5 % (1,735 MXN) | 0 | 1.54 | 0 % | sin venta |

| US | % del gasto | Pedidos | CPC | Conversión | ACoS |
|---|---|---|---|---|---|
| Detail Page | 51.3 % | 16 | 0.56 | 1.52 % | 35.6 % |
| Other on-Amazon | 41.3 % | 12 | 0.32 | 0.81 % | 34.2 % |
| Top of Search | 6.9 % | 3 | 0.48 | 1.84 % | 24.9 % |
| Off Amazon | 0.5 % | 0 | 0.15 | 0 % | sin venta |

## Prueba 3. Pausar un anuncio de producto en vez de archivarlo

- Documentación (`sponsored-products/3-0/openapi/prod`): `PUT /sp/productAds` actualiza el `state` de hasta
  1,000 anuncios; archivar es otra llamada (`POST /sp/productAds/delete`).
- En producción ya existen anuncios en ese estado: 1,579 `PAUSED` en MX y 464 en US.
- Conclusión: Amazon sí permite pausar. Lo que falta es que Orbit lo tenga en su lista de escrituras
  permitidas. Confirmarlo en vivo exige una escritura (pausar un anuncio y reactivarlo), que no se hizo.

## Prueba 4. Cuánto tarda el ciclo con la lectura nueva

- Hoy un ciclo completo tarda 0.9 segundos de mediana y 1.4 como máximo (22 ciclos por país, 21 días).
- La lectura nueva de 90 días de todas las hojas de una plataforma tarda 0.11 segundos en MX y 0.09 en US.
  La lectura diaria de 30 días tarda 0.05.
- El diseño cambia de 5 a 7 consultas por hoja a unas 4 por plataforma. No hay riesgo de tiempo.

## Lo que la lectura en vivo mostró además

Configuración real de las campañas activas (Amazon, 2026-10-09):

- US: 7 de 9 campañas tienen +40 % en páginas de producto. Ahí se va 51 % del gasto, con ACoS de 35.6 %.
  La mejor ubicación, arriba de búsqueda (24.9 %), recibe solo 6.9 % del gasto y no tiene ningún ajuste.
  Con 3 pedidos en esa ubicación, la diferencia no es concluyente.
- MX: casi todas tienen +9 % arriba de búsqueda. Una automática tiene +28 % arriba y +9 % en producto.
- Estrategia de puja: en MX las campañas exact usan puja dinámica hacia arriba y hacia abajo; el resto solo
  hacia abajo; las de marca, fija. En US casi todas solo hacia abajo.
- MX gasta 8.5 % fuera de Amazon (1,735 MXN en 30 días, 1,127 clics) sin un solo pedido atribuido. Se reparte
  en 16 campañas; la mayor es `AGM2M - Auto Discovery - MX` con 530 MXN.
- Presupuestos: en MX suman 11,126 MXN diarios contra 672 de gasto real; no limitan nada. Una campaña tiene
  5,354 MXN diarios de presupuesto. En US suman 131 USD contra 37; dos campañas gastan su presupuesto completo
  (`AU2 - Category Broad - US` y `USPerNog - Category Exact - US`).
- El payload de campaña trae `budget`, `dynamicBidding.strategy` y `dynamicBidding.placementBidding` en las
  246 campañas, más `offAmazonSettings`, `marketplaceBudgetAllocation`, `tags` y `portfolioId`.

## Lo único que sigue sin probar

Pausar y reactivar un anuncio de producto en vivo. El dueño lo autorizó el 2026-10-09 (decisión D8). La
sesión del lead lo intentó y el entorno lo bloqueó por ser una escritura a producción; no se buscó otra
vía. La sonda quedó escrita en `pruebas/sonda_pausa_product_ad.py`, sin ejecutar: toca un solo anuncio
dentro de una campaña ya pausada, aborta si algo no coincide y deja el anuncio como lo encontró.
