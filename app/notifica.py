"""Canal de avisos Telegram (ORBIT 04, task 3.3; sellados 2 y 19; APPLY.md 10.2).

FAIL-SILENT por diseno: un fallo del canal JAMAS tumba el ciclo que notifica
— deja WARNING en el log y la NOTA ``notes['telegram']`` del ciclo ejecutor
(visible en Salud), que es la UNICA visibilidad del fallo: el silencio del
canal no es invisible (sellado 2, decision 19). Canal DESHABILITADO (sin
secrets) NO es fallo: los ``notifica_*`` devuelven True y NO generan NOTA.
``envia_aviso`` es la excepcion (JEV ADS 02 S.8): inactivo devuelve False
y no llama, porque ese silencio no cuenta como entrega.

Config: ``<ORBIT_SECRETS_DIR>/telegram.json`` con ``{"bot_token": "...",
"chat_id": "..."}`` (strings no vacios; claves extra toleradas, mismo patron
que ``app.ads.config``). Sin dir/archivo, JSON invalido o claves faltantes ->
canal deshabilitado con ``logger.info`` UNA vez por proceso (no configurado
no es fallo, jamas warning). ``bot_token`` via ``register_secret``: el token
viaja en la URL del POST, asi que cualquier mensaje de error que la ecoe pasa
por ``scrub``.

Builders PUROS (sin red): arman el mensaje SIN secretos, texto plano SIN
parse_mode (sin riesgo de inyeccion HTML/Markdown desde un search_term).
``transport`` inyecta el httpx de los tests unitarios (patron del repo);
``_transporte_test`` es la puerta de los tests de INTEGRACION del ciclo (el
ciclo llama a los ``notifica_*`` sin transport). Toda la superficie publica
devuelve bool y JAMAS levanta excepciones hacia arriba.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from app import formato, notifica_contribucion
from app.redaction import install_scrub_filter, register_secret, scrub
from app.secrets import directorio_secretos

if TYPE_CHECKING:
    # Solo anotacion: importarlo en runtime crearia el ciclo
    # apply_harvest -> notifica -> apply_harvest.
    from app.apply_harvest import AlertaHarvest

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

TELEGRAM_FILENAME = "telegram.json"

# ~10s de tope por envio (mismo espiritu de timeouts cortos del cliente Ads).
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# Puerta de los tests de integracion (docstring del modulo); produccion la
# deja en None y usa el transport real de httpx.
_transporte_test: httpx.BaseTransport | None = None

# Familia de efecto por kind — ESPEJO de la columna GENERATED de apply_queue
# (0002: pause -> entity_cut; negative y harvest -> term_cut; regla 2).
FAMILIA_DE_KIND = {"pause": "entity_cut", "negative": "term_cut", "harvest": "term_cut"}


@dataclass(frozen=True)
class CorteEncolado:
    """Lo que el aviso de encola necesita del corte NUEVO (sellado 2: la
    ventana 48h es el dato que importa al dueno). La construye apply_cola al
    commit de cada INSERT de la cola; notifica la consume."""

    platform: str
    kind: str
    search_term: str | None
    vence_el: dt.datetime
    modo: str


@dataclass(frozen=True)
class _ConfigCanal:
    """Config resuelta del canal. El token JAMAS se repr: vive en la URL."""

    bot_token: str
    chat_id: str

    def url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/sendMessage"


# Cache por proceso (el resolve lee el FS una sola vez; el logger.info de
# "deshabilitado" sale UNA vez). `_reset()` es la puerta de los tests.
_estado: dict = {}


def _reset() -> None:
    """Solo tests: borra el cache de config para que cada escenario resuelva
    de nuevo (produccion jamas lo necesita)."""
    _estado.clear()


def _config_canal() -> _ConfigCanal | None:
    """Config del canal; None = DESHABILITADO. Cero excepciones hacia arriba
    (docstring del modulo): si el archivo no existe, queda cacheado como
    deshabilitado; si existe pero falla leerlo/parsearlo (p. ej. rotacion
    transitoria), registra warning y NO cachea None para reintentar luego."""
    if "config" in _estado:
        return _estado["config"]
    cfg: _ConfigCanal | None = None
    error_transitorio = False
    try:
        path = directorio_secretos() / TELEGRAM_FILENAME
        data = None
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                error_transitorio = True
                logger.info(
                    "telegram: error leyendo %s (%s); se reintentara en el proximo aviso",
                    TELEGRAM_FILENAME,
                    scrub(str(exc)) or type(exc).__name__,
                )
                data = None
        if isinstance(data, dict):
            token = data.get("bot_token")
            chat = data.get("chat_id")
            if isinstance(token, str) and token and isinstance(chat, str) and chat:
                register_secret(token)
                cfg = _ConfigCanal(bot_token=token, chat_id=chat)
    except Exception as exc:
        error_transitorio = True
        logger.warning("telegram: fallo resolviendo la config del canal: %s", scrub(str(exc)))
        cfg = None
    if cfg is None and not error_transitorio:
        logger.info(
            "canal Telegram deshabilitado (sin %s valido en el secrets dir): "
            "los avisos no salen y eso NO es fallo ni genera nota",
            TELEGRAM_FILENAME,
        )
    if not error_transitorio:
        _estado["config"] = cfg
    return cfg


def canal_activo() -> bool:
    """True si el canal esta configurado (telegram.json valido)."""
    return _config_canal() is not None


def _envia_texto(texto: str, transport: httpx.BaseTransport | None = None) -> bool:
    """POST sendMessage. CUALQUIER fallo (red, status != 200, JSON raro sin
    ok=true) -> warning con scrub + False; el caller decide la NOTA. Canal
    deshabilitado -> True (no es fallo: nada que reportar)."""
    cfg = _config_canal()
    if cfg is None:
        return True
    transporte = transport if transport is not None else _transporte_test
    try:
        with httpx.Client(transport=transporte, timeout=_TIMEOUT) as cliente:
            resp = cliente.post(cfg.url(), json={"chat_id": cfg.chat_id, "text": texto})
        if resp.status_code != 200:
            logger.warning(
                "telegram: sendMessage respondio HTTP %s — el aviso no salio", resp.status_code
            )
            return False
        try:
            cuerpo = resp.json()
        except ValueError:
            logger.warning("telegram: respuesta ilegible (JSON raro) — el aviso no salio")
            return False
        if not isinstance(cuerpo, dict) or cuerpo.get("ok") is not True:
            logger.warning("telegram: respuesta sin ok=true — el aviso no salio")
            return False
        return True
    except Exception as exc:
        logger.warning("telegram: fallo el envio: %s", scrub(str(exc)) or type(exc).__name__)
        return False


def envia_aviso(texto: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    """Aviso generico (JEV ADS 02 S.8). Canal inactivo devuelve False y no
    llama: a diferencia de los demas envios, inactivo no cuenta como entrega.
    Fail-silent igual que el resto: un fallo de red es False y no levanta."""
    if _config_canal() is None:
        return False
    return _envia_texto(texto, transport)


def _enviar_construido(
    motivo_log: str,
    builder,
    *args,
    transport: httpx.BaseTransport | None = None,
    **kwargs,
) -> bool:
    """Patron fail-silent comun a los senders: canal apagado -> True;
    excepcion armando/enviando -> warning con scrub + False; jamas levanta."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(builder(*args, **kwargs), transport=transport)
    except Exception as exc:
        logger.warning("telegram: fallo armando %s: %s", motivo_log, scrub(str(exc)))
        return False


