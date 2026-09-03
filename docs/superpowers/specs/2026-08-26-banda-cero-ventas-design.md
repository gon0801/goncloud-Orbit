# Banda de cero ventas + guarda de entidad sin trafico — APROBADA por el dueno el 2026-09-03 (A' + C; B no)

> **ESTADO: APROBADA (2026-09-03).** Lo de abajo es la propuesta original del
> 2026-08-26 tal cual se escribio; las decisiones selladas del dueno y los
> ajustes que cambian la propuesta van en esta seccion, que MANDA sobre el
> resto. Plan de implementacion: `plans/bids-01.md`.

## Decision del dueno (2026-09-03) — SELLADO

Disparador: la verificacion adversarial triple de ORBIT 05 (tarea 2.1, tres
revisores, 0 divergencias) mostro EN VIVO los dos huecos de esta propuesta:
8 de las 10 pujas aplicadas en MX el 2026-09-02 fueron -12% a entidades con
gasto y cero ventas, y 2 de ellas (2059 `arras matrimoniales`, 2078 `arras de
boda plata`) no tenian trafico desde junio (ajuste inerte que quemo 2/10
cupos). Mediciones del lead (regla 8, base viva, SELECT read-only):

- De las 123 decisiones de bid de los ciclos live 33/34, **103 eran de cero
  ventas** (39 MX, 64 US); clicks mediana 7 (MX) / 2 (US); 57 de esas 103
  tenian `window_end` de hace mas de 30 dias (inertes).
- Opcion A LITERAL (clicks >= umbral_pause/2 y cost >= piso de pausa) habria
  movido a -25% solo 1 de las 103. Con umbrales absolutos bajos (15 clicks)
  el dueno objeto: sus productos necesitan ~120 clicks por venta, y bajar
  al 10% de lo necesario para una venta es prematuro.
- Regla relativa al producto (clicks >= expected_clicks del grupo, CORTES
  01): 1 caso (US 1994: 111 clicks vs 92 esperados, 87 USD, 0 ventas).
- Hojas ENABLED (campana y grupo ENABLED) sin impresiones en 30d: **MX 172
  de 272, US 167 de 247** (14d: 183 / 170). Ninguna con ventas en 90d; 7 MX +
  33 US gastaron sin vender y murieron; 165 MX + 134 US sin nada en 90d.

**Decision 1 — literal del dueno: "si que puedan bajar 25%"**, con la regla
relativa al producto que eligio en la pregunta guiada: *"Si, con gasto >=
piso de pausa"*. Sellado (A'):

    orders == 0 AND ad_revenue == 0 (ventana de BIDS)
    AND expected_clicks del grupo NO es None (grupo con evidencia 3/60/14)
    AND clicks >= expected_clicks
    AND cost >= piso de pausa de la plataforma (40 USD / 500 MXN, CORTES 03)
    -> factor -25%, motivo `banda_menos_25_cero_ventas`

Antes de alcanzar los clicks esperados por venta sigue el -12% de hoy; al
1.5x (umbral_pause) sigue mandando el PAUSE (evaluado antes, sin cambio).
Grupo sin evidencia -> la regla no aplica (regla 3: nada de numeros
inventados). `expected_clicks` ya viaja congelado en `inputs.corte`; el
replay lo lee y las filas historicas sin la clave rejuegan igual.

**Decision 2 — literal del dueno: "las palabras sin trafico no se tendrian
que ver porque no tienen trafico y ver si subir bid o eliminar o algo"**,
concretado en la pregunta guiada como *"Reporte + herramienta de archivo por
lote"*. Sellado (C):

1. Guarda de ajuste inerte: hoja ENABLED (campana y grupo ENABLED — las de
   campana pausada ya las cubre CAMPANA ACTIVA 01) con CERO impresiones en
   los ultimos **N = 14 dias contados desde el watermark de metricas de la
   plataforma** (max(metric_date) en v_metric_latest; jamas desde hoy, para
   no confundir retraso de ingesta con inactividad) -> el ciclo NO propone
   bid; motivo cerrado `entidad_inerte` en los contadores. Fuente UNICA:
   vista SQL `v_entidad_inerte` (migracion), que tambien alimenta el reporte
   y la herramienta (regla 2).
