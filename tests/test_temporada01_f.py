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
from test_temporada01_b import (
    MESES_PRUEBA,
    _corre,
    _escenario,
    _handler_mx,
    _kw,
    _siembra_metricas,
)

from app import apply_cola, temporada_io
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
        res = apply_cola.aplica_reactivaciones(
            conn, ap, apply_cola.resumes_del_ciclo(conn, ciclo_resume), tick=None
        )

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


@_skip_db
def test_temporada01_f_conteo_confirmado_pendiente_y_divergencia():
    """El aviso cuenta confirmadas por Amazon, pendientes y divergencias por
    separado: una decision sin aplicar no se anuncia como confirmada y una
    aplicacion con verify_ok false no vuelve a contar como pendiente."""
    with _db_temporal("orbit_t01_f_conteo") as (conn, _conectar):
        config_id, _settings_f = _config(conn)
        ciclo_id = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        hojas = [
            conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id) VALUES"
                " ('amazon_us', 'campaign', %s) RETURNING id",
                (f"e-f-conteo-{i}",),
            ).fetchone()[0]
            for i in range(4)
        ]

        def _decision(motivo: str, hoja_id: int) -> int:
            return conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
                " data_observed_at, window_start, window_end, new_value, value_currency, inputs)"
                " VALUES (%s, %s, 'bid', %s, now() - interval '1 day', CURRENT_DATE - 60,"
                " CURRENT_DATE - 30, 1.00, 'USD', %s) RETURNING id",
                (ciclo_id, hoja_id, config_id, json.dumps({"motor": "bid", "motivo": motivo})),
            ).fetchone()[0]

        confirmada = _decision("entra_temporada_baja", hojas[0])
        conn.execute(
            "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack, verify_ok)"
            " VALUES (%s, now(), '{}'::jsonb, true)",
            (confirmada,),
        )
        sin_aplicar = _decision("entra_temporada_baja", hojas[1])
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
            " VALUES (%s, %s, 'sin_respuesta')",
            (sin_aplicar, ciclo_id),
        )
        divergente = _decision("sale_temporada_baja", hojas[2])
        conn.execute(
            "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack, verify_ok)"
            " VALUES (%s, now(), '{}'::jsonb, false)",
            (divergente,),
        )

        _sin_desenlace = _decision("entra_temporada_baja", hojas[3])
        assert _sin_desenlace is not None

        conteo = ciclo._conteo_temporada(
            conn,
            ciclo_id,
            reactivaciones=2,
            retenidos=1,
            excluidas={101: "dueno_decidio", 102: "bid_heredado"},
        )

        assert conteo.entradas_confirmadas == 1
        assert conteo.regresos_confirmados == 0, "la divergencia no es un regreso confirmado"
        assert conteo.pendientes == 1, "solo la decision sin desenlace es pendiente"
        assert conteo.reactivaciones == 2 and conteo.retenidos_por_presupuesto == 1
        assert conteo.excluidas == {"dueno_decidio": 1, "bid_heredado": 1}


