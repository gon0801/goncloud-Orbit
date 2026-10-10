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
        """Suma sin pesos de los dos tramos (la usan 'tiene pedidos', el minimo de 3
        del grupo y la previa)."""
        if self.reciente.pedidos is None or self.antiguo.pedidos is None:
            return None
        return self.reciente.pedidos + self.antiguo.pedidos

    def gasto_crudo(self) -> Decimal | None:
        """Dinero gastado en la ventana, sin pesos: el dinero gastado es dinero gastado."""
        if self.reciente.gasto is None or self.antiguo.gasto is None:
            return None
        return self.reciente.gasto + self.antiguo.gasto

    def clics_crudos(self) -> int | None:
        """Suma sin pesos (la usan la escalera de precio paso 4 y la previa entera)."""
        if self.reciente.clics is None or self.antiguo.clics is None:
            return None
        return self.reciente.clics + self.antiguo.clics

    def venta_cruda(self) -> Decimal | None:
        """Suma sin pesos (la usa la previa entera)."""
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
    vendia: bool | None  # >= 1 pedido en los 90 dias previos al cambio

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
class Trayectoria:
    """Historia de bids de la hoja en los ultimos 90 dias (ascendente) y el efecto del
    ultimo cambio. Sustituye a tres piezas de hoy: cooldown de 7 dias, D.2 y cpc_vigente."""

    cambios: tuple[CambioBid, ...]
    efecto: EfectoCambio | None  # None si y solo si `cambios` esta vacio

    def __post_init__(self) -> None:
        if (self.efecto is None) == bool(self.cambios):
            raise ValueError("efecto es None si y solo si cambios esta vacio")

    @property
    def ultimo(self) -> CambioBid | None:
        return self.cambios[-1] if self.cambios else None

    @property
    def piso_aprendido(self) -> Decimal | None:
        """El bid mas alto DESDE el que hubo que regresar (por desplome o por el dueno):
        a ese bid la hoja perdio su trafico. Derivado de `cambios`; no se guarda en
        ninguna tabla."""
        pisos = [
            c.bid_antes
            for c in self.cambios
            if c.origen in ("regreso_por_desplome", "regreso_del_dueno")
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
class InsumosPausa:
    """Lo que PAUSE consume hoy, sin cambios (bid._decide_pause): ventana de cortes y
    umbrales resueltos."""

    cortes: Any  # windows.AgregadoMetricas | None
    umbral_clics: int
    gasto_minimo: Decimal
    expected_clicks: Decimal | None
    politica_economica: str | None


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
    d = _objeto(datos, frozenset({"cambios", "efecto"}), "trayectoria")
    if not isinstance(d["cambios"], list):
        raise ValueError(f"trayectoria.cambios: lista esperada, llego {d['cambios']!r}")
    return Trayectoria(
        cambios=tuple(_cambio_desde(c) for c in d["cambios"]),
        efecto=_efecto_desde(d["efecto"]),
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
    d = _objeto(
        datos,
        frozenset(
            {
                "cortes",
                "umbral_clics",
                "gasto_minimo",
                "expected_clicks",
                "politica_economica",
            }
        ),
        "pausa",
    )
    return InsumosPausa(
        cortes=_cortes_desde(d["cortes"]),
        umbral_clics=_entero(d["umbral_clics"], "umbral_clics"),
        gasto_minimo=_decimal(d["gasto_minimo"], "gasto_minimo"),
        expected_clicks=_opcional(d["expected_clicks"], "expected_clicks", _decimal),
        politica_economica=_opcional(d["politica_economica"], "politica_economica", _texto),
    )
