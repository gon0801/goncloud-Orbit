"""ADS PROTECCION P03: E2E del ciclo, digest, cola y configuracion.

Llaman a las funciones reales de entrada (`app.cycle.corre_ciclo`,
`app.notifica.digest_ciclo`, `app.apply_cola.libera_vencidos`,
`app.cli_productos.main` y `app.config_write.guarda_config`).
"""

import datetime as dt
import json
from decimal import Decimal

import pytest
from test_apply_cola import (
    MOTIVO_YA_NO_CALIFICA,
    _aplicador,
    _decision_corte,
    _encola_fila,
    _fechas,
    _handler_cortes,
    _payload_pause,
    _semilla,
)
from test_apply_cola import (
    _metrica as _metrica_cola,
)
from test_cycle import (
    DECIDED_AT,
    _corre,
    _db_temporal,
    _entidad,
    _estado,
    _metrica,
    _obs,
    _rango,
    _siembra_maestra,
)

from app import apply_cola, cli_productos, config_write, notifica, pausa_economica_io
from app.optimizer import pausa_economica as regla

CAPS = {
    "ads_apply_cap_amazon_us_bid": 20,
    "ads_apply_cap_amazon_us_pause": 20,
    "ads_apply_cap_amazon_us_negative": 20,
    "ads_apply_cap_amazon_us_harvest": 20,
}


def _ceros_recientes(conn, ids, entidades) -> None:
    """Completa la cobertura de plataforma en [D-9..D-1] con ceros SOLO en los
    dias que aun no tienen ingesta (no pisa la siembra existente)."""
    run = conn.execute("SELECT max(ingest_run_id) FROM ads_metric_observation").fetchone()[0]
    if run is None:
        run = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('p03') RETURNING id"
        ).fetchone()[0]
    desde = DECIDED_AT.date() - dt.timedelta(days=9)
    hasta = DECIDED_AT.date() - dt.timedelta(days=1)
    existentes = {
        fila[0]
        for fila in conn.execute(
            "SELECT DISTINCT metric_date FROM ads_metric_observation"
            " WHERE metric_date BETWEEN %s AND %s",
            (desde, hasta),
        ).fetchall()
    }
    entidad = entidades[0]
    for fecha in _rango(desde, hasta):
        if fecha in existentes:
            continue
        _metrica(
            conn,
            run,
            entidad,
            fecha,
            _obs(fecha),
            cost="0",
            ad_revenue="0",
            clicks=0,
            orders=0,
            impressions=10,
        )
    return run


def _siembra_sangrante(conn, external: str) -> int:
    """Hoja que sangra en la ventana madura y en la de pujas (recorte -25%)."""
    ag = conn.execute(
        "SELECT id FROM ad_entity WHERE platform = 'amazon_us' AND kind = 'ad_group' LIMIT 1"
    ).fetchone()[0]
    kw = _entidad(
        conn,
        "amazon_us",
        "keyword",
        external,
        parent=ag,
        match_type="EXACT",
        keyword_text=f"kw {external}",
    )
    _estado(
        conn,
        kw,
        synced_at=DECIDED_AT - dt.timedelta(hours=4),
        current_bid=Decimal("1.00"),
        bid_currency="USD",
    )
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('p03') RETURNING id").fetchone()[0]
    for fecha in _rango(dt.date(2026, 7, 14), dt.date(2026, 8, 16)):
        _metrica(
            conn,
            run,
            kw,
            fecha,
            _obs(fecha),
            cost="10",
            ad_revenue="5",
            clicks=10,
            orders=1,
            impressions=100,
        )
    return kw


def _filas_evaluacion(conn, cycle_id):
    return conn.execute(
        "SELECT ad_entity_id, modo, veredicto, motivo, decision_id"
        " FROM pausa_economica_evaluacion WHERE cycle_id = %s ORDER BY ad_entity_id",
        (cycle_id,),
    ).fetchall()


