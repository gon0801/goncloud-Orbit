"""Pruebas de 0101 (adopcion unica de grupos legado) y harvest con hermanas parciales.

Cubre:
- Sintaxis SQL (pglast) y cabecera `-- sonda-aplicada:` de 0101 y su reversa.
- Invariantes de esquema sobre el esquema canonico completo (todas las
  migraciones): tipo='legado' exige lote NULL; fabrica5/impulso siguen
  exigiendo lote NOT NULL; reversa devuelve el esquema exacto.
- Adopcion condicional de los 5 grupos legado (AC, AGM2M, AGMX, ASEM, USPerNog):
  cero filas en DB vacia, fail-closed ante grupo parcial en ad_entity, adopcion
  completa de 5 grupos y 17 roles + resolucion por `harvest_destino`.
- Regresion de `_roster_hermanas` y flujo completo de harvest + biblioteca +
  reversa para grupos legado de 4 roles (sin `category_broad`) y 2 roles
  (USPerNog), manteniendo el rechazo fail-closed para `fabrica5` incompleto.
"""

from __future__ import annotations

import json
from pathlib import Path

import pglast
import psycopg
import pytest
from helpers.pg import db_desde_template
from test_fabrica_f2 import _skip_db
from test_fabrica_f2_hermanas import _corre_harvest_grupo, _grupo_listo, _job_de
from test_schema import _test_dsn

from app.apply_harvest import plan_reversa_harvest
from app.apply_harvest_list import ROLES_DISCOVERY
from app.optimizer import harvest_destino
from app.optimizer.harvest_destino import DestinoHarvest, SaltoHarvest

ROOT = Path(__file__).resolve().parent.parent
IDA_0101 = ROOT / "migrations" / "0101_adopta_grupos_legado.sql"
REV_0101 = ROOT / "migrations" / "0101_reversa_adopta_grupos_legado.sql"

GRUPOS_LEGADO_PROD = (
    ("AC", "amazon_mx", "category_exact", "36419758709592", "553629449717842"),
    ("AC", "amazon_mx", "auto_discovery", "6725297929896", "449528234398547"),
    ("AC", "amazon_mx", "category_phrase", "1928166522818", "422648909272683"),
    ("AC", "amazon_mx", "product_targeting", "170680510319588", "292842646731052"),
    ("AGM2M", "amazon_mx", "category_exact", "150524793905747", "231642806323471"),
    ("AGM2M", "amazon_mx", "auto_discovery", "186754400178437", "478068950298503"),
    ("AGM2M", "amazon_mx", "category_phrase", "95461755558026", "100861701218603"),
    ("AGM2M", "amazon_mx", "product_targeting", "104269918219765", "168634033266847"),
    ("AGMX", "amazon_mx", "category_exact", "77497572464498", "420478206173588"),
    ("AGMX", "amazon_mx", "auto_discovery", "138540322850772", "476942043430531"),
    ("AGMX", "amazon_mx", "category_phrase", "79296609147301", "353798003957642"),
    ("AGMX", "amazon_mx", "product_targeting", "103896791234989", "509300461895675"),
    ("ASEM", "amazon_mx", "category_exact", "240839688795909", "436018660372463"),
    ("ASEM", "amazon_mx", "auto_discovery", "26722751353851", "561594597563774"),
    ("ASEM", "amazon_mx", "category_phrase", "183722893302577", "398439659915475"),
    ("USPerNog", "amazon_us", "category_exact", "251723662158466", "522582072501798"),
    ("USPerNog", "amazon_us", "auto_discovery", "140602818838686", "529927551250133"),
)


def _sonda_0101() -> str:
    for linea in IDA_0101.read_text(encoding="utf-8").splitlines()[:5]:
        if linea.startswith("-- sonda-aplicada:"):
            return linea.split(":", 1)[1].strip()
    raise AssertionError("0101 sin cabecera '-- sonda-aplicada:'")


