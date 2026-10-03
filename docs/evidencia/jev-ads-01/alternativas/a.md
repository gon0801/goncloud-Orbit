# Candidato A: evaluación por decisión y contexto completo

Diseño del 2026-10-03, anclado en `3cbfab5`. Sólo diseño; fases Ground y Sketch cubiertas, Agree queda en la síntesis y no se inicia Implement.

## Problema

Jev aportará asesoría trazable sobre negativos, harvest y semillas de biblioteca. V1 conserva las decisiones y acciones actuales. `app/cycle.py:1740` resuelve destino, `:1747` ejecuta hygiene y `:1770` prepara la decisión congelada. `app/optimizer/hygiene.py:301` excluye ASIN, `:309` exige cero pedidos para negativos y `:340` verifica ventas para harvest. Jev no modifica estas reglas ni participa en `cycle` o `apply`. La pregunta semántica requiere conocer todos los productos anunciados y distinguir origen, destino y finalidad. Una respuesta reutilizable por producto escondería esas dependencias. Este diseño conserva cada evaluación junto con el contexto exacto que la motivó.

## Uso desde el caller

El operador solicita análisis en una operación explícita. El dashboard sólo lee. El trabajador consulta TypeSafe después de confirmar la transacción que congeló la solicitud. Los siguientes contratos son pseudocódigo Markdown; no crean módulos ejecutables.

```python
# Operación de análisis sobre una decisión existente.
from app.jev_ads import solicitar, consultar
ticket = solicitar(
    actor=operador,
    sujeto=DecisionAds(id_decision=714),
    idempotencia="revision-714-v1",
)
vista = consultar(actor=lector, ticket=ticket)
# vista.estado: pendiente | faltante | fallida | evaluada | obsoleta
```

```python
# Ensayo sobre semillas YA existentes en un borrador de fábrica.
ticket = solicitar(
    actor=operador,
    sujeto=SemillaBorrador(id_borrador=82, revision=3, id_semilla="s17"),
    idempotencia="borrador-82-r3-s17",
)
# La biblioteca y el borrador mantienen su contenido y orden actuales.
```

```python
# Comando programado con credenciales exclusivas de análisis.
from app.jev_ads import procesar_pendientes
resumen = procesar_pendientes(limite=40, presupuesto=cuota_operativa)
# Repite con seguridad; no recibe conexión ni credenciales de Ads write.
```

La UI presenta «compatible con algún producto», «todos los productos incompatibles», «información insuficiente» o «evaluación no disponible». Muestra el fundamento por producto, las fechas y los hechos citados. Para harvest presenta origen y destino por separado. «Asesoría» describe el alcance: no existe un botón que transforme la evaluación en una acción Ads.

## Forma

### Tipos y contratos

```python
type Sujeto = DecisionAds | SemillaBorrador
type Finalidad = NegativoExclusion | Harvest | SemillaPositiva | SemillaExclusion
type Cobertura = CensoCompleto | CensoIncompleto
type Ficha = FichaVerificada | FichaAusente
type Relacion = Compatible | FueraCategoria | Contradiccion | Insuficiente
type ResultadoGrupo = ExisteCompatible | TodosIncompatibles | Indeterminado
type Resultado = Faltante | Fallida | Evaluada
type Vista = Pendiente | Faltante | Fallida | Evaluada | Obsoleta

@dataclass(frozen=True)
class FichaVerificada:
    listing_id: ListingId
    product_id: ProductId
    revision: RevisionFicha
    hechos: tuple[HechoConFuente, ...]
    observado_en: Instante
    valido_desde: Instante
    valido_hasta: Instante

@dataclass(frozen=True)
class GrupoCongelado:
    identidad: IdentidadGrupo  # cuenta, plataforma, campaign, ad_group
    cobertura: Cobertura
    miembros: tuple[MiembroConVinculo, ...]
    observado_en: Instante
    vigente_hasta: Instante

@dataclass(frozen=True)
class Contexto:
    sujeto: Sujeto
    finalidad: Finalidad
    termino: TerminoLiteralYNormalizado
    origen: GrupoCongelado
    destino: SinDestino | GrupoCongelado
    evidencia: EvidenciaEconomicaCongelada | ProcedenciaSemilla
    versiones: VersionesEvaluacion
    capturado_en: Instante
    huella: HuellaContexto

def solicitar(*, actor: Actor, sujeto: Sujeto,
              idempotencia: str) -> Ticket: raise NotImplementedError
def consultar(*, actor: Actor, ticket: Ticket) -> Vista:
    raise NotImplementedError
def procesar_pendientes(*, limite: int,
                       presupuesto: CuotaOperativa) -> Resumen:
    raise NotImplementedError
def componer(grupo: GrupoCongelado,
             relaciones: tuple[RelacionPorMiembro, ...]) -> ResultadoGrupo:
    raise NotImplementedError
```

