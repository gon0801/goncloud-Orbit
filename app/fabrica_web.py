"""Adaptador web: el motor CLI conserva la autoria de planes y operaciones."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import httpx
import psycopg
from fastapi import HTTPException
from psycopg.rows import tuple_row

from app import fabrica_plan as fp
from app.db import OrbitDbError, connect
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
SELECT p.id, p.odoo_sku, count(l.id), min(l.seller_sku), m.margen_neto_pct
FROM product p
JOIN listing l ON l.product_id = p.id AND l.platform = %s::platform
LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = l.platform
GROUP BY p.id, p.odoo_sku, m.margen_neto_pct
ORDER BY p.odoo_sku, p.id
"""
_SQL_TIPOS = """
SELECT tipo_producto FROM keyword_biblioteca WHERE platform = %s::platform
UNION SELECT tipo_producto FROM negative_biblioteca WHERE platform = %s::platform
UNION SELECT tipo_producto FROM campana_grupo WHERE platform = %s::platform
ORDER BY tipo_producto
"""


def error(codigo: int, mensaje: str, lote: str | None = None) -> HTTPException:
    return HTTPException(codigo, detail={"mensaje": mensaje, "lote": lote})


def argumentos(solicitud: dict) -> SimpleNamespace:
    datos = {k: v for k, v in solicitud.items() if k != "parametros"}
    datos["productos"] = ",".join(str(pid) for pid in sorted(solicitud["productos"]))
    for rol, sufijo in SUFIJOS.items():
        for monto in ("budget", "bid"):
            datos[f"{monto}_{sufijo}"] = solicitud["parametros"][rol][monto]
    return SimpleNamespace(**datos)


def planificar(conn, solicitud: dict) -> fp.PlanGrupo:
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


def previsualizar(conn, solicitud: dict) -> dict:
    plan = planificar(conn, solicitud)
    huella = fp.huella_plan(plan)
    return {
        "huella": huella,
        "lote": f"web-{huella}",
        "plan": fp.plan_como_json(plan),
        "campanas": [
            {
                "rol": rol,
                "nombre": fp.nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha),
                "budget": str(plan.parametros[rol].budget),
                "bid": str(plan.parametros[rol].bid),
            }
            for rol in fp.ROLES_ORDEN_CREACION
        ],
        "presupuesto_diario_total": str(
            sum((p.budget for p in plan.parametros.values()), Decimal("0"))
        ),
        "existentes": list(plan.existentes),
    }


def catalogo(conn, plataforma: str) -> dict:
    conn.row_factory = tuple_row
    productos = []
    for pid, sku, listings, seller_sku, margen in conn.execute(
        _SQL_CATALOGO, (plataforma,)
    ).fetchall():
        motivo = None
        if listings != 1:
            motivo = "Tiene varios listings; esta versión requiere uno por producto."
        elif margen is None:
            motivo = "Sin margen medible."
        elif not seller_sku or not seller_sku.strip():
            motivo = "Sin seller_sku para crear el anuncio."
        productos.append(
            {
                "id": pid,
                "sku": sku,
                "margen_neto_pct": str(margen) if margen is not None else None,
                "elegible": motivo is None,
                "motivo": motivo,
            }
        )
    tipos = [
        fila[0]
        for fila in conn.execute(_SQL_TIPOS, (plataforma, plataforma, plataforma)).fetchall()
    ]
    return {
        "plataforma": plataforma,
        "moneda": fp.MONEDA_POR_PLATAFORMA[plataforma],
        "productos": productos,
        "tipos_producto": tipos,
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
            plan = planificar(lectura, solicitud)
        if fp.huella_plan(plan) != huella:
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
