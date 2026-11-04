# Candidato B: juicios por término y ficha versionada

Diseño del 2026-10-03 sobre repo `3cbfab5`; sólo propuesta, sin activación.

## Problema

Orbit necesita asesoría semántica para revisar negativos, harvest y semillas existentes. Su motor ya decide con ventanas maduras, dinero con moneda y destino congelado. El asesor debe distinguir la relevancia de un término para cada producto de su rendimiento económico y de la función de una campaña. La unidad reutilizable será el juicio sobre un término y una ficha versionada. Código puro compone esos juicios para conjuntos de productos, origen, destino y biblioteca. V1 muestra discrepancias; no genera términos, nuevos candidatos live, gates ni acciones.

El recorrido real pasa por [cycle.py:1688](/Users/dn/dev/wt/jev-ads-design/app/cycle.py:1688), [hygiene.py:301](/Users/dn/dev/wt/jev-ads-design/app/optimizer/hygiene.py:301) y [harvest_destino.py:177](/Users/dn/dev/wt/jev-ads-design/app/optimizer/harvest_destino.py:177). La biblioteca aprende efectos aplicados y excluye negativos de ruteo en [biblioteca.py:12](/Users/dn/dev/wt/jev-ads-design/app/biblioteca.py:12). Este contrato sigue siendo dueño de las acciones.

## Uso desde el caller

La pantalla pide asesorías ya escritas. Un comando independiente procesa decisiones confirmadas en base y snapshots de planes identificados por huella. Ninguno participa en `cycle`, `apply_cola` o `apply_harvest`.

```python
# Worker asesor fuera del ciclo. RevisionDecision contiene IDs y datos sellados.
from app.jev_ads import AsesorAds

asesor = AsesorAds(configuracion_local)
resumen = asesor.evaluar(RevisionDecision(decision_id=481), clave="revision-481-1")
# Devuelve evaluado, parcial, faltante o fallido; nunca una accion Ads.

# Lectura integrada en api_dashboard.cortes, sin efectos externos.
por_decision = asesor.leer(ReferenciasDecision(ids=(481, 482)), ahora=ahora)
fila["asesoria"] = por_decision[481]

# Vista previa de la fabrica sobre un plan ya calculado y sellado.
revision = asesor.evaluar(RevisionSemillas(plan=plan_sellado), clave="plan-1-1")
# El PlanCanonico, sus semillas y su huella permanecen como entrada sellada.
```

`evaluar` acepta identidades del dominio; resuelve fichas, censos, reutilización, proveedor y persistencia dentro del módulo. `leer` devuelve contrato UI, sin programar trabajos. El caller sólo distingue revisión de decisión y revisión de semillas. No administra una secuencia de cargar, validar, consultar y guardar, per `boundary-discipline` y `minimize-reader-load`.

## Forma

### Tipos y firmas

Pseudocódigo Python contenido exclusivamente en este documento:

