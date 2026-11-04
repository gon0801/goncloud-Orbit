"""Tests de la descarga paralela de reportes (`app.ads.descarga`, ingesta-01).

(a) UNITARIOS (corren siempre): `descarga` con cliente guion, reloj falso y
    conexion falsa que registra los SQL: falla de UN grupo por plazo sin
    tumbar a los demas, reemplazo FAILED -> repetido sin filas `failed`,
    numero EXACTO de GET con el calendario 5 s/30 s, escritura de un grupo
    ANTES de que otro termine, cero perfiles, error de evaluar_perfiles que
    sella las tres runs global_failed y 4xx de forma en el POST que falla
    solo su grupo.
(b) INTEGRACION (Postgres real, patron de tests/test_reports_pipeline.py):
    los escenarios (1), (2), (3), (5), (6), (7) y (8) del brief entran por
    `reports.sync_metrics`, que existe hoy: su rojo contra el codigo de hoy
    es real. Cliente guion + reloj falso; las descargas son arrays vacios,
    asi que no hace falta sembrar entidades.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import socket
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.descarga import (
    REINTENTO_TRAS_SEGUNDOS,
    GrupoDescarga,
    ResultadoEscritura,
    descarga,
)
from app.ads.reports import (
    CAMPANAS_CFG,
    KEYWORDS_CFG,
    SEARCH_TERMS_CFG,
    SOURCE,
    TARGETS_CFG,
    sync_metrics,
)
from app.ads.salud import incidentes_de_run

SQL40 = (Path(__file__).resolve().parents[1] / "migrations/0040_ads_report_result.sql").read_text()

PERFILES_API = [
    {
        "profileId": 101,
        "countryCode": "US",
        "currencyCode": "USD",
        "accountInfo": {"id": "1", "type": "seller", "name": "Goncloud US"},
    },
    {
        "profileId": 202,
        "countryCode": "MX",
        "currencyCode": "MXN",
        "accountInfo": {"id": "2", "type": "seller", "name": "Goncloud MX"},
    },
]

CAMPANAS_MIN = {
    "nombre": "campanas",
    "reportTypeId": "spCampaigns",
    "groupBy": ["campaign"],
    "columns": [],
}
KEYWORDS_MIN = {
    "nombre": "keywords",
    "reportTypeId": "spTargeting",
    "groupBy": ["targeting"],
    "columns": [],
}
TARGETS_MIN = {
    "nombre": "targets",
    "reportTypeId": "spTargeting",
    "groupBy": ["targeting"],
    "columns": [],
}
SEARCH_MIN = {
    "nombre": "search_terms",
    "reportTypeId": "spSearchTerm",
    "groupBy": ["searchTerm"],
    "columns": [],
}
CFGS_2 = (CAMPANAS_MIN, KEYWORDS_MIN)

_NOMBRES_CFG = (
    "campanas",
    "keywords",
    "targets",
    "search_terms",
    "productos",
    "place",
    "placements",
)


class _Teatro:
    """Cliente guion: creacion y poll dirigidos por (profile_id, cfg, sufijo)
    y el reloj falso. Por defecto todo crea al primer intento y completa ya."""

    def __init__(self, reloj, perfiles=None, fecha_ini=None, fecha_fin=None):
        self.reloj = reloj
        self.perfiles_api = perfiles if perfiles is not None else PERFILES_API
        self.fecha_ini = fecha_ini or dt.date(2026, 10, 20)
        self.fecha_fin = fecha_fin or self.fecha_ini
        # (profile_id, nombre) -> callable(t, sufijo) -> "ok" | ("http", status, cuerpo)
        self.crea: dict = {}
        # report_id -> callable(t) -> dict de estado del poll
        self.estados: dict = {}
        self.creates: list[dict] = []
        self.polls: list[str] = []

    def crea_siempre(self, clave, respuesta):
        self.crea[clave] = respuesta

    def estado(self, report_id, regla):
        self.estados[report_id] = regla

    def _cliente(self) -> AdsClient:
        return AdsClient(
            AdsCredentials(
                client_id="fake-id", client_secret="fake-secret", refresh_token="fake-token"
            ),
            transport=httpx.MockTransport(self.handler),
            sleep=lambda seconds: None,
        )

    def _matchea_pedido(self, body: dict):
        for perfil in self.perfiles_api or []:
            pid = perfil.get("profileId")
            if not isinstance(pid, int) or isinstance(pid, bool):
                continue
            for nombre in _NOMBRES_CFG:
                base = (
                    f"orbit-{nombre}-{pid}-"
                    f"{self.fecha_ini.isoformat()}_{self.fecha_fin.isoformat()}"
                )
                for sufijo in ("", "-r1", "-r2"):
                    if body.get("name") == base + sufijo:
                        return pid, nombre, sufijo
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access", "expires_in": 3600})
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json={"profiles": self.perfiles_api})
        if request.method == "POST" and request.url.path == "/reporting/reports":
            body = json.loads(request.content)
            scope = request.headers.get("Amazon-Advertising-API-Scope")
            self.creates.append({"scope": scope, "body": body})
            clave = self._matchea_pedido(body)
            assert clave is not None, f"pedido inesperado: {body['name']}"
            respuesta = self.crea.get((clave[0], clave[1]), "ok")
            if callable(respuesta):
                respuesta = respuesta(self.reloj["t"], clave[2])
            if respuesta == "ok":
                return httpx.Response(
                    200,
                    json={"reportId": f"rep-{clave[1]}-{clave[0]}{clave[2]}", "status": "PENDING"},
                )
            status, cuerpo = respuesta[1], respuesta[2]
            return httpx.Response(status, json=cuerpo)
        if request.url.path.startswith("/reporting/reports/"):
            report_id = request.url.path.rsplit("/", 1)[1]
            self.polls.append(report_id)
            regla = self.estados.get(report_id)
            estado = dict(regla(self.reloj["t"]) if regla else {"status": "COMPLETED"})
            if estado.get("status") == "COMPLETED":
                estado.setdefault("url", f"https://bucket.example.com/{report_id}.json")
            return httpx.Response(200, json=estado)
        if request.url.host == "bucket.example.com":
            return httpx.Response(200, content=gzip.compress(b"[]"))
        raise AssertionError(f"llamada inesperada: {request.method} {request.url}")


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------


def _calendario_falso():
    reloj = {"t": 0.0}

    def duerme(segundos: float) -> None:
        reloj["t"] += segundos

    return reloj, duerme


def _grupos(*claves: str, sources: dict | None = None) -> list[GrupoDescarga]:
    return [
        GrupoDescarga(clave=c, source=(sources or {}).get(c, f"source-{c}"), cfgs=CFGS_2)
        for c in claves
    ]


def _escritor_falso(registro: list, reloj) -> Callable:
    def escribe(conn, run_id, grupo, items, *, fecha_ini, fecha_fin):
        registro.append((grupo.clave, reloj["t"], run_id, len(items)))
        return ResultadoEscritura(
            rows_written=sum(len(item.filas) for item in items),
            rows_skipped=0,
            skip_reason=None,
        )

    return escribe


class _Resultado:
    def __init__(self, fila=None, filas=()):
        self._fila = fila
        self._filas = filas

    def fetchone(self):
        return self._fila

    def fetchall(self):
        return self._filas


class _ConnFalso:
    """Registra los SQL de las runs; abre runs con ids crecientes."""

    def __init__(self):
        self.ejecutados: list[tuple[str, tuple]] = []
        self._run_id = 0

    def execute(self, sql, params=None):
        self.ejecutados.append((sql, params or ()))
        if "INSERT INTO ingest_run" in sql and "RETURNING id" in sql:
            self._run_id += 1
            return _Resultado((self._run_id,))
        return _Resultado(None, [])

    def transaction(self):
        return _SinTx()

    def unidades_registradas(self) -> list[tuple]:
        """Las filas de ads_report_result vistas: (run_id, profile, platform,
        nombre, report_id, status, reason)."""
        filas = []
        for sql, params in self.ejecutados:
            if "INSERT INTO ads_report_result" in sql:
                filas.append(params)
        return filas


class _SinTx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _vueltas(hasta: float) -> list[float]:
    """Instantes de sondeo con el calendario 5 s/30 s (calculado en la
    prueba, no leido del modulo)."""
    instantes = []
    t = 0.0
    while t <= hasta:
        instantes.append(t)
        t += 5.0 if t < 120.0 else 30.0
    return instantes


def _descarga_falsa(conn, teatro, grupos, reloj, duerme, escritor):
    return descarga(
        conn,
        teatro._cliente(),
        grupos,
        (dt.date(2026, 10, 20), dt.date(2026, 10, 20)),
        reloj=lambda: reloj["t"],
        sleep=duerme,
        al_completar=escritor,
    )


# ---------------------------------------------------------------------------
# (a) UNITARIOS - descarga con conexion falsa
# ---------------------------------------------------------------------------


def test_agota_repetidos_y_plazo_falla_solo_su_grupo():
    """(4) un reporte que agota repetidos y plazo falla SOLO su grupo; los
    demas quedan ok=true."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj)
    grupos = [
        GrupoDescarga(clave="g1", source="source-g1", cfgs=(CAMPANAS_MIN, KEYWORDS_MIN)),
        GrupoDescarga(clave="g2", source="source-g2", cfgs=(TARGETS_MIN, SEARCH_MIN)),
    ]

    # g2/targets/202: siempre FAILED -> r1 FAILED -> r2 FAILED -> plazo.
    def fallado(t):
        return {"status": "FAILED", "failureReason": "INTERNAL_ERROR"}

    for sufijo in ("", "-r1", "-r2"):
        teatro.estado(f"rep-targets-202{sufijo}", fallado)
    conn = _ConnFalso()
    escrituras: list = []
    resultados = _descarga_falsa(
        conn, teatro, grupos, reloj, duerme, _escritor_falso(escrituras, reloj)
    )

    assert resultados["g1"].ok is True
    assert resultados["g1"].motivo == "ok"
    assert resultados["g2"].ok is False
    assert resultados["g2"].motivo == "plazo"
    assert resultados["g2"].fallos == [("amazon_mx", "targets", 202)]
    assert [e[0] for e in escrituras] == ["g1"], "g2 nunca completo: no escribe"
    pedidos_g2 = [
        c for c in teatro.creates if c["scope"] == "202" and "targets" in c["body"]["name"]
    ]
    assert len(pedidos_g2) == 3, "original + 2 repetidos"
    sufijos = [
        (
            "r2"
            if c["body"]["name"].endswith("-r2")
            else "r1"
            if c["body"]["name"].endswith("-r1")
            else "origen"
        )
        for c in pedidos_g2
    ]
    assert sorted(sufijos) == ["origen", "r1", "r2"]


