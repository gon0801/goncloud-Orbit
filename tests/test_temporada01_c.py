"""TEMPORADA 01, bloque C: la subida rapida R6r y su freno de presupuesto.

- `test_temporada01_c_recupera_con_evidencia` (dinero, la fila C.1): un bid
  recortado 8 -> 2.16 con evidencia madura recupera en UN paso detras de
  `ads_bid_recuperacion_amazon_mx=live`; el salto tras un recorte reciente
  queda frenado por R14; el ciclo siguiente no repite el salto (la propiedad
  de que bid x m* no depende de cuantos pasos se dieron) y el replay del caso
  congelado rejuega igual.
- Las demas prueban pureza: la inversion exacta de gamma_q, el terreno del
  dueno, el techo de baja compartido (20 -> 10 -> 7 -> 14), BUD, los frenos
  del primer y segundo salto, la derivacion de `ultimo_salto_recuperacion`
  y la semantica de hoy sin marcadores.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import ROUND_FLOOR, Decimal
from typing import Any

import pytest
from test_cycle import _config_version, _entidad, _estado, _metrica, _run
from test_optimizer_caso import cambio, efecto, fabrica_caso, nivel, tramo, trayectoria
from test_temporada01_b import (
    D_E1,
    _cambio_confirmado,
    _corre,
    _goal_mx,
    _handler_mx,
    _kw,
    _medianoche,
    _settings,
    _siembra_metricas,
)

from app import cycle as ciclo_mod
from app.lecturas_caso import _presupuesto_campanas
from app.optimizer import politica, recuperacion, temporada
from app.optimizer.caso import (
    BidVigente,
    CambioFechado,
    CasoHoja,
    HistoriaBid,
    InsumosRecuperacion,
    ManoExterna,
    PresupuestoCampana,
    TemporadaCaso,
)
from app.optimizer.centavos import CENTAVO
from app.optimizer.evidencia import gamma_q

PISO_MX, TECHO_MX = Decimal("1.00"), Decimal("50.00")
CONFIANZA_SUBIDA = Decimal("0.70")


# ---------------------------------------------------------------------------
# helpers de casos puros
# ---------------------------------------------------------------------------


def _nivel_caso_b():
    """Evidencia madura del caso tipo: pesados = 80 clics, 5 pedidos, venta
    2400, gasto 152 -> cpc 1.90 al bid de hoy."""
    return nivel(
        tramo(clics=40, pedidos=3, venta="1500", gasto="76", impresiones=400),
        tramo(clics=80, pedidos=4, venta="1800", gasto="152", impresiones=400),
    )


def _cadena_recorte(*, motivo: str = "pierde_dinero_fuerte") -> HistoriaBid:
    """La cadena de recortes 8 -> 2.16 con el motivo general congelado."""
    return HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("2.16"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo=motivo,
            ),
        ),
    )


def _caso_recupera(**ajas):
    """Caso vendedor recortado 8 -> 2.16 con la memoria del bid y su evidencia."""
    base = dict(
        bid=BidVigente(Decimal("2.16"), "MXN", Decimal("0.50"), TECHO_MX),
        propia=_nivel_caso_b(),
        grupo=nivel(tramo(clics=120, pedidos=7, venta="3300", gasto="228", impresiones=800)),
        cuenta=nivel(tramo(clics=120, pedidos=7, venta="3300", gasto="228", impresiones=800)),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 8, 20), "8.00", "2.16", "motor")],
            efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
        ),
        historia=_cadena_recorte(),
        recuperacion=InsumosRecuperacion(version="recuperacion_v1", memoria_dias=365),
    )
    base.update(ajas)
    return fabrica_caso(**base)


def _temporada_caso_baja(*, fin=None) -> TemporadaCaso:
    """El marcador de temporada con la hoja en baja (frontera sin fin)."""
    estacion = temporada.Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=dt.date(2027, 1, 5),
        fase="baja",
        previa_alta=False,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=dt.date(2027, 1, 1),
    )
    frontera = temporada.FronteraHoja(
        fase_hoja="baja",
        mult_cortes=Decimal("2"),
        cerca_hoja=dt.date(2027, 1, 5),
        cerca_plataforma=dt.date(2027, 1, 5),
        episodio_id=1,
        fin=fin,
    )
    return TemporadaCaso(
        estacion=estacion,
        episodio=temporada.EpisodioVigente(id=1, abierto_el=_medianoche(dt.date(2027, 1, 4))),
        excluida_en=frozenset(),
        meta_alta_pct=Decimal("25"),
        meta_manual=False,
        frontera=frontera,
    )


def _cadena_baja(base: str, bajo: str, episodio_id: int = 1) -> HistoriaBid:
    """La cadena 20 -> 10 (motor) -> 7 (entrada de temporada) del diseno."""
    return HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal(base),
                bid_despues=Decimal(bajo),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo="pierde_dinero_fuerte",
            ),
            CambioFechado(
                confirmado_el=dt.datetime(2027, 1, 4, 12, 0, tzinfo=dt.UTC),
                decision_id=2,
                bid_antes=Decimal(bajo),
                bid_despues=Decimal("7.00"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=episodio_id,
            ),
        ),
    )


def _caso_baja(cadena: HistoriaBid, bid: str) -> CasoHoja:
    return fabrica_caso(
        bid=BidVigente(Decimal(bid), "MXN", PISO_MX, TECHO_MX),
        propia=_nivel_caso_b(),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 1), "20.00", "10.00", "motor")],
            efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
        ),
        historia=cadena,
        recuperacion=InsumosRecuperacion(version="recuperacion_v1", memoria_dias=365),
        temporada=_temporada_caso_baja(),
    )


def _arg_del_caso(caso: CasoHoja) -> Decimal:
    """El argumento que R6 y R6r pasan a gamma_q, reconstruido desde la
    evidencia del caso: clics_sub x cpc / (0.85 x meta/100 x ticket_sub)."""
    pesados = caso.propia
    pedidos = pesados.pedidos_pesados()
    clics = pesados.clics_pesados()
    venta = pesados.venta_pesada()
    cvr_cuenta = Decimal(caso.cuenta.pedidos_crudos()) / Decimal(caso.cuenta.clics_crudos())
    aov_cuenta = caso.cuenta.venta_cruda() / Decimal(caso.cuenta.pedidos_crudos())
    clics_sub = clics + Decimal(1) / cvr_cuenta
    ticket_sub = (venta + aov_cuenta) / (pedidos + Decimal(1))
    cpc, _fuente = politica._precio_de_hoy(caso)
    target = caso.economia.target_acos_pct
    return clics_sub * cpc / (politica.MULT_SUBIDA * target / Decimal(100) * ticket_sub)


# ---------------------------------------------------------------------------
# pureza: multiplicador, techo, terreno, frenos, BUD
# ---------------------------------------------------------------------------


def test_temporada01_c_multiplicador_sostenible_invierte_gamma_q():
    """La biseccion de 60 iteraciones encuentra el x* donde gamma_q cae
    EXACTAMENTE a la confianza, sin importar el caso."""
    for a, arg in (
        (Decimal(6), Decimal("4.967")),
        (Decimal(3), Decimal("2.9")),
        (Decimal(13), Decimal("10.8")),
    ):
        m = recuperacion.multiplicador_sostenible(a, arg, CONFIANZA_SUBIDA)
        assert abs(gamma_q(a, m * arg) - CONFIANZA_SUBIDA) < Decimal("1e-9")
    # menos confianza exige un multiplicador mas alto
    m_70 = recuperacion.multiplicador_sostenible(Decimal(6), Decimal("4.967"), Decimal("0.70"))
    m_50 = recuperacion.multiplicador_sostenible(Decimal(6), Decimal("4.967"), Decimal("0.50"))
    assert m_50 > m_70
    # evidencia que hoy no alcanza (R6 no dispara): m* < 1, R6r tampoco actua
    assert gamma_q(Decimal(6), Decimal("4.967")) < CONFIANZA_SUBIDA
    assert m_70 < 1


def test_temporada01_c_recupera_un_paso_con_evidencia():
    """R6r decide el salto completo cuando la evidencia sostiene mas que un
    paso de R6; el destino sale topado y en centavos."""
    caso = _caso_recupera()
    arg = _arg_del_caso(caso)
    m = recuperacion.multiplicador_sostenible(
        Decimal(6), arg, caso.economia.plataforma.confianza_subida
    )
    assert m > Decimal("1.15"), "la evidencia del caso sostiene mas que +15 %"
    esperado = (Decimal("2.16") * m).quantize(CENTAVO, rounding=ROUND_FLOOR)
    techo = recuperacion.techo_conocido(caso)
    assert techo == Decimal("8.00"), "el techo conocido es el bid previo al recorte"
    esperado = min(esperado, techo, TECHO_MX)
    veredicto = politica.decide(caso)
    assert isinstance(veredicto, politica.Regresar)
    assert veredicto.motivo == politica.MOTIVO_RECUPERA_NIVEL
    assert veredicto.bid_nuevo == esperado
    assert esperado > Decimal("2.16") * Decimal("1.15")


def test_temporada01_c_sin_evidencia_decide_el_r6_de_hoy():
    """La mayoria de las keywords (evidencia que no alcanza) queda igual que
    hoy: sin pedidos la evidencia no llega ni a R6 ni a R6r, y eso es
    correcto — para ellas la palanca es el regreso de temporada."""
    delgada = nivel(
        tramo(clics=6, pedidos=0, venta="0", gasto="11.40", impresiones=60),
        tramo(clics=12, pedidos=0, venta="0", gasto="22.80", impresiones=120),
    )
    caso_sin = _caso_recupera(propia=delgada, cuenta=None, grupo=None, historia=None)
    caso_con = _caso_recupera(propia=delgada, cuenta=None, grupo=None)
    assert politica.decide(caso_con) == politica.decide(caso_sin)
    assert politica.decide(caso_con) == politica.Mantener(politica.MOTIVO_SIN_EVIDENCIA)


def test_temporada01_c_terreno_del_dueno_no_recupera():
    """Una mano del dueno registrada resetea el techo conocido: mientras el bid
    vigente no sea el ultimo de Orbit, no hay R6r (solo el R6 de hoy)."""
    con_mano = HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=_cadena_recorte().cambios,
        manos_externas=(
            ManoExterna(
                instante=dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC),
                bid_visto=Decimal("3.00"),
                origen="exclusion",
            ),
        ),
    )
    caso = _caso_recupera(historia=con_mano)
    assert recuperacion.techo_conocido(caso) is None, "dueno_al_final: terreno de David"
    veredicto = politica.decide(caso)
    assert not isinstance(veredicto, politica.Regresar)
    # la mano queda vieja cuando Orbit vuelve a mover despues: el techo arranca
    # en el bid que ella dejo
    con_mano_y_subida = HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=_cadena_recorte().cambios
        + (
            CambioFechado(
                confirmado_el=dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC),
                decision_id=2,
                bid_antes=Decimal("3.00"),
                bid_despues=Decimal("2.16"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo="pierde_dinero_fuerte",
            ),
        ),
        manos_externas=con_mano.manos_externas,
    )
    caso_2 = _caso_recupera(historia=con_mano_y_subida)
    assert recuperacion.techo_conocido(caso_2) == Decimal("3.00")


def test_temporada01_c_techo_de_baja_compartido():
    """20 -> 10 (motor) -> 7 (entrada): en baja R6r nunca pasa de
    floor(20 x 0.70) = 14.00, aunque la evidencia sostenga 23. El caso
    8 -> 5.60 sin recortes previos no tiene nada que recuperar."""
    caso_d = _caso_baja(_cadena_baja("20.00", "10.00"), "7.00")
    assert temporada.techo_de_baja(caso_d) == Decimal("14.00")
    destino = recuperacion.destino_recuperacion(
        caso_d, a_subida=Decimal(6), arg_subida=Decimal("1.00")
    )
    assert destino == Decimal("14.00"), "m* grande topa en el techo de baja"
    caso_simple = _caso_baja(_cadena_baja("8.00", "8.00"), "7.00")
    del caso_simple  # la cadena de 8 no tiene recorte previo: se arma abajo
    cadena_simple = HistoriaBid(
        desde=dt.date(2026, 12, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2027, 1, 4, 12, 0, tzinfo=dt.UTC),
                decision_id=2,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("5.60"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=1,
            ),
        ),
    )
    caso_8 = _caso_baja(cadena_simple, "5.60")
    assert temporada.techo_de_baja(caso_8) == Decimal("5.60")
    assert (
        recuperacion.destino_recuperacion(caso_8, a_subida=Decimal(6), arg_subida=Decimal("1.00"))
        is None
    ), "en baja no hay nada que recuperar sobre 5.60"
    # la reduccion que termina este ciclo (fin guardado) no protege nada
    caso_fin = _caso_baja(_cadena_baja("20.00", "10.00"), "7.00")
    caso_fin = replace(
        caso_fin,
        temporada=_temporada_caso_baja(
            fin=temporada.FinReduccion(episodio_id=1, motivo="ya_arriba", excluye_abierto=False)
        ),
    )
    assert temporada.techo_de_baja(caso_fin) is None


def test_temporada01_c_presupuesto_topado_bloquea_subida():
    """BUD: campana topada (o sin dato que alcance la racha) no sube; sin
    insumo, lo de hoy."""
    topada = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)

    def con_presupuesto(**campos) -> PresupuestoCampana:
        return PresupuestoCampana(topada=topada, **campos)

    caso_lleno = _caso_recupera()
    assert recuperacion.presupuesto_topado(caso_lleno) is False, "sin caso.presupuesto"
    caso_true = _caso_recupera(
        presupuesto=con_presupuesto(
            presupuesto_diario=Decimal("100"), dias_medidos=7, dias_topados=5
        )
    )
    assert recuperacion.presupuesto_topado(caso_true) is True
    assert politica.decide(caso_true) == politica.Mantener(politica.MOTIVO_PRESUPUESTO_TOPADO)
    caso_desconocido = _caso_recupera(
        presupuesto=con_presupuesto(presupuesto_diario=None, dias_medidos=7, dias_topados=None)
    )
    assert recuperacion.presupuesto_topado(caso_desconocido) is None
    assert politica.decide(caso_desconocido) == politica.Mantener(
        politica.MOTIVO_PRESUPUESTO_TOPADO
    )
    caso_pocos_dias = _caso_recupera(
        presupuesto=con_presupuesto(
            presupuesto_diario=Decimal("100"), dias_medidos=3, dias_topados=3
        )
    )
    assert recuperacion.presupuesto_topado(caso_pocos_dias) is None
    caso_no_topada = _caso_recupera(
        presupuesto=con_presupuesto(
            presupuesto_diario=Decimal("100"), dias_medidos=7, dias_topados=4
        )
    )
    assert recuperacion.presupuesto_topado(caso_no_topada) is False
    veredicto = politica.decide(caso_no_topada)
    assert isinstance(veredicto, politica.Regresar)
    assert veredicto.motivo == politica.MOTIVO_RECUPERA_NIVEL
    # BUD tambien frena la subida normal de R6 (+15 %) con la lectura presente
    caso_sin_memoria = _caso_recupera(
        historia=None,
        recuperacion=None,
        presupuesto=con_presupuesto(
            presupuesto_diario=Decimal("100"), dias_medidos=7, dias_topados=5
        ),
    )
    veredicto_r6 = politica.decide(caso_sin_memoria)
    assert veredicto_r6 == politica.Mantener(politica.MOTIVO_PRESUPUESTO_TOPADO)


def test_temporada01_c_frenos_del_salto():
    """Primer salto tras el recorte: solo R14 (se salta R15, R16 y el juicio
    economico). Segundo salto seguido (el ultimo cambio fue una subida): pasa
    por los frenos de R6 (R15 pide 20 clics al precio nuevo)."""
    primer = _caso_recupera(
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 8, 20), "8.00", "2.16", "motor")],
            efecto(dias_post=10, clics_post=10, gasto_post="19.00", vendia=False),
        ),
    )
    veredicto = politica.decide(primer)
    assert veredicto == politica.Mantener(politica.MOTIVO_ESPERANDO_PRECIO_MEDIDO), (
        "R14 frena el primer salto con menos de 20 clics al precio nuevo"
    )
    # segundo salto: el ultimo cambio fue una SUBIDA (2.16 -> 2.48)
    cadena_doble = HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=_cadena_recorte().cambios
        + (
            CambioFechado(
                confirmado_el=dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC),
                decision_id=2,
                bid_antes=Decimal("2.16"),
                bid_despues=Decimal("2.48"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo="bajo_target",
            ),
        ),
    )
    segundo = _caso_recupera(
        bid=BidVigente(Decimal("2.48"), "MXN", Decimal("0.50"), TECHO_MX),
        historia=cadena_doble,
        trayectoria=trayectoria(
            [
                cambio(dt.date(2026, 8, 20), "8.00", "2.16", "motor"),
                cambio(dt.date(2026, 9, 10), "2.16", "2.48", "motor"),
            ],
            efecto(dias_post=10, clics_post=10, gasto_post="19.00", vendia=False),
        ),
    )
    veredicto = politica.decide(segundo)
    assert veredicto == politica.Mantener(politica.MOTIVO_SIN_CLICS_NUEVOS), (
        "el segundo salto pasa por los frenos completos de R6"
    )


def test_temporada01_c_sin_frenos_tras_cambio_de_temporada():
    """El ultimo cambio de TEMPORADA es un movimiento planeado (decision 7 del
    plan): ningun freno de _freno_trayectoria lo juzga como si buscara un
    efecto — arriba solo espero R2 y el piso aprendido lo aplica _paso. Tanto
    el salto de recuperacion como la subida normal de R6 salen sin R14/R15."""
    cadena_temporada = HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("5.60"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=1,
            ),
        ),
    )
    trayectoria_temporada = trayectoria(
        [cambio(dt.date(2026, 9, 20), "8.00", "5.60", "temporada")],
        efecto(dias_post=10, clics_post=10, gasto_post="19.00", vendia=False),
    )
    salto = politica.decide(
        _caso_recupera(
            bid=BidVigente(Decimal("5.60"), "MXN", Decimal("0.50"), TECHO_MX),
            historia=cadena_temporada,
            trayectoria=trayectoria_temporada,
        )
    )
    assert isinstance(salto, politica.Regresar), salto
    assert salto.motivo == politica.MOTIVO_RECUPERA_NIVEL
    assert salto.bid_nuevo == Decimal("8.00"), "la evidencia sostiene hasta el techo conocido"
    # la subida normal de R6 tambien pasa sin R15 (el mismo criterio)
    subida = politica.decide(
        _caso_recupera(
            revision=1,
            historia=None,
            recuperacion=None,
            trayectoria=trayectoria_temporada,
        )
    )
    assert isinstance(subida, politica.Mover), subida
    assert subida.motivo == politica.MOTIVO_BAJO_TARGET
    assert subida.bid_nuevo == Decimal("2.48")


def test_temporada01_c_ultimo_salto_recuperacion_se_deriva():
    """`ultimo_salto_recuperacion` sale del motivo general congelado del ultimo
    cambio de Orbit; los casos congelados antes de C (motivo None) derivan
    False, como el bloque B prometio."""
    assert recuperacion.cadena_bid(_caso_recupera()).ultimo_salto_recuperacion is False
    caso_salto = _caso_recupera(historia=_cadena_recorte(motivo="recupera_nivel"))
    assert recuperacion.cadena_bid(caso_salto).ultimo_salto_recuperacion is True
    sin_motivo = HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("2.16"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
            ),
        ),
    )
    assert (
        recuperacion.cadena_bid(_caso_recupera(historia=sin_motivo)).ultimo_salto_recuperacion
        is False
    )


def test_temporada01_c_semantica_de_hoy_sin_marcadores():
    """Historia presente sin recuperacion (o sin nada) decide BYTE A BYTE lo de
    hoy; lo que cambia el veredicto es el interruptor de recuperacion."""
    caso_hoy = _caso_recupera(historia=None, recuperacion=None)
    caso_con_historia = _caso_recupera(recuperacion=None)
    assert politica.decide(caso_hoy) == politica.decide(caso_con_historia)
    # el mismo caso con el interruptor encendido SI acelera (y topa el salto)
    caso_con_flag = _caso_recupera()
    veredicto = politica.decide(caso_con_flag)
    assert isinstance(veredicto, politica.Regresar)
    assert veredicto.motivo == politica.MOTIVO_RECUPERA_NIVEL
    assert veredicto.bid_nuevo > Decimal("2.16") * Decimal("1.15")


def test_temporada01_c_freeze_con_y_sin_motivo():
    """El freeze del bloque C escribe `motivo`; los congelados antes de C no lo
    traen y deserializan igual (replay historico byte a byte)."""
    caso = _caso_recupera()
    congelado: dict[str, Any] = caso.como_json()
    for cambio_json in congelado["historia"]["cambios"]:
        assert "motivo" in cambio_json
    reproducido = CasoHoja.desde_json(congelado)
    assert politica.decide(reproducido) == politica.decide(caso)
    # shape viejo: sin la clave `motivo`
    for cambio_json in congelado["historia"]["cambios"]:
        cambio_json.pop("motivo", None)
    viejo = CasoHoja.desde_json(congelado)
    assert politica.decide(viejo) == politica.decide(caso)
    assert recuperacion.cadena_bid(viejo).ultimo_salto_recuperacion is False


def test_temporada01_c_piso_del_goal_sin_evidencia_para_cruzarlo():
    """Si el dueno elevo el piso del goal por encima del nivel que la evidencia
    sostiene, el salto no cabe en el rango: R6r se abstiene (subir hasta el
    piso le inventaria evidencia) y lo decide el R6 de hoy."""
    caso = _caso_recupera(bid=BidVigente(Decimal("2.16"), "MXN", Decimal("6.00"), TECHO_MX))
    arg = _arg_del_caso(caso)
    assert Decimal("2.16") * recuperacion.multiplicador_sostenible(
        Decimal(6), arg, Decimal("0.70")
    ) < Decimal("6.00"), "la evidencia sostiene menos que el piso nuevo"
    assert recuperacion.destino_recuperacion(caso, a_subida=Decimal(6), arg_subida=arg) is None
    veredicto = politica.decide(caso)
    assert not isinstance(veredicto, politica.Regresar)


def test_temporada01_c_memoria_acota_el_techo():
    """El techo conocido nunca autoriza saltos con pujas fuera de la memoria
    elegida, aunque la historia llegue mas atras (un episodio abierto no
    caduca: la temporada pide la historia larga)."""
    viejo = dt.datetime(2025, 5, 1, 12, 0, tzinfo=dt.UTC)
    cadena_larga = HistoriaBid(
        desde=dt.date(2025, 4, 1),
        cambios=(
            CambioFechado(
                confirmado_el=viejo,
                decision_id=1,
                bid_antes=Decimal("30.00"),
                bid_despues=Decimal("8.00"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo="pierde_dinero_fuerte",
            ),
            CambioFechado(
                confirmado_el=dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC),
                decision_id=2,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("2.16"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo="pierde_dinero_fuerte",
            ),
        ),
    )
    dentro = _caso_recupera(historia=cadena_larga)
    # fabrica_caso congela ventana_hasta 2026-09-29: hoy = 2026-10-09 y la
    # memoria de 365 dias llega a 2025-10-10; el recorte de 30.00 (2025-05)
    # queda FUERA.
    assert recuperacion.techo_conocido(dentro) == Decimal("8.00")
    destino = recuperacion.destino_recuperacion(
        dentro, a_subida=Decimal(6), arg_subida=Decimal("1.00")
    )
    assert destino is not None and destino <= Decimal("8.00"), "nunca salta con la puja vieja"
    fuera = _caso_recupera(
        historia=cadena_larga,
        recuperacion=InsumosRecuperacion(version="recuperacion_v1", memoria_dias=1000),
    )
    assert recuperacion.techo_conocido(fuera) == Decimal("30.00"), (
        "con memoria larga la puja de 2025 vuelve a contar"
    )


# ---------------------------------------------------------------------------
# dinero: la fila C.1 de punta a punta (ciclo real con Amazon simulado)
# ---------------------------------------------------------------------------


def _evidencia_caso_d(conn, kw: int, desde: dt.date, corte: dt.date, hasta: dt.date) -> None:
    """La evidencia del CASO D del diseno (12 pedidos pesados, cpc 1.90, ticket
    450 -> m* 3.29), hora 2 sobre la siembra neutral: los 41 dias recientes del
    tramo pesan entero (2 clics/dia = 82) y los 40 antiguos la mitad (1 clic en
    36 de ellos = 18) -> clics_pesados 100, pedidos_pesados 12, venta_pesada
    5400, ticket 450, a_subida 13. La previa de cuenta en CVR 3 % la completa
    `_evidencia_cuenta` con otra hoja. Los dias DESPUES del corte maduro (hasta
    D-1) se siembran con el mismo cpc para que el precio medido del cambio no
    se mezcle con el relleno neutro."""
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-c') RETURNING id"
    ).fetchone()[0]
    for i in range((hasta - desde).days + 1):
        dia = desde + dt.timedelta(days=i)
        maduro = dia <= corte
        reciente = maduro and i >= (corte - desde).days + 1 - 41
        clicks = 2 if reciente else (0 if maduro and i % 10 == 0 else 1)
        pedido = reciente and i >= (corte - desde).days + 1 - 12
        _metrica(
            conn,
            run_id,
            kw,
            dia,
            dt.datetime(dia.year, dia.month, dia.day, 2, tzinfo=dt.UTC),
            moneda="MXN",
            cost="3.80" if clicks == 2 else ("1.90" if clicks else "0.00"),
            ad_revenue="450.00" if pedido else "0.00",
            clicks=clicks,
            orders=1 if pedido else 0,
            impressions=100,
        )


def _evidencia_cuenta(conn, kw: int, desde: dt.date, hasta: dt.date) -> None:
    """Los clics de relleno que dejan la previa de cuenta en CVR 3 % (282
    clics sin pedidos a 1.20 MXN: debajo del piso de pausa de 500 MXN y del
    gasto para concluir, la hoja de relleno no decide ni se pausa)."""
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-c-cuenta') RETURNING id"
    ).fetchone()[0]
    for i in range((hasta - desde).days + 1):
        dia = desde + dt.timedelta(days=i)
        _metrica(
            conn,
            run_id,
            kw,
            dia,
            dt.datetime(dia.year, dia.month, dia.day, 2, tzinfo=dt.UTC),
            moneda="MXN",
            cost="1.20",
            ad_revenue="0.00",
            clicks=4 if i < 39 else 3,
            orders=0,
            impressions=100,
        )


def test_temporada01_c_recupera_con_evidencia(db_template):
    """La fila C.1 de punta a punta: con `ads_bid_recuperacion_amazon_mx=live`,
    un bid recortado y madurado recupera en un paso el nivel que la evidencia
    sostiene (motivo `recupera_nivel`); un recorte reciente queda frenado por
    R14; el ciclo siguiente no repite el salto; el replay del caso congelado
    rejuega igual; y sin el interruptor la misma hoja decide lo de hoy."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(
        conn, {**_settings(modo_temporada=None), "ads_bid_recuperacion_amazon_mx": "live"}
    )
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "c-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "c-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(D_E1))
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(D_E1) - dt.timedelta(days=15)),
    )
    corte_viejo = D_E1 - dt.timedelta(days=40)
    kw1 = _kw(conn, ag, "c-kw1", "kw 1", "2.16")
    _siembra_metricas(conn, [camp, kw1])
    ventana_desde, ventana_hasta = D_E1 - dt.timedelta(days=90), D_E1 - dt.timedelta(days=10)
    _evidencia_caso_d(conn, kw1, ventana_desde, ventana_hasta, D_E1 - dt.timedelta(days=1))
    # la previa de cuenta en CVR 3 %: hoja de relleno en OTRO ad group
    ag2 = _entidad(conn, "amazon_mx", "ad_group", "c-ag2", parent=camp)
    _estado(conn, ag2, synced_at=_medianoche(D_E1))
    kw_cuenta = _kw(conn, ag2, "c-kw-cuenta", "kw cuenta", "6.00")
    _siembra_metricas(conn, [kw_cuenta])
    _evidencia_cuenta(conn, kw_cuenta, ventana_desde, ventana_hasta)
    _cambio_confirmado(
        conn,
        kw1,
        config_id,
        dia=corte_viejo,
        antes="8.00",
        despues="2.16",
        motivo="pierde_dinero_fuerte",
    )
    conn.commit()

    handler = _handler_mx({"c-kw1": "2.16", "c-kw-cuenta": "6.00"})
    res = _corre(conn, D_E1, handler)
    assert res.status == "done", res.notes
    decisiones = {
        hoja: (motivo, nuevo)
        for hoja, motivo, nuevo in conn.execute(
            "SELECT d.ad_entity_id, d.inputs->>'motivo', d.new_value"
            " FROM decision d WHERE d.cycle_id = %s AND d.kind = 'bid'",
            (res.cycle_id,),
        ).fetchall()
    }
    # kw1: recorte de hace 40 dias -> R14 pasa -> R6r decide el salto completo:
    # el CASO D del diseno (12 pedidos pesados, m* 3.29) da 2.16 x 3.29 = 7.10.
    motivo_1, nuevo_1 = decisiones[kw1]
    assert motivo_1 == politica.MOTIVO_RECUPERA_NIVEL, decisiones
    congelado = conn.execute(
        "SELECT inputs->'caso' FROM decision WHERE cycle_id = %s AND ad_entity_id = %s",
        (res.cycle_id, kw1),
    ).fetchone()[0]
    print(
        "DEPURACION propia",
        congelado["propia"],
        "| cuenta",
        None if congelado.get("cuenta") is None else congelado["cuenta"]["reciente"],
        None if congelado.get("cuenta") is None else congelado["cuenta"]["antiguo"],
        "| efecto",
        congelado["trayectoria"]["efecto"],
    )
    assert nuevo_1 == Decimal("7.10"), (nuevo_1, decisiones)
    assert nuevo_1 > Decimal("2.16") * Decimal("1.15"), "el salto supera un paso de R6"
    # replay fiel: el caso congelado rejuega la misma decision.
    assert ciclo_mod.reproduce(
        conn.execute(
            "SELECT inputs FROM decision WHERE cycle_id = %s AND ad_entity_id = %s",
            (res.cycle_id, kw1),
        ).fetchone()[0]
    ) == ("bid", nuevo_1, "MXN")
    # la hoja de relleno no decide (gasto bajo el piso de pausa y de corte)
    assert kw_cuenta not in decisiones, decisiones

    # Ciclo siguiente: la misma evidencia al precio nuevo da bid x m* ~= bid:
    # ningun OTRO salto de recuperacion. El +15 % de R6 que puede seguir (la
    # evidencia del bid nuevo madura) es el camino de hoy y el diseno lo
    # permite explicitamente (caso D del diseno).
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    motivos = conn.execute(
        "SELECT d.inputs->>'motivo' FROM decision d WHERE d.cycle_id = %s AND d.kind = 'bid'"
        " AND d.ad_entity_id = %s",
        (res.cycle_id, kw1),
    ).fetchall()
    assert politica.MOTIVO_RECUPERA_NIVEL not in [fila[0] for fila in motivos], motivos
    # La puja en Amazon sigue la cadena de decisiones sin saltos raros: el
    # paso de R6 si el ciclo 2 lo decidio, o el destino de la recuperacion.
    decisiones_2 = [
        fila[0]
        for fila in conn.execute(
            "SELECT d.new_value FROM decision d WHERE d.cycle_id = %s AND d.kind = 'bid'"
            " AND d.ad_entity_id = %s",
            (res.cycle_id, kw1),
        ).fetchall()
    ]
    assert conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw1,)
    ).fetchone()[0] == (decisiones_2[0] if decisiones_2 else nuevo_1)


