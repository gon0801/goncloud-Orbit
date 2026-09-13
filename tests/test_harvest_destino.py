"""Resolutor de destino de harvest (FABRICA 02, A.1): rojo-primero.

Casos de la fila A.1 del plan, tal como estan escritos (no reformulados):
(a) grupo + senuelo; (b) exact como origen; (c) excepcion; (c') terna propia;
(c'') terna de plataforma (MAYORITARIO: 241/246 campanas); (d) sin nada;
(e) terna campaign distinta; (f) grupo mutado + terna limpiada; (g) dedupe;
(h) bid congelado. Mas goals_write.harvest_limpia_destino y el candado
tools/ del escritor unico.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest
from test_apply_harvest import _handler_harvest
from test_cycle import (
    DECIDED_AT,
    OWNER,
    _config_version,
    _estado,
    _metrica,
    _obs,
    _rango,
    _run,
    _siembra_terminos,
)
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_schema import _postgres_obligatorio_ausente

from app import cycle as ciclo
from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.apply_cola import libera_vencidos
from app.optimizer.harvest_destino import (
    DestinoHarvest,
    SaltoHarvest,
    resolver_destino,
)
from app.optimizer.replay import reproduce

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

PLATFORM = "amazon_us"


def _campana(conn, external: str = "7001") -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'campaign', %s) RETURNING id",
        (PLATFORM, external),
    ).fetchone()[0]


def _goal_plataforma_con_terna(conn, *, camp_ext="8001", ag_ext="8101") -> int:
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode) VALUES ('platform', %s, 55, 0.10, 2.50,"
        " 'USD', %s, %s, 1.00, true, 'live') RETURNING id",
        (PLATFORM, camp_ext, ag_ext),
    ).fetchone()[0]


def _goal_campana_con_terna(conn, camp: int, *, camp_ext="8001", ag_ext="8101") -> int:
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode) VALUES ('campaign', %s, 55, 0.10, 2.50,"
        " 'USD', %s, %s, 1.00, true, 'live') RETURNING id",
        (camp, camp_ext, ag_ext),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# (c) sin grupo con excepcion -> excepcion
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_sin_grupo_con_excepcion_resuelve_excepcion():
    with db_f2("orbit_a1_exc") as conn:
        camp = _campana(conn)
        conn.execute(
            "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
            " destino_ad_group_external, go_literal) VALUES (%s, '8001', '8101', 'go')",
            (camp,),
        )
        destino = resolver_destino(conn, PLATFORM, camp)
        assert destino == DestinoHarvest(
            campaign_external="8001",
            ad_group_external="8101",
            resuelto_por="excepcion",
            grupo_id=None,
            motivo=None,
            bid=None,
            moneda=None,
            floor=None,
            ceiling=None,
        )


# ---------------------------------------------------------------------------
# (c') sin grupo con terna de goal propio -> esa terna + migracion_pendiente
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_sin_grupo_con_terna_propia_resuelve_terna():
    with db_f2("orbit_a1_ternap") as conn:
        camp = _campana(conn)
        _goal_campana_con_terna(conn, camp)
        destino = resolver_destino(conn, PLATFORM, camp)
        assert isinstance(destino, DestinoHarvest)
        assert destino.campaign_external == "8001"
        assert destino.ad_group_external == "8101"
        assert destino.resuelto_por == "terna"
        assert destino.motivo == "migracion_pendiente"
        assert destino.bid == Decimal("1.00") and destino.moneda == "USD"


# ---------------------------------------------------------------------------
# (c'') campana sin goal propio, solo goal de plataforma con terna -> ESA
# terna (el caso MAYORITARIO: 241/246 + las 4 que cosechan hoy). Un mutante
# que lo mande a sin_destino_de_harvest debe morir aqui.
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_sin_goal_propio_resuelve_terna_de_plataforma():
    with db_f2("orbit_a1_ternaplat") as conn:
        camp = _campana(conn)
        _goal_plataforma_con_terna(conn)
        destino = resolver_destino(conn, PLATFORM, camp)
        assert isinstance(destino, DestinoHarvest), (
            "el caso mayoritario (241/246) debe resolver, jamas saltar"
        )
        assert destino.campaign_external == "8001"
        assert destino.ad_group_external == "8101"
        assert destino.resuelto_por == "terna"
        assert destino.motivo == "migracion_pendiente"
        assert not isinstance(destino, SaltoHarvest)


# ---------------------------------------------------------------------------
# (d) sin nada -> sin_destino_de_harvest
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_sin_nada_es_sin_destino_de_harvest():
    with db_f2("orbit_a1_nada") as conn:
        camp = _campana(conn)
        salto = resolver_destino(conn, PLATFORM, camp)
        assert salto == SaltoHarvest(motivo="sin_destino_de_harvest")


# ---------------------------------------------------------------------------
# Harness de ciclo + apply para (a)(b)(e)(f)(g)(h)
# ---------------------------------------------------------------------------


def _base_ciclo(conn, *, platform: str = PLATFORM, con_terna=True, con_goals=True):
    """Config live con caps + grupo con states ENABLED + metricas hasta
    D-3 para el watermark de plataforma (sin ellas el ciclo salta la
    plataforma entera). Sin goal de plataforma (cada caso siembra el suyo
    o ninguno)."""
    _config_version(
        conn,
        {
            "ads_optimizer_mode": "live",
            f"ads_apply_cap_{platform}_harvest": 2,
            f"ads_apply_cap_{platform}_negative": 5,
        },
    )
    run = _run(conn)
    gpo = _semilla_grupo(conn, platform=platform, con_terna=con_terna, con_goals=con_goals)
    sync = DECIDED_AT - dt.timedelta(hours=4)
    moneda = "USD" if platform == "amazon_us" else "MXN"
    for par in gpo["roles"].values():
        _estado(conn, par["camp"], synced_at=sync)
        _estado(conn, par["ag"], synced_at=sync)
        for fecha in _rango(dt.date(2026, 7, 10), dt.date(2026, 8, 19)):
            _metrica(
                conn,
                run,
                par["ag"],
                fecha,
                _obs(fecha),
                cost="0.01",
                ad_revenue="0.01",
                clicks=0,
                orders=0,
                moneda=moneda,
            )
    return gpo, run


def _corre(conn, platform: str = PLATFORM):
    return ciclo.corre_ciclo(
        conn, platform=platform, owner=OWNER, decided_at=DECIDED_AT, heartbeat_cada=1
    )


def _harvests_de(conn, cycle_id: int) -> list:
    return conn.execute(
        "SELECT id, ad_entity_id, search_term, new_value, value_currency, inputs"
        " FROM decision WHERE cycle_id = %s AND kind = 'harvest' ORDER BY id",
        (cycle_id,),
    ).fetchall()


def _negatives_de(conn, cycle_id: int) -> list:
    return conn.execute(
        "SELECT id, ad_entity_id, search_term, inputs"
        " FROM decision WHERE cycle_id = %s AND kind = 'negative' ORDER BY id",
        (cycle_id,),
    ).fetchall()


def _senuelo_exact(conn):
    """Campana FUERA de todo grupo con external_id 'category_exact': tienta
    a cualquier resolutor por nombre (caso a)."""
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'campaign', 'category_exact') RETURNING id",
        (PLATFORM,),
    ).fetchone()[0]


def _ahora_liberar() -> dt.datetime:
    """Reloj de liberacion: 3 dias en el futuro para que la fila del ciclo
    (vence a 48h) este vencida SIN UPDATE (el trigger de la cola prohibe
    UPDATEs que no cambian estado). La re-validacion usa la ventana fresca
    anclada en las observaciones, no en este reloj, asi que 'buena yarda'
    sigue calificando."""
    return dt.datetime.now(dt.UTC) + dt.timedelta(days=3)


def _aplicador_us(conn, handler, cycle_id: int) -> Aplicador:
    return Aplicador(
        conn,
        platform="amazon_us",
        profile_id=404040,
        credentials=AdsCredentials(
            client_id="fake",
            client_secret="fake",
            refresh_token="fake",
        ),
        cycle_id_ejecutor=cycle_id,
        owner="test:a1",
        job_key="ads_optimizer:amazon_us",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _posts_a(vistos: list, path: str) -> list:
    return [r for r in vistos if r.method == "POST" and r.url.path == path]


# ---------------------------------------------------------------------------
# (a) en grupo, goal con terna NULL + senuelo "category_exact" fuera del
# grupo -> la decision congela la exacta DEL GRUPO y el POST viaja a ese
# adGroupId (mata "por nombre" y "fallback al goal").
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo():
    with db_f2("orbit_a1_grupo") as conn:
        gpo, run = _base_ciclo(conn, con_goals=False)
        _goal_plataforma_con_terna(conn)
        _senuelo_exact(conn)
        exacta = gpo["roles"]["category_exact"]
        phrase = gpo["roles"]["category_phrase"]
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        assert res.status in ("done", "degraded"), (
            "en live sin credenciales la fase apply aborta fail-closed (degraded)"
            " pero las decisiones y el encolado persisten"
        )

        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1, "el harvest del grupo se decide aunque la terna sea NULL"
        _id, _ent, term, new_value, moneda, inputs = harvs[0]
        assert term == "buena yarda"
        congelado = inputs["goal"]["harvest"]
        assert congelado["campaign_id"] == exacta["camp_ext"]
        assert congelado["ad_group_id"] == exacta["ag_ext"]
        assert congelado["resuelto_por"] == "grupo"
        assert congelado["grupo_id"] == gpo["grupo_id"]

        handler, vistos = _handler_harvest()
        res2 = libera_vencidos(
            conn,
            PLATFORM,
            ahora=_ahora_liberar(),
            aplicador=_aplicador_us(conn, handler, res.cycle_id),
        )
        assert res2.aplicadas == 1
        posts = _posts_a(vistos, "/sp/keywords")
        assert len(posts) == 1
        cuerpo = json.loads(posts[0].content)["keywords"][0]
        assert cuerpo["adGroupId"] == exacta["ag_ext"], "el POST viaja a la exacta DEL GRUPO"
        assert cuerpo["campaignId"] == exacta["camp_ext"]


# ---------------------------------------------------------------------------
# (b) rol exact como origen -> motivo "origen_es_destino" (y NO
# harvest_duplicado), sembrado donde el dedupe no aplica.
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_exact_como_origen_es_skip_declarado():
    with db_f2("orbit_a1_origen") as conn:
        gpo, run = _base_ciclo(conn, con_goals=False)
        _goal_plataforma_con_terna(conn)
        exacta = gpo["roles"]["category_exact"]
        _siembra_terminos(conn, run, exacta["ag"])
        res = _corre(conn)
        assert res.status in ("done", "degraded"), (
            "en live sin credenciales la fase apply aborta fail-closed (degraded)"
            " pero las decisiones y el encolado persisten"
        )
        assert _harvests_de(conn, res.cycle_id) == [], "la exacta no se harvestea a si misma"
        skips = json.loads(res.notes)["skips"]["termino"]
        assert skips.get("origen_es_destino") == 1
        assert "harvest_duplicado" not in skips


# ---------------------------------------------------------------------------
# (e) terna scope campaign distinta del grupo -> destino_inconsistente,
# cero HTTP.
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_terna_campaign_distinta_es_inconsistente_y_cero_http():
    with db_f2("orbit_a1_incon") as conn:
        gpo, run = _base_ciclo(conn, con_terna=True)
        phrase = gpo["roles"]["category_phrase"]
        conn.execute(
            "UPDATE ads_optimizer_goal SET harvest_campaign_id = '8001',"
            " harvest_ad_group_id = '8101' WHERE scope = 'campaign' AND ad_entity_id = %s",
            (phrase["camp"],),
        )
        salto = resolver_destino(conn, PLATFORM, phrase["camp"])
        assert salto == SaltoHarvest(motivo="destino_inconsistente")
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        assert res.status in ("done", "degraded"), (
            "en live sin credenciales la fase apply aborta fail-closed (degraded)"
            " pero las decisiones y el encolado persisten"
        )
        assert _harvests_de(conn, res.cycle_id) == []
        skips = json.loads(res.notes)["skips"]["termino"]
        assert skips.get("destino_inconsistente") == 1

        _ = None
        handler, vistos = _handler_harvest()
        libera_vencidos(
            conn,
            PLATFORM,
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador_us(conn, handler, res.cycle_id),
        )
        assert vistos == [], "cero HTTP: el harvest inconsistente no sale"


# ---------------------------------------------------------------------------
# (f) decidido con G1, se muta campana_grupo_rol antes de liberar -> el POST
# no se emite (destino_desincronizado); y con la terna limpiada despues de
# decidir, apply y replay usan el congelado.
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_grupo_mutado_antes_de_liberar_no_postea():
    with db_f2("orbit_a1_mutado") as conn:
        gpo, run = _base_ciclo(conn, con_goals=False)
        _goal_plataforma_con_terna(conn)
        phrase = gpo["roles"]["category_phrase"]
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1

        otra_camp = _campana(conn, "6901")
        otro_ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES (%s, 'ad_group', '6902', %s) RETURNING id",
            (PLATFORM, otra_camp),
        ).fetchone()[0]
        conn.execute(
            "UPDATE campana_grupo_rol SET ad_entity_id = %s, ad_group_ad_entity_id = %s"
            " WHERE grupo_id = %s AND rol = 'category_exact'",
            (otra_camp, otro_ag, gpo["grupo_id"]),
        )
        handler, vistos = _handler_harvest()
        res2 = libera_vencidos(
            conn,
            PLATFORM,
            ahora=_ahora_liberar(),
            aplicador=_aplicador_us(conn, handler, res.cycle_id),
        )
        assert _posts_a(vistos, "/sp/keywords") == [], "el POST no se emite"
        assert _posts_a(vistos, "/sp/negativeKeywords") == []
        fase = conn.execute("SELECT fase FROM harvest_job").fetchone()[0]
        assert fase == "failed"
        motivos = [a.motivo for a in res2.alertas]
        assert motivos == ["destino_desincronizado"]


@_skip_db
def test_a1_terna_limpiada_tras_decidir_apply_y_replay_usan_congelado():
    with db_f2("orbit_a1_limpia") as conn:
        gpo, run = _base_ciclo(conn, con_terna=True)
        exacta = gpo["roles"]["category_exact"]
        phrase = gpo["roles"]["category_phrase"]
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1
        _id, _ent, _term, new_value, moneda, inputs = harvs[0]
        assert new_value == Decimal("11.62")

        from app.goals_write import edita_goal

        # Estado post-D.2: la terna se limpia con bid intacto
        # (harvest_limpia_destino, admitido por el trigger de 0038 en ORDEN_F2).
        goal_ids = conn.execute(
            "SELECT id FROM ads_optimizer_goal WHERE scope = 'campaign' AND ad_entity_id = ANY(%s)",
            ([par["camp"] for par in gpo["roles"].values()],),
        ).fetchall()
        assert len(goal_ids) == 5
        for (goal_id,) in goal_ids:
            fila = edita_goal(
                conn, goal_id, harvest_limpia_destino=True, updated_at=dt.datetime.now(dt.UTC)
            )
            assert fila["harvest_campaign_id"] is None
            assert fila["harvest_ad_group_id"] is None
            assert fila["harvest_default_bid"] == "11.6200"

        handler, vistos = _handler_harvest()
        res2 = libera_vencidos(
            conn,
            PLATFORM,
            ahora=_ahora_liberar(),
            aplicador=_aplicador_us(conn, handler, res.cycle_id),
        )
        assert res2.aplicadas == 1, "apply usa el congelado aunque la terna ya sea NULL"
        posts = _posts_a(vistos, "/sp/keywords")
        assert len(posts) == 1
        cuerpo = json.loads(posts[0].content)["keywords"][0]
        assert cuerpo["adGroupId"] == exacta["ag_ext"]

        kind, valor, curr = reproduce(inputs)
        assert (kind, valor, curr) == ("harvest", Decimal("11.62"), "USD")


# ---------------------------------------------------------------------------
# (g) termino ya en la exacta del grupo -> harvest_duplicado, cero fila de
# cola (dedupe re-apuntado: el goal de plataforma apunta a OTRA campana sin
# el termino; sin el re-apunte se decidiria harvest).
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_termino_en_exacta_del_grupo_es_duplicado():
    with db_f2("orbit_a1_dupli") as conn:
        gpo, run = _base_ciclo(conn, con_goals=False)
        _goal_plataforma_con_terna(conn)
        exacta = gpo["roles"]["category_exact"]
        phrase = gpo["roles"]["category_phrase"]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
            " keyword_text) VALUES (%s, 'keyword', '63001', %s, 'EXACT', 'buena yarda')",
            (PLATFORM, exacta["ag"]),
        )
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        assert res.status in ("done", "degraded"), (
            "en live sin credenciales la fase apply aborta fail-closed (degraded)"
            " pero las decisiones y el encolado persisten"
        )
        assert _harvests_de(conn, res.cycle_id) == []
        skips = json.loads(res.notes)["skips"]["termino"]
        assert skips.get("harvest_duplicado") == 1
        n_cola = conn.execute("SELECT count(*) FROM apply_queue WHERE kind = 'harvest'").fetchone()[
            0
        ]
        assert n_cola == 0, "el duplicado no encola"


# ---------------------------------------------------------------------------
# (h) terna NULL y bid 11.62 (MXN: dentro del clamp) -> el POST lleva 11.62.
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna():
    with db_f2("orbit_a1_bidmx") as conn:
        gpo, run = _base_ciclo(conn, platform="amazon_mx", con_terna=True)
        exacta = gpo["roles"]["category_exact"]
        phrase = gpo["roles"]["category_phrase"]
        _siembra_terminos_mx(conn, run, phrase["ag"])
        res = _corre(conn, "amazon_mx")
        assert res.status in ("done", "degraded"), (
            "en live sin credenciales la fase apply aborta fail-closed (degraded)"
            " pero las decisiones y el encolado persisten"
        )
        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1
        assert harvs[0][3] == Decimal("11.62")

        from app.goals_write import edita_goal

        # Estado post-D.2 (ver el otro test de terna limpiada): bid intacto
        # con el trigger real de 0038.
        goal_ids = conn.execute(
            "SELECT id FROM ads_optimizer_goal WHERE scope = 'campaign' AND ad_entity_id = ANY(%s)",
            ([par["camp"] for par in gpo["roles"].values()],),
        ).fetchall()
        assert len(goal_ids) == 5
        for (goal_id,) in goal_ids:
            fila = edita_goal(
                conn, goal_id, harvest_limpia_destino=True, updated_at=dt.datetime.now(dt.UTC)
            )
            assert fila["harvest_default_bid"] == "11.6200"

        handler, vistos = _handler_harvest()
        aplicador = Aplicador(
            conn,
            platform="amazon_mx",
            profile_id=505050,
            credentials=AdsCredentials(
                client_id="fake",
                client_secret="fake",
                refresh_token="fake",
            ),
            cycle_id_ejecutor=res.cycle_id,
            owner="test:a1mx",
            job_key="ads_optimizer:amazon_mx",
            transport=httpx.MockTransport(handler),
            sleep=lambda seconds: None,
        )
        res2 = libera_vencidos(conn, "amazon_mx", ahora=_ahora_liberar(), aplicador=aplicador)
        assert res2.aplicadas == 1
        posts = _posts_a(vistos, "/sp/keywords")
        assert len(posts) == 1
        cuerpo = json.loads(posts[0].content)["keywords"][0]
        assert cuerpo["adGroupId"] == exacta["ag_ext"]
        assert float(cuerpo["bid"]) == 11.62, "el bid congelado viaja clampeado"


def _siembra_terminos_mx(conn, run_id, ag) -> None:
    """Siembra minima MX del caso (h): 'buena yarda' harvestable (orders 3,
    ACoS 10%) + relleno para la completitud (9 fechas en ventana)."""
    from test_cycle import _obs, _rango

    def _term_mx(term, fecha, observed, **kw):
        conn.execute(
            "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
            " metric_date, observed_at, metric_currency, cost, clicks, orders, ad_revenue,"
            " is_asin_like, ingest_run_id) VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN',"
            " %s, %s, %s, %s, false, %s)",
            (
                ag,
                term,
                fecha,
                observed,
                Decimal(kw.get("cost")) if kw.get("cost") is not None else None,
                kw.get("clicks"),
                kw.get("orders"),
                Decimal(kw.get("ad_revenue")) if kw.get("ad_revenue") is not None else None,
                run_id,
            ),
        )

    for fecha, cost, revenue in (
        (dt.date(2026, 7, 13), "33.40", "300.00"),
        (dt.date(2026, 7, 14), "33.30", "300.00"),
        (dt.date(2026, 7, 15), "33.30", "400.00"),
    ):
        _term_mx(
            "buena yarda", fecha, _obs(fecha), cost=cost, ad_revenue=revenue, clicks=3, orders=1
        )
    # Relleno para la completitud (>= 7 fechas de entidad DENTRO de la
    # ventana 06-19..07-18): 9 fechas 07-10..07-18 mas 07-19..07-21 que
    # sostienen el ancla en 07-21.
    for fecha in _rango(dt.date(2026, 7, 10), dt.date(2026, 7, 12)):
        _term_mx(
            "relleno diario", fecha, _obs(fecha), cost="0.10", ad_revenue="0.10", clicks=0, orders=0
        )
    for fecha in _rango(dt.date(2026, 7, 16), dt.date(2026, 7, 21)):
        _term_mx(
            "relleno diario", fecha, _obs(fecha), cost="0.10", ad_revenue="0.10", clicks=0, orders=0
        )


# ---------------------------------------------------------------------------
# goals_write.harvest_limpia_destino (A.1): NULL en campaign/ad_group, bid
# intacto, validado DESPUES de leer la fila (la campana debe estar en grupo).
# ---------------------------------------------------------------------------


@_skip_db
def test_a1_limpia_destino_exige_campana_en_grupo():
    from app.goals_write import GoalInvalido, edita_goal

    with db_f2("orbit_a1_limpval") as conn:
        camp = _campana(conn)
        goal_id = _goal_campana_con_terna(conn, camp)
        with pytest.raises(GoalInvalido):
            edita_goal(
                conn, goal_id, harvest_limpia_destino=True, updated_at=dt.datetime.now(dt.UTC)
            )


@_skip_db
def test_a1_limpia_destino_no_se_combina_con_campos_harvest():
    from app.goals_write import GoalInvalido, edita_goal

    with db_f2("orbit_a1_limpcomb") as conn:
        gpo, _run = _base_ciclo(conn, con_terna=True)
        par = gpo["roles"]["category_phrase"]
        goal_id = conn.execute(
            "SELECT id FROM ads_optimizer_goal WHERE scope = 'campaign' AND ad_entity_id = %s",
            (par["camp"],),
        ).fetchone()[0]
        with pytest.raises(GoalInvalido):
            edita_goal(
                conn,
                goal_id,
                harvest_limpia_destino=True,
                harvest_default_bid=Decimal("5.00"),
                updated_at=dt.datetime.now(dt.UTC),
            )


# ---------------------------------------------------------------------------
# Ronda PR #258: bloqueante + CodeRabbit 1 + los cuatro huecos de mutantes
# ---------------------------------------------------------------------------


def _campana_suelta(conn, *, camp_ext="7009", ag_ext="7109"):
    """Campana + ad group hijo FUERA de todo grupo, con states ENABLED
    (los huecos de mutantes necesitan caminos excepcion/terna puros: con
    `_base_ciclo` el watermark de plataforma ya viene fresco del grupo)."""
    from test_cycle import DECIDED_AT, _estado

    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'campaign', %s) RETURNING id",
        (PLATFORM, camp_ext),
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
        (PLATFORM, ag_ext, camp),
    ).fetchone()[0]
    sync = DECIDED_AT - dt.timedelta(hours=4)
    _estado(conn, camp, synced_at=sync)
    _estado(conn, ag, synced_at=sync)
    return camp, ag


@_skip_db
def test_a1_grupo_sin_bid_negative_reproduce_sin_excepcion():
    """BLOQUEANTE (ronda PR #258): campana en grupo + goal sin
    `harvest_default_bid` + termino que decide `negative` -> cada decision
    congelada pasa por `reproduce()` sin excepcion. Rojo antes del arreglo:
    `_goal_json` congelaba `default_bid: null` y el replay reventaba con
    TypeError en Decimal(None). El congelado refleja lo que el motor uso
    (config None): harvest ausente."""
    from test_cycle import _obs, _termino

    with db_f2("orbit_a1_sinbid") as conn:
        gpo, run = _base_ciclo(conn, con_terna=False)
        phrase = gpo["roles"]["category_phrase"]
        _siembra_terminos(conn, run, phrase["ag"])
        # Negative elegible bajo la evidencia del fixture (grupo no
        # elegible: umbral 40 clicks, piso 45 USD): "tortugas" (23/20.00)
        # no corta aqui; este si.
        _termino(
            conn,
            run,
            phrase["ag"],
            "corta y quema",
            dt.date(2026, 7, 12),
            _obs(dt.date(2026, 7, 12), 4),
            cost="60.00",
            ad_revenue="1.00",
            clicks=50,
            orders=0,
        )
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        negs = _negatives_de(conn, res.cycle_id)
        assert {n[2] for n in negs} >= {"corta y quema"}
        for _id, _ent, term, inputs in negs:
            # Primero lo que exige el brief: reproduce() sin excepcion (en
            # rojo era TypeError: conversion from NoneType to Decimal).
            kind, valor, curr = reproduce(inputs)
            assert (kind, valor, curr) == ("negative", None, None), term
            assert inputs["goal"]["harvest"] is None, (
                f"{term}: sin monto no hay harvest que congelar"
            )


@_skip_db
def test_a1_limpia_y_limpia_destino_no_se_combinan():
    """CodeRabbit 1 (ronda PR #258): `harvest_limpia` +
    `harvest_limpia_destino` juntas borraban el bid en silencio (la primera
    anula los tres campos, la segunda solo re-anula dos): lo contrario de
    lo que `harvest_limpia_destino` promete. Se rechaza y la fila queda
    intacta."""
    from app.goals_write import GoalInvalido, edita_goal

    with db_f2("orbit_a1_limpdoble") as conn:
        gpo, _run = _base_ciclo(conn, con_terna=True)
        par = gpo["roles"]["category_phrase"]
        goal_id = conn.execute(
            "SELECT id FROM ads_optimizer_goal WHERE scope = 'campaign' AND ad_entity_id = %s",
            (par["camp"],),
        ).fetchone()[0]
        with pytest.raises(GoalInvalido, match="no se combinan"):
            edita_goal(
                conn,
                goal_id,
                harvest_limpia=True,
                harvest_limpia_destino=True,
                updated_at=dt.datetime.now(dt.UTC),
            )
        fila = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid"
            " FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert fila[2] is not None, "el rechazo no toca la fila: el bid sigue intacto"


@_skip_db
def test_a1_ciclo_tras_limpiar_terna_congela_destino():
    """Mutante 1 (ronda PR #258): `completa` se deriva del DESTINO, no de la
    terna del goal. Tras D.2 (terna limpiada, bid intacto) el ciclo sigue
    congelando el destino del grupo: derivarlo de
    `goal.harvest_campaign_id` congelaria null y el replay fallaria."""
    with db_f2("orbit_a1_postd2") as conn:
        gpo, run = _base_ciclo(conn, con_terna=True)
        from app.goals_write import edita_goal

        for (goal_id,) in conn.execute(
            "SELECT id FROM ads_optimizer_goal WHERE scope = 'campaign'"
        ).fetchall():
            edita_goal(
                conn, goal_id, harvest_limpia_destino=True, updated_at=dt.datetime.now(dt.UTC)
            )
        exacta = gpo["roles"]["category_exact"]
        phrase = gpo["roles"]["category_phrase"]
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1
        _id, _ent, _term, _nv, _mo, inputs = harvs[0]
        congelado = inputs["goal"]["harvest"]
        assert congelado is not None, "post-D.2 el harvest sale del destino, no de la terna"
        assert congelado["campaign_id"] == exacta["camp_ext"]
        assert congelado["resuelto_por"] == "grupo"
        kind, valor, curr = reproduce(inputs)
        assert (kind, valor, curr) == ("harvest", Decimal("11.62"), "USD")


@_skip_db
def test_a1_excepcion_ajena_no_pertenece_es_desincronizado():
    """Mutante 2a (ronda PR #258): la guarda `pertenece` es el unico cinturon
    del camino excepcion (sin re-resolucion). Par congelado ajeno a la
    campana en `ad_entity` -> `destino_desincronizado` con cero HTTP.
    Anular la guarda reviviría el POST al destino ajeno."""
    with db_f2("orbit_a1_excajan") as conn:
        _gpo, run = _base_ciclo(conn, con_goals=False)
        _goal_plataforma_con_terna(conn)
        camp, ag = _campana_suelta(conn)
        conn.execute(
            "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
            " destino_ad_group_external, go_literal) VALUES (%s, '8901', '8902', 'go')",
            (camp,),
        )
        _siembra_terminos(conn, run, ag)
        res = _corre(conn)
        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1
        assert harvs[0][5]["goal"]["harvest"]["resuelto_por"] == "excepcion"
        handler, vistos = _handler_harvest()
        res2 = libera_vencidos(
            conn,
            PLATFORM,
            ahora=_ahora_liberar(),
            aplicador=_aplicador_us(conn, handler, res.cycle_id),
        )
        assert _posts_a(vistos, "/sp/keywords") == []
        assert _posts_a(vistos, "/sp/negativeKeywords") == []
        assert [a.motivo for a in res2.alertas] == ["destino_desincronizado"]


@_skip_db
def test_a1_terna_ajena_no_pertenece_es_desincronizado():
    """Mutante 2b (ronda PR #258): lo mismo para el camino terna (tampoco
    tiene re-resolucion): terna a un par que no es hermana en `ad_entity`
    -> `destino_desincronizado` con cero HTTP."""
    with db_f2("orbit_a1_ternajan") as conn:
        _gpo, run = _base_ciclo(conn, con_goals=False)
        camp, ag = _campana_suelta(conn)
        _goal_campana_con_terna(conn, camp, camp_ext="8901", ag_ext="8902")
        _siembra_terminos(conn, run, ag)
        res = _corre(conn)
        harvs = _harvests_de(conn, res.cycle_id)
        assert len(harvs) == 1
        assert harvs[0][5]["goal"]["harvest"]["resuelto_por"] == "terna"
        handler, vistos = _handler_harvest()
        res2 = libera_vencidos(
            conn,
            PLATFORM,
            ahora=_ahora_liberar(),
            aplicador=_aplicador_us(conn, handler, res.cycle_id),
        )
        assert _posts_a(vistos, "/sp/keywords") == []
        assert _posts_a(vistos, "/sp/negativeKeywords") == []
        assert [a.motivo for a in res2.alertas] == ["destino_desincronizado"]


@_skip_db
def test_a1_limpia_destino_exige_scope_campaign():
    """Mutante 3 (ronda PR #258): `harvest_limpia_destino` exige scope
    `campaign` (hay test para campana sin grupo, ninguno para scope
    platform): un goal de plataforma se rechaza."""
    from app.goals_write import GoalInvalido, edita_goal

    with db_f2("orbit_a1_limpscope") as conn:
        goal_id = _goal_plataforma_con_terna(conn)
        with pytest.raises(GoalInvalido, match="scope campaign"):
            edita_goal(
                conn, goal_id, harvest_limpia_destino=True, updated_at=dt.datetime.now(dt.UTC)
            )


@_skip_db
def test_a1_grupo_gana_a_excepcion():
    """Mutante 4 (ronda PR #258): orden grupo > excepcion. Con los dos
    presentes el destino es el del grupo; mover la excepcion antes
    resolveria el ajeno."""
    with db_f2("orbit_a1_orden") as conn:
        gpo, _run = _base_ciclo(conn, con_terna=True)
        _goal_plataforma_con_terna(conn)
        phrase = gpo["roles"]["category_phrase"]
        exacta = gpo["roles"]["category_exact"]
        conn.execute(
            "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
            " destino_ad_group_external, go_literal) VALUES (%s, '8901', '8902', 'go')",
            (phrase["camp"],),
        )
        destino = resolver_destino(conn, PLATFORM, phrase["camp"])
        assert isinstance(destino, DestinoHarvest)
        assert destino.resuelto_por == "grupo"
        assert destino.campaign_external == exacta["camp_ext"]
        assert destino.ad_group_external == exacta["ag_ext"]


# ---------------------------------------------------------------------------
# Candado de escritor unico extendido a tools/ (A.1)
# ---------------------------------------------------------------------------


def test_a1_escritor_unico_de_goals_cubre_tools():
    """Ningun modulo de tools/ escribe ads_optimizer_goal crudo (el patron
    UPDATE/INSERT del candado): la escritura vive SOLO en app/goals_write.py.
    Rojo: un UPDATE crudo en tools/ revienta este test."""
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    ident = r'(?:"?\w+"?\.)?"?ads_optimizer_goal"?'
    patron_update = re.compile(rf"UPDATE\s+{ident}", re.IGNORECASE)
    patron_insert = re.compile(rf"INSERT\s+INTO\s+{ident}", re.IGNORECASE)
    escritores = sorted(
        str(p.relative_to(raiz))
        for p in (raiz / "tools").rglob("*.py")
        if patron_update.search(p.read_text(encoding="utf-8"))
        or patron_insert.search(p.read_text(encoding="utf-8"))
    )
    assert escritores == [], f"escritura cruda de goals en tools/: {escritores}"
