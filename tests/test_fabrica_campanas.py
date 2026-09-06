"""tools/fabrica_campanas.py (FABRICA 01, tarea 6: plan + dry-run, CERO HTTP).

SQL contra Postgres REAL (precedente: el %s::platform IndeterminateDatatype
solo lo vio una base de verdad); ventanas pineadas a la fecha UTC; dry-run y
guards sin HTTP; CLI end-to-end por stdin y por archivo sobre la DB de
pruebas. Las mutaciones (tareas 7-9) se agregan aparte."""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Json
from test_fabrica_migracion import _entidad, _ledger_producto, _producto, db_fabrica
from test_schema import _postgres_obligatorio_ausente, _test_dsn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fabrica_campanas as fc  # noqa: E402

from app import fabrica_plan as fp  # noqa: E402

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
# HOY en UTC (D-GLM-6-5): las siembras usan dias bien dentro/fuera de ventana
# para que el cruce de medianoche no flakee la suite.
HOY = dt.datetime.now(dt.UTC).date()
RAIZ = Path(__file__).resolve().parent.parent


def _config(conn, fraccion="0.5", platform="amazon_mx"):
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t', %s)",
        (Json({f"ads_target_fraccion_margen_{platform}": fraccion}),),
    )


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]


def _campana_con_producto(conn, lid, external="c-old", status="ENABLED", platform="amazon_mx"):
    """Campana existente con ad group y product_ad ligado al listing: es
    'campana del producto' para semillas y para el reporte de existentes."""
    camp = _entidad(conn, platform, "campaign", external)
    conn.execute("UPDATE ad_entity SET name = %s WHERE id = %s", (f"vieja {external}", camp))
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, now())",
        (camp, status),
    )
    ag = _entidad(conn, platform, "ad_group", f"ag-{external}", parent=camp)
    _entidad(conn, platform, "product_ad", f"pa-{external}", parent=ag, listing_id=lid)
    return camp, ag


def _termino(conn, run, origen, texto, fecha, orders, cost, revenue, asin_like=False, obs=1):
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term, metric_date,"
        " observed_at, metric_currency, cost, clicks, orders, ad_revenue, is_asin_like,"
        " ingest_run_id) VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN', %s, 5, %s, %s, %s, %s)",
        (
            origen,
            texto,
            fecha,
            dt.datetime(2026, 9, 1, obs, tzinfo=dt.UTC),
            cost,
            orders,
            revenue,
            asin_like,
            run,
        ),
    )


# ---------------------------------------------------------------------------
# SQL contra Postgres real
# ---------------------------------------------------------------------------


@_skip_db
def test_fraccion_lee_el_setting_vigente_y_aborta_sin_el():
    with db_fabrica("orbit_fab_frac") as conn:
        with pytest.raises(fc.Abortar, match="fraccion"):
            fc._fraccion(conn, "amazon_mx")
        _config(conn, "0.25")
        assert fc._fraccion(conn, "amazon_mx") == Decimal("0.25")
        _config(conn, "abc")  # config CORRUPTA presente: ValueError ruidoso, no abstencion
        with pytest.raises(ValueError):
            fc._fraccion(conn, "amazon_mx")


@_skip_db
def test_productos_exige_margen_y_seller_sku():
    with db_fabrica("orbit_fab_prod") as conn:
        con_margen, lid = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        _ledger_producto(conn, con_margen, hoy=HOY)
        sin_margen, _ = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        sin_sku, _ = _producto(conn, sku="C", asin="B0CCCCCCCC", seller_sku=None)
        _ledger_producto(
            conn, sin_sku, hoy=HOY, fee_desfase=10
        )  # no chocar los cargos prorrateados
        filas = fc._productos(conn, "amazon_mx", [con_margen])
        assert len(filas) == 1 and filas[0].seller_sku == "SA" and filas[0].listing_id == lid
        assert filas[0].margen_neto_pct == Decimal("40")
        with pytest.raises(fc.Abortar, match="sin margen"):
            fc._productos(conn, "amazon_mx", [con_margen, sin_margen])
        with pytest.raises(fc.Abortar, match="seller_sku"):
            fc._productos(conn, "amazon_mx", [sin_sku])
        with pytest.raises(fc.Abortar, match="no existe"):
            fc._productos(conn, "amazon_mx", [999999])


