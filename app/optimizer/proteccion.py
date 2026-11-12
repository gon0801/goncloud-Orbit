"""Subpolitica pura de proteccion por venta reciente (ORBIT 07 M1).

El motor de cortes pausa por cero pedidos sobre la ventana madura, que termina
en D-10. Un pedido posterior al fin de la ventana de cortes de la hoja todavia
no entra a esa ventana: sin esta subpolitica, la hoja se pausa aunque acaba de
vender. La proteccion se abre una sola vez por pedido, con ancla y vencimiento
FIJOS (ancla + 10 dias UTC).

Reglas selladas por la entrega M1:

- Un pedido reciente conocido abre un episodio anclado a la fecha de pedido mas
  antigua de la lectura. Ni la corrida ni una reobservacion mueven el ancla.
- Mientras el episodio no vence, una pausa por cero pedidos ESPERA. No cobra
  cuota, no entra a applying y no reinicia el veto de 48 horas.
- Al vencer se reevalua: si el ancla ya esta dentro de la ventana madura, la
  pausa procede. Un pedido nuevo solo abre otro episodio si el ancla anterior
  ya salio de esa ventana (no renueva una gracia vigente).
- Fuente incompleta o metrica venenosa = desconocido: se espera y no se abre
  gracia nueva. Nunca se convierte en cero.
- La pausa economica conserva precedencia: la evalua `_decide_pause` antes de
  consultar esta subpolitica.
- Sin marcador en los insumos (decision historica) no hay proteccion: la
  semantica antigua se conserva por construccion.
"""

from __future__ import annotations

import datetime as dt

from app.optimizer.caso import (
    DIAS_PROTECCION_VENTA,
    VERSION_PROTECCION_VENTA,
    InsumosProteccion,
    ResultadoProteccion,
)

MOTIVO_PROTEGIDA = "venta_reciente_protegida"
MOTIVO_INDETERMINADA = "venta_reciente_indeterminada"
MOTIVO_CONSUMIDA = "proteccion_consumida"
MOTIVO_SIN_VENTA = "sin_venta_reciente"

CLAVE_ACTIVA = "ads_proteccion_venta"


def activa_desde_settings(settings) -> bool:
    """Interruptor fail-closed de la regla M1 (reversa de seleccion de politica).

    Ausente -> False: el ciclo y la cola conservan la semantica historica y no
    leen la tabla de episodios. `proteccion_venta_v1` -> True. Cualquier otro
    valor -> ValueError (config corrupta, ciclo failed), como el resto de los
    settings del motor. Apagar la regla no borra episodios ni reescribe
    decisiones: el codigo nuevo sigue leyendo ambos historiales.
    """
    valor = settings.get(CLAVE_ACTIVA)
    if valor is None:
        return False
    if valor == VERSION_PROTECCION_VENTA:
        return True
    raise ValueError(
        f"setting {CLAVE_ACTIVA}: debe ser {VERSION_PROTECCION_VENTA!r}, llego {valor!r}"
    )


def evalua(insumos: InsumosProteccion) -> ResultadoProteccion:
    """Aplica, espera o se abstiene. Pura: sin IO, reloj ni float."""
    if insumos.version != VERSION_PROTECCION_VENTA:
        raise ValueError(f"version de proteccion desconocida: {insumos.version!r}")
    lectura = insumos.reciente
    episodio = insumos.episodio

    if episodio is not None:
        if insumos.evaluado_el < episodio.vence_el:
            return _espera(episodio.ancla, episodio.vence_el, MOTIVO_PROTEGIDA)
        # Vencido: el ancla debe estar ya en la ventana madura. Sin esa
        # madurez la fuente es desconocida y no se libera la pausa.
        if insumos.ventana_hasta is None or insumos.ventana_hasta < episodio.ancla:
            return _indeterminada(episodio.ancla, episodio.vence_el)
        if _abre_otro(insumos, episodio):
            assert lectura.ancla is not None
            nuevo = _vence(lectura.ancla)
            return _espera(lectura.ancla, nuevo, MOTIVO_PROTEGIDA, abre=True)
        return _aplica(MOTIVO_CONSUMIDA, episodio.ancla, episodio.vence_el)

    if lectura.ancla is not None:
        # Un pedido visto es conocido aunque falte un dia o la fuente no cubra
        # todo el rango: manda sobre la ausencia de dato (A-H1/A-H2).
        return _espera(lectura.ancla, _vence(lectura.ancla), MOTIVO_PROTEGIDA, abre=True)
    if not lectura.cubierta or lectura.pedidos is None:
        # Sin pedido conocido, una fuente incompleta no acredita una venta
        # reciente: no se inventa un cero ni se abre una gracia nueva, y la
        # pausa sigue su curso historico. Un episodio ACTIVO si se respeta
        # con la lectura incompleta (el bloque de arriba).
        return _aplica(MOTIVO_INDETERMINADA, None, None)
    if lectura.pedidos == 0:
        return _aplica(MOTIVO_SIN_VENTA, None, None)
    if lectura.ancla is None:
        return _indeterminada(None, None)
    return _espera(lectura.ancla, _vence(lectura.ancla), MOTIVO_PROTEGIDA, abre=True)


def _abre_otro(insumos: InsumosProteccion, episodio) -> bool:
    """Un pedido posterior abre otro episodio solo cuando el ancla vieja ya
    salio de la ventana madura. Evita renovar la gracia con pedidos sucesivos.

    Basta el ancla: un pedido visto es conocido aunque la fuente no cubra todo
    el rango o un dia venga envenenado."""
    lectura = insumos.reciente
    if insumos.ventana_desde is None or lectura.ancla is None:
        return False
    return lectura.ancla > episodio.vence_el and episodio.ancla < insumos.ventana_desde


def _vence(ancla: dt.date) -> dt.date:
    return ancla + dt.timedelta(days=DIAS_PROTECCION_VENTA)


def _espera(
    ancla: dt.date, vence_el: dt.date, motivo: str, *, abre: bool = False
) -> ResultadoProteccion:
    return ResultadoProteccion(
        version=VERSION_PROTECCION_VENTA,
        accion="esperar",
        motivo=motivo,
        ancla=ancla,
        vence_el=vence_el,
        abre_episodio=abre,
    )


def _indeterminada(ancla: dt.date | None, vence_el: dt.date | None) -> ResultadoProteccion:
    return ResultadoProteccion(
        version=VERSION_PROTECCION_VENTA,
        accion="indeterminada",
        motivo=MOTIVO_INDETERMINADA,
        ancla=ancla,
        vence_el=vence_el,
        abre_episodio=False,
    )


def _aplica(motivo: str, ancla: dt.date | None, vence_el: dt.date | None) -> ResultadoProteccion:
    return ResultadoProteccion(
        version=VERSION_PROTECCION_VENTA,
        accion="aplicar",
        motivo=motivo,
        ancla=ancla,
        vence_el=vence_el,
        abre_episodio=False,
    )
