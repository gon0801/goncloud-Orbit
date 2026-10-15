"""API del motor de precios (REPRICING 01 A.6, REPRICING 02 0.a).

`/api/dashboard/precios` y el bloque precios de `/salud` viven aqui desde
REPRICING 02 0.a; `app/api_dashboard.py` reexporta los nombres que usan sus
pruebas. El resto del dashboard no cambia.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

import psycopg.errors
from fastapi import APIRouter, HTTPException

from app.api import ConexionLectura
from app.api_common import _dec_str
from app.notifica import motivo_precio_es, validar_precio_aviso_dias
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.precio import cobertura as cobertura_precio
from app.precio import fuentes as fuentes_precio
from app.redaction import scrub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ---------------------------------------------------------------------------
# REPRICING 01 A.6 - /precios + plataformas.<p>.precios en /salud: pantalla
# del motor de precios
# ---------------------------------------------------------------------------
#
# - GET /api/dashboard/precios: por plataforma, el recuadro S10 EN VIVO
#   (armado con app.precio.fuentes + app.precio.cobertura, las MISMAS
#   consultas del motor — este modulo no duplica ni una) + las decisiones
#   del dia por resultado (precio_decision) + las huerfanas pendientes
#   (precio_cambio pendiente no-reversa) + las listas S7 (`con_goal`,
#   `acciones`, `no_evaluados_filas`, `divergentes`).
# - /salud suma plataformas.<p>.precios ({hoy, cobertura, evaluados,
#   movidos, sombra, no_evaluados, frenados, goal_inalcanzable, huerfanas,
#   decisiones, corrida_ausente}). `cobertura` es la MISMA serializacion
#   que el `recuadro` de /precios (misma funcion). El cupo murio en
#   REPRICING 02 S.1.
# - El reloj es el de la base (fuentes.hoy_base: el dia UTC que fija el
#   trigger de precio_decision, no el local).
# - Sin esquema de precios (la 0039 no esta aplicada): /salud deja el
#   bloque en None + warning (patron _spapi_de) y /precios responde 503
#   con el motivo claro — nunca 500 en ninguno de los dos.
# - Dinero como STRING via _dec_str (regla 4); ausente = null (regla 3).
#   Las frases de `acciones` llevan el dinero a 2 decimales (texto de
#   pantalla, no ledger).

_SQL_DECISIONES_PRECIO_HOY = """
SELECT resultado, count(*)
  FROM precio_decision
 WHERE platform = %s::platform
   AND decision_date = %s
 GROUP BY resultado
 ORDER BY resultado
"""

_SQL_HUERFANAS_PRECIO = """
SELECT count(*)
  FROM precio_cambio
 WHERE platform = %s::platform
   AND estado = 'pendiente'
   AND NOT es_reversa
"""

_SQL_DETALLE_DECISIONES_HOY = """
SELECT d.id, d.listing_id, l.seller_sku, d.resultado, d.mode, d.motivo,
       d.m_actual, d.goal, d.p_actual, d.p_actual_currency,
       d.p_objetivo, d.p_objetivo_currency, d.p_aplicado, d.canal
  FROM precio_decision d
  JOIN listing l ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s::platform
   AND d.decision_date = %s
 ORDER BY l.seller_sku
"""

_SQL_CAMBIOS_DE_DECISIONES = """
SELECT decision_id, precio_antes, precio_antes_currency, precio_despues,
       precio_despues_currency, aplicado, estado,
       COALESCE((enviado_at AT TIME ZONE 'UTC')::date,
                (created_at AT TIME ZONE 'UTC')::date) AS fecha
  FROM precio_cambio
 WHERE decision_id = ANY(%s) AND NOT es_reversa
 ORDER BY id DESC
"""

_SQL_GOAL_VIGENTE = """
SELECT g.listing_id, l.seller_sku, g.margen_goal_pct, g.mode
  FROM precio_goal g
  JOIN listing l ON l.id = g.listing_id AND l.platform = g.platform
 WHERE g.platform = %s::platform
   AND g.valid_from <= %s AND (g.valid_to IS NULL OR g.valid_to > %s)
 ORDER BY l.seller_sku
"""

_SQL_ULTIMOS_CAMBIOS = """
SELECT listing_id, precio_antes, precio_antes_currency, precio_despues,
       precio_despues_currency, estado,
       COALESCE((enviado_at AT TIME ZONE 'UTC')::date,
                (created_at AT TIME ZONE 'UTC')::date) AS fecha
  FROM precio_cambio
 WHERE platform = %s::platform AND NOT es_reversa AND listing_id = ANY(%s)
 ORDER BY id DESC
