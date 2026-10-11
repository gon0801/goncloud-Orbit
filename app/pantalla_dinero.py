"""Donde poner el dinero (BIDS 02, P.1): contrato puro, sin plantillas.

Suma HOJAS activas por tipo de campana (`v_hoja_activa` trae el tipo:
primero AUTO de la campana, despues match type o product target). Nunca
filas de campana: sumar campanas y hojas duplicaria el gasto. Nunca MX
con US: una tabla por mercado.

Veneno por metrica (precedente `_SQL_CAMPANAS_30D`): si alguna fila del
grupo trae la metrica NULL, la suma del grupo es NULL (no se publica un
parcial como completo); el total es NULL si algun grupo lo es. Venta 0
da ACoS NULL ("sin ventas", nunca division). Tipo NULL o fuera de los
cinco entra al total, no a los renglones, y se cuenta aparte.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal, get_args

from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.goals import gasto_para_concluir_desde_settings

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]
TipoCampana = Literal["exact", "phrase", "broad", "automatica", "product_targeting"]
Ubicacion = Literal[
    "arriba_de_busqueda", "resto_de_busqueda", "paginas_de_producto", "fuera_de_amazon"
]
EstrategiaPuja = Literal["solo_hacia_abajo", "arriba_y_abajo", "fija", "otra"]

TIPOS: tuple[str, ...] = get_args(TipoCampana)
UBICACIONES: tuple[str, ...] = get_args(Ubicacion)
DIAS_UBICACION = 30
DIAS_TOPE = 7

_ESTRATEGIA = {
    "LEGACY_FOR_SALES": "solo_hacia_abajo",
    "AUTO_FOR_SALES": "arriba_y_abajo",
    "MANUAL": "fija",
}
_AJUSTE_A_UBICACION = (
    ("arriba_de_busqueda", 0),
    ("resto_de_busqueda", 1),
    ("paginas_de_producto", 2),
)

_SQL_GRUPOS = """
WITH h AS (
    SELECT hoja_id, tipo_campana FROM v_hoja_activa WHERE platform = %s)
SELECT h.tipo_campana AS tipo,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END AS gasto,
       CASE WHEN bool_and(v.orders IS NOT NULL) THEN sum(v.orders)::bigint END AS pedidos,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END AS venta,
       count(DISTINCT h.hoja_id) AS hojas
  FROM h
  JOIN LATERAL (
        SELECT DISTINCT ON (o.metric_date) o.cost, o.orders, o.ad_revenue
          FROM ads_metric_observation o
         WHERE o.ad_entity_id = h.hoja_id
           AND o.metric_date BETWEEN %s AND %s
         ORDER BY o.metric_date, o.observed_at DESC) v ON true
 GROUP BY h.tipo_campana
"""
# El LATERAL equivale a v_metric_latest por hoja (DISTINCT ON fecha con la
# observacion mas reciente) pero recorre la tabla por indice, una vez por
# hoja, en vez de una vez entera por hoja: medido 2026-10-11 sobre la copia
# (173,850 filas), MX pasa de 19.87 s a 0.04 s con numeros identicos
# (ronda 3, B3; evidencia en docs/evidencia/bids-02/ejecucion/P.1/).

_SQL_UBICACIONES = """
SELECT u.placement, sum(u.cost), sum(u.clicks)::bigint, sum(u.orders)::bigint, sum(u.ad_revenue)
  FROM (SELECT DISTINCT ON (o.ad_entity_id, o.placement, o.metric_date)
               o.placement, o.cost, o.clicks, o.orders, o.ad_revenue
          FROM ads_placement_observation o
          JOIN ad_entity_state s ON s.ad_entity_id = o.ad_entity_id AND s.status = 'ENABLED'
         WHERE o.platform = %s AND o.metric_date BETWEEN %s AND %s
         ORDER BY o.ad_entity_id, o.placement, o.metric_date, o.observed_at DESC) u
 GROUP BY u.placement
