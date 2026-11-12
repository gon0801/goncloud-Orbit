"""Cron de la ingesta diaria (ingesta-01 A.4).

Sobre un crontab de ejemplo con el estado viejo (lineas Orbit dentro del bloque,
mas accounting, precio y spapi fuera), `tools/despliegue/cron_orbit.sh instala`
reemplaza SOLO el bloque Orbit por `tools/despliegue/crontab-orbit.txt`: deja
cada linea canonica una sola vez, borra las viejas de metricas (07:10 / 07:20 /
07:25), no toca las ajenas y agenda el diario en los minutos de HORARIOS_INGESTA
(la misma constante que usan los textos de reintento del diario, app.ads.diario).
"""

import os
import re
import subprocess
from pathlib import Path

from app.ads.diario import HORARIOS_INGESTA

RAIZ = Path(__file__).resolve().parents[1]
CRON_ORBIT = RAIZ / "tools/despliegue/cron_orbit.sh"
BLOQUE = RAIZ / "tools/despliegue/crontab-orbit.txt"

CRONTAB_FALSO = """#!/usr/bin/env bash
F=${ORBIT_FAKE_CRONTAB:?}
[ "$1" = -u ] && shift 2
case "$1" in
  -l) [ -f "$F" ] || { echo "no crontab" >&2; exit 1; }; cat "$F" ;;
  -) cat > "$F" ;;
  *) cp "$1" "$F" ;;
esac
"""

# Un crontab viejo: el bloque Orbit con las tres de metricas y las lineas sin
# envolver, mas lineas ajenas fuera del bloque.
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
        "# === Accounting ===",
        "17 3 * * * /usr/bin/accounting-job >> /var/log/accounting.log 2>&1",
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


def _bloque() -> list[str]:
    return [ln for ln in BLOQUE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _prepara(tmp_path: Path, actual: str) -> tuple[dict[str, str], Path]:
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "crontab").write_text(CRONTAB_FALSO, encoding="utf-8", newline="\n")
    (bin_ / "crontab").chmod(0o755)
    avisa = bin_ / "cron-avisa"
    avisa.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    avisa.chmod(0o755)
    crontab = tmp_path / "crontab"
    crontab.write_text(actual, encoding="utf-8", newline="\n")
    entorno = {
        **os.environ,
        "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
        "ORBIT_FAKE_CRONTAB": str(crontab),
        "ORBIT_CRON_ARCHIVO": str(tmp_path / "archive"),
        "ORBIT_CRON_AVISA": str(avisa),
    }
    return entorno, crontab


def _instala(tmp_path: Path, actual: str) -> str:
    entorno, crontab = _prepara(tmp_path, actual)
    resultado = subprocess.run(
        ["bash", str(CRON_ORBIT), "instala"],
        env=entorno,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    return crontab.read_text(encoding="utf-8")


def test_cada_linea_del_bloque_queda_una_sola_vez_y_las_viejas_se_boran(tmp_path):
    reinstalado = _instala(tmp_path, CRONTAB_DE_HOY)
    bloque = _bloque()

    for linea in bloque:
        if linea.startswith("#"):
            continue
        assert reinstalado.count(linea + "\n") == 1, f"linea duplicada o ausente: {linea}"

    assert "app.cli ingest metrics" not in reinstalado
    assert "ingest-metrics.log" not in reinstalado
    assert "ingest-productos.log" not in reinstalado
    assert "ingest-placements.log" not in reinstalado
    assert reinstalado.count("# === Orbit (") == 1


def test_las_lineas_ajenas_sobreviven_intactas(tmp_path):
    reinstalado = _instala(tmp_path, CRONTAB_DE_HOY)
    assert "# === Accounting ===\n17 3 * * * /usr/bin/accounting-job" in reinstalado
    assert "precio --platform amazon_mx --platform amazon_us" in reinstalado
    assert "spapi_vigilante" in reinstalado
    for comando in ("avisos-campana", "impulso-vigia", "jev-senales --aplicar"):
        lineas = [
            ln
            for ln in reinstalado.splitlines()
            if f"app.cli {comando}" in ln and not ln.startswith("#")
        ]
        assert len(lineas) == 1, f"{comando}: {len(lineas)} lineas"


def test_el_diario_se_agenda_en_los_minutos_de_HORARIOS_INGESTA(tmp_path):
    reinstalado = _instala(tmp_path, CRONTAB_DE_HOY)
    diario = [
        ln for ln in reinstalado.splitlines() if "app.cli ingest diario" in ln and ln[:1].isdigit()
    ]
    assert len(diario) == 3, "una linea por horario de HORARIOS_INGESTA"
    esperados = {f"{int(hhmm[3:5])} {int(hhmm[0:2])}" for hhmm in HORARIOS_INGESTA}
    vistos = {re.match(r"^(\d+ \d+)", ln).group(1) for ln in diario}
    assert vistos == esperados, "los minutos del crontab deben salir de HORARIOS_INGESTA"


def test_los_reintentos_y_el_primer_horario_llevan_solo_faltantes(tmp_path):
    reinstalado = _instala(tmp_path, CRONTAB_DE_HOY)
    diario = [
        ln for ln in reinstalado.splitlines() if "app.cli ingest diario" in ln and ln[:1].isdigit()
    ]
    assert len(diario) == len(HORARIOS_INGESTA)
    for ln in diario:
        assert "--solo-faltantes" in ln, f"sin --solo-faltantes: {ln}"


def test_la_tabla_de_horarios_documenta_los_mismos_minutos():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text(encoding="utf-8")
    assert f"| {', '.join(HORARIOS_INGESTA)} | `ingest:diario` |" in deploy


def test_la_tabla_documenta_ads_salud_que_instala_el_bloque():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text(encoding="utf-8")
    lineas = [ln for ln in _bloque() if "app.cli ads-salud" in ln and not ln.startswith("#")]
    assert len(lineas) == 1, "el bloque canonico debe traer una sola linea de ads-salud"
    assert lineas[0].startswith("*/10 10-12 * * *")
    filas = [ln for ln in deploy.splitlines() if ln.startswith("| ") and "ads-salud" in ln]
    assert len(filas) == 1, "la tabla de crons debe documentar ads-salud una sola vez"
    assert "`python -m app.cli ads-salud`" in filas[0]


def test_cada_linea_del_bloque_lleva_su_job_key():
    bloque = _bloque()
    for i, linea in enumerate(bloque):
        if linea.startswith("#"):
            continue
        assert bloque[i - 1].startswith("# job_key="), f"sin job_key: {linea}"


def test_reinstalar_dos_veces_es_idempotente(tmp_path):
    una = _instala(tmp_path, CRONTAB_DE_HOY)
    entorno, crontab = _prepara(tmp_path, una)
    resultado = subprocess.run(
        ["bash", str(CRON_ORBIT), "instala"],
        env=entorno,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert "sin cambios" in resultado.stdout
    assert crontab.read_text(encoding="utf-8") == una
