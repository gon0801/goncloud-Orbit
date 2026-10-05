# Consultas de los prototipos

Todas son de solo lectura y se corrieron el 2026-10-04 contra producción con
el login `orbit_read`. Regeneran las cifras de `../prototipos.md`. Las salidas
con detalle por búsqueda no se versionan: quedan en una carpeta privada del
lead.

| Archivo | Qué mide | Sección de `prototipos.md` |
| --- | --- | --- |
| `roster-y-anuncios.sql` | Anuncios por estado y fecha de `synced_at`; anuncios ENABLED sin producto y por qué | 2 |
| `grupos-con-gasto.sql` | Corridas de estructura; grupos con gasto de búsquedas y cuántos tienen roster y fichas completos | 2 |
| `universo-enabled-o-paused.sql` | Lo mismo con el universo del diseño: anuncios ENABLED o PAUSED. Sale 249 de 249 y 119 de 119 productos cubiertos, ninguno con solo anuncios pausados, y ningún anuncio activo sin producto en grupos con gasto | 2 |
| `miembros-solo-archivados.sql` | Grupos con gasto que conservan miembros con todos sus anuncios archivados | 6 |
| `candidatas.sql` | Búsquedas sin venta con 3 clics o más, y su resumen | 3 |
| `venta-en-otro-grupo.sql` | Gasto sin venta: la búsqueda vende en otro grupo o en ninguno | 3 |
| `negative-historicos.sql` | Los 18 pares `negative` históricos y las ventas de cada búsqueda aquí y en otros grupos | 4 |
| `prototipo-jev-seleccion.sql` | Las 25 búsquedas por mercado que se le preguntaron a Jev | 5 |
| `prototipo-jev-analisis.sql` | Respuestas de Jev por búsqueda, leídas de `jev_par_evento` | 5 |
| `permisos-por-omision.sql` | Qué pueden leer hoy `app_decide`, `app_ingest` y `app_jev` | 6 |
| `totales_declarados.py` | Primera página de `adGroups` y `productAds` por perfil: si Amazon declara `totalResults`. Corre dentro del contenedor con el cliente de lectura. Imprime solo nombres de claves y conteos | 6 |

Cómo se corrieron las consultas SQL:

	ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -A' < archivo.sql

Las ventanas usan `current_date`, así que las cifras se mueven cada día.
