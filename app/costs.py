"""Ingesta de productos y costos desde la SQLite de contabilidad (ORBIT 06 0.1).

Fuente: snapshot READ-ONLY de la base de accounting (`sku_costs` para las
vigencias de costo, `bom_headers` para el nombre del producto). El snapshot lo
produce el runbook de docs/DEPLOY.md con la API `.backup()` de SQLite (la base
vive en WAL; un cp directo deja fuera el WAL) y se pasa con `--sqlite RUTA`.
Destino: `product` + `sku_cost` en Orbit, con el rol de ingesta
(ORBIT_DSN_INGEST). Este modulo NUNCA escribe en contabilidad y jamas toca
`decision`, `apply_queue` ni `ads_optimizer_goal`.

Decisiones selladas de la tarea (plans/orbit-06.md, "Decisiones de la 0.1",
con su evidencia medida; este docstring es el resumen operativo):

1. Colapso: el costo del dia D es el de la ULTIMA fila que empieza <= D (una
   venta del dia D uso UN costo; la rotacion intradia de Odoo es ruido de
   sync). Dias consecutivos de igual costo se funden en una vigencia. Una
   fila con `valid_from` NULL (backfill 2026-02-20) arranca en
   `date(created_at)`: contabilidad la trata como "desde siempre", pero la
   fila no puede probar cobertura anterior a su creacion (regla 3).
2. Re-corrida contra el esquema real: vigencia identica ya presente = no-op;
   vigencia nueva CIERRA la abierta (`UPDATE valid_to`, unica transicion que
   el trigger `sku_cost_solo_cierra_vigencia` permite) e inserta. Un costo
   nuevo entra AL DIA SIGUIENTE de su ultimo cambio: el dia UTC de la corrida
   todavia puede cambiar (D2) y no se publica. Lo ya publicado es inmutable:
   si el origen reescribe historia, se conserva y el SKU sigue al origen desde
   ayer (convergencia, sin congelarse); un SKU ausente del origen o presente
   sin vigencia publicable (solape o todas sus filas rechazadas) cierra su
   vigencia abierta en ayer y queda sin costo. Un estado imposible aborta la
   corrida entera en vez de dejar un costo viejo abierto.
3. `includes_tax = false`: `sku_costs.cost` es el `standard_price` de Odoo
   verbatim (costo de VALORACION: kits calculados con compute_price, que suma
   componentes netos; ninguna herramienta del ecosistema aplica IVA al costo).
   Residual declarado en el plan: si un alta manual de Odoo tuviera precio
   con IVA, declarar false SUBESTIMA margen (conservador), jamas lo infla.
4. Un cierre masivo (mas del 10 % de los productos con vigencia abierta que
   quedarian sin costo por desaparicion del origen o por falta de vigencia
   publicable) es senal de una copia truncada o de un sync a medias: la corrida
   aborta sin escribir y avisa. `--acepto-cierre-masivo` lo permite a sabiendas.

Sellado 1 (regla 3): costo 0 o NULL = dato faltante -> la fila NO se escribe,
queda contada en `rows_skipped`, y ADEMAS corta la cadena (los dias que esa
fila cubria quedan sin costo: el cero no es dato y la vigencia anterior no se
estira sobre el hueco). Dinero: Decimal exacto desde el str del REAL de SQLite
(regla 4); mas de 4 decimales o fuera de NUMERIC(14,4) = fila rechazada.

Contabilidad (ingest_run): la unidad es la VIGENCIA final (post-colapso).
rows_written = vigencias insertadas + cierres; todo rechazo queda en
rows_skipped con su motivo acumulado. La corrida nace abierta en su propia
transaccion, el trabajo corre en otra y se sella al final (patron de
app/ads/structure.py); en fallo: rollback del trabajo y sello best-effort
ok=false.

`python -m app.cli ingest costs --sqlite RUTA`: sin ORBIT_DSN_INGEST o sin
snapshot -> exit 2 fail-closed; fallo de la corrida -> exit 1; exito -> 0.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import psycopg

from app.db import connect
from app.notifica import envia_aviso
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_sku_costs"

# Marca sellada (D3): nombre derivado del SKU cuando bom_headers no lo trae.
# Jamas un nombre descriptivo inventado (regla 3); los productos asi creados
# quedan contados en la corrida.
NOMBRE_SIN_ODOO = "[sin nombre en Odoo]"

# D3: el costo de Odoo es NETO de IVA (ver docstring). Constante declarada, no
# asumida: la evidencia y el residual viven en plans/orbit-06.md.
INCLUDES_TAX = False

MONEDAS = ("MXN", "USD")
_MAX_DECIMALES = Decimal("0.0001")  # money_amount = NUMERIC(14,4)
# Umbral ruido-binario vs precision real: un REAL de SQLite con dinero de 2
# decimales arrastra residuos <= 1e-13 (medido: 506/2708 filas); una precision
# genuina de mas de 4 decimales empieza en 1e-5. Entre 1e-6 y 1e-5 no hay
# datos reales: el umbral separa ruido de dato con un orden de magnitud de margen.
_RUIDO_FLOAT = Decimal("0.00001")
_MAX_COSTO = Decimal(10) ** 10  # 10 enteros + 4 decimales = 14 digitos


class CostsError(Exception):
    """Error de la ingesta de costos (snapshot inexistente o ilegible)."""


@dataclass(frozen=True)
class FilaCosto:
    """Una fila de sku_costs tal cual viene de contabilidad (valores crudos)."""

    sku: str
    costo: float | None
    moneda: str | None
    inicio: str | None  # valid_from ('YYYY-MM-DD HH:MM:SS' o None)
    fin: str | None  # valid_to (None = vigencia abierta)
    creado: str | None  # created_at: inicio cuando valid_from es NULL


class Vigencia(NamedTuple):
    """Un tramo de costo colapsado a dia, listo para sku_cost.

    NamedTuple a proposito: una Vigencia ES la tupla que se escribe, y la
    igualdad con tuplas hace los tests (y los SELECT de verificacion)
    comparables sin conversiones.
    """

    sku: str
    costo: Decimal
    moneda: str
    desde: dt.date
    hasta: dt.date | None  # None = abierta


class Cierre(NamedTuple):
    """Una vigencia publicada abierta que se cierra en `valid_to`."""

    valid_from: dt.date
    valid_to: dt.date


class PlanSku(NamedTuple):
    """La escritura que necesita un SKU contra lo ya publicado.

    `convergencia` es informativa (el origen reescribio historia ya publicada);
    `sin_costo` avisa que el origen no da costo para el ultimo dia cerrado.
    """

    cierres: list[Cierre]
    inserciones: list[Vigencia]
    convergencia: str | None
    sin_costo: bool


@dataclass(frozen=True)
class OrigenCostos:
    filas: tuple[FilaCosto, ...]
    nombres: dict[str, str]  # sku -> nombre de bom_headers (sin vacios)


@dataclass(frozen=True)
class ResultadoSync:
    """Outcome contable de la corrida (espejo de la ingest_run sellada).

    Los contadores de colapso (intradia con sucesor, intradia en el borde
    —dias que pierden su costo— y fusiones) salen del proceso por stdout:
    son la evidencia que el plan promete contar (hallazgo 3 del adversario
    + revision: un contador que no se ve no cuenta).
    """

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    productos_nuevos: int
    productos_actualizados: int
    nombres_derivados: int
    vigencias_insertadas: int
    vigencias_cerradas: int
    filas_origen: int
    vigencias_finales: int
    segmentos_intradia_colapsados: int
    segmentos_intradia_en_borde: int
    fusiones: int
    convergencias: int = 0
    sin_costo_en_origen: int = 0
    detalle: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Lectura del snapshot (read-only por construccion: mode=ro + solo SELECT)
# ---------------------------------------------------------------------------


def leer_origen(ruta: Path | str) -> OrigenCostos:
    """Lee sku_costs + bom_headers del snapshot. Abre con `mode=ro`: aunque el
    archivo se montara escribible por accidente, esta conexion no puede mutar
    nada de contabilidad."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise CostsError(f"snapshot inexistente: {ruta}")
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        filas = tuple(
            FilaCosto(
                sku=fila[0] or "",
                costo=fila[1],
                moneda=fila[2],
                inicio=fila[3],
                fin=fila[4],
                creado=fila[5],
            )
            for fila in con.execute(
                "SELECT sku, cost, currency, valid_from, valid_to, created_at"
                " FROM sku_costs"
                " ORDER BY sku, COALESCE(valid_from, created_at), id"
            )
        )
        nombres = {
            fila[0]: fila[1]
            for fila in con.execute(
                "SELECT product_sku, product_name FROM bom_headers"
                " WHERE product_name IS NOT NULL AND TRIM(product_name) != ''"
                # Un SKU con varios BOMs dejaria el nombre al orden arbitrario
                # de SQLite: ORDER BY fija el ultimo por id y hace la corrida
                # determinista (hallazgo de revision, baja).
                " ORDER BY product_sku, id"
            )
        }
    finally:
        con.close()
    return OrigenCostos(filas=filas, nombres=nombres)