@_skip_db
def test_productos_multi_listing_aborta_nombrando_el_producto():
    """Fail-loud (regla 3): un producto con 2 listings en la plataforma haria
    N product ads por campana y reventaria la PK de campana_grupo_producto
    DESPUES de los POST. El plan ABORTA nombrando el producto: la fabrica no
    elige listing (multi-listing queda fuera de F1, residual --listing)."""
    with db_fabrica("orbit_fab_multi") as conn:
        pid, _ = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0AAAAAAB2', 'SA-2')",
            (pid,),
        )
        _ledger_producto(conn, pid, hoy=HOY)
        with pytest.raises(fc.Abortar, match="listings") as exc:
            fc._productos(conn, "amazon_mx", [pid])
        assert str(pid) in str(exc.value) and "(A)" in str(exc.value)  # nombra al producto


@_skip_db
def test_terminos_del_producto_colapsan_bitemporal_y_suman_en_ventana():
    """Solo terminos de los AD GROUPS de campanas con product_ad del listing
    (grano unico sellado por la tarea 1 (f)); ultima observacion por
    (origen, termino, dia) (regla 5); ventana [D-105, D-15). Una fila en el
    grano campana NO cuenta: discrimina el UNION (si alguien lo regresa,
    'grano campana' aparece y este test truena)."""
    with db_fabrica("orbit_fab_term") as conn:
        pid, lid = _producto(conn)
        camp, ag = _campana_con_producto(conn, lid)
        _otra, otra_ag = _campana_con_producto(conn, None, external="c-ajena")
        run = _run(conn)
        d = HOY - dt.timedelta(days=40)
        _termino(conn, run, ag, "collar perro", d, 1, 10, 100, obs=1)
        _termino(conn, run, ag, "collar perro", d, 2, 12, 150, obs=2)  # re-observacion: manda
        _termino(conn, run, ag, "collar perro", d + dt.timedelta(days=1), 1, 5, 50)
        _termino(conn, run, ag, "b0zzzzzzzz", d, 1, 1, 10, asin_like=True)
        _termino(conn, run, otra_ag, "ajeno", d, 5, 1, 10)
        _termino(conn, run, ag, "viejo", HOY - dt.timedelta(days=120), 5, 1, 10)
        _termino(conn, run, ag, "inmaduro", HOY - dt.timedelta(days=5), 5, 1, 10)
        _termino(conn, run, camp, "grano campana", d, 5, 1, 10)  # grano que produccion NO tiene
        terminos = {t.texto: t for t in fc._terminos_producto(conn, "amazon_mx", [lid])}
        assert set(terminos) == {"collar perro", "b0zzzzzzzz"}
        assert terminos["collar perro"].orders == 3
        assert terminos["collar perro"].cost == Decimal("17")
        assert terminos["collar perro"].revenue == Decimal("200")
        assert terminos["b0zzzzzzzz"].is_asin_like is True


@_skip_db
def test_terminos_exact_usan_la_ventana_de_cortes():
    """Regla 6: los candidatos a semilla exact maduran con la ventana de
    CORTES del motor ([D-39, D-9)), no con la del margen ([D-105, D-15)):
    un termino de hace 12 dias entra a exact pero no al margen; uno de hace
    5 dias no entra a ninguna (inmaduro)."""
    with db_fabrica("orbit_fab_term2") as conn:
        _pid, lid = _producto(conn)
        _camp, ag = _campana_con_producto(conn, lid)
        run = _run(conn)
        _termino(conn, run, ag, "doce dias", HOY - dt.timedelta(days=12), 2, 10, 100)
        _termino(conn, run, ag, "cinco dias", HOY - dt.timedelta(days=5), 2, 10, 100)
        margen = {t.texto for t in fc._terminos_producto(conn, "amazon_mx", [lid])}
        exact = {
            t.texto
            for t in fc._terminos_producto(
                conn,
                "amazon_mx",
                [lid],
                sql=fc._SQL_TERMINOS_EXACT,
                ventana=fp.VENTANA_CORTES_DIAS,
            )
        }
        assert margen == set()  # ambos mas jovenes que D-15
        assert exact == {"doce dias"}


