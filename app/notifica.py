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
``transport`` se conserva por compatibilidad de firma (APAGON 2026-09-16:
se ignora, cero red). Toda la superficie publica devuelve bool y JAMAS
levanta excepciones hacia arriba.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.db import connect
from app.redaction import install_scrub_filter, scrub

if TYPE_CHECKING:
    # Solo anotacion: importarlo en runtime crearia el ciclo
    # apply_harvest -> notifica -> apply_harvest.
    from app.apply_harvest import AlertaHarvest

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

TELEGRAM_FILENAME = "telegram.json"

# APAGON 2026-09-16: sin red. `_transporte_test` queda como compat (siempre
# ignorado); los fixtures viejos pueden seguir seteandolo sin romper.
_transporte_test: object | None = None

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


# Cache por proceso (el resolve lee el FS una sola vez; el logger.info de
# "deshabilitado" sale UNA vez). `_reset()` es la puerta de los tests.
_estado: dict = {}


def _reset() -> None:
    """Solo tests: borra el cache de config para que cada escenario resuelva
    de nuevo (produccion jamas lo necesita)."""
    _estado.clear()


def _config_canal() -> None:
    """Canal DESHABILITADO por decision (chat limpio, 2026-09-16).

    Todos los avisos Telegram quedan apagados: los ``notifica_*`` devuelven
    True sin enviar (no generan NOTA) y la logica de negocio (encolado,
    veto 48h, digest, harvest, SP-API, cap, biblioteca, destino) corre igual.
    Visibilidad: log local + notes/salud existentes.
    """
    if "config" not in _estado:
        logger.info(
            "canal Telegram deshabilitado por decision (chat limpio): "
            "los avisos no salen y eso NO es fallo ni genera nota"
        )
        _estado["config"] = None
    return None


def canal_activo() -> bool:
    """True si el canal esta configurado (telegram.json valido)."""
    return _config_canal() is not None


def _envia_texto(texto: str, transport: object | None = None) -> bool:
    """APAGON TOTAL Telegram (2026-09-16): no envia red, deja log local y
    devuelve True (contrato canal deshabilitado: no es fallo, sin NOTA)."""
    logger.info("aviso Telegram suprimido (chat limpio, ver log/salud): %s", scrub(texto[:500]))
    return True


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


def notifica_encola(fila: CorteEncolado, *, transport: object | None = None) -> bool:
    """Aviso de UN corte nuevo encolado. False = fallo del canal (el caller
    deja la NOTA); canal deshabilitado -> True."""
    try:
        if not canal_activo():
            return True
        return _envia_texto(aviso_corte_encolado(fila), transport=transport)
    except Exception as exc:  # noqa: BLE001 - fail-silent (docstring del modulo)
        logger.warning("telegram: fallo armando el aviso de encola: %s", scrub(str(exc)))
        return False


def notifica_digest(resumen: dict, *, transport: object | None = None) -> bool:
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


def notifica_harvest_failed(alerta: AlertaHarvest, *, transport: object | None = None) -> bool:
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


def notifica_harvest_hermanas(alerta: AlertaHarvest, *, transport: object | None = None) -> bool:
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
    fuente: str, platform: str, motivo: str, *, transport: object | None = None
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


def notifica_spapi_silencio(faltantes, desde, hasta, *, transport: object | None = None) -> bool:
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
    plataforma: str, kind: str, used: int, cap: int, *, transport: object | None = None
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
    transport: object | None = None,
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


def notifica_destino_grupo(salto: SaltoDestinoGrupo, *, transport: object | None = None) -> bool:
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
