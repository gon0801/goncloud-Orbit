# Diseño de JEV ADS 02: la señal por búsqueda-en-grupo

**Estado:** sellado por el dueño el 2026-10-04 ("va, a las cuatro cosas"; ver
"Lo que el dueño selló"). Es el spec
delta de la tarea 0.1 de [JEV ADS 02](../../../plans/jev-ads-02.md). El
[diseño de JEV ADS 01](2026-10-03-jev-ads-design.md) sigue vigente para tipos,
fichas, composición y el contrato con TypeSafe; aquí solo va lo que se agrega
o cambia. Precedencia: `docs/CONTEXTO.md` > `docs/APPLY.md` > diseño 01 > este
documento > plan; en lo que este documento declara cambiar (la reutilización
de juicios, el roster probado y el job automático) prevalece sobre el 01.

Cómo se llegó: fundamento con lectura del código, cinco prototipos de solo
lectura en producción, un prototipo con Jev que corrió el dueño, tres
candidatos de diseño independientes y un juez cruzado. Registro en
`docs/evidencia/jev-ads-02/diseno/`.

## Problema

El plan pensaba el aviso como "Jev dice si la búsqueda corresponde". Los
prototipos mostraron que eso no separa nada en este catálogo:

- De los 18 pares `negative` que el motor ha propuesto, 17 son búsquedas del
  catálogo. Jev diría "sí corresponde" casi siempre.
- Casi todo el gasto sin venta es de búsquedas relevantes que no convierten en
  ese grupo. Lo ajeno estricto fue 10.7% del gasto de la muestra en MX y 1.5%
  en US.
- Lo que separa un bloqueo sano de uno peligroso está en datos que la base ya
  tiene y que el motor no mira al decidir por grupo: si la misma búsqueda
  vende en otro ad group, y si alguna vez vendió en este. En 11 de las 17 la
  búsqueda vende en otro grupo; en 8 el propio grupo tiene ventas en su
  historial.
- Lo más informativo que da Jev no es el "sí" o "no" del grupo sino la
  proporción: "corresponde a 20 de 208 productos" y "a 177 de 177" son
  búsquedas distintas. Cerca de la mitad del gasto sin venta de la muestra de
  MX es de búsquedas con atributo (oro, plata) en grupos que mezclan los dos.

La forma no es obvia por cuatro restricciones que chocan:

- El motor y la cola no pueden depender de Jev, pero más adelante el motor
  tiene que poder leer algo estable para abstenerse, sin código de Jev.
- `app_jev` no puede leer decisiones, cola ni `search_term_observation`, y la
  señal necesita las tres.
- Decir "no corresponde a ninguno" exige conocer todos los productos del ad
  group, y hoy `censo_grupo` devuelve `exhaustivo=False` como constante.
- No hay tabla de corridas, contador diario ni lector de settings
  compartidos, y `app/cli.py` está a 22 líneas de su tope.

## Uso

Cinco puntos de contacto. Ninguno coordina pasos internos.

```python
# 1. Cron, fuera del ciclo. app/cli.py gana unas 9 líneas ("main del módulo"); tiene 22 libres.
#    30 9,21 * * * flock -n ... docker exec orbit-app-1 python -m app.cli jev-senales --aplicar
if args.comando == "jev-senales":
    from app import jev_senales
    return jev_senales.main(rest)        # 0 corrió, apagado u ocupado; 1 fallo; 2 falta DSN

# 2. GET /cortes, junto a la asesoría que ya pinta.
item["senal"] = jev_senales.de_propuestas(conn, decision_ids)[decision_id].como_dict()

# 3. GET /gasto-sin-venta (pantalla nueva).
pantalla = jev_senales.gasto_sin_venta(conn, plataforma="amazon_mx")

# 4. GET /salud, bloque nuevo.
datos["jev"] = jev_senales.salud(conn, ahora=ahora)
```

```sql
-- 5. Costura para efectos futuros: UNA vista, legible sin código de Jev.
SELECT senal_id, lectura FROM jev_senal_vigente
 WHERE plataforma = %s AND ad_group_id = %s AND termino = %s AND vigente;
```

Sin `--aplicar` el job corre en seco: dice qué unidades entrarían y cuántas
llamadas pagaría, y no escribe nada.

## Forma

### Tres hechos, una lectura

La unidad es la **búsqueda-en-grupo** (`ClaveBusqueda`: plataforma,
`ad_group_id` interno, término literal). Una propuesta de corte del motor y
una búsqueda que gasta sin vender son la misma unidad. Tiene tres hechos, cada
uno con una sola fuente de verdad:

| Hecho | Pregunta | Fuente de verdad | Lo escribe |
| --- | --- | --- | --- |
| Roster | ¿Qué productos se anuncian en el grupo, y está probado que son todos? | Catálogo más el acta de listado de la ingesta | `app_ingest` |
| Juicio | ¿La búsqueda corresponde a este producto? | El primer éxito de `jev_par_evento` para esa `ClavePar` | `app_jev` |
| Economía | ¿Vendió aquí, vende en otro grupo, cuánto gastó? | `search_term_observation` con la ventana de cortes del motor | `app_ingest` |

La **lectura** es una función pura de los tres (`jev_lectura.leer`). Se
congela en una fila de `jev_senal` con los datos exactos de los que salió.
Telegram, las pantallas y las costuras leen esa fila; nadie recalcula.

