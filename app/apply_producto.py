"""Ejecucion de la pausa por producto sin ventas (AUTO-03 I.3).

Una fila `pause` de apply_queue sobre un `product_ad` (S9, S11) vuelve a
decidirse al liberarse y se ejecuta con el ledger de retiro de
app/retiro_anuncios.py, como el hook `apply_harvest.aplica_harvest`: recibe el
`Aplicador` de `apply_cola.libera_vencidos` y usa `aplicador._cliente()`, cuyo
transporte corre el heartbeat y la comprobacion de dueno del ciclo antes de
cada HTTP (`guard_http`). Ese cliente es el `escritor` de
`retiro_anuncios.ejecuta_retiro` y de `archivar.leer_anuncios`. Este modulo
nunca importa app.ads.write ni arma un escritor propio
(`archivar.preparar_escritor` no lleva esa proteccion).

Orden sellado (docs/APPLY.md §3): `revalida_pause_producto` corre PRE-cobro
sobre la fila released; `libera_vencidos` cobra la cuota de `pause` (una sola
reserva) y reclama la fila; `ejecuta_pause_producto` escribe el apply_attempt
pre-HTTP con la cuota cobrada y despues llama `ejecuta_retiro` con la
`AutorizacionDeDecision` de la fila. La intencion de retiro se escribe antes
del PUT, asi que una fila `applying` sin intencion de su decision prueba que
no salio ningun PUT: `reconcilia_pause_producto` la cierra en `failed` sin
cuota ni intencion nuevas. Solo este modulo cierra estas filas y resuelve sus
intenciones (el paso diario del vigia no toca las que traen decision_id).

Importa `apply_cola` solo para tipos: apply_cola importa este modulo.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

import psycopg

from app import apply, productos_io
from app import retiro_anuncios as ra
from app.ads import archivar
from app.ads.client import AdsApiError
from app.optimizer import productos

if TYPE_CHECKING:
    from app.apply import Aplicador
    from app.apply_cola import FilaCola

# Mismos literales que apply_cola (MOTIVO_*): no se importan por el ciclo de
# imports. Los demas motivos de descarte salen del vocabulario de
# productos.MOTIVOS_PRODUCTO (regresado_por_ti, impulso_abierto, bajo_limites...).
MOTIVO_YA_NO_CALIFICA = "ya_no_califica"
MOTIVO_ENTIDAD_NO_VIVA = "entidad_no_viva"
MOTIVO_ALCANCE_CAMBIO = "alcance_cambio"
MOTIVO_VENDIO_EN_VENTANA = "vendio_en_ventana"
MOTIVO_REGRESADO = "regresado_por_ti"
MOTIVO_RETIRO_EN_CURSO = "retiro_en_curso"

_SQL_DECISION = """
SELECT inputs, decided_at FROM decision WHERE id = %s
"""

_SQL_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_CAMPANAS = "SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = 'campaign'"

_SQL_PADRE = "SELECT parent_id FROM ad_entity WHERE id = %s"

_SQL_CACHE_ESTADO = """
UPDATE ad_entity_state SET status = %s, synced_at = now() WHERE ad_entity_id = %s
"""

_SQL_INTENTO_ABIERTO = """
SELECT id FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'normal' AND finished_at IS NULL
 ORDER BY id DESC LIMIT 1
