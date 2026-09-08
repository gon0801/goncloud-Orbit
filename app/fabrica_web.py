"""Adaptador web: el motor CLI conserva la autoria de planes y operaciones."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import httpx
import psycopg
from fastapi import HTTPException
from psycopg.rows import tuple_row

from app import economia_observada
from app import evaluacion_catalogo as ec
from app import fabrica_bids as br
from app import fabrica_plan as fp
from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsConfigError, AdsCredentials
from app.db import OrbitDbError, connect
from app.disponibilidad import estado_disponibilidad
from app.estimacion_proyeccion import adjuntar_estimaciones
from app.redaction import scrub
from tools import fabrica_campanas as fc

SUFIJOS = {
    "category_exact": "exact",
    "category_phrase": "phrase",
    "category_broad": "broad",
    "product_targeting": "product",
    "auto_discovery": "auto",
}

_SQL_RESUMEN_LOTE = """
SELECT l.lote, l.platform::text, l.estado, l.detalle, l.created_at, l.finished_at, l.plan,
       (SELECT count(*) FROM fabrica_lote_paso p WHERE p.lote = l.lote),
       EXISTS (SELECT 1 FROM fabrica_lote_paso p WHERE p.lote = l.lote
               AND p.estado <> 'applied' AND p.external_id IS NULL)
FROM fabrica_lote l
"""
_SQL_LOTE = _SQL_RESUMEN_LOTE + " WHERE l.lote = %s"

_SQL_CATALOGO = """
SELECT p.id, p.odoo_sku, p.name, jsonb_agg(jsonb_build_object(
           'id', l.id, 'asin', l.external_id, 'seller_sku', l.seller_sku,
           'platform', l.platform::text, 'margen_neto_pct', m.margen_neto_pct::text,
           'dias_con_venta', m.dias_con_venta,
           'ventana_desde', m.ventana_desde::text, 'ventana_hasta', m.ventana_hasta::text,
           'historial_ads', NULL
       ) ORDER BY l.external_id, l.id)
