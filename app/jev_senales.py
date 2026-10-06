"""El job jev-senales (JEV ADS 02, S.4): sella la senal por busqueda-en-grupo.

Lee con `lector` (READ), escribe solo `jev_*` con `escritor` (JEV, autocommit).
Apagado u ocupado: 0 sin escribir. Seco: plan y cuenta. Avisos en S.8.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from app.ads.structure_plan import huella_anuncios
from app.db import connect
from app.jev_ads import (
    FalloProveedor,
    FichaFaltante,
    HayCompatible,
    Indeterminado,
    Juicio,
    NingunoCompatible,
    NoAplicaTexto,
    _canonico,
    _identidad_del_censo,
    _solo_no_activo,
    componer,
    es_asin_like,
)
from app.jev_catalogo import censo_grupo, resolver_fichas
from app.jev_juicios import (
    clave_de,
    contrato_por_defecto,
    leer_api_key,
    pedir_juicio,
)
from app.jev_lectura import (
    REGLA_VERSION,
    ActaDeGrupo,
    ActaDeListado,
    Ajustes,
    Apagado,
    ClaveBusqueda,
    Economia,
    Gasto,
    Historial,
    NoEvaluada,
    PropuestaEnVeto,
    Relevancia,
    Roster,
    RosterProbado,
    Unidad,
    VentaEnOtroGrupo,
    ajustes_desde_settings,
    anunciados_hoy,
    leer,
    planear,
    probar_roster,
)
from app.jev_libro import Libro, SinCupo, _exceso_contexto, exito_global, intenciones_del_dia
from app.optimizer import windows
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub

_FALLOS_SEGUIDOS_MAX = 5
_HORAS_VIGENCIA = 36

MotivoCierre = Literal["apagado", "ocupado", "completa", "tope", "proveedor_caido", "sin_api_key"]


@dataclass(frozen=True)
class CierreCorrida:
    corrida_id: UUID | None
    motivo: MotivoCierre
    unidades: int
    llamadas: int
    fallos: int
    senales_nuevas: int
    avisos_enviados: int


@dataclass(frozen=True)
class SaludJev:
    interruptor: Ajustes | Apagado
    ultima_corrida: CierreCorrida | None
    ultima_inicio: datetime | None
    ultima_sin_cierre: bool
    llamadas_hoy: int
    pendientes_de_jev: int
    grupos_con_gasto: int
    grupos_probados: int
    motivos_sin_probar: dict
    ajena_impedida_por_abstencion: int
    avisos_sin_entrega: int
    fichas_por_vencer_14d: int

    def como_dict(self) -> dict:
        """El bloque `jev` de /salud, con datos planos."""
        encendido = isinstance(self.interruptor, Ajustes)
        ultima = self.ultima_corrida
        return {
            "interruptor": {
                "encendido": encendido,
                "tope_diario": self.interruptor.tope_diario if encendido else None,
                "min_clics": self.interruptor.min_clics if encendido else None,
                "avisos": self.interruptor.avisos if encendido else None,
                "motivo_apagado": None if encendido else self.interruptor.motivo,
            },
            "ultima_corrida": None
            if ultima is None
            else {
                "corrida_id": str(ultima.corrida_id) if ultima.corrida_id else None,
                "motivo": ultima.motivo,
                "unidades": ultima.unidades,
                "llamadas": ultima.llamadas,
                "fallos": ultima.fallos,
                "senales": ultima.senales_nuevas,
                "avisos": ultima.avisos_enviados,
            },
            "ultima_inicio": self.ultima_inicio.isoformat() if self.ultima_inicio else None,
            "ultima_sin_cierre": self.ultima_sin_cierre,
            "llamadas_hoy": self.llamadas_hoy,
            "pendientes_de_jev": self.pendientes_de_jev,
            "grupos_con_gasto": self.grupos_con_gasto,
            "grupos_probados": self.grupos_probados,
            "motivos_sin_probar": dict(self.motivos_sin_probar),
            "ajena_impedida_por_abstencion": self.ajena_impedida_por_abstencion,
            "avisos_sin_entrega": self.avisos_sin_entrega,
            "fichas_por_vencer_14d": self.fichas_por_vencer_14d,
        }


@dataclass
class _Estado:
    libro: Libro
    lote: UUID
    tope: int
    motivo_base: str | None
    sin_cupo: bool = False
    proveedor_caido: bool = False
    fallos_seguidos: int = 0
    memo_fallos: dict = field(default_factory=dict)


_SQL_HISTORIAL = (
    "SELECT min(metric_date), max(metric_date),"
    " CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks)::bigint END,"
    " COALESCE(sum(orders) FILTER (WHERE orders IS NOT NULL), 0)::bigint,"
    " count(*) FILTER (WHERE orders IS NULL), max(observed_at) FROM ("
    + windows._SQL_COLAPSO_TERMINOS.format(where="WHERE ad_entity_id = %s AND search_term = %s")
    + ") h"
)


def _ajustes(lector) -> Ajustes | Apagado:
    fila = lector.execute(
        "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return Apagado("sin config vigente") if fila is None else ajustes_desde_settings(*fila)


def _grupo_interno(lector, plataforma: str, externo: object) -> int | None:
    fila = lector.execute(
        "SELECT id FROM ad_entity WHERE platform = %s AND kind = 'ad_group' AND external_id = %s",
        (plataforma, str(externo)),
    ).fetchone()
    return fila[0] if fila is not None else None


def _propuestas(lector) -> tuple[tuple, tuple]:
    propuestas = []
    ilegibles = []
    filas = lector.execute(
        "SELECT q.id, q.decision_id, q.kind::text, q.modo, q.vence_el, q.ad_entity_id,"
        " q.search_term, q.platform::text, d.inputs"
        " FROM apply_queue q JOIN decision d ON d.id = q.decision_id"
        " WHERE q.kind IN ('negative', 'harvest') AND q.estado IN ('pending_veto', 'released')"
        " AND q.platform IN ('amazon_mx', 'amazon_us') ORDER BY q.vence_el, q.id"
    ).fetchall()
    for cola, decision, kind, modo, vence, grupo, termino, plataforma, inputs in filas:
        origen = ClaveBusqueda(plataforma, grupo, termino)
        destino = None
        ilegible = False
        if kind == "harvest":
            cosecha = ((inputs or {}).get("goal") or {}).get("harvest") or {}
            externo = cosecha.get("ad_group_id")
            interno = _grupo_interno(lector, plataforma, externo) if externo is not None else None
            if interno is None:
                ilegible = True
                ilegibles.append(cola)
            else:
                destino = ClaveBusqueda(plataforma, interno, termino)
        propuestas.append(
            PropuestaEnVeto(cola, decision, kind, modo, origen, destino, ilegible, vence)
        )
    return tuple(propuestas), tuple(ilegibles)


def _aplicados(lector) -> frozenset:
    filas = lector.execute(
        "SELECT platform::text, ad_entity_id, search_term FROM apply_queue"
        " WHERE estado = 'applied' AND kind IN ('negative', 'harvest')"
        " AND platform IN ('amazon_mx', 'amazon_us')"
    ).fetchall()
    return frozenset(ClaveBusqueda(*fila) for fila in filas)


def _vigentes(lector) -> frozenset:
    filas = lector.execute(
        "SELECT plataforma::text, ad_group_id, termino FROM jev_senal_vigente WHERE vigente"
    ).fetchall()
    return frozenset(ClaveBusqueda(*fila) for fila in filas)


def _historial(lector, clave: ClaveBusqueda):
    fila = lector.execute(_SQL_HISTORIAL, (clave.ad_group_id, clave.termino)).fetchone()
    if fila[0] is None:
        return None, None
    return Historial(*fila[:5]), fila[5]


def _gasto_de(ventana, agregado) -> Gasto:
    return Gasto(
        ventana.window_start, ventana.window_end, agregado.clicks, agregado.cost, agregado.orders
    )


def _busca(ventana, termino: str):
    if ventana is None or ventana.window_start is None:
        return None
    for agregado in ventana.terminos:
        if agregado.search_term == termino:
            return agregado
    return None


def _economia_de(lector, clave: ClaveBusqueda, ventanas: dict):
    vistos: list = []
    propio = _busca(ventanas.get((clave.plataforma, clave.ad_group_id)), clave.termino)
    aqui = None
    if propio is not None:
        aqui = _gasto_de(ventanas[(clave.plataforma, clave.ad_group_id)], propio)
        vistos.append(propio.observed_at_max)
    otros_que_venden = []
    otros_sin_venta = 0
    otros_sin_dato = 0
    for (plataforma, grupo), ajeno in ventanas.items():
        if plataforma != clave.plataforma or grupo == clave.ad_group_id:
            continue
        agregado = _busca(ajeno, clave.termino)
        if agregado is None:
            continue
        vistos.append(agregado.observed_at_max)
        if agregado.orders is None:
            otros_sin_dato += 1
        elif agregado.orders > 0:
            otros_que_venden.append(VentaEnOtroGrupo(grupo, _gasto_de(ajeno, agregado)))
        else:
            otros_sin_venta += 1
    historial, obs_historial = _historial(lector, clave)
    vistos.append(obs_historial)
    return Economia(
        PLATAFORMAS_MONEDA[clave.plataforma],
        aqui,
        tuple(otros_que_venden),
        otros_sin_venta,
        otros_sin_dato,
        historial,
        max((v for v in vistos if v is not None), default=None),
    )


def _economia(lector, ahora: datetime, propuestas: tuple, vigentes: frozenset) -> dict:
    ventanas = {}
    for plataforma in PLATAFORMAS_MONEDA:
        grupos = lector.execute(
            "SELECT DISTINCT ad_entity_id FROM search_term_observation WHERE platform = %s",
            (plataforma,),
        ).fetchall()
        for (grupo,) in grupos:
            ventanas[(plataforma, grupo)] = windows.terminos_cortes(lector, grupo, ahora)
    claves = set(vigentes)
    for (plataforma, grupo), ventana in ventanas.items():
        for agregado in ventana.terminos:
            claves.add(ClaveBusqueda(plataforma, grupo, agregado.search_term))
    for propuesta in propuestas:
        claves.add(propuesta.origen)
        if propuesta.destino is not None:
            claves.add(propuesta.destino)
    return {clave: _economia_de(lector, clave, ventanas) for clave in claves}


def _leer_mundo(lector, ahora: datetime) -> tuple:
    """Ajustes y mundo en una REPEATABLE READ (el SET va primero)."""
    with lector.transaction():
        lector.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        ajustes = _ajustes(lector)
        propuestas, ilegibles = _propuestas(lector)
        aplicados = _aplicados(lector)
        vigentes = _vigentes(lector)
        economia = _economia(lector, ahora, propuestas, vigentes)
    return ajustes, propuestas, economia, aplicados, vigentes, ilegibles


def _acta_de(lector, plataforma: str, grupo: int) -> ActaDeListado | None:
    fila = lector.execute(
        "SELECT r.id, r.finished_at, p.ad_groups_declarados, p.ad_groups_recibidos,"
        " p.product_ads_declarados, p.product_ads_recibidos, p.product_ads_sin_grupo,"
        " g.anuncios_vivos, g.huella_vivos, g.descartados FROM ingest_run r"
        " JOIN ads_listado_plataforma p ON p.ingest_run_id = r.id AND p.platform = %s"
        " LEFT JOIN ads_listado_grupo g ON g.ingest_run_id = r.id AND g.ad_group_id = %s"
        " WHERE r.source = 'amazon_ads_structure_v2' AND r.ok AND r.finished_at IS NOT NULL"
        " ORDER BY r.finished_at DESC, r.id DESC LIMIT 1",
        (plataforma, grupo),
    ).fetchone()
    if fila is None:
        return None
    del_grupo = None
    if fila[7] is not None:
        del_grupo = ActaDeGrupo(vivos=fila[7], huella=fila[8], descartados=fila[9])
    return ActaDeListado(*fila[:7], del_grupo)


def _roster(lector, escritor, clave: ClaveBusqueda, ahora: datetime, *, aplicar: bool) -> Roster:
    censo = censo_grupo(lector, plataforma=clave.plataforma, ad_group_id=clave.ad_group_id)
    con_fichas, fichas = resolver_fichas(lector, censo, plataforma=clave.plataforma, ahora=ahora)
    recortado = anunciados_hoy(con_fichas)
    acta = _acta_de(lector, clave.plataforma, clave.ad_group_id)
    filas = lector.execute(
        "SELECT e.external_id FROM ad_entity e JOIN ad_entity_state s ON s.ad_entity_id = e.id"
        " WHERE e.parent_id = %s AND e.kind = 'product_ad' AND s.status IN ('ENABLED', 'PAUSED')",
        (clave.ad_group_id,),
    ).fetchall()
    prueba = probar_roster(
        recortado,
        acta,
        huella_en_base=huella_anuncios([fila[0] for fila in filas]),
        ahora=ahora,
        max_edad=windows.MAX_EDAD_SYNC,
    )
    if isinstance(prueba, RosterProbado):
        recortado = replace(recortado, exhaustivo=True)
    miembros = {
        "censo": _identidad_del_censo(recortado),
        "fichas": [
            str(m.ficha_version_id) if m.ficha_version_id is not None else None
            for m in recortado.miembros
        ],
    }
    sha = hashlib.sha256(_canonico(miembros).encode("utf-8")).hexdigest()
    if aplicar:
        escritor.execute(
            "INSERT INTO jev_roster (sha256, plataforma, ad_group_id, miembros,"
            " ficha_version_ids) VALUES (%s, %s, %s, %s::jsonb, %s)"
            " ON CONFLICT (sha256) DO NOTHING",
            (
                sha,
                clave.plataforma,
                clave.ad_group_id,
                _canonico(miembros),
                [m.ficha_version_id for m in recortado.miembros if m.ficha_version_id],
            ),
        )
    return Roster(clave.plataforma, clave.ad_group_id, recortado, fichas, prueba, sha)


def _razon_sin_pago(estado: _Estado) -> str | None:
    if estado.motivo_base is not None:
        return estado.motivo_base
    if estado.proveedor_caido:
        return "proveedor_caido"
    if estado.sin_cupo:
        return "sin_cupo"
    return None


def _pares_de(estado: _Estado, unidad: Unidad, roster: Roster, contrato) -> tuple:
    termino = unidad.clave.termino
    if es_asin_like(termino):
        return (NoAplicaTexto(motivo="asin_like"),)
    pares = []
    for miembro, ficha in _miembros_pagables(roster):
        if ficha is None:
            pares.append(FichaFaltante(producto_id=miembro.producto_id))
            continue
        if _razon_sin_pago(estado) is not None:
            continue
        clave = clave_de(termino, ficha, contrato)
        if clave in estado.memo_fallos:
            pares.append(estado.memo_fallos[clave])
            continue
        resultado = estado.libro.juicio(termino, ficha, lote=estado.lote, tope_diario=estado.tope)
        if isinstance(resultado, SinCupo):
            estado.sin_cupo = True
            continue
        if isinstance(resultado, FalloProveedor):
            estado.memo_fallos[clave] = resultado
            estado.fallos_seguidos += 1
            if estado.fallos_seguidos >= _FALLOS_SEGUIDOS_MAX:
                estado.proveedor_caido = True
            pares.append(resultado)
            continue
        estado.fallos_seguidos = 0
        pares.append(resultado)
    return tuple(pares)


def _relevancia_de(roster: Roster, pares: tuple, estado: _Estado) -> Relevancia:
    censo = roster.censo
    evaluados = [p for p in pares if isinstance(p, Juicio)]
    if pares or not censo.miembros:
        conjunto = componer(censo, pares)
    else:
        conjunto = NoEvaluada(motivo=_razon_sin_pago(estado) or "sin_cupo")
    por_ficha = {m.ficha_version_id: m for m in censo.miembros if m.ficha_version_id}
    productos_ok = tuple(
        por_ficha[j.clave.ficha_version_id].producto_id
        for j in evaluados
        if j.relacion == "satisface"
    )
    return Relevancia(
        conjunto,
        len(productos_ok),
        len(evaluados),
        sum(1 for m in censo.miembros if not _solo_no_activo(m.estados)),
        productos_ok,
        tuple(j.intento_id for j in evaluados),
    )


def _texto_relevancia(relevancia: Relevancia) -> tuple[str, list]:
    conjunto = relevancia.conjunto
    if isinstance(conjunto, HayCompatible):
        return "corresponde", []
    if isinstance(conjunto, NingunoCompatible):
        return "ajena", []
    if isinstance(conjunto, Indeterminado):
        return "sin_veredicto", sorted(conjunto.motivos)
    return "no_evaluada", [conjunto.motivo]


def _valida_hasta(roster: Roster, ahora: datetime) -> datetime:
    tope = ahora + timedelta(hours=_HORAS_VIGENCIA)
    vencimientos = [
        roster.fichas[m.ficha_version_id].revisar_antes_de
        for m in roster.censo.miembros
        if m.ficha_version_id in roster.fichas
    ]
    return min([tope, *vencimientos])


def _prueba_json(roster: Roster) -> dict:
    prueba = roster.prueba
    if isinstance(prueba, RosterProbado):
        return {
            "probado": True,
            "ingest_run_id": prueba.ingest_run_id,
            "listado_de": prueba.listado_de.isoformat(),
            "anuncios_vivos": prueba.anuncios_vivos,
        }
    return {"probado": False, "motivos": sorted(prueba.motivos)}


def _dinero_str(valor) -> str | None:
    return None if valor is None else str(valor)


def _gasto_json(gasto: Gasto) -> dict:
    return {
        "desde": gasto.desde.isoformat(),
        "hasta": gasto.hasta.isoformat(),
        "clics": gasto.clics,
        "gasto": _dinero_str(gasto.gasto),
        "ordenes": gasto.ordenes,
    }


def _insumos(
    unidad: Unidad,
    roster: Roster,
    relevancia: Relevancia,
    contrato_sha: str,
    ahora: datetime,
) -> tuple[str, dict]:
    eco = unidad.economia
    aqui = eco.aqui
    historial = eco.historial
    texto, motivos_jev = _texto_relevancia(relevancia)
    insumos = {
        "regla": REGLA_VERSION,
        "contrato": contrato_sha,
        "roster": roster.sha256,
        "probado": isinstance(roster.prueba, RosterProbado),
        "ventana": None if aqui is None else [aqui.desde.isoformat(), aqui.hasta.isoformat()],
        "aqui": None
        if aqui is None
        else {"clics": aqui.clics, "gasto": _dinero_str(aqui.gasto), "ordenes": aqui.ordenes},
        "otros": [
            {"ad_group_id": v.ad_group_id, **_gasto_json(v.en_ventana)}
            for v in eco.otros_que_venden
        ],
        "otros_sin_dato": eco.otros_sin_dato,
        "historial": None
        if historial is None
        else {
            "desde": historial.desde.isoformat(),
            "hasta": historial.hasta.isoformat(),
            "clics": historial.clics,
            "ordenes_conocidas": historial.ordenes_conocidas,
            "dias_sin_dato": historial.dias_sin_dato,
        },
        "relevancia": texto,
        "juicios": [str(j) for j in relevancia.juicio_ids],
        "motivos_jev": motivos_jev,
        "productos_ok": list(relevancia.productos_ok),
        "evaluados": relevancia.evaluados,
        "miembros": relevancia.miembros,
        "fecha": ahora.date().isoformat(),
    }
    return hashlib.sha256(_canonico(insumos).encode("utf-8")).hexdigest(), insumos


_SQL_INSERT_SENAL = (
    "INSERT INTO jev_senal (id, lote_id, plataforma, ad_group_id, termino, termino_sha256,"
    " insumos_sha256, regla_version, roster_sha256, roster_probado, roster_prueba,"
    " contrato_sha256, relevancia, motivos_jev, productos_ok, evaluados, miembros,"
    " juicio_ids, ventana_inicio, ventana_fin, datos_hasta, moneda, clics, gasto,"
    " ordenes, otros_que_venden, ordenes_otros, otros_sin_dato, historial,"
    " ordenes_historial, lectura, motivos_lectura, valida_hasta)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s,"
    " %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s)"
    " ON CONFLICT (ad_group_id, termino_sha256, insumos_sha256) DO NOTHING"
)


def _sellar_unidad(
    escritor, estado: _Estado, unidad: Unidad, roster: Roster, contrato, ahora: datetime
) -> bool:
    pares = _pares_de(estado, unidad, roster, contrato)
    relevancia = _relevancia_de(roster, pares, estado)
    lectura, motivos = leer(unidad.economia, relevancia)
    texto, motivos_jev = _texto_relevancia(relevancia)
    insumos_sha, insumos = _insumos(unidad, roster, relevancia, contrato.sha256(), ahora)
    eco = unidad.economia
    aqui = eco.aqui
    historial = eco.historial
    ordenes_otros = None
    if eco.otros_sin_dato == 0:
        ordenes_otros = sum(v.en_ventana.ordenes for v in eco.otros_que_venden)
    cursor = escritor.execute(
        _SQL_INSERT_SENAL,
        (
            uuid4(),
            estado.lote,
            unidad.clave.plataforma,
            unidad.clave.ad_group_id,
            unidad.clave.termino,
            hashlib.sha256(unidad.clave.termino.encode("utf-8")).hexdigest(),
            insumos_sha,
            REGLA_VERSION,
            roster.sha256,
            isinstance(roster.prueba, RosterProbado),
            _canonico(_prueba_json(roster)),
            contrato.sha256(),
            texto,
            motivos_jev,
            list(relevancia.productos_ok),
            relevancia.evaluados,
            relevancia.miembros,
            list(relevancia.juicio_ids),
            aqui.desde if aqui else None,
            aqui.hasta if aqui else None,
            eco.datos_hasta,
            eco.moneda,
            aqui.clics if aqui else None,
            aqui.gasto if aqui else None,
            aqui.ordenes if aqui else None,
            _canonico(insumos["otros"]),
            ordenes_otros,
            eco.otros_sin_dato,
            _canonico(insumos["historial"]) if insumos["historial"] is not None else None,
            historial.ordenes_conocidas if historial else None,
            lectura,
            sorted(motivos),
            _valida_hasta(roster, ahora),
        ),
    )
    return cursor.rowcount == 1


def _abrir_lote(escritor, plan: tuple, contrato, ahora: datetime) -> UUID:
    lote = uuid4()
    tramos = Counter(unidad.tramo for unidad in plan)
    escritor.execute(
        "INSERT INTO jev_revision (solicitud, sujeto_tipo, censos, contrato, captured_at)"
        " VALUES (%s, 'lote', %s::jsonb, %s::jsonb, %s)",
        (
            lote,
            _canonico({"unidades": len(plan), "tramos": tramos}),
            _canonico(
                {
                    "modelo": contrato.modelo,
                    "opciones": list(contrato.opciones),
                    "sha256": contrato.sha256(),
                    "version": contrato.version,
                }
            ),
            ahora,
        ),
    )
    escritor.execute("INSERT INTO jev_corrida (lote_id, evento) VALUES (%s, 'inicio')", (lote,))
    return lote


def _rosteres(lector, escritor, plan: tuple, ahora: datetime, *, aplicar: bool) -> dict:
    rosters = {}
    for unidad in plan:
        llave = (unidad.clave.plataforma, unidad.clave.ad_group_id)
        if llave not in rosters:
            rosters[llave] = _roster(lector, escritor, unidad.clave, ahora, aplicar=aplicar)
    return rosters


def _miembros_pagables(roster: Roster):
    for miembro in roster.censo.miembros:
        if _solo_no_activo(miembro.estados):
            continue
        ficha = roster.fichas.get(miembro.ficha_version_id) if miembro.ficha_version_id else None
        yield miembro, ficha


def _seco(
    lector, plan: tuple, ajustes: Ajustes, contrato, ahora: datetime, api_key: str
) -> CierreCorrida:
    llamadas_hoy = intenciones_del_dia(lector, datetime.now(UTC))
    restantes = max(0, ajustes.tope_diario - llamadas_hoy)
    pagaria = 0
    if api_key and restantes:
        rosters = _rosteres(lector, None, plan, ahora, aplicar=False)
        vistos = set()
        for unidad in plan:
            termino = unidad.clave.termino
            if es_asin_like(termino):
                continue
            roster = rosters[(unidad.clave.plataforma, unidad.clave.ad_group_id)]
            for _, ficha in _miembros_pagables(roster):
                if ficha is None:
                    continue
                clave = clave_de(termino, ficha, contrato)
                if clave not in vistos:
                    vistos.add(clave)
                    if (
                        exito_global(lector, clave) is None
                        and _exceso_contexto(termino, ficha, contrato) is None
                    ):
                        pagaria += 1
        pagaria = min(pagaria, restantes)
    tramos = Counter(unidad.tramo for unidad in plan)
    detalle = " ".join(
        f"{tramo}={tramos.get(tramo, 0)}" for tramo in ("propuesta", "desempate", "resto")
    )
    print(
        f"jev-senales seco: unidades={len(plan)} {detalle} pagaria={pagaria}"
        f" tope={ajustes.tope_diario} llamadas_hoy={llamadas_hoy}"
    )
    return CierreCorrida(None, "completa", len(plan), pagaria, 0, 0, 0)


def _aplicar(
    lector,
    escritor,
    plan: tuple,
    ilegibles: tuple,
    ajustes: Ajustes,
    contrato,
    ahora: datetime,
    pedir,
    api_key: str,
) -> CierreCorrida:
    pedir_real = pedir
    if pedir_real is None:

        def pedir_real(termino, ficha):
            return pedir_juicio(termino, ficha, contrato, api_key=api_key)

    libro = Libro(escritor, pedir=pedir_real, contrato=contrato, ahora=lambda: datetime.now(UTC))
    lote = _abrir_lote(escritor, plan, contrato, ahora)
    motivo_base = None if api_key else "sin_api_key"
    if api_key and ajustes.tope_diario == 0:
        motivo_base = "tope_cero"
    estado = _Estado(libro=libro, lote=lote, tope=ajustes.tope_diario, motivo_base=motivo_base)
    rosters = _rosteres(lector, escritor, plan, ahora, aplicar=True)
    senales = 0
    for unidad in plan:
        roster = rosters[(unidad.clave.plataforma, unidad.clave.ad_group_id)]
        if _sellar_unidad(escritor, estado, unidad, roster, contrato, ahora):
            senales += 1
    llamadas = escritor.execute(
        "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s AND tipo = 'intencion'",
        (lote,),
    ).fetchone()[0]
    fallos = escritor.execute(
        "SELECT count(*) FROM jev_par_evento"
        " WHERE revision_id = %s AND tipo = 'resultado' AND error IS NOT NULL",
        (lote,),
    ).fetchone()[0]
    if estado.proveedor_caido:
        motivo: MotivoCierre = "proveedor_caido"
    elif estado.sin_cupo or motivo_base == "tope_cero":
        motivo = "tope"
    elif motivo_base == "sin_api_key":
        motivo = "sin_api_key"
    else:
        motivo = "completa"
    resumen = {
        "motivo": motivo,
        "unidades": len(plan),
        "llamadas": llamadas,
        "fallos": fallos,
        "senales_nuevas": senales,
        "avisos_enviados": 0,
        "propuestas_ilegibles": list(ilegibles),
    }
    escritor.execute(
        "INSERT INTO jev_corrida (lote_id, evento, cierre, resumen)"
        " VALUES (%s, 'fin', %s, %s::jsonb)",
        (lote, motivo, _canonico(resumen)),
    )
    print(
        f"jev-senales motivo={motivo} unidades={len(plan)} llamadas={llamadas}"
        f" fallos={fallos} senales={senales} avisos=0"
    )
    return CierreCorrida(lote, motivo, len(plan), llamadas, fallos, senales, 0)


def correr(
    lector,
    escritor,
    *,
    ahora: datetime,
    aplicar: bool = False,
    pedir=None,
    api_key: str = "",
) -> CierreCorrida:
    """La corrida: ajustes, candado, foto del mundo, plan, lote y sellado por
    unidad. Sin `--aplicar` no escribe ni llama; apagado u ocupado salen con
    0 sin tocar nada."""
    if aplicar:
        if escritor is None:
            raise ValueError("aplicar exige escritor")
        tomado = escritor.execute(
            "SELECT pg_try_advisory_lock(hashtext('jev:senales'))"
        ).fetchone()[0]
        if not tomado:
            print("jev-senales ocupado: otra corrida tiene el candado")
            return CierreCorrida(None, "ocupado", 0, 0, 0, 0, 0)
        try:
            ajustes, propuestas, economia, aplicados, vigentes, ilegibles = _leer_mundo(
                lector, ahora
            )
            if isinstance(ajustes, Apagado):
                print(f"jev-senales apagado: {ajustes.motivo}")
                return CierreCorrida(None, "apagado", 0, 0, 0, 0, 0)
            contrato = contrato_por_defecto()
            plan = planear(propuestas, economia, vigentes, aplicados, ajustes)
            return _aplicar(
                lector, escritor, plan, ilegibles, ajustes, contrato, ahora, pedir, api_key
            )
        finally:
            escritor.execute("SELECT pg_advisory_unlock(hashtext('jev:senales'))").fetchone()
    ajustes, propuestas, economia, aplicados, vigentes, _ = _leer_mundo(lector, ahora)
    if isinstance(ajustes, Apagado):
        print(f"jev-senales apagado: {ajustes.motivo}")
        return CierreCorrida(None, "apagado", 0, 0, 0, 0, 0)
    contrato = contrato_por_defecto()
    plan = planear(propuestas, economia, vigentes, aplicados, ajustes)
    return _seco(lector, plan, ajustes, contrato, ahora, api_key)


def main(argv: list[str]) -> int:
    """`python -m app.cli jev-senales`: 0 corrio, apagado u ocupado; 1 fallo;
    2 falta DSN. El seco solo necesita ORBIT_DSN_READ."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli jev-senales",
        description="Sella la senal Jev por busqueda-en-grupo (seco salvo --aplicar).",
    )
    parser.add_argument("--aplicar", action="store_true", help="escribe senales (sin esto es seco)")
    args = parser.parse_args(argv)
    dsn_read = os.environ.get("ORBIT_DSN_READ")
    if not dsn_read:
        print("jev-senales: falta ORBIT_DSN_READ", file=sys.stderr)
        return 2
    dsn_jev = os.environ.get("ORBIT_DSN_JEV")
    if args.aplicar and not dsn_jev:
        print("jev-senales: --aplicar exige ORBIT_DSN_JEV", file=sys.stderr)
        return 2
    lector = None
    escritor = None
    try:
        lector = connect(dsn_read)
        if args.aplicar:
            escritor = connect(dsn_jev, autocommit=True)
        correr(
            lector,
            escritor,
            ahora=datetime.now(UTC),
            aplicar=args.aplicar,
            api_key=leer_api_key(),
        )
    except Exception as exc:
        print(f"jev-senales fallo: {type(exc).__name__}: {scrub(str(exc))}", file=sys.stderr)
        return 1
    finally:
        if escritor is not None:
            escritor.close()
        if lector is not None:
            lector.close()
    return 0


