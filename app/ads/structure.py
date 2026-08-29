"""Sync de estructura Amazon Ads -> ad_entity + ad_entity_state (ORBIT 03, task 1.2).

EVIDENCIA DE LA CORRIDA REAL (2026-08-22, sondeo con credenciales reales;
todas las formas de abajo fueron verificadas con status 200):

- GET /v2/profiles vive (200). El payload REAL trae profileId (int),
  countryCode ("US"|"MX"|"CA"), currencyCode, dailyBudget, timezone y
  accountInfo{marketplaceStringId, id, type, name, validPaymentMethod}.
  NO existen los campos `country`, `valid` ni `account`: son
  countryCode/accountInfo.
- GET /v2/sp/campaigns y compania dan 404: Amazon RETIRO el campaign
  management v2. El unico camino de estructura es v3 LIST, que es POST:
  /sp/campaigns/list (application/vnd.spcampaign.v3+json),
  /sp/adGroups/list (vnd.spadgroup.v3+json), /sp/keywords/list
  (vnd.spkeyword.v3+json) y /sp/targets/list (vnd.sptargetingclause.v3+json).
  Sin esos vendor content-types la API responde 415. El cliente expone
  estos POST de lectura via `list_objects` (allowlist literal; la mutacion
  sigue sellada por el guard default-deny de `app.ads.client`).
- Paginacion v3: primera pagina con body {} (pageSize se ignora), siguientes
  con {"nextToken": ...}; la respuesta trae <contenedor>, totalResults y
  nextToken. La clave es `nextToken` (NO nextPageToken). Tope de seguridad:
  MAX_PAGINAS.
- Contenedores reales: campaigns, adGroups, keywords y targetingClauses.
  Los ids son strings planos.
- `bid` SOLO aparece en keywords de campanas manuales (831/1000 en la
  pagina 1 de US) y en 513/549 targets: su ausencia NO es error, es bid
  NULL con su moneda NULL (regla 3). `defaultBid` de ad group es escalar.
- `budget` de campana viene como {budget, budgetType} SIN moneda: no se
  guarda (regla 4; ademas current_bid es el bid de la entidad, no el
  presupuesto).
- acos_target: CONFIRMADO ausente en todos los items de la corrida real
  (2026-08-22) -> queda NULL por diseno, no por omision.
- Totales reales de la corrida del 2026-08-22 (5897 entidades escritas,
  0 skips): US 74 campanas / 79 ad groups / 1334 keywords / 549 targets;
  MX 167 / 188 / 2645 / 861.

Arquitectura (regla 1): IO de API separada de IO de DB.

    fetch_structure(client)          -> EstructuraAds (dataclasses + payloads crudos)
    sync_structure(conn, estructura) -> ResultadoSync (ad_entity, ad_entity_state,
                                         ingest_run)

El gate de perfiles (evaluar_perfiles/perfiles_aceptados) es la UNICA fuente
del sello seller/pais/moneda/1-pais-por-pais: app.ads.reports lo reutiliza
para las metricas de reporting v3 (regla 2, task 1.3).

Decisiones selladas de esta task:

- Perfil: countryCode US -> platform amazon_us (moneda esperada USD); MX ->
  amazon_mx (MXN). Se exige ademas accountInfo.type == "seller" (si falta o
  es otro: rechazo con motivo) y currencyCode coherente con el pais; si no
  corresponde, el perfil se RECHAZA entero con motivo: el sello de moneda de
  las metricas es el trigger metric_moneda_de_plataforma, que NO cubre
  ad_entity_state; para el estado la validacion es responsabilidad de este
  codigo. No existe campo `valid` en la API: se registra validPaymentMethod
  como evidencia, sin gatearlo. SUPUESTO DECLARADO: un solo perfil aceptado
  por pais -- si dos perfiles del mismo countryCode existieran, colapsarian
  a la misma platform y un choque de external_ids entre ellos se resolveria
  como la misma entidad (los ids de Amazon son por marketplace; hoy hay
  exactamente un perfil US y uno MX).
- Un perfil por pais (GUARD, no supuesto; hallazgo cross-review codex ronda 3):
  la platform (amazon_us/amazon_mx) es una sola por pais, asi que el segundo
  perfil aceptable con el mismo countryCode se RECHAZA con motivo "pais
  duplicado". Gana el primero en el orden del payload /v2/profiles.
- state.acos_target es SIEMPRE None (confirmado por corrida real, arriba).
- listing_id queda NULL: el catalogo no esta en el camino de la task 1.2.
- name: campana/ad group -> payload.name; keyword -> NULL (keyword_text es la
  fuente unica, regla 2); target -> JSON compacto de `expression`
  (separators sin espacios + sort_keys) o NULL si no viene.
- Padre: la referencia se resuelve SOLO contra entidades escritas en ESTA
  corrida (mapa en memoria). Si el padre no vino en el payload (o fue
  saltado en runtime), el hijo se salta con motivo; NO se consulta la base
  por padres de corridas viejas (fail-closed y determinista).
- Inmutabilidad por permisos: app_ingest solo puede hacer UPDATE de
  name/listing_id en ad_entity. Si en re-sync una entidad existente difiere
  del payload en parent_id, match_type o keyword_text (los inmutables por
  permisos), el item se SALTA con motivo (jamas se intenta UPDATE), su
  estado NO se escribe y sus hijos se saltan en cascada. Antes de la ronda 3
  de cross-review, una divergencia de keyword_text/match_type contaba como
  escrita conservando el valor viejo, en silencio. NOTA: el upsert de la entidad ya corrio cuando se
  detecta la divergencia, asi que su `name` SI queda refrescado en ese
  camino (legal: es la unica columna mutable por permisos, y refrescar el
  nombre es idempotente); lo que el skip garantiza es que NI el estado NI
  la referencia como padre se escriben.

Semantica contable (ingest_run): la unidad es el ITEM de payload (cada
campana / adGroup / keyword / target). Todo item valido cuenta como
rows_written (siempre escribe su ad_entity_state con synced_at = now()); todo
item rechazado cuenta como rows_skipped con su motivo acumulado en
skip_reason (unico TEXT con contadores: "2x keyword sin matchType, ...").
Los perfiles RECHAZADOS no generan items: su evidencia (id, countryCode,
currencyCode, accountInfo, motivo) queda en EstructuraAds.perfiles para
anotarla AFUERA (el __main__ la imprime por stdout).

ingest_run nace abierta en su PROPIA transaccion (queda registrada aunque el
resto reviente), el trabajo corre en otra, y se sella al final por columnas
(finished_at, rows_written, rows_skipped, skip_reason, ok) con source =
'amazon_ads_structure_v2'. En fallo: rollback del trabajo y sello best-effort
ok=false.

Respuesta defensiva: /v2/profiles acepta lista JSON o dict con clave
contenedora (p.ej. {"profiles": [...]}); las listas v3 responden dict con
<contenedor> + totalResults + nextToken (si no trajera la clave esperada
pero hubiera exactamente una lista, se usa esa). Cualquier otra forma es
error claro.

`python -m app.ads.structure`: carga AdsCredentials.from_secrets_dir() y la
DSN de ORBIT_DSN_INGEST (via app.db.connect), corre fetch+sync e imprime por
stdout un resumen SIN secretos. Sin ORBIT_DSN_INGEST -> mensaje claro y
exit != 0 (fail-closed).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

import psycopg

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "amazon_ads_structure_v2"

# Unico GET v2 que sigue vivo. Los cuatro de abajo son POST list v3 (ver
# docstring): el allowlist y sus vendor content-types viven en app.ads.client.
PATH_PROFILES = "/v2/profiles"
PATH_CAMPAIGNS = "/sp/campaigns/list"
PATH_AD_GROUPS = "/sp/adGroups/list"
PATH_KEYWORDS = "/sp/keywords/list"
PATH_TARGETS = "/sp/targets/list"
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

# Etiquetas legibles para los motivos de skip (sin acronimos de tabla).
_ETIQUETA_KIND = {
    "campaign": "campana",
    "ad_group": "ad group",
    "keyword": "keyword",
    "product_target": "target",
}
_ETIQUETA_PADRE = {
    "campaign": "campana",
    "ad_group": "campana",
    "keyword": "ad group",
    "product_target": "ad group",
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
}

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"

# xmax = 0 distingue INSERT (0) del UPDATE que dejo el mismo row (xid de la
# transaccion en curso). DO UPDATE solo toca `name`: parent_id, match_type y
# keyword_text vuelven con el valor EXISTENTE, que es justo lo que hay que
# comparar contra el payload (divergencia de inmutables = skip, jamas UPDATE;
# hallazgo cross-review codex, ronda 3).
_SQL_UPSERT_ENTIDAD = """
    INSERT INTO ad_entity
        (platform, kind, external_id, parent_id, name, match_type, keyword_text)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (platform, kind, external_id) DO UPDATE SET name = EXCLUDED.name
    RETURNING id, parent_id, match_type, keyword_text, (xmax = 0) AS es_nueva
