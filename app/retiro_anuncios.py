"""Retiro de product ads de bolsas compartidas, con reposicion (BIDS 02, I.4).

Un producto que entra en impulso sale de sus bolsas viejas cuando sus
campanas nuevas ya tienen impresiones (primero nace lo nuevo, despues
sale de lo viejo: nunca queda sin anuncio). El retiro PAUSA y la reposicion
REACTIVA el mismo adId; si se archivo por fuera, `repone` devuelve
`requiere_recreacion` y solo `recrea` lo vuelve a crear.

Las piezas base (`app/retiro_anuncios_base.py`), regreso
(`app/retiro_anuncios_regreso.py`) y reconciliacion
(`app/retiro_anuncios_reconcilia.py`) se re-exportan desde aqui (Ola D3a).
"""

from __future__ import annotations

import datetime as dt
from typing import get_args

from app import impulso as im
from app import pantalla_productos as pp
from app.ads import archivar
from app.ads.client import AdsApiError
from app.ads.config import AdsConfigError
from app.apply_estados import EstadoCambio, transicion_desde_error, transicion_desde_status
from app.dashboard_resumen import hoy_resumen
from app.dia_cerrado import dia_local
from app.redaction import scrub
from app.retiro_anuncios_base import (  # noqa: F401 - re-export estable
    _DETALLE_ADOPTADO,
    _RETIRO_FUERA,
    _RETIRO_RESUELTO,
    _SQL_ENTIDAD_DE_ANUNCIO,
    _SQL_INTENCION_ABIERTA,
    _SQL_YA_RETIRADO,
    DIAS_LIBRO_AL_DIA,
    Autorizacion,
    AutorizacionDeDecision,
    AutorizacionHumana,
    AutorizacionInvalida,
    MotivoRetiro,
    PlanRetiro,
    ResultadoAnuncio,
    ResultadoIntencion,
    ResultadoRegreso,
    ResultadoReposicion,
    ResultadoRetiro,
    _abre_intencion,
    _actor_de,
    _bloqueados,
    _cierra_intencion,
    _detalle_ya_sellado,
    _huella_plan,
    _put_propio,
    _sella_desde_intencion,
    _sella_retiro,
    lote_retiro_de_plan,
    no_resueltos,
)
from app.retiro_anuncios_reconcilia import (  # noqa: F401 - re-export estable
    _SQL_INTENCION_DE_DECISION,
    _SQL_INTENCIONES_PENDIENTES,
    reconcilia_intenciones,
    resuelve_intencion_de_decision,
    retiros_pendientes_de_impulso,
)
from app.retiro_anuncios_regreso import (  # noqa: F401 - re-export estable
    _SQL_INTENTO_ABIERTO,
    _SQL_RETIROS,
    _lee_retiros,
    _regresa,
    _repone_uno,
    _Retiro,
    _ya_como_estaba,
    recrea,
    repone,
)

_SQL_ANUNCIOS = """
SELECT pa.id, pa.external_id, ag.external_id, ag.name, camp.external_id,
       li.seller_sku, li.external_id
  FROM ad_entity pa
  JOIN ad_entity_state es ON es.ad_entity_id = pa.id
   AND (es.status = 'ENABLED' OR EXISTS (
       SELECT 1 FROM anuncio_retiro_intencion i WHERE i.ad_entity_id = pa.id
         AND i.cerrada_el IS NULL))
  JOIN ad_entity ag ON ag.id = pa.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity camp ON camp.id = ag.parent_id AND camp.kind = 'campaign'
  JOIN listing li ON li.id = pa.listing_id
 WHERE pa.platform = %s
   AND pa.kind = 'product_ad'
   AND pa.listing_id = ANY(%s)
   AND (%s::bigint IS NULL OR NOT EXISTS (
       SELECT 1 FROM campana_grupo_rol r
        WHERE r.grupo_id = %s AND r.ad_group_ad_entity_id = ag.id))
   AND NOT EXISTS (
       SELECT 1 FROM v_ad_group_impulso_abierto v WHERE v.ad_group_ad_entity_id = ag.id)
 ORDER BY pa.external_id
"""

