"""tools/fabrica_campanas.py (FABRICA 01, tareas 6-10: plan, mutacion, registro).

SQL contra Postgres REAL (precedente: el %s::platform IndeterminateDatatype
solo lo vio una base de verdad); ventanas pineadas a la fecha UTC; dry-run y
guards sin HTTP; mutacion/registro/reversa con HTTP simulado y Postgres
desechable. Shapes de Amazon: HIPOTESIS hasta la sonda."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from psycopg.rows import tuple_row
from psycopg.types.json import Json
from test_fabrica_migracion import _entidad, _ledger_producto, _producto, db_fabrica
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.ads.config import AdsCredentials
from app.ads.structure_api import PerfilAds

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
@pytest.mark.parametrize("metrica", ["orders", "cost", "revenue"])
@pytest.mark.parametrize("cortes", [False, True], ids=["historial", "exact"])
def test_terminos_incompletos_conservan_none_y_no_siembran_exact(metrica, cortes):
    """SUM sin guarda inventaba un ACoS con datos parciales (revision #176).

    Solo la metrica faltante queda desconocida. Cero no es NULL y una
    reobservacion completa sana el dia antes de agregar la ventana.
    """
    with db_fabrica("orbit_fab_null") as conn:
        _pid, lid = _producto(conn)
        _camp, ag = _campana_con_producto(conn, lid)
        run = _run(conn)
        fecha = HOY - dt.timedelta(days=20)
        parcial = {"orders": 1, "cost": 10, "revenue": 100, metrica: None}
        _termino(conn, run, ag, "incompleto", fecha, **parcial)
        _termino(conn, run, ag, "incompleto", fecha - dt.timedelta(days=1), 2, 10, 100)
        _termino(conn, run, ag, "cero real", fecha, 2, 0, 100)
        _termino(conn, run, ag, "corregido", fecha, **parcial)
        _termino(conn, run, ag, "corregido", fecha, 2, 10, 100, obs=2)

        terminos = fc._terminos_producto(
            conn,
            "amazon_mx",
            [lid],
            sql=fc._SQL_TERMINOS_EXACT if cortes else fc._SQL_TERMINOS,
            ventana=fp.VENTANA_CORTES_DIAS if cortes else fp.VENTANA_DIAS,
        )
        por_texto = {t.texto: t for t in terminos}
        esperado = {"orders": 3, "cost": Decimal("20"), "revenue": Decimal("200")}
        esperado[metrica] = None
        incompleto = por_texto["incompleto"]
        assert (incompleto.orders, incompleto.cost, incompleto.revenue) == (
            esperado["orders"],
            esperado["cost"],
            esperado["revenue"],
        )
        assert por_texto["cero real"].cost == Decimal("0")
        corregido = por_texto["corregido"]
        assert (corregido.orders, corregido.cost, corregido.revenue) == (
            2,
            Decimal("10"),
            Decimal("100"),
        )
        semillas = fp.semillas_desde_terminos(
            terminos, [], [], Decimal("20"), terminos_exact=terminos
        )
        assert semillas.exact == ("cero real", "corregido")
        if metrica == "orders":
            assert "incompleto" not in semillas.keywords


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
    closes: el dry-run debe ser solo lectura y cerrar su conexion.

    D-GLM-7-10-4: kwargs opcionales para mutacion/registro/reversa sin romper
    el dry-run. RETURNING sintetico en INSERT de paso (id incremental) y
    campana_grupo (id=1)."""

    def __init__(
        self,
        *,
        settings=None,
        productos=(),
        terminos=(),
        terminos_exact=None,
        biblioteca=([], []),
        existentes=(),
        secuencia=None,
        lote_fila=None,
        pendientes=(),
        grupo=(),
        lote_platform="amazon_mx",
        goals=(),
    ):
        self.settings = settings
        self.productos = list(productos)
        self.terminos = list(terminos)
        # candidatos a exact (ventana de cortes): por default los mismos terminos
        self.terminos_exact = list(terminos) if terminos_exact is None else list(terminos_exact)
        self.biblioteca = biblioteca
        self.existentes = list(existentes)
        self.secuencia = secuencia
        self.lote_fila = lote_fila
        self.pendientes = list(pendientes)
        self.grupo = list(grupo)
        self.lote_platform = lote_platform
        self.goals = list(goals)
        self.escrituras = []  # (sql plano, params)
        self.commits = 0
        self.closes = 0
        self.rollbacks = 0
        self._paso_seq = 0
        self.row_factory = None

    def execute(self, sql, params=None):
        plano = " ".join(str(sql).split())
        bajo = plano.lower()
        if self.secuencia is not None:
            self.secuencia.append(("sql", plano))
        if bajo.startswith(("insert", "update", "delete")):
            self.escrituras.append((plano, params))
            if "insert into fabrica_lote_paso" in bajo:
                self._paso_seq += 1
                return _Cursor([(self._paso_seq,)])
            if "insert into campana_grupo " in bajo:
                return _Cursor([(1,)])
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
        if "from fabrica_lote_paso" in bajo and "left join campana_grupo" in bajo:
            return _Cursor(self.grupo)
        if "from fabrica_lote_paso" in bajo and "join fabrica_lote" in bajo:
            return _Cursor(self._pendientes_filtrados(params))
        if "from fabrica_lote_paso" in bajo and "recurso in" in bajo:
            return _Cursor(self.pendientes)
        if "from fabrica_lote" in bajo and "plan" in bajo:
            return _Cursor([self.lote_fila] if self.lote_fila is not None else [])
        if "from fabrica_lote" in bajo and "platform" in bajo:
            return _Cursor([(self.lote_platform,)])
        if "from ads_optimizer_goal" in bajo:
            return _Cursor(self.goals)
        if "from ad_entity" in bajo:
            return _Cursor([])
        raise AssertionError(f"SQL inesperado: {plano[:140]}")

    def _pendientes_filtrados(self, params):
        """_SQL_PENDIENTES: (lote, lote, plataforma, plataforma). Filtra en
        el fake para que el aislamiento MX/US no dependa de Postgres."""
        filas = list(self.pendientes)
        if not params or len(params) < 4:
            return filas
        lote, _lote2, plataforma, _plat2 = params[0], params[1], params[2], params[3]
        out = []
        for fila in filas:
            if lote is not None and fila[1] != lote:
                continue
            if plataforma is not None and fila[3] != plataforma:
                continue
            out.append(fila)
        return out

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

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
    assert conn.closes >= 1
    conn = _ConnFalsa(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO]
    )
    _frontera_lectura(monkeypatch, conn)
    args = [a if a != "6.00" else "45.01" for a in ARGS_BASE]  # bid-exact sobre el techo MXN
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *args])
    with pytest.raises(fc.Abortar, match="bid"):
        fc.main()
    assert conn.closes >= 1


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


