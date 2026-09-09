"""Tests de tools/sonda_spapi.py (SP-API 01 Fase 0: sondas read-only).

Solo unitarios con httpx.MockTransport: cero red real, cero DB, cero secretos.
Patron de import de tools/ (sin __init__): sys.path + import directo,
igual que tests/test_fabrica_campanas.py.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import sonda_spapi as sonda  # noqa: E402

CRED = {
    "lwa_app_id": "id-sonda",
    "lwa_client_secret": "secreto-sonda",
    "refresh_token": "refresh-sonda",
}
TOKEN = "token-sonda-ABC-123"


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
    return sonda.SondaClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=(dormidas.append if dormidas is not None else lambda _s: None),
        clock=_reloj(),
    )


def _token_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})


def _lineas_json(salida: str) -> list[dict]:
    return [json.loads(linea) for linea in salida.splitlines() if linea.strip()]


# ---------------------------------------------------------------------------
# Allowlist default-deny: falla antes de red (cero HTTP emitido)
# ---------------------------------------------------------------------------


def test_allowlist_rechaza_host_ajeno_sin_http():
    llamadas: list = []
    cliente = _cliente(_token_ok, llamadas=llamadas)
    with pytest.raises(sonda.SondaNoPermitida):
        cliente.get("https://evil.example.com/sellers/v1/account")
    assert llamadas == []


def test_allowlist_rechaza_path_no_listado_sin_http():
    llamadas: list = []
    cliente = _cliente(_token_ok, llamadas=llamadas)
    with pytest.raises(sonda.SondaNoPermitida):
        cliente.get("/orders/v0/otro")
    with pytest.raises(sonda.SondaNoPermitida):
        cliente.get("/finances/2024-06-19/transactions")
    assert llamadas == []


def test_allowlist_rechaza_traversal_y_encoding_sin_http():
    # Entradas que SOLO esos guards rechazan: la plantilla calza y el
    # segmento es valido salvo el encoding/traversal. Sin el guard de %2f,
    # el primer GET pasaria la allowlist; sin el de traversal, los otros
    # caerian en el deny generico (el match lo delata).
    llamadas: list = []
    cliente = _cliente(_token_ok, llamadas=llamadas)
    with pytest.raises(sonda.SondaNoPermitida):
        cliente.get("/listings/2021-08-01/items/ASELLER1/a%2Fb")
    for path in ("/orders/v0/../orders", "/listings/2021-08-01/items/../x"):
        with pytest.raises(sonda.SondaNoPermitida, match="traversal"):
            cliente.get(path)
    with pytest.raises(sonda.SondaNoPermitida):
        sonda.construir_ruta_ofertas("corto")
    assert llamadas == []


def test_pricing_exige_asin_valido_antes_de_red():
    llamadas: list = []
    cliente = _cliente(_token_ok, llamadas=llamadas)
    with pytest.raises(sonda.SondaError):
        sonda.sondear_pricing(cliente, "amazon_mx", asin="no-es-asin")
    assert llamadas == []


# ---------------------------------------------------------------------------
# Redaccion: el token jamas aparece en la salida
# ---------------------------------------------------------------------------


def test_token_redactado_en_salida():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        if request.url.path == "/sellers/v1/account":
            return httpx.Response(200, json={"sellerId": "AXSELLER1"})
        return httpx.Response(200, json={"payload": []})

    cliente = _cliente(handler)
    buf = io.StringIO()
    with redirect_stdout(buf):
        sonda.sondear_sellers(cliente, "amazon_mx")
    salida = buf.getvalue()
    assert TOKEN not in salida
    assert "refresh-sonda" not in salida
    assert "secreto-sonda" not in salida


# ---------------------------------------------------------------------------
# Paginacion: los dos bugs conocidos paran y se marcan
# ---------------------------------------------------------------------------


def _handler_orders(paginas: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        token = request.url.params.get("NextToken")
        indice = (
            0
            if token is None
            else next(
                (i for i, p in enumerate(paginas) if p.get("token_entrada") == token),
                0,
            )
        )
        return httpx.Response(200, json=paginas[indice]["cuerpo"])

    return handler


def test_paginacion_token_repetido_para_y_marca():
    paginas = [
        {"cuerpo": {"payload": {"Orders": [{"AmazonOrderId": "1-1"}], "NextToken": "T"}}},
        {
            "token_entrada": "T",
            "cuerpo": {"payload": {"Orders": [{"AmazonOrderId": "1-2"}], "NextToken": "T"}},
        },
    ]
    llamadas: list = []
    cliente = _cliente(_handler_orders(paginas), llamadas=llamadas)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_orders_v0(cliente, "amazon_mx", dias=7, max_paginas=3)
    gets_orders = [r for r in llamadas if r.url.path == "/orders/v0/orders"]
    assert len(gets_orders) == 2
    assert resumen["paginas"] == 2
    assert resumen["conteo_total"] == 2
    assert resumen["aviso_paginacion"] == "next_token_repetido"


def test_paginacion_pagina_vacia_con_token_para_y_marca():
    paginas = [
        {"cuerpo": {"payload": {"Orders": [{"AmazonOrderId": "1-1"}], "NextToken": "T2"}}},
        {"token_entrada": "T2", "cuerpo": {"payload": {"Orders": [], "NextToken": "T3"}}},
    ]
    llamadas: list = []
    cliente = _cliente(_handler_orders(paginas), llamadas=llamadas)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_orders_v0(cliente, "amazon_mx", dias=7, max_paginas=3)
    gets_orders = [r for r in llamadas if r.url.path == "/orders/v0/orders"]
    assert len(gets_orders) == 2
    assert resumen["paginas"] == 2
    assert resumen["conteo_total"] == 1
    assert resumen["aviso_paginacion"] == "pagina_vacia_con_token"


# ---------------------------------------------------------------------------
# Orders sin PII: comprador y direccion jamas se imprimen
# ---------------------------------------------------------------------------


def test_orders_pii_filtrada_de_la_salida():
    cuerpo = {
        "payload": {
            "Orders": [
                {
                    "AmazonOrderId": "1-1",
                    "BuyerEmail": "buyer@example.com",
                    "BuyerInfo": {"BuyerEmail": "buyer@example.com"},
                    "ShippingAddress": {"AddressLine1": "Calle Falsa 123", "City": "CDMX"},
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(200, json=cuerpo)

    cliente = _cliente(handler)
    buf = io.StringIO()
    with redirect_stdout(buf):
        resumen = sonda.sondear_orders_v0(cliente, "amazon_mx", dias=7, max_paginas=3)
    salida = buf.getvalue()
    assert "buyer@example.com" not in salida
    assert "Calle Falsa 123" not in salida
    assert resumen["conteo_total"] == 1
    linea = next(d for d in _lineas_json(salida) if d.get("endpoint") == "/orders/v0/orders")
    assert not any("buyer" in clave.lower() for clave in linea["claves_top"])
    # claves_item sale del primer elemento YA saneado: sin sanear contiene
    # BuyerEmail/BuyerInfo/ShippingAddress y este assert falla.
    assert "claves_item" in linea
    assert not any(
        clave.lower().startswith("buyer") or clave == "ShippingAddress"
        for clave in linea["claves_item"]
    )


# ---------------------------------------------------------------------------
# Listings: el sellerId llega por parametro (account no lo trae en NA)
# ---------------------------------------------------------------------------


def test_listings_sin_seller_id_no_llama_al_item():
    llamadas: list = []
    cliente = _cliente(_token_ok, llamadas=llamadas)
    with redirect_stdout(io.StringIO()):
        resumen_none = sonda.sondear_listings(
            cliente, "amazon_mx", sku="GE-YXVC-R5BR", seller_id=None
        )
    with redirect_stdout(io.StringIO()):
        resumen_vacio = sonda.sondear_listings(
            cliente, "amazon_mx", sku="GE-YXVC-R5BR", seller_id=""
        )
    assert llamadas == []
    for resumen in (resumen_none, resumen_vacio):
        assert resumen["veredicto"] == "no_verificada"
        assert resumen["errores"] == ["sin_seller_id"]


def test_listings_con_seller_id_llama_directo_sin_account():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(200, json={"sku": "GE-YXVC-R5BR", "status": "ACTIVE"})

    llamadas: list = []
    cliente = _cliente(handler, llamadas=llamadas)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_listings(
            cliente, "amazon_mx", sku="GE-YXVC-R5BR", seller_id="AXSELLER1"
        )
    paths = [r.url.path for r in llamadas if r.url.host != "api.amazon.com"]
    assert paths == ["/listings/2021-08-01/items/AXSELLER1/GE-YXVC-R5BR"]
    assert resumen["veredicto"] == "verificada"


# ---------------------------------------------------------------------------
# Rate limit y avisos viajan en la linea JSON
# ---------------------------------------------------------------------------


def test_rate_limit_capturado_en_linea():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        headers = {
            "x-amzn-RateLimit-Limit": "0.5",
            "x-amzn-RateLimit-Remaining": "0.4",
        }
        if request.url.path == "/sellers/v1/account":
            return httpx.Response(200, headers=headers, json={"sellerId": "AXSELLER1"})
        return httpx.Response(200, json={"payload": []})

    cliente = _cliente(handler)
    buf = io.StringIO()
    with redirect_stdout(buf):
        sonda.sondear_sellers(cliente, "amazon_mx")
    linea = next(
        d for d in _lineas_json(buf.getvalue()) if d.get("endpoint") == "/sellers/v1/account"
    )
    assert linea["rate_limit"].get("x-amzn-ratelimit-limit") == "0.5"
    assert linea["status"] == 200


# ---------------------------------------------------------------------------
# 401: un refresh forzado; 429: un reintento acotado; nunca loop
# ---------------------------------------------------------------------------


def test_401_un_refresh_forzado_recupera():
    token_llamadas = {"n": 0}
    account_llamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_llamadas["n"] += 1
            return _token_ok(request)
        if request.url.path == "/sellers/v1/account":
            account_llamadas["n"] += 1
            if account_llamadas["n"] == 1:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"sellerId": "AXSELLER1"})
        return httpx.Response(200, json={"payload": []})

    cliente = _cliente(handler)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_sellers(cliente, "amazon_mx")
    assert token_llamadas["n"] == 2
    assert account_llamadas["n"] == 2
    assert resumen["veredicto"] == "verificada"


def test_401_persistente_no_reintenta_de_mas():
    # Sellers prueba dos endpoints; cada uno tiene su propio tope (1 envio
    # + 1 refresh forzado + 1 reintento). El refresh inicial es compartido.
    token_llamadas = {"n": 0}
    por_endpoint: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_llamadas["n"] += 1
            return _token_ok(request)
        por_endpoint[request.url.path] = por_endpoint.get(request.url.path, 0) + 1
        return httpx.Response(401, json={})

    cliente = _cliente(handler)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_sellers(cliente, "amazon_mx")
    assert token_llamadas["n"] == 3
    assert por_endpoint == {
        "/sellers/v1/account": 2,
        "/sellers/v1/marketplaceParticipations": 2,
    }
    assert resumen["veredicto"] == "no_verificada"


def test_429_un_reintento_acotado():
    gets = {"n": 0}
    dormidas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        if request.url.path == "/sellers/v1/account":
            gets["n"] += 1
            if gets["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={})
            return httpx.Response(200, json={"sellerId": "AXSELLER1"})
        return httpx.Response(200, json={"payload": []})

    cliente = _cliente(handler, dormidas=dormidas)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_sellers(cliente, "amazon_mx")
    assert gets["n"] == 2
    assert dormidas and all(0 <= s <= 60 for s in dormidas)
    assert resumen["veredicto"] == "verificada"


def test_429_persistente_no_hace_loop():
    # Un reintento acotado por endpoint: 2 envios por endpoint, nunca loop.
    por_endpoint: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        por_endpoint[request.url.path] = por_endpoint.get(request.url.path, 0) + 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    cliente = _cliente(handler)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_sellers(cliente, "amazon_mx")
    assert por_endpoint == {
        "/sellers/v1/account": 2,
        "/sellers/v1/marketplaceParticipations": 2,
    }
    assert resumen["veredicto"] == "no_verificada"


# ---------------------------------------------------------------------------
# Sellers: participations primaria, account 4xx declarado sin invalidar
# ---------------------------------------------------------------------------


def test_sellers_account_4xx_se_declara_sin_invalidar():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        if request.url.path == "/sellers/v1/marketplaceParticipations":
            return httpx.Response(200, json={"payload": [{"marketplaceId": "A1AM78C64UM0Y8"}]})
        return httpx.Response(404, json={})

    cliente = _cliente(handler)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_sellers(cliente, "amazon_mx")
    assert resumen["veredicto"] == "verificada"
    assert resumen["errores"] == []
    assert any("account" in nota for nota in resumen["notas"])


# ---------------------------------------------------------------------------
# Redaccion: errores con fragmento scrubbado (<=200)
# ---------------------------------------------------------------------------


def test_errores_con_fragmento_scrubbado():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        if request.url.path == "/sellers/v1/marketplaceParticipations":
            return httpx.Response(200, json={"payload": []})
        # Amazon ecoa el token en el cuerpo del 403.
        return httpx.Response(403, json={"message": f"token invalido: {TOKEN}"})

    cliente = _cliente(handler)
    buf = io.StringIO()
    with redirect_stdout(buf):
        resumen = sonda.sondear_sellers(cliente, "amazon_mx")
    salida = buf.getvalue()
    assert TOKEN not in salida
    linea = next(d for d in _lineas_json(salida) if d.get("endpoint") == "/sellers/v1/account")
    assert linea["status"] == 403
    fragmentos = [e for e in linea["errores"] if e.startswith("fragmento:")]
    # Sin scrub el fragmento trae el token y este assert falla.
    assert fragmentos and "***REDACTED***" in fragmentos[0]
    assert all(len(f) <= len("fragmento:") + 200 for f in fragmentos)
    assert resumen["veredicto"] == "verificada"


# ---------------------------------------------------------------------------
# Orders dual: v0 deprecada + 2026-01-01
# ---------------------------------------------------------------------------


def test_orders_v0_linea_trae_aviso_fijo_deprecada():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        return httpx.Response(200, json={"payload": {"Orders": []}})

    cliente = _cliente(handler)
    buf = io.StringIO()
    with redirect_stdout(buf):
        sonda.sondear_orders_v0(cliente, "amazon_mx", dias=7, max_paginas=3)
    linea = next(
        d for d in _lineas_json(buf.getvalue()) if d.get("endpoint") == "/orders/v0/orders"
    )
    assert linea["version"] == "v0"
    assert linea["aviso_deprecacion"] == "v0 deprecada; retiro 2027-03-27; sucesora 2026-01-01"


def test_orders_nueva_minusculas_sin_buyer_y_pagination_token():
    vistas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        vistas.append(request)
        if request.url.params.get("paginationToken") is None:
            cuerpo = {"orders": [{"orderId": "n-1"}], "pagination": {"nextToken": "P"}}
        else:
            assert request.url.params["paginationToken"] == "P"
            cuerpo = {"orders": [], "pagination": {"nextToken": "P2"}}
        return httpx.Response(200, json=cuerpo)

    cliente = _cliente(handler)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_orders_nueva(cliente, "amazon_mx", dias=7, max_paginas=3)
    assert len(vistas) == 2
    primera = vistas[0].url.params
    assert primera.get("marketplaceIds") == "A1AM78C64UM0Y8"
    assert "createdAfter" in primera
    assert "MarketplaceIds" not in primera and "CreatedAfter" not in primera
    assert all("includedData" not in v.url.params for v in vistas)
    assert "paginationToken" not in primera
    assert vistas[1].url.params.get("paginationToken") == "P"
    assert resumen["version"] == "2026-01-01"
    assert resumen["conteo_total"] == 1
    assert resumen["aviso_paginacion"] == "pagina_vacia_con_token"


def test_orders_nueva_token_repetido_para_y_marca():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        token = request.url.params.get("paginationToken")
        indice = "n-2" if token == "Q" else "n-1"
        cuerpo = {"orders": [{"orderId": indice}], "pagination": {"nextToken": "Q"}}
        return httpx.Response(200, json=cuerpo)

    llamadas: list = []
    cliente = _cliente(handler, llamadas=llamadas)
    with redirect_stdout(io.StringIO()):
        resumen = sonda.sondear_orders_nueva(cliente, "amazon_mx", dias=7, max_paginas=3)
    gets = [r for r in llamadas if r.url.path == "/orders/2026-01-01/orders"]
    assert len(gets) == 2
    assert resumen["conteo_total"] == 2
    assert resumen["aviso_paginacion"] == "next_token_repetido"


def test_orders_dual_corre_ambas_versiones():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_ok(request)
        if request.url.path == "/orders/v0/orders":
            return httpx.Response(200, json={"payload": {"Orders": [{"AmazonOrderId": "v0"}]}})
        if request.url.path == "/orders/2026-01-01/orders":
            return httpx.Response(200, json={"orders": [{"orderId": "n"}], "pagination": {}})
        raise AssertionError(f"endpoint inesperado: {request.url.path}")

    llamadas: list = []
    cliente = _cliente(handler, llamadas=llamadas)
    buf = io.StringIO()
    with redirect_stdout(buf):
        resumen = sonda.sondear_orders(cliente, "amazon_mx", dias=7, max_paginas=3)
    paths = {r.url.path for r in llamadas if r.url.host != "api.amazon.com"}
    assert paths == {"/orders/v0/orders", "/orders/2026-01-01/orders"}
    assert set(resumen["versiones"]) == {"v0", "2026-01-01"}
    assert resumen["conteo_total"] == 2
    assert resumen["veredicto"] == "verificada"
    versiones = {
        d.get("version") for d in _lineas_json(buf.getvalue()) if d.get("evento") == "detalle"
    }
    assert versiones == {"v0", "2026-01-01"}
