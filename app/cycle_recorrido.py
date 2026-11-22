"""Recorrido TX2 (REPEATABLE READ) de entidades y ad groups del ciclo Ads.

Extraido de `app/cycle.py` (Ola D1): lectura de goals, gates de entidad,
recorrido de decisoras (hojas) y grupos (terminos/harvest), y construccion de
la lista de `_Pendiente` y `_Contadores` dentro de TX2.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import psycopg

from app import (
    apply,
    apply_cola,
    propuestas_campana,
    proteccion_venta,
    temporada_io,
)
from app.cycle_grupos import (
    _SQL_GRUPOS,
    MOTIVO_BID_EN_VUELO,
    MOTIVO_CAMPANA_NO_ENABLED,
    MOTIVO_COOLDOWN_7D,
    MOTIVO_ENTIDAD_INERTE,
    MOTIVO_GRUPO_NO_ENABLED,
    MOTIVO_PAUSA_PRODUCTO_PENDIENTE,
    MOTIVO_POLITICA_APAGADA,
    MOTIVO_SIN_TARGET,
    MOTIVO_VETO_PENDIENTE,
    _Contadores,
    _gates_entidad,
    _procesa_grupo,
    _resuelve_target_y_procedencia,
    _saltos_previos,
    lee_goals,
)
from app.cycle_serializacion import (
    _Pendiente,
    _pendiente_pause_apagada,
    _proteccion_activa,
    _registra_proteccion,
    pendiente_bid,
)
from app.cycle_sombra import (
    MOTIVO_PRODUCTO_SIN_EVIDENCIA,
    _anota_sombra,
    _evalua_productos,
    _insumos_pausa,
    _juicio_hoja,
    _meta_viva,
    _pendientes_pausa_producto,
    _ProductosCiclo,
)
from app.cycle_target import (
    _FamiliasCiclo,
    _lee_familias_ciclo,
    _margen_familia_de_hoja,
    _resuelve_target_ciclo,
    _TargetCiclo,
)
from app.cycle_temporada import (
    _anota_regreso_retenido,
    _compara_temporada,
    _estampa_temporada,
    _frontera_temporada,
    _pausa_sombra,
)
from app.lecturas_caso import LecturasPlataforma, lee_plataforma
from app.optimizer import bid, cortes, hygiene, productos, temporada, windows
from app.optimizer import goals as g
from app.optimizer import proteccion as subproteccion
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import BidVigente, EconomiaPlataforma, InsumosRecuperacion
from app.optimizer.politica import Mantener, Pausar, decide

_SQL_CAMPANAS = """
SELECT c.id, s.acos_target
  FROM ad_entity c
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = c.id
 WHERE c.platform = %s::platform AND c.kind = 'campaign'
"""

_SQL_DECISORAS = """
SELECT k.id, k.parent_id AS ad_group_id, ag.parent_id AS campaign_id,
       s.current_bid, s.bid_currency, s.status, s.acos_target,
       sg.status AS status_grupo, sc.status AS status_campana
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
  LEFT JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = ag.parent_id
 WHERE k.platform = %s::platform AND k.kind IN ('keyword', 'product_target')
 ORDER BY k.id
