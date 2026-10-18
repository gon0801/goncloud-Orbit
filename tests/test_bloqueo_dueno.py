"""B.2: propuestas, huella, rechazo recordado y pausa.

Las pruebas 5, 6 y la mitad de propuestas de la 8 son tablas contra la
funcion pura. La consulta corre contra Postgres real. La prueba 10, en
su mitad, es que este modulo no importa app.ads.write.
"""

from __future__ import annotations

import itertools
import json
import os
import socket
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql as pgsql
from test_architecture import _imports_runtime
from test_jev_perimetro import ORDEN_PERIMETRO
from test_schema import _postgres_obligatorio_ausente

import app.bloqueo_dueno as bloqueo

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
AHORA = datetime(2026, 10, 17, 12, tzinfo=UTC)
HUELLA_BANO = "ff7fce7365be9172ac96d795821aa30a13b7740d0483f01eee37f9ccd49a1c97"
HUELLA_DUCHA = "8e0cac8dbb00f9f764ad27e325c4c978e95f8cad12702dab48473bcb79f9177a"
HUELLA_CON_NULO = "41fb9945471c1a9243df59a732c6cc7c868b25460c020a1678d088e2cafb3d0c"
HUELLA_BA = "9c09f8f86aecf8cc3c036931b3d8189c59d8dca750c336d2d4f5de2a151e5cb8"
HUELLA_AB = "2faa3dff1fa352c6cf60009128e822a2f6c16e56f792da96f22c1581c3bae71f"
HUELLA_237 = "808cd82a7c35f79def7c8e7ced8fe5a06951bd8c00b40c9bed18ef567434d494"
HUELLA_ARO = "3eb238cdf55018a0017c26392c972895873ec93c241ae70a550b744eb9f54387"
HUELLA_RING = "65f4daf2c35b6026a56edabf19b37bb57449ef3118debf3a771026ff93f9f46c"
SHA_COPIADO = "c" * 64
SHA_RING = "d" * 64

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
_CONTADOR = itertools.count()


def _ficha(
    texto: str, desconocidos: tuple[str, ...] = (), ficha_id: str = "f1"
) -> bloqueo.FichaCatalogo:
    return bloqueo.FichaCatalogo(
        hechos=({"fuente": "mano", "texto": texto},),
        desconocidos=desconocidos,
        ficha_id=ficha_id,
    )


def _producto(
    producto_id: int,
    ficha: bloqueo.FichaCatalogo | None,
    estados: tuple[str, ...] = ("ENABLED",),
) -> bloqueo.ProductoCatalogo:
    return bloqueo.ProductoCatalogo(producto_id, ficha, estados)


def _candidata(
    termino: str,
    *,
    gasto: Decimal | None,
    catalogo: tuple[bloqueo.ProductoCatalogo, ...],
    vigente: bool = True,
    lectura: str = "ajena",
    bloqueo_vivo: bool = False,
    huellas_rechazo: frozenset[str] = frozenset(),
    motor: tuple[bloqueo.FilaMotor, ...] = (),
    plataforma: str = "amazon_mx",
    grupo: int = 7,
    sha: str = SHA_COPIADO,
    senal_id: str = "11111111-1111-4111-8111-111111111111",
) -> bloqueo.Candidata:
    return bloqueo.Candidata(
        senal_id=uuid.UUID(senal_id),
        plataforma=plataforma,
        ad_group_id=grupo,
        termino=termino,
        termino_sha256=sha,
        vigente=vigente,
        lectura=lectura,
        gasto=gasto,
        clics=4,
        moneda="MXN" if plataforma == "amazon_mx" else "USD",
        catalogo=catalogo,
        bloqueo_vivo=bloqueo_vivo,
        huellas_rechazo=huellas_rechazo,
        motor=motor,
    )


