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
con target_desde_settings / fraccion_desde_settings / confianza_* /
motor_evidencia_desde_settings antes de persistirse; lo que el motor
rechazaria como config corrupta se rechaza aqui como SettingsInvalido (422),
jamas llega a una fila.

Valores como STRING en el JSONB (regla 4; misma forma que las configs
sembradas a mano: "20", "0.5", "10").
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.types.json import Json

from app.apply import KINDS_QUOTA
from app.dashboard_resumen import CANALES_POR_DEFECTO as CANALES_RESUMEN_META
from app.estimacion_universo import universo_de
from app.optimizer import goals as g
from app.optimizer import productos
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.cortes import LEGACY_NEGATIVE, NEGATIVE_COST_MIN
from app.precio.config import MODOS as MODOS_PRECIO

ACTOR = "settings-ui"
CLAVE_RESUMEN_ACOS = "resumen_acos_objetivo"

ADVERTENCIA_RESPALDO = (
    "este valor NO gobierna mientras el target por margen este encendido;"
    " queda de respaldo para cuando lo apagues"
)


class SettingsInvalido(Exception):
    """La edicion no se puede aplicar (422 en el endpoint). El mensaje ES el
    motivo que ve el operador, en espanol."""


class EdicionVacia(SettingsInvalido):
    """La edicion no cambia nada: 422 en el endpoint; el CLI productos-modo
    lo toma como exito sin escribir (el valor ya era el vigente)."""


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


def _aplica_motor_bid(nuevo: dict, cambios: list[str], platform: str, motor_bid: str) -> None:
    """Rama M.3 de proxima_config. Solo los dos valores sellados viajan:
    `niveles_v3` enciende, `apagado` QUITA la clave (ausente = el motor
    no mueve bids). Valor ajeno = SettingsInvalido (el endpoint solo
    manda su Literal: bandas_v1 -> 422)."""
    k_motor = g.clave_bid_politica(platform)
    if motor_bid == "apagado":
        if k_motor in nuevo:
            cambios.append(f"motor bid {nuevo.pop(k_motor)} -> ausente")
        return
    if motor_bid == g.POLITICA_BID_VIGENTE:
        if nuevo.get(k_motor) != motor_bid:
            cambios.append(f"motor bid {_antes(nuevo.get(k_motor))} -> {motor_bid}")
            nuevo[k_motor] = motor_bid
        return
    raise SettingsInvalido(
        f"motor bid debe ser {g.POLITICA_BID_VIGENTE!r} o 'apagado', llego {motor_bid!r}"
    )


def _valida_precio_global(valor, *, ausente_ok: bool) -> None:
    """El global vive en el vocabulario del motor (`off/shadow/live`).

    Ausente solo pasa con `ausente_ok` (config vieja sin la clave: la
    pagina la tolera, la corrida la rechaza)."""
    if valor is None:
        if ausente_ok:
            return
        raise SettingsInvalido("config sin precio_modo_global")
    if valor not in MODOS_PRECIO:
        raise SettingsInvalido(f"precio_modo_global debe ser off, shadow o live, llego {valor!r}")


def _valida_precio_universo(valor, *, ausente_ok: bool) -> None:
    """El objeto por universo: llaves `plataforma/canal` del registro de
    universos, valores del vocabulario del motor."""
    if valor is None:
        if ausente_ok:
            return
        raise SettingsInvalido("config sin precio_modo_universo")
    if not isinstance(valor, Mapping):
        raise SettingsInvalido(
            f"precio_modo_universo debe ser objeto plataforma/canal -> modo: {valor!r}"
        )
    for universo, modo in valor.items():
        partes = universo.split("/") if isinstance(universo, str) else []
        if len(partes) != 2 or not all(partes) or universo_de(*partes) is None:
            raise SettingsInvalido(
                f"precio_modo_universo: universo fuera del registro: {universo!r}"
            )
        if modo not in MODOS_PRECIO:
            raise SettingsInvalido(
                f"precio_modo_universo: modo invalido para {universo} (off/shadow/live): {modo!r}"
            )


