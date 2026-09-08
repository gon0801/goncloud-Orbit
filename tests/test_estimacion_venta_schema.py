"""Tests de la migracion 0028 (MARGEN ESTIMADO 01 A.1, acta 0.3 / spec S2-S5).

(a) ESTATICOS: la migracion parsea y trae invariantes, triggers append-only,
    politica versionada, dedupe por evento fuente y GRANTs minimos.
(b) INTEGRACION: 0001 + 0028 en Postgres real; append-only, idempotencia,
    as-of, permisos y reversa sin borrar hechos. Skip fail-closed sin Postgres.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pglast
import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
SQL28 = (ROOT / "migrations" / "0028_estimacion_venta.sql").read_text(encoding="utf-8")
SQL29 = (ROOT / "migrations" / "0029_estimacion_politica_vigencia.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

OBS = "2026-09-08 12:00:00+00"
OBS2 = "2026-09-08 13:00:00+00"
OBS_FUTURO = "2026-09-09 00:00:00+00"
FETCH = "2026-09-08 11:30:00+00"
FETCH2 = "2026-09-08 10:00:00+00"
VALORACION = "2026-09-08"

POLITICA_FBA_MX = {
    "universo": "amazon_mx/fba",
    "formula_version": "S3",
    "iva_divisor": "1.16",
    "isr_tasa": "0.025",
    "logistica": "0",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": True,
    "fee_tax_amount_requiere_politica": True,
}

# ---------------------------------------------------------------------------
# (a) ESTATICOS — fallan hasta que exista 0028 con el contrato sellado
# ---------------------------------------------------------------------------


def test_migracion_0028_existe_y_parsea():
    assert SQL28.strip(), "migrations/0028_estimacion_venta.sql debe existir"
    assert len(tuple(pglast.parse_sql(SQL28))) > 0


def test_migracion_trae_tablas_y_append_only_por_motor():
    for tabla in (
        "estimacion_politica_version",
        "estimacion_oferta_observation",
        "estimacion_fee_observation",
        "estimacion_escenario",
    ):
        assert f"CREATE TABLE {tabla}" in SQL28
    # 4 tablas de hechos x (row + truncate) = 8 triggers prohibir_mutacion
    assert SQL28.count("EXECUTE FUNCTION prohibir_mutacion()") >= 8
    codigo = "\n".join(linea for linea in SQL28.splitlines() if not linea.strip().startswith("--"))
    assert "GRANT UPDATE" not in codigo.replace("BEFORE UPDATE OR DELETE", "")
    assert "DELETE FROM estimacion_" not in codigo


def test_migracion_politica_versionada_sin_defaults_inventados():
    cuerpo = SQL28.split("CREATE TABLE estimacion_politica_version")[1].split("CREATE TABLE")[0]
    assert "settings" in cuerpo and "JSONB NOT NULL" in cuerpo
    assert "valid_from" in cuerpo and "DATE NOT NULL" in cuerpo
    assert "valid_to" in cuerpo
    assert "estimacion_politica_vigencia_coherente" in cuerpo
    assert "DEFAULT" not in cuerpo.replace("DEFAULT now()", "")
    assert "estimacion_politica_version" in SQL28
    assert "GRANT INSERT ON estimacion_politica_version TO app_admin" in SQL28


def test_migracion_dedupe_por_evento_fuente():
    for tabla in ("estimacion_oferta_observation", "estimacion_fee_observation"):
        cuerpo = SQL28.split(f"CREATE TABLE {tabla}")[1].split("CREATE TABLE")[0]
        assert "source_event_id" in cuerpo and "NOT NULL" in cuerpo
    assert "estimacion_oferta_evento_unico" in SQL28
    assert "estimacion_fee_evento_unico" in SQL28
    esc_cuerpo = SQL28.split("CREATE TABLE estimacion_escenario")[1].split("CREATE TABLE")[0]
    assert "source_event_id" in esc_cuerpo and "NOT NULL" in esc_cuerpo
    assert "estimacion_escenario_evento_unico" in SQL28
    assert "WHERE source_event_id IS NOT NULL" not in SQL28


def test_migracion_referencias_reproducibles_y_huella():
    for columna in (
        "context_fingerprint",
        "canonical_input",
        "fetched_at",
        "observed_at",
        "politica_version_id",
        "oferta_observation_id",
        "fee_observation_id",
        "sku_cost_id",
        "costo_validation_run_id",
        "costo_validated_at",
        "fx_rate",
        "fx_rate_date",
        "fx_source",
    ):
        assert columna in SQL28
    assert "NUMERIC(18, 8)" in SQL28
    assert "estimacion_fee_casa_oferta" in SQL28
    assert "estimacion_escenario_referencias_coherentes" in SQL28


def test_migracion_grants_minimos():
    assert "GRANT INSERT ON estimacion_oferta_observation" in SQL28
    assert "GRANT INSERT ON estimacion_fee_observation" in SQL28
    assert "GRANT INSERT ON estimacion_escenario" in SQL28
    assert "TO app_ingest" in SQL28
    assert "GRANT SELECT ON estimacion_politica_version" in SQL28
    assert "GRANT SELECT ON estimacion_oferta_observation TO app_read" in SQL28
    assert "GRANT USAGE ON SEQUENCE estimacion_oferta_observation_id_seq TO app_ingest" in SQL28
    assert "app_decide" in SQL28 and "app_read" in SQL28


def test_migracion_incluye_reversa_segura():
    assert "estimacion_venta_reversa" in SQL28
    assert "TRUNCATE estimacion_" not in SQL28
    assert "DELETE FROM estimacion_" not in SQL28


def test_migracion_0029_existe_y_siembra_politica():
    assert SQL29.strip(), "migrations/0029_estimacion_politica_vigencia.sql debe existir"
    assert len(tuple(pglast.parse_sql(SQL29))) > 0
    assert "ALTER TABLE" not in SQL29
    assert "amazon_mx_pf_rfc_valid_2026_01" in SQL29
    assert "'amazon_mx/fba'" in SQL29
    assert "'S3'" in SQL29
    assert "precio_incluye_iva" in SQL29
    assert "fee_tax_amount_requiere_politica" in SQL29
    assert "logistica_semantica" in SQL29


# ---------------------------------------------------------------------------
# (b) INTEGRACION
# ---------------------------------------------------------------------------

ORDEN = ("0001_initial.sql", "0028_estimacion_venta.sql", "0029_estimacion_politica_vigencia.sql")


@contextmanager
def db_estimacion(prefijo: str = "orbit_margen_a1"):
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
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


def _corrida_costos(conn, *, finished_at=FETCH2):
    return conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, rows_skipped, ok)"
        " VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE) RETURNING id",
        (finished_at, finished_at),
    ).fetchone()[0]


def _sembrar_listing(
    conn, odoo_sku="P-MX", asin="B0EST01", seller_sku="SS-MX-1"
) -> tuple[int, int]:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (odoo_sku, odoo_sku),
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', %s, %s) RETURNING id",
        (pid, asin, seller_sku),
    ).fetchone()[0]
    return pid, lid


def _politica(conn) -> int:
    return conn.execute(
        "INSERT INTO estimacion_politica_version"
        " (created_at, label, universo, formula_version, settings, valid_from, valid_to)"
        " VALUES (%s, 'fba-mx-test', 'amazon_mx/fba', 'S3', %s, '2026-01-01', NULL)"
        " RETURNING id",
        (FETCH2, Json(POLITICA_FBA_MX)),
    ).fetchone()[0]


def _oferta(
    conn,
    listing_id: int,
    *,
    fetched_at=FETCH,
    observed_at=OBS,
    evento="evt-oferta-1",
    precio="116.0000",
    seller_sku="SS-MX-1",
    asin="B0EST01",
):
    huella = f"fba:{precio}:MXN:{fetched_at}"
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
        " VALUES (%s, 'amazon_mx', %s, %s, 'fba', %s, 'MXN',"
        " %s, %s, %s, %s, %s) RETURNING id",
        (
            listing_id,
            seller_sku,
            asin,
            precio,
            fetched_at,
            observed_at,
            evento,
            Json({"bridge_row": "sanitizado"}),
            huella,
        ),
    ).fetchone()[0]


def _fee(
    conn,
    oferta_id: int,
    listing_id: int,
    *,
    fetched_at=FETCH,
    observed_at=OBS,
    fees_estimated_at=FETCH,
    evento="evt-fee-1",
    precio="116.0000",
    seller_sku="SS-MX-1",
    asin="B0EST01",
    estado="success",
):
    huella = f"fba:{precio}:MXN:{fetched_at}"
    return conn.execute(
        "INSERT INTO estimacion_fee_observation"
        " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
        " fetched_at, observed_at, estado, source_event_id, canonical_input,"
        " context_fingerprint)"
        " VALUES (%s, %s, 'amazon_mx', %s, %s, 'fba', %s, 'MXN',"
        " 15.0000, %s, %s, %s, %s, %s, '{}'::jsonb, %s) RETURNING id",
        (
            oferta_id,
            listing_id,
            seller_sku,
            asin,
            precio,
            fees_estimated_at,
            fetched_at,
            observed_at,
            estado,
            evento,
            huella,
        ),
    ).fetchone()[0]


@_skip_db
def test_grants_por_rol():
    with db_estimacion() as conn:
        if conn.execute("SHOW is_superuser").fetchone()[0] != "on":
            pytest.skip("SET ROLE exige superusuario de prueba")
        _, lid = _sembrar_listing(conn)
        pol = _politica(conn)
        conn.execute("SET ROLE app_admin")
        try:
            n_pol = conn.execute("SELECT count(*) FROM estimacion_politica_version").fetchone()[0]
            assert n_pol == 2  # 0029 siembra una; _politica inserta otra de prueba
            sellada = conn.execute(
                "SELECT label, universo, formula_version, valid_from"
                " FROM estimacion_politica_version"
                " WHERE label = 'amazon_mx_pf_rfc_valid_2026_01'"
            ).fetchone()
            assert sellada == (
                "amazon_mx_pf_rfc_valid_2026_01",
                "amazon_mx/fba",
                "S3",
                date.fromisoformat("2026-01-01"),
            )
        finally:
            conn.execute("RESET ROLE")
        conn.execute("SET ROLE app_ingest")
        try:
            oid = _oferta(conn, lid)
            _fee(conn, oid, lid, evento="evt-fee-grants")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO estimacion_politica_version"
                    " (label, universo, formula_version, settings, valid_from)"
                    " VALUES ('x', 'amazon_mx/fba', 'S3', '{}'::jsonb, '2026-01-01')"
                )
        finally:
            conn.execute("RESET ROLE")
        conn.execute("SET ROLE app_read")
        try:
            n_oferta = conn.execute(
                "SELECT count(*) FROM estimacion_oferta_observation"
            ).fetchone()[0]
            assert n_oferta == 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _oferta(conn, lid, evento="evt-read")
        finally:
            conn.execute("RESET ROLE")
        conn.execute("SET ROLE app_decide")
        try:
            n_fee = conn.execute("SELECT count(*) FROM estimacion_fee_observation").fetchone()[0]
            assert n_fee == 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _oferta(conn, lid, evento="evt-decide")
        finally:
            conn.execute("RESET ROLE")
        assert pol > 0


@_skip_db
def test_append_only_y_truncate_bloqueados():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        pol = _politica(conn)
        _oferta(conn, lid)
        for sql in (
            "UPDATE estimacion_oferta_observation SET price_amount = 1",
            "DELETE FROM estimacion_oferta_observation",
        ):
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(sql)
        # Oferta y politica tienen FK entrantes: PostgreSQL las rechaza antes
        # del trigger. Escenario no tiene dependientes y alcanza el candado.
        assert pol > 0
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE estimacion_escenario")


@_skip_db
def test_dedupe_evento_fuente_sin_rejuvenecer():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        _oferta(conn, lid, evento="evt-dup", observed_at=OBS)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _oferta(conn, lid, evento="evt-dup", observed_at=OBS2)
        _oferta(
            conn,
            lid,
            evento="evt-nuevo",
            fetched_at=FETCH2,
            observed_at=OBS2,
            precio="120.0000",
        )
        assert conn.execute("SELECT count(*) FROM estimacion_oferta_observation").fetchone()[0] == 2


@_skip_db
def test_source_event_id_obligatorio():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO estimacion_oferta_observation"
                " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
                " fetched_at, observed_at, canonical_input, context_fingerprint)"
                " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 116.0000, 'MXN',"
                " %s, %s, '{}'::jsonb, 'huella')",
                (lid, FETCH, OBS),
            )
        oid = _oferta(conn, lid, evento="evt-oferta-fee-null")
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO estimacion_fee_observation"
                " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
                " quoted_price_amount, quoted_price_currency, fetched_at, observed_at,"
                " estado, canonical_input, context_fingerprint, error_code)"
                " VALUES (%s, %s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 116.0000, 'MXN',"
                " %s, %s, 'error', '{}'::jsonb, 'huella', 'API_FAIL')",
                (oid, lid, FETCH, OBS),
            )


@_skip_db
def test_fecha_fuente_futura_rechazada():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _oferta(conn, lid, fetched_at="2026-09-09 00:00:00+00", observed_at=OBS)


@_skip_db
def test_oferta_listing_identidad_incoherente_rechazada():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _oferta(conn, lid, seller_sku="SKU-INCORRECTO", evento="evt-oferta-bad-sku")


@_skip_db
def test_fee_no_casa_oferta_rechazado():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        oid = _oferta(conn, lid, evento="evt-oferta-fee-mismatch")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO estimacion_fee_observation"
                " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
                " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
                " fetched_at, observed_at, estado, source_event_id, canonical_input,"
                " context_fingerprint)"
                " VALUES (%s, %s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 999.0000, 'MXN',"
                " 15.0000, %s, %s, %s, 'success', 'evt-fee-mismatch', '{}'::jsonb, %s)",
                (oid, lid, FETCH, FETCH, OBS, f"fba:999.0000:MXN:{FETCH}"),
            )


@_skip_db
def test_fee_success_temporal_incoherente_rechazado():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        oid = _oferta(conn, lid, evento="evt-oferta-fee-temporal", observed_at=OBS)
        with pytest.raises(psycopg.errors.CheckViolation):
            _fee(
                conn,
                oid,
                lid,
                observed_at=OBS2,
                fees_estimated_at=FETCH2,
                evento="evt-fee-antes-fetched",
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            _fee(
                conn,
                oid,
                lid,
                observed_at=OBS,
                fees_estimated_at=OBS_FUTURO,
                evento="evt-fee-despues-observed",
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO estimacion_fee_observation"
                " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
                " quoted_price_amount, quoted_price_currency, total_fees,"
                " fetched_at, observed_at, estado, source_event_id, canonical_input,"
                " context_fingerprint)"
                " VALUES (%s, %s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 116.0000, 'MXN',"
                " 15.0000, %s, %s, 'success', 'evt-fee-sin-estimated', '{}'::jsonb, %s)",
                (oid, lid, FETCH, OBS2, f"fba:116.0000:MXN:{FETCH}"),
            )
        oid_tarde = _oferta(conn, lid, evento="evt-oferta-tarde", observed_at=OBS2)
        with pytest.raises(psycopg.errors.CheckViolation):
            _fee(conn, oid_tarde, lid, observed_at=OBS, evento="evt-fee-oferta-futura")


@_skip_db
def test_escenario_referencias_futuras_rechazadas():
    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        pol = _politica(conn)
        run_id = _corrida_costos(conn)
        cost_id = conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, 40.0000, 'MXN', FALSE, '2026-01-01', %s)"
            " RETURNING id",
            (pid, run_id),
        ).fetchone()[0]
        oid_futuro = _oferta(
            conn, lid, observed_at=OBS2, evento="evt-oferta-futura", fetched_at=FETCH2
        )
        oid = _oferta(conn, lid, observed_at=OBS, evento="evt-oferta-corte")
        fid = _fee(conn, oid, lid, observed_at=OBS, evento="evt-fee-corte")
        huella = f"fba:116.0000:MXN:{FETCH}"
        huella_futura = f"fba:116.0000:MXN:{FETCH2}"
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO estimacion_escenario"
                " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
                " politica_version_id, formula_version, oferta_observation_id,"
                " canonical_input, context_fingerprint, estado)"
                " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
                " %s, 'I-C-F-L-R-v1', %s, '{}'::jsonb, %s, 'incompleta')",
                (lid, VALORACION, OBS, pol, oid_futuro, huella_futura),
            )
        fid_futuro = _fee(
            conn,
            oid,
            lid,
            observed_at=OBS2,
            evento="evt-fee-futuro",
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO estimacion_escenario"
                " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
                " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
                " canonical_input, context_fingerprint, estado)"
                " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
                " %s, 'I-C-F-L-R-v1', %s, %s, '{}'::jsonb, %s, 'incompleta')",
                (lid, VALORACION, OBS, pol, oid, fid_futuro, huella),
            )
        _, lid_otro = _sembrar_listing(conn, odoo_sku="P-OTRO", asin="B0EST02", seller_sku="SS-2")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO estimacion_escenario"
                " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
                " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
                " sku_cost_id, canonical_input, context_fingerprint, estado)"
                " VALUES (%s, 'amazon_mx', 'SS-2', 'B0EST02', 'fba', %s, %s,"
                " %s, 'I-C-F-L-R-v1', %s, %s, %s, '{}'::jsonb, %s, 'incompleta')",
                (lid_otro, VALORACION, OBS2, pol, oid, fid, cost_id, huella),
            )


@_skip_db
@pytest.mark.parametrize(
    "estado",
    ("incompleta", "desactualizada", "identidad_ambigua"),
)
def test_escenario_no_disponible_exige_totales_null(estado: str):
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        pol = _politica(conn)
        oid = _oferta(conn, lid, evento=f"evt-oferta-{estado}")
        huella = f"fba:116.0000:MXN:{FETCH}"
        conn.execute(
            "INSERT INTO estimacion_escenario"
            " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
            " politica_version_id, formula_version, oferta_observation_id, estado, motivos,"
            " canonical_input, context_fingerprint, source_event_id)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
            " %s, 'I-C-F-L-R-v1', %s, %s, '[]'::jsonb, '{}'::jsonb, %s, %s)",
            (lid, VALORACION, OBS, pol, oid, estado, huella, f"evt-escenario-{estado}"),
        )
        fila = conn.execute(
            "SELECT contribucion, contribucion_pct, moneda FROM estimacion_escenario"
            " WHERE estado = %s",
            (estado,),
        ).fetchone()
        assert fila == (None, None, None)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO estimacion_escenario"
                " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
                " politica_version_id, formula_version, oferta_observation_id, moneda,"
                " contribucion, contribucion_pct, estado, canonical_input, context_fingerprint,"
                " source_event_id)"
                " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
                " %s, 'I-C-F-L-R-v1', %s, 'MXN', 1.0000, 1.0000, %s, '{}'::jsonb, %s, %s)",
                (lid, VALORACION, OBS2, pol, oid, estado, huella, f"evt-total-{estado}"),
            )


@_skip_db
def test_fx_rate_precision_round_trip():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        pol = _politica(conn)
        oid = _oferta(conn, lid, evento="evt-oferta-fx")
        tasa = Decimal("17.12345678")
        huella = f"fba:116.0000:MXN:{FETCH}"
        conn.execute(
            "INSERT INTO estimacion_escenario"
            " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
            " politica_version_id, formula_version, oferta_observation_id,"
            " fx_rate_date, fx_base, fx_quote, fx_rate, fx_source,"
            " estado, canonical_input, context_fingerprint,"
            " source_event_id)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
            " %s, 'S3', %s, '2026-09-08', 'USD', 'MXN', %s, 'test',"
            " 'incompleta', '{}'::jsonb, %s, 'evt-escenario-fx')",
            (lid, VALORACION, OBS, pol, oid, tasa, huella),
        )
        leida = conn.execute(
            "SELECT fx_rate FROM estimacion_escenario WHERE oferta_observation_id = %s",
            (oid,),
        ).fetchone()[0]
        assert Decimal(str(leida)) == tasa


@_skip_db
def test_escenario_as_of_y_incompleto_sin_cero_inventado():
    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        pol = _politica(conn)
        run_id = _corrida_costos(conn)
        cost_id = conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, 40.0000, 'MXN', FALSE, '2026-01-01', %s)"
            " RETURNING id",
            (pid, run_id),
        ).fetchone()[0]
        oid = _oferta(conn, lid, observed_at=OBS, evento="evt-oferta-asof")
        fid = _fee(conn, oid, lid, observed_at=OBS, evento="evt-fee-asof")
        huella = f"fba:116.0000:MXN:{FETCH}"
        conn.execute(
            "INSERT INTO estimacion_escenario"
            " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
            " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
            " sku_cost_id, costo_validation_run_id, costo_validated_at,"
            " fx_rate_date, fx_base, fx_quote, fx_rate, fx_source,"
            " moneda, contribucion, contribucion_pct, estado, motivos, componentes,"
            " canonical_input, context_fingerprint, source_event_id)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
            " %s, 'S3', %s, %s, %s, %s, %s, NULL, NULL, NULL, NULL, NULL,"
            " 'MXN', 42.5000, 42.5000, 'disponible', '[]'::jsonb, '[]'::jsonb,"
            " '{}'::jsonb, %s, 'evt-escenario-asof-disponible')",
            (lid, VALORACION, OBS, pol, oid, fid, cost_id, run_id, FETCH2, huella),
        )
        conn.execute(
            "INSERT INTO estimacion_escenario"
            " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
            " politica_version_id, formula_version, oferta_observation_id,"
            " estado, motivos, componentes, canonical_input, context_fingerprint, source_event_id)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', %s, %s,"
            " %s, 'I-C-F-L-R-v1', %s, 'incompleta',"
            " '[\"fee_ausente\"]'::jsonb, '[]'::jsonb, '{}'::jsonb, %s,"
            " 'evt-escenario-asof-incompleto')",
            (lid, VALORACION, OBS2, pol, oid, huella),
        )
        fila = conn.execute(
            "SELECT contribucion, estado FROM estimacion_escenario"
            " WHERE observed_at <= %s ORDER BY observed_at DESC LIMIT 1",
            (OBS,),
        ).fetchone()
        assert fila[1] == "disponible"
        assert Decimal(str(fila[0])) == Decimal("42.5000")
        incompleta = conn.execute(
            "SELECT contribucion, contribucion_pct, moneda FROM estimacion_escenario"
            " WHERE estado = 'incompleta'"
        ).fetchone()
        assert incompleta == (None, None, None)


@_skip_db
def test_politica_vigencia_coherente():
    with db_estimacion() as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO estimacion_politica_version"
            " (label, universo, formula_version, settings, valid_from, valid_to)"
            " VALUES ('x', 'amazon_mx/fba', 'S3', '{}'::jsonb, '2026-06-01', '2026-01-01')"
        )


@_skip_db
def test_reversa_preserva_hechos_y_revoca_ingesta():
    with db_estimacion() as conn:
        _, lid = _sembrar_listing(conn)
        _politica(conn)
        _oferta(conn, lid, evento="evt-reversa")
        antes = conn.execute("SELECT count(*) FROM estimacion_oferta_observation").fetchone()[0]
        conn.execute("SELECT estimacion_venta_reversa()")
        despues = conn.execute("SELECT count(*) FROM estimacion_oferta_observation").fetchone()[0]
        assert antes == despues == 1
        if conn.execute("SHOW is_superuser").fetchone()[0] != "on":
            pytest.skip("SET ROLE exige superusuario de prueba")
        conn.execute("SET ROLE app_ingest")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _oferta(conn, lid, evento="evt-post-reversa")
        finally:
            conn.execute("RESET ROLE")
