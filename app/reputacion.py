"""Ingesta de reputacion: snapshots rating/count + reviews/seller/preguntas (A.2/A.4).

Fuentes verificadas en Fase 0 (contratos, no hipotesis):
  MeLi API oficial (E/0.3): scan, items+health, reviews, seller, questions,
  claims dispute. `total=0` trae `avg=0`: sin-dato (NULL + count 0).
  Amazon junglee (E/0.2): stars + reviewsCount, GRANO PADRE, ~$0.0025/prod.

Conciliacion (D-LEAD-A2-1, patron 0022, sin FK desde 0025): Amazon vs
`listing`; MeLi vs items del seller. Sin match = skip contado.

Estructura (D-LEAD-A4-4, guardrail 900 lineas): clientes en
app/reputacion_clientes.py, planes puros en app/reputacion_plan.py;
aqui syncs + CLI. Los nombres se re-exportan para compatibilidad.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, redact_dsn, scrub
from app.reputacion_clientes import (
    ACTOR_JUNGLEE_DEFAULT as ACTOR_JUNGLEE_DEFAULT,
)
from app.reputacion_clientes import (
    TARIFA_JUNGLEE_DEFAULT as TARIFA_JUNGLEE_DEFAULT,
)
from app.reputacion_clientes import (
    ApifyCredentials as ApifyCredentials,
)
from app.reputacion_clientes import (
    ClienteJunglee as ClienteJunglee,
)
from app.reputacion_clientes import (
    ClienteMeli as ClienteMeli,
)
from app.reputacion_clientes import (
    MeliCredentials as MeliCredentials,
)
from app.reputacion_clientes import (
    ReputacionError as ReputacionError,
)
from app.reputacion_plan import (
    PlanQuestion as PlanQuestion,
)
from app.reputacion_plan import (
    PlanReview as PlanReview,
)
from app.reputacion_plan import (
    PlanSeller as PlanSeller,
)
from app.reputacion_plan import (
    PlanSnapshot as PlanSnapshot,
)
from app.reputacion_plan import (
    plan_question as plan_question,
)
from app.reputacion_plan import (
    plan_seller as plan_seller,
)
from app.reputacion_plan import (
    plan_snapshot_junglee as plan_snapshot_junglee,
)
from app.reputacion_plan import (
    plan_snapshot_meli as plan_snapshot_meli,
)

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE_MELI = "reputacion_meli"
SOURCE_AMAZON = "reputacion_amazon"

# Escritura en Orbit (rol de ingesta), patron de app/disponibilidad.py
# ---------------------------------------------------------------------------

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"

_SQL_SELLAR_RUN = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s
 WHERE id = %s
"""

_SQL_INSERT_SNAPSHOT = """
INSERT INTO reputation_snapshot
    (platform, external_id, alcance, metric_date, rating, review_count,
     parent_asin, fetched_at, observed_at, extra)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT reputation_snapshot_anti_duplicado DO NOTHING
"""

_SQL_INSERT_REVIEW = """
INSERT INTO review_event
    (platform, external_id, review_external_id, rating, titulo, texto,
     publicada, published_at, fetched_at, observed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT review_event_anti_duplicado DO NOTHING
"""


@dataclass
class ResultadoReputacion:
    run_id: int | None
    ok: bool
    fuente: str
    filas_insertadas: int = 0
    filas_idempotentes: int = 0
    skips: dict = field(default_factory=dict)
    costo_usd: float | None = None


