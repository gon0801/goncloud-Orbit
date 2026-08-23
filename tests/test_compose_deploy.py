"""Candados del compose de deploy (ORBIT 03 / 4.1).

Defensa contra dos regresiones que ya costaron: (1) puertos en 0.0.0.0
(leccion 8055/8056, 7 semanas expuestos) y (2) tocar el servicio de Postgres
al agregar `app`. El bloque `db` es ADITIVO-intocable: si este test falla,
el diff del compose cambio una linea que no debia.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COMPOSE = RAIZ / "docker-compose.yml"
DOCKERFILE = RAIZ / "Dockerfile"

# Copia literal del servicio db en goncloud (verificado 2026-08-23). Si hay
# que cambiar Postgres, este string se actualiza CON review; no de paso.
DB_SELLADO = """\
  db:
    image: postgres:16
    restart: unless-stopped
    env_file: .env
    environment:
      POSTGRES_DB: orbit
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
"""


def test_compose_existe_y_db_esta_intacto():
    texto = COMPOSE.read_text(encoding="utf-8")
    assert DB_SELLADO in texto, "el servicio db del compose no coincide con el sellado"


def test_compose_ningun_puerto_en_todas_las_interfaces():
    texto = COMPOSE.read_text(encoding="utf-8")
    assert "0.0.0.0" not in texto, "puerto bind a 0.0.0.0: prohibido (leccion 8055/8056)"
    # "8010:8000" sin host tambien publica en todas las interfaces (hallazgo codex).
    en_ports = False
    for ln in texto.splitlines():
        if ln.strip() == "ports:":
            en_ports = True
            continue
        if en_ports:
            stripped = ln.strip()
            if stripped.startswith("- "):
                mapa = stripped[2:].strip().strip('"').strip("'")
                assert mapa.startswith("127.0.0.1:"), f"puerto no loopback: {mapa}"
            elif stripped and not stripped.startswith("#"):
                en_ports = False


def test_cron_metricas_escapa_porcentaje_de_date():
    """Vixie cron convierte % no escapado en newline: el job de metricas
    quedaria truncado antes de docker exec (alta de cross-review codex)."""
    texto = (RAIZ / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    lineas = [ln for ln in texto.splitlines() if ln.startswith("10 7 * * *")]
    assert lineas, "falta el cron 07:10 de metricas en DEPLOY.md"
    for ln in lineas:
        assert r"+\%F" in ln, f"date +%F sin escapar en crontab: {ln}"
        assert "+%F" not in ln.replace(r"+\%F", "")


def test_compose_app_en_loopback_8010_con_secrets_ro():
    texto = COMPOSE.read_text(encoding="utf-8")
    assert '"127.0.0.1:8010:8000"' in texto
    assert "secrets:/mnt/data/appdata/orbit/secrets:ro" in texto
    assert 'user: "0:0"' in texto
    assert "ORBIT_PG_HOST: db" in texto


def test_dockerfile_instala_con_lockfile_congelado():
    texto = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv.lock" in texto
    assert "uv sync --frozen" in texto
    assert "uvicorn" in texto
    assert "app.main:app" in texto
