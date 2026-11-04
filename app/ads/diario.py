"""Corrida diaria de ingesta Ads (ingesta-01 A.2).

`python -m app.cli ingest diario` (y `python -m app.ads.descarga`, que
delega aqui) corre los tres grupos --metricas, productos y placements-- en
UNA sola descarga (app.ads.descarga, tres ingest_run) sobre el rango por
defecto D-31..D-1 y avisa por Telegram lo que falle o se salve: las metricas
de campanas ya avisan por salud, asi que de aqui salen solo los mensajes de
productos y ubicaciones. Los reintentos del dia son corridas del mismo
comando programadas por el crontab (docs/DEPLOY.md): --solo-faltantes salta
un grupo solo si hoy ya tiene una ingest_run ok=true que escribio D-1 para
cada plataforma con perfil aceptado; un grupo saltado no abre ingest_run.

La tabla de horarios del crontab sale de HORARIOS_INGESTA: los textos de los
avisos nombran el reintento siguiente (o el "mañana a las 07:10" cuando el
intento es el ultimo del dia) y la prueba del crontab
(tests/test_ingesta_cron.py) fija que las lineas instaladas coincidan con
esta constante. Tope de pared de la corrida: TOPE_PARED_SEGUNDOS (2 h 45
min; el sondeo acota 2 h 30 min y el resto es holgura de escritura/avisos).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from collections.abc import Callable

import psycopg

from app.ads.descarga import GrupoDescarga, ResultadoGrupo, _registrar, descarga
from app.ads.salud import intentar_procesar_run
from app.ads.structure import _SQL_ABRIR_RUN, _sellar_run
from app.metricas_listas import (
    SOURCE_METRICAS,
    SOURCE_PLACEMENTS,
    SOURCE_PRODUCTOS,
    metricas_de_ayer_listas,
    placements_de_ayer_listos,
    productos_de_ayer_listos,
)
from app.redaction import scrub

logger = logging.getLogger(__name__)

# Telegram lo que falle o se salve. La tabla de horarios de docs/DEPLOY.md
# sale de HORARIOS_INGESTA: los reintentos del dia son corridas del mismo
# comando (--solo-faltantes salta lo ya al dia).
# ---------------------------------------------------------------------------

# Horarios UTC de la ingesta diaria (y sus dos reintentos). La prueba del
# crontab (tests/test_ingesta_cron.py) fija que las lineas instaladas
# coincidan con esta constante.
HORARIOS_INGESTA = ("07:10", "10:40", "13:15")

# Tope de pared de la corrida: el sondeo acota 2 h 30 min y el resto de la
# corrida (escrituras + avisos) tiene 15 min de holgura.
TOPE_PARED_SEGUNDOS = 9900.0  # 2 h 45 min

QUE_POR_CLAVE = {
    "productos": "las métricas por producto",
    "placements": "las métricas por ubicación",
}
PAIS_POR_PLATAFORMA = {"amazon_mx": "MX", "amazon_us": "US"}

# La guarda "al dia" vive en app.metricas_listas (un numero, una fuente):
# cada source consulta su tabla con la ligazon real que su esquema trae.
_AL_DIA_POR_SOURCE = {
    SOURCE_METRICAS: metricas_de_ayer_listas,
    SOURCE_PRODUCTOS: productos_de_ayer_listos,
    SOURCE_PLACEMENTS: placements_de_ayer_listos,
}

# Tablas de observaciones que ligan la run (para los paises rescatados).
# ads_placement_observation no liga: ver _SQL_PAISES_RESCATADOS_PLACEMENTS.
_TABLA_POR_SOURCE = {
    SOURCE_METRICAS: "ads_metric_observation",
    SOURCE_PRODUCTOS: "ads_product_metric_observation",
}

_SQL_PLATAFORMAS_DEL_SOURCE = """
SELECT DISTINCT platform FROM ads_report_result
 WHERE platform IS NOT NULL
   AND ingest_run_id IN (SELECT id FROM ingest_run WHERE source = %s)
"""

_SQL_FALLO_HOY = """
SELECT 1 FROM ingest_run
 WHERE source = %s AND ok = false
   AND (started_at AT TIME ZONE 'UTC')::date = %s
 LIMIT 1
