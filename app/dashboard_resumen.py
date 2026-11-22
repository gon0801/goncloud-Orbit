"""Contrato del Resumen multicanal (RESUMEN-ADS-02).

Un solo dueno para el catalogo, la ventana, la agregacion, la comparacion y la
media movil del Resumen. La API (`app/api_dashboard.py`) delega aqui y la
presentacion (`app/ui_metricas.py`) reutiliza la MISMA agregacion; no hay dos
copias de las sumas ni de ACoS/ROAS.

Los calculos puros sin SQL (agregacion, ACoS/ROAS, MA7, cobertura, cierre,
variacion y totales por moneda) viven en `app/dashboard_resumen_calc.py` y se
reexportan aqui para conservar la interfaz publica del modulo.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.dashboard_resumen_calc import (  # noqa: F401 - reexport contrato publico
    _CUATRO_DEC,
    _DOS_DEC,
    _METRICAS_TOTALES,
    DIAS_CALENTAMIENTO,
    DIAS_MA7,
    Agregado,
    Canal,
    Dia,
    _cerrado,
    _cobertura_variacion,
    _decimal,
    _dinero_variacion,
    _entero,
    _inmaduro,
    _ma7,
    _pct,
    _promedio_ventana,
    _suma_conocidos,
    _suma_grupo,
    _suma_ventana,
    acos_texto,
    agrega_filas,
    arma_dias,
    cierre_cobertura,
    cobertura,
    fila_serie,
    roas_texto,
    serie_publica,
    totales_dias,
    totales_por_moneda,
    variacion,
)
from app.dia_cerrado import ZONAS, dia_local

# Dias inmaduros de Amazon: [D-8, D-1]. Unico dueno del numero; la serie de
# `api_dashboard` importa esta constante (misma ventana, cero copias).
DIAS_INMADUROS = 8

# Tope duro del rango visible (la comparacion puede ir mas atras: la ventana
# anterior mide lo mismo, pero no cuenta contra este tope).
MAX_DIAS_VENTANA = 366

# Periodos cerrados del selector y default.
PERIODOS = (7, 30, 90)
DIAS_PERIODO_DEFAULT = 30

# Catalogo PROPIO del Resumen (no amplia PLATAFORMAS_MONEDA del optimizador).
# Las zonas de cada canal viven en `app.dia_cerrado.ZONAS`.
CANALES: dict[str, Canal] = {
    "amazon_mx": Canal("amazon_mx", "MXN", DIAS_INMADUROS, None),
    "amazon_us": Canal("amazon_us", "USD", DIAS_INMADUROS, None),
    "meli": Canal(
        "meli",
        "MXN",
        None,
        (
            "Mercado Libre no dice cuándo maduran sus ventas atribuidas: cierra sus "
            "cifras uno o dos días después, así que lo de ayer aún puede cambiar"
        ),
    ),
}

CANALES_POR_DEFECTO: tuple[str, ...] = ("amazon_mx", "amazon_us", "meli")

# Solo grano campana sobre la ultima observacion por (entidad, dia).
_SQL_DIAS = """
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END,
       CASE WHEN bool_and(v.orders IS NOT NULL) THEN sum(v.orders)::bigint END,
       max(v.observed_at)
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""

# Mismas columnas y mismo colapso que _SQL_DIAS (v_metric_latest es DISTINCT ON
# por entidad y dia), con la ultima lectura hasta un corte: el periodo anterior
# se lee tal como se veia hace los mismos dias, a la misma edad que el actual.
_SQL_DIAS_A_LA_EDAD = """
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END,
       CASE WHEN bool_and(v.orders IS NOT NULL) THEN sum(v.orders)::bigint END,
       max(v.observed_at)
  FROM (SELECT DISTINCT ON (o.ad_entity_id, o.metric_date)
               o.metric_date, o.cost, o.ad_revenue, o.clicks, o.orders, o.observed_at
          FROM ads_metric_observation o
          JOIN ad_entity e ON e.id = o.ad_entity_id
         WHERE e.kind = 'campaign' AND e.platform = %s::platform
           AND o.metric_date BETWEEN %s AND %s
           AND o.observed_at <= %s
         ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC) v
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""

# Frescura factica: la fecha MAS NUEVA observada para campanas del canal.
_SQL_MAX_METRIC_DATE = """
SELECT max(o.metric_date)
  FROM ads_metric_observation o
  JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
