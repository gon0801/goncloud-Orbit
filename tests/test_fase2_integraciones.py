"""Pruebas de regresion de Fase 2.4: notifica, descarga, reports, write, SP-API, MeLi y chrome."""

from __future__ import annotations

import datetime as dt
import gzip
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app import api_dashboard, notifica, precio_avisos, ui
from app.ads import descarga, reports
from app.ads import write as ads_write
from app.meli.client import ClienteMeli, MeliCredentials, ReputacionError
from app.spapi import listings as spapi_listings
from app.spapi import pricing as spapi_pricing
from tests.helpers.pg import db_desde_template, pg_disponible

_skip_pg = pytest.mark.skipif(not pg_disponible(), reason="sin Postgres local")


def test_notifica_no_cachea_error_transitorio_de_telegram_json(tmp_path, monkeypatch):
    """C5: un error transitorio leyendo telegram.json no deshabilita el canal para siempre."""
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))
    archivo = tmp_path / notifica.TELEGRAM_FILENAME
    archivo.write_text("{json roto", encoding="utf-8")
    notifica._reset()

    assert notifica.canal_activo() is False
    # Al recuperarse el archivo sin reiniciar el proceso, debe volver a activarse.
    archivo.write_text('{"bot_token": "tok_f2_123", "chat_id": "999"}', encoding="utf-8")
    assert notifica.canal_activo() is True


def test_notifica_residual_tacos_usa_moneda_de_plataforma():
    """C5/Regla 4: amazon_us imprime USD en residual_tacos, no MXN."""
    from app import notifica_contribucion

    contrib = notifica_contribucion.ContribucionDigest(
        rango=notifica_contribucion.RangoContribucion(
            moneda="USD",
            entidades=5,
            sin_halo=Decimal("100"),
            con_halo=Decimal("200"),
        ),
        sin_dato=None,
        residual_tacos=notifica_contribucion.ResidualTacos(monto=Decimal("12.50")),
    )
    texto = notifica.digest_ciclo(
        {
            "cycle_id": 1,
            "plataforma": "amazon_us",
            "modo": "shadow",
            "status": "done",
            "decisions_count": 0,
            "contribucion": contrib,
        }
    )
    assert "12.50 USD" in texto
    assert "12.50 MXN" not in texto


def test_reports_descargar_filas_bloquea_gzip_bomb(monkeypatch):
    """C6: un gzip pequeno que descomprime por encima de MAX_BYTES_REPORTE aborta fail-closed."""
    monkeypatch.setattr(reports, "MAX_BYTES_REPORTE", 64)
    bomba = gzip.compress(b"[" + b" " * 512 + b"]")
    assert len(bomba) <= 64

    class _ClienteFake:
        def download(self, _url):
            return type("_R", (), {"content": bomba})()

    with pytest.raises(reports.AdsReportsError, match="descomprimido excede"):
        reports.descargar_filas(_ClienteFake(), "https://ejemplo.test/r.gz")


def test_reports_productos_aborta_en_negativo_antes_de_same_sku():
    """A8: sales=-5 con sales_same_sku=0 debe abortar fail-closed, no caer en skip."""
    dia = dt.date(2026, 9, 1)
    fila = {
        "date": "2026-09-01",
        "advertisedAsin": "B0TEST0001",
        "advertisedSku": "SKU-1",
        "impressions": 10,
        "clicks": 1,
        "cost": "1.00",
        "purchases30d": 0,
        "sales30d": "-5.00",
        "purchasesSameSku30d": 0,
        "attributedSalesSameSku30d": "0.00",
    }
    with pytest.raises(reports.AdsReportsError, match="negativa"):
        reports._planea_filas_productos(
            [fila],
            ultimo_cerrado=dia,
            fecha_ini=dia,
            fecha_fin=dia,
        )


def test_ads_write_errores_put_fail_closed_ante_207_vacio():
    """A8: un 207 sin contenedor o sin success ni error se trata como error, no exito."""
    r_vacio = httpx.Response(207, json={})
    assert ads_write._errores_put(r_vacio, "campaigns") != []

    r_nada = httpx.Response(207, json={"campaigns": {"success": [], "error": []}})
    assert ads_write._errores_put(r_nada, "campaigns") != []

    r_ok = httpx.Response(207, json={"campaigns": {"success": [{"campaignId": "1"}], "error": []}})
    assert ads_write._errores_put(r_ok, "campaigns") == []


def test_spapi_umbral_no_aborta_en_primer_error_aislado_pero_si_en_muro():
    """A7: en universo grande, 1 fallo aislado (1/1..1/4) no mata la corrida; 2/5 si aborta."""
    spapi_pricing._vigilar_umbral(
        "B0TEST1", fallos=1, racha=1, vistos=1, ultimo_status=503, total=50
    )
    spapi_listings._vigilar_umbral(
        "SKU-1", fallos=1, racha=1, vistos=1, ultimo_status=503, total=50
    )

    with pytest.raises(spapi_pricing.IngestaPricingError, match="umbral de fallos"):
        spapi_pricing._vigilar_umbral(
            "B0TEST2", fallos=2, racha=2, vistos=5, ultimo_status=503, total=50
        )
    with pytest.raises(spapi_listings.IngestaListingsError, match="umbral de fallos"):
        spapi_listings._vigilar_umbral(
            "SKU-2", fallos=2, racha=2, vistos=5, ultimo_status=503, total=50
        )