"""

_SQL_SETTINGS = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""

_SQL_CAMPANAS = """
SELECT c.id, c.name, v.id, v.presupuesto_diario, v.estrategia_puja,
       v.ajuste_top_pct, v.ajuste_resto_pct, v.ajuste_producto_pct
  FROM ad_entity c
  JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED'
  LEFT JOIN v_campana_config_vigente v ON v.ad_entity_id = c.id
 WHERE c.platform = %s AND c.kind = 'campaign'
 ORDER BY c.id
"""

_SQL_GASTO_DIARIO = """
SELECT v.ad_entity_id, v.metric_date, v.cost
  FROM v_metric_latest v
  JOIN ad_entity c ON c.id = v.ad_entity_id
  JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED'
 WHERE c.platform = %s AND c.kind = 'campaign'
   AND v.metric_date BETWEEN %s AND %s
"""

_SQL_FUERA_CAMPANA = """
SELECT u.ad_entity_id, sum(u.cost)
  FROM (SELECT DISTINCT ON (o.ad_entity_id, o.metric_date) o.ad_entity_id, o.cost
          FROM ads_placement_observation o
          JOIN ad_entity_state s ON s.ad_entity_id = o.ad_entity_id AND s.status = 'ENABLED'
         WHERE o.platform = %s AND o.placement = 'fuera_de_amazon'
           AND o.metric_date BETWEEN %s AND %s
         ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC) u
 GROUP BY u.ad_entity_id
"""

_SQL_CONFIGS_HIST = """
SELECT o.ad_entity_id, (o.observed_at AT TIME ZONE 'UTC')::date, o.presupuesto_diario
  FROM ads_campana_config_observation o
  JOIN ad_entity c ON c.id = o.ad_entity_id
  JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED'
 WHERE c.platform = %s AND c.kind = 'campaign'
   AND (o.observed_at AT TIME ZONE 'UTC')::date <= %s
 ORDER BY o.ad_entity_id, 2, o.observed_at
"""
_SQL_REGRESABLES = """
SELECT a.id, a.campana_id, a.clase, (a.confirmado_el AT TIME ZONE 'UTC')::date
  FROM campana_ajuste a
 WHERE a.platform = %s
   AND a.confirmado_el IS NOT NULL
   AND a.clase <> 'fuera_de_amazon'
   AND NOT EXISTS (SELECT 1 FROM campana_ajuste r WHERE r.regresa_a = a.id)
 ORDER BY a.id
"""
_SQL_ULTIMO_AJUSTE_PRECIO = """
SELECT a.campana_id, max((a.confirmado_el AT TIME ZONE 'UTC')::date)
  FROM campana_ajuste a
 WHERE a.platform = %s
   AND a.confirmado_el IS NOT NULL
   AND a.clase = 'ajuste_ubicacion'
 GROUP BY a.campana_id
