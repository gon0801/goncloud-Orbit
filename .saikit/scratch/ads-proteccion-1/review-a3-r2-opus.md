# Re-review A.3-R2 — draft 340 @ 336afd3 (opus, independiente)

Revisor: `claude -p --model opus`, NO autor del cambio. Fecha: 2026-09-24.
Objeto: `origin/feat/ads-proteccion-a3` @ `336afd3` contra `origin/master`
`f31058d`. Ronda 1: NO APROBADO (2 bloqueantes). Metodo: copia via
`git archive` en /tmp, `.venv` del repo, Postgres local, gh api.

## B1: resuelto

- `tests/test_precio_pantalla.py:554` agrega `"ads_ingest"` al conjunto.
- Corrida Quality `36084399204` (workflow_dispatch sobre `336afd3`):
  success; jobs rapido/gate/completa/pesada success. Logs: completa
  3322 passed sin fallos ni skips; pesada verify/ 20 passed (regla del
  repo); rapido 107 passed.
- Mutante M8 (quitar `ads_ingest` de `api_dashboard.py:936`): muere.

## B2: resuelto

- `main()` (`salud.py:328`) imprime `ads-salud chequeo=...` en cada corrida
  (tambien omitido pre-10:30); fallo va a stderr. Cron captura ambas a
  `ads-salud.log`. Cierra tambien obs 5 de R1 (10:00/10:10/10:20).
- `test_ads_salud.py` + `test_precio_pantalla.py`: 86 passed, 0 skipped.
- `definiciones.md`: terminal/superseded/cadencia/recovery-agotada
  definidos. Fecha `caso.md` corregida a 24-sep (obs 2 R1).

## Mutantes

M1/M2/M4/M5/M7/M6b/M8 mueren. M6 sobrevive (conteo scoped sin cubrir,
ver obs 3). M3 sobrevive (global inalcanzable, ya en R1).

## Merge f31058d: limpio

`git merge-tree` limpio; imports sin ciclo; `SOURCE` unico (`salud.py:19`,
importado en `reports.py:236`); 0040 (master) -> 0041 (nueva); ruff limpio.
Numeros 0011/0031 repetidos preexisten en master (reversas), no del PR.

## Observaciones (no bloqueantes)

1. `definiciones.md` describe un camino imposible: recovery agotada no puede
   completarse "en un reintento posterior" (`_SQL_PENDIENTES` exige
   `recovery_attempts < 6`); solo la cierra superseded. Borrar la frase.
2. Falta definir aviso inicial agotado: 6 intentos sin acuse -> abierto sin
   avisar hasta el proximo exito, que lo cierra sin recovery.
3. Hueco de prueba M6: `episodios_abiertos` scoped sin cubrir (el test solo
   pasa por global). Va a fila del plan.
4. Test de heartbeat de `main()` depende de la hora (acepta ambos caminos).
5. R1 pendientes: M3 global inalcanzable, body PR desactualizado, 0041 sin
   bloque `DO $$` de privilegios.

VEREDICTO: APROBADO CON OBSERVACIONES
