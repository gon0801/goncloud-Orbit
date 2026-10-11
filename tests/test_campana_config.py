"""Configuracion de campana (BIDS 02, V.1).

`config_de_payload` es la frontera: valida forma y tipos del item de
`/sp/campaigns/list`; item ilegible = None y se cuenta como skip, jamas
ceros inventados. Presupuesto con la moneda del perfil (igual que los
bids). Sin `dynamicBidding`, estrategia y ajustes son None; sin entrada
de un placement, ese ajuste es 0. `guarda_config` es append-only y solo
inserta cuando algo cambio. `config_vigente` lee la ultima fila.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.ads.campana_config import ConfigCampana, config_de_payload, config_vigente, guarda_config

ROOT = Path(__file__).resolve().parents[1]
_SQL61 = (ROOT / "migrations" / "0061_bids02_campana_config.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _item(**cambios):
    base = {
        "campaignId": "93529333080113",
        "name": "X",
        "state": "ENABLED",
        "targetingType": "MANUAL",
        "budget": {"budget": 10.08, "budgetType": "DAILY"},
        "dynamicBidding": {
            "strategy": "LEGACY_FOR_SALES",
            "placementBidding": [{"percentage": 40, "placement": "PLACEMENT_PRODUCT_PAGE"}],
        },
        "offAmazonSettings": {},
    }
    base.update(cambios)
    return base


def test_budget_estrategia_y_placements_salen_tal_cual():
    assert config_de_payload(_item(), "USD") == ConfigCampana(
        campana_externa="93529333080113",
        presupuesto_diario=Decimal("10.08"),
        moneda="USD",
        estrategia_puja="LEGACY_FOR_SALES",
        ajuste_top_pct=0,
        ajuste_resto_pct=0,
        ajuste_producto_pct=40,
        fuera_de_amazon=None,
    )


def test_los_tres_placements_mapean_cada_uno_a_su_ajuste():
    item = _item(
        dynamicBidding={
            "strategy": "MANUAL",
            "placementBidding": [
                {"percentage": 9, "placement": "PLACEMENT_TOP"},
                {"percentage": 5, "placement": "PLACEMENT_REST_OF_SEARCH"},
                {"percentage": 40, "placement": "PLACEMENT_PRODUCT_PAGE"},
            ],
        }
    )
    config = config_de_payload(item, "MXN")
    assert (config.ajuste_top_pct, config.ajuste_resto_pct, config.ajuste_producto_pct) == (
        9,
        5,
        40,
    )


def test_sin_dynamicBidding_ajustes_y_estrategia_none():
    item = _item()
    del item["dynamicBidding"]
    config = config_de_payload(item, "USD")
    assert config.estrategia_puja is None
    assert (config.ajuste_top_pct, config.ajuste_resto_pct, config.ajuste_producto_pct) == (
        None,
        None,
        None,
    )
    assert config.presupuesto_diario == Decimal("10.08")
    nulo = config_de_payload(_item(dynamicBidding=None), "USD")
    assert nulo.estrategia_puja is None
    assert (nulo.ajuste_top_pct, nulo.ajuste_resto_pct, nulo.ajuste_producto_pct) == (
        None,
        None,
        None,
    )


def test_sin_budget_presupuesto_y_moneda_none():
    item = _item()
    del item["budget"]
    config = config_de_payload(item, "USD")
    assert config.presupuesto_diario is None
    assert config.moneda is None
    assert config_de_payload(_item(budget=None), "USD").presupuesto_diario is None


@pytest.mark.parametrize(
    "budget",
    [
        {"budget": 0, "budgetType": "DAILY"},
        {"budget": -5, "budgetType": "DAILY"},
        {"budget": "diez", "budgetType": "DAILY"},
        {"budget": None, "budgetType": "DAILY"},
        10.08,
    ],
)
def test_presupuesto_malo_anula_la_campana_entera(budget):
    assert config_de_payload(_item(budget=budget), "USD") is None


def test_presupuesto_texto_numerico_vale_como_bid_decimal():
    config = config_de_payload(_item(budget={"budget": "10.08", "budgetType": "DAILY"}), "USD")
    assert config.presupuesto_diario == Decimal("10.08")


def test_budgetType_no_diario_o_ausente_anula_la_campana():
    assert config_de_payload(_item(budget={"budget": 10, "budgetType": "LIFETIME"}), "USD") is None
    assert config_de_payload(_item(budget={"budget": 10.08}), "USD") is None


@pytest.mark.parametrize("raro", [{"donde": "x"}, ["PLACEMENT_TOP"], 42, None])
def test_placement_no_texto_anula_sin_levantar(raro):
    bidding = {
        "strategy": "LEGACY_FOR_SALES",
        "placementBidding": [{"percentage": 40, "placement": raro}],
    }
    assert config_de_payload(_item(dynamicBidding=bidding), "USD") is None


def test_sin_placementBidding_los_ajustes_son_cero():
    config = config_de_payload(_item(dynamicBidding={"strategy": "MANUAL"}), "USD")
    assert config.estrategia_puja == "MANUAL"
    assert (config.ajuste_top_pct, config.ajuste_resto_pct, config.ajuste_producto_pct) == (0, 0, 0)


@pytest.mark.parametrize(
    "placements",
    [
        [{"percentage": 40, "placement": "PLACEMENT_LUNA"}],
        [{"percentage": 901, "placement": "PLACEMENT_TOP"}],
        [{"percentage": -1, "placement": "PLACEMENT_TOP"}],
        [{"percentage": "40", "placement": "PLACEMENT_TOP"}],
        [{"percentage": True, "placement": "PLACEMENT_TOP"}],
        [{"percentage": 40.0, "placement": "PLACEMENT_TOP"}],
        [{"placement": "PLACEMENT_TOP"}],
        [{"percentage": 40}],
        "no-es-lista",
        [
            {"percentage": 9, "placement": "PLACEMENT_TOP"},
            {"percentage": 10, "placement": "PLACEMENT_TOP"},
        ],
    ],
)
def test_placements_malos_anulan_la_campana_entera(placements):
    item = _item(dynamicBidding={"strategy": "MANUAL", "placementBidding": placements})
    assert config_de_payload(item, "USD") is None


def test_dynamicBidding_con_mala_forma_anula_la_campana():
    assert config_de_payload(_item(dynamicBidding="MANUAL"), "USD") is None
    assert (
        config_de_payload(_item(dynamicBidding={"strategy": 7, "placementBidding": []}), "USD")
        is None
    )


def test_strategy_vacia_anula_en_frontera_sin_reventar_base():
    """NB1 r4: strategy '' es ilegible (None, se cuenta), no revienta en
    config_estrategia_no_vacia dentro del sync."""
    assert (
        config_de_payload(_item(dynamicBidding={"strategy": "", "placementBidding": []}), "USD")
        is None
    )
    assert (
        config_de_payload(_item(dynamicBidding={"strategy": "   ", "placementBidding": []}), "USD")
        is None
    )


def test_sin_campaignId_anula_y_numerico_se_hace_texto():
    assert config_de_payload(_item(campaignId=None), "USD") is None
    item = _item()
    del item["campaignId"]
    assert config_de_payload(item, "USD") is None
    assert config_de_payload(_item(campaignId=123), "USD").campana_externa == "123"


def test_offAmazon_vacio_none_y_lleno_json_ordenado():
    assert config_de_payload(_item(), "USD").fuera_de_amazon is None
    item = _item(offAmazonSettings={"zeta": 1, "alfa": 2})
    assert config_de_payload(item, "USD").fuera_de_amazon == '{"alfa": 2, "zeta": 1}'
    config = config_de_payload(_item(offAmazonSettings="MINIMIZE_SPEND"), "USD")
    assert config.fuera_de_amazon == '"MINIMIZE_SPEND"'


def test_offAmazon_no_serializable_anula_la_campana():
    assert config_de_payload(_item(offAmazonSettings=object()), "USD") is None


def test_item_no_dict_anula():
    assert config_de_payload("no-es-dict", "USD") is None
    assert config_de_payload(None, "USD") is None
    assert config_de_payload(["lista"], "USD") is None


def test_como_desde_json_redondo():
    config = config_de_payload(_item(), "USD")
    assert ConfigCampana.desde_json(config.como_json()) == config


def test_desde_json_malformado_falla_fuerte():
    with pytest.raises((ValueError, TypeError)):
        ConfigCampana.desde_json({"campana_externa": "1"})
    doc = config_de_payload(_item(), "USD").como_json()
    doc["presupuesto_diario"] = "doce"
    with pytest.raises((ValueError, TypeError)):
        ConfigCampana.desde_json(doc)
    doc["presupuesto_diario"] = "10.08"
    doc["moneda"] = None
    with pytest.raises((ValueError, TypeError)):
        ConfigCampana.desde_json(doc)


@contextmanager
def _db_config(prefijo: str):
    """DB temporal con 0001 + 0061 (subconjunto minimo: 0061 solo pide
    de 0001 ad_entity, prohibir_mutacion() y los roles app_*)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(_SQL61)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _campana(conn, platform="amazon_mx", external="c-1") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
        " RETURNING id",
        (platform, external),
    ).fetchone()[0]


