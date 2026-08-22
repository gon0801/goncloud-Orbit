"""Tests del cliente HTTP Amazon Ads READ-ONLY (`app.ads.client`).

Transporte 100% mock (`httpx.MockTransport`): nunca sale una llamada real.
Los "secretos" usados aqui son SIEMPRE valores falsos (`fake-...`), nunca
credenciales reales.

Cubre el DoD de la tarea 1.1:
- token vencido -> refresh -> retry (401 dispara UN refresh forzado + UN
  retry, nada mas);
- 429 -> backoff+jitter -> exito, respetando `Retry-After` cuando viene;
- 5xx agotado -> error tras exactamente 4 intentos;
- 403 NO dispara refresh;
- redaccion: ningun secreto (client_secret/refresh_token, firma de URL,
  password de DSN) aparece en logs ni en la cadena de excepciones
  (`str`/`repr`/`__cause__`/`__context__`);
- superficie publica EXACTA del cliente (sin metodos de mutacion de
  campanas) y el guard read-only rechazando bypasses;
- config de credenciales: carga OK, `repr` redactado, error sin valores;
- refresh de token encapsulado con margen de 60s antes de `expires_in`.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.ads.client import (
    AdsApiError,
    AdsAuthError,
    AdsClient,
    AdsClientError,
    MutationNotAllowedError,
)
from app.ads.config import AdsConfigError, AdsCredentials
from app.db import OrbitDbError, connect
from app.redaction import redact_dsn

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"


def _fake_credentials() -> AdsCredentials:
    return AdsCredentials(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        refresh_token=FAKE_REFRESH_TOKEN,
    )


def _token_response(n: int = 1, *, expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(200, json={"access_token": f"fake-access-{n}", "expires_in": expires_in})


def make_client(
    handler,
    *,
    credentials: AdsCredentials | None = None,
    sleep=None,
    clock=None,
    base_url: str = "https://advertising-api.amazon.com",
) -> AdsClient:
    transport = httpx.MockTransport(handler)
    kwargs: dict = {"sleep": sleep if sleep is not None else (lambda seconds: None)}
    if clock is not None:
        kwargs["clock"] = clock
    return AdsClient(
        credentials or _fake_credentials(),
        base_url=base_url,
        transport=transport,
        **kwargs,
    )


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


# ---------------------------------------------------------------------------
# 1. Token vencido -> refresh -> retry
# ---------------------------------------------------------------------------


def test_401_dispara_un_refresh_forzado_y_un_retry():
    token_calls: list[httpx.Request] = []
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_calls.append(request)
            return _token_response(len(token_calls))
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(401, json={"error": "Unauthorized"})
        return httpx.Response(200, json={"profiles": []})

    client = make_client(handler)
    resp = client.get("/v2/profiles")

    assert resp.status_code == 200
    assert resp.json() == {"profiles": []}
    assert len(token_calls) == 2, "el primer token + UN refresh forzado tras el 401"
    assert len(api_calls) == 2, "la llamada original + UN retry, nada mas"


def test_401_persistente_no_reintenta_una_segunda_vez():
    token_calls: list[httpx.Request] = []
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_calls.append(request)
            return _token_response(len(token_calls))
        api_calls.append(request)
        return httpx.Response(401, json={"error": "Unauthorized"})

    client = make_client(handler)
    with pytest.raises(AdsApiError):
        client.get("/v2/profiles")

    assert len(token_calls) == 2, "solo UN refresh forzado, no uno por intento"
    assert len(api_calls) == 2, "llamada original + UN retry; el 401 no se compone con retries"


# ---------------------------------------------------------------------------
# 2. 429 -> backoff+jitter -> exito
# ---------------------------------------------------------------------------


def test_429_backoff_con_jitter_hasta_exito():
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        if len(api_calls) < 3:
            return httpx.Response(429, json={"error": "Too Many Requests"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    client = make_client(handler, sleep=sleeps.append)
    resp = client.get("/v2/profiles")

    assert resp.status_code == 200
    assert len(api_calls) == 3
    assert len(sleeps) == 2, "dos backoffs antes del intento exitoso"
    assert sleeps[0] < sleeps[1], "el backoff crece entre reintentos"
    # base 2**(intento-1) con jitter acotado a +25%: intento1 en [1, 1.25], intento2 en [2, 2.5]
    assert 1.0 <= sleeps[0] <= 1.25
    assert 2.0 <= sleeps[1] <= 2.5


def test_429_respeta_retry_after():
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    client = make_client(handler, sleep=sleeps.append)
    resp = client.get("/v2/profiles")

    assert resp.status_code == 200
    assert sleeps == [7.0]


def test_429_retry_after_negativo_cae_a_backoff_normal():
    """Retry-After: -5 no debe reventar time.sleep (ValueError) ni respetarse:
    fail-closed -> cae al backoff exponencial normal."""
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "-5"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    client = make_client(handler, sleep=sleeps.append)
    resp = client.get("/v2/profiles")

    assert resp.status_code == 200
    assert len(sleeps) == 1
    # backoff normal del intento 1: base=1, jitter en [0, 0.25] -> nunca negativo
    assert 1.0 <= sleeps[0] <= 1.25


def test_429_retry_after_fuera_de_rango_cae_a_backoff_normal():
    """Retry-After: 9999 no debe dormirse tal cual: excede el tope sensato
    (60s) -> cae al backoff exponencial normal."""
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "9999"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    client = make_client(handler, sleep=sleeps.append)
    resp = client.get("/v2/profiles")

    assert resp.status_code == 200
    assert len(sleeps) == 1
    assert sleeps[0] < 60.0
    assert 1.0 <= sleeps[0] <= 1.25


# ---------------------------------------------------------------------------
# 3. 5xx agotado -> error
# ---------------------------------------------------------------------------


def test_5xx_agotado_lanza_tras_exactamente_4_intentos():
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler, sleep=lambda seconds: None)
    with pytest.raises(AdsApiError) as excinfo:
        client.get("/v2/profiles")

    assert len(api_calls) == 4
    assert "500" in str(excinfo.value)
    assert "/v2/profiles" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. 403 NO dispara refresh
# ---------------------------------------------------------------------------


def test_403_no_dispara_refresh():
    token_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_calls.append(request)
            return _token_response(len(token_calls))
        return httpx.Response(403, json={"error": "forbidden"})

    client = make_client(handler)
    with pytest.raises(AdsApiError):
        client.get("/v2/profiles")

    assert len(token_calls) == 1, "403 no es un problema de token: cero refresh extra"


# ---------------------------------------------------------------------------
# 5. Redaccion
# ---------------------------------------------------------------------------


def test_redaccion_refresh_lwa_fallando(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(
                400,
                json={
                    "error": "invalid_client",
                    "error_description": (
                        f"bad credentials: {FAKE_CLIENT_SECRET} / {FAKE_REFRESH_TOKEN}"
                    ),
                },
            )
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    with pytest.raises(AdsAuthError) as excinfo:
        client.get("/v2/profiles")

    for exc in _exception_chain(excinfo.value):
        assert FAKE_CLIENT_SECRET not in str(exc)
        assert FAKE_CLIENT_SECRET not in repr(exc)
        assert FAKE_REFRESH_TOKEN not in str(exc)
        assert FAKE_REFRESH_TOKEN not in repr(exc)

    assert FAKE_CLIENT_SECRET not in caplog.text
    assert FAKE_REFRESH_TOKEN not in caplog.text


def test_redaccion_download_url_firmada_fallando(caplog):
    caplog.set_level(logging.DEBUG)
    signature = "AKIAFAKESIGNATURE1234567890=="
    signed_url = (
        "https://reports-bucket.s3.amazonaws.com/reports/abc123.gz"
        f"?X-Amz-Signature={signature}&X-Amz-Expires=900"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(403, text="Forbidden")

    client = make_client(handler)
    with pytest.raises(AdsApiError) as excinfo:
        client.download(signed_url)

    for exc in _exception_chain(excinfo.value):
        assert signature not in str(exc)
        assert signature not in repr(exc)
    assert signature not in caplog.text

    # el host y el path SI se conservan (solo se elimina la query firmada)
    assert "reports-bucket.s3.amazonaws.com" in str(excinfo.value)
    assert "/reports/abc123.gz" in str(excinfo.value)


def test_redaccion_db_connect_dsn_password(caplog):
    caplog.set_level(logging.DEBUG)
    dsn = "postgresql://orbit_ingest:fake-pw-123@127.0.0.1:9/orbit"

    with pytest.raises(OrbitDbError) as excinfo:
        connect(dsn, connect_timeout=1)

    for exc in _exception_chain(excinfo.value):
        assert "fake-pw-123" not in str(exc)
        assert "fake-pw-123" not in repr(exc)
    assert "fake-pw-123" not in caplog.text


def test_redaccion_db_connect_dsn_password_con_arroba_literal(caplog):
    """Password con `@` literal (`fake@pw@123`): la regex debe anclar al
    ULTIMO `@` de la cadena (el host no lleva `@`), no al primero -- si
    corta en el primero, el resto de la password ('pw@123') queda sin
    redactar y el host se lee mal."""
    caplog.set_level(logging.DEBUG)
    dsn = "postgresql://orbit_ingest:fake@pw@123@127.0.0.1:9/orbit"

    # redact_dsn en aislado: la password COMPLETA se redacta y el host
    # (127.0.0.1) sobrevive intacto -- el anclaje no se lo come.
    redacted = redact_dsn(dsn)
    assert redacted == "postgresql://orbit_ingest:***@127.0.0.1:9/orbit"
    assert "fake@pw@123" not in redacted
    assert "pw@123" not in redacted

    with pytest.raises(OrbitDbError) as excinfo:
        connect(dsn, connect_timeout=1)

    for exc in _exception_chain(excinfo.value):
        assert "fake@pw@123" not in str(exc)
        assert "fake@pw@123" not in repr(exc)
        assert "pw@123" not in str(exc)
        assert "pw@123" not in repr(exc)
    assert "fake@pw@123" not in caplog.text
    assert "pw@123" not in caplog.text


# ---------------------------------------------------------------------------
# 6. Superficie de solo lectura
# ---------------------------------------------------------------------------


def test_superficie_publica_exacta():
    public = {
        name
        for name, value in vars(AdsClient).items()
        if not name.startswith("_") and callable(value)
    }
    assert public == {"get", "create_report", "get_report", "download"}

    assert issubclass(AdsApiError, AdsClientError)
    assert issubclass(AdsAuthError, AdsClientError)
    assert issubclass(MutationNotAllowedError, AdsClientError)


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_metodos_de_mutacion_bloqueados_siempre(method):
    client = make_client(lambda request: httpx.Response(200))
    with pytest.raises(MutationNotAllowedError):
        client._request(method, "/reporting/reports")


def test_post_fuera_de_report_request_bloqueado():
    client = make_client(lambda request: httpx.Response(200))
    with pytest.raises(MutationNotAllowedError):
        client._request("POST", "/sp/campaigns")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "https://evil.example.com/steal"),
        ("POST", "/reporting//reports"),
        ("GET", "/reporting/reports/../../sp/campaigns"),
        ("GET", "/reporting%2Freports"),
        ("POST", "/reporting/reports?x=1"),
    ],
)
def test_bypasses_del_guard_rechazados(method, path):
    client = make_client(lambda request: httpx.Response(200))
    with pytest.raises(MutationNotAllowedError):
        client._request(method, path)


def test_create_report_es_el_unico_post_permitido():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        calls.append(request)
        return httpx.Response(200, json={"reportId": "r-1"})

    client = make_client(handler)
    resp = client.create_report({"reportType": "spCampaigns"}, profile_id=123)

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/reporting/reports"


def test_download_sigue_redirect_como_get_con_tope_de_saltos():
    hops: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(request)
        if len(hops) == 1:
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/final.gz"})
        return httpx.Response(200, content=b"contenido-del-reporte")

    client = make_client(handler)
    resp = client.download("https://reports-bucket.s3.amazonaws.com/reports/abc.gz")

    assert resp.status_code == 200
    assert resp.content == b"contenido-del-reporte"
    assert len(hops) == 2
    assert hops[0].method == "GET"
    assert hops[1].method == "GET", "el redirect JAMAS cambia el metodo"


def test_download_tiene_tope_de_saltos():
    def handler(request: httpx.Request) -> httpx.Response:
        # redirect infinito a si mismo
        return httpx.Response(302, headers={"Location": str(request.url)})

    client = make_client(handler)
    with pytest.raises(AdsApiError):
        client.download("https://cdn.example.com/loop")


# ---------------------------------------------------------------------------
# 7. Config de credenciales
# ---------------------------------------------------------------------------


def test_config_carga_ok_y_repr_redactado(tmp_path):
    (tmp_path / "amazon_ads_config.json").write_text(
        json.dumps(
            {
                "client_id": FAKE_CLIENT_ID,
                "client_secret": FAKE_CLIENT_SECRET,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "amazon_ads_tokens.json").write_text(
        json.dumps(
            {
                "access_token": "fake-access-token",
                "refresh_token": FAKE_REFRESH_TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
                "obtained_at": "2026-08-01T00:00:00Z",
                "expires_at": "2026-08-01T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    creds = AdsCredentials.from_secrets_dir(tmp_path)

    assert creds.client_id == FAKE_CLIENT_ID
    assert creds.client_secret == FAKE_CLIENT_SECRET
    assert creds.refresh_token == FAKE_REFRESH_TOKEN

    rep = repr(creds)
    assert FAKE_CLIENT_SECRET not in rep
    assert FAKE_REFRESH_TOKEN not in rep
    assert str(creds) == rep


def test_config_archivo_faltante_error_sin_valores(tmp_path):
    with pytest.raises(AdsConfigError) as excinfo:
        AdsCredentials.from_secrets_dir(tmp_path)
    assert "amazon_ads_config.json" in str(excinfo.value)


def test_config_clave_faltante_error_sin_valores(tmp_path):
    (tmp_path / "amazon_ads_config.json").write_text(
        json.dumps({"client_id": FAKE_CLIENT_ID}), encoding="utf-8"
    )
    (tmp_path / "amazon_ads_tokens.json").write_text(
        json.dumps({"refresh_token": FAKE_REFRESH_TOKEN}), encoding="utf-8"
    )
    with pytest.raises(AdsConfigError) as excinfo:
        AdsCredentials.from_secrets_dir(tmp_path)
    assert FAKE_REFRESH_TOKEN not in str(excinfo.value)
    assert "client_secret" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 8. Refresh encapsulado: margen de 60s antes de expires_in
# ---------------------------------------------------------------------------


def test_token_se_renueva_con_margen_de_60s():
    token_calls: list[httpx.Request] = []
    clock = {"now": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_calls.append(request)
            return _token_response(len(token_calls), expires_in=100)
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler, clock=lambda: clock["now"])

    client.get("/v2/profiles")
    assert len(token_calls) == 1

    # a los 30s, dentro de la vida util (100 - 60 margen = 40s): sigue cacheado
    clock["now"] = 30.0
    client.get("/v2/profiles")
    assert len(token_calls) == 1

    # a los 41s ya entramos al margen de 60s antes del expires_in real -> refresca
    clock["now"] = 41.0
    client.get("/v2/profiles")
    assert len(token_calls) == 2


# ---------------------------------------------------------------------------
# 9. Fixes de la cross-review Codex (ronda 1): cada bug con su test
# ---------------------------------------------------------------------------


def test_redaccion_dsn_con_espacio_inicial(caplog):
    """[alta] Un DSN con espacio inicial (clasico de .env) tambien se redacta."""
    dsn = "  postgresql://orbit_ingest:fake-lead-pw@127.0.0.1:9/orbit"
    assert "fake-lead-pw" not in redact_dsn(dsn)

    with caplog.at_level(logging.DEBUG), pytest.raises(OrbitDbError) as excinfo:
        connect(dsn, connect_timeout=1)
    for exc in _exception_chain(excinfo.value):
        assert "fake-lead-pw" not in str(exc)
        assert "fake-lead-pw" not in repr(exc)
    assert "fake-lead-pw" not in caplog.text


def test_redact_url_elimina_userinfo():
    """[baja] https://user:pass@host no debe fugar el userinfo en logs/errores."""
    from app.redaction import redact_url, scrub

    redacted = redact_url("https://user:fake-basic-pw@reports.example.com/f.gz?X-Amz-Signature=s")
    assert redacted == "https://reports.example.com/f.gz"
    assert "fake-basic-pw" not in redacted
    # la password del userinfo quedo ademas registrada para scrub()
    assert "fake-basic-pw" not in scrub("echo fake-basic-pw")


