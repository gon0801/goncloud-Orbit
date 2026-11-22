"""Calendario, resolucion de fase y resumen anual de Temporada (PURO).

Extraido de `app/optimizer/temporada.py` (Ola D2). `resuelve()` instancia los
bloques vecinos una sola vez por llamada sobre `_Bloque` tipado.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from app.optimizer.temporada_config import (
    MESES_ANIO,
    VERSION_TEMPORADA,
    ConfigTemporada,
    Estacion,
    Evento,
    Fase,
    ResumenTemporada,
    TipoMes,
    _fecha_mes,
    _instancias,
    _rachas,
    _texto,
)

FRASE_ENTRA_BAJA = "Empieza la temporada baja: Orbit baja todos los bids {pct} %."
FRASE_RESTAURA_PREPARA = (
    "Empieza la preparación: los bids regresan a como estaban antes de la baja"
    " y Orbit reactiva las keywords que pausó durante la baja."
)
FRASE_RESTAURA = "Termina la baja: los bids regresan. Sigue un mes normal."
FRASE_PREPARA_ALTA = "Empieza la previa de la temporada alta; todavía no cambia nada."
FRASE_ENTRA_ALTA = "Empieza la temporada alta."
FRASE_SALE_ALTA = "La meta de ACoS regresa a la normal."

NOMBRES_PLATAFORMA = {"amazon_mx": "MX", "amazon_us": "US"}
MESES_LARGOS = MESES_ANIO


def resuelve(config: ConfigTemporada, dia: dt.date) -> Estacion:
    """Fase de `dia` para TODA la plataforma (prioridad: alta > preparacion > baja > normal)."""
    bloques_prev_y_actual = _instancias(config, dia.year - 1) + _instancias(config, dia.year)
    bloques = bloques_prev_y_actual + _instancias(config, dia.year + 1)

    def _estacion(fase: Fase, *, previa_alta: bool, inicio_bloque: dt.date) -> Estacion:
        return Estacion(
            version=VERSION_TEMPORADA,
            dia=dia,
            fase=fase,
            previa_alta=previa_alta,
            reduccion_baja=config.reduccion_baja,
            mult_cortes_baja=config.mult_cortes_baja,
            inicio_bloque=inicio_bloque,
        )

    for bloque in bloques_prev_y_actual:
        if bloque.tipo == "alta" and bloque.inicio <= dia < bloque.fin:
            return _estacion("alta", previa_alta=False, inicio_bloque=bloque.inicio)
    for bloque in bloques:
        if bloque.tipo != "alta":
            continue
        prep = bloque.inicio - dt.timedelta(days=config.dias_preparacion)
        if prep <= dia < bloque.inicio:
            return _estacion("normal", previa_alta=True, inicio_bloque=prep)
    for bloque in bloques:
        if (
            bloque.tipo == "baja"
            and bloque.restaura is not None
            and bloque.inicio <= dia < bloque.restaura
        ):
            return _estacion("baja", previa_alta=False, inicio_bloque=bloque.inicio)
    for bloque in bloques:
        if (
            bloque.tipo == "baja"
            and bloque.restaura is not None
            and bloque.inicio <= dia < bloque.fin
            and dia >= bloque.restaura
        ):
            return _estacion("normal", previa_alta=False, inicio_bloque=bloque.restaura)
    return _estacion(
        "normal",
        previa_alta=False,
        inicio_bloque=_inicio_racha_normal(config.meses, dia),
    )


def _inicio_racha_normal(meses: tuple[TipoMes, ...], dia: dt.date) -> dt.date:
    """Dia 1 de la racha normal que contiene el mes de `dia`."""
    for tipo, m0, largo in _rachas(meses):
        if tipo != "normal":
            continue
        indices = {(m0 + k) % 12 for k in range(largo)}
        if (dia.month - 1) in indices:
            anio = dia.year if m0 <= dia.month - 1 else dia.year - 1
            return _fecha_mes(anio, m0 + 1)
    raise AssertionError("fase normal sin racha normal que contenga el mes")


def eventos(config: ConfigTemporada, desde: dt.date, hasta: dt.date) -> tuple[Evento, ...]:
    """Los cambios del calendario en [desde, hasta), en orden."""
    if all(m == "alta" for m in config.meses):
        return ()
    anios = range(desde.year - 1, hasta.year + 2)
    bloques = [b for anio in anios for b in _instancias(config, anio)]
    preps = {
        b.inicio - dt.timedelta(days=config.dias_preparacion) for b in bloques if b.tipo == "alta"
    }
    restaura_en: set[dt.date] = {
        b.restaura
        for b in bloques
        if b.tipo == "baja" and b.restaura is not None and b.inicio < b.restaura
    }
    sueltos: list[Evento] = []
    for bloque in bloques:
        if bloque.tipo == "baja" and bloque.restaura is not None:
            if bloque.inicio < bloque.restaura and desde <= bloque.inicio < hasta:
                sueltos.append(
                    Evento(
                        bloque.inicio,
                        "entra_baja",
                        FRASE_ENTRA_BAJA.format(pct=_texto(config.reduccion_baja * 100)),
                    )
                )
            if bloque.inicio < bloque.restaura and desde <= bloque.restaura < hasta:
                frase = FRASE_RESTAURA_PREPARA if bloque.restaura in preps else FRASE_RESTAURA
                sueltos.append(Evento(bloque.restaura, "restaura", frase))
        elif bloque.tipo == "alta":
            if desde <= bloque.inicio < hasta:
                sueltos.append(Evento(bloque.inicio, "entra_alta", FRASE_ENTRA_ALTA))
            if desde <= bloque.fin < hasta:
                sueltos.append(Evento(bloque.fin, "sale_alta", FRASE_SALE_ALTA))
            prep = bloque.inicio - dt.timedelta(days=config.dias_preparacion)
            if prep not in restaura_en and desde <= prep < hasta:
                sueltos.append(Evento(prep, "prepara_alta", FRASE_PREPARA_ALTA))
    return tuple(sorted(sueltos, key=lambda e: (e.fecha, e.tipo)))


def _texto_metas(metas: Mapping[str, Decimal]) -> str:
    por_valor: dict[Decimal, list[str]] = {}
    for plataforma, meta in metas.items():
        nombre = NOMBRES_PLATAFORMA.get(plataforma, plataforma)
        por_valor.setdefault(meta, []).append(nombre)
    partes = [f"{_texto(v)} % en {' y '.join(nombres)}" for v, nombres in por_valor.items()]
    return " y ".join(partes)


def _frase_hoy(
    estacion: Estacion,
    config: ConfigTemporada,
    metas_hoy: Mapping[str, Decimal],
    meta_alta: Mapping[str, Decimal],
) -> str:
    if estacion.fase == "baja":
        return f"Hoy: temporada baja. Orbit baja los bids {_texto(config.reduccion_baja * 100)} %."
    if estacion.fase == "alta":
        texto = _texto_metas(meta_alta)
        return "Hoy: temporada alta." + (f" Meta de ACoS {texto}." if texto else "")
    if estacion.previa_alta:
        return "Hoy: preparación de la temporada alta. Los bids ya regresaron."
    texto = _texto_metas(metas_hoy)
    return "Hoy: mes normal." + (f" Meta de ACoS {texto}." if texto else "")


def _aviso_cambio(
    nueva: Estacion,
    config: ConfigTemporada,
    fase_antes: Fase,
    meta_alta: Mapping[str, Decimal],
) -> str:
    if nueva.fase == "baja":
        return (
            "Ojo: con este cambio hoy pasa a temporada baja. En el siguiente ciclo"
            f" Orbit baja los bids {_texto(config.reduccion_baja * 100)} %."
        )
    if nueva.fase == "alta":
        texto = _texto_metas(meta_alta)
        cola = f" la meta de ACoS pasa a {texto}" if texto else " empieza la meta de temporada alta"
        return f"Ojo: con este cambio hoy pasa a temporada alta. En el siguiente ciclo{cola}."
    if fase_antes == "baja":
        return (
            "Ojo: con este cambio hoy sale de la temporada baja. En el siguiente ciclo"
            " los bids regresan a como estaban."
        )
    return (
        "Ojo: con este cambio hoy deja la temporada alta. En el siguiente ciclo"
        " la meta de ACoS vuelve a la normal."
    )


def resumen(
    config: ConfigTemporada,
    hoy: dt.date,
    *,
    antes: ConfigTemporada | None,
    metas_hoy: Mapping[str, Decimal],
    meta_alta: Mapping[str, Decimal],
) -> ResumenTemporada:
    hasta = hoy + dt.timedelta(days=365)
    proximos = list(eventos(config, hoy, hasta))
    texto = _texto_metas(meta_alta)
    if texto:
        proximos = [
            replace(e, frase=f"{e.frase} Meta de ACoS {texto}.") if e.tipo == "entra_alta" else e
            for e in proximos
        ]
    estacion = resuelve(config, hoy)
    aviso = None
    if antes is not None:
        fase_antes = resuelve(antes, hoy).fase
        if fase_antes != estacion.fase:
            aviso = _aviso_cambio(estacion, config, fase_antes, meta_alta)
    return ResumenTemporada(
        hoy=hoy,
        fase_hoy=estacion.fase,
        frase_hoy=_frase_hoy(estacion, config, metas_hoy, meta_alta),
        proximos=tuple(proximos),
        aviso_cambio=aviso,
    )


def fecha_larga(dia: dt.date) -> str:
    return f"{dia.day} de {MESES_LARGOS[dia.month - 1]}"


def mensaje_guardado(resumen_temporada: ResumenTemporada) -> str:
    partes = ["Guardado."]
    if resumen_temporada.proximos:
        primero = resumen_temporada.proximos[0]
        frase = primero.frase[0].lower() + primero.frase[1:]
        partes.append(f"Próximo cambio: el {fecha_larga(primero.fecha)} {frase}")
        prepara = next(
            (e for e in resumen_temporada.proximos[1:] if e.tipo in ("restaura", "prepara_alta")),
            None,
        )
        if prepara is not None:
            partes.append(f"La próxima preparación empieza el {fecha_larga(prepara.fecha)}.")
    return " ".join(partes)
