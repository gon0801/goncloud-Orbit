"""REPRICING 02 0.b: la corrida detras del Mercado (seccion Q/I/N del diseno).

Prueba el paso con un Mercado falso (sin httpx ni SP-API): la corrida corre
de punta a punta resolviendo insumos desde la base, cotizando con una
cotizacion sintetica y escribiendo por el protocolo nuevo de `cambios`.
Tambien fija `catalogo` contra `_SQL_CANO` y `leer_cuenta` contra el
escenario que hoy arma la corrida.
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.types.json import Json
from test_precio_corrida import (
    ORDEN59,
    SETTINGS,
    _cadena,
    _config,
    _decision_unica,
    _goal,
    _listing,
    _price_obs,
    _producto,
)

RAIZ = Path(__file__).resolve().parents[1]

# ORDEN59 ya trae 0020 + 0047 + 0053 + 0054 (la corrida de hoy corre sobre
# la 0054; S.7 usa la 0020 para el trafico de la historia).
ORDEN02B = (*ORDEN59,)

_skip_sin_pg = pytest.mark.skipif(
    __import__("test_schema")._postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db02():
    """DB temporal con ORDEN59 + 0047 + 0053 + 0054; conn en autocommit."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02m_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN02B:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _hoy(conn) -> object:
    return conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]


# ---------------------------------------------------------------------------
# Mercado falso: insumos reales desde la base, cotizacion y escritura falsas
# ---------------------------------------------------------------------------


class _MercadoFalso:
    """Sustituye la red: cotiza ReferralFee 12 % + FBA 3 sobre el precio
    pedido y escribe moviendo su precio vivo. Los insumos salen de la base
    con el adaptador real (sin clientes)."""

    clave = "amazon_mx"
    moneda = "MXN"
    grupo_tasa = "spapi"

    def __init__(self, *, vivo="116.00"):
        from app.spapi.precio_mercado import MercadoAmazon

        self._real = MercadoAmazon("amazon_mx")
        self.vivo = Decimal(vivo)
        self.n_cotizaciones = 0
        self.n_escrituras = 0

    def catalogo(self, conn, *, hoy):
        return self._real.catalogo(conn, hoy=hoy)

    def insumos(self, conn, unidades, *, hoy, ahora, config, metas):
        return self._real.insumos(conn, unidades, hoy=hoy, ahora=ahora, config=config, metas=metas)

    def observado(self, conn, unidad, *, despues_de, hasta):
        return self._real.observado(conn, unidad, despues_de=despues_de, hasta=hasta)

    def cotizar(self, conn, oferta, precio):
        from app.precio.puerto import Cotizacion
        from app.precio.tipos import Fees, Importe

        self.n_cotizaciones += 1
        moneda = precio.moneda
        referral = (Decimal("0.12") * precio.valor).quantize(Decimal("0.01"))
        fba = Decimal("3.00")
        fees = Fees(
            total=Importe(referral + fba, moneda),
            variable=Importe(referral, moneda),
            fijo=Importe(fba, moneda),
            impuesto_pendiente=False,
        )
        return Cotizacion(precio=precio, fees=fees, estimada_en=datetime.now(UTC), error_code=None)

    def cotizacion_guardada(self, conn, *, evento, listing_id, hoy, oferta_id, pedido):
        return self._real.cotizacion_guardada(
            conn, evento=evento, listing_id=listing_id, hoy=hoy, oferta_id=oferta_id, pedido=pedido
        )

    def validar_destino(self, unidad) -> None:
        sku = unidad.miembros[0].seller_sku if unidad.miembros else ""
        if not sku:
            from app.precio.cambios import PublicacionSinSku

            raise PublicacionSinSku("listing_sin_sku: sin seller_sku no hay ruta que escribir")

    def precio_vivo(self, unidad):
        from app.precio.tipos import Importe

        return Importe(self.vivo, "MXN")

    def escribir(self, unidad, precio, *, sonda=False):
        from app.precio.puerto import Acuse

        self.n_escrituras += 1
        self.vivo = precio.valor
        return Acuse(aceptada=True, codigo=None, cuerpo={"falso": True})

    def cerrar(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. corrida de punta a punta con el Mercado falso
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_corrida_con_mercado_falso_sombra_inserta_virtual():
    from app.precio.corrida import correr

    with _db02() as conn:
        _cadena(conn, mode="shadow")
        _price_obs(conn)
        mercado = _MercadoFalso()
        res = correr(conn, mercado, owner="tdd")
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = _decision_unica(conn)
        assert fila[0] == "subir" and fila[2] == Decimal("127.60")
        virtual = conn.execute(
            "SELECT estado, confirmado_por, aplicado, precio_despues FROM precio_cambio"
        ).fetchone()
        assert virtual == ("confirmado", "virtual", False, Decimal("127.60"))
        assert mercado.n_escrituras == 0
        assert mercado.n_cotizaciones == 2


@_skip_sin_pg
def test_corrida_con_mercado_falso_live_aplica_y_sella():
    from app.precio.corrida import correr

    with _db02() as conn:
        _cadena(conn, mode="live")
        _price_obs(conn)
        mercado = _MercadoFalso()
        res = correr(conn, mercado, owner="tdd")
        assert (res.decisiones, res.escritas) == (1, 1)
        cambio = conn.execute("SELECT estado, precio_despues FROM precio_cambio").fetchone()
        assert cambio == ("enviado", Decimal("127.60"))
        assert mercado.n_escrituras == 1
        ack = conn.execute("SELECT ack FROM precio_cambio").fetchone()[0]
        assert ack == {"falso": True}


# ---------------------------------------------------------------------------
# 2. aplicar dos veces escribe una vez (ya_aplicado)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_aplicar_dos_veces_escribe_una_vez():
    from app.precio.cambios import aplicar

    with _db02() as conn:
        prod = _producto(conn, sku="ODOO-YA")
        lid = _listing(conn, prod, ext="B0YA0000001", sku="SKU-YA")
        _config(conn)
        _goal(conn, lid, mode="live")
        did = conn.execute(
            "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
            " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
            " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
            " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency,"
            " l_origen, verificacion)"
            " VALUES (%s, 'amazon_mx', 'subir', NULL, 'live', 0.30, 0.25,"
            " 100, 'MXN', 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN', 12, 'MXN',"
            " 5, 'MXN', 8, 'MXN', 'politica', 'cotizada') RETURNING id",
            (lid,),
        ).fetchone()[0]
        mercado = _MercadoFalso(vivo="100.00")
        primera = aplicar(conn, mercado, did)
        assert (primera.estado, primera.id_cambio) == ("enviado", primera.id_cambio)
        assert mercado.n_escrituras == 1
        segunda = aplicar(conn, mercado, did)
        assert (segunda.estado, segunda.motivo, segunda.id_cambio) == (
            "saltado",
            "ya_aplicado",
            None,
        )
        assert mercado.n_escrituras == 1
        total = conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0]
        assert total == 1