@_skip_db
def test_las_ventanas_no_dependen_del_timezone_de_la_sesion():
    """D-GLM-6-1: el dia de referencia es un parametro UTC calculado en
    Python; si el SQL volviera a CURRENT_DATE, la fecha de la SESION moveria
    la ventana. Se elige una zona cuya fecha local DIFIERE de la UTC en el
    momento de la corrida y se siembran los bordes: HOY-16 dentro, HOY-15
    fuera (ventana del margen [D-105, D-15))."""
    tz = next(
        (
            z
            for z in (
                "Etc/GMT-14",
                "Etc/GMT-13",
                "Etc/GMT-12",
                "Etc/GMT+12",
                "Etc/GMT+11",
            )
            if dt.datetime.now(ZoneInfo(z)).date() != HOY
        ),
        None,
    )
    assert tz is not None, "siempre existe una zona con fecha distinta a la UTC"
    with db_fabrica("orbit_fab_tz") as conn:
        conn.execute(f"SET TIME ZONE '{tz}'")
        _pid, lid = _producto(conn)
        _camp, ag = _campana_con_producto(conn, lid)
        run = _run(conn)
        _termino(conn, run, ag, "dentro", HOY - dt.timedelta(days=16), 1, 1, 10)
        _termino(conn, run, ag, "borde", HOY - dt.timedelta(days=15), 1, 1, 10)
        terminos = {t.texto for t in fc._terminos_producto(conn, "amazon_mx", [lid])}
        assert terminos == {"dentro"}  # 'borde' queda fuera: metric_date < D-15


@_skip_db
def test_biblioteca_y_existentes():
    with db_fabrica("orbit_fab_bib") as conn:
        pid, lid = _producto(conn)
        conn.execute(
            "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, orders)"
            " VALUES ('collar_perro', 'amazon_mx', 'con ventas', 'x', 2),"
            " ('collar_perro', 'amazon_mx', 'sin ventas', 'x', 0),"
            " ('otro_tipo', 'amazon_mx', 'ajena', 'x', 5)"
        )
        conn.execute(
            "INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)"
            " VALUES ('collar_perro', 'amazon_mx', 'gato', 'x')"
        )
        kws, negs = fc._biblioteca(conn, "collar_perro", "amazon_mx")
        assert kws == ["con ventas"] and negs == ["gato"]
        _campana_con_producto(conn, lid, external="c-old", status="PAUSED")
        existentes = fc._existentes(conn, "amazon_mx", [lid])
        assert [(e["external_id"], e["status"]) for e in existentes] == [("c-old", "PAUSED")]


# ---------------------------------------------------------------------------
# Dry-run sin HTTP (conexion falsa)
# ---------------------------------------------------------------------------