"""
_TEXTO_CLASE_AJUSTE = {
    "presupuesto": "presupuesto",
    "ajuste_ubicacion": "ajuste de ubicación",
}


def _aviso_ajuste_de(confirmado: dt.date | None, hasta: dt.date) -> str | None:
    if confirmado is None:
        return None
    from app.avisos_campana import avisos_ajuste_propio

    return avisos_ajuste_propio("ajuste_ubicacion", confirmado, hasta)


@dataclass(frozen=True)
class FilaTipo:
    """Un renglon: `tipo` es un valor de `TipoCampana`, `""` = total. `hojas`
    cuenta hojas con metricas en la ventana (las del JOIN, no todas las
    activas): son las que suman al total."""

    tipo: str
    gasto: Decimal | None
    pedidos: int | None
    venta: Decimal | None
    acos_pct: Decimal | None
    parte_del_gasto_pct: Decimal | None
    hojas: int

    @property
    def sin_ventas(self) -> bool:
        """Venta conocida igual a 0 (Decimal exacto, regla 4: la plantilla
        jamas compara dinero con float)."""
        return self.venta is not None and self.venta == 0

    def como_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "gasto": None if self.gasto is None else str(self.gasto),
            "pedidos": self.pedidos,
            "venta": None if self.venta is None else str(self.venta),
            "acos_pct": None if self.acos_pct is None else str(self.acos_pct),
            "parte_del_gasto_pct": (
                None if self.parte_del_gasto_pct is None else str(self.parte_del_gasto_pct)
            ),
            "hojas": self.hojas,
            "sin_ventas": self.sin_ventas,
        }


@dataclass(frozen=True)
class FilaUbicacion:
    """Una ubicacion del anuncio en 30 dias. `gasta_sin_vender` = gasto >=
    gasto para concluir y pedidos == 0 (con gasto None, no)."""

    ubicacion: Ubicacion
    gasto: Decimal | None
    clics: int | None
    pedidos: int | None
    venta: Decimal | None
    cpc: Decimal | None
    conversion_pct: Decimal | None
    acos_pct: Decimal | None
    parte_del_gasto_pct: Decimal | None
    gasta_sin_vender: bool

    def como_dict(self) -> dict:
        return {
            "ubicacion": self.ubicacion,
            "gasto": None if self.gasto is None else str(self.gasto),
            "clics": self.clics,
            "pedidos": self.pedidos,
            "venta": None if self.venta is None else str(self.venta),
            "cpc": None if self.cpc is None else str(self.cpc),
            "conversion_pct": None if self.conversion_pct is None else str(self.conversion_pct),
            "acos_pct": None if self.acos_pct is None else str(self.acos_pct),
            "parte_del_gasto_pct": (
                None if self.parte_del_gasto_pct is None else str(self.parte_del_gasto_pct)
            ),
            "gasta_sin_vender": self.gasta_sin_vender,
        }


@dataclass(frozen=True)
class AjusteRegresable:
    """Ajuste CONFIRMADO sin regreso y con regreso sellado (V.3): la
    pantalla le pinta su boton Regresar."""

    ajuste_id: int
    clase: str
    texto: str
    confirmado_el: dt.date


@dataclass(frozen=True)
class FilaCampana:
    """Configuracion vigente de una campana activa y como usa su
    presupuesto. `avisos` lo llena V.4; aqui siempre vacio."""

    campana_id: int
    nombre: str | None
    presupuesto_diario: Decimal | None
    gasto_medio_diario: Decimal | None
    uso_presupuesto_pct: Decimal | None
    estrategia: EstrategiaPuja | None
    ajustes_ubicacion: tuple[tuple[str, int], ...]
    gasto_fuera_de_amazon: Decimal | None
    dias_al_tope_7d: int | None
    avisos: tuple[str, ...] = ()
    regresables: tuple[AjusteRegresable, ...] = ()
    aviso_ajuste: str | None = None

    def como_dict(self) -> dict:
        return {
            "campana_id": self.campana_id,
            "nombre": self.nombre,
            "presupuesto_diario": None
            if self.presupuesto_diario is None
            else str(self.presupuesto_diario),
            "gasto_medio_diario": None
            if self.gasto_medio_diario is None
            else str(self.gasto_medio_diario),
            "uso_presupuesto_pct": None
            if self.uso_presupuesto_pct is None
            else str(self.uso_presupuesto_pct),
            "estrategia": self.estrategia,
            "ajustes_ubicacion": [list(par) for par in self.ajustes_ubicacion],
            "gasto_fuera_de_amazon": None
            if self.gasto_fuera_de_amazon is None
            else str(self.gasto_fuera_de_amazon),
            "dias_al_tope_7d": self.dias_al_tope_7d,
            "avisos": list(self.avisos),
            "regresables": [
                {
                    "ajuste_id": r.ajuste_id,
                    "clase": r.clase,
                    "texto": r.texto,
                    "confirmado_el": r.confirmado_el.isoformat(),
                }
                for r in self.regresables
            ],
            "aviso_ajuste": self.aviso_ajuste,
        }


@dataclass(frozen=True)
class PantallaDinero:
    """Una tabla por mercado, solo lo que hoy esta encendido."""

    plataforma: Plataforma
    moneda: Moneda
    desde: dt.date
    hasta: dt.date
    filas: tuple[FilaTipo, ...]
    total: FilaTipo
    hojas_sin_clasificar: int
    target_acos_pct: Decimal | None
    por_ubicacion: tuple[FilaUbicacion, ...] = ()
    por_campana: tuple[FilaCampana, ...] = ()

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "moneda": self.moneda,
            "desde": self.desde.isoformat(),
            "hasta": self.hasta.isoformat(),
            "filas": [fila.como_dict() for fila in self.filas],
            "total": self.total.como_dict(),
            "hojas_sin_clasificar": self.hojas_sin_clasificar,
            "target_acos_pct": (
                None
                if self.target_acos_pct is None
                else str(self.target_acos_pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN))
            ),
            "por_ubicacion": [fila.como_dict() for fila in self.por_ubicacion],
            "por_campana": [fila.como_dict() for fila in self.por_campana],
        }


def _pct(numerador: Decimal | int | None, denominador: Decimal | int | None) -> Decimal | None:
    """Porcentaje 0-100 a 1 decimal (HALF_EVEN, precedente `_dos_dec`).
    Denominador None o 0 da None, nunca division."""
    if numerador is None or denominador is None or denominador == 0:
        return None
    num = Decimal(numerador) if isinstance(numerador, int) else numerador
    den = Decimal(denominador) if isinstance(denominador, int) else denominador
    return (num / den * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)


def _dinero2(valor: Decimal | None) -> Decimal | None:
    """Dinero a 2 decimales (HALF_EVEN, precedente `_dos_dec`)."""
    if valor is None:
        return None
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def umbral_concluir(conn, plataforma: Plataforma) -> Decimal:
    """Gasto para concluir del mercado (settings vigentes, default del
    dueno sin config). Un SELECT de una fila; unica fuente del umbral
    (la usan el lector de ubicaciones, el reparto de avisos y el
    comando via `notifica`)."""
    fila_settings = conn.execute(_SQL_SETTINGS).fetchone()
    return gasto_para_concluir_desde_settings(fila_settings[0] if fila_settings else {}, plataforma)


def lee_ubicaciones(
    conn, *, plataforma: Plataforma, desde: dt.date, hasta: dt.date
) -> tuple[FilaUbicacion, ...]:
    """30 dias de placements por ubicacion, solo campanas ENABLED del
    mercado, con colapso DISTINCT ON (espejo de la consulta de control
    P.2a, mas la venta para el ACoS). Sin veneno: la control suma plano
    y el lector da lo mismo que ella. Siempre los 4 renglones."""
    grupos = {
        fila[0]: fila[1:]
        for fila in conn.execute(_SQL_UBICACIONES, (plataforma, desde, hasta)).fetchall()
    }
    umbral = umbral_concluir(conn, plataforma)
    total = sum(vals[0] for vals in grupos.values() if vals[0] is not None)

    def _fila(ubicacion: str) -> FilaUbicacion:
        vals = grupos.get(ubicacion)
        gasto, clics, pedidos, venta = vals if vals is not None else (None, None, None, None)
        cpc = _dinero2(gasto / clics) if gasto is not None and clics else None
        return FilaUbicacion(
            ubicacion=ubicacion,
            gasto=gasto,
            clics=clics,
            pedidos=pedidos,
            venta=venta,
            cpc=cpc,
            conversion_pct=_pct(pedidos, clics),
            acos_pct=_pct(gasto, venta),
            parte_del_gasto_pct=_pct(gasto, total if grupos else None),
            gasta_sin_vender=gasto is not None and pedidos == 0 and gasto >= umbral,
        )

    return tuple(_fila(ubicacion) for ubicacion in UBICACIONES)


def lee_campanas(
    conn, *, plataforma: Plataforma, desde: dt.date, hasta: dt.date
) -> tuple[FilaCampana, ...]:
    """Campanas ENABLED del mercado con config vigente y gasto. El gasto
    medio es sobre dias con dato desde el primer dia con dato (dias sin
    dato no cuentan; sin ninguna metrica, None). Un dia cuenta al tope
    si su gasto conocido es
    90 % o mas del presupuesto vigente ese dia; dia sin gasto o sin
    presupuesto no cuenta (la regla de "anterior a la primera
    observacion" se cumple por construccion: sin gasto no hay tope)."""
    campanas = conn.execute(_SQL_CAMPANAS, (plataforma,)).fetchall()
    gasto = {
        (fila[0], fila[1]): fila[2]
        for fila in conn.execute(_SQL_GASTO_DIARIO, (plataforma, desde, hasta)).fetchall()
    }
    fuera = {
        fila[0]: fila[1]
        for fila in conn.execute(_SQL_FUERA_CAMPANA, (plataforma, desde, hasta)).fetchall()
    }
    configs: dict[int, list] = {}
    for cid, vigente_desde, presupuesto in conn.execute(
        _SQL_CONFIGS_HIST, (plataforma, hasta)
    ).fetchall():
        configs.setdefault(cid, []).append((vigente_desde, presupuesto))
    regresables: dict[int, list] = {}
    for aid, cid, clase, confirmado in conn.execute(_SQL_REGRESABLES, (plataforma,)).fetchall():
        regresables.setdefault(cid, []).append(
            AjusteRegresable(
                ajuste_id=aid,
                clase=clase,
                texto=_TEXTO_CLASE_AJUSTE[clase],
                confirmado_el=confirmado,
            )
        )
    ultimo_precio = {
        cid: confirmado
        for cid, confirmado in conn.execute(_SQL_ULTIMO_AJUSTE_PRECIO, (plataforma,)).fetchall()
    }
    dias = (hasta - desde).days + 1

    def _presupuesto_dia(cid: int, dia: dt.date) -> Decimal | None:
        vigente = None
        for vigente_desde, presupuesto in configs.get(cid, []):
            if vigente_desde <= dia:
                vigente = presupuesto
            else:
                break
        return vigente

    filas = []
    for cid, nombre, cfg_id, presupuesto, estrategia_txt, top, resto, prod in campanas:
        gastos = [gasto.get((cid, desde + dt.timedelta(days=i))) for i in range(dias)]
        conocidos = [g for g in gastos if g is not None]
        medio = _dinero2(sum(conocidos) / len(conocidos)) if conocidos else None
        if cfg_id is None:
            al_tope = None
        else:
            al_tope = 0
            for i in range(DIAS_TOPE):
                dia = hasta - dt.timedelta(days=DIAS_TOPE - 1 - i)
                gasto_dia = gasto.get((cid, dia))
                presup_dia = _presupuesto_dia(cid, dia)
                if (
                    gasto_dia is not None
                    and presup_dia is not None
                    and gasto_dia >= presup_dia * Decimal("0.9")
                ):
                    al_tope += 1
        ajustes = tuple(
            (ubicacion, (top, resto, prod)[indice])
            for ubicacion, indice in _AJUSTE_A_UBICACION
            if (top, resto, prod)[indice] is not None
        )
        filas.append(
            FilaCampana(
                campana_id=cid,
                nombre=nombre,
                presupuesto_diario=presupuesto,
                gasto_medio_diario=medio,
                uso_presupuesto_pct=_pct(medio, presupuesto),
                estrategia=None
                if cfg_id is None or estrategia_txt is None
                else _ESTRATEGIA.get(estrategia_txt, "otra"),
                ajustes_ubicacion=ajustes,
                gasto_fuera_de_amazon=fuera.get(cid),
                dias_al_tope_7d=al_tope,
                regresables=tuple(regresables.get(cid, ())),
                aviso_ajuste=_aviso_ajuste_de(ultimo_precio.get(cid), hasta),
            )
        )
    return tuple(filas)


def lee_dinero(
    conn, *, plataforma: Plataforma, dias: int = 90, hasta: dt.date | None = None
) -> PantallaDinero:
    """Tabla por tipo de campana del mercado. SOLO SELECT. `hasta` omiso =
    ayer UTC; la ventana de tipos va de `hasta - dias` a `hasta` (BETWEEN
    inclusivo) y las de ubicacion/campana cubren los ultimos 30 dias hasta
    `hasta`. El target es el del ultimo ciclo done (`_target_margen_del_ciclo`,
    import diferido: este modulo puro no carga el dashboard al importar)."""
    from app.api_dashboard import _target_margen_del_ciclo

    fin = hasta if hasta is not None else dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    inicio = fin - dt.timedelta(days=dias)
    grupos = {
        fila[0]: fila[1:]
        for fila in conn.execute(_SQL_GRUPOS, (plataforma, inicio, fin)).fetchall()
    }
    conocidos = {tipo: grupos.get(tipo) for tipo in TIPOS}
    resto = [(tipo, vals) for tipo, vals in grupos.items() if tipo not in TIPOS]
    presentes = [vals for vals in conocidos.values() if vals is not None] + [
        vals for _, vals in resto
    ]

    def _suma(indice: int):
        """Suma de la metrica sobre todos los grupos, con veneno: un grupo
        con NULL anula el total; sin grupos, NULL (no 0)."""
        if not presentes or any(vals[indice] is None for vals in presentes):
            return None
        return sum(vals[indice] for vals in presentes)

    gasto_total = _suma(0)
    pedidos_total = _suma(1)
    venta_total = _suma(2)

    def _fila(tipo: str, vals: tuple | None) -> FilaTipo:
        gasto, pedidos, venta, hojas = vals if vals is not None else (None, None, None, 0)
        return FilaTipo(
            tipo=tipo,
            gasto=gasto,
            pedidos=pedidos,
            venta=venta,
            acos_pct=_pct(gasto, venta),
            parte_del_gasto_pct=_pct(gasto, gasto_total),
            hojas=hojas,
        )

    filas = tuple(_fila(tipo, conocidos[tipo]) for tipo in TIPOS)
    hojas_total = sum(vals[3] for vals in conocidos.values() if vals is not None) + sum(
        vals[3] for _, vals in resto
    )
    total = FilaTipo(
        tipo="",
        gasto=gasto_total,
        pedidos=pedidos_total,
        venta=venta_total,
        acos_pct=_pct(gasto_total, venta_total),
        parte_del_gasto_pct=Decimal("100.0") if gasto_total else None,
        hojas=hojas_total,
    )
    desde_30 = fin - dt.timedelta(days=DIAS_UBICACION - 1)
    por_ubicacion = lee_ubicaciones(conn, plataforma=plataforma, desde=desde_30, hasta=fin)
    por_campana = lee_campanas(conn, plataforma=plataforma, desde=desde_30, hasta=fin)
    por_campana = _con_avisos(conn, plataforma, por_ubicacion, por_campana)
    return PantallaDinero(
        plataforma=plataforma,
        moneda=PLATAFORMAS_MONEDA[plataforma],
        desde=inicio,
        hasta=fin,
        filas=filas,
        total=total,
        hojas_sin_clasificar=sum(vals[3] for _, vals in resto),
        target_acos_pct=_target_margen_del_ciclo(conn, plataforma),
        por_ubicacion=por_ubicacion,
        por_campana=por_campana,
    )


def _con_avisos(conn, plataforma: Plataforma, por_ubicacion, por_campana):
    """Reparte las frases de campana de `avisos_del_dia` en cada
    `FilaCampana.avisos` (V.4; import diferido, este modulo no carga el
    de avisos al importar)."""
    from app.avisos_campana import avisos_del_dia

    avisos = avisos_del_dia(
        por_ubicacion,
        por_campana,
        plataforma=plataforma,
        gasto_para_concluir=umbral_concluir(conn, plataforma),
    )
    return tuple(
        replace(
            fila,
            avisos=tuple(aviso.frase for aviso in avisos if aviso.campana_id == fila.campana_id),
        )
        for fila in por_campana
    )
