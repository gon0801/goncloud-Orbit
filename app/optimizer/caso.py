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
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Literal

from app.optimizer.windows import AgregadoMetricas

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]

POLITICA_BID = "niveles_v3"  # se congela en inputs.politica; el replay despacha por esta clave
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


OrigenCambio = Literal["motor", "regreso_por_desplome", "regreso_del_dueno", "ajuste_de_campana"]


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
    def direccion(self) -> Literal[-1, 1]:
        return 1 if self.bid_despues >= self.bid_antes else -1


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

    def como_json(self) -> dict:
        """Freeze. Decimal como string, fechas ISO, None explicito. Unica serializacion del caso."""
        return {
            "plataforma": self.plataforma,
            "hoja_id": self.hoja_id,
            "ad_group_id": self.ad_group_id,
            "bid": _bid_json(self.bid),
            "economia": _economia_json(self.economia),
            "propia": _evidencia_json(self.propia),
            "pedidos_inmaduros": self.pedidos_inmaduros,
            "grupo": _evidencia_json(self.grupo),
            "cuenta": _evidencia_json(self.cuenta),
            "precio": _precio_json(self.precio),
            "trayectoria": _trayectoria_json(self.trayectoria),
            "pausa": _pausa_json(self.pausa),
            "ventana_desde": self.ventana_desde.isoformat(),
            "ventana_hasta": self.ventana_hasta.isoformat(),
            "observado_al": self.observado_al.isoformat() if self.observado_al else None,
        }

    @classmethod
    def desde_json(cls, datos: dict) -> CasoHoja:
        """Inversa exacta de `como_json`; clave ausente o tipo ajeno levanta ValueError
        (frontera)."""
        d = _objeto(datos, _CLAVES_CASO, "caso")
        return cls(
            plataforma=_literal(d["plataforma"], ("amazon_mx", "amazon_us"), "plataforma"),
            hoja_id=_entero(d["hoja_id"], "hoja_id"),
            ad_group_id=_opcional(d["ad_group_id"], "ad_group_id", _entero),
            bid=_bid_desde(d["bid"]),
            economia=_economia_desde(d["economia"]),
            propia=_evidencia_desde(d["propia"], "propia"),
            pedidos_inmaduros=_opcional(d["pedidos_inmaduros"], "pedidos_inmaduros", _entero),
            grupo=_evidencia_desde(d["grupo"], "grupo"),
            cuenta=_evidencia_desde(d["cuenta"], "cuenta"),
            precio=_precio_desde(d["precio"]),
            trayectoria=_trayectoria_desde(d["trayectoria"]),
            pausa=_pausa_desde(d["pausa"]),
            ventana_desde=_fecha(d["ventana_desde"], "ventana_desde"),
            ventana_hasta=_fecha(d["ventana_hasta"], "ventana_hasta"),
            observado_al=_opcional(d["observado_al"], "observado_al", _momento),
        )


def _dinero(valor: Decimal | None) -> str | None:
    return str(valor) if valor is not None else None


def _tramo_json(tramo: Tramo) -> dict:
    return {
        "clics": tramo.clics,
        "pedidos": tramo.pedidos,
        "venta": _dinero(tramo.venta),
        "gasto": _dinero(tramo.gasto),
        "impresiones": tramo.impresiones,
    }


def _evidencia_json(ev: EvidenciaNivel | None) -> dict | None:
    if ev is None:
        return None
    return {
        "reciente": _tramo_json(ev.reciente),
        "antiguo": _tramo_json(ev.antiguo),
        "esquema": ev.esquema,
    }


def _bid_json(bid: BidVigente) -> dict:
    return {
        "valor": _dinero(bid.valor),
        "moneda": bid.moneda,
        "piso": str(bid.piso),
        "techo": str(bid.techo),
    }


