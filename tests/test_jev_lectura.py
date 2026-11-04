"""Tests de `app/jev_lectura.py`: nucleo puro de JEV ADS 02 (S.2).

Puro y sin base: tipos, `leer`, `probar_roster`, `anunciados_hoy`,
`ajustes_desde_settings`, `leer_destino` y `planear`. Tablas con una fila
por regla del diseno y del bosquejo (seccion A).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.jev_ads import (
    CensoCongelado,
    EstadoAnuncio,
    HayCompatible,
    Indeterminado,
    MiembroCenso,
    NingunoCompatible,
)
from app.jev_lectura import (
    REGLA_VERSION,
    ActaDeGrupo,
    ActaDeListado,
    Ajustes,
    Apagado,
    ClaveBusqueda,
    Economia,
    Gasto,
    Historial,
    NoEvaluada,
    PropuestaEnVeto,
    Relevancia,
    Roster,
    RosterProbado,
    RosterSinProbar,
    VentaEnOtroGrupo,
    ajustes_desde_settings,
    anunciados_hoy,
    leer,
    leer_destino,
    planear,
    probar_roster,
)

AHORA = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
MAX48 = timedelta(hours=48)
DESDE = date(2026, 8, 25)
HASTA = date(2026, 9, 23)


def _gasto(ordenes=0, clics=10, gasto="5.00"):
    return Gasto(desde=DESDE, hasta=HASTA, clics=clics, gasto=Decimal(gasto), ordenes=ordenes)


def _eco(aqui=None, otros=(), sin_dato=0, historial=None):
    if aqui is False:
        aqui = None
    elif aqui is None:
        aqui = _gasto()
    if historial is None:
        historial = Historial(
            desde=DESDE, hasta=HASTA, clics=50, ordenes_conocidas=0, dias_sin_dato=0
        )
    return Economia(
        moneda="MXN",
        aqui=aqui,
        otros_que_venden=tuple(otros),
        otros_sin_venta=1,
        otros_sin_dato=sin_dato,
        historial=historial,
        datos_hasta=AHORA,
    )


def _rel(conjunto):
    return Relevancia(
        conjunto=conjunto,
        satisfacen=0,
        evaluados=2,
        miembros=2,
        productos_ok=(),
        juicio_ids=(),
    )


def _venta_otro(grupo=8, ordenes=2):
    return VentaEnOtroGrupo(
        ad_group_id=grupo, en_ventana=_gasto(ordenes=ordenes, clics=6, gasto="9.00")
    )


def _hist(ordenes, sin_dato=0):
    return Historial(
        desde=DESDE,
        hasta=HASTA,
        clics=50,
        ordenes_conocidas=ordenes,
        dias_sin_dato=sin_dato,
    )


@pytest.mark.parametrize(
    ("economia", "conjunto", "lectura", "motivos"),
    [
        pytest.param(
            _eco(historial=_hist(1)),
            NingunoCompatible(miembros_totales=2),
            "vendio_aqui",
            frozenset(),
            id="historial-con-ordenes-gana-al-ninguno",
        ),
        pytest.param(
            _eco(otros=(_venta_otro(),)),
            NingunoCompatible(miembros_totales=2),
            "vende_en_otro",
            frozenset(),
            id="venta-en-otro-grupo",
        ),
        pytest.param(
            _eco(otros=(_venta_otro(),), sin_dato=2),
            NingunoCompatible(miembros_totales=2),
            "vende_en_otro",
            frozenset(),
            id="venta-en-otro-gana-al-dato-faltante",
        ),
        pytest.param(
            _eco(historial=_hist(1, sin_dato=1)),
            NingunoCompatible(miembros_totales=2),
            "vendio_aqui",
            frozenset(),
            id="dia-sin-dato-no-borra-la-venta",
        ),
        pytest.param(
            _eco(aqui=False),
            NingunoCompatible(miembros_totales=2),
            "sin_lectura",
            frozenset({"sin_observaciones"}),
            id="sin-observaciones",
        ),
        pytest.param(
            _eco(aqui=_gasto(ordenes=None)),
            NingunoCompatible(miembros_totales=2),
            "sin_lectura",
            frozenset({"dato_de_venta_faltante"}),
            id="orders-null-aqui",
        ),
        pytest.param(
            _eco(sin_dato=1),
            NingunoCompatible(miembros_totales=2),
            "sin_lectura",
            frozenset({"dato_de_venta_faltante"}),
            id="orders-null-en-otro-grupo",
        ),
        pytest.param(
            _eco(),
            HayCompatible(producto_ids=(7,), miembros_con_juicio=1, miembros_totales=2),
            "relevante_sin_venta",
            frozenset(),
            id="hay-compatible-sin-venta",
        ),
        pytest.param(
            _eco(),
            NingunoCompatible(miembros_totales=2),
            "ajena",
            frozenset(),
            id="ninguno-compatible-sin-venta",
        ),
        pytest.param(
            _eco(),
            Indeterminado(motivos=frozenset({"fallo_proveedor"})),
            "sin_lectura",
            frozenset({"jev_sin_veredicto"}),
            id="indeterminado",
        ),
        pytest.param(
            _eco(),
            NoEvaluada(motivo="tope_cero"),
            "sin_lectura",
            frozenset({"jev_no_evaluada"}),
            id="no-evaluada-sin-ventas",
        ),
        pytest.param(
            _eco(historial=_hist(3)),
            NoEvaluada(motivo="sin_cupo"),
            "vendio_aqui",
            frozenset(),
            id="sin-cupo-no-impide-vendio-aqui",
        ),
    ],
)
def test_leer_tabla(economia, conjunto, lectura, motivos):
    assert leer(economia, _rel(conjunto)) == (lectura, motivos)


def test_regla_version_congelada():
    assert REGLA_VERSION == 1


def _miembro(anuncios=(11,), producto=7, listings=(5,), estados=("ENABLED",)):
    return MiembroCenso(
        anuncio_ids=tuple(anuncios),
        producto_id=producto,
        listing_ids=frozenset(listings),
        estados=tuple(EstadoAnuncio(status=e, synced_at=AHORA) for e in estados),
    )


def _acta(finished=None, **cambios):
    base = {
        "ingest_run_id": 9,
        "finished_at": AHORA - timedelta(hours=1) if finished is None else finished,
        "ad_groups_declarados": 3,
        "ad_groups_recibidos": 3,
        "declarados": 10,
        "recibidos": 10,
        "sin_grupo": 0,
        "del_grupo": ActaDeGrupo(vivos=2, huella="h", descartados=0),
    }
    base.update(cambios)
    return ActaDeListado(**base)


def _censo(*miembros):
    return CensoCongelado(
        miembros=miembros or (_miembro(), _miembro(anuncios=(12,), producto=8, listings=(6,))),
        exhaustivo=False,
    )


def test_probar_roster_probado():
    prueba = probar_roster(_censo(), _acta(), huella_en_base="h", ahora=AHORA, max_edad=MAX48)
    assert prueba == RosterProbado(
        ingest_run_id=9, listado_de=AHORA - timedelta(hours=1), anuncios_vivos=2
    )


def test_probar_roster_48h_exactas_valen():
    acta = _acta(finished=AHORA - timedelta(hours=48))
    prueba = probar_roster(_censo(), acta, huella_en_base="h", ahora=AHORA, max_edad=MAX48)
    assert isinstance(prueba, RosterProbado)


def test_probar_roster_48h_mas_un_segundo_ya_no():
    acta = _acta(finished=AHORA - timedelta(hours=48, seconds=1))
    prueba = probar_roster(_censo(), acta, huella_en_base="h", ahora=AHORA, max_edad=MAX48)
    assert prueba == RosterSinProbar(motivos=frozenset({"sin_corrida_reciente"}))


@pytest.mark.parametrize(
    ("acta", "censo", "huella", "motivo"),
    [
        pytest.param(
            _acta(finished=AHORA - timedelta(hours=49)),
            _censo(),
            "h",
            "sin_corrida_reciente",
            id="acta-vieja",
        ),
        pytest.param(None, _censo(), "h", "plataforma_no_listada", id="sin-acta"),
        pytest.param(
            _acta(declarados=None),
            _censo(),
            "h",
            "listado_sin_total",
            id="sin-total-product-ads",
        ),
        pytest.param(
            _acta(ad_groups_declarados=None),
            _censo(),
            "h",
            "listado_sin_total",
            id="sin-total-ad-groups",
        ),
        pytest.param(
            _acta(declarados=9),
            _censo(),
            "h",
            "listado_no_cuadra",
            id="no-cuadra-product-ads",
        ),
        pytest.param(
            _acta(ad_groups_declarados=2),
            _censo(),
            "h",
            "listado_no_cuadra",
            id="no-cuadra-ad-groups",
        ),
        pytest.param(
            _acta(del_grupo=None),
            _censo(),
            "h",
            "grupo_no_listado",
            id="grupo-sin-acta",
        ),
        pytest.param(
            _acta(del_grupo=ActaDeGrupo(vivos=2, huella="h", descartados=1)),
            _censo(),
            "h",
            "anuncios_descartados",
            id="descartados",
        ),
        pytest.param(
            _acta(sin_grupo=2),
            _censo(),
            "h",
            "anuncios_sin_grupo",
            id="sin-grupo",
        ),
        pytest.param(_acta(), _censo(), "otra", "huella_distinta", id="huella"),
        pytest.param(
            _acta(),
            _censo(_miembro(estados=())),
            "h",
            "estado_ausente",
            id="estado-ausente",
        ),
        pytest.param(
            _acta(),
            _censo(
                MiembroCenso(
                    anuncio_ids=(11,),
                    producto_id=7,
                    listing_ids=frozenset({5}),
                    estados=(EstadoAnuncio(status=None, synced_at=None),),
                )
            ),
            "h",
            "estado_ausente",
            id="estado-ausente-sin-fila",
        ),
        pytest.param(
            _acta(),
            _censo(_miembro(producto=None, listings=())),
            "h",
            "anuncio_sin_producto",
            id="vivo-sin-producto",
        ),
    ],
)
def test_probar_roster_cada_motivo_por_separado(acta, censo, huella, motivo):
    prueba = probar_roster(censo, acta, huella_en_base=huella, ahora=AHORA, max_edad=MAX48)
    assert prueba == RosterSinProbar(motivos=frozenset({motivo}))


def test_probar_roster_reporta_todos_los_motivos_no_el_primero():
    acta = _acta(finished=AHORA - timedelta(hours=49), sin_grupo=1)
    prueba = probar_roster(_censo(), acta, huella_en_base="otra", ahora=AHORA, max_edad=MAX48)
    assert prueba == RosterSinProbar(
        motivos=frozenset({"sin_corrida_reciente", "anuncios_sin_grupo", "huella_distinta"})
    )


def test_roster_exige_prueba_acorde_al_exhaustivo():
    from dataclasses import replace

    censo = replace(_censo(), exhaustivo=True)
    with pytest.raises(ValueError, match="exhaustivo"):
        Roster(
            plataforma="amazon_mx",
            ad_group_id=1,
            censo=censo,
            fichas={},
            prueba=RosterSinProbar(motivos=frozenset({"huella_distinta"})),
            sha256="x",
        )


@pytest.mark.parametrize("exhaustivo", [False, True])
def test_anunciados_hoy_quita_solo_archivados_y_conserva_exhaustivo(exhaustivo):
    archivado = _miembro(
        anuncios=(21,), producto=1, listings=(31,), estados=("ARCHIVED", "ARCHIVED")
    )
    vivo = _miembro(anuncios=(22,), producto=2, listings=(32,), estados=("ENABLED",))
    sin_estado = _miembro(anuncios=(23,), producto=3, listings=(33,), estados=())
    mixto = MiembroCenso(
        anuncio_ids=(24, 25),
        producto_id=4,
        listing_ids=frozenset({34}),
        estados=(
            EstadoAnuncio(status="ARCHIVED", synced_at=AHORA),
            EstadoAnuncio(status=None, synced_at=None),
        ),
    )
    censo = CensoCongelado(miembros=(archivado, vivo, sin_estado, mixto), exhaustivo=exhaustivo)
    assert anunciados_hoy(censo) == CensoCongelado(
        miembros=(vivo, sin_estado, mixto), exhaustivo=exhaustivo
    )


def _settings(**cambios):
    base = {"jev.senales": True, "jev.tope_diario": 0, "jev.min_clics": 3}
    base.update(cambios)
    return base


@pytest.mark.parametrize(
    ("settings", "esperado"),
    [
        pytest.param(_settings(), Ajustes(99, 0, 3, False), id="minimo-enciende-sin-avisos"),
        pytest.param(_settings(**{"jev.avisos": True}), Ajustes(99, 0, 3, True), id="avisos-true"),
        pytest.param(
            _settings(**{"jev.avisos": "si"}),
            Ajustes(99, 0, 3, False),
            id="avisos-distinto-no-apaga",
        ),
        pytest.param(
            {"jev.tope_diario": 5, "jev.min_clics": 3},
            Apagado("jev.senales ausente"),
            id="senales-ausente",
        ),
        pytest.param(
            _settings(**{"jev.senales": "true"}),
            Apagado("jev.senales corrupto: 'true'"),
            id="senales-texto",
        ),
        pytest.param(
            _settings(**{"jev.senales": 1}),
            Apagado("jev.senales corrupto: 1"),
            id="senales-uno",
        ),
        pytest.param(
            {"jev.senales": True, "jev.min_clics": 3},
            Apagado("jev.tope_diario ausente"),
            id="tope-ausente",
        ),
        pytest.param(
            _settings(**{"jev.tope_diario": -1}),
            Apagado("jev.tope_diario corrupto: -1"),
            id="tope-negativo",
        ),
        pytest.param(
            _settings(**{"jev.tope_diario": True}),
            Apagado("jev.tope_diario corrupto: True"),
            id="tope-bool-no-es-entero",
        ),
        pytest.param(
            _settings(**{"jev.tope_diario": "mil"}),
            Apagado("jev.tope_diario corrupto: 'mil'"),
            id="tope-texto",
        ),
        pytest.param(
            {"jev.senales": True, "jev.tope_diario": 5},
            Apagado("jev.min_clics ausente"),
            id="min-clics-ausente",
        ),
        pytest.param(
            _settings(**{"jev.min_clics": 0}),
            Apagado("jev.min_clics corrupto: 0"),
            id="min-clics-cero",
        ),
        pytest.param(
            _settings(**{"jev.min_clics": False}),
            Apagado("jev.min_clics corrupto: False"),
            id="min-clics-bool-no-es-entero",
        ),
    ],
)
def test_ajustes_desde_settings_tabla(settings, esperado):
    assert ajustes_desde_settings(99, settings) == esperado


@pytest.mark.parametrize(
    ("relevancia", "destino"),
    [
        ("corresponde", "destino_corresponde"),
        ("ajena", "destino_ajeno"),
        ("sin_veredicto", "sin_lectura"),
        ("no_evaluada", "sin_lectura"),
    ],
)
def test_leer_destino_tabla(relevancia, destino):
    assert leer_destino(relevancia) == destino


def _clave(plataforma="amazon_mx", grupo=1, termino="soporte mesa"):
    return ClaveBusqueda(plataforma=plataforma, ad_group_id=grupo, termino=termino)


def _eco_spend(ordenes=0, clics=10, gasto="5.00", historial=None, sin_dato=0):
    return _eco(
        aqui=_gasto(ordenes=ordenes, clics=clics, gasto=gasto),
        historial=_hist(0) if historial is None else historial,
        sin_dato=sin_dato,
    )


def _propuesta(cola, origen, destino=None, vence=None, kind="negative"):
    return PropuestaEnVeto(
        cola_id=cola,
        decision_id=cola,
        kind=kind,
        modo="live",
        origen=origen,
        destino=destino,
        destino_ilegible=False,
        vence_el=AHORA + timedelta(hours=6) if vence is None else vence,
    )


def _ajustes_plan(min_clics=3):
    return Ajustes(config_version_id=99, tope_diario=10, min_clics=min_clics, avisos=False)


def _plan_base():
    candidata_mx = _clave(grupo=1, termino="candidata mx")
    candidata_us = _clave(plataforma="amazon_us", grupo=2, termino="candidata us")
    return (
        (
            _propuesta(1, _clave(grupo=9, termino="veto uno")),
            _propuesta(
                2,
                _clave(grupo=9, termino="veto dos"),
                destino=_clave(grupo=10, termino="veto dos"),
                kind="harvest",
            ),
        ),
        {
            _clave(grupo=9, termino="veto uno"): _eco_spend(),
            _clave(grupo=9, termino="veto dos"): _eco_spend(),
            _clave(grupo=10, termino="veto dos"): _eco_spend(),
            candidata_mx: _eco_spend(gasto="9.00"),
            candidata_us: _eco_spend(gasto="100.00"),
        },
        frozenset({candidata_mx}),
        frozenset(),
        _ajustes_plan(),
    )


def test_planear_determinista():
    propuestas, economia, vigentes, cortes, ajustes = _plan_base()
    primero = planear(propuestas, economia, vigentes, cortes, ajustes)
    assert planear(propuestas, economia, vigentes, cortes, ajustes) == primero
    revueltos = dict(reversed(list(economia.items())))
    assert planear(propuestas, revueltos, vigentes, cortes, ajustes) == primero


def test_planear_sin_claves_repetidas():
    repetida = _clave(grupo=9, termino="veto uno")
    propuestas, economia, _, _, ajustes = _plan_base()
    plan = planear(propuestas, economia, frozenset({repetida}), frozenset(), ajustes)
    claves = [u.clave for u in plan]
    assert len(claves) == len(set(claves))
    assert claves.count(repetida) == 1
    assert next(u for u in plan if u.clave == repetida).tramo == "propuesta"


def test_planear_propuestas_primero_por_vencimiento():
    antes = AHORA - timedelta(hours=2)
    pasado = AHORA - timedelta(hours=1)
    futuro = AHORA + timedelta(hours=12)
    origen_a = _clave(grupo=1, termino="veto a")
    origen_b = _clave(grupo=2, termino="veto b")
    destino_b = _clave(grupo=3, termino="veto b")
    origen_c = _clave(grupo=9, termino="veto c")
    candidata = _clave(grupo=4, termino="candidata")
    propuestas = (
        _propuesta(1, origen_a, vence=futuro),
        _propuesta(2, origen_b, destino=destino_b, vence=pasado, kind="harvest"),
        _propuesta(3, origen_c, vence=antes),
    )
    economia = {
        origen_a: _eco_spend(),
        origen_b: _eco_spend(),
        destino_b: _eco_spend(),
        origen_c: _eco_spend(),
        candidata: _eco_spend(gasto="50.00"),
    }
    plan = planear(propuestas, economia, frozenset(), frozenset(), _ajustes_plan())
    assert [(u.clave, u.tramo, u.vence_el) for u in plan[:4]] == [
        (origen_c, "propuesta", antes),
        (destino_b, "propuesta", pasado),
        (origen_b, "propuesta", pasado),
        (origen_a, "propuesta", futuro),
    ]
    assert plan[4].clave == candidata


def test_planear_tramos_alternancia_y_resellado():
    mx_caro = _clave(grupo=1, termino="mx cara")
    us_cara = _clave(plataforma="amazon_us", grupo=2, termino="us cara")
    mx_barata = _clave(grupo=3, termino="mx barata")
    resuelta = _clave(grupo=4, termino="ya vendio")
    resellada = _clave(grupo=5, termino="con corte")
    economia = {
        mx_caro: _eco_spend(gasto="9.00"),
        us_cara: _eco_spend(gasto="100.00"),
        mx_barata: _eco_spend(gasto="5.00"),
        resuelta: _eco_spend(historial=_hist(2)),
        resellada: _eco_spend(gasto="7.00"),
        _clave(grupo=6, termino="b012345678"): _eco_spend(gasto="60.00"),
        _clave(grupo=7, termino="pocos clics"): _eco_spend(clics=2, gasto="40.00"),
        _clave(grupo=8, termino="cortada"): _eco_spend(gasto="30.00"),
    }
    plan = planear(
        (),
        economia,
        frozenset({resellada}),
        frozenset({_clave(grupo=8, termino="cortada"), resellada}),
        _ajustes_plan(),
    )
    assert [(u.clave.termino, u.tramo) for u in plan] == [
        ("mx cara", "desempate"),
        ("us cara", "desempate"),
        ("con corte", "desempate"),
        ("mx barata", "desempate"),
        ("ya vendio", "resto"),
    ]
    assert all(u.vence_el is None for u in plan)
