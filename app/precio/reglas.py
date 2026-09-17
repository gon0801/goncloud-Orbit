"""Reglas de decision (REPRICING 01 A.2, S4 #1, #2, #4, #6-#10, #12, #13).

Orden de evaluacion sellado (S4 no lo fija; este es el del brief): insumos
y coherencia (S2) -> `m_actual` -> frenos (#10 `no_converge`, #6
`perdiendo_tras_subida`) -> cooldown (#9) -> direccion -> `P*` y regla 11
-> movimiento minimo (#8) -> escalon. El freno va antes que el cooldown
porque lleva aviso. La primera regla que corta decide y se registra.

No escribe nada (eso es A.5): devuelve `Decision` o `PideCotizacion`. La
llamada se repite con las cotizaciones hechas hasta agotar la maquina de
`objetivo.py` (maximo dos).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.precio.config import ConfigPrecio
from app.precio.objetivo import (
    ErrorObjetivo,
    ResultadoObjetivo,
    derivar_ref_fijo,
    paso,
    piso_centavo,
    techo_centavo,
)
from app.precio.tipos import (
    CotizacionVerificada,
    Decision,
    EntradaDecision,
    Importe,
    PideCotizacion,
    motivo_permitido,
)

__all__ = ["decidir", "repartir_cupo", "motivo_regla11"]

_DIAS_FRENO_SUBIDA = 22


def motivo_regla11(
    p_estrella: Decimal, p_actual: Decimal, costo: Decimal, envio: Decimal
) -> str | None:
    """S4 #11 sobre `P*` ya calculado (sin redondear, como sale del cociente).

    Residual: con denominador < 1 (todos los casos reales: el maximo
    teorico es 1) `P* = (C + fijo + L) / denom > C + L` siempre, asi que
    `precio_no_cubre_costo` es una guarda que no se dispara con insumos
    sanos; se prueba por predicado, no de punta a punta.
    """
    if p_estrella > 2 * p_actual:
        return "precio_mayor_al_doble"
    if p_estrella <= costo + envio:
        return "precio_no_cubre_costo"
    return None


def _base_senal(entrada: EntradaDecision) -> Decision:
    # Motivo transitorio: cada llamado lo reemplaza con `replace` antes de
    # devolver; nunca sale de este modulo (la validacion exige motivo).
    senal = entrada.senal
    return Decision(
        resultado="mantener",
        motivo="en_tolerancia",
        m_actual=None,
        goal=entrada.goal,
        p_actual=entrada.escenario.componentes.p_actual,
        p_objetivo=None,
        p_aplicado=None,
        componentes=entrada.escenario.componentes,
        u15=senal.u15,
        u60=senal.u60,
        n15=senal.n15,
        n60=senal.n60,
        racha=senal.racha,
        perdiendo=senal.estado == "perdiendo",
        prioridad=None,
        aplicado=False,
        mode=entrada.mode,
        buy_box_is_own=entrada.buy_box_is_own,
    )


def _no_evaluado(entrada: EntradaDecision, motivo: str, diagnostico: str = "") -> Decision:
    # r2-A1: un motivo fuera de lista no revienta la corrida diaria
    # (decision 11, ningun silencio): sale con el motivo crudo en el
    # diagnostico. Solo el passthrough de la estimacion puede traer un
    # motivo abierto; los propios del motor pasan validados.
    base = _base_senal(entrada)
    if not motivo_permitido("no_evaluado", motivo):
        crudo = f"motivo_estimacion={motivo!r}" + (f" {diagnostico}" if diagnostico else "")
        return replace(
            base, resultado="no_evaluado", motivo="estimacion_motivo_desconocido", diagnostico=crudo
        )
    return replace(base, resultado="no_evaluado", motivo=motivo, diagnostico=diagnostico)


def _coherencia_escenario(entrada: EntradaDecision) -> str | None:
    """r1-A1: componentes y tasas que cuadran entre si, antes de `m_actual`.

    `precio_cotizado == p_actual` (S2: son el mismo precio); `Σ final_fee ==
    F`; `|I − P / divisor| ≤ 0.01`; `|R − isr_tasa · I| ≤ 0.01`. Si algo
    falla, el qué viaja en el diagnostico.
    """
    comp = entrada.escenario.componentes
    escenario = entrada.escenario
    if (
        escenario.precio_cotizado.valor != comp.p_actual.valor
        or escenario.precio_cotizado.moneda != comp.p_actual.moneda
    ):
        return (
            f"precio_cotizado {escenario.precio_cotizado.valor} "
            f"{escenario.precio_cotizado.moneda} != "
            f"p_actual {comp.p_actual.valor} {comp.p_actual.moneda}"
        )
    suma_fees = sum((det.final_fee for det in escenario.fee_detalles), Decimal(0))
    if suma_fees != comp.fees.valor:
        return f"Σ final_fee {suma_fees} != F {comp.fees.valor}"
    divisor = escenario.iva_divisor if escenario.precio_incluye_iva else Decimal(1)
    if abs(comp.ingreso.valor - comp.p_actual.valor / divisor) > Decimal("0.01"):
        return f"I {comp.ingreso.valor} != P/divisor {comp.p_actual.valor / divisor}"
    if abs(comp.isr.valor - escenario.isr_tasa * comp.ingreso.valor) > Decimal("0.01"):
        return f"R {comp.isr.valor} != isr_tasa·I {escenario.isr_tasa * comp.ingreso.valor}"
    return None


def _frena_regla11(entrada: EntradaDecision, m_actual: Decimal, precio: Decimal) -> Decision | None:
    """r1-A2: todo precio pedido o verificado pasa por la regla 11."""
    comp = entrada.escenario.componentes
    motivo = motivo_regla11(precio, comp.p_actual.valor, comp.costo.valor, comp.envio.valor)
    if motivo is None:
        return None
    base = _base_senal(entrada)
    return replace(
        base,
        resultado="goal_inalcanzable",
        motivo=motivo,
        m_actual=m_actual,
        prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        diagnostico=f"P={precio} vs P_actual={comp.p_actual.valor}",
    )


def _freno_entrada(entrada: EntradaDecision, config: ConfigPrecio) -> Decision | None:
    """S2 + coherencia (r1-A1, r2-A2, r3-K1, r4-G4): insumos y coherencia
    antes de `m_actual` y antes de gastar una cotización. `None` = pasa."""
    if entrada.motivo_estimacion is not None:
        return _no_evaluado(entrada, entrada.motivo_estimacion)
    if entrada.pricing is None:
        return _no_evaluado(entrada, "precio_sin_observar")

    comp = entrada.escenario.componentes
    monedas = {
        comp.p_actual.moneda,
        comp.ingreso.moneda,
        comp.costo.moneda,
        comp.fees.moneda,
        comp.envio.moneda,
        comp.isr.moneda,
    }
    if len(monedas) != 1:
        return _no_evaluado(
            entrada, "escenario_incoherente", f"monedas divergen: {sorted(monedas)}"
        )
    moneda = comp.p_actual.moneda
    pricing = entrada.pricing
    if pricing.precio.moneda != moneda:
        return _no_evaluado(
            entrada,
            "moneda_divergente",
            f"escenario {moneda} vs pricing {pricing.precio.moneda}",
        )
    p_actual = comp.p_actual.valor
    if p_actual <= 0 or pricing.precio.valor <= 0:
        return _no_evaluado(
            entrada,
            "escenario_incoherente",
            f"precio no positivo: P={p_actual} pricing={pricing.precio.valor}",
        )
    divergencia = abs(pricing.precio.valor - p_actual) / p_actual
    if divergencia > config.divergencia_max_pct:
        return _no_evaluado(
            entrada,
            "precio_divergente",
            f"escenario {p_actual} {moneda} a las "
            f"{entrada.escenario.oferta_observada_en.isoformat()} vs pricing "
            f"{pricing.precio.valor} {moneda} a las {pricing.observada_en.isoformat()}",
        )

    incoherencia = _coherencia_escenario(entrada)
    if incoherencia is not None:
        return _no_evaluado(entrada, "escenario_incoherente", incoherencia)
    if comp.p_actual.moneda not in ("MXN", "USD"):
        return _no_evaluado(
            entrada,
            "escenario_incoherente",
            f"moneda sin minimo absoluto configurado: {comp.p_actual.moneda}",
        )
    if comp.ingreso.valor <= 0:
        return _no_evaluado(entrada, "ingreso_no_positivo")
    return None


def _min_abs(config: ConfigPrecio, moneda: str) -> Decimal:
    if moneda == "MXN":
        return config.movimiento_min_abs_mxn
    if moneda == "USD":
        return config.movimiento_min_abs_usd
    raise ValueError(f"moneda sin minimo absoluto configurado: {moneda!r}")


def _prioridad(m_actual: Decimal, goal: Decimal, ingreso_60d: Importe | None) -> Decimal | None:
    if ingreso_60d is None:
        return None
    return abs(m_actual - goal) * ingreso_60d.valor


def decidir(
    entrada: EntradaDecision,
    *,
    hoy: date,
    config: ConfigPrecio,
    cotizaciones: tuple[CotizacionVerificada, ...] = (),
) -> Decision | PideCotizacion:
    """Una evaluacion de S4: `Decision` final o `PideCotizacion`."""
    freno = _freno_entrada(entrada, config)
    if freno is not None:
        return freno

    comp = entrada.escenario.componentes
    ingreso = comp.ingreso.valor
    m_actual = (
        ingreso - comp.costo.valor - comp.fees.valor - comp.envio.valor - comp.isr.valor
    ) / ingreso
    goal = entrada.goal
    tol = config.tolerancia
    distancia = abs(m_actual - goal)

    reales = sorted(
        (
            h
            for h in entrada.historial
            if (entrada.mode == "shadow" or h.aplicado) and h.fecha >= entrada.goal_vigente_desde
        ),
        key=lambda h: h.fecha,
    )
    if len(reales) >= config.freno_cambios:
        ultimos = reales[-config.freno_cambios :]
        direcciones = {h.direccion for h in ultimos}
        cadena = [h.distancia for h in ultimos] + [distancia]
        if len(direcciones) == 1 and all(
            posterior >= anterior for anterior, posterior in zip(cadena, cadena[1:], strict=False)
        ):
            base = _base_senal(entrada)
            return replace(
                base,
                resultado="frenado",
                motivo="no_converge",
                m_actual=m_actual,
                prioridad=_prioridad(m_actual, goal, entrada.ingreso_60d),
                diagnostico=f"{len(ultimos)} cambios sin acercarse al goal",
            )

    if entrada.senal.estado == "perdiendo":
        for cambio in entrada.cambios:
            if (
                cambio.es_reversa
                or cambio.estado != "confirmado"
                or (entrada.mode == "live" and not cambio.aplicado)
                or cambio.enviado_en < entrada.goal_vigente_desde
            ):
                continue
            dias = (hoy - cambio.enviado_en).days
            if cambio.direccion == "subir" and dias <= _DIAS_FRENO_SUBIDA:
                futura = ", fecha futura" if dias < 0 else ""
                base = _base_senal(entrada)
                return replace(
                    base,
                    resultado="frenado",
                    motivo="perdiendo_tras_subida",
                    m_actual=m_actual,
                    prioridad=_prioridad(m_actual, goal, entrada.ingreso_60d),
                    diagnostico=(
                        f"subida confirmada hace {dias} dias{futura}, "
                        f"u15={entrada.senal.u15} u60={entrada.senal.u60}"
                    ),
                )

    for cambio in entrada.cambios:
        if cambio.es_reversa or (entrada.mode == "live" and not cambio.aplicado):
            continue
        dias = (hoy - cambio.enviado_en).days
        if dias < config.dias_entre_cambios:
            futura = ", fecha futura" if dias < 0 else ""
            base = _base_senal(entrada)
            return replace(
                base,
                resultado="mantener",
                motivo="cooldown",
                m_actual=m_actual,
                prioridad=_prioridad(m_actual, goal, entrada.ingreso_60d),
                diagnostico=f"ultimo cambio hace {dias} dias{futura}",
            )

    if m_actual < goal - tol:
        return _subir(entrada, config=config, m_actual=m_actual, cotizaciones=cotizaciones)
    if m_actual > goal + tol:
        senal = entrada.senal
        if senal.estado == "sin_dato":
            base = _base_senal(entrada)
            return replace(
                base,
                resultado="mantener",
                motivo=f"ventas_sin_dato:{senal.submotivo}",
                m_actual=m_actual,
                prioridad=_prioridad(m_actual, goal, entrada.ingreso_60d),
            )
        if senal.estado == "perdiendo":
            return _bajar(entrada, config=config, m_actual=m_actual, cotizaciones=cotizaciones)
        base = _base_senal(entrada)
        return replace(
            base,
            resultado="mantener",
            motivo="sobre_goal_sin_perdida",
            m_actual=m_actual,
            prioridad=_prioridad(m_actual, goal, entrada.ingreso_60d),
        )
    base = _base_senal(entrada)
    return replace(
        base,
        resultado="mantener",
        motivo="en_tolerancia",
        m_actual=m_actual,
        prioridad=_prioridad(m_actual, goal, entrada.ingreso_60d),
    )


def _subir(
    entrada: EntradaDecision,
    *,
    config: ConfigPrecio,
    m_actual: Decimal,
    cotizaciones: tuple[CotizacionVerificada, ...],
) -> Decision | PideCotizacion:
    comp = entrada.escenario.componentes
    moneda = comp.p_actual.moneda
    escenario = entrada.escenario
    try:
        ref, fijo = derivar_ref_fijo(
            escenario.fee_detalles,
            comp.fees.valor,
            escenario.precio_cotizado.valor,
        )
    except ErrorObjetivo as exc:
        return _no_evaluado(entrada, exc.motivo)
    # r3-L1: sin estrella cruda ni regla-11-cruda; `margen_imposible` sale
    # de la maquina (rama de cero cotizaciones) y la regla 11 sale del
    # wrapper de abajo: un solo punto de control por camino.
    salida = paso(
        costo=comp.costo.valor,
        fijo=fijo,
        envio=comp.envio.valor,
        isr_tasa=escenario.isr_tasa,
        goal=entrada.goal,
        iva_divisor=escenario.iva_divisor,
        incluye_iva=escenario.precio_incluye_iva,
        ref=ref,
        moneda=moneda,
        tolerancia=config.tolerancia,
        cotizaciones=cotizaciones,
    )
    if isinstance(salida, PideCotizacion):
        freno = _frena_regla11(entrada, m_actual, salida.precio.valor)
        return freno if freno is not None else salida
    assert isinstance(salida, ResultadoObjetivo)
    if salida.resultado == "no_evaluado":
        assert salida.motivo is not None
        return _no_evaluado(entrada, salida.motivo)
    if salida.resultado == "goal_inalcanzable":
        base = _base_senal(entrada)
        return replace(
            base,
            resultado="goal_inalcanzable",
            motivo=salida.motivo,
            m_actual=m_actual,
            prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        )
    assert salida.precio is not None
    freno = _frena_regla11(entrada, m_actual, salida.precio.valor)
    if freno is not None:
        return freno
    p_objetivo = salida.precio.valor
    umbral = max(config.movimiento_min_pct * comp.p_actual.valor, _min_abs(config, moneda))
    if abs(p_objetivo - comp.p_actual.valor) < umbral:
        base = _base_senal(entrada)
        return replace(
            base,
            resultado="mantener",
            motivo="movimiento_minimo",
            m_actual=m_actual,
            p_objetivo=salida.precio,
            prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        )
    tope = piso_centavo(comp.p_actual.valor * (Decimal(1) + config.escalon_max_pct))
    p_aplicado = min(p_objetivo, tope)
    if p_objetivo <= comp.p_actual.valor or p_aplicado <= comp.p_actual.valor:
        return _no_evaluado(
            entrada,
            "escenario_incoherente",
            f"subir con p_objetivo={p_objetivo} p_aplicado={p_aplicado} <= P={comp.p_actual.valor}",
        )
    base = _base_senal(entrada)
    return replace(
        base,
        resultado="subir",
        motivo=None,
        m_actual=m_actual,
        p_objetivo=salida.precio,
        p_aplicado=Importe(p_aplicado, moneda),
        prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        aplicado=entrada.mode == "live",
    )


def _bajar(
    entrada: EntradaDecision,
    *,
    config: ConfigPrecio,
    m_actual: Decimal,
    cotizaciones: tuple[CotizacionVerificada, ...],
) -> Decision | PideCotizacion:
    """r1-A3: la bajada usa la misma maquina (S4 #4 «nunca menor que el goal»
    verificado por cotizacion real, no supuesto por linealidad)."""
    comp = entrada.escenario.componentes
    moneda = comp.p_actual.moneda
    escenario = entrada.escenario
    try:
        ref, fijo = derivar_ref_fijo(
            escenario.fee_detalles,
            comp.fees.valor,
            escenario.precio_cotizado.valor,
        )
    except ErrorObjetivo as exc:
        return _no_evaluado(entrada, exc.motivo)
    # r3b-2, opción (a): sin estrella cruda ni regla-11-cruda tampoco aquí;
    # `margen_imposible` sale de la máquina y la regla 11 del wrapper, igual
    # que en `_subir`: los dos caminos con un solo control, el del pedido.
    salida = paso(
        costo=comp.costo.valor,
        fijo=fijo,
        envio=comp.envio.valor,
        isr_tasa=escenario.isr_tasa,
        goal=entrada.goal,
        iva_divisor=escenario.iva_divisor,
        incluye_iva=escenario.precio_incluye_iva,
        ref=ref,
        moneda=moneda,
        tolerancia=config.tolerancia,
        cotizaciones=cotizaciones,
    )
    if isinstance(salida, PideCotizacion):
        freno = _frena_regla11(entrada, m_actual, salida.precio.valor)
        return freno if freno is not None else salida
    assert isinstance(salida, ResultadoObjetivo)
    if salida.resultado == "no_evaluado":
        assert salida.motivo is not None
        return _no_evaluado(entrada, salida.motivo)
    if salida.resultado == "goal_inalcanzable":
        base = _base_senal(entrada)
        return replace(
            base,
            resultado="goal_inalcanzable",
            motivo=salida.motivo,
            m_actual=m_actual,
            prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        )
    assert salida.precio is not None
    freno = _frena_regla11(entrada, m_actual, salida.precio.valor)
    if freno is not None:
        return freno
    p_goal = salida.precio.valor
    umbral = max(config.movimiento_min_pct * comp.p_actual.valor, _min_abs(config, moneda))
    if abs(p_goal - comp.p_actual.valor) < umbral:
        base = _base_senal(entrada)
        return replace(
            base,
            resultado="mantener",
            motivo="movimiento_minimo",
            m_actual=m_actual,
            p_objetivo=Importe(p_goal, moneda),
            prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        )
    piso = techo_centavo(comp.p_actual.valor * (Decimal(1) - config.escalon_max_pct))
    p_aplicado = max(p_goal, piso)
    if p_goal >= comp.p_actual.valor or p_aplicado >= comp.p_actual.valor:
        return _no_evaluado(
            entrada,
            "escenario_incoherente",
            f"bajar con p_objetivo={p_goal} p_aplicado={p_aplicado} >= P={comp.p_actual.valor}",
        )
    base = _base_senal(entrada)
    return replace(
        base,
        resultado="bajar",
        motivo=None,
        m_actual=m_actual,
        p_objetivo=Importe(p_goal, moneda),
        p_aplicado=Importe(p_aplicado, moneda),
        prioridad=_prioridad(m_actual, entrada.goal, entrada.ingreso_60d),
        aplicado=entrada.mode == "live",
    )


def repartir_cupo(
    candidatos: tuple[tuple[int, Decision], ...], *, cupo: int
) -> tuple[tuple[int, Decision], ...]:
    """S4 #12 puro: los primeros por prioridad pasan; el resto `mantener(cuota)`.

    Solo compiten y solo consumen cupo las decisiones `subir`/`bajar` con
    `aplicado=True` (r5-J1: un `frenado` o un `subir` de sombra no se
    quedan con el lugar). Todo lo demás (`no_evaluado`, `frenado`,
    `mantener(*)`, `goal_inalcanzable`, sombra) **pasa idéntico** (`is`),
    sin ocupar lugar y sin tocarse. Entre las que compiten: prioridad
    descendente, sin prioridad al fondo (regla 3: ausente no es cero,
    pero tampoco abre la puerta), desempate por `listing_id` ascendente,
    salida en el orden de entrada (r4-G2: si devolviera sueltas
    reordenadas, el `zip` con la entrada cruzaría publicaciones). El pase
    es **por posición** en la entrada, no por identidad de objeto
    (r4b-H1). Las que compiten con `p_actual` en monedas distintas son
    error de quien llama: `ValueError` (r5-J2).
    """
    if not isinstance(cupo, int) or isinstance(cupo, bool) or cupo < 0:
        raise ValueError(f"cupo invalido: {cupo!r}")
    compiten = [
        pos
        for pos, (_, decision) in enumerate(candidatos)
        if decision.resultado in ("subir", "bajar") and decision.aplicado
    ]
    ordenados = sorted(
        compiten,
        key=lambda pos: (
            candidatos[pos][1].prioridad is None,
            -(candidatos[pos][1].prioridad or Decimal(0)),
            candidatos[pos][0],
        ),
    )
    pasan = set(ordenados[:cupo])
    salida = []
    for pos, (listing_id, decision) in enumerate(candidatos):
        if pos in pasan:
            salida.append((listing_id, decision))
        elif pos in compiten:
            salida.append(
                (
                    listing_id,
                    replace(
                        decision,
                        resultado="mantener",
                        motivo="cuota",
                        aplicado=False,
                        p_aplicado=None,
                    ),
                )
            )
        else:
            salida.append((listing_id, decision))
    return tuple(salida)
