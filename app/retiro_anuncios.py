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
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from app import impulso as im
from app.ads import archivar
from app.ads.client import AdsApiError
from app.ads.config import AdsConfigError

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


_SQL_INTENCION_ABIERTA = """
SELECT i.id FROM anuncio_retiro_intencion i
 WHERE i.ad_entity_id = %s
   AND i.cerrada_el IS NULL
"""


def _abre_intencion(conn, plan, lote, impulso_id, ent, anuncio, reversa, actor: str) -> int:
    """Intencion ANTES del PUT (tabla propia 0069): si el PUT es
    incierto o el proceso muere, el reintento la retoma. Una
    pendiente por anuncio (UNIQUE parcial)."""
    iid = conn.execute(
        "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, actor, creada_el) VALUES (%s, %s, 'pausa', %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, now()) RETURNING id",
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
            reversa,
            actor,
        ),
    ).fetchone()[0]
    conn.commit()
    return iid


def _sella_retiro(conn, plan, lote, impulso_id, ent, anuncio, reversa, actor: str) -> int:
    """Fila de retiro SOLO tras el readback que confirma (toda fila de
    anuncio_retiro es un retiro sellado: 'fila = retirado')."""
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
            reversa,
            actor,
        ),
    ).fetchone()[0]
    conn.commit()
    return rid


def _cierra_intencion(conn, iid: int, detalle: str, retiro_id: int | None) -> None:
    """Cierra la intencion (solo-cierre 0069): con el retiro que sello
    si el readback confirmo, o sin retiro si se sabe que Amazon no
    cambio (rechazo limpio). Se nombra igual (no se retiro)."""
    conn.execute(
        "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = %s,"
        " retiro_id = %s WHERE id = %s",
        (detalle, retiro_id, iid),
    )
    conn.commit()


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
    """Pausa los anuncios del plan (modo 'pausa'). Patron intencion /
    sello (0069, tabla propia): la intencion se escribe ANTES de su
    PUT con la reversa de la lectura previa; la fila de
    `anuncio_retiro` solo se escribe tras el readback que confirma
    (toda fila es un retiro sellado). Si el PUT es incierto o el
    proceso muere, la intencion pendiente es lo que el reintento
    retoma (manda el PUT sobre la misma intencion). Reanudable: salta
    los que ya tienen un retiro abierto. Todo se valida antes del
    primer PUT (entidad, campos NOT NULL, existencia en Amazon). Un
    rechazo limpio cierra su intencion sin fila de retiro y se nombra
    al final. Devuelve cuantos retiro."""
    if not go or not go.strip():
        raise ValueError("ejecutar un retiro exige el literal go del dueno")
    if (plan.motivo == "impulso") != (impulso_id is not None):
        raise ValueError("motivo 'impulso' exige impulso_id, y solo ese motivo lo trae")
    if escritor is None:
        try:
            escritor = archivar.preparar_escritor(plan.plataforma)
        except (ValueError, AdsApiError, AdsConfigError) as exc:
            raise RuntimeError(f"Amazon no disponible para retirar: {exc}") from exc
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
        # 10 n/q: si ya hay retiro abierto Y una intencion pendiente
        # (muerte entre el sello y el cierre), la intencion se cierra
        # apuntando al retiro: el reintento converge sin PUT ni fila.
        pendientes = []
        for ent, anuncio in candidatos:
            ya = conn.execute(_SQL_YA_RETIRADO, (ent,)).fetchone()
            if ya is None:
                pendientes.append((ent, anuncio))
                continue
            prev = conn.execute(_SQL_INTENCION_ABIERTA, (ent,)).fetchone()
            if prev is not None:
                _cierra_intencion(
                    conn, prev[0], f"retiro {ya[0]} ya sellado (muerte entre sello y cierre)", ya[0]
                )
        if not pendientes:
            return 0
        # Intencion pendiente de un intento anterior: se retoma (mismo
        # iid, sin INSERT); el resto abre intencion nueva antes de su PUT.
        trabajo = []
        for ent, anuncio in pendientes:
            prev = conn.execute(_SQL_INTENCION_ABIERTA, (ent,)).fetchone()
            trabajo.append((ent, anuncio, prev[0] if prev is not None else None))
        # Lectura previa global ANTES del primer PUT: existencia y reversas.
        filas = archivar.leer_anuncios(escritor, [a.ad_id_externo for _, a in pendientes])
        retirados, fallidos = _ejecuta_pendientes(
            conn, plan, trabajo, filas, lote, impulso_id, escritor, actor_final
        )
    if fallidos:
        raise RuntimeError(f"retiro no confirmado ({retirados} registrados): {fallidos}")
    return retirados


