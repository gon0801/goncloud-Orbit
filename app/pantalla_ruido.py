"""Tablero de ruido (BIDS 02, P.5). Puro contrato, sin plantillas.

Por ciclo: decisiones contadas por motivo y nivel, y abstenciones por
motivo. Por hoja: cuanto se encogio su bid en 90 dias. Comparacion
simple de antes y despues del encendido por mercado: no es causal.

Fuentes (regla 2): `decision.inputs` (motivo y nivel son llaves HERMANAS
de `caso`, leidas en Python) y `notes.skips` (via `_parse_notes`, como
`_skips_de`). Ningun `_SQL_*` nombra una llave de `inputs.caso`: la forma
vive solo en `app/optimizer/caso.py` y cada caso pasa por
`CasoHoja.desde_json` (frontera: caso ilegible = decision no contada, el
ciclo sale igual con sus abstenciones).

Decisiones que cuentan: kind 'bid' con `inputs["caso"]` presente; el
motivo y el nivel viajan tal cual si son texto, None si no. Solo salen
ciclos con al menos una de esas decisiones: la pantalla nace con el
motor. Las abstenciones son `notes.skips.entidad` (hojas; los skips de
termino son harvest, otra pantalla): notes ilegible o sin skips = None,
jamas 0. SOLO SELECT.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal

from app.api_common import _parse_notes
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import POLITICA_BID, CasoHoja

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]

DIAS_ENCOGIMIENTO = 90

_SQL_DECISIONES = """
SELECT d.cycle_id, d.inputs
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
 WHERE d.kind = 'bid' AND d.inputs ? 'caso'
   AND c.platform = %s AND c.motor = 'ads_optimizer'
 ORDER BY d.cycle_id, d.id
"""

_SQL_CICLOS = """
SELECT c.id, c.started_at, c.notes
  FROM optimizer_cycle c
 WHERE c.id = ANY(%s)
 ORDER BY c.id
"""

_SQL_CAMBIOS = """
SELECT h.hoja_id, h.current_bid, c.confirmado_el, c.bid_antes, c.bid_despues, c.decision_id
  FROM v_hoja_activa h
  JOIN v_cambio_bid c ON c.hoja_id = h.hoja_id
 WHERE h.platform = %s
   AND (c.confirmado_el AT TIME ZONE 'UTC')::date >= (now() AT TIME ZONE 'UTC')::date - %s
 ORDER BY h.hoja_id, c.confirmado_el, c.decision_id
"""

_SQL_ENCENDIDO = """
SELECT min(c.created_at)
  FROM config_version c
 WHERE c.settings ->> ('ads_bid_politica_' || %s) = %s
"""

_SQL_MERCADO = """
SELECT m.metric_date, m.cost, m.orders, m.ad_revenue
  FROM v_metric_latest m
  JOIN ad_entity h ON h.id = m.ad_entity_id
  JOIN ad_entity g ON g.id = h.parent_id
  JOIN ad_entity c ON c.id = g.parent_id
  JOIN ad_entity_state s ON s.ad_entity_id = c.id
 WHERE c.platform = %s AND c.kind = 'campaign'
   AND h.kind IN ('keyword', 'product_target')
   AND s.status = 'ENABLED'
   AND m.metric_date >= %s
