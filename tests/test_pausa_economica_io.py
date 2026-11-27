"""ADS PROTECCION P03: IO y esquema de la pausa economica v2.

`lee_cache_reciente` (cobertura + veneno), `tx3_pausa_economica` (mapeo de
`decision_id`, `SAVEPOINT` y `error_tx3`), append-only/grants/vocabulario de
`pausa_economica_evaluacion` y la guarda de la reversa (Regla 5).
"""

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Json

from app import pausa_economica_io as pio
from app.cycle_grupos import _Contadores
from app.cycle_serializacion import _Pendiente
from app.optimizer import pausa_economica as regla

HOY = dt.date(2026, 11, 27)
AHORA = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.UTC)


def _entidad(conn, external: str, *, platform="amazon_us", kind="keyword") -> int:
    if kind == "keyword":
        return conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, match_type, keyword_text)"
            " VALUES (%s, 'keyword', %s, 'EXACT', %s) RETURNING id",
            (platform, external, f"kw {external}"),
        ).fetchone()[0]
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, %s, %s) RETURNING id",
        (platform, kind, external),
    ).fetchone()[0]


def _run(conn) -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-p03') RETURNING id"
    ).fetchone()[0]


def _metrica(
    conn,
    run_id,
    entidad,
    fecha,
    *,
    cost="0",
    revenue="0",
    orders=0,
    moneda="USD",
):
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, 10, 1, %s, %s)",
        (
            entidad,
            fecha,
            dt.datetime(fecha.year, fecha.month, fecha.day, 1, tzinfo=dt.UTC),
            moneda,
            None if cost is None else Decimal(str(cost)),
            None if revenue is None else Decimal(str(revenue)),
            orders,
            run_id,
        ),
    )


def _agregado(cost="145", revenue="100", *, moneda="USD"):
    return regla.EntradaPausaEconomica(
        cortes=_cortes(cost, revenue, moneda=moneda),
        reciente=regla.LecturaRecienteEconomica(
            desde=HOY - dt.timedelta(days=9),
            hasta=HOY - dt.timedelta(days=1),
            evaluado_el=HOY,
            moneda=moneda,
            completa=True,
            cost=Decimal("0"),
            ad_revenue=Decimal("0"),
            orders=0,
        ),
        moneda=moneda,
        target_acos_pct=Decimal("20"),
        exceso_minimo=Decimal("80"),
    )


def _cortes(cost, revenue, *, moneda="USD"):
    from app.optimizer.windows import AgregadoMetricas

    fin = HOY - dt.timedelta(days=10)
    return AgregadoMetricas(
        window_start=fin - dt.timedelta(days=29),
        window_end=fin,
        fechas=tuple(fin - dt.timedelta(days=n) for n in range(22)),
        metric_currency=moneda,
        cost=None if cost is None else Decimal(str(cost)),
        ad_revenue=None if revenue is None else Decimal(str(revenue)),
        revenue_same_sku=Decimal("0"),
        impressions=100,
        clicks=5,
        orders=1,
        observed_at_max=AHORA,
    )


def test_lee_cache_reciente_cobertura_y_veneno(db_template):
    conn, _dsn = db_template
    conn.autocommit = True
    run = _run(conn)
    hoja = _entidad(conn, "p03-a")
    relleno = _entidad(conn, "p03-b")
    dias = [HOY - dt.timedelta(days=n) for n in range(9, 0, -1)]
    for fecha in dias:
        if fecha != HOY - dt.timedelta(days=2):
            _metrica(conn, run, relleno, fecha)
    _metrica(conn, run, hoja, dias[0], cost="30", revenue="0")

    cache = pio.lee_cache_reciente(conn, "amazon_us", ahora=AHORA)
    lectura = cache.lectura(hoja, desde=dias[0], hasta=dias[-1])
    # D-2 no tuvo ingesta de plataforma: el rango NO esta cubierto.
    assert lectura.completa is False
    assert lectura.cost is None and lectura.orders is None

    # Un dia cubierto sin fila de la hoja es cero MEDIDO.
    _metrica(conn, run, relleno, HOY - dt.timedelta(days=2))
    cache = pio.lee_cache_reciente(conn, "amazon_us", ahora=AHORA)
    lectura = cache.lectura(hoja, desde=dias[0], hasta=dias[-1])
    assert lectura.completa is True
    assert lectura.cost == Decimal("30.0000") or lectura.cost == Decimal("30")
    assert lectura.orders == 0

    # Otra moneda en una fila de la hoja envenena la ventana. El trigger de
    # plataforma impide esa fila en operacion normal: se desactiva para
    # ejercitar la defensa en profundidad del lector.
    conn.execute("SET session_replication_role = replica")
    try:
        _metrica(conn, run, hoja, HOY - dt.timedelta(days=5), cost="1", moneda="MXN")
    finally:
        conn.execute("SET session_replication_role = origin")
    cache = pio.lee_cache_reciente(conn, "amazon_us", ahora=AHORA)
    assert cache.lectura(hoja, desde=dias[0], hasta=dias[-1]).completa is False

    # NULL en una metrica tambien envenena (jamas se inventa un cero).
    hoja2 = _entidad(conn, "p03-c")
    _metrica(conn, run, hoja2, HOY - dt.timedelta(days=3), cost=None)
    cache = pio.lee_cache_reciente(conn, "amazon_us", ahora=AHORA)
    assert cache.lectura(hoja2, desde=dias[0], hasta=dias[-1]).completa is False


