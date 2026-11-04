"""Modo de los goals de un grupo con ceremonia (precondicion de D.3).

`tools/goals_modo_grupo.py` (patron `tools/harvest_excepcion.py`):
`--grupo N --mode shadow|live`, solo Postgres y solo `app_admin` (unico
DSN: `ORBIT_DSN_ADMIN`), cero Amazon, cero apply. Dry-run por defecto
(tabla de candidatas + envolvente + huella); mutacion real con la
ceremonia completa, goal por goal via `goals_write.edita_goal`,
reanudable, con readback del mode leido y el modo efectivo (meet).
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
from psycopg.types.json import Json
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_harvest_excepcion import _dsn_admin_de
from test_schema import _postgres_obligatorio_ausente

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_RUTA_TOOL = Path(__file__).resolve().parent.parent / "tools" / "goals_modo_grupo.py"


def _carga_tool():
    """Carga `tools/goals_modo_grupo.py` como modulo (patron
    `test_harvest_excepcion._carga_tool`); los tests llaman
    `mod.main([...])` en proceso."""
    spec = importlib.util.spec_from_file_location("goals_modo_grupo", _RUTA_TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_tool_se_importa_y_expone_main_y_abortar():
    """Rojo inicial: el modulo existe y expone `main(argv) -> int` y
    `Abortar(RuntimeError)`."""
    mod = _carga_tool()
    assert callable(mod.main)
    assert issubclass(mod.Abortar, RuntimeError)


def test_cli_sin_grupo_aborta(monkeypatch):
    """Sin `--grupo`: Abortar de uso (antes de tocar la base)."""
    mod = _carga_tool()
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    with pytest.raises(mod.Abortar, match="--grupo"):
        mod.main(["--mode", "live"])


def test_cli_sin_mode_aborta(monkeypatch):
    """Sin `--mode`: Abortar de uso (antes de tocar la base)."""
    mod = _carga_tool()
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    with pytest.raises(mod.Abortar, match="--mode"):
        mod.main(["--grupo", "1"])


def test_cli_mode_off_aborta(monkeypatch):
    """El tool solo enciende o ensombrece (`shadow|live`): `--mode off`
    aborta (el apagado es goal por goal con `goals set`, kill switch)."""
    mod = _carga_tool()
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    with pytest.raises(mod.Abortar, match="shadow o live"):
        mod.main(["--grupo", "1", "--mode", "off"])


def test_cli_sin_dsn_admin_aborta(monkeypatch):
    """Sin `ORBIT_DSN_ADMIN` en el entorno: `Abortar` (el tool no lee
    otros DSN ni acepta DSN por argumentos)."""
    mod = _carga_tool()
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    with pytest.raises(mod.Abortar, match="ORBIT_DSN_ADMIN"):
        mod.main(["--grupo", "1", "--mode", "live"])


def test_tool_solo_admin_sin_amazon_ni_apply():
    """Fronteras del tool (estaticas): un solo DSN (`ORBIT_DSN_ADMIN`;
    `ORBIT_DSN_DECIDE`/`ORBIT_DSN_READ` ni en texto), cero `app.ads.*`,
    cero `app.apply*`, cero `httpx`, cero import dinamico; escribe goals
    SOLO via `app.goals_write.edita_goal` (el candado de arquitectura lo
    cruza contra UPDATE/INSERT crudos)."""
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
    assert "edita_goal" in nombres


# ---------------------------------------------------------------------------
# Dry-run: tabla + envolvente + huella, cero escritura
# ---------------------------------------------------------------------------


def _siembra_envolvente(conn, valor):
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t', %s)",
        (Json({"ads_optimizer_mode": valor}),),
    )


def _ids_goals_grupo(conn, grupo_id):
    return [
        r[0]
        for r in conn.execute(
            "SELECT g.id FROM ads_optimizer_goal g"
            " JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id"
            " WHERE g.scope = 'campaign' AND r.grupo_id = %s ORDER BY g.id",
            (grupo_id,),
        ).fetchall()
    ]


def _huella_esperada(grupo_id, pedido, ids):
    base = f"{grupo_id}:{pedido}:{','.join(str(i) for i in sorted(ids))}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


@_skip_db
def test_dry_run_muestra_tabla_y_huella_sin_escribir(capsys, monkeypatch):
    """Dry-run feliz: grupo, envolvente con la nota del meet, una linea
    por candidata (`goal | rol | actual → pedido | terna`), conteo y
    huella sobre (grupo, pedido, ids); cero escritura."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_dry") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(["--grupo", str(g["grupo_id"]), "--mode", "live"]) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert lineas[0].startswith(f"grupo: id={g['grupo_id']} platform=amazon_us")
        assert "envolvente: ads_optimizer_mode=shadow" in lineas[1]
        assert "modo efectivo = meet" in lineas[1]
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        assert len(ids) == 5
        for gid in ids:
            assert any(
                ln.startswith(f"goal={gid} rol=") and "shadow → live terna" in ln for ln in lineas
            ), gid
        assert "candidatas: 5" in lineas
        assert f"huella: {_huella_esperada(g['grupo_id'], 'live', ids)}" in lineas
        modes = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT mode FROM ads_optimizer_goal WHERE scope = 'campaign'"
            ).fetchall()
        }
        assert modes == {"shadow"}