def test_temporada01_c_practica_no_envia_pujas(db_template):
    """Con `ads_bid_recuperacion_amazon_mx=shadow` el ciclo va byte a byte lo
    de hoy: el caso real no lleva el marcador de recuperacion y jamas envia el
    salto a Amazon. La comparacion de sombra llega con el bloque D."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(
        conn, {**_settings(modo_temporada=None), "ads_bid_recuperacion_amazon_mx": "shadow"}
    )
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "c-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "c-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(D_E1))
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(D_E1) - dt.timedelta(days=15)),
    )
    kw1 = _kw(conn, ag, "c-kw1", "kw 1", "2.16")
    _siembra_metricas(conn, [kw1])
    ventana_desde, ventana_hasta = D_E1 - dt.timedelta(days=90), D_E1 - dt.timedelta(days=10)
    _evidencia_caso_d(conn, kw1, ventana_desde, ventana_hasta, D_E1 - dt.timedelta(days=1))
    _cambio_confirmado(
        conn,
        kw1,
        config_id,
        dia=D_E1 - dt.timedelta(days=40),
        antes="8.00",
        despues="2.16",
        motivo="pierde_dinero_fuerte",
    )
    conn.commit()
    res = _corre(conn, D_E1, _handler_mx({}))
    assert res.status == "done", res.notes
    decisiones = conn.execute(
        "SELECT d.inputs->>'motivo', d.new_value FROM decision d"
        " WHERE d.cycle_id = %s AND d.kind = 'bid' AND d.ad_entity_id = %s",
        (res.cycle_id, kw1),
    ).fetchall()
    for motivo, nuevo in decisiones:
        assert motivo != politica.MOTIVO_RECUPERA_NIVEL, decisiones
        assert nuevo <= Decimal("2.50"), "sin el marcador, el camino de hoy solo da +15 %"
    # el handler no vio ningun salto: la puja vigente sigue en el paso de R6
    assert conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw1,)
    ).fetchone()[0] <= Decimal("2.50")


def test_temporada01_c_costo_null_no_tumba_el_ciclo(db_template):
    """Una fila de campana con impresiones y costo NULL (la ingesta y el
    esquema lo admiten) NO aborta el ciclo: el gasto de ese dia se desconoce y
    la racha queda None (BUD no sube: dato faltante, no inventado)."""
    conn, _dsn = db_template
    camp = _entidad(conn, "amazon_mx", "campaign", "c-camp-null")
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-c-null') RETURNING id"
    ).fetchone()[0]
    dias = [D_E1 - dt.timedelta(days=i) for i in range(1, 8)]
    for i, dia in enumerate(dias):
        _metrica(
            conn,
            run_id,
            camp,
            dia,
            _medianoche(dia),
            moneda="MXN",
            cost=None if i == 0 else "10.00",
            impressions=100,
        )
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(D_E1)),
    )
    conn.commit()
    presupuesto = _presupuesto_campanas(
        conn, "amazon_mx", frozenset(dias), temporada.CampanaTopada(Decimal("0.90"), 5)
    )
    assert presupuesto[camp].presupuesto_diario == Decimal("1000.00")
    assert presupuesto[camp].dias_medidos == 7
    assert presupuesto[camp].dias_topados is None, "el dia sin costo se desconoce"


def test_temporada01_c_presupuesto_rejuega_con_visto_el(db_template):
    """El freno BUD respeta el corte bitemporal: una correccion append-only del
    costo de campana o un cambio de presupuesto DESPUES del corte no alteran
    `dias_topados` ni el presupuesto al rejugar con `visto_el` (el caso
    congelado rejuega identico); sin corte, lo nuevo si cambia el freno."""
    conn, _dsn = db_template
    camp = _entidad(conn, "amazon_mx", "campaign", "c-camp-asof")
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-c-asof') RETURNING id"
    ).fetchone()[0]
    dias = [D_E1 - dt.timedelta(days=i) for i in range(1, 8)]
    for dia in dias:
        _metrica(
            conn,
            run_id,
            camp,
            dia,
            _medianoche(dia) + dt.timedelta(hours=1),
            moneda="MXN",
            cost="10.00",
            impressions=100,
        )
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(dias[-1])),
    )
    conn.commit()
    corte = _medianoche(D_E1)
    topada = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)
    antes = _presupuesto_campanas(conn, "amazon_mx", frozenset(dias), topada, visto_el=corte)
    assert antes[camp] == PresupuestoCampana(
        presupuesto_diario=Decimal("1000.00"), dias_medidos=7, dias_topados=0, topada=topada
    ), antes[camp]
    # DESPUES del corte: correccion append-only del costo del dia (2000 >= 90 %)
    # y un cambio de presupuesto (1000 -> 100).
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, impressions, ingest_run_id)"
        " VALUES (%s, %s, %s, 'MXN', 2000.00, 100, %s)",
        (camp, dias[0], corte + dt.timedelta(hours=1), run_id),
    )
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 100.00, 'MXN')",
        (camp, corte + dt.timedelta(hours=2)),
    )
    conn.commit()
    despues_corte = _presupuesto_campanas(
        conn, "amazon_mx", frozenset(dias), topada, visto_el=corte
    )
    assert despues_corte == antes, "el rejuego con visto_el no ve lo posterior al corte"
    # sin corte, lo nuevo SI cambia el freno (la sonda discrimina)
    ahora = _presupuesto_campanas(conn, "amazon_mx", frozenset(dias), topada)
    assert ahora[camp].presupuesto_diario == Decimal("100.00")
    assert ahora[camp].dias_topados == 1, "el dia corregido queda topado con el presupuesto nuevo"


def test_temporada01_c_techo_de_baja_comparte_el_caso_d(db_template):
    """El ejemplo del diseno con su evidencia: la keyword 20 -> 10 (motor) -> 7
    (entrada de temporada) con la evidencia del caso D sostiene
    7 x 3.29 = 23.03, pero en baja el techo de baja compartido topa el salto en
    floor(20 x 0.70) = 14.00 — R6r decide Regresar(14.00), nunca 23.03."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(conn, {**_settings(), "ads_bid_recuperacion_amazon_mx": "live"})
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "c-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "c-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(D_E1))
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(D_E1) - dt.timedelta(days=15)),
    )
    ag2 = _entidad(conn, "amazon_mx", "ad_group", "c-ag2", parent=camp)
    _estado(conn, ag2, synced_at=_medianoche(D_E1))
    ciclo_previo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (_medianoche(D_E1 - dt.timedelta(days=50)), _medianoche(D_E1 - dt.timedelta(days=50))),
    ).fetchone()[0]
    episodio = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (_medianoche(D_E1 - dt.timedelta(days=50)), ciclo_previo, config_id),
    ).fetchone()[0]
    kw2 = _kw(conn, ag, "c-kw2", "kw 2", "7.00")
    kw_cuenta = _kw(conn, ag2, "c-kw-cuenta", "kw cuenta", "6.00")
    _siembra_metricas(conn, [camp, kw2, kw_cuenta])
    ventana_desde, ventana_hasta = D_E1 - dt.timedelta(days=90), D_E1 - dt.timedelta(days=10)
    _evidencia_caso_d(conn, kw2, ventana_desde, ventana_hasta, D_E1 - dt.timedelta(days=1))
    _evidencia_cuenta(conn, kw_cuenta, ventana_desde, ventana_hasta)
    _cambio_confirmado(
        conn,
        kw2,
        config_id,
        dia=D_E1 - dt.timedelta(days=50),
        antes="20.00",
        despues="10.00",
        motivo="pierde_dinero_fuerte",
    )
    _cambio_confirmado(
        conn,
        kw2,
        config_id,
        dia=D_E1 - dt.timedelta(days=40),
        antes="10.00",
        despues="7.00",
        motivo="entra_temporada_baja",
        episodio_id=episodio,
    )
    conn.commit()

    res = _corre(conn, D_E1, _handler_mx({}))
    assert res.status == "done", res.notes
    fila = conn.execute(
        "SELECT d.inputs->>'motivo', d.new_value FROM decision d"
        " WHERE d.cycle_id = %s AND d.kind = 'bid' AND d.ad_entity_id = %s",
        (res.cycle_id, kw2),
    ).fetchone()
    assert fila == (politica.MOTIVO_RECUPERA_NIVEL, Decimal("14.00")), fila


