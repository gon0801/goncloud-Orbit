"""Orquestacion SQL y envio Telegram de los avisos del motor de precios.

Extraido de `app/notifica.py` en Fase 3.1 para desacoplar las 17 queries y
sellados de `precio_*` del transporte generico de Telegram.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx

from app import notifica
from app.precio_avisos_texto import (
    CLAVE_PRECIO_AVISO_DIAS,
    FRASEO_TIPO_PRECIO,
    TIPOS_PRECIO,
    TIPOS_PRECIO_GRUPO,
    TIPOS_PRECIO_PRODUCTO,
    TIPOS_PRECIO_SELLADOS,
    TOPE_SKUS_PRECIO,
    CambioErrorPrecio,
    CohortePrecio,
    CompuertaPrecio,
    CorridaFallidaPrecio,
    CorteErroresPrecio,
    GrupoPrecio,
    HuerfanaPrecio,
    ProductoPrecio,
    ResumenDiarioPrecio,
    ReversaPrecio,
    _es_plataforma_amazon,
    aviso_precio_cambio_error,
    aviso_precio_cohorte,
    aviso_precio_compuerta,
    aviso_precio_corrida_fallida,
    aviso_precio_corte_errores,
    aviso_precio_grupo,
    aviso_precio_huerfana,
    aviso_precio_producto,
    aviso_precio_resumen,
    aviso_precio_reversa,
    flanco_nuevos,
    flanco_umbral,
    validar_precio_aviso_dias,
)
from app.redaction import install_scrub_filter, scrub

__all__ = [
    "CLAVE_PRECIO_AVISO_DIAS",
    "FRASEO_TIPO_PRECIO",
    "TIPOS_PRECIO",
    "TIPOS_PRECIO_GRUPO",
    "TIPOS_PRECIO_PRODUCTO",
    "TIPOS_PRECIO_SELLADOS",
    "TOPE_PRODUCTO_PRECIO",
    "TOPE_SKUS_PRECIO",
    "CambioErrorPrecio",
    "CohortePrecio",
    "CompuertaPrecio",
    "CorridaFallidaPrecio",
    "CorteErroresPrecio",
    "GrupoPrecio",
    "HuerfanaPrecio",
    "ProductoPrecio",
    "ResumenDiarioPrecio",
    "ReversaPrecio",
    "_es_plataforma_amazon",
    "avisar_corrida_fallida",
    "avisar_precio",
    "aviso_precio_cambio_error",
    "aviso_precio_cohorte",
    "aviso_precio_compuerta",
    "aviso_precio_corrida_fallida",
    "aviso_precio_corte_errores",
    "aviso_precio_grupo",
    "aviso_precio_huerfana",
    "aviso_precio_producto",
    "aviso_precio_resumen",
    "aviso_precio_reversa",
    "flanco_nuevos",
    "flanco_umbral",
    "notifica_precio",
    "validar_precio_aviso_dias",
]

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

_FAMILIA_POR_TIPO_PRECIO = {
    "no_evaluado": (GrupoPrecio,),
    "goal_inalcanzable": (GrupoPrecio,),
    "frenado": (GrupoPrecio,),
    "no_confirmado": (ProductoPrecio, GrupoPrecio),
    "buy_box_perdida": (ProductoPrecio, GrupoPrecio),
    "huerfana_sin_patch": (HuerfanaPrecio,),
    "resumen_diario": (ResumenDiarioPrecio,),
    "compuerta": (CompuertaPrecio,),
    "cambio_error": (CambioErrorPrecio,),
    "corte_errores": (CorteErroresPrecio,),
    "reversa": (ReversaPrecio,),
    "cohorte": (CohortePrecio,),
    "corrida_fallida": (CorridaFallidaPrecio,),
}

_BUILDER_POR_FAMILIA_PRECIO = {
    GrupoPrecio: "aviso_precio_grupo",
    ProductoPrecio: "aviso_precio_producto",
    HuerfanaPrecio: "aviso_precio_huerfana",
    ResumenDiarioPrecio: "aviso_precio_resumen",
    CompuertaPrecio: "aviso_precio_compuerta",
    CambioErrorPrecio: "aviso_precio_cambio_error",
    CorteErroresPrecio: "aviso_precio_corte_errores",
    ReversaPrecio: "aviso_precio_reversa",
    CohortePrecio: "aviso_precio_cohorte",
    CorridaFallidaPrecio: "aviso_precio_corrida_fallida",
}


def notifica_precio(tipo: str, payload, *, transport: httpx.BaseTransport | None = None) -> bool:
    """UNICO sender de avisos de precio (S7; S.6 suma los seis de U). `tipo`
    elige el builder y el `payload` debe ser de su familia; tipo o familia
    desconocidos -> warning + False. Canal deshabilitado -> True; cualquier
    excepcion -> warning con scrub + False; jamas levanta."""
    try:
        esperados = _FAMILIA_POR_TIPO_PRECIO.get(tipo)
        if esperados is None:
            logger.warning("telegram: tipo de aviso de precio desconocido: %s", scrub(str(tipo)))
            return False
        if not isinstance(payload, esperados):
            logger.warning(
                "telegram: payload de aviso de precio no es %s para %s",
                "/".join(e.__name__ for e in esperados),
                scrub(str(tipo)),
            )
            return False
        if payload.tipo != tipo:
            logger.warning(
                "telegram: payload de aviso de precio tipo %s no coincide con %s",
                scrub(str(payload.tipo)),
                scrub(str(tipo)),
            )
            return False
        nombre_builder = None
        for familia, candidato in _BUILDER_POR_FAMILIA_PRECIO.items():
            if isinstance(payload, familia):
                nombre_builder = candidato
                break
        if nombre_builder is None:
            raise AssertionError(f"familia de aviso sin builder: {type(payload).__name__}")
        if not notifica.canal_activo():
            return True
        builder = globals()[nombre_builder]
        return notifica._envia_texto(builder(payload), transport=transport)
    except Exception as exc:
        logger.warning("telegram: fallo armando el aviso de precio: %s", scrub(str(exc)))
        return False


_SQL_PRECIO_GRUPOS_DIA = """
SELECT d.resultado, d.motivo, COALESCE(l.seller_sku, l.external_id)
  FROM precio_decision d JOIN listing l
    ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s AND d.decision_date = %s
   AND d.resultado IN ('no_evaluado', 'goal_inalcanzable', 'frenado')
 ORDER BY COALESCE(l.seller_sku, l.external_id)