# ---------------------------------------------------------------------------
# 3. catalogo == _SQL_CANO (y el orden filtrar-antes-del-DISTINCT da otra lista)
# ---------------------------------------------------------------------------


def _estado_obs(conn, *, sku, platform="amazon_mx", status="BUYABLE", observed, listing_id=None):
    conn.execute(
        "INSERT INTO spapi_listing_estado_observation (seller_sku, platform, asin, status,"
        " api_version, observed_at)"
        " VALUES (%s, %s, %s, %s, 'v1', %s)",
        (sku, platform, f"B0CATALOGO{sku[-3:]}", status, observed),
    )


@_skip_sin_pg
def test_catalogo_coincide_con_sql_cano_y_el_orden_importa():
    from app.spapi.precio_mercado import _SQL_CANO, MercadoAmazon

    with _db02() as conn:
        hoy = _hoy(conn)
        mediodia = datetime(hoy.year, hoy.month, hoy.day, 12, tzinfo=UTC)
        # Tres activas BUYABLE...
        for i, sku in enumerate(("SKU-CAT-001", "SKU-CAT-002", "SKU-CAT-003"), start=1):
            prod = _producto(conn, sku=f"ODOO-{sku}")
            _listing(conn, prod, ext=f"B0CATALOGO{i:03d}", sku=sku)
            _estado_obs(conn, sku=sku, observed=mediodia - timedelta(days=i))
        # ...una no BUYABLE con la anterior si (el caso rojo del orden)...
        prod = _producto(conn, sku="ODOO-SKU-CAT-004")
        _listing(conn, prod, ext="B0CATALOGO004", sku="SKU-CAT-004")
        _estado_obs(
            conn, sku="SKU-CAT-004", status="BUYABLE", observed=mediodia - timedelta(days=1)
        )
        _estado_obs(
            conn, sku="SKU-CAT-004", status="DISCONTINUED", observed=mediodia - timedelta(hours=1)
        )
        # ...y una BUYABLE sin fila en listing (activa canonica huerfana).
        _estado_obs(conn, sku="SKU-CAT-005", observed=mediodia - timedelta(days=2))

        esperado = conn.execute(_SQL_CANO, ("amazon_mx",)).fetchall()
        catalogo = MercadoAmazon("amazon_mx").catalogo(conn, hoy=hoy)

        assert [u.seller_sku for u in catalogo] == [fila[1] for fila in esperado]
        assert len(catalogo) == 4  # 004 queda fuera: su ULTIMA observacion no es BUYABLE
        sin_listing = [u for u in catalogo if u.unidad is None]
        assert [u.seller_sku for u in sin_listing] == ["SKU-CAT-005"]
        con_listing = [u for u in catalogo if u.unidad is not None]
        assert {u.unidad.listing_id is not None for u in con_listing} == {True}
        assert {u.unidad.plataforma for u in con_listing} == {"amazon_mx"}
        assert {u.unidad.externo for u in con_listing} == {
            "B0CATALOGO001",
            "B0CATALOGO002",
            "B0CATALOGO003",
        }
        assert all(u.dias_sin_reportar >= 1 for u in catalogo)

        # Caso rojo documentado: filtrar BUYABLE ANTES del DISTINCT ON se
        # queda con la observacion vieja del 004 y da una lista distinta.
        invertido = conn.execute(
            "SELECT listing_id, seller_sku FROM ("
            " SELECT DISTINCT ON (e.seller_sku, e.platform)"
            " l.id AS listing_id, e.seller_sku"
            " FROM spapi_listing_estado_observation e"
            " LEFT JOIN listing l ON l.seller_sku = e.seller_sku AND l.platform = e.platform"
            " WHERE e.platform = %s AND e.status LIKE '%%BUYABLE%%'"
            " ORDER BY e.seller_sku, e.platform, e.observed_at DESC, l.id NULLS LAST"
            ") u ORDER BY u.seller_sku",
            ("amazon_mx",),
        ).fetchall()
        assert [f[1] for f in invertido] != [fila[1] for fila in esperado]
        assert "SKU-CAT-004" in [f[1] for f in invertido]


