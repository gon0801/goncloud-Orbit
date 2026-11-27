"""Tests de integracion y extremo a extremo del ciclo de ubicaciones (AUTO-04).

Conserva las pruebas de contrato de TX3/config y agrega la suite E2E real que
ejecuta `cycle.corre_ciclo` y `cycle.atender_plataforma` contra PostgreSQL 16
(`db_desde_template`) con Amazon simulado en la frontera HTTP (`MockTransport`)
via `aplicador_factory`, cubriendo las 12 fronteras de la matriz de fallos y
los 6 escenarios P1 (`P1-1` a `P1-6`).
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from test_schema import _postgres_obligatorio_ausente

from app import config_write, productos_io
from app import cycle as ciclo_mod
from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.apply_ubicacion import aplica_automaticos
from app.cycle_grupos import _Contadores
from app.cycle_sombra import tx3_ubicaciones
from app.optimizer.caso import EconomiaPlataforma
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    CasoCampana,
    EvidenciaUbicacion,
    MantenerUbicacion,
    MetaCampana,
    MoverUbicacion,
    caso_a_json,
)
from app.ubicacion_io import campanas_con_ajuste_en_vuelo
from tests.helpers.pg import db_desde_template

_D = Decimal
_ANCLA = dt.datetime(2026, 11, 26, 6, 0, tzinfo=dt.UTC)
_AHORA = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.UTC)
_OWNER = "e2e-ubic:1"
_PERFIL_MX = {
    "profileId": 414141,
    "countryCode": "MX",
    "currencyCode": "MXN",
    "accountInfo": {"type": "seller", "name": "Tienda MX", "validPaymentMethod": True},
}

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(), reason="sin Postgres utilizable en ORBIT_TEST_DSN"
)


def _db():
    return db_desde_template("orbit_cyuio", autocommit=True)


_ROL_DECIDE_E2E = "orbit_decide_e2e"
_PWD_DECIDE_E2E = "orbit_decide_e2e_pwd"


@contextmanager
def _como_decide(conn_admin: psycopg.Connection, dsn: str):
    """Abre una conexion de ciclo bajo un rol LOGIN real miembro SOLO de
    `app_decide` (como `orbit_decide` en produccion, `autocommit=False`)."""
    conn_admin.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROL_DECIDE_E2E}') THEN
                CREATE ROLE {_ROL_DECIDE_E2E} LOGIN PASSWORD '{_PWD_DECIDE_E2E}' IN ROLE app_decide;
            END IF;
        END
        $$;
        """
    )
    db_actual = conn_admin.execute("SELECT current_database()").fetchone()[0]
    conn_admin.execute(
        pgsql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            pgsql.Identifier(db_actual), pgsql.Identifier(_ROL_DECIDE_E2E)
        )
    )
    dsn_dec = make_conninfo(dsn, user=_ROL_DECIDE_E2E, password=_PWD_DECIDE_E2E)
    with psycopg.connect(dsn_dec, autocommit=False) as c_dec:
        c_dec.execute("SET TIME ZONE 'UTC'")
        c_dec.commit()
        yield c_dec


def _caso(campana: int, config_id: int) -> CasoCampana:
    return CasoCampana(
        campana_id=campana,
        plataforma="amazon_mx",
        config_id=config_id,
        estrategia="LEGACY_FOR_SALES",
        ventana_desde=_ANCLA.date() - dt.timedelta(days=32),
        ventana_hasta=_ANCLA.date() - dt.timedelta(days=3),
        ubicaciones=(
            EvidenciaUbicacion("arriba_de_busqueda", 0, 40, _D("5"), 4, _D("100"), 0),
            EvidenciaUbicacion("resto_de_busqueda", 0, 20, _D("2"), 0, _D("10"), 0),
            EvidenciaUbicacion("paginas_de_producto", 0, 20, _D("2"), 0, _D("10"), 0),
        ),
        meta=MetaCampana(_D("18"), _D("24"), ("goal",)),
        economia=EconomiaPlataforma(
            moneda="MXN",
            equilibrio_acos_pct=None,
            gasto_para_concluir=_D("350"),
            confianza_recorte=_D("0.80"),
            confianza_subida=_D("0.70"),
        ),
        gasto_campana=_D("9"),
        gasto_placements_total=_D("9"),
        placements_frescos=True,
        dias_desde_ultimo_cambio=None,
        ajuste_en_vuelo=False,
        elegible=True,
        en_impulso=False,
        solo_bajadas=False,
        dias_topados_7d=0,
        topada=CampanaTopada(_D("0.90"), 5),
        fase_temporada="normal",
    )


def _siembra(conn) -> tuple[int, int, int, int, int]:
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', 'c-cyuio') RETURNING id"
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id) VALUES"
        " ('amazon_mx', 'ad_group', 'g-cyuio', %s) RETURNING id",
        (camp,),
    ).fetchone()[0]
    hoja = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES ('amazon_mx', 'keyword', 'k-cyuio', %s, 'EXACT', 't')"
        " RETURNING id",
        (ag,),
    ).fetchone()[0]
    for entidad in (camp, ag, hoja):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (entidad,),
        )
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct)"
        " VALUES (%s, %s, 100, 'MXN', 'LEGACY_FOR_SALES', 0)",
        (camp, _ANCLA),
    )
    config_id = conn.execute(
        "SELECT id FROM ads_campana_config_observation WHERE ad_entity_id = %s", (camp,)
    ).fetchone()[0]
    cv = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('cyuio', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', 'amazon_mx') RETURNING id"
    ).fetchone()[0]
    return camp, hoja, config_id, cv, ciclo


# ---------------------------------------------------------------------------
# Pruebas de contrato previas (Task 5)
# ---------------------------------------------------------------------------


@_skip_db
def test_tx3_guarda_evaluacion_shadow_y_no_toca_campana_ajuste():
    with _db() as (conn, _dsn):
        camp, _hoja, config_id, cv, ciclo = _siembra(conn)
        contadores = _Contadores()
        contadores.ubicaciones.append(
            (_caso(camp, config_id), "shadow", MoverUbicacion("arriba_de_busqueda", 0, 15, "x"))
        )
        cuerpo: dict = {}
        tx3_ubicaciones(conn, ciclo, cv, "amazon_mx", _ANCLA, contadores, {}, cuerpo)
        fila = conn.execute(
            "SELECT modo, veredicto, a_pct FROM ubicacion_evaluacion WHERE cycle_id = %s",
            (ciclo,),
        ).fetchone()
        assert fila == ("shadow", "mover", 15)
        assert conn.execute("SELECT count(*) FROM campana_ajuste").fetchone()[0] == 0
        assert cuerpo["ubicaciones"] == {
            "evaluadas": 1,
            "mover": 1,
            "en_practica": 1,
            "aplicadas": 0,
        }


@_skip_db
def test_tx3_marca_producto_sin_evidencia_si_la_hoja_perdio_meta():
    with _db() as (conn, _dsn):
        camp, hoja, config_id, cv, ciclo = _siembra(conn)
        contadores = _Contadores()
        contadores.productos = SimpleNamespace(hojas_aptas={hoja: 999})
        contadores.ubicaciones.append(
            (_caso(camp, config_id), "shadow", MoverUbicacion("arriba_de_busqueda", 0, 15, "x"))
        )
        tx3_ubicaciones(conn, ciclo, cv, "amazon_mx", _ANCLA, contadores, {}, {})
        fila = conn.execute(
            "SELECT veredicto, motivo FROM ubicacion_evaluacion WHERE cycle_id = %s",
            (ciclo,),
        ).fetchone()
        assert fila == ("mantener", "producto_sin_evidencia")


