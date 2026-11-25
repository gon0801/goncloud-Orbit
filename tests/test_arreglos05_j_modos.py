"""ARREGLOS 05 J: retirar modos sin uso conservando historia y apagadores.

Regresiones del contrato J (DISENO.md:243-248):
- sin `ads_bid_politica_<plataforma>` la hoja no muta: no hay decision bid ni
  pause de la era bandas, solo el skip `politica_apagada`;
- una politica ilegible aborta TX2 sin decisiones;
- la meta por producto en practica (`shadow`) ya no se acepta; la pausa por
  producto en shadow sigue viva;
- el replay de una pausa historica real (sin `inputs.politica`) no cambia;
- la pantalla de productos no anuncia un modo que ya no existe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_cycle import _corre, _db_temporal, _decisions_de, _siembra_maestra
from test_schema import _postgres_obligatorio_ausente

from app import config_write
from app.optimizer import productos, replay

RAIZ = Path(__file__).resolve().parents[1]
FIXTURE_PAUSA = RAIZ / "tests" / "fixtures" / "arreglos05_j_pausa_historica.json"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@_skip_db
def test_arreglos05_j_sin_politica_no_muta_y_cuenta_el_skip():
    """Config ausente no es permiso live: la hoja no encola bid ni pause."""
    with _db_temporal("orbit_j_sin_politica") as (conn, _c):
        ids = _siembra_maestra(conn)
        res = _corre(conn)
        assert res.status == "done"
        filas = _decisions_de(conn, res.cycle_id)
        por = {(f[0], f[1]) for f in filas}
        assert (ids["kw_bid"], "bid") not in por
        assert (ids["kw_pause"], "pause") not in por
        notas = json.loads(res.notes)
        assert notas["skips"]["entidad"].get("politica_apagada", 0) >= 1


@_skip_db
def test_arreglos05_j_sin_politica_conserva_el_target_para_cosecha():
    """Apagar bids no congela el ancla que usa la cosecha: la meta por hoja se
    sigue resolviendo y guardando, solo no hay decision de hoja."""
    with _db_temporal("orbit_j_target_cosecha") as (conn, _c):
        ids = _siembra_maestra(conn)
        res = _corre(conn)
        assert res.status == "done"
        por = {(f[0], f[1]) for f in _decisions_de(conn, res.cycle_id)}
        assert (ids["kw_bid"], "bid") not in por
        assert (ids["kw_pause"], "pause") not in por
        targets = {
            f[0]: (f[1], f[2])
            for f in conn.execute(
                "SELECT ad_entity_id, target_acos_pct, procedencia FROM target_acos_ciclo"
                " WHERE cycle_id = %s",
                (res.cycle_id,),
            ).fetchall()
        }
        assert ids["kw_bid"] in targets
        assert ids["kw_pause"] in targets


@_skip_db
def test_arreglos05_j_politica_ilegible_aborta_sin_decisiones():
    """Un valor corrupto de la politica revienta TX2 y no deja decisiones."""
    with _db_temporal("orbit_j_politica_ilegible") as (conn, _c):
        ids = _siembra_maestra(conn, settings={"ads_bid_politica_amazon_us": "bandas_v1"})
        with pytest.raises(ValueError, match="politica de bids"):
            _corre(conn)
        n = conn.execute(
            "SELECT count(*) FROM decision WHERE ad_entity_id IN (%s, %s)",
            (ids["kw_bid"], ids["kw_pause"]),
        ).fetchone()[0]
        assert n == 0


def test_arreglos05_j_meta_shadow_retirada_y_pausa_shadow_conservada():
    """La meta solo acepta off/live; la pausa conserva shadow."""
    with pytest.raises(ValueError, match="ads_producto_meta_amazon_us"):
        productos.modos_desde_settings({"ads_producto_meta_amazon_us": "shadow"}, "amazon_us")
    modos = productos.modos_desde_settings({"ads_producto_pausa_amazon_us": "shadow"}, "amazon_us")
    assert modos.pausa == "shadow"
    assert modos.meta == "off"
    with pytest.raises(config_write.SettingsInvalido, match="meta"):
        config_write.proxima_config({}, "amazon_us", producto_modo={"meta": "shadow"})


def test_arreglos05_j_replay_pausa_historica_sin_politica():
    """Una pausa real de produccion (decision 2736) sigue rejugando igual."""
    inputs = json.loads(FIXTURE_PAUSA.read_text(encoding="utf-8"))
    assert "politica" not in inputs
    assert replay.reproduce(inputs) == ("pause", None, None)


def test_arreglos05_j_ui_productos_no_anuncia_la_meta_en_practica():
    """La pantalla no describe un modo retirado aunque una fila vieja lo traiga."""
    from app.ui import templates

    html = templates.env.get_template("productos_sin_clics.html").render(
        pantalla="productos-sin-clics",
        plataforma="amazon_mx",
        asset_version="j",
        candidatos=[],
        impulsos=[],
        sin_pedido=[],
        anuncios_sin_producto=0,
        decisiones_producto={
            "cycle_id": 1,
            "decidido_el": "2026-10-05T08:40:01+00:00",
            "modo_meta": "shadow",
            "modo_meta_texto": "Práctica",
            "error": None,
            "resumen": {},
            "productos": [],
            "con_meta": 0,
            "motivos_presentes": [],
            "pausa": None,
            "limites_pausa": None,
        },
    )
    assert "se calcula y no cambia ninguna puja" not in html