def _economia_json(economia: Economia) -> dict:
    plat = economia.plataforma
    return {
        "plataforma": {
            "moneda": plat.moneda,
            "equilibrio_acos_pct": _dinero(plat.equilibrio_acos_pct),
            "gasto_para_concluir": str(plat.gasto_para_concluir),
            "confianza_recorte": str(plat.confianza_recorte),
            "confianza_subida": str(plat.confianza_subida),
        },
        "target_acos_pct": str(economia.target_acos_pct),
    }


def _precio_json(precio: PrecioVentana) -> dict:
    return {"gasto": _dinero(precio.gasto), "clics": precio.clics}


def _trayectoria_json(tray: Trayectoria) -> dict:
    efecto = tray.efecto
    return {
        "cambios": [
            {
                "fecha": c.fecha.isoformat(),
                "bid_antes": str(c.bid_antes),
                "bid_despues": str(c.bid_despues),
                "origen": c.origen,
            }
            for c in tray.cambios
        ],
        "efecto": {
            "dias_post": efecto.dias_post,
            "impresiones_pre7": efecto.impresiones_pre7,
            "clics_pre7": efecto.clics_pre7,
            "impresiones_post": efecto.impresiones_post,
            "dias_post_trafico": efecto.dias_post_trafico,
            "clics_post": efecto.clics_post,
            "gasto_post": _dinero(efecto.gasto_post),
            "clics_pre": efecto.clics_pre,
            "gasto_pre": _dinero(efecto.gasto_pre),
            "vendia": efecto.vendia,
        }
        if efecto is not None
        else None,
        "episodio": _episodio_economico_json(tray.episodio),
    }


def _ventana_economica_json(ventana: VentanaEconomica) -> dict:
    return {
        "desde": ventana.desde.isoformat(),
        "hasta": ventana.hasta.isoformat(),
        "gasto": _dinero(ventana.gasto),
        "venta": _dinero(ventana.venta),
        "venta_mismo_sku": _dinero(ventana.venta_mismo_sku),
        "pedidos": ventana.pedidos,
        "clics": ventana.clics,
    }


def _episodio_economico_json(episodio: EpisodioEconomico | None) -> dict | None:
    if episodio is None:
        return None
    return {
        "version": episodio.version,
        "fecha_cambio": episodio.fecha_cambio.isoformat(),
        "moneda": episodio.moneda,
        "pre": _ventana_economica_json(episodio.pre),
        "post": _ventana_economica_json(episodio.post) if episodio.post is not None else None,
        "estado": episodio.estado,
        "motivo": episodio.motivo,
        "aplicable": episodio.aplicable,
        "motivo_aplicabilidad": episodio.motivo_aplicabilidad,
        "margen_pct": _dinero(episodio.margen_pct),
        "contribucion_pre": _dinero(episodio.contribucion_pre),
        "contribucion_post": _dinero(episodio.contribucion_post),
    }


def _cortes_json(cortes: AgregadoMetricas | None) -> dict | None:
    if cortes is None:
        return None
    return {
        "window_start": cortes.window_start.isoformat(),
        "window_end": cortes.window_end.isoformat(),
        "fechas": [f.isoformat() for f in cortes.fechas],
        "metric_currency": cortes.metric_currency,
        "cost": _dinero(cortes.cost),
        "ad_revenue": _dinero(cortes.ad_revenue),
        "revenue_same_sku": _dinero(cortes.revenue_same_sku),
        "impressions": cortes.impressions,
        "clicks": cortes.clicks,
        "orders": cortes.orders,
        "observed_at_max": cortes.observed_at_max.isoformat() if cortes.observed_at_max else None,
    }


def _pausa_json(pausa: InsumosPausa) -> dict:
    return {
        "cortes": _cortes_json(pausa.cortes),
        "umbral_clics": pausa.umbral_clics,
        "gasto_minimo": str(pausa.gasto_minimo),
        "expected_clicks": _dinero(pausa.expected_clicks),
        "politica_economica": pausa.politica_economica,
        "proteccion": _proteccion_json(pausa.proteccion),
    }


