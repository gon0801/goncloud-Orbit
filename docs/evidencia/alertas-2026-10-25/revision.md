# Revision de avisos de la noche del 25 de octubre de 2026

Investigacion de solo lectura solicitada por David. Se consulto Postgres mediante ORBIT_DSN_READ con transaccion READ ONLY y limites de consulta. Se hicieron listas Amazon Ads de lectura con el cliente default-deny existente. No se cambiaron presupuestos, pujas, precios, publicaciones, crons ni contenedores. No se enviaron avisos ni encargos a otros agentes.

## Prioridad confirmada: precios Mercado Libre

El log precio-meli.log repite: precio_decision: product_id 375 no es miembro del listing 1556. Publicacion MLM2154113682, SKU NH-PLA-CH-CEN-DOR, producto NH - Platino CH 19mm Dorado. listing.product_id=375; v_precio_unidad_miembro no tiene ningun miembro activo para esa publicacion. El trigger de coherencia rechaza la decision: no conviene quitarlo ni inventar miembros.

Corridas MeLi observadas: seis abortadas el 22/10, seis el 23, seis el 24; dos abortadas hoy (13:10/15:10 UTC) y una abierta de 17:10 UTC, sin proceso de precio encontrado en el contenedor al consultar. La consulta demuestra corridas fallidas desde al menos el 22; no establece el inicio historico del incidente. No se presume que cada corrida historica fallo por identico motivo sin su log por fecha.

La causa inmediata esta localizada. La correccion futura debe conciliar catalogo/miembros reales y el manejo de unidad sin miembros; no insertar una relacion artificial para silenciar el trigger. Este encargo no implementa esa correccion.

## Presupuestos

Las dos alertas de 7/7 dias coinciden con las metricas de 18 a 24/10 y con el presupuesto leido directamente en Amazon:

| Campana | Presupuesto diario vigente | Ventana madura | Gasto | Venta atribuida Ads | Pedidos | Gasto/venta Ads |
| --- | --- | --- | --- | --- | --- | --- |
| USPerNog - Category Exact - US | 7.80 USD | 16/09 a 15/10 | 207.79 USD | 761.40 USD | 7 | 27.29 % |
| kit_arras personalizado auto_discovery 09/09 | 20.00 MXN | 16/09 a 15/10 | 625.25 MXN | 1075.86 MXN | 1 | 58.12 % |

Ventana mas reciente 25/09 a 24/10, con datos aun inmaduros: US 223.55/761.40 USD,7 pedidos,29.36%; personalizada633.93/1075.86 MXN,1 pedido,58.92%. No usar esa ventana como evidencia madura para cortar.

Agotar presupuesto no demuestra rentabilidad ni justifica subirlo. En particular, la personalizada muestra gasto alto frente a ventas atribuidas. Las ventas Ads no son beneficio neto ni prueban causalidad (halo). No se calculo utilidad causal por campana ni una propuesta de presupuesto nuevo.

Alertas de presupuesto expuesto: el codigo avisa cuando el presupuesto supera diez veces el gasto medio diario de dias observados en30d. Redondea al entero: WeddingCoin gasta0.1040USD/dia y ArrasAutoMX0.4147MXN/dia; AD_READY B0D9F4V1CC0.3903MXN/dia. Por eso mensaje gasta0 no significa gasto exactamente nulo. Presupuesto alto no significa dinero ya cobrado ni transferencia posible a otra campana. No recortar automaticamente por ese texto.

## Harvest pendiente, no fallo

Decision2844, cola23, estado pending_veto,live. Termino arras matrimoniales plata .925. Ventana16/09..15/10,27fechas observadas,153clics,2pedidos,397.17MXN de gasto,2394.13MXN de venta Ads: ACoS16.59%, inferior al targetusado20.6783% y35%.

Vence27/10/2026 08:41:02UTC. Origen adgroup339 Arras MAtrimoniales Broad, campana arras matrimoniales broad exact. Destino ArrasManual campana97835222467967/adgroup272585315669297: ambos habilitados en Amazon al consultar. Se leyeron88keywords completas del destino, sin nextToken; no se encontro el termino exacto. El job aun no existe, como corresponde antes de aplicarlo. No se forzo ciclo ni se anticipo la accion.

La accion busca mover el termino ganador a exacta y excluirlo del origen para encauzar trafico; el nombre familia term_cut no implica que sea una busqueda perdedora. El snapshot resuelve destino por terna, grupo_id null/migracion_pendiente: esto NO demuestra que el primer harvest por grupo de FABRICA02 haya terminado.

## Precios Amazon sin evaluar

Hoy se publicaron342observaciones de pricingMX y176US, corridas ok. Los49casosMX y7US si tienen observacion de25/10, pero offers_count=0 y own_listing_price=NULL. Todos muestran estado DISCOVERABLE en ultima observacion Listings. El lector de repricing exige precio propio observado del dia; sin el se abstiene. El texto sin observacion es impreciso: falta oferta/precio utilizable, no la corrida de ingesta.

En inventario FBA observado no hay cantidad disponible ni total positiva para esos casos. TreceMX y los sieteUS no tienen fila de inventario unida por SKU/plataforma: ausencia de fila no equivale a inventario cero ni cubre inventario FBM. Revisar disponibilidad/publicacion si esos articulos deberian venderse; no forzar precios inventados.

Resumen25/10: MX182mantener en_tolerancia+49noevaluados; US5cooldown+13en_tolerancia+74mantener por historia/ventas insuficientes+7noevaluados. Cero cambios hoy no demuestra fallo: las decisiones explican por que mantuvo. Envio imputado es estimacion de costo logístico por familia/marketplace, no un cargo adicional ejecutado.

## Otros avisos

Ciclos146US y147MX terminaron done, con una puja aplicada cada uno; sin fallos de ciclo en estos dos mensajes. El rango contribucion pre-cargos no decisoria es una referencia con limites, NO ganancia neta ni una perdida confirmada del extremo inferior. Entidades sin trafico se saltan por falta de evidencia de actividad, no porque el ciclo se haya detenido.

Las dos notificaciones de pedidos son informativas; no se audito aqui su envio/cumplimiento ni los montos contra el libro externo. Diun/actualizacionesNextcloud yAppFlowy anuncian versiones disponibles, no una falla. Contenedores estaban en ejecucion al consultar; no se instalaron actualizaciones ni se hizo comprobacion funcional de esas aplicaciones.

## Recomendacion unica

Corregir primero la falla del motor de precios MeLi; despues revisar rendimiento por terminos/productos de las dos campanas limitadas antes de aumentar presupuesto. Mantener el harvest en su flujo normal y comprobar su resultado al vencer. La redaccion de gasto0 y sin observacion se puede aclarar como mejora de avisos; no es motivo para alterar decisiones comerciales.

Fuentes locales: `app/avisos_campana.py:avisos_del_dia` y `_miles`; `app/pantalla_dinero.py:lee_campanas`; `app/spapi/precio_mercado.py:insumos`; `app/precio/reglas.py`; `app/notifica.py`; `migrations/0054_precio02_base.sql:precio_decision_coherente`. Datos del servidor y listas Amazon se consultaron el25/10/2026, durante esta revision. No es un monitoreo posterior permanente.
