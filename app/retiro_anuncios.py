"""Retiro de product ads de bolsas compartidas, con reposicion (BIDS 02, I.4).

Un producto que entra en impulso sale de sus bolsas viejas cuando sus
campanas nuevas ya tienen impresiones (primero nace lo nuevo, despues
sale de lo viejo: nunca queda sin anuncio). El retiro PAUSA (sonda 1
de V.0 sellada: PUT /sp/productAds) y la reposicion REACTIVA el mismo
adId. Si el anuncio se archivo por fuera entre el retiro y la vuelta,
la reposicion lo recrea con la linea guardada.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from contextlib import contextmanager
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
   AND NOT EXISTS (
       SELECT 1 FROM campana_grupo_rol r2
        JOIN impulso i2 ON i2.grupo_id = r2.grupo_id
        LEFT JOIN impulso_lectura l2 ON l2.impulso_id = i2.id AND l2.es_final
       WHERE r2.ad_group_ad_entity_id = ag.id AND l2.impulso_id IS NULL)
 ORDER BY pa.external_id
"""


_SQL_IMPRESIONES_DE_IMPULSO = """
SELECT 1 FROM impulso_lectura
 WHERE impulso_id = %s AND impresiones IS NOT NULL AND impresiones > 0
"""


def impulso_tuvo_impresiones(conn, impulso_id: int) -> bool:
    """Motivo 'impulso' por ruta: solo retira si el impulso ya midio
    (el vigia dispara con el primer dia con impresiones)."""
    return conn.execute(_SQL_IMPRESIONES_DE_IMPULSO, (impulso_id,)).fetchone() is not None


_SQL_LISTINGS_DE_GRUPO = """
SELECT DISTINCT pa.listing_id FROM ad_entity pa
  JOIN campana_grupo_rol r ON r.ad_group_ad_entity_id = pa.parent_id
 WHERE r.grupo_id = %s AND pa.kind = 'product_ad' AND pa.listing_id IS NOT NULL
"""


def grupo_alcanza_listings(conn, grupo_id: int, listing_ids) -> bool:
    """El lote a conservar es de estos productos: sin ads (nada que
    proteger) o con algun ad de los listings."""
    lids = {f[0] for f in conn.execute(_SQL_LISTINGS_DE_GRUPO, (grupo_id,)).fetchall()}
    return not lids or bool(lids & set(listing_ids))


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