"""

_SQL_INERTES = "SELECT id FROM v_entidad_inerte WHERE platform = %s::platform"

_SQL_GRUPOS_PAUSA_PRODUCTO = apply_cola.SQL_GRUPOS_PAUSA_PRODUCTO


_SQL_TIENE_GRUPOS_FABRICA = "SELECT to_regclass('public.campana_grupo_rol') IS NOT NULL"


def _procesa_decisora(  # noqa: PLR0915 - recorrido unico de una hoja
    conn: psycopg.Connection,
    *,
    fila,
    platform: str,
    setting_target: Decimal | None,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    evidencia_ad_groups: dict[int, windows.EvidenciaAdGroup],
    corte_pause_por_grupo: dict[int, windows.EvidenciaAdGroup | None],
    bloqueadas: set[tuple[int, str, str | None]],
    inertes: set[int],
    margen_plataforma: Decimal | None,
    snapshot_margen: dict,
    familias_ciclo: _FamiliasCiclo,
    pause_economica: bool,
    politica: str | None,
    lecturas: LecturasPlataforma | None,
    productos_ciclo: _ProductosCiclo | None = None,
    grupos_pausa_producto: frozenset[int] = frozenset(),
    hojas_bid_en_vuelo: frozenset[int] = frozenset(),
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    claves_released: frozenset[tuple[int, str, str | None]] = frozenset(),
    decisor=decide,
) -> None:
    (
        entidad_id,
        ad_group_id,
        campaign_id,
        current_bid,
        bid_currency,
        status,
        _acos_cache,
        status_grupo,
        status_campana,
    ) = fila
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=entidad_id,
        status=status,
        ancestros=(
            (MOTIVO_CAMPANA_NO_ENABLED, status_campana),
            (MOTIVO_GRUPO_NO_ENABLED, status_grupo),
        ),
        decided_at=decided_at,
        comprobar_cooldown=False,
    )
    if motivo is not None:
        contadores.skips_entidad[motivo] += 1
        tick()
        return
    assert goal is not None
    floor, ceiling = g.resuelve_floor_ceiling(goal, PLATAFORMAS_MONEDA[platform])
    frontera_h, efecto_en_vuelo = _frontera_temporada(
        lecturas, contadores, entidad_id, current_bid, floor=floor, ceiling=ceiling
    )
    clave_pausa = (entidad_id, "entity_cut", None)
    bloqueada = clave_pausa in bloqueadas
    espera_released = clave_pausa in claves_released
    protegible = proteccion_cache is not None and proteccion_cache.tiene_proteccion(entidad_id)
    if bloqueada and not (espera_released and protegible):
        contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
        tick()
        return
    plat_t_inerte = contadores.temporada_plat
    entran_inertes = (
        lecturas is not None
        and plat_t_inerte is not None
        and plat_t_inerte.modo == "live"
        and lecturas.tiene_pendiente(entidad_id, current_bid, piso=floor, techo=ceiling)
    )
    if entidad_id in inertes and not entran_inertes:
        contadores.skips_entidad[MOTIVO_ENTIDAD_INERTE] += 1
        tick()
        return
    if ad_group_id not in corte_pause_por_grupo:
        corte_pause_por_grupo[ad_group_id] = evidencia_ad_groups.get(ad_group_id)
    evidencia = corte_pause_por_grupo[ad_group_id]
    plat_t_hoja = contadores.temporada_plat
    mult_baja_hoja = (
        frontera_h.mult_cortes
        if (
            frontera_h is not None
            and frontera_h.fase_hoja == "baja"
            and plat_t_hoja is not None
            and plat_t_hoja.modo == "live"
        )
        else None
    )
    corte_pause = cortes.umbral_corte(evidencia, "pause", mult_baja=mult_baja_hoja)
    target_margen_familia, snapshot_fam = _margen_familia_de_hoja(
        familias_ciclo, campaign_id, entidad_id
    )
    previo = familias_ciclo.previos.get(entidad_id)
    target_base = g.cascada_target_acos(
        goal.target_acos_pct,
        setting_target,
        margen_plataforma,
        goal.scope,
        target_margen_familia,
    )
    meta_viva = _meta_viva(
        productos_ciclo, goal=goal, ad_group_id=ad_group_id, previo=previo, destino=target_base
    )
    if meta_viva == MOTIVO_PRODUCTO_SIN_EVIDENCIA:
        contadores.skips_entidad[MOTIVO_PRODUCTO_SIN_EVIDENCIA] += 1
        tick()
        return
    assert not isinstance(meta_viva, str)
    target_margen_producto = meta_viva.resolucion.aplicado if meta_viva is not None else None
    if meta_viva is not None:
        assert productos_ciclo is not None
        productos_ciclo.hojas_aptas[entidad_id] = meta_viva.product_id
    target, procedencia = _resuelve_target_y_procedencia(
        goal,
        setting_target,
        margen_plataforma,
        target_margen_familia,
        target_margen_producto=target_margen_producto,
    )
    if target is None:

        def juicio_hipotetico(t: Decimal, equilibrio: Decimal | None):
            with conn.transaction():
                return _juicio_hoja(
                    conn,
                    t,
                    platform=platform,
                    goal=goal,
                    entidad_id=entidad_id,
                    ad_group_id=ad_group_id,
                    current_bid=current_bid,
                    bid_currency=bid_currency,
                    decided_at=decided_at,
                    corte_pause=corte_pause,
                    pause_economica=pause_economica,
                    politica=politica,
                    lecturas=lecturas,
                    proteccion_cache=proteccion_cache,
                    equilibrio_acos_pct=equilibrio,
                )

        _anota_sombra(
            productos_ciclo,
            entidad_id=entidad_id,
            ad_group_id=ad_group_id,
            goal=goal,
            setting_target=setting_target,
            margen_plataforma=margen_plataforma,
            target_margen_familia=target_margen_familia,
            previo=previo,
            meta_viva=meta_viva,
            target=None,
            procedencia=None,
            actual=Mantener(MOTIVO_SIN_TARGET),
            decide_con=juicio_hipotetico,
        )
        contadores.skips_entidad[MOTIVO_SIN_TARGET] += 1
        tick()
        return
    assert procedencia is not None
    snapshot = {
        "margen_plataforma": snapshot_margen,
        "margen_familia": snapshot_fam,
        "margen_producto": meta_viva.snapshot() if meta_viva is not None else None,
    }.get(procedencia)
    evaluacion_producto = meta_viva.product_id if meta_viva is not None else None
    contadores.targets[entidad_id] = (target, procedencia)
    plat_t = contadores.temporada_plat
    mult_live = cerca_live = None
    if plat_t is not None and plat_t.modo == "live" and frontera_h is not None:
        cerca_live = frontera_h.cerca_hoja
        if frontera_h.fase_hoja == "baja":
            mult_live = frontera_h.mult_cortes
    costo_piso, cortes_hoja, pausa = _insumos_pausa(
        conn,
        platform=platform,
        entidad_id=entidad_id,
        decided_at=decided_at,
        corte_pause=corte_pause,
        pause_economica=pause_economica,
        proteccion_cache=proteccion_cache,
        mult_baja=mult_live,
        cerca=cerca_live,
    )
    if politica is None:
        resultado = bid.decide_pause(
            platform=platform,
            bids=None,
            cortes=cortes_hoja,
            target_acos_pct=target,
            umbral_pause=corte_pause.umbral,
            cost_min=costo_piso,
            policy_version=pausa.politica_economica,
            proteccion=pausa.proteccion,
            exceso_minimo=pausa.exceso_minimo,
        )
        _anota_sombra(
            productos_ciclo,
            entidad_id=entidad_id,
            ad_group_id=ad_group_id,
            goal=goal,
            setting_target=setting_target,
            margen_plataforma=margen_plataforma,
            target_margen_familia=target_margen_familia,
            previo=previo,
            meta_viva=meta_viva,
            target=target,
            procedencia=procedencia,
            actual=resultado,
            decide_con=lambda t, _eq: bid.decide_pause(
                platform=platform,
                bids=None,
                cortes=cortes_hoja,
                target_acos_pct=t,
                umbral_pause=corte_pause.umbral,
                cost_min=costo_piso,
                policy_version=pausa.politica_economica,
                proteccion=pausa.proteccion,
                exceso_minimo=pausa.exceso_minimo,
            ),
        )
        tick()
        if resultado.kind != "pause":
            if resultado.proteccion is not None and resultado.motivo in (
                subproteccion.MOTIVO_PROTEGIDA,
                subproteccion.MOTIVO_INDETERMINADA,
            ):
                _registra_proteccion(
                    contadores,
                    proteccion_cache,
                    entidad_id,
                    resultado.proteccion,
                    decidido_el=decided_at,
                )
                contadores.skips_entidad[resultado.motivo] += 1
                return
            contadores.skips_entidad[MOTIVO_POLITICA_APAGADA] += 1
            return
        if bloqueada:
            contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
            return
        if g.en_cooldown(conn, entidad_id, ahora=decided_at, kind="pause"):
            contadores.skips_entidad[MOTIVO_COOLDOWN_7D] += 1
            return
        pendiente = _pendiente_pause_apagada(
            entidad_id,
            resultado,
            platform=platform,
            modo=modo,
            goal=goal,
            cortes=cortes_hoja,
            target=target,
            procedencia=procedencia,
            snapshot=snapshot,
            bid_actual=current_bid,
            bid_moneda=bid_currency,
            decided_at=decided_at,
            corte=corte_pause,
            evidencia=evidencia,
            cost_min=costo_piso,
            policy_version=pausa.politica_economica,
            proteccion=pausa.proteccion,
        )
        pendientes.append(replace(pendiente, evaluacion_producto=evaluacion_producto))
        contadores.decisiones["pause"] += 1
        return
    assert lecturas is not None
    equilibrio_hoja = (
        meta_viva.equilibrio_pct
        if meta_viva is not None
        and meta_viva.version == productos.POLITICA_ECONOMIA
        and procedencia == "margen_producto"
        else None
    )
    pausa_sombra = _pausa_sombra(
        conn,
        contadores=contadores,
        platform=platform,
        entidad_id=entidad_id,
        decided_at=decided_at,
        frontera_h=frontera_h,
        en_vuelo=efecto_en_vuelo,
        evidencia=evidencia,
        pause_economica=pause_economica,
        proteccion_cache=proteccion_cache,
    )
    caso, caso_sombra = lecturas.casos(
        conn,
        hoja_id=entidad_id,
        ad_group_id=ad_group_id,
        bid=BidVigente(valor=current_bid, moneda=bid_currency, piso=floor, techo=ceiling),
        target_acos_pct=target,
        pausa=pausa,
        pausa_sombra=pausa_sombra,
        equilibrio_acos_pct=equilibrio_hoja,
        procedencia=procedencia,
        con_temporada=not efecto_en_vuelo,
    )
    veredicto = decisor(caso)
    _anota_regreso_retenido(
        contadores,
        lecturas,
        caso,
        veredicto,
        frontera_h,
        entidad_id,
        current_bid,
        floor=floor,
        techo=ceiling,
    )
    if caso_sombra is not None:
        _compara_temporada(contadores, entidad_id, caso, veredicto, decisor(caso_sombra))
    _anota_sombra(
        productos_ciclo,
        entidad_id=entidad_id,
        ad_group_id=ad_group_id,
        goal=goal,
        setting_target=setting_target,
        margen_plataforma=margen_plataforma,
        target_margen_familia=target_margen_familia,
        previo=previo,
        meta_viva=meta_viva,
        target=target,
        procedencia=procedencia,
        actual=veredicto,
        decide_con=lambda t, equilibrio: decisor(
            replace(
                caso,
                economia=replace(
                    caso.economia,
                    target_acos_pct=t,
                    plataforma=replace(
                        caso.economia.plataforma,
                        equilibrio_acos_pct=(
                            equilibrio
                            if equilibrio is not None
                            else caso.economia.plataforma.equilibrio_acos_pct
                        ),
                    ),
                ),
            )
        ),
    )
    tick()
    protegida = _proteccion_activa(
        platform=platform,
        entidad_id=entidad_id,
        target=target,
        corte_pause=corte_pause,
        cortes_hoja=cortes_hoja,
        costo_piso=costo_piso,
        pausa=pausa,
        proteccion_cache=proteccion_cache,
        contadores=contadores,
        decided_at=decided_at,
    )
    if isinstance(veredicto, Pausar) and (bloqueada or protegida):
        contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
        return
    if isinstance(veredicto, Mantener):
        contadores.skips_entidad[veredicto.motivo] += 1
        return
    if entidad_id in hojas_bid_en_vuelo and not isinstance(veredicto, Pausar):
        contadores.skips_entidad[MOTIVO_BID_EN_VUELO] += 1
        return
    if (
        ad_group_id in grupos_pausa_producto
        and not isinstance(veredicto, Pausar)
        and current_bid is not None
        and veredicto.bid_nuevo > current_bid
    ):
        contadores.skips_entidad[MOTIVO_PAUSA_PRODUCTO_PENDIENTE] += 1
        return
    if isinstance(veredicto, Pausar) and g.en_cooldown(
        conn, entidad_id, ahora=decided_at, kind="pause"
    ):
        contadores.skips_entidad[MOTIVO_COOLDOWN_7D] += 1
        return
    pendiente = _estampa_temporada(
        pendiente_bid(
            caso,
            veredicto,
            modo=modo,
            goal=goal,
            decidido_el=decided_at,
            procedencia=procedencia,
            snapshot=snapshot,
            corte=corte_pause,
            evidencia=evidencia,
            cost_min=costo_piso,
        ),
        frontera_h,
    )
    pendientes.append(replace(pendiente, evaluacion_producto=evaluacion_producto))
    contadores.decisiones["pause" if isinstance(veredicto, Pausar) else "bid"] += 1


def _recorre_plataforma(
    conn: psycopg.Connection,
    *,
    platform: str,
    settings: dict,
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    bloqueadas: set[tuple[int, str, str | None]],
    cycle_id: int,
    decisor=decide,
) -> _TargetCiclo:
    setting_target = g.target_desde_settings(settings, platform)
    target_ciclo = _resuelve_target_ciclo(
        conn,
        platform=platform,
        settings=settings,
        setting_target=setting_target,
        cycle_id=cycle_id,
        decided_at=decided_at,
    )
    modo_economia = (
        productos.economia_desde_settings(settings, platform)
        if platform in productos.PLATAFORMAS
        else productos.ECONOMIA_AUTO03
    )
    destino_familia = target_ciclo.margen if target_ciclo.margen is not None else setting_target
    familias_ciclo = _lee_familias_ciclo(
        conn,
        platform=platform,
        cycle_id=cycle_id,
        hoy=decided_at.date(),
        fraccion=g.fraccion_desde_settings(settings, platform),
        destino=destino_familia,
        economia=modo_economia,
    )
    target_ciclo = replace(
        target_ciclo, snapshot={**target_ciclo.snapshot, "familias": familias_ciclo.notas}
    )
    ids_campanas = [fila[0] for fila in conn.execute(_SQL_CAMPANAS, (platform,)).fetchall()]
    goals = lee_goals(conn, platform, ids_campanas)
    if platform in productos.PLATAFORMAS:
        modos = productos.modos_desde_settings(settings, platform)
        if modos != productos.ModosProducto("off", "off"):
            contadores.productos = _evalua_productos(
                conn,
                platform=platform,
                settings=settings,
                decided_at=decided_at,
                goals=goals,
                modos=modos,
                economia=modo_economia,
            )
    if g.propuestas_campana_desde_settings(settings):
        contadores.propuestas_campana = propuestas_campana.lee_evaluaciones(
            conn,
            platform=platform,
            decidido=decided_at,
            settings=settings,
            target_margen=target_ciclo.margen,
        )
    evidencia_ad_groups = windows.ventanas_evidencia_ad_group(conn, platform, decided_at)
    keywords_propias = hygiene.keywords_exactas_por_grupo(conn, platform)
    if subproteccion.activa_desde_settings(settings):
        proteccion_cache = proteccion_venta.lee_cache(conn, platform, decided_at)
        claves_released = apply_cola.claves_released(conn, platform)
    else:
        proteccion_cache = None
        claves_released = frozenset()
    inertes = {f[0] for f in conn.execute(_SQL_INERTES, (platform,)).fetchall()}
    grupos_pausa_producto = apply_cola.grupos_con_pausa_producto(conn, platform)
    hojas_bid_en_vuelo = apply.hojas_con_bid_sin_sello(conn, platform)
    tiene_grupos_fabrica = bool(conn.execute(_SQL_TIENE_GRUPOS_FABRICA).fetchone()[0])
    corte_pause_por_grupo: dict[int, windows.EvidenciaAdGroup | None] = {}
    pause_economica = g.pause_economica_desde_settings(settings)
    politica = g.politica_bid_desde_settings(settings, platform)
    version_economia = g.version_economia_cambio_desde_settings(settings, platform)
    plat_t = None
    rec_r = None
    topada_r = None
    if politica is not None:
        modo_t = temporada.modo_desde_settings(settings, platform)
        if modo_t is not None:
            config_t = temporada.desde_settings(settings)
            estacion = temporada.resuelve(config_t, decided_at.astimezone(dt.UTC).date())
            estado_t = temporada_io.lee_estado(conn, platform, estacion.dia)
            if (
                estacion.fase == "baja"
                and estado_t.episodio is not None
                and estado_t.episodio.abierto_el.astimezone(dt.UTC).date() < estacion.inicio_bloque
            ):
                estado_t = replace(estado_t, episodio=None)
            plat_t = temporada_io.TemporadaPlataforma(
                estacion=estacion,
                modo=modo_t,
                config=config_t,
                meta_alta_pct=temporada.meta_alta_desde_settings(settings, platform),
                estado=estado_t,
            )
            contadores.temporada_plat = plat_t
            contadores.reactivaciones = temporada_io.reactivaciones_pendientes(
                conn, platform, plat_t.config.mantener_pausadas
            )
        modo_r = temporada.modo_recuperacion_desde_settings(settings, platform)
        if modo_r == "live":
            rec_r = InsumosRecuperacion(
                version="recuperacion_v1",
                memoria_dias=temporada.memoria_recuperacion_desde_settings(settings, platform),
            )
            topada_r = temporada.campana_topada_desde_settings(settings)
        elif modo_t == "live":
            topada_r = temporada.campana_topada_desde_settings(settings)
    lecturas: LecturasPlataforma | None = None
    if politica is not None:
        economia = EconomiaPlataforma(
            moneda=PLATAFORMAS_MONEDA[platform],
            equilibrio_acos_pct=target_ciclo.margen_neto_pct,
            gasto_para_concluir=g.gasto_para_concluir_desde_settings(settings, platform),
            confianza_recorte=g.confianza_recorte_desde_settings(settings, platform),
            confianza_subida=g.confianza_subida_desde_settings(settings, platform),
        )
        lecturas = lee_plataforma(
            conn,
            platform,
            decided_at,
            economia=economia,
            version_economia=version_economia,
            temporada_plat=plat_t,
            recuperacion=rec_r,
            campana_topada=topada_r,
        )
    comunes = dict(
        platform=platform,
        setting_target=setting_target,
        goals=goals,
        modo=modo,
        decided_at=decided_at,
        contadores=contadores,
        pendientes=pendientes,
        tick=tick,
        evidencia_ad_groups=evidencia_ad_groups,
        bloqueadas=bloqueadas,
        margen_plataforma=target_ciclo.margen,
        snapshot_margen={k: v for k, v in target_ciclo.snapshot.items() if k != "familias"},
        familias_ciclo=familias_ciclo,
    )
    for fila in conn.execute(_SQL_DECISORAS, (platform,)).fetchall():
        contadores.entidades += 1
        _procesa_decisora(
            conn,
            fila=fila,
            corte_pause_por_grupo=corte_pause_por_grupo,
            inertes=inertes,
            pause_economica=pause_economica,
            politica=politica,
            lecturas=lecturas,
            productos_ciclo=contadores.productos,
            grupos_pausa_producto=grupos_pausa_producto,
            hojas_bid_en_vuelo=hojas_bid_en_vuelo,
            proteccion_cache=proteccion_cache,
            claves_released=claves_released,
            decisor=decisor,
            **comunes,
        )
    for fila in conn.execute(_SQL_GRUPOS, (platform,)).fetchall():
        contadores.ad_groups += 1
        _procesa_grupo(
            conn,
            fila=fila,
            keywords_propias=keywords_propias,
            tiene_grupos_fabrica=tiene_grupos_fabrica,
            **comunes,
        )
    if contadores.productos is not None and contadores.productos.modo_pausa != "off":
        for pendiente in _pendientes_pausa_producto(contadores.productos, decided_at=decided_at):
            pendientes.append(pendiente)
            contadores.decisiones["pause"] += 1
    return target_ciclo


def _fase_lecturas(
    conn: psycopg.Connection,
    *,
    platform: str,
    settings: dict,
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    cycle_id: int,
    decisor=decide,
) -> tuple[windows.MotivoSkip | None, _TargetCiclo | None, dict[str, str] | None]:
    """TX2: guarda de plataforma + claves bloqueadas + recorrido en REPEATABLE READ."""
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        guarda = windows.guarda_plataforma(conn, platform, ahora=decided_at)
        if guarda is not None:
            return (guarda, None, None)
        bloqueadas = apply_cola.claves_bloqueadas(conn, platform, decided_at)
        target_ciclo = _recorre_plataforma(
            conn,
            platform=platform,
            settings=settings,
            modo=modo,
            decided_at=decided_at,
            contadores=contadores,
            pendientes=pendientes,
            tick=tick,
            bloqueadas=bloqueadas,
            cycle_id=cycle_id,
            decisor=decisor,
        )
        return (None, target_ciclo, _saltos_previos(conn, platform, cycle_id))