def _lectura_reciente_json(lectura: LecturaReciente) -> dict:
    return {
        "pedidos": lectura.pedidos,
        "ancla": lectura.ancla.isoformat() if lectura.ancla is not None else None,
        "cubierta": lectura.cubierta,
        "observado_al": lectura.observado_al.isoformat() if lectura.observado_al else None,
    }


def _episodio_json(episodio: EpisodioProteccion | None) -> dict | None:
    if episodio is None:
        return None
    return {"ancla": episodio.ancla.isoformat(), "vence_el": episodio.vence_el.isoformat()}


def _proteccion_json(proteccion: InsumosProteccion | None) -> dict | None:
    if proteccion is None:
        return None
    return {
        "version": proteccion.version,
        "evaluado_el": proteccion.evaluado_el.isoformat(),
        "reciente": _lectura_reciente_json(proteccion.reciente),
        "ventana_desde": proteccion.ventana_desde.isoformat()
        if proteccion.ventana_desde is not None
        else None,
        "ventana_hasta": proteccion.ventana_hasta.isoformat()
        if proteccion.ventana_hasta is not None
        else None,
        "episodio": _episodio_json(proteccion.episodio),
        "frena_subidas": proteccion.frena_subidas,
    }


_CLAVES_CASO = frozenset(
    {
        "plataforma",
        "hoja_id",
        "ad_group_id",
        "bid",
        "economia",
        "propia",
        "pedidos_inmaduros",
        "grupo",
        "cuenta",
        "precio",
        "trayectoria",
        "pausa",
        "ventana_desde",
        "ventana_hasta",
        "observado_al",
    }
)

_CLAVES_PROTECCION = frozenset(
    {
        "version",
        "evaluado_el",
        "reciente",
        "ventana_desde",
        "ventana_hasta",
        "episodio",
    }
)


def _objeto(datos: Any, claves: frozenset, nombre: str) -> dict:
    if not isinstance(datos, dict) or set(datos) != claves:
        raise ValueError(f"{nombre}: claves distintas a {sorted(claves)}")
    return datos


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
    if valor not in opciones:
        raise ValueError(f"{nombre}: fuera de {opciones}, llego {valor!r}")
    return valor


def _fecha(valor: Any, nombre: str) -> dt.date:
    _texto(valor, nombre)
    return dt.date.fromisoformat(valor)


def _momento(valor: Any, nombre: str) -> dt.datetime:
    _texto(valor, nombre)
    return dt.datetime.fromisoformat(valor)


def _opcional(valor: Any, nombre: str, lector) -> Any:
    if valor is None:
        return None
    return lector(valor, nombre)


def _tramo_desde(datos: Any) -> Tramo:
    d = _objeto(datos, frozenset({"clics", "pedidos", "venta", "gasto", "impresiones"}), "tramo")
    return Tramo(
        clics=_opcional(d["clics"], "clics", _entero),
        pedidos=_opcional(d["pedidos"], "pedidos", _entero),
        venta=_opcional(d["venta"], "venta", _decimal),
        gasto=_opcional(d["gasto"], "gasto", _decimal),
        impresiones=_opcional(d["impresiones"], "impresiones", _entero),
    )


def _evidencia_desde(datos: Any, nombre: str) -> EvidenciaNivel | None:
    if datos is None:
        return None
    d = _objeto(datos, frozenset({"reciente", "antiguo", "esquema"}), nombre)
    return EvidenciaNivel(
        reciente=_tramo_desde(d["reciente"]),
        antiguo=_tramo_desde(d["antiguo"]),
        esquema=_literal(d["esquema"], (ESQUEMA_PESOS,), f"{nombre}.esquema"),
    )


def _bid_desde(datos: Any) -> BidVigente:
    d = _objeto(datos, frozenset({"valor", "moneda", "piso", "techo"}), "bid")
    return BidVigente(
        valor=_opcional(d["valor"], "bid.valor", _decimal),
        moneda=_opcional(d["moneda"], "bid.moneda", _texto),
        piso=_decimal(d["piso"], "bid.piso"),
        techo=_decimal(d["techo"], "bid.techo"),
    )