def test_despacho_prioriza_reconciliar_registrar_y_desarmar(monkeypatch):
    """D-GLM-7-10-2: reconciliar → registrar → desarmar → crear. Reemplaza
    el rechazo temporal de tarea 6; cada camino tiene su test propio."""
    llamados = []

    def _marca(nombre):
        return lambda _args: llamados.append(nombre) or 0

    monkeypatch.setattr(fc, "_reconciliar_cmd", _marca("reconciliar"))
    monkeypatch.setattr(fc, "_registrar_cmd", _marca("registrar"))
    monkeypatch.setattr(fc, "_desarmar", _marca("desarmar"))
    monkeypatch.setattr(fc, "_crear", _marca("crear"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["fabrica_campanas.py", "--reconciliar", "--registrar", "L1", "--desarmar", "L2"],
    )
    assert fc.main() == 0 and llamados == ["reconciliar"]
    llamados.clear()
    monkeypatch.setattr(
        sys, "argv", ["fabrica_campanas.py", "--registrar", "L1", "--desarmar", "L2"]
    )
    assert fc.main() == 0 and llamados == ["registrar"]
    llamados.clear()
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L2"])
    assert fc.main() == 0 and llamados == ["desarmar"]


# ---------------------------------------------------------------------------
# CLI end-to-end sobre la DB de pruebas (stdin y archivo)
# ---------------------------------------------------------------------------


@_skip_db
@pytest.mark.parametrize(
    ("plataforma", "moneda", "bid"),
    [("amazon_mx", "MXN", "5.00"), ("amazon_us", "USD", "0.50")],
)
def test_cli_dry_run_end_to_end_por_stdin_y_por_archivo(plataforma, moneda, bid):
    """El margen del ledger MXN sirve tambien al plan US, cuyos bids son USD."""
    with db_fabrica("orbit_fab_cli") as conn:
        pid, lid = _producto(
            conn, sku="CLI", asin="B0CLICLI01", seller_sku="SS-CLI", platform=plataforma
        )
        _ledger_producto(conn, pid, hoy=HOY, platform=plataforma)
        _config(conn, "0.5", platform=plataforma)
        _campana_con_producto(conn, lid, external="c-vieja", status="PAUSED", platform=plataforma)
        dsn_db = _test_dsn().rsplit("/", 1)[0] + "/" + conn.info.dbname
        args = ARGS_BASE.copy()
        args[args.index("--productos") + 1] = str(pid)
        args[args.index("--plataforma") + 1] = plataforma
        for rol in ("auto", "phrase", "product", "broad", "exact"):
            args[args.index(f"--bid-{rol}") + 1] = bid
        env = {k: v for k, v in os.environ.items() if not k.startswith("ORBIT_")}
        env.update(ORBIT_DSN_READ=dsn_db, PYTHONPATH=str(RAIZ))
        fuente = (RAIZ / "tools" / "fabrica_campanas.py").read_text(encoding="utf-8")
        for argv in (
            [sys.executable, "-", *args],  # por stdin (como en el contenedor)
            [sys.executable, str(RAIZ / "tools" / "fabrica_campanas.py"), *args],  # por archivo
        ):
            corrida = subprocess.run(argv, input=fuente, env=env, capture_output=True, text=True)
            assert corrida.returncode == 0, corrida.stderr
            assert "huella del conjunto:" in corrida.stdout
            assert f"moneda={moneda}" in corrida.stdout
            assert f"bid={bid}" in corrida.stdout
            assert "target=20.00" in corrida.stdout
            eventos = [
                json.loads(linea) for linea in corrida.stdout.splitlines() if linea.startswith("{")
            ]
            assert any(
                e["evento"] == "dry_run" and e["platform"] == plataforma and e["target"] == "20.00"
                for e in eventos
            )
            assert "existente (solo se informa" in corrida.stdout


# ---------------------------------------------------------------------------
# Mutacion (tarea 7): autorizacion, ledger pre-HTTP, readback, detencion
# ---------------------------------------------------------------------------

_CREDS = AdsCredentials(client_id="cid", client_secret="sec", refresh_token="rt")


def _perfil(platform="amazon_mx", profile_id=101):
    moneda = "MXN" if platform == "amazon_mx" else "USD"
    pais = "MX" if platform == "amazon_mx" else "US"
    return PerfilAds(
        profile_id=profile_id,
        country=pais,
        currency_code=moneda,
        account_type="seller",
        valid_payment_method=True,
        account_name="t",
        aceptado=True,
        platform=platform,
        moneda=moneda,
        motivo=None,
    )


class _Amazon:
    """MockTransport: LWA + creates por path (cola) + readbacks por LIST.

    HIPOTESIS hasta la sonda (tarea 11): el 207 con success/error anidado
    lo inventa este mock siguiendo el sello del probe 2.5."""

    def __init__(self, fallar_en=None, readback_malo=None, secuencia=None):
        self.pedidos = []
        self.creados = {}
        self.fallar_en = fallar_en
        self.readback_malo = readback_malo
        self.objetos = {}
        self.secuencia = secuencia if secuencia is not None else []

    def __call__(self, request):
        self.pedidos.append(request)
        url = str(request.url)
        if "auth/o2/token" in url:
            self.secuencia.append(("http", "lwa"))
            return httpx.Response(200, json={"access_token": "tok"})
        path = url.replace(fc.API, "")
        self.secuencia.append(("http", path))
        if path.endswith("/list"):
            return self._list(path, json.loads(request.content))
        assert path in fp.VENDOR_POR_PATH, path
        assert request.headers["Content-Type"] == fp.VENDOR_POR_PATH[path]
        assert request.headers["Accept"] == fp.VENDOR_POR_PATH[path]
        cuerpo = json.loads(request.content)
        assert list(cuerpo) == [fp.ENVOLTURA_POR_PATH[path]]
        assert len(cuerpo[fp.ENVOLTURA_POR_PATH[path]]) == 1
        n = self.creados[path] = self.creados.get(path, 0) + 1
        if self.fallar_en == (path, n):
            return httpx.Response(400, json={"code": "400", "details": "rechazado"})
        clave = fp.CLAVE_ID_POR_PATH[path]
        ext = f"{fp.ENVOLTURA_POR_PATH[path]}-{n}"
        self.objetos[ext] = cuerpo[fp.ENVOLTURA_POR_PATH[path]][0]
        return httpx.Response(
            207,
            json={
                fp.ENVOLTURA_POR_PATH[path]: {"success": [{"index": 0, clave: ext}], "error": []}
            },
        )

    def _list(self, path, body):
        filtro = fp.FILTRO_ID_POR_LIST[path]
        ext = body[filtro]["include"][0]
        contenedor = fp.CONTENEDOR_POR_LIST[path]
        clave = fp.CLAVE_ID_POR_PATH[path.removesuffix("/list")]
        estado = "PAUSED" if ext == self.readback_malo else "ENABLED"
        return httpx.Response(
            200, json={contenedor: [{**self.objetos.get(ext, {}), clave: ext, "state": estado}]}
        )

    def posts(self, path):
        return [
            p
            for p in self.pedidos
            if str(p.url).endswith(path) and not str(p.url).endswith("/list")
        ]


class _ClienteLectura:
    def __init__(self, transporte):
        self._c = httpx.Client(transport=transporte)

    def list_objects(self, path, body, *, profile_id):
        return self._c.post(f"{fc.API}{path}", json=body)


def _frontera_mutacion(
    monkeypatch, conn_read, conn_admin, amazon, perfiles=None, stub_registrar=True
):
    _frontera_lectura(monkeypatch, conn_read)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "dsn-admin")
    monkeypatch.setattr(
        fc, "connect", lambda dsn: {"dsn-read": conn_read, "dsn-admin": conn_admin}[dsn]
    )
    monkeypatch.setattr(
        AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **k: _CREDS)
    )
    transporte = httpx.MockTransport(amazon)
    monkeypatch.setattr(fc, "AdsClient", lambda cred: _ClienteLectura(transporte))
    monkeypatch.setattr(
        fc, "evaluar_perfiles", lambda c: perfiles if perfiles is not None else [_perfil()]
    )
    monkeypatch.setattr(
        fc,
        "httpx",
        SimpleNamespace(
            Client=lambda **kw: httpx.Client(transport=transporte), Timeout=lambda **kw: None
        ),
    )
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    if stub_registrar:
        monkeypatch.setattr(fc, "_registrar", lambda ctx, plan, externos: None)