def test_corre_ciclo_shadow_evalua_sin_tocar_decision_ni_cola():
    """Matriz (2): practica con optimizer live; filas shadow, cero decisiones
    economicas y LAS MISMAS decisiones que con la regla apagada."""

    def _corre_con(etiqueta, economica):
        with _db_temporal(etiqueta) as (conn, _):
            ids = _siembra_maestra(
                conn,
                escalera="live",
                settings={
                    "ads_bid_politica_amazon_us": "niveles_v3",
                    "ads_pause_economica_amazon_us": economica,
                    **CAPS,
                },
            )
            _ceros_recientes(conn, ids, [ids["kw_bid"], ids["kw_pause"]])
            res = _corre(conn)
            decisiones = conn.execute(
                "SELECT ad_entity_id, kind, new_value, inputs->>'motivo'"
                " FROM decision WHERE cycle_id = %s ORDER BY ad_entity_id, kind",
                (res.cycle_id,),
            ).fetchall()
            filas = _filas_evaluacion(conn, res.cycle_id)
            economicas = conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'pause'"
                " AND inputs->>'motivo' = 'pause_economica'",
                (res.cycle_id,),
            ).fetchone()[0]
            return {
                "status": res.status,
                "notes": res.notes,
                "decisiones": decisiones,
                "filas": filas,
                "economicas": economicas,
            }

    sin_regla = _corre_con("orbit_p03_shadow_off", "off")
    con_regla = _corre_con("orbit_p03_shadow_on", "shadow")
    assert con_regla["decisiones"] == sin_regla["decisiones"]
    assert con_regla["status"] in ("done", "degraded"), con_regla["notes"]
    filas = con_regla["filas"]
    assert filas, "la practica debe dejar una fila por hoja juzgada"
    assert {f[1] for f in filas} == {"shadow"}
    assert {f[4] for f in filas} == {None}
    assert con_regla["economicas"] == 0
    notas = json.loads(con_regla["notes"])
    assert notas["pausa_economica"]["evaluadas"] == len(filas)
    assert "error" not in notas["pausa_economica"]


def test_corre_ciclo_lector_fallando_no_cambia_decisiones_de_puja(monkeypatch):
    """Matriz (1): un lector roto se aisla en SAVEPOINT; en live degrada a
    reciente_incompleto y las decisiones de puja son LAS MISMAS."""

    def _corre_con(etiqueta, falla):
        with _db_temporal(etiqueta) as (conn, _):
            ids = _siembra_maestra(
                conn,
                escalera="live",
                settings={
                    "ads_bid_politica_amazon_us": "niveles_v3",
                    "ads_pause_economica_amazon_us": "live",
                    **CAPS,
                },
            )
            _ceros_recientes(conn, ids, [ids["kw_bid"], ids["kw_pause"]])
            if falla:

                def _revienta(*_a, **_k):
                    raise RuntimeError("lector roto")

                monkeypatch.setattr(pausa_economica_io, "lee_cache_reciente", _revienta)
            res = _corre(conn)
            decisiones = conn.execute(
                "SELECT ad_entity_id, kind, new_value, inputs->>'motivo'"
                " FROM decision WHERE cycle_id = %s ORDER BY ad_entity_id, kind",
                (res.cycle_id,),
            ).fetchall()
            notas = json.loads(res.notes)
            return decisiones, notas

    base, _ = _corre_con("orbit_p03_sin_falla", False)
    con_falla, notas = _corre_con("orbit_p03_con_falla", True)
    assert con_falla == base
    assert notas["pausa_economica"].get("error")


def test_corre_ciclo_hueco_en_d_minus_2_da_reciente_incompleto():
    """Matriz (1): un hueco de plataforma en D-2 deja la ventana incompleta."""
    with _db_temporal("orbit_p03_hueco") as (conn, _):
        ids = _siembra_maestra(
            conn,
            escalera="live",
            settings={
                "ads_bid_politica_amazon_us": "niveles_v3",
                "ads_pause_economica_amazon_us": "live",
                **CAPS,
            },
        )
        _ceros_recientes(conn, ids, [ids["kw_bid"]])
        # Una hoja que SI cruza la puerta madura es la que puede quedar
        # "incompleta" por el hueco reciente.
        sangrante = _siembra_sangrante(conn, "9621")
        # Deshace el dia D-2: sin ingesta de plataforma ese dia. El append-only
        # de metricas se desactiva SOLO en la base desechable de la prueba.
        conn.execute("SET session_replication_role = replica")
        try:
            conn.execute(
                "DELETE FROM ads_metric_observation WHERE metric_date = %s",
                (DECIDED_AT.date() - dt.timedelta(days=2),),
            )
        finally:
            conn.execute("SET session_replication_role = origin")
        res = _corre(conn)
        filas = _filas_evaluacion(conn, res.cycle_id)
        assert filas
        assert {f[2] for f in filas} <= {"mantener", "pausar"}
        economicas = conn.execute(
            "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'pause'"
            " AND inputs->>'motivo' = 'pause_economica'",
            (res.cycle_id,),
        ).fetchone()[0]
        assert economicas == 0
        incompletos = [f for f in filas if f[3] == regla.MOTIVO_INCOMPLETO]
        assert [f for f in incompletos if f[0] == sangrante], (
            "una hoja que cruza la madura con hueco reciente debe quedar incompleta"
        )


