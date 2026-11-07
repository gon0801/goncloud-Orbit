"""Retiro de product ads de bolsas compartidas, con reposicion (BIDS 02, I.4).

Un producto que entra en impulso sale de sus bolsas viejas cuando sus
campanas nuevas ya tienen impresiones (primero nace lo nuevo, despues
sale de lo viejo: nunca queda sin anuncio). El retiro PAUSA (sonda 1
de V.0 sellada: PUT /sp/productAds) y la reposicion REACTIVA el mismo
adId. Si el anuncio se archivo por fuera entre el retiro y la vuelta,
`repone` devuelve `requiere_recreacion` y solo `recrea`, con su propia
confirmacion, lo vuelve a crear con la linea guardada (AUTO-03 G.3).

Toda escritura lleva una autorizacion tipada (AUTO-03 G.2): la de un
humano (literal tecleado o, en el vigia, el literal guardado al lanzar
el impulso) o la de una decision `pause` del ciclo sobre ese product_ad,
verificada contra la cola antes del primer PUT. `ejecuta_retiro`,
`repone` y `recrea` devuelven un resultado por anuncio.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, get_args

from app import impulso as im
from app.ads import archivar
from app.ads.client import AdsApiError
from app.ads.config import AdsConfigError

MotivoRetiro = Literal["impulso", "sin_pedidos", "subfamilia", "producto_sin_ventas"]


class AutorizacionInvalida(ValueError):
    """La autorizacion no ampara este retiro: no sale ningun PUT."""


@dataclass(frozen=True)
class AutorizacionHumana:
    """Un humano autorizo: el literal que tecleo (o el que guardo al
    lanzar el impulso, S13) y quien queda como actor."""

    literal: str
    actor: str

    def __post_init__(self):
        if not self.literal or not self.literal.strip():
            raise AutorizacionInvalida("la autorizacion humana exige su literal")
        if not self.actor or not self.actor.strip():
            raise AutorizacionInvalida("la autorizacion humana exige su actor")


@dataclass(frozen=True)
class AutorizacionDeDecision:
    """La decision `pause` del ciclo sobre un product_ad, reclamada en la
    cola (fila `queue_id`). Se verifica antes del primer PUT."""

    decision_id: int
    queue_id: int


Autorizacion = AutorizacionHumana | AutorizacionDeDecision

ResultadoRetiro = Literal[
    "retirado",  # el PUT de Orbit lo paso a PAUSED: retiro sellado, autoria confirmada
    "adoptado",  # ya estaba PAUSED al confirmar: retiro sellado, autoria adoptada
    "ya_retirado",  # ya tenia un retiro abierto: nada que hacer
    "cerrado",  # su intencion pendiente se cerro: Amazon lo tiene ARCHIVED o no existe
    "pendiente",  # PUT incierto: la intencion sigue abierta y el reintento la retoma
    "rechazado",  # Amazon rechazo el PUT: intencion cerrada sin retiro
    "no_existe",  # no esta en Amazon: no se mando nada
    "archivado",  # ARCHIVED en Amazon: el PUT no lo mueve, no se mando nada
]

_RETIRO_RESUELTO = frozenset({"retirado", "adoptado", "ya_retirado", "cerrado"})
_RETIRO_FUERA = frozenset({"retirado", "adoptado", "ya_retirado"})


@dataclass(frozen=True)
class ResultadoAnuncio:
    """Que le paso a un anuncio del plan en `ejecuta_retiro`."""

    ad_id_externo: str
    resultado: ResultadoRetiro
    detalle: str = ""
    retiro_id: int | None = None
    intencion_id: int | None = None

    @property
    def fuera(self) -> bool:
        """El anuncio tiene un retiro sellado (cuenta en el n de n/m)."""
        return self.resultado in _RETIRO_FUERA

    @property
    def resuelto(self) -> bool:
        """No queda nada que reintentar para este anuncio."""
        return self.resultado in _RETIRO_RESUELTO

    def como_dict(self) -> dict:
        return {
            "ad_id_externo": self.ad_id_externo,
            "resultado": self.resultado,
            "detalle": self.detalle,
            "retiro_id": self.retiro_id,
        }


ResultadoRegreso = Literal[
    "repuesto",  # quedo como estaba antes del retiro (mismo adId): reposicion sellada
    "recreado",  # `recrea` creo otro anuncio con la linea guardada: reposicion sellada
    "ya_repuesto",  # el retiro ya tenia su reposicion
    "adoptado_sin_reposicion",  # retiro adoptado: no se reactiva sin incluye_adoptados (S14)
    "requiere_recreacion",  # ARCHIVED en Amazon: `repone` no lo recrea (S15)
    "no_requiere_recreacion",  # `recrea` sobre uno que no esta ARCHIVED: usar `repone`
    "fallo",  # no se pudo confirmar: el retiro sigue abierto
]


@dataclass(frozen=True)
class ResultadoReposicion:
    """Que le paso a un retiro en `repone` o `recrea`."""

    retiro_id: int
    ad_id_externo: str
    resultado: ResultadoRegreso
    detalle: str = ""
    ad_id_nuevo: str | None = None
    estado_nuevo: str | None = None

    @property
    def regresado(self) -> bool:
        return self.resultado in ("repuesto", "recreado")

    def como_dict(self) -> dict:
        return {
            "retiro_id": self.retiro_id,
            "ad_id_externo": self.ad_id_externo,
            "resultado": self.resultado,
            "detalle": self.detalle,
            "ad_id_nuevo": self.ad_id_nuevo,
            "estado_nuevo": self.estado_nuevo,
        }


def no_resueltos(resultados) -> str | None:
    """Los anuncios que quedaron por reintentar, en una linea; None si
    no queda ninguno."""
    malos = [
        f"{r.ad_id_externo} ({r.resultado}: {r.detalle})" for r in resultados if not r.resuelto
    ]
    if not malos:
        return None
    fuera = sum(1 for r in resultados if r.fuera)
    return f"retiro no confirmado ({fuera}/{len(resultados)} retirados): {malos}"


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
    if motivo not in get_args(MotivoRetiro):
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


def plan_de_decision(conn, decision_id: int) -> PlanRetiro:
    """El plan de retiro de una decision `pause` sobre un product_ad
    (motivo producto_sin_ventas): ese unico anuncio, con su ad group,
    campana y listing. Solo SELECT; no lee impulso (la exclusion de
    impulsos la hizo el ciclo al decidir y la rehace la revalidacion)."""
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


def _abre_intencion(
    conn, plan, lote, impulso_id, ent, anuncio, reversa, actor: str, decision_id: int | None
) -> int:
    """Intencion ANTES del PUT (tabla propia 0069): si el PUT es
    incierto o el proceso muere, el reintento la retoma. Una
    pendiente por anuncio (UNIQUE parcial). Guarda la decision que la
    autoriza (0078), o NULL si la autorizo un humano."""
    iid = conn.execute(
        "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, actor, creada_el, decision_id) VALUES (%s, %s, 'pausa', %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, now(), %s) RETURNING id",
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
            decision_id,
        ),
    ).fetchone()[0]
    conn.commit()
    return iid


def _sella_retiro(
    conn, plan, lote, impulso_id, ent, anuncio, reversa, actor: str, autoria: str
) -> int:
    """Fila de retiro SOLO tras el readback que confirma (toda fila de
    anuncio_retiro es un retiro sellado: 'fila = retirado'). `autoria`
    es 'confirmada' si el PUT de esta llamada lo movio, 'adoptada' si
    ya estaba PAUSED al confirmar (S14)."""
    rid = conn.execute(
        "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, retirado_el, actor, autoria) VALUES (%s, %s, 'pausa', %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, now(), %s, %s) RETURNING id",
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
            autoria,
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


def _verifica_autorizacion(conn, plan: PlanRetiro, autorizacion: Autorizacion, candidatos) -> None:
    """Antes del primer PUT. Una decision solo ampara el retiro de su
    propio product_ad, con motivo producto_sin_ventas: `pause` sobre ese
    anuncio, fila de cola reclamada (`applying`) en `live`, veto vencido
    y sin vetar, y un apply_attempt normal con cuota cobrada. Ese motivo
    no se ejecuta con autorizacion humana."""
    if isinstance(autorizacion, AutorizacionHumana):
        if plan.motivo == "producto_sin_ventas":
            raise AutorizacionInvalida("producto_sin_ventas solo se retira con su decision")
        return
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
    conn.commit()
    if fila is None:
        raise AutorizacionInvalida(
            f"la decision {autorizacion.decision_id} (cola {autorizacion.queue_id}) no autoriza"
            " este retiro: exige pause sobre ese product_ad, cola applying en live, veto"
            " vencido y cuota cobrada"
        )


def _actor_de(autorizacion: Autorizacion) -> str:
    if isinstance(autorizacion, AutorizacionHumana):
        return autorizacion.actor
    return f"apply_queue:{autorizacion.queue_id}"


def ejecuta_retiro(
    conn,
    plan: PlanRetiro,
    *,
    autorizacion: Autorizacion,
    impulso_id: int | None = None,
    escritor=None,
) -> tuple[ResultadoAnuncio, ...]:
    """Pausa los anuncios del plan (modo 'pausa'). Patron intencion /
    sello (0069, tabla propia): la intencion se escribe ANTES de su
    PUT con la reversa de la lectura previa; la fila de
    `anuncio_retiro` solo se escribe tras el readback que confirma
    (toda fila es un retiro sellado). Si el PUT es incierto o el
    proceso muere, la intencion pendiente es lo que el reintento
    retoma (manda el PUT sobre la misma intencion). Reanudable: los
    que ya tienen un retiro abierto salen `ya_retirado`. Todo se valida
    antes del primer PUT (autorizacion, entidad, campos NOT NULL,
    existencia en Amazon). Devuelve un resultado por anuncio del plan,
    en su orden: un fallo parcial no borra lo confirmado."""
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
    _verifica_autorizacion(conn, plan, autorizacion, candidatos)
    if not candidatos:
        return ()
    if escritor is None:
        try:
            escritor = archivar.preparar_escritor(plan.plataforma)
        except (ValueError, AdsApiError, AdsConfigError) as exc:
            raise RuntimeError(f"Amazon no disponible para retirar: {exc}") from exc
    decision_id = (
        autorizacion.decision_id if isinstance(autorizacion, AutorizacionDeDecision) else None
    )
    lote = lote_retiro_de_plan(plan)
    resultados: dict[str, ResultadoAnuncio] = {}
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
            resultados[anuncio.ad_id_externo] = ResultadoAnuncio(
                anuncio.ad_id_externo, "ya_retirado", "ya tenia un retiro abierto", ya[0]
            )
        if pendientes:
            # Intencion pendiente de un intento anterior: se retoma (mismo
            # iid, sin INSERT); el resto abre intencion nueva antes de su PUT.
            trabajo = []
            for ent, anuncio in pendientes:
                prev = conn.execute(_SQL_INTENCION_ABIERTA, (ent,)).fetchone()
                trabajo.append((ent, anuncio, prev[0] if prev is not None else None))
            # Lectura previa global ANTES del primer PUT: existencia y reversas.
            filas = archivar.leer_anuncios(escritor, [a.ad_id_externo for _, a in pendientes])
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


# Cierre de la intencion de un retiro adoptado. La sonda `retiro` de AUTO-03
# lo usa para distinguir un adoptado del codigo de G de un retiro historico.
_DETALLE_ADOPTADO = "adoptado: ya estaba PAUSED al confirmar"


def _ejecuta_pendientes(
    conn, plan, pendientes, filas, lote, impulso_id, escritor, actor_final, decision_id
) -> list[ResultadoAnuncio]:
    """Intencion-antes-del-PUT por anuncio (llamador con locks): la
    intencion se confirma con el readback (fila de retiro) o queda
    pendiente para el reintento. Un resultado por anuncio."""
    resultados: list[ResultadoAnuncio] = []
    for ent, anuncio, previo in pendientes:
        ad_id = anuncio.ad_id_externo
        fila = filas.get(ad_id)
        if fila is None or fila.get("state") == archivar.ESTADO_ARCHIVADO:
            # 11 D3/B: con intencion previa, ARCHIVED o inexistente
            # CIERRA la intencion con detalle (el reintento converge;
            # el vigia no se atora). Sin previa no se escribe nada
            # (RA21: 0 escrituras) y se nombra.
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
            # r01: el reintento sella con la reversa guardada en la
            # intencion (la foto previa al primer PUT), no con la
            # de su lectura (que ya ve PAUSED): si no, 'Regresar'
            # no manda ningun PUT y el anuncio queda apagado.
            iid = previo
            (reversa,) = conn.execute(
                "SELECT reversa FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
            ).fetchone()
        try:
            res = archivar.pausar_anuncios(escritor, [ad_id], ejecutar=True)[0]
        except Exception as exc:
            resultados.append(
                ResultadoAnuncio(
                    ad_id, "pendiente", f"PUT incierto, intencion {iid} pendiente: {exc}", None, iid
                )
            )
            continue
        detalle = res.detalle if res.detalle else res.resultado
        if res.resultado in ("pausado", "ya_estaba"):
            # S14: solo el PUT de esta llamada que lo vio pasar de
            # ENABLED a PAUSED prueba autoria. Encontrarlo ya PAUSED
            # (la retoma de un PUT incierto, o lo pauso otro) prueba
            # el estado, no quien lo cambio: el retiro queda adoptado.
            propio = res.resultado == "pausado"
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
            )
            _cierra_intencion(conn, iid, "sello del readback" if propio else _DETALLE_ADOPTADO, rid)
            resultados.append(
                ResultadoAnuncio(ad_id, "retirado" if propio else "adoptado", res.detalle, rid, iid)
            )
        elif res.resultado in ("fallo", "no_existe"):
            # Rechazo limpio o PUT nunca enviado: se sabe que Amazon no
            # cambio; la intencion se cierra SIN fila de retiro y el
            # reintento abre una nueva.
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
        else:  # sin_confirmar: incierto; la intencion queda pendiente y el reintento la retoma
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


_SQL_RETIROS = """
SELECT r.id, r.ad_id_externo, r.reversa, r.ad_entity_id, r.platform::text, r.autoria,
       (p.retiro_id IS NOT NULL) AS repuesto
  FROM anuncio_retiro r
  LEFT JOIN anuncio_reposicion p ON p.retiro_id = r.id
 WHERE r.id = ANY(%s)
