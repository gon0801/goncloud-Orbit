"""Persistencia, consultas y candado de sesion de `bloqueo_dueno_efecto`.

Extraido de `app/bloqueo_dueno_efecto.py` (Ola D3a). Modulo hoja sin imports de
`app.ads.write` ni de `app.apply`: solo SQL, DTOs y advisory lock de sesion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg import pq

from app.apply_cola import _SQL_EXTERNALES_GRUPO
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
from app.db import tx_durable as _durable

_PASOS_HUERFANOS = ("intencion", "reversa_intencion")
_PASOS_QUITAR = ("aplicado", "reversa_fallida")
ESTADOS_REPARACION_QUITAR = frozenset({"PAUSED", "ARCHIVED", "AUSENTE"})


@dataclass(frozen=True)
class Efecto:
    status: int
    veredicto_id: UUID | None = None
    paso: str | None = None
    motivo: str | None = None


@dataclass(frozen=True)
class PrecondicionQuitar:
    plataforma: str
    ad_group_id: int
    termino: str
    negative_keyword_id: str
    estado_observado: str
    senal_id: UUID


@dataclass(frozen=True)
class PrecondicionAprobar:
    origen_id: UUID
    plataforma: str
    ad_group_id: int
    termino: str


@dataclass(frozen=True)
class _Senal:
    id: UUID
    plataforma: str
    ad_group_id: int
    termino: str
    termino_sha256: str
    roster_sha256: str
    huella: str | None = None


def _lateral_ultimo_efecto(columnas: str) -> str:
    """Subconsulta LATERAL del ultimo efecto de `v` (orden unico en el modulo)."""
    return (
        "JOIN LATERAL ("
        f" SELECT {columnas}"
        "   FROM bloqueo_efecto e"
        "  WHERE e.veredicto_id = v.id"
        "  ORDER BY e.created_at DESC, e.id DESC"
        "  LIMIT 1"
        ") ult ON true"
    )


_SQL_VIVOS_DISTINTOS = (
    "SELECT v.id, v.senal_id, ult.paso, ult.negative_keyword_id"
    "  FROM bloqueo_veredicto v "
    + _lateral_ultimo_efecto("e.paso, e.negative_keyword_id")
    + " WHERE v.tipo = 'aprobado'"
    "   AND v.plataforma = %s"
    "   AND v.ad_group_id = %s"
    "   AND v.termino_sha256 = %s"
    "   AND v.id <> %s"
    "   AND ult.paso = ANY(%s)"
)

_SQL_VIVO = (
    "SELECT 1 FROM bloqueo_veredicto v "
    + _lateral_ultimo_efecto("e.paso")
    + " WHERE v.tipo = 'aprobado'"
    "   AND v.plataforma = %s"
    "   AND v.ad_group_id = %s"
    "   AND v.termino_sha256 = %s"
    "   AND ult.paso = ANY(%s)"
)

_SQL_HUERFANOS = (
    "SELECT v.id, ult.paso, ult.negative_keyword_id, v.plataforma::text, v.termino, ult.motivo"
    "  FROM bloqueo_veredicto v "
    + _lateral_ultimo_efecto("e.paso, e.negative_keyword_id, e.motivo")
    + " WHERE v.tipo = 'aprobado'"
    "   AND v.ad_group_id = %s"
    "   AND v.termino_sha256 = %s"
    "   AND ult.paso = ANY(%s)"
)

_SQL_CLAVES_HUERFANAS = (
    "SELECT DISTINCT v.ad_group_id, v.termino_sha256"
    "  FROM bloqueo_veredicto v " + _lateral_ultimo_efecto("e.paso") + " WHERE v.tipo = 'aprobado'"
    "   AND ult.paso = ANY(%s)"
    "   AND v.ad_group_id IS NOT NULL"
    "   AND v.termino_sha256 IS NOT NULL"
)


def _vivos_distintos(conn, senal: _Senal, origen_id: UUID) -> list[tuple]:
    return conn.execute(
        _SQL_VIVOS_DISTINTOS,
        (senal.plataforma, senal.ad_group_id, senal.termino_sha256, origen_id, list(PASOS_VIVOS)),
    ).fetchall()


def _anula(conn, senal: _Senal, motivo: str) -> Efecto:
    huella = _huella(conn, senal.roster_sha256)
    if huella is None:
        return Efecto(409, motivo="catalogo")
    vid = uuid4()
    with _durable(conn):
        _insert_veredicto(conn, vid, "aprobado", senal, huella)
        _insert_efecto(conn, vid, "anulado", motivo=motivo)
    return Efecto(200, veredicto_id=vid, paso="anulado", motivo=motivo)


def _abre_intencion(conn, senal: _Senal, huella: str, *, motivo: str | None = None) -> UUID:
    vid = uuid4()
    with _durable(conn):
        _insert_veredicto(conn, vid, "aprobado", senal, huella)
        _insert_efecto(conn, vid, "intencion", motivo=motivo)
    return vid


def _sella(conn, vid: UUID, paso: str, **campos) -> None:
    with _durable(conn):
        _insert_efecto(conn, vid, paso, **campos)


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
        _SQL_VIVO,
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
        _SQL_HUERFANOS,
        (ad_group_id, termino_sha256, list(_PASOS_HUERFANOS)),
    ).fetchall()


def _claves_huerfanas(conn) -> list[tuple[int, str]]:
    return conn.execute(_SQL_CLAVES_HUERFANAS, (list(_PASOS_HUERFANOS),)).fetchall()


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
        info = getattr(conn, "info", None)
        if (
            not conn.autocommit
            and info is not None
            and info.transaction_status == pq.TransactionStatus.INERROR
        ):
            conn.rollback()
        conn.execute(
            "SELECT pg_advisory_unlock(hashtext(%s))",
            (_llave(ad_group_id, termino_sha256),),
        )
        if not conn.autocommit:
            conn.commit()
    except Exception:
        if not conn.autocommit:
            conn.rollback()
