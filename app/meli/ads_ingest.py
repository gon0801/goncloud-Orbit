"""Ingesta de metricas de Mercado Libre Ads desde contabilidad (RESUMEN-ADS-01).

Fuente: snapshot READ-ONLY de la SQLite de accounting,
`meli_ads_campaigns_daily` (grano campana/dia, PK `(date, campaign_id)`), la
tabla que escribe `sync_meli_ads.py`. El adaptador y la forma canonica viven en
`app.meli.ads_source`; este modulo es el UNICO dueno de la persistencia y de
la politica temporal.

Destino: `ad_entity` (solo `platform='meli'`, `kind='campaign'`) y
`ads_metric_observation` (append-only). No hay migracion: se reutilizan el
esquema, el dedupe `metric_dedupe_reporte` y `v_metric_latest`.

Modelo temporal (sin columnas nuevas; el esquema no tiene watermark):

- Cada corrida exitosa sella una ACEPTACION con el as-of y la huella SHA-256
  de TODAS las filas canonicas (campana, fecha, moneda y metricas, en JSON
  inequivoco). `snap=<huella>` reconoce un reintento exacto aunque otra
  corrida haya cambiado despues la observacion vigente.
- Hay DOS relojes: `as_of=<epoch>` (snapshots reales) y `futuro=<epoch>`
  (aceptados como futuros). Un futuro queda en CUARENTENA para siempre: el
  avance del reloj no lo reclasifica. Cada corrida escribe solo la marca de
  su reloj; una primera corrida futura no escribe `as_of=`.
- Una aceptacion futura conserva el piso real heredado en `piso=<epoch>`. Una
  base legada sin marcas no pierde su piso cuando entra un futuro.
- Un reintento exacto (mismo as-of y huella) es un NO-OP completo: no escribe
  entidades, no toca nombres y no agrega observaciones ni aceptaciones. Se
  reconoce contra la aceptacion anterior, nunca solo contra `v_metric_latest`.
  Un snapshot vacio tambien es un no-op exitoso.
- Un snapshot con el mismo as-of y contenido distinto se rechaza completo.
- Un snapshot atrasado nunca aceptado se rechaza completo, aunque sus metricas
  coincidan. Una marca futura NO bloquea una correccion real valida.
- Una recopia real deduplicada conserva su nuevo as-of, avanza el reloj real
  y bloquea snapshots intermedios.
- Una aceptacion anterior puede no tener `snap=`. El primer reintento exacto
  se reconoce contra la observacion vigente completa; si hubo una correccion
  intermedia, se rechaza fail-closed.

Idempotencia: `source_report_id =
meli-ads|<campaign_id>|<fecha>|<version>|<as_of_epoch>` y
`INSERT ... ON CONFLICT DO NOTHING` contra `metric_dedupe_reporte`. El
`ingest_run_id` NUNCA es clave de dedupe. Cada snapshot es atomico: una fila
invalida o un snapshot atrasado aborta la corrida completa sin escrituras
parciales y sella `ingest_run.ok=false` con el motivo. Todo motivo persistido
se escapa antes de llegar a PostgreSQL (NUL y caracteres de control incluidos).

`python -m app.cli ingest meli-ads --sqlite RUTA`: sin ORBIT_DSN_INGEST o sin
snapshot -> exit 2 fail-closed; fallo de la corrida -> exit 1; exito -> 0.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg

from app.db import connect
from app.meli.ads_source import (
    KIND,
    PLATAFORMA,
    FilaMeliAds,
    MeliAdsError,
    OrigenMeliAds,
    leer_origen,
)
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_meli_ads_campaigns"
_SKEW_RELOJ = dt.timedelta(minutes=5)  # futuro que aun se considera reloj real

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

# El nombre se actualiza solo cuando el origen trae uno distinto: uno ausente
# (NULL) no borra el ya publicado. Sin cambio real, el WHERE corta el UPDATE y
# no hay escritura. Un reintento exacto ni siquiera llega aqui: es un no-op.
_SQL_UPSERT_ENTIDAD = f"""
INSERT INTO ad_entity (platform, kind, external_id, name)
VALUES ('{PLATAFORMA}', '{KIND}', %s, %s)
ON CONFLICT (platform, kind, external_id) DO UPDATE
   SET name = EXCLUDED.name
 WHERE EXCLUDED.name IS NOT NULL
   AND ad_entity.name IS DISTINCT FROM EXCLUDED.name
