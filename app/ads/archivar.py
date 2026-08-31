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

from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import PATH_PRODUCT_ADS, evaluar_perfiles
from app.ads.write import AdsWriteClient

# Estados wire del list (UPPER, mismo vocabulario que app/apply.py).
ESTADO_ARCHIVADO = "ARCHIVED"
# Estado por default al reponer: PAUSED. Un anuncio repuesto en ENABLED
# empieza a gastar solo; que lo encienda un humano.
ESTADO_PAUSADO = "PAUSED"

# Ids por llamada de LECTURA. Las lecturas si baten (el sync pagina de a
# 1000); la regla "una entrada, jamas un lote" es de las MUTACIONES.
IDS_POR_LECTURA = 100


class ListaInvalida(ValueError):
    """La lista que trajo el operador no sirve, y se sabe ANTES de mutar.

    Tiene tipo propio (y no un ValueError pelado) para que el CLI pueda
    distinguirla de un ValueError cualquiera que aparezca DESPUES de haber
    mutado — p.ej. una respuesta que no parsea en la relectura. Ese caso NO
    es un error de uso: los anuncios ya se archivaron, y reportarlo como
    "argumentos invalidos" diria que no paso nada cuando si paso (hallazgo
    CodeRabbit 2026-08-30).
    """


@dataclass(frozen=True)
class ResultadoAnuncio:
    """Que le paso a UN anuncio. `resultado` es vocabulario cerrado.

    `reversa` guarda con QUE se podria volver a crear el anuncio si se
    archivo por error (adGroupId + asin/sku). Amazon no des-archiva, asi que
    esto es lo unico que separa "me equivoque" de "perdi el dato para
    siempre" — invariante 7 del repo.
    """

    ad_id: str
    estado_previo: str | None
    resultado: str
    detalle: str = ""
    reversa: str = ""


RESULTADOS = frozenset(
    {
        "archivado",  # estaba vivo, se archivo y el readback lo confirma
        "ya_estaba",  # ya venia ARCHIVED: no se mando nada
        "no_existe",  # el id no aparece en la cuenta: no se mando nada
        "sin_confirmar",  # se mando, pero el readback no lo vio ARCHIVED
        "repuesto",  # la REVERSA: el anuncio se volvio a crear
        "fallo",  # Amazon rechazo la mutacion
    }
)


def preparar_escritor(platform: str) -> AdsWriteClient:
    """Escritor con el scope SELLADO del perfil de `platform`.

    Vive aqui y no en el CLI por el candado de arquitectura: `app.ads.write`
    es la UNICA superficie que escribe en Amazon y su lista de importadores
    esta acotada a proposito (un importador de mas = un segundo dueno de la
    mutacion). Este modulo es ese unico dueno para la limpieza; el CLI queda
    de envoltorio delgado, sin tocar el cliente de escritura.

    Fail-closed DOBLE (hallazgo cross-review codex/grok 2026-08-30):

    1. Exactamente un perfil aceptado para la plataforma.
    2. Y NINGUNO rechazado por "pais duplicado". Esto es lo que el punto 1
       solo NO alcanza a ver: `evaluar_perfiles` YA garantiza como maximo un
       aceptado por pais — cuando hay dos, acepta el PRIMERO del payload y
       marca el resto como duplicado. Asi que el conteo de aceptados jamas
       llega a 2 y la comprobacion obvia no dispara nunca. Para una
       operacion IRREVERSIBLE, "eligio uno de dos en silencio" es
       exactamente lo que no puede pasar: aqui se para.
    """
    credenciales = AdsCredentials.from_secrets_dir()
    todos = evaluar_perfiles(AdsClient(credenciales))
    perfiles = [p for p in todos if p.aceptado and p.platform == platform]
    if len(perfiles) != 1:
        raise ValueError(
            f"se esperaba EXACTAMENTE 1 perfil aceptado para {platform}, "
            f"hay {len(perfiles)}: no se escribe"
        )
    pais = perfiles[0].country
    duplicados = [
        p for p in todos if not p.aceptado and p.country == pais and "duplicado" in (p.motivo or "")
    ]
    if duplicados:
        raise ValueError(
            f"la cuenta tiene MAS DE UN perfil de {pais}: el gate se queda con el "
            f"primero del payload y descarta {len(duplicados)}. No se archiva a ciegas "
            f"en una de las dos cuentas — resolver la ambiguedad antes"
        )
    return AdsWriteClient(
        credenciales,
        platform=platform,
        profile_id=perfiles[0].profile_id,
        modo_confirmado="live",
    )


def _trozos(items: Sequence[str], tamano: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), tamano):
        yield items[i : i + tamano]


