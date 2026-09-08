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


def _reviews_completas(
    cliente: ClienteMeli, item_id: str, skips: Counter
) -> tuple[dict | None, dt.datetime | None]:
    """Todas las paginas de /reviews/item (Grok XR-1 ALTA-2: la primera
    pagina sola dejaba fuera reviews y rompia resena_1)."""
    opiniones, fetched = cliente.get(f"/reviews/item/{item_id}")
    total = (opiniones.get("paging") or {}).get("total")
    if not isinstance(total, int) or total <= 0:
        return opiniones, fetched
    vistos = {r.get("id") for r in (opiniones.get("reviews") or []) if isinstance(r, dict)}
    combinadas = list(opiniones.get("reviews") or [])
    offset = len(combinadas)
    while offset < total:
        pagina, _ = cliente.get(f"/reviews/item/{item_id}", {"limit": 50, "offset": offset})
        lote = pagina.get("reviews") or []
        if not isinstance(lote, list) or not lote:
            break
        for rv in lote:
            if isinstance(rv, dict) and rv.get("id") not in vistos:
                vistos.add(rv.get("id"))
                combinadas.append(rv)
        offset += len(lote)
        if len(lote) < 50:
            break
    opiniones["reviews"] = combinadas
    return opiniones, fetched


def fetch_meli(
    cliente: ClienteMeli, max_fallos_seguidos: int = 5
) -> tuple[list[PlanSnapshot], list[PlanReview], Counter]:
    """Fase red MeLi: items + reviews paginadas. Sin DB. Puro-red."""
    skips: Counter = Counter()
    yo, _ = cliente.get("/users/me")
    seller_id = yo.get("id")
    if not isinstance(seller_id, int):
        raise ReputacionError("MeLi /users/me sin id entero")
    snapshots: list[PlanSnapshot] = []
    reviews: list[PlanReview] = []
    fallos = 0
    ids_items, scan_truncado = cliente.items_seller(seller_id)
    if scan_truncado:
        skips["meli: scan truncado a 50 paginas (items pendientes)"] += 1
    for item_id in ids_items:
        try:
            item, _ = cliente.get(f"/items/{item_id}")
            opiniones, fetched = _reviews_completas(cliente, item_id, skips)
        except ReputacionError:
            fallos += 1
            skips["meli: item fallo (se reintenta manana)"] += 1
            if fallos >= max_fallos_seguidos:
                raise ReputacionError(f"meli: {fallos} fallos seguidos, aborto honesto") from None
            continue
        fallos = 0
        snap, eventos, skips_item = plan_snapshot_meli(
            item_id, item, opiniones or {}, fetched or dt.datetime.now(dt.UTC)
        )
        skips.update(skips_item)
        if snap is not None:
            snapshots.append(snap)
        reviews.extend(eventos)
    return snapshots, reviews, skips


