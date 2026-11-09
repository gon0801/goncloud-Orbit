# Contribucion

Contribucion muestra, por plataforma, el rango pre-cargos (sin halo .. con halo)
de cada campana en la ventana madura de 90d con vintage D-15 UTC. El rollup
suma hijas `keyword` / `product_target`; la campana no publica fila propia.
Sin JS: tabla server-rendered. No es decisoria (etiqueta fija).

## Sub-features

- `contribucion-nav` abre `/contribucion` desde el nav y marca `aria-current="page"`.
- `contribucion-vacio` sin hijas con metrica madura muestra `Sin campañas con actividad madura en la ventana.`
- `contribucion-rango` si hay fila, el rango se ve como `contrib_sin_halo .. contrib_con_halo` (con miles y 2 decimales, `1,234.50 .. 1,500.00`) con moneda y etiqueta; ausencia es `—` mas motivo en español, nunca 0.
- `contribucion-api` `GET /api/dashboard/contribucion` es el mismo snapshot.

## How to get to it (user POV)

- Elegir `Contribucion` en el nav (`<a href="/contribucion">`).
- Abrir `http://127.0.0.1:<puerto>/contribucion`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla baseline: `Campana A` + ad_group, **sin** hijas keyword/product_target
  con metrica madura. El rango con numeros exige catalogo + ledger + FX
  (`tests/test_ui_contribucion.py`); eso no va en este fixture (regla 3).

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS -D - "$BASE/contribucion"`. Status 200. El HTML contiene `data-pantalla="contribucion"`, `h2` `Contribución por campaña antes de cargos (90 días cerrados)`, `href="/contribucion"` junto a `aria-current="page"`, y bloques `Amazon US` / `Amazon MX` (`h2` por plataforma).
- **Leer el vacio de la semilla.** El HTML contiene `Sin campañas con actividad madura en la ventana.` No afirma un rango numerico contra esta semilla.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/contribucion"`. Status 200. `plataformas.amazon_us.filas` y `plataformas.amazon_mx.filas` son listas vacias. `plataformas.amazon_us.ventana.desde` / `.hasta` estan presentes (D-15-89 y D-15 UTC). No hay `ventana` en la raiz del JSON.
- **Proof.** Guarda HTML de `/` y `/contribucion` mas el JSON bajo `evidence/<run_id>/contribucion/`.

## Gotchas

- Un 500 aqui suele ser migracion 0006/0007 ausente (`v_contribucion_entidad` no existe). No es "tabla vacia".
- Hueco / ausencia se pinta `—` con motivo (`tipo de anuncio que no entra al cálculo`, `sin precio`, `catálogo incompleto`, `faltan días en la serie`, `sin tipo de cambio`, …; vienen de `motivo_contribucion` en `app/etiquetas.py`), jamas `0` ni `0.0000`.
- `fx_source=nearest_prior` se ve como chip `Tipo de cambio aproximado: del último día con dato` (`exact` sería `del día`; no solo `Tipo de cambio aproximado`). La semilla baseline no lo ejercita.
- Multilisting (`precio_min_multilisting`) pinta chip `precio min multilisting` cuando aplica; la semilla baseline no lo ejercita.
- La etiqueta visible es `contribucion pre-cargos · no decisoria` (`ETIQUETA_CONTRIBUCION` en `app/dashboard_contribucion.py`).
