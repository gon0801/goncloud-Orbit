"""Efecto del bloqueo que el dueno aprueba: ledger, Amazon y reconciliacion.

El candado es de sesion y se sostiene hasta el readback. Un candado de
transaccion se soltaria en el commit de la intencion y el segundo clic
trataria esa intencion como huerfana.

Persistencia, consultas y candado viven en `app/bloqueo_dueno_store.py` (Ola D3a).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from uuid import UUID, uuid4

from app import apply
from app.ads.client import AdsAuthError
from app.ads.sp_wire import errores_de_ack as _errores_de_ack
from app.ads.sp_wire import id_de_ack as _id_de_ack
from app.ads.write import AdsApiError
from app.apply_harvest import (
    ListaIncompleta,
    _coincidencias,
    _lista_completa,
)
from app.bloqueo_dueno import (  # noqa: F401 - re-export estable
    PASOS_VIVOS,
    interruptor_encendido,
    lee_pausas,
)
from app.bloqueo_dueno_store import (  # noqa: F401 - re-export estable
    _PASOS_HUERFANOS,
    _PASOS_QUITAR,
    ESTADOS_REPARACION_QUITAR,
    Efecto,
    PrecondicionAprobar,
    PrecondicionQuitar,
    _abre_intencion,
    _anula,
    _aprobado,
    _claves_huerfanas,
    _externos,
    _huella,
    _huerfanos,
    _id_del_ledger,
    _insert_efecto,
    _insert_veredicto,
    _interruptor,
    _llave,
    _motor,
    _pausada,
    _por_id,
    _sella,
    _Senal,
    _senal,
    _suelta,
    _toma,
    _ultimo,
    _vigente_ajena,
    _vivo,
    _vivos_distintos,
)
from app.db import connect
from app.db import tx_durable as _durable
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


def aprobar(
    conn,
    senal_id: UUID,
    *,
    transport=None,
    precondicion: PrecondicionAprobar | None = None,
) -> Efecto:
    senal = _senal(conn, senal_id)
    if senal is None:
        return Efecto(409, motivo="senal_ausente")
    if not _toma(conn, senal.ad_group_id, senal.termino_sha256):
        return Efecto(409, motivo="candado")
    try:
        if precondicion is not None:
            return _aprobar_reparacion_con_candado(conn, senal, precondicion, transport)
        return _aprobar_con_candado(conn, senal, transport)
    finally:
        _suelta(conn, senal.ad_group_id, senal.termino_sha256)


def rechazar(conn, senal_id: UUID) -> Efecto:
    if not _interruptor(conn):
        return Efecto(409, motivo="interruptor")
    senal = _senal(conn, senal_id)
    if senal is None:
        return Efecto(409, motivo="senal_ausente")
    if _pausada(conn, senal.plataforma):
        return Efecto(409, motivo="pausa")
    if not _vigente_ajena(conn, senal):
        return Efecto(409, motivo="senal_cambio")
    if not _toma(conn, senal.ad_group_id, senal.termino_sha256):
        return Efecto(409, motivo="candado")
    try:
        return _rechaza_con_candado(conn, senal)
    finally:
        _suelta(conn, senal.ad_group_id, senal.termino_sha256)


def _rechaza_con_candado(conn, senal: _Senal) -> Efecto:
    previo = conn.execute(
        "SELECT id FROM bloqueo_veredicto WHERE tipo = 'rechazado' AND senal_id = %s",
        (senal.id,),
    ).fetchone()
    if previo is not None:
        return Efecto(200, veredicto_id=previo[0], paso="rechazado", motivo="ya_rechazado")
    huella = _huella(conn, senal.roster_sha256)
    if huella is None:
        return Efecto(409, motivo="catalogo")
    vid = uuid4()
    with _durable(conn):
        _insert_veredicto(conn, vid, "rechazado", senal, huella)
    return Efecto(200, veredicto_id=vid, paso="rechazado")


def quitar(
    conn,
    veredicto_id: UUID,
    *,
    transport=None,
    negative_keyword_id_body: str | None = None,
    precondicion: PrecondicionQuitar | None = None,
) -> Efecto:
    del negative_keyword_id_body
    origen = _aprobado(conn, veredicto_id)
    if origen is None:
        return Efecto(409, motivo="no_vive")
    if not _toma(conn, origen.ad_group_id, origen.termino_sha256):
        return Efecto(409, motivo="candado")
    try:
        if precondicion is not None:
            return _quitar_reparacion_con_candado(conn, origen, precondicion, transport)
        return _quitar_con_candado(conn, origen, transport)
    finally:
        _suelta(conn, origen.ad_group_id, origen.termino_sha256)


def reanudar(conn, plataforma: str) -> Efecto:
    if not _interruptor(conn):
        return Efecto(409, motivo="interruptor")
    vid = uuid4()
    with _durable(conn):
        conn.execute(
            """
            INSERT INTO bloqueo_veredicto (id, plataforma, tipo)
            VALUES (%s, %s::platform, 'reanudacion')
            """,
            (vid, plataforma),
        )
    return Efecto(200, veredicto_id=vid, paso="reanudacion")


def reconciliar(conn, *, ad_group_id: int, termino_sha256: str, transport=None) -> Efecto:
    if not _toma(conn, ad_group_id, termino_sha256):
        return Efecto(409, motivo="candado")
    try:
        return _reconcilia_sin_candado(conn, ad_group_id, termino_sha256, transport)
    finally:
        _suelta(conn, ad_group_id, termino_sha256)


def reconcilia_todas(conn, *, transport=None) -> int:
    saltadas = 0
    for grupo, sha in _claves_huerfanas(conn):
        resultado = reconciliar(conn, ad_group_id=grupo, termino_sha256=sha, transport=transport)
        if resultado.motivo == "candado":
            saltadas += 1
    return saltadas


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(f"argumentos desconocidos para 'bloqueo-reconcilia': {argv}", file=sys.stderr)
        return 2
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print(
            "ORBIT_DSN_ADMIN no esta definido: no se puede reconciliar el bloqueo (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        with connect(dsn) as conn:
            reconcilia_todas(conn)
    except Exception:
        print("bloqueo-reconcilia fallo", file=sys.stderr)
        return 1
    return 0


def _negative_que_bloquea(items: list[dict], grupo_ext: str, termino: str) -> dict | None:
    """Solo un negative ENABLED bloquea. PAUSED no niega nada y ARCHIVED esta muerto."""
    for item in _coincidencias(items, grupo_ext, termino):
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ENABLED:
            return item
    return None


def _negative_por_id(items: list[dict], grupo_ext: str, termino: str, kid: str) -> dict | None:
    """El ENABLED con ese id exacto. Un ENABLED ajeno del mismo termino no sirve."""
    for item in _coincidencias(items, grupo_ext, termino):
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ENABLED and str(
            item.get("keywordId")
        ) == str(kid):
            return item
    return None


def _rechazo(resp) -> str | None:
    """El rechazo por item del 207, saneado y corto. Devuelve None si Amazon no rechazo nada."""
    errores = _errores_de_ack(apply._json_seguro(resp))
    if not errores:
        return None
    return scrub(json.dumps(errores, ensure_ascii=False))[:500]


def _aprobar_con_candado(conn, senal: _Senal, transport) -> Efecto:
    if not _interruptor(conn):
        return Efecto(409, motivo="interruptor")
    if _pausada(conn, senal.plataforma):
        return Efecto(409, motivo="pausa")
    previo = _reconcilia_sin_candado(conn, senal.ad_group_id, senal.termino_sha256, transport)
    if previo.motivo == "pendiente":
        return Efecto(409, motivo="reconciliacion_pendiente")
    if not _vigente_ajena(conn, senal):
        return _anula(conn, senal, "senal_cambio")
    if _vivo(conn, senal):
        return _anula(conn, senal, "ya_bloqueada")
    ahora = conn.execute("SELECT now()").fetchone()[0]
    if _motor(conn, senal, ahora):
        return _anula(conn, senal, "motor")
    huella = _huella(conn, senal.roster_sha256)
    if huella is None:
        return Efecto(409, motivo="catalogo")
    vid = _abre_intencion(conn, senal, huella)
    try:
        return _ejecuta(conn, senal, vid, transport)
    except Exception as exc:
        motivo, status, cuerpo = _fallo_de(exc)
        _sella(conn, vid, "fallido", motivo=motivo, http_status=status, respuesta=cuerpo)
        return Efecto(200, veredicto_id=vid, paso="fallido", motivo=motivo)


def _ejecuta(conn, senal: _Senal, vid: UUID, transport) -> Efecto:
    externos = _externos(conn, senal.ad_group_id)
    if externos is None:
        _sella(conn, vid, "fallido", motivo="sin_externos")
        return Efecto(200, veredicto_id=vid, paso="fallido", motivo="sin_externos")
    grupo_ext, campana_ext = externos
    cliente = _cliente(senal.plataforma, transport)
    items, estado = _lista_o(cliente, grupo_ext)
    if estado != "ok":
        motivo = f"lista_{estado}"
        _sella(conn, vid, "fallido", motivo=motivo)
        return Efecto(200, veredicto_id=vid, paso="fallido", motivo=motivo)
    vivo = _negative_que_bloquea(items, grupo_ext, senal.termino)
    if vivo is not None:
        kid = str(vivo["keywordId"])
        _sella(conn, vid, "aplicado", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=vid, paso="aplicado")
    return _postea_y_relee(conn, cliente, senal, vid, grupo_ext, campana_ext, exigir_id=False)


def _postea_y_relee(
    conn,
    cliente,
    senal: _Senal,
    vid: UUID,
    grupo_ext: str,
    campana_ext: str,
    *,
    exigir_id: bool,
) -> Efecto:
    id_creado = None
    try:
        resp = cliente.crear_negative_exacto(grupo_ext, campana_ext, senal.termino)
    except AdsApiError as exc:
        if exc.status is not None and 400 <= exc.status < 500:
            motivo, status, cuerpo = _fallo_de(exc)
            _sella(conn, vid, "fallido", motivo=motivo, http_status=status, respuesta=cuerpo)
            return Efecto(200, veredicto_id=vid, paso="fallido", motivo=motivo)
        _anota_incierto(vid, exc)
    else:
        rechazo = _rechazo(resp)
        if rechazo is not None:
            _sella(
                conn,
                vid,
                "fallido",
                motivo="rechazo_amazon",
                http_status=resp.status_code,
                respuesta=rechazo,
            )
            return Efecto(200, veredicto_id=vid, paso="fallido", motivo="rechazo_amazon")
        id_creado = _id_de_ack(apply._json_seguro(resp), "negativeKeywordId")
    if exigir_id:
        _sella(
            conn,
            vid,
            "intencion",
            motivo="reparacion",
            negative_keyword_id=id_creado,
        )
    return _readback(
        conn,
        cliente,
        senal,
        vid,
        grupo_ext,
        id_esperado=id_creado,
        exigir_id=exigir_id,
    )


def _readback(
    conn,
    cliente,
    senal: _Senal,
    vid: UUID,
    grupo_ext: str,
    *,
    id_esperado: str | None = None,
    exigir_id: bool = False,
) -> Efecto:
    items, estado = _lista_o(cliente, grupo_ext)
    if estado != "ok":
        return Efecto(200, veredicto_id=vid, paso="intencion", motivo="por_confirmar")
    if exigir_id:
        if id_esperado is None:
            return Efecto(200, veredicto_id=vid, paso="intencion", motivo="por_confirmar")
        item = _negative_por_id(items, grupo_ext, senal.termino, id_esperado)
    else:
        item = _negative_que_bloquea(items, grupo_ext, senal.termino)
    if item is not None:
        _sella(conn, vid, "aplicado", negative_keyword_id=str(item["keywordId"]))
        return Efecto(200, veredicto_id=vid, paso="aplicado")
    return Efecto(200, veredicto_id=vid, paso="intencion", motivo="por_confirmar")


def _quitar_con_candado(conn, origen: _Senal, transport) -> Efecto:
    previo = _reconcilia_sin_candado(conn, origen.ad_group_id, origen.termino_sha256, transport)
    if previo.motivo == "pendiente":
        return Efecto(409, motivo="reconciliacion_pendiente")
    ultimo = _ultimo(conn, origen.id)
    if ultimo is None or ultimo[0] not in _PASOS_QUITAR:
        return Efecto(409, motivo="no_vive")
    kid = _id_del_ledger(conn, origen.id)
    if kid is None:
        return Efecto(409, motivo="sin_id")
    externos = _externos(conn, origen.ad_group_id)
    if externos is None:
        return Efecto(409, motivo="sin_externos")
    vid = uuid4()
    with _durable(conn):
        _insert_veredicto(conn, vid, "quitado", origen, origen.huella, origen_id=origen.id)
        _insert_efecto(conn, origen.id, "reversa_intencion", negative_keyword_id=kid)
    try:
        cliente = _cliente(origen.plataforma, transport)
    except Exception as exc:
        return _sella_reversa_fallida(conn, origen.id, vid, exc)
    return _borra_y_relee_reversa(conn, cliente, origen, vid, kid, externos[0])


def _sella_reversa_fallida(conn, origen_id: UUID, vid_retorno: UUID, exc: Exception) -> Efecto:
    motivo, status, cuerpo = _fallo_de(exc)
    _sella(conn, origen_id, "reversa_fallida", motivo=motivo, http_status=status, respuesta=cuerpo)
    return Efecto(200, veredicto_id=vid_retorno, paso="reversa_fallida", motivo=motivo)


def _borra_y_relee_reversa(
    conn, cliente, origen: _Senal, vid_retorno: UUID, kid: str, grupo_ext: str
) -> Efecto:
    rechazo = None
    try:
        resp = cliente.borrar_negative(kid)
    except AdsApiError as exc:
        if exc.status is not None and 400 <= exc.status < 500:
            return _sella_reversa_fallida(conn, origen.id, vid_retorno, exc)
        _anota_incierto(vid_retorno, exc)
    except Exception as exc:
        return _sella_reversa_fallida(conn, origen.id, vid_retorno, exc)
    else:
        rechazo = _rechazo(resp)
    return _readback_reversa(conn, cliente, origen, kid, grupo_ext, rechazo=rechazo)


def _readback_reversa(
    conn, cliente, origen: _Senal, kid: str, grupo_ext: str, *, rechazo: str | None = None
) -> Efecto:
    items, estado = _lista_o(cliente, grupo_ext)
    item = _por_id(items, kid) if estado == "ok" else None
    if estado == "ok" and (item is None or str(item.get("state", "")).upper() == "ARCHIVED"):
        _sella(conn, origen.id, "revertido", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=origen.id, paso="revertido")
    if rechazo is not None and estado == "ok":
        _sella(conn, origen.id, "reversa_fallida", motivo="rechazo_amazon", respuesta=rechazo)
        return Efecto(200, veredicto_id=origen.id, paso="reversa_fallida", motivo="rechazo_amazon")
    return Efecto(200, veredicto_id=origen.id, paso="reversa_intencion", motivo="por_confirmar")


def _misma_clave(a: _Senal, b: _Senal) -> bool:
    return (a.plataforma, a.ad_group_id, a.termino, a.termino_sha256) == (
        b.plataforma,
        b.ad_group_id,
        b.termino,
        b.termino_sha256,
    )


def _concilia_clave(conn, sujeto: _Senal, transport) -> Efecto | None:
    try:
        previo = _reconcilia_sin_candado(conn, sujeto.ad_group_id, sujeto.termino_sha256, transport)
    except Exception:
        return Efecto(409, motivo="reconciliacion_pendiente")
    if previo.motivo == "pendiente":
        return Efecto(409, motivo="reconciliacion_pendiente")
    return None


def _lee_grupo_reparacion(conn, sujeto: _Senal, transport) -> tuple | Efecto:
    externos = _externos(conn, sujeto.ad_group_id)
    if externos is None:
        return Efecto(409, motivo="sin_externos")
    grupo_ext, campana_ext = externos
    try:
        cliente = _cliente(sujeto.plataforma, transport)
    except Exception:
        return Efecto(409, motivo="sin_respuesta")
    items, estado = _lista_o(cliente, grupo_ext)
    if estado != "ok":
        return Efecto(409, motivo=f"lista_{estado}")
    return (cliente, grupo_ext, campana_ext, items)


def _quitar_reparacion_con_candado(
    conn, origen: _Senal, pre: PrecondicionQuitar, transport
) -> Efecto:
    """Cierra el efecto del origen sin fila quitado para no votar rechazo en pausa_de."""
    if pre.estado_observado not in ESTADOS_REPARACION_QUITAR:
        return Efecto(409, motivo="estado_invalido")
    if bloqueo := _concilia_clave(conn, origen, transport):
        return bloqueo
    kid = _id_del_ledger(conn, origen.id)
    if kid is None:
        return Efecto(409, motivo="sin_id")
    if (origen.plataforma, origen.ad_group_id, origen.termino, kid) != (
        pre.plataforma,
        pre.ad_group_id,
        pre.termino,
        str(pre.negative_keyword_id),
    ):
        return Efecto(409, motivo="identidad_cambio")
    ultimo = _ultimo(conn, origen.id)
    if ultimo is not None and ultimo[0] == "revertido":
        return Efecto(200, veredicto_id=origen.id, paso="revertido", motivo="ya_revertido")
    if ultimo is None or ultimo[0] != "aplicado" or str(ultimo[1]) != kid:
        return Efecto(409, motivo="no_vive")
    if not _interruptor(conn):
        return Efecto(409, motivo="interruptor")
    if _pausada(conn, origen.plataforma):
        return Efecto(409, motivo="pausa")
    senal = _senal(conn, pre.senal_id)
    if senal is None or not _misma_clave(senal, origen) or not _vigente_ajena(conn, senal):
        return Efecto(409, motivo="senal_cambio")
    lectura = _lee_grupo_reparacion(conn, origen, transport)
    if isinstance(lectura, Efecto):
        return lectura
    cliente, grupo_ext, _campana_ext, items = lectura
    item = _por_id(items, kid)
    if item is not None and (
        str(item.get("adGroupId", "")) != str(grupo_ext)
        or item.get("keywordText") != origen.termino
        or str(item.get("matchType", "")).upper() != "NEGATIVE_EXACT"
    ):
        return Efecto(409, motivo="identidad_cambio")
    otro = _negative_que_bloquea(items, grupo_ext, origen.termino)
    if otro is not None and str(otro.get("keywordId")) != kid:
        return Efecto(409, motivo="otro_enabled")
    estado_actual = "AUSENTE" if item is None else str(item.get("state", "")).upper()
    if estado_actual != pre.estado_observado:
        return Efecto(409, motivo="estado_cambio")
    if estado_actual in ("ARCHIVED", "AUSENTE"):
        _sella(conn, origen.id, "revertido", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=origen.id, paso="revertido")
    _sella(conn, origen.id, "reversa_intencion", negative_keyword_id=kid)
    return _borra_y_relee_reversa(conn, cliente, origen, origen.id, kid, grupo_ext)


def _aprobar_reparacion_con_candado(
    conn, senal: _Senal, pre: PrecondicionAprobar, transport
) -> Efecto:
    if not _interruptor(conn):
        return Efecto(409, motivo="interruptor")
    if _pausada(conn, senal.plataforma):
        return Efecto(409, motivo="pausa")
    origen = _aprobado(conn, pre.origen_id)
    if origen is None:
        return Efecto(409, motivo="origen_ausente")
    if not _misma_clave(senal, origen) or (
        pre.plataforma,
        pre.ad_group_id,
        pre.termino,
    ) != (senal.plataforma, senal.ad_group_id, senal.termino):
        return Efecto(409, motivo="identidad_cambio")
    if bloqueo := _concilia_clave(conn, senal, transport):
        return bloqueo
    ult_origen = _ultimo(conn, origen.id)
    if ult_origen is None or ult_origen[0] != "revertido":
        return Efecto(409, motivo="origen_no_revertido")
    vivos = _vivos_distintos(conn, senal, origen.id)
    if vivos:
        return _verifica_reemplazo_existente(conn, senal, vivos, transport)
    if not _vigente_ajena(conn, senal):
        return Efecto(409, motivo="senal_cambio")
    if _motor(conn, senal, conn.execute("SELECT now()").fetchone()[0]):
        return Efecto(409, motivo="motor")
    huella = _huella(conn, senal.roster_sha256)
    if huella is None:
        return Efecto(409, motivo="catalogo")
    lectura = _lee_grupo_reparacion(conn, senal, transport)
    if isinstance(lectura, Efecto):
        return lectura
    cliente, grupo_ext, campana_ext, items = lectura
    if _negative_que_bloquea(items, grupo_ext, senal.termino) is not None:
        return Efecto(409, motivo="otro_enabled")
    vid = _abre_intencion(conn, senal, huella, motivo="reparacion")
    try:
        return _postea_y_relee(conn, cliente, senal, vid, grupo_ext, campana_ext, exigir_id=True)
    except Exception as exc:
        motivo, status, cuerpo = _fallo_de(exc)
        _sella(conn, vid, "fallido", motivo=motivo, http_status=status, respuesta=cuerpo)
        return Efecto(200, veredicto_id=vid, paso="fallido", motivo=motivo)


def _verifica_reemplazo_existente(conn, senal: _Senal, vivos: list[tuple], transport) -> Efecto:
    if len(vivos) != 1:
        return Efecto(409, motivo="reemplazo_ambiguo")
    vid, senal_id, paso, kid = vivos[0]
    if senal_id != senal.id:
        return Efecto(409, motivo="ya_bloqueada")
    if paso != "aplicado" or not kid:
        return Efecto(409, motivo="reemplazo_pendiente")
    lectura = _lee_grupo_reparacion(conn, senal, transport)
    if isinstance(lectura, Efecto):
        return lectura
    _cliente_ok, grupo_ext, _campana_ext, items = lectura
    confirmado = any(
        str(x.get("state", "")).upper() == apply.ESTADO_WIRE_ENABLED
        and str(x.get("keywordId")) == str(kid)
        for x in _coincidencias(items, grupo_ext, senal.termino)
    )
    if not confirmado:
        return Efecto(409, motivo="reemplazo_invalido")
    return Efecto(200, veredicto_id=vid, paso="aplicado", motivo="ya_aplicado")


def _reconcilia_sin_candado(conn, ad_group_id: int, termino_sha256: str, transport) -> Efecto:
    huerfanos = _huerfanos(conn, ad_group_id, termino_sha256)
    if not huerfanos:
        return Efecto(200, motivo="nada")
    externos = _externos(conn, ad_group_id)
    if externos is None:
        return Efecto(200, motivo="pendiente")
    cliente = _cliente(huerfanos[0][3], transport)
    items, estado = _lista_o(cliente, externos[0])
    if estado != "ok":
        return Efecto(200, motivo="pendiente")
    ultimo = Efecto(200, motivo="nada")
    for vid, paso, kid, _plataforma, termino, motivo in huerfanos:
        ultimo = _cierra_huerfano(conn, vid, paso, kid, termino, items, externos[0], motivo=motivo)
    return ultimo


def _cierra_huerfano(conn, vid, paso, kid, termino, items, grupo_ext, *, motivo=None) -> Efecto:
    if paso == "intencion":
        if motivo == "reparacion":
            item = _negative_por_id(items, grupo_ext, termino, kid) if kid else None
            if item is None:
                return Efecto(200, veredicto_id=vid, paso="intencion", motivo="por_confirmar")
        else:
            item = _negative_que_bloquea(items, grupo_ext, termino)
        if item is not None:
            _sella(conn, vid, "aplicado", negative_keyword_id=str(item["keywordId"]))
            return Efecto(200, veredicto_id=vid, paso="aplicado")
        _sella(conn, vid, "fallido", motivo="sin_resultado")
        return Efecto(200, veredicto_id=vid, paso="fallido", motivo="sin_resultado")
    item = _por_id(items, kid)
    if item is None or str(item.get("state", "")).upper() == "ARCHIVED":
        _sella(conn, vid, "revertido", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=vid, paso="revertido")
    _sella(conn, vid, "reversa_fallida", motivo="sigue_presente")
    return Efecto(200, veredicto_id=vid, paso="reversa_fallida", motivo="sigue_presente")


def _anota_incierto(vid: UUID, exc: Exception) -> None:
    status = getattr(exc, "status", None)
    cuerpo = getattr(exc, "cuerpo", None)
    if not cuerpo:
        cuerpo = str(exc)
    logger.warning(
        "post negative incierto veredicto=%s status=%s cuerpo=%s",
        vid,
        scrub("" if status is None else str(status)),
        scrub(str(cuerpo))[:500],
    )


def _fallo_de(exc: Exception) -> tuple[str, int | None, str]:
    status = getattr(exc, "status", None)
    if isinstance(exc, AdsApiError) and status is not None:
        cuerpo = getattr(exc, "cuerpo", None) or ""
        return (f"http_{status}", int(status), scrub(str(cuerpo))[:500])
    return ("sin_respuesta", None, scrub(str(exc))[:500])


def _lista_o(cliente, grupo_ext: str) -> tuple[list, str]:
    try:
        return (
            _lista_completa(cliente, "/sp/negativeKeywords/list", grupos=(str(grupo_ext),)),
            "ok",
        )
    except ListaIncompleta as exc:
        return ([], exc.causa)
    except (AdsApiError, AdsAuthError):
        return ([], "ambiguo")


def _cliente(plataforma: str, transport):
    return apply._cliente_ajuste(plataforma, transport=transport)
