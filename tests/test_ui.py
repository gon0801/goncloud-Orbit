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
import re

import jinja2
import pytest
from fastapi.testclient import TestClient
from test_api_dashboard import (
    SQL13,
    _campana,
    _ciclo,
    _config_version,
    _db_temporal,
    _decision,
    _estado_acos,
    _grupo,
    _keyword,
    _metrica,
    _product_target,
    _run,
)
from test_schema import _postgres_obligatorio_ausente

from app import ui
from app.api import _conexion_lectura
from app.api_dashboard import PageWindow
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
        "ventana": PageWindow.desde_total(total=1, page=1, page_size=50),
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
    sin_escape.filters["dinero_ui"] = ui.dinero_ui
    sin_escape.filters["ts_ui"] = ui.ts_ui
    html = sin_escape.get_template("decisiones.html").render(**_ctx_decisiones())
    assert PAYLOAD_XSS in html, "con autoescape off el payload DEBE aparecer crudo"


# ---------------------------------------------------------------------------
# Pantalla de cortes (ORBIT 04 3.1): XSS + vacio + cableo CSP
# ---------------------------------------------------------------------------


def _ctx_cortes(search_term: str = PAYLOAD_XSS) -> dict:
    """Contexto minimo del template de cortes (render sin DB): una fila
    pendiente de veto cuyo search_term es el payload XSS (el vector real)."""
    return {
        "pantalla": "cortes",
        "items": [
            {
                "id": 11,
                "plataforma": "amazon_us",
                "familia": "term_cut",
                "kind": "negative",
                "ad_entity_id": 3,
                "external_id": "7101",
                "nombre": "Campana A",
                "search_term": search_term,
                "estado": "pending_veto",
                "vence_el": "2026-09-25T12:00:00+00:00",
                "encolado_at": "2026-08-26T12:00:00+00:00",
                "decision_id": 99,
            }
        ],
    }


def test_ui_cortes_xss_search_term_escapado():
    """Regla 9: el search_term de la cola (texto libre del comprador) va por
    {{ }} en cortes.html y el entorno REAL lo escapa; el HTML servido jamas
    contiene el <script> crudo. El nav de base.html enlaza /cortes y el JS de
    la pantalla vive en /static/js/cortes.js (la CSP prohibe inline)."""
    html = ui.templates.env.get_template("cortes.html").render(**_ctx_cortes())
    assert PAYLOAD_XSS not in html
    assert "&lt;script&gt;" in html
    assert 'href="/cortes"' in html, "el nav del dashboard enlaza la pantalla de cortes"
    assert 'src="/static/js/cortes.js"' in html, "el JS del veto vive en /static (CSP 'self')"


def test_ui_cortes_entidad_muestra_nombre_no_external_id():
    html = ui.templates.env.get_template("cortes.html").render(**_ctx_cortes())
    assert "Campana A" in html
    assert "7101" not in html


def test_ui_cortes_vacio_muestra_sin_pendientes():
    """Estado vacio de la matriz: cola sin pendientes -> mensaje explicito,
    jamas una tabla mentirosa."""
    html = ui.templates.env.get_template("cortes.html").render(pantalla="cortes", items=[])
    assert "sin cortes pendientes" in html


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
    assert "maintainAspectRatio: false" in fuente
    assert "borderColor" in fuente
    assert "MutationObserver" in fuente
    assert '"data-tema"' in fuente
    assert "cargarMas" not in fuente
    assert "btn-mas" not in fuente


def test_ui_decisiones_paginador_es_html_sin_js():
    """El paginador es un GET. Falla contra a658f2b: el template pinta #btn-mas."""
    html = ui.templates.env.get_template("decisiones.html").render(
        pantalla="decisiones",
        items=[],
        ventana=PageWindow.desde_total(total=120, page=1, page_size=50),
    )
    assert 'rel="next" href="?page=2"' in html
    assert 'id="btn-mas"' not in html
    assert "data-cursor" not in html
    assert "pagina 1 de 3" in html