```python
Relacion = Literal["compatible", "fuera_categoria", "contradiccion", "insuficiente"]
FinalidadNegativo = Literal["exclusion", "ruteo"]

@dataclass(frozen=True)
class FichaVersion:
    id: UUID
    producto_id: int
    plataforma: PlataformaAmazon
    listings_cubiertos: frozenset[int]
    idioma: str
    hechos: tuple[HechoConProcedencia, ...]
    ausentes: frozenset[str]
    contenido_sha256: str
    aprobado_por: str
    observado_at: datetime
    vence_at: datetime

@dataclass(frozen=True)
class ClavePar:
    termino_literal_sha256: str
    ficha_version_id: UUID
    contrato_version: str  # modelo, preguntas, instrucciones y normalizacion

@dataclass(frozen=True)
class JuicioEvaluado:
    id: UUID
    clave: ClavePar
    relacion: Relacion
    observado_at: datetime
    vence_at: datetime

# Cada alternativa es una clase con discriminador y campos propios.
EstadoPar = JuicioEvaluado | ParFaltante | ParObsoleto | ParFallido
# Faltante: ficha o identidad; obsoleto: ID anterior y motivo;
# fallido: intento y error redactado. Insuficiente es respuesta valida del modelo.

@dataclass(frozen=True)
class CensoProductos:
    id: UUID
    referencias: tuple[ProductoListado, ...]
    huecos: tuple[HuecoIdentidad, ...]
    observado_at: datetime
    cobertura_fuente: Literal["completa", "incompleta", "desconocida"]
    contexto_sha256: str

def componer(conjunto: CensoProductos, pares: tuple[EstadoPar, ...]) -> RelevanciaConjunto:
    raise NotImplementedError

def interpretar(sujeto: DecisionSellada | SemillasSelladas,
                origen: RelevanciaConjunto,
                destino: RelevanciaConjunto | DestinoNoAplica) -> Consejo:
    raise NotImplementedError

class AsesorAds:
    def evaluar(self, revision: RevisionDecision | RevisionSemillas,
                *, clave: str) -> ResultadoRevision:
        raise NotImplementedError
    def leer(self, referencias: ReferenciasRevision, *, ahora: datetime) -> VistaAsesorias:
        raise NotImplementedError
```

Las entradas externas se parsean dentro de `evaluar`. `PlanSemillasSellado` contiene plan canónico, huella, listings y filas de biblioteca con ID/procedencia. IDs Amazon, JSON de TypeSafe y filas SQL son privados. `ResultadoRevision` conserva cobertura, juicio por producto, vigencia y consejo tipado. `DestinoNoAplica` evita usar `None` ambiguo. El consejo nunca contiene una autorización.

### Tres módulos con conocimiento propio

| Módulo propuesto | Conocimiento que oculta | Interfaz |
| --- | --- | --- |
| `app/jev_catalogo.py` | Identidades producto/listing/anuncio, procedencia, aprobación, censo y vigencia de fichas | Resolver un contexto; registrar una versión validada |
| `app/jev_juicios.py` | Clave del par, protocolo TypeSafe, contrato semántico, reintentos, historial y reutilización | Obtener juicios para pares del dominio |
| `app/jev_ads.py` | Composición, finalidades Ads, revisión sellada, permisos y presentación | `evaluar`, `leer` |

HTTP y SQL permanecen privados en estos módulos. La integración de dashboard o comando tiene una llamada a `jev_ads`; desde allí el recorrido máximo atraviesa los otros dos módulos. La división responde al conocimiento, per `laziness-protocol`; no crea módulos para etapas temporales ni adaptadores que sólo reenvían argumentos.

### Producto real, catálogo incompleto y varios productos

La identidad canónica es `product.id`, respaldada por SKU Odoo, no seller SKU ni parecido textual, según [0001:95](/Users/dn/dev/wt/jev-ads-design/migrations/0001_initial.sql:95). Un producto puede tener varios listings. Cada ficha declara plataforma y listings cubiertos; si variantes tienen atributos incompatibles necesitan versiones con cobertura disjunta. El par sigue siendo término más ficha de producto, pero nunca transfiere hechos entre variantes sin aprobación explícita.

El censo conserva cada `product_ad`, su estado, listing y producto; deduplica sólo después de contar huecos. Parte del conjunto conservador ENABLED/PAUSED usado en [0007:555](/Users/dn/dev/wt/jev-ads-design/migrations/0007_contribucion_perf.sql:555), y registra además estado de campaña/grupo. Usa LEFT JOIN también para estado; un estado faltante permanece como hueco. `campana_grupo_producto` es snapshot de alta, según [0018:140](/Users/dn/dev/wt/jev-ads-design/migrations/0018_fabrica_campanas.sql:140); su PK vigente es grupo/listing, según [0019:19](/Users/dn/dev/wt/jev-ads-design/migrations/0019_fabrica_grupo_publicacion_v2.sql:19). Sirve para contrastar identidad, no sustituye el censo actual. En semillas el universo es el conjunto explícito del plan sellado.