def test_config_json_no_objeto(tmp_path):
    """[baja] JSON valido pero no-objeto -> AdsConfigError, jamas AttributeError."""
    (tmp_path / "amazon_ads_config.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "amazon_ads_tokens.json").write_text(
        json.dumps({"refresh_token": FAKE_REFRESH_TOKEN}), encoding="utf-8"
    )
    with pytest.raises(AdsConfigError):
        AdsCredentials.from_secrets_dir(tmp_path)


def test_config_valor_no_string(tmp_path):
    """[baja] Una clave presente pero no-string -> AdsConfigError sin valores."""
    (tmp_path / "amazon_ads_config.json").write_text(
        json.dumps({"client_id": FAKE_CLIENT_ID, "client_secret": 12345}), encoding="utf-8"
    )
    (tmp_path / "amazon_ads_tokens.json").write_text(
        json.dumps({"refresh_token": FAKE_REFRESH_TOKEN}), encoding="utf-8"
    )
    with pytest.raises(AdsConfigError) as excinfo:
        AdsCredentials.from_secrets_dir(tmp_path)
    assert "12345" not in str(excinfo.value)


def test_create_report_fallo_de_red_no_reintenta():
    """[media] POST no idempotente + fallo AMBIGUO (red) -> fail-closed sin retry."""
    api_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        raise httpx.ConnectError("boom", request=request)

    client = make_client(handler, sleep=sleeps.append)
    with pytest.raises(AdsApiError) as excinfo:
        client.create_report({"reportTypeId": "spCampaigns"}, profile_id=1)

    assert len(api_calls) == 1, "sin retry: un duplicado seria posible"
    assert sleeps == []
    assert "no idempotente" in str(excinfo.value)


