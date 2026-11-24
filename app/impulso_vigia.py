"""Vigia diario del impulso y reconciliacion de lotes huerfanos (BIDS 02, I.3).

Extraido de `app/impulso_io.py` (Ola D2). Elimina la duplicacion de
`graduado_sin_reencender` y redacta errores impresos a stderr via `scrub`.
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal

from app import goals_write, retiro_anuncios
from app import impulso as im
from app.impulso_preview import _SQL_ESTADO_LOTE, AccionVigia, PausadorDeCampanas
from app.redaction import scrub

_REACTIVA_SOLO_VIGIA = ("impulso-vigia",)

_SQL_VIVOS = """
SELECT i.id, i.platform, i.lote, i.tope_monto, i.tope_moneda, i.presupuesto_diario,
       i.plazo_dias, i.lanzado_el, i.grupo_id, i.listing_id, i.go_literal
  FROM impulso i
  LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final
 WHERE l.impulso_id IS NULL
 ORDER BY i.id
"""

_SQL_YA_TUVO_IMPRESIONES = """
SELECT 1 FROM impulso_lectura
 WHERE impulso_id = %s AND impresiones IS NOT NULL AND impresiones > 0
"""

_SQL_CAMPANAS_DE_IMPULSO = """
SELECT r.ad_entity_id
  FROM campana_grupo_rol r
  JOIN impulso i ON i.grupo_id = r.grupo_id
 WHERE i.id = %s
"""

_SQL_CORTE_PLATAFORMA = """
SELECT max(v.metric_date) FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = %s AND e.kind = 'campaign'
"""

_SQL_DIAS_CON_INGESTA = """
SELECT count(DISTINCT v.metric_date) FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = %s AND v.metric_date BETWEEN %s AND %s
   AND e.kind = 'campaign'
"""

_SQL_OBSERVADO = """
SELECT v.metric_date, v.impressions, v.clicks, v.cost, v.orders
  FROM v_metric_latest v
 WHERE v.ad_entity_id = ANY(%s) AND v.metric_date >= %s
"""

_SQL_GOALS_DE_IMPULSO = """
SELECT g.id
  FROM ads_optimizer_goal g
  JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id
  JOIN impulso i ON i.grupo_id = r.grupo_id
 WHERE i.id = %s AND g.scope = 'campaign'
"""

_RECONCILIA_ANTIGUEDAD_MIN = 60
_SQL_LOTES_HUERFANOS = """
SELECT f.lote FROM fabrica_lote f
 WHERE f.lote LIKE 'impulso-%%'
   AND f.estado IN ('planeado', 'failed', 'applied', 'desarmado')
   AND NOT EXISTS (SELECT 1 FROM impulso i WHERE i.lote = f.lote)
   AND f.created_at < now() - make_interval(mins => %s)
   AND EXISTS (SELECT 1 FROM fabrica_lote_paso p
                WHERE p.lote = f.lote AND p.external_id IS NOT NULL)
 ORDER BY f.lote