def test_corre_ciclo_cortacircuito_retira_pausas_y_conserva_recortes(monkeypatch):
    """Matriz (2): 6 hojas sangrantes en live -> 0 pausas economicas, aviso y
    recortes de puja iguales a los de la practica."""
    hojas = ["9501", "9502", "9503", "9504", "9505", "9506"]

    def _corre_con(etiqueta, economica):
        with _db_temporal(etiqueta) as (conn, _):
            ids = _siembra_maestra(
                conn,
                escalera="live",
                settings={
                    "ads_bid_politica_amazon_us": "niveles_v3",
                    "ads_pause_economica_amazon_us": economica,
                    **CAPS,
                },
            )
            sangrantes = [_siembra_sangrante(conn, ext) for ext in hojas]
            _ceros_recientes(conn, ids, [ids["kw_bid"], ids["kw_pause"], *sangrantes])
            res = _corre(conn)
            decisionadas = conn.execute(
                "SELECT ad_entity_id, kind, new_value, inputs->>'motivo'"
                " FROM decision WHERE cycle_id = %s AND kind = 'bid'"
                " ORDER BY ad_entity_id",
                (res.cycle_id,),
            ).fetchall()
            economicas = conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'pause'"
                " AND inputs->>'motivo' = 'pause_economica'",
                (res.cycle_id,),
            ).fetchone()[0]
            filas = _filas_evaluacion(conn, res.cycle_id)
            return {
                "bids": decisionadas,
                "notas": json.loads(res.notes),
                "sangrantes": sangrantes,
                "economicas": economicas,
                "filas": filas,
            }

    shadow = _corre_con("orbit_p03_ct_shadow", "shadow")
    live = _corre_con("orbit_p03_ct_live", "live")
    notas = live["notas"]
    assert notas["pausa_economica"]["anomalia_tope"] is True
    assert notas["skips"]["entidad"]["pausa_economica_anomalia_tope"] == len(hojas)
    assert live["economicas"] == 0

    # Las decisiones NO economicas del ciclo son las mismas que en la
    # practica: el cortacircuito solo retira pausas economicas.
    def _no_economicas(filas):
        return [f for f in filas if not (f[1] == "pause" and f[3] == "pause_economica")]

    assert _no_economicas(live["bids"]) == _no_economicas(shadow["bids"])
    filas = live["filas"]
    sangrantes = set(live["sangrantes"])
    retiradas = [f for f in filas if f[0] in sangrantes and f[2] == "pausar"]
    assert len(retiradas) == len(hojas)
    assert {f[4] for f in retiradas} == {None}


def test_cortacircuito_no_cuenta_pausas_en_cooldown(monkeypatch):
    """5 pausas emitibles + 1 en cooldown de pausa: el tope NO se dispara.

    La sexta no habria llegado a la cola (cooldown), asi que no debe inflar el
    conteo ni retirar las cinco validas. Su fila de evaluacion se conserva con
    `decision_id NULL` y queda contada como `cooldown_7d`."""
    hojas = ["9611", "9612", "9613", "9614", "9615", "9616"]
    with _db_temporal("orbit_p03_ct_cooldown") as (conn, _):
        ids = _siembra_maestra(
            conn,
            escalera="live",
            settings={
                "ads_bid_politica_amazon_us": "niveles_v3",
                "ads_pause_economica_amazon_us": "live",
                **CAPS,
            },
        )
        sangrantes = [_siembra_sangrante(conn, ext) for ext in hojas]
        _ceros_recientes(conn, ids, [ids["kw_bid"], ids["kw_pause"], *sangrantes])
        from app import cycle as ciclo_mod

        sexta = sangrantes[-1]
        monkeypatch.setattr(
            ciclo_mod.g,
            "en_cooldown",
            lambda _conn, entidad, *, ahora, kind=None: entidad == sexta,
        )
        res = _corre(conn)
        notas = json.loads(res.notes)
        assert "anomalia_tope" not in notas["pausa_economica"]
        economicas = conn.execute(
            "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'pause'"
            " AND inputs->>'motivo' = 'pause_economica'",
            (res.cycle_id,),
        ).fetchone()[0]
        assert economicas == len(hojas) - 1
        assert notas["skips"]["entidad"]["cooldown_7d"] == 1
        filas = {f[0]: f for f in _filas_evaluacion(conn, res.cycle_id)}
        assert all(filas[hoja][2] == "pausar" for hoja in sangrantes)
        assert filas[sexta][4] is None
        assert all(filas[hoja][4] is not None for hoja in sangrantes[:-1])


