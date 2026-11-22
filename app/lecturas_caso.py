"""Lectura por plataforma para armar CasoHoja (BIDS 02 M.2). SOLO SELECT.

`lee_plataforma` lee UNA vez por plataforma y ciclo: tramos por hoja, hojas
activas, cambios de bid de 90 dias, y precio mas pedidos inmaduros (cuatro
consultas). `LecturasPlataforma.caso` arma el `CasoHoja` de una hoja con
cero consultas, o una (`_SQL_EFECTO`) si la hoja cambio de bid en 90 dias.

Frontera de lectura (lo decide este modulo, no la politica): dia sin fila
de la hoja pero con ingesta de la plataforma = cero medido; dia sin
ingesta = tramo None; metrica con alguna observacion NULL o negativa =
None solo para esa metrica. El roll-up a grupo y cuenta suma solo hojas
de `v_hoja_activa`, con veneno por metrica identico al `bool_and`.

Calendario de ingesta (guia M.2 cambio 3): dia con ingesta = fecha con al
menos una fila de alguna hoja de la plataforma en la misma lectura.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.temporada_io import TemporadaPlataforma

from app.lecturas_efecto import (  # noqa: F401 - re-export estable de efecto y helpers SQL
    _SQL_EFECTO,
    _SQL_EFECTO_ECONOMIA,
    DIAS_EFECTO,
    DIAS_INMADUROS_HASTA,
    DIAS_PRECIO_PREVIO,
    Conexion,
    _col_metrica_ventana,
    _cols_ventana,
    _completo,
    _contribucion,
    _EfectoLeido,
    _episodio_economico,
    _fuente,
    _fuente_nombrada,
    _lee_efecto,
    _mide,
    _ventana_economica,
    _ventanas_medidas,
)
from app.lecturas_historia import (  # noqa: F401 - re-export estable de historia y presupuesto
    _PRESUPUESTO_AS_OF,
    _PRESUPUESTO_VIGENTE,
    _SQL_CAMBIOS,
    _SQL_EPISODIO_ABIERTO_VIEJO,
    _SQL_FINES,
    _SQL_GASTO_CAMPANA,
    _SQL_MANOS_EXTERNAS,
    _SQL_PRESUPUESTO_CAMPANA,
    _cambios_por_hoja,
    _historia_por_hoja,
    _historia_temporada,
    _presupuesto_campanas,
)
from app.optimizer import temporada
from app.optimizer.caso import (
    DIAS_LOOKBACK,
    DIAS_MADUREZ,
    DIAS_TRAMO_RECIENTE,
    REVISION_POLITICA,
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EvidenciaNivel,
    HistoriaBid,
    InsumosPausa,
    InsumosRecuperacion,
    Plataforma,
    PrecioVentana,
    PresupuestoCampana,
    TemporadaCaso,
    Tramo,
    Trayectoria,
)
from app.optimizer.recuperacion import _cadena_desde
from app.optimizer.windows import DIAS_FRESCURA_BIDS, AgregadoMetricas

DIAS_PRECIO_DESDE = 33  # PrecioVentana cubre D-33..D-3
DIAS_INMADUROS_DESDE = 9  # pedidos inmaduros cubren D-9..D-1


_COLS_TRAMO: tuple[tuple[str, bool], ...] = (
    ("clicks", True),
    ("orders", True),
    ("ad_revenue", False),
    ("cost", False),
    ("impressions", True),
)

_SQL_TRAMOS = (
    "SELECT v.ad_entity_id,\n       "
    + _cols_ventana("rec", _COLS_TRAMO)
    + ",\n       "
    + _cols_ventana("ant", _COLS_TRAMO)
    + ",\n       max(v.observed_at),\n       array_agg(DISTINCT v.metric_date)\n"
    "  FROM {fuente}\n"
    "  JOIN ad_entity k ON k.id = v.ad_entity_id\n"
    " WHERE k.platform = %(plataforma)s::platform\n"
    "   AND k.kind IN ('keyword', 'product_target')\n"
    "   AND v.metric_currency = %(moneda)s\n"
    "   AND v.metric_date BETWEEN %(desde)s AND %(hasta)s\n"
    " GROUP BY v.ad_entity_id\n"
)

# TEMPORADA 01 D (decision 8 del plan): la MISMA consulta de tramos con la
# cerca de la hoja — cada rango empieza en greatest(rango, cerca), via unnest.
_SQL_TRAMOS_CERCA = (
    "SELECT v.ad_entity_id,\n       "
    + _cols_ventana("rec", _COLS_TRAMO, con_cerca=True)
    + ",\n       "
    + _cols_ventana("ant", _COLS_TRAMO, con_cerca=True)
    + ",\n       max(v.observed_at),\n       array_agg(DISTINCT v.metric_date)\n"
    "  FROM {fuente}\n"
    "  JOIN unnest(%(hojas)s::bigint[], %(cercas)s::date[]) AS h(hoja_id, cerca)\n"
    "    ON v.ad_entity_id = h.hoja_id\n"
    "  JOIN ad_entity k ON k.id = v.ad_entity_id\n"
    " WHERE k.platform = %(plataforma)s::platform\n"
    "   AND k.kind IN ('keyword', 'product_target')\n"
    "   AND v.metric_currency = %(moneda)s\n"
    "   AND v.metric_date BETWEEN greatest(%(desde)s, h.cerca) AND %(hasta)s\n"
    " GROUP BY v.ad_entity_id\n"
)

_SQL_PRECIO = (
    "SELECT v.ad_entity_id,\n       "
    + _cols_ventana("pre", (("cost", False), ("clicks", True)))
    + ",\n       "
    + _cols_ventana("inm", (("orders", True),))
    + ",\n       max(v.observed_at)\n"
    "  FROM {fuente}\n"
    "  JOIN ad_entity k ON k.id = v.ad_entity_id\n"
    " WHERE k.platform = %(plataforma)s::platform\n"
    "   AND k.kind IN ('keyword', 'product_target')\n"
    "   AND v.metric_currency = %(moneda)s\n"
    "   AND v.metric_date BETWEEN %(desde)s AND %(hasta)s\n"
    " GROUP BY v.ad_entity_id\n"
)

_SQL_HOJAS = """
SELECT hoja_id, ad_group_id, campana_id FROM v_hoja_activa WHERE platform = %s
"""

_SQL_EVENTOS_CAMPANA = """
SELECT o.ad_entity_id, o.observed_at
  FROM ads_campana_config_observation o
  JOIN ad_entity c ON c.id = o.ad_entity_id AND c.kind = 'campaign'
 WHERE c.platform = %s::platform
   AND o.observed_at >= %s AND o.observed_at <= %s