@_skip_db
def test_modo_shadow_no_aplica_y_registra_modo_no_live():
    with _db() as (conn, dsn):
        camp, _hoja, config_id, cv, ciclo = _siembra(conn)
        eval_id = conn.execute(
            "INSERT INTO ubicacion_evaluacion (cycle_id, campana_id, platform, modo,"
            " politica, config_version_id, antes_config_id, decided_at, ventana_desde,"
            " ventana_hasta, moneda, entrada, veredicto, motivo, ubicacion, de_pct, a_pct)"
            " VALUES (%s, %s, 'amazon_mx', 'live', 'ubicacion_v1', %s, %s, %s, %s, %s,"
            " 'MXN', %s, 'mover', 'bajo_meta_con_ventaja', 'arriba_de_busqueda', 0, 15)"
            " RETURNING id",
            (
                ciclo,
                camp,
                cv,
                config_id,
                _ANCLA,
                _ANCLA.date() - dt.timedelta(days=20),
                _ANCLA.date() - dt.timedelta(days=3),
                json.dumps(caso_a_json(_caso(camp, config_id))),
            ),
        ).fetchone()[0]
        resumen = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=SimpleNamespace(),
            platform="amazon_mx",
            cycle_id=ciclo,
            settings={"ads_ubicacion_amazon_mx": "shadow"},
            abrir_conexion=lambda: (_ for _ in ()).throw(AssertionError("no se abre conexion")),
        )
        assert resumen.aplicadas == 0
        assert resumen.sin_aplicar == ((eval_id, "modo_no_live"),)


def test_config_write_valida_settings_y_cap_ubicacion():
    nuevo, cambios = config_write.proxima_config(
        {},
        "amazon_mx",
        ubicacion_modo="shadow",
        ubicacion_solo_bajadas=True,
        ubicacion_max_en_espera=3,
        caps={"ubicacion": 2},
    )
    assert nuevo["ads_ubicacion_amazon_mx"] == "shadow"
    assert nuevo["ads_ubicacion_solo_bajadas_amazon_mx"] == "true"
    assert nuevo["ads_ubicacion_max_en_espera_amazon_mx"] == "3"
    assert nuevo["ads_apply_cap_amazon_mx_ubicacion"] == "2"
    assert cambios

    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config({}, "amazon_mx", ubicacion_modo="medio")
    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config({}, "amazon_mx", ubicacion_max_en_espera=-1)

    apagado, _ = config_write.proxima_config(
        {"ads_ubicacion_amazon_mx": "shadow"}, "amazon_mx", ubicacion_modo="off"
    )
    assert "ads_ubicacion_amazon_mx" not in apagado


def test_mantener_no_se_persiste_como_mover():
    from app.ubicacion_io import marca_producto_sin_evidencia

    salida = marca_producto_sin_evidencia(
        [(_caso(1, 1), "shadow", MantenerUbicacion("sin_evidencia_ubicacion"))], []
    )
    assert salida[0][2] == MantenerUbicacion("sin_evidencia_ubicacion")


# ---------------------------------------------------------------------------
# Arnes E2E real: `cycle.corre_ciclo` / `cycle.atender_plataforma` + HTTP mock
# ---------------------------------------------------------------------------


class _MuerteWorker(BaseException):
    """Simula caida brusca (SIGKILL) del proceso a mitad de fase."""


class _AmazonE2E:
    """API de Amazon Ads simulada en la frontera HTTP (`httpx.MockTransport`)."""

    def __init__(
        self,
        *,
        campanas: dict[str, dict] | None = None,
        bids: dict[str, str] | None = None,
    ):
        self.campanas: dict[str, dict] = {k: dict(v) for k, v in (campanas or {}).items()}
        self.bids: dict[str, str] = dict(bids or {})
        self.puts_campana: list[dict] = []
        self.puts_bid: list[dict] = []
        self.listas_campana: int = 0
        self.status_list_campana: list[int] = []
        self.status_put_campana: list[int] = []
        self.status_put_bid: list[int] = []
        self.aplica_put_al_fallar: bool = False
        self.al_list_campana = None
        self.al_put_campana = None

    def _payload_campana(self, ext: str) -> dict:
        cfg = self.campanas[ext]
        placements = []
        for campo, wire in (
            ("top", "PLACEMENT_TOP"),
            ("resto", "PLACEMENT_REST_OF_SEARCH"),
            ("producto", "PLACEMENT_PRODUCT_PAGE"),
        ):
            val = cfg.get(campo)
            if val is not None:
                placements.append({"placement": wire, "percentage": int(val)})
        return {
            "campaignId": ext,
            "budget": {"budget": float(cfg.get("presupuesto", 500)), "budgetType": "DAILY"},
            "dynamicBidding": {
                "strategy": cfg.get("estrategia", "LEGACY_FOR_SALES"),
                "placementBidding": placements,
            },
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok-e2e", "expires_in": 3600})
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(200, json={"profiles": [_PERFIL_MX]})
        cuerpo = json.loads(request.content) if request.content else {}

        if request.method == "POST" and request.url.path == "/sp/campaigns/list":
            self.listas_campana += 1
            if self.al_list_campana is not None:
                self.al_list_campana(self, cuerpo)
            if self.status_list_campana:
                codigo = self.status_list_campana.pop(0)
                if codigo >= 400:
                    return httpx.Response(codigo, json={"code": f"HTTP_{codigo}"})
            filtro = cuerpo.get("campaignIdFilter", {}).get("include")
            exts = [str(x) for x in filtro] if filtro else list(self.campanas)
            items = [self._payload_campana(ext) for ext in exts if ext in self.campanas]
            return httpx.Response(200, json={"campaigns": items})

        if request.method == "PUT" and request.url.path == "/sp/campaigns":
            obj = cuerpo["campaigns"][0]
            ext = str(obj["campaignId"])
            self.puts_campana.append(obj)
            if self.al_put_campana is not None:
                self.al_put_campana(self, obj)
            codigo = self.status_put_campana.pop(0) if self.status_put_campana else 207
            if codigo < 400 or self.aplica_put_al_fallar:
                bidding = obj.get("dynamicBidding", {})
                mapa = {
                    "PLACEMENT_TOP": "top",
                    "PLACEMENT_REST_OF_SEARCH": "resto",
                    "PLACEMENT_PRODUCT_PAGE": "producto",
                }
                for item in bidding.get("placementBidding", []):
                    clave = mapa.get(item["placement"])
                    if clave and ext in self.campanas:
                        self.campanas[ext][clave] = int(item["percentage"])
            if codigo >= 400:
                return httpx.Response(codigo, json={"code": f"HTTP_{codigo}"})
            return httpx.Response(207, json={"campaigns": {"success": [obj], "error": []}})

        if request.method == "POST" and request.url.path == "/sp/keywords/list":
            filas = [
                {"keywordId": ext, "bid": float(bid), "state": "ENABLED"}
                for ext, bid in sorted(self.bids.items())
            ]
            return httpx.Response(200, json={"keywords": filas})

        if request.method == "PUT" and request.url.path == "/sp/keywords":
            obj = cuerpo["keywords"][0]
            ext = str(obj["keywordId"])
            self.puts_bid.append(obj)
            if self.status_put_bid:
                codigo = self.status_put_bid.pop(0)
                if codigo >= 400:
                    return httpx.Response(codigo, json={"code": f"HTTP_{codigo}"})
            if "bid" in obj:
                self.bids[ext] = str(obj["bid"])
            return httpx.Response(207, json={"keywords": {"success": [obj], "error": []}})

        if request.method == "POST" and request.url.path == "/sp/targets/list":
            return httpx.Response(200, json={"targetingClauses": []})

        raise AssertionError(f"HTTP inesperado en E2E: {request.method} {request.url.path}")


def _fabrica_e2e(amazon: _AmazonE2E):
    def fabrica(conn, *, platform, cycle_id_ejecutor, owner, job_key, tick, transport=None):
        return Aplicador(
            conn,
            platform=platform,
            profile_id=_PERFIL_MX["profileId"],
            credentials=AdsCredentials("cid-e2e", "csec-e2e", "rtok-e2e"),
            cycle_id_ejecutor=cycle_id_ejecutor,
            owner=owner,
            job_key=job_key,
            tick=tick,
            guard_http=tick,
            transport=httpx.MockTransport(amazon),
            sleep=lambda _s: None,
        )

    return fabrica


def _siembra_base_mx(conn, *, settings: dict, ahora: dt.datetime = _AHORA) -> int:
    conn.execute("SET TIME ZONE 'UTC'")
    cv = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('e2e', %s::jsonb) RETURNING id",
        (json.dumps(settings),),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, bid_currency, bid_floor,"
        " bid_ceiling, enabled, mode) VALUES ('platform', 'amazon_mx', 'MXN', 1.00, 45.00,"
        " true, 'live')"
    )
    run_sync = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, ok)"
        " VALUES ('amazon_ads_structure_v2', %s, %s, 1, true) RETURNING id",
        (ahora - dt.timedelta(hours=1), ahora - dt.timedelta(hours=1)),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ads_listado_plataforma (ingest_run_id, platform,"
        " ad_groups_recibidos, product_ads_recibidos, product_ads_sin_grupo)"
        " VALUES (%s, 'amazon_mx', 1, 0, 0)",
        (run_sync,),
    )
    return cv


