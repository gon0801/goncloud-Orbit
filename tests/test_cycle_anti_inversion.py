"""D.2 (ads-proteccion-01): la reversa de un BID aplicado exige N=10 dias de
evidencia posterior al cambio (decision del dueno, D.2/decision.md).

El gate vive en _procesa_decisora DESPUES del cooldown B.2 y del no-op, y
solo para kind 'bid': PAUSE y no-op jamas consultan la historia. Aqui
ultimo_bid_aplicado se parchea con espia (el conn del harness es object(),
mismo trato que en_cooldown en test_cycle_pause_cooldown.py); la decision
permite_reversa_bid corre PURA. La fecha del caso 3835 queda lejos de hoy
a proposito: un mutante que cuente dias de reloj en vez de la ventana de
bids emite la decision y el test se pone rojo.
"""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from app import cycle
from app.optimizer import evidencia as ev
from app.optimizer import goals, windows

AHORA = dt.datetime(2026, 9, 14, 8, 40, tzinfo=dt.UTC)
D = dt.date(2026, 8, 1)  # fecha_cambio del caso 3835 (subida aplicada ese dia)
ENTIDAD = 3835

# A4: conv vacia (el hook abstiene) + cpc_vigente parcheado (el conn del
# harness es object(); los veredictos live de aqui no cambian).
_CONV_VACIA = ev.enrolla_granos(
    [], moneda="USD", ventana_desde=dt.date(2026, 6, 16), ventana_hasta=dt.date(2026, 9, 4)
)


def _agregado(
    *,
    orders=3,
    clicks=30,
    cost="100",
    revenue="1000",
    fechas=22,
    fin=dt.date(2026, 9, 4),
):
    return windows.AgregadoMetricas(
        window_start=fin - dt.timedelta(days=29),
        window_end=fin,
        fechas=tuple(fin - dt.timedelta(days=n) for n in range(fechas)),
        metric_currency="USD",
        cost=Decimal(cost),
        ad_revenue=Decimal(revenue),
        revenue_same_sku=Decimal("0"),
        impressions=200,
        clicks=clicks,
        orders=orders,
        observed_at_max=AHORA - dt.timedelta(hours=1),
    )


def _subida(fin=dt.date(2026, 9, 4)):
    # ACoS 10% < 0.85x target 25: banda +15% sobre bid 1.00 -> 1.15.
    return _agregado(orders=3, cost="100", revenue="1000", fin=fin)


def _bajada(fin=dt.date(2026, 9, 4)):
    # ACoS 50% > 1.35x target 25: banda -25% sobre bid 1.00 -> 0.75.
    return _agregado(orders=1, cost="50", revenue="100", fin=fin)


