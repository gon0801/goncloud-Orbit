"""TEMPORADA 01, bloque A: calendario y parametros en Ajustes.

Dos pruebas nombradas por la tabla del diseno + una de cableado:

- `test_temporada01_a_fechas_del_calendario` (dinero): los casos de
  «Temporada baja, salida y alta -> Calendario» del diseno (1 ene baja;
  regreso 8 feb 2027 y 9 feb 2028; ago-sep-oct con 42 dias -> 20 ago;
  dic-feb un solo bloque; fin_anticipado).
- `test_temporada01_a_post_rechaza_booleanos` (escribe en produccion):
  true/false en un campo de numero dan 422 con el mensaje exacto y la
  config no cambia; "25" y 25 se aceptan.
- `test_temporada01_a_campana_topada_una_definicion` (dinero):
  ads_campana_topada guardado lo leen pantalla_dinero y avisos_campana
  (una sola definicion: temporada.campana_topada_desde_settings).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_pantalla_dinero import (
    HASTA_2A,
    _db_dinero,
    _siembra_campana,
    _siembra_config,
    _siembra_gasto_campana,
)
from test_schema import _postgres_obligatorio_ausente

from app.main import app
from app.optimizer import temporada

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MESES_DEFECTO = list(temporada.MESES_POR_DEFECTO)


def _crudo(meses=MESES_DEFECTO, prep=21, reduccion="30", fin=None, mult="2"):
    """El objeto ads_temporada tal como viaja y se guarda."""
    return {
        "version": temporada.VERSION_TEMPORADA,
        "meses": list(meses),
        "reduccion_baja_pct": reduccion,
        "dias_preparacion": prep,
        "mult_cortes_baja": mult,
        "aumento_presupuesto_alta_pct": "25",
        "fin_anticipado": fin,
        "mantener_pausadas": [],
    }


def test_temporada01_a_fechas_del_calendario():
    """Los casos del calendario del diseno, sobre fechas reales."""
    config = temporada.valida(_crudo())

    # 1 de enero: baja, el bloque abre ese dia.
    est = temporada.resuelve(config, dt.date(2027, 1, 1))
    assert est.fase == "baja"
    assert est.inicio_bloque == dt.date(2027, 1, 1)
    assert est.reduccion_baja == Decimal("0.30")

    # Regreso el 8 feb 2027 (preparacion de marzo, 21 dias antes del 1 mar).
    assert temporada.resuelve(config, dt.date(2027, 2, 7)).fase == "baja"
    regreso_2027 = temporada.resuelve(config, dt.date(2027, 2, 8))
    assert regreso_2027.fase == "normal"
    assert regreso_2027.previa_alta is True

    # Ano bisiesto: el regreso de 2028 cae el 9 feb.
    assert temporada.resuelve(config, dt.date(2028, 2, 8)).fase == "baja"
    regreso_2028 = temporada.resuelve(config, dt.date(2028, 2, 9))
    assert regreso_2028.fase == "normal"
    assert regreso_2028.previa_alta is True

    # Marzo y abril son alta; noviembre tambien.
    assert temporada.resuelve(config, dt.date(2027, 3, 1)).fase == "alta"
    assert temporada.resuelve(config, dt.date(2027, 11, 15)).fase == "alta"
    # La preparacion de octubre empieza el 10 sep y no mueve bids (no viene de baja).
    previa_oct = temporada.resuelve(config, dt.date(2027, 9, 15))
    assert previa_oct.fase == "normal"
    assert previa_oct.previa_alta is True

    # ago-sep-oct con 42 dias de preparacion: el regreso es el 20 de agosto,
    # aunque haya un mes normal en medio.
    meses_ago = ["normal"] * 12
    meses_ago[7] = "baja"  # agosto
    meses_ago[8] = "normal"  # septiembre
    meses_ago[9] = "alta"  # octubre
    config_ago = temporada.valida(_crudo(meses=meses_ago, prep=42))
    assert temporada.resuelve(config_ago, dt.date(2027, 8, 19)).fase == "baja"
    regreso_ago = temporada.resuelve(config_ago, dt.date(2027, 8, 20))
    assert regreso_ago.fase == "normal"
    assert regreso_ago.previa_alta is True
    # La fase normal-preparacion abre el dia del regreso.
    assert regreso_ago.inicio_bloque == dt.date(2027, 8, 20)
    assert temporada.resuelve(config_ago, dt.date(2027, 8, 1)).fase == "baja"
    assert temporada.resuelve(config_ago, dt.date(2027, 10, 5)).fase == "alta"

    # dic-feb es un solo bloque: enero no abre otro episodio.
    meses_dic = ["baja", "baja", "alta", "alta"] + ["normal"] * 7 + ["baja"]
    config_dic = temporada.valida(_crudo(meses=meses_dic))
    ene = temporada.resuelve(config_dic, dt.date(2027, 1, 10))
    assert ene.fase == "baja"
    assert ene.inicio_bloque == dt.date(2026, 12, 1)
    # El bloque entero cierra con la preparacion de marzo: 8 feb 2027.
    assert temporada.resuelve(config_dic, dt.date(2027, 2, 8)).fase == "normal"
    assert temporada.resuelve(config_dic, dt.date(2026, 12, 15)).fase == "baja"

    # fin_anticipado: terminar la baja el 15 de enero.
    config_fin = temporada.valida(_crudo(fin="2027-01-15"))
    assert temporada.resuelve(config_fin, dt.date(2027, 1, 14)).fase == "baja"
    fin = temporada.resuelve(config_fin, dt.date(2027, 1, 15))
    assert fin.fase == "normal"
    # Aun no es preparacion del bloque alto: esa empieza el 8 feb.
    assert fin.previa_alta is False
    # La alta de marzo sigue tal cual.
    assert temporada.resuelve(config_fin, dt.date(2027, 3, 1)).fase == "alta"
    # El resumen anuncia el regreso en el dia elegido y el aviso de la baja
    # que hoy empieza cuando la edicion cambia la fase de hoy.
    resumen = temporada.resumen(
        config_fin,
        dt.date(2027, 1, 15),
        antes=config,
        metas_hoy={"amazon_mx": Decimal("20.08")},
        meta_alta={"amazon_mx": Decimal("25"), "amazon_us": Decimal("25")},
    )
    tipos_fechas = [(e.tipo, e.fecha) for e in resumen.proximos]
    assert ("restaura", dt.date(2027, 1, 15)) in tipos_fechas
    assert ("entra_baja", dt.date(2027, 5, 1)) in tipos_fechas
    assert resumen.aviso_cambio is not None
    assert "temporada baja" in resumen.aviso_cambio


@_skip_db
def test_temporada01_a_post_rechaza_booleanos(tmp_path, monkeypatch):
    """true/false en campo de numero dan 422 con el mensaje exacto y la
    config no cambia; "25" y 25 se aceptan (fila nueva)."""
    with _db_con_rol_admin("orbit_t01a_post") as (conn, dsn_admin, _dsn_l):
        id_viejo = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json({"ads_optimizer_mode": "shadow"})),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)

        def post_temporada(temporada_crudo, campana_topada=None, base=id_viejo):
            cuerpo = {"base_config_version_id": base, "temporada": temporada_crudo}
            if campana_topada is not None:
                cuerpo["campana_topada"] = campana_topada
            return cliente.post(
                "/api/ads-optimizer/settings/amazon_mx",
                json=cuerpo,
                headers={"x-orbit-token": TOKEN},
            )

        # true en un campo de numero: 422 con el mensaje exacto y cero filas nuevas.
        resp = post_temporada(_crudo(reduccion=True))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Escribe un número, no sí o no."
        # false en el criterio de campana topada: el mismo mensaje.
        resp = post_temporada(_crudo(), campana_topada={"pct": False, "dias": 5})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Escribe un número, no sí o no."
        # Coma decimal: mensaje exacto de la tabla.
        resp = post_temporada(_crudo(reduccion="30,5"))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Escribe solo el número, por ejemplo 30."
        assert conn.execute("SELECT count(*) FROM config_version").fetchone()[0] == 1

        # "25" y 25 se aceptan (dos guardados, fila nueva en cada uno).
        resp = post_temporada(_crudo(reduccion="30"))
        assert resp.status_code == 200, resp.text
        id_1 = resp.json()["config_version_id"]
        fila = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (id_1,)
        ).fetchone()[0]
        assert fila["ads_temporada"]["reduccion_baja_pct"] == "30"
        assert fila["ads_temporada"]["dias_preparacion"] == 21
        assert fila["ads_temporada"]["meses"] == MESES_DEFECTO
        # Sin edicion del criterio, la clave no viaja (ausente = default del lector).
        assert "ads_campana_topada" not in fila

        resp = post_temporada(
            _crudo(reduccion=25), campana_topada={"pct": "90", "dias": 5}, base=id_1
        )
        assert resp.status_code == 200, resp.text
        id_2 = resp.json()["config_version_id"]
        fila = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (id_2,)
        ).fetchone()[0]
        assert fila["ads_temporada"]["reduccion_baja_pct"] == "25"
        assert fila["ads_campana_topada"] == {"pct": "90", "dias": 5}
        # La respuesta trae el resumen de la temporada (vista de «Así queda tu año»).
        assert resp.json()["resumen_temporada"]["fase_hoy"] in ("baja", "normal", "alta")


@_skip_db
def test_temporada01_a_campana_topada_una_definicion():
    """ads_campana_topada guardado lo leen dinero y avisos con el mismo lector."""
    from app.avisos_campana import avisos_del_dia
    from app.pantalla_dinero import FilaCampana, campana_topada_vigente, lee_campanas

    with _db_dinero("orbit_t01a_topada") as conn:
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s)",
            ("topada", Json({"ads_campana_topada": {"pct": "80", "dias": 3}})),
        )
        topada = campana_topada_vigente(conn)
        assert topada.pct == Decimal("0.80")
        assert topada.dias == 3

        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        c1 = _siembra_campana(conn, "amazon_mx", "c1", "Topada 80")
        _siembra_config(conn, c1, dt.datetime(2026, 9, 27, tzinfo=dt.UTC), Decimal("100"), "MANUAL")
        for i in range(7):
            dia = HASTA_2A - dt.timedelta(days=6 - i)
            # 85 de 100: al tope con 80 %, debajo del 90 % de hoy.
            _siembra_gasto_campana(
                conn, c1, dia, Decimal("85") if i < 3 else Decimal("0"), obs, run
            )
        filas = lee_campanas(
            conn, plataforma="amazon_mx", desde=HASTA_2A - dt.timedelta(days=29), hasta=HASTA_2A
        )
        assert filas[0].dias_al_tope_7d == 3

        fila = FilaCampana(
            campana_id=c1,
            nombre="Topada 80",
            presupuesto_diario=Decimal("100"),
            gasto_medio_diario=Decimal("36.43"),
            uso_presupuesto_pct=Decimal("36.43"),
            estrategia="fija",
            ajustes_ubicacion=(),
            gasto_fuera_de_amazon=None,
            dias_al_tope_7d=3,
        )
        avisos = avisos_del_dia(
            (),
            (fila,),
            plataforma="amazon_mx",
            gasto_para_concluir=Decimal("350"),
            topada=topada,
        )
        assert [a.clase for a in avisos] == ["campana_sin_presupuesto"]
        assert avisos[0].frase == (
            "Topada 80: se queda sin presupuesto. Usó 80 % o más en 3 de los últimos 7 días."
        )