def _economia_desde(datos: Any) -> Economia:
    d = _objeto(datos, frozenset({"plataforma", "target_acos_pct"}), "economia")
    p = _objeto(
        d["plataforma"],
        frozenset(
            {
                "moneda",
                "equilibrio_acos_pct",
                "gasto_para_concluir",
                "confianza_recorte",
                "confianza_subida",
            }
        ),
        "economia.plataforma",
    )
    return Economia(
        plataforma=EconomiaPlataforma(
            moneda=_literal(p["moneda"], ("MXN", "USD"), "moneda"),
            equilibrio_acos_pct=_opcional(
                p["equilibrio_acos_pct"], "equilibrio_acos_pct", _decimal
            ),
            gasto_para_concluir=_decimal(p["gasto_para_concluir"], "gasto_para_concluir"),
            confianza_recorte=_decimal(p["confianza_recorte"], "confianza_recorte"),
            confianza_subida=_decimal(p["confianza_subida"], "confianza_subida"),
        ),
        target_acos_pct=_decimal(d["target_acos_pct"], "target_acos_pct"),
    )


def _precio_desde(datos: Any) -> PrecioVentana:
    d = _objeto(datos, frozenset({"gasto", "clics"}), "precio")
    return PrecioVentana(
        gasto=_opcional(d["gasto"], "precio.gasto", _decimal),
        clics=_opcional(d["clics"], "precio.clics", _entero),
    )


def _cambio_desde(datos: Any) -> CambioBid:
    d = _objeto(datos, frozenset({"fecha", "bid_antes", "bid_despues", "origen"}), "cambio")
    return CambioBid(
        fecha=_fecha(d["fecha"], "cambio.fecha"),
        bid_antes=_decimal(d["bid_antes"], "cambio.bid_antes"),
        bid_despues=_decimal(d["bid_despues"], "cambio.bid_despues"),
        origen=_literal(
            d["origen"],
            ("motor", "regreso_por_desplome", "regreso_del_dueno", "ajuste_de_campana"),
            "cambio.origen",
        ),
    )


def _efecto_desde(datos: Any) -> EfectoCambio | None:
    if datos is None:
        return None
    d = _objeto(
        datos,
        frozenset(
            {
                "dias_post",
                "impresiones_pre7",
                "clics_pre7",
                "impresiones_post",
                "dias_post_trafico",
                "clics_post",
                "gasto_post",
                "clics_pre",
                "gasto_pre",
                "vendia",
            }
        ),
        "efecto",
    )
    return EfectoCambio(
        dias_post=_entero(d["dias_post"], "dias_post"),
        impresiones_pre7=_opcional(d["impresiones_pre7"], "impresiones_pre7", _entero),
        clics_pre7=_opcional(d["clics_pre7"], "clics_pre7", _entero),
        impresiones_post=_opcional(d["impresiones_post"], "impresiones_post", _entero),
        dias_post_trafico=_entero(d["dias_post_trafico"], "dias_post_trafico"),
        clics_post=_opcional(d["clics_post"], "clics_post", _entero),
        gasto_post=_opcional(d["gasto_post"], "gasto_post", _decimal),
        clics_pre=_opcional(d["clics_pre"], "clics_pre", _entero),
        gasto_pre=_opcional(d["gasto_pre"], "gasto_pre", _decimal),
        vendia=_opcional(d["vendia"], "vendia", _booleano),
    )


def _trayectoria_desde(datos: Any) -> Trayectoria:
    d = _objeto_versionado(
        datos,
        _CLAVES_TRAYECTORIA,
        _CLAVES_TRAYECTORIA | {"episodio"},
        "trayectoria",
    )
    if not isinstance(d["cambios"], list):
        raise ValueError(f"trayectoria.cambios: lista esperada, llego {d['cambios']!r}")
    return Trayectoria(
        cambios=tuple(_cambio_desde(c) for c in d["cambios"]),
        efecto=_efecto_desde(d["efecto"]),
        episodio=_episodio_economico_desde(d.get("episodio")),
    )


