"""AUTO-03 I.3-I.7 (dinero y escritura): la pausa por producto sin ventas se ejecuta.

Cada caso fija que sale hacia Amazon (PUT, cuantas veces), que cuota se cobra
contra `ads_apply_cap_amazon_us_pause` y que filas quedan en la cola, el ledger
de intentos y el ledger de retiro de la base de produccion. Amazon es un
transporte falso detras del cliente de escritura real (`Aplicador._cliente()`),
asi que la clasificacion de respuestas y errores es la del cliente.

La base es la cadena completa de migraciones (incluida la 0080, como
tests/test_schema_docs.py) y el retiro, su reconciliacion y la reactivacion
corren con SET ROLE app_decide, el rol del ciclo. Los datos de la ventana de la
pausa son los de tests/test_productos_pausa.py: el producto SKU-A (anuncio pa1)
gasto 60 USD con 120 clics sin vender en [D-60, D-30).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_cycle import DECIDED_AT, _config_version, _entidad, _siembra_maestra
from test_cycle import _corre as _corre_ciclo_fijo
from test_cycle import _db_temporal as _db_ciclo
from test_productos_io import _anuncio
from test_productos_pausa import _impulso_abierto, _mundo_pausa, _repone, _retiro
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import (
    apply_cola,
    apply_producto,
    cli_productos,
    config_write,
    productos_io,
    productos_modo,
)
from app import apply_harvest_reconciliacion as reconciliacion
from app import cycle as ciclo
from app import retiro_anuncios as ra
from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.cycle import lee_goals
from app.optimizer import productos as p
from app.optimizer.politica import Mover
from tests.test_ads_write import _token_response

pytestmark = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
PLAT = "amazon_us"
CAP_PAUSE = 5
MOTOR_PAUSE = "ads_optimizer:amazon_us:pause"
LIMITES = {"ads_producto_pausa_clics_amazon_us": 100, "ads_producto_pausa_gasto_amazon_us": "40"}


# ---------------------------------------------------------------------------
# Base: cadena completa de migraciones (plantilla por modulo)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plantilla():
    dsn = _test_dsn()
    nombre = f"orbit_i_plantilla_{socket.gethostname().lower().replace('-', '_')}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(nombre)))
        with psycopg.connect(dsn, dbname=nombre, autocommit=True) as conn:
            conn.execute("SET TIME ZONE 'UTC'")
            for ruta in sorted(MIGRACIONES.glob("*.sql")):
                if "0011_" in ruta.name or "_reversa_" in ruta.name:
                    continue
                conn.execute(ruta.read_text(encoding="utf-8"))
        yield nombre
    finally:
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nombre))
        )
        admin.close()


@contextmanager
def _db(plantilla: str, prefijo: str):
    dsn = _test_dsn()
    nombre = f"{prefijo}_{socket.gethostname().lower().replace('-', '_')}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(
            pgsql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                pgsql.Identifier(nombre), pgsql.Identifier(plantilla)
            )
        )
        conn = psycopg.connect(dsn, dbname=nombre, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nombre))
        )
        admin.close()


def _otra_conexion(conn, **kw) -> psycopg.Connection:
    """conn.info.dsn no trae la contrasena: el DSN de la fixture con la base
    temporal, como en CI (Postgres con contrasena)."""
    return psycopg.connect(make_conninfo(_test_dsn(), dbname=conn.info.dbname), **kw)


@contextmanager
def _como_decide(conn):
    """El rol del ciclo: lo que no le dan sus grants revienta aqui."""
    conn.execute("SET ROLE app_decide")
    try:
        yield
    finally:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        conn.execute("RESET ROLE")


# ---------------------------------------------------------------------------
# Amazon falso detras del cliente de escritura real
# ---------------------------------------------------------------------------


class _Amazon:
    """/sp/productAds: list por adIdFilter y PUT de estado. `put` = 'ok',
    'timeout_sin_aplicar' (expira sin aplicar) o 'readback_cae' (aplica y
    desde ahi todo LIST responde 500 mientras `caido`). `al_list(n)` corre
    antes de contestar el LIST numero n."""

    def __init__(self, estados: dict[str, str], *, put: str = "ok", al_list=None):
        self.estado = dict(estados)
        self.put = put
        self.al_list = al_list
        self.caido = False
        self.puts: list[tuple[str, str]] = []
        self.lists = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.method == "PUT" and request.url.path == "/sp/productAds":
            for entrada in cuerpo["productAds"]:
                ad = str(entrada["adId"])
                if self.put == "timeout_sin_aplicar":
                    raise httpx.TimeoutException("expiro sin aplicar")
                self.puts.append((ad, str(entrada["state"])))
                self.estado[ad] = str(entrada["state"])
                self.caido = self.put == "readback_cae"
            return httpx.Response(207, json={"productAds": {"error": [], "success": []}})
        if request.method == "POST" and request.url.path.endswith("/list"):
            self.lists += 1
            if self.al_list is not None:
                self.al_list(self.lists)
            if self.caido:
                return httpx.Response(500, text="Internal error")
            pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
            return httpx.Response(
                200,
                json={
                    "productAds": [
                        {
                            "adId": ad,
                            "state": self.estado[ad],
                            "adGroupId": "g1",
                            "campaignId": "c1",
                            "sku": "SS-SKU-A",
                            "asin": "ASIN-SKU-A",
                        }
                        for ad in pedidos
                        if ad in self.estado
                    ]
                },
            )
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")


def _aplicador(conn, amazon: _Amazon, ciclo_id: int, guard=None) -> Aplicador:
    return Aplicador(
        conn,
        platform=PLAT,
        profile_id=404040,
        credentials=AdsCredentials(
            client_id="fake-client-id-123",
            client_secret="fake-client-secret-XYZ",
            refresh_token="fake-refresh-token-ABC",
        ),
        cycle_id_ejecutor=ciclo_id,
        owner="test:i",
        job_key="ads_optimizer:amazon_us",
        transport=httpx.MockTransport(amazon.handler),
        sleep=lambda segundos: None,
        guard_http=guard,
    )


# ---------------------------------------------------------------------------
# Mundo: la decision del ciclo sobre pa1 y su fila de cola
# ---------------------------------------------------------------------------


def _config(conn, **extra) -> int:
    return _config_version(
        conn,
        {
            "ads_optimizer_mode": "live",
            "ads_target_acos_pct_amazon_us": 30,
            "ads_producto_pausa_amazon_us": "live",
            **LIMITES,
            "ads_apply_cap_amazon_us_pause": CAP_PAUSE,
            **extra,
        },
    )


def _goals(conn):
    campanas = [
        f[0]
        for f in conn.execute(
            "SELECT id FROM ad_entity WHERE platform = 'amazon_us' AND kind = 'campaign'"
        )
    ]
    return lee_goals(conn, PLAT, campanas)


def _ciclo_nuevo(conn) -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]


def _decision(conn) -> int:
    """La decision pause de pa1 con los inputs que congela el ciclo
    (cycle._pendientes_pausa_producto sobre la evaluacion de ahora)."""
    ahora = dt.datetime.now(dt.UTC)
    config_id, settings = conn.execute(
        "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
    ).fetchone()
    entradas = productos_io.lee_entradas_productos(
        conn, PLAT, decidido_el=ahora, settings=settings, goals=_goals(conn)
    )
    evaluaciones = tuple(p.resolver_producto(e) for e in entradas)
    (pendiente,) = ciclo._pendientes_pausa_producto(
        ciclo._ProductosCiclo(modo_meta="off", modo_pausa="live", evaluaciones=evaluaciones),
        decided_at=ahora,
    )
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs) VALUES (%s, %s, 'pause', %s, %s,"
        " %s, %s, %s, %s) RETURNING id",
        (
            _ciclo_nuevo(conn),
            pendiente.ad_entity_id,
            ahora,
            config_id,
            pendiente.data_observed_at,
            pendiente.window_start,
            pendiente.window_end,
            Json(pendiente.inputs),
        ),
    ).fetchone()[0]


def _propuesta(conn, *, hace: dt.timedelta = dt.timedelta(hours=49)) -> tuple[int, int]:
    """Decision y fila de cola encolada hace `hace` (por omision, ya vencida)
    con el modo que da la config vigente."""
    dec = _decision(conn)
    ciclo_id = conn.execute("SELECT cycle_id FROM decision WHERE id = %s", (dec,)).fetchone()[0]
    apply_cola.encola_cortes(
        conn, object(), ciclo_id, modo_envelope="live", ahora=dt.datetime.now(dt.UTC) - hace
    )
    (q,) = conn.execute("SELECT id FROM apply_queue WHERE decision_id = %s", (dec,)).fetchone()
    return dec, q


def _libera(conn, amazon: _Amazon, *, guard=None):
    ejecutor = _ciclo_nuevo(conn)
    with _como_decide(conn):
        return apply_cola.libera_vencidos(
            conn,
            PLAT,
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, amazon, ejecutor, guard),
            cycle_id=ejecutor,
        )


def _reconcilia(conn, amazon: _Amazon) -> tuple[int, int]:
    ejecutor = _ciclo_nuevo(conn)
    with _como_decide(conn):
        return reconciliacion._reconcilia_pauses(conn, _aplicador(conn, amazon, ejecutor), PLAT)


def _cuota(conn) -> list:
    return conn.execute("SELECT motor, used, cap FROM apply_quota_state ORDER BY motor").fetchall()


def _cola(conn, q: int):
    return conn.execute(
        "SELECT modo, estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
    ).fetchone()


def _intentos(conn, dec: int) -> list:
    return conn.execute(
        "SELECT tipo, quota_cobrada, resultado, finished_at IS NOT NULL, ack"
        " FROM apply_attempt WHERE decision_id = %s ORDER BY seq",
        (dec,),
    ).fetchall()


def _intenciones(conn) -> list:
    return conn.execute(
        "SELECT decision_id, cerrada_el IS NOT NULL, retiro_id FROM anuncio_retiro_intencion"
        " ORDER BY id"
    ).fetchall()


def _retiros(conn) -> list:
    return conn.execute(
        "SELECT id, motivo, autoria, actor FROM anuncio_retiro ORDER BY id"
    ).fetchall()


def _sin_cobro(conn, q: int, motivo: str, amazon: _Amazon, res) -> None:
    """Descarte antes de cobrar: la fila discarded con su motivo, ni PUT ni
    fila de cuota ni intento."""
    assert res.descartadas == [motivo]
    assert (res.aplicadas, res.fallidas) == (0, 0)
    assert _cola(conn, q) == ("live", "discarded", motivo)
    assert amazon.puts == []
    assert _cuota(conn) == []
    assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone() == (0,)


# ---------------------------------------------------------------------------
# I.3/I.4: pausa confirmada, cuota y sellos
# ---------------------------------------------------------------------------


def test_pausa_confirmada_cobra_una_cuota_de_pause_y_sella_finished_at_y_verify_ok(plantilla):
    with _db(plantilla, "orbit_i_exito") as conn:
        m = _mundo_pausa(conn)
        _config(conn)
        dec, q = _propuesta(conn)
        amazon = _Amazon({"pa1": "ENABLED"})
        res = _libera(conn, amazon)
        assert (res.liberadas, res.aplicadas, res.fallidas, res.descartadas) == (1, 1, 0, [])
        assert amazon.puts == [("pa1", "PAUSED")]
        assert _cuota(conn) == [(MOTOR_PAUSE, 1, CAP_PAUSE)]
        [(rid, motivo, autoria, actor)] = _retiros(conn)
        assert (motivo, autoria, actor) == ("producto_sin_ventas", "confirmada", f"apply_queue:{q}")
        assert _intenciones(conn) == [(dec, True, rid)]
        assert _intentos(conn, dec) == [
            ("normal", True, "ok", True, {"retiro_id": rid, "resultado": "retirado"})
        ]
        ejecutor = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        assert conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone() == (True, ejecutor)
        assert _cola(conn, q) == ("live", "applied", None)
        assert conn.execute(
            "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s", (m["pa"],)
        ).fetchone() == ("PAUSED",)


def test_pausa_confirmada_y_muerte_antes_del_cierre_se_reconcilia_sin_put_ni_cuota(
    plantilla, monkeypatch
):
    with _db(plantilla, "orbit_i_muere_cierre") as conn:
        _mundo_pausa(conn)
        _config(conn)
        dec, q = _propuesta(conn)
        amazon = _Amazon({"pa1": "ENABLED"})

        def muere(*a, **k):
            raise RuntimeError("el proceso muere antes del cierre")

        monkeypatch.setattr(apply_producto, "_sella_aplicada", muere)
        with pytest.raises(RuntimeError, match="muere antes del cierre"):
            _libera(conn, amazon)
        monkeypatch.undo()
        [(rid, *_)] = _retiros(conn)
        assert _intenciones(conn) == [(dec, True, rid)]
        assert _cola(conn, q)[1] == "applying"
        assert _intentos(conn, dec) == [("normal", True, None, False, None)]
        lists_antes = amazon.lists
        assert _reconcilia(conn, amazon) == (1, 0)
        assert amazon.puts == [("pa1", "PAUSED")]
        assert amazon.lists == lists_antes
        assert _cuota(conn) == [(MOTOR_PAUSE, 1, CAP_PAUSE)]
        assert _cola(conn, q) == ("live", "applied", None)
        assert _intentos(conn, dec) == [
            ("normal", True, "ok", True, {"retiro_id": rid, "resultado": "reconciliado"})
        ]
        assert conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone() == (True,)


@pytest.mark.parametrize(
    ("put", "desenlace", "estado_cola"),
    [("readback_cae", (1, 0), "applied"), ("timeout_sin_aplicar", (0, 1), "failed")],
)
def test_put_incierto_se_reconcilia_por_la_intencion_sin_otro_put(
    plantilla, put, desenlace, estado_cola
):
    with _db(plantilla, f"orbit_i_incierto_{put[:7]}") as conn:
        _mundo_pausa(conn)
        _config(conn)
        dec, q = _propuesta(conn)
        amazon = _Amazon({"pa1": "ENABLED"}, put=put)
        res = _libera(conn, amazon)
        assert (res.aplicadas, res.fallidas) == (0, 0)
        assert _cola(conn, q)[1] == "applying"
        assert _intenciones(conn) == [(dec, False, None)]
        puts = list(amazon.puts)
        amazon.put, amazon.caido = "ok", False
        assert _reconcilia(conn, amazon) == desenlace
        assert amazon.puts == puts
        assert _cuota(conn) == [(MOTOR_PAUSE, 1, CAP_PAUSE)]
        assert _cola(conn, q)[1] == estado_cola
        retiros = _retiros(conn)
        if estado_cola == "applied":
            [(rid, _m, autoria, _a)] = retiros
            assert autoria == "adoptada"
            assert _intenciones(conn) == [(dec, True, rid)]
        else:
            assert retiros == []
            assert _intenciones(conn) == [(dec, True, None)]
            assert _intentos(conn, dec)[0][2:4] == ("fallo:reconciliado_sin_retiro", True)


# ---------------------------------------------------------------------------
# I.3: descartes antes de cobrar
# ---------------------------------------------------------------------------


def test_mezcla_de_productos_cambiada_descarta_antes_de_cobrar(plantilla):
    with _db(plantilla, "orbit_i_mezcla") as conn:
        m = _mundo_pausa(conn)
        _config(conn)
        _dec, q = _propuesta(conn)
        pid_otro = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('SKU-Z', 'SKU-Z') RETURNING id"
        ).fetchone()[0]
        lid_otro = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'ASIN-SKU-Z', 'SS-SKU-Z') RETURNING id",
            (pid_otro,),
        ).fetchone()[0]
        _anuncio(conn, m["ag"], lid_otro, "pa-z", "ENABLED")
        amazon = _Amazon({"pa1": "ENABLED"})
        _sin_cobro(conn, q, "alcance_cambio", amazon, _libera(conn, amazon))
        assert amazon.lists == 0


def test_impulso_abierto_descarta(plantilla):
    with _db(plantilla, "orbit_i_impulso") as conn:
        m = _mundo_pausa(conn)
        _config(conn)
        _dec, q = _propuesta(conn)
        _impulso_abierto(conn, m["pid"], m["lid"])
        amazon = _Amazon({"pa1": "ENABLED"})
        _sin_cobro(conn, q, "impulso_abierto", amazon, _libera(conn, amazon))
        assert amazon.lists == 0


def test_pausa_en_off_descarta_las_pendientes(plantilla):
    with _db(plantilla, "orbit_i_off") as conn:
        _mundo_pausa(conn)
        _config(conn)
        _dec, q = _propuesta(conn)
        _config(conn, ads_producto_pausa_amazon_us=None)
        amazon = _Amazon({"pa1": "ENABLED"})
        _sin_cobro(conn, q, "modo_no_live", amazon, _libera(conn, amazon))
        assert amazon.lists == 0


def test_regresado_antes_del_envio_descarta_con_regresado_por_ti(plantilla):
    """H.7: lo regresaste (reposicion de hace una hora) mientras la fila
    esperaba su veto: la gracia de 7 dias la descarta antes de cobrar."""
    with _db(plantilla, "orbit_i_regresado") as conn:
        m = _mundo_pausa(conn)
        _config(conn)
        _dec, q = _propuesta(conn)
        _repone(conn, _retiro(conn, m["pa"], hace="20 days"), hace="1 hour")
        amazon = _Amazon({"pa1": "ENABLED"})
        _sin_cobro(conn, q, "regresado_por_ti", amazon, _libera(conn, amazon))
        assert amazon.lists == 0


def test_reactivado_en_amazon_con_cache_anterior_al_retiro_se_descarta_sin_put(plantilla):
    """El retiro sigue abierto y el cache es anterior a el (el sync todavia
    no lo vio): la lectura viva lo da ENABLED, asi que lo reactivaste en
    Amazon. Se descarta con regresado_por_ti antes de cobrar."""
    with _db(plantilla, "orbit_i_reactivado_cache") as conn:
        m = _mundo_pausa(conn)
        _config(conn)
        _dec, q = _propuesta(conn)
        _retiro(conn, m["pa"], hace="1 hour")
        conn.execute(
            "UPDATE ad_entity_state SET synced_at = now() - interval '2 days'"
            " WHERE ad_entity_id = %s",
            (m["pa"],),
        )
        amazon = _Amazon({"pa1": "ENABLED"})
        _sin_cobro(conn, q, "regresado_por_ti", amazon, _libera(conn, amazon))
        assert amazon.lists == 1


# ---------------------------------------------------------------------------
# I.3: el dueno perdido y la muerte antes de la intencion
# ---------------------------------------------------------------------------


def test_perder_el_dueno_tras_revalidar_y_antes_del_put_no_muta_ni_cobra_otra_cuota(plantilla):
    with _db(plantilla, "orbit_i_dueno") as conn:
        _mundo_pausa(conn)
        _config(conn)
        dec, q = _propuesta(conn)
        perdido = {"si": False}

        def guard():
            if perdido["si"]:
                raise ciclo.ApplyAbortado("lock ads_optimizer:amazon_us ya no es nuestro")

        def tras_revalidar(n: int) -> None:
            perdido["si"] = True  # el LIST 1 es el de la revalidacion

        amazon = _Amazon({"pa1": "ENABLED"}, al_list=tras_revalidar)
        with pytest.raises(ciclo.ApplyAbortado):
            _libera(conn, amazon, guard=guard)
        assert (amazon.puts, amazon.lists) == ([], 1)
        assert _cuota(conn) == [(MOTOR_PAUSE, 1, CAP_PAUSE)]
        assert _intenciones(conn) == []
        assert _intentos(conn, dec) == [("normal", True, None, False, None)]
        assert _cola(conn, q)[1] == "applying"
        sucesor = _Amazon({"pa1": "ENABLED"})
        assert _reconcilia(conn, sucesor) == (0, 1)
        assert (sucesor.puts, sucesor.lists) == ([], 0)
        assert _cuota(conn) == [(MOTOR_PAUSE, 1, CAP_PAUSE)]
        assert _cola(conn, q)[1] == "failed"
        assert _intentos(conn, dec) == [
            ("normal", True, "fallo:sin_intencion_sin_http", True, None)
        ]


def test_muerte_tras_el_commit_del_intento_y_antes_de_la_intencion_cierra_failed_sin_cuota(
    plantilla, monkeypatch
):
    with _db(plantilla, "orbit_i_muere_intencion") as conn:
        _mundo_pausa(conn)
        _config(conn)
        dec, q = _propuesta(conn)
        amazon = _Amazon({"pa1": "ENABLED"})

        def muere(*a, **k):
            raise RuntimeError("el proceso muere antes de abrir la intencion")

        monkeypatch.setattr(ra, "_abre_intencion", muere)
        with pytest.raises(RuntimeError, match="antes de abrir la intencion"):
            _libera(conn, amazon)
        monkeypatch.undo()
        assert amazon.puts == []
        assert _intenciones(conn) == []
        assert _intentos(conn, dec) == [("normal", True, None, False, None)]
        lists_antes = amazon.lists
        assert _reconcilia(conn, amazon) == (0, 1)
        assert (amazon.puts, amazon.lists) == ([], lists_antes)
        assert _cuota(conn) == [(MOTOR_PAUSE, 1, CAP_PAUSE)]
        assert _cola(conn, q)[1] == "failed"
        assert _intentos(conn, dec)[0][2:4] == ("fallo:sin_intencion_sin_http", True)


# ---------------------------------------------------------------------------
# H.7 + I.3: reactivacion manual y gracia
# ---------------------------------------------------------------------------


def test_retiro_reactivacion_manual_y_8_dias_despues_put_nuevo_y_retiro_nuevo(plantilla):
    with _db(plantilla, "orbit_i_reactiva_8") as conn:
        m = _mundo_pausa(conn)
        _config(conn)
        _dec1, q1 = _propuesta(conn)
        amazon = _Amazon({"pa1": "ENABLED"})
        assert _libera(conn, amazon).aplicadas == 1
        [(retiro1, *_)] = _retiros(conn)
        # La reactivas en Amazon y el sync lo ve ENABLED despues del retiro.
        amazon.estado["pa1"] = "ENABLED"
        conn.execute(
            "UPDATE ad_entity_state SET status = 'ENABLED', synced_at = now()"
            " WHERE ad_entity_id = %s",
            (m["pa"],),
        )
        with _como_decide(conn):
            (entrada,) = productos_io.lee_entradas_productos(
                conn,
                PLAT,
                decidido_el=dt.datetime.now(dt.UTC),
                settings={},
                goals=_goals(conn),
            )
            assert entrada.excluidos == (p.AnuncioExcluido(m["pa"], "regresado_por_ti", retiro1),)
            assert conn.execute("SELECT cierra_retiro_reactivado(%s)", (retiro1,)).fetchone()[0]
        assert conn.execute("SELECT retiro_id, actor FROM anuncio_reposicion").fetchall() == [
            (retiro1, "reactivacion_manual_amazon")
        ]
        # 8 dias despues (la reposicion es append-only: se adelanta el reloj
        # moviendo su repuesto_el, sin triggers).
        conn.execute("SET session_replication_role = replica")
        conn.execute("UPDATE anuncio_reposicion SET repuesto_el = repuesto_el - interval '8 days'")
        conn.execute("SET session_replication_role = origin")
        dec2, q2 = _propuesta(conn)
        res = _libera(conn, amazon)
        assert res.aplicadas == 1
        assert amazon.puts == [("pa1", "PAUSED"), ("pa1", "PAUSED")]
        [(r1, *_), (retiro2, motivo, autoria, actor)] = _retiros(conn)
        assert r1 == retiro1 and retiro2 != retiro1
        assert (motivo, autoria, actor) == (
            "producto_sin_ventas",
            "confirmada",
            f"apply_queue:{q2}",
        )
        assert [(_cola(conn, q)[1]) for q in (q1, q2)] == ["applied", "applied"]
        assert _intenciones(conn)[-1] == (dec2, True, retiro2)


# ---------------------------------------------------------------------------
# I.1: la tabla temporal homonima no oculta el retiro abierto
# ---------------------------------------------------------------------------


def test_tabla_temporal_homonima_vacia_no_deja_sellar_un_segundo_retiro_abierto(plantilla):
    with _db(plantilla, "orbit_i_temporal") as conn:
        m = _mundo_pausa(conn)
        _retiro(conn, m["pa"], hace="1 hour")
        with _como_decide(conn):
            conn.execute("CREATE TEMP TABLE anuncio_retiro (id bigint, ad_entity_id bigint)")
            conn.execute("CREATE TEMP TABLE anuncio_reposicion (retiro_id bigint)")
            with pytest.raises(psycopg.errors.RaiseException, match="ya tiene un retiro abierto"):
                conn.execute(
                    "INSERT INTO public.anuncio_retiro (platform, motivo, modo, lote_retiro,"
                    " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
                    " reversa, retirado_el, actor) VALUES ('amazon_us', 'sin_pedidos', 'pausa',"
                    " 'lote-temp', %s, 'pa1', 'g1', 'c1', 'SS-SKU-A', 'ASIN-SKU-A',"
                    " 'state=ENABLED', now(), 'app_decide')",
                    (m["pa"],),
                )
        assert conn.execute("SELECT count(*) FROM public.anuncio_retiro").fetchone() == (1,)


# ---------------------------------------------------------------------------
# I.5: subida omitida y bajada aplicada en el grupo exclusivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("compartido", "omitida"), [(False, True), (True, False)])
def test_grupo_exclusivo_con_pausa_en_vuelo_omite_la_subida_y_aplica_la_bajada(
    monkeypatch, compartido, omitida
):
    with _db_ciclo(f"orbit_i5_{'comp' if compartido else 'excl'}") as (conn, _extra):
        ids = _siembra_maestra(conn, settings={"ads_bid_politica_amazon_us": "niveles_v3"})
        listings = []
        for sku in ("SKU-P", "SKU-Q") if compartido else ("SKU-P",):
            pid = conn.execute(
                "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
            ).fetchone()[0]
            listings.append(
                conn.execute(
                    "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
                    " VALUES (%s, 'amazon_us', %s, %s) RETURNING id",
                    (pid, f"ASIN-{sku}", f"SS-{sku}"),
                ).fetchone()[0]
            )
        anuncios = []
        for n, lid in enumerate(listings):
            pa = _entidad(conn, PLAT, "product_ad", f"pa-{n}", parent=ids["ag"])
            conn.execute("UPDATE ad_entity SET listing_id = %s WHERE id = %s", (lid, pa))
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', now())",
                (pa,),
            )
            anuncios.append(pa)
        previo = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform, status) VALUES ('live', 'amazon_us',"
            " 'done') RETURNING id"
        ).fetchone()[0]
        dec = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
            " data_observed_at, window_start, window_end, inputs) VALUES (%s, %s, 'pause', %s,"
            " %s, %s, %s, '{}'::jsonb) RETURNING id",
            (
                previo,
                anuncios[0],
                ids["config_id"],
                DECIDED_AT - dt.timedelta(days=40),
                (DECIDED_AT - dt.timedelta(days=60)).date(),
                (DECIDED_AT - dt.timedelta(days=31)).date(),
            ),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo, estado,"
            " vence_el, request_payload) VALUES ('amazon_us', %s, 'pause', %s, 'live',"
            " 'pending_veto', now() + interval '40 hours', '{\"adId\": \"pa-0\"}'::jsonb)",
            (anuncios[0], dec),
        )

        def decide(caso):
            if caso.hoja_id == ids["kw_bid"]:
                return Mover(Decimal("1.20"), Decimal("1.20"), "subida", "hoja")
            return Mover(Decimal("0.80"), Decimal("0.80"), "recorte", "hoja")

        monkeypatch.setattr(ciclo, "decide", decide)
        res = _corre_ciclo_fijo(conn)
        bids = conn.execute(
            "SELECT ad_entity_id, new_value FROM decision WHERE cycle_id = %s AND kind = 'bid'"
            " ORDER BY ad_entity_id",
            (res.cycle_id,),
        ).fetchall()
        skips = json.loads(res.notes)["skips"]["entidad"]
        if omitida:
            assert bids == [(ids["kw_pause"], Decimal("0.80"))]
            assert skips.get("pausa_producto_pendiente") == 1
        else:
            assert bids == [(ids["kw_bid"], Decimal("1.20")), (ids["kw_pause"], Decimal("0.80"))]
            assert "pausa_producto_pendiente" not in skips


# ---------------------------------------------------------------------------
# I.7: encender cierra la practica (S18)
# ---------------------------------------------------------------------------


def _config_practica(conn, **extra) -> int:
    return _config(conn, ads_producto_pausa_amazon_us="shadow", **extra)


def _vigente(conn) -> int:
    return conn.execute("SELECT max(id) FROM config_version").fetchone()[0]


def _enciende(conn, modo: str = "live") -> dict:
    otra = _otra_conexion(conn)
    try:
        return productos_modo.cambia_pausa(
            otra, PLAT, modo, _vigente(conn), dt.datetime.now(dt.UTC), actor="test"
        )
    finally:
        otra.close()


def _cola_producto(conn) -> list:
    return conn.execute(
        "SELECT q.modo, q.estado, q.discard_motivo FROM apply_queue q"
        " JOIN ad_entity e ON e.id = q.ad_entity_id AND e.kind = 'product_ad' ORDER BY q.id"
    ).fetchall()


def _ciclo_vivo(conn):
    amazon = _Amazon({})

    def fabrica(conn, *, platform, cycle_id_ejecutor, owner, job_key, tick, transport=None):
        return _aplicador(conn, amazon, cycle_id_ejecutor, guard=tick)

    res = ciclo.corre_ciclo(
        conn,
        platform=PLAT,
        owner="test-host:i7",
        decided_at=dt.datetime.now(dt.UTC),
        heartbeat_cada=1,
        aplicador_factory=fabrica,
    )
    assert res.status == "done", res.notes
    assert amazon.puts == []
    return res


def test_practica_encender_deja_cero_practicas_y_el_ciclo_siguiente_encola_la_real(plantilla):
    with _db(plantilla, "orbit_i7_enciende") as conn:
        _mundo_pausa(conn)
        _config_practica(conn)
        _ciclo_vivo(conn)
        assert _cola_producto(conn) == [("shadow", "pending_veto", None)]
        r = _enciende(conn)
        assert (r["config_escrita"], r["practicas_antes"], r["practicas_descartadas"]) == (
            True,
            1,
            1,
        )
        assert r["practicas_despues"] == 0
        assert _cola_producto(conn) == [("shadow", "discarded", "encendido_pausa_producto")]
        res = _ciclo_vivo(conn)
        assert "clave_en_vuelo" not in json.loads(res.notes)["productos"]["anuncios_excluidos"]
        assert _cola_producto(conn) == [
            ("shadow", "discarded", "encendido_pausa_producto"),
            ("live", "pending_veto", None),
        ]


def test_practica_encolado_que_espera_el_candado_entra_como_fila_en_vivo(plantilla):
    with _db(plantilla, "orbit_i7_candado") as conn:
        _mundo_pausa(conn)
        _config_practica(conn)
        dec = _decision(conn)
        ciclo_id = conn.execute("SELECT cycle_id FROM decision WHERE id = %s", (dec,)).fetchone()[0]
        enciende = _otra_conexion(conn)
        encola = _otra_conexion(conn)
        resultado: dict = {}
        try:
            enciende.execute(productos_modo._SQL_CANDADO, (PLAT,))

            def encolar():
                try:
                    with encola.transaction():
                        resultado["r"] = apply_cola.encola_cortes(
                            encola,
                            object(),
                            ciclo_id,
                            modo_envelope="live",
                            ahora=dt.datetime.now(dt.UTC),
                        )
                except Exception as exc:  # noqa: BLE001 - se reporta en el assert
                    resultado["error"] = exc

            hilo = threading.Thread(target=encolar)
            hilo.start()
            esperando = False
            for _ in range(100):
                esperando = (
                    conn.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                        " AND query LIKE %s",
                        ("%advisory_xact_lock_shared%",),
                    ).fetchone()[0]
                    == 1
                )
                if esperando:
                    break
                time.sleep(0.05)
            assert esperando, "el encolado no espero el candado"
            r = productos_modo.cambia_pausa(
                enciende, PLAT, "live", _vigente(conn), dt.datetime.now(dt.UTC), actor="test"
            )
            hilo.join(10)
            assert not hilo.is_alive()
        finally:
            enciende.close()
            encola.close()
        assert "error" not in resultado, resultado.get("error")
        assert r["config_escrita"] is True
        assert (resultado["r"].encoladas_live, resultado["r"].encoladas_shadow) == (1, 0)
        assert _cola_producto(conn) == [("live", "pending_veto", None)]


def test_practica_si_el_descarte_lanza_la_config_no_queda_en_vivo_y_las_practicas_siguen(
    plantilla, monkeypatch
):
    with _db(plantilla, "orbit_i7_descarte_lanza") as conn:
        _mundo_pausa(conn)
        _config_practica(conn)
        _propuesta(conn, hace=dt.timedelta(0))
        antes = _vigente(conn)
        monkeypatch.setattr(
            productos_modo, "_SQL_DESCARTA_PRACTICAS", "SELECT 1 / 0, %s::text, %s::text"
        )
        with pytest.raises(psycopg.errors.DivisionByZero):
            _enciende(conn)
        assert _vigente(conn) == antes
        settings = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (antes,)
        ).fetchone()[0]
        assert settings["ads_producto_pausa_amazon_us"] == "shadow"
        assert _cola_producto(conn) == [("shadow", "pending_veto", None)]


def test_practica_con_la_pausa_ya_en_vivo_repetir_descarta_las_que_queden_y_no_escribe(plantilla):
    with _db(plantilla, "orbit_i7_repite") as conn:
        _mundo_pausa(conn)
        _config_practica(conn)
        _propuesta(conn, hace=dt.timedelta(0))
        _config(conn)
        antes = _vigente(conn)
        r = _enciende(conn)
        assert (r["modo_anterior"], r["config_escrita"], r["config_version_id"]) == (
            "live",
            False,
            antes,
        )
        assert (r["practicas_antes"], r["practicas_descartadas"], r["practicas_despues"]) == (
            1,
            1,
            0,
        )
        assert _vigente(conn) == antes
        assert _cola_producto(conn) == [("shadow", "discarded", "encendido_pausa_producto")]


def test_practica_sin_limites_guardados_pausa_live_sale_con_settings_invalido_y_no_escribe(
    plantilla, monkeypatch, capsys
):
    with _db(plantilla, "orbit_i7_sin_limites") as conn:
        _mundo_pausa(conn)
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                "ads_producto_pausa_amazon_us": "shadow",
                "ads_producto_pausa_clics_amazon_us": 100,
            },
        )
        antes = _vigente(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", make_conninfo(_test_dsn(), dbname=conn.info.dbname))
        args = SimpleNamespace(platform=PLAT, meta=None, pausa="live", clics=None, gasto=None)
        assert cli_productos.corre(args) == 2
        assert "la pausa en vivo exige guardar antes los limites" in capsys.readouterr().err
        with pytest.raises(config_write.SettingsInvalido):
            _enciende(conn)
        assert _vigente(conn) == antes