_SQL_IMPRESIONES_DE_IMPULSO = """
SELECT 1 FROM impulso_lectura
 WHERE impulso_id = %s AND impresiones IS NOT NULL AND impresiones > 0
"""

_SQL_LISTINGS_DE_GRUPO = """
SELECT DISTINCT pa.listing_id FROM ad_entity pa
  JOIN campana_grupo_rol r ON r.ad_group_ad_entity_id = pa.parent_id
 WHERE r.grupo_id = %s AND pa.kind = 'product_ad' AND pa.listing_id IS NOT NULL
"""

_SQL_ANUNCIO_DE_DECISION = """
SELECT d.kind::text, pa.kind, pa.platform::text, pa.external_id, ag.external_id, ag.name,
       camp.external_id, li.seller_sku, li.external_id
  FROM decision d
  JOIN ad_entity pa ON pa.id = d.ad_entity_id
  LEFT JOIN ad_entity ag ON ag.id = pa.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity camp ON camp.id = ag.parent_id AND camp.kind = 'campaign'
  LEFT JOIN listing li ON li.id = pa.listing_id
 WHERE d.id = %s
"""

_SQL_GRUPO_DE_LOTE = """
SELECT id FROM campana_grupo WHERE lote = %s
"""

_SQL_AUTORIZA_DECISION = """
SELECT 1
  FROM decision d
  JOIN ad_entity pa ON pa.id = d.ad_entity_id AND pa.kind = 'product_ad'
  JOIN apply_queue q ON q.decision_id = d.id AND q.ad_entity_id = d.ad_entity_id
 WHERE d.id = %s
   AND q.id = %s
   AND d.ad_entity_id = %s
   AND pa.platform = %s
   AND d.kind = 'pause'
   AND q.kind = 'pause'
   AND q.estado = 'applying'
   AND q.modo = 'live'
   AND q.vetoed_at IS NULL
   AND q.vence_el <= now()
   AND EXISTS (SELECT 1 FROM apply_attempt a
                WHERE a.decision_id = d.id AND a.tipo = 'normal' AND a.quota_cobrada)
"""


def impulso_tuvo_impresiones(conn, impulso_id: int) -> bool:
    """Motivo 'impulso' por ruta: solo retira si el impulso ya midio."""
    return conn.execute(_SQL_IMPRESIONES_DE_IMPULSO, (impulso_id,)).fetchone() is not None


def grupo_alcanza_listings(conn, grupo_id: int, listing_ids) -> bool:
    """El lote a conservar es de estos productos."""
    lids = {f[0] for f in conn.execute(_SQL_LISTINGS_DE_GRUPO, (grupo_id,)).fetchall()}
    return not lids or bool(lids & set(listing_ids))


def planea_retiro(
    conn,
    plataforma: str,
    *,
    listing_ids: tuple[int, ...],
    motivo: MotivoRetiro,
    conservar_grupo_id: int | None,
    hoy: dt.date | None = None,
) -> PlanRetiro:
    """Los product ads ENABLED de esos listings en ad groups fuera del grupo a conservar."""
    if motivo not in get_args(MotivoRetiro):
        raise ValueError(f"motivo de retiro fuera de vocabulario: {motivo!r}")
    vendieron: tuple[int, ...] = ()
    if motivo == "sin_pedidos":
        hoy = hoy or hoy_resumen((plataforma,), dt.datetime.now(dt.UTC))
        libro = pp.ultimo_libro(conn)
        if libro is None or dia_local(plataforma, libro) < hoy - dt.timedelta(
            days=DIAS_LIBRO_AL_DIA
        ):
            return PlanRetiro(
                plataforma,
                motivo,
                (),
                _huella_plan(()),
                (),
                ventas_al_dia=False,
                ventas_al_dia_hasta=libro,
            )
        vendio = pp.vendio_por_listing(conn, plataforma, listing_ids, hoy)
        vendieron = tuple(sorted(lid for lid, si in vendio.items() if si))
        listing_ids = tuple(sorted(lid for lid, si in vendio.items() if not si))
    filas = conn.execute(
        _SQL_ANUNCIOS, (plataforma, list(listing_ids), conservar_grupo_id, conservar_grupo_id)
    ).fetchall()
    anuncios = tuple(
        im.AnuncioARetirar(
            ad_id_externo=f[1],
            ad_group_externo=f[2],
            campana_externa=f[4],
            ad_group_nombre=f[3] or "",
            sku=f[5],
            asin=f[6],
        )
        for f in filas
    )
    return PlanRetiro(plataforma, motivo, anuncios, _huella_plan(anuncios), vendieron)