def _aplica_precio_modos(
    nuevo: dict, cambios: list[str], modo_global: str | None, modo_universo: Mapping | None
) -> None:
    """Ramas S.2 de proxima_config (helper: las dos ramas juntas)."""
    if modo_global is not None:
        _aplica_precio_global(nuevo, cambios, modo_global)
    if modo_universo is not None:
        _aplica_precio_universo(nuevo, cambios, modo_universo)


def _aplica_precio_global(nuevo: dict, cambios: list[str], modo: str) -> None:
    """Rama S.2 de proxima_config: el modo global, igual desde cualquier
    plataforma (la config es global)."""
    _valida_precio_global(modo, ausente_ok=False)
    if nuevo.get("precio_modo_global") != modo:
        cambios.append(f"precio modo global {_antes(nuevo.get('precio_modo_global'))} -> {modo}")
        nuevo["precio_modo_global"] = modo


def _aplica_precio_universo(nuevo: dict, cambios: list[str], entrante: Mapping) -> None:
    """Rama S.2 de proxima_config: fusion por llave sobre el objeto vigente
    (el JS manda solo lo cambiado; un POST parcial no borra los demas)."""
    _valida_precio_universo(entrante, ausente_ok=False)
    vigente = nuevo.get("precio_modo_universo")
    fusionado = dict(vigente) if isinstance(vigente, Mapping) else {}
    for universo, modo in entrante.items():
        if fusionado.get(universo) != modo:
            cambios.append(f"precio modo {universo} {_antes(fusionado.get(universo))} -> {modo}")
            fusionado[universo] = modo
    nuevo["precio_modo_universo"] = fusionado


def _decimal_acos_resumen(canal: str, crudo) -> Decimal | None:
    """Valida el porcentaje opcional del Resumen (> 0 y <= 1000). None/"" limpia."""
    if crudo is None or (isinstance(crudo, str) and crudo.strip() == ""):
        return None
    if isinstance(crudo, bool):
        raise SettingsInvalido(f"resumen_acos_objetivo[{canal}] invalido: {crudo!r}")
    try:
        valor = Decimal(str(crudo).strip())
    except (InvalidOperation, ValueError):
        raise SettingsInvalido(
            f"resumen_acos_objetivo[{canal}] debe ser numero positivo: {crudo!r}"
        ) from None
    if not valor.is_finite() or valor <= 0 or valor > Decimal("1000"):
        raise SettingsInvalido(f"resumen_acos_objetivo[{canal}] debe estar en (0, 1000]: {crudo!r}")
    return valor


def _valida_resumen_acos_objetivo(valor, *, ausente_ok: bool) -> None:
    """Objeto canal -> porcentaje opcional del Resumen."""
    if valor is None:
        if ausente_ok:
            return
        raise SettingsInvalido(f"config sin {CLAVE_RESUMEN_ACOS}")
    if not isinstance(valor, Mapping):
        raise SettingsInvalido(
            f"{CLAVE_RESUMEN_ACOS} debe ser objeto canal -> porcentaje: {valor!r}"
        )
    for canal, crudo in valor.items():
        if canal not in CANALES_RESUMEN_META:
            raise SettingsInvalido(f"{CLAVE_RESUMEN_ACOS}: canal fuera del catalogo: {canal!r}")
        _decimal_acos_resumen(canal, crudo)


def metas_resumen_desde_settings(settings: Mapping | None) -> dict[str, str | None]:
    """Metas de ACoS del Resumen por canal (None = sin configurar)."""
    crudo = settings.get(CLAVE_RESUMEN_ACOS) if isinstance(settings, Mapping) else None
    mapa = crudo if isinstance(crudo, Mapping) else {}
    salida: dict[str, str | None] = {}
    for canal in CANALES_RESUMEN_META:
        valor = mapa.get(canal)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            salida[canal] = None
            continue
        try:
            dec = _decimal_acos_resumen(canal, valor)
        except SettingsInvalido:
            salida[canal] = None
            continue
        salida[canal] = None if dec is None else _texto(dec)
    return salida