def test_ui_old_new_en_dos_columnas_con_dinero_ui():
    """Old y New son dos columnas. Falla contra a658f2b: una celda 'old / new'."""
    ctx = _ctx_decisiones()
    ctx["items"][0]["old_value"] = "1.0000"
    ctx["items"][0]["new_value"] = "0.8800"
    ctx["items"][0]["value_currency"] = "USD"
    html = ui.templates.env.get_template("decisiones.html").render(**ctx)
    assert ">Old</th>" in html and ">New</th>" in html
    assert 'class="num dinero">Old</th>' in html.replace("'", '"')
    assert "old / new" not in html
    assert 'class="num dinero">1.00<' in html.replace("</td>", "<")
    assert 'class="num dinero">0.88<' in html.replace("</td>", "<")
    assert "1.0000 / 0.8800" not in html
    pause = _ctx_decisiones()
    html_pause = ui.templates.env.get_template("decisiones.html").render(**pause)
    assert html_pause.count('<td class="num dinero"><span class="mutado">—</span></td>') == 2
    assert "0.00 /" not in html_pause and "/ 0.00" not in html_pause


def test_ui_canvas_siempre_dentro_de_lienzo():
    """Cada canvas vive en .lienzo y no declara height. Falla contra a658f2b."""
    import re

    for nombre in ("resumen.html", "salud.html"):
        fuente = (ui._TEMPLATES_DIR / nombre).read_text(encoding="utf-8")
        for tag in re.findall(r"<canvas\b[^>]*>", fuente):
            assert "height=" not in tag, tag
        assert fuente.count("<canvas") == fuente.count('<div class="lienzo">')


def test_css_lienzo_y_sin_max_width_en_canvas():
    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "dashboard.css").read_text(
        encoding="utf-8"
    )
    assert re.search(r"\.lienzo\s*\{[^}]*aspect-ratio", css)
    assert re.search(r"\.lienzo\s*\{[^}]*position:\s*relative", css)
    assert "canvas { max-width" not in css
    assert ".paginador" in css


# ---------------------------------------------------------------------------
# Bugs reales del bloque 2 (codex altas/medias): cursor de la UI y cero != null
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_decisiones_pagina_2_trae_los_ids_siguientes(monkeypatch):
    """GET /decisiones?page=2 con page_size=2 siembra 3 ids (DESC): pagina 2
    trae solo el id mas viejo. ?page=9 clampa a la ultima. Falla contra el
    feed por cursor: ?page= se ignoraba y salian los 3 ids."""
    with _db_temporal("orbit_ui_pagina") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        inputs = {"motivo": "banda_menos_12", "target_acos_pct_usado": "20"}
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
        monkeypatch.setattr(ui.dash, "LIMITE_FEED_DEFAULT", 2)
        html = TestClient(app).get("/decisiones", params={"page": 2}).text
        assert f"<td>{ids[0]}</td>" in html
        assert f"<td>{ids[1]}</td>" not in html and f"<td>{ids[2]}</td>" not in html
        assert 'rel="prev" href="?page=1"' in html
        assert 'rel="next"' not in html
        assert "pagina 2 de 2" in html
        html_clamp = TestClient(app).get("/decisiones", params={"page": 9}).text
        assert "pagina 2 de 2" in html_clamp
        assert f"<td>{ids[0]}</td>" in html_clamp


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_decisiones_entidad_no_vuelca_json_de_target(monkeypatch):
    with _db_temporal("orbit_ui_etiqueta") as (conn, dsn):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _campana(conn, "amazon_us", "9001", name="Campana A")
        ag = _grupo(conn, "amazon_us", "9101", parent=camp)
        target = _product_target(
            conn,
            "amazon_us",
            "9301",
            ag,
            '[{"type":"ASIN_SAME_AS","value":"B086TVLJ43"}]',
        )
        _decision(
            conn,
            _ciclo(conn, platform="amazon_us"),
            target,
            kind="bid",
            config_id=config_id,
            inputs={"motivo": "banda_menos_12", "target_acos_pct_usado": "25.00"},
            old_value="1.00",
            new_value="0.88",
            moneda="USD",
        )
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        html = TestClient(app).get("/decisiones").text
        assert "mismo ASIN B086TVLJ43" in html
        assert "Campana A" in html
        assert "ASIN_SAME_AS" not in html
        assert "B086TVLJ43" in html


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


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_salud_muestra_nota_telegram_del_ciclo(monkeypatch):
    """ORBIT 04 3.3 (sellado 2): la NOTA notes['telegram'] — la unica
    visibilidad del fallo del canal — se MUESTRA en la pantalla de Salud del
    ultimo ciclo; sin la linea, un ciclo done con el canal caido se veria
    perfecto (regla 9: el rojo de este test se capturo antes del display)."""
    import json as _json

    with _db_temporal("orbit_ui_salud_tg") as (conn, dsn):
        notas = _json.dumps(
            {
                "skips": {"entidad": {}},
                "telegram": {"digest": "fallo: digest del ciclo no enviado por Telegram"},
            }
        )
        conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, status, finished_at,"
            " decisions_count, applied_count, notes) VALUES"
            " ('ads_optimizer', 'shadow', 'amazon_us', 'done', now(), 0, 0, %s)",
            (notas,),
        )
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        salud_html = TestClient(app).get("/salud").text
        assert "telegram" in salud_html, "la NOTA debe verse en Salud (sellado 2)"
        assert "digest" in salud_html, "la clave que fallo es la evidencia visible"


