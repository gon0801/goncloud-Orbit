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
       sum(contrib_con_halo)
  FROM v_contribucion_entidad
 WHERE platform = %s::platform
 GROUP BY metric_currency
"""

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
    if datos.rango is not None:
        r = datos.rango
        cuerpo = (
            f"{_formatea_monto(r.sin_halo)} .. {_formatea_monto(r.con_halo)}"
            f" {r.moneda} ({r.entidades} entidades)"
        )
    elif datos.sin_dato is not None:
        s = datos.sin_dato
        motivos = ", ".join(f"{m} {n}" for m, n in s.por_motivo)
        cuerpo = f"sin dato ({s.total_ausentes} entidades ausentes: {motivos})"
    else:
        return None
    return f"{ETIQUETA_CONTRIBUCION}: {cuerpo}"


def _arma_contribucion_digest(
    filas_rango: list[tuple],
    filas_ausentes: list[tuple],
    residual: Decimal | None,
) -> ContribucionDigest | None:
    rango: RangoContribucion | None = None
    sin_dato: SinDatoContribucion | None = None
    if len(filas_rango) == 1:
        moneda, entidades, sin_h, con_h = filas_rango[0]
        if entidades and sin_h is not None and con_h is not None:
            rango = RangoContribucion(moneda, entidades, sin_h, con_h)
    if rango is None and filas_ausentes:
        por_motivo = tuple((m, n) for m, n in filas_ausentes)
        total = sum(n for _, n in por_motivo)
        if total > 0:
            sin_dato = SinDatoContribucion(total, por_motivo)
    if rango is None and sin_dato is None:
        return None
    res_tacos = ResidualTacos(residual) if residual is not None and residual > 0 else None
    return ContribucionDigest(rango=rango, sin_dato=sin_dato, residual_tacos=res_tacos)


def carga_contribucion_digest(plataforma: str, *, conn=None) -> ContribucionDigest | None:
    """Lee v_contribucion_entidad / cobertura / v_tacos (ORBIT_DSN_READ).

      Fail-silent hacia arriba: el caller omite la seccion si devuelve None.
    Solo lectura; sin ORBIT_DSN_READ devuelve None sin excepcion."""
    propia = conn is None
    try:
        if conn is None:
            dsn = os.environ.get("ORBIT_DSN_READ")
            if not dsn:
                return None
            conn = connect(dsn)
        filas_rango = conn.execute(SQL_CONTRIB_RANGO, (plataforma,)).fetchall()
        filas_ausentes: list[tuple] = []
        if not filas_rango or not filas_rango[0][1]:
            filas_ausentes = conn.execute(SQL_CONTRIB_AUSENTES, (plataforma,)).fetchall()
        residual_row = conn.execute(SQL_RESIDUAL_TACOS, (plataforma,)).fetchone()
        residual = residual_row[0] if residual_row else None
        return _arma_contribucion_digest(filas_rango, filas_ausentes, residual)
    except Exception as exc:  # noqa: BLE001 - fail-silent (digest sigue sin contrib)
        logger.warning("telegram: fallo leyendo contribucion para digest: %s", scrub(str(exc)))
        return None
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
    contrib = resumen.get("contribucion")
    if isinstance(contrib, ContribucionDigest):
        linea = _linea_contribucion(contrib)
        if linea:
            lineas.append(linea)
        if contrib.residual_tacos is not None:
            lineas.append(
                f"residual tacos campaign: {_formatea_monto(contrib.residual_tacos.monto)} MXN"
            )
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
            try:
                contrib = carga_contribucion_digest(plataforma)
            except Exception:  # noqa: BLE001 - contrib es opcional
                contrib = None
            if contrib is not None:
                payload = {**resumen, "contribucion": contrib}
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