# ---------------------------------------------------------------------------
# 4. leer_cuenta == los importes que arma el adaptador (sucesor de armar_entrada)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_leer_cuenta_da_los_mismos_importes_que_la_entrada_del_adaptador():
    from app.estimacion_reader import leer_cuenta
    from app.spapi.precio_mercado import MercadoAmazon, MetaGoal

    with _db02() as conn:
        datos = _cadena(conn)
        _price_obs(conn)
        hoy = _hoy(conn)
        ahora = datetime.now(UTC)
        from app.precio.config import leer_config
        from app.precio.puerto import Miembro, UnidadPrecio

        unidad = UnidadPrecio(
            listing_id=datos["listing"],
            plataforma="amazon_mx",
            externo=datos["ext"],
            canal=None,
            producto_ancla=datos["producto"],
            miembros=(Miembro(datos["producto"], datos["sku"], None),),
        )
        config = leer_config(SETTINGS)
        lecturas = MercadoAmazon("amazon_mx").insumos(
            conn,
            (unidad,),
            hoy=hoy,
            ahora=ahora,
            config=config,
            metas={
                datos["listing"]: MetaGoal(
                    goal=Decimal("0.30"),
                    mode="shadow",
                    goal_vigente_desde=hoy - timedelta(days=30),
                )
            },
        )
        cuenta = leer_cuenta(conn, datos["listing"], as_of=ahora)
        entrada = lecturas[datos["listing"]].entrada
        assert cuenta is not None and entrada is not None
        esc = entrada.escenario
        assert cuenta.componentes == esc.componentes
        assert cuenta.fee_detalles == esc.fee_detalles
        assert cuenta.precio_cotizado == esc.precio_cotizado
        assert cuenta.iva_divisor == esc.iva_divisor
        assert cuenta.isr_tasa == esc.isr_tasa
        assert cuenta.precio_incluye_iva == esc.precio_incluye_iva
        assert cuenta.oferta_observada_en == esc.oferta_observada_en
        assert cuenta.tasas.iva_divisor == esc.iva_divisor
        assert cuenta.refs.escenario_id == lecturas[datos["listing"]].ids["escenario_id"]
        assert cuenta.canal == entrada.canal
        assert lecturas[datos["listing"]].motivo is None


# ---------------------------------------------------------------------------
# 5. goal_id, l_origen y verificacion en la fila de decision
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_toda_decision_guarda_goal_origen_y_verificacion():
    from app.precio.corrida import correr

    with _db02() as conn:
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        goal_id = conn.execute(
            "SELECT id FROM precio_goal WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()[0]
        res = correr(conn, _MercadoFalso(), owner="tdd")
        assert res.decisiones == 1
        fila = conn.execute(
            "SELECT goal_id, l_origen, verificacion, resultado FROM precio_decision"
        ).fetchone()
        assert fila == (goal_id, "politica", "cotizada", "subir")


@_skip_sin_pg
def test_decision_no_evaluada_tambien_guarda_goal_y_origen():
    from app.precio.corrida import correr

    with _db02() as conn:
        # Goal sin escenario: sale no_evaluado(precio_ausente) con sus refs.
        prod = _producto(conn, sku="ODOO-NE")
        lid = _listing(conn, prod, ext="B0NE0000001", sku="SKU-NE")
        otro = _listing(conn, _producto(conn, sku="ODOO-NE-M"), ext="B0NE0000002", sku="SKU-NE-M")
        from test_precio_corrida import _oferta_obs

        _oferta_obs(conn, otro, ext="B0NE0000002", sku="SKU-NE-M")
        _config(conn)
        goal_id = _goal(conn, lid, mode="shadow")
        res = correr(conn, _MercadoFalso(), owner="tdd")
        assert res.decisiones == 1
        fila = conn.execute(
            "SELECT goal_id, l_origen, verificacion, resultado, motivo FROM precio_decision"
        ).fetchone()
        assert fila == (goal_id, None, None, "no_evaluado", "precio_ausente")


# ---------------------------------------------------------------------------
# 6. escenario sin ReferralFee unico: lazy (en tolerancia pasa, subir no)
# ---------------------------------------------------------------------------


def _cadena_sin_referral(conn):
    """Cadena coherente cuyo fee_details no tiene ReferralFee unico.

    La tabla es append-only: se INSERTA la observacion corregida y se
    apunta el escenario a ella (nunca UPDATE in-place).
    """
    datos = _cadena(conn)
    oferta_vieja = conn.execute(
        "SELECT oferta_observation_id FROM estimacion_escenario"
        " WHERE listing_id = %s ORDER BY id DESC LIMIT 1",
        (datos["listing"],),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at, source_event_id,"
        " canonical_input, context_fingerprint)"
        " SELECT listing_id, platform, seller_sku, asin, canal,"
        " price_amount, price_currency, fetched_at - interval '3 seconds',"
        " observed_at - interval '3 seconds', 'oferta-sin-referral',"
        " canonical_input, 'ctx-sin-referral'"
        " FROM estimacion_oferta_observation WHERE id = %s",
        (oferta_vieja,),
    )
    oferta_nueva = conn.execute(
        "SELECT currval('estimacion_oferta_observation_id_seq')"
    ).fetchone()[0]
    fee_viejo = conn.execute(
        "SELECT fee_observation_id FROM estimacion_escenario"
        " WHERE listing_id = %s ORDER BY id DESC LIMIT 1",
        (datos["listing"],),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO estimacion_fee_observation (oferta_observation_id, listing_id, platform,"
        " seller_sku, asin, canal, quoted_price_amount, quoted_price_currency, total_fees,"
        " fee_details, fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        " canonical_input, context_fingerprint)"
        " SELECT %s, listing_id, platform, seller_sku, asin, canal,"
        " quoted_price_amount, quoted_price_currency, 15,"
        " %s, fees_estimated_at - interval '2 seconds',"
        " fetched_at - interval '2 seconds', observed_at - interval '2 seconds', estado,"
        " 'fee-sin-referral', canonical_input, 'ctx-sin-referral'"
        " FROM estimacion_fee_observation WHERE id = %s",
        (
            oferta_nueva,
            Json(
                [
                    {
                        "fee_type": "FbaFee",
                        "final_fee": "15",
                        "tax_amount": None,
                        "included_fee_details": [],
                    }
                ]
            ),
            fee_viejo,
        ),
    )
    fee_nuevo = conn.execute("SELECT currval('estimacion_fee_observation_id_seq')").fetchone()[0]
    conn.execute(
        "INSERT INTO estimacion_escenario (listing_id, platform, seller_sku, asin, canal,"
        " valoracion_date, observed_at, politica_version_id, formula_version,"
        " oferta_observation_id, fee_observation_id, sku_cost_id, costo_validation_run_id,"
        " costo_validated_at, moneda, contribucion, contribucion_pct, estado, motivos,"
        " componentes, exclusiones, canonical_input, context_fingerprint, source_event_id)"
        " SELECT listing_id, platform, seller_sku, asin, canal,"
        " valoracion_date, observed_at, politica_version_id, formula_version,"
        " %s, %s, sku_cost_id, costo_validation_run_id,"
        " costo_validated_at, moneda, contribucion, contribucion_pct, estado, motivos,"
        " componentes, exclusiones, canonical_input, 'ctx-sin-referral',"
        " 'esc-sin-referral'"
        " FROM estimacion_escenario WHERE listing_id = %s ORDER BY id DESC LIMIT 1",
        (oferta_nueva, fee_nuevo, datos["listing"]),
    )
    return datos


@_skip_sin_pg
def test_sin_referral_unico_en_tolerancia_mantiene_y_bajo_el_goal_no_evalua():
    from app.precio.config import leer_config
    from app.precio.reglas import decidir
    from app.spapi.precio_mercado import MercadoAmazon, MetaGoal

    with _db02() as conn:
        datos = _cadena_sin_referral(conn)
        _price_obs(conn)
        hoy = _hoy(conn)
        ahora = datetime.now(UTC)
        config = leer_config(SETTINGS)
        mercado = MercadoAmazon("amazon_mx")

        def _entrada(goal: Decimal):
            from app.precio.puerto import Miembro, UnidadPrecio

            unidad = UnidadPrecio(
                listing_id=datos["listing"],
                plataforma="amazon_mx",
                externo=datos["ext"],
                canal=None,
                producto_ancla=datos["producto"],
                miembros=(Miembro(datos["producto"], datos["sku"], None),),
            )
            lectura = mercado.insumos(
                conn,
                (unidad,),
                hoy=hoy,
                ahora=ahora,
                config=config,
                metas={
                    datos["listing"]: MetaGoal(
                        goal=goal,
                        mode="shadow",
                        goal_vigente_desde=hoy - timedelta(days=30),
                    )
                },
            )[datos["listing"]]
            assert lectura.entrada is not None
            return lectura.entrada, lectura.fees_escenario

        # fees_escenario llega como el motivo: partir los fees fallo.
        _e, fees = _entrada(Decimal("0.30"))
        assert fees == "fee_error:referral_ausente"

        # m=0.225 con goal 0.225: dentro de tolerancia -> mantener, sin tocar fees.
        entrada, fees = _entrada(Decimal("0.225"))
        d = decidir(entrada, hoy=hoy, config=config, fees_escenario=fees)
        assert (d.resultado, d.motivo) == ("mantener", "en_tolerancia")

        # Bajo el goal (0.30): _subir convierte el str en no_evaluado.
        entrada, fees = _entrada(Decimal("0.30"))
        d = decidir(entrada, hoy=hoy, config=config, fees_escenario=fees)
        assert (d.resultado, d.motivo) == ("no_evaluado", "fee_error:referral_ausente")


# ---------------------------------------------------------------------------
# 7. F.4: historia de la unidad (disponibilidad por canal, ventas de la vista)
# ---------------------------------------------------------------------------


def _run_ledger(conn, *, source="accounting_ledger_events", ok=True):
    marca = datetime.now(UTC)
    return conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
        " rows_skipped, ok) VALUES (%s, %s, %s, 1, 0, %s) RETURNING id",
        (source, marca, marca, ok),
    ).fetchone()[0]


