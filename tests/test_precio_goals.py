"""Herramienta de goals de precio (REPRICING 01, A.1).

`app/precio/goals_write.py` (unico escritor de `precio_goal`, solo
`app_admin`, cero Amazon) + `tools/precio_goal.py` (siembra unitaria y lote
CSV, `--mode live` con ceremonia completa, `--mode shadow` sin go literal,
`--cerrar`; dry-run con `m_actual` y `P*` por fila y aborto si el salto
`abs(P* - P_actual) > 25 %` salvo `--confirmar-salto`).

Base real (`ORBIT_TEST_DSN`, cero `skipped` con el DSN de VERIFY): cada test
levanta una DB temporal con ORDEN54 (0001 + 0002 + 0028 + 0039 + 0047 + 0053
+ 0054) y la tira al salir (patron `db_39` de `test_precio_migracion`,
autocontenido aqui para exponer el DSN al subprocess del tool). La
`config_version` se siembra con el patron `_config_version(conn, settings)`
de `test_api` l.149.
"""

from __future__ import annotations

import ast
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
import psycopg.errors
import pytest
from psycopg.types.json import Json
from test_architecture import (
    PROHIBIDOS_PRECIO,
    _usos_import_dinamico,
    _usos_reloj,
)
from test_schema import _postgres_obligatorio_ausente