@_skip_db
def test_dry_run_grupo_inexistente_aborta_sin_escribir(monkeypatch):
    mod = _carga_tool()
    with db_f2("orbit_gmg_nogrupo") as conn:
        _semilla_grupo(conn, mode="shadow")
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="grupo 9999 no existe"):
            mod.main(["--grupo", "9999", "--mode", "live"])


@_skip_db
def test_dry_run_ya_esta_no_cuenta_ni_entra_a_huella(capsys, monkeypatch):
    """Un goal ya en el modo pedido se informa («ya esta») pero no es
    candidata: ni cuenta ni entra a la huella."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_yaesta") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'live' WHERE id = %s", (ids[0],))
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(["--grupo", str(g["grupo_id"]), "--mode", "live"]) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert any(ln.startswith(f"[ya esta] goal={ids[0]}") for ln in lineas)
        assert "candidatas: 4" in lineas
        assert f"huella: {_huella_esperada(g['grupo_id'], 'live', ids[1:])}" in lineas


@_skip_db
def test_dry_run_sin_goal_informa_y_no_cuenta(capsys, monkeypatch):
    """Campana del grupo sin goal: se informa («sin goal») y no cuenta."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_singoal") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        conn.execute("DELETE FROM ads_optimizer_goal WHERE id = %s", (ids[0],))
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(["--grupo", str(g["grupo_id"]), "--mode", "live"]) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert any(ln.startswith("[sin goal] rol=") for ln in lineas)
        assert "candidatas: 4" in lineas