TOPE_CORTES_POR_AVISO = 5


def _texto_corte(fila: CorteEncolado, etiqueta_fn) -> str:
    if fila.kind == "negative" and fila.search_term:
        return f"Negar la búsqueda «{fila.search_term}»"
    if fila.kind == "harvest" and fila.search_term:
        return f"Pasar a exacta la búsqueda «{fila.search_term}»"
    if fila.kind == "pause":
        return "Pausar una keyword o un producto"
    return etiqueta_fn(fila.kind, "kind")


def _seccion_cortes(encabezado: str, filas: Sequence[CorteEncolado], etiqueta_fn) -> list[str]:
    lineas = [encabezado]
    visibles = filas[:TOPE_CORTES_POR_AVISO]
    for fila in visibles:
        lineas.append(f"- {_texto_corte(fila, etiqueta_fn)}")
    resto = len(filas) - len(visibles)
    if resto > 0:
        lineas.append(f"y {resto} más")
    return lineas


def avisos_cortes_del_ciclo(avisos: Sequence[CorteEncolado]) -> list[str]:
    """Un mensaje por plataforma con todos los cortes nuevos del ciclo. Lista vacia sin cortes."""
    if not avisos:
        return []
    from app.etiquetas import etiqueta

    por_plataforma: dict[str, list[CorteEncolado]] = {}
    for fila in avisos:
        por_plataforma.setdefault(fila.platform, []).append(fila)

    mensajes: list[str] = []
    for platform, filas in por_plataforma.items():
        total = len(filas)
        conteo = (
            "1 corte nuevo en este ciclo" if total == 1 else f"{total} cortes nuevos en este ciclo"
        )
        lineas = [f"[Orbit] {etiqueta(platform, 'plataforma')}: {conteo}"]
        en_vivo = [f for f in filas if f.modo == "live"]
        en_practica = [f for f in filas if f.modo != "live"]
        if en_vivo:
            vence = min(
                en_vivo,
                key=lambda f: (
                    f.vence_el
                    if f.vence_el.tzinfo is not None
                    else f.vence_el.replace(tzinfo=dt.UTC)
                ),
            ).vence_el
            lineas.extend(
                _seccion_cortes(
                    f"En vivo: {len(en_vivo)}. Se aplican solos el {formato.fecha_hora(vence)}"
                    " (hora del centro de México) si no los rechazas en Propuestas.",
                    en_vivo,
                    etiqueta,
                )
            )
        if en_practica:
            lineas.extend(
                _seccion_cortes(
                    f"En práctica: {len(en_practica)}. Son de prueba: no se aplicarán.",
                    en_practica,
                    etiqueta,
                )
            )
        mensajes.append("\n".join(lineas))
    return mensajes


