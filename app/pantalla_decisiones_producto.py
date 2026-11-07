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
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from app import productos_io
from app.cycle import lee_goals
from app.optimizer import goals as g
from app.optimizer import productos as p
from app.productos_io import evaluacion_de_fila

_SQL_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

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
SELECT e.product_id, pr.odoo_sku, pr.name, e.entrada, e.resultado
  FROM evaluacion_producto_ciclo e
  JOIN product pr ON pr.id = e.product_id
 WHERE e.cycle_id = %s AND e.platform = %s::platform
 ORDER BY e.product_id
"""

_SQL_ENTIDADES = """
SELECT id, coalesce(name, keyword_text, external_id) FROM ad_entity WHERE id = ANY(%s)
"""

_SQL_TARGETS_DEL_CICLO = """
SELECT ad_entity_id, target_acos_pct, procedencia
  FROM target_acos_ciclo
 WHERE cycle_id = %s AND ad_entity_id = ANY(%s)
"""

MODOS_TEXTO = {"off": "apagada", "shadow": "en práctica", "live": "en vivo"}
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
        }


def lee_decisiones_producto(conn, plataforma: str) -> DecisionesProducto:
    ciclo = conn.execute(_SQL_CICLO, (plataforma,)).fetchone()
    if ciclo is None:
        return DecisionesProducto(None, None, None, None, {}, ())
    cycle_id, decidido_el, notes = ciclo
    try:
        notas = json.loads(notes) if isinstance(notes, str) else notes or {}
    except ValueError:
        notas = {"productos": {"error": "las notas del ciclo no son JSON"}}
    notas = notas.get("productos") or {}
    resumen = {k: v for k, v in notas.items() if k not in ("error", "modo_meta")}
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
        for pid, sku, nombre, entrada, resultado in filas
    ]
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
    productos = [
        _producto(pid, sku, nombre, ev, resultado, nombres, vigentes)
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
