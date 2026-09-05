"""Escritura de settings de plataforma desde la UI (DASHBOARD 01 3.1, E1-E6).

UN solo camino (regla 1): `guarda_config` es la UNICA escritura de
`config_version` del codigo de la app — el endpoint POST
`/api/ads-optimizer/settings/{platform}` (app/api_write.py, auth sellada 18)
DESPACHA aqui. Espejo de app/goals_write.py: la logica vive en una funcion
PURA (`proxima_config`, testeable sin DB) y la cascara solo lee, inserta y
confirma.

CONFIG = FILA NUEVA (E5): config_version es append-only por trigger; cambiar
un setting es copiar la config vigente ENTERA, tocar las claves de ESA
plataforma y escribir una fila nueva con label legible. Lo que no se toca
viaja intacto (incluido `ads_optimizer_mode`, que esta pantalla JAMAS edita,
E6). Concurrencia optimista: el caller declara sobre que config edito
(`base_config_version_id`); si ya no es la vigente -> ConfigObsoleta (409).

EL INTERRUPTOR DEL MARGEN ES LA CLAVE (E3, A4/D-2.3.12): margen encendido =
`ads_target_fraccion_margen_<plat>` presente con un valor en (0, 1]; apagado
= la clave se OMITE en la config nueva. Jamas se escribe NULL: la ausencia
es el apagado, y fraccion_desde_settings devuelve None -> abstencion
sin_fraccion -> el motor cae al target manual.

ADVERTENCIA DE RESPALDO (E2): editar el target manual con el margen
encendido (en la config RESULTANTE) exige `ack_respaldo=True`; sin el, la
edicion se rechaza con el mismo texto que la pantalla muestra ANTES de
guardar. El servidor no confia en el copy del HTML.

VALIDACION CON LOS LECTORES DEL MOTOR (regla 2): la config nueva se relee
con target_desde_settings / fraccion_desde_settings antes de persistirse; lo
que el motor rechazaria como config corrupta se rechaza aqui como
SettingsInvalido (422), jamas llega a una fila.

Valores como STRING en el JSONB (regla 4; misma forma que las configs
sembradas a mano: "20", "0.5", "10").
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal

import psycopg
from psycopg.types.json import Json

from app.apply import KINDS_QUOTA
from app.optimizer import goals as g

ACTOR = "settings-ui"

ADVERTENCIA_RESPALDO = (
    "este valor NO gobierna mientras el target por margen este encendido;"
    " queda de respaldo para cuando lo apagues"
)


class SettingsInvalido(Exception):
    """La edicion no se puede aplicar (422 en el endpoint). El mensaje ES el
    motivo que ve el operador, en espanol."""


class ConfigObsoleta(Exception):
    """El caller edito sobre una config que ya no es la vigente (409)."""


def clave_fraccion(platform: str) -> str:
    """Clave del interruptor del peldano margen (la misma que lee
    fraccion_desde_settings en app/optimizer/goals.py)."""
    return f"ads_target_fraccion_margen_{platform}"


def clave_cap(platform: str, kind: str) -> str:
    """Clave del cap diario (la misma que lee _cap_de_config en app/apply.py
    y el trigger apply_cap_de_config de 0002)."""
    return f"ads_apply_cap_{platform}_{kind}"


def _texto(valor: Decimal) -> str:
    """Decimal -> string sin notacion cientifica ("25", "0.5"), la forma de
    las configs sembradas a mano."""
    return format(valor, "f")


def _antes(valor) -> str:
    """El valor previo en el rastro del label: clave ausente se dice."""
    return "ausente" if valor is None else str(valor)


def proxima_config(
    actual: Mapping,
    platform: str,
    *,
    target_manual_pct: Decimal | None = None,
    margen_habilitado: bool | None = None,
    fraccion: Decimal | None = None,
    caps: Mapping[str, int | None] | None = None,
    ack_respaldo: bool = False,
) -> tuple[dict, list[str]]:
    """Config NUEVA a partir de la vigente + la lista legible de cambios.

    PURA (sin I/O). `None` = no tocar. Copia TODA la config (las claves de
    otras plataformas y `ads_optimizer_mode` viajan intactas). El margen se
    resuelve PRIMERO: la exigencia de ack_respaldo se evalua sobre la config
    RESULTANTE (apagar el margen y editar el manual en la misma edicion no
    pide ack: el manual va a gobernar). Una edicion que no cambia nada es
    invalida (una fila identica seria un rastro que miente)."""
    nuevo = dict(actual)
    cambios: list[str] = []

    k_fraccion = clave_fraccion(platform)
    if margen_habilitado is None:
        if fraccion is not None:
            raise SettingsInvalido("fraccion sin interruptor: manda margen.habilitado")
    elif margen_habilitado:
        if fraccion is None:
            raise SettingsInvalido("margen encendido exige una fraccion en (0, 1]")
        valor = _texto(fraccion)
        if nuevo.get(k_fraccion) != valor:
            cambios.append(f"margen encendido (fraccion {_antes(nuevo.get(k_fraccion))} -> {valor})")
            nuevo[k_fraccion] = valor
    else:
        if fraccion is not None:
            raise SettingsInvalido("margen apagado no lleva fraccion")
        if k_fraccion in nuevo:
            cambios.append(f"margen apagado (fraccion {nuevo.pop(k_fraccion)} -> ausente)")

    k_target = g.clave_target_plataforma(platform)
    if target_manual_pct is not None:
        if k_fraccion in nuevo and not ack_respaldo:
            raise SettingsInvalido(
                f"target manual con el margen encendido: {ADVERTENCIA_RESPALDO}."
                " Confirma con ack_respaldo para guardarlo como respaldo"
            )
        valor = _texto(target_manual_pct)
        if nuevo.get(k_target) != valor:
            cambios.append(f"target manual {_antes(nuevo.get(k_target))} -> {valor}")
            nuevo[k_target] = valor

    for kind, cap in (caps or {}).items():
        if kind not in KINDS_QUOTA:
            raise SettingsInvalido(f"cap desconocido {kind!r}: los kinds son {KINDS_QUOTA}")
        if cap is None:
            continue
        if cap < 0:
            raise SettingsInvalido(f"cap {kind} debe ser >= 0, llego {cap}")
        k_cap = clave_cap(platform, kind)
        if nuevo.get(k_cap) != str(cap):
            cambios.append(f"cap {kind} {_antes(nuevo.get(k_cap))} -> {cap}")
            nuevo[k_cap] = str(cap)

    if not cambios:
        raise SettingsInvalido("edicion vacia: nada que cambiar")
    try:
        g.target_desde_settings(nuevo, platform)
        g.fraccion_desde_settings(nuevo, platform)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    return nuevo, cambios


_SQL_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
_SQL_INSERTA = "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id"


def guarda_config(
    conn: psycopg.Connection,
    *,
    platform: str,
    base_config_version_id: int,
    ahora: dt.datetime,
    **edicion,
) -> dict:
    """Inserta la config NUEVA (fila de config_version) con label
    `settings UI · settings-ui · <fecha> · <plataforma>: <que cambio>`.

    `edicion` son los kwargs de `proxima_config`. Lee la vigente y exige que
    sea `base_config_version_id` (ConfigObsoleta si otro guardo antes);
    SettingsInvalido sube tal cual. La conexion puede venir con o sin
    autocommit: el INSERT se confirma aqui."""
    # Lock de aplicacion por transaccion (app_admin no tiene privilegio para
    # LOCK TABLE): dos guardados simultaneos se serializan y el segundo ve la
    # fila del primero -> 409, en vez de pisarla en silencio. En autocommit se
    # libera al instante y no estorba.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('config_version'))")
    fila = conn.execute(_SQL_VIGENTE).fetchone()
    vigente = fila[0] if fila is not None else None
    if vigente != base_config_version_id:
        raise ConfigObsoleta(
            f"la config vigente es {vigente}, no {base_config_version_id}:"
            " recarga la pantalla y vuelve a editar"
        )
    nuevo, cambios = proxima_config(fila[1], platform, **edicion)
    label = f"settings UI · {ACTOR} · {ahora.date().isoformat()} · {platform}: {', '.join(cambios)}"
    nuevo_id = conn.execute(_SQL_INSERTA, (label, Json(nuevo))).fetchone()[0]
    conn.commit()
    return {"config_version_id": nuevo_id, "created": True, "label": label}
