"""Escritura puntual de descripciones PEZ: deriva, respaldo y recuperacion."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.reputacion_clientes import MeliCredentials
from tools import corrige_descripciones_pez as herramienta


@pytest.fixture
def escenario():
    ids = sorted(herramienta.ITEMS)
    estados = ["active"] * 7 + ["under_review", "paused"]
    lote = {
        "version": 1,
        "confirmacion22mm": True,
        "filas": [
            {
                "item_id": item,
                "variante_pez": str(i),
                "seller_sku": f"PEZ-{i}",
                "seller_id": 42,
                "status": estados[i],
            }
            for i, item in enumerate(ids)
        ],
    }
    for i, f in enumerate(lote["filas"]):
        f.update(
            status=estados[i],
            antes=f"Original {i}",
            despues=(
                f"Original {i}\nVariante Sello cristiano (PEZ): arras de 22 mm de di\u00e1metro."
            ),
        )
    textos = {f["item_id"]: f["antes"] for f in lote["filas"]}
    items = {
        f["item_id"]: {
            "id": f["item_id"],
            "seller_id": 42,
            "status": f["status"],
            "variations": [
                {
                    "id": int(f["variante_pez"]),
                    "attributes": [{"id": "SELLER_SKU", "value_name": f["seller_sku"]}],
                }
            ],
        }
        for f in lote["filas"]
    }
    llamadas = []
    control = {"modo": None, "item": None, "put_count": 0, "desc_gets": {}}

    def handler(req):
        llamadas.append(req)
        assert req.url.host == "api.mercadolibre.com"
        assert req.headers["Authorization"] == "Bearer PASE-FALSO"
        if req.url.path == "/users/me":
            return httpx.Response(200, json={"id": 42})
        item = req.url.path.split("/")[2]
        if req.url.path.endswith("/description"):
            if req.method == "GET":
                control["desc_gets"][item] = control["desc_gets"].get(item, 0) + 1
                if (
                    control["item"] == item
                    and control["modo"] == "deriva_cas"
                    and control["desc_gets"][item] == 2
                ):
                    textos[item] = "edicion entre preflight y PUT"
                return httpx.Response(200, json={"plain_text": textos[item]})
            assert req.method == "PUT"
            payload = json.loads(req.content)
            assert set(payload) == {"plain_text"}
            control["put_count"] += 1
            if control["item"] == item and control["modo"] == "timeout_sin_aplicar":
                raise httpx.ReadTimeout("incierto", request=req)
            if control["item"] == item and control["modo"] == "5xx_sin_aplicar":
                return httpx.Response(503)
            if control["item"] == item and control["modo"] == "rechazo":
                return httpx.Response(400, json={"message": "no autorizado"})
            textos[item] = payload["plain_text"]
            if control["item"] == item and control["modo"] == "normaliza_tabs":
                textos[item] = textos[item].replace("\t", " ")
            if control["item"] == item and control["modo"] == "5xx_aplicado":
                return httpx.Response(503)
            if control["item"] == item and control["modo"] == "timeout_aplicado":
                raise httpx.ReadTimeout("incierto", request=req)
            return httpx.Response(200, json={"plain_text": textos[item]})
        assert req.method == "GET"
        return httpx.Response(200, json=items[item])

    cliente = herramienta.ClienteDescripciones(
        MeliCredentials("PASE-FALSO"), transport=httpx.MockTransport(handler)
    )
    yield cliente, lote, textos, items, llamadas, control
    cliente.close()


def test_roundtrip_solo_plain_text_y_respaldo(escenario, tmp_path):
    cliente, lote, textos, items, llamadas, control = escenario
    respaldo = tmp_path / "respaldo.json"
    assert herramienta.aplicar(cliente, lote)["estado"] == "dry-run"
    assert control["put_count"] == 0 and not respaldo.exists()
    assert herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["estado"] == "aplicado"
    assert all(textos[f["item_id"]] == f["despues"] for f in lote["filas"])
    assert respaldo.stat().st_mode & 0o777 == 0o600
    assert herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["ya_aplicados"] == 9
    assert control["put_count"] == 9
    assert herramienta.revertir(cliente, respaldo)["estado"] == "revertido"
    assert all(textos[f["item_id"]] == f["antes"] for f in lote["filas"])
    assert herramienta.revertir(cliente, respaldo)["ya_revertidos"] == 9
    assert control["put_count"] == 18
    ledger = [
        json.loads(linea) for linea in Path(str(respaldo) + ".jsonl").read_text().splitlines()
    ]
    assert sum(evento["evento"] == "intento" for evento in ledger) == 18
    assert sum(evento["evento"] == "confirmado" for evento in ledger) == 18
    assert [items[f["item_id"]]["status"] for f in lote["filas"]] == ["active"] * 7 + [
        "under_review",
        "paused",
    ]


@pytest.mark.parametrize("deriva", ["seller", "descripcion", "sku", "usuario"])
def test_deriva_preflight_cero_put(escenario, tmp_path, deriva):
    cliente, lote, textos, items, llamadas, control = escenario
    ultimo = lote["filas"][-1]["item_id"]
    if deriva == "seller":
        items[ultimo]["seller_id"] = 43
    elif deriva == "descripcion":
        textos[ultimo] = "edicion de tercero"
    elif deriva == "sku":
        items[ultimo]["variations"][0]["attributes"][0]["value_name"] = "OTRO"
    else:
        lote["filas"][-1]["seller_id"] = 43
    with pytest.raises(herramienta.ErrorDescripcion):
        herramienta.aplicar(cliente, lote, respaldo=tmp_path / "r.json", apply=True)
    assert control["put_count"] == 0


def test_respaldo_no_durable_cero_put(escenario, tmp_path, monkeypatch):
    cliente, lote, textos, items, llamadas, control = escenario

    def falla(fd):
        raise OSError("sin disco durable")

    monkeypatch.setattr(herramienta.os, "fsync", falla)
    with pytest.raises(OSError):
        herramienta.aplicar(cliente, lote, respaldo=tmp_path / "r.json", apply=True)
    assert control["put_count"] == 0


@pytest.mark.parametrize(
    "modo",
    ["timeout_aplicado", "timeout_sin_aplicar", "5xx_aplicado", "5xx_sin_aplicar", "rechazo"],
)
def test_fallo_y_reanudacion_sin_repetir_confirmados(escenario, tmp_path, modo):
    cliente, lote, textos, items, llamadas, control = escenario
    respaldo = tmp_path / "r.json"
    control.update(modo=modo, item=lote["filas"][1]["item_id"])
    if modo in ("timeout_aplicado", "5xx_aplicado"):
        assert (
            herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["estado"]
            == "aplicado"
        )
        assert control["put_count"] == 9
    else:
        with pytest.raises(herramienta.ErrorDescripcion):
            herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)
        assert respaldo.exists() and control["put_count"] == 2
        assert textos[lote["filas"][0]["item_id"]] == lote["filas"][0]["despues"]
        control["modo"] = None
        assert (
            herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["estado"]
            == "aplicado"
        )
        assert control["put_count"] == 10
    assert herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["ya_aplicados"] == 9


def test_reversa_aborta_ante_edicion_de_tercero_y_lote_no_intercambiable(escenario, tmp_path):
    cliente, lote, textos, items, llamadas, control = escenario
    respaldo = tmp_path / "r.json"
    herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)
    textos[lote["filas"][-1]["item_id"]] = "tercero"
    with pytest.raises(herramienta.ErrorDescripcion):
        herramienta.revertir(cliente, respaldo)
    assert control["put_count"] == 9
    lote["filas"][0]["despues"] += " editado"
    with pytest.raises(herramienta.ErrorDescripcion):
        herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)
    assert control["put_count"] == 9


def test_deriva_entre_preflight_y_put_no_pisa_edicion(escenario, tmp_path):
    cliente, lote, textos, items, llamadas, control = escenario
    control.update(modo="deriva_cas", item=lote["filas"][0]["item_id"])
    respaldo = tmp_path / "r.json"
    with pytest.raises(herramienta.ErrorDescripcion, match="deriva antes del PUT"):
        herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)
    assert control["put_count"] == 0 and respaldo.exists()
    assert textos[control["item"]] == "edicion entre preflight y PUT"


def test_canario_filtra_put_pero_preflight_y_respaldo_son_de_nueve(escenario, tmp_path):
    cliente, lote, textos, items, llamadas, control = escenario
    respaldo = tmp_path / "r.json"
    elegido = lote["filas"][0]["item_id"]
    resultado = herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True, items=[elegido])
    assert resultado["cambiados"] == 1 and control["put_count"] == 1
    assert textos[elegido] == lote["filas"][0]["despues"]
    assert all(textos[f["item_id"]] == f["antes"] for f in lote["filas"][1:])
    assert len(json.loads(respaldo.read_text())["lote"]["filas"]) == 9
    items[lote["filas"][-1]["item_id"]]["seller_id"] = 43
    with pytest.raises(herramienta.ErrorDescripcion):
        herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True, items=[elegido])
    assert control["put_count"] == 1
    items[lote["filas"][-1]["item_id"]]["seller_id"] = 42
    assert herramienta.revertir(cliente, respaldo)["ya_revertidos"] == 8
    assert control["put_count"] == 2


def test_cli_canario_respeta_item_de_argv(escenario, tmp_path, monkeypatch, capsys):
    cliente, lote, textos, items, llamadas, control = escenario
    archivo = tmp_path / "lote.json"
    archivo.write_text(json.dumps(lote), encoding="utf-8")
    elegido = lote["filas"][0]["item_id"]
    monkeypatch.setattr(
        herramienta.MeliCredentials, "from_secrets_dir", lambda: MeliCredentials("PASE-FALSO")
    )
    monkeypatch.setattr(herramienta, "ClienteDescripciones", lambda creds: cliente)
    assert (
        herramienta.main(
            [str(archivo), "--apply", "--respaldo", str(tmp_path / "r.json"), "--item", elegido]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["cambiados"] == 1
    assert control["put_count"] == 1
    assert textos[elegido] == lote["filas"][0]["despues"]
    assert all(textos[f["item_id"]] == f["antes"] for f in lote["filas"][1:])


def test_respaldo_nuevo_no_apropia_textos_ya_publicados(escenario, tmp_path):
    cliente, lote, textos, items, llamadas, control = escenario
    for f in lote["filas"]:
        textos[f["item_id"]] = f["despues"]
    respaldo = tmp_path / "nuevo.json"
    with pytest.raises(herramienta.ErrorDescripcion, match="respaldo original"):
        herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)
    assert control["put_count"] == 0
    assert not respaldo.exists()


def test_regresion_meli_normaliza_tabs_sin_ocultar_ediciones(escenario, tmp_path):
    """MLM4749600210 devolvio PUT200 con cada TAB convertido en un espacio."""
    cliente, lote, textos, items, llamadas, control = escenario
    item = "MLM4749600210"
    fila = next(f for f in lote["filas"] if f["item_id"] == item)
    fila["antes"] += "\tDetalle\tfinal"
    fila["despues"] += "\tDetalle\tfinal"
    textos[item] = fila["antes"]
    control.update(modo="normaliza_tabs", item=item)
    respaldo = tmp_path / "r.json"
    assert herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["cambiados"] == 9
    assert textos[item] == fila["despues"].replace("\t", " ")
    original = respaldo.read_bytes()
    assert herramienta.aplicar(cliente, lote)["ya_aplicados"] == 9
    assert herramienta.aplicar(cliente, lote, respaldo=respaldo, apply=True)["ya_aplicados"] == 9
    assert control["put_count"] == 9
    assert herramienta.revertir(cliente, respaldo)["cambiados"] == 9
    assert textos[item] == fila["antes"].replace("\t", " ")
    assert herramienta.revertir(cliente, respaldo)["ya_revertidos"] == 9
    assert respaldo.read_bytes() == original
    assert control["put_count"] == 18
    restaurado = textos[item]
    for delta in ("\n", " tercero"):
        textos[item] = restaurado + delta
        with pytest.raises(herramienta.ErrorDescripcion, match="tercero"):
            herramienta.revertir(cliente, respaldo)
        assert control["put_count"] == 18