"""


def _acumula(filas):
    """Suma por metrica; si alguna fila trae NULL, el acumulado es None."""
    tot = []
    for col in (1, 2, 3, 4):
        valores = [f[col] for f in filas]
        tot.append(None if any(v is None for v in valores) else sum(valores, 0))
    return tuple(tot)


def _marcar_graduado_sin_reencender(conn, lote: str, pauso: str | None) -> str:
    quien = pauso if pauso is not None else "fuera de Orbit (consola?)"
    conn.execute(
        "UPDATE fabrica_lote SET detalle = %s WHERE lote = %s",
        (f"graduado sin reencender: pauso {quien}, goals a live", lote),
    )
    return "graduado_sin_reencender"


def _vigila_uno(conn, pausador, fila, *, hoy, escritor_retiro):
    """Un impulso vivo: lee, actua y guarda."""
    (imp, platform, lote, monto, moneda, diario, plazo, lanzado, grupo, listing, go) = fila
    tope = im.TopeAprendizaje(Decimal(monto), moneda, Decimal(diario), plazo)
    corte = conn.execute(_SQL_CORTE_PLATAFORMA, (platform,)).fetchone()[0]
    if corte is None:
        lectura = im.LecturaImpulso(hoy, 0, None, None, None, None)
        conn.commit()
        return (
            AccionVigia(
                imp,
                lectura,
                im.VeredictoImpulso("en_curso", False, "seguir"),
                "nada",
                atendido=False,
            ),
            escritor_retiro,
        )
    ayer = hoy - dt.timedelta(days=1)
    if corte > ayer:
        corte = ayer
    dias = conn.execute(_SQL_DIAS_CON_INGESTA, (platform, lanzado.date(), corte)).fetchone()[0]
    camps = [f[0] for f in conn.execute(_SQL_CAMPANAS_DE_IMPULSO, (imp,)).fetchall()]
    filas = [f for f in conn.execute(_SQL_OBSERVADO, (camps, lanzado.date())).fetchall()]
    filas = [f for f in filas if f[0] <= corte]
    if filas:
        impr, clics, gasto, pedidos = _acumula(filas)
    else:
        impr, clics, gasto, pedidos = 0, 0, Decimal("0"), 0
    lectura = im.LecturaImpulso(corte, dias, impr, clics, gasto, pedidos)
    veredicto = im.veredicto_impulso(lectura, tope)
    hecho = "nada"
    ya = conn.execute(
        "SELECT hecho FROM impulso_lectura WHERE impulso_id = %s AND corte = %s",
        (imp, corte),
    ).fetchone()
    if ya is not None:
        conn.commit()
        return AccionVigia(imp, lectura, veredicto, ya[0]), escritor_retiro
    estado_lote = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
    pausado_por_dueno = (
        estado_lote is not None and estado_lote[0] == "pausado" and estado_lote[1] == "dueno"
    )
    if (
        estado_lote is not None
        and veredicto.accion != "graduar"
        and (estado_lote[0] == "desarmado" or pausado_por_dueno)
    ):
        veredicto = im.VEREDICTO_DESARMADO
    if veredicto.accion == "pausar":
        if estado_lote is None or estado_lote[0] not in ("pausado", "desarmado"):
            pausador.pausa(lote)
            conn.execute(
                "UPDATE fabrica_lote SET estado = 'pausado', pausado_por = 'impulso-vigia'"
                " WHERE lote = %s AND estado = 'applied'",
                (lote,),
            )
        else:
            pausador.pausa_huerfanas(lote)
        hecho = "pausado"
    elif veredicto.accion == "graduar":
        reencendio = (
            estado_lote is not None
            and estado_lote[0] != "desarmado"
            and estado_lote[1] in _REACTIVA_SOLO_VIGIA
        )
        if reencendio:
            pausador.reactiva(lote)
        ahora = dt.datetime.now(dt.UTC)
        for (gid,) in conn.execute(_SQL_GOALS_DE_IMPULSO, (imp,)).fetchall():
            goals_write.edita_goal(conn, gid, mode="live", updated_at=ahora)
        if reencendio or (estado_lote is not None and estado_lote[0] == "desarmado"):
            hecho = "graduado"
        elif estado_lote is not None and estado_lote[0] == "pausado":
            hecho = _marcar_graduado_sin_reencender(conn, lote, estado_lote[1])
        else:
            try:
                hay_pausada = any(s != "ENABLED" for s in pausador.estados(lote).values())
            except Exception:
                hay_pausada = True
            if hay_pausada:
                pauso = estado_lote[1] if estado_lote is not None else None
                hecho = _marcar_graduado_sin_reencender(conn, lote, pauso)
            else:
                hecho = "graduado"
    if hecho in ("nada", "graduado") and (lectura.impresiones or 0) > 0:
        primero = conn.execute(_SQL_YA_TUVO_IMPRESIONES, (imp,)).fetchone() is None
        if primero:
            plan = retiro_anuncios.planea_retiro(
                conn,
                platform,
                listing_ids=(listing,),
                motivo="impulso",
                conservar_grupo_id=grupo,
            )
            pendientes = retiro_anuncios.retiros_pendientes_de_impulso(conn, imp, plan)
            if pendientes.anuncios:
                if escritor_retiro is None:
                    from app.ads import archivar

                    escritor_retiro = archivar.preparar_escritor(
                        platform, modo_confirmado=archivar.MODO_AUTORIZADO_LIVE
                    )
                resultados = retiro_anuncios.ejecuta_retiro(
                    conn,
                    pendientes,
                    autorizacion=retiro_anuncios.AutorizacionHumana(go, "impulso-vigia"),
                    impulso_id=imp,
                    escritor=escritor_retiro,
                )
                falta = retiro_anuncios.no_resueltos(resultados)
                if falta is not None:
                    raise RuntimeError(falta)
                if hecho == "nada":
                    hecho = "retiro_de_bolsas"
    conn.execute(
        "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
        " impresiones, clics, gasto, gasto_moneda, pedidos, veredicto, es_final, hecho)"
        " VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (impulso_id, corte) DO NOTHING",
        (
            imp,
            corte,
            dias,
            impr,
            clics,
            gasto,
            moneda if gasto is not None else None,
            pedidos,
            veredicto.estado,
            veredicto.final,
            hecho,
        ),
    )
    conn.commit()
    return AccionVigia(imp, lectura, veredicto, hecho), escritor_retiro


def reconcilia_huerfanos(conn, pausador) -> list[str]:
    reconcilia = getattr(pausador, "pausa_huerfanas", None)
    if reconcilia is None:
        return []
    lotes = [
        r[0] for r in conn.execute(_SQL_LOTES_HUERFANOS, (_RECONCILIA_ANTIGUEDAD_MIN,)).fetchall()
    ]
    conn.commit()
    pausados = []
    for lote in lotes:
        try:
            if reconcilia(lote):
                pausados.append(lote)
        except Exception as exc:
            conn.rollback()
            print(f"reconcilia: lote {lote} fallo ({scrub(str(exc))})", file=sys.stderr)
    if pausados:
        print(f"reconcilia: pausadas huerfanas de {pausados}", file=sys.stderr)
    return pausados


def vigila(
    conn, pausador: PausadorDeCampanas, *, hoy: dt.date, escritor_retiro=None
) -> list[AccionVigia]:
    acciones = []
    reconcilia_huerfanos(conn, pausador)
    retiro_anuncios.reconcilia_intenciones(conn, escritor=escritor_retiro)
    escritores: dict[str, object] = {}
    for fila in conn.execute(_SQL_VIVOS).fetchall():
        platform = fila[1]
        fijo = escritor_retiro if escritor_retiro is not None else escritores.get(platform)
        try:
            accion, usado = _vigila_uno(conn, pausador, fila, hoy=hoy, escritor_retiro=fijo)
        except Exception as exc:
            conn.rollback()
            print(f"vigila: impulso {fila[0]} fallo ({scrub(str(exc))})", file=sys.stderr)
            continue
        if escritor_retiro is None and usado is not None:
            escritores[platform] = usado
        if not accion.atendido:
            print(
                f"vigila: impulso {accion.impulso_id} sin ingesta de su plataforma"
                " (corte None): no se guardo lectura",
                file=sys.stderr,
            )
        acciones.append(accion)
    return acciones
