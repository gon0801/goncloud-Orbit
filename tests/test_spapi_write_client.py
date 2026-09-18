"""Tests del cliente de escritura SP-API (REPRICING 01, A.3).

`SpapiWriteClient` es la unica puerta de escritura a Amazon del carril C:
compone un `SpapiClient` solo para el token (patron D5), hace su propio
HTTP y expone exactamente `patch_listing`. Todo con `httpx.MockTransport`:
cero red, cero Amazon. Rojo primero por caso (`.saikit/scratch/C/tdd.md`).
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.spapi.client import (
    MERCADOS,
    SP_API_BASE,
    VENDEDORES_PROPIOS,
    SpapiClient,
    SpapiNoPermitida,
    construir_ruta_listings,
)
from app.spapi.write_client import (
    MutationNotAllowedError,
    SpapiWriteClient,
    validar_patch_listings,
)

CRED = {"lwa_app_id": "id-falsa", "lwa_client_secret": "secreto-falso", "refresh_token": "r"}
MARKETPLACE_MX = MERCADOS["amazon_mx"]
SELLER_MX = VENDEDORES_PROPIOS[MARKETPLACE_MX]
SKU = "SKU-P1"
RUTA = construir_ruta_listings(SELLER_MX, SKU)
CUERPO = {"patches": [{"op": "replace", "path": "/attributes/purchasable_offer"}]}


class _RedFalsa:
    """MockTransport con LWA automatico y PATCH scripteado; cuenta todo."""

    def __init__(self, patchs):
        self.patchs = list(patchs)
        self.pedidos = []
        self.tokens = 0
        self.sleeps = []

    def _handler(self, request):
        self.pedidos.append(request)
        if request.url.path == "/auth/o2/token":
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{self.tokens}", "expires_in": 3600}
            )
        status, headers, body = self.patchs.pop(0)
        return httpx.Response(status, headers=headers, json=body)

    @property
    def transport(self):
        return httpx.MockTransport(self._handler)

    @property
    def llamadas_patch(self):
        return [p for p in self.pedidos if p.method == "PATCH"]


def _lector(red):
    return SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=red.sleeps.append)


def _escritor(red, **kw):
    args = dict(
        platform="amazon_mx",
        modo_confirmado="live",
        lector=_lector(red),
        transport=red.transport,
    )
    args.update(kw)
    args["sleep"] = red.sleeps.append
    return SpapiWriteClient(**args)


# ---------------------------------------------------------- constructor


def test_constructor_sin_live_no_construye():
    red = _RedFalsa([])
    with pytest.raises(MutationNotAllowedError, match="live"):
        SpapiWriteClient(platform="amazon_mx", modo_confirmado="shadow", lector=_lector(red))
    assert red.pedidos == [] and red.tokens == 0


def test_platform_fuera_de_vocabulario():
    red = _RedFalsa([])
    with pytest.raises(ValueError, match="platform"):
        SpapiWriteClient(platform="mercadolibre", modo_confirmado="live", lector=_lector(red))


def test_sin_instancia_compartida():
    red = _RedFalsa([(202, {}, {"submissionId": "s1"}), (202, {}, {"submissionId": "s2"})])
    lector = _lector(red)
    primero = SpapiWriteClient(
        platform="amazon_mx", modo_confirmado="live", lector=lector, transport=red.transport
    )
    segundo = SpapiWriteClient(
        platform="amazon_mx", modo_confirmado="live", lector=lector, transport=red.transport
    )
    assert primero is not segundo
    assert primero.patch_listing(SKU, CUERPO).status_code == 202
    assert segundo.patch_listing(SKU, CUERPO).status_code == 202
    assert len(red.llamadas_patch) == 2


def test_superficie_publica_exacta():
    publicos = {n for n in dir(SpapiWriteClient) if not n.startswith("_")}
    assert publicos == {"patch_listing", "seller_id"}


def test_r1_a7_seller_id_solo_lectura_y_sellado():
    """r1-A7a: el seller se lee, no se escribe."""
    red = _RedFalsa([])
    escritor = _escritor(red)
    assert escritor.seller_id == VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]
    with pytest.raises(AttributeError):
        escritor.seller_id = "OTRO"


# ------------------------------------------------------------- validador


def test_validar_patch_listings_ok():
    assert validar_patch_listings(RUTA, SELLER_MX, SKU) == RUTA


@pytest.mark.parametrize(
    "path,seller,sku",
    [
        ("listings/2021-08-01/items/X/S", SELLER_MX, SKU),  # sin / inicial
        ("https://x/listings/2021-08-01/items/X/S", SELLER_MX, SKU),  # ://
        (RUTA + "?x=1", SELLER_MX, SKU),  # query
        (RUTA + "#f", SELLER_MX, SKU),  # fragment
        (RUTA.replace("SKU-P1", "SKU%2fP1"), SELLER_MX, SKU),  # barra encoded
        ("/listings/2021-08-01/items/../X", SELLER_MX, SKU),  # traversal
        ("/products/pricing/v0/price", SELLER_MX, SKU),  # otro prefijo
        (RUTA, "A0000000000000", SKU),  # seller ajeno
        (RUTA, SELLER_MX, "OTRO"),  # sku distinto
        (RUTA, SELLER_MX, ""),  # sku vacio
    ],
)
def test_validar_patch_listings_rechaza_antes_de_red(path, seller, sku):
    with pytest.raises(SpapiNoPermitida):
        validar_patch_listings(path, seller, sku)


def test_validar_falla_antes_de_red():
    red = _RedFalsa([])
    escritor = _escritor(red)
    with pytest.raises(SpapiNoPermitida):
        escritor.patch_listing("A/B", CUERPO)
    assert red.pedidos == [] and red.tokens == 0


# ----------------------------------------------------------------- PATCH


def test_patch_listing_feliz():
    red = _RedFalsa([(202, {}, {"submissionId": "abc", "status": "ACCEPTED"})])
    resp = _escritor(red).patch_listing(SKU, CUERPO)
    assert resp.status_code == 202
    (pedido,) = red.llamadas_patch
    assert pedido.url.path == RUTA
    assert str(pedido.url).startswith(SP_API_BASE)
    assert pedido.headers["x-amz-access-token"] == "tok-1"
    import json as _json

    assert _json.loads(pedido.content) == CUERPO


def test_401_un_refresh_y_reintento():
    red = _RedFalsa([(401, {}, {}), (202, {}, {"submissionId": "s"})])
    resp = _escritor(red).patch_listing(SKU, CUERPO)
    assert resp.status_code == 202
    assert len(red.llamadas_patch) == 2
    assert red.tokens == 2
    assert red.llamadas_patch[1].headers["x-amz-access-token"] == "tok-2"


def test_401_doble_se_devuelve_sin_tercer_intento():
    red = _RedFalsa([(401, {}, {}), (401, {}, {})])
    resp = _escritor(red).patch_listing(SKU, CUERPO)
    assert resp.status_code == 401
    assert len(red.llamadas_patch) == 2


def test_429_honra_retry_after_y_reintenta_una_vez():
    red = _RedFalsa([(429, {"Retry-After": "7"}, {}), (202, {}, {"submissionId": "s"})])
    resp = _escritor(red).patch_listing(SKU, CUERPO)
    assert resp.status_code == 202
    assert red.sleeps == [7.0]
    assert len(red.llamadas_patch) == 2


def test_429_con_retry_after_absurdo_se_acota():
    red = _RedFalsa([(429, {"Retry-After": "9999"}, {}), (202, {}, {})])
    _escritor(red).patch_listing(SKU, CUERPO)
    assert red.sleeps == [60.0]


def test_429_doble_se_devuelve():
    red = _RedFalsa([(429, {"Retry-After": "1"}, {}), (429, {"Retry-After": "1"}, {})])
    resp = _escritor(red).patch_listing(SKU, CUERPO)
    assert resp.status_code == 429
    assert len(red.llamadas_patch) == 2
    assert red.sleeps == [1.0]


def test_5xx_sin_reintento():
    red = _RedFalsa([(500, {}, {}), (202, {}, {})])
    resp = _escritor(red).patch_listing(SKU, CUERPO)
    assert resp.status_code == 500
    assert len(red.llamadas_patch) == 1


def test_error_de_red_sin_reintento():
    def _caida(request):
        raise httpx.ConnectError("caido", request=request)

    red = _RedFalsa([])
    lector = _lector(red)
    caida = httpx.MockTransport(_caida)
    escritor = SpapiWriteClient(
        platform="amazon_mx",
        modo_confirmado="live",
        lector=lector,
        transport=caida,
        sleep=red.sleeps.append,
    )
    with pytest.raises(httpx.ConnectError):
        escritor.patch_listing(SKU, CUERPO)


def test_r1_m_w3_seller_ajeno_aunque_la_ruta_coincida():
    """r1-M W3: seller fuera de VENDEDORES_PROPIOS se rechaza aunque el path matchee."""
    ajeno = "A0000000000000"
    ruta_ajena = construir_ruta_listings(ajeno, SKU)
    with pytest.raises(SpapiNoPermitida, match="VENDEDORES_PROPIOS"):
        validar_patch_listings(ruta_ajena, ajeno, SKU)


def test_r1_m_w10_traversal_falla_con_su_mensaje():
    """r1-M W10: el `..` lo rechaza SU defensa, no el tapa-final."""
    with pytest.raises(SpapiNoPermitida, match=r"traversal \('\.\.'\)"):
        validar_patch_listings("/listings/2021-08-01/items/../X", SELLER_MX, SKU)


def test_r1_m_w11_query_y_fragment_fallan_con_su_mensaje():
    """r1-M W11: `?` y `#` los rechaza SU defensa, no el tapa-final."""
    with pytest.raises(SpapiNoPermitida, match="query o fragment"):
        validar_patch_listings(RUTA + "?x=1", SELLER_MX, SKU)
    with pytest.raises(SpapiNoPermitida, match="query o fragment"):
        validar_patch_listings(RUTA + "#f", SELLER_MX, SKU)


def test_r1_m_cable_patch_ruta_header():
    """r1-M cable: metodo PATCH, ruta exacta con SKU encoded, token vigente."""
    sku = "SKU P1"
    red = _RedFalsa([(202, {}, {"submissionId": "abc", "status": "ACCEPTED"})])
    resp = _escritor(red).patch_listing(sku, CUERPO)
    assert resp.status_code == 202
    (pedido,) = red.llamadas_patch
    assert pedido.method == "PATCH"
    crudo = pedido.url.raw_path.decode("ascii")
    ruta, _, query = crudo.partition("?")
    assert ruta == construir_ruta_listings(SELLER_MX, sku)
    assert "SKU%20P1" in ruta and " " not in ruta
    assert query == f"marketplaceIds={MARKETPLACE_MX}"
    assert pedido.headers["x-amz-access-token"] == "tok-1"


@pytest.mark.parametrize(
    "platform,marketplace",
    [("amazon_mx", "A1AM78C64UM0Y8"), ("amazon_us", "ATVPDKIKX0DER")],
    ids=["mx", "us"],
)
def test_r4_g1_patch_lleva_marketplace_ids_del_platform(platform, marketplace):
    """r4-G1: el PATCH lleva marketplaceIds=<el del platform>, aunque el seller sea el mismo."""
    red = _RedFalsa([(202, {}, {"submissionId": "g1", "status": "ACCEPTED"})])
    escritor = _escritor(red, platform=platform)
    resp = escritor.patch_listing(SKU, CUERPO)
    assert resp.status_code == 202
    (pedido,) = red.llamadas_patch
    assert pedido.method == "PATCH"
    assert pedido.url.params["marketplaceIds"] == marketplace


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"patches": [{"op": "replace", "value": Decimal("10.00")}]},
        {"patches": [{"op": "replace", "value": {"monto": 10.5}}]},
    ],
    ids=["decimal-anidado", "float-anidado"],
)
def test_r4_g5_cuerpo_con_numeros_binarios_es_mal_uso(cuerpo):
    """r4-G5: Decimal o float en cualquier nivel -> ValueError antes de la red."""
    red = _RedFalsa([(202, {}, {"submissionId": "g5"})])
    with pytest.raises(ValueError, match="serializable"):
        _escritor(red).patch_listing(SKU, cuerpo)
    assert red.pedidos == [] and red.tokens == 0