"""

_SQL_PRECIO_DIAS_MOTIVO = """
SELECT DISTINCT decision_date FROM precio_decision
 WHERE platform = %s AND resultado = 'no_evaluado' AND motivo = %s
   AND decision_date BETWEEN %s AND %s
"""

_SQL_PRECIO_BUYBOX_DIA = """
SELECT COALESCE(l.seller_sku, l.external_id), l.seller_sku, l.external_id,
       d.resultado, d.p_actual, d.p_actual_currency
  FROM precio_decision d JOIN listing l
    ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s AND d.decision_date = %s AND d.buy_box_is_own = false
"""

_SQL_PRECIO_FRENADO_NOCONF_DIA = """
SELECT d.listing_id, l.seller_sku, l.external_id
  FROM precio_decision d JOIN listing l
    ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s AND d.decision_date = %s
   AND d.resultado = 'frenado' AND d.motivo = 'no_confirmado'
 ORDER BY d.listing_id
"""

_SQL_PRECIO_ULTIMO_NOCONF = """
SELECT c.precio_antes, c.precio_antes_currency,
       c.precio_despues, c.precio_despues_currency
  FROM precio_cambio c
 WHERE c.listing_id = %s AND c.platform = %s
   AND c.estado = 'no_confirmado' AND c.aplicado AND NOT c.es_reversa
 ORDER BY c.id DESC LIMIT 1