def test_digest_ciclo_menciona_anomalia_tope_y_la_omite_sin_clave():
    linea = "pausa economica frenada por tope: 6"
    con = notifica.digest_ciclo(
        {
            "cycle_id": 7,
            "plataforma": "amazon_us",
            "modo": "live",
            "status": "done",
            "decisions_count": 0,
            "skips": {"entidad": {"pausa_economica_anomalia_tope": 6}},
        }
    )
    assert linea in con
    sin = notifica.digest_ciclo(
        {
            "cycle_id": 7,
            "plataforma": "amazon_us",
            "modo": "live",
            "status": "done",
            "decisions_count": 0,
            "skips": {"entidad": {"entidad_inerte": 2}},
        }
    )
    assert "pausa economica" not in sin


def _siembra_cola(conn, *, modo_economica, liberada_en=None):
    """Una fila economica released cuya hoja gana target pero su ventana
    reciente no siempre es medible."""
    ids = _semilla(
        conn,
        caps={
            "ads_apply_cap_amazon_us_pause": 2,
            "ads_apply_cap_amazon_us_negative": 5,
            "ads_pause_economica_amazon_us": modo_economica,
        },
    )
    d = ids["ahora"]
    conn.execute("UPDATE ads_optimizer_goal SET target_acos_pct = 20 WHERE platform = 'amazon_us'")
    conn.execute(
        "UPDATE optimizer_cycle SET notes = %s WHERE id = %s",
        (json.dumps({"target": {"target_aplicado": None}}), ids["ciclo_ejec"]),
    )
    for i, fecha in enumerate(
        _fechas(d.date() - dt.timedelta(days=20), d.date() - dt.timedelta(days=11))
    ):
        _metrica_cola(
            conn,
            ids["run"],
            ids["kw"],
            fecha,
            clicks=5,
            cost=10 if i == 0 else 15,
            orders=1 if i == 0 else 0,
            ad_revenue=100 if i == 0 else 0,
        )
    dec = _decision_corte(
        conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause", motivo="pause_economica"
    )
    q = _encola_fila(
        conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"), encolado=liberada_en
    )
    conn.execute(
        "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE id = %s",
        (liberada_en or d, q),
    )
    conn.execute(
        "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
        " VALUES (%s, %s, 'espera_reciente_economica') ON CONFLICT DO NOTHING",
        (dec, ids["ciclo_ejec"]),
    )
    return ids, d, q


def test_libera_vencidos_espera_reciente_sin_gastar_cuota():
    with _db_temporal("orbit_p03_cola_espera") as (conn, _):
        ids, d, q = _siembra_cola(conn, modo_economica="live")
        _ceros_recientes(conn, ids, [ids["kw"]])
        conn.execute(
            "DELETE FROM ads_metric_observation WHERE metric_date = %s",
            (d.date() - dt.timedelta(days=2),),
        )
        handler, vistos = _handler_cortes()
        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
            modo_economica="live",
        )
        assert res.espera_reciente >= 1
        assert res.sin_quota == 0
        assert all(r.method != "PUT" for r in vistos)
        assert conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone() == (
            "released",
        )


def test_libera_vencidos_espera_vencida_se_descarta():
    with _db_temporal("orbit_p03_cola_vencida") as (conn, _):
        ids, d, q = _siembra_cola(
            conn, modo_economica="live", liberada_en=DECIDED_AT - dt.timedelta(days=8)
        )
        handler, vistos = _handler_cortes()
        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
            modo_economica="live",
        )
        assert res.esperas_vencidas == 1
        assert res.sin_quota == 0
        assert all(r.method != "PUT" for r in vistos)
        assert conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone() == ("discarded", MOTIVO_YA_NO_CALIFICA)


def test_libera_vencidos_modo_no_live_descarta_espera_reciente():
    with _db_temporal("orbit_p03_cola_shadow") as (conn, _):
        ids, d, q = _siembra_cola(conn, modo_economica="shadow")
        handler, _vistos = _handler_cortes()
        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
            modo_economica="shadow",
        )
        assert apply_cola.MOTIVO_PAUSA_ECONOMICA_APAGADA in res.descartadas
        assert conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone() == ("discarded", apply_cola.MOTIVO_PAUSA_ECONOMICA_APAGADA)