### La regla

`leer(economia, relevancia)`. Orden estricto; la primera que aplica gana:

| # | Condición | Lectura | Qué le dice al dueño |
| --- | --- | --- | --- |
| 1 | Alguna orden en todo el historial de este grupo, contando los días que traen dato | `vendio_aqui` | Aquí ya convirtió. Bloquear es el riesgo. |
| 2 | Otro grupo de la plataforma tiene órdenes en su ventana madura | `vende_en_otro` | Bloquear aquí junta el tráfico donde ya vende. |
| 3 | Sin observaciones, o falta un dato de órdenes | `sin_lectura` | Un dato faltante no es cero. |
| 4 | No vende en ninguno y algún producto corresponde | `relevante_sin_venta` | Es del catálogo y no convierte. |
| 5 | No vende en ninguno y ningún producto corresponde, con roster probado | `ajena` | Bloquear, y pronto. |
| 6 | No vende en ninguno y Jev no pudo concluir o no se le preguntó | `sin_lectura` | Con el motivo a la vista: fallo, ficha faltante, sin cupo. |

Decisiones que cargan peso:

- **Las ventas mandan; Jev desempata.** Las reglas 1 y 2 van antes que
  cualquier juicio. Un juicio nunca tapa una venta, y un fallo de TypeSafe o
  la falta de cupo no impiden esas dos lecturas. El job puede encenderse con
  tope 0 y ya entrega lectura sin una sola llamada.
- **"No vende" solo sobre ventana madura; "vendió" sobre todo lo observado.**
  Una orden es un hecho positivo aunque tenga 4 días. La ausencia de órdenes
  solo cuenta en los 30 días que terminan 10 atrás (regla 6 de CONTEXTO).
- **La proporción siempre viaja.** La señal guarda cuántos productos
  corresponden, cuántos se evaluaron y cuántos hay, y cuáles dijeron "sí".
  Se evalúa el grupo completo aunque el primero ya satisfaga: el costo es de
  centavos y parar antes tira el dato más útil.
- **`ajena` es inalcanzable sin roster probado**, por tres candados: `leer`
  la da solo con `NingunoCompatible`; `componer` da `NingunoCompatible` solo
  con censo exhaustivo; y el censo de un grupo solo es exhaustivo con
  `RosterProbado`. La base lo repite con un CHECK.
- **`ajena` es estricta y frágil a propósito.** Un solo falso "sí" o una
  abstención la impide. Es la clase que un efecto futuro podría leer. Para el
  dueño manda la proporción (ver "Superficies").
- **Harvest.** Una propuesta de harvest son dos búsquedas-en-grupo, origen y
  destino, y cada una es una señal completa con su `lectura` de `leer`. Para
  el dueño, del destino importa la relevancia: `leer_destino(relevancia)` da
  `destino_corresponde`, `destino_ajeno` o `sin_lectura`. Eso se deriva al
  pintar y al avisar; no se guarda en `jev_senal`.
- **El universo son los productos anunciados hoy.** El roster de una señal
  deja fuera a los miembros cuyos anuncios están todos archivados, y
  `miembros` cuenta solo los que tienen un anuncio ENABLED o PAUSED. Sin eso
  `componer` agrega `no_anunciado` y `ajena` sería inalcanzable en 8 de los 22
  grupos con gasto de MX, que conservan 622 productos solo archivados. El CLI
  manual no cambia.

### Módulos

| Archivo | Estado | Qué sabe |
| --- | --- | --- |
| `app/jev_lectura.py` | nuevo, puro | Tipos de la señal, `leer`, `leer_destino`, `probar_roster`, `planear`, `ajustes_desde_settings` |
| `app/jev_libro.py` | nuevo, IO | El libro de juicios por par: único que inserta eventos `intencion` y `resultado` (lo que cuesta dinero) y único que conoce el tope diario |
| `app/jev_senales.py` | nuevo, IO | El job (`correr`, `main`) y las tres lecturas de pantalla. Único que conoce `jev_senal`, `jev_roster`, `jev_aviso*` y `jev_corrida` |
| `app/jev_vista.py` | tocado, puro | Texto del aviso, frases por lectura y bandas de proporción |
| `app/jev_asesor.py` | tocado | Deja de insertar `intencion` y `resultado` a mano: llama a `Libro.pagar`. Conserva sus eventos `reutilizacion` y su política |
| `app/jev_catalogo.py` | tocado | `resolver_fichas`: la regla de fichas sale de `_enriquecer` para que asesor y roster usen la misma |
| `app/ads/structure*.py` | tocados | Registrar el acta de listado. `listar_todo` queda como envoltura de `listar_con_prueba`, que devuelve además el total declarado; sus otros consumidores no cambian |
| `app/optimizer/windows.py` | tocado | Expone la subconsulta de colapso (última observación por día) como constante compartida, sin cambiar `terminos_cortes`. Tiene 874 líneas de 900: no cabe una consulta nueva |
| `app/notifica.py` | tocado | `envia_aviso(texto) -> bool`, genérico y fail-silent; devuelve False si el canal está inactivo. No importa Jev |
| `app/cli.py`, `api_dashboard.py`, `ui.py`, plantillas | tocados | Registro del comando y las lecturas, con import perezoso |

