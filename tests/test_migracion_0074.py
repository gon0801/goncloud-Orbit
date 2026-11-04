"""Migracion 0074: ledger del bloqueo del dueno y su reversa.

La prueba 9 usa logins reales (CREATE ROLE LOGIN + GRANT del grupo), no
SET ROLE. Los nombres llevan el pid: en produccion esos grupos son
orbit_read, orbit_admin, orbit_decide y orbit_ingest.
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
from test_jev_perimetro import ORDEN_PERIMETRO
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
REVERSA_0074 = MIGRACIONES / "0074_reversa_jev_ads03_bloqueo.sql"
REVERSA_0052 = MIGRACIONES / "0052_reversa_jev_senales.sql"

VID = "11111111-1111-4111-8111-111111111111"
EID = "22222222-2222-4222-8222-222222222222"
QID = "33333333-3333-4333-8333-333333333333"
_EID_PASO = {
    "aplicado": "44444444-4444-4444-8444-444444444441",
    "reversa_intencion": "44444444-4444-4444-8444-444444444442",
    "revertido": "44444444-4444-4444-8444-444444444443",
}

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


@contextmanager
def db_bloqueo(exige_aprobado: bool = False):
    from psycopg import sql as pgsql

    dsn = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
    partes = urlsplit(dsn)
    db = f"b74_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(
            urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment)),
            autocommit=True,
        )
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_PERIMETRO:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        if not exige_aprobado:
            # Las pruebas de CHECK cuelgan pasos de una reanudacion por comodidad.
            conn.execute("ALTER TABLE bloqueo_efecto DISABLE TRIGGER bloqueo_efecto_exige_aprobado")
            conn.execute(
                "ALTER TABLE bloqueo_veredicto"
                " DISABLE TRIGGER bloqueo_veredicto_quitado_exige_aprobado"
            )
        yield conn, db, dsn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _reanudacion(conn, vid: str = VID) -> str:
    conn.execute(
        "INSERT INTO bloqueo_veredicto (id, plataforma, tipo)"
        " VALUES (%s, 'amazon_mx', 'reanudacion')",
        (vid,),
    )
    return vid


def _efecto(conn, paso: str, *, eid: str = EID, vid: str = VID, **campos) -> None:
    columnas = ["id", "veredicto_id", "paso", *campos]
    valores = [eid, vid, paso, *campos.values()]
    marcas = ", ".join(["%s"] * len(valores))
    nombres = ", ".join(columnas)
    conn.execute(
        f"INSERT INTO bloqueo_efecto ({nombres}) VALUES ({marcas})",
        valores,
    )


def _login(conn, rol: str, grupo: str) -> None:
    try:
        conn.execute(f"CREATE ROLE {rol} LOGIN PASSWORD 'clave' NOSUPERUSER")
    except psycopg.errors.InsufficientPrivilege:
        pytest.fail(
            "ORBIT_TEST_DSN sin CREATEROLE: la prueba 9 es la evidencia del"
            " perimetro y no puede saltarse en silencio"
        )
    conn.execute(f"GRANT {grupo} TO {rol}")


@_skip_db
def test_check_rechaza_paso_sin_negative_keyword_id():
    with db_bloqueo() as (conn, _, _):
        _reanudacion(conn)
        for paso in ("aplicado", "reversa_intencion", "revertido"):
            with pytest.raises(
                psycopg.errors.CheckViolation, match="bloqueo_efecto_paso_exige_negative"
            ):
                _efecto(conn, paso, eid=_EID_PASO[paso])
        _efecto(conn, "intencion")
        leido = conn.execute(
            "SELECT paso, negative_keyword_id FROM bloqueo_efecto WHERE id = %s",
            (EID,),
        ).fetchone()
        assert leido == ("intencion", None)
        _efecto(
            conn,
            "aplicado",
            eid="55555555-5555-4555-8555-555555555555",
            negative_keyword_id="nk-1",
        )
        assert conn.execute(
            "SELECT negative_keyword_id FROM bloqueo_efecto WHERE id = %s",
            ("55555555-5555-4555-8555-555555555555",),
        ).fetchone() == ("nk-1",)


@_skip_db
def test_check_rechaza_paso_sin_motivo():
    with db_bloqueo() as (conn, _, _):
        _reanudacion(conn)
        for paso in ("fallido", "anulado", "reversa_fallida"):
            with pytest.raises(
                psycopg.errors.CheckViolation, match="bloqueo_efecto_paso_exige_motivo"
            ):
                _efecto(conn, paso)
        _efecto(conn, "fallido", motivo="readback")
        assert conn.execute(
            "SELECT motivo FROM bloqueo_efecto WHERE id = %s",
            (EID,),
        ).fetchone() == ("readback",)


@_skip_db
def test_check_rechaza_quitado_sin_origen_id():
    with db_bloqueo() as (conn, _, _):
        with pytest.raises(
            psycopg.errors.CheckViolation, match="bloqueo_veredicto_quitado_cita_origen"
        ):
            conn.execute(
                "INSERT INTO bloqueo_veredicto (id, plataforma, tipo)"
                " VALUES (%s, 'amazon_mx', 'quitado')",
                (QID,),
            )
        origen = _reanudacion(conn)
        conn.execute(
            "INSERT INTO bloqueo_veredicto (id, plataforma, tipo, origen_id)"
            " VALUES (%s, 'amazon_mx', 'quitado', %s)",
            (QID, origen),
        )
        assert conn.execute(
            "SELECT origen_id::text FROM bloqueo_veredicto WHERE id = %s",
            (QID,),
        ).fetchone() == (origen,)


@_skip_db
def test_check_tipo_reanudacion_y_decision():
    with db_bloqueo() as (conn, _, _):
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="bloqueo_veredicto_reanudacion_solo_plataforma",
        ):
            conn.execute(
                "INSERT INTO bloqueo_veredicto (id, plataforma, tipo, termino)"
                " VALUES (%s, 'amazon_mx', 'reanudacion', 'x')",
                (VID,),
            )
        with pytest.raises(
            psycopg.errors.CheckViolation, match="bloqueo_veredicto_decision_exige_clave"
        ):
            conn.execute(
                "INSERT INTO bloqueo_veredicto (id, plataforma, tipo)"
                " VALUES (%s, 'amazon_us', 'aprobado')",
                (QID,),
            )
        _reanudacion(conn)
        assert conn.execute(
            "SELECT plataforma::text, tipo FROM bloqueo_veredicto WHERE id = %s",
            (VID,),
        ).fetchone() == ("amazon_mx", "reanudacion")


@_skip_db
def test_fk_senal_rechaza_id_inexistente():
    with db_bloqueo() as (conn, _, _):
        (grupo,) = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'ad_group', 'g-fk') RETURNING id"
        ).fetchone()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO bloqueo_veredicto"
                " (id, plataforma, tipo, ad_group_id, termino, senal_id, huella_catalogo)"
                " VALUES (%s, 'amazon_mx', 'rechazado', %s, 'termino', %s, 'huella')",
                (VID, grupo, "99999999-9999-4999-8999-999999999999"),
            )
        assert conn.execute("SELECT count(*) FROM bloqueo_veredicto").fetchone() == (0,)


@_skip_db
def test_append_only_rechaza_update_delete_y_truncate():
    with db_bloqueo() as (conn, _, _):
        _reanudacion(conn)
        _efecto(conn, "intencion")
        with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
            conn.execute(
                "UPDATE bloqueo_veredicto SET plataforma = 'amazon_us' WHERE id = %s",
                (VID,),
            )
        with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
            conn.execute("DELETE FROM bloqueo_efecto WHERE id = %s", (EID,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
            conn.execute("UPDATE bloqueo_efecto SET intento = 2 WHERE id = %s", (EID,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
            conn.execute("TRUNCATE bloqueo_efecto")
        assert conn.execute("SELECT count(*) FROM bloqueo_efecto").fetchone() == (1,)


@_skip_db
def test_reversa_0074_antes_de_0052():
    with db_bloqueo() as (conn, _, _):
        with pytest.raises(psycopg.errors.DependentObjectsStillExist):
            conn.execute(REVERSA_0052.read_text(encoding="utf-8"))
        conn.rollback()
        assert conn.execute("SELECT to_regclass('public.jev_senal')").fetchone()[0] is not None
        assert (
            conn.execute("SELECT to_regclass('public.bloqueo_veredicto')").fetchone()[0] is not None
        )
        conn.execute(REVERSA_0074.read_text(encoding="utf-8"))
        assert conn.execute("SELECT to_regclass('public.bloqueo_veredicto')").fetchone()[0] is None
        assert conn.execute("SELECT to_regclass('public.bloqueo_efecto')").fetchone()[0] is None
        conn.execute(REVERSA_0052.read_text(encoding="utf-8"))
        assert conn.execute("SELECT to_regclass('public.jev_senal')").fetchone()[0] is None


@_skip_db
def test_reversa_aborta_si_hay_filas():
    with db_bloqueo() as (conn, _, _):
        _reanudacion(conn)
        with pytest.raises(psycopg.errors.RaiseException, match="reversa 0074"):
            conn.execute(REVERSA_0074.read_text(encoding="utf-8"))
        conn.rollback()
        assert conn.execute(
            "SELECT count(*) FROM bloqueo_veredicto WHERE id = %s",
            (VID,),
        ).fetchone() == (1,)


@_skip_db
def test_prueba_9_logins_reales():
    from psycopg.conninfo import make_conninfo

    with db_bloqueo() as (conn, db, dsn):
        roles = {
            "app_read": f"b74_{os.getpid()}_read",
            "app_admin": f"b74_{os.getpid()}_admin",
            "app_decide": f"b74_{os.getpid()}_decide",
            "app_ingest": f"b74_{os.getpid()}_ingest",
        }
        creados: list[str] = []
        try:
            for grupo, rol in roles.items():
                _login(conn, rol, grupo)
                creados.append(rol)
            dsn_read = make_conninfo(dsn, dbname=db, user=roles["app_read"], password="clave")
            dsn_admin = make_conninfo(dsn, dbname=db, user=roles["app_admin"], password="clave")
            dsn_decide = make_conninfo(dsn, dbname=db, user=roles["app_decide"], password="clave")
            dsn_ingest = make_conninfo(dsn, dbname=db, user=roles["app_ingest"], password="clave")
            with psycopg.connect(dsn_admin, autocommit=True) as admin:
                admin.execute(
                    "INSERT INTO bloqueo_veredicto (id, plataforma, tipo)"
                    " VALUES (%s, 'amazon_mx', 'reanudacion')",
                    (VID,),
                )
                admin.execute(
                    "INSERT INTO bloqueo_efecto (id, veredicto_id, paso)"
                    " VALUES (%s, %s, 'intencion')",
                    (EID, VID),
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    admin.execute(
                        "UPDATE bloqueo_veredicto SET plataforma = 'amazon_us' WHERE id = %s",
                        (VID,),
                    )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    admin.execute("DELETE FROM bloqueo_veredicto WHERE id = %s", (VID,))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    admin.execute(
                        "UPDATE bloqueo_efecto SET intento = 2 WHERE id = %s",
                        (EID,),
                    )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    admin.execute("DELETE FROM bloqueo_efecto WHERE id = %s", (EID,))
            with psycopg.connect(dsn_read, autocommit=True) as lectura:
                assert lectura.execute("SELECT count(*) FROM bloqueo_veredicto").fetchone() == (1,)
                assert lectura.execute("SELECT count(*) FROM bloqueo_efecto").fetchone() == (1,)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    lectura.execute(
                        "INSERT INTO bloqueo_veredicto (id, plataforma, tipo)"
                        " VALUES (%s, 'amazon_us', 'reanudacion')",
                        (QID,),
                    )
            for dsn_ciclo in (dsn_decide, dsn_ingest):
                with psycopg.connect(dsn_ciclo, autocommit=True) as ciclo:
                    for tabla in ("bloqueo_veredicto", "bloqueo_efecto"):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            ciclo.execute(f"SELECT count(*) FROM {tabla}")
        finally:
            conn.rollback()
            for rol in creados:
                conn.execute(f"DROP OWNED BY {rol}")
                conn.execute(f"DROP ROLE IF EXISTS {rol}")


@_skip_db
def test_trigger_efecto_y_quitado_exigen_aprobado():
    with db_bloqueo(exige_aprobado=True) as (conn, _, _):
        _reanudacion(conn)
        with pytest.raises(psycopg.errors.CheckViolation, match="exige un veredicto aprobado"):
            _efecto(conn, "intencion")
        with pytest.raises(psycopg.errors.CheckViolation, match="exige un veredicto aprobado"):
            conn.execute(
                "INSERT INTO bloqueo_veredicto (id, plataforma, tipo, origen_id)"
                " VALUES (%s, 'amazon_mx', 'quitado', %s)",
                (QID, VID),
            )
        assert conn.execute("SELECT count(*) FROM bloqueo_efecto").fetchone() == (0,)
