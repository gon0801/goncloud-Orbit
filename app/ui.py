"""UI server-rendered del dashboard (ORBIT 16 - DASHBOARD 01, task 1.6).

Cuatro pantallas Jinja2 (brief §2): Resumen (series spend/revenue/ACoS por
plataforma), Campanas (tabla 30d con target efectivo y procedencia),
Decisiones (feed por cursor) y Salud (snapshot + historico 14d + skips).

Sellos (plan 1.6 + brief §6):

- Server-rendered con Jinja2 PINNEADA (pyproject + uv.lock commiteado).
- AUTOESCAPE VERIFICADO (no asumido): el search_term (texto libre del
  comprador, el vector XSS real) va por {{ }} y el entorno de Jinja2Templates
  escapa; el test demuestra el caso sin autoescape (regla 9).
- Los datos hacia JS pasan EXCLUSIVAMENTE por |tojson (decision 12) en un
  bloque <script type="application/json">; el cliente los parsea con
  JSON.parse y el dinero string con Number() (decision 7: el backend jamas
  emite floats de dinero).
- Lib de graficas VENDOREADA (Chart.js 4.4.9 en /static/vendor, brief §6):
  cero CDN y cero hosts externos en el HTML.
- Headers CSP default-src 'self' y Cache-Control: no-store en el HTML: los
  agrega el middleware de app/main.py (consistente para toda la superficie
  HTML).
- Un camino (regla 2): esta capa CONSUME los endpoints de app/api_dashboard
  (la misma conexion de lectura ConexionLectura), jamas reimplementa queries.

DETERMINISMO: las paginas delegan en los endpoints (que ya usan `_hoy_utc`).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import api_dashboard as dash
from app.api import ConexionLectura
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.goals import PELDANOS_CASCADA

router = APIRouter(prefix="", tags=["dashboard-ui"])

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
# Jinja2Templates de Starlette crea el Environment con autoescape=True:
# el search_term del comprador nunca se inyecta crudo en el HTML (los tests
# de 1.6 lo VERIFICAN, no lo asumen — regla 9).
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Columnas que YA estan en campanas.html. No se inventan orders/impressions.
COLUMNAS_ORDEN = (
    "nombre",
    "estado",
    "plataforma",
    "cost",
    "revenue",
    "clicks",
    "acos",
    "target",
    "procedencia",
)
COLUMNAS_NUMERICAS = frozenset({"cost", "revenue", "clicks", "acos", "target"})
ESTADOS_CAMPANA = ("ENABLED", "PAUSED", "ARCHIVED")


def _limpia_query(valor: str | None) -> str | None:
    """Query vacia o solo espacios = ausente (el form GET manda "")."""
    if valor is None:
        return None
    limpio = valor.strip()
    return limpio or None


def _vocab_o_422(valor: str | None, permitidos: frozenset[str], nombre: str) -> str | None:
    """Vocabulario cerrado: valor ajeno -> 422 (mismo patron que el feed)."""
    limpio = _limpia_query(valor)
    if limpio is None:
        return None
    if limpio not in permitidos:
        raise HTTPException(status_code=422, detail=f"{nombre} fuera de vocabulario")
    return limpio


def _decimal_o_none(valor: str | None) -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(valor)
    except (InvalidOperation, ValueError):
        return None


def _valor_orden(item: dict, columna: str):
    """Valor TIPEADO para sort numerico (9 < 80). Hueco = None (regla 3)."""
    metricas = item.get("metricas_30d") or {}
    if columna == "nombre":
        return item.get("nombre")
    if columna == "estado":
        return item.get("status")
    if columna == "plataforma":
        return item.get("plataforma")
    if columna == "cost":
        return _decimal_o_none(metricas.get("cost"))
    if columna == "revenue":
        return _decimal_o_none(metricas.get("ad_revenue"))
    if columna == "clicks":
        return metricas.get("clicks")
    if columna == "acos":
        return _decimal_o_none(metricas.get("acos"))
    if columna == "target":
        return _decimal_o_none((item.get("target_efectivo") or {}).get("valor"))
    if columna == "procedencia":
        return (item.get("target_efectivo") or {}).get("peldano")
    return None


def _texto_filtro(item: dict, columna: str) -> str:
    """Texto de la celda para filtrar (lo que el dueno ve, no un id interno)."""
    metricas = item.get("metricas_30d") or {}
    if columna == "nombre":
        return item.get("nombre") or ""
    if columna == "clicks":
        clicks = metricas.get("clicks")
        return "" if clicks is None else str(clicks)
    if columna == "acos":
        if metricas.get("sin_ventas"):
            return "sin ventas"
        return metricas.get("acos") or ""
    if columna == "cost":
        return metricas.get("cost") or ""
    if columna == "revenue":
        return metricas.get("ad_revenue") or ""
    if columna == "target":
        valor = (item.get("target_efectivo") or {}).get("valor")
        return "" if valor is None else str(valor)
    if columna == "procedencia":
        return (item.get("target_efectivo") or {}).get("peldano") or ""
    return ""


def _pasa_filtro(item: dict, filtros: dict) -> bool:
    """AND de los filtros presentes. Plataforma/estado son igualdad exacta."""
    if filtros.get("plataforma") and item.get("plataforma") != filtros["plataforma"]:
        return False
    if filtros.get("estado") and item.get("status") != filtros["estado"]:
        return False
    pedido_clicks = filtros.get("clicks")
    if pedido_clicks:
        clicks = (item.get("metricas_30d") or {}).get("clicks")
        if clicks is None:
            return False
        if pedido_clicks.isdigit() or (
            pedido_clicks.startswith("-") and pedido_clicks[1:].isdigit()
        ):
            if clicks != int(pedido_clicks):
                return False
        elif pedido_clicks.casefold() not in str(clicks).casefold():
            return False
    for columna in ("nombre", "acos", "cost", "revenue", "target", "procedencia"):
        pedido = filtros.get(columna)
        if not pedido:
            continue
        if pedido.casefold() not in _texto_filtro(item, columna).casefold():
            return False
    return True


def filtra_y_ordena_campanas(
    items: list[dict],
    filtros: dict,
    ordenar: str | None,
    direccion: str | None,
) -> list[dict]:
    """Vista de la tabla: filtra en memoria y ordena. No reimplementa SQL."""
    visibles = [item for item in items if _pasa_filtro(item, filtros)]
    if ordenar is None:
        return visibles
    presentes = [item for item in visibles if _valor_orden(item, ordenar) is not None]
    ausentes = [item for item in visibles if _valor_orden(item, ordenar) is None]
    reverso = direccion == "desc"
    presentes.sort(key=lambda item: _valor_orden(item, ordenar), reverse=reverso)
    return presentes + ausentes


def _href_campanas(params: dict) -> str:
    pares = [(k, v) for k, v in params.items() if v not in (None, "")]
    if not pares:
        return "/campanas"
    return "/campanas?" + urlencode(pares)


def _params_vista(filtros: dict, ordenar: str | None, direccion: str | None) -> dict:
    return {
        "plataforma": filtros.get("plataforma"),
        "estado": filtros.get("estado"),
        "nombre": filtros.get("nombre"),
        "clicks": filtros.get("clicks"),
        "acos": filtros.get("acos"),
        "cost": filtros.get("cost"),
        "revenue": filtros.get("revenue"),
        "target": filtros.get("target"),
        "procedencia": filtros.get("procedencia"),
        "ordenar": ordenar,
        "dir": direccion,
    }


def vista_tabla_campanas(filtros: dict, ordenar: str | None, direccion: str | None) -> dict:
    """Hrefs de headers y el form GET (CSP: cero JS)."""
    base = _params_vista(filtros, ordenar, direccion)
    hrefs_orden = {}
    for col in COLUMNAS_ORDEN:
        if ordenar == col:
            nueva_dir = "asc" if direccion == "desc" else "desc"
        else:
            nueva_dir = "desc" if col in COLUMNAS_NUMERICAS else "asc"
        hrefs_orden[col] = _href_campanas({**base, "ordenar": col, "dir": nueva_dir})
    return {
        "filtros": filtros,
        "ordenar": ordenar,
        "dir": direccion,
        "hrefs_orden": hrefs_orden,
        "href_limpiar": "/campanas",
        "plataformas": tuple(PLATAFORMAS_MONEDA),
        "estados": ESTADOS_CAMPANA,
        "peldanos": PELDANOS_CASCADA,
        "activo": any(filtros.values()) or ordenar is not None,
    }


@router.get("/", response_class=HTMLResponse)
def resumen(request: Request, conn: ConexionLectura) -> HTMLResponse:
    """Resumen: series diarias spend/revenue/ACoS por plataforma (moneda
    nativa, grano campaign, D-30..D-1, inmaduros marcados, NULL = hueco)."""
    series = {
        plataforma: dash.serie_plataforma(conn=conn, platform=plataforma)
        for plataforma in PLATAFORMAS_MONEDA
    }
    return templates.TemplateResponse(
        request, "resumen.html", {"pantalla": "resumen", "series": series}
    )


@router.get("/campanas", response_class=HTMLResponse)
def pagina_campanas(
    request: Request,
    conn: ConexionLectura,
    plataforma: Annotated[str | None, Query()] = None,
    estado: Annotated[str | None, Query()] = None,
    ordenar: Annotated[str | None, Query()] = None,
    direccion: Annotated[str | None, Query(alias="dir")] = None,
    nombre: Annotated[str | None, Query()] = None,
    clicks: Annotated[str | None, Query()] = None,
    acos: Annotated[str | None, Query()] = None,
    cost: Annotated[str | None, Query()] = None,
    revenue: Annotated[str | None, Query()] = None,
    target: Annotated[str | None, Query()] = None,
    procedencia: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Campanas: tabla 30d con target efectivo CON procedencia (5 peldanos,
    goals.py 1.2) y estado del goal. Cada fila lleva su moneda (regla 4).
    Filtro y sort son query params del GET (mismo patron que ?cursor=): la
    CSP prohibe JS inline y esta pantalla no necesita un archivo nuevo."""
    plataforma = _vocab_o_422(plataforma, frozenset(PLATAFORMAS_MONEDA), "plataforma")
    estado = _vocab_o_422(estado, frozenset(ESTADOS_CAMPANA), "estado")
    ordenar = _vocab_o_422(ordenar, frozenset(COLUMNAS_ORDEN), "ordenar")
    direccion = _vocab_o_422(direccion, frozenset({"asc", "desc"}), "dir")
    procedencia = _vocab_o_422(procedencia, frozenset(PELDANOS_CASCADA), "procedencia")
    filtros = {
        "plataforma": plataforma,
        "estado": estado,
        "nombre": _limpia_query(nombre),
        "clicks": _limpia_query(clicks),
        "acos": _limpia_query(acos),
        "cost": _limpia_query(cost),
        "revenue": _limpia_query(revenue),
        "target": _limpia_query(target),
        "procedencia": procedencia,
    }
    datos = dash.campanas(conn=conn, platform=plataforma)
    items = filtra_y_ordena_campanas(datos["items"], filtros, ordenar, direccion)
    return templates.TemplateResponse(
        request,
        "campanas.html",
        {
            "pantalla": "campanas",
            "items": items,
            "vista": vista_tabla_campanas(filtros, ordenar, direccion),
        },
    )


