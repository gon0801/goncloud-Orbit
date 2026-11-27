"""Los datos de una decision de bid (BIDS 02, politica niveles_v3). PURO.

CasoHoja es TODO lo que decide el bid de una hoja: el argumento de
`decide` y, serializado, `decision.inputs["caso"]`. Invariante:
`decide` no lee nada fuera de este valor.

Freeze unico: `como_json` (Decimal como string, fechas ISO, None
explicito) y su inversa exacta `desde_json`, que levanta ValueError
ante clave ausente, clave extra o tipo ajeno. Sin IO, sin reloj.
"""

from __future__ import annotations

import datetime as dt
import functools
import sys
import types
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, localcontext
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from app.optimizer import temporada_config as temporada
from app.optimizer.windows import AgregadoMetricas

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]

POLITICA_BID = "niveles_v3"  # se congela en inputs.politica; el replay despacha por esta clave
# ARREGLOS 02 B: revision 1 = el motor decide bids en centavos. Ausente en las
# decisiones viejas: su replay conserva la semantica de su era (4 decimales).
REVISION_POLITICA = 1
ESQUEMA_PESOS = "dos_tramos_v1"  # reciente D-50..D-10 peso 1; antiguo D-90..D-51 peso 1/2 (A5)
PESO_ANTIGUO = Decimal("0.5")
DIAS_MADUREZ = 10  # misma regla 6 que cortes (windows.DIAS_MADUREZ_CORTES): un numero, una fuente
DIAS_TRAMO_RECIENTE = 41
DIAS_LOOKBACK = 90  # windows.LOOKBACK_EVIDENCIA
PRECISION = 28  # espejo de evidencia._PRECISION: el unico contexto de las divisiones puras
# ORBIT 07 M1: la proteccion de venta reciente vence a los diez dias del dato
# mas antiguo que la motivo. La fecha la fija la lectura; jamas la corrida.
VERSION_PROTECCION_VENTA = "proteccion_venta_v1"
DIAS_PROTECCION_VENTA = 10
# ORBIT 07 M5/M6: episodio economico del ultimo cambio de bid. Las ventanas
# son de siete dias completos a cada lado, SIN el dia del cambio. La ventana
# posterior solo cuenta como madura cuando sus siete fechas ya tienen diez
# dias de madurez: D+17 como pronto.
VERSION_ECONOMIA_CAMBIO = "economia_cambio_v1"
# V2: minimo de clics para juzgar (C-H1.1) y "no crecio la venta" antes de
# "la semana previa no vendio" (C-H2). V1 queda para el replay de lo congelado.
VERSION_ECONOMIA_CAMBIO_V2 = "economia_cambio_v2"
VERSIONES_ECONOMIA_CAMBIO = (VERSION_ECONOMIA_CAMBIO, VERSION_ECONOMIA_CAMBIO_V2)
DIAS_ECONOMIA_CAMBIO = 7
DIAS_MADUREZ_ECONOMIA = DIAS_MADUREZ
# Decision del dueno (2026-11-09): el freno dura 14 dias despues de madurar, o
# sea, caduca el dia 31 desde el cambio. Mas viejo deja de gobernar: la
# trayectoria vigente (R14-R17) decide como antes.
DIAS_VIGENCIA_ECONOMIA_CAMBIO = 31
MOTIVO_ECONOMIA_POST_INMADURO = "post_inmaduro"
MOTIVO_ECONOMIA_CAMBIO_EN_VENTANA = "cambio_en_ventana"
MOTIVO_ECONOMIA_COBERTURA_INCOMPLETA = "cobertura_incompleta"
MOTIVO_ECONOMIA_CAMBIO_POSTERIOR = "cambio_posterior"
MOTIVO_ECONOMIA_EVIDENCIA_VENCIDA = "evidencia_vencida"


