# ADS PROTECCION 01 — decisiones de producto

Estado: politica elegida por el dueno el 24-sep-2026; implementacion y
activacion sujetas a los gates de `plans/ads-proteccion-01.md`. Este documento
define el comportamiento nuevo. Los invariantes generales siguen en
`docs/CONTEXTO.md`; este spec prevalece sobre las reglas de PAUSE y cooldown
anteriores del diseno v2 solo donde los cambia expresamente.

## Resultado buscado

Una hoja con gasto anormal puede entrar en PAUSE aunque tenga ventas, la puja
este en cooldown o el umbral adaptativo de clics haya subido. Una campana con
el mismo riesgo produce una propuesta visible para el dueno, nunca una pausa
automatica. Los datos atrasados se avisan antes de que el gate de 7 dias
permita varios ciclos con informacion vieja.

## Datos y calculo economico

- Usar la ventana de cortes existente: fechas maduras al menos 10 dias,
  al menos 7 fechas distintas por entidad y ultima observacion conocida al
  instante de decidir. No usar el dia en curso ni la ventana de bids.
- Fuente de dinero de hoja: metricas de esa keyword o product_target. Fuente
  de campana: reporte `spCampaigns` de esa campana. Nunca sumar reportes de
  campana y hojas para el mismo gasto. Moneda original USD o MXN; todos los
  calculos monetarios usan Decimal.
- `revenue` es la venta atribuida completa de Ads, incluido halo, de la
  misma ventana y grano. ACoS Ads no equivale a utilidad neta. Si `cost`,
  `revenue`, moneda o target efectivo no son confiables, abstenerse con
  motivo auditable; `None` no se convierte en cero.
- Resolver el target efectivo por la cascada unica y vigente de goals,
  incluido su ultimo peldano de cache o default si corresponde. Guardar el
  valor y su procedencia, y medir candidatos por cada peldano en el replay.
  Si la resolucion falla o carece de valor, abstenerse. Goal deshabilitado,
  campana PAUSED, entidad inerte o veto vigente conservan sus guardas.
- Definir `exceso = cost - (target_pct / 100) * revenue`. Se alcanza el
  limite economico solo si **ambas** condiciones son ciertas:
  `cost > 3 * (target_pct / 100) * revenue` y
  `exceso >= 80 USD` o `exceso >= 1000 MXN`, segun la moneda.
  El primer termino equivale a ACoS > 3 veces el target cuando `revenue>0`.
  Si la venta atribuida es **cero medido**, la comparacion algebraica evita
  dividir por cero y el limite absoluto frena el gasto aun cuando crezca el
  umbral adaptativo. El dueno aprobo expresamente esta extension tras elegir
  el limite para hojas con ventas. Revenue ausente sigue siendo abstencion.
- Los limites son inclusivos solo para el exceso (`>=`), estrictos para el
  cociente (`>`). No se hace FX ni se mezcla moneda. El bid en su floor no
  veta la proteccion; tampoco obliga a actuar si las otras guardas fallan.

## Precedencia y aplicacion en hojas

1. Conservar la PAUSE actual de cero pedidos por clics/costo como primera
   razon de corte; luego evaluar el limite economico anterior, incluso si
   hubo ventas. Si ninguna PAUSE califica, evaluar bandas de bid.
2. El cooldown de 7 dias originado por un **bid** no bloquea ninguna PAUSE.
   Una PAUSE aplicada y verificada si conserva su enfriamiento de 7 dias;
   revertirla no lo borra, para impedir un ciclo PAUSE/RESUME/PAUSE diario.
   Siguen vigentes goal, ancestros, estado Amazon, gracia por reactivacion
   manual si aplica, entidad inerte, veto de corte pendiente, quota y una
   decision por entidad/ciclo. NEGATIVE_EXACT conserva sus propias guardas.
3. PAUSE de hoja puede aplicarse automaticamente por el camino existente:
   cola y veto de 48 h, revalidacion fresca antes del claim, escritura,
   readback externo, ledger y reversa ya implementada. La revalidacion
   entiende la razon economica: una venta no la descarta por si sola.
   Recalcula en cada intento, tambien si la fila espera quota tras las 48 h,
   con la politica, target y metricas maduras **vigentes**. Si cambia la
   politica o el target, usa los vigentes; si faltan o ya no cruza el
   limite, cancela sin enviar la mutacion. El ledger conserva los valores
   de decision y de liberacion.
