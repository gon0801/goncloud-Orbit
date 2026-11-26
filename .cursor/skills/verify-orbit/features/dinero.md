# Dónde poner el dinero (`/donde-poner-el-dinero`)

Dónde poner el dinero reparte el gasto de las campañas encendidas por tipo y por ubicación, y deja cambiar el presupuesto diario, el ajuste por ubicación y el gasto fuera de Amazon. El HTML llega server-rendered; los planes y la escritura van por `/api/ads-optimizer/campana-ajuste/*` con JS. En el baseline de esta skill solo se verifica la lectura: POST aplicar escribe en Amazon.

## Sub-features

- `dinero-nav` abre `/donde-poner-el-dinero` desde el sidebar y marca `aria-current="page"`. El `<body data-pantalla>` es `donde-poner-el-dinero` y el `h1` dice `Dónde poner el dinero`. Los enlaces `Amazon MX` / `Amazon US` cambian de mercado por GET.
- `dinero-tipos` `section.rendimiento-resumen.superficie` agrupa la explicacion, la ventana y la tabla de gasto, parte del gasto, pedidos, venta y ACoS por tipo de campaña, con sus totales. Los enlaces de mercado quedan fuera del resumen; debajo siguen `Por ubicación (30 días)` y `Por campaña`.
- `dinero-por-campana` una fila por campaña con presupuesto diario, gasto medio, uso del presupuesto, estrategia, ajustes por ubicación, gasto fuera de Amazon y el botón `Cambiar presupuesto` (`button[data-ajuste-clase="presupuesto"][data-campana-id]`). Sin campañas con configuración vigente la tabla queda vacía.
- `dinero-propuesta-temporada` la columna `Temporada` muestra la propuesta de presupuesto del bloque G (`propuesta_temporada.frase`: baja, fin de baja o alta sin presupuesto, con el monto absoluto). Con propuesta trae `button[data-propuesta-presupuesto]` (texto `Revisar`) que abre el form `presupuesto` de esa campaña, escribe el monto propuesto en `input[name="presupuesto"]` y pide el plan; la aprobación sigue siendo el literal `APLICAR AJUSTE` del dueño. Sin propuesta la celda dice `—`.
- `dinero-ajuste` el botón abre `form#ajuste-<campana_id>-<clase>[data-ajuste-form]`, oculto por defecto. El form pide el monto (`input[name="presupuesto"]` en la clase presupuesto), `Ver plan` (`[data-ver-plan]`), `Actor`, `Token`, el literal `APLICAR AJUSTE` y `Aplicar` (`button[type="submit"]`, nace deshabilitado). `Ver plan` hace `GET /api/ads-optimizer/campana-ajuste/plan?campana_id=<id>&clase=presupuesto&presupuesto=<monto>` (sin token, solo lectura) y pinta `frase` en `[data-frase]`, guarda la huella y habilita `Aplicar`. Sin el literal exacto no sale el POST; `Aplicar` manda `POST /api/ads-optimizer/campana-ajuste/aplicar` con `{campana_id, clase, presupuesto, huella, confirmacion, actor}` y el token en `x-orbit-token`. Un error se pinta como `Error: <motivo>.`; 200 dice `Ajuste aplicado.`.
- `dinero-presupuesto-alto` (ARREGLOS 04 W-N): un presupuesto de más del doble del que esa campaña tiene hoy ya no se rechaza. La previa trae `aviso_presupuesto` y el form lo pinta en `p[data-aviso-presupuesto][role=alert]`: `Vas a poner <cifra> <moneda> diarios. Es más del doble de lo que esta campaña tiene hoy (<antes> <moneda>).`; sin presupuesto anterior cierra con `Esta campaña no tiene un presupuesto anterior para comparar.`. Bajo el literal aparece `label[data-casilla-presupuesto]` con `input[data-acepto-presupuesto-alto]` y el texto `Sí, quiero ese presupuesto`; sin aviso no se ve. Con aviso y sin marcar, `Aplicar` no manda nada y dice `Marca «Sí, quiero ese presupuesto» para continuar`. Cada plan nuevo deja la casilla sin marcar y esconde el aviso si no aplica. El POST lleva `acepto_presupuesto_alto: true` solo marcada; sin el, el servidor responde 422 con `Falta confirmar el presupuesto alto: recarga la página, vuelve a ver el plan y marca «Sí, quiero ese presupuesto»`. Justo el doble no avisa; el aviso no entra en la huella.
- `dinero-regresar` los ajustes reversibles traen `button[data-regresar-ajuste]` y `form[data-regresar-form]` que manda `POST /campana-ajuste/<ajuste_id>/regresar` con actor y token. Regresar no pasa por el aviso: restaura un valor que ya estuvo puesto.
- `dinero-api` `GET /api/ads-optimizer/campana-ajuste/plan` es lectura sin token; `POST /api/ads-optimizer/campana-ajuste/aplicar` y `/regresar` piden `x-orbit-token`.

## How to get to it (user POV)

- Elegir `Dónde poner el dinero` en el sidebar (`<a href="/donde-poner-el-dinero">`).
- Cambiar de mercado con `Amazon MX` o `Amazon US`.
- En `Por campaña`, pulsar `Cambiar presupuesto`, escribir el monto y `Ver plan`; leer la frase (y el aviso, si es más del doble de lo que tiene hoy), escribir `APLICAR AJUSTE`, marcar `Sí, quiero ese presupuesto` cuando aparezca y `Aplicar`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla baseline: la campaña `Campana A` (amazon_us) no tiene fila en `ads_campana_config_observation`, así que `Por campaña` no ofrece cambios y el plan de una campaña responde 422 `campana <id> sin configuracion vigente`. No se POSTea aplicar.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Abrir la pantalla.** Corre `curl -sS "$BASE/donde-poner-el-dinero?plataforma=amazon_us"`. Status 200. El HTML contiene `data-pantalla="donde-poner-el-dinero"`, `h2` `Dónde poner el dinero`, `href="/donde-poner-el-dinero"` junto a `aria-current="page"`, las tablas de tipo y de ubicación, y `Por campaña`.
- **Leer el plan sin escribir.** Con una campaña sembrada con presupuesto, corre `curl -sS "$BASE/api/ads-optimizer/campana-ajuste/plan?campana_id=<id>&clase=presupuesto&presupuesto=300"`. Status 200 con `frase`, `huella`, `antes`, `despues` y, si el monto pasa del doble del de hoy, `aviso_presupuesto` con `Es más del doble de lo que esta campaña tiene hoy`.
- **Proof.** Guarda HTML de `/` y `/donde-poner-el-dinero` bajo `evidence/<run_id>/dinero/`. No hay `drive-dinero` en esta skill.

## Gotchas

- El path es `/donde-poner-el-dinero`; `data-pantalla` es `donde-poner-el-dinero`, no `dinero`. No inventes `href="/dinero"`.
- No POSTees `/campana-ajuste/aplicar` ni `/regresar` en el baseline: escriben en Amazon con el token.
- Sin `ads_campana_config_observation` no hay botones de cambio ni plan; es el vacío esperado de la semilla, no un 500.
- Las cifras llevan miles y dos decimales (`20,000.00 MXN`); el `antes` de la base trae cuatro decimales y la frase lo muestra con dos.
