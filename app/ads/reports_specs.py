"""Configuracion declarativa, tipos y transporte HTTP de reportes Amazon Ads v3.

Modulo hoja respecto a `app.ads.reports` y `app.ads.descarga`: aloja las
especificaciones (`CAMPANAS_CFG`, `KEYWORDS_CFG`, `TARGETS_CFG`,
`SEARCH_TERMS_CFG`, `PRODUCTOS_CFG`), los tipos de resultado (`ResultadoIngesta`,
`ResumenReporte`) y el transporte (`solicitar_reporte`, `descargar_filas`,
`_json_de`, `_validar_rango`), rompiendo el ciclo entre `descarga` y `reports`.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.ads.client import AdsClient
from app.ads.reports_plan import AdsReportsError
from app.ads.structure import PerfilAds
from app.redaction import scrub

SOURCE_PRODUCTOS = "amazon_ads_products_v3"

# Tope de rango de la API (verificado: spCampaigns max 31 dias).
MAX_RANGO_DIAS = 31

# Tope fail-closed de descarga (se valida en fileSize del poll y tras bajar):
# un backfill de 31 dias pesa pocos MB; 512 MB indica payload anormal.
MAX_BYTES_REPORTE = 512 * 1024 * 1024

_METRICAS = (
    "impressions",
    "clicks",
    "cost",
    "purchases30d",
    "sales30d",
    "attributedSalesSameSku30d",
)

CAMPANAS_CFG = {
    "nombre": "campanas",
    "reportTypeId": "spCampaigns",
    "groupBy": ["campaign"],
    "columns": ["date", "campaignId", *_METRICAS],
    "kind": "campaign",
    "id_field": "campaignId",
}
KEYWORDS_CFG = {
    "nombre": "keywords",
    "reportTypeId": "spTargeting",
    "groupBy": ["targeting"],
    "filters": [{"field": "keywordType", "values": ["BROAD", "PHRASE", "EXACT"]}],
    "columns": ["date", "campaignId", "adGroupId", "keywordId", *_METRICAS],
    "kind": "keyword",
    "id_field": "keywordId",
}
TARGETS_CFG = {
    "nombre": "targets",
    "reportTypeId": "spTargeting",
    "groupBy": ["targeting"],
    "filters": [
        {
            "field": "keywordType",
            "values": ["TARGETING_EXPRESSION", "TARGETING_EXPRESSION_PREDEFINED"],
        }
    ],
    "columns": ["date", "campaignId", "adGroupId", "keywordId", *_METRICAS],
    "kind": "product_target",
    "id_field": "keywordId",
}
SEARCH_TERMS_CFG = {
    "nombre": "search_terms",
    "reportTypeId": "spSearchTerm",
    "groupBy": ["searchTerm"],
    "columns": ["date", "adGroupId", "searchTerm", "clicks", "cost", "purchases7d", "sales7d"],
    "kind": "ad_group",
    "id_field": "adGroupId",
    "tabla": "search_term_observation",
}
REPORTES_CFG = (CAMPANAS_CFG, KEYWORDS_CFG, TARGETS_CFG, SEARCH_TERMS_CFG)

PRODUCTOS_CFG = {
    "nombre": "productos",
    "reportTypeId": "spAdvertisedProduct",
    "groupBy": ["advertiser"],
    "columns": [
        "date",
        "advertisedAsin",
        "advertisedSku",
        "campaignId",
        "adGroupId",
        "adId",
        "impressions",
        "clicks",
        "cost",
        "purchases30d",
        "sales30d",
        "purchasesSameSku30d",
        "attributedSalesSameSku30d",
    ],
    "tabla": "ads_product_metric_observation",
}

_SQL_RESULTADO_REPORTE = """
    INSERT INTO ads_report_result
        (ingest_run_id, profile_id, platform, report_name, source_report_id, status, reason,
         fecha_desde, fecha_hasta, cobertura_integra)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


@dataclass
class ResultadoIngesta:
    """Outcome contable de UN reporte ingerido."""

    report_id: str
    filas: int
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    skips: Counter[str]
    # AUTO-03 D.7: solo productos la calcula (None en los demas reportes).
    cobertura_integra: bool | None = None


@dataclass
class ResumenReporte:
    """Evidencia por (perfil, reporte) para el resumen del CLI."""

    profile_id: int
    platform: str | None
    nombre: str
    report_id: str
    status: str
    filas: int
    rows_written: int
    rows_skipped: int
    skip_reason: str | None