def _venta(conn, *, pid, dia, qty, evento, run):
    return conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, source_event_id, ingest_run_id)"
        " VALUES ('amazon_mx', 'sale', %s, %s, %s, 100, 'MXN', %s, %s) RETURNING id",
        (dia, pid, qty, evento, run),
    ).fetchone()[0]


def _atribuir(conn, *, evento_id, lid, run, por="sku"):
    conn.execute(
        "INSERT INTO ledger_event_atribucion"
        " (ledger_event_id, platform, listing_id, variante_ext, resuelto_por, ingest_run_id)"
        " VALUES (%s, 'amazon_mx', %s, NULL, %s, %s)",
        (evento_id, lid, por, run),
    )


def _inventario(conn, *, sku, dia, qty, hora=12):
    obs = datetime(dia.year, dia.month, dia.day, hora, tzinfo=UTC)
    conn.execute(
        "INSERT INTO spapi_inventario_observation (seller_sku, platform, metric_date,"
        " observed_at, total_quantity, api_version) VALUES (%s, 'amazon_mx', %s, %s, %s, 'v1')",
        (sku, dia, obs, qty),
    )


def _ads(conn, *, asin, sku, dia, obs_hora=12, impresiones=None, clics=None):
    obs = datetime(dia.year, dia.month, dia.day, obs_hora, tzinfo=UTC)
    run = _run_ledger(conn, source="ads_product_metrics")
    conn.execute(
        "INSERT INTO ads_product_metric_observation (platform, advertised_asin,"
        " advertised_sku, metric_date, observed_at, metric_currency, impressions,"
        " clicks, ingest_run_id)"
        " VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN', %s, %s, %s)",
        (asin, sku, dia, obs, impresiones, clics, run),
    )


def _unidad_f4(pid, lid, *, sku, ext):
    from app.precio.puerto import Miembro, UnidadPrecio

    return UnidadPrecio(
        listing_id=lid,
        plataforma="amazon_mx",
        externo=ext,
        canal=None,
        producto_ancla=pid,
        miembros=(Miembro(pid, sku, None),),
    )


def _mediodia(dia, hora=12):
    return datetime(dia.year, dia.month, dia.day, hora, tzinfo=UTC)


