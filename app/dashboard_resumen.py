"""Contrato del Resumen multicanal (RESUMEN-ADS-02).

Un solo dueno para el catalogo, la ventana, la agregacion, la comparacion y la
media movil del Resumen. La API (`app/api_dashboard.py`) delega aqui y la
presentacion (`app/ui_metricas.py`) reutiliza la MISMA agregacion; no hay dos
copias de las sumas ni de ACoS/ROAS.

Reglas del contrato (plan `plans/resumen-ads-multicanal.md`, seccion
RESUMEN-ADS-02):

1. CATALOGO PROPIO: `amazon_mx` (MXN), `amazon_us` (USD) y `meli` (MXN). NO se
   amplia `PLATAFORMAS_MONEDA` ni el vocabulario del optimizador. Las dos
   entradas de Amazon coinciden con la moneda sellada del motor (lo comprueba
   la prueba monetaria); MeLi trae su moneda del codigo de ADS-01
   (`app/meli/ads_source.py`: site MLM, trigger `meli -> MXN`). Cada canal
   declara tambien su ZONA de dia de reporte (`zona`): Amazon MX y MeLi
   cierran el dia en `America/Mexico_City`, Amazon US en
   `America/Los_Angeles` (el perfil v2 de Amazon trae `timezone`, ver
   `app/ads/structure.py`).
2. VENTANA: periodos de 7, 30 y 90 dias o rango personalizado `desde`/`hasta`.
   La ventana visible termina en el D-1 del "hoy" del Resumen: la fecha local
   MINIMA entre las zonas de los canales pedidos, calculada desde un instante
   inyectable. Asi D-1 esta cerrado en TODOS (entre 18:00 y 24:00 CDMX, UTC ya
   es el dia siguiente pero CDMX no). Un `hasta` >= hoy se RECORTA a D-1 y la
   respuesta declara `hasta_solicitado` y `recortado: true`. Rango invertido,
   fuera del calendario o de mas de 366 dias -> `ValueError` (la API lo
   publica 422).
3. COMPARACION: el periodo anterior de igual longitud (termina el dia previo
   a `desde`). Totales con la misma semantica que el periodo visible. La
   variacion solo se publica si ambos periodos tienen el MISMO numero de dias
   con dato (`comparable`); con cobertura distinta es `None` y `motivo` lo
   declara (un dia faltante contaria como cero en el porcentaje).
4. DINERO: `NUMERIC` -> `Decimal`, jamas float. En JSON viaja como STRING tal
   cual sale de la base (4 decimales, criterio de la serie del dashboard).
5. NULOS Y CEROS: dia sin dato -> todas sus metricas `None` (brecha visible);
   cero conocido -> `0`. Una metrica `NULL` en alguna campana del dia
   envenena ESA metrica del agregado (`bool_and`), jamas una suma parcial.
6. ACoS Y ROAS: desde LAS SUMAS del periodo (`acos = cost/revenue*100`,
   `roas = revenue/cost`), nunca promediando ratios diarios. Denominador cero
   o valor necesario desconocido -> `None`; `sin_ventas` solo se afirma cuando
   la venta esta cubierta en TODOS los dias del rango. Si gasto y ventas no
   cubren exactamente los mismos dias, ACoS/ROAS son `None` (`dinero_alineado`
   en los totales): sumar dias distintos seria un ratio inventado.
7. MA7: ventana movil de 7 dias (el dia y los 6 anteriores). Cada metrica se
   suma en la ventana; si falta CUALQUIER dia de la ventana, su MA7 es `None`
   (no se rellena con cero). `ma7.completo` declara si las dos metricas de
   dinero estan completas en la ventana. Para el primer dia visible se
   consultan 6 dias de calentamiento antes de `desde`.
8. GRANO: solo `ad_entity.kind = 'campaign'` sobre `v_metric_latest` (ultima
   observacion por entidad/dia), para no doble contar keyword/product_target.
9. MADUREZ: Amazon marca `inmaduro` en [D-8, D-1]. MeLi NO hereda esa
   madurez: `maduracion.aplica=false`, `inmaduro=null` y motivo explicito.
10. FRESCURA: `max_metric_date` (ultima fecha con dato de campana),
    `last_ingest_at` (ultima corrida `ok=true` que escribio observaciones de
    campana dentro de la ventana y su calentamiento) y `edad_dias`. No se
    inventa un umbral de frescura: sin corrida -> `last_ingest_at: null`.
11. TOTALES POR MONEDA: una metrica que algun canal del grupo no tiene (p. ej.
    `orders` de MeLi) NO se suma como si fuera completa: queda `None` y
    `cobertura` lo declara por metrica. `completo` exige ademas la cobertura
    por dia de todos los canales del grupo.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.api_common import _dec_str

# Dias inmaduros de Amazon: [D-8, D-1]. Unico dueno del numero; la serie de
# `api_dashboard` importa esta constante (misma ventana, cero copias).
DIAS_INMADUROS = 8

# Tope duro del rango visible (la comparacion puede ir mas atras: la ventana
# anterior mide lo mismo, pero no cuenta contra este tope).
MAX_DIAS_VENTANA = 366

# Periodos cerrados del selector y default.
PERIODOS = (7, 30, 90)
DIAS_PERIODO_DEFAULT = 30

# MA7: 6 dias previos al primero visible.
DIAS_CALENTAMIENTO = 6


@dataclass(frozen=True)
class Canal:
    """Un canal del Resumen: moneda NATIVA, zona de dia de reporte y madurez.

    `zona` es la zona IANA en la que la fuente cierra su dia de reporte. El
    "hoy" del Resumen es la fecha local MINIMA entre los canales pedidos, para
    que D-1 este cerrado en todos. `dias_inmaduros=None` significa "la fuente
    no publica madurez": el contrato lo declara y jamas se rellena con la
    ventana de Amazon.
    """

    plataforma: str
    moneda: str
    zona: str
    dias_inmaduros: int | None
    motivo_sin_madurez: str | None


# Catalogo PROPIO del Resumen (no amplia PLATAFORMAS_MONEDA del optimizador).
CANALES: dict[str, Canal] = {
    # Amazon: el perfil v2 trae `timezone` (app/ads/structure.py:7); el perfil
    # MX cierra en America/Mexico_City y el US en America/Los_Angeles.
    "amazon_mx": Canal("amazon_mx", "MXN", "America/Mexico_City", DIAS_INMADUROS, None),
    "amazon_us": Canal("amazon_us", "USD", "America/Los_Angeles", DIAS_INMADUROS, None),
    # MeLi: site MLM (app/meli/ads_source.py). El sync pide fechas con el
    # reloj UTC, pero el dia que publica la API es el dia local: el 2026-08-21
    # visto a las 19:19 UTC traia 16% del gasto final (evidencia ADS-01).
    "meli": Canal(
        "meli",
        "MXN",
        "America/Mexico_City",
        None,
        (
            "meli no publica madurez de atribucion D-8 (no hereda la de Amazon); "
            "cierra sus cifras en T+1/T+2, asi que su D-1 aun puede cambiar"
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
       CASE WHEN bool_and(v.orders IS NOT NULL) THEN sum(v.orders)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""

# Frescura factica: la fecha MAS NUEVA observada para campanas del canal. Se
# lee la tabla cruda (no v_metric_latest) porque aqui no se agrega ninguna
# metrica: la ultima fecha observada es identica en ambas fuentes y el indice
# por entidad evita el DISTINCT ON completo de la vista (~75 ms -> ~5 ms).
_SQL_MAX_METRIC_DATE = """
SELECT max(o.metric_date)
  FROM ads_metric_observation o
  JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
