"""Catalogo Jev: censo con huecos explicitos, fichas aprobadas y vigencia.

JEV ADS 01 (1.2). Adaptador de DB para los tipos puros de `app/jev_ads.py`.
El producto se resuelve por IDs, jamas por parecido entre nombres (diseno
"Catalogo y reglas de composicion").

- El censo parte de `ad_group -> product_ad` con LEFT JOIN a
  `ad_entity_state` y `listing` y conserva los faltantes: un anuncio sin
  listing sigue en el censo (con producto sin resolver), un anuncio sin
  fila de estado conserva `status`/`synced_at` en None, y NINGUNO se
  descarta por su estado (la exclusion del ARCHIVED es regla de
  composicion en `app.jev_ads.componer`, no del censo).
- El universo de grupos Amazon es `desconocido` en V1: `censo_grupo`
  entrega `exhaustivo=False` SIEMPRE (E/0.2: el sincronizador no prueba
  exhaustividad; produccion tiene listings faltantes 1/33 MX y 24/48 US).
- `ficha_vigente` aplica la regla del diseno: sigue aprobada (sin
  revocacion), cubre ese listing y no vencio su revision.
- `registrar_ficha` es idempotente por hash canonico del contenido; cada
  correccion inserta una version nueva. `revocar_ficha` inserta el evento
  append-only; una segunda revocacion se rechaza.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Json

from app.jev_ads import (
    CensoCongelado,
    EstadoAnuncio,
    FichaVersion,
    HechoConFuente,
    MiembroCenso,
)

_PLATAFORMAS = ("amazon_mx", "amazon_us")

_FICHA_SELECT = (
    "SELECT f.id, f.producto_id, f.plataforma, f.listings, f.hechos, f.desconocidos,"
    " f.aprobador, f.observado_at, f.revisar_antes_de, f.sha256"
    " FROM jev_ficha_version f"
)


def _utc_iso(valor: datetime) -> str:
    if valor.tzinfo is None:
        raise ValueError("las fechas de la ficha deben venir con zona (UTC)")
    return valor.astimezone(UTC).isoformat()


def hash_ficha(
    *,
    producto_id: int,
    plataforma: str,
    listings: Iterable[int],
    hechos: Sequence[tuple[str, str]],
    desconocidos: Iterable[str],
    aprobador: str,
    observado_at: datetime,
    revisar_antes_de: datetime,
) -> str:
    """Hash canonico del contenido de la ficha (sha256 hex). Mismo contenido,
    mismo hash: la idempotencia del registro es la del hash."""
    contenido = {
        "aprobador": aprobador,
        "desconocidos": sorted(desconocidos),
        "hechos": [{"fuente": fuente, "texto": texto} for texto, fuente in hechos],
        "listings": sorted(listings),
        "observado_at": _utc_iso(observado_at),
        "plataforma": plataforma,
        "producto_id": producto_id,
        "revisar_antes_de": _utc_iso(revisar_antes_de),
    }
    canonico = json.dumps(contenido, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _hechos_de_json(valor: object) -> tuple[HechoConFuente, ...]:
    if not isinstance(valor, list):
        raise ValueError("hechos de ficha con forma inesperada")
    hechos: list[HechoConFuente] = []
    for item in valor:
        if not isinstance(item, dict) or set(item) != {"texto", "fuente"}:
            raise ValueError("hecho de ficha sin {texto, fuente}")
        hechos.append(HechoConFuente(texto=item["texto"], fuente=item["fuente"]))
    return tuple(hechos)


def _ficha_de_fila(
    fila: tuple[UUID, int, str, list[int], object, list[str], str, datetime, datetime, str],
) -> FichaVersion:
    (
        ficha_id,
        producto_id,
        plataforma,
        listings,
        hechos,
        desconocidos,
        aprobador,
        observado_at,
        revisar_antes_de,
        sha,
    ) = fila
    return FichaVersion(
        id=ficha_id,
        producto_id=producto_id,
        plataforma=plataforma,  # type: ignore[arg-type]
        listings=frozenset(listings),
        hechos=_hechos_de_json(hechos),
        desconocidos=frozenset(desconocidos),
        aprobador=aprobador,
        observado_at=observado_at,
        revisar_antes_de=revisar_antes_de,
        sha256=sha,
    )


@dataclass(frozen=True)
class RegistroFicha:
    """Resultado de registrar: la ficha y si ya existia (idempotencia)."""

    ficha: FichaVersion
    ya_existia: bool


def registrar_ficha(
    conn: psycopg.Connection,
    *,
    producto_id: int,
    plataforma: str,
    listings: Sequence[int],
    hechos: Sequence[tuple[str, str]],
    desconocidos: Iterable[str],
    aprobador: str,
    observado_at: datetime,
    revisar_antes_de: datetime,
) -> RegistroFicha:
    """Inserta la version de ficha aprobada; mismo contenido -> misma fila.

    La base muerde con triggers: listing inexistente o ajeno al producto y
    plataforma, fechas incoherentes o listings duplicados. Con el mismo
    hash devuelve la fila existente (ya_existia=True); cualquier correccion
    de contenido inserta version nueva.
    """
    if plataforma not in _PLATAFORMAS:
        raise ValueError(f"plataforma fuera del alcance Jev: {plataforma}")
    # Materializar UNA vez: hash, INSERT y FichaVersion deben ver el MISMO
    # contenido aunque el llamador pase iterables de un solo uso (B2-r4 F2).
    listings = tuple(listings)
    hechos = tuple(hechos)
    desconocidos = sorted(desconocidos)
    sha = hash_ficha(
        producto_id=producto_id,
        plataforma=plataforma,
        listings=listings,
        hechos=hechos,
        desconocidos=desconocidos,
        aprobador=aprobador,
        observado_at=observado_at,
        revisar_antes_de=revisar_antes_de,
    )
    fila = conn.execute(f"{_FICHA_SELECT} WHERE f.sha256 = %s", (sha,)).fetchone()
    if fila is not None:
        return RegistroFicha(ficha=_ficha_de_fila(fila), ya_existia=True)
    ficha_id = uuid4()
    conn.execute(
        "INSERT INTO jev_ficha_version (id, producto_id, plataforma, listings, hechos,"
        " desconocidos, sha256, aprobador, observado_at, revisar_antes_de)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            ficha_id,
            producto_id,
            plataforma,
            list(listings),
            Json([{"texto": texto, "fuente": fuente} for texto, fuente in hechos]),
            list(desconocidos),
            sha,
            aprobador,
            observado_at,
            revisar_antes_de,
        ),
    )
    return RegistroFicha(
        ficha=FichaVersion(
            id=ficha_id,
            producto_id=producto_id,
            plataforma=plataforma,  # type: ignore[arg-type]
            listings=frozenset(listings),
            hechos=tuple(HechoConFuente(texto=texto, fuente=fuente) for texto, fuente in hechos),
            desconocidos=frozenset(desconocidos),
            aprobador=aprobador,
            observado_at=observado_at,
            revisar_antes_de=revisar_antes_de,
            sha256=sha,
        ),
        ya_existia=False,
    )


def revocar_ficha(
    conn: psycopg.Connection,
    *,
    ficha_version_id: UUID,
    autor: str,
    motivo: str,
    fecha: datetime | None = None,
) -> None:
    """Inserta el evento de revocacion (append-only). Una ficha ya revocada
    levanta ValueError; una ficha inexistente, la FK."""
    try:
        conn.execute(
            "INSERT INTO jev_ficha_revocacion (ficha_version_id, autor, motivo, fecha)"
            " VALUES (%s, %s, %s, COALESCE(%s, now()))",
            (ficha_version_id, autor, motivo, fecha),
        )
    except psycopg.errors.UniqueViolation as error:
        raise ValueError(f"ficha {ficha_version_id} ya revocada") from error


def ficha_vigente(
    conn: psycopg.Connection,
    *,
    producto_id: int,
    plataforma: str,
    listing_id: int,
    ahora: datetime,
) -> FichaVersion | None:
    """La ficha aprobada vigente que cubre el listing: sin revocacion, cubre
    ese listing y con revisar_antes_de no vencido. Ultima por observado_at."""
    fila = conn.execute(
        f"{_FICHA_SELECT}"
        " LEFT JOIN jev_ficha_revocacion r ON r.ficha_version_id = f.id"
        " WHERE f.producto_id = %s AND f.plataforma = %s"
        "   AND r.id IS NULL AND f.revisar_antes_de >= %s"
        "   AND f.listings @> ARRAY[%s]::bigint[]"
        " ORDER BY f.observado_at DESC, f.created_at DESC"
        " LIMIT 1",
        (producto_id, plataforma, ahora, listing_id),
    ).fetchone()
    return _ficha_de_fila(fila) if fila is not None else None


def censo_grupo(conn: psycopg.Connection, *, plataforma: str, ad_group_id: int) -> CensoCongelado:
    """Universo congelado del grupo Amazon con huecos explicitos.

    Conserva la identidad de cada anuncio ANTES de deduplicar productos: el
    anuncio sin listing se conserva como miembro sin producto resuelto y el
    anuncio sin estado no se descarta (no se consulta estado). El producto
    sale por IDs (FK de listing), jamas por parecido de nombres.
    """
    filas = conn.execute(
        "SELECT pa.id AS anuncio_id, pa.listing_id, l.product_id, s.status, s.synced_at"
        "  FROM ad_entity ag"
        "  JOIN ad_entity pa"
        "    ON pa.platform = ag.platform AND pa.kind = 'product_ad'"
        "   AND pa.parent_id = ag.id"
        "  LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id"
        "  LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform"
        " WHERE ag.id = %s AND ag.kind = 'ad_group' AND ag.platform = %s"
        " ORDER BY pa.id",
        (ad_group_id, plataforma),
    ).fetchall()
    anuncios_por_producto: dict[int, list[int]] = {}
    listings_por_producto: dict[int, set[int]] = {}
    estados_por_producto: dict[int, list[EstadoAnuncio]] = {}
    sueltos: list[MiembroCenso] = []
    for anuncio_id, listing_id, producto_id, status, synced_at in filas:
        estado = EstadoAnuncio(status=status, synced_at=synced_at)
        if producto_id is None:
            sueltos.append(
                MiembroCenso(
                    anuncio_ids=(anuncio_id,),
                    producto_id=None,
                    listing_ids=frozenset({listing_id} if listing_id else ()),
                    estados=(estado,),
                )
            )
            continue
        anuncios_por_producto.setdefault(producto_id, []).append(anuncio_id)
        if listing_id is not None:
            listings_por_producto.setdefault(producto_id, set()).add(listing_id)
        estados_por_producto.setdefault(producto_id, []).append(estado)
    miembros = [
        MiembroCenso(
            anuncio_ids=tuple(anuncios_por_producto[producto_id]),
            producto_id=producto_id,
            listing_ids=frozenset(listings_por_producto.get(producto_id, ())),
            estados=tuple(estados_por_producto[producto_id]),
        )
        for producto_id in sorted(anuncios_por_producto)
    ]
    return CensoCongelado(miembros=tuple([*miembros, *sueltos]), exhaustivo=False)