def _conn_plan(**extra):
    base = dict(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"},
        productos=[FILA_PRODUCTO],
        terminos=[("collar noche", False, 2, Decimal("10"), Decimal("100"))],
        biblioteca=(["collar led", "B0DDDDDDDD"], ["gato"]),
    )
    base.update(extra)
    return _ConnFalsa(**base)


def _args_go(huella, esperado=5, go="go"):
    return [
        "fabrica_campanas.py",
        *ARGS_BASE,
        "--acepto-mutacion-real",
        "--esperado",
        str(esperado),
        "--huella",
        huella,
        "--go",
        go,
    ]


def _huella_de(monkeypatch):
    conn = _conn_plan()
    _frontera_lectura(monkeypatch, conn)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    fc.main()
    return conn


def _pasos(conn_admin):
    return [
        p for s, p in conn_admin.escrituras if s.lower().startswith("insert into fabrica_lote_paso")
    ]


def _sellos(conn_admin, estado):
    return [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote_paso") and f"'{estado}'" in s.lower()
    ]


def _eventos(capsys):
    return [
        json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith("{")
    ]


def test_go_exige_esperado_huella_y_literal(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    for argv, patron in (
        (["fabrica_campanas.py", *ARGS_BASE, "--acepto-mutacion-real"], "esperado"),
        (_args_go(huella, esperado=4), "esperado 4"),
        (_args_go("deadbeef"), "huella"),
        (_args_go(huella, go=""), "go"),
    ):
        conn_admin = _ConnFalsa()
        _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, _Amazon())
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(fc.Abortar, match=patron):
            fc.main()
        assert conn_admin.escrituras == [], "ningun guard debe escribir el ledger"


def test_mutacion_orden_fijo_ledger_pre_http_y_readback(monkeypatch, capsys):
    """exact -> phrase -> broad -> product -> auto; ledger COMMIT antes del POST."""
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    secuencia = []
    conn_admin = _ConnFalsa(secuencia=secuencia)
    amazon = _Amazon(secuencia=secuencia)
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    assert fc.main() == 0
    campanas = [
        json.loads(p.content)["campaigns"][0]["name"] for p in amazon.posts("/sp/campaigns")
    ]
    assert [n.split(" | ")[2] for n in campanas] == list(fp.ROLES_ORDEN_CREACION)
    assert len(amazon.posts("/sp/adGroups")) == 5 and len(amazon.posts("/sp/productAds")) == 5
    assert len(amazon.posts("/sp/keywords")) == 1 + 2 + 2
    assert len(amazon.posts("/sp/targets")) == 1 and len(amazon.posts("/sp/negativeKeywords")) == 1
    kw_exact = json.loads(amazon.posts("/sp/keywords")[0].content)["keywords"][0]
    assert (
        kw_exact["matchType"] == "EXACT"
        and kw_exact["campaignId"] == "campaigns-1"
        and kw_exact["adGroupId"] == "adGroups-1"
    )
    pa = json.loads(amazon.posts("/sp/productAds")[0].content)["productAds"][0]
    assert pa == {
        "campaignId": "campaigns-1",
        "adGroupId": "adGroups-1",
        "sku": "SS-1",
        "state": "ENABLED",
    }
    total_posts = 5 + 5 + 5 + 5 + 1 + 1
    assert (
        len(_pasos(conn_admin)) == total_posts
        and len(_sellos(conn_admin, "applied")) == total_posts
    )
    lote = [
        p for s, p in conn_admin.escrituras if s.lower().startswith("insert into fabrica_lote ")
    ]
    assert len(lote) == 1 and lote[0][4] == "go" and lote[0][5] == huella
    assert conn_admin.commits >= 1 + 2 * total_posts
    i_lote = next(
        i
        for i, (t, x) in enumerate(secuencia)
        if t == "sql" and x.lower().startswith("insert into fabrica_lote ")
    )
    i_paso = next(
        i
        for i, (t, x) in enumerate(secuencia)
        if t == "sql" and x.lower().startswith("insert into fabrica_lote_paso")
    )
    i_campana = next(i for i, (t, x) in enumerate(secuencia) if (t, x) == ("http", "/sp/campaigns"))
    posts_creacion = [
        i
        for i, (t, x) in enumerate(secuencia)
        if t == "http" and x != "lwa" and not x.endswith("/list")
    ]
    assert posts_creacion, "no hubo POSTs de creacion"
    assert all(i_lote < i for i in posts_creacion), "un POST corrio antes del INSERT del lote"
    assert i_paso < i_campana, "el POST /sp/campaigns corrio antes del INSERT del paso"
    eventos = _eventos(capsys)
    assert eventos[-1]["evento"] == "reconciliacion_final" and eventos[-1]["ok"] is True