def _ingest_e2e(conn, ahora: dt.datetime = _AHORA) -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, ok)"
        " VALUES ('amazon_ads_reports_v3', %s, %s, 1, true) RETURNING id",
        (ahora - dt.timedelta(hours=2), ahora - dt.timedelta(hours=2)),
    ).fetchone()[0]


def _crea_campana_e2e(
    conn,
    *,
    ext: str,
    kw_exts: tuple[str, ...] = (),
    top: int = 0,
    estrategia: str = "LEGACY_FOR_SALES",
    presupuesto: str = "500",
    ahora: dt.datetime = _AHORA,
) -> dict:
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES ('amazon_mx', 'campaign', %s) RETURNING id",
        (ext,),
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES ('amazon_mx', 'ad_group', %s, %s) RETURNING id",
        (f"ag-{ext}", camp),
    ).fetchone()[0]
    for eid in (camp, ag):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', %s)",
            (eid, ahora - dt.timedelta(hours=1)),
        )
    kws = []
    for kw_ext in kw_exts or (f"kw-{ext}",):
        kw = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
            " keyword_text) VALUES ('amazon_mx', 'keyword', %s, %s, 'EXACT', %s) RETURNING id",
            (kw_ext, ag, f"term-{kw_ext}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at, current_bid,"
            " bid_currency) VALUES (%s, 'ENABLED', %s, 2.00, 'MXN')",
            (kw, ahora - dt.timedelta(hours=1)),
        )
        kws.append(kw)
    cfg_id = conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
        " ajuste_resto_pct, ajuste_producto_pct)"
        " VALUES (%s, %s, %s, 'MXN', %s, %s, 0, 0) RETURNING id",
        (camp, ahora - dt.timedelta(days=40), presupuesto, estrategia, top),
    ).fetchone()[0]
    return {"campana": camp, "grupo": ag, "hojas": kws, "config_id": cfg_id, "ext": ext}


def _siembra_subida_lista(
    conn,
    datos: dict,
    ingest_id: int,
    *,
    ahora: dt.datetime = _AHORA,
    escala: int = 1,
    con_metricas_hoja: bool = True,
) -> None:
    """Siembra metricas de campana, hojas y placements para que `arriba_de_busqueda`
    califique limpio para subir +15% (`bajo_meta_con_ventaja`)."""
    hoy = ahora.date()
    obs = ahora - dt.timedelta(hours=2)
    camp = datos["campana"]
    # Dias D-12..D-3 (10 dias maduros): arriba gana fuerte (4*escala pedidos,
    # 40 clics, gasto 50*escala, venta 1000*escala); resto y pdp suman 20 clics.
    for i in range(10):
        dia = hoy - dt.timedelta(days=3 + i)
        ped_top = escala if i < 4 else 0
        vta_top = _D("250") * escala if i < 4 else _D("0")
        g_top = _D("5") * escala
        g_resto = _D("2") * escala
        g_pdp = _D("2") * escala
        vta_r = _D("5") * escala
        for pl, cl, co, pe, ve in (
            ("arriba_de_busqueda", 4, g_top, ped_top, vta_top),
            ("resto_de_busqueda", 2, g_resto, 0, vta_r),
            ("paginas_de_producto", 2, g_pdp, 0, vta_r),
        ):
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks, cost,"
                " orders, ad_revenue, source_report_id)"
                " VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN', 200, %s, %s, %s, %s, 'r-e2e')",
                (camp, pl, dia, obs, cl, co, pe, ve),
            )
        conn.execute(
            "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
            " metric_currency, impressions, clicks, cost, orders, ad_revenue, ingest_run_id)"
            " VALUES (%s, %s, %s, 'MXN', 600, 8, %s, %s, %s, %s)",
            (camp, dia, obs, g_top + g_resto + g_pdp, ped_top, vta_top + 2 * vta_r, ingest_id),
        )
    # D-2 y D-1: frescura de watermark, cobertura de inmaduros y 7 dias medidos no topados.
    for d in (2, 1):
        dia = hoy - dt.timedelta(days=d)
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
            " metric_date, observed_at, metric_currency, impressions, clicks, cost,"
            " orders, ad_revenue, source_report_id)"
            " VALUES ('amazon_mx', %s, 'arriba_de_busqueda', %s, %s, 'MXN',"
            " 100, 2, 10, 0, 0, 'r-e2e')",
            (camp, dia, obs),
        )
        conn.execute(
            "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
            " metric_currency, impressions, clicks, cost, orders, ad_revenue, ingest_run_id)"
            " VALUES (%s, %s, %s, 'MXN', 100, 2, 10, 0, 0, %s)",
            (camp, dia, obs, ingest_id),
        )
    if con_metricas_hoja:
        # Hojas activas (no inertes): 10 dias con impresiones y clics moderados.
        for hoja in datos["hojas"]:
            for d in range(1, 11):
                dia = hoy - dt.timedelta(days=d)
                conn.execute(
                    "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                    " observed_at, metric_currency, impressions, clicks, cost, orders,"
                    " ad_revenue, ingest_run_id)"
                    " VALUES (%s, %s, %s, 'MXN', 50, 2, 4, 0, 0, %s)",
                    (hoja, dia, obs, ingest_id),
                )


