"""AUTO-04 Task 6: sugerencia de ubicacion en pantalla, resumen en Salud y
verificador de replay determinista."""

from __future__ import annotations

import datetime as dt

import pytest
from test_apply_ubicacion import (
    _ANCLA,
    _caso,
    _ciclo,
    _config,
    _config_version,
    _db,
    _settings,
    _siembra,
)
from test_schema import _postgres_obligatorio_ausente

from app import ui
from app.ads.campana_config import guarda_config
from app.optimizer.ubicacion import MantenerUbicacion, MoverUbicacion, decide_ubicacion
from app.pantalla_dinero import lee_campanas
from app.ubicacion_io import guarda_evaluaciones
from tools.auto04_verifica import main, resumen_practica, verifica_replay

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(), reason="sin Postgres utilizable en ORBIT_TEST_DSN"
)
_HASTA = _ANCLA.date() - dt.timedelta(days=1)
_SUBE = MoverUbicacion("arriba_de_busqueda", 0, 15, "bajo_meta_con_ventaja")


def _guarda(conn, ciclo, cv, evaluaciones) -> dict[int, int]:
    return guarda_evaluaciones(
        conn,
        cycle_id=ciclo,
        config_version_id=cv,
        platform="amazon_mx",
        decided_at=_ANCLA,
        evaluaciones=evaluaciones,
    )


def _plano(html: str) -> str:
    return " ".join(html.split())


