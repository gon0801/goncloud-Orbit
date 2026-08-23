"""Tests del CLI de operacion `python -m app.cli {ingest,cycle}` (ORBIT 03,
task 3.3).

UNITARIOS (sin Postgres): el CLI es un ENVOLTORIO DELGADO — cada subcomando
invoca EXACTAMENTE el mismo camino que ya existe, asi que se prueba con
monkeypatch al punto de entrada:

1. `cycle` llama al MISMO orquestador (`app.cycle.corre_ciclo`) con el MISMO
   job_key que el cron de 4.2 (`ads_optimizer:<platform>` — el claim del lock
   es compartido; UNA fuente: `app.cycle.job_key_de`).
2. `ingest` delega a los mains de los pipelines de app/ads (structure y
   reports) sin duplicar ninguna regla; propaga su exit code.
3. Fail-closed: sin DSN -> exit 2 con mensaje claro; fallo -> exit 1 con el
   error scrubbado (jamas un DSN); CicloOcupado -> exit 0 (condicion de
   concurrencia esperada, el claim ya lo esta corriendo).
4. `--help` documenta ambos subcomandos.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import cli
from app import cycle as ciclo

FALSO_RESULTADO = ciclo.ResultadoCiclo(cycle_id=42, status="done", decisions_count=4, notes="{}")


class _FakeConn:
    """Conexion falsa: el test verifica que el CLI la pasa tal cual al
    orquestador (el CLI no debe tocar su contenido). `close` existe porque el
    CLI SIEMPRE cierra la conexion en un finally."""

    def close(self) -> None:
        pass


def _captura(monkeypatch) -> dict:
    """Monkeypatch de corre_ciclo: captura kwargs y devuelve un resultado."""
    capturado: dict = {}

    def _fake(conn, **kwargs):
        capturado["conn"] = conn
        capturado.update(kwargs)
        return FALSO_RESULTADO

    monkeypatch.setattr(ciclo, "corre_ciclo", _fake)
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    return capturado


# ---------------------------------------------------------------------------
# 1. cycle: llama al mismo orquestador con el mismo job_key que el cron
# ---------------------------------------------------------------------------


def test_cli_cycle_llama_al_mismo_orquestador(monkeypatch, capsys):
    capturado = _captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit_decide:secreta@127.0.0.1:5432/orbit")

    codigo = cli.main(
        ["cycle", "--platform", "amazon_us", "--decided-at", "2026-08-22T12:00:00+00:00"]
    )

    assert codigo == 0
    # el MISMO orquestador (app.cycle.corre_ciclo), no una copia de la logica
    assert isinstance(capturado["conn"], _FakeConn)
    assert capturado["platform"] == "amazon_us"
    assert capturado["decided_at"] == dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
    assert capturado["owner"]  # default hostname:pid

    # el MISMO job_key que el cron de 4.2 (ads_optimizer:<platform>): el claim
    # del lock es COMPARTIDO entre cron y CLI, una sola fuente (regla 1)
    assert ciclo.job_key_de("amazon_us") == "ads_optimizer:amazon_us"
    out = capsys.readouterr().out
    assert "ads_optimizer:amazon_us" in out
    assert "42" in out and "done" in out  # el resumen sin secretos


def test_cli_cycle_owner_explicito_y_decided_at_por_default(monkeypatch, capsys):
    capturado = _captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit_decide:secreta@127.0.0.1:5432/orbit")

    codigo = cli.main(["cycle", "--platform", "amazon_mx", "--owner", "cron-01"])

    assert codigo == 0
    assert capturado["platform"] == "amazon_mx"
    assert capturado["owner"] == "cron-01"
    assert capturado["decided_at"].tzinfo is not None  # now() tz-aware
    assert ciclo.job_key_de("amazon_mx") == "ads_optimizer:amazon_mx"


def test_cli_cycle_plataforma_fuera_del_vocabulario_rechazada(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["cycle", "--platform", "meli"])
    assert exc.value.code == 2  # argparse: choice invalido


def test_cli_cycle_decided_at_naive_rechazado(monkeypatch, capsys):
    """El reloj de decisiones exige tz-aware (contrato de corre_ciclo): un
    ISO naive evaluaria segun la TZ local — se rechaza con mensaje claro."""
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit_decide:secreta@127.0.0.1:5432/orbit")

    codigo = cli.main(["cycle", "--platform", "amazon_us", "--decided-at", "2026-08-22T12:00:00"])

    assert codigo == 2
    assert "tz-aware" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 2. Fail-closed: DSN ausente, fallo scrubbado, CicloOcupado -> 0
# ---------------------------------------------------------------------------


def test_cli_cycle_sin_dsn_fail_closed(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_DECIDE", raising=False)
    codigo = cli.main(["cycle", "--platform", "amazon_us"])
    assert codigo == 2
    assert "ORBIT_DSN_DECIDE" in capsys.readouterr().err


def test_cli_cycle_fallo_de_conexion_sin_filtrar_dsn(monkeypatch, capsys):
    from app.db import OrbitDbError

    def _reventar(dsn):
        raise OrbitDbError(
            "no se pudo conectar a la base de datos: postgresql://***REDACTED***@..."
        )

    monkeypatch.setattr(cli, "connect", _reventar)
    monkeypatch.setenv(
        "ORBIT_DSN_DECIDE", "postgresql://orbit_decide:SUPER_SECRETA@127.0.0.1:5432/orbit"
    )

    codigo = cli.main(["cycle", "--platform", "amazon_us"])

    assert codigo == 1
    err = capsys.readouterr().err
    assert "no se pudo conectar" in err
    assert "SUPER_SECRETA" not in err  # jamas el secreto en la salida


def test_cli_cycle_ocupado_sale_cero(monkeypatch, capsys):
    """CicloOcupado NO es un fallo de esta corrida: el claim del lock ya lo
    esta corriendo otro proceso (cron + manual coincidiendo). exit 0 para que
    el cron no almee por una condicion esperada de concurrencia."""

    def _ocupado(conn, **kwargs):
        raise ciclo.CicloOcupado("lock vigente de otro owner para ads_optimizer:amazon_us")

    monkeypatch.setattr(ciclo, "corre_ciclo", _ocupado)
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit_decide:secreta@127.0.0.1:5432/orbit")

    codigo = cli.main(["cycle", "--platform", "amazon_us"])

    assert codigo == 0
    assert "ya en curso" in capsys.readouterr().err


def test_cli_cycle_args_extra_rechazados(monkeypatch, capsys):
    """Regresion (hallazgo reviewer 3.2, baja): el ciclo ESCRIBE decisiones;
    un flag mal tipeado (p.ej. --decided-at-time) NO puede ignorarse en
    silencio y correr con el reloj equivocado: es un error del operador."""
    capturado = _captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit_decide:secreta@127.0.0.1:5432/orbit")

    codigo = cli.main(
        ["cycle", "--platform", "amazon_us", "--decided-at-time", "2026-08-22T12:00:00+00:00"]
    )

    assert codigo == 2
    assert "desconocidos" in capsys.readouterr().err
    assert not capturado  # el orquestador JAMAS se llamo


def test_cli_ingest_structure_args_extra_rechazados(monkeypatch, capsys):
    """Regresion (hallazgo reviewer 3.2, baja): `ingest structure` no define
    ninguna opcion; tokens extra ahi son un error del operador, no basura
    ignorada en silencio."""
    llamadas: list = []

    def _main_estructura():
        llamadas.append("structure")
        return 0

    monkeypatch.setattr("app.ads.structure.main", _main_estructura)
    codigo = cli.main(["ingest", "structure", "--fecha", "2026-08-20"])

    assert codigo == 2
    assert "desconocidos" in capsys.readouterr().err
    assert llamadas == []  # el pipeline JAMAS se llamo


def test_cli_cycle_imprime_motivo_si_no_done(monkeypatch, capsys):
    """Observacion reviewer 3.2: el operador manual del ciclo necesita el
    PORQUE de un skipped/degraded; el resumen imprime el motivo_skip del
    notes sin tragar el JSON entero."""

    def _skipped(conn, **kwargs):
        return ciclo.ResultadoCiclo(
            cycle_id=7,
            status="skipped",
            decisions_count=0,
            notes='{"skips": {}, "motivo_skip": "escalera_off", "detalle": "escalera global off"}',
        )

    monkeypatch.setattr(ciclo, "corre_ciclo", _skipped)
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit_decide:secreta@127.0.0.1:5432/orbit")

    codigo = cli.main(["cycle", "--platform", "amazon_us"])

    assert codigo == 0
    out = capsys.readouterr().out
    assert "status=skipped" in out
    assert "motivo: escalera_off" in out


# ---------------------------------------------------------------------------
# 3. ingest: delega a los pipelines de app/ads (el mismo camino, cero logica)
# ---------------------------------------------------------------------------


def test_cli_ingest_structure_delega_al_pipeline(monkeypatch, capsys):
    llamadas: list = []

    def _main_estructura():
        llamadas.append("structure")
        return 0

    monkeypatch.setattr("app.ads.structure.main", _main_estructura)
    codigo = cli.main(["ingest", "structure"])
    assert codigo == 0
    assert llamadas == ["structure"]


def test_cli_ingest_metrics_delega_al_pipeline_con_sus_args(monkeypatch, capsys):
    llamadas: list = []

    def _main_reportes(argv=None):
        llamadas.append(argv)
        return 7  # exit code del pipeline propagado tal cual

    monkeypatch.setattr("app.ads.reports.main", _main_reportes)
    codigo = cli.main(["ingest", "metrics", "--fecha", "2026-08-20"])
    assert codigo == 7  # el envoltorio propaga el exit del pipeline
    assert llamadas == [["--fecha", "2026-08-20"]]


def test_cli_ingest_pipeline_desconocido_rechazado(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["ingest", "algo"])
    assert exc.value.code == 2  # argparse: choice invalido


# ---------------------------------------------------------------------------
# 4. --help documenta ambos subcomandos
# ---------------------------------------------------------------------------


def test_cli_help_documenta_ingest_y_cycle(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "ingest" in out and "cycle" in out


def test_cli_cycle_help_documenta_plataforma(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["cycle", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--platform" in out and "amazon_us" in out


def test_cli_ingest_help_documenta_pipelines(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["ingest", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "structure" in out and "metrics" in out
