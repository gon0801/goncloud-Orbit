"""Revision sub-B: el camino CLI de product ads (archivar-anuncios /
reponer-anuncios -> app/ads/archivar.py) no deja intencion durable.

1. Archivar es irreversible y la UNICA reversa es la linea `reversa` que
   `archivar_anuncios` devuelve AL FINAL. Si la relectura posterior falla
   (5xx sostenido del list), la excepcion sube y la linea se pierde: el
   anuncio ya quedo ARCHIVED y no hay ledger ni salida con que recrearlo.
2. `reponer_anuncios` no lee si ya hay un anuncio vivo del mismo sku en el
   ad group (retiro_anuncios.recrea si lo hace): repetir la corrida crea
   otro anuncio.
"""

from __future__ import annotations

import json

import httpx
from test_ads_write import _token_response, make_write_client

from app.ads.archivar import archivar_anuncios, reponer_anuncios
from app.ads.client import AdsApiError

PATH_LIST = "/sp/productAds/list"
PATH_DELETE = "/sp/productAds/delete"


def test_relectura_caida_tras_archivar_pierde_la_linea_de_reversa():
    deletes: list[str] = []
    lists = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(PATH_DELETE):
            deletes.append(cuerpo["adIdFilter"]["include"][0])
            return httpx.Response(207, json={"productAds": {"success": [{"adId": "1"}]}})
        if request.url.path.endswith(PATH_LIST):
            lists["n"] += 1
            if lists["n"] == 1:  # lectura previa: vivo, con datos de reversa
                return httpx.Response(
                    200,
                    json={
                        "productAds": [
                            {
                                "adId": "1",
                                "state": "ENABLED",
                                "adGroupId": "77",
                                "campaignId": "55",
                                "sku": "SKU-1",
                            }
                        ]
                    },
                )
            return httpx.Response(503, json={"message": "caido"})  # relectura
        return httpx.Response(500)

    resultados = None
    try:
        resultados = archivar_anuncios(
            make_write_client(handler),
            ["1"],
            ejecutar=True,
            avisar=lambda _m: None,
            dormir=lambda _s: None,
        )
    except AdsApiError:
        pass
    assert deletes == ["1"], "el archivado SI salio a Amazon"
    assert resultados is not None, (
        "archivo en Amazon (irreversible) y la linea de reversa se perdio: "
        "la relectura caida tumbo la funcion antes de devolver nada"
    )


def test_reponer_dos_veces_crea_dos_anuncios():
    creados: list[dict] = []
    vivos: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        path = request.url.path
        if path.endswith(PATH_LIST):
            pedidos = cuerpo.get("adIdFilter", {}).get("include")
            filas = [v for v in vivos if pedidos is None or v["adId"] in pedidos]
            return httpx.Response(200, json={"productAds": filas})
        if path.rstrip("/").endswith("/sp/productAds"):
            creados.append(cuerpo)
            nuevo = str(900 + len(creados))
            vivos.append(
                {"adId": nuevo, "state": "PAUSED", "adGroupId": "77", "sku": "SKU-1"}
            )
            return httpx.Response(207, json={"productAds": {"success": [{"adId": nuevo}]}})
        return httpx.Response(500)

    escritor = make_write_client(handler)
    linea = "adGroupId=77 campaignId=55 sku=SKU-1 state=PAUSED"
    for _ in range(2):  # el operador repite la corrida (p.ej. tras un sin_confirmar)
        reponer_anuncios(
            escritor, [linea], ejecutar=True, avisar=lambda _m: None, dormir=lambda _s: None
        )
    assert len(creados) == 1, (
        f"reponer no mira si ya hay un anuncio vivo del sku en el ad group: "
        f"{len(creados)} creates, vivos={[v['adId'] for v in vivos]}"
    )