"""

# acos_target siempre NULL (confirmado por corrida real 2026-08-22): se
# declara en el SET para que el conflict-path quede explicito y honesto con
# la fuente.
_SQL_UPSERT_STATE = """
    INSERT INTO ad_entity_state
        (ad_entity_id, current_bid, bid_currency, status, targeting_type,
         acos_target, synced_at)
    VALUES (%s, %s, %s, %s, %s, NULL, now())
    ON CONFLICT (ad_entity_id) DO UPDATE SET
        current_bid = EXCLUDED.current_bid,
        bid_currency = EXCLUDED.bid_currency,
        status = EXCLUDED.status,
        targeting_type = EXCLUDED.targeting_type,
        acos_target = EXCLUDED.acos_target,
        synced_at = now()
"""

_SQL_SELLAR_RUN = """
    UPDATE ingest_run
    SET finished_at = now(), rows_written = %s, rows_skipped = %s,
        skip_reason = %s, ok = %s
    WHERE id = %s
"""


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


@dataclass
class EstructuraAds:
    """Salida de fetch_structure: evidencia de TODOS los perfiles + payloads."""

    perfiles: list[PerfilAds]
    estructuras: list[EstructuraPerfil]


@dataclass
class _ItemEntidad:
    """Un item planificado: lo que se escribe si ningun gate lo salta."""

    platform: str
    kind: str
    external_id: str
    parent_ref: tuple[str, str, str] | None  # (platform, kind, external_id) del padre
    name: str | None
    match_type: str | None
    keyword_text: str | None
    status: str | None
    targeting_type: str | None
    bid: Decimal | None
    bid_currency: str | None


@dataclass
class ResultadoSync:
    """Outcome contable de la corrida (espejo de la ingest_run sellada)."""

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    counts: dict[tuple[str, str], int]  # (platform, kind) -> items escritos
    entidades_nuevas: int


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
    """GET /v2/profiles + los 4 POST list v3 por cada perfil aceptado.

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
            )
        )
    return EstructuraAds(perfiles=perfiles, estructuras=estructuras)


