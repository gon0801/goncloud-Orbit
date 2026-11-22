"""Lanzamiento de lotes de impulso, sellado y reconciliacion de huerfanos en lanza.

Extraido de `app/impulso_io.py` (Ola D2). `_descarta_huerfano` usa `with
_pausador_propio(conn)` para cerrar tanto el `httpx.Client` como la conexion DB.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app import impulso as im
from app.impulso_preview import (
    _SQL_ESTADO_LOTE,
    EjecutorFabrica,
    ErrorImpulso,
    ImpulsoLanzado,
    SolicitudImpulso,
    VistaPreviaImpulso,
    _conecta_propia,
    _exige_acepto_presupuesto,
    _pausador_propio,
    _reactiva_para_sellar,
    lote_de_plan,
    previsualiza,
)

_SQL_VIVO_PRODUCTO = """
SELECT i.id, i.lote, i.listing_id FROM impulso i
 LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final
 WHERE i.platform = %s AND i.product_id = %s AND l.impulso_id IS NULL
 LIMIT 1
"""

_SQL_PRODUCTO_DE_LISTING = """
SELECT product_id FROM listing WHERE id = %s AND platform = %s
"""

_SQL_IMPULSO_DE_LOTE = """
SELECT id FROM impulso WHERE lote = %s
"""

_SQL_GRUPO_DE_LOTE = """
SELECT id FROM campana_grupo WHERE lote = %s
"""

_SQL_PENDIENTES_LISTING = """
SELECT f.lote, f.estado, g.id
  FROM fabrica_lote f
  LEFT JOIN campana_grupo g ON g.lote = f.lote
 WHERE f.lote LIKE %s
   AND f.estado IN ('applied', 'failed', 'planeado')
   AND NOT EXISTS (SELECT 1 FROM impulso i WHERE i.lote = f.lote)
 ORDER BY f.created_at DESC