RAIZ = Path(__file__).resolve().parents[1]
# G.1: la lista termina en la 0054 (reedicion el mismo dia por indice
# parcial + columnas origen/lote/m_referencia). La 0054 necesita la 0047
# por la FK a `familia` (patron ORDEN02 de `test_precio02_base.py`).
ORDEN54 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
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
    """DB temporal con ORDEN54; entrega `(conn, dsn)` con conn en autocommit."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"goals_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN54:
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

GOAL_SETTINGS = {"precio_goal_min_pct": "0.10", "precio_goal_max_pct": "0.60"}


def _config(conn, settings=None) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-goals", Json(dict(settings if settings is not None else GOAL_SETTINGS))),
    ).fetchone()[0]


def _producto(conn, sku="SKU-GOAL-1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto goal') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing(conn, producto: int, *, platform="amazon_mx", ext="ASIN-G1", sku="SKU-G1") -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (producto, platform, ext, sku),
    ).fetchone()[0]


def _politica(conn) -> int:
    return conn.execute(
        "INSERT INTO estimacion_politica_version (label, universo, formula_version,"
        " settings, valid_from)"
        " VALUES ('test-goals', 'amazon_mx/fba', 'vtest', %s, '2026-09-01') RETURNING id",
        (Json({}),),
    ).fetchone()[0]


def _costo(conn, producto: int) -> int:
    return conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from)"
        " VALUES (%s, 40, 'MXN', false, '2026-01-01') RETURNING id",
        (producto,),
    ).fetchone()[0]


def _oferta(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    ext="ASIN-G1",
    sku="SKU-G1",
    precio="116",
    huella="ctx-g1",
    evento="oferta-g1",
    fetched=None,
    observed=None,
) -> int:
    base = datetime.now(UTC)
    fetched = fetched or base
    observed = observed or base
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, 'fba', %s, 'MXN', %s, %s, %s, %s, %s) RETURNING id",
        (
            listing,
            platform,
            sku,
            ext,
            Decimal(precio),
            fetched,
            observed,
            evento,
            Json({}),
            huella,
        ),
    ).fetchone()[0]


_FEE_DETALLES = [
    {
        "fee_type": "ReferralFee",
        "final_fee": "12",
        "tax_amount": None,
        "included_fee_details": [],
    },
    {
        "fee_type": "FbaFee",
        "final_fee": "3",
        "tax_amount": None,
        "included_fee_details": [],
    },
]


def _fee(
    conn,
    oferta_id: int,
    listing: int,
    *,
    platform="amazon_mx",
    ext="ASIN-G1",
    sku="SKU-G1",
    precio="116",
    total="15",
    huella="ctx-g1",
    evento="fee-g1",
    fetched=None,
    observed=None,
) -> int:
    base = datetime.now(UTC)
    fetched = fetched or base
    observed = observed or base
    return conn.execute(
        "INSERT INTO estimacion_fee_observation (oferta_observation_id, listing_id, platform,"
        " seller_sku, asin, canal, quoted_price_amount, quoted_price_currency, total_fees,"
        " fee_details, fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, 'fba', %s, 'MXN', %s, %s, %s, %s, %s, 'success',"
        " %s, %s, %s) RETURNING id",
        (
            oferta_id,
            listing,
            platform,
            sku,
            ext,
            Decimal(precio),
            Decimal(total),
            Json(_FEE_DETALLES),
            fetched,
            fetched,
            observed,
            evento,
            Json({}),
            huella,
        ),
    ).fetchone()[0]


def _componentes() -> list:
    return [
        {
            "nombre": "precio_bruto",
            "importe_normalizado": "116",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "ingreso_normalizado",
            "importe_normalizado": "100",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "costo_normalizado",
            "importe_normalizado": "40",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "logistica",
            "importe_normalizado": "0",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "isr",
            "importe_normalizado": "2.50",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
    ]


def _escenario(
    conn,
    listing: int,
    oferta_id: int,
    fee_id: int,
    politica_id: int,
    costo_id: int,
    run_id: int,
    validada_en,
    *,
    platform="amazon_mx",
    ext="ASIN-G1",
    sku="SKU-G1",
    m="24.00",
    evento="esc-g1",
    observed=None,
    valoracion=None,
    sin_componente=None,
) -> int:
    observed = observed or (datetime.now(UTC) + timedelta(seconds=120))
    valoracion = valoracion or conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    componentes = [c for c in _componentes() if c["nombre"] != sin_componente]
    return conn.execute(
        "INSERT INTO estimacion_escenario (listing_id, platform, seller_sku, asin, canal,"
        " valoracion_date, observed_at, politica_version_id, formula_version,"
        " oferta_observation_id, fee_observation_id, sku_cost_id, costo_validation_run_id,"
        " costo_validated_at, moneda, contribucion, contribucion_pct, estado, motivos,"
        " componentes, exclusiones, canonical_input, context_fingerprint, source_event_id)"
        " VALUES (%s, %s, %s, %s, 'fba', %s, %s, %s, 'vtest', %s, %s, %s, %s,"
        " %s, 'MXN', %s, %s, 'disponible', %s, %s, %s, %s,"
        " 'ctx-g1', %s) RETURNING id",
        (
            listing,
            platform,
            sku,
            ext,
            valoracion,
            observed,
            politica_id,
            oferta_id,
            fee_id,
            costo_id,
            run_id,
            validada_en,
            Decimal(m).quantize(Decimal("0.01")),
            Decimal(m),
            Json([]),
            Json(componentes),
            Json([]),
            Json({}),
            evento,
        ),
    ).fetchone()[0]


def _run(conn):
    """Run `accounting_sku_costs` ok (el que el trigger del escenario exige)."""
    return conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok, rows_skipped)"
        " VALUES ('accounting_sku_costs', now(), true, 0)"
        " RETURNING id, finished_at"
    ).fetchone()


def _cadena(conn, *, sku="SKU-GOAL-1", ext="ASIN-G1", m="24.00"):
    """Listing + config + escenario disponible completo (C=40, P=116, F=15)."""
    prod = _producto(conn, sku=sku)
    listing = _listing(conn, prod, ext=ext, sku=sku)
    _config(conn)
    marca = datetime.now(UTC)
    oferta = _oferta(
        conn,
        listing,
        ext=ext,
        sku=sku,
        evento=f"oferta-{sku}",
        fetched=marca,
        observed=marca,
    )
    fee = _fee(
        conn,
        oferta,
        listing,
        ext=ext,
        sku=sku,
        evento=f"fee-{sku}",
        fetched=marca,
        observed=marca,
    )
    pol = _politica(conn)
    costo = _costo(conn, prod)
    run_id, validada_en = _run(conn)
    _escenario(
        conn,
        listing,
        oferta,
        fee,
        pol,
        costo,
        run_id,
        validada_en,
        ext=ext,
        sku=sku,
        m=m,
        evento=f"esc-{sku}",
    )
    return {"listing": listing, "sku": sku, "ext": ext}


# P* a mano (C=40, fijo=3, L=0, r=0.025, goal=0.30, d=1.16, ref=12/116):
# denominador = 0.675/1.16 - 12/116 = 0.4784482758...; P* = 43/denominador
# = 89.8738... -> techo al centavo 89.88. |89.88-116|/116 = 22.5 % (< 25 %).
P_ESTRELLA_30 = "89.88"
# Goal 0.55: denominador = 0.425/1.16 - 12/116 = 0.2629310344...;
# P* = 43/denominador = 163.5408... -> techo 163.55. Salto 41 % (> 25 %).
P_ESTRELLA_55 = "163.55"


def _tool(*args, dsn, **kw):
    """Corre `tools/precio_goal.py` solo contra el DSN dado.

    `ORBIT_DSN_DECIDE`/`ORBIT_DSN_READ` apuntan a un puerto muerto: si el
    tool tocara otro DSN que el admin, revienta (solo `app_admin` escribe
    goals; cero Amazon).
    """
    env = dict(os.environ)
    env["ORBIT_DSN_ADMIN"] = dsn
    env["ORBIT_DSN_DECIDE"] = "postgresql://orbit:orbit@127.0.0.1:1/nula"
    env["ORBIT_DSN_READ"] = "postgresql://orbit:orbit@127.0.0.1:1/nula"
    env.pop("ORBIT_PG_HOST", None)
    env["PYTHONPATH"] = RAIZ.as_posix()
    return subprocess.run(
        [sys.executable, "tools/precio_goal.py", *args],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        env=env,
        **kw,
    )


def _cuenta_goals(conn) -> int:
    return conn.execute("SELECT count(*) FROM precio_goal").fetchone()[0]


# ---------------------------------------------------------------------------
# Conversion y banda (puro, sin base)
# ---------------------------------------------------------------------------


def test_porcentaje_a_fraccion_dos_decimales():
    from app.precio.goals_write import fraccion_desde_porcentaje

    assert fraccion_desde_porcentaje("30.00") == Decimal("0.3000")
    assert isinstance(fraccion_desde_porcentaje("30.00"), Decimal)


def test_fraccion_rechaza_valor_en_fraccion():
    from app.precio.goals_write import PrecioGoalInvalido, fraccion_desde_porcentaje

    with pytest.raises(PrecioGoalInvalido, match="por ciento"):
        fraccion_desde_porcentaje("0.30")


def test_fraccion_rechaza_mas_de_dos_decimales_y_basura():
    from app.precio.goals_write import PrecioGoalInvalido, fraccion_desde_porcentaje

    with pytest.raises(PrecioGoalInvalido, match="dos decimales"):
        fraccion_desde_porcentaje("30.555")
    with pytest.raises(PrecioGoalInvalido, match="por ciento|numerico"):
        fraccion_desde_porcentaje("treinta")


def test_banda_sin_claves_aborta_sin_defaults():
    from app.precio.goals_write import banda_desde_settings

    with pytest.raises(ValueError, match="config sin precio_goal_min_pct"):
        banda_desde_settings({})


def test_banda_incoherente_aborta():
    from app.precio.goals_write import banda_desde_settings

    with pytest.raises(ValueError, match="0 < min < max < 1"):
        banda_desde_settings({"precio_goal_min_pct": "0.60", "precio_goal_max_pct": "0.10"})


# ---------------------------------------------------------------------------
# Escritor contra base real
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_sembrar_shadow_guarda_fraccion_exacta():
    from app.precio.goals_write import sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        gid = sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        fila = conn.execute(
            "SELECT margen_goal_pct, mode, go_literal, valid_to FROM precio_goal WHERE id = %s",
            (gid,),
        ).fetchone()
        assert fila[0] == Decimal("0.3000")
        assert fila[1] == "shadow"
        assert fila[2] is None
        assert fila[3] is None


@_skip_sin_pg
def test_sembrar_live_exige_go_en_python():
    from app.precio.goals_write import PrecioGoalInvalido, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        with pytest.raises(PrecioGoalInvalido, match="live sin go"):
            sembrar_goal(
                conn,
                listing_id=datos["listing"],
                platform="amazon_mx",
                goal_pct="30.00",
                mode="live",
                go_literal=None,
            )
        with pytest.raises(PrecioGoalInvalido, match="live sin go"):
            sembrar_goal(
                conn,
                listing_id=datos["listing"],
                platform="amazon_mx",
                goal_pct="30.00",
                mode="live",
                go_literal="   ",
            )
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_live_sin_go_rechazado_por_la_base():
    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_goal_live_exige_go"):
            conn.execute(
                "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
                " valid_from, creado_por, go_literal)"
                " VALUES (%s, 'amazon_mx', 0.30, 'live', '2026-09-10', 't', NULL)",
                (datos["listing"],),
            )


@_skip_sin_pg
def test_shadow_con_go_rechazado_en_python_y_en_base():
    from app.precio.goals_write import PrecioGoalInvalido, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        with pytest.raises(PrecioGoalInvalido, match="shadow.*go|go.*shadow"):
            sembrar_goal(
                conn,
                listing_id=datos["listing"],
                platform="amazon_mx",
                goal_pct="30.00",
                mode="shadow",
                go_literal="voy",
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_goal_live_exige_go"):
            conn.execute(
                "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
                " valid_from, creado_por, go_literal)"
                " VALUES (%s, 'amazon_mx', 0.30, 'shadow', '2026-09-10', 't', 'voy')",
                (datos["listing"],),
            )
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_goal_fuera_de_banda_rechazado_en_python_y_en_base():
    from app.precio.goals_write import PrecioGoalInvalido, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        with pytest.raises(PrecioGoalInvalido, match="fuera de banda"):
            sembrar_goal(
                conn,
                listing_id=datos["listing"],
                platform="amazon_mx",
                goal_pct="5.00",
                mode="shadow",
                go_literal=None,
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_goal_banda"):
            conn.execute(
                "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
                " valid_from, creado_por, go_literal)"
                " VALUES (%s, 'amazon_mx', 0.05, 'shadow', '2026-09-10', 't', NULL)",
                (datos["listing"],),
            )
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_segundo_vigente_rechazado():
    from app.precio.goals_write import PrecioGoalInvalido, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        with pytest.raises(PrecioGoalInvalido, match="vigente|--cerrar"):
            sembrar_goal(
                conn,
                listing_id=datos["listing"],
                platform="amazon_mx",
                goal_pct="40.00",
                mode="shadow",
                go_literal=None,
            )
        assert _cuenta_goals(conn) == 1


@_skip_sin_pg
def test_cerrar_solo_fija_valid_to_y_el_trigger_rechaza_lo_demas():
    from app.precio.goals_write import cerrar_goal, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        gid = sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        assert cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx") == gid
        fila = conn.execute(
            "SELECT valid_to, margen_goal_pct FROM precio_goal WHERE id = %s", (gid,)
        ).fetchone()
        assert fila[0] is not None
        assert fila[1] == Decimal("0.3000")
        with pytest.raises(psycopg.errors.RestrictViolation, match="s.lo se puede cerrar"):
            conn.execute("UPDATE precio_goal SET margen_goal_pct = 0.40 WHERE id = %s", (gid,))


@_skip_sin_pg
def test_cerrar_sin_vigente_aborta():
    from app.precio.goals_write import PrecioGoalAusente, cerrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        _config(conn)
        with pytest.raises(PrecioGoalAusente, match="sin goal vigente"):
            cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx")


# ---------------------------------------------------------------------------
# Tool: dry-run, ceremonia y lote
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_dry_run_imprime_referencia_y_no_escribe():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 0, res.stderr
        assert "m_actual=24.00%" in res.stdout
        assert f"P*={P_ESTRELLA_30} MXN" in res.stdout
        assert "P_actual=116.00 MXN" in res.stdout
        assert "huella: " in res.stdout
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_huella_estable_entre_dry_runs():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        args = [
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
        ]
        h1 = _tool(*args, dsn=dsn).stdout
        h2 = _tool(*args, dsn=dsn).stdout
        assert h1 == h2


@_skip_sin_pg
def test_escenario_mas_reciente_manda():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        run_id, validada_en = _run(conn)
        pol = conn.execute("SELECT max(id) FROM estimacion_politica_version").fetchone()[0]
        costo = conn.execute("SELECT max(id) FROM sku_cost").fetchone()[0]
        oferta = conn.execute("SELECT max(id) FROM estimacion_oferta_observation").fetchone()[0]
        fee = conn.execute("SELECT max(id) FROM estimacion_fee_observation").fetchone()[0]
        _escenario(
            conn,
            datos["listing"],
            oferta,
            fee,
            pol,
            costo,
            run_id,
            validada_en,
            ext=datos["ext"],
            sku=datos["sku"],
            m="41.00",
            evento="esc-g2",
            observed=datetime.now(UTC) + timedelta(seconds=600),
        )
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 0, res.stderr
        assert "m_actual=41.00%" in res.stdout
        assert "m_actual=24.00%" not in res.stdout


@_skip_sin_pg
def test_salto_mayor_25_aborta_sin_confirmacion_y_pasa_con_ella():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        args = [
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "55.00",
            "--mode",
            "shadow",
        ]
        res = _tool(*args, dsn=dsn)
        assert res.returncode == 2
        assert "25" in res.stderr
        assert f"P*={P_ESTRELLA_55} MXN" in res.stdout
        assert "huella: " not in res.stdout
        assert _cuenta_goals(conn) == 0
        res2 = _tool(*args, "--confirmar-salto", str(datos["listing"]), dsn=dsn)
        assert res2.returncode == 0, res2.stderr
        assert "huella: " in res2.stdout


@_skip_sin_pg
def test_shadow_go_sin_go_literal_y_rechaza_go():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        args = [
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
        ]
        seco = _tool(*args, dsn=dsn)
        huella = seco.stdout.split("huella: ")[1].split()[0]
        con_go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, "--go", "voy", dsn=dsn)
        assert con_go.returncode == 2
        assert "sin go" in con_go.stderr
        assert _cuenta_goals(conn) == 0
        go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, dsn=dsn)
        assert go.returncode == 0, go.stderr
        fila = conn.execute(
            "SELECT mode, go_literal FROM precio_goal WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()
        assert fila[0] == "shadow"
        assert fila[1] is None


@_skip_sin_pg
def test_huella_distinta_aborta_y_no_escribe():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            "--acepto-mutacion-real",
            "--huella",
            "huella-muerta",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "huella" in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_go_sin_acepto_no_mut_aunque_traiga_go():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "live",
            "--huella",
            "x",
            "--go",
            "voy",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "acepto-mutacion-real" in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_live_go_completo_escribe_go_literal():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        args = [
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "live",
        ]
        seco = _tool(*args, dsn=dsn)
        assert seco.returncode == 0, seco.stderr
        huella = seco.stdout.split("huella: ")[1].split()[0]
        sin_go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, dsn=dsn)
        assert sin_go.returncode == 2
        assert "--go" in sin_go.stderr
        assert _cuenta_goals(conn) == 0
        go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, "--go", "enciende", dsn=dsn)
        assert go.returncode == 0, go.stderr
        fila = conn.execute(
            "SELECT mode, go_literal, margen_goal_pct FROM precio_goal WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert (fila[0], fila[1], fila[2]) == ("live", "enciende", Decimal("0.3000"))


@_skip_sin_pg
def test_goal_pct_en_fraccion_rechazado():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "0.30",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "por ciento" in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_csv_repetido_aborta_antes_de_la_huella_con_linea(tmp_path):
    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-CSV-A", ext="ASIN-CSV-A")
        b = _cadena(conn, sku="SKU-CSV-B", ext="ASIN-CSV-B")
        csv = tmp_path / "lote.csv"
        csv.write_text(
            "listing_id,platform,goal_pct\n"
            f"{a['listing']},amazon_mx,30.00\n"
            f"{b['listing']},amazon_mx,30.00\n"
            f"{a['listing']},amazon_mx,40.00\n",
            encoding="utf-8",
        )
        res = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
        assert res.returncode == 2
        assert "repetida" in res.stderr
        assert "nea 4" in res.stderr
        assert "huella: " not in res.stdout
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_csv_linea_en_blanco_no_corre_el_numero_de_linea(tmp_path):
    """AC3: con una linea en blanco, la repetida se reporta con su linea real."""
    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-AC3-A", ext="ASIN-AC3-A")
        csv = tmp_path / "lote.csv"
        csv.write_text(
            "listing_id,platform,goal_pct\n"
            "\n"
            f"{a['listing']},amazon_mx,30.00\n"
            f"{a['listing']},amazon_mx,40.00\n",
            encoding="utf-8",
        )
        res = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
        assert res.returncode == 2
        assert "repetida" in res.stderr
        assert "nea 4" in res.stderr
        assert "primera en linea 3" in res.stderr
        assert "huella: " not in res.stdout
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_csv_con_bom_no_aborta_por_cabecera(tmp_path):
    """AC3: el CSV UTF-8 con BOM (Excel) se lee sin abortar por cabecera."""
    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-AC3-B", ext="ASIN-AC3-B")
        csv = tmp_path / "lote.csv"
        csv.write_text(
            f"\ufefflisting_id,platform,goal_pct\n{a['listing']},amazon_mx,30.00\n",
            encoding="utf-8",
        )
        res = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
        assert res.returncode == 0, res.stderr
        assert "[plan] siembra" in res.stdout


@_skip_sin_pg
def test_csv_go_escribe_exactamente_n_filas(tmp_path):
    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-CSV-A", ext="ASIN-CSV-A")
        b = _cadena(conn, sku="SKU-CSV-B", ext="ASIN-CSV-B")
        csv = tmp_path / "lote.csv"
        csv.write_text(
            "listing_id,platform,goal_pct\n"
            f"{a['listing']},amazon_mx,30.00\n"
            f"{b['listing']},amazon_mx,40.00\n",
            encoding="utf-8",
        )
        seco = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
        assert seco.returncode == 0, seco.stderr
        huella = seco.stdout.split("huella: ")[1].split()[0]
        go = _tool(
            "--csv",
            str(csv),
            "--mode",
            "shadow",
            "--acepto-mutacion-real",
            "--huella",
            huella,
            dsn=dsn,
        )
        assert go.returncode == 0, go.stderr
        assert _cuenta_goals(conn) == 2


@_skip_sin_pg
def test_sin_escenario_shadow_avisa_y_live_aborta():
    with _db() as (conn, dsn):
        prod = _producto(conn, sku="SKU-SIN-ESC")
        listing = _listing(conn, prod, ext="ASIN-SIN-ESC", sku="SKU-SIN-ESC")
        _config(conn)
        args = [
            "--listing-id",
            str(listing),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
        ]
        seco_shadow = _tool(*args, "--mode", "shadow", dsn=dsn)
        assert seco_shadow.returncode == 0, seco_shadow.stderr
        assert "m_actual=sin_escenario" in seco_shadow.stdout
        assert "P*=sin_escenario" in seco_shadow.stdout
        assert "aviso" in seco_shadow.stdout
        huella = seco_shadow.stdout.split("huella: ")[1].split()[0]
        go = _tool(*args, "--mode", "shadow", "--acepto-mutacion-real", "--huella", huella, dsn=dsn)
        assert go.returncode == 0, go.stderr
        assert _cuenta_goals(conn) == 1
        prod2 = _producto(conn, sku="SKU-SIN-ESC-2")
        listing2 = _listing(conn, prod2, ext="ASIN-SIN-ESC-2", sku="SKU-SIN-ESC-2")
        seco_live = _tool(
            "--listing-id",
            str(listing2),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "live",
            dsn=dsn,
        )
        assert seco_live.returncode == 2
        assert "sin_escenario" in seco_live.stderr
        assert _cuenta_goals(conn) == 1


@_skip_sin_pg
def test_cerrar_flujo_completo(tmp_path):
    from app.precio.goals_write import sembrar_goal

    with _db() as (conn, dsn):
        datos = _cadena(conn)
        sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        args = ["--listing-id", str(datos["listing"]), "--platform", "amazon_mx", "--cerrar"]
        seco = _tool(*args, dsn=dsn)
        assert seco.returncode == 0, seco.stderr
        assert "cerrar" in seco.stdout
        huella = seco.stdout.split("huella: ")[1].split()[0]
        con_go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, "--go", "x", dsn=dsn)
        assert con_go.returncode == 2
        go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, dsn=dsn)
        assert go.returncode == 0, go.stderr
        fila = conn.execute(
            "SELECT valid_to FROM precio_goal WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()
        assert fila[0] is not None
        otra = _tool(*args, dsn=dsn)
        assert otra.returncode == 2
        assert "sin goal vigente" in otra.stderr


@_skip_sin_pg
def test_cerrar_con_confirmar_salto_aborta():
    """AC5: `--cerrar` con `--confirmar-salto` aborta (sobra, no se ignora)."""
    from app.precio.goals_write import sembrar_goal

    with _db() as (conn, dsn):
        datos = _cadena(conn)
        sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--cerrar",
            "--confirmar-salto",
            str(datos["listing"]),
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "--cerrar no siembra: --confirmar-salto sobra" in res.stderr


@_skip_sin_pg
def test_goal_fuera_de_banda_rechazado_por_el_tool():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "5.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "fuera de banda" in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_sin_claves_de_config_aborta():
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        _config(conn, settings={"otra_clave": 1})
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "config sin precio_goal_min_pct" in res.stderr
        assert _cuenta_goals(conn) == 0


# ---------------------------------------------------------------------------
# Candado propio de goals_write: PROHIBIDOS_PRECIO salvo psycopg + reloj,
# entorno e import dinamico (cierre-r1 AC1, misma forma que el carril B)
# ---------------------------------------------------------------------------

_PERMITIDOS_GOALS_WRITE = ("psycopg", "app.precio.")

# AC1: el candado de imports aplica `PROHIBIDOS_PRECIO` completo salvo
# `psycopg` (lo unico que la excepcion levanta); el resto de stdlib sigue
# permitido para `Decimal`/`Mapping`/`Any`. El apareo es exacto o por
# prefijo punteado (`os.getenv` cae por `os`).
_PROHIBIDOS_IMPORTS_GOALS_WRITE = tuple(e for e in PROHIBIDOS_PRECIO if e != "psycopg")


def _import_prohibido_goals_write(nombre: str) -> bool:
    return any(
        nombre == entrada or nombre.startswith(f"{entrada}.")
        for entrada in _PROHIBIDOS_IMPORTS_GOALS_WRITE
        if not entrada.startswith("<")
    )


def _imports_goals_write(path: Path) -> set[str]:
    importados: set[str] = set()
    arbol = ast.parse(path.read_text(encoding="utf-8"))

    def _visitar(nodos: list) -> None:
        for nodo in nodos:
            if isinstance(nodo, ast.Import):
                importados.update(a.name for a in nodo.names)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.module and nodo.level == 0:
                    importados.add(nodo.module)
                    importados.update(f"{nodo.module}.{a.name}" for a in nodo.names)
                elif nodo.level and nodo.level >= 1:
                    importados.add(f"<relativo-nivel-{nodo.level}>")
            else:
                _visitar(list(ast.iter_child_nodes(nodo)))

    _visitar(arbol.body)
    return importados


def _fugas_imports_goals_write(path: Path) -> list[str]:
    import sys as _sys

    return sorted(
        i
        for i in _imports_goals_write(path)
        if _import_prohibido_goals_write(i)
        or (
            i.split(".")[0] not in _sys.stdlib_module_names
            and not any(i == p or i.startswith(p) for p in _PERMITIDOS_GOALS_WRITE)
        )
    )


def _goals_write_mas(extra: str, tmp_path: Path) -> Path:
    """Copia de `goals_write.py` en `tmp_path` con `extra` al final (fuga)."""
    destino = tmp_path / "goals_write.py"
    destino.write_text(
        (RAIZ / "app" / "precio" / "goals_write.py").read_text(encoding="utf-8")
        + "\n"
        + extra
        + "\n",
        encoding="utf-8",
    )
    return destino


def test_goals_write_solo_importa_psycopg_precio_y_stdlib():
    fugas = _fugas_imports_goals_write(RAIZ / "app" / "precio" / "goals_write.py")
    assert not fugas, f"goals_write.py importa fuera de lo permitido: {fugas}"


def test_goals_write_sin_reloj_ni_entorno_ni_dinamico():
    """AC1: la excepcion por nombre no ampara reloj, entorno ni import
    dinamico en `goals_write.py`."""
    arbol = ast.parse((RAIZ / "app" / "precio" / "goals_write.py").read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) == []
    assert _usos_import_dinamico(arbol) == []


def test_candado_imports_caza_fuga_sembrada(tmp_path):
    (tmp_path / "goals_write.py").write_text(
        (RAIZ / "app" / "precio" / "goals_write.py").read_text(encoding="utf-8")
        + "\nimport httpx  # fuga sembrada\n",
        encoding="utf-8",
    )
    assert _fugas_imports_goals_write(tmp_path / "goals_write.py") == ["httpx"]


def test_candado_imports_goals_write_caza_import_time(tmp_path):
    """AC1, fuga sembrada: `import time` + `time.time()` sale rojo."""
    copia = _goals_write_mas("import time\n\n_huella = time.time()  # fuga\n", tmp_path)
    assert _fugas_imports_goals_write(copia) != []


def test_candado_imports_goals_write_caza_from_os_getenv(tmp_path):
    """AC1, fuga sembrada: `from os import getenv` sale rojo."""
    copia = _goals_write_mas("from os import getenv\n\n_ajuste = getenv  # fuga\n", tmp_path)
    assert _fugas_imports_goals_write(copia) != []


def test_candado_reloj_goals_write_caza_os_environ(tmp_path):
    """AC1, fuga sembrada: `os.environ[...]` sale rojo (reloj/entorno)."""
    copia = _goals_write_mas('_ajuste = os.environ["X"]  # fuga\n', tmp_path)
    arbol = ast.parse(copia.read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) != []


def test_candado_imports_goals_write_caza_importlib(tmp_path):
    """AC1, fuga sembrada: `import importlib` + uso sale rojo."""
    copia = _goals_write_mas(
        'import importlib\n\n_mod = importlib.import_module("x")  # fuga\n', tmp_path
    )
    assert _fugas_imports_goals_write(copia) != []


def test_candado_dinamico_goals_write_caza_dunder_import(tmp_path):
    """AC1, fuga sembrada: `__import__("httpx")` sale rojo."""
    copia = _goals_write_mas('_mod = __import__("httpx")  # fuga\n', tmp_path)
    arbol = ast.parse(copia.read_text(encoding="utf-8"))
    assert _usos_import_dinamico(arbol) != []


def test_candado_reloj_goals_write_caza_datetime_now(tmp_path):
    """AC1, fuga sembrada: `datetime.now()` sale rojo (reloj)."""
    copia = _goals_write_mas(
        "from datetime import datetime\n\n_ahora = datetime.now()  # fuga\n", tmp_path
    )
    arbol = ast.parse(copia.read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) != []


# ---------------------------------------------------------------------------
# Ronda 1 (auditoria del lead + cruzada): mutantes del lead y comportamiento
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_banda_bordes_inclusivos_se_siembran():
    """L4: 10.00 y 60.00 entran (BETWEEN 0.10 AND 0.60)."""
    from app.precio.goals_write import sembrar_goal

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-L4-A", ext="ASIN-L4-A")
        b = _cadena(conn, sku="SKU-L4-B", ext="ASIN-L4-B")
        gid_a = sembrar_goal(
            conn,
            listing_id=a["listing"],
            platform="amazon_mx",
            goal_pct="10.00",
            mode="shadow",
            go_literal=None,
        )
        gid_b = sembrar_goal(
            conn,
            listing_id=b["listing"],
            platform="amazon_mx",
            goal_pct="60.00",
            mode="shadow",
            go_literal=None,
        )
        assert gid_a != gid_b
        assert _cuenta_goals(conn) == 2


@_skip_sin_pg
def test_banda_bordes_inclusivos_en_el_tool():
    """L4b: el dry-run acepta 10.00 y 60.00 (con salto confirmado)."""
    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-L4-A", ext="ASIN-L4-A")
        b = _cadena(conn, sku="SKU-L4-B", ext="ASIN-L4-B")
        for datos, goal in ((a, "10.00"), (b, "60.00")):
            res = _tool(
                "--listing-id",
                str(datos["listing"]),
                "--platform",
                "amazon_mx",
                "--goal-pct",
                goal,
                "--mode",
                "shadow",
                "--confirmar-salto",
                str(datos["listing"]),
                dsn=dsn,
            )
            assert res.returncode == 0, res.stderr
            assert "fuera de banda" not in res.stderr


@_skip_sin_pg
def test_huella_ata_el_modo():
    """L5: la huella de un dry-run shadow no autoriza un go live."""
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        seco = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert seco.returncode == 0, seco.stderr
        huella = seco.stdout.split("huella: ")[1].split()[0]
        go = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "live",
            "--acepto-mutacion-real",
            "--huella",
            huella,
            "--go",
            "enciende",
            dsn=dsn,
        )
        assert go.returncode == 2
        assert "huella" in go.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_huella_ata_el_goal():
    """L5b: la huella de un dry-run al 30.00 no autoriza un go al 50.00."""
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        seco = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert seco.returncode == 0, seco.stderr
        huella = seco.stdout.split("huella: ")[1].split()[0]
        go = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "50.00",
            "--mode",
            "shadow",
            "--acepto-mutacion-real",
            "--huella",
            huella,
            dsn=dsn,
        )
        assert go.returncode == 2
        assert "huella" in go.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_salto_hacia_abajo_tambien_aborta():
    """L10: P* 43 % abajo de P_actual aborta sin --confirmar-salto."""
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "10.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "25" in res.stderr
        assert "P*=66.07 MXN" in res.stdout
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_cerrar_elige_el_vigente_entre_cerrados():
    """L11: con un goal viejo ya cerrado, --cerrar cierra el vigente."""
    from app.precio.goals_write import cerrar_goal, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        viejo = sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        nuevo = conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
            " valid_from, creado_por, go_literal)"
            " VALUES (%s, 'amazon_mx', 0.35, 'shadow', %s, 't', NULL) RETURNING id",
            (datos["listing"], hoy - timedelta(days=10)),
        ).fetchone()[0]
        assert cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx") == nuevo
        estados = dict(conn.execute("SELECT id, valid_to FROM precio_goal").fetchall())
        assert estados[viejo] is not None
        assert estados[nuevo] is not None


@_skip_sin_pg
def test_huella_sin_acepto_tambien_aborta():
    """L12: --huella sin --acepto-mutacion-real es dry-run enganoso."""
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            "--huella",
            "cualquiera",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "acepto-mutacion-real" in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_r1_siembra_con_vigente_reemplaza():
    """R1 (G.1, cambio 3/3): un listing con goal vigente no aborta: el plan
    propone reemplazar y el go cierra el vigente y siembra el nuevo."""
    from app.precio.goals_write import sembrar_goal

    with _db() as (conn, dsn):
        datos = _cadena(conn)
        gid_viejo = sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        args = [
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "40.00",
            "--mode",
            "shadow",
        ]
        seco = _tool(*args, dsn=dsn)
        assert seco.returncode == 0, seco.stderr
        assert "reemplaza" in seco.stdout
        assert "goal_actual=30.00%" in seco.stdout
        assert "goal=40.00%" in seco.stdout
        huella = seco.stdout.split("huella: ")[1].split()[0]
        go = _tool(*args, "--acepto-mutacion-real", "--huella", huella, dsn=dsn)
        assert go.returncode == 0, go.stderr
        assert _cuenta_goals(conn) == 2
        vigente = conn.execute(
            "SELECT id, margen_goal_pct FROM precio_goal"
            " WHERE listing_id = %s AND platform = 'amazon_mx' AND valid_to IS NULL",
            (datos["listing"],),
        ).fetchall()
        assert len(vigente) == 1
        assert vigente[0][1] == Decimal("0.4000")
        assert vigente[0][0] != gid_viejo
        viejo = conn.execute(
            "SELECT valid_from, valid_to FROM precio_goal WHERE id = %s", (gid_viejo,)
        ).fetchone()
        assert viejo[1] is not None


@_skip_sin_pg
def test_r1_csv_con_vigente_reemplaza(tmp_path):
    """R1 en lote (G.1, cambio 3/3): la fila con vigente se reemplaza: el
    dry-run sale 0 con `siembra` para la nueva y `reemplaza` para la
    vigente, y el go escribe las dos."""
    from app.precio.goals_write import sembrar_goal

    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-R1-A", ext="ASIN-R1-A")
        b = _cadena(conn, sku="SKU-R1-B", ext="ASIN-R1-B")
        sembrar_goal(
            conn,
            listing_id=b["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        csv = tmp_path / "lote.csv"
        csv.write_text(
            "listing_id,platform,goal_pct\n"
            f"{a['listing']},amazon_mx,30.00\n"
            f"{b['listing']},amazon_mx,40.00\n",
            encoding="utf-8",
        )
        seco = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
        assert seco.returncode == 0, seco.stderr
        assert "[plan] siembra" in seco.stdout
        assert "[plan] reemplaza" in seco.stdout
        huella = seco.stdout.split("huella: ")[1].split()[0]
        go = _tool(
            "--csv",
            str(csv),
            "--mode",
            "shadow",
            "--acepto-mutacion-real",
            "--huella",
            huella,
            dsn=dsn,
        )
        assert go.returncode == 0, go.stderr
        assert _cuenta_goals(conn) == 3
        vigentes = conn.execute(
            "SELECT listing_id, margen_goal_pct FROM precio_goal WHERE valid_to IS NULL"
        ).fetchall()
        assert sorted((v[0], v[1]) for v in vigentes) == [
            (a["listing"], Decimal("0.3000")),
            (b["listing"], Decimal("0.4000")),
        ]


@_skip_sin_pg
def test_dry_run_listing_inexistente_aborta_antes_de_la_huella():
    """AC2: `--listing-id` mal escrito aborta el dry-run antes de la huella."""
    with _db() as (conn, dsn):
        _config(conn)
        res = _tool(
            "--listing-id",
            "999999",
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "inexistente" in res.stderr
        assert "999999" in res.stderr
        assert "huella: " not in res.stdout
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_dry_run_lote_listing_de_otra_platform_aborta_con_linea(tmp_path):
    """AC2 en lote: la fila con listing de otra platform aborta con su linea."""
    with _db() as (conn, dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod, platform="amazon_mx")
        _config(conn)
        csv = tmp_path / "lote.csv"
        csv.write_text(
            f"listing_id,platform,goal_pct\n{lid},amazon_mx,30.00\n{lid},amazon_us,30.00\n",
            encoding="utf-8",
        )
        res = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
        assert res.returncode == 2
        assert "inexistente" in res.stderr
        assert "nea 3" in res.stderr
        assert "huella: " not in res.stdout
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_r2_mismo_dia_reedita_deja_vigente_y_anulado():
    """R2 (G.1, cambio 2/3 + guia §7): sembrar y volver a sembrar el mismo
    dia deja un goal vigente y uno anulado (`valid_to = valid_from`)."""
    from app.precio.goals_write import cerrar_goal, sembrar_goal

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        gid_viejo = sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx")
        gid_nuevo = sembrar_goal(
            conn,
            listing_id=datos["listing"],
            platform="amazon_mx",
            goal_pct="40.00",
            mode="shadow",
            go_literal=None,
        )
        assert gid_nuevo != gid_viejo
        assert _cuenta_goals(conn) == 2
        filas = {
            gid: (desde, hasta)
            for gid, desde, hasta in conn.execute(
                "SELECT id, valid_from, valid_to FROM precio_goal"
            ).fetchall()
        }
        assert filas[gid_viejo][1] is not None
        assert filas[gid_viejo][0] == filas[gid_viejo][1]
        assert filas[gid_nuevo][1] is None
        vigente = conn.execute(
            "SELECT margen_goal_pct FROM precio_goal WHERE valid_to IS NULL"
        ).fetchone()[0]
        assert vigente == Decimal("0.4000")


def test_k7_no_positivo_separado_de_fraccion():
    """K7: -5 y 0 dicen «no positivo»; 0.30 dice «parece fraccion»."""
    from app.precio.goals_write import PrecioGoalInvalido, fraccion_desde_porcentaje

    with pytest.raises(PrecioGoalInvalido, match="no positivo"):
        fraccion_desde_porcentaje("-5")
    with pytest.raises(PrecioGoalInvalido, match="no positivo"):
        fraccion_desde_porcentaje("0")
    with pytest.raises(PrecioGoalInvalido, match="parece fracci"):
        fraccion_desde_porcentaje("0.30")


@_skip_sin_pg
def test_g1_goal_y_m_actual_en_por_ciento():
    """G1: `contribucion_pct` esta en por ciento; el plan imprime goal y
    m_actual en la misma unidad (por ciento, dos decimales)."""
    with _db() as (conn, dsn):
        datos = _cadena(conn, sku="SKU-G1", ext="ASIN-G1", m="36.6379")
        res = _tool(
            "--listing-id",
            str(datos["listing"]),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 0, res.stderr
        esperada = (
            f"[plan] siembra listing={datos['listing']} platform=amazon_mx"
            f" goal=30.00% m_actual=36.64% P*={P_ESTRELLA_30} MXN P_actual=116.00 MXN"
        )
        assert esperada in res.stdout


@_skip_sin_pg
def test_g3_confirmar_salto_es_por_listing(tmp_path):
    """G3: confirmar un salto no autoriza el salto del otro listing del lote."""
    with _db() as (conn, dsn):
        a = _cadena(conn, sku="SKU-G3-A", ext="ASIN-G3-A")
        b = _cadena(conn, sku="SKU-G3-B", ext="ASIN-G3-B")
        csv = tmp_path / "lote.csv"
        csv.write_text(
            "listing_id,platform,goal_pct\n"
            f"{a['listing']},amazon_mx,55.00\n"
            f"{b['listing']},amazon_mx,55.00\n",
            encoding="utf-8",
        )
        solo_uno = _tool(
            "--csv",
            str(csv),
            "--mode",
            "shadow",
            "--confirmar-salto",
            str(a["listing"]),
            dsn=dsn,
        )
        assert solo_uno.returncode == 2
        assert str(b["listing"]) in solo_uno.stderr
        assert _cuenta_goals(conn) == 0
        desconocido = _tool(
            "--csv",
            str(csv),
            "--mode",
            "shadow",
            "--confirmar-salto",
            "999999",
            dsn=dsn,
        )
        assert desconocido.returncode == 2
        assert "999999" in desconocido.stderr
        los_dos = _tool(
            "--csv",
            str(csv),
            "--mode",
            "shadow",
            "--confirmar-salto",
            str(a["listing"]),
            "--confirmar-salto",
            str(b["listing"]),
            dsn=dsn,
        )
        assert los_dos.returncode == 0, los_dos.stderr
        assert "huella: " in los_dos.stdout


@_skip_sin_pg
def test_g4_aviso_con_motivo_si_falta_p_estrella():
    """G4: con escenario pero sin P* (componente faltante), el aviso sale
    y dice por que no se evalua el salto."""
    with _db() as (conn, dsn):
        prod = _producto(conn, sku="SKU-G4")
        listing = _listing(conn, prod, ext="ASIN-G4", sku="SKU-G4")
        _config(conn)
        marca = datetime.now(UTC)
        oferta = _oferta(
            conn,
            listing,
            ext="ASIN-G4",
            sku="SKU-G4",
            evento="oferta-G4",
            fetched=marca,
            observed=marca,
        )
        fee = _fee(
            conn,
            oferta,
            listing,
            ext="ASIN-G4",
            sku="SKU-G4",
            evento="fee-G4",
            fetched=marca,
            observed=marca,
        )
        pol = _politica(conn)
        costo = _costo(conn, prod)
        run_id, validada_en = _run(conn)
        _escenario(
            conn,
            listing,
            oferta,
            fee,
            pol,
            costo,
            run_id,
            validada_en,
            ext="ASIN-G4",
            sku="SKU-G4",
            m="24.00",
            evento="esc-G4",
            sin_componente="isr",
        )
        res = _tool(
            "--listing-id",
            str(listing),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 0, res.stderr
        assert "m_actual=24.00%" in res.stdout
        assert "P*=sin_escenario" in res.stdout
        assert "aviso:" in res.stdout
        assert "referencia_sin_derivar" in res.stdout


@_skip_sin_pg
def test_s1_sku_sin_publicacion_aborta():
    """S1: --sku que no mapea a nada aborta."""
    with _db() as (conn, dsn):
        _config(conn)
        res = _tool(
            "--sku",
            "SKU-NADIE",
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert "sin publicaci" in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_s1_sku_ambiguo_aborta_con_candidatos():
    """S1: --sku que mapea a dos publicaciones aborta con los listing_id."""
    with _db() as (conn, dsn):
        _config(conn)
        prod_a = _producto(conn, sku="SKU-DUP-A")
        lid_a = _listing(conn, prod_a, ext="ASIN-DUP-A", sku="SKU-DUP")
        prod_b = _producto(conn, sku="SKU-DUP-B")
        lid_b = _listing(conn, prod_b, ext="ASIN-DUP-B", sku="SKU-DUP")
        res = _tool(
            "--sku",
            "SKU-DUP",
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert res.returncode == 2
        assert str(lid_a) in res.stderr
        assert str(lid_b) in res.stderr
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_s1_sku_unico_sigue_como_listing_id():
    """S1: --sku con una sola publicacion siembra como --listing-id."""
    with _db() as (conn, dsn):
        datos = _cadena(conn, sku="SKU-UNICO", ext="ASIN-UNICO")
        seco = _tool(
            "--sku",
            "SKU-UNICO",
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert seco.returncode == 0, seco.stderr
        assert f"listing={datos['listing']}" in seco.stdout
        huella = seco.stdout.split("huella: ")[1].split()[0]
        go = _tool(
            "--sku",
            "SKU-UNICO",
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            "--acepto-mutacion-real",
            "--huella",
            huella,
            dsn=dsn,
        )
        assert go.returncode == 0, go.stderr
        assert _cuenta_goals(conn) == 1


@_skip_sin_pg
def test_s1_sku_excluyente_con_listing_y_csv(tmp_path):
    """S1: --sku no se combina con --listing-id ni con --csv."""
    with _db() as (conn, dsn):
        _config(conn)
        res = _tool(
            "--sku",
            "X",
            "--listing-id",
            "1",
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            dsn=dsn,
        )
        assert res.returncode == 2
        csv = tmp_path / "lote.csv"
        csv.write_text("listing_id,platform,goal_pct\n1,amazon_mx,30.00\n", encoding="utf-8")
        res2 = _tool(
            "--sku",
            "X",
            "--csv",
            str(csv),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            dsn=dsn,
        )
        assert res2.returncode == 2


# ---------------------------------------------------------------------------
# G.1 (REPRICING 02): plan de goals puro + escritura todo o nada
# ---------------------------------------------------------------------------

BANDA_G1 = (Decimal("0.10"), Decimal("0.60"))

# Misma cuenta de `_cadena` (C=40, P=116, F=15, ref=12/116, r=0.025,
# d=1.16): P* al 30 % = 89.88, al 55 % = 163.55, al 10 % = 66.07.
_FEE_DETALLES_G1 = (
    {
        "fee_type": "ReferralFee",
        "final_fee": "12",
        "tax_amount": None,
        "included_fee_details": [],
    },
    {
        "fee_type": "FbaFee",
        "final_fee": "3",
        "tax_amount": None,
        "included_fee_details": [],
    },
)


def _g1_unidad(listing_id=7, platform="amazon_mx"):
    from app.precio.puerto import Miembro, UnidadPrecio

    return UnidadPrecio(
        listing_id=listing_id,
        plataforma=platform,
        externo="ASIN-G1",
        canal="fba",
        producto_ancla=11,
        miembros=(Miembro(11, "SKU-G1", None),),
    )


def _g1_componentes(costo="40", ingreso="100", envio="0", isr="2.50", bruto="116"):
    def _uno(nombre, importe):
        return {
            "nombre": nombre,
            "importe_normalizado": str(importe),
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        }

    return [
        _uno("precio_bruto", bruto),
        _uno("ingreso_normalizado", ingreso),
        _uno("costo_normalizado", costo),
        _uno("logistica", envio),
        _uno("isr", isr),
    ]


def _g1_lectura(**cambios):
    from app.precio.siembra import LecturaHoy

    base = dict(
        m_pct=Decimal("24.00"),
        p_actual=Decimal("116"),
        moneda="MXN",
        componentes=_g1_componentes(),
        fee_total=Decimal("15"),
        fee_quoted=Decimal("116"),
        fee_detalles=list(_FEE_DETALLES_G1),
        fee_estado="success",
    )
    base.update(cambios)
    return LecturaHoy(**base)


def _g1_vigente(gid=9, goal="0.3000", mode="shadow"):
    from app.precio.siembra import GoalVigente

    return GoalVigente(id=gid, goal=Decimal(goal), mode=mode)


def _g1_candidato(lectura="auto", vigente=None, listing_id=7, platform="amazon_mx"):
    from app.precio.siembra import Candidato

    if lectura == "auto":
        lectura = _g1_lectura()
    return Candidato(
        unidad=_g1_unidad(listing_id, platform),
        seller_sku="SKU-G1",
        goal_vigente=vigente,
        lectura=lectura,
    )


def _g1_plan(conn, pares, pedido, banda=BANDA_G1):
    """`leer_candidatos` + `planear`: el plan fresco de `aplicar_plan`."""
    from app.precio.goals_write import PorIds, leer_candidatos
    from app.precio.siembra import planear

    return planear(
        leer_candidatos(conn, filtro=PorIds(pares=tuple(pares))),
        pedido=pedido,
        banda=banda,
    )


@_skip_sin_pg
def test_g1_contrato_siembra_reproduce_el_plan_del_tool():
    """G.1 (contrato, TDD): con la misma cadena, `siembra.planear` produce
    el mismo plan con la misma huella que el dry-run del tool."""
    import hashlib

    from app.precio.goals_write import PorIds, leer_candidatos
    from app.precio.siembra import PedidoValores, ValorPorUnidad, planear

    with _db() as (conn, dsn):
        datos = _cadena(conn)
        lid = datos["listing"]
        seco = _tool(
            "--listing-id",
            str(lid),
            "--platform",
            "amazon_mx",
            "--goal-pct",
            "30.00",
            "--mode",
            "shadow",
            dsn=dsn,
        )
        assert seco.returncode == 0, seco.stderr
        huella_tool = seco.stdout.split("huella: ")[1].split()[0]
        plan = planear(
            leer_candidatos(conn, filtro=PorIds(pares=((lid, "amazon_mx"),))),
            pedido=PedidoValores(
                goals=(ValorPorUnidad(lid, "amazon_mx", Decimal("0.3000")),),
                mode="shadow",
            ),
            banda=BANDA_G1,
        )
        assert len(plan.filas) == 1
        fila = plan.filas[0]
        assert fila.accion == "sembrar"
        assert fila.goal_propuesto == Decimal("0.3000")
        assert fila.m_hoy == Decimal("0.24")
        assert fila.p_estrella is not None and fila.p_estrella.valor == Decimal("89.88")
        assert fila.p_estrella.moneda == "MXN"
        assert fila.p_actual is not None and fila.p_actual.valor == Decimal("116")
        assert fila.p_actual.moneda == "MXN"
        assert fila.bloqueo is None
        assert fila.motivo is None
        assert plan.huella == huella_tool
        vieja = hashlib.sha256(f"siembra:{lid}:amazon_mx:0.3000:shadow".encode()).hexdigest()[:16]
        assert plan.huella == vieja


def test_g1_planear_margen_de_hoy_propone_margen():
    """Guia §1: `planear` con `PedidoMargenDeHoy` propone el margen de hoy
    de cada unidad (el goal se cuantiza a 4 decimales; `m_hoy` no)."""
    from app.precio.siembra import PedidoMargenDeHoy, planear

    plan = planear(
        (
            _g1_candidato(_g1_lectura(m_pct=Decimal("30.00")), listing_id=7),
            _g1_candidato(_g1_lectura(m_pct=Decimal("36.6379")), listing_id=8),
        ),
        pedido=PedidoMargenDeHoy(mode="shadow"),
        banda=BANDA_G1,
    )
    assert [f.accion for f in plan.filas] == ["sembrar", "sembrar"]
    assert [f.bloqueo for f in plan.filas] == [None, None]
    assert plan.filas[0].m_hoy == Decimal("0.30")
    assert plan.filas[0].goal_propuesto == Decimal("0.3000")
    assert plan.filas[1].m_hoy == Decimal("0.366379")
    assert plan.filas[1].goal_propuesto == Decimal("0.3664")


def test_g1_planear_fuera_de_banda_propone_borde_confirmable():
    """Guia §2a: margen de hoy fuera de la banda sale con el bloqueo
    `fuera_de_banda`, confirmable, proponiendo el borde (nunca recorte
    en silencio: el borde va visible en el plan)."""
    from app.precio.siembra import PedidoMargenDeHoy, planear

    plan = planear(
        (
            _g1_candidato(_g1_lectura(m_pct=Decimal("8.00")), listing_id=7),
            _g1_candidato(_g1_lectura(m_pct=Decimal("63.00")), listing_id=8),
        ),
        pedido=PedidoMargenDeHoy(mode="shadow"),
        banda=BANDA_G1,
    )
    assert [f.bloqueo for f in plan.filas] == ["fuera_de_banda", "fuera_de_banda"]
    assert [f.confirmable for f in plan.filas] == [True, True]
    assert plan.filas[0].goal_propuesto == Decimal("0.10")
    assert plan.filas[1].goal_propuesto == Decimal("0.60")


def test_g1_planear_sin_escenario_no_confirmable():
    """Guia §2b: sin escenario sale `sin_escenario`, no confirmable: no hay
    margen de hoy que copiar."""
    from app.precio.siembra import PedidoMargenDeHoy, planear

    plan = planear(
        (_g1_candidato(None, listing_id=7),),
        pedido=PedidoMargenDeHoy(mode="shadow"),
        banda=BANDA_G1,
    )
    (fila,) = plan.filas
    assert fila.accion == "sembrar"
    assert fila.bloqueo == "sin_escenario"
    assert fila.confirmable is False
    assert fila.goal_propuesto is None
    assert fila.motivo == "sin_escenario"


def test_g1_planear_salto_mayor_25():
    """Guia §3: precio objetivo a mas de 25 % del actual sale
    `salto_mayor_25` (arriba y abajo); a 22.5 % no sale bloqueo."""
    from app.precio.siembra import PedidoValores, ValorPorUnidad, planear

    def _bloqueo(lid, goal):
        plan = planear(
            (_g1_candidato(listing_id=lid),),
            pedido=PedidoValores(
                goals=(ValorPorUnidad(lid, "amazon_mx", Decimal(goal)),), mode="shadow"
            ),
            banda=BANDA_G1,
        )
        return plan.filas[0]

    arriba = _bloqueo(7, "0.5500")
    assert arriba.p_estrella is not None and arriba.p_estrella.valor == Decimal("163.55")
    assert arriba.bloqueo == "salto_mayor_25"
    assert arriba.confirmable is True
    abajo = _bloqueo(8, "0.1000")
    assert abajo.p_estrella is not None and abajo.p_estrella.valor == Decimal("66.07")
    assert abajo.bloqueo == "salto_mayor_25"
    dentro = _bloqueo(9, "0.3000")
    assert dentro.bloqueo is None


def test_g1_planear_vigente_reemplaza():
    """Guia §4: un listing con goal vigente sale con la accion `reemplazar`."""
    from app.precio.siembra import PedidoValores, ValorPorUnidad, planear

    plan = planear(
        (_g1_candidato(vigente=_g1_vigente(), listing_id=7),),
        pedido=PedidoValores(
            goals=(ValorPorUnidad(7, "amazon_mx", Decimal("0.4000")),), mode="shadow"
        ),
        banda=BANDA_G1,
    )
    (fila,) = plan.filas
    assert fila.accion == "reemplazar"
    assert fila.goal_id == 9
    assert fila.goal_actual == Decimal("0.3000")
    assert fila.goal_propuesto == Decimal("0.4000")
    assert fila.bloqueo is None
    assert fila.confirmable is False


def test_g1_planear_mismo_goal_y_modo_sale_sin_cambio():
    """Con el mismo goal y el mismo modo no hay nada que escribir: accion
    `nada` con bloqueo `sin_cambio` (no se bate churn de cierra+siembra)."""
    from app.precio.siembra import PedidoValores, ValorPorUnidad, planear

    plan = planear(
        (_g1_candidato(vigente=_g1_vigente(), listing_id=7),),
        pedido=PedidoValores(
            goals=(ValorPorUnidad(7, "amazon_mx", Decimal("0.3000")),), mode="shadow"
        ),
        banda=BANDA_G1,
    )
    (fila,) = plan.filas
    assert fila.accion == "nada"
    assert fila.bloqueo == "sin_cambio"
    assert fila.confirmable is False
    assert fila.goal_propuesto == Decimal("0.3000")


def test_g1_planear_motivos_sin_p_estrella():
    """Sin P* derivable (oferta ausente, fee inutilizable, componente
    faltante), la fila avisa con su motivo y el salto no se evalua."""
    from app.precio.siembra import PedidoValores, ValorPorUnidad, planear

    def _fila(lid, **cambios):
        plan = planear(
            (_g1_candidato(_g1_lectura(**cambios), listing_id=lid),),
            pedido=PedidoValores(
                goals=(ValorPorUnidad(lid, "amazon_mx", Decimal("0.3000")),),
                mode="shadow",
            ),
            banda=BANDA_G1,
        )
        return plan.filas[0]

    sin_oferta = _fila(7, p_actual=None, moneda=None)
    assert (sin_oferta.p_estrella, sin_oferta.motivo) == (None, "oferta_ausente")
    assert sin_oferta.m_hoy == Decimal("0.24")
    assert sin_oferta.bloqueo is None
    fee_malo = _fila(8, fee_estado="error")
    assert (fee_malo.p_estrella, fee_malo.motivo) == (None, "fee_no_utilizable")
    assert fee_malo.bloqueo is None
    sin_comp = _fila(9, componentes=[c for c in _g1_componentes() if c["nombre"] != "isr"])
    assert (sin_comp.p_estrella, sin_comp.motivo) == (None, "referencia_sin_derivar")
    assert sin_comp.bloqueo is None


def test_g1_planear_modo_conserva_goal_y_sin_cambio_no_hace_nada():
    """`PedidoModo` conserva el goal vigente; con el modo ya igual sale
    `sin_cambio` con accion `nada`."""
    from app.precio.siembra import PedidoModo, planear

    cambia = planear(
        (_g1_candidato(vigente=_g1_vigente(), listing_id=7),),
        pedido=PedidoModo(mode="live"),
        banda=BANDA_G1,
    )
    (fila,) = cambia.filas
    assert fila.accion == "reemplazar"
    assert fila.goal_propuesto == Decimal("0.3000")
    assert fila.bloqueo is None
    igual = planear(
        (_g1_candidato(vigente=_g1_vigente(), listing_id=7),),
        pedido=PedidoModo(mode="shadow"),
        banda=BANDA_G1,
    )
    (nada,) = igual.filas
    assert nada.accion == "nada"
    assert nada.bloqueo == "sin_cambio"
    assert nada.confirmable is False


def test_g1_planear_valor_en_bloque_aplica_el_mismo_goal():
    """`PedidoValor` fija un goal a mano a toda la seleccion; fuera de
    banda sale `fuera_de_banda` no confirmable (no se puede sembrar)."""
    from app.precio.siembra import PedidoValor, planear

    plan = planear(
        (_g1_candidato(listing_id=7), _g1_candidato(listing_id=8)),
        pedido=PedidoValor(goal=Decimal("0.3500"), mode="shadow"),
        banda=BANDA_G1,
    )
    assert [f.goal_propuesto for f in plan.filas] == [Decimal("0.3500"), Decimal("0.3500")]
    assert [f.bloqueo for f in plan.filas] == [None, None]
    fuera = planear(
        (_g1_candidato(listing_id=7),),
        pedido=PedidoValor(goal=Decimal("0.0500"), mode="shadow"),
        banda=BANDA_G1,
    )
    (fila,) = fuera.filas
    assert fila.bloqueo == "fuera_de_banda"
    assert fila.confirmable is False


@_skip_sin_pg
def test_g1_aplicar_huella_distinta_no_escribe():
    """Guia §5: `aplicar_plan` con una huella distinta levanta
    `HuellaDistinta` (con el plan nuevo) y no escribe nada."""
    from app.precio.goals_write import HuellaDistinta, aplicar_plan, sembrar_goal
    from app.precio.siembra import PedidoValores, ValorPorUnidad

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        lid = datos["listing"]
        plan = _g1_plan(
            conn,
            [(lid, "amazon_mx")],
            PedidoValores(
                goals=(ValorPorUnidad(lid, "amazon_mx", Decimal("0.3000")),), mode="shadow"
            ),
        )
        assert plan.filas[0].accion == "sembrar"
        sembrar_goal(
            conn,
            listing_id=lid,
            platform="amazon_mx",
            goal_pct="50.00",
            mode="shadow",
            go_literal=None,
        )
        with pytest.raises(HuellaDistinta) as exc:
            aplicar_plan(
                conn,
                plan,
                huella=plan.huella,
                confirmados=frozenset(),
                go_literal=None,
                actor="t",
            )
        assert exc.value.plan.filas[0].accion == "reemplazar"
        assert exc.value.plan.huella != plan.huella
        assert _cuenta_goals(conn) == 1


@_skip_sin_pg
def test_g1_aplicar_fallo_a_mitad_no_deja_ninguna():
    """Guia §6 (cambio 1/3, todo o nada): si la tercera fila de cinco
    falla, no queda ninguna."""
    from app.precio.goals_write import PrecioGoalInvalido, aplicar_plan
    from app.precio.siembra import PedidoValores, ValorPorUnidad

    with _db() as (conn, _dsn):
        lids = []
        for tag in ("A", "B", "C", "D", "E"):
            datos = _cadena(conn, sku=f"SKU-G1-{tag}", ext=f"ASIN-G1-{tag}")
            lids.append(datos["listing"])
        pedido = PedidoValores(
            goals=tuple(ValorPorUnidad(lid, "amazon_mx", Decimal("0.3000")) for lid in lids),
            mode="shadow",
        )
        plan = _g1_plan(conn, [(lid, "amazon_mx") for lid in lids], pedido)
        import app.precio.goals_write as gw

        llamadas = []
        sembrar_real = gw.sembrar_goal

        def _falla_en_tercera(conn_, **kw):
            llamadas.append(kw["listing_id"])
            if len(llamadas) == 3:
                raise PrecioGoalInvalido("saboteo: la tercera fila falla")
            return sembrar_real(conn_, **kw)

        gw.sembrar_goal = _falla_en_tercera
        try:
            with pytest.raises(PrecioGoalInvalido, match="tercera fila"):
                aplicar_plan(
                    conn,
                    plan,
                    huella=plan.huella,
                    confirmados=frozenset(),
                    go_literal=None,
                    actor="t",
                )
        finally:
            gw.sembrar_goal = sembrar_real
        assert len(llamadas) == 3
        assert _cuenta_goals(conn) == 0


@_skip_sin_pg
def test_g1_aplicar_omite_bloqueo_y_cuenta():
    """Bosquejo K: la fila con bloqueo no confirmable (o confirmable sin
    confirmar) se omite y se cuenta: el resto se escribe."""
    from app.precio.goals_write import HuellaDistinta, aplicar_plan
    from app.precio.siembra import PedidoValores, ValorPorUnidad

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-G1-OA", ext="ASIN-G1-OA")
        b = _cadena(conn, sku="SKU-G1-OB", ext="ASIN-G1-OB")
        plan = _g1_plan(
            conn,
            [(a["listing"], "amazon_mx"), (b["listing"], "amazon_mx")],
            PedidoValores(
                goals=(
                    ValorPorUnidad(a["listing"], "amazon_mx", Decimal("0.3000")),
                    ValorPorUnidad(b["listing"], "amazon_mx", Decimal("0.5500")),
                ),
                mode="shadow",
            ),
        )
        assert [f.bloqueo for f in plan.filas] == [None, "salto_mayor_25"]
        res = aplicar_plan(
            conn,
            plan,
            huella=plan.huella,
            confirmados=frozenset(),
            go_literal=None,
            actor="t",
        )
        assert (res.sembrados, res.cerrados, res.lote) == (1, 0, plan.huella)
        assert _cuenta_goals(conn) == 1
        vigente = conn.execute(
            "SELECT listing_id FROM precio_goal WHERE valid_to IS NULL"
        ).fetchone()[0]
        assert vigente == a["listing"]
        with pytest.raises(HuellaDistinta):
            aplicar_plan(
                conn,
                plan,
                huella=plan.huella,
                confirmados=frozenset({b["listing"]}),
                go_literal=None,
                actor="t",
            )
        assert _cuenta_goals(conn) == 1
        pedido2 = PedidoValores(
            goals=(
                ValorPorUnidad(a["listing"], "amazon_mx", Decimal("0.3000")),
                ValorPorUnidad(b["listing"], "amazon_mx", Decimal("0.5500")),
            ),
            mode="shadow",
        )
        plan2 = _g1_plan(conn, [(a["listing"], "amazon_mx"), (b["listing"], "amazon_mx")], pedido2)
        assert [f.accion for f in plan2.filas] == ["nada", "sembrar"]
        res2 = aplicar_plan(
            conn,
            plan2,
            huella=plan2.huella,
            confirmados=frozenset({b["listing"]}),
            go_literal=None,
            actor="t",
        )
        assert (res2.sembrados, res2.cerrados) == (1, 0)
        assert _cuenta_goals(conn) == 2


@_skip_sin_pg
def test_g1_aplicar_estricto_aborta_con_bloqueo():
    """El tool (estricto) no omite en silencio: un bloqueado sin confirmar
    aborta todo con `BloqueoSinConfirmar` y no escribe nada."""
    import pytest as _pytest

    from app.precio.goals_write import BloqueoSinConfirmar, aplicar_plan
    from app.precio.siembra import PedidoValores, ValorPorUnidad

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        lid = datos["listing"]
        plan = _g1_plan(
            conn,
            [(lid, "amazon_mx")],
            PedidoValores(
                goals=(ValorPorUnidad(lid, "amazon_mx", Decimal("0.5500")),), mode="shadow"
            ),
        )
        assert plan.filas[0].bloqueo == "salto_mayor_25"
        with _pytest.raises(BloqueoSinConfirmar) as exc:
            aplicar_plan(
                conn,
                plan,
                huella=plan.huella,
                confirmados=frozenset(),
                go_literal=None,
                actor="t",
                estricto=True,
            )
        assert exc.value.plan.filas[0].bloqueo == "salto_mayor_25"
        assert _cuenta_goals(conn) == 0
        res = aplicar_plan(
            conn,
            plan,
            huella=plan.huella,
            confirmados=frozenset({lid}),
            go_literal=None,
            actor="t",
            estricto=True,
        )
        assert (res.sembrados, res.cerrados) == (1, 0)
        assert _cuenta_goals(conn) == 1


@_skip_sin_pg
def test_g1_aplicar_live_sin_go_no_escribe():
    """Guia §8: `live` sin go literal no escribe; con go escribe el lote
    con su rastro (`origen`, `lote`, `m_referencia`)."""
    from app.precio.goals_write import PrecioGoalInvalido, aplicar_plan
    from app.precio.siembra import PedidoValores, ValorPorUnidad

    with _db() as (conn, _dsn):
        datos = _cadena(conn)
        lid = datos["listing"]
        plan = _g1_plan(
            conn,
            [(lid, "amazon_mx")],
            PedidoValores(
                goals=(ValorPorUnidad(lid, "amazon_mx", Decimal("0.3000")),), mode="live"
            ),
        )
        with pytest.raises(PrecioGoalInvalido, match="live sin go"):
            aplicar_plan(
                conn,
                plan,
                huella=plan.huella,
                confirmados=frozenset(),
                go_literal=None,
                actor="t",
            )
        assert _cuenta_goals(conn) == 0
        res = aplicar_plan(
            conn,
            plan,
            huella=plan.huella,
            confirmados=frozenset(),
            go_literal="enciende",
            actor="t",
        )
        assert (res.sembrados, res.cerrados, res.lote) == (1, 0, plan.huella)
        fila = conn.execute(
            "SELECT mode, go_literal, origen, lote, m_referencia FROM precio_goal"
        ).fetchone()
        assert fila == ("live", "enciende", "manual", plan.huella, Decimal("0.2400"))