def _siembra_bid_recortable(
    conn, hoja: int, ingest_id: int, *, ahora: dt.datetime = _AHORA
) -> None:
    """Metricas de hoja sin ventas con gasto alto (600 MXN en D-19..D-10) y pocos
    clics (10 < F_PAUSE) con calendario completo D-90..D-1: provoca recorte R9
    de `bid` bajo `niveles_v3` sin disparar PAUSE."""
    hoy = ahora.date()
    obs = ahora - dt.timedelta(hours=2)
    for d in range(1, 91):
        dia = hoy - dt.timedelta(days=d)
        if 10 <= d < 20:
            imp, clk, cst = 100, 1, 60
        else:
            imp, clk, cst = 10, 0, 0
        conn.execute(
            "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
            " metric_currency, impressions, clicks, cost, orders, ad_revenue, ingest_run_id)"
            " VALUES (%s, %s, %s, 'MXN', %s, %s, %s, 0, 0, %s)",
            (hoja, dia, obs, imp, clk, cst, ingest_id),
        )


def _ciclo_previo_cerrado(conn, *, ahora: dt.datetime = _AHORA) -> int:
    ciclo_prev = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_mx') RETURNING id"
    ).fetchone()[0]
    ayer = ahora - dt.timedelta(days=1)
    conn.execute(
        "UPDATE optimizer_cycle SET status = 'done', finished_at = %s,"
        " decision_committed_at = %s WHERE id = %s",
        (ayer, ayer, ciclo_prev),
    )
    return ciclo_prev


def _inserta_pendiente_automatica(
    conn,
    dsn: str,
    *,
    campana: int,
    config_id: int,
    cv: int,
    ext: str,
    de_pct: int = 0,
    a_pct: int = 15,
    ahora: dt.datetime = _AHORA,
) -> tuple[int, int]:
    """Siembra una evaluacion `live` + intencion pendiente en `campana_ajuste`
    como `app_decide` (mismo estado que deja un PUT/readback 500)."""
    ciclo_prev = _ciclo_previo_cerrado(conn, ahora=ahora)
    caso = _caso(campana, config_id)
    eval_id = conn.execute(
        "INSERT INTO ubicacion_evaluacion (cycle_id, campana_id, platform, modo, politica,"
        " config_version_id, antes_config_id, decided_at, ventana_desde, ventana_hasta,"
        " moneda, entrada, veredicto, motivo, ubicacion, de_pct, a_pct)"
        " VALUES (%s, %s, 'amazon_mx', 'live', 'ubicacion_v1', %s, %s, %s, %s, %s,"
        " 'MXN', %s, 'mover', 'bajo_meta_con_ventaja', 'arriba_de_busqueda', %s, %s)"
        " RETURNING id",
        (
            ciclo_prev,
            campana,
            cv,
            config_id,
            ahora - dt.timedelta(days=1),
            ahora.date() - dt.timedelta(days=20),
            ahora.date() - dt.timedelta(days=3),
            json.dumps(caso_a_json(caso)),
            de_pct,
            a_pct,
        ),
    ).fetchone()[0]
    despues = {
        "campana_externa": ext,
        "presupuesto_diario": "500.0000",
        "moneda": "MXN",
        "estrategia_puja": "LEGACY_FOR_SALES",
        "ajuste_top_pct": a_pct,
        "ajuste_resto_pct": 0,
        "ajuste_producto_pct": 0,
        "fuera_de_amazon": None,
    }
    with psycopg.connect(dsn, autocommit=False) as c_dec:
        c_dec.execute("SET ROLE app_decide")
        ajuste_id = c_dec.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, evaluacion_id)"
            " VALUES (%s, 'amazon_mx', 'ajuste_ubicacion', %s, %s, %s,"
            " 'orbit_ubicacion', 'AUTO_UBICACION', %s) RETURNING id",
            (campana, config_id, json.dumps(despues), f"auto:{eval_id}:e2e", eval_id),
        ).fetchone()[0]
        c_dec.commit()
    return eval_id, ajuste_id


def _settings_live(
    *, cap_ubic: str | None = "2", bid_politica: str | None = None, extra=None
) -> dict:
    base: dict = {
        "ads_optimizer_mode": "live",
        "ads_ubicacion_amazon_mx": "live",
        "ads_target_acos_pct_amazon_mx": "25",
        "ads_apply_cap_amazon_mx_bid": "10",
        "ads_apply_cap_amazon_mx_pause": "2",
        "ads_apply_cap_amazon_mx_negative": "5",
        "ads_apply_cap_amazon_mx_harvest": "2",
    }
    if cap_ubic is not None:
        base["ads_apply_cap_amazon_mx_ubicacion"] = cap_ubic
    if bid_politica is not None:
        base["ads_bid_politica_amazon_mx"] = bid_politica
    if extra:
        base.update(extra)
    return base


# --- P1-1 E2E ---


