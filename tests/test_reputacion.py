"""Tests de la ingesta A.2 (app/reputacion.py + CLI snapshot).

(a) UNITARIOS: planes puros con el shape E/0.3 (MeLi).
(b) CLIENTES: MockTransport (jamas red real); guardia y refresh.
(c) INTEGRACION: PG real desechable (0001+0024+0025); transaccion,
    idempotencia, conciliacion, redaction y CLI ejecutado de verdad.
    La escritura entra por _corre_meli (el camino del cron) o por
    fetch_seller/fetch_questions + _escribe_todo_meli.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
import urllib.parse
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import httpx
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import cli as app_cli
from app.redaction import REDACTED, register_secret, scrub
from app.reputacion import (
    ClienteMeli,
    MeliCredentials,
    ReputacionError,
    _corre_meli,
    _escribe_todo_meli,
    _pagina,
    ejecuta_snapshot,
    fetch_questions,
    fetch_seller,
    plan_question,
    plan_seller,
    plan_snapshot_meli,
)

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

UTC = dt.UTC
OBS = dt.datetime(2026, 9, 8, 22, 0, 0, tzinfo=UTC)
OBS2 = dt.datetime(2026, 9, 8, 23, 0, 0, tzinfo=UTC)
FETCH = dt.datetime(2026, 9, 8, 21, 30, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Fakes con el shape E/0.3 (MeLi)
# ---------------------------------------------------------------------------

_ME = {"id": 135734858, "nickname": "ELECTRONICSHOUSE"}
_SCAN_1 = {
    "results": ["MLM1"],
    "paging": {"total": 2},
    "scroll_id": "SCROLL1",
}
_SCAN_2 = {"results": ["MLM0"], "paging": {"total": 2}}
_ITEM = {"id": "MLM1", "health": 0.87, "status": "active", "sub_status": []}
_REVIEWS_CON_DATOS = {
    "rating_average": 4.3,
    "paging": {"total": 4, "limit": 50, "offset": 0},
    "rating_levels": {
        "one_star": 0,
        "two_star": 0,
        "three_star": 1,
        "four_star": 1,
        "five_star": 2,
    },
    "reviews": [
        {
            "id": 3025304027,
            "rate": 5,
            "title": "Excelente",
            "content": "Buena compra.",
            "date_created": "2026-07-25T15:38:54Z",
            "status": "published",
        },
        {
            "id": 3025304028,
            "rate": 1,
            "title": "Malo",
            "content": "Llego roto.",
            "date_created": "2026-08-01T10:00:00Z",
            "status": "published",
        },
    ],
}
# Grok XR-1 ALTA-2: segunda pagina real (total=3, 2+1).
_REVIEWS_PAGINA_2 = {
    "paging": {"total": 3, "limit": 50, "offset": 2},
    "reviews": [
        {
            "id": 3025304029,
            "rate": 4,
            "title": "Bien",
            "content": "Cumple.",
            "date_created": "2026-08-05T10:00:00Z",
            "status": "published",
        },
    ],
}
_REVIEWS_VACIAS = {
    "rating_average": 0,
    "paging": {"total": 0, "limit": 50, "offset": 0},
    "rating_levels": {
        "one_star": 0,
        "two_star": 0,
        "three_star": 0,
        "four_star": 0,
        "five_star": 0,
    },
    "reviews": [],
}
_USER_REP = {
    "id": 135734858,
    "seller_reputation": {
        "level_id": "5_green",
        "power_seller_status": "silver",
        "transactions": {
            "total": 662,
            "completed": 635,
            "canceled": 27,
            "ratings": {"positive": 1, "neutral": 0, "negative": 0},
        },
    },
}
_CLAIMS = {
    "paging": {"total": 2, "limit": 50, "offset": 0},
    "data": [
        {"id": 1, "resource": "order", "status": "open", "stage": "dispute"},
        {"id": 2, "resource": "order", "status": "closed", "stage": "dispute"},
    ],
}
_QUESTIONS = {
    "total": 2,
    "limit": 50,
    "questions": [
        {
            "id": 13550511917,
            "item_id": "MLM1",
            "status": "UNANSWERED",
            "date_created": "2026-03-25T12:31:13.243906-04:00",
            "text": "Tiene costo extra?",
            "answer": None,
        },
        {
            "id": 13528531156,
            "item_id": "MLM1",
            "status": "ANSWERED",
            "date_created": "2026-02-18T21:32:09.655275-04:00",
            "text": "De que ciudad envian?",
            "answer": {"text": "Se envia de CDMX", "date_created": "2026-02-19T11:57:00-04:00"},
        },
    ],
}


def _creds_meli(tmp_path: Path) -> MeliCredentials:
    """El pase tal como lo deja el cron del bridge: access_token + user_id
    + scope, SIN refresh_token ni client_id/client_secret."""
    (tmp_path / "meli_tokens.json").write_text(
        json.dumps({"access_token": "ACCESO", "user_id": 135734858, "scope": "read write"}),
        encoding="utf-8",
    )
    return MeliCredentials.from_secrets_dir(tmp_path)


# ---------------------------------------------------------------------------
# (a) UNITARIOS: planes puros
# ---------------------------------------------------------------------------


def test_plan_meli_total_cero_rating_null_count_cero():
    """E/0.3: avg=0 con total=0 es sin-dato: NULL, no rating 0."""
    snap, eventos, skips = plan_snapshot_meli("MLM0", {"id": "MLM0"}, _REVIEWS_VACIAS, FETCH)
    assert snap is not None
    assert snap.rating is None
    assert snap.review_count == 0
    assert eventos == []
    assert not skips


def test_plan_meli_con_datos_y_reviews():
    snap, eventos, skips = plan_snapshot_meli("MLM1", _ITEM, _REVIEWS_CON_DATOS, FETCH)
    assert snap is not None
    assert snap.rating == 4.3
    assert snap.review_count == 4
    assert snap.extra["health"] == 0.87
    assert snap.extra["levels"]["five_star"] == 2
    assert [e.review_external_id for e in eventos] == ["3025304027", "3025304028"]
    assert eventos[1].rating == 1
    assert eventos[0].publicada
    assert eventos[0].published_at == dt.datetime(2026, 7, 25, 15, 38, 54, tzinfo=UTC)
    assert not skips


def test_plan_meli_avg_invalido_no_inventa():
    malas = dict(_REVIEWS_CON_DATOS, rating_average=9.9)
    snap, _, skips = plan_snapshot_meli("MLM1", _ITEM, malas, FETCH)
    assert snap is not None and snap.rating is None
    assert "meli: rating_average fuera de [1,5] (NULL, no inventado)" in skips


def test_plan_meli_review_sin_id_y_rate_malo():
    malas = dict(
        _REVIEWS_CON_DATOS,
        reviews=[{"rate": 5, "title": "x"}, {"id": 9, "rate": 99, "status": "published"}],
    )
    snap, eventos, skips = plan_snapshot_meli("MLM1", _ITEM, malas, FETCH)
    assert snap is not None
    assert [e.review_external_id for e in eventos] == ["9"]
    assert eventos[0].rating is None
    assert "meli: review sin id (se descarta)" in skips
    assert "meli: rate fuera de [1,5] (NULL, no inventado)" in skips


def test_plan_meli_status_no_published():
    una = dict(
        _REVIEWS_CON_DATOS,
        reviews=[dict(_REVIEWS_CON_DATOS["reviews"][0], status="moderated")],
    )
    _, eventos, _ = plan_snapshot_meli("MLM1", _ITEM, una, FETCH)
    assert not eventos[0].publicada


# ---------------------------------------------------------------------------
# (b) CLIENTES con MockTransport
# ---------------------------------------------------------------------------


def test_cliente_meli_guardia_bloquea_post_sin_red(tmp_path):
    def explota(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debio tocar la red")

    cliente = ClienteMeli(_creds_meli(tmp_path), transport=httpx.MockTransport(explota))
    with pytest.raises(ReputacionError, match="solo-GET"):
        cliente._request("POST", "/items")
    with pytest.raises(ReputacionError, match="solo-GET"):
        cliente._request("PUT", "/items/MLM1")
    with pytest.raises(ReputacionError, match="solo-GET"):
        cliente._request("DELETE", "/items/MLM1")
    cliente.close()


def test_cliente_meli_scan_pagina_doble(tmp_path):
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(str(request.url))
        if "scroll_id" in str(request.url):
            return httpx.Response(200, json=_SCAN_2)
        return httpx.Response(200, json=_SCAN_1)

    cliente = ClienteMeli(
        _creds_meli(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0
    )
    assert cliente.items_seller(135734858) == (["MLM1", "MLM0"], False)
    assert len(llamadas) == 2
    cliente.close()


def test_cliente_meli_scan_truncado_cuenta(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": ["X"], "paging": {"total": 9}, "scroll_id": "S"}
        )

    cliente = ClienteMeli(
        _creds_meli(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0
    )
    ids, truncado = cliente.items_seller(1, max_paginas=3)
    # ARREGLOS 03 D-M: la misma pagina repetida no avanza; con 9 pendientes
    # la cobertura queda desconocida y los IDs se cuentan una sola vez.
    assert truncado and ids == ["X"]
    cliente.close()


# ---------------------------------------------------------------------------
# ARREGLOS 03 D-M (TN06): items_seller certifica completitud en origen.
# truncado=False exige paginas validas que alcanzan exactamente un total
# entero no booleano, no negativo y coherente, contando IDs unicos.
# ---------------------------------------------------------------------------


def _cliente_scan(tmp_path: Path, paginas: list[dict]) -> tuple[ClienteMeli, list[str]]:
    """ClienteMeli real (sin red) con paginas encoladas del scan."""
    turno = iter(paginas)
    llamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(str(request.url))
        assert request.url.path == "/users/7/items/search"
        return httpx.Response(200, json=next(turno))

    cliente = ClienteMeli(
        _creds_meli(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0
    )
    return cliente, llamadas


def test_arreglos03_dm_scroll_ausente_no_certifica_completitud(tmp_path):
    """TN06: total sin alcanzar y sin scroll_id: la cobertura es
    desconocida; la ausencia de scroll nunca prueba que el scan termino."""
    cliente, llamadas = _cliente_scan(tmp_path, [{"results": ["MLM1"], "paging": {"total": 2}}])
    try:
        assert cliente.items_seller(7) == (["MLM1"], True)
        assert len(llamadas) == 1
    finally:
        cliente.close()


def test_arreglos03_dm_fin_precozo_pagina_vacia_no_certifica(tmp_path):
    """TN06: pagina vacia antes de alcanzar el total: fin prematuro,
    no catalogo completo (solo un total 0 con contenedor vacio lo seria)."""
    cliente, llamadas = _cliente_scan(
        tmp_path,
        [
            {"results": ["MLM1"], "paging": {"total": 2}, "scroll_id": "x"},
            {"results": [], "paging": {"total": 2}},
        ],
    )
    try:
        assert cliente.items_seller(7) == (["MLM1"], True)
        assert len(llamadas) == 2
    finally:
        cliente.close()


def test_arreglos03_dm_total_inconsistente_no_certifica(tmp_path):
    # El total cambia entre paginas: parcial, conservando los IDs validos
    # ya observados de la pagina incoherente (no acreditan cobertura).
    cliente, _ = _cliente_scan(
        tmp_path,
        [
            {"results": ["MLM1"], "paging": {"total": 2}, "scroll_id": "x"},
            {"results": ["MLM0"], "paging": {"total": 3}},
        ],
    )
    try:
        assert cliente.items_seller(7) == (["MLM1", "MLM0"], True)
    finally:
        cliente.close()
    # Mas IDs unicos que el total anunciado: incoherente, no completo.
    cliente, _ = _cliente_scan(tmp_path, [{"results": ["MLM1", "MLM0"], "paging": {"total": 1}}])
    try:
        ids, truncado = cliente.items_seller(7)
        assert truncado and sorted(ids) == ["MLM0", "MLM1"]
    finally:
        cliente.close()
    # Un total booleano no es un entero util para certificar.
    cliente, _ = _cliente_scan(tmp_path, [{"results": ["MLM1"], "paging": {"total": True}}])
    try:
        assert cliente.items_seller(7) == (["MLM1"], True)
    finally:
        cliente.close()


def test_arreglos03_dm_duplicados_no_acreditan_completitud(tmp_path):
    """TN06: dos filas del mismo ID no suman cobertura: cuenta el conjunto
    de IDs unicos, no la suma de longitudes."""
    cliente, _ = _cliente_scan(tmp_path, [{"results": ["MLM1", "MLM1"], "paging": {"total": 2}}])
    try:
        assert cliente.items_seller(7) == (["MLM1"], True)
    finally:
        cliente.close()


def test_arreglos03_dm_solapamiento_con_avance_alcanza_el_total_unico(tmp_path):
    """Vecino: el solapamiento no invalida por si mismo; si tras el
    duplicado el conteo unico alcanza el total con avance, completa."""
    cliente, llamadas = _cliente_scan(
        tmp_path,
        [
            {"results": ["MLM1", "MLM0"], "paging": {"total": 3}, "scroll_id": "x"},
            {"results": ["MLM0", "MLM2"], "paging": {"total": 3}},
        ],
    )
    try:
        assert cliente.items_seller(7) == (["MLM1", "MLM0", "MLM2"], False)
        assert len(llamadas) == 2
    finally:
        cliente.close()


def test_arreglos03_dm_paginas_malformadas_son_parciales(tmp_path):
    # El contenedor no es lista.
    cliente, _ = _cliente_scan(tmp_path, [{"results": "MLM1", "paging": {"total": 1}}])
    try:
        assert cliente.items_seller(7) == ([], True)
    finally:
        cliente.close()
    # Un ID ilegible (bool) no se convierte en identidad.
    cliente, _ = _cliente_scan(tmp_path, [{"results": [True], "paging": {"total": 1}}])
    try:
        assert cliente.items_seller(7) == ([], True)
    finally:
        cliente.close()
    # paging no es objeto: la pagina no certifica nada.
    cliente, _ = _cliente_scan(tmp_path, [{"results": ["MLM1"], "paging": 5}])
    try:
        assert cliente.items_seller(7) == ([], True)
    finally:
        cliente.close()
    # Sin total verificable el fin del scroll no prueba completitud.
    cliente, _ = _cliente_scan(
        tmp_path, [{"results": ["MLM1"], "scroll_id": "x"}, {"results": ["MLM0"]}]
    )
    try:
        assert cliente.items_seller(7) == (["MLM1", "MLM0"], True)
    finally:
        cliente.close()


def test_arreglos03_dm_catalogo_vacio_certificado(tmp_path):
    """Vecino: total 0 con contenedor valido y vacio si demuestra
    catalogo vacio (no es lo mismo que un scan fallido)."""
    cliente, _ = _cliente_scan(tmp_path, [{"results": [], "paging": {"total": 0}}])
    try:
        assert cliente.items_seller(7) == ([], False)
    finally:
        cliente.close()


def test_arreglos03_dm_total_heredado_de_pagina_anterior_no_certifica(tmp_path):
    """R1 de la revision del PR: alcanzar un total que la pagina actual no
    corrobora (paging ausente en esa pagina) no certifica. Si el scroll
    sigue, avanza; si el total cambia, queda parcial (DISENO D-M: total
    cambiante implica parcial) conservando los IDs observados."""
    cliente, llamadas = _cliente_scan(
        tmp_path,
        [
            {"results": ["MLM1"], "paging": {"total": 2}, "scroll_id": "a"},
            {"results": ["MLM2"], "scroll_id": "b"},
            {"results": ["MLM3"], "paging": {"total": 3}},
        ],
    )
    try:
        assert cliente.items_seller(7) == (["MLM1", "MLM2", "MLM3"], True)
        assert len(llamadas) == 3
    finally:
        cliente.close()
    # Sin total vigente al terminarse el scroll: cobertura desconocida.
    cliente, _ = _cliente_scan(
        tmp_path,
        [
            {"results": ["MLM1"], "paging": {"total": 2}, "scroll_id": "a"},
            {"results": ["MLM2"]},
        ],
    )
    try:
        assert cliente.items_seller(7) == (["MLM1", "MLM2"], True)
    finally:
        cliente.close()


def test_cliente_meli_401_pase_vencido_sin_refresh(tmp_path):
    """Pase del bridge (opcion 1): un 401 aborta con mensaje claro; Orbit
    NUNCA refresca (cero POST a /oauth/token, cero reintento, el archivo
    del cron queda intacto)."""
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append((request.method, request.url.path))
        return httpx.Response(401, json={"error": "expired"})

    cliente = ClienteMeli(
        _creds_meli(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0
    )
    with pytest.raises(ReputacionError, match="pase del bridge vencido"):
        cliente.get("/users/me")
    assert llamadas == [("GET", "/users/me")]
    guardado = json.loads((tmp_path / "meli_tokens.json").read_text(encoding="utf-8"))
    assert guardado["access_token"] == "ACCESO"  # intacto: nadie lo reescribe
    assert not (tmp_path / "meli_tokens.tmp").exists()
    cliente.close()


def test_cliente_meli_429_reintenta_y_aborta(tmp_path):
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(1)
        return httpx.Response(429, json={"error": "lento"})

    cliente = ClienteMeli(
        _creds_meli(tmp_path),
        transport=httpx.MockTransport(handler),
        backoff_base=0,
        max_intentos=3,
    )
    with pytest.raises(ReputacionError, match="agotados 3 intentos"):
        cliente.get("/users/me")
    assert len(llamadas) == 3
    cliente.close()


# ---------------------------------------------------------------------------
# (c) INTEGRACION: PG real (0001 + 0024 + 0025)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def _migracion(tema: str) -> str:
    """Nombre de la migracion de un tema: su numero cambia si al rebasar ya
    estaba tomado (plans/arreglos-03.md), el tema no."""
    (nombre,) = [
        p.name
        for p in (ROOT / "migrations").glob(f"*_{tema}.sql")
        if re.fullmatch(rf"\d+_{tema}\.sql", p.name)
    ]
    return nombre


ORDEN = (
    "0001_initial.sql",
    "0024_reputacion.sql",
    "0025_reputacion_sin_fk.sql",
    "0026_reputacion_preguntas_reobservacion.sql",
    "0027_reputacion_reviews_reobservacion.sql",
    _migracion("arreglos03_reputacion_avisada"),
)


@contextmanager
def db_reputacion(prefijo: str = "orbit_repa2", orden: tuple[str, ...] = ORDEN):
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in orden:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, dsn.rsplit("/", 1)[0] + "/" + db
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_listing(conn, odoo_sku="P-1", plataforma="amazon_mx", asin="B0X"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (odoo_sku, odoo_sku),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO listing (product_id, platform, external_id) VALUES (%s, %s, %s)",
        (pid, plataforma, asin),
    )


def _mock_meli_completo():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/me":
            return httpx.Response(200, json=_ME)
        if path == "/users/135734858/items/search":
            if "scroll_id" in str(request.url):
                return httpx.Response(200, json=_SCAN_2)
            return httpx.Response(200, json=_SCAN_1)
        if path == "/items/MLM1":
            return httpx.Response(200, json=_ITEM)
        if path == "/reviews/item/MLM1":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(str(request.url)).query)
            if int((query.get("offset") or [0])[0]) > 0:
                return httpx.Response(200, json=_REVIEWS_PAGINA_2)
            return httpx.Response(200, json=_REVIEWS_CON_DATOS)
        if path == "/items/MLM0":
            return httpx.Response(200, json={"id": "MLM0", "status": "active"})
        if path == "/reviews/item/MLM0":
            return httpx.Response(200, json=_REVIEWS_VACIAS)
        if path == "/users/135734858":
            return httpx.Response(200, json=_USER_REP)
        if path == "/post-purchase/v1/claims/search":
            return httpx.Response(200, json=_CLAIMS)
        if path == "/questions/search":
            return httpx.Response(200, json=_QUESTIONS)
        return httpx.Response(404, json={"error": "no mockeado"})

    return httpx.MockTransport(handler)


@_skip_db
def test_corre_meli_completo_e_idempotente(tmp_path):
    """2 items (1 con reviews, 1 sin): snapshots+eventos+seller+questions;
    x2 = 0 nuevas."""
    with db_reputacion() as (conn, _dsn):
        cliente = ClienteMeli(
            _creds_meli(tmp_path), transport=_mock_meli_completo(), backoff_base=0
        )
        r1 = _corre_meli(conn, cliente, OBS, OBS.date())
        # 2 snapshots + 3 reviews (MLM1 pagina 2 via offset, XR-1 ALTA-2)
        # + 1 seller + 2 questions.
        assert r1["filas_insertadas"] == 8
        assert r1["filas_idempotentes"] == 0
        assert (
            conn.execute("SELECT count(*) FROM review_event WHERE external_id = 'MLM1'").fetchone()[
                0
            ]
            == 3
        )
        nulo = conn.execute(
            "SELECT rating, review_count FROM reputation_snapshot WHERE external_id = 'MLM0'"
        ).fetchone()
        assert nulo == (None, 0)
        r2 = _corre_meli(conn, cliente, OBS, OBS.date())
        assert r2["filas_insertadas"] == 0
        assert r2["filas_idempotentes"] == 8
        assert conn.execute("SELECT count(*) FROM reputation_snapshot").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM review_event").fetchone()[0] == 3
        cliente.close()


@_skip_db
def test_corre_meli_fallo_sella_sin_autocommit(tmp_path):
    """A.7 (run 119 huerfano): prod corre SIN autocommit; el sello failed
    debe sobrevivir al close(). Con mock 401 (pase vencido, camino real
    del deploy): tras close, el run debe estar sellado failed. Falla sin
    el fix."""
    with db_reputacion() as (conn, dsn_db):

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "expired"})

        prod = psycopg.connect(dsn_db)  # prod-like: sin autocommit
        cliente = ClienteMeli(
            _creds_meli(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0
        )
        with pytest.raises(ReputacionError, match="pase del bridge vencido"):
            _corre_meli(prod, cliente, OBS, OBS.date())
        prod.close()
        cliente.close()
        fila = conn.execute(
            "SELECT ok, rows_written FROM ingest_run WHERE source = 'reputacion_meli'"
        ).fetchone()
        assert fila[0] is False and fila[1] == 0


@_skip_db
def test_corre_meli_exito_persiste_sin_autocommit(tmp_path):
    """A.7: el mismo bug revertia HECHOS en corrida exitosa (sello ok
    atrapado en la tx implicita). Tras close, hechos + sello visibles."""
    with db_reputacion() as (conn, dsn_db):
        prod = psycopg.connect(dsn_db)  # prod-like: sin autocommit
        cliente = ClienteMeli(
            _creds_meli(tmp_path), transport=_mock_meli_completo(), backoff_base=0
        )
        resultado = _corre_meli(prod, cliente, OBS, OBS.date())
        assert resultado["filas_insertadas"] == 8
        prod.close()
        cliente.close()
        assert conn.execute("SELECT count(*) FROM reputation_snapshot").fetchone()[0] == 2
        fila = conn.execute(
            "SELECT ok, rows_written FROM ingest_run WHERE source = 'reputacion_meli'"
        ).fetchone()
        assert fila[0] is True and fila[1] == 8


@_skip_db
def test_corte_a_mitad_deja_cero_filas_de_hechos(tmp_path):
    """DoD A.2: corte en plena escritura -> rollback total de hechos."""
    with db_reputacion() as (conn, _dsn):
        cliente = ClienteMeli(
            _creds_meli(tmp_path), transport=_mock_meli_completo(), backoff_base=0
        )
        original = conn.execute
        llamadas = {"n": 0}

        def execute_cortado(query, *args, **kwargs):
            if isinstance(query, str) and query.lstrip().startswith("INSERT INTO reput"):
                llamadas["n"] += 1
                if llamadas["n"] == 2:
                    raise RuntimeError("corte simulado")
            return original(query, *args, **kwargs)

        with (
            mock.patch.object(conn, "execute", side_effect=execute_cortado),
            pytest.raises(RuntimeError, match="corte simulado"),
        ):
            _corre_meli(conn, cliente, OBS, OBS.date())
        assert conn.execute("SELECT count(*) FROM reputation_snapshot").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM review_event").fetchone()[0] == 0
        run = conn.execute("SELECT ok, rows_written FROM ingest_run").fetchone()
        assert run == (False, 0)  # evidencia del fallo, no hechos
        cliente.close()


def test_redaction_key_falsa_ausente_en_salida():
    """DoD A.2: secreto registrado no sobrevive a scrub()."""
    register_secret("SUPERCLAVE-FALSA-123")
    assert "SUPERCLAVE-FALSA-123" not in scrub("fallo con SUPERCLAVE-FALSA-123 dentro")
    assert REDACTED in scrub("fallo con SUPERCLAVE-FALSA-123 dentro")


@_skip_db
def test_redaction_en_fallo_real_ejecuta(tmp_path, monkeypatch, capsys):
    """XR-1.5: el secreto en un error de red NO sale por stderr."""
    register_secret("SECRETO-CAMINO-REAL-456")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom SECRETO-CAMINO-REAL-456")

    with db_reputacion() as (_conn, dsn_db):
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_db)
        _creds_meli(tmp_path)
        codigo = ejecuta_snapshot(
            "meli", transport=httpx.MockTransport(handler), secrets_dir=tmp_path
        )
        assert codigo == 1
        err = capsys.readouterr().err
        assert "SECRETO-CAMINO-REAL-456" not in err
        assert REDACTED in err


@_skip_db
def test_ejecuta_snapshot_meli_fin_a_fin(tmp_path, monkeypatch, capsys):
    with db_reputacion() as (conn, dsn_db):
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_db)
        _creds_meli(tmp_path)
        codigo = ejecuta_snapshot(
            "meli",
            fecha="2026-09-08",
            observed_at=OBS,
            transport=_mock_meli_completo(),
            secrets_dir=tmp_path,
        )
        assert codigo == 0
        resumen = json.loads(capsys.readouterr().out)
        # 5 snapshots/reviews + 1 seller + 2 questions, un run (AC3).
        assert resumen["filas_insertadas"] == 8
        assert sorted(resumen["run_ids"]) == ["meli"]
        assert conn.execute("SELECT count(*) FROM review_event").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM meli_question").fetchone()[0] == 2


@_skip_db
def test_cli_snapshot_meli_dry_run_real(tmp_path, monkeypatch, capsys):
    """CLI ejecutado de verdad (meli dry-run: red completa, cero writes)."""
    import app.reputacion as reputacion_mod

    class _ClienteMeliConMock(reputacion_mod.ClienteMeli):
        def __init__(self, creds, transport=None, **kwargs):
            super().__init__(creds, transport=_mock_meli_completo(), backoff_base=0, **kwargs)

    with db_reputacion() as (conn, dsn_db):
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_db)
        _creds_meli(tmp_path)
        monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))
        monkeypatch.setattr(reputacion_mod, "ClienteMeli", _ClienteMeliConMock)
        codigo = app_cli.main(["reputacion", "snapshot", "--fuente", "meli", "--dry-run"])
        assert codigo == 0
        resumen = json.loads(capsys.readouterr().out)
        assert resumen["dry_run"] is True
        assert resumen["items_vistos"] == 2
        assert resumen["preguntas_vistas"] == 2
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 0


def test_cli_snapshot_exit_2_sin_dsn_ni_args(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert app_cli.main(["reputacion", "snapshot", "--fuente", "meli"]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_cli_snapshot_rechaza_rest_y_fecha(monkeypatch):
    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://u:p@h/db")
    assert app_cli.main(["reputacion", "snapshot", "--fuente", "meli", "--noexiste"]) == 2
    assert app_cli.main(["reputacion", "snapshot", "--fuente", "meli", "--fecha", "ayer"]) == 2


# ---------------------------------------------------------------------------
# XR-1: re-observacion reviews (0027), truncados, redaction real
# ---------------------------------------------------------------------------


@_skip_db
def test_review_event_reobserva_por_observed(tmp_path):
    """XR-1.4/0027: mismo review + observed distinto = fila nueva (estado
    actual); mismo observed = duplicado."""
    with db_reputacion() as (conn, _dsn):
        _sembrar_listing(conn, odoo_sku="P-1", plataforma="meli", asin="MLM1")
        base = (
            "INSERT INTO review_event (platform, external_id, review_external_id,"
            " rating, texto, publicada, published_at, fetched_at, observed_at) VALUES ("
        )
        conn.execute(
            base + "'meli', 'MLM1', 'R1', 1, 'malo', TRUE,"
            f" '2026-09-06 10:00:00+00', '{FETCH}', '{OBS}')"
        )
        conn.execute(
            base + "'meli', 'MLM1', 'R1', 1, 'malo', FALSE,"
            f" '2026-09-06 10:00:00+00', '{FETCH}', '{OBS2}')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                base + "'meli', 'MLM1', 'R1', 1, 'malo', FALSE,"
                f" '2026-09-06 10:00:00+00', '{FETCH}', '{OBS2}')"
            )
        actual = conn.execute(
            "SELECT DISTINCT ON (review_external_id) publicada FROM review_event"
            " ORDER BY review_external_id, observed_at DESC"
        ).fetchone()[0]
        assert actual is False


def test_arreglos03_dl_pagina_repetida_aborta_lectura_de_preguntas():
    pagina = {
        "total": 51,
        "questions": [{"id": i, "item_id": "MLM1", "status": "UNANSWERED"} for i in range(50)],
    }
    cliente = mock.Mock(spec=ClienteMeli)
    cliente.get.side_effect = [({"id": 1}, FETCH), (pagina, FETCH), (pagina, FETCH)]

    with pytest.raises(ReputacionError, match="lista incompleta de preguntas"):
        fetch_questions(cliente)

    assert [llamada.args[1]["offset"] for llamada in cliente.get.call_args_list[1:]] == [0, 50]


@pytest.mark.parametrize("cantidad_final", [0, 1], ids=["vacia", "corta"])
def test_arreglos03_dl_pagina_final_con_preguntas_pendientes_aborta(cantidad_final):
    total = 51 + cantidad_final
    primera = {"total": total, "questions": [{"id": i} for i in range(50)]}
    ultima = {"questions": [{"id": 50 + i} for i in range(cantidad_final)]}
    cliente = mock.Mock(spec=ClienteMeli)
    cliente.get.side_effect = [({"id": 1}, FETCH), (primera, FETCH), (ultima, FETCH)]

    with pytest.raises(ReputacionError, match="lista incompleta de preguntas"):
        fetch_questions(cliente)


def test_pagina_truncada_por_max_paginas(tmp_path):
    """XR-1.7: paginas agotadas con datos pendientes = truncado."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(str(request.url)).query)
        offset = int((query.get("offset") or [0])[0])
        return httpx.Response(
            200,
            json={
                "paging": {"total": 500},
                "data": [{"id": offset + i} for i in range(50)],
            },
        )

    cliente = ClienteMeli(
        _creds_meli(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0
    )
    items, truncado = _pagina(cliente, "/x", {}, "data", max_paginas=2)
    assert truncado and len(items) == 100
    cliente.close()


# ---------------------------------------------------------------------------
# A.4: seller + questions
# ---------------------------------------------------------------------------


def test_plan_seller_shape_e03():
    plan = plan_seller(_USER_REP["seller_reputation"], _CLAIMS["data"], FETCH)
    assert plan.level_id == "5_green"
    assert plan.power_seller == "silver"
    assert (plan.tx_total, plan.tx_completed, plan.tx_canceled) == (662, 635, 27)
    assert (plan.ratings_positive, plan.ratings_neutral, plan.ratings_negative) == (1, 0, 0)
    assert plan.disputas_total == 2
    assert plan.disputas_abiertas == 1  # open si, closed no


def test_plan_question_estados():
    sin_responder, skip0 = plan_question("MLM1", _QUESTIONS["questions"][0], FETCH)
    assert sin_responder is not None and skip0 is None
    assert sin_responder.estado == "UNANSWERED"
    assert sin_responder.respuesta is None
    assert sin_responder.question_external_id == "13550511917"
    respondida, skip1 = plan_question("MLM1", _QUESTIONS["questions"][1], FETCH)
    assert respondida is not None and skip1 is None
    assert respondida.estado == "ANSWERED"
    assert respondida.respuesta == "Se envia de CDMX"
    assert respondida.answered_at is not None


def test_plan_question_descartes_y_answer_no_objeto():
    plan, motivo = plan_question("MLM1", {"id": 1, "status": "PENDING"}, FETCH)
    assert plan is None and "estado desconocido" in (motivo or "")
    plan, motivo = plan_question("MLM1", {"status": "UNANSWERED"}, FETCH)
    assert plan is None and "sin id" in (motivo or "")
    # XR-1.2: answer string no tumba nada; respuesta NULL + skip contado.
    plan, motivo = plan_question(
        "MLM1", {"id": 7, "status": "ANSWERED", "answer": "texto suelto"}, FETCH
    )
    assert plan is not None and plan.respuesta is None
    assert motivo is not None and "answer no-objeto" in motivo


@_skip_db
def test_fetch_seller_una_fila_e_idempotente(tmp_path):
    with db_reputacion() as (conn, _dsn):
        cliente = ClienteMeli(
            _creds_meli(tmp_path), transport=_mock_meli_completo(), backoff_base=0
        )
        plan, skips = fetch_seller(cliente)
        assert not skips
        i1, idem1 = _escribe_todo_meli(conn, [], [], plan, [], OBS.date(), OBS)
        assert (i1, idem1) == (1, 0)
        fila = conn.execute(
            "SELECT level_id, tx_total, disputas_total, disputas_abiertas"
            " FROM seller_reputation_snapshot"
        ).fetchone()
        assert fila == ("5_green", 662, 2, 1)
        i2, idem2 = _escribe_todo_meli(conn, [], [], plan, [], OBS.date(), OBS)
        assert (i2, idem2) == (0, 1)
        cliente.close()


@_skip_db
def test_fetch_questions_reobservacion_y_pendientes(tmp_path):
    with db_reputacion() as (conn, _dsn):
        cliente = ClienteMeli(
            _creds_meli(tmp_path), transport=_mock_meli_completo(), backoff_base=0
        )
        planes, skips = fetch_questions(cliente)
        assert not skips
        i1, _ = _escribe_todo_meli(conn, [], [], None, planes, OBS.date(), OBS)
        assert i1 == 2
        # Misma corrida: idempotente; otra corrida: re-observa (0026).
        i2, idem2 = _escribe_todo_meli(conn, [], [], None, planes, OBS.date(), OBS)
        assert (i2, idem2) == (0, 2)
        obs2 = OBS + dt.timedelta(days=1)
        i3, _ = _escribe_todo_meli(conn, [], [], None, planes, obs2.date(), obs2)
        assert i3 == 2
        pendientes = conn.execute(
            "SELECT DISTINCT ON (question_external_id) question_external_id, estado"
            " FROM meli_question WHERE estado = 'UNANSWERED'"
            " ORDER BY question_external_id, observed_at DESC"
        ).fetchall()
        assert [qid for qid, _ in pendientes] == ["13550511917"]
        cliente.close()


@_skip_db
def test_corre_meli_fallo_conserva_skips_acumulados(tmp_path, monkeypatch):
    """Grok XR-1 R2-5: si una fase red tardia falla, el sello failed
    conserva los skips de las fases que si corrieron (no Counter vacio)."""
    import app.reputacion as reputacion_mod

    with db_reputacion() as (conn, _dsn):
        skips_previos = Counter({"meli: scan de items incompleto (cobertura desconocida)": 1})
        monkeypatch.setattr(
            reputacion_mod,
            "fetch_meli",
            lambda cliente: ([], [], skips_previos),
        )

        def _truena(_cliente):
            raise RuntimeError("seller truena")

        monkeypatch.setattr(reputacion_mod, "fetch_seller", _truena)
        with pytest.raises(RuntimeError, match="seller truena"):
            _corre_meli(conn, object(), OBS, OBS.date())
        fila = conn.execute("SELECT ok, rows_skipped, skip_reason FROM ingest_run").fetchone()
        assert fila[0] is False
        assert fila[1] == 1
        assert "scan de items incompleto" in (fila[2] or "")


def test_clientes_meli_sin_posts():
    """Cero POSTs en clientes de reputacion: con el pase del bridge
    Orbit NUNCA pega a /oauth/token (no refresca) y MeLi es resto
    solo-GET."""
    fuente = Path(__file__).resolve().parents[1] / "app" / "meli" / "client.py"
    texto = fuente.read_text(encoding="utf-8")
    assert texto.count(".post(") == 0
    assert "/oauth/token" not in texto


@_skip_db
def test_corre_meli_fallo_en_questions_cero_hechos(tmp_path, monkeypatch, capsys):
    """Grok XR-1 ALTA-1: questions truena tras snapshots/seller OK ->
    rollback total (cero hechos), run failed, exit 1."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/questions/search":
            return httpx.Response(500, json={"error": "caido"})
        if path == "/users/me":
            return httpx.Response(200, json=_ME)
        if path == "/users/135734858/items/search":
            return httpx.Response(200, json={"results": ["MLM0"], "paging": {"total": 1}})
        if path == "/items/MLM0":
            return httpx.Response(200, json={"id": "MLM0"})
        if path == "/reviews/item/MLM0":
            return httpx.Response(200, json=_REVIEWS_VACIAS)
        if path == "/users/135734858":
            return httpx.Response(200, json=_USER_REP)
        if path == "/post-purchase/v1/claims/search":
            return httpx.Response(200, json=_CLAIMS)
        return httpx.Response(404, json={"error": "no mockeado"})

    with db_reputacion() as (conn, dsn_db):
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_db)
        _creds_meli(tmp_path)
        codigo = ejecuta_snapshot(
            "meli", transport=httpx.MockTransport(handler), secrets_dir=tmp_path
        )
        assert codigo == 1
        for tabla in (
            "reputation_snapshot",
            "review_event",
            "seller_reputation_snapshot",
            "meli_question",
        ):
            assert conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0
        assert conn.execute("SELECT every(NOT ok) FROM ingest_run").fetchone()[0] is True
