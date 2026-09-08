"""Wire S5 de estimacion: proyeccion pura y attach batch.

detalle siempre esta en el sobre. Vale None si estado==disponible o si
canonical_input.resultado no trae contribucion ni contribucion_pct numericos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from test_schema import _postgres_obligatorio_ausente

CLAVES_S5 = (
    "estado",
    "motivos",
    "snapshot_id",
    "escenario",
    "moneda",
    "contribucion",
    "contribucion_pct",
    "base_porcentaje",
    "componentes",
    "exclusiones",
    "detalle",
)

CLAVES_COMPONENTE = (
    "nombre",
    "importe_original",
    "moneda_original",
    "importe_normalizado",
    "moneda_normalizada",
    "fuente",
    "fecha_fuente",
    "observed_at",
    "vigencia",
    "estado",
    "pertenencia",
)

STUB = {
    "estado": "incompleta",
    "motivos": ["escenario_ausente"],
    "snapshot_id": None,
    "escenario": {
        "unidad": None,
        "canal": None,
        "fecha_valoracion": None,
        "version_formula": None,
        "version_politica": None,
    },
    "moneda": None,
    "contribucion": None,
    "contribucion_pct": None,
    "base_porcentaje": "ingreso_normalizado",
    "componentes": [],
    "exclusiones": [],
    "detalle": None,
}

AS_OF = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _escenario(**overrides):
    from app.estimacion_repository import EscenarioLeido

    datos = dict(
        id=10,
        listing_id=1,
        canal="fba",
        valoracion_date=date(2026, 9, 8),
        observed_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
        estado="disponible",
        motivos=(),
        contribucion=Decimal("42.5000"),
        contribucion_pct=Decimal("36.6379"),
        moneda="MXN",
        componentes=[],
        exclusiones=("ads",),
        canonical_input={
            "resultado": {
                "estado": "disponible",
                "contribucion": "42.5000",
                "contribucion_pct": "36.6379",
            }
        },
        context_fingerprint="fp",
        politica_version_id=3,
        formula_version="S3",
    )
    datos.update(overrides)
    return EscenarioLeido(**datos)


def test_proyeccion_s5_stub_escenario_ausente():
    from app.estimacion_proyeccion import proyeccion_s5

    sobre = proyeccion_s5(None)
    assert sobre == STUB
    for clave in CLAVES_S5:
        assert clave in sobre


def test_proyeccion_s5_decimal_como_cadena_y_null_se_queda_null():
    from app.estimacion_proyeccion import proyeccion_s5

    sobre = proyeccion_s5(_escenario())
    assert sobre["contribucion"] == "42.5000"
    assert sobre["contribucion_pct"] == "36.6379"
    assert isinstance(sobre["contribucion"], str)
    assert isinstance(sobre["contribucion_pct"], str)
    assert sobre["moneda"] == "MXN"
    assert sobre["snapshot_id"] == 10
    assert sobre["base_porcentaje"] == "ingreso_normalizado"
    assert sobre["detalle"] is None

    nulo = proyeccion_s5(
        _escenario(
            estado="incompleta",
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            canonical_input={"resultado": {"estado": "incompleta", "contribucion": None}},
        )
    )
    assert nulo["contribucion"] is None
    assert nulo["contribucion_pct"] is None
    assert nulo["moneda"] is None


def test_proyeccion_s5_no_disponible_fuerza_principales_null():
    from app.estimacion_proyeccion import proyeccion_s5

    sobre = proyeccion_s5(
        _escenario(
            estado="desactualizada",
            motivos=("oferta_desactualizada",),
            contribucion=Decimal("42.5000"),
            contribucion_pct=Decimal("36.6379"),
            moneda="MXN",
            canonical_input={
                "resultado": {
                    "estado": "disponible",
                    "contribucion": "42.5000",
                    "contribucion_pct": "36.6379",
                }
            },
        )
    )
    assert sobre["contribucion"] is None
    assert sobre["contribucion_pct"] is None
    assert sobre["estado"] == "desactualizada"
    assert sobre["detalle"] == {
        "contribucion": "42.5000",
        "contribucion_pct": "36.6379",
        "estado": "disponible",
    }


def test_proyeccion_s5_detalle_omitido_si_no_hay_numero_congelado():
    from app.estimacion_proyeccion import proyeccion_s5

    sobre = proyeccion_s5(
        _escenario(
            estado="incompleta",
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            canonical_input={
                "resultado": {
                    "estado": "incompleta",
                    "contribucion": None,
                    "contribucion_pct": None,
                }
            },
        )
    )
    assert "detalle" in sobre
    assert sobre["detalle"] is None


def test_proyeccion_s5_componente_pertenencia_sin_promover_fecha():
    from app.estimacion_proyeccion import proyeccion_s5

    sobre = proyeccion_s5(
        _escenario(
            componentes=[
                {
                    "nombre": "isr",
                    "importe_original": "2.5000",
                    "moneda_original": "MXN",
                    "importe_normalizado": "2.5000",
                    "moneda_normalizada": "MXN",
                    "fuente": "politica",
                    "tasa": "0.025",
                    "fecha": "2026-09-08",
                    "pertenece_a_total": True,
                },
                {"nombre": "costo_original", "pertenece_a_total": False},
                {"nombre": "fee_detalle:ReferralFee"},
            ]
        )
    )
    isr, costo, fee = sobre["componentes"]
    for comp in (isr, costo, fee):
        assert tuple(comp) == CLAVES_COMPONENTE or set(CLAVES_COMPONENTE) <= set(comp)
        assert "tasa" not in comp
        assert "fecha" not in comp
        assert "pertenece_a_total" not in comp
        assert comp["fecha_fuente"] is None
        assert comp["observed_at"] is None
        assert comp["vigencia"] is None
        assert comp["estado"] is None
    assert isr["nombre"] == "isr"
    assert isr["importe_original"] == "2.5000"
    assert isr["pertenencia"] is True
    assert costo["pertenencia"] is False
    assert costo["importe_original"] is None
    assert fee["pertenencia"] is None
    assert sobre["escenario"] == {
        "unidad": None,
        "canal": "fba",
        "fecha_valoracion": "2026-09-08",
        "version_formula": "S3",
        "version_politica": 3,
    }
    assert sobre["exclusiones"] == ["ads"]


def test_adjuntar_estimaciones_rechaza_as_of_naive():
    from app.estimacion_proyeccion import adjuntar_estimaciones

    with pytest.raises(ValueError, match="zona horaria"):
        adjuntar_estimaciones(object(), [1], as_of=datetime(2026, 9, 8, 12, 0, 0))


def test_adjuntar_estimaciones_stub_si_reader_omite(monkeypatch):
    from app.estimacion_proyeccion import adjuntar_estimaciones

    llamadas = []

    def fake_leer(conn, listing_ids, *, as_of):
        llamadas.append((list(listing_ids), as_of))
        return []

    monkeypatch.setattr("app.estimacion_proyeccion.leer_escenarios", fake_leer)
    mapa = adjuntar_estimaciones(object(), [7, 8], as_of=AS_OF)
    assert llamadas == [([7, 8], AS_OF)]
    assert mapa[7] == STUB
    assert mapa[8] == STUB


def test_adjuntar_estimaciones_una_sola_llamada_a_leer_escenarios(monkeypatch):
    from app.estimacion_proyeccion import adjuntar_estimaciones

    llamadas = []

    def fake_leer(conn, listing_ids, *, as_of):
        llamadas.append(list(listing_ids))
        return [_escenario(id=11, listing_id=listing_ids[0])]

    monkeypatch.setattr("app.estimacion_proyeccion.leer_escenarios", fake_leer)
    mapa = adjuntar_estimaciones(object(), [11, 12], as_of=AS_OF)
    assert llamadas == [[11, 12]]
    assert mapa[11]["snapshot_id"] == 11
    assert mapa[12] == STUB


@_skip_db
def test_adjuntar_as_of_temprano_no_usa_filas_futuras():
    from app.estimacion_proyeccion import adjuntar_estimaciones
    from psycopg.types.json import Json
    from test_estimacion_venta import NOW, _sembrar_listing, db_estimacion

    corte = NOW + timedelta(minutes=30)
    futuro = NOW + timedelta(hours=2)
    with db_estimacion() as conn:
        _pid, lid = _sembrar_listing(conn)
        plat, sku, asin = conn.execute(
            "SELECT platform::text, seller_sku, external_id FROM listing WHERE id = %s",
            (lid,),
        ).fetchone()
        id_temprano = conn.execute(
            "INSERT INTO estimacion_escenario"
            " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
            " formula_version, estado, motivos, componentes, exclusiones, canonical_input,"
            " context_fingerprint, source_event_id)"
            " VALUES (%s, %s, %s, %s, 'fba', %s, %s, 'S3', 'incompleta', %s, '[]'::jsonb,"
            " '[]'::jsonb, '{}'::jsonb, 'fp-temprano', 'evt-asof-temprano') RETURNING id",
            (lid, plat, sku, asin, NOW.date(), NOW + timedelta(seconds=1), Json(["temprano"])),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO estimacion_escenario"
            " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
            " formula_version, estado, motivos, componentes, exclusiones, canonical_input,"
            " context_fingerprint, source_event_id)"
            " VALUES (%s, %s, %s, %s, 'fba', %s, %s, 'S3', 'incompleta', %s, '[]'::jsonb,"
            " '[]'::jsonb, '{}'::jsonb, 'fp-futuro', 'evt-asof-futuro')",
            (lid, plat, sku, asin, NOW.date(), futuro, Json(["futuro"])),
        )
        conn.commit()
        temprano = adjuntar_estimaciones(conn, [lid], as_of=corte)
        assert temprano[lid]["snapshot_id"] == id_temprano
        assert temprano[lid]["motivos"] == ["temprano"]
        tarde = adjuntar_estimaciones(conn, [lid], as_of=futuro + timedelta(minutes=1))
        assert tarde[lid]["motivos"] == ["futuro"]
        assert tarde[lid]["snapshot_id"] != id_temprano
