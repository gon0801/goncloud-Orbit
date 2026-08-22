"""Tests del esquema `migrations/0001_initial.sql`.

(a) ESTÁTICOS: parsean la migración con pglast y afirman los invariantes
    sellados del diseño sobre el AST (no sobre texto). Corren siempre.
(b) INTEGRACIÓN: aplican la migración en un Postgres temporal y prueban
    rechazos reales. Skip automático si no hay servidor en localhost:5432.

Regla 8 del diseño: los invariantes se prueban contra la forma real del dato;
aquí la "forma real" es el AST que PostgreSQL ejecutaría.
"""

import os
import socket
from pathlib import Path

import pglast
import pytest
from pglast import ast, enums

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations" / "0001_initial.sql").read_text(encoding="utf-8")
STMTS = tuple(pglast.parse_sql(SQL))

APPEND_ONLY = {
    "ads_metric_observation",
    "search_term_observation",
    "ledger_event",
    "config_version",
    "decision",
    "fx_rate",
    "external_reconciliation",
}

TIPOS_FLOAT = {"float4", "float8"}  # real / double precision / float


def _stmts(cls):
    return [s.stmt for s in STMTS if isinstance(s.stmt, cls)]


TABLES = {t.relation.relname: t for t in _stmts(ast.CreateStmt)}
TRIGGERS = _stmts(ast.CreateTrigStmt)
INDEXES = _stmts(ast.IndexStmt)
FUNCTIONS = {f.funcname[-1].sval: f for f in _stmts(ast.CreateFunctionStmt)}


def _cols(tabla):
    return {e.colname: e for e in TABLES[tabla].tableElts if isinstance(e, ast.ColumnDef)}


def _type_name(coldef):
    return ".".join(n.sval for n in coldef.typeName.names)


def _contypes(coldef):
    return {c.contype for c in (coldef.constraints or ())}


def _check_constraints(tabla):
    return {
        e.conname: e
        for e in TABLES[tabla].tableElts
        if isinstance(e, ast.Constraint)
        and e.contype == enums.ConstrType.CONSTR_CHECK
        and e.conname
    }


def _body_plpgsql(nombre_funcion):
    """Cuerpo de una función PL/pgSQL, extraído del AST de CREATE FUNCTION."""
    func = FUNCTIONS[nombre_funcion]
    for opt in func.options:
        if opt.defname == "as":
            return "".join(parte.sval for parte in opt.arg)
    raise AssertionError(f"{nombre_funcion}: sin cuerpo 'as' en el AST")


# ---------------------------------------------------------------------------
# (a) ESTÁTICOS — sobre el AST de la migración
# ---------------------------------------------------------------------------


def test_migracion_parsea():
    assert len(STMTS) > 100  # sanity: el archivo completo parseó


def test_las_siete_append_only_tienen_candado():
    # Constantes de trigger.h: BEFORE=2, DELETE=8, UPDATE=16 (INSERT=4).
    protegidas = set()
    for t in TRIGGERS:
        if t.funcname and t.funcname[-1].sval == "prohibir_mutacion":
            assert t.timing == 2 and t.row, (
                f"{t.relation.relname}: prohibir_mutacion debe ser BEFORE ... FOR EACH ROW"
            )
            assert t.events & 8 and t.events & 16, (
                f"{t.relation.relname}: el candado debe cubrir UPDATE y DELETE"
            )
            protegidas.add(t.relation.relname)
    assert protegidas >= APPEND_ONLY, (
        f"tablas append-only sin trigger prohibir_mutacion: {APPEND_ONLY - protegidas}"
    )


def test_ninguna_columna_usa_float():
    for tabla, cols in ((n, _cols(n)) for n in TABLES):
        for col in cols.values():
            tipo = _type_name(col).split(".")[-1]
            assert tipo not in TIPOS_FLOAT, f"{tabla}.{col.colname} usa {tipo}"


def test_toda_tabla_con_dinero_tiene_moneda():
    for tabla in TABLES:
        tipos = {_type_name(c) for c in _cols(tabla).values()}
        if "money_amount" in tipos:
            assert "currency" in tipos, f"{tabla} tiene money_amount sin currency"


def test_decision_tiene_ventana_not_null():
    cols = _cols("decision")
    for nombre in ("window_start", "window_end"):
        assert nombre in cols, f"decision.{nombre} no existe"
        assert enums.ConstrType.CONSTR_NOTNULL in _contypes(cols[nombre])


def test_trigger_madurez_cubre_los_tres_cortes():
    candidatos = [
        t
        for t in TRIGGERS
        if t.relation.relname == "decision"
        and t.funcname
        and t.funcname[-1].sval == "decision_madurez_corte"
    ]
    assert candidatos, "falta el trigger decision_madurez_corte en decision"
    # BEFORE INSERT FOR EACH ROW (constantes de parsenodes.h: BEFORE=2, INSERT=4).
    assert any(t.timing == 2 and t.events & 4 and t.row for t in candidatos)
    # Los kinds viven en el cuerpo PL/pgSQL: el AST de CREATE FUNCTION lo trae
    # como constante de texto (pglast no parsea cuerpos plpgsql completos), así
    # que la lista de kinds se afirma sobre ESE cuerpo extraído del AST.
    cuerpo = " ".join(_body_plpgsql("decision_madurez_corte").split())
    assert "NEW.kind IN ('pause', 'negative', 'harvest')" in cuerpo, (
        "el trigger de madurez no cubre exactamente pause/negative/harvest"
    )