FROM product p
JOIN listing l ON l.product_id = p.id AND l.platform = %s::platform
LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = l.platform
GROUP BY p.id, p.odoo_sku
ORDER BY p.odoo_sku, p.id
"""
_SQL_TIPOS = """
SELECT tipo_producto FROM keyword_biblioteca WHERE platform = %s::platform
UNION SELECT tipo_producto FROM negative_biblioteca WHERE platform = %s::platform
UNION SELECT tipo_producto FROM campana_grupo WHERE platform = %s::platform
ORDER BY tipo_producto
"""
_SQL_CONFIG_VIGENTE = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"


def error(codigo: int, mensaje: str, lote: str | None = None) -> HTTPException:
    return HTTPException(codigo, detail={"mensaje": mensaje, "lote": lote})


def argumentos(solicitud: dict) -> SimpleNamespace:
    datos = {k: v for k, v in solicitud.items() if k != "parametros"}
    productos = solicitud.get("productos")
    listings = solicitud.get("listing_ids")
    datos["productos"] = ",".join(str(pid) for pid in sorted(productos)) if productos else None
    datos["listing_ids"] = ",".join(str(lid) for lid in sorted(listings)) if listings else None
    objetivo = solicitud.get("objetivo")
    datos["target_acos"] = objetivo.get("acos_pct") if objetivo else None
    datos["origen_objetivo"] = objetivo.get("origen") if objetivo else None
    for rol, sufijo in SUFIJOS.items():
        for monto in ("budget", "bid"):
            datos[f"{monto}_{sufijo}"] = solicitud["parametros"][rol][monto]
        datos[f"fuente_bid_{sufijo}"] = solicitud["parametros"][rol].get("fuente_bid", "manual")
        datos[f"recomendaciones_{sufijo}"] = solicitud["parametros"][rol].get("recomendaciones", [])
    return SimpleNamespace(**datos)


def planificar(conn, solicitud: dict) -> fp.PlanGrupo | fp.PlanGrupoV2:
    conn.row_factory = tuple_row
    try:
        return fc._arma_plan(argumentos(solicitud), conn)
    except fc.Abortar as exc:
        if "fraccion" in str(exc):
            raise error(503, "La configuración de margen no está disponible.") from None
        raise error(422, scrub(str(exc))) from None
    except (ValueError, ArithmeticError):
        raise error(503, "La configuración del plan no es válida.") from None
    except psycopg.Error:
        raise error(503, "No se pudo consultar la información del plan.") from None


def _huella_plan(plan: fp.PlanGrupo | fp.PlanGrupoV2) -> str:
    if isinstance(plan, fp.PlanGrupoV2):
        return fp.huella_plan_v2(plan)
    return fp.huella_plan(plan)


def _plan_como_json(plan: fp.PlanGrupo | fp.PlanGrupoV2) -> dict:
    if isinstance(plan, fp.PlanGrupoV2):
        return fp.plan_v2_como_json(plan)
    return fp.plan_como_json(plan)


def _bids_como_json(plan: fp.PlanGrupo | fp.PlanGrupoV2) -> dict[str, list[dict]]:
    """Detalle auditable de cada objetivo Amazon y del bid que se enviaria."""
    return {
        rol: [
            {
                "tipo": recomendacion.expresion.tipo,
                "valor": recomendacion.expresion.valor,
                "minimo": str(recomendacion.minimo),
                "sugerido": str(recomendacion.sugerido),
                "maximo": str(recomendacion.maximo),
                "bid_efectivo": str(
                    plan.parametros[rol].bid
                    if rol == "auto_discovery"
                    else br.bid_objetivo(recomendacion, plan.moneda)
                ),
            }
            for recomendacion in plan.parametros[rol].recomendaciones
        ]
        for rol in fp.ROLES_ORDEN_CREACION
    }


def previsualizar(conn, solicitud: dict) -> dict:
    plan = planificar(conn, solicitud)
    huella = _huella_plan(plan)
    return {
        "huella": huella,
        "lote": f"web-{huella}",
        "plan": _plan_como_json(plan),
        "bids": _bids_como_json(plan),
        "campanas": [
            {
                "rol": rol,
                "nombre": fp.nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha),
                "budget": str(plan.parametros[rol].budget),
                "bid": str(plan.parametros[rol].bid),
                "fuente_bid": plan.parametros[rol].fuente_bid,
            }
            for rol in fp.ROLES_ORDEN_CREACION
        ],
        "presupuesto_diario_total": str(
            sum((p.budget for p in plan.parametros.values()), Decimal("0"))
        ),
        "existentes": list(plan.existentes),
    }


def sugerir_bids(conn, solicitud: dict) -> dict:
    """Obtiene bids iniciales de Amazon para el conjunto que se prepara."""
    args = SimpleNamespace(
        plataforma=solicitud["plataforma"],
        listing_ids=",".join(str(i) for i in sorted(solicitud["listing_ids"])),
        origen_objetivo=solicitud["objetivo"]["origen"],
        target_acos=solicitud["objetivo"].get("acos_pct"),
    )
    try:
        tipo = fp.valida_tipo_producto(solicitud["tipo_producto"])
        publicaciones, _, semillas, _ = fc._datos_plan_v2(args, conn, tipo)
        conn.commit()
        credenciales = AdsCredentials.from_secrets_dir()
        cliente = AdsClient(credenciales)
        perfiles = fc._perfiles(cliente)
        profile_id = perfiles.get(solicitud["plataforma"])
        if profile_id is None:
            raise br.RecomendacionIncompleta("sin perfil Amazon aceptado para la plataforma")
        expresiones = {rol: fp.expresiones_bid(semillas, rol) for rol in fp.ROLES_ORDEN_CREACION}
        resultados = br.consultar_roles(
            cliente,
            profile_id=profile_id,
            moneda=fp.MONEDA_POR_PLATAFORMA[solicitud["plataforma"]],
            asins=tuple(publicacion.asin for publicacion in publicaciones),
            expresiones_por_rol=expresiones,
        )
    except fc.Abortar as exc:
        raise error(422, scrub(str(exc))) from None
    except fp.PlanInvalido as exc:
        raise error(422, scrub(str(exc))) from None
    except (br.RecomendacionIncompleta, AdsApiError, AdsConfigError, ValueError):
        raise error(
            503,
            "Amazon no entregó sugerencias completas. No se asignó ninguna puja.",
        ) from None
    return {
        "fuente": "amazon_v4",
        "roles": {
            rol: {
                "disponible": resultado.bid is not None,
                "bid": str(resultado.bid) if resultado.bid is not None else None,
                "recomendaciones": [
                    {
                        "tipo": recomendacion.expresion.tipo,
                        "valor": recomendacion.expresion.valor,
                        "minimo": str(recomendacion.minimo),
                        "sugerido": str(recomendacion.sugerido),
                        "maximo": str(recomendacion.maximo),
                        "bid_efectivo": (
                            None
                            if resultado.bid is None
                            else str(resultado.bid)
                            if rol == "auto_discovery"
                            else str(
                                br.bid_objetivo(
                                    recomendacion,
                                    fp.MONEDA_POR_PLATAFORMA[solicitud["plataforma"]],
                                )
                            )
                        ),
                    }
                    for recomendacion in resultado.recomendaciones
                ],
                "faltantes": [
                    {"tipo": expresion.tipo, "valor": expresion.valor}
                    for expresion in resultado.faltantes
                ],
            }
            for rol, resultado in resultados.items()
        },
    }


def _creacion_v2_habilitada(conn) -> bool:
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    settings = fila[0] if fila is not None else {}
    return fp.version_creacion_desde_settings(settings or {}) == "v2"


def catalogo(conn, plataforma: str, as_of: dt.datetime | None = None) -> dict:
    conn.row_factory = tuple_row
    productos = []
    for pid, sku, nombre, publicaciones in conn.execute(_SQL_CATALOGO, (plataforma,)).fetchall():
        dominio = {"amazon_mx": "www.amazon.com.mx", "amazon_us": "www.amazon.com"}[plataforma]
        for publicacion in publicaciones:
            asin = publicacion["asin"]
            sku_amazon = publicacion["seller_sku"]
            margen = publicacion["margen_neto_pct"]
            margen_valor = Decimal(margen) if margen is not None else None
            motivos = []
            asin_valido = bool(fp.PATRON_ASIN.fullmatch(asin or ""))
            if not asin:
                motivos.append("ASIN ausente.")
            elif not asin_valido:
                motivos.append("ASIN invalido.")
            if not sku_amazon or not sku_amazon.strip():
                motivos.append("SKU de Amazon ausente.")
            if margen_valor is None:
                motivos.append("Margen sin medir.")
            elif margen_valor == 0:
                motivos.append("Margen cero.")
            elif margen_valor < 0:
                motivos.append("Margen negativo.")
            publicacion["elegible"] = bool(asin_valido and sku_amazon and sku_amazon.strip())
            publicacion["motivos"] = motivos
            publicacion["url"] = (
                f"https://{dominio}/dp/{asin}"
                if re.fullmatch(r"[A-Za-z0-9]{10}", asin or "")
                else None
            )
        productos.append(
            {
                "id": pid,
                "sku": sku,
                "nombre": nombre,
                "publicaciones": publicaciones,
            }
        )
    corte = as_of if as_of is not None else dt.datetime.now(dt.UTC)
    if corte.tzinfo is None:
        raise error(422, "as_of debe incluir zona horaria.")
    corte = corte.astimezone(dt.UTC)
    listing_ids = [pub["id"] for prod in productos for pub in prod["publicaciones"]]
    por_listing = adjuntar_estimaciones(conn, listing_ids, as_of=corte)
    for prod in productos:
        for pub in prod["publicaciones"]:
            pub["estimacion"] = por_listing[pub["id"]]
    tipos = [
        fila[0]
        for fila in conn.execute(_SQL_TIPOS, (plataforma, plataforma, plataforma)).fetchall()
    ]
    return {
        "plataforma": plataforma,
        "moneda": fp.MONEDA_POR_PLATAFORMA[plataforma],
        "as_of": corte.isoformat(),
        "productos": productos,
        "tipos_producto": tipos,
    }


# ---------------------------------------------------------------------------
# Evaluacion del catalogo (ORBIT 19 B.4): adaptador DB -> modulo puro
# app/evaluacion_catalogo.py. Aqui SOLO consultas; la logica de etiquetas y
# ratios vive en el modulo puro (sin IO).
# ---------------------------------------------------------------------------

# Colapso a la observacion mas reciente por (asin, sku, metric_date): la tabla
# es append-only y el cron D-31..D-1 re-observa cada fecha ~31 veces; sin este
# colapso, sumar doblaria gasto/ventas (hallazgo bloqueante B.R).
_SQL_ADS_VENTANA = """
SELECT DISTINCT ON (advertised_asin, advertised_sku, metric_date)
       advertised_asin, advertised_sku, metric_date, observed_at, clicks, cost,
       purchases30d, sales30d, attributed_sales_same_sku_30d