2. Reporte por causa: pagina `/inertes` (server-rendered) + linea en el
   digest. Clasificacion: `gasto_sin_ventas` (gasto > 0 y 0 ordenes en 90d),
   `con_ventas_previas` (ordenes > 0 en 90d), `peso_muerto` (nada en 90d).
   Revivir (subir bid) NO es automatico: decision humana desde el reporte.
3. Herramienta de archivo por lote con REVERSA (regla 7): `tools/
   archiva_inertes.py`, dry-run por defecto, ejecuta solo con go literal del
   dueno y conteo esperado anti-typo; archiva keywords (v3 delete =
   ARCHIVED) con readback y ledger propio con la identidad completa
   (campana, grupo, texto, match type, bid) para poder REPONERLAS
   (`--reponer <lote>` recrea por POST /sp/keywords). Alcance v1: keywords;
   los product targets solo se reportan.

**Opcion B: NO por ahora** (queda la prueba de fuego empirica descrita
abajo). **Rechazos declarados**: se conservan.



> Propuesta del lead (2026-08-26) tras auditar las 627 decisiones shadow del
> motor (2026-08-24 a 2026-08-26). NO es spec aprobado: los numeros y la
> opcion los sella el dueno. Si se aprueba, nace plan propio (sugerido:
> BIDS 01) y NO bloquea ORBIT 04 Phase 3 ni el probe 2.5.

## Evidencia (base viva, 3 dias de shadow)

- 547 bids, de los cuales **456 (83%) son bajadas de solo -12% a entidades
  con CERO ventas en su ventana** (orders=0, ad_revenue=0). Ejemplos reales:
  113 clicks / $87.91 USD / 0 ordenes -> -12% (0.48 -> 0.42); 117 clicks /
  $50.82 / 0 ordenes -> -12%.
- Causa estructural verificada en `app/optimizer/bid.py` (`_factor_banda`):
  con `ad_revenue=0` la comparacion `cost > 1.15 * target * 0` se reduce a
  `cost > 0` -> siempre banda_menos_12. La banda -25% exige `orders >= 1`
  (`ORDERS_MIN_BAJA_FUERTE`): **por construccion, cero ventas jamas recibe
  mas que -12%**. A -12%/dia una keyword de $0.48 tarda ~12 dias en llegar
  al floor $0.10, gastando ~$4/dia mientras tanto.
- La via PAUSE si funciona para los peores casos (32 pauses el dia 24, ej.
  36 clicks/$26.91/0 ordenes), pero el umbral adaptativo (CORTES 01) es
  deliberadamente conservador (piso 100 clicks legacy desde CORTES 03,
  dueno 2026-08-28; este spec lo escribio con el piso 25 de CORTES 01, sube
  con la rotacion del producto): el sangrado moderado (60-115 clicks sin ventas en grupos
  de rotacion lenta) queda en tierra de nadie: no pausa, solo -12%.
- **Ventanas rancias**: muchas entidades tienen `window_end` 2026-06-23 —
  sin trafico desde junio (los datos frescos existen: los cortes usan
  ventanas al 14-19 ago). Ajustar el bid de una keyword que Amazon no esta
  sirviendo es inerte: ni dana ni ayuda, pero ensucia el digest y quema
  quota de la rampa si llegara a live.

## Lo que un experto haria distinto

1. Cero ventas con gasto acumulado real: corte agresivo o pause, no -12%.
2. Entidad sin trafico en semanas: decision explicita (revivir con subida o
   dejar morir), no ajuste periodico inerte.

## Opciones (el dueno sella una, o ninguna)

### Opcion A — Banda dura de cero ventas en el bid (minima)

Nueva regla en `_factor_banda`, ANTES de las bandas actuales:
`orders == 0 AND clicks >= K_bid AND cost >= piso plataforma` ->
factor **-25%** (o baja directa al floor). Sin kind nuevo, sin cola, sin
migracion: es un bid mas, con su cooldown y su rampa.
- `K_bid` candidato: `max(15, umbral_pause // 2)` (mitad del umbral de
  pause adaptativo — escala con la rotacion del producto, coherente con
  CORTES 01).
- Pros: 30 lineas, testeable puro, no toca la cola. Contras: sigue sin
  pausar; una entidad a -25%/dia llega al floor en ~7 dias.