def plan_de_decision(conn, decision_id: int) -> PlanRetiro:
    """El plan de retiro de una decision `pause` sobre un product_ad."""
    fila = conn.execute(_SQL_ANUNCIO_DE_DECISION, (decision_id,)).fetchone()
    conn.commit()
    if fila is None:
        raise ValueError(f"la decision {decision_id} no existe")
    kind, entidad, plataforma, ad_id, ag, ag_nombre, camp, sku, asin = fila
    if kind != "pause" or entidad != "product_ad":
        raise ValueError(
            f"la decision {decision_id} es {kind} sobre {entidad}, no pause de product_ad"
        )
    if ag is None or camp is None or sku is None or asin is None:
        raise ValueError(
            f"el product_ad {ad_id} de la decision {decision_id} sin ad group, campana o listing"
        )
    anuncios = (
        im.AnuncioARetirar(
            ad_id_externo=ad_id,
            ad_group_externo=ag,
            campana_externa=camp,
            ad_group_nombre=ag_nombre or "",
            sku=sku,
            asin=asin,
        ),
    )
    return PlanRetiro(plataforma, "producto_sin_ventas", anuncios, _huella_plan(anuncios))


def grupo_de_lote(conn, lote: str) -> int | None:
    """El grupo del lote, o None si no existe."""
    fila = conn.execute(_SQL_GRUPO_DE_LOTE, (lote,)).fetchone()
    return fila[0] if fila is not None else None


def _verifica_autorizacion(conn, plan: PlanRetiro, autorizacion: Autorizacion, candidatos) -> str:
    """Verifica autorizacion humana o de decision antes del primer PUT.

    Devuelve el modo autorizado que el escritor revalida: solo una autorizacion
    probada en vivo llega a `preparar_escritor` (ARREGLOS 05 D)."""
    if isinstance(autorizacion, AutorizacionHumana):
        if plan.motivo == "producto_sin_ventas":
            raise AutorizacionInvalida("producto_sin_ventas solo se retira con su decision")
        return archivar.MODO_AUTORIZADO_LIVE
    if not isinstance(autorizacion, AutorizacionDeDecision):
        raise AutorizacionInvalida(f"autorizacion desconocida: {type(autorizacion).__name__}")
    if plan.motivo != "producto_sin_ventas" or len(candidatos) != 1:
        raise AutorizacionInvalida(
            "una decision autoriza solo el retiro de su product_ad (producto_sin_ventas)"
        )
    ((ent, _anuncio),) = candidatos
    fila = conn.execute(
        _SQL_AUTORIZA_DECISION,
        (autorizacion.decision_id, autorizacion.queue_id, ent, plan.plataforma),
    ).fetchone()
    previa = conn.execute(
        "SELECT id FROM anuncio_retiro_intencion WHERE decision_id = %s AND cerrada_el IS NOT NULL",
        (autorizacion.decision_id,),
    ).fetchone()
    conn.commit()
    if previa is not None:
        raise AutorizacionInvalida(
            f"la decision {autorizacion.decision_id} ya cerro su intencion {previa[0]}:"
            " no hay segundo PUT para la misma decision"
        )
    if fila is None:
        raise AutorizacionInvalida(
            f"la decision {autorizacion.decision_id} (cola {autorizacion.queue_id}) no autoriza"
            " este retiro: exige pause sobre ese product_ad, cola applying en live, veto"
            " vencido y cuota cobrada"
        )
    return archivar.MODO_AUTORIZADO_LIVE


