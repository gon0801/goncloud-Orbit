"""Reproducciones de la revision de ingesta (solo lectura del codigo de app/).

Cada prueba FALLA por la asercion que describe la falla. No toca la red ni
la base: son pruebas puras sobre los planificadores y el armado del rango.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

from app.ads import diario
from app.ads.reports import SEARCH_TERMS_CFG, _planea_filas_terminos
from app.meli.ads_source import leer_origen

# ---------------------------------------------------------------------------
# F3: la fusion de search terms publica un subtotal parcial como completo
# (productos ya lo corrigio con el "veneno adhesivo"; terminos no).
# ---------------------------------------------------------------------------

_HOY = dt.date(2026, 11, 12)
_DIA = dt.date(2026, 11, 1)


def _fila(**metricas):
    base = {"date": _DIA.isoformat(), "adGroupId": 111, "searchTerm": "arras boda"}
    base.update(metricas)
    return base


def _plan(filas):
    plan, skips = _planea_filas_terminos(
        SEARCH_TERMS_CFG,
        filas,
        hoy=_HOY,
        fecha_ini=_DIA - dt.timedelta(days=30),
        fecha_fin=_HOY - dt.timedelta(days=1),
    )
    return plan, skips


def test_terminos_fila_invalida_no_envenena_la_clave():
    """Dos aportes al mismo (ad group, termino, dia): uno trae purchases7d
    ilegible (sus 2 pedidos son desconocidos) y el otro 0 pedidos. Productos
    descarta la clave entera (subtotal parcial); terminos publica orders=0
    como dato completo -- el insumo exacto de un NEGATIVE_EXACT erroneo."""
    filas = [
        _fila(clicks=12, cost=9.5, purchases7d="2?", sales7d=40.0),
        _fila(clicks=30, cost=21.0, purchases7d=0, sales7d=0.0),
    ]
    plan, _ = _plan(filas)
    publicadas = [(p.search_term, p.clicks, p.orders) for p in plan]
    assert publicadas == [], f"subtotal parcial publicado como completo: {publicadas}"


def test_terminos_par_envenenado_se_recrea_con_la_tercera_fila():
    """Par complementario (cada fila trae lo que la otra no) deja la clave
    100% vacia y se borra; una TERCERA fila de la misma clave la recrea con
    solo sus valores (orders=0, 30 clics) aunque la segunda traia 2 pedidos."""
    filas = [
        _fila(clicks=5, cost=1.0, purchases7d=None, sales7d=None),
        _fila(clicks=None, cost=None, purchases7d=2, sales7d=40.0),
        _fila(clicks=30, cost=20.0, purchases7d=0, sales7d=0.0),
    ]
    plan, _ = _plan(filas)
    publicadas = [(p.search_term, p.clicks, p.orders) for p in plan]
    assert publicadas == [], f"clave envenenada recreada con un subtotal: {publicadas}"


# ---------------------------------------------------------------------------
# F2: MeLi -- la fila del dia EN CURSO entra a ads_metric_observation.
# ---------------------------------------------------------------------------

_DDL_ORIGEN = """
CREATE TABLE meli_ads_campaigns_daily (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT DEFAULT '',
    clicks INTEGER DEFAULT 0, impressions INTEGER DEFAULT 0, spend REAL DEFAULT 0,
    ad_sales REAL DEFAULT 0, acos REAL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (date, campaign_id)
);
"""


def test_meli_acepta_el_dia_en_curso(tmp_path):
    """El cron de las 08:15 UTC (02:15 en Mexico) copia la fila de HOY con
    ~2 h de gasto. Regla 6: el dia en curso se descarta; aqui entra."""
    hoy = dt.datetime.now(dt.UTC).date()
    ayer = hoy - dt.timedelta(days=1)
    as_of = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    ruta = tmp_path / "snap.db"
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_ORIGEN)
    con.executemany(
        "INSERT INTO meli_ads_campaigns_daily"
        " (date, campaign_id, campaign_name, clicks, impressions, spend, ad_sales, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (ayer.isoformat(), "C1", "ALFA", 230, 9000, 834.0, 2100.0, as_of),
            (hoy.isoformat(), "C1", "ALFA", 4, 150, 22.97, 0.0, as_of),
        ],
    )
    con.commit()
    con.close()
    origen = leer_origen(ruta)
    fechas = sorted({f.metric_date for f in origen.filas})
    assert hoy not in fechas, f"el dia en curso ({hoy}) entra como dato: {fechas}"


# ---------------------------------------------------------------------------
# F1: Amazon US -- en horario de invierno, a las 07:10 UTC el "D-1" que pide
# la ingesta sigue en curso en el marketplace (America/Los_Angeles).
# ---------------------------------------------------------------------------


class _ConnMuda:
    def commit(self):
        pass

    def close(self):
        pass


def test_diario_pide_el_dia_en_curso_de_us_en_invierno(monkeypatch):
    rangos = []

    def _descarga_falsa(conn, client, grupos, rango, **kw):
        rangos.append(rango)
        return {}

    monkeypatch.setattr(diario, "descarga", _descarga_falsa)
    ahora = dt.datetime(2026, 11, 12, 7, 10, tzinfo=dt.UTC)  # primer horario del cron
    salida = diario.main(
        [], conn=_ConnMuda(), client=object(), enviar=lambda _t: True, ahora=ahora
    )
    assert salida == 0
    (_, fecha_fin), = rangos
    local_us = ahora.astimezone(ZoneInfo("America/Los_Angeles"))
    ultimo_dia_cerrado_us = local_us.date() - dt.timedelta(days=1)
    assert fecha_fin <= ultimo_dia_cerrado_us, (
        f"pide hasta {fecha_fin}, pero en US son las {local_us:%H:%M} del {local_us.date()}:"
        f" ese dia sigue en curso (ultimo cerrado: {ultimo_dia_cerrado_us})"
    )