@pytest.mark.parametrize(
    "a, arg, confianza",
    [(6, "4.967", "0.70"), (3, "2.9", "0.50"), (13, "10.8", "0.80")],
)
def test_temporada01_c_biseccion_parametrizada(a: int, arg: str, confianza: str):
    """Misma inversion de gamma_q, mas casos: la propiedad que usa R6r."""
    m = recuperacion.multiplicador_sostenible(Decimal(a), Decimal(arg), Decimal(confianza))
    assert abs(gamma_q(Decimal(a), m * Decimal(arg)) - Decimal(confianza)) < Decimal("1e-9")


def test_temporada01_criterio_topada_unificado_motor_y_pantalla(db_template):
    """Motor (`_presupuesto_campanas`) y «Donde poner el dinero» (`lee_campanas`)
    dan exactamente el mismo conteo de dias al tope: ultimos 7 dias CON ingesta
    comparados contra el presupuesto vigente de cada dia."""
    from app.pantalla_dinero import lee_campanas

    conn, _dsn = db_template
    camp = _entidad(conn, "amazon_mx", "campaign", "c-camp-unif")
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-c-unif') RETURNING id"
    ).fetchone()[0]
    # 7 dias con ingesta saltando D-3 (hueco de ingesta) y cambio de presupuesto
    # a media semana: dias D-8..D-5 con presupuesto 100 (gasto 95 -> 4 topados),
    # dias D-4, D-2, D-1 con presupuesto 200 (gasto 100 -> 0 topados).
    dias_ingesta = [
        D_E1 - dt.timedelta(days=8),
        D_E1 - dt.timedelta(days=7),
        D_E1 - dt.timedelta(days=6),
        D_E1 - dt.timedelta(days=5),
        D_E1 - dt.timedelta(days=4),
        D_E1 - dt.timedelta(days=2),
        D_E1 - dt.timedelta(days=1),
    ]
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 100.00, 'MXN'),"
        " (%s, %s, 200.00, 'MXN')",
        (
            camp,
            _medianoche(D_E1 - dt.timedelta(days=10)),
            camp,
            _medianoche(D_E1 - dt.timedelta(days=4)),
        ),
    )
    for dia in dias_ingesta:
        costo = "95.00" if dia < D_E1 - dt.timedelta(days=4) else "100.00"
        _metrica(
            conn,
            run_id,
            camp,
            dia,
            _medianoche(dia) + dt.timedelta(hours=1),
            moneda="MXN",
            cost=costo,
            impressions=100,
        )
    conn.commit()
    topada = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)
    motor = _presupuesto_campanas(conn, "amazon_mx", frozenset(dias_ingesta), topada)[camp]
    filas = lee_campanas(
        conn,
        plataforma="amazon_mx",
        desde=D_E1 - dt.timedelta(days=30),
        hasta=D_E1 - dt.timedelta(days=1),
    )
    pantalla = next(f for f in filas if f.campana_id == camp)
    assert motor.dias_topados == 4
    assert pantalla.dias_al_tope_7d == motor.dias_topados == 4


