"""Archivado operado de product ads muertos (app/ads/archivar.py).

Lo que estos tests protegen, en orden de gravedad:

1. El ENSAYO no manda ni una mutacion. Es el unico modo que el operador ve
   antes de decidir; si mintiera, el "dry-run" no seria dry.
2. No se re-archiva lo ya archivado ni se le pega a ids que no existen.
3. Un readback que NO confirma se reporta `sin_confirmar` — jamas se cuenta
   como exito (el list es eventualmente consistente, y un exito inventado es
   peor que un pendiente declarado).
4. Los adIds repetidos revientan ANTES de mutar: la misma lista dos veces es
   la forma facil de mandar el doble de mutaciones sin darse cuenta.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ads.archivar import (
    RESULTADOS,
    archivar_anuncios,
    estados_actuales,
    resumen,
)
from tests.test_ads_write import _token_response, make_write_client

PATH_LIST = "/sp/productAds/list"
PATH_DELETE = "/sp/productAds/delete"


def _handler(estados: dict[str, str], *, estados_despues: dict[str, str] | None = None):
    """Transporte falso: responde el list con `estados` y, despues del primer
    delete, con `estados_despues` (para simular el efecto del archivado)."""
    llamadas: list[tuple[str, dict]] = []
    hubo_delete = {"si": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        llamadas.append((request.url.path, cuerpo))

        if request.url.path.endswith(PATH_DELETE):
            hubo_delete["si"] = True
            return httpx.Response(207, json={"productAds": {"success": []}})

        vigentes = estados_despues if (hubo_delete["si"] and estados_despues) else estados
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        filas = [
            {"adId": ad_id, "state": vigentes[ad_id]} for ad_id in pedidos if ad_id in vigentes
        ]
        return httpx.Response(200, json={"productAds": filas})

    return handler, llamadas


def _deletes(llamadas) -> list[str]:
    return [c[1]["adIdFilter"]["include"][0] for c in llamadas if c[0].endswith(PATH_DELETE)]


def test_el_ensayo_no_manda_ni_una_mutacion():
    """El modo que el operador usa para decidir NO puede tocar la cuenta."""
    handler, llamadas = _handler({"1": "ENABLED", "2": "ENABLED"})
    resultados = archivar_anuncios(
        make_write_client(handler), ["1", "2"], ejecutar=False, avisar=lambda _m: None
    )
    assert _deletes(llamadas) == []
    assert [r.resultado for r in resultados] == ["sin_confirmar", "sin_confirmar"]
    assert all(r.detalle == "ENSAYO" for r in resultados)


def test_archiva_solo_los_vivos_y_confirma_con_readback():
    handler, llamadas = _handler(
        {"1": "ENABLED", "2": "PAUSED", "3": "ARCHIVED"},
        estados_despues={"1": "ARCHIVED", "2": "ARCHIVED", "3": "ARCHIVED"},
    )
    resultados = archivar_anuncios(
        make_write_client(handler),
        ["1", "2", "3", "4"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    por_id = {r.ad_id: r for r in resultados}

    assert sorted(_deletes(llamadas)) == ["1", "2"], "solo los vivos se mutan"
    assert por_id["1"].resultado == "archivado"
    assert por_id["2"].resultado == "archivado"
    assert por_id["3"].resultado == "ya_estaba"
    assert por_id["4"].resultado == "no_existe"
    assert por_id["4"].estado_previo is None


def test_readback_que_no_confirma_no_se_cuenta_como_exito():
    """El list es eventualmente consistente: si no dice ARCHIVED, el
    resultado es `sin_confirmar` y el operador lo ve. Un exito inventado
    dejaria anuncios vivos que nadie vuelve a mirar."""
    handler, _llamadas = _handler({"1": "ENABLED"}, estados_despues={"1": "ENABLED"})
    (resultado,) = archivar_anuncios(
        make_write_client(handler),
        ["1"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    assert resultado.resultado == "sin_confirmar"
    assert "ENABLED" in resultado.detalle


def test_un_rechazo_de_amazon_no_frena_a_los_demas():
    estados = {"1": "ENABLED", "2": "ENABLED"}
    intentos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(PATH_DELETE):
            ad_id = cuerpo["adIdFilter"]["include"][0]
            intentos.append(ad_id)
            if ad_id == "1":
                return httpx.Response(400, json={"message": "no se pudo"})
            return httpx.Response(207, json={"productAds": {"success": []}})
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        vigentes = dict(estados) if not intentos else {"1": "ENABLED", "2": "ARCHIVED"}
        return httpx.Response(
            200,
            json={
                "productAds": [{"adId": i, "state": vigentes[i]} for i in pedidos if i in vigentes]
            },
        )

    resultados = archivar_anuncios(
        make_write_client(handler),
        ["1", "2"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    por_id = {r.ad_id: r for r in resultados}
    assert intentos == ["1", "2"], "el fallo del primero no aborta el resto"
    assert por_id["1"].resultado == "fallo"
    assert por_id["1"].detalle
    assert por_id["2"].resultado == "archivado"


def test_ids_repetidos_revientan_antes_de_cualquier_mutacion():
    """La misma lista pegada dos veces mandaria el doble de mutaciones."""
    handler, llamadas = _handler({"1": "ENABLED"})
    with pytest.raises(ValueError, match="repetidos"):
        archivar_anuncios(
            make_write_client(handler), ["1", "1"], ejecutar=True, avisar=lambda _m: None
        )
    assert llamadas == [], "ni siquiera se leyo: se aborta antes"


def test_la_lectura_de_estados_se_parte_en_trozos():
    """Un include con cientos de ids no viaja en una sola llamada."""
    estados = {str(i): "ENABLED" for i in range(250)}
    handler, llamadas = _handler(estados)
    leidos = estados_actuales(make_write_client(handler), [str(i) for i in range(250)])
    lecturas = [c for c in llamadas if c[0].endswith(PATH_LIST)]
    assert len(lecturas) == 3, "250 ids con tope de 100 = 3 llamadas"
    assert len(leidos) == 250


def test_el_resumen_declara_los_ceros():
    """Un resultado en cero y un resultado ausente no son lo mismo al leer
    un log de una corrida que muto la cuenta del dueno."""
    cuenta = resumen([])
    assert set(cuenta) == set(RESULTADOS)
    assert set(cuenta.values()) == {0}


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
        mod.preparar_escritor("amazon_mx")
    assert construidos == [], "no se construye escritor con la cuenta ambigua"


def test_preparar_escritor_sin_perfil_aceptado_no_construye_nada(monkeypatch):
    from app.ads import archivar as mod

    construidos = _monta_perfiles(monkeypatch, [_perfil_crudo(111, "US")])
    with pytest.raises(ValueError, match="EXACTAMENTE 1 perfil"):
        mod.preparar_escritor("amazon_mx")
    assert construidos == []


def test_preparar_escritor_sella_plataforma_y_perfil(monkeypatch):
    from app.ads import archivar as mod

    construidos = _monta_perfiles(monkeypatch, [_perfil_crudo(303030)])
    assert mod.preparar_escritor("amazon_mx") == "ESCRITOR"
    assert construidos[0]["platform"] == "amazon_mx"
    assert construidos[0]["profile_id"] == 303030
    assert construidos[0]["modo_confirmado"] == "live"


# ---------------------------------------------------------------------------
# Cierres de la cross-review codex/grok (2026-08-30)
# ---------------------------------------------------------------------------


def _handler_envio(respuesta_delete, estados_despues: dict[str, str]):
    """Delete que responde `respuesta_delete`; el list posterior dice `estados_despues`."""
    hubo_delete = {"si": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(PATH_DELETE):
            hubo_delete["si"] = True
            return respuesta_delete
        vigentes = estados_despues if hubo_delete["si"] else {"1": "ENABLED"}
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        return httpx.Response(
            200,
            json={
                "productAds": [
                    {"adId": i, "state": vigentes[i], "adGroupId": "77", "asin": "B0X"}
                    for i in pedidos
                    if i in vigentes
                ]
            },
        )

    return handler


def _archivar_uno(handler):
    (resultado,) = archivar_anuncios(
        make_write_client(handler),
        ["1"],
        ejecutar=True,
        avisar=lambda _m: None,
        dormir=lambda _s: None,
    )
    return resultado


def test_el_estado_en_amazon_manda_sobre_el_fallo_del_envio():
    """Un corte despues de aplicar reportaba `fallo` sobre algo que SI quedo
    archivado. La verdad es el estado en Amazon, no lo que dijo el envio."""
    resultado = _archivar_uno(
        _handler_envio(httpx.Response(500, json={"message": "se corto"}), {"1": "ARCHIVED"})
    )
    assert resultado.resultado == "archivado"
    assert "pese al rechazo del envio" in resultado.detalle


def test_un_207_con_rechazo_por_item_es_fallo_y_no_sin_confirmar():
    """Un 2xx NO es exito automatico: Amazon mete el rechazo por-item dentro
    del 207. Sin leer error[], un rechazo se disfrazaba de `sin_confirmar` —
    que se lee como 'quiza tarde en reflejarse' y no como 'no paso'."""
    ack = httpx.Response(
        207, json={"productAds": {"error": [{"errors": [{"errorType": "ENTITY_NOT_FOUND"}]}]}}
    )
    resultado = _archivar_uno(_handler_envio(ack, {"1": "ENABLED"}))
    assert resultado.resultado == "fallo"
    assert "207 con rechazo por-item" in resultado.detalle
    assert "ENTITY_NOT_FOUND" in resultado.detalle


def test_guarda_con_que_re_crear_el_anuncio_archivado():
    """Amazon no des-archiva: adGroupId + asin es lo unico que separa
    'me equivoque' de 'perdi el dato para siempre' (invariante 7)."""
    resultado = _archivar_uno(_handler_envio(httpx.Response(207, json={}), {"1": "ARCHIVED"}))
    assert resultado.resultado == "archivado"
    assert "adGroupId=77" in resultado.reversa
    assert "asin=B0X" in resultado.reversa