@dataclass(frozen=True)
class Tramo:
    """Sumas de UN nivel (hoja, ad group o cuenta) en UN tramo de edad de la ventana madura.
    Enteros y Decimal crudos, tal como se midieron. None = desconocido (regla 3): una metrica con
    alguna observacion NULL o negativa en el tramo viaja None y quien la consuma se abstiene.
    Tramo sin filas = ceros MEDIDOS solo si la plataforma tuvo ingesta esos dias (lo decide la
    frontera de lectura, no la politica)."""

    clics: int | None
    pedidos: int | None
    venta: Decimal | None
    gasto: Decimal | None
    impresiones: int | None


@dataclass(frozen=True)
class EvidenciaNivel:
    """Ventana madura D-90..D-10 de un nivel, en dos tramos de edad. Los pesos NO se
    guardan aplicados: se congelan los crudos por tramo y el esquema; `pedidos_pesados` y
    compania derivan (un numero, una fuente). Los pedidos pesados pueden salir en medios: la
    politica los redondea EN CONTRA del movimiento (hacia arriba para recortar, hacia abajo
    para subir) y asi la gamma sigue entera."""

    reciente: Tramo
    antiguo: Tramo
    esquema: str = ESQUEMA_PESOS

    def pedidos_pesados(self) -> Decimal | None:
        if self.reciente.pedidos is None or self.antiguo.pedidos is None:
            return None
        return Decimal(self.reciente.pedidos) + PESO_ANTIGUO * Decimal(self.antiguo.pedidos)

    def clics_pesados(self) -> Decimal | None:
        if self.reciente.clics is None or self.antiguo.clics is None:
            return None
        return Decimal(self.reciente.clics) + PESO_ANTIGUO * Decimal(self.antiguo.clics)

    def venta_pesada(self) -> Decimal | None:
        if self.reciente.venta is None or self.antiguo.venta is None:
            return None
        return self.reciente.venta + PESO_ANTIGUO * self.antiguo.venta

    def gasto_pesado(self) -> Decimal | None:
        if self.reciente.gasto is None or self.antiguo.gasto is None:
            return None
        return self.reciente.gasto + PESO_ANTIGUO * self.antiguo.gasto

    def pedidos_crudos(self) -> int | None:
        """Suma sin pesos de los dos tramos."""
        if self.reciente.pedidos is None or self.antiguo.pedidos is None:
            return None
        return self.reciente.pedidos + self.antiguo.pedidos

    def gasto_crudo(self) -> Decimal | None:
        """Dinero gastado en la ventana, sin pesos."""
        if self.reciente.gasto is None or self.antiguo.gasto is None:
            return None
        return self.reciente.gasto + self.antiguo.gasto

    def clics_crudos(self) -> int | None:
        """Suma sin pesos."""
        if self.reciente.clics is None or self.antiguo.clics is None:
            return None
        return self.reciente.clics + self.antiguo.clics

    def venta_cruda(self) -> Decimal | None:
        """Suma sin pesos."""
        if self.reciente.venta is None or self.antiguo.venta is None:
            return None
        return self.reciente.venta + self.antiguo.venta


OrigenCambio = Literal[
    "motor",
    "regreso_por_desplome",
    "regreso_del_dueno",
    "ajuste_de_campana",
    "temporada",
]

# TEMPORADA 01 B: el motivo de temporada de un cambio de v_cambio_bid.
MotivoTemporada = Literal["entra", "ajusta", "sale"]


@dataclass(frozen=True)
class CambioBid:
    """Un cambio de bid YA aplicado y verificado en Amazon. Sale de la vista `v_cambio_bid`
    (bids del motor confirmados en ciclo live + reversas confirmadas del ledger + ajustes de
    campana de ubicacion o de estrategia confirmados, con origen `ajuste_de_campana` y
    bid_antes == bid_despues: el bid no cambio pero el precio del clic si, asi que R2 espera
    y la escalera de precio no proyecta)."""

    fecha: dt.date  # fecha UTC de la confirmacion
    bid_antes: Decimal
    bid_despues: Decimal
    origen: OrigenCambio

    @property
    def direccion(self) -> Literal[-1, 0, 1]:
        # 0 si bid_despues == bid_antes (ajuste de campana); 1 si sube; -1 si baja
        if self.bid_despues == self.bid_antes:
            return 0
        return 1 if self.bid_despues > self.bid_antes else -1