@_skip_db
def test_e2e_p1_1_campana_sin_impresiones_en_d3_recorta():
    """P1-1 E2E: en `corre_ciclo` real, una campana activa con 580 MXN sin venta
    en D-32..D-4 y 0 impresiones en D-3 (con plataforma fresca en D-3..D-1)
    recorta en vez de bloquearse por `datos_placements_incompletos`."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings={
                "ads_optimizer_mode": "shadow",
                "ads_ubicacion_amazon_mx": "shadow",
                "ads_target_acos_pct_amazon_mx": "20",
            },
        )
        ingest = _ingest_e2e(conn)
        c_rec = _crea_campana_e2e(conn, ext="91001", top=40)
        c_fresca = _crea_campana_e2e(conn, ext="91002", top=0)
        hoy = _AHORA.date()
        obs = _AHORA - dt.timedelta(hours=2)
        for d in (3, 2, 1):
            dia = hoy - dt.timedelta(days=d)
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks, cost,"
                " orders, ad_revenue, source_report_id)"
                " VALUES ('amazon_mx', %s, 'arriba_de_busqueda', %s, %s, 'MXN',"
                " 100, 2, 10, 0, 0, 'r')",
                (c_fresca["campana"], dia, obs),
            )
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
                " metric_currency, impressions, clicks, cost, orders, ad_revenue, ingest_run_id)"
                " VALUES (%s, %s, %s, 'MXN', 100, 2, 10, 0, 0, %s)",
                (c_fresca["campana"], dia, obs, ingest),
            )
        for d in range(4, 33):
            dia = hoy - dt.timedelta(days=d)
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks, cost,"
                " orders, ad_revenue, source_report_id)"
                " VALUES ('amazon_mx', %s, 'arriba_de_busqueda', %s, %s, 'MXN',"
                " 100, 5, 20, 0, 0, 'r')",
                (c_rec["campana"], dia, obs),
            )
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
                " metric_currency, impressions, clicks, cost, orders, ad_revenue, ingest_run_id)"
                " VALUES (%s, %s, %s, 'MXN', 100, 5, 20, 0, 0, %s)",
                (c_rec["campana"], dia, obs, ingest),
            )
        for hoja in (*c_rec["hojas"], *c_fresca["hojas"]):
            for d in range(1, 11):
                conn.execute(
                    "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                    " observed_at, metric_currency, impressions, clicks, cost, orders,"
                    " ad_revenue, ingest_run_id)"
                    " VALUES (%s, %s, %s, 'MXN', 50, 2, 4, 0, 0, %s)",
                    (hoja, hoy - dt.timedelta(days=d), obs, ingest),
                )

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec, platform="amazon_mx", owner=_OWNER, decided_at=_AHORA, heartbeat_cada=1
            )
        assert res.status == "done"
        fila = conn.execute(
            "SELECT veredicto, motivo, de_pct, a_pct FROM ubicacion_evaluacion"
            " WHERE cycle_id = %s AND campana_id = %s",
            (res.cycle_id, c_rec["campana"]),
        ).fetchone()
        assert fila == ("mover", "gasto_sin_venta", 40, 25)


# --- P1-2 E2E (500 aislado, 401 global, shadow/off y post-cierre) ---


@_skip_db
def test_e2e_p1_2_error_reconcilia_ubicaciones_no_tira_apply_del_ciclo(monkeypatch):
    """P1-2 Rama A: un 500 al reconciliar 1 ubicacion pendiente queda aislado en
    `ubicaciones_reconcilia_error` y NO anula `aplicador`: el bid del ciclo SI se aplica."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with _db() as (conn, dsn):
        cv = _siembra_base_mx(conn, settings=_settings_live(bid_politica="niveles_v3"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="92001", kw_exts=("kw-92001",), top=0)
        _siembra_subida_lista(conn, c1, ingest, con_metricas_hoja=False)
        _siembra_bid_recortable(conn, c1["hojas"][0], ingest)
        _inserta_pendiente_automatica(
            conn, dsn, campana=c1["campana"], config_id=c1["config_id"], cv=cv, ext="92001"
        )

        amazon = _AmazonE2E(
            campanas={"92001": {"top": 0, "estrategia": "LEGACY_FOR_SALES"}},
            bids={"kw-92001": "2.00"},
        )
        amazon.status_list_campana = [500] * 4

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        notas = json.loads(res.notes)
        assert res.status == "done"
        assert "ubicaciones_reconcilia_error" in notas["apply"]
        assert "apply_error" not in notas["apply"]
        assert notas["apply"]["bids_aplicados"] == 1
        assert len(amazon.puts_bid) == 1
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({c1["campana"]})


@_skip_db
def test_e2e_p1_2_401_reconcilia_ubicaciones_aborta_apply_global():
    """P1-2 (ajuste Opus): 401/403 al reconciliar ubicaciones ES fallo global
    de identidad -> `aplicador=None`, `apply_error` y 0 PUTs de bids."""
    with _db() as (conn, dsn):
        cv = _siembra_base_mx(conn, settings=_settings_live(bid_politica="niveles_v3"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="92002", kw_exts=("kw-92002",), top=0)
        _siembra_subida_lista(conn, c1, ingest, con_metricas_hoja=False)
        _siembra_bid_recortable(conn, c1["hojas"][0], ingest)
        _inserta_pendiente_automatica(
            conn, dsn, campana=c1["campana"], config_id=c1["config_id"], cv=cv, ext="92002"
        )

        amazon = _AmazonE2E(
            campanas={"92002": {"top": 0, "estrategia": "LEGACY_FOR_SALES"}},
            bids={"kw-92002": "2.00"},
        )
        amazon.status_list_campana = [403]

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        notas = json.loads(res.notes)
        assert res.status == "degraded"
        assert "apply_error" in notas["apply"]
        assert len(amazon.puts_bid) == 0
        assert len(amazon.puts_campana) == 0


@_skip_db
@pytest.mark.parametrize("modo_global", ["shadow", "off"])
def test_e2e_p1_2_reconcilia_ubicaciones_en_shadow_y_off(modo_global):
    """P1-2 Rama B: bajar `ads_optimizer_mode` a `shadow` u `off` con una
    intencion pendiente sigue reconciliandola (solo lectura + sello, 0 PUTs)."""
    with _db() as (conn, dsn):
        cv = _siembra_base_mx(
            conn,
            settings={
                "ads_optimizer_mode": modo_global,
                "ads_ubicacion_amazon_mx": "shadow",
                "ads_target_acos_pct_amazon_mx": "25",
            },
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="92003", top=0)
        _siembra_subida_lista(conn, c1, ingest)
        _eval_id, ajuste_id = _inserta_pendiente_automatica(
            conn, dsn, campana=c1["campana"], config_id=c1["config_id"], cv=cv, ext="92003"
        )
        amazon = _AmazonE2E(campanas={"92003": {"top": 15, "estrategia": "LEGACY_FOR_SALES"}})

        with _como_decide(conn, dsn) as c_dec:
            ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert len(amazon.puts_campana) == 0
        assert amazon.listas_campana == 1
        assert conn.execute(
            "SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE id = %s", (ajuste_id,)
        ).fetchone() == (True,)
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset()


@_skip_db
def test_e2e_readback_500_se_reconcilia_mismo_dia(monkeypatch):
    """P1-2 Rama B + Fronteras 9a/10b: PUT aplica en Amazon, readback da 500 ->
    ciclo cierra `apply.completo=True`; en el siguiente horario del MISMO dia
    (`ciclo_id=None`), `atender_plataforma` reconcilia y sella `confirmado_el`."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live())
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="92004", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        amazon = _AmazonE2E(campanas={"92004": {"top": 0, "estrategia": "LEGACY_FOR_SALES"}})
        # 1a lista: _motivo_consola OK; 2a: _aplica_y_confirma OK; 3a (readback): 500 x4.
        amazon.status_list_campana = [200, 200] + [500] * 4

        with _como_decide(conn, dsn) as c_dec:
            at1 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at1.ciclo is not None and at1.ciclo.status == "done"
        assert len(amazon.puts_campana) == 1
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({c1["campana"]})

        # Segundo horario del mismo dia: ya cerro hoy (ciclo_id=None), pero reconcilia igual.
        with _como_decide(conn, dsn) as c_dec:
            at2 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA + dt.timedelta(hours=2),
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at2.ciclo is None
        assert len(amazon.puts_campana) == 1
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset()
        assert conn.execute(
            "SELECT count(*), bool_and(confirmado_el IS NOT NULL) FROM campana_ajuste"
            " WHERE evaluacion_id IS NOT NULL"
        ).fetchone() == (1, True)


# --- P1-3 E2E (temporada baja sin ads_bid_politica + temporada shadow) ---


def _temporada_noviembre_baja() -> dict:
    meses = ["normal"] * 12
    meses[10] = "baja"  # noviembre (indice 10)
    return {
        "version": "temporada_v1",
        "meses": meses,
        "reduccion_baja_pct": "20",
        "dias_preparacion": 14,
        "mult_cortes_baja": "2",
        "aumento_presupuesto_alta_pct": "20",
    }


@_skip_db
def test_e2e_p1_3_temporada_baja_frena_subida_sin_bid_politica():
    """P1-3 E2E: con `ads_temporada_amazon_mx=live` en mes `baja` y
    `ads_bid_politica_amazon_mx` ausente, el ciclo real frena la subida con
    `temporada_baja_sin_subida`."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings=_settings_live(
                bid_politica=None,
                extra={
                    "ads_temporada": _temporada_noviembre_baja(),
                    "ads_temporada_amazon_mx": "live",
                },
            ),
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="93001", top=0)
        _siembra_subida_lista(conn, c1, ingest)
        amazon = _AmazonE2E(campanas={"93001": {"top": 0}})

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        fila = conn.execute(
            "SELECT veredicto, motivo, entrada->>'fase_temporada'"
            " FROM ubicacion_evaluacion WHERE cycle_id = %s AND campana_id = %s",
            (res.cycle_id, c1["campana"]),
        ).fetchone()
        assert fila == ("mantener", "temporada_baja_sin_subida", "baja")
        assert len(amazon.puts_campana) == 0