_CLAVES_TRAYECTORIA = frozenset({"cambios", "efecto"})


def _ventana_economica_desde(datos: Any, nombre: str) -> VentanaEconomica:
    d = _objeto(
        datos,
        frozenset({"desde", "hasta", "gasto", "venta", "venta_mismo_sku", "pedidos", "clics"}),
        nombre,
    )
    return VentanaEconomica(
        desde=_fecha(d["desde"], f"{nombre}.desde"),
        hasta=_fecha(d["hasta"], f"{nombre}.hasta"),
        gasto=_opcional(d["gasto"], f"{nombre}.gasto", _decimal),
        venta=_opcional(d["venta"], f"{nombre}.venta", _decimal),
        venta_mismo_sku=_opcional(d["venta_mismo_sku"], f"{nombre}.venta_mismo_sku", _decimal),
        pedidos=_opcional(d["pedidos"], f"{nombre}.pedidos", _entero),
        clics=_opcional(d["clics"], f"{nombre}.clics", _entero),
    )


def _episodio_economico_desde(datos: Any) -> EpisodioEconomico | None:
    if datos is None:
        return None
    d = _objeto(
        datos,
        frozenset(
            {
                "version",
                "fecha_cambio",
                "moneda",
                "pre",
                "post",
                "estado",
                "motivo",
                "aplicable",
                "motivo_aplicabilidad",
                "margen_pct",
                "contribucion_pre",
                "contribucion_post",
            }
        ),
        "trayectoria.episodio",
    )
    return EpisodioEconomico(
        version=_literal(d["version"], (VERSION_ECONOMIA_CAMBIO,), "trayectoria.episodio.version"),
        fecha_cambio=_fecha(d["fecha_cambio"], "trayectoria.episodio.fecha_cambio"),
        moneda=_literal(d["moneda"], ("MXN", "USD"), "trayectoria.episodio.moneda"),
        pre=_ventana_economica_desde(d["pre"], "trayectoria.episodio.pre"),
        post=(
            _ventana_economica_desde(d["post"], "trayectoria.episodio.post")
            if d["post"] is not None
            else None
        ),
        estado=_literal(
            d["estado"],
            ("maduro", "inmaduro", "incomparable"),
            "trayectoria.episodio.estado",
        ),
        motivo=_opcional(d["motivo"], "trayectoria.episodio.motivo", _texto),
        aplicable=_booleano(d["aplicable"], "trayectoria.episodio.aplicable"),
        motivo_aplicabilidad=_opcional(
            d["motivo_aplicabilidad"], "trayectoria.episodio.motivo_aplicabilidad", _texto
        ),
        margen_pct=_opcional(d["margen_pct"], "trayectoria.episodio.margen_pct", _decimal),
        contribucion_pre=_opcional(
            d["contribucion_pre"], "trayectoria.episodio.contribucion_pre", _decimal
        ),
        contribucion_post=_opcional(
            d["contribucion_post"], "trayectoria.episodio.contribucion_post", _decimal
        ),
    )


def _cortes_desde(datos: Any) -> AgregadoMetricas | None:
    if datos is None:
        return None
    d = _objeto(
        datos,
        frozenset(
            {
                "window_start",
                "window_end",
                "fechas",
                "metric_currency",
                "cost",
                "ad_revenue",
                "revenue_same_sku",
                "impressions",
                "clicks",
                "orders",
                "observed_at_max",
            }
        ),
        "cortes",
    )
    if not isinstance(d["fechas"], list):
        raise ValueError("cortes.fechas: lista esperada")
    return AgregadoMetricas(
        window_start=_fecha(d["window_start"], "window_start"),
        window_end=_fecha(d["window_end"], "window_end"),
        fechas=tuple(_fecha(f, "cortes.fechas[]") for f in d["fechas"]),
        metric_currency=_opcional(d["metric_currency"], "metric_currency", _texto),
        cost=_opcional(d["cost"], "cost", _decimal),
        ad_revenue=_opcional(d["ad_revenue"], "ad_revenue", _decimal),
        revenue_same_sku=_opcional(d["revenue_same_sku"], "revenue_same_sku", _decimal),
        impressions=_opcional(d["impressions"], "impressions", _entero),
        clicks=_opcional(d["clicks"], "clicks", _entero),
        orders=_opcional(d["orders"], "orders", _entero),
        observed_at_max=_opcional(d["observed_at_max"], "observed_at_max", _momento),
    )


