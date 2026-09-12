"""Banco de pruebas de FABRICA 02 (F2, A.0): fixture unificado + humo.

`ORDEN_F2` = 0001, 0002, 0003, 0004, 0013-0019 (la migracion de F2 es A.2:
el hueco queda preparado al final de la tupla). Reutiliza los helpers de
harvest de `tests/test_apply_harvest.py` (`_semilla`, `_handler_harvest`,
`_aplicador`, `_encola_fila`): NO los duplica. Aporta `_semilla_grupo`, un
LIST que honra `adGroupIdFilter`/`nextToken` y fallo por hermana.

Sin tests de comportamiento todavia (llegan en A.1/A.3). DoD de A.0: el
fixture levanta y siembra; un test humo por helper;
`tests/test_architecture.py` verde.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_apply_harvest import (
    _aplicador,
    _decision_harvest,
    _encola_fila,
    _handler_harvest,
    _semilla,
)
from test_schema import _postgres_obligatorio_ausente, _test_dsn

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Fixture unificado de F2 (A.0)
# ---------------------------------------------------------------------------

ORDEN_F2 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0003_goal_bounds_explicit.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0013_entidad_inerte.sql",
    "0014_keyword_archivo_manual.sql",
    "0015_target_margen_plataforma.sql",
    "0016_target_margen_correcciones.sql",
    "0017_first_seen_at.sql",
    "0018_fabrica_campanas.sql",
    "0019_fabrica_grupo_publicacion_v2.sql",
    # Hueco preparado: la migracion de F2 (A.2, numero al aplicar) se agrega
    # al final de esta tupla; nada de F2 depende de migraciones 0005-0012 ni
    # 0020+.
)


@contextmanager
def db_f2(prefijo: str):
    """DB temporal con ORDEN_F2; yields conn autocommit (misma maquinaria que
    `db_fabrica` de test_fabrica_migracion y `_db_temporal` de
    test_apply_harvest)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        root = Path(__file__).resolve().parents[1] / "migrations"
        for nombre in ORDEN_F2:
            conn.execute((root / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


ROLES_F2 = (
    "auto_discovery",
    "category_phrase",
    "product_targeting",
    "category_broad",
    "category_exact",
)


def _semilla_grupo(
    conn,
    *,
    platform: str = "amazon_us",
    tipo_producto: str = "collar_perro",
    bid: str = "11.62",
    mode: str = "live",
    con_terna: bool = True,
    con_goals: bool = True,
) -> dict:
    """Grupo F2 completo: lote + grupo + 5 campanas (una por rol) con su ad
    group hijo + 5 filas en `campana_grupo_rol` + (`con_goals`) 5 goals de
    campana con la terna a la exacta del grupo (como sembro F1;
    `con_terna=False` deja los tres campos NULL). Externos 61xx (campanas) /
    62xx (ad groups) para no chocar con los 7xxx/8xxx de `_semilla`. Moneda
    por plataforma (regla 4). Devuelve grupo_id, lote y por rol
    {camp, ag, camp_ext, ag_ext}."""
    moneda = "USD" if platform == "amazon_us" else "MXN"
    lote = f"fabrica-f2-{platform}-{tipo_producto}-t1"
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, %s, %s, 'Base F2', 'go', 'h',"
        " '{}'::jsonb, 'live', 'applied')",
        (lote, platform, tipo_producto),
    )
    grupo_id = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
        " VALUES (%s, %s, 'Base F2', %s, 20, 20, 0.5, 'humo', 'go') RETURNING id",
        (platform, tipo_producto, lote),
    ).fetchone()[0]
    roles: dict[str, dict] = {}
    for i, rol in enumerate(ROLES_F2):
        camp_ext, ag_ext = f"61{i:02d}", f"62{i:02d}"
        camp = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
            " RETURNING id",
            (platform, camp_ext),
        ).fetchone()[0]
        ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
            (platform, ag_ext, camp),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
            " VALUES (%s, %s, %s, %s)",
            (grupo_id, rol, camp, ag),
        )
        roles[rol] = {"camp": camp, "ag": ag, "camp_ext": camp_ext, "ag_ext": ag_ext}
    exacta = roles["category_exact"]
    if not con_goals:
        return {"grupo_id": grupo_id, "lote": lote, "roles": roles, "json": Json({})}
    piso, techo = ("0.10", "2.50") if moneda == "USD" else ("1.00", "45.00")
    for par in roles.values():
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
            " bid_floor, bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
            " harvest_default_bid, enabled, mode) VALUES ('campaign', %s, 20, %s, %s, %s,"
            " %s, %s, %s, true, %s)",
            (
                par["camp"],
                piso,
                techo,
                moneda,
                exacta["camp_ext"] if con_terna else None,
                exacta["ag_ext"] if con_terna else None,
                Decimal(bid) if con_terna else None,
                mode,
            ),
        )
    return {"grupo_id": grupo_id, "lote": lote, "roles": roles, "json": Json({})}


