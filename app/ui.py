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

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import api_dashboard as dash
from app.api import ConexionLectura
from app.optimizer.bid import PLATAFORMAS_MONEDA

router = APIRouter(prefix="", tags=["dashboard-ui"])

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
# Jinja2Templates de Starlette crea el Environment con autoescape=True:
# el search_term del comprador nunca se inyecta crudo en el HTML (los tests
# de 1.6 lo VERIFICAN, no lo asumen — regla 9).
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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
def pagina_campanas(request: Request, conn: ConexionLectura) -> HTMLResponse:
    """Campanas: tabla 30d con target efectivo CON procedencia (5 peldanos,
    goals.py 1.2) y estado del goal. Cada fila lleva su moneda (regla 4)."""
    datos = dash.campanas(conn=conn)
    return templates.TemplateResponse(
        request,
        "campanas.html",
        {"pantalla": "campanas", "items": datos["items"]},
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
