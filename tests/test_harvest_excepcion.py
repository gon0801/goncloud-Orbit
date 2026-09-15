"""Herramienta de migracion a `harvest_excepcion` y limpieza de terna (FABRICA 02, A.5).

Bloques del brief: esqueleto CLI y candados (1), `--migrar` dry-run y
validacion (2), `--migrar` go (3), `--limpiar-terna` dry-run y validacion
(4), `--limpiar-terna` go (5). Solo Postgres y solo `app_admin`, cero
Amazon: el DSN sale de `ORBIT_DSN_ADMIN` derivado de `_test_dsn()` (jamas
un DSN fijo) y lo que el test escribe a mano va con `SET ROLE app_admin`.
"""

from __future__ import annotations

import hashlib
import importlib.util
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from test_apply_harvest import _entidad, _semilla
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.goals_write import GoalInvalido
from app.optimizer.harvest_destino import DestinoHarvest, resolver_destino

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_RUTA_TOOL = Path(__file__).resolve().parent.parent / "tools" / "harvest_excepcion.py"


def _carga_tool():
    """Carga `tools/harvest_excepcion.py` como modulo (patron
    `test_reversa_harvest._carga_tool`); los tests llaman `mod.main([...])`
    en proceso."""
    spec = importlib.util.spec_from_file_location("harvest_excepcion", _RUTA_TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Bloque 1: esqueleto y candados de CLI
# ---------------------------------------------------------------------------


def test_tool_se_importa_y_expone_main_y_abortar():
    """Rojo inicial: el modulo existe y expone `main(argv) -> int` y
    `Abortar(RuntimeError)`."""
    mod = _carga_tool()
    assert callable(mod.main)
    assert issubclass(mod.Abortar, RuntimeError)


def test_cli_sin_modo_falla(capsys):
    """Sin `--migrar` ni `--limpiar-terna`: `SystemExit` 2 (grupo
    excluyente obligatorio de argparse)."""
    mod = _carga_tool()
    with pytest.raises(SystemExit) as exc:
        mod.main([])
    assert exc.value.code == 2


def test_cli_dos_modos_falla(capsys):
    """Los dos modos juntos: `SystemExit` 2."""
    mod = _carga_tool()
    with pytest.raises(SystemExit) as exc:
        mod.main(["--migrar", "--limpiar-terna", "--grupo", "1"])
    assert exc.value.code == 2


def test_cli_sin_dsn_admin_aborta(monkeypatch):
    """Sin `ORBIT_DSN_ADMIN` en el entorno: `Abortar` (el tool no lee
    otros DSN ni acepta DSN por argumentos)."""
    mod = _carga_tool()
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    with pytest.raises(mod.Abortar, match="ORBIT_DSN_ADMIN"):
        mod.main(
            [
                "--migrar",
                "--plataforma",
                "amazon_us",
                "--campana",
                "7001",
                "--destino-campana",
                "7001",
                "--destino-ad-group",
                "7101",
            ]
        )


def test_tool_solo_admin_sin_amazon_ni_apply():
    """Fronteras del tool (estaticas): un solo DSN (`ORBIT_DSN_ADMIN`;
    `ORBIT_DSN_DECIDE`/`ORBIT_DSN_READ` ni en texto), cero `app.ads.*`,
    cero `app.apply*`, cero `httpx`, cero import dinamico, y
    `AdsWriteClient` ni siquiera como codigo. Regla 9: cualquier import
    de escritura o DSN alterno cae aqui y en la allowlist positiva."""
    import ast as _ast

    fuente = _RUTA_TOOL.read_text(encoding="utf-8")
    arbol = _ast.parse(fuente)
    importados: set[str] = set()
    nombres: set[str] = set()
    for nodo in _ast.walk(arbol):
        if isinstance(nodo, _ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, _ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)
            nombres.update(a.name for a in nodo.names)
        elif isinstance(nodo, _ast.Name):
            nombres.add(nodo.id)
        elif isinstance(nodo, _ast.Attribute):
            nombres.add(nodo.attr)
    assert not [i for i in importados if i == "app.ads" or i.startswith("app.ads.")]
    assert "AdsWriteClient" not in nombres
    assert not [i for i in importados if i == "app.apply" or i.startswith("app.apply")]
    assert "httpx" not in importados
    assert "ORBIT_DSN_ADMIN" in fuente
    assert "ORBIT_DSN_DECIDE" not in fuente and "ORBIT_DSN_READ" not in fuente


# ---------------------------------------------------------------------------
# Bloque 2: `--migrar` dry-run y validacion (db_f2 + _semilla + _semilla_grupo)
# ---------------------------------------------------------------------------


def _dsn_admin_de(conn) -> str:
    """`ORBIT_DSN_ADMIN` derivado del DSN de prueba real: conserva
    usuario/host/puerto y cambia solo la base por la del fixture (jamas
    un DSN fijo `orbit:orbit@localhost`: hallazgo pendiente del PR #267).
    """
    base = _test_dsn()
    raiz, _, resto = base.rpartition("/")
    _nombre, q, query = resto.partition("?")
    dsn = f"{raiz}/{conn.info.dbname}"
    return dsn + (f"?{query}" if q else "")


def _destino(conn, *, camp_ext="8001", ag_ext="8101"):
    """Campana + ad group hijo en amazon_us (el destino que el tool debe
    resolver a filas reales, no aceptar como texto)."""
    camp = _entidad(conn, "campaign", camp_ext)
    ag = _entidad(conn, "ad_group", ag_ext, parent=camp)
    return camp, ag


def _n_excepciones(conn) -> int:
    return conn.execute("SELECT count(*) FROM harvest_excepcion").fetchone()[0]


def _argv_migrar(campana="7001", camp_dest="8001", ag_dest="8101", platform="amazon_us"):
    return [
        "--migrar",
        "--plataforma",
        platform,
        "--campana",
        campana,
        "--destino-campana",
        camp_dest,
        "--destino-ad-group",
        ag_dest,
    ]


@_skip_db
def test_migrar_dry_run_muestra_plan_completo_y_no_escribe(capsys, monkeypatch):
    """Dry-run feliz: imprime origen, par LEIDO con ids, resolucion hoy
    (terna/migracion_pendiente por el goal de plataforma de `_semilla`) y
    despues (excepcion), cierra con `huella: <h>` y deja cero filas."""
    mod = _carga_tool()
    with db_f2("orbit_a5_migplan") as conn:
        _semilla(conn)
        camp_dest, ag_dest = _destino(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert any("external_id=7001" in ln and "ad_entity_id=" in ln for ln in lineas)
        assert any(f"campaign_external=8001 ad_entity_id={camp_dest}" in ln for ln in lineas)
        assert any(f"ad_group_external=8101 ad_entity_id={ag_dest}" in ln for ln in lineas)
        assert any("resolucion_hoy: terna motivo=migracion_pendiente" in ln for ln in lineas)
        assert any("resolucion_despues: excepcion" in ln for ln in lineas)
        huella = hashlib.sha256(b"amazon_us:7001>8001/8101").hexdigest()[:16]
        assert lineas[-1] == f"huella: {huella}"
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_campana_inexistente_aborta_sin_escribir(monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_mignoor") as conn:
        _semilla(conn)
        _destino(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="origen.*no existe"):
            mod.main(_argv_migrar(campana="9999"))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_campana_de_grupo_aborta_con_grupo_y_rol(monkeypatch):
    """Una campana de grupo resuelve por grupo: la excepcion seria letra
    muerta; el motivo trae grupo_id y rol."""
    mod = _carga_tool()
    with db_f2("orbit_a5_miggrupo") as conn:
        _semilla(conn)
        g = _semilla_grupo(conn)
        _destino(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        exacta = g["roles"]["category_exact"]
        with pytest.raises(mod.Abortar, match=f"grupo {g['grupo_id']}.*category_exact"):
            mod.main(_argv_migrar(campana=exacta["camp_ext"]))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_ad_group_inexistente_aborta(monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_mignoag") as conn:
        _semilla(conn)
        _destino(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="ad group destino.*no existe"):
            mod.main(_argv_migrar(ag_dest="9999"))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_ad_group_hijo_de_otra_campana_aborta(monkeypatch):
    """El ad group existe pero cuelga de otra campana: el eslabon
    `parent_id` falla con el padre real."""
    mod = _carga_tool()
    with db_f2("orbit_a5_migpadre") as conn:
        _semilla(conn)
        _destino(conn)
        otra = _entidad(conn, "campaign", "8002")
        _entidad(conn, "ad_group", "8199", parent=otra)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="hijo de 8002"):
            mod.main(_argv_migrar(ag_dest="8199"))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_ad_group_huerfano_aborta(monkeypatch):
    # Ad group destino existe pero parent_id NULL: eslabon roto.
    # Rojo-primero sobre el TypeError del fetchone()[0] (_SQL_PADRE_EXT):
    # debe Abortar fail-closed y escribir cero filas.
    mod = _carga_tool()
    with db_f2("orbit_a5_mighuerfano") as conn:
        _semilla(conn)
        _destino(conn)
        _entidad(conn, "ad_group", "8188", parent=None)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="8188 sin campana padre"):
            mod.main(_argv_migrar(ag_dest="8188"))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_destino_otra_plataforma_aborta(monkeypatch):
    """Mismo `external_id` en MX y US: con `--plataforma amazon_us` el
    destino MX no existe (el id solo es unico con su plataforma)."""
    mod = _carga_tool()
    with db_f2("orbit_a5_migplat") as conn:
        _semilla(conn)
        camp_mx = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'campaign', '8001') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES ('amazon_mx', 'ad_group', '8101', %s)",
            (camp_mx,),
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="campana destino.*no existe"):
            mod.main(_argv_migrar())
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_texto_libre_aborta(monkeypatch):
    """`--destino-ad-group "exact-mx"`: texto sin fila en `ad_entity`; el
    tool no escribe lo tecleado."""
    mod = _carga_tool()
    with db_f2("orbit_a5_migtxt") as conn:
        _semilla(conn)
        _destino(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="ad group destino.*no existe"):
            mod.main(_argv_migrar(ag_dest="exact-mx"))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_sin_bid_avisa_harvest_sin_config(capsys, monkeypatch):
    """Sin goal con `harvest_default_bid` (MX sin goals): el plan avisa
    que el motor saltara con `harvest_sin_config`; no es error."""
    mod = _carga_tool()
    with db_f2("orbit_a5_migbid") as conn:
        origen = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'campaign', '7001') RETURNING id"
        ).fetchone()[0]
        assert origen
        camp_mx = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'campaign', '8001') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES ('amazon_mx', 'ad_group', '8101', %s)",
            (camp_mx,),
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar(platform="amazon_mx")) == 0
        out = capsys.readouterr().out
        assert "resolucion_hoy: skip motivo=sin_destino_de_harvest" in out
        assert "resolucion_despues: excepcion" in out
        assert "aviso" in out and "harvest_sin_config" in out
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_sin_parametros_aborta(monkeypatch):
    """`--migrar` sin `--plataforma/--campana/--destino-*`: Abortar de
    uso, no traceback ni defaults inventados."""
    mod = _carga_tool()
    with db_f2("orbit_a5_migargs") as conn:
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="exige --plataforma"):
            mod.main(["--migrar"])


