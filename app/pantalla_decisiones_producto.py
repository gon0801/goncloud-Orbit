"""Decisiones por producto en la pantalla de productos (AUTO-03, E.1).

Lector puro, solo SELECT: la ultima evaluacion de cada producto en el ultimo
ciclo done de la plataforma (evaluacion_producto_ciclo), sin llamar al
resolutor. Muestra margen y ventana, meta, grupos aptos con el target y la
procedencia que target_acos_ciclo congelo EN ESE MISMO ciclo, el veredicto
actual frente al veredicto con meta (resultado.sombra_hojas del ciclo) y los
motivos con las etiquetas de app/optimizer/productos.py. Ciclo, target y
veredicto se corresponden: una hoja sin fila de target en ese ciclo queda sin
target, nunca con el de otro ciclo. Un dato ausente se muestra ausente: nunca
se convierte en cero.

Pausa por producto (AUTO-03 H.5): por producto, las propuestas congeladas en
la evaluacion (cada product ad con su grupo), el gasto observado de la ventana
[D-60, D-30) frente al gasto sobre el que Orbit puede actuar (filas cuyos
ad_ids son todos anuncios elegibles), los anuncios elegibles y los que quedan
fuera por motivo, los motivos sin propuesta y confirmados/total: confirmados
son las decisiones pause de esa evaluacion cuya fila de cola esta applied. El
modo de la pausa sale de notes.productos del ciclo. Los limites vigentes de
la pausa (H.9) se leen de la ultima config_version con el mismo lector que el
ciclo; la pantalla los edita por POST /api/ads-optimizer/settings/{platform}.

Encendido (AUTO-03 I.8): de la misma config_version salen el modo vigente de la
pausa (no el del ultimo ciclo) y si las dos claves de limites estan guardadas,
con `config_write.limites_pausa_guardados`, la misma regla con la que
`productos_modo.cambia_pausa` rechaza encenderla. `estado_pausa` es lo que
devuelve GET /api/dashboard/productos/pausa.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal

from app import config_write, productos_io
from app.cycle import lee_goals
from app.optimizer import goals as g
from app.optimizer import productos as p
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.productos_io import evaluacion_de_fila

_SQL_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_CONFIG_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_CAMPANAS = """
SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = 'campaign'
"""

_SQL_CICLO = """
SELECT id, to_char(coalesce(finished_at, started_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'),
       notes
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND platform = %s::platform AND status = 'done'
 ORDER BY id DESC LIMIT 1
"""

_SQL_EVALUACIONES = """
SELECT e.product_id, pr.odoo_sku, pr.name, e.entrada, e.resultado, e.id
  FROM evaluacion_producto_ciclo e
  JOIN product pr ON pr.id = e.product_id
 WHERE e.cycle_id = %s AND e.platform = %s::platform
 ORDER BY e.product_id
"""

_SQL_ENTIDADES = """
SELECT id, coalesce(name, keyword_text, external_id) FROM ad_entity WHERE id = ANY(%s)
"""

_SQL_ANUNCIOS = """
SELECT id, name, external_id FROM ad_entity WHERE id = ANY(%s)
"""

# Decisiones pause de las evaluaciones del ciclo y el estado de su ultima fila
# de cola (sin fila: el ciclo no la encolo).
_SQL_COLA_PAUSAS = """
SELECT d.evaluacion_producto_id, d.ad_entity_id, q.estado
  FROM decision d
  LEFT JOIN LATERAL (
      SELECT estado FROM apply_queue WHERE decision_id = d.id ORDER BY id DESC LIMIT 1
  ) q ON true
 WHERE d.cycle_id = %s AND d.kind = 'pause' AND d.evaluacion_producto_id IS NOT NULL
"""

_SQL_TARGETS_DEL_CICLO = """
SELECT ad_entity_id, target_acos_pct, procedencia
  FROM target_acos_ciclo
 WHERE cycle_id = %s AND ad_entity_id = ANY(%s)
"""

MODOS_TEXTO = {"off": "apagada", "shadow": "en práctica", "live": "en vivo"}
# Estado de la pausa en la seccion de limites (I.8).
ESTADOS_PAUSA = {"off": "Apagada", "shadow": "Práctica", "live": "En vivo"}
_PROCEDENCIAS_GOAL = frozenset({"goal_campana", "goal_plataforma"})


@dataclass(frozen=True)
class HojaVista:
    hoja_id: int
    texto: str
    target_vigente: str | None
    procedencia_vigente: str | None
    veredicto_actual: str | None
    target_con_meta: str | None
    veredicto_con_meta: str | None

    @property
    def cambia(self) -> bool:
        return self.veredicto_con_meta is not None and (
            self.veredicto_con_meta != self.veredicto_actual
        )

    def como_dict(self) -> dict:
        return {
            "hoja_id": self.hoja_id,
            "texto": self.texto,
            "target_vigente": self.target_vigente,
            "procedencia_vigente": self.procedencia_vigente,
            "veredicto_actual": self.veredicto_actual,
            "target_con_meta": self.target_con_meta,
            "veredicto_con_meta": self.veredicto_con_meta,
            "cambia": self.cambia,
        }


@dataclass(frozen=True)
class GrupoVista:
    grupo_id: int
    grupo: str
    campana: str
    hojas: tuple[HojaVista, ...]

    @property
    def domina_goal(self) -> bool:
        """S1: un goal con target pisa a la meta en las hojas de este grupo."""
        return any(h.procedencia_vigente in _PROCEDENCIAS_GOAL for h in self.hojas)

    def como_dict(self) -> dict:
        return {
            "grupo_id": self.grupo_id,
            "grupo": self.grupo,
            "campana": self.campana,
            "domina_goal": self.domina_goal,
            "hojas": [h.como_dict() for h in self.hojas],
        }


@dataclass(frozen=True)
class PropuestaVista:
    ad_entity_id: int
    anuncio: str
    externo: str | None
    grupo: str
    estado_cola: str | None

    def como_dict(self) -> dict:
        return {
            "ad_entity_id": self.ad_entity_id,
            "anuncio": self.anuncio,
            "externo": self.externo,
            "grupo": self.grupo,
            "estado_cola": self.estado_cola,
        }


@dataclass(frozen=True)
class PausaVista:
    """La pausa de un producto en la evaluacion del ciclo. gasto_observado y
    clics son None si la actividad de la ventana no esta completa (SinDato,
    con motivos_actividad); gasto_actuable es None si no se calculo."""

    ventana_desde: str
    ventana_ultimo_dia: str
    moneda: str
    gasto_observado: str | None
    clics: int | None
    gasto_actuable: str | None
    motivos_actividad: tuple[tuple[str, str], ...]
    limite_clics: int
    limite_gasto: str | None
    elegibles: int
    excluidos: tuple[tuple[str, str, int], ...]
    motivos: tuple[tuple[str, str], ...]
    propuestas: tuple[PropuestaVista, ...]
    confirmados: int

    @property
    def total(self) -> int:
        return len(self.propuestas)

    def como_dict(self) -> dict:
        return {
            "ventana_desde": self.ventana_desde,
            "ventana_ultimo_dia": self.ventana_ultimo_dia,
            "moneda": self.moneda,
            "gasto_observado": self.gasto_observado,
            "clics": self.clics,
            "gasto_actuable": self.gasto_actuable,
            "motivos_actividad": [{"codigo": c, "etiqueta": e} for c, e in self.motivos_actividad],
            "limite_clics": self.limite_clics,
            "limite_gasto": self.limite_gasto,
            "elegibles": self.elegibles,
            "excluidos": [{"codigo": c, "etiqueta": e, "n": n} for c, e, n in self.excluidos],
            "excluidos_total": sum(n for *_, n in self.excluidos),
            "motivos": [{"codigo": c, "etiqueta": e} for c, e in self.motivos],
            "propuestas": [pr.como_dict() for pr in self.propuestas],
            "confirmados": self.confirmados,
            "total": self.total,
        }


@dataclass(frozen=True)
class LimitesVista:
    """Limites vigentes de la pausa del mercado (H.9). config_version_id es la
    base que exige el guardado; sin config_version no hay sobre que guardar.
    Una config ilegible deja error y ningun valor. `guardados` dice si las dos
    claves de limites estan en la config: sin ellas los valores son los de
    omision y guardarlos tal cual SI es un cambio (S8 los exige guardados), y
    la pausa no se puede encender. `modo` es el modo vigente de la pausa en
    esa config; si los interruptores son ilegibles queda None con su
    `modo_error` y la pantalla no ofrece encender ni regresar."""

    config_version_id: int | None
    clics: int | None
    gasto: str | None
    moneda: str
    error: str | None
    guardados: bool = False
    modo: str | None = None
    modo_error: str | None = None

    def como_dict(self) -> dict:
        return {
            "config_version_id": self.config_version_id,
            "clics": self.clics,
            "gasto": self.gasto,
            "gasto_texto": _numero(self.gasto),
            "moneda": self.moneda,
            "error": self.error,
            "guardados": self.guardados,
            "modo": self.modo,
            "modo_texto": ESTADOS_PAUSA.get(self.modo) if self.modo else None,
            "modo_error": self.modo_error,
        }


@dataclass(frozen=True)
class ProductoVista:
    product_id: int
    sku: str
    nombre: str
    margen_pct: str | None
    margen_desde: str | None
    margen_hasta: str | None
    meta_pct: str | None
    motivos: tuple[tuple[str, str], ...]
    grupos: tuple[GrupoVista, ...]
    pausa: PausaVista | None = None

    def como_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "nombre": self.nombre,
            "margen_pct": self.margen_pct,
            "margen_desde": self.margen_desde,
            "margen_hasta": self.margen_hasta,
            "meta_pct": self.meta_pct,
            "motivos": [{"codigo": c, "etiqueta": e} for c, e in self.motivos],
            "grupos": [gr.como_dict() for gr in self.grupos],
        }


@dataclass(frozen=True)
class DecisionesProducto:
    cycle_id: int | None
    decidido_el: str | None
    modo_meta: str | None
    error: str | None
    resumen: dict
    productos: tuple[ProductoVista, ...]
    modo_pausa: str | None = None
    limites: LimitesVista | None = None

    @property
    def con_meta(self) -> int:
        return sum(pr.meta_pct is not None for pr in self.productos)

    @property
    def motivos_presentes(self) -> tuple[tuple[str, str, int], ...]:
        cuenta = Counter(c for pr in self.productos for c, _ in pr.motivos)
        return tuple((c, p.MOTIVOS_PRODUCTO[c], n) for c, n in sorted(cuenta.items()))

    def como_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "decidido_el": self.decidido_el,
            "modo_meta": self.modo_meta,
            "modo_meta_texto": MODOS_TEXTO.get(self.modo_meta or "off"),
            "error": self.error,
            "resumen": self.resumen,
            "con_meta": self.con_meta,
            "motivos_presentes": [
                {"codigo": c, "etiqueta": e, "n": n} for c, e, n in self.motivos_presentes
            ],
            "productos": [pr.como_dict() for pr in self.productos],
            "pausa": self._pausa_dict(),
            "limites_pausa": self.limites.como_dict() if self.limites else None,
        }

    def _pausa_dict(self) -> dict:
        """Primero los productos con propuestas, luego por gasto observado
        (sin dato al final)."""
        con_pausa = [pr for pr in self.productos if pr.pausa is not None]
        con_pausa.sort(
            key=lambda pr: (
                -pr.pausa.total,
                pr.pausa.gasto_observado is None,
                -Decimal(pr.pausa.gasto_observado or 0),
                pr.product_id,
            )
        )
        return {
            "modo": self.modo_pausa,
            "modo_texto": MODOS_TEXTO.get(self.modo_pausa or "off"),
            "con_propuestas": sum(pr.pausa.total > 0 for pr in con_pausa),
            "propuestas": sum(pr.pausa.total for pr in con_pausa),
            "confirmados": sum(pr.pausa.confirmados for pr in con_pausa),
            "productos": [
                {
                    "product_id": pr.product_id,
                    "sku": pr.sku,
                    "nombre": pr.nombre,
                    **pr.pausa.como_dict(),
                }
                for pr in con_pausa
            ],
        }


def lee_limites(conn, plataforma: str) -> LimitesVista:
    """Los limites de la pausa que el ciclo leeria hoy (C.7): clave ausente
    da el valor vigente del motor de cortes. Tambien el modo vigente de la
    pausa y si sus limites estan guardados (I.8), con la misma regla que
    `productos_modo.cambia_pausa` exige para encenderla."""
    moneda = PLATAFORMAS_MONEDA[plataforma]
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    config_id, settings = (fila[0], fila[1] or {}) if fila else (None, {})
    try:
        modo, modo_error = p.modos_desde_settings(settings, plataforma).pausa, None
    except ValueError as exc:
        modo, modo_error = None, str(exc)
    try:
        limites = p.limites_pausa_desde_settings(settings, plataforma)
    except ValueError as exc:
        return LimitesVista(
            config_id, None, None, moneda, str(exc), modo=modo, modo_error=modo_error
        )
    return LimitesVista(
        config_id,
        limites.clics_min,
        format(limites.gasto_min.valor.normalize(), "f"),
        limites.gasto_min.moneda,
        None,
        config_write.limites_pausa_guardados(settings, plataforma),
        modo,
        modo_error,
    )


def estado_pausa(conn, plataforma: str) -> dict:
    """GET /api/dashboard/productos/pausa (I.8): modo vigente de la pausa,
    limites vigentes, si las dos claves estan guardadas y la config_version
    sobre la que se enciende o se regresa a practica. Solo SELECT."""
    lim = lee_limites(conn, plataforma)
    return {
        "platform": plataforma,
        "modo": lim.modo,
        "modo_texto": ESTADOS_PAUSA.get(lim.modo) if lim.modo else None,
        "config_version_id": lim.config_version_id,
        "limites": None
        if lim.error
        else {"clics": lim.clics, "gasto": {"valor": lim.gasto, "moneda": lim.moneda}},
        "limites_guardados": lim.guardados,
        "error": lim.modo_error or lim.error,
    }


def lee_decisiones_producto(conn, plataforma: str) -> DecisionesProducto:
    limites = lee_limites(conn, plataforma)
    ciclo = conn.execute(_SQL_CICLO, (plataforma,)).fetchone()
    if ciclo is None:
        return DecisionesProducto(None, None, None, None, {}, (), limites=limites)
    cycle_id, decidido_el, notes = ciclo
    try:
        notas = json.loads(notes) if isinstance(notes, str) else notes or {}
    except ValueError:
        notas = {"productos": {"error": "las notas del ciclo no son JSON"}}
    notas = notas.get("productos") or {}
    resumen = {k: v for k, v in notas.items() if k not in ("error", "modo_meta", "modo_pausa")}
    filas = conn.execute(_SQL_EVALUACIONES, (cycle_id, plataforma)).fetchall()
    try:
        productos = _productos(conn, cycle_id, filas)
        error = notas.get("error")
    except (KeyError, TypeError, ValueError) as exc:
        # Una evaluacion ilegible apaga esta seccion, no la pagina entera.
        productos, error = [], f"evaluacion ilegible: {type(exc).__name__}: {exc}"
    return DecisionesProducto(
        cycle_id=cycle_id,
        decidido_el=decidido_el,
        modo_meta=notas.get("modo_meta"),
        error=error,
        resumen=resumen,
        productos=tuple(productos),
        modo_pausa=notas.get("modo_pausa"),
        limites=limites,
    )


def simula_pausa(
    conn, plataforma: str, *, ahora, clics: str | None, gasto: str | None
) -> p.SimulacionPausa:
    """AUTO-03 H.8, solo lectura: la pausa con los limites vigentes o con los
    que pida la pantalla (sin guardarlos). La usan la ruta de la pantalla y
    tools.auto03_verifica simula. Un limite invalido lanza
    config_write.SettingsInvalido."""
    fila = conn.execute(_SQL_CONFIG).fetchone()
    settings = fila[0] if fila else {}
    limites = productos_io.limites_de_simulacion(settings, plataforma, clics=clics, gasto=gasto)
    campanas = [f[0] for f in conn.execute(_SQL_CAMPANAS, (plataforma,))]
    return productos_io.simula_pausa(
        conn,
        plataforma,
        ahora=ahora,
        settings=settings,
        goals=lee_goals(conn, plataforma, campanas),
        limites=limites,
    )


def _productos(conn, cycle_id: int, filas) -> list[ProductoVista]:
    evaluaciones = [
        (pid, sku, nombre, evaluacion_de_fila(entrada, resultado), resultado)
        for pid, sku, nombre, entrada, resultado, _ in filas
    ]
    id_evaluacion = {fila[0]: fila[5] for fila in filas}
    grupos = [gr for *_, ev, _ in evaluaciones for gr in ev.entrada.grupos_aptos_para_meta]
    hojas = sorted({h for gr in grupos for h in gr.hojas})
    ids = sorted({*hojas, *(gr.grupo_id for gr in grupos), *(gr.campana_id for gr in grupos)})
    nombres = dict(conn.execute(_SQL_ENTIDADES, (ids,)).fetchall()) if ids else {}
    vigentes = {
        fila[0]: (fila[1], fila[2])
        for fila in (
            conn.execute(_SQL_TARGETS_DEL_CICLO, (cycle_id, hojas)).fetchall() if hojas else ()
        )
    }
    pausas = _pausas(conn, cycle_id, [ev for *_, ev, _ in evaluaciones], id_evaluacion)
    productos = [
        replace(_producto(pid, sku, nombre, ev, resultado, nombres, vigentes), pausa=pausas[pid])
        for pid, sku, nombre, ev, resultado in evaluaciones
    ]
    productos.sort(key=lambda pr: (pr.meta_pct is None, -Decimal(pr.meta_pct or 0), pr.product_id))
    return productos


def _producto(
    pid: int,
    sku: str,
    nombre: str,
    ev: p.EvaluacionProducto,
    resultado: dict,
    nombres: dict[int, str],
    vigentes: dict[int, tuple[Decimal, str]],
) -> ProductoVista:
    margen = ev.entrada.margen
    medido = isinstance(margen, g.MedicionMargen)
    sombra = {s["hoja_id"]: s for s in resultado.get("sombra_hojas") or ()}
    grupos = tuple(
        GrupoVista(
            grupo_id=gr.grupo_id,
            grupo=nombres.get(gr.grupo_id, str(gr.grupo_id)),
            campana=nombres.get(gr.campana_id, str(gr.campana_id)),
            hojas=tuple(_hoja(h, nombres, vigentes, sombra.get(h)) for h in gr.hojas),
        )
        for gr in ev.entrada.grupos_aptos_para_meta
    )
    return ProductoVista(
        product_id=pid,
        sku=sku,
        nombre=nombre,
        margen_pct=_numero(margen.margen_neto_pct) if medido else None,
        margen_desde=_fecha(margen.ventana_desde) if medido else None,
        margen_hasta=_fecha(margen.ventana_hasta) if medido else None,
        meta_pct=_numero(ev.meta.porcentaje) if isinstance(ev.meta, p.MetaReferencia) else None,
        motivos=tuple((m, p.MOTIVOS_PRODUCTO[m]) for m in ev.motivos),
        grupos=grupos,
    )


def _pausas(
    conn, cycle_id: int, evaluaciones: list[p.EvaluacionProducto], id_evaluacion: dict[int, int]
) -> dict[int, PausaVista]:
    propuestos = sorted({pr.anuncio.ad_entity_id for ev in evaluaciones for pr in ev.pausas})
    grupos = sorted({pr.anuncio.grupo_id for ev in evaluaciones for pr in ev.pausas})
    anuncios = (
        {f[0]: (f[1], f[2]) for f in conn.execute(_SQL_ANUNCIOS, (propuestos,))}
        if propuestos
        else {}
    )
    nombres = dict(conn.execute(_SQL_ENTIDADES, (grupos,)).fetchall()) if grupos else {}
    cola = (
        {(f[0], f[1]): f[2] for f in conn.execute(_SQL_COLA_PAUSAS, (cycle_id,))}
        if propuestos
        else {}
    )
    return {
        ev.producto.product_id: _pausa(
            ev, id_evaluacion[ev.producto.product_id], anuncios, nombres, cola
        )
        for ev in evaluaciones
    }


def _pausa(
    ev: p.EvaluacionProducto,
    evaluacion_id: int,
    anuncios: dict[int, tuple[str | None, str]],
    nombres: dict[int, str],
    cola: dict[tuple[int, int], str | None],
) -> PausaVista:
    entrada = ev.entrada
    actividad = entrada.actividad_pausa
    completa = isinstance(actividad, p.AdsCompletos)
    ventana = p.ventana_pausa(entrada.decidido_el)
    propuestas = []
    for propuesta in ev.pausas:
        anuncio = propuesta.anuncio
        nombre, externo = anuncios.get(anuncio.ad_entity_id, (None, None))
        propuestas.append(
            PropuestaVista(
                ad_entity_id=anuncio.ad_entity_id,
                anuncio=nombre or externo or str(anuncio.ad_entity_id),
                externo=externo,
                grupo=nombres.get(anuncio.grupo_id, str(anuncio.grupo_id)),
                estado_cola=cola.get((evaluacion_id, anuncio.ad_entity_id)),
            )
        )
    excluidos = Counter(a.motivo for a in entrada.excluidos)
    limites = entrada.limites_pausa
    return PausaVista(
        ventana_desde=ventana.desde.isoformat(),
        ventana_ultimo_dia=(ventana.hasta_exclusivo - dt.timedelta(days=1)).isoformat(),
        moneda=limites.gasto_min.moneda,
        gasto_observado=_numero(actividad.gasto.valor) if completa else None,
        clics=actividad.clics if completa else None,
        gasto_actuable=(
            _numero(entrada.gasto_actuable.valor) if entrada.gasto_actuable is not None else None
        ),
        motivos_actividad=(
            () if completa else tuple((m, p.MOTIVOS_PRODUCTO[m]) for m in actividad.motivos)
        ),
        limite_clics=limites.clics_min,
        limite_gasto=_numero(limites.gasto_min.valor),
        elegibles=len(entrada.anuncios),
        excluidos=tuple((m, p.MOTIVOS_PRODUCTO[m], n) for m, n in sorted(excluidos.items())),
        motivos=tuple((m, p.MOTIVOS_PRODUCTO[m]) for m in ev.motivos_pausa),
        propuestas=tuple(propuestas),
        confirmados=sum(pr.estado_cola == "applied" for pr in propuestas),
    )


def _hoja(
    hoja_id: int,
    nombres: dict[int, str],
    vigentes: dict[int, tuple[Decimal, str]],
    sombra: dict | None,
) -> HojaVista:
    target, procedencia = vigentes.get(hoja_id, (None, None))
    return HojaVista(
        hoja_id=hoja_id,
        texto=nombres.get(hoja_id, str(hoja_id)),
        target_vigente=_numero(target),
        procedencia_vigente=procedencia,
        veredicto_actual=veredicto_texto(sombra["veredicto_actual"]) if sombra else None,
        target_con_meta=_numero(sombra.get("target_con_meta")) if sombra else None,
        veredicto_con_meta=(
            veredicto_texto(sombra["veredicto_con_meta"])
            if sombra and sombra.get("veredicto_con_meta")
            else None
        ),
    )


def veredicto_texto(veredicto: dict) -> str:
    """Mantener · sin_target; Mover -12 % a 0.88 · gasto_sin_venta. Sin la
    politica de bids el ciclo juzga con bid.ResultadoBid (pause o no-op)."""
    if veredicto["tipo"] == "ResultadoBid":
        partes = ["Pausar" if veredicto.get("kind") == "pause" else "Mantener"]
    else:
        partes = [veredicto["tipo"]]
        if veredicto.get("factor") is not None:
            partes.append(f"{_numero(Decimal(veredicto['factor']) * 100)} %")
        if veredicto.get("bid_nuevo") is not None:
            partes.append(f"a {_numero(veredicto['bid_nuevo'])}")
    motivo = veredicto.get("motivo")
    return " ".join(partes) + (f" · {motivo}" if motivo else "")


def _numero(valor) -> str | None:
    if valor is None:
        return None
    return f"{Decimal(str(valor)).quantize(Decimal('0.01')):f}"


def _fecha(valor) -> str | None:
    return None if valor is None else valor.isoformat()
