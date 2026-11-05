# Correccion del aborto de precios Mercado Libre

Peticion: David autorizo corregirlo el25/10/2026. PR https://github.com/gon0801/goncloud-Orbit/pull/504. Rama fix/precio-meli-sin-miembros, codigo6622930a383444b69e0d330c6498e63b7d75dc76.

## Causa y correccion

La consulta real a MeLi devolvio MLM2154113682 en paused. Sus cinco miembros historicos estan inactivos; listing1556 conserva producto_ancla375. Esto es un estado valido del catalogo, no motivo para inventar una relacion ni debilitar el trigger.

_persistir_uno tomaba ese ancla sin comprobar membresia vigente cuando faltaba el escenario. Ahora conserva el producto del escenario cuando existe; para el fallback consulta la fuente canonica v_precio_unidad_miembro. Si el ancla ya no es miembro, persiste product_idNULL en el diagnostico de la publicacion. El trigger permanece. No se modifica catalogo ni metas.

Un cambio en app/precio/corrida.py; persistir_decision expresa int|None, como su columna nullable. Una nueva regresion en test_precio_corrida reproduce el caso con el trigger PostgreSQL real, una publicacion sin miembros y otra valida: la primera queda no evaluada, ambas se registran, la corrida cierra y no hay cambios de precio.

## Evidencia

- Antes: la nueva regresion fallo exactamente con CheckViolation: product_id4 no es miembro listing11.
- Despues: esa regresion y tres existentes (Amazon sin insumos, Amazon aplicar subida, MeLi miembro economico distinto del ancla) pasaron:4passed. Ruff sin errores. Secretos/conflictos de commit y push aprobados.
- PostgreSQL de prueba desechable, fuera de produccion, cerrado al terminar. No bateria general local ni nueva revision cruzada.
- Fuente del servidor y contenedor ANTES coincidia exactamente con la base de la correccion: SHA2568b7282edbd31bb5b99b4733cf4c55eed7b772cb54f5f75057387f3c754691a5b.
- Fuente nueva SHA2568f252564a85a9acf065ca40386d4754071f8704cbed566bb499647bae5eeb8e1.

## Despliegue acotado

Servidor /mnt/data/appdata/orbit conserva copias de despliegues y cambios historicos. No se hizo git reset/pull ni se copio el arbol completo. Se partio de la imagen exacta que corria y se reemplazo exclusivamente app/precio/corrida.py; se persistio tambien el archivo del host para posteriores builds. No se tocaron bridge/accounting/otrosservicios.

Respaldo y rollback preparado ANTES: /mnt/data/appdata/orbit/predeploy-meli-sin-miembros-20261025-182350/.
Imagen anterior sha256:1b060f1f339ec31ad64bd445918aeba3c208b46f2f0a1cdcc8bb909eda363781, tag orbit-meli-anterior:20261025-182350.
Imagen nueva sha256:404cd2402cfed054b7b9e8df917a407622950f36f2e3d5402e3b3e8f034d3b89.
Archivo Dockerfile del respaldo contiene FROM de esa imagen anterior y COPY del unico modulo. Se recreo solo app y /health dio200. Python real3.12.15. No habia CLI en vuelo al recrear.

Reversa: bash /mnt/data/appdata/orbit/predeploy-meli-sin-miembros-20261025-182350/rollback.sh. Restaura archivo e imagen anterior y recrea solo app. No se ejecuto una reversa innecesaria sobre el arreglo activo.

## Comprobacion real posterior

Mismo comando del cron: docker exec orbit-app-1 python -m app.cli precio --platform meli. Exit0, decisiones44,escritas0.

Lectura posterior con ORBIT_DSN_READ:
- precio_corrida191: cerrada,25/10/2026 18:24:12.729486..18:24:12.833637UTC,44decisiones nuevas,0movidas,0virtuales,0retenidas,0errores.
- precio_decision3882: listing1556,no_evaluado,oferta_ausente,product_idNULL.
- El dia tiene1mantener anterior+9noevaluados oferta_ausente+35noevaluados oferta_desactualizada. Se registraron los pendientes posteriores al que antes abortaba.
- Las9publicaciones sin oferta estan paused. Las35con oferta desactualizada estan active en el catalogo. Las35ofertas provienen todas de11:30UTC; la comprobacion real se hizo18:24UTC, mas de las6h de frescura. Es la abstencion prevista al ejecutar tarde, despues de resolver el aborto que impidio el pase13:10. La ingesta diaria debe aportar datos frescos antes del proximo pase normal; no se altero la fecha ni el precio para simular frescura.

La CI remota ya estaba en curso sobre el PR; no se duplico localmente su bateria. El cierre de repo debe registrar su resultado e integracion real.

Cierre de repo: auto-merge nativo no esta habilitado y no se modifico la configuracion global. Se dejo un cierre acotado en segundo plano que solo integra el SHA probado cuando terminan bien los checks requeridos; se detiene ante cambio de contenido, fallo o plazo1h. Estado local /tmp/orbit-pr504-cierre.json. Al terminar este encargo el arreglo esta instalado y comprobado; no se afirma aun que el PR este unido.

PR #504 integrado: commit de union `6a5a39b3dd8650061deca5b20bccd0442d7f829c`, codigo comprobado `6622930a383444b69e0d330c6498e63b7d75dc76`. Recibo local: `/tmp/orbit-pr504-cierre.json`, estado `integrado`.
