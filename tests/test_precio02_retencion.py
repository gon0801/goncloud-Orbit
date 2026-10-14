"""Migracion 0059 de REPRICING 02 (R03k): `precio_retencion_coherente` reparado.

Aplica 0001..0054 + 0059 y ejercita el cuerpo del trigger por los caminos
feliz y de rechazo. El positivo falla contra 0054 sin 0059.
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_precio02_base import ORDEN02
from test_schema import _postgres_obligatorio_ausente

RAIZ = Path(__file__).resolve().parents[1]
DATOS_DISENO = RAIZ / "docs/evidencia/repricing-02/diseno/datos.sql"

ORDEN59 = tuple(ORDEN02) + ("0059_precio02_repara_retencion_coherente.sql",)
REVERSA59 = "0059_reversa_precio02_repara_retencion_coherente.sql"

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


def _bloque_0058() -> str:
    """El bloque comentado `0058_precio02_decision_exige.sql` de datos.sql,
    des-comentado: los dos CHECK NOT VALID que S.1 despliega en D.2. Fuente
    unica: si el diseno cambia de forma, los asserts avisan en vez de pasar
    en vacio."""
    lineas = DATOS_DISENO.read_text(encoding="utf-8").splitlines()
    inicio = next(
        i
        for i, linea in enumerate(lineas)
        if linea.startswith("-- 0058_precio02_decision_exige.sql")
    )
    begin = next(i for i in range(inicio, len(lineas)) if lineas[i] == "-- BEGIN;")
    commit = next(i for i in range(begin, len(lineas)) if lineas[i] == "-- COMMIT;")
    bloque = "\n".join(
        linea[3:] if linea.startswith("-- ") else "" for linea in lineas[begin : commit + 1]
    )
    assert "precio_decision_l_exige_origen" in bloque
    assert "precio_decision_mover_exige_verificacion" in bloque
    return bloque


@contextmanager
def _db(*, sql_previo_0059: tuple[str, ...] = ()):
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio59_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN59[:-1]:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        for sql in sql_previo_0059:
            conn.execute(sql)
        conn.execute((RAIZ / "migrations" / ORDEN59[-1]).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _config(conn) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('zz-test-0059',"
        " '{}'::jsonb) RETURNING id"
    ).fetchone()[0]


def _listing(conn, cfg: int, sku: str = "zz-SKU-0059") -> int:
    prod = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('zz-test-0059', 'Test 0059') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', 'zz-ASIN-0059', %s) RETURNING id",
        (prod, sku),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por, go_literal) VALUES (%s, 'amazon_mx', 0.30,"
        " 'live', '2026-09-01', 'candado', 'go test 0059')",
        (lid,),
    )
    return lid


def _decision_subir(
    conn,
    lid: int,
    *,
    canal: str | None = None,
    l_origen: str | None = None,
    verificacion: str | None = None,
) -> int:
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
        " goal, m_actual, canal, p_actual, p_actual_currency, p_objetivo,"
        " p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor,"
        " i_currency, c_valor, c_currency, f_valor, f_currency, l_valor,"
        " l_currency, r_valor, r_currency, l_origen, verificacion)"
        " VALUES (%s, 'amazon_mx', 'subir', NULL, 'live', 0.30, 0.25, %s, 100.00,"
        " 'MXN', 110.00, 'MXN', 110.00, 'MXN', 10.00, 'MXN', 20.00, 'MXN',"
        " 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN', %s, %s) RETURNING id",
        (lid, canal, l_origen, verificacion),
    ).fetchone()[0]


def _corrida(conn, cfg: int, platform: str = "amazon_mx") -> int:
    return conn.execute(
        "INSERT INTO precio_corrida (platform, owner, config_version_id, modo_global)"
        " VALUES (%s, 'test-0059', %s, 'shadow') RETURNING id",
        (platform, cfg),
    ).fetchone()[0]


@_skip_sin_pg
def test_retencion_apagador_entra():
    """El INSERT positivo ejecuta el cuerpo reparado: la retencion queda."""
    with _db() as conn:
        cfg = _config(conn)
        did = _decision_subir(conn, _listing(conn, cfg))
        corrida = _corrida(conn, cfg)
        rid = conn.execute(
            "INSERT INTO precio_retencion (decision_id, corrida_id, causa)"
            " VALUES (%s, %s, 'apagador') RETURNING id",
            (did, corrida),
        ).fetchone()[0]
        fila = conn.execute(
            "SELECT decision_id, corrida_id, causa FROM precio_retencion WHERE id = %s", (rid,)
        ).fetchone()
        assert tuple(fila) == (did, corrida, "apagador")


@_skip_sin_pg
def test_retencion_cruzada_otra_plataforma_revienta():
    """Decision y corrida de distinta plataforma: check_violation, no silencio."""
    with _db() as conn:
        cfg = _config(conn)
        did = _decision_subir(conn, _listing(conn, cfg))
        otra = _corrida(conn, cfg, "amazon_us")
        with pytest.raises(psycopg.errors.CheckViolation, match="misma plataforma"):
            conn.execute(
                "INSERT INTO precio_retencion (decision_id, corrida_id, causa)"
                " VALUES (%s, %s, 'apagador')",
                (did, otra),
            )


@_skip_sin_pg
def test_retencion_fusible_sin_medicion_revienta():
    """`movimiento_masivo` sin su medicion: check_violation."""
    with _db() as conn:
        cfg = _config(conn)
        did = _decision_subir(conn, _listing(conn, cfg), canal="fba")
        corrida = _corrida(conn, cfg)
        with pytest.raises(psycopg.errors.CheckViolation, match="medicion"):
            conn.execute(
                "INSERT INTO precio_retencion (decision_id, corrida_id, causa)"
                " VALUES (%s, %s, 'movimiento_masivo')",
                (did, corrida),
            )


@_skip_sin_pg
def test_retencion_fusible_con_medicion_entra():
    """`movimiento_masivo` CON su medicion del mismo canal: entra."""
    with _db() as conn:
        cfg = _config(conn)
        did = _decision_subir(conn, _listing(conn, cfg), canal="fba")
        corrida = _corrida(conn, cfg)
        conn.execute(
            "INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas,"
            " causa, detalle, avisada_at) VALUES (%s, 'fba', 10, 6,"
            " 'movimiento_masivo', 'test-0059', now())",
            (corrida,),
        )
        rid = conn.execute(
            "INSERT INTO precio_retencion (decision_id, corrida_id, causa)"
            " VALUES (%s, %s, 'movimiento_masivo') RETURNING id",
            (did, corrida),
        ).fetchone()[0]
        assert rid is not None


@_skip_sin_pg
def test_0059_aplica_limpio_tras_la_0058():
    """B1: con los CHECKs de la 0058 (S.1, D.2) ya puestos, la 0059 aplica
    limpio — su DO siembra decisiones con l_origen/verificacion — y el
    positivo entra. Sin el arreglo, aplicar aborta con
    precio_decision_l_exige_origen."""
    with _db(sql_previo_0059=(_bloque_0058(),)) as conn:
        cfg = _config(conn)
        did = _decision_subir(
            conn, _listing(conn, cfg), l_origen="politica", verificacion="cotizada"
        )
        corrida = _corrida(conn, cfg)
        rid = conn.execute(
            "INSERT INTO precio_retencion (decision_id, corrida_id, causa)"
            " VALUES (%s, %s, 'apagador') RETURNING id",
            (did, corrida),
        ).fetchone()[0]
        fila = conn.execute(
            "SELECT decision_id, corrida_id, causa FROM precio_retencion WHERE id = %s", (rid,)
        ).fetchone()
        assert tuple(fila) == (did, corrida, "apagador")


@_skip_sin_pg
def test_reversa_restaura_cuerpo_roto_0054():
    """Tras la reversa vuelve el cuerpo roto: marcador viejo presente y el
    INSERT positivo falla como antes (si alguien arregla la reversa, caza)."""
    with _db() as conn:
        conn.execute((RAIZ / "migrations" / REVERSA59).read_text(encoding="utf-8"))
        cuerpo = conn.execute(
            "SELECT pg_get_functiondef('precio_retencion_coherente()'::regprocedure)"
        ).fetchone()[0]
        assert "SELECT c.platform, c.fecha FROM precio_corrida" in cuerpo
        assert "v_corr_platform" not in cuerpo
        cfg = _config(conn)
        did = _decision_subir(conn, _listing(conn, cfg))
        corrida = _corrida(conn, cfg)
        with pytest.raises(psycopg.errors.SyntaxError, match="only one column"):
            conn.execute(
                "INSERT INTO precio_retencion (decision_id, corrida_id, causa)"
                " VALUES (%s, %s, 'apagador')",
                (did, corrida),
            )
