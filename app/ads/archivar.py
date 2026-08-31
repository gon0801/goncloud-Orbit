"""Archivado operado de product ads MUERTOS (ORBIT 06, limpieza 2026-08-30).

Un "product ad muerto" es un anuncio cuya PUBLICACION ya no existe en la
cuenta: el anuncio sigue vivo en una campana vivo, pero apunta a la nada. No
gasta (sin publicacion no hay impresion), pero ensucia: cada medicion de
cobertura arrastra un hueco que no corresponde a un producto real.

Este modulo NO decide cuales estan muertos — esa evidencia es del operador y
viaja como una lista EXPLICITA de adIds. La razon es que Orbit por si solo no
puede distinguir un anuncio muerto de un producto real todavia sin mapear:
los dos se ven igual (`listing_id IS NULL`). Meterle una heuristica seria
darle permiso de borrar por corazonada.

TRAMPA SELLADA (sonda de lectura del 2026-08-30, perfil amazon_mx en vivo):
la API IGNORA EN SILENCIO los filtros cuyo nombre no reconoce — `adIdFilter`
devolvio 1 anuncio y una clave inventada devolvio 1000, la pagina entera. Un
delete con la clave equivocada NO falla: viaja SIN FILTRO. Por eso el nombre
del filtro esta clavado con test propio en tests/test_ads_write.py.

"Borrar" en Amazon ARCHIVA: el anuncio queda operativamente muerto y su fila
sigue saliendo en el list con state=ARCHIVED. NO HAY REVERSA.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.ads.client import AdsApiError
from app.ads.structure import PATH_PRODUCT_ADS
from app.ads.write import AdsWriteClient

# Estados wire del list (UPPER, mismo vocabulario que app/apply.py).
ESTADO_ARCHIVADO = "ARCHIVED"

# Ids por llamada de LECTURA. Las lecturas si baten (el sync pagina de a
# 1000); la regla "una entrada, jamas un lote" es de las MUTACIONES.
IDS_POR_LECTURA = 100


@dataclass(frozen=True)
class ResultadoAnuncio:
    """Que le paso a UN anuncio. `resultado` es vocabulario cerrado."""

    ad_id: str
    estado_previo: str | None
    resultado: str
    detalle: str = ""


RESULTADOS = frozenset(
    {
        "archivado",  # estaba vivo, se archivo y el readback lo confirma
        "ya_estaba",  # ya venia ARCHIVED: no se mando nada
        "no_existe",  # el id no aparece en la cuenta: no se mando nada
        "sin_confirmar",  # se mando, pero el readback no lo vio ARCHIVED
        "fallo",  # Amazon rechazo la mutacion
    }
)


def _trozos(items: Sequence[str], tamano: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), tamano):
        yield items[i : i + tamano]


def estados_actuales(escritor: AdsWriteClient, ad_ids: Sequence[str]) -> dict[str, str]:
    """Estado wire de cada adId segun el list, por el scope SELLADO.

    Los ids que no aparecen quedan FUERA del dict: ausencia es ausencia, no
    se inventa un estado (regla 3).
    """
    estados: dict[str, str] = {}
    for trozo in _trozos(ad_ids, IDS_POR_LECTURA):
        resp = escritor.list_sellado(PATH_PRODUCT_ADS, {"adIdFilter": {"include": list(trozo)}})
        for fila in resp.json().get("productAds", []):
            ad_id = fila.get("adId")
            estado = fila.get("state")
            if ad_id is not None and estado is not None:
                estados[str(ad_id)] = str(estado)
    return estados


def archivar_anuncios(
    escritor: AdsWriteClient,
    ad_ids: Sequence[str],
    *,
    ejecutar: bool,
    avisar: Callable[[str], None] = print,
    dormir: Callable[[float], None] = time.sleep,
    pausa: float = 0.2,
) -> list[ResultadoAnuncio]:
    """Archiva los anuncios de `ad_ids`; con `ejecutar=False` no manda nada.

    Tres pasadas: leer el estado de todos, mutar UNO POR UNO (el sello del
    cliente de escritura) y releer todos para confirmar. La confirmacion es
    una relectura al final y no una por anuncio porque el list es
    eventualmente consistente: preguntarle enseguida da falsos negativos.
    """
    ids = [str(x) for x in ad_ids]
    if len(set(ids)) != len(ids):
        raise ValueError("hay adIds repetidos en la lista: se aborta antes de mutar")

    previos = estados_actuales(escritor, ids)
    avisar(f"lectura previa: {len(previos)} de {len(ids)} adIds existen en la cuenta")

    resultados: list[ResultadoAnuncio] = []
    a_mutar: list[str] = []
    for ad_id in ids:
        estado = previos.get(ad_id)
        if estado is None:
            resultados.append(ResultadoAnuncio(ad_id, None, "no_existe"))
        elif estado == ESTADO_ARCHIVADO:
            resultados.append(ResultadoAnuncio(ad_id, estado, "ya_estaba"))
        else:
            a_mutar.append(ad_id)

    avisar(
        f"a archivar: {len(a_mutar)}  (ya archivados: "
        f"{sum(1 for r in resultados if r.resultado == 'ya_estaba')}, "
        f"inexistentes: {sum(1 for r in resultados if r.resultado == 'no_existe')})"
    )

    if not ejecutar:
        for ad_id in a_mutar:
            resultados.append(ResultadoAnuncio(ad_id, previos[ad_id], "sin_confirmar", "ENSAYO"))
        return resultados

    fallidos: dict[str, str] = {}
    for i, ad_id in enumerate(a_mutar, 1):
        try:
            escritor.archivar_product_ad(ad_id)
        except AdsApiError as exc:
            fallidos[ad_id] = str(getattr(exc, "cuerpo", "") or exc)[:300]
        if i % 25 == 0:
            avisar(f"  ... {i}/{len(a_mutar)}")
        dormir(pausa)

    posteriores = estados_actuales(escritor, a_mutar)
    for ad_id in a_mutar:
        if ad_id in fallidos:
            resultados.append(ResultadoAnuncio(ad_id, previos[ad_id], "fallo", fallidos[ad_id]))
        elif posteriores.get(ad_id) == ESTADO_ARCHIVADO:
            resultados.append(ResultadoAnuncio(ad_id, previos[ad_id], "archivado"))
        else:
            resultados.append(
                ResultadoAnuncio(
                    ad_id,
                    previos[ad_id],
                    "sin_confirmar",
                    f"readback dijo {posteriores.get(ad_id)!r}",
                )
            )
    return resultados


def resumen(resultados: Iterable[ResultadoAnuncio]) -> dict[str, int]:
    """Cuenta por resultado. Las claves con cero NO se omiten: un resultado
    ausente y un resultado en cero no son lo mismo al leer un log."""
    cuenta = dict.fromkeys(sorted(RESULTADOS), 0)
    for r in resultados:
        cuenta[r.resultado] += 1
    return cuenta
