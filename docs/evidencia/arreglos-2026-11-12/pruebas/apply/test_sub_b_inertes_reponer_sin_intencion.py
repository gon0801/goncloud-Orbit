"""Revision sub-B: `tools/archiva_inertes.py --reponer` CREA una keyword en
Amazon sin escribir antes ninguna intencion durable. Si el readback no
cuadra (consistencia eventual a 0.3 s, red), el lote aborta sin dejar rastro
del keywordId creado y la fila sigue 'applied': la siguiente corrida vuelve
a mandar el create.
"""

from __future__ import annotations

import sys

import pytest
import test_archiva_inertes as t

from app.ads.client import AdsApiError

ar = t.ar


def test_reponer_crea_sin_intencion_y_repite_el_create(monkeypatch):
    red = t._RedFalsa(creates=[t._ACK_CREATE_OK, t._ACK_CREATE_OK])
    conn_admin = t._ConnFalsa(reponer=[t._FILA_REPONER])
    # Corrida 1: create aceptado (kw-nuevo-1), el LIST aun no lo ve.
    cliente = t._ClienteFalso({"kw-nuevo-1": [AdsApiError("aun no visible")]})
    t._fakea_frontera(monkeypatch, t._ConnFalsa(), conn_admin, cliente, red)
    monkeypatch.setattr(ar.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv", t._argv_reponer("--go", t.GO))
    with pytest.raises(ar.Abortar, match="no cuadra"):
        ar.main()

    rastro = [p for p in conn_admin.inserts + [u[1] for u in conn_admin.updates]]
    creates_1 = [p for p in red.pedidos if str(p.url).endswith("/sp/keywords")]
    assert len(creates_1) == 1

    # Corrida 2 (el operador reintenta): la fila sigue 'applied'.
    t._fakea_frontera(monkeypatch, t._ConnFalsa(), conn_admin, t._cliente_repuesta(), red)
    ar.main()
    creates = [p for p in red.pedidos if str(p.url).endswith("/sp/keywords")]
    assert rastro and len(creates) == 1, (
        f"sin intencion pre-HTTP: rastro en ledger tras corrida 1 = {rastro}; "
        f"creates enviados en total = {len(creates)} (la keyword de la corrida 1 quedo huerfana)"
    )