@dataclass(frozen=True)
class CambioFechado:
    """Un cambio de v_cambio_bid con su instante y su id: el orden de intervenciones
    del mismo dia (TEMPORADA 01 B). `motivo` (TEMPORADA 01 C) es el motivo general
    de la decision del motor (`inputs->>'motivo'`): None en las ramas sin decision
    (regreso del dueno y ajuste de campana) y en los casos congelados antes de C.

    Freeze: los casos congelados antes de C no traen la clave `motivo` en sus
    cambios y la leen como None, de modo que el replay historico rejuega byte a
    byte (ultimo_salto_recuperacion derivaba False para ellos)."""

    confirmado_el: dt.datetime
    decision_id: int | None
    bid_antes: Decimal | None  # None en el regreso del dueno (rama 2 de la vista)
    bid_despues: Decimal
    origen: OrigenCambio
    motivo_temporada: MotivoTemporada | None
    episodio_id: int | None
    motivo: str | None = None  # el motivo general del motor (bloque C)


@dataclass(frozen=True)
class ManoExterna:
    """Una mano de David que no pasa por v_cambio_bid, con fecha: una fila de
    temporada_exclusion con bid_visto (del aplicador o de TX3) o una reactivacion
    manual (la segunda llega con el bloque F)."""

    instante: dt.datetime
    bid_visto: Decimal | None  # None en la reactivacion manual
    origen: Literal["exclusion", "reactivacion_manual"]


@dataclass(frozen=True)
class HistoriaBid:
    """Todos los cambios confirmados de la hoja desde `desde`, con instante.
    `desde` es D-365, o antes si la hoja sigue reducida por un episodio mas viejo
    (un episodio abierto no caduca). La leen solo recuperacion.cadena_bid y la
    temporada; Trayectoria (90 dias) no cambia."""

    desde: dt.date
    cambios: tuple[CambioFechado, ...]  # ascendentes por (confirmado_el, decision_id)
    fines: frozenset[int] = frozenset()  # episodios con fila en temporada_fin_reduccion
    manos_externas: tuple[ManoExterna, ...] = ()  # exclusiones con bid_visto desde `desde`


@dataclass(frozen=True)
class EfectoCambio:
    """Lo que paso alrededor del ULTIMO cambio de la hoja. Se mide contra el calendario de
    ingesta de la plataforma, nunca contra las filas de la propia hoja: una hoja que se queda
    sin impresiones deja de tener filas, y eso aqui cuenta como cero medido (mismo criterio
    que v_entidad_inerte)."""

    dias_post: int  # dias con ingesta de la plataforma posteriores al cambio (hasta ayer)
    impresiones_pre7: int | None  # 7 dias antes del cambio
    clics_pre7: int | None
    impresiones_post: int | None  # primeros min(7, dias_post) dias despues
    dias_post_trafico: int
    clics_post: (
        int | None
    )  # desde el dia siguiente al cambio hasta D-3 (precio medido al bid nuevo)
    gasto_post: Decimal | None
    clics_pre: int | None  # tramo de precio anterior al cambio (hasta 30 dias o el cambio previo)
    gasto_pre: Decimal | None
    vendia: bool | None  # >= 1 pedido en la ventana de cortes de la pausa (CLARIDAD 01 K)

    def razon_trafico(self) -> Decimal | None:
        """(impresiones_post / dias_post_trafico) / (impresiones_pre7 / 7). None sin base
        o sin dias."""
        if (
            self.impresiones_pre7 is None
            or self.impresiones_post is None
            or self.impresiones_pre7 <= 0
            or self.dias_post_trafico <= 0
        ):
            return None
        with localcontext(prec=PRECISION):
            return (Decimal(self.impresiones_post) / Decimal(self.dias_post_trafico)) / (
                Decimal(self.impresiones_pre7) / Decimal(7)
            )


