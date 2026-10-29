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

- Cada corrida exitosa se sella como ACEPTACION de un snapshot completo. Su
  `skip_reason` guarda el as-of y la huella SHA-256 de TODAS las filas
  canonicas (campana, fecha y version), no solo de la observacion vigente.
  `snap=<huella>` es lo que permite reconocer un reintento exacto aunque otra
  corrida haya cambiado despues la observacion vigente.
- Hay DOS relojes independientes: `as_of=<epoch>` (snapshots reales) y
  `futuro=<epoch>` (snapshots aceptados como futuros al momento de aceptarse).
  Un as-of aceptado como futuro queda en CUARENTENA para siempre: el avance
  del reloj no lo reclasifica como real. Cada corrida escribe solo la marca
  del reloj al que pertenece; la otra sigue viva en su propia corrida. Por eso
  una primera corrida exclusivamente futura no escribe `as_of=`.
- Un reintento exacto (mismo as-of y misma huella) es un NO-OP completo: no
  escribe entidades, no toca nombres y no agrega observaciones. Se reconoce
  contra la aceptacion anterior, nunca comparando solo `v_metric_latest`.
- Un snapshot con el mismo as-of y contenido distinto se rechaza completo.
- Un snapshot atrasado que nunca fue aceptado se rechaza completo, aunque sus
  metricas coincidan. Una marca futura NO bloquea una correccion real valida:
  los snapshots reales se comparan solo contra el reloj real.
- Una recopia real deduplicada conserva su nuevo as-of, avanza el reloj real
  y bloquea snapshots intermedios.

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
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
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
       e.external_id, o.metric_date, o.source_report_id
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

# Aceptaciones de TODOS los snapshots exitosos. Viven en el skip_reason porque
# el esquema no tiene columna de watermark y esta fase no crea migraciones. El
# id de ingest_run no sirve como orden: los runs se abren antes del candado y
# pueden terminar en otro orden. Se toma el maximo de cada reloj, no la ultima
# corrida por id.
_SQL_MARCA = (
    "SELECT skip_reason FROM ingest_run"
    " WHERE source = %s AND ok = true"
    " AND (skip_reason LIKE 'as_of=%%' OR skip_reason LIKE 'futuro=%%')"
)
_MARCA_RE = re.compile(r"^as_of=(\d+)(?:;|$)")
_FUTURO_RE = re.compile(r"(?:^|; )futuro=(\d+)")
_HUELLA_RE = re.compile(r"(?:^|; )snap=([0-9a-f]{64})(?:;|$)")

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