"""


@dataclass(frozen=True)
class PantallaRuido:
    """Tablero de ruido del mercado. Sin grupo de control (decision D4 del
    diseno): la comparacion de antes y despues es simple, no causal."""

    plataforma: Plataforma
    ciclos: tuple[dict, ...]  # por ciclo: decisiones por motivo y nivel, abstenciones por motivo
    encogimiento: tuple[dict, ...]  # por hoja: bid de hoy entre el bid mas antiguo de 90 dias
    antes_y_despues: dict | None  # gasto, pedidos, venta y ACoS antes/desde el encendido

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "ciclos": list(self.ciclos),
            "encogimiento": list(self.encogimiento),
            "antes_y_despues": self.antes_y_despues,
        }


def _celda(fila, indice: int, nombre: str):
    """Tupla o dict_row (la conexion puede venir con dict_row: nota J5-r1)."""
    return fila[nombre] if isinstance(fila, dict) else fila[indice]


def _en_utc(instante: dt.datetime) -> dt.datetime:
    return instante if instante.tzinfo is not None else instante.replace(tzinfo=dt.UTC)


def _texto_o_none(valor) -> str | None:
    return valor if isinstance(valor, str) else None


def _abstenciones_de(notes) -> dict | None:
    """`notes.skips.entidad` por motivo. Notes ilegible o sin skips = None."""
    parsed = _parse_notes(notes)
    if not isinstance(parsed, dict):
        return None
    skips = parsed.get("skips")
    if not isinstance(skips, dict):
        return None
    entidad = skips.get("entidad")
    if not isinstance(entidad, dict):
        return None
    return dict(entidad)


def _cuenta_decisiones(filas) -> tuple[dict[int, dict[tuple, int]], set[int]]:
    """{(ciclo, motivo, nivel): n} + ciclos vistos. Solo cuenta la decision
    cuyo caso parsea con `CasoHoja.desde_json`; motivo y nivel salen de las
    llaves hermanas, en Python (el SQL jamas las nombra)."""
    conteo: dict[tuple, int] = {}
    vistos: set[int] = set()
    for fila in filas:
        cycle_id = _celda(fila, 0, "cycle_id")
        inputs = _celda(fila, 1, "inputs")
        caso = inputs.get("caso") if isinstance(inputs, dict) else None
        try:
            CasoHoja.desde_json(caso)
        except (ValueError, TypeError, AttributeError):
            continue
        vistos.add(cycle_id)
        clave = (
            cycle_id,
            _texto_o_none(inputs.get("motivo")),
            _texto_o_none(inputs.get("nivel")),
        )
        conteo[clave] = conteo.get(clave, 0) + 1
    por_ciclo: dict[int, dict[tuple, int]] = {}
    for (ciclo, motivo, nivel), veces in conteo.items():
        por_ciclo.setdefault(ciclo, {})[(motivo, nivel)] = veces
    return por_ciclo, vistos


def _fila_ciclo(fila, conteo: dict[tuple, int]) -> dict:
    decisiones = sorted(
        (
            {"motivo": motivo, "nivel": nivel, "count": veces}
            for (motivo, nivel), veces in conteo.items()
        ),
        key=lambda d: (d["motivo"] or "", d["nivel"] or ""),
    )
    return {
        "cycle_id": _celda(fila, 0, "id"),
        "started_at": _celda(fila, 1, "started_at").isoformat(),
        "decisiones": decisiones,
        "abstenciones": _abstenciones_de(_celda(fila, 2, "notes")),
    }


def _encogimiento(filas, moneda: Moneda) -> tuple[dict, ...]:
    """Por hoja activa con cambios en la ventana: bid de hoy (vigente)
    entre el bid previo al cambio mas antiguo. Sin cambios, sin base
    (regreso sin previo en ventana) o sin bid vigente la hoja no sale.
    Orden: mas encogido primero; desempate por hoja."""
    por_hoja: dict[int, dict] = {}
    for fila in filas:
        hoja_id = _celda(fila, 0, "hoja_id")
        entrada = por_hoja.setdefault(
            hoja_id, {"hoy": _celda(fila, 1, "current_bid"), "cambios": []}
        )
        entrada["cambios"].append(
            (
                _en_utc(_celda(fila, 2, "confirmado_el")),
                _celda(fila, 5, "decision_id"),
                _celda(fila, 3, "bid_antes"),
            )
        )
    medidas = []
    for hoja_id, entrada in por_hoja.items():
        hoy = entrada["hoy"]
        base = min(entrada["cambios"])[2]
        if hoy is None or base is None or base == 0:
            continue
        with localcontext(prec=28):
            razon = hoy / base
        medidas.append((razon, hoja_id, base, hoy))
    medidas.sort(key=lambda m: (m[0], m[1]))
    return tuple(
        {
            "hoja_id": hoja,
            "bid_base": str(base),
            "bid_hoy": str(hoy),
            "razon": str(razon),
            "moneda": moneda,
        }
        for razon, hoja, base, hoy in medidas
    )


def _suma(valores) -> Decimal | int | None:
    medidos = [v for v in valores if v is not None]
    if not medidos:
        return None
    return sum(medidos, Decimal(0) if isinstance(medidos[0], Decimal) else 0)


def _bloque(filas) -> dict | None:
    """Suma de la ventana: gasto, pedidos, venta y ACoS. Sin filas = None;
    NULLs no suman (regla 3). ACoS a 2 decimales, None sin venta."""
    if not filas:
        return None
    gasto = _suma(_celda(f, 1, "cost") for f in filas)
    pedidos = _suma(_celda(f, 2, "orders") for f in filas)
    venta = _suma(_celda(f, 3, "ad_revenue") for f in filas)
    acos = None
    if gasto is not None and venta is not None and venta > 0:
        with localcontext(prec=28):
            acos = (gasto / venta * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return {
        "gasto": None if gasto is None else str(gasto),
        "pedidos": pedidos,
        "venta": None if venta is None else str(venta),
        "acos_pct": None if acos is None else str(acos),
    }


def _comparacion(conn, plataforma: Plataforma, dias: int) -> dict | None:
    """`dias` previos al encendido contra los dias transcurridos desde
    entonces, en `v_metric_latest`, solo campanas ENABLED. Sin fila de
    encendido = None (comparacion simple, no causal)."""
    primera = conn.execute(_SQL_ENCENDIDO, (plataforma, POLITICA_BID)).fetchone()
    encendido = _celda(primera, 0, "min") if primera is not None else None
    if encendido is None:
        return None
    dia = _en_utc(encendido).astimezone(dt.UTC).date()
    filas = conn.execute(_SQL_MERCADO, (plataforma, dia - dt.timedelta(days=dias))).fetchall()
    antes = [f for f in filas if _celda(f, 0, "metric_date") < dia]
    despues = [f for f in filas if _celda(f, 0, "metric_date") >= dia]
    return {
        "encendido_el": dia.isoformat(),
        "moneda": PLATAFORMAS_MONEDA[plataforma],
        "antes": _bloque(antes),
        "despues": _bloque(despues),
    }


def lee_ruido(conn, *, plataforma: Plataforma, dias: int = 30) -> PantallaRuido:
    """Tablero de ruido del mercado. SOLO SELECT."""
    filas_decisiones = conn.execute(_SQL_DECISIONES, (plataforma,)).fetchall()
    conteo, vistos = _cuenta_decisiones(filas_decisiones)
    filas_ciclos = conn.execute(_SQL_CICLOS, (sorted(vistos),)).fetchall() if vistos else []
    ciclos = tuple(
        _fila_ciclo(fila, conteo.get(_celda(fila, 0, "id"), {})) for fila in filas_ciclos
    )
    filas_cambios = conn.execute(_SQL_CAMBIOS, (plataforma, DIAS_ENCOGIMIENTO)).fetchall()
    encogimiento = _encogimiento(filas_cambios, PLATAFORMAS_MONEDA[plataforma])
    return PantallaRuido(plataforma, ciclos, encogimiento, _comparacion(conn, plataforma, dias))