def _juicio(
    tipo: str,
    minutos: int,
    *,
    n: int = 1,
    origen: uuid.UUID | None = None,
) -> bloqueo.JuicioDueno:
    return bloqueo.JuicioDueno(
        tipo=tipo,
        creado_en=AHORA + timedelta(minutes=minutos),
        id=uuid.UUID(f"{n:08d}-0000-4000-8000-000000000000"),
        origen_id=origen,
    )


def test_rechazo_recordado_por_huella():
    base = _ficha("baño", ("color",))
    mismo = _producto(7, base, ("ENABLED",))
    pausado = _producto(7, base, ("PAUSED",))
    renovada = _producto(7, _ficha("baño", ("color",), ficha_id="f2"))
    texto_nuevo = _producto(7, _ficha("ducha", ("color",)))
    con_nulo = (mismo, _producto(8, None))
    tabla = (
        ("misma huella", (mismo,), HUELLA_BANO),
        ("estado de anuncio no entra", (pausado,), HUELLA_BANO),
        ("ficha renovada con el mismo texto", (renovada,), HUELLA_BANO),
        ("texto nuevo", (texto_nuevo,), HUELLA_DUCHA),
        ("producto nuevo o sin ficha", con_nulo, HUELLA_CON_NULO),
        ("orden de desconocidos", (_producto(7, _ficha("baño", ("b", "a"))),), HUELLA_BA),
        ("orden distinta de desconocidos", (_producto(7, _ficha("baño", ("a", "b"))),), HUELLA_AB),
    )
    for nombre, catalogo, esperado in tabla:
        assert bloqueo.huella_catalogo(catalogo) == esperado, nombre
    huella = bloqueo.huella_catalogo((mismo,))
    oculta = _candidata(
        "baño", gasto=Decimal("13.6200"), catalogo=(mismo,), huellas_rechazo=frozenset({huella})
    )
    reaparece = _candidata(
        "baño",
        gasto=Decimal("13.6200"),
        catalogo=(texto_nuevo,),
        huellas_rechazo=frozenset({huella}),
    )
    assert bloqueo.proponer((oculta,), ahora=AHORA, pausadas=frozenset()) == ()
    salidas = bloqueo.proponer((reaparece,), ahora=AHORA, pausadas=frozenset())
    assert [s.termino for s in salidas] == ["baño"]
    assert salidas[0].huella_catalogo == HUELLA_DUCHA
    assert salidas[0].termino_sha256 == SHA_COPIADO


def test_huella_de_roster_grande_ordena_y_no_duplica():
    catalogo = tuple(
        _producto(i, _ficha(f"p{i}", ()), ("ENABLED", "PAUSED")) for i in range(237, 0, -1)
    )
    duplicado = catalogo + (_producto(1, _ficha("otro"), ("ARCHIVED",)),)
    assert bloqueo.huella_catalogo(catalogo) == HUELLA_237
    assert bloqueo.huella_catalogo(duplicado) == HUELLA_237


def test_parser_de_censo_usa_posicion_1_y_ficha_paralela():
    ficha_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    miembros = {
        "censo": [
            [["ad-9"], 9, [11], ["PAUSED"]],
            [["ad-2"], 2, [22], ["ENABLED"]],
            [["ad-9b"], 9, [33], ["ENABLED"]],
        ],
        "fichas": [ficha_id, None, ficha_id],
    }
    fichas = {ficha_id: _ficha("baño", ("color",), ficha_id)}
    catalogo = bloqueo.catalogo_de_miembros(miembros, fichas)
    assert [p.producto_id for p in catalogo] == [9, 2]
    assert catalogo[1].ficha is None
    assert catalogo[0].estados == ("PAUSED",)
    assert bloqueo.huella_catalogo(catalogo) == bloqueo.huella_catalogo(
        (_producto(2, None, ()), _producto(9, fichas[ficha_id], ()))
    )