# ---------------------------------------------------------------------------
# Bloque 3: `--migrar` go (ceremonia, idempotencia, congelada)
# ---------------------------------------------------------------------------

_GO_DUENO = "go dueno A.5 fase D.2"


def _argv_go(base, *, esperado=1, huella, go=_GO_DUENO):
    argv = [*base, "--acepto-mutacion-real", "--esperado", str(esperado), "--huella", huella]
    if go is not None:
        argv += ["--go", go]
    return argv


def _huella_dry_run(capsys) -> str:
    """La huella que vio el dueno: ultima linea `huella: <h>` del dry-run."""
    ultima = capsys.readouterr().out.strip().splitlines()[-1]
    prefijo, _, huella = ultima.partition("huella: ")
    assert prefijo == "" and huella, f"el dry-run no cerro con huella: {ultima!r}"
    return huella


def _siembra_migrable(conn):
    """`_semilla` (7001 sin grupo + goal plataforma) + destino 8001/8101."""
    _semilla(conn)
    return _destino(conn)


@_skip_db
def test_migrar_go_sin_esperado_aborta(capsys, monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_go_noesp") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        huella = _huella_dry_run(capsys)
        argv = [
            "--migrar",
            "--plataforma",
            "amazon_us",
            "--campana",
            "7001",
            "--destino-campana",
            "8001",
            "--destino-ad-group",
            "8101",
            "--acepto-mutacion-real",
            "--huella",
            huella,
            "--go",
            _GO_DUENO,
        ]
        with pytest.raises(mod.Abortar, match="--esperado"):
            mod.main(argv)
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_go_esperado_distinto_de_1_aborta(capsys, monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_go_esp2") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        with pytest.raises(mod.Abortar, match="--esperado 2 != 1"):
            mod.main(_argv_go(_argv_migrar(), esperado=2, huella=_huella_dry_run(capsys)))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_go_sin_go_aborta(capsys, monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_go_nogo") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        with pytest.raises(mod.Abortar, match="--go"):
            mod.main(_argv_go(_argv_migrar(), huella=_huella_dry_run(capsys), go=None))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_go_vacio_o_espacios_aborta(capsys, monkeypatch):
    """`--go ""` y `--go "   "` cuentan como ausentes (mas estricto que
    `reversa_harvest`, que solo caza el vacio)."""
    mod = _carga_tool()
    with db_f2("orbit_a5_go_vacio") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        huella = _huella_dry_run(capsys)
        for go in ("", "   "):
            with pytest.raises(mod.Abortar, match="--go"):
                mod.main(_argv_go(_argv_migrar(), huella=huella, go=go))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_go_sin_huella_aborta(capsys, monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_go_nohue") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        _huella_dry_run(capsys)
        argv = [
            "--migrar",
            "--plataforma",
            "amazon_us",
            "--campana",
            "7001",
            "--destino-campana",
            "8001",
            "--destino-ad-group",
            "8101",
            "--acepto-mutacion-real",
            "--esperado",
            "1",
            "--go",
            _GO_DUENO,
        ]
        with pytest.raises(mod.Abortar, match="--huella"):
            mod.main(argv)
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_go_huella_distinta_aborta(capsys, monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_go_huex") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        _huella_dry_run(capsys)
        with pytest.raises(mod.Abortar, match="huella.*cambio"):
            mod.main(_argv_go(_argv_migrar(), huella="0000000000000000"))
        assert _n_excepciones(conn) == 0


@_skip_db
def test_migrar_go_completo_escribe_fila_leida_y_resuelve_excepcion(capsys, monkeypatch):
    """Ceremonia completa: exactamente una fila con los externos LEIDOS
    de `ad_entity` y `go_literal` tal cual; `resolver_destino` da
    `excepcion` con ese par (readback del go y del test)."""
    mod = _carga_tool()
    with db_f2("orbit_a5_go_ok") as conn:
        camp_origen = conn.execute(
            "SELECT id FROM ad_entity WHERE platform = 'amazon_us'"
            " AND kind = 'campaign' AND external_id = '7001'"
        ).fetchone()
        assert camp_origen is None
        _siembra_migrable(conn)
        origen_id = conn.execute(
            "SELECT id FROM ad_entity WHERE platform = 'amazon_us'"
            " AND kind = 'campaign' AND external_id = '7001'"
        ).fetchone()[0]
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        assert mod.main(_argv_go(_argv_migrar(), huella=_huella_dry_run(capsys))) == 0
        out = capsys.readouterr().out
        assert "migrada" in out and "readback" in out
        fila = conn.execute(
            "SELECT ad_entity_id, destino_campaign_external, destino_ad_group_external,"
            " go_literal FROM harvest_excepcion"
        ).fetchall()
        assert fila == [(origen_id, "8001", "8101", _GO_DUENO)]
        res = resolver_destino(conn, "amazon_us", origen_id)
        assert isinstance(res, DestinoHarvest)
        assert res.resuelto_por == "excepcion"
        assert (res.campaign_external, res.ad_group_external) == ("8001", "8101")


@_skip_db
def test_migrar_segunda_corrida_ya_migrada(capsys, monkeypatch):
    """Idempotente: repetido el go (o un dry-run pelado) sale 0 con «ya
    migrada», sin exigir ceremonia y con una sola fila."""
    mod = _carga_tool()
    with db_f2("orbit_a5_go_idem") as conn:
        _siembra_migrable(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        argv_go = _argv_go(_argv_migrar(), huella=_huella_dry_run(capsys))
        assert mod.main(argv_go) == 0
        _ = capsys.readouterr()
        assert mod.main(argv_go) == 0
        assert "ya migrada" in capsys.readouterr().out
        assert mod.main(_argv_migrar()) == 0
        assert "ya migrada" in capsys.readouterr().out
        assert _n_excepciones(conn) == 1


@_skip_db
def test_migrar_par_distinto_no_pisa_congelada(capsys, monkeypatch):
    """Con excepcion a 8001/8101, pedir otro par aborta («congelada») y
    la fila original queda intacta."""
    mod = _carga_tool()
    with db_f2("orbit_a5_go_cong") as conn:
        _siembra_migrable(conn)
        otra = _entidad(conn, "campaign", "8002")
        _entidad(conn, "ad_group", "8199", parent=otra)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_migrar()) == 0
        assert mod.main(_argv_go(_argv_migrar(), huella=_huella_dry_run(capsys))) == 0
        _ = capsys.readouterr()
        with pytest.raises(mod.Abortar, match="congelada"):
            mod.main(_argv_migrar(camp_dest="8002", ag_dest="8199"))
        fila = conn.execute(
            "SELECT destino_campaign_external, destino_ad_group_external FROM harvest_excepcion"
        ).fetchall()
        assert fila == [("8001", "8101")]


# ---------------------------------------------------------------------------
# Bloque 4: `--limpiar-terna` dry-run y validacion
# ---------------------------------------------------------------------------


@contextmanager
def _como_app_admin(conn):
    """La escritura manual del test en tablas del dominio del tool corre
    con el rol real (`app_admin` tiene INSERT/UPDATE en goals por 0001 y
    en `harvest_excepcion` por 0018). Las siembras de entidades/grupos
    quedan como superuser, igual que `_semilla`/`_semilla_grupo` (el
    INSERT en `ad_entity` es de `app_ingest` por diseno de 0001)."""
    conn.execute("SET ROLE app_admin")
    try:
        yield conn
    finally:
        conn.execute("RESET ROLE")


def _argv_limpiar(grupo):
    return ["--limpiar-terna", "--grupo", str(grupo)]


def _goals_campana(conn, grupo_id):
    return conn.execute(
        "SELECT g.id, g.harvest_campaign_id, g.harvest_ad_group_id, g.harvest_default_bid,"
        " g.updated_at FROM ads_optimizer_goal g JOIN campana_grupo_rol r"
        " ON r.ad_entity_id = g.ad_entity_id AND g.scope = 'campaign'"
        " WHERE r.grupo_id = %s ORDER BY g.id",
        (grupo_id,),
    ).fetchall()


def _huella_limpieza(filas) -> str:
    """Replica del test de la huella del brief: sha256 de las lineas
    ordenadas `{goal_id}:{camp}/{ag}/{bid}` de las candidatas, [:16]
    (union con `\\n`; vacio = sha256 de vacio)."""
    lineas = sorted(f"{gid}:{camp}/{ag}/{bid}" for gid, camp, ag, bid, _ts in filas)
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()[:16]


@_skip_db
def test_limpiar_grupo_inexistente_aborta(monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_limpnog") as conn:
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="grupo 999 no existe"):
            mod.main(_argv_limpiar(999))


@_skip_db
def test_limpiar_dry_run_5_candidatas_huella_y_cero_update(capsys, monkeypatch):
    """5 goals con terna a la exacta: 5 candidatas, huella replicada,
    `updated_at` de los 5 intacto y el goal de plataforma fuera."""
    mod = _carga_tool()
    with db_f2("orbit_a5_limpplan") as conn:
        _semilla(conn)
        g = _semilla_grupo(conn)
        gid = g["grupo_id"]
        antes = _goals_campana(conn, gid)
        assert len(antes) == 5
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert any(f"grupo: id={gid} platform=amazon_us" in ln for ln in lineas)
        exacta = g["roles"]["category_exact"]
        assert any(
            f"exacta: campaign_external={exacta['camp_ext']}"
            f" ad_group_external={exacta['ag_ext']}" in ln
            for ln in lineas
        )
        assert sum("[candidata]" in ln for ln in lineas) == 5
        for fila in antes:
            assert any(f"goal={fila[0]} " in ln for ln in lineas)
        assert any("candidatas: 5" in ln for ln in lineas)
        assert lineas[-1] == f"huella: {_huella_limpieza(antes)}"
        despues = _goals_campana(conn, gid)
        assert [f[4] for f in despues] == [f[4] for f in antes]
        assert [f[1:4] for f in despues] == [f[1:4] for f in antes]


@_skip_db
def test_limpiar_terna_otro_destino_aborta_sin_tocar(capsys, monkeypatch):
    """Un goal con terna a otro destino: Abortar `destino_inconsistente`
    con el `goal_id`, sin mover `updated_at` de ninguno."""
    mod = _carga_tool()
    with db_f2("orbit_a5_limpinc") as conn:
        g = _semilla_grupo(conn)
        gid = g["grupo_id"]
        victima = _goals_campana(conn, gid)[0][0]
        with _como_app_admin(conn):
            conn.execute(
                "UPDATE ads_optimizer_goal SET harvest_campaign_id = '8001',"
                " harvest_ad_group_id = '8101' WHERE id = %s",
                (victima,),
            )
        antes = _goals_campana(conn, gid)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match=f"destino_inconsistente.*goal {victima}"):
            mod.main(_argv_limpiar(gid))
        despues = _goals_campana(conn, gid)
        assert [f[4] for f in despues] == [f[4] for f in antes]


@_skip_db
def test_limpiar_ya_limpias_no_cuentan(capsys, monkeypatch):
    """Ternas NULL/NULL: «ya limpia», 0 candidatas y huella del vacio."""
    mod = _carga_tool()
    with db_f2("orbit_a5_limpnul") as conn:
        g = _semilla_grupo(conn, con_terna=False)
        gid = g["grupo_id"]
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert sum("[ya limpia]" in ln for ln in lineas) == 5
        assert any("candidatas: 0" in ln for ln in lineas)
        assert lineas[-1] == "huella: e3b0c44298fc1c14"


@_skip_db
def test_limpiar_sin_goal_se_lista(capsys, monkeypatch):
    """Campana del grupo sin goal de campana: se lista «sin goal», no es
    error y no cuenta."""
    mod = _carga_tool()
    with db_f2("orbit_a5_limpsg") as conn:
        g = _semilla_grupo(conn, con_goals=False)
        gid = g["grupo_id"]
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert sum("[sin goal]" in ln for ln in lineas) == 5
        assert any("candidatas: 0" in ln for ln in lineas)
        assert lineas[-1] == "huella: e3b0c44298fc1c14"


@_skip_db
def test_limpiar_grupo_sin_discovery_aborta_antes_de_escribir(monkeypatch):
    """Sin campana `auto_discovery` no hay readback de resolucion: el
    dry-run aborta antes de imprimir el plan (fail-closed temprano)."""
    mod = _carga_tool()
    with db_f2("orbit_a5_limpdis") as conn:
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('lote-a5-sin-discovery', 'amazon_us',"
            " 'collar_perro', 'Base', 'go', 'h', '{}'::jsonb, 'live', 'applied')"
        )
        gid = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
            " VALUES ('amazon_us', 'collar_perro', 'Base', 'lote-a5-sin-discovery', 20, 20,"
            " 0.5, 't', 'go') RETURNING id"
        ).fetchone()[0]
        for rol, camp_ext, ag_ext in (
            ("category_phrase", "6151", "6251"),
            ("category_exact", "6154", "6254"),
        ):
            camp = _entidad(conn, "campaign", camp_ext)
            ag = _entidad(conn, "ad_group", ag_ext, parent=camp)
            conn.execute(
                "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
                " ad_group_ad_entity_id) VALUES (%s, %s, %s, %s)",
                (gid, rol, camp, ag),
            )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="sin campana auto_discovery"):
            mod.main(_argv_limpiar(gid))


@_skip_db
def test_limpiar_sin_grupo_param_aborta(monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_a5_limpar") as conn:
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="exige --grupo"):
            mod.main(["--limpiar-terna"])


@_skip_db
def test_limpiar_grupo_sin_exacta_aborta(monkeypatch):
    """Grupo sin fila `category_exact`: sin esa referencia no hay contra
    que comparar; Abortar fail-closed."""
    mod = _carga_tool()
    with db_f2("orbit_a5_limpexa") as conn:
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('lote-a5-sin-exacta', 'amazon_us',"
            " 'collar_perro', 'Base', 'go', 'h', '{}'::jsonb, 'live', 'applied')"
        )
        gid = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
            " VALUES ('amazon_us', 'collar_perro', 'Base', 'lote-a5-sin-exacta', 20, 20, 0.5,"
            " 't', 'go') RETURNING id"
        ).fetchone()[0]
        camp = _entidad(conn, "campaign", "6150")
        ag = _entidad(conn, "ad_group", "6250", parent=camp)
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
            " VALUES (%s, 'category_phrase', %s, %s)",
            (gid, camp, ag),
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="sin rol category_exact"):
            mod.main(_argv_limpiar(gid))


