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


def _corre_hoja(monkeypatch, *, bids, historia, cooldown=False, cortes=None, motor=False, cpc=None):
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
    monkeypatch.setattr(cycle.windows, "cpc_vigente", lambda *_a, **_k: cpc)
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
        familias_ciclo=cycle._FamiliasCiclo({}, {}, {}, {}, None, AHORA.date(), None, {}),
        pause_sin_cooldown_bid=True,
        pause_economica=False,
        conv_jerarquica=_CONV_VACIA,
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
        motor_evidencia=motor,
    )
    return pendientes, contadores, consultas


def test_repro_lead_d2_v2_20_clics_post_cambio_permite_reversa(monkeypatch):
    """Plan A6 (Build + lane 4): hoja recortada hace 5 dias con 25 clics
    post-cambio. bandas_v1: D.2 (10 dias) bloquea. evidencia: el check es
    CPC post-cambio >= 20 clics, la subida se emite."""
    historia = goals.UltimoBidAplicado(direccion=-1, fecha_cambio=D)
    cpc = ev.CostoPorClic(
        cost=Decimal("12.5"),
        clicks=25,
        desde=D + dt.timedelta(days=1),
        hasta=D + dt.timedelta(days=5),
        post_cambio=True,
    )
    fin5 = D + dt.timedelta(days=5)
    _v1, c1, _ = _corre_hoja(monkeypatch, bids=_subida(fin=fin5), historia=historia)
    assert c1.skips_entidad == {"inversion_sin_evidencia": 1}
    v2, c2, _ = _corre_hoja(
        monkeypatch, bids=_subida(fin=fin5), historia=historia, motor=True, cpc=cpc
    )
    assert [p.kind for p in v2] == ["bid"], c2.skips_entidad
