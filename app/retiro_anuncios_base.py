"""Tipos, ledger intencion/sello y candado de sesion para retiro de anuncios.

Extraido de `app/retiro_anuncios.py` (Ola D3a). Modulo base compartido por
`app/retiro_anuncios.py`, `app/retiro_anuncios_regreso.py` y
`app/retiro_anuncios_reconcilia.py`.

El candado de exclusion mutua por anuncio (`retiro-ad:<id>`) permanece
unificado aqui para que retiro, regreso y reconciliacion serialicen sobre la
misma llave de sesion.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from psycopg import pq

from app import impulso as im
from app.db import tx_durable

DIAS_LIBRO_AL_DIA = 2

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
    "retirado",
    "adoptado",
    "ya_retirado",
    "cerrado",
    "pendiente",
    "rechazado",
    "no_existe",
    "archivado",
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
    "repuesto",
    "recreado",
    "ya_repuesto",
    "adoptado_sin_reposicion",
    "requiere_recreacion",
    "no_requiere_recreacion",
    "fallo",
]

ResultadoIntencion = Literal["sin_intencion", "sellado", "cerrado", "pendiente"]


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
    """Los anuncios que quedaron por reintentar, en una linea; None si no queda ninguno."""
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
    vendieron: tuple[int, ...] = ()
    ventas_al_dia: bool = True
    ventas_al_dia_hasta: dt.datetime | None = None

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
            "vendieron": list(self.vendieron),
            "ventas_al_dia": self.ventas_al_dia,
            "ventas_al_dia_hasta": (
                None if self.ventas_al_dia_hasta is None else self.ventas_al_dia_hasta.isoformat()
            ),
        }


def _huella_plan(anuncios: tuple[im.AnuncioARetirar, ...]) -> str:
    canon = json.dumps(
        [[a.ad_id_externo, a.ad_group_externo, a.campana_externa, a.sku, a.asin] for a in anuncios],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def lote_retiro_de_plan(plan: PlanRetiro) -> str:
    return f"retiro-{plan.huella[:12]}"


_SQL_YA_RETIRADO = """
SELECT r.id, r.autoria FROM anuncio_retiro r
 WHERE r.ad_entity_id = %s
   AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id)
"""

_SQL_INTENCION_ABIERTA = """
SELECT i.id, i.decision_id FROM anuncio_retiro_intencion i
 WHERE i.ad_entity_id = %s
   AND i.cerrada_el IS NULL
"""

_SQL_ENTIDAD_DE_ANUNCIO = """
SELECT pa.id FROM ad_entity pa
 WHERE pa.platform = %s AND pa.kind = 'product_ad' AND pa.external_id = %s
"""

# Cierre de la intencion de un retiro adoptado. La sonda `retiro` de AUTO-03
# lo usa para distinguir un adoptado del codigo de G de un retiro historico.
_DETALLE_ADOPTADO = "adoptado: ya estaba PAUSED al confirmar"


def _detalle_ya_sellado(ya, causa: str) -> str:
    """Cierre de una intencion cuyo anuncio ya tiene su retiro sellado."""
    texto = f"retiro {ya[0]} ya sellado ({causa})"
    return f"adoptado: {texto}" if ya[1] == "adoptada" else texto


def _put_propio(res) -> bool:
    """`pausado` sin detalle: el PUT salio limpio y el readback lo vio PAUSED."""
    return res.resultado == "pausado" and not res.detalle


def _actor_de(autorizacion: Autorizacion) -> str:
    if isinstance(autorizacion, AutorizacionHumana):
        return autorizacion.actor
    return f"apply_queue:{autorizacion.queue_id}"


def _abre_intencion(
    conn, plan, lote, impulso_id, ent, anuncio, reversa, actor: str, decision_id: int | None
) -> int:
    """Intencion ANTES del PUT (tabla propia 0069): una pendiente por anuncio."""
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
    conn,
    plan,
    lote,
    impulso_id,
    ent,
    anuncio,
    reversa,
    actor: str,
    autoria: str,
    *,
    cerrar_intencion: tuple[int, str] | None = None,
) -> int:
    """Sella retiro (y cierra intencion en la misma tx durable si se indica)."""
    with tx_durable(conn):
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
        if cerrar_intencion is not None:
            conn.execute(
                "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = %s,"
                " retiro_id = %s WHERE id = %s",
                (cerrar_intencion[1], rid, cerrar_intencion[0]),
            )
    return rid


def _cierra_intencion(conn, iid: int, detalle: str, retiro_id: int | None) -> None:
    """Cierra la intencion (solo-cierre 0069) en una transaccion durable."""
    with tx_durable(conn):
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = %s,"
            " retiro_id = %s WHERE id = %s",
            (detalle, retiro_id, iid),
        )


def _sella_desde_intencion(conn, *datos, cerrar_intencion: tuple[int, str] | None = None) -> int:
    """Sella retiro con los datos guardados en la intencion."""
    plat, mot, lote, imp_id, ent, ad_id, ag, camp, sku, asin, rev, actor, autoria = datos
    plan = PlanRetiro(plat, mot, (), "")
    anuncio = im.AnuncioARetirar(ad_id, ag, camp, "", sku, asin)
    return _sella_retiro(
        conn,
        plan,
        lote,
        imp_id,
        ent,
        anuncio,
        rev,
        actor,
        autoria,
        cerrar_intencion=cerrar_intencion,
    )


@contextmanager
def _bloqueados(conn, llaves):
    """Advisory locks de SESION, ordenados (sobreviven los commits y cubren el HTTP)."""
    tomadas: list[str] = []
    try:
        for llave in sorted(set(llaves)):
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (llave,))
            tomadas.append(llave)
        yield
    finally:
        if conn.info.transaction_status == pq.TransactionStatus.INERROR:
            conn.rollback()
        for llave in reversed(tomadas):
            with contextlib.suppress(Exception):
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (llave,))
        if not conn.autocommit and conn.info.transaction_status != pq.TransactionStatus.IDLE:
            with contextlib.suppress(Exception):
                conn.commit()