@_skip_db
def test_temporada01_f_sello_de_reactivacion_sobrevive_al_aborto():
    """Cada reactivacion sella su reversa de forma DURABLE antes de la
    siguiente: si la fase aborta en la segunda, la habilitacion que Amazon ya
    aplico no se pierde ni se repite el PUT."""
    import pytest as _pytest

    from app.cycle import ApplyAbortado

    with _db_temporal("orbit_t01_f_dur") as (conn, conectar):
        ahora = _reloj()
        ids = _semilla(conn)
        config_id, _settings_f = _config(conn)
        cerrado = _episodio(conn, config_id, ids["ciclo_ejec"], ahora, cerrado=True)
        hojas = [ids["kw"], ids["kw2"]]
        pausas = []
        for hoja in hojas:
            dec_pausa, _q = _pausa_baja(
                conn,
                ciclo_id=ids["ciclo_dec"],
                config_id=config_id,
                hoja=hoja,
                ahora=ahora,
                episodio=cerrado,
            )
            pausas.append(dec_pausa)
            _pausado(conn, hoja)
        ciclo_resume = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        estacion = temporada.resuelve(temporada.desde_settings(_settings_f), estacion_dia())
        pendientes = temporada_io.reactivaciones_pendientes(conn, "amazon_us", frozenset())
        decision_ids = []
        for r in pendientes:
            p = ciclo.pendiente_reactivacion(r, estacion, decidido_el=ahora)
            decision_ids.append(
                conn.execute(
                    "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                    " config_version_id, data_observed_at, window_start, window_end, inputs)"
                    " VALUES (%s, %s, 'resume', %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        ciclo_resume,
                        r.ad_entity_id,
                        ahora,
                        config_id,
                        ahora,
                        ahora.date(),
                        ahora.date(),
                        json.dumps(p.inputs),
                    ),
                ).fetchone()[0]
            )
        assert len(decision_ids) == 2

        handler, _vistos = _handler_cortes(estados={"7201": "PAUSED", "7202": "PAUSED"})
        conn_no_auto = conectar(autocommit=False)
        llamadas = {"n": 0}

        def tick_que_aborta() -> None:
            llamadas["n"] += 1
            if llamadas["n"] == 3:
                raise ApplyAbortado("lease perdido en la segunda reactivacion")

        try:
            ap = _aplicador(conn_no_auto, handler, ids["ciclo_ejec"])
            with _pytest.raises(ApplyAbortado):
                apply_cola.aplica_reactivaciones(
                    conn_no_auto,
                    ap,
                    apply_cola.resumes_del_ciclo(conn_no_auto, ciclo_resume),
                    tick=tick_que_aborta,
                )
            conn_no_auto.rollback()
        finally:
            conn_no_auto.close()

        primera = pausas[0]
        sellos = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'",
            (primera,),
        ).fetchall()
        assert sellos == [("ok",)], "el sello de la primera sigue tras el rollback"
        pendientes_tras = temporada_io.reactivaciones_pendientes(conn, "amazon_us", frozenset())
        assert [r.ad_entity_id for r in pendientes_tras] == [hojas[1]], (
            "la primera ya no se repite; la segunda espera"
        )


def _mundo_aviso(conn) -> tuple[dict, int, int]:
    ids = _escenario(conn)
    kw = _kw(conn, ids["ag"], "e-kw", "kw", "8.00")
    kw_heredado = _kw(conn, ids["ag"], "e-kw-h", "kw h", "8.00")
    conn.execute(
        "UPDATE ad_entity_state SET current_bid = NULL, bid_currency = NULL"
        " WHERE ad_entity_id = %s",
        (kw_heredado,),
    )
    _siembra_metricas(conn, [kw, kw_heredado])
    return ids, kw, kw_heredado


@_skip_db
def test_temporada01_f_aviso_llega_al_canal(monkeypatch):
    """El aviso del dia sale por el canal fail-silent (no solo en notes) y
    cuenta la exclusion real de la hoja con bid heredado."""
    from app import notifica

    with _db_temporal("orbit_t01_f_canal") as (conn, _conectar):
        _ids, _kw_bid, _kw_h = _mundo_aviso(conn)
        enviados: list[str] = []
        monkeypatch.setattr(
            notifica,
            "notifica_temporada",
            lambda texto, **_k: (enviados.append(texto), True)[1],
        )
        handler = _handler_mx({"e-kw": "8.00", "e-kw-h": "8.00"})
        res = _corre(conn, dt.date(2027, 1, 1), handler)

        assert res.status == "done", res.notes
        assert len(enviados) == 1, "un aviso de temporada por ciclo"
        assert "No tocó 1" in enviados[0], enviados[0]
        assert json.loads(res.notes)["temporada"]["aviso"] == enviados[0]