"""

# {tabla} se sustituye SOLO con las claves de _TABLA_POR_SOURCE (constantes).
# Las observaciones de metricas/products ligan la run (ingest_run_id) y
# traen metric_date: el rescate exige D-1 escrito por ESTA run.
_SQL_PAISES_RESCATADOS = """
SELECT DISTINCT platform FROM {tabla} WHERE ingest_run_id = %s AND metric_date = %s
"""

# ads_placement_observation (0062) no liga run: el rescate de placements se
# acredita con lo que ESTA run dejo written en ads_report_result.
_SQL_PAISES_RESCATADOS_PLACEMENTS = """
SELECT DISTINCT platform FROM ads_report_result
 WHERE ingest_run_id = %s AND status = 'written' AND platform IS NOT NULL
"""


def _horario_actual(ahora: dt.time) -> str:
    """El horario de ingesta que corre ahora (el ultimo pasado; antes del
    primero, el primero: el cron dispara EN el horario)."""
    ahora_txt = ahora.strftime("%H:%M")
    pasados = [horario for horario in HORARIOS_INGESTA if horario <= ahora_txt]
    return pasados[-1] if pasados else HORARIOS_INGESTA[0]


def _siguiente_horario(horario: str) -> str | None:
    idx = HORARIOS_INGESTA.index(horario)
    return HORARIOS_INGESTA[idx + 1] if idx + 1 < len(HORARIOS_INGESTA) else None


def _texto_plazo(que: str, pais: str, reintento: str) -> str:
    return (
        f"Amazon no entregó a tiempo {que} de {pais} (esperamos 2 h 30 min). "
        f"Orbit lo vuelve a intentar a las {reintento} UTC; mientras, "
        "el ciclo de pujas espera."
    )


def _texto_error(que: str, pais: str, reintento: str) -> str:
    return (
        f"Amazon respondió con error al pedir {que} de {pais}. "
        f"Orbit lo vuelve a intentar a las {reintento} UTC; mientras, "
        "el ciclo de pujas espera."
    )


def _texto_salvado(que: str, pais: str, horario: str) -> str:
    return f"Ya llegaron {que} de {pais} en el intento de las {horario} UTC."


def _texto_agotado(que: str, pais: str) -> str:
    return (
        f"Hoy no llegaron {que} de {pais} después de tres intentos. "
        f"Mañana a las {HORARIOS_INGESTA[0]} se intenta otra vez."
    )


def _grupos_diario() -> list[GrupoDescarga]:
    from app.ads.placements import PLACEMENTS_CFG, SOURCE_PLACEMENTS
    from app.ads.reports import PRODUCTOS_CFG, REPORTES_CFG, SOURCE, SOURCE_PRODUCTOS

    return [
        GrupoDescarga(clave="metricas", source=SOURCE, cfgs=tuple(REPORTES_CFG)),
        GrupoDescarga(clave="productos", source=SOURCE_PRODUCTOS, cfgs=(PRODUCTOS_CFG,)),
        GrupoDescarga(clave="placements", source=SOURCE_PLACEMENTS, cfgs=(PLACEMENTS_CFG,)),
    ]


def _escritor_diario(conn, run_id, grupo, items, *, fecha_ini, fecha_fin):
    """Despacha la fase DB por source: placements tiene su escritor propio;
    metricas y productos comparten el de reports (dispatch por cfg['tabla'])."""
    if grupo.source == "amazon_ads_placements_v3":
        from app.ads.placements import escribir_grupo
    else:
        from app.ads.reports import escribir_grupo
    return escribir_grupo(conn, run_id, grupo, items, fecha_ini=fecha_ini, fecha_fin=fecha_fin)


def _plataformas_del_source(conn: psycopg.Connection, source: str) -> list[str]:
    """Las plataformas con perfil aceptado que este source escribe: las que
    alguna vez dejo escritas en ads_report_result (la fuente del gate)."""
    return [fila[0] for fila in conn.execute(_SQL_PLATAFORMAS_DEL_SOURCE, (source,)).fetchall()]


def _grupo_al_dia(conn: psycopg.Connection, grupo: GrupoDescarga, *, hoy: dt.date) -> bool:
    """True si hoy ya hay una run ok=true del source que escribio D-1 para
    CADA plataforma del source. Un backfill viejo iniciado hoy no cuenta: el
    filtro es metric_date = D-1. La forma de ligar run y observaciones la
    fija app.metricas_listas (cada tabla trae lo que trae: ads_metric_observation
    cruza ad_entity; placements no liga run y es doble existe)."""
    plataformas = _plataformas_del_source(conn, grupo.source)
    if not plataformas:
        return False
    al_dia = _AL_DIA_POR_SOURCE[grupo.source]
    return all(al_dia(conn, plataforma, hoy=hoy) for plataforma in plataformas)


def _fallo_anterior_hoy(conn: psycopg.Connection, source: str, *, hoy: dt.date) -> bool:
    return conn.execute(_SQL_FALLO_HOY, (source, hoy)).fetchone() is not None


def _paises_rescatados(conn: psycopg.Connection, res: ResultadoGrupo, *, hoy: dt.date) -> list[str]:
    """Plataformas que ESTA run (ok) rescribio en D-1: el salvamento es real
    solo si los datos llegaron. placements no liga run en su tabla de
    observaciones: se acredita con lo que la run dejo written en
    ads_report_result."""
    ayer = hoy - dt.timedelta(days=1)
    if res.source == SOURCE_PLACEMENTS:
        filas = conn.execute(_SQL_PAISES_RESCATADOS_PLACEMENTS, (res.run_id,)).fetchall()
    else:
        filas = conn.execute(
            _SQL_PAISES_RESCATADOS.format(tabla=_TABLA_POR_SOURCE[res.source]),
            (res.run_id, ayer),
        ).fetchall()
    return sorted({PAIS_POR_PLATAFORMA[fila[0]] for fila in filas})


def _paises_de_fallo(res: ResultadoGrupo) -> list[str]:
    """Unidades que no completaron, mapeadas a pais; un fallo global (sin
    plataforma conocida, p.ej. evaluar_perfiles) afecta a las dos."""
    if not res.fallos or any(fallo[0] is None for fallo in res.fallos):
        return ["MX", "US"]
    return sorted({PAIS_POR_PLATAFORMA[fallo[0]] for fallo in res.fallos})


def _envia_mensajes(
    conn: psycopg.Connection,
    resultados: dict[str, ResultadoGrupo],
    *,
    hoy: dt.date,
    ahora: dt.datetime,
    enviar: Callable[[str], bool],
) -> None:
    """Un mensaje por grupo de productos/ubicaciones y pais que fallo o se
    salvo. Las metricas de campanas avisan por salud: aqui no."""
    horario = _horario_actual(ahora.time())
    siguiente = _siguiente_horario(horario)
    for clave in ("productos", "placements"):
        res = resultados.get(clave)
        if res is None:
            continue  # grupo saltado por --solo-faltantes
        que = QUE_POR_CLAVE[clave]
        if res.ok:
            if _fallo_anterior_hoy(conn, res.source, hoy=hoy):
                for pais in _paises_rescatados(conn, res, hoy=hoy):
                    enviar(_texto_salvado(que, pais, horario))
            continue
        for pais in _paises_de_fallo(res):
            if siguiente is None:
                enviar(_texto_agotado(que, pais))
            elif res.motivo == "plazo":
                enviar(_texto_plazo(que, pais, siguiente))
            else:
                enviar(_texto_error(que, pais, siguiente))


def _sella_fallo_de_credenciales(conn: psycopg.Connection, grupos, exc: BaseException) -> None:
    """Sin cliente no hay corrida, pero la ausencia queda auditada: una run
    global_failed por grupo (misma disciplina que reports.main)."""
    motivo = scrub(str(exc)) or type(exc).__name__
    for grupo in grupos:
        with conn.transaction():
            run_id = conn.execute(_SQL_ABRIR_RUN, (grupo.source,)).fetchone()[0]
            _registrar(conn, run_id, None, None, None, "global_failed", motivo)
            _sellar_run(conn, run_id, ok=False, rows_skipped=0, skip_reason=motivo)
        intentar_procesar_run(conn, run_id)


def main(
    argv: list[str] | None = None,
    *,
    conn: psycopg.Connection | None = None,
    client=None,
    reloj: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    enviar: Callable[[str], bool] | None = None,
    ahora: dt.datetime | None = None,
    hoy: dt.date | None = None,
) -> int:
    """`python -m app.cli ingest diario` (y `python -m app.ads.descarga`):
    UNA descarga para los tres grupos (tres ingest_run), rango por defecto
    D-31..D-1. --solo-faltantes salta un grupo solo si hoy ya tiene una run
    ok=true que escribio D-1 para cada plataforma con perfil aceptado; un
    grupo saltado no abre ingest_run. Al terminar avisa (si el canal de
    notifica esta activo) por grupo de productos/ubicaciones y pais. Sale 1
    si algun grupo fallo, 0 si todo salio (o no habia nada que correr),
    2 sin DSN o con rango invalido."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest diario",
        description=(
            "Corrida diaria de ingesta Ads: metricas + productos + placements "
            "en una sola descarga (tres ingest_run)"
        ),
    )
    parser.add_argument("--fecha", help="YYYY-MM-DD del dia inicial (default: D-31)")
    parser.add_argument(
        "--fecha-fin", help="YYYY-MM-DD del dia final (default: D-1; requiere --fecha)"
    )
    parser.add_argument(
        "--solo-faltantes",
        action="store_true",
        help="salta los grupos que hoy ya escribieron D-1 para todas sus plataformas",
    )
    args = parser.parse_args(argv)

    ahora = ahora or dt.datetime.now(dt.UTC)
    hoy = hoy or ahora.date()
    if args.fecha_fin and not args.fecha:
        print("--fecha-fin requiere --fecha", file=sys.stderr)
        return 2

    def _parsear(texto: str | None, etiqueta: str) -> dt.date | None:
        if texto is None:
            return None
        try:
            return dt.date.fromisoformat(texto)
        except ValueError:
            print(f"{etiqueta} no es una fecha YYYY-MM-DD: {texto!r}", file=sys.stderr)
            return None

    fecha_ini = _parsear(args.fecha, "--fecha")
    fecha_fin = _parsear(args.fecha_fin, "--fecha-fin")
    if args.fecha and fecha_ini is None or args.fecha_fin and fecha_fin is None:
        return 2
    if fecha_ini is None:
        fecha_ini = hoy - dt.timedelta(days=31)
        fecha_fin = hoy - dt.timedelta(days=1)
    elif fecha_fin is None:
        fecha_fin = fecha_ini
    from app.ads.reports import _validar_rango

    try:
        _validar_rango(fecha_ini, fecha_fin)
    except ValueError as exc:
        print(f"rango invalido: {exc}", file=sys.stderr)
        return 2

    cierra_conn = False
    if conn is None:
        dsn = os.environ.get("ORBIT_DSN_INGEST")
        if not dsn:
            print(
                "ORBIT_DSN_INGEST no esta definido: no se puede correr el diario (fail-closed)",
                file=sys.stderr,
            )
            return 2
        from app.db import connect

        conn = connect(dsn)
        cierra_conn = True

    grupos = _grupos_diario()
    try:
        if client is None:
            from app.ads.client import AdsClient
            from app.ads.config import AdsCredentials

            try:
                client = AdsClient(AdsCredentials.from_secrets_dir())
            except Exception as exc:
                _sella_fallo_de_credenciales(conn, grupos, exc)
                print(
                    f"ingest diario: credenciales de Amazon Ads no disponibles: {scrub(str(exc))}",
                    file=sys.stderr,
                )
                return 1
        if args.solo_faltantes:
            # el grupo saltado no abre ingest_run: se filtra ANTES de descargar
            grupos = [grupo for grupo in grupos if not _grupo_al_dia(conn, grupo, hoy=hoy)]
            # cerrar la transaccion implicita del SELECT: si no, los
            # `transaction()` de la descarga serian savepoints y el close()
            # final haria ROLLBACK de todo
            conn.commit()
        if not grupos:
            return 0

        t0 = reloj()
        try:
            resultados = descarga(
                conn,
                client,
                grupos,
                (fecha_ini, fecha_fin),
                reloj=reloj,
                sleep=sleep,
                al_completar=_escritor_diario,
            )
        except Exception as exc:
            # evaluar_perfiles fallo: descarga sello las runs global_failed
            # y re-lanzo; aqui se convierte en fallo de los tres grupos.
            logger.warning("ingest diario: la descarga fallo en fase de API: %s", scrub(str(exc)))
            resultados = {
                grupo.clave: ResultadoGrupo(
                    clave=grupo.clave,
                    run_id=0,
                    source=grupo.source,
                    ok=False,
                    motivo="error",
                    skip_reason=scrub(str(exc)),
                    exc=exc,
                    fallos=[(None, None, None)],
                )
                for grupo in grupos
            }
        if enviar is None:
            from app import notifica

            if notifica.canal_activo():
                enviar = notifica.envia_aviso
        if enviar is not None:
            try:
                _envia_mensajes(conn, resultados, hoy=hoy, ahora=ahora, enviar=enviar)
            finally:
                conn.commit()  # las lecturas de los mensajes no dejan nada abierto
        if reloj() - t0 > TOPE_PARED_SEGUNDOS:
            logger.warning(
                "ingest diario excedio su tope de pared de 2 h 45 min (tardo %.0f s)",
                reloj() - t0,
            )
        return 1 if any(not res.ok for res in resultados.values()) else 0
    finally:
        if cierra_conn:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
