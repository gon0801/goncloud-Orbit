# Orbit — Rediseño de la página Decisiones (`/decisiones`)

Archivos que acompañan este documento:
- `decisiones-diseno.png` — cómo debe verse (1440 px de ancho, grupos plegados).
- `decisiones-diseno.dc.html` — el diseño. **No es HTML para pegar tal cual**: es el formato de un editor de diseño (`<x-dc>`, `{{holes}}`, `<sc-for>`, `<sc-if>`, clase `Component` con `renderVals()`). Úsalo como referencia exacta de estructura, textos, colores, tamaños y espaciados (estilos inline).
- Mismo sistema visual que el Resumen y Campañas: ver `INSTRUCCIONES-resumen.md` (tipografías, colores, sidebar, responsive).

## Reglas generales
1. **Solo cambia la presentación.** Mismos datos y misma paginación que hoy (`?page=N`, 50 por página, "anterior / página N de M / siguiente", total de decisiones).
2. Los datos del diseño son los reales de la página 1 (50 decisiones, 31 oct – 11 nov) y están fijos en el script como muestra.
3. No se pierde ningún campo de la tabla actual. Correspondencia:
   - **Entidad** → texto en negritas (ej. "búsqueda amplia", "wedding arras", "B0B36NHWY5") + "· campaña".
   - Detalle "#3840 · decision #2903" → línea gris pequeña.
   - **Kind** → chip de tipo (ver regla).
   - **Search term** → se añade a la línea gris como "search term: …" cuando no es "—".
   - **Motivo** → texto bajo la entidad.
   - **Target usado** → "Target usado X %" a la derecha (o "—").
   - **Bid** → "antes → **después** MONEDA" a la derecha, solo si hay cambio de puja.
   - **Decidida** → la fecha agrupa (encabezado del día) y la hora va a la derecha de cada fila.

## Estructura
1. **Encabezado**: "N decisiones · de la más reciente a la más antigua", título "Decisiones", paginación arriba a la derecha (y repetida al final).
2. **En esta página**: 4 contadores de las decisiones de la página actual: Pausas, Pujas subidas, Pujas bajadas, Negativos. Rango de fechas de la página.
3. **Filtro por tipo y buscador** — **NUEVO**, hoy no existe. Definir con el dueño si filtran solo la página actual o todas las decisiones; lo segundo requiere parámetros en el backend.
4. **Línea de tiempo por día**: encabezado con la fecha y "N decisiones · X pausas · Y pujas subidas · …"; debajo una tarjeta con las decisiones del día.
   - Las decisiones **sin cambio de puja** (Pausa, Negativo) del mismo día con el **mismo kind y el mismo motivo** se juntan en una fila plegable: "31 pausas · 6 entidades en 10 campañas" + motivo + "Ver las 31 decisiones". Al abrirla se listan todas con entidad, campaña e ids.
   - Las **pujas nunca se agrupan**: cada una muestra su bid antes → después.

## Regla de tipo (chip)
- **Puja ↑**: bid después > bid antes. Chip `#CCFBF1` / texto `#115E59`; valor nuevo en `#0F766E`.
- **Puja ↓**: bid después < bid antes. Chip `#FEF3C7` / texto `#92400E`; valor nuevo en `#B45309`.
- **Pausa**: chip `#E5E7EB` / texto `#1F2937`.
- **Negativo**: chip `#FFEDD5` / texto `#9A3412`.
- Si existen otros kinds en el backend que no aparecen en la página 1, **preguntar** cómo mostrarlos; no inventar colores ni nombres.

## Antes de desplegar
- Comparar una página contra la tabla actual: mismo número de decisiones y mismos valores.
- Probar una página donde un día quede partido entre dos páginas (el encabezado del día debe aparecer en ambas).
- Revisar en móvil.
