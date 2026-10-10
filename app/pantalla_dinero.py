"""Donde poner el dinero (BIDS 02, P.1): contrato puro, sin plantillas.

Suma HOJAS activas por tipo de campana (`v_hoja_activa` trae el tipo:
primero AUTO de la campana, despues match type o product target). Nunca
filas de campana: sumar campanas y hojas duplicaria el gasto. Nunca MX
con US: una tabla por mercado.

Veneno por metrica (precedente `_SQL_CAMPANAS_30D`): si alguna fila del
grupo trae la metrica NULL, la suma del grupo es NULL (no se publica un
parcial como completo); el total es NULL si algun grupo lo es. Venta 0
da ACoS NULL ("sin ventas", nunca division). Tipo NULL o fuera de los
cinco entra al total, no a los renglones, y se cuenta aparte.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal, get_args

from app.optimizer.bid import PLATAFORMAS_MONEDA

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]
TipoCampana = Literal["exact", "phrase", "broad", "automatica", "product_targeting"]

TIPOS: tuple[str, ...] = get_args(TipoCampana)

_SQL_GRUPOS = """
SELECT h.tipo_campana AS tipo,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END AS gasto,
       CASE WHEN bool_and(v.orders IS NOT NULL) THEN sum(v.orders)::bigint END AS pedidos,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END AS venta,
       count(DISTINCT h.hoja_id) AS hojas
  FROM v_hoja_activa h
  JOIN v_metric_latest v ON v.ad_entity_id = h.hoja_id
 WHERE h.platform = %s
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY h.tipo_campana
"""


@dataclass(frozen=True)
class FilaTipo:
    """Un renglon: `tipo` es un valor de `TipoCampana`, `""` = total."""

    tipo: str
    gasto: Decimal | None
    pedidos: int | None
    venta: Decimal | None
    acos_pct: Decimal | None
    parte_del_gasto_pct: Decimal | None
    hojas: int

    def como_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "gasto": None if self.gasto is None else str(self.gasto),
            "pedidos": self.pedidos,
            "venta": None if self.venta is None else str(self.venta),
            "acos_pct": None if self.acos_pct is None else str(self.acos_pct),
            "parte_del_gasto_pct": (
                None if self.parte_del_gasto_pct is None else str(self.parte_del_gasto_pct)
            ),
            "hojas": self.hojas,
        }


@dataclass(frozen=True)
class PantallaDinero:
    """Una tabla por mercado, solo lo que hoy esta encendido."""

    plataforma: Plataforma
    moneda: Moneda
    desde: dt.date
    hasta: dt.date
    filas: tuple[FilaTipo, ...]
    total: FilaTipo
    hojas_sin_clasificar: int
    target_acos_pct: Decimal | None

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "moneda": self.moneda,
            "desde": self.desde.isoformat(),
            "hasta": self.hasta.isoformat(),
            "filas": [fila.como_dict() for fila in self.filas],
            "total": self.total.como_dict(),
            "hojas_sin_clasificar": self.hojas_sin_clasificar,
            "target_acos_pct": None if self.target_acos_pct is None else str(self.target_acos_pct),
        }


def _pct(numerador: Decimal | None, denominador: Decimal | None) -> Decimal | None:
    """Porcentaje 0-100 a 1 decimal (HALF_EVEN, precedente `_dos_dec`).
    Denominador None o 0 da None, nunca division."""
    if numerador is None or denominador is None or denominador == 0:
        return None
    return (numerador / denominador * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)


def lee_dinero(
    conn, *, plataforma: Plataforma, dias: int = 90, hasta: dt.date | None = None
) -> PantallaDinero:
    """Tabla por tipo de campana del mercado. SOLO SELECT. `hasta` omiso =
    ayer UTC; la ventana va de `hasta - dias` a `hasta` (BETWEEN inclusivo).
    El target es el del ultimo ciclo done (`_target_margen_del_ciclo`,
    import diferido: este modulo puro no carga el dashboard al importar)."""
    from app.api_dashboard import _target_margen_del_ciclo

    fin = hasta if hasta is not None else dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    inicio = fin - dt.timedelta(days=dias)
    grupos = {
        fila[0]: fila[1:]
        for fila in conn.execute(_SQL_GRUPOS, (plataforma, inicio, fin)).fetchall()
    }
    conocidos = {tipo: grupos.get(tipo) for tipo in TIPOS}
    resto = [(tipo, vals) for tipo, vals in grupos.items() if tipo not in TIPOS]
    presentes = [vals for vals in conocidos.values() if vals is not None] + [
        vals for _, vals in resto
    ]

    def _suma(indice: int):
        """Suma de la metrica sobre todos los grupos, con veneno: un grupo
        con NULL anula el total; sin grupos, NULL (no 0)."""
        if not presentes or any(vals[indice] is None for vals in presentes):
            return None
        return sum(vals[indice] for vals in presentes)

    gasto_total = _suma(0)
    pedidos_total = _suma(1)
    venta_total = _suma(2)

    def _fila(tipo: str, vals: tuple | None) -> FilaTipo:
        gasto, pedidos, venta, hojas = vals if vals is not None else (None, None, None, 0)
        return FilaTipo(
            tipo=tipo,
            gasto=gasto,
            pedidos=pedidos,
            venta=venta,
            acos_pct=_pct(gasto, venta),
            parte_del_gasto_pct=_pct(gasto, gasto_total),
            hojas=hojas,
        )

    filas = tuple(_fila(tipo, conocidos[tipo]) for tipo in TIPOS)
    hojas_total = sum(vals[3] for vals in conocidos.values() if vals is not None) + sum(
        vals[3] for _, vals in resto
    )
    total = FilaTipo(
        tipo="",
        gasto=gasto_total,
        pedidos=pedidos_total,
        venta=venta_total,
        acos_pct=_pct(gasto_total, venta_total),
        parte_del_gasto_pct=Decimal("100.0") if gasto_total is not None else None,
        hojas=hojas_total,
    )
    return PantallaDinero(
        plataforma=plataforma,
        moneda=PLATAFORMAS_MONEDA[plataforma],
        desde=inicio,
        hasta=fin,
        filas=filas,
        total=total,
        hojas_sin_clasificar=sum(vals[3] for _, vals in resto),
        target_acos_pct=_target_margen_del_ciclo(conn, plataforma),
    )