# ---------------------------------------------------------------------------
# Quota del dia en Salud (ORBIT 05 preflight 1.5): render de la tabla quota
# ---------------------------------------------------------------------------


_QUOTA_BASE = {
    "bid": {"used": 3, "cap": 10, "fuente": "fila_del_dia"},
    "pause": {"used": 0, "cap": 2, "fuente": "config_vigente"},
    "negative": {"used": 0, "cap": None, "fuente": "sin_clave"},
    "harvest": {"used": 0, "cap": None, "fuente": "config_rota"},
}


def _plataforma_quota(quota: dict | None = None) -> dict:
    """Tarjeta de plataforma del payload de dash.salud (preflight 1.4): la
    forma EXACTA que pagina_salud entrega al template salud.html."""
    return {
        "watermark": "2026-08-28",
        "synced_at": "2026-08-28T09:00:00+00:00",
        "ultimo_ciclo": None,
        "historico_14d": [],
        "skips": {"entidad": {}, "termino": {}},
        "quota": _QUOTA_BASE if quota is None else quota,
    }


def _salud_html_fakeado(monkeypatch, plataformas: dict) -> str:
    """Render de /salud por el camino REAL (TestClient -> pagina_salud ->
    dash.salud(conn) -> template) con el endpoint FAKEADO: la UI consume el
    endpoint (regla 2) y aqui se prueba SOLO el render, sin DB (la
    dependencia de lectura se sobreescribe y no se toca)."""
    monkeypatch.setattr(ui.dash, "salud", lambda conn: {"plataformas": plataformas})
    app.dependency_overrides[_conexion_lectura] = lambda: None
    try:
        return TestClient(app).get("/salud").text
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)


def _fila_de(html: str, forma: str) -> str:
    """La fila <tr> de UNA forma de la tabla de quota (aserciones por fila:
    el used/cap de una forma no se confunde con el de otra)."""
    filas = [fila for fila in html.split("<tr>") if fila.startswith(f"<td>{forma}</td>")]
    assert len(filas) == 1, f"la forma {forma} debe aparecer en exactamente una fila"
    return filas[0]


def _celda(fila: str, indice: int) -> str:
    """Contenido EXACTO de la celda indice de una fila (orden contractual:
    forma · used · cap · estado)."""
    return fila.split("<td")[1 + indice].split(">", 1)[1].split("</td>")[0]


