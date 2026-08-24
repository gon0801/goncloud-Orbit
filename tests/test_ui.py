"""Tests de la UI server-rendered del dashboard (ORBIT 16 - DASHBOARD 01,
task 1.6).

Contrato sellado (plan 1.6 + brief §2/§6):

1. CUATRO RUTAS -> 200 con marcador unico por pantalla (data-pantalla en el
   body); la UI es server-rendered (Jinja2) y consume los endpoints del
   dashboard (un camino, regla 2).
2. XSS regla 9 en DOS contextos: (a) HTML — el search_term (texto libre del
   comprador, el vector real) via {{ }} con AUTOESCAPE VERIFICADO: el test
   demuestra que con autoescape OFF el payload crudo aparece (el candado
   discrimina) y con el entorno real queda escapado; (b) tojson — los datos
   hacia JS de las graficas pasan EXCLUSIVAMENTE por |tojson (decision 12):
   el payload en los datos queda neutralizado (\\u003c) en el HTML servido.
3. CERO hosts externos en el HTML servido (lib vendoreada en /static, sin
   CDN — Reject del plan).
4. Headers: CSP default-src 'self' y Cache-Control: no-store en las
   respuestas HTML.
5. uv.lock COMMITEADO (Jinja2 pinneada en pyproject; el lock viaja en el
   repo).
6. El cliente parsea el dinero string con Number() (decision 7): el backend
   jamas emite floats de dinero (los endpoints ya lo sellan).
"""

from __future__ import annotations

import datetime as dt

import jinja2
import pytest
from fastapi.testclient import TestClient
from test_api_dashboard import (
    _campana,
    _ciclo,
    _config_version,
    _db_temporal,
    _decision,
    _metrica,
    _run,
)
from test_schema import _postgres_obligatorio_ausente

from app import ui
from app.main import app

PANTALLAS = {
    "/": "resumen",
    "/campanas": "campanas",
    "/decisiones": "decisiones",
    "/salud": "salud",
}

PAYLOAD_XSS = "<script>alert('xss')</script>"


# ---------------------------------------------------------------------------
# XSS contexto HTML: search_term con autoescape VERIFICADO (regla 9)
# ---------------------------------------------------------------------------


def _ctx_decisiones() -> dict:
    """Contexto minimo del template de decisiones (render sin DB): una
    decision cuyo search_term es el payload XSS (el vector real del dominio)."""
    return {
        "pantalla": "decisiones",
        "items": [
            {
                "id": 1,
                "cycle_id": 1,
                "ad_entity_id": 1,
                "nombre": "Campana",
                "plataforma": "amazon_us",
                "kind": "negative",
                "decided_at": "2026-08-20T12:00:00Z",
                "search_term": PAYLOAD_XSS,
                "old_value": None,
                "new_value": None,
                "value_currency": None,
                "target_acos_pct_usado": "20.00",
                "motivo_es": "Negativo: termino sin ventas",
            }
        ],
        "next_cursor": None,
        "has_more": False,
    }


def test_ui_xss_search_term_escapado_en_html_con_autoescape():
    """Regla 9: el search_term (texto libre del comprador) va por {{ }} y el
    entorno REAL de Jinja2 lo ESCAPA (autoescape verificado, no asumido). El
    HTML servido jamas contiene el <script> crudo del comprador."""
    html = ui.templates.env.get_template("decisiones.html").render(**_ctx_decisiones())
    assert PAYLOAD_XSS not in html
    assert "&lt;script&gt;" in html


def test_ui_xss_search_term_demostrado_fallando_con_autoescape_off():
    """Regla 9, demostracion in situ: con autoescape OFF el mismo template
    SIRVE el payload crudo — si el candado no discriminara, no probaria nada.
    El entorno de la app NO es este: es el de ui.templates.env (autoescape
    on, cubierto por el test anterior)."""
    loader = jinja2.FileSystemLoader(str(ui._TEMPLATES_DIR))
    sin_escape = jinja2.Environment(loader=loader, autoescape=False)
    html = sin_escape.get_template("decisiones.html").render(**_ctx_decisiones())
    assert PAYLOAD_XSS in html, "con autoescape off el payload DEBE aparecer crudo"


