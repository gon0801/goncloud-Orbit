"""Revision sub-A: un 207 con error por item en el POST del bloqueo.

`_ejecuta` (app/bloqueo_dueno_efecto.py:225) descarta la respuesta del POST:
un 207 donde Amazon dice que NO (error por item) se trata igual que un
envio incierto. El dueno ve "por confirmar", la reconciliacion de la hora
siguiente sella `fallido/sin_resultado` y el motivo de Amazon se pierde.
"""

from __future__ import annotations

import httpx
import test_bloqueo_dueno_efecto as t
from test_bloqueo_dueno import _db, _skip_db

efecto = t.efecto


@_skip_db
def test_207_con_error_por_item_no_se_lee(monkeypatch):
    t._parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = t._prepara(conn)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=t._perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                return httpx.Response(200, json={"negativeKeywords": []})
            if request.url.path == "/sp/negativeKeywords":
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "success": [],
                            "error": [
                                {
                                    "index": 0,
                                    "errors": [{"errorType": "invalidArgument", "errorValue": {}}],
                                }
                            ],
                        }
                    },
                )
            return httpx.Response(400, json={"message": request.url.path})

        resultado = efecto.aprobar(conn, senal, transport=httpx.MockTransport(handler))
        pasos = t._pasos(conn, resultado.veredicto_id)
        # Lo que deberia pasar: rechazo explicito de Amazon -> fallido con su motivo.
        assert resultado.paso == "fallido", (
            f"207 con error por item quedo {resultado.paso}/{resultado.motivo}; pasos={pasos}"
        )