FROM ads_product_metric_observation
WHERE platform = %s AND metric_date BETWEEN %s AND %s
ORDER BY advertised_asin, advertised_sku, metric_date, observed_at DESC
"""

_SQL_OBJETIVOS_GRUPO = """
SELECT p.listing_id, g.target_acos_pct
FROM campana_grupo g
JOIN campana_grupo_producto p ON p.grupo_id = g.id
JOIN fabrica_lote l ON l.lote = g.lote
WHERE g.platform = %s AND l.estado = 'planeado'
"""


def _fecha_o_texto(valor):
    return valor.isoformat() if valor is not None else None


def _monto_o_texto(valor: Decimal | None) -> str | None:
    return str(valor) if valor is not None else None


def _serializar_evaluacion(e: ec.EvaluacionListing) -> dict:
    econ = e.economia
    ads = e.ads
    return {
        "listing_id": e.listing_id,
        "platform": e.platform,
        "product_id": e.product_id,
        "asin": e.asin,
        "seller_sku": e.seller_sku,
        "seleccionable": e.seleccionable,
        "motivos": list(e.motivos),
        "objetivo_acos_pct": _monto_o_texto(e.objetivo_acos_pct),
        "economia": {
            "ventana_desde": _fecha_o_texto(econ.ventana_desde),
            "ventana_hasta": _fecha_o_texto(econ.ventana_hasta),
            "moneda": econ.moneda,
            "venta_total": _monto_o_texto(econ.venta_total),
            "venta_cubierta": _monto_o_texto(econ.venta_cubierta),
            "cobertura": _monto_o_texto(econ.cobertura),
            "dias_con_venta": econ.dias_con_venta,
            "margen_neto_pct": _monto_o_texto(econ.margen_neto_pct),
            "integridad_ok": econ.integridad_ok,
            "muestra_limitada": econ.muestra_limitada,
            "muestra_venta": _monto_o_texto(econ.muestra_venta),
            "muestra_margen_neto_pct": _monto_o_texto(econ.muestra_margen_neto_pct),
            "ledger_fresco_at": _fecha_o_texto(econ.ledger_fresco_at),
        },
        "ads": {
            "etiqueta": ads.etiqueta,
            "maduro": ads.maduro,
            "provisional": ads.provisional,
            "muestra": ads.muestra,
            "moneda": ads.moneda,
            "cost": _monto_o_texto(ads.cost),
            "clicks": int(ads.clicks) if ads.clicks is not None else None,
            "sales30d": _monto_o_texto(ads.sales30d),
            "purchases30d": _monto_o_texto(ads.purchases30d),
            "promoted30d": _monto_o_texto(ads.promoted30d),
            "halo30d": _monto_o_texto(ads.halo30d),
            "acos_pct": _monto_o_texto(ads.acos_pct),
            "cpc": _monto_o_texto(ads.cpc),
            "cvr_pct": _monto_o_texto(ads.cvr_pct),
        },
        "disponibilidad": e.disponibilidad,
    }


def evaluacion(
    conn,
    plataforma: str,
    orden: str = "margen_observado",
    direccion: str = "desc",
    objetivo: Decimal | None = None,
    as_of: dt.datetime | None = None,
):
    """Evaluacion completa por listing (B.2 + Ads B.1 + B.3 + objetivo D2).

    Ventana Ads igual al cron (0.4 §2): max 31 dias, D-31..D-1 UTC.
    El objetivo por listing viene de grupos en preparacion (lote 'planeado'
    con target_origen manual_lanzamiento o margen_medido, CHECK de 0019);
    targets distintos entre grupos => sin objetivo, jamas promedio (D2/§3).
    `objetivo` explicito (el del formulario, grupo AUN sin crear) toma
    precedencia sobre la consulta de grupos: es el grupo que el dueno esta
    preparando (hallazgo cross-review codex 2026-09-07).
    `as_of` alinea SOLO el corte de estimacion con catalogo. La ventana Ads
    sigue el reloj UTC vigente (cron D-31..D-1); no se desplaza con as_of.
    """
    if orden not in ec.METRICAS_ORDEN:
        raise error(422, "El criterio de orden no es válido.")
    if direccion not in ("asc", "desc"):
        raise error(422, "La dirección del orden no es válida.")
    conn.row_factory = tuple_row
    # Reloj Ads: siempre pared UTC. Independiente de as_of (AC10).
    hoy = dt.datetime.now(dt.UTC).date()
    hasta = hoy - dt.timedelta(days=1)
    desde = hasta - dt.timedelta(days=30)
    corte = as_of if as_of is not None else dt.datetime.now(dt.UTC)
    if corte.tzinfo is None:
        raise error(422, "as_of debe incluir zona horaria.")
    corte = corte.astimezone(dt.UTC)
    # Mismo formato que el NUMERIC(5,2) de campana_grupo: "10" -> "10.00".
    objetivo = objetivo.quantize(Decimal("0.01")) if objetivo is not None else None

    try:
        listings = conn.execute(
            "SELECT id, product_id, external_id, seller_sku FROM listing"
            " WHERE platform = %s::platform ORDER BY id",
            (plataforma,),
        ).fetchall()
        filas_ads = conn.execute(_SQL_ADS_VENTANA, (plataforma, desde, hasta)).fetchall()
        objetivos: dict[int, list[Decimal]] = {}
        for listing_id, target in conn.execute(_SQL_OBJETIVOS_GRUPO, (plataforma,)).fetchall():
            objetivos.setdefault(listing_id, []).append(target)
    except psycopg.Error:
        raise error(503, "No se pudo consultar la evaluación del catálogo.") from None

    # Grano de comparacion (0.4 §2): filas sumadas por (asin, sku); las filas
    # del mismo ASIN en varias campanas se SUMAN, jamas se reparten.
    por_clave: dict[tuple[str | None, str | None], list[ec.ObservacionAds]] = {}
    for asin, sku, metric_date, observed_at, clicks, cost, p30, s30, prom in filas_ads:
        por_clave.setdefault((asin, sku), []).append(
            ec.ObservacionAds(
                metric_date=metric_date,
                observed_at=observed_at,
                clicks=clicks,
                cost=cost,
                purchases30d=p30,
                sales30d=s30,
                attributed_sales_same_sku30d=prom,
            )
        )

    economia = economia_observada.por_listing(conn, plataforma)
    evaluaciones = []
    for listing_id, product_id, asin, seller_sku in listings:
        try:
            disponibilidad = (
                estado_disponibilidad(conn, plataforma, seller_sku) if seller_sku else None
            )
        except psycopg.Error:
            raise error(503, "No se pudo consultar la evaluación del catálogo.") from None
        evaluaciones.append(
            ec.evaluar_listing(
                listing_id=listing_id,
                platform=plataforma,
                product_id=product_id,
                asin=asin,
                seller_sku=seller_sku,
                filas_ads=por_clave.get((asin, seller_sku), []),
                ventana=(desde, hasta),
                economia=economia.get(
                    listing_id, economia_observada.EconomiaProducto.vacio(plataforma, product_id)
                ),
                disponibilidad=disponibilidad
                if disponibilidad is not None
                else {"estado": "desconocido"},
                objetivos_grupos=(objetivo,)
                if objetivo is not None
                else objetivos.get(listing_id, ()),
                # Moneda de Ads = la del PERFIL (amazon_us -> USD), no la de la
                # economia: para US el ledger puede venir en MXN y las cifras
                # Ads son USD (hallazgo cross-review codex 2026-09-07). El mapa
                # de esta capa es fp.MONEDA_POR_PLATAFORMA (alias sellado del
                # del motor); app.ads.write esta fuera por el guard de imports.
                moneda_ads=fp.MONEDA_POR_PLATAFORMA[plataforma],
            )
        )
    ordenadas = ec.ordenar(evaluaciones, orden, descendente=direccion == "desc")
    publicaciones = [_serializar_evaluacion(e) for e in ordenadas]
    listing_ids = [p["listing_id"] for p in publicaciones]
    por_listing = adjuntar_estimaciones(conn, listing_ids, as_of=corte)
    for p in publicaciones:
        p["estimacion"] = por_listing[p["listing_id"]]
    return {
        "plataforma": plataforma,
        "ventana_ads": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "orden": orden,
        "direccion": direccion,
        "as_of": corte.isoformat(),
        "publicaciones": publicaciones,
    }


def _fila_lote(fila) -> dict:
    datos = dict(
        zip(
            ("lote", "plataforma", "estado", "detalle", "created_at", "finished_at", "plan"),
            fila[:7],
            strict=True,
        )
    )
    # El CLI puede guardar respuestas externas dentro del texto de error.
    # El cliente recibe resumen operativo y pasos, nunca ese texto ni ACKs.
    if datos["estado"] in ("planeado", "failed"):
        if fila[8]:
            datos["detalle"] = (
                "Resultado incierto: Amazon pudo crear recursos sin devolver su identificador. "
                "No repetir la creación; revisa la consola de Amazon por nombre. "
                "La pausa solo alcanza campañas con identificador conocido."
            )
        elif fila[7] == 0:
            datos["detalle"] = (
                "No se registraron pasos. Revisa la configuración y el estado del lote "
                "antes de iniciar una nueva creación."
            )
        else:
            datos["detalle"] = (
                "La operación está pendiente o se detuvo. Revisa los pasos; puedes reconciliar, "
                "completar el registro o pausar las campañas conocidas."
            )
    elif datos["detalle"] is not None:
        datos["detalle"] = {
            "desarmado": "Las campañas conocidas del lote están pausadas.",
            "applied": "El grupo quedó registrado.",
        }[datos["estado"]]
    return datos


def detalle_lote(conn, lote: str) -> dict | None:
    conn.row_factory = tuple_row
    fila = conn.execute(_SQL_LOTE, (lote,)).fetchone()
    if fila is None:
        return None
    datos = _fila_lote(fila)
    if datos["plan"]:
        plan = (
            fp.plan_v2_desde_json(datos["plan"])
            if datos["plan"].get("schema_version") == 2
            else fp.plan_desde_json(datos["plan"])
        )
        datos["bids"] = _bids_como_json(plan)
    datos["pasos"] = [
        dict(
            zip(
                ("orden", "rol", "recurso", "external_id", "estado", "readback_estado"),
                paso,
                strict=True,
            )
        )
        for paso in conn.execute(
            "SELECT orden, rol::text, recurso, external_id, estado, readback_estado "
            "FROM fabrica_lote_paso WHERE lote = %s ORDER BY orden",
            (lote,),
        ).fetchall()
    ]
    return datos


def lotes(conn, plataforma: str) -> dict:
    conn.row_factory = tuple_row
    filas = conn.execute(
        _SQL_RESUMEN_LOTE + " WHERE l.platform = %s::platform "
        "ORDER BY l.created_at DESC, l.lote DESC LIMIT 50",
        (plataforma,),
    ).fetchall()
    return {"items": [_fila_lote(fila) for fila in filas]}


@contextmanager
def _conexion(nombre: str, lote: str):
    dsn = os.environ.get(nombre)
    if not dsn:
        raise error(503, "La conexión necesaria no está configurada.", lote)
    try:
        conn = connect(dsn, autocommit=True, row_factory=tuple_row)
    except OrbitDbError:
        raise error(503, "No se pudo conectar a la base de datos.", lote) from None
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _lote_bloqueado(lote: str):
    # Lock de SESION: sobrevive los commits del motor y cubre sus otras conexiones.
    # La conexion dedicada se cierra en toda salida; PostgreSQL libera el lock.
    clave = int.from_bytes(hashlib.sha256(f"fabrica-web:{lote}".encode()).digest()[:8], signed=True)
    with _conexion("ORBIT_DSN_ADMIN", lote) as conn:
        adquirido = conn.execute("SELECT pg_try_advisory_lock(%s)", (clave,)).fetchone()[0]
        if not adquirido:
            raise error(409, "Hay otra operación en curso para este lote.", lote)
        yield conn


def _fallo_operacion(exc: Exception, lote: str) -> HTTPException:
    configuracion = isinstance(exc, fc.Abortar) and (
        str(exc).startswith(("ORBIT_DSN_", "sin perfil aceptado", "LWA "))
    )
    if configuracion or isinstance(
        exc, (OSError, OrbitDbError, psycopg.Error, httpx.HTTPError, ValueError)
    ):
        return error(
            503, "No se pudo completar la operación. Consulta el lote antes de continuar.", lote
        )
    return error(
        409, "La operación se detuvo. Consulta el lote y sus pasos para recuperarlo.", lote
    )


def crear(solicitud: dict, huella: str, confirmacion: str) -> dict:
    lote = f"web-{huella}"
    with _lote_bloqueado(lote) as conn:
        existente = detalle_lote(conn, lote)
        if existente is not None:
            return existente
        with _conexion("ORBIT_DSN_READ", lote) as lectura:
            if solicitud.get("listing_ids") is not None and not _creacion_v2_habilitada(lectura):
                raise error(
                    409,
                    "Altas por publicacion deshabilitadas; conserva el lote para recuperacion.",
                    lote,
                )
            plan = planificar(lectura, solicitud)
        if _huella_plan(plan) != huella:
            raise error(409, "El plan cambió. Vuelve a previsualizar antes de crear.", lote)
        args = argumentos(solicitud)
        args.acepto_mutacion_real = True
        args.esperado = 5
        args.huella = huella
        args.go = confirmacion
        try:
            fc._mutar(args, plan, huella, lote=lote)
        except Exception as exc:
            # Incluso si fallo antes del INSERT del motor, se conserva el intento.
            # Un retry de la misma huella consulta este lote y nunca vuelve a crear.
            if detalle_lote(conn, lote) is None:
                fc._inserta_lote(conn, lote, plan, huella, confirmacion)
                fc._sella_lote(conn, lote, "failed", "Operación detenida antes de crear pasos.")
            raise _fallo_operacion(exc, lote) from None
        resultado = detalle_lote(conn, lote)
        if resultado is None:
            raise error(503, "No se encontró el resultado durable de la operación.", lote)
        return resultado


def operar(lote: str, accion: str, confirmacion: str) -> dict:
    with _lote_bloqueado(lote) as conn:
        actual = detalle_lote(conn, lote)
        if actual is None:
            raise error(404, "El lote no existe.", lote)
        if actual["estado"] == "desarmado":
            if accion == "pausar":
                return actual
            raise error(409, "El lote está pausado; no puede reactivarse con esta operación.", lote)
        if accion == "registrar" and actual["estado"] == "applied":
            return actual
        args = SimpleNamespace(
            plataforma=actual["plataforma"],
            lote=lote,
            desarmar=lote,
            registrar=lote,
            acepto_mutacion_real=True,
            go=confirmacion,
        )
        funcion = {
            "pausar": fc._desarmar,
            "reconciliar": fc._reconciliar_cmd,
            "registrar": fc._registrar_cmd,
        }[accion]
        try:
            funcion(args)
        except Exception as exc:
            raise _fallo_operacion(exc, lote) from None
        return detalle_lote(conn, lote)