def test_pausa_por_proporcion_de_juicios():
    def serie(
        n_aprobados: int, n_rechazados: int, inicio: int = 1
    ) -> tuple[bloqueo.JuicioDueno, ...]:
        juicios = [_juicio("aprobado", i, n=i) for i in range(inicio, inicio + n_aprobados)]
        base = inicio + n_aprobados
        juicios += [_juicio("rechazado", base + i, n=base + i) for i in range(n_rechazados)]
        return tuple(juicios)

    trece = serie(13, 7)
    catorce = serie(14, 6)
    assert bloqueo.pausa_de(trece) == bloqueo.Pausa(True, 13, 20)
    assert bloqueo.pausa_de(catorce) == bloqueo.Pausa(False, 14, 20)
    reanudada = serie(13, 7) + (_juicio("reanudacion", 30, n=90),)
    assert bloqueo.pausa_de(reanudada) == bloqueo.Pausa(False, 0, 0)
    assert bloqueo.pausa_de(serie(0, 19)) == bloqueo.Pausa(False, 0, 19)
    cola = serie(0, 1, inicio=1) + serie(21, 9, inicio=10)
    assert bloqueo.pausa_de(cola) == bloqueo.Pausa(False, 21, 30)
    aprobado = _juicio("aprobado", 1, n=1)
    quitado = bloqueo.JuicioDueno(
        tipo="quitado",
        creado_en=AHORA + timedelta(minutes=50),
        id=uuid.UUID("00000032-0000-4000-8000-000000000000"),
        origen_id=aprobado.id,
    )
    con_quitado = (aprobado,) + serie(13, 6, inicio=2) + (quitado,)
    assert bloqueo.pausa_de(con_quitado) == bloqueo.Pausa(True, 13, 20)


def test_motor_oculta_la_propuesta_en_los_estados_del_spec():
    def fila(estado: str, *, reversa: bool = False, dias: int = 2) -> bloqueo.FilaMotor:
        return bloqueo.FilaMotor(estado, AHORA + timedelta(days=dias), reversa)

    tabla = (
        ("pending_veto", (fila("pending_veto"),), True),
        ("released", (fila("released"),), True),
        ("applying", (fila("applying"),), True),
        ("applied sin reversa", (fila("applied"),), True),
        ("vetoed dentro de 30 dias", (fila("vetoed", dias=29),), True),
        ("applied revertido", (fila("applied", reversa=True),), False),
        ("vetoed de hace mas de 30 dias", (fila("vetoed", dias=-31),), False),
        ("failed", (fila("failed"),), False),
        ("discarded", (fila("discarded"),), False),
        ("sin fila", (), False),
    )
    for nombre, filas, esperado in tabla:
        assert bloqueo.motor_oculta(filas, AHORA) is esperado, nombre
    catalogo = (_producto(7, _ficha("baño", ("color",))),)
    visible = _candidata("baño", gasto=Decimal("3"), catalogo=catalogo)
    oculta = _candidata(
        "baño",
        gasto=Decimal("9"),
        catalogo=catalogo,
        motor=(bloqueo.FilaMotor("pending_veto", AHORA + timedelta(days=2), False),),
        senal_id="22222222-2222-4222-8222-222222222222",
    )
    salidas = bloqueo.proponer((oculta, visible), ahora=AHORA, pausadas=frozenset())
    assert [s.termino_sha256 for s in salidas] == [SHA_COPIADO]
    assert salidas[0].gasto == Decimal("3")