def test_ok_con_reemplazo_no_deja_failed_y_salud_sin_incidentes():
    """(9) un FAILED reemplazado por su repetido deja la run ok=true, sin
    filas failed: salud no abre incidentes."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj)
    grupos = _grupos("metricas", sources={"metricas": SOURCE})
    teatro.estado(
        "rep-campanas-202",
        lambda t: {"status": "FAILED", "failureReason": "QUEUE_LOST"},
    )
    conn = _ConnFalso()
    resultados = _descarga_falsa(conn, teatro, grupos, reloj, duerme, _escritor_falso([], reloj))

    res = resultados["metricas"]
    assert res.ok is True
    repetidos = [c for c in teatro.creates if "-r1" in c["body"]["name"]]
    assert len(repetidos) == 1
    estados = [u[5] for u in conn.unidades_registradas()]
    assert "failed" not in estados
    assert "global_failed" not in estados
    unidades = [(u[1], u[2], u[3], u[5], u[6]) for u in conn.unidades_registradas()]
    assert incidentes_de_run(source=SOURCE, ok=True, unidades=unidades) == []


def test_numero_exacto_de_gets_para_doce_reportes_dos_horas():
    """(10) 12 reportes que completan a las 2 h: el numero de GET se calcula
    aqui con el calendario 5 s/30 s y las reglas de repetido (repetir a los
    40 min, maximo 2 repetidos, sondeados desde la vuelta siguiente a su
    pedido)."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj)
    grupos = _grupos("a", "b", "c")
    dos_horas = 2 * 3600.0
    vueltas = _vueltas(dos_horas)
    t_reintento = next(v for v in vueltas if v >= REINTENTO_TRAS_SEGUNDOS)
    t_segundo = next(v for v in vueltas if v >= t_reintento + REINTENTO_TRAS_SEGUNDOS)

    def desde(t):
        return sum(1 for v in vueltas if v > t)

    # El repetido se sondea desde la vuelta siguiente a su pedido; en la
    # vuelta en que el primero completa (todas a las 2 h), los demas intentos
    # del mismo reporte ya no se sondean: gana el primero en COMPLETED.
    esperados = 12 * (len(vueltas) + max(desde(t_reintento) - 1, 0) + max(desde(t_segundo) - 1, 0))

    def regla(t):
        return {"status": "COMPLETED" if t >= dos_horas else "PROCESSING"}

    for perfil in teatro.perfiles_api:
        for nombre in ("campanas", "keywords"):
            for sufijo in ("", "-r1", "-r2"):
                teatro.estado(f"rep-{nombre}-{perfil['profileId']}{sufijo}", regla)

    conn = _ConnFalso()
    resultados = _descarga_falsa(conn, teatro, grupos, reloj, duerme, _escritor_falso([], reloj))

    assert all(resultados[g.clave].ok for g in grupos)
    assert len(teatro.polls) == esperados
    assert reloj["t"] <= dos_horas + 30.0, "ninguna vuelta despues de completar"