"""

_SQL_AJUSTES_CAMPANA = """
SELECT a.campana_id, a.confirmado_el
  FROM campana_ajuste a
 WHERE a.platform = %s::platform
   AND a.confirmado_el IS NOT NULL
   AND a.confirmado_el >= %s AND a.confirmado_el <= %s
"""

_SQL_PRECIOS_GRUPO = """
SELECT pa.parent_id, pc.enviado_at
  FROM precio_cambio pc
  JOIN ad_entity pa ON pa.platform = pc.platform
                   AND pa.kind = 'product_ad'
                   AND pa.listing_id = pc.listing_id
 WHERE pc.platform = %s::platform
   AND pc.aplicado IS TRUE
   AND pc.estado IN ('enviado', 'confirmado')
   AND pc.enviado_at IS NOT NULL
   AND pc.enviado_at >= %s AND pc.enviado_at <= %s
"""

_SQL_ANUNCIOS_NUEVOS = """
SELECT a.parent_id, a.first_seen_at
  FROM ad_entity a
 WHERE a.platform = %s::platform
   AND a.kind = 'product_ad'
   AND a.first_seen_at >= %s AND a.first_seen_at <= %s
"""

# Usa started_at (intento) y no finished_at, que en reconciliacion puede caer en otro ciclo.
_SQL_CORTES_GRUPO = """
SELECT d.ad_entity_id, a.started_at
  FROM apply_attempt a
  JOIN decision d  ON d.id = a.decision_id AND d.kind IN ('negative','harvest')
  JOIN ad_entity g ON g.id = d.ad_entity_id AND g.kind = 'ad_group'
 WHERE g.platform = %s::platform
   AND a.resultado IN ('ok','ok:reconciliado')
   AND a.tipo IN ('normal','reversa')
   AND a.started_at >= %s AND a.started_at <= %s