# ---------------------------------------------------------------------------
# Colapso (pura): filas crudas -> vigencias por dia
# ---------------------------------------------------------------------------


def _dia(texto: str | None) -> dt.date | None:
    """'YYYY-MM-DD[ HH:MM:SS]' -> date; None/basura -> None.

    Se parsea el timestamp COMPLETO, no los primeros 10 caracteres: un
    '2026-04-01basura' es un dato corrupto, no una fecha plausible que se
    publica (hallazgo 4 de codex, cross-review).
    """
    if not texto:
        return None
    try:
        return dt.datetime.fromisoformat(texto).date()
    except ValueError:
        return None


def _costo_decimal(costo: float) -> Decimal:
    """Decimal exacto del REAL de origen, cuantizado a 4 decimales.

    Hallazgo de la corrida real (2026-08-29): 506 de 2,708 filas llegan como
    554.1800000000001 — ruido binario del float de SQLite (residuo <= 1e-13,
    dinero de 2 decimales de Odoo). El ruido se cuantiza; la precision
    GENUINA de mas de 4 decimales (residuo >= 1e-5) la rechaza
    _motivo_rechazo: guardarla exigiria redondear dinero en silencio.
    """
    return Decimal(str(costo)).quantize(_MAX_DECIMALES)


def _motivo_rechazo(fila: FilaCosto) -> str | None:
    """Sellado 1 + regla 4: que hace que una FILA no se escriba, con motivo."""
    if not fila.sku or not fila.sku.strip():
        return "sku vacio"
    if fila.costo is None or not math.isfinite(fila.costo) or fila.costo <= 0:
        return "costo cero o nulo (dato faltante)"
    if not fila.moneda or fila.moneda.strip().upper() not in MONEDAS:
        return f"moneda fuera de dominio (MXN/USD): {fila.moneda}"
    if _dia(fila.inicio) is None and _dia(fila.creado) is None:
        return "fecha de inicio ilegible (valid_from/created_at)"
    if fila.fin is not None and _dia(fila.fin) is None:
        return "fecha de cierre ilegible (valid_to)"
    valor = Decimal(str(fila.costo))
    if abs(valor - valor.quantize(_MAX_DECIMALES)) >= _RUIDO_FLOAT:
        return "costo con mas de 4 decimales"
    if valor >= _MAX_COSTO:
        return "costo fuera de rango NUMERIC(14,4)"
    if _costo_decimal(fila.costo) <= 0:
        # Sub-centavo (0 < costo < 5e-5) cuantiza a 0.0000: sin este rechazo
        # el INSERT reventaria sku_cost_positivo y abortaria la corrida ENTERA
        # (hallazgo 1 del adversario) en vez del skip contado del sellado 1.
        return "costo cero o nulo (dato faltante)"
    return None


