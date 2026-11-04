"""B5-r1: repro del bloqueante G1#1 (claude, grupo dominio).

Reclamo: `_abrir_revision` no incluye el contrato en la comparacion de
coherencia (app/jev_ads.py), asi que retomar la misma solicitud con OTRO
contrato no lanza ValueError: la fila de jev_revision conserva el contrato
v1 y los eventos nuevos llevan contrato_sha256 del v2 (juicios de contratos
mezclados bajo una misma revision).

Monta una base temporal (0001+0004+0049), siembra un grupo de 2 anuncios
con ficha, evalua la misma solicitud dos veces con contratos distintos y
imprime el estado. Exit 0 = bloqueante reproducido; exit 2 = no se reprodujo.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(RAIZ))

import psycopg  # noqa: E402 (el path del repo se arma arriba, antes de importar app)
from psycopg import sql as pgsql  # noqa: E402

from app.jev_ads import (  # noqa: E402
    AsesorAds,
    SemillasARevisar,
)
from app.jev_juicios import Contrato, ResultadoPar, contrato_por_defecto  # noqa: E402

DSN = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
DB = f"orbit_jev01_b5r1_{socket.gethostname().lower()}_{os.getpid()}"
AHORA = datetime(2026, 10, 4, tzinfo=UTC)
VENCE = datetime(2026, 11, 2, tzinfo=UTC)
ORDEN = ("0001_initial.sql", "0004_ad_entity_kind_product_ad.sql", "0049_jev_ads.sql")
SOLICITUD = uuid.UUID("00000000-0000-0000-0000-0000000000b5")


def pedir(termino, ficha):
    from app.jev_ads import ClavePar, Juicio

    return ResultadoPar(
        juicio=Juicio(
            intento_id=uuid.uuid4(),
            clave=ClavePar("a" * 64, ficha.id, "b" * 64),
            relacion="no_satisface",
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


def _sujeto(censo, terminos):
    canonico = {
        "grupo": censo.miembros[0].anuncio_ids[0] if censo.miembros else 0,
        "terminos": list(terminos),
    }
    canon = json.dumps(canonico, sort_keys=True, ensure_ascii=False)
    return SemillasARevisar(
        plan_sha256=hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        plan_canonico=canonico,
        fuentes_semillas={"origen": "lote-de-prueba"},
        terminos=tuple(terminos),
        censo=censo,
        plataforma="amazon_mx",
    )


def main() -> int:
    admin = psycopg.connect(DSN, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(DB)))
        conn = psycopg.connect(DSN, dbname=DB, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))

        p1 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('R1', 'R1') RETURNING id"
        ).fetchone()[0]
        l1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_mx', 'B0R1LISTA1') RETURNING id",
            (p1,),
        ).fetchone()[0]
        p2 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('R2', 'R2') RETURNING id"
        ).fetchone()[0]
        l2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_mx', 'B0R2LISTA2') RETURNING id",
            (p2,),
        ).fetchone()[0]
        ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'ad_group', 'ag-repro') RETURNING id"
        ).fetchone()[0]
        for i, listing in enumerate((l1, l2)):
            conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
                " VALUES ('amazon_mx', 'product_ad', %s, %s, %s)",
                (f"ad-{ag}-{i}", ag, listing),
            )
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ENABLED', now())",
                (f"ad-{ag}-{i}",),
            )
        for producto, listing in ((p1, (l1,)), (p2, (l2,))):
            conn.execute(
                "INSERT INTO jev_ficha_version (id, producto_id, plataforma, listings, hechos,"
                " desconocidos, sha256, aprobador, observado_at, revisar_antes_de)"
                " VALUES (%s, %s, 'amazon_mx', %s, %s::jsonb, '{}', %s, 'aprobador', %s, %s)",
                (
                    uuid.uuid4(),
                    producto,
                    list(listing),
                    json.dumps([{"texto": "hecho", "fuente": "fuente"}]),
                    uuid.uuid4().hex * 2,
                    AHORA,
                    VENCE,
                ),
            )

        from app.jev_catalogo import censo_grupo

        censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=ag)
        assert len(censo.miembros) == 2, censo
        sujeto = _sujeto(censo, ("soporte mesa",))

        base = contrato_por_defecto()
        otro = Contrato(
            modelo=base.modelo,
            opciones=base.opciones,
            criterios=base.criterios,
            instrucciones=base.instrucciones,
            version=base.version + "-OTRA",
            max_bytes_termino=base.max_bytes_termino,
            max_bytes_ficha=base.max_bytes_ficha,
        )

        r1 = AsesorAds(conn, pedir=pedir, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
            sujeto, solicitud_id=SOLICITUD
        )
        print(f"evaluar 1 (contrato {base.version}): {r1.resultados}")

        lanzo = False
        try:
            r2 = AsesorAds(
                conn,
                pedir=pedir,
                api_key="k",
                presupuesto=5,
                ahora=lambda: AHORA,
                contrato=otro,
            ).evaluar(sujeto, solicitud_id=SOLICITUD)
            print(f"evaluar 2 (contrato {otro.version}, MISMA solicitud): {r2.resultados}")
            print(">>> NO lanzo ValueError 'misma solicitud con otro payload'")
        except ValueError as exc:
            lanzo = True
            print(f">>> lanzo ValueError: {exc}")

        rev = conn.execute(
            "SELECT contrato->>'sha256' FROM jev_revision WHERE solicitud = %s", (SOLICITUD,)
        ).fetchone()[0]
        eventos = conn.execute(
            "SELECT tipo, ordinal, contrato_sha256, respuesta IS NOT NULL AS exito"
            " FROM jev_par_evento WHERE revision_id = %s ORDER BY ordinal",
            (SOLICITUD,),
        ).fetchall()
        print(f"jev_revision.contrato->>'sha256' = {rev[:16]}...")
        print("jev_par_evento de ESA revision:")
        for fila in eventos:
            print(f"  ordinal={fila[1]} tipo={fila[0]} contrato={fila[2][:16]}... exito={fila[3]}")
        distintos = {fila[2] for fila in eventos}
        mezcla = len(distintos) > 1 or (distintos and distintos != {rev})
        print(f"contratos distintos en los eventos: {len(distintos)}; mezclados: {mezcla}")
        if not lanzo and mezcla:
            print("REPRODUCIDO: misma solicitud, dos contratos, sin ValueError y eventos mezclados")
            return 0
        print("NO REPRODUCIDO: el comportamiento difiere del reclamo")
        return 2
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(DB))
        )
        admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
