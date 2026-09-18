"""Recuadro de cobertura del catalogo (REPRICING 01, A.7).

`app/precio/cobertura.py` (puro: filas -> recuadro S10 por plataforma) +
`app/precio/fuentes.py` (solo SELECT: fuente canonica
`spapi_listing_estado_observation`, canal, goals, decisiones, puente) +
`tools/precio_cobertura.py --platform <p>` (lector con `ORBIT_DSN_READ`).

Base real (`ORBIT_TEST_DSN`, cero `skipped` con el DSN de VERIFY): base
temporal con ORDENCOB (0001 + 0002 + 0028 + 0033 + 0034 + 0035 + 0039; la
0035 crea la fuente canonica y no esta en ORDEN39, que no se toca).
"""

from __future__ import annotations

import itertools
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente

RAIZ = Path(__file__).resolve().parents[1]
ORDENCOB = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0033_ingest_run_llamadas.sql",
    "0034_ingest_run_llamadas_grant.sql",
    "0035_spapi_listings_inventario.sql",
    "0039_precio.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    """DB temporal con ORDENCOB; entrega `(conn, dsn)` con conn en autocommit."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"cob_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDENCOB:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


def _config(conn, settings=None) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        (
            "test-cobertura",
            Json(
                dict(
                    settings
                    if settings is not None
                    else {"precio_catalogo_max_dias_sin_reportar": 3}
                )
            ),
        ),
    ).fetchone()[0]


def _producto(conn, sku="SKU-COB-1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto cobertura') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing(conn, producto: int, *, platform="amazon_mx", ext="ASIN-C1", sku="SKU-C1") -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (producto, platform, ext, sku),
    ).fetchone()[0]


def _estado(conn, *, sku="SKU-C1", platform="amazon_mx", status="BUYABLE", observed=None) -> int:
    observed = observed or datetime.now(UTC)
    return conn.execute(
        "INSERT INTO spapi_listing_estado_observation (seller_sku, platform, status,"
        " api_version, observed_at)"
        " VALUES (%s, %s, %s, 'v1', %s) RETURNING id",
        (sku, platform, status, observed),
    ).fetchone()[0]


def _oferta(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    ext="ASIN-C1",
    sku="SKU-C1",
    canal="fba",
    precio="116",
    observed=None,
) -> int:
    marca = observed or datetime.now(UTC)
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'MXN', %s, %s, %s, %s, %s) RETURNING id",
        (
            listing,
            platform,
            sku,
            ext,
            canal,
            Decimal(precio),
            marca,
            marca,
            f"oferta-cob-{sku}-{ext}-{canal}",
            Json({}),
            f"ctx-cob-{sku}",
        ),
    ).fetchone()[0]


def _goal(conn, listing: int, *, platform="amazon_mx", pct="0.30") -> int:
    return conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por, go_literal)"
        " VALUES (%s, %s, %s, 'shadow', (now() AT TIME ZONE 'UTC')::date, 't', NULL)"
        " RETURNING id",
        (listing, platform, Decimal(pct)),
    ).fetchone()[0]


_MONEDAS = "'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN'"


def _decision(conn, listing: int, *, platform="amazon_mx", resultado, motivo=None) -> int:
    if resultado in ("subir", "bajar"):
        return conn.execute(
            "INSERT INTO precio_decision (listing_id, platform, resultado,"
            " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
            " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
            " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
            " VALUES (%s, %s, %s, 0.30, 0.24, 116, 'MXN', 116, 'MXN', 116, 'MXN',"
            " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', 'shadow')"
            " RETURNING id",
            (listing, platform, resultado),
        ).fetchone()[0]
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo,"
        " p_actual_currency, p_objetivo_currency, p_aplicado_currency, i_currency,"
        " c_currency, f_currency, l_currency, r_currency, mode)"
        f" VALUES (%s, %s, %s, %s, {_MONEDAS}, 'shadow') RETURNING id",
        (listing, platform, resultado, motivo),
    ).fetchone()[0]


def _pub(
    conn,
    *,
    sku,
    ext=None,
    platform="amazon_mx",
    canal="fba",
    estado="BUYABLE",
    goal=False,
    resultado=None,
    motivo=None,
    observed=None,
):
    """Publicacion completa: listing + estado + oferta (+ goal + decision)."""
    ext = ext or sku.replace("SKU", "ASIN")
    prod = _producto(conn, sku=f"ODOO-{sku}")
    listing = _listing(conn, prod, platform=platform, ext=ext, sku=sku)
    _estado(conn, sku=sku, platform=platform, status=estado, observed=observed)
    if canal is not None:
        _oferta(conn, listing, platform=platform, ext=ext, sku=sku, canal=canal, observed=observed)
    if goal:
        _goal(conn, listing, platform=platform)
    if resultado is not None:
        _decision(conn, listing, platform=platform, resultado=resultado, motivo=motivo)
    return listing