def colapsar(
    filas: list[FilaCosto] | tuple[FilaCosto, ...],
) -> tuple[dict[str, list[Vigencia]], Counter, Counter]:
    """Colapsa las filas de contabilidad a vigencias de dia, por SKU.

    Regla por fila (D2): la fila cubre [date(inicio o creado), date(fin)); el
    tramo vacio (abre y cierra el mismo dia) desaparece cuando la serie sigue
    cubriendo ese dia (ruido intradia del sync) y se cuenta APARTE cuando esta
    en el borde (el dia queda sin costo: dato faltante, sellado 1); una fila
    rechazada CORTA la cadena (sus dias quedan sin costo, la vigencia anterior
    no se estira sobre el hueco); un SOLAPE en el origen corta el SKU ENTERO
    (no se publica ni la vigencia vieja como abierta). Tramos contiguos de
    igual costo y moneda se funden. Devuelve (vigencias por sku, skips con
    motivo, stats).
    """
    skips: Counter = Counter()
    stats: Counter = Counter()
    stats["filas"] = len(filas)
    vigencias: dict[str, list[Vigencia]] = {}

    por_sku: dict[str, list[FilaCosto]] = {}
    for fila in filas:
        por_sku.setdefault(fila.sku, []).append(fila)

    for sku, filas_sku in por_sku.items():
        serie: list[Vigencia] = []
        intradia: list[dt.date] = []  # dias de tramos que vivieron < 1 dia
        solape = False
        for fila in sorted(filas_sku, key=lambda f: f.inicio or f.creado or ""):
            if solape:
                # Hallazgo 2 del adversario: tras un solape, el resto de la
                # cadena del origen ya no es reconstruible: se cuenta todo y
                # el SKU completo queda sin escribir.
                skips["vigencia solapada en el origen (sku completo sin escribir)"] += 1
                continue
            motivo = _motivo_rechazo(fila)
            desde = _dia(fila.inicio) or _dia(fila.creado)
            if motivo is not None:
                # Una fila rechazada tambien puede solapar la vigencia abierta
                # del origen: si lo hace, el SKU entero queda sin escribir (no
                # se valida el costo viejo sobre un origen inconsistente).
                if (
                    desde is not None
                    and serie
                    and (serie[-1].hasta is None or serie[-1].hasta > desde)
                ):
                    solape = True
                else:
                    skips[motivo] += 1
                    continue
                skips["vigencia solapada en el origen (sku completo sin escribir)"] += 1
                continue
            hasta = _dia(fila.fin)
            assert desde is not None  # _motivo_rechazo ya valido el inicio
            if hasta is not None and hasta < desde:
                skips["vigencia invertida (valid_to < valid_from)"] += 1
                continue
            if hasta == desde:
                # Costo que vivio menos de un dia: si la serie termina
                # cubriendo ese dia (la fila que sigue), fue ruido del sync;
                # si no, el dia queda SIN costo (dato faltante). Solo se puede
                # clasificar con la serie completa (post-pase, abajo).
                intradia.append(desde)
                continue
            tramo = Vigencia(
                sku=sku,
                costo=_costo_decimal(fila.costo),
                moneda=fila.moneda.strip().upper(),
                desde=desde,
                hasta=hasta,
            )
            if serie and (serie[-1].hasta is None or serie[-1].hasta > tramo.desde):
                # El origen solaparia sus propias vigencias: no construimos un
                # C que violaria el EXCLUDE, y tampoco publicamos la vieja como
                # abierta (divergiria del costo vigente de la fuente).
                solape = True
                skips["vigencia solapada en el origen (sku completo sin escribir)"] += 1
                continue
            if (
                serie
                and serie[-1].hasta == tramo.desde
                and serie[-1].costo == tramo.costo
                and serie[-1].moneda == tramo.moneda
            ):
                serie[-1] = serie[-1]._replace(hasta=tramo.hasta)
                stats["fusiones"] += 1
            else:
                serie.append(tramo)
        if solape:
            # Los tramos ya construidos del SKU tambien quedan sin escribir:
            # el contador refleja TODO lo que no se publico del SKU.
            skips["vigencia solapada en el origen (sku completo sin escribir)"] += len(serie)
            continue
        if serie:
            vigencias[sku] = serie
            stats["vigencias"] += len(serie)
        # Clasificacion diferida de los intradia (hallazgo 3 del adversario):
        # cubierto por la serie = ruido del sync; dia sin cobertura = borde,
        # el dia se pierde como dato faltante y queda contado APARTE.
        for dia in intradia:
            cubierto = any(t.desde <= dia and (t.hasta is None or t.hasta > dia) for t in serie)
            if cubierto:
                stats["segmentos_intradia_colapsados"] += 1
            else:
                stats["segmentos_intradia_en_borde"] += 1

    return vigencias, skips, stats


