"""Canal de avisos Telegram (ORBIT 04, task 3.3; sellados 2 y 19; APPLY.md 10.2).

FAIL-SILENT por diseno: un fallo del canal JAMAS tumba el ciclo que notifica
— deja WARNING en el log y la NOTA ``notes['telegram']`` del ciclo ejecutor
(visible en Salud), que es la UNICA visibilidad del fallo: el silencio del
canal no es invisible (sellado 2, decision 19). Canal DESHABILITADO (sin
secrets) NO es fallo: los ``notifica_*`` devuelven True y NO generan NOTA.

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
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.ads.config import DEFAULT_SECRETS_DIR
from app.db import connect
from app.redaction import install_scrub_filter, register_secret, scrub

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

ETIQUETA_CONTRIBUCION = "contribucion pre-cargos · no decisoria"

SQL_CONTRIB_RANGO = """
SELECT metric_currency::text,
       count(*)::int,
       sum(contrib_sin_halo),
       sum(contrib_con_halo),
       bool_or(rango_invertido) AS rango_invertido,
       bool_or(precio_min_multilisting) AS precio_min_multilisting
  FROM v_contribucion_entidad
 WHERE platform = %s::platform
 GROUP BY metric_currency
"""

_CONTRIB_CONNECT_TIMEOUT = 5
_CONTRIB_STATEMENT_TIMEOUT_MS = 10_000

SQL_CONTRIB_AUSENTES = """
SELECT motivo, count(*)::int AS n
  FROM v_contribucion_cobertura
 WHERE platform = %s::platform
 GROUP BY motivo
 ORDER BY n DESC, motivo
"""

SQL_RESIDUAL_TACOS = """
SELECT gasto_campaign_sin_contraparte
  FROM v_tacos
 WHERE platform = %s::platform
   AND mes = date_trunc(
           'month',
           ((now() AT TIME ZONE 'UTC')::date - 15)
       )::date
"""


@dataclass(frozen=True)
class RangoContribucion:
    moneda: str
    entidades: int
    sin_halo: Decimal
    con_halo: Decimal
    invertido: bool = False
    entidades_maduras: int | None = None
    # 0008: alguna entidad publicada uso el precio MENOR de un producto US
    # multilisting (enmienda D1.bis) — la linea del digest lo declara.
    precio_min_multilisting: bool = False


@dataclass(frozen=True)
class SinDatoContribucion:
    total_ausentes: int
    por_motivo: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ResidualTacos:
    monto: Decimal


@dataclass(frozen=True)
class ContribucionDigest:
    rango: RangoContribucion | None
    sin_dato: SinDatoContribucion | None
    residual_tacos: ResidualTacos | None
    lectura_fallida: bool = False


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
    (docstring del modulo): cualquier problema de lectura/parseo deja el
    canal deshabilitado, que no es fallo."""
    if "config" in _estado:
        return _estado["config"]
    cfg: _ConfigCanal | None = None
    try:
        path = Path(os.environ.get("ORBIT_SECRETS_DIR", DEFAULT_SECRETS_DIR)) / TELEGRAM_FILENAME
        data = None
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
        if isinstance(data, dict):
            token = data.get("bot_token")
            chat = data.get("chat_id")
            if isinstance(token, str) and token and isinstance(chat, str) and chat:
                register_secret(token)
                cfg = _ConfigCanal(bot_token=token, chat_id=chat)
    except Exception as exc:  # noqa: BLE001 - jamas hacia arriba
        logger.warning("telegram: fallo resolviendo la config del canal: %s", scrub(str(exc)))
        cfg = None
    if cfg is None:
        logger.info(
            "canal Telegram deshabilitado (sin %s valido en el secrets dir): "
            "los avisos no salen y eso NO es fallo ni genera nota",
            TELEGRAM_FILENAME,
        )
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
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo el envio: %s", scrub(str(exc)) or type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Builders puros (sin red): texto plano, sin secretos
# ---------------------------------------------------------------------------


def aviso_corte_encolado(fila: CorteEncolado) -> str:
    """Aviso de UN corte nuevo en la cola de veto (sellado 2: al ENCOLAR, con
    vencimiento — el reloj de la ventana 48h NO se detiene)."""
    familia = FAMILIA_DE_KIND.get(fila.kind, "desconocida")
    lineas = [
        "[Orbit] corte encolado — ventana de veto 48h",
        f"plataforma: {fila.platform}",
        f"kind: {fila.kind} (familia {familia})",
    ]
    if fila.search_term is not None:  # regla 3: entity_cut no tiene termino
        lineas.append(f"search_term: {fila.search_term}")
    lineas.append(f"modo: {fila.modo}")
    lineas.append(f"vence_el: {fila.vence_el.isoformat()}")
    return "\n".join(lineas)


def _formatea_monto(valor: Decimal) -> str:
    return format(valor.quantize(Decimal("0.01")), "f")


