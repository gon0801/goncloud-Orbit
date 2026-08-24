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


def _bloque_servicio(texto: str, nombre: str) -> str:
    """Extrae las lineas del servicio `nombre` (indentacion de compose:
    servicios a 2 espacios bajo services:, contenido a 4+). Validar por
    BLOQUE evita los dos fallos del literal (hallazgo CodeRabbit): un
    cambio inocuo de formato ya no rompe, y una linea AGREGADA dentro
    del servicio ya no pasa desapercibida."""
    dentro_services = False
    dentro = False
    bloque: list[str] = []
    for ln in texto.splitlines():
        if ln.rstrip() == "services:":
            dentro_services = True
            continue
        if ln.strip() and not ln.startswith(" "):
            dentro_services = False
            dentro = False
            continue
        if dentro_services and ln.startswith("  ") and not ln.startswith("   "):
            dentro = ln.strip() == f"{nombre}:"
            continue
        if dentro:
            bloque.append(ln)
    return "\n".join(bloque)


def test_compose_db_sellado_por_contrato():
    """El servicio db es ADITIVO-intocable: el SET exacto de sus lineas
    operativas esta sellado — reordenar es inocuo, agregar o quitar NO."""
    bloque = _bloque_servicio(COMPOSE.read_text(encoding="utf-8"), "db")
    lineas = {
        ln.strip() for ln in bloque.splitlines() if ln.strip() and not ln.strip().startswith("#")
    }
    esperadas = {
        "image: postgres:16",
        "restart: unless-stopped",
        "env_file: .env",
        "environment:",
        "POSTGRES_DB: orbit",
        "volumes:",
        "- pgdata:/var/lib/postgresql/data",
        "ports:",
        '- "127.0.0.1:5432:5432"',
    }
    assert lineas == esperadas, (
        f"servicio db cambio (sellado 2026-08-23; diff simetrico): {lineas ^ esperadas}"
    )


# Allowlist EXACTA de MAPEOS completos host:puerto:puerto (DASHBOARD 01 task
# 2.1; endurecida por hallazgo Greptile P2: un set de solo hosts permitia
# mover el bind wg0 a OTRO servicio — p.ej. Postgres publicado en la VPN —
# sin ponerse rojo). 10.13.13.1 = interfaz WireGuard wg0, RFC1918
# point-to-point (solo el host y los peers cifrados del tunel; evidencia de
# firewall/NAT en ORBIT 16). Un mapeo nuevo aqui es DECISION con review.
MAPEOS_PERMITIDOS = {
    "127.0.0.1:5432:5432",
    "127.0.0.1:8010:8000",
    "10.13.13.1:8010:8000",
}


def _mapeos_de_ports(texto: str) -> set[str]:
    """Set de TODAS las entradas ports: del compose, mapeo COMPLETO. Una
    entrada sin host ("8010:8000") entra tal cual al set y el candado la
    rechaza (publicar sin host = todas las interfaces, hallazgo codex)."""
    mapeos: set[str] = set()
    en_ports = False
    for ln in texto.splitlines():
        if ln.strip() == "ports:":
            en_ports = True
            continue
        if en_ports:
            stripped = ln.strip()
            if stripped.startswith("- "):
                mapeos.add(stripped[2:].strip().strip('"').strip("'"))
            elif stripped and not stripped.startswith("#"):
                en_ports = False
    return mapeos


def test_compose_ningun_puerto_en_todas_las_interfaces():
    """0.0.0.0 prohibido (leccion 8055/8056) y allowlist EXACTA de MAPEOS
    completos (2.1 + Greptile P2): el compose publica EXACTAMENTE
    {db en loopback:5432, app en loopback:8010 y en wg0:8010 — la IP de la
    VPN para el cel del dueno}. El set EXACTO corta todos los lados: un
    mapeo de mas expone, uno de menos deja sin acceso, y mover el bind wg0
    a otro servicio/puerto tambien truena. Regla 9 in situ: los mutantes
    0.0.0.0, IP publica sintetica y entrada sin host DEBEN ser rechazados."""
    texto = COMPOSE.read_text(encoding="utf-8")
    assert "0.0.0.0" not in texto, "puerto bind a 0.0.0.0: prohibido (leccion 8055/8056)"
    assert _mapeos_de_ports(texto) == MAPEOS_PERMITIDOS, (
        f"mapeos del compose fuera de la allowlist exacta: "
        f"{_mapeos_de_ports(texto) ^ MAPEOS_PERMITIDOS}"
    )
    for mutante in (
        texto.replace('"127.0.0.1:8010:8000"', '"0.0.0.0:8010:8000"'),
        texto.replace('"127.0.0.1:8010:8000"', '"203.0.113.9:8010:8000"'),
        texto.replace('"127.0.0.1:8010:8000"', '"8010:8000"'),
        # el caso que el set de solo-hosts NO atrapaba: wg0 movido a Postgres
        texto.replace('"127.0.0.1:5432:5432"', '"10.13.13.1:5432:5432"'),
    ):
        assert _mapeos_de_ports(mutante) != MAPEOS_PERMITIDOS, (
            "el candado no discrimina un mutante de mapeo"
        )


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
    # Scoped al BLOQUE del servicio app: un texto igual en un comentario
    # o en otro servicio ya no da falso verde (hallazgo CodeRabbit)
    bloque = _bloque_servicio(COMPOSE.read_text(encoding="utf-8"), "app")
    assert '"127.0.0.1:8010:8000"' in bloque
    assert "secrets:/mnt/data/appdata/orbit/secrets:ro" in bloque
    assert 'user: "0:0"' in bloque
    assert "ORBIT_PG_HOST: db" in bloque


def test_dockerfile_instala_con_lockfile_congelado():
    texto = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv.lock" in texto
    assert "uv sync --frozen" in texto
    assert "uvicorn" in texto
    assert "app.main:app" in texto