def _config(externa="c-1", **cambios) -> ConfigCampana:
    base = {
        "campana_externa": externa,
        "presupuesto_diario": Decimal("10.08"),
        "moneda": "MXN",
        "estrategia_puja": "LEGACY_FOR_SALES",
        "ajuste_top_pct": 0,
        "ajuste_resto_pct": 0,
        "ajuste_producto_pct": 40,
        "fuera_de_amazon": None,
    }
    base.update(cambios)
    return ConfigCampana(**base)


def _filas(conn) -> int:
    return conn.execute("SELECT count(*) FROM ads_campana_config_observation").fetchone()[0]


@_skip_db
def test_guarda_config_inserta_solo_con_cambio():
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    despues = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_guarda") as conn:
        _campana(conn)
        assert guarda_config(conn, "amazon_mx", [_config()], ahora) == 1
        assert guarda_config(conn, "amazon_mx", [_config()], despues) == 0
        assert _filas(conn) == 1
        assert (
            guarda_config(conn, "amazon_mx", [_config(presupuesto_diario=Decimal("12"))], despues)
            == 1
        )
        assert _filas(conn) == 2


@_skip_db
def test_guarda_config_duplicada_en_una_llamada_inserta_una():
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_dedup") as conn:
        _campana(conn)
        assert guarda_config(conn, "amazon_mx", [_config(), _config()], ahora) == 1
        assert _filas(conn) == 1