def _linea_contribucion(datos: ContribucionDigest) -> str | None:
    if datos.lectura_fallida:
        return f"{ETIQUETA_CONTRIBUCION}: lectura no disponible"
    if datos.rango is not None:
        r = datos.rango
        sufijo = ""
        if r.entidades_maduras is not None and r.entidades_maduras > r.entidades:
            sufijo = f" de {r.entidades_maduras} entidades maduras"
        if r.precio_min_multilisting:
            sufijo += " · precio min multilisting"
        if r.invertido:
            cuerpo = (
                f"totales sin_halo={_formatea_monto(r.sin_halo)},"
                f" con_halo={_formatea_monto(r.con_halo)} {r.moneda}"
                f" ({r.entidades} entidades{sufijo}; rango_invertido en alguna)"
            )
        else:
            cuerpo = (
                f"{_formatea_monto(r.sin_halo)} .. {_formatea_monto(r.con_halo)}"
                f" {r.moneda} ({r.entidades} entidades{sufijo})"
            )
    elif datos.sin_dato is not None:
        s = datos.sin_dato
        motivos = ", ".join(f"{m} {n}" for m, n in s.por_motivo)
        cuerpo = f"sin dato ({s.total_ausentes} entidades ausentes: {motivos})"
    else:
        return None
    return f"{ETIQUETA_CONTRIBUCION}: {cuerpo}"


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
    except Exception:  # noqa: BLE001 - ilegible: sin linea, ancla intacta
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
    except Exception:  # noqa: BLE001 - ilegible: sin linea
        return None
    if g.MARGEN_BANDA_MIN <= numero <= g.MARGEN_BANDA_MAX:
        return None
    return (
        f"target margen {plat}: derivado {crudo} fuera de banda "
        f"[{g.MARGEN_BANDA_MIN}, {g.MARGEN_BANDA_MAX}] "
        f"(aplicado {target.get('target_aplicado')})"
    )


def _linea_target_margen(plataforma, target, ancla) -> str | None:
    """Linea del target de margen para el digest (ORBIT 06 2.3 segunda
    vuelta, D-2.3.7/D-2.3.14, spec §9): `target margen {plat}: {ancla} ->
    {nuevo}` si el aplicado acumula >= 1 punto desde el ultimo aviso;
    `target margen {plat}: abstencion {motivo} ({etiqueta})` si el peldano
    se abstiene. Cualquier ausente (sin bloque, sin aplicado, motivo sin
    etiqueta conocida -> id crudo) = None: la linea no sale (regla 3).
    Pura."""
    from app.optimizer import goals as g

    if not isinstance(target, dict):
        return None
    plat = plataforma if isinstance(plataforma, str) and plataforma else "?"
    motivo = target.get("motivo_abstencion")
    if motivo:
        etiqueta = g.ETIQUETA_ABSTENCION.get(motivo, motivo)
        return f"target margen {plat}: abstencion {motivo} ({etiqueta})"
    emitir, _ = decide_aviso_target(target.get("target_aplicado"), ancla)
    if not emitir:
        return None
    nuevo = target.get("target_aplicado")
    if ancla is None:
        return f"target margen {plat}: {nuevo} (primer aviso)"
    return f"target margen {plat}: {ancla} -> {nuevo}"


def _arma_contribucion_digest(
    filas_rango: list[tuple],
    filas_ausentes: list[tuple],
    residual: Decimal | None,
) -> ContribucionDigest | None:
    rango: RangoContribucion | None = None
    sin_dato: SinDatoContribucion | None = None
    por_motivo = tuple((m, n) for m, n in filas_ausentes)
    total_ausentes = sum(n for _, n in por_motivo)
    if len(filas_rango) == 1:
        moneda, entidades, sin_h, con_h, invertido, multilisting = filas_rango[0]
        if entidades and sin_h is not None and con_h is not None:
            maduras = entidades + total_ausentes if total_ausentes else None
            rango = RangoContribucion(
                moneda,
                entidades,
                sin_h,
                con_h,
                bool(invertido),
                maduras,
                bool(multilisting),
            )
    elif len(filas_rango) > 1 and total_ausentes > 0:
        sin_dato = SinDatoContribucion(total_ausentes, por_motivo)
    if rango is None and sin_dato is None and total_ausentes > 0:
        sin_dato = SinDatoContribucion(total_ausentes, por_motivo)
    if rango is None and sin_dato is None:
        return None
    res_tacos = ResidualTacos(residual) if residual is not None and residual != 0 else None
    return ContribucionDigest(rango=rango, sin_dato=sin_dato, residual_tacos=res_tacos)


def _contrib_conn(dsn: str):
    conn = connect(dsn, connect_timeout=_CONTRIB_CONNECT_TIMEOUT)
    conn.execute(f"SET statement_timeout = {_CONTRIB_STATEMENT_TIMEOUT_MS}")
    return conn


