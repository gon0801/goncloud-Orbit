"""ARREGLOS 05 G: un termino reobservado no revierte todo el lote.

Hallazgo 13. El sello bitemporal `_sello_bitemporal` de
`app/cycle_serializacion.py` solo se aplicaba en `_pendiente_termino` dentro de
`if corte is not None`, es decir, solo a la rama `negative`. El `harvest` sin
corte copiaba `resultado.data_observed_at` (= `termino.observed_at_max`) crudo;
una reobservacion posterior de un termino maduro dejaba `data_observed_at` en el
futuro, `decision_dato_no_del_futuro` rechazaba la fila y el `executemany` de la
TX3 revertia TODO el lote mixto, incluidas las decisiones `pause` y `negative`
que ya eran validas.

El contrato (DISENO F-G parte G) pide el sello comun tambien al harvest,
conservando el `observed_at` real en inputs y metricas append-only y clampeando
solo `data_observed_at <= decided_at`, sin mover ventanas ni relajar la madurez.

Estas regresiones ejercitan el ciclo completo sobre PostgreSQL 16 desechable:
lecturas, TX2, TX3 y replay. El modo del motor es shadow (fixture maestra): las
decisiones se crean y se sellan, pero no se envia nada a Amazon.
"""

import datetime as dt

import psycopg
import pytest
from test_cycle import (
    DECIDED_AT,
    _corre,
    _db_temporal,
    _decisions_de,
    _run,
    _siembra_maestra,
    _termino,
)
from test_schema import _postgres_obligatorio_ausente

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_FECHA_MADURA = dt.date(2026, 7, 15)
# observed_at posterior al reloj de la decision: la reobservacion del termino
# maduro que hoy tumba el lote.
_OBS_POSTERIOR = DECIDED_AT + dt.timedelta(seconds=1)
_CORTE_MADURO = DECIDED_AT.date() - dt.timedelta(days=10)


def _reobserva_buena_yarda(conn, ag) -> None:
    """Backfill append-only: fila NUEVA del termino harvestable con observed_at
    posterior al reloj y el MISMO dato maduro (no reescribe la original)."""
    run = _run(conn)
    _termino(
        conn,
        run,
        ag,
        "buena yarda",
        _FECHA_MADURA,
        _OBS_POSTERIOR,
        cost="3.33",
        ad_revenue="40",
        clicks=2,
        orders=1,
    )


def _mundo(conn, *, reobservado=False) -> dict:
    ids = _siembra_maestra(conn, settings={"ads_bid_politica_amazon_us": "niveles_v3"})
    if reobservado:
        _reobserva_buena_yarda(conn, ids["ag"])
    return ids


def _corre_lote(conn):
    """Corre el ciclo; contra el padre el lote mixto aborta por el CHECK. El
    fallo se convierte en asercion para que el rojo nombre la causa del producto
    y no parezca un error de fixture."""
    try:
        return _corre(conn)
    except psycopg.errors.CheckViolation as exc:
        pytest.fail(
            "el lote mixto aborto por "
            f"{exc.diag.constraint_name or exc.sqlstate}: una reobservacion revirtio"
            " decisiones validas"
        )


def _por_kind(filas) -> dict:
    return {fila[1]: fila for fila in filas}


# ---------------------------------------------------------------------------
# Camino 1: el lote mixto se confirma y conserva sus decisiones validas
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_g_lote_mixto_reobservado_se_confirma():
    with _db_temporal("orbit_g05_mixto") as (conn, _extra):
        _mundo(conn, reobservado=True)
        res = _corre_lote(conn)
        assert res.status == "done", res.notes
        filas = _decisions_de(conn, res.cycle_id)
        kinds = {fila[1] for fila in filas}
        assert {"pause", "negative", "harvest"} <= kinds, kinds
        # El lote no se revierte: todas las decisiones validas persistieron.
        assert len(filas) == res.decisions_count
        env = conn.execute(
            "SELECT status, decisions_count, decision_committed_at"
            " FROM optimizer_cycle WHERE id = %s",
            (res.cycle_id,),
        ).fetchone()
        assert env[0] == "done"
        assert env[1] == len(filas)
        assert env[2] is not None, "el lote confirmado lleva su sello de TX3"


