# Jev en Ads de Orbit

Diseño del 2026-10-03, basado en `3cbfab5`. Estado: diseño y prototipos;
implementación productiva pendiente. Alcance: negativos, harvest y semillas de
campañas. Reseñas quedan para otro trabajo.

## Problema y resultado esperado

Orbit ya decide con ventas, gasto y ventanas maduras. Le falta contrastar el
término buscado con lo que realmente ofrece cada producto. Jev aportará esa
lectura semántica y la pantalla mostrará el resultado junto a la evidencia.
El primer piloto medirá si ayuda a detectar problemas y revisar decisiones.
Todavía no hay evidencia de ahorro ni de mejora de ventas.

La unidad elegida es **término literal + versión de ficha de producto**. Se
evalúa cada par por separado y Orbit compone los resultados para el grupo,
el destino de harvest o el conjunto de productos de una campaña propuesta.

| Uso | Qué añade Jev | Cómo ayuda al operador |
| --- | --- | --- |
| Negativo de exclusión | Relación del término con los productos conocidos | Distingue una búsqueda pertinente que gastó sin vender de una búsqueda ajena al producto |
| Harvest | Evaluación separada de productos del origen y del destino | Permite revisar destinos cuyos productos parecen incompatibles con la búsqueda ganadora |
| Semillas existentes | Evaluación de keywords y negativos heredados contra los nuevos productos | Hace visibles asociaciones que no deberían darse por válidas al reutilizar una biblioteca |

Una búsqueda pertinente puede merecer un corte económico. La UI presenta ambos
hechos sin llamar "error" a esa combinación. Una venta agregada por grupo no
identifica qué producto compró la persona.

## How: recorrido que recibe la asesoría

El [grounding](../../evidencia/jev-ads-01/how-why.md) registra fuentes y límites.
La cadena actual es:

1. `windows.py` reúne métricas maduras por grupo y término, conservando faltantes.
2. `cycle.py` resuelve destino, llama a `optimizer/hygiene.py` y congela la decisión.
3. Hygiene propone negativo con cero pedidos y gasto/clics suficientes; propone
   harvest con al menos dos pedidos y ACoS dentro del límite existente.
4. `apply_cola.py` revalida y aplica al vencer el veto de 48 horas. No exige
   aprobación positiva. Si el término vendió, descarta el negativo correspondiente.
5. `apply_harvest.py` crea negativo de origen, keyword EXACT en destino, hace
   readback y trata hermanas. Su reversa tiene un orden propio.
6. `biblioteca.py` excluye de la biblioteca los negativos de ruteo. La fábrica
   combina semillas existentes y términos vendedores.

El nuevo comando asesor lee decisiones ya guardadas o un plan canónico de la
fábrica y escribe su propia revisión. El dashboard consulta revisiones guardadas.
La asesoría no se inserta entre operaciones del ciclo ni de la aplicación.

## Why: restricciones que vienen de la historia

- `docs/CONTEXTO.md` documenta un sistema anterior que dejó de actuar por exceso
  de filtros. Por eso la disponibilidad de Jev no será un requisito de ejecución.
- PR 23 ajustó cortes al valor del producto. La semántica no reemplaza gasto,
  moneda, pedidos ni maduración.
- PR 253 separó el ruteo hacia EXACT de la exclusión por irrelevancia. Una búsqueda
  compatible puede necesitar un negativo de ruteo y nunca debe aprenderse como
  exclusión general.
- PR 258 exige que destino y evidencia congelados representen lo usado. PR 378
  mostró un fallo real por origen igual a destino. Identidad y destino siguen
  siendo comprobaciones deterministas.

Estas fuentes justifican asesoría trazable fuera del camino de aplicación.
No demuestran que Jev clasifique bien el catálogo de Orbit.

## Uso desde los consumidores

Pseudocódigo de contrato, sin stubs en `app/`. `solicitud_id` identifica una
ejecución solicitada y permite retomarla después de un fallo.

```python
# CLI por lote, fuera del ciclo de Ads.
revision = asesor.evaluar(
    DecisionARevisar(decision_id=481), solicitud_id=solicitud_id
)

# GET de cortes: lectura y comprobacion local de vigencia, sin HTTP externo.
vistas = asesor.leer(Decisiones(ids=(481, 482)), ahora=ahora)
fila["asesoria"] = vistas[481]

# Preview existente: devuelve plan y hash, no un borrador persistido.
revision = asesor.evaluar(
    SemillasARevisar(plan=plan_canonico, huella=huella, fuentes=fuentes_semillas),
    solicitud_id=solicitud_id,
)
```

