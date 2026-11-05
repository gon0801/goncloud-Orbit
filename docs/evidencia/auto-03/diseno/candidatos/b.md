# AUTO-03, candidato B: controlador previo por producto

Estado: propuesta para comparar; requiere decisiones economicas antes de implementar.
Base: `origin/master` (ce956518); solo lectura local. Ninguna llamada externa.

## Uso del llamador, escrito primero

El ciclo obtiene una politica por producto antes de juzgar hojas. La politica limita
lo permitido; el motor vigente sigue escogiendo cada bid. Ambos comparten el mismo
claim y reloj. No hay cron nuevo ni politica mutable que otro proceso publique.

```python
# app/cycle.py, tras cargar fuentes y antes de recorrer hojas (API propuesta)
politicas = productos.prepara(conn, ciclo=contexto_ciclo)

# app/cycle.py, _procesa_decisora: identidad y ventana ya validadas
veredicto = politica.decide(caso_actual)
veredicto = politicas.permite(veredicto, alcance=alcance_del_ad_group)
# Se audita tambien Mantener si la politica impidio una subida.

# app/cycle.py, fase existente de apply; no HTTP en productos.py
apply_cola.encola_cortes_producto(conn, ciclo=contexto_ciclo, politicas=politicas)
# Al vencer 48 h, apply_cola revalida y delega a retiro_anuncios.
```

`prepara` devuelve todas las politicas congeladas, incluidas las abstenciones con
motivo. `permite` nunca convierte mantener en recortar ni cambia magnitudes del
motor: puede impedir subidas o todo movimiento de una hoja cuyo producto se retira.
El retiro afecta sus product_ads en todas las campanas elegibles; nunca el listing.

## Problema y eleccion

La hoja actual es keyword/product_target; el segundo es un objetivo de publicidad,
no un producto propio. El costo de un producto puede repartirse entre campanas y
una puja puede alcanzar varios productos. Hace falta decidir una sola vez por
producto, conservar esa evidencia y limitar que acciones de hoja puede originar.

Elijo controlador previo, con un paquete inmutable por ciclo. Es una frontera de
dominio, no otro servicio. La alternativa integrada en `_procesa_decisora` puede
leer/cachar el producto y resolver alli: ahorra una tabla, pero debe reconstruir
el total de sus anuncios fuera del recorrido de hojas y repetir la misma politica
en la liberacion de cortes. Su API corta oculta mal el alcance muchos-a-muchos.
El controlador concentra alcance, evidencia financiera, cobertura y decisiones
producto; el consumidor solo recibe restricciones y cortes tipados.

## Forma y tipos propuestos

Los nombres siguientes son nuevos. No son APIs existentes ni stubs runtime.

```python
@dataclass(frozen=True)
class ProductoMercado:
    plataforma: Plataforma
    product_id: int


@dataclass(frozen=True)
class Dinero:
    valor: Decimal
    moneda: Moneda


@dataclass(frozen=True)
class Ventana:
    desde: date
    hasta_exclusivo: date


@dataclass(frozen=True)
class EvidenciaFinanciera:
    producto: ProductoMercado
    ventana: Ventana
    contribucion_antes_ads: Dinero
    gasto_ads: Dinero
    fraccion: Decimal
    observaciones: tuple[ReferenciaFuente, ...]


@dataclass(frozen=True)
class Abstener:
    motivo: MotivoAbstencion


@dataclass(frozen=True)
class Normal:
    evidencia: ReferenciaEvidencia


@dataclass(frozen=True)
class SoloReducir:
    evidencia: ReferenciaEvidencia


@dataclass(frozen=True)
class Retirar:
    evidencia: EvidenciaCorteMadura
    anuncios: tuple[IdentidadProductAd, ...]


PoliticaProducto = Abstener | Normal | SoloReducir | Retirar
AlcanceGrupo = ProductoUnico | ProductosCompartidos | AlcanceDesconocido


def evalua(evidencia: EvidenciaProducto) -> PoliticaProducto:
    raise NotImplementedError


def prepara(conn, *, ciclo: ContextoCiclo) -> PoliticasCiclo:
    raise NotImplementedError


class PoliticasCiclo:
    def permite(self, veredicto: Veredicto, *, alcance: AlcanceGrupo) -> Veredicto:
        raise NotImplementedError
```

`EvidenciaProducto` es una union de evidencia financiera completa, cero pedidos
completos y evidencia insuficiente. El adaptador SQL construye variantes validas;
no acepta `None` como cero. `EvidenciaCorteMadura` solo nace al verificar cada fecha,
moneda, vintage, cobertura y alcance. `PoliticasCiclo` indexa por ProductoMercado y
guarda el mapa grupo→productos; no hace SELECT por hoja. `Retirar` no transporta
payload HTTP. Son boundary-discipline, type-system-discipline y model-the-domain.

## Regla economica exacta propuesta

