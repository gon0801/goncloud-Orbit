"""Fotos reales por publicacion: origen, aislamiento y fallos sin romper el catalogo."""

import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import _conexion_lectura
from app.main import app


def test_ruta_foto_publicacion_inexistente_no_consulta_amazon():
    conn = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: None))
    app.dependency_overrides[_conexion_lectura] = lambda: conn
    try:
        response = TestClient(app).get("/api/fabrica/publicaciones/999999/imagen")
        assert response.status_code == 404
        assert response.json()["detail"] == "Publicacion sin foto disponible."
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def fotos(tmp_path, monkeypatch):
    modulo = importlib.import_module("app.publicacion_fotos")
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
    return modulo


def origen(asin="B0849JWYD8", marketplace="A1AM78C64UM0Y8", link=None):
    return {
        "asin": asin,
        "images": [
            {
                "marketplaceId": marketplace,
                "images": [
                    {
                        "variant": "PT01",
                        "link": "https://m.media-amazon.com/images/I/otra.jpg",
                        "width": 500,
                        "height": 500,
                    },
                    {
                        "variant": "MAIN",
                        "link": link or "https://m.media-amazon.com/images/I/prueba.jpg",
                        "width": 500,
                        "height": 500,
                    },
                ],
            }
        ],
    }


def cliente(fotos, respuesta=None, fallo=None, contenido=b"\xff\xd8\xfffoto", mime="image/jpeg"):
    llamadas = []

    def manejar(request):
        llamadas.append(request)
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "access-prueba", "expires_in": 3600})
        if request.url.host == "sellingpartnerapi-na.amazon.com":
            if fallo:
                return httpx.Response(fallo, json={"errors": [{"message": "secreto-prueba"}]})
            return httpx.Response(200, json=respuesta if respuesta is not None else origen())
        return httpx.Response(200, content=contenido, headers={"Content-Type": mime})

    return fotos.FotosPublicacion(transport=httpx.MockTransport(manejar)), llamadas


def test_main_correcta_y_cache_evitan_repetir_llamadas(fotos):
    servicio, llamadas = cliente(fotos)
    assert servicio.obtener("amazon_mx", "B0849JWYD8") == (b"\xff\xd8\xfffoto", "image/jpeg")
    assert servicio.obtener("amazon_mx", "B0849JWYD8") == (b"\xff\xd8\xfffoto", "image/jpeg")
    assert len(llamadas) == 3
    assert llamadas[1].method == "GET"
    assert llamadas[1].url.params["includedData"] == "images"
    assert llamadas[1].url.params["marketplaceIds"] == "A1AM78C64UM0Y8"
    assert llamadas[2].url.path.endswith("/prueba.jpg")


@pytest.mark.parametrize(
    "dato",
    [
        origen(asin="B000000000"),
        origen(marketplace="ATVPDKIKX0DER"),
        {"asin": "B0849JWYD8", "images": []},
    ],
)
def test_no_confunde_asin_mercado_o_foto_ausente(fotos, dato):
    servicio, llamadas = cliente(fotos, respuesta=dato)
    assert servicio.obtener("amazon_mx", "B0849JWYD8") is None
    assert len(llamadas) == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://m.media-amazon.com/images/I/a.jpg",
        "https://127.0.0.1/a",
        "https://m.media-amazon.com.evil.test/a",
        "https://user@m.media-amazon.com/images/I/a.jpg",
    ],
)
def test_no_descarga_urls_ajenas(fotos, url):
    servicio, llamadas = cliente(fotos, respuesta=origen(link=url))
    assert servicio.obtener("amazon_mx", "B0849JWYD8") is None
    assert len(llamadas) == 2


def test_error_temporal_se_redacta_y_no_se_repite_en_rafaga(fotos):
    servicio, llamadas = cliente(fotos, fallo=429)
    for _ in range(2):
        with pytest.raises(fotos.FotoNoDisponible, match="temporalmente") as exc:
            servicio.obtener("amazon_mx", "B0849JWYD8")
        assert "secreto-prueba" not in str(exc.value)
    assert len(llamadas) == 2


@pytest.mark.parametrize(
    "contenido,mime",
    [
        (b"<html>error</html>", "image/jpeg"),
        (b"<svg/>", "image/svg+xml"),
        (b"\xff\xd8\xff" + b"x" * 300000, "image/jpeg"),
    ],
)
def test_rechaza_contenido_no_imagen_o_excesivo(fotos, contenido, mime):
    servicio, _ = cliente(fotos, contenido=contenido, mime=mime)
    with pytest.raises(fotos.FotoNoDisponible):
        servicio.obtener("amazon_mx", "B0849JWYD8")


def test_ruta_foto_usa_listing_y_devuelve_imagen(fotos, monkeypatch):
    conn = SimpleNamespace(
        execute=lambda *a: SimpleNamespace(fetchone=lambda: ("amazon_mx", "B0849JWYD8"))
    )
    claves = []

    def obtener(plataforma, asin):
        claves.append((plataforma, asin))
        return b"\xff\xd8\xfffoto", "image/jpeg"

    monkeypatch.setattr(fotos.fotos_publicacion, "obtener", obtener)
    app.dependency_overrides[_conexion_lectura] = lambda: conn
    try:
        response = TestClient(app).get("/api/fabrica/publicaciones/1027/imagen")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "private, max-age=3600"
        assert response.content == b"\xff\xd8\xfffoto"
        assert claves == [("amazon_mx", "B0849JWYD8")]
    finally:
        app.dependency_overrides.clear()


def test_cache_separa_mercados_y_expira(fotos, monkeypatch):
    servicio, llamadas = cliente(fotos)
    reloj = [100.0]
    monkeypatch.setattr(fotos.time, "monotonic", lambda: reloj[0])
    monkeypatch.setattr(fotos.time, "sleep", lambda _: None)
    assert servicio.obtener("amazon_mx", "B0849JWYD8") is not None
    assert servicio.obtener("amazon_us", "B0849JWYD8") is None
    assert len(llamadas) == 4
    reloj[0] += 86401
    assert servicio.obtener("amazon_mx", "B0849JWYD8") is not None
    assert len(llamadas) == 7


def test_cache_acota_memoria_al_recorrer_catalogo(fotos, monkeypatch):
    servicio = fotos.FotosPublicacion()
    llamadas = []

    def consultar(plataforma, asin):
        llamadas.append(asin)
        return b"\xff\xd8\xfffoto", "image/jpeg"

    monkeypatch.setattr(servicio, "_consultar", consultar)
    for n in range(129):
        servicio.obtener("amazon_mx", f"B{n:09d}")
    servicio.obtener("amazon_mx", "B000000128")
    assert len(llamadas) == 129
    servicio.obtener("amazon_mx", "B000000000")
    assert len(llamadas) == 130
