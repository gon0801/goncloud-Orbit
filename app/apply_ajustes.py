"""Aplicacion, reconciliacion y regreso de ajustes de campana (extraido de app/apply.py, Fase 3.4).

No importa `app.ads.write`: construye el cliente a traves de `apply._cliente_ajuste`,
conservando intacto el candado `PERMITIDOS_IMPORTAR_ADS_WRITE` de `test_architecture`.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from app import apply
from app.ads.campana_config import (
    PLACEMENT_POR_CAMPO,
    ConfigCampana,
    config_de_payload,
    config_por_id,
    config_vigente,
    config_vigente_con_id,
    cuerpo_put_clase,
    cuerpo_put_ubicacion,
    guarda_config,
    mezcla_observacion,
)
from app.apply import SinPerfilAjuste as SinPerfilAjuste
from app.campana_ajustes import (
    AjusteSinCambio,
    CambiarAjusteUbicacion,
    CambiarPresupuesto,
    PlanAjuste,
    campo_del_ajuste,
    coincide_campo,
    coincide_clase,
    planea_ajuste,
)
from app.optimizer.bid import PLATAFORMAS_MONEDA

GO_AJUSTE = "APLICAR AJUSTE"
GO_REGRESO_AJUSTE = "REGRESAR AJUSTE"

_SQL_PLATAFORMA_CAMPANA = """
SELECT platform::text, external_id FROM ad_entity WHERE id = %s AND kind = 'campaign'
"""
_SQL_AJUSTE_POR_HUELLA = """
SELECT id, confirmado_el IS NOT NULL, campana_id, clase FROM campana_ajuste WHERE huella = %s
"""
_SQL_AJUSTE_POR_ID = """
SELECT id, campana_id, platform::text, clase, antes_config_id, confirmado_el,
       (SELECT count(*) FROM campana_ajuste r
         WHERE r.regresa_a = campana_ajuste.id AND r.confirmado_el IS NOT NULL)
  FROM campana_ajuste WHERE id = %s
"""
_SQL_HIJO_REGRESO = """
SELECT id, confirmado_el IS NOT NULL FROM campana_ajuste
 WHERE regresa_a = %s ORDER BY id
"""
_SQL_DESPUES_POR_ID = """
SELECT despues FROM campana_ajuste WHERE id = %s
"""
_SQL_CUENTA_HUELLA = """
SELECT count(*) FROM campana_ajuste WHERE huella = %s OR huella LIKE %s
"""
_SQL_INSERTA_AJUSTE = """
INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id, despues,
    huella, actor, go_literal, regresa_a)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (huella) DO NOTHING
RETURNING id
"""
_SQL_INSERTA_REGRESO = """
INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id, despues,
    huella, actor, go_literal, regresa_a)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (regresa_a) DO NOTHING
RETURNING id
"""
_SQL_CONFIRMA_AJUSTE = """
UPDATE campana_ajuste SET confirmado_el = %s WHERE id = %s
"""
_SQL_AJUSTES_PENDIENTES_HN = """
SELECT id, huella, despues FROM campana_ajuste
 WHERE huella LIKE %s AND confirmado_el IS NULL AND campana_id = %s AND clase = %s
 ORDER BY id
"""
_SQL_AJUSTE_POR_HUELLA_RECONCILIA = """
SELECT id, confirmado_el IS NOT NULL, campana_id, clase, despues
  FROM campana_ajuste WHERE huella = %s
"""
_SQL_CONFIRMADAS_HN = """
SELECT id, despues
  FROM campana_ajuste
 WHERE campana_id = %s AND clase = %s AND huella LIKE %s AND confirmado_el IS NOT NULL
 ORDER BY id DESC
