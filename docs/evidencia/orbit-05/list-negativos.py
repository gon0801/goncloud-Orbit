# ORBIT 05 2.3: readback LIST de los negativos de los harvests 2-4 (solo lectura; lee credenciales).
import json

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure_api import perfiles_aceptados

IDS = ["92333897493675", "238992858651508", "123271341601901"]
c = AdsClient(AdsCredentials.from_secrets_dir())
perfil = [p for p in perfiles_aceptados(c) if p.platform == "amazon_mx"][0]
data = c.list_objects(
    "/sp/negativeKeywords/list",
    {
        "negativeKeywordIdFilter": {"include": IDS},
        "stateFilter": {"include": ["ENABLED", "PAUSED", "ARCHIVED"]},
    },
    profile_id=perfil.profile_id,
).json()
for k in data.get("negativeKeywords", []):
    print(
        json.dumps(
            {
                x: k.get(x)
                for x in (
                    "keywordId",
                    "keywordText",
                    "matchType",
                    "state",
                    "campaignId",
                    "adGroupId",
                )
            },
            ensure_ascii=False,
        )
    )
print("total", len(data.get("negativeKeywords", [])))
