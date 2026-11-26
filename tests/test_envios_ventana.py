"""ENVIOS 01: la clave `envios_meli_dias_cierre` (migracion 0103 y `envios-ventana`)."""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
from pathlib import Path

import pglast
import psycopg
import pytest
from helpers.pg import db_desde_template
from psycopg.types.json import Json
from test_fabrica_f2 import _skip_db
from test_schema import _test_dsn

from app import cli, config_write

MIGRACIONES = Path(__file__).resolve().parent.parent / "migrations"
IDA = MIGRACIONES / "0103_envios01_ventana_cierre.sql"
REVERSA = MIGRACIONES / "0103_reversa_envios01_ventana_cierre.sql"
CLAVE = "envios_meli_dias_cierre"
VIGENTE = {"ads_optimizer_mode": "shadow", "precio_modo_global": "off"}


@pytest.fixture
def base():
    with db_desde_template("orbit_envios_ventana", base=_test_dsn(), autocommit=True) as par:
        yield par


def _sonda(conn) -> bool:
    primera = IDA.read_text(encoding="utf-8").splitlines()[0]
    filas = conn.execute(primera.split(":", 1)[1].strip()).fetchall()
    # tools/despliegue/comun.sh exige exactamente una respuesta t o f.
    assert len(filas) == 1 and isinstance(filas[0][0], bool)
    return filas[0][0]


def _aplica(conn, ruta: Path) -> None:
    conn.execute(ruta.read_text(encoding="utf-8"))


def _vigente(conn) -> tuple[int, str, dict]:
    return conn.execute(
        "SELECT id, label, settings FROM config_version ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _filas(conn) -> int:
    return conn.execute("SELECT count(*) FROM config_version").fetchone()[0]


def _siembra(conn, settings: dict) -> None:
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('sembrada', %s)", (Json(settings),)
    )


def test_0103_sintaxis_cabecera_y_receta():
    # Mismo patron con el que tools/despliegue/comun.sh aborta una migracion.
    control = re.compile(
        r"^\s*(BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK)\s*(TRANSACTION|WORK)?\s*;",
        re.IGNORECASE | re.MULTILINE,
    )
    for ruta in (IDA, REVERSA):
        texto = ruta.read_text(encoding="utf-8")
        assert pglast.parse_sql(texto)
        assert control.search(texto) is None, ruta.name
    assert IDA.read_text(encoding="utf-8").splitlines()[0].startswith("-- sonda-aplicada: SELECT ")


@_skip_db
def test_0103_siembra_90_copiando_la_vigente_y_se_revierte(base):
    conn, _ = base
    _siembra(conn, VIGENTE)
    assert _sonda(conn) is False
    antes = _filas(conn)

    _aplica(conn, IDA)

    assert _sonda(conn) is True
    assert _filas(conn) == antes + 1
    assert _vigente(conn)[2] == VIGENTE | {CLAVE: "90"}

    _aplica(conn, REVERSA)
    assert _sonda(conn) is False
    assert _filas(conn) == antes + 2
    assert _vigente(conn)[2] == VIGENTE
    _aplica(conn, REVERSA)
    assert _filas(conn) == antes + 2


@_skip_db
def test_0103_no_pisa_un_valor_ya_puesto(base):
    conn, _ = base
    _siembra(conn, VIGENTE | {CLAVE: "120"})
    antes = _filas(conn)
    _aplica(conn, IDA)
    assert (_filas(conn), _vigente(conn)[2][CLAVE], _sonda(conn)) == (antes, "120", True)


@_skip_db
def test_0103_con_config_vacia_no_inserta(base):
    conn, _ = base
    # config_version es append-only: una tabla temporal vacia la tapa en esta sesion.
    conn.execute("CREATE TEMP TABLE config_version (LIKE public.config_version INCLUDING ALL)")
    for ruta in (IDA, REVERSA):
        _aplica(conn, ruta)
        assert _filas(conn) == 0
        assert _sonda(conn) is False