# ---------------------------------------------------------------------------
# Escritura en Orbit (product + sku_cost, rol de ingesta)
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

# El UPDATE del nombre solo cuando cambia: el re-run de una base ya cargada no
# genera escrituras (no-op REAL). Sin fila devuelta = el WHERE corto la
# actualizacion y hay que leer el id existente.
# El UPDATE del nombre solo cuando MEJORA (hallazgo 2 de codex, cross-review):
# si bom_headers pierde temporalmente el nombre de un SKU cuyo nombre real ya
# esta publicado (sync de Odoo incompleto), el derivado NO lo pisa — la
# degradacion real->derivado quedaria grabada en el catalogo. Se actualiza
# solo si el existente es derivado (upgrade) o el entrante es real (cambio
# real->real). Los dos %s extra son el prefijo de la marca, dos veces.
_SQL_UPSERT_PRODUCTO = """
INSERT INTO product (odoo_sku, name) VALUES (%s, %s)
ON CONFLICT (odoo_sku) DO UPDATE SET name = EXCLUDED.name
 WHERE product.name IS DISTINCT FROM EXCLUDED.name
   AND (product.name LIKE %s OR EXCLUDED.name NOT LIKE %s)
RETURNING id, (xmax = 0) AS es_nuevo
"""

_SQL_VIGENCIAS_PUBLICADAS = (
    "SELECT valid_from, cost_amount, valid_to, cost_currency, includes_tax"
    " FROM sku_cost WHERE product_id = %s ORDER BY valid_from"
)

