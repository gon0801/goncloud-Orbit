"""Sonda F1: reconcilia_bids reintenta el PUT de un bid divergente aunque el
goal de la campana ya este en shadow/off (o enabled=false). aplica_bids, con
la MISMA entidad y el MISMO goal, NO escribiria (modo_efectivo -> shadow).

Pura: conexion falsa que despacha por SQL + httpx.MockTransport. Cero red.
"""
from __future__ import annotations

import contextlib
import json
from decimal import Decimal

import httpx

from app import apply
from app.ads.config import AdsCredentials

PUTS: list[dict] = []
LISTS = 0
BID_VIVO = 1.00  # lo que Amazon tiene antes del reintento


def handler(request: httpx.Request) -> httpx.Response:
    global LISTS
    if request.url.host == "api.amazon.com":
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if request.method == "PUT" and request.url.path == "/sp/keywords":
        body = json.loads(request.content)
        PUTS.append(body)
        return httpx.Response(207, json={"keywords": {"success": [{"keywordId": "KW1"}], "error": []}})
    if request.method == "POST" and request.url.path == "/sp/keywords/list":
        LISTS += 1
        # Amazon tiene 1.00 (el PUT original nunca entro); despues del
        # reintento ya tiene 1.50.
        bid = 1.50 if PUTS else BID_VIVO
        return httpx.Response(200, json={"keywords": [{"keywordId": "KW1", "bid": bid, "state": "ENABLED"}]})
    return httpx.Response(404, json={})


class Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConn:
    """Estado: 1 intento normal sin sello de la decision 77 (bid 1.00 -> 1.50,
    MXN) sobre la keyword 10; goal de campana en mode='shadow'."""

    autocommit = False

    def __init__(self, goal_mode: str, goal_enabled: bool):
        self.goal_mode = goal_mode
        self.goal_enabled = goal_enabled
        self.normales = 1

    @contextlib.contextmanager
    def transaction(self):
        yield

    def commit(self):
        pass

    def execute(self, sql, params=None):
        if sql is apply._SQL_INTENTOS_BID_SIN_SELLO:
            payload = {"keywordId": "KW1", "bid": "1.50"}
            return Cur([(501, 77, payload, 10, Decimal("1.50"), "MXN")])
        if sql is apply._SQL_ANCESTROS:
            return Cur([("ENABLED", "ENABLED")])
        if sql is apply._SQL_IDENTIDAD:
            return Cur([("keyword", "KW1")])
        if sql is apply._SQL_COUNT_INTENTOS:
            return Cur([(self.normales,)])
        if sql is apply._SQL_MAX_SEQ:
            return Cur([(self.normales,)])
        if sql is apply._SQL_INSERT_LEDGER:
            self.normales += 1
            return Cur([(502,)])
        if sql is apply._SQL_ENTIDAD_VIVA:
            return Cur([(5, True)])  # campaign_id=5, tiene_state
        if sql is apply._SQL_GOALS_ENTIDAD:
            return Cur([
                ("campaign", 5, None, Decimal("20"), Decimal("1.00"), Decimal("45.00"), "MXN",
                 None, None, None, self.goal_enabled, self.goal_mode),
            ])
        # sellos, resumen, cache, applied_count: se ignoran
        return Cur([])


def corre(goal_mode: str, goal_enabled: bool) -> None:
    PUTS.clear()
    conn = FakeConn(goal_mode, goal_enabled)
    aplicador = apply.Aplicador(
        conn,
        platform="amazon_mx",
        cycle_id_ejecutor=900,
        owner="o",
        job_key="k",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        credentials=AdsCredentials("cid", "csecret", "rtok"),
        profile_id="111",
    )
    decision = apply.DecisionBid(id=77, ad_entity_id=10, old_value=Decimal("1.00"),
                                 new_value=Decimal("1.50"), value_currency="MXN", inputs={})
    modo = aplicador.modo_efectivo(conn, decision, escalera_global="live")
    res = apply.reconcilia_bids(conn, aplicador, "amazon_mx")
    print(f"goal mode={goal_mode!r} enabled={goal_enabled}: "
          f"modo_efectivo (lo que usa aplica_bids)={modo!r}; "
          f"reconcilia_bids -> (confirmadas, fallidas)={res}; PUTs enviados a Amazon={PUTS}")


if __name__ == "__main__":
    corre("shadow", True)
    corre("off", True)
    corre("live", False)
    # Variante "estado viejo": Amazon ya tiene 0.80, el bid de una decision
    # POSTERIOR (D2) aplicada y confirmada; la fila sin sello es del reintento
    # de D1 (1.50). El SQL de reconcilia_bids no mira decisiones posteriores.
    BID_VIVO = 0.80
    print("-- Amazon tiene 0.80 (decision posterior ya aplicada):")
    corre("live", True)
