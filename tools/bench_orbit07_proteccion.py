"""Banco reproducible de rendimiento de ORBIT 07 M1.

Construye una carga fija (2000 hojas, 18000 filas recientes, 200 episodios y
200 filas de cola) en una base PostgreSQL 16 desechable y mide la base
`f86ed7d` y el candidato sobre la MISMA copia, alternados por ronda. Cada
medicion arranca de la misma plantilla, asi que entradas, hojas, filas y
episodios son identicos para los dos roles.

Uso desde la raiz del repo candidato:

    $env:ORBIT_TEST_DSN = "postgresql://orbit:orbit@127.0.0.1:5433/postgres"
    uv run --frozen python tools/bench_orbit07_proteccion.py preparar `
        --plantilla orbit07_bench_tpl
    uv run --frozen python tools/bench_orbit07_proteccion.py comparar `
        --plantilla orbit07_bench_tpl --code-base <worktree f86ed7d> `
        --rondas 3 --salida docs/evidencia/orbit-07/m0/rendimiento-comparado.json

`medir` corre en un subproceso con un unico `app` en `sys.path`: el del rol.
Asi la base y el candidato nunca comparten imports. El DSN nunca se imprime.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CARGA = {
    "hojas": 2000,
    "filas_metricas_recientes": 18000,
    "episodios": 200,
    "filas_cola": 200,
}
PLATAFORMA = "amazon_us"
DECIDED_AT = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
RECIENTE_DESDE = DECIDED_AT.date() - dt.timedelta(days=9)
RECIENTE_HASTA = DECIDED_AT.date() - dt.timedelta(days=1)
ANCLA = RECIENTE_DESDE
VENCE = ANCLA + dt.timedelta(days=10)


def _dsn() -> str:
    valor = os.environ.get("ORBIT_TEST_DSN")
    if not valor:
        raise SystemExit("falta ORBIT_TEST_DSN (no se imprime ni se inventa)")
    return valor


def _conecta(dsn: str, dbname: str | None = None):
    import psycopg

    if dbname is None:
        return psycopg.connect(dsn, autocommit=True)
    return psycopg.connect(dsn, dbname=dbname, autocommit=True)


def _fechas(inicio: dt.date, fin: dt.date) -> list[dt.date]:
    dias: list[dt.date] = []
    fecha = inicio
    while fecha <= fin:
        dias.append(fecha)
        fecha += dt.timedelta(days=1)
    return dias


def _aplica_migraciones(conn) -> int:
    """Esquema completo: mismo orden que verify/Launch.md (sin 0011 ni reversas)."""
    aplicadas = 0
    for ruta in sorted((RAIZ / "migrations").glob("*.sql")):
        if "_reversa_" in ruta.name or ruta.name.startswith("0011_"):
            continue
        conn.execute(ruta.read_text(encoding="utf-8"))
        aplicadas += 1
    return aplicadas


class _CursorContador:
    def __init__(self, cursor, contador: _ConexionContador):
        self._cursor = cursor
        self._contador = contador

    def execute(self, *args, **kwargs):
        self._contador.sentencias += 1
        return self._cursor.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        self._contador.sentencias += 1
        return self._cursor.executemany(*args, **kwargs)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self._cursor.__exit__(*args)

    def __getattr__(self, nombre):
        return getattr(self._cursor, nombre)


class _ConexionContador:
    """Proxy de conexion que cuenta sentencias ejecutadas por el codigo medido."""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "sentencias", 0)

    def execute(self, *args, **kwargs):
        self.sentencias += 1
        return self._conn.execute(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return _CursorContador(self._conn.cursor(*args, **kwargs), self)

    def __getattr__(self, nombre):
        return getattr(self._conn, nombre)


def _siembra(conn, *, proteccion: bool = True, productos: int = 0) -> dict:
    from decimal import Decimal

    from psycopg.types.json import Json

    from app import goals_write
    from app.optimizer.caso import VERSION_PROTECCION_VENTA

    hojas = CARGA["hojas"]
    settings = {
        "ads_optimizer_mode": "shadow",
        "ads_bid_politica_amazon_us": "niveles_v3",
    }
    if productos:
        # ORBIT 07 M2: carga comparable del peldaño de producto (grupos
        # exclusivos con margen valido). El goal de plataforma sin target deja
        # entrar la meta individual; el goal de campana del bloque M1 conserva
        # su target 25 y no cambia.
        settings["ads_producto_meta_amazon_us"] = "live"
        settings["ads_target_fraccion_margen_amazon_us"] = "0.5"
    if proteccion:
        settings["ads_proteccion_venta"] = VERSION_PROTECCION_VENTA
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("bench-orbit07", Json(settings)),
    ).fetchone()[0]
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', '9001')"
        " RETURNING id",
        (PLATAFORMA,),
    ).fetchone()[0]
    goal = goals_write.crea_goal(
        conn,
        ad_entity_id=camp,
        target_acos_pct=Decimal("25"),
        bid_currency="USD",
        mode="live",
        harvest_campaign_id="9002",
        harvest_ad_group_id="9102",
        harvest_default_bid=Decimal("0.75"),
        created_at=DECIDED_AT,
    )
    goal_id = goal["id"]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s, 'ad_group', '9101', %s) RETURNING id",
        (PLATAFORMA, camp),
    ).fetchone()[0]
    hojas_ids = [
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
            " keyword_text) VALUES (%s, 'keyword', %s, %s, 'EXACT', %s) RETURNING id",
            (PLATAFORMA, f"kw-{i:05d}", ag, f"kw {i:05d}"),
        ).fetchone()[0]
        for i in range(hojas)
    ]
    sincronizado = DECIDED_AT - dt.timedelta(hours=4)
    estados = [(camp, None, None), (ag, None, None)]
    estados += [(hoja, "1.00", "USD") for hoja in hojas_ids]
    conn.cursor().executemany(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " synced_at) VALUES (%s, %s, %s, 'ENABLED', %s)",
        [(entidad, bid, moneda, sincronizado) for entidad, bid, moneda in estados],
    )
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('bench') RETURNING id").fetchone()[
        0
    ]
    filas = []
    for indice, hoja in enumerate(hojas_ids):
        for dia in _fechas(RECIENTE_DESDE, RECIENTE_HASTA):
            pedidos = 1 if indice < 200 and dia == ANCLA else 0
            filas.append(
                (
                    hoja,
                    dia,
                    dt.datetime(dia.year, dia.month, dia.day, 1, tzinfo=dt.UTC),
                    "USD",
                    "0.1000",
                    "1.0000" if pedidos else "0.1000",
                    10,
                    1,
                    pedidos,
                    run,
                )
            )
    assert len(filas) == CARGA["filas_metricas_recientes"]
    conn.cursor().executemany(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        filas,
    )
    episodios = [
        (
            PLATAFORMA,
            hojas_ids[indice],
            "proteccion_venta_v1",
            ANCLA,
            VENCE,
            DECIDED_AT,
            DECIDED_AT - dt.timedelta(hours=1),
        )
        for indice in range(400, 600)
    ]
    conn.cursor().executemany(
        "INSERT INTO proteccion_venta_reciente (platform, ad_entity_id, version, ancla,"
        " vence_el, abierta_el, observado_el) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        episodios,
    )
    ciclo_semilla = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', %s) RETURNING id",
        (PLATAFORMA,),
    ).fetchone()[0]
    decisiones = []
    for indice in range(600, 800):
        decisiones.append(
            conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end, inputs)"
                " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, '{}'::jsonb) RETURNING id",
                (
                    ciclo_semilla,
                    hojas_ids[indice],
                    DECIDED_AT - dt.timedelta(days=2),
                    config_id,
                    DECIDED_AT - dt.timedelta(days=12),
                    DECIDED_AT.date() - dt.timedelta(days=39),
                    DECIDED_AT.date() - dt.timedelta(days=12),
                ),
            ).fetchone()[0]
        )
    conn.cursor().executemany(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo,"
        " estado, vence_el, request_payload)"
        " VALUES (%s, %s, 'pause', %s, 'live', 'pending_veto', %s, '{}'::jsonb)",
        [
            (PLATAFORMA, hojas_ids[600 + i], decision_id, DECIDED_AT)
            for i, decision_id in enumerate(decisiones)
        ],
    )
    conn.execute(
        "UPDATE apply_queue SET estado = 'released', released_at = %s"
        " WHERE platform = %s AND decision_id = ANY(%s)",
        (DECIDED_AT - dt.timedelta(hours=3), PLATAFORMA, decisiones),
    )
    productos_sembrados = _siembra_productos(conn, productos) if productos else 0
    return {
        "config_id": config_id,
        "goal_id": goal_id,
        "hojas": hojas,
        "filas_metricas_recientes": len(filas),
        "episodios": len(episodios),
        "filas_cola": len(decisiones),
        "productos": productos_sembrados,
    }


def _siembra_productos(conn, productos: int) -> int:
    """ORBIT 07 M2: `productos` grupos exclusivos con margen 8 % (meta 4 %),
    cada uno con su campana y goal sin target (camino unico
    goals_write.crea_goal), keyword con metricas y product ad ENABLED."""

    from decimal import Decimal

    from app import goals_write

    sincronizado = DECIDED_AT - dt.timedelta(hours=4)
    hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    run_metricas = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('bench-productos') RETURNING id"
    ).fetchone()[0]
    run_ventas = conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok)"
        " VALUES ('accounting_ledger_events', now(), true) RETURNING id"
    ).fetchone()[0]
    filas_metricas = []
    for i in range(productos):
        camp = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES (%s, 'campaign', %s) RETURNING id",
            (PLATAFORMA, f"pc-{i:05d}"),
        ).fetchone()[0]
        goals_write.crea_goal(
            conn,
            ad_entity_id=camp,
            target_acos_pct=None,
            bid_currency="USD",
            mode="live",
            harvest_campaign_id=f"pch-{i:05d}",
            harvest_ad_group_id=f"pgh-{i:05d}",
            harvest_default_bid=Decimal("0.75"),
            created_at=DECIDED_AT,
        )
        ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
            (PLATAFORMA, f"pg-{i:05d}", camp),
        ).fetchone()[0]
        kw = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
            " keyword_text) VALUES (%s, 'keyword', %s, %s, 'EXACT', %s) RETURNING id",
            (PLATAFORMA, f"pk-{i:05d}", ag, f"pk {i:05d}"),
        ).fetchone()[0]
        for entidad, bid in ((camp, None), (ag, None), (kw, "1.00")):
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency,"
                " status, synced_at) VALUES (%s, %s, %s, 'ENABLED', %s)",
                (entidad, bid, "USD" if bid is not None else None, sincronizado),
            )
        for dia in _fechas(dt.date(2026, 7, 3), dt.date(2026, 8, 12)):
            filas_metricas.append(
                (
                    kw,
                    dia,
                    dt.datetime(dia.year, dia.month, dia.day, 1, tzinfo=dt.UTC),
                    "USD",
                    "1.0000",
                    "0.0000",
                    10,
                    10,
                    0,
                    run_metricas,
                )
            )
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
            (f"BENCH-SKU-{i:05d}", f"Producto {i:05d}"),
        ).fetchone()[0]
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pid, PLATAFORMA, f"BENCH-ASIN-{i:05d}", f"BENCH-SS-{i:05d}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, 82, 'USD', true, %s, %s)",
            (pid, hoy - dt.timedelta(days=80), run_ventas),
        )
        for n in range(40):
            fecha = hoy - dt.timedelta(days=60 - n)
            orden = f"b-{i:05d}-{n}"
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity, amount, amount_currency, ingest_run_id)"
                " VALUES (%s, 'sale', %s, %s, %s, 1, 100, 'USD', %s)",
                (PLATAFORMA, fecha, orden, pid, run_ventas),
            )
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
                " amount_currency, fee_type, ingest_run_id)"
                " VALUES (%s, 'fee', %s, %s, -10, 'USD', 'referral', %s)",
                (PLATAFORMA, fecha, orden, run_ventas),
            )
        pa = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES (%s, 'product_ad', %s, %s, %s) RETURNING id",
            (PLATAFORMA, f"PA-B-{i:05d}", ag, lid),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', %s)",
            (pa, sincronizado),
        )
    conn.cursor().executemany(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        filas_metricas,
    )
    return productos


def _huella(conn) -> dict:
    consulta = {
        "hojas": "SELECT count(*) FROM ad_entity WHERE platform = %s AND kind = 'keyword'",
        "filas_metricas_recientes": (
            "SELECT count(*) FROM v_metric_latest v JOIN ad_entity k ON k.id = v.ad_entity_id"
            " WHERE k.platform = %s AND k.kind = 'keyword'"
            " AND v.metric_date BETWEEN %s AND %s"
        ),
        "episodios": "SELECT count(*) FROM proteccion_venta_reciente WHERE platform = %s",
        "filas_cola": "SELECT count(*) FROM apply_queue WHERE platform = %s",
    }
    huella = {}
    for nombre, sql in consulta.items():
        parametros = (PLATAFORMA,)
        if nombre == "filas_metricas_recientes":
            parametros = (PLATAFORMA, RECIENTE_DESDE, RECIENTE_HASTA)
        huella[nombre] = conn.execute(sql, parametros).fetchone()[0]
    huella["productos"] = conn.execute(
        "SELECT count(*) FROM ad_entity WHERE kind = 'product_ad'"
    ).fetchone()[0]
    return huella


def _crea_desde_plantilla(admin, plantilla: str, prefijo: str) -> str:
    from psycopg import sql as pgsql

    nombre = f"{prefijo}_{uuid.uuid4().hex[:12]}"
    admin.execute(
        pgsql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            pgsql.Identifier(nombre), pgsql.Identifier(plantilla)
        )
    )
    return nombre


def _borra(admin, nombre: str) -> None:
    from psycopg import sql as pgsql

    admin.execute(
        pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nombre))
    )


def _sha(code_dir: Path) -> str | None:
    try:
        salida = subprocess.run(
            ["git", "-C", str(code_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return salida.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def cmd_preparar(args) -> int:
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    dsn = _dsn()
    admin = _conecta(dsn)
    try:
        _borra(admin, args.plantilla)
        from psycopg import sql as pgsql

        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(args.plantilla)))
        conn = _conecta(dsn, args.plantilla)
        try:
            conn.execute("SET TIME ZONE 'UTC'")
            migraciones = _aplica_migraciones(conn)
            sembrado = _siembra(conn, proteccion=args.proteccion == "on", productos=args.productos)
            huella = _huella(conn)
        finally:
            conn.close()
        print(
            json.dumps(
                {
                    "plantilla": args.plantilla,
                    "migraciones": migraciones,
                    "carga": sembrado,
                    "huella": huella,
                },
                ensure_ascii=False,
            )
        )
    finally:
        admin.close()
    return 0


def _mide_una(conn, rol: str, ronda: int) -> dict:
    from app import apply_cola
    from app import cycle as ciclo
    from app.optimizer import goals as g

    try:
        from app.optimizer import proteccion as subproteccion
    except ImportError:  # base: el modulo no existe
        subproteccion = None

    try:
        from app import proteccion_venta
    except ImportError:  # base: el modulo no existe
        proteccion_venta = None

    _config_id, settings = ciclo._config_reciente(conn)
    modo = g.resuelve_modo(g.modo_desde_settings(settings), ciclo._MODO_TOPE_ENVELOPE)
    activa = bool(getattr(subproteccion, "activa_desde_settings", lambda _s: False)(settings))
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', %s) RETURNING id",
        (PLATAFORMA,),
    ).fetchone()[0]

    def fase_tx2(proxy):
        ciclo._fase_lecturas(
            proxy,
            platform=PLATAFORMA,
            settings=settings,
            modo=modo.modo,
            decided_at=DECIDED_AT,
            contadores=ciclo._Contadores(),
            pendientes=[],
            tick=lambda: None,
            cycle_id=ciclo_id,
        )

    tx2_ms, tx2_consultas = _cuenta(conn, fase_tx2)

    def bloque(proxy):
        claves = getattr(apply_cola, "claves_released", None)
        if activa and claves is not None:
            claves(proxy, PLATAFORMA)
            proteccion_venta.lee_cache(proxy, PLATAFORMA, DECIDED_AT)

    bloque_ms, bloque_consultas = _cuenta(conn, bloque)

    filas: list = []

    def barrido(proxy):
        filas.extend(proxy.execute(apply_cola._SQL_VENCIDAS, (PLATAFORMA, DECIDED_AT)).fetchall())
        if activa and proteccion_venta is not None:
            proteccion_venta.lee_cache(proxy, PLATAFORMA, DECIDED_AT)

    barrido_ms, barrido_consultas = _cuenta(conn, barrido)

    persistencia_ms = None
    persistencia_filas = 0
    persistencia_consultas = 0
    if activa and proteccion_venta is not None:
        ids = [
            fila[0]
            for fila in conn.execute(
                "SELECT id FROM ad_entity WHERE platform = %s AND kind = 'keyword'"
                " ORDER BY id OFFSET %s LIMIT %s",
                (PLATAFORMA, 800, CARGA["episodios"]),
            ).fetchall()
        ]
        transiciones = [
            proteccion_venta.TransicionProteccion(
                platform=PLATAFORMA,
                ad_entity_id=entidad,
                ancla=ANCLA,
                vence_el=VENCE,
                abierta_el=DECIDED_AT,
                observado_el=DECIDED_AT - dt.timedelta(hours=1),
            )
            for entidad in ids
        ]
        persistencia_ms, persistencia_consultas = _cuenta(
            conn, lambda proxy: proteccion_venta.inserta_transiciones(proxy, transiciones)
        )
        persistencia_filas = len(transiciones)

    return {
        "rol": rol,
        "ronda": ronda,
        "proteccion": activa,
        "tx2_ms": tx2_ms,
        "tx2_consultas": tx2_consultas,
        "bloque_ms": bloque_ms,
        "bloque_consultas": bloque_consultas,
        "barrido_ms": barrido_ms,
        "barrido_consultas": barrido_consultas,
        "barrido_filas": len(filas),
        "persistencia_ms": persistencia_ms,
        "persistencia_consultas": persistencia_consultas,
        "persistencia_filas": persistencia_filas,
        "nueva_fase": proteccion_venta is not None,
    }


def _mide_ciclo(conn, rol: str, ronda: int) -> dict:
    from app import cycle as ciclo

    def corre(proxy):
        return ciclo.corre_ciclo(
            proxy,
            platform=PLATAFORMA,
            owner=f"bench-{rol}-{ronda}",
            decided_at=DECIDED_AT,
            heartbeat_cada=1,
        )

    proxy = _ConexionContador(conn)
    inicio = time.perf_counter()
    resultado = corre(proxy)
    ms = (time.perf_counter() - inicio) * 1000.0
    return {
        "rol": rol,
        "ronda": ronda,
        "ciclo_ms": ms,
        "ciclo_consultas": proxy.sentencias,
        "ciclo_status": resultado.status,
        "ciclo_decisiones": resultado.decisions_count,
    }


def _cuenta(conn, fn) -> tuple[float, int]:
    proxy = _ConexionContador(conn)
    inicio = time.perf_counter()
    fn(proxy)
    return (time.perf_counter() - inicio) * 1000.0, proxy.sentencias


def cmd_medir(args) -> int:
    import tempfile

    code_dir = Path(args.code_dir).resolve()
    sys.path = [
        p
        for p in sys.path
        if Path(p or ".").resolve() not in (RAIZ, RAIZ / "tools", code_dir / "tools")
    ]
    sys.path.insert(0, str(code_dir))
    os.environ.setdefault("ORBIT_SECRETS_DIR", tempfile.mkdtemp(prefix="orbit-bench-secrets-"))

    dsn = _dsn()
    admin = _conecta(dsn)
    rondas: list[dict] = []
    try:
        for indice in range(args.rondas):
            ronda = args.ronda + indice
            fila = {"ronda": ronda}
            # Fase de lecturas, bloque y barrido sobre una copia.
            nombre = _crea_desde_plantilla(admin, args.plantilla, "orbit07_bench")
            conn = _conecta(dsn, nombre)
            try:
                conn.execute("SET TIME ZONE 'UTC'")
                fila.update(_mide_una(conn, args.rol, ronda))
            finally:
                conn.close()
                _borra(admin, nombre)
            # Ciclo completo sobre una copia limpia: la persistencia medida
            # arriba no altera las entradas del ciclo.
            nombre = _crea_desde_plantilla(admin, args.plantilla, "orbit07_bench")
            conn = _conecta(dsn, nombre)
            try:
                conn.execute("SET TIME ZONE 'UTC'")
                fila.update(_mide_ciclo(conn, args.rol, ronda))
            finally:
                conn.close()
                _borra(admin, nombre)
            rondas.append(fila)
    finally:
        admin.close()
    print(
        json.dumps(
            {
                "rol": args.rol,
                "sha": _sha(code_dir),
                "code_dir": str(code_dir),
                "rondas": rondas,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _resumen(rol: dict) -> dict:
    rondas = rol["rondas"]
    metricas = (
        "tx2_ms",
        "bloque_ms",
        "barrido_ms",
        "ciclo_ms",
        "persistencia_ms",
    )
    conteos = (
        "tx2_consultas",
        "bloque_consultas",
        "barrido_consultas",
        "ciclo_consultas",
        "persistencia_consultas",
    )
    salida = {"sha": rol["sha"], "rondas": len(rondas)}
    for metrica in metricas:
        valores = [r[metrica] for r in rondas if r.get(metrica) is not None]
        if not valores:
            salida[metrica] = None
            continue
        salida[metrica] = {
            "media": round(statistics.mean(valores), 2),
            "mediana": round(statistics.median(valores), 2),
            "min": round(min(valores), 2),
            "max": round(max(valores), 2),
        }
    for conteo in conteos:
        valores = [r[conteo] for r in rondas if r.get(conteo) is not None]
        salida[conteo] = valores[0] if valores else None
    salida["proteccion"] = rondas[0]["proteccion"]
    salida["barrido_filas"] = rondas[0]["barrido_filas"]
    salida["ciclo_decisiones"] = [r["ciclo_decisiones"] for r in rondas]
    return salida


def cmd_comparar(args) -> int:
    dsn = _dsn()
    conn = _conecta(dsn, args.plantilla)
    try:
        huella = _huella(conn)
    finally:
        conn.close()

    resultados = {"base": [], "candidato": []}
    for ronda in range(args.rondas):
        for rol in ("base", "candidato"):
            code_dir = args.code_base if rol == "base" else RAIZ
            comando = [
                sys.executable,
                str(RAIZ / "tools" / "bench_orbit07_proteccion.py"),
                "medir",
                "--plantilla",
                args.plantilla,
                "--rol",
                rol,
                "--code-dir",
                str(code_dir),
                "--rondas",
                "1",
                "--ronda",
                str(ronda),
            ]
            salida = subprocess.run(
                comando,
                capture_output=True,
                text=True,
                env={**os.environ, "ORBIT_TEST_DSN": dsn},
                check=True,
            )
            datos = json.loads(salida.stdout.strip().splitlines()[-1])
            resultados[rol].extend(datos["rondas"])

    comparacion = {
        "fecha": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "carga": {
            **CARGA,
            **({"productos": huella["productos"]} if huella.get("productos") else {}),
        },
        "decided_at": DECIDED_AT.isoformat(),
        "plantilla": args.plantilla,
        "huella": huella,
        "nota": (
            "Cada corrida clona la MISMA plantilla fija; base y candidato alternan "
            "por ronda sobre esa copia. tx2_ms mide la fase de lecturas completa; "
            "bloque_ms el bloque cycle.py:3151-3152; barrido_ms la lectura del "
            "barrido (apply_cola.py:1333-1334); ciclo_ms el ciclo en shadow. "
            "`proteccion` indica si la plantilla tiene el setting "
            "ads_proteccion_venta (regla M1 encendida). Con `productos` > 0 la "
            "plantilla suma grupos exclusivos con margen 8 % y meta live: la "
            "resolucion por lote del peldaño de producto corre en TX2 aunque la "
            "ventana reciente del banco no produzca decisiones."
        ),
        "base": _resumen({"rondas": resultados["base"], "sha": _sha(args.code_base)}),
        "candidato": _resumen({"rondas": resultados["candidato"], "sha": _sha(RAIZ)}),
    }
    texto = json.dumps(comparacion, ensure_ascii=False, indent=2)
    if args.salida:
        Path(args.salida).write_text(texto + "\n", encoding="utf-8")
    print(texto)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("preparar")
    p.add_argument("--plantilla", required=True)
    p.add_argument("--proteccion", choices=("on", "off"), default="on")
    p.add_argument(
        "--productos",
        type=int,
        default=0,
        help="grupos exclusivos con margen 8 por ciento para la carga de M2 (default 0)",
    )
    p.set_defaults(func=cmd_preparar)

    p = sub.add_parser("medir")
    p.add_argument("--plantilla", required=True)
    p.add_argument("--rol", required=True, choices=("base", "candidato"))
    p.add_argument("--code-dir", required=True)
    p.add_argument("--rondas", type=int, default=1)
    p.add_argument("--ronda", type=int, default=0)
    p.set_defaults(func=cmd_medir)

    p = sub.add_parser("comparar")
    p.add_argument("--plantilla", required=True)
    p.add_argument("--code-base", required=True)
    p.add_argument("--rondas", type=int, default=3)
    p.add_argument("--salida")
    p.set_defaults(func=cmd_comparar)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
