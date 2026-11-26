"""Tests del ejecutor live de ubicaciones y de la guardia de cambio externo."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from app.ads.campana_config import ConfigCampana, cuerpo_put_campana, guarda_config
from app.apply_ajustes import aplica_ajuste_motor
from app.apply_ubicacion import aplica_automaticos, reconcilia_automaticos
from app.campana_ajustes import CambiarAjusteUbicacion
from app.optimizer.caso import EconomiaPlataforma
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    CasoCampana,
    EvidenciaUbicacion,
    MetaCampana,
    caso_a_json,
)
from tests.helpers.pg import db_desde_template

_D = Decimal
_ANCLA = dt.datetime(2026, 11, 26, 6, 0, tzinfo=dt.UTC)
_EXTERNA = "93600000000001"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(), reason="sin Postgres utilizable en ORBIT_TEST_DSN"
)


def _db():
    return db_desde_template("orbit_appuio", autocommit=True)


def _config(top=0, *, externa=_EXTERNA, estrategia="LEGACY_FOR_SALES") -> ConfigCampana:
    return ConfigCampana(
        campana_externa=externa,
        presupuesto_diario=_D("100"),
        moneda="MXN",
        estrategia_puja=estrategia,
        ajuste_top_pct=top,
        ajuste_resto_pct=0,
        ajuste_producto_pct=0,
        fuera_de_amazon=None,
    )


def _siembra(conn, *, top=0, externa=_EXTERNA) -> tuple[int, int]:
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', %s) RETURNING id",
        (externa,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
        " VALUES (%s, 'ENABLED', now())",
        (camp,),
    )
    guarda_config(conn, "amazon_mx", [_config(top, externa=externa)], _ANCLA)
    config_id = conn.execute(
        "SELECT id FROM ads_campana_config_observation WHERE ad_entity_id = %s"
        " ORDER BY id DESC LIMIT 1",
        (camp,),
    ).fetchone()[0]
    return camp, config_id


def _ciclo(conn, mode="live") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES (%s, 'amazon_mx') RETURNING id",
        (mode,),
    ).fetchone()[0]


def _caso(campana: int, config_id: int, de_pct: int, a_pct: int) -> CasoCampana:
    return CasoCampana(
        campana_id=campana,
        plataforma="amazon_mx",
        config_id=config_id,
        estrategia="LEGACY_FOR_SALES",
        ventana_desde=_ANCLA.date() - dt.timedelta(days=32),
        ventana_hasta=_ANCLA.date() - dt.timedelta(days=3),
        ubicaciones=(
            EvidenciaUbicacion("arriba_de_busqueda", de_pct, 40, _D("5"), 4, _D("100"), 0),
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


def _eval(
    conn,
    *,
    ciclo: int,
    campana: int,
    antes: int,
    cv: int,
    de_pct: int,
    a_pct: int,
    modo="live",
) -> int:
    return conn.execute(
        "INSERT INTO ubicacion_evaluacion (cycle_id, campana_id, platform, modo, politica,"
        " config_version_id, antes_config_id, decided_at, ventana_desde, ventana_hasta,"
        " moneda, entrada, veredicto, motivo, ubicacion, de_pct, a_pct) VALUES (%s, %s,"
        " 'amazon_mx', %s, 'ubicacion_v1', %s, %s, %s, %s, %s, 'MXN', %s, 'mover',"
        " 'bajo_meta_con_ventaja', 'arriba_de_busqueda', %s, %s) RETURNING id",
        (
            ciclo,
            campana,
            modo,
            cv,
            antes,
            _ANCLA,
            _ANCLA.date() - dt.timedelta(days=20),
            _ANCLA.date() - dt.timedelta(days=3),
            json.dumps(caso_a_json(_caso(campana, antes, de_pct, a_pct))),
            de_pct,
            a_pct,
        ),
    ).fetchone()[0]


def _config_version(conn, settings=None) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('appuio', %s::jsonb) RETURNING id",
        (json.dumps(settings or {}),),
    ).fetchone()[0]


def _settings(cap="2", espera=6) -> dict:
    datos = {
        "ads_ubicacion_amazon_mx": "live",
        "ads_ubicacion_max_en_espera_amazon_mx": str(espera),
    }
    if cap is not None:
        datos["ads_apply_cap_amazon_mx_ubicacion"] = cap
    return datos


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class ClienteFalso:
    """Amazon simulado: list devuelve la config viva y el PUT la reemplaza."""

    def __init__(self, actual: ConfigCampana, destino: ConfigCampana, *, dsn=None, llave=None):
        self.actual = actual
        self.destino = destino
        self.puts = 0
        self.lock_libre: bool | None = None
        self._dsn = dsn
        self._llave = llave

    def list_sellado(self, _path, _payload):
        config = self.destino if self.puts else self.actual
        return _Resp({"campaigns": [cuerpo_put_campana(config)]})

    def ajustar_campana(self, _externa, _cuerpo):
        self.puts += 1
        if self._dsn is not None and self._llave is not None:
            with psycopg.connect(self._dsn, autocommit=True) as otra:
                self.lock_libre = otra.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))", (self._llave,)
                ).fetchone()[0]


def _abre_decide(dsn):
    conexion = psycopg.connect(dsn, autocommit=False)
    conexion.execute("SET ROLE app_decide")
    return conexion


@_skip_db
def test_ejecutor_usa_conexion_sin_autocommit_y_sostiene_advisory_lock():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = ClienteFalso(
            _config(0),
            _config(15),
            dsn=dsn,
            llave=f"campana:{camp}:ajuste_top_pct",
        )
        resumen = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=cliente,
            platform="amazon_mx",
            cycle_id=ciclo,
            settings=_settings(),
            abrir_conexion=lambda: _abre_decide(dsn),
        )
        assert resumen.aplicadas == 1
        assert resumen.sin_aplicar == ()
        assert cliente.puts == 1
        # El lock de transaccion seguia tomado durante el PUT de la conexion
        # dedicada (autocommit=False).
        assert cliente.lock_libre is False
        confirmado = conn.execute(
            "SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE evaluacion_id = %s",
            (eval_id,),
        ).fetchone()
        assert confirmado == (True,)


@_skip_db
def test_put_aborta_si_porcentaje_vivo_difiere_del_juzgado():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=10)
        ciclo = _ciclo(conn)
        eval_id = _eval(
            conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=10, a_pct=25
        )
        # El dueno movio el placement a 25% en la consola: Orbit juzgo 10%.
        cliente = ClienteFalso(_config(25), _config(25))
        resumen = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=cliente,
            platform="amazon_mx",
            cycle_id=ciclo,
            settings=_settings(),
            abrir_conexion=lambda: _abre_decide(dsn),
        )
        assert resumen.aplicadas == 0
        assert resumen.sin_aplicar == ((eval_id, "cambio_externo"),)
        assert cliente.puts == 0
        motivo = conn.execute(
            "SELECT motivo FROM ubicacion_sin_aplicar WHERE evaluacion_id = %s", (eval_id,)
        ).fetchone()[0]
        assert motivo == "cambio_externo"


@_skip_db
def test_tope_campanas_en_espera_y_cuota_fail_closed():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings(espera=1))
        otra, otra_cfg = _siembra(conn, top=0, externa="93600000000002")
        conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, confirmado_el) VALUES (%s, 'amazon_mx',"
            " 'ajuste_ubicacion', %s, %s, 'h-espera', 'dueno', 'APLICAR AJUSTE', now())",
            (otra, otra_cfg, json.dumps(_config(15).como_json())),
        )
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = ClienteFalso(_config(0), _config(15))
        resumen = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=cliente,
            platform="amazon_mx",
            cycle_id=ciclo,
            settings=_settings(espera=1),
            abrir_conexion=lambda: _abre_decide(dsn),
        )
        assert resumen.sin_aplicar == ((eval_id, "tope_campanas_en_espera"),)
        assert cliente.puts == 0
        assert conn.execute("SELECT count(*) FROM apply_quota_state").fetchone()[0] == 0

    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings(cap=None))
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = ClienteFalso(_config(0), _config(15))
        resumen = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=cliente,
            platform="amazon_mx",
            cycle_id=ciclo,
            settings=_settings(cap=None),
            abrir_conexion=lambda: _abre_decide(dsn),
        )
        assert resumen.sin_aplicar == ((eval_id, "cuota_agotada"),)
        assert cliente.puts == 0


@_skip_db
def test_huella_automatica_permite_reaplicar_tras_regreso_del_dueno():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        cliente = ClienteFalso(_config(0), _config(15))
        ciclo_1 = _ciclo(conn)
        _eval(conn, ciclo=ciclo_1, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        primero = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=cliente,
            platform="amazon_mx",
            cycle_id=ciclo_1,
            settings=_settings(),
            abrir_conexion=lambda: _abre_decide(dsn),
        )
        assert primero.aplicadas == 1

        # El dueno regresa a mano: configuracion nueva (0%) como app_admin.
        guarda_config(conn, "amazon_mx", [_config(0)], _ANCLA + dt.timedelta(days=1))
        config_nueva = conn.execute(
            "SELECT id FROM ads_campana_config_observation WHERE ad_entity_id = %s"
            " ORDER BY id DESC LIMIT 1",
            (camp,),
        ).fetchone()[0]
        ciclo_2 = _ciclo(conn)
        _eval(conn, ciclo=ciclo_2, campana=camp, antes=config_nueva, cv=cv, de_pct=0, a_pct=15)
        segundo = aplica_automaticos(
            conn,
            dsn=dsn,
            cliente=_ClienteReaplicable(dsn, camp),
            platform="amazon_mx",
            cycle_id=ciclo_2,
            settings=_settings(),
            abrir_conexion=lambda: _abre_decide(dsn),
        )
        assert segundo.aplicadas == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM campana_ajuste WHERE campana_id = %s"
                " AND confirmado_el IS NOT NULL",
                (camp,),
            ).fetchone()[0]
            == 2
        )


class _ClienteReaplicable(ClienteFalso):
    def __init__(self, dsn, camp):
        super().__init__(_config(0), _config(15))
        self._dsn = dsn
        self._camp = camp


@_skip_db
def test_reconcilia_automaticos_sella_pendiente_confirmada_en_amazon():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        conexion = _abre_decide(dsn)
        try:
            conexion.execute(
                "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
                " despues, huella, actor, go_literal, evaluacion_id) VALUES (%s,"
                " 'amazon_mx', 'ajuste_ubicacion', %s, %s, 'auto:pendiente',"
                " 'orbit_ubicacion', 'AUTO_UBICACION', %s)",
                (camp, config_id, json.dumps(_config(15).como_json()), eval_id),
            )
            conexion.commit()
        finally:
            conexion.close()
        cliente = ClienteFalso(_config(15), _config(15))
        resumen = reconcilia_automaticos(conn, cliente, "amazon_mx")
        assert resumen.revisadas == 1
        assert resumen.selladas == 1
        assert (
            conn.execute(
                "SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE evaluacion_id = %s",
                (eval_id,),
            ).fetchone()[0]
            is True
        )


@_skip_db
def test_aplica_ajuste_motor_confirma_sin_literal_humano():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = ClienteFalso(_config(0), _config(15))
        conexion = _abre_decide(dsn)
        try:
            hecho = aplica_ajuste_motor(
                conexion,
                cliente,
                campana_id=camp,
                plataforma="amazon_mx",
                ajuste=CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=15),
                evaluacion_id=eval_id,
            )
        finally:
            conexion.close()
        assert hecho.confirmada is True
        assert cliente.puts == 1
