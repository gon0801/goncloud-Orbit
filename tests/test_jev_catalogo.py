"""Tests de la migracion 0049 y de `app/jev_catalogo.py` (JEV ADS 01, 1.2).

(a) ESTATICOS: la migracion parsea y trae las cuatro tablas, FKs, CHECKs,
    UNIQUEs, triggers append-only/encadenados y los grants de minimo
    privilegio.
(b) INTEGRACION: 0001 + 0049 en Postgres real; cada invariante muerde.
    Skip fail-closed sin Postgres (misma condicion que test_schema).

Casos del plan que deben discriminar:
- Un producto sin `listing_id` no permite declarar incompatible a todo el
  grupo (censo lo conserva y la composicion con 1.1 no llega a
  NingunoCompatible).
- Una ficha de otra variante no acredita un producto (lookup por IDs).
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pglast
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.jev_ads import (
    CensoCongelado,
    ClavePar,
    FichaFaltante,
    Indeterminado,
    Juicio,
    MiembroCenso,
    componer,
)
from app.jev_catalogo import (
    RegistroFicha,
    censo_grupo,
    ficha_vigente,
    hash_ficha,
    registrar_ficha,
    revocar_ficha,
)

ROOT = Path(__file__).resolve().parents[1]
SQL49 = (ROOT / "migrations" / "0049_jev_ads.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

AHORA = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
VENCE = AHORA + timedelta(days=30)

TABLAS_JEV = ("jev_ficha_version", "jev_ficha_revocacion", "jev_revision", "jev_par_evento")

# ---------------------------------------------------------------------------
# (a) ESTATICOS
# ---------------------------------------------------------------------------


def test_migracion_parsea_y_trae_las_cuatro_tablas():
    assert len(tuple(pglast.parse_sql(SQL49))) > 0
    for tabla in TABLAS_JEV:
        assert f"CREATE TABLE {tabla} (" in SQL49


def test_migracion_trae_unicos_y_checks_discriminadores():
    # Idempotencia: hash de ficha y solicitud de revision son UNIQUE.
    assert "UNIQUE (sha256)" in SQL49
    assert "solicitud        UUID NOT NULL UNIQUE" in SQL49
    # Reutilizacion auditable: UNIQUE revision/par/ordinal/tipo.
    assert "UNIQUE (revision_id, termino_sha256, ficha_version_id," in SQL49
    assert "contrato_sha256, ordinal, tipo)" in SQL49
    # Una sola revocacion por ficha.
    assert "UNIQUE (ficha_version_id)" in SQL49
    # Discriminadores en CHECK.
    assert "sujeto_tipo IN ('decision', 'semillas')" in SQL49
    assert "tipo IN ('intencion', 'resultado', 'reutilizacion')" in SQL49
    assert "jev_revision_sujeto_coherente" in SQL49
    assert "jev_par_discriminador" in SQL49


def test_migracion_trae_fks_y_roles():
    assert "REFERENCES product(id)" in SQL49
    assert "REFERENCES decision(id)" in SQL49
    assert "REFERENCES jev_ficha_version(id)" in SQL49
    assert "REFERENCES jev_revision(solicitud)" in SQL49
    assert "REFERENCES jev_par_evento(id)" in SQL49
    # Rol nuevo del asesor, NOLOGIN como los demas grupos de 0001.
    assert "CREATE ROLE app_jev NOLOGIN" in SQL49
    assert "GRANT SELECT, INSERT ON jev_revision, jev_par_evento TO app_jev" in SQL49
    assert "GRANT SELECT, INSERT ON jev_ficha_version, jev_ficha_revocacion TO app_admin" in SQL49
    # Minimo privilegio por el ARBOL de sentencias (R18): un GRANT a varias
    # tablas ya no escapa a una busqueda de texto. Exactamente estas tablas.
    assert _tablas_cedidas(SQL49, "app_jev") == {
        "ad_entity",
        "ad_entity_state",
        "listing",
        "product",
        "jev_revision",
        "jev_par_evento",
        "jev_ficha_version",
        "jev_ficha_revocacion",
    }


def _tablas_cedidas(sql: str, rol: str) -> set[str]:
    tablas: set[str] = set()
    for crudo in pglast.parse_sql(sql):
        sentencia = crudo.stmt
        if type(sentencia).__name__ != "GrantStmt" or not sentencia.is_grant:
            continue
        if any(getattr(g, "rolename", None) == rol for g in sentencia.grantees):
            tablas.update(objeto.relname for objeto in sentencia.objects)
    return tablas


def test_migracion_trae_append_only_y_triggers():
    # 4 tablas x (UPDATE/DELETE + TRUNCATE), leccion de 0023: por motor.
    assert SQL49.count("EXECUTE FUNCTION prohibir_mutacion()") == 8
    # Reglas temporales en trigger UTC, nunca CHECK contra la hora.
    assert "jev_ficha_version_cobertura" in SQL49
    assert "jev_revision_tiempos" in SQL49
    assert "jev_par_evento_encadenado" in SQL49
    assert "revisar_antes_de < NEW.observado_at" in SQL49
    assert "NEW.captured_at > clock_timestamp()" in SQL49
    # 0050 (R1) reemplaza ese cuerpo: created_at lo fija el trigger.
    sql50 = (ROOT / "migrations" / "0050_jev_revision_created_at.sql").read_text(encoding="utf-8")
    assert "NEW.created_at := clock_timestamp();" in sql50
    assert "NEW.captured_at > NEW.created_at OR NEW.decided_at > NEW.created_at" in sql50


# ---------------------------------------------------------------------------
# (b) INTEGRACION: Postgres real con 0001 + 0049
# ---------------------------------------------------------------------------

ORDEN = (
    "0001_initial.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0049_jev_ads.sql",
    "0050_jev_revision_created_at.sql",
)


@contextmanager
def db_jev(prefijo: str = "orbit_jev01"):
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{__import__('os').getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _dsn_jev() -> str:
    return _test_dsn().rsplit("/", 1)[0] + "/" + _db_jev_nombre()


def _db_jev_nombre() -> str:
    return f"orbit_jev01_{socket.gethostname().lower()}_{os.getpid()}"


def _producto(conn, sku: str = "P-1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
    ).fetchone()[0]


def _listing(conn, producto: int, plataforma: str = "amazon_mx", asin: str | None = None) -> int:
    asin = asin or f"B0{producto:08X}"
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id) VALUES (%s, %s, %s) RETURNING id",
        (producto, plataforma, asin),
    ).fetchone()[0]


def _grupo(conn, plataforma: str, ads: tuple[int | None, ...]) -> int:
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'ad_group', %s)"
        " RETURNING id",
        (plataforma, f"ag-{uuid.uuid4()}"),
    ).fetchone()[0]
    for i, listing_id in enumerate(ads):
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES (%s, 'product_ad', %s, %s, %s)",
            (plataforma, f"ad-{ag}-{i}", ag, listing_id),
        )
    return ag


def _ficha(
    conn,
    producto: int,
    listings: tuple[int, ...],
    plataforma: str = "amazon_mx",
    *,
    observado: datetime = AHORA,
    vence: datetime = VENCE,
) -> uuid.UUID:
    fid = uuid.uuid4()
    conn.execute(
        "INSERT INTO jev_ficha_version (id, producto_id, plataforma, listings, hechos,"
        " desconocidos, sha256, aprobador, observado_at, revisar_antes_de)"
        " VALUES (%s, %s, %s, %s, %s::jsonb, '{}', %s, 'aprobador', %s, %s)",
        (
            fid,
            producto,
            plataforma,
            list(listings),
            json.dumps([{"texto": "hecho", "fuente": "fuente"}]),
            uuid.uuid4().hex * 2,
            observado,
            vence,
        ),
    )
    return fid


def _revision(
    conn,
    solicitud: uuid.UUID | None = None,
    *,
    decision_id: int | None = None,
    created_at: str | None = None,
    captured_at: str | None = None,
    decided_at: str | None = None,
    sujeto_tipo: str = "semillas",
    plan_canonico: str | None = '{"v": 1}',
    plan_sha256: str | None = "c" * 64,
    fuentes_semillas: str | None = '{"filas": []}',
    censos: str = "{}",
    contrato: str = "{}",
) -> uuid.UUID:
    valores: dict[str, object] = {
        "solicitud": solicitud or uuid.uuid4(),
        "sujeto_tipo": sujeto_tipo,
        "plan_canonico": plan_canonico,
        "plan_sha256": plan_sha256,
        "fuentes_semillas": fuentes_semillas,
        "censos": censos,
        "contrato": contrato,
    }
    if decision_id is not None:
        valores["decision_id"] = decision_id
    if created_at is not None:
        valores["created_at"] = created_at
    if captured_at is not None:
        valores["captured_at"] = captured_at
    if decided_at is not None:
        valores["decided_at"] = decided_at
    columnas = list(valores)
    jsonb = {"plan_canonico", "fuentes_semillas", "censos", "contrato"}
    placeholders = ", ".join("%s::jsonb" if c in jsonb else "%s" for c in columnas)
    conn.execute(
        f"INSERT INTO jev_revision ({', '.join(columnas)}) VALUES ({placeholders})",
        tuple(valores.values()),
    )
    return valores["solicitud"]  # type: ignore[return-value]


def _par(
    conn,
    revision: uuid.UUID,
    ficha: uuid.UUID,
    ordinal: int,
    tipo: str,
    **extra,
) -> uuid.UUID:
    pid = uuid.uuid4()
    conn.execute(
        "INSERT INTO jev_par_evento (id, revision_id, termino_sha256, ficha_version_id,"
        " contrato_sha256, ordinal, tipo, request_sha256, respuesta, error, intencion_id,"
        " reutiliza_id, duracion_ms, usage)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)",
        (
            pid,
            revision,
            "a" * 64,
            ficha,
            "b" * 64,
            ordinal,
            tipo,
            extra.get("request_sha256"),
            extra.get("respuesta"),
            extra.get("error"),
            extra.get("intencion_id"),
            extra.get("reutiliza_id"),
            extra.get("duracion_ms"),
            extra.get("usage"),
        ),
    )
    return pid


def _registrar(
    conn,
    producto: int,
    listings: tuple[int, ...],
    *,
    aprobador: str = "aprobador",
    observado: datetime = AHORA,
    vence: datetime = VENCE,
) -> RegistroFicha:
    return registrar_ficha(
        conn,
        producto_id=producto,
        plataforma="amazon_mx",
        listings=listings,
        hechos=(("hecho", "fuente"),),
        desconocidos=(),
        aprobador=aprobador,
        observado_at=observado,
        revisar_antes_de=vence,
    )


@_skip_db
def test_fks_muerden_en_las_cuatro_tablas():
    with db_jev() as conn:
        # producto_id inexistente: el trigger de cobertura muerde antes que
        # la FK (defensa en profundidad; la FK queda como respaldo).
        with pytest.raises(psycopg.errors.CheckViolation):
            _ficha(conn, 999999, (1,))
        producto = _producto(conn)
        listing = _listing(conn, producto)
        ficha = _ficha(conn, producto, (listing,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO jev_ficha_revocacion (ficha_version_id, autor, motivo)"
                " VALUES (%s, 'autor', 'motivo')",
                (uuid.uuid4(),),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _revision(
                conn,
                decision_id=999999,
                sujeto_tipo="decision",
                plan_canonico=None,
                plan_sha256=None,
                fuentes_semillas=None,
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _par(conn, uuid.uuid4(), ficha, 1, "intencion", request_sha256="a" * 64)


@_skip_db
def test_hash_de_ficha_y_registro_idempotente():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        primero = _registrar(conn, producto, (listing,))
        assert primero.ya_existia is False
        hash_esperado = hash_ficha(
            producto_id=producto,
            plataforma="amazon_mx",
            listings=(listing,),
            hechos=(("hecho", "fuente"),),
            desconocidos=(),
            aprobador="aprobador",
            observado_at=AHORA,
            revisar_antes_de=VENCE,
        )
        assert primero.ficha.sha256 == hash_esperado
        repetido = _registrar(conn, producto, (listing,))
        assert repetido.ya_existia is True
        assert repetido.ficha.id == primero.ficha.id
        assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 1
        corregida = _registrar(conn, producto, (listing,), aprobador="otro")
        assert corregida.ya_existia is False
        assert corregida.ficha.id != primero.ficha.id
        assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 2


@_skip_db
def test_listings_vacios_no_registran_ficha():
    """Regresion revision automatica B2-r4 (F1): array_length('{}',1) es
    NULL y el CHECK/trigger viejos dejaban pasar una ficha que no cubre
    nada en tabla append-only."""
    with db_jev() as conn:
        producto = _producto(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _registrar(conn, producto, ())
        assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 0


@_skip_db
def test_desconocidos_generador_no_corrompe_hash_ni_fila():
    """Regresion revision automatica B2-r4 (F2): desconocidos/hechos/listings
    se consumen UNA vez; un iterable de un solo uso no puede desalinear el
    hash guardado con el contenido de la fila."""
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        registro = registrar_ficha(
            conn,
            producto_id=producto,
            plataforma="amazon_mx",
            listings=iter((listing,)),
            hechos=iter((("hecho", "fuente"),)),
            desconocidos=iter(("peso", "material")),
            aprobador="aprobador",
            observado_at=AHORA,
            revisar_antes_de=VENCE,
        )
        hash_esperado = hash_ficha(
            producto_id=producto,
            plataforma="amazon_mx",
            listings=(listing,),
            hechos=(("hecho", "fuente"),),
            desconocidos=("material", "peso"),
            aprobador="aprobador",
            observado_at=AHORA,
            revisar_antes_de=VENCE,
        )
        assert registro.ficha.sha256 == hash_esperado
        assert registro.ficha.desconocidos == frozenset({"material", "peso"})
        # R15: hechos y listings tambien llegan completos a la fila y al objeto.
        assert registro.ficha.listings == frozenset({listing})
        assert [(h.texto, h.fuente) for h in registro.ficha.hechos] == [("hecho", "fuente")]
        fila = conn.execute(
            "SELECT desconocidos, sha256, listings, hechos FROM jev_ficha_version WHERE id = %s",
            (registro.ficha.id,),
        ).fetchone()
        assert sorted(fila[0]) == ["material", "peso"]
        assert fila[1] == hash_esperado
        assert fila[2] == [listing]
        assert fila[3] == [{"texto": "hecho", "fuente": "fuente"}]


@_skip_db
def test_revocar_dos_veces_no_aborta_la_transaccion_del_llamador():
    """R7: la segunda revocacion sale como ValueError y la transaccion del
    llamador sigue usable (antes: UniqueViolation abortaba la transaccion)."""
    with db_jev() as conn:
        producto = _producto(conn)
        ficha = _registrar(conn, producto, (_listing(conn, producto),)).ficha.id
        with psycopg.connect(_dsn_jev()) as tx:
            revocar_ficha(tx, ficha_version_id=ficha, autor="a", motivo="m")
            with pytest.raises(ValueError, match="ya revocada"):
                revocar_ficha(tx, ficha_version_id=ficha, autor="a", motivo="m")
            assert tx.execute("SELECT count(*) FROM jev_ficha_revocacion").fetchone()[0] == 1


@_skip_db
def test_registro_concurrente_del_mismo_contenido_devuelve_la_fila_existente():
    """R7: dos registros del MISMO contenido en transacciones concurrentes. El
    segundo no ve la fila del primero (sin confirmar), espera en el indice
    UNIQUE(sha256) y, al confirmarse el primero, devuelve esa fila en vez de
    reventar con UniqueViolation."""
    import threading
    import time

    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        resultado: dict = {}
        with psycopg.connect(_dsn_jev()) as primera, psycopg.connect(_dsn_jev()) as segunda:
            propia = _registrar(primera, producto, (listing,))

            def registrar_en_segunda():
                try:
                    resultado["registro"] = _registrar(segunda, producto, (listing,))
                    segunda.commit()
                except Exception as error:  # noqa: BLE001 - la prueba reporta cualquiera
                    resultado["error"] = error

            pid_segunda = segunda.info.backend_pid
            hilo = threading.Thread(target=registrar_en_segunda)
            hilo.start()
            # La segunda llego al INSERT y espera el lock del indice UNIQUE(sha256).
            espera = "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s"
            for _ in range(100):
                if conn.execute(espera, (pid_segunda,)).fetchone()[0] == "Lock":
                    break
                time.sleep(0.05)
            else:
                pytest.fail("la segunda transaccion nunca quedo esperando el lock")
            primera.commit()
            hilo.join(timeout=10)
            assert not hilo.is_alive(), "la segunda transaccion no termino tras el commit"
        assert "error" not in resultado, resultado.get("error")
        assert resultado["registro"].ya_existia is True
        assert resultado["registro"].ficha.id == propia.ficha.id
        assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 1


@_skip_db
def test_solicitud_idempotente_y_discriminador_de_sujeto():
    with db_jev() as conn:
        solicitud = _revision(conn)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _revision(conn, solicitud)
        # El mismo solicitud con otro payload se rechaza antes del HTTP (1.4
        # lo ejercita; aqui el candado UNIQUE es el que muerde).
        with pytest.raises(psycopg.errors.UniqueViolation):
            _revision(conn, solicitud, censos='{"otro": true}')
        # Sujeto coherente: semillas exige plan canonico + hash.
        with pytest.raises(psycopg.errors.CheckViolation):
            _revision(conn, sujeto_tipo="semillas", plan_canonico=None, plan_sha256=None)
        with pytest.raises(psycopg.errors.CheckViolation):
            _revision(
                conn,
                sujeto_tipo="decision",
                plan_canonico='{"v": 1}',
                plan_sha256="c" * 64,
            )


@_skip_db
def test_append_only_por_motor_en_las_cuatro_tablas():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        ficha = _ficha(conn, producto, (listing,))
        _ficha(conn, producto, (listing,))
        revision = _revision(conn)
        _par(conn, revision, ficha, 1, "intencion", request_sha256="a" * 64)
        conn.execute(
            "INSERT INTO jev_ficha_revocacion (ficha_version_id, autor, motivo)"
            " VALUES (%s, 'autor', 'motivo')",
            (ficha,),
        )
        columna_noop = {
            "jev_ficha_version": "aprobador",
            "jev_ficha_revocacion": "autor",
            "jev_revision": "censos",
            "jev_par_evento": "error",
        }
        for tabla, columna in columna_noop.items():
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"UPDATE {tabla} SET {columna} = {columna}")
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"DELETE FROM {tabla}")


@_skip_db
def test_reglas_temporales_y_cobertura_en_triggers():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        otro_listing = _listing(conn, _producto(conn, "P-2"), plataforma="amazon_mx")
        # Revisar_antes_de anterior a observado_at.
        with pytest.raises(psycopg.errors.CheckViolation):
            _ficha(conn, producto, (listing,), vence=AHORA - timedelta(days=1))
        # Listing de otro producto o de otra plataforma no cubre la ficha.
        with pytest.raises(psycopg.errors.CheckViolation):
            _ficha(conn, producto, (otro_listing,))
        with pytest.raises(psycopg.errors.CheckViolation):
            _ficha(conn, producto, (listing,), plataforma="amazon_us")
        # Duplicados en listings.
        with pytest.raises(psycopg.errors.CheckViolation):
            _ficha(conn, producto, (listing, listing))
        # Regla temporal: nada posterior a la INSERCION REAL (clock_timestamp).
        _revision(
            conn,
            created_at="2026-10-03 12:00:00+00",
            captured_at="2026-10-03 11:00:00+00",
        )
        futuro = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        with pytest.raises(psycopg.errors.CheckViolation):
            _revision(conn, captured_at=futuro)
        # Los tiempos guardados vienen en UTC.
        fid = _ficha(conn, producto, (listing,))
        creado = conn.execute(
            "SELECT created_at FROM jev_ficha_version WHERE id = %s", (fid,)
        ).fetchone()[0]
        assert creado.utcoffset() == timedelta(0)


@_skip_db
def test_roles_de_minimo_privilegio():
    with db_jev() as conn:
        if conn.execute("SHOW is_superuser").fetchone()[0] != "on":
            pytest.skip("SET ROLE exige superusuario de prueba")
        producto = _producto(conn)
        listing = _listing(conn, producto)
        try:
            conn.execute("SET ROLE app_jev")
            # El asesor lee entradas de catalogo e inserta su revision.
            assert conn.execute("SELECT count(*) FROM product").fetchone()[0] >= 1
            assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] >= 1
            _revision(conn)
            # Sin permisos sobre decisiones, ledger, goals ni bibliotecas.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT count(*) FROM decision")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT count(*) FROM ledger_event")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT count(*) FROM ads_optimizer_goal")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT count(*) FROM search_term_observation")
            # La administracion de fichas NO es del asesor.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _ficha(conn, producto, (listing,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE jev_revision SET censos = '{}'")
        finally:
            conn.execute("RESET ROLE")
        conn.execute("SET ROLE app_admin")
        try:
            ficha = _ficha(conn, producto, (listing,))
            conn.execute(
                "INSERT INTO jev_ficha_revocacion (ficha_version_id, autor, motivo)"
                " VALUES (%s, 'autor', 'motivo')",
                (ficha,),
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _revision(conn)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE jev_ficha_version SET aprobador = 'x'")
        finally:
            conn.execute("RESET ROLE")
        conn.execute("SET ROLE app_read")
        try:
            assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0] >= 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO jev_revision (solicitud, sujeto_tipo, censos, contrato)"
                    " VALUES (gen_random_uuid(), 'semillas', '{}', '{}')"
                )
        finally:
            conn.execute("RESET ROLE")


@_skip_db
def test_lookup_de_ficha_vigente_por_ids():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        otro_listing = _listing(conn, producto, asin="B0OTRO1234")
        registro = _registrar(conn, producto, (listing,))
        # Cubre el listing registrado.
        vigente = ficha_vigente(
            conn, producto_id=producto, plataforma="amazon_mx", listing_id=listing, ahora=AHORA
        )
        assert vigente is not None and vigente.id == registro.ficha.id
        # Un listing NO cubierto (otra variante) no se acredita con esta ficha.
        assert (
            ficha_vigente(
                conn,
                producto_id=producto,
                plataforma="amazon_mx",
                listing_id=otro_listing,
                ahora=AHORA,
            )
            is None
        )
        # Otra variante de producto (otro ID) tampoco.
        otro_producto = _producto(conn, "P-3")
        assert (
            ficha_vigente(
                conn,
                producto_id=otro_producto,
                plataforma="amazon_mx",
                listing_id=listing,
                ahora=AHORA,
            )
            is None
        )
        # Revocada deja de acreditar.
        revocar_ficha(conn, ficha_version_id=registro.ficha.id, autor="autor", motivo="motivo")
        assert (
            ficha_vigente(
                conn, producto_id=producto, plataforma="amazon_mx", listing_id=listing, ahora=AHORA
            )
            is None
        )
        # Doble revocacion se rechaza.
        with pytest.raises(ValueError):
            revocar_ficha(conn, ficha_version_id=registro.ficha.id, autor="autor", motivo="motivo")
        # Vencida deja de acreditar.
        segunda = _registrar(conn, producto, (listing,), vence=AHORA + timedelta(days=1))
        assert segunda.ficha.id != registro.ficha.id
        assert (
            ficha_vigente(
                conn,
                producto_id=producto,
                plataforma="amazon_mx",
                listing_id=listing,
                ahora=AHORA,
            )
            is not None
        )
        assert (
            ficha_vigente(
                conn,
                producto_id=producto,
                plataforma="amazon_mx",
                listing_id=listing,
                ahora=AHORA + timedelta(days=2),
            )
            is None
        )


@_skip_db
def test_captura_dentro_de_la_transaccion_se_acepta_y_futura_se_rechaza():
    """Regresion VEREDICTO-B2-r1 B2: el flujo del diseno (snapshot
    REPEATABLE READ: BEGIN, leer censo, captured_at, INSERT de la revision
    antes del primer HTTP) produce captured_at posterior al now() del BEGIN
    y anterior a la insercion real; el trigger lo debe aceptar. Una
    captured_at futura de verdad se sigue rechazando."""
    import time

    with db_jev() as conn:
        with conn.transaction():
            conn.execute("SELECT 1")
            time.sleep(0.05)
            captura = datetime.now(UTC).isoformat()
            _revision(conn, captured_at=captura)
        futuro = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        with pytest.raises(psycopg.errors.CheckViolation):
            _revision(conn, captured_at=futuro)


@_skip_db
def test_created_at_es_la_insercion_real_y_nunca_precede_a_otros_tiempos():
    """R1 (B7): con `DEFAULT now()` el created_at de una revision era el
    inicio de la transaccion y quedaba ANTES de una captura hecha dentro de
    ella. 0050 hace que el trigger FIJE created_at con clock_timestamp()
    (un valor explicito se ignora: la cronologia no se puede falsear) y
    exige decided_at/captured_at <= created_at."""
    import time

    with db_jev() as conn:
        with conn.transaction():
            conn.execute("SELECT 1")
            time.sleep(0.05)
            captura = conn.execute("SELECT clock_timestamp()").fetchone()[0]
            solicitud = _revision(conn, captured_at=captura.isoformat())
        creado = conn.execute(
            "SELECT created_at FROM jev_revision WHERE solicitud = %s", (solicitud,)
        ).fetchone()[0]
        assert creado >= captura
        antes = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        falseada = _revision(
            conn,
            created_at="2000-01-01 00:00:00+00",
            captured_at="2026-10-03 11:59:00+00",
            decided_at="2026-10-03 11:00:00+00",
        )
        despues = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        creado = conn.execute(
            "SELECT created_at FROM jev_revision WHERE solicitud = %s", (falseada,)
        ).fetchone()[0]
        assert antes <= creado <= despues
        futuro = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        with pytest.raises(psycopg.errors.CheckViolation):
            _revision(conn, captured_at=futuro)
        with pytest.raises(psycopg.errors.CheckViolation):
            _revision(conn, decided_at=futuro)


@_skip_db
def test_censo_lleva_estado_y_archived_no_acredita_compatible():
    """Regresion VEREDICTO-B2-r1 B1: el censo conserva status/synced_at por
    anuncio (LEFT JOIN a ad_entity_state) y componer excluye al producto
    cuyo unico anuncio esta ARCHIVED: su satisface jamas produce
    HayCompatible."""
    with db_jev() as conn:
        x = _producto(conn, "X")
        listing_x = _listing(conn, x, asin="B0XXXXXXX1")
        y = _producto(conn, "Y")
        listing_y = _listing(conn, y, asin="B0YYYYYYY1")
        grupo = _grupo(conn, "amazon_mx", (listing_x, listing_y))
        for i, status in ((0, "ARCHIVED"), (1, "ENABLED")):
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), %s, now())",
                (f"ad-{grupo}-{i}", status),
            )
        censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=grupo)
        por_producto = {m.producto_id: m for m in censo.miembros}
        assert [e.status for e in por_producto[x].estados] == ["ARCHIVED"]
        assert por_producto[x].estados[0].synced_at is not None
        assert [e.status for e in por_producto[y].estados] == ["ENABLED"]
        ficha_x, ficha_y = uuid.uuid4(), uuid.uuid4()
        miembros = tuple(
            MiembroCenso(
                anuncio_ids=m.anuncio_ids,
                producto_id=m.producto_id,
                listing_ids=m.listing_ids,
                estados=m.estados,
                ficha_version_id=ficha_x if m.producto_id == x else ficha_y,
            )
            for m in censo.miembros
        )
        congelado = CensoCongelado(miembros=miembros, exhaustivo=censo.exhaustivo)
        juicio_x = Juicio(
            intento_id=uuid.uuid4(),
            clave=ClavePar(
                termino_literal_sha256="a" * 64,
                ficha_version_id=ficha_x,
                contrato_sha256="b" * 64,
            ),
            relacion="satisface",
            probabilidades={
                "satisface": Decimal("0.80"),
                "no_satisface": Decimal("0.10"),
                "informacion_insuficiente": Decimal("0.10"),
            },
            confidence=Decimal("0.90"),
            observado_at=AHORA,
        )
        juicio_y = Juicio(
            intento_id=uuid.uuid4(),
            clave=ClavePar(
                termino_literal_sha256="a" * 64,
                ficha_version_id=ficha_y,
                contrato_sha256="b" * 64,
            ),
            relacion="no_satisface",
            probabilidades={
                "satisface": Decimal("0.10"),
                "no_satisface": Decimal("0.80"),
                "informacion_insuficiente": Decimal("0.10"),
            },
            confidence=Decimal("0.90"),
            observado_at=AHORA,
        )
        resultado = componer(congelado, (juicio_x, juicio_y))
        assert resultado == Indeterminado(frozenset({"no_anunciado", "universo_desconocido"}))


@_skip_db
def test_censo_conserva_anuncios_sin_listing_ni_estado():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        producto_2 = _producto(conn, "P-2")
        listing_2 = _listing(conn, producto_2, asin="B0DOS00001")
        grupo = _grupo(conn, "amazon_mx", (listing, None, listing_2, listing))
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ENABLED', now())",
            (f"ad-{grupo}-0",),
        )
        censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=grupo)
        assert censo.exhaustivo is False
        con_producto = [m for m in censo.miembros if m.producto_id is not None]
        sin_producto = [m for m in censo.miembros if m.producto_id is None]
        assert len(con_producto) == 2
        assert len(sin_producto) == 1
        por_producto = {m.producto_id: m for m in con_producto}
        assert por_producto[producto].listing_ids == frozenset({listing})
        assert set(por_producto[producto].anuncio_ids) == {
            conn.execute(
                "SELECT id FROM ad_entity WHERE external_id = %s", (f"ad-{grupo}-0",)
            ).fetchone()[0],
            conn.execute(
                "SELECT id FROM ad_entity WHERE external_id = %s", (f"ad-{grupo}-3",)
            ).fetchone()[0],
        }
        assert sin_producto[0].listing_ids == frozenset()
        # El anuncio con listing pero sin estado tambien conservo su member.
        assert por_producto[producto_2].listing_ids == frozenset({listing_2})


@_skip_db
def test_producto_sin_listing_bloquea_negativo_universal():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        grupo = _grupo(conn, "amazon_mx", (listing, None))
        registro = _registrar(conn, producto, (listing,))
        censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=grupo)
        miembros = tuple(
            MiembroCenso(
                anuncio_ids=m.anuncio_ids,
                producto_id=m.producto_id,
                listing_ids=m.listing_ids,
                ficha_version_id=(
                    registro.ficha.id
                    if m.producto_id is not None
                    and ficha_vigente(
                        conn,
                        producto_id=m.producto_id,
                        plataforma="amazon_mx",
                        listing_id=next(iter(m.listing_ids)),
                        ahora=AHORA,
                    )
                    else None
                ),
            )
            for m in censo.miembros
        )
        congelado = CensoCongelado(miembros=miembros, exhaustivo=censo.exhaustivo)
        juicio = Juicio(
            intento_id=uuid.uuid4(),
            clave=ClavePar(
                termino_literal_sha256="a" * 64,
                ficha_version_id=registro.ficha.id,
                contrato_sha256="b" * 64,
            ),
            relacion="no_satisface",
            probabilidades={
                "satisface": Decimal("0.10"),
                "no_satisface": Decimal("0.80"),
                "informacion_insuficiente": Decimal("0.10"),
            },
            confidence=Decimal("0.90"),
            observado_at=AHORA,
        )
        resultado = componer(congelado, (juicio, FichaFaltante(producto_id=None)))
        assert isinstance(resultado, Indeterminado)
        assert "ficha_ausente" in resultado.motivos


@_skip_db
def test_par_evento_encadena_intencion_resultado_y_reutilizacion():
    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        ficha = _ficha(conn, producto, (listing,))
        revision = _revision(conn)
        otra_revision = _revision(conn)
        intencion = _par(conn, revision, ficha, 1, "intencion", request_sha256="a" * 64)
        _par(conn, otra_revision, ficha, 1, "intencion", request_sha256="a" * 64)
        # Resultado con su intencion de la misma revision y clave.
        resultado_exito = _par(
            conn,
            revision,
            ficha,
            2,
            "resultado",
            intencion_id=intencion,
            respuesta='{"relacion": "no_satisface"}',
            duracion_ms=1200,
            usage='{"input_tokens": 10}',
        )
        # Resultado sin intencion: CHECK y trigger lo impiden.
        with pytest.raises(psycopg.errors.CheckViolation):
            _par(conn, revision, ficha, 3, "resultado", respuesta="{}")
        # Resultado con intencion de OTRA revision: trigger lo impide.
        with pytest.raises(psycopg.errors.CheckViolation):
            intencion_ajena = _par(
                conn, otra_revision, ficha, 9, "intencion", request_sha256="a" * 64
            )
            _par(
                conn,
                revision,
                ficha,
                3,
                "resultado",
                intencion_id=intencion_ajena,
                respuesta="{}",
            )
        # Resultado de fallo (con error, sin respuesta) es valido.
        resultado_fallo = _par(
            conn, revision, ficha, 4, "resultado", intencion_id=intencion, error="timeout"
        )
        # Reutilizacion a un FALLO se rechaza; a un exito, no.
        with pytest.raises(psycopg.errors.CheckViolation):
            _par(conn, revision, ficha, 5, "reutilizacion", reutiliza_id=resultado_fallo)
        reutilizado = _par(conn, revision, ficha, 5, "reutilizacion", reutiliza_id=resultado_exito)
        # UNIQUE revisión/par/ordinal/tipo: mismo ordinal y tipo se rechaza.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _par(conn, revision, ficha, 1, "intencion", request_sha256="a" * 64)
        # La reutilizacion apuntando fuera de su revision se rechaza.
        with pytest.raises(psycopg.errors.CheckViolation):
            _par(conn, otra_revision, ficha, 2, "reutilizacion", reutiliza_id=resultado_exito)
        assert reutilizado is not None


@_skip_db
def test_reversa_0050_vuelve_a_now_y_reaplicar_restituye():
    """La reversa de 0050 devuelve el DEFAULT now() y el trigger de 0049
    (acepta captured_at > created_at); reaplicar 0050 vuelve a exigirlo."""
    with db_jev() as conn:
        migraciones = ROOT / "migrations"
        conn.execute(
            (migraciones / "0050_reversa_jev_revision_created_at.sql").read_text(encoding="utf-8")
        )
        default = conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'jev_revision' AND column_name = 'created_at'"
        ).fetchone()[0]
        assert default == "now()"
        comentario = conn.execute(
            "SELECT obj_description('jev_ficha_version'::regclass, 'pg_class')"
        ).fetchone()[0]
        assert comentario.endswith("jam el parecido de nombres).")
        viejo = _revision(
            conn, created_at="2026-10-03 12:00:00+00", captured_at="2026-10-03 13:00:00+00"
        )
        assert conn.execute(
            "SELECT created_at FROM jev_revision WHERE solicitud = %s", (viejo,)
        ).fetchone()[0] == datetime(2026, 10, 3, 12, tzinfo=UTC)
        conn.execute((migraciones / "0050_jev_revision_created_at.sql").read_text(encoding="utf-8"))
        default = conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'jev_revision' AND column_name = 'created_at'"
        ).fetchone()[0]
        assert default == "clock_timestamp()"
        assert (
            conn.execute("SELECT obj_description('jev_ficha_version'::regclass, 'pg_class')")
            .fetchone()[0]
            .endswith("jamas el parecido de nombres).")
        )
        nuevo = _revision(conn, created_at="2026-10-03 12:00:00+00")
        assert conn.execute(
            "SELECT created_at FROM jev_revision WHERE solicitud = %s", (nuevo,)
        ).fetchone()[0] > datetime(2026, 10, 3, 12, tzinfo=UTC)


@_skip_db
def test_reversa_deja_la_base_como_0001():
    dsn = _test_dsn()
    db = _db_jev_nombre() + "_reversa"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in (
            *ORDEN,
            "0050_reversa_jev_revision_created_at.sql",
            "0049_reversa_jev_ads.sql",
        ):
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        for tabla in TABLAS_JEV:
            assert (
                conn.execute("SELECT to_regclass(%s)", (f"public.{tabla}",)).fetchone()[0] is None
            )
        assert (
            conn.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'app_jev'").fetchone()[0]
            == 0
        )
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# CLI administrativo
# ---------------------------------------------------------------------------


def _hechos_tmp(tmp_path: Path) -> str:
    ruta = tmp_path / "hechos.json"
    ruta.write_text(json.dumps([{"texto": "hecho", "fuente": "fuente"}]), encoding="utf-8")
    return str(ruta)


@_skip_db
def test_cli_registra_seco_y_aplica(tmp_path, monkeypatch, capsys):
    from tools.jev_fichas import main as cli_fichas

    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_jev())
        argv = [
            "registrar",
            "--producto-id",
            str(producto),
            "--plataforma",
            "amazon_mx",
            "--listings",
            str(listing),
            "--hechos",
            _hechos_tmp(tmp_path),
            "--aprobador",
            "aprobador",
            "--observado-at",
            "2026-10-03T12:00:00+00:00",
            "--revisar-antes-de",
            "2026-11-02T12:00:00+00:00",
        ]
        # Dry-run: imprime el hash y NO escribe.
        assert cli_fichas(argv) == 0
        salida = capsys.readouterr().out
        assert "sha256" in salida
        assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 0
        # Aplicar: escribe una sola vez; repetir es idempotente.
        assert cli_fichas([*argv, "--aplicar"]) == 0
        salida = capsys.readouterr().out
        assert cli_fichas([*argv, "--aplicar"]) == 0
        capsys.readouterr()
        assert conn.execute("SELECT count(*) FROM jev_ficha_version").fetchone()[0] == 1
        assert f"id {salida.split('id ')[1].split()[0]}" in salida


@_skip_db
def test_cli_revoca_y_rechaza_doble_revocacion(tmp_path, monkeypatch, capsys):
    from tools.jev_fichas import main as cli_fichas

    with db_jev() as conn:
        producto = _producto(conn)
        listing = _listing(conn, producto)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_jev())
        argv_registro = [
            "registrar",
            "--producto-id",
            str(producto),
            "--plataforma",
            "amazon_mx",
            "--listings",
            str(listing),
            "--hechos",
            _hechos_tmp(tmp_path),
            "--aprobador",
            "aprobador",
            "--observado-at",
            "2026-10-03T12:00:00+00:00",
            "--revisar-antes-de",
            "2026-11-02T12:00:00+00:00",
            "--aplicar",
        ]
        assert cli_fichas(argv_registro) == 0
        salida = capsys.readouterr().out
        ficha_id = None
        for linea in salida.splitlines():
            if linea.startswith("id "):
                ficha_id = linea.split()[1]
        assert ficha_id is not None
        revocar = ["revocar", "--ficha-version-id", ficha_id, "--autor", "autor", "--motivo", "m"]
        revocaciones = "SELECT count(*) FROM jev_ficha_revocacion"
        # R5: en seco por omision, igual que registrar; dice que haria y no escribe.
        assert cli_fichas(revocar) == 0
        assert "seco" in capsys.readouterr().out
        assert conn.execute(revocaciones).fetchone()[0] == 0
        # El seco valida lo mismo que --aplicar: fecha ilegible y autor vacio salen 1.
        assert cli_fichas([*revocar, "--fecha", "ayer"]) == 1
        sin_autor = ["revocar", "--ficha-version-id", ficha_id, "--autor", " ", "--motivo", "m"]
        assert cli_fichas(sin_autor) == 1
        capsys.readouterr()
        assert conn.execute(revocaciones).fetchone()[0] == 0
        assert cli_fichas([*revocar, "--aplicar"]) == 0
        capsys.readouterr()
        assert conn.execute(revocaciones).fetchone()[0] == 1
        # Ya revocada: el seco reporta lo mismo que fallaria al aplicar.
        assert cli_fichas(revocar) == 1
        assert "ya revocada" in capsys.readouterr().err
        assert cli_fichas([*revocar, "--aplicar"]) == 1
        assert conn.execute(revocaciones).fetchone()[0] == 1
        inexistente = ["revocar", "--ficha-version-id", str(uuid.uuid4()), "--autor", "a"]
        assert cli_fichas([*inexistente, "--motivo", "m"]) == 1
        assert "no existe" in capsys.readouterr().err