def ejecuta_retiro(
    conn,
    plan: PlanRetiro,
    *,
    autorizacion: Autorizacion,
    impulso_id: int | None = None,
    escritor=None,
) -> tuple[ResultadoAnuncio, ...]:
    """Pausa los anuncios del plan (modo 'pausa') con patron intencion/sello."""
    if (plan.motivo == "impulso") != (impulso_id is not None):
        raise ValueError("motivo 'impulso' exige impulso_id, y solo ese motivo lo trae")
    candidatos = []
    for anuncio in plan.anuncios:
        ent = conn.execute(
            _SQL_ENTIDAD_DE_ANUNCIO, (plan.plataforma, anuncio.ad_id_externo)
        ).fetchone()
        if ent is None:
            raise ValueError(f"anuncio {anuncio.ad_id_externo} sin fila en ad_entity")
        for campo in ("ad_group_externo", "campana_externa", "sku", "asin"):
            if not getattr(anuncio, campo):
                raise ValueError(
                    f"anuncio {anuncio.ad_id_externo} sin {campo}: valida antes de pausar"
                )
        candidatos.append((ent[0], anuncio))
    conn.commit()
    modo = _verifica_autorizacion(conn, plan, autorizacion, candidatos)
    if not candidatos:
        return ()
    if escritor is None:
        try:
            escritor = archivar.preparar_escritor(plan.plataforma, modo_confirmado=modo)
        except (ValueError, AdsApiError, AdsConfigError) as exc:
            raise RuntimeError(f"Amazon no disponible para retirar: {exc}") from exc
    decision_id = (
        autorizacion.decision_id if isinstance(autorizacion, AutorizacionDeDecision) else None
    )
    lote = lote_retiro_de_plan(plan)
    resultados: dict[str, ResultadoAnuncio] = {}
    with _bloqueados(conn, [f"retiro-ad:{ent}" for ent, _ in candidatos]):
        pendientes = []
        for ent, anuncio in candidatos:
            ya = conn.execute(_SQL_YA_RETIRADO, (ent,)).fetchone()
            if ya is None:
                pendientes.append((ent, anuncio))
                continue
            prev = conn.execute(_SQL_INTENCION_ABIERTA, (ent,)).fetchone()
            if prev is not None:
                _cierra_intencion(
                    conn, prev[0], _detalle_ya_sellado(ya, "muerte entre sello y cierre"), ya[0]
                )
            resultados[anuncio.ad_id_externo] = ResultadoAnuncio(
                anuncio.ad_id_externo, "ya_retirado", "ya tenia un retiro abierto", ya[0]
            )
        if pendientes:
            trabajo = []
            for ent, anuncio in pendientes:
                prev = conn.execute(_SQL_INTENCION_ABIERTA, (ent,)).fetchone()
                if prev is not None and prev[1] != decision_id:
                    resultados[anuncio.ad_id_externo] = ResultadoAnuncio(
                        anuncio.ad_id_externo,
                        "pendiente",
                        f"intencion {prev[0]} pendiente de otra autorizacion: la resuelve su dueno",
                        None,
                        prev[0],
                    )
                    continue
                trabajo.append((ent, anuncio, prev[0] if prev is not None else None))
            pendientes = [(ent, a) for ent, a, _ in trabajo]
            filas = (
                archivar.leer_anuncios(escritor, [a.ad_id_externo for _, a in pendientes])
                if pendientes
                else {}
            )
            for res in _ejecuta_pendientes(
                conn,
                plan,
                trabajo,
                filas,
                lote,
                impulso_id,
                escritor,
                _actor_de(autorizacion),
                decision_id,
            ):
                resultados[res.ad_id_externo] = res
    return tuple(resultados[a.ad_id_externo] for _, a in candidatos)