@_skip_db
def test_e2e_temporada_shadow_no_frena_subida_ubicacion():
    """Frontera 3b (P2-6 cerrado con P1-3): `ads_temporada_amazon_mx=shadow`
    es practica y no bloquea subidas de ubicacion."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings=_settings_live(
                bid_politica=None,
                extra={
                    "ads_temporada": _temporada_noviembre_baja(),
                    "ads_temporada_amazon_mx": "shadow",
                },
            ),
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="93002", top=0)
        _siembra_subida_lista(conn, c1, ingest)
        amazon = _AmazonE2E(campanas={"93002": {"top": 0}})

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        fila = conn.execute(
            "SELECT veredicto, motivo, entrada->>'fase_temporada'"
            " FROM ubicacion_evaluacion WHERE cycle_id = %s AND campana_id = %s",
            (res.cycle_id, c1["campana"]),
        ).fetchone()
        assert fila == ("mover", "bajo_meta_con_ventaja", None)
        assert len(amazon.puts_campana) == 1


# --- P1-4 E2E (fallo lectura productos TX2 y fallo guardado productos TX3) ---


@_skip_db
def test_e2e_p1_4_fallo_lectura_productos_tx2_abstiene_campana_mixta(monkeypatch):
    """P1-4 E2E: en una campana mixta (k1 previa `margen_producto=10%`, k2
    `setting_plataforma=25%`), si `lee_entradas_productos` falla en TX2, TX3
    guarda `mantener('producto_sin_evidencia')` y no envia PUT."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings=_settings_live(
                extra={"ads_producto_meta_amazon_mx": "live"},
            ),
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="94001", kw_exts=("kw-94a", "kw-94b"), top=0)
        _siembra_subida_lista(conn, c1, ingest)
        # Sembrar target previo `margen_producto` = 10% solo para la hoja 0.
        ciclo_prev = _ciclo_previo_cerrado(conn)
        conn.execute(
            "INSERT INTO target_acos_ciclo (cycle_id, ad_entity_id, decided_at,"
            " target_acos_pct, procedencia) VALUES (%s, %s, %s, 10, 'margen_producto')",
            (ciclo_prev, c1["hojas"][0], _AHORA - dt.timedelta(days=1)),
        )

        def falla_lectura(*_a, **_kw):
            raise RuntimeError("fallo lectura productos TX2")

        monkeypatch.setattr(productos_io, "lee_entradas_productos", falla_lectura)
        amazon = _AmazonE2E(campanas={"94001": {"top": 0}})

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        fila = conn.execute(
            "SELECT veredicto, motivo FROM ubicacion_evaluacion"
            " WHERE cycle_id = %s AND campana_id = %s",
            (res.cycle_id, c1["campana"]),
        ).fetchone()
        assert fila == ("mantener", "producto_sin_evidencia")
        assert len(amazon.puts_campana) == 0


# --- P1-5 E2E (401/403 no quema eval #1 y ejecutor de recuperacion la aplica) ---


@_skip_db
def test_e2e_p1_5_401_403_no_quema_evaluacion_y_recuperacion_la_aplica():
    """P1-5 E2E: con 2 campanas listas para subir, 403 en `_motivo_consola` de
    la #1 aborta sin descartarla (`apply.completo` ausente); el ejecutor de
    recuperacion siguiente aplica AMBAS (#1 y #2) en orden."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live(cap_ubic="2"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="95001", top=0)
        c2 = _crea_campana_e2e(conn, ext="95002", top=0)
        _siembra_subida_lista(conn, c1, ingest, escala=2)  # mayor holgura -> #1
        _siembra_subida_lista(conn, c2, ingest, escala=1)  # menor holgura -> #2

        amazon = _AmazonE2E(campanas={"95001": {"top": 0}, "95002": {"top": 0}})
        amazon.status_list_campana = [403]

        with _como_decide(conn, dsn) as c_dec:
            at1 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at1.ciclo is not None and at1.ciclo.status == "degraded"
        assert conn.execute("SELECT count(*) FROM ubicacion_sin_aplicar").fetchone()[0] == 0
        assert len(amazon.puts_campana) == 0

        # Siguiente horario del mismo dia: LWA se recupero -> ejecutor aplica ambas.
        with _como_decide(conn, dsn) as c_dec:
            at2 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA + dt.timedelta(hours=1),
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at2.ciclo is not None
        assert at2.ciclo.resume_of == at1.ciclo.cycle_id
        assert at2.ciclo.status == "done"
        assert [p["campaignId"] for p in amazon.puts_campana] == ["95001", "95002"]
        assert (
            conn.execute(
                "SELECT count(*) FROM campana_ajuste WHERE confirmado_el IS NOT NULL"
            ).fetchone()[0]
            == 2
        )


# --- P1-6 E2E (claim perdido antes del PUT y en reconciliacion) ---


@_skip_db
def test_e2e_p1_6_claim_perdido_antes_de_put_bloquea_envio_ubicacion():
    """P1-6 E2E: si otro owner toma el claim justo antes del PUT de ubicacion
    (tras pasar el tick inicial y la lectura previa), `_TransportGuardado`
    aborta el PUT antes de enviarlo a Amazon."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live())
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="96001", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        def roba_claim(api, _cuerpo):
            # Robar claim despues de la 2a lectura (justo antes del PUT /sp/campaigns).
            if api.listas_campana == 2:
                with psycopg.connect(dsn, autocommit=True) as otra:
                    otra.execute(
                        "UPDATE ads_optimizer_lock SET owner = 'otro-worker'"
                        " WHERE job_key = 'ads_optimizer:amazon_mx'"
                    )

        amazon = _AmazonE2E(campanas={"96001": {"top": 0}})
        amazon.al_list_campana = roba_claim

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        notas = json.loads(res.notes)
        assert res.status == "degraded"
        assert notas["apply"].get("apply_abortado_owner") is True
        assert len(amazon.puts_campana) == 0


@_skip_db
def test_e2e_claim_perdido_en_reconcilia_ubicacion_aborta_sin_sellar():
    """Frontera 5e: si el claim se pierde durante la lectura HTTP de
    `reconcilia_automaticos`, se levanta `CicloOcupado` y NO se sella `confirmado_el`."""
    with _db() as (conn, dsn):
        cv = _siembra_base_mx(conn, settings=_settings_live())
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="96002", top=0)
        _siembra_subida_lista(conn, c1, ingest)
        _eval_id, ajuste_id = _inserta_pendiente_automatica(
            conn, dsn, campana=c1["campana"], config_id=c1["config_id"], cv=cv, ext="96002"
        )

        def roba_en_lectura(_api, _cuerpo):
            with psycopg.connect(dsn, autocommit=True) as otra:
                otra.execute(
                    "UPDATE ads_optimizer_lock SET owner = 'otro-worker'"
                    " WHERE job_key = 'ads_optimizer:amazon_mx'"
                )

        amazon = _AmazonE2E(campanas={"96002": {"top": 15}})
        amazon.al_list_campana = roba_en_lectura

        with _como_decide(conn, dsn) as c_dec, pytest.raises(ciclo_mod.CicloOcupado):
            ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert conn.execute(
            "SELECT confirmado_el IS NULL FROM campana_ajuste WHERE id = %s", (ajuste_id,)
        ).fetchone() == (True,)


# --- Fronteras restantes: pujas pendientes, cuota fail-closed, cambio de modo,
#     cambio de dia, reinicio a mitad de fase, TX3 claim perdido y sin meta ---