def carga_contribucion_digest(plataforma: str, *, conn=None) -> ContribucionDigest | None:
    """Lee v_contribucion_entidad / cobertura / v_tacos (ORBIT_DSN_READ).

    Fail-silent hacia arriba: el caller omite la seccion si devuelve None.
    Lectura fallida devuelve ContribucionDigest(lectura_fallida=True) para
    distinguirla de 'sin dato'. Solo lectura; sin ORBIT_DSN_READ -> None."""
    propia = conn is None
    try:
        if conn is None:
            dsn = os.environ.get("ORBIT_DSN_READ")
            if not dsn:
                return None
            conn = _contrib_conn(dsn)
        filas_rango = conn.execute(SQL_CONTRIB_RANGO, (plataforma,)).fetchall()
        filas_ausentes = conn.execute(SQL_CONTRIB_AUSENTES, (plataforma,)).fetchall()
        residual_row = conn.execute(SQL_RESIDUAL_TACOS, (plataforma,)).fetchone()
        residual = residual_row[0] if residual_row else None
        return _arma_contribucion_digest(filas_rango, filas_ausentes, residual)
    except Exception as exc:  # noqa: BLE001 - fail-silent (digest sigue sin contrib)
        logger.warning("telegram: fallo leyendo contribucion para digest: %s", scrub(str(exc)))
        return ContribucionDigest(
            rango=None, sin_dato=None, residual_tacos=None, lectura_fallida=True
        )
    finally:
        if propia and conn is not None:
            conn.close()


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
    if "bids_aplicados" in apply:
        lineas.append(f"bids aplicados: {apply['bids_aplicados']}")
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
    if isinstance(contrib, ContribucionDigest):
        linea = _linea_contribucion(contrib)
        if linea:
            lineas.append(linea)
        if contrib.residual_tacos is not None:
            signo = "-" if contrib.residual_tacos.monto < 0 else ""
            lineas.append(
                f"residual tacos campaign: {signo}"
                f"{_formatea_monto(abs(contrib.residual_tacos.monto))} MXN"
            )
    # REPUTACION 01 A.5: bloque aditivo (ausente o vacio = cero lineas,
    # regla 3; el formato existente no cambia).
    bloque_rep = resumen.get("reputacion")
    if isinstance(bloque_rep, list):
        lineas.extend(_lineas_reputacion(bloque_rep))
    return "\n".join(lineas)


_TOPE_LINEAS_REPUTACION = 10


def _lineas_reputacion(alertas: list) -> list[str]:
    """Una linea por alerta + resto contado (D-A5-1: tope 10, no inunda
    el digest). Los mensajes ya vienen sin texto externo (A.5)."""
    lineas = []
    for alerta in alertas[:_TOPE_LINEAS_REPUTACION]:
        entidad = alerta.get("external_id") or "cuenta"
        lineas.append(
            f"reputacion [{alerta.get('severidad')}] {alerta.get('tipo')}"
            f" {entidad}: {alerta.get('mensaje')}"
        )
    resto = len(alertas) - _TOPE_LINEAS_REPUTACION
    if resto > 0:
        lineas.append(f"reputacion: y {resto} mas")
    return lineas


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


# ---------------------------------------------------------------------------
# Senders de alto nivel: devuelven bool, JAMAS levantan excepciones
# ---------------------------------------------------------------------------


