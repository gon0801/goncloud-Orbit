# ORBIT 05 2.3: readback LIST de solo lectura (AdsClient read-only; list_objects = POST de lectura).
import json

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure_api import perfiles_aceptados

IDS = ["197174507964917", "173682599413296", "103183625886360"]
TEXTOS = {"arras matrimoniales de oro", "arras de boda catolica", "arras de boda cristiana"}
CAMPANA = "97835222467967"

c = AdsClient(AdsCredentials.from_secrets_dir())
perfil = [p for p in perfiles_aceptados(c) if p.platform == "amazon_mx"][0]


def lista(filtro):
    items, body = [], dict(filtro)
    while True:
        data = c.list_objects("/sp/keywords/list", body, profile_id=perfil.profile_id).json()
        items += data.get("keywords", [])
        if not data.get("nextToken"):
            return items
        body = dict(filtro, nextToken=data["nextToken"])


campos = ("keywordId", "keywordText", "matchType", "state", "bid", "campaignId", "adGroupId")
print("== por keywordId")
for k in lista({"keywordIdFilter": {"include": IDS}}):
    print(json.dumps({x: k.get(x) for x in campos}, ensure_ascii=False))
print("== campana Arras Manual: mismas palabras, todos los match (duplicados)")
for k in lista({"campaignIdFilter": {"include": [CAMPANA]}}):
    if k.get("keywordText", "").lower() in TEXTOS:
        print(json.dumps({x: k.get(x) for x in campos}, ensure_ascii=False))
