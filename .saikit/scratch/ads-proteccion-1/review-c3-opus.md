# Review C.3 — draft PR 341 @ a7d036a (opus, independiente)

Revisor: `claude -p --model opus`, NO autor del cambio. Fecha: 2026-09-24.
Objeto: `origin/feat/ads-proteccion-c3` @ `a7d036a` vs `origin/master`
`f31058d` (se ignora el brief C.2b para el veredicto de codigo). Metodo:
copia `git archive` en /tmp, `.venv` del repo, Postgres local.

## Bloqueantes

**B1 — Sin aislamiento off-by-default: el deploy activaria la PAUSE
economica.** `cycle.py:1522` pasa `policy_version=POLITICA_PAUSE_ECONOMICA`
siempre; `apply_cola.py:702` la aplica al resolver target. El flag B.2a
solo controla cooldown. Repro: `_corre_hoja(corte=<105/100/1>,
flag=False, bid_confirmado=None)` -> `pause_economica`; en live se
aplicaria a las 48h. Tambien cambia filas ya en cola al desplegar.

**B2 — Los 2 tests rotos en local estan rotos de verdad.** La ventana
termina en min(max_fecha-3, hoy-10): el fixture (d-17..d-11) deja 4
fechas, costo 55, agregado incompleto, `exceso=None` -> `ya_no_califica`.
CI no lo vio (completa/pesada skipping en draft). El codigo de cola esta
bien (con fixture d-20..d-11 y Decimal, ambos pasan); los tests no prueban
el DoD.

**B3 — Test del 3x estricto no discrimina.** Mutante `>` -> `>=` sobrevive:
el caso ("60","100") tiene exceso 40 < 80 y se descarta antes del 3x.
Fix: agregar ("120","200") esperando None (cociente == 3x, exceso 80).

## Observaciones (no bloquean)

1. Hoja que empeoro y califica regla antigua se descarta a `ya_no_califica`
   aunque siga cruzando (spec: cancelar solo si ya no cruza); retrasa corte
   48h+. M6 sobrevive.
2. Sin cubrir: goal enabled=false/mode=off (M8), peldano de margen (M12),
   cost/revenue negativos (M11).
3. Evidencia de revalidacion solo en notes del ciclo ejecutor; se pierde
   con ApplyAbortado; sin test a nivel ciclo.
4. Ciclo sin notes.target: filas economicas descartadas para siempre en
   vez de esperar (el siguiente ciclo las repropone; sin peligro).
5. "multiplicador": "3" a mano en 2 lugares; usar MULT_PAUSE_ECONOMICA.
6. Proceso: C.2b pendiente bloquea el merge igual; sin bateria completa en CI.

## Lo verificado OK

Regla economica del spec (3x estricto, exceso >=, revenue=0 cuenta,
None se abstiene, moneda+madurez; M1/M3/M4 mueren; orden correcto);
venta tardia recalculada (M7 muere); target vigente + procedencia,
`target_no_confiable` sin cobro; policy congelada en inputs (M9/M10);
readback/reversa por camino existente; dinero sin duplicar ni ahorros;
ruff limpio; 868 pasan salvo los 2 de B2.

VEREDICTO: NO APROBADO
