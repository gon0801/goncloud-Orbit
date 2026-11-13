"""ORBIT 07 M5/M6: episodio economico del ultimo cambio y repeticion.

- M5: la lectura congela las ventanas economicas [x-7, x-1] y [x+1, x+7]
  del ultimo cambio de bid, separa madurez, comparabilidad y aplicabilidad, y
  no fabrica contribucion sin margen compatible.
- M6: la politica repite un recorte con ventas conservadas y ACoS mejor, y
  frena una subida cuyo trafico adicional no compensa el gasto. Sin episodio
  maduro, aplicable y comparable, la trayectoria vigente decide como antes.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from psycopg.types.json import Json
from test_cycle import (
    DECIDED_AT,
    _config_version,
    _entidad,
    _estado,
    _metrica,
    _obs,
    _run,
)
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import cycle as ciclo
from app.lecturas_caso import lee_plataforma
from app.optimizer.caso import (
    VERSION_ECONOMIA_CAMBIO,
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EpisodioEconomico,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
    Trayectoria,
    VentanaEconomica,
)
from app.optimizer.economia_cambio import SIN_EVIDENCIA, juicio_repeticion
from app.optimizer.politica import MOTIVO_SUBIDA_SIN_ECONOMIA

_SIN_PG = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextlib.contextmanager
def _db_completa(prefijo: str):
    """Base desechable con TODAS las migraciones de produccion.

    La lectura de M5 cruza ads_campana_config_observation (0061),
    campana_ajuste (0067) y precio_cambio (0039); el subconjunto de
    _db_temporal no las trae y una sonda sobre una base incompleta no prueba
    el contrato real.
    """
    import psycopg
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        raiz = Path(__file__).resolve().parents[1] / "migrations"
        for ruta in sorted(raiz.glob("*.sql")):
            if "_reversa_" in ruta.name or ruta.name.startswith("0011_"):
                continue
            conn.execute(ruta.read_text(encoding="utf-8"))
        yield conn, None
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


_SYNCED = DECIDED_AT - dt.timedelta(hours=4)
_X = DECIDED_AT.date() - dt.timedelta(days=33)


def _ventana(desde: dt.date, gasto, venta, venta_mismo_sku=None, pedidos=5, clics=100):
    return VentanaEconomica(
        desde=desde,
        hasta=desde + dt.timedelta(days=6),
        gasto=Decimal(gasto) if gasto is not None else None,
        venta=Decimal(venta) if venta is not None else None,
        venta_mismo_sku=(
            Decimal(venta_mismo_sku if venta_mismo_sku is not None else venta)
            if venta is not None
            else None
        ),
        pedidos=pedidos,
        clics=clics,
    )


def _episodio(
    *,
    pre_gasto="200",
    pre_venta="1000",
    post_gasto="150",
    post_venta="1000",
    estado="maduro",
    motivo=None,
    aplicable=True,
    margen="8",
):
    return EpisodioEconomico(
        version=VERSION_ECONOMIA_CAMBIO,
        fecha_cambio=_X,
        moneda="USD",
        pre=_ventana(_X - dt.timedelta(days=7), pre_gasto, pre_venta),
        post=_ventana(_X + dt.timedelta(days=1), post_gasto, post_venta),
        estado=estado,
        motivo=motivo,
        aplicable=aplicable,
        motivo_aplicabilidad=None if aplicable else "cambio_posterior",
        margen_pct=Decimal(margen) if margen is not None else None,
        contribucion_pre=(
            Decimal(pre_venta) * Decimal(margen) / 100 - Decimal(pre_gasto)
            if margen is not None
            else None
        ),
        contribucion_post=(
            Decimal(post_venta) * Decimal(margen) / 100 - Decimal(post_gasto)
            if margen is not None
            else None
        ),
    )


def _cambio_pasado(*, antes="1.00", despues="0.85"):
    return CambioBid(
        fecha=_X,
        bid_antes=Decimal(antes),
        bid_despues=Decimal(despues),
        origen="motor",
    )


def _propia_sin_pedidos() -> EvidenciaNivel:
    return EvidenciaNivel(
        reciente=Tramo(
            clics=10,
            pedidos=0,
            venta=Decimal("0"),
            gasto=Decimal("41"),
            impresiones=410,
        ),
        antiguo=Tramo(clics=0, pedidos=0, venta=Decimal("0"), gasto=Decimal("0"), impresiones=0),
    )


def _propia_vendedora() -> EvidenciaNivel:
    return EvidenciaNivel(
        reciente=Tramo(
            clics=40,
            pedidos=2,
            venta=Decimal("200"),
            gasto=Decimal("20"),
            impresiones=1000,
        ),
        antiguo=Tramo(clics=0, pedidos=0, venta=Decimal("0"), gasto=Decimal("0"), impresiones=0),
    )


def _caso(
    *,
    propia: EvidenciaNivel,
    bid: str,
    cambios: tuple[CambioBid, ...],
    efecto: EfectoCambio,
    episodio: EpisodioEconomico | None,
    target: str = "25",
    equilibrio: str | None = None,
) -> CasoHoja:
    return CasoHoja(
        plataforma="amazon_us",
        hoja_id=4925,
        ad_group_id=3927,
        bid=BidVigente(
            valor=Decimal(bid), moneda="USD", piso=Decimal("0.40"), techo=Decimal("2.50")
        ),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="USD",
                equilibrio_acos_pct=Decimal(equilibrio) if equilibrio is not None else None,
                gasto_para_concluir=Decimal("36"),
                confianza_recorte=Decimal("0.80"),
                confianza_subida=Decimal("0.70"),
            ),
            target_acos_pct=Decimal(target),
        ),
        propia=propia,
        pedidos_inmaduros=0,
        grupo=(_propia_vendedora() if (propia.pedidos_crudos() or 0) > 0 else None),
        cuenta=(_propia_vendedora() if (propia.pedidos_crudos() or 0) > 0 else None),
        precio=PrecioVentana(gasto=Decimal("5"), clics=10),
        trayectoria=Trayectoria(cambios=cambios, efecto=efecto, episodio=episodio),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=Decimal("40"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=dt.date(2026, 5, 24),
        ventana_hasta=dt.date(2026, 8, 12),
        observado_al=DECIDED_AT - dt.timedelta(days=3),
    )


def _efecto_corte() -> EfectoCambio:
    """Recorte del motor con el trafico por debajo de 0.70: R16 frenaria."""
    return EfectoCambio(
        dias_post=20,
        impresiones_pre7=700,
        clics_pre7=70,
        impresiones_post=350,
        dias_post_trafico=7,
        clics_post=40,
        gasto_post=Decimal("5"),
        clics_pre=70,
        gasto_pre=Decimal("40"),
        vendia=True,
    )


def _efecto_subida() -> EfectoCambio:
    """Subida del motor con trafico 1.10: R16 no frena por trafico."""
    return EfectoCambio(
        dias_post=20,
        impresiones_pre7=700,
        clics_pre7=70,
        impresiones_post=770,
        dias_post_trafico=7,
        clics_post=40,
        gasto_post=Decimal("20"),
        clics_pre=70,
        gasto_pre=Decimal("20"),
        vendia=True,
    )


def _corre_decisora(monkeypatch, caso: CasoHoja):
    """_procesa_decisora con lecturas fijas: decide() corre de verdad."""
    from types import SimpleNamespace

    from app.optimizer import goals as g

    monkeypatch.setattr(ciclo.windows, "ventana_cortes", lambda *_: None)
    monkeypatch.setattr(ciclo.g, "en_cooldown", lambda *_a, **_k: False)
    goal = g.Goal(
        scope="platform",
        ad_entity_id=None,
        platform="amazon_us",
        target_acos_pct=Decimal("25"),
        bid_floor=Decimal("0.40"),
        bid_ceiling=Decimal("2.50"),
        bid_currency="USD",
        harvest_campaign_id=None,
        harvest_ad_group_id=None,
        harvest_default_bid=None,
        enabled=True,
        mode="shadow",
    )
    lecturas = SimpleNamespace(caso=lambda _conn, **_k: caso)
    contadores = ciclo._Contadores()
    pendientes: list = []
    ciclo._procesa_decisora(
        object(),
        fila=(4925, 3927, 3926, caso.bid.valor, "USD", "ENABLED", None, "ENABLED", "ENABLED"),
        platform="amazon_us",
        setting_target=None,
        goals=(goal, {}),
        modo="shadow",
        decided_at=DECIDED_AT,
        contadores=contadores,
        pendientes=pendientes,
        tick=lambda: None,
        evidencia_ad_groups={},
        corte_pause_por_grupo={},
        bloqueadas=set(),
        inertes=set(),
        margen_plataforma=None,
        snapshot_margen={},
        familias_ciclo=ciclo._FamiliasCiclo({}, {}, {}, {}, {}, None, DECIDED_AT.date(), None, {}),
        pause_economica=False,
        politica="niveles_v3",
        lecturas=lecturas,
    )
    return pendientes, contadores


def test_recorte_con_ventas_conservadas_y_economia_mejor_repite(monkeypatch):
    """El ACoS baja de 20 % a 15 % con la misma venta: el recorte se repite
    aunque el trafico haya caido por debajo de 0.70 (R16)."""
    caso = _caso(
        propia=_propia_sin_pedidos(),
        bid="0.85",
        cambios=(_cambio_pasado(),),
        efecto=_efecto_corte(),
        episodio=_episodio(pre_gasto="200", post_gasto="150"),
    )
    pendientes, _ = _corre_decisora(monkeypatch, caso)
    assert [(p.kind, p.new_value) for p in pendientes] == [("bid", Decimal("0.748"))]
    assert pendientes[0].inputs["motivo"] == "gasto_sin_venta"


def test_recorte_sin_ventas_conservadas_deja_decidir_a_r16(monkeypatch):
    """M6 no frena recortes: sin ventas conservadas devuelve sin evidencia y
    R16 conserva su freno de trafico."""
    caso = _caso(
        propia=_propia_sin_pedidos(),
        bid="0.85",
        cambios=(_cambio_pasado(),),
        efecto=_efecto_corte(),
        episodio=_episodio(pre_venta="1000", post_venta="700", post_gasto="150"),
    )
    pendientes, contadores = _corre_decisora(monkeypatch, caso)
    assert pendientes == []
    assert contadores.skips_entidad == {"recorte_costo_trafico": 1}


def test_recorte_sin_mejora_de_acos_deja_decidir_a_r16(monkeypatch):
    caso = _caso(
        propia=_propia_sin_pedidos(),
        bid="0.85",
        cambios=(_cambio_pasado(),),
        efecto=_efecto_corte(),
        episodio=_episodio(pre_gasto="200", post_gasto="250"),
    )
    pendientes, contadores = _corre_decisora(monkeypatch, caso)
    assert pendientes == []
    assert contadores.skips_entidad == {"recorte_costo_trafico": 1}


def test_vendedora_que_pierde_dinero_repite_el_recorte_que_la_base_hace(monkeypatch):
    """Trafico conservado (0.857) y una vendedora que pierde dinero: un
    episodio sin ventas conservadas no puede frenar el recorte. R16 deja
    pasar y R3 recorta -25 %, como en la base. La version con el freno
    devolvia recorte_sin_economia y dejaba la hoja perdiendo dinero."""
    propia = EvidenciaNivel(
        reciente=Tramo(
            clics=3200,
            pedidos=20,
            venta=Decimal("2000"),
            gasto=Decimal("1600"),
            impresiones=60000,
        ),
        antiguo=Tramo(clics=0, pedidos=0, venta=Decimal("0"), gasto=Decimal("0"), impresiones=0),
    )
    efecto = EfectoCambio(
        dias_post=20,
        impresiones_pre7=700,
        clics_pre7=70,
        impresiones_post=600,
        dias_post_trafico=7,
        clics_post=60,
        gasto_post=Decimal("30"),
        clics_pre=70,
        gasto_pre=Decimal("40"),
        vendia=True,
    )
    caso = _caso(
        propia=propia,
        bid="0.85",
        cambios=(_cambio_pasado(),),
        efecto=efecto,
        episodio=_episodio(
            pre_gasto="800",
            pre_venta="1000",
            post_gasto="600",
            post_venta="999.99",
        ),
        target="25",
        equilibrio="30",
    )
    pendientes, contadores = _corre_decisora(monkeypatch, caso)
    assert [(p.kind, p.new_value, p.inputs["motivo"]) for p in pendientes] == [
        ("bid", Decimal("0.6375"), "pierde_dinero_fuerte")
    ]
    assert contadores.skips_entidad == {}


@pytest.mark.parametrize(
    "episodio",
    [
        _episodio(estado="inmaduro", motivo="post_inmaduro"),
        _episodio(estado="incomparable", motivo="cambio_en_ventana"),
        _episodio(aplicable=False),
        None,
    ],
)
def test_sin_episodio_maduro_aplicable_la_trayectoria_vigente_frena(monkeypatch, episodio):
    """Sin evidencia madura y aplicable, R16 conserva su freno de trafico."""
    caso = _caso(
        propia=_propia_sin_pedidos(),
        bid="0.85",
        cambios=(_cambio_pasado(),),
        efecto=_efecto_corte(),
        episodio=episodio,
    )
    pendientes, contadores = _corre_decisora(monkeypatch, caso)
    assert pendientes == []
    assert contadores.skips_entidad == {"recorte_costo_trafico": 1}


def test_subida_cuyo_trafico_no_compensa_el_gasto_se_frena(monkeypatch):
    """Venta 200 -> 210 y gasto 40 -> 60: el ACoS empeora (28.6 %) sobre el
    target 25 %, asi que la subida no se repite."""
    caso = _caso(
        propia=_propia_vendedora(),
        bid="0.70",
        cambios=(_cambio_pasado(antes="0.50", despues="0.70"),),
        efecto=_efecto_subida(),
        episodio=_episodio(
            pre_gasto="40",
            pre_venta="200",
            post_gasto="60",
            post_venta="210",
            margen=None,
        ),
    )
    pendientes, contadores = _corre_decisora(monkeypatch, caso)
    assert pendientes == []
    assert contadores.skips_entidad == {MOTIVO_SUBIDA_SIN_ECONOMIA: 1}


def test_subida_con_economia_dentro_del_target_se_repite(monkeypatch):
    """Venta 200 -> 500 con gasto 40 -> 60: el ACoS baja a 12 %, dentro del
    target; sin margen compatible el ACoS decide."""
    caso = _caso(
        propia=_propia_vendedora(),
        bid="0.70",
        cambios=(_cambio_pasado(antes="0.50", despues="0.70"),),
        efecto=_efecto_subida(),
        episodio=_episodio(
            pre_gasto="40",
            pre_venta="200",
            post_gasto="60",
            post_venta="500",
            margen=None,
        ),
    )
    pendientes, _ = _corre_decisora(monkeypatch, caso)
    assert [(p.kind, p.new_value) for p in pendientes] == [("bid", Decimal("0.805"))]
    assert pendientes[0].inputs["motivo"] == "bajo_target"


def test_subida_con_contribucion_que_empeora_se_frena(monkeypatch):
    """Con margen 8 % la contribucion estimada cae: el gasto extra no se
    compensa aunque la venta suba."""
    caso = _caso(
        propia=_propia_vendedora(),
        bid="0.70",
        cambios=(_cambio_pasado(antes="0.50", despues="0.70"),),
        efecto=_efecto_subida(),
        episodio=_episodio(
            pre_gasto="40",
            pre_venta="200",
            post_gasto="60",
            post_venta="210",
            margen="8",
        ),
    )
    pendientes, contadores = _corre_decisora(monkeypatch, caso)
    assert pendientes == []
    assert contadores.skips_entidad == {MOTIVO_SUBIDA_SIN_ECONOMIA: 1}


# ---------------------------------------------------------------------------
# M5: lectura y freeze del episodio
# ---------------------------------------------------------------------------


def _filler_calendario(conn, run_id) -> int:
    relleno = _entidad(
        conn,
        "amazon_us",
        "keyword",
        "filler-m5",
        parent=_entidad(conn, "amazon_us", "ad_group", "filler-ag"),
        match_type="EXACT",
        keyword_text="filler m5",
    )
    _estado(conn, relleno, synced_at=_SYNCED, status="PAUSED")
    for dia in (DECIDED_AT.date() - dt.timedelta(days=n) for n in range(180, 0, -1)):
        _metrica(
            conn,
            run_id,
            relleno,
            dia,
            _obs(dia),
            cost="0",
            ad_revenue="0",
            clicks=0,
            orders=0,
            impressions=0,
        )
    return relleno


def _metrica_economica(conn, run_id, kw, fecha, *, gasto, venta, pedidos=5, clics=100) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, impressions, clicks, orders,"
        " ingest_run_id) VALUES (%s, %s, %s, 'USD', %s, %s, %s, 1000, %s, %s, %s)",
        (
            kw,
            fecha,
            _obs(fecha),
            Decimal(gasto),
            Decimal(venta),
            Decimal(venta),
            clics,
            pedidos,
            run_id,
        ),
    )


def _mundo_m5(conn, *, edad: int = 30) -> dict:
    run_id = _run(conn)
    _filler_calendario(conn, run_id)
    x = DECIDED_AT.date() - dt.timedelta(days=edad)
    camp = _entidad(conn, "amazon_us", "campaign", "9501")
    ag = _entidad(conn, "amazon_us", "ad_group", "9511", parent=camp)
    kw = _entidad(
        conn,
        "amazon_us",
        "keyword",
        "9521",
        parent=ag,
        match_type="EXACT",
        keyword_text="kw m5",
    )
    _estado(conn, kw, synced_at=_SYNCED, current_bid=Decimal("0.85"), bid_currency="USD")
    _estado(conn, ag, synced_at=_SYNCED)
    _estado(conn, camp, synced_at=_SYNCED)
    config_id = _config_version(
        conn,
        {
            "ads_optimizer_mode": "shadow",
            "ads_bid_politica_amazon_us": "niveles_v3",
            "ads_bid_economia_amazon_us": VERSION_ECONOMIA_CAMBIO,
        },
    )
    live = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live', 'amazon_us', 'done', %s, %s) RETURNING id",
        (
            DECIDED_AT - dt.timedelta(days=edad + 1),
            DECIDED_AT - dt.timedelta(days=edad),
        ),
    ).fetchone()[0]
    decision_id = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, 1.00, 0.85, 'USD', %s)"
        " RETURNING id",
        (
            live,
            kw,
            DECIDED_AT - dt.timedelta(days=edad, hours=2),
            config_id,
            DECIDED_AT - dt.timedelta(days=edad, hours=3),
            x - dt.timedelta(days=30),
            x - dt.timedelta(days=9),
            Json({"seed": "m5"}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
        " verify_ok, platform_ack, applied_cycle_id) VALUES (%s, %s, %s, TRUE, %s, %s)",
        (
            decision_id,
            DECIDED_AT - dt.timedelta(days=edad, hours=2),
            DECIDED_AT - dt.timedelta(days=edad),
            Json({"estado": "ok"}),
            live,
        ),
    )
    for n in range(7):
        fecha = x - dt.timedelta(days=7) + dt.timedelta(days=n)
        _metrica_economica(conn, run_id, kw, fecha, gasto="200", venta="1000")
    for n in range(7):
        fecha = x + dt.timedelta(days=1) + dt.timedelta(days=n)
        _metrica_economica(conn, run_id, kw, fecha, gasto="150", venta="1000")
    return {"kw": kw, "ag": ag, "camp": camp, "ciclo": live, "config": config_id, "x": x}


def _lecturas(conn, *, con_economia=True):
    from app.optimizer.caso import EconomiaPlataforma

    return lee_plataforma(
        conn,
        "amazon_us",
        DECIDED_AT,
        economia=EconomiaPlataforma(
            moneda="USD",
            equilibrio_acos_pct=Decimal("8"),
            gasto_para_concluir=Decimal("36"),
            confianza_recorte=Decimal("0.80"),
            confianza_subida=Decimal("0.70"),
        ),
        con_economia=con_economia,
    )


def _caso_desde_lecturas(conn, lect, ids):
    return lect.caso(
        conn,
        hoja_id=ids["kw"],
        ad_group_id=ids["ag"],
        bid=BidVigente(
            valor=Decimal("0.85"), moneda="USD", piso=Decimal("0.40"), techo=Decimal("2.50")
        ),
        target_acos_pct=Decimal("25"),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=Decimal("40"),
            expected_clicks=None,
            politica_economica=None,
        ),
        equilibrio_acos_pct=Decimal("8"),
    )


@_SIN_PG
def test_lectura_congela_el_episodio_maduro_y_su_contribucion():
    with _db_completa("orbit_m5_episodio") as (conn, _c):
        ids = _mundo_m5(conn)
        caso = _caso_desde_lecturas(conn, _lecturas(conn), ids)
        episodio = caso.trayectoria.episodio
        assert episodio is not None
        assert episodio.version == VERSION_ECONOMIA_CAMBIO
        assert episodio.estado == "maduro"
        assert episodio.motivo is None
        assert episodio.aplicable is True
        assert episodio.fecha_cambio == ids["x"]
        assert (episodio.pre.desde, episodio.pre.hasta) == (
            ids["x"] - dt.timedelta(days=7),
            ids["x"] - dt.timedelta(days=1),
        )
        assert (episodio.post.desde, episodio.post.hasta) == (
            ids["x"] + dt.timedelta(days=1),
            ids["x"] + dt.timedelta(days=7),
        )
        assert (episodio.pre.gasto, episodio.pre.venta, episodio.pre.pedidos) == (
            Decimal("1400"),
            Decimal("7000"),
            35,
        )
        assert (episodio.post.gasto, episodio.post.venta, episodio.post.pedidos) == (
            Decimal("1050"),
            Decimal("7000"),
            35,
        )
        assert episodio.margen_pct == Decimal("8")
        assert episodio.contribucion_pre == Decimal("7000") * 8 / 100 - Decimal("1400")
        assert episodio.contribucion_post == Decimal("7000") * 8 / 100 - Decimal("1050")


@_SIN_PG
def test_lectura_marca_incomparable_el_cambio_en_ventana_y_posterior():
    with _db_completa("orbit_m5_invalida") as (conn, _c):
        ids = _mundo_m5(conn)
        # Un cambio de configuracion DENTRO de [x-7, x+7] contamina la
        # comparacion y ademas es posterior al cambio.
        conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
            " ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon)"
            " VALUES (%s, %s, 100, 'USD', 'AUTO', 0, 0, 0, NULL)",
            (ids["camp"], ids["x"] + dt.timedelta(days=3)),
        )
        episodio = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert episodio is not None
        assert episodio.estado == "incomparable"
        assert episodio.motivo == "cambio_en_ventana"
        assert episodio.aplicable is False
        assert episodio.motivo_aplicabilidad == "cambio_posterior"


@_SIN_PG
def test_lectura_mantiene_comparable_un_cambio_posterior_fuera_de_ventana():
    with _db_completa("orbit_m5_posterior") as (conn, _c):
        ids = _mundo_m5(conn)
        conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
            " ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon)"
            " VALUES (%s, %s, 100, 'USD', 'AUTO', 0, 0, 0, NULL)",
            (ids["camp"], DECIDED_AT - dt.timedelta(days=10)),
        )
        episodio = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert episodio is not None
        assert episodio.estado == "maduro"
        assert episodio.motivo is None
        assert episodio.aplicable is False
        assert episodio.motivo_aplicabilidad == "cambio_posterior"


@_SIN_PG
def test_arreglos01_t_el_episodio_caduca_a_los_31_dias():
    with _db_completa("orbit_m5_vencido") as (conn, _c):
        ids = _mundo_m5(conn, edad=31)
        episodio = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert episodio is not None
        assert episodio.fecha_cambio == ids["x"]
        assert episodio.estado == "maduro"
        assert episodio.motivo is None
        assert episodio.aplicable is False
        assert episodio.motivo_aplicabilidad == "evidencia_vencida"
        assert (
            juicio_repeticion(
                episodio,
                target_acos_pct=Decimal("25"),
                factor=Decimal("0.15"),
            )
            == SIN_EVIDENCIA
        )


@_SIN_PG
@pytest.mark.parametrize("kind", ["negative", "harvest"])
@pytest.mark.parametrize("resultado", ["ok", "ok:reconciliado"])
def test_arreglos01_t_un_corte_en_el_mismo_ad_group_invalida_el_episodio(kind, resultado):
    with _db_completa(f"orbit_m5_corte_{kind}_{resultado.replace(':', '_')}") as (conn, _c):
        ids = _mundo_m5(conn)
        corte = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
            " config_version_id, data_observed_at, window_start, window_end, search_term,"
            " new_value, value_currency, inputs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
            " 'termino corte', 0.85, %s, %s) RETURNING id",
            (
                ids["ciclo"],
                ids["ag"],
                kind,
                ids["x"] + dt.timedelta(days=3),
                ids["config"],
                ids["x"] + dt.timedelta(days=3),
                ids["x"] - dt.timedelta(days=30),
                ids["x"] - dt.timedelta(days=9),
                "USD" if kind == "harvest" else None,
                Json({"seed": "corte"}),
            ),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, started_at, resultado, finished_at)"
            " VALUES (%s, 1, 'normal', %s, true, %s, %s, %s)",
            (
                corte,
                Json({"seed": "corte"}),
                ids["x"] + dt.timedelta(days=3),
                resultado,
                ids["x"] + dt.timedelta(days=3),
            ),
        )
        episodio = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert episodio is not None
        assert episodio.estado == "incomparable"
        assert episodio.motivo == "cambio_en_ventana"


@_SIN_PG
@pytest.mark.parametrize(
    ("intento", "sello", "invalida"),
    [
        (dt.timedelta(days=-3), dt.timedelta(days=10), True),
        (dt.timedelta(days=-20), dt.timedelta(days=3), False),
    ],
)
def test_arreglos01_t_el_corte_se_fecha_con_el_intento_real(intento, sello, invalida):
    with _db_completa(f"orbit_m5_corte_fecha_{'invalida' if invalida else 'no'}") as (conn, _c):
        ids = _mundo_m5(conn)
        ventana_fin = ids["x"] + intento - dt.timedelta(days=10)
        corte = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
            " config_version_id, data_observed_at, window_start, window_end, search_term,"
            " inputs) VALUES (%s, %s, 'negative', %s, %s, %s, %s, %s,"
            " 'termino corte', %s) RETURNING id",
            (
                ids["ciclo"],
                ids["ag"],
                ids["x"] + intento,
                ids["config"],
                ids["x"] + intento,
                ventana_fin - dt.timedelta(days=30),
                ventana_fin,
                Json({"seed": "corte"}),
            ),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, started_at, resultado, finished_at)"
            " VALUES (%s, 1, 'normal', %s, true, %s, 'ok:reconciliado', %s)",
            (
                corte,
                Json({"seed": "corte"}),
                ids["x"] + intento,
                ids["x"] + sello,
            ),
        )
        episodio = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert episodio is not None
        if invalida:
            assert episodio.estado == "incomparable"
            assert episodio.motivo == "cambio_en_ventana"
        else:
            assert episodio.estado == "maduro"
            assert episodio.motivo is None


@_SIN_PG
def test_interruptor_apagado_no_lee_ni_congela_episodio():
    with _db_completa("orbit_m5_apagado") as (conn, _c):
        ids = _mundo_m5(conn)
        caso = _caso_desde_lecturas(conn, _lecturas(conn, con_economia=False), ids)
        assert caso.trayectoria.episodio is None
        assert caso.trayectoria.efecto is not None


def test_freeze_acepta_la_forma_historica_sin_episodio():
    """Las decisiones anteriores a M5 no llevan la clave y rejuegan igual."""
    caso = _caso(
        propia=_propia_sin_pedidos(),
        bid="0.85",
        cambios=(_cambio_pasado(),),
        efecto=_efecto_corte(),
        episodio=None,
    )
    datos = caso.como_json()
    datos["trayectoria"].pop("episodio")
    from app.optimizer.caso import CasoHoja

    reconstruido = CasoHoja.desde_json(datos)
    assert reconstruido.trayectoria.episodio is None
    assert reconstruido == caso