Es un control de gasto respecto de la contribucion total real del producto,
equivalente a TACoS economico; **no sustituye silenciosamente el ACoS del motor**.
Incluye ventas organicas como fuente de financiamiento. No afirma causalidad Ads.
No crea un target ACoS producto ni otra cascada; goals mantiene la unica existente.

SQL verificado: `0018_fabrica_campanas.sql:274,315-325` excluye `fee_type='ads'`;
`0021_economia_observada.sql:70,95` hace lo mismo. `v_economia_producto` proyecta
el margen maduro de `v_margen_producto`; ese margen es **antes** de publicidad.

Para un producto/mercado, en la ventana literal de esa fila:

- `V = venta_cubierta`; `C = V + cargos_con_orden + cargos_sin_orden - cogs`.
- Los cargos tienen su signo contable y ya incluyen el prorrateo de ISR de la vista.
- `A = SUM(cost)` de Ads del producto, misma ventana, misma moneda y vintage.
- `f = ads_target_fraccion_margen_<plataforma>`, lector existente; sin f abstiene.
  Valores vigentes comunicados por el orquestador: MX 0.5 y US 0.8.
- `B = f * max(C, 0)`. `C=0`/negativo medido permite presupuesto economico cero;
  `C` ausente no produce B. No hay clamp minimo de 10% que permita perder dinero.
- `A <= B`: Normal. `A > B`: SoloReducir.
- Corte economico: `A > 3*B AND A-B >= E`, `E=1000 MXN / 80 USD`.
  Es propuesta de extender la regla economica vigente al producto, no regla vigente.
- Se reporta `resultado_despues_ads = C-A`, restando publicidad una sola vez.
  Si `A<=B` y C positivo, queda al menos `(1-f)*C` **en esa ventana cubierta**.
  Es una condicion retrospectiva; no garantiza utilidad futura ni un cap duro.

Los goals manuales siguen determinando target/floor/ceiling, opt-out y elegibilidad.
La restriccion producto es un limite adicional propuesto y gana frente a una subida
que el goal permitiria; goal enabled=false gana siempre e impide accion automatica.
No se retiran anuncios de campanas opt-out. En ese caso la UI dice explicitamente
que el gasto restante queda fuera del alcance; nunca promete detener todo el gasto.

## Ventanas, muestra y cero pedidos

La vista financiera actual usa `[2026-02-20, D-15)`, >=30 fechas con venta,
cobertura por monto >=95%, moneda unica y cargos clasificados. Se congela su fila
completa; se conservan guards existentes de frescura y origen. La muestra limitada
de 1-29 dias de `v_economia_producto` sigue siendo informativa.

El gasto A debe cubrir **esa misma ventana completa**, colapsado por la ultima
observacion `<= decided_at`; exige reobservacion del costo por fecha a edad >=15d.
Esto NO usa `sales30d`: costo maduro no requiere esperar atribucion de ventas30d.
Falta historico verificable, fecha observada, mapeo o moneda: control financiero
Abstener; no prorratear gasto de campana ni extrapolar la historia corta.
Una fecha sin fila solo cuenta como cero si el reporte completo exitoso declara
ausencia de actividad para ese producto/fecha; de otro modo es hueco.

Sin margen maduro, el producto conserva el motor actual y un camino separado de
cero pedidos, propuesto sobre producto agregado en `[D-60,D-30)`:
`purchases30d=0 AND clicks>=100 AND cost>={500 MXN,40 USD}` → Retirar.
El piso100 es la politica fallback vigente de PAUSE, trasladada explicitamente a
producto; no es un umbral de rentabilidad ni el gasto350/36 para concluir bids.
Exige columnas presentes y cada fecha reobservada a `metric_date+30d` como minimo.
Pedidos desconocidos o atribucion incoherente abstienen; cero gasto no es fracaso.
Si hay compras atribuidas positivas pero no margen, no corta por economia inventada.

Halo: `sales30d` completo sigue en ACoS de las hojas. El controlador financiero usa
ventas reales totales del ledger, sin atribuirlas a Ads; no multiplica ventas halo
por margen del SKU anunciado. Cero pedidos incluye el halo; con compras halo deja
de cumplirse cero pedidos. Se informa sameSKU/halo por separado cuando existen.
Elige conscientemente que un producto que vende poco pero anuncia otros pueda
retirarse si no financia su publicidad: no inventa el margen de los receptores.

Cobertura real informada por el orquestador: hoy solo 7 productos MX y 3 US tienen
margen positivo publicado, de 154 y 57 filas; MeLi ninguno. El limite financiero no
se presenta como cobertura universal. El retiro por cero pedidos y el motor actual
siguen atendiendo los demas segun su evidencia. No exige muestras maduras para
dejar que un producto nuevo siga aprendiendo; las exige para concluir que fracasa.

## Alcance compartido y propiedad de escrituras

