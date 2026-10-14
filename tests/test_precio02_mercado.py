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

# ORDEN59 ya trae 0047 + 0053 + 0054 (la corrida de hoy corre sobre la 0054).
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
            " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency)"
            " VALUES (%s, 'amazon_mx', 'subir', NULL, 'live', 0.30, 0.25,"
            " 100, 'MXN', 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN', 12, 'MXN',"
            " 5, 'MXN', 8, 'MXN') RETURNING id",
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
        assert fila == (goal_id, "politica", None, "no_evaluado", "precio_ausente")


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