def leer_anuncios(escritor: AdsWriteClient, ad_ids: Sequence[str]) -> dict[str, dict]:
    """Las filas del list para cada adId, por el scope SELLADO.

    Devuelve la fila CRUDA y no solo el estado porque el archivado no tiene
    reversa: adGroupId + asin/sku son lo unico con lo que un humano podria
    volver a crear el anuncio si se archivo por error. Sin esto, un error ni
    siquiera es reconstruible (invariante 7 del repo).

    Los ids que no aparecen quedan FUERA del dict: ausencia es ausencia, no
    se inventa un estado (regla 3).
    """
    filas: dict[str, dict] = {}
    for trozo in _trozos(ad_ids, IDS_POR_LECTURA):
        resp = escritor.list_sellado(PATH_PRODUCT_ADS, {"adIdFilter": {"include": list(trozo)}})
        for fila in resp.json().get("productAds", []):
            ad_id = fila.get("adId")
            if ad_id is not None and fila.get("state") is not None:
                filas[str(ad_id)] = fila
    return filas


def estados_actuales(escritor: AdsWriteClient, ad_ids: Sequence[str]) -> dict[str, str]:
    """Solo el estado wire de cada adId (vista delgada de `leer_anuncios`)."""
    return {k: str(v["state"]) for k, v in leer_anuncios(escritor, ad_ids).items()}


def _errores_del_ack(ack: object) -> list:
    """Los rechazos POR-ITEM de un 207.

    Shape SELLADO por el probe 2.5 y ya explotado por el aplicador
    (`_errores_de_ack` de app/apply_harvest.py): {"<recurso>": {"error":
    [...], "success": [...]}}. Un 2xx NO es exito automatico — Amazon
    contesta 207 y mete el rechazo adentro. [] = sin rechazos legibles
    (regla 3: un shape sin anidado no inventa errores).
    """
    if not isinstance(ack, dict):
        return []
    for valor in ack.values():
        if isinstance(valor, dict):
            error = valor.get("error")
            if isinstance(error, list) and error:
                return error
    return []


CLAVES_REVERSA = ("adGroupId", "campaignId", "sku", "state", "asin")


def _reversa(fila: dict) -> str:
    """Con que se volveria a crear ESTE anuncio, en formato `clave=valor`.

    Lleva TODO lo que pide el create (adGroupId, campaignId, sku y el estado
    que tenia ANTES) y ademas el asin, que no se manda pero es lo unico
    legible por un humano al revisar la lista. `reponer_anuncios` come
    exactamente estas lineas.
    """
    return " ".join(f"{c}={fila.get(c)}" for c in CLAVES_REVERSA if fila.get(c))


def parsear_reversa(linea: str) -> dict[str, str]:
    """Una linea `clave=valor ...` de reversa -> dict, o revienta con motivo.

    Fail-closed: si falta cualquiera de los tres datos que el create exige,
    NO se adivina (regla 3). Reponer con un dato inventado crearia el
    anuncio equivocado, y eso si gasta plata.
    """
    datos: dict[str, str] = {}
    for token in linea.split():
        clave, sep, valor = token.partition("=")
        if sep and clave in CLAVES_REVERSA and valor:
            datos[clave] = valor
    faltan = [c for c in ("adGroupId", "campaignId", "sku") if c not in datos]
    if faltan:
        raise ListaInvalida(f"linea de reversa sin {', '.join(faltan)}: {linea.strip()!r}")
    datos.setdefault("state", ESTADO_PAUSADO)
    return datos


def _id_del_ack(ack: object) -> str | None:
    """El adId que Amazon devuelve al crear, si es legible.

    Mismo shape anidado sellado que el resto ({"<recurso>": {"success":
    [...]}}). None = ack sin id legible: regla 3, no se inventa.
    """
    if not isinstance(ack, dict):
        return None
    for valor in ack.values():
        if not isinstance(valor, dict):
            continue
        for item in valor.get("success") or []:
            if isinstance(item, dict):
                for clave in ("adId", "productAdId"):
                    if item.get(clave):
                        return str(item[clave])
    return None