El alcance se resuelve por **ad group y product_ads vigentes**, no solo por campana
o grupo de fabrica. ProductoUnico exige todos identificados y mismo ProductoMercado.
Una campana puede compartir presupuesto y tener ad groups exclusivos.

En grupo exclusivo, SoloReducir veta subidas y permite reducciones/reversas de
seguridad del motor vigente. En grupo compartido, no fuerza un recorte de puja
por un producto; veta subidas si alguno tiene SoloReducir/Retirar, con auditoria
del efecto sobre los demas. Este costo compartido es deliberado y visible.
Retirar pausa exclusivamente product_ads del producto. No baja presupuesto de
campana, no aisla automaticamente ni crea campanas; AUTO05 permanece separado.
Alcance desconocido impide restriccion/retirada nueva, conserva decisiones actuales.
Dos listings comparten C una sola vez; A suma sus metricas sin duplicar ASIN/SKU.

`optimizer/productos.py` nuevo: tipos y evalua puro; `app/productos_io.py` nuevo:
lectura y prepara; `cycle.py`: tres integraciones de uso. Se amplia `apply_cola.py`
y `retiro_anuncios.py` existentes; no se introduce escritor Ads nuevo. Profundidad
maxima de decision: ciclo→productos_io→productos. Apply conserva su camino propio.

## Modelo persistido, estados y reversa

Nueva tabla append-only propuesta `producto_politica`, PK(cycle_id,platform,product_id):
version politica/config, ventanas, referencias de observaciones y fila financiera,
C/A/B originales con NUMERIC(14,4)+currency, veredicto, motivo y huella de alcance.
No campo global politica_actual: lecturas por ciclo o ultimo ciclo terminado.
Nueva relacion `decision_producto` vincula politica y decisiones afectadas.
Replay consume el freeze y llama evalua/permite; nunca reconsulta la vista actual.
Se amplia pause de apply_queue para product_ad; no se finge que ya los soporta.

`evaluada→encolada48h→vetada|obsoleta|aplicando→retirada|incierta→reconciliada`.
El corte guarda evidencia >=10d; trigger con UTC fijo mantiene el invariante.
Al vencer: revalidacion financiera/atribucion segun causa, goal, topologia, retiro
en curso, stock y estado vivo; despues claim/quota, intencion y HTTP/readback.
Si cambia alcance o evidencia, descarta y deja que un ciclo posterior regenere.

Nuevo motivo cerrado `politica_producto` en retiro_anuncios, sus constraints e
intenciones. Apply delega a ese propietario para pausar; no hace otro PUT.
Se agrega una entrada automatica tipada al mismo modulo, sin falsificar el literal
`go` humano del ejecutor actual. Se declaran permisos minimos al rol ejecutor
existente; no se conecta otro actor como admin para saltarse la separacion.
Mismas llaves `retiro-ad:<id>` para impulso/manual/auto/reposicion; sin doble retiro.
Pendientes de impulso quedan excluidos tambien por el controlador; no compiten.

Antes del primer PUT existe reversa del estado real. Reintento y reconciliacion
usan la intencion existente, incluida muerte antes del sello. La clave de efecto
del corte es anuncio+familia entity_cut; veto30d se comparte con cola existente.
Reponer usa retiro_anuncios.repone; registra anuncio_reposicion, sin nuevo escritor.
Se implementa autorizacion automatica tipada tambien para ese camino si se ofrece.
Propuesta inicial: reposicion manual explicita; mejorar margen no reactiva Ads
automaticamente. Apagar AUTO03 detiene nuevas restricciones/cortes, no repone solo.
Stock: no se automatiza pausa ni retorno con FBA incompleto; requiere autoridad
Bridge reconciliada y frescura para una politica futura separada de rendimiento.

## Balance, riesgos y siguiente paso

Acepto snapshot extra por una politica unica verificable antes del fan-out; acepto
historia financiera lenta y cobertura actual baja por no inventar utilidad reciente.
Acepto subsidio organico y desconocer beneficio halo a cambio de no estimarlo falso.
La alternativa integrada reduce persistencia, pero expone alcance/cobertura a cada
consumidor y favorece reconstruir retrospectivamente una politica distinta.
El controlador asincrono en otro cron pierde: ancla dos relojes y exige caducidad,
versiones y coordinacion sin mejorar el contrato del mismo ciclo.

Synthesis decision: candidato B listo para comparar; eleccion del orquestador pendiente.
¿Se adopta financiamiento con ventas totales y el trato conservador del halo?
¿Se adopta corte producto con cifras vigentes de hojas y reversa manual inicial?
¿Hay cobertura de costo desde 2026-02-20 para activar la rama financiera hoy?
Primer paso tras acordar: SELECT real de cobertura/grano y permisos, y congelar
ejemplos financieros; despues pruebas focalizadas de dinero/reversa/produccion con
regresiones demostradas fallando antes. No pruebas de texto ni bateria nueva.