@_skip_db
def test_dry_run_terna_bid_solo_y_sin_terna(capsys, monkeypatch):
    """La columna de destino distingue terna completa, bid-solo y
    sin-terna."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_dest") as conn:
        g = _semilla_grupo(conn, mode="shadow", con_terna=False)
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        conn.execute(
            "UPDATE ads_optimizer_goal SET harvest_campaign_id = 'c',"
            " harvest_ad_group_id = 'ag', harvest_default_bid = 3 WHERE id = %s",
            (ids[0],),
        )
        conn.execute(
            "UPDATE ads_optimizer_goal SET harvest_default_bid = 5 WHERE id = %s", (ids[1],)
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        assert mod.main(["--grupo", str(g["grupo_id"]), "--mode", "live"]) == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        assert any(ln.startswith(f"goal={ids[0]} ") and ln.endswith("terna") for ln in lineas)
        assert any(ln.startswith(f"goal={ids[1]} ") and ln.endswith("bid-solo") for ln in lineas)
        assert any(ln.startswith(f"goal={ids[2]} ") and ln.endswith("sin-terna") for ln in lineas)


# ---------------------------------------------------------------------------
# Go: ceremonia, escritura acotada, reanudacion y readback con meet
# ---------------------------------------------------------------------------


def _argv_go(grupo_id, pedido, *, esperado, huella, go="enciende grupo 1"):
    return [
        "--grupo",
        str(grupo_id),
        "--mode",
        pedido,
        "--acepto-mutacion-real",
        "--esperado",
        str(esperado),
        "--huella",
        huella,
        "--go",
        go,
    ]


def _modes_grupo(conn, grupo_id):
    return {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT g.id, g.mode FROM ads_optimizer_goal g"
            " JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id"
            " WHERE g.scope = 'campaign' AND r.grupo_id = %s",
            (grupo_id,),
        ).fetchall()
    }


@_skip_db
def test_go_esperado_distinto_aborta_sin_escribir(monkeypatch):
    """`--esperado` != candidatas: Abortar y cero escritura."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_esp") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="--esperado 4 != 5 candidatas"):
            mod.main(
                _argv_go(
                    g["grupo_id"],
                    "live",
                    esperado=4,
                    huella=_huella_esperada(g["grupo_id"], "live", ids),
                )
            )
        assert set(_modes_grupo(conn, g["grupo_id"]).values()) == {"shadow"}


@_skip_db
def test_go_huella_distinta_aborta_sin_escribir(monkeypatch):
    """Huella != dry-run: Abortar (el conjunto cambio) y cero escritura."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_huella") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="huella 00.*!= huella"):
            mod.main(_argv_go(g["grupo_id"], "live", esperado=5, huella="00" * 8))
        assert set(_modes_grupo(conn, g["grupo_id"]).values()) == {"shadow"}


@_skip_db
def test_go_esperado_que_cuenta_ya_esta_aborta(monkeypatch):
    """`--esperado` que cuenta los «ya esta» aborta: solo las candidatas
    autorizan."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_espye") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'live' WHERE id = %s", (ids[0],))
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="--esperado 5 != 4 candidatas"):
            mod.main(
                _argv_go(
                    g["grupo_id"],
                    "live",
                    esperado=5,
                    huella=_huella_esperada(g["grupo_id"], "live", ids[1:]),
                )
            )


@_skip_db
def test_go_sin_esperado_ni_go_aborta(monkeypatch):
    """Ceremonia incompleta: sin `--esperado` o sin `--go` (o vacio),
    Abortar sin escribir."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_cerem") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        huella = _huella_esperada(g["grupo_id"], "live", ids)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        base = ["--grupo", str(g["grupo_id"]), "--mode", "live", "--acepto-mutacion-real"]
        with pytest.raises(mod.Abortar, match="exige --esperado"):
            mod.main(base + ["--huella", huella, "--go", "x"])
        with pytest.raises(mod.Abortar, match="exige --go"):
            mod.main(base + ["--esperado", "5", "--huella", huella])
        with pytest.raises(mod.Abortar, match="exige --go"):
            mod.main(base + ["--esperado", "5", "--huella", huella, "--go", "   "])
        assert set(_modes_grupo(conn, g["grupo_id"]).values()) == {"shadow"}


@_skip_db
def test_go_escribe_solo_los_del_grupo(capsys, monkeypatch):
    """El go mueve SOLO los goals del grupo pedido: otro grupo y un
    goal de plataforma quedan intactos; el readback trae el mode nuevo."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_acota") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        otro = _semilla_grupo(conn, platform="amazon_mx", tipo_producto="otro", mode="shadow")
        plat = conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
            " bid_ceiling, bid_currency, enabled, mode) VALUES ('platform', 'amazon_us',"
            " 20, 0.10, 2.50, 'USD', true, 'shadow') RETURNING id"
        ).fetchone()[0]
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        rc = mod.main(
            _argv_go(
                g["grupo_id"],
                "live",
                esperado=5,
                huella=_huella_esperada(g["grupo_id"], "live", ids),
            )
        )
        assert rc == 0
        assert set(_modes_grupo(conn, g["grupo_id"]).values()) == {"live"}
        assert set(_modes_grupo(conn, otro["grupo_id"]).values()) == {"shadow"}
        mode_plat = conn.execute(
            "SELECT mode FROM ads_optimizer_goal WHERE id = %s", (plat,)
        ).fetchone()[0]
        assert mode_plat == "shadow"
        lineas = capsys.readouterr().out.strip().splitlines()
        for gid in ids:
            assert any(ln == f"readback: goal={gid} mode=live efectivo=shadow" for ln in lineas), (
                gid
            )


