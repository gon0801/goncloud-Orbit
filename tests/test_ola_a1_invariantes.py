"""Pruebas de regresion de Ola A1 (bugs P0 y fugas de invariantes).

Cada prueba fija un fallo real encontrado en la auditoria Thermo-Nuclear:
- P0-1: atomicidad en retiro_anuncios y tx_durable en envio_muestras / apply_harvest*.
- P0-2: lock compartido entre fabrica_web y fabrica_motor (hashtextextended).
- P0-3: fail-closed de calcular_contribucion ante motivos fuera del viejo set bloqueantes.
- P0-4: _SQL_MANOS_EXTERNAS preserva origen='reactivacion_manual'.
- P0-5: temporada_io.frontera_hoja respeta modo_desde_settings y usa el bid real.
- P0-6: jev_lectura rechaza float y motivos desconocidos.
- P0-7: archiva_inertes falla cerrado ante perfiles duplicados; orders sanitiza skip_reason.
- P0-8: Retry-After NaN/Inf cae a default en spapi/meli write_client.
- P0-10: /api/dashboard/chrome filtra motor='ads_optimizer'.
- P0-12: cli_precio --reporte no abre conexion si falla validacion; PausadorFabrica cierra HTTP;
  _tomar_candado_lote libera ads_optimizer_lock si falla el advisory lock.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from helpers.pg import db_desde_template
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import (
    cli_precio,
    fabrica_web,
    impulso_io,
    jev_lectura,
    lecturas_caso,
    reputacion,
    temporada_io,
)
from app.api_dashboard import _SQL_CHROME_ULTIMO_CICLO
from app.estimacion_venta import (
    DetalleFee,
    EntradaCalculo,
    PoliticaCalculo,
    calcular_contribucion,
)
from app.fabrica_motor import Abortar as FabricaAbortar
from app.fabrica_motor import _bloquea_lote, _desbloquea_lote
from app.meli import write_client as meli_wc
from app.precio import cambios as precio_cambios
from app.spapi import orders as spapi_orders
from app.spapi import write_client as spapi_wc
from tools import archiva_inertes

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def test_estimacion_venta_fail_closed_motivos_no_listados():
    """P0-3: cualquier motivo en entrada.motivos_entrada impide estado='disponible'."""
    pol = PoliticaCalculo(
        politica_version_id=1,
        label="test-s4",
        universo="amazon_mx/fbm",
        formula_version="S4",
        settings={
            "iva_divisor": "1.16",
            "isr_tasa": "0.025",
            "retencion_iva_tasa": "0.08",
            "referral_fee_iva_multiplicador": "1.16",
            "logistica": "0",
            "precio_incluye_iva": True,
            "fee_tax_amount_requiere_politica": True,
        },
        valid_from=date(2026, 11, 1),
        valid_to=None,
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    for motivo_extra in ("costo_ambiguo", "variantes_sin_mapear", "logistica_fbm_pendiente"):
        entrada = EntradaCalculo(
            precio_bruto=Decimal("989.00"),
            precio_moneda="MXN",
            costo_original=Decimal("242.47"),
            costo_moneda="MXN",
            costo_includes_tax=False,
            fee_total=Decimal("127.89"),
            fee_estado="success",
            fee_detalles=(DetalleFee(fee_type="ReferralFee", final_fee=Decimal("127.89")),),
            logistica=Decimal("91.00"),
            motivos_entrada=(motivo_extra,),
        )
        res = calcular_contribucion(
            entrada, pol, valoracion_date=date(2026, 11, 21), universo_esperado="amazon_mx/fbm"
        )
        assert res.estado == "incompleta"
        assert motivo_extra in res.motivos


def test_jev_lectura_rechaza_float_y_motivo_desconocido():
    """P0-6: _como_dinero rechaza float (Regla 4) y _como_motivo_no_evaluada falla cerrado."""
    with pytest.raises(ValueError, match="ilegible"):
        jev_lectura._como_dinero(12.34)
    assert jev_lectura._como_dinero("12.34") == Decimal("12.34")
    assert jev_lectura._como_dinero(12) == Decimal("12")
    with pytest.raises(ValueError, match="fuera de vocabulario"):
        jev_lectura._como_motivo_no_evaluada("motivo_inexistente")


def test_retry_after_nan_e_inf_caen_a_default():
    """P0-8: Retry-After NaN o Inf jamas produce espera 0.0."""
    for valor in ("NaN", "nan", "inf", "-5"):
        resp = httpx.Response(429, headers={"Retry-After": valor})
        assert spapi_wc._espera_retry_after(resp) == spapi_wc.ESPERA_429_DEFAULT
        assert meli_wc._espera_retry_after(resp) == meli_wc._ESPERA_429_DEFAULT


def test_archiva_inertes_perfiles_duplicados_aborta(monkeypatch):
    """P0-7: dos perfiles aceptados para la misma plataforma abortan fail-closed."""
    duplicados = [
        SimpleNamespace(aceptado=True, platform="amazon_mx", profile_id=101),
        SimpleNamespace(aceptado=True, platform="amazon_mx", profile_id=102),
    ]
    monkeypatch.setattr(archiva_inertes, "evaluar_perfiles", lambda _c: duplicados)
    with pytest.raises(archiva_inertes.Abortar, match="multiples perfiles aceptados"):
        archiva_inertes._perfiles(object())


def test_cli_precio_reporte_invalido_no_abre_conexion(monkeypatch):
    """P0-12: validar --platform antes de connect() evita fugar conexiones."""
    monkeypatch.setenv("ORBIT_DSN_READ", "postgresql://x@127.0.0.1/x")

    def _no_conectar(*_a, **_kw):
        raise AssertionError("no debe conectar si --platform es invalido")

    monkeypatch.setattr(cli_precio, "connect", _no_conectar)
    args = argparse.Namespace(reporte=True, desde=None, hasta=None, platform=["amazon_mx", "meli"])
    assert cli_precio.main_precio(args) == 2


def test_pausador_fabrica_close_cierra_http_y_propia():
    """P0-12: PausadorFabrica como context manager cierra http y conexion propia."""
    cerrados: list[str] = []

    class _Cerrable:
        def __init__(self, nombre: str) -> None:
            self.nombre = nombre

        def close(self) -> None:
            cerrados.append(self.nombre)

    with impulso_io.PausadorFabrica(object()) as p:
        p._armado = (None, None, _Cerrable("http"), {})
        p._propia = _Cerrable("propia")
    assert cerrados == ["http", "propia"]


def test_spapi_orders_formato_skip_reason_sanitiza_nul():
    """P0-7: _formato_skip_reason de orders delega en ingest_run y quita bytes NUL."""
    res = spapi_orders._formato_skip_reason(Counter({"raro\x00texto": 2}))
    assert res is not None and "\x00" not in res


def test_reputacion_corre_meli_no_enmascara_causa_si_falla_sellar(monkeypatch):
    """F5: si falla el sello de corrida fallida, se propaga la excepcion original."""
    monkeypatch.setattr(reputacion, "_abrir_run", lambda _c, _s: 42)
    monkeypatch.setattr(
        reputacion,
        "fetch_meli",
        lambda _cli: (_ for _ in ()).throw(RuntimeError("caida original de meli")),
    )
    monkeypatch.setattr(
        reputacion,
        "_sellar",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("db rota al sellar")),
    )
    with pytest.raises(RuntimeError, match="caida original de meli"):
        reputacion._corre_meli(object(), object(), None, None)


@_skip_db
def test_ola_a1_invariantes_en_pg16(monkeypatch):
    """P0-2, P0-4, P0-5, P0-10 y P0-12 verificados en PostgreSQL 16 real."""
    with db_desde_template(prefijo="orbit_ola_a1", base=_test_dsn(), autocommit=False) as (
        conn,
        dsn,
    ):
        # P0-2: lock compartido bidireccional fabrica_web <-> fabrica_motor.
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        lote = "web-prueba-lock-a1"
        with (
            fabrica_web._lote_bloqueado(lote),
            pytest.raises(FabricaAbortar, match="ocupado"),
        ):
            _bloquea_lote(conn, lote)
        _bloquea_lote(conn, lote)
        try:
            with (
                pytest.raises(HTTPException) as exc_info,
                fabrica_web._lote_bloqueado(lote),
            ):
                pass
            assert exc_info.value.status_code == 409
        finally:
            _desbloquea_lote(conn, lote)

        # P0-4: _SQL_MANOS_EXTERNAS distingue reactivacion_manual vs exclusion.
        camp = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, name)"
            " VALUES ('amazon_mx', 'campaign', 'C-A1', 'Camp A1') RETURNING id"
        ).fetchone()[0]
        ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, name)"
            " VALUES ('amazon_mx', 'ad_group', 'AG-A1', %s, 'AG A1') RETURNING id",
            (camp,),
        ).fetchone()[0]
        kw = conn.execute(
            "INSERT INTO ad_entity"
            " (platform, kind, external_id, parent_id, name, match_type, keyword_text)"
            " VALUES ('amazon_mx', 'keyword', 'KW-A1', %s, 'KW A1', 'EXACT', 'kw a1')"
            " RETURNING id",
            (ag,),
        ).fetchone()[0]
        ahora = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
        conn.execute(
            "INSERT INTO reactivacion_manual (ad_entity_id, detectada_en) VALUES (%s, %s)",
            (kw, ahora),
        )
        conn.commit()
        filas = conn.execute(
            lecturas_caso._SQL_MANOS_EXTERNAS,
            ("amazon_mx", date(2026, 11, 1), "amazon_mx", date(2026, 11, 1)),
        ).fetchall()
        assert any(f[0] == kw and f[3] == "reactivacion_manual" for f in filas)

        # P0-5: frontera_hoja respeta modo apagado en plataforma (devuelve None).
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('a1-off', %s)",
            (
                Json(
                    {
                        "ads_temporada": {
                            "version": 1,
                            "meses": [
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "normal",
                                "baja",
                                "alta",
                            ],
                            "reduccion_baja_pct": "30",
                            "dias_preparacion": 7,
                            "mult_cortes_baja": "1.5",
                            "aumento_presupuesto_alta_pct": "50",
                        },
                        "ads_temporada_amazon_us": "shadow",
                    }
                ),
            ),
        )
        conn.commit()
        with pytest.raises(TypeError):
            temporada_io.frontera_hoja(conn, kw, date(2026, 11, 21))
        assert (
            temporada_io.frontera_hoja(
                conn, kw, date(2026, 11, 21), piso=Decimal("0.10"), techo=Decimal("5.00")
            )
            is None
        )

        # P0-10: _SQL_CHROME_ULTIMO_CICLO ignora ciclos de otro motor.
        conn.execute(
            "INSERT INTO optimizer_cycle"
            " (platform, mode, status, started_at, decisions_count, motor)"
            " VALUES ('amazon_mx', 'live', 'done', %s, 7, 'ads_optimizer')",
            (datetime(2026, 11, 21, 10, 0, tzinfo=UTC),),
        )
        conn.execute(
            "INSERT INTO optimizer_cycle"
            " (platform, mode, status, started_at, decisions_count, motor)"
            " VALUES ('amazon_mx', 'shadow', 'done', %s, 99, 'otro_motor')",
            (datetime(2026, 11, 21, 11, 0, tzinfo=UTC),),
        )
        conn.commit()
        chrome_filas = dict(
            (r[0], (r[1], r[4])) for r in conn.execute(_SQL_CHROME_ULTIMO_CICLO).fetchall()
        )
        assert chrome_filas["amazon_mx"] == ("live", 7)

        # P0-12: _tomar_candado_lote libera ads_optimizer_lock si falla advisory lock.
        class _ConnFallaAdvisory:
            def __init__(self, real) -> None:
                self._real = real

            def execute(self, sql, params=None):
                if "pg_try_advisory_lock" in sql:
                    raise RuntimeError("boom advisory")
                return self._real.execute(sql, params)

        with pytest.raises(RuntimeError, match="boom advisory"):
            precio_cambios._tomar_candado_lote(_ConnFallaAdvisory(conn), "amazon_mx", "owner-a1")
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = 'precio:amazon_mx'"
            ).fetchone()[0]
            == 0
        )