El historial ("¿vendió alguna vez aquí?") lo arma `jev_senales` sobre esa
subconsulta compartida, con su propio agregado: cuenta las órdenes de los días
que sí traen dato y marca aparte si algún día vino sin dato. No puede reusar
el agregado del motor, que devuelve NULL si falta un solo día: un día sin dato
borraría una venta real de otro día.

Seguir un caso toca tres archivos: `cli` → `jev_senales.correr` →
`jev_libro.juicio`. `jev_senales` expone cinco funciones y esconde dos
logins, el roster y su prueba, el orden, la idempotencia, el tope, el corte
por proveedor caído y los avisos. `jev_libro` expone una pregunta: "dame el
juicio de este par; paga solo si hace falta y hay cupo".

### Tipos que fijan invariantes

El bosquejo completo, con cada firma y sus invariantes, está en
`docs/evidencia/jev-ads-02/diseno/bosquejo.py`. No es código de `app/`: es el
contrato que la implementación debe cumplir. Lo que sigue es el resumen.

```python
Lectura = Literal["vendio_aqui", "vende_en_otro", "relevante_sin_venta", "ajena", "sin_lectura"]

@dataclass(frozen=True)
class Economia:
    aqui: Gasto | None                      # ventana madura; None = sin observaciones
    otros_que_venden: tuple[VentaEnOtroGrupo, ...]   # solo con órdenes > 0
    otros_sin_dato: int                     # mientras sea > 0 no existe "no vende en ninguno"
    historial: Historial | None             # todo lo observado; solo afirma "vendió":
                                            # órdenes de los días con dato, y cuántos días sin dato

PruebaRoster = RosterProbado | RosterSinProbar          # la prueba es un tipo, no un booleano

@dataclass(frozen=True)
class Relevancia:
    conjunto: HayCompatible | NingunoCompatible | Indeterminado | NoEvaluada
    satisfacen: int; evaluados: int; miembros: int     # SIEMPRE, también en Indeterminado
    productos_ok: tuple[int, ...]; juicio_ids: tuple[UUID, ...]

def leer(economia: Economia, relevancia: Relevancia) -> tuple[Lectura, frozenset[MotivoSinLectura]]: ...
def probar_roster(censo, acta, *, huella_en_base, ahora, max_edad) -> PruebaRoster: ...
def ajustes_desde_settings(config_version_id, settings) -> Ajustes | Apagado: ...

class Libro:
    def juicio(self, termino, ficha, *, lote, tope_diario) -> Juicio | FalloProveedor | SinCupo: ...
```

`NoEvaluada` separa "no se le preguntó a Jev" (sin cupo, o tope 0) de "Jev no
pudo concluir". `SinCupo` no es fallo ni evidencia.

### Persistencia

**Cambio a la decisión R4.** Hoy un éxito solo se reutiliza dentro de su
revisión. Aquí, **un juicio pagado vale para cualquier señal que lo cite por
id.** El juicio recibe solo el término literal y una ficha, así que no depende
de quién lo pidió. Lo que R4 protegía se conserva: la señal guarda los ids de
los eventos que usó y es inmutable, así que nada se "mejora" después. Sin este
cambio, pausar un anuncio volvería a pagar los 50 productos de su grupo y una
búsqueda presente en 9 grupos se pagaría 9 veces. El trigger de 0049 no se
toca: restringe los eventos `reutilizacion`, y el job no los usa. El CLI
manual conserva su regla.

Una corrida del job es una `jev_revision` con `sujeto_tipo = 'lote'`: una
ejecución con su contexto congelado antes del primer HTTP. De ella cuelgan
los eventos que paga. No hay reanudación por solicitud: lo que se retoma son
pares sin éxito, y eso es global.

