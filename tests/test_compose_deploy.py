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
    operativas esta sellado — reordenar es inocuo, agregar o quitar NO.
    Re-sello 4.1 (env por servicio): db ya NO hereda el .env completo
    (llevaba hasta ORBIT_DSN_ADMIN); solo POSTGRES_* por interpolacion."""
    bloque = _bloque_servicio(COMPOSE.read_text(encoding="utf-8"), "db")
    lineas = {
        ln.strip() for ln in bloque.splitlines() if ln.strip() and not ln.strip().startswith("#")
    }
    esperadas = {
        "image: postgres:16",
        "restart: unless-stopped",
        "environment:",
        "POSTGRES_DB: orbit",
        "POSTGRES_USER: ${POSTGRES_USER}",
        "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}",
        "volumes:",
        "- pgdata:/var/lib/postgresql/data",
        "ports:",
        '- "127.0.0.1:5432:5432"',
    }
    assert lineas == esperadas, (
        f"servicio db cambio (re-sello 4.1 2026-08-27; diff simetrico): {lineas ^ esperadas}"
    )


def test_compose_db_no_recibe_ningun_dsn():
    """4.1 (env por servicio): el DSN admin vive SOLO en app. db no tiene
    env_file ni ningun ORBIT_DSN_* — antes heredaba TODO el .env (incluido
    ORBIT_DSN_ADMIN) por env_file. Regla 9: contra el compose viejo (env_file
    en db) este candado reventaba."""
    bloque = _bloque_servicio(COMPOSE.read_text(encoding="utf-8"), "db")
    operativas = "\n".join(ln for ln in bloque.splitlines() if not ln.strip().startswith("#"))
    assert "env_file" not in operativas, (
        "db hereda el .env completo: el DSN admin NO es solo de app"
    )
    assert "ORBIT_DSN" not in operativas, "db recibe DSNs de Orbit: env por servicio roto"
    # Y el otro lado del contrato: app recibe SOLO los 4 DSN de servicio por
    # interpolacion (CodeRabbit Major PR #36: env_file inyectaba TODO el
    # .env — incluido ORBIT_DSN_TEST, cuyo rol tiene ADMIN OPTION sobre
    # app_* = escritura en prod desde dentro del contenedor).
    bloque_app = _bloque_servicio(COMPOSE.read_text(encoding="utf-8"), "app")
    operativas_app = "\n".join(
        ln for ln in bloque_app.splitlines() if not ln.strip().startswith("#")
    )
    assert "env_file" not in operativas_app, "app hereda TODO el .env por env_file"
    for svc in ("INGEST", "DECIDE", "READ", "ADMIN"):
        assert f"ORBIT_DSN_{svc}: ${{ORBIT_DSN_{svc}}}" in operativas_app
    assert "ORBIT_DSN_TEST" not in operativas_app, (
        "ORBIT_DSN_TEST no entra al contenedor (ADMIN OPTION sobre app_*)"
    )
    assert "POSTGRES_PASSWORD" not in operativas_app


def test_runbook_documenta_la_ceremonia_del_uid_10001():
    """El chown de secrets/ a 10001 es operacion del SERVER: si el runbook
    no la documenta, el proximo deploy la pierde. Candado del mismo patron
    que el cron de metricas (texto de DEPLOY.md). Asercion del COMANDO
    exacto (CodeRabbit PR #36: con 'chown' + 'secrets' sueltos pasaba en
    verde aunque la ceremonia se borrara — la rotacion del token tambien
    los menciona)."""
    texto = (RAIZ / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    assert "chown -R 10001:10001 /mnt/data/appdata/orbit/secrets" in texto, (
        "DEPLOY.md no documenta el chown inicial de secrets/ a 10001"
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
    assert "ORBIT_PG_HOST: db" in bloque


def test_compose_app_corre_non_root_con_uid_de_secrets():
    """4.1: la app ya NO corre como root (user 0:0 era residual aceptado de
    ORBIT 03: los secrets eran root 0600). Resuelto en el SERVER: secrets/
    pasan a uid 10001 (mismos 600/700) y el contenedor corre con ESE uid —
    mismo acceso, cero root. Regla 9: contra el compose viejo (user 0:0)
    este candado reventaba."""
    bloque = _bloque_servicio(COMPOSE.read_text(encoding="utf-8"), "app")
    assert 'user: "0:0"' not in bloque, "la app sigue corriendo como root"
    assert 'user: "10001:10001"' in bloque, (
        "uid distinto del dueno de secrets/ (10001): el contenedor no podria leerlos"
    )


def test_dockerfile_instala_con_lockfile_congelado():
    texto = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv.lock" in texto
    assert "uv sync --frozen" in texto
    assert "uvicorn" in texto
    assert "app.main:app" in texto