def _corre_hoja(monkeypatch, *, bids, historia, cooldown=False, cortes=None):
    ventanas = SimpleNamespace(
        bids=bids,
        cortes=cortes if cortes is not None else _agregado(orders=1, cost="50", revenue="100"),
    )
    monkeypatch.setattr(cycle.windows, "ventanas_entidad", lambda *_: ventanas)
    monkeypatch.setattr(
        cycle.cortes,
        "umbral_corte",
        lambda *_: SimpleNamespace(umbral=157, expected_clicks=None, elegible=False),
    )
    monkeypatch.setattr(cycle.g, "en_cooldown", lambda *_a, **_k: cooldown)
    consultas = []

    def ultimo_bid(_conn, entidad):
        consultas.append(entidad)
        return historia

    monkeypatch.setattr(cycle.g, "ultimo_bid_aplicado", ultimo_bid)
    monkeypatch.setattr(cycle.windows, "cpc_vigente", lambda *_a, **_k: None)
    goal = goals.Goal(
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
    contadores = cycle._Contadores()
    pendientes = []
    cycle._procesa_decisora(
        object(),
        fila=(ENTIDAD, 3927, 3926, Decimal("1"), "USD", "ENABLED", None, "ENABLED", "ENABLED"),
        platform="amazon_us",
        setting_target=None,
        goals=(goal, {}),
        modo="shadow",
        decided_at=AHORA,
        contadores=contadores,
        pendientes=pendientes,
        tick=lambda: None,
        evidencia_ad_groups={},
        corte_pause_por_grupo={},
        bloqueadas=set(),
        inertes=set(),
        margen_plataforma=None,
        snapshot_margen={},
        familias_ciclo=cycle._FamiliasCiclo({}, {}, {}, {}, {}, None, AHORA.date(), None, {}),
        pause_sin_cooldown_bid=True,
        pause_economica=False,
        conv_jerarquica=_CONV_VACIA,
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
        motor_evidencia=False,
    )
    return pendientes, contadores, consultas


def test_caso_3835_reversa_bloqueada_con_ventana_d9_y_emitida_con_d10(monkeypatch):
    bloqueada, contadores, consultas = _corre_hoja(
        monkeypatch,
        bids=_bajada(fin=D + dt.timedelta(days=9)),
        historia=goals.UltimoBidAplicado(direccion=1, fecha_cambio=D),
    )
    assert bloqueada == []
    assert contadores.skips_entidad == {"inversion_sin_evidencia": 1}
    assert contadores.decisiones == {}
    assert consultas == [ENTIDAD]

    emitida, contadores, consultas = _corre_hoja(
        monkeypatch,
        bids=_bajada(fin=D + dt.timedelta(days=10)),
        historia=goals.UltimoBidAplicado(direccion=1, fecha_cambio=D),
    )
    assert [p.kind for p in emitida] == ["bid"]
    assert contadores.skips_entidad == {}
    assert contadores.decisiones == {"bid": 1}
    assert emitida[0].inputs["inversion_policy_version"] == "inversion_n10_v1"
    assert consultas == [ENTIDAD]


def test_misma_direccion_con_ventana_corta_se_emite(monkeypatch):
    pendientes, contadores, _ = _corre_hoja(
        monkeypatch,
        bids=_subida(fin=D + dt.timedelta(days=2)),
        historia=goals.UltimoBidAplicado(direccion=1, fecha_cambio=D),
    )
    assert [p.kind for p in pendientes] == ["bid"]
    assert contadores.skips_entidad == {}


def test_pause_y_no_op_no_consultan_el_ultimo_bid(monkeypatch):
    # La PAUSE seria reversa si el gate la tocara (bajada tras subida): si el
    # gate corre sobre kind 'pause', el espia se llena y/o la pause se frena.
    pause, _, consultas = _corre_hoja(
        monkeypatch,
        bids=_bajada(),
        historia=goals.UltimoBidAplicado(direccion=1, fecha_cambio=D),
        cortes=_agregado(orders=0, clicks=164, cost="127.94", revenue="0"),
    )
    assert [p.kind for p in pause] == ["pause"]
    assert consultas == []

    no_op, contadores, consultas = _corre_hoja(
        monkeypatch,
        bids=_agregado(orders=1, cost="280", revenue="1000"),
        historia=goals.UltimoBidAplicado(direccion=1, fecha_cambio=D),
    )
    assert no_op == []
    assert contadores.skips_entidad == {"sin_banda": 1}
    assert consultas == []


def test_sin_historia_previa_se_emite(monkeypatch):
    pendientes, contadores, _ = _corre_hoja(
        monkeypatch,
        bids=_bajada(fin=D + dt.timedelta(days=2)),
        historia=goals.SinHistoriaBid(),
    )
    assert [p.kind for p in pendientes] == ["bid"]
    assert contadores.skips_entidad == {}


def test_historia_rota_bloquea_la_reversa(monkeypatch):
    pendientes, contadores, consultas = _corre_hoja(
        monkeypatch,
        bids=_bajada(fin=D + dt.timedelta(days=2)),
        historia=goals.HistoriaBidRota(),
    )
    assert pendientes == []
    assert contadores.skips_entidad == {"inversion_sin_evidencia": 1}
    assert consultas == [ENTIDAD]


def test_cooldown_b2_gana_antes_de_consultar_la_inversion(monkeypatch):
    pendientes, contadores, consultas = _corre_hoja(
        monkeypatch,
        bids=_bajada(fin=D + dt.timedelta(days=2)),
        historia=goals.UltimoBidAplicado(direccion=1, fecha_cambio=D),
        cooldown=True,
    )
    assert pendientes == []
    assert contadores.skips_entidad == {"cooldown_7d": 1}
    assert consultas == []
