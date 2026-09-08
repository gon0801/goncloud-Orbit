"""Alertas de reputacion v1 (REPUTACION 01 A.5; acta 0.5 D3 + §2).

Evaluadores PUROS (cero I/O): reciben datos, devuelven AlertaNueva o
None. El flanco (abrir solo al subir, resolver al bajar) lo aplica la
capa DB contra reputation_alert. Sin datos = None, nunca un disparo
inventado (regla 3).

Tipos y fuentes (acta §2; Amazon fuera de resena_1 y reclamos_suben):
- rating_bajo (aviso): ultimo snapshot < 4.2. Por listing.
- resena_1 (critica): MeLi rate==1 publicada, nueva desde la ultima
  evaluacion. Por review (dedupe por mensaje canonico con review id).
- reclamos_suben (aviso, cuenta): disputas nuevas 7d >= previas 7d + 2
  (D-A5-2: nuevas = total(hoy) - total(-7d); requiere 3 snapshots).
- caida_rating (aviso): baja >= 0.3 (MeLi 7d / Amazon N vs N-1).
- salud_cuenta (aviso; info si level no mapeado): level_id a peor.

Los mensajes jamas incluyen texto externo (solo ids y numeros):
seguros para Telegram sin parse_mode (el titulo/texto vive en DB
para la pantalla A.6).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from dataclasses import dataclass

from app.db import connect
from app.redaction import redact_dsn, scrub

UMBRAL_RATING_BAJO = 4.2
UMBRAL_CAIDA_RATING = 0.3
DELTA_RECLAMOS = 2
VENTANA_RECLAMOS_DIAS = 7

# peor -> mejor (mapa explicito del acta; lo no mapeado = info visible).
MAPA_LEVEL = {
    "1_red": 1,
    "2_orange": 2,
    "3_yellow": 3,
    "4_light_green": 4,
    "5_green": 5,
}


@dataclass(frozen=True)
class AlertaNueva:
    """Alerta calificada por un evaluador puro (aun no persistida)."""

    tipo: str
    severidad: str  # info | aviso | critica
    platform: str | None
    external_id: str | None  # None = alerta de cuenta
    mensaje: str


def evalua_rating_bajo(platform: str, external_id: str, rating: float | None) -> AlertaNueva | None:
    """Ultimo snapshot bajo el umbral (acta §2: < 4.2, en flanco)."""
    if rating is None or rating >= UMBRAL_RATING_BAJO:
        return None
    return AlertaNueva(
        tipo="rating_bajo",
        severidad="aviso",
        platform=platform,
        external_id=external_id,
        mensaje=f"rating {rating:.1f} < {UMBRAL_RATING_BAJO}",
    )


def evalua_resena_1(
    item_id: str, review_id: str, rate: int | None, publicada: bool
) -> AlertaNueva | None:
    """Review MeLi de 1 estrella publicada. Solo califica; lo de
    "nueva desde la ultima evaluacion" lo decide la capa DB (dedupe
    por mensaje canonico, que incluye el review id)."""
    if rate != 1 or not publicada:
        return None
    return AlertaNueva(
        tipo="resena_1",
        severidad="critica",
        platform="meli",
        external_id=item_id,
        mensaje=f"1 estrella nueva: review {review_id} en {item_id}",
    )


def evalua_reclamos_suben(
    total_hoy: int | None, total_7d: int | None, total_14d: int | None
) -> AlertaNueva | None:
    """Disputas acelerandose (D-A5-2). Cuenta MeLi (external_id None)."""
    if total_hoy is None or total_7d is None or total_14d is None:
        return None
    nuevas = total_hoy - total_7d
    previas = total_7d - total_14d
    if nuevas < previas + DELTA_RECLAMOS:
        return None
    return AlertaNueva(
        tipo="reclamos_suben",
        severidad="aviso",
        platform="meli",
        external_id=None,
        mensaje=f"disputas nuevas 7d: {nuevas} (previas {previas})",
    )


def evalua_caida_rating(
    platform: str, external_id: str, actual: float | None, previo: float | None
) -> AlertaNueva | None:
    """Baja de rating >= 0.3. La ventana la elige la capa DB (MeLi 7d,
    Amazon N vs N-1); aqui solo la resta (None = sin datos)."""
    if actual is None or previo is None:
        return None
    # Epsilon: 4.8-4.5 da 0.2999... en float y el acta pide >= 0.3.
    if previo - actual + 1e-9 < UMBRAL_CAIDA_RATING:
        return None
    return AlertaNueva(
        tipo="caida_rating",
        severidad="aviso",
        platform=platform,
        external_id=external_id,
        mensaje=f"rating {previo:.1f} -> {actual:.1f} (-{previo - actual:.1f})",
    )


def evalua_salud_cuenta(level_actual: str | None, level_previo: str | None) -> AlertaNueva | None:
    """Level a peor (mapa explicito). Desconocido = info visible, no
    silencio: el operador debe ver que hay algo sin mapear."""
    if level_actual is None or level_previo is None:
        return None
    rango_actual = MAPA_LEVEL.get(level_actual)
    if rango_actual is None:
        return AlertaNueva(
            tipo="salud_cuenta",
            severidad="info",
            platform="meli",
            external_id=None,
            mensaje=f"level no mapeado: {level_actual} (previo {level_previo})",
        )
    rango_previo = MAPA_LEVEL.get(level_previo)
    if rango_previo is None or rango_actual >= rango_previo:
        return None
    return AlertaNueva(
        tipo="salud_cuenta",
        severidad="aviso",
        platform="meli",
        external_id=None,
        mensaje=f"level {level_previo} -> {level_actual}",
    )


# ---------------------------------------------------------------------------
# Capa DB: lectura, flanco y persistencia (UNA transaccion)
# ---------------------------------------------------------------------------

# Tipos que se auto-resuelven al dejar de calificar. resena_1 no: una
# review 1 estrella no deja de existir (D-A5-7; la atiende el operador).
_AUTO_RESUELVEN = frozenset({"rating_bajo", "caida_rating", "reclamos_suben", "salud_cuenta"})


def _lee_snapshots(conn) -> dict:
    """Snapshots por (platform, external_id), recientes primero.

    Se lee toda la tabla y se agrupa en Python: a esta escala (~500
    filas/semana) es trivial y la seleccion de ventanas queda auditable.
    """
    filas = conn.execute(
        "SELECT platform, external_id, metric_date, rating"
        " FROM reputation_snapshot ORDER BY metric_date DESC, observed_at DESC"
    ).fetchall()
    por_entidad: dict = {}
    for plat, ext, fecha, rating in filas:
        nulo = rating is None
        por_entidad.setdefault((plat, ext), []).append((fecha, None if nulo else float(rating)))
    return por_entidad


def _lee_seller(conn) -> list:
    """Snapshots de cuenta MeLi, recientes primero."""
    return conn.execute(
        "SELECT metric_date, level_id, disputas_total FROM seller_reputation_snapshot"
        " ORDER BY metric_date DESC, observed_at DESC"
    ).fetchall()


def _lee_reviews_1(conn) -> list:
    """Reviews MeLi 1 estrella publicadas (una fila por review).

    Grok XR-1 R2-2: 0027 permite N filas por review; DISTINCT ON por
    review toma la mas reciente por observed_at (A.5 duena).
    Kimi A.R H1: el filtro va en el OUTER — filtrar antes del
    DISTINCT ON resucitaba la fila vieja de una review moderada
    (alerta critica fantasma que nunca se auto-resuelve).
    """
    return conn.execute(
        "SELECT external_id, review_external_id FROM ("
        " SELECT DISTINCT ON (platform, review_external_id)"
        " platform, external_id, review_external_id, rating, publicada"
        " FROM review_event WHERE platform = 'meli'"
        " ORDER BY platform, review_external_id, observed_at DESC"
        ") s WHERE rating = 1 AND publicada"
    ).fetchall()


def _lee_abiertas(conn) -> list:
    return conn.execute(
        "SELECT id, platform, external_id, tipo, mensaje FROM reputation_alert WHERE NOT resolved"
    ).fetchall()


def _lee_mensajes_resena_1(conn) -> set:
    """Mensajes canonicos de resena_1 ya alertadas (abiertas o no)."""
    filas = conn.execute("SELECT mensaje FROM reputation_alert WHERE tipo = 'resena_1'").fetchall()
    return {f[0] for f in filas}


def _duplicada(alerta: AlertaNueva, abiertas_llave: set, abiertas_msg: set) -> bool:
    """resena_1 dedupe por mensaje (lleva el review id): una 2a review
    1 estrella del mismo item SI abre. El resto, por entidad+tipo."""
    if alerta.tipo == "resena_1":
        return (alerta.tipo, alerta.mensaje) in abiertas_msg
    return (alerta.tipo, alerta.platform, alerta.external_id) in abiertas_llave


def _ventana_previa(snaps: list, hoy, dias: int):
    """Snapshot mas reciente con metric_date <= hoy - dias (o None)."""
    corte = hoy - dt.timedelta(days=dias)
    for fecha, rating in snaps:
        if fecha <= corte:
            return fecha, rating
    return None


def evalua_y_persiste(conn, hoy=None, dry_run: bool = False) -> dict:
    """Evalua los 5 tipos con lo ultimo en DB y aplica el flanco.

    Abre INSERT solo si califica y no hay abierta igual; resuelve las
    abiertas que se evaluaron y ya no califican (sin datos = intactas,
    regla 3). UNA transaccion; dry_run calcula sin escribir.
    """
    hoy = hoy or dt.datetime.now(dt.UTC).date()
    with conn.transaction():
        califican: list = []  # AlertaNueva que califican esta corrida
        evaluadas: set = set()  # llaves evaluadas (califiquen o no)

        snaps = _lee_snapshots(conn)
        for (plat, ext), serie in snaps.items():
            actual = serie[0][1]
            if actual is not None:
                evaluadas.add(("rating_bajo", plat, ext))
                alerta = evalua_rating_bajo(plat, ext, actual)
                if alerta is not None:
                    califican.append(alerta)
            if plat == "meli":
                previa = _ventana_previa(serie[1:], hoy, VENTANA_RECLAMOS_DIAS)
            else:
                fechas = [f for f, _ in serie]
                previa = None
                for fecha, rating in serie[1:]:
                    if fecha != fechas[0]:
                        previa = (fecha, rating)
                        break
            if actual is not None and previa is not None and previa[1] is not None:
                evaluadas.add(("caida_rating", plat, ext))
                alerta = evalua_caida_rating(plat, ext, actual, previa[1])
                if alerta is not None:
                    califican.append(alerta)

        seller = _lee_seller(conn)
        if len(seller) >= 2 and seller[0][1] is not None and seller[1][1] is not None:
            evaluadas.add(("salud_cuenta", "meli", None))
            alerta = evalua_salud_cuenta(seller[0][1], seller[1][1])
            if alerta is not None:
                califican.append(alerta)
        tot_hoy = seller[0][2] if seller and seller[0][0] <= hoy else None
        tot_7 = _ventana_previa([(f, t) for f, _l, t in seller], hoy, 7)
        tot_14 = _ventana_previa([(f, t) for f, _l, t in seller], hoy, 14)
        if (
            tot_hoy is not None
            and tot_7 is not None
            and tot_7[1] is not None
            and tot_14 is not None
            and tot_14[1] is not None
        ):
            evaluadas.add(("reclamos_suben", "meli", None))
            alerta = evalua_reclamos_suben(tot_hoy, tot_7[1], tot_14[1])
            if alerta is not None:
                califican.append(alerta)

        ya_alertadas = _lee_mensajes_resena_1(conn)
        for item_id, review_id in _lee_reviews_1(conn):
            alerta = evalua_resena_1(item_id, review_id, 1, True)
            if alerta is not None and alerta.mensaje not in ya_alertadas:
                califican.append(alerta)
                ya_alertadas.add(alerta.mensaje)  # 1 corrida, 1 alerta

        abiertas = _lee_abiertas(conn)
        abiertas_llave = {(t, p, e) for _i, p, e, t, _m in abiertas}
        abiertas_msg = {(t, m) for _i, _p, _e, t, m in abiertas}
        nuevas = [a for a in califican if not _duplicada(a, abiertas_llave, abiertas_msg)]
        llaves_califican = {(a.tipo, a.platform, a.external_id) for a in califican}
        a_resolver = [
            i
            for i, p, e, t, _m in abiertas
            if t in _AUTO_RESUELVEN and (t, p, e) not in llaves_califican and (t, p, e) in evaluadas
        ]

        if not dry_run:
            for alerta in nuevas:
                conn.execute(
                    "INSERT INTO reputation_alert"
                    " (platform, external_id, tipo, severidad, mensaje)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (
                        alerta.platform,
                        alerta.external_id,
                        alerta.tipo,
                        alerta.severidad,
                        alerta.mensaje,
                    ),
                )
            for ident in a_resolver:
                conn.execute(
                    "UPDATE reputation_alert"
                    " SET resolved = TRUE, resolved_at = now() WHERE id = %s",
                    (ident,),
                )
    return {
        "ok": True,
        "abiertas": len(nuevas),
        "resueltas": len(a_resolver),
        "alertas": [
            {
                "tipo": a.tipo,
                "severidad": a.severidad,
                "platform": a.platform,
                "external_id": a.external_id,
                "mensaje": a.mensaje,
            }
            for a in nuevas
        ],
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Digest: novedades (D-A5-1) + entrada CLI (ORBIT_DSN_DECIDE)
# ---------------------------------------------------------------------------

VENTANA_DIGEST_HORAS = 24


def carga_reputacion_digest(conn=None) -> list | None:
    """Alertas abiertas creadas en las ultimas 24h (novedades del dia).

    Criticas primero, luego por antiguedad. Sin ORBIT_DSN_READ o ante
    cualquier fallo devuelve None (el digest sigue sin el bloque,
    fail-silent patron contrib). Lista vacia = sin novedades = el
    digest no imprime nada (regla 3).
    """
    propia = conn is None
    try:
        if conn is None:
            dsn = os.environ.get("ORBIT_DSN_READ")
            if not dsn:
                return None
            conn = connect(dsn)
        filas = conn.execute(
            "SELECT tipo, severidad, platform, external_id, mensaje"
            " FROM reputation_alert WHERE NOT resolved"
            " AND created_at >= now() - make_interval(hours => %s)"
            " ORDER BY CASE severidad"
            "  WHEN 'critica' THEN 0 WHEN 'aviso' THEN 1 ELSE 2 END,"
            " created_at",
            (VENTANA_DIGEST_HORAS,),
        ).fetchall()
        return [
            {
                "tipo": t,
                "severidad": s,
                "platform": p,
                "external_id": e,
                "mensaje": m,
            }
            for t, s, p, e, m in filas
        ]
    except Exception:
        return None
    finally:
        if propia and conn is not None:
            conn.close()


def ejecuta_alertas(fecha: str | None = None, dry_run: bool = False) -> int:
    """`reputacion alertas`: evalua con lo ultimo en DB (AC4 same-day).

    Corre con ORBIT_DSN_DECIDE (unico rol con INSERT/UPDATE en
    reputation_alert, 0024). Exit 0/1/2, patron ejecuta_snapshot; cero
    alertas NO es fallo (es lo normal).
    """
    metric_date: dt.date | None = None
    if fecha is not None:
        try:
            metric_date = dt.date.fromisoformat(fecha)
        except ValueError:
            print(f"--fecha invalida (YYYY-MM-DD): {fecha!r}", file=sys.stderr)
            return 2
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        print(
            "ORBIT_DSN_DECIDE no esta definido: no se puede evaluar (fail-closed)",
            file=sys.stderr,
        )
        return 2
    redact_dsn(dsn)  # registra la password para scrub(), defensa en profundidad
    try:
        conn = connect(dsn)
        try:
            resumen = evalua_y_persiste(conn, metric_date, dry_run=dry_run)
        finally:
            conn.close()
    except Exception as exc:
        print(f"reputacion alertas fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    print(json.dumps(resumen, ensure_ascii=False))
    return 0