def test_metrics_se_escribe_antes_de_que_placements_termine():
    """(11) el grupo que cierra primero escribe primero: la corrida de
    metricas (20 min) se escribe antes de que placements (30 min) termine."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj)
    grupos = [
        GrupoDescarga(clave="metrics", source="source-metrics", cfgs=CFGS_2),
        GrupoDescarga(
            clave="placements",
            source="source-placements",
            cfgs=(
                {
                    "nombre": "place",
                    "reportTypeId": "spCampaigns",
                    "groupBy": ["campaign"],
                    "columns": [],
                },
            ),
        ),
    ]

    def regla_metricas(t):
        return {"status": "COMPLETED" if t >= 20 * 60 else "PROCESSING"}

    def regla_placements(t):
        return {"status": "COMPLETED" if t >= 30 * 60 else "PROCESSING"}

    for perfil in teatro.perfiles_api:
        for nombre in ("campanas", "keywords"):
            teatro.estado(f"rep-{nombre}-{perfil['profileId']}", regla_metricas)
        teatro.estado(f"rep-place-{perfil['profileId']}", regla_placements)

    escrituras: list = []
    resultados = _descarga_falsa(
        _ConnFalso(), teatro, grupos, reloj, duerme, _escritor_falso(escrituras, reloj)
    )

    assert [e[0] for e in escrituras] == ["metrics", "placements"]
    assert escrituras[0][1] <= 20 * 60 + 30.0
    assert escrituras[1][1] >= 30 * 60
    assert all(resultados[g.clave].ok for g in grupos)


def test_cero_perfiles_sella_todas_las_runs_sin_pedir_nada():
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, perfiles=[])
    conn = _ConnFalso()
    resultados = _descarga_falsa(
        conn,
        teatro,
        _grupos("metricas", "productos", "placements"),
        reloj,
        duerme,
        _escritor_falso([], reloj),
    )
    assert {r.motivo for r in resultados.values()} == {"cero_perfiles"}
    assert all(not r.ok for r in resultados.values())
    assert teatro.creates == []
    assert conn._run_id == 3


def test_error_de_evaluar_perfiles_sella_las_tres_runs_global_failed():
    from app.ads.structure_api import AdsStructureError

    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj)
    teatro.perfiles_api = None  # el handler responde {"profiles": None}
    conn = _ConnFalso()
    with pytest.raises(AdsStructureError):
        _descarga_falsa(
            conn,
            teatro,
            _grupos("metricas", "productos", "placements"),
            reloj,
            duerme,
            _escritor_falso([], reloj),
        )
    global_failed = [u for u in conn.unidades_registradas() if u[5] == "global_failed"]
    assert len(global_failed) == 3, "las tres runs sellan global_failed"


def test_4xx_de_forma_en_el_post_falla_solo_su_grupo():
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj)
    grupos = [
        GrupoDescarga(clave="g1", source="source-g1", cfgs=(CAMPANAS_MIN, KEYWORDS_MIN)),
        GrupoDescarga(clave="g2", source="source-g2", cfgs=(TARGETS_MIN, SEARCH_MIN)),
    ]
    # create de targets/101 -> 400 (forma, no se repite): g2 muere al crear;
    # g1 (campanas/keywords) sigue y completa.
    teatro.crea_siempre(
        (101, "targets"),
        ("http", 400, {"code": "InvalidParameter"}),
    )
    conn = _ConnFalso()
    escrituras: list = []
    resultados = _descarga_falsa(
        conn, teatro, grupos, reloj, duerme, _escritor_falso(escrituras, reloj)
    )

    assert resultados["g1"].ok is True
    assert resultados["g2"].ok is False
    assert resultados["g2"].motivo == "error"
    assert isinstance(resultados["g2"].exc, AdsApiError)
    assert resultados["g2"].exc.status == 400
    assert [e[0] for e in escrituras] == ["g1"]


def test_file_size_anormal_da_error_antes_de_descargar():
    """La validacion del COMPLETED (guarda viva del pipeline viejo) sigue
    siendo fail-closed antes de descargar."""
    from app.ads.descarga import _url_de_completado
    from app.ads.reports import MAX_BYTES_REPORTE, AdsReportsError

    assert (
        _url_de_completado(
            {"status": "COMPLETED", "url": "https://bucket.example.com/fino.gz"}, "rep-fino"
        )
        == "https://bucket.example.com/fino.gz"
    )

    with pytest.raises(AdsReportsError, match="fileSize"):
        _url_de_completado(
            {
                "status": "COMPLETED",
                "url": "https://bucket.example.com/gordo.gz",
                "fileSize": MAX_BYTES_REPORTE + 1,
            },
            "rep-gordo",
        )


# ---------------------------------------------------------------------------
# (b) INTEGRACION - los escenarios del brief entran por reports.sync_metrics
# ---------------------------------------------------------------------------

REPORTES_CFG = (CAMPANAS_CFG, KEYWORDS_CFG, TARGETS_CFG, SEARCH_TERMS_CFG)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@pytest.fixture
def conn_pg():
    import psycopg
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_descarga_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL40)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sync_con_teatro(conn, teatro, reloj, duerme):
    fecha = teatro.fecha_ini
    return sync_metrics(
        conn,
        teatro._cliente(),
        fecha_ini=fecha,
        fecha_fin=fecha,
        sleep=duerme,
        reloj=lambda: reloj["t"],
    )


def _run_sellada(conn, run_id) -> tuple:
    return conn.execute(
        "SELECT ok, finished_at FROM ingest_run WHERE id = %s", (run_id,)
    ).fetchone()


@_skip_db
def test_un_reporte_de_55_min_se_repite_y_la_corrida_sale_ok(conn_pg):
    """(1) un reporte sigue PROCESSING 55 min: a los 40 min se pide un
    repetido (-r1) y el primero en COMPLETED (el original) gana. La run
    sale ok=true."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    limite = 55 * 60.0
    teatro.estado(
        "rep-campanas-101",
        lambda t: {"status": "COMPLETED" if t >= limite else "PROCESSING"},
    )
    # el repetido tarda mas: gana el original.
    teatro.estado(
        "rep-campanas-101-r1",
        lambda t: {"status": "COMPLETED" if t >= 70 * 60 else "PROCESSING"},
    )
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    ok, finished_at = _run_sellada(conn_pg, resultado.run_id)
    assert ok is True and finished_at is not None
    repetidos = [c for c in teatro.creates if "-r1" in c["body"]["name"]]
    assert len(repetidos) == 1
    assert repetidos[0]["body"]["name"].endswith("-r1")
    assert "rep-campanas-101" in teatro.polls, "el original se sigue sondeando"
    ganador = next(r for r in resultado.reportes if r.nombre == "campanas" and r.profile_id == 101)
    assert ganador.report_id == "rep-campanas-101"