def test_proponer_aplica_las_cinco_y_ordena_por_gasto():
    catalogo = (_producto(7, _ficha("baño", ("color",))),)
    alta = _candidata(
        "baño",
        gasto=Decimal("20.00"),
        catalogo=catalogo,
        senal_id="33333333-3333-4333-8333-333333333333",
    )
    baja = _candidata(
        "aro",
        gasto=Decimal("1.00"),
        catalogo=catalogo,
        plataforma="amazon_us",
        grupo=8,
        sha=SHA_RING,
        senal_id="44444444-4444-4444-8444-444444444444",
    )
    sin_gasto = _candidata(
        "sin dato",
        gasto=None,
        catalogo=catalogo,
        grupo=9,
        sha="e" * 64,
        senal_id="55555555-5555-4555-8555-555555555555",
    )
    no_vigente = _candidata(
        "vieja", gasto=Decimal("99"), catalogo=catalogo, vigente=False, grupo=10
    )
    no_ajena = _candidata(
        "vende", gasto=Decimal("99"), catalogo=catalogo, lectura="vendio_aqui", grupo=11
    )
    viva = _candidata("viva", gasto=Decimal("99"), catalogo=catalogo, bloqueo_vivo=True, grupo=12)
    pausada = _candidata(
        "pausada", gasto=Decimal("99"), catalogo=catalogo, plataforma="amazon_us", grupo=13
    )
    salidas = bloqueo.proponer(
        (baja, sin_gasto, alta, no_vigente, no_ajena, viva, pausada),
        ahora=AHORA,
        pausadas=frozenset({"amazon_us"}),
    )
    assert [(s.termino, s.gasto) for s in salidas] == [
        ("baño", Decimal("20.00")),
        ("sin dato", None),
    ]


def test_no_importa_ads_write():
    ruta = Path(bloqueo.__file__)
    imports = _imports_runtime(ruta)
    assert "app.ads.write" not in imports
    assert not any(nombre == "jev" or nombre.startswith(("jev_", "app.jev")) for nombre in imports)


@contextmanager
def _db():
    dsn = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
    partes = urlsplit(dsn)
    db = f"b2_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(
            urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment)),
            autocommit=True,
        )
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_PERIMETRO:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _grupo(conn, plataforma: str) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'ad_group', %s) RETURNING id",
        (plataforma, f"ag-{uuid.uuid4()}"),
    ).fetchone()[0]


def _lote(conn) -> uuid.UUID:
    lote = uuid.uuid4()
    conn.execute(
        "INSERT INTO jev_revision (solicitud, sujeto_tipo, censos, contrato)"
        " VALUES (%s, 'lote', '{}'::jsonb, '{}'::jsonb)",
        (lote,),
    )
    return lote


def _senal_ajena(
    conn,
    grupo: int,
    termino: str,
    sha: str,
    roster: str,
    *,
    plataforma: str,
    gasto: Decimal | None,
    lote: uuid.UUID,
    miembros: int,
    vigente: bool = True,
    lectura: str = "ajena",
) -> uuid.UUID:
    senal = uuid.uuid4()
    ahora = conn.execute("SELECT now()").fetchone()[0]
    moneda = "MXN" if plataforma == "amazon_mx" else "USD"
    relevancia = "ajena" if lectura == "ajena" else "corresponde"
    conn.execute(
        "INSERT INTO jev_senal (id, lote_id, plataforma, ad_group_id, termino,"
        " termino_sha256, insumos_sha256, regla_version, roster_sha256,"
        " roster_probado, roster_prueba, contrato_sha256, relevancia, motivos_jev,"
        " productos_ok, evaluados, miembros, juicio_ids, ventana_inicio, ventana_fin,"
        " moneda, clics, gasto, ordenes, otros_que_venden, ordenes_otros, otros_sin_dato,"
        " ordenes_historial, lectura, motivos_lectura, valida_hasta)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, true, '{}'::jsonb, %s, %s, '{}',"
        " '{}', %s, %s, '{}', '2026-08-03', '2026-09-01', %s, 4, %s, 0, '[]'::jsonb,"
        " 0, 0, 0, %s, '{}', %s)",
        (
            senal,
            lote,
            plataforma,
            grupo,
            termino,
            sha,
            uuid.uuid4().hex,
            roster,
            "ab" * 32,
            relevancia,
            miembros,
            miembros,
            moneda,
            gasto,
            lectura,
            ahora + timedelta(hours=36 if vigente else -1),
        ),
    )
    return senal


