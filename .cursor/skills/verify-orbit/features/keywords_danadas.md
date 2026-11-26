# Keywords dañadas (`/keywords-danadas`)

Detecta keywords que vendían y perdieron visitas después de recortes de puja. Permite regresar una o todas a la puja anterior con confirmación, actor y token.

## Sub-features

- `danadas-nav` entra desde Resumen por `Oportunidades` y después `Keywords dañadas` en `nav.subnavegacion`. El padre usa `aria-current="true"`; `/keywords-danadas` en la subnavegación y `Oportunidades` en la tab bar usan `aria-current="page"`.
- `danadas-mercado` cambia por GET entre Amazon MX y US.
- `danadas-regresos` conserva `section.oportunidad-resumen.superficie`, `section.oportunidad-contenido`, `section.oportunidad-detalle`, `button[data-regresar]`, `form[data-regreso]`, `form[data-regresar-todas]` y `details` de ya regresadas. `/static/js/danadas.js` cablea los formularios sin handlers inline.
- `danadas-vacio` sin candidatas dice `Sin keywords dañadas en este mercado: nada que regresar.` Si todas se regresaron dice `Ninguna pendiente: todas ya se regresaron.` La semilla baseline está vacía.

## Driving it with curl

```bash
curl -sS "$BASE/"
curl -sS "$BASE/keywords-danadas?plataforma=amazon_us"
curl -sS "$BASE/api/dashboard/keywords-danadas?plataforma=amazon_us"
```

Las tres rutas responden 200 con `doctor` verde. El JSON gemelo `GET /api/dashboard/keywords-danadas` contiene `hojas`, plataforma y fecha de cálculo; sus importes conservan moneda.

## Operaciones prohibidas en baseline

No envíes `POST /api/ads-optimizer/bid/regresar` ni `POST /api/ads-optimizer/bid/regresar-todas`: cambian pujas en Amazon y exigen `x-orbit-token`. Tampoco simules esos POST con datos de producción.
