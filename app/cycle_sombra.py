"""Evaluacion por producto, meta viva, sombra y pausas de producto del ciclo (Fase 3.4).

Agrupa los tipos `_ProductosCiclo` y `_MetaHoja` y las funciones de evaluacion,
sombra, propuestas de pausa de producto y descarte sin evidencia. Sin imports de
`app.cycle` (DAG: `cycle -> cycle_sombra -> cycle_serializacion`).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg

from app import productos_io, proteccion_venta
from app.cycle_serializacion import _dec_str, _fecha_iso, _Pendiente
from app.lecturas_caso import LecturasPlataforma
from app.optimizer import bid, cortes, productos, windows
from app.optimizer import goals as g
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import BidVigente, InsumosPausa, InsumosProteccion
from app.optimizer.politica import decide
from app.redaction import install_scrub_filter, scrub

if TYPE_CHECKING:
    from app.cycle import _Contadores
    from app.cycle_target import _PrevioHoja

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

# AUTO-03 F.2: con meta en live, una hoja que venia en margen_producto sin
# evaluacion disponible (lectura o guardado fallido) no cae al respaldo.
MOTIVO_PRODUCTO_SIN_EVIDENCIA = "producto_sin_evidencia"


@dataclass
class _ProductosCiclo:
    """AUTO-03 D.4: las evaluaciones por producto, leidas y resueltas UNA vez
    en TX2, el indice grupo apto -> evaluacion y la sombra por hoja (producto
    -> veredictos sin y con la meta). Los errores del camino de productos
    quedan en notes.productos.error y jamas cambian hojas, targets ni
    decisiones."""

    modo_meta: str
    modo_pausa: str = "off"
    modo_economia: str = productos.ECONOMIA_AUTO03
    evaluaciones: tuple[productos.EvaluacionProducto, ...] = ()
    por_grupo: dict[int, productos.EvaluacionProducto] = field(default_factory=dict)
    sombra: dict[int, list[dict]] = field(default_factory=lambda: defaultdict(list))
    resumen: dict | None = None
    errores: list[str] = field(default_factory=list)
    # AUTO-03 F.2, meta en live: hoja de grupo apto -> product_id de la
    # evaluacion que la decidio (sin ella guardada, la hoja no decide).
    hojas_aptas: dict[int, int] = field(default_factory=dict)

    @property
    def lectura_fallida(self) -> bool:
        return self.resumen is None


@dataclass(frozen=True)
class _MetaHoja:
    """AUTO-03 F.2: la meta por producto de una hoja de grupo apto con la
    meta en live. `destino` es la cascada sin producto y `previo` el ultimo
    target live de la hoja (ancla solo si fue margen_producto, S2)."""

    evaluacion: productos.EvaluacionProducto
    grupo: productos.GrupoAptoParaMeta
    resolucion: g.ResolucionMargen
    destino: Decimal | None
    previo: _PrevioHoja | None
    version: str = productos.ECONOMIA_AUTO03

    @property
    def product_id(self) -> int:
        return self.evaluacion.producto.product_id

    @property
    def equilibrio_pct(self) -> Decimal | None:
        """Punto de equilibrio del producto (margen neto antes de publicidad)
        que corresponde a la meta de ESTA hoja. None = el producto no lo mide
        o la resolucion converge por un dato invalido (B-H1: con el ledger
        rancio el equilibrio no se publica y R3 queda apagado)."""
        if self.resolucion.motivo is not None:
            return None
        margen = self.evaluacion.entrada.margen
        return margen.margen_neto_pct if isinstance(margen, g.MedicionMargen) else None

    def snapshot(self) -> dict:
        """target_snapshot de la decision cuando gana margen_producto."""
        meta = self.evaluacion.meta
        margen = self.evaluacion.entrada.margen
        ancla = (
            self.previo.target
            if self.previo is not None and self.previo.procedencia == "margen_producto"
            else None
        )
        aplicado = self.resolucion.aplicado
        halo = self.evaluacion.entrada.ventana_halo
        return {
            "version": self.version,
            "procedencia": "margen_producto",
            "product_id": self.product_id,
            "grupo_id": self.grupo.grupo_id,
            "huella_alcance": self.grupo.huella_alcance,
            "politica": self.evaluacion.politica,
            "meta_pct": _dec_str(meta.porcentaje)
            if isinstance(meta, productos.MetaReferencia)
            else None,
            "margen_neto_pct": _dec_str(margen.margen_neto_pct)
            if isinstance(margen, g.MedicionMargen)
            else None,
            "equilibrio_pct": _dec_str(margen.margen_neto_pct)
            if isinstance(margen, g.MedicionMargen)
            else None,
            "moneda": margen.moneda if isinstance(margen, g.MedicionMargen) else None,
            "margen_ventana_desde": _fecha_iso(margen.ventana_desde)
            if isinstance(margen, g.MedicionMargen)
            else None,
            "margen_ventana_hasta": _fecha_iso(margen.ventana_hasta)
            if isinstance(margen, g.MedicionMargen)
            else None,
            "ventana_halo": {
                "desde": _fecha_iso(halo.desde),
                "hasta_exclusivo": _fecha_iso(halo.hasta_exclusivo),
            },
            "motivos": list(self.evaluacion.motivos),
            "motivo": self.resolucion.motivo,
            "derivado": _dec_str(self.resolucion.derivado),
            "target_aplicado": _dec_str(aplicado),
            "ancla": _dec_str(ancla),
            "paso": _dec_str(aplicado - ancla)
            if ancla is not None and aplicado is not None
            else None,
            "convergiendo": self.resolucion.convergiendo,
            "destino": _dec_str(self.destino),
        }


def _insumos_pausa(
    conn: psycopg.Connection,
    *,
    platform: str,
    entidad_id: int,
    decided_at: dt.datetime,
    corte_pause: cortes.UmbralResuelto,
    pause_economica: bool,
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    mult_baja: Decimal | None = None,
    cerca: dt.date | None = None,
) -> tuple[Decimal, windows.AgregadoMetricas | None, InsumosPausa]:
    """Piso de costo, ventana de cortes e insumos de la PAUSE de una hoja.
    TEMPORADA 01 D: `mult_baja` (el multiplicador del dueno en baja) multiplica
    el piso de pausa y el exceso economico; `cerca` levanta el inicio de la
    ventana de cortes."""
    costo_piso = cortes.piso_pausa(platform, mult_baja=mult_baja)
    cortes_hoja = windows.ventana_cortes(conn, entidad_id, decided_at, cerca=cerca)
    proteccion: InsumosProteccion | None = None
    if proteccion_cache is not None:
        proteccion = proteccion_cache.insumos(
            entidad_id,
            ventana_desde=cortes_hoja.window_start if cortes_hoja is not None else None,
            ventana_hasta=cortes_hoja.window_end if cortes_hoja is not None else None,
        )
    pausa = InsumosPausa(
        cortes=cortes_hoja,
        umbral_clics=corte_pause.umbral,
        gasto_minimo=costo_piso,
        expected_clicks=corte_pause.expected_clicks,
        politica_economica=bid.POLITICA_PAUSE_ECONOMICA if pause_economica else None,
        proteccion=proteccion,
        exceso_minimo=(
            cortes.exceso_minimo(PLATAFORMAS_MONEDA[platform], mult_baja=mult_baja)
            if mult_baja is not None
            else None
        ),
    )
    return costo_piso, cortes_hoja, pausa


def _juicio_hoja(
    conn: psycopg.Connection,
    target: Decimal,
    *,
    platform: str,
    goal: g.Goal,
    entidad_id: int,
    ad_group_id: int,
    current_bid: Decimal | None,
    bid_currency: str | None,
    decided_at: dt.datetime,
    corte_pause: cortes.UmbralResuelto,
    pause_economica: bool,
    politica: str | None,
    lecturas: LecturasPlataforma | None,
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    equilibrio_acos_pct: Decimal | None = None,
):
    """AUTO-03 D.4: el veredicto que el camino de _procesa_decisora daria con
    `target`, con los mismos insumos y sin escribir nada."""
    floor, ceiling = g.resuelve_floor_ceiling(goal, PLATAFORMAS_MONEDA[platform])
    costo_piso, cortes_hoja, pausa = _insumos_pausa(
        conn,
        platform=platform,
        entidad_id=entidad_id,
        decided_at=decided_at,
        corte_pause=corte_pause,
        pause_economica=pause_economica,
        proteccion_cache=proteccion_cache,
    )
    if politica is None:
        return bid.decide_pause(
            platform=platform,
            bids=None,
            cortes=cortes_hoja,
            target_acos_pct=target,
            umbral_pause=corte_pause.umbral,
            cost_min=costo_piso,
            policy_version=pausa.politica_economica,
            proteccion=pausa.proteccion,
        )
    assert lecturas is not None  # _recorre_plataforma: politica => lecturas
    return decide(
        lecturas.caso(
            conn,
            hoja_id=entidad_id,
            ad_group_id=ad_group_id,
            bid=BidVigente(valor=current_bid, moneda=bid_currency, piso=floor, techo=ceiling),
            target_acos_pct=target,
            pausa=pausa,
            equilibrio_acos_pct=equilibrio_acos_pct,
        )
    )


def _evalua_productos(
    conn: psycopg.Connection,
    *,
    platform: str,
    settings: dict,
    decided_at: dt.datetime,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    modos: productos.ModosProducto,
    economia: str = productos.ECONOMIA_AUTO03,
) -> _ProductosCiclo:
    """AUTO-03 D.4: lectura por lote y resolutor, UNA vez por plataforma en
    TX2. La lectura corre en un SAVEPOINT: un error de SQL no aborta TX2 y
    queda en notes.productos.error."""
    resultado = _ProductosCiclo(
        modo_meta=modos.meta, modo_pausa=modos.pausa, modo_economia=economia
    )
    try:
        with conn.transaction():
            entradas = productos_io.lee_entradas_productos(
                conn, platform, decidido_el=decided_at, settings=settings, goals=goals
            )
        evaluaciones = tuple(productos.resolver_producto(e) for e in entradas)
        por_grupo = {
            grupo.grupo_id: ev for ev in evaluaciones for grupo in ev.entrada.grupos_aptos_para_meta
        }
        resumen = productos_io.resumen_cobertura(platform, evaluaciones, goals)
    except Exception as exc:
        logger.warning("productos: lectura fallida en %s: %s", platform, scrub(str(exc)))
        resultado.errores.append(f"lectura: {scrub(str(exc)) or type(exc).__name__}")
        return resultado
    resultado.evaluaciones = evaluaciones
    resultado.por_grupo = por_grupo
    resultado.resumen = resumen
    return resultado


def _plano(valor):
    if isinstance(valor, Decimal):
        return _dec_str(valor)
    if isinstance(valor, dt.date):
        return valor.isoformat()
    if valor is None or isinstance(valor, (str, int, bool)):
        return valor
    return str(valor)


def _veredicto_json(veredicto) -> dict:
    return {
        "tipo": type(veredicto).__name__,
        **{f.name: _plano(getattr(veredicto, f.name)) for f in fields(veredicto)},
    }


def _meta_viva(
    productos_ciclo: _ProductosCiclo | None,
    *,
    goal: g.Goal,
    ad_group_id: int,
    previo: _PrevioHoja | None,
    destino: Decimal | None,
) -> _MetaHoja | str | None:
    """AUTO-03 F.2, meta en live: la meta de una hoja de grupo apto cuyo goal
    no trae target (S1: un goal con target gana y la meta no la alcanza)."""
    if (
        productos_ciclo is None
        or productos_ciclo.modo_meta != "live"
        or goal.target_acos_pct is not None
    ):
        return None
    tiene_previo = previo is not None and previo.procedencia == "margen_producto"
    if productos_ciclo.lectura_fallida:
        return MOTIVO_PRODUCTO_SIN_EVIDENCIA if tiene_previo else None
    evaluacion = productos_ciclo.por_grupo.get(ad_group_id)
    if evaluacion is None:
        return None
    grupo = next(
        gr for gr in evaluacion.entrada.grupos_aptos_para_meta if gr.grupo_id == ad_group_id
    )
    resolucion = productos.meta_de_hoja(
        evaluacion.meta,
        ultimo=previo.target if previo is not None else None,
        destino=destino,
        tiene_previo=tiene_previo,
        version=productos_ciclo.modo_economia,
    )
    return _MetaHoja(
        evaluacion,
        grupo,
        resolucion,
        destino,
        previo,
        version=productos_ciclo.modo_economia,
    )


def _hoja_sombra(
    entidad_id: int,
    ad_group_id: int,
    *,
    target: Decimal | None,
    procedencia: str | None,
    meta: g.ResolucionMargen,
    target_meta: Decimal | None,
    procedencia_meta: str | None,
    actual,
    con_meta,
) -> dict:
    return {
        "hoja_id": entidad_id,
        "grupo_id": ad_group_id,
        "target_actual": _dec_str(target),
        "procedencia_actual": procedencia,
        "meta": {
            "aplicado": _dec_str(meta.aplicado),
            "derivado": _dec_str(meta.derivado),
            "motivo": meta.motivo,
            "convergiendo": meta.convergiendo,
        },
        "target_con_meta": _dec_str(target_meta),
        "procedencia_con_meta": procedencia_meta,
        "veredicto_actual": _veredicto_json(actual),
        "veredicto_con_meta": _veredicto_json(con_meta) if con_meta is not None else None,
    }


def _anota_sombra(
    productos_ciclo: _ProductosCiclo | None,
    *,
    entidad_id: int,
    ad_group_id: int,
    goal: g.Goal,
    setting_target: Decimal | None,
    margen_plataforma: Decimal | None,
    target_margen_familia: Decimal | None,
    previo: _PrevioHoja | None,
    meta_viva: _MetaHoja | None,
    target: Decimal | None,
    procedencia: str | None,
    actual,
    decide_con: Callable[[Decimal, Decimal | None], object],
) -> None:
    """AUTO-03 D.4 / F.2: congela la sombra de meta por producto para una hoja."""
    if productos_ciclo is None:
        return
    if productos_ciclo.modo_meta == "live":
        if meta_viva is not None:
            productos_ciclo.sombra[meta_viva.product_id].append(
                _hoja_sombra(
                    entidad_id,
                    ad_group_id,
                    target=target,
                    procedencia=procedencia,
                    meta=meta_viva.resolucion,
                    target_meta=target,
                    procedencia_meta=procedencia,
                    actual=actual,
                    con_meta=actual,
                )
            )
        return
    if productos_ciclo.modo_meta != "shadow":
        return
    evaluacion = productos_ciclo.por_grupo.get(ad_group_id)
    if evaluacion is None:
        return
    try:
        meta = productos.meta_de_hoja(
            evaluacion.meta,
            ultimo=previo.target if previo is not None else None,
            destino=target,
            tiene_previo=previo is not None and previo.procedencia == "margen_producto",
            version=productos_ciclo.modo_economia,
        )
        target_meta = g.cascada_target_acos(
            goal.target_acos_pct,
            setting_target,
            margen_plataforma,
            goal.scope,
            target_margen_familia,
            target_margen_producto=meta.aplicado,
        )
        procedencia_meta = g.peldano_target_acos(
            goal.target_acos_pct,
            goal.scope,
            margen_plataforma,
            setting_target,
            target_margen_familia,
            target_margen_producto=meta.aplicado,
        )
        margen = evaluacion.entrada.margen
        equilibrio = (
            margen.margen_neto_pct
            if productos_ciclo.modo_economia == productos.POLITICA_ECONOMIA
            and procedencia_meta == "margen_producto"
            and meta.motivo is None
            and isinstance(margen, g.MedicionMargen)
            else None
        )
        con_meta = decide_con(target_meta, equilibrio) if target_meta is not None else None
    except Exception as exc:
        productos_ciclo.errores.append(
            f"sombra hoja {entidad_id}: {scrub(str(exc)) or type(exc).__name__}"
        )
        return
    productos_ciclo.sombra[evaluacion.producto.product_id].append(
        _hoja_sombra(
            entidad_id,
            ad_group_id,
            target=target,
            procedencia=procedencia,
            meta=meta,
            target_meta=target_meta,
            procedencia_meta=procedencia_meta,
            actual=actual,
            con_meta=con_meta,
        )
    )


def _pendientes_pausa_producto(
    productos_ciclo: _ProductosCiclo, *, decided_at: dt.datetime
) -> list[_Pendiente]:
    """AUTO-03 H.3: una decision pause sobre el product_ad por cada propuesta
    de la evaluacion."""
    pendientes = []
    for ev in productos_ciclo.evaluaciones:
        actividad = ev.entrada.actividad_pausa
        limites = ev.entrada.limites_pausa
        for propuesta in ev.pausas:
            assert isinstance(actividad, productos.AdsCompletos)
            inputs = {
                "motor": "producto",
                "politica": ev.politica,
                "platform": ev.producto.plataforma,
                "modo": productos_ciclo.modo_pausa,
                "motivo": propuesta.motivo,
                "product_id": ev.producto.product_id,
                "propuesta": propuesta.como_json(),
                "huella_alcance": propuesta.anuncio.huella_alcance,
                "ventana": {
                    "desde": _fecha_iso(actividad.ventana.desde),
                    "hasta_exclusivo": _fecha_iso(actividad.ventana.hasta_exclusivo),
                },
                "limites": {
                    "clics_min": limites.clics_min,
                    "gasto_min": _dec_str(limites.gasto_min.valor),
                    "moneda": limites.gasto_min.moneda,
                },
                "actividad": {
                    "gasto": _dec_str(actividad.gasto.valor),
                    "clics": actividad.clics,
                    "compras": _dec_str(actividad.compras_total),
                    "ventas": _dec_str(actividad.ventas_total.valor),
                    "moneda": actividad.gasto.moneda,
                },
            }
            pendientes.append(
                _Pendiente(
                    ad_entity_id=propuesta.anuncio.ad_entity_id,
                    kind="pause",
                    data_observed_at=decided_at,
                    window_start=actividad.ventana.desde,
                    window_end=actividad.ventana.hasta_exclusivo - dt.timedelta(days=1),
                    search_term=None,
                    old_value=None,
                    new_value=None,
                    value_currency=None,
                    inputs=inputs,
                    evaluacion_producto=ev.producto.product_id,
                )
            )
    return pendientes


def _notas_productos(
    conn: psycopg.Connection,
    cycle_id: int,
    config_id: int | None,
    productos_ciclo: _ProductosCiclo,
) -> tuple[dict, dict[int, int]]:
    """TX3: guarda las evaluaciones (SAVEPOINT propio, antes que las
    decisiones) y arma notes.productos con el resumen de cobertura."""
    notas: dict = {
        "modo_meta": productos_ciclo.modo_meta,
        "modo_pausa": productos_ciclo.modo_pausa,
        **(productos_ciclo.resumen or {}),
    }
    errores = list(productos_ciclo.errores)
    ids: dict[int, int] = {}
    if productos_ciclo.resumen is not None:
        guardado = productos_io.guarda_evaluaciones(
            conn,
            cycle_id,
            productos_ciclo.evaluaciones,
            config_version_id=config_id,
            sombra_hojas=productos_ciclo.sombra,
            cierra_reactivados=productos_ciclo.modo_pausa != "off",
        )
        if guardado.error is not None:
            errores.append(f"guardado: {guardado.error}")
        else:
            notas["evaluaciones_guardadas"] = len(guardado.ids)
            notas["retiros_reactivados_cerrados"] = list(guardado.retiros_cerrados)
            ids = {product_id: id_ for (_plat, product_id), id_ in guardado.ids.items()}
    if errores:
        notas["error"] = "; ".join(errores)
    return notas, ids


def _descarta_sin_evidencia(
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    ids_evaluacion: dict[int, int],
    cuerpo: dict,
) -> None:
    """AUTO-03 F.2 / H.3: descarta decisiones cuya evaluacion de producto no
    quedo guardada."""
    productos_ciclo = contadores.productos
    assert productos_ciclo is not None
    sin_evidencia = {
        hoja for hoja, pid in productos_ciclo.hojas_aptas.items() if pid not in ids_evaluacion
    }
    pausas_sin_evidencia = [
        p
        for p in pendientes
        if p.inputs.get("motor") == "producto" and p.evaluacion_producto not in ids_evaluacion
    ]
    if not sin_evidencia and not pausas_sin_evidencia:
        return
    for pendiente in pausas_sin_evidencia:
        pendientes.remove(pendiente)
        contadores.decisiones[pendiente.kind] -= 1
        contadores.skips_entidad[MOTIVO_PRODUCTO_SIN_EVIDENCIA] += 1
    for pendiente in [p for p in pendientes if p.ad_entity_id in sin_evidencia]:
        pendientes.remove(pendiente)
        contadores.decisiones[pendiente.kind] -= 1
        contadores.skips_entidad[MOTIVO_PRODUCTO_SIN_EVIDENCIA] += 1
    for hoja in sin_evidencia:
        contadores.targets.pop(hoja, None)
    cuerpo["skips"]["entidad"] = dict(contadores.skips_entidad)
    cuerpo["decisiones"] = {k: n for k, n in contadores.decisiones.items() if n}
