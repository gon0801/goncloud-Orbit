# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Sonda de ESCRITURA: pausar y reactivar UN product ad.

Estado: NO EJECUTADA. El dueno la autorizo el 2026-10-09 (decision D8). La sesion del lead intento correrla
y el entorno la bloqueo por ser una escritura a produccion. Queda para quien ejecuta en produccion.

Que prueba: que `PUT /sp/productAds` acepta `state` PAUSED y ENABLED, con que cuerpo y que responde, para
poder sacar un producto de una bolsa compartida con una pausa reversible en vez de archivar.

Por que no afecta ninguna entrega: el anuncio elegido esta ENABLED dentro de una campana que ya esta PAUSADA
(`A1U - Auto Discovery - US`), asi que pausarlo y reactivarlo no cambia lo que Amazon muestra.

Seguros: aborta sin escribir si el anuncio no esta ENABLED o si su campana no esta PAUSED en Amazon en ese
momento; toca un solo anuncio; siempre intenta dejarlo ENABLED, como lo encontro; imprime cada paso.

Como se corre (dentro del contenedor de la app, con sus credenciales):
    docker exec -i orbit-app-1 python - < sonda_pausa_product_ad.py
"""

import json
import sys
import time

from app.ads.client import AdsClient, AdsCredentials
from app.ads.structure_api import listar_todo, perfiles_aceptados

AD_ID = "284583606382521"  # ASIN B0B384M2M8; elegido por lectura el 2026-10-09
CAMPANA = "A1U - Auto Discovery - US"
VENDOR = "application/vnd.spproductad.v3+json"  # el mismo vendor ya sellado para crear y archivar product ads

cliente = AdsClient(AdsCredentials.from_secrets_dir())
perfil = [p for p in perfiles_aceptados(cliente) if p.platform == "amazon_us"][0]
registro: dict = {"ad_id": AD_ID, "pasos": []}


def lee() -> dict | None:
    cuerpo = cliente.list_objects(
        "/sp/productAds/list", {"adIdFilter": {"include": [AD_ID]}}, profile_id=perfil.profile_id
    ).json()
    anuncios = cuerpo.get("productAds") or []
    return anuncios[0] if len(anuncios) == 1 else None


def escribe(estado: str) -> tuple[int, dict]:
    token = cliente._ensure_token()
    encabezados = cliente._build_headers(token, perfil.profile_id, path=None, method="PUT")
    encabezados["Content-Type"] = VENDOR
    encabezados["Accept"] = VENDOR
    respuesta = cliente._client.request(
        "PUT",
        f"{cliente._base_url}/sp/productAds",
        headers=encabezados,
        json={"productAds": [{"adId": AD_ID, "state": estado}]},
    )
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {"texto": respuesta.text[:300]}
    return respuesta.status_code, cuerpo


try:
    antes = lee()
    campanas = [
        c
        for c in listar_todo(cliente, "/sp/campaigns/list", profile_id=perfil.profile_id)
        if c.get("name") == CAMPANA
    ]
    registro["antes"] = {
        "anuncio": antes
        and {k: antes.get(k) for k in ("adId", "state", "asin", "sku", "campaignId", "adGroupId")},
        "campana": [{"state": c.get("state"), "campaignId": c.get("campaignId")} for c in campanas],
    }
    seguro = (
        antes is not None
        and antes.get("state") == "ENABLED"
        and len(campanas) == 1
        and campanas[0].get("state") == "PAUSED"
        and str(campanas[0].get("campaignId")) == str(antes.get("campaignId"))
    )
    if not seguro:
        registro["resultado"] = (
            "ABORTADO sin escribir: el anuncio no esta ENABLED o su campana no esta PAUSED"
        )
    else:
        status, cuerpo = escribe("PAUSED")
        registro["pasos"].append({"put": "PAUSED", "status": status, "cuerpo": cuerpo})
        time.sleep(3)
        pausado = lee()
        registro["pasos"].append({"lectura_tras_pausar": pausado and pausado.get("state")})
        status, cuerpo = escribe("ENABLED")
        registro["pasos"].append({"put": "ENABLED", "status": status, "cuerpo": cuerpo})
        time.sleep(3)
        final = lee()
        registro["pasos"].append({"lectura_tras_reactivar": final and final.get("state")})
        registro["estado_final"] = final and final.get("state")
        registro["resultado"] = (
            "OK: pauso y reactivo"
            if (
                pausado
                and pausado.get("state") == "PAUSED"
                and final
                and final.get("state") == "ENABLED"
            )
            else "REVISAR: el estado final no es el esperado"
        )
except Exception as exc:  # la sonda reporta el error y el estado en que quedo el anuncio
    registro["error"] = f"{type(exc).__name__}: {exc}"
    try:
        registro["estado_al_fallar"] = (lee() or {}).get("state")
    except Exception as exc2:
        registro["estado_al_fallar"] = f"no se pudo leer: {type(exc2).__name__}"

json.dump(registro, sys.stdout, ensure_ascii=False, indent=1, default=str)
