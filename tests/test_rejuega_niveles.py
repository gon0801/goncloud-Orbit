"""Rejuego de niveles_v3 sobre ciclos pasados (BIDS 02 M.4).

`cumple()` es verdadero con los cuatro criterios y falso con cualquiera
roto. `rejuega` arma cada caso con el target congelado y el bid del dia,
no lee nada observado despues de cada ciclo y lista las vendedoras que
recortaria. El CLI acepta `--platform` y `--ciclos`, imprime el informe
(ultima linea `cumple: true|false`) y sale 0|1.

Son INTEGRACION: base temporal aislada con las migraciones en orden, sin
la 0011 y sin las reversas (incluye la 0060: `v_hoja_activa` y
`v_cambio_bid`), como tests/test_lecturas_caso.py. La base de
ORBIT_TEST_DSN no se toca: solo presta el servidor para crear la temporal.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

import tools.rejuega_niveles as rejuega_mod
from app.optimizer.politica import Mover
from tools.rejuega_niveles import InformeRejuego, main, rejuega, texto_informe

ROOT = Path(__file__).resolve().parents[1]

_CICLO = dt.datetime(2026, 10, 9, 8, 40, tzinfo=dt.UTC)
_D = dt.date(2026, 10, 9)
_ANCLA = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)
_OBSERVADO = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)
_OBSERVADO_PRECIO = _OBSERVADO + dt.timedelta(hours=1)
_VENTANA_DESDE = dt.date(2026, 8, 15)
_VENTANA_HASTA = dt.date(2026, 9, 5)

_MONEDA = {"amazon_mx": "MXN", "amazon_us": "USD"}

FALTA_PG = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def _db_rejuego():
    """DB temporal aislada con todas las migraciones en orden, sin la 0011
    y sin las reversas (igual que `tests/test_lecturas_caso.py`)."""
    dsn = _test_dsn()
    db = f"orbit_bids02_rej_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for ruta in sorted((ROOT / "migrations").glob("*.sql")):
            if "0011_" in ruta.name or "_reversa_" in ruta.name:
                continue
            conn.execute(ruta.read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _entidad(conn, platform, kind, external, parent=None, *, match=None, texto=None):
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, match, texto),
    ).fetchone()[0]


def _estado(conn, ad_entity_id, status="ENABLED", *, targeting=None, bid=None, moneda=None):
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, targeting_type, current_bid,"
        " bid_currency, synced_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (ad_entity_id, status, targeting, bid, moneda, _ANCLA),
    )


def _triple(conn, platform="amazon_mx", *, campana="ENABLED", bid=Decimal("10"), tag=""):
    c = _entidad(conn, platform, "campaign", f"c-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-{tag}", c)
    h = _entidad(conn, platform, "keyword", f"h-{tag}", g, match="EXACT", texto=f"kw-{tag}")
    _estado(conn, c, campana, targeting="MANUAL")
    _estado(conn, g, "ENABLED")
    _estado(conn, h, "ENABLED", bid=bid, moneda=_MONEDA[platform])
    return c, g, h


def _config(conn, settings=None):
    return conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s) RETURNING id",
        (Json(settings if settings is not None else {}),),
    ).fetchone()[0]


def _ciclo(
    conn, mode="live", platform="amazon_mx", *, started_at=_CICLO, notes=None, status="done"
):
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, started_at, finished_at, status,"
        " notes) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (mode, platform, started_at, started_at + dt.timedelta(minutes=5), status, notes),
    ).fetchone()[0]


def _target(
    conn, cycle_id, hoja, target=Decimal("20"), procedencia="setting_plataforma", decidido_el=_CICLO
):
    conn.execute(
        "INSERT INTO target_acos_ciclo (cycle_id, ad_entity_id, decided_at,"
        " target_acos_pct, procedencia) VALUES (%s, %s, %s, %s, %s)",
        (cycle_id, hoja, decidido_el, target, procedencia),
    )


def _goal_platform(
    conn,
    platform="amazon_mx",
    *,
    piso=Decimal("2"),
    techo=Decimal("50"),
    target=None,
    enabled=True,
    mode="live",
):
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, enabled, mode) VALUES ('platform', %s, %s, %s, %s,"
        " %s, %s, %s)",
        (platform, target, piso, techo, _MONEDA[platform], enabled, mode),
    )


def _ingest(conn):
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('prueba') RETURNING id"
    ).fetchone()[0]


def _metrica(
    conn,
    ingest,
    hoja,
    fecha,
    observado,
    *,
    clics=0,
    pedidos=0,
    venta=0,
    gasto=0,
    impresiones=0,
    moneda="MXN",
):
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            hoja,
            fecha,
            observado,
            moneda,
            Decimal(gasto) if gasto is not None else None,
            Decimal(venta) if venta is not None else None,
            impresiones,
            clics,
            pedidos,
            ingest,
        ),
    )


def _relleno(conn, ingest, hoja, desde, hasta, observado, *, salta=frozenset()):
    """Una fila por dia para marcar ingesta de la plataforma (la hoja de
    relleno es inactiva: sus filas no entran a ningun roll-up)."""
    dia = desde
    while dia <= hasta:
        if dia not in salta:
            _metrica(conn, ingest, hoja, dia, observado)
        dia += dt.timedelta(days=1)


def _recorte(conn, *, hoja, config, ejecutor, old, new, confirmado, motivo=None, moneda="MXN"):
    """Decision de bid aplicada por un ciclo live. Devuelve el decision_id."""
    decision = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
        (
            _ciclo(conn, "live"),
            hoja,
            config,
            confirmado,
            confirmado,
            _VENTANA_DESDE,
            _VENTANA_HASTA,
            old,
            new,
            moneda,
            Json({"motivo": motivo} if motivo else {}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, applied_cycle_id, confirmed_at,"
        " platform_ack, verify_ok) VALUES (%s, %s, %s, %s, true)",
        (decision, ejecutor, confirmado, Json({"readback": True})),
    )
    return decision


def _informe(**cambios):
    """InformeRejuego base que cumple; cada prueba rompe lo suyo."""
    base = {
        "plataforma": "amazon_mx",
        "desde": dt.date(2026, 9, 10),
        "hasta": dt.date(2026, 10, 9),
        "por_motivo": {"gasto_sin_venta": 3},
        "recortes_del_motor_viejo": 5,
        "recortes_que_se_repetirian": 2,
        "hojas_recortadas": 2,
        "parte_del_gasto_recortada_pct": Decimal("4.5"),
        "regresos": 0,
        "recortes_sobre_danadas": 0,
        "deterministas_pct": Decimal("100"),
        "invariantes_rotos": 0,
        "cobertura_pct": Decimal("100"),
        "vendedoras_que_recortaria": ((7, "pierde_dinero"),),
    }
    base.update(cambios)
    return InformeRejuego(**base)


def test_cumple_verdadero_con_cuatro_criterios_en_el_limite():
    assert _informe(
        deterministas_pct=Decimal("100"),
        invariantes_rotos=0,
        cobertura_pct=Decimal("95"),
        recortes_sobre_danadas=0,
    ).cumple()


def test_cumple_falso_si_una_fila_no_se_reproduce():
    assert not _informe(deterministas_pct=Decimal("99")).cumple()


def test_cumple_falso_con_recorte_de_regla_prohibida():
    assert not _informe(invariantes_rotos=1).cumple()


def test_cumple_falso_con_cobertura_94():
    assert not _informe(cobertura_pct=Decimal("94")).cumple()


def test_cumple_falso_con_recorte_sobre_danada():
    assert not _informe(recortes_sobre_danadas=1).cumple()


def test_informe_dice_cuantos_ciclos_aportaron():
    """R03-chico-3: el header no dice solo "30 ciclos": dice cuantos
    aportaron casos (los otros no tenian target congelado)."""
    texto = texto_informe(_informe(ciclos=30, ciclos_que_aportaron=14, casos=100))
    assert texto.splitlines()[0].endswith("30 ciclos live, 14 aportaron, 100 casos")


def _grupo_con_hojas(conn, platform, n, tag):
    c = _entidad(conn, platform, "campaign", f"c-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-{tag}", c)
    _estado(conn, c, "ENABLED", targeting="MANUAL")
    _estado(conn, g, "ENABLED")
    hojas = []
    for i in range(n):
        h = _entidad(
            conn,
            platform,
            "keyword",
            f"h-{tag}-{i}",
            g,
            match="EXACT",
            texto=f"kw-{tag}-{i}",
        )
        _estado(conn, h, "ENABLED", bid=Decimal("10"), moneda=_MONEDA[platform])
        hojas.append(h)
    return c, g, hojas


def _relleno_plataforma(conn, ingest, platform, tag, *, desde, hasta, observado=_OBSERVADO):
    """Hoja inactiva con una fila diaria: marca ingesta sin entrar a roll-ups."""
    c = _entidad(conn, platform, "campaign", f"c-rel-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-rel-{tag}", c)
    h = _entidad(
        conn,
        platform,
        "keyword",
        f"h-rel-{tag}",
        g,
        match="EXACT",
        texto=f"rel-{tag}",
    )
    _estado(conn, c, "ENABLED", targeting="MANUAL")
    _estado(conn, g, "ENABLED")
    _estado(conn, h, "PAUSED", bid=Decimal("10"), moneda=_MONEDA[platform])
    _relleno(conn, ingest, h, desde, hasta, observado, salta=frozenset())
    return h


def _diarias(conn, ingest, hoja, desde, hasta, *, gorda, dia_gorda, moneda="MXN"):
    """Una fila por dia; el dia gordo concentra las sumas."""
    dia = desde
    while dia <= hasta:
        if dia == dia_gorda:
            _metrica(conn, ingest, hoja, dia, _OBSERVADO, moneda=moneda, **gorda)
        else:
            _metrica(conn, ingest, hoja, dia, _OBSERVADO, moneda=moneda)
        dia += dt.timedelta(days=1)


_REC_MX = (dt.date(2026, 8, 20), dt.date(2026, 9, 29))  # tramo reciente de D=10-09
_GORDA = dt.date(2026, 9, 29)
_PRECIO = dt.date(2026, 9, 6)
_NOTES_SETTING = json.dumps({"target": {"procedencia": "setting_plataforma"}})


@FALTA_PG
def test_vendedoras_lista_hojas_con_pedidos_que_recortan():
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "v",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (a, sangra) = _grupo_con_hojas(conn, "amazon_mx", 2, "v")
        _diarias(
            conn,
            ingest,
            a,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 100, "pedidos": 1, "venta": 100, "gasto": 500, "impresiones": 1000},
        )
        _metrica(conn, ingest, a, _PRECIO, _OBSERVADO_PRECIO, clics=50, gasto=200)
        _diarias(
            conn,
            ingest,
            sangra,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 10, "pedidos": 3, "venta": 300, "gasto": 10, "impresiones": 1000},
        )
        _metrica(conn, ingest, sangra, _PRECIO, _OBSERVADO_PRECIO, clics=10, gasto=10)
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, a)
        _target(conn, ciclo, sangra)
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.vendedoras_que_recortaria == ((a, "grupo_sangra_vendedora"),)
    assert informe.por_motivo["grupo_sangra_vendedora"] == 1
    assert informe.hojas_recortadas == 1
    assert informe.recortes_sobre_danadas == 0
    assert informe.invariantes_rotos == 0
    assert informe.deterministas_pct == Decimal("100")
    assert informe.cobertura_pct == Decimal("100")
    assert informe.recortes_del_motor_viejo == 0
    assert informe.cumple()


@FALTA_PG
def test_ciclo_sin_target_congelado_no_aporta():
    """R03-chico-3: de dos ciclos live, el que no trae targets congelados
    arma cero casos y no cuenta como aportado."""
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(conn, ingest, "amazon_mx", "ap", desde=dt.date(2026, 4, 12), hasta=_D)
        _, _, (a, b) = _grupo_con_hojas(conn, "amazon_mx", 2, "ap")
        _diarias(
            conn,
            ingest,
            a,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 100, "pedidos": 1, "venta": 100, "gasto": 500, "impresiones": 1000},
        )
        _diarias(
            conn,
            ingest,
            b,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 100, "pedidos": 1, "venta": 100, "gasto": 500, "impresiones": 1000},
        )
        con_target = _ciclo(conn, notes=_NOTES_SETTING)
        sin_target = _ciclo(conn, notes=_NOTES_SETTING)
        assert sin_target != con_target
        _target(conn, con_target, a)
        _target(conn, con_target, b)
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.ciclos == 2
    assert informe.casos == 2
    assert informe.ciclos_que_aportaron == 1


@FALTA_PG
def test_r03b6_recorte_que_viola_cuenta_invariante_y_tumba_cumple(monkeypatch):
    """R03-B6: un recorte con motivo fuera de MOTIVOS_RECORTE pasa por el
    conteo real (`if _viola`) y suma invariantes_rotos (cumple falso).
    El mutante `if False` da 0 y muere aqui."""

    def _mala(_caso):
        return Mover(Decimal("-0.12"), Decimal("8.8"), "motivo_falso", "hoja")

    monkeypatch.setattr(rejuega_mod, "decide", _mala)
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "m9",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (a, sangra) = _grupo_con_hojas(conn, "amazon_mx", 2, "m9")
        _diarias(
            conn,
            ingest,
            a,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 100, "pedidos": 1, "venta": 100, "gasto": 500, "impresiones": 1000},
        )
        _diarias(
            conn,
            ingest,
            sangra,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 10, "pedidos": 3, "venta": 300, "gasto": 10, "impresiones": 1000},
        )
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, a)
        _target(conn, ciclo, sangra)
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.invariantes_rotos == 2
    assert not informe.cumple()


@FALTA_PG
def test_no_lee_nada_observado_despues_de_started_at():
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "f",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (h,) = _grupo_con_hojas(conn, "amazon_mx", 1, "f")
        _diarias(
            conn,
            ingest,
            h,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 50, "pedidos": 0, "venta": 0, "gasto": 800, "impresiones": 1000},
        )
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, h)
        antes = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
        assert antes.por_motivo == {"gasto_sin_venta_doble": 1}
        _metrica(
            conn,
            ingest,
            h,
            dt.date(2026, 10, 4),
            _CICLO + dt.timedelta(hours=4),
            clics=5,
            pedidos=1,
            venta=50,
            gasto=10,
            impresiones=100,
        )
        despues = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert despues == antes


@FALTA_PG
def test_caso_trae_target_congelado_y_bid_del_dia():
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn, {"ads_target_acos_pct_amazon_mx": 500})
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "t",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (a, b, sangra) = _grupo_con_hojas(conn, "amazon_mx", 3, "t")
        _diarias(
            conn,
            ingest,
            a,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 100, "pedidos": 1, "venta": 100, "gasto": 500, "impresiones": 1000},
        )
        _metrica(conn, ingest, a, _PRECIO, _OBSERVADO_PRECIO, clics=50, gasto=200)
        # B concentra sus sumas el 09-20: con la racha posterior al ciclo no
        # sale danada (sus 14 dias previos a la racha traen 0 clics) y el
        # juicio R4 es el mismo (las sumas no cambian). Las impresiones del
        # 09-29 la sacan de inerte (el watermark incluye al relleno).
        _diarias(
            conn,
            ingest,
            b,
            *_REC_MX,
            dia_gorda=dt.date(2026, 9, 20),
            gorda={"clics": 100, "pedidos": 1, "venta": 100, "gasto": 500, "impresiones": 1000},
        )
        _metrica(conn, ingest, b, _PRECIO, _OBSERVADO_PRECIO, clics=50, gasto=200)
        _metrica(
            conn,
            ingest,
            b,
            _GORDA,
            _OBSERVADO_PRECIO,
            impresiones=100,
        )
        _diarias(
            conn,
            ingest,
            sangra,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 10, "pedidos": 3, "venta": 300, "gasto": 10, "impresiones": 1000},
        )
        _metrica(conn, ingest, sangra, _PRECIO, _OBSERVADO_PRECIO, clics=10, gasto=10)
        config = _config(conn)
        ejecutor = _ciclo(conn)
        conn.execute(
            "UPDATE ad_entity_state SET current_bid = %s WHERE ad_entity_id = %s",
            (Decimal("0.03"), b),
        )
        _recorte(
            conn,
            hoja=b,
            config=config,
            ejecutor=ejecutor,
            old=Decimal("10"),
            new=Decimal("3"),
            confirmado=_CICLO + dt.timedelta(days=1),
        )
        _recorte(
            conn,
            hoja=b,
            config=config,
            ejecutor=ejecutor,
            old=Decimal("3"),
            new=Decimal("0.03"),
            confirmado=_CICLO + dt.timedelta(days=2),
        )
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        for hoja in (a, b, sangra):
            _target(conn, ciclo, hoja)
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.por_motivo["grupo_sangra_vendedora"] == 2
    assert informe.vendedoras_que_recortaria == (
        (a, "grupo_sangra_vendedora"),
        (b, "grupo_sangra_vendedora"),
    )
    assert informe.cumple()


def _dsn_de(conn):
    base = _test_dsn().rsplit("/", 1)[0]
    return f"{base}/{conn.info.dbname}"


_TABLAS_FOTO = (
    "decision",
    "decision_application",
    "apply_attempt",
    "apply_queue",
    "target_acos_ciclo",
    "optimizer_cycle",
    "config_version",
    "ads_optimizer_goal",
    "ads_metric_observation",
    "ingest_run",
    "ad_entity",
    "ad_entity_state",
)


def _foto(conn):
    foto = {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] for tabla in _TABLAS_FOTO
    }
    foto["ciclos"] = conn.execute(
        "SELECT id, status, notes FROM optimizer_cycle ORDER BY id"
    ).fetchall()
    foto["decisiones"] = conn.execute(
        "SELECT id, kind, new_value FROM decision ORDER BY id"
    ).fetchall()
    return foto


@FALTA_PG
def test_ciclos_30_cubre_recientes_y_no_escribe(monkeypatch, capsys):
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        visto = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "m",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
            observado=visto,
        )
        _, _, (h,) = _grupo_con_hojas(conn, "amazon_mx", 1, "m")
        dia = dt.date(2026, 7, 1)
        while dia <= dt.date(2026, 10, 8):
            _metrica(
                conn,
                ingest,
                h,
                dia,
                visto,
                clics=2,
                gasto=25,
                impresiones=100,
            )
            dia += dt.timedelta(days=1)
        for n in range(31):
            inicio = _CICLO - dt.timedelta(days=30 - n)
            ciclo = _ciclo(conn, started_at=inicio, notes=_NOTES_SETTING)
            _target(conn, ciclo, h, decidido_el=inicio)
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_de(conn))
        foto_antes = _foto(conn)
        salida = main(["--platform", "amazon_mx", "--ciclos", "30"])
        texto = capsys.readouterr().out
        foto_despues = _foto(conn)
    assert salida == 0
    assert "2026-09-10..2026-10-09" in texto
    assert "gasto_sin_venta_doble: 30" in texto
    assert texto.rstrip().splitlines()[-1] == "cumple: true"
    assert foto_despues == foto_antes


@FALTA_PG
def test_main_sale_1_cuando_no_cumple(monkeypatch, capsys):
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "e",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (h,) = _grupo_con_hojas(conn, "amazon_mx", 1, "e")
        _diarias(
            conn,
            ingest,
            h,
            *_REC_MX,
            dia_gorda=_GORDA,
            gorda={"clics": 50, "pedidos": 0, "venta": 0, "gasto": 800, "impresiones": 1000},
        )
        _metrica(conn, ingest, h, dt.date(2026, 9, 1), _OBSERVADO_PRECIO, pedidos=None)
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, h)
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_de(conn))
        salida = main(["--platform", "amazon_mx", "--ciclos", "30"])
        texto = capsys.readouterr().out
    assert salida == 1
    assert texto.rstrip().splitlines()[-1] == "cumple: false"
    assert "cobertura_pct: 0" in texto


@FALTA_PG
def test_recorte_sobre_danada_cuenta():
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "d",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (h,) = _grupo_con_hojas(conn, "amazon_mx", 1, "d")
        config = _config(conn)
        ejecutor = _ciclo(conn)
        conn.execute(
            "UPDATE ad_entity_state SET current_bid = %s WHERE ad_entity_id = %s",
            (Decimal("8"), h),
        )
        _recorte(
            conn,
            hoja=h,
            config=config,
            ejecutor=ejecutor,
            old=Decimal("10"),
            new=Decimal("8"),
            confirmado=dt.datetime(2026, 9, 19, 8, 40, tzinfo=dt.UTC),
        )
        _metrica(
            conn,
            ingest,
            h,
            dt.date(2026, 7, 1),
            _OBSERVADO,
            clics=10,
            pedidos=1,
            venta=200,
            gasto=50,
            impresiones=500,
        )
        dia = dt.date(2026, 7, 11)
        while dia <= dt.date(2026, 8, 19):
            _metrica(conn, ingest, h, dia, _OBSERVADO, clics=1, gasto=20, impresiones=10)
            dia += dt.timedelta(days=1)
        dia = dt.date(2026, 8, 20)
        while dia <= dt.date(2026, 9, 4):
            _metrica(conn, ingest, h, dia, _OBSERVADO)
            dia += dt.timedelta(days=1)
        for n in range(13):
            _metrica(
                conn,
                ingest,
                h,
                dt.date(2026, 9, 5) + dt.timedelta(days=n),
                _OBSERVADO,
                clics=7,
                impresiones=100,
            )
        _metrica(
            conn,
            ingest,
            h,
            dt.date(2026, 9, 18),
            _OBSERVADO,
            clics=9,
            impresiones=100,
        )
        _metrica(conn, ingest, h, dt.date(2026, 9, 19), _OBSERVADO)
        for n, clics in enumerate((6, 6, 6, 7)):
            _metrica(
                conn,
                ingest,
                h,
                dt.date(2026, 9, 20) + dt.timedelta(days=n),
                _OBSERVADO,
                clics=clics,
                impresiones=100,
            )
        dia = dt.date(2026, 9, 24)
        while dia <= dt.date(2026, 10, 6):
            _metrica(conn, ingest, h, dia, _OBSERVADO, impresiones=100)
            dia += dt.timedelta(days=1)
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, h)
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.por_motivo == {"gasto_sin_venta_doble": 1}
    assert informe.recortes_sobre_danadas == 1
    assert informe.vendedoras_que_recortaria == ()
    assert not informe.cumple()


def _caso_r9(hoja_id):
    """Caso R9-doble congelable: decide da Mover(-25 %) a 7.5."""
    tramo_gordo = {
        "clics": 50,
        "pedidos": 0,
        "venta": "0",
        "gasto": "800",
        "impresiones": 1000,
    }
    tramo_cero = {
        "clics": 0,
        "pedidos": 0,
        "venta": "0",
        "gasto": "0",
        "impresiones": 0,
    }
    return {
        "plataforma": "amazon_mx",
        "hoja_id": hoja_id,
        "ad_group_id": None,
        "bid": {"valor": "10", "moneda": "MXN", "piso": "2", "techo": "50"},
        "economia": {
            "plataforma": {
                "moneda": "MXN",
                "equilibrio_acos_pct": None,
                "gasto_para_concluir": "350",
                "confianza_recorte": "0.8",
                "confianza_subida": "0.7",
            },
            "target_acos_pct": "20",
        },
        "propia": {
            "reciente": tramo_gordo,
            "antiguo": tramo_cero,
            "esquema": "dos_tramos_v1",
        },
        "pedidos_inmaduros": 0,
        "grupo": None,
        "cuenta": None,
        "precio": {"gasto": "0", "clics": 0},
        "trayectoria": {"cambios": [], "efecto": None},
        "pausa": {
            "cortes": None,
            "umbral_clics": 100,
            "gasto_minimo": "500",
            "expected_clicks": None,
            "politica_economica": None,
        },
        "ventana_desde": "2026-07-11",
        "ventana_hasta": "2026-09-29",
        "observado_al": None,
    }


def _decision_guardada(conn, ciclo, hoja, config, *, nuevo, caso):
    conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, old_value,"
        " new_value, value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s)",
        (
            ciclo,
            hoja,
            config,
            _CICLO,
            _OBSERVADO,
            _VENTANA_DESDE,
            _VENTANA_HASTA,
            Decimal("10"),
            nuevo,
            "MXN",
            Json({"motor": "bid", "politica": "niveles_v3", "caso": caso}),
        ),
    )


@FALTA_PG
def test_casos_guardados_se_reproducen_con_reproduce():
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "g",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (primera, segunda) = _grupo_con_hojas(conn, "amazon_mx", 2, "g")
        for hoja in (primera, segunda):
            _diarias(
                conn,
                ingest,
                hoja,
                *_REC_MX,
                dia_gorda=_GORDA,
                gorda={"clics": 50, "pedidos": 0, "venta": 0, "gasto": 800, "impresiones": 1000},
            )
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, primera)
        _target(conn, ciclo, segunda)
        _decision_guardada(
            conn,
            ciclo,
            primera,
            config,
            nuevo=Decimal("7.5"),
            caso=_caso_r9(primera),
        )
        _decision_guardada(
            conn,
            ciclo,
            segunda,
            config,
            nuevo=Decimal("7.0"),
            caso=_caso_r9(segunda),
        )
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.deterministas_pct == Decimal("75")
    assert not informe.cumple()


@FALTA_PG
def test_hoja_con_veto_vigente_no_es_elegible():
    with _db_rejuego() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        _goal_platform(conn)
        _relleno_plataforma(
            conn,
            ingest,
            "amazon_mx",
            "w",
            desde=dt.date(2026, 4, 12),
            hasta=_D,
        )
        _, _, (libre, vetada) = _grupo_con_hojas(conn, "amazon_mx", 2, "w")
        for hoja in (libre, vetada):
            _diarias(
                conn,
                ingest,
                hoja,
                *_REC_MX,
                dia_gorda=_GORDA,
                gorda={"clics": 50, "pedidos": 0, "venta": 0, "gasto": 800, "impresiones": 1000},
            )
        ciclo = _ciclo(conn, notes=_NOTES_SETTING)
        _target(conn, ciclo, libre)
        _target(conn, ciclo, vetada)
        pausa = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
            " decided_at, data_observed_at, window_start, window_end, inputs)"
            " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                ciclo,
                vetada,
                config,
                _CICLO,
                _OBSERVADO,
                _VENTANA_DESDE,
                _VENTANA_HASTA,
                Json({}),
            ),
        ).fetchone()[0]
        veto = conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo,"
            " estado, vence_el, request_payload) VALUES ('amazon_mx', %s, 'pause',"
            " %s, 'live', 'pending_veto', %s, %s) RETURNING id",
            (
                vetada,
                pausa,
                _CICLO + dt.timedelta(days=2),
                Json({"motivo": "prueba"}),
            ),
        ).fetchone()[0]
        conn.execute(
            "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
            " vetoed_by = 'dueno', vence_el = %s WHERE id = %s",
            (_CICLO + dt.timedelta(days=11), veto),
        )
        informe = rejuega(conn, plataforma="amazon_mx", desde=_D, hasta=_D)
    assert informe.por_motivo == {"gasto_sin_venta_doble": 1}
    assert informe.cobertura_pct == Decimal("100")