def decide_aviso_target(aplicado, ancla) -> tuple[bool, str | None]:
    """Logica UNICA del aviso por cambio (ORBIT 06 2.3 segunda vuelta, A8,
    D-2.3.14): el umbral >= 1 punto corre sobre el ACUMULADO desde el ultimo
    aviso emitido (el paso maximo 0.5 jamas dispara solo). Devuelve (emitir,
    nuevo_ancla). Sin ancla (primera vez) se avisa; sin aplicado
    (abstencion) jamas se avisa ni el ancla avanza. Pura; el ciclo la usa
    para persistir `ultimo_avisado` y el digest para renderizar (cero
    duplicados). Valores ilegibles = no avisar, ancla intacta (regla 3)."""
    if aplicado is None:
        return (False, ancla)
    if ancla is None:
        return (True, str(aplicado))
    try:
        delta = abs(Decimal(str(aplicado)) - Decimal(str(ancla)))
    except Exception:
        return (False, ancla)
    if delta < 1:
        return (False, ancla)
    return (True, str(aplicado))


def _linea_banda_margen(plataforma, target: dict) -> str | None:
    """Linea por derivado fuera de banda (A1, D-2.3.10): UNA por ciclo con
    derivado presente y fuera de [10, 45], con el valor crudo (el aplicado
    ya viene clampeado). Bordes de goals (cero segundas fuentes)."""
    from app.optimizer import goals as g

    plat = plataforma if isinstance(plataforma, str) and plataforma else "?"
    crudo = target.get("target_derivado")
    if crudo is None:
        return None
    try:
        numero = Decimal(str(crudo))
    except Exception:
        return None
    if g.MARGEN_BANDA_MIN <= numero <= g.MARGEN_BANDA_MAX:
        return None
    return (
        f"target margen {plat}: derivado {crudo} fuera de banda "
        f"[{g.MARGEN_BANDA_MIN}, {g.MARGEN_BANDA_MAX}] "
        f"(aplicado {target.get('target_aplicado')})"
    )


def _etiqueta(codigo: str | None, dominio) -> str:
    """El registro de pantalla (app.etiquetas) importa app.cycle, que importa
    este modulo: el import va aqui para no cerrar el ciclo."""
    from app.etiquetas import etiqueta

    return etiqueta(codigo, dominio)