def test_rechazo_sella_failed_y_detiene_declarando_lo_creado(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon(fallar_en=("/sp/campaigns", 3))
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="category_broad"):
        fc.main()
    assert len(amazon.posts("/sp/campaigns")) == 3 and len(amazon.posts("/sp/adGroups")) == 2
    assert len(_sellos(conn_admin, "failed")) == 1
    lote_failed = [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "failed"
    ]
    assert len(lote_failed) == 1
    detenido = [e for e in _eventos(capsys) if e["evento"] == "lote_detenido"][0]
    assert detenido["creadas"] == ["category_exact", "category_phrase"]


def test_readback_que_no_cuadra_detiene(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon(readback_malo="campaigns-1")
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="readback"):
        fc.main()
    assert len(amazon.posts("/sp/campaigns")) == 1 and amazon.posts("/sp/adGroups") == []
    assert len(_sellos(conn_admin, "failed")) == 1


def test_sin_perfil_aceptado_no_muta(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon()
    _frontera_mutacion(
        monkeypatch, _conn_plan(), conn_admin, amazon, perfiles=[_perfil("amazon_us", 102)]
    )
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="perfil"):
        fc.main()
    assert amazon.posts("/sp/campaigns") == [] and conn_admin.escrituras == []


def test_readback_cuadra_exige_expresion_y_bid():
    payload_t = {
        "expressionType": "MANUAL",
        "expression": [{"type": "ASIN_SAME_AS", "value": "B0X"}],
        "state": "ENABLED",
        "bid": 5.0,
    }
    exp = [{"type": "ASIN_SAME_AS", "value": "B0X"}]
    base = {"state": "ENABLED", "expression": exp, "bid": 5.0}
    assert fc._readback_cuadra(base, payload_t)
    assert not fc._readback_cuadra(
        {**base, "expression": [{"type": "ASIN_SAME_AS", "value": "B0OTRO"}]}, payload_t
    )
    assert not fc._readback_cuadra({**base, "expression": []}, payload_t)
    assert not fc._readback_cuadra({**base, "bid": 4.0}, payload_t)
    assert not fc._readback_cuadra({k: v for k, v in base.items() if k != "bid"}, payload_t)
    assert fc._readback_cuadra({**base, "bid": "5.00"}, payload_t)
    payload_kw = {"keywordText": "k", "matchType": "PHRASE", "state": "ENABLED", "bid": 6.0}
    leido_kw = {"keywordText": "k", "matchType": "PHRASE", "state": "ENABLED", "bid": 6.0}
    assert fc._readback_cuadra(leido_kw, payload_kw)
    assert not fc._readback_cuadra({**leido_kw, "matchType": "EXACT"}, payload_kw)
    payload_ag = {"name": "g", "state": "ENABLED", "defaultBid": 4.5}
    leido_ag = {"name": "g", "state": "ENABLED", "defaultBid": 4.5}
    assert fc._readback_cuadra(leido_ag, payload_ag)
    assert not fc._readback_cuadra({**leido_ag, "defaultBid": 4.0}, payload_ag)
    assert not fc._readback_cuadra({"name": "g", "state": "ENABLED"}, payload_ag)
    payload_c = {
        "name": "c",
        "state": "ENABLED",
        "budget": {"budgetType": "DAILY", "budget": 150.0},
    }
    leido_c = {"name": "c", "state": "ENABLED", "budget": {"budgetType": "DAILY", "budget": 150.0}}
    assert fc._readback_cuadra(leido_c, payload_c)
    assert not fc._readback_cuadra(
        {**leido_c, "budget": {"budgetType": "DAILY", "budget": 100.0}}, payload_c
    )
    assert not fc._readback_cuadra(
        {**leido_c, "budget": {"budgetType": "LIFETIME", "budget": 150.0}}, payload_c
    )
    payload_hijo = {
        "name": "g",
        "state": "ENABLED",
        "defaultBid": 4.5,
        "campaignId": "camp-A",
        "adGroupId": "ag-A",
    }
    leido_hijo = {
        "name": "g",
        "state": "ENABLED",
        "defaultBid": 4.5,
        "campaignId": "camp-A",
        "adGroupId": "ag-A",
    }
    assert fc._readback_cuadra(leido_hijo, payload_hijo)
    assert not fc._readback_cuadra({**leido_hijo, "campaignId": "camp-OTRA"}, payload_hijo)
    assert not fc._readback_cuadra({**leido_hijo, "adGroupId": "ag-OTRA"}, payload_hijo)


