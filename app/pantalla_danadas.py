"""Keywords danadas (BIDS 02, P.3a): contrato puro, sin plantillas.

Hoja activa con racha vigente de recortes (`v_cambio_bid`), que vendia
(>= 1 pedido en los 90 dias previos a la racha) y cuyos clics de los
ultimos 14 dias legibles son menos de 30 % de los 14 dias previos a la
racha (definicion medida en `prototipos/a6_pantallas.py`). La consumen
M.4 (rejuego), M.5 (regreso del dueno) y P.3b (pantalla).

Reglas de la racha (guia P.3a, cambio 2): un `regreso_del_dueno` NO la
cierra (es invisible para el computo, como en la consulta de control, que
lo excluye por `bid_antes IS NULL`); una subida del motor SI, incluido un
`regreso_por_desplome` (lo decide el motor). `ya_regresada` solo mira
regresos posteriores al ultimo recorte: un regreso en medio de la racha
deja dano nuevo y no marca.

Ventanas (guia P.3a, cambio 3): los 90 y 14 dias previos terminan el dia
anterior al primer recorte; los ultimos 14 van de HOY - 15 a HOY - 2,
donde HOY es la ultima fecha con metricas de la plataforma. `bid_hoy` es
el bid vigente (`v_hoja_activa.current_bid`), no el del ultimo recorte:
con un regreso posterior difieren. Sin filtro de `bid_hoy < bid_antes`
(la consulta de control no lo trae; la guia gana al bosquejo).

`calculado_el` es la medianoche UTC de HOY: determinista, derivado de los
datos, sin reloj. `None` sin metricas.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from app.etiqueta_entidad import etiqueta_entidad
from app.optimizer.bid import PLATAFORMAS_MONEDA

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]

ORIGEN_REGRESO_DUENO = "regreso_del_dueno"

_SQL_CAMBIOS = """
SELECT h.hoja_id, h.current_bid, c.confirmado_el, c.bid_antes, c.bid_despues,
       c.origen, c.decision_id
  FROM v_hoja_activa h
  JOIN v_cambio_bid c ON c.hoja_id = h.hoja_id
 WHERE h.platform = %s
 ORDER BY h.hoja_id, c.confirmado_el, c.decision_id
"""

_SQL_FECHA_MAX = """
SELECT max(m.metric_date) AS fecha_max
  FROM v_metric_latest m
  JOIN v_hoja_activa h ON h.hoja_id = m.ad_entity_id
 WHERE h.platform = %s
"""

_SQL_METRICAS = """
SELECT m.ad_entity_id, m.metric_date, m.clicks, m.orders, m.ad_revenue
  FROM v_metric_latest m
 WHERE m.ad_entity_id = ANY(%s) AND m.metric_date >= %s
"""

_SQL_NOMBRES = """
SELECT e.id, e.kind, e.name, e.keyword_text, c.name AS campana
  FROM ad_entity e
  JOIN ad_entity g ON g.id = e.parent_id
  JOIN ad_entity c ON c.id = g.parent_id
 WHERE e.id = ANY(%s)