"""

_SQL_PRECIO_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

TOPE_PRODUCTO_PRECIO = 5


def _grupos_precio_dia(conn, platform: str, dia) -> dict:
    grupos: dict = {}
    filas = conn.execute(_SQL_PRECIO_GRUPOS_DIA, (platform, dia)).fetchall()
    for resultado, motivo, sku in filas:
        grupos.setdefault((resultado, motivo or "desconocido"), []).append(sku)
    return grupos


def _avisar_grupos_precio(conn, platform: str, hoy, ayer, umbral) -> int:
    try:
        grupos_hoy = _grupos_precio_dia(conn, platform, hoy)
        grupos_ayer = _grupos_precio_dia(conn, platform, ayer)
    except Exception as exc:
        logger.warning("telegram: fallo avisando grupos de precio: %s", scrub(str(exc)))
        return 0
    nuevos = flanco_nuevos(sorted(grupos_hoy), sorted(grupos_ayer))
    enviados = 0
    for (resultado, motivo), skus in sorted(grupos_hoy.items()):
        try:
            if resultado == "no_evaluado":
                if umbral is None:
                    continue
                dias = {
                    r[0]
                    for r in conn.execute(
                        _SQL_PRECIO_DIAS_MOTIVO,
                        (platform, motivo, hoy - dt.timedelta(days=umbral), hoy),
                    ).fetchall()
                }
                if not flanco_umbral(dias, hoy, umbral):
                    continue
            elif (resultado, motivo) not in nuevos:
                continue
            grupo = GrupoPrecio(platform, resultado, motivo, len(skus), tuple(skus))
            if notifica_precio(resultado, grupo):
                enviados += 1
        except Exception as exc:
            logger.warning(
                "telegram: fallo avisando el grupo de precio %s: %s",
                scrub(f"{resultado}/{motivo}"),
                scrub(str(exc)),
            )
    return enviados


def _enviar_productos_precio_con_tope(tipo: str, motivo: str, piezas: list) -> int:
    enviados = 0
    for item in piezas[:TOPE_PRODUCTO_PRECIO]:
        try:
            if notifica_precio(tipo, item):
                enviados += 1
        except Exception as exc:
            logger.warning(
                "telegram: fallo avisando producto de precio %s: %s",
                scrub(str(tipo)),
                scrub(str(exc)),
            )
    resto = piezas[TOPE_PRODUCTO_PRECIO:]
    if resto:
        try:
            skus = tuple(s for s in ((p.sku or p.asin) for p in resto[:TOPE_SKUS_PRECIO]) if s)
            grupo = GrupoPrecio(resto[0].platform, tipo, motivo, len(resto), skus)
            if notifica_precio(tipo, grupo):
                enviados += 1
        except Exception as exc:
            logger.warning(
                "telegram: fallo avisando resto agrupado de precio %s: %s",
                scrub(str(tipo)),
                scrub(str(exc)),
            )
    return enviados


def _avisar_buybox_precio(conn, platform: str, hoy, ayer) -> int:
    try:
        bb_hoy = {r[0]: r for r in conn.execute(_SQL_PRECIO_BUYBOX_DIA, (platform, hoy)).fetchall()}
        bb_ayer = {r[0] for r in conn.execute(_SQL_PRECIO_BUYBOX_DIA, (platform, ayer)).fetchall()}
    except Exception as exc:
        logger.warning("telegram: fallo avisando buy box de precio: %s", scrub(str(exc)))
        return 0
    piezas = []
    for clave in flanco_nuevos(sorted(bb_hoy), sorted(bb_ayer)):
        try:
            _id, sku, asin, estado, p_actual, moneda = bb_hoy[clave]
            piezas.append(
                ProductoPrecio(
                    platform,
                    "buy_box_perdida",
                    sku,
                    asin,
                    None,
                    str(p_actual) if p_actual is not None else None,
                    str(moneda) if moneda is not None else None,
                    estado,
                    "buy_box_perdida",
                )
            )
        except Exception as exc:
            logger.warning(
                "telegram: fallo avisando buy box de precio %s: %s",
                scrub(str(clave)),
                scrub(str(exc)),
            )
    return _enviar_productos_precio_con_tope("buy_box_perdida", "buy_box_perdida", piezas)


def _avisar_noconf_precio(conn, platform: str, hoy, ayer) -> int:
    try:
        nc_hoy = {
            r[0]: r
            for r in conn.execute(_SQL_PRECIO_FRENADO_NOCONF_DIA, (platform, hoy)).fetchall()
        }
        nc_ayer = {
            r[0] for r in conn.execute(_SQL_PRECIO_FRENADO_NOCONF_DIA, (platform, ayer)).fetchall()
        }
    except Exception as exc:
        logger.warning("telegram: fallo avisando no confirmados de precio: %s", scrub(str(exc)))
        return 0
    piezas = []
    for lid in flanco_nuevos(sorted(nc_hoy), sorted(nc_ayer)):
        try:
            _lid, sku, asin = nc_hoy[lid]
            ult = conn.execute(_SQL_PRECIO_ULTIMO_NOCONF, (lid, platform)).fetchone()
            if ult is None:
                continue
            antes, mon_antes, despues, mon_despues = ult
            moneda = mon_despues or mon_antes
            piezas.append(
                ProductoPrecio(
                    platform,
                    "no_confirmado",
                    sku,
                    asin,
                    str(antes) if antes is not None else None,
                    str(despues) if despues is not None else None,
                    str(moneda) if moneda else None,
                    "no_confirmado",
                    "no_confirmado",
                )
            )
        except Exception as exc:
            logger.warning(
                "telegram: fallo avisando no confirmado de precio %s: %s",
                scrub(str(lid)),
                scrub(str(exc)),
            )
    return _enviar_productos_precio_con_tope("no_confirmado", "no_confirmado", piezas)


def _avisar_huerfana_precio(platform: str, resumen) -> int:
    try:
        huerfanas = resumen.huerfanas or 0
        if huerfanas > 0:
            huerfana = HuerfanaPrecio(platform, int(huerfanas))
            if notifica_precio("huerfana_sin_patch", huerfana):
                return 1
        return 0
    except Exception as exc:
        logger.warning("telegram: fallo avisando huerfanas de precio: %s", scrub(str(exc)))
        return 0


_SQL_CORRIDA_CERRADA_HOY = """
SELECT id FROM precio_corrida
 WHERE platform = %s AND fecha = %s AND estado = 'cerrada'
 ORDER BY id DESC LIMIT 1