def _tool(*args, dsn):
    """Corre `tools/precio_cobertura.py` solo contra el DSN dado (lector)."""
    env = dict(os.environ)
    env["ORBIT_DSN_READ"] = dsn
    env["ORBIT_DSN_ADMIN"] = "postgresql://orbit:orbit@127.0.0.1:1/nula"
    env["ORBIT_DSN_DECIDE"] = "postgresql://orbit:orbit@127.0.0.1:1/nula"
    env.pop("ORBIT_PG_HOST", None)
    env["PYTHONPATH"] = RAIZ.as_posix()
    return subprocess.run(
        [sys.executable, "tools/precio_cobertura.py", *args],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        env=env,
    )


# ---------------------------------------------------------------------------
# Recuadro puro: 12 publicaciones en los cuatro estados, cuadra exacto
# ---------------------------------------------------------------------------


def _doce():
    from app.precio.cobertura import FilaPublicacion

    filas = [
        FilaPublicacion(
            1, "SKU-E1", "amazon_mx", "fba", Decimal("116"), "MXN", 0, True, "subir", None
        ),
        FilaPublicacion(
            2,
            "SKU-E2",
            "amazon_mx",
            "fba",
            Decimal("116"),
            "MXN",
            0,
            True,
            "mantener",
            "en_tolerancia",
        ),
        FilaPublicacion(
            3, "SKU-E3", "amazon_mx", "fba", Decimal("116"), "MXN", 0, True, "bajar", None
        ),
        FilaPublicacion(
            4,
            "SKU-N1",
            "amazon_mx",
            "fba",
            Decimal("116"),
            "MXN",
            0,
            True,
            "no_evaluado",
            "fee_ausente",
        ),
        FilaPublicacion(
            5,
            "SKU-N2",
            "amazon_mx",
            "fba",
            Decimal("116"),
            "MXN",
            0,
            True,
            "no_evaluado",
            "oferta_desactualizada",
        ),
        FilaPublicacion(
            6, "SKU-N3", "amazon_mx", "fba", Decimal("116"), "MXN", 0, True, None, None
        ),
        FilaPublicacion(
            7, "SKU-S1", "amazon_mx", "fba", Decimal("200"), "MXN", 0, False, None, None
        ),
        FilaPublicacion(
            8, "SKU-S2", "amazon_mx", "fba", Decimal("300"), "MXN", 0, False, None, None
        ),
        FilaPublicacion(9, "SKU-S3", "amazon_mx", None, None, None, 0, False, None, None),
        FilaPublicacion(
            10, "SKU-F1", "amazon_mx", "fbm", Decimal("116"), "MXN", 0, False, None, None
        ),
        FilaPublicacion(
            11, "SKU-F2", "amazon_mx", "fbm", Decimal("116"), "MXN", 0, True, "subir", None
        ),
        FilaPublicacion(
            12, "SKU-F3", "amazon_mx", "fbm", Decimal("116"), "MXN", 0, False, None, None
        ),
    ]
    return filas


def test_recuadro_cuadra_exacto_con_doce():
    from app.precio.cobertura import armar_recuadro, cuadra_exact

    rec = armar_recuadro(_doce(), platform="amazon_mx", max_dias=3)
    assert rec.activas == 12
    assert rec.evaluadas == 3
    assert dict(rec.no_evaluadas) == {
        "fee_ausente": 1,
        "oferta_desactualizada": 1,
        "sin_decision": 1,
    }
    assert [s.sku for s in rec.sin_goal] == ["SKU-S1", "SKU-S2", "SKU-S3"]
    assert dict(rec.fuera_de_alcance) == {"fase_E_envio_fbm": 3}
    assert cuadra_exact(rec)


def test_sin_goal_aparece_listada_con_precio_y_canal():
    from app.precio.cobertura import armar_recuadro

    rec = armar_recuadro(_doce(), platform="amazon_mx", max_dias=3)
    primera = rec.sin_goal[0]
    assert (primera.sku, primera.precio, primera.moneda, primera.canal) == (
        "SKU-S1",
        Decimal("200"),
        "MXN",
        "fba",
    )