def test_temporada01_en_terreno_del_dueno_con_episodio_abierto_en_normal():
    """En el primer ciclo normal antes de cerrar el episodio, una exclusion
    persistida en ese episodio abierto sigue vetando la subida rapida R6r."""
    cfg = temporada.desde_settings(_settings())
    assert cfg is not None
    est_normal = temporada.resuelve(cfg, dt.date(2027, 2, 1))
    assert est_normal.fase == "normal"
    caso = _caso_recupera(
        temporada=TemporadaCaso(
            estacion=est_normal,
            episodio=temporada.EpisodioVigente(id=42, abierto_el=_medianoche(dt.date(2027, 1, 1))),
            excluida_en=frozenset({42}),
            meta_alta_pct=Decimal("25"),
            meta_manual=False,
            frontera=temporada.FronteraHoja(
                fase_hoja="normal",
                mult_cortes=Decimal("1"),
                cerca_hoja=None,
                cerca_plataforma=None,
                episodio_id=42,
                fin=None,
                exclusion_hoy=None,
            ),
        )
    )
    assert temporada.en_terreno_del_dueno(caso, recuperacion.cadena_bid(caso)) is True
    assert (
        recuperacion.destino_recuperacion(caso, a_subida=Decimal("6"), arg_subida=Decimal("1.5"))
        is None
    )


