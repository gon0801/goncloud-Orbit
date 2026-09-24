"""La PAUSE madura de una hoja no hereda el cooldown de un BID."""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import cycle
from app.optimizer import goals, windows

AHORA = dt.datetime(2026, 9, 14, 8, 40, tzinfo=dt.UTC)
BID_CONFIRMADO = dt.datetime(2026, 9, 11, 8, 40, 3, tzinfo=dt.UTC)


def _agregado(*, orders=0, clicks=164, cost="127.94", fechas=22, fin=dt.date(2026, 9, 4)):
    return windows.AgregadoMetricas(
        window_start=fin - dt.timedelta(days=29),
        window_end=fin,
        fechas=tuple(fin - dt.timedelta(days=n) for n in range(fechas)),
        metric_currency="USD",
        cost=Decimal(cost),
        ad_revenue=Decimal("0"),
        revenue_same_sku=Decimal("0"),
        impressions=200,
        clicks=clicks,
        orders=orders,
        observed_at_max=AHORA - dt.timedelta(hours=1),
    )


def _corre_hoja(
    monkeypatch,
    *,
    corte=None,
    bid_confirmado=BID_CONFIRMADO,
    pause_confirmada=None,
    estado="ENABLED",
    bloqueada=False,
    inerte=False,
):
    corte = _agregado() if corte is None else corte
    ventanas = SimpleNamespace(bids=_agregado(orders=1, clicks=30, cost="50"), cortes=corte)
    monkeypatch.setattr(cycle.windows, "ventanas_entidad", lambda *_: ventanas)
    monkeypatch.setattr(
        cycle.cortes,
        "umbral_corte",
        lambda *_: SimpleNamespace(umbral=157, expected_clicks=None),
    )
    monkeypatch.setattr(cycle, "_pendiente_bid", lambda _id, resultado, **_kw: resultado)
    consultas = []

    def cooldown(_conn, _id, *, ahora, kind=None):
        consultas.append(kind)
        eventos = (("bid", bid_confirmado), ("pause", pause_confirmada))
        return any(
            fecha is not None
            and (kind is None or kind == clase)
            and ahora - dt.timedelta(days=7) < fecha <= ahora
            for clase, fecha in eventos
        )

    monkeypatch.setattr(cycle.g, "en_cooldown", cooldown)
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
        fila=(4925, 3927, 3926, Decimal("1"), "USD", estado, None, "ENABLED", "ENABLED"),
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
        bloqueadas={(4925, "entity_cut", None)} if bloqueada else set(),
        inertes={4925} if inerte else set(),
        margen_plataforma=None,
        snapshot_margen={},
    )
    return pendientes, contadores, consultas


def test_4925_pause_madura_14_sep_pasa_bid_cooldown_sin_lookahead(monkeypatch):
    pendientes, contadores, consultas = _corre_hoja(monkeypatch)
    assert [p.kind for p in pendientes] == ["pause"]
    assert pendientes[0].window_end == dt.date(2026, 9, 4)
    assert contadores.decisiones == {"pause": 1}
    assert consultas == ["pause"]


@pytest.mark.parametrize(
    ("corte", "esperado"),
    [
        (_agregado(clicks=156), "cooldown_7d"),
        (_agregado(orders=None), "cooldown_7d"),
        (_agregado(fechas=6), "cooldown_7d"),
    ],
)
def test_bid_sigue_en_cooldown_si_pause_no_califica(monkeypatch, corte, esperado):
    pendientes, contadores, consultas = _corre_hoja(monkeypatch, corte=corte)
    assert pendientes == []
    assert contadores.skips_entidad == {esperado: 1}
    assert consultas == [None]


def test_pause_aplicada_y_revertida_conserva_cooldown_7d(monkeypatch):
    pendientes, contadores, consultas = _corre_hoja(
        monkeypatch,
        bid_confirmado=None,
        pause_confirmada=AHORA - dt.timedelta(days=6),
    )
    assert pendientes == []
    assert contadores.skips_entidad == {"cooldown_7d": 1}
    assert consultas == ["pause"]


def test_borde_exacto_7d_no_enfria(monkeypatch):
    pendientes, _, consultas = _corre_hoja(
        monkeypatch,
        bid_confirmado=None,
        pause_confirmada=AHORA - dt.timedelta(days=7),
    )
    assert [p.kind for p in pendientes] == ["pause"]
    assert consultas == ["pause"]


@pytest.mark.parametrize(
    ("opciones", "motivo"),
    [
        ({"estado": "PAUSED"}, "estado_no_enabled"),
        ({"bloqueada": True}, "veto_pendiente"),
        ({"inerte": True}, "entidad_inerte"),
    ],
)
def test_vetos_previos_siguen_impidiendo_pause(monkeypatch, opciones, motivo):
    pendientes, contadores, _ = _corre_hoja(monkeypatch, **opciones)
    assert pendientes == []
    assert contadores.skips_entidad == {motivo: 1}