@dataclass(frozen=True)
class VentanaEconomica:
    """Gasto, venta, pedidos y clics de una ventana de siete fechas.

    `desde`/`hasta` son inclusive. Un None por metrica = desconocido (fuente
    incompleta o metrica venenosa); nunca un cero inventado. La ventana se
    congela entera para que el replay no vuelva a leer metricas.
    """

    desde: dt.date
    hasta: dt.date
    gasto: Decimal | None
    venta: Decimal | None
    venta_mismo_sku: Decimal | None
    pedidos: int | None
    clics: int | None

    @property
    def dias(self) -> int:
        return (self.hasta - self.desde).days + 1


EstadoEpisodio = Literal["maduro", "inmaduro", "incomparable"]


@dataclass(frozen=True)
class EpisodioEconomico:
    """Episodio del ultimo cambio de bid confirmado de la hoja (ORBIT 07 M5).

    `pre` y `post` son las ventanas economicas [x-7, x-1] y [x+1, x+7] del
    cambio en `fecha_cambio`; la postal solo se mide completa cuando x+7 ya
    tiene diez dias de madurez (D+17). `estado` separa la comparabilidad
    historica de la aplicabilidad: `aplicable` es False cuando un cambio
    posterior de puja, presupuesto, placement, precio o alcance dejo el
    episodio como historia y no gobierna la repeticion de un cambio nuevo.
    `contribucion_*` solo existe con un equilibrio compatible (None = no se
    inventa una utilidad).
    """

    version: str
    fecha_cambio: dt.date
    moneda: Moneda
    pre: VentanaEconomica
    post: VentanaEconomica | None
    estado: EstadoEpisodio
    motivo: str | None
    aplicable: bool
    motivo_aplicabilidad: str | None
    margen_pct: Decimal | None
    contribucion_pre: Decimal | None
    contribucion_post: Decimal | None


@dataclass(frozen=True)
class Trayectoria:
    """Historia de bids de la hoja en los ultimos 90 dias (ascendente), el efecto del
    ultimo cambio y, desde ORBIT 07 M5, el episodio economico de su ultimo
    cambio de bid. `episodio` None conserva la semantica historica."""

    cambios: tuple[CambioBid, ...]
    efecto: EfectoCambio | None
    episodio: EpisodioEconomico | None = None

    def __post_init__(self) -> None:
        if (self.efecto is None) == bool(self.cambios):
            raise ValueError("efecto es None si y solo si cambios esta vacio")
        if self.episodio is not None and not self.cambios:
            raise ValueError("episodio requiere cambios")

    @property
    def ultimo(self) -> CambioBid | None:
        return self.cambios[-1] if self.cambios else None

    @property
    def piso_aprendido(self) -> Decimal | None:
        """El bid mas alto desde el que hubo que REGRESAR HACIA ARRIBA (por desplome o
        por el dueno): a ese bid la hoja perdio su trafico. Revertir una subida
        (direccion -1) no ensena ningun piso. Derivado de `cambios`; no se guarda en
        ninguna tabla."""
        pisos = [
            c.bid_antes
            for c in self.cambios
            if c.origen in ("regreso_por_desplome", "regreso_del_dueno") and c.direccion == 1
        ]
        return max(pisos) if pisos else None


@dataclass(frozen=True)
class PrecioVentana:
    """Gasto y clics de la hoja en D-33..D-3: el CPC cuando no hubo cambio de bid reciente."""

    gasto: Decimal | None
    clics: int | None


@dataclass(frozen=True)
class EconomiaPlataforma:
    """Lo que el ciclo resuelve UNA vez por plataforma y la politica consume resuelto."""

    moneda: Moneda
    equilibrio_acos_pct: (
        Decimal | None
    )  # margen neto antes de publicidad; None si el target no vino del margen
    gasto_para_concluir: (
        Decimal  # 350 MXN / 36 USD (dueno); la MISMA cifra es el tope de aprendizaje
    )
    confianza_recorte: Decimal  # settings existentes (0.80)
    confianza_subida: Decimal  # settings existentes (0.70)


