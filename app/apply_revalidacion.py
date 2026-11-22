"""Re-validacion PRE-claim de cortes `pause` y `negative` (ORBIT 04 / ORBIT 07 M1-M2).

Extraido de `app/apply_cola.py` (Ola D3b). Introduce `VeredictoRevalidacion`
para distinguir tipadamente `aplicar`, `esperar` (fila sigue `released`) y
`descartar` (transicion terminal `discarded`), y acepta `evidencia_grupos`
precalculada una vez por barrido FIFO en `libera_vencidos` (evita recalcular
80 dias de metricas por fila).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import psycopg

from app import (
    apply,
    economia_revalidacion_io,
    productos_io,
    proteccion_venta,
    temporada_io,
)
from app.optimizer import bid as motor_bid
from app.optimizer import cortes, hygiene, productos, windows
from app.optimizer import goals as g
from app.optimizer import proteccion as subproteccion

if TYPE_CHECKING:
    from app.apply import Aplicador
    from app.apply_cola import FilaCola

GRACIA_REACTIVACION = dt.timedelta(days=7)

MOTIVO_VENDIO_EN_VENTANA = "vendio_en_ventana"
MOTIVO_YA_NO_CALIFICA = "ya_no_califica"
MOTIVO_ENTIDAD_NO_VIVA = "entidad_no_viva"
MOTIVO_REACTIVACION_MANUAL = "reactivacion_manual"
MOTIVO_MODO_NO_LIVE = "modo_no_live"
MOTIVO_ALCANCE_CAMBIO = "alcance_cambio"
MOTIVO_ESPERA_TARGET = apply.MOTIVO_ESPERA_TARGET
MOTIVO_ESPERA_PROTECCION = apply.MOTIVO_ESPERA_PROTECCION
MOTIVO_PROTECCION_APAGADA = "proteccion_apagada"
MOTIVO_CAMPANA_NO_ENABLED = apply.MOTIVO_CAMPANA_NO_ENABLED
MOTIVO_GRUPO_NO_ENABLED = apply.MOTIVO_GRUPO_NO_ENABLED

MOTIVOS_ESPERA = frozenset({MOTIVO_ESPERA_TARGET, MOTIVO_ESPERA_PROTECCION})

# El PAUSE antiguo no consume target; el economico resuelve el vigente.
_TARGET_REVALIDA = Decimal("100")

_SQL_GRACIA = """
SELECT detectada_en FROM reactivacion_manual WHERE ad_entity_id = %s
"""

_SQL_PAUSE_PROPIO = """
SELECT EXISTS (
    SELECT 1
      FROM decision_application da
      JOIN decision d ON d.id = da.decision_id
     WHERE d.ad_entity_id = %s AND d.kind = 'pause' AND da.verify_ok IS TRUE
       AND NOT EXISTS (
           SELECT 1
             FROM decision r
            WHERE r.kind = 'resume' AND r.inputs->>'motor' = 'temporada'
              AND (r.inputs->>'pausa_decision_id')::bigint = d.id
              AND EXISTS (SELECT 1 FROM apply_attempt a
                           WHERE a.decision_id = d.id
                             AND a.tipo = 'reversa' AND a.resultado = 'ok'))
)
"""

_SQL_INSERT_REACTIVACION = """
INSERT INTO reactivacion_manual (ad_entity_id) VALUES (%s) ON CONFLICT DO NOTHING
"""

_SQL_PADRE = """
SELECT parent_id FROM ad_entity WHERE id = %s
"""

_SQL_CAMPANA_DE_GRUPO = """
SELECT parent_id FROM ad_entity WHERE id = %s AND kind = 'ad_group'
"""

_SQL_DECISION_INPUTS = """
SELECT inputs FROM decision WHERE id = %s
"""

_SQL_PAUSE_TARGET_STATE = """
SELECT ag.parent_id, s.acos_target
  FROM ad_entity e
  JOIN ad_entity ag ON ag.id = e.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.id = %s AND e.kind IN ('keyword', 'product_target')