def _aplica_0101(conn: psycopg.Connection) -> None:
    conn.execute(IDA_0101.read_text(encoding="utf-8"))


def _aplica_reversa_0101(conn: psycopg.Connection) -> None:
    conn.execute(REV_0101.read_text(encoding="utf-8"))


def _siembra_entidades_legado(
    conn: psycopg.Connection, filas=GRUPOS_LEGADO_PROD
) -> dict[tuple[str, str], tuple[int, int]]:
    mapa: dict[tuple[str, str], tuple[int, int]] = {}
    for grupo, plat, rol, camp_ext, ag_ext in filas:
        cid = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, name)"
            " VALUES (%s::platform, 'campaign', %s, %s) RETURNING id",
            (plat, camp_ext, f"{grupo} - {rol}"),
        ).fetchone()[0]
        agid = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, name)"
            " VALUES (%s::platform, 'ad_group', %s, %s, %s) RETURNING id",
            (plat, ag_ext, cid, f"{grupo} - {rol} - AG"),
        ).fetchone()[0]
        mapa[(grupo, rol)] = (cid, agid)
    return mapa


def test_migracion_0101_sintaxis_y_sonda():
    assert IDA_0101.exists()
    assert REV_0101.exists()
    assert pglast.parse_sql(IDA_0101.read_text(encoding="utf-8"))
    assert pglast.parse_sql(REV_0101.read_text(encoding="utf-8"))
    assert "campana_grupo_lote_segun_tipo" in _sonda_0101()


@_skip_db
def test_0101_esquema_invariantes_y_reversa():
    sonda = _sonda_0101()
    with db_desde_template("orbit_0101_esq", base=_test_dsn(), autocommit=True) as (conn, _):
        assert conn.execute(sonda).fetchone()[0] is True
        # En DB vacia no siembra ningun grupo legado.
        n_legado = conn.execute(
            "SELECT count(*) FROM campana_grupo WHERE tipo = 'legado'"
        ).fetchone()[0]
        assert n_legado == 0

        # tipo='legado' con lote NULL pasa.
        gid = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_origen,"
            " target_procedencia, go_literal, tipo)"
            " VALUES ('amazon_mx', 'kit_arras', 'prueba_legado', NULL, 20.00, NULL, NULL,"
            " 'manual_lanzamiento', 'test', 'go test', 'legado') RETURNING id"
        ).fetchone()[0]
        assert gid > 0

        # fabrica5 con lote NULL falla; tipo fuera de catalogo falla; legado con lote falla.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_origen,"
                " target_procedencia, go_literal, tipo)"
                " VALUES ('amazon_mx', 'kit_arras', 'bad_fab5', NULL, 20.00, NULL, NULL,"
                " 'manual_lanzamiento', 'test', 'go test', 'fabrica5')"
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_origen,"
                " target_procedencia, go_literal, tipo)"
                " VALUES ('amazon_mx', 'kit_arras', 'bad_tipo', NULL, 20.00, NULL, NULL,"
                " 'manual_lanzamiento', 'test', 'go test', 'inventado')"
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_origen,"
                " target_procedencia, go_literal, tipo)"
                " VALUES ('amazon_mx', 'kit_arras', 'bad_legado_lote', 'lote-x', 20.00,"
                " NULL, NULL, 'manual_lanzamiento', 'test', 'go test', 'legado')"
            )

        _aplica_reversa_0101(conn)
        assert conn.execute(sonda).fetchone()[0] is False
        assert conn.execute("SELECT count(*) FROM campana_grupo").fetchone()[0] == 0

        # Tras reversa, lote NULL vuelve a violar NOT NULL y tipo='legado' vuelve a violar CHECK.
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_origen,"
                " target_procedencia, go_literal, tipo)"
                " VALUES ('amazon_mx', 'kit_arras', 'post_rev_null', NULL, 20.00, NULL, NULL,"
                " 'manual_lanzamiento', 'test', 'go test', 'fabrica5')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_origen,"
                " target_procedencia, go_literal, tipo)"
                " VALUES ('amazon_mx', 'kit_arras', 'post_rev_tipo', 'lote-inexistente', 20.00,"
                " NULL, NULL, 'manual_lanzamiento', 'test', 'go test', 'legado')"
            )