def test_cli_rechaza_pausa_y_pausa_economica_juntas(capsys):
    """`--pausa` va solo: combinarla con `--pausa-economica` sale 2 sin tocar
    config (antes devolvia 0 apagando solo la pausa por producto)."""
    from types import SimpleNamespace

    args = SimpleNamespace(
        platform="amazon_us",
        meta=None,
        pausa="off",
        clics=None,
        gasto=None,
        economia=None,
        bid_economia=None,
        proteccion=None,
        pausa_economica="off",
    )
    assert cli_productos.corre(args) == 2
    assert "va solo" in capsys.readouterr().err


def test_guarda_config_rechaza_modo_invalido_y_editar_mx_no_toca_us():
    base = {
        "ads_optimizer_mode": "live",
        "ads_pause_economica_amazon_us": "off",
        "ads_propuestas_campana": False,
    }
    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config(base, "amazon_mx", pausa_economica_modo="encendido")
    nuevo, cambios = config_write.proxima_config(base, "amazon_mx", pausa_economica_modo="shadow")
    assert nuevo["ads_pause_economica_amazon_mx"] == "shadow"
    assert nuevo["ads_pause_economica_amazon_us"] == "off"
    assert nuevo["ads_optimizer_mode"] == "live"
    assert nuevo["ads_propuestas_campana"] is False
    assert any("pausa economica amazon_mx" in c for c in cambios)
    # `off` quita la clave (misma semantica que _aplica_ubicacion).
    quitado, _ = config_write.proxima_config(nuevo, "amazon_mx", pausa_economica_modo="off")
    assert "ads_pause_economica_amazon_mx" not in quitado


def test_cli_productos_baja_a_shadow_descarta_esperas_en_la_misma_tx(monkeypatch):
    with _db_temporal("orbit_p03_cli") as (conn, _extra):
        from types import SimpleNamespace

        import psycopg
        from test_schema import _test_dsn

        ids, d, q = _siembra_cola(conn, modo_economica="live")
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
            " SELECT %s, %s, 'espera_reciente_economica'"
            " ON CONFLICT DO NOTHING",
            (
                conn.execute("SELECT decision_id FROM apply_queue WHERE id = %s", (q,)).fetchone()[
                    0
                ],
                ids["ciclo_ejec"],
            ),
        )
        db = conn.execute("SELECT current_database()").fetchone()[0]
        monkeypatch.setenv(
            "ORBIT_DSN_ADMIN", psycopg.conninfo.make_conninfo(_test_dsn(), dbname=db)
        )
        args = SimpleNamespace(
            platform="amazon_us",
            meta=None,
            pausa=None,
            clics=None,
            gasto=None,
            economia=None,
            bid_economia=None,
            proteccion=None,
            pausa_economica="shadow",
        )
        assert cli_productos.corre(args) == 0
        assert conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone() == ("discarded", apply_cola.MOTIVO_PAUSA_ECONOMICA_APAGADA)
        settings = conn.execute(
            "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert settings["ads_pause_economica_amazon_us"] == "shadow"


def test_editar_mx_no_toca_us_ni_optimizer_mode_ni_propuestas():
    base = {
        "ads_optimizer_mode": "shadow",
        "ads_pause_economica_amazon_us": "live",
        "ads_propuestas_campana": False,
    }
    nuevo, _ = config_write.proxima_config(base, "amazon_mx", pausa_economica_modo="live")
    assert nuevo["ads_pause_economica_amazon_mx"] == "live"
    assert nuevo["ads_pause_economica_amazon_us"] == "live"
    assert nuevo["ads_optimizer_mode"] == "shadow"
    assert nuevo["ads_propuestas_campana"] is False


def test_resumen_ultimo_ciclo_para_salud():
    with _db_temporal("orbit_p03_salud") as (conn, _):
        ids = _siembra_maestra(
            conn,
            escalera="live",
            settings={
                "ads_bid_politica_amazon_us": "niveles_v3",
                "ads_pause_economica_amazon_us": "shadow",
                **CAPS,
            },
        )
        _ceros_recientes(conn, ids, [ids["kw_bid"], ids["kw_pause"]])
        res = _corre(conn)
        resumen = pausa_economica_io.resumen_ultimo_ciclo(conn, "amazon_us")
        assert resumen is not None
        assert resumen["cycle_id"] == res.cycle_id
        assert resumen["evaluadas"] == len(_filas_evaluacion(conn, res.cycle_id))