@dataclass(frozen=True)
class Economia:
    """EconomiaPlataforma mas el target de ESTA hoja (la escalera de goals lo resuelve por hoja)."""

    plataforma: EconomiaPlataforma
    target_acos_pct: Decimal


@dataclass(frozen=True)
class BidVigente:
    valor: Decimal | None  # None: la hoja hereda el bid del ad group (clausulas de auto)
    moneda: str | None
    piso: Decimal
    techo: Decimal


@dataclass(frozen=True)
class LecturaReciente:
    """Pedidos ya vistos despues de la ventana de cortes de la hoja (desde el
    dia siguiente a su fin, nunca antes de D-9, hasta D-1) y la fecha mas
    antigua con pedido.

    `pedidos` None = desconocido (fuente incompleta o metrica venenosa); cero
    solo cuando la ingesta cubre todo el rango. `ancla` es `metric_date` mas
    antigua con orders > 0 en esa misma lectura; un pedido visto no se borra
    por un dia venenoso ni por falta de cobertura.
    """

    pedidos: int | None
    ancla: dt.date | None
    cubierta: bool
    observado_al: dt.datetime | None


@dataclass(frozen=True)
class EpisodioProteccion:
    """Episodio persistido: ancla y vencimiento FIJOS (ancla + 10 dias UTC)."""

    ancla: dt.date
    vence_el: dt.date


@dataclass(frozen=True)
class InsumosProteccion:
    """Insumos congelados de la subpolitica de pausa por venta reciente.

    `ventana_desde`/`ventana_hasta` son la ventana de CORTES del intento: al
    vencer, el ancla ya debe estar dentro de la ventana madura para liberar.
    `episodio` es la fila mas reciente persistida (activa o ya vencida).
    `frena_subidas` congela la regla que impide subir el bid mientras la pausa
    espera por venta reciente; ausente en las decisiones historicas.
    """

    version: str
    evaluado_el: dt.date
    reciente: LecturaReciente
    ventana_desde: dt.date | None
    ventana_hasta: dt.date | None
    episodio: EpisodioProteccion | None
    frena_subidas: bool = False


@dataclass(frozen=True)
class LecturaRecienteEconomica:
    """Ventana reciente `[desde .. hasta]` de una hoja para la pausa economica v2.

    `completa` es un campo EXPLICITO (cobertura de plataforma + veneno de
    metrica), no la propiedad `AgregadoMetricas.completa`; `evaluado_el` deja
    que la funcion pura compruebe que la ventana termina en D-1 (regla 3).
    Montos en `Decimal`; None = desconocido, jamas un cero inventado.
    """

    desde: dt.date
    hasta: dt.date
    evaluado_el: dt.date
    moneda: str
    completa: bool
    cost: Decimal | None
    ad_revenue: Decimal | None
    orders: int | None


@dataclass(frozen=True)
class ResultadoProteccion:
    """Salida pura: aplicar la pausa, esperar o abstenerse por dato desconocido.

    `abre_episodio` es la unica transicion de escritura: el escritor inserta la
    fila (ancla, vence_el) de forma append-only. Cerrar un episodio no escribe:
    se deriva de `vence_el` y de la ventana madura.
    """

    version: str
    accion: Literal["aplicar", "esperar", "indeterminada"]
    motivo: str
    ancla: dt.date | None
    vence_el: dt.date | None
    abre_episodio: bool


@dataclass(frozen=True)
class InsumosRecuperacion:
    """Insumos de la subida rapida R6r (TEMPORADA 01, bloque C). Congelados."""

    version: Literal["recuperacion_v1"]
    memoria_dias: int


