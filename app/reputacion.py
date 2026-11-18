"""Ingesta de reputacion: snapshots rating/count + reviews/seller/preguntas (A.2/A.4).

Fuentes verificadas en Fase 0 (contratos, no hipotesis):
  MeLi API oficial (E/0.3): scan, items+health, reviews, seller, questions,
  claims dispute. `total=0` trae `avg=0`: sin-dato (NULL + count 0).

Conciliacion (D-LEAD-A2-1, patron 0022, sin FK desde 0025): MeLi vs
items del seller. Sin match = skip contado.

Estructura (D-LEAD-A4-4, guardrail 900 lineas): clientes en
app/reputacion_clientes.py, planes puros en app/reputacion_plan.py;
aqui fetch + escritura + CLI.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import httpx
import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, redact_dsn, scrub
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
    plan_snapshot_meli as plan_snapshot_meli,
)

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE_MELI = "reputacion_meli"

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
    cliente: ClienteMeli, item_id: str
) -> tuple[dict | None, dt.datetime | None]:
    """Todas las paginas de /reviews/item (Grok XR-1 ALTA-2: la primera
    pagina sola dejaba fuera reviews y rompia resena_1; R2-4: primera
    pagina con limit/offset explicitos para no depender del default).
    """
    opiniones, fetched = cliente.get(f"/reviews/item/{item_id}", {"limit": 50, "offset": 0})
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
        # ARREGLOS 03 D-M: truncado significa cobertura desconocida (no solo
        # el tope de paginas): scroll ausente, fin prematuro o total
        # inconsistente tambien dejan el scan parcial.
        skips["meli: scan de items incompleto (cobertura desconocida)"] += 1
    for item_id in ids_items:
        try:
            item, _ = cliente.get(f"/items/{item_id}")
            opiniones, fetched = _reviews_completas(cliente, item_id)
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
) -> tuple[list[dict] | None, bool]:
    """Paginado limit/offset con guardias. Devuelve (items, truncado).

    XR-1.6: el total vive en `paging.total` (claims) o raiz (questions).
    XR-1.7: si se agotan las paginas con datos pendientes, truncado=True
    para contarlo como skip (nunca corte silencioso).
    ARREGLOS 03 D-L (F9): si la primera pagina no trae la lista `clave`
    (falta la clave o no es lista), devuelve (None, False): sin dato, no
    cero. Una lista vacia sigue siendo lista vacia. Si una pagina posterior
    llega sin la lista, lo leido es parcial: truncado=True.
    """
    items: list[dict] = []
    vistos: set[str] = set()
    offset = 0
    total_anunciado: int | None = None
    for numero in range(max_paginas):
        data, _ = cliente.get(path, {**params, "limit": 50, "offset": offset})
        pagina = data.get(clave)
        if not isinstance(pagina, list):
            return (None, False) if numero == 0 else (items, True)
        total = (data.get("paging") or {}).get("total", data.get("total"))
        if isinstance(total, int) and (total_anunciado is None or total > total_anunciado):
            total_anunciado = total
        if not pagina:
            return items, total_anunciado is not None and len(vistos) < total_anunciado
        nuevos = [p for p in pagina if isinstance(p, dict) and str(p.get("id")) not in vistos]
        if not nuevos:
            return items, True  # sin IDs nuevos no podemos confirmar la lectura completa
        for pregunta in nuevos:
            vistos.add(str(pregunta.get("id")))
        items.extend(nuevos)
        offset += len(pagina)
        if len(pagina) < 50 or (total_anunciado is not None and offset >= total_anunciado):
            return items, total_anunciado is not None and len(vistos) < total_anunciado
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
    if disputas is None:
        skips["meli: claims sin lista (conteo vacío, no cero)"] += 1
    if truncadas:
        skips["meli: claims incompletos (total parcial)"] += 1
    return plan_seller(rep, disputas, fetched), skips


def fetch_questions(cliente: ClienteMeli) -> tuple[list[PlanQuestion], Counter]:
    """Fase red questions: todas las del seller. Sin DB."""
    skips: Counter = Counter()
    yo, _ = cliente.get("/users/me")
    seller_id = yo.get("id")
    if not isinstance(seller_id, int):
        raise ReputacionError("MeLi /users/me sin id entero")
    crudas, truncadas = _pagina(cliente, "/questions/search", {"seller_id": seller_id}, "questions")
    if crudas is None:
        # Sin la lista no hay "ninguna pendiente": la lectura entera falla
        # (una sola transaccion en _corre_meli) y avisa cron-avisa de las 09:30.
        raise ReputacionError("MeLi /questions/search respondió sin la lista de preguntas")
    if truncadas:
        # La pantalla toma la ausencia en la ultima lectura como pregunta
        # borrada: una lectura parcial no se guarda (falla entera, como arriba).
        raise ReputacionError("MeLi /questions/search devolvió una lista incompleta de preguntas")
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
    # Grok XR-1 R2-5: el sello failed conserva los skips acumulados
    # (antes Counter() vacio: la corrida fallida perdia su detalle).
    motivo = _formato_skips(skips) if ok else "fallo la corrida"
    if not ok and skips:
        motivo += ": " + _formato_skips(skips)
    with conn.transaction():
        conn.execute(_SQL_SELLAR_RUN, (ins, sum(skips.values()), motivo, ok, run_id))


# ---------------------------------------------------------------------------
# Entrada CLI (envoltorio delgado en app/cli.py)
# ---------------------------------------------------------------------------


def ejecuta_snapshot(
    fuente: str,
    fecha: str | None = None,
    dry_run: bool = False,
    observed_at: dt.datetime | None = None,
    transport: httpx.BaseTransport | None = None,
    secrets_dir: str | Path | None = None,
) -> int:
    """Corre una ingesta A.2. `transport` solo existe para tests. Exit 0/1/2."""
    if fuente != "meli":
        print(f"fuente invalida: {fuente!r} (meli)", file=sys.stderr)
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
        creds = MeliCredentials.from_secrets_dir(secrets_dir)
        cliente = ClienteMeli(creds, transport=transport)
    except Exception as exc:
        if not dry_run:
            _sella_caida_temprana(dsn, exc)
        print(f"reputacion {fuente} fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    try:
        conn = connect(dsn)
        try:
            if dry_run:
                resumen = _ensayo(cliente, observed_at, metric_date)
            else:
                resumen = _corre_meli(conn, cliente, observed_at, metric_date)
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


def _sella_caida_temprana(dsn: str, exc: Exception) -> None:
    """La lectura se cayo antes de que _corre_meli abriera su corrida (llave o
    cliente): deja una corrida fallida de hoy con el nombre de la clase del
    error, para que la revision de las 10:30 no la tome por una lectura que
    no corrio (ARREGLOS 03 D-K). Si la base tampoco responde, no deja nada."""
    try:
        conn = connect(dsn)
        try:
            run_id = _abrir_run(conn, SOURCE_MELI)
            motivo = f"fallo antes de empezar: {type(exc).__name__}"
            with conn.transaction():
                conn.execute(_SQL_SELLAR_RUN, (0, 0, motivo, False, run_id))
        finally:
            conn.close()
    except Exception as error:  # noqa: BLE001 - sin base no hay donde dejarla
        logger.warning("reputacion meli: no se dejo la corrida fallida: %s", scrub(str(error)))


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
    rollback total. El run unico se sella failed como evidencia
    (Grok XR-1 R2-3: antes decia "los 3 runs", stale del diseno previo).
    """
    observed_at, metric_date = _reloj(observed_at, metric_date)
    run_id = _abrir_run(conn, SOURCE_MELI)
    skips: Counter = Counter()
    try:
        # Acumulo tras CADA fase (R2-5): si una fase tardia falla, el
        # sello failed conserva lo de las fases que si corrieron.
        snapshots, reviews, sk_snap = fetch_meli(cliente)
        skips.update(sk_snap)
        seller, sk_seller = fetch_seller(cliente)
        skips.update(sk_seller)
        questions, sk_questions = fetch_questions(cliente)
        skips.update(sk_questions)
        insertadas, idempotentes = _escribe_todo_meli(
            conn, snapshots, reviews, seller, questions, metric_date, observed_at
        )
        _sellar(conn, run_id, insertadas, skips, True)
    except Exception:
        _sellar(conn, run_id, 0, skips, False)
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


def _ensayo(
    cliente: ClienteMeli,
    observed_at: dt.datetime | None,
    metric_date: dt.date | None,
) -> dict:
    """Dry-run: CERO writes (ni ingest_run). Red completa sin escribir."""
    observed_at, metric_date = _reloj(observed_at, metric_date)
    yo, _ = cliente.get("/users/me")
    items, scan_truncado = cliente.items_seller(yo["id"])
    usuario, _ = cliente.get(f"/users/{yo['id']}")
    rep = usuario.get("seller_reputation") or {}
    preguntas, truncadas = _pagina(
        cliente, "/questions/search", {"seller_id": yo["id"]}, "questions"
    )
    return {
        "fuente": "meli",
        "ok": True,
        "dry_run": True,
        "items_vistos": len(items),
        "level": rep.get("level_id"),
        "preguntas_vistas": None if preguntas is None else len(preguntas),
        "paginado_truncado": truncadas or scan_truncado,
    }