"""

_SQL_RESUMEN_YA_ENVIADO = """
SELECT 1 FROM precio_corrida
 WHERE platform = %s AND fecha = %s AND resumen_enviado_at IS NOT NULL
 LIMIT 1
"""

_SQL_SELLAR_RESUMEN = "UPDATE precio_corrida SET resumen_enviado_at = now() WHERE id = %s"

_SQL_RESUMEN_MOVIMIENTOS = """
SELECT resultado, count(*) FROM precio_decision
 WHERE platform = %s AND decision_date = %s AND mode = 'live'
   AND resultado IN ('subir', 'bajar')
 GROUP BY resultado
"""

_SQL_RESUMEN_SOMBRA = """
SELECT count(*) FROM precio_decision
 WHERE platform = %s AND decision_date = %s AND mode = 'shadow'
   AND resultado IN ('subir', 'bajar')
"""

_SQL_RESUMEN_RETENIDOS = """
SELECT count(DISTINCT r.decision_id)
  FROM precio_retencion r JOIN precio_corrida c ON c.id = r.corrida_id
 WHERE c.platform = %s AND c.fecha = %s
"""

_SQL_RESUMEN_ERRORES = """
SELECT count(*)
  FROM precio_cambio c JOIN precio_decision d ON d.id = c.decision_id
 WHERE c.platform = %s AND d.decision_date = %s AND c.estado = 'error'
   AND c.aplicado AND NOT c.es_reversa
"""

_SQL_RESUMEN_SIN_EVALUAR = """
SELECT count(*) FROM precio_decision
 WHERE platform = %s AND decision_date = %s AND resultado = 'no_evaluado'
"""

_SQL_RESUMEN_IMPUTADO = """
SELECT count(*) FROM precio_decision
 WHERE platform = %s AND decision_date = %s AND l_origen IN ('familia', 'marketplace')