def test_post_incierto_declara_incierto_no_rechazado(monkeypatch, capsys):
    """Timeout/excepcion en POST: Abortar dice INCERTO, no 'rechazado'."""
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)

    def _boom(*_a, **_k):
        raise TimeoutError("simulado")

    monkeypatch.setattr(fc, "_post", _boom)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="INCERTO"):
        fc.main()
    detenido = [e for e in _eventos(capsys) if e["evento"] == "lote_detenido"][0]
    assert "INCERTO" in detenido["motivo"]
    assert "rechazado" not in detenido["motivo"].lower()


def test_fallo_de_registro_sella_failed_con_rollback_previo(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]
    conn_admin = _ConnFalsa()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, _Amazon())

    def _boom(ctx, plan, creadas):
        raise RuntimeError("sync caido")

    monkeypatch.setattr(fc, "_registrar", _boom)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="registro interno incompleto"):
        fc.main()
    sellos = [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "failed"
    ]
    assert len(sellos) == 1 and "registro interno incompleto" in sellos[0][1]
    assert conn_admin.rollbacks >= 1, "rollback antes de sellar (la txn pudo quedar abortada)"


# ---------------------------------------------------------------------------
# Registro (tarea 8): sync, grupo, goals, --registrar idempotente
# ---------------------------------------------------------------------------

_CREADAS = [
    {"rol": rol, "campaign": f"c-{rol}", "ad_group": f"ag-{rol}", "product_ads": [], "semillas": []}
    for rol in fp.ROLES_ORDEN_CREACION
]


def _plan_min():
    return fp.PlanGrupo(
        platform="amazon_mx",
        tipo_producto="collar_perro",
        nombre_base="Collar",
        fecha=HOY,
        moneda="MXN",
        modo="shadow",
        productos=(fp.ProductoGrupo(1, "ODOO-1", 11, "B0AAAAAAAA", "SS-1", Decimal("38.20")),),
        parametros={
            rol: fp.ParametrosRol(rol, Decimal("120"), Decimal("6.00"))
            for rol in fp.ROLES_ORDEN_CREACION
        },
        target=fp.target_del_grupo([Decimal("38.20")], Decimal("0.5")),
        semillas=fp.Semillas((), (), (), ()),
    )


@_skip_db
def test_registrar_escribe_grupo_roles_productos_y_goals(monkeypatch):
    with db_fabrica("orbit_fab_reg") as conn:
        pid, lid = _producto(conn)
        for c in _CREADAS:
            camp = _entidad(conn, "amazon_mx", "campaign", c["campaign"])
            _entidad(conn, "amazon_mx", "ad_group", c["ad_group"], parent=camp)
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base,"
            " go_literal, huella, plan, modo_goal, estado) VALUES"
            " ('L1', 'amazon_mx', 'collar_perro', 'Collar', 'go', 'h',"
            " '{}', 'shadow', 'planeado')"
        )
        monkeypatch.setattr(fc, "_sync", lambda cliente: None)
        plan = dataclasses.replace(
            _plan_min(),
            productos=(
                fp.ProductoGrupo(pid, "ODOO-1", lid, "B0AAAAAAAA", "SS-1", Decimal("38.20")),
            ),
        )
        ctx = fc._Ctx(None, "tok", None, None, 101, conn, "L1")
        grupo = fc._registrar(ctx, plan, _CREADAS)
        conn.row_factory = tuple_row
        fila = conn.execute(
            "SELECT target_acos_pct, target_derivado_pct, fraccion, lote"
            " FROM campana_grupo WHERE id = %s",
            (grupo,),
        ).fetchone()
        assert fila == (Decimal("19.10"), Decimal("19.1000"), Decimal("0.5000"), "L1")
        roles = conn.execute(
            "SELECT r.rol, c.external_id, g.external_id FROM campana_grupo_rol r"
            " JOIN ad_entity c ON c.id = r.ad_entity_id"
            " JOIN ad_entity g ON g.id = r.ad_group_ad_entity_id"
            " WHERE r.grupo_id = %s ORDER BY r.rol",
            (grupo,),
        ).fetchall()
        assert (
            len(roles) == 5 and ("category_exact", "c-category_exact", "ag-category_exact") in roles
        )
        assert conn.execute(
            "SELECT seller_sku, margen_neto_pct FROM campana_grupo_producto WHERE grupo_id = %s",
            (grupo,),
        ).fetchone() == ("SS-1", Decimal("38.2000"))
        goals = conn.execute(
            "SELECT g.mode, g.enabled, g.target_acos_pct, g.harvest_campaign_id,"
            " g.harvest_ad_group_id,"
            " g.harvest_default_bid, g.bid_floor FROM ads_optimizer_goal g"
            " JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id WHERE r.grupo_id = %s",
            (grupo,),
        ).fetchall()
        assert len(goals) == 5
        assert all(
            g
            == (
                "shadow",
                True,
                Decimal("19.10"),
                "c-category_exact",
                "ag-category_exact",
                Decimal("6.0000"),
                Decimal("1.0000"),
            )
            for g in goals
        )


@_skip_db
def test_registrar_aborta_si_el_sync_no_trajo_la_campana(monkeypatch):
    with db_fabrica("orbit_fab_reg2") as conn:
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base,"
            " go_literal, huella, plan, modo_goal, estado) VALUES"
            " ('L1', 'amazon_mx', 'collar_perro', 'Collar', 'go', 'h',"
            " '{}', 'shadow', 'planeado')"
        )
        monkeypatch.setattr(fc, "_sync", lambda cliente: None)
        ctx = fc._Ctx(None, "tok", None, None, 101, conn, "L1")
        with pytest.raises(fc.Abortar, match="ad_entity"):
            fc._registrar(ctx, _plan_min(), _CREADAS)
        assert conn.execute("SELECT count(*) FROM campana_grupo").fetchone()[0] == 0


def test_registrar_sincroniza_antes_de_escribir(monkeypatch):
    llamadas = []
    conn = _ConnFalsa()
    monkeypatch.setattr(fc, "_sync", lambda cliente: llamadas.append("sync"))
    monkeypatch.setattr(fc, "_id_entidad", lambda c, p, k, e: llamadas.append(f"id:{k}:{e}") or 7)
    monkeypatch.setattr(
        fc.goals_write, "crea_goal", lambda c, **kw: llamadas.append("goal") or {"id": 1}
    )
    ctx = fc._Ctx(None, "tok", None, "cliente", 101, conn, "L1")
    fc._registrar(ctx, _plan_min(), _CREADAS)
    assert llamadas[0] == "sync" and llamadas.count("goal") == 5
    assert any(s.lower().startswith("insert into campana_grupo ") for s, _ in conn.escrituras)


