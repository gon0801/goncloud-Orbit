"""TEMPORADA 01 I: el punto de equilibrio con el costo de HOY de cada producto.

I.2: las tres vistas de margen (`v_target_margen_plataforma`, `v_margen_producto`
y `v_margen_familia`) agregan `cogs_vigente`, `cobertura_vigente` y
`margen_vigente_pct`, y la reversa restaura la definicion de 0086 (con sus
permisos y comentarios). I.3: el margen que gobierna es el vigente
(`goals.medicion_desde_fila` + `goals.resuelve_target_margen`).

Prueba de dinero (casos del diseno "Punto de equilibrio con el costo vigente"):
10 ventas maduras de 1,000 MXN con cargos de -300 y costo 500 hasta el dia D,
5 antes y 5 despues. Las guardas de dias de las vistas (60 en plataforma, 30 en
producto) quedan conservadas, asi que el caso de aritmetica (10 ventas) se
comprueba por las columnas crudas (cogs y cobertura en las dos vigencias) y el
fixture de vista repite la misma aritmetica con 70 dias para que los margenes
salgan vivos: mismo reparto 500/300, mismos porcentajes 30/40.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import itertools
import os
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from psycopg.rows import dict_row
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

RAIZ = Path(__file__).resolve().parents[1]


def _migracion(nombre: str) -> str:
    return (RAIZ / "migrations" / nombre).read_text(encoding="utf-8")


# La cadena de las vistas de margen, EN ORDEN, como en produccion: 0015 crea la
# vista de plataforma, 0016 la corrige, 0018 trae v_margen_producto, 0046 la
# tabla que 0048 altera, 0047 las familias que v_margen_familia consulta, 0048
# la vista familiar, 0086 manda los cargos por v_ledger_cargo y 0092 agrega el
# margen vigente (TEMPORADA 01 I).
_CADENA = tuple(
    _migracion(n)
    for n in (
        "0015_target_margen_plataforma.sql",
        "0016_target_margen_correcciones.sql",
        "0018_fabrica_campanas.sql",
        "0046_target_acos_ciclo.sql",
        "0047_familias.sql",
        "0048_margen_familia.sql",
        "0086_arreglos02_cargos.sql",
        "0092_temporada01_margen_vigente.sql",
    )
)
SQL92 = _migracion("0092_temporada01_margen_vigente.sql")
REVERSA92 = _migracion("0092_reversa_temporada01_margen_vigente.sql")

_VISTAS = ("v_target_margen_plataforma", "v_margen_producto", "v_margen_familia")
_VISTAS_NUEVAS = ("cogs_vigente", "cobertura_vigente", "margen_vigente_pct")

_DIA = dt.timedelta(days=1)

pytestmark = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_NUMERO = itertools.count()


@contextlib.contextmanager
def _db(prefijo: str, *, con_margen_vigente: bool = True):
    """DB temporal con 0001 + la cadena de las vistas de margen. Con
    `con_margen_vigente=False` se queda en la cadena de 0086 (la 0092 es la
    ultima de _CADENA): el estado previo al bloque I."""
    import psycopg
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = (
        f"{prefijo}_{socket.gethostname().lower().replace('-', '_')}"
        f"_{os.getpid()}_{next(_NUMERO)}"
    )
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("DROP DATABASE IF EXISTS {}").format(pgsql.Identifier(db)))
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        for migracion in (_CADENA if con_margen_vigente else _CADENA[:-1]):
            conn.execute(migracion)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _hoy(conn) -> dt.date:
    """El hoy UTC de la base (el mismo que fija el join de vigencia)."""
    return conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]


def _libro_fresco(conn) -> None:
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
        " ('accounting_ledger_events', now(), true)"
    )


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _producto(
    conn,
    sku: str,
    *,
    costo: str,
    desde: dt.date,
    hasta: dt.date | None,
    costo_moneda: str = "MXN",
) -> int:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, valid_to) VALUES (%s, %s, %s, true, %s, %s)",
        (pid, Decimal(costo), costo_moneda, desde, hasta),
    )
    return pid


def _venta(conn, run: int, pid: int, fecha: dt.date, monto: str, orden: str) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, order_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'sale', %s, %s, 1, %s, 'MXN', %s, %s)",
        (fecha, pid, Decimal(monto), orden, run),
    )


def _cargo(conn, run: int, fecha: dt.date, monto: str, orden: str) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
        " fee_type, order_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'fee', %s, %s, 'MXN', 'closing', %s, %s)",
        (fecha, Decimal(monto), orden, run),
    )


def _familia(conn, pid: int) -> int:
    fid = conn.execute(
        "INSERT INTO familia (platform, nombre, slug) VALUES ('amazon_mx', 'Funda', 'funda')"
        " RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO producto_familia (platform, product_id, familia_id)"
        " VALUES ('amazon_mx', %s, %s)",
        (pid, fid),
    )
    return fid


def _fila(conn, vista: str, columna: str = "platform", valor="amazon_mx") -> dict | None:
    conn.row_factory = dict_row
    try:
        return conn.execute(f"SELECT * FROM {vista} WHERE {columna} = %s", (valor,)).fetchone()
    finally:
        conn.row_factory = tuple


def _siembra(conn, *, dias: int, costo_vigente: str, costo_hoy: str | None, desde_hoy: int):
    """Ventas de 1,000 MXN con fee de -300, una por dia; costo del producto
    `costo_vigente` hasta `desde_hoy` dias antes de hoy y `costo_hoy` desde
    entonces (None = sin costo nuevo). Devuelve (product_id, familia_id)."""
    _libro_fresco(conn)
    run = _run(conn)
    hoy = _hoy(conn)
    frontera = hoy - _DIA * desde_hoy
    pid = _producto(conn, "SKU-I", costo=costo_vigente, desde=hoy - _DIA * 200, hasta=frontera)
    if costo_hoy is not None:
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from) VALUES (%s, %s, 'MXN', true, %s)",
            (pid, Decimal(costo_hoy), frontera),
        )
    mitad = dias // 2
    fechas = [hoy - _DIA * (desde_hoy + mitad - i) for i in range(mitad)]
    fechas += [hoy - _DIA * (desde_hoy - 1 - j) for j in range(mitad)]
    for i, fecha in enumerate(fechas):
        orden = f"o-{i}"
        _venta(conn, run, pid, fecha, "1000", orden)
        _cargo(conn, run, fecha, "-300", orden)
    fid = _familia(conn, pid)
    conn.commit()
    return pid, fid


def test_temporada01_i_equilibrio_con_costo_vigente():
    """Dinero (diseno, "Punto de equilibrio con el costo vigente"): con costo 500
    hasta el dia D y 300 desde D, el historico da 30 % y el vigente 40 %; el
    equilibrio y la meta derivada (fraccion 0.5) salen del vigente. La aritmetica
    exacta del diseno (10 ventas) se comprueba en las columnas crudas con las
    guardas de dias conservadas; el fixture de vista repite la misma aritmetica
    con 70 dias para que los margenes salgan vivos."""
    from app.optimizer import goals as g

    # --- Aritmetica del diseno: 10 ventas maduras, 5 antes y 5 despues de D.
    with _db("orbit_t01_i_arit") as conn:
        pid, _fid = _siembra(conn, dias=10, costo_vigente="500", costo_hoy="300", desde_hoy=40)
        fila = _fila(conn, "v_target_margen_plataforma")
        assert fila["venta_total"] == Decimal("10000")
        assert fila["venta_cubierta"] == Decimal("10000")
        # Historico: 5 x 500 + 5 x 300 = 4,000. Vigente: 10 x 300 = 3,000.
        assert fila["cogs"] == Decimal("4000")
        assert fila["cogs_vigente"] == Decimal("3000")
        assert fila["cobertura"] == Decimal("1")
        assert fila["cobertura_vigente"] == Decimal("1")
        # Guardas de dias conservadas: 10 dias no miden margen (60 en
        # plataforma, 30 en producto).
        assert fila["dias_con_venta"] == 10
        assert fila["margen_neto_pct"] is None
        assert fila["margen_vigente_pct"] is None
        prod = _fila(conn, "v_margen_producto", "product_id", pid)
        assert prod["margen_neto_pct"] is None
        assert prod["margen_vigente_pct"] is None

    # --- La vista con dias suficientes: 70 ventas con el mismo reparto 500/300.
    with _db("orbit_t01_i_baja") as conn:
        pid, fid = _siembra(conn, dias=70, costo_vigente="500", costo_hoy="300", desde_hoy=70)
        fila = _fila(conn, "v_target_margen_plataforma")
        # Historico (70,000 - 21,000 - 28,000) / 70,000 = 30 %; vigente
        # (70,000 - 21,000 - 21,000) / 70,000 = 40 %.
        assert fila["cogs"] == Decimal("28000")
        assert fila["cogs_vigente"] == Decimal("21000")
        assert fila["margen_neto_pct"] == Decimal("30")
        assert fila["margen_vigente_pct"] == Decimal("40")
        assert fila["cobertura"] == Decimal("1")
        assert fila["cobertura_vigente"] == Decimal("1")
        # El margen que gobierna es el vigente; el historico queda conservado.
        medicion = g.medicion_desde_fila(fila)
        assert medicion.margen_neto_pct == Decimal("40")
        assert medicion.margen_historico_pct == Decimal("30")
        assert medicion.cobertura == Decimal("1")
        res = g.resuelve_target_margen(
            medicion, Decimal("0.5"), g.hoy_de_ventana(fila["ventana_hasta"]), None
        )
        assert res.motivo is None
        assert res.derivado == Decimal("20")
        assert res.aplicado == Decimal("20")
        # Producto y familia miden lo mismo en las dos vigencias.
        prod = _fila(conn, "v_margen_producto", "product_id", pid)
        assert prod["margen_neto_pct"] == Decimal("30")
        assert prod["margen_vigente_pct"] == Decimal("40")
        fam = _fila(conn, "v_margen_familia", "familia_id", fid)
        assert fam["margen_neto_pct"] == Decimal("30")
        assert fam["margen_vigente_pct"] == Decimal("40")

    # --- El costo sube a 700: vigente 0 %, equilibrio 0 y la meta de la banda
    # (10) pierde dinero.
    with _db("orbit_t01_i_subida") as conn:
        _siembra(conn, dias=70, costo_vigente="500", costo_hoy="700", desde_hoy=70)
        fila = _fila(conn, "v_target_margen_plataforma")
        assert fila["margen_neto_pct"] == Decimal("10")
        assert fila["margen_vigente_pct"] == Decimal("0")
        medicion = g.medicion_desde_fila(fila)
        assert medicion.margen_neto_pct == Decimal("0")
        assert medicion.margen_historico_pct == Decimal("10")
        res = g.resuelve_target_margen(
            medicion, Decimal("0.5"), g.hoy_de_ventana(fila["ventana_hasta"]), None
        )
        assert res.motivo is None
        assert res.derivado == Decimal("0")
        assert res.aplicado == Decimal("10")

    # --- Costo de hoy abierto en USD con venta en MXN: la venta queda NO
    # cubierta (jamas se convierte) y el margen vigente es NULL; el historico
    # sigue midiendo con el costo de la fecha de venta (el costo de USD nace
    # HOY, para que el join historico no duplique lineas del producto).
    with _db("orbit_t01_i_usd") as conn:
        _libro_fresco(conn)
        run = _run(conn)
        hoy = _hoy(conn)
        pid = _producto(
            conn, "SKU-I", costo="500", desde=hoy - _DIA * 200, hasta=hoy, costo_moneda="MXN"
        )
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from) VALUES (%s, %s, 'USD', true, %s)",
            (pid, Decimal("300"), hoy),
        )
        for i in range(70):
            fecha = hoy - _DIA * (104 - i)
            orden = f"o-{i}"
            _venta(conn, run, pid, fecha, "1000", orden)
            _cargo(conn, run, fecha, "-300", orden)
        conn.commit()
        fila = _fila(conn, "v_target_margen_plataforma")
        assert fila["margen_neto_pct"] == Decimal("20")
        assert fila["cogs_vigente"] == Decimal("0")
        assert fila["cobertura_vigente"] is None
        assert fila["margen_vigente_pct"] is None

    # --- Producto sin costo abierto (el costo cerro y no hay otro): igual, la
    # venta no queda cubierta para hoy.
    with _db("orbit_t01_i_cerrado") as conn:
        _libro_fresco(conn)
        run = _run(conn)
        hoy = _hoy(conn)
        pid = _producto(
            conn, "SKU-I", costo="500", desde=hoy - _DIA * 200, hasta=hoy, costo_moneda="MXN"
        )
        for i in range(70):
            fecha = hoy - _DIA * (104 - i)
            orden = f"o-{i}"
            _venta(conn, run, pid, fecha, "1000", orden)
            _cargo(conn, run, fecha, "-300", orden)
        conn.commit()
        fila = _fila(conn, "v_target_margen_plataforma")
        assert fila["margen_neto_pct"] == Decimal("20")
        assert fila["cogs_vigente"] == Decimal("0")
        assert fila["margen_vigente_pct"] is None

    # --- Un costo publicado HOY nace abierto con inicio manana: no cuenta
    # todavia; el vigente sigue siendo el costo de 500 (20 %), no el de 300.
    # La publicacion cierra la vigencia vieja en manana (0001 prohibe solape
    # de vigencias por producto, exclusion gist): el costo de HOY es el de
    # 500 porque hoy < hoy+1; el de 300 toma el relevo manana.
    with _db("orbit_t01_i_manana") as conn:
        _libro_fresco(conn)
        run = _run(conn)
        hoy = _hoy(conn)
        pid = _producto(conn, "SKU-I", costo="500", desde=hoy - _DIA * 200, hasta=hoy + _DIA)
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from) VALUES (%s, %s, 'MXN', true, %s)",
            (pid, Decimal("300"), hoy + _DIA),
        )
        for i in range(70):
            fecha = hoy - _DIA * (104 - i)
            orden = f"o-{i}"
            _venta(conn, run, pid, fecha, "1000", orden)
            _cargo(conn, run, fecha, "-300", orden)
        conn.commit()
        fila = _fila(conn, "v_target_margen_plataforma")
        assert fila["margen_neto_pct"] == Decimal("20")
        assert fila["cogs_vigente"] == Decimal("35000")
        assert fila["margen_vigente_pct"] == Decimal("20")


def _huella_vistas(conn) -> dict:
    """Columnas + permisos + comentarios + definicion de las cuatro vistas."""
    vistas = (*_VISTAS, "v_economia_producto")
    columnas = {}
    for vista in vistas:
        filas = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            (vista,),
        ).fetchall()
        columnas[vista] = [(f[0], f[1]) for f in filas]
    permisos = conn.execute(
        "SELECT table_name, grantee, privilege_type FROM information_schema.role_table_grants"
        " WHERE table_schema = 'public'"
        " AND table_name IN ('v_target_margen_plataforma', 'v_margen_producto',"
        " 'v_margen_familia', 'v_economia_producto') ORDER BY 1, 2, 3"
    ).fetchall()
    comentarios = conn.execute(
        "SELECT c.relname, col_description(c.oid, 0) FROM pg_class c"
        " WHERE c.relnamespace = 'public'::regnamespace"
        " AND c.relname IN ('v_target_margen_plataforma', 'v_margen_producto',"
        " 'v_margen_familia', 'v_economia_producto') ORDER BY 1"
    ).fetchall()
    definiciones = {
        vista: conn.execute("SELECT pg_get_viewdef(%s::regclass, true)", (vista,)).fetchone()[0]
        for vista in vistas
    }
    return {
        "columnas": columnas,
        "permisos": permisos,
        "comentarios": comentarios,
        "definiciones": definiciones,
    }


def test_temporada01_i_reversa_restaura_las_vistas_de_0086():
    """I.2 (regla 7): la reversa devuelve las tres vistas al texto exacto de
    0086 — columnas, permisos, comentarios y definicion, incluida
    v_economia_producto que depende de v_margen_producto."""
    with _db("orbit_t01_i_reversa", con_margen_vigente=False) as conn:
        antes = _huella_vistas(conn)
        conn.execute(SQL92)
        columnas_92 = _huella_vistas(conn)["columnas"]
        for vista in _VISTAS:
            nombres = {nombre for nombre, _ in columnas_92[vista]}
            assert nombres.issuperset(_VISTAS_NUEVAS), vista
            # Las columnas nuevas van AL FINAL, sin quitar las historicas.
            assert [n for n, _ in columnas_92[vista]][-3:] == list(_VISTAS_NUEVAS)
        assert "margen_vigente_pct" not in {n for n, _ in columnas_92["v_economia_producto"]}
        conn.execute(REVERSA92)
        despues = _huella_vistas(conn)
        assert despues == antes
        # Y siguen consultables (la cadena de vistas quedo intacta).
        conn.execute("SELECT count(*) FROM v_economia_producto").fetchone()


def test_temporada01_i_guardas_del_margen_vigente():
    """Las guardas del margen vigente son las mismas del historico: fee sin
    clasificar deja los dos margenes en NULL y el resolver abstiene."""
    from app.optimizer import goals as g

    with _db("orbit_t01_i_guardas") as conn:
        _siembra(conn, dias=70, costo_vigente="500", costo_hoy="300", desde_hoy=70)
        run = _run(conn)
        hoy = _hoy(conn)
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
            " fee_type, ingest_run_id)"
            " VALUES ('amazon_mx', 'fee', %s, -1, 'MXN', NULL, %s)",
            (hoy - _DIA * 40, run),
        )
        conn.commit()
        fila = _fila(conn, "v_target_margen_plataforma")
        assert fila["fees_sin_tipo"] == 1
        assert fila["margen_neto_pct"] is None
        assert fila["margen_vigente_pct"] is None
        medicion = g.medicion_desde_fila(fila)
        res = g.resuelve_target_margen(
            medicion, Decimal("0.5"), g.hoy_de_ventana(fila["ventana_hasta"]), None
        )
        assert res.motivo == "sin_margen"
