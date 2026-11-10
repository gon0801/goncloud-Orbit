"""Presentacion de /campanas. No calcula metricas: clasifica y suma filas.

Las filas ya vienen de `dash.campanas` y de `filtra_y_ordena_campanas`.
Aqui solo se decide el estado visual, las tarjetas por plataforma y la
vista rapida. Una suma con un None queda en None (no se publica un parcial).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.dashboard_resumen import acos_texto
from app.etiquetas import etiqueta as etiqueta_de

VISTAS_RAPIDAS = frozenset({"todas", "sobre", "gasto", "en", "inactiva"})

_PESTANAS = (
    ("todas", "Todas"),
    ("sobre", "Sobre target"),
    ("gasto", "Gasto sin venta"),
    ("en", "En target"),
    ("inactiva", "Sin actividad"),
)
_BARRA = (
    ("en", "En target"),
    ("sobre", "Sobre target"),
    ("gasto", "Gasto sin venta"),
    ("sint", "Sin target"),
    ("inactiva", "Sin actividad"),
)
_ORDEN_PLATAFORMA = ("amazon_mx", "amazon_us")


def _num(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _suma(valores: list[Decimal | None]) -> Decimal | None:
    if not valores or any(valor is None for valor in valores):
        return None
    return sum(valores, Decimal(0))


def _texto_dinero(total: Decimal | None) -> str:
    if total is None:
        return "—"
    return f"{total:,.2f}"


def _texto_clicks(total: Decimal | None) -> str:
    if total is None:
        return "—"
    if total == total.to_integral_value():
        return f"{int(total):,}"
    return f"{total:,.2f}"


@dataclass(frozen=True)
class EstadoFila:
    clave: str
    etiqueta: str


def _target_valor(item: dict) -> str | None:
    target = item.get("target_efectivo")
    if not isinstance(target, dict):
        return None
    valor = target.get("valor")
    if valor is None:
        return None
    return str(valor)


def estado_de(item: dict) -> EstadoFila | None:
    """Orden del diseño: inactiva, gasto sin venta, sin target, sobre, en.

    Un rango o «mixto» no es un target unico: no se compara y no se llama
    «sin target». Cost o revenue desconocidos no se tratan como cero.
    """
    metricas = item.get("metricas_30d") or {}
    cost = _num(metricas.get("cost"))
    revenue = _num(metricas.get("ad_revenue"))
    if cost is not None and cost == 0:
        return EstadoFila("inactiva", "Sin actividad")
    if cost is not None and cost > 0 and revenue is not None and revenue == 0:
        return EstadoFila("gasto", "Gasto sin venta")
    valor = _target_valor(item)
    target = _num(valor) if valor is not None else None
    if cost is not None and cost > 0 and valor is None:
        return EstadoFila("sint", "Sin target")
    acos = _num(metricas.get("acos"))
    if target is None or acos is None:
        return None
    if acos > target:
        diff = (acos - target).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return EstadoFila("sobre", f"+{diff} pp sobre target")
    return EstadoFila("en", "En target")


@dataclass(frozen=True)
class FilaVista:
    item: dict
    estado: EstadoFila | None

    @property
    def clase(self) -> str:
        if self.estado is None:
            return ""
        return f"fila-{self.estado.clave}"


@dataclass(frozen=True)
class Segmento:
    clave: str
    etiqueta: str
    n: int


@dataclass(frozen=True)
class TarjetaPlataforma:
    etiqueta: str
    moneda: str
    n: int
    target: str
    acos: str
    acos_tono: str
    cost: str
    revenue: str
    clicks: str
    segmentos: tuple[Segmento, ...]
    unidades: tuple[str, ...]
    frase: str


@dataclass(frozen=True)
class Pestana:
    clave: str
    etiqueta: str
    n: int
    href: str
    activa: bool


@dataclass(frozen=True)
class PresentacionCampanas:
    tarjetas: tuple[TarjetaPlataforma, ...]
    pestanas: tuple[Pestana, ...]
    filas: tuple[FilaVista, ...]
    total: int
    mostradas: int
    vista: str


def _target_tarjeta(filas: list[FilaVista]) -> tuple[str, Decimal | None]:
    vistos: list[str] = []
    for fila in filas:
        valor = _target_valor(fila.item)
        if valor is None or valor in vistos:
            continue
        vistos.append(valor)
    if not vistos:
        return "sin target", None
    if len(vistos) > 1:
        return "varios targets", None
    unico = vistos[0]
    numero = _num(unico)
    if numero is None:
        return unico, None
    return f"{unico}%", numero


def _moneda(filas: list[FilaVista]) -> str:
    monedas = []
    for fila in filas:
        moneda = fila.item.get("moneda")
        if moneda and moneda not in monedas:
            monedas.append(moneda)
    if not monedas:
        return "—"
    if len(monedas) > 1:
        return "varias"
    return str(monedas[0])


def _frase(
    *,
    n: int,
    en: int,
    sobre: int,
    sinv: int,
    inact: int,
    sint: int,
    moneda: str,
    gasto: str,
    target_txt: str,
    target_num: Decimal | None,
    acos_txt: str,
) -> str:
    if sobre > 0 and en == 0 and target_num is not None and acos_txt.endswith("%"):
        if sobre == 1:
            cabeza = (
                f"La campaña con ventas está sobre el target de {target_txt}; "
                f"ACoS combinado {acos_txt}."
            )
        else:
            cabeza = (
                f"Las {sobre} campañas con ventas están sobre el target de {target_txt}; "
                f"ACoS combinado {acos_txt}."
            )
        extras = []
        if sinv == 1:
            extras.append(f"1 campaña gastó {gasto} {moneda} sin ventas.")
        elif sinv > 1:
            extras.append(f"{sinv} campañas gastaron {gasto} {moneda} sin ventas.")
        if inact == 1:
            extras.append("1 no tuvo gasto.")
        elif inact > 1:
            extras.append(f"{inact} no tuvieron gasto.")
        if sint == 1:
            extras.append("1 sin target.")
        elif sint > 1:
            extras.append(f"{sint} sin target.")
        if extras:
            return cabeza + " " + " ".join(extras)
        return cabeza
    gasto_verbo = "1 gastó" if sinv == 1 else f"{sinv} gastaron"
    inact_verbo = "1 no tuvo gasto" if inact == 1 else f"{inact} no tuvieron gasto"
    frase = (
        f"{en} de {n} campañas en target; {sobre} sobre target. "
        f"{gasto_verbo} {gasto} {moneda} sin ventas y {inact_verbo}."
    )
    if sint == 1:
        frase += " 1 sin target."
    elif sint > 1:
        frase += f" {sint} sin target."
    return frase


def _tarjeta(plataforma: str, filas: list[FilaVista]) -> TarjetaPlataforma:
    conteo = Counter(fila.estado.clave for fila in filas if fila.estado)
    costs = [_num((fila.item.get("metricas_30d") or {}).get("cost")) for fila in filas]
    revenues = [_num((fila.item.get("metricas_30d") or {}).get("ad_revenue")) for fila in filas]
    clicks = [_num((fila.item.get("metricas_30d") or {}).get("clicks")) for fila in filas]
    cost_sum = _suma(costs)
    rev_sum = _suma(revenues)
    target_txt, target_num = _target_tarjeta(filas)
    if cost_sum is None or rev_sum is None:
        acos_txt, tono = "—", "neutro"
    elif rev_sum == 0:
        acos_txt, tono = "sin ventas", "sinventas"
    else:
        bruto = acos_texto(cost_sum, rev_sum)
        if bruto is None:
            acos_txt, tono = "—", "neutro"
        else:
            acos_txt = f"{bruto}%"
            if target_num is None:
                tono = "neutro"
            elif Decimal(bruto) > target_num:
                tono = "sobre"
            else:
                tono = "en"
    segmentos = tuple(
        Segmento(clave, etiqueta, conteo[clave]) for clave, etiqueta in _BARRA if conteo[clave] > 0
    )
    unidades = tuple(segmento.clave for segmento in segmentos for _ in range(segmento.n))
    sinv_costos = [
        _num((fila.item.get("metricas_30d") or {}).get("cost"))
        for fila in filas
        if fila.estado and fila.estado.clave == "gasto"
    ]
    gasto = _texto_dinero(_suma(sinv_costos) if sinv_costos else Decimal(0))
    return TarjetaPlataforma(
        etiqueta=etiqueta_de(plataforma, "plataforma"),
        moneda=_moneda(filas),
        n=len(filas),
        target=target_txt,
        acos=acos_txt,
        acos_tono=tono,
        cost=_texto_dinero(cost_sum),
        revenue=_texto_dinero(rev_sum),
        clicks=_texto_clicks(_suma(clicks)),
        segmentos=segmentos,
        unidades=unidades,
        frase=_frase(
            n=len(filas),
            en=conteo["en"],
            sobre=conteo["sobre"],
            sinv=conteo["gasto"],
            inact=conteo["inactiva"],
            sint=conteo["sint"],
            moneda=_moneda(filas),
            gasto=gasto,
            target_txt=target_txt,
            target_num=target_num,
            acos_txt=acos_txt,
        ),
    )


def _orden_plataformas(filas: tuple[FilaVista, ...]) -> list[tuple[str, list[FilaVista]]]:
    grupos: dict[str, list[FilaVista]] = {}
    for fila in filas:
        grupos.setdefault(str(fila.item.get("plataforma") or ""), []).append(fila)
    orden: list[tuple[str, list[FilaVista]]] = []
    for clave in _ORDEN_PLATAFORMA:
        if clave in grupos:
            orden.append((clave, grupos.pop(clave)))
    for clave in sorted(grupos):
        orden.append((clave, grupos[clave]))
    return orden


def presenta_campanas(items: list[dict], vista: str, href_vista) -> PresentacionCampanas:
    """Tarjetas y pestañas sobre `items` (form ya aplicado). La tabla obedece `vista`."""
    base = tuple(FilaVista(item, estado_de(item)) for item in items)
    if vista == "todas":
        visibles = base
    else:
        visibles = tuple(
            fila for fila in base if fila.estado is not None and fila.estado.clave == vista
        )
    conteo = Counter(fila.estado.clave for fila in base if fila.estado)
    pestanas = tuple(
        Pestana(
            clave=clave,
            etiqueta=etiqueta,
            n=len(base) if clave == "todas" else conteo[clave],
            href=href_vista(clave),
            activa=clave == vista,
        )
        for clave, etiqueta in _PESTANAS
    )
    tarjetas = tuple(_tarjeta(clave, grupo) for clave, grupo in _orden_plataformas(base))
    return PresentacionCampanas(
        tarjetas=tarjetas,
        pestanas=pestanas,
        filas=visibles,
        total=len(base),
        mostradas=len(visibles),
        vista=vista,
    )
