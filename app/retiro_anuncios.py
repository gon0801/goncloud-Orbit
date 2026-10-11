"""Retiro de product ads de bolsas compartidas, con reposicion (BIDS 02, I.4).

Un producto que entra en impulso sale de sus bolsas viejas cuando sus
campanas nuevas ya tienen impresiones (primero nace lo nuevo, despues
sale de lo viejo: nunca queda sin anuncio). El retiro PAUSA (sonda 1
de V.0 sellada: PUT /sp/productAds) y la reposicion REACTIVA el mismo
adId. Si el anuncio se archivo por fuera entre el retiro y la vuelta,
la reposicion lo recrea con la linea guardada.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from app import impulso as im
from app.ads import archivar

MotivoRetiro = Literal["impulso", "sin_pedidos", "subfamilia"]


@dataclass(frozen=True)
class PlanRetiro:
    plataforma: str
    motivo: str
    anuncios: tuple[im.AnuncioARetirar, ...]
    huella: str

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "motivo": self.motivo,
            "huella": self.huella,
            "anuncios": [
                {
                    "ad_id_externo": a.ad_id_externo,
                    "ad_group_externo": a.ad_group_externo,
                    "campana_externa": a.campana_externa,
                    "ad_group_nombre": a.ad_group_nombre,
                    "sku": a.sku,
                    "asin": a.asin,
                }
                for a in self.anuncios
            ],
        }


def _huella_plan(anuncios: tuple[im.AnuncioARetirar, ...]) -> str:
    canon = json.dumps(
        [[a.ad_id_externo, a.ad_group_externo, a.campana_externa, a.sku, a.asin] for a in anuncios],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


_SQL_ANUNCIOS = """
SELECT pa.id, pa.external_id, ag.external_id, ag.name, camp.external_id,
       li.seller_sku, li.external_id
  FROM ad_entity pa
  JOIN ad_entity_state es ON es.ad_entity_id = pa.id AND es.status = 'ENABLED'
  JOIN ad_entity ag ON ag.id = pa.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity camp ON camp.id = ag.parent_id AND camp.kind = 'campaign'
  JOIN listing li ON li.id = pa.listing_id
 WHERE pa.platform = %s
   AND pa.kind = 'product_ad'
   AND pa.listing_id = ANY(%s)
   AND (%s::bigint IS NULL OR NOT EXISTS (
       SELECT 1 FROM campana_grupo_rol r
        WHERE r.grupo_id = %s AND r.ad_group_ad_entity_id = ag.id))
 ORDER BY pa.external_id
"""


def planea_retiro(
    conn,
    plataforma: str,
    *,
    listing_ids: tuple[int, ...],
    motivo: MotivoRetiro,
    conservar_grupo_id: int | None,
) -> PlanRetiro:
    """Los product ads ENABLED de esos listings en ad groups que NO son
    del grupo a conservar. Solo SELECT. Los anuncios sin listing quedan
    fuera (no se buscan por ASIN en este paso)."""
    if motivo not in ("impulso", "sin_pedidos", "subfamilia"):
        raise ValueError(f"motivo de retiro fuera de vocabulario: {motivo!r}")
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
    return PlanRetiro(plataforma, motivo, anuncios, _huella_plan(anuncios))


def lote_retiro_de_plan(plan: PlanRetiro) -> str:
    return f"retiro-{plan.huella[:12]}"


_SQL_GRUPO_DE_LOTE = """
SELECT id FROM campana_grupo WHERE lote = %s
"""


def grupo_de_lote(conn, lote: str) -> int | None:
    """El grupo del lote, o None si no existe. Resuelve en el servidor
    el `lote` que mandan las pantallas (el cliente nunca manda ids de
    grupo)."""
    fila = conn.execute(_SQL_GRUPO_DE_LOTE, (lote,)).fetchone()
    return fila[0] if fila is not None else None


_SQL_YA_RETIRADO = """
SELECT r.id FROM anuncio_retiro r
 WHERE r.ad_entity_id = %s
   AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id)
"""

_SQL_ENTIDAD_DE_ANUNCIO = """
SELECT pa.id FROM ad_entity pa
 WHERE pa.platform = %s AND pa.kind = 'product_ad' AND pa.external_id = %s
