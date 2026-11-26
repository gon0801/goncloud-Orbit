"""ENVIOS 01: esquema 0102, su reversa y el escritor unico `app.envios.store`."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pglast
import psycopg
import pytest
from helpers.pg import db_desde_template
from psycopg.rows import dict_row
from test_envios_estado import envio_de_prueba
from test_fabrica_f2 import _skip_db
from test_schema import _test_dsn

from app.envios.modelo import Articulo, PaqueteObservado
from app.envios.store import guardar

ROOT = Path(__file__).resolve().parent.parent
IDA = ROOT / "migrations" / "0102_envios01_seguimiento.sql"
REVERSA = ROOT / "migrations" / "0102_reversa_envios01_seguimiento.sql"
TABLAS = ("envio_observation", "envio_paquete_observation")

T0 = datetime(2026, 11, 25, 2, 25, tzinfo=UTC)
T1 = T0 + timedelta(hours=4)
T2 = T0 + timedelta(hours=8)
CREADO = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)

PAQUETE = PaqueteObservado(
    paquete_ref="PKG-1",
    transportista="AMZMX",
    servicio="Standard",
    guia="GUIA-1",
    despachado_el=datetime(2026, 11, 21, 9, 0, tzinfo=UTC),
    estado_fuente="IN_TRANSIT",
    detalle_fuente="PICKED_UP",
)
ENVIO_A = envio_de_prueba(
    envio_ref="701-1",
    pedidos=("701-1",),
    estado_fuente="SHIPPED",
    pedido_creado_el=CREADO,
    fuente_actualizado_el=datetime(2026, 11, 21, 10, 0, tzinfo=UTC),
    articulos=(Articulo("701-1", "SKU-A", "Arras de boda", 2, "B000TEST01"),),
    paquetes=(PAQUETE,),
)
ENVIO_B = replace(
    ENVIO_A,
    paquetes=(
        replace(PAQUETE, estado_fuente="DELIVERED", detalle_fuente="DELIVERED"),
        replace(PAQUETE, paquete_ref="PKG-2", guia=None),
    ),
)


def _sonda() -> str:
    primera = IDA.read_text(encoding="utf-8").splitlines()[0]
    assert primera.startswith("-- sonda-aplicada: SELECT ")
    return primera.split(":", 1)[1].strip()


def _run(conn: psycopg.Connection) -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('prueba_envios') RETURNING id"
    ).fetchone()[0]


def _cuenta(conn: psycopg.Connection) -> tuple[int, int]:
    return conn.execute(
        "SELECT (SELECT count(*) FROM envio_observation),"
        " (SELECT count(*) FROM envio_paquete_observation)"
    ).fetchone()


def _actual(conn: psycopg.Connection, envio_ref: str = "701-1") -> tuple[dict, list[dict]]:
    with conn.cursor(row_factory=dict_row) as cur:
        filas = cur.execute(
            "SELECT * FROM v_envio_actual WHERE envio_ref = %s", (envio_ref,)
        ).fetchall()
        assert len(filas) == 1
        paquetes = cur.execute(
            "SELECT * FROM envio_paquete_observation WHERE envio_obs_id = %s ORDER BY paquete_ref",
            (filas[0]["id"],),
        ).fetchall()
    return filas[0], paquetes


def test_0102_sintaxis_sonda_y_receta():
    # Mismo patron con el que tools/despliegue/comun.sh aborta una migracion.
    control = re.compile(
        r"^\s*(BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK)\s*(TRANSACTION|WORK)?\s*;",
        re.IGNORECASE | re.MULTILINE,
    )
    for ruta in (IDA, REVERSA):
        texto = ruta.read_text(encoding="utf-8")
        assert pglast.parse_sql(texto)
        assert texto.splitlines()[0].startswith("-- sonda-aplicada: SELECT ")
        assert control.search(texto) is None, ruta.name


@_skip_db
def test_0102_append_only_huella_permisos_y_reversa():
    with db_desde_template("orbit_envios", base=_test_dsn(), autocommit=True) as (conn, _):
        assert conn.execute(_sonda()).fetchone()[0] is True

        # El escritor corre con el rol de la ingesta.
        conn.execute("SET ROLE app_ingest")
        run_id = _run(conn)
        assert guardar(conn, [ENVIO_A], observed_at=T0, run_id=run_id) == (1, 0)
        fila_a, paquetes_a = _actual(conn)
        assert guardar(conn, [ENVIO_A], observed_at=T1, run_id=run_id) == (0, 1)
        assert _actual(conn) == (fila_a, paquetes_a)
        assert _cuenta(conn) == (1, 1)

        assert guardar(conn, [ENVIO_B], observed_at=T1, run_id=run_id) == (1, 0)
        fila_b, paquetes_b = _actual(conn)
        assert fila_b["id"] != fila_a["id"]
        assert fila_b["observed_at"] == T1
        assert [(p["paquete_ref"], p["estado_fuente"], p["guia"]) for p in paquetes_b] == [
            ("PKG-1", "DELIVERED", "GUIA-1"),
            ("PKG-2", "IN_TRANSIT", None),
        ]

        # A -> B -> A: se compara contra la ultima, no contra toda la historia.
        assert guardar(conn, [ENVIO_A], observed_at=T2, run_id=run_id) == (1, 0)
        assert [p["estado_fuente"] for p in _actual(conn)[1]] == ["IN_TRANSIT"]
        assert _cuenta(conn) == (3, 4)

        conn.execute("SET ROLE app_read")
        assert _cuenta(conn) == (3, 4)
        assert conn.execute("SELECT count(*) FROM v_envio_actual").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            guardar(conn, [ENVIO_B], observed_at=T2 + timedelta(hours=4), run_id=run_id)

        conn.execute("SET ROLE app_decide")
        for relacion in (*TABLAS, "v_envio_actual"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT 1 FROM {relacion}")  # noqa: S608
        conn.execute("RESET ROLE")

        esperado = {
            "app_ingest": {"SELECT", "INSERT"},
            "app_read": {"SELECT"},
            "app_admin": {"SELECT"},
            "app_decide": set(),
        }
        for relacion in (*TABLAS, "v_envio_actual"):
            for rol, concedidos in esperado.items():
                reales = {
                    privilegio
                    for privilegio in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                    if conn.execute(
                        "SELECT has_table_privilege(%s, %s, %s)", (rol, relacion, privilegio)
                    ).fetchone()[0]
                }
                permitidos = concedidos
                if relacion == "v_envio_actual":
                    permitidos = concedidos - {"INSERT"}
                assert reales == permitidos, (rol, relacion)
        for tabla in TABLAS:
            usan = [
                rol
                for rol in esperado
                if conn.execute(
                    "SELECT has_sequence_privilege(%s, %s, 'USAGE')", (rol, f"{tabla}_id_seq")
                ).fetchone()[0]
            ]
            assert usan == ["app_ingest"], tabla

        # Ni el dueno del esquema reescribe una observacion.
        for tabla, columna in (
            ("envio_observation", "estado_fuente"),
            ("envio_paquete_observation", "guia"),
        ):
            for sentencia in (
                f"UPDATE {tabla} SET {columna} = 'x'",
                f"DELETE FROM {tabla}",
                f"TRUNCATE {tabla} CASCADE",
            ):
                with pytest.raises(
                    psycopg.errors.RestrictViolation, match=f"La tabla {tabla} es APPEND-ONLY"
                ):
                    conn.execute(sentencia)
        assert _cuenta(conn) == (3, 4)

        # La reversa retira todo aun con filas, converge si se repite y deja reaplicar.
        for _intento in range(2):
            conn.execute(REVERSA.read_text(encoding="utf-8"))
            assert conn.execute(_sonda()).fetchone()[0] is False
            assert conn.execute(
                "SELECT to_regclass('public.envio_observation'),"
                " to_regclass('public.envio_paquete_observation'),"
                " to_regclass('public.v_envio_actual'),"
                " to_regprocedure('public.envio_tiempo_coherente()')"
            ).fetchone() == (None, None, None, None)
        conn.execute(IDA.read_text(encoding="utf-8"))
        assert conn.execute(_sonda()).fetchone()[0] is True


@_skip_db
def test_0102_columnas_sin_pii_ni_montos():
    with db_desde_template("orbit_envios_col", base=_test_dsn(), autocommit=True) as (conn, _):

        def columnas(relacion: str) -> list[str]:
            return [
                fila[0]
                for fila in conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = %s"
                    " ORDER BY ordinal_position",
                    (relacion,),
                ).fetchall()
            ]

        envio = [
            "id",
            "platform",
            "envio_ref",
            "pedidos",
            "pack_ref",
            "logistica_fuente",
            "estado_fuente",
            "subestado_fuente",
            "pedido_creado_el",
            "fuente_actualizado_el",
            "despachar_antes_de",
            "entrega_prometida_el",
            "entrega_limite_el",
            "entregado_el",
            "devuelto_el",
            "articulos",
            "huella",
            "observed_at",
            "ingest_run_id",
        ]
        assert columnas("envio_observation") == envio
        assert columnas("v_envio_actual") == envio
        assert columnas("envio_paquete_observation") == [
            "id",
            "envio_obs_id",
            "paquete_ref",
            "transportista",
            "servicio",
            "guia",
            "despachado_el",
            "estado_fuente",
            "detalle_fuente",
        ]
        indices = dict(
            conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
                " AND tablename IN ('envio_observation', 'envio_paquete_observation')"
            ).fetchall()
        )
        actual = indices["envio_observation_actual_idx"]
        assert actual.endswith("(platform, envio_ref, observed_at DESC, id DESC)")
        assert indices["envio_paquete_por_envio"].endswith("(envio_obs_id)")


@_skip_db
def test_guardar_escribe_cada_hecho_en_su_columna():
    envio = envio_de_prueba(
        platform="meli",
        envio_ref="44000000001",
        pedidos=("2000001", "2000002"),
        pack_ref="2000009",
        logistica_fuente="xd_drop_off",
        estado_fuente="not_delivered",
        subestado_fuente="returning_to_sender",
        pedido_creado_el=datetime(2026, 11, 1, tzinfo=UTC),
        fuente_actualizado_el=datetime(2026, 11, 2, tzinfo=UTC),
        despachar_antes_de=datetime(2026, 11, 3, tzinfo=UTC),
        entrega_prometida_el=datetime(2026, 11, 4, tzinfo=UTC),
        entrega_limite_el=datetime(2026, 11, 5, tzinfo=UTC),
        entregado_el=datetime(2026, 11, 6, tzinfo=UTC),
        devuelto_el=datetime(2026, 11, 7, tzinfo=UTC),
        articulos=(Articulo("2000001", None, "Collar", None, "MLM1"),),
        paquetes=(PAQUETE,),
    )
    with db_desde_template("orbit_envios_map", base=_test_dsn(), autocommit=True) as (conn, _):
        run_id = _run(conn)
        assert guardar(conn, [envio], observed_at=T0, run_id=run_id) == (1, 0)
        fila, paquetes = _actual(conn, "44000000001")

        huella_guardada = fila.pop("huella")
        assert re.fullmatch(r"[0-9a-f]{64}", huella_guardada)
        obs_id = fila.pop("id")
        assert fila == {
            "platform": "meli",
            "envio_ref": "44000000001",
            "pedidos": ["2000001", "2000002"],
            "pack_ref": "2000009",
            "logistica_fuente": "xd_drop_off",
            "estado_fuente": "not_delivered",
            "subestado_fuente": "returning_to_sender",
            "pedido_creado_el": datetime(2026, 11, 1, tzinfo=UTC),
            "fuente_actualizado_el": datetime(2026, 11, 2, tzinfo=UTC),
            "despachar_antes_de": datetime(2026, 11, 3, tzinfo=UTC),
            "entrega_prometida_el": datetime(2026, 11, 4, tzinfo=UTC),
            "entrega_limite_el": datetime(2026, 11, 5, tzinfo=UTC),
            "entregado_el": datetime(2026, 11, 6, tzinfo=UTC),
            "devuelto_el": datetime(2026, 11, 7, tzinfo=UTC),
            "articulos": [
                {
                    "pedido_ref": "2000001",
                    "sku": None,
                    "titulo": "Collar",
                    "cantidad": None,
                    "producto_ref": "MLM1",
                }
            ],
            "observed_at": T0,
            "ingest_run_id": run_id,
        }
        assert len(paquetes) == 1
        del paquetes[0]["id"]
        assert paquetes[0] == {
            "envio_obs_id": obs_id,
            "paquete_ref": "PKG-1",
            "transportista": "AMZMX",
            "servicio": "Standard",
            "guia": "GUIA-1",
            "despachado_el": datetime(2026, 11, 21, 9, 0, tzinfo=UTC),
            "estado_fuente": "IN_TRANSIT",
            "detalle_fuente": "PICKED_UP",
        }


@_skip_db
def test_guardar_converge_en_lotes_repetidos_sin_abortar():
    with db_desde_template("orbit_envios_lote", base=_test_dsn()) as (conn, _):
        with conn.transaction():
            run_id = _run(conn)
            assert guardar(conn, [], observed_at=T0, run_id=run_id) == (0, 0)
            otro_mercado = replace(ENVIO_A, platform="amazon_us")
            lote = [ENVIO_A, ENVIO_A, otro_mercado]
            assert guardar(conn, lote, observed_at=T0, run_id=run_id) == (2, 1)
            # Mismo envio y mismo instante con otros hechos: gana el primero.
            assert guardar(conn, [ENVIO_B], observed_at=T0, run_id=run_id) == (0, 1)
            nuevo = replace(ENVIO_A, envio_ref="701-2")
            lote = [nuevo, replace(ENVIO_B, envio_ref="701-2")]
            assert guardar(conn, lote, observed_at=T0, run_id=run_id) == (1, 1)
            # Reintento identico y cambio solo en la fecha de la fuente.
            refrescado = replace(ENVIO_A, fuente_actualizado_el=T1)
            assert guardar(conn, [refrescado], observed_at=T1, run_id=run_id) == (0, 1)
        assert _cuenta(conn) == (3, 3)


@_skip_db
def test_guardar_no_confirma_la_transaccion_del_llamador():
    with db_desde_template("orbit_envios_tx", base=_test_dsn()) as (conn, _):
        with pytest.raises(RuntimeError, match="falla posterior"), conn.transaction():
            assert guardar(conn, [ENVIO_A], observed_at=T0, run_id=_run(conn)) == (1, 0)
            raise RuntimeError("falla posterior del llamador")
        assert _cuenta(conn) == (0, 0)


@_skip_db
def test_guardar_nunca_deja_un_envio_sin_sus_paquetes():
    repetido = replace(ENVIO_A, paquetes=(PAQUETE, PAQUETE))
    with db_desde_template("orbit_envios_atom", base=_test_dsn(), autocommit=True) as (conn, _):
        run_id = _run(conn)
        with pytest.raises(psycopg.errors.UniqueViolation):
            guardar(conn, [repetido], observed_at=T0, run_id=run_id)
        assert _cuenta(conn) == (0, 0)


@_skip_db
@pytest.mark.parametrize("platform", ["amazon_mx", "amazon_us", "meli"])
def test_envio_tiempo_coherente_solo_en_amazon(platform):
    # 10:00-06:00 es 16:00 UTC: la comparacion es absoluta, no de reloj local.
    envio = replace(
        ENVIO_A,
        platform=platform,
        pedido_creado_el=datetime.fromisoformat("2026-11-21T10:00:00-06:00"),
        fuente_actualizado_el=datetime.fromisoformat("2026-11-21T15:00:00+00:00"),
    )
    with db_desde_template("orbit_envios_t", base=_test_dsn(), autocommit=True) as (conn, _):
        run_id = _run(conn)
        if platform == "meli":
            assert guardar(conn, [envio], observed_at=T0, run_id=run_id) == (1, 0)
            return
        with pytest.raises(psycopg.errors.CheckViolation, match="pedido_creado_el"):
            guardar(conn, [envio], observed_at=T0, run_id=run_id)
        # Iguales o con una fecha ausente no violan nada.
        igual = replace(envio, fuente_actualizado_el=envio.pedido_creado_el)
        sin_fecha = replace(envio, envio_ref="701-9", fuente_actualizado_el=None)
        assert guardar(conn, [igual, sin_fecha], observed_at=T0, run_id=run_id) == (2, 0)


@_skip_db
def test_0102_invariantes_de_fila():
    with db_desde_template("orbit_envios_inv", base=_test_dsn(), autocommit=True) as (conn, _):
        run_id = _run(conn)
        for malo in (
            replace(ENVIO_A, pedidos=()),
            replace(ENVIO_A, pedidos=("701-1", None)),
            replace(ENVIO_A, paquetes=(replace(PAQUETE, paquete_ref=""),)),
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                guardar(conn, [malo], observed_at=T0, run_id=run_id)

        insertar = (
            "INSERT INTO envio_observation (platform, envio_ref, pedidos, articulos,"
            " huella, observed_at, ingest_run_id)"
            " VALUES ('meli', '1', '{1}', %s::jsonb, %s, %s, %s)"
        )
        buena = "a" * 64
        for articulos, firma in (("{}", buena), ("[]", "A" * 64), ("[]", "a" * 63)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(insertar, (articulos, firma, T0, run_id))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(insertar, ("[]", buena, T0, run_id + 1000))
        conn.execute(insertar, ("[]", buena, T0, run_id))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insertar, ("[]", "b" * 64, T0, run_id))
        assert _cuenta(conn) == (1, 0)
