"""Aceptacion externa: casos correctos, no expectativas que perpetuan fallos.

07 recorre correr -> insumos reales -> decidir -> cotizar_y_decidir ->
CotizadorMeli real con MockTransport -> INSERT PG. La variante guardada
siembra una cotizacion valida como dato, no reemplaza funciones del producto.
08/D2 y cargos reutilizan pruebas existentes; solo se cambia su fabrica de DB
para no tocar bases de otros revisores. Nunca ejecuta la suite completa.
"""
import os
import sys
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get('ORBIT_REVIEW_REPO', os.getcwd())).resolve()
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Json

DB = 'thermo_pricing_current'


@contextmanager
def base(orden=None):
    dsn = os.environ['ORBIT_TEST_DSN']
    info = conninfo_to_dict(dsn)
    assert (info.get('host'), info.get('port', '5432')) in {
        ('127.0.0.1', '55449'), ('orbit-review-current-pg', '5432')
    }, 'Solo PG desechable de esta revision'
    propia = make_conninfo(dsn, dbname=DB)
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert admin.execute('SHOW server_version_num').fetchone()[0].startswith('16')
        assert admin.execute("SELECT count(*) FROM pg_roles WHERE rolname IN "
                             "('app_read','app_decide','app_ingest','app_admin')").fetchone()[0] == 4
        assert not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (DB,)).fetchone()
        admin.execute('CREATE DATABASE thermo_pricing_current')
        try:
            with psycopg.connect(propia, autocommit=True) as conn:
                conn.execute("SET TIME ZONE 'UTC'")
                conn.execute("SET statement_timeout='30s'")
                rutas = [ROOT / 'migrations' / n for n in orden] if orden else [
                    p for p in sorted((ROOT / 'migrations').glob('*.sql'))
                    if '0011_' not in p.name and '_reversa_' not in p.name
                ]
                for ruta in rutas:
                    conn.execute(ruta.read_text())
                print(f'PG: {DB}; migraciones={len(rutas)}; ultima={rutas[-1].name}')
                yield conn, propia
        finally:
            admin.execute('DROP DATABASE thermo_pricing_current')
            print('Limpieza: eliminada solo thermo_pricing_current')


@pytest.fixture(autouse=True)
def sin_red_externa(monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError('HTTP real prohibido: debe usarse MockTransport')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', prohibido)
    monkeypatch.setenv('ORBIT_SECRETS_DIR', str(Path(__file__).with_name('pricing.sin-secretos')))


def preparar_meli(conn):
    import test_precio02_escenario_meli as semillas
    from test_precio_corrida import SETTINGS, _config, _goal
    from app.precio.config import leer_config
    from app.meli.precio_mercado import MercadoMeli
    from app.meli.estimacion import CotizadorMeli
    from app.meli.write_client import MeliWriteClient
    from app.meli.client import ClienteMeli, MeliCredentials
    from app.precio.puerto import MetaGoal

    ahora = conn.execute('SELECT clock_timestamp()').fetchone()[0]
    pid = semillas._producto(conn, 'SKU-ACCEPT-07')
    # La observacion de la publicacion nace dentro del dia UTC de la corrida
    # (el insumo exige metric_date = hoy) y sin cruzar el fee que sembrara
    # _siembra 30 minutos antes de su `ahora`. Entre las 00:00 y las 01:00
    # UTC un retroceso de una hora cruzaba la medianoche: su metric_date
    # quedaba en ayer y la decision salia no_evaluado/precio_sin_observar.
    medianoche = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    observado = ahora - timedelta(hours=1)
    if observado < medianoche:
        observado = medianoche
    lid = semillas._publicacion(conn, 'MLM1', [('SKU-ACCEPT-07', pid)],
                               observado=observado)
    # Costo vigente con ingesta reciente y completa. El helper historico M.3
    # fecha el run en septiembre y la corrida actual lo rechaza por frescura.
    run_costo = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, "
        "rows_skipped, ok) VALUES ('accounting_sku_costs', now(), now(), 1, 0, TRUE) "
        "RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, "
        "valid_from, ingest_run_id) VALUES (%s, 400, 'MXN', FALSE, %s, %s)",
        (pid, ahora.date()-timedelta(days=200), run_costo),
    )
    semillas._muestra(conn, pid)
    # La cadena 0093 trae una politica S4 abierta para meli/meli: una segunda abierta
    # vuelve ambigua la resolucion. La de prueba solo cubre cadenas que no la traen.
    politica_de_la_cadena = conn.execute(
        "SELECT 1 FROM estimacion_politica_version"
        " WHERE universo = 'meli/meli' AND valid_to IS NULL"
    ).fetchone()
    if not politica_de_la_cadena:
        semillas._politica(conn)
    ahora = conn.execute('SELECT clock_timestamp()').fetchone()[0]
    # Fuera de la ventana 00:00-01:00 UTC esto es `ahora` (comportamiento
    # historico). Dentro, se corre para que el fee de _siembra (que fecha a
    # `ahora - 30 min`) no quede anterior a la observacion de la publicacion:
    # el trigger `estimacion_fuente_no_futura` exige fetched <= observed (solo
    # orden relativo, sin reloj).
    siembra_desde = max(ahora, observado + timedelta(minutes=30))
    semillas._siembra(conn, lid, ahora=siembra_desde, observed_at=ahora, valoracion=ahora.date())
    settings = {**SETTINGS, 'precio_modo_global': 'shadow',
                'precio_modo_universo': {'meli/meli': 'shadow'}}
    _config(conn, settings)
    _goal(conn, lid, platform='meli', mode='shadow')
    calls = []

    def respuesta(request):
        calls.append((request.method, request.url.path))
        assert request.method == 'GET', 'Este caso shadow no debe escribir'
        assert request.url.path == '/sites/MLM/listing_prices'
        precio = D(request.url.params['price'])
        fee = (precio * D('.195')).quantize(D('.01'))
        return httpx.Response(200, json={
            'currency_id': 'MXN', 'sale_fee_amount': str(fee),
            'sale_fee_details': {'percentage_fee': '19.5', 'fixed_fee': '0'},
            'listing_fee_amount': '0',
        })

    creds = MeliCredentials(access_token='synthetic-acceptance')
    transport = httpx.MockTransport(respuesta)
    mercado = MercadoMeli('meli',
        lector=ClienteMeli(creds, transport=transport, backoff_base=0),
        fees=CotizadorMeli(ClienteMeli(creds, transport=transport, backoff_base=0)),
        escritor=MeliWriteClient(modo_confirmado='live', creds=creds, transport=transport))
    config = leer_config(settings)
    unidad = mercado.unidades(conn, [lid])[lid]
    lectura = mercado.insumos(conn, [unidad], hoy=ahora.date(), ahora=ahora,
        config=config, metas={lid: MetaGoal(goal=D('.30'), mode='shadow',
        goal_vigente_desde=ahora.date()-timedelta(days=30))})[lid]
    assert lectura.entrada is not None, (lectura.motivo, lectura.diagnostico)
    return mercado, lectura, config, ahora, calls