@contextmanager
def _bloqueados(conn, llaves):
    """Advisory locks de SESION, ordenados (sobreviven los commits y
    cubren el HTTP entre lecturas y escrituras). Se sueltan al salir;
    si la conexion muere, PostgreSQL los libera."""
    for llave in sorted(set(llaves)):
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (llave,))
    try:
        yield
    finally:
        for llave in sorted(set(llaves)):
            with contextlib.suppress(Exception):
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (llave,))


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
    `anuncio_retiro` (modo 'pausa'). La fila se escribe ANTES de su PUT
    (append-only: sin UPDATE ni DELETE) con la reversa de la lectura
    previa: si el PUT es incierto (red o relectura caida), la fila
    conservada es lo que permite reponerlo despues. Reanudable: salta
    los que ya tienen un retiro abierto. Todo se valida antes del
    primer PUT (entidad, campos NOT NULL, existencia en Amazon). Un
    rechazo limpio deja su fila (visible; reponer la cierra) y se
    nombra al final. Devuelve cuantos retiro."""
    if not go or not go.strip():
        raise ValueError("ejecutar un retiro exige el literal go del dueno")
    if (plan.motivo == "impulso") != (impulso_id is not None):
        raise ValueError("motivo 'impulso' exige impulso_id, y solo ese motivo lo trae")
    if escritor is None:
        escritor = archivar.preparar_escritor(plan.plataforma)
    actor_final = actor if actor is not None else go
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
    if not candidatos:
        return 0
    lote = lote_retiro_de_plan(plan)
    with _bloqueados(conn, [f"retiro-ad:{ent}" for ent, _ in candidatos]):
        # Re-chequeo bajo lock: otro pudo retirar mientras se esperaba.
        pendientes = [
            (ent, anuncio)
            for ent, anuncio in candidatos
            if conn.execute(_SQL_YA_RETIRADO, (ent,)).fetchone() is None
        ]
        if not pendientes:
            return 0
        # Lectura previa global ANTES del primer PUT: existencia y reversas.
        filas = archivar.leer_anuncios(escritor, [a.ad_id_externo for _, a in pendientes])
        retirados, fallidos = _ejecuta_pendientes(
            conn, plan, pendientes, filas, lote, impulso_id, escritor, actor_final
        )
    if fallidos:
        raise RuntimeError(f"retiro no confirmado ({retirados} registrados): {fallidos}")
    return retirados


def _ejecuta_pendientes(conn, plan, pendientes, filas, lote, impulso_id, escritor, actor_final):
    """INSERT-antes-del-PUT por anuncio (llamador con locks): conserva
    la fila en lo incierto y nombra al final."""
    retirados = 0
    fallidos: list[str] = []
    for ent, anuncio in pendientes:
        fila = filas.get(anuncio.ad_id_externo)
        if fila is None:
            fallidos.append(f"{anuncio.ad_id_externo} (no_existe en Amazon: no se mando nada)")
            continue
        if fila.get("state") == archivar.ESTADO_ARCHIVADO:
            fallidos.append(f"{anuncio.ad_id_externo} (ARCHIVED: el PUT no lo mueve)")
            continue
        rid = conn.execute(
            "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, retirado_el, actor) VALUES (%s, %s, 'pausa', %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, now(), %s) RETURNING id",
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
                archivar.reversa_de_fila(fila),
                actor_final,
            ),
        ).fetchone()[0]
        conn.commit()
        try:
            res = archivar.pausar_anuncios(escritor, [anuncio.ad_id_externo], ejecutar=True)[0]
        except Exception as exc:
            fallidos.append(f"{anuncio.ad_id_externo} (PUT incierto, fila {rid} conservada: {exc})")
            continue
        if res.resultado in ("pausado", "ya_estaba"):
            retirados += 1
        else:  # fallo, no_existe, sin_confirmar: la fila queda (append-only)
            detalle = res.detalle if res.detalle else res.resultado
            fallidos.append(
                f"{anuncio.ad_id_externo} ({res.resultado}, fila {rid} conservada: {detalle})"
            )
    return retirados, fallidos


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
    group del que salio, con el estado que tenia (el `state` de la linea
    guardada). Lee el estado actual: PAUSED se reactiva (mismo adId)
    solo si estaba ENABLED; si ya esta como estaba, solo cierra;
    ARCHIVED se recrea con la linea guardada (que trae su state).
    Uno que falla no bloquea a los demas: se nombran al final.
    Una segunda llamada no repone otra vez. Devuelve cuantos repuso."""
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
    plataformas = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT platform::text FROM anuncio_retiro WHERE id = ANY(%s)",
            ([f[0] for f in pendientes],),
        ).fetchall()
    }
    conn.commit()
    if len(plataformas) > 1:
        raise ValueError(f"reponer mezcla plataformas {sorted(plataformas)}: una por llamada")
    if escritor is None:
        plataforma = next(iter(plataformas))
        escritor = archivar.preparar_escritor(plataforma)
    with _bloqueados(conn, [f"repone-rid:{rid}" for rid in retiro_ids]):
        # Re-lee bajo lock: otro pudo reponer mientras se esperaba.
        filas = conn.execute(_SQL_RETIROS, (list(retiro_ids),)).fetchall()
        pendientes = [f for f in filas if not f[4]]
        if not pendientes:
            return 0
        estados = archivar.estados_actuales(escritor, [f[1] for f in pendientes])
        repuestos = 0
        fallidos: list[str] = []
        for rid, ad_id, reversa, _ent, _rep in pendientes:
            try:
                nuevo, estado_nuevo = _repone_uno(escritor, ad_id, reversa, estados.get(ad_id))
            except Exception as exc:
                fallidos.append(f"{ad_id} ({exc})")
                continue
            conn.execute(
                "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo,"
                " repuesto_el, actor) VALUES (%s, %s, %s, now(), %s)",
                (rid, nuevo, estado_nuevo, actor_final),
            )
            conn.commit()
            repuestos += 1
    if fallidos:
        raise RuntimeError(f"reposicion parcial ({repuestos} repuestos): {fallidos}")
    return repuestos


def _repone_uno(escritor, ad_id, reversa, estado):
    """Devuelve el anuncio a su estado previo (mismo adId o recreado).
    Levanta si no se confirma. Reversa legacy (sin `state`): previo
    desconocido, se sigue el camino historico (PAUSED reactiva)."""
    try:
        previo = archivar.parsear_reversa(reversa).get("state")
    except archivar.ListaInvalida:
        previo = None
    if estado == "PAUSED" and previo == archivar.ESTADO_PAUSADO:
        return ad_id, "PAUSED"
    if estado == "PAUSED":
        res = archivar.reactivar_anuncios(escritor, [ad_id], ejecutar=True)
        if not res or res[0].resultado != "reactivado":
            detalle = res[0].detalle if res else "sin resultado"
            raise RuntimeError(f"reposicion de {ad_id} no confirmada: {detalle}")
        return ad_id, "ENABLED"
    if estado == "ENABLED" and previo == archivar.ESTADO_PAUSADO:
        res = archivar.pausar_anuncios(escritor, [ad_id], ejecutar=True)
        if not res or res[0].resultado != "pausado":
            detalle = res[0].detalle if res else "sin resultado"
            raise RuntimeError(f"reposicion de {ad_id} no confirmada: {detalle}")
        return ad_id, "PAUSED"
    if estado == "ARCHIVED":
        res = archivar.reponer_anuncios(escritor, [reversa], ejecutar=True)
        if not res or res[0].resultado != "repuesto":
            detalle = res[0].detalle if res else "sin resultado"
            raise RuntimeError(f"reposicion de {ad_id} no confirmada: {detalle}")
        return res[0].ad_id, res[0].estado_previo
    if estado == "ENABLED":
        return ad_id, "ENABLED"
    raise RuntimeError(f"reposicion de {ad_id}: estado {estado!r} sin camino")


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