@_skip_db
def test_temporada01_f_aviso_al_canal_fallido_deja_nota(monkeypatch):
    """Si el canal falla, el ciclo no se degrada: queda la nota fail-silent."""
    from app import notifica

    with _db_temporal("orbit_t01_f_canal2") as (conn, _conectar):
        _mundo_aviso(conn)
        monkeypatch.setattr(notifica, "notifica_temporada", lambda *_a, **_k: False)
        handler = _handler_mx({"e-kw": "8.00", "e-kw-h": "8.00"})
        res = _corre(conn, dt.date(2027, 1, 1), handler)

        assert res.status == "done", "un fallo de Telegram no degrada el ciclo"
        nota = json.loads(res.notes)["telegram"]["aviso_temporada"]
        assert nota.startswith("fallo:")


@_skip_db
def test_temporada01_f_aviso_incluye_la_mano_del_aplicador(monkeypatch):
    """La lectura fresca del aplicador descubre una mano del dueno que el
    recorrido no veia: esa hoja tambien aparece en «No tocó» del aviso."""
    from app import notifica

    with _db_temporal("orbit_t01_f_mano") as (conn, _conectar):
        ids = _escenario(conn)
        kw = _kw(conn, ids["ag"], "e-mano", "kw mano", "8.00")
        _siembra_metricas(conn, [kw])
        enviados: list[str] = []
        monkeypatch.setattr(
            notifica,
            "notifica_temporada",
            lambda texto, **_k: (enviados.append(texto), True)[1],
        )
        handler = _handler_mx({"e-mano": "7.00"})  # Amazon ya tiene 7.00
        res = _corre(conn, dt.date(2027, 1, 1), handler)

        assert res.status == "done", res.notes
        assert len(enviados) == 1
        assert "No tocó 1" in enviados[0], enviados[0]
        assert "los cambiaste tú" in enviados[0]


@_skip_db
def test_temporada01_f_aviso_cuenta_la_inerte_con_bid_heredado(monkeypatch):
    """Una hoja inerte con bid heredado sale por el retorno de inertes y aun
    asi cuenta en «No tocó»: su exclusion es real."""
    from app import notifica

    with _db_temporal("orbit_t01_f_inerte") as (conn, _conectar):
        ids = _escenario(conn)
        kw = _kw(conn, ids["ag"], "e-kw", "kw", "8.00")
        _siembra_metricas(conn, [kw])
        inerte = _kw(conn, ids["ag"], "e-inerte", "kw inerte", "8.00")
        conn.execute(
            "UPDATE ad_entity_state SET current_bid = NULL, bid_currency = NULL"
            " WHERE ad_entity_id = %s",
            (inerte,),
        )
        # sin metricas: v_entidad_inerte la saca del juicio antes del aviso
        enviados: list[str] = []
        monkeypatch.setattr(
            notifica,
            "notifica_temporada",
            lambda texto, **_k: (enviados.append(texto), True)[1],
        )
        handler = _handler_mx({"e-kw": "8.00"})
        res = _corre(conn, dt.date(2027, 1, 1), handler)

        assert res.status == "done", res.notes
        assert len(enviados) == 1
        assert "No tocó 1" in enviados[0], enviados[0]


@_skip_db
def test_temporada01_f_aviso_solo_en_ciclo_live(monkeypatch):
    """Con la escalera del ciclo en practica no se envia el aviso operativo,
    aunque la temporada este en live: las pujas son simuladas."""
    from test_cycle import _config_version

    from app import notifica

    with _db_temporal("orbit_t01_f_shadow") as (conn, _conectar):
        _mundo_aviso(conn)
        _config_version(conn, {**CONFIG_TEMPORADA, "ads_optimizer_mode": "shadow"})
        enviados: list[str] = []
        monkeypatch.setattr(
            notifica,
            "notifica_temporada",
            lambda texto, **_k: (enviados.append(texto), True)[1],
        )
        handler = _handler_mx({"e-kw": "8.00", "e-kw-h": "8.00"})
        res = _corre(conn, dt.date(2027, 1, 1), handler)

        assert res.status == "done", res.notes
        assert enviados == [], "en practica no hay aviso operativo"
        assert "aviso" not in (json.loads(res.notes).get("temporada") or {})