def _ejecuta_pendientes(
    conn, plan, pendientes, filas, lote, impulso_id, escritor, actor_final, decision_id
) -> list[ResultadoAnuncio]:
    """Intencion-antes-del-PUT por anuncio (llamador con locks)."""
    resultados: list[ResultadoAnuncio] = []
    for ent, anuncio, previo in pendientes:
        ad_id = anuncio.ad_id_externo
        fila = filas.get(ad_id)
        if fila is None or fila.get("state") == archivar.ESTADO_ARCHIVADO:
            if previo is None:
                if fila is None:
                    resultados.append(
                        ResultadoAnuncio(
                            ad_id, "no_existe", "no_existe en Amazon: no se mando nada"
                        )
                    )
                else:
                    resultados.append(
                        ResultadoAnuncio(ad_id, "archivado", "ARCHIVED: el PUT no lo mueve")
                    )
                continue
            detalle_cierre = (
                f"{ad_id} inexistente en Amazon: nada que pausar"
                if fila is None
                else f"{ad_id} ARCHIVED en Amazon: el PUT no lo mueve"
            )
            _cierra_intencion(conn, previo, detalle_cierre, None)
            resultados.append(ResultadoAnuncio(ad_id, "cerrado", detalle_cierre, None, previo))
            continue
        if previo is None:
            reversa = archivar.reversa_de_fila(fila)
            iid = _abre_intencion(
                conn, plan, lote, impulso_id, ent, anuncio, reversa, actor_final, decision_id
            )
        else:
            iid = previo
            (reversa,) = conn.execute(
                "SELECT reversa FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
            ).fetchone()
        from app.cycle import ApplyAbortado

        try:
            res = archivar.pausar_anuncios(escritor, [ad_id], ejecutar=True)[0]
        except ApplyAbortado:
            raise
        except Exception as exc:
            if (
                decision_id is not None
                and transicion_desde_error(EstadoCambio.INCIERTO, exc).propaga_aborto
            ):
                raise
            resultados.append(
                ResultadoAnuncio(
                    ad_id,
                    "pendiente",
                    f"PUT incierto, intencion {iid} pendiente: {scrub(str(exc))}",
                    None,
                    iid,
                )
            )
            continue
        detalle = res.detalle if res.detalle else res.resultado
        if (
            decision_id is not None
            and transicion_desde_status(EstadoCambio.INCIERTO, res.status_http).propaga_aborto
        ):
            # Solo en cortes automaticos (AutorizacionDeDecision): el barrido
            # aborta la fase y deja la intencion abierta (resuelve_intencion_de_decision
            # solo lee y nunca reenvia). En retiros humanos (decision_id None)
            # el rechazo explicito cierra la intencion y responde por anuncio.
            error = AdsApiError(f"status={res.status_http}: {scrub(detalle)}")
            error.status = res.status_http
            raise error
        if res.resultado in ("pausado", "ya_estaba"):
            propio = _put_propio(res)
            rid = _sella_retiro(
                conn,
                plan,
                lote,
                impulso_id,
                ent,
                anuncio,
                reversa,
                actor_final,
                "confirmada" if propio else "adoptada",
                cerrar_intencion=(iid, "sello del readback" if propio else _DETALLE_ADOPTADO),
            )
            resultados.append(
                ResultadoAnuncio(ad_id, "retirado" if propio else "adoptado", res.detalle, rid, iid)
            )
        elif res.resultado in ("fallo", "no_existe"):
            _cierra_intencion(conn, iid, detalle, None)
            resultados.append(
                ResultadoAnuncio(
                    ad_id,
                    "rechazado",
                    f"{res.resultado}, intencion {iid} cerrada: {detalle}",
                    None,
                    iid,
                )
            )
        else:
            resultados.append(
                ResultadoAnuncio(
                    ad_id,
                    "pendiente",
                    f"{res.resultado}, intencion {iid} pendiente: {detalle}",
                    None,
                    iid,
                )
            )
    return resultados