El censo read-only de las 07:02:52Z del 2026-10-03 encontró anuncios ENABLED/PAUSED sin listing en 1/33 grupos MX y 24/48 US. Estados frescos y cero huecos tampoco prueban exhaustividad: anuncios ausentes del payload no se actualizan. V1 marca los grupos observados `roster_no_demostrado`; no emite un universal negativo sobre Amazon. `completa` exige roster confirmado con procedencia propia o universo draft declarado. Otros grupos sin anuncios observados no son vacíos demostrados. Evidencia suministrada por el orquestador en [catalogo-grupos.json](/Users/dn/dev/wt/jev-ads-design/docs/evidencia/jev-ads-01/catalogo-grupos.json).

La ingesta [spapi/listings.py:81](/Users/dn/dev/wt/jev-ads-design/app/spapi/listings.py:81) no trae ficha comercial completa. V1 admite documentos manuales aprobados con fuente, fecha, campos conocidos y desconocidos. No extrae atributos del nombre. La API rechaza ficha sin vigencia o cobertura explícita; una ficha incompleta puede existir y producir `insuficiente`.

`componer` usa reglas deterministas. Si existe un par vigente compatible, devuelve `hay_compatible` y señala cobertura parcial si quedan huecos. Si el conjunto es no vacío, está completo y todos sus pares vigentes son `fuera_categoria` o `contradiccion`, devuelve `ninguno_compatible`, conservando ambos motivos. En cualquier otro caso devuelve `indeterminado`. Conjunto vacío también es indeterminado. No multiplica probabilidades ni convierte ausencia de compatible en prueba negativa. La cobertura desconocida impide el universal, incluso si todos los productos conocidos parecen incompatibles.

### Interpretación de los tres usos Ads

Para negativo de exclusión, `ninguno_compatible` ofrece acuerdo semántico; `hay_compatible` ofrece desacuerdo para revisar; lo demás carece de conclusión. Esto no modifica cero pedidos, madurez, clicks, costo o moneda que exige [hygiene.py:309](/Users/dn/dev/wt/jev-ads-design/app/optimizer/hygiene.py:309). Una relación semántica no prueba venta ni causalidad.

Para harvest se evalúan origen y destino como conjuntos independientes. Compatibilidad en origen y contradicción en destino se muestra como discrepancia de destino. Compatibilidad en destino no prueba que allí ocurrió una venta. Origen igual a destino conserva el salto determinista existente. El negativo en origen/hermanas tiene finalidad `ruteo`, aunque el mismo término sea compatible; nunca se interpreta como exclusión o candidato para `negative_biblioteca`. La secuencia negativa origen, keyword EXACT destino y readback de [apply_harvest.py:14](/Users/dn/dev/wt/jev-ads-design/app/apply_harvest.py:14) sigue completa y sin HTTP Jev intercalado.

Para semillas se asesoran filas ya presentes en bibliotecas y el plan obtenido por [fabrica_plan.py:369](/Users/dn/dev/wt/jev-ads-design/app/fabrica_plan.py:369). [fabrica_web.py:140](/Users/dn/dev/wt/jev-ads-design/app/fabrica_web.py:140) devuelve plan y huella sin persistir el preview; la revisión asesora guarda ambos. Su lector agrega IDs, tipo, plataforma, origen y fechas de las filas de biblioteca usadas, sin recalcular semillas. Una negativa histórica se revisa contra todos los productos propuestos; no hereda relevancia del producto antiguo. Keywords positivas se contrastan con el nuevo conjunto. ASIN-like queda `no_aplica_texto`; no se interpreta como término natural. Phrase, broad y exact conservan sus reglas actuales. Jev no añade ni quita semillas y no escribe bibliotecas.

### Tablas, índices y dueños