RETURNING id, (xmax = 0) AS es_nueva
"""

_SQL_ID_ENTIDAD = (
    f"SELECT id FROM ad_entity WHERE platform = '{PLATAFORMA}'"
    f" AND kind = '{KIND}' AND external_id = %s"
)

# Ultima observacion por campana/dia: su version y su as-of viven en el
# source_report_id. La consulta se hace dentro de la transaccion de escritura.
_SQL_ULTIMAS = f"""
SELECT DISTINCT ON (e.external_id, o.metric_date)
       e.external_id, o.metric_date, o.source_report_id,
       o.cost, o.ad_revenue, o.impressions, o.clicks, o.orders, o.metric_currency
  FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = '{PLATAFORMA}' AND e.kind = '{KIND}'
 ORDER BY e.external_id, o.metric_date, o.observed_at DESC
"""

# Serializa SOLO las ingestas de MeLi: dos corridas concurrentes no pueden
# leer el mismo estado temporal y escribir versiones distintas.
_SQL_LOCK_MELI = "SELECT pg_advisory_xact_lock(%s, %s)"
_LOCK_MELI = (7071, 1)

# Reloj logico de observacion: si el reloj de pared retrocede, el nuevo
# observed_at debe superar al maximo ya publicado o `v_metric_latest` elegiria
# la fila anterior. Solo mira MeLi: no acopla el reloj de Amazon.
_SQL_MAX_OBSERVADO = f"""
SELECT max(o.observed_at)
  FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = '{PLATAFORMA}' AND e.kind = '{KIND}'
"""

# Aceptaciones y piso real de TODOS los snapshots exitosos. Viven en el
# skip_reason porque el esquema no tiene columna de watermark y esta fase no
# crea migraciones. El id de ingest_run no sirve como orden: los runs se abren
# antes del candado y pueden terminar en otro orden. Se toma el maximo de cada
# reloj, no la ultima corrida por id. Los reintentos no escriben marcas, asi
# que no agregan aceptaciones nuevas.
_SQL_MARCA = (
    "SELECT skip_reason FROM ingest_run"
    " WHERE source = %s AND ok = true"
    " AND (skip_reason LIKE 'as_of=%%' OR skip_reason LIKE 'futuro=%%'"
    " OR skip_reason LIKE 'piso=%%')"
)
_MARCA_RE = re.compile(r"^as_of=(\d+)(?:;|$)")
_FUTURO_RE = re.compile(r"(?:^|; )futuro=(\d+)")
_PISO_RE = re.compile(r"(?:^|; )piso=(\d+)")
_HUELLA_RE = re.compile(r"(?:^|; )snap=([0-9a-f]{64})(?:;|$)")

# Piso real de observaciones LEGADAS: as-of maximo de MeLi excluyendo los
# as-ofs que fueron aceptados como futuros. Se usa solo cuando no hay marca
# real ni piso explicito; asi una base legada no pierde su orden cuando entra
# un futuro.
_SQL_MAX_AS_OF = f"""
SELECT max(CASE WHEN epoch_texto ~ '^[0-9]+$' THEN epoch_texto::bigint END)
  FROM (
        SELECT split_part(o.source_report_id, '|', 5) AS epoch_texto
          FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
         WHERE e.platform = '{PLATAFORMA}' AND e.kind = '{KIND}'
           AND o.source_report_id LIKE 'meli-ads|%%'
       ) s
 WHERE NOT (
       CASE WHEN epoch_texto ~ '^[0-9]+$' THEN epoch_texto::bigint END
       = ANY(%s::bigint[])
 )
"""

# Observaciones escritas con un as-of concreto. Sirven para distinguir un
# duplicado concurrente del snapshot entrante de un contenido legado distinto
# con el mismo as-of: el primero se acepta y se deduplica; el segundo se
# rechaza completo. No basta mirar la observacion vigente, que puede haber sido
# tapada por una correccion posterior.
_SQL_OBSERVACIONES_AS_OF = f"""
SELECT e.external_id, o.metric_date, o.source_report_id,
       o.cost, o.ad_revenue, o.impressions, o.clicks, o.orders, o.metric_currency
  FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = '{PLATAFORMA}' AND e.kind = '{KIND}'
   AND split_part(o.source_report_id, '|', 5) = %s