def _ejecuta_pendientes(conn, plan, pendientes, filas, lote, impulso_id, escritor, actor_final):
    """Intencion-antes-del-PUT por anuncio (llamador con locks): la
    intencion se confirma con el readback (fila de retiro) o queda
    pendiente para el reintento; al final se nombra."""
    retirados = 0
    fallidos: list[str] = []
    for ent, anuncio, previo in pendientes:
        fila = filas.get(anuncio.ad_id_externo)
        if fila is None or fila.get("state") == archivar.ESTADO_ARCHIVADO:
            # 11 D3/B: con intencion previa, ARCHIVED o inexistente
            # CIERRA la intencion con detalle y no falla (el reintento
            # converge; el vigia no se atora). Sin previa, se nombra
            # como fallo (RA21: fail fast, 0 escrituras).
            if previo is None:
                motivo = (
                    "no_existe en Amazon: no se mando nada"
                    if fila is None
                    else "ARCHIVED: el PUT no lo mueve"
                )
                fallidos.append(f"{anuncio.ad_id_externo} ({motivo})")
                continue
            detalle_cierre = (
                f"{anuncio.ad_id_externo} inexistente en Amazon: nada que pausar"
                if fila is None
                else f"{anuncio.ad_id_externo} ARCHIVED en Amazon: el PUT no lo mueve"
            )
            _cierra_intencion(conn, previo, detalle_cierre, None)
            continue
        if previo is None:
            reversa = archivar.reversa_de_fila(fila)
            iid = _abre_intencion(conn, plan, lote, impulso_id, ent, anuncio, reversa, actor_final)
        else:
            # r01: el reintento sella con la reversa guardada en la
            # intencion (la foto previa al primer PUT), no con la
            # de su lectura (que ya ve PAUSED): si no, 'Regresar'
            # no manda ningun PUT y el anuncio queda apagado.
            iid = previo
            (reversa,) = conn.execute(
                "SELECT reversa FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
            ).fetchone()
        try:
            res = archivar.pausar_anuncios(escritor, [anuncio.ad_id_externo], ejecutar=True)[0]
        except Exception as exc:
            fallidos.append(
                f"{anuncio.ad_id_externo} (PUT incierto, intencion {iid} pendiente: {exc})"
            )
            continue
        if res.resultado in ("pausado", "ya_estaba"):
            rid = _sella_retiro(conn, plan, lote, impulso_id, ent, anuncio, reversa, actor_final)
            _cierra_intencion(conn, iid, "sello del readback", rid)
            retirados += 1
        elif res.resultado in ("fallo", "no_existe"):
            # Rechazo limpio o PUT nunca enviado: se sabe que Amazon no
            # cambio; la intencion se cierra SIN fila de retiro y el
            # reintento abre una nueva. Se nombra igual.
            detalle = res.detalle if res.detalle else res.resultado
            _cierra_intencion(conn, iid, detalle, None)
            fallidos.append(
                f"{anuncio.ad_id_externo} ({res.resultado}, intencion {iid} cerrada: {detalle})"
            )
        else:  # sin_confirmar: incierto; la intencion queda pendiente y el reintento la retoma
            detalle = res.detalle if res.detalle else res.resultado
            fallidos.append(
                f"{anuncio.ad_id_externo} ({res.resultado}, intencion {iid} pendiente: {detalle})"
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
        try:
            escritor = archivar.preparar_escritor(plataforma)
        except (ValueError, AdsApiError, AdsConfigError) as exc:
            raise RuntimeError(f"Amazon no disponible para reponer: {exc}") from exc
    # Misma llave que ejecuta (por anuncio): retirar y regresar el mismo
    # anuncio se excluyen; con llaves distintas se cruzaban (Y-05 c).
    with _bloqueados(conn, [f"retiro-ad:{f[3]}" for f in pendientes]):
        # Re-lee bajo lock: otro pudo reponer mientras se esperaba.
        filas = conn.execute(_SQL_RETIROS, (list(retiro_ids),)).fetchall()
        pendientes = [f for f in filas if not f[4]]
        if not pendientes:
            return 0
        estados = archivar.estados_actuales(escritor, [f[1] for f in pendientes])
        repuestos = 0
        fallidos: list[str] = []
        # Toda fila de anuncio_retiro es un retiro sellado; una
        # intencion pendiente no tiene retiro_id, asi que regresarla
        # es imposible estructuralmente (no manda nada a Amazon).
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


_SQL_INTENCIONES_PENDIENTES = """
SELECT i.id, i.platform, i.motivo, i.lote_retiro, i.impulso_id, i.ad_entity_id,
       i.ad_id_externo, i.ad_group_externo, i.campana_externa, i.sku, i.asin,
       i.reversa, i.actor
  FROM anuncio_retiro_intencion i
 WHERE i.cerrada_el IS NULL
 ORDER BY i.id
"""


def reconcilia_intenciones(conn, *, escritor=None, escritores=None) -> list[tuple[int, str]]:
    """Paso diario: resuelve TODA intencion pendiente, de cualquier
    motivo y aunque el impulso ya haya terminado, leyendo Amazon:
    PAUSED sella (con la reversa guardada en la intencion), ENABLED
    reintenta el PUT (sella; rechazo explicito cierra; incierto sigue
    pendiente), ARCHIVED o inexistente cierra con detalle. Best effort
    por intencion: una atorada nunca tumba a las demas. Devuelve
    (intencion_id, 'sellado'|'cerrado') por cada una resuelta."""
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
                    _cierra_intencion(
                        conn, iid, f"retiro {ya[0]} ya sellado (reconciliacion)", ya[0]
                    )
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
                        f"reconcilia retiros: LIST caido para {ad_id} ({exc})",
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
                    )
                    _cierra_intencion(conn, iid, "reconciliacion: Amazon ya lo tenia PAUSED", rid)
                    resueltas.append((iid, "sellado"))
                    continue
                res = archivar.pausar_anuncios(esc, [ad_id], ejecutar=True)[0]
                if res.resultado in ("pausado", "ya_estaba"):
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
                    )
                    _cierra_intencion(conn, iid, "reconciliacion: reintento sello", rid)
                    resueltas.append((iid, "sellado"))
                elif res.resultado in ("fallo", "no_existe"):
                    _cierra_intencion(
                        conn, iid, f"reconciliacion: {res.resultado} ({res.detalle})", None
                    )
                    resueltas.append((iid, "cerrado"))
                # sin_confirmar: incierto; sigue pendiente (manana sera).
        except Exception as exc:
            conn.rollback()
            print(f"reconcilia retiros: intencion {iid} fallo ({exc})", file=sys.stderr)
    return resueltas


def _sella_desde_intencion(
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
) -> int:
    """Sella el retiro con los datos guardados en la intencion (su
    reversa es la foto previa al primer PUT)."""
    plan = PlanRetiro(platform, motivo, (), "")
    anuncio = im.AnuncioARetirar(
        ad_id_externo=ad_id,
        ad_group_externo=ag,
        campana_externa=camp,
        ad_group_nombre="",
        sku=sku,
        asin=asin,
    )
    return _sella_retiro(conn, plan, lote, impulso_id, ent, anuncio, reversa, actor)


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