def _decision_s7(conn, lid, *, mode="shadow"):
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency,"
        " l_origen, verificacion)"
        " VALUES (%s, 'amazon_mx', 'subir', NULL, %s, 0.30, 0.25,"
        " 100, 'MXN', 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN', 12, 'MXN',"
        " 5, 'MXN', 8, 'MXN', 'politica', 'cotizada') RETURNING id",
        (lid, mode),
    ).fetchone()[0]


def _cambio_subida(conn, *, lid, did, dia):
    cid = conn.execute(
        "INSERT INTO precio_cambio (listing_id, platform, decision_id, estado,"
        " aplicado, precio_antes, precio_antes_currency,"
        " precio_despues, precio_despues_currency, enviado_at, es_reversa)"
        " VALUES (%s, 'amazon_mx', %s, 'pendiente', TRUE,"
        " 100, 'MXN', 110, 'MXN', %s, FALSE) RETURNING id",
        (lid, did, _mediodia(dia)),
    ).fetchone()[0]
    conn.execute(
        "UPDATE precio_cambio SET estado = 'enviado',"
        " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
        (cid,),
    )
    conn.execute(
        "UPDATE precio_cambio SET estado = 'confirmado',"
        " confirmado_por = 'observacion' WHERE id = %s",
        (cid,),
    )


@_skip_sin_pg
def test_historia_fbm_disponibilidad_sale_del_estado_e_ignora_inventario():
    from app.spapi.precio_mercado import historia_unidad

    with _db02() as conn:
        pid = _producto(conn, sku="ODOO-F4-H1")
        lid = _listing(conn, pid, ext="B0F4H10001", sku="SS-F4-H1")
        hoy = _hoy(conn)
        d1, d2, d3 = hoy - timedelta(days=3), hoy - timedelta(days=2), hoy - timedelta(days=1)
        _estado_obs(conn, sku="SS-F4-H1", observed=_mediodia(d1))
        _estado_obs(conn, sku="SS-F4-H1", status="DISCONTINUED", observed=_mediodia(d2))
        _inventario(conn, sku="SS-F4-H1", dia=d1, qty=0)
        run = _run_ledger(conn)
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=d3, qty=2, evento="evt-h1", run=run),
            lid=lid,
            run=run,
        )
        historia = historia_unidad(
            conn,
            unidad=_unidad_f4(pid, lid, sku="SS-F4-H1", ext="B0F4H10001"),
            canal="fbm",
            hoy=hoy,
        )
        por_dia = {d.dia: d for d in historia.dias}
        assert por_dia[d1].disponibilidad == "disponible"
        assert por_dia[d1].unidades == 0
        assert por_dia[d2].disponibilidad == "no_disponible"
        assert por_dia[d3].disponibilidad == "sin_observacion"
        assert por_dia[d3].unidades == 2


@_skip_sin_pg
def test_historia_fba_exige_stock_y_buyable():
    from app.spapi.precio_mercado import historia_unidad

    with _db02() as conn:
        pid = _producto(conn, sku="ODOO-F4-H2")
        lid = _listing(conn, pid, ext="B0F4H20001", sku="SS-F4-H2")
        hoy = _hoy(conn)
        d1 = hoy - timedelta(days=5)
        d2 = hoy - timedelta(days=4)
        d3 = hoy - timedelta(days=3)
        d4 = hoy - timedelta(days=2)
        d5 = hoy - timedelta(days=1)
        _estado_obs(conn, sku="SS-F4-H2", observed=_mediodia(d1))
        _inventario(conn, sku="SS-F4-H2", dia=d1, qty=5)
        _estado_obs(conn, sku="SS-F4-H2", observed=_mediodia(d2))
        _inventario(conn, sku="SS-F4-H2", dia=d2, qty=0)
        _estado_obs(conn, sku="SS-F4-H2", observed=_mediodia(d3))
        _estado_obs(conn, sku="SS-F4-H2", observed=_mediodia(d4))
        _estado_obs(conn, sku="SS-F4-H2", status="DISCONTINUED", observed=_mediodia(d4, 15))
        _inventario(conn, sku="SS-F4-H2", dia=d4, qty=5)
        _inventario(conn, sku="SS-F4-H2", dia=d5, qty=5)
        historia = historia_unidad(
            conn,
            unidad=_unidad_f4(pid, lid, sku="SS-F4-H2", ext="B0F4H20001"),
            canal="fba",
            hoy=hoy,
        )
        por_dia = {d.dia: d for d in historia.dias}
        assert por_dia[d1].disponibilidad == "disponible"
        assert por_dia[d2].disponibilidad == "no_disponible"
        assert por_dia[d3].disponibilidad == "sin_observacion"
        assert por_dia[d4].disponibilidad == "no_disponible"
        assert por_dia[d5].disponibilidad == "sin_observacion"


@_skip_sin_pg
def test_historia_sin_atribuir_solo_ventana():
    from app.spapi.precio_mercado import historia_unidad

    with _db02() as conn:
        pid = _producto(conn, sku="ODOO-F4-H3B")
        lid = _listing(conn, pid, ext="B0F4H30011", sku="SS-F4-H3B")
        hoy = _hoy(conn)
        run = _run_ledger(conn)
        _venta(conn, pid=pid, dia=hoy - timedelta(days=76), qty=9, evento="evt-h3b-vieja", run=run)
        _venta(conn, pid=pid, dia=hoy - timedelta(days=75), qty=2, evento="evt-h3b-borde", run=run)
        _venta(conn, pid=pid, dia=hoy, qty=3, evento="evt-h3b-hoy", run=run)
        historia = historia_unidad(
            conn,
            unidad=_unidad_f4(pid, lid, sku="SS-F4-H3B", ext="B0F4H30011"),
            canal="fbm",
            hoy=hoy,
        )
        assert historia.sin_atribuir == 2