def test_ui_salud_quota_muestra_las_cuatro_formas_con_sus_numeros(monkeypatch):
    """Preflight 1.5: la tarjeta de cada plataforma dibuja UNA tabla con una
    fila por forma (KINDS_QUOTA) y su used/cap: el dueno responde de un
    vistazo cuanto lleva hoy y contra que tope."""
    html = _salud_html_fakeado(monkeypatch, {"amazon_us": _plataforma_quota()})
    assert "<h3>Quota del dia</h3>" in html
    for forma in _QUOTA_BASE:
        assert _celda(_fila_de(html, forma), 0) == forma, "la 1a celda es la forma"
    fila_bid = _fila_de(html, "bid")
    assert _celda(fila_bid, 1) == "3", "used de bid"
    assert _celda(fila_bid, 2) == "10", "cap de bid (fila_del_dia)"
    assert _celda(_fila_de(html, "pause"), 1) == "0", "pause sin consumo hoy"
    assert _celda(_fila_de(html, "pause"), 2) == "2", "cap de pause (config_vigente)"
    # fuente distinguible a simple vista: fila_del_dia (chip ok) vs
    # config_vigente (chip neutro), con su nombre legible en la fila
    assert 'class="chip ok"' in _fila_de(html, "bid")
    assert 'class="chip ok"' not in _fila_de(html, "pause")
    assert "fila_del_dia" in _celda(_fila_de(html, "bid"), 3)
    assert "config_vigente" in _celda(_fila_de(html, "pause"), 3)


def test_ui_salud_quota_cap_nulo_sin_clave_no_se_pinta_como_cero(monkeypatch):
    """Regla 3 en presentacion: cap None (sin_clave) es FAIL-CLOSED, no tope
    cero — la celda muestra '—' CON su etiqueta, jamas 0 ni el literal None."""
    html = _salud_html_fakeado(monkeypatch, {"amazon_us": _plataforma_quota()})
    fila = _fila_de(html, "negative")
    cap = _celda(fila, 2)
    assert "—" in cap and "sin clave" in cap, "cap nulo: guion + etiqueta"
    assert "0" not in cap, "cap nulo jamas se pinta como 0 (no es tope cero)"
    assert "None" not in cap, "cap nulo jamas se pinta como el literal None"
    assert 'class="chip alerta"' in _celda(fila, 3), "sin_clave se marca como alerta"


def test_ui_salud_quota_config_rota_marcada_como_alerta(monkeypatch):
    """config_rota (clave presente, valor no numerico) es ESTADO DE ALARMA: el
    cobro de esa forma revienta ruidoso a proposito — la fila se marca con la
    clase de alerta existente y NO se confunde con sin_clave."""
    html = _salud_html_fakeado(monkeypatch, {"amazon_us": _plataforma_quota()})
    fila = _fila_de(html, "harvest")
    estado = _celda(fila, 3)
    assert 'class="chip alerta"' in estado, "config_rota se marca como alerta"
    assert "config_rota" in estado
    cap = _celda(fila, 2)
    assert "—" in cap and "config rota" in cap, "cap nulo con SU etiqueta (rota)"
    assert "0" not in cap and "None" not in cap
    assert "sin clave" not in estado and "sin_clave" not in estado, (
        "etiquetas distintas de sin_clave"
    )


def test_ui_salud_quota_used_igual_cap_se_ve_saturada(monkeypatch):
    """used >= cap con cap NO nulo es el estado que dispara el aviso Telegram
    de 1.4: la fila se ve SATURADA (chip de alerta) en la misma tabla."""
    quota = dict(_QUOTA_BASE)
    quota["bid"] = {"used": 3, "cap": 3, "fuente": "fila_del_dia"}
    quota["pause"] = {"used": 5, "cap": 2, "fuente": "fila_del_dia"}
    html = _salud_html_fakeado(monkeypatch, {"amazon_us": _plataforma_quota(quota)})
    for forma in ("bid", "pause"):
        estado = _celda(_fila_de(html, forma), 3)
        assert "saturada" in estado, f"{forma} con used >= cap debe verse saturada"
        assert 'class="chip alerta"' in estado
    assert '<span class="chip alerta">saturada</span>' in _celda(_fila_de(html, "bid"), 3), (
        "el chip de saturacion usa la clase de alerta existente"
    )
    estado_negativo = _celda(_fila_de(html, "negative"), 3)
    assert "saturada" not in estado_negativo, "la fila sana no lleva el chip"


