"""Candados de la migración 0011 (ORBIT 06 · palanca de mapeo, 2026-09-02).

0011 es la primera corrección de DATOS del repo: siembra el costo histórico de
los dos SKU «Peseta» que contabilidad publicó recién el 2026-08-18 y que, sin
historia, dejaban a los seis grupos Arras MX en `catalogo_parcial` (la vista
exige costo as-of cada día de la ventana de 90 días, para el catálogo vivo
completo).

Lo que estos tests protegen, y que ningún test previo cubría:

1. **El agujero mismo** (`test_ventana_sin_costo_deja_dias_descubiertos`): un
   producto cuya primera vigencia nace dentro de la ventana deja días sin
   costo. Es la prueba que habría atrapado el bug: falla contra el estado
   ANTERIOR a 0011 y pasa después.
2. **Los importes**, que son dinero y vienen del dueño (no se derivan de nada
   que el código pueda recalcular).
3. **La excepción declarada**: 0011 apaga el trigger append-only para UN
   DELETE y lo vuelve a prender en la misma transacción. Si una edición futura
   olvida el `ENABLE`, la tabla queda desprotegida en silencio — el candado
   estático y el vivo lo truenan.

Los tests vivos aplican 0001 + 0011 sobre una base temporal (mismo patrón que
`test_migracion_rechaza_en_vivo`), siembran el estado real medido en
producción y verifican el resultado.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_schema import (  # candados vivos: mismo DSN y misma condición de skip
    SQL as SQL_0001,
)
from tests.test_schema import (
    _postgres_obligatorio_ausente,
    _test_dsn,
)

pglast = pytest.importorskip("pglast")

ROOT = Path(__file__).resolve().parents[1]
SQL11 = (ROOT / "migrations" / "0011_costo_historico_peseta.sql").read_text(encoding="utf-8")
STMTS11 = tuple(pglast.parse_sql(SQL11))

TRIGGER = "sku_cost_solo_cierra_vigencia"

# Los dos SKU y sus costos, sellados por el dueño el 2026-09-02 ("correcto").
# Salen de sus hermanos de familia con historia desde el backfill 2026-02-20:
# gamuza negro plateado 325.00 (NH-GAM-NEG-MAX-PLA / -COR-PLA); nogal con
# ventana dorado 458.00 -> 459.29 el 2026-08-18 (NH-NOG-VEN-CEN-DOR).
PLA = "NH-GAM-NEG-PESETA-PLA"
DOR = "NH-NOG-VEN-PESETA-DOR"
COSTO_PLA = Decimal("325.0000")
COSTO_DOR_HISTORICO = Decimal("458.0000")
COSTO_DOR_VIGENTE = Decimal("459.2900")
DESDE_HISTORICO = dt.date(2026, 2, 20)
CORTE = dt.date(2026, 8, 18)
# Inicio de la ventana de 90 días maduros el día que se aplicó la migración.
VENTANA_DESDE = dt.date(2026, 5, 21)


# ---------------------------------------------------------------------------
# Estáticos: leen el SQL, no necesitan Postgres
# ---------------------------------------------------------------------------


def _sql_plano() -> str:
    return " ".join(SQL11.split())


def _tablas_mutadas() -> set[str]:
    """Tablas que 0011 escribe o altera, mirando el ÁRBOL COMPLETO.

    Recorrido recursivo a propósito: el INSERT de procedencia vive dentro de
    un CTE de `CREATE TEMP TABLE ... AS`, y un barrido de sólo el nivel
    superior no lo vería — un candado ciego a los CTE deja pasar justo la
    forma en que una migración de datos crece sin querer.
    """
    from pglast import ast
    from pglast.visitors import Visitor

    tocadas: set[str] = set()

    class _Walk(Visitor):
        def visit(self, ancestors, node):
            if isinstance(
                node, (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.AlterTableStmt)
            ):
                tocadas.add(node.relation.relname)

    for raw in STMTS11:
        _Walk()(raw)
    return tocadas


def test_0011_parsea():
    """El SQL es válido para Postgres 16 (mismo candado que 0005-0010)."""
    assert STMTS11, "0011 no produjo sentencias"


def test_0011_solo_toca_sku_cost_e_ingest_run():
    """Una corrección de datos que tocara otra tabla sería otra cosa.

    El repo nació de matar un monolito: una migración de datos que se expande
    a tablas vecinas es exactamente cómo empieza esa historia.
    """
    tocadas = _tablas_mutadas()
    assert tocadas <= {"sku_cost", "ingest_run"}, (
        f"0011 muta tablas fuera de su alcance: {sorted(tocadas - {'sku_cost', 'ingest_run'})}"
    )
    # Y sí muta las dos: un test que sólo mirara "⊆" pasaría con cero mutaciones.
    assert tocadas == {"sku_cost", "ingest_run"}, tocadas


def test_0011_no_crea_ni_altera_estructura():
    """Cero DDL de esquema: ni tablas, ni vistas, ni tipos, ni GRANTs."""
    from pglast import ast

    prohibidas = (
        ast.CreateStmt,
        ast.ViewStmt,
        ast.CreateEnumStmt,
        ast.GrantStmt,
        ast.IndexStmt,
        ast.CreateTrigStmt,
        ast.CreateFunctionStmt,
        ast.DropStmt,
    )
    ofensivas = [type(s.stmt).__name__ for s in STMTS11 if isinstance(s.stmt, prohibidas)]
    # CreateTableAsStmt del TEMP de procedencia es la única excepción: vive y
    # muere dentro de la transacción (ON COMMIT DROP).
    assert not ofensivas, f"0011 hace DDL de esquema: {ofensivas}"


def test_0011_reenciende_el_trigger_que_apaga():
    """La excepción es MOMENTÁNEA: apaga y prende el MISMO trigger, una vez.

    Éste es el candado que importa. Si una edición futura borra el ENABLE, el
    append-only de sku_cost queda apagado en producción y nadie se entera: el
    histórico de márgenes se vuelve reescribible en silencio.
    """
    plano = _sql_plano()
    apagados = plano.count(f"DISABLE TRIGGER {TRIGGER}")
    prendidos = plano.count(f"ENABLE TRIGGER {TRIGGER}")
    assert apagados == 1, f"0011 apaga el trigger {apagados} veces, se esperaba 1"
    assert prendidos == 1, f"0011 lo re-enciende {prendidos} veces, se esperaba 1"
    assert plano.index(f"DISABLE TRIGGER {TRIGGER}") < plano.index(f"ENABLE TRIGGER {TRIGGER}")
    # Y el DELETE — la razón entera de la excepción — vive ENTRE los dos.
    assert (
        plano.index(f"DISABLE TRIGGER {TRIGGER}")
        < plano.index("DELETE FROM sku_cost")
        < plano.index(f"ENABLE TRIGGER {TRIGGER}")
    ), "el DELETE quedó fuera de la ventana con el trigger apagado"


def test_0011_un_solo_delete_y_es_del_sku_de_plata():
    """El DELETE es la excepción; que no se cuele un segundo."""
    from pglast import ast

    deletes = [s.stmt for s in STMTS11 if isinstance(s.stmt, ast.DeleteStmt)]
    assert len(deletes) == 1, f"0011 tiene {len(deletes)} DELETE, se esperaba 1"
    assert deletes[0].relation.relname == "sku_cost"
    plano = _sql_plano()
    recorte = plano[plano.index("DELETE FROM sku_cost") :][:400]
    assert PLA in recorte, "el DELETE no está acotado al SKU de plata"
    assert "2026-08-18" in recorte, "el DELETE no está acotado a la vigencia del 2026-08-18"


def test_0011_importes_sellados_por_el_dueno():
    """Dinero: los tres importes son los aprobados, con escala de 4 decimales.

    Un test que sólo mirara "hay un INSERT" pasaría con cualquier número.
    """
    plano = _sql_plano()
    assert "458.0000, 'MXN'" in plano, "falta el costo histórico 458.0000 MXN del nogal oro"
    assert "325.0000, 'MXN'" in plano, "falta el costo 325.0000 MXN de la gamuza plata"
    assert "459.2900" in plano, "0011 debe verificar el costo vigente 459.2900 del nogal oro"
    assert "DATE '2026-02-20'" in plano, "falta la fecha de inicio del histórico"


def test_0011_deja_procedencia_propia():
    """Las filas nuevas no se disfrazan de ingesta de contabilidad."""
    assert "manual_costo_historico_0011" in SQL11


# ---------------------------------------------------------------------------
# Vivos: aplican 0001 + 0011 sobre una base temporal
# ---------------------------------------------------------------------------


def _base_temporal(psycopg, sufijo: str):
    """Crea una base con 0001 aplicada. Devuelve (admin, conn, nombre)."""
    from psycopg import sql

    dsn = _test_dsn()
    db = f"orbit_0011_{sufijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db)))
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
    conn = psycopg.connect(dsn, dbname=db, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute(SQL_0001)
    return admin, conn, db


def _tirar(psycopg, admin, conn, db):
    from psycopg import sql

    if conn is not None:
        conn.close()
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db)))
    admin.close()


def _sembrar_estado_previo(conn, *, costo_pla=COSTO_PLA, costo_dor=COSTO_DOR_VIGENTE):
    """El estado REAL de producción antes de 0011: una vigencia por SKU,
    abierta, nacida el 2026-08-18 (por eso los días previos no tienen costo)."""
    run = conn.execute(
        "INSERT INTO ingest_run (source, ok) VALUES ('siembra_test', TRUE) RETURNING id"
    ).fetchone()[0]
    for sku, costo in ((PLA, costo_pla), (DOR, costo_dor)):
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
            (sku, f"[test] {sku}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, valid_to, ingest_run_id) VALUES (%s, %s, 'MXN', FALSE, %s, NULL, %s)",
            (pid, costo, CORTE, run),
        )


def _dias_sin_costo(conn, sku: str, desde: dt.date, hasta: dt.date) -> int:
    """Días de [desde, hasta] en que ese SKU NO tiene costo vigente.

    Es la misma pregunta que hace `v_contribucion_entidad` (costo as-of cada
    metric_date); cualquier día sin respuesta deja al ad group en
    `catalogo_parcial`.
    """
    return conn.execute(
        """
        SELECT count(*) FROM generate_series(%s::date, %s::date, '1 day') d
         WHERE NOT EXISTS (
               SELECT 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
                WHERE p.odoo_sku = %s
                  AND d.d >= c.valid_from
                  AND (c.valid_to IS NULL OR d.d < c.valid_to))
        """,
        (desde, hasta, sku),
    ).fetchone()[0]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ventana_sin_costo_deja_dias_descubiertos():
    """LA prueba del bug: antes de 0011 faltan 90 días de costo; después, 0.

    Contra el estado anterior el assert de "cero días sin costo" falla para
    AMBOS SKU — es exactamente el agujero que dejó a los grupos Arras sin
    publicar contribución.
    """
    psycopg = pytest.importorskip("psycopg")
    admin, conn, db = _base_temporal(psycopg, "hueco")
    try:
        _sembrar_estado_previo(conn)

        # ANTES: los días de la ventana previos al 2026-08-18 no tienen costo.
        for sku in (PLA, DOR):
            faltan = _dias_sin_costo(conn, sku, VENTANA_DESDE, CORTE)
            assert faltan == 89, (
                f"{sku}: se esperaban 89 días sin costo antes de 0011, hay {faltan}"
            )

        conn.execute(SQL11)

        # DESPUÉS: cobertura completa de la ventana, para los dos.
        for sku in (PLA, DOR):
            faltan = _dias_sin_costo(conn, sku, VENTANA_DESDE, CORTE)
            assert faltan == 0, f"{sku}: quedan {faltan} días sin costo después de 0011"
    finally:
        _tirar(psycopg, admin, conn, db)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_0011_deja_las_vigencias_exactas():
    """Los importes y los cortes quedan tal cual los selló el dueño."""
    psycopg = pytest.importorskip("psycopg")
    admin, conn, db = _base_temporal(psycopg, "vigencias")
    try:
        _sembrar_estado_previo(conn)
        conn.execute(SQL11)

        pla = conn.execute(
            "SELECT c.cost_amount, c.cost_currency, c.includes_tax, c.valid_from, c.valid_to"
            "  FROM sku_cost c JOIN product p ON p.id = c.product_id"
            " WHERE p.odoo_sku = %s ORDER BY c.valid_from",
            (PLA,),
        ).fetchall()
        assert pla == [(COSTO_PLA, "MXN", False, DESDE_HISTORICO, None)], pla

        dor = conn.execute(
            "SELECT c.cost_amount, c.cost_currency, c.includes_tax, c.valid_from, c.valid_to"
            "  FROM sku_cost c JOIN product p ON p.id = c.product_id"
            " WHERE p.odoo_sku = %s ORDER BY c.valid_from",
            (DOR,),
        ).fetchall()
        assert dor == [
            (COSTO_DOR_HISTORICO, "MXN", False, DESDE_HISTORICO, CORTE),
            (COSTO_DOR_VIGENTE, "MXN", False, CORTE, None),
        ], dor
    finally:
        _tirar(psycopg, admin, conn, db)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_0011_devuelve_el_append_only_armado():
    """Tras la excepción, sku_cost vuelve a rechazar UPDATE y DELETE.

    No basta con leer `pg_trigger`: se prueba el comportamiento REAL, que es
    lo que protege el histórico de márgenes.
    """
    psycopg = pytest.importorskip("psycopg")
    admin, conn, db = _base_temporal(psycopg, "armado")
    try:
        _sembrar_estado_previo(conn)
        conn.execute(SQL11)

        # El trigger alza con ERRCODE = 'restrict_violation' (0001), NO la
        # P0001 generica: afirmar la clase exacta es lo que distingue "el
        # candado disparo" de "algo fallo".
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "DELETE FROM sku_cost c USING product p"
                " WHERE p.id = c.product_id AND p.odoo_sku = %s",
                (PLA,),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE sku_cost c SET cost_amount = 1 FROM product p"
                " WHERE p.id = c.product_id AND p.odoo_sku = %s",
                (PLA,),
            )
    finally:
        _tirar(psycopg, admin, conn, db)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_0011_aborta_si_el_estado_no_es_el_esperado():
    """Fail-closed: contra un costo distinto al medido, 0011 REVIENTA.

    Sin esta guarda, aplicar la migración a una base que ya cambió sembraría
    historia falsa encima de dinero real.
    """
    psycopg = pytest.importorskip("psycopg")
    admin, conn, db = _base_temporal(psycopg, "guarda")
    try:
        _sembrar_estado_previo(conn, costo_pla=Decimal("999.0000"))
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(SQL11)
        # 0011 trae su propio BEGIN/COMMIT: al abortar deja la conexión en
        # transacción fallida aun con autocommit. El ROLLBACK explícito es
        # parte de la prueba: demuestra que NADA de la migración sobrevivió.
        conn.rollback()
        # Y no dejó nada a medias: sigue habiendo UNA vigencia por SKU.
        filas = conn.execute(
            "SELECT count(*) FROM sku_cost c JOIN product p ON p.id = c.product_id"
            " WHERE p.odoo_sku IN (%s, %s)",
            (PLA, DOR),
        ).fetchone()[0]
        assert filas == 2, f"0011 escribió a medias: {filas} vigencias"
    finally:
        _tirar(psycopg, admin, conn, db)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_0011_no_es_re_runnable():
    """La segunda corrida aborta en la guarda, no duplica historia."""
    psycopg = pytest.importorskip("psycopg")
    admin, conn, db = _base_temporal(psycopg, "rerun")
    try:
        _sembrar_estado_previo(conn)
        conn.execute(SQL11)
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(SQL11)
        conn.rollback()  # ver test_0011_aborta_si_el_estado_no_es_el_esperado
        filas = conn.execute(
            "SELECT count(*) FROM sku_cost c JOIN product p ON p.id = c.product_id"
            " WHERE p.odoo_sku IN (%s, %s)",
            (PLA, DOR),
        ).fetchone()[0]
        assert filas == 3, f"tras el segundo intento hay {filas} vigencias, se esperaban 3"
    finally:
        _tirar(psycopg, admin, conn, db)