def _roster(conn, grupo: int, plataforma: str, miembros: dict, sha: str) -> None:
    conn.execute(
        "INSERT INTO jev_roster (sha256, plataforma, ad_group_id, miembros, ficha_version_ids)"
        " VALUES (%s, %s, %s, %s::jsonb, %s)",
        (
            sha,
            plataforma,
            grupo,
            json.dumps(miembros),
            [x for x in miembros["fichas"] if x],
        ),
    )


def _ficha_real(conn, plataforma: str, ficha_id: str, texto: str) -> int:
    producto = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (ficha_id[:8], ficha_id[:8]),
    ).fetchone()[0]
    listing = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id) VALUES (%s, %s, %s) RETURNING id",
        (producto, plataforma, f"B0{producto:08X}"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO jev_ficha_version (id, producto_id, plataforma, listings, hechos,"
        " desconocidos, sha256, aprobador, observado_at, revisar_antes_de)"
        " VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, 'aprobador', %s, %s)",
        (
            ficha_id,
            producto,
            plataforma,
            [listing],
            json.dumps([{"fuente": "mano", "texto": texto}]),
            ["metal"] if texto == "aro" else [],
            uuid.uuid4().hex + uuid.uuid4().hex[:32],
            AHORA,
            AHORA + timedelta(days=30),
        ),
    )
    return producto


def _cola(
    conn,
    grupo: int,
    plataforma: str,
    termino: str,
    *,
    estado: str = "pending_veto",
    reversa: bool = False,
) -> None:
    config = conn.execute(
        "INSERT INTO config_version (settings) VALUES ('{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', %s) RETURNING id",
        (plataforma,),
    ).fetchone()[0]
    decision = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, inputs)"
        " VALUES (%s, %s, 'negative', now(), %s, now(), '2026-08-03', '2026-09-01', %s, '{}')"
        " RETURNING id",
        (ciclo, grupo, config, termino),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, search_term, kind, decision_id,"
        " modo, estado, vence_el, request_payload)"
        " VALUES (%s, %s, %s, 'negative', %s, 'live', 'pending_veto',"
        " now() + interval '2 days', '{}')",
        (plataforma, grupo, termino, decision),
    )
    fila = conn.execute(
        "SELECT id FROM apply_queue WHERE decision_id = %s", (decision,)
    ).fetchone()[0]
    for paso in {
        "released": ("released",),
        "applied": ("released", "applying", "applied"),
    }.get(estado, ()):
        conn.execute("UPDATE apply_queue SET estado = %s WHERE id = %s", (paso, fila))
    if reversa:
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, resultado) VALUES (%s, 1, 'reversa', '{}', false, 'ok')",
            (decision,),
        )