@_skip_db
def test_temporada01_ola2_reactivaciones_un_escaneo_y_cap_saturado(monkeypatch):
    """Ola 2D: aplica_reactivaciones consulta presupuestos UNA sola vez por
    lote (no por decision) y reporta CapSaturado cuando satura el cupo."""
    from app import apply_cola_ejecuta

    with _db_temporal("orbit_t01_ola2_react") as (conn, _conectar):
        ahora = _reloj()
        ids = _semilla(conn)
        from psycopg.types.json import Json

        settings_f = {**CONFIG_TEMPORADA, "ads_apply_cap_amazon_us_pause": 2}
        config_id = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('ola2-cap', %s) RETURNING id",
            (Json(settings_f),),
        ).fetchone()[0]
        cerrado = _episodio(conn, config_id, ids["ciclo_ejec"], ahora, cerrado=True)
        for hoja in (ids["kw"], ids["kw2"]):
            _pausa_baja(
                conn,
                ciclo_id=ids["ciclo_dec"],
                config_id=config_id,
                hoja=hoja,
                ahora=ahora,
                episodio=cerrado,
            )
            _pausado(conn, hoja)
        ciclo_resume = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        estacion = temporada.resuelve(temporada.desde_settings(settings_f), estacion_dia())
        for r in temporada_io.reactivaciones_pendientes(conn, "amazon_us", frozenset()):
            p = ciclo.pendiente_reactivacion(r, estacion, decidido_el=ahora)
            conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end, inputs)"
                " VALUES (%s, %s, 'resume', %s, %s, %s, %s, %s, %s)",
                (
                    ciclo_resume,
                    r.ad_entity_id,
                    ahora,
                    config_id,
                    ahora,
                    ahora.date(),
                    ahora.date(),
                    json.dumps(p.inputs),
                ),
            )
        escaneos = {"n": 0}
        original = apply_cola_ejecuta.presupuestos_campanas_hoy

        def _contando(*args, **kwargs):
            escaneos["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(apply_cola_ejecuta, "presupuestos_campanas_hoy", _contando)
        handler, _vistos = _handler_cortes(estados={"7201": "PAUSED", "7202": "PAUSED"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = apply_cola.aplica_reactivaciones(
            conn, ap, apply_cola.resumes_del_ciclo(conn, ciclo_resume), tick=None
        )

        assert res.confirmadas == 2, res
        assert escaneos["n"] == 1, f"esperaba 1 escaneo de presupuesto, hubo {escaneos['n']}"
        assert len(res.caps_saturados) == 1, res.caps_saturados
        assert res.caps_saturados[0].kind == "pause"


def _handler_mx_completo(bids: dict[str, str], estados: dict[str, str]):
    """Amazon MX simulado: soporta PUT de bid y PUT de state (resume)."""
    import httpx

    remoto_bid = dict(bids)
    remoto_estado = dict(estados)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(
                200,
                json={
                    "profiles": [
                        {
                            "profileId": 414141,
                            "countryCode": "MX",
                            "currencyCode": "MXN",
                            "accountInfo": {"type": "seller", "name": "MX"},
                        }
                    ]
                },
            )
        if request.method == "PUT":
            body = json.loads(request.content)
            obj = body["keywords"][0] if "keywords" in body else body["targetingClauses"][0]
            ext = str(obj.get("keywordId") or obj.get("targetId"))
            if "bid" in obj:
                remoto_bid[ext] = str(obj["bid"])
            if "state" in obj:
                remoto_estado[ext] = str(obj["state"])
            return httpx.Response(207, json={"ack": obj})
        if request.method == "POST" and request.url.path.endswith("/list"):
            contenedor, campo = (
                ("targetingClauses", "targetId")
                if request.url.path == "/sp/targets/list"
                else ("keywords", "keywordId")
            )
            claves = sorted(set(remoto_bid) | set(remoto_estado))
            filas = []
            for ext in claves:
                fila: dict = {campo: ext}
                if ext in remoto_bid:
                    fila["bid"] = remoto_bid[ext]
                if ext in remoto_estado:
                    fila["state"] = remoto_estado[ext]
                filas.append(fila)
            return httpx.Response(200, json={contenedor: filas, "totalResults": len(filas)})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    return handler


def test_dia_de_salida_cierra_episodio_y_reactiva_en_el_mismo_ciclo(db_template, monkeypatch):
    """Hallazgo #6a + Review Focus #3: en shadow global no cierra episodio ni
    inserta resume; el dia exacto de restaura en live cierra el episodio y
    emite y confirma la reactivacion en ESE mismo ciclo."""
    from psycopg.types.json import Json
    from test_cycle import _config_version
    from test_temporada01_b import _medianoche, _settings

    from app import notifica

    conn, _dsn = db_template
    ids = _escenario(conn)
    kw_act = _kw(conn, ids["ag"], "e-act", "kw act", "8.00")
    kw_pausa = _kw(conn, ids["ag"], "e-pausa", "kw pausa", "8.00")
    _siembra_metricas(conn, [kw_act, kw_pausa])

    dia_baja = dt.date(2027, 1, 1)
    dia_salida = dt.date(2027, 2, 1)
    ciclo_baja = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live', 'amazon_mx', 'done', %s, %s) RETURNING id",
        (_medianoche(dia_baja), _medianoche(dia_baja)),
    ).fetchone()[0]
    ep_id = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (_medianoche(dia_baja), ciclo_baja, ids["config_id"]),
    ).fetchone()[0]

    decidido = _medianoche(dt.date(2027, 1, 15))
    dec_pausa = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            ciclo_baja,
            kw_pausa,
            decidido,
            ids["config_id"],
            decidido - dt.timedelta(days=1),
            dt.date(2026, 11, 15),
            dt.date(2027, 1, 1),
            Json(
                {
                    "motor": "bid",
                    "motivo": "pause_economica",
                    "temporada_episodio_id": ep_id,
                    "caso": {"temporada": {"frontera": {"fase_hoja": "baja"}}},
                }
            ),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack, verify_ok)"
        " VALUES (%s, %s, '{}'::jsonb, true)",
        (dec_pausa, decidido),
    )
    q_id = conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo, estado,"
        " vence_el, encolado_at, request_payload) VALUES ('amazon_mx', %s, 'pause', %s, 'live',"
        " 'pending_veto', %s, %s, '{}'::jsonb) RETURNING id",
        (kw_pausa, dec_pausa, decidido, decidido),
    ).fetchone()[0]
    for estado, col in (
        ("released", "released_at"),
        ("applying", "applying_at"),
        ("applied", "applied_at"),
    ):
        conn.execute(
            f"UPDATE apply_queue SET estado = %s, {col} = %s WHERE id = %s",
            (estado, decidido, q_id),
        )
    _pausado(conn, kw_pausa)
    conn.commit()

    enviados: list[str] = []
    monkeypatch.setattr(
        notifica,
        "notifica_temporada",
        lambda texto, **_k: (enviados.append(texto), True)[1],
    )

    # Primero en shadow global: no cierra episodio, no abre efecto ni inserta resume.
    _config_version(conn, {**_settings("live"), "ads_optimizer_mode": "shadow"})
    conn.commit()

    def _sin_http(request):
        raise AssertionError(f"shadow global no debe llamar HTTP: {request.method} {request.url}")

    res_shadow = _corre(conn, dia_salida, _sin_http)
    assert res_shadow.status == "done", res_shadow.notes
    assert (
        conn.execute(
            "SELECT cerrado_el FROM temporada_episodio WHERE id = %s", (ep_id,)
        ).fetchone()[0]
        is None
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'resume'",
            (res_shadow.cycle_id,),
        ).fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT count(*) FROM temporada_efecto").fetchone()[0] == 0
    assert enviados == []

    # Ahora en live el dia exacto de salida: cierra episodio y reactiva en ESE ciclo.
    _config_version(conn, _settings("live"))
    conn.commit()
    handler = _handler_mx_completo(
        bids={"e-act": "8.00", "e-pausa": "8.00"},
        estados={"e-act": "ENABLED", "e-pausa": "PAUSED"},
    )
    res = _corre(conn, dia_salida, handler)

    assert res.status == "done", res.notes
    cerrado_el, cerrado_en = conn.execute(
        "SELECT cerrado_el, cerrado_en_ciclo FROM temporada_episodio WHERE id = %s",
        (ep_id,),
    ).fetchone()
    assert cerrado_el is not None and cerrado_en == res.cycle_id
    resumes = conn.execute(
        "SELECT ad_entity_id FROM decision WHERE cycle_id = %s AND kind = 'resume'",
        (res.cycle_id,),
    ).fetchall()
    assert resumes == [(kw_pausa,)], f"debio emitir resume el dia de salida, obtuvo {resumes}"
    notas = json.loads(res.notes)
    assert notas["apply"]["reactivaciones"]["confirmadas"] == 1, notas
    assert notas["temporada"]["reactivaciones"] == 1, notas
    assert len(enviados) == 1, enviados
    assert "Va a reactivar 1 keyword que pausó en la baja." in enviados[0], enviados[0]
    assert notas["temporada"]["aviso"] == enviados[0]


