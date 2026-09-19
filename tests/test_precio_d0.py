"""Tests del paquete de D.0 (REPRICING 01): `docs/evidencia/repricing-01/D.0/`.

D.0 lo corre el dueño contra producción con `correr.sh`; estos tests prueban
sus piezas contra una base temporal con base real (patrón `db_39` de
`tests/test_precio_migracion.py`):

- `siembra.sql` copia la `config_version` vigente, agrega exactamente las 20
  claves de `verificar_config.CLAVES`, deja los caps de Ads como estaban y se
  niega a sembrar dos veces;
- `cuota.sql` hace nacer la cuota `precio:amazon_mx` con cap 5 como
  `app_admin` y no deja filas;
- `preflight.sql` y `readback.sql` corren como `app_read` en solo lectura;
- `verificar_config.fallas` discrimina cada falla que promete;
- el backup del schema reconoce un `pg_dump` real: `pg_dump` escribe
  `CREATE FUNCTION` y el `grep` de `docs/DEPLOY.md` §0039 buscaba
  `CREATE OR REPLACE FUNCTION`, así que el backup de D.0 abortaba con
  «DUMP INVALIDO» (lo cazó el ensayo local del paquete).
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
D0 = ROOT / "docs" / "evidencia" / "repricing-01" / "D.0"
ANTES_DE_0039 = ("0001_initial.sql", "0002_apply.sql", "0028_estimacion_venta.sql")

CAPS_ADS = {
    f"ads_apply_cap_{plataforma}_{kind}": cap
    for plataforma in ("amazon_mx", "amazon_us")
    for kind, cap in (("bid", 20), ("pause", 5), ("negative", 10), ("harvest", 2))
}
SETTINGS_VIGENTES = {**CAPS_ADS, "ads_optimizer_mode": "shadow", "isr_tasa": "0.0125"}
MOTORES_ADS = [
    f"ads_optimizer:{plataforma}:{kind}"
    for plataforma in ("amazon_mx", "amazon_us")
    for kind in ("bid", "pause", "negative", "harvest")
]

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _verificador():
    spec = importlib.util.spec_from_file_location("verificar_config_d0", D0 / "verificar_config.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


VERIFICADOR = _verificador()


@contextmanager
def _db(prefijo: str, *, con_0039: bool):
    """Base temporal con 0001/0002/0028 (+ 0039 si se pide) y una config
    vigente con los ocho caps de Ads; yields `(conn autocommit, conninfo)`."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ANTES_DE_0039:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('vigente', %s)",
            (Json(SETTINGS_VIGENTES),),
        )
        if con_0039:
            conn.execute((ROOT / "migrations" / "0039_precio.sql").read_text(encoding="utf-8"))
        yield conn, make_conninfo(dsn, dbname=db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _siembra(conn, go: str) -> None:
    """`siembra.sql` con la variable de psql `go` resuelta como literal."""
    texto = (D0 / "siembra.sql").read_text(encoding="utf-8")
    assert texto.count(":'go'") == 1
    literal = pgsql.Literal(go).as_string(conn)
    conn.execute(texto.replace(":'go'", literal))


def _vigente(conn) -> tuple[int, str, dict]:
    return conn.execute(
        "SELECT id, label, settings FROM config_version ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _pares(conn, texto: str, *, rol: str) -> dict[str, str]:
    """Corre un archivo `clave|valor` como `rol` en solo lectura; junta los pares."""
    pares: dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute(f"SET ROLE {rol}")
        cur.execute("BEGIN READ ONLY")
        try:
            cur.execute(texto)
            while True:
                if cur.description is not None:
                    for clave, valor in cur.fetchall():
                        pares[clave] = valor
                if not cur.nextset():
                    break
        finally:
            cur.execute("ROLLBACK")
            cur.execute("RESET ROLE")
    return pares


def _cap(conn, motor: str):
    return conn.execute("SELECT apply_cap_de_config(%s)", (motor,)).fetchone()[0]


# ---------------------------------------------------------------------------
# siembra.sql
# ---------------------------------------------------------------------------


@_skip_db
def test_siembra_copia_la_vigente_y_agrega_las_20_claves():
    with _db("orbit_d0_siembra", con_0039=True) as (conn, _):
        caps_antes = {m: _cap(conn, m) for m in MOTORES_ADS}
        assert _cap(conn, "precio:amazon_mx") is None
        _siembra(conn, "D.0 go del dueño: 'literal'")
        id_nuevo, label, settings = _vigente(conn)
        assert id_nuevo == 2
        assert label == "D.0 go del dueño: 'literal'"
        for clave, valor in SETTINGS_VIGENTES.items():
            assert settings[clave] == valor, clave
        precio = {k for k in settings if k.startswith("precio_")}
        assert precio == set(VERIFICADOR.CLAVES)
        assert len(precio) == 20
        assert VERIFICADOR.fallas(settings) == []
        for motor in ("precio:amazon_mx", "precio:amazon_us", "precio:meli"):
            assert _cap(conn, motor) == 5
        assert {m: _cap(conn, m) for m in MOTORES_ADS} == caps_antes


@_skip_db
def test_siembra_no_siembra_dos_veces():
    with _db("orbit_d0_dos_veces", con_0039=True) as (conn, _):
        _siembra(conn, "primera")
        with pytest.raises(psycopg.errors.RaiseException, match="no se siembra dos veces"):
            _siembra(conn, "segunda")
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT count(*) FROM config_version").fetchone()[0] == 2
        assert _vigente(conn)[1] == "primera"


# ---------------------------------------------------------------------------
# cuota.sql
# ---------------------------------------------------------------------------


@_skip_db
def test_cuota_nace_con_cap_5_como_app_admin_y_no_deja_filas():
    with _db("orbit_d0_cuota", con_0039=True) as (conn, _):
        _siembra(conn, "go")
        pares: dict[str, str] = {}
        with conn.cursor() as cur:
            cur.execute("SET ROLE app_admin")
            cur.execute((D0 / "cuota.sql").read_text(encoding="utf-8"))
            while True:
                if cur.description is not None:
                    pares.update(dict(cur.fetchall()))
                if not cur.nextset():
                    break
            cur.execute("RESET ROLE")
        assert pares == {"cuota:precio:amazon_mx": "5", "cuota_filas_despues": "0"}


@_skip_db
def test_cuota_sin_siembra_revienta_fail_closed():
    """Sin las claves `precio_cap_*` el trigger de 0002 no deja nacer la fila:
    la comprobación de `cuota.sql` no puede salir verde por accidente."""
    with _db("orbit_d0_cuota_sin", con_0039=True) as (conn, _):
        conn.execute("SET ROLE app_admin")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute((D0 / "cuota.sql").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# preflight.sql y readback.sql
# ---------------------------------------------------------------------------


@_skip_db
def test_preflight_como_lector_antes_de_la_0039():
    with _db("orbit_d0_pre", con_0039=False) as (conn, _):
        pares = _pares(conn, (D0 / "preflight.sql").read_text(encoding="utf-8"), rol="app_read")
        assert pares["sin_0039"] == "true"
        assert pares["btree_gist"] == "1"
        assert pares["claves_precio"] == "0"
        assert pares["listing_dup_id_platform"] == "0"
        assert pares["config_label"] == "vigente"
        assert {k: v for k, v in pares.items() if k.startswith("cap:")} == {
            f"cap:{m}": str(CAPS_ADS[f"ads_apply_cap_{m.split(':')[1]}_{m.split(':')[2]}"])
            for m in MOTORES_ADS
        }


@_skip_db
def test_readback_como_lector_despues_de_migracion_y_siembra():
    with _db("orbit_d0_rb", con_0039=True) as (conn, _):
        _siembra(conn, "go del readback")
        pares = _pares(conn, (D0 / "readback.sql").read_text(encoding="utf-8"), rol="app_read")
        assert pares["tablas"] == (
            "precio_cambio,precio_cotizacion,precio_decision,precio_envio_muestra,precio_goal"
        )
        for tabla in pares["tablas"].split(","):
            assert pares[f"filas:{tabla}"] == "0"
        assert pares["listing_id_platform_key"] == "1"
        assert pares["exclude:precio_goal_sin_solape"] == "1"
        assert pares["indices_parciales"] == "2"
        assert len([k for k in pares if k.startswith("triggers:")]) == 5
        for motor in ("precio:amazon_mx", "precio:amazon_us", "precio:meli"):
            assert pares[f"cap:{motor}"] == "5"
        assert all(pares[f"cap:{m}"] != "NULL" for m in MOTORES_ADS)
        assert pares["config_label"] == "go del readback"
        assert {k.removeprefix("clave:") for k in pares if k.startswith("clave:")} == set(
            VERIFICADOR.CLAVES
        )
        # Los conteos son de verdad: con un goal sembrado, el readback lo ve.
        listing_id = conn.execute(
            "WITH p AS (INSERT INTO product (odoo_sku, name) VALUES ('D0-RB', 'rb') RETURNING id)"
            " INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " SELECT p.id, 'amazon_mx', 'B0D0RB', 'D0-RB' FROM p RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode, valid_from,"
            " creado_por) VALUES (%s, 'amazon_mx', 0.30, 'shadow',"
            " (now() AT TIME ZONE 'UTC')::date, 'test')",
            (listing_id,),
        )
        pares = _pares(conn, (D0 / "readback.sql").read_text(encoding="utf-8"), rol="app_read")
        assert pares["filas:precio_goal"] == "1"
        assert pares["filas:precio_decision"] == "0"


# ---------------------------------------------------------------------------
# verificar_config.py
# ---------------------------------------------------------------------------


def _config_buena() -> dict:
    return {
        clave: ([] if isinstance(valor, list) else str(valor))
        for clave, valor in VERIFICADOR.CLAVES.items()
    }


def test_verificar_config_acepta_la_tabla_del_plan():
    assert VERIFICADOR.fallas(_config_buena()) == []


@pytest.mark.parametrize(
    ("cambio", "esperado"),
    [
        (lambda s: s.pop("precio_cap_meli"), "falta precio_cap_meli"),
        (lambda s: s.update(precio_cap_amazon_mx="6"), "precio_cap_amazon_mx = '6'"),
        (lambda s: s.update(precio_envio_ventana_dias=90), "claves de envío sembradas"),
        (lambda s: s.update(precio_otra=1), "fuera de la tabla del plan"),
        (lambda s: s.update(precio_goal_min_pct="0.70"), "banda_desde_settings"),
        (lambda s: s.update(precio_catalogo_max_dias_sin_reportar=True), "precio_catalogo"),
        (lambda s: s.update(precio_fechas_excluidas="[]"), "precio_fechas_excluidas"),
    ],
)
def test_verificar_config_caza_cada_falla(cambio, esperado):
    settings = _config_buena()
    cambio(settings)
    errores = VERIFICADOR.fallas(settings)
    assert any(esperado in e for e in errores), errores


@pytest.mark.parametrize("nombre", ["validar_freno_dias_error", "validar_precio_aviso_dias"])
def test_verificar_config_corre_los_validadores_de_la_fase_11(monkeypatch, nombre):
    """La corrida (A.5) y la pantalla (A.6) validan sus claves al arrancar:
    el paso 5 corre esos mismos validadores sobre la config sembrada."""

    def _revienta(*_args, **_kwargs):
        raise ValueError(f"{nombre} saboteado")

    monkeypatch.setattr(VERIFICADOR, nombre, _revienta)
    errores = VERIFICADOR.fallas(_config_buena())
    assert any(f"{nombre} saboteado" in e for e in errores), errores


@pytest.mark.parametrize("plataforma", ["amazon_mx", "amazon_us", "meli"])
def test_verificar_config_valida_el_cap_de_cada_plataforma(monkeypatch, plataforma):
    """`/precios` lee el cap de amazon_mx y amazon_us (fail-closed global) y
    la corrida acepta `--platform` de las tres: el paso 5 valida los tres
    caps sembrados, no solo el de la corrida de MX."""
    real = VERIFICADOR.validar_cap

    def _cap(settings, platform):
        if platform == plataforma:
            raise ValueError(f"cap {platform} saboteado")
        return real(settings, platform)

    monkeypatch.setattr(VERIFICADOR, "validar_cap", _cap)
    errores = VERIFICADOR.fallas(_config_buena())
    assert any(f"cap {plataforma} saboteado" in e for e in errores), errores


# ---------------------------------------------------------------------------
# backup del schema contra un pg_dump real
# ---------------------------------------------------------------------------

_PATRON_GREP = re.compile(r'grep -q "([^"]+)" "\$TMP"')


def _patrones_del_backup(texto: str) -> list[str]:
    """Los `grep -q "<patrón>" "$TMP"` del bloque de backup de la 0039."""
    return [p for p in _PATRON_GREP.findall(texto) if "listing" in p or "apply_cap" in p]


def _pg_dump():
    ruta = shutil.which("pg_dump")
    if ruta is None:
        if "CI" in os.environ:
            raise RuntimeError("CI sin pg_dump: el test del backup de D.0 no puede saltarse")
        pytest.skip("sin pg_dump en PATH")
    return ruta


@_skip_db
def test_backup_de_la_0039_reconoce_un_pg_dump_real():
    pg_dump = _pg_dump()
    fuentes = {
        "docs/DEPLOY.md": (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8"),
        "D.0/correr.sh": (D0 / "correr.sh").read_text(encoding="utf-8"),
    }
    with _db("orbit_d0_dump", con_0039=True) as (_conn, conninfo):
        dump = subprocess.run(
            [pg_dump, "--schema-only", f"--dbname={conninfo}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    assert "PostgreSQL database dump complete" in "\n".join(dump.splitlines()[-5:])
    for nombre, texto in fuentes.items():
        patrones = _patrones_del_backup(texto)
        assert len(patrones) >= 2, (nombre, patrones)
        for patron in patrones:
            assert re.search(re.escape(patron), dump), f"{nombre}: {patron!r} no está en el pg_dump"