def salud(conn, *, ahora: datetime) -> SaludJev:
    """Bloque `jev` de /salud, con consultas baratas. Revienta si falta una
    tabla (base sin S.3): `_jev_de` lo convierte en None."""

    def cuenta(sql, *params):
        return conn.execute(sql, params or None).fetchone()[0]

    interruptor = _ajustes(conn)
    fin = conn.execute(
        "SELECT lote_id, cierre, resumen, at FROM jev_corrida"
        " WHERE evento = 'fin' ORDER BY at DESC LIMIT 1"
    ).fetchone()
    ultima = None
    fin_at = None
    if fin is not None:
        fin_at = fin[3]
        resumen = fin[2] or {}
        nums = {
            k: resumen.get(k, 0)
            for k in ("unidades", "llamadas", "fallos", "senales_nuevas", "avisos_enviados")
        }
        ultima = CierreCorrida(fin[0], fin[1], **nums)
    inicio = cuenta("SELECT max(at) FROM jev_corrida WHERE evento = 'inicio'")
    grupos = conn.execute(
        "SELECT ad_group_id, roster_probado, roster_prueba FROM (SELECT DISTINCT ON"
        " (ad_group_id) ad_group_id, roster_probado, roster_prueba FROM jev_senal"
        " ORDER BY ad_group_id, created_at DESC, id DESC) u"
    ).fetchall()
    probados = sum(1 for _, es_probado, _ in grupos if es_probado)
    motivos = Counter(
        motivo
        for _, es_probado, prueba in grupos
        if not es_probado
        for motivo in (prueba or {}).get("motivos", [])
    )
    return SaludJev(
        interruptor,
        ultima,
        inicio,
        inicio is not None and (fin_at is None or inicio > fin_at),
        intenciones_del_dia(conn, ahora),
        cuenta(
            "SELECT count(*) FROM jev_senal_vigente v JOIN jev_senal s ON s.id = v.senal_id"
            " WHERE v.vigente AND s.relevancia = 'no_evaluada'"
        ),
        cuenta("SELECT count(DISTINCT ad_group_id) FROM jev_senal WHERE gasto > 0"),
        probados,
        dict(motivos),
        cuenta(
            "SELECT count(*) FROM (SELECT DISTINCT ON (ad_group_id, termino_sha256) lectura,"
            " motivos_jev, productos_ok, evaluados FROM jev_senal"
            " ORDER BY ad_group_id, termino_sha256, created_at DESC, id DESC) u"
            " WHERE lectura = 'sin_lectura'"
            " AND motivos_jev && ARRAY['juicio_insuficiente', 'fallo_proveedor']"
            " AND cardinality(productos_ok) = 0 AND evaluados > 0"
        ),
        cuenta(
            "SELECT count(*) FROM jev_aviso a"
            " LEFT JOIN jev_aviso_entrega e ON e.aviso_id = a.id WHERE e.aviso_id IS NULL"
        ),
        cuenta(
            "SELECT count(*) FROM jev_ficha_version f"
            " LEFT JOIN jev_ficha_revocacion r ON r.ficha_version_id = f.id"
            " WHERE r.id IS NULL AND f.revisar_antes_de <= %s",
            ahora + timedelta(days=14),
        ),
    )
