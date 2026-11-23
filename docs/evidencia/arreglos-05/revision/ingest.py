"""Revalidacion portable: solo HTTP simulado y base propia thermo_ingest_current.

ORBIT_REVIEW_REPO=/repo (o cwd), ORBIT_REVIEW_SHA informativo,
ORBIT_REVIEW_PG_DSN obligatorio para PG desechable local.
python -B /evidence/ingest.py; pytest externo importa los mismos escenarios.
Instala el esquema real solo en PG desechable; no cambia el checkout ni usa secretos.
"""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(os.environ.get("ORBIT_REVIEW_REPO", os.getcwd())).resolve()
sys.path[:0] = [str(REPO), str(REPO / "tests")]

import contextlib
import datetime as dt
import hashlib
import json
import re
import subprocess
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import cli, jev_senales, metricas_listas
from app.ads import reports
from app.ads.structure import evaluar_perfiles
from app.dia_cerrado import ultimo_dia_cerrado
from app.meli import catalogo
from app.meli.client import ClienteMeli, MeliCredentials
from app.optimizer import windows

DATABASE = "thermo_ingest_current"
# Cadena completa del checkout, con las exclusiones de verify/Launch.md.
# No salta dependencias intermedias (p.ej. 0020 y sus tablas de productos).
MIGRACIONES = tuple(
    p.name for p in sorted((REPO / "migrations").glob("*.sql"))
    if not p.name.startswith("0011_") and "_reversa_" not in p.name
)


def identidad():
    sha = os.environ.get("ORBIT_REVIEW_SHA")
    if not sha:
        sha = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    print("SHA:", sha, "Python:", sys.version.split()[0], "repo:", REPO, flush=True)
    for m in (reports, catalogo, jev_senales, metricas_listas):
        p = Path(m.__file__).resolve()
        if not p.is_relative_to(REPO):
            raise RuntimeError("Import fuera del checkout: " + str(p))
        print("IMPORT:", p, "sha256:", hashlib.sha256(p.read_bytes()).hexdigest(), flush=True)


def esquema(c):
    print("MIGRACIONES COMPLETAS:", len(MIGRACIONES), MIGRACIONES[-1], flush=True)
    for nombre in MIGRACIONES:
        c.execute((REPO / "migrations" / nombre).read_text())


@contextlib.contextmanager
def base():
    dsn = os.environ["ORBIT_REVIEW_PG_DSN"]
    info = conninfo_to_dict(dsn)
    if info.get("host") not in {"127.0.0.1", "localhost", "orbit-review-current-pg"}:
        raise ValueError("Solo PG local desechable autorizado")
    if info.get("host") in {"127.0.0.1", "localhost"} and info.get("port") != "55449":
        raise ValueError("Puerto local debe ser 55449")
    if info.get("dbname") != "postgres":
        raise ValueError("DSN administrador debe apuntar a postgres")
    propio = make_conninfo(dsn, dbname=DATABASE)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        assert 160000 <= admin.info.server_version < 170000
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone():
            raise RuntimeError("Base ya existe: no tocar ni borrar una base ajena")
        admin.execute("CREATE DATABASE thermo_ingest_current")
        try:
            with psycopg.connect(propio, autocommit=True) as c:
                c.execute("SET TIME ZONE 'UTC'")
                esquema(c)
                yield c, propio
        finally:
            admin.execute("DROP DATABASE thermo_ingest_current")
            print("CLEANUP: thermo_ingest_current eliminada", flush=True)


def meli(paginas, *, sku="SKU-REVISION"):
    turno = iter(paginas)
    def responder(req):
        assert req.method == "GET" and req.url.host == "api.mercadolibre.com"
        ruta = req.url.path
        if ruta == "/users/me":
            data = {"id": 7}
        elif ruta == "/users/7/items/search":
            data = next(turno)
        elif ruta.endswith("/visits/time_window"):
            data = {"total_visits": 3}
        elif ruta.startswith("/items/"):
            data = {"id": ruta.rsplit("/", 1)[1], "status": "active", "price": 100,
                    "currency_id": "MXN", "available_quantity": 2,
                    "attributes": [{"id": "SELLER_SKU", "value_name": sku}]}
        else:
            raise AssertionError(ruta)
        return httpx.Response(200, json=data)
    return ClienteMeli(MeliCredentials("TOKEN_FICTICIO"), transport=httpx.MockTransport(responder))


def producto(c):
    return c.execute("INSERT INTO product(odoo_sku,name) VALUES ('SKU-REVISION','Sintetico') RETURNING id").fetchone()[0]