### Opcion B — Umbral de pause secundario para cero ventas

Segundo umbral de pause estrictamente MENOR que el adaptativo para el caso
`orders == 0` sostenido: ej. `clicks >= max(25, umbral_pause * 0.6)` con
`cost >= PAUSE_COST_MIN`. Pasa por la cola con veto (ya existe), madurez
10d (ya existe).
- Pros: ataca la raiz (el sangrado se DETIENE). Contras: mas pauses a
  revisar en la ventana de veto; roza el piso sellado de CORTES 01 (el piso
  25 se respeta, pero el espiritu del adaptativo era subir, no crear una
  via paralela mas agresiva).

### Opcion C — Sin ajustes inertes + VISIBILIZAR, no silenciar (corregida por el dueno 2026-08-26)

Correccion del dueno (acertada): un profesional NO ignora una keyword sin
impresiones — la diagnostica, porque las causas son distintas (bid bajo que
pierde la subasta, campania pausada o sin budget, termino sin volumen,
historial rentable que perdio trafico). Diagnostico real contra la base
(2026-08-26): la MAYORIA de las entidades ENABLED sin impresiones en 30d
viven en campanias PAUSED (Wedding Coin - Asin Targeting: 177; Arras
Manual: 19; A1U: 16...) — la causa no es la keyword, es la campania. El
resto (decenas, en campanias ENABLED: AC, AU2, AGMX, AD_READY) son las
verdaderas "no se sirven" a diagnosticar caso por caso.

Sellado del rediseño:
1. **Guarda de ajuste inerte**: entidad cuya campania esta PAUSED (estado
   vivo de `ad_entity_state`, no el cache de metricas) o sin impresiones en
   los ultimos N dias (candidato N=14, de `ads_metric_observation`, regla
   3) -> el motor NO propone ajuste de bid. Nuevo motivo cerrado
   `entidad_inerte` con la CAUSA en inputs (`campania_pausada` /
   `sin_impresiones`), no-op auditable — jamas silencioso.
2. **Superficie de diagnostico**: seccion "entidades sin trafico" en el
   digest/dashboard (3.3/dashboard-01 la renderiza; el motor solo deja el
   motivo estructurado): clasificada por causa (campania pausada / sin
   impresiones con historial rentable = candidata a revivir / sin
   impresiones sin historial = peso muerto). El humano decide revivir o
   matar; el motor solo deja de hacer ruido y la pone en la mesa.
3. **Revivir NO es automatico en esta propuesta**: subir bid para probar
   subasta es decision humana por ahora (caso a caso desde el reporte).

### Recomendacion del lead

**A + C juntas, B NO (por ahora)**: A corta el sangrado 2x mas rapido sin
tocar la cola; C (rediseñada por el dueno) elimina los ajustes inertes y
CONVIERTE el sintoma en reporte clasificado por causa; B queda como via si
tras 2 semanas de shadow con A+C los ceros-ventas siguen gastando de mas.
La prueba de fuego de B es empirica: medir cuanto gasta el conjunto
orders=0 bajo A+C.

## Invariantes que cualquier opcion debe respetar

- Decimal exacto, comparacion por multiplicacion (prohibido dividir): la
  regla 4 y el patron de `_factor_banda` se conservan.
- Vocabulario cerrado de motivos: cada motivo nuevo nace con su test literal.
- El replay lee congelados de `inputs`: cualquier parametro nuevo
  (`K_bid`, `N`) viaja en `inputs` de la fila decision.
- Madurez: bids no la exigen (no son cortes, regla 6); si se elige B, el
  pause secundario hereda la madurez y la cola de veto existentes.
- TDD con rojo previo (regla 9) y evidencia contra la base viva (regla 8):
  re-correr el analisis de las 627 decisiones tras el cambio y publicar el
  antes/despues por motivo.

## Rechazos declarados

- **Subir el -25% quitando `ORDERS_MIN_BAJA_FUERTE`**: cambia tambien el
  comportamiento con ventas (orders>=1 ya lo satisface); el sellado
  distingue los dos mundos y no se toca sin evidencia de que falla.
- **Baja directa al floor como default**: demasiado agresiva para productos
  de conversion lenta (caso arras documentado en CORTES 01).