def _request_lista(path: str, body: dict) -> httpx.Request:
    return httpx.Request(
        "POST",
        f"https://fake-ads{path}",
        content=json.dumps(body).encode(),
    )


def _request_post(path: str, objeto: dict) -> httpx.Request:
    contenedor = "negativeKeywords" if "negative" in path else "keywords"
    return httpx.Request(
        "POST",
        f"https://fake-ads{path}",
        content=json.dumps({contenedor: [objeto]}).encode(),
    )


# ---------------------------------------------------------------------------
# Humo del fixture
# ---------------------------------------------------------------------------


@_skip_db
def test_f2_fixture_levanta_con_maquinaria_de_harvest_y_grupos():
    """El fixture aplica ORDEN_F2: harvest_job (0001/0002), goals con terna
    (0003), product_ad (0004), inerte/archivo (0013/0014) y grupo,
    biblioteca y excepcion (0018) con publicaciones v2 (0019)."""
    with db_f2("orbit_f2_orden") as conn:
        tablas = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN"
            " ('harvest_job', 'ads_optimizer_goal', 'campana_grupo_rol',"
            " 'keyword_biblioteca', 'negative_biblioteca', 'harvest_excepcion')"
        ).fetchall()
        assert {t[0] for t in tablas} == {
            "harvest_job",
            "ads_optimizer_goal",
            "campana_grupo_rol",
            "keyword_biblioteca",
            "negative_biblioteca",
            "harvest_excepcion",
        }
        assert "0019_fabrica_grupo_publicacion_v2.sql" in ORDEN_F2


@_skip_db
def test_f2_semilla_harvest_reutilizada_siembra_goal_con_terna():
    """Humo de `_semilla` (vive en test_apply_harvest, no duplicada):
    campana, ad group, keyword y goal de plataforma con destino."""
    with db_f2("orbit_f2_semilla") as conn:
        ids = _semilla(conn)
        fila = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid"
            " FROM ads_optimizer_goal WHERE scope = 'platform'"
        ).fetchone()
        assert fila is not None and fila[0] is not None and fila[1] is not None
        n = conn.execute("SELECT count(*) FROM ad_entity").fetchone()[0]
        assert n == 3, ids


@_skip_db
def test_f2_encola_y_aplicador_reutilizados():
    """Humo de `_encola_fila` + `_aplicador`: la fila queda pending_veto y
    el aplicador se construye sin HTTP."""
    with db_f2("orbit_f2_cola") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        qid = _encola_fila(conn, dec, ids["ag"], term="humo f2")
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert estado == "pending_veto"
        handler, _vistos = _handler_harvest()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        assert aplicador is not None