"""


def _exige_tz(momento: dt.datetime, nombre: str) -> None:
    if momento.tzinfo is None:
        raise ValueError(f"{nombre} debe ser tz-aware: un naive evaluaria segun la TZ local")


def _tramo(
    filas: int,
    valores: tuple,
    desde: dt.date,
    hasta: dt.date,
    dias: frozenset[dt.date],
) -> Tramo:
    """Tramo desde una fila cruda: hueco de ingesta = None; sin filas con
    ingesta completa = ceros medidos; con filas = sumas (veneno intacto)."""
    if not _completo(desde, hasta, dias):
        return Tramo(None, None, None, None, None)
    if filas == 0:
        return Tramo(0, 0, Decimal(0), Decimal(0), 0)
    return Tramo(*valores)


def _junta(valores: list) -> Any:
    """Suma con veneno por metrica (semantica del bool_and global)."""
    if any(v is None for v in valores):
        return None
    return sum(valores)


def _enrolla(niveles: list[EvidenciaNivel]) -> EvidenciaNivel | None:
    if not niveles:
        return None
    return EvidenciaNivel(
        reciente=Tramo(
            clics=_junta([e.reciente.clics for e in niveles]),
            pedidos=_junta([e.reciente.pedidos for e in niveles]),
            venta=_junta([e.reciente.venta for e in niveles]),
            gasto=_junta([e.reciente.gasto for e in niveles]),
            impresiones=_junta([e.reciente.impresiones for e in niveles]),
        ),
        antiguo=Tramo(
            clics=_junta([e.antiguo.clics for e in niveles]),
            pedidos=_junta([e.antiguo.pedidos for e in niveles]),
            venta=_junta([e.antiguo.venta for e in niveles]),
            gasto=_junta([e.antiguo.gasto for e in niveles]),
            impresiones=_junta([e.antiguo.impresiones for e in niveles]),
        ),
    )


@dataclass(frozen=True)
class LecturasPlataforma:
    """Todo lo que se lee UNA vez por plataforma y ciclo: tramos por hoja,
    roll-up a ad group y cuenta solo con hojas activas, cambios de bid de
    90 dias, precio, inmaduros y calendario de dias con ingesta. Los dicts
    son privados: el unico acceso es `caso`."""

    plataforma: Plataforma
    decidido_el: dt.datetime
    economia: EconomiaPlataforma
    _por_hoja: dict[int, EvidenciaNivel]
    _por_grupo: dict[int, EvidenciaNivel]
    _cuenta: EvidenciaNivel | None
    _cambios: dict[int, tuple[CambioBid, ...]]
    _precio: dict[int, PrecioVentana]
    _inmaduros: dict[int, int | None]
    _campanas: dict[int, int]
    _eventos_campana: dict[int, tuple[dt.date, ...]]
    _eventos_grupo: dict[int, tuple[dt.date, ...]]
    _version_economia: str | None
    _dias_con_ingesta: frozenset[dt.date]
    _d: dt.date
    _visto_el: dt.datetime | None
    _observado_al: dt.datetime | None
    _temporada: TemporadaPlataforma | None = None
    _historia: dict[int, HistoriaBid] | None = None
    _historia_desde: dt.date | None = None
    _recuperacion: InsumosRecuperacion | None = None
    # El caso que decide solo lleva los marcadores de recuperacion y de
    # presupuesto cuando su motor esta en live; el caso de sombra los lleva
    # siempre (diseno, decision 12: la practica decide un segundo caso con
    # marcadores). Por defecto live, que es como los llamadores historicos
    # construian el unico caso.
    _recuperacion_live: bool = True
    _presupuesto: dict[int, PresupuestoCampana] | None = None
    _presupuesto_live: bool = True

    def frontera(
        self,
        hoja_id: int,
        bid_vigente: Decimal | None,
        *,
        piso: Decimal,
        techo: Decimal,
    ) -> temporada.FronteraHoja | None:
        """temporada.frontera sobre lo leido, sin consulta. None sin temporada."""
        t = self._temporada
        if t is None:
            return None
        cadena = _cadena_desde(self._historia.get(hoja_id), bid_vigente)
        return temporada.frontera(
            t.estacion,
            cadena,
            episodio=t.estado.episodio,
            excluida_en=t.estado.excluidas.get(hoja_id, frozenset()),
            cerca_plataforma=t.estado.cerca_plataforma,
            bid=bid_vigente,
            piso=piso,
            techo=techo,
        )

    def casos(
        self,
        conn: Conexion,
        *,
        hoja_id: int,
        ad_group_id: int | None,
        bid: BidVigente,
        target_acos_pct: Decimal,
        pausa: InsumosPausa,
        pausa_sombra: InsumosPausa | None = None,
        equilibrio_acos_pct: Decimal | None = None,
        procedencia: str | None = None,
        con_temporada: bool = True,
    ) -> tuple[CasoHoja, CasoHoja | None]:
        """(caso que decide, caso de sombra). Live: el que decide trae los
        marcadores y la sombra es None. Shadow: el que decide va SIN marcadores
        (byte a byte lo de hoy) y la sombra los trae, con su propia pausa
        resuelta (x mult y cerca). Reemplaza a `caso` en los llamadores del
        ciclo (TEMPORADA 01 D)."""
        t = self._temporada
        en_shadow = t is not None and t.modo == "shadow"
        caso = self.caso(
            conn,
            hoja_id=hoja_id,
            ad_group_id=ad_group_id,
            bid=bid,
            target_acos_pct=target_acos_pct,
            pausa=pausa,
            equilibrio_acos_pct=equilibrio_acos_pct,
            procedencia=procedencia,
            con_temporada=con_temporada,
        )
        sombra = None
        if en_shadow and con_temporada and pausa_sombra is not None:
            sombra = self.caso(
                conn,
                hoja_id=hoja_id,
                ad_group_id=ad_group_id,
                bid=bid,
                target_acos_pct=target_acos_pct,
                pausa=pausa_sombra,
                equilibrio_acos_pct=equilibrio_acos_pct,
                procedencia=procedencia,
                con_temporada=True,
                sombra=True,
            )
        return caso, sombra

    def tiene_pendiente(
        self, hoja_id: int, bid_vigente: Decimal | None, *, piso: Decimal, techo: Decimal
    ) -> bool:
        """¿Entrada, ajuste o regreso pendiente? Lo usa el ciclo para que una
        keyword INERTE con movimiento de temporada entre al ciclo (bloque D)."""
        return isinstance(
            self.pendiente(hoja_id, bid_vigente, piso=piso, techo=techo),
            (
                temporada.EntradaPendiente,
                temporada.AjustePendiente,
                temporada.RestauracionPendiente,
            ),
        )

    def pendiente(
        self, hoja_id: int, bid_vigente: Decimal | None, *, piso: Decimal, techo: Decimal
    ) -> temporada.Pendiente | None:
        """El pendiente de temporada de UNA hoja (None sin temporada). Lo usa el
        ciclo para anotar el regreso retenido por campana topada (bloque D)."""
        t = self._temporada
        if t is None:
            return None
        cadena = _cadena_desde(self._historia.get(hoja_id), bid_vigente)
        return temporada._pendiente_desde(
            t.estacion,
            cadena,
            episodio=t.estado.episodio,
            excluida_en=t.estado.excluidas.get(hoja_id, frozenset()),
            bid=bid_vigente,
            piso=piso,
            techo=techo,
        )

    def caso(
        self,
        conn: Conexion,
        *,
        hoja_id: int,
        ad_group_id: int | None,
        bid: BidVigente,
        target_acos_pct: Decimal,
        pausa: InsumosPausa,
        equilibrio_acos_pct: Decimal | None = None,
        procedencia: str | None = None,
        con_temporada: bool = True,
        sombra: bool = False,
    ) -> CasoHoja:
        """Arma el caso de una hoja. Cero consultas si la hoja no cambio de
        bid en 90 dias; UNA consulta (`_SQL_EFECTO`) si cambio."""
        cambios = self._cambios.get(hoja_id, ())
        plataforma = (
            replace(self.economia, equilibrio_acos_pct=equilibrio_acos_pct)
            if equilibrio_acos_pct is not None
            else self.economia
        )
        if cambios:
            leido = _lee_efecto(conn, self, hoja_id, cambios, pausa.cortes)
            if self._version_economia is not None:
                assert leido.pre_economico is not None and leido.post_economico is not None
            episodio = (
                _episodio_economico(
                    self,
                    cambios=cambios,
                    pre=leido.pre_economico,
                    post=leido.post_economico,
                    margen_pct=plataforma.equilibrio_acos_pct,
                    campana_id=self._campanas.get(hoja_id),
                    ad_group_id=ad_group_id,
                )
                if self._version_economia is not None
                else None
            )
            trayectoria = Trayectoria(cambios=cambios, efecto=leido.efecto, episodio=episodio)
        else:
            trayectoria = Trayectoria(cambios=(), efecto=None)
        temporada_caso = None
        historia_caso = None
        recuperacion_caso = None
        t = self._temporada
        recuperacion_decide = (
            self._recuperacion is not None and con_temporada and (sombra or self._recuperacion_live)
        )
        con_cadena = (
            t is not None and (t.modo == "live" or sombra) and con_temporada
        ) or recuperacion_decide
        if con_cadena:
            historia_caso = self._historia.get(hoja_id) or HistoriaBid(
                desde=self._historia_desde or self._d,
                cambios=(),
                fines=frozenset(),
                manos_externas=(),
            )
            if t is not None and (t.modo == "live" or sombra) and con_temporada:
                temporada_caso = TemporadaCaso(
                    estacion=t.estacion,
                    episodio=t.estado.episodio,
                    excluida_en=t.estado.excluidas.get(hoja_id, frozenset()),
                    meta_alta_pct=t.meta_alta_pct,
                    meta_manual=procedencia in ("goal_campana", "goal_plataforma"),
                    frontera=self.frontera(hoja_id, bid.valor, piso=bid.piso, techo=bid.techo),
                )
            if recuperacion_decide:
                recuperacion_caso = self._recuperacion
        presupuesto_caso = None
        if self._presupuesto is not None and (sombra or self._presupuesto_live):
            campana = self._campanas.get(hoja_id)
            presupuesto_caso = self._presupuesto.get(campana) if campana is not None else None
        return CasoHoja(
            plataforma=self.plataforma,
            hoja_id=hoja_id,
            ad_group_id=ad_group_id,
            bid=bid,
            economia=Economia(plataforma=plataforma, target_acos_pct=target_acos_pct),
            propia=self._por_hoja.get(hoja_id),
            pedidos_inmaduros=self._inmaduros.get(hoja_id),
            grupo=self._por_grupo.get(ad_group_id),
            cuenta=self._cuenta,
            precio=self._precio.get(hoja_id, PrecioVentana(gasto=None, clics=None)),
            trayectoria=trayectoria,
            pausa=pausa,
            ventana_desde=self._d - dt.timedelta(days=DIAS_LOOKBACK),
            ventana_hasta=self._d - dt.timedelta(days=DIAS_MADUREZ),
            observado_al=self._observado_al,
            revision=REVISION_POLITICA,
            temporada=temporada_caso,
            historia=historia_caso,
            recuperacion=recuperacion_caso,
            presupuesto=presupuesto_caso,
        )

    def efecto(
        self, conn: Conexion, *, hoja_id: int, cortes: AgregadoMetricas | None
    ) -> tuple[CambioBid, EfectoCambio] | None:
        """Ultimo cambio y su efecto como en `caso`; None sin cambio en 90 dias."""
        cambios = self._cambios.get(hoja_id, ())
        if not cambios:
            return None
        return cambios[-1], _lee_efecto(conn, self, hoja_id, cambios, cortes).efecto


def _lee_tramos(
    conn: Conexion,
    plataforma: Plataforma,
    d: dt.date,
    desde: dt.date,
    hasta: dt.date,
    rec_desde: dt.date,
    ant_hasta: dt.date,
    cal_desde: dt.date,
    ayer: dt.date,
    fuente_nom: str,
    pv_nom: dict[str, Any],
    moneda: str,
    temporada_plat: TemporadaPlataforma | None,
    recuperacion: InsumosRecuperacion | None,
    visto_el: dt.datetime | None,
) -> tuple[
    list, list | None, dict[int, dt.date], dt.date, dt.date, dt.date, dict, dict, dict, dict | None
]:
    """Las DOS corridas de tramos y la historia extendida (TEMPORADA 01 D,
    decision 8 del plan): corrida CON cerca (unnest hoja/cerca) y corrida SIN
    cerca (cerca de plataforma para el resto y rolls de grupo/cuenta)."""
    desde_historia, manos_por_hoja, fines_por_hoja, cambios_90, historia = _historia_temporada(
        conn,
        plataforma,
        d,
        desde,
        visto_el,
        activa=temporada_plat is not None or recuperacion is not None,
        memoria_dias=recuperacion.memoria_dias if recuperacion is not None else 365,
    )
    cerca_plat = (
        temporada_plat.estado.cerca_plataforma
        if temporada_plat is not None and temporada_plat.modo == "live"
        else None
    )
    cercas: dict[int, dt.date] = {}
    if temporada_plat is not None and temporada_plat.modo == "live" and historia:
        for hoja_h, hist_h in historia.items():
            cerca_h = temporada.cerca_hoja_de(_cadena_desde(hist_h, None), d)
            if cerca_h is not None:
                cercas[hoja_h] = cerca_h
    rec_desde_2 = max(rec_desde, cerca_plat) if cerca_plat is not None else rec_desde
    desde_2 = max(desde, cerca_plat) if cerca_plat is not None else desde
    filas_tramos = conn.execute(
        _SQL_TRAMOS.format(fuente=fuente_nom),
        {
            **pv_nom,
            "rec_ini": rec_desde_2,
            "rec_fin": hasta,
            "ant_ini": desde_2,
            "ant_fin": ant_hasta,
            "plataforma": plataforma,
            "moneda": moneda,
            "desde": cal_desde,
            "hasta": ayer,
        },
    ).fetchall()
    filas_tramos_cerca = None
    if cercas:
        hojas_cerca = list(cercas)
        cercas_orden = [cercas[h] for h in hojas_cerca]
        filas_tramos_cerca = conn.execute(
            _SQL_TRAMOS_CERCA.format(fuente=fuente_nom),
            {
                **pv_nom,
                "rec_ini": rec_desde,
                "rec_fin": hasta,
                "ant_ini": desde,
                "ant_fin": ant_hasta,
                "hojas": hojas_cerca,
                "cercas": cercas_orden,
                "plataforma": plataforma,
                "moneda": moneda,
                "desde": cal_desde,
                "hasta": ayer,
            },
        ).fetchall()
    return (
        filas_tramos,
        filas_tramos_cerca,
        cercas,
        rec_desde_2,
        desde_2,
        desde_historia,
        manos_por_hoja,
        fines_por_hoja,
        cambios_90,
        historia,
    )


def lee_plataforma(
    conn: Conexion,
    plataforma: Plataforma,
    decidido_el: dt.datetime,
    *,
    economia: EconomiaPlataforma,
    visto_el: dt.datetime | None = None,
    version_economia: str | None = None,
    temporada_plat: TemporadaPlataforma | None = None,
    recuperacion: InsumosRecuperacion | None = None,
    recuperacion_live: bool = True,
    campana_topada: temporada.CampanaTopada | None = None,
    presupuesto_live: bool = True,
) -> LecturasPlataforma:
    """Lectura unica por plataforma y ciclo (v_metric_latest o metrics_as_of)."""
    _exige_tz(decidido_el, "decidido_el")
    if visto_el is not None:
        _exige_tz(visto_el, "visto_el")
    d = decidido_el.astimezone(dt.UTC).date()
    hasta = d - dt.timedelta(days=DIAS_MADUREZ)
    desde = d - dt.timedelta(days=DIAS_LOOKBACK)
    rec_desde = hasta - dt.timedelta(days=DIAS_TRAMO_RECIENTE - 1)
    ant_hasta = rec_desde - dt.timedelta(days=1)
    cal_desde = d - dt.timedelta(days=2 * DIAS_LOOKBACK)
    ayer = d - dt.timedelta(days=DIAS_INMADUROS_HASTA)
    pre_desde = d - dt.timedelta(days=DIAS_PRECIO_DESDE)
    fresco = d - dt.timedelta(days=DIAS_FRESCURA_BIDS)
    inm_desde = d - dt.timedelta(days=DIAS_INMADUROS_DESDE)
    moneda = economia.moneda
    fuente_nom, pv_nom = _fuente_nombrada(visto_el)
    (
        filas_tramos,
        filas_tramos_cerca,
        cercas,
        rec_desde_2,
        desde_2,
        desde_historia,
        _manos_por_hoja,
        _fines_por_hoja,
        cambios_90,
        historia,
    ) = _lee_tramos(
        conn,
        plataforma,
        d,
        desde,
        hasta,
        rec_desde,
        ant_hasta,
        cal_desde,
        ayer,
        fuente_nom,
        pv_nom,
        moneda,
        temporada_plat,
        recuperacion,
        visto_el,
    )
    filas_hojas = conn.execute(_SQL_HOJAS, (plataforma,)).fetchall()
    filas_precio = conn.execute(
        _SQL_PRECIO.format(fuente=fuente_nom),
        {
            **pv_nom,
            "pre_ini": pre_desde,
            "pre_fin": fresco,
            "inm_ini": inm_desde,
            "inm_fin": ayer,
            "plataforma": plataforma,
            "moneda": moneda,
            "desde": pre_desde,
            "hasta": ayer,
        },
    ).fetchall()
    eventos_campana: dict[int, list[dt.date]] = {}
    eventos_grupo: dict[int, list[dt.date]] = {}
    if version_economia is not None:
        corte = visto_el if visto_el is not None else decidido_el
        bajo = dt.datetime.combine(cal_desde, dt.time.min, tzinfo=dt.UTC)
        for consulta, destino in (
            (_SQL_EVENTOS_CAMPANA, eventos_campana),
            (_SQL_AJUSTES_CAMPANA, eventos_campana),
        ):
            for entidad, sello in conn.execute(consulta, (plataforma, bajo, corte)).fetchall():
                destino.setdefault(entidad, []).append(sello.astimezone(dt.UTC).date())
        for consulta in (_SQL_PRECIOS_GRUPO, _SQL_ANUNCIOS_NUEVOS, _SQL_CORTES_GRUPO):
            for entidad, sello in conn.execute(consulta, (plataforma, bajo, corte)).fetchall():
                eventos_grupo.setdefault(entidad, []).append(sello.astimezone(dt.UTC).date())
    dias = frozenset(f for fila in filas_tramos for f in (fila[14] or ()))
    presupuesto = (
        _presupuesto_campanas(conn, plataforma, dias, campana_topada, visto_el)
        if campana_topada is not None
        else None
    )

    def _niveles_de(filas: list, rec: dt.date, des: dt.date) -> dict[int, EvidenciaNivel]:
        niveles: dict[int, EvidenciaNivel] = {}
        for fila in filas:
            (hoja, fr, c_r, p_r, v_r, g_r, i_r, fa, c_a, p_a, v_a, g_a, i_a, _obs, _f) = fila
            if fr + fa == 0:
                continue  # sin filas en la ventana madura: propia None
            niveles[hoja] = EvidenciaNivel(
                reciente=_tramo(fr, (c_r, p_r, v_r, g_r, i_r), rec, hasta, dias),
                antiguo=_tramo(fa, (c_a, p_a, v_a, g_a, i_a), des, ant_hasta, dias),
            )
        return niveles

    evid_sin_cerca = _niveles_de(filas_tramos, rec_desde_2, desde_2)
    por_hoja = evid_sin_cerca
    niveles_rolls = evid_sin_cerca
    if filas_tramos_cerca:
        evid_cerca = _niveles_de(filas_tramos_cerca, rec_desde, desde)
        por_hoja = {h: ev for h, ev in evid_sin_cerca.items() if h not in cercas}
        por_hoja.update(evid_cerca)
        niveles_rolls = evid_sin_cerca
    activas: dict[int, int] = {}
    miembros: dict[int, list[int]] = {}
    campanas: dict[int, int] = {}
    for hoja, grupo, campana in filas_hojas:
        activas[hoja] = grupo
        campanas[hoja] = campana
        miembros.setdefault(grupo, []).append(hoja)
    por_grupo = {}
    for grupo, hojas in miembros.items():
        nivel = _enrolla([niveles_rolls[h] for h in hojas if h in niveles_rolls])
        if nivel is not None:
            por_grupo[grupo] = nivel
    cuenta = _enrolla([ev for h, ev in niveles_rolls.items() if h in activas])
    precio_ok = _completo(pre_desde, fresco, dias)
    inm_ok = _completo(inm_desde, ayer, dias)
    precio = {}
    inmaduros = {}
    for hoja, f_pre, gasto, clics, f_inm, pedidos, _obs in filas_precio:
        if not precio_ok:
            precio[hoja] = PrecioVentana(gasto=None, clics=None)
        elif f_pre == 0:
            precio[hoja] = PrecioVentana(gasto=Decimal(0), clics=0)
        else:
            precio[hoja] = PrecioVentana(gasto=gasto, clics=clics)
        if not inm_ok:
            inmaduros[hoja] = None
        elif f_inm == 0:
            inmaduros[hoja] = 0
        else:
            inmaduros[hoja] = pedidos
    sellos = [fila[13] for fila in filas_tramos] + [fila[6] for fila in filas_precio]
    observado = max((s for s in sellos if s is not None), default=None)

    return LecturasPlataforma(
        plataforma=plataforma,
        decidido_el=decidido_el,
        economia=economia,
        _por_hoja=por_hoja,
        _por_grupo=por_grupo,
        _cuenta=cuenta,
        _cambios=cambios_90,
        _precio=precio,
        _inmaduros=inmaduros,
        _campanas=campanas,
        _eventos_campana={k: tuple(v) for k, v in eventos_campana.items()},
        _eventos_grupo={k: tuple(v) for k, v in eventos_grupo.items()},
        _version_economia=version_economia,
        _dias_con_ingesta=dias,
        _d=d,
        _visto_el=visto_el,
        _observado_al=observado,
        _temporada=temporada_plat,
        _historia=historia,
        _historia_desde=desde_historia,
        _recuperacion=recuperacion,
        _recuperacion_live=recuperacion_live,
        _presupuesto=presupuesto,
        _presupuesto_live=presupuesto_live,
    )
