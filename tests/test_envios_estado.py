"""ENVIOS 01: huella del envio observado y clasificador puro (spec §2 y §3)."""

from __future__ import annotations

import re
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.envios.estado import AMAZON_CONFIRMA_ENTREGA, Clasificacion, clasificar
from app.envios.modelo import (
    DIAS_SEGUIMIENTO,
    Articulo,
    EnvioObservado,
    PaqueteObservado,
    huella,
)

AHORA = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
VENCIDO = AHORA - timedelta(days=1)
VIGENTE = AHORA + timedelta(days=1)


def envio_de_prueba(**cambios) -> EnvioObservado:
    """Envio sin hechos: cada prueba declara solo los que le importan."""
    base = dict(
        platform="amazon_mx",
        envio_ref="701-0000001-0000001",
        pedidos=("701-0000001-0000001",),
        pack_ref=None,
        logistica_fuente="MERCHANT",
        estado_fuente=None,
        subestado_fuente=None,
        pedido_creado_el=None,
        fuente_actualizado_el=None,
        despachar_antes_de=None,
        entrega_prometida_el=None,
        entrega_limite_el=None,
        entregado_el=None,
        devuelto_el=None,
        articulos=(),
        paquetes=(),
    )
    base.update(cambios)
    return EnvioObservado(**base)


def _paquete(estado=None, detalle=None, transportista="AMZMX", ref="PKG-1") -> PaqueteObservado:
    return PaqueteObservado(
        paquete_ref=ref,
        transportista=transportista,
        servicio=None,
        guia=None,
        despachado_el=None,
        estado_fuente=estado,
        detalle_fuente=detalle,
    )


def _juzga(**cambios) -> Clasificacion:
    return clasificar(envio_de_prueba(**cambios), juzgado_al=AHORA)


def _meli(status, substatus=None, **cambios) -> Clasificacion:
    cambios.setdefault("logistica_fuente", "xd_drop_off")
    return _juzga(platform="meli", estado_fuente=status, subestado_fuente=substatus, **cambios)


# ---------------------------------------------------------------------------
# Constantes y huella
# ---------------------------------------------------------------------------


def test_constantes_del_spec():
    assert DIAS_SEGUIMIENTO == 30
    assert sorted(AMAZON_CONFIRMA_ENTREGA) == [("amazon_mx", "AMZMX"), ("amazon_mx", "DHLC")]


def _envio_completo() -> EnvioObservado:
    return envio_de_prueba(
        platform="meli",
        envio_ref="44000000001",
        pedidos=("2000001", "2000002"),
        pack_ref="2000009",
        logistica_fuente="xd_drop_off",
        estado_fuente="shipped",
        subestado_fuente="out_for_delivery",
        pedido_creado_el=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        fuente_actualizado_el=datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
        despachar_antes_de=datetime(2026, 11, 21, 10, 0, tzinfo=UTC),
        entrega_prometida_el=datetime(2026, 11, 26, 10, 0, tzinfo=UTC),
        entrega_limite_el=datetime(2026, 11, 27, 10, 0, tzinfo=UTC),
        entregado_el=datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
        devuelto_el=datetime(2026, 11, 25, 11, 0, tzinfo=UTC),
        articulos=(
            Articulo("2000001", "SKU-A", "Arras", 1, "MLM1"),
            Articulo("2000002", None, "Collar", 2, None),
        ),
        paquetes=(
            PaqueteObservado("44000000001", "Agencias", None, "G-1", None, None, None),
            PaqueteObservado("44000000002", "Agencias", None, None, VENCIDO, None, None),
        ),
    )


def test_huella_ignora_el_orden_de_la_fuente():
    envio = _envio_completo()
    volteado = replace(
        envio,
        pedidos=envio.pedidos[::-1],
        articulos=envio.articulos[::-1],
        paquetes=envio.paquetes[::-1],
    )
    assert re.fullmatch(r"[0-9a-f]{64}", huella(envio))
    assert huella(volteado) == huella(envio)