@pytest.mark.parametrize('guardada', [False, True], ids=['corriente', 'persistida'])
def test_07_corrida_meli_conserva_fecha_y_detalle(guardada):
    from app.precio import corrida
    from app.precio.decision_io import _evento_cotizacion
    from app.precio.reglas import decidir
    from app.precio.tipos import PideCotizacion
    with base() as (conn, _):
        mercado, lectura, config, ahora, calls = preparar_meli(conn)
        try:
            pedido = decidir(lectura.entrada, hoy=ahora.date(), config=config,
                             fees_escenario=lectura.fees_escenario)
            assert isinstance(pedido, PideCotizacion), pedido
            print('07 estado alcanzable:', pedido, 'listing=', lectura.entrada.listing_id)
            lid = lectura.entrada.listing_id
            oid = lectura.ids['oferta_observation_id']
            evento = _evento_cotizacion(lid, 'meli', ahora.date(), pedido.intento)
            if guardada:
                # Estado persistido valido; fecha/arbol proceden del cotizador real.
                resultado = mercado._fees.cotizar(lectura.cotizable, pedido.precio.valor,
                    ahora=lambda: conn.execute('SELECT clock_timestamp()').fetchone()[0])
                assert resultado.estado == 'success'
                conn.execute("INSERT INTO precio_cotizacion "
                    "(listing_id, platform, intento, oferta_observation_id, quoted_price, "
                    "quoted_price_currency, total_fees, total_fees_currency, fee_details, "
                    "fees_estimated_at, estado, source_event_id) "
                    "VALUES (%s,'meli',%s,%s,%s,'MXN',%s,'MXN',%s,%s,'success',%s)",
                    (lid, pedido.intento, oid, pedido.precio.valor, resultado.total_fees,
                     Json(resultado.fee_details), resultado.fees_estimated_at, evento))
                calls.clear()
            resumen = corrida.correr(conn, mercado, owner='accept-pricing')
            assert resumen.decisiones == 1 and not resumen.errores, resumen
            fila = conn.execute('SELECT fees_estimated_at, fee_details FROM precio_cotizacion '
                                'WHERE source_event_id=%s', (evento,)).fetchone()
            assert fila and fila[0] is not None and fila[1]
            _, cot = mercado.cotizacion_guardada(conn, evento=evento, listing_id=lid,
                        hoy=ahora.date(), oferta_id=oid, pedido=pedido)
            assert cot.estimada_en == fila[0], '07: el readback descarta la fecha valida'
            assert cot.fees and cot.fees.detalle, '07: el readback descarta el arbol valido'
            assert sum(d.final_fee for d in cot.fees.detalle) == cot.fees.total.valor
            if guardada:
                assert not calls, 'La cotizacion persistida debe reutilizarse sin otro GET'
            print('07 cierre: corrida completa, persistencia y readback conservan metadatos')
        finally:
            mercado.cerrar()


def test_08_aplicar_y_guardas_reversa_con_unidades_reales(monkeypatch):
    import test_precio_write as existente
    @contextmanager
    def propia():
        with base(existente.ORDEN59S5) as (conn, _):
            yield conn
    monkeypatch.setattr(existente, '_db59', propia)
    existente.test_arreglos01_r_meli_con_variaciones_recibe_precio()
    existente.test_arreglos01_r_variacion_nueva_sin_producto_no_recibe_precio()
    existente.test_arreglos01_r_reversa_salta_variacion_sin_producto()


def test_D2_retencion_ayer_sin_liberar_no_abre_hoy(monkeypatch):
    import test_precio_corrida as existente
    @contextmanager
    def propia():
        with base(existente.ORDEN59) as datos:
            yield datos
    monkeypatch.setattr(existente, '_db', propia)
    existente.test_arreglos01_e_dia_con_retencion_masiva_no_soltada_no_es_base()


@pytest.mark.parametrize('nombre', [
    'test_arreglos02_d_la_estimada_no_se_suma_a_la_comision_real',
    'test_arreglos02_d_el_cargo_sin_source_event_id_no_se_descarta',
    'test_arreglos02_d_la_reversa_regresa_las_vistas',
])
def test_arreglos02_cargos_y_reversa_existentes(monkeypatch, nombre):
    import test_arreglos02_cargos as existente
    @contextmanager
    def propia(*args):
        with base() as (conn, _):
            yield conn
    monkeypatch.setattr(existente, '_db_arreglos02', propia)
    getattr(existente, nombre)()