# ---------------------------------------------------------------------------
# XSS contexto tojson: datos de graficas neutralizados (decision 12)
# ---------------------------------------------------------------------------


def _ctx_resumen_con_payload() -> dict:
    """Contexto del template de resumen: los datos de grafica llevan el
    payload XSS dentro de un string del dict que viaja por |tojson (el
    mecanismo es lo que se prueba: TODO el dict de la serie se neutraliza)."""
    return {
        "pantalla": "resumen",
        "series": {
            "amazon_us": {
                "plataforma": "amazon_us",
                "moneda": "USD",
                "desde": "2026-07-25",
                "hasta": "2026-08-23",
                "ventana_inmaduros": {"desde": "2026-08-16", "hasta": "2026-08-23"},
                "series": [
                    {
                        "fecha": "2026-08-20",
                        "cost": "1.0000",
                        "ad_revenue": "3.0000",
                        "clicks": 1,
                        "acos": "33.33",
                        "sin_ventas": False,
                        "inmaduro": True,
                    }
                ],
                # string dentro de los datos que viajan por tojson
                "nombre_payload": PAYLOAD_XSS,
            }
        },
    }


def test_ui_xss_datos_de_grafica_neutralizados_por_tojson():
    """Regla 9, segundo contexto: los datos hacia JS pasan EXCLUSIVAMENTE por
    |tojson (decision 12). tojson escapa < y > (\\u003c/\\u003e): el HTML
    servido no contiene el <script> crudo dentro del bloque de datos."""
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert PAYLOAD_XSS not in html
    assert "\\u003cscript\\u003e" in html, "tojson debe escapar < y > en los datos"


# ---------------------------------------------------------------------------
# CSP: los templates NO dependen de nada inline (hallazgo mayor de review)
# ---------------------------------------------------------------------------


def test_ui_templates_compatibles_con_csp_self():
    """Regla 9 (hallazgo MAYOR de la review del bloque 2): la CSP
    `default-src 'self'` bloquea <script> inline, atributos on*= y <style>
    inline — las graficas y la paginacion morian EN SILENCIO en el navegador
    real (TestClient no ejecuta JS: por eso este candado es ESTATICO y
    discrimina en cualquier maquina). Todo JS/CSS vive en /static; los unicos
    <script> inline permitidos son los bloques INERTES de datos
    type="application/json"."""
    import re

    for archivo in sorted(ui._TEMPLATES_DIR.glob("*.html")):
        fuente = archivo.read_text(encoding="utf-8")
        assert "<style" not in fuente, f"{archivo.name}: CSS inline (la CSP lo bloquea)"
        assert not re.search(r"\son[a-z]+\s*=", fuente), (
            f"{archivo.name}: handler inline on*= (la CSP lo bloquea)"
        )
        for tag in re.findall(r"<script\b[^>]*>", fuente):
            assert 'type="application/json"' in tag or "src=" in tag, (
                f"{archivo.name}: <script> inline ejecutable (la CSP lo bloquea): {tag}"
            )


def test_docs_de_fastapi_sin_csp_que_los_rompa():
    """grok r2: /docs y /redoc cargan swagger/redoc desde CDN; la CSP
    default-src 'self' los dejaria EN BLANCO. El middleware los excluye de la
    CSP (superficie dev por tunel, fuera del contrato cero-CDN del dashboard);
    las pantallas del dashboard SI la llevan (test de headers existente)."""
    resp = TestClient(app).get("/docs")
    assert resp.status_code == 200
    assert "Content-Security-Policy" not in resp.headers


def test_vendor_chartjs_intacto_por_hash():
    """El asset vendoreado es EXACTAMENTE el documentado en el brief §6
    (hallazgo review: el hash documentado no tenia candado — un reemplazo o
    edicion a mano del bundle pasaba invisible)."""
    import hashlib
    from pathlib import Path

    ruta = Path(ui.__file__).resolve().parent / "static" / "vendor" / "chart.umd.min.js"
    hash_real = hashlib.sha256(ruta.read_bytes()).hexdigest()
    assert hash_real == "bce154080959c574be0bb6b1a924ff32f08ebc6ff460c159171f51c53802c844"
    brief = Path(ui.__file__).resolve().parent.parent / "docs" / "DASHBOARD.md"
    assert hash_real.upper() in brief.read_text(encoding="utf-8"), "el brief §6 documenta otro hash"


