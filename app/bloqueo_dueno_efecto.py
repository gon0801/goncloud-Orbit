"""Efecto del bloqueo que el dueno aprueba: ledger, Amazon y reconciliacion.

El candado es de sesion y se sostiene hasta el readback. Un candado de
transaccion se soltaria en el commit de la intencion y el segundo clic
trataria esa intencion como huerfana.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from app import apply
from app.ads.client import AdsAuthError
from app.ads.write import AdsApiError
from app.apply_cola import _SQL_EXTERNALES_GRUPO
from app.apply_harvest import _identidad, _lista_filtrada
from app.bloqueo_dueno import (
    PASOS_VIVOS,
    FichaCatalogo,
    FilaMotor,
    catalogo_de_miembros,
    huella_catalogo,
    interruptor_encendido,
    lee_pausas,
    motor_oculta,
)
from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

_PASOS_HUERFANOS = ("intencion", "reversa_intencion")
_PASOS_QUITAR = ("aplicado", "reversa_fallida")


@dataclass(frozen=True)
class Efecto:
    status: int
    veredicto_id: UUID | None = None
    paso: str | None = None
    motivo: str | None = None


@dataclass(frozen=True)
class _Senal:
    id: UUID
    plataforma: str
    ad_group_id: int
    termino: str
    termino_sha256: str
    roster_sha256: str
    huella: str | None = None


def aprobar(conn, senal_id: UUID, *, transport=None) -> Efecto:
    senal = _senal(conn, senal_id)
    if senal is None:
        return Efecto(409, motivo="senal_ausente")
    if not _toma(conn, senal.ad_group_id, senal.termino_sha256):
        return Efecto(409, motivo="candado")
    try:
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
) -> Efecto:
    del negative_keyword_id_body
    origen = _aprobado(conn, veredicto_id)
    if origen is None:
        return Efecto(409, motivo="no_vive")
    if not _toma(conn, origen.ad_group_id, origen.termino_sha256):
        return Efecto(409, motivo="candado")
    try:
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
    vivo = _identidad(items, grupo_ext, senal.termino)
    if vivo is not None:
        kid = str(vivo["keywordId"])
        _sella(conn, vid, "aplicado", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=vid, paso="aplicado")
    try:
        cliente.crear_negative_exacto(grupo_ext, campana_ext, senal.termino)
    except AdsApiError as exc:
        if exc.status is not None and 400 <= exc.status < 500:
            motivo, status, cuerpo = _fallo_de(exc)
            _sella(conn, vid, "fallido", motivo=motivo, http_status=status, respuesta=cuerpo)
            return Efecto(200, veredicto_id=vid, paso="fallido", motivo=motivo)
        _anota_incierto(vid, exc)
    return _readback(conn, cliente, senal, vid, grupo_ext)


def _readback(conn, cliente, senal: _Senal, vid: UUID, grupo_ext: str) -> Efecto:
    items, estado = _lista_o(cliente, grupo_ext)
    item = _identidad(items, grupo_ext, senal.termino) if estado == "ok" else None
    if item is not None and str(item.get("state", "")).upper() == "ENABLED":
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
        cliente.borrar_negative(kid)
        return _readback_reversa(conn, cliente, origen, kid, externos[0])
    except Exception as exc:
        motivo, status, cuerpo = _fallo_de(exc)
        _sella(
            conn,
            origen.id,
            "reversa_fallida",
            motivo=motivo,
            http_status=status,
            respuesta=cuerpo,
        )
        return Efecto(200, veredicto_id=vid, paso="reversa_fallida", motivo=motivo)


def _readback_reversa(conn, cliente, origen: _Senal, kid: str, grupo_ext: str) -> Efecto:
    items, estado = _lista_o(cliente, grupo_ext)
    if estado != "ok":
        motivo = f"lista_{estado}"
        _sella(conn, origen.id, "reversa_fallida", motivo=motivo)
        return Efecto(200, veredicto_id=origen.id, paso="reversa_fallida", motivo=motivo)
    item = _por_id(items, kid)
    if item is None or str(item.get("state", "")).upper() == "ARCHIVED":
        _sella(conn, origen.id, "revertido", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=origen.id, paso="revertido")
    _sella(conn, origen.id, "reversa_fallida", motivo="sigue_presente")
    return Efecto(200, veredicto_id=origen.id, paso="reversa_fallida", motivo="sigue_presente")


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
    for vid, paso, kid, _plataforma, termino in huerfanos:
        ultimo = _cierra_huerfano(conn, vid, paso, kid, termino, items, externos[0])
    return ultimo


def _cierra_huerfano(conn, vid, paso, kid, termino, items, grupo_ext) -> Efecto:
    if paso == "intencion":
        item = _identidad(items, grupo_ext, termino)
        if item is not None and str(item.get("state", "")).upper() == "ENABLED":
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


def _anula(conn, senal: _Senal, motivo: str) -> Efecto:
    huella = _huella(conn, senal.roster_sha256)
    if huella is None:
        return Efecto(409, motivo="catalogo")
    vid = uuid4()
    with _durable(conn):
        _insert_veredicto(conn, vid, "aprobado", senal, huella)
        _insert_efecto(conn, vid, "anulado", motivo=motivo)
    return Efecto(200, veredicto_id=vid, paso="anulado", motivo=motivo)


def _abre_intencion(conn, senal: _Senal, huella: str) -> UUID:
    vid = uuid4()
    with _durable(conn):
        _insert_veredicto(conn, vid, "aprobado", senal, huella)
        _insert_efecto(conn, vid, "intencion")
    return vid


def _sella(conn, vid: UUID, paso: str, **campos) -> None:
    with _durable(conn):
        _insert_efecto(conn, vid, paso, **campos)


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
        return (f"http_{status}", int(status), str(cuerpo)[:500])
    return ("sin_respuesta", None, scrub(str(exc))[:500])


def _lista_o(cliente, grupo_ext: str) -> tuple[list, str]:
    try:
        return _lista_filtrada(cliente, [grupo_ext])
    except (AdsApiError, AdsAuthError):
        # AdsAuthError no hereda de AdsApiError. Si el refresh cae en el
        # LIST de confirmacion, dejarlo subir sella fallido un POST ya enviado.
        return ([], "ambiguo")


def _cliente(plataforma: str, transport):
    return apply._cliente_ajuste(plataforma, transport=transport)


def _interruptor(conn) -> bool:
    return interruptor_encendido(conn)


def _pausada(conn, plataforma: str) -> bool:
    pausa = lee_pausas(conn).get(plataforma)
    return pausa is not None and pausa.pausada


def _senal(conn, senal_id: UUID) -> _Senal | None:
    fila = conn.execute(
        """
        SELECT id, plataforma::text, ad_group_id, termino, termino_sha256, roster_sha256
          FROM jev_senal
         WHERE id = %s
        """,
        (senal_id,),
    ).fetchone()
    if fila is None or fila[2] is None or not fila[3] or not fila[4] or not fila[5]:
        return None
    return _Senal(fila[0], fila[1], fila[2], fila[3], fila[4], fila[5])


def _aprobado(conn, veredicto_id: UUID) -> _Senal | None:
    fila = conn.execute(
        """
        SELECT id, plataforma::text, ad_group_id, termino, termino_sha256,
               huella_catalogo, senal_id
          FROM bloqueo_veredicto
         WHERE id = %s AND tipo = 'aprobado'
        """,
        (veredicto_id,),
    ).fetchone()
    if fila is None or fila[2] is None or not fila[4]:
        return None
    senal = _senal(conn, fila[6]) if fila[6] is not None else None
    roster = senal.roster_sha256 if senal is not None else ""
    return _Senal(fila[0], fila[1], fila[2], fila[3] or "", fila[4], roster, fila[5])


def _vigente_ajena(conn, senal: _Senal) -> bool:
    fila = conn.execute(
        """
        SELECT v.senal_id, v.vigente, v.lectura
          FROM jev_senal_vigente v
          JOIN jev_senal s ON s.id = v.senal_id
         WHERE v.ad_group_id = %s AND s.termino_sha256 = %s
        """,
        (senal.ad_group_id, senal.termino_sha256),
    ).fetchone()
    return fila is not None and fila[0] == senal.id and fila[1] is True and fila[2] == "ajena"


def _vivo(conn, senal: _Senal) -> bool:
    fila = conn.execute(
        """
        SELECT 1
          FROM bloqueo_veredicto v
          JOIN LATERAL (
                SELECT e.paso
                  FROM bloqueo_efecto e
                 WHERE e.veredicto_id = v.id
                 ORDER BY e.created_at DESC, e.id DESC
                 LIMIT 1
               ) ult ON true
         WHERE v.tipo = 'aprobado'
           AND v.plataforma = %s
           AND v.ad_group_id = %s
           AND v.termino_sha256 = %s
           AND ult.paso = ANY(%s)
        """,
        (senal.plataforma, senal.ad_group_id, senal.termino_sha256, list(PASOS_VIVOS)),
    ).fetchone()
    return fila is not None


def _motor(conn, senal: _Senal, ahora) -> bool:
    filas = tuple(
        FilaMotor(estado, vence, reversa)
        for estado, vence, reversa in conn.execute(
            """
            SELECT q.estado, q.vence_el,
                   EXISTS (
                       SELECT 1
                         FROM apply_attempt t
                        WHERE t.decision_id = q.decision_id
                          AND t.tipo = 'reversa'
                          AND t.resultado = 'ok'
                   )
              FROM apply_queue q
             WHERE q.familia = 'term_cut'
               AND q.platform = %s
               AND q.ad_entity_id = %s
               AND q.search_term = %s
            """,
            (senal.plataforma, senal.ad_group_id, senal.termino),
        )
    )
    return motor_oculta(filas, ahora)


def _huella(conn, roster_sha: str) -> str | None:
    fila = conn.execute(
        "SELECT miembros FROM jev_roster WHERE sha256 = %s", (roster_sha,)
    ).fetchone()
    if fila is None:
        return None
    miembros = fila[0]
    if isinstance(miembros, str):
        miembros = json.loads(miembros)
    ids = [str(item) for item in miembros.get("fichas", []) if item]
    fichas: dict[str, FichaCatalogo] = {}
    if ids:
        for fid, hechos, desconocidos in conn.execute(
            """
            SELECT id::text, hechos, desconocidos
              FROM jev_ficha_version
             WHERE id = ANY(%s::uuid[])
            """,
            (ids,),
        ):
            hechos_t = tuple(hechos) if not isinstance(hechos, str) else tuple(json.loads(hechos))
            desc = tuple(desconocidos or ())
            fichas[fid] = FichaCatalogo(hechos_t, desc, fid)
    catalogo = catalogo_de_miembros(miembros, fichas)
    if catalogo is None:
        return None
    return huella_catalogo(catalogo)


def _externos(conn, ad_group_id: int) -> tuple[str, str] | None:
    fila = conn.execute(_SQL_EXTERNALES_GRUPO, (ad_group_id,)).fetchone()
    if fila is None or fila[0] is None or fila[1] is None:
        return None
    return (str(fila[0]), str(fila[1]))


def _huerfanos(conn, ad_group_id: int, termino_sha256: str) -> list[tuple]:
    return conn.execute(
        """
        SELECT v.id, ult.paso, ult.negative_keyword_id, v.plataforma::text, v.termino
          FROM bloqueo_veredicto v
          JOIN LATERAL (
                SELECT e.paso, e.negative_keyword_id
                  FROM bloqueo_efecto e
                 WHERE e.veredicto_id = v.id
                 ORDER BY e.created_at DESC, e.id DESC
                 LIMIT 1
               ) ult ON true
         WHERE v.tipo = 'aprobado'
           AND v.ad_group_id = %s
           AND v.termino_sha256 = %s
           AND ult.paso = ANY(%s)
        """,
        (ad_group_id, termino_sha256, list(_PASOS_HUERFANOS)),
    ).fetchall()


def _claves_huerfanas(conn) -> list[tuple[int, str]]:
    return conn.execute(
        """
        SELECT DISTINCT v.ad_group_id, v.termino_sha256
          FROM bloqueo_veredicto v
          JOIN LATERAL (
                SELECT e.paso
                  FROM bloqueo_efecto e
                 WHERE e.veredicto_id = v.id
                 ORDER BY e.created_at DESC, e.id DESC
                 LIMIT 1
               ) ult ON true
         WHERE v.tipo = 'aprobado'
           AND ult.paso = ANY(%s)
           AND v.ad_group_id IS NOT NULL
           AND v.termino_sha256 IS NOT NULL
        """,
        (list(_PASOS_HUERFANOS),),
    ).fetchall()


def _ultimo(conn, veredicto_id: UUID) -> tuple | None:
    return conn.execute(
        """
        SELECT paso, negative_keyword_id
          FROM bloqueo_efecto
         WHERE veredicto_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (veredicto_id,),
    ).fetchone()