"""

# Ultima corrida exitosa que escribio observaciones de campana DENTRO de la
# ventana consultada (visible + calentamiento). Un canal sin ingesta que cubra
# la ventana -> None explicito (regla 3: jamas se inventa una frescura).
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


def hoy_por_canal(canales: Iterable[str], instante: dt.datetime) -> dict[str, dt.date]:
    """Fecha local del dia de reporte de cada canal en `instante`.

    La zona vive en el catalogo del Resumen. `instante` debe ser consciente
    (tz-aware): un instante sin zona no define un dia local.
    """
    if instante.tzinfo is None:
        raise ValueError("el instante del resumen debe incluir zona horaria")
    return {
        nombre: instante.astimezone(ZoneInfo(CANALES[nombre].zona)).date() for nombre in canales
    }


def hoy_resumen(canales: Iterable[str], instante: dt.datetime) -> dt.date:
    """`hoy` del Resumen: fecha local MINIMA entre los canales pedidos.

    Asi D-1 esta cerrado en TODOS: a las 23:15 CDMX del 31-oct Amazon MX y
    MeLi siguen en 31-oct aunque UTC ya sea 1-nov. El calculo depende solo del
    instante inyectado, jamas del reloj del proceso.
    """
    return min(hoy_por_canal(canales, instante).values())


def ventana_resumen(
    *,
    periodo: int | None,
    desde: dt.date | None,
    hasta: dt.date | None,
    hoy: dt.date,
) -> Ventana:
    """Ventana visible (termina en D-1) y su comparacion de igual longitud.

    Personalizado cuando vienen `desde` y `hasta` (ambos; uno solo -> error).
    En ese caso `periodo` se ignora y la respuesta usa "personalizado". Un
    `hasta` >= hoy se RECORTA a D-1 (se declara `recortado`); un rango
    invertido, fuera del calendario soportado o de mas de 366 dias es error
    4xx (`ValueError`).
    """
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
    )


@dataclass(frozen=True)
class Dia:
    """Un dia del spine: `None` = sin dato (brecha), `Decimal(0)` = cero real."""

    fecha: dt.date
    cost: Decimal | None
    ad_revenue: Decimal | None
    clicks: int | None
    orders: int | None


@dataclass(frozen=True)
class Agregado:
    """Sumas y cobertura de una lista de dias (o de filas de serie).

    `dias_pares` cuenta los dias con gasto Y venta conocidos;
    `dinero_alineado` exige que gasto, venta y pares cubran el mismo conjunto
    de dias: solo entonces el ACoS/ROAS de las sumas es un ratio honesto.
    """

    cost: Decimal | None
    ad_revenue: Decimal | None
    clicks: int | None
    orders: int | None
    dias_cost: int
    dias_ad_revenue: int
    dias_clicks: int
    dias_orders: int
    dias_pares: int
    dias_con_dato: int

    @property
    def dinero_alineado(self) -> bool:
        return (
            self.dias_cost > 0
            and self.dias_cost == self.dias_ad_revenue
            and self.dias_cost == self.dias_pares
        )


def _decimal(valor) -> Decimal | None:
    """Dinero/entero de una fila a Decimal exacto. `None` pasa como `None`."""
    if valor is None:
        return None
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def _entero(valor) -> int | None:
    return None if valor is None else int(valor)


def agrega_filas(filas: Iterable[Mapping]) -> Agregado:
    """Suma SOLO los valores conocidos y declara la cobertura de cada uno.

    Jamas convierte un `None` en cero: si no hay ningun dia con la metrica, la
    suma es `None`. Es la MISMA agregacion para el Resumen y para la
    presentacion que ya usaba `ui_metricas`.
    """
    cost = Decimal(0)
    ad_revenue = Decimal(0)
    clicks = 0
    orders = 0
    dias_cost = dias_ad_revenue = dias_clicks = dias_orders = 0
    dias_pares = dias_con_dato = 0
    for fila in filas:
        valor_cost = _decimal(fila.get("cost"))
        valor_revenue = _decimal(fila.get("ad_revenue"))
        valor_clicks = _entero(fila.get("clicks"))
        valor_orders = _entero(fila.get("orders"))
        if valor_cost is not None:
            cost += valor_cost
            dias_cost += 1
        if valor_revenue is not None:
            ad_revenue += valor_revenue
            dias_ad_revenue += 1
        if valor_clicks is not None:
            clicks += valor_clicks
            dias_clicks += 1
        if valor_orders is not None:
            orders += valor_orders
            dias_orders += 1
        if valor_cost is not None and valor_revenue is not None:
            dias_pares += 1
        if any(
            valor is not None for valor in (valor_cost, valor_revenue, valor_clicks, valor_orders)
        ):
            dias_con_dato += 1
    return Agregado(
        cost=cost if dias_cost else None,
        ad_revenue=ad_revenue if dias_ad_revenue else None,
        clicks=clicks if dias_clicks else None,
        orders=orders if dias_orders else None,
        dias_cost=dias_cost,
        dias_ad_revenue=dias_ad_revenue,
        dias_clicks=dias_clicks,
        dias_orders=dias_orders,
        dias_pares=dias_pares,
        dias_con_dato=dias_con_dato,
    )


def acos_texto(cost: Decimal | None, ad_revenue: Decimal | None) -> str | None:
    """ACoS de DOS SUMAS: `cost/revenue*100`, 2 decimales, STRING.

    Venta cero o desconocida -> `None` (infinito o dato faltante: jamas
    division). Costo desconocido -> `None`. Redondeo HALF_UP explicito (la
    convencion comercial del dashboard, no el half-even del contexto).
    """
    if cost is None or ad_revenue is None or ad_revenue == 0:
        return None
    ratio = (cost / ad_revenue * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(ratio)


def roas_texto(cost: Decimal | None, ad_revenue: Decimal | None) -> str | None:
    """ROAS de DOS SUMAS: `revenue/cost`, 2 decimales, STRING.

    Gasto cero o desconocido -> `None` (jamas division); venta desconocida ->
    `None`.
    """
    if cost is None or ad_revenue is None or cost == 0:
        return None
    ratio = (ad_revenue / cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(ratio)


def totales_dias(dias: Sequence[Dia], dias_rango: int) -> dict:
    """Totales del rango desde las sumas (regla 6).

    `sin_ventas` solo se afirma cuando la venta esta cubierta en TODOS los dias
    del rango y suma cero: con dias desconocidos no se puede concluir.
    `dinero_alineado` explica por que ACoS/ROAS pueden ser `None` aunque haya
    totales.
    """
    agregado = agrega_filas(
        {
            "cost": dia.cost,
            "ad_revenue": dia.ad_revenue,
            "clicks": dia.clicks,
            "orders": dia.orders,
        }
        for dia in dias
    )
    alineado = agregado.dinero_alineado
    return {
        "cost": _dec_str(agregado.cost),
        "ad_revenue": _dec_str(agregado.ad_revenue),
        "clicks": agregado.clicks,
        "orders": agregado.orders,
        "acos": acos_texto(agregado.cost, agregado.ad_revenue) if alineado else None,
        "roas": roas_texto(agregado.cost, agregado.ad_revenue) if alineado else None,
        "sin_ventas": (
            agregado.ad_revenue is not None
            and agregado.ad_revenue == 0
            and agregado.dias_ad_revenue == dias_rango
        ),
        "dias_con_dato": agregado.dias_con_dato,
        "dinero_alineado": alineado,
    }


def arma_dias(por_fecha: Mapping[dt.date, tuple], desde: dt.date, hasta: dt.date) -> list[Dia]:
    """Spine completo [desde, hasta]: fecha sin fila -> todo `None` (jamas 0)."""
    dias: list[Dia] = []
    fecha = desde
    while fecha <= hasta:
        fila = por_fecha.get(fecha)
        dias.append(
            Dia(
                fecha=fecha,
                cost=None if fila is None else fila[0],
                ad_revenue=None if fila is None else fila[1],
                clicks=None if fila is None else fila[2],
                orders=None if fila is None else fila[3],
            )
        )
        fecha += dt.timedelta(days=1)
    return dias


def _suma_ventana(valores: Sequence) -> Decimal | int | None:
    """Suma de una metrica en la ventana; un solo dia desconocido -> None."""
    if any(valor is None for valor in valores):
        return None
    return sum(valores)


def _ma7(ventana: Sequence[Dia]) -> dict:
    """MA7 del dia: cada metrica suma 7 dias; faltante -> None (no cero).

    `completo` declara si las DOS metricas de dinero tienen sus 7 dias.
    ACoS/ROAS del MA7 salen de esas sumas, jamas del promedio de ratios.
    """
    cost = _suma_ventana([dia.cost for dia in ventana])
    ad_revenue = _suma_ventana([dia.ad_revenue for dia in ventana])
    clicks = _suma_ventana([dia.clicks for dia in ventana])
    orders = _suma_ventana([dia.orders for dia in ventana])
    return {
        "cost": _dec_str(cost),
        "ad_revenue": _dec_str(ad_revenue),
        "clicks": clicks,
        "orders": orders,
        "acos": acos_texto(cost, ad_revenue),
        "roas": roas_texto(cost, ad_revenue),
        "completo": all(dia.cost is not None and dia.ad_revenue is not None for dia in ventana),
    }


def _inmaduro(fecha: dt.date, hoy: dt.date, canal: Canal) -> bool | None:
    """Amazon: [D-8, D-1]. Canal sin madurez publicada -> `None` (no se inventa)."""
    if canal.dias_inmaduros is None:
        return None
    return hoy - dt.timedelta(days=canal.dias_inmaduros) <= fecha <= hoy - dt.timedelta(days=1)


def fila_serie(dia: Dia, ma7: dict | None, canal: Canal, hoy: dt.date) -> dict:
    """Una fila visible: valores del dia, ACoS/ROAS del dia y su MA7.

    Dia sin dato -> todas las metricas `None` (brecha). Cero conocido -> `0`.
    """
    return {
        "fecha": dia.fecha.isoformat(),
        "cost": _dec_str(dia.cost),
        "ad_revenue": _dec_str(dia.ad_revenue),
        "clicks": dia.clicks,
        "orders": dia.orders,
        "acos": acos_texto(dia.cost, dia.ad_revenue),
        "roas": roas_texto(dia.cost, dia.ad_revenue),
        "sin_ventas": dia.ad_revenue is not None and dia.ad_revenue == 0,
        "inmaduro": _inmaduro(dia.fecha, hoy, canal),
        "ma7": ma7,
    }


def serie_publica(
    por_fecha: Mapping[dt.date, tuple],
    desde: dt.date,
    hasta: dt.date,
    canal: Canal,
    hoy: dt.date,
) -> list[dict]:
    """Un punto por dia del rango visible; MA7 con 6 dias de calentamiento."""
    inicio_ma7 = desde - dt.timedelta(days=DIAS_CALENTAMIENTO)
    por_dia = {dia.fecha: dia for dia in arma_dias(por_fecha, inicio_ma7, hasta)}
    filas: list[dict] = []
    fecha = desde
    while fecha <= hasta:
        ventana = [
            por_dia[fecha - dt.timedelta(days=atras)] for atras in range(DIAS_CALENTAMIENTO + 1)
        ]
        filas.append(fila_serie(por_dia[fecha], _ma7(ventana), canal, hoy))
        fecha += dt.timedelta(days=1)
    return filas


def cobertura(dias: Sequence[Dia], dias_rango: int) -> dict:
    """Dias con dato / dias del rango y dias faltantes, por metrica."""
    agregado = agrega_filas(
        {
            "cost": dia.cost,
            "ad_revenue": dia.ad_revenue,
            "clicks": dia.clicks,
            "orders": dia.orders,
        }
        for dia in dias
    )
    return {
        "dias_rango": dias_rango,
        "dias_con_dato": agregado.dias_con_dato,
        "dias_faltantes": dias_rango - agregado.dias_con_dato,
        "por_metrica": {
            "cost": agregado.dias_cost,
            "ad_revenue": agregado.dias_ad_revenue,
            "clicks": agregado.dias_clicks,
            "orders": agregado.dias_orders,
        },
        "completo": agregado.dias_con_dato == dias_rango,
    }


def _pct(nuevo, anterior) -> str | None:
    """Variacion porcentual exacta; base desconocida o cero -> `None`."""
    if nuevo is None or anterior is None:
        return None
    valor_nuevo, valor_anterior = _decimal(nuevo), _decimal(anterior)
    if valor_nuevo is None or valor_anterior is None or valor_anterior == 0:
        return None
    variacion = ((valor_nuevo - valor_anterior) / valor_anterior * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return str(variacion)


def variacion(actual: Mapping, anterior: Mapping) -> dict:
    """Cambio contra el periodo anterior: % en volumen y puntos en ACoS.

    Solo se publica si AMBOS periodos tienen el mismo numero de dias con dato:
    un dia faltante contaria como cero en el porcentaje. Con cobertura
    distinta todas las variaciones son `None` y `motivo` lo declara
    (`comparable: false`). `roas_delta` es la diferencia absoluta del ratio
    (no porcentaje). Cualquier lado desconocido -> `None`.
    """
    dias_actual = actual.get("dias_con_dato")
    dias_anterior = anterior.get("dias_con_dato")
    comparable = dias_actual is not None and dias_actual == dias_anterior
    acos_actual, acos_anterior = _decimal(actual.get("acos")), _decimal(anterior.get("acos"))
    roas_actual, roas_anterior = _decimal(actual.get("roas")), _decimal(anterior.get("roas"))
    dos_dec = Decimal("0.01")
    return {
        "cost_pct": _pct(actual.get("cost"), anterior.get("cost")) if comparable else None,
        "ad_revenue_pct": (
            _pct(actual.get("ad_revenue"), anterior.get("ad_revenue")) if comparable else None
        ),
        "clicks_pct": _pct(actual.get("clicks"), anterior.get("clicks")) if comparable else None,
        "acos_pp": (
            None
            if not comparable or acos_actual is None or acos_anterior is None
            else str((acos_actual - acos_anterior).quantize(dos_dec, rounding=ROUND_HALF_UP))
        ),
        "roas_delta": (
            None
            if not comparable or roas_actual is None or roas_anterior is None
            else str((roas_actual - roas_anterior).quantize(dos_dec, rounding=ROUND_HALF_UP))
        ),
        "comparable": comparable,
        "motivo": None if comparable else "cobertura_distinta",
    }


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


def bloque_canal(conn, canal: Canal, ventana: Ventana, hoy: dt.date) -> dict:
    """Bloque de un canal: totales, comparacion, serie, cobertura y frescura.

    Una sola consulta de dias cubre la ventana visible, su calentamiento y el
    periodo anterior (continuo hacia atras); el resto son dos agregados chicos
    (ultima fecha y ultima ingesta).
    """
    inicio = min(ventana.anterior_desde, ventana.desde - dt.timedelta(days=DIAS_CALENTAMIENTO))
    filas = conn.execute(_SQL_DIAS, (canal.plataforma, inicio, ventana.hasta)).fetchall()
    por_fecha = {fila[0]: (fila[1], fila[2], fila[3], fila[4]) for fila in filas}
    dias_actuales = arma_dias(por_fecha, ventana.desde, ventana.hasta)
    dias_anteriores = arma_dias(por_fecha, ventana.anterior_desde, ventana.anterior_hasta)
    totals = totales_dias(dias_actuales, ventana.dias)
    anterior = totales_dias(dias_anteriores, ventana.dias)
    max_fecha = max_metric_date(conn, canal.plataforma)
    ingest = last_ingest_at(
        conn,
        canal.plataforma,
        ventana.desde - dt.timedelta(days=DIAS_CALENTAMIENTO),
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
            "variacion": variacion(totals, anterior),
        },
        "series": serie_publica(por_fecha, ventana.desde, ventana.hasta, canal, hoy),
        "coverage": cobertura(dias_actuales, ventana.dias),
        "max_metric_date": None if max_fecha is None else max_fecha.isoformat(),
        "last_ingest_at": None if ingest is None else ingest.isoformat(),
        "edad_dias": None if max_fecha is None else (hoy - max_fecha).days,
        "maduracion": _maduracion(canal, hoy),
    }


def _suma_conocidos(valores: Sequence) -> Decimal | None:
    """Suma de los totales conocidos; `None` si ninguno lo es (jamas 0)."""
    conocidos = [_decimal(valor) for valor in valores]
    conocidos = [valor for valor in conocidos if valor is not None]
    return sum(conocidos) if conocidos else None


_METRICAS_TOTALES = ("cost", "ad_revenue", "clicks", "orders")


def _suma_grupo(totales: Sequence[Mapping], metrica: str) -> Decimal | None:
    """Suma de una metrica solo si TODOS los canales del grupo la tienen.

    Una metrica que un canal no publica (p. ej. `orders` de MeLi siempre NULL)
    jamas se suma como si fuera completa: queda `None` y la cobertura por
    metrica lo declara.
    """
    valores = [total.get(metrica) for total in totales]
    if any(valor is None for valor in valores):
        return None
    return _suma_conocidos(valores)


def _bloque_con_cobertura_completa(bloque: Mapping) -> bool:
    """El canal cubre TODOS los dias del rango (`coverage.completo`)."""
    cobertura = bloque.get("coverage")
    if cobertura is not None:
        return bool(cobertura.get("completo"))
    dias = bloque.get("dias")
    return dias is not None and bloque["totals"].get("dias_con_dato") == dias


def totales_por_moneda(bloques: Mapping[str, Mapping]) -> list[dict]:
    """Totales multi-canal AGRUPADOS por moneda (jamas una moneda mezclada).

    Cada metrica se suma solo si TODOS los canales del grupo la tienen; si
    alguno no la publica, la metrica del grupo es `None` (nunca una suma
    parcial disfrazada). `cobertura` declara, metrica por metrica, si el grupo
    esta completo, y `dias` si todos los canales cubren todos los dias del
    rango. `completo` exige ambas cosas. ACoS/ROAS se publican solo con dinero
    completo y alineado en todos los canales; en cualquier otro caso son
    `None` (no se inventa un ratio entre dias distintos). `canales_sin_dato`
    lista los que no aportaron dinero.
    """
    grupos: dict[str, list[tuple[str, Mapping]]] = {}
    for nombre, bloque in bloques.items():
        grupos.setdefault(bloque["moneda"], []).append((nombre, bloque))
    salida: list[dict] = []
    for moneda in sorted(grupos):
        miembros = grupos[moneda]
        totales = [bloque["totals"] for _nombre, bloque in miembros]
        dias_completos = all(_bloque_con_cobertura_completa(bloque) for _nombre, bloque in miembros)
        cobertura = {
            metrica: all(total.get(metrica) is not None for total in totales)
            for metrica in _METRICAS_TOTALES
        }
        cobertura["dias"] = dias_completos
        completo = all(cobertura.values())
        alineado = (
            cobertura["cost"]
            and cobertura["ad_revenue"]
            and dias_completos
            and all(bool(total.get("dinero_alineado")) for total in totales)
        )
        cost = _suma_grupo(totales, "cost")
        ad_revenue = _suma_grupo(totales, "ad_revenue")
        clicks = _suma_grupo(totales, "clicks")
        orders = _suma_grupo(totales, "orders")
        salida.append(
            {
                "moneda": moneda,
                "canales": [nombre for nombre, _bloque in miembros],
                "canales_sin_dato": [
                    nombre
                    for nombre, bloque in miembros
                    if bloque["totals"].get("cost") is None
                    and bloque["totals"].get("ad_revenue") is None
                ],
                "cost": _dec_str(cost),
                "ad_revenue": _dec_str(ad_revenue),
                "clicks": None if clicks is None else int(clicks),
                "orders": None if orders is None else int(orders),
                "acos": acos_texto(cost, ad_revenue) if alineado else None,
                "roas": roas_texto(cost, ad_revenue) if alineado else None,
                "sin_ventas": (
                    alineado
                    and ad_revenue is not None
                    and ad_revenue == 0
                    and all(bool(total.get("sin_ventas")) for total in totales)
                ),
                "cobertura": cobertura,
                "completo": completo,
            }
        )
    return salida


def resumen(
    conn,
    *,
    ventana: Ventana,
    canales: Sequence[str],
    hoy: dt.date,
    instante: dt.datetime,
) -> dict:
    """Respuesta del Resumen: rango efectivo, bloques por canal y monedas.

    `hoy` es la fecha local MINIMA de los canales pedidos; la respuesta
    declara el instante UTC y la zona de cada canal con los que se calculo.
    """
    fechas = hoy_por_canal(canales, instante)
    bloques = {nombre: bloque_canal(conn, CANALES[nombre], ventana, hoy) for nombre in canales}
    return {
        "hoy": hoy.isoformat(),
        "hoy_instante": instante.astimezone(dt.UTC).isoformat(),
        "zonas": {nombre: CANALES[nombre].zona for nombre in canales},
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
