"""Meta publicitaria por producto (AUTO-03, bloque C): tipos y resolutor puros.

Spec: docs/superpowers/specs/2026-10-24-decisiones-producto-design.md, con los
supuestos S1..S18 de su seccion "Supuestos de construccion". Sin SQL, HTTP ni
reloj: la lectura por lote (app/productos_io.py, bloque D) construye entradas
validas y este modulo decide. La pantalla y el replay leen la evaluacion
persistida (como_json) y, para revalidar, corren este mismo resolutor sobre la
entrada congelada que la evaluacion trae dentro.

- Meta (S2): min(fraccion x margen, goals.MARGEN_BANDA_MAX), SIN el piso de 10
  del margen de plataforma (8 % x 0.5 = 4 %, no 10 %). El margen es
  goals.MedicionMargen con los guards de producto
  (goals.motivo_dato_invalido con MARGEN_DIAS_MIN_FAMILIA); margen cero o
  negativo deja motivo, jamas meta cero (regla 3).
- Halo (S7): ventas atribuidas de otro SKU en la ventana del halo impiden la
  meta (halo_sin_margen). Solo cuentan las fechas con fila; el tramo de menos
  de 30 dias se acepta sin madurez y deja el motivo halo_sin_madurar.
- Identidad (S5): los impedimentos que el adaptador encontro (p. ej.
  sku_distinto_en_asin) impiden la meta.
- Paso por hoja (S2). Dos versiones seleccionables por
  `ads_producto_economia_<plat>`: ausente o `auto03` conserva la rampa de
  0.5 y la convergencia de AUTO-03; `producto_directo_v1` (ORBIT 07 M2)
  aplica la meta calculada DIRECTA desde el primer ciclo elegible y en cada
  cambio posterior del margen. No hay rampa para este peldano (excepcion
  explicita): desde un target previo de 25 % y una meta nueva de 4 %, la
  hoja aplica 4 %, no 24.5 %. Si el producto no tiene economia apta, la
  version nueva resuelve la cascada completa de respaldo en ese ciclo: el
  peldano de producto no aporta un valor en transicion y el target no se
  interpola hacia otra economia. Los pasos de cambio de PUJA siguen
  intactos en ambas versiones.
- Pausa (H.2): con la ventana [D-60, D-30) completa y madura (S6), compras y
  ventas totales en cero observado y clics y gasto en o sobre los limites
  congelados (S8), una PropuestaPausa por AnuncioElegible (S4). Una venta de
  otro SKU, una fecha desconocida, un impedimento de identidad o un impulso
  abierto del producto dejan motivo y ninguna propuesta. La politica v1 (antes
  de H) no tenia esta regla: el replay de una evaluacion v1 la corre sin ella.
- Venta reciente del producto (M4, politica v3): antes de proponer la pausa se
  mira la ventana reciente inmadura [D-30, D-1] de la fuente de productos, que
  reobserva cada fecha 30 dias. Una compra o venta observada ahi (aunque sea de
  otro SKU) deja la pausa en espera con el ancla en la fecha mas antigua de esa
  venta y vencimiento ancla + 30 dias; una reobservacion o una venta posterior
  no mueven el ancla. La ventana y la madura son contiguas: el dia de madurez
  la venta sigue en la reciente (la pausa espera) y al dia siguiente entra a
  [D-60, D-30), donde cuenta como venta de la ventana. No reutiliza el
  vencimiento de diez dias de la hoja. La politica v2 conserva el replay
  historico sin esta espera.
- Simulacion (H.8): simula_pausa corre la misma regla con otros limites sobre
  las entradas de hoy y, para el costo de equivocarse, sobre las de hace 30
  dias.
- Interruptores: ads_producto_meta_<plat> y ads_producto_pausa_<plat>
  (ausente = off; shadow | live). Limites de la pausa (S8):
  ads_producto_pausa_clics_<plat> y ads_producto_pausa_gasto_<plat>, con
  cortes.F_PAUSE y bid.PAUSE_COST_MIN por omision; los pisos se validan en la
  frontera (app/config_write.py), no aqui.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, get_args, get_type_hints

from app.optimizer import goals
from app.optimizer.bid import PAUSE_COST_MIN, PLATAFORMAS_MONEDA
from app.optimizer.cortes import F_PAUSE

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]
Modo = Literal["off", "shadow", "live"]

PLATAFORMAS: tuple[str, ...] = get_args(Plataforma)

# Version de la politica, congelada en cada evaluacion: el replay sabe con que
# reglas se calculo una evaluacion vieja. v1 = meta (C/D); v2 = meta y pausa (H);
# v3 = pausa con la espera de venta reciente inmadura del producto (ORBIT 07 M4).
POLITICA_PRODUCTO = "v3"
POLITICAS: tuple[str, ...] = ("v1", "v2", "v3")

MOTIVO_PAUSA = "producto_sin_ventas"
# M4: una venta del producto con menos de 30 dias todavia puede cambiar de
# atribucion; la pausa espera con un ancla fija.
MOTIVO_VENTA_RECIENTE = "venta_reciente_producto"

# ORBIT 07 M2: version de la economia del producto que se congela en la
# decision (`target_snapshot.version`) y se relee al revalidar la cola. La
# evaluacion persistida conserva su propia version (POLITICA_PRODUCTO).
POLITICA_ECONOMIA = "producto_directo_v1"
# AUTO-03: la version anterior. El setting ads_producto_economia_<plat>
# selecciona la regla sin regresar el codigo; ausente = auto03 (desplegar no
# cambia la meta de AUTO-03 hasta que el dueno elija la version nueva).
ECONOMIA_AUTO03 = "auto03"
POLITICAS_ECONOMIA: tuple[str, ...] = (ECONOMIA_AUTO03, POLITICA_ECONOMIA)

# S17: un anuncio que regresaste no se vuelve a proponer en 7 dias.
GRACIA_REGRESADO = dt.timedelta(days=7)

MADUREZ_HALO = dt.timedelta(days=30)

# M4: la fuente de productos reobserva cada fecha hasta los 30 dias (AUTO-03
# E.4). Una venta con menos de 30 dias aun puede cambiar. La ventana reciente
# inmadura es [D-30, D-1]; el dia de madurez (D-30) sigue en la reciente para
# que no haya hueco con la ventana madura [D-60, D-30).
MADUREZ_PRODUCTO = dt.timedelta(days=30)

# Ventana de la pausa (S6/S7): [D-60, D-30) UTC, 30 fechas maduras.
DIAS_PAUSA_DESDE = 60
DIAS_PAUSA_HASTA = 30

# Vocabulario cerrado. Los cinco primeros son los de goals.motivo_dato_invalido
# y sin_fraccion; los demas son propios del producto.
MOTIVOS_PRODUCTO: dict[str, str] = {
    "sin_fraccion": "sin la fracción del margen configurada para la plataforma",
    "sin_margen": "sin margen medido en la ventana",
    "cobertura_baja": "cobertura de costos bajo el minimo",
    "ventana_corta": "pocos dias con venta en la ventana",
    "ledger_rancio": "las ventas del libro llevan más de 3 días sin actualizarse",
    "margen_no_positivo": "margen medido cero o negativo: no es una meta de cero",
    "halo_sin_margen": "ventas atribuidas de otro SKU: su margen no es el del producto",
    "sku_distinto_en_asin": "otro SKU con gasto en el ASIN del producto",
    "halo_sin_madurar": "el tramo del halo de menos de 30 dias todavia puede cambiar",
    "sin_grupo_apto": "ningun grupo exclusivo y apto: la meta no llega a ninguna puja",
    "grupo_no_apto": "el grupo no es exclusivo del producto o no esta comprobado",
    "ads_con_metrica_ausente": "una fecha del reporte trae una metrica ausente: no se puede sumar",
    # Actividad de la ventana de la pausa (S6); la meta no las usa.
    "fecha_desconocida": "una fecha sin fila ni cobertura demostrada de la ingesta",
    "fecha_inmadura": "una fecha todavia sin su reobservacion de 30 dias",
    "sin_filas_en_ventana": "el producto no tiene ninguna fila en la ventana",
    # Regla de la pausa (H.2).
    "vendio_en_ventana": "el producto vendio en la ventana de la pausa",
    "venta_otro_sku": "ventas atribuidas de otro SKU en la ventana: no son cero ventas",
    "venta_reciente_producto": "una venta reciente del producto aun no madura: la pausa espera",
    "bajo_limites": "no llega a los clics o al gasto minimos de la pausa",
    "impulso_abierto": "un impulso abierto del producto: sus anuncios quedan bajo el vigia",
    "sin_anuncios_elegibles": "ningun anuncio del producto esta al alcance de la pausa",
    # Por que un anuncio ENABLED del producto queda fuera del alcance (S4, S17).
    "goal_no_live": "la meta de su campaña no está encendida en vivo",
    "alta_en_ventana": "el anuncio no existia antes de la ventana",
    "sin_actividad_en_ventana": "el anuncio no aparece en las filas de la ventana",
    "clave_en_vuelo": "el anuncio ya tiene una pausa en la cola o un veto vigente",
    "regresado_por_ti": "lo regresaste hace menos de 7 dias",
    # Simulacion (H.8).
    "sin_historia": "la ventana es anterior al primer dato del reporte de productos: no es cero",
}


@dataclass(frozen=True)
class Dinero:
    valor: Decimal
    moneda: Moneda


@dataclass(frozen=True)
class Producto:
    plataforma: Plataforma
    product_id: int


@dataclass(frozen=True)
class Ventana:
    desde: dt.date
    hasta_exclusivo: dt.date

    def contiene(self, fecha: dt.date) -> bool:
        return self.desde <= fecha < self.hasta_exclusivo


@dataclass(frozen=True)
class SinDato:
    motivos: tuple[str, ...]


@dataclass(frozen=True)
class DiaProducto:
    """Una fecha con fila del reporte de productos, ya colapsada a la ultima
    observacion y sumada por producto (S5). Una fecha sin fila no llega."""

    fecha: dt.date
    gasto: Dinero
    clics: int
    compras: Decimal
    ventas: Dinero
    ventas_mismo_sku: Dinero


@dataclass(frozen=True)
class AdsCompletos:
    """Actividad del producto en una ventana con TODAS sus fechas conocidas y
    maduras: con fila, o en cero observado por la cobertura S6. Cada fecha
    deja su referencia (la fila o el reporte que la cubre)."""

    ventana: Ventana
    gasto: Dinero
    clics: int
    compras_total: Decimal
    ventas_total: Dinero
    ventas_mismo_sku: Dinero
    referencias_por_fecha: tuple[str, ...]


@dataclass(frozen=True)
class VentaRecienteProducto:
    """M4: la venta inmadura que deja la pausa en espera. `ancla` es la fecha
    mas antigua con compra o venta observada en [D-30, D-1]; `vence_el` es
    ancla + 30 dias (la madurez de la fuente) y `libera_el` es el primer dia en
    que la venta ya esta en [D-60, D-30): recien ahi la pausa vuelve a decidir.
    Una reobservacion o una venta posterior no mueven el ancla."""

    ventana: Ventana
    ancla: dt.date
    vence_el: dt.date
    libera_el: dt.date
    compras: Decimal
    ventas: Dinero


@dataclass(frozen=True)
class GrupoAptoParaMeta:
    """Ad group exclusivo del producto: todos sus product ads no archivados
    tienen listing del mismo producto. `hojas` son sus keywords y product
    targets ENABLED, y `campana_id` resuelve su goal."""

    grupo_id: int
    campana_id: int
    hojas: tuple[int, ...]
    ventana_evidencia_hojas: Ventana
    referencias_identidad_y_halo: tuple[str, ...]
    huella_alcance: str


@dataclass(frozen=True)
class AnuncioElegible:
    """Product ad al alcance de una pausa del producto (S4): ENABLED, con
    identidad, goal resuelto enabled y live, alta anterior a la ventana, su
    adId en ad_ids de la ventana (pertenencia, nunca reparto), fuera de un
    impulso abierto, sin clave en vuelo en la cola y sin regreso reciente
    (S17)."""

    ad_entity_id: int
    listing_id: int
    grupo_id: int
    huella_alcance: str


@dataclass(frozen=True)
class AnuncioExcluido:
    """Product ad ENABLED del producto que queda fuera del alcance de la pausa
    con su motivo. `retiro_id` es el retiro abierto de un anuncio reactivado
    a mano en Amazon (S17): TX3 lo cierra y su gracia corre desde ese ciclo."""

    ad_entity_id: int
    motivo: str
    retiro_id: int | None = None


@dataclass(frozen=True)
class PropuestaPausa:
    anuncio: AnuncioElegible
    motivo: Literal["producto_sin_ventas"] = MOTIVO_PAUSA

    def como_json(self) -> dict:
        return _a_json(self)

    @classmethod
    def desde_json(cls, datos: Mapping) -> PropuestaPausa:
        return _de_json(datos, cls)


@dataclass(frozen=True)
class LimitesPausa:
    clics_min: int
    gasto_min: Dinero


@dataclass(frozen=True)
class EntradaProducto:
    producto: Producto
    decidido_el: dt.datetime
    margen: goals.MedicionMargen | SinDato
    fraccion: Decimal | SinDato
    ventana_halo: Ventana
    dias_halo: tuple[DiaProducto, ...]
    actividad_pausa: AdsCompletos | SinDato
    anuncios: tuple[AnuncioElegible, ...]
    grupos_aptos_para_meta: tuple[GrupoAptoParaMeta, ...]
    impedimentos: tuple[str, ...]
    limites_pausa: LimitesPausa
    # Bloque H, con valor por omision para leer las entradas congeladas antes.
    # gasto_actuable: el de las filas de la ventana cuyos ad_ids son todos
    # anuncios elegibles (pertenencia, nunca reparto).
    excluidos: tuple[AnuncioExcluido, ...] = ()
    bloqueos_pausa: tuple[str, ...] = ()
    gasto_actuable: Dinero | None = None
    # M4: la ventana reciente y sus dias, leidos del reporte de productos hasta
    # D-1 (no del watermark de hojas). Las entradas congeladas antes no los
    # traen; la regla cae a dias_halo.
    ventana_venta_reciente: Ventana | None = None
    dias_recientes: tuple[DiaProducto, ...] = ()

    def como_json(self) -> dict:
        return _a_json(self)


@dataclass(frozen=True)
class MetaReferencia:
    porcentaje: Decimal
    grupos_elegibles: tuple[GrupoAptoParaMeta, ...]


@dataclass(frozen=True)
class EvaluacionProducto:
    """La entrada viaja dentro: la evaluacion persistida congela los insumos,
    los limites de la pausa y la politica con que se calculo."""

    entrada: EntradaProducto
    meta: MetaReferencia | SinDato
    motivos: tuple[str, ...]
    politica: str
    pausas: tuple[PropuestaPausa, ...] = ()
    motivos_pausa: tuple[str, ...] = ()

    @property
    def producto(self) -> Producto:
        return self.entrada.producto

    def como_json(self) -> dict:
        return _a_json(self)

    @classmethod
    def desde_json(cls, datos: Mapping) -> EvaluacionProducto:
        return _de_json(datos, cls)


@dataclass(frozen=True)
class ModosProducto:
    meta: Modo
    pausa: Modo


def resolver_producto(
    entrada: EntradaProducto, *, politica: str = POLITICA_PRODUCTO
) -> EvaluacionProducto:
    """La meta del producto y sus pausas, o los motivos por los que no las
    tiene. `politica` solo cambia para el replay de una evaluacion vieja."""
    if politica not in POLITICAS:
        raise ValueError(f"politica de producto desconocida: {politica!r}")
    desconocidos = {
        *entrada.impedimentos,
        *entrada.bloqueos_pausa,
        *(x.motivo for x in entrada.excluidos),
    } - MOTIVOS_PRODUCTO.keys()
    if desconocidos:
        raise ValueError(f"motivos fuera del vocabulario: {sorted(desconocidos)}")
    fraccion = entrada.fraccion
    if isinstance(fraccion, Decimal) and not (fraccion.is_finite() and 0 < fraccion <= 1):
        raise ValueError(f"fraccion de margen invalida: {fraccion!r} (debe estar en (0, 1])")
    motivos = _motivos_sin_meta(entrada)
    informativos: list[str] = []
    if entrada.ventana_halo.hasta_exclusivo > entrada.decidido_el.date() - MADUREZ_HALO:
        informativos.append("halo_sin_madurar")
    if motivos:
        meta: MetaReferencia | SinDato = SinDato(motivos)
    else:
        assert isinstance(entrada.margen, goals.MedicionMargen)
        assert isinstance(entrada.fraccion, Decimal)
        assert entrada.margen.margen_neto_pct is not None
        derivado = entrada.fraccion * entrada.margen.margen_neto_pct
        meta = MetaReferencia(min(derivado, goals.MARGEN_BANDA_MAX), entrada.grupos_aptos_para_meta)
        if not entrada.grupos_aptos_para_meta:
            informativos.append("sin_grupo_apto")
    if politica == "v1":
        return EvaluacionProducto(entrada, meta, (*motivos, *informativos), politica)
    pausas, motivos_pausa = _regla_pausa(entrada, reciente=politica != "v2")
    return EvaluacionProducto(
        entrada, meta, (*motivos, *informativos), politica, pausas, motivos_pausa
    )


def ventana_venta_reciente(decidido_el: dt.datetime) -> Ventana:
    """M4: [D-30, D-1], contigua con la ventana madura [D-60, D-30). El dia de
    madurez (D-30) sigue en la reciente; al dia siguiente la venta entra a la
    madura y la pausa vuelve a decidir."""
    fecha = decidido_el.astimezone(dt.UTC).date()
    return Ventana(fecha - MADUREZ_PRODUCTO, fecha)


def venta_reciente_producto(entrada: EntradaProducto) -> VentaRecienteProducto | None:
    """M4: la venta inmadura mas antigua de [D-30, D-1], o None si no hay. Usa
    los dias recientes que el lector tomo del reporte de productos hasta D-1
    (no el watermark de hojas); una entrada congelada antes cae a dias_halo. Una
    compra o venta total observada (incluye la de otro SKU) deja la pausa en
    espera; la fecha mas antigua es el ancla, vence a los 30 dias y libera al
    dia siguiente, cuando entra a la ventana madura."""
    ventana = entrada.ventana_venta_reciente or ventana_venta_reciente(entrada.decidido_el)
    moneda = PLATAFORMAS_MONEDA[entrada.producto.plataforma]
    ancla: dt.date | None = None
    compras = Decimal(0)
    ventas = Decimal(0)
    for dia in sorted(entrada.dias_recientes or entrada.dias_halo, key=lambda x: x.fecha):
        if not ventana.contiene(dia.fecha):
            continue
        if dia.ventas.moneda != moneda or dia.ventas_mismo_sku.moneda != moneda:
            raise ValueError(f"dia {dia.fecha} en otra moneda que {moneda}")
        if dia.compras == 0 and dia.ventas.valor == 0:
            continue
        if ancla is None:
            ancla = dia.fecha
        compras += dia.compras
        ventas += dia.ventas.valor
    if ancla is None:
        return None
    return VentaRecienteProducto(
        ventana=ventana,
        ancla=ancla,
        vence_el=ancla + MADUREZ_PRODUCTO,
        libera_el=ancla + MADUREZ_PRODUCTO + dt.timedelta(days=1),
        compras=compras,
        ventas=Dinero(ventas, moneda),
    )


def motivos_economicos_pausa(entrada: EntradaProducto, *, reciente: bool = True) -> tuple[str, ...]:
    """La regla de la pausa sin el alcance de anuncios (H.2): vacio si el
    producto gasto sin vender, con la identidad comprobada y sin impulso
    abierto. La regla completa (_regla_pausa) exige ademas anuncios
    elegibles. Con `reciente` (v3), una venta inmadura de [D-30, D-1] deja la
    pausa en espera; v2 conserva el replay sin esa espera."""
    motivos = list(entrada.bloqueos_pausa)
    motivos.extend(m for m in entrada.impedimentos if m == "sku_distinto_en_asin")
    actividad = entrada.actividad_pausa
    limites = entrada.limites_pausa
    if isinstance(actividad, SinDato):
        motivos.extend(actividad.motivos)
    elif actividad.ventana != ventana_pausa(entrada.decidido_el):
        motivos.append("fecha_inmadura")
    else:
        if actividad.gasto.moneda != limites.gasto_min.moneda:
            raise ValueError(
                f"gasto en {actividad.gasto.moneda} y limite en {limites.gasto_min.moneda}"
            )
        if actividad.ventas_total.valor > actividad.ventas_mismo_sku.valor:
            motivos.append("venta_otro_sku")
        elif actividad.compras_total != 0 or actividad.ventas_total.valor != 0:
            motivos.append("vendio_en_ventana")
        elif actividad.clics < limites.clics_min or actividad.gasto.valor < limites.gasto_min.valor:
            motivos.append("bajo_limites")
        elif reciente and venta_reciente_producto(entrada) is not None:
            motivos.append(MOTIVO_VENTA_RECIENTE)
    return tuple(dict.fromkeys(motivos))


def _regla_pausa(
    entrada: EntradaProducto, *, reciente: bool = True
) -> tuple[tuple[PropuestaPausa, ...], tuple[str, ...]]:
    motivos = motivos_economicos_pausa(entrada, reciente=reciente)
    if motivos:
        return (), motivos
    if not entrada.anuncios:
        return (), ("sin_anuncios_elegibles",)
    return tuple(PropuestaPausa(a) for a in entrada.anuncios), ()


def _motivos_sin_meta(entrada: EntradaProducto) -> tuple[str, ...]:
    motivos = list(entrada.impedimentos)
    if isinstance(entrada.fraccion, SinDato):
        motivos.extend(entrada.fraccion.motivos)
    margen = entrada.margen
    if isinstance(margen, SinDato):
        motivos.extend(margen.motivos)
    elif margen.ventana_hasta is None:
        motivos.append("sin_margen")
    else:
        hoy = goals.hoy_de_ventana(margen.ventana_hasta)
        motivo = goals.motivo_dato_invalido(margen, hoy, dias_min=goals.MARGEN_DIAS_MIN_FAMILIA)
        if motivo is not None:
            motivos.append(motivo)
        elif margen.margen_neto_pct <= 0:
            motivos.append("margen_no_positivo")
    if _halo(entrada) > 0:
        motivos.append("halo_sin_margen")
    return tuple(dict.fromkeys(motivos))


def _halo(entrada: EntradaProducto) -> Decimal:
    """Ventas atribuidas de otro SKU en la ventana del halo (S7). Un dia con
    mas ventas del mismo SKU que totales no compensa el halo de otro dia."""
    moneda = PLATAFORMAS_MONEDA[entrada.producto.plataforma]
    total = Decimal(0)
    for dia in entrada.dias_halo:
        if not entrada.ventana_halo.contiene(dia.fecha):
            continue
        if dia.ventas.moneda != moneda or dia.ventas_mismo_sku.moneda != moneda:
            raise ValueError(f"dia {dia.fecha} en otra moneda que {moneda}")
        total += max(dia.ventas.valor - dia.ventas_mismo_sku.valor, Decimal(0))
    return total


def cumple_limites_pausa(entrada: EntradaProducto) -> bool:
    """El producto gasto sin vender en la ventana de la pausa: actividad
    completa (S6), compras y ventas totales en cero observado, clics y gasto en
    o sobre los limites congelados (S8) y sin una venta inmadura reciente
    (M4)."""
    actividad = entrada.actividad_pausa
    limites = entrada.limites_pausa
    return (
        isinstance(actividad, AdsCompletos)
        and actividad.compras_total == 0
        and actividad.ventas_total.valor == 0
        and actividad.clics >= limites.clics_min
        and actividad.gasto.valor >= limites.gasto_min.valor
        and venta_reciente_producto(entrada) is None
    )


@dataclass(frozen=True)
class PausaSimulada:
    producto: Producto
    gasto: Dinero
    clics: int
    anuncios: int


@dataclass(frozen=True)
class SimulacionPausa:
    """La pausa con otros limites, sin escribir (H.8). `productos` son los
    que hoy tendrian propuestas en [D-60, D-30). El costo de equivocarse corre
    la misma regla completa (con el alcance de anuncios) sobre [D-90, D-60):
    solo cuenta un producto que entonces habria tenido alguna propuesta, y
    suma lo que esos productos
    gastaron y vendieron despues, en [D-60, D-30): el gasto es observado, no
    una promesa de ahorro. `sin_cobertura_d7` cuenta los productos con alguna
    fecha de la ventana sin fila ni rango pedido guardado (S6, D.7);
    `sin_dato_despues`, los elegidos antes que hoy ya no tienen entrada o
    tienen una fecha con metrica ausente: no suman, nunca como cero."""

    limites: LimitesPausa
    ventana: Ventana
    productos: tuple[PausaSimulada, ...]
    gasto_total: Dinero
    anuncios_total: int
    sin_cobertura_d7: int
    ventana_anterior: Ventana
    productos_anteriores: tuple[int, ...]
    gasto_evitado: Dinero | SinDato
    ventas_perdidas: Dinero | SinDato
    sin_cobertura_d7_anterior: int
    sin_dato_despues: int
    primer_dato: dt.date | None

    def como_json(self) -> dict:
        return _a_json(self)


def simula_pausa(
    entradas_hoy: Sequence[EntradaProducto],
    entradas_antes: Sequence[EntradaProducto],
    limites: LimitesPausa,
    *,
    decidido_el: dt.datetime,
    primer_dato: dt.date | None,
) -> SimulacionPausa:
    """La regla de la pausa de H.2 con `limites` en lugar de los congelados.
    `entradas_antes` son las entradas decididas 30 dias antes, sin
    observaciones posteriores; `primer_dato` es la primera fecha del reporte
    de productos: una ventana anterior a ella no tiene historia y no es cero.
    Las ventas de despues son las de las fechas con fila de la entrada de hoy
    (una fecha sin fila no tuvo clics, S7)."""
    moneda = limites.gasto_min.moneda
    hoy = [replace(e, limites_pausa=limites) for e in entradas_hoy]
    ventana = ventana_pausa(decidido_el)
    ventana_anterior = Ventana(ventana.desde - dt.timedelta(days=30), ventana.desde)
    productos: list[PausaSimulada] = []
    for e in hoy:
        pausas, _ = _regla_pausa(e)
        if pausas:
            actividad = e.actividad_pausa
            assert isinstance(actividad, AdsCompletos)
            productos.append(
                PausaSimulada(e.producto, actividad.gasto, actividad.clics, len(pausas))
            )
    antes = [replace(e, limites_pausa=limites) for e in entradas_antes]
    elegidos = tuple(sorted(e.producto.product_id for e in antes if _regla_pausa(e)[0]))
    por_producto = {
        e.producto.product_id: e for e in hoy if "ads_con_metrica_ausente" not in e.impedimentos
    }
    if primer_dato is None or ventana_anterior.desde < primer_dato:
        evitado: Dinero | SinDato = SinDato(("sin_historia",))
        perdidas: Dinero | SinDato = SinDato(("sin_historia",))
    else:
        gasto = ventas = Decimal(0)
        for pid in elegidos:
            entrada = por_producto.get(pid)
            for dia in entrada.dias_halo if entrada is not None else ():
                if ventana.contiene(dia.fecha):
                    gasto += dia.gasto.valor
                    ventas += dia.ventas.valor
        evitado, perdidas = Dinero(gasto, moneda), Dinero(ventas, moneda)
    return SimulacionPausa(
        limites=limites,
        ventana=ventana,
        productos=tuple(sorted(productos, key=lambda x: (-x.gasto.valor, x.producto.product_id))),
        gasto_total=Dinero(sum((x.gasto.valor for x in productos), Decimal(0)), moneda),
        anuncios_total=sum(x.anuncios for x in productos),
        sin_cobertura_d7=_sin_cobertura(hoy),
        ventana_anterior=ventana_anterior,
        productos_anteriores=elegidos,
        gasto_evitado=evitado,
        ventas_perdidas=perdidas,
        sin_cobertura_d7_anterior=_sin_cobertura(antes),
        sin_dato_despues=sum(1 for pid in elegidos if pid not in por_producto),
        primer_dato=primer_dato,
    )


def _sin_cobertura(entradas: Iterable[EntradaProducto]) -> int:
    return sum(
        1
        for e in entradas
        if isinstance(e.actividad_pausa, SinDato)
        and "fecha_desconocida" in e.actividad_pausa.motivos
    )


def meta_para_grupo(
    evaluaciones: Iterable[EvaluacionProducto], grupo_id: int
) -> MetaReferencia | SinDato:
    """Lo que la evaluacion del producto dice para un grupo apto; un grupo que
    ninguna entrada declara apto no recibe meta."""
    for evaluacion in evaluaciones:
        if any(g.grupo_id == grupo_id for g in evaluacion.entrada.grupos_aptos_para_meta):
            return evaluacion.meta
    return SinDato(("grupo_no_apto",))


def meta_de_hoja(
    meta: MetaReferencia | SinDato,
    *,
    ultimo: Decimal | None,
    destino: Decimal | None,
    tiene_previo: bool,
    version: str = ECONOMIA_AUTO03,
) -> goals.ResolucionMargen:
    """El target de producto de una hoja (S2) en la version seleccionada.

    `auto03` conserva la regla anterior: con ancla previa del mismo producto
    la meta sube de una vez y baja <= 0.5 por ciclo, y una abstencion converge
    al destino. `producto_directo_v1` (M2) aplica la meta calculada DIRECTA,
    sin rampa; sin meta, el peldano de producto no aporta un valor en
    transicion (`aplicado` None) y la cascada resuelve completa su respaldo.

    El replay lee la evaluacion y el caso congelados y no recalcula con
    esta funcion."""
    if version not in POLITICAS_ECONOMIA:
        raise ValueError(f"version economica desconocida: {version!r}")
    if isinstance(meta, SinDato):
        motivo = meta.motivos[0]
        if version == ECONOMIA_AUTO03 and tiene_previo:
            return goals.converge_al_destino(ultimo, destino, motivo, piso=Decimal(0))
        return goals.ResolucionMargen(None, None, motivo, False)
    nueva = meta.porcentaje
    if version == ECONOMIA_AUTO03 and tiene_previo and ultimo is not None:
        ultimo = min(max(ultimo, Decimal(0)), goals.MARGEN_BANDA_MAX)
        if nueva >= ultimo:
            return goals.ResolucionMargen(nueva, nueva, None, False)
        return goals.ResolucionMargen(
            max(nueva, ultimo - goals.MARGEN_PASO_MAX_BAJADA), nueva, None, False
        )
    return goals.ResolucionMargen(nueva, nueva, None, False)


def ventana_pausa(decidido_el: dt.datetime) -> Ventana:
    hoy = decidido_el.astimezone(dt.UTC).date()
    return Ventana(
        hoy - dt.timedelta(days=DIAS_PAUSA_DESDE), hoy - dt.timedelta(days=DIAS_PAUSA_HASTA)
    )


def clave_modo(tipo: Literal["meta", "pausa"], plataforma: str) -> str:
    return f"ads_producto_{tipo}_{plataforma}"


def clave_economia(plataforma: str) -> str:
    return f"ads_producto_economia_{plataforma}"


def economia_desde_settings(settings: Mapping, plataforma: str) -> str:
    """Seleccion de la version economica de la plataforma. Ausente o `auto03`
    conserva la regla de AUTO-03; `producto_directo_v1` activa M2. Un valor
    ajeno es config corrupta (ValueError, fail-closed), como los demas
    interruptores."""
    _valida_plataforma(plataforma)
    clave = clave_economia(plataforma)
    valor = settings.get(clave)
    if valor is None or valor == ECONOMIA_AUTO03:
        return ECONOMIA_AUTO03
    if valor in POLITICAS_ECONOMIA:
        return valor
    raise ValueError(f"setting {clave}: debe ser auto03 o {POLITICA_ECONOMIA}, llego {valor!r}")


def clave_limite(tipo: Literal["clics", "gasto"], plataforma: str) -> str:
    return f"ads_producto_pausa_{tipo}_{plataforma}"


def modos_desde_settings(settings: Mapping, plataforma: str) -> ModosProducto:
    """Interruptores de la plataforma: ausente (o JSON null) es off; otro valor
    que shadow o live es config corrupta y lanza ValueError."""
    _valida_plataforma(plataforma)
    modos = {}
    for tipo in ("meta", "pausa"):
        clave = clave_modo(tipo, plataforma)
        valor = settings.get(clave)
        if valor is None:
            modos[tipo] = "off"
        elif valor in ("shadow", "live"):
            modos[tipo] = valor
        else:
            raise ValueError(f"setting {clave}: debe ser shadow o live, llego {valor!r}")
    return ModosProducto(**modos)


def limites_pausa_desde_settings(settings: Mapping, plataforma: str) -> LimitesPausa:
    """Limites de la pausa de la plataforma; clave ausente da el valor vigente
    del motor de cortes. Solo parsea: los pisos los exige config_write."""
    _valida_plataforma(plataforma)
    k_clics = clave_limite("clics", plataforma)
    k_gasto = clave_limite("gasto", plataforma)
    crudo_clics = settings.get(k_clics)
    crudo_gasto = settings.get(k_gasto)
    if crudo_clics is None:
        clics = F_PAUSE
    elif isinstance(crudo_clics, bool) or not str(crudo_clics).strip().isdigit():
        raise ValueError(f"setting {k_clics}: debe ser un entero, llego {crudo_clics!r}")
    else:
        clics = int(str(crudo_clics).strip())
    if crudo_gasto is None:
        gasto = PAUSE_COST_MIN[plataforma]
    else:
        try:
            gasto = Decimal(str(crudo_gasto).strip())
        except InvalidOperation:
            gasto = Decimal("NaN")
        if isinstance(crudo_gasto, bool) or not gasto.is_finite():
            raise ValueError(f"setting {k_gasto}: debe ser un decimal, llego {crudo_gasto!r}")
    return LimitesPausa(clics, Dinero(gasto, PLATAFORMAS_MONEDA[plataforma]))


def _valida_plataforma(plataforma: str) -> None:
    if plataforma not in PLATAFORMAS:
        raise ValueError(f"plataforma sin decisiones por producto: {plataforma!r}")


# JSON: cada dataclass lleva su "tipo" para leer las uniones sin adivinar;
# Decimal y fechas van como texto.
_TIPOS_JSON: dict[str, type] = {
    t.__name__: t
    for t in (
        Dinero,
        Producto,
        Ventana,
        SinDato,
        DiaProducto,
        AdsCompletos,
        AnuncioElegible,
        AnuncioExcluido,
        PropuestaPausa,
        GrupoAptoParaMeta,
        LimitesPausa,
        EntradaProducto,
        MetaReferencia,
        EvaluacionProducto,
        goals.MedicionMargen,
    )
}


def _a_json(valor: Any) -> Any:
    if is_dataclass(valor):
        return {
            "tipo": type(valor).__name__,
            **{f.name: _a_json(getattr(valor, f.name)) for f in fields(valor)},
        }
    if isinstance(valor, tuple):
        return [_a_json(v) for v in valor]
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dt.date):
        return valor.isoformat()
    return valor


def _de_json(valor: Any, tipo: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, Mapping):
        clase = _TIPOS_JSON[valor["tipo"]]
        pistas = get_type_hints(clase)
        # Un campo ausente toma su valor por omision (JSON congelado antes de
        # que existiera); sin valor por omision, el constructor lanza.
        return clase(
            **{
                f.name: _de_json(valor[f.name], pistas[f.name])
                for f in fields(clase)
                if f.name in valor
            }
        )
    if isinstance(valor, list):
        return tuple(_de_json(v, get_args(tipo)[0]) for v in valor)
    if isinstance(valor, str):
        opciones = get_args(tipo) or (tipo,)
        if Decimal in opciones:
            return Decimal(valor)
        if dt.datetime in opciones:
            return dt.datetime.fromisoformat(valor)
        if dt.date in opciones:
            return dt.date.fromisoformat(valor)
    return valor
