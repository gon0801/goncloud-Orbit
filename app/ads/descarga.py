"""Descarga paralela de reportes de Amazon Ads (ingesta-01 A.1).

Un solo `descarga` pide TODOS los reportes de TODOS los grupos (metricas,
productos, placements) y perfiles a la vez -- UN solo `evaluar_perfiles` -- y
los sondea EN UNA SOLA VUELTA: 5 s los primeros 2 min, 30 s despues, con
plazo de sondeo de 2 h 30 min. Cada reporte COMPLETED se descarga en ese
momento; cuando un grupo tiene todos los suyos, `al_completar` lo escribe y
sella su `ingest_run` en SU transaccion (puede cerrarse a los 20 min aunque
otro grupo siga sondeando).

Repetidos (ingesta-01): un reporte se repite con un POST nuevo cuyo `name`
lleva sufijo `-r1`/`-r2` (maximo 2 repetidos) cuando sigue PENDING/
PROCESSING 40 min despues de su pedido, cuando queda FAILED o cuando el POST
falla por 5xx, red o 429 agotado. El original se sigue sondeando: gana el
primero en COMPLETED. Un 425 ("The Request is a duplicate of : <id>", forma
documentada) adopta el reportId que anuncia el cuerpo de la respuesta. Un GET
que falla deja el reporte pendiente para la siguiente vuelta. Un 4xx de
forma en el POST (que no sea 425) NO se repite: falla solo su grupo.

Frente al diseno secuencial anterior (sondeo reporte por reporte, ahora
borrado): un reporte lento o FALLIDO ya no tumba la corrida entera ni obliga
a los demas a esperar; el presupuesto por reporte (25 min de productos) sobra
con el umbral de repeticion de 40 min + plazo global de 2 h 30 min.

Disciplina heredada de app.ads.reports: UNA `ingest_run` por grupo (source
propio), abierta antes de consultar perfiles; todo fallo se sella ok=false
con rastro (rejected/pending/downloaded/written/failed/global_failed en
ads_report_result); la fase de DB corre en UNA transaccion por grupo con el
"hoy" del reloj de la DB (UTC fijado) y el sello ok=true va en la misma;
`intentar_procesar_run` corre despues de CADA sello (salud nunca cambia el
resultado contable).

`reloj` y `sleep` se inyectan: los tests manejan un calendario falso (5 s/30 s
sin dormir). El modulo NO importa app.ads.reports a nivel de modulo: reports
y placements delegan aqui su fase de API (import de descarga a nivel modulo)
y descarga los usa por import diferido, para no armar un ciclo.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.ads.client import AdsApiError
from app.ads.salud import intentar_procesar_run
from app.ads.structure import (
    _SQL_ABRIR_RUN,
    PerfilAds,
    _sellar_run,
    evaluar_perfiles,
)
from app.redaction import scrub

logger = logging.getLogger(__name__)

# Calendario de sondeo (ingesta-01 A.1): 5 s los primeros 2 min, 30 s despues,
# plazo total de sondeo 2 h 30 min. Un reporte se repite tras 40 min sin
# salir de PENDING/PROCESSING (o en cuanto queda FAILED), maximo 2 repetidos.
PLAZO_SONDEO_SEGUNDOS = 9000.0  # 2 h 30 min
RAPIDO_HASTA_SEGUNDOS = 120.0  # los primeros 2 min
ESPERA_RAPIDA_SEGUNDOS = 5.0
ESPERA_LENTA_SEGUNDOS = 30.0
REINTENTO_TRAS_SEGUNDOS = 2400.0  # 40 min
MAX_REPETIDOS = 2

# Forma documentada del 425 (reporte duplicado): el cuerpo anuncia el id.
_RE_DUPLICADO = re.compile(r"duplicate of\s*:\s*([A-Za-z0-9._-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class GrupoDescarga:
    """Un grupo de reportes = una `ingest_run` (source propio)."""

    clave: str  # identificador estable para el caller (p.ej. "metrics")
    source: str  # ingest_run.source
    cfgs: tuple[dict, ...]


@dataclass
class ItemDescargado:
    """Un reporte ganador: perfil x cfg con sus filas ya descargadas."""

    perfil: PerfilAds
    cfg: dict
    report_id: str
    filas: list[dict]


@dataclass
class ResultadoEscritura:
    """Lo que devuelve `al_completar`: outcome contable del grupo."""

    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    reportes: list[Any] = field(default_factory=list)


@dataclass
class ResultadoGrupo:
    """Outcome de UN grupo (espejo de su ingest_run sellada)."""

    clave: str
    run_id: int
    source: str
    ok: bool
    motivo: str  # "ok" | "plazo" | "error" | "cero_perfiles"
    rows_written: int = 0
    rows_skipped: int = 0
    skip_reason: str | None = None
    exc: BaseException | None = None
    perfiles_rechazados: list[PerfilAds] = field(default_factory=list)
    escritura: ResultadoEscritura | None = None
    # unidades que no completaron: (platform, cfg_nombre, profile_id); un
    # (None, None, None) marca fallo global del grupo (plataformas a cuenta
    # del caller: p.ej. un error de evaluar_perfiles afecta a todas).
    fallos: list[tuple[str | None, str | None, int | None]] = field(default_factory=list)


@dataclass
class _Intento:
    """Un POST emitido (original o repetido) con su presupuesto de 40 min."""

    report_id: str
    t_pedido: float
    terminal: bool = False  # FAILED: no se vuelve a sondear
    repetido: bool = False  # ya se emitio un repetido por este intento


@dataclass
class _Logico:
    """El reporte logico (grupo, perfil, cfg) y todos sus intentos."""

    grupo: GrupoDescarga
    perfil: PerfilAds
    cfg: dict
    fecha_ini: dt.date
    fecha_fin: dt.date
    pedidos: int = 0  # POSTs emitidos (original + repetidos), tope 1 + MAX_REPETIDOS
    intentos: list[_Intento] = field(default_factory=list)
    ganador: ItemDescargado | None = None


class _FalloDeGrupo(Exception):
    """4xx de forma en el POST (no 425): el grupo falla, los demas siguen."""

    def __init__(self, logico: _Logico, exc: BaseException) -> None:
        super().__init__(scrub(str(exc)))
        self.logico = logico
        self.exc = exc


def _id_de_duplicado(exc: AdsApiError) -> str | None:
    """El 425 documenta el id duplicado en el cuerpo; None si no parsea."""
    if exc.status != 425 or not exc.cuerpo:
        return None
    match = _RE_DUPLICADO.search(exc.cuerpo)
    return match.group(1) if match else None


def _post_reintentable(exc: AdsApiError) -> bool:
    """5xx, fallo de red (sin status) o 429 agotado: el POST se repite."""
    return exc.status is None or exc.status == 429 or exc.status >= 500


def _emitir_post(client, logico: _Logico, ahora: float) -> None:
    """POST original o repetido hasta conseguir un reportId.

    425 -> adopta el id que anuncia el cuerpo. 5xx/red/429 agotado -> consume
    un repetido y reemite el POST (sin dormir: el backoff ya lo hizo el
    cliente). Otro 4xx -> _FalloDeGrupo. Sin pedidos disponibles -> queda lo
    que haya (el plazo de sondeo falla el grupo si nada completo).
    """
    from app.ads.reports import AdsReportsError, solicitar_reporte

    while True:
        if logico.pedidos == 0:
            sufijo = ""
        elif logico.pedidos <= MAX_REPETIDOS:
            sufijo = f"-r{logico.pedidos}"
        else:
            return  # agotado (original + 2 repetidos): no se pide mas
        try:
            report_id = solicitar_reporte(
                client,
                logico.perfil,
                logico.cfg,
                logico.fecha_ini,
                logico.fecha_fin,
                sufijo=sufijo,
            )
        except AdsApiError as exc:
            duplicado = _id_de_duplicado(exc)
            if duplicado is not None:
                logico.intentos.append(_Intento(duplicado, ahora))
                logico.pedidos += 1
                return
            if _post_reintentable(exc):
                logico.pedidos += 1
                logger.warning(
                    "POST del reporte %s/%s fallo (%s); pedidos=%d",
                    logico.perfil.profile_id,
                    logico.cfg["nombre"],
                    scrub(str(exc)),
                    logico.pedidos,
                )
                continue
            raise _FalloDeGrupo(logico, exc) from exc
        except AdsReportsError as exc:
            # respuesta 200 ilegible (sin reportId / no JSON): forma
            # permanente, reintentar no la arregla -> falla el grupo.
            raise _FalloDeGrupo(logico, exc) from exc
        logico.intentos.append(_Intento(report_id, ahora))
        logico.pedidos += 1
        return


def _url_de_completado(data: dict, report_id: str) -> str:
    """Valida el COMPLETED antes de descargar (fileSize anormal = fail-closed
    ANTES de meter el gzip a memoria). Misma iglesia que la validacion que el
    pipeline secuencial hacia en su poll antes de descargar."""
    from app.ads.reports import MAX_BYTES_REPORTE, AdsReportsError

    url = data.get("url")
    if not isinstance(url, str) or not url:
        raise AdsReportsError(f"reporte {report_id} COMPLETED sin url de descarga")
    file_size = data.get("fileSize")
    if (
        isinstance(file_size, (int, float))
        and not isinstance(file_size, bool)
        and file_size > MAX_BYTES_REPORTE
    ):
        raise AdsReportsError(
            f"reporte {report_id} con fileSize {int(file_size)} bytes excede el "
            f"tope de {MAX_BYTES_REPORTE} (descarga anormal): la corrida aborta "
            "fail-closed"
        )
    return url


def _registrar(
    conn: psycopg.Connection,
    run_id: int,
    perfil: PerfilAds | None,
    cfg: dict | None,
    report_id: str | None,
    status: str,
    reason: str | None = None,
) -> None:
    from app.ads.reports import _registrar_resultado

    _registrar_resultado(conn, run_id, perfil, cfg, report_id, status, reason)


def _sondear_intento(
    client, conn, run_id: int, logico: _Logico, intento: _Intento, ahora: float
) -> None:
    """Un GET de estado. COMPLETED -> descarga (validando) y marca ganador;
    FAILED -> terminal + repetido; PENDING/PROCESSING -> repetido si pasaron
    40 min; GET que falla -> se reintenta la siguiente vuelta."""
    from app.ads.reports import AdsReportsError, _json_de, descargar_filas

    path = f"/reporting/reports/{intento.report_id}"
    try:
        data = _json_de(
            client.get_report(intento.report_id, profile_id=logico.perfil.profile_id),
            "GET",
            path,
        )
    except AdsApiError as exc:
        logger.warning(
            "GET del reporte %s fallo (%s): queda pendiente para la siguiente vuelta",
            intento.report_id,
            scrub(str(exc)),
        )
        return
    except AdsReportsError as exc:
        # GET ilegible (no JSON): se trata como un GET que falla.
        logger.warning(
            "GET del reporte %s ilegible (%s): queda pendiente para la siguiente vuelta",
            intento.report_id,
            scrub(str(exc)),
        )
        return
    status = data.get("status") if isinstance(data, dict) else None
    if status == "COMPLETED":
        try:
            url = _url_de_completado(data, intento.report_id)
            filas = descargar_filas(client, url)
        except AdsApiError as exc:
            logger.warning(
                "descarga del reporte %s fallo (%s): se reintenta la siguiente vuelta",
                intento.report_id,
                scrub(str(exc)),
            )
            return
        except AdsReportsError as exc:
            # forma permanente (sin url, fileSize anormal, contenido corrupto):
            # reintentar no la arregla -> falla el grupo.
            raise _FalloDeGrupo(logico, exc) from exc
        logico.ganador = ItemDescargado(
            perfil=logico.perfil, cfg=logico.cfg, report_id=intento.report_id, filas=filas
        )
        intento.terminal = True
        with conn.transaction():
            _registrar(conn, run_id, logico.perfil, logico.cfg, intento.report_id, "downloaded")
        return
    if status == "FAILED":
        intento.terminal = True
        logger.warning(
            "reporte %s (%s/%s) FAILED: se pide un repetido",
            intento.report_id,
            logico.perfil.profile_id,
            logico.cfg["nombre"],
        )
        _emitir_post(client, logico, ahora)
        return
    if status in ("PENDING", "PROCESSING"):
        if not intento.repetido and ahora - intento.t_pedido >= REINTENTO_TRAS_SEGUNDOS:
            intento.repetido = True
            _emitir_post(client, logico, ahora)
        return
    # status desconocido: la siguiente vuelta lo vuelve a mirar (fail-closed
    # solo si agota el plazo de sondeo).


class _Corrida:
    """Estado y pasos de una invocacion de `descarga` (un reloj, una vuelta)."""

    def __init__(
        self,
        conn: psycopg.Connection,
        grupos: list[GrupoDescarga],
        rango: tuple[dt.date, dt.date],
        al_completar: Callable[..., ResultadoEscritura],
    ) -> None:
        self.conn = conn
        self.grupos = grupos
        self.fecha_ini, self.fecha_fin = rango
        self.al_completar = al_completar
        self.run_ids: dict[str, int] = {}
        self.resultados: dict[str, ResultadoGrupo] = {}
        self.sellado: set[str] = set()
        self.rechazados: list[PerfilAds] = []
        self.logicos: list[_Logico] = []
        self.t0 = 0.0
        self.plazo = 0.0

    # -- sellos -------------------------------------------------------------
    def _sellar_fallo(
        self,
        grupo: GrupoDescarga,
        motivo: str,
        *,
        skip_reason: str,
        exc: BaseException | None = None,
        fallos: list[tuple[str | None, str | None, int | None]] | None = None,
        unidad: tuple[PerfilAds | None, dict | None, str | None] | None = None,
    ) -> None:
        if grupo.clave in self.sellado:
            return
        self.sellado.add(grupo.clave)
        try:
            with self.conn.transaction():
                if unidad is not None:
                    perfil, cfg, reason = unidad
                    _registrar(
                        self.conn, self.run_ids[grupo.clave], perfil, cfg, None, "failed", reason
                    )
                _sellar_run(
                    self.conn,
                    self.run_ids[grupo.clave],
                    ok=False,
                    rows_skipped=0,
                    skip_reason=skip_reason,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo",
                self.run_ids[grupo.clave],
            )
        intentar_procesar_run(self.conn, self.run_ids[grupo.clave])
        self.resultados[grupo.clave] = ResultadoGrupo(
            clave=grupo.clave,
            run_id=self.run_ids[grupo.clave],
            source=grupo.source,
            ok=False,
            motivo=motivo,
            skip_reason=skip_reason,
            exc=exc,
            perfiles_rechazados=self.rechazados,
            fallos=fallos or [],
        )

    def _fallar_todos(self, exc: BaseException) -> None:
        """evaluar_perfiles fallo: las runs se sellan global_failed y la
        excepcion original sube (el caller jamas ve resultados)."""
        motivo = scrub(str(exc)) or type(exc).__name__
        for grupo in self.grupos:
            try:
                with self.conn.transaction():
                    _registrar(
                        self.conn,
                        self.run_ids[grupo.clave],
                        None,
                        None,
                        None,
                        "global_failed",
                        motivo,
                    )
                    _sellar_run(
                        self.conn,
                        self.run_ids[grupo.clave],
                        ok=False,
                        rows_skipped=0,
                        skip_reason=motivo,
                    )
            except Exception:
                logger.warning(
                    "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo",
                    self.run_ids[grupo.clave],
                )
            self.sellado.add(grupo.clave)
            intentar_procesar_run(self.conn, self.run_ids[grupo.clave])
            self.resultados[grupo.clave] = ResultadoGrupo(
                clave=grupo.clave,
                run_id=self.run_ids[grupo.clave],
                source=grupo.source,
                ok=False,
                motivo="error",
                skip_reason=motivo,
                exc=exc,
                fallos=[(None, None, None)],
            )

    def _sellar_cero_perfiles(self) -> None:
        motivo = (
            "ningun perfil aceptado: /v2/profiles devolvio 0 perfiles"
            if not self.rechazados
            else "ningun perfil aceptado: "
            + "; ".join(perfil.motivo or "sin motivo" for perfil in self.rechazados)
        )
        for grupo in self.grupos:
            with self.conn.transaction():
                if not self.rechazados:
                    _registrar(
                        self.conn,
                        self.run_ids[grupo.clave],
                        None,
                        None,
                        None,
                        "global_failed",
                        motivo,
                    )
                _sellar_run(
                    self.conn,
                    self.run_ids[grupo.clave],
                    ok=False,
                    rows_skipped=0,
                    skip_reason=motivo,
                )
            self.sellado.add(grupo.clave)
            intentar_procesar_run(self.conn, self.run_ids[grupo.clave])
            self.resultados[grupo.clave] = ResultadoGrupo(
                clave=grupo.clave,
                run_id=self.run_ids[grupo.clave],
                source=grupo.source,
                ok=False,
                motivo="cero_perfiles",
                skip_reason=motivo,
                perfiles_rechazados=self.rechazados,
            )

    def _fallo_de_grupo(self, fallo: _FalloDeGrupo) -> None:
        logico = fallo.logico
        razon = scrub(str(fallo.exc)) or type(fallo.exc).__name__
        self._sellar_fallo(
            logico.grupo,
            "error",
            skip_reason=razon,
            exc=fallo.exc,
            fallos=[(logico.perfil.platform, logico.cfg["nombre"], logico.perfil.profile_id)],
            unidad=(logico.perfil, logico.cfg, razon),
        )

    # -- fases ----------------------------------------------------------------
    def _abrir_runs(self) -> None:
        with self.conn.transaction():
            for grupo in self.grupos:
                self.run_ids[grupo.clave] = self.conn.execute(
                    _SQL_ABRIR_RUN, (grupo.source,)
                ).fetchone()[0]

    def _registrar_perfiles(self, client) -> list[PerfilAds]:
        todos = evaluar_perfiles(client)
        aceptados = [perfil for perfil in todos if perfil.aceptado]
        self.rechazados = [perfil for perfil in todos if not perfil.aceptado]
        for perfil in self.rechazados:
            logger.warning(
                "perfil %s rechazado, sin metricas esta corrida: %s",
                perfil.profile_id,
                perfil.motivo,
            )
        with self.conn.transaction():
            for grupo in self.grupos:
                for rechazado in self.rechazados:
                    _registrar(
                        self.conn,
                        self.run_ids[grupo.clave],
                        rechazado,
                        None,
                        None,
                        "rejected",
                        rechazado.motivo or "perfil rechazado sin motivo",
                    )
                for perfil in aceptados:
                    for cfg in grupo.cfgs:
                        _registrar(
                            self.conn, self.run_ids[grupo.clave], perfil, cfg, None, "pending"
                        )
        return aceptados

    def _pedir_todos(self, client, aceptados: list[PerfilAds], ahora: float) -> None:
        for grupo in self.grupos:
            for perfil in aceptados:
                for cfg in grupo.cfgs:
                    logico = _Logico(grupo, perfil, cfg, self.fecha_ini, self.fecha_fin)
                    self.logicos.append(logico)
                    if grupo.clave in self.sellado:
                        continue
                    try:
                        _emitir_post(client, logico, ahora)
                    except _FalloDeGrupo as fallo:
                        self._fallo_de_grupo(fallo)

    def _sondear_una_vuelta(self, client, reloj: Callable[[], float]) -> None:
        for logico in self.logicos:
            if logico.grupo.clave in self.sellado or logico.ganador is not None:
                continue
            try:
                # snapshot: un repetido emitido en esta vuelta se sondea desde
                # la siguiente (su pedido acaba de salir).
                for intento in list(logico.intentos):
                    if logico.ganador is not None or intento.terminal:
                        continue
                    if reloj() >= self.plazo:
                        break
                    _sondear_intento(
                        client,
                        self.conn,
                        self.run_ids[logico.grupo.clave],
                        logico,
                        intento,
                        reloj(),
                    )
            except _FalloDeGrupo as fallo:
                self._fallo_de_grupo(fallo)

    def _completar_grupos(self) -> None:
        for grupo in self.grupos:
            if grupo.clave in self.sellado:
                continue
            suyos = [logico for logico in self.logicos if logico.grupo is grupo]
            if any(logico.ganador is None for logico in suyos):
                continue
            items = [logico.ganador for logico in suyos if logico.ganador is not None]
            try:
                escritura = self.al_completar(
                    self.conn,
                    self.run_ids[grupo.clave],
                    grupo,
                    items,
                    fecha_ini=self.fecha_ini,
                    fecha_fin=self.fecha_fin,
                )
            except BaseException as exc:  # el escritor ya sello ok=false
                self._sellar_fallo(
                    grupo,
                    "error",
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                    exc=exc,
                    fallos=[
                        (logico.perfil.platform, logico.cfg["nombre"], logico.perfil.profile_id)
                        for logico in suyos
                    ],
                )
            else:
                self.sellado.add(grupo.clave)
                intentar_procesar_run(self.conn, self.run_ids[grupo.clave])
                self.resultados[grupo.clave] = ResultadoGrupo(
                    clave=grupo.clave,
                    run_id=self.run_ids[grupo.clave],
                    source=grupo.source,
                    ok=True,
                    motivo="ok",
                    rows_written=escritura.rows_written,
                    rows_skipped=escritura.rows_skipped,
                    skip_reason=escritura.skip_reason,
                    perfiles_rechazados=self.rechazados,
                    escritura=escritura,
                )

    def _fallar_por_plazo(self) -> None:
        for grupo in self.grupos:
            if grupo.clave in self.sellado:
                continue
            incompletos = [
                logico
                for logico in self.logicos
                if logico.grupo is grupo and logico.ganador is None
            ]
            if not incompletos:
                continue
            fallos = [
                (logico.perfil.platform, logico.cfg["nombre"], logico.perfil.profile_id)
                for logico in incompletos
            ]
            detalle = "; ".join(
                f"{plataforma or 'global'}/{nombre}/perfil {perfil}" if perfil else f"{nombre}"
                for plataforma, nombre, perfil in fallos
            )
            primero = incompletos[0]
            self._sellar_fallo(
                grupo,
                "plazo",
                skip_reason=(
                    f"reportes sin COMPLETED tras el plazo de sondeo de "
                    f"{int(PLAZO_SONDEO_SEGUNDOS // 60)} min: {detalle}"
                ),
                fallos=fallos,
                unidad=(
                    primero.perfil,
                    primero.cfg,
                    "sin COMPLETED tras agotar el plazo de sondeo y los repetidos",
                ),
            )

    # -- orquestacion ---------------------------------------------------------
    def run(
        self,
        client,
        *,
        reloj: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> dict[str, ResultadoGrupo]:
        self.t0 = reloj()
        self.plazo = self.t0 + PLAZO_SONDEO_SEGUNDOS
        self._abrir_runs()
        try:
            aceptados = self._registrar_perfiles(client)
        except BaseException as exc:
            self._fallar_todos(exc)
            raise
        if not aceptados:
            self._sellar_cero_perfiles()
            return self.resultados
        self._pedir_todos(client, aceptados, reloj())
        while True:
            self._sondear_una_vuelta(client, reloj)
            self._completar_grupos()
            vivos = [
                logico
                for logico in self.logicos
                if logico.ganador is None and logico.grupo.clave not in self.sellado
            ]
            if not vivos or reloj() >= self.plazo:
                break
            espera = (
                ESPERA_RAPIDA_SEGUNDOS
                if reloj() - self.t0 < RAPIDO_HASTA_SEGUNDOS
                else ESPERA_LENTA_SEGUNDOS
            )
            sleep(espera)
        self._fallar_por_plazo()
        return self.resultados


def descarga(
    conn: psycopg.Connection,
    client,
    grupos: list[GrupoDescarga] | tuple[GrupoDescarga, ...],
    rango: tuple[dt.date, dt.date],
    *,
    reloj: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    al_completar: Callable[..., ResultadoEscritura],
) -> dict[str, ResultadoGrupo]:
    """Pide, sondea y descarga TODOS los reportes de `grupos` en una vuelta.

    Devuelve un ResultadoGrupo por clave. `al_completar(conn, run_id, grupo,
    items, *, fecha_ini, fecha_fin)` escribe y sella cada grupo en su
    transaccion en cuanto tiene todos sus reportes; un fallo suyo deja el
    grupo sellado ok=false (best-effort) y no tumba a los demas. Un grupo que
    agota repetidos y plazo falla SOLO su grupo: los demas quedan ok=true.
    """
    corrida = _Corrida(conn, list(grupos), rango, al_completar)
    return corrida.run(client, reloj=reloj, sleep=sleep)
