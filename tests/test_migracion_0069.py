"""Migracion 0069 (BIDS 02 R09-r4): tabla de intenciones de retiro.

anuncio_retiro_intencion: intencion escrita ANTES del PUT (cerrada_el
NULL = pendiente). Solo-cierre (NULL -> valor, sin tocar lo demas),
sin DELETE, una pendiente por anuncio."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import _entidad, _producto, db_fabrica
from test_migracion_0065 import SQL64, SQL65
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL66 = (MIGRACIONES / "0066_bids02_retiro_anuncios.sql").read_text(encoding="utf-8")
SQL68 = (MIGRACIONES / "0068_bids02_sellos.sql").read_text(encoding="utf-8")
SQL69 = (MIGRACIONES / "0069_bids02_retiro_intencion.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _anuncio(conn, lid, external="ad-69"):
    camp = _entidad(conn, "amazon_mx", "campaign", f"c-{external}")
    ag = _entidad(conn, "amazon_mx", "ad_group", f"ag-{external}", parent=camp)
    return _entidad(conn, "amazon_mx", "product_ad", external, parent=ag, listing_id=lid)


def _intencion(conn, ent, external="ad-69"):
    return conn.execute(
        "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
        " 'retiro-69', NULL, %s, %s, 'ag-69', 'c-69', 'SS-69', 'B069000001',"
        " 'state=ENABLED', 'dueno', now()) RETURNING id",
        (ent, external),
    ).fetchone()[0]


@_skip_db
def test_0069_una_pendiente_por_anuncio():
    """0069: dos intenciones pendientes del mismo anuncio se rechazan;
    pendiente + cerrada conviven."""
    with db_fabrica("orbit_m69") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        iid = _intencion(conn, ent)
        with pytest.raises(psycopg.errors.UniqueViolation, match="una_pendiente"):
            _intencion(conn, ent)
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'x' WHERE id = %s",
            (iid,),
        )
        _intencion(conn, ent)


@_skip_db
def test_0069_cierra_y_lo_demas_es_historia():
    """0069: cerrar una pendiente (NULL -> now()) pasa; tocar otro
    campo, re-cerrar o DELETE se rechaza."""
    with db_fabrica("orbit_m69b") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        iid = _intencion(conn, ent)
        assert (
            conn.execute(
                "SELECT cerrada_el FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
            ).fetchone()[0]
            is None
        )
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'sello'"
            " WHERE id = %s",
            (iid,),
        )
        assert (
            conn.execute(
                "SELECT cerrada_el IS NOT NULL FROM anuncio_retiro_intencion WHERE id = %s",
                (iid,),
            ).fetchone()[0]
            is True
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="SOLO-CIERRE"):
            conn.execute(
                "UPDATE anuncio_retiro_intencion SET modo = 'archivo' WHERE id = %s", (iid,)
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="SOLO-CIERRE"):
            conn.execute(
                "UPDATE anuncio_retiro_intencion SET cerrada_el = now() WHERE id = %s", (iid,)
            )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM anuncio_retiro_intencion WHERE id = %s", (iid,))


REV69 = (MIGRACIONES / "0069_reversa_bids02_retiro_intencion.sql").read_text(encoding="utf-8")

# Sin "id": es GENERATED ALWAYS y Postgres lo rechaza antes que al
# trigger (M03 es indistinguible: equivalente-en-efecto, se nombra).
_COLUMNAS_SOLO_CIERRE = {
    "platform": "'amazon_us'",
    "motivo": "'subfamilia'",
    "modo": "'archivo'",
    "lote_retiro": "'otro'",
    "impulso_id": "999999",
    "ad_entity_id": "999999",
    "ad_id_externo": "'otro-ad'",
    "ad_group_externo": "'otro-ag'",
    "campana_externa": "'otra-c'",
    "sku": "'OTRO'",
    "asin": "'B0OTRO0000'",
    "reversa": "'otra'",
    "actor": "'otro'",
    "creada_el": "now()",
}


@_skip_db
def test_r09r5_m03_m18_solo_cierre_columna_por_columna():
    """R09-r5 B6 (M04-M18): tocar CUALQUIER columna protegida al
    cerrar se rechaza; tocar detalle sin cerrar tambien. M03 (id) no
    se prueba: GENERATED ALWAYS lo rechaza antes que al trigger."""
    with db_fabrica("orbit_m69c") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        iid = _intencion(conn, ent)
        for columna, valor in _COLUMNAS_SOLO_CIERRE.items():
            with pytest.raises(psycopg.errors.CheckViolation, match="SOLO-CIERRE"):
                conn.execute(
                    f"UPDATE anuncio_retiro_intencion SET {columna} = {valor},"
                    f" cerrada_el = now() WHERE id = %s",
                    (iid,),
                )
        with pytest.raises(psycopg.errors.CheckViolation, match="SOLO-CIERRE"):
            conn.execute(
                "UPDATE anuncio_retiro_intencion SET detalle = 'sin cerrar' WHERE id = %s",
                (iid,),
            )
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'ok' WHERE id = %s",
            (iid,),
        )


@_skip_db
def test_r09r5_m21_m23_vocabulario_y_coherencia():
    """R09-r5 B6 (M21-M23): motivo/modo fuera del vocabulario y
    motivo/impulso_id incoherentes se rechazan."""
    with db_fabrica("orbit_m69d") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)

        def _mete(motivo, modo, impulso):
            conn.execute(
                "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
                " impulso_id, ad_entity_id, ad_id_externo, ad_group_externo, campana_externa,"
                " sku, asin, reversa, actor, creada_el) VALUES ('amazon_mx', %s, %s,"
                " 'retiro-x', %s, %s, 'ad-x', 'ag-x', 'c-x', 'SS-X', 'B0X', 'r', 'd', now())",
                (motivo, modo, impulso, ent),
            )

        with pytest.raises(psycopg.errors.CheckViolation, match="intencion_motivo"):
            _mete("otro", "pausa", None)
        with pytest.raises(psycopg.errors.CheckViolation, match="intencion_modo"):
            _mete("sin_pedidos", "otro", None)
        with pytest.raises(psycopg.errors.CheckViolation, match="intencion_impulso_coherente"):
            _mete("impulso", "pausa", None)
        with pytest.raises(psycopg.errors.CheckViolation, match="intencion_impulso_coherente"):
            _mete("sin_pedidos", "pausa", 999999)


@_skip_db
def test_r09r5_m24_m26_m32_permisos_minimos():
    """R09-r5 B6 (M24-M26, M32): app_admin actualiza solo 3 columnas,
    sin TRUNCATE ni DELETE; app_jev no lee."""
    with db_fabrica("orbit_m69e") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        conn.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_jev')"
            " THEN CREATE ROLE app_jev NOLOGIN; END IF; END; $$"
        )
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.column_privileges"
                " WHERE table_name = 'anuncio_retiro_intencion' AND grantee = 'app_admin'"
                " AND privilege_type = 'UPDATE'"
            ).fetchall()
        }
        assert cols == {"cerrada_el", "detalle", "retiro_id"}
        tabla = {
            r[0]
            for r in conn.execute(
                "SELECT privilege_type FROM information_schema.role_table_grants"
                " WHERE table_name = 'anuncio_retiro_intencion' AND grantee = 'app_admin'"
            ).fetchall()
        }
        assert "UPDATE" not in tabla
        assert "TRUNCATE" not in tabla
        assert "DELETE" not in tabla
        assert (
            conn.execute(
                "SELECT has_table_privilege('app_jev', 'public.anuncio_retiro_intencion', 'SELECT')"
            ).fetchone()[0]
            is False
        )


@_skip_db
def test_r09r5_m27_m31_llaves_y_obligatorios():
    """R09-r5 B6 (M27-M31): retiro/ad/impulso sin fila se rechazan;
    reversa y actor son obligatorios."""
    with db_fabrica("orbit_m69f") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)

        def _mete(ent_id=ent, retiro=None, impulso=None, reversa="r", actor="d"):
            conn.execute(
                "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
                " impulso_id, ad_entity_id, ad_id_externo, ad_group_externo, campana_externa,"
                " sku, asin, reversa, actor, creada_el, retiro_id) VALUES ('amazon_mx',"
                " 'sin_pedidos', 'pausa', 'retiro-x', %s, %s, 'ad-x', 'ag-x', 'c-x', 'SS-X',"
                " 'B0X', %s, %s, now(), %s)",
                (impulso, ent_id, reversa, actor, retiro),
            )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _mete(retiro=999999)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _mete(ent_id=999999)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
                " impulso_id, ad_entity_id, ad_id_externo, ad_group_externo, campana_externa,"
                " sku, asin, reversa, actor, creada_el) VALUES ('amazon_mx', 'impulso',"
                " 'pausa', 'retiro-x', 999999, %s, 'ad-x', 'ag-x', 'c-x', 'SS-X', 'B0X',"
                " 'r', 'd', now())",
                (ent,),
            )
        with pytest.raises(psycopg.errors.NotNullViolation):
            _mete(reversa=None)
        with pytest.raises(psycopg.errors.NotNullViolation):
            _mete(actor=None)


@_skip_db
def test_r09r5_v03_reversa_limpia_funcion_y_tabla():
    """R09-r5 B6 (V03): la reversa quita la tabla, los triggers y la
    funcion (nada huerfano)."""
    with db_fabrica("orbit_m69g") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        conn.execute(REV69)
        assert (
            conn.execute("SELECT to_regclass('public.anuncio_retiro_intencion')").fetchone()[0]
            is None
        )
        assert (
            conn.execute(
                "SELECT to_regprocedure('anuncio_retiro_intencion_0069_solo_cierre()')"
            ).fetchone()[0]
            is None
        )