def notifica_encola(fila: CorteEncolado, *, transport: httpx.BaseTransport | None = None) -> bool:
    """Aviso de UN corte nuevo encolado. False = fallo del canal (el caller
    deja la NOTA); canal deshabilitado -> True."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(aviso_corte_encolado(fila), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de encola: %s", scrub(str(exc)))
        return False


def notifica_digest(resumen: dict, *, transport: httpx.BaseTransport | None = None) -> bool:
    """Digest del ciclo ejecutor al final del ciclo. False = fallo del canal."""
    try:
        if not canal_activo():
            return True
        plataforma = resumen.get("plataforma")
        payload = resumen
        if isinstance(plataforma, str):
            contrib = carga_contribucion_digest(plataforma)
            if contrib is not None:
                payload = {**resumen, "contribucion": contrib}
        # REPUTACION 01 A.5: novedades del dia, fail-silent (sin DSN o
        # fallo = bloque ausente = cero lineas, patron contrib).
        if "reputacion" not in payload:
            from app.reputacion_alertas import carga_reputacion_digest

            novedades = carga_reputacion_digest()
            if novedades:
                payload = {**payload, "reputacion": novedades}
        return _envia_texto(digest_ciclo(payload), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el digest: %s", scrub(str(exc)))
        return False


def notifica_harvest_failed(
    alerta: AlertaHarvest, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Alerta de harvest failed (sellado 13): sale en el punto de fallo
    definitivo, junto a la reversa automatica. False = fallo del canal (la
    bandera envio_fallido viaja con la alerta hasta el ciclo)."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(alerta_harvest_failed(alerta), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando la alerta de harvest: %s", scrub(str(exc)))
        return False


def notifica_harvest_hermanas(
    alerta: AlertaHarvest, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de harvest aplicado con hermanas pendientes (F2, A.3): sale al
    cerrar `done` con pendientes declaradas. Mismo contrato fail-silent de
    los otros senders: canal deshabilitado -> True (no es fallo); cualquier
    excepcion -> warning con scrub + False; JAMAS levanta."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(alerta_harvest_hermanas(alerta), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de hermanas: %s", scrub(str(exc)))
        return False


def aviso_spapi_fallo(fuente: str, platform: str, motivo: str) -> str:
    """Builder PURO del aviso de fallo SP-API (A.5): fuente, plataforma y
    motivo ya redactado en el sello (doble scrub por si acaso). Sin fecha
    (patron aviso_cap_agotado: la fecha es la del run, visible en /salud).
    Con motivo LWA, matiz explicito de que el ciclo de Ads sigue."""
    lineas = [
        "[Orbit] ALERTA fallo SP-API",
        f"fuente: {fuente}",
        f"plataforma: {platform}",
        f"motivo: {scrub(motivo)}",
    ]
    if motivo.startswith("lwa_fallido"):
        lineas.append("El ciclo de Ads no se afecta (procesos y credenciales distintos).")
    return "\n".join(lineas)


def notifica_spapi_fallo(
    fuente: str, platform: str, motivo: str, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de fallo SP-API en flanco (A.5): sale UNA vez por racha, en el
    primer 429/LWA o al abrirse la segunda fallida seguida. Mismo contrato
    fail-silent de los otros senders: canal deshabilitado -> True (no es
    fallo); cualquier excepcion -> warning con scrub + False; JAMAS levanta.
    """
    try:
        if not canal_activo():
            return True
        return _envia_texto(aviso_spapi_fallo(fuente, platform, motivo), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso SP-API: %s", scrub(str(exc)))
        return False


# Total de pares (fuente, plataforma) del cron SP-API: 4 fuentes x 2
# plataformas del wrapper spapi-diario.sh (app/spapi/vigilante.py). Vive
# aqui como literal porque notifica.py no puede importar de spapi (salud
# ya importa notifica: seria un ciclo); el test de igualdad exacta lo
# pinza y vigilante.py garantiza los 8 pares.
_TOTAL_PARES_SPAPI = 8


def aviso_spapi_silencio(faltantes, desde, hasta) -> str:
    """Builder PURO del aviso de silencio SP-API (vigilante): una linea
    por par faltante en el orden del caller (el vigilante pasa el orden
    fuente→plataforma del catalogo: FUENTES_SPAPI x PLATAFORMAS_SPAPI).
    `desde`/`hasta` aware (el vigilante los trae de `_fecha_utc`); se
    normalizan a UTC para que la etiqueta no mienta con otro offset.
    Sin secretos."""
    desde_utc = desde.astimezone(dt.UTC)
    hasta_utc = hasta.astimezone(dt.UTC)
    ventana = f"{desde_utc:%Y-%m-%d %H:%M} UTC → {hasta_utc:%Y-%m-%d %H:%M} UTC"
    lineas = [
        "[Orbit] ALERTA SP-API sin corrida",
        f"ventana: {ventana}",
        f"faltan {len(faltantes)} de {_TOTAL_PARES_SPAPI}:",
    ]
    lineas.extend(f"- {fuente} / {plataforma}" for fuente, plataforma in faltantes)
    lineas.append(
        "El cron de las 05:00 UTC no dejó estas corridas en ingest_run. "
        "Revisar crontab de gon, flock y el log spapi-diario.log."
    )
    return "\n".join(lineas)


def aviso_spapi_vigilante_ciego(motivo: str) -> str:
    """Builder PURO del aviso ciego: el vigilante no pudo leer
    `ingest_run` (DB caida, DSN roto) y no sabe si el cron corrio. El
    motivo viaja con scrub (el DSN roto puede traer password)."""
    return "\n".join(
        [
            "[Orbit] ALERTA vigilante SP-API sin lectura",
            f"no pude leer ingest_run: {scrub(motivo)}",
            "No sé si el cron corrió. Revisar Postgres y el DSN de lectura.",
        ]
    )


def notifica_spapi_silencio(
    faltantes, desde, hasta, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de silencio SP-API (vigilante): sale cuando faltan corridas
    en la ventana. Mismo contrato fail-silent de notifica_spapi_fallo:
    canal deshabilitado -> True (no es fallo); cualquier excepcion ->
    warning con scrub + False; JAMAS levanta.
    """
    try:
        if not canal_activo():
            return True
        return _envia_texto(aviso_spapi_silencio(faltantes, desde, hasta), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso SP-API: %s", scrub(str(exc)))
        return False


def notifica_cap_agotado(
    plataforma: str, kind: str, used: int, cap: int, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de cap agotado (preflight 1.4): lo manda el ciclo por CADA evento
    de transicion (UNA vez por (motor, dia), D3a). Mismo contrato fail-silent
    de los otros senders: canal deshabilitado -> True (no es fallo);
    cualquier excepcion -> warning con scrub + False; JAMAS levanta."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(aviso_cap_agotado(plataforma, kind, used, cap), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de cap agotado: %s", scrub(str(exc)))
        return False


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
    """Aviso de biblioteca no escrita (F2, A.4): sale en el punto del fallo,
    con el sello ya aplicado. Mismo contrato fail-silent que
    `notifica_harvest_hermanas`: canal apagado -> True; excepcion ->
    warning con scrub + False; jamas levanta. La visibilidad de respaldo
    si el canal falla es el rastro durable (`external_ids["biblioteca"]`
    en el harvest; log con scrub en el negative)."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(
            alerta_biblioteca_no_escrita(
                aplicado=aplicado,
                plataforma=plataforma,
                grupo_id=grupo_id,
                decision_id=decision_id,
                job_id=job_id,
                texto=texto,
                motivo=motivo,
                detalle=detalle,
            ),
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de biblioteca: %s", scrub(str(exc)))
        return False


# ---------------------------------------------------------------------------
# FABRICA 02 (A.6): aviso de harvest de grupo sin destino (en flanco)
# ---------------------------------------------------------------------------


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
    excepcion -> warning con scrub + False; jamas levanta. El aviso NO
    reintenta en el ciclo siguiente si el envio fallo: la NOTA en
    notes.telegram y la lista saltos_grupo en /salud son la visibilidad
    de respaldo (mismo criterio que cap_agotado)."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(aviso_destino_grupo(salto), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de destino: %s", scrub(str(exc)))
        return False


# ---------------------------------------------------------------------------
# REPRICING 01 (A.6): avisos del motor de precios, un sender en flanco
# ---------------------------------------------------------------------------
#
# Un solo sender `notifica_precio(tipo, payload)` en flanco por racha (S7):
# por (plataforma, motivo) con conteo y hasta 5 SKUs para `no_evaluado`
# (solo cuando el grupo lleva `precio_aviso_dias_sin_evaluar` dias seguidos),
# `goal_inalcanzable` y `frenado`; por producto para `no_confirmado` y
# `buy_box_perdida`; por plataforma para `huerfana_sin_patch`. Mismo contrato
# fail-silent de los otros senders: canal apagado -> True; cualquier
# excepcion -> warning con scrub + False; jamas levanta.
#
# El texto lleva solo SKU, plataforma, precios, estado y motivo en
# palabras: nunca costo, margen, goal ni cuerpos de error. Por eso el motivo
# viaja traducido por `MOTIVO_PRECIO_ES` y el estado por `ESTADO_PRECIO_ES`
# (los ids crudos `precio_no_cubre_costo`, `sobre_goal_sin_perdida` y
# `goal_inalcanzable` contienen esas palabras); el ASIN solo sale en
# plataformas Amazon (fuera de Amazon el id externo no es un ASIN);
# el motivo desconocido cae al id crudo (la evidencia jamas se pierde).
# Los builders son puros (sin red, sin secretos) y no reciben ningun campo
# de costo/margen/goal/error: la ausencia es estructural, no un filtro.
# El flanco lo decide `avisar_precio` (gancho `avisar` de la corrida A.5)
# leyendo hoy y dias previos de `precio_decision`/`precio_cambio`.

TIPOS_PRECIO = (
    "no_evaluado",
    "goal_inalcanzable",
    "frenado",
    "no_confirmado",
    "buy_box_perdida",
    "huerfana_sin_patch",
)

TIPOS_PRECIO_GRUPO = ("no_evaluado", "goal_inalcanzable", "frenado")
TIPOS_PRECIO_PRODUCTO = ("no_confirmado", "buy_box_perdida")

# Tope de SKUs por aviso de grupo (S7: conteo + hasta 5 SKUs).
TOPE_SKUS_PRECIO = 5

# Umbral de dias seguidos para avisar `no_evaluado` (spec l.69, AC15):
# entero 1-14, ValueError que nombra la clave (replica de
# `app/precio/corrida.py::_numero_entero`, que no se puede importar).
CLAVE_PRECIO_AVISO_DIAS = "precio_aviso_dias_sin_evaluar"

FRASEO_TIPO_PRECIO = {
    "no_evaluado": "no evaluados",
    "goal_inalcanzable": "objetivo inalcanzable",
    "frenado": "frenados",
    "no_confirmado": "no confirmado",
    "buy_box_perdida": "buy box perdida",
    "huerfana_sin_patch": "huerfanas sin parche",
}

MOTIVO_PRECIO_ES = {
    "precio_divergente": "precio observado distinto del publicado",
    "moneda_divergente": "moneda distinta entre observacion y escenario",
    "precio_sin_observar": "sin observacion de precio del dia",
    "precio_ausente": "sin precio publicado",
    "fee_ausente": "sin cotizacion de comision",
    "fee_no_lineal": "comision no lineal",
    "impuesto_fee_pendiente": "impuesto pendiente en la comision",
    "escenario_incoherente": "escenario incoherente",
    "estimacion_motivo_desconocido": "motivo de estimacion desconocido",
    "ingreso_no_positivo": "ingreso no positivo",
    "precio_no_cubre_costo": "precio bajo el piso de rentabilidad",
    "precio_mayor_al_doble": "precio mayor al doble del objetivo",
    "sobre_goal_sin_perdida": "por encima del objetivo sin perdida",
    "ledger_hueco": "ventas sin dato (libro rezagado)",
    "historia_corta": "historia de ventas corta",
    "sin_dato": "sin dato",
    "n15_insuficiente": "ventas de 15 dias insuficientes",
    "u60_bajo_minimo": "unidades de 60 dias bajo el minimo",
    "racha_incompleta": "racha de senal incompleta",
    "ventana_60_excluida": "ventana de 60 dias excluida",
    "dia_sin_stock": "dia sin stock",
    "dia_sin_observacion_inventario": "dia sin observacion de inventario",
    "dia_sin_estado_listing": "dia sin estado del listing",
    "listing_inactivo": "listing inactivo",
    "no_converge": "sin convergencia tras el cambio de direccion",
    "perdiendo_tras_subida": "perdiendo tras la subida",
    "api_error": "falla de la API",
    "no_confirmado": "no confirmado por la observacion",
    "buy_box_perdida": "buy box perdida",
    "cooldown": "enfriamiento entre cambios",
    "cuota": "cupo del dia agotado",
    "en_tolerancia": "dentro de la tolerancia",
    "movimiento_minimo": "movimiento bajo el minimo",
    "sin_decision": "sin decision",
    "sin_listing": "sin listing",
    "catalogo_desactualizado": "catalogo desactualizado",
    "canal_sin_dato": "canal sin dato",
    "desconocido": "desconocido",
}


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


def motivo_precio_es(motivo: str | None) -> str:
    """Motivo de precio en palabras (sin costo/margen/goal). `fee_error:<code>`
    colapsa al codigo corto (el cuerpo del error jamas viaja); motivo
    desconocido -> id crudo (la evidencia no se pierde)."""
    if not motivo:
        return "desconocido"
    if motivo in MOTIVO_PRECIO_ES:
        return MOTIVO_PRECIO_ES[motivo]
    if motivo.startswith("fee_error"):
        codigo = motivo.partition(":")[2].strip()
        return f"falla de comision ({codigo})" if codigo else "falla de comision"
    return motivo


def aviso_precio_grupo(grupo: GrupoPrecio) -> str:
    """Builder PURO del aviso por (plataforma, motivo): conteo + hasta 5
    SKUs + resto contado (200 con el mismo motivo -> UN aviso)."""
    lineas = [
        f"[Orbit] precio: {FRASEO_TIPO_PRECIO[grupo.tipo]}",
        f"plataforma: {grupo.platform}",
        f"motivo: {motivo_precio_es(grupo.motivo)}",
        f"total: {grupo.total}",
    ]
    skus = tuple(grupo.skus)[:TOPE_SKUS_PRECIO]
    if skus:
        lineas.append(f"skus: {', '.join(skus)}")
    resto = grupo.total - len(skus)
    if resto > 0:
        lineas.append(f"y {resto} mas")
    return "\n".join(lineas)


ESTADO_PRECIO_ES = {
    "subir": "subida",
    "bajar": "bajada",
    "mantener": "mantenido",
    "no_evaluado": "no evaluado",
    "goal_inalcanzable": "objetivo inalcanzable",
    "frenado": "frenado",
    "no_confirmado": "no confirmado",
    "buy_box_perdida": "buy box perdida",
}


def estado_precio_es(estado: str | None) -> str:
    """Estado de precio en palabras (B5: jamas el id crudo con «goal»).
    Estado desconocido -> id crudo (la evidencia no se pierde)."""
    if not estado:
        return "desconocido"
    return ESTADO_PRECIO_ES.get(estado, estado)


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
        lineas.append(f"estado: {estado_precio_es(item.estado)}")
    if item.motivo:
        lineas.append(f"motivo: {motivo_precio_es(item.motivo)}")
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


def validar_precio_aviso_dias(settings) -> int:
    """`precio_aviso_dias_sin_evaluar` entero 1-14; `ValueError` nombra la
    clave si falta o sale de cota (ninguna otra clave se lee aqui)."""
    clave = CLAVE_PRECIO_AVISO_DIAS
    if not isinstance(settings, dict) or clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if isinstance(valor, bool):
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}")
    try:
        numero = Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))
    except Exception as exc:
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not numero.is_finite() or numero != numero.to_integral_value():
        raise ValueError(f"setting {clave}: debe ser entero: {valor!r}")
    entero = int(numero)
    if not 1 <= entero <= 14:
        raise ValueError(f"setting {clave}: fuera de cota [1, 14]: {entero}")
    return entero


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


