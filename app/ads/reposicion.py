"""Reposicion durable de keywords archivadas (ARREGLOS 05 C).

Un solo protocolo por fila: claim (intencion durable visible antes del HTTP),
payload exacto inmutable, POST, ACK con ID durable antes del readback, lectura
completa por ID y sello del intento y del archivo en la misma transaccion. Dos
CLI no crean dos objetos: el indice unico parcial de intentos abiertos y el
lease por fila lo impiden. La reversa archiva solo el ID repuesto propio.

La implementacion vive aqui por el presupuesto de 900 lineas de
`app/ads/archivar.py`; el punto de entrada publico sigue siendo
`app.ads.archivar.reponer_keyword` / `app.ads.archivar.revertir_reposicion`.
Toda transicion se consulta en `app.apply_estados` (tabla unica); este modulo
solo ejecuta la `accion` que la tabla ordena.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

import psycopg

from app import apply_estados as est
from app.ads import reposicion_store as store
from app.ads.client import AdsApiError
from app.ads.sp_wire import errores_de_ack, id_de_ack
from app.redaction import scrub

LISTA_KEYWORDS = "/sp/keywords/list"
MATCH_VALIDOS = frozenset({"EXACT", "PHRASE", "BROAD"})
ESTADO_PAUSADO = est.ESTADO_WIRE_PAUSED

Enviar = Callable[[str, int, dict], dict]
Dormir = Callable[[float], None]


class AbortoFase(RuntimeError):
    """Fallo global (401/403, lease perdido o sello ya tomado): detiene la fase."""


class ReposicionInvalida(ValueError):
    """La fila no se puede reponer y se sabe ANTES de reclamar o enviar."""


@dataclass(frozen=True)
class ResultadoReposicionKeyword:
    archivo_id: int
    keyword_external: str
    resultado: str
    detalle: str = ""
    external_id: str | None = None


class _LectorSellado:
    """Adapta `AdsClient` + perfil fijo a la puerta `list_sellado` del lector
    estricto de ARREGLOS 04 B-C."""

    def __init__(self, cliente, profile: int) -> None:
        self._cliente = cliente
        self._profile = profile

    def list_sellado(self, path: str, body: dict):
        return self._cliente.list_objects(path, body, profile_id=self._profile)


def _lee_keywords(cliente, profile: int) -> list[dict] | None:
    """Lectura completa estricta; None si no concluye (nunca adopta ausencia)."""
    from app.apply_harvest_list import _lista_completa

    try:
        return _lista_completa(_LectorSellado(cliente, profile), LISTA_KEYWORDS)
    except AdsApiError as exc:
        if est.es_rechazo_de_identidad(exc):
            raise
        return None


def _ahora() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _lease() -> dt.datetime:
    return _ahora() + dt.timedelta(seconds=store.LEASE_SEGUNDOS)


def _ack_seguro(ack: dict) -> dict:
    return json.loads(scrub(json.dumps(ack, default=str)))


def _bid_wire(bid: Decimal) -> float:
    if not isinstance(bid, Decimal):
        raise ReposicionInvalida(f"bid debe ser Decimal, no {type(bid).__name__}")
    return float(bid.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _payload(fila: store.FilaArchivo) -> dict:
    return {
        "adGroupId": str(fila.ad_group_external),
        "campaignId": str(fila.campaign_external),
        "keywordText": fila.keyword_text,
        "matchType": fila.match_type,
        "state": ESTADO_PAUSADO,
        "bid": _bid_wire(fila.bid),
    }


def _payload_reversa(fila: store.FilaArchivo) -> dict:
    return {"keywordIdFilter": {"include": [str(fila.repuesto_external)]}}


def _valida(filas: list[store.FilaArchivo], perfiles: dict[str, int]) -> None:
    from app.optimizer.bid import PLATAFORMAS_MONEDA

    for f in filas:
        if f.match_type not in MATCH_VALIDOS:
            raise ReposicionInvalida(
                f"fila {f.id} con match {f.match_type!r} fuera de {sorted(MATCH_VALIDOS)}"
            )
        if f.bid is None or f.bid_currency is None:
            raise ReposicionInvalida(
                f"fila {f.id} ({f.keyword_external}) sin bid en el ledger: no se inventa"
            )
        if PLATAFORMAS_MONEDA.get(f.plataforma) != f.bid_currency:
            raise ReposicionInvalida(
                f"fila {f.id}: moneda {f.bid_currency!r} != moneda de {f.plataforma}"
            )
        if f.plataforma not in perfiles:
            raise ReposicionInvalida(f"sin perfil aceptado para {f.plataforma} ({f.id})")


def _estado(hechos: store.Hechos) -> est.EstadoCambio:
    resultado = (
        est.RESULTADO_OK
        if hechos.hay_ok
        else est.resultado_terminal_reposicion(hechos.ultimo_resultado)
    )
    return est.estado_inicial_desde_hechos(
        en_vuelo_ajeno=False,
        tiene_intento_abierto=hechos.intento_abierto_id is not None,
        resultado_terminal=resultado,
        intentos_totales=hechos.intentos_totales,
        tope_intentos=store.TOPE_INTENTOS_REPOSICION,
    )


def _trans(estado: est.EstadoCambio, evento: est.EventoCambio) -> est.Transicion:
    return est.decidir(estado, evento)


def _sella_ok(
    conn: psycopg.Connection,
    fila: store.FilaArchivo,
    intento: store.Intento,
    external_id: str,
    ack: dict,
    *,
    archivo: bool,
) -> None:
    """Sello del intento y del archivo en la misma transaccion."""
    with conn.transaction():
        store.bloquea_reposicion(conn, fila.id, intento.tipo)
        if intento.external_id is None:
            store.guarda_external(conn, intento.id, external_id)
        if intento.ack is None:
            store.guarda_ack(conn, intento.id, ack)
        if not store.sella(conn, intento.id, est.RESULTADO_OK):
            raise AbortoFase(f"el intento {intento.id} ya fue sellado por otro dueno")
        if archivo and not store.sella_archivo_repuesto(conn, fila.id, external_id, ack):
            raise AbortoFase(f"la fila {fila.id} ya no esta 'applied': no se sella repuesta")
    conn.commit()


def _sella_resultado(
    conn: psycopg.Connection,
    archivo_id: int,
    tipo: str,
    intento_id: int,
    resultado: str,
    ack: dict | None = None,
) -> None:
    with conn.transaction():
        store.bloquea_reposicion(conn, archivo_id, tipo)
        if ack is not None:
            store.guarda_ack(conn, intento_id, ack)
        if not store.sella(conn, intento_id, resultado):
            raise AbortoFase(f"el intento {intento_id} ya fue sellado por otro dueno")
    conn.commit()


def _resultado_terminal(evento: est.EventoCambio) -> str:
    if evento == est.EventoCambio.IDENTIDAD_SUSTITUIDA:
        return est.RESULTADO_IDENTIDAD_SUSTITUIDA
    if evento == est.EventoCambio.ENTIDAD_ARCHIVADA:
        return est.RESULTADO_REPOSICION_ENTIDAD_ARCHIVADA
    raise AbortoFase(f"evento sin sello terminal previsto: {evento}")


def _pendiente(fila: store.FilaArchivo, detalle: str) -> ResultadoReposicionKeyword:
    return ResultadoReposicionKeyword(fila.id, fila.keyword_external, "pendiente", detalle)


def _sin_envio(
    fila: store.FilaArchivo, estado: est.EstadoCambio, *, exito: str
) -> ResultadoReposicionKeyword:
    if estado == est.EstadoCambio.APLICADO:
        return ResultadoReposicionKeyword(fila.id, fila.keyword_external, f"ya_{exito}", "")
    nombre = {
        est.EstadoCambio.TOPE_INTENTOS: "tope",
        est.EstadoCambio.AJENO: "ajeno",
        est.EstadoCambio.ARCHIVADO: "archivado",
    }.get(estado, "pendiente")
    return ResultadoReposicionKeyword(fila.id, fila.keyword_external, nombre, f"estado {estado}")


def _desenlace(
    conn: psycopg.Connection,
    fila: store.FilaArchivo,
    intento: store.Intento,
    evento: est.EventoCambio,
    *,
    tipo: str,
) -> ResultadoReposicionKeyword:
    """Aplica a la tabla la lectura de un intento abierto o de su readback."""
    trans = _trans(est.EstadoCambio.INCIERTO, evento)
    if trans.accion == est.AccionCambio.SELLAR_APLICADO:
        if tipo == "reponer":
            if not intento.external_id:
                return _pendiente(fila, "lectura confirmo sin id propio: se relee")
            _sella_ok(
                conn,
                fila,
                intento,
                intento.external_id,
                intento.ack or {"conciliado": True, "fuente": "list"},
                archivo=True,
            )
            return ResultadoReposicionKeyword(
                fila.id, fila.keyword_external, "repuesto", "", intento.external_id
            )
        ack = intento.ack or {"conciliado": True, "fuente": "list", "sin_envio": True}
        _sella_ok(
            conn,
            fila,
            intento,
            str(fila.repuesto_external),
            ack,
            archivo=False,
        )
        return ResultadoReposicionKeyword(
            fila.id, fila.keyword_external, "revertido", "", fila.repuesto_external
        )
    if trans.accion == est.AccionCambio.SELLAR_TERMINAL:
        resultado = _resultado_terminal(evento)
        _sella_resultado(conn, fila.id, tipo, intento.id, resultado)
        nombre = "ajeno" if resultado == est.RESULTADO_IDENTIDAD_SUSTITUIDA else "archivado"
        return ResultadoReposicionKeyword(
            fila.id, fila.keyword_external, nombre, f"sellado {resultado}"
        )
    if trans.accion == est.AccionCambio.CONTAR_INTENTO:
        _sella_resultado(conn, fila.id, tipo, intento.id, est.RESULTADO_NO_APLICADO)
        return _pendiente(fila, "lectura completa no encontro el efecto: se cuenta el intento")
    if trans.accion == est.AccionCambio.ABORTAR_FASE:
        raise AbortoFase(f"rechazo de identidad en la lectura de {fila.keyword_external}")
    return _pendiente(fila, f"lectura no concluyente ({evento})")


def _rechazo_definitivo(
    conn: psycopg.Connection, fila: store.FilaArchivo, intento: store.Intento
) -> ResultadoReposicionKeyword:
    """Sella el 401/403 ya guardado en el ACK y deja la fila reintentable."""
    trans = _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.RECHAZO_IDENTIDAD_DEFINITIVO)
    if trans.accion != est.AccionCambio.SELLAR_RECHAZADO:
        raise AbortoFase(f"rechazo de identidad sin sello previsto en {fila.keyword_external}")
    _sella_resultado(
        conn,
        fila.id,
        intento.tipo,
        intento.id,
        est.RESULTADO_REPOSICION_RECHAZO,
        intento.ack,
    )
    return ResultadoReposicionKeyword(
        fila.id,
        fila.keyword_external,
        "reintentable",
        "401/403 sellado: la operacion puede reintentarse",
    )


def _reconciliar(
    conn: psycopg.Connection,
    intento: store.Intento | None,
    fila: store.FilaArchivo,
    cliente,
    profile: int | None,
    owner: str,
) -> ResultadoReposicionKeyword:
    if intento is None:
        return _pendiente(fila, "sin intento abierto que conciliar")
    if profile is None:
        return _pendiente(fila, f"sin perfil aceptado para {fila.plataforma}")
    if intento.owner != owner:
        if intento.lease_hasta >= _ahora():
            return _pendiente(fila, "otro CLI tiene el lease vigente: no se toca")
        if not store.toma_intento(conn, intento.id, owner, _lease()):
            return _pendiente(fila, "otro CLI tomo el intento vencido")
        conn.commit()
    if est.evento_desde_ack_identidad(intento.ack) is not None:
        return _rechazo_definitivo(conn, fila, intento)
    items = _lee_keywords(cliente, profile)
    if items is None:
        return _pendiente(fila, "lectura no concluyente: el intento sigue abierto")
    if intento.tipo == "reponer":
        evento = est.evento_reposicion_desde_lectura(
            items,
            id_propio=intento.external_id,
            ad_group=fila.ad_group_external,
            texto=fila.keyword_text,
            match=fila.match_type,
            destino="vivo",
            campaign_esperada=fila.campaign_external,
            bid_esperado=fila.bid,
            estado_esperado=ESTADO_PAUSADO,
        )
    else:
        evento = est.evento_reposicion_desde_lectura(
            items,
            id_propio=str(fila.repuesto_external),
            ad_group=fila.ad_group_external,
            texto=fila.keyword_text,
            match=fila.match_type,
            destino="ausente",
            abierto=True,
        )
    return _desenlace(conn, fila, intento, evento, tipo=intento.tipo)


def _reponer_fila(
    conn: psycopg.Connection,
    fila: store.FilaArchivo,
    cliente,
    profile: int | None,
    enviar: Enviar,
    owner: str,
) -> ResultadoReposicionKeyword:
    if profile is None:
        return _pendiente(fila, f"sin perfil aceptado para {fila.plataforma}")
    hechos = store.hechos(conn, fila.id, "reponer")
    trans = _trans(_estado(hechos), est.EventoCambio.NUEVO_HORARIO)
    if trans.accion == est.AccionCambio.LEER_RESULTADO:
        return _reconciliar(
            conn, store.abierto(conn, fila.id, "reponer"), fila, cliente, profile, owner
        )
    if trans.accion != est.AccionCambio.ENVIAR:
        return _sin_envio(fila, _estado(hechos), exito="repuesto")
    payload = _payload(fila)
    intento_id = store.abre_intento(
        conn,
        archivo_id=fila.id,
        tipo="reponer",
        owner=owner,
        payload=payload,
        lease_hasta=_lease(),
        estado_esperado="applied",
    )
    conn.commit()
    if intento_id is None:
        return _pendiente(fila, "otro intento de reposicion esta abierto")
    intento = store.abierto(conn, fila.id, "reponer")
    if intento is None:
        return _pendiente(fila, "el intento reclamado no se pudo releer")
    try:
        ack = enviar(fila.plataforma, profile, payload)
    except Exception as exc:  # noqa: BLE001 - red/timeout: pudo aplicarse
        _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.ERROR_5XX_O_RED)
        return _pendiente(fila, f"envio incierto: {scrub(str(exc))[:300]}")
    ack = _ack_seguro(ack)
    status = ack.get("status")
    if status in est.STATUS_IDENTIDAD_RECHAZADA:
        with conn.transaction():
            store.guarda_ack(conn, intento.id, ack)
        conn.commit()
        trans = _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.RECHAZO_IDENTIDAD)
        if trans.propaga_aborto:
            raise AbortoFase(f"401/403 al reponer {fila.keyword_external}: aborta la fase")
    if not isinstance(status, int) or status >= 500:
        _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.ERROR_5XX_O_RED)
        return _pendiente(fila, f"envio incierto ({status}): el intento sigue abierto")
    cuerpo = ack.get("cuerpo")
    if status >= 400:
        _sella_resultado(
            conn, fila.id, "reponer", intento.id, est.RESULTADO_REPOSICION_RECHAZO, ack
        )
        return ResultadoReposicionKeyword(
            fila.id, fila.keyword_external, "fallo", f"rechazo {status}: {ack.get('texto', '')}"
        )
    if not isinstance(cuerpo, dict):
        # 200/207 con JSON null, lista u otra forma: Amazon pudo crear el
        # objeto y no hay ID. No es un rechazo; el intento sigue abierto y el
        # ACK queda durable para la lectura siguiente.
        with conn.transaction():
            store.guarda_ack(conn, intento.id, ack)
        conn.commit()
        return _pendiente(fila, "ACK sin objeto legible: el intento sigue abierto")
    if not errores_de_ack(cuerpo):
        external = id_de_ack(cuerpo, "keywordId")
        if not external:
            with conn.transaction():
                store.guarda_ack(conn, intento.id, ack)
            conn.commit()
            return _pendiente(fila, "ack sin keywordId: el intento sigue abierto")
        with conn.transaction():
            if not store.guarda_ack_e_id(conn, intento.id, ack, external):
                raise AbortoFase(f"el intento {intento.id} fue tomado por otro dueno")
        conn.commit()
        if not store.renueva(conn, intento.id, owner, _lease()):
            _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.DUENO_DESPLAZADO)
            raise AbortoFase(f"lease de {fila.keyword_external} perdido tras el ACK")
        conn.commit()
        intento = store.abierto(conn, fila.id, "reponer") or intento
        items = _lee_keywords(cliente, profile)
        if items is None:
            return _pendiente(fila, "ACK e ID durables; readback no concluyente")
        evento = est.evento_reposicion_desde_lectura(
            items,
            id_propio=external,
            ad_group=fila.ad_group_external,
            texto=fila.keyword_text,
            match=fila.match_type,
            destino="vivo",
            campaign_esperada=fila.campaign_external,
            bid_esperado=fila.bid,
            estado_esperado=ESTADO_PAUSADO,
        )
        return _desenlace(conn, fila, intento, evento, tipo="reponer")
    _sella_resultado(conn, fila.id, "reponer", intento.id, est.RESULTADO_REPOSICION_RECHAZO, ack)
    return ResultadoReposicionKeyword(
        fila.id, fila.keyword_external, "fallo", f"rechazo {status}: {ack.get('texto', '')}"
    )


def reponer_keyword(
    conn: psycopg.Connection,
    *,
    lote: str,
    cliente,
    perfiles: dict[str, int],
    enviar: Enviar,
    owner: str,
    ejecutar: bool,
    avisar: Callable[[str], None] = print,
    dormir: Dormir = time.sleep,
    pausa: float = 0.3,
) -> list[ResultadoReposicionKeyword]:
    """Repone las filas `applied` del lote, una por una y con evidencia durable."""
    filas = store.filas_reponibles(conn, lote)
    conn.commit()
    if not filas:
        return []
    if not ejecutar:
        return [
            ResultadoReposicionKeyword(f.id, f.keyword_external, "sin_confirmar", "ENSAYO")
            for f in filas
        ]
    _valida(filas, perfiles)
    avisar(f"a reponer: {len(filas)}")
    resultados: list[ResultadoReposicionKeyword] = []
    for abierto in store.abiertos_del_lote(conn, lote, "reponer"):
        resultados.append(
            _reconciliar(
                conn,
                abierto.intento,
                abierto.fila,
                cliente,
                perfiles.get(abierto.fila.plataforma),
                owner,
            )
        )
    for fila in store.filas_reponibles(conn, lote):
        res = _reponer_fila(conn, fila, cliente, perfiles.get(fila.plataforma), enviar, owner)
        resultados.append(res)
        avisar(f"  {fila.plataforma} | {fila.keyword_external} | {res.resultado}")
        dormir(pausa)
    return resultados


def _revertir_fila(
    conn: psycopg.Connection,
    fila: store.FilaArchivo,
    cliente,
    profile: int | None,
    eliminar: Enviar,
    owner: str,
) -> ResultadoReposicionKeyword:
    if profile is None:
        return _pendiente(fila, f"sin perfil aceptado para {fila.plataforma}")
    hechos = store.hechos(conn, fila.id, "reversa")
    trans = _trans(_estado(hechos), est.EventoCambio.NUEVO_HORARIO)
    if trans.accion == est.AccionCambio.LEER_RESULTADO:
        return _reconciliar(
            conn, store.abierto(conn, fila.id, "reversa"), fila, cliente, profile, owner
        )
    if trans.accion != est.AccionCambio.ENVIAR:
        return _sin_envio(fila, _estado(hechos), exito="revertido")
    items = _lee_keywords(cliente, profile)
    if items is None:
        return _pendiente(fila, "lectura previa no concluyente: no se envia")
    evento = est.evento_reposicion_desde_lectura(
        items,
        id_propio=str(fila.repuesto_external),
        ad_group=fila.ad_group_external,
        texto=fila.keyword_text,
        match=fila.match_type,
        destino="ausente",
    )
    previa = _trans(est.EstadoCambio.PENDIENTE, evento)
    if previa.accion == est.AccionCambio.SELLAR_APLICADO:
        intento_id = store.abre_intento(
            conn,
            archivo_id=fila.id,
            tipo="reversa",
            owner=owner,
            payload=_payload_reversa(fila),
            lease_hasta=_lease(),
            estado_esperado="repuesto",
            repuesto_external_esperado=str(fila.repuesto_external),
        )
        conn.commit()
        if intento_id is None:
            return _pendiente(fila, "otro intento de reversa esta abierto")
        intento = store.abierto(conn, fila.id, "reversa")
        if intento is None:
            return _pendiente(fila, "el intento de reversa no se pudo releer")
        _sella_ok(
            conn,
            fila,
            intento,
            str(fila.repuesto_external),
            {"conciliado": True, "fuente": "list", "sin_envio": True},
            archivo=False,
        )
        return ResultadoReposicionKeyword(
            fila.id, fila.keyword_external, "revertido", "ya estaba archivada", None
        )
    if not est.envia_tras_lectura(est.EstadoCambio.PENDIENTE, evento):
        return _pendiente(fila, "la lectura previa no autoriza el DELETE")
    intento_id = store.abre_intento(
        conn,
        archivo_id=fila.id,
        tipo="reversa",
        owner=owner,
        payload=_payload_reversa(fila),
        lease_hasta=_lease(),
        estado_esperado="repuesto",
        repuesto_external_esperado=str(fila.repuesto_external),
    )
    conn.commit()
    if intento_id is None:
        return _pendiente(fila, "otro intento de reversa esta abierto")
    intento = store.abierto(conn, fila.id, "reversa")
    if intento is None:
        return _pendiente(fila, "el intento de reversa no se pudo releer")
    try:
        ack = eliminar(fila.plataforma, profile, _payload_reversa(fila))
    except Exception as exc:  # noqa: BLE001 - red/timeout: pudo aplicarse
        _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.ERROR_5XX_O_RED)
        return _pendiente(fila, f"DELETE incierto: {scrub(str(exc))[:300]}")
    ack = _ack_seguro(ack)
    status = ack.get("status")
    if status in est.STATUS_IDENTIDAD_RECHAZADA:
        with conn.transaction():
            store.guarda_ack(conn, intento.id, ack)
        conn.commit()
        trans = _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.RECHAZO_IDENTIDAD)
        if trans.propaga_aborto:
            raise AbortoFase(f"401/403 al revertir {fila.keyword_external}: aborta la fase")
    if not isinstance(status, int) or status >= 500:
        _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.ERROR_5XX_O_RED)
        return _pendiente(fila, f"DELETE incierto ({status}): el intento sigue abierto")
    cuerpo = ack.get("cuerpo")
    if status >= 400 or (isinstance(cuerpo, dict) and errores_de_ack(cuerpo)):
        _sella_resultado(
            conn, fila.id, "reversa", intento.id, est.RESULTADO_REPOSICION_RECHAZO, ack
        )
        return ResultadoReposicionKeyword(
            fila.id, fila.keyword_external, "fallo", f"rechazo {status}: {ack.get('texto', '')}"
        )
    if not isinstance(cuerpo, dict):
        with conn.transaction():
            store.guarda_ack(conn, intento.id, ack)
        conn.commit()
        return _pendiente(fila, "ACK sin objeto legible: el intento sigue abierto")
    with conn.transaction():
        if not store.guarda_ack_e_id(conn, intento.id, ack, str(fila.repuesto_external)):
            raise AbortoFase(f"el intento {intento.id} fue tomado por otro dueno")
    conn.commit()
    if not store.renueva(conn, intento.id, owner, _lease()):
        _trans(est.EstadoCambio.INCIERTO, est.EventoCambio.DUENO_DESPLAZADO)
        raise AbortoFase(f"lease de {fila.keyword_external} perdido tras el DELETE")
    conn.commit()
    intento = store.abierto(conn, fila.id, "reversa") or intento
    items = _lee_keywords(cliente, profile)
    if items is None:
        return _pendiente(fila, "ACK e ID durables; readback no concluyente")
    evento = est.evento_reposicion_desde_lectura(
        items,
        id_propio=str(fila.repuesto_external),
        ad_group=fila.ad_group_external,
        texto=fila.keyword_text,
        match=fila.match_type,
        destino="ausente",
        abierto=True,
    )
    return _desenlace(conn, fila, intento, evento, tipo="reversa")


def reconciliar_reposicion(
    conn: psycopg.Connection,
    *,
    lote: str | None,
    cliente,
    perfiles: dict[str, int],
    owner: str,
) -> list[ResultadoReposicionKeyword]:
    """Concilia por lectura los intentos abiertos, sin emitir mutaciones.

    Con `lote=None` recorre todos los lotes. Un intento con lease vigente de
    otro owner no se toca; uno vencido se toma y se resuelve por lectura.
    """
    resultados: list[ResultadoReposicionKeyword] = []
    for tipo in ("reponer", "reversa"):
        for abierto in store.abiertos_del_lote(conn, lote, tipo):
            resultados.append(
                _reconciliar(
                    conn,
                    abierto.intento,
                    abierto.fila,
                    cliente,
                    perfiles.get(abierto.fila.plataforma),
                    owner,
                )
            )
    return resultados


def revertir_reposicion(
    conn: psycopg.Connection,
    *,
    lote: str,
    cliente,
    perfiles: dict[str, int],
    eliminar: Enviar,
    owner: str,
    ejecutar: bool,
    avisar: Callable[[str], None] = print,
    dormir: Dormir = time.sleep,
    pausa: float = 0.3,
) -> list[ResultadoReposicionKeyword]:
    """Archiva solo el ID repuesto propio de cada fila `repuesto` del lote."""
    filas = store.filas_revertibles(conn, lote)
    conn.commit()
    if not filas:
        return []
    if not ejecutar:
        return [
            ResultadoReposicionKeyword(f.id, f.keyword_external, "sin_confirmar", "ENSAYO")
            for f in filas
        ]
    _valida(filas, perfiles)
    avisar(f"a revertir reposiciones: {len(filas)}")
    resultados: list[ResultadoReposicionKeyword] = []
    for abierto in store.abiertos_del_lote(conn, lote, "reversa"):
        resultados.append(
            _reconciliar(
                conn,
                abierto.intento,
                abierto.fila,
                cliente,
                perfiles.get(abierto.fila.plataforma),
                owner,
            )
        )
    for fila in store.filas_revertibles(conn, lote):
        res = _revertir_fila(conn, fila, cliente, perfiles.get(fila.plataforma), eliminar, owner)
        resultados.append(res)
        avisar(f"  {fila.plataforma} | {fila.keyword_external} | {res.resultado}")
        dormir(pausa)
    return resultados