def _id_del_ledger(conn, veredicto_id: UUID) -> str | None:
    fila = conn.execute(
        """
        SELECT negative_keyword_id
          FROM bloqueo_efecto
         WHERE veredicto_id = %s
           AND negative_keyword_id IS NOT NULL
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (veredicto_id,),
    ).fetchone()
    if fila is None:
        return None
    return str(fila[0])


def _por_id(items: list, kid: str) -> dict | None:
    for item in items:
        if str(item.get("keywordId")) == str(kid):
            return item
    return None


def _insert_veredicto(
    conn, vid, tipo, senal: _Senal, huella: str | None, *, origen_id=None
) -> None:
    conn.execute(
        """
        INSERT INTO bloqueo_veredicto (
            id, plataforma, ad_group_id, termino, termino_sha256,
            senal_id, huella_catalogo, origen_id, tipo
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            vid,
            senal.plataforma,
            senal.ad_group_id,
            senal.termino,
            senal.termino_sha256,
            senal.id if tipo != "quitado" else None,
            huella,
            origen_id,
            tipo,
        ),
    )


def _insert_efecto(conn, veredicto_id, paso: str, **campos) -> None:
    conn.execute(
        """
        INSERT INTO bloqueo_efecto (
            id, veredicto_id, paso, negative_keyword_id, http_status, respuesta, motivo
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid4(),
            veredicto_id,
            paso,
            campos.get("negative_keyword_id"),
            campos.get("http_status"),
            campos.get("respuesta"),
            campos.get("motivo"),
        ),
    )


@contextmanager
def _durable(conn):
    # Con conexion sin autocommit, conn.transaction() dentro de una transaccion
    # abierta es un SAVEPOINT: sin este commit el ledger no es visible antes del HTTP.
    with conn.transaction():
        yield
    if not conn.autocommit:
        conn.commit()


def _llave(ad_group_id: int, termino_sha256: str) -> str:
    return f"bloqueo:{ad_group_id}:{termino_sha256}"


def _toma(conn, ad_group_id: int, termino_sha256: str) -> bool:
    fila = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s))",
        (_llave(ad_group_id, termino_sha256),),
    ).fetchone()
    if not conn.autocommit:
        conn.commit()
    return bool(fila[0])


def _suelta(conn, ad_group_id: int, termino_sha256: str) -> None:
    try:
        conn.execute(
            "SELECT pg_advisory_unlock(hashtext(%s))",
            (_llave(ad_group_id, termino_sha256),),
        )
        if not conn.autocommit:
            conn.commit()
    except Exception:
        if not conn.autocommit:
            conn.rollback()