def _siembra_tx3(conn, *, modo="live") -> tuple[int, int, int, int]:
    config = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('p03', %s) RETURNING id",
        (Json({"ads_pause_economica_amazon_us": "live"}),),
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    hoja = _entidad(conn, f"p03-tx-{modo}")
    decision = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            ciclo,
            hoja,
            AHORA,
            config,
            AHORA,
            HOY - dt.timedelta(days=39),
            HOY - dt.timedelta(days=10),
            Json({"motivo": "pause_economica"}),
        ),
    ).fetchone()[0]
    return config, ciclo, hoja, decision


def _evaluacion(hoja: int, *, modo="live"):
    entrada = _agregado()
    return pio.EvaluacionPausa(
        ad_entity_id=hoja,
        platform="amazon_us",
        modo=modo,
        decided_at=AHORA,
        ventana_desde=entrada.reciente.desde,
        ventana_hasta=entrada.reciente.hasta,
        moneda="USD",
        target_acos_pct=Decimal("20"),
        entrada=entrada,
        veredicto=regla.evalua_pausa_economica(entrada),
    )


def _pendiente(hoja: int) -> _Pendiente:
    return _Pendiente(
        ad_entity_id=hoja,
        kind="pause",
        data_observed_at=AHORA,
        window_start=HOY - dt.timedelta(days=39),
        window_end=HOY - dt.timedelta(days=10),
        search_term=None,
        old_value=None,
        new_value=None,
        value_currency=None,
        inputs={"motivo": "pause_economica"},
    )


def test_tx3_mapea_decision_id_y_persiste_null(db_template):
    conn, _dsn = db_template
    conn.autocommit = True
    config, ciclo, hoja, decision = _siembra_tx3(conn)
    contadores = _Contadores()
    contadores.pausa_economica_evals.append(_evaluacion(hoja))
    cuerpo: dict = {}
    pio.tx3_pausa_economica(conn, ciclo, config, contadores, [_pendiente(hoja)], [decision], cuerpo)
    fila = conn.execute(
        "SELECT modo, veredicto, motivo, decision_id FROM pausa_economica_evaluacion"
        " WHERE cycle_id = %s",
        (ciclo,),
    ).fetchone()
    assert fila == ("live", "pausar", "pause_economica", decision)
    assert "error_tx3" not in cuerpo.get("pausa_economica", {})

    # Shadow: la practica JAMAS enlaza una decision.
    config_s, ciclo_s, hoja_s, _dec = _siembra_tx3(conn, modo="shadow")
    contadores_s = _Contadores()
    contadores_s.pausa_economica_evals.append(_evaluacion(hoja_s, modo="shadow"))
    pio.tx3_pausa_economica(conn, ciclo_s, config_s, contadores_s, [], [], {})
    assert conn.execute(
        "SELECT modo, decision_id FROM pausa_economica_evaluacion WHERE cycle_id = %s",
        (ciclo_s,),
    ).fetchone() == ("shadow", None)


def test_tx3_error_no_aborta_tx3(db_template):
    conn, _dsn = db_template
    conn.autocommit = True
    config, ciclo, hoja, _decision = _siembra_tx3(conn)
    contadores = _Contadores()
    contadores.pausa_economica_evals.append(_evaluacion(hoja))
    cuerpo: dict = {"pausa_economica": {"evaluadas": 1}}
    # cycle_id inexistente: FK -> el SAVEPOINT captura y TX3 sigue viva.
    pio.tx3_pausa_economica(conn, 10**9, config, contadores, [], [], cuerpo)
    assert "error_tx3" in cuerpo["pausa_economica"]
    assert cuerpo["pausa_economica"]["evaluadas"] == 1
    assert conn.execute("SELECT count(*) FROM pausa_economica_evaluacion").fetchone()[0] == 0


