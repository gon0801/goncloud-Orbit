"""Reconciliacion de intenciones pendientes de retiro y calculo de pendientes de impulso.

Extraido de `app/retiro_anuncios.py` (Ola D3a). Comparte la llave de sesion y el
sello atomico de `app/retiro_anuncios_base.py`.
"""

from __future__ import annotations

import sys

from app.ads import archivar
from app.ads.client import AdsApiError
from app.redaction import scrub
from app.retiro_anuncios_base import (
    _DETALLE_ADOPTADO,
    _SQL_ENTIDAD_DE_ANUNCIO,
    _SQL_YA_RETIRADO,
    PlanRetiro,
    ResultadoIntencion,
    _bloqueados,
    _cierra_intencion,
    _detalle_ya_sellado,
    _huella_plan,
    _put_propio,
    _sella_desde_intencion,
)

_SQL_INTENCIONES_PENDIENTES = """
SELECT i.id, i.platform, i.motivo, i.lote_retiro, i.impulso_id, i.ad_entity_id,
       i.ad_id_externo, i.ad_group_externo, i.campana_externa, i.sku, i.asin,
       i.reversa, i.actor
  FROM anuncio_retiro_intencion i
 WHERE i.cerrada_el IS NULL
   AND i.decision_id IS NULL
 ORDER BY i.id
"""

_SQL_INTENCION_DE_DECISION = """
SELECT i.id, i.platform, i.motivo, i.lote_retiro, i.impulso_id, i.ad_entity_id,
       i.ad_id_externo, i.ad_group_externo, i.campana_externa, i.sku, i.asin,
       i.reversa, i.actor, i.cerrada_el IS NOT NULL, i.retiro_id
  FROM anuncio_retiro_intencion i
 WHERE i.decision_id = %s
"""


def reconcilia_intenciones(conn, *, escritor=None, escritores=None) -> list[tuple[int, str]]:
    """Paso diario: resuelve toda intencion pendiente de un motivo humano leyendo Amazon."""
    resueltas: list[tuple[int, str]] = []
    filas = conn.execute(_SQL_INTENCIONES_PENDIENTES).fetchall()
    conn.commit()
    propios: dict[str, object] = dict(escritores or {})
    for fila in filas:
        (
            iid,
            platform,
            motivo,
            lote,
            impulso_id,
            ent,
            ad_id,
            ag,
            camp,
            sku,
            asin,
            reversa,
            actor,
        ) = fila
        try:
            with _bloqueados(conn, [f"retiro-ad:{ent}"]):
                vigente = conn.execute(
                    "SELECT 1 FROM anuncio_retiro_intencion WHERE id = %s AND cerrada_el IS NULL",
                    (iid,),
                ).fetchone()
                if vigente is None:
                    continue
                ya = conn.execute(_SQL_YA_RETIRADO, (ent,)).fetchone()
                if ya is not None:
                    _cierra_intencion(conn, iid, _detalle_ya_sellado(ya, "reconciliacion"), ya[0])
                    resueltas.append((iid, "sellado"))
                    continue
                esc = escritor if escritor is not None else propios.get(platform)
                if esc is None:
                    esc = archivar.preparar_escritor(platform)
                    propios[platform] = esc
                try:
                    leidas = archivar.leer_anuncios(esc, [ad_id])
                except AdsApiError as exc:
                    print(
                        f"reconcilia retiros: LIST caido para {ad_id} ({scrub(str(exc))})",
                        file=sys.stderr,
                    )
                    continue
                vista = leidas.get(ad_id)
                if vista is None or vista.get("state") == archivar.ESTADO_ARCHIVADO:
                    detalle = (
                        f"{ad_id} inexistente en Amazon: nada que pausar"
                        if vista is None
                        else f"{ad_id} ARCHIVED en Amazon: el PUT no lo mueve"
                    )
                    _cierra_intencion(conn, iid, f"reconciliacion: {detalle}", None)
                    resueltas.append((iid, "cerrado"))
                    continue
                if vista.get("state") == archivar.ESTADO_PAUSADO:
                    _sella_desde_intencion(
                        conn,
                        platform,
                        motivo,
                        lote,
                        impulso_id,
                        ent,
                        ad_id,
                        ag,
                        camp,
                        sku,
                        asin,
                        reversa,
                        actor,
                        "adoptada",
                        cerrar_intencion=(iid, f"{_DETALLE_ADOPTADO} (reconciliacion)"),
                    )
                    resueltas.append((iid, "sellado"))
                    continue
                res = archivar.pausar_anuncios(esc, [ad_id], ejecutar=True)[0]
                if res.resultado in ("pausado", "ya_estaba"):
                    propio = _put_propio(res)
                    _sella_desde_intencion(
                        conn,
                        platform,
                        motivo,
                        lote,
                        impulso_id,
                        ent,
                        ad_id,
                        ag,
                        camp,
                        sku,
                        asin,
                        reversa,
                        actor,
                        "confirmada" if propio else "adoptada",
                        cerrar_intencion=(
                            iid,
                            "reconciliacion: reintento sello"
                            if propio
                            else f"{_DETALLE_ADOPTADO} (reconciliacion)",
                        ),
                    )
                    resueltas.append((iid, "sellado"))
                elif res.resultado in ("fallo", "no_existe"):
                    _cierra_intencion(
                        conn, iid, f"reconciliacion: {res.resultado} ({res.detalle})", None
                    )
                    resueltas.append((iid, "cerrado"))
        except Exception as exc:
            conn.rollback()
            print(
                f"reconcilia retiros: intencion {iid} fallo ({scrub(str(exc))})",
                file=sys.stderr,
            )
    return resueltas


