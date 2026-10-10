"""La unica politica de bid (BIDS 02, niveles_v3). PURA.

Sin IO, sin reloj, sin float: Decimal en toda la aritmetica y comparacion
por multiplicacion. `decide` no lee nada fuera del CasoHoja que recibe.
gamma_p/gamma_q vienen de evidencia.py y el bloque PAUSE de bid.py:
importados, jamas copiados. Las bandas, pasos y clamps son los de hoy
(espejos pineados en tests/test_arq_bids_m.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Literal

from app.optimizer.bid import _decide_pause
from app.optimizer.caso import POLITICA_BID, PRECISION, CasoHoja, Economia, EvidenciaNivel
from app.optimizer.evidencia import gamma_p, gamma_q

# Bandas y pasos: los de hoy (docs/CONTEXTO.md), sin tocar.
MULT_BAJA_FUERTE = Decimal("1.35")
MULT_BAJA_SUAVE = Decimal("1.15")
MULT_SUBIDA = Decimal("0.85")
PASO_BAJA_FUERTE = Decimal("-0.25")
PASO_BAJA_SUAVE = Decimal("-0.12")
PASO_SUBIDA = Decimal("0.15")
# Clamps de hoy (cola de bid.py): espejos pineados.
CLAMP_FACTOR_MIN = Decimal("-0.30")
CLAMP_FACTOR_MAX = Decimal("0.20")
MIN_DELTA_ABSOLUTO = Decimal("0.01")
# Efecto del ultimo cambio.
DIAS_EFECTO = 7  # dias para leer un cambio; es el cooldown de hoy con otro nombre (goals.COOLDOWN)
DIAS_GUARDA = 4  # la guarda de desplome ya discrimina con 4 dias (A4)
DIAS_SALIDA = 14  # invertir direccion sin 20 clics nuevos: salida por tiempo (elegido; sin medir)
CLICS_NUEVOS = 20  # clics al bid nuevo para repetir direccion (evidencia.MIN_CLICS_CPC de hoy)
RAZON_DESPLOME = Decimal("0.30")  # P2 y A4
RAZON_RETIENE = Decimal("0.70")  # recorte que "no costo trafico" (P2, A4)
RAZON_CRECE = Decimal("1.10")  # subida que "trajo trafico" (A7)
VOLUMEN_IMPRESIONES = 1000  # sin este filtro la guarda no distingue (P2)
VOLUMEN_CLICS = 20
PEDIDOS_GRUPO = 3  # minimo para que el ad group tenga veredicto (P1)
MATERIALIDAD_GRUPO = Decimal("0.25")  # 1/4 del gasto para concluir (injerto runner-c p_c2)

# Motivos (vocabulario cerrado; los dashboards los traducen en MOTIVOS_ES).
MOTIVO_REGRESO_DESPLOME = "regreso_por_desplome"
MOTIVO_ESPERANDO_EFECTO = "esperando_efecto"
MOTIVO_PIERDE_DINERO = "pierde_dinero"
MOTIVO_PIERDE_DINERO_FUERTE = "pierde_dinero_fuerte"
MOTIVO_GRUPO_SANGRA_VENDEDORA = "grupo_sangra_vendedora"
MOTIVO_VENDE_DENTRO_DEL_MARGEN = "vende_dentro_del_margen"
MOTIVO_BAJO_TARGET = "bajo_target"
MOTIVO_AZAR_LO_EXPLICA = "azar_lo_explica"
MOTIVO_VENTA_RECIENTE = "venta_reciente"
MOTIVO_GASTO_SIN_VENTA = "gasto_sin_venta"
MOTIVO_GASTO_SIN_VENTA_DOBLE = "gasto_sin_venta_doble"
MOTIVO_SIN_GASTO = "sin_gasto"
MOTIVO_GRUPO_SANGRA = "grupo_sangra"
MOTIVO_HOJA_DELGADA = "hoja_delgada_sin_dinero_que_mover"
MOTIVO_GRUPO_CUMPLE = "grupo_cumple"
MOTIVO_SIN_EVIDENCIA = "sin_evidencia"
MOTIVO_ESPERANDO_PRECIO_MEDIDO = "esperando_precio_medido"
MOTIVO_SIN_CLICS_NUEVOS = "sin_clics_nuevos"
MOTIVO_RECORTE_COSTO_TRAFICO = "recorte_costo_trafico"
MOTIVO_SUBIDA_SIN_TRAFICO = "subida_sin_trafico"
MOTIVO_PISO_APRENDIDO = "piso_aprendido"
MOTIVO_DATO_FALTANTE = "dato_faltante"
MOTIVO_SIN_PRECIO = "sin_precio"
MOTIVO_RANGO_BLOQUEA_AJUSTE = "rango_bloquea_ajuste"  # espejo de bid.py
MOTIVO_DELTA_BAJO_UMBRAL = "delta_bajo_umbral"  # espejo de bid.py

NivelQueJuzga = Literal["hoja", "ad_group"]
EstadoGrupo = Literal["sangra", "cumple", "sin_veredicto"]
FuentePrecio = Literal["medido_tras_cambio", "proyectado", "ventana", "ventana_madura"]

_CIEN = Decimal(100)
_UNO = Decimal(1)


@dataclass(frozen=True)
class Mantener:
    """No se mueve el bid. `motivo` dice por que, en el vocabulario cerrado. No se persiste:
    cuenta en notes.skips como hoy."""

    motivo: str


@dataclass(frozen=True)
class Mover:
    """Recorte o subida de un paso. Invariantes de construccion (solo `_paso` lo crea):
    `bid_nuevo` esta en [piso, techo], el cambio respeta el clamp por decision y nunca cruza
    el piso aprendido."""

    factor: Decimal
    bid_nuevo: Decimal
    motivo: str
    nivel: NivelQueJuzga


@dataclass(frozen=True)
class Regresar:
    """Volver al bid anterior al ultimo recorte. No lleva factor: el destino es un bid ya
    conocido."""

    bid_nuevo: Decimal
    motivo: str


@dataclass(frozen=True)
class Pausar:
    motivo: str


Veredicto = Mantener | Mover | Regresar | Pausar


@dataclass(frozen=True)
class Estimacion:
    """Derivada auditable (NO se congela: `estima` la re-deriva exacta del caso). La leen el
    tablero de ruido y la ficha de una decision."""

    cpc: Decimal | None
    fuente_precio: FuentePrecio | None
    acos_hoy_pct: Decimal | None  # CPC al bid de hoy / (conversion x ticket de la ventana pesada)
    p_sobre_equilibrio: Decimal | None
    p_sobre_suave: Decimal | None
    p_sobre_fuerte: Decimal | None
    p_bajo_target: Decimal | None
    estado_grupo: EstadoGrupo
    acos_grupo_pct: Decimal | None


@dataclass(frozen=True)
class _Fondo:
    """Numeros comunes del juicio de una vendedora. None = dato faltante (R18)."""

    ticket: Decimal
    base_mu: Decimal  # clics_pesados x cpc / ticket: mu al limite 100 %
    a_recorte: Decimal  # ceil(pedidos_pesados) + 1, entera para la gamma
    a_subida: Decimal  # floor(pedidos_pesados) + 1
    clics_pesados: Decimal
    pedidos_pesados: Decimal
    venta_pesada: Decimal
    acos_hoy_pct: Decimal


def decide(caso: CasoHoja) -> Veredicto:
    """La politica completa. Orden sellado (tabla-decision.md lleva el mismo numero de regla):

    0. PAUSE: identico a hoy (cortes maduros, umbral adaptativo, piso de costo). Gana a todo.

    1. Efecto del ultimo cambio (trayectoria):
       R1  ultimo cambio = recorte del motor, la hoja vendia, tenia volumen (>= 1000
           impresiones o >= 20 clics en 7 dias), dias_post >= DIAS_GUARDA y
           razon_trafico < RAZON_DESPLOME
           -> Regresar(ultimo.bid_antes, regreso_por_desplome)
       R2  dias_post < DIAS_EFECTO -> Mantener(esperando_efecto)

    2. Juicio. cpc = _precio_de_hoy(caso); grupo = estado_grupo(caso.grupo, caso.economia)
       Con pedidos en la ventana madura (propia.pedidos_crudos >= 1):
         p_sobre(limite) = gamma_p(ceil(pedidos_pesados) + 1,
                                 clics_pesados * cpc / (limite * ticket_propio))
            previa PLANA: solo datos propios; pedidos redondeados hacia arriba
            (beneficio de la duda).
         R3  p_sobre(equilibrio) >= confianza_recorte -> candidato de recorte, nivel hoja:
             fuerte (-25 %) si ademas p_sobre(1.35 x target) >= confianza; si no -12 %.
             Sin equilibrio (target que no vino del margen) R3 no dispara y el juicio sigue.
         R4  si no, y acos_hoy > target y grupo == "sangra" -> candidato -12 %, nivel ad_group
             (una vendedora que no pierde dinero con certeza NUNCA recibe -25 %).
         R5  si no, y p_sobre(1.15 x target) >= confianza -> Mantener(vende_dentro_del_margen)
         R6  si no: p_bajo = gamma_q(floor(pedidos_pesados) + 1, (clics_pesados +
                                     1/cvr_previa) * cpc / (0.85 * target * ticket_encogido))
             la previa (cuenta -> resto del ad group, K = 1, enteros sin pesos) FRENA la subida;
             p_bajo >= confianza_subida -> candidato +15 %, nivel hoja.
         R7  si no -> Mantener(azar_lo_explica)
       Sin pedidos:
         R8  pedidos_inmaduros >= 1 -> Mantener(venta_reciente)
         R9  gasto_crudo >= 2 x gasto_para_concluir -> candidato -25 %; >= 1 x ->
             candidato -12 % (nivel hoja)
         R10 gasto_crudo == 0 -> Mantener(sin_gasto)
         R11 grupo == "sangra" y gasto_crudo >= MATERIALIDAD_GRUPO x gasto_para_concluir
             -> candidato -12 %, nivel ad_group
         R11b grupo == "sangra" y gasto_crudo menor -> Mantener(hoja_delgada_sin_dinero_que_mover)
         R12 grupo == "cumple" -> Mantener(grupo_cumple)
         R13 si no -> Mantener(sin_evidencia)
       Cualquier metrica None que la regla consuma -> Mantener(dato_faltante). Sin CPC ->
       Mantener(sin_precio).

    3. Un movimiento se repite solo si el anterior tuvo el efecto buscado (sobre el candidato):
       R14 direccion contraria al ultimo cambio y clics_post < CLICS_NUEVOS y
           dias_post < DIAS_SALIDA -> Mantener(esperando_precio_medido)
       R15 misma direccion y clics_post < CLICS_NUEVOS -> Mantener(sin_clics_nuevos)
       R16 otro recorte y razon_trafico < RAZON_RETIENE -> Mantener(recorte_costo_trafico)
           otra subida y razon_trafico < RAZON_CRECE -> Mantener(subida_sin_trafico)
       R17 recorte que dejaria el bid en o debajo de trayectoria.piso_aprendido ->
           Mantener(piso_aprendido)

    4. _paso(caso.bid, candidato): clamps de hoy (factor en [-30 %, +20 %], [piso, techo],
       delta minimo, rango_bloquea_ajuste) -> Mover, o Mantener con el motivo del clamp.
    """
    target = caso.economia.target_acos_pct
    if not target.is_finite() or target <= 0:
        raise ValueError(f"target_acos_pct invalido: {target!r} (debe ser > 0)")

    # R0: PAUSE identico a hoy; solo un pause real interrumpe (un bloqueo informa y sigue).
    pausa, _motivo_bloqueado = _decide_pause(
        caso.pausa.cortes,
        caso.economia.plataforma.moneda,
        caso.pausa.umbral_clics,
        caso.pausa.gasto_minimo,
        target,
        caso.pausa.politica_economica,
        POLITICA_BID,
    )
    if pausa is not None:
        assert pausa.kind == "pause"
        assert pausa.motivo is not None
        return Pausar(pausa.motivo)

    ultimo = caso.trayectoria.ultimo
    if ultimo is not None:
        efecto = caso.trayectoria.efecto
        assert efecto is not None  # lo sella Trayectoria.__post_init__
        r1 = _evalua_r1(ultimo, efecto)
        if r1 is None:
            return Mantener(MOTIVO_DATO_FALTANTE)
        if r1:
            return Regresar(ultimo.bid_antes, MOTIVO_REGRESO_DESPLOME)
        if efecto.dias_post < DIAS_EFECTO:
            return Mantener(MOTIVO_ESPERANDO_EFECTO)

    with localcontext(prec=PRECISION):
        estado = estado_grupo(caso.grupo, caso.economia)
        propia = caso.propia
        crudos = propia.pedidos_crudos() if propia is not None else 0
        if crudos is None:
            return Mantener(MOTIVO_DATO_FALTANTE)
        if crudos >= 1:
            assert propia is not None
            fallo = _juzga_vendedora(caso, propia, estado)
            if isinstance(fallo, Mantener):
                return fallo
        else:
            fallo = _juzga_sin_pedidos(caso, estado)
            if isinstance(fallo, Mantener):
                return fallo
        factor, motivo, nivel = fallo
        if ultimo is not None:
            efecto = caso.trayectoria.efecto
            assert efecto is not None
            freno = _freno_trayectoria(caso, ultimo, efecto, factor)
            if freno is not None:
                return freno
        return _paso(caso.bid, factor, motivo, nivel, caso.trayectoria.piso_aprendido)


def _evalua_r1(ultimo, efecto) -> bool | None:
    """Tres valores: True regresa, False sigue a R2, None = dato faltante."""
    if ultimo.direccion != -1 or ultimo.origen != "motor":
        return False
    if efecto.dias_post < DIAS_GUARDA:
        return False
    if efecto.vendia is False:
        return False
    pre_impr = efecto.impresiones_pre7
    pre_clics = efecto.clics_pre7
    if (pre_impr is not None and pre_impr >= VOLUMEN_IMPRESIONES) or (
        pre_clics is not None and pre_clics >= VOLUMEN_CLICS
    ):
        volumen: bool | None = True
    elif pre_impr is None or pre_clics is None:
        volumen = None
    else:
        volumen = False
    if volumen is False:
        return False
    if efecto.impresiones_pre7 is None or efecto.impresiones_post is None:
        desplome: bool | None = None
    else:
        razon = efecto.razon_trafico()
        desplome = razon is not None and razon < RAZON_DESPLOME
    if desplome is False:
        return False
    if efecto.vendia is None or volumen is None or desplome is None:
        return None
    return True


def _juzga_vendedora(
    caso: CasoHoja, propia: EvidenciaNivel, estado: EstadoGrupo
) -> Mantener | tuple[Decimal, str, NivelQueJuzga]:
    cpc, _fuente = _precio_de_hoy(caso)
    if cpc is None:
        return Mantener(MOTIVO_SIN_PRECIO)
    fondo = _fondo_juicio(propia, cpc)
    if fondo is None:
        return Mantener(MOTIVO_DATO_FALTANTE)
    economia = caso.economia
    target = economia.target_acos_pct
    confianza = economia.plataforma.confianza_recorte
    equilibrio = economia.plataforma.equilibrio_acos_pct
    if equilibrio is not None and _p_sobre(fondo, equilibrio) >= confianza:
        if _p_sobre(fondo, MULT_BAJA_FUERTE * target) >= confianza:
            return (PASO_BAJA_FUERTE, MOTIVO_PIERDE_DINERO_FUERTE, "hoja")
        return (PASO_BAJA_SUAVE, MOTIVO_PIERDE_DINERO, "hoja")
    if fondo.acos_hoy_pct > target and estado == "sangra":
        return (PASO_BAJA_SUAVE, MOTIVO_GRUPO_SANGRA_VENDEDORA, "ad_group")
    if _p_sobre(fondo, MULT_BAJA_SUAVE * target) >= confianza:
        return Mantener(MOTIVO_VENDE_DENTRO_DEL_MARGEN)
    previa = _previa(caso.cuenta, caso.grupo, propia)
    if previa is None:
        return Mantener(MOTIVO_DATO_FALTANTE)
    if _p_bajo(fondo, cpc, target, previa) >= economia.plataforma.confianza_subida:
        return (PASO_SUBIDA, MOTIVO_BAJO_TARGET, "hoja")
    return Mantener(MOTIVO_AZAR_LO_EXPLICA)


def _juzga_sin_pedidos(
    caso: CasoHoja, estado: EstadoGrupo
) -> Mantener | tuple[Decimal, str, NivelQueJuzga]:
    inmaduros = caso.pedidos_inmaduros
    if inmaduros is not None and inmaduros >= 1:
        return Mantener(MOTIVO_VENTA_RECIENTE)
    if inmaduros is None:
        return Mantener(MOTIVO_DATO_FALTANTE)
    gasto = Decimal(0) if caso.propia is None else caso.propia.gasto_crudo()
    if gasto is None:
        return Mantener(MOTIVO_DATO_FALTANTE)
    concluir = caso.economia.plataforma.gasto_para_concluir
    if gasto >= 2 * concluir:
        return (PASO_BAJA_FUERTE, MOTIVO_GASTO_SIN_VENTA_DOBLE, "hoja")
    if gasto >= concluir:
        return (PASO_BAJA_SUAVE, MOTIVO_GASTO_SIN_VENTA, "hoja")
    if gasto == 0:
        return Mantener(MOTIVO_SIN_GASTO)
    if estado == "sangra":
        if gasto >= MATERIALIDAD_GRUPO * concluir:
            return (PASO_BAJA_SUAVE, MOTIVO_GRUPO_SANGRA, "ad_group")
        return Mantener(MOTIVO_HOJA_DELGADA)
    if estado == "cumple":
        return Mantener(MOTIVO_GRUPO_CUMPLE)
    return Mantener(MOTIVO_SIN_EVIDENCIA)


def _freno_trayectoria(caso, ultimo, efecto, factor: Decimal) -> Mantener | None:
    misma = (-1 if factor < 0 else 1) == ultimo.direccion
    if not misma:
        if efecto.clics_post is None:
            return Mantener(MOTIVO_DATO_FALTANTE)
        if efecto.clics_post < CLICS_NUEVOS and efecto.dias_post < DIAS_SALIDA:
            return Mantener(MOTIVO_ESPERANDO_PRECIO_MEDIDO)
    else:
        if efecto.clics_post is None:
            return Mantener(MOTIVO_DATO_FALTANTE)
        if efecto.clics_post < CLICS_NUEVOS:
            return Mantener(MOTIVO_SIN_CLICS_NUEVOS)
        if efecto.impresiones_pre7 is None or efecto.impresiones_post is None:
            return Mantener(MOTIVO_DATO_FALTANTE)
        razon = efecto.razon_trafico()
        if razon is not None:
            if factor < 0 and razon < RAZON_RETIENE:
                return Mantener(MOTIVO_RECORTE_COSTO_TRAFICO)
            if factor > 0 and razon < RAZON_CRECE:
                return Mantener(MOTIVO_SUBIDA_SIN_TRAFICO)
    if factor < 0:
        piso = caso.trayectoria.piso_aprendido
        bid = caso.bid.valor
        if piso is not None and bid is not None and bid * (_UNO + factor) <= piso:
            return Mantener(MOTIVO_PISO_APRENDIDO)
    return None


def _fondo_juicio(propia: EvidenciaNivel, cpc: Decimal) -> _Fondo | None:
    pedidos = propia.pedidos_pesados()
    clics = propia.clics_pesados()
    venta = propia.venta_pesada()
    if pedidos is None or clics is None or venta is None or venta <= 0 or pedidos <= 0:
        return None
    ticket = venta / pedidos
    return _Fondo(
        ticket=ticket,
        base_mu=clics * cpc / ticket,
        a_recorte=Decimal(int(pedidos.to_integral_value(rounding=ROUND_CEILING)) + 1),
        a_subida=Decimal(int(pedidos.to_integral_value(rounding=ROUND_FLOOR)) + 1),
        clics_pesados=clics,
        pedidos_pesados=pedidos,
        venta_pesada=venta,
        acos_hoy_pct=_CIEN * cpc * clics / venta,
    )


def _p_sobre(fondo: _Fondo, limite_pct: Decimal) -> Decimal:
    return gamma_p(fondo.a_recorte, fondo.base_mu / (limite_pct / _CIEN))


def _p_bajo(
    fondo: _Fondo,
    cpc: Decimal,
    target: Decimal,
    previa: tuple[Decimal | None, Decimal | None],
) -> Decimal:
    cvr_previa, aov_previa = previa
    if cvr_previa is None or aov_previa is None:
        clics_sub = fondo.clics_pesados
        ticket_sub = fondo.ticket
    else:
        clics_sub = fondo.clics_pesados + _UNO / cvr_previa
        ticket_sub = (fondo.venta_pesada + aov_previa) / (fondo.pedidos_pesados + _UNO)
    return gamma_q(fondo.a_subida, clics_sub * cpc / ((MULT_SUBIDA * target / _CIEN) * ticket_sub))


def _previa(
    cuenta: EvidenciaNivel | None,
    grupo: EvidenciaNivel | None,
    propia: EvidenciaNivel,
) -> tuple[Decimal | None, Decimal | None] | None:
    """Pliegue cuenta -> resto del ad group, K = 1, enteros sin pesos.

    (None, None) si la cuenta esta vacia (conocida): la subida usa previa
    plana, como el prototipo. None si falta cualquier insumo (veneno).
    """
    if cuenta is None or grupo is None:
        return None
    ped_c = cuenta.pedidos_crudos()
    cli_c = cuenta.clics_crudos()
    ven_c = cuenta.venta_cruda()
    ped_g = grupo.pedidos_crudos()
    cli_g = grupo.clics_crudos()
    ven_g = grupo.venta_cruda()
    ped_p = propia.pedidos_crudos()
    cli_p = propia.clics_crudos()
    ven_p = propia.venta_cruda()
    crudos = (ped_c, cli_c, ven_c, ped_g, cli_g, ven_g, ped_p, cli_p, ven_p)
    if any(c is None for c in crudos):
        return None
    assert ped_c is not None and cli_c is not None and ven_c is not None
    assert ped_g is not None and cli_g is not None and ven_g is not None
    assert ped_p is not None and cli_p is not None and ven_p is not None
    if ped_c <= 0 or cli_c <= 0:
        return (None, None)
    ped_r = ped_g - ped_p
    cli_r = cli_g - cli_p
    ven_r = ven_g - ven_p
    if ped_r < 0 or cli_r < 0 or ven_r < 0:
        return None
    cvr_cuenta = Decimal(ped_c) / Decimal(cli_c)
    aov_cuenta = ven_c / Decimal(ped_c)
    if cli_r > 0:
        cvr = Decimal(ped_r + 1) / (Decimal(cli_r) + _UNO / cvr_cuenta)
        aov = (ven_r + aov_cuenta) / Decimal(ped_r + 1)
        return (cvr, aov)
    return (cvr_cuenta, aov_cuenta)


def estima(caso: CasoHoja) -> Estimacion:
    """Los numeros detras del veredicto, para pantallas y auditoria. Misma aritmetica que `decide`
    (comparten los privados); un caso sin datos devuelve campos None, jamas levanta."""
    with localcontext(prec=PRECISION):
        cpc, fuente = _precio_de_hoy(caso)
        estado = estado_grupo(caso.grupo, caso.economia)
        propia = caso.propia
        p_eq = p_suave = p_fuerte = p_bajo = acos_hoy = None
        if propia is not None and (propia.pedidos_crudos() or 0) >= 1 and cpc is not None:
            fondo = _fondo_juicio(propia, cpc)
            if fondo is not None:
                target = caso.economia.target_acos_pct
                equilibrio = caso.economia.plataforma.equilibrio_acos_pct
                if equilibrio is not None and equilibrio > 0:
                    p_eq = _p_sobre(fondo, equilibrio)
                    p_fuerte = _p_sobre(fondo, MULT_BAJA_FUERTE * target)
                p_suave = _p_sobre(fondo, MULT_BAJA_SUAVE * target)
                previa = _previa(caso.cuenta, caso.grupo, propia)
                if previa is not None:
                    p_bajo = _p_bajo(fondo, cpc, target, previa)
                acos_hoy = fondo.acos_hoy_pct
        return Estimacion(
            cpc=cpc,
            fuente_precio=fuente,
            acos_hoy_pct=acos_hoy,
            p_sobre_equilibrio=p_eq,
            p_sobre_suave=p_suave,
            p_sobre_fuerte=p_fuerte,
            p_bajo_target=p_bajo,
            estado_grupo=estado,
            acos_grupo_pct=_acos_grupo(caso.grupo),
        )


def _acos_grupo(grupo: EvidenciaNivel | None) -> Decimal | None:
    if grupo is None:
        return None
    gasto = grupo.gasto_pesado()
    venta = grupo.venta_pesada()
    if gasto is None or venta is None or venta <= 0:
        return None
    return _CIEN * gasto / venta


def estado_grupo(grupo: EvidenciaNivel | None, economia: Economia) -> EstadoGrupo:
    """Regla de grupo del dueno, una sola definicion (P1): con >= PEDIDOS_GRUPO pedidos
    crudos, 'sangra' si gasto_pesado > 1.15 x target x venta_pesada y 'cumple' si gasto_pesado
    <= target x venta_pesada; con 0 pedidos, 'sangra' si gasto_crudo >= gasto_para_concluir.
    Todo lo demas, incluido None o veneno, 'sin_veredicto'. Comparacion por multiplicacion,
    nunca division."""
    if grupo is None:
        return "sin_veredicto"
    crudos = grupo.pedidos_crudos()
    if crudos is None:
        return "sin_veredicto"
    if crudos >= PEDIDOS_GRUPO:
        gasto = grupo.gasto_pesado()
        venta = grupo.venta_pesada()
        if gasto is None or venta is None or venta <= 0:
            return "sin_veredicto"
        target = economia.target_acos_pct
        if gasto > MULT_BAJA_SUAVE * target * venta / _CIEN:
            return "sangra"
        if gasto <= target * venta / _CIEN:
            return "cumple"
        return "sin_veredicto"
    if crudos == 0:
        gasto = grupo.gasto_crudo()
        if gasto is None:
            return "sin_veredicto"
        if gasto >= economia.plataforma.gasto_para_concluir:
            return "sangra"
    return "sin_veredicto"


def _precio_de_hoy(caso: CasoHoja) -> tuple[Decimal | None, FuentePrecio | None]:
    """CPC al bid vigente. Escalera (A4 c: error mediano 6 % al escalar; 26 % con el CPC
    del ad group):
    1. clics_post >= CLICS_NUEVOS   -> gasto_post / clics_post  (medido_tras_cambio)
    2. hay cambio y clics_pre > 0   -> (gasto_pre / clics_pre) * bid_despues / bid_antes
       (proyectado)
    3. sin cambio y precio.clics > 0         -> precio.gasto / precio.clics       (ventana)
    4. propia con clics                      -> gasto_crudo / clics crudos        (ventana_madura)
    5. nada                                  -> (None, None)

    El paso 2 exige bids distintos: un ajuste de campana (bid_antes == bid_despues) no proyecta.
    Cada paso exige gasto > 0, como el prototipo: un CPC de 0 con clics romperia la gamma.
    """
    ultimo = caso.trayectoria.ultimo
    if ultimo is not None:
        efecto = caso.trayectoria.efecto
        assert efecto is not None
        if (
            efecto.clics_post is not None
            and efecto.clics_post >= CLICS_NUEVOS
            and efecto.gasto_post is not None
            and efecto.gasto_post > 0
        ):
            return (efecto.gasto_post / Decimal(efecto.clics_post), "medido_tras_cambio")
        if (
            ultimo.bid_despues != ultimo.bid_antes
            and ultimo.bid_antes > 0
            and efecto.clics_pre is not None
            and efecto.clics_pre > 0
            and efecto.gasto_pre is not None
            and efecto.gasto_pre > 0
        ):
            return (
                efecto.gasto_pre
                / Decimal(efecto.clics_pre)
                * ultimo.bid_despues
                / ultimo.bid_antes,
                "proyectado",
            )
    else:
        precio = caso.precio
        if (
            precio.clics is not None
            and precio.clics > 0
            and precio.gasto is not None
            and precio.gasto > 0
        ):
            return (precio.gasto / Decimal(precio.clics), "ventana")
    propia = caso.propia
    if propia is not None:
        clics = propia.clics_crudos()
        gasto = propia.gasto_crudo()
        if clics is not None and clics > 0 and gasto is not None and gasto > 0:
            return (gasto / Decimal(clics), "ventana_madura")
    return (None, None)


def _paso(
    bid,
    factor: Decimal,
    motivo: str,
    nivel: NivelQueJuzga,
    piso_aprendido: Decimal | None,
) -> Mover | Mantener:
    """Cola de clamps de hoy: piso aprendido, delta minimo, rango. Solo aqui nace un Mover."""
    valor = bid.valor
    if valor is None or valor <= 0:
        return Mantener(MOTIVO_DATO_FALTANTE)
    factor_c = min(max(factor, CLAMP_FACTOR_MIN), CLAMP_FACTOR_MAX)
    nuevo = valor * (_UNO + factor_c)
    nuevo = min(max(nuevo, bid.piso), bid.techo)
    if factor_c < 0 and piso_aprendido is not None and nuevo <= piso_aprendido:
        return Mantener(MOTIVO_PISO_APRENDIDO)
    if abs(nuevo - valor) < MIN_DELTA_ABSOLUTO:
        return Mantener(MOTIVO_DELTA_BAJO_UMBRAL)
    delta = nuevo - valor
    direccion_ok = delta < 0 if factor_c < 0 else delta > 0
    magnitud_ok = delta <= CLAMP_FACTOR_MAX * valor and (
        -delta <= -CLAMP_FACTOR_MIN * valor
    )  # cambio final dentro de [-30 %, +20 %] (multiplicacion, sin division)
    if not (direccion_ok and magnitud_ok):
        return Mantener(MOTIVO_RANGO_BLOQUEA_AJUSTE)
    return Mover(factor, nuevo, motivo, nivel)
