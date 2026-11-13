"""Revision sub-B: el smoke sella 'ok' en el ledger un 207 que trae el
rechazo por item (Amazon contesta 207 y mete el error adentro)."""

from __future__ import annotations

import httpx
import test_smoke_apply as t

sa = t.sa


class _ConnGraba(t._ConnStub):
    def __init__(self):
        super().__init__()
        self.params = []

    def execute(self, sql, params=None):
        self.params.append((" ".join(str(sql).split())[:40], params))
        return super().execute(sql, params)


def test_207_con_error_por_item_queda_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return t._token_response()
        return httpx.Response(
            207,
            json={"keywords": {"success": [], "error": [{"index": 0, "errors": ["BID_TOO_LOW"]}]}},
        )

    ctx = t._ctx_sin_db(handler)
    ctx.conn = _ConnGraba()
    paso = sa._paso_mutacion(
        ctx, "bid", {"keywordId": "1"}, lambda: ctx.cliente.actualizar_bid_keyword(
            "1", t.Decimal("0.50") if hasattr(t, "Decimal") else __import__("decimal").Decimal("0.50"), "USD"
        )
    )
    sellos = [p for s, p in ctx.conn.params if s.upper().startswith("UPDATE")]
    assert paso["ok"] is False, f"Amazon rechazo por item y el paso dice ok; sello={sellos}"