def test_fuera_de_alcance_nombra_la_fase():
    from app.precio.cobertura import FilaPublicacion, armar_recuadro

    filas = [
        FilaPublicacion(1, "SKU-M1", "meli", "fbm", Decimal("100"), "MXN", 0, False, None, None),
        FilaPublicacion(2, "SKU-M2", "meli", None, None, None, 0, False, None, None),
    ]
    rec = armar_recuadro(filas, platform="meli", max_dias=3)
    assert dict(rec.fuera_de_alcance) == {"fase_M_meli": 2}
    assert rec.evaluadas == 0


def test_canal_desconocido_es_canal_sin_dato_sin_default():
    from app.precio.cobertura import FilaPublicacion, armar_recuadro

    filas = [
        FilaPublicacion(
            1, "SKU-X", "amazon_mx", None, Decimal("116"), "MXN", 0, True, "subir", None
        ),
    ]
    rec = armar_recuadro(filas, platform="amazon_mx", max_dias=3)
    assert rec.evaluadas == 0
    assert dict(rec.no_evaluadas) == {"canal_sin_dato": 1}


def test_stale_mas_de_max_dias_es_catalogo_desactualizado():
    from app.precio.cobertura import FilaPublicacion, armar_recuadro

    filas = [
        FilaPublicacion(
            1, "SKU-V", "amazon_mx", "fba", Decimal("116"), "MXN", 5, True, "subir", None
        ),
        FilaPublicacion(
            2, "SKU-B", "amazon_mx", "fba", Decimal("116"), "MXN", 3, True, "subir", None
        ),
    ]
    rec = armar_recuadro(filas, platform="amazon_mx", max_dias=3)
    assert dict(rec.no_evaluadas) == {"catalogo_desactualizado": 1}
    assert rec.evaluadas == 1
    assert any("catalogo_desactualizado" in a for a in rec.avisos)


def test_fuera_estructural_antes_que_stale():
    from app.precio.cobertura import FilaPublicacion, armar_recuadro

    filas = [
        FilaPublicacion(
            1, "SKU-F", "amazon_mx", "fbm", Decimal("116"), "MXN", 9, False, None, None
        ),
    ]
    rec = armar_recuadro(filas, platform="amazon_mx", max_dias=3)
    assert dict(rec.fuera_de_alcance) == {"fase_E_envio_fbm": 1}
    assert dict(rec.no_evaluadas) == {}


def test_aviso_puente_mas_de_5_por_ciento():
    from app.precio.cobertura import aviso_puente

    assert aviso_puente(activas=12, puente=12) is None
    assert aviso_puente(activas=100, puente=104) is None
    aviso = aviso_puente(activas=12, puente=14)
    assert aviso is not None and "16.7%" in aviso
    assert aviso_puente(activas=0, puente=5) is not None


# ---------------------------------------------------------------------------
# Fuentes contra base real
# ---------------------------------------------------------------------------


def test_max_dias_de_config_con_cota():
    from app.precio.fuentes import max_dias_desde_settings

    assert max_dias_desde_settings({"precio_catalogo_max_dias_sin_reportar": 3}) == 3
    with pytest.raises(ValueError, match="config sin precio_catalogo_max_dias"):
        max_dias_desde_settings({})
    for malo in ("0", "15", "3.5", "x"):
        with pytest.raises(ValueError, match="precio_catalogo_max_dias"):
            max_dias_desde_settings({"precio_catalogo_max_dias_sin_reportar": malo})


def _normalizar_sql(texto: str) -> str:
    sin_comentarios = "\n".join(
        linea
        for linea in texto.splitlines()
        if not linea.strip().startswith("--") and not linea.strip().startswith("\\set")
    )
    colapsado = " ".join(sin_comentarios.split())
    return (
        colapsado.replace(":'platform'", "%s")
        .replace(":'hoy'", "%s")
        .replace("%%", "%")
        .replace("( ", "(")
        .replace(" )", ")")
    )


def test_consultas_iguales_a_las_que_ejecuta_fuentes():
    """DoD (g): `consultas/*.sql` son las mismas que el modulo ejecuta."""
    import app.precio.fuentes as fuentes

    pares = (
        ("01_canonicas.sql", fuentes._SQL_CANO),
        ("02_canales.sql", fuentes._SQL_CANAL),
        ("03_goals.sql", fuentes._SQL_GOALS),
        ("04_decisiones.sql", fuentes._SQL_DECISIONES),
        ("05_puente.sql", fuentes._SQL_PUENTE),
        ("06_hoy.sql", fuentes._SQL_HOY),
    )
    for nombre, constante in pares:
        ruta = RAIZ / "docs" / "evidencia" / "repricing-01" / "A.7" / "consultas" / nombre
        assert ruta.is_file(), f"falta {nombre} (DoD (g))"
        assert _normalizar_sql(ruta.read_text(encoding="utf-8")).rstrip(";") == _normalizar_sql(
            constante
        ).rstrip(";"), nombre