`Harvest` exige destino distinto del origen mediante constructor validado. `SemillaExclusion` exige procedencia de exclusión. Los negativos de ruteo quedan fuera de ese tipo. `Evaluada` contiene relaciones identificadas por miembro, evidencia y resultado compuesto; `Fallida` contiene código operativo sin conclusión semántica; `Faltante` identifica campos o identidades ausentes. `Obsoleta` envuelve un resultado histórico y enumera diferencias con el contexto vigente. Los tipos delimitan estados; el parser rechaza combinaciones imposibles en cada frontera, según `model-the-domain` y `boundary-discipline`.

### Tres módulos con responsabilidad completa

| Módulo propuesto | Conocimiento que posee | Contrato |
| --- | --- | --- |
| `app/jev_catalogo.py` | Identidades, censos, procedencia y revisiones de fichas | Capturar grupos; registrar revisión validada; comparar vigencia |
| `app/jev_semantica.py` | Preguntas, protocolo TypeSafe, parser y composición pura | Evaluar contexto congelado; producir relaciones y evidencia |
| `app/jev_ads.py` | Sujetos Ads, permisos, persistencia, cola, presupuesto y vistas | Los tres contratos usados arriba |

Las rutas FastAPI y el comando adaptan autenticación y transporte existentes. No introducen servicios intermediarios. La llamada pública oculta transacción, huellas, completitud, programación y obsolescencia; expone únicamente sujeto, identidad de operación y resultado. La composición pura reside junto a las preguntas que interpreta. El SQL queda junto al propietario del dato, según `minimize-reader-load`. El recorrido de análisis cruza como máximo estos tres módulos.

### Catálogo e identidades

La identidad canónica es `product.odoo_sku`, y varios listings pueden pertenecer al mismo producto, según `migrations/0001_initial.sql:95` y `:106`. Una ficha es específica de listing y plataforma: variantes y textos comerciales pueden diferir. `app/spapi/listings.py:81` guarda estado y tipo, que no equivalen a una ficha comercial.

El censo recorre product ads pertenecientes al ad group, su enlace a listing y producto, y conserva anuncios activos o de estado desconocido. Un anuncio sin listing, una respuesta parcial o una sincronización fuera de vigencia producen `CensoIncompleto`, con los IDs sin resolver. Una lista vacía no prueba ausencia de productos. No se identifica producto por similitud del nombre ni se infiere el producto comprado del término agregado.

La consulta parte de anuncios y usa `LEFT JOIN`. El censo comunicado del 2026-10-03T07:02:52Z encontró anuncios ENABLED/PAUSED sin listing en 1 de 33 grupos MX y 24 de 48 US. Incluso cero faltantes no demuestra exhaustividad: `app/ads/structure.py:203` no actualiza entidades ausentes del payload. `CensoCompleto` exige constancia explícita del universo completo; estados frescos no bastan. Sin ella, V1 sólo concluye sobre pares observados. El borrador admite universo completo declarado, identificado como selección planeada.

V1 permite fichas manuales verificadas con fuente, responsable, hechos explícitos, fechas y revisión inmutable. El nombre del producto sirve de etiqueta, sin demostrar materiales, talla, uso o compatibilidad. Las exclusiones necesitan evidencia negativa explícita o una categoría verificable que realmente contradiga la intención. La falta de atributos produce `Insuficiente`.

### Relaciones y composición

Cada pregunta recibe término literal y fichas identificadas, con hechos delimitados como datos. Jev clasifica cada relación y devuelve identificadores de los hechos utilizados. El parser exige miembros y referencias existentes; prosa libre o referencias inventadas invalidan la respuesta. Los motivos UI proceden de plantillas locales.

`Compatible` requiere apoyo en hechos; `Contradiccion` identifica un requisito explícito incompatible; `FueraCategoria` representa una categoría verificablemente ajena; `Insuficiente` representa evidencia insuficiente, aun con un modelo seguro. Una baja confianza semántica también impide elevar una relación a conclusión firme. No se equiparan ausencia y duda.

La composición es determinista. Un compatible respaldado demuestra que existe un producto compatible, aunque otros miembros sean desconocidos; la UI conserva la advertencia de cobertura parcial. «Todos incompatibles» exige censo completo, al menos un miembro y todas las relaciones concluyentes de incompatibilidad. Cualquier ausencia restante produce `Indeterminado`. No se multiplican probabilidades ni se presentan como probabilidad conjunta del grupo.

