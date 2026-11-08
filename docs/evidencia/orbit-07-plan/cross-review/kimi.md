El objetivo es activar mejoras reales del motor sin pilotos largos, manteniendo dinero, historia, datos faltantes y reversa bajo control.

## Dictamen asesor

**No apto para implementar sin dos correcciones de contrato.** Encontré dos omisiones bloqueantes. M2 no lleva la economía individual a la revalidación posterior al veto. M3 no asigna el freno de subidas al único evaluador que puede emitirlas.

No encontré defectos demostrados en la gracia acotada de M1, el retiro y reversa de M4, la madurez D+17 de M5 ni la entrega conjunta M5+M6. La secuencia sí puede producir mejoras incrementales sin una espera global.

## Hallazgos verificados

### 1. M2 decide con el target del producto, pero revalida con el target de plataforma

**Severidad. Alta.**

**Referencias.** M2 enumera sus archivos en `plans/orbit-07-motor-decisiones.md:183-184` y exige economía individual en `:189-194`. No incluye `app/apply_cola.py`. Sus pruebas y recorrido en `:202-206` tampoco cubren la liberación de una pausa económica.

La cola resuelve otro target en `app/apply_cola.py:618-666` y lo usa al revalidar en `app/apply_cola.py:768-808`. El comportamiento actual está cubierto por `tests/test_apply_cola.py:2043-2097`.

**Comando reproducible.**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests \
/Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
import inspect
from decimal import Decimal
from test_optimizer_caso import cortes
from app import apply_cola
from app.optimizer import goals
from app.optimizer.bid import POLITICA_PAUSE_ECONOMICA, decide_pause

m2 = open('plans/orbit-07-motor-decisiones.md', encoding='utf-8').read().split(
    '## Resolver la meta y el límite económico del mismo producto (M2)'
)[1].split('## Compartir el aprendizaje')[0]
print('M2 asigna apply_cola:', 'app/apply_cola.py' in m2)
print('revalidacion llama:', inspect.getsource(
    apply_cola._target_pause_vigente
).split('return g.cascada_target_acos_con_procedencia')[1].splitlines()[1].strip())
meta, origen = goals.cascada_target_acos_con_procedencia(
    None, None, {}, None, 'amazon_mx', Decimal('20')
)
print('target plataforma:', meta, origen, '| target M2:', Decimal('8') * Decimal('0.5'))
evidencia = cortes(orders=1, clicks=120, cost='5000', revenue='10000')
for target in ('4', '20'):
    r = decide_pause(
        platform='amazon_mx', bids=None, cortes=evidencia,
        target_acos_pct=Decimal(target), umbral_pause=100,
        policy_version=POLITICA_PAUSE_ECONOMICA,
    )
    print(f'target={target}% kind={r.kind} motivo={r.motivo}')