# Unica mutacion permitida por el trigger: NULL -> fecha, una vez. El guard
# valid_to IS NULL es defensa (el plan ya valido que solo se cierra lo abierto).
_SQL_CERRAR_VIGENCIA = (
    "UPDATE sku_cost SET valid_to = %s"
    " WHERE product_id = %s AND valid_from = %s AND valid_to IS NULL"
)

_SQL_INSERTAR_VIGENCIA = (
    "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
    " valid_from, valid_to, ingest_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def recortar(serie: list[Vigencia], *, corte: dt.date) -> list[Vigencia]:
    """Lo firme de la serie: el dia `corte` todavia puede cambiar (D2).

    Quita las vigencias que empiezan en `corte` o despues y deja abierta
    (`hasta=None`) la que llega a `corte` o lo pasa. Asi el costo nuevo entra
    al dia siguiente de su ultimo cambio, nunca el mismo dia de la corrida.
    """
    firme: list[Vigencia] = []
    for v in serie:
        if v.desde >= corte:
            continue
        if v.hasta is None or v.hasta >= corte:
            firme.append(v._replace(hasta=None))
        else:
            firme.append(v)
    return firme


def _prefijo(
    publicadas: dict[dt.date, tuple[Decimal, dt.date | None, str, bool]],
    firme: list[Vigencia],
) -> bool:
    """True si lo publicado es prefijo inmutable de `firme` (mismos importes)."""
    if not publicadas:
        return True
    max_desde = max(publicadas)
    por_desde = {v.desde: v for v in firme}
    for desde_pub, (costo, hasta, moneda, tax) in publicadas.items():
        v = por_desde.get(desde_pub)
        if v is None:
            return False
        if v.costo != costo or v.moneda != moneda or tax != INCLUDES_TAX:
            return False
        if hasta is not None and v.hasta != hasta:
            return False
    return all(not (v.desde <= max_desde and v.desde not in publicadas) for v in firme)


def _plan_prefijo(
    publicadas: dict[dt.date, tuple[Decimal, dt.date | None, str, bool]],
    firme: list[Vigencia],
) -> PlanSku:
    """Plan de escritura cuando lo publicado es prefijo de la serie firme."""
    cierres = [
        Cierre(v.desde, v.hasta)
        for v in firme
        if v.desde in publicadas and publicadas[v.desde][1] is None and v.hasta is not None
    ]
    inserciones = [v for v in firme if v.desde not in publicadas]
    return PlanSku(cierres, inserciones, None, False)


def planear_sku(
    serie: list[Vigencia] | None,
    publicadas: dict[dt.date, tuple[Decimal, dt.date | None, str, bool]],
    *,
    corte: dt.date,
) -> PlanSku:
    """Lo publicado es inmutable; lo que Orbit no ha fijado sigue al origen.

    Si lo publicado es prefijo de `firme`, cierra e inserta como siempre. Si
    el origen reescribio historia, borro filas o el SKU desaparecio, converge
    hacia adelante desde U = max(corte - 1 dia, abierta.valid_from + 1 dia):
    nunca toca dias anteriores a U. Un estado imposible levanta CostsError en
    vez de dejar un costo viejo abierto.
    """
    firme = recortar(list(serie or []), corte=corte)
    if _prefijo(publicadas, firme):
        return _plan_prefijo(publicadas, firme)

    abiertas = [(desde, pub) for desde, pub in publicadas.items() if pub[1] is None]
    if len(abiertas) > 1:
        raise CostsError(
            f"estado imposible: hay mas de una vigencia publicada abierta ({len(abiertas)})"
        )

    if not abiertas:
        # Un SKU que desaparecio y volvio no tiene vigencia abierta que cerrar:
        # su ultima cerrada manda el piso. Se reabre desde ayer o desde ese
        # cierre, nunca antes (lo publicado queda igual).
        ultimo_cierre = max(
            (pub[1] for pub in publicadas.values() if pub[1] is not None),
            default=None,
        )
        if ultimo_cierre is None:
            raise CostsError("estado imposible: lo publicado no tiene vigencia abierta ni cerrada")
        u = max(corte - dt.timedelta(days=1), ultimo_cierre)
        v = next(
            (x for x in firme if x.desde <= u and (x.hasta is None or x.hasta > u)),
            None,
        )
        if v is None:
            return PlanSku([], [], None, False)
        if u == corte:
            # Se publicaria hoy: se reconcilia manana, sin aviso (no hubo
            # escritura y el estado se repite hasta que el dia sea firme).
            return PlanSku([], [], None, False)
        return PlanSku(
            [],
            [Vigencia(v.sku, v.costo, v.moneda, u, None)],
            "el origen reescribio historia publicada; sigue al origen desde " + u.isoformat(),
            False,
        )

    desde_abierta, (costo, _, moneda, tax) = abiertas[0]
    u = max(corte - dt.timedelta(days=1), desde_abierta + dt.timedelta(days=1))
    if desde_abierta >= u:
        raise CostsError(
            f"estado imposible: la vigencia abierta ({desde_abierta}) no termina antes de {u}"
        )
    for hasta in (pub[1] for pub in publicadas.values() if pub[1] is not None):
        if hasta > u:
            raise CostsError(
                f"estado imposible: una vigencia cerrada termina en {hasta}, despues de {u}"
            )

    v = next(
        (x for x in firme if x.desde <= u and (x.hasta is None or x.hasta > u)),
        None,
    )
    if v is None:
        return PlanSku([Cierre(desde_abierta, u)], [], None, True)

    convergencia = "el origen reescribio historia publicada; sigue al origen desde " + u.isoformat()
    if (v.costo, v.moneda) == (costo, moneda) and tax == INCLUDES_TAX:
        # La divergencia es de historia ya publicada (inmutable) y el costo
        # abierto ya coincide con el origen: no hay escritura ni aviso diario.
        return PlanSku([], [], None, False)
    if u == corte:
        # La abierta empezo ayer: cerrarla hoy dejaria el costo nuevo a partir
        # de hoy (el dia que todavia puede cambiar). Se reconcilia manana.
        return PlanSku([], [], None, False)
    return PlanSku(
        [Cierre(desde_abierta, u)],
        [Vigencia(v.sku, v.costo, v.moneda, u, None)],
        convergencia,
        False,
    )


def sync_costos(
    conn: psycopg.Connection,
    ruta_sqlite: Path | str,
    *,
    corte: dt.date,
    acepto_cierre_masivo: bool = False,
) -> ResultadoSync:
    """Corre la ingesta completa y sella su ingest_run (patron structure.sync).

    `corte` es el dia UTC de la corrida: ese dia todavia puede cambiar (D2) y
    no se publica hasta la corrida siguiente. Antes de escribir, un cierre por
    falta de costo de mas del 10 % de los productos con vigencia abierta
    aborta la corrida (copia truncada) salvo `acepto_cierre_masivo`.
    """
    origen = leer_origen(ruta_sqlite)
    vigencias_por_sku, skips, stats = colapsar(origen.filas)

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    productos_nuevos = 0
    productos_actualizados = 0
    nombres_derivados = 0
    insertadas = 0
    cerradas = 0
    convergencias = 0
    sin_costo_en_origen = 0
    detalle: list[str] = []
    try:
        with conn.transaction():
            for sku in sorted(vigencias_por_sku):
                nombre = origen.nombres.get(sku)
                if nombre is None:
                    nombre = f"{NOMBRE_SIN_ODOO} {sku}"
                    nombres_derivados += 1
                fila = conn.execute(
                    _SQL_UPSERT_PRODUCTO,
                    (sku, nombre, f"{NOMBRE_SIN_ODOO}%", f"{NOMBRE_SIN_ODOO}%"),
                ).fetchone()
                if fila is None:
                    # Nombre identico O protegido contra degradacion: el WHERE
                    # corto el UPDATE; la fila existente ya esta en `productos`.
                    pass
                else:
                    _, es_nuevo = fila
                    if es_nuevo:
                        productos_nuevos += 1
                    else:
                        productos_actualizados += 1

            # Un solo bucle sobre TODOS los productos de Orbit: los que traen
            # vigencias del origen, los ausentes y los presentes sin vigencia
            # publicable (solape o todas rechazadas). Ausentes y no publicables
            # cierran su abierta en ayer y quedan sin costo; el detalle
            # distingue el motivo y ambos cuentan para la guarda del 10 %.
            productos = dict(conn.execute("SELECT odoo_sku, id FROM product").fetchall())
            skus_origen = {f.sku for f in origen.filas}
            sin_vigencia_publicable = skus_origen - set(vigencias_por_sku)
            planes: list[tuple[str, int, PlanSku]] = []
            con_abierta = 0
            candidatos_sin_costo = 0
            for sku, product_id in sorted(productos.items()):
                publicadas = {
                    vf: (costo, hasta, moneda, tax)
                    for vf, costo, hasta, moneda, tax in conn.execute(
                        _SQL_VIGENCIAS_PUBLICADAS, (product_id,)
                    )
                }
                if any(pub[1] is None for pub in publicadas.values()):
                    con_abierta += 1
                plan = planear_sku(vigencias_por_sku.get(sku), publicadas, corte=corte)
                planes.append((sku, product_id, plan))
                if plan.sin_costo:
                    candidatos_sin_costo += 1

            # Una copia truncada no puede dejar media cartera sin costo:
            # 10 * n > total equivale a "pasa del 10 %" sin floats.
            if (
                not acepto_cierre_masivo
                and con_abierta > 0
                and candidatos_sin_costo * 10 > con_abierta
            ):
                aviso = (
                    f"Costos: la corrida dejaria {candidatos_sin_costo} de"
                    f" {con_abierta} productos sin costo (ausentes del origen o"
                    " sin vigencia publicable); se detuvo sin escribir. Si la"
                    " copia de contabilidad esta completa, corre con"
                    " --acepto-cierre-masivo."
                )
                envia_aviso(aviso)
                raise CostsError(aviso)

            for sku, product_id, plan in planes:
                # Primero los cierres: la vigencia nueva que empieza donde la
                # abierta se cierra chocaria el EXCLUDE si se insertara antes.
                for cierre in plan.cierres:
                    conn.execute(
                        _SQL_CERRAR_VIGENCIA,
                        (cierre.valid_to, product_id, cierre.valid_from),
                    )
                    cerradas += 1
                for v in plan.inserciones:
                    conn.execute(
                        _SQL_INSERTAR_VIGENCIA,
                        (product_id, v.costo, v.moneda, INCLUDES_TAX, v.desde, v.hasta, run_id),
                    )
                    insertadas += 1
                if plan.convergencia is not None:
                    convergencias += 1
                    detalle.append(f"{sku}: {plan.convergencia}")
                if plan.sin_costo:
                    sin_costo_en_origen += 1
                    if sku in sin_vigencia_publicable:
                        detalle.append(f"{sku}: sin vigencia publicable; su costo cerro ayer")
                    else:
                        detalle.append(f"{sku}: sin costo en el origen; su costo cerro ayer")

            skip_reason = _formato_skip_reason(skips)
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=insertadas + cerradas,
                rows_skipped=sum(skips.values()),
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        # El with de arriba ya hizo rollback del trabajo; la run abierta se
        # sella como fallo si la conexion sigue viva (best-effort). BaseException
        # (no solo Exception): un Ctrl-C tambien deja la run sellada, no abierta.
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_written=0,
                    rows_skipped=0,
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo; "
                "el error original de la corrida era: %s",
                run_id,
                scrub(str(exc)),
            )
        raise

    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=insertadas + cerradas,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        productos_nuevos=productos_nuevos,
        productos_actualizados=productos_actualizados,
        nombres_derivados=nombres_derivados,
        vigencias_insertadas=insertadas,
        vigencias_cerradas=cerradas,
        filas_origen=stats["filas"],
        vigencias_finales=stats["vigencias"],
        segmentos_intradia_colapsados=stats["segmentos_intradia_colapsados"],
        segmentos_intradia_en_borde=stats["segmentos_intradia_en_borde"],
        fusiones=stats["fusiones"],
        convergencias=convergencias,
        sin_costo_en_origen=sin_costo_en_origen,
        detalle=tuple(detalle),
    )