```sql
-- Migración A: acta de listado (la ingesta). Misma transacción que sella ingest_run.
CREATE TABLE ads_listado_plataforma (
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_run(id),
    platform      platform NOT NULL,
    ad_groups_recibidos    INTEGER NOT NULL, ad_groups_declarados   INTEGER,  -- NULL = Amazon no lo declaró
    product_ads_recibidos  INTEGER NOT NULL, product_ads_declarados INTEGER,
    product_ads_sin_grupo  INTEGER NOT NULL,
    PRIMARY KEY (ingest_run_id, platform));
CREATE TABLE ads_listado_grupo (
    ingest_run_id BIGINT NOT NULL, platform platform NOT NULL,
    ad_group_id   BIGINT NOT NULL REFERENCES ad_entity(id),
    anuncios_vivos INTEGER NOT NULL,
    huella_vivos   TEXT NOT NULL,        -- sha256 de los adId ENABLED/PAUSED, ordenados
    descartados    INTEGER NOT NULL,     -- no archivados del payload que no se escribieron
    PRIMARY KEY (ingest_run_id, ad_group_id),
    FOREIGN KEY (ingest_run_id, platform) REFERENCES ads_listado_plataforma);

-- Migración B: señales.
ALTER TABLE jev_revision
  DROP CONSTRAINT jev_revision_sujeto_tipo_check,      -- CHECK inline de 0049
  ADD  CONSTRAINT jev_revision_sujeto_tipo_check
       CHECK (sujeto_tipo IN ('decision', 'semillas', 'lote')),
  DROP CONSTRAINT jev_revision_sujeto_coherente,
  ADD  CONSTRAINT jev_revision_sujeto_coherente CHECK (
       (sujeto_tipo = 'decision' AND decision_id IS NOT NULL AND plan_canonico IS NULL
            AND plan_sha256 IS NULL AND fuentes_semillas IS NULL)
    OR (sujeto_tipo = 'semillas' AND decision_id IS NULL AND plan_canonico IS NOT NULL
            AND plan_sha256 IS NOT NULL AND fuentes_semillas IS NOT NULL)
    OR (sujeto_tipo = 'lote' AND decision_id IS NULL AND plan_canonico IS NULL
            AND plan_sha256 IS NULL AND fuentes_semillas IS NULL));
-- Una fila 'lote' llena `censos` (resumen del plan de la corrida) y `contrato`,
-- que son NOT NULL. Los triggers de 0049 y 0050 la aceptan tal como están.
CREATE TABLE jev_roster (                -- hecho 1 por contenido: sin synced_at y sin la prueba
    sha256 TEXT PRIMARY KEY, plataforma platform NOT NULL,
    ad_group_id BIGINT NOT NULL REFERENCES ad_entity(id),
    miembros JSONB NOT NULL, ficha_version_ids UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE jev_senal (
    id UUID PRIMARY KEY,
    lote_id UUID NOT NULL REFERENCES jev_revision(solicitud),
    plataforma platform NOT NULL, ad_group_id BIGINT NOT NULL REFERENCES ad_entity(id),
    termino TEXT NOT NULL, termino_sha256 TEXT NOT NULL, insumos_sha256 TEXT NOT NULL,
    regla_version INTEGER NOT NULL,
    roster_sha256 TEXT NOT NULL REFERENCES jev_roster(sha256),
    roster_probado BOOLEAN NOT NULL, roster_prueba JSONB NOT NULL,
    contrato_sha256 TEXT NOT NULL,
    relevancia TEXT NOT NULL CHECK (relevancia IN ('corresponde','ajena','sin_veredicto','no_evaluada')),
    motivos_jev TEXT[] NOT NULL DEFAULT '{}',
    productos_ok BIGINT[] NOT NULL DEFAULT '{}',
    evaluados INTEGER NOT NULL, miembros INTEGER NOT NULL, juicio_ids UUID[] NOT NULL DEFAULT '{}',
    ventana_inicio DATE, ventana_fin DATE, datos_hasta TIMESTAMPTZ,
    moneda currency NOT NULL,     -- la de la plataforma; todo importe de la fila, también en JSONB, va en ella
    clics BIGINT, gasto money_amount, ordenes INTEGER,           -- NULL = dato faltante, nunca cero
    otros_que_venden JSONB NOT NULL DEFAULT '[]', ordenes_otros INTEGER, otros_sin_dato INTEGER NOT NULL,
    historial JSONB, ordenes_historial INTEGER,
    lectura TEXT NOT NULL CHECK (lectura IN
        ('vendio_aqui','vende_en_otro','relevante_sin_venta','ajena','sin_lectura')),
    motivos_lectura TEXT[] NOT NULL DEFAULT '{}',
    valida_hasta TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ad_group_id, termino_sha256, insumos_sha256),
    CONSTRAINT jev_senal_moneda_de_plataforma CHECK (             -- regla 4 de CONTEXTO
        (plataforma = 'amazon_mx' AND moneda = 'MXN') OR (plataforma = 'amazon_us' AND moneda = 'USD')),
    CONSTRAINT jev_senal_ajena_exige_roster CHECK (relevancia <> 'ajena'
        OR (roster_probado AND miembros > 0 AND evaluados = miembros
            AND cardinality(motivos_jev) = 0 AND cardinality(productos_ok) = 0)),
    -- IS NOT DISTINCT FROM, no "=": con NULL, "ordenes_otros = 0" da NULL y el CHECK pasaría.
    CONSTRAINT jev_senal_lectura_ajena CHECK (lectura <> 'ajena'
        OR (relevancia = 'ajena' AND otros_sin_dato = 0
            AND ordenes IS NOT DISTINCT FROM 0 AND ordenes_otros IS NOT DISTINCT FROM 0
            AND ordenes_historial IS NOT DISTINCT FROM 0)));

CREATE TABLE jev_aviso (                 -- lo que se le quiso decir al dueño, tal cual
    id UUID PRIMARY KEY, lote_id UUID NOT NULL REFERENCES jev_revision(solicitud),
    cola_id BIGINT NOT NULL, decision_id BIGINT NOT NULL,      -- sin FK al motor
    senal_origen_id UUID NOT NULL REFERENCES jev_senal(id),
    senal_destino_id UUID REFERENCES jev_senal(id),
    lectura TEXT NOT NULL,                -- la anunciada: la del origen; en harvest, la del destino
    texto TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (cola_id, lectura));
CREATE TABLE jev_aviso_entrega (         -- existe solo si Telegram aceptó el mensaje
    aviso_id UUID PRIMARY KEY REFERENCES jev_aviso(id),
    entregado_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE jev_corrida (               -- rastro para /salud: dos eventos por corrida
    lote_id UUID NOT NULL REFERENCES jev_revision(solicitud),
    evento TEXT NOT NULL CHECK (evento IN ('inicio','fin')),
    motivo TEXT, resumen JSONB, at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (lote_id, evento));

CREATE VIEW jev_senal_vigente AS         -- LA vigencia, definida una sola vez
SELECT DISTINCT ON (s.ad_group_id, s.termino_sha256)
       s.id AS senal_id, s.plataforma, s.ad_group_id, s.termino, s.lectura, s.created_at,
       (s.valida_hasta > now() AND NOT EXISTS (
            SELECT 1 FROM jev_roster ro JOIN jev_ficha_revocacion rv
                ON rv.ficha_version_id = ANY (ro.ficha_version_ids)
             WHERE ro.sha256 = s.roster_sha256)) AS vigente
  FROM jev_senal s ORDER BY s.ad_group_id, s.termino_sha256, s.created_at DESC, s.id DESC;
```