def test_techo_conocido_sin_recuperacion_respeta_mano_del_dueno():
    """Hallazgo #4: con caso.recuperacion=None, techo_conocido debe respetar
    cad.manos_del_dueno para no doblar el techo_de_baja."""
    ts_entrada = dt.datetime(2027, 1, 4, 12, 0, tzinfo=dt.UTC)
    cadena = HistoriaBid(
        desde=dt.date(2026, 7, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal("20.00"),
                bid_despues=Decimal("15.00"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
                motivo="pierde_dinero_fuerte",
            ),
            CambioFechado(
                confirmado_el=ts_entrada,
                decision_id=2,
                bid_antes=Decimal("10.00"),
                bid_despues=Decimal("7.00"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=1,
            ),
        ),
        manos_externas=(
            ManoExterna(
                instante=dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC),
                bid_visto=Decimal("10.00"),
                origen="exclusion",
            ),
        ),
    )
    caso = fabrica_caso(
        bid=BidVigente(Decimal("7.00"), "MXN", PISO_MX, TECHO_MX),
        propia=_nivel_caso_b(),
        trayectoria=trayectoria(
            [cambio(dt.date(2027, 1, 4), "10.00", "7.00", "temporada")],
            efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
        ),
        historia=cadena,
        recuperacion=None,
        temporada=_temporada_caso_baja(),
    )
    assert recuperacion.techo_conocido(caso, hasta=ts_entrada) == Decimal("10.00")
    assert temporada.techo_de_baja(caso) == Decimal("7.00")


