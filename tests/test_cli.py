"""Tests del CLI de operacion `python -m app.cli {ingest,cycle,goals}` (ORBIT
03 task 3.3; `goals set` llego en ORBIT 04 3.2).

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
from decimal import Decimal

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
    # anclado a la SALIDA del CLI (no una tautologia sobre job_key_de):
    # el resumen debe reportar el job_key del orquestador (CodeRabbit)
    assert ciclo.job_key_de("amazon_mx") in capsys.readouterr().out


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
    from app.redaction import redact_dsn

    def _reventar(dsn):
        # como el connect real: redact_dsn REGISTRA la password antes del
        # error; el mensaje lleva el DSN CRUDO para que el scrub del camino
        # de salida sea quien lo limpie (CodeRabbit: el fake pre-redactado
        # no ejercitaba nada)
        redact_dsn(dsn)
        raise OrbitDbError(f"no se pudo conectar a la base de datos: {dsn}")

    monkeypatch.setattr(cli, "connect", _reventar)
    monkeypatch.setenv(
        "ORBIT_DSN_DECIDE", "postgresql://orbit_decide:SUPER_SECRETA@127.0.0.1:5432/orbit"
    )

    codigo = cli.main(["cycle", "--platform", "amazon_us"])

    assert codigo == 1
    err = capsys.readouterr().err
    assert "no se pudo conectar" in err
    assert "SUPER_SECRETA" not in err  # jamas el secreto en la salida
    assert "REDACTED" in err  # y la redaccion OCURRIO (no solo ausencia)


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


def test_cli_ingest_costs_delega_al_pipeline_con_sus_args(monkeypatch, capsys):
    """ORBIT 06 0.1: `ingest costs --sqlite RUTA` despacha a app.costs.main
    con sus args tal cual (mismo patron que metrics: cero logica aqui)."""
    llamadas: list = []

    def _main_costos(argv=None):
        llamadas.append(argv)
        return 4  # exit code del pipeline propagado tal cual

    monkeypatch.setattr("app.costs.main", _main_costos)
    codigo = cli.main(["ingest", "costs", "--sqlite", "/tmp/snap.db"])
    assert codigo == 4
    assert llamadas == [["--sqlite", "/tmp/snap.db"]]


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


# ---------------------------------------------------------------------------
# 5. goals set (ORBIT 04 3.2): DESPACHA a app/goals_write.edita_goal, cero SQL
# ---------------------------------------------------------------------------

FILA_GOAL = {
    "id": 7,
    "scope": "platform",
    "ad_entity_id": None,
    "platform": "amazon_us",
    "target_acos_pct": "20.00",
    "bid_floor": "0.4000",
    "bid_ceiling": "2.5000",
    "bid_currency": "USD",
    "harvest_campaign_id": None,
    "harvest_ad_group_id": None,
    "harvest_default_bid": None,
    "enabled": True,
    "mode": "shadow",
    "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    "updated_at": dt.datetime(2026, 8, 26, 10, 0, tzinfo=dt.UTC),
}


def _goal_captura(monkeypatch, resultado=FILA_GOAL, error=None):
    """Spy de goals_write.edita_goal + connect falso: el CLI de goals JAMAS
    abre DSN real en los tests unitarios (regla 9 del plan de tests)."""
    from app import goals_write

    capturado: dict = {}

    def _edita(conn, goal_id, *, updated_at, **kw):
        capturado["conn"] = conn
        capturado["goal_id"] = goal_id
        capturado["updated_at"] = updated_at
        capturado.update(kw)
        if error is not None:
            raise error
        return resultado

    monkeypatch.setattr(goals_write, "edita_goal", _edita)
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    return capturado


def test_cli_goals_set_sin_dsn_admin_fail_closed(monkeypatch, capsys):
    """DoD: sin ORBIT_DSN_ADMIN -> mensaje claro + exit 2 (fail-closed, patron
    _cycle); edita_goal JAMAS se llama."""
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    capturado: dict = {}
    monkeypatch.setattr(
        "app.goals_write.edita_goal",
        lambda *a, **kw: capturado.setdefault("llamado", True),
    )
    codigo = cli.main(["goals", "set", "7", "--target", "20"])
    assert codigo == 2
    assert "ORBIT_DSN_ADMIN" in capsys.readouterr().err
    assert not capturado


def test_cli_goals_set_llama_a_edita_goal(monkeypatch, capsys):
    """Camino unico (regla 1): `goals set` DESPACHA a goals_write.edita_goal
    con el goal_id, los campos parseados y updated_at now-UTC; imprime la fila
    resultante con updated_at VISIBLE (una linea por campo)."""
    capturado = _goal_captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")

    codigo = cli.main(
        [
            "goals",
            "set",
            "7",
            "--target",
            "20",
            "--enabled",
            "true",
            "--floor",
            "0.40",
            "--ceiling",
            "2.50",
            "--harvest-campaign",
            "9002",
            "--harvest-ad-group",
            "9102",
            "--harvest-bid",
            "1.00",
        ]
    )

    assert codigo == 0
    assert capturado["goal_id"] == 7
    assert capturado["target_acos_pct"] == Decimal("20")
    assert capturado["enabled"] is True
    assert capturado["bid_floor"] == Decimal("0.40")
    assert capturado["bid_ceiling"] == Decimal("2.50")
    assert capturado["harvest_campaign_id"] == "9002"
    assert capturado["harvest_ad_group_id"] == "9102"
    assert capturado["harvest_default_bid"] == Decimal("1.00")
    assert capturado["harvest_limpia"] is False
    assert capturado["updated_at"].tzinfo is not None  # now UTC
    assert isinstance(capturado["conn"], _FakeConn)
    out = capsys.readouterr().out
    assert "updated_at=2026-08-26T10:00:00+00:00" in out
    assert "target_acos_pct=20.00" in out


def test_cli_goals_set_harvest_limpia(monkeypatch):
    capturado = _goal_captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    codigo = cli.main(["goals", "set", "7", "--harvest-limpia"])
    assert codigo == 0
    assert capturado["harvest_limpia"] is True


def test_cli_goals_set_sin_campos_es_uso_invalido(monkeypatch, capsys):
    """`goals set 7` sin NINGUN campo: edicion vacia = error del operador
    (exit 2), jamas un UPDATE que solo toque updated_at."""
    capturado = _goal_captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    codigo = cli.main(["goals", "set", "7"])
    assert codigo == 2
    assert "al menos un campo" in capsys.readouterr().err
    assert not capturado  # edita_goal JAMAS se llamo


def test_cli_goals_set_argumentos_invalidos_exit_2(monkeypatch, capsys):
    """Decimal no numerico / enabled fuera de true|false / goal_id no entero:
    error de USO -> exit 2 con mensaje claro de argparse (patron del repo)."""
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    for argv in (
        ["goals", "set", "7", "--target", "veinte"],
        ["goals", "set", "7", "--enabled", "si"],
        ["goals", "set", "siete", "--target", "20"],
        ["goals", "set", "7", "--floor", "0,40"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2, f"{argv} deberia ser exit 2"


def test_cli_goals_set_args_extra_rechazados(monkeypatch, capsys):
    """Tokens extra = error del operador (patron cycle): ni se conecta."""
    capturado = _goal_captura(monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    codigo = cli.main(["goals", "set", "7", "--targe", "20"])
    assert codigo == 2
    assert "desconocidos" in capsys.readouterr().err
    assert not capturado


def test_cli_goals_set_goal_invalido_exit_2(monkeypatch, capsys):
    """GoalInvalido (validacion) = USO invalido: exit 2 con el motivo en
    espanol (eleccion sellada en el docstring de goals_write)."""
    from app import goals_write

    _goal_captura(monkeypatch, error=goals_write.GoalInvalido("bid_floor 3.00 > bid_ceiling 2.50"))
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    codigo = cli.main(["goals", "set", "7", "--floor", "3.00"])
    assert codigo == 2
    err = capsys.readouterr().err
    assert "bid_floor" in err


def test_cli_goals_set_goal_inexistente_exit_1(monkeypatch, capsys):
    """GoalInexistente = fallo contra la base (el id no esta): exit 1, patron
    de error de corrida (no de uso)."""
    from app import goals_write

    _goal_captura(monkeypatch, error=goals_write.GoalInexistente("goal 7 no existe"))
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    codigo = cli.main(["goals", "set", "7", "--target", "20"])
    assert codigo == 1
    assert "no existe" in capsys.readouterr().err


def test_cli_goals_set_fallo_de_conexion_scrubbed(monkeypatch, capsys):
    from app.db import OrbitDbError
    from app.redaction import redact_dsn

    def _reventar(dsn):
        redact_dsn(dsn)
        raise OrbitDbError(f"no se pudo conectar a la base de datos: {dsn}")

    monkeypatch.setattr(cli, "connect", _reventar)
    monkeypatch.setenv(
        "ORBIT_DSN_ADMIN", "postgresql://orbit_admin:SUPER_SECRETA@127.0.0.1:5432/orbit"
    )
    codigo = cli.main(["goals", "set", "7", "--target", "20"])
    assert codigo == 1
    err = capsys.readouterr().err
    assert "SUPER_SECRETA" not in err


def test_cli_goals_set_harvest_cadena_vacia_exit_2(monkeypatch, capsys):
    """#1 (regresion review 3.2): `--harvest-campaign ''` cuenta como
    "presente" para la terna harvest (regla 3: faltante no es cadena vacia).
    Lo rechaza el camino UNICO (goals_write) ANTES de leer la fila — edita_goal
    va REAL (sin mock): si el guard no disparara, la conexion falsa reventaria
    y el exit seria 1 generico, no 2."""
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    for argv in (
        ["goals", "set", "7", "--harvest-campaign", ""],
        ["goals", "set", "7", "--harvest-ad-group", "   "],
    ):
        codigo = cli.main(argv)
        assert codigo == 2, f"{argv} deberia ser exit 2, no {codigo}"
        err = capsys.readouterr().err
        assert "harvest_" in err, f"{argv}: el mensaje debe nombrar el campo"


def test_cli_goals_set_numeros_no_finitos_exit_2(monkeypatch, capsys):
    """#3 (regresion review 3.2): Decimal('NaN') PARSEA en argparse y esquia
    toda comparacion (NaN <= 0 es False); Infinity pasa gt=0 — y PG16 acepta
    ambos en NUMERIC. El camino unico los rechaza con mensaje claro (exit 2,
    patron de uso invalido), sin traceback. edita_goal va REAL (sin mock)."""
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://orbit_admin:secreta@127.0.0.1:5432/o")
    for argv in (
        ["goals", "set", "7", "--target", "NaN"],
        ["goals", "set", "7", "--floor", "Infinity"],
        ["goals", "set", "7", "--harvest-bid", "NaN"],
    ):
        codigo = cli.main(argv)
        assert codigo == 2, f"{argv} deberia ser exit 2, no {codigo}"
        err = capsys.readouterr().err
        assert "finito" in err, f"{argv}: mensaje claro, no un fallo generico"
        assert "Traceback" not in err


def test_cli_goals_help_documentado(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["goals", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "set" in out


def test_cli_goals_sin_subcomando_exit_2(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["goals"])
    assert exc.value.code == 2