def test_cliente_meli_bloquea_ssrf_y_envuelve_http_error():
    """C4: ClienteMeli bloquea URLs absolutas (anti-SSRF) y envuelve httpx.HTTPError."""
    llamadas = []

    def handler(req: httpx.Request) -> httpx.Response:
        llamadas.append(str(req.url))
        raise httpx.ConnectTimeout("timeout red")

    cliente = ClienteMeli(
        MeliCredentials("tok_meli_secreto_f2"),
        transport=httpx.MockTransport(handler),
        max_intentos=1,
        backoff_base=0.0,
    )
    try:
        with pytest.raises(ReputacionError, match="invalido"):
            cliente.get("https://evil.example/steal")
        assert llamadas == []

        with pytest.raises(ReputacionError, match="error de red"):
            cliente.get("/items/MLM123")
    finally:
        cliente.close()


def test_descarga_corta_loop_cuando_todos_agotaron_intentos(monkeypatch):
    """C6: si un reporte agoto sus intentos y todos son terminales, run() no duerme 2h 30m."""
    grupo = descarga.GrupoDescarga(
        clave="metricas", source="amazon_ads_reports_v3", cfgs=({"nombre": "camp"},)
    )
    perfil = SimpleNamespace(profile_id=1, platform="amazon_mx")
    corrida = descarga._Corrida(
        conn=None,
        grupos=[grupo],
        rango=(dt.date(2026, 9, 1), dt.date(2026, 9, 1)),
        al_completar=lambda *_a, **_k: None,
    )
    corrida.run_ids = {"metricas": 1}
    logico = descarga._Logico(
        grupo=grupo,
        perfil=perfil,
        cfg={"nombre": "camp"},
        fecha_ini=dt.date(2026, 9, 1),
        fecha_fin=dt.date(2026, 9, 1),
        pedidos=1 + descarga.MAX_REPETIDOS,
        intentos=[descarga._Intento("r1", 0.0, terminal=True, repetido=True)],
    )
    monkeypatch.setattr(corrida, "_abrir_runs", lambda: None)
    monkeypatch.setattr(corrida, "_registrar_perfiles", lambda _c: [perfil])
    monkeypatch.setattr(
        corrida, "_pedir_todos", lambda _c, _a, _t: setattr(corrida, "logicos", [logico])
    )
    monkeypatch.setattr(corrida, "_sondear_una_vuelta", lambda _c, _r: None)
    monkeypatch.setattr(corrida, "_completar_grupos", lambda: None)
    monkeypatch.setattr(corrida, "_fallar_por_plazo", lambda: None)

    sleeps: list[float] = []
    corrida.run(object(), reloj=lambda: 0.0, sleep=sleeps.append)
    assert sleeps == []


def test_ui_campanas_sort_rango_y_procedencia_mixto():
    """A12: rango '20.00-25.00' ordena usando minimo y 'mixto' pasa validacion de procedencia."""
    item = {"target_efectivo": {"valor": "20.00\u201325.00", "minimo": "20.00", "peldano": "mixto"}}
    assert ui._valor_orden(item, "target") == Decimal("20.00")
    assert (
        ui._vocab_o_422("mixto", frozenset(ui.PELDANOS_CASCADA) | {"mixto"}, "procedencia")
        == "mixto"
    )


@_skip_pg
def test_chrome_y_resumen_precio_no_sella_con_canal_apagado():
    """A12 + C5: /chrome responde liviano y _avisar_resumen_precio no sella apagado."""
    with db_desde_template("orbit_f2_chrome") as (conn, _dsn):
        res = api_dashboard.chrome(conn)
        assert res["propuestas"] == {"live": 0, "shadow": 0}
        assert set(res["plataformas"]) == {"amazon_mx", "amazon_us"}

        cfg_id = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('t', '{}'::jsonb) RETURNING id"
        ).fetchone()[0]
        hoy = dt.date(2026, 9, 1)
        corrida_id = conn.execute(
            "INSERT INTO precio_corrida (platform, owner, config_version_id, modo_global,"
            " fecha, estado, cerrada_at, decisiones, movidas, virtuales, retenidas,"
            " errores, saltadas_por_edicion)"
            " VALUES ('amazon_mx', 'test', %s, 'shadow', %s, 'cerrada', now(),"
            " 0, 0, 0, 0, 0, 0) RETURNING id",
            (cfg_id, hoy),
        ).fetchone()[0]
        conn.commit()

        # Canal apagado (autouse fixture de conftest): no debe sellar resumen_enviado_at
        assert precio_avisos._avisar_resumen_precio(conn, "amazon_mx", hoy) == 0
        sello = conn.execute(
            "SELECT resumen_enviado_at FROM precio_corrida WHERE id = %s", (corrida_id,)
        ).fetchone()[0]
        assert sello is None
