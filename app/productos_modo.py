"""Cambio de modo de la pausa por producto (AUTO-03 I.7).

`cambia_pausa` es el unico camino que acepta `live` para la pausa: lo llaman
el CLI `productos-modo --pausa` y el boton de la pantalla de productos
(POST /api/ads-optimizer/productos/pausa/{platform}), los dos con
ORBIT_DSN_ADMIN. En una sola transaccion toma el candado exclusivo
`auto03-pausa-<plataforma>` (el encolado de pausas de product ad lo toma
compartido y lee la config despues), escribe la config si el modo cambia y,
al encender, descarta las practicas abiertas de product ads de la plataforma
(S18). Si el descarte falla no queda la pausa en vivo con practicas abiertas,
y repetir el encendido con la pausa ya en vivo vuelve a limpiar sin escribir
config.
"""

from __future__ import annotations

import datetime as dt

import psycopg

from app import config_write
from app.optimizer import productos

MOTIVO_DESCARTE = "encendido_pausa_producto"

_SQL_CANDADO = "SELECT pg_advisory_xact_lock(hashtext('auto03-pausa-' || %s::text))"

_SQL_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_PRACTICAS = """
SELECT count(*)
  FROM apply_queue q
  JOIN ad_entity e ON e.id = q.ad_entity_id AND e.kind = 'product_ad'
 WHERE q.platform = %s::platform AND q.kind = 'pause' AND q.modo = 'shadow'
   AND q.estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
"""

_SQL_DESCARTA_PRACTICAS = """
UPDATE apply_queue q
   SET estado = 'discarded', discarded_at = now(), discard_motivo = %s
  FROM ad_entity e
 WHERE e.id = q.ad_entity_id AND e.kind = 'product_ad'
   AND q.platform = %s::platform AND q.kind = 'pause' AND q.modo = 'shadow'
   AND q.estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
RETURNING q.id
"""


def cambia_pausa(
    conn: psycopg.Connection,
    platform: str,
    modo: str,
    base_config_version_id: int,
    ahora: dt.datetime,
    *,
    actor: str = "productos-modo",
) -> dict:
    """Deja la pausa de `platform` en `modo` (off, shadow o live).

    Todas las ramas exigen que `base_config_version_id` sea la config vigente
    (ConfigObsoleta). Repetir el modo vigente no escribe config. Solo `live`
    exige las dos claves de limites (SettingsInvalido) y descarta las filas
    shadow no terminales de pause sobre product_ad de la plataforma. El
    commit es uno solo, al final; ante cualquier error se revierte todo."""
    if modo not in ("off", "shadow", "live"):
        raise config_write.SettingsInvalido(
            f"modo de la pausa por producto debe ser off, shadow o live, llego {modo!r}"
        )
    if platform not in productos.PLATAFORMAS:
        raise config_write.SettingsInvalido(f"plataforma sin pausa por producto: {platform!r}")
    if conn.autocommit:
        raise ValueError("cambia_pausa necesita una conexion sin autocommit: es una sola transaccion")
    try:
        conn.execute(_SQL_CANDADO, (platform,))
        fila = conn.execute(_SQL_VIGENTE).fetchone()
        vigente_id, settings = (fila[0], fila[1]) if fila is not None else (None, {})
        if vigente_id != base_config_version_id:
            raise config_write.ConfigObsoleta(
                f"la config vigente es {vigente_id}, no {base_config_version_id}:"
                " recarga la pantalla y vuelve a intentar"
            )
        try:
            anterior = productos.modos_desde_settings(settings, platform).pausa
        except ValueError as exc:
            raise config_write.SettingsInvalido(str(exc)) from None
        if modo == "live" and not config_write.limites_pausa_guardados(settings, platform):
            raise config_write.SettingsInvalido(
                f"la pausa en vivo exige guardar antes los limites de clics y gasto de {platform}"
            )
        config_id = vigente_id
        escrita = False
        if anterior != modo:
            guardado = config_write.guarda_config(
                conn,
                platform=platform,
                base_config_version_id=base_config_version_id,
                ahora=ahora,
                actor=actor,
                confirma=False,
                producto_modo={"pausa": modo},
                enciende_pausa=modo == "live",
            )
            config_id = guardado["config_version_id"]
            escrita = True
        antes = conn.execute(_SQL_PRACTICAS, (platform,)).fetchone()[0]
        descartadas: list[int] = []
        if modo == "live":
            descartadas = [
                f[0]
                for f in conn.execute(_SQL_DESCARTA_PRACTICAS, (MOTIVO_DESCARTE, platform))
            ]
        despues = conn.execute(_SQL_PRACTICAS, (platform,)).fetchone()[0]
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {
        "platform": platform,
        "modo": modo,
        "modo_anterior": anterior,
        "config_version_id": config_id,
        "config_escrita": escrita,
        "practicas_antes": antes,
        "practicas_descartadas": len(descartadas),
        "practicas_despues": despues,
    }