"""

_SQL_RACHA_RECIENTE = """
SELECT d.listing_id, l.seller_sku, d.decision_date, d.resultado, d.motivo,
       d.p_actual, d.p_actual_currency
  FROM precio_decision d
  JOIN listing l ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s::platform
   AND d.decision_date BETWEEN %s AND %s
 ORDER BY l.seller_sku, d.decision_date DESC
"""

# Estados del cambio que prueban PATCH aceptado (B2): `pendiente` aun no
# sale y `error` no movio el precio, aunque el cambio sea real.
_ESTADOS_PRECIO_MOVIERON = ("enviado", "confirmado", "no_confirmado")

# Tope de historia que recorre el bloque (e) (B4): la racha real se cuenta
# dia por dia hacia atras desde hoy y se corta aqui (el umbral de dias
# cabe siempre: su cota es 1-14).
_TOPE_RACHA_DIVERGENTE = 30

_ERROR_SIN_0039 = "el motor de precios todavía no está instalado en esta base"


class _SinEsquemaPrecio(Exception):
    """La 0039 no esta aplicada: el probe no vio tablas `precio_*`."""


def _hay_esquema_precio(conn: ConexionLectura) -> bool:
    """Probe de cero filas: True si `precio_decision` existe (la conexion de
    lectura corre en autocommit: el fallo no envenena lo que sigue)."""
    try:
        conn.execute("SELECT 1 FROM precio_decision LIMIT 0")
    except psycopg.errors.UndefinedTable:
        return False
    return True


_ESTADO_CAMBIO_ES = {
    "pendiente": "pendiente de confirmación",
    "enviado": "enviado",
    "error": "en error",
    "confirmado": "confirmado",
    "no_confirmado": "no confirmado por la observación",
}


def _decisiones_precio_hoy(conn: ConexionLectura, plataforma: str, hoy: dt.date) -> dict[str, int]:
    """{resultado: conteo} de precio_decision del dia (grano exacto del
    UNIQUE (listing, platform, decision_date): una fila por publicacion)."""
    return {
        fila[0]: fila[1]
        for fila in conn.execute(_SQL_DECISIONES_PRECIO_HOY, (plataforma, hoy)).fetchall()
    }


def _huerfanas_precio(conn: ConexionLectura, plataforma: str) -> int:
    """Cambios pendientes no-reversa (las huerfanas que la corrida cierra a
    `error`: espejo del WHERE de cerrar_huerfanas en corrida.py)."""
    return int(conn.execute(_SQL_HUERFANAS_PRECIO, (plataforma,)).fetchone()[0])


def _seguridad_precio(conn: ConexionLectura, plataforma: str, hoy: dt.date) -> dict:
    """`EstadoSeguridad` serializado para /precios (JSON + HTML): modos,
    ultima corrida, retenciones abiertas del dia y corrida liberable."""
    estado = fuentes_precio.leer_seguridad(conn, plataforma, hoy=hoy)
    ultima = estado.ultima_corrida
    return {
        "modo_global": estado.modo_global,
        "modo_universo": (dict(estado.modo_universo) if estado.modo_universo is not None else None),
        "ultima_corrida": (
            None
            if ultima is None
            else {
                "corrida_id": ultima.corrida_id,
                "fecha": ultima.fecha.isoformat(),
                "estado": ultima.estado,
                "decisiones": ultima.decisiones,
                "movidas": ultima.movidas,
                "retenidas": ultima.retenidas,
                "errores": ultima.errores,
            }
        ),
        "retenciones_abiertas": {
            canal: {
                "causa": retenida.causa,
                "medidas": retenida.medidas,
                "nuevas": retenida.nuevas,
                "insumo": retenida.insumo,
                "detalle": retenida.detalle,
            }
            for canal, retenida in estado.retenciones_abiertas.items()
        },
        "liberable_corrida_id": estado.liberable_corrida_id,
        "cohorte": None,
    }


def _recuadro_precio(conn: ConexionLectura, plataforma: str, hoy: dt.date) -> dict:
    """Recuadro S10 serializado (la ecuacion cuadra: cobertura.cuadra_exact).
    La clave de dias la lee fuentes.py de la config (este modulo no conoce
    claves); el dinero sale como string (regla 4)."""
    from app import precio_mercados as mercados

    settings = fuentes_precio.config_vigente_settings(conn)
    max_dias = fuentes_precio.max_dias_desde_settings(settings)
    with mercados.abrir(plataforma) as vitrina:
        filas = fuentes_precio.leer_publicaciones(conn, vitrina, hoy=hoy)
    rec = cobertura_precio.armar_recuadro(
        filas,
        platform=plataforma,
        max_dias=max_dias,
        universos=fuentes_precio.universos_encendidos(conn),
    )
    return {
        "activas": rec.activas,
        "evaluadas": rec.evaluadas,
        "no_evaluadas": [{"motivo": motivo, "count": n} for motivo, n in rec.no_evaluadas],
        "sin_goal": [
            {
                "sku": detalle.sku,
                "precio": _dec_str(detalle.precio),
                "moneda": detalle.moneda,
                "canal": detalle.canal,
            }
            for detalle in rec.sin_goal
        ],
        "fuera_de_alcance": [{"fase": fase, "count": n} for fase, n in rec.fuera_de_alcance],
        "avisos": list(rec.avisos),
    }


def _dinero_2(valor) -> str | None:
    """Dinero para la frase de pantalla: 2 decimales (el JSON lleva _dec_str)."""
    if valor is None:
        return None
    return f"{Decimal(str(valor)):.2f}"


def _detalle_decisiones_hoy(conn: ConexionLectura, plataforma: str, hoy: dt.date) -> list[dict]:
    """Decisiones de hoy con su SKU (una fila por listing: el UNIQUE diario)."""
    cols = (
        "id",
        "listing_id",
        "sku",
        "resultado",
        "mode",
        "motivo",
        "m_actual",
        "goal",
        "p_actual",
        "p_actual_currency",
        "p_objetivo",
        "p_objetivo_currency",
        "p_aplicado",
        "canal",
    )
    return [
        dict(zip(cols, fila, strict=True))
        for fila in conn.execute(_SQL_DETALLE_DECISIONES_HOY, (plataforma, hoy)).fetchall()
    ]


def _cambios_por_decision(conn: ConexionLectura, ids: list) -> dict:
    """Ultimo cambio real no-reversa por decision (el primero de id DESC gana)."""
    cambios: dict = {}
    if not ids:
        return cambios
    for fila in conn.execute(_SQL_CAMBIOS_DE_DECISIONES, (ids,)).fetchall():
        (dec_id, antes, antes_mon, despues, despues_mon, aplicado, estado, fecha) = fila
        if dec_id not in cambios:
            cambios[dec_id] = {
                "antes": antes,
                "antes_moneda": antes_mon,
                "despues": despues,
                "despues_moneda": despues_mon,
                "real": bool(aplicado),
                "estado": estado,
                "fecha": fecha.isoformat() if fecha is not None else None,
            }
    return cambios


def _conteo_por_motivo(detalle: list[dict], resultado: str) -> dict[str, int]:
    """{motivo: conteo} de las decisiones de hoy con ese resultado."""
    conteo: dict[str, int] = {}
    for fila in detalle:
        if fila["resultado"] == resultado and fila["motivo"] is not None:
            conteo[fila["motivo"]] = conteo.get(fila["motivo"], 0) + 1
    return conteo


def _frase_accion(
    *, sku: str, resultado: str, mode: str, motivo, antes, despues, moneda, cambio_estado
) -> str:
    """Una frase por decision del dia (S7 bloque c): en `live` en indicativo
    («subió de 116.00 a 127.60 MXN») y en `shadow` en condicional («habría
    subido de 116.00 a 127.60 MXN»); `mantener`, `frenado` y
    `goal_inalcanzable` llevan su motivo en palabras. Un `subir`/`bajar` en
    `live` sin cambio real (`cambio_estado` None: `cambiar_precio` salto sin
    escribir fila o la rama de parche no sello) dice «decidió subir ...; sin
    cambio aplicado», nunca «subió»; con el cambio `pendiente` se dice
    («...; pendiente de confirmación») y en `error` como ya estaba
    (`saltado` no existe en el ledger: el CHECK de `precio_cambio` solo
    admite pendiente/enviado/error/confirmado/no_confirmado)."""
    if resultado == "subir":
        if mode == "live" and cambio_estado is None:
            return f"{sku} decidió subir de {antes} a {despues} {moneda}; sin cambio aplicado"
        verbo = "habría subido" if mode == "shadow" else "subió"
        frase = f"{sku} {verbo} de {antes} a {despues} {moneda}"
    elif resultado == "bajar":
        if mode == "live" and cambio_estado is None:
            return f"{sku} decidió bajar de {antes} a {despues} {moneda}; sin cambio aplicado"
        verbo = "habría bajado" if mode == "shadow" else "bajó"
        frase = f"{sku} {verbo} de {antes} a {despues} {moneda}"
    elif resultado == "mantener":
        frase = f"{sku} se mantuvo en {antes} {moneda} ({motivo_precio_es(motivo)})"
    elif resultado == "frenado":
        frase = f"{sku} se frenó: {motivo_precio_es(motivo)}"
    else:  # goal_inalcanzable (las no_evaluado van al bloque (d), no aqui)
        frase = f"{sku} con objetivo inalcanzable: {motivo_precio_es(motivo)}"
    if cambio_estado == "error":
        frase += "; el cambio quedó en error"
    elif cambio_estado == "pendiente":
        frase += "; pendiente de confirmación"
    return frase


def _acciones_precio(detalle: list[dict], cambios: dict, plataforma: str) -> list[dict]:
    """Una frase por decision de hoy (las `no_evaluado` van al bloque (d))."""
    moneda_def = PLATAFORMAS_MONEDA[plataforma]
    acciones = []
    for fila in detalle:
        if fila["resultado"] == "no_evaluado":
            continue
        cambio = cambios.get(fila["id"])
        if cambio is not None:
            antes = _dinero_2(cambio["antes"] if cambio["antes"] is not None else fila["p_actual"])
            despues = _dinero_2(
                cambio["despues"] if cambio["despues"] is not None else fila["p_aplicado"]
            )
            moneda = cambio["despues_moneda"] or fila["p_actual_currency"] or moneda_def
            estado = cambio["estado"]
        else:
            antes = _dinero_2(fila["p_actual"])
            despues = _dinero_2(fila["p_aplicado"])
            moneda = fila["p_actual_currency"] or moneda_def
            estado = None
        acciones.append(
            {
                "sku": fila["sku"],
                "resultado": fila["resultado"],
                "mode": fila["mode"],
                "frase": _frase_accion(
                    sku=fila["sku"],
                    resultado=fila["resultado"],
                    mode=fila["mode"],
                    motivo=fila["motivo"],
                    antes=antes,
                    despues=despues,
                    moneda=moneda,
                    cambio_estado=estado,
                ),
            }
        )
    return acciones


def _filas_no_evaluados(detalle: list[dict]) -> list[dict]:
    """Bloque (d): una fila por SKU con su motivo en palabras (sale de las
    decisiones `no_evaluado` de hoy, no del recuadro en vivo)."""
    return [
        {
            "sku": fila["sku"],
            "motivo": fila["motivo"],
            "motivo_es": motivo_precio_es(fila["motivo"]),
        }
        for fila in detalle
        if fila["resultado"] == "no_evaluado"
    ]


def _con_goal_precio(
    conn: ConexionLectura, plataforma: str, hoy: dt.date, por_listing: dict
) -> list[dict]:
    """Bloque (b): una fila por producto con goal vigente (SKU, margen de
    hoy, goal, precio actual, precio objetivo, canal, modo y ultimo cambio
    con su estado en palabras; sin decision hoy se dice en palabras)."""
    filas = [
        dict(zip(("listing_id", "sku", "goal", "goal_mode"), fila, strict=True))
        for fila in conn.execute(_SQL_GOAL_VIGENTE, (plataforma, hoy, hoy)).fetchall()
    ]
    ultimos: dict = {}
    if filas:
        for fila in conn.execute(
            _SQL_ULTIMOS_CAMBIOS, (plataforma, [f["listing_id"] for f in filas])
        ).fetchall():
            (lid, antes, antes_mon, despues, despues_mon, estado, fecha) = fila
            if lid not in ultimos:
                ultimos[lid] = {
                    "fecha": fecha.isoformat() if fecha is not None else None,
                    "antes": _dec_str(antes),
                    "despues": _dec_str(despues),
                    "moneda": despues_mon or antes_mon,
                    "estado": estado,
                    "estado_es": _ESTADO_CAMBIO_ES.get(estado, estado),
                }
    con_goal = []
    for fila in filas:
        det = por_listing.get(fila["listing_id"])
        con_goal.append(
            {
                "sku": fila["sku"],
                "m_actual": _dec_str(det["m_actual"]) if det is not None else None,
                "goal": _dec_str(fila["goal"]),
                "p_actual": _dec_str(det["p_actual"]) if det is not None else None,
                "p_objetivo": _dec_str(det["p_objetivo"]) if det is not None else None,
                "moneda": (det["p_actual_currency"] if det is not None else None)
                or PLATAFORMAS_MONEDA[plataforma],
                "canal": det["canal"] if det is not None else None,
                "modo": det["mode"] if det is not None else fila["goal_mode"],
                "decision_hoy": det["resultado"] if det is not None else None,
                "ultimo_cambio": ultimos.get(fila["listing_id"]),
            }
        )
    return con_goal


# G.3: una fila por listing de la plataforma, con lo que el dueno mira
# antes de sembrar (margen de hoy, goal, precio objetivo, origen del
# envio, TACoS). Granos fijados: el margen sale del escenario
# `disponible` mas reciente (lo mismo que copia "margen de hoy" en
# G.2); el objetivo, de la decision de hoy (sin decision no hay
# objetivo: None, nunca un invento); el origen, del escenario
# (`logistica_origen` de la 0054); el TACoS, de `v_tacos` del mes del
# corte maduro (mes de hoy - 15: la unica TACoS del sistema, por
# plataforma y mes, repetida en cada fila).
_SQL_UNIDADES_G3 = """
SELECT l.id, l.seller_sku,
       v.margen_goal_pct, v.mode::text,
       e.contribucion_pct, e.logistica_origen, e.canal::text,
       d.p_objetivo
  FROM listing l
  LEFT JOIN v_precio_goal_vigente v
    ON v.listing_id = l.id AND v.platform = l.platform
  LEFT JOIN LATERAL (
      SELECT contribucion_pct, logistica_origen, canal
        FROM estimacion_escenario
       WHERE listing_id = l.id AND platform = l.platform AND estado = 'disponible'
       ORDER BY observed_at DESC LIMIT 1
  ) e ON true
  LEFT JOIN precio_decision d
    ON d.listing_id = l.id AND d.platform = l.platform AND d.decision_date = %s
 WHERE l.platform = %s::platform
 ORDER BY l.seller_sku