def test_aviso_temporada_cuenta_propuestas_temporada_y_no_proteccion_campana(
    db_template, monkeypatch
):
    """Hallazgo #6b: el aviso del dia cuenta propuestas reales de presupuesto de
    temporada (pantalla_dinero.lee_campanas) y no len(contadores.propuestas_campana)."""
    from test_cycle import _config_version, _entidad, _estado
    from test_temporada01_b import _medianoche, _settings

    from app import notifica
    from app.ads.campana_config import ConfigCampana, guarda_config

    conn, _dsn = db_template
    ids = _escenario(conn)
    kw = _kw(conn, ids["ag"], "e-kw-prop", "kw prop", "8.00")
    _siembra_metricas(conn, [kw])

    # Dos campanas adicionales ENABLED sin config de presupuesto: ADS PROTECCION 01
    # evalua las 3 campanas, pero solo la primera tiene propuesta de temporada (300 -> 210).
    for ext in ("b-camp-2", "b-camp-3"):
        c_extra = _entidad(conn, "amazon_mx", "campaign", ext)
        _estado(conn, c_extra, synced_at=_medianoche(dt.date(2026, 12, 31)))

    guarda_config(
        conn,
        "amazon_mx",
        [
            ConfigCampana(
                campana_externa="b-camp",
                presupuesto_diario=Decimal("300.00"),
                moneda="MXN",
                estrategia_puja="LEGACY_FOR_SALES",
                ajuste_top_pct=0,
                ajuste_resto_pct=0,
                ajuste_producto_pct=0,
                fuera_de_amazon=None,
            )
        ],
        _medianoche(dt.date(2026, 12, 15)),
    )
    _config_version(conn, {**_settings("live"), "ads_propuestas_campana": True})
    conn.commit()

    enviados: list[str] = []
    monkeypatch.setattr(
        notifica,
        "notifica_temporada",
        lambda texto, **_k: (enviados.append(texto), True)[1],
    )
    handler = _handler_mx({"e-kw-prop": "8.00"})
    res = _corre(conn, dt.date(2027, 1, 1), handler)

    assert res.status == "done", res.notes
    notas = json.loads(res.notes)
    assert sum(notas["propuestas_campana"].values()) == 3, notas["propuestas_campana"]
    assert len(enviados) == 1, enviados
    assert (
        "Hay 1 campañas con un presupuesto más bajo en «Dónde poner el dinero»." in enviados[0]
    ), enviados[0]
    assert "Hay 3 campañas" not in enviados[0], enviados[0]