def test_huella_ignora_el_offset_horario_del_mismo_instante():
    envio = _envio_completo()
    cdmx = timezone(timedelta(hours=-6))
    otro = replace(
        envio,
        pedido_creado_el=envio.pedido_creado_el.astimezone(cdmx),
        entrega_limite_el=envio.entrega_limite_el.astimezone(cdmx),
        paquetes=(
            envio.paquetes[0],
            replace(envio.paquetes[1], despachado_el=VENCIDO.astimezone(cdmx)),
        ),
    )
    assert re.fullmatch(r"[0-9a-f]{64}", huella(envio))
    assert huella(otro) == huella(envio)


def test_huella_excluye_fuente_actualizado_el():
    envio = _envio_completo()
    assert re.fullmatch(r"[0-9a-f]{64}", huella(envio))
    assert huella(replace(envio, fuente_actualizado_el=VIGENTE)) == huella(envio)
    assert huella(replace(envio, fuente_actualizado_el=None)) == huella(envio)


def test_huella_cambia_con_cualquier_otro_hecho():
    envio = _envio_completo()
    otro_instante = datetime(2026, 11, 1, tzinfo=UTC)
    cambios = {
        "platform": "amazon_mx",
        "envio_ref": "otro",
        "pedidos": ("2000001",),
        "pack_ref": None,
        "logistica_fuente": "self_service",
        "estado_fuente": "delivered",
        "subestado_fuente": None,
        "pedido_creado_el": otro_instante,
        "despachar_antes_de": otro_instante,
        "entrega_prometida_el": otro_instante,
        "entrega_limite_el": None,
        "entregado_el": None,
        "devuelto_el": None,
        "articulos": (envio.articulos[0], replace(envio.articulos[1], cantidad=3)),
        "paquetes": (envio.paquetes[0], replace(envio.paquetes[1], guia="G-2")),
    }
    # Un campo nuevo del modelo obliga a decidir si entra en la huella.
    assert set(cambios) | {"fuente_actualizado_el"} == {f.name for f in fields(EnvioObservado)}
    base = huella(envio)
    for campo, valor in cambios.items():
        assert huella(replace(envio, **{campo: valor})) != base, campo


def test_huella_rechaza_fecha_sin_zona():
    envio = envio_de_prueba(entregado_el=datetime(2026, 11, 25, 10, 0))
    with pytest.raises(ValueError, match="zona horaria"):
        huella(envio)


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------


def test_amazon_fba_shipped_vencido_sin_atencion():
    assert _juzga(
        logistica_fuente="AMAZON",
        estado_fuente="SHIPPED",
        despachar_antes_de=VENCIDO,
        entrega_prometida_el=VENCIDO,
    ) == Clasificacion("enviado_sin_confirmacion", "amazon_fba", None)


@pytest.mark.parametrize(
    ("platform", "transportista"),
    [("amazon_us", "DHL"), ("amazon_mx", "DHL"), ("amazon_us", "AMZMX")],
)
def test_amazon_us_y_mx_dhl_shipped_vencido_sin_atencion(platform, transportista):
    enviado = _juzga(
        platform=platform,
        estado_fuente="SHIPPED",
        despachar_antes_de=VENCIDO,
        entrega_prometida_el=VENCIDO,
        paquetes=(_paquete("SHIPPED", None, transportista),),
    )
    assert enviado == Clasificacion("enviado_sin_confirmacion", "amazon_fbm", None)

    sin_despachar = _juzga(
        platform=platform,
        estado_fuente="UNSHIPPED",
        despachar_antes_de=VENCIDO,
        entrega_prometida_el=VENCIDO,
    )
    assert sin_despachar == Clasificacion("por_despachar", "amazon_fbm", "despacho_vencido")


@pytest.mark.parametrize("transportista", ["AMZMX", "DHLC"])
def test_amazon_mx_shipped_con_transportista_que_confirma_va_en_camino(transportista):
    assert _juzga(
        estado_fuente="SHIPPED",
        entrega_prometida_el=VENCIDO,
        paquetes=(_paquete("SHIPPED", None, transportista),),
    ) == Clasificacion("en_camino", "amazon_fbm", "entrega_vencida")