def resuelve_intencion_de_decision(
    conn, decision_id: int, escritor
) -> tuple[ResultadoIntencion, int | None]:
    """AUTO-03 I.3: la intencion de retiro de una decision `pause` sobre un
    product_ad. Nunca manda un PUT."""
    fila = conn.execute(_SQL_INTENCION_DE_DECISION, (decision_id,)).fetchone()
    conn.commit()
    if fila is None:
        return ("sin_intencion", None)
    iid, platform, motivo, lote, impulso_id, ent, ad_id, ag, camp, sku, asin = fila[:11]
    reversa, actor, cerrada, retiro_id = fila[11:]
    if cerrada:
        return ("sellado", retiro_id) if retiro_id is not None else ("cerrado", None)
    with _bloqueados(conn, [f"retiro-ad:{ent}"]):
        vigente = conn.execute(
            "SELECT retiro_id FROM anuncio_retiro_intencion"
            " WHERE id = %s AND cerrada_el IS NOT NULL",
            (iid,),
        ).fetchone()
        if vigente is not None:
            conn.commit()
            return ("sellado", vigente[0]) if vigente[0] is not None else ("cerrado", None)
        ya = conn.execute(_SQL_YA_RETIRADO, (ent,)).fetchone()
        if ya is not None:
            _cierra_intencion(conn, iid, _detalle_ya_sellado(ya, "reconciliacion"), ya[0])
            return ("sellado", ya[0])
        conn.commit()
        try:
            vista = archivar.leer_anuncios(escritor, [ad_id]).get(ad_id)
        except AdsApiError as exc:
            print(
                f"reconcilia pausa de producto: LIST caido para {ad_id} ({scrub(str(exc))})",
                file=sys.stderr,
            )
            return ("pendiente", None)
        estado = vista.get("state") if vista is not None else None
        if estado == archivar.ESTADO_PAUSADO:
            rid = _sella_desde_intencion(
                conn,
                platform,
                motivo,
                lote,
                impulso_id,
                ent,
                ad_id,
                ag,
                camp,
                sku,
                asin,
                reversa,
                actor,
                "adoptada",
                cerrar_intencion=(iid, f"{_DETALLE_ADOPTADO} (reconciliacion)"),
            )
            return ("sellado", rid)
        detalle = {
            None: f"{ad_id} inexistente en Amazon: nada que pausar",
            archivar.ESTADO_ARCHIVADO: f"{ad_id} ARCHIVED en Amazon: el PUT no lo mueve",
        }.get(estado, f"{ad_id} {estado} en Amazon: la decision no reenvia el PUT")
        _cierra_intencion(conn, iid, f"reconciliacion: {detalle}", None)
        return ("cerrado", None)


def retiros_pendientes_de_impulso(conn, impulso_id: int, plan: PlanRetiro) -> PlanRetiro:
    """El plan de retiro del impulso menos los anuncios que ya tienen un retiro abierto."""
    if not plan.anuncios:
        return plan
    pendientes = []
    for anuncio in plan.anuncios:
        ent = conn.execute(
            _SQL_ENTIDAD_DE_ANUNCIO, (plan.plataforma, anuncio.ad_id_externo)
        ).fetchone()
        if ent is None or conn.execute(_SQL_YA_RETIRADO, (ent[0],)).fetchone() is None:
            pendientes.append(anuncio)
    conn.commit()
    if len(pendientes) == len(plan.anuncios):
        return plan
    resto = tuple(pendientes)
    return PlanRetiro(plan.plataforma, plan.motivo, resto, _huella_plan(resto))