def caso09(c, dsn):
    # ARREGLOS 03 D-E.7: reloj de la guarda y del fixture fijados al mismo
    # instante (invierno, 07:30 UTC: US y MX tienen distinto ultimo dia
    # cerrado); la corrida lleva la fecha UTC de ese instante y cada
    # plataforma su ultimo dia cerrado. Original en historico/ingest.py.
    ahora = dt.datetime(2026, 11, 12, 7, 30, tzinfo=dt.UTC)
    rid = c.execute("INSERT INTO ingest_run(source,ok,started_at,finished_at) VALUES (%s,true,%s,%s) RETURNING id", (metricas_listas.SOURCE_METRICAS, ahora, ahora)).fetchone()[0]
    for plat, moneda in (("amazon_mx", "MXN"), ("amazon_us", "USD")):
        eid = c.execute("INSERT INTO ad_entity(platform,kind,external_id) VALUES (%s,'campaign','repro09') RETURNING id", (plat,)).fetchone()[0]
        c.execute("INSERT INTO ads_metric_observation(ad_entity_id,metric_date,observed_at,metric_currency,ingest_run_id) VALUES (%s,%s,%s,%s,%s)", (eid, ultimo_dia_cerrado(plat, ahora), ahora, moneda, rid))
    c.execute("INSERT INTO config_version(settings) VALUES (%s::jsonb)", (json.dumps({"jev.senales": True, "jev.tope_diario": 10, "jev.min_clics": 3}),))
    avisos = []
    entradas_mundo, retornos_mundo, retornos_guarda = [], [], []
    codigo_mundo = jev_senales._leer_mundo.__code__
    codigo_guarda = metricas_listas.guarda.__code__
    def observar(frame, evento, arg):
        # Instrumentacion del interprete; no reemplaza funcion, conexion ni SQL.
        if frame.f_code is codigo_mundo:
            if evento == "call":
                entradas_mundo.append(frame.f_locals["lector"].info.transaction_status.name)
            elif evento == "return":
                retornos_mundo.append(isinstance(arg, tuple) and len(arg) == 6)
        elif frame.f_code is codigo_guarda and evento == "return":
            retornos_guarda.append(arg)
    perfil_anterior = sys.getprofile()
    try:
        sys.setprofile(observar)
        # Llave ficticia, no vacia: desde ARREGLOS 03 D-J una corrida sin llave
        # cierra `sin_api_key` y sale 1 a proposito. El plan esta vacio (no hay
        # search terms sembrados), asi que nadie llama al proveedor; si alguien
        # lo llamara, el pedido falla aqui en vez de salir a la red.
        with patch.dict(os.environ, {"ORBIT_DSN_READ": dsn, "ORBIT_DSN_JEV": dsn}), \
             patch.object(jev_senales, "leer_api_key", return_value="LLAVE_FICTICIA_TN09"), \
             patch.object(jev_senales, "pedir_juicio", side_effect=AssertionError("TN09 no sale a la red")), \
             patch.object(metricas_listas, "_ahora", return_value=ahora), \
             patch("app.notifica.envia_aviso", side_effect=lambda *a, **kw: avisos.append(a)):
            salida = cli.main(["jev-senales", "--aplicar"])
    finally:
        sys.setprofile(perfil_anterior)
    cierre = c.execute("SELECT cierre FROM jev_corrida WHERE evento='fin'").fetchall()
    # Vecino: la guarda no revierte una transaccion que pertenece al llamador.
    with psycopg.connect(dsn) as lector:
        with lector.transaction():
            lector.execute("INSERT INTO product(odoo_sku,name) VALUES ('CONTROL-TX','Control')")
            assert metricas_listas.guarda(lector, ("amazon_mx", "amazon_us"), ahora=ahora)
        conservado = lector.execute("SELECT count(*) FROM product WHERE odoo_sku='CONTROL-TX'").fetchone()[0]
    return {"exit": salida, "cierres": cierre, "avisos": len(avisos), "tx_ajena_conservada": conservado,
            "guarda_real": retornos_guarda, "entradas_leer_mundo": entradas_mundo,
            "leer_mundo_completo": retornos_mundo}


