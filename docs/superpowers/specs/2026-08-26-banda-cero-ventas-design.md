# Banda de cero ventas + guarda de entidad sin trafico — PROPUESTA (no aprobada)

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
