"""Pantallas de Jev (JEV ADS 02, S.5): fila Senal en cortes + gasto sin venta.

Puro contrato, sin plantillas: `banda_de_proporcion` y los `como_dict` son
puros; `vista_de_dict`/`relevancia_de_texto` decodifican la fila guardada;
`de_propuestas`/`gasto_sin_venta` son SOLO SELECT sobre la conexion de
lectura (fakes aqui; Postgres real al final).
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from test_jev_catalogo import _grupo
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.jev_lectura import (
    ClaveBusqueda,
    Gasto,
    PropuestaEnVeto,
    leer,
    relevancia_de_texto,
    vista_de_dict,
)
from app.jev_vista import banda_de_proporcion

RAIZ = Path(__file__).resolve().parents[1]
ORDEN_S5 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0049_jev_ads.sql",
    "0050_jev_revision_created_at.sql",
    "0051_ads_acta_listado.sql",
    "0052_jev_senales.sql",
)
SQL_S5 = "\n".join(
    (RAIZ / "migrations" / nombre).read_text(encoding="utf-8") for nombre in ORDEN_S5
)

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

AHORA = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)
VENTANA = (date(2026, 8, 3), date(2026, 9, 1))


def _fila_base(**cambios):
    fila = {
        "senal_id": uuid.uuid4(),
        "plataforma": "amazon_mx",
        "ad_group_id": 7,
        "termino": "zapato rojo",
        "lectura": "relevante_sin_venta",
        "motivos_lectura": [],
        "relevancia": "corresponde",
        "motivos_jev": [],
        "productos_ok": [11],
        "evaluados": 3,
        "miembros": 5,
        "regla_version": 1,
        "roster_probado": True,
        "roster_prueba": {"probado": True, "ingest_run_id": 9},
        "ventana_inicio": VENTANA[0],
        "ventana_fin": VENTANA[1],
        "datos_hasta": AHORA,
        "moneda": "MXN",
        "clics": 10,
        "gasto": Decimal("12.50"),
        "ordenes": 0,
        "otros_que_venden": [],
        "otros_sin_dato": 0,
        "historial": {
            "desde": "2026-07-01",
            "hasta": "2026-09-01",
            "clics": 40,
            "ordenes_conocidas": 0,
            "dias_sin_dato": 2,
        },
        "valida_hasta": AHORA + timedelta(hours=36),
        "created_at": AHORA,
        "vigente": True,
    }
    fila.update(cambios)
    return fila


# --- banda: solo presentacion -------------------------------------------------


@pytest.mark.parametrize(
    ("satisfacen", "evaluados", "esperada"),
    [
        (0, 0, "sin_dato"),
        (0, 5, "ninguno"),
        (3, 20, "pocos"),  # 15% exacto todavia es pocos
        (4, 20, "una_parte"),
        (18, 20, "una_parte"),  # 90% aun no es todos
        (19, 20, "todos"),  # 95% exacto ya es todos
        (20, 20, "todos"),
    ],
)
def test_banda_de_proporcion_tabla(satisfacen, evaluados, esperada):
    assert banda_de_proporcion(satisfacen, evaluados) == esperada


# --- decodificacion de la fila guardada ---------------------------------------


def test_vista_de_dict_decodifica_la_fila():
    vista = vista_de_dict(_fila_base())
    assert vista.clave == ClaveBusqueda("amazon_mx", 7, "zapato rojo")
    assert vista.lectura == "relevante_sin_venta"
    assert vista.economia.aqui == Gasto(VENTANA[0], VENTANA[1], 10, Decimal("12.50"), 0)
    assert vista.economia.otros_que_venden == ()
    assert vista.economia.otros_sin_venta == 0
    assert vista.economia.historial.ordenes_conocidas == 0
    assert vista.satisfacen == 1  # len(productos_ok), no evaluados
    assert vista.motivos_roster == frozenset()
    assert vista.vigente is True


def test_vista_de_dict_sin_ventana_deja_aqui_en_none():
    vista = vista_de_dict(
        _fila_base(
            lectura="sin_lectura",
            motivos_lectura=["sin_observaciones"],
            relevancia="no_evaluada",
            motivos_jev=["sin_cupo"],
            productos_ok=[],
            evaluados=0,
            miembros=4,
            ventana_inicio=None,
            ventana_fin=None,
            clics=None,
            gasto=None,
            ordenes=None,
            historial=None,
            roster_probado=False,
            roster_prueba={"probado": False, "motivos": ["grupo_no_listado"]},
        )
    )
    assert vista.economia.aqui is None
    assert vista.economia.historial is None
    assert vista.motivos == frozenset({"sin_observaciones"})
    assert vista.motivos_roster == frozenset({"grupo_no_listado"})


def test_vista_de_dict_acepta_json_serializado():
    fila = _fila_base(
        otros_que_venden=json.dumps(
            [
                {
                    "ad_group_id": 8,
                    "desde": "2026-08-03",
                    "hasta": "2026-09-01",
                    "clics": 5,
                    "gasto": "3.25",
                    "ordenes": 2,
                }
            ]
        ),
        historial=json.dumps(
            {
                "desde": "2026-07-01",
                "hasta": "2026-09-01",
                "clics": 40,
                "ordenes_conocidas": 0,
                "dias_sin_dato": 2,
            }
        ),
        roster_prueba=json.dumps({"probado": True}),
        gasto="12.50",
    )
    vista = vista_de_dict(fila)
    assert len(vista.economia.otros_que_venden) == 1
    assert vista.economia.otros_que_venden[0].en_ventana.ordenes == 2
    assert vista.economia.aqui.gasto == Decimal("12.50")


def test_vista_de_dict_falla_cerrado_ante_basura():
    with pytest.raises(ValueError):
        vista_de_dict(_fila_base(otros_que_venden=42))
    with pytest.raises(ValueError):
        vista_de_dict(_fila_base(gasto=object()))


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        ("corresponde", "HayCompatible"),
        ("ajena", "NingunoCompatible"),
        ("sin_veredicto", "Indeterminado"),
        ("no_evaluada", "NoEvaluada"),
    ],
)
def test_relevancia_de_texto_tabla(texto, tipo):
    relevancia = relevancia_de_texto(
        texto, productos_ok=(11,), evaluados=3, miembros=5, motivos_jev=("motivo_x",)
    )
    assert type(relevancia.conjunto).__name__ == tipo
    assert relevancia.satisfacen == 1
    assert relevancia.evaluados == 3


def test_relevancia_de_texto_ilegible_falla():
    with pytest.raises(ValueError):
        relevancia_de_texto(
            "corresponde_manana",
            productos_ok=(),
            evaluados=0,
            miembros=0,
            motivos_jev=(),
        )


def test_la_fila_guardada_se_relee_igual():
    """Toda fila sellada por el job se relee a su misma lectura: la pantalla
    nunca contradice al job (variantes por cada camino de `leer`)."""
    casos = [
        _fila_base(),  # relevante_sin_venta
        _fila_base(
            lectura="vendio_aqui",
            motivos_lectura=[],
            historial={
                "desde": "2026-07-01",
                "hasta": "2026-09-01",
                "clics": 40,
                "ordenes_conocidas": 2,
                "dias_sin_dato": 0,
            },
        ),
        _fila_base(
            lectura="vende_en_otro",
            otros_que_venden=[
                {
                    "ad_group_id": 8,
                    "desde": "2026-08-03",
                    "hasta": "2026-09-01",
                    "clics": 5,
                    "gasto": "3.25",
                    "ordenes": 2,
                }
            ],
        ),
        _fila_base(
            lectura="ajena",
            relevancia="ajena",
            productos_ok=[],
            evaluados=5,
            miembros=5,
        ),
        _fila_base(
            lectura="sin_lectura",
            motivos_lectura=["jev_sin_veredicto"],
            relevancia="sin_veredicto",
            motivos_jev=["juicio_insuficiente"],
            productos_ok=[],
            evaluados=2,
            miembros=5,
        ),
        _fila_base(
            lectura="sin_lectura",
            motivos_lectura=["jev_no_evaluada"],
            relevancia="no_evaluada",
            motivos_jev=["sin_cupo"],
            productos_ok=[],
            evaluados=0,
            miembros=5,
        ),
    ]
    for fila in casos:
        vista = vista_de_dict(fila)
        relevancia = relevancia_de_texto(
            vista.relevancia_texto,
            productos_ok=vista.productos_ok,
            evaluados=vista.evaluados,
            miembros=vista.miembros,
            motivos_jev=tuple(fila["motivos_jev"]),
        )
        assert leer(vista.economia, relevancia) == (vista.lectura, vista.motivos)


def test_otros_sin_venta_no_decide():
    """El conteo que la fila no guarda tampoco decidiria: `leer` lo ignora."""
    base = vista_de_dict(_fila_base()).economia
    relevancia = relevancia_de_texto(
        "corresponde", productos_ok=(11,), evaluados=3, miembros=5, motivos_jev=()
    )
    assert leer(base, relevancia) == leer(replace(base, otros_sin_venta=4), relevancia)


# --- contrato serializado ------------------------------------------------------


def test_como_dict_de_senal():
    vista = vista_de_dict(
        _fila_base(motivos_lectura=["jev_sin_veredicto", "dato_de_venta_faltante"])
    )
    contrato = vista.como_dict()
    assert contrato["senal_id"] == str(vista.senal_id)
    assert contrato["clave"] == {
        "plataforma": "amazon_mx",
        "ad_group_id": 7,
        "termino": "zapato rojo",
    }
    assert contrato["economia"]["aqui"]["gasto"] == "12.50"
    assert contrato["economia"]["otros_sin_venta"] == 0
    assert contrato["productos_ok"] == [11]
    assert contrato["motivos"] == ["dato_de_venta_faltante", "jev_sin_veredicto"]
    json.dumps(contrato)  # el contrato viaja como JSON


def test_como_dict_de_propuesta_con_nones():
    from app.jev_lectura import SenalPropuesta

    contrato = SenalPropuesta(None, None, True).como_dict()
    assert contrato == {"origen": None, "destino": None, "destino_ilegible": True}
    vista = vista_de_dict(_fila_base())
    contrato = SenalPropuesta(vista, None, False).como_dict()
    assert contrato["origen"]["lectura"] == "relevante_sin_venta"
    assert contrato["destino"] is None


def test_como_dict_de_pantalla_gasto():
    from app.jev_lectura import PantallaGasto

    vista = vista_de_dict(_fila_base())
    pantalla = PantallaGasto(
        "amazon_mx", AHORA, (vista,), {"relevante_sin_venta": (1, Decimal("12.50"))}
    )
    contrato = pantalla.como_dict()
    assert contrato["totales"] == {"relevante_sin_venta": {"busquedas": 1, "gasto": "12.50"}}
    assert len(contrato["filas"]) == 1
    vacia = PantallaGasto("amazon_us", None, (), {})
    assert vacia.como_dict()["calculado_el"] is None


# --- lecturas con conexion falsa ------------------------------------------------


class _CursorFalso:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConnFalsa:
    """Conexion de lectura falsa: respuestas en orden de llamada."""

    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        return _CursorFalso(self._respuestas.pop(0))


def _tupla(fila):
    from app.jev_salud import _COLUMNAS_SENAL

    return tuple(fila[columna] for columna in _COLUMNAS_SENAL)


def _propuesta(cola, decision, kind, origen, destino=None, ilegible=False):
    return PropuestaEnVeto(cola, decision, kind, "live", origen, destino, ilegible, AHORA)


def test_de_propuestas_resuelve_origen_y_destino():
    import app.jev_salud as pantallas

    clave_a = ClaveBusqueda("amazon_mx", 7, "zapato rojo")
    clave_b = ClaveBusqueda("amazon_mx", 7, "tenis azul")
    clave_c = ClaveBusqueda("amazon_mx", 9, "tenis azul")
    clave_d = ClaveBusqueda("amazon_mx", 7, "bota negra")
    propuestas = (
        _propuesta(10, 1, "negative", clave_a),
        _propuesta(11, 2, "harvest", clave_b, clave_c),
        _propuesta(12, 3, "harvest", clave_d, None, True),
    )
    fila_a = _fila_base(ad_group_id=7, termino="zapato rojo")
    fila_c = _fila_base(ad_group_id=9, termino="tenis azul")
    conn = _ConnFalsa([[_tupla(fila_a), _tupla(fila_c)]])
    original = pantallas._propuestas
    pantallas._propuestas = lambda _conn: (propuestas, ())
    try:
        resultado = pantallas.de_propuestas(conn, [1, 2, 3, 99])
    finally:
        pantallas._propuestas = original
    assert resultado[1].origen.clave.termino == "zapato rojo"
    assert resultado[1].destino is None
    assert resultado[1].destino_ilegible is False
    assert resultado[2].origen is None  # sin sello todavia
    assert resultado[2].destino.clave.termino == "tenis azul"
    assert resultado[3].origen is None
    assert resultado[3].destino is None
    assert resultado[3].destino_ilegible is True
    assert 99 not in resultado


def test_de_propuestas_sin_pedidas_no_toca_la_base():
    import app.jev_salud as pantallas

    conn = _ConnFalsa([])
    assert pantallas.de_propuestas(conn, []) == {}
    assert conn.consultas == []


def test_de_propuestas_una_decision_muestra_la_primera():
    import app.jev_salud as pantallas

    clave_a = ClaveBusqueda("amazon_mx", 7, "zapato rojo")
    clave_b = ClaveBusqueda("amazon_mx", 7, "tenis azul")
    propuestas = (
        _propuesta(10, 1, "negative", clave_a),
        _propuesta(11, 1, "negative", clave_b),
    )
    fila_b = _fila_base(ad_group_id=7, termino="tenis azul")
    conn = _ConnFalsa([[_tupla(fila_b)]])
    original = pantallas._propuestas
    pantallas._propuestas = lambda _conn: (propuestas, ())
    try:
        resultado = pantallas.de_propuestas(conn, [1])
    finally:
        pantallas._propuestas = original
    assert resultado[1].origen is None  # la primera (clave_a) no tiene sello
    assert resultado[1].destino is None


def test_gasto_sin_venta_ordena_y_totaliza():
    import app.jev_salud as pantallas

    fila_barata = _fila_base(termino="tenis azul", gasto=Decimal("5.00"))
    fila_cara = _fila_base(
        termino="bota negra",
        gasto=Decimal("20.00"),
        lectura="ajena",
        relevancia="ajena",
        productos_ok=[],
        evaluados=5,
        miembros=5,
    )
    conn = _ConnFalsa([[_tupla(fila_cara), _tupla(fila_barata)], [(AHORA,)]])
    pantalla = pantallas.gasto_sin_venta(conn, plataforma="amazon_mx")
    assert [v.clave.termino for v in pantalla.filas] == ["bota negra", "tenis azul"]
    assert pantalla.totales == {
        "relevante_sin_venta": (1, Decimal("5.00")),
        "ajena": (1, Decimal("20.00")),
    }
    assert pantalla.calculado_el == AHORA


def test_gasto_sin_venta_sin_corridas_deja_calculado_en_none():
    import app.jev_salud as pantallas

    conn = _ConnFalsa([[], [(None,)]])
    pantalla = pantallas.gasto_sin_venta(conn, plataforma="amazon_us")
    assert pantalla.filas == ()
    assert pantalla.totales == {}
    assert pantalla.calculado_el is None


# --- Postgres real ------------------------------------------------------------


@contextmanager
def db_s5():
    dsn = _test_dsn()
    db = f"orbit_jevs5_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL_S5)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _roster(conn, grupo, sha="r" * 64):
    conn.execute(
        "INSERT INTO jev_roster (sha256, plataforma, ad_group_id, miembros)"
        " VALUES (%s, 'amazon_mx', %s, '[]'::jsonb)",
        (sha, grupo),
    )
    return sha


def _lote(conn):
    lote = uuid.uuid4()
    conn.execute(
        "INSERT INTO jev_revision (solicitud, sujeto_tipo, censos, contrato)"
        " VALUES (%s, 'lote', '{}'::jsonb, '{}'::jsonb)",
        (lote,),
    )
    return lote


def _senal(conn, grupo, termino, lote, roster, **cambios):
    valores = {
        "gasto": Decimal("12.50"),
        "ordenes": 0,
        "lectura": "relevante_sin_venta",
        "relevancia": "corresponde",
        "valida_hasta": AHORA + timedelta(hours=36),
        "clics": 10,
    }
    valores.update(cambios)
    return conn.execute(
        "INSERT INTO jev_senal (id, lote_id, plataforma, ad_group_id, termino,"
        " termino_sha256, insumos_sha256, regla_version, roster_sha256,"
        " roster_probado, roster_prueba, contrato_sha256, relevancia, motivos_jev,"
        " productos_ok, evaluados, miembros, juicio_ids, ventana_inicio, ventana_fin,"
        " moneda, clics, gasto, ordenes, otros_que_venden, otros_sin_dato,"
        " historial, lectura, motivos_lectura, valida_hasta)"
        " VALUES (%s, %s, 'amazon_mx', %s, %s, %s, %s, 1, %s, true,"
        " '{\"probado\": true}'::jsonb, %s, %s, '{}', '{11}', 3, 5, '{}',"
        " '2026-08-03', '2026-09-01', 'MXN', %s, %s, %s, '[]'::jsonb, 0,"
        ' \'{"desde": "2026-07-01", "hasta": "2026-09-01", "clics": 40,'
        ' "ordenes_conocidas": 0, "dias_sin_dato": 2}\'::jsonb,'
        " %s, '{}', %s) RETURNING id",
        (
            uuid.uuid4(),
            lote,
            grupo,
            termino,
            f"ts-{termino}",
            f"ins-{termino}-{uuid.uuid4().hex[:8]}",
            roster,
            f"ct-{uuid.uuid4().hex[:8]}",
            valores["relevancia"],
            valores["clics"],
            valores["gasto"],
            valores["ordenes"],
            valores["lectura"],
            valores["valida_hasta"],
        ),
    ).fetchone()[0]


@_skip_db
def test_gasto_sin_venta_punta_a_punta():
    import app.jev_salud as pantallas

    with db_s5() as conn:
        grupo = _grupo(conn, "amazon_mx", ())
        roster = _roster(conn, grupo)
        lote = _lote(conn)
        _senal(conn, grupo, "tenis azul", lote, roster, gasto=Decimal("5.00"))
        _senal(conn, grupo, "bota negra", lote, roster, gasto=Decimal("20.00"))
        _senal(conn, grupo, "ya vendio", lote, roster, ordenes=2)  # excluida
        _senal(  # vencida: la vista la marca no vigente
            conn,
            grupo,
            "vencida",
            lote,
            roster,
            valida_hasta=AHORA - timedelta(hours=1),
        )
        pantalla = pantallas.gasto_sin_venta(conn, plataforma="amazon_mx")
        assert [v.clave.termino for v in pantalla.filas] == ["bota negra", "tenis azul"]
        assert pantalla.totales == {"relevante_sin_venta": (2, Decimal("25.00"))}
        assert pantalla.calculado_el is None
        conn.execute(
            "INSERT INTO jev_corrida (lote_id, evento, cierre, resumen, at)"
            " VALUES (%s, 'fin', 'completa', '{}'::jsonb, %s)",
            (lote, AHORA),
        )
        pantalla = pantallas.gasto_sin_venta(conn, plataforma="amazon_mx")
        assert pantalla.calculado_el == AHORA


@_skip_db
def test_vistas_de_claves_punta_a_punta():
    import app.jev_salud as pantallas

    with db_s5() as conn:
        grupo = _grupo(conn, "amazon_mx", ())
        roster = _roster(conn, grupo)
        lote = _lote(conn)
        _senal(conn, grupo, "zapato rojo", lote, roster)
        vistas = pantallas._vistas_de_claves(
            conn,
            {
                ClaveBusqueda("amazon_mx", grupo, "zapato rojo"),
                ClaveBusqueda("amazon_mx", grupo, "ausente"),
            },
        )
        assert list(vistas) == [ClaveBusqueda("amazon_mx", grupo, "zapato rojo")]
        assert vistas[ClaveBusqueda("amazon_mx", grupo, "zapato rojo")].vigente is True


# --- frase y titulo de lectura (S.5 J5b, presentacion pura) ------------------------


@pytest.mark.parametrize(
    "lectura,esperada",
    [
        ("vendio_aqui", "Aquí ya convirtió. Bloquear es el riesgo."),
        ("vende_en_otro", "Bloquear aquí junta el tráfico donde ya vende."),
        ("relevante_sin_venta", "Es del catálogo y no convierte."),
    ],
)
def test_frase_de_lectura_directa(lectura, esperada):
    from app.jev_vista import frase_de_lectura

    assert frase_de_lectura(lectura) == esperada


def test_frase_ajena_nombra_productos_y_fecha():
    from app.jev_vista import frase_de_lectura

    assert (
        frase_de_lectura("ajena", [], 5, "2026-09-01T12:00:00+00:00")
        == "No corresponde a ninguno de los 5 productos; lista comprobada con el"
        " listado de Amazon del 2026-09-01. Bloquear, y pronto."
    )


def test_frase_sin_lectura_muestra_el_motivo():
    from app.jev_vista import frase_de_lectura

    assert (
        frase_de_lectura("sin_lectura", ["dato_de_venta_faltante", "jev_no_evaluada"])
        == "Sin lectura: falta un dato de venta, Jev no la evaluó."
        " Un dato faltante no es cero."
    )


def test_titulo_de_lectura_desconocida_falla_cerrado():
    from app.jev_vista import titulo_de_lectura

    with pytest.raises(KeyError):
        titulo_de_lectura("lectura_futura")


def test_gasto_sin_venta_acepta_conexion_con_dict_row():
    """Nota J5-r1: el cableado puede traer la conexion con dict_row; la
    lectura del ultimo `fin` sale por nombre en ese caso."""
    import app.jev_salud as pantallas

    fila = _fila_base(termino="bota negra", gasto=Decimal("20.00"))
    conn = _ConnFalsa([[dict(fila)], [{"calculado": AHORA}]])
    pantalla = pantallas.gasto_sin_venta(conn, plataforma="amazon_mx")
    assert [v.clave.termino for v in pantalla.filas] == ["bota negra"]
    assert pantalla.calculado_el == AHORA