def _pausa_desde(datos: Any) -> InsumosPausa:
    d = _objeto_versionado(
        datos,
        _CLAVES_PAUSA,
        _CLAVES_PAUSA | {"proteccion"},
        "pausa",
    )
    return InsumosPausa(
        cortes=_cortes_desde(d["cortes"]),
        umbral_clics=_entero(d["umbral_clics"], "umbral_clics"),
        gasto_minimo=_decimal(d["gasto_minimo"], "gasto_minimo"),
        expected_clicks=_opcional(d["expected_clicks"], "expected_clicks", _decimal),
        politica_economica=_opcional(d["politica_economica"], "politica_economica", _texto),
        proteccion=_proteccion_desde(d.get("proteccion")),
    )


_CLAVES_PAUSA = frozenset(
    {
        "cortes",
        "umbral_clics",
        "gasto_minimo",
        "expected_clicks",
        "politica_economica",
    }
)


def _objeto_versionado(datos: Any, viejo: frozenset, nuevo: frozenset, nombre: str) -> dict:
    """`_objeto` que acepta la forma historica o la ampliada (M1).

    Las decisiones anteriores a la version nueva no llevan el marcador; su
    ausencia conserva la semantica de su era. Una clave ajena sigue siendo
    error.
    """
    if not isinstance(datos, dict) or set(datos) not in (viejo, nuevo):
        raise ValueError(f"{nombre}: claves distintas a {sorted(nuevo)}")
    return datos


def _lectura_reciente_desde(datos: Any, nombre: str) -> LecturaReciente:
    d = _objeto(
        datos,
        frozenset({"pedidos", "ancla", "cubierta", "observado_al"}),
        f"{nombre}.reciente",
    )
    return LecturaReciente(
        pedidos=_opcional(d["pedidos"], f"{nombre}.pedidos", _entero),
        ancla=_opcional(d["ancla"], f"{nombre}.ancla", _fecha),
        cubierta=_booleano(d["cubierta"], f"{nombre}.cubierta"),
        observado_al=_opcional(d["observado_al"], f"{nombre}.observado_al", _momento),
    )


def _episodio_desde(datos: Any, nombre: str) -> EpisodioProteccion | None:
    if datos is None:
        return None
    d = _objeto(datos, frozenset({"ancla", "vence_el"}), f"{nombre}.episodio")
    return EpisodioProteccion(
        ancla=_fecha(d["ancla"], f"{nombre}.ancla"),
        vence_el=_fecha(d["vence_el"], f"{nombre}.vence_el"),
    )


def _proteccion_desde(datos: Any) -> InsumosProteccion | None:
    if datos is None:
        return None
    d = _objeto_versionado(
        datos,
        _CLAVES_PROTECCION,
        _CLAVES_PROTECCION | {"frena_subidas"},
        "proteccion",
    )
    return InsumosProteccion(
        version=_literal(d["version"], (VERSION_PROTECCION_VENTA,), "proteccion.version"),
        evaluado_el=_fecha(d["evaluado_el"], "proteccion.evaluado_el"),
        reciente=_lectura_reciente_desde(d["reciente"], "proteccion"),
        ventana_desde=_opcional(d["ventana_desde"], "proteccion.ventana_desde", _fecha),
        ventana_hasta=_opcional(d["ventana_hasta"], "proteccion.ventana_hasta", _fecha),
        episodio=_episodio_desde(d["episodio"], "proteccion"),
        frena_subidas=_booleano(d["frena_subidas"], "proteccion.frena_subidas")
        if "frena_subidas" in d
        else False,
    )