def reponer_anuncios(
    escritor: AdsWriteClient,
    lineas: Sequence[str],
    *,
    ejecutar: bool,
    avisar: Callable[[str], None] = print,
    dormir: Callable[[float], None] = time.sleep,
    pausa: float = 0.2,
) -> list[ResultadoAnuncio]:
    """LA REVERSA: vuelve a crear anuncios archivados desde sus lineas.

    Come las lineas `reversa` que dejo `archivar_anuncios`. Es la vuelta
    atras que exige el invariante 7 — y como CREA anuncios, o sea habilita
    gasto, tiene el mismo candado que el archivado: ensayo por default.

    Las lineas se parsean TODAS antes de mandar nada: una linea rota a mitad
    de camino dejaria media reposicion hecha.
    """
    datos = [parsear_reversa(linea) for linea in lineas if linea.strip()]
    if not datos:
        return []

    avisar(f"a reponer: {len(datos)} anuncios")
    if not ejecutar:
        return [
            ResultadoAnuncio(d["sku"], d["state"], "sin_confirmar", "ENSAYO", _reversa(d))
            for d in datos
        ]

    resultados: list[ResultadoAnuncio] = []
    for i, d in enumerate(datos, 1):
        reversa = _reversa(d)
        try:
            resp = escritor.crear_product_ad(d["adGroupId"], d["campaignId"], d["sku"], d["state"])
        except AdsApiError as exc:
            detalle = str(getattr(exc, "cuerpo", "") or exc)[:300]
            resultados.append(ResultadoAnuncio(d["sku"], d["state"], "fallo", detalle, reversa))
        else:
            try:
                ack = resp.json()
            except ValueError:
                ack = {}
            errores = _errores_del_ack(ack)
            if errores:
                detalle = f"207 con rechazo por-item: {errores}"[:300]
                resultados.append(ResultadoAnuncio(d["sku"], d["state"], "fallo", detalle, reversa))
            else:
                nuevo = _id_del_ack(ack)
                resultados.append(
                    ResultadoAnuncio(
                        nuevo or d["sku"],
                        d["state"],
                        "repuesto" if nuevo else "sin_confirmar",
                        "" if nuevo else "el ack no trajo adId legible",
                        reversa,
                    )
                )
        if i % 25 == 0:
            avisar(f"  ... {i}/{len(datos)}")
        dormir(pausa)
    return resultados


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
        raise ListaInvalida("hay adIds repetidos en la lista: se aborta antes de mutar")

    filas = leer_anuncios(escritor, ids)
    previos = {k: str(v["state"]) for k, v in filas.items()}
    avisar(f"lectura previa: {len(previos)} de {len(ids)} adIds existen en la cuenta")

    resultados: list[ResultadoAnuncio] = []
    a_mutar: list[str] = []
    for ad_id in ids:
        estado = previos.get(ad_id)
        if estado is None:
            resultados.append(ResultadoAnuncio(ad_id, None, "no_existe"))
        elif estado == ESTADO_ARCHIVADO:
            resultados.append(
                ResultadoAnuncio(ad_id, estado, "ya_estaba", reversa=_reversa(filas[ad_id]))
            )
        else:
            a_mutar.append(ad_id)

    avisar(
        f"a archivar: {len(a_mutar)}  (ya archivados: "
        f"{sum(1 for r in resultados if r.resultado == 'ya_estaba')}, "
        f"inexistentes: {sum(1 for r in resultados if r.resultado == 'no_existe')})"
    )

    if not ejecutar:
        for ad_id in a_mutar:
            resultados.append(
                ResultadoAnuncio(
                    ad_id, previos[ad_id], "sin_confirmar", "ENSAYO", _reversa(filas[ad_id])
                )
            )
        return resultados

    # Un 2xx NO es exito automatico: Amazon contesta 207 y mete el rechazo
    # por-item adentro (CX3 del aplicador). Se guardan las DOS formas de
    # rechazo -- el >=400 que revienta y el error[] del 207 -- pero ninguna
    # decide sola: manda el readback (ver abajo).
    rechazos: dict[str, str] = {}
    for i, ad_id in enumerate(a_mutar, 1):
        try:
            resp = escritor.archivar_product_ad(ad_id)
        except AdsApiError as exc:
            rechazos[ad_id] = str(getattr(exc, "cuerpo", "") or exc)[:300]
        else:
            try:
                errores = _errores_del_ack(resp.json())
            except ValueError:
                errores = []
            if errores:
                rechazos[ad_id] = f"207 con rechazo por-item: {errores}"[:300]
        if i % 25 == 0:
            avisar(f"  ... {i}/{len(a_mutar)}")
        dormir(pausa)

    posteriores = estados_actuales(escritor, a_mutar)
    for ad_id in a_mutar:
        reversa = _reversa(filas[ad_id])
        rechazo = rechazos.get(ad_id)
        if posteriores.get(ad_id) == ESTADO_ARCHIVADO:
            # El estado en Amazon MANDA sobre lo que dijo el envio: un corte
            # de red despues de aplicar reportaba "fallo" sobre algo que si
            # quedo archivado (hallazgo cross-review codex 2026-08-30).
            detalle = f"quedo archivado pese al rechazo del envio: {rechazo}" if rechazo else ""
            resultados.append(
                ResultadoAnuncio(ad_id, previos[ad_id], "archivado", detalle, reversa)
            )
        elif rechazo:
            resultados.append(ResultadoAnuncio(ad_id, previos[ad_id], "fallo", rechazo, reversa))
        else:
            resultados.append(
                ResultadoAnuncio(
                    ad_id,
                    previos[ad_id],
                    "sin_confirmar",
                    f"readback dijo {posteriores.get(ad_id)!r}",
                    reversa,
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