def _aplica_resumen_acos_objetivo(
    nuevo: dict, cambios: list[str], entrante: Mapping | None
) -> None:
    """Fusiona el ACoS objetivo del Resumen por canal; vacio/null quita la clave."""
    if entrante is None:
        return
    _valida_resumen_acos_objetivo(entrante, ausente_ok=False)
    vigente = nuevo.get(CLAVE_RESUMEN_ACOS)
    fusionado = dict(vigente) if isinstance(vigente, Mapping) else {}
    for canal, crudo in entrante.items():
        dec = _decimal_acos_resumen(canal, crudo)
        if dec is None:
            if canal in fusionado:
                cambios.append(f"acos objetivo resumen {canal} {fusionado.pop(canal)} -> ausente")
        else:
            texto = _texto(dec)
            if fusionado.get(canal) != texto:
                cambios.append(
                    f"acos objetivo resumen {canal} {_antes(fusionado.get(canal))} -> {texto}"
                )
                fusionado[canal] = texto
    if fusionado:
        nuevo[CLAVE_RESUMEN_ACOS] = fusionado
    else:
        nuevo.pop(CLAVE_RESUMEN_ACOS, None)


def _aplica_producto_modo(
    nuevo: dict,
    cambios: list[str],
    platform: str,
    producto_modo: Mapping[str, str] | None,
    enciende_pausa: bool,
) -> None:
    """Interruptores de AUTO-03 (meta y pausa por producto; None = no tocar):
    off QUITA la clave, shadow y live la escriben. La pausa en live solo
    entra con `enciende_pausa`, que pasa unicamente
    productos_modo.cambia_pausa: ahi el encendido descarta las practicas en
    la misma transaccion (AUTO-03 I.7)."""
    for tipo, modo in (producto_modo or {}).items():
        if tipo not in ("meta", "pausa"):
            raise SettingsInvalido(f"interruptor de producto desconocido: {tipo!r}")
        if modo not in g.MODOS:
            raise SettingsInvalido(
                f"modo de {tipo} por producto debe ser off, shadow o live, llego {modo!r}"
            )
        if tipo == "pausa" and modo == "live" and not enciende_pausa:
            raise SettingsInvalido(
                "la pausa por producto solo se enciende con el boton de la pantalla de"
                " productos o con productos-modo --pausa live"
            )
        clave = productos.clave_modo(tipo, platform)
        if modo == "off":
            if clave in nuevo:
                cambios.append(f"{tipo} por producto {nuevo.pop(clave)} -> ausente")
        elif nuevo.get(clave) != modo:
            cambios.append(f"{tipo} por producto {_antes(nuevo.get(clave))} -> {modo}")
            nuevo[clave] = modo


def _aplica_limites_pausa(
    nuevo: dict, cambios: list[str], platform: str, limites: productos.LimitesPausa | None
) -> None:
    """Limites de la pausa por producto (AUTO-03 S8; None = no tocar)."""
    if limites is None:
        return
    valida_limites_pausa(platform, limites)
    for tipo, valor in (
        ("clics", str(limites.clics_min)),
        ("gasto", _texto(limites.gasto_min.valor.normalize())),
    ):
        clave = productos.clave_limite(tipo, platform)
        if nuevo.get(clave) != valor:
            cambios.append(f"pausa por producto {tipo} {_antes(nuevo.get(clave))} -> {valor}")
            nuevo[clave] = valor


