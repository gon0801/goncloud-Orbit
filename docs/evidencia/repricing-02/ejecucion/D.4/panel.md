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

## GLM 5.3 (opencode): APROBADA con fixes, 0 bloqueantes

3 medias + 5 bajas (solo 1-8 tocan D.4; 2-3 de X.4 en `X.4/panel.md`):

1. [media] Checklist modo real con servidor muerto da exit 1 en vez
   de 4 (cron vacio -> "0" -> FALLA; cobertura 255 -> FALLA).
   ANOTADO: patron heredado de D.3; el txt lo hace visible
   (NOMEDIDOs + ceros = servidor muerto, no falla del deploy) y el
   rollback exige juicio humano. El modo sim SI da NOMEDIDO
   (verificado: `grep` a archivo ausente -> vacio).
2. (X.4: ver `X.4/panel.md`.)
3. (X.4: ver `X.4/panel.md`.)
4. [baja] Redespliegue acepta cualquier forma (un typo pasa 1b).
   ANOTADO (residual aceptado): runtime fail-closed (forma !=
   sellada -> `EscrituraNoDisponible`, cero escritura); el valor
   sellado se desconoce hoy, no hay contra que validar.
5. [baja] La restauracion del guard-cron no se verificaba en la
   salida. APLICADO: tras restaurar se relee y se difunde
   `RESTAURADO (verificado)` o se avisa que NO quedo (salida
   re-corrida).
6. [baja] Carrera teorica en `politicas.sh` (doble vigente).
   ANOTADO: despliegues secuenciales por proceso; el `count=2`
   aborta como detector. El fix real (indice unico parcial) es
   cambio de esquema, fuera de alcance.
7. [baja] Cronologia entre sims (mismo STAMP tras wipe).
   APLICADO: documentado en `evidencia.md` decision 10.
8. [baja] Trap ERR no menciona estados 4b/5. APLICADO: mensaje
   extendido ("migracion, codigo o crontab").

## GLM 5.2 (opencode): APROBADA con fixes, 0 bloqueantes

Verifico por su cuenta: coherencia de salidas (sellos, huellas,
conteos), `eb3ac536` (1 archivo 1 linea), shapes de API, TOPE 500,
DB prod en UTC. 2 medias + 7 bajas:

- M1 [media] Guard cron solo relativo (no exige contexto D.3).
  ANOTADO: escenario exotico; checklist s4 lo detecta (exit 1 ->
  rollback) y el estado final es correcto (D.4 no causo la falta).
- M2 [media] 7v sim corre el arbol, no el SHA. ANOTADO (limitacion
  heredada de D.3; solo afecta fidelidad sim; en real corre el
  contenedor del SHA).
- L3 = GLM-5.3 #5 (restauracion sin capturar). APLICADO (mismo fix).
- L4 = GLM-5.3 #4 (cualquier forma). ANOTADO (mismo residual).
- L5 [baja] `--listing-ids 1,,2` revienta feo. APLICADO en
  `X.4/sembrar.sh` (valida sin huecos; probado 2/2/2/0/0).
- L6 [baja] WARNINGs de transaccion anidada. ANOTADO (ruido
  heredado de D.3, atomicidad intacta).
- L7 [baja] "restaura 0028 verbatim" inexacto. REFUTADO: diff
  corrido, reversa == 0028 verbatim (salvo CREATE OR REPLACE);
  0054/0055 nunca tocaron la funcion.
- L8 [baja] Espejo 0=0 + cobertura vacua. ANOTADO (por diseno: sin
  catalogo no hay activas; prueba real post-11:30).
- L9 [baja] Ventana 7b (tercero instala las 4 exactas). ANOTADO
  (mensaje confuso, estado final correcto; ventana minima).