def _formato_skips(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def _reloj(
    observed_at: dt.datetime | None, metric_date: dt.date | None
) -> tuple[dt.datetime, dt.date]:
    if observed_at is None:
        observed_at = dt.datetime.now(dt.UTC)
    if observed_at.tzinfo is None:
        raise ReputacionError("observed_at debe traer zona horaria (UTC)")
    return observed_at, metric_date or observed_at.date()


def _escribe_hechos(
    conn: psycopg.Connection,
    snapshots: list[PlanSnapshot],
    reviews: list[PlanReview],
    metric_date: dt.date,
    observed_at: dt.datetime,
) -> tuple[int, int]:
    """UNA transaccion: todos los hechos o ninguno. Devuelve (nuevas, idem)."""
    insertadas = idempotentes = 0
    with conn.transaction():
        for snap in sorted(snapshots, key=lambda s: (s.plataforma, s.external_id)):
            cur = conn.execute(
                _SQL_INSERT_SNAPSHOT,
                (
                    snap.plataforma,
                    snap.external_id,
                    snap.alcance,
                    metric_date,
                    snap.rating,
                    snap.review_count,
                    snap.parent_asin,
                    snap.fetched_at,
                    observed_at,
                    json.dumps(snap.extra),
                ),
            )
            if cur.rowcount == 0:
                idempotentes += 1
            else:
                insertadas += 1
        for rev in sorted(reviews, key=lambda r: (r.plataforma, r.review_external_id)):
            cur = conn.execute(
                _SQL_INSERT_REVIEW,
                (
                    rev.plataforma,
                    rev.external_id,
                    rev.review_external_id,
                    rev.rating,
                    rev.titulo,
                    rev.texto,
                    rev.publicada,
                    rev.published_at,
                    rev.fetched_at,
                    observed_at,
                ),
            )
            if cur.rowcount == 0:
                idempotentes += 1
            else:
                insertadas += 1
    return insertadas, idempotentes


def sync_meli(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
    max_fallos_seguidos: int = 5,
) -> ResultadoReputacion:
    """Snapshots + reviews MeLi. Fase red completa, luego UNA transaccion."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE_MELI,)).fetchone()[0]
    skips: Counter = Counter()
    try:
        yo, _ = cliente.get("/users/me")
        seller_id = yo.get("id")
        if not isinstance(seller_id, int):
            raise ReputacionError("MeLi /users/me sin id entero")
        snapshots: list[PlanSnapshot] = []
        reviews: list[PlanReview] = []
        fallos = 0
        for item_id in cliente.items_seller(seller_id):
            try:
                item, _ = cliente.get(f"/items/{item_id}")
                opiniones, fetched = cliente.get(f"/reviews/item/{item_id}")
            except ReputacionError:
                fallos += 1
                skips["meli: item fallo (se reintenta manana)"] += 1
                if fallos >= max_fallos_seguidos:
                    raise ReputacionError(
                        f"meli: {fallos} fallos seguidos, aborto honesto"
                    ) from None
                continue
            fallos = 0
            snap, eventos, skips_item = plan_snapshot_meli(item_id, item, opiniones, fetched)
            skips.update(skips_item)
            if snap is not None:
                snapshots.append(snap)
            reviews.extend(eventos)
        insertadas, idempotentes = _escribe_hechos(
            conn, snapshots, reviews, metric_date, observed_at
        )
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN,
                (insertadas, sum(skips.values()), _formato_skips(skips), True, run_id),
            )
    except Exception:
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN, (0, sum(skips.values()), "fallo la corrida", False, run_id)
            )
        raise
    return ResultadoReputacion(
        run_id=run_id,
        ok=True,
        fuente="meli",
        filas_insertadas=insertadas,
        filas_idempotentes=idempotentes,
        skips=dict(sorted(skips.items())),
    )


_AMAZON_DOMINIOS = {"amazon_mx": "amazon.com.mx", "amazon_us": "amazon.com"}


def sync_amazon(
    conn: psycopg.Connection,
    cliente: ClienteJunglee,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
    max_productos: int = 600,
    tope_usd: float = 2.0,
) -> ResultadoReputacion:
    """Snapshots Amazon via junglee. Solo ASINs del catalogo (D2)."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE_AMAZON,)).fetchone()[0]
    skips: Counter = Counter()
    try:
        catalogo = conn.execute(
            "SELECT platform, external_id FROM listing"
            " WHERE platform IN ('amazon_mx', 'amazon_us')"
            " ORDER BY platform, external_id"
        ).fetchall()
        urls = [f"https://www.{_AMAZON_DOMINIOS[plat]}/dp/{asin}" for plat, asin in catalogo]
        por_url = {url: (plat, asin) for (plat, asin), url in zip(catalogo, urls, strict=True)}
        items, costo_est = cliente.scrape_productos(
            urls, max_productos=max_productos, tope_usd=tope_usd
        )
        fetched = dt.datetime.now(dt.UTC)
        snapshots: list[PlanSnapshot] = []
        for item in items:
            pedido = _asin_de_input(item.get("input"))
            conciliado = por_url.get(item.get("input") or "")
            if pedido is None or conciliado is None:
                skips["amazon: item sin input conciliable (se descarta)"] += 1
                continue
            snap, skips_item = plan_snapshot_junglee(pedido, item, fetched, conciliado[0])
            skips.update(skips_item)
            if snap is not None:
                snapshots.append(snap)
        vistos = {s.external_id for s in snapshots}
        for _plat, asin in catalogo:
            if asin not in vistos:
                skips["amazon: ASIN sin item en respuesta (ausente, no cero)"] += 1
        insertadas, idempotentes = _escribe_hechos(conn, snapshots, [], metric_date, observed_at)
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN,
                (insertadas, sum(skips.values()), _formato_skips(skips), True, run_id),
            )
    except Exception:
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN, (0, sum(skips.values()), "fallo la corrida", False, run_id)
            )
        raise
    return ResultadoReputacion(
        run_id=run_id,
        ok=True,
        fuente="amazon",
        filas_insertadas=insertadas,
        filas_idempotentes=idempotentes,
        skips=dict(sorted(skips.items())),
        costo_usd=costo_est,
    )


