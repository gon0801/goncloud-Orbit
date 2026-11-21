"""Builders puros y tipos de los avisos Telegram del motor de precios.

Separado de `app/notifica.py` en Fase 3.1: sin SQL ni HTTP. Nunca expone
costo, margen, goal ni cuerpos de error (los motivos se traducen via
`app.etiquetas`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.precio.config import _entero as _entero_config

TIPOS_PRECIO = (
    "no_evaluado",
    "goal_inalcanzable",
    "frenado",
    "no_confirmado",
    "buy_box_perdida",
    "huerfana_sin_patch",
    "resumen_diario",
    "compuerta",
    "cambio_error",
    "corte_errores",
    "reversa",
    "cohorte",
    "corrida_fallida",
)

TIPOS_PRECIO_GRUPO = ("no_evaluado", "goal_inalcanzable", "frenado")
TIPOS_PRECIO_PRODUCTO = ("no_confirmado", "buy_box_perdida")
TIPOS_PRECIO_SELLADOS = ("resumen_diario", "compuerta")
TOPE_SKUS_PRECIO = 5
CLAVE_PRECIO_AVISO_DIAS = "precio_aviso_dias_sin_evaluar"

FRASEO_TIPO_PRECIO = {
    "no_evaluado": "no evaluados",
    "goal_inalcanzable": "objetivo inalcanzable",
    "frenado": "frenados",
    "no_confirmado": "no confirmado",
    "buy_box_perdida": "buy box perdida",
    "huerfana_sin_patch": "huerfanas sin parche",
    "resumen_diario": "resumen diario",
    "compuerta": "compuerta",
    "cambio_error": "cambio en error",
    "corte_errores": "corte por errores",
    "reversa": "reversa",
    "cohorte": "cohorte",
    "corrida_fallida": "corrida fallida",
}


def _etiqueta(codigo: str | None, dominio) -> str:
    from app.etiquetas import etiqueta

    return etiqueta(codigo, dominio)


@dataclass(frozen=True)
class GrupoPrecio:
    """Un (plataforma, motivo) de hoy para los tipos de grupo —y para el
    resto agrupado de los tipos por producto (B3)—: conteo total y SKUs
    (el builder corta a `TOPE_SKUS_PRECIO`)."""

    platform: str
    tipo: str
    motivo: str
    total: int
    skus: tuple


@dataclass(frozen=True)
class ProductoPrecio:
    """Un producto para los tipos por producto: SKU/ASIN, precios
    preformateados, estado y motivo. Sin costo, margen, goal ni errores."""

    platform: str
    tipo: str
    sku: str | None
    asin: str | None
    precio_antes: str | None
    precio_despues: str | None
    moneda: str | None
    estado: str | None
    motivo: str | None


@dataclass(frozen=True)
class HuerfanaPrecio:
    """Pendientes cerradas sin parche en la corrida de hoy, por plataforma."""

    platform: str
    total: int
    tipo: str = "huerfana_sin_patch"


def aviso_precio_grupo(grupo: GrupoPrecio) -> str:
    """Builder PURO del aviso por (plataforma, motivo): conteo + hasta 5
    SKUs + resto contado (200 con el mismo motivo -> UN aviso)."""
    lineas = [
        f"[Orbit] precio: {FRASEO_TIPO_PRECIO[grupo.tipo]}",
        f"plataforma: {grupo.platform}",
        f"motivo: {_etiqueta(grupo.motivo, 'motivo_precio')}",
        f"total: {grupo.total}",
    ]
    skus = tuple(grupo.skus)[:TOPE_SKUS_PRECIO]
    if skus:
        lineas.append(f"skus: {', '.join(skus)}")
    resto = grupo.total - len(skus)
    if resto > 0:
        lineas.append(f"y {resto} mas")
    return "\n".join(lineas)


def _es_plataforma_amazon(platform: str | None) -> bool:
    """El `external_id` solo es un ASIN en plataformas Amazon (B5)."""
    return (platform or "").lower().startswith("amazon")


def aviso_precio_producto(item: ProductoPrecio) -> str:
    """Builder PURO del aviso por producto: SKU, precios, estado y motivo
    en palabras (regla 3: ausente no se menciona). El ASIN solo sale en
    plataformas Amazon; fuera de Amazon el id externo viaja como tal."""
    lineas = [
        f"[Orbit] precio: {FRASEO_TIPO_PRECIO[item.tipo]}",
        f"plataforma: {item.platform}",
    ]
    if item.sku:
        lineas.append(f"sku: {item.sku}")
    if item.asin:
        if _es_plataforma_amazon(item.platform):
            lineas.append(f"asin: {item.asin}")
        else:
            lineas.append(f"id externo: {item.asin}")
    if item.precio_antes is not None and item.precio_despues is not None:
        precio = f"{item.precio_antes} -> {item.precio_despues}"
    else:
        precio = item.precio_despues if item.precio_despues is not None else item.precio_antes
    if precio is not None:
        lineas.append(f"precio: {precio}" + (f" {item.moneda}" if item.moneda else ""))
    if item.estado:
        lineas.append(f"estado: {_etiqueta(item.estado, 'estado_precio')}")
    if item.motivo:
        lineas.append(f"motivo: {_etiqueta(item.motivo, 'motivo_precio')}")
    return "\n".join(lineas)


def aviso_precio_huerfana(huerfana: HuerfanaPrecio) -> str:
    """Builder PURO del aviso por plataforma: cuantas pendientes cerro la
    corrida sin parche (el codigo de error no viaja: es uno solo y fijo)."""
    return "\n".join(
        [
            "[Orbit] precio: huerfanas sin parche",
            f"plataforma: {huerfana.platform}",
            f"total: {huerfana.total}",
            "la corrida cerro pendientes sin parche: revisar el log"
            " y revertir si el precio se movio",
        ]
    )


@dataclass(frozen=True)
class ResumenDiarioPrecio:
    """El dia de UNA plataforma (bosquejo U): conteos + hasta 5 movidos con
    precio antes -> despues (preformateados). Sin costo, margen, goal ni
    errores: la ausencia es estructural."""

    platform: str
    dia: dt.date
    subieron: int
    bajaron: int
    en_sombra: int
    retenidos: int
    errores: int
    sin_evaluar: int
    con_envio_imputado: int
    muestra: tuple[tuple[str, str, str], ...]
    tipo: str = "resumen_diario"


def aviso_precio_resumen(resumen: ResumenDiarioPrecio) -> str:
    """Builder PURO del resumen diario. Un dia sin movimiento tambien avisa."""
    lineas = [
        f"[Orbit] precio: {FRASEO_TIPO_PRECIO[resumen.tipo]}",
        f"plataforma: {resumen.platform}",
        f"dia: {resumen.dia.isoformat()}",
    ]
    if resumen.subieron == 0 and resumen.bajaron == 0:
        lineas.append("hoy no se movio ningun precio")
    lineas.extend(
        [
            f"subieron: {resumen.subieron} bajaron: {resumen.bajaron}",
            f"en sombra: {resumen.en_sombra}",
            f"retenidos: {resumen.retenidos}",
            f"errores: {resumen.errores}",
            f"sin evaluar: {resumen.sin_evaluar}",
            f"con envio imputado: {resumen.con_envio_imputado}",
        ]
    )
    for sku, antes, despues in tuple(resumen.muestra)[:TOPE_SKUS_PRECIO]:
        lineas.append(f"{sku}: {antes} -> {despues}")
    return "\n".join(lineas)


@dataclass(frozen=True)
class CompuertaPrecio:
    """Un universo retenido hoy (bosquejo U): causa, conteos y el detalle de
    la medicion. Sin costo, margen ni goal."""

    platform: str
    dia: dt.date
    canal: str
    causa: str
    medidas: int
    nuevas: int
    insumo: str | None
    detalle: str
    tipo: str = "compuerta"


def aviso_precio_compuerta(aviso: CompuertaPrecio) -> str:
    """Builder PURO del aviso de compuerta: N de M quieren moverse hoy, no
    se aplico nada, y donde soltarlo. Con `insumo_sistemico` nombra el
    insumo (traducido); el detalle viaja con el mismo reemplazo."""
    lineas = [
        f"[Orbit] precio: {FRASEO_TIPO_PRECIO[aviso.tipo]}",
        f"plataforma: {aviso.platform}",
        f"dia: {aviso.dia.isoformat()}",
        f"universo: {aviso.platform}/{aviso.canal}",
        f"causa: {_etiqueta(aviso.causa, 'causa_compuerta')}",
    ]
    if aviso.insumo:
        lineas.append(f"insumo: {_etiqueta(aviso.insumo, 'insumo_compuerta')}")
    lineas.append(
        f"{aviso.nuevas} de {aviso.medidas} quieren moverse hoy;"
        " no se aplico nada. Entra a /precios para soltarlo."
    )
    if aviso.detalle:
        detalle = aviso.detalle
        if aviso.insumo:
            detalle = detalle.replace(aviso.insumo, _etiqueta(aviso.insumo, "insumo_compuerta"), 1)
        lineas.append(f"detalle: {detalle}")
    return "\n".join(lineas)


@dataclass(frozen=True)
class CambioErrorPrecio:
    """Un cambio que quedo en `error`: SKU y motivo en palabras. El cuerpo
    del error jamas viaja (no hay campo para el)."""

    platform: str
    dia: dt.date
    sku: str | None
    asin: str | None
    motivo: str | None
    tipo: str = "cambio_error"


def aviso_precio_cambio_error(aviso: CambioErrorPrecio) -> str:
    """Builder PURO del aviso por cambio en error."""
    lineas = [
        f"[Orbit] precio: {FRASEO_TIPO_PRECIO[aviso.tipo]}",
        f"plataforma: {aviso.platform}",
        f"dia: {aviso.dia.isoformat()}",
    ]
    if aviso.sku:
        lineas.append(f"sku: {aviso.sku}")
    if aviso.asin:
        if _es_plataforma_amazon(aviso.platform):
            lineas.append(f"asin: {aviso.asin}")
        else:
            lineas.append(f"id externo: {aviso.asin}")
    if aviso.motivo:
        lineas.append(f"motivo: {_etiqueta(aviso.motivo, 'motivo_precio')}")
    lineas.append("el cambio quedo en error: revisar y reintentar en el repaso")
    return "\n".join(lineas)


@dataclass(frozen=True)
class CorteErroresPrecio:
    """El corte por errores seguidos se abrio en una corrida: cuantos y
    cuantos retuvo. Sin costo, margen ni goal."""

    platform: str
    dia: dt.date
    seguidos: int
    limite: int
    retenidas: int
    tipo: str = "corte_errores"


def aviso_precio_corte_errores(aviso: CorteErroresPrecio) -> str:
    """Builder PURO del aviso por corte de errores."""
    return "\n".join(
        [
            f"[Orbit] precio: {FRASEO_TIPO_PRECIO[aviso.tipo]}",
            f"plataforma: {aviso.platform}",
            f"dia: {aviso.dia.isoformat()}",
            f"errores seguidos: {aviso.seguidos} (limite {aviso.limite})",
            f"retenidas por el corte: {aviso.retenidas}",
            "no se aplico nada mas en esta corrida",
        ]
    )


@dataclass(frozen=True)
class ReversaPrecio:
    """Una reversa en lote termino: conteo de vueltas y saltadas. Solo
    conteos (bosquejo I)."""

    platform: str
    dia: dt.date
    revertidas: int
    saltadas: int
    tipo: str = "reversa"


def aviso_precio_reversa(aviso: ReversaPrecio) -> str:
    """Builder PURO del aviso por reversa en lote."""
    return "\n".join(
        [
            f"[Orbit] precio: {FRASEO_TIPO_PRECIO[aviso.tipo]}",
            f"plataforma: {aviso.platform}",
            f"dia: {aviso.dia.isoformat()}",
            f"revertidas: {aviso.revertidas} saltadas: {aviso.saltadas}",
        ]
    )


@dataclass(frozen=True)
class CohortePrecio:
    """Lectura de cohorte de un universo (S.7 la produce): solo avisa. La
    diferencia viaja preformateada en palabras y numeros."""

    platform: str
    dia: dt.date
    canal: str
    diferencia: str
    tipo: str = "cohorte"


def aviso_precio_cohorte(aviso: CohortePrecio) -> str:
    """Builder PURO del aviso por lectura de cohorte."""
    return "\n".join(
        [
            f"[Orbit] precio: {FRASEO_TIPO_PRECIO[aviso.tipo]}",
            f"plataforma: {aviso.platform}",
            f"dia: {aviso.dia.isoformat()}",
            f"universo: {aviso.platform}/{aviso.canal}",
            f"tratada frente al control: {aviso.diferencia}",
            "lectura de cohorte: solo avisa, no frena ni baja",
        ]
    )


@dataclass(frozen=True)
class CorridaFallidaPrecio:
    """La corrida de una plataforma se cayo (ARREGLOS 03 P-B): no decidio ni
    aplico precios. `motivo` es el NOMBRE de la clase del error; su texto
    jamas viaja (no hay campo para el)."""

    platform: str
    dia: dt.date
    motivo: str
    tipo: str = "corrida_fallida"


def aviso_precio_corrida_fallida(aviso: CorridaFallidaPrecio) -> str:
    """Builder PURO del aviso de corrida caida."""
    return "\n".join(
        [
            f"[Orbit] precio: {FRASEO_TIPO_PRECIO[aviso.tipo]}",
            f"plataforma: {aviso.platform}",
            f"dia: {aviso.dia.isoformat()}",
            "la corrida se cayo y no decidio ni aplico precios;"
            " el siguiente repaso lo vuelve a intentar",
            f"motivo: {aviso.motivo}",
        ]
    )


def validar_precio_aviso_dias(settings) -> int:
    """`precio_aviso_dias_sin_evaluar` entero 1-14; `ValueError` nombra la
    clave si falta o sale de cota (ninguna otra clave se lee aqui). El
    ayudante es el unico de `app/precio/config.py` (REPRICING 02 0.a)."""
    return _entero_config(settings, CLAVE_PRECIO_AVISO_DIAS, minimo=1, maximo=14)


def flanco_nuevos(hoy, ayer) -> list:
    """Claves presentes hoy que ayer no estaban, en el orden de hoy (el
    flanco: la racha que sigue no vuelve a avisar; la que se corta y vuelve,
    si). Pura."""
    previos = set(ayer)
    vistos = set()
    nuevos = []
    for clave in hoy:
        if clave not in previos and clave not in vistos:
            vistos.add(clave)
            nuevos.append(clave)
    return nuevos


def flanco_umbral(presentes, hoy, umbral) -> bool:
    """True solo el dia en que la racha de dias presentes ALCANZA el umbral
    (racha == umbral): con mas dias (sigue) o sin hoy (corta) es False; al
    reconstruirse (vuelve) es True de nuevo. Pura."""
    try:
        umbral = int(umbral)
    except (TypeError, ValueError):
        return False
    if hoy not in presentes:
        return False
    racha = 0
    dia = hoy
    while dia in presentes:
        racha += 1
        dia -= dt.timedelta(days=1)
    return racha == umbral