V1 expone la evaluación por CLI con lote y presupuesto explícitos. Exportar el
plan conserva su contenido y las filas de biblioteca usadas. No recalcula
semillas para reconstruir la evidencia. Un POST que encole trabajo queda fuera
del primer bloque; requeriría diseñar y operar una cola que hoy no hace falta.

## Tipos y módulos

```python
Relacion = Literal["satisface", "no_satisface", "informacion_insuficiente"]
Finalidad = Literal["exclusion", "ruteo", "keyword"]

@dataclass(frozen=True)
class FichaVersion:
    id: UUID
    producto_id: int
    plataforma: PlataformaAmazon
    listings: frozenset[int]
    hechos: tuple[HechoConFuente, ...]
    desconocidos: frozenset[str]
    aprobador: str
    observado_at: datetime
    revisar_antes_de: datetime
    sha256: str

@dataclass(frozen=True)
class ClavePar:
    termino_literal_sha256: str
    ficha_version_id: UUID
    contrato_sha256: str

@dataclass(frozen=True)
class Juicio:
    intento_id: UUID
    clave: ClavePar
    relacion: Relacion
    probabilidades: DistribucionRelacion
    confidence: Decimal
    observado_at: datetime

EstadoPar = Juicio | FichaFaltante | NoAplicaTexto | FalloProveedor
RelevanciaConjunto = HayCompatible | NingunoCompatible | Indeterminado
Vigencia = Vigente | Obsoleta | NoComprobable
Sujeto = DecisionARevisar | SemillasARevisar

class AsesorAds:
    def evaluar(self, sujeto: Sujeto, *, solicitud_id: UUID) -> Revision:
        raise NotImplementedError

    def leer(self, referencias: Referencias, *, ahora: datetime) -> Vistas:
        raise NotImplementedError

def componer(censo: CensoCongelado, pares: tuple[EstadoPar, ...]) -> RelevanciaConjunto:
    raise NotImplementedError
```

Son uniones discriminadas con campos propios. `Indeterminado` contiene motivos,
incluidos ficha ausente, universo desconocido, juicio insuficiente o fallo.
`NoAplicaTexto` distingue ASIN-like. Vigencia describe el uso actual de una
revisión, no cambia su resultado histórico.

| Módulo propuesto | Conocimiento que concentra |
| --- | --- |
| `app/jev_catalogo.py` | Identidades, fichas aprobadas, fuentes y censo de productos |
| `app/jev_juicios.py` | Contrato TypeSafe, clave exacta, solicitudes HTTP, validación y reutilización |
| `app/jev_ads.py` | Finalidad Ads, composición, revisiones y las dos operaciones públicas |

El CLI y dashboard conocen tipos de dominio, no el JSON del proveedor ni el
esquema SQL. No coordinan cargar, llamar y guardar por separado. Esta división
aplica `boundary-discipline` y `minimize-reader-load`; no agrega un framework de
proveedores ni métodos que sólo reenvían argumentos.

## Catálogo y reglas de composición

El censo parte de `ad_group -> product_ad` y conserva faltantes mediante LEFT JOIN
a `ad_entity_state`, `listing` y `product`. Cada anuncio conserva su identidad
antes de deduplicar productos. Estados ausentes o inciertos no se descartan.
`campana_grupo_producto` registra selección al crear, no demuestra el catálogo
actual. El producto se resuelve por IDs, nunca por parecido entre nombres.

El [censo de producción](../../evidencia/jev-ads-01/catalogo-grupos.json), sólo
lectura, encontró listings faltantes en 1 de 33 grupos con anuncios ENABLED/PAUSED
de MX y 24 de 48 de US. Además, el sincronizador no prueba exhaustividad del
conjunto aunque todos los anuncios observados tengan ficha.

Por eso el universo de grupos Amazon será `desconocido` en V1. En la fábrica sí
puede conocerse el conjunto explícito de listings del plan. El futuro soporte
de un censo completo de Amazon exige evidencia de enumeración exhaustiva;
no basta una casilla manual de "completo" ni un timestamp reciente.

Las primeras fichas serán documentos revisados por una persona con producto,
plataforma, listings cubiertos, hechos, fuente, autor y fecha de revisión. No se
extraen material o compatibilidad de `product.name`. Variantes que difieren en
atributos no comparten hechos sin evidencia. La revisión registra la versión
exacta de ficha utilizada.

`componer` aplica estas reglas sobre juicios vigentes y fichas aplicables:

- Un producto evaluado como compatible permite mostrar "Jev encontró un producto
  compatible", incluso con cobertura parcial, siempre mostrando esa limitación.