# ---------------------------------------------------------------------------
# Bloque 5: `--limpiar-terna` go (ceremonia, reanudable, readback)
# ---------------------------------------------------------------------------


def _argv_go_limpiar(grupo, *, esperado, huella, go=_GO_DUENO):
    argv = [
        "--limpiar-terna",
        "--grupo",
        str(grupo),
        "--acepto-mutacion-real",
        "--esperado",
        str(esperado),
        "--huella",
        huella,
    ]
    if go is not None:
        argv += ["--go", go]
    return argv


def _ternas(conn, grupo_id):
    return [(f[1], f[2], f[3]) for f in _goals_campana(conn, grupo_id)]


@_skip_db
def test_limpiar_go_ceremonia_incompleta_aborta_ternas_intactas(capsys, monkeypatch):
    """Sin `--esperado`, sin `--go` o con huella distinta: Abortar y las
    5 ternas intactas (orden esperado -> go -> huella)."""
    mod = _carga_tool()
    with db_f2("orbit_a5_lgo_cer") as conn:
        _semilla(conn)
        gid = _semilla_grupo(conn)["grupo_id"]
        intactas = _ternas(conn, gid)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        huella = _huella_dry_run(capsys)
        base = ["--limpiar-terna", "--grupo", str(gid), "--acepto-mutacion-real"]
        casos = [
            (base + ["--go", _GO_DUENO, "--huella", huella], "--esperado"),
            (base + ["--esperado", "5", "--huella", huella], "--go"),
            (
                base + ["--esperado", "5", "--huella", "0000000000000000", "--go", _GO_DUENO],
                "huella.*cambio",
            ),
            (
                base + ["--esperado", "4", "--huella", huella, "--go", _GO_DUENO],
                "--esperado 4 != 5",
            ),
        ]
        for argv, motivo in casos:
            with pytest.raises(mod.Abortar, match=motivo):
                mod.main(argv)
        assert _ternas(conn, gid) == intactas