@pytest.mark.parametrize(
    ("detalle", "estado"),
    [
        ("DROPPED_OFF", "en_camino"),
        ("PICKED_UP", "en_camino"),
        (None, "en_camino"),
        ("OUT_FOR_DELIVERY", "en_reparto"),
    ],
)
def test_amazon_mx_in_transit(detalle, estado):
    paquetes = (_paquete("IN_TRANSIT", detalle),)
    a_tiempo = _juzga(estado_fuente="SHIPPED", entrega_prometida_el=VIGENTE, paquetes=paquetes)
    assert a_tiempo == Clasificacion(estado, "amazon_fbm", None)
    tarde = _juzga(estado_fuente="SHIPPED", entrega_prometida_el=VENCIDO, paquetes=paquetes)
    assert tarde == Clasificacion(estado, "amazon_fbm", "entrega_vencida")


@pytest.mark.parametrize("transportista", ["AMZMX", "DHLC"])
def test_amazon_mx_entregado_sin_atencion(transportista):
    assert _juzga(
        estado_fuente="SHIPPED",
        entrega_prometida_el=VENCIDO,
        paquetes=(_paquete("DELIVERED", "DELIVERED", transportista),),
    ) == Clasificacion("entregado", "amazon_fbm", None)


@pytest.mark.parametrize(
    ("detalle", "estado", "motivo"),
    [
        ("RETURNING_TO_SELLER", "en_devolucion", "problema_entrega"),
        ("RETURNED_TO_SELLER", "devuelto", None),
        ("RETURNED", "devuelto", None),
        ("DAMAGED", "problema", "problema_entrega"),
        (None, "problema", "problema_entrega"),
    ],
)
def test_amazon_undeliverable(detalle, estado, motivo):
    assert _juzga(
        estado_fuente="SHIPPED",
        paquetes=(_paquete("UNDELIVERABLE", detalle),),
    ) == Clasificacion(estado, "amazon_fbm", motivo)


def test_amazon_multi_paquete_manda_mas_atrasado():
    entregado = _paquete("DELIVERED", "DELIVERED", ref="PKG-1")
    regresa = _paquete("UNDELIVERABLE", "RETURNING_TO_SELLER", ref="PKG-2")
    for paquetes in ((entregado, regresa), (regresa, entregado)):
        assert _juzga(estado_fuente="SHIPPED", paquetes=paquetes) == Clasificacion(
            "en_devolucion", "amazon_fbm", "problema_entrega"
        )
    assert _juzga(
        estado_fuente="PARTIALLY_SHIPPED",
        despachar_antes_de=VIGENTE,
        paquetes=(entregado,),
    ) == Clasificacion("por_despachar", "amazon_fbm", None)


_PROBLEMA = ("UNDELIVERABLE", "DAMAGED")
_REGRESANDO = ("UNDELIVERABLE", "RETURNING_TO_SELLER")
_RARO = ("ALGO_NUEVO", None)
_REPARTO = ("IN_TRANSIT", "OUT_FOR_DELIVERY")
_CAMINO = ("IN_TRANSIT", "PICKED_UP")
_SIN_CONFIRMAR = ("SHIPPED", None)
_DEVUELTO = ("UNDELIVERABLE", "RETURNED")
_ENTREGADO = ("DELIVERED", "DELIVERED")


@pytest.mark.parametrize(
    ("mayor", "menor", "estado"),
    [
        (_PROBLEMA, _REGRESANDO, "problema"),
        (_REGRESANDO, _RARO, "en_devolucion"),
        (_RARO, _REPARTO, "sin_clasificar"),
        (_REPARTO, _CAMINO, "en_reparto"),
        (_CAMINO, _SIN_CONFIRMAR, "en_camino"),
        (_SIN_CONFIRMAR, _DEVUELTO, "enviado_sin_confirmacion"),
        (_DEVUELTO, _ENTREGADO, "devuelto"),
    ],
)
def test_amazon_orden_de_severidad_entre_paquetes(mayor, menor, estado):
    paquetes = (
        _paquete(*menor, transportista="DHL", ref="PKG-1"),
        _paquete(*mayor, transportista="DHL", ref="PKG-2"),
    )
    for orden in (paquetes, paquetes[::-1]):
        assert _juzga(estado_fuente="SHIPPED", paquetes=orden).estado == estado