def test_create_report_5xx_no_reintenta():
    """[media] POST no idempotente + 5xx (ambiguo) -> fail-closed sin retry."""
    api_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler, sleep=sleeps.append)
    with pytest.raises(AdsApiError):
        client.create_report({"reportTypeId": "spCampaigns"}, profile_id=1)

    assert len(api_calls) == 1
    assert sleeps == []


def test_create_report_429_si_reintenta():
    """[media] 429 = rechazado SIN procesar -> el POST si puede reintentarse."""
    api_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        api_calls.append(request)
        if len(api_calls) == 1:
            return httpx.Response(429, json={"error": "throttled"})
        return httpx.Response(200, json={"reportId": "r1"})

    client = make_client(handler, sleep=sleeps.append)
    resp = client.create_report({"reportTypeId": "spCampaigns"}, profile_id=1)

    assert resp.status_code == 200
    assert len(api_calls) == 2
    assert len(sleeps) == 1


def test_refresh_lwa_reintenta_5xx_transitorio():
    """[media] El refresh LWA es idempotente: un 5xx transitorio NO aborta."""
    token_calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            token_calls.append(request)
            if len(token_calls) < 3:
                return httpx.Response(500, json={"error": "hiccup"})
            return _token_response(len(token_calls))
        return httpx.Response(200, json={"profiles": []})

    client = make_client(handler, sleep=sleeps.append)
    resp = client.get("/v2/profiles")

    assert resp.status_code == 200
    assert len(token_calls) == 3, "dos 5xx transitorios + el exito"
    assert len(sleeps) == 2


def test_download_rechaza_http():
    """[media] download() solo acepta https: nada de descargas en claro."""
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        api_calls.append(request)
        return httpx.Response(200, content=b"x")

    client = make_client(handler)
    with pytest.raises(AdsApiError) as excinfo:
        client.download("http://reports.example.com/f.gz")

    assert api_calls == [], "la URL http jamas debe salir a la red"
    assert "no-https" in str(excinfo.value)


def test_download_redirect_a_http_rechazado():
    """[media] Un redirect con downgrade a http se rechaza (la firma iria en claro)."""
    api_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        api_calls.append(request)
        return httpx.Response(302, headers={"location": "http://evil.example.com/f.gz"})

    client = make_client(handler)
    with pytest.raises(AdsApiError) as excinfo:
        client.download("https://reports.example.com/f.gz?X-Amz-Signature=fake-sig")

    assert len(api_calls) == 1, "el salto http no debe emitirse"
    assert "no-https" in str(excinfo.value)
    assert "fake-sig" not in str(excinfo.value)
