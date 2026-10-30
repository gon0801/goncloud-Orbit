"""Regresiones de la conciliacion MeLi vs accounting (RESUMEN-ADS-01).

La conciliacion es un gate de dinero: cualquier campana/dia o dia que falte o
sobre, un NULL convertido en cero, una metrica distinta o una moneda que no
sea MXN debe salir distinto de cero. Se ejecuta el script REAL de evidencia
(no una copia del algoritmo) contra SQLite real y un Postgres simulado.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = (
    RAIZ
    / "docs/evidencia/resumen-ads-multicanal/01-ingesta"
    / "30-revision-10266d91/scripts/conciliar.py"
)
DIA = dt.date(2026, 10, 28)


def _carga_script():
    spec = importlib.util.spec_from_file_location("conciliar_meli_ads_test", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


class _Resultado:
    def __init__(self, filas):
        self.filas = filas

    def fetchall(self):
        return self.filas


class _Conexion:
    def __init__(self, filas):
        self.filas = filas

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, consulta):
        assert "v_metric_latest" in consulta
        return _Resultado(self.filas)


def _snapshot(
    tmp_path: Path,
    campanas,
    dias,
    *,
    con_moneda: bool = False,
    moneda: str = "MXN",
) -> Path:
    ruta = tmp_path / "source.db"
    ruta.unlink(missing_ok=True)
    con = sqlite3.connect(ruta)
    columnas = (
        "campaign_id TEXT, date TEXT, spend REAL, ad_sales REAL,"
        " impressions INTEGER, clicks INTEGER"
    )
    if con_moneda:
        columnas += ", currency TEXT"
    con.execute(f"CREATE TABLE meli_ads_campaigns_daily ({columnas})")
    con.execute(
        "CREATE TABLE meli_ads_metrics_daily (date TEXT, clicks INTEGER,"
        " impressions INTEGER, spend REAL, ad_sales REAL)"
    )
    if con_moneda:
        con.executemany(
            "INSERT INTO meli_ads_campaigns_daily VALUES (?,?,?,?,?,?,?)",
            [(*fila, moneda) for fila in campanas],
        )
    else:
        con.executemany("INSERT INTO meli_ads_campaigns_daily VALUES (?,?,?,?,?,?)", campanas)
    con.executemany("INSERT INTO meli_ads_metrics_daily VALUES (?,?,?,?,?)", dias)
    con.commit()
    con.close()
    return ruta


def _corre(
    monkeypatch,
    tmp_path,
    campanas,
    dias,
    orbit,
    capsys,
    *,
    con_moneda=False,
    moneda="MXN",
    desde=None,
    hasta=None,
):
    modulo = _carga_script()
    monkeypatch.setattr(modulo.psycopg, "connect", lambda _dsn: _Conexion(orbit))
    ruta = _snapshot(tmp_path, campanas, dias, con_moneda=con_moneda, moneda=moneda)
    fechas = (
        [fila[1] for fila in campanas]
        + [fila[0] for fila in dias]
        + [fila[1].isoformat() for fila in orbit]
    )
    validas = []
    for fecha in fechas:
        try:
            dt.date.fromisoformat(fecha)
        except (TypeError, ValueError):
            continue
        validas.append(fecha)
    desde = desde or (min(validas) if validas else DIA.isoformat())
    hasta = hasta or (max(validas) if validas else DIA.isoformat())
    monkeypatch.setenv("CONCILIAR_DSN", "postgresql:///desechable")
    codigo = modulo.main([str(ruta), "--desde", desde, "--hasta", hasta])
    return codigo, capsys.readouterr().out


def _campana(spend=1.0, ad_sales=2.0, impressions=3, clicks=4):
    return [("C1", DIA.isoformat(), spend, ad_sales, impressions, clicks)]


def _dia(spend=1.0, ad_sales=2.0, impressions=3, clicks=4):
    return [(DIA.isoformat(), clicks, impressions, spend, ad_sales)]


def _orbit(spend=1.0, ad_sales=2.0, impressions=3, clicks=4, moneda="MXN"):
    def dinero(valor):
        return None if valor is None else Decimal(str(valor)).quantize(Decimal("0.0001"))

    return [
        (
            "C1",
            DIA,
            dinero(spend),
            dinero(ad_sales),
            impressions,
            clicks,
            moneda,
        )
    ]


def test_conciliacion_positiva_acepta_datos_identicos(monkeypatch, tmp_path, capsys):
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), _dia(), _orbit(), capsys)
    assert codigo == 0
    assert "CONCILIACION OK" in salida


def test_campana_dia_que_falta_en_orbit_falla(monkeypatch, tmp_path, capsys):
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), _dia(), [], capsys)
    assert codigo != 0
    assert "FALTA en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_campana_dia_que_sobra_en_orbit_falla(monkeypatch, tmp_path, capsys):
    codigo, salida = _corre(monkeypatch, tmp_path, [], [], _orbit(), capsys)
    assert codigo != 0
    assert "SOBRA en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_dia_completo_que_falta_en_orbit_falla(monkeypatch, tmp_path, capsys):
    siguiente = (DIA + dt.timedelta(days=1)).isoformat()
    campanas = _campana() + [("C1", siguiente, 1.0, 1.0, 1, 1)]
    dias = _dia() + [(siguiente, 1, 1, 1.0, 1.0)]
    codigo, salida = _corre(monkeypatch, tmp_path, campanas, dias, _orbit(), capsys)
    assert codigo != 0
    assert f"FALTA el dia {siguiente} en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_dia_completo_que_sobra_en_orbit_falla(monkeypatch, tmp_path, capsys):
    siguiente = DIA + dt.timedelta(days=1)
    orbit = _orbit() + [
        (
            "C1",
            siguiente,
            Decimal("1.0000"),
            Decimal("1.0000"),
            1,
            1,
            "MXN",
        )
    ]
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), _dia(), orbit, capsys)
    assert codigo != 0
    assert f"SOBRA el dia {siguiente.isoformat()} en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_dia_intermedio_con_gasto_y_sin_campanas_falla(monkeypatch, tmp_path, capsys):
    """Un dia interior con gasto en el control y cero campanas es un hueco de
    dinero: no se puede limitar el control a las fechas que ya tienen campana."""
    dia2 = DIA + dt.timedelta(days=1)
    dia3 = DIA + dt.timedelta(days=2)
    campanas = [
        ("C1", DIA.isoformat(), 10.0, 20.0, 3, 4),
        ("C1", dia3.isoformat(), 10.0, 20.0, 3, 4),
    ]
    dias = [
        (DIA.isoformat(), 4, 3, 10.0, 20.0),
        (dia2.isoformat(), 9, 8, 500.0, 900.0),
        (dia3.isoformat(), 4, 3, 10.0, 20.0),
    ]
    orbit = [
        ("C1", DIA, Decimal("10.0000"), Decimal("20.0000"), 3, 4, "MXN"),
        ("C1", dia3, Decimal("10.0000"), Decimal("20.0000"), 3, 4, "MXN"),
    ]
    codigo, salida = _corre(monkeypatch, tmp_path, campanas, dias, orbit, capsys)
    assert codigo != 0
    assert f"FALTA el dia {dia2.isoformat()} en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_dia_posterior_a_la_ultima_campana_con_gasto_falla(monkeypatch, tmp_path, capsys):
    """Un dia despues de la ultima campana con gasto no puede quedar fuera."""
    siguiente = DIA + dt.timedelta(days=1)
    dias = _dia() + [(siguiente.isoformat(), 9, 8, 500.0, 900.0)]
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), dias, _orbit(), capsys)
    assert codigo != 0
    assert f"FALTA el dia {siguiente.isoformat()} en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_origen_sin_campanas_con_gasto_falla(monkeypatch, tmp_path, capsys):
    """Si no hay campanas pero el control diario tiene gasto, es un hueco."""
    codigo, salida = _corre(
        monkeypatch, tmp_path, [], [(DIA.isoformat(), 9, 8, 500.0, 900.0)], [], capsys
    )
    assert codigo != 0
    assert f"FALTA el dia {DIA.isoformat()} en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_control_diario_incompleto_falla(monkeypatch, tmp_path, capsys):
    """Un dia de campanas sin fila en el control diario no se puede probar."""
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), [], _orbit(), capsys)
    assert codigo != 0
    assert f"FALTA el dia {DIA.isoformat()} en el control diario" in salida
    assert "CONCILIACION OK" not in salida


def test_null_convertido_en_cero_falla(monkeypatch, tmp_path, capsys):
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        _campana(spend=None, ad_sales=None, impressions=None, clicks=None),
        _dia(spend=None, ad_sales=None, impressions=None, clicks=None),
        _orbit(spend=0.0, ad_sales=0.0, impressions=0, clicks=0),
        capsys,
    )
    assert codigo != 0
    assert "DIFIERE" in salida
    assert "CONCILIACION OK" not in salida


def test_metrica_con_otro_valor_falla(monkeypatch, tmp_path, capsys):
    codigo, salida = _corre(
        monkeypatch, tmp_path, _campana(spend=1.0), _dia(spend=1.0), _orbit(spend=1.0001), capsys
    )
    assert codigo != 0
    assert "DIFIERE" in salida
    assert "CONCILIACION OK" not in salida


def test_moneda_distinta_de_mxn_falla(monkeypatch, tmp_path, capsys):
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), _dia(), _orbit(moneda="USD"), capsys)
    assert codigo != 0
    assert "moneda fuera de dominio" in salida
    assert "CONCILIACION OK" not in salida


def test_orbit_vacio_no_imprime_ok_ni_sale_cero(monkeypatch, tmp_path, capsys):
    """El repro del revisor: Orbit vacio con origen no vacio no puede pasar."""
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), _dia(), [], capsys)
    assert codigo == 1
    assert "CONCILIACION OK" not in salida
    assert "CONCILIACION FALLA" in salida


def test_moneda_distinta_en_el_origen_falla(monkeypatch, tmp_path, capsys):
    """La tabla de origen puede traer currency: USD no puede pasar como MXN."""
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        _campana(),
        _dia(),
        _orbit(),
        capsys,
        con_moneda=True,
        moneda="USD",
    )
    assert codigo != 0
    assert "moneda fuera de dominio en origen" in salida
    assert "CONCILIACION OK" not in salida


def test_precision_del_origen_no_se_redondea(monkeypatch, tmp_path, capsys):
    """1.00001 tiene cinco decimales genuinos: no se redondea a 1.0000."""
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        [("C1", DIA.isoformat(), 1.00001, 2.0, 3, 4)],
        _dia(),
        _orbit(),
        capsys,
    )
    assert codigo != 0
    assert "mas de 4 decimales" in salida
    assert "CONCILIACION OK" not in salida


def test_precision_genuina_de_seis_decimales_falla(monkeypatch, tmp_path, capsys):
    """1.000001 es precision decimal real, no ruido binario del REAL."""
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        [("C1", DIA.isoformat(), 1.000001, 2.0, 3, 4)],
        _dia(spend=1.000001),
        _orbit(),
        capsys,
    )
    assert codigo != 0
    assert "mas de 4 decimales" in salida
    assert "CONCILIACION OK" not in salida


def test_control_con_spend_null_en_dia_sin_campanas_falla(monkeypatch, tmp_path, capsys):
    """Un NULL no es inactividad: el dia no se puede probar y falla cerrado."""
    siguiente = DIA + dt.timedelta(days=1)
    dias = _dia() + [(siguiente.isoformat(), 0, 0, None, 0.0)]
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), dias, _orbit(), capsys)
    assert codigo != 0
    assert f"FALTA el dia {siguiente.isoformat()} en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_control_con_fecha_ilegible_falla(monkeypatch, tmp_path, capsys):
    """Una fecha vacia con gasto no puede quedar fuera por orden lexicografico."""
    dias = _dia() + [("", 9, 8, 500.0, 900.0)]
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), dias, _orbit(), capsys)
    assert codigo != 0
    assert "fecha ilegible" in salida
    assert "CONCILIACION OK" not in salida


def test_orbit_duplicado_en_la_vista_vigente_falla(monkeypatch, tmp_path, capsys):
    """Dos observaciones vigentes de la misma campana/dia rompen la identidad."""
    orbit = _orbit() + [("C1", DIA, Decimal("9.0000"), Decimal("2.0000"), 3, 4, "MXN")]
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), _dia(), orbit, capsys)
    assert codigo != 0
    assert "duplicado en Orbit" in salida
    assert "CONCILIACION OK" not in salida


def test_campana_duplicada_en_origen_falla(monkeypatch, tmp_path, capsys):
    """Una clave repetida no se sobrescribe: la discrepancia anterior importa."""
    campanas = _campana() + [("C1", DIA.isoformat(), 9.0, 2.0, 3, 4)]
    codigo, salida = _corre(monkeypatch, tmp_path, campanas, _dia(), _orbit(), capsys)
    assert codigo != 0
    assert "duplicado en origen campana/dia" in salida
    assert "CONCILIACION OK" not in salida


def test_control_diario_duplicado_falla(monkeypatch, tmp_path, capsys):
    dias = _dia() + [(DIA.isoformat(), 4, 3, 9.0, 2.0)]
    codigo, salida = _corre(monkeypatch, tmp_path, _campana(), dias, _orbit(), capsys)
    assert codigo != 0
    assert "duplicado en origen control diario" in salida
    assert "CONCILIACION OK" not in salida


def test_null_coincidente_no_concilia(monkeypatch, tmp_path, capsys):
    """NULL en origen, control y Orbit no prueba conciliacion exacta."""
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        _campana(spend=None, ad_sales=None, impressions=None, clicks=None),
        _dia(spend=None, ad_sales=None, impressions=None, clicks=None),
        _orbit(spend=None, ad_sales=None, impressions=None, clicks=None),
        capsys,
    )
    assert codigo != 0
    assert "NULL no conciliable" in salida
    assert "CONCILIACION OK" not in salida


def test_actividad_antes_del_inicio_declarado_falla(monkeypatch, tmp_path, capsys):
    """El inicio es explicito: si el dia anterior esta en la ventana, un
    control con actividad y cero campanas es un hueco, aunque la primera
    campana del snapshot sea posterior."""
    dia1 = DIA
    dia2 = DIA + dt.timedelta(days=1)
    campanas = [("C1", dia2.isoformat(), 1.0, 2.0, 3, 4)]
    dias = [
        (dia1.isoformat(), 9, 8, 500.0, 900.0),
        (dia2.isoformat(), 4, 3, 1.0, 2.0),
    ]
    orbit = [("C1", dia2, Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")]

    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        campanas,
        dias,
        orbit,
        capsys,
        desde=dia1.isoformat(),
        hasta=dia2.isoformat(),
    )
    assert codigo != 0
    assert f"FALTA el dia {dia1.isoformat()} en Orbit" in salida
    assert "CONCILIACION OK" not in salida

    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        campanas,
        dias,
        orbit,
        capsys,
        desde=dia2.isoformat(),
        hasta=dia2.isoformat(),
    )
    assert codigo == 0
    assert "historia anterior a --desde excluida: 1" in salida


def test_identidad_null_no_concilia(monkeypatch, tmp_path, capsys):
    """Una identidad NULL en origen y Orbit tampoco es una coincidencia."""
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        [(None, DIA.isoformat(), 1.0, 2.0, 3, 4)],
        _dia(),
        [(None, DIA, Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")],
        capsys,
    )
    assert codigo != 0
    assert "campaign_id ilegible" in salida
    assert "identidad ilegible en Orbit" in salida


def test_campana_fuera_de_la_ventana_declarada_falla(monkeypatch, tmp_path, capsys):
    """La ventana no puede descartar campanas en silencio."""
    siguiente = DIA + dt.timedelta(days=1)
    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        _campana(),
        _dia(),
        _orbit(),
        capsys,
        desde=siguiente.isoformat(),
        hasta=siguiente.isoformat(),
    )
    assert codigo != 0
    assert "campana/dia fuera de la ventana declarada" in salida


# ---------------------------------------------------------------------------
# T+0: el control diario de HOY existe en accounting antes que el desglose
# de campanas. La ventana cerrada termina AYER; la exencion es SOLO para
# HOY, sin NULL, y fuera de la ventana declarada.
# ---------------------------------------------------------------------------


def test_control_t0_de_hoy_se_excluye_con_ventana_que_termina_ayer(monkeypatch, tmp_path, capsys):
    """Campanas y Orbit coinciden hasta AYER; el control ya trae el gasto de
    HOY. Con DESDE=HASTA=ayer la conciliacion es OK e informa el dia T+0."""
    ayer = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    hoy = ayer + dt.timedelta(days=1)
    campanas = [("C1", ayer.isoformat(), 1.0, 2.0, 3, 4)]
    dias = [
        (ayer.isoformat(), 4, 3, 1.0, 2.0),
        (hoy.isoformat(), 9, 8, 500.0, 900.0),
    ]
    orbit = [("C1", ayer, Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")]

    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        campanas,
        dias,
        orbit,
        capsys,
        desde=ayer.isoformat(),
        hasta=ayer.isoformat(),
    )
    assert codigo == 0
    assert "CONCILIACION OK" in salida
    assert "T+0" in salida


def test_control_historico_posterior_a_hasta_sigue_fallando(monkeypatch, tmp_path, capsys):
    """La exencion es SOLO de HOY: un control con actividad de una fecha ya
    pasada y fuera de la ventana sigue fallando."""
    hace_dos = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=2)
    hace_tres = hace_dos - dt.timedelta(days=1)
    campanas = [("C1", hace_tres.isoformat(), 1.0, 2.0, 3, 4)]
    dias = [
        (hace_tres.isoformat(), 4, 3, 1.0, 2.0),
        (hace_dos.isoformat(), 9, 8, 500.0, 900.0),
    ]
    orbit = [("C1", hace_tres, Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")]

    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        campanas,
        dias,
        orbit,
        capsys,
        desde=hace_tres.isoformat(),
        hasta=hace_tres.isoformat(),
    )
    assert codigo != 0
    assert "control diario fuera de la ventana declarada" in salida
    assert "CONCILIACION OK" not in salida


def test_control_t0_con_null_sigue_fallando(monkeypatch, tmp_path, capsys):
    """Un NULL en el control de HOY no es T+0: el dia no se puede probar."""
    ayer = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    hoy = ayer + dt.timedelta(days=1)
    campanas = [("C1", ayer.isoformat(), 1.0, 2.0, 3, 4)]
    dias = [
        (ayer.isoformat(), 4, 3, 1.0, 2.0),
        (hoy.isoformat(), 9, 8, None, 900.0),
    ]
    orbit = [("C1", ayer, Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")]

    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        campanas,
        dias,
        orbit,
        capsys,
        desde=ayer.isoformat(),
        hasta=ayer.isoformat(),
    )
    assert codigo != 0
    assert f"control diario fuera de la ventana declarada {hoy.isoformat()}" in salida
    assert "CONCILIACION OK" not in salida


def test_campana_de_hoy_fuera_de_la_ventana_sigue_fallando(monkeypatch, tmp_path, capsys):
    """La exencion T+0 es del CONTROL, nunca de las campanas: una campana de
    HOY con ventana cerrada en AYER sigue fallando."""
    ayer = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    hoy = ayer + dt.timedelta(days=1)
    campanas = [
        ("C1", ayer.isoformat(), 1.0, 2.0, 3, 4),
        ("C2", hoy.isoformat(), 1.0, 2.0, 3, 4),
    ]
    dias = [
        (ayer.isoformat(), 4, 3, 1.0, 2.0),
        (hoy.isoformat(), 13, 11, 501.0, 902.0),
    ]
    orbit = [("C1", ayer, Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")]

    codigo, salida = _corre(
        monkeypatch,
        tmp_path,
        campanas,
        dias,
        orbit,
        capsys,
        desde=ayer.isoformat(),
        hasta=ayer.isoformat(),
    )
    assert codigo != 0
    assert "campana/dia fuera de la ventana declarada" in salida
    assert "CONCILIACION OK" not in salida


def test_dsn_por_entorno_y_redactado(monkeypatch, tmp_path, capsys):
    """El DSN no se pasa por argv y los errores de conexion se redactan."""
    modulo = _carga_script()
    ruta = _snapshot(tmp_path, _campana(), _dia())
    monkeypatch.delenv("CONCILIAR_DSN", raising=False)
    codigo = modulo.main([str(ruta), "--desde", DIA.isoformat(), "--hasta", DIA.isoformat()])
    assert codigo == 2
    assert "CONCILIAR_DSN" in capsys.readouterr().err

    monkeypatch.setenv("CONCILIAR_DSN", "postgresql://usuario:CLAVE_FALSA@10.255.255.1:5432/orbit")

    def falla(_dsn):
        raise RuntimeError("no conecta postgresql://usuario:CLAVE_FALSA@10.255.255.1:5432/orbit")

    monkeypatch.setattr(modulo.psycopg, "connect", falla)
    codigo = modulo.main([str(ruta), "--desde", DIA.isoformat(), "--hasta", DIA.isoformat()])
    salida = capsys.readouterr()
    assert codigo == 1
    assert "CLAVE_FALSA" not in salida.err
    assert "***REDACTED***" in salida.err


@pytest.mark.parametrize(
    "ruta",
    sorted(RAIZ.glob("docs/evidencia/resumen-ads-multicanal/01-ingesta/*/scripts/conciliar.py")),
)
def test_todas_las_copias_de_conciliar_son_identicas(ruta):
    assert ruta.read_bytes() == SCRIPT.read_bytes(), (
        f"{ruta} no es identica al conciliar canonico: no puede quedar una copia "
        "con comportamiento distinto"
    )
