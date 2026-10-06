"""Tests del job `jev-senales` (JEV ADS 02, S.4).

Nueve pruebas contra Postgres real con 0001 + 0002 + 0004 + 0049 + 0050 +
0051 + 0052. `pedir` siempre falso (Sin TypeSafe real); los tests importan
`app.jev_senales` dentro de cada prueba para que el rojo TDD no rompa la
coleccion del archivo antes de que el modulo exista.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from test_jev_catalogo import _ficha, _grupo, _listing, _producto
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.jev_ads import ClavePar, Juicio
from app.jev_juicios import FalloPar, ResultadoPar

RAIZ = Path(__file__).resolve().parents[1]
ORDEN_S4 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0049_jev_ads.sql",
    "0050_jev_revision_created_at.sql",
    "0051_ads_acta_listado.sql",
    "0052_jev_senales.sql",
)
SQL_S4 = "\n".join(
    (RAIZ / "migrations" / nombre).read_text(encoding="utf-8") for nombre in ORDEN_S4
)

TABLAS_JEV_S4 = (
    "jev_ficha_version",
    "jev_ficha_revocacion",
    "jev_revision",
    "jev_par_evento",
    "jev_roster",
    "jev_senal",
    "jev_aviso",
    "jev_aviso_entrega",
    "jev_corrida",
)

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

AHORA = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)
VENCE_FICHA = AHORA + timedelta(days=30)
ON = {"jev.senales": True, "jev.tope_diario": 5000, "jev.min_clics": 3}


@contextmanager
def db_s4():
    dsn = _test_dsn()
    db = f"orbit_jevs4_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL_S4)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _dsn_s4() -> str:
    base = _test_dsn().rsplit("/", 1)[0]
    return f"{base}/orbit_jevs4_{socket.gethostname().lower()}_{os.getpid()}"


class _Pedido:
    """Juicio falso: guion termino -> relacion, fallos en llamadas dadas."""

    def __init__(self, guion=None, falla_en=()):
        self.guion = guion or {}
        self.falla_en = set(falla_en)
        self.llamados: list[str] = []

    def __call__(self, termino, ficha):
        self.llamados.append(termino)
        if len(self.llamados) in self.falla_en:
            return FalloPar(codigo="timeout", detalle="se agoto la espera", duracion_ms=12)
        return ResultadoPar(
            juicio=Juicio(
                intento_id=uuid.uuid4(),
                clave=ClavePar("a" * 64, ficha.id, "b" * 64),
                relacion=self.guion.get(termino, "no_satisface"),
                probabilidades={
                    "satisface": Decimal("0.10"),
                    "no_satisface": Decimal("0.80"),
                    "informacion_insuficiente": Decimal("0.10"),
                },
                confidence=Decimal("0.90"),
                observado_at=AHORA,
            ),
            usage={"input_tokens": 5},
            duracion_ms=7,
        )


def _config(conn, settings: dict) -> int:
    return conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s::jsonb) RETURNING id",
        (json.dumps(settings),),
    ).fetchone()[0]


def _run(conn, source="amazon_ads_structure_v2", *, ok=True, finished_at=None) -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source, ok, finished_at) VALUES (%s, %s, %s) RETURNING id",
        (source, ok, finished_at or AHORA),
    ).fetchone()[0]


def _acta(
    conn,
    run_id: int,
    plataforma: str,
    grupos: int,
    ads: int,
    filas_grupo: tuple = (),
    *,
    sin_grupo: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO ads_listado_plataforma (ingest_run_id, platform, ad_groups_recibidos,"
        " ad_groups_declarados, product_ads_recibidos, product_ads_declarados,"
        " product_ads_sin_grupo) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (run_id, plataforma, grupos, grupos, ads, ads, sin_grupo),
    )
    for ad_group_id, vivos, huella, descartados in filas_grupo:
        conn.execute(
            "INSERT INTO ads_listado_grupo (ingest_run_id, platform, ad_group_id,"
            " anuncios_vivos, huella_vivos, descartados) VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, plataforma, ad_group_id, vivos, huella, descartados),
        )


def _obs(
    conn,
    run_id: int,
    plataforma: str,
    grupo: int,
    termino: str,
    fecha,
    *,
    cost,
    clicks,
    orders,
    moneda: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders,"
        " ad_revenue, is_asin_like, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, FALSE, 'r', %s)",
        (
            plataforma,
            grupo,
            termino,
            fecha,
            AHORA,
            moneda or {"amazon_mx": "MXN", "amazon_us": "USD"}[plataforma],
            cost,
            clicks,
            orders,
            run_id,
        ),
    )


def _ventana(conn, run_id, plataforma, grupo, termino, *, dias=7, cost=10, clicks=5):
    """Observaciones 09-20..09-26 con cero ordenes: candidatas en ventana."""
    base = datetime(2026, 9, 20, tzinfo=UTC).date()
    for i in range(dias):
        _obs(
            conn,
            run_id,
            plataforma,
            grupo,
            termino,
            base + timedelta(days=i),
            cost=cost,
            clicks=clicks,
            orders=0,
        )


def _ads_de(conn, grupo: int) -> list[tuple]:
    return conn.execute(
        "SELECT id, external_id, listing_id FROM ad_entity WHERE parent_id = %s ORDER BY id",
        (grupo,),
    ).fetchall()


def _estados(conn, grupo: int, status: str = "ENABLED") -> None:
    for ad_id, _, _ in _ads_de(conn, grupo):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, %s)",
            (ad_id, status, AHORA - timedelta(days=1)),
        )


def _ciclo(conn, plataforma: str = "amazon_mx") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', %s) RETURNING id",
        (plataforma,),
    ).fetchone()[0]


def _decision(
    conn,
    ciclo: int,
    grupo: int,
    kind: str,
    termino: str,
    config_id: int,
    inputs: dict,
) -> int:
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
        (
            ciclo,
            grupo,
            kind,
            AHORA,
            config_id,
            AHORA,
            datetime(2026, 8, 28, tzinfo=UTC).date(),
            datetime(2026, 9, 26, tzinfo=UTC).date(),
            termino,
            100 if kind == "harvest" else None,
            "MXN" if kind == "harvest" else None,
            json.dumps(inputs),
        ),
    ).fetchone()[0]


def _cola(
    conn,
    decision_id: int,
    plataforma: str,
    grupo: int,
    termino: str,
    kind: str,
    *,
    estado: str = "pending_veto",
) -> int:
    fila = conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, search_term, kind, decision_id,"
        " modo, estado, vence_el, request_payload)"
        " VALUES (%s, %s, %s, %s, %s, 'live', 'pending_veto', %s, '{}') RETURNING id",
        (plataforma, grupo, termino, kind, decision_id, AHORA + timedelta(days=2)),
    ).fetchone()[0]
    for paso in {"released": ("released",), "applied": ("released", "applying", "applied")}.get(
        estado, ()
    ):
        conn.execute("UPDATE apply_queue SET estado = %s WHERE id = %s", (paso, fila))
    return fila


def _conteos(conn) -> dict:
    return {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
        for tabla in TABLAS_JEV_S4
    }


def _intenciones(conn) -> int:
    return conn.execute("SELECT count(*) FROM jev_par_evento WHERE tipo = 'intencion'").fetchone()[
        0
    ]


def _escritor():
    return psycopg.connect(_dsn_s4(), autocommit=True)


def _grupo_con_roster(conn, plataforma="amazon_mx", n=2):
    """Grupo con n productos, listings, ads ENABLED y fichas vigentes."""
    productos = [_producto(conn, f"P-{uuid.uuid4().hex[:8]}") for _ in range(n)]
    listings = [_listing(conn, p, plataforma) for p in productos]
    grupo = _grupo(conn, plataforma, tuple(listings))
    _estados(conn, grupo)
    fichas = [
        _ficha(conn, prod, (lst,), plataforma, observado=AHORA - timedelta(days=1))
        for prod, lst in zip(productos, listings, strict=True)
    ]
    return grupo, productos, listings, fichas


@_skip_db
def test_no_se_paga_dos_veces():
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn)
        compartido = _producto(conn, "P-compartido")
        lc = _listing(conn, compartido, "amazon_mx")
        p2 = _producto(conn, "P-2")
        l2 = _listing(conn, p2, "amazon_mx")
        p3 = _producto(conn, "P-3")
        l3 = _listing(conn, p3, "amazon_mx")
        g1 = _grupo(conn, "amazon_mx", (lc, l2))
        g2 = _grupo(conn, "amazon_mx", (lc, l3))
        _estados(conn, g1)
        _estados(conn, g2)
        for prod, lst in ((compartido, lc), (p2, l2), (p3, l3)):
            _ficha(conn, prod, (lst,), "amazon_mx", observado=AHORA - timedelta(days=1))
        _ventana(conn, run, "amazon_mx", g1, "collar oro")
        _ventana(conn, run, "amazon_mx", g2, "collar oro")
        with _escritor() as escritor:
            pedido = _Pedido()
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key="k"
            )
            assert cierre.motivo == "completa"
            assert len(pedido.llamados) == 3
            assert _intenciones(conn) == 3
            senales = conn.execute("SELECT count(*) FROM jev_senal").fetchone()[0]
            assert senales == 2

            pedido2 = _Pedido()
            cierre2 = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido2, api_key="k"
            )
            assert cierre2.motivo == "completa"
            assert pedido2.llamados == []
            assert _intenciones(conn) == 3
            assert conn.execute("SELECT count(*) FROM jev_senal").fetchone()[0] == senales

            conn.execute("UPDATE ad_entity_state SET synced_at = %s", (AHORA,))
            pedido3 = _Pedido()
            jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido3, api_key="k"
            )
            assert pedido3.llamados == []
            assert _intenciones(conn) == 3

            p4 = _producto(conn, "P-4")
            l4 = _listing(conn, p4, "amazon_mx")
            conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
                " VALUES ('amazon_mx', 'product_ad', %s, %s, %s) RETURNING id",
                (f"ad-{g1}-9", g1, l4),
            )
            ad4 = conn.execute(
                "SELECT id FROM ad_entity WHERE external_id = %s", (f"ad-{g1}-9",)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', %s)",
                (ad4, AHORA),
            )
            _ficha(conn, p4, (l4,), "amazon_mx", observado=AHORA - timedelta(days=1))
            pedido4 = _Pedido()
            jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido4, api_key="k"
            )
            assert len(pedido4.llamados) == 1
            assert _intenciones(conn) == 4


@_skip_db
def test_tope_aguanta_una_caida():
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, {"jev.senales": True, "jev.tope_diario": 3, "jev.min_clics": 3})
        run = _run(conn)
        grupo, _, _, _ = _grupo_con_roster(conn, n=4)
        _ventana(conn, run, "amazon_mx", grupo, "collar plata")
        with _escritor() as escritor:
            pedido = _Pedido(falla_en={2})
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key="k"
            )
            assert cierre.motivo == "tope"
            assert len(pedido.llamados) == 3
            assert _intenciones(conn) == 3

            pedido2 = _Pedido()
            cierre2 = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido2, api_key="k"
            )
            assert cierre2.motivo == "tope"
            assert pedido2.llamados == []
            assert _intenciones(conn) == 3


@_skip_db
def test_apagado_es_cero(capsys):
    from app import jev_senales

    with db_s4() as conn:
        run = _run(conn)
        grupo, _, _, _ = _grupo_con_roster(conn)
        _ventana(conn, run, "amazon_mx", grupo, "collar oro")
        antes = _conteos(conn)
        with _escritor() as escritor:
            for settings in (
                {},
                {"jev.senales": "true", "jev.tope_diario": 5, "jev.min_clics": 3},
                {"jev.senales": True, "jev.tope_diario": -1, "jev.min_clics": 3},
            ):
                _config(conn, settings)
                pedido = _Pedido()
                cierre = jev_senales.correr(
                    conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key="k"
                )
                assert cierre.motivo == "apagado"
                assert "apagado" in capsys.readouterr().out
                assert pedido.llamados == []
                assert _conteos(conn) == antes


@_skip_db
def test_sin_clave_sella_ventas_sin_intenciones():
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn)
        grupo, _, _, _ = _grupo_con_roster(conn)
        _ventana(conn, run, "amazon_mx", grupo, "anillo oro")
        _ventana(conn, run, "amazon_mx", grupo, "anillo plata")
        _obs(
            conn,
            run,
            "amazon_mx",
            grupo,
            "anillo oro",
            datetime(2026, 10, 1, tzinfo=UTC).date(),
            cost=50,
            clicks=9,
            orders=1,
        )
        with _escritor() as escritor:
            pedido = _Pedido()
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key=""
            )
            assert cierre.motivo == "sin_api_key"
            assert pedido.llamados == []
            assert _intenciones(conn) == 0
            filas = {
                fila[0]: fila[1:]
                for fila in conn.execute(
                    "SELECT termino, lectura, relevancia, motivos_lectura FROM jev_senal"
                ).fetchall()
            }
            assert filas["anillo oro"][:2] == ("vendio_aqui", "no_evaluada")
            assert filas["anillo plata"] == ("sin_lectura", "no_evaluada", ["jev_no_evaluada"])


@_skip_db
def test_universo_de_punta_a_punta():
    from app import jev_senales
    from app.ads.structure_plan import huella_anuncios

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn, finished_at=AHORA - timedelta(hours=1))
        p_arch = _producto(conn, "P-arch")
        l_arch = _listing(conn, p_arch, "amazon_mx")
        p1 = _producto(conn, "P-1")
        l1 = _listing(conn, p1, "amazon_mx")
        p2 = _producto(conn, "P-2")
        l2 = _listing(conn, p2, "amazon_mx")
        grupo = _grupo(conn, "amazon_mx", (l_arch, l1, l2))
        ads = _ads_de(conn, grupo)
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, %s)",
            (ads[0][0], "ARCHIVED", AHORA - timedelta(days=1)),
        )
        for ad_id, _, _ in ads[1:]:
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', %s)",
                (ad_id, AHORA - timedelta(days=1)),
            )
        for prod, lst in ((p1, l1), (p2, l2)):
            _ficha(conn, prod, (lst,), "amazon_mx", observado=AHORA - timedelta(days=1))
        vivos = sorted(externo for _, externo, _ in ads[1:])
        _acta(
            conn,
            run,
            "amazon_mx",
            1,
            3,
            ((grupo, 2, huella_anuncios(vivos), 0),),
        )
        _ventana(conn, run, "amazon_mx", grupo, "anillo oro")
        with _escritor() as escritor:
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=_Pedido(), api_key="k"
            )
            assert cierre.motivo == "completa"
            fila = conn.execute(
                "SELECT lectura, miembros, evaluados, roster_probado, productos_ok"
                " FROM jev_senal WHERE termino = 'anillo oro'"
            ).fetchone()
            assert fila == ("ajena", 2, 2, True, [])


@_skip_db
def test_madurez_otro_grupo_vs_historial():
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn)
        grupo_a, _, _, _ = _grupo_con_roster(conn)
        grupo_b, _, _, _ = _grupo_con_roster(conn)
        _ventana(conn, run, "amazon_mx", grupo_a, "collar oro")
        _ventana(conn, run, "amazon_mx", grupo_a, "collar plata")
        _obs(
            conn,
            run,
            "amazon_mx",
            grupo_b,
            "collar oro",
            datetime(2026, 10, 1, tzinfo=UTC).date(),
            cost=40,
            clicks=8,
            orders=2,
        )
        _obs(
            conn,
            run,
            "amazon_mx",
            grupo_a,
            "collar plata",
            datetime(2026, 10, 1, tzinfo=UTC).date(),
            cost=40,
            clicks=8,
            orders=1,
        )
        with _escritor() as escritor:
            jev_senales.correr(
                conn,
                escritor,
                ahora=AHORA,
                aplicar=True,
                pedir=_Pedido({"collar oro": "satisface"}),
                api_key="k",
            )
            filas = {
                fila[0]: fila[1]
                for fila in conn.execute(
                    "SELECT termino, lectura FROM jev_senal WHERE ad_group_id = %s", (grupo_a,)
                ).fetchall()
            }
            assert filas["collar oro"] == "relevante_sin_venta"
            assert filas["collar plata"] == "vendio_aqui"


@_skip_db
def test_harvest_sin_destino_legible():
    from app import jev_senales

    with db_s4() as conn:
        config_id = _config(conn, ON)
        run = _run(conn)
        ciclo = _ciclo(conn)
        origen, _, _, _ = _grupo_con_roster(conn)
        destino, _, _, _ = _grupo_con_roster(conn)
        externo_destino = conn.execute(
            "SELECT external_id FROM ad_entity WHERE id = %s", (destino,)
        ).fetchone()[0]
        _ventana(conn, run, "amazon_mx", origen, "collar oro")
        _ventana(conn, run, "amazon_mx", origen, "collar plata")
        d1 = _decision(conn, ciclo, origen, "harvest", "collar oro", config_id, {})
        d2 = _decision(
            conn,
            ciclo,
            origen,
            "harvest",
            "collar plata",
            config_id,
            {"goal": {"harvest": {"ad_group_id": externo_destino}}},
        )
        c1 = _cola(conn, d1, "amazon_mx", origen, "collar oro", "harvest")
        c2 = _cola(conn, d2, "amazon_mx", origen, "collar plata", "harvest")
        with _escritor() as escritor:
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=_Pedido(), api_key="k"
            )
            assert cierre.motivo == "completa"
            claves = {
                (fila[0], fila[1])
                for fila in conn.execute("SELECT ad_group_id, termino FROM jev_senal").fetchall()
            }
            assert (origen, "collar oro") in claves
            assert (origen, "collar plata") in claves
            assert (destino, "collar plata") in claves
            resumen = conn.execute(
                "SELECT resumen FROM jev_corrida WHERE evento = 'fin'"
            ).fetchone()[0]
            assert c1 in resumen["propuestas_ilegibles"]
            assert c2 not in resumen["propuestas_ilegibles"]


@_skip_db
def test_seco_por_omision(capsys, monkeypatch):
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn)
        grupo, _, _, _ = _grupo_con_roster(conn)
        _ventana(conn, run, "amazon_mx", grupo, "collar oro")
        antes = _conteos(conn)
        pedido = _Pedido()
        cierre = jev_senales.correr(
            conn, None, ahora=AHORA, aplicar=False, pedir=pedido, api_key="k"
        )
        assert cierre.unidades == 1
        assert cierre.llamadas == 2
        salida = capsys.readouterr().out
        assert "unidades=1" in salida
        assert "pagaria=2" in salida
        assert pedido.llamados == []
        assert _conteos(conn) == antes

        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_s4())
        monkeypatch.delenv("ORBIT_DSN_JEV", raising=False)
        assert jev_senales.main([]) == 0
        assert "seco" in capsys.readouterr().out
        assert _conteos(conn) == antes


@_skip_db
def test_dos_procesos_a_la_vez(capsys):
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn)
        grupo, _, _, _ = _grupo_con_roster(conn)
        _ventana(conn, run, "amazon_mx", grupo, "collar oro")
        antes = _conteos(conn)
        otra = psycopg.connect(_dsn_s4(), autocommit=True)
        try:
            otra.execute("SELECT pg_advisory_lock(hashtext('jev:senales'))").fetchone()
            with _escritor() as escritor:
                pedido = _Pedido()
                cierre = jev_senales.correr(
                    conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key="k"
                )
                assert cierre.motivo == "ocupado"
                assert "ocupado" in capsys.readouterr().out
                assert pedido.llamados == []
                assert _conteos(conn) == antes
        finally:
            otra.execute("SELECT pg_advisory_unlock(hashtext('jev:senales'))").fetchone()
            otra.close()


def _ultima_lectura(conn, grupo: int, termino: str):
    return conn.execute(
        "SELECT lectura, evaluados, miembros FROM jev_senal"
        " WHERE ad_group_id = %s AND termino = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (grupo, termino),
    ).fetchone()


@_skip_db
def test_cupo_agotado_reutiliza_juicios_guardados():
    """B1 (J4-r2): con el cupo agotado, las unidades con juicios guardados
    conservan su lectura y cuesta 0. La propuesta sin guardar va primero
    (tramo propuesta) y agota el razon; el resto con todo guardado debe
    sellar igual que en la corrida 1."""
    from app import jev_senales
    from app.ads.structure_plan import huella_anuncios

    with db_s4() as conn:
        config_id = _config(conn, {"jev.senales": True, "jev.tope_diario": 2, "jev.min_clics": 3})
        run = _run(conn, finished_at=AHORA - timedelta(hours=1))
        g_ok, _, _, _ = _grupo_con_roster(conn, n=2)
        vivos = sorted(externo for _, externo, _ in _ads_de(conn, g_ok))
        _acta(conn, run, "amazon_mx", 1, 2, ((g_ok, 2, huella_anuncios(vivos), 0),))
        _ventana(conn, run, "amazon_mx", g_ok, "collar oro")
        with _escritor() as escritor:
            pedido = _Pedido()
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key="k"
            )
            assert cierre.motivo == "completa"
            assert len(pedido.llamados) == 2
            assert _intenciones(conn) == 2
            antes = _ultima_lectura(conn, g_ok, "collar oro")
            assert antes[0] != "sin_lectura"

            g_nuevo, _, _, _ = _grupo_con_roster(conn, n=1)
            _ventana(conn, run, "amazon_mx", g_nuevo, "collar plata")
            ciclo = _ciclo(conn)
            dec = _decision(conn, ciclo, g_nuevo, "negative", "collar plata", config_id, {})
            _cola(conn, dec, "amazon_mx", g_nuevo, "collar plata", "negative")
            pedido2 = _Pedido()
            cierre2 = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido2, api_key="k"
            )
            assert cierre2.motivo == "tope"
            assert pedido2.llamados == []
            assert _intenciones(conn) == 2
            assert _ultima_lectura(conn, g_ok, "collar oro") == antes


@_skip_db
def test_proveedor_caido_reutiliza_juicios_guardados():
    """B1 (J4-r2): tras 5 fallos HTTP seguidos, las unidades siguientes con
    juicios guardados conservan su lectura; el cierre dice proveedor_caido
    y no se abre ninguna intencion mas."""
    from app import jev_senales
    from app.ads.structure_plan import huella_anuncios

    with db_s4() as conn:
        config_id = _config(conn, ON)
        run = _run(conn, finished_at=AHORA - timedelta(hours=1))
        g_ok, _, _, _ = _grupo_con_roster(conn, n=2)
        vivos = sorted(externo for _, externo, _ in _ads_de(conn, g_ok))
        _acta(conn, run, "amazon_mx", 1, 2, ((g_ok, 2, huella_anuncios(vivos), 0),))
        _ventana(conn, run, "amazon_mx", g_ok, "collar oro")
        with _escritor() as escritor:
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=_Pedido(), api_key="k"
            )
            assert cierre.motivo == "completa"
            assert _intenciones(conn) == 2
            antes = _ultima_lectura(conn, g_ok, "collar oro")
            assert antes[0] != "sin_lectura"

            g_falla, _, _, _ = _grupo_con_roster(conn, n=5)
            _ventana(conn, run, "amazon_mx", g_falla, "collar plata")
            ciclo = _ciclo(conn)
            dec = _decision(conn, ciclo, g_falla, "negative", "collar plata", config_id, {})
            _cola(conn, dec, "amazon_mx", g_falla, "collar plata", "negative")
            pedido2 = _Pedido(falla_en={1, 2, 3, 4, 5})
            cierre2 = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido2, api_key="k"
            )
            assert cierre2.motivo == "proveedor_caido"
            assert len(pedido2.llamados) == 5
            assert _intenciones(conn) == 7
            assert _ultima_lectura(conn, g_ok, "collar oro") == antes


@_skip_db
def test_cinco_excesos_no_tumban_al_proveedor():
    """NB1 (J4-r2): el disyuntor solo cuenta fallos del HTTP. Cinco excesos
    de contexto seguidos (nunca llaman) no marcan proveedor_caido."""
    from app import jev_senales

    with db_s4() as conn:
        _config(conn, ON)
        run = _run(conn)
        grupo, _, _, _ = _grupo_con_roster(conn, n=5)
        _ventana(conn, run, "amazon_mx", grupo, "x" * 1100)
        with _escritor() as escritor:
            pedido = _Pedido()
            cierre = jev_senales.correr(
                conn, escritor, ahora=AHORA, aplicar=True, pedir=pedido, api_key="k"
            )
            assert cierre.motivo == "completa"
            assert pedido.llamados == []
            assert _intenciones(conn) == 0