@_skip_db
def test_guarda_config_sin_ad_entity_salta():
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_huerfana") as conn:
        assert guarda_config(conn, "amazon_mx", [_config(externa="nadie")], ahora) == 0
        assert _filas(conn) == 0


@_skip_db
def test_guarda_config_sin_presupuesto_guarda_nulos():
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_nulos") as conn:
        _campana(conn)
        sin = _config(presupuesto_diario=None, moneda=None)
        assert guarda_config(conn, "amazon_mx", [sin], ahora) == 1
        fila = conn.execute(
            "SELECT presupuesto_diario, presupuesto_moneda FROM ads_campana_config_observation"
        ).fetchone()
        assert fila == (None, None)


@_skip_db
def test_config_vigente_trae_ultima_y_none_sin_filas():
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    despues = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_vigente") as conn:
        camp = _campana(conn)
        assert config_vigente(conn, camp) is None
        assert config_vigente(conn, camp + 999) is None
        guarda_config(conn, "amazon_mx", [_config()], ahora)
        nueva = _config(presupuesto_diario=Decimal("12"), estrategia_puja="MANUAL")
        guarda_config(conn, "amazon_mx", [nueva], despues)
        assert config_vigente(conn, camp) == nueva


# B10 (R05 r3): "solo si cambio" contra la ULTIMA fila, sin cruce MX/US.
# ---------------------------------------------------------------------------