def sync_meli(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
    max_fallos_seguidos: int = 5,
) -> ResultadoReputacion:
    """Snapshots + reviews MeLi. Fase red completa, luego UNA transaccion."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    run_id = _abrir_run(conn, SOURCE_MELI)
    try:
        snapshots, reviews, skips = fetch_meli(cliente, max_fallos_seguidos)
        insertadas, idempotentes = _escribe_hechos(
            conn, snapshots, reviews, metric_date, observed_at
        )
        _sellar(conn, run_id, insertadas, skips, True)
    except Exception:
        _sellar(conn, run_id, 0, Counter(), False)
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
        # Grok XR-1 MEDIA-3: conciliar por ASIN (input u originalAsin),
        # no por string de URL (junglee normaliza: sin www, trailing /).
        por_asin = {asin.upper(): (plat, asin) for plat, asin in catalogo}
        items, costo_est = cliente.scrape_productos(
            urls, max_productos=max_productos, tope_usd=tope_usd
        )
        fetched = dt.datetime.now(dt.UTC)
        snapshots: list[PlanSnapshot] = []
        for item in items:
            pedido = _asin_de_input(item.get("input")) or _asin_valido(item.get("originalAsin"))
            conciliado = por_asin.get(pedido or "")
            if pedido is None or conciliado is None:
                skips["amazon: item sin ASIN conciliable (se descarta)"] += 1
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
    return _asin_valido(entrada.split("/dp/", 1)[1].split("/")[0].split("?")[0].strip())


def _asin_valido(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    asin = valor.strip().upper()
    return asin if asin and all(c.isalnum() for c in asin) else None


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
) -> tuple[list[dict], bool]:
    """Paginado limit/offset con guardias. Devuelve (items, truncado).

    XR-1.6: el total vive en `paging.total` (claims) o raiz (questions).
    XR-1.7: si se agotan las paginas con datos pendientes, truncado=True
    para contarlo como skip (nunca corte silencioso).
    """
    items: list[dict] = []
    vistos: set[str] = set()
    offset = 0
    for _ in range(max_paginas):
        data, _ = cliente.get(path, {**params, "limit": 50, "offset": offset})
        pagina = data.get(clave) or []
        if not isinstance(pagina, list) or not pagina:
            return items, False
        nuevos = [p for p in pagina if isinstance(p, dict) and str(p.get("id")) not in vistos]
        if not nuevos:
            return items, False  # la API ignoro el offset: no repetir para siempre
        for pregunta in nuevos:
            vistos.add(str(pregunta.get("id")))
        items.extend(nuevos)
        total = (data.get("paging") or {}).get("total", data.get("total"))
        offset += len(pagina)
        if isinstance(total, int) and offset >= total:
            return items, False
        if len(pagina) < 50:
            return items, False
    return items, True


def fetch_seller(cliente: ClienteMeli) -> tuple[PlanSeller, Counter]:
    """Fase red seller: cuenta + disputas. Sin DB."""
    skips: Counter = Counter()
    yo, _ = cliente.get("/users/me")
    seller_id = yo.get("id")
    if not isinstance(seller_id, int):
        raise ReputacionError("MeLi /users/me sin id entero")
    usuario, fetched = cliente.get(f"/users/{seller_id}")
    rep = usuario.get("seller_reputation")
    if not isinstance(rep, dict):
        raise ReputacionError("MeLi /users/{id} sin seller_reputation")
    disputas, truncadas = _pagina(
        cliente,
        "/post-purchase/v1/claims/search",
        {"player": seller_id, "stage": "dispute"},
        "data",
    )
    if truncadas:
        skips["meli: claims truncados por max_paginas (total parcial)"] += 1
    return plan_seller(rep, disputas, fetched), skips


def fetch_questions(cliente: ClienteMeli) -> tuple[list[PlanQuestion], Counter]:
    """Fase red questions: todas las del seller. Sin DB."""
    skips: Counter = Counter()
    yo, _ = cliente.get("/users/me")
    seller_id = yo.get("id")
    if not isinstance(seller_id, int):
        raise ReputacionError("MeLi /users/me sin id entero")
    crudas, truncadas = _pagina(cliente, "/questions/search", {"seller_id": seller_id}, "questions")
    if truncadas:
        skips["meli: questions truncadas por max_paginas (total parcial)"] += 1
    fetched = dt.datetime.now(dt.UTC)
    planes: list[PlanQuestion] = []
    for pregunta in crudas:
        item_id = pregunta.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            skips["meli: pregunta sin item_id (se descarta)"] += 1
            continue
        plan, motivo = plan_question(item_id, pregunta, fetched)
        if plan is None:
            skips[motivo or "meli: pregunta descartada"] += 1
            continue
        if motivo is not None:
            skips[motivo] += 1
        planes.append(plan)
    return planes, skips


def _escribe_todo_meli(
    conn: psycopg.Connection,
    snapshots: list[PlanSnapshot],
    reviews: list[PlanReview],
    seller: PlanSeller | None,
    questions: list[PlanQuestion],
    metric_date: dt.date,
    observed_at: dt.datetime,
) -> tuple[int, int]:
    """TODOS los hechos MeLi en UNA transaccion (Grok XR-1 ALTA-1: AC3
    exige rollback total; 3 txs separadas dejaban corrida a medias)."""
    insertadas = idempotentes = 0
    with conn.transaction():
        sub_ins, sub_idem = _escribe_hechos(conn, snapshots, reviews, metric_date, observed_at)
        insertadas += sub_ins
        idempotentes += sub_idem
        if seller is not None:
            cur = conn.execute(
                _SQL_INSERT_SELLER,
                (
                    "meli",
                    metric_date,
                    seller.level_id,
                    seller.power_seller,
                    seller.tx_total,
                    seller.tx_completed,
                    seller.tx_canceled,
                    seller.ratings_positive,
                    seller.ratings_neutral,
                    seller.ratings_negative,
                    seller.disputas_total,
                    seller.disputas_abiertas,
                    seller.fetched_at,
                    observed_at,
                ),
            )
            if cur.rowcount == 0:
                idempotentes += 1
            else:
                insertadas += 1
        for plan in sorted(questions, key=lambda p: (p.external_id, p.question_external_id)):
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
    return insertadas, idempotentes


def _abrir_run(conn: psycopg.Connection, source: str) -> int:
    with conn.transaction():
        return conn.execute(_SQL_ABRIR_RUN, (source,)).fetchone()[0]


def _sellar(conn: psycopg.Connection, run_id: int, ins: int, skips: Counter, ok: bool) -> None:
    motivo = _formato_skips(skips) if ok else "fallo la corrida"
    with conn.transaction():
        conn.execute(_SQL_SELLAR_RUN, (ins, sum(skips.values()), motivo, ok, run_id))


def sync_seller(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
) -> ResultadoReputacion:
    """Una fila diaria de cuenta MeLi (level, tx, disputas)."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    run_id = _abrir_run(conn, SOURCE_SELLER)
    try:
        plan, skips = fetch_seller(cliente)
        insertadas, idempotentes = _escribe_todo_meli(
            conn, [], [], plan, [], metric_date, observed_at
        )
        _sellar(conn, run_id, insertadas, skips, True)
    except Exception:
        _sellar(conn, run_id, 0, Counter(), False)
        raise
    return ResultadoReputacion(
        run_id=run_id,
        ok=True,
        fuente="meli_seller",
        filas_insertadas=insertadas,
        filas_idempotentes=idempotentes,
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
    run_id = _abrir_run(conn, SOURCE_QUESTIONS)
    try:
        planes, skips = fetch_questions(cliente)
        insertadas, idempotentes = _escribe_todo_meli(
            conn, [], [], None, planes, metric_date, observed_at
        )
        _sellar(conn, run_id, insertadas, skips, True)
    except Exception:
        _sellar(conn, run_id, 0, Counter(), False)
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
    """Corrida MeLi completa: red total, luego UNA tx de hechos (AC3).

    Grok XR-1 ALTA-1: 3 syncs en serie dejaban corrida a medias (ej.
    questions truena con snapshots ya escritos). Ahora: si CUALQUIERA
    de las 3 fases red falla, cero hechos; si la escritura falla,
    rollback total. Los 3 runs se sellan failed como evidencia.
    """
    observed_at, metric_date = _reloj(observed_at, metric_date)
    run_id = _abrir_run(conn, SOURCE_MELI)
    skips: Counter = Counter()
    try:
        snapshots, reviews, sk_snap = fetch_meli(cliente)
        seller, sk_seller = fetch_seller(cliente)
        questions, sk_questions = fetch_questions(cliente)
        skips.update(sk_snap)
        skips.update(sk_seller)
        skips.update(sk_questions)
        insertadas, idempotentes = _escribe_todo_meli(
            conn, snapshots, reviews, seller, questions, metric_date, observed_at
        )
        _sellar(conn, run_id, insertadas, skips, True)
    except Exception:
        _sellar(conn, run_id, 0, Counter(), False)
        raise
    return {
        "fuente": "meli",
        "ok": True,
        "run_ids": {"meli": run_id},
        "filas_insertadas": insertadas,
        "filas_idempotentes": idempotentes,
        "skips": dict(sorted(skips.items())),
        "costo_usd": None,
    }


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
    """Dry-run: CERO writes (ni ingest_run). Meli hace red completa;
    amazon solo cuenta el catalogo (sin llamadas a junglee)."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    if fuente == "meli":
        assert isinstance(cliente, ClienteMeli)
        yo, _ = cliente.get("/users/me")
        items, scan_truncado = cliente.items_seller(yo["id"])
        usuario, _ = cliente.get(f"/users/{yo['id']}")
        rep = usuario.get("seller_reputation") or {}
        preguntas, truncadas = _pagina(
            cliente, "/questions/search", {"seller_id": yo["id"]}, "questions"
        )
        return {
            "fuente": fuente,
            "ok": True,
            "dry_run": True,
            "items_vistos": len(items),
            "level": rep.get("level_id"),
            "preguntas_vistas": len(preguntas),
            "paginado_truncado": truncadas or scan_truncado,
        }
    assert isinstance(cliente, ClienteJunglee)
    catalogo = conn.execute(
        "SELECT count(*) FROM listing WHERE platform IN ('amazon_mx', 'amazon_us')"
    ).fetchone()[0]
    return {"fuente": fuente, "ok": True, "dry_run": True, "asins_catalogo": catalogo}