"""


def _sella_impulso(conn, plan, grupo_id, lote, actor, confirmacion):
    return conn.execute(
        "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote, subfamilia,"
        " tope_monto, tope_moneda, presupuesto_diario, plazo_dias, lanzado_el, actor,"
        " go_literal) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)"
        " RETURNING id",
        (
            plan.plataforma,
            plan.listing_id,
            plan.product_id,
            grupo_id,
            lote,
            plan.subfamilia,
            plan.tope.monto,
            plan.tope.moneda,
            plan.tope.presupuesto_diario,
            plan.tope.plazo_dias,
            actor,
            confirmacion,
        ),
    ).fetchone()[0]


def _tiene_externals(conn, lote: str) -> bool:
    fila = conn.execute(
        "SELECT 1 FROM fabrica_lote_paso WHERE lote = %s AND external_id IS NOT NULL LIMIT 1",
        (lote,),
    ).fetchone()
    return fila is not None


def _archiva_lote_vacio(conn, lote: str) -> None:
    """H6: archiva un lote failed/planeado sin externals para reusar el nombre."""
    propia = _conecta_propia(conn, autocommit=True)
    try:
        if propia.execute("SELECT 1 FROM impulso WHERE lote = %s", (lote,)).fetchone() is not None:
            raise ErrorImpulso(502, f"lote {lote} tiene impulso: no se archiva")
        base, n = f"{lote}-descartado", 0
        while True:
            nuevo = base if n == 0 else f"{base}-{n}"
            n += 1
            if (
                propia.execute("SELECT 1 FROM fabrica_lote WHERE lote = %s", (nuevo,)).fetchone()
                is None
            ):
                break
        with propia.transaction():
            propia.execute(
                "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
                " huella, plan, modo_goal, estado, detalle, created_at, finished_at, pausado_por)"
                " SELECT %s, platform, tipo_producto, nombre_base, go_literal, huella, plan,"
                " modo_goal, 'desarmado',"
                " 'H6: fallo sin nada creado; el reintento reusa el nombre',"
                " created_at, finished_at, pausado_por FROM fabrica_lote WHERE lote = %s",
                (nuevo, lote),
            )
            propia.execute("UPDATE fabrica_lote_paso SET lote = %s WHERE lote = %s", (nuevo, lote))
            propia.execute("UPDATE campana_grupo SET lote = %s WHERE lote = %s", (nuevo, lote))
            propia.execute("DELETE FROM fabrica_lote WHERE lote = %s", (lote,))
    finally:
        propia.close()


def _plan_coincide_con_lote(conn, plan: im.PlanImpulso, lote: str) -> bool:
    fila = conn.execute("SELECT plan FROM fabrica_lote WHERE lote = %s", (lote,)).fetchone()
    if fila is None or not isinstance(fila[0], dict):
        return False
    guardado = fila[0].get("parametros") or {}
    mitad = (plan.tope.presupuesto_diario / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    bids, _roles = im.acota_bids(plan.bid_por_rol, mitad)
    for rol in im.ROLES_IMPULSO:
        viejo = guardado.get(rol) or {}
        try:
            if Decimal(str(viejo.get("budget"))) != mitad:
                return False
            if Decimal(str(viejo.get("bid"))) != bids[rol]:
                return False
        except (InvalidOperation, ValueError, TypeError, KeyError):
            return False
    return True


def _archiva_vacios(conn, listing_id: int) -> None:
    for otro, estado_otro, _ in conn.execute(
        _SQL_PENDIENTES_LISTING, (f"impulso-{listing_id}-%",)
    ).fetchall():
        if estado_otro in ("failed", "planeado") and not _tiene_externals(conn, otro):
            _archiva_lote_vacio(conn, otro)


def _lanza_par_nuevo(
    conn, plan, lote, actor, confirmacion, ejecutor, aviso_huerfano, lanzados, fallos
) -> bool:
    try:
        hecho = ejecutor(im.grupo_v2_de_plan(plan), plan.huella(), lote)
    except Exception as exc:
        base = f"la fabrica fallo ({exc})"
        fallos[plan.listing_id] = f"{base}; ademas: {aviso_huerfano}" if aviso_huerfano else base
        return False
    if hecho.estado_lote != "applied":
        fallos[plan.listing_id] = f"lote {lote} no quedo applied"
        if aviso_huerfano:
            fallos[plan.listing_id] += f"; ademas: {aviso_huerfano}"
        return False
    imp = _sella_impulso(conn, plan, hecho.grupo_id, lote, actor, confirmacion)
    lanzados.append(ImpulsoLanzado(imp, lote, hecho.estado_lote))
    if aviso_huerfano is not None:
        fallos[plan.listing_id] = aviso_huerfano
    return True


def _descarta_huerfano(conn, lote_viejo: str, actor: str) -> str | None:
    """H2: pausa best-effort (cerrando HTTP y DB via `with`) y desarma el huerfano."""
    del actor
    with _pausador_propio(conn) as pausador:
        try:
            pausador.pausa(lote_viejo)
            extra = "pausado"
            aviso = None
        except Exception as exc:
            extra = f"pausa best-effort fallo ({exc}); el vigia reconcilia"
            aviso = (
                f"huerfano {lote_viejo} de otro plan sigue ENABLED en Amazon"
                f" (su pausa fallo: {exc}); el vigia lo reconcilia"
            )
    conn.execute(
        "UPDATE fabrica_lote SET estado = 'desarmado', detalle = %s WHERE lote = %s",
        (
            f"H2: plan distinto al confirmado ({extra}); el par nuevo nace aparte",
            lote_viejo,
        ),
    )
    return aviso


def _fallos_por_vivo(conn, solicitud: SolicitudImpulso, vista: VistaPreviaImpulso, fallos) -> None:
    for no in vista.no_lanzables:
        prod = conn.execute(
            _SQL_PRODUCTO_DE_LISTING, (no.listing_id, solicitud.plataforma)
        ).fetchone()
        if prod is None:
            continue
        vivo = conn.execute(_SQL_VIVO_PRODUCTO, (solicitud.plataforma, prod[0])).fetchone()
        if vivo is not None and vivo[2] != no.listing_id:
            fallos[no.listing_id] = (
                f"el producto ya tiene un impulso vivo en el listing {vivo[2]} (tope por producto)"
            )


def _falla_si_hay_vivo(conn, plan, fallos) -> bool:
    vivo = conn.execute(_SQL_VIVO_PRODUCTO, (plan.plataforma, plan.product_id)).fetchone()
    if vivo is None:
        return False
    fallos[plan.listing_id] = (
        f"el producto ya tiene un impulso vivo en el listing {vivo[2]} (tope por producto)"
    )
    return True


def _reutiliza_huerfano(
    conn,
    plan: im.PlanImpulso,
    lote_huerfano: str,
    grupo_id: int,
    *,
    actor: str,
    confirmacion: str,
    verificar_externals: bool,
    lanzados: list[ImpulsoLanzado],
    fallos: dict[int, str],
) -> None:
    """Reactiva un lote applied huerfano coincidente y lo sella como impulso."""
    try:
        if not verificar_externals or _tiene_externals(conn, lote_huerfano):
            _reactiva_para_sellar(conn, lote_huerfano)
    except Exception as exc:
        fallos[plan.listing_id] = f"huerfano {lote_huerfano} coincide pero no activa ({exc})"
        return
    imp = _sella_impulso(conn, plan, grupo_id, lote_huerfano, actor, confirmacion)
    lanzados.append(ImpulsoLanzado(imp, lote_huerfano, "applied"))


def _lanza_un_plan(
    conn,
    plan: im.PlanImpulso,
    *,
    actor: str,
    confirmacion: str,
    ejecutor: EjecutorFabrica,
    lanzados: list[ImpulsoLanzado],
    fallos: dict[int, str],
) -> None:
    lote = lote_de_plan(plan)
    with conn.transaction():
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"impulso-producto:{plan.plataforma}:{plan.product_id}",),
        )
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"impulso:{plan.listing_id}",))
        existente = conn.execute(_SQL_IMPULSO_DE_LOTE, (lote,)).fetchone()
        if existente is not None:
            estado = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()[0]
            lanzados.append(ImpulsoLanzado(existente[0], lote, estado))
            return
        if _falla_si_hay_vivo(conn, plan, fallos):
            return
        huerfano = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
        if huerfano is not None:
            grupo = conn.execute(_SQL_GRUPO_DE_LOTE, (lote,)).fetchone()
            if huerfano[0] == "applied" and grupo is not None:
                _reutiliza_huerfano(
                    conn,
                    plan,
                    lote,
                    grupo[0],
                    actor=actor,
                    confirmacion=confirmacion,
                    verificar_externals=True,
                    lanzados=lanzados,
                    fallos=fallos,
                )
                return
            if huerfano[0] in ("failed", "planeado") and not _tiene_externals(conn, lote):
                _archiva_lote_vacio(conn, lote)
            else:
                fallos[plan.listing_id] = f"lote {lote} quedo en {huerfano[0]}: reconcilia a mano"
                return
        pendientes = conn.execute(
            _SQL_PENDIENTES_LISTING, (f"impulso-{plan.listing_id}-%",)
        ).fetchall()
        if pendientes:
            _archiva_vacios(conn, plan.listing_id)
            pendientes = conn.execute(
                _SQL_PENDIENTES_LISTING, (f"impulso-{plan.listing_id}-%",)
            ).fetchall()
        aviso_huerfano = None
        if pendientes:
            malos = [p for p in pendientes if p[1] != "applied" or p[2] is None]
            if malos:
                fallos[plan.listing_id] = (
                    "lotes pendientes de este listing: "
                    + ", ".join(f"{lt} ({estado})" for lt, estado, _ in malos)
                    + ": reconcilia a mano"
                )
                return
            viejo, _, grupo_viejo = pendientes[0]
            if _plan_coincide_con_lote(conn, plan, viejo):
                _reutiliza_huerfano(
                    conn,
                    plan,
                    viejo,
                    grupo_viejo,
                    actor=actor,
                    confirmacion=confirmacion,
                    verificar_externals=False,
                    lanzados=lanzados,
                    fallos=fallos,
                )
                return
            aviso_huerfano = _descarta_huerfano(conn, viejo, actor)
        _lanza_par_nuevo(
            conn, plan, lote, actor, confirmacion, ejecutor, aviso_huerfano, lanzados, fallos
        )


def lanza(
    conn,
    solicitud: SolicitudImpulso,
    *,
    huella: str,
    confirmacion: str,
    actor: str,
    ejecutor: EjecutorFabrica,
    sugeridor=None,
    hoy: dt.date | None = None,
    acepto_presupuesto_alto: bool = False,
) -> list[ImpulsoLanzado]:
    if not solicitud.listing_ids:
        raise ErrorImpulso(422, "impulso sin productos: listing_ids vacio")
    if not actor or not actor.strip():
        raise ErrorImpulso(422, "lanzar exige actor")
    if "\x00" in actor:
        raise ErrorImpulso(422, "actor con NUL")
    vista = previsualiza(conn, solicitud, sugeridor=sugeridor, hoy=hoy)
    fallos: dict[int, str] = {}
    _fallos_por_vivo(conn, solicitud, vista, fallos)
    if not vista.planes and not fallos:
        raise ErrorImpulso(422, "nada lanzable: revisa no_lanzables")
    if vista.huella != huella:
        raise ErrorImpulso(409, "el plan cambio desde la vista previa: previsualiza de nuevo")
    if vista.confirmacion_esperada != confirmacion:
        raise ErrorImpulso(
            422, f"confirmacion distinta: se esperaba {vista.confirmacion_esperada!r}"
        )
    _exige_acepto_presupuesto(vista, acepto_presupuesto_alto)
    lanzados: list[ImpulsoLanzado] = []
    for plan in vista.planes:
        _lanza_un_plan(
            conn,
            plan,
            actor=actor,
            confirmacion=confirmacion,
            ejecutor=ejecutor,
            lanzados=lanzados,
            fallos=fallos,
        )
    if fallos:
        raise ErrorImpulso(
            502, "algunos productos no se lanzaron", lanzados=lanzados, fallos=fallos
        )
    return lanzados
