"""Pase MeLi opcion 1: Orbit SOLO lee el access_token que el bridge deja
en `meli_tokens.json` (junto a user_id y scope) y NUNCA refresca.

Las pruebas discriminan: el archivo del cron basta solo con
`access_token`; un 401 aborta con el mensaje claro `pase del bridge
vencido` sin intentar refresh (cero POST a /oauth/token, archivo
intacto); y las credenciales ni exigen ni exponen
`refresh_token`/`client_id`/`client_secret`.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.reputacion_clientes import ClienteMeli, MeliCredentials, ReputacionError


def _archivo_pase(tmp_path: Path, **extra) -> Path:
    """El JSON tal como lo deja el cron del bridge (sin secretos de app)."""
    datos = {"access_token": "PASE-BRIDGE", "user_id": 135734858, "scope": "read write"}
    datos.update(extra)
    (tmp_path / "meli_tokens.json").write_text(json.dumps(datos), encoding="utf-8")
    return tmp_path


def test_from_secrets_dir_acepta_solo_access_token(tmp_path):
    """El archivo del cron (access_token + user_id + scope) basta."""
    creds = MeliCredentials.from_secrets_dir(_archivo_pase(tmp_path))
    assert creds.access_token == "PASE-BRIDGE"


def test_credenciales_no_exigen_ni_exponen_refresh(tmp_path):
    """Ni refresh_token ni client_id/client_secret: ni se piden ni existen."""
    creds = MeliCredentials.from_secrets_dir(_archivo_pase(tmp_path))
    for atributo in ("refresh_token", "client_id", "client_secret"):
        assert not hasattr(creds, atributo)


def test_from_secrets_dir_sin_access_token_falla_claro(tmp_path):
    (tmp_path / "meli_tokens.json").write_text(json.dumps({"user_id": 1}), encoding="utf-8")
    with pytest.raises(ReputacionError, match="access_token"):
        MeliCredentials.from_secrets_dir(tmp_path)


def test_get_funciona_con_el_pase_del_bridge(tmp_path):
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json={"id": 135734858})

    creds = MeliCredentials.from_secrets_dir(_archivo_pase(tmp_path))
    cliente = ClienteMeli(creds, transport=httpx.MockTransport(handler), backoff_base=0.0)
    data, _ = cliente.get("/users/me")
    assert data["id"] == 135734858
    assert vistos[0].headers["Authorization"] == "Bearer PASE-BRIDGE"
    cliente.close()


def test_401_falla_pase_vencido_sin_intentar_refresh(tmp_path):
    """401 -> mensaje claro, cero refresh, cero reintento, archivo intacto."""
    llamadas: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append((request.method, request.url.path))
        if request.method != "GET" or request.url.path == "/oauth/token":
            raise AssertionError("Orbit NUNCA refresca: no debio intentarse")
        return httpx.Response(401, json={"error": "expired"})

    creds = MeliCredentials.from_secrets_dir(_archivo_pase(tmp_path))
    antes = (tmp_path / "meli_tokens.json").read_text(encoding="utf-8")
    cliente = ClienteMeli(creds, transport=httpx.MockTransport(handler), backoff_base=0.0)
    with pytest.raises(ReputacionError, match="pase del bridge vencido"):
        cliente.get("/users/me")
    assert llamadas == [("GET", "/users/me")]
    assert (tmp_path / "meli_tokens.json").read_text(encoding="utf-8") == antes
    cliente.close()


def test_put_401_es_acuse_pase_vencido_sin_refresh(tmp_path):
    """La escritura tampoco refresca: un solo PUT y acuse con el motivo."""
    from app.meli.write_client import MeliWriteClient

    llamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(401, json={"message": "expired"})

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=MeliCredentials.from_secrets_dir(_archivo_pase(tmp_path)),
        transport=httpx.MockTransport(handler),
    )
    acuse = cli.poner_precio("MLM1", [], Decimal("10"), "MXN", sonda=True)
    assert acuse.aceptada is False
    assert "pase del bridge vencido" in (acuse.codigo or "")
    assert [r.method for r in llamadas] == ["PUT"]
    cli.close()


def test_escritor_ya_no_acepta_refresco_inyectado():
    """Sin parametro `refresco` no hay camino que intente renovar el pase."""
    from app.meli.write_client import MeliWriteClient

    assert "refresco" not in inspect.signature(MeliWriteClient.__init__).parameters