@_skip_db
def test_donde_poner_el_dinero_muestra_sugerencia_practica_vigente():
    with _db() as (conn, _dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        _guarda(
            conn, _ciclo(conn, "shadow"), cv, [(_caso(camp, config_id, 0, 15), "shadow", _SUBE)]
        )

        (fila,) = lee_campanas(
            conn, plataforma="amazon_mx", desde=_HASTA - dt.timedelta(days=29), hasta=_HASTA
        )
        sugerencia = fila.como_dict()["sugerencia_ubicacion"]
        assert sugerencia["frase"] == (
            "Práctica: subir arriba de búsqueda de +0 % a +15 % (bajo la meta con ventaja"
            " contra el resto de la campana)."
        )
        html = ui.templates.env.get_template("donde_poner_el_dinero.html").render(
            pantalla="donde-poner-el-dinero",
            plataforma="amazon_mx",
            moneda="MXN",
            desde="2026-10-27",
            hasta="2026-11-25",
            filas=[],
            total=None,
            hojas_sin_clasificar=0,
            target_acos_pct=None,
            clases_ajuste=["ajuste_ubicacion"],
            por_campana=[fila.como_dict()],
        )
        assert "data-sugerencia-ubicacion" in html
        assert sugerencia["frase"] in _plano(html)

        guarda_config(conn, "amazon_mx", [_config(5)], _ANCLA + dt.timedelta(hours=1))
        (fila,) = lee_campanas(
            conn, plataforma="amazon_mx", desde=_HASTA - dt.timedelta(days=29), hasta=_HASTA
        )
        assert fila.como_dict()["sugerencia_ubicacion"] is None, "config nueva: sugerencia vieja"


@_skip_db
def test_salud_incluye_cuota_y_resumen_ubicaciones():
    from app.dashboard_salud import leer_salud
    from app.ui_salud import vista_salud

    with _db() as (conn, _dsn):
        cv = _config_version(conn, _settings())
        uno, cfg1 = _siembra(conn, top=0)
        dos, cfg2 = _siembra(conn, top=0, externa="93600000000002")
        _guarda(
            conn,
            _ciclo(conn, "shadow"),
            cv,
            [
                (_caso(uno, cfg1, 0, 15), "shadow", _SUBE),
                (_caso(dos, cfg2, 0, 15), "shadow", MantenerUbicacion("sin_evidencia_ubicacion")),
            ],
        )
        datos = leer_salud(
            conn, hoy_pantalla_fn=lambda *_a: _ANCLA.date(), spapi_salud_fn=lambda *_a, **_k: None
        )
        mx = datos["plataformas"]["amazon_mx"]
        assert mx["quota"]["ubicacion"]["cap"] == 2
        assert mx["ubicaciones"] == {"evaluadas": 2, "mover": 1, "en_practica": 1, "aplicadas": 0}
        assert datos["plataformas"]["amazon_us"]["ubicaciones"] is None

        vista = vista_salud(datos["plataformas"], None, "amazon_mx")
        html = _plano(
            ui.templates.env.get_template("salud.html").render(pantalla="salud", vista=vista)
        )
        assert "Ajuste de ubicación" in html
        assert "Ubicaciones del último ciclo" in html


@_skip_db
def test_auto04_verifica_replay_determinista_sin_discrepancias(monkeypatch, capsys):
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        uno, cfg1 = _siembra(conn, top=0)
        dos, cfg2 = _siembra(conn, top=0, externa="93600000000002")
        tres, cfg3 = _siembra(conn, top=0, externa="93600000000003")
        caso1, caso2, caso3 = (
            _caso(uno, cfg1, 0, 15),
            _caso(dos, cfg2, 0, 15),
            _caso(tres, cfg3, 0, 15),
        )
        ids = _guarda(
            conn,
            _ciclo(conn, "shadow"),
            cv,
            [
                (caso1, "shadow", decide_ubicacion(caso1)),
                (caso2, "shadow", MantenerUbicacion("producto_sin_evidencia")),
            ],
        )
        limpio = verifica_replay(conn)
        assert (limpio.revisadas, limpio.sustituidas, limpio.discrepancias) == (2, 1, ())

        alterada = _guarda(
            conn, _ciclo(conn, "shadow"), cv, [(caso3, "shadow", MantenerUbicacion("otro_motivo"))]
        )
        sucio = verifica_replay(conn)
        assert sucio.discrepancias == (alterada[tres],)
        assert ids[uno] not in sucio.discrepancias
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        assert main(["replay", "--platform", "amazon_mx"]) == 1
        assert f'"discrepancias": [{alterada[tres]}]' in capsys.readouterr().out

        resumen = resumen_practica(conn, "amazon_mx", hoy=_ANCLA.date(), dias=14)
        assert resumen["dias_en_practica"] == 1
        assert resumen["evaluaciones"] == {"shadow": 3}


@_skip_db
def test_auto04_verifica_replay_vacio_o_solo_sustituidas_sale_con_1(monkeypatch, capsys):
    """Punto 1 (#788): exit 0 reservado a un replay con al menos una comparacion
    real y cero discrepancias."""
    import json as _json

    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        uno, cfg1 = _siembra(conn, top=0)
        dos, cfg2 = _siembra(conn, top=0, externa="93600000000002")
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)

        assert main(["replay", "--platform", "amazon_mx"]) == 1
        salida_vacia = _json.loads(capsys.readouterr().out)
        assert salida_vacia == {
            "comparadas": 0,
            "discrepancias": [],
            "revisadas": 0,
            "sustituidas": 0,
        }

        _guarda(
            conn,
            _ciclo(conn, "shadow"),
            cv,
            [(_caso(uno, cfg1, 0, 15), "shadow", MantenerUbicacion("producto_sin_evidencia"))],
        )
        assert main(["replay", "--platform", "amazon_mx"]) == 1
        salida_sust = _json.loads(capsys.readouterr().out)
        assert salida_sust == {
            "comparadas": 0,
            "discrepancias": [],
            "revisadas": 1,
            "sustituidas": 1,
        }

        caso2 = _caso(dos, cfg2, 0, 15)
        _guarda(conn, _ciclo(conn, "shadow"), cv, [(caso2, "shadow", decide_ubicacion(caso2))])
        assert main(["replay", "--platform", "amazon_mx"]) == 0
        salida_ok = _json.loads(capsys.readouterr().out)
        assert salida_ok == {
            "comparadas": 1,
            "discrepancias": [],
            "revisadas": 2,
            "sustituidas": 1,
        }
