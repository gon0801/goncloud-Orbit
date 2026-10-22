"""Tests del cliente unico SP-API (SP-API 01 A.1, D5).

Solo unitarios con httpx.MockTransport: cero red, cero DB, cero secretos.
El cliente canonico vive en app.spapi.client; la sonda lo reutiliza.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.redaction import scrub
from app.spapi.client import (
    MERCADOS,
    RUTA_ORDERS_NUEVA,
    CuboTasa,
    SpapiClient,
    SpapiNoPermitida,
    construir_ruta_catalogo,
    construir_ruta_fees,
    construir_ruta_ofertas,
    rate_limit_de,
    sanear,
    siguiente_token,
    tasa_anunciada,
    validar_get,
    validar_post_fees,
)

CRED = {
    "lwa_app_id": "id-spapi",
    "lwa_client_secret": "secreto-spapi",
    "refresh_token": "refresh-spapi",
}
TOKEN = "token-spapi-ABC-123"


def _reloj():
    ahora = {"v": 1000.0}
    return lambda: ahora["v"]


def _cliente(handler, *, llamadas=None, dormidas=None):
    if llamadas is not None:
        original = handler

        def envoltorio(request: httpx.Request) -> httpx.Response:
            llamadas.append(request)
            return original(request)

        handler = envoltorio
    return SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=(dormidas.append if dormidas is not None else lambda _s: None),
        clock=_reloj(),
    )


def _token_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})


def test_allowlist_get_fijo_y_plantillas_sin_http():
    # Rutas validas pasan el guard puro sin emitir red (cero HTTP).
    assert validar_get("/sellers/v1/marketplaceParticipations")
    assert validar_get(RUTA_ORDERS_NUEVA)
    assert validar_get(construir_ruta_ofertas("B0849JWYD8"))
    assert validar_get(construir_ruta_catalogo("B0849JWYD8"))


def test_camino_de_lectura_sale_como_GET():
    """A.R/H2: el eje "cero escrituras a Amazon" vivia SOLO en el codigo.

    Mutante M15 del informe A.R (`client.get` -> `client.post` en
    client.py): 57 tests pasaban igual. Ningun test afirmaba el VERBO del
    camino de lectura; el unico assert de metodo era el POST de
    post_fees. Esto lo pinea: todo lo que sale hacia SP-API es GET, y a
    LWA solo el POST del token.
    """
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(200, json={"payload": {}})

    cliente = _cliente(handler, llamadas=llamadas)
    cliente.get("/sellers/v1/marketplaceParticipations")
    cliente.get(RUTA_ORDERS_NUEVA, params={"MarketplaceIds": "A1AM78C64UM0Y8"})
    cliente.get(construir_ruta_ofertas("B0849JWYD8"))

    spapi = [r for r in llamadas if r.url.host != "api.amazon.com"]
    assert len(spapi) == 3
    assert [r.method for r in spapi] == ["GET", "GET", "GET"]
    # LWA: el unico POST legitimo del cliente es el refresh del token.
    lwa = [r for r in llamadas if r.url.host == "api.amazon.com"]
    assert {r.method for r in lwa} == {"POST"}


def test_allowlist_rechaza_antes_de_red():
    llamadas: list = []
    cliente = _cliente(_token_ok, llamadas=llamadas)
    with pytest.raises(SpapiNoPermitida):
        cliente.get("https://evil.example.com/sellers/v1/account")
    with pytest.raises(SpapiNoPermitida):
        cliente.get("/finances/2024-06-19/transactions")
    with pytest.raises(SpapiNoPermitida):
        cliente.get("/orders/v0/../orders")
    with pytest.raises(SpapiNoPermitida):
        cliente.get("/listings/2021-08-01/items/ASELLER1/a%2Fb")
    with pytest.raises(SpapiNoPermitida):
        cliente.get("/catalog/2022-04-01/items/corto")
    assert llamadas == []


def test_post_fees_allowlist_y_unico_post():
    assert construir_ruta_fees("SKU-1") == "/products/fees/v0/listings/SKU-1/feesEstimate"
    assert validar_post_fees(construir_ruta_fees("SKU con espacio"), "SKU con espacio")
    with pytest.raises(SpapiNoPermitida):
        validar_post_fees("/products/fees/v0/feesEstimate", "SKU-1")
    with pytest.raises(SpapiNoPermitida):
        validar_post_fees(construir_ruta_fees("SKU-1"), "OTRO")
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        assert request.method == "POST"
        assert request.headers["x-amz-access-token"] == TOKEN
        return httpx.Response(200, json={"payload": {}})

    cliente = _cliente(handler, llamadas=llamadas)
    resp = cliente.post_fees("SKU-1", b"{}")
    assert resp.status_code == 200
    posts_lwa = [r for r in llamadas if r.url.host == "api.amazon.com"]
    assert len(posts_lwa) == 1


def test_401_un_refresh_forzado_recupera():
    lwa = {"n": 0}
    api = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            lwa["n"] += 1
            return _token_ok(request)
        api["n"] += 1
        if api["n"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"payload": {}})

    cliente = _cliente(handler)
    resp = cliente.get("/sellers/v1/marketplaceParticipations")
    assert resp.status_code == 200
    assert lwa["n"] == 2
    assert api["n"] == 2


def test_401_persistente_no_reintenta_de_mas():
    lwa = {"n": 0}
    api = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            lwa["n"] += 1
            return _token_ok(request)
        api["n"] += 1
        return httpx.Response(401, json={})

    cliente = _cliente(handler)
    resp = cliente.get("/sellers/v1/marketplaceParticipations")
    assert resp.status_code == 401
    assert lwa["n"] == 2
    assert api["n"] == 2


def _handler_429(codigos, *, retry_after=None):
    """429 las veces de `codigos` y luego 200; con `codigos=None`, siempre 429."""
    cuenta = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        cuenta["n"] += 1
        if codigos is None or cuenta["n"] <= codigos:
            cabeceras = {"Retry-After": retry_after} if retry_after else {}
            return httpx.Response(429, headers=cabeceras, json={})
        return httpx.Response(200, json={"payload": {}})

    return handler, cuenta


def test_429_tres_y_luego_200_espera_2_4_8():
    dormidas: list = []
    handler, cuenta = _handler_429(3)
    cliente = _cliente(handler, dormidas=dormidas)
    assert cliente.get("/sellers/v1/account").status_code == 200
    assert dormidas == [2.0, 4.0, 8.0]
    assert cuenta["n"] == 4


def test_429_retry_after_manda_sobre_espera_fija():
    dormidas: list = []
    handler, _ = _handler_429(2, retry_after="30")
    cliente = _cliente(handler, dormidas=dormidas)
    assert cliente.get("/sellers/v1/account").status_code == 200
    assert dormidas == [30.0, 30.0]


def test_429_que_nunca_cesa_devuelve_429_tras_6_intentos():
    dormidas: list = []
    handler, cuenta = _handler_429(None)
    cliente = _cliente(handler, dormidas=dormidas)
    assert cliente.get("/sellers/v1/account").status_code == 429
    assert cuenta["n"] == 6
    assert dormidas == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert sum(dormidas) <= 120

    # Con Retry-After grande el tope de 120 s corta antes de los 6 intentos.
    dormidas2: list = []
    handler2, cuenta2 = _handler_429(None, retry_after="50")
    cliente2 = _cliente(handler2, dormidas=dormidas2)
    assert cliente2.get("/sellers/v1/account").status_code == 429
    assert dormidas2 == [50.0, 50.0]
    assert sum(dormidas2) <= 120
    assert cuenta2["n"] == 3


def test_post_fees_429_reintenta_hasta_5():
    dormidas: list = []
    handler, cuenta = _handler_429(None)
    cliente = _cliente(handler, dormidas=dormidas)
    assert cliente.post_fees("SKU-1", b"{}").status_code == 429
    assert cuenta["n"] == 6
    assert dormidas == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_429_deja_linea_en_log_sin_headers_ni_cuerpo(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(429, headers={"Retry-After": "7", "x-secreto": "SHH"}, text="CUERPO")

    cliente = _cliente(handler)
    with caplog.at_level("WARNING", logger="app.spapi.client"):
        cliente.get("/sellers/v1/account", params={"MarketplaceIds": "PARAMX"})
    lineas = [r.getMessage() for r in caplog.records if "429" in r.getMessage()]
    assert len(lineas) == 6
    assert "/sellers/v1/account" in lineas[0]
    assert "intento=1" in lineas[0] and "espera=7.0s" in lineas[0]
    assert "Retry-After=7s" in lineas[0]
    texto = " ".join(lineas)
    assert "PARAMX" not in texto and "SHH" not in texto and "CUERPO" not in texto
    assert "espera=0.0s" in lineas[-1]


def test_429_sin_retry_after_lo_dice_en_el_log(caplog):
    handler, _ = _handler_429(1)
    cliente = _cliente(handler)
    with caplog.at_level("WARNING", logger="app.spapi.client"):
        cliente.get("/sellers/v1/account")
    lineas = [r.getMessage() for r in caplog.records if "429" in r.getMessage()]
    assert len(lineas) == 1
    assert "sin Retry-After" in lineas[0] and "espera=2.0s" in lineas[0]


def test_reintento_429_consume_cuota():
    # Revision #5: el reintento interno tambien pasa por el cubo; a 0.5/s
    # el segundo intento espera 2 s del cubo ademas de los 2 s fijos del 429.
    dormidas: list = []
    reloj = _reloj()
    n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        n["n"] += 1
        if n["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"payload": {}})

    cubo = CuboTasa(sleep=dormidas.append, clock=reloj, capacidad=1, tasa=0.5)
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=dormidas.append,
        clock=reloj,
    )
    assert cliente.get("/sellers/v1/account", limitador=cubo).status_code == 200
    assert dormidas == [pytest.approx(2.0), pytest.approx(2.0)]


def test_header_ratelimit_ajusta_tasa():
    # Revision #5 (contrato del brief): el limitador parte de
    # x-amzn-RateLimit-Limit; con tasa 2 el siguiente consumo espera 0.5 s.
    dormidas: list = []
    reloj = _reloj()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(200, headers={"x-amzn-RateLimit-Limit": "2"}, json={"payload": {}})

    cubo = CuboTasa(sleep=dormidas.append, clock=reloj, capacidad=1, tasa=0.5)
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=dormidas.append,
        clock=reloj,
    )
    assert cliente.get("/sellers/v1/account", limitador=cubo).status_code == 200
    cubo.consumir()
    assert dormidas == [pytest.approx(0.5)]


def test_tasa_anunciada_ilegible_se_ignora():
    assert tasa_anunciada({}) is None
    for mala in ("abc", "0", "-2", "nan", "inf"):
        assert tasa_anunciada({"x-amzn-RateLimit-Limit": mala}) is None
    assert tasa_anunciada({"X-Amzn-RateLimit-Limit": "2"}) == pytest.approx(2.0)
    dormidas: list = []
    cubo = CuboTasa(sleep=dormidas.append, clock=_reloj(), capacidad=1, tasa=0.5)
    cubo.fijar_tasa("abc")
    cubo.fijar_tasa(0)
    cubo.fijar_tasa(True)
    cubo.consumir()
    cubo.consumir()
    assert dormidas == [pytest.approx(2.0)]


def test_5xx_y_red_sin_retry():
    llamadas: list = []

    def handler_500(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(500, json={})

    cliente = _cliente(handler_500, llamadas=llamadas)
    assert cliente.get("/sellers/v1/account").status_code == 500
    assert len([r for r in llamadas if r.url.host != "api.amazon.com"]) == 1

    def handler_red(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        raise httpx.ConnectError("corte")

    cliente2 = _cliente(handler_red)
    with pytest.raises(httpx.HTTPError):
        cliente2.get("/sellers/v1/account")


def test_scrub_en_errores_y_rate_limit_y_paginacion():
    from app.redaction import register_secret, scrub

    register_secret("secreto-spapi-sensible")
    err = None
    try:
        raise SpapiNoPermitida("fallo con secreto-spapi-sensible dentro")
    except SpapiNoPermitida as exc:
        err = exc
    assert err is not None
    assert "secreto-spapi-sensible" not in str(err)
    assert "secreto-spapi-sensible" not in scrub("eco secreto-spapi-sensible fin")
    assert rate_limit_de({"X-Amzn-RateLimit-Limit": "0.5", "otro": "1"}) == {
        "x-amzn-ratelimit-limit": "0.5"
    }
    assert siguiente_token({"pagination": {"nextToken": "T"}}) == "T"
    assert siguiente_token({"payload": {"NextToken": "N"}}) == "N"
    assert sanear({"BuyerEmail": "b@x.mx", "Asin": "B1"}, "orders") == {"Asin": "B1"}


def test_sonda_reutiliza_cliente_unico(monkeypatch):
    from pathlib import Path as _P

    import app.spapi.client as canon

    monkeypatch.syspath_prepend(str(_P(__file__).resolve().parent.parent / "tools"))
    import sonda_spapi as sonda

    assert issubclass(sonda.SondaClient, SpapiClient)
    assert sonda.SondaError is canon.SpapiError
    assert sonda.SondaNoPermitida is canon.SpapiNoPermitida
    assert sonda.MERCADOS == MERCADOS
    assert sonda.siguiente_token({"pagination": {"nextToken": "T"}}) == "T"


def test_un_solo_refresh_con_dos_modulos_en_mismo_proceso(tmp_path, monkeypatch):
    """Fees + fotos comparten un SpapiClient: un solo POST a LWA."""
    from app.estimacion_fees import ProductFeesClient, cotizar_oferta
    from app.estimacion_insumos import (
        FilaOfertaBridge,
        construir_canonical_input,
        construir_context_fingerprint,
        construir_source_event_id,
    )
    from app.publicacion_fotos import FotosPublicacion

    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))
    (tmp_path / "amazon_credentials.json").write_text(
        json.dumps(
            {
                "lwa_app_id": "cliente-prueba",
                "lwa_client_secret": "secreto-prueba",
                "refresh_token": "refresh-prueba",
            }
        )
    )
    lwa = {"n": 0}
    fetched = datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC)
    ahora = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    tiempo = {"v": 5000.0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            lwa["n"] += 1
            return httpx.Response(200, json={"access_token": "tk-compartido", "expires_in": 3600})
        path = request.url.path
        if path.endswith("/feesEstimate"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "FeesEstimateResult": {
                            "Status": "Success",
                            "FeesEstimateIdentifier": {
                                "MarketplaceId": "A1AM78C64UM0Y8",
                                "IdType": "SellerSKU",
                                "IdValue": "SS-MX-1",
                                "SellerInputIdentifier": ident,
                                "IsAmazonFulfilled": True,
                                "PriceToEstimateFees": {
                                    "ListingPrice": {"CurrencyCode": "MXN", "Amount": 116.0}
                                },
                            },
                            "FeesEstimate": {
                                "TimeOfFeesEstimation": (ahora - timedelta(minutes=30)).isoformat(),
                                "TotalFeesEstimate": {"CurrencyCode": "MXN", "Amount": 15.0},
                                "FeeDetailList": [
                                    {
                                        "FeeType": "FBAFees",
                                        "FeeAmount": {"CurrencyCode": "MXN", "Amount": 10.0},
                                        "FinalFee": {"CurrencyCode": "MXN", "Amount": 10.0},
                                    },
                                    {
                                        "FeeType": "ReferralFee",
                                        "FeeAmount": {"CurrencyCode": "MXN", "Amount": 5.0},
                                        "FinalFee": {"CurrencyCode": "MXN", "Amount": 5.0},
                                    },
                                ],
                            },
                        }
                    }
                },
            )
        if "/catalog/2022-04-01/items/" in path:
            return httpx.Response(
                200,
                json={
                    "asin": "B0849JWYD8",
                    "images": [
                        {
                            "marketplaceId": "A1AM78C64UM0Y8",
                            "images": [
                                {
                                    "variant": "MAIN",
                                    "link": "https://m.media-amazon.com/images/I/prueba.jpg",
                                    "width": 500,
                                    "height": 500,
                                }
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(
            200, content=b"\xff\xd8\xfffoto", headers={"Content-Type": "image/jpeg"}
        )

    transport = httpx.MockTransport(handler)
    reloj = lambda: tiempo["v"]  # noqa: E731
    compartido = SpapiClient(
        credentials={
            "lwa_app_id": "cliente-prueba",
            "lwa_client_secret": "secreto-prueba",
            "refresh_token": "refresh-prueba",
        },
        transport=transport,
        sleep=lambda s: None,
        clock=reloj,
    )
    fila = FilaOfertaBridge(
        seller_sku="SS-MX-1",
        asin="B0EST01",
        marketplace_id="A1AM78C64UM0Y8",
        marketplace_name="amazon_mx",
        price=116.0,
        fulfillment_channel="AMAZON_NA",
        fetched_at=fetched.isoformat(),
    )
    precio = Decimal("116.0000")
    canon = construir_canonical_input(fila, "amazon_mx", "fba", precio, "MXN")
    from app.estimacion_insumos import OfertaResuelta

    oferta = OfertaResuelta(
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        canal="fba",
        price_amount=precio,
        price_currency="MXN",
        fetched_at=fetched,
        canonical_input=canon,
        context_fingerprint=construir_context_fingerprint("fba", precio, "MXN", fetched),
        source_event_id=construir_source_event_id(canon),
    )
    ident = oferta.source_event_id
    fees = ProductFeesClient(
        credentials={
            "lwa_app_id": "cliente-prueba",
            "lwa_client_secret": "secreto-prueba",
            "refresh_token": "refresh-prueba",
        },
        transport=transport,
        sleep=lambda s: None,
        clock=reloj,
        spapi_client=compartido,
    )
    fotos = FotosPublicacion(transport=transport, spapi_client=compartido)
    resultado = cotizar_oferta(fees, oferta, observed_at=ahora)
    assert resultado.estado == "success"
    assert fotos.obtener("amazon_mx", "B0849JWYD8") == (b"\xff\xd8\xfffoto", "image/jpeg")
    assert lwa["n"] == 1
    assert compartido.refreshes == 1
    assert "tk-compartido" not in scrub("eco tk-compartido fin")


def test_rechazo_lwa_es_error_tipado_con_status():
    import pytest as _pt

    from app.spapi.client import SpapiAuthError, SpapiRechazoLWA

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=_reloj(),
    )
    with _pt.raises(SpapiRechazoLWA) as exc:
        cliente._acceso()
    assert exc.value.status == 403
    assert isinstance(exc.value, SpapiAuthError)


def test_cliente_compartido_una_instancia_por_credenciales():
    import app.spapi.client as canon

    canon._COMPARTIDOS.clear()
    try:
        a = canon.cliente_compartido(credentials=dict(CRED))
        b = canon.cliente_compartido(credentials=dict(CRED))
        assert a is b
        otras = dict(CRED, lwa_app_id="otro-id")
        assert canon.cliente_compartido(credentials=otras) is not a
    finally:
        canon._COMPARTIDOS.clear()


def test_fees_usa_compartido_con_defaults_produccion():
    import app.spapi.client as canon
    from app.estimacion_fees import ProductFeesClient

    canon._COMPARTIDOS.clear()
    try:
        uno = ProductFeesClient(credentials=dict(CRED))
        dos = ProductFeesClient(credentials=dict(CRED))
        assert uno._spapi is dos._spapi
        mock = ProductFeesClient(
            credentials=dict(CRED),
            transport=httpx.MockTransport(_token_ok),
            sleep=lambda _s: None,
            clock=_reloj(),
        )
        assert mock._spapi is not uno._spapi
    finally:
        canon._COMPARTIDOS.clear()


def test_refresh_forzado_coordinado_un_solo_post():
    """F2: dos 401 con el mismo token viejo producen un solo POST a LWA."""
    lwa = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            lwa["n"] += 1
            return httpx.Response(
                200,
                json={"access_token": f"tk-spapi-client-fixture-{lwa['n']}", "expires_in": 3600},
            )
        return httpx.Response(200, json={"payload": {}})

    cliente = _cliente(handler)
    viejo = cliente._acceso()
    assert viejo == "tk-spapi-client-fixture-1"
    nuevo = cliente._acceso(forzar=True, rechazado=viejo)
    assert nuevo == "tk-spapi-client-fixture-2"
    # El segundo 401 llega con el mismo token viejo cuando el nuevo ya esta
    # instalado: reusar sin POST.
    assert cliente._acceso(forzar=True, rechazado=viejo) == "tk-spapi-client-fixture-2"
    assert lwa["n"] == 2


def test_fees_403_invalida_token_y_fotos_refresca():
    """F1: 403 en fees invalida el compartido; fotos hace POST nuevo a LWA."""
    from app.estimacion_fees import ProductFeesClient, cotizar_oferta
    from app.estimacion_insumos import (
        FilaOfertaBridge,
        OfertaResuelta,
        construir_canonical_input,
        construir_context_fingerprint,
        construir_source_event_id,
    )
    from app.publicacion_fotos import FotosPublicacion

    lwa = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            lwa["n"] += 1
            return httpx.Response(
                200,
                json={"access_token": f"tk-spapi-f1-fixture-{lwa['n']}", "expires_in": 3600},
            )
        if request.url.path.endswith("/feesEstimate"):
            return httpx.Response(403, json={})
        if "/catalog/2022-04-01/items/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "asin": "B0849JWYD8",
                    "images": [
                        {
                            "marketplaceId": "A1AM78C64UM0Y8",
                            "images": [
                                {
                                    "variant": "MAIN",
                                    "link": "https://m.media-amazon.com/images/I/prueba.jpg",
                                    "width": 500,
                                    "height": 500,
                                }
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(
            200, content=b"\xff\xd8\xfffoto", headers={"Content-Type": "image/jpeg"}
        )

    transport = httpx.MockTransport(handler)
    reloj = _reloj()
    creds = {
        "lwa_app_id": "id-f1",
        "lwa_client_secret": "secreto-f1",
        "refresh_token": "refresh-f1",
    }
    compartido_f1 = SpapiClient(
        credentials=creds,
        transport=transport,
        sleep=lambda _s: None,
        clock=reloj,
    )
    fetched = datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC)
    ahora = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    fila = FilaOfertaBridge(
        seller_sku="SS-MX-1",
        asin="B0EST01",
        marketplace_id="A1AM78C64UM0Y8",
        marketplace_name="amazon_mx",
        price=116.0,
        fulfillment_channel="AMAZON_NA",
        fetched_at=fetched.isoformat(),
    )
    precio = Decimal("116.0000")
    canon = construir_canonical_input(fila, "amazon_mx", "fba", precio, "MXN")
    oferta = OfertaResuelta(
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SS-MX-1",
        asin="B0EST01",
        canal="fba",
        price_amount=precio,
        price_currency="MXN",
        fetched_at=fetched,
        canonical_input=canon,
        context_fingerprint=construir_context_fingerprint("fba", precio, "MXN", fetched),
        source_event_id=construir_source_event_id(canon),
    )
    fees = ProductFeesClient(
        credentials=dict(creds),
        transport=transport,
        sleep=lambda _s: None,
        clock=reloj,
        spapi_client=compartido_f1,
    )
    fotos = FotosPublicacion(transport=transport, spapi_client=compartido_f1)
    resultado = cotizar_oferta(fees, oferta, observed_at=ahora)
    assert resultado.estado == "error"
    assert resultado.error_code == "fee_http_403"
    assert lwa["n"] == 1
    # El token quedo invalidado: fotos refresca (POST 2) y sale con T2.
    assert fotos.obtener("amazon_mx", "B0849JWYD8") == (b"\xff\xd8\xfffoto", "image/jpeg")
    assert lwa["n"] == 2
