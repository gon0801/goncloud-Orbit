# ruff: noqa
"""Conciliacion de solo lectura: Orbit (Postgres desechable) vs accounting.

Uso: CONCILIAR_DSN=postgresql://... python conciliar.py <snapshot.db> \
       --desde YYYY-MM-DD --hasta YYYY-MM-DD

La ventana [desde, hasta] es explicita: no se deriva de las campanas del
snapshot, para que un dia perdido no desplace el inicio. La actividad anterior
a `desde` se excluye y se reporta; la posterior a `hasta` falla, con UNA
excepcion: el control diario de HOY (T+0: accounting ya publico el total,
el desglose de campanas de hoy todavia no existe) se excluye y se reporta
cuando la ventana cerrada termina antes de hoy. Un NULL de HOY sigue
fallando, y con `hasta` incluyendo a HOY el control de hoy no tiene
exencion. El DSN va por entorno (nunca en argv) y los errores de conexion
se redactan.

Sale distinto de cero ante CUALQUIER diferencia: una campana/dia que falta o
sobra en Orbit, un dia completo que falta o sobra, un NULL (aunque coincida),
una metrica con otro valor o una moneda distinta de MXN. No hay una salida
"parcial" que imprima CONCILIACION OK.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

try:
    from app.redaction import redact_dsn, scrub
except ModuleNotFoundError:
    # `python ruta/al/script.py` pone en sys.path el directorio del script,
    # no la raiz del repo. La evidencia vive siempre a esta profundidad.
    sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
    from app.redaction import redact_dsn, scrub

CENTIMOS = Decimal("0.0001")
MAX_DINERO = Decimal(10) ** 10
_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DatoInvalido(Exception):
    """Un importe del origen no cumple la disciplina de dinero."""


def _tolerancia_binaria(valor) -> Decimal:
    """Residuo maximo atribuible al REAL de SQLite (hasta 4 ulps)."""
    if isinstance(valor, float):
        return Decimal(str(math.ulp(valor))) * 4
    return Decimal(0)


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
    residuo = abs(numero - cuantizado)
    if residuo:
        if numero > 0 and cuantizado == 0:
            raise DatoInvalido(f"{campo} positivo que cuantiza a cero: {valor!r}")
        if residuo > _tolerancia_binaria(valor):
            raise DatoInvalido(f"{campo} con mas de 4 decimales: {valor!r}")
    if cuantizado >= MAX_DINERO:
        raise DatoInvalido(f"{campo} fuera de NUMERIC(14,4): {valor!r}")
    return Decimal("0.0000") if cuantizado == 0 else cuantizado


def _fecha_origen(valor, etiqueta: str) -> str | None:
    """Valida una fecha ISO antes de usarla como clave o compararla."""
    if not isinstance(valor, str) or not _FECHA_RE.match(valor):
        return f"fecha ilegible en origen {etiqueta}: {valor!r}"
    try:
        dt.date.fromisoformat(valor)
    except ValueError:
        return f"fecha ilegible en origen {etiqueta}: {valor!r}"
    return None


def _fecha_ventana(texto: str) -> dt.date | None:
    if not _FECHA_RE.match(texto):
        return None
    try:
        return dt.date.fromisoformat(texto)
    except ValueError:
        return None


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
                campana = row["campaign_id"]
                if not isinstance(campana, str) or not campana.strip():
                    errores.append(f"campaign_id ilegible en origen: {campana!r}")
                    continue
                campana = campana.strip()
                etiqueta = f"{campana} {row['date']}"
                if "currency" in columnas:
                    error_moneda = _moneda_origen(row["currency"], etiqueta)
                    if error_moneda:
                        errores.append(error_moneda)
                error_fecha = _fecha_origen(row["date"], etiqueta)
                if error_fecha:
                    errores.append(error_fecha)
                    continue
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
                clave = (campana, row["date"])
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
                error_fecha = _fecha_origen(row["date"], etiqueta)
                if error_fecha:
                    errores.append(error_fecha)
                    continue
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
        if not isinstance(ext, str) or not ext.strip():
            errores.append(f"identidad ilegible en Orbit: {ext!r}")
            continue
        ext = ext.strip()
        if not isinstance(fecha, dt.date):
            errores.append(f"fecha ilegible en Orbit para {ext}: {fecha!r}")
            continue
        if moneda != "MXN":
            errores.append(f"moneda fuera de dominio en {ext} {fecha}: {moneda}")
        clave = (ext, fecha.isoformat())
        valores = (cost, revenue, impressions, clicks)
        if clave in orbit:
            errores.append(f"duplicado en Orbit campana/dia {clave}: {orbit[clave]} vs {valores}")
            continue
        orbit[clave] = valores
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


def _compara_ventana(
    origen: dict,
    dias: dict,
    orbit: dict,
    desde: dt.date,
    hasta: dt.date,
) -> list[str]:
    """Devuelve TODAS las diferencias de la ventana explicita [desde, hasta]."""
    errores: list[str] = []
    desde_txt, hasta_txt = desde.isoformat(), hasta.isoformat()

    def _con_actividad(valores) -> bool:
        return any(valor is not None and valor != 0 for valor in valores)

    def _comparable(valores) -> bool:
        # Un NULL no es inactividad: no se puede probar que el dia este en
        # cero. La fila incompleta entra a la ventana y falla cerrada.
        return _con_actividad(valores) or any(valor is None for valor in valores)

    origen_ventana: dict = {}
    for clave, valores in origen.items():
        if desde_txt <= clave[1] <= hasta_txt:
            origen_ventana[clave] = valores
        else:
            errores.append(f"campana/dia fuera de la ventana declarada {clave}: {valores}")

    for fecha, valores in sorted(dias.items()):
        if fecha > hasta_txt and _comparable(valores):
            errores.append(f"control diario fuera de la ventana declarada {fecha}: {valores}")
    historia = sum(
        1 for fecha, valores in dias.items() if fecha < desde_txt and _comparable(valores)
    )
    if historia:
        print(f"historia anterior a --desde excluida: {historia} dias con actividad o NULL")

    faltantes = sorted(set(origen_ventana) - set(orbit))
    sobrantes = sorted(set(orbit) - set(origen_ventana))
    for clave in faltantes:
        errores.append(f"FALTA en Orbit {clave}: origen={origen_ventana[clave]}")
    for clave in sobrantes:
        errores.append(f"SOBRA en Orbit {clave}: orbit={orbit[clave]}")

    diferencias = 0
    for clave in sorted(set(origen_ventana) & set(orbit)):
        if origen_ventana[clave] != orbit[clave]:
            diferencias += 1
            errores.append(f"DIFIERE {clave}: origen={origen_ventana[clave]} orbit={orbit[clave]}")
        elif any(valor is None for valor in origen_ventana[clave]) or any(
            valor is None for valor in orbit[clave]
        ):
            errores.append(
                f"NULL no conciliable en campana/dia {clave}:"
                f" origen={origen_ventana[clave]} orbit={orbit[clave]}"
            )

    # Dentro de la ventana, un dia de campanas sin control diario no se puede
    # probar. Tambien entra cualquier dia con actividad o NULL, aunque no
    # tenga campanas: es un hueco de dinero o un dato desconocido.
    dias_ventana = {
        fecha: valores for fecha, valores in dias.items() if desde_txt <= fecha <= hasta_txt
    }
    fechas_campana = {fecha for (_ext, fecha) in origen_ventana}
    if fechas_campana:
        dias_control = {
            fecha: valores
            for fecha, valores in dias_ventana.items()
            if fecha in fechas_campana or _comparable(valores)
        }
    else:
        dias_control = {
            fecha: valores for fecha, valores in dias_ventana.items() if _comparable(valores)
        }
    control_faltante = sorted(fechas_campana - set(dias_ventana))
    for fecha in control_faltante:
        errores.append(f"FALTA el dia {fecha} en el control diario del origen")

    orbit_ventana = {
        clave: valores for clave, valores in orbit.items() if desde_txt <= clave[1] <= hasta_txt
    }
    orbit_dias = _agrega_dias(orbit_ventana)
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
        elif any(valor is None for valor in dias_control[fecha]) or any(
            valor is None for valor in orbit_dias[fecha]
        ):
            errores.append(
                f"NULL no conciliable en el dia {fecha}:"
                f" origen={dias_control[fecha]} orbit={orbit_dias[fecha]}"
            )

    print(f"filas origen: {len(origen)}")
    print(f"filas Orbit:  {len(orbit)}")
    print(f"faltantes en Orbit: {faltantes[:10]}")
    print(f"sobrantes en Orbit: {sobrantes[:10]}")
    print(
        f"campana/dia comparables: {len(set(origen_ventana) & set(orbit))},"
        f" diferencias: {diferencias}"
    )
    print(
        f"dias control: {len(dias_control)}, dias Orbit: {len(orbit_dias)},"
        f" faltantes en el control: {len(control_faltante)},"
        f" faltantes en Orbit: {len(dias_faltantes)},"
        f" sobrantes en Orbit: {len(dias_sobrantes)},"
        f" diferencias: {dias_diferentes}"
    )
    return errores


def compara(
    origen: dict,
    dias: dict,
    orbit: dict,
    desde: dt.date,
    hasta: dt.date,
) -> list[str]:
    """Concilia la ventana cerrada y valida T+0 de forma simetrica."""
    hoy = dt.datetime.now(dt.UTC).date()
    if hasta >= hoy:
        return _compara_ventana(origen, dias, orbit, desde, hasta)

    hoy_txt = hoy.isoformat()
    origen_t0 = {clave: valores for clave, valores in origen.items() if clave[1] == hoy_txt}
    orbit_t0 = {clave: valores for clave, valores in orbit.items() if clave[1] == hoy_txt}
    dias_t0 = {fecha: valores for fecha, valores in dias.items() if fecha == hoy_txt}
    origen_cerrado = {clave: valores for clave, valores in origen.items() if clave[1] != hoy_txt}
    orbit_cerrado = {clave: valores for clave, valores in orbit.items() if clave[1] != hoy_txt}
    dias_cerrados = {fecha: valores for fecha, valores in dias.items() if fecha != hoy_txt}
    errores = _compara_ventana(origen_cerrado, dias_cerrados, orbit_cerrado, desde, hasta)

    if origen_t0 or orbit_t0:
        print("T+0 con desglose: conciliando campanas y control de HOY")
        return errores + _compara_ventana(origen_t0, dias_t0, orbit_t0, hoy, hoy)

    errores_t0: list[str] = []
    for fecha, valores in sorted(dias_t0.items()):
        if any(valor is None for valor in valores):
            errores_t0.append(f"NULL no conciliable en el control T+0 {fecha}: {valores}")
    if dias_t0 and not errores_t0:
        print("control T+0 excluido: 1 dia de HOY sin desglose de campanas en origen ni Orbit")
    return errores + errores_t0


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if len(argumentos) != 5 or argumentos[1] != "--desde" or argumentos[3] != "--hasta":
        print(
            "uso: conciliar.py <snapshot.db> --desde YYYY-MM-DD --hasta YYYY-MM-DD"
            " (DSN en CONCILIAR_DSN)",
            file=sys.stderr,
        )
        return 2
    desde = _fecha_ventana(argumentos[2])
    hasta = _fecha_ventana(argumentos[4])
    if desde is None or hasta is None or desde > hasta:
        print("ventana invalida: --desde <= --hasta, fechas ISO", file=sys.stderr)
        return 2
    dsn = os.environ.get("CONCILIAR_DSN", "").strip()
    if not dsn:
        print("falta CONCILIAR_DSN en el entorno", file=sys.stderr)
        return 2
    redact_dsn(dsn)  # registra la password para que scrub() la borre
    ruta = argumentos[0]

    origen, dias, errores_origen = carga_origen(ruta)
    try:
        with psycopg.connect(dsn) as conn:
            orbit, errores_orbit = carga_orbit(conn)
    except Exception as exc:  # noqa: BLE001 - la conciliacion falla cerrada
        print(f"no se pudo leer Orbit: {scrub(str(exc))}", file=sys.stderr)
        return 1

    errores = errores_origen + errores_orbit + compara(origen, dias, orbit, desde, hasta)
    print(f"fecha de conciliacion: {dt.datetime.now(dt.UTC).isoformat()}")
    print(f"ventana: {desde.isoformat()}..{hasta.isoformat()}")
    if errores:
        for error in errores:
            print(error)
        print("CONCILIACION FALLA")
        return 1
    print("CONCILIACION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