"""


def _termina(conn: psycopg.Connection, queue_id: int, estado: str) -> None:
    """applied|failed de la fila (espejo de apply_cola._termina)."""
    sello = "applied_at" if estado == "applied" else "failed_at"
    conn.execute(
        f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s", (estado, queue_id)
    )


def _goals(conn: psycopg.Connection, platform: str):
    """Goals frescos con el mismo lector que el ciclo (import local: el ciclo
    importa apply_cola, que importa este modulo)."""
    from app.cycle import lee_goals

    campanas = [f[0] for f in conn.execute(_SQL_CAMPANAS, (platform,)).fetchall()]
    return lee_goals(conn, platform, campanas)


def _entrada_de(
    entradas: Sequence[productos.EntradaProducto], product_id: int
) -> productos.EntradaProducto | None:
    return next((e for e in entradas if e.producto.product_id == product_id), None)


def _ventana_congelada(entrada: productos.EntradaProducto | None, inputs: dict) -> str | None:
    """La ventana con que se decidio, releida con las observaciones hasta
    ahora: exige cero compras y cero ventas en sus fechas. Una fecha que ya
    no se puede probar descarta con su motivo."""
    if entrada is None:
        return MOTIVO_YA_NO_CALIFICA
    actividad = entrada.actividad_pausa
    if isinstance(actividad, productos.SinDato):
        return actividad.motivos[0] if actividad.motivos else MOTIVO_YA_NO_CALIFICA
    ventana = inputs.get("ventana")
    leida = {
        "desde": actividad.ventana.desde.isoformat(),
        "hasta_exclusivo": actividad.ventana.hasta_exclusivo.isoformat(),
    }
    if ventana != leida:
        return MOTIVO_YA_NO_CALIFICA
    if actividad.compras_total != 0 or actividad.ventas_total.valor != 0:
        return MOTIVO_VENDIO_EN_VENTANA
    return None


def revalida_pause_producto(
    conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, ahora: dt.datetime
) -> str | None:
    """Ver `_revalida`. Un dato que la regla rechaza (ValueError: config o
    insumos corruptos) descarta esta fila en vez de tumbar el barrido: la
    fila quedaria released y trabaria a las que vienen detras."""
    try:
        return _revalida(conn, aplicador, fila, ahora)
    except ValueError as exc:
        print(f"revalida pausa de producto {fila.id}: dato invalido ({exc})", file=sys.stderr)
        return MOTIVO_YA_NO_CALIFICA


def _revalida(
    conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, ahora: dt.datetime
) -> str | None:
    """None = la pausa sigue calificando; un motivo = descarte PRE-cobro.

    Vuelve a correr la regla de la pausa del producto de la decision con la
    ventana [D-60, D-30) al reloj de liberacion, con settings y goals frescos
    (goal y modo del anuncio, impulso abierto por v_ad_group_impulso_abierto,
    regreso y gracia de S17); la propia fila no cuenta como clave en vuelo.
    Antes, la huella del ad group debe seguir igual a la congelada
    (alcance_cambio). Despues exige cero ventas en las fechas de la ventana
    congelada, releida con las observaciones hasta ahora. Al final lee el
    estado vivo en Amazon con el cliente del Aplicador: no ENABLED =
    entidad_no_viva; ENABLED con un retiro abierto (lo reactivaste en Amazon
    antes del sync) = regresado_por_ti; con una intencion de retiro abierta de
    otra autorizacion = retiro_en_curso (ejecuta_retiro no mandaria el PUT y la
    cuota se perderia). AdsApiError sube: libera_vencidos la trata como
    sin_respuesta y la fila espera released."""
    platform = aplicador._platform
    decision = conn.execute(_SQL_DECISION, (fila.decision_id,)).fetchone()
    inputs = decision[0] if decision is not None else None
    if (
        not isinstance(inputs, dict)
        or inputs.get("motor") != "producto"
        or not isinstance(inputs.get("product_id"), int)
    ):
        return MOTIVO_YA_NO_CALIFICA
    product_id = inputs["product_id"]
    grupo = conn.execute(_SQL_PADRE, (fila.ad_entity_id,)).fetchone()
    if grupo is None or grupo[0] is None:
        return MOTIVO_ENTIDAD_NO_VIVA
    if productos_io.huella_grupo(conn, grupo[0]) != inputs.get("huella_alcance"):
        return MOTIVO_ALCANCE_CAMBIO
    config = conn.execute(_SQL_CONFIG).fetchone()
    settings = config[0] if config is not None and isinstance(config[0], dict) else {}
    lectura = {
        "settings": settings,
        "goals": _goals(conn, platform),
        "solo_productos": (product_id,),
        "excluye_cola": fila.id,
    }
    fresca = _entrada_de(
        productos_io.lee_entradas_productos(conn, platform, decidido_el=ahora, **lectura),
        product_id,
    )
    if fresca is None:
        return MOTIVO_YA_NO_CALIFICA
    evaluacion = productos.resolver_producto(fresca)
    if fila.ad_entity_id not in {x.anuncio.ad_entity_id for x in evaluacion.pausas}:
        excluido = next(
            (x.motivo for x in fresca.excluidos if x.ad_entity_id == fila.ad_entity_id), None
        )
        if excluido is not None:
            return excluido
        return evaluacion.motivos_pausa[0] if evaluacion.motivos_pausa else MOTIVO_YA_NO_CALIFICA
    congelada = _entrada_de(
        productos_io.lee_entradas_productos(
            conn, platform, decidido_el=decision[1], observadas_hasta=ahora, **lectura
        ),
        product_id,
    )
    motivo = _ventana_congelada(congelada, inputs)
    if motivo is not None:
        return motivo
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] != "product_ad":
        return MOTIVO_ENTIDAD_NO_VIVA
    vista = archivar.leer_anuncios(aplicador._cliente(), [identidad[1]]).get(identidad[1])
    if vista is None or vista.get("state") != archivar.ESTADO_HABILITADO:
        return MOTIVO_ENTIDAD_NO_VIVA
    if conn.execute(ra._SQL_YA_RETIRADO, (fila.ad_entity_id,)).fetchone() is not None:
        return MOTIVO_REGRESADO
    ajena = conn.execute(ra._SQL_INTENCION_ABIERTA, (fila.ad_entity_id,)).fetchone()
    if ajena is not None and ajena[1] != fila.decision_id:
        return MOTIVO_RETIRO_EN_CURSO
    return None


def _sella_aplicada(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    queue_id: int,
    decision_id: int,
    ad_entity_id: int,
    id_attempt: int | None,
    ack: dict,
) -> None:
    """El cierre de una pausa confirmada, en UNA transaccion: apply_attempt
    con ack, resultado y finished_at; decision_application con verify_ok y el
    ciclo ejecutor; ad_entity_state con lo leido (PAUSED) y la fila applied."""
    conn.commit()
    with conn.transaction():
        if id_attempt is not None:
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
        apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
        conn.execute(_SQL_CACHE_ESTADO, (archivar.ESTADO_PAUSADO, ad_entity_id))
        _termina(conn, queue_id, "applied")


def _sella_fallida(
    conn: psycopg.Connection,
    queue_id: int,
    id_attempt: int | None,
    ack: dict | None,
    resultado: str,
) -> None:
    conn.commit()
    with conn.transaction():
        if id_attempt is not None:
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        _termina(conn, queue_id, "failed")


def ejecuta_pause_producto(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola) -> str:
    """La fila ya esta `applying` y su cuota de `pause` ya se cobro
    (libera_vencidos). apply_attempt pre-HTTP con la cuota cobrada y commit;
    despues `ejecuta_retiro` con la autorizacion de la decision y el cliente
    del Aplicador. Devuelve:

    - "applied": retirado o adoptado (PAUSED confirmado por el readback).
    - "failed": rechazo, no existe, archivado, cerrado, ya retirado por otro
      camino, autorizacion invalida o tope de intentos.
    - "pendiente": PUT incierto (intencion abierta) o Amazon no respondio la
      lectura previa (sin intencion, sin PUT): la fila sigue applying con su
      attempt abierto y la cierra reconcilia_pause_producto."""
    try:
        plan = ra.plan_de_decision(conn, fila.decision_id)
    except ValueError:
        _sella_fallida(conn, fila.id, None, None, "fallo:entidad_sin_identidad")
        return "failed"
    id_attempt = apply._ledger(
        conn, fila.decision_id, "normal", fila.request_payload, quota_cobrada=True
    )
    if id_attempt is None:
        _sella_fallida(conn, fila.id, None, None, "fallo:tope_intentos")
        return "failed"
    conn.commit()  # intencion durable PRE-HTTP
    try:
        (resultado,) = ra.ejecuta_retiro(
            conn,
            plan,
            autorizacion=ra.AutorizacionDeDecision(fila.decision_id, fila.id),
            escritor=aplicador._cliente(),
        )
    except ra.AutorizacionInvalida as exc:
        _sella_fallida(conn, fila.id, id_attempt, None, f"fallo:autorizacion_invalida: {exc}")
        return "failed"
    except AdsApiError:
        return "pendiente"
    ack = {"retiro_id": resultado.retiro_id, "resultado": resultado.resultado}
    if resultado.resultado in ("retirado", "adoptado"):
        _sella_aplicada(
            conn, aplicador, fila.id, fila.decision_id, fila.ad_entity_id, id_attempt, ack
        )
        return "applied"
    if resultado.resultado == "pendiente":
        return "pendiente"
    _sella_fallida(
        conn,
        fila.id,
        id_attempt,
        {**ack, "detalle": resultado.detalle},
        f"fallo:retiro_{resultado.resultado}",
    )
    return "failed"


def reconcilia_pause_producto(conn: psycopg.Connection, aplicador: Aplicador, fila) -> str | None:
    """Una fila `applying` de pausa por producto (crash, PUT incierto o dueno
    perdido). `fila` trae id, ad_entity_id y decision_id. Nunca manda un PUT
    ni cobra cuota:

    - sin intencion de la decision: no salio ningun PUT; sella el
      apply_attempt abierto como fallo sin HTTP y cierra en failed.
    - intencion cerrada con retiro (o abierta y Amazon la ve PAUSED, sellada
      ahora como adoptada): los mismos sellos que el exito.
    - intencion cerrada sin retiro (o ENABLED, ARCHIVED o ausente): failed.
    - Amazon no responde: None, la fila espera al proximo ciclo.

    Devuelve "applied", "failed" o None."""
    estado, retiro_id = ra.resuelve_intencion_de_decision(
        conn, fila.decision_id, aplicador._cliente()
    )
    if estado == "pendiente":
        return None
    intento = conn.execute(_SQL_INTENTO_ABIERTO, (fila.decision_id,)).fetchone()
    id_attempt = intento[0] if intento is not None else None
    if estado == "sellado":
        ack = {"retiro_id": retiro_id, "resultado": "reconciliado"}
        _sella_aplicada(
            conn, aplicador, fila.id, fila.decision_id, fila.ad_entity_id, id_attempt, ack
        )
        return "applied"
    resultado = (
        "fallo:sin_intencion_sin_http"
        if estado == "sin_intencion"
        else "fallo:reconciliado_sin_retiro"
    )
    _sella_fallida(conn, fila.id, id_attempt, None, resultado)
    return "failed"