def _linea_target_margen(plataforma, target, ancla) -> str | None:
    """Linea del target de margen para el digest (ORBIT 06 2.3 segunda
    vuelta, D-2.3.7/D-2.3.14, spec §9): `target margen {plat}: {ancla} ->
    {nuevo}` si el aplicado acumula >= 1 punto desde el ultimo aviso;
    `target margen {plat}: abstencion {motivo} ({etiqueta})` si el peldano
    se abstiene. Cualquier ausente (sin bloque, sin aplicado, motivo sin
    etiqueta conocida -> id crudo) = None: la linea no sale (regla 3).
    Pura."""

    if not isinstance(target, dict):
        return None
    plat = plataforma if isinstance(plataforma, str) and plataforma else "?"
    motivo = target.get("motivo_abstencion")
    if motivo:
        texto = _etiqueta(motivo, "motivo_abstencion")
        return f"target margen {plat}: abstencion {motivo} ({texto})"
    emitir, _ = decide_aviso_target(target.get("target_aplicado"), ancla)
    if not emitir:
        return None
    nuevo = target.get("target_aplicado")
    if ancla is None:
        return f"target margen {plat}: {nuevo} (primer aviso)"
    return f"target margen {plat}: {ancla} -> {nuevo}"


def digest_ciclo(resumen: dict) -> str:
    """Digest MINIMO del ciclo ejecutor: cycle_id, plataforma, modo del ciclo
    (live/shadow — en shadow el dueno practica el veto y el digest tambien
    sale; sin el modo en el encabezado un digest de shadow se confunde con
    uno live), status y decisiones, mas lo que EXISTA en notes['apply']
    (regla 3: clave ausente no se menciona, jamas un 0 inventado)."""
    apply = resumen.get("apply")
    apply = apply if isinstance(apply, dict) else {}
    modo = resumen.get("modo")
    lineas = [
        f"[Orbit] digest ciclo #{resumen['cycle_id']}"
        f" {resumen['plataforma']}" + (f" [{modo}]" if modo else "") + f" — {resumen['status']}",
        f"decisiones: {resumen['decisions_count']}",
    ]
    if resumen.get("resume_of") is not None:
        # ARREGLOS 05 A: ejecutor de recuperacion; no decidio, completa un origen.
        lineas.append(f"recupera las decisiones del ciclo #{resumen['resume_of']}")
    if "bids_aplicados" in apply:
        lineas.append(f"bids aplicados: {apply['bids_aplicados']}")
    if apply.get("bids_inciertos"):
        lineas.append(
            f"bids inciertos: {apply['bids_inciertos']} (se resuelven con una lectura; "
            "no se reenvian)"
        )
    if "bids_descartados" in apply:
        lineas.append(f"bids fuera de cap hoy: {apply['bids_descartados']}")
    if "cortes_encolados" in apply:
        cortes = apply["cortes_encolados"]
        lineas.append(
            "cortes encolados: "
            f"live={cortes.get('live')} shadow={cortes.get('shadow')} "
            f"choques={cortes.get('choques')}"
        )
    if "cortes_liberados" in apply:
        liberados = apply["cortes_liberados"]
        lineas.append(
            f"cortes liberados: aplicadas={liberados.get('aplicadas')} "
            f"fallidas={liberados.get('fallidas')}"
        )
    if "apply_error" in apply:
        lineas.append(f"apply_error: {apply['apply_error']}")
    if apply.get("apply_abortado_owner"):
        lineas.append("apply_abortado_owner: true")
    # BIDS 01 1.3: hojas sin trafico saltadas por la guarda entidad_inerte.
    # Regla 3: clave ausente = no se menciona (jamas un 0 inventado).
    skips = resumen.get("skips")
    if isinstance(skips, dict):
        skips_entidad = skips.get("entidad")
        if isinstance(skips_entidad, dict) and "entidad_inerte" in skips_entidad:
            lineas.append(f"entidades sin trafico (saltadas): {skips_entidad['entidad_inerte']}")
    # ORBIT 06 2.3 segunda vuelta (D-2.3.7/D-2.3.10/D-2.3.14, spec §9):
    # linea por cambio ACUMULADO >= 1 punto desde el ultimo aviso (o
    # abstencion con motivo) + linea por derivado fuera de banda (valor
    # crudo). Ausentes = no se menciona (regla 3).
    bloque_target = resumen.get("target")
    if isinstance(bloque_target, dict):
        linea_target = _linea_target_margen(
            resumen.get("plataforma"), bloque_target, resumen.get("target_ancla")
        )
        if linea_target:
            lineas.append(linea_target)
        linea_banda = _linea_banda_margen(resumen.get("plataforma"), bloque_target)
        if linea_banda:
            lineas.append(linea_banda)
    contrib = resumen.get("contribucion")
    if isinstance(contrib, notifica_contribucion.ContribucionDigest):
        linea = notifica_contribucion.linea_contribucion(contrib)
        if linea:
            lineas.append(linea)
        if contrib.residual_tacos is not None:
            signo = "-" if contrib.residual_tacos.monto < 0 else ""
            if contrib.rango is not None:
                moneda_res = contrib.rango.moneda
            else:
                moneda_res = "USD" if resumen.get("plataforma") == "amazon_us" else "MXN"
            lineas.append(
                f"residual tacos campaign: {signo}"
                f"{notifica_contribucion.formatea_monto(abs(contrib.residual_tacos.monto))} "
                f"{moneda_res}"
            )
    return "\n".join(lineas)


