# ruff: noqa
from __future__ import annotations

import datetime as dt
import os
import socket
import sqlite3
import tempfile
import threading
from pathlib import Path

import psycopg
from psycopg import sql

from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, D2, _FILAS, _snapshot
from test_schema import SQL, _test_dsn

PID = os.getpid()
HOST = socket.gethostname().lower()
TMP = Path(tempfile.mkdtemp(prefix=f"orbit-review-{PID}-"))
DSN = _test_dsn()


def dbname(case: str) -> str:
    return f"orbit_rev_{case}_{HOST}_{PID}"


def fresh(case: str):
    name = dbname(case)
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin.close()
    conn = psycopg.connect(DSN, dbname=name, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute(SQL)
    return name, conn


def drop(name: str, conn=None):
    if conn is not None:
        conn.close()
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()


def latest(conn, campaign="C1", day=D1):
    return conn.execute(
        "SELECT l.cost, e.name FROM v_metric_latest l JOIN ad_entity e ON e.id=l.ad_entity_id "
        "WHERE e.platform='meli' AND e.external_id=%s AND l.metric_date=%s",
        (campaign, day),
    ).fetchone()


def runs(conn):
    return conn.execute(
        "SELECT id, ok, rows_written, rows_skipped, skip_reason FROM ingest_run ORDER BY id"
    ).fetchall()


def case_mark_interleaving():
    name, c = fresh("mark")
    try:
        base = _snapshot(TMP / "mark-base.db", _FILAS, as_of="2026-10-29 08:00:00")
        ai.ingest_meli_ads(c, base)
        # La recopia alta solo contiene C1/D1; la correccion intermedia toca C2/D1.
        recopy = _snapshot(
            TMP / "mark-recopy.db",
            [(D1.isoformat(), "C1", "ALFA", 10, 100, 12.34, 0.0)],
            as_of="2026-10-29 12:00:00",
        )
        middle = _snapshot(
            TMP / "mark-middle.db",
            [(D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0)],
            as_of="2026-10-29 10:00:00",
        )
        stale = _snapshot(
            TMP / "mark-stale.db",
            [(D1.isoformat(), "C1", "VIEJO", 10, 100, 99.0, 0.0)],
            as_of="2026-10-29 11:00:00",
        )
        entered = threading.Event()
        release = threading.Event()
        errors = []

        class PausaAntesLock:
            def __init__(self, inner): self.inner = inner
            def execute(self, query, params=None):
                if query == ai._SQL_LOCK_MELI:
                    entered.set()
                    if not release.wait(10): raise RuntimeError("timeout pausa")
                return self.inner.execute(query, params) if params is not None else self.inner.execute(query)
            def __getattr__(self, attr): return getattr(self.inner, attr)

        def recopia_baja_id():
            try:
                with psycopg.connect(DSN, dbname=name, autocommit=True) as other:
                    other.execute("SET TIME ZONE 'UTC'")
                    ai.ingest_meli_ads(PausaAntesLock(other), recopy)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=recopia_baja_id)
        t.start()
        assert entered.wait(10)
        ai.ingest_meli_ads(c, middle)  # run id mayor, as_of 10, termina primero
        release.set()
        t.join(10)
        assert not t.is_alive() and not errors, errors
        before = c.execute(ai._SQL_MARCA, (ai.SOURCE,)).fetchone()[0]
        ai.ingest_meli_ads(c, stale)  # debio rechazarse contra la recopia as_of 12
        print("MARK_INTERLEAVING", {
            "marca_leida_tras_recopia_12": before,
            "stale_11_aceptado_costo_nombre": tuple(map(str, latest(c))),
            "runs": runs(c),
        })
    finally:
        drop(name, c)


class RelojFijo:
    def __init__(self, inner, instante):
        self.inner = inner
        self.instante = instante
    def execute(self, query, params=None):
        if query == "SELECT clock_timestamp()":
            class Result:
                def __init__(self, value): self.value = value
                def fetchone(self): return (self.value,)
            return Result(self.instante)
        return self.inner.execute(query, params) if params is not None else self.inner.execute(query)
    def __getattr__(self, attr): return getattr(self.inner, attr)