def valida_limites_pausa(platform: str, limites: productos.LimitesPausa) -> None:
    """Nunca por debajo de los pisos del camino negative y con el gasto en la
    moneda de la plataforma. La usan el guardado y la simulacion (H.8)."""
    moneda = PLATAFORMAS_MONEDA[platform]
    if isinstance(limites.clics_min, bool) or limites.clics_min < LEGACY_NEGATIVE:
        raise SettingsInvalido(
            f"clics minimos de la pausa: al menos {LEGACY_NEGATIVE}, llego {limites.clics_min}"
        )
    if limites.gasto_min.moneda != moneda:
        raise SettingsInvalido(
            f"gasto minimo de la pausa en {moneda}, llego {limites.gasto_min.moneda}"
        )
    piso = NEGATIVE_COST_MIN[platform]
    if not limites.gasto_min.valor.is_finite() or limites.gasto_min.valor < piso:
        raise SettingsInvalido(
            f"gasto minimo de la pausa: al menos {_texto(piso)} {moneda},"
            f" llego {limites.gasto_min.valor}"
        )


def limites_pausa_guardados(settings: Mapping, platform: str) -> bool:
    """Las dos claves de limites de la pausa estan en la config con valor (S8):
    sin ellas (o en null, que el lector toma como ausente) la pausa corre con
    los valores por omision, que nadie eligio."""
    return all(
        settings.get(productos.clave_limite(t, platform)) is not None for t in ("clics", "gasto")
    )


def exige_limites_guardados(settings: Mapping, platform: str) -> None:
    """La pausa en live exige sus dos claves de limites (S8). Una plataforma
    sin pausa por producto no tiene nada que exigir."""
    if platform not in productos.PLATAFORMAS:
        return
    if productos.modos_desde_settings(settings, platform).pausa == "live" and not (
        limites_pausa_guardados(settings, platform)
    ):
        raise SettingsInvalido(
            f"la pausa en vivo exige guardar antes los limites de clics y gasto de {platform}"
        )


def proxima_config(
    actual: Mapping,
    platform: str,
    *,
    target_manual_pct: Decimal | None = None,
    margen_habilitado: bool | None = None,
    fraccion: Decimal | None = None,
    caps: Mapping[str, int | None] | None = None,
    ack_respaldo: bool = False,
    confianza_recorte: Decimal | None = None,
    confianza_subida: Decimal | None = None,
    motor_bid: str | None = None,
    precio_modo_global: str | None = None,
    precio_modo_universo: Mapping[str, str] | None = None,
    resumen_acos_objetivo: Mapping[str, Decimal | str | None] | None = None,
    producto_modo: Mapping[str, str] | None = None,
    limites_pausa: productos.LimitesPausa | None = None,
    enciende_pausa: bool = False,
) -> tuple[dict, list[str]]:
    """Config NUEVA a partir de la vigente + la lista legible de cambios.

    PURA (sin I/O). `None` = no tocar; un valor igual al vigente tampoco es
    cambio. Copia TODA la config (las claves de otras plataformas y
    `ads_optimizer_mode` viajan intactas). El margen se resuelve PRIMERO: la
    exigencia de ack_respaldo se evalua sobre la config RESULTANTE y solo
    cuando el manual CAMBIA (apagar el margen y editar el manual en la misma
    edicion no pide ack: el manual va a gobernar). Los modos de precio son
    GLOBALES (se guardan igual desde cualquier plataforma) y el objeto por
    universo se FUSIONA por llave (un POST parcial no borra los demas).
    Una edicion que no cambia nada es invalida (una fila identica seria un
    rastro que miente)."""
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
            antes = _antes(nuevo.get(k_fraccion))
            cambios.append(f"margen encendido (fraccion {antes} -> {valor})")
            nuevo[k_fraccion] = valor
    else:
        if fraccion is not None:
            raise SettingsInvalido("margen apagado no lleva fraccion")
        if k_fraccion in nuevo:
            cambios.append(f"margen apagado (fraccion {nuevo.pop(k_fraccion)} -> ausente)")

    k_target = g.clave_target_plataforma(platform)
    if target_manual_pct is not None:
        valor = _texto(target_manual_pct)
        if nuevo.get(k_target) != valor:
            if nuevo.get(k_fraccion) is not None and not ack_respaldo:
                raise SettingsInvalido(
                    f"target manual con el margen encendido: {ADVERTENCIA_RESPALDO}."
                    " Confirma con ack_respaldo para guardarlo como respaldo"
                )
            cambios.append(f"target manual {_antes(nuevo.get(k_target))} -> {valor}")
            nuevo[k_target] = valor

    # A3: confianzas del motor (solo cambian si se mandan; ausentes en la
    # config = defaults del lector, nunca se escriben solos).
    for nombre, clave, entrante in (
        ("recorte", g.clave_confianza_recorte(platform), confianza_recorte),
        ("subida", g.clave_confianza_subida(platform), confianza_subida),
    ):
        if entrante is not None:
            valor = _texto(entrante)
            if nuevo.get(clave) != valor:
                cambios.append(f"confianza {nombre} {_antes(nuevo.get(clave))} -> {valor}")
                nuevo[clave] = valor

    # A6: interruptor del motor de bids (None = no tocar).
    if motor_bid is not None:
        _aplica_motor_bid(nuevo, cambios, platform, motor_bid)

    _aplica_precio_modos(nuevo, cambios, precio_modo_global, precio_modo_universo)
    _aplica_resumen_acos_objetivo(nuevo, cambios, resumen_acos_objetivo)
    _aplica_producto_modo(nuevo, cambios, platform, producto_modo, enciende_pausa)
    _aplica_limites_pausa(nuevo, cambios, platform, limites_pausa)

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
        raise EdicionVacia("edicion vacia: nada que cambiar")
    try:
        g.target_desde_settings(nuevo, platform)
        g.fraccion_desde_settings(nuevo, platform)
        g.confianza_recorte_desde_settings(nuevo, platform)
        g.confianza_subida_desde_settings(nuevo, platform)
        g.politica_bid_desde_settings(nuevo, platform)
        g.economia_cambio_desde_settings(nuevo, platform)
        productos.modos_desde_settings(nuevo, platform)
        productos.limites_pausa_desde_settings(nuevo, platform)
        exige_limites_guardados(nuevo, platform)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    # S.2: la resultante se valida con el vocabulario del motor de precios
    # (una corrupcion previa tambien rechaza, como motor_bid con bandas_v1).
    _valida_precio_global(nuevo.get("precio_modo_global"), ausente_ok=True)
    _valida_precio_universo(nuevo.get("precio_modo_universo"), ausente_ok=True)
    _valida_resumen_acos_objetivo(nuevo.get(CLAVE_RESUMEN_ACOS), ausente_ok=True)
    return nuevo, cambios