| Tabla propuesta | Campos que definen el registro | Índices y escritor |
| --- | --- | --- |
| `jev_ficha_version` | UUID, producto, plataforma, listings cubiertos, hechos/procedencia, ausentes, hash, aprobador, observado/vence | UNIQUE producto/plataforma/hash/cobertura; índice producto/plataforma/observado DESC; admin |
| `jev_contexto` | UUID, alcance grupo o huella plan, miembros y huecos, cobertura, fuente/ingest_run, hash, observado/vence | UNIQUE alcance/hash/observado; índice alcance/observado DESC; asesor |
| `jev_par_intento` | UUID, actor_run_id, ClavePar, término literal, número/fase intento, estado, categoría nullable, request hash, modelo/preguntas, tiempos, usage nullable, error redactado | UNIQUE actor_run_id/clave/número/fase; índice clave/observado DESC; asesor |
| `jev_revision` | UUID, clave idempotencia, sujeto decisión o plan, inputs/plan/biblioteca congelados y hash, IDs contextos origen/destino, versión composición, consejo, fecha | UNIQUE clave idempotencia; índice decision_id/fecha DESC, huella_plan/fecha DESC; asesor |
| `jev_revision_feedback` | UUID, revisión, etiqueta humana, actor, fecha, revisión anterior opcional | índice revision_id/fecha DESC; admin |

FKs y triggers verifican identidad/plataforma, discriminadores, campos válidos por estado y prohibición UPDATE/DELETE. Invariantes temporales usan triggers con UTC fijado, nunca CHECK dependiente de hora actual. La revisión incluye una asociación normalizada `jev_revision_par(revision_id, intento_id, rol, listing_id)` con PK compuesta; permite reconstruir cada agregado y evita buscar IDs dentro de JSON. Roles no comparten escritura. Los estados de vigencia se derivan al leer, no se sincronizan en filas. `app_admin` inserta fichas/feedback; un nuevo rol `app_jev` inserta contextos, intentos y revisiones, y sólo lee entradas Orbit. No hereda `app_decide`, no escribe `decision`, cola, ledger, goals o biblioteca. `app_read` sólo lee estas tablas. Los triggers de historia siguen el patrón [0023:6](/Users/dn/dev/wt/jev-ads-design/migrations/0023_append_only_bloque_b.sql:6).

Cada actor escribe intentos propios identificados por `actor_run_id`, per `separate-before-serializing-shared-state`. Una transacción corta escribe intención; HTTP ocurre fuera de transacción y termina con otro registro resultado enlazado. El lector elige el primer éxito vigente por fecha e ID y la revisión congela ese ID. Dos actores pueden gastar llamadas redundantes; no mutan un cache común ni duplican la revisión visible. UNIQUE de clave idempotencia devuelve la revisión ya existente y rechaza reutilizar la clave con otro sujeto. Un crash deja intención auditable y admite reintento acotado. No hay efectos Ads que reconciliar.

### Tiempo, contrato y proveedor

El contrato fija `jev-1.13.0`, preguntas Choice y plantilla versionada. La pregunta del par incluye término literal, idioma, hechos y desconocidos de una sola ficha; no incluye métricas, origen ni destino. `contradiccion` significa incompatibilidad con un atributo explícito; `fuera_categoria` significa finalidad ajena a la categoría validada; `compatible` significa uso respaldado; `insuficiente` significa evidencia insuficiente. La UI atribuye la clasificación a Jev y enlaza la ficha; no inventa una explicación específica.

La clave conserva el texto literal UTF-8 y versión de serialización. V1 no fusiona acentos, números o palabras similares. Cambio de ficha, modelo, idioma o preguntas crea otra clave. Cambio de destino, miembros o plan recompone con otra revisión, reutilizando sólo pares todavía aplicables. Una revisión anterior conserva sus IDs; nunca se reescribe como si hubiese usado el destino nuevo.

`vence_at` proviene de una política explícita de revisión de fuentes y de la configuración del piloto. No convierte edad en precisión semántica. `leer` marca obsoleto por vencimiento o discrepancia con contexto vigente. Si no puede comprobar contexto devuelve vigencia desconocida. Nombre, listing y estado actuales son mutables; congela valores además de IDs. Un censo posterior se etiqueta `contexto_en_revision`; jamás describe retrospectivamente productos a `decided_at`. La ventana económica sigue la de `decision.inputs` y no se mezcla con observaciones posteriores.

