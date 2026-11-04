"""Regresion de la sonda S.0 (docs/evidencia/ingesta-01/ejecucion/S.0/sonda.py).

La corrida real 2026-10-21 perdio los targets de (a) y (d): el POST devuelve
el `reportId` AL FINAL del JSON y la sonda truncaba el cuerpo a 600 chars
ANTES de parsearlo (las cfg con `filters` largos -- targets -- pasan de 600).
Este test congela esa leccion contra un cuerpo con la forma real.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials

SONDA_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "evidencia"
    / "ingesta-01"
    / "ejecucion"
    / "S.0"
    / "sonda.py"
)
RID_ESPERADO = "b3bdeec5-ef1d-4461-8adc-344ccd230512"
PID_MX = 3850003733258937


def _sonda():
    spec = importlib.util.spec_from_file_location("sonda_s0", SONDA_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _respuesta_reporte(sonda) -> str:
    """Cuerpo con la forma del objeto reporte de la API v3: configuracion
    repetida + campos estandar (status/failureReason/url/createTime/...) y el
    reportId AL FINAL, como llego en la corrida real (targets > 600 chars)."""
    hoy = dt.date(2026, 10, 21)
    ini, fin = hoy - dt.timedelta(days=31), hoy - dt.timedelta(days=1)
    cuerpo = sonda.cuerpo_de(sonda.CFG_NOMBRE["targets"], PID_MX, ini, fin)
    return json.dumps(
        {
            "configuration": cuerpo["configuration"],
            "name": cuerpo["name"],
            "startDate": cuerpo["startDate"],
            "endDate": cuerpo["endDate"],
            "status": "PENDING",
            "failureReason": None,
            "url": None,
            "createTime": 1777005960000,
            "lastUpdatedTime": 1777005960000,
            "reportId": RID_ESPERADO,
        },
        separators=(",", ":"),
    )


@pytest.fixture()
def sonda_con_mock():
    sonda = _sonda()
    cuerpo = _respuesta_reporte(sonda)

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-falso", "expires_in": 3600})
        return httpx.Response(200, content=cuerpo)

    credenciales = AdsCredentials(
        client_id="cid-falso", client_secret="sec-falso", refresh_token="rt-falso"
    )
    # Un solo transport para ambos clientes (LWA del AdsClient y POST de la
    # sonda): el handler despacha por URL.
    transport = httpx.MockTransport(handler)
    cliente = AdsClient(credenciales, transport=transport)
    return sonda, sonda.Sonda(cliente, transport=transport), cuerpo


def test_post_parsea_report_id_del_cuerpo_completo(sonda_con_mock):
    """El reportId cae despues de los 600 chars que se loguean: post() debe
    devolverlo ya parseado del cuerpo completo (regresion de la corrida
    2026-10-21, donde targets quedo id=None con HTTP 200)."""
    modulo, sonda, cuerpo = sonda_con_mock
    assert '"reportId"' not in cuerpo[:600], "precondicion: reportId fuera del trozo logueado"

    cod, txt, _, rid = sonda.post(PID_MX, {"name": "prueba"})
    assert cod == 200
    assert rid == RID_ESPERADO
    assert modulo.rid_de(txt) == RID_ESPERADO


def test_modulo_importable_sin_ejecutar_la_sonda():
    """El guard de __main__ permite importar la sonda (este test) sin que
    arranque la corrida ni toque la red."""
    sonda = _sonda()
    try:
        assert callable(sonda.main)
    finally:
        sys.modules.pop("sonda_s0", None)