def test_ui_salud_sin_bloque_quota_no_rompe_la_pantalla(monkeypatch):
    """Payload VIEJO (plataforma sin la clave quota, pre-1.4): la tarjeta se
    renderiza entera SIN tabla de quota y no arrastra a las demas tarjetas."""
    vieja = _plataforma_quota()
    del vieja["quota"]
    html = _salud_html_fakeado(monkeypatch, {"amazon_mx": vieja, "amazon_us": _plataforma_quota()})
    assert html.count("Quota del dia") == 1, "solo la tarjeta CON quota dibuja la tabla"
    assert "<h2>amazon_mx" in html, "la tarjeta vieja sigue renderizando"
    assert "Historico (14d)" in html, "las secciones existentes quedan intactas"


# ---------------------------------------------------------------------------
# Campanas: filtro y sort por columnas que YA estan en la tabla
# ---------------------------------------------------------------------------


def _item_campana(
    nombre: str,
    *,
    status="ENABLED",
    plataforma="amazon_us",
    clicks=8,
    cost="10.0000",
    revenue="40.0000",
    acos="25.00",
    sin_ventas=False,
    peldano="goal_plataforma",
    target="25.00",
) -> dict:
    """Fila con el shape de `_fila_campana` (api_dashboard). Sin orders ni
    impressions: esas columnas no existen en campanas.html."""
    return {
        "ad_entity_id": abs(hash(nombre)) % 10000,
        "nombre": nombre,
        "status": status,
        "plataforma": plataforma,
        "moneda": "USD" if plataforma == "amazon_us" else "MXN",
        "metricas_30d": {
            "cost": cost,
            "ad_revenue": revenue,
            "clicks": clicks,
            "acos": acos,
            "sin_ventas": sin_ventas,
            "inmaduro": True,
        },
        "target_efectivo": {"valor": target, "peldano": peldano},
        "goal": None,
    }


def _lote_campanas() -> list[dict]:
    """Cuatro filas distintas en las columnas utiles (plataforma, estado,
    clicks, ACoS, cost, revenue). El orden de insercion NO es el de clicks."""
    return [
        _item_campana("Alfa US", clicks=80, cost="9.0000", revenue="40.0000", acos="22.50"),
        _item_campana("Beta US", status="PAUSED", clicks=8, cost="12.0000", acos="60.00"),
        _item_campana(
            "Gama MX",
            plataforma="amazon_mx",
            clicks=30,
            cost="100.0000",
            revenue="400.0000",
            acos="25.00",
            peldano="default",
        ),
        _item_campana(
            "Delta US",
            clicks=None,
            cost=None,
            revenue="0.0000",
            acos=None,
            sin_ventas=True,
        ),
    ]


def _nombres(items: list[dict]) -> list[str]:
    return [item["nombre"] for item in items]


def test_filtra_campanas_plataforma_estado_clicks_acos():
    """Las cuatro columnas pedidas filtran de verdad. Contra el handler viejo
    (que ignoraba query params) este test FALLA: devolveria las 4 filas."""
    lote = _lote_campanas()
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"plataforma": "amazon_mx"}, None, None)) == [
        "Gama MX"
    ]
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"estado": "PAUSED"}, None, None)) == [
        "Beta US"
    ]
    # clicks=8 es igualdad exacta: 80 no cuela (substring mentiria)
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"clicks": "8"}, None, None)) == ["Beta US"]
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"acos": "60"}, None, None)) == ["Beta US"]
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"acos": "sin ventas"}, None, None)) == [
        "Delta US"
    ]