PY
```

**Salida observada.**

```text
M2 asigna apply_cola: False
revalidacion llama: campana, plataforma, settings, estado[1], platform, margen
target plataforma: 20 margen_plataforma | target M2: 4.0
target=4% kind=pause motivo=pause_economica
target=20% kind=None motivo=bids_sin_observaciones
```

**Resultado exigido.** La decisión y su revalidación deben usar la misma economía efectiva del producto, con datos frescos y las mismas reglas de elegibilidad y precedencia.

**Impacto.** Con el caso ejecutado, M2 ordenaría pausar al 4 % y la cola descartaría esa pausa al revalidar al 20 %. El sentido contrario también es posible con otras cifras. El efecto sería aplicar o cancelar cortes con una regla distinta de la decidida. La pérdida económica real es una inferencia no cuantificada.

**Corrección mínima del plan.** Añadir `app/apply_cola.py` a M2. Llevar la evaluación fresca por producto a `_target_pause_vigente` y `_revalida_pause`, conservando goal explícito, identidad, atribución, moneda y fallback completo. Añadir al recorrido una pausa económica cuyo target individual y el de plataforma produzcan resultados distintos tras el veto.

### 2. M3 agrega el insumo de aprendizaje, pero no asigna el freno al dueño de la subida

**Severidad. Alta.**

**Referencias.** M3 solo asigna resolutor, lectores, caso, ciclo y replay en `plans/orbit-07-motor-decisiones.md:222-224`. La obligación de impedir subidas está en `:229-233`. M2 había declarado `politica.decide` único dueño de la puja en `:194`.

Actualmente `decide` genera la subida en `app/optimizer/politica.py:247-254` y `:315-316`. El ciclo la persiste directamente en `app/cycle.py:1800-1831`.

**Comando reproducible.**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests \
/Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
from decimal import Decimal
from test_optimizer_caso import fabrica_caso, nivel, tramo
from test_optimizer_politica import _cuenta_llena
from app.optimizer.caso import PrecioVentana
from app.optimizer.politica import decide

m3 = open('plans/orbit-07-motor-decisiones.md', encoding='utf-8').read().split(
    '## Compartir el aprendizaje'
)[1].split('## Retirar anuncios')[0]
print('M3 asigna app/optimizer/politica.py:', 'app/optimizer/politica.py' in m3)
propia = nivel(tramo(
    clics=50, pedidos=5, venta='5000', gasto='200', impresiones=2000
))
caso = fabrica_caso(
    propia=propia, grupo=propia, cuenta=_cuenta_llena(),
    precio=PrecioVentana(Decimal('100'), 50),
)
print('veredicto actual sin restriccion de aprendizaje:', decide(caso))
print('CasoHoja tiene campo de aprendizaje:',
      any('aprend' in campo for campo in caso.__dataclass_fields__))
PY
```

**Salida observada.**

```text
M3 asigna app/optimizer/politica.py: False
veredicto actual sin restriccion de aprendizaje: Mover(factor=Decimal('0.15'), bid_nuevo=Decimal('11.5000'), motivo='bajo_target', nivel='hoja')
CasoHoja tiene campo de aprendizaje: False
```

**Resultado exigido.** Con aprendizaje agotado y evidencia suficiente, el evaluador puro debe impedir la subida y conservar recortes, pausas y regreso por desplome.

**Impacto.** Implementar solo los archivos asignados no cambia la subida que M3 pretende controlar. Filtrar el `Mover` después en `cycle.py` dividiría la decisión entre dos dueños. La exposición adicional resultante es una inferencia, no una medición de producción.

**Corrección mínima del plan.** Añadir `app/optimizer/politica.py` a M3. El caso debe transportar el episodio congelado y `decide` debe devolver un `Mantener` específico antes de crear una subida. Comprobar en el mismo contrato puro que recortes, pausas y `Regresar` permanecen intactos y que el replay reproduce el bloqueo.

## Hipótesis y limitaciones no verificadas

- No consulté producción, Amazon ni datos privados. No estimo frecuencia, pérdidas ni mejoras comerciales.
- No demostré un defecto adicional en M4. El plan ya exige autoría, conciliación, cuota y reversa. Adaptar el retiro genérico existente sigue siendo trabajo de implementación, no un fallo probado del plan.
- La fecha D+17 de M5 es coherente con siete días posteriores completos y diez días de madurez.
- La ausencia de episodios históricos comparables puede limitar la cobertura inicial de M6. El plan ya prevé fallback por entidad, así que no lo clasifico como defecto.

## Comprobaciones ejecutadas

- `graphify query` sobre la cadena de pausas, economía, aprendizaje y replay. `graphify path` sobre lecturas y política.
- Revisión completa del plan, contexto, diseño y código de M1-M6.
- Lectura de las pruebas de ingesta por producto, economía, vínculo de anuncios, política, lecturas, cola y retiro.
- Sondas reproducibles incluidas en cada hallazgo.
- Verificación focalizada existente.

```text
3 passed in 0.18s
```

Correspondió a `test_r6_sube_con_evidencia`, `test_r8_venta_reciente_gana_al_gasto` y `test_roundtrip_caso_rico`. No ejecuté migraciones, escrituras externas ni una suite extensa. No modifiqué archivos.