@_skip_sin_pg
def test_historia_ventas_salen_de_la_vista_y_cuenta_sin_atribuir():
    from app.spapi.precio_mercado import historia_unidad

    with _db02() as conn:
        pid = _producto(conn, sku="ODOO-F4-H3")
        lid = _listing(conn, pid, ext="B0F4H30001", sku="SS-F4-H3")
        otro = _listing(conn, pid, ext="B0F4H30002", sku="SS-F4-H3B")
        hoy = _hoy(conn)
        d0 = hoy - timedelta(days=10)
        d1 = hoy - timedelta(days=3)
        d2 = hoy - timedelta(days=2)
        run = _run_ledger(conn)
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=d1, qty=2, evento="evt-h3-a", run=run),
            lid=lid,
            run=run,
        )
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=d1, qty=3, evento="evt-h3-b", run=run),
            lid=lid,
            run=run,
        )
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=d0, qty=1, evento="evt-h3-c", run=run),
            lid=lid,
            run=run,
        )
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=d1, qty=7, evento="evt-h3-d", run=run),
            lid=otro,
            run=run,
        )
        _venta(conn, pid=pid, dia=d1, qty=4, evento="evt-h3-e", run=run)
        _venta(conn, pid=pid, dia=d2, qty=1, evento="evt-h3-f", run=run)
        historia = historia_unidad(
            conn,
            unidad=_unidad_f4(pid, lid, sku="SS-F4-H3", ext="B0F4H30001"),
            canal="fbm",
            hoy=hoy,
        )
        por_dia = {d.dia: d for d in historia.dias}
        assert por_dia[d1].unidades == 5
        assert por_dia[d0].unidades == 1
        assert d2 not in por_dia
        assert historia.sin_atribuir == 5
        assert historia.primera_venta == d0


@_skip_sin_pg
def test_historia_trafico_y_buy_box_del_dia():
    from app.spapi.precio_mercado import historia_unidad

    with _db02() as conn:
        pid = _producto(conn, sku="ODOO-F4-H4")
        lid = _listing(conn, pid, ext="B0F4H40001", sku="SS-F4-H4")
        hoy = _hoy(conn)
        d1 = hoy - timedelta(days=2)
        d2 = hoy - timedelta(days=1)
        _ads(conn, asin="B0F4H40001", sku="SS-F4-H4", dia=d1, obs_hora=10, impresiones=100, clics=5)
        _ads(conn, asin="B0F4H40001", sku="SS-F4-H4", dia=d1, obs_hora=14, impresiones=120, clics=7)
        _ads(conn, asin="B0F4H40001", sku="SS-F4-H4", dia=d2, impresiones=50, clics=None)
        _price_obs(conn, asin="B0F4H40001", dia=d1, observada=_mediodia(d1, 10), buybox=True)
        _price_obs(conn, asin="B0F4H40001", dia=d1, observada=_mediodia(d1, 15), buybox=False)
        historia = historia_unidad(
            conn,
            unidad=_unidad_f4(pid, lid, sku="SS-F4-H4", ext="B0F4H40001"),
            canal="fbm",
            hoy=hoy,
        )
        por_dia = {d.dia: d for d in historia.dias}
        assert (por_dia[d1].exposiciones, por_dia[d1].clics) == (120, 7)
        assert por_dia[d1].buy_box_propia is False
        assert (por_dia[d2].exposiciones, por_dia[d2].clics) == (50, None)
        assert por_dia[d2].buy_box_propia is None


@_skip_sin_pg
def test_historia_ventana_76_dias_primera_y_cubierto():
    from app.spapi.precio_mercado import historia_unidad

    with _db02() as conn:
        pid = _producto(conn, sku="ODOO-F4-H5")
        lid = _listing(conn, pid, ext="B0F4H50001", sku="SS-F4-H5")
        hoy = _hoy(conn)
        vieja = hoy - timedelta(days=80)
        borde = hoy - timedelta(days=75)
        reciente = hoy - timedelta(days=1)
        run = _run_ledger(conn)
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=vieja, qty=1, evento="evt-h5-a", run=run),
            lid=lid,
            run=run,
        )
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=pid, dia=borde, qty=1, evento="evt-h5-b", run=run),
            lid=lid,
            run=run,
        )
        _venta(conn, pid=pid, dia=reciente, qty=1, evento="evt-h5-c", run=run)
        historia = historia_unidad(
            conn,
            unidad=_unidad_f4(pid, lid, sku="SS-F4-H5", ext="B0F4H50001"),
            canal="fbm",
            hoy=hoy,
        )
        assert historia.primera_venta == vieja
        assert historia.ledger_cubierto_hasta == reciente
        assert [d.dia for d in historia.dias] == [borde]
        assert all(hoy - timedelta(days=75) <= d.dia <= hoy for d in historia.dias)


@_skip_sin_pg
def test_insumos_adjunta_historia_a_la_lectura():
    from app.precio.config import leer_config
    from app.precio.puerto import HistoriaUnidad
    from app.spapi.precio_mercado import MercadoAmazon, MetaGoal

    with _db02() as conn:
        datos = _cadena(conn)
        _price_obs(conn)
        hoy = _hoy(conn)
        ahora = datetime.now(UTC)
        dia = hoy - timedelta(days=1)
        _estado_obs(conn, sku=datos["sku"], observed=_mediodia(dia))
        _inventario(conn, sku=datos["sku"], dia=dia, qty=3)
        run = _run_ledger(conn)
        _atribuir(
            conn,
            evento_id=_venta(conn, pid=datos["producto"], dia=dia, qty=2, evento="evt-h6", run=run),
            lid=datos["listing"],
            run=run,
        )
        config = leer_config(SETTINGS)
        lectura = MercadoAmazon("amazon_mx").insumos(
            conn,
            (_unidad_f4(datos["producto"], datos["listing"], sku=datos["sku"], ext=datos["ext"]),),
            hoy=hoy,
            ahora=ahora,
            config=config,
            metas={
                datos["listing"]: MetaGoal(
                    goal=Decimal("0.30"),
                    mode="shadow",
                    goal_vigente_desde=hoy - timedelta(days=30),
                )
            },
        )[datos["listing"]]
        assert lectura.entrada is not None
        assert isinstance(lectura.historia, HistoriaUnidad)
        por_dia = {d.dia: d for d in lectura.historia.dias}
        assert por_dia[dia].disponibilidad == "disponible"
        assert por_dia[dia].unidades == 2
        assert lectura.historia.sin_atribuir == 0