# ---------------------------------------------------------------------------
# Planificacion pura (sin DB): items validos + skips con motivo
# ---------------------------------------------------------------------------


def _bid_decimal(valor: object, campo: str) -> Decimal | None:
    """Convierte un bid del payload a Decimal (via str, sin meter float a NUMERIC).

    None (ausente o null) -> None (regla 3: en keywords de campanas AUTO y en
    parte de los targets la API NO trae `bid`; corrida real 2026-08-22).
    Un valor presente pero no numerico (o no finito) es dato corrupto:
    ValueError para que el item se salte con motivo, no un numero inventado.
    Un bid <= 0 tambien es payload invalido (hallazgo CodeRabbit): Amazon no
    publica pujas no positivas y el esquema sella la positividad donde puede
    (goal_bids_positivos); para el cache, la puerta es esta.
    """
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        raise ValueError(f"{campo} no numerico")
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{campo} no numerico") from None
    if not numero.is_finite() or numero <= 0:
        raise ValueError(f"{campo} no numerico o no positivo")
    return numero


def _nombre_target(expression: object) -> str | None:
    """JSON compacto y determinista de `expression` (None si no viene)."""
    if expression is None:
        return None
    return json.dumps(expression, separators=(",", ":"), sort_keys=True)


def _plan_items(estructura: EstructuraAds) -> tuple[list[_ItemEntidad], Counter[str]]:
    """Valida la coherencia de cada item ANTES de tocar la base.

    Orden por perfil: campanas -> ad groups -> keywords -> targets (el padre
    siempre se planifica antes que el hijo). Un item solo entra si su padre
    esta entre los items ya planificados DE ESTA CORRIDA y, para keywords y
    targets, si el campaignId que el payload trae DE MAS no contradice al del
    ad group padre planificado (hallazgo cross-review codex, ronda 3). Claves
    v3: ids string planos, defaultBid escalar, bid OPCIONAL (su ausencia no
    es skip).
    """
    items: list[_ItemEntidad] = []
    skips: Counter[str] = Counter()

    for est in estructura.estructuras:
        platform = est.perfil.platform or ""
        moneda = est.perfil.moneda
        conocidos: set[tuple[str, str, str]] = set()
        # adGroupId -> campaignId con el que se planifico el ad group (para
        # validar la coherencia del campaignId que keyword/target traen DE
        # MAS en su payload; hallazgo cross-review codex, ronda 3).
        campana_del_ad_group: dict[str, str] = {}

        for payload in est.campanas:
            external = payload.get("campaignId")
            if external is None:
                skips["campana sin campaignId"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="campaign",
                    external_id=str(external),
                    parent_ref=None,
                    name=payload.get("name"),
                    match_type=None,
                    keyword_text=None,
                    status=payload.get("state"),
                    targeting_type=payload.get("targetingType"),
                    bid=None,  # budget sin moneda: no se guarda (docstring)
                    bid_currency=None,
                )
            )
            conocidos.add((platform, "campaign", str(external)))

        for payload in est.ad_groups:
            external = payload.get("adGroupId")
            if external is None:
                skips["ad group sin adGroupId"] += 1
                continue
            if (platform, "campaign", str(payload.get("campaignId"))) not in conocidos:
                skips["ad group sin campana planificada"] += 1
                continue
            try:
                bid = _bid_decimal(payload.get("defaultBid"), "defaultBid")
            except ValueError:
                skips["ad group con defaultBid no numerico o no positivo"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="ad_group",
                    external_id=str(external),
                    parent_ref=(platform, "campaign", str(payload.get("campaignId"))),
                    name=payload.get("name"),
                    match_type=None,
                    keyword_text=None,
                    status=payload.get("state"),
                    targeting_type=None,
                    bid=bid,
                    bid_currency=moneda if bid is not None else None,
                )
            )
            conocidos.add((platform, "ad_group", str(external)))
            campana_del_ad_group[str(external)] = str(payload.get("campaignId"))

        for payload in est.keywords:
            external = payload.get("keywordId")
            if external is None:
                skips["keyword sin keywordId"] += 1
                continue
            keyword_text = payload.get("keywordText")
            if not keyword_text:
                skips["keyword sin keywordText"] += 1
                continue
            match_type = payload.get("matchType")
            if not match_type:
                skips["keyword sin matchType"] += 1
                continue
            ad_group_id = str(payload.get("adGroupId"))
            if (platform, "ad_group", ad_group_id) not in conocidos:
                skips["keyword sin ad group planificado"] += 1
                continue
            campaign_id = payload.get("campaignId")
            if campaign_id is not None and str(campaign_id) != campana_del_ad_group[ad_group_id]:
                skips[
                    "keyword con campaignId distinto al de su ad group (payload incoherente)"
                ] += 1
                continue
            try:
                bid = _bid_decimal(payload.get("bid"), "bid")
            except ValueError:
                skips["keyword con bid no numerico o no positivo"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="keyword",
                    external_id=str(external),
                    parent_ref=(platform, "ad_group", ad_group_id),
                    name=None,  # keyword_text es la fuente unica (regla 2)
                    match_type=match_type,
                    keyword_text=keyword_text,
                    status=payload.get("state"),
                    targeting_type=None,
                    bid=bid,
                    bid_currency=moneda if bid is not None else None,
                )
            )
            conocidos.add((platform, "keyword", str(external)))

        for payload in est.targets:
            external = payload.get("targetId")
            if external is None:
                skips["target sin targetId"] += 1
                continue
            ad_group_id = str(payload.get("adGroupId"))
            if (platform, "ad_group", ad_group_id) not in conocidos:
                skips["target sin ad group planificado"] += 1
                continue
            campaign_id = payload.get("campaignId")
            if campaign_id is not None and str(campaign_id) != campana_del_ad_group[ad_group_id]:
                skips["target con campaignId distinto al de su ad group (payload incoherente)"] += 1
                continue
            try:
                bid = _bid_decimal(payload.get("bid"), "bid")
            except ValueError:
                skips["target con bid no numerico o no positivo"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="product_target",
                    external_id=str(external),
                    parent_ref=(platform, "ad_group", ad_group_id),
                    name=_nombre_target(payload.get("expression")),
                    match_type=None,
                    keyword_text=None,
                    status=payload.get("state"),
                    targeting_type=None,
                    bid=bid,
                    bid_currency=moneda if bid is not None else None,
                )
            )
            conocidos.add((platform, "product_target", str(external)))

    return items, skips