def _texto_persistible(texto: str | None) -> str | None:
    """Escapa NUL y controles antes de persistir o registrar un motivo.

    PostgreSQL rechaza NUL en columnas de texto; si el sello de la corrida
    falla con el mismo error, la corrida queda abierta. El escape es central:
    no depende de que cada validacion limpie su propio mensaje.
    """
    if texto is None:
        return None
    return "".join(
        f"\\x{ord(caracter):02x}" if ord(caracter) < 0x20 or ord(caracter) == 0x7F else caracter
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
    """Huella del snapshot COMPLETO: campana, fecha y version de cada fila.

    No incluye el nombre: renombrar no cambia el hecho monetario. Incluye el
    conteo para que agregar o quitar filas cambie la huella. Dos snapshots con
    la misma huella y el mismo as-of son el mismo snapshot.
    """
    resumen = hashlib.sha256()
    resumen.update(f"{len(filas)}\n".encode())
    for fila in sorted(filas, key=lambda f: (f.metric_date, f.campaign_external_id)):
        resumen.update(
            (
                f"{fila.metric_date.isoformat()}\x1f{fila.campaign_external_id}"
                f"\x1f{fila.source_version}\n"
            ).encode()
        )
    return resumen.hexdigest()


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


def _aceptaciones(conn: psycopg.Connection) -> list[_Aceptacion]:
    """Todas las aceptaciones registradas, con su huella de snapshot."""
    aceptadas: list[_Aceptacion] = []
    for (skip_reason,) in conn.execute(_SQL_MARCA, (SOURCE,)).fetchall():
        huella = _huella_de_marca(skip_reason)
        if (real := _marca_de_as_of(skip_reason)) is not None:
            aceptadas.append(_Aceptacion(real, False, huella))
        if (futuro := _futuro_de_marca(skip_reason)) is not None:
            aceptadas.append(_Aceptacion(futuro, True, huella))
    return aceptadas


def _marcas_corridas(conn: psycopg.Connection) -> tuple[int, int]:
    """Maximos as-of REAL y FUTURO de todas las corridas exitosas.

    Son dos relojes separados. Un as-of que fue futuro al aceptarse queda en
    cuarentena y no bloquea correcciones reales aunque el reloj se acerque.
    """
    aceptadas = _aceptaciones(conn)
    reales = [a.as_of for a in aceptadas if not a.es_futuro]
    futuras = [a.as_of for a in aceptadas if a.es_futuro]
    return max(reales, default=-1), max(futuras, default=-1)


def _estado_temporal(
    conn: psycopg.Connection,
    ultimas: dict[tuple[str, dt.date], tuple[str, int]],
) -> tuple[dt.datetime, int, int, int]:
    """Reloj logico de observacion y marcas real/futura bajo el candado."""
    ahora_reloj = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    max_observado = conn.execute(_SQL_MAX_OBSERVADO).fetchone()[0]
    observed_at = (
        max(ahora_reloj, max_observado + dt.timedelta(microseconds=1))
        if max_observado is not None
        else ahora_reloj
    )
    marca_real_corrida, marca_futura = _marcas_corridas(conn)
    obs_max = max((partes[1] for partes in ultimas.values()), default=-1)
    # Sin marcas (base legada): las observaciones existentes son el unico
    # orden disponible. Una marca futura NO se convierte en real.
    marca_real = obs_max if marca_real_corrida < 0 and marca_futura < 0 else marca_real_corrida
    limite_real = int(ahora_reloj.timestamp()) + int(_SKEW_RELOJ.total_seconds())
    return observed_at, marca_real, marca_futura, limite_real


def _decision_temporal(
    aceptadas: list[_Aceptacion],
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
    """
    mismo_as_of = [aceptada for aceptada in aceptadas if aceptada.as_of == as_of_epoch]
    previa = next((a for a in mismo_as_of if a.huella == huella), None)
    if previa is not None:
        return previa, previa.es_futuro, marca_real, marca_futura
    if mismo_as_of:
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
    skips: Counter,
) -> str:
    """Sella la marca del reloj que corresponde y la huella del snapshot."""
    nombre_marca = "futuro" if es_futuro else "as_of"
    partes = [f"{nombre_marca}={as_of_epoch}", f"snap={huella}"]
    motivo = _formato_skip_reason(skips)
    if motivo:
        partes.append(motivo)
    return "; ".join(partes)


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
        huella = _huella_snapshot(filas)
        with conn.transaction():
            conn.execute(_SQL_LOCK_MELI, _LOCK_MELI)
            # DESPUES del candado: el reloj de ingesta no puede quedar antes
            # que el de una corrida que termino mientras esta esperaba, o
            # v_metric_latest elegiria la observacion equivocada.
            ultimas: dict[tuple[str, dt.date], tuple[str, int]] = {}
            for external_id, metric_date, report_id in conn.execute(_SQL_ULTIMAS):
                partes = _version_y_as_of(report_id)
                if partes is None:
                    raise MeliAdsError(
                        "observacion vigente sin as_of legible en su source_report_id:"
                        f" {external_id} {metric_date.isoformat()}"
                    )
                ultimas[(external_id, metric_date)] = partes

            observed_at, marca_real, marca_futura, limite_real = _estado_temporal(conn, ultimas)
            as_of_epoch = int(origen.as_of.timestamp()) if origen.as_of is not None else -1
            try:
                previa, es_futuro, real_final, futuro_final = _decision_temporal(
                    _aceptaciones(conn),
                    marca_real,
                    marca_futura,
                    as_of_epoch,
                    limite_real,
                    huella,
                )
            except MeliAdsError:
                rechazo_total = True
                raise

            if previa is not None:
                # Reintento exacto: no se toca el catalogo ni la historia. La
                # marca conserva la clasificacion original (un futuro sigue
                # siendo futuro aunque el reloj ya haya avanzado).
                duplicados_version = len(filas)
                skips = Counter(
                    {"misma version canonica (recopia o reintento)": duplicados_version}
                )
                _sellar_run(
                    conn,
                    run_id,
                    ok=True,
                    rows_written=0,
                    rows_skipped=duplicados_version,
                    skip_reason=_skip_reason(previa.es_futuro, previa.as_of, huella, skips),
                )
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
                    if vigente is not None and vigente[0] == fila.source_version:
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
                _sellar_run(
                    conn,
                    run_id,
                    ok=True,
                    rows_written=insertadas,
                    rows_skipped=duplicados_version + duplicados_id,
                    skip_reason=_skip_reason(es_futuro, as_of_epoch, huella, skips),
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
