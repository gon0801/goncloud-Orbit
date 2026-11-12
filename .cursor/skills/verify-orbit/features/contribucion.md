# Contribucion

Contribucion muestra, por plataforma, el rango pre-cargos (sin halo .. con halo)
de cada campana en la ventana madura de 90d con vintage D-15 UTC. El rollup
suma hijas `keyword` / `product_target`; la campana no publica fila propia.
Sin JS: tarjetas y tabla server-rendered. No es decisoria (etiqueta fija).

## Sub-features

- `contribucion-nav` abre `/contribucion` desde el nav y marca `aria-current="page"`.
- `contribucion-resumen` una tarjeta por plataforma (`section.contribucion-plataformas`,
  `article.contribucion-tarjeta#contribucion-card-amazon_us` / `-amazon_mx`) con
  moneda, numero de campanas, rango de fechas, barra segmentada
  (`div.contribucion-segmentos`), conteo por clase (`ul.contribucion-clases`) y
  la leyenda `contribucion pre-cargos · no decisoria · MONEDA · el tipo de cambio
  aproximado se marca en la fila`.
- `contribucion-clases` cada fila y cada segmento llevan `contribucion-pos`,
  `contribucion-cruza`, `contribucion-neg` o `contribucion-sin`; el conteo de la
  tarjeta usa `contribucion-seg-<clase>` y `contribucion-ancho-<n>`.
- `contribucion-tabla` una tabla real por plataforma
  (`table.contribucion-tabla`) con columnas Campana, Contribucion (MONEDA),
  barra de rango y Datos.
- `contribucion-barra` `td.contribucion-col-barra` dibuja `span.contribucion-barra`
  con `span.contribucion-rango` o `span.contribucion-punto` cuando minimo = maximo;
  una fila sin dato no dibuja barra. La linea en 0 es `span.contribucion-linea-cero`.
  La fila recortada por la escala muestra `span.contribucion-real` con
  `▸ valor real: <monto>`.
- `contribucion-eje` el encabezado de la columna de barra lleva
  `span.contribucion-eje` con `.contribucion-eje-min`, `.contribucion-eje-cero`
  y `.contribucion-eje-max`; el cero se posiciona con `contribucion-izq-<n>`.
- `contribucion-vacio` sin hijas con metrica madura muestra `Sin campañas con
  actividad madura en la ventana.` y conserva la tarjeta de la plataforma.
- `contribucion-api` `GET /api/dashboard/contribucion` es el mismo snapshot y no
  cambia su forma.

## How to get to it (user POV)

- Elegir `Contribucion` en el nav (`<a href="/contribucion">`).
- Abrir `http://127.0.0.1:<puerto>/contribucion`.
- Leer las tarjetas de Amazon US y Amazon MX y bajar a la tabla de cada una.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla baseline: `Campana A` + ad_group, **sin** hijas keyword/product_target
  con metrica madura. El rango con numeros exige catalogo + ledger + FX
  (`tests/test_ui_contribucion.py`); eso no va en este fixture (regla 3).

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS -D - "$BASE/contribucion"`. Status 200. El HTML contiene
  `data-pantalla="contribucion"`, el `h1` `Contribución por campaña`,
  `section.contribucion-plataformas`, dos `article.contribucion-tarjeta`, el
  `href="/contribucion"` con `aria-current="page"` y la leyenda de no decisoria
  cuando hay filas.
- **Leer el vacio de la semilla.** El HTML contiene `Sin campañas con actividad
  madura en la ventana.` No afirma un rango numerico contra esta semilla.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/contribucion"`. Status 200.
  `plataformas.amazon_us.filas` y `plataformas.amazon_mx.filas` son listas vacias.
  `plataformas.amazon_us.ventana.desde` / `.hasta` estan presentes (D-15-89 y D-15 UTC).
  No hay `ventana` en la raiz del JSON.
- **Proof.** Guarda HTML de `/` y `/contribucion`, el JSON y capturas 1440/390 bajo
  `evidence/<run_id>/contribucion/`.

## Gotchas

- Un 500 aqui suele ser migracion 0006/0007 ausente (`v_contribucion_entidad` no existe). No es "tabla vacia".
- Hueco / ausencia se pinta `—` con motivo (`tipo de anuncio que no entra al cálculo`, `sin precio`, `catálogo incompleto`, `faltan días en la serie`, `sin tipo de cambio`, …; vienen de `motivo_contribucion` en `app/etiquetas.py`), jamas `0` ni `0.0000`.
- `fx_source=nearest_prior` se ve como chip `Tipo de cambio aproximado: del último día con dato` (`exact` sería `del día`; no solo `Tipo de cambio aproximado`). La semilla baseline no lo ejercita.
- Multilisting (`precio_min_multilisting`) pinta chip `precio min multilisting` cuando aplica; la semilla baseline no lo ejercita.
- La etiqueta visible es `contribucion pre-cargos · no decisoria` (`ETIQUETA_CONTRIBUCION` en `app/dashboard_contribucion.py`). Aparece una vez en la tarjeta y otra en el encabezado de su tabla; nunca por fila.
- En movil la tabla se desplaza dentro de `.tabla-scroll`; el documento no debe
  desbordar a 390 px (lo comprueba `docs/evidencia/disenos-01/C/mide_390.py`).
- El estilo va por clases en `app/static/css/contribucion.css`; la CSP
  `default-src 'self'` prohibe `style` inline.