def _formato_skip_reason(skips: Counter[str]) -> str | None:
    """Concatena motivos con contador, deterministicamente (mas frecuentes primero)."""
    if not skips:
        return None
    partes = [
        f"{cantidad}x {motivo}"
        for motivo, cantidad in sorted(skips.items(), key=lambda par: (-par[1], par[0]))
    ]
    return ", ".join(partes)


# ---------------------------------------------------------------------------
# IO de DB: sync_structure
# ---------------------------------------------------------------------------


def _sellar_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    rows_written: int | None = None,
    rows_skipped: int | None = None,
    skip_reason: str | None = None,
) -> None:
    conn.execute(_SQL_SELLAR_RUN, (rows_written, rows_skipped, skip_reason, ok, run_id))


def sync_structure(conn: psycopg.Connection, estructura: EstructuraAds) -> ResultadoSync:
    """Escribe ad_entity + ad_entity_state y sella la ingest_run.

    La corrida nace abierta en su propia transaccion y se sella al final. Si
    el trabajo revienta: rollback y sello best-effort ok=false (la corrida
    queda registrada como fallo, sin filas a medias), y la excepcion sube.
    """
    items, skips = _plan_items(estructura)

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    written = 0
    nuevas = 0
    counts: Counter[tuple[str, str]] = Counter()
    try:
        with conn.transaction():
            refs: dict[tuple[str, str, str], int] = {}
            for item in items:
                parent_id = refs.get(item.parent_ref) if item.parent_ref else None
                if item.parent_ref is not None and parent_id is None:
                    # El padre era valido en el payload pero se salto en runtime
                    # (p.ej. parent_id inmutable distinto): cascada fail-closed.
                    etiqueta = _ETIQUETA_KIND[item.kind]
                    padre = _ETIQUETA_PADRE[item.kind]
                    skips[f"{etiqueta} sin {padre} escrito en esta corrida"] += 1
                    continue
                row = conn.execute(
                    _SQL_UPSERT_ENTIDAD,
                    (
                        item.platform,
                        item.kind,
                        item.external_id,
                        parent_id,
                        item.name,
                        item.match_type,
                        item.keyword_text,
                    ),
                ).fetchone()
                entidad_id, parent_actual, match_actual, keyword_text_actual, es_nueva = row
                if parent_actual != parent_id:
                    # Columna inmutable por permisos: jamas UPDATE, skip con motivo.
                    etiqueta = _ETIQUETA_KIND[item.kind]
                    skips[f"{etiqueta} con parent_id distinto al existente (inmutable)"] += 1
                    continue
                if match_actual != item.match_type or keyword_text_actual != item.keyword_text:
                    # Divergencia del resto de inmutables (match_type/keyword_text,
                    # solo keywords): mismo trato que el padre, sin estado escrito
                    # y con la divergencia EXPRESA en skip_reason (antes contaba
                    # como escrita conservando el valor viejo, en silencio).
                    etiqueta = _ETIQUETA_KIND[item.kind]
                    skips[
                        f"{etiqueta} con keyword_text/match_type distinto al existente (inmutable)"
                    ] += 1
                    continue
                conn.execute(
                    _SQL_UPSERT_STATE,
                    (
                        entidad_id,
                        item.bid,
                        item.bid_currency,
                        item.status,
                        item.targeting_type,
                    ),
                )
                refs[(item.platform, item.kind, item.external_id)] = entidad_id
                written += 1
                if es_nueva:
                    nuevas += 1
                counts[(item.platform, item.kind)] += 1
            skip_reason = _formato_skip_reason(skips)
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=written,
                rows_skipped=sum(skips.values()),
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        # El with de arriba ya hizo rollback del trabajo; la run abierta se
        # sella como fallo si la conexion sigue viva (best-effort). Si el
        # sello TAMBIEN falla, se deja rastro en el log: una run eternamente
        # abierta solo se descubre consultando ingest_run. BaseException (no
        # solo Exception): un KeyboardInterrupt en una corrida larga tambien
        # deja la run sellada, no abierta (hallazgo cross-review).
        # rows_skipped=0 EXPLICITO: la columna es NOT NULL DEFAULT 0 y el
        # DEFAULT no aplica en UPDATE -- pasar None violaba la constraint y
        # el suppress se lo tragaba, dejando la run abierta para siempre (bug
        # cazado por el test del sello en fallo). 0 es la verdad contable de
        # una corrida que no llego a terminar su trabajo.
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_skipped=0,
                    # str(KeyboardInterrupt) es "": el nombre del tipo es el
                    # rastro honesto minimo para esa familia de excepciones.
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo; "
                "el error original de la corrida era: %s",
                run_id,
                scrub(str(exc)),
            )
        raise

    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=written,
        rows_skipped=sum(skips.values()),
        skip_reason=skip_reason,
        counts=dict(counts),
        entidades_nuevas=nuevas,
    )


