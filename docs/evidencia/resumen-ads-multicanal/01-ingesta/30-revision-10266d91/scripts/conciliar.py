# ruff: noqa
"""Conciliacion de solo lectura: Orbit (Postgres desechable) vs accounting.

Uso: python conciliar.py <snapshot.db> <dsn_postgres_desechable>

Sale distinto de cero ante CUALQUIER diferencia: una campana/dia que falta o
sobra en Orbit, un dia completo que falta o sobra, un NULL convertido en cero,
una metrica con otro valor o una moneda distinta de MXN. No hay una salida
"parcial" que imprima CONCILIACION OK.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

CENTIMOS = Decimal("0.0001")
RUIDO = Decimal("0.00001")
MAX_DINERO = Decimal(10) ** 10


class DatoInvalido(Exception):
    """Un importe del origen no cumple la disciplina de dinero."""


def _dinero(valor, campo="importe"):
    """Dinero exacto: no redondea precision genuina ni convierte NULL en cero."""
    if valor is None:
        return None
    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        raise DatoInvalido(f"{campo} ilegible: {valor!r}") from None
    if not numero.is_finite():
        raise DatoInvalido(f"{campo} no finito: {valor!r}")
    if numero < 0:
        raise DatoInvalido(f"{campo} negativo: {valor!r}")
    try:
        cuantizado = numero.quantize(CENTIMOS)
    except InvalidOperation:
        raise DatoInvalido(f"{campo} fuera de NUMERIC(14,4): {valor!r}") from None
    if abs(numero - cuantizado) >= RUIDO:
        raise DatoInvalido(f"{campo} con mas de 4 decimales: {valor!r}")
    if cuantizado >= MAX_DINERO:
        raise DatoInvalido(f"{campo} fuera de NUMERIC(14,4): {valor!r}")
    if numero > 0 and cuantizado == 0:
        raise DatoInvalido(f"{campo} positivo que cuantiza a cero: {valor!r}")
    return Decimal("0.0000") if cuantizado == 0 else cuantizado


def _columnas(con, tabla: str) -> set[str]:
    return {fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")}


def _moneda_origen(valor, etiqueta: str) -> str | None:
    if valor is None:
        return None
    moneda = str(valor).strip().upper()
    if moneda != "MXN":
        return f"moneda fuera de dominio en origen {etiqueta}: {valor!r}"
    return None


def carga_origen(ruta: str) -> tuple[dict, dict, list[str]]:
    """Lee campana/dia y total diario del snapshot. Nunca escribe.

    Rechaza moneda distinta de MXN, precision genuina de mas de 4 decimales y
    claves duplicadas. Una clave repetida NO se sobrescribe: se reporta.
    """
    errores: list[str] = []
    origen: dict = {}
    dias: dict = {}
    uri = f"{Path(ruta).resolve().as_uri()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            columnas = _columnas(con, "meli_ads_campaigns_daily")
            consulta = (
                "SELECT campaign_id, date, spend, ad_sales, impressions, clicks"
                " FROM meli_ads_campaigns_daily"
            )
            if "currency" in columnas:
                consulta = consulta.replace("clicks", "clicks, currency")
            for row in con.execute(consulta):
                etiqueta = f"{row['campaign_id']} {row['date']}"
                if "currency" in columnas:
                    error_moneda = _moneda_origen(row["currency"], etiqueta)
                    if error_moneda:
                        errores.append(error_moneda)
                try:
                    valores = (
                        _dinero(row["spend"], f"spend {etiqueta}"),
                        _dinero(row["ad_sales"], f"ad_sales {etiqueta}"),
                        row["impressions"],
                        row["clicks"],
                    )
                except DatoInvalido as exc:
                    errores.append(str(exc))
                    continue
                clave = (row["campaign_id"], row["date"])
                if clave in origen:
                    errores.append(
                        f"duplicado en origen campana/dia {clave}: {origen[clave]} vs {valores}"
                    )
                    continue
                origen[clave] = valores

            columnas_dias = _columnas(con, "meli_ads_metrics_daily")
            consulta_dias = (
                "SELECT date, clicks, impressions, spend, ad_sales FROM meli_ads_metrics_daily"
            )
            if "currency" in columnas_dias:
                consulta_dias = consulta_dias.replace("ad_sales", "ad_sales, currency")
            for row in con.execute(consulta_dias):
                etiqueta = f"control diario {row['date']}"
                if "currency" in columnas_dias:
                    error_moneda = _moneda_origen(row["currency"], etiqueta)
                    if error_moneda:
                        errores.append(error_moneda)
                try:
                    valores = (
                        row["clicks"],
                        row["impressions"],
                        _dinero(row["spend"], f"spend {etiqueta}"),
                        _dinero(row["ad_sales"], f"ad_sales {etiqueta}"),
                    )
                except DatoInvalido as exc:
                    errores.append(str(exc))
                    continue
                clave = row["date"]
                if clave in dias:
                    errores.append(
                        f"duplicado en origen control diario {clave}: {dias[clave]} vs {valores}"
                    )
                    continue
                dias[clave] = valores
        finally:
            con.close()
    except sqlite3.Error as exc:
        errores.append(f"snapshot ilegible: {exc}")
    return origen, dias, errores


def carga_orbit(conn) -> tuple[dict, list[str]]:
    """Lee la observacion vigente por campana/dia. Solo SELECT."""
    errores: list[str] = []
    filas = conn.execute(
        "SELECT e.external_id, l.metric_date, l.cost, l.ad_revenue, l.impressions,"
        " l.clicks, l.metric_currency"
        " FROM v_metric_latest l JOIN ad_entity e ON e.id = l.ad_entity_id"
        " WHERE e.platform = 'meli' AND e.kind = 'campaign'"
    ).fetchall()
    orbit: dict = {}
    for ext, fecha, cost, revenue, impressions, clicks, moneda in filas:
        if moneda != "MXN":
            errores.append(f"moneda fuera de dominio en {ext} {fecha}: {moneda}")
        orbit[(ext, fecha.isoformat())] = (cost, revenue, impressions, clicks)
    return orbit, errores


def _agrega_dias(orbit: dict) -> dict:
    sumas: dict = {}
    for (_ext, fecha), (cost, revenue, impressions, clicks) in orbit.items():
        dia = sumas.setdefault(
            fecha,
            {
                "clicks": 0,
                "clicks_null": False,
                "impressions": 0,
                "impressions_null": False,
                "cost": Decimal(0),
                "cost_null": False,
                "revenue": Decimal(0),
                "revenue_null": False,
            },
        )
        if clicks is None:
            dia["clicks_null"] = True
        else:
            dia["clicks"] += clicks
        if impressions is None:
            dia["impressions_null"] = True
        else:
            dia["impressions"] += impressions
        if cost is None:
            dia["cost_null"] = True
        else:
            dia["cost"] += cost
        if revenue is None:
            dia["revenue_null"] = True
        else:
            dia["revenue"] += revenue
    return {
        fecha: (
            None if dia["clicks_null"] else dia["clicks"],
            None if dia["impressions_null"] else dia["impressions"],
            None if dia["cost_null"] else dia["cost"],
            None if dia["revenue_null"] else dia["revenue"],
        )
        for fecha, dia in sumas.items()
    }


def compara(origen: dict, dias: dict, orbit: dict) -> list[str]:
    """Devuelve TODAS las diferencias; lista vacia solo si son identicos."""
    errores: list[str] = []
    faltantes = sorted(set(origen) - set(orbit))
    sobrantes = sorted(set(orbit) - set(origen))
    for clave in faltantes:
        errores.append(f"FALTA en Orbit {clave}: origen={origen[clave]}")
    for clave in sobrantes:
        errores.append(f"SOBRA en Orbit {clave}: orbit={orbit[clave]}")

    diferencias = 0
    for clave in sorted(set(origen) & set(orbit)):
        if origen[clave] != orbit[clave]:
            diferencias += 1
            errores.append(f"DIFIERE {clave}: origen={origen[clave]} orbit={orbit[clave]}")

    # El control diario se acota al inicio de la ventana de campanas: la tabla
    # diaria puede traer historia anterior al primer dia con campanas, y esos
    # dias no son comparables campana/dia. Desde el primer dia de campanas SI
    # se incluye cualquier dia con actividad, aunque no tenga filas de campana:
    # es un hueco de dinero que el sync puede producir si el desglose falla o si
    # el dia quedo despues de la ultima campana. Un dia en cero y sin campanas
    # no es una diferencia monetaria. Un dia de campanas sin control diario no
    # se puede probar y tambien falla. Si no hay campanas, cualquier dia con
    # actividad del control es un hueco.
    fechas_campana = {fecha for (_ext, fecha) in origen}

    def _con_actividad(valores) -> bool:
        return any(valor is not None and valor != 0 for valor in valores)

    if fechas_campana:
        inicio = min(fechas_campana)
        dias_control = {
            fecha: valores
            for fecha, valores in dias.items()
            if fecha >= inicio and (fecha in fechas_campana or _con_actividad(valores))
        }
    else:
        dias_control = {
            fecha: valores for fecha, valores in dias.items() if _con_actividad(valores)
        }
    control_faltante = sorted(fechas_campana - set(dias))
    for fecha in control_faltante:
        errores.append(f"FALTA el dia {fecha} en el control diario del origen")

    orbit_dias = _agrega_dias(orbit)
    dias_faltantes = sorted(set(dias_control) - set(orbit_dias))
    dias_sobrantes = sorted(set(orbit_dias) - set(dias_control))
    for fecha in dias_faltantes:
        errores.append(f"FALTA el dia {fecha} en Orbit: origen={dias_control[fecha]}")
    for fecha in dias_sobrantes:
        errores.append(f"SOBRA el dia {fecha} en Orbit: orbit={orbit_dias[fecha]}")

    dias_diferentes = 0
    for fecha in sorted(set(dias_control) & set(orbit_dias)):
        if dias_control[fecha] != orbit_dias[fecha]:
            dias_diferentes += 1
            errores.append(
                f"DIA DIFIERE {fecha}: origen={dias_control[fecha]} orbit={orbit_dias[fecha]}"
            )

    print(f"filas origen: {len(origen)}")
    print(f"filas Orbit:  {len(orbit)}")
    print(f"faltantes en Orbit: {faltantes[:10]}")
    print(f"sobrantes en Orbit: {sobrantes[:10]}")
    print(f"campana/dia comparables: {len(set(origen) & set(orbit))}, diferencias: {diferencias}")
    print(
        f"dias control: {len(dias_control)}, dias Orbit: {len(orbit_dias)},"
        f" faltantes en el control: {len(control_faltante)},"
        f" faltantes en Orbit: {len(dias_faltantes)},"
        f" sobrantes en Orbit: {len(dias_sobrantes)},"
        f" diferencias: {dias_diferentes}"
    )
    return errores


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if len(argumentos) != 2:
        print("uso: conciliar.py <snapshot.db> <dsn_postgres_desechable>", file=sys.stderr)
        return 2
    ruta, dsn = argumentos

    origen, dias, errores_origen = carga_origen(ruta)
    try:
        with psycopg.connect(dsn) as conn:
            orbit, errores_orbit = carga_orbit(conn)
    except Exception as exc:  # noqa: BLE001 - la conciliacion falla cerrada
        print(f"no se pudo leer Orbit: {exc}", file=sys.stderr)
        return 1

    errores = errores_origen + errores_orbit + compara(origen, dias, orbit)
    print(f"fecha de conciliacion: {dt.datetime.now(dt.UTC).isoformat()}")
    if errores:
        for error in errores:
            print(error)
        print("CONCILIACION FALLA")
        return 1
    print("CONCILIACION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