"""

_SQL_TACOS_MES_G3 = """
SELECT tacos_pct FROM v_tacos
 WHERE platform = %s::platform
   AND mes = date_trunc('month', (%s::date - 15))::date
"""


def _unidades_precio(conn: ConexionLectura, plataforma: str, hoy: dt.date) -> list[dict]:
    """Bloque G.3: una fila por unidad con margen de hoy (tanto por uno),
    goal vigente, precio objetivo de hoy, origen del envio y TACoS del
    mes del corte. `None` donde no hay dato (regla 3)."""
    tacos = conn.execute(_SQL_TACOS_MES_G3, (plataforma, hoy)).fetchone()
    tacos_str = _dec_str(tacos[0]) if tacos is not None else None
    filas = []
    for lid, sku, goal, goal_mode, m_pct, origen, canal, p_obj in conn.execute(
        _SQL_UNIDADES_G3, (hoy, plataforma)
    ).fetchall():
        filas.append(
            {
                "listing_id": lid,
                "sku": sku,
                "canal": canal,
                "tiene_goal": goal is not None,
                "margen_hoy": _dec_str(m_pct / 100) if m_pct is not None else None,
                "goal": _dec_str(goal),
                "goal_mode": goal_mode,
                "precio_objetivo": _dec_str(p_obj),
                "envio_origen": origen,
                "tacos": tacos_str,
            }
        )
    return filas


def _divergentes_precio(
    conn: ConexionLectura, plataforma: str, hoy: dt.date, umbral: int
) -> list[dict]:
    """Bloque (e): listings cuyos ultimos `umbral` dias seguidos hasta hoy
    son todos `no_evaluado(precio_divergente)` (una fila por SKU con la
    racha real en `dias` —hasta donde llegue la historia, con tope
    `_TOPE_RACHA_DIVERGENTE`— y el `p_actual` si esta;
    `moneda_divergente` va al (d))."""
    desde = hoy - dt.timedelta(days=_TOPE_RACHA_DIVERGENTE - 1)
    por_listing: dict = {}
    for fila in conn.execute(_SQL_RACHA_RECIENTE, (plataforma, desde, hoy)).fetchall():
        (lid, sku, fecha, resultado, motivo, p_actual, p_moneda) = fila
        entrada = por_listing.setdefault(lid, {"sku": sku, "dias": {}})
        entrada["dias"][fecha] = (resultado, motivo, p_actual, p_moneda)
    divergentes = []
    for entrada in por_listing.values():
        dias = entrada["dias"]
        racha = 0
        dia = hoy
        while racha < _TOPE_RACHA_DIVERGENTE:
            actual = dias.get(dia)
            if actual is None or actual[0] != "no_evaluado" or actual[1] != "precio_divergente":
                break
            racha += 1
            dia -= dt.timedelta(days=1)
        if racha < umbral:
            continue
        hoy_fila = dias[hoy]
        divergentes.append(
            {
                "sku": entrada["sku"],
                "dias": racha,
                "p_actual": _dec_str(hoy_fila[2]),
                "moneda": hoy_fila[3],
            }
        )
    divergentes.sort(key=lambda d: d["sku"])
    return divergentes


def _bloque_precios(
    conn: ConexionLectura, plataforma: str, hoy: dt.date, *, con_recuadro: bool
) -> dict:
    """Bloque de precios de UNA plataforma: hoy, cobertura S10 en vivo,
    decisiones del dia, conteos de /salud y (solo /precios) el recuadro con
    su alias historico mas las listas S7 (`con_goal`, `acciones`,
    `no_evaluados_filas`, `divergentes`)."""
    if not _hay_esquema_precio(conn):
        raise _SinEsquemaPrecio(plataforma)
    settings = fuentes_precio.config_vigente_settings(conn)
    umbral = validar_precio_aviso_dias(settings)
    detalle = _detalle_decisiones_hoy(conn, plataforma, hoy)
    cambios = _cambios_por_decision(conn, [fila["id"] for fila in detalle])
    movieron = {
        dec_id
        for dec_id, cambio in cambios.items()
        if cambio.get("real") is True and cambio.get("estado") in _ESTADOS_PRECIO_MOVIERON
    }
    bloque: dict = {
        "hoy": hoy.isoformat(),
        "decisiones": _decisiones_precio_hoy(conn, plataforma, hoy),
        "huerfanas": _huerfanas_precio(conn, plataforma),
        "cobertura": _recuadro_precio(conn, plataforma, hoy),
        "evaluados": sum(
            1 for fila in detalle if fila["resultado"] in cobertura_precio.RESULTADOS_EVALUADOS
        ),
        "movidos": sum(1 for fila in detalle if fila["mode"] == "live" and fila["id"] in movieron),
        "sombra": sum(
            1
            for fila in detalle
            if fila["mode"] == "shadow" and fila["resultado"] in ("subir", "bajar")
        ),
        "no_evaluados": _conteo_por_motivo(detalle, "no_evaluado"),
        "frenados": _conteo_por_motivo(detalle, "frenado"),
        "goal_inalcanzable": sum(1 for fila in detalle if fila["resultado"] == "goal_inalcanzable"),
    }
    if con_recuadro:
        bloque["recuadro"] = bloque["cobertura"]
        bloque["seguridad"] = _seguridad_precio(conn, plataforma, hoy)
        bloque["con_goal"] = _con_goal_precio(
            conn, plataforma, hoy, {fila["listing_id"]: fila for fila in detalle}
        )
        bloque["unidades"] = _unidades_precio(conn, plataforma, hoy)
        bloque["acciones"] = _acciones_precio(detalle, cambios, plataforma)
        bloque["no_evaluados_filas"] = _filas_no_evaluados(detalle)
        bloque["divergentes"] = _divergentes_precio(conn, plataforma, hoy, umbral)
        bloque["divergentes_umbral"] = umbral
    return bloque


_CLAVES_PRECIOS_SALUD = (
    "hoy",
    "cobertura",
    "evaluados",
    "movidos",
    "sombra",
    "no_evaluados",
    "frenados",
    "goal_inalcanzable",
    "huerfanas",
    "decisiones",
)


_SQL_CORRIDA_CERRADA_HOY = (
    "SELECT 1 FROM precio_corrida WHERE platform = %s AND fecha = %s AND estado = 'cerrada' LIMIT 1"
)


def _corrida_ausente(conn: ConexionLectura, plataforma: str, hoy: dt.date) -> bool:
    """S.6: True si hoy no cerro ninguna corrida de la plataforma (el aviso
    de /salud). Sin tabla de corridas tampoco hay corrida cerrada: True."""
    try:
        return conn.execute(_SQL_CORRIDA_CERRADA_HOY, (plataforma, hoy)).fetchone() is None
    except psycopg.errors.UndefinedTable:
        return True


def _precios_de(conn: ConexionLectura, plataforma: str) -> dict | None:
    """El bloque precios de UNA plataforma para /salud (sin las listas S7,
    que viven solo en /precios). Cada conteo sale de `precio_decision` del
    dia (reloj de la base, no local):

    - `cobertura`: el recuadro S10 con la misma serializacion que el
      `recuadro` de /precios (misma funcion `_recuadro_precio`).
    - `evaluados`: decisiones de hoy con resultado evaluado (`subir`,
      `bajar`, `mantener`, `frenado`, `goal_inalcanzable`).
    - `movidos`: decisiones de hoy en `live` con cambio real no-reversa en
      estado `enviado`/`confirmado`/`no_confirmado` (hubo PATCH aceptado; un
      cambio en `error` o `pendiente` no movio el precio).
    - `sombra`: decisiones de hoy en `shadow` que suben o bajan.
    - `no_evaluados`: decisiones `no_evaluado` de hoy (motivo -> conteo).
    - `frenados`: decisiones `frenado` de hoy (motivo -> conteo).
    - `goal_inalcanzable`: decisiones `goal_inalcanzable` de hoy (conteo).
    - `hoy`, `huerfanas` (pendientes no-reversa) y `decisiones` (por
      resultado) como en /precios.
    - `corrida_ausente` (S.6): True si hoy no cerro ninguna corrida de la
      plataforma (el aviso va DENTRO del bloque, no como clave nueva de
      alto nivel).

    Si la 0039 aun no se aplico, o la config no trae la clave, el bloque
    queda en None + warning y la pantalla NO muere entera (patron
    _spapi_de: degradacion visible, nunca 500)."""
    try:
        hoy = fuentes_precio.hoy_base(conn)
        bloque = _bloque_precios(conn, plataforma, hoy, con_recuadro=False)
        salida = {clave: bloque[clave] for clave in _CLAVES_PRECIOS_SALUD}
        salida["corrida_ausente"] = _corrida_ausente(conn, plataforma, hoy)
        return salida
    except _SinEsquemaPrecio as exc:
        logger.warning(
            "salud: precios %s ilegible (%s): %s",
            plataforma,
            _ERROR_SIN_0039,
            scrub(str(exc)),
        )
        return None
    except psycopg.errors.UndefinedTable as exc:
        logger.warning(
            "salud: precios %s ilegible (%s): %s",
            plataforma,
            _ERROR_SIN_0039,
            scrub(str(exc)),
        )
        return None
    except Exception as exc:  # noqa: BLE001 - degradacion visible, no caida
        logger.warning(
            "salud: precios %s ilegible: %s",
            plataforma,
            scrub(str(exc)),
        )
        return None


@router.get("/precios")
def precios(conn: ConexionLectura) -> dict:
    """Pantalla del motor de precios por plataforma (REPRICING 01 A.6):
    recuadro S10 en vivo + decisiones del dia por resultado + huerfanas
    pendientes + listas S7. Sin esquema de precios o sin la clave de
    config -> 503 con el motivo claro (fail-closed como sin DSN), nunca 500."""
    hoy = fuentes_precio.hoy_base(conn)
    plataformas: dict[str, dict] = {}
    for plataforma in PLATAFORMAS_MONEDA:
        try:
            plataformas[plataforma] = _bloque_precios(conn, plataforma, hoy, con_recuadro=True)
        except _SinEsquemaPrecio as exc:
            raise HTTPException(status_code=503, detail=_ERROR_SIN_0039) from exc
        except psycopg.errors.UndefinedTable as exc:
            raise HTTPException(status_code=503, detail=_ERROR_SIN_0039) from exc
        except ValueError as exc:
            logger.warning(
                "precios: %s ilegible (config): %s",
                plataforma,
                scrub(str(exc)),
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - error claro, nunca 500
            logger.warning(
                "precios: %s ilegible: %s",
                plataforma,
                scrub(str(exc)),
            )
            raise HTTPException(
                status_code=503, detail=f"precios de {plataforma} ilegibles"
            ) from exc
    return {"hoy": hoy.isoformat(), "plataformas": plataformas}
