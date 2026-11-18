"""ARREGLOS 03 P-B.2: comprobacion de evidencia (no entra a la bateria de `tests/`).

Las dos pruebas del plan (`tests/test_precio_corrida.py -k arreglos03_pb_`)
cubren `_persistir_uno` y el aviso del CLI. Esto comprueba los otros tres
sitios donde `ERRORES_DE_FILA` aisla la fila (`_decidir_lectura`,
`insertar_virtual` y `cambios.aplicar`) y que un error de infra sigue
abortando la corrida. Se corre a mano desde la raiz, con el PG desechable:

    PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs \
        -p no:cacheprovider docs/evidencia/arreglos-03/P-B/comprobar_sitios_pb2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests"))

from test_precio_corrida import (  # noqa: E402
    _cadena,
    _corre,
    _db,
    _price_obs,
    _RedFalsa,
    _skip_sin_pg,
)


def _rota_y_sana(conn, mode):
    """Dos publicaciones que quieren subir; la rota tiene el listing_id menor."""
    rota = _cadena(conn, sku="SKU-PB2-ROTA", ext="B0A5PB2ROT", mode=mode)
    sana = _cadena(conn, sku="SKU-PB2-SANA", ext="B0A5PB2SAN", mode=mode)
    _price_obs(conn, asin="B0A5PB2SAN")
    _price_obs(conn, asin="B0A5PB2ROT")
    red = _RedFalsa(skus={sana["evento"]: sana["sku"], rota["evento"]: rota["sku"]})
    return rota, sana, red


def _estado_corrida(conn):
    return conn.execute("SELECT estado FROM precio_corrida").fetchone()[0]


@_skip_sin_pg
def test_decidir_lectura_aisla_el_rechazo_de_la_fila(monkeypatch):
    """Un DataError al cotizar UNA publicacion la deja
    `no_evaluado(escenario_incoherente)`; la otra se decide y se aplica."""
    import app.precio.corrida as corrida_mod

    original = corrida_mod.cotizar_y_decidir
    with _db() as (conn, _dsn):
        rota, _sana, red = _rota_y_sana(conn, "live")

        def cotizar(conn_, entrada, **kw):
            if entrada.listing_id == rota["listing"]:
                raise psycopg.errors.NumericValueOutOfRange("numeric field overflow")
            return original(conn_, entrada, **kw)

        monkeypatch.setattr(corrida_mod, "cotizar_y_decidir", cotizar)
        res = _corre(conn, red)
        fila_rota = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (rota["listing"],),
        ).fetchone()
        assert tuple(fila_rota) == ("no_evaluado", "escenario_incoherente")
        assert (red.n_patch, res.escritas, _estado_corrida(conn)) == (1, 1, "cerrada")
        assert any("fase1: NumericValueOutOfRange" in e for e in res.errores)


@_skip_sin_pg
def test_insertar_virtual_aisla_el_rechazo_de_la_fila(monkeypatch):
    """En sombra, un CheckViolation al insertar el virtual de UNA publicacion
    no impide el virtual de la otra."""
    import app.precio.cambios as cambios_mod

    original = cambios_mod.insertar_virtual
    with _db() as (conn, _dsn):
        rota, sana, red = _rota_y_sana(conn, "shadow")

        def virtual(conn_, did, lid, *a, **kw):
            if lid == rota["listing"]:
                raise psycopg.errors.CheckViolation("precio_cambio: dato de UNA publicacion")
            return original(conn_, did, lid, *a, **kw)

        monkeypatch.setattr(cambios_mod, "insertar_virtual", virtual)
        res = _corre(conn, red)
        con_virtual = conn.execute(
            "SELECT listing_id FROM precio_cambio WHERE NOT aplicado ORDER BY id"
        ).fetchall()
        assert [f[0] for f in con_virtual] == [sana["listing"]]
        assert (red.n_patch, _estado_corrida(conn)) == (0, "cerrada")
        assert any("rechazo_de_base: CheckViolation" in e for e in res.errores)


@_skip_sin_pg
def test_aplicar_aisla_el_rechazo_de_la_fila(monkeypatch):
    """En vivo, un UniqueViolation al aplicar UNA publicacion no impide el
    PATCH de la otra."""
    import app.precio.cambios as cambios_mod

    original = cambios_mod.aplicar
    with _db() as (conn, _dsn):
        rota, _sana, red = _rota_y_sana(conn, "live")

        def aplicar(conn_, mercado, did, **kw):
            lid = conn_.execute(
                "SELECT listing_id FROM precio_decision WHERE id = %s", (did,)
            ).fetchone()[0]
            if lid == rota["listing"]:
                raise psycopg.errors.UniqueViolation("precio_cambio: llave de UNA publicacion")
            return original(conn_, mercado, did, **kw)

        monkeypatch.setattr(cambios_mod, "aplicar", aplicar)
        res = _corre(conn, red)
        assert (red.n_patch, res.escritas, _estado_corrida(conn)) == (1, 1, "cerrada")
        assert any("rechazo_de_base: UniqueViolation" in e for e in res.errores)


@_skip_sin_pg
def test_error_de_infra_sigue_abortando(monkeypatch):
    """Control: un OperationalError (infra) sigue tumbando la corrida."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        _rota, _sana, red = _rota_y_sana(conn, "live")

        def persistir(*a, **kw):
            raise psycopg.OperationalError("server closed the connection")

        monkeypatch.setattr(corrida_mod, "persistir_decision", persistir)
        with pytest.raises(psycopg.OperationalError):
            _corre(conn, red)
        assert (red.n_patch, _estado_corrida(conn)) == (0, "abierta")