@_skip_db
def test_guarda_config_a_b_a_deja_tres_filas_y_vigente_a():
    """v1-02: volver al valor anterior tambien es cambio (compara contra
    la ultima fila, no la primera)."""
    import datetime as dt

    base = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_aba") as conn:
        _campana(conn)
        for i, presupuesto in enumerate(("10", "12", "10")):
            guarda_config(
                conn,
                "amazon_mx",
                [_config(presupuesto_diario=Decimal(presupuesto))],
                base + dt.timedelta(hours=i),
            )
        filas = conn.execute(
            "SELECT presupuesto_diario FROM ads_campana_config_observation ORDER BY observed_at"
        ).fetchall()
        assert [f[0] for f in filas] == [Decimal("10"), Decimal("12"), Decimal("10")]


@_skip_db
def test_guarda_config_mismo_id_en_dos_mercados_no_se_cruza():
    """v1-07: el mismo campaignId en MX y US guarda cada config en su campana."""
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_mxus") as conn:
        mx = _campana(conn, "amazon_mx", "c-1")
        us = _campana(conn, "amazon_us", "c-1")
        assert (
            guarda_config(conn, "amazon_mx", [_config(presupuesto_diario=Decimal("200"))], ahora)
            == 1
        )
        assert guarda_config(conn, "amazon_us", [_config(moneda="USD")], ahora) == 1
        filas = dict(
            conn.execute(
                "SELECT ad_entity_id, presupuesto_moneda FROM ads_campana_config_observation"
            ).fetchall()
        )
        assert filas == {mx: "MXN", us: "USD"}


@_skip_db
def test_guarda_config_fuera_de_amazon_persiste_y_su_cambio_abre_fila():
    """v1-08: fuera_de_amazon llega a la base y su cambio abre fila."""
    import datetime as dt

    ahora = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)
    despues = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.UTC)
    with _db_config("orbit_cfg_fuera") as conn:
        _campana(conn)
        guarda_config(conn, "amazon_mx", [_config(fuera_de_amazon='{"a": 1}')], ahora)
        assert guarda_config(conn, "amazon_mx", [_config(fuera_de_amazon='{"a": 2}')], despues) == 1
        filas = conn.execute(
            "SELECT fuera_de_amazon FROM ads_campana_config_observation ORDER BY observed_at"
        ).fetchall()
        assert filas == [('{"a": 1}',), ('{"a": 2}',)]


def test_cuerpo_put_lleva_config_completa_sin_state():
    """V.3: el PUT manda budget, dynamicBidding y offAmazonSettings (nunca
    state); lo ausente no viaja."""
    from app.ads.campana_config import LLAVES_CUERPO_PUT, cuerpo_put_campana

    llena = ConfigCampana(
        campana_externa="93529333080113",
        presupuesto_diario=Decimal("120"),
        moneda="MXN",
        estrategia_puja="LEGACY_FOR_SALES",
        ajuste_top_pct=0,
        ajuste_resto_pct=0,
        ajuste_producto_pct=40,
        fuera_de_amazon='{"offAmazonBudgetControlStrategy": "MINIMIZE_SPEND"}',
    )
    assert cuerpo_put_campana(llena) == {
        "campaignId": "93529333080113",
        "budget": {"budget": 120.0, "budgetType": "DAILY"},
        "dynamicBidding": {
            "strategy": "LEGACY_FOR_SALES",
            "placementBidding": [
                {"placement": "PLACEMENT_TOP", "percentage": 0},
                {"placement": "PLACEMENT_REST_OF_SEARCH", "percentage": 0},
                {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": 40},
            ],
        },
        "offAmazonSettings": {"offAmazonBudgetControlStrategy": "MINIMIZE_SPEND"},
    }
    assert frozenset({"budget", "dynamicBidding", "offAmazonSettings"}) == LLAVES_CUERPO_PUT


def test_cuerpo_put_omite_lo_ausente():
    from app.ads.campana_config import cuerpo_put_campana

    minima = ConfigCampana(
        campana_externa="93529333080113",
        presupuesto_diario=Decimal("120"),
        moneda="MXN",
        estrategia_puja=None,
        ajuste_top_pct=None,
        ajuste_resto_pct=None,
        ajuste_producto_pct=None,
        fuera_de_amazon=None,
    )
    assert cuerpo_put_campana(minima) == {
        "campaignId": "93529333080113",
        "budget": {"budget": 120.0, "budgetType": "DAILY"},
    }
