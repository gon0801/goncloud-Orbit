"""La PAUSE madura de una hoja no hereda el cooldown de un BID (camino niveles_v3)."""

import dataclasses
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import cycle
from app.optimizer import goals, windows
from app.optimizer.caso import (
    BidVigente,
    Economia,
    EconomiaPlataforma,
    InsumosPausa,
    PrecioVentana,
    Trayectoria,
)

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


def _caso_base():
    """Caso sin evidencia (propia None, inmaduros 0): si R0 no dispara, el
    juicio da sin_gasto. La pausa la inyecta el harness por llamada."""
    from app.optimizer.caso import CasoHoja

    return CasoHoja(
        plataforma="amazon_us",
        hoja_id=4925,
        ad_group_id=3927,
        bid=BidVigente(
            valor=Decimal("1"), moneda="USD", piso=Decimal("0.40"), techo=Decimal("2.50")
        ),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="USD",
                equilibrio_acos_pct=None,
                gasto_para_concluir=Decimal("36"),
                confianza_recorte=Decimal("0.80"),
                confianza_subida=Decimal("0.70"),
            ),
            target_acos_pct=Decimal("25"),
        ),
        propia=None,
        pedidos_inmaduros=0,
        grupo=None,
        cuenta=None,
        precio=PrecioVentana(gasto=None, clics=None),
        trayectoria=Trayectoria(cambios=(), efecto=None),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=157,
            gasto_minimo=Decimal("40"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=dt.date(2026, 6, 16),
        ventana_hasta=dt.date(2026, 9, 4),
        observado_al=AHORA - dt.timedelta(hours=1),
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
    econ=True,
):
    corte = _agregado() if corte is None else corte
    base = _caso_base()
    monkeypatch.setattr(cycle.windows, "ventana_cortes", lambda *_: corte)
    monkeypatch.setattr(
        cycle.cortes,
        "umbral_corte",
        lambda *_: SimpleNamespace(umbral=157, expected_clicks=None, elegible=False),
    )

    def _caso(_conn, **kwargs):
        return dataclasses.replace(base, pausa=kwargs["pausa"])

    lecturas = SimpleNamespace(caso=_caso)
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
        familias_ciclo=cycle._FamiliasCiclo({}, {}, {}, {}, {}, None, AHORA.date(), None, {}),
        pause_economica=econ,
        politica="niveles_v3",
        lecturas=lecturas,
    )
    return pendientes, contadores, consultas


def test_4925_pause_madura_14_sep_pasa_bid_cooldown_sin_lookahead(monkeypatch):
    pendientes, contadores, consultas = _corre_hoja(monkeypatch)
    assert [p.kind for p in pendientes] == ["pause"]
    assert pendientes[0].window_end == dt.date(2026, 9, 4)
    assert pendientes[0].inputs["politica"] == "niveles_v3"
    assert pendientes[0].inputs["target_procedencia"] == "goal_plataforma"
    assert pendientes[0].inputs["motivo"] == "pause_umbral"
    assert pendientes[0].inputs["caso"]["pausa"]["umbral_clics"] == 157
    assert contadores.decisiones == {"pause": 1}
    assert consultas == ["pause"]


@pytest.mark.parametrize(
    "corte",
    [
        _agregado(clicks=156, cost="79"),
        # Obs4r2: costo SOBRE el limite (127.94): solo la abstencion por
        # orders desconocidos impide la PAUSE (con cost 79 el caso no
        # discriminaba: ni la economica cruzaba).
        _agregado(orders=None),
        _agregado(fechas=6),
    ],
)
def test_sin_pause_y_sin_evidencia_el_juicio_decide_sin_cooldown(monkeypatch, corte):
    """M.3: si la PAUSE no califica, el juicio decide (sin_gasto, sin
    evidencia) y no hay consulta de cooldown: el gate generico ya no
    existe y un bid previo no enfria nada."""
    pendientes, contadores, consultas = _corre_hoja(monkeypatch, corte=corte)
    assert pendientes == []
    assert contadores.skips_entidad == {"sin_gasto": 1}
    assert consultas == []


def test_pause_aplicada_hace_3_dias_no_recibe_otra_pause(monkeypatch):
    """M.3: una hoja con una PAUSE aplicada hace 3 dias no recibe otra
    PAUSE y se cuenta con cooldown_7d."""
    pendientes, contadores, consultas = _corre_hoja(
        monkeypatch,
        bid_confirmado=None,
        pause_confirmada=AHORA - dt.timedelta(days=3),
    )
    assert pendientes == []
    assert contadores.skips_entidad == {"cooldown_7d": 1}
    assert consultas == ["pause"]


def test_bid_aplicado_hace_3_dias_con_datos_maduros_si_recibe_pause(monkeypatch):
    """M.3: una hoja con un bid aplicado hace 3 dias y datos maduros
    para pausar SI recibe la PAUSE (un bid previo no enfria una PAUSE)."""
    pendientes, contadores, consultas = _corre_hoja(
        monkeypatch, bid_confirmado=AHORA - dt.timedelta(days=3)
    )
    assert [p.kind for p in pendientes] == ["pause"]
    assert consultas == ["pause"]


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


def test_2423_venta_cara_emite_pause_y_replayea_con_caso(monkeypatch):
    corte = _agregado(orders=1, clicks=231, cost="105")
    corte = windows.AgregadoMetricas(**{**corte.__dict__, "ad_revenue": Decimal("100")})
    pendientes, contadores, _ = _corre_hoja(monkeypatch, corte=corte)
    assert contadores.decisiones == {"pause": 1}
    assert pendientes[0].inputs["motivo"] == "pause_economica"
    assert pendientes[0].inputs["caso"]["pausa"]["politica_economica"] == "economic_pause_v1"
    assert cycle.reproduce(pendientes[0].inputs) == ("pause", None, None)


def test_flag_economico_apagado_decide_regla_vieja(monkeypatch):
    """C.3 B1: con el flag apagado no hay PAUSE economica (misma hoja que
    con flag emitiria pause_economica); la umbral sigue pausando."""
    corte = _agregado(orders=1, clicks=231, cost="105")
    corte = windows.AgregadoMetricas(**{**corte.__dict__, "ad_revenue": Decimal("100")})
    pendientes, contadores, _ = _corre_hoja(monkeypatch, corte=corte, econ=False)
    assert pendientes == []
    assert contadores.decisiones == {}
    assert contadores.skips_entidad == {"sin_gasto": 1}
    umbral, _, _ = _corre_hoja(monkeypatch, econ=False)
    assert umbral[0].inputs["motivo"] == "pause_umbral"
    assert umbral[0].inputs["caso"]["pausa"]["politica_economica"] is None