def _espera_candado(conn, pid: int, hilo: threading.Thread) -> bool:
    """True si la sesion `pid` queda esperando un candado de aplicacion."""
    limite = time.monotonic() + 15
    while hilo.is_alive() and time.monotonic() < limite:
        fila = conn.execute(
            "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if fila == ("Lock", "advisory"):
            return True
        time.sleep(0.02)
    return False


@_skip_db
@pytest.mark.parametrize(
    ("ruta", "sembrada", "esperada"),
    [
        pytest.param(IDA, VIGENTE, {CLAVE: "90"}, id="ida"),
        pytest.param(REVERSA, VIGENTE | {CLAVE: "90"}, {}, id="reversa"),
    ],
)
def test_0103_espera_al_guardado_en_curso_y_conserva_su_clave(base, ruta, sembrada, esperada):
    conn, dsn = base
    _siembra(conn, sembrada)
    antes = _filas(conn)
    errores: list[BaseException] = []

    with psycopg.connect(dsn) as editor, psycopg.connect(dsn) as migrador:
        config_write.guarda_config(
            editor,
            platform="meli",
            base_config_version_id=config_write.config_vigente_id(editor),
            ahora=dt.datetime(2026, 11, 26, tzinfo=dt.UTC),
            confirma=False,
            precio_modo_global="shadow",
        )
        migrador.execute("SET statement_timeout = '30s'")
        migrador.commit()

        def migra() -> None:
            # Como tools/despliegue (psql -1): el archivo entero en una transaccion.
            try:
                with migrador.transaction():
                    _aplica(migrador, ruta)
            except BaseException as exc:
                errores.append(exc)

        hilo = threading.Thread(target=migra)
        hilo.start()
        try:
            espero = _espera_candado(conn, migrador.info.backend_pid, hilo)
        finally:
            editor.commit()
            hilo.join(timeout=40)

        assert not hilo.is_alive()

    assert errores == []
    assert espero, "la migracion no espero al guardado en curso"
    assert _filas(conn) == antes + 2
    assert (
        _vigente(conn)[2]
        == {
            "ads_optimizer_mode": "shadow",
            "precio_modo_global": "shadow",
        }
        | esperada
    )


@_skip_db
def test_cli_envios_ventana_escribe_una_vez_por_el_escritor_unico(base, monkeypatch, capsys):
    conn, dsn = base
    _siembra(conn, VIGENTE)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
    escritas: list[str] = []
    real = config_write.guarda_config

    def espia(*args, **kwargs):
        escritas.append(kwargs["actor"])
        return real(*args, **kwargs)

    monkeypatch.setattr(config_write, "guarda_config", espia)
    antes = _filas(conn)

    assert cli.main(["envios-ventana", "--dias", "45"]) == 0

    nueva, label, settings = _vigente(conn)
    assert escritas == ["envios-ventana"]
    assert _filas(conn) == antes + 1
    assert settings == VIGENTE | {CLAVE: "45"}
    assert " · envios-ventana · " in label
    assert label.endswith("meli: dias de cierre de envios ausente -> 45")
    assert f"config_version {nueva}" in capsys.readouterr().out

    assert cli.main(["envios-ventana", "--dias", "45"]) == 0
    assert _filas(conn) == antes + 1
    assert "sin cambios" in capsys.readouterr().out


@_skip_db
def test_cli_envios_ventana_corrige_un_valor_invalido(base, monkeypatch):
    conn, dsn = base
    _siembra(conn, VIGENTE | {CLAVE: "noventa"})
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)

    assert cli.main(["envios-ventana", "--dias", "90"]) == 0

    assert _vigente(conn)[2] == VIGENTE | {CLAVE: "90"}


@_skip_db
@pytest.mark.parametrize("dias", ["29", "366", "0", "-1"])
def test_cli_envios_ventana_rechaza_fuera_de_rango(base, monkeypatch, capsys, dias):
    conn, dsn = base
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
    antes = _filas(conn)

    assert cli.main(["envios-ventana", "--dias", dias]) == 2

    assert _filas(conn) == antes
    assert "de 30 a 365" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argumentos",
    [[], ["--dias", "noventa"], ["--dias", "4.5"]],
)
def test_cli_envios_ventana_uso_invalido(argumentos):
    with pytest.raises(SystemExit) as salida:
        cli.main(["envios-ventana", *argumentos])
    assert salida.value.code == 2


def test_cli_envios_ventana_sin_dsn_o_con_tokens_extra_sale_dos(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    assert cli.main(["envios-ventana", "--dias", "45"]) == 2
    assert "ORBIT_DSN_ADMIN" in capsys.readouterr().err
    assert cli.main(["envios-ventana", "--dias", "45", "--seco"]) == 2
    assert "--seco" in capsys.readouterr().err
