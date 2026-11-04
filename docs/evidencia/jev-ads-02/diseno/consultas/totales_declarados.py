# Prototipo de SOLO LECTURA: una primera pagina de adGroups y de productAds por
# perfil aceptado, para ver si Amazon declara totalResults. Imprime solo
# nombres de claves y conteos; ningun dato de anuncios ni credenciales.
from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure_api import (
    PATH_AD_GROUPS,
    PATH_PRODUCT_ADS,
    _json_de,
    perfiles_aceptados,
)

client = AdsClient(AdsCredentials.from_secrets_dir())
for perfil in perfiles_aceptados(client):
    for path, clave in ((PATH_AD_GROUPS, "adGroups"), (PATH_PRODUCT_ADS, "productAds")):
        data = _json_de(client.list_objects(path, {}, profile_id=perfil.profile_id), "POST", path)
        total = data.get("totalResults") if isinstance(data, dict) else None
        print(
            perfil.platform,
            path,
            "claves=" + ",".join(sorted(data.keys()))
            if isinstance(data, dict)
            else type(data).__name__,
            "items=" + str(len(data.get(clave, []))),
            "totalResults=" + repr(total) + ":" + type(total).__name__,
            "hay_nextToken=" + str(bool(data.get("nextToken"))),
        )