def aviso_reputacion(alertas: list) -> str:
    """Aviso de las alertas de reputacion de Mercado Libre que aun no salieron
    (ARREGLOS 03 D-K; lo manda la revision de las 10:30, en lotes). Una linea
    por alerta: "Urgente:" las criticas, "Aviso:" las demas, con la
    publicacion si el mensaje no la nombra. Los mensajes guardados solo traen
    ids y numeros (A.5): seguros sin parse_mode."""
    lineas = ["[Orbit] Reputación de Mercado Libre"]
    for alerta in alertas:
        prefijo = "Urgente" if alerta.get("severidad") == "critica" else "Aviso"
        mensaje = str(alerta.get("mensaje"))
        publicacion = alerta.get("external_id")
        if publicacion and publicacion not in mensaje:
            mensaje = f"{mensaje} ({publicacion})"
        lineas.append(f"{prefijo}: {mensaje}")
    return "\n".join(lineas)


def alerta_harvest_failed(alerta: AlertaHarvest) -> str:
    """Alerta de fallo definitivo de harvest (sellado 13). `alerta` es la
    AlertaHarvest de app.apply_harvest por duck typing (no se importa la
    clase: apply_harvest importa este modulo para enviarla)."""
    return "\n".join(
        [
            "[Orbit] ALERTA harvest failed",
            f"plataforma: {alerta.plataforma}",
            f"motivo: {alerta.motivo}",
            f"decision: {alerta.decision_id}",
            f"search_term: {alerta.search_term}",
            f"job: {alerta.job_id}",
            f"detalle: {alerta.detalle}",
        ]
    )


def alerta_harvest_hermanas(alerta: AlertaHarvest) -> str:
    """Alerta de harvest APLICADO con hermanas pendientes (F2, A.3): el
    evento de valor quedo confirmado y la higiene no completo. El texto
    jamas dice "failed": seria mentira operativa (la keyword vende). Las
    pendientes viajan en `detalle` (rol: motivo por linea)."""
    return "\n".join(
        [
            "[Orbit] harvest aplicado con hermanas pendientes",
            f"plataforma: {alerta.plataforma}",
            f"motivo: {alerta.motivo}",
            f"decision: {alerta.decision_id}",
            f"search_term: {alerta.search_term}",
            f"job: {alerta.job_id}",
            f"detalle: {alerta.detalle}",
        ]
    )


def aviso_cap_agotado(plataforma: str, kind: str, used: int, cap: int) -> str:
    """Aviso de cap de quota agotado (preflight 1.4): la rampa del dia llego
    a su tope en una forma. Sin FECHA en el texto (decision declarada): ni
    datetime.now() del cliente ni parametro inyectado — el dia es la
    quota_date de la propia fila, visible en /salud con su fuente; una fecha
    aqui seria un segundo reloj (regla 2)."""
    return "\n".join(
        [
            "[Orbit] ALERTA cap agotado",
            f"plataforma: {plataforma}",
            f"kind: {kind}",
            f"used: {used}",
            f"cap: {cap}",
        ]
    )


@dataclass(frozen=True)
class PropuestaCampanaNueva:
    """Lo que el aviso de propuesta nueva necesita (C.4 B1): la fila open
    con su ventana madura. Sin secretos, sin relojes inyectados (las fechas
    son datos de la fila, regla 2)."""

    proposal_id: int
    platform: str
    campaign_external_id: str
    nombre: str | None
    first_seen_at: dt.datetime
    window_start: dt.date
    window_end: dt.date
    cost: Decimal
    revenue: Decimal
    currency: str
    target_pct: Decimal
    target_source: str
    excess: Decimal
    acos_pct: Decimal | None