def caso05(c, dsn):
    ahora = dt.datetime.now(dt.UTC)
    hoy = ahora.date()
    gid = c.execute("INSERT INTO ad_entity(platform,kind,external_id) VALUES ('amazon_mx','ad_group','7') RETURNING id").fetchone()[0]
    def responder_perfiles(req):
        assert req.method == "GET" and req.url.path == "/v2/profiles"
        return httpx.Response(200, json=[{"profileId": 202, "countryCode": "MX", "currencyCode": "MXN", "accountInfo": {"type": "seller", "id": "2"}}])
    # evaluar_perfiles recibe un cliente con get(), no una lista ya evaluada.
    with httpx.Client(base_url="https://advertising-api.amazon.com", transport=httpx.MockTransport(responder_perfiles)) as cliente_perfiles:
        perfil = evaluar_perfiles(cliente_perfiles)[0]
    assert perfil.aceptado and perfil.platform == "amazon_mx"
    filas = []
    # Diez fechas: siete quedan dentro de la ventana tras el desfase de tres dias.
    for delta in range(14, 24):
        base_fila = {"adGroupId": 7, "searchTerm": "collar", "date": (hoy-dt.timedelta(days=delta)).isoformat()}
        filas.extend([{**base_fila, "cost": "130", "clicks": 100, "purchases7d": 0, "sales7d": "0"},
                      {**base_fila, "cost": "ilegible", "clicks": 5, "purchases7d": 2, "sales7d": "2000"}])
    rid = c.execute("INSERT INTO ingest_run(source) VALUES (%s) RETURNING id", (reports.SOURCE,)).fetchone()[0]
    item = SimpleNamespace(perfil=perfil, cfg=reports.SEARCH_TERMS_CFG, report_id="repro05", filas=filas)
    reports.escribir_grupo(c, rid, None, [item], fecha_ini=hoy-dt.timedelta(days=23), fecha_fin=hoy-dt.timedelta(days=14))
    ventana = windows.terminos_cortes(c, gid, ahora)
    agregado = [(t.search_term, str(t.cost), t.orders, str(t.ad_revenue)) for t in ventana.terminos]
    # Variante vecina con datos parciales tipados: no debe depender del orden.
    base_fila = {"adGroupId": 7, "searchTerm": "collar", "date": (hoy-dt.timedelta(days=14)).isoformat()}
    a, b = {**base_fila, "clicks": 5}, {**base_fila, "cost": "0.5"}
    buena = {**base_fila, "cost": "130", "clicks": 100, "purchases7d": 0, "sales7d": "0"}
    orden = []
    for crudas in ([a,b,buena], [buena,a,b]):
        p, _ = reports._planea_filas_terminos(reports.SEARCH_TERMS_CFG, crudas, ultimo_cerrado=ultimo_dia_cerrado("amazon_mx", ahora), fecha_ini=hoy-dt.timedelta(days=23), fecha_fin=hoy)
        orden.append(len(p))
    return {"consumidor_windows": agregado, "entidad_completa": ventana.completa, "planes_por_orden": orden,
            "sello": c.execute("SELECT ok,rows_written,rows_skipped FROM ingest_run WHERE id=%s", (rid,)).fetchone()}


def caso06(c, dsn):
    pid = producto(c)
    for ext in ("MLM1001", "MLM1002"):
        lid = c.execute("INSERT INTO listing(product_id,platform,external_id,seller_sku) VALUES (%s,'meli',%s,'SKU-REVISION') RETURNING id", (pid, ext)).fetchone()[0]
        c.execute("INSERT INTO listing_miembro(listing_id,platform,seller_sku,product_id,visto_at) VALUES (%s,'meli','SKU-REVISION',%s,now())", (lid,pid))
    cliente = meli([{"results": ["MLM1001"], "paging": {"total": 2}}])
    try:
        with patch.dict(os.environ, {"ORBIT_DSN_INGEST": dsn}):
            salida = catalogo.main([], cliente=cliente)
    finally:
        cliente.close()
    activos = c.execute("SELECT l.external_id,m.activo FROM listing l JOIN listing_miembro m ON l.id=m.listing_id ORDER BY l.external_id").fetchall()
    # Mismo contrato: pagina vacia antes del total y duplicados no acreditan completitud.
    variantes = []
    for paginas in (
        [{"results":["MLM1001"],"paging":{"total":2},"scroll_id":"x"}, {"results":[],"paging":{"total":2}}],
        [{"results":["MLM1001","MLM1001"],"paging":{"total":2}}],
    ):
        cliente = meli(paginas)
        try:
            variantes.append(cliente.items_seller(7))
        finally:
            cliente.close()
    return {"exit": salida, "miembros": activos, "variantes_scan": variantes}