def _asin_de_input(entrada) -> str | None:
    if not isinstance(entrada, str) or "/dp/" not in entrada:
        return None
    asin = entrada.split("/dp/", 1)[1].split("/")[0].split("?")[0].strip()
    return asin or None


# ---------------------------------------------------------------------------
SOURCE_SELLER = "reputacion_meli_seller"
SOURCE_QUESTIONS = "reputacion_meli_questions"

_SQL_INSERT_SELLER = """
INSERT INTO seller_reputation_snapshot
    (platform, metric_date, level_id, power_seller, tx_total, tx_completed,
     tx_canceled, ratings_positive, ratings_neutral, ratings_negative,
     disputas_total, disputas_abiertas, fetched_at, observed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT seller_reputation_anti_duplicado DO NOTHING
"""

_SQL_INSERT_QUESTION = """
INSERT INTO meli_question
    (platform, external_id, question_external_id, estado, texto, respuesta,
     asked_at, answered_at, fetched_at, observed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT meli_question_anti_duplicado DO NOTHING
"""


def _pagina(
    cliente: ClienteMeli, path: str, params: dict, clave: str, max_paginas: int = 20
) -> list[dict]:
    """Paginado limit/offset con guardias (sin loop infinito)."""
    items: list[dict] = []
    vistos: set[str] = set()
    offset = 0
    for _ in range(max_paginas):
        data, _ = cliente.get(path, {**params, "limit": 50, "offset": offset})
        pagina = data.get(clave) or []
        if not isinstance(pagina, list) or not pagina:
            break
        nuevos = [p for p in pagina if isinstance(p, dict) and str(p.get("id")) not in vistos]
        if not nuevos:
            break  # la API ignoro el offset: no repetir para siempre
        for pregunta in nuevos:
            vistos.add(str(pregunta.get("id")))
        items.extend(nuevos)
        total = (data.get("paging") or {}).get("total")
        offset += len(pagina)
        if isinstance(total, int) and offset >= total:
            break
        if len(pagina) < 50:
            break
    return items