def aviso_propuesta_campana_nueva(aviso: PropuestaCampanaNueva) -> str:
    """Aviso de UNA propuesta de campana nueva: pausa MANUAL en Amazon
    (C.4 es proposal-only; el mensaje jamas sugiere que Orbit pauso)."""
    lineas = [
        "[Orbit] propuesta de campana — pausar a mano en Amazon",
        f"plataforma: {aviso.platform}",
        f"campana: {aviso.campaign_external_id}",
    ]
    if aviso.nombre:  # regla 3: nombre ausente no se menciona
        lineas.append(f"nombre: {aviso.nombre}")
    fmt = notifica_contribucion.formatea_monto
    lineas.extend(
        [
            f"costo: {fmt(aviso.cost)} {aviso.currency}",
            f"ingreso: {fmt(aviso.revenue)} {aviso.currency}",
            f"target: {fmt(aviso.target_pct)}% ({aviso.target_source})",
            f"exceso: {fmt(aviso.excess)} {aviso.currency}",
        ]
    )
    if aviso.acos_pct is not None:  # regla 3: revenue 0 no tiene ACoS
        lineas.append(f"acos: {fmt(aviso.acos_pct)}%")
    lineas.append(f"ventana: {aviso.window_start.isoformat()}..{aviso.window_end.isoformat()}")
    lineas.append(f"propuesta: {aviso.proposal_id}")
    return "\n".join(lineas)