@dataclass(frozen=True)
class PresupuestoCampana:
    """Presupuesto diario vigente de la campana de la hoja y cuantos de los ultimos 7 dias
    con ingesta gasto al menos `topada.pct` de el. None por campo = desconocido (no se
    inventa). Congelado (TEMPORADA 01, bloques C/D)."""

    presupuesto_diario: Decimal | None
    dias_medidos: int
    dias_topados: int | None
    topada: temporada.CampanaTopada  # la definicion vigente, congelada


@dataclass(frozen=True)
class TemporadaCaso:
    """Presente sii ads_temporada_<plat> decide este caso (live) o en el caso de
    sombra (TEMPORADA 01 B/D)."""

    estacion: temporada.Estacion
    episodio: temporada.EpisodioVigente | None  # None: no hay episodio abierto
    excluida_en: frozenset[int]  # episodios donde el aplicador o TX3 vieron la mano
    meta_alta_pct: Decimal
    meta_manual: bool  # procedencia goal_campana o goal_plataforma
    frontera: temporada.FronteraHoja


@dataclass(frozen=True)
class InsumosPausa:
    """Lo que PAUSE consume (bid._decide_pause): ventana de cortes y umbrales resueltos."""

    cortes: AgregadoMetricas | None
    umbral_clics: int
    gasto_minimo: Decimal
    expected_clicks: Decimal | None
    politica_economica: str | None
    # ORBIT 07 M1: ausente en las filas historicas y en las pausas con bids
    # apagados anteriores; su ausencia conserva la semantica antigua.
    proteccion: InsumosProteccion | None = None
    # P03: ventana reciente de la pausa economica v2. Ausente = semantica de
    # hoy (v1) y replay historico intacto; `None` en era v2 = no medible.
    reciente_economica: LecturaRecienteEconomica | None = None
    # TEMPORADA 01 D: el exceso minimo de la pausa economica ya multiplicado
    # por el mult del dueno (EXCESO_MINIMO x mult en baja). None = EXCESO_
    # MINIMO[moneda] (semantica de hoy, replay intacto); el freeze lo lleva
    # para que el replay de una pausa de la baja rejuegue con su multiplicador.
    exceso_minimo: Decimal | None = None


@dataclass(frozen=True)
class CasoHoja:
    """TODO lo que decide el bid de una hoja en un ciclo. Es el argumento de `decide` y,
    serializado, es `decision.inputs["caso"]`. Invariante: `decide` no lee nada fuera de
    este valor."""

    plataforma: Plataforma
    hoja_id: int
    ad_group_id: int | None
    bid: BidVigente
    economia: Economia
    propia: EvidenciaNivel | None  # None = hoja sin filas en la ventana madura
    pedidos_inmaduros: int | None  # pedidos ya vistos en D-9..D-1
    grupo: EvidenciaNivel | None  # hojas ACTIVAS del ad group, incluida esta
    cuenta: EvidenciaNivel | None  # hojas ACTIVAS de la plataforma (raiz de la previa)
    precio: PrecioVentana
    trayectoria: Trayectoria
    pausa: InsumosPausa
    ventana_desde: dt.date
    ventana_hasta: dt.date
    observado_al: dt.datetime | None
    # Marcador de replay (ARREGLOS 02 B): 0 = semantica de su era. No llega a
    # las decisiones viejas; `como_json` solo lo escribe si es > 0.
    revision: int = 0
    # TEMPORADA 01 (marcadores congelados, opcionales): ausentes = semantica de
    # hoy. `temporada` presente sii ads_temporada_<plat> decide (o en el caso de
    # sombra del bloque D); `historia` presente si temporada o recuperacion
    # deciden; `recuperacion` (bloque C) y `presupuesto` (bloques C/D) igual.
    temporada: TemporadaCaso | None = None
    historia: HistoriaBid | None = None
    recuperacion: InsumosRecuperacion | None = None
    presupuesto: PresupuestoCampana | None = None

    def como_json(self) -> dict:
        """Freeze. Decimal como string, fechas ISO, None explicito. Unica serializacion del caso."""
        return _a_json(self)

    @classmethod
    def desde_json(cls, datos: dict) -> CasoHoja:
        """Inversa exacta de `como_json`; clave ausente o tipo ajeno levanta ValueError
        (frontera). `revision` es opcional: ausente = 0 = semantica de su era. Las claves
        de TEMPORADA 01 son opcionales igual: ausentes = semantica de hoy."""
        return _desde_dataclass(datos, cls, "caso")