class _ConnFalsa:
    """Sirve el plan enlatado por consulta y GRABA escrituras + commits +
    closes: el dry-run debe ser solo lectura y cerrar su conexion."""

    def __init__(
        self,
        *,
        settings=None,
        productos=(),
        terminos=(),
        terminos_exact=None,
        biblioteca=([], []),
        existentes=(),
    ):
        self.settings = settings
        self.productos = list(productos)
        self.terminos = list(terminos)
        # candidatos a exact (ventana de cortes): por default los mismos terminos
        self.terminos_exact = list(terminos) if terminos_exact is None else list(terminos_exact)
        self.biblioteca = biblioteca
        self.existentes = list(existentes)
        self.escrituras = []  # (sql plano, params)
        self.commits = 0
        self.closes = 0

    def execute(self, sql, params=None):
        plano = " ".join(str(sql).split())
        bajo = plano.lower()
        if bajo.startswith(("insert", "update", "delete")):
            self.escrituras.append((plano, params))
            return _Cursor([])
        if "from config_version" in bajo:
            return _Cursor([(1, self.settings)] if self.settings is not None else [])
        if "v_margen_producto" in bajo:
            return _Cursor(self.productos)
        if "ventana_cortes" in bajo:  # _SQL_TERMINOS_EXACT (ventana de cortes)
            return _Cursor(self.terminos_exact)
        if "search_term_observation" in bajo:
            return _Cursor(self.terminos)
        if "from keyword_biblioteca" in bajo:
            return _Cursor([(t,) for t in self.biblioteca[0]])
        if "from negative_biblioteca" in bajo:
            return _Cursor([(t,) for t in self.biblioteca[1]])
        if "ad_entity_state" in bajo and "product_ad" in bajo:
            return _Cursor(self.existentes)
        raise AssertionError(f"SQL inesperado: {plano[:140]}")

    def commit(self):
        self.commits += 1

    def close(self):
        self.closes += 1


class _Cursor:
    def __init__(self, filas):
        self._filas = list(filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


# Fila de _SQL_PRODUCTOS: (product_id, odoo_sku, listing_id, asin, seller_sku, margen)
FILA_PRODUCTO = (1, "ODOO-1", 11, "B0AAAAAAAA", "SS-1", Decimal("38.20"))
ARGS_BASE = [
    "--plataforma",
    "amazon_mx",
    "--tipo-producto",
    "collar_perro",
    "--nombre-base",
    "Collar reflectante",
    "--productos",
    "1",
    "--modo",
    "shadow",
    "--budget-auto",
    "150",
    "--budget-phrase",
    "120",
    "--budget-product",
    "120",
    "--budget-broad",
    "120",
    "--budget-exact",
    "150",
    "--bid-auto",
    "4.50",
    "--bid-phrase",
    "5.00",
    "--bid-product",
    "5.00",
    "--bid-broad",
    "4.00",
    "--bid-exact",
    "6.00",
]


def _frontera_lectura(monkeypatch, conn_read):
    monkeypatch.setenv("ORBIT_DSN_READ", "dsn-read")
    monkeypatch.setattr(fc, "connect", lambda dsn: {"dsn-read": conn_read}[dsn])


def _sin_red(monkeypatch):
    """Cualquier intento de socket (HTTP incluido) revienta (test D)."""

    def _revienta(*args, **kwargs):
        raise AssertionError("HTTP/red prohibidos en el dry-run de la tarea 6")

    monkeypatch.setattr(socket, "socket", _revienta)


def test_dry_run_imprime_plan_huella_y_no_abre_http(monkeypatch, capsys):
    conn = _ConnFalsa(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"},
        productos=[FILA_PRODUCTO],
        terminos=[("collar perro", False, 3, Decimal("10"), Decimal("100"))],
        biblioteca=(["collar led"], ["gato"]),
        existentes=[("c-old", "vieja", "PAUSED")],
    )
    _frontera_lectura(monkeypatch, conn)
    _sin_red(monkeypatch)  # cualquier HTTP revienta
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    assert fc.main() == 0
    salida = capsys.readouterr().out
    assert "target=19.10" in salida and "semillas=" in salida
    assert "huella del conjunto:" in salida
    assert "existente (solo se informa" in salida and "c-old" in salida
    eventos = [json.loads(linea) for linea in salida.splitlines() if linea.startswith("{")]
    assert any(e["evento"] == "dry_run" for e in eventos)
    assert conn.escrituras == [] and conn.commits >= 1 and conn.closes >= 1  # solo lectura y cierra


def test_dry_run_dice_semillas_cero_explicito(monkeypatch, capsys):
    conn = _ConnFalsa(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO]
    )
    _frontera_lectura(monkeypatch, conn)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    assert fc.main() == 0
    salida = capsys.readouterr().out
    assert "category_phrase" in salida and "semillas=0" in salida


