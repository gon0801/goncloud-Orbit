# M.0: sonda de Mercado Libre — FRENADA por credenciales rotas en producción

Fila **M.0** de `plans/repricing-02.md` (`[stage:verificacion] [lane:gate]
[tdd:skip:sonda]`), sección 1, rama `rp02/sec1-lecturas`. Intentada el
**2026-10-13** UTC. **No corrió**: el token de Mercado Libre de
`orbit-app-1` está vencido y la cadena de refresco no existe en el servidor.
Este archivo deja el hallazgo medido y la sonda lista para correr en cuanto
las credenciales se reparen.

## El hallazgo (medido)

1. **La sonda no arranca.** `MeliCredentials.from_secrets_dir` levanta
   `falta la clave 'client_id' (string no vacio) en meli_tokens.json`.
   El archivo `meli_tokens.json` del directorio de secretos trae solo las
   seis claves de una respuesta OAuth cruda
   (`access_token`, `expires_in`, `refresh_token`, `scope`, `token_type`,
   `user_id`); no trae `client_id` ni `client_secret`. La copia de respaldo
   (`meli_tokens.json.bak-20260908-041756`) trae exactamente la misma
   forma: tampoco los tiene.

2. **El token está muerto.** `GET /users/me` con el `access_token` del
   archivo responde **401** (probado desde el contenedor, sin imprimir el
   token). El archivo no cambió desde el **2026-09-27 17:33 UTC** (mtime) y
   un token de MeLi vive horas: está vencido desde hace dos semanas.

3. **Sin refresco posible.** Refrescar necesita `client_id` y
   `client_secret`; no existe ninguna copia en el servidor (solo están los
   dos archivos de arriba). Traerlos de otra parte o coserlos de otra app
   sería inventar credenciales: no se hizo.

4. **La ingesta diaria lleva 16 días cayendo por lo mismo.** El log del
   cron en el servidor (`/mnt/data/appdata/orbit/logs/reputacion-meli.log`)
   pasa de corridas `ok: true` (última: 2026-09-27, 232 filas) a la línea
   `reputacion meli fallo: falta la clave 'client_id' (string no vacio) en
   meli_tokens.json` repetida 16 veces, una por día. En la base, la última
   `ingest_run` de `reputacion_meli` es del **2026-09-27**: la reputación
   de Mercado Libre no se ingiere desde entonces.

5. **Qué lo rompió.** El requisito de `client_id`/`client_secret` vive en
   `MeliCredentials` desde `feat(reputacion-01) ... A.4 (+0026)` (2026-09-08,
   commit `3693585`; el `.bak` del archivo es de ese mismo día) y la ingesta
   funcionó con él hasta el 2026-09-27. El 2026-09-27 17:33 UTC el archivo
   quedó (re)escrito con la respuesta OAuth pelada, sin las dos claves, y la
   cadena murió. Reconstruir `client_id`/`client_secret` en
   `meli_tokens.json` es cosa del dueño; ningún script de este repo lo
   escribe.

## Qué falta para correr M.0

Únicamente credenciales vivas: `meli_tokens.json` con `client_id` y
`client_secret` de la aplicación de Orbit y un `access_token` vigente (o un
`refresh_token` válido con esos dos datos: `ClienteMeli` refresca solo tras
un 401 y reescribe el archivo dentro del contenedor, que es el único lugar
donde la sonda corre). Con eso, la sonda de esta carpeta corre tal cual:

```
ssh goncloud 'docker exec -i orbit-app-1 python -' \
    < docs/evidencia/repricing-02/ejecucion/M.0/sonda_meli.py
```

## La sonda (lista, sin correr)

`sonda_meli.py`: una consulta por pregunta sobre `ClienteMeli.get`
(GET-only), corre dentro de `orbit-app-1`, y antes de imprimir sanea cada
respuesta (ids de publicación, vendedor y aplicación, títulos, enlaces,
imágenes y SKUs redactados; repo público). Rutas que usará, una por pregunta
más las de contexto:

- `/users/me` — solo para el id del vendedor (redactado).
- `/users/{vendedor}/items/search?search_type=scan&limit=50` — candidatos.
- `/items/{id}` — una publicación con variantes y una sin (preguntas 1 y 3).
- `/pricing/llm/listing-charges?item_id=…&price=…&quantity=1` — pregunta 2.
- `/pricing/llm/listing-charges/tables?category_id=…&listing_type_id=…&price=…&quantity=1`
  — pregunta 2, por precio, categoría y tipo de publicación.
- `/items/{id}/visits/time-window?last=7&unit=day` — visitas (tráfico, D10).
- `/users/{vendedor}/applications/{aplicacion}` — pregunta 4: los scopes
  que la aplicación declara para este vendedor (nunca escribiendo).

Las cuatro conclusiones (de dónde sale el SKU por variante, si la consulta
de cargos separa porcentaje y fijo, si el precio va por publicación o por
variante, y si el token permite escribir) quedan **pendientes** de esa
corrida. Ningún universo se registra sin cotizador por esta frenada: eso lo
decide la corrida cuando exista.