def test_indices_de_cooldown_en_decision():
    columnas = {
        tuple(p.name for p in i.indexParams) for i in INDEXES if i.relation.relname == "decision"
    }
    assert ("ad_entity_id", "decided_at") in columnas  # cooldown 7d por entidad
    assert ("cycle_id",) in columnas  # reconstrucción del envelope
    # "una decisión por entidad (o entidad+término) por ciclo": dos únicos
    # parciales (con WHERE), uno por familia de kinds.
    unicos_parciales = [
        i
        for i in INDEXES
        if i.relation.relname == "decision" and i.unique and i.whereClause is not None
    ]
    assert len(unicos_parciales) == 2


def test_is_asin_like_obligatorio_sin_default():
    col = _cols("search_term_observation")["is_asin_like"]
    tipos = _contypes(col)
    assert enums.ConstrType.CONSTR_NOTNULL in tipos
    assert enums.ConstrType.CONSTR_DEFAULT not in tipos, (
        "is_asin_like con default = 'no lo revisé' disfrazado de 'no es ASIN'"
    )


def test_checks_de_signo_y_cantidad_en_ledger():
    checks = _check_constraints("ledger_event")
    # Afirmar la FORMA de la expresión, no solo el nombre: un CHECK con
    # expresión NULL pasa (no es FALSE), así que la obligación de quantity
    # solo existe si el NULL está prohibido explícitamente (NullTest).
    signos = repr(checks["ledger_convencion_signos"].raw_expr)
    assert "amount" in signos and "kind" in signos
    cantidad = repr(checks["ledger_venta_con_cantidad"].raw_expr)
    assert "NullTest" in cantidad and "quantity" in cantidad, (
        "ledger_venta_con_cantidad sin NullTest: quantity NULL pasaría el CHECK"
    )


def test_sello_moneda_en_ambas_tablas_de_metricas():
    selladas = {
        t.relation.relname
        for t in TRIGGERS
        if t.funcname and t.funcname[-1].sval == "metric_moneda_de_plataforma"
    }
    assert {"ads_metric_observation", "search_term_observation"} <= selladas


# ---------------------------------------------------------------------------
# (b) INTEGRACIÓN — skip automático sin Postgres local
# ---------------------------------------------------------------------------


def _hay_postgres_local():
    try:
        with socket.create_connection(("localhost", 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _hay_postgres_local(),
    reason="sin Postgres en localhost:5432 (Docker no levantado en esta máquina)",
)
def test_migracion_rechaza_en_vivo():
    """Aplica la migración en una base temporal y prueba 3 rechazos reales."""
    psycopg = pytest.importorskip("psycopg")
    db = f"orbit_schema_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dbname="postgres", autocommit=True)
    try:
        admin.execute(f'CREATE DATABASE "{db}"')
        conn = psycopg.connect(dbname=db, autocommit=True)
        try:
            conn.execute("SET TIME ZONE 'UTC'")
            conn.execute(SQL)  # la migración entera, con su BEGIN/COMMIT

            run_id = conn.execute(
                "INSERT INTO ingest_run (source) VALUES ('test') RETURNING id"
            ).fetchone()[0]
            entidad_us = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id)"
                " VALUES ('amazon_us', 'campaign', 'T1') RETURNING id"
            ).fetchone()[0]

            # 1. Métrica en moneda que no corresponde a la plataforma.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                    " observed_at, metric_currency, ingest_run_id) VALUES"
                    f" ({entidad_us}, '2026-08-01', now(), 'MXN', {run_id})"
                )

            # 2. Pausa con ventana inmadura (window_end = hoy).
            config_id = conn.execute(
                "INSERT INTO config_version (settings) VALUES ('{}') RETURNING id"
            ).fetchone()[0]
            ciclo_id = conn.execute(
                "INSERT INTO optimizer_cycle (mode, platform)"
                " VALUES ('shadow', 'amazon_us') RETURNING id"
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO decision (cycle_id, ad_entity_id, kind,"
                    " config_version_id, data_observed_at, window_start, window_end,"
                    " inputs) VALUES"
                    f" ({ciclo_id}, {entidad_us}, 'pause', {config_id},"
                    " now(), CURRENT_DATE - 30, CURRENT_DATE, '{}')"
                )

            # 3. Re-ingestar el mismo reporte duplica la observación.
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                " observed_at, metric_currency, source_report_id, ingest_run_id)"
                f" VALUES ({entidad_us}, '2026-08-01', now(), 'USD', 'R1', {run_id})"
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                    " observed_at, metric_currency, source_report_id, ingest_run_id)"
                    f" VALUES ({entidad_us}, '2026-08-01', now() + interval '1h',"
                    f" 'USD', 'R1', {run_id})"
                )
        finally:
            conn.close()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        admin.close()
