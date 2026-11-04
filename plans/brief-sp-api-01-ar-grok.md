# Brief para Grok: SP-API 01 A.R — revisión independiente de la Fase A

**SHA a revisar: `958c00f`** (`origin/master` tras el PR #248). Todo el código
de la Fase A está mergeado ahí. Fija ese SHA al empezar (`git fetch && git
checkout 958c00f`) y que tu veredicto hable de él: si master avanza mientras
revisas, tu APPROVE sigue siendo sobre `958c00f`.

Contrato: fila **A.R** de `plans/sp-api-01.md`. Reglas de diseño innegociables
y trampas del dominio: `docs/CONTEXTO.md` (léelo antes de empezar). Tú **no
implementas**: A.R es revisión.

## Qué se revisa

`app/spapi/*` (cliente, orders, pricing, listings, inventario, salud),
`app/notifica.py` (solo lo nuevo de A.5), `app/api_dashboard.py::salud`,
`app/templates/salud.html`, `migrations/0030`–`0037`, la propuesta de cron de
`docs/DEPLOY.md`, y los tests de todo lo anterior.

## Los seis ejes del contrato (son los criterios de aceptación de A.R)

1. **Guard default-deny** del cliente SP-API: allowlist de método/host/path,
   sin forma de salirse por traversal, encoding de barra, query embebida ni
   SKU/ASIN mal validado. Cero escrituras a Amazon: confirma que no existe
   ningún verbo que no sea GET en el camino SP-API.
2. **Un solo refrescador LWA** (decisión D5). Antes de la Fase A había dos ad
   hoc (`app/estimacion_fees.py`, `app/publicacion_fotos.py`). Verifica que
   quedó uno solo y que los migrados no cambiaron de comportamiento.
3. **Append-only**: las tablas de observación y sus triggers. Que no exista
   UPDATE/DELETE posible desde los roles de aplicación.
4. **Dinero**: moneda sellada junto al importe, sin conversiones implícitas,
   sin redondeos que muevan totales.
5. **Redacción**: ningún secreto ni credencial en logs, errores, `ingest_run.
   skip_reason` ni mensajes de Telegram.
6. **Sin PII**: Orders no debe persistir dirección ni destinatario.

Además: **ningún hallazgo bloqueante abierto** al cerrar.

Tu fuerte es infra/ops, así que mira con especial cuidado la **propuesta de
cron** de `docs/DEPLOY.md` y el **orden de deploy** (`0035` → `0036` → `0037`
→ rebuild): son lo que A.6 va a ejecutar contra producción.

## Contexto honesto (dónde apretar más)

- **A.5 se mergeó incompleta.** El PR #246 entró con 5 bloqueantes vivos que
  la review del lead ya había encontrado; el arreglo es el PR #247 y los nits
  finales el PR #248. Esa es la zona con más historia reciente y donde más
  vale tu mirada fresca: `app/spapi/salud.py`, el flanco de alertas, y el
  manejo transaccional de `evaluar_alertas`.
- **Producción está en 0031-0034.** `0035`, `0036` y `0037` NO están
  aplicadas. Nada de A.4 ni A.5 está en vivo.
- Hubo un bug de producción por esta vía: los GRANT de UPDATE sobre
  `ingest_run` son **por columna**, y una migración agregó una columna sin el
  suyo (0033 → hotfix 0034); dos corridas quedaron abiertas. Busca si queda
  alguna otra asimetría del mismo tipo.

## Residuales ya declarados (no los re-litigues; solo di si te parecen aceptables)

- En `pricing` y `listings`, una corrida que muere **solo** por red (sin
  status HTTP) sella `contrato` en vez de `red`. Decisión de taxonomía
  diferida, documentada en `docs/evidencia/sp-api-01/A.5/reporte.md`.
- `import app.spapi.salud` arrastra `app.ads.config` a `sys.modules` vía
  `app/notifica.py` (módulo inerte: constantes y dataclasses, cero IO). Está
  pineado con allowlist explícita en un test de subproceso.

## Reglas duras de tu trabajo

- **Solo lectura.** Cero escrituras a Amazon, cero corridas contra producción,
  cero ssh de escritura, cero cambios de código, cero tracker, cero merges.
- Puedes correr la suite localmente y usar sondas de **lectura** si las
  necesitas (`tools/sonda_spapi.py`).
- **No confíes en los reportes de evidencia: verifica contra el código.** Ya
  hubo un caso donde el reporte afirmaba una propiedad ("no importa
  `app.ads`") que en runtime era falsa.
- **Audita el poder discriminante de los tests, no el verde.** Es la debilidad
  recurrente de este repo: en A.5, 21 de 29 mutantes sobrevivían a una suite
  completamente verde. Muta y reporta qué sobrevive. Si mutas, restaura: el
  árbol debe quedar limpio (`git status` vacío) y sin commits tuyos.

## Entrega

`docs/evidencia/sp-api-01/A.R/informe.md` con:

- **Veredicto explícito sobre `958c00f`**: `APPROVE` o `CHANGES`.
- Un apartado **por cada uno de los seis ejes**, diciendo qué verificaste y
  cómo (comando y salida), no solo la conclusión.
- Hallazgos con **archivo:línea**, severidad, y el **escenario concreto de
  falla** (entradas → resultado incorrecto). Nada de estilo ni de gusto.
- Lo que auditaste por mutación y qué sobrevivió.

Pásame el informe pegado en el chat, o abre un PR con **solo** ese archivo
(carril `fast`). El lead cierra la fila A.R; tú no la marques.
