"""Prueba real del bloque W-I (web.md F1, lado goals), para en_prod.sh.

Funciones puras con conexion falsa: sin base, sin red, sin escritura.
"""

import datetime as dt
from decimal import Decimal

from app import goals_write

COLUMNAS = tuple(col.strip() for col in goals_write._COLUMNAS.split(","))
T_BASE = dt.datetime(2020, 1, 1, 0, 0, tzinfo=dt.UTC)
T_EDITADO = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.UTC)


def _fila_mxn(**sobreescribe) -> dict:
    valores = {
        "id": 7,
        "scope": "platform",
        "ad_entity_id": None,
        "platform": "amazon_mx",
        "target_acos_pct": Decimal("25"),
        "bid_floor": Decimal("1.00"),
        "bid_ceiling": Decimal("45.00"),
        "bid_currency": "MXN",
        "harvest_campaign_id": None,
        "harvest_ad_group_id": None,
        "harvest_default_bid": None,
        "enabled": True,
        "mode": "shadow",
        "created_at": T_BASE,
        "updated_at": T_BASE,
    }
    valores.update(sobreescribe)
    fila = {col: valores[col] for col in COLUMNAS}
    assert tuple(fila.keys()) == COLUMNAS
    return fila


class _ConnFalsa:
    def __init__(self, fila: dict):
        self.fila = fila
        self.sql = ""
        self.params: tuple = ()
        self.commits = 0

    def execute(self, sql: str, params=None):
        if sql.lstrip().upper().startswith("SELECT"):
            return self
        self.sql = sql
        self.params = tuple(params or ())
        return self

    def fetchone(self):
        return dict(self.fila)

    def commit(self):
        self.commits += 1


RECHAZOS = (
    ({}, {"target_acos_pct": Decimal("2000")}),
    ({}, {"target_acos_pct": Decimal("0.2")}),
    ({}, {"target_acos_pct": Decimal("9.99")}),
    ({}, {"target_acos_pct": Decimal("45.01")}),
    ({}, {"bid_ceiling": Decimal("4500")}),
    ({}, {"bid_ceiling": Decimal("180.01")}),
    ({"bid_ceiling": Decimal("500.00")}, {"bid_floor": Decimal("180.01")}),
    (
        {
            "harvest_campaign_id": "c-1",
            "harvest_ad_group_id": "ag-1",
            "harvest_default_bid": Decimal("5.00"),
        },
        {"harvest_default_bid": Decimal("180.01")},
    ),
    (
        {
            "platform": "amazon_us",
            "bid_currency": "USD",
            "bid_floor": Decimal("0.10"),
            "bid_ceiling": Decimal("2.50"),
        },
        {"bid_ceiling": Decimal("10.01")},
    ),
)

for base_extra, edicion in RECHAZOS:
    conn = _ConnFalsa(_fila_mxn(**base_extra))
    try:
        goals_write.edita_goal(conn, 7, updated_at=T_EDITADO, **edicion)
    except goals_write.GoalInvalido:
        assert conn.sql == "" and conn.commits == 0, f"escribio antes de rechazar {edicion}"
    else:
        raise AssertionError(f"edita_goal acepto fuera de rango: {edicion}")

ACEPTADOS = (
    ({}, {"target_acos_pct": Decimal("10")}),
    ({}, {"target_acos_pct": Decimal("45")}),
    ({}, {"bid_ceiling": Decimal("180")}),
    ({"bid_ceiling": Decimal("180.00")}, {"bid_floor": Decimal("180")}),
    (
        {
            "harvest_campaign_id": "c-1",
            "harvest_ad_group_id": "ag-1",
            "harvest_default_bid": Decimal("5.00"),
        },
        {"harvest_default_bid": Decimal("180")},
    ),
    (
        {
            "platform": "amazon_us",
            "bid_currency": "USD",
            "bid_floor": Decimal("0.10"),
            "bid_ceiling": Decimal("2.50"),
        },
        {"bid_ceiling": Decimal("10")},
    ),
)

for base_extra, edicion in ACEPTADOS:
    conn = _ConnFalsa(_fila_mxn(**base_extra))
    goals_write.edita_goal(conn, 7, updated_at=T_EDITADO, **edicion)
    assert conn.sql.startswith("UPDATE ") and conn.commits == 1, edicion

print("W-I OK")
