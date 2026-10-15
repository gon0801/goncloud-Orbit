# Panel D.4/X.4: paquete de despliegue + scripts de encendido MeLi

## Composicion

- Muse A (workflow panel-d4x4-ops, enfasis despliegue D.4):
  VEREDICTO APROBADA con fixes, 0 bloqueantes.
- Muse B (workflow panel-d4x4-ops, enfasis encendido X.4):
  VEREDICTO APROBADA con fixes, 0 bloqueantes.
- Sintesis: APROBADA con fixes, 0 bloqueantes, 1 alta unificada
  (U1, reportada por ambos).

Limitacion de recuperacion: del workflow solo se entrego la
sintesis (veredicto + bloqueantes + altos); los reportes completos
de A y B (medias/bajas) no se recuperaron. Las medias/bajas del
panel quedan sin triagear; por regla de calidad solo un bloqueante
reabre y no hubo ninguno.

## U1 [ALTA, interfaz D.4/X.4] -> aplicado

El redespliegue sellado que ordena X.4 era imposible: la guarda 1b
exigia `FORMA_ESCRITURA_MELI = "pendiente_sonda"` en el SHA o
abortaba, pero tras mergear el PR del sello (que cambia SOLO la
constante) el SHA ya no la trae, y la guia prohibe saltar/editar
guardas al correr.

Aplicado: 1b con dos modos segun `PENDIENTES` (decision 5 de
`evidencia.md`): con la 0073 pendiente (deploy inicial) EXIGE
`pendiente_sonda`; con la 0073 aplicada (redespliegue) acepta
cualquiera y la anuncia. El PR del sello sigue cambiando solo la
constante, sin tocar scripts ni guardas.

Pruebas (commit sellado colgante `eb3ac536`, 1 archivo 1 linea,
jamas en rama ni push):

- `guard-forma-inicial-salida.txt` (exit=1): sellada + 0073
  pendiente -> ABORTA en 1b antes de tocar nada.
- `redespliegue-sellado-simulacion.txt` (exit=0): sellada + 0073
  aplicada -> LISTO (el flujo del sello real).
- `desplegar-simulacion-reintento.txt`: sin sellar + 0073 aplicada
  -> pasa como redespliegue (reintento normal intacto).

Tras el fix se re-corrieron TODAS las sims D.4 en verde
(desplegar, reintento, checklist 3->0, rollback, guard-cron).