def _sellar_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    rows_written: int,
    rows_skipped: int,
    skip_reason: str | None,
) -> None:
    conn.execute(_SQL_SELLAR_RUN, (rows_written, rows_skipped, skip_reason, ok, run_id))


# ---------------------------------------------------------------------------
# main del pipeline (el CLI despacha aqui; cero logica de argparse en el CLI)
# ---------------------------------------------------------------------------


# Telegram corta cerca de 4096 caracteres: un cambio masivo no puede perder el
# aviso entero. Se listan los primeros y se resume el resto.
_MAX_DETALLES_AVISO = 20
_MAX_TEXTO_AVISO = 3900


def _texto_aviso_costos(detalle: tuple[str, ...]) -> str:
    """Texto unico del aviso de convergencias y cierres por falta de costo."""
    visibles = detalle[:_MAX_DETALLES_AVISO]
    resto = len(detalle) - len(visibles)
    cuerpo = "; ".join(visibles)
    if resto > 0:
        cuerpo += f"; y {resto} mas"
    texto = (
        f"Costos: {len(detalle)} avisos: {cuerpo}."
        " Orbit sigue al origen sin reescribir lo ya publicado."
    )
    if len(texto) > _MAX_TEXTO_AVISO:
        texto = texto[: _MAX_TEXTO_AVISO - 3] + "..."
    return texto


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest costs",
        description=(
            "Ingresa productos y costos desde un snapshot read-only de la SQLite"
            " de contabilidad (runbook: docs/DEPLOY.md, 'Ingesta de costos')."
        ),
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="ruta del snapshot (producido con la API .backup(); ver runbook)",
    )
    parser.add_argument(
        "--acepto-cierre-masivo",
        action="store_true",
        help=(
            "permite seguir cuando mas del 10 %% de los productos quedaria sin"
            " costo (ausentes del origen o sin vigencia publicable); solo si la"
            " copia de contabilidad esta completa"
        ),
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir costos (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(
            f"el snapshot no existe: {ruta} (runbook: docs/DEPLOY.md, ingesta de costos)",
            file=sys.stderr,
        )
        return 2
    corte = dt.datetime.now(dt.UTC).date()
    try:
        conn = connect(dsn)
        try:
            resultado = sync_costos(
                conn,
                ruta,
                corte=corte,
                acepto_cierre_masivo=args.acepto_cierre_masivo,
            )
        finally:
            conn.close()
    except Exception as exc:
        print(
            "ingesta de costos fallo (la ingest_run quedo sellada ok=false cuando"
            f" fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1

    print(f"== Ingesta de costos desde contabilidad ({SOURCE}) ==")
    print(
        f"run_id={resultado.run_id} ok={resultado.ok}"
        f" rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped}"
    )
    print(
        f"productos: nuevos={resultado.productos_nuevos}"
        f" actualizados={resultado.productos_actualizados}"
        f" nombres_derivados={resultado.nombres_derivados}"
    )
    print(f"corte={corte.isoformat()} (el dia de hoy queda para manana: D2, ultimo valor del dia)")
    print(
        f"vigencias: insertadas={resultado.vigencias_insertadas}"
        f" cerradas={resultado.vigencias_cerradas}"
        f" finales={resultado.vigencias_finales} (filas origen:"
        f" {resultado.filas_origen})"
    )
    convergencias = f" ({'; '.join(resultado.detalle)})" if resultado.detalle else ""
    print(f"convergencias={resultado.convergencias}{convergencias}")
    print(f"sin_costo_en_origen={resultado.sin_costo_en_origen}")
    # Evidencia del colapso (hallazgo 3 del adversario + revision): los dias
    # intradia en el borde PIERDEN su costo y tienen que ser visibles, igual
    # que el ruido colapsado y las fusiones — un contador que no se ve no cuenta.
    print(
        f"colapso: intradia_con_sucesor={resultado.segmentos_intradia_colapsados}"
        f" intradia_en_borde={resultado.segmentos_intradia_en_borde}"
        f" fusiones={resultado.fusiones}"
    )
    if resultado.skip_reason:
        print(f"skips: {resultado.skip_reason}")
    if resultado.detalle:
        envia_aviso(_texto_aviso_costos(resultado.detalle))
    return 0
