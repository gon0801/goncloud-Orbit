"""Aceptacion externa: exige comportamiento correcto, sin xfail ni SHA fijo."""
import ingest
import pytest


@pytest.fixture
def db():
    with ingest.base() as conexion:
        yield conexion


def test_09_jev_cron_cierra_corrida_sin_forzar_y_preserva_tx_ajena(db):
    r = ingest.caso09(*db)
    assert r["guarda_real"] == [True], r
    assert r["entradas_leer_mundo"] == ["IDLE"], r
    assert r["leer_mundo_completo"] == [True], r
    assert r["exit"] == 0 and r["cierres"] and r["avisos"] == 0, r
    assert r["tx_ajena_conservada"] == 1, r


def test_05_fuente_corrupta_no_publica_subtotal_ni_recrea_clave(db):
    r = ingest.caso05(*db)
    assert not r["consumidor_windows"] and r["planes_por_orden"] == [0,0], r


def test_06_scan_incompleto_no_desactiva_ausentes(db):
    r = ingest.caso06(*db)
    assert all(activo for _,activo in r["miembros"]), r
    assert all(truncado for _,truncado in r["variantes_scan"]), r


def test_14_contencion_no_declara_escrituras_revertidas(db):
    r = ingest.caso14(*db)
    assert r["observadas"] == r["observaciones_reales"] == r["sello"][1], r
    assert not r["ok"] and not r["sello"][0], r


def test_S1_error_de_proveedor_redactado_antes_de_persistir(db):
    r = ingest.casoS1(*db)
    assert all(not fila["clave_ficticia_persistida"] for fila in r), r


def test_control_productos_no_publica_subtotal_en_ningun_orden():
    assert ingest.control_productos() == [0,0]