"""


@dataclass(frozen=True)
class HojaDanada:
    hoja_id: int
    nombre: str | None  # parte hoja de etiqueta_entidad; None si no hay texto
    campana: str | None
    recortes: int
    bid_antes: Decimal  # bid anterior al primer recorte de la racha
    bid_hoy: Decimal | None  # bid vigente; None si la hoja hereda el del grupo
    moneda: Moneda
    clics_antes_14d: int
    clics_ahora_14d: int
    pedidos_antes_90d: int
    venta_antes_90d: Decimal
    ya_regresada: bool
    regresada_el: dt.date | None

    def como_dict(self) -> dict:
        return {
            "hoja_id": self.hoja_id,
            "nombre": self.nombre,
            "campana": self.campana,
            "recortes": self.recortes,
            "bid_antes": str(self.bid_antes),
            "bid_hoy": None if self.bid_hoy is None else str(self.bid_hoy),
            "moneda": self.moneda,
            "clics_antes_14d": self.clics_antes_14d,
            "clics_ahora_14d": self.clics_ahora_14d,
            "pedidos_antes_90d": self.pedidos_antes_90d,
            "venta_antes_90d": str(self.venta_antes_90d),
            "ya_regresada": self.ya_regresada,
            "regresada_el": None if self.regresada_el is None else self.regresada_el.isoformat(),
        }


@dataclass(frozen=True)
class PantallaDanadas:
    """Keywords que vendian y perdieron su trafico tras un recorte. Orden:
    mas venta previa primero; desempate por hoja_id ascendente."""

    plataforma: Plataforma
    calculado_el: dt.datetime | None  # medianoche UTC de HOY; None sin metricas
    hojas: tuple[HojaDanada, ...]

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "calculado_el": (
                None
                if self.calculado_el is None
                else self.calculado_el.astimezone(dt.UTC).isoformat()
            ),
            "hojas": [hoja.como_dict() for hoja in self.hojas],
        }


def _celda(fila, indice: int, nombre: str):
    """Tupla o dict_row (la conexion puede venir con dict_row: nota J5-r1)."""
    return fila[nombre] if isinstance(fila, dict) else fila[indice]


def _en_utc(instante: dt.datetime) -> dt.datetime:
    return instante if instante.tzinfo is not None else instante.replace(tzinfo=dt.UTC)


def _racha_vigente(cambios: list[tuple]) -> tuple[list[tuple], dt.datetime | None]:
    """Recortes posteriores a la ultima subida (los regresos del dueno no
    cuentan ni cortan). Devuelve la racha y el fin (ultimo recorte)."""
    subidas = [
        _en_utc(c[0]) for c in cambios if c[1] is not None and c[2] is not None and c[2] > c[1]
    ]
    ultima_subida = max(subidas) if subidas else None
    racha = [
        c
        for c in cambios
        if c[3] != ORIGEN_REGRESO_DUENO
        and c[1] is not None
        and c[2] is not None
        and c[2] < c[1]
        and (ultima_subida is None or _en_utc(c[0]) >= ultima_subida)
    ]
    fin = max((_en_utc(c[0]) for c in racha), default=None)
    return racha, fin


def lee_danadas(conn, *, plataforma: Plataforma) -> PantallaDanadas:
    """Hoja activa con racha vigente de recortes, >= 1 pedido en los 90 dias
    previos a la racha y clics de los ultimos 14 dias legibles menores a
    30 % de los 14 dias previos. SOLO SELECT."""
    filas_cambios = conn.execute(_SQL_CAMBIOS, (plataforma,)).fetchall()
    por_hoja: dict[int, dict] = {}
    for fila in filas_cambios:
        hoja_id = _celda(fila, 0, "hoja_id")
        entrada = por_hoja.setdefault(
            hoja_id, {"bid_hoy": _celda(fila, 1, "current_bid"), "cambios": []}
        )
        entrada["cambios"].append(
            (
                _celda(fila, 2, "confirmado_el"),
                _celda(fila, 3, "bid_antes"),
                _celda(fila, 4, "bid_despues"),
                _celda(fila, 5, "origen"),
                _celda(fila, 6, "decision_id"),
            )
        )
    rachas = {hoja_id: _racha_vigente(entrada["cambios"]) for hoja_id, entrada in por_hoja.items()}
    rachas = {h: r for h, r in rachas.items() if r[0]}

    ultima = conn.execute(_SQL_FECHA_MAX, (plataforma,)).fetchone()
    hoy = _celda(ultima, 0, "fecha_max")
    if hoy is None or not rachas:
        calculado = None if hoy is None else dt.datetime.combine(hoy, dt.time.min, tzinfo=dt.UTC)
        return PantallaDanadas(plataforma, calculado, ())

    inicios = {
        h: min(_en_utc(c[0]) for c in racha).astimezone(dt.UTC).date()
        for h, (racha, _) in rachas.items()
    }
    desde = min(inicios.values()) - dt.timedelta(days=90)
    metricas: dict[int, list[tuple]] = {}
    for fila in conn.execute(_SQL_METRICAS, (sorted(rachas), desde)).fetchall():
        metricas.setdefault(_celda(fila, 0, "ad_entity_id"), []).append(
            (
                _celda(fila, 1, "metric_date"),
                _celda(fila, 2, "clicks"),
                _celda(fila, 3, "orders"),
                _celda(fila, 4, "ad_revenue"),
            )
        )

    candidatas: list[HojaDanada] = []
    for hoja_id, (racha, fin) in rachas.items():
        inicio = inicios[hoja_id]
        antes_hasta = inicio - dt.timedelta(days=1)
        pedidos_antes = 0
        venta_antes = Decimal(0)
        clics_antes = 0
        clics_ahora = 0
        for fecha, clics, pedidos, venta in metricas.get(hoja_id, []):
            if inicio - dt.timedelta(days=90) <= fecha <= antes_hasta:
                if pedidos is not None:
                    pedidos_antes += pedidos
                if venta is not None:
                    venta_antes += venta
            if inicio - dt.timedelta(days=14) <= fecha <= antes_hasta and clics is not None:
                clics_antes += clics
            if (
                hoy - dt.timedelta(days=15) <= fecha <= hoy - dt.timedelta(days=2)
                and clics is not None
            ):
                clics_ahora += clics
        if not (pedidos_antes >= 1 and clics_antes > 0 and clics_ahora * 10 < clics_antes * 3):
            continue
        regresos = sorted(
            _en_utc(c[0]).astimezone(dt.UTC).date()
            for c in por_hoja[hoja_id]["cambios"]
            if c[3] == ORIGEN_REGRESO_DUENO and _en_utc(c[0]) > fin
        )
        primero = min(racha, key=lambda c: (_en_utc(c[0]), c[4]))
        candidatas.append(
            HojaDanada(
                hoja_id=hoja_id,
                nombre=None,
                campana=None,
                recortes=len(racha),
                bid_antes=primero[1],
                bid_hoy=por_hoja[hoja_id]["bid_hoy"],
                moneda=PLATAFORMAS_MONEDA[plataforma],
                clics_antes_14d=clics_antes,
                clics_ahora_14d=clics_ahora,
                pedidos_antes_90d=pedidos_antes,
                venta_antes_90d=venta_antes,
                ya_regresada=bool(regresos),
                regresada_el=regresos[-1] if regresos else None,
            )
        )
    if not candidatas:
        calculado = dt.datetime.combine(hoy, dt.time.min, tzinfo=dt.UTC)
        return PantallaDanadas(plataforma, calculado, ())

    nombres = {
        _celda(f, 0, "id"): f
        for f in conn.execute(_SQL_NOMBRES, ([c.hoja_id for c in candidatas],)).fetchall()
    }
    hojas = []
    for cand in candidatas:
        fila = nombres.get(cand.hoja_id)
        if fila is None:
            hojas.append(cand)
            continue
        etiqueta = etiqueta_entidad(
            kind=_celda(fila, 1, "kind"),
            name=_celda(fila, 2, "name"),
            keyword_text=_celda(fila, 3, "keyword_text"),
            campana=_celda(fila, 4, "campana"),
        )
        hojas.append(replace(cand, nombre=etiqueta.hoja, campana=etiqueta.campana))
    hojas.sort(key=lambda h: (-h.venta_antes_90d, h.hoja_id))
    calculado = dt.datetime.combine(hoy, dt.time.min, tzinfo=dt.UTC)
    return PantallaDanadas(plataforma, calculado, tuple(hojas))