@_skip_db
def test_failed_se_repite_y_sale_ok(conn_pg):
    """(2) un reporte que queda FAILED se repite y la corrida sale ok."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    teatro.estado(
        "rep-keywords-202",
        lambda t: {"status": "FAILED", "failureReason": "INTERNAL_ERROR"},
    )
    teatro.estado(
        "rep-keywords-202-r1",
        lambda t: {"status": "COMPLETED" if t >= 60 else "PROCESSING"},
    )
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    ok, finished_at = _run_sellada(conn_pg, resultado.run_id)
    assert ok is True and finished_at is not None
    repetidos = [c for c in teatro.creates if "-r1" in c["body"]["name"]]
    assert len(repetidos) == 1
    ganador = next(r for r in resultado.reportes if r.nombre == "keywords" and r.profile_id == 202)
    assert ganador.report_id == "rep-keywords-202-r1"


@_skip_db
def test_todos_a_2h10m_salen_ok(conn_pg):
    """(3) todos los reportes completan a las 2 h 10 min (dentro del plazo de
    2 h 30 min): la corrida sale ok."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    limite = 2 * 3600.0 + 10 * 60.0

    def regla(t):
        return {"status": "COMPLETED" if t >= limite else "PROCESSING"}

    for perfil in teatro.perfiles_api:
        for nombre in ("campanas", "keywords", "targets", "search_terms"):
            for sufijo in ("", "-r1", "-r2"):
                teatro.estado(f"rep-{nombre}-{perfil['profileId']}{sufijo}", regla)
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    assert reloj["t"] >= limite
    assert reloj["t"] < 9000.0, "el plazo de 2 h 30 min no se paso"