@_skip_db
def test_e2e_pujas_inciertas_apartan_ubicaciones():
    """Frontera 8e: si un bid del ciclo queda incierto (PUT 500), ubicaciones
    se aparta con `pujas_pendientes` sin cuota ni PUT."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live(bid_politica="niveles_v3"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="97001", kw_exts=("kw-97001",), top=0)
        _siembra_subida_lista(conn, c1, ingest, con_metricas_hoja=False)
        _siembra_bid_recortable(conn, c1["hojas"][0], ingest)

        amazon = _AmazonE2E(campanas={"97001": {"top": 0}}, bids={"kw-97001": "2.00"})
        amazon.status_put_bid = [500]

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert len(amazon.puts_bid) == 1
        assert len(amazon.puts_campana) == 0
        assert conn.execute("SELECT motivo FROM ubicacion_sin_aplicar").fetchall() == [
            ("pujas_pendientes",)
        ]
        assert res.status == "done"


@_skip_db
def test_e2e_cuota_ausente_fail_closed():
    """Frontera 7a: sin clave `ads_apply_cap_amazon_mx_ubicacion` en `live`,
    descarta con `cuota_agotada` y 0 PUTs."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live(cap_ubic=None))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="97002", top=0)
        _siembra_subida_lista(conn, c1, ingest)
        amazon = _AmazonE2E(campanas={"97002": {"top": 0}})

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert res.status == "done"
        assert len(amazon.puts_campana) == 0
        assert conn.execute("SELECT motivo FROM ubicacion_sin_aplicar").fetchall() == [
            ("cuota_agotada",)
        ]


@_skip_db
def test_e2e_cambio_de_modo_entre_tx3_y_tx4_y_vuelta_a_live():
    """Frontera 11b/11c: si `ads_ubicacion_amazon_mx` baja a `shadow` tras TX3,
    TX4 descarta con `modo_no_live` y 0 PUTs; al volver a `live` al dia
    siguiente nunca aplica evaluaciones `shadow` viejas y evalua fresco."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live(bid_politica="niveles_v3"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="97003", kw_exts=("kw-97003",), top=0)
        _siembra_subida_lista(conn, c1, ingest, con_metricas_hoja=False)
        _siembra_bid_recortable(conn, c1["hojas"][0], ingest)

        # Cuando corre el PUT de bid (entre TX3 y ubicaciones), bajar ubicaciones a shadow.
        class _AmazonBajaModo(_AmazonE2E):
            def __call__(self, request):
                resp = super().__call__(request)
                if request.method == "PUT" and request.url.path == "/sp/keywords":
                    cfg_baja = {**_settings_live(), "ads_ubicacion_amazon_mx": "shadow"}
                    with psycopg.connect(dsn, autocommit=True) as otra:
                        otra.execute(
                            "INSERT INTO config_version (label, settings)"
                            " VALUES ('baja', %s::jsonb)",
                            (json.dumps(cfg_baja),),
                        )
                return resp

        amazon = _AmazonBajaModo(campanas={"97003": {"top": 0}}, bids={"kw-97003": "2.00"})
        with _como_decide(conn, dsn) as c_dec:
            res1 = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert res1.status == "done"
        assert len(amazon.puts_campana) == 0
        assert conn.execute("SELECT motivo FROM ubicacion_sin_aplicar").fetchall() == [
            ("modo_no_live",)
        ]

        # Vuelta a live: nuevo ciclo aplica fresco.
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('vuelve', %s::jsonb)",
            (json.dumps(_settings_live()),),
        )
        with _como_decide(conn, dsn) as c_dec:
            res2 = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert res2.status == "done"
        assert len(amazon.puts_campana) == 1


@_skip_db
def test_e2e_cambio_de_dia_no_aplica_evaluacion_de_ayer():
    """Frontera 10c: una evaluacion `live` del dia D que fallo por 403 no se
    aplica en D+1; D+1 decide fresco con su propio `cycle_id`."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live())
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="97004", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        amazon = _AmazonE2E(campanas={"97004": {"top": 0}})
        amazon.status_list_campana = [403]
        with _como_decide(conn, dsn) as c_dec:
            at_d1 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at_d1.ciclo is not None and at_d1.ciclo.status == "degraded"
        eval_ayer = conn.execute(
            "SELECT id FROM ubicacion_evaluacion WHERE cycle_id = %s", (at_d1.ciclo.cycle_id,)
        ).fetchone()[0]

        # Dia D+1 con ubicaciones en shadow: no debe aplicar la evaluacion `live` de ayer.
        manana = _AHORA + dt.timedelta(days=1)
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('d2', %s::jsonb)",
            (json.dumps({**_settings_live(), "ads_ubicacion_amazon_mx": "shadow"}),),
        )
        run_sync = conn.execute(
            "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, ok)"
            " VALUES ('amazon_ads_structure_v2', %s, %s, 1, true) RETURNING id",
            (manana - dt.timedelta(hours=1), manana - dt.timedelta(hours=1)),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_listado_plataforma (ingest_run_id, platform,"
            " ad_groups_recibidos, product_ads_recibidos, product_ads_sin_grupo)"
            " VALUES (%s, 'amazon_mx', 1, 0, 0)",
            (run_sync,),
        )
        with _como_decide(conn, dsn) as c_dec:
            at_d2 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=manana,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at_d2.ciclo is not None and at_d2.ciclo.resume_of is None
        assert len(amazon.puts_campana) == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM campana_ajuste WHERE evaluacion_id = %s", (eval_ayer,)
            ).fetchone()[0]
            == 0
        )


@_skip_db
def test_e2e_reinicio_a_mitad_de_apply_retoma_sin_duplicar():
    """Frontera 12b: si el worker muere entre la eval #1 (ya confirmada) y la
    eval #2, el ejecutor de recuperacion salta #1 y envia solo #2 (1 PUT cada una)."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live(cap_ubic="2"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="98001", top=0)
        c2 = _crea_campana_e2e(conn, ext="98002", top=0)
        _siembra_subida_lista(conn, c1, ingest, escala=2)
        _siembra_subida_lista(conn, c2, ingest, escala=1)

        def muere_en_segunda(api, _cuerpo):
            # 1a eval usa 3 listas (_motivo_consola, pre-put, readback); la 4a lista es eval #2.
            if api.listas_campana == 4:
                raise _MuerteWorker("SIGKILL entre eval 1 y eval 2")

        amazon = _AmazonE2E(campanas={"98001": {"top": 0}, "98002": {"top": 0}})
        amazon.al_list_campana = muere_en_segunda

        with _como_decide(conn, dsn) as c_dec, pytest.raises(_MuerteWorker):
            ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert [p["campaignId"] for p in amazon.puts_campana] == ["98001"]

        amazon.al_list_campana = None
        with _como_decide(conn, dsn) as c_dec:
            at2 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA + dt.timedelta(minutes=30),
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at2.ciclo is not None and at2.ciclo.status == "done"
        assert at2.ciclo.resume_of is not None
        assert [p["campaignId"] for p in amazon.puts_campana] == ["98001", "98002"]
        assert (
            conn.execute(
                "SELECT count(*) FROM campana_ajuste WHERE confirmado_el IS NOT NULL"
            ).fetchone()[0]
            == 2
        )


@_skip_db
def test_e2e_reinicio_post_insert_pre_sello_converge_en_reconciliacion():
    """Frontera 12c: si el worker muere justo despues del PUT (antes de sellar),
    el ejecutor siguiente reconcilia la fila pendiente sin enviar un 2o PUT."""
    with _db() as (conn, dsn):
        _siembra_base_mx(conn, settings=_settings_live(cap_ubic="2"))
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="98003", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        def muere_en_readback(api, _cuerpo):
            if api.listas_campana == 3:  # readback post-PUT
                raise _MuerteWorker("SIGKILL antes de sellar")

        amazon = _AmazonE2E(campanas={"98003": {"top": 0}})
        amazon.al_list_campana = muere_en_readback

        with _como_decide(conn, dsn) as c_dec, pytest.raises(_MuerteWorker):
            ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert len(amazon.puts_campana) == 1
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({c1["campana"]})

        amazon.al_list_campana = None
        with _como_decide(conn, dsn) as c_dec:
            at2 = ciclo_mod.atender_plataforma(
                c_dec,
                "amazon_mx",
                owner=_OWNER,
                ahora=_AHORA + dt.timedelta(minutes=30),
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        assert at2.ciclo is not None and at2.ciclo.status == "done"
        assert len(amazon.puts_campana) == 1
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset()
        assert conn.execute(
            "SELECT count(*), bool_and(confirmado_el IS NOT NULL) FROM campana_ajuste"
        ).fetchone() == (1, True)


@_skip_db
def test_e2e_ubicacion_off_no_escribe_ni_toca_http():
    """Frontera 1f: con `ads_ubicacion_amazon_mx` ausente (`off`), el ciclo no
    escribe `ubicacion_evaluacion` ni llama a `/sp/campaigns/list`."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings={"ads_optimizer_mode": "shadow", "ads_target_acos_pct_amazon_mx": "25"},
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="99001", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec, platform="amazon_mx", owner=_OWNER, decided_at=_AHORA, heartbeat_cada=1
            )
        assert res.status == "done"
        assert conn.execute("SELECT count(*) FROM ubicacion_evaluacion").fetchone()[0] == 0