@_skip_db
def test_limpiar_go_completo_limpia_5_bid_intacto(capsys, monkeypatch):
    """Go completo: 5 goals con campaign/ad_group NULL, bid `11.6200`
    intacto, `updated_at` movido, la `category_phrase` sigue resolviendo
    `grupo` y el goal de plataforma intacto."""
    mod = _carga_tool()
    with db_f2("orbit_a5_lgo_ok") as conn:
        _semilla(conn)
        g = _semilla_grupo(conn)
        gid = g["grupo_id"]
        antes = {f[0]: f[4] for f in _goals_campana(conn, gid)}
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        argv_go = _argv_go_limpiar(gid, esperado=5, huella=_huella_dry_run(capsys))
        assert mod.main(argv_go) == 0
        out = capsys.readouterr().out
        assert out.count("limpio: goal=") == 5
        assert "readback" in out and "resuelto_por=grupo" in out
        despues = _goals_campana(conn, gid)
        assert [(f[1], f[2], f[3]) for f in despues] == [(None, None, Decimal("11.6200"))] * 5
        assert all(f[4] != antes[f[0]] for f in despues)
        phrase = g["roles"]["category_phrase"]["camp"]
        res = resolver_destino(conn, "amazon_us", phrase)
        assert isinstance(res, DestinoHarvest)
        assert res.resuelto_por == "grupo" and res.motivo is None
        plat = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid"
            " FROM ads_optimizer_goal WHERE scope = 'platform'"
        ).fetchone()
        assert tuple(plat) == ("8001", "8101", Decimal("1.00"))