def test_sync_usa_dsn_ingest_y_el_escritor_unico(monkeypatch):
    llamadas = {}
    monkeypatch.setenv("ORBIT_DSN_INGEST", "dsn-ingest")
    monkeypatch.setattr(
        fc, "connect", lambda dsn: llamadas.setdefault("dsn", dsn) and "conn-ingest"
    )
    monkeypatch.setattr(fc, "fetch_structure", lambda cliente: "estructura")
    monkeypatch.setattr(
        fc, "sync_structure", lambda conn, est: llamadas.setdefault("sync", (conn, est))
    )
    fc._sync("cliente")
    assert llamadas == {"dsn": "dsn-ingest", "sync": ("conn-ingest", "estructura")}


def test_registrar_cmd_reintenta_solo_el_registro(monkeypatch):
    plan_json = fp.plan_como_json(_plan_min())
    pasos = [
        (rol, recurso, f"{prefijo}-{rol}")
        for rol in fp.ROLES_ORDEN_CREACION
        for recurso, prefijo in (("campaign", "c"), ("ad_group", "ag"))
    ]
    conn_admin = _ConnFalsa(lote_fila=(plan_json, "failed"), pendientes=pasos)
    amazon = _Amazon()
    llamadas = []
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon, stub_registrar=False)
    monkeypatch.setattr(fc, "_sync", lambda cliente: llamadas.append("sync"))
    monkeypatch.setattr(fc, "_id_entidad", lambda c, p, k, e: llamadas.append(f"id:{k}:{e}") or 7)
    monkeypatch.setattr(
        fc.goals_write, "crea_goal", lambda c, **kw: llamadas.append("goal") or {"id": 1}
    )
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L1"])
    assert fc.main() == 0
    assert llamadas[0] == "sync" and llamadas.count("goal") == 5
    sellos = [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "applied"
    ]
    assert len(sellos) == 1
    assert amazon.pedidos == [], "el reintento del registro no abre HTTP"


@_skip_db
def test_registrar_cmd_doble_corrida_es_idempotente(monkeypatch):
    with db_fabrica("orbit_fab_reg3") as conn:
        pid, lid = _producto(conn)
        for c in _CREADAS:
            camp = _entidad(conn, "amazon_mx", "campaign", c["campaign"])
            _entidad(conn, "amazon_mx", "ad_group", c["ad_group"], parent=camp)
        plan = dataclasses.replace(
            _plan_min(),
            productos=(
                fp.ProductoGrupo(pid, "ODOO-1", lid, "B0AAAAAAAA", "SS-1", Decimal("38.20")),
            ),
        )
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('L1', 'amazon_mx', 'collar_perro',"
            " 'Collar', 'go', 'h', %s, 'shadow', 'failed')",
            (Json(fp.plan_como_json(plan)),),
        )
        orden = 0
        for c in _CREADAS:
            for recurso, ext in (("campaign", c["campaign"]), ("ad_group", c["ad_group"])):
                orden += 1
                conn.execute(
                    "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                    " external_id, ack, readback_estado, estado) VALUES ('L1', %s,"
                    " %s::campana_rol, %s, '{}'::jsonb, %s, '{}'::jsonb, 'ENABLED', 'applied')",
                    (orden, c["rol"], recurso, ext),
                )
        monkeypatch.setattr(fc, "connect", lambda dsn: conn)
        conn.close = lambda: None  # el cmd cierra; la fixture reusa esta conn
        monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://fake")
        monkeypatch.setattr(
            AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **k: _CREDS)
        )
        monkeypatch.setattr(fc, "AdsClient", lambda cred: None)
        monkeypatch.setattr(fc, "evaluar_perfiles", lambda c: [_perfil()])
        monkeypatch.setattr(fc, "_sync", lambda cliente: None)
        monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L1"])
        assert fc.main() == 0
        conn.execute("UPDATE fabrica_lote SET estado = 'failed' WHERE lote = 'L1'")
        conn.row_factory = tuple_row
        assert fc.main() == 0
        conn.row_factory = tuple_row
        filas = conn.execute(
            "SELECT id, target_procedencia, go_literal FROM campana_grupo WHERE lote = 'L1'"
        ).fetchall()
        assert len(filas) == 1 and filas[0][2] == "go"
        grupo = filas[0][0]
        assert "margen_minimo_grupo" in filas[0][1]
        n_roles = conn.execute(
            "SELECT count(*) FROM campana_grupo_rol WHERE grupo_id = %s", (grupo,)
        ).fetchone()[0]
        n_prods = conn.execute(
            "SELECT count(*) FROM campana_grupo_producto WHERE grupo_id = %s", (grupo,)
        ).fetchone()[0]
        n_goals = conn.execute(
            "SELECT count(*) FROM ads_optimizer_goal g JOIN campana_grupo_rol r"
            " ON r.ad_entity_id = g.ad_entity_id WHERE r.grupo_id = %s",
            (grupo,),
        ).fetchone()[0]
        assert (n_roles, n_prods, n_goals) == (5, 1, 5)


def test_registrar_rechaza_lote_desarmado(monkeypatch):
    conn_admin = _ConnFalsa(lote_fila=({"x": 1}, "desarmado"))
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, _Amazon())
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L9"])
    with pytest.raises(fc.Abortar, match="desarmado"):
        fc.main()


def test_registrar_aborta_si_goal_existente_apagado(monkeypatch):
    plan_json = fp.plan_como_json(_plan_min())
    pasos = [
        (rol, recurso, f"{prefijo}-{rol}")
        for rol in fp.ROLES_ORDEN_CREACION
        for recurso, prefijo in (("campaign", "c"), ("ad_group", "ag"))
    ]
    goals = [(50, False, "shadow", "c-category_exact", "ag-category_exact", Decimal("6.00"))]
    conn_admin = _ConnFalsa(lote_fila=(plan_json, "failed"), pendientes=pasos, goals=goals)
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, _Amazon(), stub_registrar=False)
    monkeypatch.setattr(fc, "_sync", lambda cliente: None)
    monkeypatch.setattr(fc, "_id_entidad", lambda c, p, k, e: 7)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L1"])
    with pytest.raises(fc.Abortar, match="no esta listo"):
        fc.main()
    sellos = [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "applied"
    ]
    assert sellos == []