@_skip_db
def test_go_ya_esta_no_se_reescribe(monkeypatch):
    """Un «ya esta» no se reescribe: su `updated_at` queda intacto tras
    el go (edita_goal siempre re-sella: si lo tocara, se veria)."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_norew") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'live' WHERE id = %s", (ids[0],))
        sello = conn.execute(
            "SELECT updated_at FROM ads_optimizer_goal WHERE id = %s", (ids[0],)
        ).fetchone()[0]
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        rc = mod.main(
            _argv_go(
                g["grupo_id"],
                "live",
                esperado=4,
                huella=_huella_esperada(g["grupo_id"], "live", ids[1:]),
            )
        )
        assert rc == 0
        sello_despues = conn.execute(
            "SELECT updated_at FROM ads_optimizer_goal WHERE id = %s", (ids[0],)
        ).fetchone()[0]
        assert sello_despues == sello


@_skip_db
def test_go_fallo_a_mitad_aborta_con_id_y_reanuda(capsys, monkeypatch):
    """Fallo inyectado en el segundo goal: aborta con su id, el primero
    queda cambiado (sin transaccion global) y re-correr con ceremonia
    nueva termina el resto."""
    from app.goals_write import GoalInvalido

    mod = _carga_tool()
    with db_f2("orbit_gmg_reanuda") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        orden = [
            r[0]
            for r in conn.execute(
                "SELECT g.id FROM ads_optimizer_goal g"
                " JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id"
                " WHERE g.scope = 'campaign' AND r.grupo_id = %s ORDER BY r.ad_entity_id",
                (g["grupo_id"],),
            ).fetchall()
        ]
        real = mod.edita_goal
        llamadas = []

        def _falla_segundo(conn_e, goal_id, **kw):
            llamadas.append(goal_id)
            if len(llamadas) == 2:
                raise GoalInvalido("fallo inyectado en el segundo goal")
            return real(conn_e, goal_id, **kw)

        monkeypatch.setattr(mod, "edita_goal", _falla_segundo)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        # El match exige el mensaje DEL LOOP (con el motivo inyectado y el
        # conteo): si el tool siguiera tras el fallo, abortaria en el
        # readback («quedo shadow») y este match no morderia.
        with pytest.raises(mod.Abortar, match=f"goal {orden[1]}: fallo inyectado"):
            mod.main(
                _argv_go(
                    g["grupo_id"],
                    "live",
                    esperado=5,
                    huella=_huella_esperada(g["grupo_id"], "live", orden),
                )
            )
        modes = _modes_grupo(conn, g["grupo_id"])
        assert modes[orden[0]] == "live"
        assert modes[orden[1]] == "shadow"
        # Re-correr con ceremonia NUEVA (la huella cambio: uno ya esta).
        monkeypatch.setattr(mod, "edita_goal", real)
        capsys.readouterr()
        rc = mod.main(
            _argv_go(
                g["grupo_id"],
                "live",
                esperado=4,
                huella=_huella_esperada(g["grupo_id"], "live", orden[1:]),
            )
        )
        assert rc == 0
        assert set(_modes_grupo(conn, g["grupo_id"]).values()) == {"live"}


@_skip_db
def test_go_readback_modo_efectivo_con_envolvente_live(capsys, monkeypatch):
    """Con la envolvente en `live`, el readback muestra meet live (el
    caso shadow ya lo pinza `test_go_escribe_solo_los_del_grupo`)."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_meet") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "live")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        rc = mod.main(
            _argv_go(
                g["grupo_id"],
                "live",
                esperado=5,
                huella=_huella_esperada(g["grupo_id"], "live", ids),
            )
        )
        assert rc == 0
        lineas = capsys.readouterr().out.strip().splitlines()
        for gid in ids:
            assert any(ln == f"readback: goal={gid} mode=live efectivo=live" for ln in lineas), gid