_SQL_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
_SQL_INSERTA = "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id"


def config_vigente_id(conn: psycopg.Connection) -> int | None:
    """Id de la config vigente (None sin ninguna): la base de una edicion que
    no viene de una pantalla, como el CLI productos-modo."""
    fila = conn.execute(_SQL_VIGENTE).fetchone()
    return None if fila is None else fila[0]


def guarda_config(
    conn: psycopg.Connection,
    *,
    platform: str,
    base_config_version_id: int,
    ahora: dt.datetime,
    actor: str = ACTOR,
    confirma: bool = True,
    **edicion,
) -> dict:
    """Inserta la config NUEVA (fila de config_version) con label
    `settings UI · settings-ui · <fecha> · <plataforma>: <que cambio>`.

    `edicion` son los kwargs de `proxima_config`; `actor` firma el label
    (el CLI productos-modo firma con su nombre). Lee la vigente y exige que
    sea `base_config_version_id` (ConfigObsoleta si otro guardo antes);
    SettingsInvalido sube tal cual. La conexion puede venir con o sin
    autocommit: el INSERT se confirma aqui, salvo con `confirma=False`, que
    deja el commit al llamador (el encendido de la pausa, AUTO-03 I.7)."""
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
    label = f"settings UI · {actor} · {ahora.date().isoformat()} · {platform}: {', '.join(cambios)}"
    nuevo_id = conn.execute(_SQL_INSERTA, (label, Json(nuevo))).fetchone()[0]
    if confirma:
        conn.commit()
    return {"config_version_id": nuevo_id, "created": True, "label": label}
