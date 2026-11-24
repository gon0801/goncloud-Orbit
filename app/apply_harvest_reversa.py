"""Reversas automatica y manual del corte harvest (regla 7, ORBIT 04 / F2 A.3).

Extraido de `app/apply_harvest.py` (Ola D3b). Unifica el envio de DELETE con
ledger pre-HTTP en `_post_delete_reversa` y la validacion comun de jobs en
`_validar_job_para_reversa`.
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Json

from app import apply
from app.ads.client import AdsApiError, AdsClientError
from app.ads.sp_wire import id_de_ack as _id_de_ack
from app.apply_estados import (
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    decidir,
    envia_tras_lectura,
    transicion_desde_error,
)
from app.apply_harvest_list import (
    ROLES_DISCOVERY,
    ListaIncompleta,
    PasoReversa,
    _Contexto,
    _Job,
    _job_de_fila,
    _lista_completa,
    _resultado_reversa_rechazada,
    _reversa_rechazada,
    evento_de_identidad,
    motivo_identidad,
)
from app.db import tx_durable

_SQL_EXTERNALES = """
SELECT grp.external_id, cam.external_id
  FROM ad_entity grp
  JOIN ad_entity cam ON cam.id = grp.parent_id AND cam.kind = 'campaign'
 WHERE grp.id = %s
"""

_SQL_PADRE = """
SELECT parent_id FROM ad_entity WHERE id = %s
"""

_SQL_DECISION = """
SELECT new_value, value_currency, inputs FROM decision WHERE id = %s
"""

_SQL_JOB_POR_ID = """
SELECT id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
  FROM harvest_job WHERE id = %s
"""

_SQL_VERIFY_OK = """
SELECT verify_ok FROM decision_application WHERE decision_id = %s
"""

_SQL_ULTIMA_COLA = """
SELECT estado FROM apply_queue WHERE decision_id = %s ORDER BY id DESC LIMIT 1
"""

_SQL_ACKS_HERMANA = """
SELECT request_payload->>'adGroupId', ack FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'hermana' AND ack IS NOT NULL
 ORDER BY id
"""

_SQL_INTENTO_NORMAL_OBJETO = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'normal'
       AND request_payload->>'adGroupId' = %s
       AND request_payload->>'keywordText' = %s
       AND request_payload->>'matchType' = %s
)
"""

_SQL_INTENTOS_NORMAL_OK_NEGATIVE = """
SELECT ack FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'normal' AND resultado = 'ok'
   AND request_payload->>'adGroupId' = %s
   AND request_payload->>'keywordText' = %s
   AND request_payload->>'matchType' = 'NEGATIVE_EXACT'
 ORDER BY id
"""

_SQL_REVERSA_OK_KEYWORD = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
       AND request_payload->'keywordIdFilter'->'include' @> %s::jsonb
)
"""

_SQL_REVERSA_OK_NEGATIVE = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
       AND request_payload->'negativeKeywordIdFilter'->'include' @> %s::jsonb
)
"""


def _intento_normal_objeto(conn: psycopg.Connection, job: _Job, grupo: str, tipo: str) -> bool:
    """Una intencion propia durable identifica el objeto, pero no prueba su creacion."""
    return bool(
        conn.execute(
            _SQL_INTENTO_NORMAL_OBJETO,
            (job.decision_id, grupo, job.search_term, tipo),
        ).fetchone()[0]
    )