@_skip_db
def test_0101_adopta_cinco_grupos_y_resuelve_destinos():
    with db_desde_template("orbit_0101_adopta", base=_test_dsn(), autocommit=True) as (conn, _):
        _aplica_reversa_0101(conn)
        mapa = _siembra_entidades_legado(conn)
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, bid_floor, bid_ceiling,"
            " bid_currency, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid,"
            " enabled, mode)"
            " VALUES ('platform', 'amazon_mx', 1.0, 45.0, 'MXN', 'legacy_c', 'legacy_ag',"
            " 2.50, true, 'live'),"
            " ('platform', 'amazon_us', 0.1, 2.5, 'USD', 'legacy_cu', 'legacy_agu',"
            " 0.68, true, 'live')"
        )

        _aplica_0101(conn)

        grupos = conn.execute(
            "SELECT nombre_base, platform::text, tipo, lote, tipo_producto"
            " FROM campana_grupo ORDER BY platform, nombre_base"
        ).fetchall()
        assert grupos == [
            ("AC", "amazon_mx", "legado", None, "kit_arras"),
            ("AGM2M", "amazon_mx", "legado", None, "kit_arras"),
            ("AGMX", "amazon_mx", "legado", None, "kit_arras"),
            ("ASEM", "amazon_mx", "legado", None, "kit_arras"),
            ("USPerNog", "amazon_us", "legado", None, "kit_arras"),
        ]
        assert conn.execute("SELECT count(*) FROM campana_grupo_rol").fetchone()[0] == 17

        # Cada discovery resuelve a la Exact de SU propio grupo (resuelto_por='grupo').
        for grupo, plat, exact_camp_ext, exact_ag_ext, disc_rol in (
            ("AC", "amazon_mx", "36419758709592", "553629449717842", "auto_discovery"),
            ("AGM2M", "amazon_mx", "150524793905747", "231642806323471", "auto_discovery"),
            ("AGMX", "amazon_mx", "77497572464498", "420478206173588", "category_phrase"),
            ("ASEM", "amazon_mx", "240839688795909", "436018660372463", "auto_discovery"),
            ("USPerNog", "amazon_us", "251723662158466", "522582072501798", "auto_discovery"),
        ):
            cid, _ = mapa[(grupo, disc_rol)]
            dest = harvest_destino.resolver_destino(conn, plat, cid)
            assert isinstance(dest, DestinoHarvest), (grupo, dest)
            assert dest.resuelto_por == harvest_destino.RESUELTO_GRUPO
            assert dest.campaign_external == exact_camp_ext
            assert dest.ad_group_external == exact_ag_ext

            exact_cid, _ = mapa[(grupo, "category_exact")]
            salto = harvest_destino.resolver_destino(conn, plat, exact_cid)
            assert isinstance(salto, SaltoHarvest)
            assert salto.motivo == "origen_es_destino"

        # Reversa limpia todos los roles y grupos legado sin error.
        _aplica_reversa_0101(conn)
        assert conn.execute("SELECT count(*) FROM campana_grupo_rol").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM campana_grupo").fetchone()[0] == 0


@_skip_db
def test_0101_aborta_fail_closed_si_grupo_parcial_en_ad_entity():
    with db_desde_template("orbit_0101_parc", base=_test_dsn(), autocommit=True) as (conn, _):
        _aplica_reversa_0101(conn)
        # Solo sembramos 2 de los 4 roles de AC.
        _siembra_entidades_legado(conn, GRUPOS_LEGADO_PROD[:2])
        with pytest.raises(psycopg.errors.RaiseException, match="incompleto"):
            _aplica_0101(conn)


