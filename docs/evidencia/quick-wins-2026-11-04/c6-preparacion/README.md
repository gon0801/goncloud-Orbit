# C6 — preparacion, 4-nov-2026

Resultado: C6 listo para comenzar por lectura RO; **no se activo proteccion
economica ni se inicio sombra**. Plan existente: [apendice C6](../../../../plans/ads-proteccion-01.md#preparacion-vigente-de-c6--4-nov-2026).
No hay cambio de producto: Spec skip reason registrado en ese apendice.

## Evidencia reutilizada y alcance

- [A.4/B.4](../alertas-pausa/resultado.md): siete dias de ingesta/cron,
  doce recovery soportados y PAUSE 2736 causal durante cooldown BID,
  conciliada con Amazon. No prueban corte economico.
- [Fotografia productiva](../../estado-planes-2026-11-04/produccion.json),
  5-nov 00:32 UTC: config 42, flag economico ausente, flag sin cooldown true.
  Revalidar al ejecutar; no se hizo otra lectura remota en esta preparacion.
- [C.2b](../../ads-proteccion-01/C.2b/aceptacion.md): horizonte 10d,
  tolerancia 0, sin piso propio. No es go H6.4 ni live.
- [H6 historico](../../ads-proteccion-01/H6/estado.md),
  [runbook H6](../../../runbooks/ads-proteccion-01.md#h6--c2b-c3-c4-c5-y-c6-proteccion-economica)
  y [BIDS 02](../../../../plans/bids-02.md): no usar IDs/config de septiembre.
  C6 conserva cinco ciclos propios; BIDS 02 no los suprime.

Graphify: `graphify query 'proteccion economica pause_economica revalidacion target niveles_v3'`
antes de leer codigo. Grafo con 25,755 nodos; salida truncada y usada solo
para localizar fuentes. Lectura directa cotejada:
`app/cycle.py::_recorre_hoja` (target/ventana de cortes/PAUSE),
`app/optimizer/bid.py::_decide_pause`,
`app/optimizer/goals.py::pause_economica_desde_settings`,
`app/apply_cola.py::_revalida` y `tools/replay_ads_economico.py`.
Estos archivos no se modificaron.

## Siguiente unidad reproducible (P1/P2)

Reutilizar consultas RO ya registradas; elegir solo resultados necesarios:

```bash
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/alertas-pausa/consultas.py.txt
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/alertas-pausa/consultas-detalle.py.txt
ssh goncloud 'curl -sf http://127.0.0.1:8010/api/dashboard/salud'
```

Completar con SELECT acotados de SHA/config/goals/caps/cola y propuestas actuales;
las consultas anteriores no sustituyen ese inventario. Cada consulta nueva usa
`ORBIT_DSN_READ`, transaccion READ ONLY y `SET LOCAL statement_timeout='15s'`;
conservar SQL, reloj UTC y resultado sin DSN/tokens. Fuente externa de identidad,
estado y dinero: Amazon mediante cliente RO/listas/reportes existentes, no una
segunda suma interna. No crear reportes remotos para esta preparacion ni usar
endpoints de escritura. La propuesta real se localiza en `/propuestas`; la ruta
JSON exacta se obtiene del codigo vigente, no se inventa del runbook historico.

Replay existente (ejemplo de intervalo reciente explicito, ajustar al ejecutar):

```bash
ssh goncloud 'docker exec -e PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=15000" orbit-app-1 python tools/replay_ads_economico.py --desde 2026-10-05 --hasta 2026-11-03'
```

Comprobar primero presencia/version del script en SHA productivo. Sus defaults
son septiembre: siempre pasar fechas. La opcion PGOPTIONS endurece conexion,
no imprime secretos y limita cada sentencia a 15s; si timeout, acotar consulta
sin elevar permisos ni sacar conclusion de ausencia. Guardar salida localmente
antes de interpretar y separar MXN/USD, campaign/leaf y target/procedencia.

**Limites del replay:** mide cruce numerico, no aplica gates completos de
estado/goal/veto. Usa parentesco actual de `ad_entity`, no historia completa de
estado. Sin reloj unico de decisiones omite ciclo; sin freeze confiable target
queda desconocido. Lee metricas hasta limite de observacion y selecciona vintage
por reloj, pero no produce por si solo la tabla de maduracion C.2b ni contrafactual
de applies. Completar P2 con seguimiento as-of y observacion +10d sobre la misma
cohorte/ventana, sin reemplazar target congelado por actual; separar la
revalidacion de apply, que si usa target/politica vigentes. Si falta cobertura,
reportar unknown. Cero candidatos no prueba FP=0 ni exito live.

## Criterios para el expediente P3

- Formula Decimal: `cost > 3 * target_pct / 100 * revenue` y
  `cost - target_pct / 100 * revenue >= 80 USD / 1000 MXN`; cero revenue
  medido distinto de None; >=7 fechas y fin de corte <= decision UTC -10d.
- Sin cache/default: BIDS 02 T.1/0063 prevalece en la cascada vigente;
  no cambiar el spec historico para inventar otro target. La frase antigua
  «cache o default si corresponde» queda declarada obsoleta en ese punto.
- Snapshot actual IDs/modos/config/caps, escritores concurrentes y pendientes;
  no suponer que todos los escritores nuevos BIDS 02 se apagan al cambiar goals.
  Demostrar aislamiento o parar preparacion del encendido con causa concreta.
- Sombra H6.4 en produccion escribe config/goals/auditoria; no equivale a RO.
  Necesita go de cambio productivo que declare cinco dias sin applies globales.
  Comparar ledger completo (incluye bids), no solo `apply_queue.applied_at`.
- Reversa concreta antes de activar: flag/config y modos anteriores, contencion
  de cola economica que podria convertirse en PAUSE tradicional, y reversas
  existentes por entidad con readback. No restaurar todos los shadow a live ni
  reactivar campanas. Ver reglas 2/3 del runbook para rollback de codigo/datos.
- Paradas: ciclo no done, mutacion inesperada, candidato sin explicacion,
  dato/moneda/target no confiable o >=1 FP a 10d. No avanzar live; investigar
  sin escrituras, y cualquier reanudacion/reversa productiva usa el go previsto.
- Verificacion proporcional: sin tests nuevos de texto por estos documentos;
  pre-commit con gitleaks y revision independiente del plan. Si surge defecto
  monetario, corregir en unidad aparte con prueba relevante roja en codigo
  anterior, checks focalizados y CI final verde antes de integrar.

## Revision y limitaciones

El host coordina revision independiente Producto/Arquitectura/Seguridad/QA/Skeptic
(`team_validation_mode: subagent`). El dictamen debe adjuntarse antes de integrar.
Esta preparacion reutiliza evidencia fechada; no afirma readback productivo nuevo,
CI nuevo, sombra observada ni retorno economico. No se tocan roadmap, manifest,
config, goals, crons, infraestructura ni otras filas del plan.


Comprobaciones locales de preparacion: `git diff --check` limpio; enlaces
locales de este expediente resueltos; `pre-commit run --files` sobre plan,
estado H6 y este README: secretos y conflictos Passed. No se ejecutaron tests
ni CI nuevos porque no hay cambio de codigo; CI final sigue siendo gate de
integracion, no un resultado inferido de estas comprobaciones.