"""


def ejecuta_retiro(
    conn,
    plan: PlanRetiro,
    *,
    go: str,
    impulso_id: int | None = None,
    escritor=None,
    actor: str | None = None,
) -> int:
    """Pausa los anuncios del plan y deja una fila por anuncio en
    `anuncio_retiro` (modo 'pausa'). Reanudable: salta los que ya
    tienen un retiro abierto. Si alguno no se confirma, registra los
    confirmados y falla al final nombrando a los otros. Devuelve
    cuantos retiro."""
    if not go or not go.strip():
        raise ValueError("ejecutar un retiro exige el literal go del dueno")
    if (plan.motivo == "impulso") != (impulso_id is not None):
        raise ValueError("motivo 'impulso' exige impulso_id, y solo ese motivo lo trae")
    if escritor is None:
        escritor = archivar.preparar_escritor(plan.plataforma)
    actor_final = actor if actor is not None else go
    pendientes = []
    for anuncio in plan.anuncios:
        ent = conn.execute(
            _SQL_ENTIDAD_DE_ANUNCIO, (plan.plataforma, anuncio.ad_id_externo)
        ).fetchone()
        if ent is None:
            raise ValueError(f"anuncio {anuncio.ad_id_externo} sin fila en ad_entity")
        if conn.execute(_SQL_YA_RETIRADO, (ent[0],)).fetchone() is None:
            pendientes.append((ent[0], anuncio))
    conn.commit()
    if not pendientes:
        return 0
    resultados = archivar.pausar_anuncios(
        escritor, [a.ad_id_externo for _, a in pendientes], ejecutar=True
    )
    por_id = {r.ad_id: r for r in resultados}
    lote = lote_retiro_de_plan(plan)
    retirados = 0
    fallidos: list[str] = []
    for ent, anuncio in pendientes:
        res = por_id.get(anuncio.ad_id_externo)
        if res is None or res.resultado != "pausado":
            detalle = res.detalle if res is not None else "sin resultado"
            fallidos.append(f"{anuncio.ad_id_externo} ({detalle})")
            continue
        conn.execute(
            "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, retirado_el, actor) VALUES (%s, %s, 'pausa', %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, now(), %s)",
            (
                plan.plataforma,
                plan.motivo,
                lote,
                impulso_id,
                ent,
                anuncio.ad_id_externo,
                anuncio.ad_group_externo,
                anuncio.campana_externa,
                anuncio.sku,
                anuncio.asin,
                res.reversa,
                actor_final,
            ),
        )
        conn.commit()
        retirados += 1
    if fallidos:
        raise RuntimeError(f"retiro no confirmado ({retirados} registrados): {fallidos}")
    return retirados


_SQL_RETIROS = """
SELECT r.id, r.ad_id_externo, r.reversa, r.ad_entity_id,
       (p.retiro_id IS NOT NULL) AS repuesto
  FROM anuncio_retiro r
  LEFT JOIN anuncio_reposicion p ON p.retiro_id = r.id
 WHERE r.id = ANY(%s)
"""


def repone(
    conn, *, retiro_ids: tuple[int, ...], go: str, escritor=None, actor: str | None = None
) -> int:
    """La reversa (regla 7): deja al producto con un anuncio en cada ad
    group del que salio, con el estado que tenia. Lee el estado actual:
    PAUSED se reactiva (mismo adId); ARCHIVED se recrea con la linea
    guardada; ENABLED ya esta como debe y solo cierra el retiro. Una
    segunda llamada no repone otra vez. Devuelve cuantos repuso."""
    if not go or not go.strip():
        raise ValueError("reponer exige el literal go del dueno")
    actor_final = actor if actor is not None else go
    filas = conn.execute(_SQL_RETIROS, (list(retiro_ids),)).fetchall()
    conn.commit()
    if len(filas) != len(set(retiro_ids)):
        raise ValueError("algun retiro pedido no existe")
    pendientes = [f for f in filas if not f[4]]
    if not pendientes:
        return 0
    if escritor is None:
        plataforma = conn.execute(
            "SELECT platform::text FROM anuncio_retiro WHERE id = %s", (pendientes[0][0],)
        ).fetchone()[0]
        conn.commit()
        escritor = archivar.preparar_escritor(plataforma)
    estados = archivar.estados_actuales(escritor, [f[1] for f in pendientes])
    repuestos = 0
    for rid, ad_id, reversa, _ent, _rep in pendientes:
        estado = estados.get(ad_id)
        if estado == "PAUSED":
            res = archivar.reactivar_anuncios(escritor, [ad_id], ejecutar=True)
            if not res or res[0].resultado != "reactivado":
                detalle = res[0].detalle if res else "sin resultado"
                raise RuntimeError(f"reposicion de {ad_id} no confirmada: {detalle}")
            nuevo, estado_nuevo = ad_id, "ENABLED"
        elif estado == "ARCHIVED":
            res = archivar.reponer_anuncios(escritor, [reversa], ejecutar=True)
            if not res or res[0].resultado != "repuesto":
                detalle = res[0].detalle if res else "sin resultado"
                raise RuntimeError(f"reposicion de {ad_id} no confirmada: {detalle}")
            nuevo, estado_nuevo = res[0].ad_id, "PAUSED"
        elif estado == "ENABLED":
            nuevo, estado_nuevo = ad_id, "ENABLED"
        else:
            raise RuntimeError(f"reposicion de {ad_id}: estado {estado!r} sin camino")
        conn.execute(
            "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, repuesto_el,"
            " actor) VALUES (%s, %s, %s, now(), %s)",
            (rid, nuevo, estado_nuevo, actor_final),
        )
        conn.commit()
        repuestos += 1
    return repuestos


def retiros_pendientes_de_impulso(conn, impulso_id: int, plan: PlanRetiro) -> PlanRetiro:
    """El plan de retiro del impulso menos los anuncios que ya tienen un
    retiro abierto."""
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