@router.get("/decisiones", response_class=HTMLResponse)
def pagina_decisiones(
    request: Request,
    conn: ConexionLectura,
    cursor: Annotated[int | None, Query(ge=1)] = None,
) -> HTMLResponse:
    """Decisiones: feed por cursor con motivo en espanol; el search_term se
    renderiza ESCAPADO ({{ }}) — es el vector XSS real del dominio. El
    `?cursor=` del boton 'Cargar mas' se PROPAGA al feed (hallazgo alta de
    codex: ignorarlo recargaba la primera pagina por siempre)."""
    datos = dash.decisiones(conn=conn, cursor=cursor)
    return templates.TemplateResponse(
        request,
        "decisiones.html",
        {
            "pantalla": "decisiones",
            "items": datos["items"],
            "next_cursor": datos["next_cursor"],
            "has_more": datos["has_more"],
        },
    )


@router.get("/salud", response_class=HTMLResponse)
def pagina_salud(request: Request, conn: ConexionLectura) -> HTMLResponse:
    """Salud: snapshot del ultimo ciclo por plataforma + historico 14d +
    watermarks + skips traducidos."""
    datos = dash.salud(conn=conn)
    return templates.TemplateResponse(
        request, "salud.html", {"pantalla": "salud", "plataformas": datos["plataformas"]}
    )