# ---------------------------------------------------------------------------
# uv.lock commiteado
# ---------------------------------------------------------------------------


def test_uv_lock_commiteado_y_jinja2_pinneada():
    """DoD: uv.lock viaja en el repo (git lo trackea) y pyproject pinnea
    jinja2 (el lock resuelto la incluye)."""
    import subprocess
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    assert (raiz / "uv.lock").is_file(), "uv.lock debe existir"
    rastreo = subprocess.run(
        ["git", "ls-files", "uv.lock"], capture_output=True, text=True, cwd=raiz
    )
    assert rastreo.returncode == 0
    assert "uv.lock" in rastreo.stdout, "uv.lock debe estar COMMITEADO"
    pyproject = (raiz / "pyproject.toml").read_text(encoding="utf-8")
    assert "jinja2" in pyproject
    lock = (raiz / "uv.lock").read_text(encoding="utf-8")
    assert "jinja2" in lock


# ---------------------------------------------------------------------------
# INTEGRACION (skipif sin Postgres): rutas 200, headers, cero hosts externos
# ---------------------------------------------------------------------------


def _siembra_ui(conn) -> None:
    """Seed minimo para que las 4 pantallas tengan datos: campana con
    metricas, goal de plataforma, config, ciclo con skips y una decision."""
    run = _run(conn)
    config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
    camp = _campana(conn, "amazon_us", "9001", name="Campana A")
    _metrica(
        conn,
        run,
        camp,
        dt.date(2026, 8, 20),
        cost="1.0000",
        ad_revenue="3.0000",
        clicks=1,
        moneda="USD",
    )
    ciclo = _ciclo(
        conn,
        platform="amazon_us",
        notes='{"skips": {"entidad": {"estado_no_enabled": 3}}, "decisiones": {"bid": 1}}',
    )
    _decision(
        conn,
        ciclo,
        camp,
        kind="bid",
        config_id=config_id,
        moneda="USD",
        inputs={"motor": "bid", "motivo": "banda_menos_12", "target_acos_pct_usado": "25.00"},
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_4_rutas_200_con_marcador_unico(monkeypatch):
    """DoD: las 4 rutas -> 200 con el marcador unico de su pantalla
    (data-pantalla en el body). La UI consume los endpoints del dashboard
    (un camino, regla 2): sin DB los endpoints responden 503 y la pantalla
    de la ruta no renderiza."""
    with _db_temporal("orbit_ui_rutas") as (conn, dsn):
        _siembra_ui(conn)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        cliente = TestClient(app)
        for ruta, pantalla in PANTALLAS.items():
            resp = cliente.get(ruta)
            assert resp.status_code == 200, f"{ruta} -> {resp.status_code}"
            html = resp.text
            assert f'data-pantalla="{pantalla}"' in html, (
                f"{ruta} sin el marcador unico de la pantalla {pantalla}"
            )
            assert html.count('data-pantalla="') == 1


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_headers_csp_y_no_store(monkeypatch):
    """DoD: CSP default-src 'self' y Cache-Control: no-store en las respuestas
    HTML (middleware consistente, decision 12 del header)."""
    with _db_temporal("orbit_ui_headers") as (conn, dsn):
        _siembra_ui(conn)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        resp = TestClient(app).get("/")
        assert resp.status_code == 200
        assert resp.headers.get("content-security-policy") == "default-src 'self'"
        assert "no-store" in resp.headers.get("cache-control", "")
        # la API JSON no necesita los headers de HTML (pero no debe romper)
        assert TestClient(app).get("/health").status_code == 200


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_cero_hosts_externos_en_el_html(monkeypatch):
    """DoD + Reject del plan (cero CDN): el HTML servido no referencia ningun
    host externo (la lib de graficas es VENDOREADA en /static, jamas una URL
    absoluta http/https)."""
    with _db_temporal("orbit_ui_hosts") as (conn, dsn):
        _siembra_ui(conn)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        cliente = TestClient(app)
        for ruta in PANTALLAS:
            html = cliente.get(ruta).text
            assert "http://" not in html and "https://" not in html, (
                f"{ruta}: hosts externos prohibidos (cero CDN, Reject del plan)"
            )
        # el asset vendoreado se sirve localmente
        chart = cliente.get("/static/vendor/chart.umd.min.js")
        assert chart.status_code == 200
        assert chart.headers["content-type"].startswith("application/javascript") or (
            "javascript" in chart.headers["content-type"]
        )


# ---------------------------------------------------------------------------
# El cliente parsea el dinero string con Number() (decision 7)
# ---------------------------------------------------------------------------


def test_ui_js_parsea_dinero_string_con_number():
    """Decision 7: el JS del cliente parsea el dinero string de la API con
    Number() (el backend jamas emite floats de dinero). El patron vive en
    /static/js/dashboard.js (externalizado por la CSP — hallazgo mayor de
    review) y este test lo fija ahi."""
    from pathlib import Path

    js = Path(ui.__file__).resolve().parent / "static" / "js" / "dashboard.js"
    fuente = js.read_text(encoding="utf-8")
    assert "Number(" in fuente, "el cliente debe parsear los strings con Number()"
    assert "JSON.parse" in fuente, "los datos inertes se leen con JSON.parse"


# ---------------------------------------------------------------------------
# Bugs reales del bloque 2 (codex altas/medias): cursor de la UI y cero != null
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_decisiones_propaga_el_cursor(monkeypatch):
    """Regla 9 (hallazgo ALTA de codex): la ruta UI ignoraba ?cursor= —
    'Cargar mas' recargaba la misma pagina por siempre. Con cursor=<id>, la
    pagina solo muestra ids MENORES."""
    with _db_temporal("orbit_ui_cursor") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        inputs = {"motivo": "banda_menos_12", "target_acos_pct_usado": "20"}
        # un ciclo por decision: el schema sella UNA decision por entidad
        # por ciclo (decision_unica_entidad_ciclo)
        ids = [
            _decision(
                conn,
                _ciclo(conn, platform="amazon_us"),
                camp,
                kind="bid",
                config_id=config_id,
                inputs=inputs,
                old_value="1.00",
                new_value="0.88",
                moneda="USD",
            )
            for _ in range(3)
        ]
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        html = TestClient(app).get("/decisiones", params={"cursor": ids[1]}).text
        assert f"<td>{ids[0]}</td>" in html, "la pagina con cursor debe traer los ids menores"
        assert f"<td>{ids[1]}</td>" not in html and f"<td>{ids[2]}</td>" not in html, (
            "la ruta UI debe propagar el cursor al feed (id < cursor)"
        )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_cero_no_se_pinta_como_dato_faltante(monkeypatch):
    """Regla 3 en la capa de PRESENTACION (hallazgo media de codex): el idiom
    `or "—"` pintaba clicks=0 y applied_count=0 como dato faltante. CERO es
    dato (y en shadow applied_count es SIEMPRE 0: se veia "—" en todos los
    ciclos)."""
    with _db_temporal("orbit_ui_ceros") as (conn, dsn):
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 8, 20),
            cost="1.0000",
            ad_revenue="3.0000",
            clicks=0,
            moneda="USD",
        )
        conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, status, finished_at,"
            " decisions_count, applied_count, notes) VALUES"
            " ('ads_optimizer', 'shadow', 'amazon_us', 'done', now(), 0, 0, NULL)"
        )
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        cliente = TestClient(app)
        campanas_html = cliente.get("/campanas").text
        assert 'class="num">0<' in campanas_html.replace("</td>", "<"), (
            "clicks=0 debe renderizarse como 0, jamas como dato faltante"
        )
        salud_html = cliente.get("/salud").text
        assert "applies: 0" in salud_html, (
            "applied_count=0 (lo normal en shadow) debe verse como 0, no como —"
        )