@_skip_db
def test_e2e_sin_meta_campana_abstiene():
    """Frontera 2c: sin target de plataforma ni goal con target, TX2 congela
    `meta=None` y guarda `mantener('sin_meta_campana')`."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings={"ads_optimizer_mode": "shadow", "ads_ubicacion_amazon_mx": "shadow"},
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="99002", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec, platform="amazon_mx", owner=_OWNER, decided_at=_AHORA, heartbeat_cada=1
            )
        assert res.status == "done"
        assert conn.execute(
            "SELECT veredicto, motivo FROM ubicacion_evaluacion WHERE cycle_id = %s",
            (res.cycle_id,),
        ).fetchone() == ("mantener", "sin_meta_campana")


@_skip_db
def test_e2e_claim_perdido_en_tx3_no_deja_ubicacion_evaluacion(monkeypatch):
    """Frontera 4a: si el claim se pierde justo antes de `_compromete_tx3`,
    toda TX3 revierte atomicamente y no queda ninguna fila en `ubicacion_evaluacion`."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings={
                "ads_optimizer_mode": "shadow",
                "ads_ubicacion_amazon_mx": "shadow",
                "ads_target_acos_pct_amazon_mx": "25",
            },
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="99003", top=0)
        _siembra_subida_lista(conn, c1, ingest)

        real_compromete = ciclo_mod._compromete_tx3

        def roba_antes_de_sellar(*args, **kwargs):
            with psycopg.connect(dsn, autocommit=True) as otra:
                otra.execute(
                    "UPDATE ads_optimizer_lock SET owner = 'otro-tx3'"
                    " WHERE job_key = 'ads_optimizer:amazon_mx'"
                )
            return real_compromete(*args, **kwargs)

        monkeypatch.setattr(ciclo_mod, "_compromete_tx3", roba_antes_de_sellar)
        with _como_decide(conn, dsn) as c_dec, pytest.raises(ciclo_mod.CicloOcupado):
            ciclo_mod.corre_ciclo(
                c_dec, platform="amazon_mx", owner=_OWNER, decided_at=_AHORA, heartbeat_cada=1
            )
        assert conn.execute("SELECT count(*) FROM ubicacion_evaluacion").fetchone()[0] == 0


@_skip_db
def test_e2e_fallo_guardado_productos_tx3_abstiene_campana(monkeypatch):
    """Frontera 2b: si `productos_io.guarda_evaluaciones` falla en TX3 cuando
    la hoja era `hoja_apta` de producto, TX3 fuerza `mantener('producto_sin_evidencia')`
    y no envia PUT."""
    with _db() as (conn, dsn):
        _siembra_base_mx(
            conn,
            settings=_settings_live(extra={"ads_producto_meta_amazon_mx": "live"}),
        )
        ingest = _ingest_e2e(conn)
        c1 = _crea_campana_e2e(conn, ext="99004", kw_exts=("kw-99004",), top=0)
        _siembra_subida_lista(conn, c1, ingest)

        # Sembrar una entrada de producto valida via lee_entradas_productos para que
        # la hoja entre en `hojas_aptas`, y hacer fallar `guarda_evaluaciones` en TX3.
        from app.optimizer import goals as g_mod
        from app.optimizer import productos as prod_mod

        hoy = _AHORA.date()
        vent_h = prod_mod.Ventana(hoy - dt.timedelta(days=90), hoy - dt.timedelta(days=2))
        entrada_prod = prod_mod.EntradaProducto(
            producto=prod_mod.Producto("amazon_mx", 101),
            decidido_el=_AHORA,
            margen=g_mod.MedicionMargen(
                margen_neto_pct=_D("40"),
                cobertura=_D("1.00"),
                dias_con_venta=45,
                venta_cubierta=_D("10000"),
                ledger_fresco_at=_AHORA - dt.timedelta(hours=1),
                moneda="MXN",
                ventana_desde=hoy - dt.timedelta(days=105),
                ventana_hasta=hoy - dt.timedelta(days=15),
            ),
            fraccion=_D("0.5"),
            ventana_halo=vent_h,
            dias_halo=(),
            actividad_pausa=prod_mod.SinDato(("sin_filas_en_ventana",)),
            anuncios=(),
            grupos_aptos_para_meta=(
                prod_mod.GrupoAptoParaMeta(
                    grupo_id=c1["grupo"],
                    campana_id=c1["campana"],
                    hojas=tuple(c1["hojas"]),
                    ventana_evidencia_hojas=vent_h,
                    referencias_identidad_y_halo=(),
                    huella_alcance="sha256:e2e",
                ),
            ),
            impedimentos=(),
            limites_pausa=prod_mod.LimitesPausa(
                clics_min=30, gasto_min=prod_mod.Dinero(_D("250"), "MXN")
            ),
        )
        monkeypatch.setattr(
            productos_io,
            "lee_entradas_productos",
            lambda *_a, **_kw: [entrada_prod],
        )
        monkeypatch.setattr(
            productos_io,
            "guarda_evaluaciones",
            lambda *_a, **_kw: productos_io.ResultadoGuardado(
                ids={}, retiros_cerrados=(), error="fallo guardado productos TX3"
            ),
        )
        amazon = _AmazonE2E(campanas={"99004": {"top": 0}})

        with _como_decide(conn, dsn) as c_dec:
            res = ciclo_mod.corre_ciclo(
                c_dec,
                platform="amazon_mx",
                owner=_OWNER,
                decided_at=_AHORA,
                heartbeat_cada=1,
                aplicador_factory=_fabrica_e2e(amazon),
            )
        fila = conn.execute(
            "SELECT veredicto, motivo FROM ubicacion_evaluacion"
            " WHERE cycle_id = %s AND campana_id = %s",
            (res.cycle_id, c1["campana"]),
        ).fetchone()
        assert fila == ("mantener", "producto_sin_evidencia")
        assert len(amazon.puts_campana) == 0
