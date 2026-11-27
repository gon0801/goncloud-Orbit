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
from typing import Literal

import psycopg
from psycopg.types.json import Json

from app.apply_cuota import KINDS_QUOTA, cap_desde_settings
from app.dashboard_resumen import CANALES_POR_DEFECTO as CANALES_RESUMEN_META
from app.envios.estado import CLAVE_DIAS_CIERRE, dias_cierre_desde_settings
from app.estimacion_universo import universo_de
from app.optimizer import goals as g
from app.optimizer import pausa_economica as regla_pausa
from app.optimizer import productos
from app.optimizer import proteccion as subproteccion
from app.optimizer import temporada as temporada_mod
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


def _cap_entrante(platform: str, kind: str, cap) -> int:
    """Cuota de la EDICION, no de la config vigente.

    Unico camino: `cap_desde_settings` decide que forma se puede guardar.
    `null` explicito se rechaza (la forma de conservar el valor es omitir la
    llave); un entero valido vuelve como int canonico para escribirlo igual en
    toda la base (`"7"`, no `"007"`)."""
    if cap is None:
        raise SettingsInvalido(f"cap {kind} llego null: omite la cuota para conservar su valor")
    try:
        valor = cap_desde_settings({clave_cap(platform, kind): cap}, platform, kind)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    if valor is None:
        raise SettingsInvalido(f"cap {kind} llego null: omite la cuota para conservar su valor")
    return valor


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
        if modo != "off" and modo not in productos.MODOS_POR_TIPO[tipo]:
            permitidos = " o ".join(productos.MODOS_POR_TIPO[tipo])
            raise SettingsInvalido(
                f"modo de {tipo} por producto debe ser off o {permitidos}, llego {modo!r}"
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


def _aplica_orbit07(
    nuevo: dict,
    cambios: list[str],
    platform: str,
    *,
    proteccion_venta: str | None,
    economia_producto: str | None,
    economia_cambio: str | None,
) -> None:
    """Selecciones ORBIT 07: `off` quita la clave (ausente = semantica
    historica) y cualquier otro valor se escribe. Los lectores del motor
    validan la resultante; esta rama no toca meta, pausa ni limites."""
    for clave, valor in (
        (subproteccion.CLAVE_ACTIVA, proteccion_venta),
        (productos.clave_economia(platform), economia_producto),
        (g.clave_economia_cambio(platform), economia_cambio),
    ):
        if valor is None:
            continue
        if valor == "off":
            if clave in nuevo:
                cambios.append(f"{clave} {nuevo.pop(clave)} -> ausente")
            continue
        if nuevo.get(clave) != valor:
            cambios.append(f"{clave} {_antes(nuevo.get(clave))} -> {valor}")
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


def _exige_banda_manual(acos_pct: Decimal) -> None:
    try:
        g.exige_banda_meta_manual(acos_pct)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None


def _aplica_temporada(
    nuevo: dict, cambios: list[str], edicion_temporada: Mapping | Literal["off"] | None
) -> None:
    """Rama TEMPORADA 01 A de proxima_config: el objeto `ads_temporada`
    (global, como precio_modo_global). "off" quita la clave. La revision de
    tipos crudos vive en temporada.valida: sus mensajes son los que ve el
    operador (422), incluido el rechazo de true/false en campos de numero."""
    if edicion_temporada is None:
        return
    if edicion_temporada == "off":
        if temporada_mod.CLAVE_TEMPORADA in nuevo:
            cambios.append(
                f"temporadas de venta {nuevo.pop(temporada_mod.CLAVE_TEMPORADA)} -> ausente"
            )
        return
    try:
        config_t = temporada_mod.valida(edicion_temporada)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    blob = temporada_mod.como_guardada(config_t)
    if nuevo.get(temporada_mod.CLAVE_TEMPORADA) != blob:
        cambios.append("temporadas de venta (calendario y parametros)")
        nuevo[temporada_mod.CLAVE_TEMPORADA] = blob


def _aplica_campana_topada(nuevo: dict, cambios: list[str], crudo: Mapping | None) -> None:
    """Rama TEMPORADA 01 A: el criterio de campana topada (global; lo leen
    el aviso de Telegram y «Donde poner el dinero» con el mismo lector)."""
    if crudo is None:
        return
    try:
        topada = temporada_mod.valida_campana_topada(crudo)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    blob = temporada_mod.campana_topada_guardada(topada)
    if nuevo.get(temporada_mod.CLAVE_CAMPANA_TOPADA) != blob:
        cambios.append(
            f"criterio de campana topada {_antes(nuevo.get(temporada_mod.CLAVE_CAMPANA_TOPADA))}"
            f" -> {blob['pct']} % en {blob['dias']} de 7"
        )
        nuevo[temporada_mod.CLAVE_CAMPANA_TOPADA] = blob


def _aplica_temporada_modo(
    nuevo: dict, cambios: list[str], modos: Mapping[str, str] | None
) -> None:
    """Interruptor de temporada POR PLATAFORMA (None = no tocar): `off`
    quita la clave, shadow y live la escriben. Encender sin meses guardados
    lo rechaza la validacion final de la resultante (modo_desde_settings)."""
    if modos is None:
        return
    if not isinstance(modos, Mapping):
        raise SettingsInvalido(
            "El modo de temporada llegó mal: recarga la página y vuelve a guardar."
        )
    for plataforma, modo in modos.items():
        if plataforma not in temporada_mod.PLATAFORMAS_TEMPORADA:
            raise SettingsInvalido(f"plataforma de temporada desconocida: {plataforma!r}")
        clave = f"ads_temporada_{plataforma}"
        if modo == "off":
            if clave in nuevo:
                cambios.append(f"temporada {plataforma} {nuevo.pop(clave)} -> ausente")
        elif modo in temporada_mod.MODOS_MOTOR:
            if nuevo.get(clave) != modo:
                cambios.append(f"temporada {plataforma} {_antes(nuevo.get(clave))} -> {modo}")
                nuevo[clave] = modo
        else:
            raise SettingsInvalido(
                f"modo de temporada de {plataforma} debe ser off, shadow o live, llego {modo!r}"
            )


def _aplica_meta_alta(
    nuevo: dict, cambios: list[str], metas: Mapping[str, str | int | Decimal] | None
) -> None:
    """Meta de ACoS de temporada alta por plataforma (global la pareja)."""
    if metas is None:
        return
    if not isinstance(metas, Mapping):
        raise SettingsInvalido(
            "La meta de temporada alta llegó mal: recarga la página y vuelve a guardar."
        )
    for plataforma, crudo in metas.items():
        if plataforma not in temporada_mod.PLATAFORMAS_TEMPORADA:
            raise SettingsInvalido(f"plataforma de temporada desconocida: {plataforma!r}")
        try:
            valor = temporada_mod.valida_meta_alta(crudo)
        except ValueError as exc:
            raise SettingsInvalido(str(exc)) from None
        clave = f"ads_temporada_meta_alta_{plataforma}"
        texto = _texto(valor)
        if nuevo.get(clave) != texto:
            cambios.append(f"meta de alta {plataforma} {_antes(nuevo.get(clave))} -> {texto}")
            nuevo[clave] = texto


def _aplica_recuperacion(
    nuevo: dict,
    cambios: list[str],
    platform: str,
    modo: str | None,
    memoria: int | float | str | None,
) -> None:
    """Campos de subida rapida bajo «Motor de bids» (bloque A los guarda;
    el motor que los lee llega con el bloque C, detras de
    ads_bid_recuperacion_<plat>): `off` quita la clave, shadow y live la
    escriben; la memoria del bid mas alto va de 90 a 730 dias."""
    if modo is not None:
        clave = f"ads_bid_recuperacion_{platform}"
        if modo == "off":
            if clave in nuevo:
                cambios.append(f"subida rapida {nuevo.pop(clave)} -> ausente")
        elif modo in temporada_mod.MODOS_MOTOR:
            if nuevo.get(clave) != modo:
                cambios.append(f"subida rapida {_antes(nuevo.get(clave))} -> {modo}")
                nuevo[clave] = modo
        else:
            raise SettingsInvalido(f"la subida rapida debe ser off, shadow o live, llego {modo!r}")
    if memoria is None:
        return
    clave = f"ads_bid_recuperacion_memoria_dias_{platform}"
    try:
        valor = temporada_mod.memoria_recuperacion_desde_settings({clave: memoria}, platform)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    if nuevo.get(clave) != valor or type(nuevo.get(clave)) is not int:
        cambios.append(f"memoria del bid mas alto {_antes(nuevo.get(clave))} -> {valor}")
        nuevo[clave] = valor


def _aplica_envios_dias_cierre(nuevo: dict, cambios: list[str], dias: int | None) -> None:
    """Dias para cerrar por vencimiento un envio de Mercado Libre (global; None =
    no tocar). Solo se valida el valor entrante: asi corrige uno ilegible."""
    if dias is None:
        return
    texto = str(dias)
    try:
        dias_cierre_desde_settings({CLAVE_DIAS_CIERRE: texto})
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    if nuevo.get(CLAVE_DIAS_CIERRE) != texto:
        antes = _antes(nuevo.get(CLAVE_DIAS_CIERRE))
        cambios.append(f"dias de cierre de envios {antes} -> {texto}")
        nuevo[CLAVE_DIAS_CIERRE] = texto


def _valida_resultante_temporada(nuevo: Mapping) -> None:
    """TEMPORADA 01 A: la resultante se relee con los lectores del motor
    (temporada, criterio de campana y modos de LAS DOS plataformas, porque la
    tarjeta es global). Aqui sale «Para encender la temporada primero guarda
    los meses.» cuando el interruptor llega sin calendario guardado."""
    temporada_mod.desde_settings(nuevo)
    temporada_mod.campana_topada_desde_settings(nuevo)
    for plataforma in temporada_mod.PLATAFORMAS_TEMPORADA:
        temporada_mod.modo_desde_settings(nuevo, plataforma)
        temporada_mod.meta_alta_desde_settings(nuevo, plataforma)
        temporada_mod.modo_recuperacion_desde_settings(nuevo, plataforma)
        temporada_mod.memoria_recuperacion_desde_settings(nuevo, plataforma)


def _aplica_ubicacion(
    nuevo: dict,
    cambios: list[str],
    platform: str,
    *,
    modo: str | None,
    solo_bajadas: bool | None,
    max_en_espera: int | None,
) -> None:
    """AUTO-04: settings del motor de ubicaciones. `off` quita la clave."""
    from app.optimizer import ubicacion as u

    if modo is not None:
        clave = f"ads_ubicacion_{platform}"
        try:
            validado = u.modo_ubicacion_desde_settings({clave: modo}, platform)
        except ValueError as exc:
            raise SettingsInvalido(str(exc)) from None
        if validado == "off":
            if clave in nuevo:
                cambios.append(f"ubicaciones {platform} {nuevo.pop(clave)} -> ausente")
        elif nuevo.get(clave) != validado:
            cambios.append(f"ubicaciones {platform} {_antes(nuevo.get(clave))} -> {validado}")
            nuevo[clave] = validado
    if solo_bajadas is not None:
        clave = f"ads_ubicacion_solo_bajadas_{platform}"
        try:
            validado_b = u.solo_bajadas_desde_settings({clave: solo_bajadas}, platform)
        except ValueError as exc:
            raise SettingsInvalido(str(exc)) from None
        texto = "true" if validado_b else "false"
        if nuevo.get(clave) != texto:
            cambios.append(f"solo bajadas {platform} {_antes(nuevo.get(clave))} -> {texto}")
            nuevo[clave] = texto
    if max_en_espera is not None:
        clave = f"ads_ubicacion_max_en_espera_{platform}"
        try:
            validado_n = u.max_en_espera_desde_settings({clave: max_en_espera}, platform)
        except ValueError as exc:
            raise SettingsInvalido(str(exc)) from None
        if nuevo.get(clave) != str(validado_n):
            cambios.append(f"max en espera {platform} {_antes(nuevo.get(clave))} -> {validado_n}")
            nuevo[clave] = str(validado_n)


def _valida_resultante_ubicacion(nuevo: Mapping, platform: str) -> None:
    from app.optimizer import ubicacion as u

    try:
        u.modo_ubicacion_desde_settings(nuevo, platform)
        u.solo_bajadas_desde_settings(nuevo, platform)
        u.max_en_espera_desde_settings(nuevo, platform)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None


def _aplica_pausa_economica(
    nuevo: dict, cambios: list[str], platform: str, modo: str | None
) -> None:
    """P03: modo por mercado; delega validacion y texto en el motor puro."""
    if modo is None:
        return
    try:
        regla_pausa.aplica_modo_en_config(nuevo, cambios, platform, modo)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None


def proxima_config(
    actual: Mapping,
    platform: str,
    *,
    target_manual_pct: Decimal | None = None,
    margen_habilitado: bool | None = None,
    fraccion: Decimal | None = None,
    caps: Mapping[str, object] | None = None,
    ack_respaldo: bool = False,
    confianza_recorte: Decimal | None = None,
    confianza_subida: Decimal | None = None,
    motor_bid: str | None = None,
    precio_modo_global: str | None = None,
    precio_modo_universo: Mapping[str, str] | None = None,
    resumen_acos_objetivo: Mapping[str, Decimal | str | None] | None = None,
    producto_modo: Mapping[str, str] | None = None,
    limites_pausa: productos.LimitesPausa | None = None,
    proteccion_venta: str | None = None,
    economia_producto: str | None = None,
    economia_cambio: str | None = None,
    enciende_pausa: bool = False,
    temporada: Mapping | Literal["off"] | None = None,
    campana_topada: Mapping | None = None,
    temporada_modo: Mapping[str, str] | None = None,
    meta_alta: Mapping[str, str | int | Decimal] | None = None,
    recuperacion_modo: str | None = None,
    recuperacion_memoria_dias: int | float | str | None = None,
    envios_dias_cierre: int | None = None,
    ubicacion_modo: str | None = None,
    ubicacion_solo_bajadas: bool | None = None,
    ubicacion_max_en_espera: int | None = None,
    pausa_economica_modo: str | None = None,
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
    Las selecciones ORBIT 07 (`proteccion_venta`, `economia_producto`,
    `economia_cambio`) usan `off` para quitar la clave; una edicion que no
    cambia nada es invalida (una fila identica seria un rastro que miente).
    Las cuotas (`caps`) pasan por `_cap_entrante`/`cap_desde_settings`: omitir
    una llave conserva su valor, `null` explicito, booleanos, fracciones y
    texto ilegible se rechazan, y un entero valido se escribe canonicalizado.
    TEMPORADA 01 A: la tarjeta «Temporadas de venta» es global (se guarda
    igual desde cualquier plataforma) y trae `temporada`, `campana_topada`,
    `temporada_modo` (par MX/US) y `meta_alta` (par MX/US); la subida rapida
    (`recuperacion_modo`, `recuperacion_memoria_dias`) es por plataforma.
    Los objetos llegan crudos (Any en CuerpoSettings) y los revisa
    temporada.valida antes que nada: un booleano nunca llega como 0 o 1."""
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
            _exige_banda_manual(target_manual_pct)
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
    _aplica_orbit07(
        nuevo,
        cambios,
        platform,
        proteccion_venta=proteccion_venta,
        economia_producto=economia_producto,
        economia_cambio=economia_cambio,
    )
    _aplica_temporada(nuevo, cambios, temporada)
    _aplica_campana_topada(nuevo, cambios, campana_topada)
    _aplica_temporada_modo(nuevo, cambios, temporada_modo)
    _aplica_meta_alta(nuevo, cambios, meta_alta)
    _aplica_recuperacion(nuevo, cambios, platform, recuperacion_modo, recuperacion_memoria_dias)
    _aplica_envios_dias_cierre(nuevo, cambios, envios_dias_cierre)

    for kind, cap in (caps or {}).items():
        if kind not in KINDS_QUOTA:
            raise SettingsInvalido(f"cap desconocido {kind!r}: los kinds son {KINDS_QUOTA}")
        valor = _cap_entrante(platform, kind, cap)
        k_cap = clave_cap(platform, kind)
        if nuevo.get(k_cap) != str(valor):
            cambios.append(f"cap {kind} {_antes(nuevo.get(k_cap))} -> {valor}")
            nuevo[k_cap] = str(valor)

    _aplica_ubicacion(
        nuevo,
        cambios,
        platform,
        modo=ubicacion_modo,
        solo_bajadas=ubicacion_solo_bajadas,
        max_en_espera=ubicacion_max_en_espera,
    )
    _aplica_pausa_economica(nuevo, cambios, platform, pausa_economica_modo)

    if not cambios:
        raise EdicionVacia("edicion vacia: nada que cambiar")
    try:
        g.target_desde_settings(nuevo, platform)
        g.fraccion_desde_settings(nuevo, platform)
        g.confianza_recorte_desde_settings(nuevo, platform)
        g.confianza_subida_desde_settings(nuevo, platform)
        g.politica_bid_desde_settings(nuevo, platform)
        g.version_economia_cambio_desde_settings(nuevo, platform)
        # Mercado Libre no tiene decisiones por producto que releer.
        if platform in productos.PLATAFORMAS:
            productos.modos_desde_settings(nuevo, platform)
            productos.economia_desde_settings(nuevo, platform)
            productos.limites_pausa_desde_settings(nuevo, platform)
        subproteccion.activa_desde_settings(nuevo)
        exige_limites_guardados(nuevo, platform)
        for kind in KINDS_QUOTA:
            cap_desde_settings(nuevo, platform, kind)
        # TEMPORADA 01 A: la resultante se relee con los lectores del motor
        # para LAS DOS plataformas (la tarjeta es global); aqui sale el
        # «Para encender la temporada primero guarda los meses.» cuando el
        # interruptor llega sin calendario guardado.
        _valida_resultante_temporada(nuevo)
    except ValueError as exc:
        raise SettingsInvalido(str(exc)) from None
    # S.2: la resultante se valida con el vocabulario del motor de precios
    # (una corrupcion previa tambien rechaza, como motor_bid con bandas_v1).
    _valida_precio_global(nuevo.get("precio_modo_global"), ausente_ok=True)
    _valida_precio_universo(nuevo.get("precio_modo_universo"), ausente_ok=True)
    _valida_resumen_acos_objetivo(nuevo.get(CLAVE_RESUMEN_ACOS), ausente_ok=True)
    _valida_resultante_ubicacion(nuevo, platform)
    return nuevo, cambios


_SQL_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
_SQL_INSERTA = "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id"
_SQL_LOCK_CONFIG_VERSION = "SELECT pg_advisory_xact_lock(hashtext('config_version'))"


def lock_config_version(conn: psycopg.Connection) -> None:
    """Candado transaccional compartido para serializar lecturas+escrituras de
    `config_version` (usado por `guarda_config` y los endpoints de temporada)."""
    conn.execute(_SQL_LOCK_CONFIG_VERSION)


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
    lock_config_version(conn)
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
    salida = {"config_version_id": nuevo_id, "created": True, "label": label}
    # TEMPORADA 01 A: cuando la edicion toca la temporada CON VALOR (la clave
    # sola no: el endpoint manda los seis kwargs siempre, hallazgo de Kimi),
    # la respuesta trae «Así queda tu año» (la misma resumen que la vista
    # previa) y el mensaje verde que pega settings.js. SELECT de la cascada
    # para la meta de hoy.
    if any(
        edicion.get(clave) is not None
        for clave in ("temporada", "campana_topada", "temporada_modo", "meta_alta")
    ):
        from app.dashboard_temporada import resumen_temporada_para_guardado

        resumen_guardado = resumen_temporada_para_guardado(conn, fila[1], nuevo, ahora.date())
        if resumen_guardado is not None:
            salida["resumen_temporada"] = resumen_guardado.como_dict()
            salida["mensaje"] = temporada_mod.mensaje_guardado(resumen_guardado)
    return salida