# ---------------------------------------------------------------------------
# __main__: corrida real contra goncloud (resumen por stdout, sin secretos)
# ---------------------------------------------------------------------------


def _imprimir_resumen(estructura: EstructuraAds, resultado: ResultadoSync) -> None:
    print(f"== Sync de estructura Amazon Ads ({SOURCE}) ==")
    print(f"Perfiles vistos: {len(estructura.perfiles)}")
    for perfil in estructura.perfiles:
        if perfil.aceptado:
            tratamiento = f"{perfil.platform} (aceptado)"
        else:
            tratamiento = f"RECHAZADO: {perfil.motivo}"
        print(
            f"  perfil {perfil.profile_id} countryCode={perfil.country} "
            f"currency={perfil.currency_code} account_type={perfil.account_type} "
            f"validPaymentMethod={perfil.valid_payment_method} "
            f"account={perfil.account_name!r} -> {tratamiento}"
        )
    print("Entidades escritas por (platform, kind):")
    for (platform, kind), cantidad in sorted(resultado.counts.items()):
        print(f"  {platform}/{kind}: {cantidad}")
    print(
        f"ingest_run {resultado.run_id}: ok={resultado.ok} "
        f"rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped} "
        f"entidades_nuevas={resultado.entidades_nuevas}"
    )
    if resultado.skip_reason:
        print(f"skip_reason: {resultado.skip_reason}")


def main() -> int:
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede escribir la estructura (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        estructura = fetch_structure(AdsClient(AdsCredentials.from_secrets_dir()))
        conn = connect(dsn)
        try:
            resultado = sync_structure(conn, estructura)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "sync de estructura fallo (la ingest_run quedo sellada ok=false cuando "
            f"fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1
    _imprimir_resumen(estructura, resultado)
    # resultado.ok es siempre True aqui: sync_structure solo retorna en exito
    # (los fallos levantan excepcion y salen por el except de arriba).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
