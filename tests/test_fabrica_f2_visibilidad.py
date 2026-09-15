"""Visibilidad y aviso del destino de harvest (FABRICA 02, A.6).

Sobre `db_f2` + `_semilla_grupo` (el banco de `test_api_dashboard` no carga
0018/0038): `notes.harvest_destino` del ciclo, `harvest_job` en el feed de
decisiones, `destino` + `hermanas` en /cortes y el aviso en flanco por
campana de grupo. El DSN de lectura de los endpoints se deriva de
`_test_dsn()` + `conn.info.dbname` (hallazgo PR #267: nunca un DSN fijo).
"""

from __future__ import annotations

import json

import pytest
from test_cycle import _siembra_terminos
from test_fabrica_f2 import db_f2
from test_harvest_destino import _base_ciclo, _campana_suelta, _corre
from test_schema import _postgres_obligatorio_ausente

PLATFORM = "amazon_us"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _goal_campana_sin_terna(conn, camp: int) -> None:
    """Goal de campana habilitado SIN config de harvest (estado 1 del
    trigger de 0038: los tres campos NULL)."""
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode) VALUES ('campaign', %s, 55, 0.10, 2.50,"
        " 'USD', NULL, NULL, NULL, true, 'live')",
        (camp,),
    )


# ---------------------------------------------------------------------------
# A.6 bloque 2: notes.harvest_destino del ciclo
# ---------------------------------------------------------------------------


@_skip_db
def test_a6_notes_trae_resueltos_por_grupo_y_sin_saltos():
    """Tras el ciclo, notes.harvest_destino cuenta por ad group evaluado:
    los 4 discovery resuelven por grupo (la exacta-origen salta con
    origen_es_destino y no cuenta) y no hay saltos de grupo."""
    with db_f2("orbit_a6_notas") as conn:
        _base_ciclo(conn, con_terna=True)
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        bloque = json.loads(res.notes)["harvest_destino"]
        assert bloque["resueltos"] == {"grupo": 4, "excepcion": 0, "terna": 0}
        assert bloque["saltos_grupo"] == {}


@_skip_db
def test_a6_terna_distinta_deja_salto_de_grupo_en_notes():
    """El escenario de test_a1_terna_campaign_distinta: la phrase con terna
    de goal propio a otro destino deja saltos_grupo[camp] =
    destino_inconsistente y no cuenta como resuelto."""
    with db_f2("orbit_a6_salto") as conn:
        gpo, run = _base_ciclo(conn, con_terna=True)
        phrase = gpo["roles"]["category_phrase"]
        conn.execute(
            "UPDATE ads_optimizer_goal SET harvest_campaign_id = '8001',"
            " harvest_ad_group_id = '8101' WHERE scope = 'campaign' AND ad_entity_id = %s",
            (phrase["camp"],),
        )
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        bloque = json.loads(res.notes)["harvest_destino"]
        assert bloque["resueltos"]["grupo"] == 3
        assert bloque["saltos_grupo"] == {str(phrase["camp"]): "destino_inconsistente"}
        skips = json.loads(res.notes)["skips"]["termino"]
        assert skips.get("destino_inconsistente") == 1


@_skip_db
def test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo():
    """Una campana suelta sin nada es el estado normal de hoy: su
    sin_destino_de_harvest vive en skips.termino, JAMAS en saltos_grupo
    (ahi solo entran campanas DE GRUPO)."""
    with db_f2("orbit_a6_suelta") as conn:
        _base_ciclo(conn, con_terna=True)
        camp, ag = _campana_suelta(conn)
        _goal_campana_sin_terna(conn, camp)
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        notes = json.loads(res.notes)
        assert notes["harvest_destino"]["saltos_grupo"] == {}
        assert "sin_destino_de_harvest" not in notes["harvest_destino"]["saltos_grupo"].values()
        assert ag is not None  # la suelta existe y se evaluo sin grupo


@_skip_db
def test_a6_campana_suelta_con_termino_salta_sin_aviso_de_grupo():
    """Con un termino harvestable, la suelta sin nada salta con
    sin_destino_de_harvest en skips.termino — y sigue sin entrar a
    saltos_grupo (el aviso en flanco es solo para campanas de grupo)."""
    with db_f2("orbit_a6_sueltat") as conn:
        _gpo, run = _base_ciclo(conn, con_terna=True)
        camp, ag = _campana_suelta(conn)
        _goal_campana_sin_terna(conn, camp)
        _siembra_terminos(conn, run, ag)
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        notes = json.loads(res.notes)
        assert notes["skips"]["termino"].get("sin_destino_de_harvest") == 1
        assert notes["harvest_destino"]["saltos_grupo"] == {}
