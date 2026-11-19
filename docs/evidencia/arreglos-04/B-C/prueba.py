"""Sonda pura B-C / TN02: lector unico estricto con transporte simulado.

Sin base ni red: simula el transporte HTTP de `AdsWriteClient` y comprueba
que contenedor ausente/malformado, elemento invalido, filtro ignorado y
`nextToken` persistente lanzan `ListaIncompleta` y producen cero POST/delete.
"""

from __future__ import annotations

from contextlib import nullcontext, suppress
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from app import apply as apply_mod
from app import apply_cola, apply_harvest, apply_harvest_reconciliacion, bloqueo_dueno_efecto
from app.ads.client import AdsApiError, AdsAuthError
from app.ads.config import AdsCredentials
from app.ads.write import AdsWriteClient


def _cliente_falso(handler) -> AdsWriteClient:
    return AdsWriteClient(
        AdsCredentials(
            client_id="fake-bc-probe-id",
            client_secret="fake-bc-probe-secret",
            refresh_token="fake-bc-probe-refresh",
        ),
        platform="amazon_us",
        profile_id=404040,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


def _aplicador_falso(conn, handler) -> apply_mod.Aplicador:
    return apply_mod.Aplicador(
        conn,
        platform="amazon_us",
        profile_id=404040,
        credentials=AdsCredentials(
            client_id="fake-bc-probe-id",
            client_secret="fake-bc-probe-secret",
            refresh_token="fake-bc-probe-refresh",
        ),
        cycle_id_ejecutor=1,
        owner="probe:bc",
        job_key="ads_optimizer:amazon_us",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


def _handler_caso(caso: str):
    vistos: list[httpx.Request] = []
    n = {"list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok-bc", "expires_in": 3600})
        vistos.append(request)
        path = request.url.path
        if path in ("/sp/keywords/list", "/sp/negativeKeywords/list"):
            n["list"] += 1
            clave = "negativeKeywords" if path.startswith("/sp/neg") else "keywords"
            if caso == "missing":
                return httpx.Response(200, json={})
            if caso == "malformado":
                return httpx.Response(200, json={clave: "no-lista"})
            if caso == "item_incompleto":
                return httpx.Response(200, json={clave: [{"adGroupId": "7101"}]})
            if caso == "fuera_de_filtro":
                return httpx.Response(
                    200,
                    json={
                        clave: [
                            {
                                "adGroupId": "9999",
                                "keywordId": "id-ajeno",
                                "keywordText": "termino",
                                "matchType": (
                                    "NEGATIVE_EXACT" if clave == "negativeKeywords" else "EXACT"
                                ),
                                "state": "ENABLED",
                            }
                        ]
                    },
                )
            if caso == "token_repetido":
                return httpx.Response(200, json={clave: [], "nextToken": "mismo"})
            if caso == "token_persistente":
                return httpx.Response(200, json={clave: [], "nextToken": f"pag-{n['list']}"})
        if path == "/sp/negativeKeywords" and request.method == "POST":
            return httpx.Response(
                207,
                json={
                    "negativeKeywords": {
                        "error": [],
                        "success": [{"index": 0, "negativeKeywordId": "n-dup"}],
                    }
                },
            )
        if path.endswith("/delete") and request.method == "POST":
            clave = "negativeKeywords" if path.startswith("/sp/neg") else "keywords"
            campo = "negativeKeywordId" if path.startswith("/sp/neg") else "keywordId"
            return httpx.Response(207, json={clave: {"error": [], "success": [{campo: "x"}]}})
        raise AssertionError(f"HTTP inesperado: {request.method} {path}")

    return handler, vistos


def _comprueba_reversa_automatica(conn, ctx, caso: str) -> None:
    vistos: list[httpx.Request] = []
    pag = {"n": 0, "auth": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            pag["auth"] += 1
            if caso == "auth_en_lectura":
                if pag["auth"] == 1:
                    return httpx.Response(200, json={"access_token": "tok-bc", "expires_in": 30})
                return httpx.Response(400, json={"error": "temporarily_unavailable"})
            return httpx.Response(200, json={"access_token": "tok-bc", "expires_in": 3600})
        vistos.append(request)
        if request.url.path == "/sp/keywords/list":
            return httpx.Response(200, json={"keywords": [], "totalResults": 0})
        if request.url.path == "/sp/negativeKeywords/list":
            pag["n"] += 1
            if caso == "missing":
                return httpx.Response(200, json={})
            if caso == "malformado":
                return httpx.Response(200, json={"negativeKeywords": "no-lista"})
            if caso == "token_repetido":
                return httpx.Response(200, json={"negativeKeywords": [], "nextToken": "m"})
            return httpx.Response(200, json={"negativeKeywords": [], "nextToken": f"p-{pag['n']}"})
        if request.url.path.endswith("/delete") and request.method == "POST":
            return httpx.Response(
                207, json={"keywords": {"error": [], "success": [{"keywordId": "k-7"}]}}
            )
        raise AssertionError(f"HTTP inesperado: {request.method} {request.url.path}")

    fallos: list[str] = []
    apply_harvest._falla_job = lambda _c, _j, m, **_k: fallos.append(m) or ("failed", None)
    try:
        job = apply_harvest._Job(
            3,
            12,
            "termino",
            5,
            "exact_created",
            {"keyword_id": "k-7", "keyword_creada": True, "negative_creada": True},
            "amazon_us",
        )
        with suppress(AdsApiError, AdsAuthError):
            apply_harvest._paso_readback(conn, _aplicador_falso(conn, handler), job, ctx, 91)
        deletes = [r.url.path for r in vistos if r.url.path.endswith("/delete")]
        assert deletes == [], f"reversa borro con {caso}: {deletes}"
        assert fallos == [], f"reversa cerro job con {caso}: {fallos}"
        assert job.fase == "exact_created", f"job cambio de fase con {caso}: {job.fase}"
    finally:
        apply_harvest._falla_job = lambda *_a, **_k: ("failed", None)


def main() -> None:
    conn = Mock()
    conn.transaction.side_effect = nullcontext
    conn.execute.return_value.fetchone.return_value = None
    ctx = apply_harvest._Contexto(
        plataforma="amazon_us",
        grupo_ext="7101",
        campana_ext="7001",
        destino_grupo="8101",
        destino_campana="8001",
        default_bid=Decimal("1.00"),
        moneda="USD",
        floor=Decimal("0.20"),
        ceiling=Decimal("2.50"),
        resuelto_por=None,
        grupo_id=None,
    )
    orig_ledger = apply_mod._ledger
    orig_sella = apply_mod._sella_ledger
    orig_falla = apply_harvest._falla_job
    try:
        apply_mod._ledger = lambda *_a, **_k: 1
        apply_mod._sella_ledger = lambda *_a, **_k: None
        apply_harvest._falla_job = lambda *_a, **_k: ("failed", None)
        for caso in ("missing", "malformado", "token_repetido", "token_persistente"):
            h_neg, v_neg = _handler_caso(caso)
            job_p = apply_harvest._Job(1, 10, "termino", 5, "pending", {}, "amazon_us")
            with suppress(AdsApiError):
                apply_harvest._paso_negative(conn, _aplicador_falso(conn, h_neg), job_p, ctx, None)
            posts = [r for r in v_neg if r.url.path == "/sp/negativeKeywords"]
            assert posts == [], f"creacion negative posteada con {caso}: {len(posts)}"

            h_rb, v_rb = _handler_caso(caso)
            job_e = apply_harvest._Job(
                2,
                11,
                "termino",
                5,
                "exact_created",
                {
                    "negative_id": "n-1",
                    "negative_creada": True,
                    "keyword_id": "k-1",
                    "keyword_creada": True,
                },
                "amazon_us",
            )
            with suppress(AdsApiError):
                apply_harvest._paso_readback(conn, _aplicador_falso(conn, h_rb), job_e, ctx, None)
            deletes = [r for r in v_rb if r.url.path.endswith("/delete")]
            assert deletes == [], f"readback borro con {caso}: {[r.url.path for r in deletes]}"
            _comprueba_reversa_automatica(conn, ctx, caso)
        _comprueba_reversa_automatica(conn, ctx, "auth_en_lectura")
    finally:
        apply_mod._ledger = orig_ledger
        apply_mod._sella_ledger = orig_sella
        apply_harvest._falla_job = orig_falla

    orig_gate = apply_mod.gate_ancestros
    orig_modo = apply_cola._modo_efectivo_corte
    try:
        apply_mod.gate_ancestros = lambda *_a: None
        apply_cola._modo_efectivo_corte = lambda *_a, **_k: "live"

        def _exec(sql, params=()):
            if sql == apply_harvest_reconciliacion._SQL_NEGATIVAS_APLICANDO:
                filas = [(1, 5, "termino", 10)]
            elif sql == apply_harvest._SQL_EXTERNALES:
                filas = [("7101", "7001")]
            elif sql == apply_mod._SQL_COUNT_INTENTOS:
                filas = [(1,)]
            else:
                filas = []
            return SimpleNamespace(
                fetchall=lambda: filas, fetchone=lambda: filas[0] if filas else None
            )

        conn.execute.side_effect = _exec
        for caso in ("missing", "token_persistente"):
            h_rec, v_rec = _handler_caso(caso)
            conf, fall = apply_harvest_reconciliacion._reconcilia_negativas(
                conn, _aplicador_falso(conn, h_rec), "amazon_us"
            )
            reposts = [r for r in v_rec if r.url.path == "/sp/negativeKeywords"]
            assert reposts == [] and (conf, fall) == (0, 0), f"reconciliacion muto con {caso}"
    finally:
        apply_mod.gate_ancestros = orig_gate
        apply_cola._modo_efectivo_corte = orig_modo

    assert hasattr(apply_harvest, "ListaIncompleta"), "falta ListaIncompleta"
    assert not hasattr(apply_harvest, "_lista_todos"), "_lista_todos sigue presente"
    assert not hasattr(apply_harvest, "_lista_filtrada"), "_lista_filtrada sigue presente"
    lista_incompleta_cls = apply_harvest.ListaIncompleta

    for caso, causa_esperada, grupos in (
        ("missing", "ambiguo", None),
        ("malformado", "ambiguo", None),
        ("item_incompleto", "ambiguo", None),
        ("fuera_de_filtro", "ambiguo", ("7101",)),
        ("token_repetido", "ambiguo", None),
        ("token_persistente", "truncado", None),
    ):
        handler, _ = _handler_caso(caso)
        cliente = _cliente_falso(handler)
        try:
            res = apply_harvest._lista_completa(cliente, "/sp/negativeKeywords/list", grupos=grupos)
        except lista_incompleta_cls as exc:
            assert exc.causa == causa_esperada, (caso, exc.causa)
        else:
            raise AssertionError(f"_lista_completa devolvio lista utilizable en {caso}: {res}")

    h_amb, _ = _handler_caso("missing")
    assert bloqueo_dueno_efecto._lista_o(_cliente_falso(h_amb), "7101") == ([], "ambiguo")
    h_tr, _ = _handler_caso("token_persistente")
    assert bloqueo_dueno_efecto._lista_o(_cliente_falso(h_tr), "7101") == ([], "truncado")
    print("B-C OK")


if __name__ == "__main__":
    main()
