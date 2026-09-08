"""Ingesta de reputacion: snapshots rating/count + reviews MeLi (A.2).

Fuentes verificadas en Fase 0 (contratos, no hipotesis):
  MeLi API oficial (E/0.3, shapes en 0.3/shapes.json): scan de items,
  `/items` (health), `/reviews/item` (avg, total, levels, reviews).
  `total=0` trae `avg=0`: ese 0 es sin-dato (rating NULL + count 0).
  Amazon junglee (E/0.2): `stars` + `reviewsCount`, GRANO PADRE
  (`originalAsin` pedido, `asin` padre devuelto), ~$0.0025/producto.

Conciliacion (D-LEAD-A2-1, patron 0022, sin FK desde 0025):
  Amazon vs `listing` de Orbit; MeLi vs items del seller (todo lo que
  trae el scan es propio; el mapa bridge se uso en 0.1 para D2).
  Sin match = skip contado, fila NO escrita, nada rellenado.

Estructura extensible: A.4 agrega seller/preguntas/claims a este
modulo (mismo cliente MeLi) — no hay stubs, hay sitio.

Interpretacion del DoD "UNA transaccion" (A.2): los HECHOS
(snapshots/reviews) van en una sola transaccion (corte a mitad =
cero filas de hechos); el `ingest_run` se abre/sella fuera para
dejar evidencia del fallo (patron disponibilidad).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, redact_dsn, register_secret, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE_MELI = "reputacion_meli"
SOURCE_AMAZON = "reputacion_amazon"
MELI_BASE = "https://api.mercadolibre.com"
APIFY_BASE = "https://api.apify.com/v2"
ACTOR_JUNGLEE_DEFAULT = "junglee~Amazon-crawler"
# Medido E/0.2 con 2 corridas chicas; la primera corrida real fija el numero.
TARIFA_JUNGLEE_DEFAULT = 0.0025

DEFAULT_SECRETS_DIR = "/mnt/data/appdata/orbit/secrets"
MELI_TOKENS_FILENAME = "meli_tokens.json"
APIFY_TOKEN_FILENAME = "apify_token.json"


class ReputacionError(Exception):
    """Error de la ingesta de reputacion. El mensaje NUNCA lleva secretos."""


# ---------------------------------------------------------------------------
# Credenciales (patron app/ads/config.py)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise ReputacionError(f"no existe el archivo de credenciales: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ReputacionError(f"JSON invalido en {path.name}") from None
    if not isinstance(data, dict):
        raise ReputacionError(f"formato invalido en {path.name}: se esperaba objeto JSON")
    return data


def _require(data: dict, key: str, filename: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ReputacionError(f"falta la clave '{key}' (string no vacio) en {filename}")
    return value


@dataclass(repr=False)
class MeliCredentials:
    """Token MeLi + app para refresh. Un solo refrescador de SU archivo."""

    access_token: str
    refresh_token: str
    client_id: str
    client_secret: str
    ruta: Path

    def __repr__(self) -> str:
        return "MeliCredentials(***)"

    __str__ = __repr__

    @classmethod
    def from_secrets_dir(cls, secrets_dir: str | Path | None = None) -> MeliCredentials:
        base = Path(secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", DEFAULT_SECRETS_DIR))
        ruta = base / MELI_TOKENS_FILENAME
        data = _load_json(ruta)
        access = _require(data, "access_token", MELI_TOKENS_FILENAME)
        refresh = _require(data, "refresh_token", MELI_TOKENS_FILENAME)
        client_id = _require(data, "client_id", MELI_TOKENS_FILENAME)
        client_secret = _require(data, "client_secret", MELI_TOKENS_FILENAME)
        register_secret(access)
        register_secret(refresh)
        register_secret(client_secret)
        return cls(access, refresh, client_id, client_secret, ruta)

    def guardar_tokens(self, access_token: str, refresh_token: str) -> None:
        """Reescritura ATOMICA (tmp + rename) tras un refresh."""
        data = _load_json(self.ruta)
        data["access_token"] = access_token
        data["refresh_token"] = refresh_token
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.rename(self.ruta)
        self.access_token = access_token
        self.refresh_token = refresh_token
        register_secret(access_token)
        register_secret(refresh_token)


@dataclass(repr=False)
class ApifyCredentials:
    """Token Apify (solo lectura de datasets + corridas junglee pagadas)."""

    token: str

    def __repr__(self) -> str:
        return "ApifyCredentials(***)"

    __str__ = __repr__

    @classmethod
    def from_secrets_dir(cls, secrets_dir: str | Path | None = None) -> ApifyCredentials:
        base = Path(secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", DEFAULT_SECRETS_DIR))
        data = _load_json(base / APIFY_TOKEN_FILENAME)
        token = _require(data, "token", APIFY_TOKEN_FILENAME)
        register_secret(token)
        return cls(token)


# ---------------------------------------------------------------------------
# Cliente MeLi: solo GET, default-deny (A.4 lo reutiliza)
# ---------------------------------------------------------------------------


class ClienteMeli:
    """GET a api.mercadolibre.com. Cualquier otro metodo levanta sin red."""

    def __init__(
        self,
        creds: MeliCredentials,
        transport: httpx.BaseTransport | None = None,
        max_intentos: int = 3,
        backoff_base: float = 0.5,
        timeout: float = 30.0,
    ) -> None:
        self._creds = creds
        self._max_intentos = max_intentos
        self._backoff_base = backoff_base
        self._client = httpx.Client(base_url=MELI_BASE, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _request(self, metodo: str, path: str, params: dict | None = None) -> dict:
        if metodo != "GET":
            raise ReputacionError(f"cliente MeLi solo-GET: metodo {metodo} bloqueado")
        headers = {"Authorization": "Bearer " + self._creds.access_token}
        intento_401_hecho = False
        for intento in range(self._max_intentos):
            resp = self._client.request(metodo, path, params=params, headers=headers)
            if resp.status_code == 401:
                if intento_401_hecho:
                    raise ReputacionError(f"MeLi GET {path}: 401 tras refresh")
                intento_401_hecho = True
                self._refresh()
                headers = {"Authorization": "Bearer " + self._creds.access_token}
                continue
            if resp.status_code in (429,) or resp.status_code >= 500:
                if intento + 1 < self._max_intentos:
                    time.sleep(self._backoff_base * (2**intento))
                    continue
                raise ReputacionError(
                    f"MeLi GET {path}: agotados {self._max_intentos} intentos"
                    f" (ultimo {resp.status_code})"
                )
            if resp.status_code != 200:
                raise ReputacionError(f"MeLi GET {path} -> {resp.status_code}")
            data = resp.json()
            if not isinstance(data, dict):
                raise ReputacionError(f"MeLi GET {path}: respuesta no-objeto")
            return data
        raise ReputacionError(f"MeLi GET {path}: sin respuesta")  # inalcanzable

    def _refresh(self) -> None:
        resp = self._client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._creds.client_id,
                "client_secret": self._creds.client_secret,
                "refresh_token": self._creds.refresh_token,
            },
        )
        if resp.status_code != 200:
            raise ReputacionError(f"MeLi refresh -> {resp.status_code}")
        data = resp.json()
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            raise ReputacionError("MeLi refresh sin tokens en respuesta")
        self._creds.guardar_tokens(access, refresh)

    def get(self, path: str, params: dict | None = None) -> tuple[dict, dt.datetime]:
        """GET + instante de la respuesta (fetched_at). A.4 reusa este metodo."""
        data = self._request("GET", path, params)
        return data, dt.datetime.now(dt.UTC)

    def items_seller(self, seller_id: int, max_paginas: int = 50) -> list[str]:
        """IDs de items via scan paginado (E/0.3: 65 con scroll_id)."""
        items: list[str] = []
        params: dict = {"search_type": "scan", "limit": 100}
        for _ in range(max_paginas):
            data, _ = self.get(f"/users/{seller_id}/items/search", params)
            resultados = data.get("results") or []
            items.extend(str(i) for i in resultados)
            scroll = data.get("scroll_id")
            if not scroll or not resultados:
                break
            params = {"search_type": "scan", "scroll_id": scroll}
        return items


# ---------------------------------------------------------------------------
# Cliente junglee: run-sync pagado, topes duros (cada intento gasta)
# ---------------------------------------------------------------------------


class ClienteJunglee:
    """Snapshots Amazon via junglee/Amazon-crawler (E/0.2 verificado)."""

    def __init__(
        self,
        creds: ApifyCredentials,
        actor: str = ACTOR_JUNGLEE_DEFAULT,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 300.0,
        tarifa_usd_por_producto: float = TARIFA_JUNGLEE_DEFAULT,
    ) -> None:
        self._creds = creds
        self._actor = actor
        self._tarifa = tarifa_usd_por_producto
        self._client = httpx.Client(base_url=APIFY_BASE, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def scrape_productos(
        self,
        urls: list[str],
        max_items_por_url: int = 1,
        max_productos: int = 600,
        tope_usd: float = 2.0,
    ) -> tuple[list[dict], float]:
        """Corre junglee sobre URLs /dp/ASIN. Devuelve (items, costo_est).

        Topes ANTES de gastar: n productos y costo estimado. Sin
        reintentos automaticos: cada intento cobra. `productPageReviews`
        se ignora (hallazgo no verificado E/0.2).
        """
        if len(urls) > max_productos:
            raise ReputacionError(
                f"junglee: {len(urls)} productos exceden max_productos={max_productos}"
            )
        costo_est = len(urls) * self._tarifa
        if costo_est > tope_usd:
            raise ReputacionError(
                f"junglee: costo est ${costo_est:.2f} excede tope_usd=${tope_usd:.2f}"
            )
        resp = self._client.post(
            f"/acts/{self._actor}/run-sync-get-dataset-items?token={self._creds.token}",
            json={
                "categoryOrProductUrls": [{"url": u} for u in urls],
                "maxItemsPerStartUrl": max_items_por_url,
            },
        )
        if resp.status_code not in (200, 201):
            raise ReputacionError(f"junglee run-sync -> {resp.status_code}")
        items = resp.json()
        if not isinstance(items, list):
            raise ReputacionError("junglee: respuesta no-lista")
        return [i for i in items if isinstance(i, dict)], costo_est


# ---------------------------------------------------------------------------
# Planes puros (sin DB ni red)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanSnapshot:
    plataforma: str
    external_id: str
    alcance: str = "listing"
    rating: float | None = None
    review_count: int | None = None
    parent_asin: str | None = None
    fetched_at: dt.datetime | None = None
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class PlanReview:
    plataforma: str
    external_id: str
    review_external_id: str
    rating: int | None = None
    titulo: str | None = None
    texto: str | None = None
    publicada: bool = True
    published_at: dt.datetime | None = None
    fetched_at: dt.datetime | None = None


def _rating_1_5(valor) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if 1 <= numero <= 5 else None


def _entero_no_negativo(valor) -> int | None:
    if isinstance(valor, bool):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero >= 0 else None


def _instante(valor) -> dt.datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        momento = dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return momento if momento.tzinfo is not None else momento.replace(tzinfo=dt.UTC)


def plan_snapshot_meli(
    item_id: str,
    item: dict,
    reviews: dict,
    fetched_at: dt.datetime,
) -> tuple[PlanSnapshot | None, list[PlanReview], Counter]:
    """Snapshot + reviews de un item MeLi (shapes E/0.3). Puro."""
    skips: Counter = Counter()
    total = (reviews.get("paging") or {}).get("total")
    review_count = _entero_no_negativo(total)
    if review_count is None and total is not None:
        skips["meli: paging.total invalido (se descarta)"] += 1
        return None, [], skips
    # E/0.3: total=0 trae avg=0; ese 0 es sin-dato, no rating.
    rating = None
    if (review_count or 0) > 0:
        rating = _rating_1_5(reviews.get("rating_average"))
        if rating is None and reviews.get("rating_average") is not None:
            skips["meli: rating_average fuera de [1,5] (NULL, no inventado)"] += 1
    extra: dict = {}
    health = item.get("health")
    if isinstance(health, (int, float)) and not isinstance(health, bool):
        extra["health"] = health
    levels = reviews.get("rating_levels")
    if isinstance(levels, dict):
        extra["levels"] = {k: v for k, v in levels.items() if _entero_no_negativo(v) is not None}
    snapshot = PlanSnapshot(
        plataforma="meli",
        external_id=item_id,
        rating=rating,
        review_count=review_count if review_count is not None else 0,
        fetched_at=fetched_at,
        extra=extra,
    )
    eventos: list[PlanReview] = []
    crudas = reviews.get("reviews")
    if not isinstance(crudas, list):
        if crudas is not None:
            skips["meli: reviews no-lista (se descarta)"] += 1
        return snapshot, [], skips
    for rv in crudas:
        if not isinstance(rv, dict) or rv.get("id") is None:
            skips["meli: review sin id (se descarta)"] += 1
            continue
        rate = _entero_no_negativo(rv.get("rate"))
        if rate is not None and not 1 <= rate <= 5:
            skips["meli: rate fuera de [1,5] (NULL, no inventado)"] += 1
            rate = None
        eventos.append(
            PlanReview(
                plataforma="meli",
                external_id=item_id,
                review_external_id=str(rv["id"]),
                rating=rate,
                titulo=rv.get("title") if isinstance(rv.get("title"), str) else None,
                texto=rv.get("content") if isinstance(rv.get("content"), str) else None,
                publicada=rv.get("status", "published") == "published",
                published_at=_instante(rv.get("date_created")),
                fetched_at=fetched_at,
            )
        )
    return snapshot, eventos, skips


def plan_snapshot_junglee(
    pedido: str,
    item: dict,
    fetched_at: dt.datetime,
    plataforma: str,
) -> tuple[PlanSnapshot | None, Counter]:
    """Snapshot Amazon desde un item junglee (E/0.2). Puro."""
    skips: Counter = Counter()
    rating = _rating_1_5(item.get("stars"))
    if rating is None and item.get("stars") is not None:
        skips["amazon: stars fuera de [1,5] o invalido (NULL)"] += 1
    review_count = _entero_no_negativo(item.get("reviewsCount"))
    if review_count is None and item.get("reviewsCount") is not None:
        skips["amazon: reviewsCount invalido (NULL)"] += 1
    padre = item.get("asin")
    return (
        PlanSnapshot(
            plataforma=plataforma,
            external_id=pedido,
            rating=rating,
            review_count=review_count,
            parent_asin=str(padre) if padre else None,
            fetched_at=fetched_at,
        ),
        skips,
    )


# ---------------------------------------------------------------------------
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
                resultado = sync_meli(conn, cliente, observed_at, metric_date)
                resumen = _resumen(resultado)
            else:
                assert isinstance(cliente, ClienteJunglee)
                resultado = sync_amazon(
                    conn, cliente, observed_at, metric_date, max_productos, tope_usd
                )
                resumen = _resumen(resultado)
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


def _resumen(resultado: ResultadoReputacion) -> dict:
    return {
        "fuente": resultado.fuente,
        "ok": resultado.ok,
        "run_id": resultado.run_id,
        "filas_insertadas": resultado.filas_insertadas,
        "filas_idempotentes": resultado.filas_idempotentes,
        "skips": resultado.skips,
        "costo_usd": resultado.costo_usd,
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
        return {"fuente": fuente, "ok": True, "dry_run": True, "items_vistos": len(items)}
    assert isinstance(cliente, ClienteJunglee)
    catalogo = conn.execute(
        "SELECT count(*) FROM listing WHERE platform IN ('amazon_mx', 'amazon_us')"
    ).fetchone()[0]
    return {"fuente": fuente, "ok": True, "dry_run": True, "asins_catalogo": catalogo}
