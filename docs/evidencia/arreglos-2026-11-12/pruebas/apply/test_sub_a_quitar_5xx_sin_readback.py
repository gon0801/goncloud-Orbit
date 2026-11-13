"""Revision sub-A: quitar un bloqueo cuando el POST /delete responde 5xx
pero Amazon SI archivo el negative.

`_quitar_con_candado` (app/bloqueo_dueno_efecto.py:261-275) trata cualquier
excepcion del delete como `reversa_fallida` sin releer Amazon (la ida, en
`_ejecuta`, si relee tras un 5xx). `reversa_fallida` no esta en
`_PASOS_HUERFANOS`, asi que la reconciliacion de cada hora nunca la
revisa: el ledger dice "bloqueo vivo" para siempre aunque el negative ya
esta ARCHIVED.
"""

from __future__ import annotations

import json

import httpx
import test_bloqueo_dueno_efecto as t
from test_bloqueo_dueno import _db, _skip_db

efecto = t.efecto


@_skip_db
def test_delete_5xx_aplicado_queda_reversa_fallida_para_siempre(monkeypatch):
    t._parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _campana, termino, _roster = t._prepara(conn)
        transport, _vistos, store = t._transporte(grupo_ext, termino)
        hecho = efecto.aprobar(conn, senal, transport=transport)
        assert hecho.paso == "aplicado"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=t._perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                return httpx.Response(200, json={"negativeKeywords": list(store)})
            if request.url.path == "/sp/negativeKeywords/delete":
                kid = json.loads(request.content)["negativeKeywordIdFilter"]["include"][0]
                for item in store:
                    if item["keywordId"] == str(kid):
                        item["state"] = "ARCHIVED"  # Amazon lo aplico...
                return httpx.Response(503, json={"message": "gateway"})  # ...y respondio 5xx
            return httpx.Response(400, json={"message": request.url.path})

        quitado = efecto.quitar(
            conn, hecho.veredicto_id, transport=httpx.MockTransport(handler)
        )
        efecto.reconcilia_todas(conn, transport=httpx.MockTransport(handler))
        ultimo = t._pasos(conn, hecho.veredicto_id)[-1]
        assert [i["state"] for i in store] == ["ARCHIVED"]
        # Lo que deberia pasar: con el negative ARCHIVED en Amazon el
        # ledger termina en 'revertido' (relectura tras el 5xx o en la
        # reconciliacion).
        assert ultimo[0] == "revertido", (
            f"quitar={quitado.paso}/{quitado.motivo}; ultimo paso {ultimo} con el negative"
            " ya ARCHIVED en Amazon; la reconciliacion no lo revisa"
        )
        assert grupo > 0