"""


_SQL_CONFIRMADA_POR_ID = """
SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE id = %s
"""


class ConfirmacionInvalida(Exception):
    pass


class HuellaDesactualizada(Exception):
    """El vigente cambio desde la vista previa. Lleva el plan fresco para
    que la ruta lo devuelva en el 409."""

    def __init__(self, mensaje: str, plan_nuevo=None) -> None:
        super().__init__(mensaje)
        self.plan_nuevo = plan_nuevo


class AjusteNoConfirmado(Exception):
    """El PUT salio pero el readback no coincide: Amazon no aplico (o aun
    no refleja) el cambio. La fila queda sin confirmar y el reintento la
    reusa (o la reconciliacion la sella si el sync ya observo el destino)."""


class AjusteEnCurso(Exception):
    """Otro intento con la misma huella o regreso gano la carrera: su fila
    sigue pendiente. Reintentar (409)."""


class AjusteInexistente(Exception):
    pass


class AjusteYaRegresado(Exception):
    pass


class AjusteSinRegreso(Exception):
    """La clase no tiene camino de regreso sellado (V.0: fuera_de_amazon —
    mandar {} no regresa nada) o la configuracion de antes no es
    expresable como ajuste."""


class AjusteHecho:
    def __init__(self, ajuste_id: int, campana_id: int, clase: str, confirmada: bool) -> None:
        self.ajuste_id = ajuste_id
        self.campana_id = campana_id
        self.clase = clase
        self.confirmada = confirmada

    def como_dict(self) -> dict:
        return {
            "ajuste_id": self.ajuste_id,
            "campana_id": self.campana_id,
            "clase": self.clase,
            "confirmada": self.confirmada,
        }


def _config_leida(cliente, plataforma: str, externa: str):
    """Readback de la campana por POST /sp/campaigns/list (scope sellado):
    la config parseada, o None si Amazon no la trae, trae otra (cruce por
    id, nunca items[0]: la API ignora filtros en silencio) o la trae
    ilegible (fail-closed, reintentable)."""
    respuesta = cliente.list_sellado(
        "/sp/campaigns/list", {"campaignIdFilter": {"include": [externa]}}
    )
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    if not isinstance(cuerpo, dict):
        return None
    for item in cuerpo.get("campaigns", []):
        if isinstance(item, dict) and str(item.get("campaignId")) == str(externa):
            try:
                return config_de_payload(item, PLATAFORMAS_MONEDA[plataforma])
            except ValueError:
                return None
    return None


_SQL_AJUSTE_POSTERIOR = """
SELECT id, antes_config_id, despues, regresa_a
  FROM campana_ajuste
 WHERE campana_id = %s AND clase = %s AND id > %s AND confirmado_el IS NOT NULL
 ORDER BY id
"""

_SQL_AJUSTE_POSTERIOR_PENDIENTE_O_CONF = """
SELECT id, antes_config_id, despues, regresa_a
  FROM campana_ajuste
 WHERE campana_id = %s AND clase = %s AND id > %s
 ORDER BY id
"""

_SQL_AJUSTE_POSTERIOR_PENDIENTE = """
SELECT id, antes_config_id, despues, regresa_a
  FROM campana_ajuste
 WHERE campana_id = %s AND clase = %s AND id > %s AND confirmado_el IS NULL
 ORDER BY id
