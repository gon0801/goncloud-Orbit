"""Vigilante del cron SP-API: el silencio avisa.

El cron de las 05:00 UTC deja 8 corridas en `ingest_run` (4 fuentes x 2
plataformas, wrapper spapi-diario.sh). Si el cron NO dispara, no hay fila
que fallar y `salud.py` no tiene nada que evaluar: este modulo convierte
la ausencia en aviso. Solo lee (`ORBIT_DSN_READ`); sin migracion.

Bloques: `faltantes` pura (ventana [desde, hasta), ok=false cuenta,
finished NULL es ausencia), builders con igualdad exacta, sender
fail-silent, validacion de ventana, lector con DSN real, CLI
(`--dry-run`, exits 0/1/2) y candado de la linea de crontab.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from psycopg.conninfo import make_conninfo
from test_schema import _postgres_obligatorio_ausente, _test_dsn
from test_spapi_salud import db_salud

from app.spapi import vigilante
from app.spapi.vigilante import PLATAFORMAS_SPAPI, faltantes, lee_ventana, main

DESDE = datetime(2026, 9, 16, 4, 30, tzinfo=UTC)
HASTA = datetime(2026, 9, 16, 7, 30, tzinfo=UTC)
FUENTES = ("spapi_orders", "spapi_pricing", "spapi_listings", "spapi_inventario")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _fila(
    source,
    platform,
    inicio=DESDE + timedelta(minutes=10),
    fin=DESDE + timedelta(minutes=12),
    ok=True,
):
    return (source, platform, inicio, fin, ok)


def _ocho_presentes():
    return [_fila(fuente, plat) for fuente in FUENTES for plat in ("amazon_mx", "amazon_us")]


def test_faltantes_ocho_presentes_vacio():
    assert faltantes(_ocho_presentes(), desde=DESDE, hasta=HASTA) == []


def test_faltantes_fuente_sin_fila_devuelve_sus_dos_pares():
    filas = [f for f in _ocho_presentes() if f[0] != "spapi_pricing"]
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == [
        ("spapi_pricing", "amazon_mx"),
        ("spapi_pricing", "amazon_us"),
    ]


def test_faltantes_ok_false_cuenta_como_presente():
    """Una corrida fallida ya aviso por su camino (salud.py); el
    vigilante no re-avisa fallos, solo ausencias."""
    filas = _ocho_presentes()
    filas[0] = _fila("spapi_orders", "amazon_mx", ok=False)
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == []


def test_faltantes_finished_none_es_ausencia():
    """Empezada y no terminada = colgada = silencio."""
    filas = _ocho_presentes()
    filas[0] = _fila("spapi_orders", "amazon_mx", fin=None, ok=None)
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == [("spapi_orders", "amazon_mx")]


def test_faltantes_fila_de_ayer_no_cuenta():
    """Solo vale started_at en [desde, hasta): la corrida de ayer no
    tapa el silencio de hoy."""
    filas = [
        _fila(f, p, inicio=DESDE - timedelta(days=1), fin=DESDE - timedelta(days=1))
        for f in FUENTES
        for p in ("amazon_mx", "amazon_us")
    ]
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == [
        (f, p) for f in FUENTES for p in ("amazon_mx", "amazon_us")
    ]


def test_faltantes_started_en_hasta_exacto_es_ausencia():
    """El limite superior es EXCLUSIVO: una corrida que empieza justo en
    `hasta` ya es de la ventana siguiente, no tapa el silencio de esta."""
    filas = _ocho_presentes()
    filas[0] = _fila("spapi_orders", "amazon_mx", inicio=HASTA, fin=HASTA)
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == [("spapi_orders", "amazon_mx")]


def test_faltantes_source_ajena_se_ignora():
    filas = _ocho_presentes()
    filas.append(_fila("amazon_ads_structure_v2", "amazon_mx"))
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == []


def test_faltantes_plataforma_null_se_ignora_sin_error():
    """Corridas pre-A.5 (platform NULL): invisibles, nunca error (mismo
    contrato que salud.py)."""
    filas = _ocho_presentes()
    filas[0] = _fila("spapi_orders", None)
    assert faltantes(filas, desde=DESDE, hasta=HASTA) == [("spapi_orders", "amazon_mx")]


def test_plataformas_son_dos_y_ocho_pares():
    assert PLATAFORMAS_SPAPI == ("amazon_mx", "amazon_us")
    assert len(FUENTES) * len(PLATAFORMAS_SPAPI) == 8


# ---------------------------------------------------------------------------
# 4. Validacion de la ventana: desde >= hasta -> exit 2 sin SELECT ni envio
# ---------------------------------------------------------------------------


def _espia_quieto(nombre):
    def _falla(*a, **k):
        raise AssertionError(f"{nombre} no debio llamarse")

    return _falla


def test_ventana_invertida_exit_2_sin_select_ni_envio(monkeypatch, capsys):
    """Intervalo invertido: se rechaza ANTES de abrir la base y sin enviar
    nada (un aviso por ventana invalida seria falso)."""
    monkeypatch.setattr(vigilante, "lee_ventana", _espia_quieto("lee_ventana"))
    monkeypatch.setattr(
        vigilante, "notifica_spapi_silencio", _espia_quieto("notifica_spapi_silencio")
    )
    monkeypatch.setattr(vigilante, "avisa_ciego", _espia_quieto("avisa_ciego"))
    rc = main(["--desde", "2026-09-16T07:30:00Z", "--hasta", "2026-09-16T04:30:00Z"])
    assert rc == 2
    assert "ventana invalida" in capsys.readouterr().err


def test_ventana_vacia_exit_2_sin_select_ni_envio(monkeypatch, capsys):
    """desde == hasta tambien es invalido (el intervalo es [desde,
    hasta), vacio)."""
    monkeypatch.setattr(vigilante, "lee_ventana", _espia_quieto("lee_ventana"))
    monkeypatch.setattr(
        vigilante, "notifica_spapi_silencio", _espia_quieto("notifica_spapi_silencio")
    )
    monkeypatch.setattr(vigilante, "avisa_ciego", _espia_quieto("avisa_ciego"))
    rc = main(["--desde", "2026-09-16T04:30:00Z", "--hasta", "2026-09-16T04:30:00Z"])
    assert rc == 2
    assert "ventana invalida" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 5. Lector con DSN real (+ rol de lectura basta)
# ---------------------------------------------------------------------------


def _corre(conn, source, platform, *, inicio, fin, ok=True):
    conn.execute(
        "INSERT INTO ingest_run (source, platform, ok, rows_written, rows_skipped,"
        " skip_reason, llamadas, started_at, finished_at)"
        " VALUES (%s, %s, %s, 0, 0, NULL, 0, %s, %s)",
        (source, platform, ok, inicio, fin),
    )


def _ocho_sembradas(conn):
    for fuente in FUENTES:
        for plat in ("amazon_mx", "amazon_us"):
            _corre(
                conn,
                fuente,
                plat,
                inicio=DESDE + timedelta(minutes=10),
                fin=DESDE + timedelta(minutes=12),
            )


@_skip_db
def test_lee_ventana_trae_filas_y_rol_lectura_basta():
    """El SELECT trae (source, platform, started_at, finished_at, ok) de
    la ventana y corre bajo SET ROLE app_read (el rol de lectura basta;
    el vigilante solo lee)."""
    with db_salud("orbit_spapi_vig") as conn:
        _ocho_sembradas(conn)
        _corre(
            conn,
            "spapi_orders",
            "amazon_mx",
            inicio=DESDE - timedelta(days=1),
            fin=DESDE - timedelta(days=1),
        )
        # Borde `hasta` (exclusivo): empezada justo al cierre NO es de hoy.
        _corre(
            conn,
            "spapi_orders",
            "amazon_mx",
            inicio=HASTA,
            fin=HASTA + timedelta(minutes=2),
        )
        conn.commit()
        filas = lee_ventana(conn, desde=DESDE, hasta=HASTA)
        assert len(filas) == 8
        assert filas[0][:2] == ("spapi_orders", "amazon_mx")
        conn.execute("SET ROLE app_read")
        try:
            filas_lectura = lee_ventana(conn, desde=DESDE, hasta=HASTA)
        finally:
            conn.execute("RESET ROLE")
        assert len(filas_lectura) == 8


# ---------------------------------------------------------------------------
# 6. CLI: dry-run, exit codes, futuro, DB rota
# ---------------------------------------------------------------------------


def _dsn_leer(conn):
    return make_conninfo(_test_dsn(), dbname=conn.info.dbname)


@_skip_db
def test_cli_verde_exit_0_y_silencioso(monkeypatch, capsys):
    """Ocho presentes: imprime los pares + `faltan 0 de 8`, exit 0, y NO
    llama al sender (el vigilante es silencioso en verde)."""
    with db_salud("orbit_spapi_vig0") as conn:
        _ocho_sembradas(conn)
        conn.commit()
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_leer(conn))
        monkeypatch.setattr(
            vigilante, "notifica_spapi_silencio", _espia_quieto("notifica_spapi_silencio")
        )
        rc = main(
            [
                "--desde",
                "2026-09-16T04:30:00Z",
                "--hasta",
                "2026-09-16T07:30:00Z",
            ]
        )
    assert rc == 0
    salida = capsys.readouterr().out
    assert "spapi_orders / amazon_mx: presente" in salida
    assert salida.count(": presente") == 8
    assert "faltan 0 de 8" in salida


@_skip_db
def test_cli_faltantes_exit_1_y_avisa(monkeypatch, capsys):
    """Con ausencias: exit 1 DESPUES de intentar el aviso (spy registra
    los faltantes que se avisarian)."""
    avisados: list = []

    def _spy(falt, desde, hasta, **kw):
        avisados.append(list(falt))
        return True

    with db_salud("orbit_spapi_vig1") as conn:
        for plat in ("amazon_mx", "amazon_us"):
            _corre(
                conn,
                "spapi_orders",
                plat,
                inicio=DESDE + timedelta(minutes=10),
                fin=DESDE + timedelta(minutes=12),
            )
        conn.commit()
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_leer(conn))
        monkeypatch.setattr(vigilante, "notifica_spapi_silencio", _spy)
        rc = main(
            [
                "--desde",
                "2026-09-16T04:30:00Z",
                "--hasta",
                "2026-09-16T07:30:00Z",
            ]
        )
    assert rc == 1
    assert avisados == [
        [
            ("spapi_pricing", "amazon_mx"),
            ("spapi_pricing", "amazon_us"),
            ("spapi_listings", "amazon_mx"),
            ("spapi_listings", "amazon_us"),
            ("spapi_inventario", "amazon_mx"),
            ("spapi_inventario", "amazon_us"),
        ]
    ]
    salida = capsys.readouterr().out
    assert "faltan 6 de 8" in salida


@_skip_db
def test_cli_dry_run_no_envia_pero_mismo_codigo(monkeypatch, capsys):
    """--dry-run evalua e imprime con los mismos codigos, sin enviar."""
    with db_salud("orbit_spapi_vigd") as conn:
        conn.commit()
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_leer(conn))
        monkeypatch.setattr(
            vigilante, "notifica_spapi_silencio", _espia_quieto("notifica_spapi_silencio")
        )
        monkeypatch.setattr(vigilante, "avisa_ciego", _espia_quieto("avisa_ciego"))
        rc = main(
            [
                "--desde",
                "2026-09-16T04:30:00Z",
                "--hasta",
                "2026-09-16T07:30:00Z",
                "--dry-run",
            ]
        )
    assert rc == 1
    assert "faltan 8 de 8" in capsys.readouterr().out


@_skip_db
def test_cli_desde_futuro_ocho_ausentes(monkeypatch, capsys):
    """Ventana futura valida: las 8 ausentes (nada ha corrido manana)."""
    with db_salud("orbit_spapi_vigf") as conn:
        _ocho_sembradas(conn)
        conn.commit()
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_leer(conn))
        monkeypatch.setattr(vigilante, "notifica_spapi_silencio", lambda *a, **k: True)
        rc = main(
            [
                "--desde",
                "2026-12-01T04:30:00Z",
                "--hasta",
                "2026-12-01T07:30:00Z",
            ]
        )
    assert rc == 1
    assert "faltan 8 de 8" in capsys.readouterr().out


def test_cli_db_rota_aviso_ciego_y_exit_2(monkeypatch, capsys):
    """DSN invalido: intenta el aviso ciego y sale 2 (sin Postgres)."""
    ciegos: list = []

    def _spy(motivo, **kw):
        ciegos.append(motivo)
        return False

    monkeypatch.setenv("ORBIT_DSN_READ", "host=127.0.0.1 port=1 dbname=no_existe")
    monkeypatch.setattr(vigilante, "avisa_ciego", _spy)
    monkeypatch.setattr(
        vigilante, "notifica_spapi_silencio", _espia_quieto("notifica_spapi_silencio")
    )
    rc = main(
        [
            "--desde",
            "2026-09-16T04:30:00Z",
            "--hasta",
            "2026-09-16T07:30:00Z",
        ]
    )
    assert rc == 2
    assert len(ciegos) == 1 and ciegos[0]


def test_cli_sin_dsn_aviso_ciego_y_exit_2(monkeypatch, capsys):
    """Sin ORBIT_DSN_READ: ciego + exit 2 (fail-closed, sin traceback)."""
    ciegos: list = []
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    monkeypatch.setattr(vigilante, "avisa_ciego", lambda motivo, **kw: ciegos.append(motivo))
    rc = main(
        [
            "--desde",
            "2026-09-16T04:30:00Z",
            "--hasta",
            "2026-09-16T07:30:00Z",
        ]
    )
    assert rc == 2
    assert len(ciegos) == 1


def test_cli_pasa_connect_timeout_a_la_conexion(monkeypatch, capsys):
    """El vigilante no cuelga con el flock tomado: `connect` recibe
    `connect_timeout=10` (un host blackholeado cae al camino ciego en
    segundos, no en ~2 min)."""
    llamadas: list = []

    class _Conn:
        def execute(self, *a, **k):
            class _Cur:
                def fetchall(self):
                    return []

            return _Cur()

        def close(self):
            pass

    def _connect(dsn, **kw):
        llamadas.append(kw)
        return _Conn()

    monkeypatch.setenv("ORBIT_DSN_READ", "postgresql://x/y")
    monkeypatch.setattr(vigilante, "connect", _connect)
    monkeypatch.setattr(vigilante, "notifica_spapi_silencio", lambda *a, **k: True)
    rc = main(
        [
            "--desde",
            "2026-09-16T04:30:00Z",
            "--hasta",
            "2026-09-16T07:30:00Z",
        ]
    )
    assert rc == 1
    assert llamadas == [{"connect_timeout": 10}]


# ---------------------------------------------------------------------------
# 7. La linea de crontab vive en docs/DEPLOY.md y sobrevive al instalador
# ---------------------------------------------------------------------------

# Fuente unica de la linea (docs/DEPLOY.md la copia de aqui): sin
# `app.cli ingest` para que el instalador de ORBIT 03 no la borre (H1).
LINEA_CRONTAB_VIGILANTE = (
    "30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock"
    " docker exec orbit-app-1 python -m app.cli spapi_vigilante"
    " >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1"
)


def test_linea_crontab_en_deploy_y_sobrevive_instalador():
    """docs/DEPLOY.md trae la linea EXACTA (copiada de esta constante):
    con job_key, flock y log, y SIN `app.cli ingest` (el instalador de
    ORBIT 03 borra lo que calce ese patron)."""
    from pathlib import Path

    deploy = (Path(__file__).resolve().parents[1] / "docs" / "DEPLOY.md").read_text(
        encoding="utf-8"
    )
    assert "job_key=spapi:vigilante" in deploy
    assert LINEA_CRONTAB_VIGILANTE in deploy
    assert "app.cli ingest" not in LINEA_CRONTAB_VIGILANTE
    assert "flock" in LINEA_CRONTAB_VIGILANTE
    assert "spapi-vigilante.log" in LINEA_CRONTAB_VIGILANTE


@_skip_db
def test_cli_registrado_en_app_cli_top_level(monkeypatch, capsys):
    """`python -m app.cli spapi_vigilante` existe como subcomando TOP-LEVEL
    (no bajo `ingest`: la linea de crontab no debe contener `app.cli
    ingest` o el instalador de ORBIT 03 la borra). Camino completo."""
    from app import cli as app_cli

    with db_salud("orbit_spapi_vigc") as conn:
        _ocho_sembradas(conn)
        conn.commit()
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_leer(conn))
        monkeypatch.setattr(
            vigilante, "notifica_spapi_silencio", _espia_quieto("notifica_spapi_silencio")
        )
        rc = app_cli.main(
            [
                "spapi_vigilante",
                "--desde",
                "2026-09-16T04:30:00Z",
                "--hasta",
                "2026-09-16T07:30:00Z",
                "--dry-run",
            ]
        )
    assert rc == 0
    salida = capsys.readouterr().out
    assert salida.count(": presente") == 8
    assert "faltan 0 de 8" in salida