@_skip_db
def test_go_readback_imprime_lo_leido_no_lo_pedido(capsys, monkeypatch):
    """El readback imprime el mode LEIDO de vuelta, no el pedido: con
    `edita_goal` saboteado (no escribe), el go aborta declarando lo
    leido (`shadow`) y la linea impresa tambien dice `shadow`."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_leido") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        monkeypatch.setattr(mod, "edita_goal", lambda *a, **k: {"id": a[1]})
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match=f"goal {ids[0]} quedo shadow"):
            mod.main(
                _argv_go(
                    g["grupo_id"],
                    "live",
                    esperado=5,
                    huella=_huella_esperada(g["grupo_id"], "live", ids),
                )
            )
        lineas = capsys.readouterr().out.strip().splitlines()
        assert any(f"readback: goal={ids[0]} mode=shadow" in ln for ln in lineas)


@_skip_db
def test_go_readback_goal_desaparecido_aborta_limpio(monkeypatch):
    """Si un goal desaparece entre la edicion y el readback, el tool
    aborta limpio (Abortar, no ValueError crudo de `modo_efectivo`)."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_desap") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])

        def _borra_en_vez_de_editar(conn_e, goal_id, **kw):
            conn_e.execute("DELETE FROM ads_optimizer_goal WHERE id = %s", (goal_id,))
            return {"id": goal_id}

        monkeypatch.setattr(mod, "edita_goal", _borra_en_vez_de_editar)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        with pytest.raises(mod.Abortar, match="desaparecio a mitad"):
            mod.main(
                _argv_go(
                    g["grupo_id"],
                    "live",
                    esperado=5,
                    huella=_huella_esperada(g["grupo_id"], "live", ids),
                )
            )


@_skip_db
def test_go_sobre_grupo_bid_solo_escribe_los_cinco(capsys, monkeypatch):
    """Estado post-D.2: los cinco goals en bid-solo (puestos por el
    camino unico, como deja D.2). El go los enciende a `live` y el
    readback los lee live."""
    import datetime as dt

    from app.goals_write import edita_goal

    mod = _carga_tool()
    with db_f2("orbit_gmg_postd2") as conn:
        g = _semilla_grupo(conn, mode="shadow")
        _siembra_envolvente(conn, "shadow")
        ids = _ids_goals_grupo(conn, g["grupo_id"])
        for gid in ids:
            edita_goal(conn, gid, harvest_limpia_destino=True, updated_at=dt.datetime.now(dt.UTC))
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        rc = mod.main(
            _argv_go(
                g["grupo_id"],
                "live",
                esperado=5,
                huella=_huella_esperada(g["grupo_id"], "live", ids),
            )
        )
        assert rc == 0
        assert set(_modes_grupo(conn, g["grupo_id"]).values()) == {"live"}
        lineas = capsys.readouterr().out.strip().splitlines()
        for gid in ids:
            assert any(ln == f"readback: goal={gid} mode=live efectivo=shadow" for ln in lineas)


@_skip_db
def test_go_cero_candidatas_idempotente(capsys, monkeypatch):
    """Todo «ya esta»: el go con esperado 0 y la huella del vacio es
    idempotente (rc 0, nada que verificar)."""
    mod = _carga_tool()
    with db_f2("orbit_gmg_cero") as conn:
        g = _semilla_grupo(conn, mode="live")
        _siembra_envolvente(conn, "shadow")
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))
        rc = mod.main(
            _argv_go(
                g["grupo_id"],
                "live",
                esperado=0,
                huella=_huella_esperada(g["grupo_id"], "live", []),
            )
        )
        assert rc == 0
        assert "readback: sin candidatas, nada cambio" in capsys.readouterr().out