"""

# Ultima corrida exitosa que escribio observaciones de campana DENTRO de la
# ventana consultada (visible + calentamiento).
_SQL_LAST_INGEST = """
SELECT max(r.finished_at)
  FROM ingest_run r
  JOIN ads_metric_observation o ON o.ingest_run_id = r.id
  JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE r.ok = true
   AND e.kind = 'campaign'
   AND e.platform = %s::platform
   AND o.metric_date BETWEEN %s AND %s
"""


@dataclass(frozen=True)
class Ventana:
    """Rango visible ya validado y su periodo anterior de igual longitud."""

    desde: dt.date
    hasta: dt.date
    dias: int
    periodo: int | str
    hasta_solicitado: dt.date
    recortado: bool
    anterior_desde: dt.date
    anterior_hasta: dt.date
    calentamiento_desde: dt.date


def hoy_por_canal(canales: Iterable[str], instante: dt.datetime) -> dict[str, dt.date]:
    """Fecha local del dia de reporte de cada canal en `instante`."""
    if instante.tzinfo is None:
        raise ValueError("el instante del resumen debe incluir zona horaria")
    return {nombre: dia_local(CANALES[nombre].plataforma, instante) for nombre in canales}


def hoy_resumen(canales: Iterable[str], instante: dt.datetime) -> dt.date:
    """`hoy` del Resumen: fecha local MINIMA entre los canales pedidos."""
    return min(hoy_por_canal(canales, instante).values())


def ventana_resumen(
    *,
    periodo: int | None,
    desde: dt.date | None,
    hasta: dt.date | None,
    hoy: dt.date,
) -> Ventana:
    """Ventana visible (termina en D-1) y su comparacion de igual longitud."""
    ultimo = hoy - dt.timedelta(days=1)
    if (desde is None) != (hasta is None):
        raise ValueError("desde y hasta van juntos: el rango personalizado pide ambos")
    if desde is not None and hasta is not None:
        hasta_solicitado = hasta
        hasta_ef = hasta if hasta <= ultimo else ultimo
        recortado = hasta > ultimo
        if desde > hasta_ef:
            raise ValueError("desde no puede ser posterior a hasta (el dia en curso esta excluido)")
        dias = (hasta_ef - desde).days + 1
        etiqueta: int | str = "personalizado"
    else:
        if periodo not in PERIODOS:
            raise ValueError(f"periodo fuera del vocabulario {PERIODOS}: {periodo!r}")
        dias = periodo
        hasta_ef = ultimo
        hasta_solicitado = ultimo
        recortado = False
        desde = hasta_ef - dt.timedelta(days=dias - 1)
        etiqueta = periodo
    if dias > MAX_DIAS_VENTANA:
        raise ValueError(f"ventana de mas de {MAX_DIAS_VENTANA} dias: acota el rango")
    try:
        anterior_hasta = desde - dt.timedelta(days=1)
        anterior_desde = anterior_hasta - dt.timedelta(days=dias - 1)
        calentamiento_desde = desde - dt.timedelta(days=DIAS_CALENTAMIENTO)
    except OverflowError:
        raise ValueError(
            "rango fuera del calendario soportado: desde es demasiado antiguo"
        ) from None
    return Ventana(
        desde=desde,
        hasta=hasta_ef,
        dias=dias,
        periodo=etiqueta,
        hasta_solicitado=hasta_solicitado,
        recortado=recortado,
        anterior_desde=anterior_desde,
        anterior_hasta=anterior_hasta,
        calentamiento_desde=calentamiento_desde,
    )


def _maduracion(canal: Canal, hoy: dt.date) -> dict:
    """Ventana de inmadurez del canal; sin madurez publicada queda explicito."""
    if canal.dias_inmaduros is None:
        return {
            "aplica": False,
            "dias_inmaduros": None,
            "ventana_inmaduros": None,
            "motivo": canal.motivo_sin_madurez,
        }
    return {
        "aplica": True,
        "dias_inmaduros": canal.dias_inmaduros,
        "ventana_inmaduros": {
            "desde": (hoy - dt.timedelta(days=canal.dias_inmaduros)).isoformat(),
            "hasta": (hoy - dt.timedelta(days=1)).isoformat(),
        },
        "motivo": None,
    }


def max_metric_date(conn, plataforma: str) -> dt.date | None:
    """Ultima fecha con observacion de campana (frescura factica del canal)."""
    fila = conn.execute(_SQL_MAX_METRIC_DATE, (plataforma,)).fetchone()
    return None if fila is None else fila[0]


def last_ingest_at(conn, plataforma: str, desde: dt.date, hasta: dt.date) -> dt.datetime | None:
    """Ultima corrida exitosa que cubrio la ventana consultada (o `None`)."""
    fila = conn.execute(_SQL_LAST_INGEST, (plataforma, desde, hasta)).fetchone()
    valor = None if fila is None else fila[0]
    return None if valor is None else valor.astimezone(dt.UTC)


def bloque_canal(
    conn, canal: Canal, ventana: Ventana, hoy: dt.date, *, instante: dt.datetime
) -> dict:
    """Bloque de un canal: totales, comparacion, serie, cobertura y frescura."""
    filas = conn.execute(
        _SQL_DIAS, (canal.plataforma, ventana.calentamiento_desde, ventana.hasta)
    ).fetchall()
    por_fecha = {fila[0]: (fila[1], fila[2], fila[3], fila[4], fila[5]) for fila in filas}
    dias_actuales = arma_dias(por_fecha, ventana.desde, ventana.hasta)
    if canal.dias_inmaduros is None:
        corte = None
        filas_ant = conn.execute(
            _SQL_DIAS, (canal.plataforma, ventana.anterior_desde, ventana.anterior_hasta)
        ).fetchall()
    else:
        corte = instante - dt.timedelta(days=ventana.dias)
        filas_ant = conn.execute(
            _SQL_DIAS_A_LA_EDAD,
            (canal.plataforma, ventana.anterior_desde, ventana.anterior_hasta, corte),
        ).fetchall()
    por_fecha_ant = {fila[0]: (fila[1], fila[2], fila[3], fila[4], fila[5]) for fila in filas_ant}
    dias_anteriores = arma_dias(por_fecha_ant, ventana.anterior_desde, ventana.anterior_hasta)
    totals = totales_dias(dias_actuales, ventana.dias)
    anterior = totales_dias(dias_anteriores, ventana.dias)
    max_fecha = max_metric_date(conn, canal.plataforma)
    ingest = last_ingest_at(
        conn,
        canal.plataforma,
        ventana.calentamiento_desde,
        ventana.hasta,
    )
    return {
        "plataforma": canal.plataforma,
        "moneda": canal.moneda,
        "granularidad": "campaign",
        "desde": ventana.desde.isoformat(),
        "hasta": ventana.hasta.isoformat(),
        "dias": ventana.dias,
        "totals": totals,
        "comparison": {
            "desde": ventana.anterior_desde.isoformat(),
            "hasta": ventana.anterior_hasta.isoformat(),
            "dias": ventana.dias,
            "totals": anterior,
            "observado_hasta": None if corte is None else corte.astimezone(dt.UTC).isoformat(),
            "variacion": variacion(totals, anterior),
        },
        "series": serie_publica(por_fecha, ventana.desde, ventana.hasta, canal, hoy),
        "coverage": cobertura(dias_actuales, ventana.dias, canal),
        "max_metric_date": None if max_fecha is None else max_fecha.isoformat(),
        "last_ingest_at": None if ingest is None else ingest.isoformat(),
        "edad_dias": None if max_fecha is None else (hoy - max_fecha).days,
        "maduracion": _maduracion(canal, hoy),
    }


def resumen(
    conn,
    *,
    ventana: Ventana,
    canales: Sequence[str],
    hoy: dt.date,
    instante: dt.datetime,
) -> dict:
    """Respuesta del Resumen: rango efectivo, bloques por canal y monedas."""
    fechas = hoy_por_canal(canales, instante)
    bloques = {
        nombre: bloque_canal(conn, CANALES[nombre], ventana, hoy, instante=instante)
        for nombre in canales
    }
    return {
        "hoy": hoy.isoformat(),
        "hoy_instante": instante.astimezone(dt.UTC).isoformat(),
        "zonas": {nombre: ZONAS[CANALES[nombre].plataforma] for nombre in canales},
        "hoy_por_canal": {nombre: fecha.isoformat() for nombre, fecha in fechas.items()},
        "desde": ventana.desde.isoformat(),
        "hasta": ventana.hasta.isoformat(),
        "dias": ventana.dias,
        "periodo": ventana.periodo,
        "hasta_solicitado": ventana.hasta_solicitado.isoformat(),
        "recortado": ventana.recortado,
        "granularidad": "campaign",
        "canales": bloques,
        "totales_por_moneda": totales_por_moneda(bloques),
    }