def caso14(c, dsn):
    producto(c)
    cliente = meli([{"results":["MLM1001"],"paging":{"total":1}}])
    # Contencion real: SHARE permite leer listing, pero impide INSERT/UPDATE.
    # No se inventa una constraint ni se sustituye _observa/_aplica_plan.
    try:
        with psycopg.connect(dsn) as bloqueo:
            bloqueo.execute("LOCK TABLE listing IN SHARE MODE")
            with psycopg.connect(dsn) as trabajo:
                trabajo.execute("SET lock_timeout = '150ms'")
                trabajo.commit()
                ahora = dt.datetime.now(dt.UTC)
                r = catalogo.sincronizar(trabajo, cliente, hoy=ahora.date(), ahora=ahora)
    finally:
        cliente.close()
    reales = c.execute("SELECT count(*) FROM meli_publicacion_observation WHERE ingest_run_id=%s", (r.run_id,)).fetchone()[0]
    sello = c.execute("SELECT ok,rows_written,rows_skipped FROM ingest_run WHERE id=%s", (r.run_id,)).fetchone()
    return {"observaciones_reales": reales, "observadas": r.observadas, "ok": r.ok, "sello": sello}


def casoS1(c, dsn):
    from app.jev_ads import FichaVersion, HechoConFuente
    from app.jev_juicios import Contrato, pedir_juicio
    from app.jev_libro import Libro
    from test_jev_catalogo import _ficha, _revision
    pid = producto(c)
    ahora = dt.datetime.now(dt.UTC)
    lid = c.execute("INSERT INTO listing(product_id,platform,external_id,seller_sku) VALUES (%s,'amazon_mx','B012345678','SKU-REVISION') RETURNING id", (pid,)).fetchone()[0]
    fid = _ficha(c,pid,(lid,),observado=ahora,vence=ahora+dt.timedelta(days=30))
    ficha = FichaVersion(fid,pid,"amazon_mx",frozenset({lid}),(HechoConFuente("hecho","fuente"),),frozenset(),"aprobador",ahora,ahora+dt.timedelta(days=30),"a"*64)
    contrato = Contrato()
    clave = "CLAVE_FICTICIA_REVISION_12345"
    resultados = []
    for campo in ("model", "choice"):
        cuerpo = {"model":clave} if campo=="model" else {"model":contrato.modelo,"answers":{"relacion":{"type":"choice","choice":clave}}}
        # Solo la respuesta del proveedor es simulada; validacion/libro/PG reales.
        respuesta = SimpleNamespace(status_code=200,json=lambda:cuerpo)
        pedir = lambda termino, f: pedir_juicio(termino,f,contrato,api_key=clave,transporte=lambda *a,**k:respuesta)
        revision = _revision(c)
        Libro(c,pedir=pedir,contrato=contrato).juicio("collar",ficha,lote=revision,tope_diario=10)
        error = c.execute("SELECT error FROM jev_par_evento WHERE revision_id=%s AND tipo='resultado'", (revision,)).fetchone()[0]
        resultados.append({"campo":campo,"clave_ficticia_persistida":clave in error})
    return resultados


def control_productos():
    instante = dt.datetime.now(dt.UTC)
    hoy = instante.date()
    b = {"advertisedAsin":"B012345678","advertisedSku":"SKU", "date":(hoy-dt.timedelta(days=15)).isoformat()}
    buena = {**b,"cost":"130","clicks":100,"purchases30d":"0","sales30d":"0"}
    mala = {**b,"cost":"ilegible","purchases30d":"2","sales30d":"2000"}
    salidas=[]
    for filas in ([buena,mala],[mala,buena]):
        p,_=reports._planea_filas_productos(filas,ultimo_cerrado=ultimo_dia_cerrado("amazon_mx", instante),fecha_ini=hoy-dt.timedelta(days=30),fecha_fin=hoy)
        salidas.append(len(p))
    return salidas


def main():
    identidad()
    for nombre, caso in (("09",caso09),("05",caso05),("06",caso06),("14",caso14),("S1",casoS1)):
        with base() as (c,dsn):
            print(nombre, json.dumps(caso(c,dsn),default=str,ensure_ascii=False),flush=True)
    print("Control productos:",control_productos(),flush=True)
    print("LIMITES: cadena completa de migraciones segun Launch, pero se omiten DO/privilegios/roles; no certifica esos bloques. HTTP sintetico; sin APIs ni cron productivos. S1 condicional al eco del proveedor.",flush=True)


if __name__ == "__main__":
    main()