Cada solicitud evalúa el conjunto completo congelado. Si excede el límite de contexto, el sistema registra `Fallida(contexto_excedido)`; nunca trunca miembros ni completa silenciosamente con resultados antiguos. El eventual particionado será una revisión del protocolo con pruebas de composición.

### Los tres usos Ads

Para un negativo económico, Jev caracteriza relevancia del grupo origen. «Existe compatible» señala desacuerdo semántico para revisión, sin vetar ni aprobar la decisión económica. Tampoco prueba rentabilidad. Los umbrales, madurez, moneda y ventanas siguen perteneciendo a hygiene.

Para harvest, se evalúan ambos grupos. Una venta en origen no demuestra compatibilidad del destino. La pertenencia del destino procede del resolutor existente `app/optimizer/harvest_destino.py:177`. Un cambio de destino invalida la aplicabilidad de aquella asesoría aunque la cadena del término sea idéntica. Los negativos de origen y hermanas son ruteo; `app/biblioteca.py:12` prohíbe aprenderlos como exclusiones. El análisis no participa entre negativo, keyword y readback.

Para semillas, el sujeto es una entrada identificable de un borrador y revisión congelados, con texto, procedencia y grupos objetivo. El identificador asesor conserva el snapshot y la huella que produce `app/fabrica_web.py:140`, incluyendo filas de biblioteca utilizadas. Cada finalidad se evalúa por separado: semilla positiva frente a productos del objetivo; exclusión heredada frente a todos ellos. Jev no inventa palabras, altera el ranking, elimina semillas ni escribe bibliotecas. Las fuentes actuales siguen siendo biblioteca y términos vendidos, según `app/fabrica_plan.py:369`. Las semillas EXACT conservan las exigencias de harvest. Una semilla sin objetivo concreto queda `Faltante`.

Si la procedencia histórica no permite distinguir exclusión de ruteo, el constructor devuelve `Faltante(procedencia_ambigua)`; el texto idéntico no basta para unificar finalidades.

### Tablas, campos e índices

Todas las tablas incorporan ámbito de cuenta y plataforma. Las relaciones usan claves compuestas que impiden cruzarlos.

| Tabla | Campos principales | Restricciones e índices |
| --- | --- | --- |
| `jev_ficha_revision` | id, listing_id, hechos_json, fuente, autor, observado_en, valido_desde/hasta, reemplaza_id, hash | Inmutable; único listing+hash+vigencia; índice listing+observado_en |
| `jev_contexto` | id, sujeto_tipo/id/revision, finalidad, payload_json, schema_version, huella, capturado_en | Inmutable; índice sujeto+capturado_en; referencias de decisión/borrador validadas; sin unicidad global de huella |
| `jev_solicitud` | id, contexto_id, solicitante, idempotencia, creada_en | Inmutable; único cuenta+solicitante+idempotencia; repetición con otro sujeto devuelve conflicto |
| `jev_trabajo` | solicitud_id, estado, disponible_en, lease_token, lease_hasta, intentos | PK solicitud; índice parcial estado+disponible_en; único propietario mediante claim atómico |
| `jev_intento` | id, solicitud_id, ordinal, inicio/fin, resultado_operativo, modelo_reportado, usage, duracion, error_codigo | Inmutable al insertar intento terminado; único solicitud+ordinal; índice solicitud+inicio |
| `jev_evaluacion` | id, solicitud_id, intento_id, resultado_json, resultado_version, finalizado_en | Inmutable; único solicitud_id; índices resultado_version+finalizado_en y solicitud |

El contexto incluye miembros, fichas completas o ausencias, destinos, evidencia económica con moneda y ventana, versiones de normalización/preguntas/composición/modelo y bytes canónicos enviados. La huella identifica contenido; nunca autoriza reutilizar otra evaluación. Las lecturas dominantes localizan el sujeto y recuperan su última solicitud, resultado e historial mediante esos índices.

`jev_ads` escribe solicitudes y resultados. `jev_catalogo` admite nuevas revisiones de fichas. Sólo la cola muta: claim, lease y cierre. El worker confirma resultado y cierre juntos. Un worker cuyo lease expiró no puede publicar: comprueba el token dentro de la transacción final. Un fallo tras HTTP puede repetir consumo externo; la unicidad de resultado evita duplicar evidencia final, según `make-operations-idempotent`.

### Versiones, tiempo y permisos

Dos fechas tienen significado distinto: evidencia de la decisión y observación del catálogo. Una ficha registrada después no demuestra qué se conocía al decidir. La UI etiqueta «revisión posterior»; el replay histórico sólo admite hechos conocidos en aquel corte. El snapshot declara ambos tiempos.

