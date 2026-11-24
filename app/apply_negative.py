"""El negative de corte: creacion con evidencia previa y su reversa (ARREGLOS 05 B).

Un dueno para el camino del negative de `apply_queue`. Antes del POST, una
lectura completa demuestra que la identidad no esta viva y esa evidencia nace
con la fila del ledger (`ausencia_previa`, 0095). La reversa relee antes de
cada envio y solo toca el objeto propio: el que vive con la identidad esperada
bajo el id que la decision acredita.

Modulo hoja para `app.apply_reversa`: no lo importa, ni a
`app.apply_cola_ejecuta`. Importa `app.apply` en top-level; ese modulo no
conoce este en el suyo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg

from app import apply, biblioteca
from app.apply_estados import (
    RESULTADO_NO_APLICADO,
    RESULTADO_OK,
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    decidir,
    envia_tras_lectura,
    evento_desde_lectura_objeto,
    transicion_desde_error,
)
from app.apply_store import (
    _json_seguro,
    _ledger,
    _sella_ledger,
    _tick,
    estado_de_reversa,
)
from app.db import tx_durable

if TYPE_CHECKING:
    from app.apply import Aplicador
    from app.apply_cola_ejecuta import FilaCola

LISTA_NEGATIVE = "/sp/negativeKeywords/list"
MATCH_NEGATIVE = "NEGATIVE_EXACT"
RESULTADO_RECONCILIADO = "ok:reconciliado"
FUENTE_LIST = "list"

# Como llego a Amazon el negative aplicado de una decision. `creacion_propia`
# es un ACK remoto con su id; `efecto_reconciliado` es una aparicion tras una
# ausencia previa durable. Los dos permiten la reversa manual del efecto;
# ninguno autoriza la limpieza automatica ni adoptar un objeto preexistente.
PROCEDENCIA_CREACION_PROPIA = "creacion_propia"
PROCEDENCIA_EFECTO_RECONCILIADO = "efecto_reconciliado"

_SQL_INTENTOS_APLICADOS = """
SELECT ack, resultado, ausencia_previa FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'normal' AND resultado IS NOT NULL
 ORDER BY seq
"""

_SQL_REVERSA_SIN_SELLO = """
SELECT id FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'reversa' AND finished_at IS NULL
 ORDER BY seq DESC
 LIMIT 1
