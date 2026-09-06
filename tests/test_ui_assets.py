"""La recarga tras un deploy no reutiliza CSS/JS de una version anterior."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app import ui
from app.main import app


def test_html_versiona_todos_los_css_y_js():
    html = TestClient(app).get("/campanas/nuevas").text
    urls = re.findall(r'(?:src|href)="(/static/[^\"]+)"', html)
    activos = [url for url in urls if urlsplit(url).path.endswith((".css", ".js"))]
    assert len(activos) >= 8
    for url in activos:
        version = parse_qs(urlsplit(url).query).get("v", [])
        assert len(version) == 1 and re.fullmatch(r"[a-f0-9]{16}", version[0]), url
        response = TestClient(app).get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"


def test_css_legacy_y_condicional_exigen_revalidacion():
    cliente = TestClient(app)
    ruta = "/static/css/dashboard.css"
    response = cliente.get(ruta)
    assert response.headers.get("cache-control") == "no-cache"
    condicional = cliente.get(ruta, headers={"If-None-Match": response.headers["etag"]})
    assert condicional.status_code == 304
    assert condicional.headers["cache-control"] == "no-cache"


def test_url_cambia_si_cambia_css_o_js_aunque_tamano_y_fecha_se_conserven(tmp_path):
    import os

    calcular = getattr(ui, "version_estaticos", None)
    assert callable(calcular), "Falta version automatica por contenido"
    archivo = tmp_path / "dashboard.css"
    archivo.write_text("body{color:red}")
    estado = archivo.stat()
    version = calcular(tmp_path)
    assert version == calcular(tmp_path)
    archivo.write_text("body{color:tan}")
    os.utime(archivo, ns=(estado.st_atime_ns, estado.st_mtime_ns))
    assert calcular(tmp_path) != version
    version_css = calcular(tmp_path)
    (tmp_path / "dashboard.js").write_text("const version = 1;")
    assert calcular(tmp_path) != version_css
    assert ui.templates.env.globals["asset_version"] == calcular(
        Path(ui.__file__).parent / "static"
    )
