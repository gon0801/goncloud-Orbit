"""Herramienta de goals de precio (REPRICING 01, A.1).

`app/precio/goals_write.py` (unico escritor de `precio_goal`, solo
`app_admin`, cero Amazon) + `tools/precio_goal.py` (siembra unitaria y lote
CSV, `--mode live` con ceremonia completa, `--mode shadow` sin go literal,
`--cerrar`; dry-run con `m_actual` y `P*` por fila y aborto si el salto
`abs(P* - P_actual) > 25 %` salvo `--confirmar-salto`).

Base real (`ORBIT_TEST_DSN`, cero `skipped` con el DSN de VERIFY): cada test
levanta una DB temporal con ORDEN39 (0001 + 0002 + 0028 + 0039) y la tira al
salir (patron `db_39` de `test_precio_migracion`, autocontenido aqui para
exponer el DSN al subprocess del tool). La `config_version` se siembra con
el patron `_config_version(conn, settings)` de `test_api` l.149.
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
from test_schema import _postgres_obligatorio_ausente

RAIZ = Path(__file__).resolve().parents[1]
ORDEN39 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
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
    """DB temporal con ORDEN39; entrega `(conn, dsn)` con conn en autocommit."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"goals_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN39:
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
    from datetime import datetime

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
    from datetime import datetime

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
    m="0.24",
    evento="esc-g1",
    observed=None,
    valoracion=None,
) -> int:
    from datetime import datetime, timedelta

    observed = observed or (datetime.now(UTC) + timedelta(seconds=120))
    valoracion = valoracion or conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
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
            (Decimal(m) * 100).quantize(Decimal("0.01")),
            Decimal(m),
            Json([]),
            Json(_componentes()),
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


def _cadena(conn, *, sku="SKU-GOAL-1", ext="ASIN-G1", m="0.24"):
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
        assert "m_actual=0.2400" in res.stdout
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
            m="0.41",
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
        assert "m_actual=0.4100" in res.stdout
        assert "m_actual=0.2400" not in res.stdout


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
        res2 = _tool(*args, "--confirmar-salto", dsn=dsn)
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
# Candado propio de goals_write: solo psycopg, app.precio.* y stdlib
# ---------------------------------------------------------------------------

_PERMITIDOS_GOALS_WRITE = ("psycopg", "app.precio.")


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
        if i.split(".")[0] not in _sys.stdlib_module_names
        and not any(i == p or i.startswith(p) for p in _PERMITIDOS_GOALS_WRITE)
    )


def test_goals_write_solo_importa_psycopg_precio_y_stdlib():
    fugas = _fugas_imports_goals_write(RAIZ / "app" / "precio" / "goals_write.py")
    assert not fugas, f"goals_write.py importa fuera de lo permitido: {fugas}"


def test_candado_imports_caza_fuga_sembrada(tmp_path):
    (tmp_path / "goals_write.py").write_text(
        (RAIZ / "app" / "precio" / "goals_write.py").read_text(encoding="utf-8")
        + "\nimport httpx  # fuga sembrada\n",
        encoding="utf-8",
    )
    assert _fugas_imports_goals_write(tmp_path / "goals_write.py") == ["httpx"]