def sync_seller(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
) -> ResultadoReputacion:
    """Una fila diaria de cuenta MeLi (level, tx, disputas)."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE_SELLER,)).fetchone()[0]
    skips: Counter = Counter()
    try:
        yo, _ = cliente.get("/users/me")
        seller_id = yo.get("id")
        if not isinstance(seller_id, int):
            raise ReputacionError("MeLi /users/me sin id entero")
        usuario, fetched = cliente.get(f"/users/{seller_id}")
        rep = usuario.get("seller_reputation")
        if not isinstance(rep, dict):
            raise ReputacionError("MeLi /users/{id} sin seller_reputation")
        disputas = _pagina(
            cliente,
            "/post-purchase/v1/claims/search",
            {"player": seller_id, "stage": "dispute"},
            "data",
        )
        plan = plan_seller(rep, disputas, fetched)
        with conn.transaction():
            cur = conn.execute(
                _SQL_INSERT_SELLER,
                (
                    "meli",
                    metric_date,
                    plan.level_id,
                    plan.power_seller,
                    plan.tx_total,
                    plan.tx_completed,
                    plan.tx_canceled,
                    plan.ratings_positive,
                    plan.ratings_neutral,
                    plan.ratings_negative,
                    plan.disputas_total,
                    plan.disputas_abiertas,
                    plan.fetched_at,
                    observed_at,
                ),
            )
            insertadas = cur.rowcount
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN,
                (insertadas, sum(skips.values()), _formato_skips(skips), True, run_id),
            )
    except Exception:
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN, (0, sum(skips.values()), "fallo la corrida", False, run_id)
            )
        raise
    return ResultadoReputacion(
        run_id=run_id,
        ok=True,
        fuente="meli_seller",
        filas_insertadas=insertadas,
        filas_idempotentes=0 if insertadas else 1,
        skips=dict(sorted(skips.items())),
    )


def sync_questions(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
) -> ResultadoReputacion:
    """Todas las preguntas del seller, una fila por pregunta y corrida."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE_QUESTIONS,)).fetchone()[0]
    skips: Counter = Counter()
    try:
        yo, _ = cliente.get("/users/me")
        seller_id = yo.get("id")
        if not isinstance(seller_id, int):
            raise ReputacionError("MeLi /users/me sin id entero")
        crudas = _pagina(cliente, "/questions/search", {"seller_id": seller_id}, "questions")
        fetched = dt.datetime.now(dt.UTC)
        planes: list[PlanQuestion] = []
        for pregunta in crudas:
            item_id = pregunta.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                skips["meli: pregunta sin item_id (se descarta)"] += 1
                continue
            plan = plan_question(item_id, pregunta, fetched)
            if plan is None:
                skips["meli: pregunta sin id o estado desconocido (se descarta)"] += 1
                continue
            planes.append(plan)
        insertadas = idempotentes = 0
        with conn.transaction():
            for plan in sorted(planes, key=lambda p: (p.external_id, p.question_external_id)):
                cur = conn.execute(
                    _SQL_INSERT_QUESTION,
                    (
                        "meli",
                        plan.external_id,
                        plan.question_external_id,
                        plan.estado,
                        plan.texto,
                        plan.respuesta,
                        plan.asked_at,
                        plan.answered_at,
                        plan.fetched_at,
                        observed_at,
                    ),
                )
                if cur.rowcount == 0:
                    idempotentes += 1
                else:
                    insertadas += 1
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN,
                (insertadas, sum(skips.values()), _formato_skips(skips), True, run_id),
            )
    except Exception:
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN, (0, sum(skips.values()), "fallo la corrida", False, run_id)
            )
        raise
    return ResultadoReputacion(
        run_id=run_id,
        ok=True,
        fuente="meli_questions",
        filas_insertadas=insertadas,
        filas_idempotentes=idempotentes,
        skips=dict(sorted(skips.items())),
    )


# ---------------------------------------------------------------------------
# Entrada CLI (envoltorio delgado en app/cli.py)
# ---------------------------------------------------------------------------