def test_filtra_campanas_cost_revenue_nombre_procedencia():
    """Las otras columnas utiles de la tabla tambien filtran (cost=spend,
    revenue=sales). Orders/impressions no estan en la tabla: no se inventan."""
    lote = _lote_campanas()
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"cost": "9.0000"}, None, None)) == [
        "Alfa US"
    ]
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"revenue": "400"}, None, None)) == [
        "Gama MX"
    ]
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"nombre": "gama"}, None, None)) == [
        "Gama MX"
    ]
    assert _nombres(ui.filtra_y_ordena_campanas(lote, {"procedencia": "default"}, None, None)) == [
        "Gama MX"
    ]


def test_ordena_campanas_clicks_y_acos_numericamente():
    """Sort numerico, no lexicografico: 80 > 30 > 8; cost 9.0000 < 12.0000.
    Los huecos (regla 3) van al final, nunca se pintan como 0 para ordenar."""
    lote = _lote_campanas()
    por_clicks = ui.filtra_y_ordena_campanas(lote, {}, "clicks", "desc")
    assert _nombres(por_clicks) == ["Alfa US", "Gama MX", "Beta US", "Delta US"]
    por_acos = ui.filtra_y_ordena_campanas(lote, {}, "acos", "asc")
    assert _nombres(por_acos)[:3] == ["Alfa US", "Gama MX", "Beta US"]
    assert por_acos[-1]["nombre"] == "Delta US"
    por_cost = ui.filtra_y_ordena_campanas(lote, {}, "cost", "asc")
    assert _nombres(por_cost)[:3] == ["Alfa US", "Beta US", "Gama MX"]


def _html_campanas(monkeypatch, items: list[dict], **params) -> str:
    """Render del camino REAL (TestClient -> pagina_campanas) con el
    endpoint fakeado: se prueba la vista, no el SQL."""

    def _campanas(conn, platform=None):
        if platform is None:
            return {"items": items}
        return {"items": [i for i in items if i["plataforma"] == platform]}

    monkeypatch.setattr(ui.dash, "campanas", _campanas)
    app.dependency_overrides[_conexion_lectura] = lambda: None
    try:
        return TestClient(app).get("/campanas", params=params)
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)


def test_ui_campanas_formulario_y_headers_ordenables(monkeypatch):
    """La pantalla tiene el form GET (CSP: cero JS) y headers con href de
    sort para las columnas utiles. Goal no se ordena: es texto compuesto."""
    html = _html_campanas(monkeypatch, _lote_campanas()).text
    assert 'class="filtros"' in html
    assert 'method="get"' in html
    assert 'name="plataforma"' in html
    assert 'name="estado"' in html
    assert 'name="clicks"' in html
    assert 'name="acos"' in html
    assert 'name="cost"' in html
    assert 'name="revenue"' in html
    assert 'href="/campanas?ordenar=plataforma&amp;dir=asc"' in html
    assert 'href="/campanas?ordenar=estado&amp;dir=asc"' in html
    assert 'href="/campanas?ordenar=clicks&amp;dir=desc"' in html
    assert 'href="/campanas?ordenar=acos&amp;dir=desc"' in html
    assert "ordenar=goal" not in html
    assert "impressions" not in html
    assert ">orders<" not in html.lower()


def test_ui_campanas_query_filtra_y_ordena_en_el_html(monkeypatch):
    """Regla 9: el GET /campanas?estado=PAUSED ignorado por el handler viejo
    seguia pintando Alfa US. Aqui solo queda Beta US. Sort por clicks desc
    pone Alfa (80) antes que Gama (30)."""
    lote = _lote_campanas()
    filtrado = _html_campanas(monkeypatch, lote, estado="PAUSED").text
    assert "Beta US" in filtrado
    assert "Alfa US" not in filtrado
    assert "Gama MX" not in filtrado
    assert "Ninguna campana coincide" not in filtrado
    ordenado = _html_campanas(monkeypatch, lote, ordenar="clicks", dir="desc").text
    assert ordenado.index("Alfa US") < ordenado.index("Gama MX") < ordenado.index("Beta US")
    assert 'aria-sort="descending"' in ordenado
    mx = _html_campanas(monkeypatch, lote, plataforma="amazon_mx").text
    assert "Gama MX" in mx and "Alfa US" not in mx
    vacio = _html_campanas(monkeypatch, lote, estado="ARCHIVED").text
    assert "Ninguna campana coincide con el filtro." in vacio


