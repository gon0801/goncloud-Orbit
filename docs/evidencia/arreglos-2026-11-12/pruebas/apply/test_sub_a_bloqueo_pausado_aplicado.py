"""Revision sub-A: aprobar un bloqueo cuando Amazon ya tiene el negative
del termino en PAUSED.

`_ejecuta` (app/bloqueo_dueno_efecto.py:219-223) adopta cualquier item no
ARCHIVED que devuelve `_identidad` y sella `aplicado` SIN mandar el POST y
sin mirar el `state`. Un negative PAUSED no niega nada: el termino sigue
gastando y la pantalla del dueno dice "bloqueado". `_readback` (:238) y
`_cierra_huerfano` (:312) si exigen ENABLED; el camino de ida no.
"""

from __future__ import annotations

import httpx
import test_bloqueo_dueno_efecto as t
from test_bloqueo_dueno import _db, _skip_db

efecto = t.efecto


@_skip_db
def test_negative_pausado_no_cuenta_como_bloqueo_aplicado(monkeypatch):
    t._parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = t._prepara(conn)
        transport, vistos, store = t._transporte(grupo_ext, termino)
        store.append(
            {
                "keywordId": "kid-pausado",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "PAUSED",
            }
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        posts = t._posts(vistos)
        pasos = t._pasos(conn, resultado.veredicto_id)
        # Lo que deberia pasar: el PAUSED no bloquea; o se crea/activa el
        # negative (POST) o el veredicto NO queda 'aplicado'.
        assert not (resultado.paso == "aplicado" and posts == []), (
            f"sello {pasos} sin POST con el negative en PAUSED: el termino sigue gastando"
        )
        assert isinstance(transport, httpx.MockTransport)