Se fija `jev-1.13.0`. Cambiar modelo, preguntas, parser o composición requiere nueva versión y solicitud; nunca reescribir resultados. Vigencia termina al vencer fichas/censo o cambiar miembros, vínculos, destino, borrador o una versión activa. `consultar` calcula `Obsoleta` con lecturas locales y muestra los motivos. No agenda trabajos desde GET. El resultado histórico permanece disponible.

El ensayo comienza con caducidad máxima de censo de 24 horas y de fichas manuales de 30 días, siempre acortada por su vigencia declarada. Son parámetros conservadores de versión, pendientes de contrastar con la frecuencia de cambios; no afirman una propiedad real del catálogo.

Un rol de catálogo inserta fichas; un rol solicitante crea análisis; el worker lee fuentes locales e inserta sus tablas; el dashboard sólo tiene SELECT. Ninguno recibe permisos sobre decisiones, bibliotecas o escrituras Amazon por este servicio. La comprobación del sujeto y ámbito ocurre antes de revelar existencia. El contexto enviado excluye secretos y datos personales innecesarios; las credenciales TypeSafe viven fuera de PostgreSQL y los artefactos.

### API y operación

`POST /api/ads/asesoria` recibe sujeto e idempotencia y devuelve 202 con ticket. `GET /api/ads/asesoria/{ticket}` devuelve estado, relaciones, fechas, versiones, motivos de obsolescencia e historial autorizado. La UI actual de cortes puede incorporar esa vista junto a `app/api_dashboard.py:1047`. Un comando separado solicita lotes limitados por fecha y finalidad; otro procesa pendientes. Ningún scheduler se instala durante el diseño.

La cuota limita solicitudes y bytes de entrada antes del envío; el costo real requiere usage verificable. Un intento normal admite dos reintentos por timeout, 429 o 5xx con espera y dispersión. Errores de autenticación, validación o respuesta inválida terminan sin reintento automático. Timeout y lease tienen límites explícitos. Métricas contabilizan cobertura, faltantes, obsolescencia, errores, latencia, consumo y desacuerdos revisados. Deshabilitar el worker deja visible su estado sin afectar Ads.

### Pruebas y expansión

Los ejemplos discriminantes cubren compatible más desconocido, todos incompatibles con censo completo, censo incompleto aparentemente incompatible, cero miembros y variantes del mismo producto. Otras pruebas cambian destino, ficha, membresía y versión tras evaluar; verifican resultado histórico intacto y vista obsoleta. Un contrato comprueba que GET no escribe; otro que ningún permiso permite mutar Ads o bibliotecas. Los fallos simulados cubren respuesta incompleta, inyección textual, timeout, crash tras HTTP y publicación con lease vencido.

Antes de producción se validan censo real, correspondencias de listings, límites TypeSafe, confianza calibrada y ejemplos mexicanos/estadounidenses etiquetados por humanos. El ensayo separa negativos, harvest y semillas; informa cobertura y desacuerdos, sin atribuir ahorro. Futuras fichas automáticas entran como nuevas revisiones con procedencia. Una futura política activa exige diseño y autorización propios, evidencia medida y contratos nuevos; las evaluaciones de V1 nunca adquieren autoridad retroactiva.

## Decisión de síntesis

Pendiente del orquestador. Este candidato ofrece una base centrada en sujetos Ads y evidencia congelada; no compara candidatos ajenos.

## Compromisos aceptados

- Aceptamos duplicar contexto y consultas Jev entre decisiones para conservar evidencia independiente y evitar invalidez oculta por reutilización.
- Aceptamos resultados insuficientes frecuentes durante el censo inicial para impedir conclusiones universales sobre productos desconocidos.
- Aceptamos revisión posterior y trabajo asíncrono para conservar independencia operativa del motor Ads.

## Alternativas consideradas

Una matriz reutilizable término×ficha reduce llamadas, pero obliga al consumidor a reunir resultados, versiones y cobertura por cada finalidad y destino. Una evaluación integrada en hygiene reduce demora visible, pero incorpora disponibilidad externa al camino de decisión y dificulta distinguir ausencia de asesoría de reglas económicas. Ambas interfaces exponen más coordinación al caller.

## Preguntas y riesgos previos a producción

¿El censo demuestra cobertura del grupo con las fuentes locales disponibles? Se resuelve con muestreo contra una exportación autorizada antes de activar el ensayo. ¿Los lectores distinguen incompatibilidad de rentabilidad y revisión posterior de evidencia histórica? Se valida con tareas UI sobre casos etiquetados. Estas validaciones no requieren decisiones humanas para completar el diseño.

## Siguiente paso de implementación

Construir el prototipo puro de composición y obsolescencia con fixtures, seguido del contrato de captura de contexto, sin conectar escrituras Ads.