def test_sin_fraccion_o_bid_fuera_de_banda_aborta_sin_http(monkeypatch):
    conn = _ConnFalsa(settings={}, productos=[FILA_PRODUCTO])
    _frontera_lectura(monkeypatch, conn)
    _sin_red(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    with pytest.raises(fc.Abortar, match="fraccion"):
        fc.main()
    conn = _ConnFalsa(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO]
    )
    _frontera_lectura(monkeypatch, conn)
    args = [a if a != "6.00" else "45.01" for a in ARGS_BASE]  # bid-exact sobre el techo MXN
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *args])
    with pytest.raises(fc.Abortar, match="bid"):
        fc.main()


def test_modo_es_obligatorio_y_cerrado(monkeypatch):
    sin_modo = ARGS_BASE.copy()
    del sin_modo[sin_modo.index("--modo") : sin_modo.index("--modo") + 2]
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *sin_modo])
    with pytest.raises(SystemExit):
        fc.main()
    monkeypatch.setattr(
        sys, "argv", ["fabrica_campanas.py", *[a if a != "shadow" else "off" for a in ARGS_BASE]]
    )
    with pytest.raises(SystemExit):
        fc.main()


def test_opciones_de_mutacion_se_rechazan_explicito(monkeypatch, capsys):
    """Tarea 6 es SOLO lectura: --acepto-mutacion-real, --desarmar y
    --reconciliar abortan con mensaje claro, sin HTTP ni escrituras."""
    conn = _ConnFalsa(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO]
    )
    _frontera_lectura(monkeypatch, conn)
    _sin_red(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE, "--acepto-mutacion-real"])
    with pytest.raises(fc.Abortar, match="tarea 7"):
        fc.main()
    assert conn.escrituras == []
    for extra in (["--desarmar", "fabrica-x"], ["--reconciliar"]):
        monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *extra])
        with pytest.raises(fc.Abortar, match="tarea 9"):
            fc.main()


# ---------------------------------------------------------------------------
# CLI end-to-end sobre la DB de pruebas (stdin y archivo)
# ---------------------------------------------------------------------------


@_skip_db
def test_cli_dry_run_end_to_end_por_stdin_y_por_archivo():
    with db_fabrica("orbit_fab_cli") as conn:
        pid, lid = _producto(conn, sku="CLI", asin="B0CLICLI01", seller_sku="SS-CLI")
        _ledger_producto(conn, pid, hoy=HOY)
        _config(conn, "0.5")
        _campana_con_producto(conn, lid, external="c-vieja", status="PAUSED")
        dsn_db = _test_dsn().rsplit("/", 1)[0] + "/" + conn.info.dbname
        args = [a if a != "1" else str(pid) for a in ARGS_BASE]
        env = {**os.environ, "ORBIT_DSN_READ": dsn_db, "PYTHONPATH": str(RAIZ)}
        fuente = (RAIZ / "tools" / "fabrica_campanas.py").read_text(encoding="utf-8")
        for argv in (
            [sys.executable, "-", *args],  # por stdin (como en el contenedor)
            [sys.executable, str(RAIZ / "tools" / "fabrica_campanas.py"), *args],  # por archivo
        ):
            corrida = subprocess.run(argv, input=fuente, env=env, capture_output=True, text=True)
            assert corrida.returncode == 0, corrida.stderr
            assert "huella del conjunto:" in corrida.stdout
            eventos = [
                json.loads(linea) for linea in corrida.stdout.splitlines() if linea.startswith("{")
            ]
            assert any(e["evento"] == "dry_run" for e in eventos)
            assert "existente (solo se informa" in corrida.stdout
