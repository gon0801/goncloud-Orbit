"""Tests de la integracion de ubicaciones en el ciclo (AUTO-04, Tasks 5)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_schema import _postgres_obligatorio_ausente

from app import config_write
from app.apply_ubicacion import aplica_automaticos
from app.cycle_grupos import _Contadores
from app.cycle_sombra import tx3_ubicaciones
from app.optimizer.caso import EconomiaPlataforma
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    CasoCampana,
    EvidenciaUbicacion,
    MantenerUbicacion,
    MetaCampana,
    MoverUbicacion,
    caso_a_json,
)
from tests.helpers.pg import db_desde_template

_D = Decimal
_ANCLA = dt.datetime(2026, 11, 26, 6, 0, tzinfo=dt.UTC)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(), reason="sin Postgres utilizable en ORBIT_TEST_DSN"
)


def _db():
    return db_desde_template("orbit_cyuio", autocommit=True)


def _caso(campana: int, config_id: int) -> CasoCampana:
    return CasoCampana(
        campana_id=campana,
        plataforma="amazon_mx",
        config_id=config_id,
        estrategia="LEGACY_FOR_SALES",
        ventana_desde=_ANCLA.date() - dt.timedelta(days=32),
        ventana_hasta=_ANCLA.date() - dt.timedelta(days=3),
        ubicaciones=(
            EvidenciaUbicacion("arriba_de_busqueda", 0, 40, _D("5"), 4, _D("100"), 0),
            EvidenciaUbicacion("resto_de_busqueda", 0, 20, _D("2"), 0, _D("10"), 0),
            EvidenciaUbicacion("paginas_de_producto", 0, 20, _D("2"), 0, _D("10"), 0),
        ),
        meta=MetaCampana(_D("18"), _D("24"), ("goal",)),
        economia=EconomiaPlataforma(
            moneda="MXN",
            equilibrio_acos_pct=None,
            gasto_para_concluir=_D("350"),
            confianza_recorte=_D("0.80"),
            confianza_subida=_D("0.70"),
        ),
        gasto_campana=_D("9"),
        gasto_placements_total=_D("9"),
        placements_frescos=True,
        dias_desde_ultimo_cambio=None,
        ajuste_en_vuelo=False,
        elegible=True,
        en_impulso=False,
        solo_bajadas=False,
        dias_topados_7d=0,
        topada=CampanaTopada(_D("0.90"), 5),
        fase_temporada="normal",
    )


def _siembra(conn) -> tuple[int, int, int, int]:
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', 'c-cyuio') RETURNING id"
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id) VALUES"
        " ('amazon_mx', 'ad_group', 'g-cyuio', %s) RETURNING id",
        (camp,),
    ).fetchone()[0]
    hoja = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES ('amazon_mx', 'keyword', 'k-cyuio', %s, 'EXACT', 't')"
        " RETURNING id",
        (ag,),
    ).fetchone()[0]
    for entidad in (camp, ag, hoja):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (entidad,),
        )
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct)"
        " VALUES (%s, %s, 100, 'MXN', 'LEGACY_FOR_SALES', 0)",
        (camp, _ANCLA),
    )
    config_id = conn.execute(
        "SELECT id FROM ads_campana_config_observation WHERE ad_entity_id = %s", (camp,)
    ).fetchone()[0]
    cv = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('cyuio', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', 'amazon_mx') RETURNING id"
    ).fetchone()[0]
    return camp, hoja, config_id, cv, ciclo


@_skip_db
def test_tx3_guarda_evaluacion_shadow_y_no_toca_campana_ajuste():
    with _db() as (conn, _dsn):
        camp, _hoja, config_id, cv, ciclo = _siembra(conn)
        contadores = _Contadores()
        contadores.ubicaciones.append(
            (_caso(camp, config_id), "shadow", MoverUbicacion("arriba_de_busqueda", 0, 15, "x"))
        )
        cuerpo: dict = {}
        tx3_ubicaciones(conn, ciclo, cv, "amazon_mx", _ANCLA, contadores, {}, cuerpo)
        fila = conn.execute(
            "SELECT modo, veredicto, a_pct FROM ubicacion_evaluacion WHERE cycle_id = %s",
            (ciclo,),
        ).fetchone()
        assert fila == ("shadow", "mover", 15)
        assert conn.execute("SELECT count(*) FROM campana_ajuste").fetchone()[0] == 0
        assert cuerpo["ubicaciones"] == {
            "evaluadas": 1,
            "mover": 1,
            "en_practica": 1,
            "aplicadas": 0,
        }


@_skip_db
def test_tx3_marca_producto_sin_evidencia_si_la_hoja_perdio_meta():
    with _db() as (conn, _dsn):
        camp, hoja, config_id, cv, ciclo = _siembra(conn)
        contadores = _Contadores()
        contadores.productos = SimpleNamespace(hojas_aptas={hoja: 999})
        contadores.ubicaciones.append(
            (_caso(camp, config_id), "shadow", MoverUbicacion("arriba_de_busqueda", 0, 15, "x"))
        )
        tx3_ubicaciones(conn, ciclo, cv, "amazon_mx", _ANCLA, contadores, {}, {})
        fila = conn.execute(
            "SELECT veredicto, motivo FROM ubicacion_evaluacion WHERE cycle_id = %s",
            (ciclo,),
        ).fetchone()
        assert fila == ("mantener", "producto_sin_evidencia")


@_skip_db
def test_modo_shadow_no_aplica_y_registra_modo_no_live():
    with _db() as (conn, dsn):
        camp, _hoja, config_id, cv, ciclo = _siembra(conn)
        eval_id = conn.execute(
            "INSERT INTO ubicacion_evaluacion (cycle_id, campana_id, platform, modo,"
            " politica, config_version_id, antes_config_id, decided_at, ventana_desde,"
            " ventana_hasta, moneda, entrada, veredicto, motivo, ubicacion, de_pct, a_pct)"
            " VALUES (%s, %s, 'amazon_mx', 'live', 'ubicacion_v1', %s, %s, %s, %s, %s,"
            " 'MXN', %s, 'mover', 'bajo_meta_con_ventaja', 'arriba_de_busqueda', 0, 15)"
            " RETURNING id",
            (
                ciclo,
                camp,
                cv,
                config_id,
                _ANCLA,
                _ANCLA.date() - dt.timedelta(days=20),
                _ANCLA.date() - dt.timedelta(days=3),
                json.dumps(caso_a_json(_caso(camp, config_id))),
            ),
        ).fetchone()[0]
        resumen = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=SimpleNamespace(),
            platform="amazon_mx",
            cycle_id=ciclo,
            settings={"ads_ubicacion_amazon_mx": "shadow"},
            abrir_conexion=lambda: (_ for _ in ()).throw(AssertionError("no se abre conexion")),
        )
        assert resumen.aplicadas == 0
        assert resumen.sin_aplicar == ((eval_id, "modo_no_live"),)


def test_config_write_valida_settings_y_cap_ubicacion():
    nuevo, cambios = config_write.proxima_config(
        {},
        "amazon_mx",
        ubicacion_modo="shadow",
        ubicacion_solo_bajadas=True,
        ubicacion_max_en_espera=3,
        caps={"ubicacion": 2},
    )
    assert nuevo["ads_ubicacion_amazon_mx"] == "shadow"
    assert nuevo["ads_ubicacion_solo_bajadas_amazon_mx"] == "true"
    assert nuevo["ads_ubicacion_max_en_espera_amazon_mx"] == "3"
    assert nuevo["ads_apply_cap_amazon_mx_ubicacion"] == "2"
    assert cambios

    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config({}, "amazon_mx", ubicacion_modo="medio")
    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config({}, "amazon_mx", ubicacion_max_en_espera=-1)

    apagado, _ = config_write.proxima_config(
        {"ads_ubicacion_amazon_mx": "shadow"}, "amazon_mx", ubicacion_modo="off"
    )
    assert "ads_ubicacion_amazon_mx" not in apagado


def test_mantener_no_se_persiste_como_mover():
    from app.ubicacion_io import marca_producto_sin_evidencia

    salida = marca_producto_sin_evidencia(
        [(_caso(1, 1), "shadow", MantenerUbicacion("sin_evidencia_ubicacion"))], []
    )
    assert salida[0][2] == MantenerUbicacion("sin_evidencia_ubicacion")