# ---------------------------------------------------------------------------
# 8. S.7: el motor usa la senal por unidad
# ---------------------------------------------------------------------------


def _metas_s7(lid, hoy):
    from app.spapi.precio_mercado import MetaGoal

    return {
        lid: MetaGoal(
            goal=Decimal("0.30"),
            mode="shadow",
            goal_vigente_desde=hoy - timedelta(days=30),
        )
    }


@_skip_sin_pg
def test_senal_por_unidad_no_ve_las_ventas_de_otra_publicacion():
    from app.precio.config import leer_config
    from app.spapi.precio_mercado import MercadoAmazon

    with _db02() as conn:
        datos = _cadena(conn)
        _price_obs(conn)
        hoy = _hoy(conn)
        ahora = datetime.now(UTC)
        otro = _listing(conn, datos["producto"], ext="B0F4W10002", sku="SKU-W1-B")
        run = _run_ledger(conn)
        _atribuir(
            conn,
            evento_id=_venta(
                conn,
                pid=datos["producto"],
                dia=hoy - timedelta(days=200),
                qty=1,
                evento="evt-w1-vieja",
                run=run,
            ),
            lid=datos["listing"],
            run=run,
        )
        _atribuir(
            conn,
            evento_id=_venta(
                conn,
                pid=datos["producto"],
                dia=hoy - timedelta(days=2),
                qty=5,
                evento="evt-w1-otra",
                run=run,
            ),
            lid=otro,
            run=run,
        )
        lectura = MercadoAmazon("amazon_mx").insumos(
            conn,
            (_unidad_f4(datos["producto"], datos["listing"], sku=datos["sku"], ext=datos["ext"]),),
            hoy=hoy,
            ahora=ahora,
            config=leer_config(SETTINGS),
            metas=_metas_s7(datos["listing"], hoy),
        )[datos["listing"]]
        senal = lectura.entrada.senal
        assert (senal.estado, senal.submotivo, senal.u60) == (
            "sin_dato",
            "u60_bajo_minimo",
            0,
        )


@_skip_sin_pg
def test_senal_usa_ultima_subida_y_trafico():
    from app.precio.config import leer_config
    from app.spapi.precio_mercado import MercadoAmazon

    with _db02() as conn:
        datos = _cadena(conn)
        hoy = _hoy(conn)
        conn.execute(
            "UPDATE precio_goal SET valid_to = %s WHERE listing_id = %s AND valid_to IS NULL",
            (hoy, datos["listing"]),
        )
        _goal(conn, datos["listing"], mode="live", desde=hoy)
        _price_obs(conn)
        ahora = datetime.now(UTC)
        subida = hoy - timedelta(days=10)
        _cambio_subida(
            conn,
            lid=datos["listing"],
            did=_decision_s7(conn, datos["listing"], mode="live"),
            dia=subida,
        )
        run = _run_ledger(conn)
        _atribuir(
            conn,
            evento_id=_venta(
                conn,
                pid=datos["producto"],
                dia=hoy - timedelta(days=200),
                qty=1,
                evento="evt-w2-vieja",
                run=run,
            ),
            lid=datos["listing"],
            run=run,
        )
        for i in range(20, 40):
            dia = hoy - timedelta(days=i)
            _atribuir(
                conn,
                evento_id=_venta(
                    conn,
                    pid=datos["producto"],
                    dia=dia,
                    qty=1,
                    evento=f"evt-w2-60-{i}",
                    run=run,
                ),
                lid=datos["listing"],
                run=run,
            )
        _atribuir(
            conn,
            evento_id=_venta(
                conn,
                pid=datos["producto"],
                dia=hoy - timedelta(days=2),
                qty=2,
                evento="evt-w2-15",
                run=run,
            ),
            lid=datos["listing"],
            run=run,
        )
        for d in range(11, 39):
            _ads(
                conn,
                asin=datos["ext"],
                sku=datos["sku"],
                dia=hoy - timedelta(days=d),
                impresiones=200,
                clics=20,
            )
        for d in range(1, 10):
            _ads(
                conn,
                asin=datos["ext"],
                sku=datos["sku"],
                dia=hoy - timedelta(days=d),
                impresiones=200,
                clics=8,
            )
        lectura = MercadoAmazon("amazon_mx").insumos(
            conn,
            (_unidad_f4(datos["producto"], datos["listing"], sku=datos["sku"], ext=datos["ext"]),),
            hoy=hoy,
            ahora=ahora,
            config=leer_config(SETTINGS),
            metas=_metas_s7(datos["listing"], hoy),
        )[datos["listing"]]
        senal = lectura.entrada.senal
        assert (senal.estado, senal.evidencia) == ("perdiendo", "trafico")


@_skip_sin_pg
def test_senal_sin_clave_de_trafico_falla_fuerte():
    from test_precio_corrida import _config as _config_corrida

    from app.precio.config import leer_config
    from app.spapi.precio_mercado import MercadoAmazon

    with _db02() as conn:
        datos = _cadena(conn)
        _price_obs(conn)
        hoy = _hoy(conn)
        ahora = datetime.now(UTC)
        ajuste = {k: v for k, v in SETTINGS.items() if k != "precio_senal_trafico_min"}
        _config_corrida(conn, ajuste)
        with pytest.raises(ValueError, match="precio_senal_trafico_min"):
            MercadoAmazon("amazon_mx").insumos(
                conn,
                (
                    _unidad_f4(
                        datos["producto"], datos["listing"], sku=datos["sku"], ext=datos["ext"]
                    ),
                ),
                hoy=hoy,
                ahora=ahora,
                config=leer_config(SETTINGS),
                metas=_metas_s7(datos["listing"], hoy),
            )