def notifica_propuesta_campana(
    aviso: PropuestaCampanaNueva, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de UNA propuesta de campana nueva (C.4 B1)."""
    return _enviar_construido(
        "el aviso de propuesta", aviso_propuesta_campana_nueva, aviso, transport=transport
    )


def notifica_temporada(texto: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    """Aviso de temporada del dia (TEMPORADA 01 F). Canal apagado -> True."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(texto, transport=transport)
    except Exception as exc:
        logger.warning("telegram: fallo armando el aviso de temporada: %s", scrub(str(exc)))
        return False


def notifica_cortes_encolados(
    avisos: Sequence[CorteEncolado], *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Canal apagado o sin cortes -> True. False si algun mensaje no salio."""
    try:
        if not avisos or not canal_activo():
            return True
        ok = True
        for texto in avisos_cortes_del_ciclo(avisos):
            if not _envia_texto(texto, transport=transport):
                ok = False
        return ok
    except Exception as exc:
        logger.warning("telegram: fallo armando el aviso de encola: %s", scrub(str(exc)))
        return False


def notifica_digest(
    resumen: dict, *, transport: httpx.BaseTransport | None = None, con_contribucion: bool = True
) -> bool:
    """Digest del ciclo ejecutor al final del ciclo. False = fallo del canal.

    `con_contribucion` False omite la linea de contribucion del dia: la usa el
    digest de un ejecutor de recuperacion, que no repite el contexto que ya
    mando el ciclo que decidio."""
    try:
        if not canal_activo():
            return True
        plataforma = resumen.get("plataforma")
        payload = resumen
        if con_contribucion and isinstance(plataforma, str):
            contrib = notifica_contribucion.carga_contribucion_digest(plataforma)
            if contrib is not None:
                payload = {**resumen, "contribucion": contrib}
        return _envia_texto(digest_ciclo(payload), transport=transport)
    except Exception as exc:
        logger.warning("telegram: fallo armando el digest: %s", scrub(str(exc)))
        return False


def notifica_harvest_failed(
    alerta: AlertaHarvest, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Alerta de harvest failed (sellado 13)."""
    return _enviar_construido(
        "la alerta de harvest", alerta_harvest_failed, alerta, transport=transport
    )


def notifica_harvest_hermanas(
    alerta: AlertaHarvest, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de harvest aplicado con hermanas pendientes (F2, A.3)."""
    return _enviar_construido(
        "el aviso de hermanas", alerta_harvest_hermanas, alerta, transport=transport
    )


def notifica_cap_agotado(
    plataforma: str, kind: str, used: int, cap: int, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de cap agotado (preflight 1.4)."""
    return _enviar_construido(
        "el aviso de cap agotado",
        aviso_cap_agotado,
        plataforma,
        kind,
        used,
        cap,
        transport=transport,
    )


def alerta_biblioteca_no_escrita(
    *,
    aplicado: str,
    plataforma: str,
    grupo_id: int | None,
    decision_id: int | None,
    job_id: int | None,
    texto: str | None,
    motivo: str,
    detalle: str,
) -> str:
    """Texto veraz de biblioteca no aprendida (F2, A.4): el harvest/negative
    SI quedo aplicado y solo la biblioteca no aprendio el termino. Jamas
    dice "failed" (mismo criterio que `alerta_harvest_hermanas`: seria
    mentira operativa — la keyword vende / el corte aplico). Sin acentos
    (estilo de este modulo) y sin secretos (`detalle` es la clase de la
    excepcion y se re-scrubbea por si acaso; `job_id` solo existe en el
    camino harvest)."""
    lineas = [
        f"[Orbit] biblioteca no aprendio el termino ({aplicado} aplicado)",
        f"plataforma: {plataforma}",
        f"aplicado: {aplicado}",
        f"grupo: {grupo_id}",
        f"decision: {decision_id}",
    ]
    if job_id is not None:
        lineas.append(f"job: {job_id}")
    lineas.extend(
        [
            f"search_term: {texto}",
            f"motivo: {motivo}",
            f"detalle: {scrub(detalle)}",
        ]
    )
    return "\n".join(lineas)


def notifica_biblioteca_no_escrita(
    *,
    aplicado: str,
    plataforma: str,
    grupo_id: int | None,
    decision_id: int | None,
    job_id: int | None,
    texto: str | None,
    motivo: str,
    detalle: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """Aviso de biblioteca no escrita (F2, A.4)."""
    return _enviar_construido(
        "el aviso de biblioteca",
        alerta_biblioteca_no_escrita,
        aplicado=aplicado,
        plataforma=plataforma,
        grupo_id=grupo_id,
        decision_id=decision_id,
        job_id=job_id,
        texto=texto,
        motivo=motivo,
        detalle=detalle,
        transport=transport,
    )


@dataclass(frozen=True)
class SaltoDestinoGrupo:
    """Salto del resolutor en una campana DE GRUPO (cycle.py lo construye
    desde `_SQL_MEMBRESIA_GRUPO`; el ciclo lo avisa una vez por racha y lo
    persiste en notes.harvest_destino.saltos_grupo)."""

    platform: str
    grupo_id: int
    campaign_ad_entity_id: int
    campaign_external: str | None
    nombre: str | None
    rol: str | None
    motivo: str  # destino_inconsistente | sin_destino_de_harvest


def aviso_destino_grupo(salto: SaltoDestinoGrupo) -> str:
    """Builder PURO del aviso de harvest de grupo sin destino: encabezado,
    plataforma, grupo, campana (nombre o external) con rol, motivo, linea
    de accion por motivo y cierre veraz. Sin acentos, sin fecha (patron
    aviso_cap_agotado: la fecha es la del ciclo, visible en /salud), sin
    secretos y sin la palabra "failed". Motivo desconocido: sin linea de
    accion, sin reventar."""
    from app.optimizer import hygiene

    lineas = [
        "[Orbit] ALERTA harvest de grupo sin destino",
        f"plataforma: {salto.platform}",
        f"grupo: {salto.grupo_id}",
        f"campana: {salto.nombre or salto.campaign_external}"
        f" (#{salto.campaign_ad_entity_id}, rol {salto.rol})",
        f"motivo: {salto.motivo}",
    ]
    if salto.motivo == hygiene.MOTIVO_DESTINO_INCONSISTENTE:
        lineas.append(
            "la terna del goal contradice la exacta del grupo: revisar con "
            f"tools/harvest_excepcion.py --limpiar-terna --grupo {salto.grupo_id} "
            "(dry-run primero)"
        )
    elif salto.motivo == hygiene.MOTIVO_SIN_DESTINO_HARVEST:
        lineas.append(
            "el grupo no resuelve su exacta (rol category_exact ausente o de otra "
            "plataforma): revisar campana_grupo_rol"
        )
    lineas.append("El harvest de esta campana queda saltado hasta corregirlo.")
    return "\n".join(lineas)


def notifica_destino_grupo(
    salto: SaltoDestinoGrupo, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de harvest de grupo sin destino (F2, A.6): lo manda el ciclo
    una vez por racha por campana. Mismo contrato fail-silent que
    `notifica_biblioteca_no_escrita`: canal apagado -> True; cualquier
    excepcion -> warning con scrub + False; jamas levanta."""
    return _enviar_construido(
        "el aviso de destino",
        aviso_destino_grupo,
        salto,
        transport=transport,
    )
