"""Endpoints de TEMPORADA 01 de la tarjeta «Temporadas de venta» (bloque A).

Mismo prefijo /api/ads-optimizer que la escritura (app/api_write.py) y la
misma auth (token solo en el header; sellado 18): viven en modulo propio
para respetar el presupuesto de tamaño (test_architecture). La lectura de
equilibrio y estado de baja vive en app/dashboard_temporada (misma vista y
mismos conteos que pinta la tarjeta); este modulo es la cascara delgada.

- GET  /temporada/vista-previa  (sin token, solo lectura, como
  /campana-ajuste/plan): temporada.resumen + equilibrio por plataforma.
- POST /temporada/terminar-baja (token): ads_temporada.fin_anticipado = hoy.
- POST /temporada/mantener-pausada (token): agrega una keyword de la baja a
  ads_temporada.mantener_pausadas.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import config_write
from app.api import ConexionLectura
from app.api_write import _ERRORES_SETTINGS, ConexionEscritura, exige_token
from app.dashboard_temporada import (
    _config_vigente_con_id,
    equilibrio_temporada,
    metas_vigentes_temporada,
    pausadas_de_baja,
)
from app.optimizer import temporada

router = APIRouter(prefix="/api/ads-optimizer", tags=["ads-optimizer-temporada"])

# Mismo candado transaccional que `config_write.guarda_config`: serializa
# la lectura de `ads_temporada` y su escritura posterior bajo la misma clave.
_SQL_LOCK_CONFIG_VERSION = "SELECT pg_advisory_xact_lock(hashtext('config_version'))"


@router.get("/temporada/vista-previa")
def vista_previa_temporada(
    conn: ConexionLectura,
    meses: str | None = None,
    reduccion_baja_pct: str | None = None,
    dias_preparacion: str | None = None,
    mult_cortes_baja: str | None = None,
    aumento_presupuesto_alta_pct: str | None = None,
    fin_anticipado: str | None = None,
    meta_alta_amazon_mx: str | None = None,
    meta_alta_amazon_us: str | None = None,
) -> dict:
    """«Así queda tu año» de la tarjeta (SIN token y de solo lectura, como
    /campana-ajuste/plan). Llama a temporada.resumen, la misma funcion pura
    que pinta la pagina; el JS no repite ninguna regla. 422 con el mensaje
    de temporada.valida (tal cual lo pega settings.js). Devuelve ademas el
    equilibrio y el aviso por plataforma, que se ven al escribir la meta."""
    _config_id, settings = _config_vigente_con_id(conn)
    guardada = temporada.desde_settings(settings)
    crudo = {
        "version": temporada.VERSION_TEMPORADA,
        "meses": (meses or "").split(","),
        "reduccion_baja_pct": reduccion_baja_pct,
        "dias_preparacion": dias_preparacion,
        "mult_cortes_baja": mult_cortes_baja,
        "aumento_presupuesto_alta_pct": aumento_presupuesto_alta_pct,
        "fin_anticipado": (
            fin_anticipado
            if fin_anticipado is not None
            else (
                guardada.fin_anticipado.isoformat()
                if guardada is not None and guardada.fin_anticipado is not None
                else None
            )
        ),
        "mantener_pausadas": (sorted(guardada.mantener_pausadas) if guardada is not None else []),
    }
    try:
        config_t = temporada.valida(crudo)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    meta_alta: dict[str, Decimal] = {}
    for plataforma in temporada.PLATAFORMAS_TEMPORADA:
        crudo_meta = meta_alta_amazon_mx if plataforma == "amazon_mx" else meta_alta_amazon_us
        if crudo_meta is None or not crudo_meta.strip():
            meta_alta[plataforma] = temporada.meta_alta_desde_settings(settings, plataforma)
            continue
        try:
            meta_alta[plataforma] = temporada.valida_meta_alta(crudo_meta)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    hoy = dt.datetime.now(dt.UTC).date()
    metas_hoy, peldanos = metas_vigentes_temporada(conn)
    res = temporada.resumen(config_t, hoy, antes=guardada, metas_hoy=metas_hoy, meta_alta=meta_alta)
    detalle = {}
    for plataforma in temporada.PLATAFORMAS_TEMPORADA:
        detalle[plataforma] = equilibrio_temporada(
            conn,
            plataforma,
            meta_hoy=metas_hoy.get(plataforma),
            peldano=peldanos.get(plataforma),
            meta_alta=meta_alta.get(plataforma),
            hoy=hoy,
        )
    return {
        "resumen": res.como_dict(),
        "mensaje": temporada.mensaje_guardado(res),
        "plataformas": detalle,
    }


class CuerpoTerminaBaja(BaseModel):
    """«Terminar la baja hoy»: solo la base optimista; el dia es hoy UTC."""

    base_config_version_id: int = Field(ge=1)


@router.post("/temporada/terminar-baja")
def terminar_baja_temporada(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoTerminaBaja,
) -> dict:
    """«Terminar la baja hoy»: fija ads_temporada.fin_anticipado = hoy UTC en
    la config guardada (la misma config_write de siempre; el calendario del
    ano no cambia). Fuera de una baja -> 422 con el texto de la tarjeta."""
    conn.execute(_SQL_LOCK_CONFIG_VERSION)
    _config_id, settings = _config_vigente_con_id(conn)
    guardada = temporada.desde_settings(settings)
    hoy = dt.datetime.now(dt.UTC).date()
    if guardada is None or temporada.resuelve(guardada, hoy).fase != "baja":
        raise HTTPException(
            status_code=422,
            detail=temporada.MSG_FIN_BAJA,
        )
    nueva = replace(guardada, fin_anticipado=hoy)
    try:
        return config_write.guarda_config(
            conn,
            platform="amazon_mx",  # la tarjeta es global: el label nombra una plataforma
            base_config_version_id=cuerpo.base_config_version_id,
            ahora=dt.datetime.now(dt.UTC),
            actor="terminar-baja",
            temporada=temporada.como_guardada(nueva),
        )
    except tuple(_ERRORES_SETTINGS) as exc:
        raise HTTPException(status_code=_ERRORES_SETTINGS[type(exc)], detail=str(exc)) from None


class CuerpoMantenerPausada(BaseModel):
    """«Mantener pausada»: la keyword pausada por Orbit en esta baja que no
    hay que reactivar al terminar."""

    ad_entity_id: int = Field(ge=1)
    base_config_version_id: int = Field(ge=1)


@router.post("/temporada/mantener-pausada")
def mantener_pausada_temporada(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoMantenerPausada,
) -> dict:
    """«Mantener pausada»: agrega la keyword a ads_temporada.mantener_pausadas
    (la misma config_write de siempre; la lista la consume el bloque F al
    reactivar). Fuera de una baja -> 422; una keyword que ya no esta en la
    lista de pausadas de la baja -> 409 (recarga la pagina)."""
    conn.execute(_SQL_LOCK_CONFIG_VERSION)
    _config_id, settings = _config_vigente_con_id(conn)
    guardada = temporada.desde_settings(settings)
    hoy = dt.datetime.now(dt.UTC).date()
    if guardada is None or temporada.resuelve(guardada, hoy).fase != "baja":
        raise HTTPException(
            status_code=422,
            detail="Solo puedes mantener keywords pausadas durante una temporada baja.",
        )
    plataforma = conn.execute(
        "SELECT platform::text FROM ad_entity WHERE id = %s", (cuerpo.ad_entity_id,)
    ).fetchone()
    if plataforma is None:
        raise HTTPException(status_code=404, detail="Esa keyword no existe.")
    en_lista = any(
        fila["ad_entity_id"] == cuerpo.ad_entity_id
        for fila in pausadas_de_baja(
            conn, plataforma[0], estacion_inicio(guardada, hoy), guardada.mantener_pausadas
        )
    )
    if not en_lista:
        raise HTTPException(
            status_code=409,
            detail=(
                "Esa keyword ya no está en la lista de pausadas de la baja: recarga la página."
            ),
        )
    nueva = replace(guardada, mantener_pausadas=guardada.mantener_pausadas | {cuerpo.ad_entity_id})
    try:
        salida = config_write.guarda_config(
            conn,
            platform="amazon_mx",  # la tarjeta es global: el label nombra una plataforma
            base_config_version_id=cuerpo.base_config_version_id,
            ahora=dt.datetime.now(dt.UTC),
            actor="mantener-pausada",
            temporada=temporada.como_guardada(nueva),
        )
    except tuple(_ERRORES_SETTINGS) as exc:
        raise HTTPException(status_code=_ERRORES_SETTINGS[type(exc)], detail=str(exc)) from None
    salida["mantener_pausadas"] = sorted(nueva.mantener_pausadas)
    return salida


def estacion_inicio(config: temporada.ConfigTemporada, hoy: dt.date) -> dt.date:
    """Primer dia del bloque de baja vigente (el mismo que pinta la tarjeta)."""
    return temporada.resuelve(config, hoy).inicio_bloque