def test_ui_campanas_vocabulario_query_cerrado(monkeypatch):
    """plataforma/estado/ordenar/dir ajenos -> 422 (Literal del feed)."""
    lote = _lote_campanas()
    assert _html_campanas(monkeypatch, lote, plataforma="meli").status_code == 422
    assert _html_campanas(monkeypatch, lote, estado="ON").status_code == 422
    assert _html_campanas(monkeypatch, lote, ordenar="impressions").status_code == 422
    assert _html_campanas(monkeypatch, lote, dir="up").status_code == 422


# ---------------------------------------------------------------------------
# Pantalla de inertes (BIDS 01 1.3): resumen + tabla, XSS escapado
# ---------------------------------------------------------------------------


def _ctx_inertes(texto: str = PAYLOAD_XSS) -> dict:
    """Contexto minimo del template de inertes (render sin DB): una hoja
    gasto_sin_ventas cuyo texto es el payload XSS (keyword_text es el
    vector real de esta pantalla)."""
    return {
        "pantalla": "inertes",
        "totales": {"amazon_us": {"gasto_sin_ventas": 1}},
        "items": [
            {
                "plataforma": "amazon_us",
                "kind": "keyword",
                "texto": texto,
                "external_id": "8103",
                "campana": "Campana A",
                "ad_group": "Grupo A",
                "clasificacion": "gasto_sin_ventas",
                "dias_sin_impresiones": 19,
                "ultima_impresion": "2026-07-28",
                "gasto_90d": "12.50",
                "moneda": "USD",
                "ordenes_90d": 0,
            }
        ],
    }


def test_ui_inertes_xss_texto_escapado():
    """Regla 9: el texto de la hoja (keyword_text libre, el vector XSS de
    esta pantalla) va por {{ }} en inertes.html y el entorno REAL lo
    escapa; el HTML servido jamas contiene el <script> crudo."""
    html = ui.templates.env.get_template("inertes.html").render(**_ctx_inertes())
    assert PAYLOAD_XSS not in html
    assert "&lt;script&gt;" in html


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_inertes_200_con_clasificacion_y_escape(monkeypatch):
    """BIDS 01 1.3 por el camino REAL (TestClient -> pagina_inertes ->
    endpoint -> vista): 200 con la clasificacion y el keyword_text con
    <script> escapado en el HTML servido."""
    with _db_temporal("orbit_ui_inertes") as (conn, dsn):
        conn.execute(SQL13)
        run = _run(conn)
        camp = _campana(conn, "amazon_us", "8201", name="Campana B")
        ag = _grupo(conn, "amazon_us", "8202", camp)
        kw = _keyword(conn, "amazon_us", "8203", ag, PAYLOAD_XSS)
        ancla = _keyword(conn, "amazon_us", "8204", ag, "ancla")
        for eid in (camp, ag, kw, ancla):
            _estado_acos(conn, eid, None)
        _metrica(
            conn,
            run,
            kw,
            dt.date(2026, 7, 28),
            cost="5.00",
            ad_revenue="0.00",
            clicks=2,
            orders=0,
            impressions=20,
            moneda="USD",
        )
        _metrica(
            conn,
            run,
            ancla,
            dt.date(2026, 8, 16),
            cost="1.00",
            ad_revenue="0.00",
            clicks=1,
            orders=0,
            impressions=10,
            moneda="USD",
        )
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        resp = TestClient(app).get("/inertes")
        assert resp.status_code == 200, resp.text
        assert "gasto_sin_ventas" in resp.text
        assert PAYLOAD_XSS not in resp.text
        assert "&lt;script&gt;" in resp.text