"""

_SQL_INSERTAR_OBS = """
INSERT INTO ads_metric_observation
    (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,
     impressions, clicks, orders, source_report_id, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ad_entity_id, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL
DO NOTHING
"""


@dataclass(frozen=True)
class _Aceptacion:
    """Snapshot completo aceptado: as-of, reloj y huella de sus filas."""

    as_of: int
    es_futuro: bool
    huella: str | None


@dataclass(frozen=True)
class _Marcas:
    """Estado temporal leido una sola vez bajo el candado."""

    aceptaciones: tuple[_Aceptacion, ...]
    real: int
    futuro: int
    piso: int


def _texto_persistible(texto: str | None) -> str | None:
    """Escapa NUL y controles (C0 y C1) antes de persistir un motivo.

    PostgreSQL rechaza NUL en columnas de texto; si el sello de la corrida
    falla con el mismo error, la corrida queda abierta. El escape es central:
    no depende de que cada validacion limpie su propio mensaje. Se usa la
    categoria Unicode Cc para cubrir tambien los controles C1 (U+0085, etc.).
    """
    if texto is None:
        return None
    return "".join(
        f"\\x{ord(caracter):02x}" if unicodedata.category(caracter) == "Cc" else caracter
        for caracter in texto
    )


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def _sellar_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    rows_written: int,
    rows_skipped: int,
    skip_reason: str | None,
) -> None:
    conn.execute(
        _SQL_SELLAR_RUN,
        (rows_written, rows_skipped, _texto_persistible(skip_reason), ok, run_id),
    )


def _entidad(
    conn: psycopg.Connection,
    campaign_external_id: str,
    nombre: str | None,
) -> tuple[int, bool, bool]:
    """Devuelve (id, es_nueva, nombre_actualizado).

    Un nombre ausente en el origen no borra el ya publicado. El renombre de un
    snapshot atrasado no llega aqui: ese snapshot se rechaza antes.
    """
    resultado = conn.execute(_SQL_UPSERT_ENTIDAD, (campaign_external_id, nombre)).fetchone()
    if resultado is not None:
        return int(resultado[0]), bool(resultado[1]), not bool(resultado[1])
    return int(conn.execute(_SQL_ID_ENTIDAD, (campaign_external_id,)).fetchone()[0]), False, False


def _nombres_vigentes(filas: list[FilaMeliAds]) -> dict[str, str]:
    """Nombre mas reciente por campana dentro del snapshot.

    Un renombre a mitad de la ventana deja nombres viejos en las filas viejas;
    el catalogo debe quedarse con el nombre de la fecha mas nueva, no con el
    de la primera fila que se procesa.
    """
    nombres: dict[str, str] = {}
    for fila in sorted(filas, key=lambda f: (f.metric_date, f.campaign_external_id)):
        if fila.campaign_name is not None:
            nombres[fila.campaign_external_id] = fila.campaign_name
    return nombres


def _huella_snapshot(filas: tuple[FilaMeliAds, ...]) -> str:
    """Huella inequivoca del snapshot COMPLETO.

    Incluye campana, fecha, moneda, TODAS las metricas y la version/revision.
    Se serializa como JSON: los separadores, comillas y controles dentro de los
    textos quedan escapados, asi que dos snapshots estructuralmente distintos
    no pueden producir los mismos bytes. No incluye el nombre: renombrar no
    cambia el hecho monetario.
    """
    filas_ordenadas = sorted(filas, key=lambda f: (f.metric_date, f.campaign_external_id))
    payload = [
        [
            fila.campaign_external_id,
            fila.metric_date.isoformat(),
            fila.metric_currency,
            None if fila.cost is None else format(fila.cost, "f"),
            None if fila.ad_revenue is None else format(fila.ad_revenue, "f"),
            fila.impressions,
            fila.clicks,
            fila.orders,
            fila.source_version,
        ]
        for fila in filas_ordenadas
    ]
    datos = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(datos.encode("utf-8")).hexdigest()


def _marca_de_as_of(skip_reason: str | None) -> int | None:
    """Lee el epoch del marcador `as_of=<epoch>` de una corrida exitosa."""
    if not skip_reason:
        return None
    coincidencia = _MARCA_RE.match(skip_reason)
    return int(coincidencia.group(1)) if coincidencia else None


def _futuro_de_marca(skip_reason: str | None) -> int | None:
    """Lee el epoch del marcador `futuro=<epoch>` de una corrida exitosa."""
    if not skip_reason:
        return None
    coincidencia = _FUTURO_RE.search(skip_reason)
    return int(coincidencia.group(1)) if coincidencia else None


def _piso_de_marca(skip_reason: str | None) -> int | None:
    """Lee el epoch del piso real `piso=<epoch>` de una corrida futura."""
    if not skip_reason:
        return None
    coincidencia = _PISO_RE.search(skip_reason)
    return int(coincidencia.group(1)) if coincidencia else None


def _huella_de_marca(skip_reason: str | None) -> str | None:
    """Lee la huella `snap=<sha256>` de una aceptacion, si existe."""
    if not skip_reason:
        return None
    coincidencia = _HUELLA_RE.search(skip_reason)
    return coincidencia.group(1) if coincidencia else None


def _version_y_as_of(source_report_id: str) -> tuple[str, int] | None:
    """Recupera (version, as_of_epoch) del id de una observacion de MeLi.

    None = id con formato desconocido (no se puede ordenar: fail-closed).
    """
    try:
        sin_epoch, epoch_texto = source_report_id.rsplit("|", 1)
        _, version = sin_epoch.rsplit("|", 1)
        return version, int(epoch_texto)
    except (AttributeError, ValueError):
        return None


def _contenido_desde(
    cost: Decimal | None,
    ad_revenue: Decimal | None,
    impressions: int | None,
    clicks: int | None,
    orders: int | None,
    moneda: str,
) -> tuple:
    """Contenido canonico completo de un hecho campana/dia (sin el nombre)."""
    return (cost, ad_revenue, impressions, clicks, orders, moneda)


def _contenido(fila: FilaMeliAds) -> tuple:
    return _contenido_desde(
        fila.cost, fila.ad_revenue, fila.impressions, fila.clicks, fila.orders, fila.metric_currency
    )


def _versiones_snapshot(filas: tuple[FilaMeliAds, ...]) -> set[tuple[str, dt.date, tuple]]:
    """Contenido canonico completo por campana/dia del snapshot entrante."""
    return {(fila.campaign_external_id, fila.metric_date, _contenido(fila)) for fila in filas}


def _observaciones_en_as_of(
    conn: psycopg.Connection, as_of_epoch: int
) -> set[tuple[str, dt.date, tuple]]:
    """Contenido canonico completo ya escrito con ese as-of.

    Son conjuntos porque dos contenidos distintos para la misma campana/dia
    no pueden colapsar en uno y clasificar mal el snapshot.
    """
    versiones: set[tuple[str, dt.date, tuple]] = set()
    for external_id, metric_date, report_id, *valores in conn.execute(
        _SQL_OBSERVACIONES_AS_OF, (str(as_of_epoch),)
    ):
        partes = _version_y_as_of(report_id)
        if partes is None:
            continue
        version, epoch = partes
        if epoch == as_of_epoch:
            versiones.add((external_id, metric_date, _contenido_desde(*valores)))
    return versiones


def _lee_marcas(conn: psycopg.Connection) -> _Marcas:
    """Lee UNA vez aceptaciones, relojes y piso de todas las corridas exitosas."""
    aceptaciones: list[_Aceptacion] = []
    reales: list[int] = []
    futuras: list[int] = []
    pisos: list[int] = []
    for (skip_reason,) in conn.execute(_SQL_MARCA, (SOURCE,)).fetchall():
        huella = _huella_de_marca(skip_reason)
        if (real := _marca_de_as_of(skip_reason)) is not None:
            aceptaciones.append(_Aceptacion(real, False, huella))
            reales.append(real)
        if (futuro := _futuro_de_marca(skip_reason)) is not None:
            aceptaciones.append(_Aceptacion(futuro, True, huella))
            futuras.append(futuro)
        if (piso := _piso_de_marca(skip_reason)) is not None:
            pisos.append(piso)
    return _Marcas(
        aceptaciones=tuple(aceptaciones),
        real=max(reales, default=-1),
        futuro=max(futuras, default=-1),
        piso=max(pisos, default=-1),
    )


def _aceptaciones(conn: psycopg.Connection) -> list[_Aceptacion]:
    """Todas las aceptaciones registradas, con su huella de snapshot."""
    return list(_lee_marcas(conn).aceptaciones)


def _marcas_corridas(conn: psycopg.Connection) -> tuple[int, int]:
    """Maximos as-of REAL y FUTURO de todas las corridas exitosas.

    Son dos relojes separados. Un as-of que fue futuro al aceptarse queda en
    cuarentena y no bloquea correcciones reales aunque el reloj se acerque.
    """
    marcas = _lee_marcas(conn)
    return marcas.real, marcas.futuro


def _max_as_of_observaciones(conn: psycopg.Connection, futuras: set[int], as_of_epoch: int) -> int:
    """Piso real de observaciones legadas, sin as-ofs futuros ni el entrante.

    El as-of entrante se excluye porque una observacion con ese mismo as-of es
    el snapshot que se esta evaluando (o un duplicado concurrente), no un piso
    anterior. El conflicto de contenido con el mismo as-of lo resuelve la
    aceptacion registrada, no este piso.
    """
    excluidas = sorted(futuras | {as_of_epoch})
    fila = conn.execute(_SQL_MAX_AS_OF, (excluidas,)).fetchone()
    if fila is None or fila[0] is None:
        return -1
    return int(fila[0])


def _estado_temporal(
    conn: psycopg.Connection,
    ultimas: dict[tuple[str, dt.date], tuple[str, int]],
    as_of_epoch: int,
) -> tuple[dt.datetime, int, _Marcas, int]:
    """Reloj logico de observacion y marcas real/futura/piso bajo el candado."""
    ahora_reloj = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    max_observado = conn.execute(_SQL_MAX_OBSERVADO).fetchone()[0]
    observed_at = (
        max(ahora_reloj, max_observado + dt.timedelta(microseconds=1))
        if max_observado is not None
        else ahora_reloj
    )
    marcas = _lee_marcas(conn)
    # El piso real explicito conserva la base legada aunque exista un futuro.
    # Sin marca real ni piso, las observaciones cuyo as-of NO fue aceptado como
    # futuro son el unico orden disponible. Una marca futura jamas se vuelve
    # real, pero tampoco borra el piso heredado.
    marca_real = max(marcas.real, marcas.piso)
    if marca_real < 0:
        futuras = {a.as_of for a in marcas.aceptaciones if a.es_futuro}
        marca_real = _max_as_of_observaciones(conn, futuras, as_of_epoch)
    limite_real = int(ahora_reloj.timestamp()) + int(_SKEW_RELOJ.total_seconds())
    return observed_at, marca_real, marcas, limite_real


def _coincide_con_ultimas(
    filas: tuple[FilaMeliAds, ...],
    ultimas: dict[tuple[str, dt.date], tuple],
) -> bool:
    """Compatibilidad: snapshot igual, en contenido completo, a la vigente."""
    if len(filas) != len(ultimas):
        return False
    for fila in filas:
        if ultimas.get((fila.campaign_external_id, fila.metric_date)) != _contenido(fila):
            return False
    return True


def _decision_temporal(
    aceptadas: tuple[_Aceptacion, ...],
    filas: tuple[FilaMeliAds, ...],
    ultimas: dict[tuple[str, dt.date], tuple],
    legado_mismo_as_of: set[tuple[str, dt.date, tuple]],
    versiones_snapshot: set[tuple[str, dt.date, tuple]],
    marca_real: int,
    marca_futura: int,
    as_of_epoch: int,
    limite_real: int,
    huella: str,
) -> tuple[_Aceptacion | None, bool, int, int]:
    """Clasifica el snapshot y devuelve (reintento, es_futuro, real, futuro).

    El reintento exacto se busca contra la aceptacion ANTERIOR del snapshot
    completo (as-of + huella), no contra `v_metric_latest`. Si hay aceptacion
    con el mismo as-of pero otra huella, el snapshot se rechaza. Un snapshot
    nunca aceptado que cae detras del reloj que le corresponde se rechaza.
    Una aceptacion vieja sin `snap=` se reconoce solo si el snapshot coincide
    con TODA la observacion vigente; si hubo una correccion intermedia, se
    rechaza fail-closed. Una observacion legada con el mismo as-of y otra
    version tambien rechaza: el contenido distinto no puede pisar el piso.
    """
    mismo_as_of = [aceptada for aceptada in aceptadas if aceptada.as_of == as_of_epoch]
    previa = next((a for a in mismo_as_of if a.huella == huella), None)
    if previa is not None:
        return previa, previa.es_futuro, marca_real, marca_futura
    if legado_mismo_as_of and legado_mismo_as_of != versiones_snapshot:
        raise MeliAdsError(
            "snapshot con el mismo as_of y contenido distinto (snapshot atrasado):"
            " no se escribio nada"
        )
    if mismo_as_of:
        if (
            all(a.huella is None for a in mismo_as_of)
            and _coincide_con_ultimas(filas, ultimas)
            and len({a.es_futuro for a in mismo_as_of}) == 1
        ):
            return mismo_as_of[0], mismo_as_of[0].es_futuro, marca_real, marca_futura
        raise MeliAdsError(
            "snapshot con el mismo as_of y contenido distinto (snapshot atrasado):"
            " no se escribio nada"
        )

    es_futuro = as_of_epoch > limite_real
    if es_futuro:
        # La cuarentena ordena solo a otros futuros; el reloj real manda si el
        # as-of ya quedo detras de una correccion real.
        if as_of_epoch <= max(marca_real, marca_futura):
            raise MeliAdsError(
                "snapshot atrasado: su as_of no es posterior a la marca aceptada"
                f" ({max(marca_real, marca_futura)}); no se escribio nada"
            )
        return None, True, marca_real, as_of_epoch

    # Un futuro en cuarentena no bloquea una correccion real valida.
    if as_of_epoch <= marca_real:
        raise MeliAdsError(
            "snapshot atrasado: su as_of no es posterior a la marca aceptada"
            f" ({marca_real}); no se escribio nada"
        )
    return None, False, as_of_epoch, marca_futura


def _skip_reason(
    es_futuro: bool,
    as_of_epoch: int,
    huella: str,
    piso: int,
    skips: Counter,
) -> str:
    """Sella la marca del reloj, la huella y el piso real de un futuro."""
    nombre_marca = "futuro" if es_futuro else "as_of"
    partes = [f"{nombre_marca}={as_of_epoch}", f"snap={huella}"]
    if es_futuro and piso >= 0:
        partes.append(f"piso={piso}")
    motivo = _formato_skip_reason(skips)
    if motivo:
        partes.append(motivo)
    return "; ".join(partes)


def _skip_reason_reintento(huella: str, skips: Counter) -> str:
    """Sella un reintento SIN crear otra aceptacion temporal."""
    partes = [f"reintento={huella}"]
    motivo = _formato_skip_reason(skips)
    if motivo:
        partes.append(motivo)
    return "; ".join(partes)


def _sella_reintento(
    conn: psycopg.Connection,
    run_id: int,
    filas: tuple[FilaMeliAds, ...],
    huella: str,
) -> int:
    """Sella un reintento exacto: cero escrituras y cero aceptaciones nuevas."""
    duplicados = len(filas)
    skips = Counter({"misma version canonica (recopia o reintento)": duplicados})
    _sellar_run(
        conn,
        run_id,
        ok=True,
        rows_written=0,
        rows_skipped=duplicados,
        skip_reason=_skip_reason_reintento(huella, skips),
    )
    return duplicados


def _fuente_report_id(fila: FilaMeliAds, as_of: dt.datetime) -> str:
    """Identidad del hecho + version + as-of del snapshot.

    El as-of forma parte del id (y por tanto del indice de dedupe) para poder
    ordenar snapshots sin columnas nuevas. Dos snapshots con la misma version
    y el mismo as-of comparten id: reingestar es no-op por schema.
    """
    epoch = int(as_of.timestamp())
    return (
        f"meli-ads|{fila.campaign_external_id}|{fila.metric_date.isoformat()}"
        f"|{fila.source_version}|{epoch}"
    )


def ingest_meli_ads(
    conn: psycopg.Connection,
    sqlite_path: Path | str,
) -> dict[str, int]:
    """Ingesta atomica de un snapshot de `meli_ads_campaigns_daily`.

    Abre la `ingest_run` ANTES de leer el snapshot: una tabla ausente, una
    SQLite corrupta o una fila invalida dejan corrida `ok=false` cuando hay
    PostgreSQL. Lee y valida el snapshot completo y escribe entidades y
    observaciones en UNA transaccion. Un snapshot atrasado (o una fila
    invalida) se rechaza completo, sin escrituras parciales. En cualquier
    fallo: rollback y sello best-effort con el motivo escapado.
    """
    origen: OrigenMeliAds | None = None
    rechazo_total = False

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    entidades_nuevas = 0
    entidades_actualizadas = 0
    insertadas = 0
    duplicados_version = 0
    duplicados_id = 0
    try:
        origen = leer_origen(sqlite_path)
        filas = origen.filas
        rechazos = origen.rechazos
        if rechazos:
            rechazo_total = True
            raise MeliAdsError(
                "snapshot rechazado (cero escrituras): "
                + (", ".join(f"{n}x {motivo}" for motivo, n in sorted(rechazos.items())))
            )
        if filas and origen.as_of is None:
            rechazo_total = True
            raise MeliAdsError(
                "snapshot rechazado (cero escrituras): sin as_of util"
                " (updated_at ausente o ilegible)"
            )
        if not filas:
            # Un snapshot vacio es un no-op exitoso: no crea aceptacion
            # temporal, no toca el catalogo y no inventa un as-of.
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=True,
                    rows_written=0,
                    rows_skipped=0,
                    skip_reason="snapshot vacio (no-op)",
                )
            return {
                "run_id": run_id,
                "filas_origen": 0,
                "rows_written": 0,
                "rows_skipped": 0,
                "duplicados": 0,
                "duplicados_version": 0,
                "duplicados_id": 0,
                "rechazos": 0,
                "entidades_nuevas": 0,
                "entidades_actualizadas": 0,
            }
        huella = _huella_snapshot(filas)
        with conn.transaction():
            conn.execute(_SQL_LOCK_MELI, _LOCK_MELI)
            # DESPUES del candado: el reloj de ingesta no puede quedar antes
            # que el de una corrida que termino mientras esta esperaba, o
            # v_metric_latest elegiria la observacion equivocada.
            ultimas: dict[tuple[str, dt.date], tuple] = {}
            for external_id, metric_date, report_id, *valores in conn.execute(_SQL_ULTIMAS):
                partes = _version_y_as_of(report_id)
                if partes is None:
                    raise MeliAdsError(
                        "observacion vigente sin as_of legible en su source_report_id:"
                        f" {external_id} {metric_date.isoformat()}"
                    )
                ultimas[(external_id, metric_date)] = _contenido_desde(*valores)

            as_of_epoch = int(origen.as_of.timestamp()) if origen.as_of is not None else -1
            observed_at, marca_real, marcas, limite_real = _estado_temporal(
                conn, ultimas, as_of_epoch
            )
            versiones = _versiones_snapshot(filas)
            legado_mismo_as_of = _observaciones_en_as_of(conn, as_of_epoch)
            # Un snapshot futuro que solo repite observaciones legadas ya
            # escritas con ese as-of es un no-op: si sellara `futuro=` sin
            # `piso=`, borraria el piso que esas observaciones ya eran. No crea
            # aceptacion nueva. Un futuro sin observaciones previas sigue el
            # camino normal y queda en cuarentena.
            duplicado_futuro = (
                not any(a.as_of == as_of_epoch for a in marcas.aceptaciones)
                and as_of_epoch > limite_real
                and bool(legado_mismo_as_of)
                and legado_mismo_as_of == versiones
            )
            try:
                previa, es_futuro, real_final, _futuro_final = _decision_temporal(
                    marcas.aceptaciones,
                    filas,
                    ultimas,
                    legado_mismo_as_of,
                    versiones,
                    marca_real,
                    marcas.futuro,
                    as_of_epoch,
                    limite_real,
                    huella,
                )
            except MeliAdsError:
                rechazo_total = True
                raise

            if duplicado_futuro:
                duplicados_version = _sella_reintento(conn, run_id, filas, huella)
            elif previa is not None:
                # Reintento exacto: no se toca el catalogo ni la historia y no
                # se agrega otra aceptacion. La clasificacion original sigue
                # viva en la corrida que acepto el snapshot.
                duplicados_version = _sella_reintento(conn, run_id, filas, huella)
            else:
                cache: dict[str, tuple[int, bool, bool]] = {}
                nombres = _nombres_vigentes(list(filas))
                for fila in filas:
                    entidad = cache.get(fila.campaign_external_id)
                    if entidad is None:
                        entidad = _entidad(
                            conn,
                            fila.campaign_external_id,
                            nombres.get(fila.campaign_external_id),
                        )
                        cache[fila.campaign_external_id] = entidad
                        entidades_nuevas += int(entidad[1])
                        entidades_actualizadas += int(entidad[2])
                    vigente = ultimas.get((fila.campaign_external_id, fila.metric_date))
                    if vigente is not None and vigente == _contenido(fila):
                        # Mismo hecho canonico (recopia con otro as-of): no es
                        # una correccion. El catalogo ya se actualizo arriba.
                        duplicados_version += 1
                        continue
                    cursor = conn.execute(
                        _SQL_INSERTAR_OBS,
                        (
                            entidad[0],
                            fila.metric_date,
                            observed_at,
                            fila.metric_currency,
                            fila.cost,
                            fila.ad_revenue,
                            fila.impressions,
                            fila.clicks,
                            fila.orders,
                            _fuente_report_id(fila, origen.as_of),
                            run_id,
                        ),
                    )
                    if cursor.rowcount:
                        insertadas += 1
                    else:
                        duplicados_id += 1

                skips = Counter()
                if duplicados_version:
                    skips["misma version canonica (recopia o reintento)"] = duplicados_version
                if duplicados_id:
                    skips["source_report_id ya ingerido"] = duplicados_id
                # Un futuro conserva el piso real heredado; un real no necesita
                # piso porque su propia marca `as_of=` es el piso.
                piso = real_final if es_futuro else -1
                _sellar_run(
                    conn,
                    run_id,
                    ok=True,
                    rows_written=insertadas,
                    rows_skipped=duplicados_version + duplicados_id,
                    skip_reason=_skip_reason(es_futuro, as_of_epoch, huella, piso, skips),
                )
    except BaseException as exc:
        # El with de arriba ya hizo rollback del trabajo. La run se sella como
        # fallo si la conexion sigue viva (best-effort). BaseException: un
        # Ctrl-C tambien deja la corrida sellada, no abierta.
        motivo = _texto_persistible(scrub(str(exc)) or type(exc).__name__)
        rows_skipped = origen.total if (origen is not None and rechazo_total) else 0
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_written=0,
                    rows_skipped=rows_skipped,
                    skip_reason=motivo,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo;"
                " el error original de la corrida era: %s",
                run_id,
                motivo,
            )
        raise

    duplicados = duplicados_version + duplicados_id
    return {
        "run_id": run_id,
        "filas_origen": origen.total if origen is not None else 0,
        "rows_written": insertadas,
        "rows_skipped": duplicados,
        "duplicados": duplicados,
        "duplicados_version": duplicados_version,
        "duplicados_id": duplicados_id,
        "rechazos": sum(origen.rechazos.values()) if origen is not None else 0,
        "entidades_nuevas": entidades_nuevas,
        "entidades_actualizadas": entidades_actualizadas,
    }


# ---------------------------------------------------------------------------
# main del pipeline (el CLI despacha aqui; cero logica de argparse en el CLI)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest meli-ads",
        description=(
            "Ingesta campana/dia de Mercado Libre Ads desde un snapshot"
            " read-only de la SQLite de contabilidad (runbook: docs/DEPLOY.md)."
        ),
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="ruta del snapshot (producido con la API .backup(); ver runbook)",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir MeLi Ads (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(
            f"el snapshot no existe: {ruta} (runbook: docs/DEPLOY.md, ingesta de MeLi Ads)",
            file=sys.stderr,
        )
        return 2

    try:
        conn = connect(dsn)
        try:
            resultado = ingest_meli_ads(conn, ruta)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "ingesta de MeLi Ads fallo (la ingest_run quedo sellada ok=false"
            f" cuando fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1

    print(f"== Ingesta de MeLi Ads desde contabilidad ({SOURCE}) ==")
    print(
        f"run_id={resultado['run_id']} ok=True"
        f" rows_written={resultado['rows_written']}"
        f" rows_skipped={resultado['rows_skipped']}"
        f" filas_origen={resultado['filas_origen']}"
    )
    print(
        f"entidades: nuevas={resultado['entidades_nuevas']}"
        f" actualizadas={resultado['entidades_actualizadas']}"
        f" duplicados={resultado['duplicados']}"
        f" rechazos={resultado['rechazos']}"
    )
    return 0