@pytest.mark.parametrize(
    ("paquete", "estado"),
    [
        (_PROBLEMA, "problema"),
        (_REGRESANDO, "en_devolucion"),
        (_RARO, "sin_clasificar"),
        (_REPARTO, "por_despachar"),
        (_ENTREGADO, "por_despachar"),
    ],
)
def test_amazon_pedido_parcial_compite_por_severidad(paquete, estado):
    assert (
        _juzga(estado_fuente="PARTIALLY_SHIPPED", paquetes=(_paquete(*paquete),)).estado == estado
    )


@pytest.mark.parametrize(
    ("estado_fuente", "estado"),
    [
        ("PENDING", "pendiente"),
        ("PENDING_AVAILABILITY", "pendiente"),
        ("INVOICE_UNCONFIRMED", "pendiente"),
        ("UNSHIPPED", "por_despachar"),
        ("PARTIALLY_SHIPPED", "por_despachar"),
        ("SHIPPED", "enviado_sin_confirmacion"),
        ("CANCELLED", "cancelado"),
        ("UNFULFILLABLE", "problema"),
        ("ALGO_NUEVO", "sin_clasificar"),
        (None, "sin_clasificar"),
    ],
)
def test_amazon_sin_paquetes(estado_fuente, estado):
    assert _juzga(estado_fuente=estado_fuente).estado == estado


# ---------------------------------------------------------------------------
# Mercado Libre
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "substatus", "estado", "motivo"),
    [
        ("pending", "buffered", "pendiente", None),
        ("handling", None, "por_despachar", None),
        ("ready_to_ship", "ready_to_print", "por_despachar", None),
        ("ready_to_ship", "printed", "por_despachar", None),
        ("ready_to_ship", None, "por_despachar", None),
        ("ready_to_ship", "dropped_off", "en_camino", None),
        ("ready_to_ship", "picked_up", "en_camino", None),
        ("ready_to_ship", "delayed", "por_despachar", "despacho_vencido"),
        ("ready_to_ship", "stale", "por_despachar", "despacho_vencido"),
        ("shipped", None, "en_camino", None),
        ("shipped", "delayed", "en_camino", None),
        ("shipped", "out_for_delivery", "en_reparto", None),
        ("shipped", "soon_deliver", "en_reparto", None),
        ("shipped", "at_the_door", "en_reparto", None),
        ("shipped", "in_delivery_zone", "en_reparto", None),
        ("shipped", "waiting_for_withdrawal", "esperando_recoleccion", None),
        ("shipped", "bad_address", "problema", "problema_entrega"),
        ("shipped", "refused_delivery", "problema", "problema_entrega"),
        ("shipped", "not_localized", "problema", "problema_entrega"),
        ("shipped", "contact_with_carrier_required", "problema", "problema_entrega"),
        ("delivered", None, "entregado", None),
        ("not_delivered", "receiver_absent", "problema", "problema_entrega"),
        ("not_delivered", None, "problema", "problema_entrega"),
        ("not_delivered", "returning_to_sender", "en_devolucion", "problema_entrega"),
        ("not_delivered", "returned_to_hub", "en_devolucion", "problema_entrega"),
        ("cancelled", None, "cancelado", None),
    ],
)
def test_meli_estados_y_excepciones(status, substatus, estado, motivo):
    # Plazo de despacho ausente: xd_drop_off no lo manda (sonda §2).
    clasificacion = _meli(status, substatus, entrega_prometida_el=VIGENTE)
    assert clasificacion == Clasificacion(estado, "meli_envios", motivo)


def test_meli_entregado_a_agencia_ignora_plazo_de_despacho_vencido():
    assert _meli(
        "ready_to_ship", "dropped_off", despachar_antes_de=VENCIDO, entrega_prometida_el=VIGENTE
    ) == Clasificacion("en_camino", "meli_envios", None)