def ejecuta_snapshot(
    fuente: str,
    fecha: str | None = None,
    dry_run: bool = False,
    max_productos: int = 600,
    tope_usd: float = 2.0,
    observed_at: dt.datetime | None = None,
    transport: httpx.BaseTransport | None = None,
    secrets_dir: str | Path | None = None,
) -> int:
    """Corre una ingesta A.2. `transport` solo existe para tests. Exit 0/1/2."""
    if fuente not in ("meli", "amazon"):
        print(f"fuente invalida: {fuente!r} (meli|amazon)", file=sys.stderr)
        return 2
    metric_date: dt.date | None = None
    if fecha is not None:
        try:
            metric_date = dt.date.fromisoformat(fecha)
        except ValueError:
            print(f"--fecha invalida (YYYY-MM-DD): {fecha!r}", file=sys.stderr)
            return 2
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir (fail-closed)",
            file=sys.stderr,
        )
        return 2
    redact_dsn(dsn)  # registra la password para scrub(), defensa en profundidad
    try:
        if fuente == "meli":
            creds = MeliCredentials.from_secrets_dir(secrets_dir)
            cliente: ClienteMeli | ClienteJunglee = ClienteMeli(creds, transport=transport)
        else:
            creds = ApifyCredentials.from_secrets_dir(secrets_dir)
            cliente = ClienteJunglee(creds, transport=transport)
        conn = connect(dsn)
        try:
            if dry_run:
                resumen = _ensayo(conn, fuente, cliente, observed_at, metric_date)
            elif fuente == "meli":
                assert isinstance(cliente, ClienteMeli)
                resumen = _corre_meli(conn, cliente, observed_at, metric_date)
            else:
                assert isinstance(cliente, ClienteJunglee)
                resultado = sync_amazon(
                    conn, cliente, observed_at, metric_date, max_productos, tope_usd
                )
                resumen = _resumen("amazon", [("snapshots", resultado)])
        finally:
            conn.close()
            cliente.close()
    except Exception as exc:
        print(f"reputacion {fuente} fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    if (
        not dry_run
        and resumen.get("filas_insertadas", 0) == 0
        and not resumen.get("filas_idempotentes")
    ):
        print(f"reputacion {fuente}: cero snapshots con inputs validos", file=sys.stderr)
        return 1
    print(json.dumps(resumen, ensure_ascii=False))
    return 0


def _corre_meli(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    observed_at: dt.datetime | None,
    metric_date: dt.date | None,
) -> dict:
    """Corrida MeLi completa: snapshots + seller + questions en serie."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    partes = [
        ("snapshots", sync_meli(conn, cliente, observed_at, metric_date)),
        ("seller", sync_seller(conn, cliente, observed_at, metric_date)),
        ("questions", sync_questions(conn, cliente, observed_at, metric_date)),
    ]
    return _resumen("meli", partes)


def _resumen(fuente: str, partes: list[tuple[str, ResultadoReputacion]]) -> dict:
    skips: Counter = Counter()
    for _, resultado in partes:
        skips.update(resultado.skips)
    return {
        "fuente": fuente,
        "ok": all(r.ok for _, r in partes),
        "run_ids": {nombre: r.run_id for nombre, r in partes},
        "filas_insertadas": sum(r.filas_insertadas for _, r in partes),
        "filas_idempotentes": sum(r.filas_idempotentes for _, r in partes),
        "skips": dict(sorted(skips.items())),
        "costo_usd": next((r.costo_usd for _, r in partes if r.costo_usd is not None), None),
    }


def _ensayo(
    conn: psycopg.Connection,
    fuente: str,
    cliente: ClienteMeli | ClienteJunglee,
    observed_at: dt.datetime | None,
    metric_date: dt.date | None,
) -> dict:
    """Dry-run: red + planes, CERO writes (ni ingest_run)."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    if fuente == "meli":
        assert isinstance(cliente, ClienteMeli)
        yo, _ = cliente.get("/users/me")
        items = cliente.items_seller(yo["id"])
        usuario, _ = cliente.get(f"/users/{yo['id']}")
        rep = usuario.get("seller_reputation") or {}
        preguntas = _pagina(cliente, "/questions/search", {"seller_id": yo["id"]}, "questions")
        return {
            "fuente": fuente,
            "ok": True,
            "dry_run": True,
            "items_vistos": len(items),
            "level": rep.get("level_id"),
            "preguntas_vistas": len(preguntas),
        }
    assert isinstance(cliente, ClienteJunglee)
    catalogo = conn.execute(
        "SELECT count(*) FROM listing WHERE platform IN ('amazon_mx', 'amazon_us')"
    ).fetchone()[0]
    return {"fuente": fuente, "ok": True, "dry_run": True, "asins_catalogo": catalogo}