"""


@dataclass(frozen=True)
class _Retiro:
    id: int
    ad_id_externo: str
    reversa: str
    ad_entity_id: int
    plataforma: str
    autoria: str
    repuesto: bool


def _lee_retiros(conn, retiro_ids) -> dict[int, _Retiro]:
    return {f[0]: _Retiro(*f) for f in conn.execute(_SQL_RETIROS, (list(retiro_ids),)).fetchall()}


def _regresa(conn, retiro_ids, autorizacion, escritor, verbo: str, paso):
    """Lo comun de `repone` y `recrea`: autorizacion humana, retiros que
    existen y de una sola plataforma, misma llave por anuncio que
    `ejecuta_retiro` (retirar y regresar el mismo anuncio se excluyen;
    con llaves distintas se cruzaban, Y-05 c), relectura bajo la llave y
    estado actual en Amazon. `paso(escritor, retiro, estado)` decide cada
    uno; si regresa, aqui se sella su reposicion. Uno que falla no
    bloquea a los demas. Un resultado por retiro pedido, en su orden."""
    if not isinstance(autorizacion, AutorizacionHumana):
        raise AutorizacionInvalida(f"{verbo} exige autorizacion humana")
    retiros = _lee_retiros(conn, retiro_ids)
    conn.commit()
    if len(retiros) != len(set(retiro_ids)):
        raise ValueError("algun retiro pedido no existe")
    resultados: dict[int, ResultadoReposicion] = {}
    abiertos = [r for r in retiros.values() if not r.repuesto]
    if abiertos:
        plataformas = {r.plataforma for r in abiertos}
        if len(plataformas) > 1:
            raise ValueError(f"{verbo} mezcla plataformas {sorted(plataformas)}: una por llamada")
        if escritor is None:
            try:
                escritor = archivar.preparar_escritor(next(iter(plataformas)))
            except (ValueError, AdsApiError, AdsConfigError) as exc:
                raise RuntimeError(f"Amazon no disponible para {verbo}: {exc}") from exc
        with _bloqueados(conn, [f"retiro-ad:{r.ad_entity_id}" for r in abiertos]):
            # Re-lee bajo lock: otro pudo regresarlo mientras se esperaba.
            retiros = _lee_retiros(conn, retiro_ids)
            abiertos = [r for r in retiros.values() if not r.repuesto]
            estados = (
                archivar.estados_actuales(escritor, [r.ad_id_externo for r in abiertos])
                if abiertos
                else {}
            )
            # Toda fila de anuncio_retiro es un retiro sellado; una
            # intencion pendiente no tiene retiro_id, asi que regresarla
            # es imposible estructuralmente (no manda nada a Amazon).
            for r in abiertos:
                res = paso(escritor, r, estados.get(r.ad_id_externo))
                if res.regresado:
                    conn.execute(
                        "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo,"
                        " repuesto_el, actor) VALUES (%s, %s, %s, now(), %s)",
                        (r.id, res.ad_id_nuevo, res.estado_nuevo, autorizacion.actor),
                    )
                    conn.commit()
                resultados[r.id] = res
            conn.commit()
    return tuple(
        resultados.get(rid)
        or ResultadoReposicion(rid, retiros[rid].ad_id_externo, "ya_repuesto", "ya regresado")
        for rid in dict.fromkeys(retiro_ids)
    )


def repone(
    conn,
    *,
    retiro_ids: tuple[int, ...],
    autorizacion: AutorizacionHumana,
    incluye_adoptados: bool = False,
    escritor=None,
) -> tuple[ResultadoReposicion, ...]:
    """La reversa (regla 7): deja al producto con un anuncio en cada ad
    group del que salio, con el estado que tenia (el `state` de la linea
    guardada). Lee el estado actual: PAUSED se reactiva (mismo adId)
    solo si estaba ENABLED; si ya esta como estaba, solo cierra. Nunca
    recrea (S15): ARCHIVED sale `requiere_recreacion` sin POST, y
    recuperarlo pasa por `recrea`. Un retiro adoptado (Orbit lo
    encontro ya PAUSED: no hay prueba de que lo pauso el) sale
    `adoptado_sin_reposicion` sin PUT, salvo que el humano pida
    `incluye_adoptados` (S14); si ya esta como estaba antes del
    retiro, se cierra sin escribir. Una segunda llamada no repone otra
    vez."""

    def paso(escritor, retiro: _Retiro, estado) -> ResultadoReposicion:
        # Un adoptado que ya esta como estaba (el dueno lo tenia pausado
        # antes del retiro) se cierra sin escribir; si regresarlo exige
        # un PUT o una recreacion, no sin el humano.
        if (
            retiro.autoria == "adoptada"
            and not incluye_adoptados
            and not _ya_como_estaba(retiro.reversa, estado)
        ):
            return ResultadoReposicion(
                retiro.id,
                retiro.ad_id_externo,
                "adoptado_sin_reposicion",
                "Orbit lo encontro ya pausado: no se reactiva sin incluir adoptados",
            )
        if estado == archivar.ESTADO_ARCHIVADO:
            return ResultadoReposicion(
                retiro.id,
                retiro.ad_id_externo,
                "requiere_recreacion",
                "ARCHIVED en Amazon: regresar no lo recrea",
            )
        try:
            nuevo, estado_nuevo = _repone_uno(
                escritor, retiro.ad_id_externo, retiro.reversa, estado
            )
        except Exception as exc:
            return ResultadoReposicion(retiro.id, retiro.ad_id_externo, "fallo", str(exc))
        return ResultadoReposicion(
            retiro.id, retiro.ad_id_externo, "repuesto", "", nuevo, estado_nuevo
        )

    return _regresa(conn, retiro_ids, autorizacion, escritor, "reponer", paso)


def recrea(
    conn,
    *,
    retiro_ids: tuple[int, ...],
    autorizacion: AutorizacionHumana,
    escritor=None,
) -> tuple[ResultadoReposicion, ...]:
    """Recreacion explicita (S15): solo para retiros cuyo anuncio Amazon
    tiene ARCHIVED (Amazon no des-archiva); crea otro anuncio con la
    linea guardada (`archivar.reponer_anuncios`) y sella la reposicion
    con el adId nuevo. Comprueba el estado bajo la misma llave: uno que
    no esta ARCHIVED sale `no_requiere_recreacion` sin POST."""

    def paso(escritor, retiro: _Retiro, estado) -> ResultadoReposicion:
        if estado != archivar.ESTADO_ARCHIVADO:
            visto = estado if estado is not None else "inexistente"
            return ResultadoReposicion(
                retiro.id,
                retiro.ad_id_externo,
                "no_requiere_recreacion",
                f"Amazon lo tiene {visto}: no se recrea",
            )
        res = archivar.reponer_anuncios(escritor, [retiro.reversa], ejecutar=True)
        if not res or res[0].resultado != "repuesto":
            detalle = res[0].detalle if res else "sin resultado"
            return ResultadoReposicion(
                retiro.id,
                retiro.ad_id_externo,
                "fallo",
                f"recreacion de {retiro.ad_id_externo} no confirmada: {detalle}",
            )
        return ResultadoReposicion(
            retiro.id, retiro.ad_id_externo, "recreado", "", res[0].ad_id, res[0].estado_previo
        )

    return _regresa(conn, retiro_ids, autorizacion, escritor, "recrear", paso)


def _ya_como_estaba(reversa: str, estado: str | None) -> bool:
    """Amazon ya lo tiene en el estado previo al retiro: regresarlo no
    manda nada."""
    try:
        previo = archivar.parsear_reversa(reversa).get("state")
    except archivar.ListaInvalida:
        return False
    return estado is not None and estado == previo


def _repone_uno(escritor, ad_id, reversa, estado):
    """Devuelve el anuncio (mismo adId) a su estado previo. Levanta si
    no se confirma. Reversa legacy (sin `state`): previo desconocido, se
    sigue el camino historico (PAUSED reactiva)."""
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
    if estado == "ENABLED":
        return ad_id, "ENABLED"
    raise RuntimeError(f"reposicion de {ad_id}: estado {estado!r} sin camino")


_SQL_INTENCIONES_PENDIENTES = """
SELECT i.id, i.platform, i.motivo, i.lote_retiro, i.impulso_id, i.ad_entity_id,
       i.ad_id_externo, i.ad_group_externo, i.campana_externa, i.sku, i.asin,
       i.reversa, i.actor
  FROM anuncio_retiro_intencion i
 WHERE i.cerrada_el IS NULL
   AND i.decision_id IS NULL
 ORDER BY i.id
