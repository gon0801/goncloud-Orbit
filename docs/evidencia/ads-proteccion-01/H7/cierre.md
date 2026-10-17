# H7 — cierre (DRAFT, foto 2026-09-26; se actualiza al cierre real)

Requisito del runbook H7: ledger del plan (decisiones, gos con cita, SHAs,
fechas UTC), AppFlowy a `Done`, y `plans/ads-proteccion-01.md` con estados
finales en PR de cierre con CI verde (go de merge). Lo no bloqueante pendiente
va a fila del plan y se nombra en el PR; un bloqueante nunca se mergea abierto.

## Estado por requisito (foto; no es el cierre)

- [ ] Ledger completo: parcial (C.0/C.2b/C.5/D.1/H5/H6 documentados; faltan
  A.4, B.4, C.6 y sus gos).
- [ ] AppFlowy a `Done`: pendiente (sigue trabajo abierto A.4/B.4/C.6).
- [ ] Plan con estados finales: pendiente (A.4 TODO, B.4 WIP 1/5, C.2a TODO,
  C.6 TODO; resto 完了 con evidencia).
- [ ] PR de cierre con CI verde + go de merge: no abierto (bloqueado por lo anterior).

## Residuales no bloqueantes conocidos (van a fila del plan al cierre)

- A.3: M6 (`episodios_abiertos` scoped sin test), M3 (cancel global
  inalcanzable, mutante equivalente documentado).
- D.1b (no bloqueantes revision IA) y D.2 (anti-inversion de bids, decision
  del dueno) — ver `D.1/deploy.md` Pendiente.
