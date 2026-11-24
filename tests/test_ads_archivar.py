"""Operaciones de product ads (app/ads/archivar.py): lectura por trozos y por
grupo, preparacion de escritor sellado, pausa, reactivacion y reposicion.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ads.archivar import (
    FILTRO_AD_GROUP,
    LecturaAmbigua,
    anuncios_vivos_del_grupo,
    estados_actuales,
    pausar_anuncios,
    reactivar_anuncios,
    reponer_anuncios,
)
from tests.test_ads_write import _token_response, make_write_client

PATH_LIST = "/sp/productAds/list"


def _handler(estados: dict[str, str]):
    """Transporte falso: responde el list con `estados`."""
    llamadas: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        llamadas.append((request.url.path, cuerpo))
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        filas = [{"adId": ad_id, "state": estados[ad_id]} for ad_id in pedidos if ad_id in estados]
        return httpx.Response(200, json={"productAds": filas})

    return handler, llamadas


def test_la_lectura_de_estados_se_parte_en_trozos():
    """Un include con cientos de ids no viaja en una sola llamada."""
    estados = {str(i): "ENABLED" for i in range(250)}
    handler, llamadas = _handler(estados)
    leidos = estados_actuales(make_write_client(handler), [str(i) for i in range(250)])
    lecturas = [c for c in llamadas if c[0].endswith(PATH_LIST)]
    assert len(lecturas) == 3, "250 ids con tope de 100 = 3 llamadas"
    assert len(leidos) == 250


def _perfil_crudo(profile_id: int, pais: str = "MX") -> dict:
    """Payload crudo de /v2/profiles, forma REAL (countryCode + accountInfo)."""
    return {
        "profileId": profile_id,
        "countryCode": pais,
        "currencyCode": "MXN" if pais == "MX" else "USD",
        "accountInfo": {"type": "seller", "validPaymentMethod": True, "name": "Cuenta"},
    }


def _monta_perfiles(monkeypatch, crudos: list[dict]) -> list:
    """Deja preparar_escritor corriendo contra el evaluar_perfiles REAL.

    No se fabrican PerfilAds a mano a proposito: el hallazgo de la
    cross-review fue justamente que el test viejo simulaba una salida que
    `evaluar_perfiles` no puede producir. Aca entra el payload crudo y el
    gate de verdad decide, asi que el test prueba el CONTRATO entre los dos.
    """
    from app.ads import archivar as mod

    construidos: list = []
    monkeypatch.setattr(mod.AdsCredentials, "from_secrets_dir", classmethod(lambda cls: "CREDS"))
    monkeypatch.setattr(mod, "AdsClient", lambda *a, **k: _ClienteDePerfiles(crudos))
    monkeypatch.setattr(mod, "AdsWriteClient", lambda *a, **k: construidos.append(k) or "ESCRITOR")
    return construidos


class _ClienteDePerfiles:
    """Cliente falso que solo sabe contestar GET /v2/profiles."""

    def __init__(self, crudos: list[dict]) -> None:
        self._crudos = crudos

    def get(self, _path, **_k):
        return httpx.Response(200, json=self._crudos)


def test_preparar_escritor_para_si_la_cuenta_tiene_DOS_perfiles_del_pais(monkeypatch):
    """El caso que el conteo de aceptados NO puede ver.

    `evaluar_perfiles` garantiza como maximo UN aceptado por pais: con dos
    perfiles de MX acepta el PRIMERO del payload y marca el otro como
    duplicado. Por eso `len(aceptados) != 1` jamas dispara — y sin este
    candado se archivaria a ciegas en una de las dos cuentas, sin reversa
    (hallazgo cross-review codex/grok 2026-08-30).
    """
    from app.ads import archivar as mod

    construidos = _monta_perfiles(monkeypatch, [_perfil_crudo(111), _perfil_crudo(222)])
    with pytest.raises(ValueError, match="MAS DE UN perfil"):
        mod.preparar_escritor("amazon_mx", modo_confirmado="live")
    assert construidos == [], "no se construye escritor con la cuenta ambigua"


def test_preparar_escritor_sin_perfil_aceptado_no_construye_nada(monkeypatch):
    from app.ads import archivar as mod

    construidos = _monta_perfiles(monkeypatch, [_perfil_crudo(111, "US")])
    with pytest.raises(ValueError, match="EXACTAMENTE 1 perfil"):
        mod.preparar_escritor("amazon_mx", modo_confirmado="live")
    assert construidos == []


def test_preparar_escritor_sella_plataforma_y_perfil(monkeypatch):
    from app.ads import archivar as mod

    construidos = _monta_perfiles(monkeypatch, [_perfil_crudo(303030)])
    assert mod.preparar_escritor("amazon_mx", modo_confirmado="live") == "ESCRITOR"
    assert construidos[0]["platform"] == "amazon_mx"
    assert construidos[0]["profile_id"] == 303030
    assert construidos[0]["modo_confirmado"] == "live"


def test_preparar_escritor_no_fabrica_el_modo(monkeypatch):
    """ARREGLOS 05 D: el modo es del dueno; el helper no lo escribe solo."""
    from app.ads import archivar as mod

    construidos = _monta_perfiles(monkeypatch, [_perfil_crudo(303030)])
    with pytest.raises(TypeError):
        mod.preparar_escritor("amazon_mx")  # type: ignore[call-arg]
    mod.preparar_escritor("amazon_mx", modo_confirmado="shadow")
    assert construidos[-1]["modo_confirmado"] == "shadow", (
        "el modo debe llegar tal cual del dueno: el rechazo real lo aplica el "
        "constructor del cliente (test_arreglos05_d_preparar_escritor_exige_el_modo_autorizado)"
    )


# ---------------------------------------------------------------------------
# LA REVERSA (invariante 7, decision del dueno 2026-08-30)
# ---------------------------------------------------------------------------

PATH_CREATE = "/sp/productAds"


def _handler_create(respuesta, *, readback_state: str | None = "PAUSED"):
    """Create responde `respuesta`. Si el ack trae adId y `readback_state`
    no es None, el list posterior lo confirma con ese state; None = list vacio
    (para cazar el falso `repuesto` sin fila viva)."""
    llamadas: list[dict] = []
    nuevo: dict[str, str | None] = {"id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(PATH_LIST):
            filas = []
            if nuevo["id"] is not None and readback_state is not None:
                filas = [{"adId": nuevo["id"], "state": readback_state}]
            return httpx.Response(200, json={"productAds": filas})
        llamadas.append(cuerpo)
        try:
            ack = json.loads(respuesta.content) if respuesta.content else {}
        except ValueError:
            ack = {}
        for valor in ack.values() if isinstance(ack, dict) else ():
            if not isinstance(valor, dict):
                continue
            for item in valor.get("success") or []:
                if isinstance(item, dict):
                    for clave in ("adId", "productAdId"):
                        if item.get(clave):
                            nuevo["id"] = str(item[clave])
                            break
        return respuesta

    return handler, llamadas


def test_el_ensayo_de_la_reversa_no_crea_nada():
    handler, llamadas = _handler_create(httpx.Response(207, json={}))
    resultados = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-1 state=PAUSED"],
        ejecutar=False,
        avisar=lambda _m: None,
    )
    assert llamadas == []
    assert [r.resultado for r in resultados] == ["sin_confirmar"]


def test_la_reversa_repone_y_devuelve_el_id_nuevo():
    ack = httpx.Response(207, json={"productAds": {"success": [{"adId": "999"}]}})
    handler, llamadas = _handler_create(ack)
    (resultado,) = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-1 state=ENABLED"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    objeto = llamadas[0]["productAds"][0]
    assert objeto == {"adGroupId": "77", "campaignId": "55", "sku": "SKU-1", "state": "ENABLED"}
    assert resultado.resultado == "repuesto"
    assert resultado.ad_id == "999"


def test_ack_con_adId_sin_fila_en_list_es_sin_confirmar():
    """Un 207 success sin readback vivo no es `repuesto`: el operador veria
    exito sobre un anuncio que quiza no existe."""
    ack = httpx.Response(207, json={"productAds": {"success": [{"adId": "999"}]}})
    handler, _ = _handler_create(ack, readback_state=None)
    (resultado,) = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-1 state=PAUSED"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert resultado.resultado == "sin_confirmar"
    assert resultado.ad_id == "999"
    assert "readback" in resultado.detalle


def test_r09r4_ar02_relecturas_con_pausa():
    """R09-r4 B6 (AR02): el nuevo tarda 1 list en verse: se duerme
    entre relecturas (no salen seguidas)."""
    ack = httpx.Response(207, json={"productAds": {"success": [{"adId": "999"}]}})
    base, _ = _handler_create(ack, readback_state="ENABLED")
    llamadas = {"list": 0}
    dormidas: list = []

    def handler(request):
        if request.url.host == "api.amazon.com":
            return _token_response()
        if request.url.path.endswith(PATH_LIST):
            llamadas["list"] += 1
            if llamadas["list"] < 2:
                return httpx.Response(200, json={"productAds": []})
        return base(request)

    (resultado,) = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-1 state=ENABLED"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda s: dormidas.append(s),
        pausa=0.2,
    )
    assert resultado.resultado == "repuesto"
    # Una entre relecturas + una por anuncio procesado.
    assert dormidas == [0.2, 0.2]


def test_r09r4_ar03_archived_no_corta_reintento():
    """R09-r4 B6 (AR03): relectura ARCHIVED del anuncio recreado: no
    corta, sigue hasta verlo vivo (o agotar los 3 intentos)."""
    ack = httpx.Response(207, json={"productAds": {"success": [{"adId": "999"}]}})
    base, _ = _handler_create(ack, readback_state="ENABLED")
    llamadas = {"list": 0}

    def handler(request):
        if request.url.host == "api.amazon.com":
            return _token_response()
        if request.url.path.endswith(PATH_LIST):
            llamadas["list"] += 1
            if llamadas["list"] < 3:
                return httpx.Response(
                    200, json={"productAds": [{"adId": "999", "state": "ARCHIVED"}]}
                )
        return base(request)

    (resultado,) = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-1 state=ENABLED"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert resultado.resultado == "repuesto"
    assert llamadas["list"] == 3


def test_una_linea_incompleta_aborta_antes_de_crear_nada():
    """Reponer con un dato adivinado crearia el anuncio equivocado, y eso
    gasta plata. Y se parsea TODO antes de mandar: una linea rota a mitad
    dejaria media reposicion hecha."""
    handler, llamadas = _handler_create(httpx.Response(207, json={}))
    with pytest.raises(ValueError, match="sin campaignId"):
        reponer_anuncios(
            make_write_client(handler),
            ["adGroupId=77 campaignId=55 sku=SKU-1", "adGroupId=78 sku=SKU-2"],
            ejecutar=True,
            avisar=lambda _m: None,
        )
    assert llamadas == [], "ni la primera linea, que si estaba completa, se mando"


def test_sin_estado_la_reversa_repone_PAUSADO():
    """Un anuncio repuesto en ENABLED empieza a gastar solo."""
    handler, llamadas = _handler_create(
        httpx.Response(207, json={"productAds": {"success": [{"adId": "1"}]}})
    )
    reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-1"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert llamadas[0]["productAds"][0]["state"] == "PAUSED"


def test_un_207_con_rechazo_al_reponer_es_fallo():
    ack = httpx.Response(207, json={"productAds": {"error": [{"errors": [{"e": "NO_SKU"}]}]}})
    handler, _ = _handler_create(ack)
    (resultado,) = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=77 campaignId=55 sku=SKU-MALO state=PAUSED"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert resultado.resultado == "fallo"
    assert "NO_SKU" in resultado.detalle


def test_el_rechazo_REAL_de_amazon_al_crear_se_lee_como_fallo():
    """El ack de la sonda del 2026-08-31, TAL CUAL lo devolvio Amazon.

    Los otros tests del 207 usan un cuerpo inventado por nosotros; este usa
    el shape REAL, capturado en vivo al intentar crear un anuncio con un sku
    inexistente. Si el parser solo entendiera nuestra version de juguete,
    este test lo delata.
    """
    ack_real = {
        "productAds": {
            "error": [
                {
                    "errors": [
                        {
                            "errorType": "adEligibilityError",
                            "errorValue": {
                                "adEligibilityError": {
                                    "cause": {},
                                    "message": "Product is ineligible for advertising",
                                    "reason": "AD_INELIGIBLE",
                                }
                            },
                        }
                    ],
                    "index": 0,
                }
            ],
            "success": [],
        }
    }
    handler, _ = _handler_create(httpx.Response(207, json=ack_real))
    (resultado,) = reponer_anuncios(
        make_write_client(handler),
        ["adGroupId=224640906079130 campaignId=177498916708097 sku=NO-EXISTE state=PAUSED"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert resultado.resultado == "fallo", "un 207 con error[] NO es exito"
    assert "AD_INELIGIBLE" in resultado.detalle


PATH_PUT_ADS = "/sp/productAds"


def _handler_put(estados: dict[str, str]):
    """Transporte falso: el list responde `estados` y cada PUT actualiza
    el estado en vivo (el readback confirma lo mutado)."""
    llamadas: list[tuple[str, dict]] = []
    vivos = dict(estados)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        llamadas.append((request.url.path, cuerpo))
        if request.method == "PUT" and request.url.path == PATH_PUT_ADS:
            for entrada in cuerpo.get("productAds", []):
                vivos[str(entrada["adId"])] = str(entrada["state"])
            return httpx.Response(207, json={"productAds": {"error": [], "success": []}})
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        filas = [{"adId": ad_id, "state": vivos[ad_id]} for ad_id in pedidos if ad_id in vivos]
        return httpx.Response(200, json={"productAds": filas})

    return handler, llamadas


def _puts(llamadas) -> list[tuple[str, str]]:
    return [
        (e["adId"], e["state"])
        for path, cuerpo in llamadas
        if path == PATH_PUT_ADS
        for e in cuerpo.get("productAds", [])
    ]


def test_pausa_solo_los_enabled_y_reactivar_solo_los_paused():
    """I.4: pausar toca ENABLED, reactivar toca PAUSED; lo que ya esta en
    destino sale `ya_estaba` y lo inexistente `no_existe`."""
    handler, llamadas = _handler_put({"1": "ENABLED", "2": "PAUSED"})
    pausa = pausar_anuncios(
        make_write_client(handler),
        ["1", "2", "3"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert _puts(llamadas) == [("1", "PAUSED")]
    assert {r.ad_id: r.resultado for r in pausa} == {
        "1": "pausado",
        "2": "ya_estaba",
        "3": "no_existe",
    }
    handler2, llamadas2 = _handler_put({"1": "ENABLED", "2": "PAUSED"})
    reactiva = reactivar_anuncios(
        make_write_client(handler2),
        ["1", "2"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert _puts(llamadas2) == [("2", "ENABLED")]
    assert {r.ad_id: r.resultado for r in reactiva} == {"1": "ya_estaba", "2": "reactivado"}


def test_r09r4_previa_caida_es_sin_confirmar_no_no_existe():
    """R09-r4 B4 (f): si la lectura previa cae (500), no se sabe nada:
    sale `sin_confirmar` (retomable), NO `no_existe` (que cerraria el
    retiro para siempre). Cero PUTs."""
    llamadas: list = []

    def handler(request):
        if request.url.host == "api.amazon.com":
            return _token_response()
        llamadas.append(request.url.path)
        return httpx.Response(500, json={"message": "caido"})

    (res,) = pausar_anuncios(
        make_write_client(handler),
        ["1"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert res.resultado == "sin_confirmar"
    assert "previa" in res.detalle
    assert [p for p in llamadas if p == PATH_PUT_ADS] == []


def test_put_no_toca_archived_y_ensayo_no_muta():
    """I.4: un ARCHIVED sale `fallo` sin PUT; el ensayo no manda nada."""
    handler, llamadas = _handler_put({"1": "ARCHIVED", "2": "ENABLED"})
    (arch,) = pausar_anuncios(
        make_write_client(handler),
        ["1"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert arch.resultado == "fallo"
    assert _puts(llamadas) == []
    handler2, llamadas2 = _handler_put({"2": "ENABLED"})
    (ensayo,) = pausar_anuncios(
        make_write_client(handler2),
        ["2"],
        ejecutar=False,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert ensayo.resultado == "sin_confirmar" and ensayo.detalle == "ENSAYO"
    assert _puts(llamadas2) == []


def test_r09r3_pausa_207_con_rechazo_por_item_es_fallo():
    """R09 B12 (A08): al pausar, un 207 con error por item y readback sin
    cambio se reporta `fallo`, no `pausado`."""
    import json as _json

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        if request.method == "PUT" and request.url.path == PATH_PUT_ADS:
            return httpx.Response(
                207,
                json={"productAds": {"error": [{"errors": [{"errorType": "NO_PUDO"}]}]}},
            )
        pedidos = _json.loads(request.content).get("adIdFilter", {}).get("include", [])
        return httpx.Response(
            200,
            json={"productAds": [{"adId": i, "state": "ENABLED"} for i in pedidos]},
        )

    (resultado,) = pausar_anuncios(
        make_write_client(handler),
        ["1"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert resultado.resultado == "fallo"


# ---------------------------------------------------------------------------
# AUTO-03 G (F3/F4): la lectura previa al create de `recrea`. Solo una lectura
# completa y valida dice "no hay anuncio vivo"; cualquier otra forma levanta.
# ---------------------------------------------------------------------------


def _paginas(*paginas):
    """Transporte que responde el list con `paginas` en orden (la ultima se
    repite) y guarda el body de cada llamada."""
    cuerpos: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        assert request.method == "POST" and request.url.path == PATH_LIST
        cuerpos.append(json.loads(request.content))
        return httpx.Response(200, json=paginas[min(len(cuerpos), len(paginas)) - 1])

    return make_write_client(handler), cuerpos


def _ad(ad_id, *, sku="SKU-1", state="ENABLED", ag="31"):
    return {"adId": ad_id, "adGroupId": ag, "campaignId": "21", "sku": sku, "state": state}


def test_el_filtro_del_list_por_ad_group_se_llama_exactamente_adGroupIdFilter():
    """Como `adIdFilter` en el archivado: Amazon ignora EN SILENCIO un filtro
    que no reconoce y devuelve la pagina entera. Con la clave equivocada, la
    lectura previa al create dejaria de mirar el ad group pedido. El body
    lleva SOLO adGroupIdFilter con el ad group, y nextToken en las paginas
    siguientes; ademas se cuentan solo los vivos de ese sku.

    Sonda de lectura del 2026-11-07 (POST /sp/productAds/list en vivo,
    perfil amazon_mx, ad group 330240993709802, cliente de lectura): con
    `adGroupIdFilter` la API devolvio 12 filas, todas de ese ad group (las
    12 que tiene la base); con una clave inventada devolvio 1000 filas de
    50 ad groups ajenos."""
    escritor, cuerpos = _paginas(
        {"productAds": [_ad("1", state="ARCHIVED"), _ad("2", sku="OTRO")], "nextToken": "t1"},
        {"productAds": [_ad("3"), _ad("4", state="PAUSED")]},
    )
    vivos = anuncios_vivos_del_grupo(escritor, "31", "SKU-1")
    assert [v["adId"] for v in vivos] == ["3", "4"]
    assert FILTRO_AD_GROUP == "adGroupIdFilter"
    assert cuerpos == [
        {"adGroupIdFilter": {"include": ["31"]}},
        {"adGroupIdFilter": {"include": ["31"]}, "nextToken": "t1"},
    ]


@pytest.mark.parametrize(
    "paginas",
    [
        pytest.param(
            [{"productAds": [_ad("1", state="ARCHIVED")], "nextToken": ""}], id="token-vacio"
        ),
        pytest.param([{"productAds": [], "nextToken": 7}], id="token-numero"),
        pytest.param([{"productAds": [], "nextToken": "t"}], id="token-repetido"),
        pytest.param(
            [{"productAds": [], "nextToken": f"t{i}"} for i in range(30)], id="truncada-al-tope"
        ),
        pytest.param(
            [{"productAds": [{"adId": "1", "adGroupId": "31", "sku": "SKU-1"}]}],
            id="fila-sin-state",
        ),
        pytest.param([{"productAds": [_ad("1", state="PROPOSED")]}], id="state-desconocido"),
        pytest.param([{"productAds": [_ad("1", sku="")]}], id="sku-vacio"),
        pytest.param([{"productAds": [{**_ad("1"), "adId": None}]}], id="fila-sin-adId"),
        pytest.param([{"productAds": ["no-es-fila"]}], id="fila-no-dict"),
        pytest.param([{"productAds": [_ad("9", ag="99")]}], id="otro-ad-group"),
        pytest.param([{"otra": []}], id="sin-contenedor"),
        pytest.param([["no", "es", "dict"]], id="body-no-dict"),
    ],
)
def test_la_lectura_previa_al_create_no_concluye_ausencia_de_una_pagina_dudosa(paginas):
    escritor, _ = _paginas(*paginas)
    with pytest.raises(LecturaAmbigua):
        anuncios_vivos_del_grupo(escritor, "31", "SKU-1")
