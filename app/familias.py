"""Familias de producto en dos niveles (A2).

Escritor unico de `familia` y `producto_familia`: la UI /familias escribe
solo por aqui (via POST /api/familias*). El motor leera el arbol desde A4.

Reglas (migracion 0047):
- Maximo dos niveles: familia -> subfamilia. El tope vive en el trigger
  `familia_dos_niveles` (un CHECK no ve otras filas).
- Un producto tiene UNA familia por plataforma (upsert idempotente).
- El slug es el tipo_producto de la fabrica (misma forma ^[a-z0-9_]+$).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

import psycopg

from app.optimizer.bid import PLATAFORMAS_MONEDA

PLATAFORMAS = frozenset(PLATAFORMAS_MONEDA)

PATRON_SLUG = re.compile(r"^[a-z0-9_]+$")

# A2: sin la 0048 ninguna familia mide margen propio; todas usan la meta
# del pais. A5 especializa esta funcion por familia medible.
ORIGEN_META_PAIS = "usa la meta del país"

VENTANA_VENTAS_DIAS = 90


class FamiliaNoExiste(LookupError):
    """La familia o el producto pedido no existe (API: 404)."""


class FamiliaDuplicada(ValueError):
    """Nombre o slug ya usado en la plataforma (API: 409)."""


class FamiliaInvalida(ValueError):
    """Tercer nivel, plataforma ajena o forma rota (API: 422)."""


def slug(nombre: str) -> str:
    """Slug determinista para tipo_producto: minusculas, sin acentos,
    lo ajeno a [a-z0-9] colapsa a _. Vacio o irreconocible = error."""
    if not isinstance(nombre, str):
        raise FamiliaInvalida("el nombre de la familia debe ser texto")
    base = unicodedata.normalize("NFKD", nombre.strip().lower())
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    if not base or not PATRON_SLUG.match(base):
        raise FamiliaInvalida(f"el nombre {nombre!r} no da un slug valido")
    return base


def _valida_plataforma(platform: str) -> str:
    if platform not in PLATAFORMAS:
        raise FamiliaInvalida(f"plataforma fuera de vocabulario: {platform!r}")
    return platform


def _valida_nombre(nombre: str) -> str:
    if not isinstance(nombre, str) or not nombre.strip():
        raise FamiliaInvalida("el nombre de la familia no puede ser vacio")
    limpio = nombre.strip()
    if len(limpio) > 100 or any(ord(c) < 32 for c in limpio):
        raise FamiliaInvalida("nombre de familia invalido")
    return limpio


_SQL_FAMILIA = "SELECT id, platform, nombre, slug, padre_id FROM familia WHERE id = %s"


def _lee_familia(conn, familia_id: int) -> tuple:
    fila = conn.execute(_SQL_FAMILIA, (familia_id,)).fetchone()
    if fila is None:
        raise FamiliaNoExiste(f"la familia {familia_id} no existe")
    return fila


def crea(conn, platform: str, nombre: str, padre_id: int | None = None) -> dict:
    """Crea una familia (padre NULL) o subfamilia. El trigger 0047 es el
    candado final del tope de dos niveles; aqui se valida para dar el
    error cerrado sin depender del mensaje de Postgres."""
    _valida_plataforma(platform)
    limpio = _valida_nombre(nombre)
    if padre_id is not None:
        try:
            padre_id = int(padre_id)
        except (TypeError, ValueError):
            raise FamiliaInvalida("padre_id debe ser entero") from None
        padre = _lee_familia(conn, padre_id)
        if padre[1] != platform:
            raise FamiliaInvalida("la subfamilia debe ser de la plataforma del padre")
        if padre[4] is not None:
            raise FamiliaInvalida("tercer nivel rechazado: el padre ya es subfamilia")
    try:
        fila = conn.execute(
            "INSERT INTO familia(platform, nombre, slug, padre_id)"
            " VALUES (%s, %s, %s, %s)"
            " RETURNING id, platform, nombre, slug, padre_id",
            (platform, limpio, slug(limpio), padre_id),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise FamiliaDuplicada(f"la familia {limpio!r} ya existe en {platform}") from exc
    return {
        "id": fila[0],
        "platform": fila[1],
        "nombre": fila[2],
        "slug": fila[3],
        "padre_id": fila[4],
    }


_SQL_ASIGNA = """
INSERT INTO producto_familia(product_id, platform, familia_id)
VALUES (%s, %s::platform, %s)
ON CONFLICT (product_id, platform) DO UPDATE
  SET familia_id = EXCLUDED.familia_id, asignada_at = now()