def registrar_resultado(
    conn: Any,
    run_id: int,
    perfil: PerfilAds | None,
    cfg: dict | None,
    report_id: str | None,
    status: str,
    reason: str | None = None,
    *,
    rango: tuple[dt.date, dt.date] | None = None,
    cobertura_integra: bool | None = None,
) -> None:
    """Registra una fila en `ads_report_result` (rango pedido + cobertura opcional)."""
    profile_id = perfil.profile_id if perfil else None
    if (
        not isinstance(profile_id, int)
        or isinstance(profile_id, bool)
        or not -(2**63) <= profile_id < 2**63
    ):
        profile_id = None
    conn.execute(
        _SQL_RESULTADO_REPORTE,
        (
            run_id,
            profile_id,
            perfil.platform if perfil else None,
            cfg["nombre"] if cfg else None,
            report_id,
            status,
            scrub(reason) if reason else None,
            rango[0] if rango else None,
            rango[1] if rango else None,
            cobertura_integra,
        ),
    )


def json_de(resp: Any, metodo: str, path: str) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise AdsReportsError(f"respuesta no-JSON de {metodo} {path}") from None


def solicitar_reporte(
    client: AdsClient,
    perfil: PerfilAds,
    reporte_cfg: dict,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
    *,
    sufijo: str = "",
) -> str:
    """POST /reporting/reports -> report_id (forma verificada en vivo)."""
    configuration: dict[str, Any] = {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": list(reporte_cfg["groupBy"]),
        "columns": list(reporte_cfg["columns"]),
        "reportTypeId": reporte_cfg["reportTypeId"],
        "timeUnit": "DAILY",
        "format": "GZIP_JSON",
    }
    if "filters" in reporte_cfg:
        configuration["filters"] = reporte_cfg["filters"]
    body = {
        "name": (
            f"orbit-{reporte_cfg['nombre']}-{perfil.profile_id}"
            f"-{fecha_ini.isoformat()}_{fecha_fin.isoformat()}{sufijo}"
        ),
        "startDate": fecha_ini.isoformat(),
        "endDate": fecha_fin.isoformat(),
        "configuration": configuration,
    }
    data = json_de(
        client.create_report(body, profile_id=perfil.profile_id),
        "POST",
        "/reporting/reports",
    )
    report_id = data.get("reportId") if isinstance(data, dict) else None
    if not isinstance(report_id, str) or not report_id:
        raise AdsReportsError("respuesta de POST /reporting/reports sin reportId")
    return report_id


def descargar_filas(
    client: AdsClient, url: str, *, max_bytes: int = MAX_BYTES_REPORTE
) -> list[dict]:
    """download(url) -> array JSON de filas del reporte (gzip o plano, con tope)."""
    contenido = client.download(url).content
    if len(contenido) > max_bytes:
        raise AdsReportsError(
            f"descarga del reporte excede el tope de {max_bytes} bytes "
            f"(recibidos {len(contenido)}): la corrida aborta fail-closed"
        )
    if contenido[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(contenido)) as gz:
                descomprimido = gz.read(max_bytes + 1)
            if len(descomprimido) > max_bytes:
                raise AdsReportsError(
                    f"reporte descomprimido excede el tope de {max_bytes} bytes: "
                    "la corrida aborta fail-closed"
                )
            texto = descomprimido.decode("utf-8")
        except AdsReportsError:
            raise
        except (OSError, EOFError, zlib.error, UnicodeDecodeError):
            raise AdsReportsError("gzip del reporte corrupto o no utf-8") from None
    else:
        try:
            texto = contenido.decode("utf-8")
        except UnicodeDecodeError:
            raise AdsReportsError("descarga del reporte no es gzip ni utf-8") from None
    try:
        filas = json.loads(texto)
    except ValueError:
        raise AdsReportsError("descarga del reporte no es JSON") from None
    if not isinstance(filas, list) or any(not isinstance(fila, dict) for fila in filas):
        raise AdsReportsError("descarga del reporte no es un array JSON de filas")
    return filas


def validar_rango(fecha_ini: dt.date, fecha_fin: dt.date) -> None:
    """El rango de los reportes: [fecha_ini, fecha_fin], max MAX_RANGO_DIAS."""
    dias = (fecha_fin - fecha_ini).days + 1
    if dias < 1:
        raise ValueError(f"rango invertido: {fecha_ini} > {fecha_fin}")
    if dias > MAX_RANGO_DIAS:
        raise ValueError(f"rango de {dias} dias excede el maximo de {MAX_RANGO_DIAS} de la API")