4. Congelar en `inputs` la version de politica, ventana, moneda, target y
   procedencia, cost, revenue, exceso y limites usados. Replay de una
   decision historica usa su version congelada, nunca el limite vigente.

La keyword 4925 de AU2 ya calificaba para la PAUSE existente el 14-sep
con datos conocidos entonces; su BID aplicado el 11-sep no debe bloquearla.
En la frontera exacta de 7 dias, el BID sigue enfriado hasta cumplir el
plazo; una PAUSE verificada y su reversa mantienen el enfriamiento propio.
Ejemplos del limite nuevo con target 20% y moneda USD: `cost=80`,
`revenue=0` medidos califican por exceso 80; `cost=79.99`, `revenue=0`
no califican. `cost=100`, `revenue=100` califican (exceso 80,
ACoS 100% > 60%); `cost=80`, `revenue=500` no califican (ACoS 16%).
`cost=30`, `revenue=5` supera 3x target pero no el exceso minimo.

## Campanas: propuesta y pausa manual en Amazon

- Aplicar el mismo calculo al grano `spCampaigns`; mostrar costo, venta,
  ACoS si revenue>0, target y fuente, exceso, ventana, estado y motivo.
  Generar una sola propuesta abierta por campana y episodio de riesgo.
- La propuesta vive en un registro auditable separado de `apply_queue`.
  Clave unica de propuesta abierta por campana y tipo de riesgo. Un ciclo
  repetido actualiza su evidencia sin crear duplicados. El episodio termina
  cuando el riesgo deja de cumplirse, el estado externo pasa a PAUSED o el
  dueno lo descarta; un episodio nuevo solo abre despues de una ventana
  observada sin riesgo. Venta tardia o cambio de target actualiza/cierra
  la propuesta tras revalidacion.
- Ni el paso de 48 h ni el cron convierten la propuesta en mutacion. El
  dueno revisa la propuesta y, si decide cortarla, pausa la campana en
  Amazon. Orbit sincroniza la estructura, confirma `PAUSED` en el mismo
  campaignId/profile y cierra la propuesta como `pausa_externa_observada`,
  con fecha y snapshot. Una marca de revision o el mensaje Telegram no
  ejecutan PAUSE. Si la campana sigue ENABLED, la propuesta permanece
  pendiente o el dueno la descarta; Orbit no presume que una revision
  significa pausa. El readback prueba estado externo, no autor individual
  ni causalidad de la pausa. Orbit no escribe PAUSE/RESUME de campana.

## Salud de ingesta y avisos

- La salud distingue pipeline principal de productos y resultado por
  perfil/plataforma/reporte. La ingesta registra el resultado de cada unidad
  y el fallo global si ocurre antes de identificar un perfil; no atribuye
  falsamente ese fallo a un perfil concreto. `cycle=done`, `/health` y un
  reporte de productos exitoso no certifican frescura principal.
- Cron principal: 07:10 UTC. Avisar por Telegram una vez al salir con
  fallo. Si no existe exito principal **de hoy** a las 10:30 UTC, avisar
  atraso por plataforma/perfil afectado. Una unica notificacion de
  recuperacion sigue a un exito principal real de la unidad afectada y a
  una alerta efectivamente entregada. Registrar episodio y envio por
  pipeline/perfil/plataforma/tipo con estado `pending/sent`, intento y
  acuse HTTP; fallo del canal no se marca como enviado y tiene reintento
  acotado. Salud muestra incidente abierto y fallo de entrega sin secretos.
- Exponer ultima corrida principal exitosa, maximo `metric_date` por grano
  y plataforma, edad y estado de alerta. Fecha no publicada por Amazon se
  presenta como ausente, no como cero ni como D-1 supuesto.

## Medicion y activacion

`plans/ads-proteccion-01.md` obliga a replay sin lookahead, casos frontera,
CI y review antes de activar cambios de corte. El replay debe mostrar los
candidatos y cambios tras nuevas atribuciones; un candidato que deja de
cruzar el limite durante el veto se cancela. Si aparece un falso positivo
material, se mantiene shadow y se revisa la politica antes de live.
Ni este spec ni las decisiones aprobadas reactivan A1U/AU2,
cambian goals o autorizan presupuestos automaticos.
