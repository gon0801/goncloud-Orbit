"""IO de API del sync de estructura Amazon Ads (ESTRUCTURA 01).

PATH_*, gate de perfiles y fetch_structure. Sin psycopg.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.ads.client import AdsClient
from app.redaction import scrub

# Unico GET v2 que sigue vivo. Los list v3 de abajo (salvo negativeKeywords,
# que solo consume el snapshot) los pide fetch_structure. Vendor types en
# app.ads.client.
PATH_PROFILES = "/v2/profiles"
PATH_CAMPAIGNS = "/sp/campaigns/list"
PATH_AD_GROUPS = "/sp/adGroups/list"
PATH_KEYWORDS = "/sp/keywords/list"
PATH_TARGETS = "/sp/targets/list"
PATH_PRODUCT_ADS = "/sp/productAds/list"
# Evidencia REGLA 8 en vivo (lead, 2026-08-25; log out/regla8-negkeywords.log):
# POST /sp/negativeKeywords/list con el vendor vnd.spnegativekeyword.v3+json
# (allowlist de app.ads.client) responde 200 en AMBOS perfiles (US y MX);
# contenedor `negativeKeywords`, paginacion nextToken+totalResults. Lo consume
# el snapshot read-only de listas (ORBIT 05 preflight 1.3,
# tools/snapshot_listas.py); el sync de estructura NO lo lista.
PATH_NEGATIVE_KEYWORDS = "/sp/negativeKeywords/list"

# Tope de seguridad de la paginacion nextToken: una lista que nunca termina
# (bug de la API o de este codigo) no debe colgar la corrida para siempre.
MAX_PAGINAS = 100

# countryCode -> (platform, moneda esperada). Fuera de aqui, perfil rechazado.
_PAIS_PLATAFORMA_MONEDA: dict[str, tuple[str, str]] = {
    "US": ("amazon_us", "USD"),
    "MX": ("amazon_mx", "MXN"),
}

# Clave contenedora de cada respuesta (ojo targets: "targetingClauses";
# negativeKeywords: evidencia regla 8, comentario en PATH_NEGATIVE_KEYWORDS).
_CLAVE_CONTENEDORA = {
    PATH_PROFILES: "profiles",
    PATH_CAMPAIGNS: "campaigns",
    PATH_AD_GROUPS: "adGroups",
    PATH_KEYWORDS: "keywords",
    PATH_TARGETS: "targetingClauses",
    PATH_NEGATIVE_KEYWORDS: "negativeKeywords",
    PATH_PRODUCT_ADS: "productAds",
}


class AdsStructureError(Exception):
    """Error de forma en la respuesta de la API o en un payload.

    El mensaje no toca secretos por construccion (paths sin query, sin
    headers); `scrub()` como ultima linea de defensa.
    """

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


@dataclass(frozen=True)
class PerfilAds:
    """Evidencia de un perfil visto en GET /v2/profiles y su tratamiento.

    Formas REALES del payload (corrida 2026-08-22): countryCode en vez de
    country, accountInfo en vez de account, sin campo `valid` (se registra
    validPaymentMethod como evidencia, sin gatearlo). `motivo` es la razon
    del rechazo (None si fue aceptado); `platform` y `moneda` solo se fijan
    al aceptar.
    """

    profile_id: int | None
    country: str | None
    currency_code: str | None
    account_type: str | None
    valid_payment_method: bool | None
    account_name: str | None
    aceptado: bool
    platform: str | None = None
    moneda: str | None = None
    motivo: str | None = None


@dataclass
class EstructuraPerfil:
    """Payloads crudos de un perfil aceptado, agrupados por kind."""

    perfil: PerfilAds
    campanas: list[dict]
    ad_groups: list[dict]
    keywords: list[dict]
    targets: list[dict]
    product_ads: list[dict] = field(default_factory=list)


@dataclass
class EstructuraAds:
    """Salida de fetch_structure: evidencia de TODOS los perfiles + payloads."""

    perfiles: list[PerfilAds]
    estructuras: list[EstructuraPerfil]


# ---------------------------------------------------------------------------
# IO de API: fetch_structure
# ---------------------------------------------------------------------------


def _json_de(resp, metodo: str, path: str):
    try:
        return resp.json()
    except ValueError:
        raise AdsStructureError(f"respuesta no-JSON de {metodo} {path}") from None


def _extraer_lista(data: object, path: str) -> list[dict]:
    """Acepta lista JSON o dict con clave contenedora; cualquier otra forma, error."""
    clave = _CLAVE_CONTENEDORA[path]
    lista: list | None = None
    if isinstance(data, list):
        lista = data
    elif isinstance(data, dict):
        valor = data.get(clave)
        if isinstance(valor, list):
            lista = valor
        else:
            listas = [v for v in data.values() if isinstance(v, list)]
            if len(listas) == 1:
                lista = listas[0]
    if lista is None:
        raise AdsStructureError(
            f"respuesta inesperada de {path}: se esperaba una lista JSON "
            f"o un dict con la clave '{clave}'"
        )
    for item in lista:
        if not isinstance(item, dict):
            raise AdsStructureError(f"{path}: item de la lista no es un objeto JSON")
    return lista


def listar_todo(client: AdsClient, path: str, *, profile_id: int) -> list[dict]:
    """Lectura paginada v3 COMPLETA (publica para consumidores read-only).

    Itera la paginacion v3 por nextToken hasta que falta (tope MAX_PAGINAS).
    Consumidores: fetch_structure y el snapshot read-only de listas
    (tools/snapshot_listas.py, ORBIT 05 preflight 1.3). El `path` debe estar
    en _CLAVE_CONTENEDORA y en el allowlist de lectura del cliente
    (app.ads.client.LIST_REQUEST_TYPES); el guard de totalResults vive aqui,
    asi que todo consumidor hereda la verificacion de lista completa.

    Primera pagina con body {} (pageSize se ignora, corrida real); las
    siguientes piden {"nextToken": ...}. La clave es nextToken, NO
    nextPageToken. Si la respuesta declara totalResults (int), el acumulado
    final tiene que cuadrar: "falta nextToken" ya no basta como prueba de
    lista completa (hallazgo cross-review codex, ronda 3).
    """
    items: list[dict] = []
    next_token: str | None = None
    for _ in range(MAX_PAGINAS):
        body = {"nextToken": next_token} if next_token else {}
        data = _json_de(client.list_objects(path, body, profile_id=profile_id), "POST", path)
        items.extend(_extraer_lista(data, path))
        if not isinstance(data, dict) or "nextToken" not in data or data["nextToken"] is None:
            next_token = None
        else:
            # Fin de paginacion SOLO por clave ausente o None (hallazgo
            # CodeRabbit): un nextToken malformado (false, "", numero) tratado
            # como fin dejaba estructura parcial sellada ok -- fail-closed.
            candidato = data["nextToken"]
            if not isinstance(candidato, str) or not candidato:
                raise AdsStructureError(f"nextToken malformado en POST {path}: {candidato!r}")
            next_token = candidato
        if next_token is None:
            total = data.get("totalResults") if isinstance(data, dict) else None
            # Solo se exige cuando totalResults viene como int: dato faltante o
            # de otro tipo = sin prueba, se mantiene el comportamiento actual.
            if isinstance(total, int) and not isinstance(total, bool) and total != len(items):
                raise AdsStructureError(
                    f"paginacion incompleta de POST {path}: {len(items)} acumulados"
                    f" de {total} segun totalResults"
                )
            return items
    raise AdsStructureError(f"paginacion de POST {path} excede el tope de {MAX_PAGINAS} paginas")


def _evaluar_perfil(raw: dict) -> PerfilAds:
    """Sello de perfil: seller + countryCode en {US, MX} + moneda coherente.

    El payload real trae countryCode y accountInfo (sin country/valid/account:
    ver docstring del modulo). validPaymentMethod se registra como evidencia,
    sin gatearlo.
    """
    profile_id = raw.get("profileId")
    country = raw.get("countryCode")
    currency_code = raw.get("currencyCode")
    account = raw.get("accountInfo")
    account_type = account.get("type") if isinstance(account, dict) else None
    valid_payment = account.get("validPaymentMethod") if isinstance(account, dict) else None
    account_name = account.get("name") if isinstance(account, dict) else None

    def perfil(
        aceptado: bool,
        platform: str | None = None,
        moneda: str | None = None,
        motivo: str | None = None,
    ) -> PerfilAds:
        return PerfilAds(
            profile_id=profile_id,
            country=country,
            currency_code=currency_code,
            account_type=account_type,
            valid_payment_method=valid_payment,
            account_name=account_name,
            aceptado=aceptado,
            platform=platform,
            moneda=moneda,
            motivo=motivo,
        )

    if not isinstance(profile_id, int) or isinstance(profile_id, bool):
        return perfil(False, motivo="perfil sin profileId")
    if account_type != "seller":
        return perfil(False, motivo=f"perfil no seller (accountInfo.type={account_type!r})")
    if not isinstance(country, str):
        return perfil(False, motivo=f"pais no soportado: {country!r}")
    mapeo = _PAIS_PLATAFORMA_MONEDA.get(country)
    if mapeo is None:
        return perfil(False, motivo=f"pais no soportado: {country}")
    platform, moneda_esperada = mapeo
    if currency_code != moneda_esperada:
        return perfil(
            False,
            motivo=(
                f"moneda {currency_code!r} no corresponde al pais {country} "
                f"(se esperaba {moneda_esperada})"
            ),
        )
    return perfil(True, platform=platform, moneda=moneda_esperada)


def evaluar_perfiles(client: AdsClient) -> list[PerfilAds]:
    """GET /v2/profiles + sello: TODOS los perfiles vistos, ya evaluados.

    Unica fuente del gate seller/pais/moneda/1-pais-por-pais (regla 2): la
    usan fetch_structure (evidencia completa) y perfiles_aceptados (la vista
    de los syncs que solo necesitan los aceptados, p.ej. app.ads.reports).
    Los rechazados llevan su motivo en el propio PerfilAds.
    """
    perfiles: list[PerfilAds] = []
    paises_aceptados: set[str] = set()
    for raw in _extraer_lista(
        _json_de(client.get(PATH_PROFILES), "GET", PATH_PROFILES), PATH_PROFILES
    ):
        perfil = _evaluar_perfil(raw)
        if perfil.aceptado and perfil.country in paises_aceptados:
            # GUARD (no supuesto): la platform (amazon_us/amazon_mx) es una
            # sola por pais; dos perfiles del mismo pais romperian el mapeo
            # entidad->plataforma. Gana el PRIMERO visto en el payload.
            perfil = replace(
                perfil,
                aceptado=False,
                platform=None,
                moneda=None,
                motivo=f"pais duplicado: ya se acepto otro perfil {perfil.country}",
            )
        perfiles.append(perfil)
        if perfil.aceptado:
            paises_aceptados.add(perfil.country)
    return perfiles


def perfiles_aceptados(client: AdsClient) -> list[PerfilAds]:
    """GET /v2/profiles -> SOLO los perfiles aceptados por el sello.

    Vista de evaluar_perfiles para los syncs que no necesitan la evidencia
    de rechazo (task 1.3: metricas de reporting v3). Los perfiles aceptados
    traen profile_id/platform/moneda fijados.
    """
    return [perfil for perfil in evaluar_perfiles(client) if perfil.aceptado]


def fetch_structure(client: AdsClient) -> EstructuraAds:
    """GET /v2/profiles + los 5 POST list v3 por cada perfil aceptado.

    Los perfiles rechazados (seller/pais/moneda/pais duplicado) NO generan
    llamadas de lista: su evidencia queda en `perfiles` con el motivo. El
    gate vive en evaluar_perfiles (unica fuente, regla 2).
    """
    perfiles = evaluar_perfiles(client)
    estructuras: list[EstructuraPerfil] = []
    for perfil in perfiles:
        if not perfil.aceptado:
            continue
        # aceptado implica profile_id/platform/moneda fijados por _evaluar_perfil
        estructuras.append(
            EstructuraPerfil(
                perfil=perfil,
                campanas=listar_todo(client, PATH_CAMPAIGNS, profile_id=perfil.profile_id),
                ad_groups=listar_todo(client, PATH_AD_GROUPS, profile_id=perfil.profile_id),
                keywords=listar_todo(client, PATH_KEYWORDS, profile_id=perfil.profile_id),
                targets=listar_todo(client, PATH_TARGETS, profile_id=perfil.profile_id),
                product_ads=listar_todo(client, PATH_PRODUCT_ADS, profile_id=perfil.profile_id),
            )
        )
    return EstructuraAds(perfiles=perfiles, estructuras=estructuras)
