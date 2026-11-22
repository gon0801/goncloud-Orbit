"""Reposicion y recreacion de product ads retirados (AUTO-03 G.3, regla 7).

Extraido de `app/retiro_anuncios.py` (Ola D3a). Comparte la misma llave de
sesion `retiro-ad:<id>` via `app/retiro_anuncios_base.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ads import archivar
from app.ads.client import AdsApiError
from app.ads.config import AdsConfigError
from app.retiro_anuncios_base import (
    AutorizacionHumana,
    AutorizacionInvalida,
    ResultadoReposicion,
    _bloqueados,
)

_SQL_RETIROS = """
SELECT r.id, r.ad_id_externo, r.reversa, r.ad_entity_id, r.platform::text, r.autoria,
       (p.retiro_id IS NOT NULL) AS repuesto
  FROM anuncio_retiro r
  LEFT JOIN anuncio_reposicion p ON p.retiro_id = r.id
 WHERE r.id = ANY(%s)
"""

_SQL_INTENTO_ABIERTO = """
SELECT id, ad_id_nuevo
  FROM anuncio_recreacion_intento
 WHERE retiro_id = %s AND cerrada_el IS NULL
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


def _regresa(conn, retiro_ids, autorizacion, escritor, verbo: str, paso, al_sellar=None):
    """Lo comun de `repone` y `recrea`: autorizacion humana, misma llave por
    anuncio que `ejecuta_retiro`, relectura bajo la llave y sello atomico."""
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
            retiros = _lee_retiros(conn, retiro_ids)
            abiertos = [r for r in retiros.values() if not r.repuesto]
            estados = (
                archivar.estados_actuales(escritor, [r.ad_id_externo for r in abiertos])
                if abiertos
                else {}
            )
            for r in abiertos:
                res = paso(escritor, r, estados.get(r.ad_id_externo))
                if res.regresado:
                    with conn.transaction():
                        conn.execute(
                            "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo,"
                            " repuesto_el, actor) VALUES (%s, %s, %s, now(), %s)",
                            (r.id, res.ad_id_nuevo, res.estado_nuevo, autorizacion.actor),
                        )
                        if al_sellar is not None:
                            al_sellar(r, res)
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
    """La reversa (regla 7): deja al producto con su anuncio en el estado previo.
    Nunca recrea (S15) ni reactiva adoptados sin `incluye_adoptados` (S14)."""

    def paso(escritor, retiro: _Retiro, estado) -> ResultadoReposicion:
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
    """Recreacion explicita (S15) para retiros cuyo anuncio Amazon tiene ARCHIVED."""

    def resultado(retiro, tipo, detalle, ad_id=None, estado_nuevo=None):
        return ResultadoReposicion(
            retiro.id, retiro.ad_id_externo, tipo, detalle, ad_id, estado_nuevo
        )

    def vivo_ya_creado(escritor, linea, ad_id_nuevo):
        if ad_id_nuevo is not None:
            fila = archivar.leer_anuncios(escritor, [ad_id_nuevo]).get(ad_id_nuevo)
            if fila is not None and fila.get("state") != archivar.ESTADO_ARCHIVADO:
                return fila
        vivos = archivar.anuncios_vivos_del_grupo(escritor, linea["adGroupId"], linea["sku"])
        return vivos[0] if vivos else None

    def paso(escritor, retiro: _Retiro, estado) -> ResultadoReposicion:
        if estado != archivar.ESTADO_ARCHIVADO:
            visto = estado if estado is not None else "inexistente"
            return resultado(
                retiro, "no_requiere_recreacion", f"Amazon lo tiene {visto}: no se recrea"
            )
        intento = conn.execute(_SQL_INTENTO_ABIERTO, (retiro.id,)).fetchone()
        conn.commit()
        try:
            linea = archivar.parsear_reversa(retiro.reversa)
            ya = vivo_ya_creado(escritor, linea, intento[1] if intento else None)
        except Exception as exc:
            return resultado(
                retiro, "fallo", f"sin lectura confiable de Amazon ({exc}): no se recrea"
            )
        if ya is not None:
            if ya.get("state") not in (archivar.ESTADO_HABILITADO, archivar.ESTADO_PAUSADO):
                return resultado(
                    retiro,
                    "fallo",
                    f"el ad group ya tiene {ya.get('adId')} en {ya.get('state')!r}: no se recrea",
                )
            ad_id = str(ya.get("adId"))
            return resultado(
                retiro,
                "recreado",
                f"Amazon ya muestra {ad_id} vivo en el ad group: se sella sin crear otro",
                ad_id,
                str(ya.get("state")),
            )
        if intento is not None:
            iid, ad_id_nuevo = intento
            creado = f", que creo {ad_id_nuevo}," if ad_id_nuevo else ""
            return resultado(
                retiro,
                "fallo",
                f"el intento {iid}{creado} sigue sin resolver y Amazon todavia no muestra el"
                " anuncio: no se crea otro. Si en Amazon no existe, crealo a mano en su ad group"
                " y recrear lo sella",
            )
        iid = conn.execute(
            "INSERT INTO anuncio_recreacion_intento (retiro_id, actor, creada_el)"
            " VALUES (%s, %s, now()) RETURNING id",
            (retiro.id, autorizacion.actor),
        ).fetchone()[0]
        conn.commit()
        try:
            res = archivar.reponer_anuncios(escritor, [retiro.reversa], ejecutar=True)
        except Exception as exc:
            return resultado(
                retiro,
                "fallo",
                f"recreacion de {retiro.ad_id_externo} sin confirmar ({exc}): el POST pudo"
                f" crear el anuncio, revisa Amazon; el intento {iid} queda abierto y ningun"
                " reintento crea otro hasta resolverlo",
            )
        detalle = res[0].detalle if res else "sin resultado"
        creado = (
            res[0].ad_id
            if res
            and res[0].resultado in ("repuesto", "sin_confirmar")
            and res[0].ad_id != linea["sku"]
            else None
        )
        if creado is not None:
            conn.execute(
                "UPDATE anuncio_recreacion_intento SET ad_id_nuevo = %s WHERE id = %s",
                (creado, iid),
            )
            conn.commit()
        if res and res[0].resultado == "repuesto":
            return resultado(retiro, "recreado", "", res[0].ad_id, res[0].estado_previo)
        if res and res[0].resultado == "fallo" and res[0].rechazo:
            conn.execute(
                "UPDATE anuncio_recreacion_intento SET cerrada_el = now(), detalle = %s"
                " WHERE id = %s",
                (f"rechazado: {detalle}", iid),
            )
            conn.commit()
        return resultado(
            retiro,
            "fallo",
            f"recreacion de {retiro.ad_id_externo} no confirmada: {detalle}",
        )

    def cierra_intento(retiro: _Retiro, res: ResultadoReposicion) -> None:
        conn.execute(
            "UPDATE anuncio_recreacion_intento SET cerrada_el = now(), detalle = %s"
            " WHERE retiro_id = %s AND cerrada_el IS NULL",
            (f"sellado {res.ad_id_nuevo}", retiro.id),
        )

    return _regresa(conn, retiro_ids, autorizacion, escritor, "recrear", paso, cierra_intento)


def _ya_como_estaba(reversa: str, estado: str | None) -> bool:
    """Amazon ya lo tiene en el estado previo al retiro: regresarlo no manda nada."""
    try:
        previo = archivar.parsear_reversa(reversa).get("state")
    except archivar.ListaInvalida:
        return False
    return estado is not None and estado == previo


def _repone_uno(escritor, ad_id, reversa, estado):
    """Devuelve el anuncio (mismo adId) a su estado previo. Levanta si no se confirma."""
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