"""

_SQL_NOTAS_CICLO_APLICADOR = """
SELECT notes FROM optimizer_cycle WHERE id = %s
"""

_SQL_CONFIG_TARGET = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""

_SQL_LEE_NOTAS_CICLO = "SELECT notes FROM optimizer_cycle WHERE id = %s FOR UPDATE"
_SQL_ESCRIBE_NOTAS_CICLO = "UPDATE optimizer_cycle SET notes = %s WHERE id = %s"

_SQL_HAY_GOAL = """
SELECT 1 FROM ads_optimizer_goal
 WHERE (scope = 'platform' AND platform = %s)
    OR (scope = 'campaign' AND ad_entity_id = %s)
 LIMIT 1
"""


@dataclass(frozen=True)
class VeredictoRevalidacion:
    """Resultado tipado de re-validacion PRE-claim: aplicar, esperar o descartar."""

    accion: Literal["aplicar", "esperar", "descartar"]
    motivo: str | None = None


def veredicto_de_motivo(motivo: str | None) -> VeredictoRevalidacion:
    """Traduce el motivo de `_revalida` al veredicto tipado del barrido FIFO."""
    if motivo is None:
        return VeredictoRevalidacion("aplicar")
    if motivo in MOTIVOS_ESPERA:
        return VeredictoRevalidacion("esperar", motivo)
    return VeredictoRevalidacion("descartar", motivo)


def _gracia_activa(conn: psycopg.Connection, ad_entity_id: int, ahora: dt.datetime) -> bool:
    fila = conn.execute(_SQL_GRACIA, (ad_entity_id,)).fetchone()
    return fila is not None and (ahora - fila[0]) < GRACIA_REACTIVACION


def _pause_propio_verificado(conn: psycopg.Connection, ad_entity_id: int) -> bool:
    return conn.execute(_SQL_PAUSE_PROPIO, (ad_entity_id,)).fetchone()[0]


def _flag_pause_economica(conn: psycopg.Connection) -> bool:
    """C.3 B1: la PAUSE economica rige solo con ads_pause_economica=true."""
    config = conn.execute(_SQL_CONFIG_TARGET).fetchone()
    settings = config[0] if config is not None else {}
    if not isinstance(settings, dict):
        return False
    return g.pause_economica_desde_settings(settings)


def _peldano_congelado(inputs: dict, peldano: str) -> Decimal | None:
    """El target_aplicado del snapshot de la decision si gano `peldano`."""
    snapshot = inputs.get("target_snapshot")
    if inputs.get("target_procedencia") != peldano or not isinstance(snapshot, dict):
        return None
    valor = snapshot.get("target_aplicado")
    return Decimal(str(valor)) if valor is not None else None


def _alcance_cambio(conn: psycopg.Connection, ad_entity_id: int, inputs: dict) -> bool:
    """AUTO-03 F.3 (S12): huella del grupo congelada vs actual."""
    if inputs.get("target_procedencia") != "margen_producto":
        return False
    snapshot = inputs.get("target_snapshot")
    congelada = snapshot.get("huella_alcance") if isinstance(snapshot, dict) else None
    grupo = conn.execute(_SQL_PADRE, (ad_entity_id,)).fetchone()[0]
    return congelada is None or productos_io.huella_grupo(conn, grupo) != congelada


def _settings_vigentes(conn: psycopg.Connection) -> dict:
    config = conn.execute(_SQL_CONFIG_TARGET, ()).fetchone()
    settings = config[0] if config is not None else {}
    return settings if isinstance(settings, dict) else {}


def _economia_elegida(settings: dict, platform: str) -> str:
    if platform not in productos.PLATAFORMAS:
        return productos.ECONOMIA_AUTO03
    return productos.economia_desde_settings(settings, platform)


def _producto_fresco(
    conn: psycopg.Connection,
    platform: str,
    grupo: int,
    cycle_id: int,
    settings: dict,
    inputs: dict,
    *,
    ad_entity_id: int,
    destino: Decimal | None,
) -> tuple[Decimal | None, dict]:
    """ORBIT 07 M2: meta del producto resuelta con la evaluacion del ciclo EJECUTOR."""
    detalle: dict = {"grupo_id": grupo}
    modos = productos.modos_desde_settings(settings, platform)
    if modos.meta != "live":
        detalle["motivo"] = "meta_no_live"
        return (None, detalle)
    producto = economia_revalidacion_io.producto_exclusivo(conn, grupo)
    detalle["product_id"] = producto
    if producto is None:
        detalle["motivo"] = "grupo_no_exclusivo"
        return (None, detalle)
    snapshot = inputs.get("target_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    congelado = snapshot.get("product_id")
    if congelado is not None and int(congelado) != producto:
        detalle["motivo"] = "identidad_cambio"
        return (None, detalle)
    leida = economia_revalidacion_io.lee_evaluacion_ciclo(conn, cycle_id, platform, producto)
    if leida is None:
        detalle["motivo"] = "sin_evaluacion_fresca"
        return (None, detalle)
    evaluacion_id, evaluacion = leida
    detalle.update(
        {
            "evaluacion_id": evaluacion_id,
            "politica": evaluacion.politica,
            "motivos": list(evaluacion.motivos),
        }
    )
    grupo_apto = next(
        (gr for gr in evaluacion.entrada.grupos_aptos_para_meta if gr.grupo_id == grupo),
        None,
    )
    if grupo_apto is None:
        detalle["motivo"] = "grupo_no_apto"
        return (None, detalle)
    if grupo_apto.huella_alcance != productos_io.huella_grupo(conn, grupo):
        detalle["motivo"] = MOTIVO_ALCANCE_CAMBIO
        return (None, detalle)
    margen = evaluacion.entrada.margen
    detalle.update(
        {
            "meta_pct": str(evaluacion.meta.porcentaje)
            if isinstance(evaluacion.meta, productos.MetaReferencia)
            else None,
            "equilibrio_pct": str(margen.margen_neto_pct)
            if isinstance(margen, g.MedicionMargen) and margen.margen_neto_pct is not None
            else None,
            "moneda": margen.moneda if isinstance(margen, g.MedicionMargen) else None,
            "huella_alcance": grupo_apto.huella_alcance,
        }
    )
    previo = economia_revalidacion_io.previo_hoja(conn, ad_entity_id, platform, cycle_id)
    tiene_previo = previo is not None and previo[1] == "margen_producto"
    resolucion = productos.meta_de_hoja(
        evaluacion.meta,
        ultimo=previo[0] if previo is not None else None,
        destino=destino,
        tiene_previo=tiene_previo,
        version=productos.POLITICA_ECONOMIA,
    )
    detalle["motivo"] = resolucion.motivo
    detalle["previo"] = str(previo[0]) if previo is not None else None
    detalle["tiene_previo"] = tiene_previo
    if resolucion.aplicado is None:
        return (None, detalle)
    detalle["target_aplicado"] = str(resolucion.aplicado)
    return (resolucion.aplicado, detalle)


def _familia_respaldo(
    conn: psycopg.Connection,
    platform: str,
    grupo: int,
    settings: dict,
    destino: Decimal | None,
    ahora: dt.datetime,
    *,
    ad_entity_id: int,
    cycle_id: int,
    economia: str,
) -> tuple[Decimal | None, dict]:
    """ORBIT 07 M2: peldano familia fresco cuando la hoja venia de la meta individual."""
    campana = conn.execute(_SQL_CAMPANA_DE_GRUPO, (grupo,)).fetchone()
    if campana is None:
        return (None, {"motivo": "sin_campana"})
    return economia_revalidacion_io.familia_fresca(
        conn,
        platform,
        campana[0],
        ad_entity_id=ad_entity_id,
        cycle_id=cycle_id,
        ahora=ahora,
        fraccion=g.fraccion_desde_settings(settings, platform),
        destino=destino,
        version=economia,
    )


def _target_pause_vigente_detalle(
    conn: psycopg.Connection,
    platform: str,
    ad_entity_id: int,
    cycle_id: int,
    inputs: dict,
    ahora: dt.datetime,
    economia: str | None = None,
) -> tuple[Decimal | None, str | None, dict]:
    """Resuelve la cascada del ciclo aplicador con goal y cache frescos."""
    detalle: dict = {}
    estado = conn.execute(_SQL_PAUSE_TARGET_STATE, (ad_entity_id,)).fetchone()
    notas_fila = conn.execute(_SQL_NOTAS_CICLO_APLICADOR, (cycle_id,)).fetchone()
    if estado is None or notas_fila is None:
        return (None, None, detalle)
    try:
        notas = json.loads(notas_fila[0]) if isinstance(notas_fila[0], str) else notas_fila[0]
        snapshot = notas["target"]
        if not isinstance(snapshot, dict) or "target_aplicado" not in snapshot:
            return (None, None, detalle)
        margen = snapshot["target_aplicado"]
        margen = Decimal(str(margen)) if margen is not None else None
        config = conn.execute(_SQL_CONFIG_TARGET).fetchone()
        settings = config[0] if config is not None else {}
        if economia is None:
            economia = (
                productos.economia_desde_settings(settings, platform)
                if platform in productos.PLATAFORMAS
                else productos.ECONOMIA_AUTO03
            )
        detalle["version"] = economia
        campana = plataforma = None
        for f in conn.execute(apply._SQL_GOALS_ENTIDAD, (platform, estado[0])).fetchall():
            goal = g.Goal(*f)
            if goal.scope == "campaign":
                campana = goal
            else:
                plataforma = goal
        elegido = g.resuelve_goal(campana, plataforma)
        if elegido is None or not elegido.enabled or elegido.mode == "off":
            return (None, None, detalle)
        destino, _ = g.cascada_target_acos_con_procedencia(
            campana, plataforma, settings, estado[1], platform, margen
        )
        detalle["destino"] = str(destino) if destino is not None else None
        if (
            economia == productos.POLITICA_ECONOMIA
            and inputs.get("target_procedencia") == "margen_producto"
        ):
            grupo_fila = conn.execute(_SQL_PADRE, (ad_entity_id,)).fetchone()
            if grupo_fila is None:
                return (None, None, detalle)
            grupo = grupo_fila[0]
            producto, detalle["producto"] = _producto_fresco(
                conn,
                platform,
                grupo,
                cycle_id,
                settings,
                inputs,
                ad_entity_id=ad_entity_id,
                destino=destino,
            )
            familia = None
            if producto is None:
                familia, detalle["familia"] = _familia_respaldo(
                    conn,
                    platform,
                    grupo,
                    settings,
                    destino,
                    ahora,
                    ad_entity_id=ad_entity_id,
                    cycle_id=cycle_id,
                    economia=economia,
                )
        elif economia == productos.POLITICA_ECONOMIA:
            producto = None
            familia = _peldano_congelado(inputs, "margen_familia")
        else:
            producto = _peldano_congelado(inputs, "margen_producto")
            familia = _peldano_congelado(inputs, "margen_familia")
        target, procedencia = g.cascada_target_acos_con_procedencia(
            campana,
            plataforma,
            settings,
            estado[1],
            platform,
            margen,
            familia,
            target_margen_producto=producto,
        )
        detalle["procedencia"] = procedencia
        detalle["target"] = str(target) if target is not None else None
        return (target, procedencia, detalle)
    except (KeyError, TypeError, ValueError, ArithmeticError, json.JSONDecodeError):
        return (None, None, detalle)


def _target_pause_vigente(
    conn: psycopg.Connection,
    platform: str,
    ad_entity_id: int,
    cycle_id: int,
    inputs: dict,
    ahora: dt.datetime | None = None,
    detalle: dict | None = None,
    economia: str | None = None,
) -> tuple[Decimal | None, str | None]:
    target, procedencia, resolucion = _target_pause_vigente_detalle(
        conn,
        platform,
        ad_entity_id,
        cycle_id,
        inputs,
        ahora or dt.datetime.now(dt.UTC),
        economia,
    )
    if detalle is not None:
        detalle.update(resolucion)
    return (target, procedencia)


def _hay_goal(conn: psycopg.Connection, platform: str, ad_entity_id: int) -> bool:
    """True si existe goal de plataforma o de la campana."""
    estado = conn.execute(_SQL_PAUSE_TARGET_STATE, (ad_entity_id,)).fetchone()
    if estado is None:
        return False
    return conn.execute(_SQL_HAY_GOAL, (platform, estado[0])).fetchone() is not None


def _registra_evidencia(
    conn: psycopg.Connection,
    cycle_id: int,
    evidencias: list[dict] | None,
    evidencia: dict,
) -> None:
    """Obs3: anexa evidencia economica a notes del ciclo EJECUTOR en la misma TX."""
    if evidencias is not None:
        evidencias.append(evidencia)
    fila = conn.execute(_SQL_LEE_NOTAS_CICLO, (cycle_id,)).fetchone()
    crudo = fila[0] if fila is not None else None
    notas: dict = {}
    previo = None
    if isinstance(crudo, str) and crudo:
        try:
            notas = json.loads(crudo)
        except ValueError:
            previo = crudo
    if not isinstance(notas, dict):
        previo = crudo if previo is None else previo
        notas = {}
    if previo is not None:
        notas.setdefault("nota_previa_no_json", previo)
    lista = notas.get("revalidaciones_economicas")
    if not isinstance(lista, list):
        lista = []
        notas["revalidaciones_economicas"] = lista
    lista.append(evidencia)
    conn.execute(
        _SQL_ESCRIBE_NOTAS_CICLO,
        (json.dumps(notas, ensure_ascii=False, default=str), cycle_id),
    )


def _floor_ceiling_hoja(
    conn: psycopg.Connection, platform: str, grupo: int
) -> tuple[Decimal, Decimal]:
    """Resuelve piso y techo efectivos de la hoja via el goal de su campana."""
    campana_rv = conn.execute(_SQL_CAMPANA_DE_GRUPO, (grupo,)).fetchone()[0]
    goal_campana = goal_plataforma = None
    for fila_g in conn.execute(apply._SQL_GOALS_ENTIDAD, (platform, campana_rv)).fetchall():
        goal_rv = g.Goal(*fila_g)
        if fila_g[0] == "campaign":
            goal_campana = goal_rv
        else:
            goal_plataforma = goal_rv
    moneda_hoja = motor_bid.PLATAFORMAS_MONEDA[platform]
    return g.resuelve_floor_ceiling(g.resuelve_goal(goal_campana, goal_plataforma), moneda_hoja)


def revalida_pause(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
    evidencias: list[dict] | None = None,
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    *,
    evidencia_grupos: dict | None = None,
    gracia_activa_fn=_gracia_activa,
    pause_propio_fn=_pause_propio_verificado,
    flag_economica_fn=_flag_pause_economica,
    target_vigente_fn=_target_pause_vigente,
    hay_goal_fn=_hay_goal,
) -> str | None:
    """Re-validacion de un pause: LIST fresco + regla completa al reloj de liberacion."""
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        return MOTIVO_ENTIDAD_NO_VIVA
    kind, external_id = identidad
    _, path, contenedor, param = apply._KINDS_DECISORAS[kind]
    estado = apply._estado_de_readback(aplicador._cliente(), path, contenedor, param, external_id)
    if estado != apply.ESTADO_WIRE_ENABLED:
        return MOTIVO_ENTIDAD_NO_VIVA
    if gracia_activa_fn(conn, fila.ad_entity_id, ahora):
        return MOTIVO_REACTIVACION_MANUAL
    if pause_propio_fn(conn, fila.ad_entity_id):
        conn.execute(_SQL_INSERT_REACTIVACION, (fila.ad_entity_id,))
        return MOTIVO_REACTIVACION_MANUAL
    decision = conn.execute(_SQL_DECISION_INPUTS, (fila.decision_id,)).fetchone()
    if decision is None or not isinstance(decision[0], dict):
        return MOTIVO_YA_NO_CALIFICA
    razon = decision[0].get("motivo")
    economica = razon == motor_bid.MOTIVO_PAUSE_ECONOMICA
    settings = _settings_vigentes(conn)
    economia = _economia_elegida(settings, platform)
    if economia == productos.ECONOMIA_AUTO03 and _alcance_cambio(
        conn, fila.ad_entity_id, decision[0]
    ):
        return MOTIVO_ALCANCE_CAMBIO
    resolucion_economica: dict = {}
    target, procedencia = target_vigente_fn(
        conn,
        platform,
        fila.ad_entity_id,
        aplicador.cycle_id_ejecutor,
        decision[0],
        ahora,
        resolucion_economica,
        economia,
    )
    if target is None and economica:
        espera = hay_goal_fn(conn, platform, fila.ad_entity_id)
        _registra_evidencia(
            conn,
            aplicador.cycle_id_ejecutor,
            evidencias,
            {
                "decision_id": fila.decision_id,
                "resultado": MOTIVO_ESPERA_TARGET if espera else MOTIVO_YA_NO_CALIFICA,
                "motivo": "target_no_confiable",
                "revalidado_at": ahora.isoformat(),
                **({"economia": resolucion_economica} if resolucion_economica else {}),
            },
        )
        return MOTIVO_ESPERA_TARGET if espera else MOTIVO_YA_NO_CALIFICA
    grupo = conn.execute(_SQL_PADRE, (fila.ad_entity_id,)).fetchone()[0]
    mapa_ev = (
        evidencia_grupos
        if evidencia_grupos is not None
        else windows.ventanas_evidencia_ad_group(conn, platform, ahora)
    )
    evidencia = mapa_ev.get(grupo)
    floor_rv, ceiling_rv = _floor_ceiling_hoja(conn, platform, grupo)
    frontera_h = temporada_io.frontera_hoja(
        conn, fila.ad_entity_id, ahora.date(), piso=floor_rv, techo=ceiling_rv
    )
    mult_baja = (
        frontera_h.mult_cortes
        if frontera_h is not None and frontera_h.fase_hoja == "baja"
        else None
    )
    cerca = frontera_h.cerca_hoja if frontera_h is not None else None
    umbral = cortes.umbral_corte(evidencia, "pause", mult_baja=mult_baja).umbral
    fresco = windows.ventana_cortes(conn, fila.ad_entity_id, ahora, cerca=cerca)
    version_efectiva = (
        motor_bid.POLITICA_PAUSE_ECONOMICA
        if target is not None and flag_economica_fn(conn)
        else None
    )
    proteccion = (
        proteccion_cache.insumos(
            fila.ad_entity_id,
            ventana_desde=fresco.window_start if fresco is not None else None,
            ventana_hasta=fresco.window_end if fresco is not None else None,
        )
        if proteccion_cache is not None
        else None
    )
    resultado = motor_bid.decide_pause(
        platform=platform,
        bids=None,
        cortes=fresco,
        target_acos_pct=target if target is not None else _TARGET_REVALIDA,
        umbral_pause=umbral,
        cost_min=(
            cortes.piso_pausa(platform, mult_baja=mult_baja) if mult_baja is not None else None
        ),
        policy_version=version_efectiva,
        proteccion=proteccion,
        exceso_minimo=(
            cortes.exceso_minimo(motor_bid.PLATAFORMAS_MONEDA[platform], mult_baja=mult_baja)
            if mult_baja is not None
            else None
        ),
    )
    califica = resultado.kind == "pause"
    if (
        economica
        or resultado.motivo == motor_bid.MOTIVO_PAUSE_ECONOMICA
        or decision[0].get("target_procedencia") == "margen_producto"
    ):
        exceso = (
            motor_bid.exceso_economico(fresco, target, motor_bid.PLATAFORMAS_MONEDA[platform])
            if target is not None
            else None
        )
        _registra_evidencia(
            conn,
            aplicador.cycle_id_ejecutor,
            evidencias,
            {
                "decision_id": fila.decision_id,
                "revalidado_at": ahora.isoformat(),
                "version": version_efectiva,
                "window_start": fresco.window_start.isoformat() if fresco else None,
                "window_end": fresco.window_end.isoformat() if fresco else None,
                "moneda": fresco.metric_currency if fresco else None,
                "target": str(target) if target is not None else None,
                "target_procedencia": procedencia,
                "cost": str(fresco.cost) if fresco and fresco.cost is not None else None,
                "revenue": str(fresco.ad_revenue)
                if fresco and fresco.ad_revenue is not None
                else None,
                "exceso": str(exceso) if exceso is not None else None,
                "multiplicador": str(motor_bid.MULT_PAUSE_ECONOMICA),
                "exceso_minimo": str(
                    motor_bid.EXCESO_MINIMO[motor_bid.PLATAFORMAS_MONEDA[platform]]
                ),
                "resultado": "califica" if califica else MOTIVO_YA_NO_CALIFICA,
                **({"economia": resolucion_economica} if resolucion_economica else {}),
            },
        )
    if califica:
        return None
    if (
        resultado.proteccion is not None
        and resultado.motivo in (subproteccion.MOTIVO_PROTEGIDA, subproteccion.MOTIVO_INDETERMINADA)
        and resultado.proteccion.accion in ("esperar", "indeterminada")
    ):
        if resultado.proteccion.abre_episodio and proteccion_cache is not None:
            transicion = proteccion_cache.transicion(fila.ad_entity_id, resultado.proteccion, ahora)
            if transicion is not None:
                proteccion_venta.inserta_transiciones(conn, [transicion])
        _registra_evidencia(
            conn,
            aplicador.cycle_id_ejecutor,
            evidencias,
            {
                "decision_id": fila.decision_id,
                "revalidado_at": ahora.isoformat(),
                "resultado": MOTIVO_ESPERA_PROTECCION,
                "motivo": resultado.motivo,
                "ancla": resultado.proteccion.ancla.isoformat()
                if resultado.proteccion.ancla is not None
                else None,
                "vence_el": resultado.proteccion.vence_el.isoformat()
                if resultado.proteccion.vence_el is not None
                else None,
                "abre_episodio": resultado.proteccion.abre_episodio,
            },
        )
        return MOTIVO_ESPERA_PROTECCION
    if not economica and fresco is not None and fresco.orders is not None and fresco.orders > 0:
        return MOTIVO_VENDIO_EN_VENTANA
    return MOTIVO_YA_NO_CALIFICA


def revalida_negative(
    conn: psycopg.Connection,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
    *,
    evidencia_grupos: dict | None = None,
) -> str | None:
    """Re-validacion de un negative con evidencia fresca al reloj de liberacion."""
    grupo = fila.ad_entity_id
    mapa_ev = (
        evidencia_grupos
        if evidencia_grupos is not None
        else windows.ventanas_evidencia_ad_group(conn, platform, ahora)
    )
    evidencia = mapa_ev.get(grupo)
    mult_baja = temporada_io.mult_negativa(conn, platform, ahora.date())
    cerca_plat = temporada_io.cerca_plataforma(conn, platform, ahora.date())
    umbral = cortes.umbral_corte(evidencia, "negative", mult_baja=mult_baja).umbral
    piso = cortes.piso_corte(evidencia, platform, mult_baja=mult_baja).piso_cost
    terminos = windows.terminos_cortes(conn, grupo, ahora, cerca=cerca_plat)
    term = next((t for t in terminos.terminos if t.search_term == fila.search_term), None)
    if term is None:
        return MOTIVO_YA_NO_CALIFICA
    single = windows.TerminosCortes(
        ad_entity_id=grupo,
        window_start=terminos.window_start,
        window_end=terminos.window_end,
        fechas_entidad=terminos.fechas_entidad,
        terminos=(term,),
    )
    (resultado,) = hygiene.decide_hygiene(
        platform=platform,
        terminos=single,
        target_acos_pct=_TARGET_REVALIDA,
        config_harvest=None,
        keywords_existentes=frozenset(),
        keywords_propias=hygiene.keywords_exactas_por_grupo(conn, platform, solo_grupo=grupo).get(
            grupo, frozenset()
        ),
        umbral_negative=umbral,
        piso_negative=piso,
    )
    if resultado.kind == "negative":
        return None
    if term.orders is not None and term.orders > 0:
        return MOTIVO_VENDIO_EN_VENTANA
    return MOTIVO_YA_NO_CALIFICA