@_skip_db
def test_grupo_legado_cuatro_roles_sin_broad_niega_dos_hermanas_y_revierte():
    """Un grupo legado sin category_broad (como AC/AGM2M/AGMX) niega solo las
    hermanas presentes, escribe biblioteca y revierte en orden canonico."""
    with db_desde_template("orbit_legado_4r", base=_test_dsn(), autocommit=True) as (conn, _):
        setup = _grupo_listo(conn, origen_rol="auto_discovery")
        gid = setup["grupo_id"]
        conn.execute(
            "UPDATE campana_grupo SET tipo = 'legado', lote = NULL WHERE id = %s",
            (gid,),
        )
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = 'category_broad'",
            (gid,),
        )

        sal = _corre_harvest_grupo(conn, setup)
        job = _job_de(conn, sal["dec"])
        assert job["fase"] == "done", job
        assert list(job["ext"]["hermanas_objetivo"].keys()) == [
            "category_phrase",
            "product_targeting",
        ], job["ext"]
        assert sorted(job["ext"]["hermanas"].keys()) == [
            "category_phrase",
            "product_targeting",
        ]
        bib = conn.execute("SELECT tipo_producto, texto FROM keyword_biblioteca").fetchall()
        assert bib == [("collar_perro", "termino hermana f2")]

        job_id = conn.execute(
            "SELECT id FROM harvest_job WHERE decision_id = %s", (sal["dec"],)
        ).fetchone()[0]
        _plat, _term, _dec_id, pasos = plan_reversa_harvest(conn, job_id)
        assert [(p.clase, p.rol) for p in pasos] == [
            ("keyword", None),
            ("negative", "category_phrase"),
            ("negative", "product_targeting"),
            ("negative", None),
        ]


@_skip_db
def test_grupo_legado_dos_roles_uspernog_cierra_done_sin_hermanas():
    """Un grupo legado de dos roles (USPerNog: auto_discovery + category_exact)
    cierra done con hermanas_objetivo={} y escribe biblioteca sin HTTP extra."""
    with db_desde_template("orbit_legado_2r", base=_test_dsn(), autocommit=True) as (conn, _):
        setup = _grupo_listo(conn, origen_rol="auto_discovery")
        gid = setup["grupo_id"]
        conn.execute(
            "UPDATE campana_grupo SET tipo = 'legado', lote = NULL WHERE id = %s",
            (gid,),
        )
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s"
            " AND rol IN ('category_broad', 'category_phrase', 'product_targeting')",
            (gid,),
        )

        sal = _corre_harvest_grupo(conn, setup)
        job = _job_de(conn, sal["dec"])
        assert job["fase"] == "done", job
        assert job["ext"]["hermanas_objetivo"] == {}
        muts = [r for r in sal["vistos"] if r.method == "POST" and not r.url.path.endswith("/list")]
        assert [r.url.path for r in muts] == ["/sp/negativeKeywords", "/sp/keywords"]
        post_kw = next(r for r in muts if r.url.path == "/sp/keywords")
        assert (
            json.loads(post_kw.content)["keywords"][0]["adGroupId"]
            == setup["roles"]["category_exact"]["ag_ext"]
        )


@_skip_db
def test_grupo_fabrica5_incompleto_sigue_fallando_tras_0101():
    """Guardia de regresion: un grupo fabrica5 sin un rol discovery sigue
    fallando fail-closed con entidad_incompleta."""
    with db_desde_template("orbit_legado_fab5", base=_test_dsn(), autocommit=True) as (conn, _):
        setup = _grupo_listo(conn, origen_rol="auto_discovery")
        faltante = next(r for r in ROLES_DISCOVERY if r != "auto_discovery")
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = %s",
            (setup["grupo_id"], faltante),
        )
        sal = _corre_harvest_grupo(conn, setup)
        job = _job_de(conn, sal["dec"])
        assert job["fase"] == "failed", job
        assert [a.motivo for a in sal["res"].alertas] == ["entidad_incompleta"]
