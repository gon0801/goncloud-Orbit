"""El job jev-senales (JEV ADS 02, S.4): sella la senal por busqueda-en-grupo.

Lee con `lector` (READ), escribe solo `jev_*` con `escritor` (JEV, autocommit).
Apagado u ocupado: 0 sin escribir. Seco: plan y cuenta. Avisos solo si
`jev.avisos` es true: fila antes de enviar, entrega solo si el envio fue True.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
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
from app.jev_avisos import _avisos_de
from app.jev_catalogo import censo_grupo, resolver_fichas
from app.jev_consultas import (  # noqa: F401 - reexport contrato pruebas
    MOTIVOS_SIN_JUICIO,
    TEXTO_SIN_JUICIO,
    CierreCorrida,
    MotivoCierre,
    _ajustes,
    _grupo_interno,
    _propuestas,
)
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
    Relevancia,
    Roster,
    RosterProbado,
    Unidad,
    VentaEnOtroGrupo,
    anunciados_hoy,
    leer,
    planear,
    probar_roster,
)
from app.jev_libro import (
    Libro,
    SinCupo,
    _exceso_contexto,
    exito_global,
    fallo_tras_http,
    intenciones_del_dia,
)
from app.optimizer import windows
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub

_FALLOS_SEGUIDOS_MAX = 5
_HORAS_VIGENCIA = 36


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


_SQL_HISTORIALES_LOTE = (
    "WITH pares(ent_id, term) AS ("
    " SELECT DISTINCT ent_id, term"
    "   FROM unnest(%s::bigint[], %s::text[]) AS t(ent_id, term)"
    "), colapsado AS ("
    + windows._SQL_COLAPSO_TERMINOS.format(
        where=(
            "JOIN pares p ON p.ent_id = search_term_observation.ad_entity_id"
            " AND p.term = search_term_observation.search_term"
        )
    )
    + ") SELECT ad_entity_id, search_term, min(metric_date), max(metric_date),"
    " CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks)::bigint END,"
    " COALESCE(sum(orders) FILTER (WHERE orders IS NOT NULL), 0)::bigint,"
    " count(*) FILTER (WHERE orders IS NULL), max(observed_at)"
    " FROM colapsado"
    " GROUP BY ad_entity_id, search_term"
)


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


def _historiales_lote(lector, claves: set[ClaveBusqueda]) -> dict[tuple[int, str], tuple]:
    """Lee todos los historiales del conjunto en 1 consulta dentro de REPEATABLE READ."""
    pares = sorted({(c.ad_group_id, c.termino) for c in claves})
    if not pares:
        return {}
    grupos = [g for g, _ in pares]
    terminos = [t for _, t in pares]
    filas = lector.execute(_SQL_HISTORIALES_LOTE, (grupos, terminos)).fetchall()
    return {
        (fila[0], fila[1]): (Historial(*fila[2:7]), fila[7])
        for fila in filas
        if fila[2] is not None
    }


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


def _economia_de(
    clave: ClaveBusqueda,
    *,
    propios: dict,
    por_termino: dict,
    historiales: dict,
):
    vistos: list = []
    par_propio = propios.get((clave.plataforma, clave.ad_group_id, clave.termino))
    aqui = None
    if par_propio is not None:
        ventana_propia, propio = par_propio
        aqui = _gasto_de(ventana_propia, propio)
        vistos.append(propio.observed_at_max)
    otros_que_venden = []
    otros_sin_venta = 0
    otros_sin_dato = 0
    for grupo, ajeno, agregado in por_termino.get((clave.plataforma, clave.termino), ()):
        if grupo == clave.ad_group_id:
            continue
        vistos.append(agregado.observed_at_max)
        if agregado.orders is None:
            otros_sin_dato += 1
        elif agregado.orders > 0:
            otros_que_venden.append(VentaEnOtroGrupo(grupo, _gasto_de(ajeno, agregado)))
        else:
            otros_sin_venta += 1
    historial, obs_historial = historiales.get((clave.ad_group_id, clave.termino), (None, None))
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
    propios: dict[tuple[str, int, str], tuple] = {}
    por_termino: dict[tuple[str, str], list] = {}
    claves = set(vigentes)
    for (plataforma, grupo), ventana in ventanas.items():
        if ventana is None or ventana.window_start is None:
            continue
        for agregado in ventana.terminos:
            claves.add(ClaveBusqueda(plataforma, grupo, agregado.search_term))
            propios[(plataforma, grupo, agregado.search_term)] = (ventana, agregado)
            por_termino.setdefault((plataforma, agregado.search_term), []).append(
                (grupo, ventana, agregado)
            )
    for propuesta in propuestas:
        claves.add(propuesta.origen)
        if propuesta.destino is not None:
            claves.add(propuesta.destino)
    historiales = _historiales_lote(lector, claves)
    return {
        clave: _economia_de(
            clave, propios=propios, por_termino=por_termino, historiales=historiales
        )
        for clave in claves
    }


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
        razon = _razon_sin_pago(estado)
        if razon is not None:
            if razon in ("sin_cupo", "proveedor_caido"):
                guardado = estado.libro.guardado(termino, ficha)
                if guardado is not None:
                    pares.append(guardado)
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
            if fallo_tras_http(resultado):
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
    llamadas_hoy = intenciones_del_dia(lector, ahora)
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
    propuestas: tuple = (),
    avisar=None,
) -> CierreCorrida:
    pedir_real = pedir
    if pedir_real is None:

        def pedir_real(termino, ficha):
            return pedir_juicio(termino, ficha, contrato, api_key=api_key)

    # Libro cuenta intenciones contra `jev_par_evento.created_at` (DEFAULT now()
    # en Postgres): debe usar el mismo reloj UTC del servidor que el INSERT.
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
    avisos_error: Exception | None = None
    try:
        avisos = _avisos_de(ajustes, avisar, lector, escritor, lote, propuestas)
    except Exception as exc:  # las senales ya estan escritas: el `fin` va antes de fallar
        avisos_error = exc
        avisos = escritor.execute(
            "SELECT count(*) FROM jev_aviso_entrega WHERE entregado_at >="
            " (SELECT at FROM jev_corrida WHERE lote_id = %s AND evento = 'inicio')",
            (lote,),
        ).fetchone()[0]
    resumen = {
        "motivo": motivo,
        "unidades": len(plan),
        "llamadas": llamadas,
        "fallos": fallos,
        "senales_nuevas": senales,
        "avisos_enviados": avisos,
        "propuestas_ilegibles": list(ilegibles),
    }
    if avisos_error is not None:
        resumen["avisos_error"] = type(avisos_error).__name__
    escritor.execute(
        "INSERT INTO jev_corrida (lote_id, evento, cierre, resumen)"
        " VALUES (%s, 'fin', %s, %s::jsonb)",
        (lote, motivo, _canonico(resumen)),
    )
    if avisos_error is not None:
        raise avisos_error
    print(
        f"jev-senales motivo={motivo} unidades={len(plan)} llamadas={llamadas}"
        f" fallos={fallos} senales={senales} avisos={avisos}"
    )
    return CierreCorrida(lote, motivo, len(plan), llamadas, fallos, senales, avisos)


def correr(
    lector,
    escritor,
    *,
    ahora: datetime,
    aplicar: bool = False,
    pedir=None,
    api_key: str = "",
    avisar=None,
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
                lector,
                escritor,
                plan,
                ilegibles,
                ajustes,
                contrato,
                ahora,
                pedir,
                api_key,
                propuestas,
                avisar,
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
    """`python -m app.cli jev-senales`: 0 corrio, apagado u ocupado; 1 fallo o
    no juzgo ninguna busqueda (sin llave, proveedor caido o tope; ARREGLOS 03
    D-J: cron-avisa avisa); 2 falta DSN. Sin metricas de ayer sale 75 mientras
    queda horario hoy y 1 desde el fin de intentos. El seco solo necesita
    ORBIT_DSN_READ."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli jev-senales",
        description="Sella la senal Jev por busqueda-en-grupo (seco salvo --aplicar).",
    )
    parser.add_argument("--aplicar", action="store_true", help="escribe senales (sin esto es seco)")
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="corre sin la guarda de metricas de ayer (uso manual)",
    )
    args = parser.parse_args(argv)
    dsn_read = os.environ.get("ORBIT_DSN_READ")
    if not dsn_read:
        print("jev-senales: falta ORBIT_DSN_READ", file=sys.stderr)
        return 2
    dsn_jev = os.environ.get("ORBIT_DSN_JEV")
    if args.aplicar and not dsn_jev:
        print("jev-senales: --aplicar exige ORBIT_DSN_JEV", file=sys.stderr)
        return 2
    from app import notifica

    lector = None
    escritor = None
    try:
        lector = connect(dsn_read)
        # Guarda de ingesta-01 A.3: las senales se sellan sobre
        # search_term_observation (la escribe la corrida de metricas de hoy):
        # sin esas filas de D-1 no se abre nada, ni siquiera el candado.
        from app import metricas_listas

        if not metricas_listas.guarda(lector, ("amazon_us", "amazon_mx"), forzar=args.forzar):
            return metricas_listas.salida_sin_metricas("jev-senales")
        if args.aplicar:
            escritor = connect(dsn_jev, autocommit=True)
        cierre = correr(
            lector,
            escritor,
            ahora=datetime.now(UTC),
            aplicar=args.aplicar,
            api_key=leer_api_key(),
            avisar=notifica.envia_aviso,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {scrub(str(exc))}"
        print(f"jev-senales fallo: {error}", file=sys.stderr)
        notifica.envia_aviso(
            "[Orbit] Gasto sin venta no se calculó: la revisión de Jev falló."
            f" La pantalla sigue con el cálculo anterior.\nError: {error}"
        )
        return 1
    finally:
        if escritor is not None:
            escritor.close()
        if lector is not None:
            lector.close()
    # ARREGLOS 03 D-J (seccion «OK»): una corrida que no juzgo ninguna
    # busqueda no cierra como exito; cron-avisa manda este stderr a Telegram.
    if cierre.motivo in MOTIVOS_SIN_JUICIO:
        print(
            f"jev-senales: no se juzgó ninguna búsqueda: {TEXTO_SIN_JUICIO[cierre.motivo]}."
            " La pantalla Gasto sin venta sigue con las lecturas anteriores.",
            file=sys.stderr,
        )
        return 1
    return 0