"""


def reconcilia_intenciones(conn, *, escritor=None, escritores=None) -> list[tuple[int, str]]:
    """Paso diario: resuelve toda intencion pendiente de un motivo
    humano, aunque el impulso ya haya terminado, leyendo Amazon:
    PAUSED sella adoptado (con la reversa guardada en la intencion; el
    estado esta probado, la autoria no: S14), ENABLED reintenta el PUT
    (sella; rechazo explicito cierra; incierto sigue pendiente),
    ARCHIVED o inexistente cierra con detalle. Las intenciones con
    decision_id (pausa por producto) no son suyas: las resuelve
    `apply_producto.reconcilia_pause_producto`, que nunca reenvia el
    PUT. Best effort por intencion: una atorada nunca tumba a las
    demas. Devuelve (intencion_id, 'sellado'|'cerrado') por cada una
    resuelta."""
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
                        "adoptada",
                    )
                    _cierra_intencion(conn, iid, f"{_DETALLE_ADOPTADO} (reconciliacion)", rid)
                    resueltas.append((iid, "sellado"))
                    continue
                res = archivar.pausar_anuncios(esc, [ad_id], ejecutar=True)[0]
                if res.resultado in ("pausado", "ya_estaba"):
                    propio = res.resultado == "pausado"
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
                        "confirmada" if propio else "adoptada",
                    )
                    _cierra_intencion(
                        conn,
                        iid,
                        "reconciliacion: reintento sello"
                        if propio
                        else f"{_DETALLE_ADOPTADO} (reconciliacion)",
                        rid,
                    )
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
    autoria,
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
    return _sella_retiro(conn, plan, lote, impulso_id, ent, anuncio, reversa, actor, autoria)


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