# ---------------------------------------------------------------------------
# Reversa y reconciliacion (tarea 9)
# ---------------------------------------------------------------------------


class _AmazonDesarme(_Amazon):
    def __init__(self, estado_readback="PAUSED"):
        super().__init__()
        self.puts = []
        self.estado_readback = estado_readback

    def __call__(self, request):
        if request.method == "PUT":
            self.puts.append(request)
            assert request.headers["Content-Type"] == fp.VENDOR_POR_PATH["/sp/campaigns"]
            cuerpo = json.loads(request.content)["campaigns"][0]
            assert cuerpo["state"] == "PAUSED" and isinstance(cuerpo["campaignId"], str)
            return httpx.Response(
                207,
                json={
                    "campaigns": {
                        "success": [{"index": 0, "campaignId": cuerpo["campaignId"]}],
                        "error": [],
                    }
                },
            )
        if str(request.url).endswith("/sp/campaigns/list"):
            ext = json.loads(request.content)["campaignIdFilter"]["include"][0]
            return httpx.Response(
                200, json={"campaigns": [{"campaignId": ext, "state": self.estado_readback}]}
            )
        return super().__call__(request)


_FILAS_LOTE = [(rol, f"c-{rol}", 50 + i) for i, rol in enumerate(fp.ROLES_ORDEN_CREACION)]


def test_desarmar_pausa_las_5_y_apaga_sus_goals(monkeypatch, capsys):
    conn_admin = _ConnFalsa(grupo=_FILAS_LOTE)
    amazon = _AmazonDesarme()
    apagados = []
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(
        fc.goals_write,
        "edita_goal",
        lambda c, gid, **kw: apagados.append((gid, kw["enabled"])) or {},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["fabrica_campanas.py", "--desarmar", "L1", "--acepto-mutacion-real", "--go", "pausa"],
    )
    assert fc.main() == 0
    assert len(amazon.puts) == 5
    assert apagados == [(gid, False) for _, _, gid in _FILAS_LOTE]
    sellos = [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "desarmado"
    ]
    assert len(sellos) == 1
    assert _eventos(capsys)[-1]["evento"] == "reconciliacion_final"


def test_desarmar_sin_go_es_dry_run_y_readback_malo_detiene(monkeypatch, capsys):
    conn_admin = _ConnFalsa(grupo=_FILAS_LOTE)
    amazon = _AmazonDesarme()
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L1"])
    assert fc.main() == 0 and amazon.puts == [] and conn_admin.escrituras == []
    monkeypatch.setattr(
        sys, "argv", ["fabrica_campanas.py", "--desarmar", "L1", "--acepto-mutacion-real"]
    )
    with pytest.raises(fc.Abortar, match="go"):
        fc.main()
    amazon = _AmazonDesarme(estado_readback="ENABLED")
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(
        sys,
        "argv",
        ["fabrica_campanas.py", "--desarmar", "L1", "--acepto-mutacion-real", "--go", "pausa"],
    )
    with pytest.raises(fc.Abortar, match="readback"):
        fc.main()
    assert len(amazon.puts) == 1


def test_desarmar_lote_sin_pasos_applied_aborta(monkeypatch):
    conn_admin = _ConnFalsa(grupo=[])
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, _AmazonDesarme())
    monkeypatch.setattr(
        sys,
        "argv",
        ["fabrica_campanas.py", "--desarmar", "L9", "--acepto-mutacion-real", "--go", "x"],
    )
    with pytest.raises(fc.Abortar, match="L9"):
        fc.main()


def test_desarmar_lote_failed_a_medias_pausa_lo_applied_y_lo_failed_con_external(
    monkeypatch, capsys
):
    filas = [(rol, f"c-{rol}", None) for rol in fp.ROLES_ORDEN_CREACION[:3]]
    rol_broad = fp.ROLES_ORDEN_CREACION[3]
    filas.append((rol_broad, f"c-{rol_broad}", None))
    conn_admin = _ConnFalsa(grupo=filas)
    amazon = _AmazonDesarme()
    apagados = []
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)

    def _edita(c, gid, **kw):
        apagados.append((gid, kw))
        return {}

    monkeypatch.setattr(fc.goals_write, "edita_goal", _edita)
    monkeypatch.setattr(
        sys,
        "argv",
        ["fabrica_campanas.py", "--desarmar", "L7", "--acepto-mutacion-real", "--go", "pausa"],
    )
    assert fc.main() == 0
    assert len(amazon.puts) == 4
    assert apagados == []
    sellos = [
        p
        for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "desarmado"
    ]
    assert len(sellos) == 1
    assert _eventos(capsys)[-1]["evento"] == "reconciliacion_final"


_PENDIENTES = [
    (
        1,
        "L1",
        "category_exact",
        "amazon_mx",
        "campaign",
        "/sp/campaigns",
        "campaigns-1",
        {"name": "x"},
    ),
    (2, "L1", "category_exact", "amazon_mx", "ad_group", "/sp/adGroups", None, {"name": "y"}),
    (
        3,
        "L1",
        "category_phrase",
        "amazon_mx",
        "keyword",
        "/sp/keywords",
        "keywords-9",
        {"keywordText": "k", "matchType": "PHRASE"},
    ),
]


def test_reconciliar_promueve_solo_lo_verificado_y_aborta_si_queda_sin_verificar(
    monkeypatch, capsys
):
    conn_admin = _ConnFalsa(pendientes=_PENDIENTES)
    amazon = _Amazon()
    amazon.objetos = {
        "campaigns-1": {"name": "x"},
        "keywords-9": {"keywordText": "k", "matchType": "PHRASE"},
    }
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--reconciliar", "--lote", "L1"])
    with pytest.raises(fc.Abortar, match="sin verificar"):
        fc.main()
    promovidos = [p for s, p in conn_admin.escrituras if "estado = 'applied'" in s.lower()]
    assert [p[-1] for p in promovidos] == [1, 3]
    resumen = [e for e in _eventos(capsys) if e["evento"] == "reconciliacion"][0]
    assert resumen["recuperadas"] == 2 and resumen["sin_verificar"] == 1