def notifica_precio(tipo: str, payload, *, transport: httpx.BaseTransport | None = None) -> bool:
    """UNICO sender de avisos de precio (S7). `tipo` elige el builder y el
    `payload` debe ser de su familia (grupo / producto / huerfana; los tipos
    por producto tambien aceptan grupo para el resto agrupado de B3); tipo o
    familia desconocidos -> warning + False. Canal deshabilitado -> True (no
    es fallo); cualquier excepcion -> warning con scrub + False; jamas levanta.
    """
    try:
        if tipo in TIPOS_PRECIO_GRUPO:
            esperados: tuple = (GrupoPrecio,)
        elif tipo in TIPOS_PRECIO_PRODUCTO:
            esperados = (ProductoPrecio, GrupoPrecio)
        elif tipo == "huerfana_sin_patch":
            esperados = (HuerfanaPrecio,)
        else:
            logger.warning("telegram: tipo de aviso de precio desconocido: %s", scrub(str(tipo)))
            return False
        if not isinstance(payload, esperados):
            logger.warning(
                "telegram: payload de aviso de precio no es %s para %s",
                "/".join(e.__name__ for e in esperados),
                scrub(str(tipo)),
            )
            return False
        if payload.tipo != tipo:
            logger.warning(
                "telegram: payload de aviso de precio tipo %s no coincide con %s",
                scrub(str(payload.tipo)),
                scrub(str(tipo)),
            )
            return False
        if isinstance(payload, GrupoPrecio):
            texto = aviso_precio_grupo(payload)
        elif isinstance(payload, ProductoPrecio):
            texto = aviso_precio_producto(payload)
        else:
            texto = aviso_precio_huerfana(payload)
        if not canal_activo():
            return True
        return _envia_texto(texto, transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de precio: %s", scrub(str(exc)))
        return False


_SQL_PRECIO_GRUPOS_DIA = """
SELECT d.resultado, d.motivo, COALESCE(l.seller_sku, l.external_id)
  FROM precio_decision d JOIN listing l
    ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s AND d.decision_date = %s
   AND d.resultado IN ('no_evaluado', 'goal_inalcanzable', 'frenado')
 ORDER BY COALESCE(l.seller_sku, l.external_id)
"""

_SQL_PRECIO_DIAS_MOTIVO = """
SELECT DISTINCT decision_date FROM precio_decision
 WHERE platform = %s AND resultado = 'no_evaluado' AND motivo = %s
   AND decision_date BETWEEN %s AND %s
"""

_SQL_PRECIO_BUYBOX_DIA = """
SELECT COALESCE(l.seller_sku, l.external_id), l.seller_sku, l.external_id,
       d.resultado, d.p_actual, d.p_actual_currency
  FROM precio_decision d JOIN listing l
    ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s AND d.decision_date = %s AND d.buy_box_is_own = false
"""

_SQL_PRECIO_FRENADO_NOCONF_DIA = """
SELECT d.listing_id, l.seller_sku, l.external_id
  FROM precio_decision d JOIN listing l
    ON l.id = d.listing_id AND l.platform = d.platform
 WHERE d.platform = %s AND d.decision_date = %s
   AND d.resultado = 'frenado' AND d.motivo = 'no_confirmado'
 ORDER BY d.listing_id
"""

_SQL_PRECIO_ULTIMO_NOCONF = """
SELECT c.precio_antes, c.precio_antes_currency,
       c.precio_despues, c.precio_despues_currency
  FROM precio_cambio c
 WHERE c.listing_id = %s AND c.platform = %s
   AND c.estado = 'no_confirmado' AND c.aplicado AND NOT c.es_reversa
 ORDER BY c.id DESC LIMIT 1
"""

_SQL_PRECIO_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"


def _grupos_precio_dia(conn, platform: str, dia) -> dict:
    grupos: dict = {}
    filas = conn.execute(_SQL_PRECIO_GRUPOS_DIA, (platform, dia)).fetchall()
    for resultado, motivo, sku in filas:
        grupos.setdefault((resultado, motivo or "desconocido"), []).append(sku)
    return grupos


def _avisar_grupos_precio(conn, platform: str, hoy, ayer, umbral) -> int:
    """Un aviso por (resultado, motivo) nuevo (B6: `try` propio). `no_evaluado`
    exige su racha de `umbral` dias (B4: sin umbral valido no sale);
    `goal_inalcanzable`/`frenado` salen si ayer no estaban. Jamas levanta."""
    try:
        grupos_hoy = _grupos_precio_dia(conn, platform, hoy)
        grupos_ayer = _grupos_precio_dia(conn, platform, ayer)
    except Exception as exc:  # noqa: BLE001 - B6: los grupos no callan
        logger.warning("telegram: fallo avisando grupos de precio: %s", scrub(str(exc)))
        return 0
    nuevos = flanco_nuevos(sorted(grupos_hoy), sorted(grupos_ayer))
    enviados = 0
    for (resultado, motivo), skus in sorted(grupos_hoy.items()):
        try:
            if resultado == "no_evaluado":
                if umbral is None:
                    continue
                dias = {
                    r[0]
                    for r in conn.execute(
                        _SQL_PRECIO_DIAS_MOTIVO,
                        (platform, motivo, hoy - dt.timedelta(days=umbral), hoy),
                    ).fetchall()
                }
                if not flanco_umbral(dias, hoy, umbral):
                    continue
            elif (resultado, motivo) not in nuevos:
                continue
            grupo = GrupoPrecio(platform, resultado, motivo, len(skus), tuple(skus))
            if notifica_precio(resultado, grupo):
                enviados += 1
        except Exception as exc:  # noqa: BLE001 - B6: un grupo no calla
            logger.warning(
                "telegram: fallo avisando el grupo de precio %s: %s",
                scrub(f"{resultado}/{motivo}"),
                scrub(str(exc)),
            )
    return enviados


# Tope de avisos por producto, por tipo y corrida (B3: 200 buy box
# perdidas no son 200 mensajes): los primeros salen individuales y el resto
# va en UN aviso agrupado de ese tipo con conteo y hasta 5 SKUs.
TOPE_PRODUCTO_PRECIO = 5


def _enviar_productos_precio_con_tope(tipo: str, motivo: str, piezas: list) -> int:
    """B3: hasta `TOPE_PRODUCTO_PRECIO` avisos por producto; el resto en UN
    aviso agrupado de ese tipo con conteo y hasta `TOPE_SKUS_PRECIO` SKUs
    (`notifica_precio` acepta el grupo para tipos por producto). Un `try`
    por pieza y otro por el agrupado (B6). Jamas levanta."""
    enviados = 0
    for item in piezas[:TOPE_PRODUCTO_PRECIO]:
        try:
            if notifica_precio(tipo, item):
                enviados += 1
        except Exception as exc:  # noqa: BLE001 - B6: un producto no calla
            logger.warning(
                "telegram: fallo avisando producto de precio %s: %s",
                scrub(str(tipo)),
                scrub(str(exc)),
            )
    resto = piezas[TOPE_PRODUCTO_PRECIO:]
    if resto:
        try:
            skus = tuple(s for s in ((p.sku or p.asin) for p in resto[:TOPE_SKUS_PRECIO]) if s)
            grupo = GrupoPrecio(resto[0].platform, tipo, motivo, len(resto), skus)
            if notifica_precio(tipo, grupo):
                enviados += 1
        except Exception as exc:  # noqa: BLE001 - B6: el agrupado no calla
            logger.warning(
                "telegram: fallo avisando resto agrupado de precio %s: %s",
                scrub(str(tipo)),
                scrub(str(exc)),
            )
    return enviados


def _avisar_buybox_precio(conn, platform: str, hoy, ayer) -> int:
    """Un aviso por producto que pierde la buy box hoy y ayer la tenia
    (B6: `try` propio y un `try` por pieza; B3: con tope + resto agrupado).
    Jamas levanta."""
    try:
        bb_hoy = {r[0]: r for r in conn.execute(_SQL_PRECIO_BUYBOX_DIA, (platform, hoy)).fetchall()}
        bb_ayer = {r[0] for r in conn.execute(_SQL_PRECIO_BUYBOX_DIA, (platform, ayer)).fetchall()}
    except Exception as exc:  # noqa: BLE001 - B6: buy box no calla
        logger.warning("telegram: fallo avisando buy box de precio: %s", scrub(str(exc)))
        return 0
    piezas = []
    for clave in flanco_nuevos(sorted(bb_hoy), sorted(bb_ayer)):
        try:
            _id, sku, asin, estado, p_actual, moneda = bb_hoy[clave]
            piezas.append(
                ProductoPrecio(
                    platform,
                    "buy_box_perdida",
                    sku,
                    asin,
                    None,
                    str(p_actual) if p_actual is not None else None,
                    str(moneda) if moneda is not None else None,
                    estado,
                    "buy_box_perdida",
                )
            )
        except Exception as exc:  # noqa: BLE001 - B6: un producto no calla
            logger.warning(
                "telegram: fallo avisando buy box de precio %s: %s",
                scrub(str(clave)),
                scrub(str(exc)),
            )
    return _enviar_productos_precio_con_tope("buy_box_perdida", "buy_box_perdida", piezas)


def _avisar_noconf_precio(conn, platform: str, hoy, ayer) -> int:
    """Un aviso por producto con `frenado(no_confirmado)` hoy que ayer no lo
    estaba (A1); los precios salen del ultimo cambio real no-reversa
    `no_confirmado` (B6: `try` propio; B3: con tope + resto agrupado).
    Jamas levanta."""
    try:
        nc_hoy = {
            r[0]: r
            for r in conn.execute(_SQL_PRECIO_FRENADO_NOCONF_DIA, (platform, hoy)).fetchall()
        }
        nc_ayer = {
            r[0] for r in conn.execute(_SQL_PRECIO_FRENADO_NOCONF_DIA, (platform, ayer)).fetchall()
        }
    except Exception as exc:  # noqa: BLE001 - B6: no confirmado no calla
        logger.warning("telegram: fallo avisando no confirmados de precio: %s", scrub(str(exc)))
        return 0
    piezas = []
    for lid in flanco_nuevos(sorted(nc_hoy), sorted(nc_ayer)):
        try:
            _lid, sku, asin = nc_hoy[lid]
            ult = conn.execute(_SQL_PRECIO_ULTIMO_NOCONF, (lid, platform)).fetchone()
            if ult is None:
                continue
            antes, mon_antes, despues, mon_despues = ult
            moneda = mon_despues or mon_antes
            piezas.append(
                ProductoPrecio(
                    platform,
                    "no_confirmado",
                    sku,
                    asin,
                    str(antes) if antes is not None else None,
                    str(despues) if despues is not None else None,
                    str(moneda) if moneda else None,
                    "no_confirmado",
                    "no_confirmado",
                )
            )
        except Exception as exc:  # noqa: BLE001 - B6: un producto no calla
            logger.warning(
                "telegram: fallo avisando no confirmado de precio %s: %s",
                scrub(str(lid)),
                scrub(str(exc)),
            )
    return _enviar_productos_precio_con_tope("no_confirmado", "no_confirmado", piezas)


def _avisar_huerfana_precio(platform: str, resumen) -> int:
    """La huerfana es un evento de la corrida (A2): si cerro >= 1 avisa una
    vez, sin mirar la historia (B6: `try` propio). Jamas levanta."""
    try:
        huerfanas = resumen.huerfanas or 0
        if huerfanas > 0:
            huerfana = HuerfanaPrecio(platform, int(huerfanas))
            if notifica_precio("huerfana_sin_patch", huerfana):
                return 1
        return 0
    except Exception as exc:  # noqa: BLE001 - B6: la huerfana no calla
        logger.warning("telegram: fallo avisando huerfanas de precio: %s", scrub(str(exc)))
        return 0


def avisar_precio(conn, platform: str, hoy, resumen) -> int:
    """Gancho `avisar` de la corrida A.5 (`correr(..., avisar=...)` lo llama
    con `(conn, platform, hoy, resumen)` una vez al final, fail-silent).
    Lee hoy y dias previos y manda en flanco por racha: devuelve cuantos
    avisos salieron; jamas levanta (fallo -> warning con scrub + 0).

    B2: los avisos por grupo y por producto solo salen si la corrida
    persistio >= 1 decision (`resumen.decisiones > 0`): una reejecucion el
    mismo dia no reenvia. Residuo declarado: si una corrida cae despues de
    persistir todo y antes de avisar, ese dia no avisa (queda el log).
    Residuo declarado (r2): una segunda corrida del mismo dia que persiste
    >= 1 decision nueva (un goal agregado despues de la corrida de la
    manana) reenvia todos los avisos del dia; no se corrige en A.6.

    B4: un umbral `precio_aviso_dias_sin_evaluar` invalido o ausente solo
    apaga el aviso `no_evaluado` (warning); los demas tipos salen igual.
    """
    try:
        fila = conn.execute(_SQL_PRECIO_CONFIG).fetchone()
        if fila is None:
            return 0
        try:
            umbral = validar_precio_aviso_dias(fila[0])
        except ValueError as exc:
            logger.warning("telegram: umbral de aviso de precio invalido: %s", scrub(str(exc)))
            umbral = None
        ayer = hoy - dt.timedelta(days=1)
        enviados = 0
        if resumen.decisiones > 0:
            enviados += _avisar_grupos_precio(conn, platform, hoy, ayer, umbral)
            enviados += _avisar_buybox_precio(conn, platform, hoy, ayer)
            enviados += _avisar_noconf_precio(conn, platform, hoy, ayer)
        enviados += _avisar_huerfana_precio(platform, resumen)
        return enviados
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando los avisos de precio: %s", scrub(str(exc)))
        return 0
