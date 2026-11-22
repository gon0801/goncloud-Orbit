"""TEMPORADA 01, bloque F: al terminar la baja se reactiva lo que Orbit pauso.

- `test_temporada01_f_reactiva_solo_pausas_de_baja` (escribe en Amazon): de
  seis pausas, solo la de Orbit confirmada en una baja con el episodio cerrado
  entra en `reactivaciones_pendientes`; la del dueno, la que el dueno reactivo,
  la ya revertida, la de «Mantener pausada» y la de un episodio abierto no. La
  elegida produce una decision `resume` sin dinero y el aplicador manda el PUT
  de reactivacion (una sola vez).
- `test_temporada01_f_reactivar_no_bloquea_pausas` (escribe en Amazon): tras la
  reversa de temporada, el nuevo pause de la hoja no se lee como reactivacion
  manual del dueno (la pausa que la temporada deshizo no cuenta) y no inserta
  `reactivacion_manual`.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from test_apply_cola import (
    _aplicador,
    _decision_corte,
    _encola_fila,
    _fechas,
    _handler_cortes,
    _metrica,
    _reloj,
    _semilla,
    _skip_db,
)
from test_cycle_apply import _db_temporal
from test_temporada01_b import MESES_PRUEBA

from app import apply, apply_cola, temporada_io
from app import cycle as ciclo
from app.optimizer import temporada

CONFIG_TEMPORADA = {
    "ads_optimizer_mode": "live",
    "ads_bid_politica_amazon_us": "niveles_v3",
    "ads_apply_cap_amazon_us_bid": 10,
    "ads_apply_cap_amazon_us_pause": 5,
    "ads_apply_cap_amazon_us_negative": 5,
    "ads_apply_cap_amazon_us_harvest": 2,
    "ads_temporada": {
        "version": temporada.VERSION_TEMPORADA,
        "meses": list(MESES_PRUEBA),
        "reduccion_baja_pct": "30",
        "dias_preparacion": 21,
        "mult_cortes_baja": "2",
        "aumento_presupuesto_alta_pct": "25",
        "fin_anticipado": None,
        "mantener_pausadas": [],
    },
    "ads_temporada_amazon_us": "live",
}


def _config(conn, *, mantener=()) -> tuple[int, dict]:
    from psycopg.types.json import Json

    settings = {
        **CONFIG_TEMPORADA,
        "ads_temporada": {**CONFIG_TEMPORADA["ads_temporada"], "mantener_pausadas": list(mantener)},
    }
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('temporada-f', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]
    return config_id, settings


def _episodio(conn, config_id: int, ciclo_id: int, ahora: dt.datetime, *, cerrado: bool) -> int:
    fila = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id, cerrado_el, cerrado_en_ciclo)"
        " VALUES ('amazon_us', %s, %s, %s, %s, %s) RETURNING id",
        (
            ahora - dt.timedelta(days=40),
            ciclo_id,
            config_id,
            (ahora - dt.timedelta(days=1)) if cerrado else None,
            ciclo_id if cerrado else None,
        ),
    ).fetchone()[0]
    return fila


def _pausa_baja(
    conn,
    *,
    ciclo_id: int,
    config_id: int,
    hoja: int,
    ahora: dt.datetime,
    episodio: int | None = None,
    fase: str | None = "baja",
) -> tuple[int, int]:
    """Pausa verificada de la baja + su fila aplicada en la cola."""
    from psycopg.types.json import Json

    inputs: dict = {"motor": "bid", "motivo": "pause_economica"}
    if fase is not None:
        inputs["caso"] = {"temporada": {"frontera": {"fase_hoja": fase}}}
    if episodio is not None:
        inputs["temporada_episodio_id"] = episodio
    decidido = ahora - dt.timedelta(days=10)
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, %s, %s - interval '1 day', %s - 60, %s - 30, %s)"
        " RETURNING id",
        (
            ciclo_id,
            hoja,
            decidido,
            config_id,
            decidido,
            decidido.date(),
            decidido.date(),
            Json(inputs),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack, verify_ok)"
        " VALUES (%s, %s, '{}'::jsonb, true)",
        (dec, ahora - dt.timedelta(days=10)),
    )
    q = conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo, estado,"
        " vence_el, encolado_at, request_payload) VALUES ('amazon_us', %s, 'pause', %s, 'live',"
        " 'pending_veto', %s, %s, '{}'::jsonb) RETURNING id",
        (hoja, dec, decidido, decidido),
    ).fetchone()[0]
    for estado, columna in (
        ("released", "released_at"),
        ("applying", "applying_at"),
        ("applied", "applied_at"),
    ):
        conn.execute(
            f"UPDATE apply_queue SET estado = %s, {columna} = %s WHERE id = %s",
            (estado, decidido, q),
        )
    return dec, q


def _pausado(conn, hoja: int) -> None:
    conn.execute("UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (hoja,))


@_skip_db
def test_temporada01_f_reactiva_solo_pausas_de_baja():
    """La seleccion de pausas a reactivar y el PUT de reactivacion."""
    with _db_temporal("orbit_t01_f_sel") as (conn, _conectar):
        ids = _semilla(conn)
        ahora = _reloj()
        # «Mantener pausada» con el id real de la hoja que no debe reactivarse.
        kw_ok, kw_dueno, kw_manual, kw_revertida, kw_mantener, kw_abierto = (
            ids["kw"],
            ids["kw2"],
            _nueva_kw(conn, ids["ag"], "7203"),
            _nueva_kw(conn, ids["ag"], "7204"),
            _nueva_kw(conn, ids["ag"], "7205"),
            _nueva_kw(conn, ids["ag"], "7206"),
        )
        config_id, settings = _config(conn, mantener=[kw_mantener])
        cerrado = _episodio(conn, config_id, ids["ciclo_ejec"], ahora, cerrado=True)
        abierto = _episodio(conn, config_id, ids["ciclo_ejec"], ahora, cerrado=False)

        dec_ok, _q_ok = _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=kw_ok,
            ahora=ahora,
            episodio=cerrado,
        )
        _pausado(conn, kw_ok)
        _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=kw_dueno,
            ahora=ahora,
            episodio=cerrado,
            fase=None,
        )
        _pausado(conn, kw_dueno)
        _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=kw_manual,
            ahora=ahora,
            episodio=cerrado,
        )
        _pausado(conn, kw_manual)
        conn.execute(
            "INSERT INTO reactivacion_manual (ad_entity_id, detectada_en) VALUES (%s, %s)",
            (kw_manual, ahora - dt.timedelta(hours=1)),
        )
        dec_rev, _q_rev = _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=kw_revertida,
            ahora=ahora,
            episodio=cerrado,
        )
        _pausado(conn, kw_revertida)
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, resultado, finished_at)"
            " VALUES (%s, 1, 'reversa', '{}', false, 'ok', now())",
            (dec_rev,),
        )
        _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=kw_mantener,
            ahora=ahora,
            episodio=cerrado,
        )
        _pausado(conn, kw_mantener)
        _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=kw_abierto,
            ahora=ahora,
            episodio=abierto,
        )
        _pausado(conn, kw_abierto)

        mantener = frozenset(settings["ads_temporada"]["mantener_pausadas"])
        pendientes = temporada_io.reactivaciones_pendientes(conn, "amazon_us", mantener)
        assert [r.ad_entity_id for r in pendientes] == [kw_ok], pendientes
        assert pendientes[0].pausa_decision_id == dec_ok
        assert pendientes[0].episodio_id == cerrado

        # La decision resume nace sin dinero y con la pausa y la estacion.
        estacion = temporada.resuelve(temporada.desde_settings(settings), estacion_dia())
        pendiente = ciclo.pendiente_reactivacion(pendientes[0], estacion, decidido_el=ahora)
        assert pendiente.kind == "resume"
        assert pendiente.old_value is None and pendiente.new_value is None
        assert pendiente.value_currency is None
        assert pendiente.inputs["motor"] == "temporada"
        assert pendiente.inputs["version"] == "temporada_v1"
        assert pendiente.inputs["pausa_decision_id"] == dec_ok
        assert ciclo.reproduce(pendiente.inputs) == ("resume", None, None)
        ciclo_resume = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec_resume = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
            " data_observed_at, window_start, window_end, inputs)"
            " VALUES (%s, %s, 'resume', %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                ciclo_resume,
                pendiente.ad_entity_id,
                ahora,
                config_id,
                pendiente.data_observed_at,
                pendiente.window_start,
                pendiente.window_end,
                json.dumps(pendiente.inputs),
            ),
        ).fetchone()[0]

        handler, vistos = _handler_cortes(estados={"7201": "PAUSED"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = ap.aplica_reactivaciones(conn, apply.resumes_del_ciclo(conn, ciclo_resume), tick=None)

        assert res.confirmadas == 1, res
        assert res.omitidas == 0 and res.fallidas == 0 and res.sin_quota == 0
        puts = [r for r in vistos if r.method == "PUT"]
        assert [json.loads(r.content) for r in puts] == [
            {"keywords": [{"keywordId": "7201", "state": "ENABLED"}]}
        ], "una sola reactivacion, la de la pausa de la baja"
        assert conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw_ok,)
        ).fetchone()[0] == Decimal("1.00")
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt WHERE decision_id = %s",
            (dec_ok,),
        ).fetchall()
        assert filas == [("reversa", False, "ok")], "la reversa es EXENTA"
        assert (
            conn.execute(
                "SELECT used FROM apply_quota_state WHERE motor = 'ads_optimizer:amazon_us:pause'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'resume'",
                (ciclo_resume,),
            ).fetchone()[0]
            == 1
        ), dec_resume


@_skip_db
def test_temporada01_f_reactivar_no_bloquea_pausas():
    """Tras la reversa de temporada, la pausa que la temporada deshizo no cuenta
    como «pause propio»: la pausa nueva no inserta reactivacion_manual."""
    with _db_temporal("orbit_t01_f_nb") as (conn, _conectar):
        ids = _semilla(conn)
        ahora = _reloj()
        config_id, _settings = _config(conn)
        cerrado = _episodio(conn, config_id, ids["ciclo_ejec"], ahora, cerrado=True)
        dec_vieja, q_vieja = _pausa_baja(
            conn,
            ciclo_id=ids["ciclo_dec"],
            config_id=config_id,
            hoja=ids["kw"],
            ahora=ahora,
            episodio=cerrado,
        )
        # La temporada ya la reactivo: decision resume + reversa ok.
        ciclo_resume = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        resume = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
            " data_observed_at, window_start, window_end, inputs)"
            " VALUES (%s, %s, 'resume', %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                ciclo_resume,
                ids["kw"],
                ahora - dt.timedelta(hours=2),
                config_id,
                ahora - dt.timedelta(hours=2),
                (ahora - dt.timedelta(hours=2)).date(),
                (ahora - dt.timedelta(hours=2)).date(),
                json.dumps(
                    {
                        "motor": "temporada",
                        "version": "temporada_v1",
                        "pausa_decision_id": dec_vieja,
                        "queue_id": q_vieja,
                        "temporada_episodio_id": cerrado,
                    }
                ),
            ),
        ).fetchone()[0]
        assert resume is not None
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, resultado, finished_at)"
            " VALUES (%s, 1, 'reversa', '{}', false, 'ok', now())",
            (dec_vieja,),
        )
        # La hoja quedo ENABLED por la reversa de temporada.
        conn.execute(
            "UPDATE ad_entity_state SET status = 'ENABLED' WHERE ad_entity_id = %s", (ids["kw"],)
        )
        assert apply_cola._pause_propio_verificado(conn, ids["kw"]) is False, (
            "la pausa que la temporada deshizo no es terreno del dueno"
        )

        # Una pausa NUEVA (evidencia fresca de cero pedidos) no se bloquea.
        for fecha in _fechas(
            ahora.date() - dt.timedelta(days=28), ahora.date() - dt.timedelta(days=11)
        ):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=15, cost=3, orders=0)
        ciclo_nuevo = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec_nueva = _decision_corte(conn, ciclo_nuevo, config_id, ids["kw"], "pause")
        _encola_fila(
            conn, dec_nueva, ids["kw"], "pause", payload={"keywordId": "7201", "state": "PAUSED"}
        )
        handler, vistos = _handler_cortes()  # estado remoto: ENABLED

        res = apply_cola.libera_vencidos(
            conn, "amazon_us", ahora=ahora, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert apply_cola.MOTIVO_REACTIVACION_MANUAL not in res.descartadas, res.descartadas
        assert conn.execute("SELECT count(*) FROM reactivacion_manual").fetchone()[0] == 0
        puts = [r for r in vistos if r.method == "PUT"]
        assert [json.loads(r.content) for r in puts] == [
            {"keywords": [{"keywordId": "7201", "state": "PAUSED"}]}
        ], "la pausa nueva si se aplica"


def _nueva_kw(conn, ag: int, external: str) -> int:
    from test_apply_cola import _entidad, _estado

    kw = _entidad(conn, "keyword", external, parent=ag)
    _estado(conn, kw)
    return kw


def estacion_dia() -> dt.date:
    return dt.date(2027, 1, 5)


def test_temporada01_f_aviso_del_dia():
    """El aviso sale el dia del cambio y 3 dias antes de entrada y regreso; los
    demas dias no. Cuenta confirmaciones y no inventa costo, margen ni meta."""
    config = temporada.desde_settings(CONFIG_TEMPORADA)
    assert config is not None
    conteo = temporada.ConteoTemporada(
        entradas_confirmadas=212,
        regresos_confirmados=0,
        pendientes=9,
        retenidos_por_presupuesto=0,
        excluidas={"dueno_decidio": 3, "bid_heredado": 1},
        reactivaciones=0,
    )
    aviso = temporada.aviso_del_dia(
        config, temporada.resuelve(config, dt.date(2027, 1, 1)), conteo=conteo, propuestas=12
    )
    assert aviso is not None
    assert "212" in aviso and "9" in aviso and "12" in aviso
    assert "No tocó 4" in aviso
    assert "MXN" not in aviso and "USD" not in aviso, "sin costo ni moneda"

    normal = temporada.resuelve(config, dt.date(2027, 1, 20))
    assert temporada.aviso_del_dia(config, normal, conteo=conteo, propuestas=0) is None

    preparado = temporada.resuelve(config, dt.date(2027, 1, 29))  # 3 dias antes del 1 feb
    aviso_3 = temporada.aviso_del_dia(config, preparado, conteo=conteo, propuestas=0)
    assert aviso_3 is not None and aviso_3.startswith("En 3 días:")