def test_pausa_economica_evaluacion_append_only_grants_y_vocabulario(db_template):
    conn, _dsn = db_template
    conn.autocommit = True
    config, ciclo, hoja, _decision = _siembra_tx3(conn)
    contadores = _Contadores()
    contadores.pausa_economica_evals.append(_evaluacion(hoja))
    pio.tx3_pausa_economica(conn, ciclo, config, contadores, [], [], {})

    with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
        conn.execute("UPDATE pausa_economica_evaluacion SET motivo = 'pause_economica'")
    with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
        conn.execute("DELETE FROM pausa_economica_evaluacion")
    with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
        conn.execute("TRUNCATE pausa_economica_evaluacion")

    privilegios = conn.execute(
        "SELECT has_table_privilege('app_read', 'pausa_economica_evaluacion', 'SELECT'),"
        " has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'INSERT'),"
        " has_table_privilege('app_ingest', 'pausa_economica_evaluacion', 'INSERT'),"
        " has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'UPDATE'),"
        " has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'DELETE'),"
        " has_sequence_privilege('app_decide',"
        " 'pausa_economica_evaluacion_id_seq', 'USAGE')"
    ).fetchone()
    assert privilegios == (True, True, False, False, False, True)

    # `pausar` exige `pause_economica`; `mantener` exige el vocabulario.
    base = (
        ciclo,
        hoja,
        "amazon_us",
        "live",
        "economic_pause_v2",
        config,
        AHORA,
        HOY - dt.timedelta(days=9),
        HOY - dt.timedelta(days=1),
        "USD",
        Decimal("20"),
        True,
        Json({}),
        Decimal("80"),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO pausa_economica_evaluacion (cycle_id, ad_entity_id, platform,"
            " modo, politica, config_version_id, decided_at, ventana_reciente_desde,"
            " ventana_reciente_hasta, moneda, target_acos_pct, cortes_completa, entrada,"
            " exceso_minimo, veredicto, motivo)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " 'pausar', 'otro_motivo')",
            base,
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO pausa_economica_evaluacion (cycle_id, ad_entity_id, platform,"
            " modo, politica, config_version_id, decided_at, ventana_reciente_desde,"
            " ventana_reciente_hasta, moneda, target_acos_pct, cortes_completa, entrada,"
            " exceso_minimo, veredicto, motivo)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " 'mantener', 'motivo_inventado')",
            base,
        )


def test_motivos_v2_espejan_el_check_de_la_migracion():
    """Espejo estatico (brief P03 punto 5): las 5 constantes de motivo de
    `mantener` y el CHECK de 0105 son la misma lista, mismo orden."""
    texto = (
        Path(__file__).resolve().parents[1] / "migrations" / "0105_pausa_economica_v2.sql"
    ).read_text(encoding="utf-8")
    bloque = texto.split("pausa_economica_evaluacion_motivo_veredicto", 1)[1]
    bloque = bloque.split("motivo IN (", 1)[1].split("))", 1)[0]
    motivos = tuple(re.findall(r"'([a-z_]+)'", bloque))
    assert motivos == regla.MOTIVOS_MANTENER


def test_reversa_0105_aborta_con_datos_live_o_espera_reciente(db_template):
    conn, _dsn = db_template
    conn.autocommit = True
    reversa = (
        Path(__file__).resolve().parents[1] / "migrations" / "0105_reversa_pausa_economica_v2.sql"
    ).read_text(encoding="utf-8")
    config, ciclo, hoja, decision = _siembra_tx3(conn)
    contadores = _Contadores()
    contadores.pausa_economica_evals.append(_evaluacion(hoja))
    pio.tx3_pausa_economica(conn, ciclo, config, contadores, [_pendiente(hoja)], [decision], {})
    with pytest.raises(psycopg.errors.RaiseException, match="filas live"):
        conn.execute(reversa)
    assert conn.execute("SELECT to_regclass('public.pausa_economica_evaluacion')").fetchone()[0]


def test_reversa_0105_sin_datos_borra_la_tabla_y_restaura_el_check(db_template):
    conn, _dsn = db_template
    conn.autocommit = True
    reversa = (
        Path(__file__).resolve().parents[1] / "migrations" / "0105_reversa_pausa_economica_v2.sql"
    ).read_text(encoding="utf-8")
    conn.execute(reversa)
    assert (
        conn.execute("SELECT to_regclass('public.pausa_economica_evaluacion')").fetchone()[0]
        is None
    )
    _, defin = conn.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'decision_sin_aplicar'::regclass"
        " AND conname = 'decision_sin_aplicar_motivo_check'"
    ).fetchone()
    assert "espera_reciente_economica" not in defin
    # Restaura EXACTAMENTE el vocabulario de 0099 (mismo orden incluido):
    # un literal distinto rechazaria un motivo que el aplicador si escribe.
    texto_0099 = (
        Path(__file__).resolve().parents[1] / "migrations" / "0099_arreglos05_tope_ciclo.sql"
    ).read_text(encoding="utf-8")
    bloque = texto_0099.split("ADD CONSTRAINT decision_sin_aplicar_motivo_check", 1)[1]
    bloque = bloque.split("motivo IN (", 1)[1].split("))", 1)[0]
    esperados = tuple(re.findall(r"'([a-z_]+)'", bloque))
    assert tuple(re.findall(r"'([a-z_]+)'", defin)) == esperados
    # Y una fila con `entidad_no_decisora` vuelve a entrar tras la reversa.
    _config, ciclo, _hoja, decision = _siembra_tx3(conn)
    conn.execute(
        "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
        " VALUES (%s, %s, 'entidad_no_decisora')",
        (decision, ciclo),
    )
