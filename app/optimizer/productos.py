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
- Paso por hoja (S2): el ancla es el target previo de la hoja solo si fue
  margen_producto. Con ella meta_de_hoja sube de una vez y baja <= 0.5 por
  ciclo, y si el producto se abstiene converge al siguiente peldano
  (goals.converge_al_destino con piso 0). Sin ella la meta aplica directa.
- Interruptores: ads_producto_meta_<plat> y ads_producto_pausa_<plat>
  (ausente = off; shadow | live). Limites de la pausa (S8):
  ads_producto_pausa_clics_<plat> y ads_producto_pausa_gasto_<plat>, con
  cortes.F_PAUSE y bid.PAUSE_COST_MIN por omision; los pisos se validan en la
  frontera (app/config_write.py), no aqui.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
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
# reglas se calculo una evaluacion vieja.
POLITICA_PRODUCTO = "v1"

MADUREZ_HALO = dt.timedelta(days=30)

# Ventana de la pausa (S6/S7): [D-60, D-30) UTC, 30 fechas maduras.
DIAS_PAUSA_DESDE = 60
DIAS_PAUSA_HASTA = 30

# Vocabulario cerrado. Los cinco primeros son los de goals.motivo_dato_invalido
# y sin_fraccion; los demas son propios del producto.
MOTIVOS_PRODUCTO: dict[str, str] = {
    "sin_fraccion": goals.ETIQUETA_ABSTENCION["sin_fraccion"],
    "sin_margen": goals.ETIQUETA_ABSTENCION["sin_margen"],
    "cobertura_baja": goals.ETIQUETA_ABSTENCION["cobertura_baja"],
    "ventana_corta": goals.ETIQUETA_ABSTENCION["ventana_corta"],
    "ledger_rancio": goals.ETIQUETA_ABSTENCION["ledger_rancio"],
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
    grupos_aptos_para_meta: tuple[GrupoAptoParaMeta, ...]
    impedimentos: tuple[str, ...]
    limites_pausa: LimitesPausa

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


def resolver_producto(entrada: EntradaProducto) -> EvaluacionProducto:
    """La meta del producto o los motivos por los que no la tiene."""
    desconocidos = set(entrada.impedimentos) - MOTIVOS_PRODUCTO.keys()
    if desconocidos:
        raise ValueError(f"impedimentos fuera del vocabulario: {sorted(desconocidos)}")
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
    return EvaluacionProducto(entrada, meta, (*motivos, *informativos), POLITICA_PRODUCTO)


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
) -> goals.ResolucionMargen:
    """El target de producto de una hoja (S2). `ultimo` es el target previo de
    la hoja, `destino` la cascada sin producto y `tiene_previo` dice si el
    previo fue margen_producto. Solo ese previo es ancla: con el, la meta sube
    de una vez y baja <= 0.5 por ciclo, y una abstencion converge al destino.
    Sin el, la meta aplica directa y una abstencion cae al siguiente peldano."""
    if isinstance(meta, SinDato):
        motivo = meta.motivos[0]
        if not tiene_previo:
            return goals.ResolucionMargen(None, None, motivo, False)
        return goals.converge_al_destino(ultimo, destino, motivo, piso=Decimal(0))
    nueva = meta.porcentaje
    if not tiene_previo or ultimo is None:
        return goals.ResolucionMargen(nueva, nueva, None, False)
    ultimo = min(max(ultimo, Decimal(0)), goals.MARGEN_BANDA_MAX)
    if nueva >= ultimo:
        return goals.ResolucionMargen(nueva, nueva, None, False)
    return goals.ResolucionMargen(
        max(nueva, ultimo - goals.MARGEN_PASO_MAX_BAJADA), nueva, None, False
    )


def ventana_pausa(decidido_el: dt.datetime) -> Ventana:
    hoy = decidido_el.astimezone(dt.UTC).date()
    return Ventana(
        hoy - dt.timedelta(days=DIAS_PAUSA_DESDE), hoy - dt.timedelta(days=DIAS_PAUSA_HASTA)
    )


def clave_modo(tipo: Literal["meta", "pausa"], plataforma: str) -> str:
    return f"ads_producto_{tipo}_{plataforma}"


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
        return clase(**{f.name: _de_json(valor[f.name], pistas[f.name]) for f in fields(clase)})
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