# ---------------------------------------------------------------------------
# Codec JSON dirigido por tipos (inversa exacta, fail-closed en claves y tipos)
# ---------------------------------------------------------------------------

_OPCIONALES_POR_TIPO: dict[type, frozenset[str]] = {
    CasoHoja: frozenset({"revision", "temporada", "historia", "recuperacion", "presupuesto"}),
    Trayectoria: frozenset({"episodio"}),
    InsumosPausa: frozenset({"proteccion", "reciente_economica", "exceso_minimo"}),
    InsumosProteccion: frozenset({"frena_subidas"}),
    CambioFechado: frozenset({"motivo"}),
}

# Claves opcionales de CasoHoja que `como_json` omite en su valor por defecto y
# que, si aparecen explicitamente en JSON, no admiten `null`.
_CASO_OMITIR_SI_DEFAULT: frozenset[str] = _OPCIONALES_POR_TIPO[CasoHoja]

# Restricciones de vocabulario cerrado sobre campos anotados como `str`.
_LITERALES_CAMPO: dict[tuple[type, str], tuple[str, ...]] = {
    (EvidenciaNivel, "esquema"): (ESQUEMA_PESOS,),
    (EpisodioEconomico, "version"): VERSIONES_ECONOMIA_CAMBIO,
    (InsumosProteccion, "version"): (VERSION_PROTECCION_VENTA,),
}


# Claves obligatorias del freeze de CasoHoja (derivadas de los campos).
_CLAVES_CASO: frozenset[str] = frozenset(f.name for f in fields(CasoHoja)) - _CASO_OMITIR_SI_DEFAULT


def _proteccion_json(proteccion: InsumosProteccion | None) -> dict | None:
    """Freeze de `InsumosProteccion` (reutilizado por `app.cycle_serializacion`)."""
    return _a_json(proteccion)


@functools.lru_cache(maxsize=64)
def _esquema_dataclass(
    cls: type,
) -> tuple[frozenset[str], frozenset[str], tuple[tuple[str, Any], ...]]:
    ns = vars(sys.modules[cls.__module__])
    hints = get_type_hints(cls, globalns=ns)
    opcionales = _OPCIONALES_POR_TIPO.get(cls, frozenset())
    campos = tuple((f.name, hints[f.name]) for f in fields(cls))
    obligatorias = frozenset(nombre for nombre, _ in campos) - opcionales
    return obligatorias, opcionales, campos


def _a_json(valor: Any) -> Any:
    if valor is None or isinstance(valor, bool | int | str):
        return valor
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dt.datetime | dt.date):
        return valor.isoformat()
    if isinstance(valor, frozenset):
        return [_a_json(x) for x in sorted(valor)]
    if isinstance(valor, tuple | list):
        return [_a_json(x) for x in valor]
    if is_dataclass(valor):
        cls = type(valor)
        salida: dict[str, Any] = {}
        for f in fields(valor):
            dato = getattr(valor, f.name)
            if cls is CasoHoja and f.name in _CASO_OMITIR_SI_DEFAULT:
                if f.name == "revision":
                    if dato <= 0:
                        continue
                elif dato is None:
                    continue
            elif cls is InsumosPausa and f.name == "exceso_minimo" and dato is None:
                continue
            salida[f.name] = _a_json(dato)
        return salida
    raise TypeError(f"tipo no serializable en CasoHoja: {type(valor).__name__}")


def _entero(valor: Any, nombre: str) -> int:
    if type(valor) is not int:
        raise ValueError(f"{nombre}: entero esperado, llego {valor!r}")
    return valor


