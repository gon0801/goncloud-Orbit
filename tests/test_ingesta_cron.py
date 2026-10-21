"""Cron de la ingesta diaria (ingesta-01 A.4).

Sobre un crontab de ejemplo con el estado de hoy (lineas Orbit viejas dentro
del bloque, las sueltas de avisos-campana / impulso-vigia / jev-senales
fuera de el, mas accounting, precio y spapi), el instalador documentado en
docs/DEPLOY.md: deja cada linea de la tabla una sola vez, borra las viejas
(07:10 / 07:20 / 07:25 de metricas), no toca las ajenas y agenda el diario
en los minutos de HORARIOS_INGESTA (la misma constante que usan los textos
de reintento del diario, app.ads.diario).
"""

import re
import subprocess
from pathlib import Path

from app.ads.diario import HORARIOS_INGESTA

RAIZ = Path(__file__).resolve().parents[1]

# Un crontab real de "hoy": bloque viejo (con las tres de metricas), las
# sueltas de avisos-campana / impulso-vigia / jev-senales, y lineas ajenas.
CRONTAB_DE_HOY = "\n".join(
    [
        "# === Orbit (ORBIT 03 / 4.2) ===",
        "# job_key=ingest:structure",
        (
            "45 6 * * * docker exec orbit-app-1 python -m app.cli ingest structure"
            " >> /mnt/data/appdata/orbit/logs/ingest-structure.log 2>&1"
        ),
        "# job_key=ingest:metrics",
        (
            '10 7 * * * FECHA=$(date -u -d "31 days ago" +\\%F)'
            ' FECHA_FIN=$(date -u -d "1 day ago" +\\%F) && docker exec orbit-app-1'
            ' python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN"'
            " >> /mnt/data/appdata/orbit/logs/ingest-metrics.log 2>&1"
        ),
        "# job_key=ingest:metrics:productos",
        (
            '20 7 * * * FECHA=$(date -u -d "31 days ago" +\\%F)'
            ' FECHA_FIN=$(date -u -d "1 day ago" +\\%F) && docker exec orbit-app-1'
            ' python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN"'
            " --productos >> /mnt/data/appdata/orbit/logs/ingest-productos.log 2>&1"
        ),
        "# job_key=ingest:metrics:placements",
        (
            '25 7 * * * FECHA=$(date -u -d "31 days ago" +\\%F)'
            ' FECHA_FIN=$(date -u -d "1 day ago" +\\%F) && docker exec orbit-app-1'
            ' python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN"'
            " --placements >> /mnt/data/appdata/orbit/logs/ingest-placements.log 2>&1"
        ),
        "# job_key=ads_optimizer:amazon_us",
        (
            "40 8 * * * docker exec orbit-app-1 python -m app.cli cycle --platform amazon_us"
            " >> /mnt/data/appdata/orbit/logs/optimizer.log 2>&1"
        ),
        "# job_key=ads-salud",
        (
            "*/10 10-12 * * * docker exec orbit-app-1 python -m app.cli ads-salud"
            " >> /mnt/data/appdata/orbit/logs/ads-salud.log 2>&1"
        ),
        "# accounting",
        "17 3 * * * /usr/bin/accounting-job >> /var/log/accounting.log 2>&1",
        (
            "5 8 * * * /usr/bin/flock -n /tmp/avisos-campana.lock docker exec orbit-app-1"
            " python -m app.cli avisos-campana"
            " >> /mnt/data/appdata/orbit/logs/avisos-campana.log 2>&1"
        ),
        (
            "20 8 * * * /usr/bin/flock -n /tmp/impulso-vigia.lock docker exec orbit-app-1"
            " python -m app.cli impulso-vigia"
            " >> /mnt/data/appdata/orbit/logs/impulso-vigia.log 2>&1"
        ),
        (
            "30 9,21 * * * /usr/bin/flock -n /tmp/jev-senales.lock docker exec orbit-app-1"
            " python -m app.cli jev-senales --aplicar"
            " >> /mnt/data/appdata/orbit/logs/jev-senales.log 2>&1"
        ),
        (
            "10 13 * * * /usr/bin/flock -n /tmp/precio-spapi.lock docker exec orbit-app-1"
            " python -m app.cli precio --platform amazon_mx --platform amazon_us"
            " >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1"
        ),
        (
            "30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1"
            " python -m app.cli spapi_vigilante"
            " >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1"
        ),
    ]
)