# ---------------------------------------------------------------------------
# Camino 2: el sello clampa data_observed_at y conserva el insumo real
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_g_sello_clampa_y_conserva_el_insumo():
    with _db_temporal("orbit_g05_sello") as (conn, _extra):
        ids = _mundo(conn, reobservado=True)
        res = _corre_lote(conn)
        filas = _decisions_de(conn, res.cycle_id)
        por = _por_kind(filas)
        assert "harvest" in por, por.keys()
        harv = por["harvest"]

        # Toda decision respeta el contrato bitemporal del sello.
        for fila in filas:
            assert fila[8] is not None
            assert fila[8] <= DECIDED_AT, (fila[1], fila[8])

        # El insumo real permanece: la fecha de observacion posterior queda
        # intacta en inputs (no se rejuvenece ni se reescribe).
        obs_insumo = harv[9]["termino"]["observed_at_max"]
        assert dt.datetime.fromisoformat(obs_insumo) == _OBS_POSTERIOR
        assert dt.datetime.fromisoformat(obs_insumo) > DECIDED_AT

        # El sello del harvest queda clampeado al reloj de la decision.
        assert harv[8] == DECIDED_AT

        # Append-only: la fila nueva existe y las originales siguen ahi.
        observados = {
            fila[0]
            for fila in conn.execute(
                "SELECT observed_at FROM search_term_observation"
                " WHERE ad_entity_id = %s AND lower(search_term) = 'buena yarda'",
                (ids["ag"],),
            ).fetchall()
        }
        assert _OBS_POSTERIOR in observados
        originales = {
            dt.datetime(2026, 7, 13, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 14, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 15, 1, tzinfo=dt.UTC),
        }
        assert originales <= observados, "la reobservacion no reescribe la original"


# ---------------------------------------------------------------------------
# Camino 3: la ventana madura no se mueve y el trigger sigue vigente
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_g_ventana_madura_intacta_y_trigger_vigente():
    with _db_temporal("orbit_g05_madurez") as (conn, _extra):
        ids = _mundo(conn, reobservado=True)
        res = _corre_lote(conn)
        for fila in _decisions_de(conn, res.cycle_id):
            if fila[1] in ("pause", "negative", "harvest"):
                assert fila[7] <= _CORTE_MADURO, (fila[1], fila[7])
        # El trigger de madurez (regla 6) no se relajo: un corte con dia
        # inmaduro se rechaza en la base y no persiste.
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end,"
                " search_term, inputs)"
                " VALUES (%s, %s, 'negative', %s, %s, %s, %s, %s, 'inmaduro', '{}'::jsonb)",
                (
                    res.cycle_id,
                    ids["ag"],
                    DECIDED_AT,
                    ids["config_id"],
                    DECIDED_AT,
                    _CORTE_MADURO + dt.timedelta(days=1),
                    _CORTE_MADURO + dt.timedelta(days=8),
                ),
            )
        assert exc.value.sqlstate == "23514"
        inmaduros = conn.execute(
            "SELECT count(*) FROM decision WHERE search_term = 'inmaduro'"
        ).fetchone()[0]
        assert inmaduros == 0


# ---------------------------------------------------------------------------
# Camino 4: replay igual y las decisiones del lote se conservan
# ---------------------------------------------------------------------------


def _clave(fila) -> tuple:
    return (fila[1], fila[2], fila[3], fila[4], fila[5])


@_skip_db
def test_arreglos05_g_replay_igual_y_decisiones_conservadas():
    from app.optimizer import replay

    with _db_temporal("orbit_g05_control") as (conn, _extra):
        _mundo(conn)
        control = _corre_lote(conn)
        assert control.status == "done", control.notes
        control_filas = _decisions_de(conn, control.cycle_id)
        assert {"pause", "negative", "harvest"} <= {f[1] for f in control_filas}
        for fila in control_filas:
            assert replay.reproduce(fila[9]) == (fila[1], fila[4], fila[5])

    with _db_temporal("orbit_g05_replay") as (conn, _extra):
        _mundo(conn, reobservado=True)
        res = _corre_lote(conn)
        assert res.status == "done", res.notes
        filas = _decisions_de(conn, res.cycle_id)
        for fila in filas:
            assert replay.reproduce(fila[9]) == (fila[1], fila[4], fila[5])
        # El lote reobservado decide exactamente lo mismo que el control.
        assert {_clave(f) for f in filas} == {_clave(f) for f in control_filas}