Todas las tablas nuevas son append-only (`prohibir_mutacion`) y llevan índice
en cada clave foránea, como hizo 0049.

**`insumos_sha256`** es el hash canónico de todo lo que entra a `leer` más el
día en que se selló: versión de la regla, contrato, `roster_sha256`,
`roster_probado`, ventana, los números de venta (aquí, otros grupos,
historial), la relevancia con sus `juicio_ids` y motivos, y la fecha UTC. Con
eso, dos corridas del mismo día con los mismos datos no duplican la fila; una
corrida que por fin logra los juicios que antes fallaron sí sella una nueva; y
al día siguiente siempre hay fila nueva, aunque nada haya cambiado.

**`valida_hasta`** es lo que ocurra primero: 36 horas desde que se selló, o el
vencimiento más próximo (`revisar_antes_de`) de las fichas de su roster. Las 36
horas son un latido operativo: si el job deja de correr, las señales dejan de
estar vigentes solas.

| Objeto | `app_jev` | `app_read`, `app_admin` | `app_ingest` | `app_decide` |
| --- | --- | --- | --- | --- |
| Tablas `jev_*` nuevas y la vista | SELECT, INSERT | SELECT | REVOKE ALL | REVOKE ALL |
| `ads_listado_*` | nada | SELECT | SELECT, INSERT | SELECT |

El REVOKE es explícito porque `0001` concede SELECT por omisión sobre toda
tabla nueva. Comprobado en producción el 2026-10-04: por ese permiso por
omisión, `app_decide` y `app_ingest` ya pueden leer `jev_revision`,
`jev_par_evento` y `jev_ficha_version`; hoy lo único que separa al ciclo de
Jev es la guarda de imports. La migración B revoca también en las cuatro
tablas de 0049 y termina con un bloque que falla si `app_decide` puede leer
cualquier tabla `jev_*` o si `app_jev` puede leer `decision`, `apply_queue` o
`search_term_observation`. Las tablas de Jev no tienen FK hacia tablas del
motor: guardan el número que les pasa el lector, y así Jev no toma ningún
candado sobre una fila de la cola.

**Logins.** El job lee con `ORBIT_DSN_READ` y escribe con `ORBIT_DSN_JEV`, un
login nuevo `orbit_jev` miembro solo de `app_jev`. `app_jev` no gana ningún
permiso sobre tablas existentes.

**Qué queda reconstruible.** De un aviso, su texto exacto y las señales que
citó. De una señal, los números de venta y su ventana, el roster con sus
fichas, la prueba del roster con su corrida de ingesta, el id de cada evento
de juicio y la versión de la regla.

### Roster probado

Hoy la base no distingue "el grupo tiene estos 22 anuncios" de "estos son los
22 que conozco". Un anuncio que la ingesta salta por un filtro no deja rastro
por grupo.

**Qué registra la ingesta** (acta, por corrida): por plataforma, cuántos ad
groups y product ads llegaron, cuántos declaró Amazon (`totalResults`, o NULL)
y cuántos anuncios no archivados venían sin grupo atribuible; por ad group,
cuántos anuncios vivos se escribieron, la huella de sus ids y cuántos no
archivados se descartaron. Una plataforma cuyo perfil se rechazó no tiene
fila. Una corrida que falla no deja acta.

**La regla** (`probar_roster`). Probado solo si todo se cumple; cada condición
que falla agrega su motivo:

1. Hay acta de una corrida ok con menos de `windows.MAX_EDAD_SYNC` (48 h, el
   número que el motor ya usa).
2. Los totales declarados existen e igualan a los recibidos, en ad groups y
   en product ads.
3. No hubo anuncios sin grupo.
4. El ad group tiene acta, sin descartados.
5. La huella del acta es igual a la huella de los anuncios ENABLED o PAUSED
   que la base tiene hoy para ese grupo.
6. Ningún anuncio carece de estado y todo anuncio vivo liga a producto.

La comparación de huellas no depende de `synced_at`, detecta un anuncio de más
en la base y uno de menos, y se invalida sola si algo cambia entre la ingesta
y el job. Con los datos de hoy la regla es alcanzable: todo el gasto de
búsquedas está en 22 grupos de MX y 11 de US donde cada anuncio activo tiene
producto y ficha.

### El trabajo fuera del ciclo

- **Qué toma y en qué orden** (`planear`, puro). Primero las propuestas
  `negative` y harvest en `pending_veto` o `released`, por vencimiento.
  Después las búsquedas con gasto y cero órdenes en la ventana madura, desde
  `jev.min_clics`, no ASIN-like y sin corte ya aplicado por Orbit: primero las
  que necesitan a Jev para tener lectura, por gasto y alternando plataformas
  (no se comparan MXN con USD); al final las que las ventas ya resuelven.
  También vuelve a sellar toda búsqueda con señal vigente aunque ya no sea
  candidata, para que la pantalla no diga "sin venta" de algo que vendió ayer.