@_skip_db
def test_limpiar_no_toca_goals_fuera_del_grupo(capsys, monkeypatch):
    """Mutante: limpiar goals de campanas fuera del grupo. Un goal de
    campana ajena (7001, sin grupo) con terna a la exacta, y un SEGUNDO
    grupo con sus 5 goals, quedan intactos tras el go del primero
    (terna y `updated_at`). Sin el filtro de grupo el go veria 10
    candidatas y la ceremonia de 5 abortaria."""
    mod = _carga_tool()
    with db_f2("orbit_a5_lgo_fuera") as conn:
        ids = _semilla(conn)
        g = _semilla_grupo(conn)
        gid = g["grupo_id"]
        exacta = g["roles"]["category_exact"]
        with _como_app_admin(conn):
            ajeno = conn.execute(
                "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
                " bid_floor, bid_ceiling, bid_currency, harvest_campaign_id,"
                " harvest_ad_group_id, harvest_default_bid, enabled, mode)"
                " VALUES ('campaign', %s, 55, 0.10, 2.50, 'USD', %s, %s, 1.00, true, 'live')"
                " RETURNING id, updated_at",
                (ids["camp"], exacta["camp_ext"], exacta["ag_ext"]),
            ).fetchone()
        otro = _semilla_grupo(conn, platform="amazon_mx")
        intacto_otro = _goals_campana(conn, otro["grupo_id"])
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        argv_go = _argv_go_limpiar(gid, esperado=5, huella=_huella_dry_run(capsys))
        assert mod.main(argv_go) == 0
        fila = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid, updated_at"
            " FROM ads_optimizer_goal WHERE id = %s",
            (ajeno[0],),
        ).fetchone()
        assert tuple(fila) == (exacta["camp_ext"], exacta["ag_ext"], Decimal("1.00"), ajeno[1])
        assert _goals_campana(conn, otro["grupo_id"]) == intacto_otro