def _instalador(deploy: str) -> tuple[list[str], str]:
    lineas = deploy.splitlines()
    inicio = next(i for i, ln in enumerate(lineas) if ln.startswith("ORBIT_BLOCK=$(cat <<'CRON'"))
    fin = next(i for i in range(inicio, len(lineas)) if lineas[i] == "CRON")
    bloque = [ln for ln in lineas[inicio + 1 : fin] if ln.strip()]
    fila = next(ln for ln in lineas if "crontab -u gon -l 2>/dev/null | grep -v" in ln)
    filtros = fila.split("crontab -u gon -l 2>/dev/null | ", 1)[1].split(" ; printf", 1)[0]
    return bloque, filtros


def _reinstala(deploy: str, crontab_actual: str) -> str:
    bloque, filtros = _instalador(deploy)
    resultado = subprocess.run(
        ["bash", "-c", f"cat | {filtros}"],
        input=crontab_actual,
        text=True,
        capture_output=True,
        check=True,
    )
    return resultado.stdout + "\n".join(bloque) + "\n"


def _deploy() -> str:
    return (RAIZ / "docs/DEPLOY.md").read_text()


def test_cada_linea_del_bloque_queda_una_sola_vez_y_las_viejas_se_boran():
    reinstalado = _reinstala(_deploy(), CRONTAB_DE_HOY)
    bloque, _ = _instalador(_deploy())

    # cada linea de comando del bloque queda exactamente una vez
    for linea in bloque:
        if linea.startswith("#"):
            continue
        assert reinstalado.count(linea + "\n") == 1, f"linea duplicada o ausente: {linea}"

    # las viejas de metricas (07:10/07:20/07:25) desaparecen por completo
    assert "app.cli ingest metrics" not in reinstalado
    assert "ingest-metrics.log" not in reinstalado
    assert "ingest-productos.log" not in reinstalado
    assert "ingest-placements.log" not in reinstalado

    # nada del header viejo queda duplicado
    assert reinstalado.count("# === Orbit (") == 1


def test_las_lineas_ajenas_sobreviven_intactas():
    reinstalado = _reinstala(_deploy(), CRONTAB_DE_HOY)
    assert "# accounting\n17 3 * * * /usr/bin/accounting-job" in reinstalado
    assert "precio --platform amazon_mx --platform amazon_us" in reinstalado
    assert "spapi_vigilante" in reinstalado
    # las sueltas de avisos/impulso/jev se funden con las del bloque: una vez
    for comando in ("avisos-campana", "impulso-vigia", "jev-senales --aplicar"):
        lineas = [
            ln
            for ln in reinstalado.splitlines()
            if f"app.cli {comando}" in ln and not ln.startswith("#")
        ]
        assert len(lineas) == 1, f"{comando}: {len(lineas)} lineas"


def test_el_diario_se_agenda_en_los_minutos_de_HORARIOS_INGESTA():
    reinstalado = _reinstala(_deploy(), CRONTAB_DE_HOY)
    diario = [
        ln for ln in reinstalado.splitlines() if "app.cli ingest diario" in ln and ln[:1].isdigit()
    ]
    assert len(diario) == 3, "una linea por horario de HORARIOS_INGESTA"
    esperados = {f"{int(hhmm[3:5])} {int(hhmm[0:2])}" for hhmm in HORARIOS_INGESTA}
    vistos = {re.match(r"^(\d+ \d+)", ln).group(1) for ln in diario}
    assert vistos == esperados, "los minutos del crontab deben salir de HORARIOS_INGESTA"


def test_la_tabla_de_horarios_documenta_los_mismos_minutos():
    deploy = _deploy()
    assert f"| {', '.join(HORARIOS_INGESTA)} | `ingest:diario` |" in deploy


def test_cada_linea_del_bloque_lleva_su_job_key():
    bloque, _ = _instalador(_deploy())
    for i, linea in enumerate(bloque):
        if linea.startswith("#"):
            continue
        assert bloque[i - 1].startswith("# job_key="), f"sin job_key: {linea}"


def test_reinstalar_dos_veces_es_idempotente():
    una = _reinstala(_deploy(), CRONTAB_DE_HOY)
    dos = _reinstala(_deploy(), una)
    assert dos == una