"""


@dataclass(frozen=True)
class Identidad:
    """La identidad completa de un negative: ad group, texto y match."""

    ad_group_id: str
    keyword_text: str
    match_type: str = MATCH_NEGATIVE

    def como_dict(self) -> dict:
        return {
            "adGroupId": self.ad_group_id,
            "keywordText": self.keyword_text,
            "matchType": self.match_type,
        }


def identidad_del_payload(payload: dict) -> Identidad:
    """La identidad del negative que pide el payload espejo del wire de la cola."""
    return Identidad(
        ad_group_id=str(payload["adGroupId"]),
        keyword_text=payload["keywordText"],
        match_type=payload["matchType"],
    )


def ausencia_previa(plataforma: str, identidad: Identidad) -> dict:
    """Evidencia durable: una lectura completa no encontro viva esa identidad."""
    return {
        "lectura": LISTA_NEGATIVE,
        "plataforma": plataforma,
        "identidad": identidad.como_dict(),
    }


def lee_negativos(cliente, grupos: tuple[str, ...] | None = None) -> list[dict]:
    """Lectura estricta de la lista de negatives (ARREGLOS 04 B-C): completa o
    lanza `ListaIncompleta`. Con `grupos`, filtrada por ad group."""
    from app.apply_harvest_list import _lista_completa

    return _lista_completa(cliente, LISTA_NEGATIVE, grupos=grupos)


def evento_de_la_lectura(
    items: list[dict], *, id_propio: str | None, identidad: Identidad, destino: str
) -> EventoCambio:
    """El resolver unico de identidad, acotado a un negative."""
    return evento_desde_lectura_objeto(
        items,
        id_propio=id_propio,
        ad_group=identidad.ad_group_id,
        texto=identidad.keyword_text,
        match=identidad.match_type,
        destino=destino,
    )


def _id_del_ack(ack: dict) -> str | None:
    from app.ads.sp_wire import id_de_ack

    return id_de_ack(ack, "negativeKeywordId")


def _termina(conn: psycopg.Connection, fila: FilaCola, estado: str) -> None:
    from app.apply_cola_ejecuta import _termina as termina

    termina(conn, fila, estado)


def _ejecuta_negative(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola) -> str:
    """Aplica el corte negative. Antes del POST lee la lista del ad group: solo
    si la identidad no esta viva nace el intento con esa evidencia y sale el
    envio. Si ya vive algo con esa identidad (preexistente o dudoso) no se
    envia ni se adopta: la fila queda fallida, igual que ante un duplicado.
    Una lectura incompleta o un 401/403 al leer se propagan sin tocar nada."""
    payload = fila.request_payload
    identidad = identidad_del_payload(payload)
    cliente = aplicador._cliente()
    previa = lee_negativos(cliente, (identidad.ad_group_id,))
    evento = evento_de_la_lectura(previa, id_propio=None, identidad=identidad, destino="vivo")
    if not envia_tras_lectura(EstadoCambio.PENDIENTE, evento):
        _termina(conn, fila, "failed")
        return "failed"
    id_attempt = _ledger(
        conn,
        fila.decision_id,
        "normal",
        payload,
        quota_cobrada=True,
        ausencia_previa=ausencia_previa(aplicador._platform, identidad),
    )
    if id_attempt is None:
        _termina(conn, fila, "failed")
        return "failed"
    conn.commit()
    try:
        resp_http = cliente.crear_negative_exacto(
            payload["adGroupId"], payload["campaignId"], payload["keywordText"]
        )
    except apply.AdsApiErrorMutacion as exc:
        with tx_durable(conn):
            _sella_ledger(
                conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
            )
            _termina(conn, fila, "failed")
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        return "failed"
    ack = _json_seguro(resp_http)
    verify = _id_del_ack(ack) is not None
    resultado = RESULTADO_OK if verify else "fallo:ack_sin_id"
    rastro_biblio: dict | None = None
    grupo_biblio: int | None = None
    with tx_durable(conn):
        if verify:
            grupo = biblioteca.grupo_de_ad_group(conn, fila.ad_entity_id)
            if grupo is not None:
                grupo_biblio = grupo[0]
                rastro_biblio = biblioteca.registra_negative(
                    conn,
                    grupo_id=grupo[0],
                    tipo_producto=grupo[1],
                    platform=aplicador._platform,
                    texto=fila.search_term,
                    origen=f"grupo:{grupo[0]}/campana:{grupo[2]}/decision:{fila.decision_id}",
                    decision_id=fila.decision_id,
                )
        _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        aplicador.confirma_cercada(fila.decision_id, ack, verify)
        _termina(conn, fila, "applied" if verify else "failed")
    biblioteca.avisa_si_fallo(
        "negative",
        rastro_biblio,
        plataforma=aplicador._platform,
        grupo_id=grupo_biblio,
        decision_id=fila.decision_id,
        job_id=None,
        texto=fila.search_term,
    )
    return "applied" if verify else "failed"


def procedencia_del_efecto(
    conn: psycopg.Connection, decision_id: int, negative_id: str
) -> str | None:
    """Como llego a Amazon el negative `negative_id` de la decision, derivado
    de evidencia durable del ledger. None si el ledger no lo acredita."""
    filas = conn.execute(_SQL_INTENTOS_APLICADOS, (decision_id,)).fetchall()
    for ack, resultado, _ausencia in filas:
        if resultado == RESULTADO_OK and ack is not None and _id_del_ack(ack) == str(negative_id):
            return PROCEDENCIA_CREACION_PROPIA
    for _ack, resultado, ausencia in filas:
        if resultado == RESULTADO_RECONCILIADO and ausencia is not None:
            return PROCEDENCIA_EFECTO_RECONCILIADO
    return None


def _lee_vivo(cliente, identidad: Identidad, negative_id: str) -> EventoCambio:
    return evento_de_la_lectura(
        lee_negativos(cliente, (identidad.ad_group_id,)),
        id_propio=str(negative_id),
        identidad=identidad,
        destino="ausente",
    )


def _confirma_por_lectura(conn, decision_id: int, negative_id: str) -> bool:
    """El propio ya no vive: la reversa queda hecha sin enviar nada. La fila
    nace y se sella en la misma transaccion, con un ack que declara su fuente
    (no es un ACK remoto)."""
    payload = {"negativeKeywordIdFilter": {"include": [str(negative_id)]}}
    id_attempt = _ledger(conn, decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    with tx_durable(conn):
        _sella_ledger(
            conn,
            id_attempt,
            ack={"conciliado": True, "fuente": FUENTE_LIST, "leido": "ausente"},
            resultado=RESULTADO_OK,
        )
    return True


def _resuelve_abierto(
    conn: psycopg.Connection, cliente, identidad: Identidad, decision_id: int, negative_id: str
) -> bool:
    """Resuelve por LECTURA el intento de reversa que quedo abierto, antes de
    otro envio. True solo si la lectura confirma la reversa; si el negative
    sigue vivo el intento se cuenta como no aplicado y el llamador vuelve a
    consultar la tabla antes de enviar."""
    fila_intento = conn.execute(_SQL_REVERSA_SIN_SELLO, (decision_id,)).fetchone()
    if fila_intento is None:
        return False
    transicion = decidir(EstadoCambio.INCIERTO, _lee_vivo(cliente, identidad, negative_id))
    if transicion.accion == AccionCambio.SELLAR_APLICADO:
        resultado = RESULTADO_OK
    elif transicion.accion == AccionCambio.CONTAR_INTENTO:
        resultado = RESULTADO_NO_APLICADO
    else:
        return False
    with tx_durable(conn):
        _sella_ledger(
            conn,
            fila_intento[0],
            ack={"conciliado": True, "fuente": FUENTE_LIST},
            resultado=resultado,
        )
    return transicion.accion == AccionCambio.SELLAR_APLICADO


def reversa_negative(
    conn: psycopg.Connection,
    cliente,
    fila: FilaCola,
    negative_id: str | int,
    *,
    tick=None,
) -> bool:
    """Reversa del negative: POST /sp/negativeKeywords/delete con verificacion 207.

    Un intento de reversa abierto se resuelve leyendo antes de otro envio.
    Antes de enviar se relee el ad group: solo se borra el negative propio si
    vive con la identidad esperada bajo su id. Sustituido, duplicado, con el
    id cambiado o sin lectura concluyente, queda pendiente y no sale HTTP."""
    identidad = identidad_del_payload(fila.request_payload)
    if procedencia_del_efecto(conn, fila.decision_id, str(negative_id)) is None:
        return False
    transicion = decidir(estado_de_reversa(conn, fila.decision_id), EventoCambio.NUEVO_HORARIO)
    if transicion.accion == AccionCambio.LEER_RESULTADO:
        if _resuelve_abierto(conn, cliente, identidad, fila.decision_id, negative_id):
            return True
        transicion = decidir(estado_de_reversa(conn, fila.decision_id), EventoCambio.NUEVO_HORARIO)
    if transicion.accion != AccionCambio.ENVIAR:
        return False
    _tick(tick)
    evento = _lee_vivo(cliente, identidad, negative_id)
    previa = decidir(EstadoCambio.PENDIENTE, evento)
    if previa.accion == AccionCambio.SELLAR_APLICADO:
        return _confirma_por_lectura(conn, fila.decision_id, str(negative_id))
    if not envia_tras_lectura(EstadoCambio.PENDIENTE, evento):
        return False
    payload = {"negativeKeywordIdFilter": {"include": [str(negative_id)]}}
    id_attempt = _ledger(conn, fila.decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    conn.commit()
    try:
        resp_http = cliente.borrar_negative(negative_id)
    except apply.AdsApiErrorMutacion as exc:
        _sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        return False
    ack = _json_seguro(resp_http)
    _tick(tick)
    from app.apply_harvest_list import (
        _resultado_reversa_rechazada as resultado_reversa_rechazada,
    )
    from app.apply_harvest_list import (  # diferido: ese modulo importa app.apply
        _reversa_rechazada as reversa_rechazada,
    )

    if reversa_rechazada(ack, "negativeKeywordId", negative_id):
        with conn.transaction():
            _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado_reversa_rechazada(ack))
        return False
    with conn.transaction():
        _sella_ledger(conn, id_attempt, ack=ack, resultado=RESULTADO_OK)
    return True
