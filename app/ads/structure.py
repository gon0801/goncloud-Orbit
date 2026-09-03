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
- listing_id: solo kind='product_ad' (ORBIT 06 0.4). Join
  (platform, asin del anuncio) -> listing.(platform, external_id). Los
  demas kinds quedan NULL; este camino NUNCA escribe ad_group.listing_id.
  ARCHIVED no se materializa (la ventana de 90d pierde atribucion; 0.7
  mide si importa).
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
campana / adGroup / keyword / target / product ad). Todo item valido cuenta como
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

Mapa de modulos (ESTRUCTURA 01): IO de API en app.ads.structure_api
(PATH_*, perfiles, listar_todo, fetch_structure); planificacion pura en
app.ads.structure_plan (items y skips, sin psycopg ni httpx); este modulo
queda como fachada + IO de DB (SQL sellada, sync_structure, main) y
re-exporta los nombres que importan app/, tools/ y tests/.

"""

from __future__ import annotations

import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass

import psycopg

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure_api import (  # noqa: F401 - fachada sellada (ESTRUCTURA 01)
    _CLAVE_CONTENEDORA,
    MAX_PAGINAS,
    PATH_AD_GROUPS,
    PATH_CAMPAIGNS,
    PATH_KEYWORDS,
    PATH_NEGATIVE_KEYWORDS,
    PATH_PRODUCT_ADS,
    PATH_PROFILES,
    PATH_TARGETS,
    AdsStructureError,
    EstructuraAds,
    EstructuraPerfil,
    PerfilAds,
    evaluar_perfiles,
    fetch_structure,
    listar_todo,
    perfiles_aceptados,
)
from app.ads.structure_plan import (
    _ETIQUETA_KIND,
    _ETIQUETA_PADRE,
    ESTADO_ARCHIVED,
    _archivados_por_plataforma,
    _formato_skip_reason,
    _plan_items,
)
from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "amazon_ads_structure_v2"

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

# Un product ad que YA seguimos y que Amazon reporta ARCHIVED: se marca en
# el cache. El filtro del borde descarta los archivados para no ingerir las
# decenas de miles que nunca seguimos (75% del inventario en MX), pero
# descartarlos TAMBIEN para los que ya existen dejaba el cache MINTIENDO:
# ad_entity_state es cache de "como esta hoy en Amazon" y se quedaba con el
# ENABLED viejo para siempre, porque el sync jamas los volvia a tocar.
# Lo destapo la limpieza del 2026-08-30: 203 anuncios archivados en vivo y
# confirmados por readback seguian ENABLED en la base tras el sync siguiente.
# No hay inferencia: el ARCHIVED viene EXPLICITO en el payload. Los que NO
# aparecen en el payload no se tocan (regla 3: ausencia no es muerte).
_SQL_MARCAR_ARCHIVADOS = """
    UPDATE ad_entity_state s
       SET status = %s, synced_at = now()
      FROM ad_entity e
     WHERE s.ad_entity_id = e.id
       AND e.platform = %s
       AND e.kind = 'product_ad'
       AND e.external_id = ANY(%s)
       AND s.status IS DISTINCT FROM %s
"""

_SQL_SELLAR_RUN = """
    UPDATE ingest_run
    SET finished_at = now(), rows_written = %s, rows_skipped = %s,
        skip_reason = %s, ok = %s
    WHERE id = %s
"""

# El upsert no toca listing_id: un INSERT de campaign/keyword con NULL no
# puede borrar un vinculo. Solo product_ad lo escribe despues (tambien NULL
# si el listing ya no esta).
_SQL_UPDATE_LISTING_ID = "UPDATE ad_entity SET listing_id = %s WHERE id = %s"

_SQL_CARGAR_LISTINGS = """
    SELECT l.id, l.platform::text, l.external_id,
           EXISTS (SELECT 1 FROM sku_cost c WHERE c.product_id = l.product_id)
    FROM listing l
"""


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
            listings: dict[tuple[str, str], tuple[int, bool]] = {
                (plat, ext): (lid, bool(tiene_costo))
                for lid, plat, ext, tiene_costo in conn.execute(_SQL_CARGAR_LISTINGS)
            }
            clasificacion: Counter[str] = Counter()
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
                if item.kind == "product_ad":
                    resuelto = listings.get((item.platform, item.asin or ""))
                    listing_id = resuelto[0] if resuelto else None
                    conn.execute(_SQL_UPDATE_LISTING_ID, (listing_id, entidad_id))
                    if listing_id is None:
                        clasificacion["product ad sin listing"] += 1
                    else:
                        clasificacion["product ad con listing"] += 1
                        if not resuelto[1]:
                            clasificacion["product ad sin costo"] += 1
                refs[(item.platform, item.kind, item.external_id)] = entidad_id
                written += 1
                if es_nueva:
                    nuevas += 1
                counts[(item.platform, item.kind)] += 1
            for plataforma, archivados in _archivados_por_plataforma(estructura).items():
                cur = conn.execute(
                    _SQL_MARCAR_ARCHIVADOS,
                    (ESTADO_ARCHIVED, plataforma, archivados, ESTADO_ARCHIVED),
                )
                if cur.rowcount:
                    clasificacion["product ad marcado archivado"] += cur.rowcount
                    # SUMA a rows_written: son filas de ad_entity_state
                    # REALMENTE escritas. Contarlas solo en skip_reason dejaba
                    # el ledger de la corrida diciendo menos escrituras de las
                    # que hubo (hallazgo cross-review codex 2026-08-30).
                    written += cur.rowcount

            skip_reason = _formato_skip_reason(skips + clasificacion)
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