@_skip_db
def test_f2_semilla_grupo_cinco_roles_con_terna_a_la_exacta():
    """Humo de `_semilla_grupo`: grupo, 5 roles con campana + ad group hijo
    y goals de campana con terna a la exacta del grupo."""
    with db_f2("orbit_f2_grupo") as conn:
        g = _semilla_grupo(conn)
        roles = conn.execute(
            "SELECT rol FROM campana_grupo_rol WHERE grupo_id = %s ORDER BY rol",
            (g["grupo_id"],),
        ).fetchall()
        assert sorted(r[0] for r in roles) == [
            "auto_discovery",
            "category_broad",
            "category_exact",
            "category_phrase",
            "product_targeting",
        ]
        for rol, par in g["roles"].items():
            kinds = conn.execute(
                "SELECT kind FROM ad_entity WHERE id IN (%s, %s) ORDER BY id",
                (par["camp"], par["ag"]),
            ).fetchall()
            assert {k[0] for k in kinds} == {"campaign", "ad_group"}, rol
            padre = conn.execute(
                "SELECT parent_id FROM ad_entity WHERE id = %s", (par["ag"],)
            ).fetchone()[0]
            assert padre == par["camp"], rol
        ternas = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id"
            " FROM ads_optimizer_goal WHERE scope = 'campaign'"
        ).fetchall()
        assert len(ternas) == 5
        exacta = g["roles"]["category_exact"]
        assert all(t[0] == exacta["camp_ext"] and t[1] == exacta["ag_ext"] for t in ternas)


# ---------------------------------------------------------------------------
# Humo del LIST con filtro/paginacion y del fallo por hermana
# ---------------------------------------------------------------------------


def test_f2_list_honra_adgroupidfilter():
    """El LIST filtra por `adGroupIdFilter.include`: con negativos en dos
    ad groups, pedir uno devuelve solo ese (sin filtro, el store entero)."""
    negativos = [
        {
            "adGroupId": "62001",
            "campaignId": "61001",
            "keywordId": "n-1",
            "keywordText": "uno",
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        },
        {
            "adGroupId": "62002",
            "campaignId": "61002",
            "keywordId": "n-2",
            "keywordText": "dos",
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        },
    ]
    handler, _vistos = _handler_harvest(negatives=negativos)
    resp = handler(_request_lista("/sp/negativeKeywords/list", {}))
    assert resp.json()["totalResults"] == 2
    resp = handler(
        _request_lista("/sp/negativeKeywords/list", {"adGroupIdFilter": {"include": ["62001"]}})
    )
    cuerpo = resp.json()
    assert cuerpo["totalResults"] == 1
    assert [n["keywordId"] for n in cuerpo["negativeKeywords"]] == ["n-1"]


def test_f2_list_pagina_con_nexttoken():
    """Con `page_size=1` y dos items: la primera pagina trae uno + nextToken,
    la segunda (con el token) trae el otro sin token."""
    negativos = [
        {
            "adGroupId": "62001",
            "campaignId": "61001",
            "keywordId": "n-1",
            "keywordText": "uno",
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        },
        {
            "adGroupId": "62001",
            "campaignId": "61001",
            "keywordId": "n-2",
            "keywordText": "dos",
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        },
    ]
    handler, _vistos = _handler_harvest(negatives=negativos, page_size=1)
    primera = handler(_request_lista("/sp/negativeKeywords/list", {})).json()
    assert len(primera["negativeKeywords"]) == 1
    assert primera.get("nextToken"), "la primera pagina anuncia que hay mas"
    segunda = handler(
        _request_lista("/sp/negativeKeywords/list", {"nextToken": primera["nextToken"]})
    ).json()
    assert len(segunda["negativeKeywords"]) == 1
    assert not segunda.get("nextToken"), "la ultima pagina no anuncia mas"
    assert (
        primera["negativeKeywords"][0]["keywordId"] != segunda["negativeKeywords"][0]["keywordId"]
    )


def test_f2_fallo_por_hermana_solo_tumba_su_adgroup():
    """`fallo_post_negative_por_adgroup` responde el status pedido SOLO en
    ese adGroupId; la hermana sana recibe su 207 con id."""
    handler, _vistos = _handler_harvest(fallo_post_negative_por_adgroup={"62001": 400})

    def _neg(adgroup: str) -> dict:
        return {
            "adGroupId": adgroup,
            "campaignId": "61001",
            "keywordText": "termino hermana",
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }

    mala = handler(_request_post("/sp/negativeKeywords", _neg("62001")))
    assert mala.status_code == 400
    buena = handler(_request_post("/sp/negativeKeywords", _neg("62002")))
    assert buena.status_code == 207
    assert buena.json()["negativeKeywords"]["success"][0]["negativeKeywordId"]