- "Jev no encontró compatibilidad en ninguno" exige universo no vacío, completo,
  fichas de todos sus miembros y `no_satisface` en cada par.
- En el resto de casos, el resultado es indeterminado. Vacío no prueba exclusión.
- No se multiplican probabilidades ni se convierte ausencia de evidencia en
  incompatibilidad. La clasificación es una apreciación del modelo, no un hecho
  validado por la base.

Origen y destino de harvest se componen por separado. Los negativos de ruteo
conservan esa finalidad aunque haya compatibilidad. Para semillas, el universo
es el nuevo plan; no se hereda la conclusión del producto de origen.

## Contrato con Jev y reutilización

La llamada contiene sólo término literal, hechos y desconocidos de **una ficha**.
No contiene grupo, campaña, métricas ni otros productos. Una pregunta Choice
devuelve las tres categorías del prototipo. Se fija `jev-1.13.0` y se versionan
instrucciones, serialización y orden de opciones. La categoría no incluye una
explicación libre ni IDs de hechos que la API no haya evaluado explícitamente.
La UI enlaza la ficha y muestra una plantilla atribuida a Jev.

La clave del par usa texto literal UTF-8, ficha y hash del contrato completo.
No fusiona acentos, números o términos parecidos. Se reutiliza el primer éxito
validado sólo si la ficha sigue aprobada, cubre ese listing y no venció su fecha
de revisión. Una nueva ficha o contrato cambia la clave. Cambiar destino o
miembros crea otra revisión que puede reutilizar pares aún aplicables.

La fecha de revisión de la ficha la declara su responsable según la fuente.
No se inventa un TTL que pretenda medir precisión. Revocar una ficha inserta
un evento; revisiones anteriores permanecen consultables y aparecen obsoletas.

Se valida modelo, IDs de preguntas, categorías, distribución y valores finitos.
El contrato declara un máximo de bytes para término y ficha antes del HTTP;
superarlo produce `contexto_excedido` sin truncar hechos. El piloto fija ese
límite dentro del máximo documentado del modelo y lo guarda en su configuración.
Timeout de 30 segundos y presupuesto máximo de llamadas por lote son límites
operativos, no umbrales de calidad. V1 no reintenta automáticamente: registra el
fallo y permite retomar explícitamente. HTTP ocurre fuera de transacciones SQL.
La falta de `usage` deja costo desconocido. El costo calculable usa Decimal,
moneda USD y tarifa versionada. Secretos vía `ORBIT_SECRETS_DIR`, errores
redactados, TLS y sin redirecciones que reenvíen autorización.

Confidence y probabilidades se conservan como salida del proveedor. No se
introduce un umbral de aceptación sin calibración con casos reales separados
de los usados para ajustar preguntas. El ensayo sintético no sustituye eso.

## Persistencia, concurrencia y permisos

Cuatro tablas nuevas bastan para el primer piloto. Nombres y firmas aquí son
propuestos, no migraciones ya instaladas.

| Tabla | Contenido e invariantes |
| --- | --- |
| `jev_ficha_version` | UUID, identidad/cobertura, hechos y fuentes, hash, aprobador y fechas; cada corrección inserta versión |
| `jev_ficha_revocacion` | FK versión, autor, fecha y motivo; append-only |
| `jev_revision` | UUID solicitud, sujeto discriminado, decisión o plan canónico y hash, fuentes de semillas, censos, versiones de fichas, contrato y fechas; se inserta antes del primer HTTP |
| `jev_par_evento` | UUID, FK revisión, clave par, ordinal, tipo intención/resultado/reutilización, FK intención o resultado reutilizado, request hash, respuesta validada o error, tiempos y usage |

Índices por decisión/fecha, hash de plan/fecha y clave de par/fecha. UNIQUE
solicitud impide duplicar contexto; reutilizarla con otro payload se rechaza.
UNIQUE revisión/par/ordinal/tipo y FK de resultado a intención hacen auditable
cada intento. CHECK de discriminadores y triggers impiden resultado sin su
intención, referencias de reutilización a fallos y UPDATE/DELETE. Las reglas
temporales van en triggers UTC, no CHECK dependiente de la hora.

Cada revisión congela su universo y las claves de pares esperadas. Al leer, sólo
se consideran eventos ligados a esa revisión, incluidos enlaces explícitos a
resultados reutilizados. Nunca se buscan éxitos posteriores para mejorar
silenciosamente una revisión antigua. La versión de composición queda guardada;
si cambia, se crea revisión nueva y se preserva el lector histórico.