El proveedor acepta categorías enumeradas y rechaza payload inesperado. Timeout y 429/5xx admiten máximo dos reintentos con backoff y presupuesto; error de esquema no se reintenta. Datos incompletos no se convierten en fallo proveedor. Términos y fichas son datos delimitados, sin instrucciones ejecutables; logs pasan por redacción. Usage real queda nullable; costo se calcula con tarifa versionada y moneda, sin inventar tokens ausentes.

### APIs y operación

GET `/api/jev/decisiones/{id}` y GET `/api/jev/planes/{huella}` leen revisión histórica, vigencia y feedback. POST autenticado `/api/jev/revisiones` sólo solicita asesoría; POST `/api/jev/fichas` y `/api/jev/feedback` requieren admin. Auth sigue token por header antes de abrir DSN, como [api_write.py:109](/Users/dn/dev/wt/jev-ads-design/app/api_write.py:109). Comando `orbit jev revisar` procesa un lote acotado con presupuesto; desactivarlo no afecta Ads. [api_dashboard.py:1217](/Users/dn/dev/wt/jev-ads-design/app/api_dashboard.py:1217) incorpora estado, cobertura, fecha, modelo y discrepancia usando GET puro. La pantalla recuerda que la cola puede ejecutar al vencer veto; el consejo no cambia ese plazo.

## Decisión de síntesis

Pendiente del orquestador. Este candidato propone reutilizar juicios independientes por producto y componer significado Ads en código; las revisiones selladas son evidencia de consumo, no el núcleo inferencial.

## Costos aceptados

- Aceptamos gestionar claves y vigencia a cambio de reutilizar un juicio entre decisiones y planes sin repetir contexto económico.
- Aceptamos perder razonamiento semántico entre productos a cambio de universales explícitos y auditables, sin probabilidad conjunta inventada.
- Aceptamos ficha aprobada y censo completos para conclusiones negativas a cambio de no ocultar catálogo incompleto.

## Alternativas consideradas

Evaluación integral por decisión escondería agregación y destino dentro de una llamada, pero repetiría hechos y expondría al auditor una conclusión contextual difícil de recomponer. Gana simplicidad de almacenamiento y pierde reutilización y separación de finalidades. Un modelo que genera negativos o keywords requeriría controlar propuestas y efectos nuevos; su interfaz oculta texto generado pero obliga al caller a validar autoridad y reversas fuera del alcance V1.

## Riesgos y validaciones antes de producción

El piloto debe comprobar cobertura real de anuncios y procedencia de fichas mediante census read-only autorizado. Debe etiquetar casos MX/US por humanos: compatible, otra categoría, contradicción y evidencia insuficiente, incluidos varios productos. Medirá errores y desacuerdos por categoría, cambios de orden Choice, inyección y términos numéricos; no supondrá que confidence equivale a precisión. La aceptación de categorías y la vigencia inicial se fijan tras ese ensayo. Ningún resultado V1 habilita automatización.

Pruebas focalizadas cubrirán compatible más hueco, todos incompatibles con hueco, vacío, variante no cubierta, fallo proveedor, expiración, origen distinto de destino, cambio de destino y ruteo compatible que nunca entra a biblioteca. Integración verificará roles negativos, append-only, duplicación concurrente, crash tras intención, GET sin HTTP/escritura y reconstrucción por IDs históricos. Fakes discriminantes preceden proveedor real; hooks y batería completa se ejecutan una vez sobre el commit final.

## Próximo paso de implementación

Construir las funciones puras `componer` e `interpretar` contra fixtures de catálogo incompleto y cambios de destino; después implementar persistencia y proveedor, conservando fuera del ciclo toda la asesoría.

La expansión posterior puede agregar fichas de fuentes comerciales verificadas y nuevos consumidores del mismo par. Cualquier ranking semántico, revisión de ASIN o efecto live requiere contrato distinto, evaluación propia y el camino de reversa antes de habilitar acciones.