@_skip_db
def test_consulta_de_propuestas_en_postgres():
    ficha_mx = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ficha_us = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    with _db() as conn:
        lote = _lote(conn)
        grupo_mx = _grupo(conn, "amazon_mx")
        grupo_us = _grupo(conn, "amazon_us")
        grupo_vivo = _grupo(conn, "amazon_mx")
        producto_mx = _ficha_real(conn, "amazon_mx", ficha_mx, "aro")
        _ficha_real(conn, "amazon_us", ficha_us, "ring")
        miembros_mx = {
            "censo": [
                [["ad-1"], producto_mx, [1], ["ENABLED"]],
                [["ad-2"], producto_mx + 1, [2], ["PAUSED"]],
            ],
            "fichas": [ficha_mx, None],
        }
        miembros_us = {
            "censo": [[["ad-3"], 3, [3], ["ENABLED"]]],
            "fichas": [ficha_us],
        }
        _roster(conn, grupo_mx, "amazon_mx", miembros_mx, "a" * 64)
        _roster(conn, grupo_us, "amazon_us", miembros_us, "b" * 64)
        _roster(conn, grupo_vivo, "amazon_mx", miembros_mx, "9" * 64)
        senal_mx = _senal_ajena(
            conn,
            grupo_mx,
            "baño",
            SHA_COPIADO,
            "a" * 64,
            plataforma="amazon_mx",
            gasto=Decimal("20.0000"),
            lote=lote,
            miembros=2,
        )
        _senal_ajena(
            conn,
            grupo_us,
            "ring",
            SHA_RING,
            "b" * 64,
            plataforma="amazon_us",
            gasto=Decimal("5.0000"),
            lote=lote,
            miembros=1,
        )
        senal_viva = _senal_ajena(
            conn,
            grupo_vivo,
            "viva",
            "e" * 64,
            "9" * 64,
            plataforma="amazon_mx",
            gasto=Decimal("99.0000"),
            lote=lote,
            miembros=2,
        )
        grupo_viejo = _grupo(conn, "amazon_mx")
        _roster(conn, grupo_viejo, "amazon_mx", miembros_mx, "8" * 64)
        _senal_ajena(
            conn,
            grupo_viejo,
            "vieja",
            "f" * 64,
            "8" * 64,
            plataforma="amazon_mx",
            gasto=Decimal("50"),
            lote=lote,
            miembros=2,
            vigente=False,
        )
        assert producto_mx == 1
        grupo_corta = _grupo(conn, "amazon_mx")
        grupo_vuelve = _grupo(conn, "amazon_mx")
        _roster(conn, grupo_corta, "amazon_mx", miembros_us, "7" * 64)
        _roster(conn, grupo_vuelve, "amazon_mx", miembros_us, "6" * 64)
        _senal_ajena(
            conn,
            grupo_corta,
            "cortada",
            "1" * 64,
            "7" * 64,
            plataforma="amazon_mx",
            gasto=Decimal("30.0000"),
            lote=lote,
            miembros=1,
        )
        _senal_ajena(
            conn,
            grupo_vuelve,
            "vuelve",
            "2" * 64,
            "6" * 64,
            plataforma="amazon_mx",
            gasto=Decimal("8.0000"),
            lote=lote,
            miembros=1,
        )
        _cola(conn, grupo_corta, "amazon_mx", "cortada", estado="applied")
        _cola(conn, grupo_vuelve, "amazon_mx", "vuelve", estado="applied", reversa=True)
        aprobado = uuid.uuid4()
        conn.execute(
            "INSERT INTO bloqueo_veredicto (id, plataforma, ad_group_id, termino,"
            " termino_sha256, senal_id, huella_catalogo, tipo)"
            " VALUES (%s, 'amazon_mx', %s, 'viva', %s, %s, %s, 'aprobado')",
            (aprobado, grupo_vivo, "e" * 64, senal_viva, HUELLA_ARO),
        )
        conn.execute(
            "INSERT INTO bloqueo_efecto (id, veredicto_id, paso) VALUES (%s, %s, 'intencion')",
            (uuid.uuid4(), aprobado),
        )
        primeras = bloqueo.lee_propuestas(conn)
        assert [(p.termino, p.huella_catalogo, p.termino_sha256, p.gasto) for p in primeras] == [
            ("baño", HUELLA_ARO, SHA_COPIADO, Decimal("20.0000")),
            ("vuelve", HUELLA_RING, "2" * 64, Decimal("8.0000")),
            ("ring", HUELLA_RING, SHA_RING, Decimal("5.0000")),
        ]
        conn.execute(
            "INSERT INTO bloqueo_veredicto (id, plataforma, ad_group_id, termino,"
            " termino_sha256, senal_id, huella_catalogo, tipo)"
            " VALUES (%s, 'amazon_mx', %s, 'baño', %s, %s, %s, 'rechazado')",
            (uuid.uuid4(), grupo_mx, SHA_COPIADO, senal_mx, HUELLA_ARO),
        )
        assert [p.termino for p in bloqueo.lee_propuestas(conn)] == ["vuelve", "ring"]
        _cola(conn, grupo_us, "amazon_us", "ring")
        assert [p.termino for p in bloqueo.lee_propuestas(conn)] == ["vuelve"]