- **Identidad.** La `ClaveBusqueda`. No hay UUID que recordar entre corridas.
- **Tope diario.** `jev.tope_diario`. El gasto del día es el conteo de eventos
  `intencion` del día UTC, de cualquier origen. La reserva es atómica: candado
  de transacción, conteo, inserción de la intención, commit, y solo entonces
  el HTTP. No hay tabla de contador. Sin clave de API no se abre ninguna
  intención.
- **Apagado.** `Apagado` si `jev.senales` no es el JSON `true`, o si
  `jev.tope_diario` o `jev.min_clics` faltan o no son enteros válidos. Apagado
  significa cero filas y cero HTTP. `jev.tope_diario = 0` es un modo válido.
  `jev.avisos` enciende Telegram aparte.
- **Candados.** `flock -n` en el crontab y `pg_try_advisory_lock` de sesión.
  Ocupado sale con 0 sin escribir.
- **Rastro para `/salud`.** Inicio y fin en `jev_corrida`. Inicio sin fin: se
  cayó o sigue corriendo. El bloque muestra interruptor y motivo, llamadas de
  hoy contra el tope, búsquedas que esperan cupo, grupos con roster probado
  contra grupos con gasto (con motivos), búsquedas donde `ajena` quedó
  bloqueada solo por una abstención o un fallo, avisos sin entrega y fichas
  que vencen en 14 días.

### Superficies

**Telegram.** Un aviso por propuesta en veto cuando tiene lectura, y otro solo
si pasa a una lectura que no se le había anunciado. Se inserta `jev_aviso`
antes de enviar y `jev_aviso_entrega` solo si Telegram aceptó; canal inactivo
no cuenta como entrega. Un aviso sin entrega se reintenta en la corrida
siguiente con el mismo texto guardado, y por eso el texto lleva la fecha en
que se aplica, no "faltan n horas". Texto plano. Forma (lo que va entre ‹ › lo pone el
job):

```
[Orbit · Jev] negative en veto: "‹búsqueda›"
‹plataforma› · ad group ‹nombre›
Se aplica solo el ‹fecha y hora UTC› si no lo rechazas.

YA VENDIÓ AQUÍ — RIESGO
Aquí: 0 órdenes en la ventana, pero ‹n› órdenes y ‹n› clics en todo el historial.
Si la bloqueas, deja de aparecer donde ya convirtió.
Jev: corresponde a ‹k› de ‹n› productos de este ad group.
Rechazar: /cortes
```

