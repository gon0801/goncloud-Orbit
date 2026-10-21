"""Tests del `ingest diario` (app.ads.descarga.main, ingesta-01 A.2).

(a) UNITARIOS (corren siempre): los cuatro textos literales de Telegram, los
    horarios de HORARIOS_INGESTA y el tope de pared (2 h 45 min > plazo de
    sondeo de 2 h 30 min).
(b) INTEGRACION (Postgres real): rango por defecto D-31..D-1,
    --solo-faltantes (salta el grupo listo, corre el fallido, corre el que
    trae D-1 de MX y no de US, no cuenta un backfill viejo iniciado hoy, el
    saltado no abre ingest_run), salida 1 con mensaje de plazo/error/agotado
    y el mensaje de recuperacion. Cliente guion + reloj falso (calendario de
    la descarga), asi las corridas que agotan plazo tardan centesimas.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import socket
from pathlib import Path

import pytest
from test_ads_descarga import _calendario_falso, _Teatro
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.ads import diario
from app.ads.descarga import PLAZO_SONDEO_SEGUNDOS
from app.ads.diario import (
    HORARIOS_INGESTA,
    TOPE_PARED_SEGUNDOS,
    ResultadoGrupo,
    _horario_actual,
    _siguiente_horario,
    _texto_agotado,
    _texto_error,
    _texto_plazo,
    _texto_salvado,
    main,
)

_MIG = Path(__file__).resolve().parents[1] / "migrations"
SQL40 = (_MIG / "0040_ads_report_result.sql").read_text()
SQL20 = (_MIG / "0020_ads_producto_metrica.sql").read_text()
SQL62 = (_MIG / "0062_bids02_placement.sql").read_text()

HOY = dt.date(2026, 10, 21)
AYER = HOY - dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# (a) UNITARIOS - horarios, textos y tope de pared
# ---------------------------------------------------------------------------


def test_textos_de_falla_salvado_y_agotado_literalmente():
    assert _texto_plazo("las métricas por producto", "MX", "10:40") == (
        "Amazon no entregó a tiempo las métricas por producto de MX "
        "(esperamos 2 h 30 min). Orbit lo vuelve a intentar a las 10:40 UTC; "
        "mientras, el ciclo de pujas espera."
    )
    assert _texto_error("las métricas por ubicación", "US", "13:15") == (
        "Amazon respondió con error al pedir las métricas por ubicación de US. "
        "Orbit lo vuelve a intentar a las 13:15 UTC; mientras, "
        "el ciclo de pujas espera."
    )
    assert _texto_salvado("las métricas por producto", "MX", "10:40") == (
        "Ya llegaron las métricas por producto de MX en el intento de las 10:40 UTC."
    )
    assert _texto_agotado("las métricas por ubicación", "US") == (
        "Hoy no llegaron las métricas por ubicación de US después de tres "
        "intentos. Mañana a las 07:10 se intenta otra vez."
    )


def test_horarios_y_su_siguiente():
    assert HORARIOS_INGESTA == ("07:10", "10:40", "13:15")
    assert _horario_actual(dt.time(7, 10)) == "07:10"
    assert _horario_actual(dt.time(7, 9)) == "07:10", "antes del primero: el primero"
    assert _horario_actual(dt.time(10, 41)) == "10:40"
    assert _horario_actual(dt.time(13, 16)) == "13:15"
    assert _horario_actual(dt.time(23, 59)) == "13:15"
    assert _siguiente_horario("07:10") == "10:40"
    assert _siguiente_horario("10:40") == "13:15"
    assert _siguiente_horario("13:15") is None


def test_tope_de_pared_mayor_que_el_plazo_de_sondeo():
    assert TOPE_PARED_SEGUNDOS == 9900.0
    assert TOPE_PARED_SEGUNDOS > PLAZO_SONDEO_SEGUNDOS, (
        "el sondeo acota 2 h 30 min y la corrida entera 2 h 45 min"
    )


def test_fecha_fin_requiere_fecha():
    assert main(["--fecha-fin", "2026-10-20"], enviar=[], ahora=None) == 2


def test_sql_del_diario_parsea_como_postgres():
    """Guarda barata que SIEMPRE corre (patron test_reports_pipeline): la
    sintaxis SQL del diario es Postgres valida segun el parser real."""
    import pglast

    from app.ads.diario import (
        _SQL_FALLO_HOY,
        _SQL_GRUPO_AL_DIA,
        _SQL_PAISES_RESCATADOS,
        _SQL_PLATAFORMAS_DEL_SOURCE,
        _TABLA_POR_SOURCE,
    )

    for nombre, sql in (
        ("_SQL_PLATAFORMAS_DEL_SOURCE", _SQL_PLATAFORMAS_DEL_SOURCE),
        ("_SQL_FALLO_HOY", _SQL_FALLO_HOY),
    ):
        assert pglast.parse_sql(sql.replace("%s", "NULL")), f"{nombre} no parseo"
    for sql in (_SQL_GRUPO_AL_DIA, _SQL_PAISES_RESCATADOS):
        for tabla in _TABLA_POR_SOURCE.values():
            assert pglast.parse_sql(sql.format(tabla=tabla).replace("%s", "NULL"))


class _ResultadoFalso:
    def __init__(self, fila=None, filas=()):
        self._fila = fila
        self._filas = filas

    def fetchone(self):
        return self._fila

    def fetchall(self):
        return self._filas


class _ConnMensajes:
    """Responde los SQL de mensajeria con datos canned: fallo de hoy y
    plataformas rescatadas por run_id."""

    def __init__(self, *, fallos_hoy=(), rescatados=None):
        self._fallos_hoy = set(fallos_hoy)
        self._rescatados = rescatados or {}

    def execute(self, sql, params=None):
        params = params or ()
        if "ok = false" in sql:
            fuente = (params[0],)
            return _ResultadoFalso((1,) if fuente[0] in self._fallos_hoy else None)
        if "DISTINCT platform" in sql and "ingest_run_id = %s" in sql:
            run_id = params[0]
            return _ResultadoFalso(None, [(p,) for p in self._rescatados.get(run_id, ())])
        raise AssertionError(f"SQL no esperado: {sql}")


def _res(clave, source, *, ok, motivo="plazo", run_id=1, fallos=()):
    return ResultadoGrupo(
        clave=clave, run_id=run_id, source=source, ok=ok, motivo=motivo, fallos=list(fallos)
    )


def test_mensajes_por_grupo_pais_y_momento():
    """La seleccion de mensajes con conexion falsa: falla de plazo avisa el
    reintento; el salvamento solo por las plataformas que escribieron D-1;
    las metricas de campanas jamas avisan por aqui (salud)."""
    ahora_0710 = dt.datetime(2026, 10, 21, 7, 10, tzinfo=dt.UTC)
    ahora_1040 = dt.datetime(2026, 10, 21, 10, 40, tzinfo=dt.UTC)

    # 07:10: productos fallo por plazo (MX y US) y placements por error ->
    # reintento de las 10:40 en ambos grupos.
    enviados: list[str] = []
    conn = _ConnMensajes()
    resultados = {
        "metricas": _res("metricas", "amazon_ads_reports_v3", ok=False, motivo="plazo"),
        "productos": _res(
            "productos",
            "amazon_ads_products_v3",
            ok=False,
            fallos=[("amazon_mx", "productos", 202), ("amazon_us", "productos", 101)],
        ),
        "placements": _res(
            "placements",
            "amazon_ads_placements_v3",
            ok=False,
            motivo="error",
            fallos=[("amazon_us", "placements", 101)],
        ),
    }
    diario._envia_mensajes(conn, resultados, hoy=HOY, ahora=ahora_0710, enviar=enviados.append)
    assert enviados == [
        (
            "Amazon no entregó a tiempo las métricas por producto de MX "
            "(esperamos 2 h 30 min). Orbit lo vuelve a intentar a las 10:40 "
            "UTC; mientras, el ciclo de pujas espera."
        ),
        (
            "Amazon no entregó a tiempo las métricas por producto de US "
            "(esperamos 2 h 30 min). Orbit lo vuelve a intentar a las 10:40 "
            "UTC; mientras, el ciclo de pujas espera."
        ),
        (
            "Amazon respondió con error al pedir las métricas por ubicación "
            "de US. Orbit lo vuelve a intentar a las 10:40 UTC; mientras, "
            "el ciclo de pujas espera."
        ),
    ]

    # 10:40: productos se salvo (fallo anterior de hoy) y escribio D-1 de MX
    # pero no de US: solo se nombra MX. Sin fallo anterior: silencio.
    enviados = []
    conn = _ConnMensajes(fallos_hoy={"amazon_ads_products_v3"}, rescatados={7: ("amazon_mx",)})
    resultados = {
        "metricas": _res("metricas", "amazon_ads_reports_v3", ok=True),
        "productos": _res("productos", "amazon_ads_products_v3", ok=True, run_id=7),
        "placements": _res("placements", "amazon_ads_placements_v3", ok=True),
    }
    diario._envia_mensajes(conn, resultados, hoy=HOY, ahora=ahora_1040, enviar=enviados.append)
    assert enviados == [
        "Ya llegaron las métricas por producto de MX en el intento de las 10:40 UTC."
    ]

    # 13:15 (ultimo horario): la falla agota el dia.
    enviados = []
    conn = _ConnMensajes()
    resultados = {
        "productos": _res(
            "productos",
            "amazon_ads_products_v3",
            ok=False,
            fallos=[("amazon_mx", "productos", 202)],
        )
    }
    ahora_1315 = dt.datetime(2026, 10, 21, 13, 15, tzinfo=dt.UTC)
    diario._envia_mensajes(conn, resultados, hoy=HOY, ahora=ahora_1315, enviar=enviados.append)
    assert enviados == [
        (
            "Hoy no llegaron las métricas por producto de MX después de tres "
            "intentos. Mañana a las 07:10 se intenta otra vez."
        )
    ]


# ---------------------------------------------------------------------------
# (b) INTEGRACION - corrida diaria contra Postgres real
# ---------------------------------------------------------------------------

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@pytest.fixture
def conn_pg():
    import psycopg
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_diario_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL40)
        conn.execute(SQL20)
        conn.execute(SQL62)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _teatro_diario(reloj, fallan_productos: bool = False, filas_mx=None, sin_perfiles=False):
    """Cliente guion para el diario: todo completa ya salvo configuracion."""
    teatro = _Teatro(
        reloj, fecha_ini=HOY - dt.timedelta(days=31), fecha_fin=HOY - dt.timedelta(days=1)
    )
    if sin_perfiles:
        teatro.perfiles_api = None
    if fallan_productos:

        def fallado(t):
            return {"status": "FAILED", "failureReason": "INTERNAL_ERROR"}

        for perfil in teatro.perfiles_api or []:
            for sufijo in ("", "-r1", "-r2"):
                teatro.estado(f"rep-productos-{perfil['profileId']}{sufijo}", fallado)
    if filas_mx is not None:
        original = teatro.handler

        def handler(request):
            if request.url.host == "bucket.example.com":
                reporte = request.url.path.removesuffix(".json").removeprefix("/")
                if reporte.startswith("rep-productos-202"):
                    return _respuesta_json(filas_mx)
                return _respuesta_json([])
            return original(request)

        teatro.handler = handler
    return teatro


def _respuesta_json(filas):
    import httpx

    return httpx.Response(200, content=gzip.compress(json.dumps(filas).encode("utf-8")))


def _corrida(conn, teatro, reloj, duerme, enviados, argv=(), ahora=None, hoy=HOY):
    return main(
        list(argv),
        conn=conn,
        client=teatro._cliente(),
        reloj=lambda: reloj["t"],
        sleep=duerme,
        enviar=enviados.append,
        ahora=ahora or dt.datetime(2026, 10, 21, 7, 10, tzinfo=dt.UTC),
        hoy=hoy,
    )


def _runs(conn, source):
    return conn.execute("SELECT count(*) FROM ingest_run WHERE source = %s", (source,)).fetchone()[
        0
    ]


def _abre_run_ok(conn, source):
    return conn.execute(
        "INSERT INTO ingest_run (source, ok, started_at, finished_at)"
        " VALUES (%s, true, now(), now()) RETURNING id",
        (source,),
    ).fetchone()[0]


def _escrito_result(conn, run_id, plataforma, nombre, reporte):
    conn.execute(
        "INSERT INTO ads_report_result"
        " (ingest_run_id, profile_id, platform, report_name, source_report_id, status)"
        " VALUES (%s, %s, %s, %s, %s, 'written')",
        (run_id, 101 if plataforma == "amazon_us" else 202, plataforma, nombre, reporte),
    )


def _siembra_metricas_al_dia(conn, plataformas=("amazon_us", "amazon_mx"), metric_date=AYER):
    """Una corrida ok de metricas de HOY que escribio metric_date para las
    plataformas pedidas (con su evidencia written por plataforma)."""
    run = _abre_run_ok(conn, "amazon_ads_reports_v3")
    moneda = {"amazon_us": "USD", "amazon_mx": "MXN"}
    for plataforma in plataformas:
        entidad = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
            " RETURNING id",
            (plataforma, f"seed-{plataforma}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_metric_observation"
            " (ad_entity_id, metric_date, observed_at, metric_currency,"
            "  cost, source_report_id, ingest_run_id)"
            " VALUES (%s, %s, now(), %s, 1.0, %s, %s)",
            (entidad, metric_date, moneda[plataforma], f"R-{plataforma}", run),
        )
        _escrito_result(conn, run, plataforma, "campanas", f"R-{plataforma}")
    return run


def _siembra_productos_fallido(conn):
    return conn.execute(
        "INSERT INTO ingest_run (source, ok, started_at, finished_at)"
        " VALUES ('amazon_ads_products_v3', false, now(), now()) RETURNING id",
        (),
    ).fetchone()[0]


@_skip_db
def test_rango_por_defecto_d31_d1_y_tres_grupos_en_una_descarga(conn_pg, monkeypatch):
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj)
    capturado = {}

    def descarga_falsa(conn, client, grupos, rango, *, reloj, sleep, al_completar):
        capturado["grupos"] = grupos
        capturado["rango"] = rango
        return {
            g.clave: ResultadoGrupo(clave=g.clave, run_id=0, source=g.source, ok=True, motivo="ok")
            for g in grupos
        }

    monkeypatch.setattr(diario, "descarga", descarga_falsa)
    enviados: list[str] = []
    salida = _corrida(conn_pg, teatro, reloj, duerme, enviados)

    assert salida == 0
    assert capturado["rango"] == (HOY - dt.timedelta(days=31), HOY - dt.timedelta(days=1))
    assert [g.clave for g in capturado["grupos"]] == ["metricas", "productos", "placements"]
    assert enviados == [], "todo ok a la primera: nada que avisar"


@_skip_db
def test_solo_faltantes_salta_el_grupo_listo_y_no_abre_run(conn_pg):
    _siembra_metricas_al_dia(conn_pg)
    _siembra_productos_fallido(conn_pg)
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj)

    salida = _corrida(conn_pg, teatro, reloj, duerme, [], argv=["--solo-faltantes"])

    assert salida == 0
    assert _runs(conn_pg, "amazon_ads_reports_v3") == 1, "metricas estaba al dia: sin run nueva"
    assert _runs(conn_pg, "amazon_ads_products_v3") == 2, "productos fallo hoy: corre"
    assert _runs(conn_pg, "amazon_ads_placements_v3") == 1, "placements nunca corrio: corre"


@_skip_db
def test_solo_faltantes_corre_si_falta_una_plataforma(conn_pg):
    _siembra_metricas_al_dia(conn_pg, plataformas=("amazon_mx",))
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj)

    _corrida(conn_pg, teatro, reloj, duerme, [], argv=["--solo-faltantes"])

    assert _runs(conn_pg, "amazon_ads_reports_v3") == 2, "sin D-1 de US: el grupo corre"


@_skip_db
def test_solo_faltantes_no_cuenta_un_backfill_viejo_iniciado_hoy(conn_pg):
    # corrida ok de HOY cuyas filas son de septiembre: no es D-1, el grupo corre
    _siembra_metricas_al_dia(conn_pg, metric_date=dt.date(2026, 9, 15))
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj)

    _corrida(conn_pg, teatro, reloj, duerme, [], argv=["--solo-faltantes"])

    assert _runs(conn_pg, "amazon_ads_reports_v3") == 2


@_skip_db
def test_solo_faltantes_todo_al_dia_no_abre_nada(conn_pg):
    _siembra_metricas_al_dia(conn_pg)
    run_p = _abre_run_ok(conn_pg, "amazon_ads_products_v3")
    conn_pg.execute(
        "INSERT INTO ads_product_metric_observation"
        " (platform, advertised_asin, advertised_sku, metric_date, observed_at,"
        "  metric_currency, clicks, ingest_run_id)"
        " VALUES ('amazon_mx', 'B0P1', 'S1', %s, now(), 'MXN', 1, %s)",
        (AYER, run_p),
    )
    _escrito_result(conn_pg, run_p, "amazon_mx", "productos", "R-P")
    conn_pg.execute(
        "INSERT INTO ads_product_metric_observation"
        " (platform, advertised_asin, advertised_sku, metric_date, observed_at,"
        "  metric_currency, clicks, ingest_run_id)"
        " VALUES ('amazon_us', 'B0P2', 'S2', %s, now(), 'USD', 1, %s)",
        (AYER, run_p),
    )
    _escrito_result(conn_pg, run_p, "amazon_us", "productos", "R-P2")
    run_l = _abre_run_ok(conn_pg, "amazon_ads_placements_v3")
    entidad = conn_pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx', 'campaign', 'L1')"
        " RETURNING id"
    ).fetchone()[0]
    conn_pg.execute(
        "INSERT INTO ads_placement_observation"
        " (platform, ad_entity_id, placement, metric_date, observed_at,"
        "  metric_currency, clicks, source_report_id)"
        " VALUES ('amazon_mx', %s, 'arriba_de_busqueda', %s, now(), 'MXN', 1, 'R-L')",
        (entidad, AYER),
    )
    _escrito_result(conn_pg, run_l, "amazon_mx", "placements", "R-L")
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj)

    salida = _corrida(conn_pg, teatro, reloj, duerme, [], argv=["--solo-faltantes"])

    assert salida == 0
    assert conn_pg.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 3
    assert teatro.creates == [], "grupos saltados: ningun pedido a la API"


@_skip_db
def test_tope_de_pared_con_reloj_falso_mensaje_de_plazo_y_salida_1(conn_pg):
    """(tope de pared) los productos nunca completan: la corrida agota el
    plazo de sondeo con el reloj falso, avisa el reintento de las 10:40 y
    sale 1. metricas y placements salen bien y no avisan."""
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj, fallan_productos=True)
    enviados: list[str] = []

    salida = _corrida(conn_pg, teatro, reloj, duerme, enviados)

    assert salida == 1
    assert reloj["t"] >= PLAZO_SONDEO_SEGUNDOS
    assert reloj["t"] < TOPE_PARED_SEGUNDOS, "la corrida entera cabe en la pared"
    assert enviados == [
        (
            "Amazon no entregó a tiempo las métricas por producto de MX "
            "(esperamos 2 h 30 min). Orbit lo vuelve a intentar a las 10:40 "
            "UTC; mientras, el ciclo de pujas espera."
        ),
        (
            "Amazon no entregó a tiempo las métricas por producto de US "
            "(esperamos 2 h 30 min). Orbit lo vuelve a intentar a las 10:40 "
            "UTC; mientras, el ciclo de pujas espera."
        ),
    ]
    assert _runs(conn_pg, "amazon_ads_reports_v3") == 1
    assert _runs(conn_pg, "amazon_ads_products_v3") == 1
    assert _runs(conn_pg, "amazon_ads_placements_v3") == 1
    ok = conn_pg.execute("SELECT source, ok FROM ingest_run ORDER BY id").fetchall()
    assert ("amazon_ads_products_v3", False) in ok
    assert ("amazon_ads_reports_v3", True) in ok
    assert ("amazon_ads_placements_v3", True) in ok


@_skip_db
def test_error_al_pedir_mensaje_de_error_para_ambos_paises(conn_pg):
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj, sin_perfiles=True)
    enviados: list[str] = []

    salida = _corrida(conn_pg, teatro, reloj, duerme, enviados)

    assert salida == 1
    assert (
        "Amazon respondió con error al pedir las métricas por producto de MX. "
        "Orbit lo vuelve a intentar a las 10:40 UTC; mientras, "
        "el ciclo de pujas espera."
    ) in enviados
    assert (
        "Amazon respondió con error al pedir las métricas por producto de US. "
        "Orbit lo vuelve a intentar a las 10:40 UTC; mientras, "
        "el ciclo de pujas espera."
    ) in enviados
    assert (
        "Amazon respondió con error al pedir las métricas por ubicación de MX. "
        "Orbit lo vuelve a intentar a las 10:40 UTC; mientras, "
        "el ciclo de pujas espera."
    ) in enviados


@_skip_db
def test_se_salva_avisa_en_el_intento_de_las_1040(conn_pg):
    """Un grupo que fallo en el intento de las 07:10 y llega en el de las
    10:40 manda el mensaje de recuperacion por las plataformas que ESCRIBIO
    D-1 (MX trajo datos; US no: no se nombra)."""
    _siembra_productos_fallido(conn_pg)
    reloj, duerme = _calendario_falso()
    fila = {
        "date": AYER.isoformat(),
        "advertisedAsin": "B0DIARIO001",
        "advertisedSku": "SKU-DIARIO",
        "campaignId": 1,
        "adGroupId": 1,
        "adId": 1,
        "impressions": 10,
        "clicks": 1,
        "cost": 5.0,
        "purchases30d": 0,
        "sales30d": 0.0,
        "purchasesSameSku30d": 0,
        "attributedSalesSameSku30d": 0.0,
    }
    teatro = _teatro_diario(reloj, filas_mx=[fila])
    enviados: list[str] = []

    salida = _corrida(
        conn_pg,
        teatro,
        reloj,
        duerme,
        enviados,
        ahora=dt.datetime(2026, 10, 21, 10, 40, tzinfo=dt.UTC),
    )

    assert salida == 0
    assert enviados == [
        "Ya llegaron las métricas por producto de MX en el intento de las 10:40 UTC."
    ]


@_skip_db
def test_agotado_en_el_ultimo_horario_mensaje_final(conn_pg):
    reloj, duerme = _calendario_falso()
    teatro = _teatro_diario(reloj, fallan_productos=True)
    enviados: list[str] = []

    salida = _corrida(
        conn_pg,
        teatro,
        reloj,
        duerme,
        enviados,
        ahora=dt.datetime(2026, 10, 21, 13, 15, tzinfo=dt.UTC),
    )

    assert salida == 1
    assert (
        "Hoy no llegaron las métricas por producto de MX después de tres "
        "intentos. Mañana a las 07:10 se intenta otra vez."
    ) in enviados
    assert (
        "Hoy no llegaron las métricas por producto de US después de tres "
        "intentos. Mañana a las 07:10 se intenta otra vez."
    ) in enviados