def _decimal(valor: Any, nombre: str) -> Decimal:
    if type(valor) is not str:
        raise ValueError(f"{nombre}: string decimal esperado, llego {valor!r}")
    try:
        numero = Decimal(valor)
    except Exception as exc:
        raise ValueError(f"{nombre}: decimal invalido {valor!r}") from exc
    if not numero.is_finite():
        raise ValueError(f"{nombre}: decimal no finito {valor!r}")
    return numero


def _texto(valor: Any, nombre: str) -> str:
    if type(valor) is not str:
        raise ValueError(f"{nombre}: string esperado, llego {valor!r}")
    return valor


def _booleano(valor: Any, nombre: str) -> bool:
    if type(valor) is not bool:
        raise ValueError(f"{nombre}: booleano esperado, llego {valor!r}")
    return valor


def _literal(valor: Any, opciones: tuple, nombre: str) -> Any:
    if not any(type(valor) is type(op) and valor == op for op in opciones):
        raise ValueError(f"{nombre}: fuera de {opciones}, llego {valor!r}")
    return valor


def _desde_tipo(valor: Any, tipo: Any, nombre: str, *, permitir_none: bool = True) -> Any:
    origin = get_origin(tipo)
    if origin in (Union, types.UnionType):
        ramas = get_args(tipo)
        es_opcional = type(None) in ramas
        if valor is None:
            if es_opcional and permitir_none:
                return None
            raise ValueError(f"{nombre}: valor no puede ser null")
        no_nulas = [r for r in ramas if r is not type(None)]
        if len(no_nulas) == 1:
            return _desde_tipo(valor, no_nulas[0], nombre)
        raise ValueError(f"{nombre}: union no soportada {tipo!r}")

    if origin is Literal:
        return _literal(valor, get_args(tipo), nombre)
    if origin is tuple:
        if not isinstance(valor, list):
            raise ValueError(f"{nombre}: lista esperada, llego {valor!r}")
        elem_tipo = get_args(tipo)[0]
        return tuple(_desde_tipo(x, elem_tipo, f"{nombre}[]") for x in valor)
    if origin is frozenset:
        if not isinstance(valor, list):
            raise ValueError(f"{nombre}: lista esperada, llego {valor!r}")
        elem_tipo = get_args(tipo)[0]
        return frozenset(_desde_tipo(x, elem_tipo, f"{nombre}[]") for x in valor)

    if tipo is int:
        return _entero(valor, nombre)
    if tipo is bool:
        return _booleano(valor, nombre)
    if tipo is str:
        return _texto(valor, nombre)
    if tipo is Decimal:
        return _decimal(valor, nombre)
    if tipo is dt.datetime:
        return dt.datetime.fromisoformat(_texto(valor, nombre))
    if tipo is dt.date:
        return dt.date.fromisoformat(_texto(valor, nombre))
    if isinstance(tipo, type) and is_dataclass(tipo):
        return _desde_dataclass(valor, tipo, nombre)
    raise ValueError(f"{nombre}: tipo no soportado {tipo!r}")


def _desde_dataclass(datos: Any, cls: type, nombre: str) -> Any:
    obligatorias, opcionales, campos = _esquema_dataclass(cls)
    if not isinstance(datos, dict) or not (obligatorias <= set(datos) <= obligatorias | opcionales):
        raise ValueError(f"{nombre}: claves distintas a {sorted(obligatorias | opcionales)}")
    kwargs: dict[str, Any] = {}
    for campo, tipo in campos:
        if campo not in datos:
            continue
        etiqueta = f"{nombre}.{campo}"
        raw = datos[campo]
        opciones = _LITERALES_CAMPO.get((cls, campo))
        if opciones is not None:
            kwargs[campo] = _literal(raw, opciones, etiqueta)
        else:
            permitir_none = not (cls is CasoHoja and campo in _CASO_OMITIR_SI_DEFAULT)
            kwargs[campo] = _desde_tipo(raw, tipo, etiqueta, permitir_none=permitir_none)
    return cls(**kwargs)