"""

_SQL_RESUMEN_MUESTRA = """
SELECT COALESCE(l.seller_sku, l.external_id), c.precio_antes, c.precio_despues
  FROM precio_cambio c
  JOIN precio_decision d ON d.id = c.decision_id
  JOIN listing l ON l.id = c.listing_id AND l.platform = c.platform
 WHERE c.platform = %s AND d.decision_date = %s
   AND c.aplicado AND NOT c.es_reversa
 ORDER BY 1 LIMIT 5
"""

_SQL_MEDICIONES_CON_CAUSA = """
SELECT canal, causa, medidas, nuevas, insumo, detalle
  FROM precio_compuerta_medicion
 WHERE corrida_id = %s AND causa IS NOT NULL
 ORDER BY canal
"""

_SQL_COMPUERTA_YA_AVISADA = """
SELECT 1 FROM precio_compuerta_medicion
 WHERE platform = %s AND fecha = %s AND canal = %s AND causa = %s
   AND avisada_at IS NOT NULL
 LIMIT 1
"""

_SQL_SELLAR_AVISADA = (
    "UPDATE precio_compuerta_medicion SET avisada_at = now() WHERE corrida_id = %s AND canal = %s"
)


def _corrida_cerrada_hoy(conn, platform: str, hoy):
    fila = conn.execute(_SQL_CORRIDA_CERRADA_HOY, (platform, hoy)).fetchone()
    return fila[0] if fila is not None else None


def _leer_resumen_diario(conn, platform: str, hoy) -> ResumenDiarioPrecio:
    movs = dict(conn.execute(_SQL_RESUMEN_MOVIMIENTOS, (platform, hoy)).fetchall())
    muestra = tuple(
        (sku, str(antes), str(despues))
        for sku, antes, despues in conn.execute(_SQL_RESUMEN_MUESTRA, (platform, hoy)).fetchall()
    )
    return ResumenDiarioPrecio(
        platform=platform,
        dia=hoy,
        subieron=int(movs.get("subir", 0)),
        bajaron=int(movs.get("bajar", 0)),
        en_sombra=conn.execute(_SQL_RESUMEN_SOMBRA, (platform, hoy)).fetchone()[0],
        retenidos=conn.execute(_SQL_RESUMEN_RETENIDOS, (platform, hoy)).fetchone()[0],
        errores=conn.execute(_SQL_RESUMEN_ERRORES, (platform, hoy)).fetchone()[0],
        sin_evaluar=conn.execute(_SQL_RESUMEN_SIN_EVALUAR, (platform, hoy)).fetchone()[0],
        con_envio_imputado=conn.execute(_SQL_RESUMEN_IMPUTADO, (platform, hoy)).fetchone()[0],
        muestra=muestra,
    )


def _avisar_resumen_precio(conn, platform: str, hoy) -> int:
    try:
        if conn.execute(_SQL_RESUMEN_YA_ENVIADO, (platform, hoy)).fetchone() is not None:
            return 0
        corrida = _corrida_cerrada_hoy(conn, platform, hoy)
        if corrida is None:
            return 0
        if not notifica.canal_activo():
            return 0
        if notifica_precio("resumen_diario", _leer_resumen_diario(conn, platform, hoy)):
            conn.execute(_SQL_SELLAR_RESUMEN, (corrida,))
            return 1
        return 0
    except Exception as exc:
        logger.warning("telegram: fallo avisando el resumen diario de precio: %s", scrub(str(exc)))
        return 0


def _avisar_compuerta_precio(conn, platform: str, hoy) -> int:
    try:
        corrida = _corrida_cerrada_hoy(conn, platform, hoy)
        if corrida is None:
            return 0
        filas = conn.execute(_SQL_MEDICIONES_CON_CAUSA, (corrida,)).fetchall()
    except Exception as exc:
        logger.warning("telegram: fallo avisando compuerta de precio: %s", scrub(str(exc)))
        return 0
    enviados = 0
    for canal, causa, medidas, nuevas, insumo, detalle in filas:
        try:
            ya = conn.execute(_SQL_COMPUERTA_YA_AVISADA, (platform, hoy, canal, causa)).fetchone()
            if ya is not None:
                continue
            aviso = CompuertaPrecio(
                platform, hoy, canal, causa, medidas, nuevas, insumo, detalle or ""
            )
            if not notifica.canal_activo():
                continue
            if notifica_precio("compuerta", aviso):
                conn.execute(_SQL_SELLAR_AVISADA, (corrida, canal))
                enviados += 1
        except Exception as exc:
            logger.warning(
                "telegram: fallo avisando compuerta de precio %s: %s",
                scrub(f"{canal}/{causa}"),
                scrub(str(exc)),
            )
    return enviados


def avisar_precio(conn, platform: str, hoy, resumen) -> int:
    """Gancho `avisar` de la corrida A.5 (`correr(..., avisar=...)` lo llama
    con `(conn, platform, hoy, resumen)` una vez al final, fail-silent).
    Lee hoy y dias previos y manda en flanco por racha: devuelve cuantos
    avisos salieron; jamas levanta (fallo -> warning con scrub + 0).

    B2: los avisos por grupo y por producto solo salen si la corrida
    persistio >= 1 decision (`resumen.decisiones > 0`): una reejecucion el
    mismo dia no reenvia. Residuo declarado: si una corrida cae despues de
    persistir todo y antes de avisar, ese dia no avisa (queda el log).
    Residuo declarado (r2): una segunda corrida del mismo dia que persiste
    >= 1 decision nueva (un goal agregado despues de la corrida de la
    manana) reenvia todos los avisos del dia; no se corrige en A.6.

    B4: un umbral `precio_aviso_dias_sin_evaluar` invalido o ausente solo
    apaga el aviso `no_evaluado` (warning); los demas tipos salen igual.

    S.6: el resumen diario y la compuerta van FUERA de la guarda B2 (sin
    decisiones tambien salen: el repaso no-op reintenta lo fallido); el que
    no repite es el sello (`resumen_enviado_at` / `avisada_at`).

    J-H3: una corrida que espera el insumo del dia (`resumen.espera_insumos`)
    no manda ni sella el resumen diario: lo manda la corrida que si decide,
    para que el dia se resuma con sus decisiones y cambios reales.
    """
    try:
        fila = conn.execute(_SQL_PRECIO_CONFIG).fetchone()
        if fila is None:
            return 0
        try:
            umbral = validar_precio_aviso_dias(fila[0])
        except ValueError as exc:
            logger.warning("telegram: umbral de aviso de precio invalido: %s", scrub(str(exc)))
            umbral = None
        ayer = hoy - dt.timedelta(days=1)
        enviados = 0
        if resumen.decisiones > 0:
            enviados += _avisar_grupos_precio(conn, platform, hoy, ayer, umbral)
            enviados += _avisar_buybox_precio(conn, platform, hoy, ayer)
            enviados += _avisar_noconf_precio(conn, platform, hoy, ayer)
        enviados += _avisar_huerfana_precio(platform, resumen)
        if not getattr(resumen, "espera_insumos", False):
            enviados += _avisar_resumen_precio(conn, platform, hoy)
        enviados += _avisar_compuerta_precio(conn, platform, hoy)
        return enviados
    except Exception as exc:
        logger.warning("telegram: fallo armando los avisos de precio: %s", scrub(str(exc)))
        return 0


def avisar_corrida_fallida(platform: str, exc: BaseException) -> bool:
    """ARREGLOS 03 P-B: la corrida de precios de `platform` se cayo (o ni
    siquiera pudo abrir la plataforma). Un aviso `corrida_fallida` con el
    dia UTC de hoy y el NOMBRE de la clase del error como motivo."""
    try:
        aviso = CorridaFallidaPrecio(
            platform=str(platform),
            dia=dt.datetime.now(dt.UTC).date(),
            motivo=type(exc).__name__,
        )
        return notifica_precio(aviso.tipo, aviso)
    except Exception as fallo:
        logger.warning("telegram: fallo avisando corrida fallida de precio: %s", scrub(str(fallo)))
        return False