def _create_storm(status: int):
    def respuesta(t, sufijo):
        if sufijo == "-r2":
            return "ok"
        return ("http", status, {"error": "storm"})

    return respuesta


@_skip_db
def test_cinco_429_seguidos_al_crear_se_repite_y_sale_ok(conn_pg):
    """(5) el POST del reporte falla con 429 agotado una y otra vez: se
    repite con -r1 y -r2 hasta que uno entra; la corrida sale ok. El 429
    tambien reintenta a nivel HTTP dentro de cada POST (politica sellada
    del cliente: 429 = rechazado sin procesar), asi que aqui se fija el
    contrato de descarga: UN POST por sufijo (original + 2 repetidos)."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    teatro.crea_siempre((101, "campanas"), _create_storm(429))
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    pedidos = [c for c in teatro.creates if "campanas-101-" in c["body"]["name"]]
    nombres = sorted({c["body"]["name"] for c in pedidos})
    assert len(nombres) == 3, "original + 2 repetidos (un POST de descarga por sufijo)"
    assert nombres[1].endswith("-r1") and nombres[2].endswith("-r2")
    ganador = next(r for r in resultado.reportes if r.nombre == "campanas" and r.profile_id == 101)
    assert ganador.report_id == "rep-campanas-101-r2"


@_skip_db
def test_5xx_al_crear_se_repite_y_sale_ok(conn_pg):
    """(6) un 5xx en el POST (ambiguo, fail-closed en el cliente: UN intento
    HTTP, sin retry) tambien se repite a nivel de corrida: un POST por
    sufijo hasta que el -r2 entra."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    teatro.crea_siempre((202, "targets"), _create_storm(500))
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    pedidos = [c for c in teatro.creates if "targets-202-" in c["body"]["name"]]
    assert len(pedidos) == 3
    ganador = next(r for r in resultado.reportes if r.nombre == "targets" and r.profile_id == 202)
    assert ganador.report_id == "rep-targets-202-r2"