"""


def _toca_campo(
    conn, antes_id: int, despues: dict, campo: str, *, regresa_a: int | None = None
) -> bool:
    """True si la fila cambio ESE campo de ConfigCampana (una orden de otra
    ubicacion no invalida la de esta).

    Una REVERSION guarda como `despues` la configuracion completa anterior al
    ajuste original, aunque solo restaura UNA ubicacion; su campo real es el
    del ajuste padre (`regresa_a`), no las diferencias del completo."""
    if regresa_a is not None:
        padre = conn.execute(
            "SELECT antes_config_id, despues FROM campana_ajuste WHERE id = %s",
            (regresa_a,),
        ).fetchone()
        if padre is None:
            return False
        antes_id, despues = padre
    antes = config_por_id(conn, antes_id)
    despues_cfg = ConfigCampana.desde_json(despues)
    return getattr(antes, campo) != getattr(despues_cfg, campo)


def _confirmada_equivalente(
    conn, huella: str, *, campana_id: int, clase: str, ajuste, vigente
) -> int | None:
    """El acto con sufijo que SI confirmo el destino, para no sellar la orden
    base superada en una reconciliacion."""
    for pid, despues in conn.execute(
        _SQL_CONFIRMADAS_HN, (campana_id, clase, huella + "#%")
    ).fetchall():
        if coincide_campo(ajuste, vigente, ConfigCampana.desde_json(despues)):
            return pid
    return None


def _superada(
    conn,
    ajuste_id: int,
    *,
    campana_id: int,
    clase: str,
    ajuste,
    incluir_pendientes: bool = False,
) -> bool:
    """True si una orden posterior toco el mismo campo: con `incluir_pendientes=False`
    solo revisa ordenes confirmadas; con `True` bloquea tambien ante pendientes."""
    campo = campo_del_ajuste(ajuste)
    sql = _SQL_AJUSTE_POSTERIOR_PENDIENTE_O_CONF if incluir_pendientes else _SQL_AJUSTE_POSTERIOR
    for _pid, antes_id, despues, regresa_a in conn.execute(
        sql, (campana_id, clase, ajuste_id)
    ).fetchall():
        if _toca_campo(conn, antes_id, despues, campo, regresa_a=regresa_a):
            return True
    return False


def _pendiente_posterior_con_otro_destino(
    conn, ajuste_id: int, *, campana_id: int, clase: str, ajuste
) -> bool:
    """True si otra orden PENDIENTE posterior cambia el mismo campo hacia un
    valor distinto. Esas ordenes mandan sobre `ajuste_id`: su PUT pudo llegar a
    Amazon sin readback ni sync, asi que reenviar la vieja (o sellarla como
    confirmada) pisaria su valor y atribuiria un efecto que no produjo. Las
    posteriores hacia el MISMO valor son reintentos del mismo acto."""
    campo = campo_del_ajuste(ajuste)
    fila = conn.execute(_SQL_DESPUES_POR_ID, (ajuste_id,)).fetchone()
    if fila is None:
        return False
    valor = getattr(ConfigCampana.desde_json(fila[0]), campo)
    for _pid, antes_id, despues, regresa_a in conn.execute(
        _SQL_AJUSTE_POSTERIOR_PENDIENTE, (campana_id, clase, ajuste_id)
    ).fetchall():
        if not _toca_campo(conn, antes_id, despues, campo, regresa_a=regresa_a):
            continue
        if getattr(ConfigCampana.desde_json(despues), campo) != valor:
            return True
    return False


def _aplica_y_confirma(
    conn, cliente, plataforma, clase, destino, ajuste, *, campana_id, ajuste_id, previa
) -> None:
    """PUT del campo + readback + sello, serializado por acto (B9). El
    PUT lleva SOLO el campo que el ajuste cambia (B11: un placement
    solo, con la estrategia leida ANTES del PUT; las otras clases
    llevan su llave, B1); quien llama ya commiteo la intencion; aqui
    se INTENTA un candado advisory de transaccion por `llave` (try, no
    espera) y se re-lee la fila: si otro intento ya confirmo, se
    vuelve sin PUT (un solo PUT por acto); si otro trabaja, 409
    AjusteEnCurso. El sello final commitea (durable + libera el
    candado). El readback manda: si lo leido no coincide en el campo,
    la fila queda sin confirmar (reintentable: el reintento reusa la
    pendiente). Lo que se guarda es la leida mezclada sobre `previa`
    (B1: el PUT no mueve campos ajenos)."""
    # TEMPORADA 01 G: el candado es por campaña y campo, tambien al regresar.
    campo = campo_del_ajuste(ajuste)
    llave = f"campana:{campana_id}:{campo}"
    candado = conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (llave,)).fetchone()[0]
    if not candado:
        # Otro intento trabaja este acto (try, no espera: un reintento
        # anidado dentro del PUT del primero se interbloquearia, L).
        conn.rollback()
        raise AjusteEnCurso(f"ajuste {ajuste_id} en curso: reintenta en un momento")
    try:
        ya = conn.execute(_SQL_CONFIRMADA_POR_ID, (ajuste_id,)).fetchone()
        if ya is not None and ya[0]:
            conn.rollback()
            return
        if _superada(
            conn,
            ajuste_id,
            campana_id=campana_id,
            clase=clase,
            ajuste=ajuste,
            incluir_pendientes=True,
        ):
            # La orden quedo vieja: hay otra (confirmada o pendiente) DESPUES
            # para el mismo campo. Sin PUT; el endpoint responde 409.
            raise AjusteEnCurso(
                "Ya mandaste otra orden para esta campaña después de esta. Recarga."
            )
        vigente_actual = config_vigente(conn, campana_id)
        if vigente_actual is None or not coincide_campo(ajuste, vigente_actual, previa):
            raise HuellaDesactualizada("el vigente cambio antes de enviar el ajuste", None)
        if isinstance(ajuste, CambiarAjusteUbicacion):
            actual = _config_leida(cliente, plataforma, destino.campana_externa)
            strategy = actual.estrategia_puja if actual is not None else None
            if strategy is None:
                # La lectura no trae dynamicBidding (raro: el vigente si
                # tenia, o planear habria rechazado): fallback al vigente,
                # no hay actual que respetar. Sin ninguna, fail-closed.
                strategy = destino.estrategia_puja
            if strategy is None:
                raise AjusteNoConfirmado(
                    f"ajuste {ajuste_id}: sin estrategia no se manda el PUT de un placement"
                )
            cuerpo = cuerpo_put_ubicacion(
                PLACEMENT_POR_CAMPO[campo], ajuste.porcentaje, destino, strategy
            )
        else:
            cuerpo = cuerpo_put_clase(clase, destino)
        cliente.ajustar_campana(
            destino.campana_externa,
            {k: v for k, v in cuerpo.items() if k != "campaignId"},
        )
        leida = _config_leida(cliente, plataforma, destino.campana_externa)
        if leida is None or not coincide_campo(ajuste, leida, destino):
            raise AjusteNoConfirmado(
                f"ajuste {ajuste_id}: Amazon no confirma la config enviada"
                " (readback distinto); la fila queda sin confirmar"
            )
        ahora = datetime.datetime.now(datetime.UTC)
        with conn.transaction():
            conn.execute(_SQL_CONFIRMA_AJUSTE, (ahora, ajuste_id))
            guarda_config(conn, plataforma, [mezcla_observacion(previa, leida)], ahora)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _pendientes_hn(conn, huella: str, campana_id: int, clase: str) -> list:
    """Todas las H#N pendientes de la huella, de la mas vieja a la mas
    nueva. Las dos puertas de R07-09C leen de aqui para no divergir."""
    return conn.execute(_SQL_AJUSTES_PENDIENTES_HN, (huella + "#%", campana_id, clase)).fetchall()


def _sella_pendientes_en_destino(conn, pendientes, ajuste, vigente) -> int | None:
    """Sella cada pendiente cuyo destino ya es el vigente y devuelve el
    id de la primera, o None si ninguna llego. Sellar solo la primera
    dejaria el resto huerfanas con el mismo efecto vivo."""
    sellada = None
    for pid, _huella, despues in pendientes:
        destino = ConfigCampana.desde_json(despues)
        if vigente == destino or coincide_campo(ajuste, vigente, destino):
            _sella_pendiente(conn, pid)
            if sellada is None:
                sellada = pid
    return sellada


def _sella_pendiente(conn, ajuste_id: int) -> None:
    """Sella una fila pendiente sin PUT (reconciliacion: el vigente ya es
    el destino porque el sync observo lo que el PUT si aplico). El commit
    es explicito: quien llama (la ruta, sin autocommit) solo cierra, y
    sin commit el `with transaction` es un savepoint revertido al cerrar
    (B3: respondia confirmada:true sin sello durable)."""
    with conn.transaction():
        conn.execute(_SQL_CONFIRMA_AJUSTE, (datetime.datetime.now(datetime.UTC), ajuste_id))
    conn.commit()


def aplica_ajuste_campana(
    conn,
    plan,
    *,
    huella: str,
    confirmacion: str,
    actor: str,
    transport=None,
) -> AjusteHecho:
    """Aplica el plan que el dueno vio: re-planea con el vigente, exige la
    huella y el literal, escribe la fila ANTES del HTTP (intencion durable
    pre-HTTP, como reversa_bid) y confirma con el readback. Idempotente por
    huella: el mismo plan dos veces hace UN PUT, y solo cuando el vigente
    sigue en destino (tras un regreso, re-aplicar es un acto nuevo con
    huella H#N). Una pendiente con el vigente ya en destino se reconcilia
    sin PUT; una carrera perdida devuelve 409, nunca 500."""
    if confirmacion != GO_AJUSTE:
        raise ConfirmacionInvalida(f"confirmacion invalida: esperaba {GO_AJUSTE!r}")
    if not actor or not actor.strip():
        raise ValueError("actor vacio")
    fila = conn.execute(_SQL_PLATAFORMA_CAMPANA, (plan.campana_id,)).fetchone()
    if fila is None or fila[0] != plan.plataforma:
        raise ValueError(f"campana {plan.campana_id} no es de {plan.plataforma}")
    # Vigente + id en UN select y ANTES de la red: el sync no se cuela.
    antes_id, vigente = config_vigente_con_id(conn, plan.campana_id)
    if vigente is None:
        raise HuellaDesactualizada(
            f"campana {plan.campana_id} sin configuracion vigente: re-planea", None
        )
    existente = conn.execute(_SQL_AJUSTE_POR_HUELLA, (huella,)).fetchone()
    huella_efectiva = huella
    ajuste_id = None
    if existente is not None:
        _eid, confirmada, ecamp, eclase = existente
        if ecamp != plan.campana_id or eclase != plan.clase:
            raise ValueError(f"la huella {huella} es de otra campana o clase")
        pendientes_hn = _pendientes_hn(conn, huella, plan.campana_id, plan.clase)
        if vigente == plan.despues:
            pendientes_rec = [
                p
                for p in pendientes_hn
                if not _superada(
                    conn,
                    p[0],
                    campana_id=plan.campana_id,
                    clase=plan.clase,
                    ajuste=plan.ajuste,
                )
            ]
            if pendientes_rec:
                sellada = _sella_pendientes_en_destino(conn, pendientes_rec, plan.ajuste, vigente)
                return AjusteHecho(sellada, plan.campana_id, plan.clase, True)
            if confirmada:
                return AjusteHecho(_eid, plan.campana_id, plan.clase, True)
            if not _superada(
                conn, _eid, campana_id=plan.campana_id, clase=plan.clase, ajuste=plan.ajuste
            ):
                _sella_pendiente(conn, _eid)
                return AjusteHecho(_eid, plan.campana_id, plan.clase, True)
            equivalente = _confirmada_equivalente(
                conn,
                huella,
                campana_id=plan.campana_id,
                clase=plan.clase,
                ajuste=plan.ajuste,
                vigente=vigente,
            )
            if equivalente is not None:
                return AjusteHecho(equivalente, plan.campana_id, plan.clase, True)
        # R07-09C + PR #739: solo se reutiliza una pendiente que siga vigente
        # bajo la misma regla que el candado (incluir_pendientes=True). Si quedo
        # superada por otra orden posterior (confirmada o pendiente), se abre H#N.
        pendientes_activas = [
            p
            for p in pendientes_hn
            if not _superada(
                conn,
                p[0],
                campana_id=plan.campana_id,
                clase=plan.clase,
                ajuste=plan.ajuste,
                incluir_pendientes=True,
            )
        ]
        if pendientes_activas:
            ajuste_id = pendientes_activas[0][0]
            huella_efectiva = pendientes_activas[0][1]
        elif not confirmada and not _superada(
            conn,
            _eid,
            campana_id=plan.campana_id,
            clase=plan.clase,
            ajuste=plan.ajuste,
            incluir_pendientes=True,
        ):
            ajuste_id = _eid
        elif _pendiente_posterior_con_otro_destino(
            conn, _eid, campana_id=plan.campana_id, clase=plan.clase, ajuste=plan.ajuste
        ):
            # El sufijo nace DESPUES de la orden que invalido esta aprobacion:
            # _aplica_y_confirma solo mira ids mayores y ya no la veria. Si esa
            # orden pendiente llego a Amazon, reenviar esta la pisa con un valor
            # viejo. Se rechaza y se pide recargar la vista previa.
            raise AjusteEnCurso(
                "Ya mandaste otra orden pendiente para esta campaña. Recarga la vista previa."
            )
        else:
            n = 1 + conn.execute(_SQL_CUENTA_HUELLA, (huella, huella + "#%")).fetchone()[0]
            huella_efectiva = f"{huella}#{n}"
    fresco = planea_ajuste(
        vigente, plan.ajuste, campana_id=plan.campana_id, plataforma=plan.plataforma
    )
    if fresco.huella() != huella or plan.huella() != huella:
        raise HuellaDesactualizada("el vigente cambio desde la vista previa", fresco)
    cliente = apply._cliente_ajuste(plan.plataforma, transport=transport)
    if ajuste_id is None:
        metida = conn.execute(
            _SQL_INSERTA_AJUSTE,
            (
                plan.campana_id,
                plan.plataforma,
                plan.clase,
                antes_id,
                json.dumps(plan.despues.como_json()),
                huella_efectiva,
                actor.strip(),
                confirmacion,
                None,
            ),
        ).fetchone()
        if metida is None:
            ganadora = conn.execute(_SQL_AJUSTE_POR_HUELLA, (huella_efectiva,)).fetchone()
            if ganadora is not None and ganadora[1]:
                return AjusteHecho(ganadora[0], plan.campana_id, plan.clase, True)
            raise AjusteEnCurso(f"huella {huella_efectiva} en curso en otro intento: reintenta")
        ajuste_id = metida[0]
    conn.commit()
    _aplica_y_confirma(
        conn,
        cliente,
        plan.plataforma,
        plan.clase,
        plan.despues,
        plan.ajuste,
        campana_id=plan.campana_id,
        ajuste_id=ajuste_id,
        previa=plan.antes,
    )
    return AjusteHecho(ajuste_id, plan.campana_id, plan.clase, True)


def reconcilia_ajuste_campana(
    conn, campana_id: int, clase: str, ajuste, *, huella: str, actor: str
) -> AjusteHecho:
    """Reconcilia un envio cuyo ajuste ya no cambia nada contra el
    vigente (B4: la ruta llega aqui cuando planear falla con
    AjusteSinCambio). El replay identico tras el exito devuelve el
    mismo hecho y una pendiente con el destino ya observado se sella
    (commit dentro de _sella_pendiente, B3); sin fila previa con esa
    huella, ValueError (422, como antes). Si el completo difiere pero
    el campo del ajuste ya esta en el destino (otro campo cambio por
    fuera), tambien sella (B12)."""
    if not actor or not actor.strip():
        raise ValueError("actor vacio")
    fila = conn.execute(_SQL_PLATAFORMA_CAMPANA, (campana_id,)).fetchone()
    if fila is None:
        raise ValueError(f"campana {campana_id} no existe o no es campana")
    vigente = config_vigente(conn, campana_id)
    if vigente is None:
        raise ValueError(f"campana {campana_id} sin configuracion vigente")
    previa = conn.execute(_SQL_AJUSTE_POR_HUELLA_RECONCILIA, (huella,)).fetchone()
    if previa is None:
        raise ValueError("el ajuste no cambia nada")
    _eid, confirmada, ecamp, eclase, despues = previa
    if ecamp != campana_id or eclase != clase:
        raise HuellaDesactualizada(f"la huella {huella} es de otra campana o clase", None)
    # R07-09C: cada H#N pendiente cuyo efecto el sync ya observo se
    # sella aqui. Devolver la base sellada dejaria las pendientes
    # huerfanas para siempre con su efecto vivo.
    pendientes = [
        p
        for p in _pendientes_hn(conn, huella, campana_id, clase)
        if not _superada(conn, p[0], campana_id=campana_id, clase=clase, ajuste=ajuste)
        and not _pendiente_posterior_con_otro_destino(
            conn, p[0], campana_id=campana_id, clase=clase, ajuste=ajuste
        )
    ]
    sellada = _sella_pendientes_en_destino(conn, pendientes, ajuste, vigente)
    if sellada is not None:
        return AjusteHecho(sellada, campana_id, clase, True)
    superada = (not confirmada) and (
        _superada(conn, _eid, campana_id=campana_id, clase=clase, ajuste=ajuste)
        or _pendiente_posterior_con_otro_destino(
            conn, _eid, campana_id=campana_id, clase=clase, ajuste=ajuste
        )
    )
    equivalente = (
        _confirmada_equivalente(
            conn, huella, campana_id=campana_id, clase=clase, ajuste=ajuste, vigente=vigente
        )
        if superada
        else None
    )
    destino = ConfigCampana.desde_json(despues)
    if vigente == destino or coincide_campo(ajuste, vigente, destino):
        # B12: si el completo o el campo del ajuste ya coincide en Amazon,
        # el acto se cumplio (respetando si quedo superado por orden posterior).
        if confirmada:
            return AjusteHecho(_eid, campana_id, clase, True)
        if superada:
            if equivalente is not None:
                return AjusteHecho(equivalente, campana_id, clase, True)
            raise HuellaDesactualizada(
                "la orden quedo superada por otra posterior: pide un plan nuevo", None
            )
        _sella_pendiente(conn, _eid)
        return AjusteHecho(_eid, campana_id, clase, True)
    try:
        fresco = planea_ajuste(vigente, ajuste, campana_id=campana_id, plataforma=fila[0])
    except AjusteSinCambio:
        fresco = None
    raise HuellaDesactualizada("el vigente cambio desde la vista previa", fresco)


def regresa_ajuste_campana(conn, *, ajuste_id: int, actor: str, transport=None) -> AjusteHecho:
    """Regreso del ajuste: restaura SOLO los campos de la clase de ese
    ajuste a su valor de antes (B2: el diseno manda "el mismo camino
    con el valor anterior"; la config completa pisaria ajustes
    posteriores de otra clase y podria SUBIR un presupuesto). Un solo
    regreso por ajuste; fuera_de_amazon no tiene regreso sellado (V.0)
    y no presenta boton."""
    if not actor or not actor.strip():
        raise ValueError("actor vacio")
    fila = conn.execute(_SQL_AJUSTE_POR_ID, (ajuste_id,)).fetchone()
    if fila is None:
        raise AjusteInexistente(f"ajuste {ajuste_id} no existe")
    _id, campana_id, plataforma, clase, antes_id, confirmado, regresos = fila
    if confirmado is None:
        raise AjusteNoConfirmado(
            f"ajuste {ajuste_id} sin confirmar: no se regresa lo que Amazon no aplico"
        )
    if regresos:
        raise AjusteYaRegresado(f"ajuste {ajuste_id} ya tiene su regreso")
    if clase == "fuera_de_amazon":
        raise AjusteSinRegreso(
            f"ajuste {ajuste_id}: fuera_de_amazon no tiene regreso sellado (V.0)"
        )
    antes = config_por_id(conn, antes_id)
    vigente_id, vigente = config_vigente_con_id(conn, campana_id)
    hijo = conn.execute(_SQL_HIJO_REGRESO, (ajuste_id,)).fetchone()
    if vigente is None:
        raise AjusteNoConfirmado(
            f"ajuste {ajuste_id}: sin configuracion vigente de la campana en DB"
        )
    if vigente == antes:
        if hijo is not None and not hijo[1]:
            _sella_pendiente(conn, hijo[0])
            return AjusteHecho(hijo[0], campana_id, clase, True)
        raise AjusteYaRegresado(
            f"ajuste {ajuste_id}: la campana ya tiene la configuracion de antes"
        )
    if vigente != antes and coincide_clase(clase, vigente, antes):
        # Solo la clase volvio a antes (otro ajuste posterior o cambio
        # manual + sync). El `!=` blinda el caso completo-igual para la
        # rama de arriba (M10).
        hijo_pendiente = hijo is not None and not hijo[1]
        if hijo_pendiente:
            _sella_pendiente(conn, hijo[0])
            return AjusteHecho(hijo[0], campana_id, clase, True)
        raise AjusteYaRegresado(
            f"ajuste {ajuste_id}: la campana ya tiene el valor de antes en la clase {clase}"
        )
    despues_padre = ConfigCampana.desde_json(
        conn.execute(_SQL_DESPUES_POR_ID, (ajuste_id,)).fetchone()[0]
    )
    ajuste_hijo = _ajuste_hacia(clase, antes, despues_padre)
    if hijo is not None and not hijo[1]:
        if _superada(
            conn,
            hijo[0],
            campana_id=campana_id,
            clase=clase,
            ajuste=ajuste_hijo,
            incluir_pendientes=True,
        ):
            raise AjusteSinRegreso(
                f"ajuste {ajuste_id}: el regreso pendiente quedo superado por otra orden"
                " posterior en el mismo campo; aplica un ajuste nuevo con el valor deseado"
            )
        nuevo_id = hijo[0]
    else:
        regreso = PlanAjuste(
            campana_id=campana_id,
            plataforma=plataforma,
            clase=clase,
            ajuste=ajuste_hijo,
            antes=vigente,
            despues=antes,
            frase=(f"Orbit regresa la campaña {antes.campana_externa} a su configuración de antes"),
        )
        metido = conn.execute(
            _SQL_INSERTA_REGRESO,
            (
                campana_id,
                plataforma,
                clase,
                vigente_id,
                json.dumps(antes.como_json()),
                _huella_regreso(regreso.huella(), ajuste_id),
                actor.strip(),
                GO_REGRESO_AJUSTE,
                ajuste_id,
            ),
        ).fetchone()
        if metido is None:
            ganador = conn.execute(_SQL_HIJO_REGRESO, (ajuste_id,)).fetchone()
            if ganador is not None and ganador[1]:
                raise AjusteYaRegresado(f"ajuste {ajuste_id} ya tiene su regreso")
            raise AjusteEnCurso(f"regreso de {ajuste_id} en curso en otro intento: reintenta")
        nuevo_id = metido[0]
    cliente = apply._cliente_ajuste(plataforma, transport=transport)
    conn.commit()
    _aplica_y_confirma(
        conn,
        cliente,
        plataforma,
        clase,
        antes,
        ajuste_hijo,
        campana_id=campana_id,
        ajuste_id=nuevo_id,
        previa=vigente,
    )
    return AjusteHecho(nuevo_id, campana_id, clase, True)


def _huella_regreso(base: str, ajuste_id: int) -> str:
    """Huella del regreso: la del plan mas el padre que regresa (B8).
    Sin el padre, dos regresos del mismo antes/despues (aplicar,
    regresar, re-aplicar, regresar) chocan en UNIQUE(huella) con 500;
    el padre distingue cada acto."""
    return hashlib.sha256(f"{base}:regresa:{ajuste_id}".encode()).hexdigest()


def _ajuste_hacia(clase, antes, despues_padre):
    """Ajuste representativo del regreso (el destino real es `antes`,
    del que el PUT solo toma el campo, B11). El presupuesto restaura
    el monto de antes; la ubicacion restaura el placement que el padre
    movio (diff entre su antes y su despues, no contra el vigente:
    otro ajuste posterior no se toca)."""
    if clase == "presupuesto":
        if antes.presupuesto_diario is None:
            raise AjusteSinRegreso("la configuracion de antes no trae presupuesto")
        return CambiarPresupuesto(presupuesto_diario=antes.presupuesto_diario)
    movidos = [
        (campo, ubicacion)
        for campo, ubicacion in (
            ("ajuste_top_pct", "arriba_de_busqueda"),
            ("ajuste_resto_pct", "resto_de_busqueda"),
            ("ajuste_producto_pct", "paginas_de_producto"),
        )
        if getattr(antes, campo) != getattr(despues_padre, campo)
    ]
    if len(movidos) != 1:
        raise AjusteSinRegreso(
            "el padre no movio exactamente un placement; el regreso no sabe cual restaurar"
        )
    campo, ubicacion = movidos[0]
    if getattr(antes, campo) is None:
        raise AjusteSinRegreso("la configuracion de antes no trae ese ajuste")
    return CambiarAjusteUbicacion(ubicacion=ubicacion, porcentaje=getattr(antes, campo))
