"""Aviso de Telegram de una propuesta en veto (JEV ADS 02, S.8).

Vive fuera de `jev_senales` por el presupuesto de 900 lineas. El job lo
llama al cerrar la corrida, solo si `jev.avisos` es true.
"""

from __future__ import annotations

from uuid import uuid4

from app.jev_lectura import Ajustes, PropuestaEnVeto, SenalPropuesta


def _nombres_grupo(lector, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    marcas = ",".join(["%s"] * len(ids))
    filas = lector.execute(
        f"SELECT id, name FROM ad_entity WHERE id IN ({marcas})", tuple(ids)
    ).fetchall()
    return {fila[0]: fila[1] for fila in filas if fila[1]}


def _armado_aviso(propuesta: PropuestaEnVeto, vistas: dict):
    """(lectura, origen_id, destino_id, SenalPropuesta) o None sin senal de origen.

    Harvest anuncia la lectura del destino. Sin destino legible, `sin_lectura`.
    """
    origen = vistas.get(propuesta.origen)
    if origen is None:
        return None
    destino = vistas.get(propuesta.destino) if propuesta.destino is not None else None
    if propuesta.kind == "harvest" and (propuesta.destino_ilegible or destino is None):
        lectura, destino_id, destino = "sin_lectura", None, None
    elif propuesta.kind == "harvest" and destino is not None:
        lectura, destino_id = destino.lectura, destino.senal_id
    else:
        lectura, destino_id, destino = origen.lectura, None, None
    return (
        lectura,
        origen.senal_id,
        destino_id,
        SenalPropuesta(origen, destino, propuesta.destino_ilegible),
    )


def _enviar_avisos(escritor, lote, propuestas, vistas, nombres, avisar) -> int:
    """Fila en jev_aviso (autocommit) antes de enviar. Reintento: el texto guardado."""
    from app.jev_vista import texto_aviso

    enviados = 0
    for propuesta in propuestas:
        armado = _armado_aviso(propuesta, vistas)
        if armado is None:
            continue
        lectura, origen_id, destino_id, senal = armado
        fila = escritor.execute(
            "SELECT a.id, a.texto, (e.aviso_id IS NOT NULL)"
            " FROM jev_aviso a LEFT JOIN jev_aviso_entrega e ON e.aviso_id = a.id"
            " WHERE a.cola_id = %s AND a.lectura = %s",
            (propuesta.cola_id, lectura),
        ).fetchone()
        if fila is not None and fila[2]:
            continue
        if fila is None:
            texto = texto_aviso(propuesta, senal, nombres)
            nuevo = escritor.execute(
                "INSERT INTO jev_aviso (id, lote_id, cola_id, decision_id,"
                " senal_origen_id, senal_destino_id, lectura, texto)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (cola_id, lectura) DO NOTHING RETURNING id, texto",
                (
                    uuid4(),
                    lote,
                    propuesta.cola_id,
                    propuesta.decision_id,
                    origen_id,
                    destino_id,
                    lectura,
                    texto,
                ),
            ).fetchone()
            if nuevo is None:
                nuevo = escritor.execute(
                    "SELECT id, texto FROM jev_aviso WHERE cola_id = %s AND lectura = %s",
                    (propuesta.cola_id, lectura),
                ).fetchone()
            aviso_id, texto_guardado = nuevo[0], nuevo[1]
        else:
            aviso_id, texto_guardado = fila[0], fila[1]
        if avisar(texto_guardado):
            escritor.execute(
                "INSERT INTO jev_aviso_entrega (aviso_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (aviso_id,),
            )
            enviados += 1
    return enviados


def _avisar(lector, escritor, lote, propuestas, avisar) -> int:
    from app.jev_salud import _vistas_de_claves

    claves: set = set()
    for propuesta in propuestas:
        claves.add(propuesta.origen)
        if propuesta.destino is not None:
            claves.add(propuesta.destino)
    vistas = _vistas_de_claves(lector, claves)
    return _enviar_avisos(
        escritor,
        lote,
        propuestas,
        vistas,
        _nombres_grupo(lector, {clave.ad_group_id for clave in claves}),
        avisar,
    )


def _avisos_de(ajustes: Ajustes, avisar, lector, escritor, lote, propuestas) -> int:
    if not ajustes.avisos or avisar is None:
        return 0
    return _avisar(lector, escritor, lote, propuestas, avisar)