def test_dias_topados_coincide_en_motor_cola_y_pantalla_dinero(db_template):
    """Hallazgo #2: motor (lee_plataforma), cola (presupuestos_campanas_hoy) y
    pantalla_dinero (lee_campanas) deben compartir los ultimos 7 dias con
    ingesta de campanas (incluidas pausadas, excluyendo keywords)."""
    from app.lecturas_caso import lee_plataforma
    from app.lecturas_historia import presupuestos_campanas_hoy
    from app.optimizer.caso import EconomiaPlataforma, InsumosPausa
    from app.pantalla_dinero import lee_campanas

    conn, _dsn = db_template
    hoy = D_E1
    ayer = hoy - dt.timedelta(days=1)
    camp_on = _entidad(conn, "amazon_mx", "campaign", "c-unif-on")
    camp_off = _entidad(conn, "amazon_mx", "campaign", "c-unif-off")
    ag = _entidad(conn, "amazon_mx", "ad_group", "ag-unif", parent=camp_on)
    kw = _kw(conn, ag, "kw-unif", "kw unif", "5.00")
    _estado(conn, camp_on, synced_at=_medianoche(hoy), status="ENABLED")
    _estado(conn, camp_off, synced_at=_medianoche(hoy), status="PAUSED")
    _estado(conn, ag, synced_at=_medianoche(hoy), status="ENABLED")
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 100.00, 'MXN')",
        (camp_on, _medianoche(hoy - dt.timedelta(days=15))),
    )
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-unif-3vias') RETURNING id"
    ).fetchone()[0]
    # Campana ENABLED: 5 dias topados (D-8..D-4) + 2 dias no topados (D-3..D-2).
    for offset in range(8, 3, -1):
        dia = hoy - dt.timedelta(days=offset)
        _metrica(
            conn,
            run_id,
            camp_on,
            dia,
            _medianoche(dia) + dt.timedelta(hours=1),
            moneda="MXN",
            cost="95.00",
            impressions=100,
        )
    for offset in (3, 2):
        dia = hoy - dt.timedelta(days=offset)
        _metrica(
            conn,
            run_id,
            camp_on,
            dia,
            _medianoche(dia) + dt.timedelta(hours=1),
            moneda="MXN",
            cost="10.00",
            impressions=100,
        )
    # Campana PAUSED con metrica en D-1: desplaza la ventana de 7 dias a D-7..D-1.
    _metrica(
        conn,
        run_id,
        camp_off,
        ayer,
        _medianoche(ayer) + dt.timedelta(hours=1),
        moneda="MXN",
        cost="5.00",
        impressions=10,
    )
    # Keyword con metricas solo D-10..D-4 (no debe definir el calendario de campanas).
    for offset in range(10, 3, -1):
        dia = hoy - dt.timedelta(days=offset)
        _metrica(
            conn,
            run_id,
            kw,
            dia,
            _medianoche(dia) + dt.timedelta(hours=1),
            moneda="MXN",
            cost="1.00",
            clicks=1,
            orders=0,
            impressions=10,
        )
    conn.commit()

    topada = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)
    economia = EconomiaPlataforma(
        moneda="MXN",
        equilibrio_acos_pct=Decimal("30"),
        gasto_para_concluir=Decimal("350"),
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
    )
    lect = lee_plataforma(
        conn,
        "amazon_mx",
        _medianoche(hoy),
        economia=economia,
        campana_topada=topada,
        presupuesto_live=True,
    )
    caso = lect.caso(
        conn,
        hoja_id=kw,
        ad_group_id=ag,
        bid=BidVigente(Decimal("5.00"), "MXN", PISO_MX, TECHO_MX),
        target_acos_pct=Decimal("20"),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=Decimal("500"),
            expected_clicks=None,
            politica_economica=None,
        ),
    )
    assert caso.presupuesto is not None
    motor_dias = caso.presupuesto.dias_topados
    cola_dias = presupuestos_campanas_hoy(conn, "amazon_mx", hoy, topada)[camp_on].dias_topados
    filas = lee_campanas(
        conn,
        plataforma="amazon_mx",
        desde=ayer - dt.timedelta(days=29),
        hasta=ayer,
    )
    pantalla_dias = next(f for f in filas if f.campana_id == camp_on).dias_al_tope_7d
    assert motor_dias == cola_dias == pantalla_dias == 4, (
        f"divergencia 3 vias: motor={motor_dias}, cola={cola_dias}, pantalla={pantalla_dias}"
    )