@_skip_db
def test_limpiar_segunda_corrida_0_candidatas(capsys, monkeypatch):
    """Tras el go, un dry-run da 0 candidatas («ya limpia»), exit 0 y
    nada cambia."""
    mod = _carga_tool()
    with db_f2("orbit_a5_lgo_idem") as conn:
        gid = _semilla_grupo(conn)["grupo_id"]
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        assert mod.main(_argv_go_limpiar(gid, esperado=5, huella=_huella_dry_run(capsys))) == 0
        sellos = [f[4] for f in _goals_campana(conn, gid)]
        _ = capsys.readouterr()
        assert mod.main(_argv_limpiar(gid)) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert sum("[ya limpia]" in ln for ln in lineas) == 5
        assert any("candidatas: 0" in ln for ln in lineas)
        assert [f[4] for f in _goals_campana(conn, gid)] == sellos


@_skip_db
def test_limpiar_fallo_tercer_goal_aborta_y_reanuda(capsys, monkeypatch):
    """`GoalInvalido` inyectado en el tercer `edita_goal`: 2 limpias, 3
    intactas, Abortar con el `goal_id`; un go nuevo (nueva ceremonia de
    3) limpia las restantes."""
    mod = _carga_tool()
    with db_f2("orbit_a5_lgo_rean") as conn:
        gid = _semilla_grupo(conn)["grupo_id"]
        orden = [
            f[0]
            for f in conn.execute(
                "SELECT g.id FROM ads_optimizer_goal g JOIN campana_grupo_rol r"
                " ON r.ad_entity_id = g.ad_entity_id AND g.scope = 'campaign'"
                " WHERE r.grupo_id = %s ORDER BY r.ad_entity_id",
                (gid,),
            ).fetchall()
        ]
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(_argv_limpiar(gid)) == 0
        argv_go = _argv_go_limpiar(gid, esperado=5, huella=_huella_dry_run(capsys))
        real = mod.edita_goal
        llamadas: list[int] = []

        def _falla_tercera(conn, goal_id, **kw):
            llamadas.append(goal_id)
            if len(llamadas) == 3:
                raise GoalInvalido("fallo inyectado en el tercer goal")
            return real(conn, goal_id, **kw)

        monkeypatch.setattr(mod, "edita_goal", _falla_tercera)
        with pytest.raises(mod.Abortar, match=f"goal {orden[2]}.*limpias 2 de 5"):
            mod.main(argv_go)
        estado = {f[0]: (f[1], f[2]) for f in _goals_campana(conn, gid)}
        assert [estado[gid_] for gid_ in orden[:2]] == [(None, None)] * 2
        assert all(estado[gid_] != (None, None) for gid_ in orden[2:])
        monkeypatch.setattr(mod, "edita_goal", real)
        _ = capsys.readouterr()
        assert mod.main(_argv_limpiar(gid)) == 0
        argv_go2 = _argv_go_limpiar(gid, esperado=3, huella=_huella_dry_run(capsys))
        assert mod.main(argv_go2) == 0
        assert [f[1:3] for f in _goals_campana(conn, gid)] == [(None, None)] * 5