@_skip_sin_pg
def test_fuentes_doce_publicaciones_cuadran():
    from app.precio.cobertura import armar_recuadro, cuadra_exact
    from app.precio.fuentes import (
        config_vigente_settings,
        hoy_base,
        leer_publicaciones,
        max_dias_desde_settings,
    )

    with _db() as (conn, _dsn):
        _config(conn)
        _pub(conn, sku="SKU-E1", goal=True, resultado="subir")
        _pub(conn, sku="SKU-E2", goal=True, resultado="mantener", motivo="en_tolerancia")
        _pub(conn, sku="SKU-E3", goal=True, resultado="bajar")
        _pub(conn, sku="SKU-N1", goal=True, resultado="no_evaluado", motivo="fee_ausente")
        _pub(conn, sku="SKU-N2", goal=True, resultado="no_evaluado", motivo="oferta_desactualizada")
        _pub(conn, sku="SKU-N3", goal=True)
        _pub(conn, sku="SKU-S1")
        _pub(conn, sku="SKU-S2")
        _pub(conn, sku="SKU-S3", canal=None)
        _pub(conn, sku="SKU-F1", canal="fbm")
        _pub(conn, sku="SKU-F2", canal="fbm", goal=True, resultado="subir")
        _pub(conn, sku="SKU-F3", canal="fbm")
        hoy = hoy_base(conn)
        max_dias = max_dias_desde_settings(config_vigente_settings(conn))
        filas = leer_publicaciones(conn, platform="amazon_mx", hoy=hoy)
        rec = armar_recuadro(filas, platform="amazon_mx", max_dias=max_dias)
        assert rec.activas == 12
        assert rec.evaluadas == 3
        assert dict(rec.no_evaluadas) == {
            "fee_ausente": 1,
            "oferta_desactualizada": 1,
            "sin_decision": 1,
        }
        assert sorted(s.sku for s in rec.sin_goal) == ["SKU-S1", "SKU-S2", "SKU-S3"]
        assert dict(rec.fuera_de_alcance) == {"fase_E_envio_fbm": 3}
        assert cuadra_exact(rec)


@_skip_sin_pg
def test_fuentes_dos_listings_mismo_sku_cuentan_uno():
    """El join con `listing` se abanica si dos ASINs comparten SKU: cuenta uno."""
    from app.precio.cobertura import armar_recuadro, cuadra_exact
    from app.precio.fuentes import hoy_base, leer_publicaciones

    with _db() as (conn, _dsn):
        _config(conn)
        prod_a = _producto(conn, sku="ODOO-DUP-A")
        _listing(conn, prod_a, ext="ASIN-DUP-A", sku="SKU-DUP")
        prod_b = _producto(conn, sku="ODOO-DUP-B")
        _listing(conn, prod_b, ext="ASIN-DUP-B", sku="SKU-DUP")
        _estado(conn, sku="SKU-DUP")
        hoy = hoy_base(conn)
        filas = leer_publicaciones(conn, platform="amazon_mx", hoy=hoy)
        assert len(filas) == 1
        rec = armar_recuadro(filas, platform="amazon_mx", max_dias=3)
        assert rec.activas == 1
        assert cuadra_exact(rec)


@_skip_sin_pg
def test_fuentes_status_contiene_buyable_y_manda_la_ultima():
    from app.precio.fuentes import hoy_base, leer_publicaciones

    with _db() as (conn, _dsn):
        _config(conn)
        _pub(conn, sku="SKU-JOIN")
        ahora = datetime.now(UTC)
        _estado(conn, sku="SKU-JOIN", status="CLOSED", observed=ahora - timedelta(hours=2))
        _estado(conn, sku="SKU-JOIN", status="BUYABLE,DISCOVERABLE", observed=ahora)
        prod = _producto(conn, sku="ODOO-NULA")
        _listing(conn, prod, ext="ASIN-NULA", sku="SKU-NULA")
        _estado(conn, sku="SKU-NULA", status=None)
        hoy = hoy_base(conn)
        filas = leer_publicaciones(conn, platform="amazon_mx", hoy=hoy)
        skus = sorted(f.seller_sku for f in filas)
        assert "SKU-JOIN" in skus  # la BUYABLE vieja sigue contando si es la ultima
        assert "SKU-NULA" not in skus