@router.get("/contribucion", response_class=HTMLResponse)
def pagina_contribucion(request: Request, conn: ConexionLectura) -> HTMLResponse:
    """Contribucion pre-cargos por campana (ORBIT 06 1.4): rollup de hijas,
    rango con/sin halo, ventana D-15/90d, fx_source si aplica. Sin JS."""
    datos = dash.contribucion_campanas(conn=conn)
    return templates.TemplateResponse(
        request,
        "contribucion.html",
        {"pantalla": "contribucion", "plataformas": datos["plataformas"]},
    )


@router.get("/inertes", response_class=HTMLResponse)
def pagina_inertes(request: Request, conn: ConexionLectura) -> HTMLResponse:
    """Inertes: hojas sin trafico con su clasificacion (BIDS 01 1.3).
    Server-rendered desde el endpoint (regla 22); el texto de la hoja se
    renderiza ESCAPADO ({{ }}) — keyword_text es el vector XSS real."""
    datos = dash.inertes(conn=conn)
    return templates.TemplateResponse(
        request,
        "inertes.html",
        {"pantalla": "inertes", "totales": datos["totales"], "items": datos["items"]},
    )


@router.get("/cortes", response_class=HTMLResponse)
def pagina_cortes(request: Request, conn: ConexionLectura) -> HTMLResponse:
    """Cortes pendientes de veto (ORBIT 04 3.1, sellado 20): tabla de
    pending_veto/released con su vencimiento y boton Vetar (mini-form inline
    con dias/actor/token). El submit lo cablea /static/js/cortes.js contra el
    endpoint autenticado de app/api_write.py (la CSP prohibe JS inline). El
    search_term se renderiza ESCAPADO ({{ }}) — es el vector XSS real."""
    datos = dash.cortes(conn=conn)
    return templates.TemplateResponse(
        request, "cortes.html", {"pantalla": "cortes", "items": datos["items"]}
    )