def test_ultima_subida_ignora_lo_que_no_movio_precio():
    from datetime import date

    from app.precio.tipos import CambioPrevio
    from app.spapi.precio_mercado import _ultima_subida

    d1 = date(2026, 9, 1)
    d2 = date(2026, 9, 5)
    d3 = date(2026, 9, 9)
    cambios = (
        CambioPrevio(d1, "subir", "confirmado", aplicado=True, sin_efecto=False),
        CambioPrevio(d2, "subir", "error", aplicado=False, sin_efecto=False),
        CambioPrevio(d3, "bajar", "confirmado", aplicado=True, sin_efecto=False),
        CambioPrevio(d2, "subir", "confirmado", aplicado=True, sin_efecto=True),
    )
    assert _ultima_subida(cambios) == d1
    assert _ultima_subida(()) is None


def test_ultima_subida_ignora_virtual_confirmado():
    from datetime import date

    from app.precio.tipos import CambioPrevio
    from app.spapi.precio_mercado import _ultima_subida

    d1 = date(2026, 9, 1)
    d2 = date(2026, 9, 5)
    cambios = (
        CambioPrevio(d1, "subir", "confirmado", aplicado=True, sin_efecto=False),
        CambioPrevio(d2, "subir", "confirmado", aplicado=False, sin_efecto=False),
    )
    assert _ultima_subida(cambios) == d1
    assert _ultima_subida(cambios[1:]) is None


@_skip_sin_pg
def test_persistir_uno_guarda_procedencia_real_y_evidencia():
    import test_precio_reglas as reglas_fx

    from app.precio.corrida import _persistir_uno
    from app.precio.reglas import decidir

    with _db02() as conn:
        datos = _cadena(conn)
        cfg_id = conn.execute("SELECT max(id) FROM config_version").fetchone()[0]
        goal_id = conn.execute(
            "SELECT id FROM precio_goal WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()[0]
        muestra = conn.execute(
            "INSERT INTO precio_envio_muestra (product_id, platform, canal,"
            " ventana_desde, ventana_hasta, envios, percentil, valor,"
            " valor_currency, mediana, mediana_currency, p90, p90_currency,"
            " maximo, maximo_currency, origen, regla_version)"
            " VALUES (%s, 'amazon_mx', 'fbm', '2026-03-12', '2026-09-07', 4,"
            " 'mediana', 30, 'MXN', 30, 'MXN', 30, 'MXN', 30, 'MXN', 'propio',"
            " 'f2.1') RETURNING id",
            (datos["producto"],),
        ).fetchone()[0]
        ent = reglas_fx.entrada(costo="60", mode="shadow")
        decision = decidir(
            ent,
            hoy=reglas_fx.HOY,
            config=reglas_fx.cfg(),
            cotizaciones=(),
            fees_escenario=reglas_fx.fees_escenario_de(ent),
            lineal=True,
        )
        _persistir_uno(
            conn,
            decision,
            lid=datos["listing"],
            mode="shadow",
            prod_id=datos["producto"],
            canal="fbm",
            meta={
                "escenario_id": None,
                "fee_observation_id": None,
                "oferta_observation_id": None,
                "cotizacion_id": None,
                "canal": "fbm",
                "logistica_origen": "propio",
                "envio_muestra_id": muestra,
            },
            platform="amazon_mx",
            goal_id=goal_id,
            cfg_id=cfg_id,
            lineas=[],
            errores=[],
            senal_evidencia="trafico",
        )
        fila = conn.execute(
            "SELECT l_origen, envio_muestra_id, senal_evidencia FROM precio_decision"
            " WHERE listing_id = %s ORDER BY id DESC LIMIT 1",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("propio", muestra, "trafico")


@_skip_sin_pg
def test_persistir_uno_guarda_verificacion_lineal():
    import test_precio_reglas as reglas_fx

    from app.precio.corrida import _persistir_uno
    from app.precio.reglas import decidir

    with _db02() as conn:
        datos = _cadena(conn)
        cfg_id = conn.execute("SELECT max(id) FROM config_version").fetchone()[0]
        goal_id = conn.execute(
            "SELECT id FROM precio_goal WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()[0]
        ent = reglas_fx.entrada(costo="60", mode="shadow")
        decision = decidir(
            ent,
            hoy=reglas_fx.HOY,
            config=reglas_fx.cfg(),
            cotizaciones=(),
            fees_escenario=reglas_fx.fees_escenario_de(ent),
            lineal=True,
        )
        assert (decision.resultado, decision.verificacion) == ("subir", "lineal")
        lineas: list = []
        errores: list = []
        _persistir_uno(
            conn,
            decision,
            lid=datos["listing"],
            mode="shadow",
            prod_id=datos["producto"],
            canal="fba",
            meta={
                "escenario_id": None,
                "fee_observation_id": None,
                "oferta_observation_id": None,
                "cotizacion_id": None,
                "canal": "fba",
                "logistica_origen": "politica",
                "envio_muestra_id": None,
            },
            platform="amazon_mx",
            goal_id=goal_id,
            cfg_id=cfg_id,
            lineas=lineas,
            errores=errores,
        )
        fila = conn.execute(
            "SELECT verificacion FROM precio_decision WHERE listing_id = %s"
            " ORDER BY id DESC LIMIT 1",
            (datos["listing"],),
        ).fetchone()[0]
        assert fila == "lineal"