El primer operador es un único CLI con lock de proceso y lote acotado. La base
mantiene unicidad aunque un segundo proceso intente la misma solicitud. No hay
cache mutable compartido. Solicitudes distintas concurrentes pueden pagar un
par duplicado; sus resultados siguen separados. Un crash tras intención deja
estado interrumpido, con costo desconocido si no se recibió usage. Retomar
registra otro ordinal sin borrar el anterior.

Rol nuevo `orbit_jev` con lectura de entradas e INSERT en revisiones/eventos;
sin permisos sobre decisiones, cola, ledger, goals ni bibliotecas. Administración
inserta fichas/revocaciones; `orbit_read` lee resultados. Los grants y triggers
se prueban con conexiones de cada rol. La asesoría no recibe credenciales Ads.

## Tiempo y presentación

Cada revisión conserva dos tiempos: `decided_at` de la decisión y `captured_at`
del catálogo revisado. Un catálogo actual no demuestra qué productos había
cuando se decidió. La pantalla dice "revisado con catálogo del ...". Las métricas
económicas proceden del snapshot original de la decisión.

El GET consulta base local, sin escrituras ni llamadas a Jev. Compara IDs,
versiones y contexto disponibles; si no puede comprobar vigencia muestra
"vigencia no comprobable". Un destino modificado marca revisión obsoleta para
el uso actual. No reescribe su historia.

En cortes se muestran categoría por producto, cobertura, ficha fuente, fecha y
estado del proveedor. En harvest se distinguen origen y destino. En fábrica se
anexa la asesoría al hash de plan revisado. El reloj del veto permanece visible:
pedir o leer asesoría no detiene la aplicación al vencer las 48 horas.

## Síntesis y costos aceptados

Se toma B como base: pares aislados reutilizables y composición determinista.
De A se conserva la captura previa del contexto y la distinción entre tiempos
de decisión y revisión. Ambos paquetes y el juicio independiente quedan en
[evidencia](../../evidencia/jev-ads-01/README.md).

Se eliminan de A la cola con leases y la evaluación integral repetida. De B se
eliminan feedback dedicado, endpoint de encolado y tablas separadas de contexto
y asociaciones: el snapshot de revisión y los eventos bastan para el piloto.
Se corrige en B "compatible = desacuerdo con el corte"; relevancia y rentabilidad
pueden coexistir. Las cuatro categorías propuestas se reducen a las tres
efectivamente ensayadas. Separar motivos de incompatibilidad requiere otro ensayo.

- Aceptamos una llamada por par a cambio de poder reutilizarlo sin contexto oculto.
- Aceptamos revisar fichas a mano para disponer de atributos con procedencia.
- Aceptamos conclusiones indeterminadas mientras falte catálogo completo.
- Aceptamos asesoría solicitada por lote para evitar infraestructura de trabajos
  antes de conocer su utilidad.

La alternativa integral por decisión concentra el contexto en una llamada, pero
vuelve a enviar hechos y hace depender el juicio de productos vecinos. Una
integración dentro de hygiene introduce una dependencia externa en decisiones
que hoy tienen reglas auditables. La generación libre de keywords cambia el
alcance y no corresponde al contrato Choice de este piloto.

## Verificación y pasos de implementación

La [evidencia de prototipos](../../evidencia/jev-ads-01/README.md) separa ensayos
sintéticos de mediciones productivas. No se enviaron términos ni fichas reales
a TypeSafe. Quedan por responder con el piloto: ¿qué cobertura logramos con
fichas aprobadas?, ¿qué errores comete en búsquedas reales MX/US?, ¿reduce tiempo
de revisión o detecta problemas confirmados por el operador?

El primer bloque implementará `componer` y sus tipos con casos discriminantes:
testigo compatible con hueco, todos negativos con hueco, universo vacío,
variante no cubierta, origen/destino distintos y ruteo compatible. Después:

1. Migraciones y lectura de catálogo. Probar permisos, append-only y dos tiempos.
2. Adaptador aislado y CLI. Probar respuesta inválida, timeout, crash después de
   intención, reanudación y clave idempotente con otro payload.
3. Lecturas de cortes y fábrica. Probar GET sin HTTP/escritura, plan cambiado,
   revocación de ficha y reconstrucción histórica sin resultados futuros.
4. Piloto con etiquetas humanas separadas de ajustes del contrato. Medir
   cobertura, falsos positivos/negativos por idioma, abstenciones, cambios por
   orden de opciones, latencia, usage y correcciones útiles por revisión.

La aceptación de V1 exige que fallar o apagar Jev deje el comportamiento Ads
igual, que cada etiqueta permita reconstruir sus entradas y que faltantes nunca
sean exclusiones universales. Automatizar efectos requerirá una decisión y
evaluación posterior, con reversa y criterios medidos propios.