def test_meli_plazo_de_despacho_vencido():
    assert _meli("ready_to_ship", "printed", despachar_antes_de=VENCIDO) == Clasificacion(
        "por_despachar", "meli_envios", "despacho_vencido"
    )


def test_meli_esperando_recoleccion_con_fecha_vencida_sin_atencion():
    assert _meli(
        "shipped",
        "waiting_for_withdrawal",
        entrega_prometida_el=VENCIDO,
        entrega_limite_el=VENCIDO,
    ) == Clasificacion("esperando_recoleccion", "meli_envios", None)


@pytest.mark.parametrize("substatus", ["receiver_absent", "returning_to_sender"])
def test_meli_devuelto_cierra_sin_atencion(substatus):
    assert _meli("not_delivered", substatus, devuelto_el=VENCIDO) == Clasificacion(
        "devuelto", "meli_envios", None
    )


@pytest.mark.parametrize(
    ("limite", "prometida", "motivo"),
    [
        (VIGENTE, VENCIDO, None),
        (VENCIDO, VIGENTE, "entrega_vencida"),
        (None, VENCIDO, "entrega_vencida"),
        (None, VIGENTE, None),
    ],
)
def test_meli_el_limite_de_entrega_manda_sobre_la_fecha_prometida(limite, prometida, motivo):
    for substatus in (None, "out_for_delivery"):
        clasificacion = _meli(
            "shipped", substatus, entrega_limite_el=limite, entrega_prometida_el=prometida
        )
        assert clasificacion.motivo == motivo


@pytest.mark.parametrize(
    ("logistica", "tipo", "motivo"),
    [
        ("fulfillment", "meli_full", None),
        ("self_service", "meli_flex", "entrega_vencida"),
        ("xd_drop_off", "meli_envios", "entrega_vencida"),
        ("drop_off", "meli_envios", "entrega_vencida"),
        ("cross_docking", "meli_envios", "entrega_vencida"),
        ("algo_nuevo", None, None),
        (None, None, None),
    ],
)
def test_meli_tipo_logistico_decide_si_hay_atencion(logistica, tipo, motivo):
    assert _meli(
        "shipped", logistica_fuente=logistica, entrega_prometida_el=VENCIDO
    ) == Clasificacion("en_camino", tipo, motivo)


@pytest.mark.parametrize(
    ("status", "substatus"),
    [("shipped", "bad_address"), ("ready_to_ship", "delayed")],
)
def test_meli_full_nunca_pide_atencion(status, substatus):
    clasificacion = _meli(
        status, substatus, logistica_fuente="fulfillment", despachar_antes_de=VENCIDO
    )
    assert clasificacion.tipo == "meli_full"
    assert clasificacion.motivo is None


# ---------------------------------------------------------------------------
# Desconocidos, fechas ausentes y reloj inyectado
# ---------------------------------------------------------------------------


def test_desconocido_fechas_none_y_reloj_congelado():
    desconocido = _meli(
        "algo_nuevo",
        "delayed",
        despachar_antes_de=VENCIDO,
        entrega_prometida_el=VENCIDO,
        entrega_limite_el=VENCIDO,
    )
    assert desconocido == Clasificacion("sin_clasificar", "meli_envios", None)

    assert _juzga(estado_fuente="UNSHIPPED") == Clasificacion("por_despachar", "amazon_fbm", None)
    assert _meli("shipped") == Clasificacion("en_camino", "meli_envios", None)

    por_despachar = envio_de_prueba(estado_fuente="UNSHIPPED", despachar_antes_de=AHORA)
    en_camino = envio_de_prueba(
        estado_fuente="SHIPPED",
        entrega_prometida_el=AHORA,
        paquetes=(_paquete("IN_TRANSIT", "PICKED_UP"),),
    )
    for envio, motivo in ((por_despachar, "despacho_vencido"), (en_camino, "entrega_vencida")):
        un_segundo = timedelta(seconds=1)
        assert clasificar(envio, juzgado_al=AHORA - un_segundo).motivo is None
        assert clasificar(envio, juzgado_al=AHORA).motivo is None
        assert clasificar(envio, juzgado_al=AHORA + un_segundo).motivo == motivo