Las otras cabeceras: `VENDE EN OTRO AD GROUP` ("bloquearla aquí junta ese
tráfico donde ya vende"), `CORRESPONDE Y NO VENDE EN NINGUNO — RIESGO`
("bloquearla aquí no la manda a otro lado"), `AJENA` ("no corresponde a
ninguno de los ‹n› productos; lista comprobada con el listado de Amazon del
‹fecha›") y `SIN LECTURA` con el motivo en claro. Nunca dice "ninguno" sin
roster probado.

**`/cortes`.** Cada propuesta gana una fila "Señal" encima de la asesoría que
ya existe: la lectura, la frase, tres renglones de evidencia (aquí en la
ventana; otros ad groups que venden; historial aquí) y "Jev: k de n". En
harvest, dos bloques. Señal no vigente: "desactualizada". Si la lectura
falla, la pantalla responde igual con "Señal no disponible".

**`/gasto-sin-venta`** (GET nuevo). Una pestaña por mercado, con "calculado
el…" y aviso si la última corrida tiene más de 36 h. Secciones por lectura,
cada una con su total. Dentro de "corresponden y no venden en ninguno", las
filas se ordenan por proporción ascendente y se agrupan en bandas: *ninguno*,
*pocos* (hasta 15%), *una parte* y *todos* (95% o más). Así una búsqueda que
corresponde a 8 de 176 productos sube junto a las ajenas, y las de atributo
(102 de 208) quedan juntas. Las bandas son solo presentación: no se guardan
ni las lee ningún efecto. Cada fila despliega qué productos dijeron "sí", que
es lo que permite corregir una ficha cuando el "sí" es un error.

### Costuras para los efectos

No se construye ningún efecto. Queda la forma del dato.

- **El motor.** Lee `jev_senal_vigente` con SQL crudo, filtra `vigente` y
  congela `senal_id` y `lectura` en `decision.inputs` para que el replay
  reproduzca la decisión. Sin fila vigente decide como hoy. El permiso a
  `app_decide` no se concede en este bloque.
- **La herramienta del dueño.** Una aprobación cita un `senal_id` y una
  acción. Antes del HTTP comprueba que ese `senal_id` sigue siendo el vigente.
  Si llegó una venta, el job ya selló una señal nueva y la aprobación queda
  sin efecto sola. Para un ruteo, `otros_que_venden` nombra los grupos donde
  la búsqueda vende y `productos_ok` los productos a los que corresponde.

### Qué pasa si

| Situación | Resultado |
| --- | --- |
| Corre dos veces | Pares con éxito: cero HTTP. Señales con los mismos insumos: cero filas. Avisos: chocan con su UNIQUE. Solo se reintentan los pares que fallaron. |
| Dos procesos a la vez | El segundo no obtiene el candado y sale con 0. |
| Se cae a medias | Cada juicio y cada señal tienen commit propio. La intención huérfana cuenta contra el tope y su par se paga de nuevo. `/salud` muestra el inicio sin fin. |
| TypeSafe falla | El par queda como fallo; la lectura cae en hechos de venta o en `sin_lectura`, nunca en `ajena`. Tras 5 fallos seguidos el job deja de pagar y sigue sellando. La cola no se entera. |
| Una ficha vence o se revoca | La vista marca la señal no vigente a esa hora. En la corrida siguiente el miembro queda sin ficha. |
| La ingesta de estructura no corrió | Con acta de menos de 48 h la prueba vale solo si la huella coincide. Sin acta, `ajena` no puede salir; las lecturas de ventas siguen. |
| El job lleva días apagado | Las señales dejan de estar vigentes para la costura y las pantallas dicen "desactualizada". |

### Pruebas que deben discriminar

1. **`ajena` exige roster probado.** Todos `no_satisface`, cero ventas y una
   huella que difiere en un anuncio: `sin_lectura`. Un INSERT directo de
   `ajena` con `roster_probado = false` o con `ordenes_otros` NULL lo rechaza
   la base.
2. **Un juicio no tapa una venta.** Roster probado, todos `no_satisface` y
   órdenes en el historial: `vendio_aqui`. Con venta en otro grupo:
   `vende_en_otro`.
3. **Dato faltante no es cero, y la madurez.** Otro grupo con `orders` NULL:
   `sin_lectura`. Una orden de hace 5 días cuenta como "vendió"; su ausencia
   ahí no afirma nada.
4. **No se paga dos veces.** La misma búsqueda y ficha en dos grupos cuesta 1
   llamada; segunda corrida, 0; mover `synced_at` de todos los anuncios entre
   corridas, 0; agregar un producto al roster, exactamente 1.
5. **El tope aguanta una caída.** El transporte revienta en la llamada k; se
   corre otra vez; las intenciones del día nunca pasan del tope.
6. **Apagado es cero.** Con `jev.senales` ausente, `"true"` como texto o tope
   negativo: ninguna tabla `jev_*` cambia, el transporte no se toca y el ciclo
   y la cola producen lo mismo.
7. **Perímetro de roles.** Como `app_jev`, leer `decision`, `apply_queue` y
   `search_term_observation` falla. Como `app_decide`, leer cualquier tabla
   `jev_*` (las de 0049 incluidas) y la vista falla.
8. **GET puro, vigencia viva y la fila es la verdad.** Las dos pantallas con
   un transporte que revienta y conteos iguales antes y después; tras revocar
   una ficha, la señal sale "desactualizada"; y para toda fila guardada,
   `leer(...)` con sus insumos y su `regla_version` da la `lectura` guardada.
9. **El aviso es honesto.** Telegram falla o el canal está inactivo: hay
   `jev_aviso` y no hay entrega; la corrida siguiente lo envía una sola vez.
10. **El universo son los anunciados hoy.** Un grupo con un producto cuyos
    anuncios están todos archivados, roster probado y todos los activos en
    `no_satisface`: `ajena`, con `miembros` igual al número de activos.
11. **Una venta no se borra por un día sin dato.** Historial con una orden un
    día y `orders` NULL otro día: `vendio_aqui`.

La guarda de imports revisa además `apply_harvest_reconciliacion.py`,
`notifica.py` y `app/optimizer/*`, y suma a su lista de prohibidos los tres
módulos nuevos. Las pruebas usan
búsquedas sintéticas: el repo es público.

### Orden de entrega

Cada paso se despliega sin cambiar nada visible hasta su interruptor.

1. **Acta de listado en la ingesta.** Sin consumidor. Tras la primera corrida,
   una consulta de solo lectura dice cuántos grupos cumplen la regla.
2. **Núcleo puro** (`jev_lectura.py`) con sus pruebas de tabla, y los dos
   traslados sin cambio de comportamiento (`Libro.pagar`, `resolver_fichas`).
3. **Migración B, login `orbit_jev` y `ORBIT_DSN_JEV`.**
4. **El job y su cron**, con `jev.senales` ausente. Guarda ampliada. `/salud`.
5. **Pantallas.** Vacías hasta que haya señales.
6. **Encender con `jev.tope_diario = 0`.** Señales solo con ventas. Cero costo.
7. **Subir el tope.** Jev desempata y aporta la proporción.
8. **`jev.avisos`.** Telegram.

Los pasos 6 y 7 son cambios de config. El 8 trae el código del aviso y su
interruptor, y se construye después de medir en el 7. Cada encendido lleva el
go del dueño.

## Decisión de síntesis

**Base: candidato A** ("tres hechos, una lectura"). El juez cruzado, en otro
modelo, llegó a la misma base: fue el único cuya regla, unidad de persistencia
y prueba de roster sobrevivieron al prototipo con Jev sin cambiar de forma, y
el único que lleva la proporción como columnas.

Injertado:

- De C: `regla_version` y la prueba que recorre toda fila guardada; el aviso
  separado de su entrega; seco por omisión con `--aplicar`; `no_evaluada` como
  valor propio.
- De B: los totales de ad groups en el acta; la prueba de resincronización
  entre corridas.
- Del prototipo, que los tres pasaron por alto: bandas de proporción y orden
  por proporción en la pantalla; qué productos dijeron "sí"; el conteo en
  `/salud` de `ajena` bloqueada por una abstención.

Rechazado:

- El orden de B, que pone a Jev antes que las ventas: un "ninguno" taparía 7
  órdenes en el historial, y sin cupo no habría lectura.
- Parar al primer `satisface` y no consultar a Jev cuando las ventas hablan
  (C): tira la proporción.
- Probar el roster por `synced_at` (B) o por conteo (C): más débil que la
  huella.
- Una revisión por búsqueda con solicitud determinista y trigger relajado (B
  y C): obliga a tocar el trigger de 0049 y la reanudación del asesor.

## Costos aceptados

- Una señal por búsqueda-en-grupo y por día (unas 320 filas diarias), a cambio
  de que lo mostrado sea reconstruible y el motor tenga un dato estable.
- Pagar el grupo completo en cada búsqueda (unas 96 llamadas en el prototipo),
  a cambio de la proporción. Lo que escasea es el tope, no el dinero.
- Cambiar R4, a cambio de no repagar un grupo por un anuncio pausado.
- Dos políticas de reutilización mientras exista el CLI manual (él dentro de
  su revisión, el job global). Unificarlo o retirarlo se decide cuando el job
  lleve un mes.
- Una corrida guardada como `jev_revision` de tipo `lote`, a cambio de no
  tocar las restricciones de `jev_par_evento`.
- Que `vende_en_otro` no requiera a Jev: una venta en otro grupo prueba que la
  búsqueda es del catálogo mejor que un juicio.
- Un quinto login y DSN, a cambio de que el job no corra con `orbit_admin`.
- Telegram "al menos una vez": un duplicado es preferible a un aviso perdido
  dentro de 48 h. Una propuesta que va de una lectura a otra y regresa no se
  avisa por tercera vez.
- Claves de config con punto (`jev.senales`), como `fabrica.creacion`, aunque
  otras del repo usan guion bajo.
- No saber de negativos puestos a mano en Amazon: no se ingieren. La lista
  puede mostrar una búsqueda que ya no gasta; la ventana lo deja ver en días.
- `jev_senales` concentra job y lecturas de pantalla. Si se acerca al tope de
  tamaño se parte por lo que cada parte sabe, no por etapas.

## Alternativas consideradas

- **El job conduce `AsesorAds.evaluar` por decisión** (la forma del plan). Su
  unidad no cubre las búsquedas sin decisión, la reanudación entre días choca
  con `synced_at` y de todos modos obliga a cambiar R4. Quien llama tendría
  que conocer solicitudes, presupuestos y reanudación.
- **Señal derivada al leer, sin tabla.** El motor no puede ejecutar esa
  composición, el texto de Telegram no se reconstruye y la vigencia quedaría
  definida dos veces.
- **Contador diario propio o un motor nuevo en `apply_quota_state`.** Lo
  segundo cruza roles; lo primero es un dato que hay que mantener igual a los
  eventos. Contar intenciones da el mismo número sin sincronizar nada.
- **Una clase por banda de proporción dentro de `lectura`.** Metería umbrales
  sin calibrar en el dato que un efecto puede leer. Las bandas quedan como
  presentación.

## Preguntas abiertas y riesgos

Lo que el dueño selló el 2026-10-04:

1. Jev corre sola dos veces al día (09:30 y 21:30 UTC), con tope diario.
2. El cambio de R4: una respuesta ya pagada se reutiliza entre grupos y entre
   días.
3. Un grupo de Amazon puede dar "ninguno" cuando su roster esté probado.
4. Valores iniciales: `jev.tope_diario` 5,000 (cubre la primera pasada, de
   15,400 a 19,258 llamadas según cuánto se reutilice, en 3 o 4 días, y cuesta
   cerca de 0.25 USD diarios como máximo al precio de documentación) y
   `jev.min_clics` 3.

Encender cada interruptor en producción sigue siendo un cambio de config
aparte, con su go.

Lo que falta saber:

5. Amazon sí declara los totales. Comprobado el 2026-10-04 con una primera
   página de solo lectura por plataforma: `totalResults` viene como entero en
   ad groups (193 en MX, 79 en US) y en product ads (31,068 y 6,918). La regla
   de roster es alcanzable. Se comprobó la primera página, no la última:
   `listar_con_prueba` toma el total de la primera página que lo declare y lo
   coteja al final. Que lo siga declarando corrida tras corrida, y las
   condiciones de descartados y huella, las mide el acta del paso 1.
6. Las abstenciones bloquean `ajena`: 11 en 4,811 pares del prototipo. Se mide
   en `/salud` antes de pensar en tolerarlas.
7. Higiene de fichas: una palabra de la ficha que también es una búsqueda
   produce un falso "sí" (pasó con una familia entera). La pantalla lo deja
   ver; corregirlo es registrar una versión nueva de la ficha.
8. La llamada de la fábrica a la ingesta de estructura, ¿lista las dos
   plataformas completas? La regla lo tolera, pero conviene confirmarlo.

## Siguiente paso

Registrar el acta de listado en la ingesta de estructura: es la única pieza
que necesita corridas reales para probarse y la que decide si `ajena` es
alcanzable.