def test_reconciliar_aborta_si_vive_pero_no_cuadra(monkeypatch, capsys):
    conn_admin = _ConnFalsa(pendientes=_PENDIENTES)
    amazon = _Amazon()
    amazon.objetos = {
        "campaigns-1": {"name": "x"},
        "keywords-9": {"keywordText": "OTRA", "matchType": "PHRASE"},
    }
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--reconciliar", "--lote", "L1"])
    with pytest.raises(fc.Abortar, match="no cuadran"):
        fc.main()
    promovidos = [p for s, p in conn_admin.escrituras if "estado = 'applied'" in s.lower()]
    assert [p[-1] for p in promovidos] == [1]
    resumen = [e for e in _eventos(capsys) if e["evento"] == "reconciliacion"][0]
    assert resumen["ausentes"] == 1 and resumen["sin_verificar"] == 1


def test_reconciliar_con_plataforma_excluye_la_otra_y_no_la_cuenta(monkeypatch, capsys):
    pendientes = _PENDIENTES + [
        (
            4,
            "L2",
            "category_exact",
            "amazon_us",
            "campaign",
            "/sp/campaigns",
            "campaigns-9",
            {"name": "us"},
        ),
    ]
    conn_admin = _ConnFalsa(pendientes=pendientes)
    amazon = _Amazon()
    amazon.objetos = {
        "campaigns-1": {"name": "x"},
        "keywords-9": {"keywordText": "k", "matchType": "PHRASE"},
        "campaigns-9": {"name": "us"},
    }
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(
        sys, "argv", ["fabrica_campanas.py", "--reconciliar", "--plataforma", "amazon_mx"]
    )
    with pytest.raises(fc.Abortar, match="sin verificar"):
        fc.main()
    assert amazon.posts("/sp/campaigns") == []
    lists = [p for p in amazon.pedidos if str(p.url).endswith("/list")]
    assert all(b"campaigns-9" not in p.content for p in lists), "el paso US no se LISTea"
    promovidos = [p for s, p in conn_admin.escrituras if "estado = 'applied'" in s.lower()]
    assert [p[-1] for p in promovidos] == [1, 3]
    resumen = [e for e in _eventos(capsys) if e["evento"] == "reconciliacion"][0]
    assert resumen["pendientes"] == 3 and resumen["recuperadas"] == 2
    assert resumen["sin_verificar"] == 1


def test_reconciliar_lote_y_plataforma_cruzados_aborta(monkeypatch):
    conn_admin = _ConnFalsa(lote_platform="amazon_mx", pendientes=_PENDIENTES)
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, _Amazon())
    monkeypatch.setattr(
        sys,
        "argv",
        ["fabrica_campanas.py", "--reconciliar", "--lote", "L1", "--plataforma", "amazon_us"],
    )
    with pytest.raises(fc.Abortar, match="contradictorios"):
        fc.main()


@_skip_db
def test_sql_del_ledger_contra_postgres_real():
    with db_fabrica("orbit_fab_sql") as conn:
        fc._inserta_lote(conn, "L1", _plan_min(), "h", "go")
        ctx = fc._Ctx(None, "tok", None, None, 101, conn, "L1")
        paso_c = fp.Paso("category_exact", "campaign", "/sp/campaigns", {"name": "x"}, "c")
        paso_a = fp.Paso("category_phrase", "ad_group", "/sp/adGroups", {"name": "y"}, "a")
        pid1 = fc._inserta_paso(ctx, paso_c, {"name": "x"})
        fc._sella_paso(ctx, pid1, True, "c-1", {"ok": 1}, "ENABLED")
        pid2 = fc._inserta_paso(ctx, paso_a, {"name": "y"})
        fc._sella_paso(ctx, pid2, False, "ag-2", {"status": 207}, "PAUSED")
        paso_c2 = fp.Paso("category_broad", "campaign", "/sp/campaigns", {"name": "z"}, "c")
        pid3 = fc._inserta_paso(ctx, paso_c2, {"name": "z"})
        fc._sella_paso(ctx, pid3, False, "c-3", {"status": 207}, "PAUSED")
        paso_c3 = fp.Paso("auto_discovery", "campaign", "/sp/campaigns", {"name": "w"}, "c")
        pid4 = fc._inserta_paso(ctx, paso_c3, {"name": "w"})
        fc._sella_paso(ctx, pid4, False, None, {"status": 400}, None)
        filas = conn.execute(fc._SQL_CAMPANAS_DEL_LOTE, ("L1",)).fetchall()
        assert [(f[0], f[1], f[2]) for f in filas] == [
            ("category_exact", "c-1", None),
            ("category_broad", "c-3", None),
        ]
        pend = conn.execute(fc._SQL_PENDIENTES, ("L1", "L1", None, None)).fetchall()
        esperado = [
            (pid2, "ad_group", "/sp/adGroups", "ag-2"),
            (pid3, "campaign", "/sp/campaigns", "c-3"),
            (pid4, "campaign", "/sp/campaigns", None),
        ]
        assert [(p[0], p[4], p[5], p[6]) for p in pend] == esperado
        assert {p[3] for p in pend} == {"amazon_mx"}
        conn.execute(fc._SQL_PROMUEVE_APPLIED, (json.dumps({"fuente": "t"}), "ENABLED", pid2))
        conn.execute(fc._SQL_PROMUEVE_APPLIED, (json.dumps({"fuente": "t"}), "ENABLED", pid3))
        conn.commit()
        pend_final = conn.execute(fc._SQL_PENDIENTES, ("L1", "L1", None, None)).fetchall()
        assert [(p[0], p[6]) for p in pend_final] == [(pid4, None)]
        fc._sella_lote(conn, "L1", "failed", "detalle")
        fila = conn.execute("SELECT estado, detalle FROM fabrica_lote WHERE lote = 'L1'").fetchone()
        assert fila == ("failed", "detalle")
