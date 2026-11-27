"""Tests del ejecutor live de ubicaciones y de la guardia de cambio externo."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from app import apply
from app.ads.campana_config import (
    ConfigCampana,
    config_vigente,
    cuerpo_put_campana,
    guarda_config,
)
from app.ads.client import AdsApiError
from app.ads.write import AdsApiErrorMutacion
from app.apply_ajustes import aplica_ajuste_campana
from app.apply_ubicacion import aplica_ajuste_motor, aplica_automaticos, reconcilia_automaticos
from app.campana_ajustes import CambiarAjusteUbicacion, planea_ajuste
from app.db import OrbitDbError
from app.optimizer.caso import EconomiaPlataforma
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    CasoCampana,
    EvidenciaUbicacion,
    MetaCampana,
    caso_a_json,
)
from app.ubicacion_io import campanas_con_ajuste_en_vuelo
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
    if conn.execute("SELECT count(*) FROM ads_optimizer_goal").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, bid_currency, bid_floor,"
            " bid_ceiling, enabled, mode) VALUES ('platform', 'amazon_mx', 'MXN', 0.10,"
            " 2.50, true, 'live')"
        )
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
        "ads_optimizer_mode": "live",
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


def _intencion(dsn, campana: int, config_id: int, eval_id: int, *, top: int, huella: str) -> None:
    """Intencion automatica pre-HTTP sin confirmar, escrita como `app_decide`."""
    conexion = _abre_decide(dsn)
    try:
        conexion.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, evaluacion_id) VALUES (%s,"
            " 'amazon_mx', 'ajuste_ubicacion', %s, %s, %s, 'orbit_ubicacion',"
            " 'AUTO_UBICACION', %s)",
            (campana, config_id, json.dumps(_config(top).como_json()), huella, eval_id),
        )
        conexion.commit()
    finally:
        conexion.close()


def _cierra(conn, eval_id: int, motivo: str = "error_put") -> None:
    conn.execute(
        "INSERT INTO ubicacion_sin_aplicar (evaluacion_id, motivo, registrado_el)"
        " VALUES (%s, %s, %s)",
        (eval_id, motivo, _ANCLA),
    )


def _cuenta(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]


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
        assert _cuenta(conn, "campana_ajuste") == 0, "la revalidacion viva va antes de la intencion"
        assert _cuenta(conn, "apply_quota_state") == 0, "el cambio en consola no gasta cuota"


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


@_skip_db
def test_reconciliacion_ignora_intenciones_que_nunca_salieron():
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
                " 'amazon_mx', 'ajuste_ubicacion', %s, %s, 'auto:nunca',"
                " 'orbit_ubicacion', 'AUTO_UBICACION', %s)",
                (camp, config_id, json.dumps(_config(15).como_json()), eval_id),
            )
            conexion.commit()
        finally:
            conexion.close()
        conn.execute(
            "INSERT INTO ubicacion_sin_aplicar (evaluacion_id, motivo, registrado_el)"
            " VALUES (%s, 'cambio_externo', %s)",
            (eval_id, _ANCLA),
        )
        cliente = ClienteFalso(_config(15), _config(15))
        resumen = reconcilia_automaticos(conn, cliente, "amazon_mx")
        assert resumen.revisadas == 0
        assert resumen.selladas == 0
        assert (
            conn.execute(
                "SELECT confirmado_el IS NULL FROM campana_ajuste WHERE evaluacion_id = %s",
                (eval_id,),
            ).fetchone()[0]
            is True
        )


@_skip_db
def test_no_aplica_si_el_dueno_apago_la_campana_tras_evaluar():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, bid_currency, bid_floor,"
            " bid_ceiling, enabled, mode) VALUES ('campaign', %s, 'MXN', 0.10, 2.50,"
            " false, 'live')",
            (camp,),
        )
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = ClienteFalso(_config(0), _config(15))
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
        assert resumen.sin_aplicar == ((eval_id, "modo_no_live"),)
        assert cliente.puts == 0


@_skip_db
@pytest.mark.parametrize(
    ("cap", "esperado"),
    [("1", "apply_quota_state"), (None, "ubicacion_sin_aplicar")],
)
def test_cuota_y_registro_sobreviven_al_rollback_del_ciclo(cap, esperado):
    """Neon #786 / ai-review F8: con la conexion del ciclo SIN autocommit (como
    en `app/cli.py`), la cuota y el motivo se confirman ANTES del HTTP y un
    rollback posterior del ciclo no los revierte."""
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings(cap=cap))
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        ciclo_conn = psycopg.connect(dsn, autocommit=False)
        try:
            aplica_automaticos(
                ciclo_conn,
                dsn=dsn,
                cliente=ClienteFalso(_config(0), _config(15)),
                platform="amazon_mx",
                cycle_id=ciclo,
                settings=_settings(cap=cap),
                abrir_conexion=lambda: _abre_decide(dsn),
            )
            ciclo_conn.rollback()
        finally:
            ciclo_conn.close()
        assert _cuenta(conn, esperado) == 1, f"{esperado} sobrevive al rollback del ciclo"


class ClienteFalla(ClienteFalso):
    def __init__(self, actual, *, donde, exc):
        super().__init__(actual, actual)
        self.donde = donde
        self.exc = exc

    def list_sellado(self, path, payload):
        if self.donde == "lectura_previa" or (self.donde == "readback" and self.puts):
            raise self.exc
        return super().list_sellado(path, payload)

    def ajustar_campana(self, externa, cuerpo):
        if self.donde == "put":
            raise self.exc
        super().ajustar_campana(externa, cuerpo)


def _corre(conn, dsn, ciclo, cliente):
    return aplica_automaticos(
        conn,
        dsn=dsn,
        cliente=cliente,
        platform="amazon_mx",
        cycle_id=ciclo,
        settings=_settings(),
        abrir_conexion=lambda: _abre_decide(dsn),
    )


@_skip_db
def test_readback_500_deja_intencion_pendiente_sin_marcarla_no_aplicada():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        error = AdsApiError("status=500 readback")
        error.status = 500
        resumen = _corre(conn, dsn, ciclo, ClienteFalla(_config(0), donde="readback", exc=error))
        assert resumen.aplicadas == 0
        assert resumen.sin_aplicar == ()
        assert conn.execute("SELECT count(*) FROM ubicacion_sin_aplicar").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT confirmado_el IS NULL FROM campana_ajuste WHERE evaluacion_id IS NOT NULL"
            ).fetchone()[0]
            is True
        )


def _error_api(status: int) -> AdsApiError:
    error = AdsApiError(f"status={status}")
    error.status = status
    return error


def _rechazo(status: int) -> AdsApiErrorMutacion:
    return AdsApiErrorMutacion(
        f"status={status}", cuerpo="{}", status=status, method="PUT", path="/sp/campaigns"
    )


@_skip_db
@pytest.mark.parametrize("status", [500, 429, 400, 207])
def test_put_fallido_deja_la_intencion_pendiente_sin_importar_el_error(status):
    """El PUT empezo: su destino lo decide la reconciliacion leyendo Amazon,
    no el tipo de error (un 207 ilegible o un 400 tambien quedan pendientes)."""
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        resumen = _corre(
            conn, dsn, ciclo, ClienteFalla(_config(0), donde="put", exc=_rechazo(status))
        )
        assert (resumen.aplicadas, resumen.sin_aplicar) == (0, ())
        assert conn.execute(
            "SELECT confirmado_el IS NULL FROM campana_ajuste WHERE evaluacion_id = %s",
            (eval_id,),
        ).fetchone() == (True,)
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({camp})


@_skip_db
def test_fallo_de_base_aborta_la_fase_y_no_marca_no_aplicado():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        with pytest.raises(OrbitDbError):
            aplica_automaticos(
                conn,
                dsn=dsn,
                cliente=ClienteFalso(_config(0), _config(15)),
                platform="amazon_mx",
                cycle_id=ciclo,
                settings=_settings(),
                abrir_conexion=lambda: (_ for _ in ()).throw(OrbitDbError("sin base")),
            )
        assert conn.execute("SELECT count(*) FROM ubicacion_sin_aplicar").fetchone()[0] == 0
        assert _cuenta(conn, "apply_quota_state") == 0, (
            "Devin #786: sin conexion dedicada no se cobra cuota que la recuperacion recobraria"
        )


@_skip_db
def test_lectura_previa_fallida_descarta_sin_cobrar_cuota_ni_escribir_intencion():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = ClienteFalla(_config(0), donde="lectura_previa", exc=_error_api(500))
        resumen = _corre(conn, dsn, ciclo, cliente)
        assert resumen.sin_aplicar == ((eval_id, "error_put"),)
        assert _cuenta(conn, "campana_ajuste") == 0
        assert _cuenta(conn, "apply_quota_state") == 0


@_skip_db
def test_rechazo_de_identidad_aborta_la_fase():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        with pytest.raises(AdsApiError):
            _corre(
                conn,
                dsn,
                ciclo,
                ClienteFalla(_config(0), donde="lectura_previa", exc=_error_api(401)),
            )
        assert conn.execute(
            "SELECT motivo FROM ubicacion_sin_aplicar WHERE evaluacion_id = %s", (eval_id,)
        ).fetchone() == ("error_put",)
        assert _cuenta(conn, "apply_quota_state") == 0

    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        with pytest.raises(AdsApiErrorMutacion):
            _corre(conn, dsn, ciclo, ClienteFalla(_config(0), donde="put", exc=_rechazo(403)))
        assert _cuenta(conn, "ubicacion_sin_aplicar") == 0, (
            "el PUT empezo: decide la reconciliacion"
        )
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({camp})


class _ClienteIlegible(ClienteFalso):
    def list_sellado(self, _path, _payload):
        return _Resp({"campaigns": []})


@_skip_db
def test_reconcilia_cierra_la_intencion_que_amazon_no_refleja():
    """ai-review F1: una intencion que Amazon no refleja ya no bloquea la
    campana para siempre; la lectura ilegible espera al siguiente ciclo."""
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        _intencion(dsn, camp, config_id, eval_id, top=15, huella="auto:sin_reflejo")

        ilegible = reconcilia_automaticos(
            conn, _ClienteIlegible(_config(0), _config(0)), "amazon_mx"
        )
        assert (ilegible.revisadas, ilegible.selladas, ilegible.cerradas) == (1, 0, 0)
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({camp})

        otro = reconcilia_automaticos(conn, ClienteFalso(_config(0), _config(0)), "amazon_mx")
        assert (otro.revisadas, otro.selladas, otro.cerradas) == (1, 0, 1)
        assert conn.execute(
            "SELECT motivo FROM ubicacion_sin_aplicar WHERE evaluacion_id = %s", (eval_id,)
        ).fetchone() == ("error_put",)
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset()

        despues = reconcilia_automaticos(conn, ClienteFalso(_config(15), _config(15)), "amazon_mx")
        assert (despues.revisadas, despues.selladas) == (0, 0), "una intencion cerrada no se sella"


@_skip_db
def test_en_vuelo_cuenta_pendientes_y_no_intenciones_cerradas():
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        abierta, cfg_a = _siembra(conn, top=0)
        cerrada, cfg_c = _siembra(conn, top=0, externa="93600000000002")
        ciclo = _ciclo(conn)
        ev_a = _eval(conn, ciclo=ciclo, campana=abierta, antes=cfg_a, cv=cv, de_pct=0, a_pct=15)
        ev_c = _eval(conn, ciclo=ciclo, campana=cerrada, antes=cfg_c, cv=cv, de_pct=0, a_pct=15)
        _intencion(dsn, abierta, cfg_a, ev_a, top=15, huella="auto:abierta")
        _intencion(dsn, cerrada, cfg_c, ev_c, top=15, huella="auto:cerrada")
        _cierra(conn, ev_c)
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset({abierta})


@_skip_db
def test_intencion_automatica_cerrada_no_bloquea_reintento_manual(monkeypatch):
    """Devin #786: una intencion automatica posterior, cerrada en
    `ubicacion_sin_aplicar`, nunca salio a Amazon; el reintento de la orden
    manual anterior que quedo pendiente sigue su curso."""
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        plan = planea_ajuste(
            config_vigente(conn, camp),
            CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=20),
            campana_id=camp,
            plataforma="amazon_mx",
        )

        def manual(cliente):
            monkeypatch.setattr(apply, "_cliente_ajuste", lambda _plat, transport=None: cliente)
            return aplica_ajuste_campana(
                conn, plan, huella=plan.huella(), confirmacion="APLICAR AJUSTE", actor="dueno"
            )

        with pytest.raises(AdsApiErrorMutacion):
            manual(ClienteFalla(_config(0), donde="put", exc=_rechazo(400)))
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        _intencion(dsn, camp, config_id, eval_id, top=15, huella="auto:cerrada")
        _cierra(conn, eval_id)

        cliente = ClienteFalso(_config(0), _config(20))
        hecho = manual(cliente)
        assert (hecho.confirmada, cliente.puts) == (True, 1)
        assert conn.execute(
            "SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE id = %s", (hecho.ajuste_id,)
        ).fetchone() == (True,)


@_skip_db
def test_pujas_pendientes_aparta_las_ubicaciones_del_ciclo():
    """Spec 7.4: si las pujas del origen no cerraron, las ubicaciones no se
    envian y quedan apartadas con `pujas_pendientes`."""
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
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
            settings=_settings(),
            pujas_completas=False,
            abrir_conexion=lambda: (_ for _ in ()).throw(AssertionError("no se abre conexion")),
        )
        assert (resumen.aplicadas, resumen.sin_aplicar) == (0, ((eval_id, "pujas_pendientes"),))
        assert (cliente.puts, _cuenta(conn, "apply_quota_state")) == (0, 0)


def _plan_manual(conn, camp: int, porcentaje: int):
    return planea_ajuste(
        config_vigente(conn, camp),
        CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=porcentaje),
        campana_id=camp,
        plataforma="amazon_mx",
    )


def _manual(monkeypatch, conn, plan, cliente):
    monkeypatch.setattr(apply, "_cliente_ajuste", lambda _plat, transport=None: cliente)
    return aplica_ajuste_campana(
        conn, plan, huella=plan.huella(), confirmacion="APLICAR AJUSTE", actor="dueno"
    )


@_skip_db
def test_manual_con_cambio_en_consola_no_deja_fila_que_bloquee_ubicaciones(monkeypatch):
    """Neon #786 r1: aplicar o regresar a mano con la consola cambiada responde
    409 sin escribir una orden que AUTO-04 veria en vuelo para siempre."""
    from app.apply_ajustes import CambioExternoDetectado, regresa_ajuste_campana

    with _db() as (conn, _dsn):
        camp, _config_id = _siembra(conn, top=0)
        with pytest.raises(CambioExternoDetectado):
            _manual(
                monkeypatch,
                conn,
                _plan_manual(conn, camp, 20),
                ClienteFalso(_config(25), _config(25)),
            )
        assert _cuenta(conn, "campana_ajuste") == 0
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset()

        hecho = _manual(
            monkeypatch, conn, _plan_manual(conn, camp, 20), ClienteFalso(_config(0), _config(20))
        )
        monkeypatch.setattr(
            apply,
            "_cliente_ajuste",
            lambda _plat, transport=None: ClienteFalso(_config(30), _config(30)),
        )
        with pytest.raises(CambioExternoDetectado):
            regresa_ajuste_campana(conn, ajuste_id=hecho.ajuste_id, actor="dueno")
        assert _cuenta(conn, "campana_ajuste") == 1, "el regreso rechazado no deja fila hija"
        assert campanas_con_ajuste_en_vuelo(conn, "amazon_mx") == frozenset()


@_skip_db
def test_reintento_manual_sella_sin_put_si_el_anterior_ya_llego(monkeypatch):
    """Neon #786 r1: el PUT llego pero el readback fallo; el reintento ve el
    destino en Amazon y sella la orden pendiente sin un segundo PUT."""
    with _db() as (conn, _dsn):
        camp, _config_id = _siembra(conn, top=0)
        plan = _plan_manual(conn, camp, 20)
        with pytest.raises(AdsApiError):
            _manual(
                monkeypatch,
                conn,
                plan,
                ClienteFalla(_config(0), donde="readback", exc=_error_api(500)),
            )
        cliente = ClienteFalso(_config(20), _config(20))
        hecho = _manual(monkeypatch, conn, plan, cliente)
        assert (hecho.confirmada, cliente.puts) == (True, 0)
        assert conn.execute(
            "SELECT count(*), bool_and(confirmado_el IS NOT NULL) FROM campana_ajuste"
        ).fetchone() == (1, True)


@_skip_db
def test_lectura_viva_incompleta_descarta_sin_cuota_ni_put():
    """Neon #786 r1: sin lectura viva completa el ejecutor no manda un PUT a
    ciegas ni gasta cuota."""
    with _db() as (conn, dsn):
        cv = _config_version(conn, _settings())
        camp, config_id = _siembra(conn, top=0)
        ciclo = _ciclo(conn)
        eval_id = _eval(conn, ciclo=ciclo, campana=camp, antes=config_id, cv=cv, de_pct=0, a_pct=15)
        cliente = _ClienteIlegible(_config(0), _config(15))
        resumen = _corre(conn, dsn, ciclo, cliente)
        assert resumen.sin_aplicar == ((eval_id, "error_put"),)
        assert (
            cliente.puts,
            _cuenta(conn, "apply_quota_state"),
            _cuenta(conn, "campana_ajuste"),
        ) == (0, 0, 0)