RETURNING product_id
"""


def asigna(conn, product_ids: list[int], familia_id: int) -> dict:
    """Etiqueta productos con una familia. Idempotente: correr dos veces
    deja las mismas filas (una por producto y plataforma)."""
    familia = _lee_familia(conn, familia_id)
    platform = familia[1]
    ids = sorted({int(pid) for pid in product_ids})
    if not ids:
        return {"familia_id": familia_id, "asignados": 0}
    existentes = {
        fila[0]
        for fila in conn.execute("SELECT id FROM product WHERE id = ANY(%s)", (ids,)).fetchall()
    }
    faltan = [pid for pid in ids if pid not in existentes]
    if faltan:
        raise FamiliaNoExiste(f"productos inexistentes: {faltan}")
    for pid in ids:
        conn.execute(_SQL_ASIGNA, (pid, platform, familia_id))
    return {"familia_id": familia_id, "asignados": len(ids)}


_SQL_ARBOL = """
SELECT f.id, f.nombre, f.slug, f.padre_id, COUNT(pf.product_id) AS productos,
       (SELECT COALESCE(SUM(l.quantity), 0)
          FROM producto_familia pf2
          JOIN ledger_event l ON l.product_id = pf2.product_id
           AND l.platform = pf2.platform
         WHERE pf2.familia_id = f.id
           AND l.kind = 'sale'
           AND l.event_date >= %s::date - %s
           AND l.event_date < %s::date) AS ventas_90d
  FROM familia f
  LEFT JOIN producto_familia pf ON pf.familia_id = f.id
 WHERE f.platform = %s::platform
 GROUP BY f.id
 ORDER BY f.nombre
"""


def arbol(conn, platform: str, hoy: dt.date | None = None) -> list[dict]:
    """Arbol de la plataforma: familias con sus hijas, conteo de productos
    (etiqueta directa, sin heredar), ventas 90d y origen de meta."""
    _valida_plataforma(platform)
    if hoy is None:
        hoy = dt.datetime.now(dt.UTC).date()
    nodos = {}
    for fid, nombre, fslug, padre_id, productos, ventas in conn.execute(
        _SQL_ARBOL, (hoy, VENTANA_VENTAS_DIAS, hoy, platform)
    ):
        nodos[fid] = {
            "id": fid,
            "nombre": nombre,
            "slug": fslug,
            "padre_id": padre_id,
            "productos": int(productos),
            "ventas_90d": int(ventas),
            "origen_meta": ORIGEN_META_PAIS,
            "hijas": [],
        }
    raices = []
    for nodo in nodos.values():
        padre = nodos.get(nodo["padre_id"]) if nodo["padre_id"] else None
        if padre is None:
            raices.append(nodo)
        else:
            padre["hijas"].append(nodo)
    return raices


_SQL_FAMILIA_PRODUCTOS = """
SELECT pf.product_id, f.id, f.nombre, f.slug
  FROM producto_familia pf
  JOIN familia f ON f.id = pf.familia_id
 WHERE pf.platform = %s::platform AND pf.product_id = ANY(%s)
"""


def familia_de_productos(conn, platform: str, product_ids: list[int]) -> dict[int, dict]:
    """Familia de cada producto etiquetado. Sin etiqueta = ausente
    (regla 3: el hueco no se inventa). Una sola query."""
    _valida_plataforma(platform)
    ids = sorted({int(pid) for pid in product_ids})
    if not ids:
        return {}
    return {
        pid: {"id": fid, "nombre": nombre, "slug": fslug}
        for pid, fid, nombre, fslug in conn.execute(
            _SQL_FAMILIA_PRODUCTOS, (platform, ids)
        ).fetchall()
    }


_SQL_PRODUCTOS = """
SELECT p.id, p.odoo_sku, p.name,
       f.id, f.nombre, f.slug,
       (SELECT COALESCE(SUM(l.quantity), 0)
          FROM ledger_event l
         WHERE l.product_id = p.id
           AND l.platform = %s::platform
           AND l.kind = 'sale'
           AND l.event_date >= %s::date - %s
           AND l.event_date < %s::date) AS ventas_90d
  FROM product p
  JOIN listing li ON li.product_id = p.id AND li.platform = %s::platform
  LEFT JOIN producto_familia pf
    ON pf.product_id = p.id AND pf.platform = %s::platform
  LEFT JOIN familia f ON f.id = pf.familia_id
 WHERE (%s::text IS NULL OR p.name ILIKE '%%' || %s || '%%'
        OR p.odoo_sku ILIKE '%%' || %s || '%%')
   AND (%s::boolean IS NOT TRUE OR pf.product_id IS NULL)
 GROUP BY p.id, f.id
 ORDER BY p.name, p.id
"""


def productos(
    conn,
    platform: str,
    q: str | None = None,
    sin_familia: bool = False,
    hoy: dt.date | None = None,
) -> list[dict]:
    """Productos con publicacion en la plataforma, con su etiqueta y sus
    ventas 90d del ledger (unidades, [hoy-90, hoy)). Una sola query."""
    _valida_plataforma(platform)
    if hoy is None:
        hoy = dt.datetime.now(dt.UTC).date()
    texto = (q or "").strip() or None
    filas = conn.execute(
        _SQL_PRODUCTOS,
        (
            platform,
            hoy,
            VENTANA_VENTAS_DIAS,
            hoy,
            platform,
            platform,
            texto,
            texto,
            texto,
            sin_familia,
        ),
    ).fetchall()
    return [
        {
            "id": pid,
            "odoo_sku": odoo_sku,
            "name": name,
            "familia": ({"id": fid, "nombre": nombre, "slug": fslug} if fid is not None else None),
            "ventas_90d": int(ventas),
        }
        for pid, odoo_sku, name, fid, nombre, fslug, ventas in filas
    ]


def origen_meta_familia(conn, platform: str, familia_id: int) -> str:
    """Origen del target de una familia. A2: siempre la meta del pais
    (la 0048/A5 medira margen por familia). Existe para pinchar la
    costura que A5 especializa."""
    _valida_plataforma(platform)
    _lee_familia(conn, familia_id)
    return ORIGEN_META_PAIS
