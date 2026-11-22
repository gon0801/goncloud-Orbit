"""Medicion SQL de la compuerta de precios por universo (REPRICING 02 S.3).

Extraido de `app/precio/corrida.py` (Ola E): arma `Intencion` y `Deriva` desde
las decisiones persistidas del dia y escribe `precio_compuerta_medicion`.
Sin reloj local, sin red, sin imports fuera del perimetro de `corrida.py`.
Escribe UNICAMENTE en `precio_compuerta_medicion`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg

from app.precio.compuerta import (
    Anterior,
    Intencion,
    Retenida,
    armar_deriva,
    cambios_en_vivo,
    es_nueva,
    evaluar,
)

__all__ = ["a_la_venta", "medir_compuerta"]

_SQL_LISTINGS_DE_SKUS = "SELECT id FROM listing WHERE platform = %s AND seller_sku = ANY(%s)"

_SQL_MEDIR_HOY = (
    "SELECT d.id, d.listing_id, d.canal::text, d.resultado, d.motivo, d.goal_id,"
    " d.c_valor, d.c_currency::text, d.l_valor, d.l_currency::text,"
    " d.r_valor, d.r_currency::text, d.escenario_id, d.i_valor, d.i_currency::text,"
    " d.mode::text"
    " FROM precio_decision d"
    " WHERE d.platform = %s AND d.decision_date = %s AND d.m_actual IS NOT NULL"
    " AND d.canal IS NOT NULL"
    " ORDER BY d.listing_id"
)

_SQL_MEDIR_PREVIA = (
    "SELECT DISTINCT ON (d.listing_id) d.listing_id, d.resultado, d.motivo,"
    " d.goal_id, d.c_valor, d.c_currency::text, d.l_valor, d.l_currency::text,"
    " d.r_valor, d.r_currency::text, d.escenario_id, d.i_valor, d.i_currency::text,"
    " d.mode::text"
    " FROM precio_decision d"
    " WHERE d.platform = %s AND d.decision_date < %s AND d.listing_id = ANY(%s)"
    # Dia retenido y no soltado del mismo universo: no es linea base (G-H1).
    # Regla espejo de liberaciones.py: solo lo SOLTADO deja de contar.
    " AND NOT EXISTS ("
    "   SELECT 1 FROM precio_retencion r"
    "   JOIN precio_decision dr ON dr.id = r.decision_id"
    "   WHERE r.causa IN ('movimiento_masivo', 'insumo_sistemico')"
    "     AND dr.platform = d.platform AND dr.decision_date = d.decision_date"
    "     AND dr.canal = d.canal"
    "     AND r.decision_id NOT IN (SELECT decision_id FROM v_precio_decision_liberada))"
    " ORDER BY d.listing_id, d.decision_date DESC, d.id DESC"
)

_SQL_MEDIR_ESCENARIOS = (
    "SELECT e.id, e.fx_rate, e.fx_base::text, e.fx_quote::text,"
    " e.fee_variable, e.fee_fijo, e.moneda::text, e.formula_version"
    " FROM estimacion_escenario e WHERE e.id = ANY(%s)"
)

_SQL_MEDIR_LIBERADAS = (
    "SELECT decision_id FROM v_precio_decision_liberada WHERE decision_id = ANY(%s)"
)

_SQL_MEDIR_INSERT = (
    "INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas, causa,"
    " insumo, detalle) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

_INSUMOS_DECISION = ("costo", "envio", "retencion")
_INSUMOS_ESCENARIO = ("fx", "fee_fijo", "fee_variable")
_INSUMOS_MEDIR = (*_INSUMOS_DECISION, *_INSUMOS_ESCENARIO)
_INSUMOS_FORMULA = frozenset({"retencion", "fee_variable"})
_INSUMOS_POR_INGRESO = frozenset({"retencion", "fee_variable"})


@dataclass(frozen=True)
class _FilaDecisionMedida:
    """Proyeccion tipada de `precio_decision` para la compuerta (hoy o previa)."""

    decision_id: int | None
    listing_id: int
    canal: str | None
    resultado: str
    motivo: str | None
    goal_id: int | None
    costo: Decimal | None
    costo_moneda: str | None
    envio: Decimal | None
    envio_moneda: str | None
    retencion: Decimal | None
    retencion_moneda: str | None
    escenario_id: int | None
    ingreso: Decimal | None
    ingreso_moneda: str | None
    modo: str

    def insumo(self, nombre: str) -> tuple[Decimal | None, str | None]:
        if nombre == "costo":
            return (self.costo, self.costo_moneda)
        if nombre == "envio":
            return (self.envio, self.envio_moneda)
        if nombre == "retencion":
            return (self.retencion, self.retencion_moneda)
        raise KeyError(nombre)


@dataclass(frozen=True)
class _FilaEscenarioMedido:
    """Proyeccion tipada de `estimacion_escenario` para derivas de fx/fees."""

    escenario_id: int
    fx_rate: Decimal | None
    fx_base: str | None
    fx_quote: str | None
    fee_variable: Decimal | None
    fee_fijo: Decimal | None
    moneda: str | None
    formula_version: str | None

    def insumo(self, nombre: str) -> tuple[Decimal | None, tuple[str | None, ...]]:
        if nombre == "fx":
            return (self.fx_rate, (self.fx_base, self.fx_quote))
        if nombre == "fee_fijo":
            return (self.fee_fijo, (self.moneda,))
        if nombre == "fee_variable":
            return (self.fee_variable, (self.moneda,))
        raise KeyError(nombre)


def a_la_venta(conn: psycopg.Connection, mercado, hoy: date) -> frozenset[int] | None:
    """E.2: `listing_id` cuyo SKU esta en el catalogo activo, o `None` si el
    mercado no filtra por catalogo (MeLi decide sobre todo goal vigente).
    El catalogo es uno por SKU: dos listings con el mismo SKU estan los dos
    a la venta (la unidad del catalogo trae solo uno)."""
    if not mercado.filtra_catalogo:
        return None
    skus = sorted({activa.seller_sku for activa in mercado.catalogo(conn, hoy=hoy)})
    filas = conn.execute(_SQL_LISTINGS_DE_SKUS, (mercado.clave, skus)).fetchall()
    return frozenset(fila[0] for fila in filas)


def _tasa(importe: Decimal | None, ingreso: Decimal | None) -> Decimal | None:
    """`importe / ingreso`, o None si falta un lado o el ingreso no es positivo
    (regla 3: no comparable, nunca inventado)."""
    if importe is None or ingreso is None or ingreso <= 0:
        return None
    return importe / ingreso


def _sobre_ingreso(
    fila: _FilaDecisionMedida | None,
    importe: Decimal | None,
    moneda_importe: str | None,
) -> Decimal | None:
    """Tasa del importe sobre el I de la misma fila; None si la moneda del
    importe no es la de I (el fee_variable vive en la moneda del escenario)."""
    if fila is None or importe is None or moneda_importe is None:
        return None
    if moneda_importe != fila.ingreso_moneda:
        return None
    return _tasa(importe, fila.ingreso)


def _par_decision(
    fila_hoy: _FilaDecisionMedida,
    fila_previa: _FilaDecisionMedida | None,
    insumo: str,
) -> tuple[Decimal | None, Decimal | None]:
    """Par `(antes, ahora)` de un insumo de la decision, o `(None, None)`
    si no compara (ausente o distinta moneda)."""
    ahora, ahora_mon = fila_hoy.insumo(insumo)
    if fila_previa is None:
        return (None, ahora)
    antes, antes_mon = fila_previa.insumo(insumo)
    if antes is None or ahora is None or antes_mon != ahora_mon:
        return (None, None)
    return (antes, ahora)


def _par_escenario(
    esc_hoy: _FilaEscenarioMedido | None,
    esc_previa: _FilaEscenarioMedido | None,
    insumo: str,
) -> tuple[Decimal | None, Decimal | None]:
    """Par `(antes, ahora)` de un insumo del escenario, o `(None, None)`
    si no compara (sin escenario, ausente o distinto par moneda)."""
    if esc_hoy is None or esc_previa is None:
        return (None, None)
    ahora, monedas_hoy = esc_hoy.insumo(insumo)
    antes, monedas_prev = esc_previa.insumo(insumo)
    if any(m is None for m in monedas_hoy) or any(m is None for m in monedas_prev):
        return (None, None)
    if monedas_hoy != monedas_prev or antes is None or ahora is None:
        return (None, None)
    return (antes, ahora)


def medir_compuerta(conn: psycopg.Connection, mercado, hoy: date, motor, corrida_id: int) -> dict:
    """S.3: arma `Intencion` y `Deriva` desde lo PERSISTIDO hoy, evalua UN
    universo por canal y escribe una fila de `precio_compuerta_medicion`
    por universo con medidas > 0. Devuelve `{canal: Veredicto}`.

    Mide el dia completo, no solo lo decidido en esta corrida: una segunda
    corrida con goals sembrados a media tarde ve el mismo numerador. Miden
    las decisiones de hoy CON cuenta (`m_actual`), shadow y live juntas,
    con canal conocido (sin canal no hay universo que medir). E.2: si el
    mercado filtra por catalogo, una publicacion que ya no esta a la venta
    no mide (su decision de hoy tampoco se aplica).
    """
    plataforma = mercado.clave
    todas = [
        _FilaDecisionMedida(*fila)
        for fila in conn.execute(_SQL_MEDIR_HOY, (plataforma, hoy)).fetchall()
    ]
    if not todas:
        return {}
    # Conteo del dia (P-D): TODAS las decisiones que mueven precio de hoy,
    # SIN el filtro de catalogo. Un cambio real ya aplicado sigue contando
    # aunque su publicacion salga de la venta.
    liberadas = {
        fila[0]
        for fila in conn.execute(_SQL_MEDIR_LIBERADAS, ([f.decision_id for f in todas],)).fetchall()
    }
    intenciones_dia: dict[str, list[Intencion]] = {}
    for fila in todas:
        if fila.resultado in ("subir", "bajar"):
            assert fila.decision_id is not None and fila.canal is not None
            intenciones_dia.setdefault(fila.canal, []).append(
                Intencion(
                    decision_id=fila.decision_id,
                    listing_id=fila.listing_id,
                    canal=fila.canal,
                    nueva=False,
                    liberada=fila.decision_id in liberadas,
                    en_vivo=fila.modo == "live",
                )
            )
    cambios_dia = cambios_en_vivo(intenciones_dia)
    filas = todas
    activas = a_la_venta(conn, mercado, hoy)
    if activas is not None:
        filas = [fila for fila in filas if fila.listing_id in activas]
    if not filas:
        return {}
    por_listing = {fila.listing_id: fila for fila in filas}
    previas = {
        fila[0]: _FilaDecisionMedida(None, fila[0], None, *fila[1:])
        for fila in conn.execute(_SQL_MEDIR_PREVIA, (plataforma, hoy, list(por_listing))).fetchall()
    }
    ids_escenario = {
        f.escenario_id for f in (*por_listing.values(), *previas.values()) if f.escenario_id
    }
    escenarios = (
        {
            fila[0]: _FilaEscenarioMedido(*fila)
            for fila in conn.execute(_SQL_MEDIR_ESCENARIOS, (list(ids_escenario),)).fetchall()
        }
        if ids_escenario
        else {}
    )
    por_canal: dict[str, list[_FilaDecisionMedida]] = {}
    for fila in filas:
        assert fila.canal is not None
        por_canal.setdefault(fila.canal, []).append(fila)
    intenciones_por_canal: dict[str, list[Intencion]] = {}
    pares_por_canal: dict[str, dict[str, list]] = {}
    for canal, medidas_filas in por_canal.items():
        intenciones: list[Intencion] = []
        pares: dict[str, list] = {insumo: [] for insumo in _INSUMOS_MEDIR}
        for fila in medidas_filas:
            previa = previas.get(fila.listing_id)
            assert fila.decision_id is not None
            liberada = fila.decision_id in liberadas
            if fila.resultado in ("subir", "bajar"):
                anterior = (
                    Anterior(
                        resultado=previa.resultado,
                        motivo=previa.motivo,
                        goal_id=previa.goal_id,
                        modo=previa.modo,
                    )
                    if previa is not None
                    else None
                )
                intenciones.append(
                    Intencion(
                        decision_id=fila.decision_id,
                        listing_id=fila.listing_id,
                        canal=canal,
                        nueva=es_nueva(anterior, goal_id=fila.goal_id, modo=fila.modo),
                        liberada=liberada,
                        en_vivo=fila.modo == "live",
                    )
                )
            esc_hoy = escenarios.get(fila.escenario_id) if fila.escenario_id is not None else None
            esc_prev = (
                escenarios.get(previa.escenario_id)
                if previa is not None and previa.escenario_id is not None
                else None
            )
            cambio_formula = (
                esc_hoy is not None
                and esc_prev is not None
                and esc_hoy.formula_version != esc_prev.formula_version
            )
            for insumo in _INSUMOS_DECISION:
                if cambio_formula and insumo in _INSUMOS_FORMULA:
                    pares[insumo].append((None, None, liberada))
                    continue
                antes, ahora = _par_decision(fila, previa, insumo)
                if insumo in _INSUMOS_POR_INGRESO:
                    mon_prev = previa.insumo(insumo)[1] if previa is not None else None
                    mon_hoy = fila.insumo(insumo)[1]
                    antes = _sobre_ingreso(previa, antes, mon_prev)
                    ahora = _sobre_ingreso(fila, ahora, mon_hoy)
                pares[insumo].append((antes, ahora, liberada))
            for insumo in _INSUMOS_ESCENARIO:
                if cambio_formula and insumo in _INSUMOS_FORMULA:
                    pares[insumo].append((None, None, liberada))
                    continue
                antes, ahora = _par_escenario(esc_hoy, esc_prev, insumo)
                if insumo in _INSUMOS_POR_INGRESO:
                    antes = _sobre_ingreso(previa, antes, esc_prev.moneda if esc_prev else None)
                    ahora = _sobre_ingreso(fila, ahora, esc_hoy.moneda if esc_hoy else None)
                pares[insumo].append((antes, ahora, liberada))
        intenciones_por_canal[canal] = intenciones
        pares_por_canal[canal] = pares
    veredictos = {}
    for canal, medidas_filas in por_canal.items():
        derivas = [
            armar_deriva(canal, insumo, pares_por_canal[canal][insumo], pct=motor.insumo_salto_pct)
            for insumo in _INSUMOS_MEDIR
        ]
        veredicto = evaluar(
            intenciones_por_canal[canal],
            medidas=len(medidas_filas),
            derivas=derivas,
            config=motor,
            cambios_dia=cambios_dia,
        )
        veredictos[canal] = veredicto
        if isinstance(veredicto, Retenida):
            causa, insumo, detalle = veredicto.causa, veredicto.insumo, veredicto.detalle
        else:
            causa, insumo, detalle = None, None, None
        conn.execute(
            _SQL_MEDIR_INSERT,
            (corrida_id, canal, veredicto.medidas, veredicto.nuevas, causa, insumo, detalle),
        )
    return veredictos