@_skip_sin_pg
def test_fuentes_cerrada_reciente_apaga():
    from app.precio.fuentes import hoy_base, leer_publicaciones

    with _db() as (conn, _dsn):
        _config(conn)
        _pub(conn, sku="SKU-OFF")
        _estado(conn, sku="SKU-OFF", status="CLOSED")
        hoy = hoy_base(conn)
        filas = leer_publicaciones(conn, platform="amazon_mx", hoy=hoy)
        assert [f.seller_sku for f in filas] == []


@_skip_sin_pg
def test_fuentes_stale_y_dias():
    from app.precio.fuentes import hoy_base, leer_publicaciones

    with _db() as (conn, _dsn):
        _config(conn)
        _pub(
            conn,
            sku="SKU-STALE",
            goal=True,
            resultado="subir",
            observed=datetime.now(UTC) - timedelta(days=5),
        )
        hoy = hoy_base(conn)
        filas = leer_publicaciones(conn, platform="amazon_mx", hoy=hoy)
        assert len(filas) == 1
        assert filas[0].dias_sin_reportar == 5


@_skip_sin_pg
def test_tool_imprime_recuadro_con_puente_al_lado(tmp_path):
    with _db() as (conn, dsn):
        _config(conn)
        _pub(conn, sku="SKU-T1", goal=True, resultado="subir")
        _pub(conn, sku="SKU-T2")
        prod = _producto(conn, sku="ODOO-FANTASMA")
        _listing(conn, prod, ext="ASIN-FANTASMA", sku="SKU-FANTASMA")
        res = _tool("--platform", "amazon_mx", dsn=dsn)
        assert res.returncode == 0, res.stderr
        assert "activas=2" in res.stdout
        assert "sin_goal" in res.stdout and "SKU-T2" in res.stdout
        assert "puente" in res.stdout
        assert "aviso" in res.stdout  # 3 del puente vs 2 canonicas = 50 %


@_skip_sin_pg
def test_recuadro_desde_salidas_roundtrip(tmp_path):
    """Las consultas volcadas en `psql -tA` alimentan el mismo recuadro."""
    import importlib.util

    from app.precio.cobertura import armar_recuadro
    from app.precio.fuentes import (
        config_vigente_settings,
        hoy_base,
        leer_publicaciones,
        max_dias_desde_settings,
    )

    with _db() as (conn, _dsn):
        _config(conn)
        _pub(conn, sku="SKU-R1", goal=True, resultado="subir")
        _pub(conn, sku="SKU-R2")
        hoy = hoy_base(conn)
        max_dias = max_dias_desde_settings(config_vigente_settings(conn))
        filas = leer_publicaciones(conn, platform="amazon_mx", hoy=hoy)
        esperado = armar_recuadro(filas, platform="amazon_mx", max_dias=max_dias)

        def _volcar(nombres, valores):
            lineas = []
            for fila in valores:
                lineas.append("|".join("" if v is None else str(v) for v in fila))
            ruta = tmp_path / nombres
            ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
            return str(ruta)

        canonicas = _volcar(
            "canonicas.txt",
            [
                (f.listing_id, f.seller_sku, f.platform, "ASIN-X", "BUYABLE", hoy.isoformat())
                for f in filas
            ],
        )
        canales = _volcar(
            "canales.txt",
            [(f.listing_id, f.canal, f.precio, f.moneda) for f in filas],
        )
        goals = _volcar("goals.txt", [(f.listing_id,) for f in filas if f.tiene_goal])
        decisiones = _volcar(
            "decisiones.txt",
            [
                (f.listing_id, f.resultado_hoy, f.motivo_hoy)
                for f in filas
                if f.resultado_hoy is not None
            ],
        )
        puente = _volcar("puente.txt", [(2,)])
        hoy_f = _volcar("hoy.txt", [(hoy.isoformat(),)])
        guion = RAIZ / "docs" / "evidencia" / "repricing-01" / "A.7" / "recuadro_desde_salidas.py"
        assert guion.is_file(), "falta recuadro_desde_salidas.py (DoD (g))"
        spec = importlib.util.spec_from_file_location("recuadro_desde_salidas", guion)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        rec = modulo.recuadro_desde_archivos(
            canonicas,
            canales,
            goals,
            decisiones,
            puente,
            hoy_f,
            platform="amazon_mx",
            max_dias=3,
        )
        assert (rec.activas, rec.evaluadas) == (esperado.activas, esperado.evaluadas)
        assert dict(rec.no_evaluadas) == dict(esperado.no_evaluadas)
        assert [s.sku for s in rec.sin_goal] == [s.sku for s in esperado.sin_goal]
        assert dict(rec.fuera_de_alcance) == dict(esperado.fuera_de_alcance)
        assert rec == esperado