@_skip_db
def test_caso_2026_10_19_us_keywords_a_27_min_con_5_de_8(conn_pg):
    """(7) el caso real del 2026-10-19: a los 27 min, con 5 de 8 reportes
    descargados, el reporte de keywords de US queda FAILED. Con el sondeo
    nuevo se repite y la corrida sale ok (el diseno viejo abortaba la
    corrida entera a los 25 min de presupuesto)."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    minuto_27 = 27 * 60.0
    teatro.estado(
        "rep-keywords-101",
        lambda t: (
            {"status": "FAILED", "failureReason": "INTERNAL_ERROR"}
            if t >= minuto_27
            else {"status": "PROCESSING"}
        ),
    )
    teatro.estado(
        "rep-keywords-101-r1",
        lambda t: {"status": "COMPLETED" if t >= 30 * 60 else "PROCESSING"},
    )
    rapidos = [
        "rep-campanas-101",
        "rep-targets-101",
        "rep-search_terms-101",
        "rep-campanas-202",
        "rep-keywords-202",
    ]
    for report_id in rapidos:
        teatro.estado(report_id, lambda t: {"status": "COMPLETED" if t >= 60 else "PROCESSING"})
    teatro.estado(
        "rep-targets-202", lambda t: {"status": "COMPLETED" if t >= 28 * 60 else "PROCESSING"}
    )
    teatro.estado(
        "rep-search_terms-202",
        lambda t: {"status": "COMPLETED" if t >= 29 * 60 else "PROCESSING"},
    )
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    repetidos = [c for c in teatro.creates if "-r1" in c["body"]["name"]]
    assert len(repetidos) == 1
    assert repetidos[0]["scope"] == "101"
    ganador = next(r for r in resultado.reportes if r.nombre == "keywords" and r.profile_id == 101)
    assert ganador.report_id == "rep-keywords-101-r1"


@_skip_db
def test_425_con_cuerpo_sondea_el_id_de_la_respuesta(conn_pg):
    """(8) un 425 anuncia en el cuerpo el id del duplicado documentado; la
    corrida sondea ESE reportId y sale ok."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    teatro.crea_siempre(
        (202, "campanas"),
        lambda t, sufijo: (
            "http",
            425,
            {"message": "The Request is a duplicate of : rep-previo-9"},
        ),
    )
    teatro.estado("rep-previo-9", lambda t: {"status": "COMPLETED" if t >= 30 else "PROCESSING"})
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    assert "rep-previo-9" in teatro.polls
    assert "rep-campanas-202" not in teatro.polls, "el 425 no creo un id nuevo"
    ganador = next(r for r in resultado.reportes if r.nombre == "campanas" and r.profile_id == 202)
    assert ganador.report_id == "rep-previo-9"


@_skip_db
def test_sync_metrics_sellado_ok_y_unidades(conn_pg):
    """La corrida feliz sella ok=true y deja pending+downloaded+written por
    reporte (sin rejected: los dos perfiles de la fixture son aceptados)."""
    reloj, duerme = _calendario_falso()
    teatro = _Teatro(reloj, fecha_ini=dt.date(2026, 10, 20))
    resultado = _sync_con_teatro(conn_pg, teatro, reloj, duerme)

    assert resultado.ok is True
    assert len(resultado.reportes) == 8
    assert [r.report_id for r in resultado.reportes][:4] == [
        "rep-campanas-101",
        "rep-keywords-101",
        "rep-targets-101",
        "rep-search_terms-101",
    ]
    filas = conn_pg.execute(
        "SELECT status, count(*) FROM ads_report_result WHERE ingest_run_id = %s"
        " GROUP BY status ORDER BY status",
        (resultado.run_id,),
    ).fetchall()
    assert filas == [("downloaded", 8), ("pending", 8), ("written", 8)]
    assert SOURCE == "amazon_ads_reports_v3"
