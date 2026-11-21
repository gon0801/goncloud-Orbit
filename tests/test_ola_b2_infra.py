"""Pruebas de Ola B2: http_base, pooling SP-API, fotos sin bloquear cache y cursor_dict."""

from __future__ import annotations

import threading

import httpx
import pytest

from app import db, estimacion_fees, http_base, publicacion_fotos
from app.spapi import client as spapi_client


def test_validar_ruta_relativa_rechaza_query_y_percent_barra():
    assert http_base.validar_ruta_relativa("/sp/campaigns/list") == "/sp/campaigns/list"
    with pytest.raises(ValueError, match="query"):
        http_base.validar_ruta_relativa("/a?x=1", prohibir_query=True)
    with pytest.raises(ValueError, match="encoding de barra"):
        http_base.validar_ruta_relativa("/a/%2f/b", prohibir_percent_barra=True)
    with pytest.raises(ValueError, match="traversal"):
        http_base.validar_ruta_relativa("/a/../b")
    with pytest.raises(ValueError, match="URLs absolutas"):
        http_base.validar_ruta_relativa("/https://evil.test")


def test_estimacion_fees_retry_after_rechaza_nan_inf_y_negativos():
    cliente = estimacion_fees.ProductFeesClient(
        credentials={
            "lwa_app_id": "id",
            "lwa_client_secret": "sec",
            "refresh_token": "ref",
        },
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
    )
    try:
        for malo in ("NaN", "inf", "-inf", "-5", "basura"):
            resp = httpx.Response(429, headers={"Retry-After": malo})
            assert cliente._retry_after_seconds(resp) == 1.0
        resp_ok = httpx.Response(429, headers={"Retry-After": "999"})
        assert cliente._retry_after_seconds(resp_ok) == estimacion_fees.RETRY_AFTER_MAX_SECONDS
    finally:
        cliente.close()


def test_spapi_client_reutiliza_pool_http_y_cierra():
    llamadas = []

    def handler(req: httpx.Request) -> httpx.Response:
        llamadas.append(req.url.path)
        if req.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok-b2", "expires_in": 3600})
        return httpx.Response(200, json={"payload": []})

    with spapi_client.SpapiClient(
        credentials={"lwa_app_id": "id", "lwa_client_secret": "sec", "refresh_token": "ref"},
        transport=httpx.MockTransport(handler),
    ) as cli:
        r1 = cli.get("/sellers/v1/marketplaceParticipations")
        pool1 = cli._http
        r2 = cli.get("/sellers/v1/marketplaceParticipations")
        assert r1.status_code == 200 and r2.status_code == 200
        assert cli._http is pool1
        assert cli.token_acceso() == "tok-b2"
    assert cli._http is None


def test_fotos_cache_hit_no_se_bloquea_con_fetch_en_vuelo():
    en_vuelo = threading.Event()
    liberar = threading.Event()
    servicio = publicacion_fotos.FotosPublicacion()

    def consultar_falso(plataforma: str, asin: str):
        if asin == "B000000001":
            return b"\xff\xd8\xffok", "image/jpeg"
        en_vuelo.set()
        assert liberar.wait(timeout=5)
        return b"\xff\xd8\xffotro", "image/jpeg"

    servicio._consultar = consultar_falso  # type: ignore[method-assign]
    assert servicio.obtener("amazon_mx", "B000000001") == (b"\xff\xd8\xffok", "image/jpeg")

    hilo = threading.Thread(target=servicio.obtener, args=("amazon_mx", "B000000002"))
    hilo.start()
    try:
        assert en_vuelo.wait(timeout=2)
        # Cache hit debe devolver inmediatamente sin esperar a que termine B000000002.
        assert servicio.obtener("amazon_mx", "B000000001") == (b"\xff\xd8\xffok", "image/jpeg")
    finally:
        liberar.set()
        hilo.join(timeout=5)


def test_cursor_dict_no_muta_row_factory():
    class _CurFake:
        def __init__(self, rf):
            self.rf = rf

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

    class _ConnFake:
        row_factory = "tuple_row_original"

        def cursor(self, *, row_factory=None):
            return _CurFake(row_factory)

    conn = _ConnFake()
    with db.cursor_dict(conn) as cur:
        assert cur.rf is db.dict_row
    assert conn.row_factory == "tuple_row_original"