def case_clock_collision():
    name, c = fresh("clock")
    try:
        fixed = dt.datetime(2026, 10, 29, 13, 0, tzinfo=dt.UTC)
        w = RelojFijo(c, fixed)
        one = _snapshot(TMP / "clock-one.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)], as_of="2026-10-29 08:00:00")
        two = _snapshot(TMP / "clock-two.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)], as_of="2026-10-29 09:00:00")
        ai.ingest_meli_ads(w, one)
        result = ai.ingest_meli_ads(w, two)
        obs = c.execute("SELECT count(*), count(DISTINCT observed_at) FROM ads_metric_observation").fetchone()
        print("CLOCK_COLLISION", {"resultado_segunda_correccion": result, "latest": str(latest(c)[0]), "observaciones": obs, "ultimo_run": runs(c)[-1]})
    finally:
        drop(name, c)


def case_retention():
    name, c = fresh("retention")
    try:
        one = _snapshot(TMP / "ret-one.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)], as_of="2026-10-29 08:00:00")
        recopy = _snapshot(TMP / "ret-copy.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)], as_of="2026-10-29 12:00:00")
        stale = _snapshot(TMP / "ret-stale.db", [(D1.isoformat(), "C1", "VIEJO", 1, 2, 7.0, 8.0)], as_of="2026-10-29 11:00:00")
        ai.ingest_meli_ads(c, one)
        copied = ai.ingest_meli_ads(c, recopy)
        c.execute("DELETE FROM ingest_run WHERE id=%s", (copied["run_id"],))
        ai.ingest_meli_ads(c, stale)
        fk_error = None
        try:
            c.execute("DELETE FROM ingest_run WHERE rows_written > 0")
        except Exception as exc:
            fk_error = type(exc).__name__
        print("RETENTION", {"stale_11_tras_borrar_recopia_12": tuple(map(str, latest(c))), "delete_run_con_hechos": fk_error})
    finally:
        drop(name, c)


def case_legacy():
    name, c = fresh("legacy")
    try:
        run = c.execute("INSERT INTO ingest_run(source,ok,finished_at) VALUES('legacy',true,now()) RETURNING id").fetchone()[0]
        ent = c.execute("INSERT INTO ad_entity(platform,kind,external_id,name) VALUES('meli','campaign','C1','ALFA') RETURNING id").fetchone()[0]
        c.execute(
            "INSERT INTO ads_metric_observation(ad_entity_id,metric_date,observed_at,metric_currency,cost,source_report_id,ingest_run_id) "
            "VALUES(%s,%s,now(),'MXN',5,NULL,%s)", (ent, D1, run)
        )
        snap = _snapshot(TMP / "legacy.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)], as_of="2026-10-29 08:00:00")
        error = None
        try:
            ai.ingest_meli_ads(c, snap)
        except Exception as exc:
            error = str(exc)
        print("LEGACY", {"error": error, "latest": str(latest(c)[0]), "observaciones": c.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0]})
    finally:
        drop(name, c)


def case_future_clock():
    name, c = fresh("future")
    try:
        future = _snapshot(TMP / "future.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)], as_of="2099-01-01 00:00:00")
        current = _snapshot(TMP / "current.db", [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)], as_of="2026-10-29 08:00:00")
        ai.ingest_meli_ads(c, future)
        error = None
        try:
            ai.ingest_meli_ads(c, current)
        except Exception as exc:
            error = str(exc)
        print("FUTURE_CLOCK", {"latest": str(latest(c)[0]), "snapshot_actual_rechazado": error})
    finally:
        drop(name, c)


def case_midwrite_atomicity():
    name, c = fresh("atomic")
    try:
        snap = _snapshot(TMP / "atomic.db", [
            (D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0),
            (D1.isoformat(), "C2", "BETA", "9223372036854775808", 2, 5.0, 8.0),
        ], as_of="2026-10-29 08:00:00")
        error = None
        try:
            ai.ingest_meli_ads(c, snap)
        except Exception as exc:
            error = type(exc).__name__
        print("MIDWRITE_ATOMICITY", {"error": error, "entidades": c.execute("SELECT count(*) FROM ad_entity").fetchone()[0], "observaciones": c.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0], "run": runs(c)[-1]})
    finally:
        drop(name, c)


def case_matrix():
    name, c = fresh("matrix")
    try:
        empty = _snapshot(TMP / "matrix-empty.db", [], as_of="2026-10-29 08:00:00")
        empty_result = ai.ingest_meli_ads(c, empty)
        base = _snapshot(TMP / "matrix-base.db", _FILAS, as_of="2026-10-29 08:00:00")
        ai.ingest_meli_ads(c, base)
        count0 = c.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0]
        retry = ai.ingest_meli_ads(c, base)
        subset_same = _snapshot(TMP / "matrix-subset-same.db", [(D1.isoformat(), "C1", "OTRO", 10, 100, 12.34, 0.0)], as_of="2026-10-29 08:00:00")
        same = ai.ingest_meli_ads(c, subset_same)
        same_new = _snapshot(TMP / "matrix-same-new.db", [(D1.isoformat(), "NEW", "NUEVA", 1, 2, 3.0, 4.0)], as_of="2026-10-29 08:00:00")
        same_new_error = None
        try: ai.ingest_meli_ads(c, same_new)
        except Exception as exc: same_new_error = str(exc)
        correction_b = _snapshot(TMP / "matrix-b.db", [(D1.isoformat(), "C1", "ALFA B", 10, 100, 20.0, 0.0)], as_of="2026-10-29 09:00:00")
        ai.ingest_meli_ads(c, correction_b)
        correction_a = _snapshot(TMP / "matrix-a.db", [(D1.isoformat(), "C1", "ALFA A", 10, 100, 12.34, 0.0)], as_of="2026-10-29 10:00:00")
        ai.ingest_meli_ads(c, correction_a)
        new_campaign = _snapshot(TMP / "matrix-new.db", [(D1.isoformat(), "NEW", "NUEVA", 1, 2, 3.0, 4.0)], as_of="2026-10-29 11:00:00")
        ai.ingest_meli_ads(c, new_campaign)
        empty_after = ai.ingest_meli_ads(c, empty)
        c.execute("INSERT INTO ingest_run(source,ok,skip_reason) VALUES(%s,NULL,'as_of=4102444800')", (ai.SOURCE,))
        c.execute("INSERT INTO ingest_run(source,ok,finished_at,skip_reason) VALUES(%s,false,now(),'fallo')", (ai.SOURCE,))
        counts = c.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0]
        print("MATRIX", {
            "vacia_inicial": empty_result,
            "reintento": retry,
            "mismo_as_of_subset_nombre_conservado": (same, latest(c, "C1")[1]),
            "mismo_as_of_campana_nueva": same_new_error,
            "ABA_latest": str(latest(c, "C1")[0]),
            "campana_nueva_posterior": str(latest(c, "NEW")[0]),
            "vacia_posterior_conserva": (empty_after, counts),
            "historia_C1_D1": c.execute("SELECT count(*) FROM ads_metric_observation o JOIN ad_entity e ON e.id=o.ad_entity_id WHERE e.external_id='C1' AND o.metric_date=%s", (D1,)).fetchone()[0],
            "runs_abierto_fallido_ignorados_en_marca": c.execute(ai._SQL_MARCA, (ai.SOURCE,)).fetchone()[0],
        })
    finally:
        drop(name, c)


if __name__ == "__main__":
    print("TMP", TMP)
    case_matrix()
    case_midwrite_atomicity()
    case_mark_interleaving()
    case_clock_collision()
    case_retention()
    case_legacy()
    case_future_clock()