def _post_delete_reversa(
    conn: psycopg.Connection,
    cliente,
    decision_id: int,
    clase: str,
    objeto_id,
    *,
    capturar_ambiguo: bool,
) -> tuple[int | None, dict | None]:
    """Nucleo compartido de delete de reversa (ledger pre-HTTP + POST + check 207)."""
    filtro = f"{clase}IdFilter" if clase == "keyword" else "negativeKeywordIdFilter"
    payload = {filtro: {"include": [str(objeto_id)]}}
    id_attempt = apply._ledger(conn, decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return (None, None)
    conn.commit()
    try:
        if clase == "keyword":
            resp = cliente.borrar_keyword(objeto_id)
        else:
            resp = cliente.borrar_negative(objeto_id)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            # Identidad o permisos de toda la corrida: el sello del DELETE se
            # conserva y el rechazo sube; no es un fallo de esta reversa.
            raise
        return (None, None)
    except AdsApiError:
        if capturar_ambiguo:
            return (id_attempt, None)
        raise
    ack = apply._json_seguro(resp)
    clave = "keywordId" if clase == "keyword" else "negativeKeywordId"
    if _reversa_rechazada(ack, clave, objeto_id):
        with tx_durable(conn):
            apply._sella_ledger(
                conn, id_attempt, ack=ack, resultado=_resultado_reversa_rechazada(ack)
            )
        return (None, None)
    return (id_attempt, ack)


def _reversa_delete(
    conn: psycopg.Connection, cliente, decision_id: int, clase: str, objeto_id
) -> bool:
    """UN delete de reversa con ledger pre-HTTP sellado al volver."""
    id_attempt, ack = _post_delete_reversa(
        conn, cliente, decision_id, clase, objeto_id, capturar_ambiguo=False
    )
    if id_attempt is None or ack is None:
        return False
    with tx_durable(conn):
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
    return True


def _delete_reversa_pendiente(
    conn: psycopg.Connection, cliente, decision_id: int, clase: str, objeto_id
) -> tuple[int | None, dict | None]:
    """POST de delete para reversa manual: deja la fila abierta hasta readback."""
    return _post_delete_reversa(conn, cliente, decision_id, clase, objeto_id, capturar_ambiguo=True)


def reversa_harvest_parcial(
    conn: psycopg.Connection, cliente, decision_id: int, negative_id
) -> bool:
    """Reversa del harvest PARCIAL (la keyword no nacio): delete del negativo."""
    return _reversa_delete(conn, cliente, decision_id, "negative", negative_id)


def reversa_harvest_completo(
    conn: psycopg.Connection, cliente, decision_id: int, negative_id, keyword_id
) -> bool:
    """Reversa del harvest COMPLETO: delete de KEYWORD primero, negativo despues."""
    if not _reversa_delete(conn, cliente, decision_id, "keyword", keyword_id):
        return False
    return _reversa_delete(conn, cliente, decision_id, "negative", negative_id)


def _validar_job_para_reversa(conn: psycopg.Connection, job_id: int) -> _Job:
    """Precondiciones comunes fail-closed para planear una reversa manual."""
    fila = conn.execute(_SQL_JOB_POR_ID, (job_id,)).fetchone()
    if fila is None:
        raise ValueError(f"job {job_id}: no existe")
    job = _job_de_fila(fila)
    if job.fase != "done":
        raise ValueError(f"job {job_id}: fase {job.fase} (la reversa manual solo acepta done)")
    ver = conn.execute(_SQL_VERIFY_OK, (job.decision_id,)).fetchone()
    if ver is None or ver[0] is not True:
        raise ValueError(f"job {job_id}: decision {job.decision_id} sin verify_ok")
    cola = conn.execute(_SQL_ULTIMA_COLA, (job.decision_id,)).fetchone()
    if cola is None or cola[0] != "applied":
        raise ValueError(f"job {job_id}: cola no applied")
    return job


def plan_reversa_harvest(
    conn: psycopg.Connection, job_id: int
) -> tuple[str, str, int, list[PasoReversa]]:
    """Deriva el plan de reversa en orden canonico (keyword -> hermanas -> origen)."""
    job = _validar_job_para_reversa(conn, job_id)
    ext = dict(job.external_ids)
    if "incierta" in (ext.get("keyword_creada"), ext.get("negative_creada")):
        raise ValueError(f"job {job_id}: procedencia de keyword o negativo incierta")
    keyword_id = ext.get("keyword_id")
    negative_id = ext.get("negative_id")
    if keyword_id is None or negative_id is None:
        raise ValueError(f"job {job_id}: sin keyword_id o negative_id en external_ids")
    _dec = conn.execute(_SQL_DECISION, (job.decision_id,)).fetchone()
    congelado = (((_dec[2] or {}).get("goal") or {}).get("harvest") or {}) if _dec else {}
    destino_ag = congelado.get("ad_group_id")
    if not destino_ag:
        raise ValueError(f"job {job_id}: sin destino congelado para el readback")
    externos = conn.execute(_SQL_EXTERNALES, (job.ad_entity_id,)).fetchone()
    if externos is None:
        raise ValueError(f"job {job_id}: origen sin externos")
    objetivo = dict(ext.get("hermanas_objetivo") or {})
    hermanas = dict(ext.get("hermanas") or {})
    registradas = {
        str(v.get("negative_id"))
        for v in hermanas.values()
        if isinstance(v, dict) and v.get("negative_id") is not None
    }
    ag_por_rol = {
        str(v.get("ad_group_id")): rol
        for rol, v in objetivo.items()
        if isinstance(v, dict) and v.get("ad_group_id") is not None
    }
    provisionales: dict[str, list[str]] = {}
    for ag, ack in conn.execute(_SQL_ACKS_HERMANA, (job.decision_id,)).fetchall():
        if ag is None or ag not in ag_por_rol or not isinstance(ack, dict):
            continue
        id_ = _id_de_ack(ack, "negativeKeywordId")
        if id_ is not None and str(id_) not in registradas:
            provisionales.setdefault(ag_por_rol[ag], []).append(str(id_))
    pasos = []
    if ext.get("keyword_creada") is True:
        pasos.append(
            PasoReversa(
                clase="keyword", rol=None, ad_group_ext=destino_ag, objeto_id=str(keyword_id)
            )
        )
    for rol in ROLES_DISCOVERY:
        reg = hermanas.get(rol)
        if isinstance(reg, dict) and reg.get("creada") is True:
            ag = (objetivo.get(rol) or {}).get("ad_group_id")
            if not ag:
                raise ValueError(f"job {job_id}: hermana {rol} sin ad group en el roster")
            pasos.append(
                PasoReversa(
                    clase="negative",
                    rol=rol,
                    ad_group_ext=ag,
                    objeto_id=str(reg["negative_id"]),
                )
            )
        for extra in provisionales.get(rol, []):
            ag = (objetivo.get(rol) or {}).get("ad_group_id")
            if not ag:
                raise ValueError(f"job {job_id}: hermana {rol} sin ad group en el roster")
            pasos.append(
                PasoReversa(
                    clase="negative",
                    rol=rol,
                    ad_group_ext=ag,
                    objeto_id=extra,
                    provisional=True,
                )
            )
    if ext.get("negative_creada") is True:
        pasos.append(
            PasoReversa(
                clase="negative", rol=None, ad_group_ext=externos[0], objeto_id=str(negative_id)
            )
        )
    return (job.plataforma, job.search_term, job.decision_id, pasos)


def plan_reversa_origen_harvest(
    conn: psycopg.Connection, job_id: int
) -> tuple[str, str, int, list[PasoReversa]]:
    """Plan SOLO-origen de un job legado con firma D.4 (`origen_es_destino`)."""
    job = _validar_job_para_reversa(conn, job_id)
    ext = dict(job.external_ids)
    negative_id = ext.get("negative_id")
    if negative_id is None:
        raise ValueError(f"job {job_id}: sin negative_id en external_ids")
    if ext.get("keyword_creada") is True or ext.get("negative_creada") is True:
        raise ValueError(
            f"job {job_id}: con procedencia F2 va por la reversa completa, no --solo-origen"
        )
    hermanas = dict(ext.get("hermanas") or {})
    if any(isinstance(v, dict) and v.get("creada") is True for v in hermanas.values()):
        raise ValueError(
            f"job {job_id}: con hermanas creadas va por la reversa completa, no --solo-origen"
        )
    externos = conn.execute(_SQL_EXTERNALES, (job.ad_entity_id,)).fetchone()
    if externos is None:
        raise ValueError(f"job {job_id}: origen sin externos")
    _dec = conn.execute(_SQL_DECISION, (job.decision_id,)).fetchone()
    congelado = (((_dec[2] or {}).get("goal") or {}).get("harvest") or {}) if _dec else {}
    if not congelado.get("campaign_id") or not congelado.get("ad_group_id"):
        raise ValueError(f"job {job_id}: sin destino congelado para la firma D.4")
    if str(congelado["ad_group_id"]) != externos[0]:
        raise ValueError(
            f"job {job_id}: el destino congelado no es el origen"
            " (--solo-origen solo remedia origen_es_destino)"
        )
    probado = False
    for (ack,) in conn.execute(
        _SQL_INTENTOS_NORMAL_OK_NEGATIVE, (job.decision_id, externos[0], job.search_term)
    ).fetchall():
        id_ack = _id_de_ack(ack, "negativeKeywordId")
        if id_ack is not None and str(id_ack) == str(negative_id):
            probado = True
            break
    if not probado:
        raise ValueError(
            f"job {job_id}: procedencia del negativo no probada por el ledger"
            " (sin intento normal ok con ack-id)"
        )
    return (
        job.plataforma,
        job.search_term,
        job.decision_id,
        [
            PasoReversa(
                clase="negative",
                rol=None,
                ad_group_ext=externos[0],
                objeto_id=str(negative_id),
            )
        ],
    )


def _reversa_confirmada(
    conn: psycopg.Connection, decision_id: int, clase: str, objeto_id: str
) -> bool:
    sql = _SQL_REVERSA_OK_KEYWORD if clase == "keyword" else _SQL_REVERSA_OK_NEGATIVE
    fila = conn.execute(sql, (decision_id, Json([str(objeto_id)]))).fetchone()
    return fila is not None and fila[0] is True


def _propio_vivo(items: list[dict], id_propio: str, ad_group: str, texto: str, match: str) -> bool:
    """True solo si el resolver ve vivo el objeto propio: su ID del ACK, con su
    grupo, su texto y su match. Un booleano de creacion no autoriza nada solo
    (ARREGLOS 05 B)."""
    return (
        evento_de_identidad(
            items, id_propio=str(id_propio), ad_group=ad_group, texto=texto, match=match
        )
        == EventoCambio.OK_CONFIRMADO
    )


def ejecuta_reversa_harvest(
    conn: psycopg.Connection,
    cliente,
    decision_id: int,
    term: str,
    pasos: list[PasoReversa],
    *,
    reversa_confirmada_fn=_reversa_confirmada,
    lista_completa_fn=_lista_completa,
) -> tuple[bool, str]:
    """Ejecuta el plan en orden canonico, con readback entre deletes y stop al
    primer paso que no se confirma.

    ARREGLOS 05 B: cada paso relee antes de enviar y usa el mismo resolver que
    la reversa automatica y la creacion. Solo sale un DELETE si el objeto
    propio vive con su identidad bajo el id del plan. Si ya no vive, el paso
    queda confirmado por lectura, sin HTTP. Sustituido o dudoso: pendiente, no
    se toca nada y el orden sellado detiene el resto."""
    for paso in pasos:
        quien = paso.rol or paso.clase
        if reversa_confirmada_fn(conn, decision_id, paso.clase, paso.objeto_id):
            continue
        try:
            items = _lee_paso(lista_completa_fn, cliente, paso)
        except ListaIncompleta:
            return (False, f"reversa: {quien} {paso.objeto_id} readback no concluyente")
        evento = _evento_del_paso(items, term, paso)
        transicion = decidir(EstadoCambio.PENDIENTE, evento)
        if transicion.accion == AccionCambio.SELLAR_APLICADO:
            if not _confirma_por_lectura(conn, decision_id, paso):
                return (False, f"reversa: stop en {quien} {paso.objeto_id}")
            continue
        if not envia_tras_lectura(EstadoCambio.PENDIENTE, evento):
            return (False, f"reversa: {quien} {paso.objeto_id} {motivo_identidad(evento)}")
        id_attempt, ack = _delete_reversa_pendiente(
            conn, cliente, decision_id, paso.clase, paso.objeto_id
        )
        if id_attempt is None or ack is None:
            return (False, f"reversa: stop en {quien} {paso.objeto_id}")
        try:
            items = _lee_paso(lista_completa_fn, cliente, paso)
        except ListaIncompleta:
            return (False, f"reversa: {quien} {paso.objeto_id} readback no concluyente")
        vivas = {
            str(x.get("keywordId"))
            for x in items
            if str(x.get("state", "")).upper() != apply.ESTADO_WIRE_ARCHIVED
        }
        if paso.objeto_id in vivas:
            with tx_durable(conn):
                apply._sella_ledger(conn, id_attempt, ack=ack, resultado="fallo:sigue_vivo")
            return (False, f"reversa: {quien} {paso.objeto_id} sigue vivo")
        with tx_durable(conn):
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
    return (True, "reversa: ok")


def _lee_paso(lista_completa_fn, cliente, paso: PasoReversa) -> list[dict]:
    """Lectura estricta de la lista del paso: completa o `ListaIncompleta`."""
    if paso.clase == "keyword":
        return lista_completa_fn(cliente, "/sp/keywords/list")
    return lista_completa_fn(cliente, "/sp/negativeKeywords/list", grupos=(str(paso.ad_group_ext),))


def _evento_del_paso(items: list[dict], term: str, paso: PasoReversa) -> EventoCambio:
    """El paso quiere al propio AUSENTE de Amazon."""
    return evento_de_identidad(
        items,
        id_propio=paso.objeto_id,
        ad_group=paso.ad_group_ext,
        texto=term,
        match="EXACT" if paso.clase == "keyword" else "NEGATIVE_EXACT",
        destino="ausente",
    )


def _confirma_por_lectura(conn: psycopg.Connection, decision_id: int, paso: PasoReversa) -> bool:
    """El propio ya no vive: el paso queda revertido sin enviar nada. La fila
    nace y se sella en la misma transaccion, con un ack que declara su fuente
    (no es un ACK remoto), asi la reanudacion no lo repite."""
    filtro = "keywordIdFilter" if paso.clase == "keyword" else "negativeKeywordIdFilter"
    id_attempt = apply._ledger(
        conn,
        decision_id,
        "reversa",
        {filtro: {"include": [str(paso.objeto_id)]}},
        quota_cobrada=False,
    )
    if id_attempt is None:
        return False
    with tx_durable(conn):
        apply._sella_ledger(
            conn,
            id_attempt,
            ack={"conciliado": True, "fuente": "list", "leido": "ausente"},
            resultado="ok",
        )
    return True


def _reversa_automatica(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    *,
    lista_completa_fn=_lista_completa,
    reversa_delete_fn=_reversa_delete,
) -> str:
    """Reversa del fallo definitivo: retirar lo que Orbit creo, en orden sellado
    (keyword primero).

    ARREGLOS 05 B: las lecturas estrictas van ANTES del primer delete y solo se
    borra un objeto si el resolver lo ve vivo con su identidad bajo el ID del
    ACK. Lo propio que ya no vive no lleva DELETE; lo ajeno nunca se toca. Una
    lectura incompleta se propaga sin reversa parcial (ARREGLOS 04 B-C)."""
    cliente = aplicador._cliente()
    ext = dict(job.external_ids)
    kw_id = ext.get("keyword_id") if ext.get("keyword_creada") is True else None
    neg_id = ext.get("negative_id") if ext.get("negative_creada") is True else None
    vivo_kw = kw_id is not None and _propio_vivo(
        lista_completa_fn(cliente, "/sp/keywords/list"),
        kw_id,
        ctx.destino_grupo,
        job.search_term,
        "EXACT",
    )
    vivo_neg = neg_id is not None and _propio_vivo(
        lista_completa_fn(cliente, "/sp/negativeKeywords/list"),
        neg_id,
        ctx.grupo_ext,
        job.search_term,
        "NEGATIVE_EXACT",
    )
    ausentes = [x for x, vivo in ((kw_id, vivo_kw), (neg_id, vivo_neg)) if x and not vivo]
    try:
        if (
            kw_id is not None
            and vivo_kw
            and not reversa_delete_fn(conn, cliente, job.decision_id, "keyword", kw_id)
        ):
            return f"reversa: fallo borrando keyword {kw_id}"
        if neg_id is None:
            if kw_id is None:
                return "reversa: nada que borrar (sin keyword ni negativo)"
            return "reversa: keyword borrada; negativo no encontrado (nada mas que borrar)"
        if not vivo_neg:
            return f"reversa: ok (lo propio ya no vive en Amazon: {', '.join(ausentes)})"
        if not reversa_delete_fn(conn, cliente, job.decision_id, "negative", neg_id):
            return f"reversa: fallo borrando negativo {neg_id}"
        return "reversa: ok"
    except AdsClientError as exc:
        if transicion_desde_error(EstadoCambio.INCIERTO, exc).propaga_aborto:
            raise
        return f"reversa: fallo ({exc})"
