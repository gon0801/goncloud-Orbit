"""Tests del snapshot READ-ONLY de listas `tools/snapshot_listas` (ORBIT 05
preflight 1.3, decision sellada 3: conciliar contra Amazon, no contra
consistencia interna).

PUROS: sin red, sin base, sin psycopg — el "Amazon" es un cliente falso que
sirve paginas v3 canned (nextToken en 2 paginas, totalResults consistente,
contenedores reales keywords/negativeKeywords/targetingClauses) y el main se
prueba con la frontera fakeada (patron de tests/test_smoke_apply.py). Cubren:

1. agrupa_por_campana: agrupa por str(campaignId), conserva items verbatim;
   sin campaignId -> clave declarada "sin_campaignId" (regla 3).
2. conteos: (plataforma, recurso) -> total de items.
3. comparar_con_cache: filas con diferencia CON SIGNO sobre la UNION de
   claves; negativeKeywords queda con cache=None (sin espejo en ad_entity:
   declarado, no drop silencioso).
4. snapshot: shape exacto, agrupado por campana, conteos y superficie del
   cliente (solo los TRES paths allowlist, con el profile_id del perfil).
5. totalResults mentiroso -> excepcion: el guard de listar_todo vive en el
   camino que el tool usa.
6-9. main fail-closed (sin secrets SIN red, sin flags = error de uso,
   --platform sin perfiles), --solo-conteos imprime y no escribe, --out
   crea el dir y escribe el JSON exacto (600 en POSIX), filtro --platform.
10. el fuente del tool jamas importa app.ads.write / smoke_apply / psycopg /
    app.db (complemento puro del candado de test_architecture.py).
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import snapshot_listas as sl  # noqa: E402

from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.structure import (  # noqa: E402
    PATH_KEYWORDS,
    PATH_NEGATIVE_KEYWORDS,
    PATH_TARGETS,
    AdsStructureError,
    PerfilAds,
)

# ---------------------------------------------------------------------------
# Fixtures canned: items con el WIRE v3 real (ids string planos; el
# campaignId INT de KW1/KW2 prueba que el agrupado pasa por str(...)).
# ---------------------------------------------------------------------------

KW1 = {
    "keywordId": "7201",
    "campaignId": 7001,
    "adGroupId": "7101",
    "keywordText": "tenis",
    "matchType": "PHRASE",
    "state": "PAUSED",
}
KW2 = {
    "keywordId": "7202",
    "campaignId": 7001,
    "adGroupId": "7101",
    "keywordText": "zapato",
    "matchType": "EXACT",
    "state": "ENABLED",
}
KW3 = {
    "keywordId": "7203",
    "campaignId": "7002",
    "adGroupId": "7102",
    "keywordText": "correr",
    "matchType": "BROAD",
    "state": "ENABLED",
}
NEG = {
    "negativeKeywordId": "7401",
    "campaignId": 7001,
    "adGroupId": "7101",
    "keywordText": "gratis",
    "matchType": "NEGATIVE_EXACT",
    "state": "ENABLED",
}
T1 = {
    "targetId": "7301",
    "campaignId": 7001,
    "adGroupId": "7101",
    "expression": [{"type": "asinSameAs", "value": "B0TEST1234"}],
    "state": "ENABLED",
}
T2 = {
    "targetId": "7305",
    "campaignId": 7001,
    "adGroupId": "7101",
    "expression": [{"type": "queryHighRelMatches", "value": "tenis"}],
    "state": "PAUSED",
}

PAGINAS = {
    PATH_KEYWORDS: [
        {"keywords": [KW1, KW2], "nextToken": "p2", "totalResults": 3},
        {"keywords": [KW3], "totalResults": 3},
    ],
    PATH_NEGATIVE_KEYWORDS: [{"negativeKeywords": [NEG], "totalResults": 1}],
    PATH_TARGETS: [{"targetingClauses": [T1, T2], "totalResults": 2}],
}


class _Respuesta:
    """Lo unico que listar_todo pide de la respuesta: .json()."""

    def __init__(self, datos):
        self._datos = datos

    def json(self):
        return self._datos


class _ClienteFalso:
    """Amazon v3 canned: por path sirve la cola de paginas en orden (una
    llamada con body {} toma la primera; una con nextToken, la siguiente).
    Registra cada (path, body, profile_id) para los asserts de superficie."""

    def __init__(self, paginas: dict[str, list[dict]]):
        self._paginas = {path: list(cols) for path, cols in paginas.items()}
        self.llamadas: list[dict] = []

    def list_objects(self, path, body, *, profile_id=None):
        self.llamadas.append({"path": path, "body": dict(body), "profile_id": profile_id})
        cola = self._paginas.get(path)
        assert cola, f"llamada inesperada a {path}"
        return _Respuesta(cola.pop(0))


def _perfil(profile_id: int, platform: str, moneda: str) -> PerfilAds:
    pais = "MX" if platform == "amazon_mx" else "US"
    return PerfilAds(
        profile_id=profile_id,
        country=pais,
        currency_code=moneda,
        account_type="seller",
        valid_payment_method=True,
        account_name="cuenta de prueba",
        aceptado=True,
        platform=platform,
        moneda=moneda,
        motivo=None,
    )


def _perfil_us() -> PerfilAds:
    return _perfil(101, "amazon_us", "USD")


def _perfil_mx() -> PerfilAds:
    return _perfil(202, "amazon_mx", "MXN")


# Snapshot canned para los tests de main (la frontera fakeada lo devuelve
# tal cual; --out debe escribirlo EXACTO).
SNAPSHOT_FALSO = {
    "generado_utc": "2026-08-28T12:00:00+00:00",
    "plataformas": {
        "amazon_us": {
            "keywords": {"9001": [{"keywordId": "9201", "campaignId": "9001"}]},
            "negativeKeywords": {},
            "targetingClauses": {"9001": [{"targetId": "9301", "campaignId": "9001"}]},
        },
        "amazon_mx": {
            "keywords": {"7001": [{"keywordId": "7201", "campaignId": "7001"}]},
            "negativeKeywords": {},
            "targetingClauses": {},
        },
    },
    "resumen": {
        "amazon_us": {"keywords": 1, "negativeKeywords": 0, "targetingClauses": 1},
        "amazon_mx": {"keywords": 1, "negativeKeywords": 0, "targetingClauses": 0},
    },
}

_CREDS_FALSAS = AdsCredentials(
    client_id="fake-client-id",
    client_secret="fake-client-secret",
    refresh_token="fake-refresh-token",
)


class _ClienteInutilizado:
    """AdsClient falso para los tests de main: si algo intentara USARLO,
    revienta sin red (snapshot/perfiles_aceptados van fakeados)."""

    def __init__(self, *a, **kw):
        pass

    def get(self, *a, **kw):
        raise AssertionError("llamada de red inesperada")

    def list_objects(self, *a, **kw):
        raise AssertionError("llamada de red inesperada")


def _fakea_main(monkeypatch, *, perfiles: list[PerfilAds] | None = None) -> None:
    """Fakea la frontera del main: credenciales, clase de cliente, perfiles
    aceptados y snapshot (canned). Los tests que espiAN re-fakean despues."""
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(lambda cls: _CREDS_FALSAS))
    monkeypatch.setattr(sl, "AdsClient", _ClienteInutilizado)
    aceptados = [_perfil_us(), _perfil_mx()] if perfiles is None else perfiles
    monkeypatch.setattr(sl, "perfiles_aceptados", lambda cliente: list(aceptados))
    monkeypatch.setattr(sl, "snapshot", lambda perfiles_, cliente: SNAPSHOT_FALSO)


# ---------------------------------------------------------------------------
# 1-2. Partes puras: agrupado y conteos
# ---------------------------------------------------------------------------


def test_agrupa_por_campana_conserva_items_verbatim():
    grupos = sl.agrupa_por_campana([KW1, KW3, T1])
    assert grupos == {"7001": [KW1, T1], "7002": [KW3]}, (
        "agrupa por str(campaignId) (el INT 7001 y el string '7002' caen juntos "
        "con los suyos) conservando los items tal cual y en orden"
    )
    assert grupos["7001"][0] is KW1, "los items viajan VERBATIM (misma referencia)"


def test_agrupa_por_campana_sin_campaign_id_va_a_la_clave_declarada():
    """Regla 3: un item sin campaignId NO inventa id: va a la clave declarada."""
    huera = {"keywordId": "7777", "keywordText": "sin campana"}
    grupos = sl.agrupa_por_campana([KW1, huera])
    assert set(grupos) == {"7001", sl.SIN_CAMPAIGN_ID}
    assert sl.SIN_CAMPAIGN_ID == "sin_campaignId"
    assert grupos["sin_campaignId"] == [huera]


def test_conteos_por_recurso():
    plataformas = {
        "amazon_mx": {
            "keywords": {"7001": [KW1, KW2], "7002": [KW3]},
            "negativeKeywords": {"7001": [NEG], "sin_campaignId": [{"targetId": "1"}]},
            "targetingClauses": {},
        }
    }
    assert sl.conteos(plataformas) == {
        ("amazon_mx", "keywords"): 3,
        ("amazon_mx", "negativeKeywords"): 2,
        ("amazon_mx", "targetingClauses"): 0,
    }


# ---------------------------------------------------------------------------
# 3. comparar_con_cache: filas con diferencia CON SIGNO (no un bool)
# ---------------------------------------------------------------------------


def test_comparar_con_cache_filas_con_diferencia_con_signo():
    """El cache (ad_entity) usa kind 'keyword'/'product_target': el mapeo
    contenedor->kind lo hace el caller de la corrida real; la funcion pura
    solo difiere las dicts sobre la UNION de claves."""
    conteos_snapshot = {
        ("amazon_mx", "keywords"): 2645,
        ("amazon_mx", "negativeKeywords"): 12,
        ("amazon_us", "keywords"): 1336,
    }
    conteos_cache = {
        ("amazon_mx", "keywords"): 2645,
        ("amazon_mx", "product_target"): 861,
        ("amazon_us", "keywords"): 1330,
    }
    filas = sl.comparar_con_cache(conteos_snapshot, conteos_cache)
    por_clave = {(f["plataforma"], f["recurso"]): f for f in filas}
    assert set(por_clave) == set(conteos_snapshot) | set(conteos_cache), (
        "una fila por clave de la UNION"
    )
    assert por_clave[("amazon_mx", "keywords")] == {
        "plataforma": "amazon_mx",
        "recurso": "keywords",
        "snapshot": 2645,
        "cache": 2645,
        "diferencia": 0,
    }
    diverge = por_clave[("amazon_us", "keywords")]
    assert diverge["diferencia"] == 6, "snapshot - cache CON signo"
    solo_cache = por_clave[("amazon_mx", "product_target")]
    assert solo_cache["snapshot"] is None and solo_cache["diferencia"] == -861
    # negativeKeywords NO tiene espejo en ad_entity: el caller no la pasa en
    # el cache y la fila queda con cache=None — declarado, no drop silencioso.
    neg = por_clave[("amazon_mx", "negativeKeywords")]
    assert neg["cache"] is None and neg["diferencia"] == 12
    claves = [(f["plataforma"], f["recurso"]) for f in filas]
    assert claves == sorted(claves), "orden determinista por (plataforma, recurso)"


# ---------------------------------------------------------------------------
# 4-5. snapshot con cliente falso: shape, agrupado, superficie y guard
# ---------------------------------------------------------------------------


def test_snapshot_con_cliente_falso():
    cliente = _ClienteFalso(PAGINAS)
    snap = sl.snapshot([_perfil_mx()], cliente)

    assert set(snap) == {"generado_utc", "plataformas", "resumen"}
    assert dt.datetime.fromisoformat(snap["generado_utc"]).tzinfo is not None, (
        "generado_utc es ISO con zona (UTC)"
    )

    recursos = snap["plataformas"]["amazon_mx"]
    assert set(recursos) == {"keywords", "negativeKeywords", "targetingClauses"}
    assert recursos["keywords"] == {"7001": [KW1, KW2], "7002": [KW3]}
    assert recursos["negativeKeywords"] == {"7001": [NEG]}
    assert recursos["targetingClauses"] == {"7001": [T1, T2]}
    assert snap["resumen"] == {
        "amazon_mx": {"keywords": 3, "negativeKeywords": 1, "targetingClauses": 2}
    }

    # Al cliente solo llegaron los TRES paths allowlist (keywords x2: pagina
    # en dos) y SIEMPRE el profile_id del perfil.
    paths = [c["path"] for c in cliente.llamadas]
    assert sorted(paths) == sorted(
        [PATH_KEYWORDS, PATH_KEYWORDS, PATH_NEGATIVE_KEYWORDS, PATH_TARGETS]
    )
    assert all(c["profile_id"] == 202 for c in cliente.llamadas)
    # Paginacion v3 real: primera pagina body {}, segunda pidiendo nextToken.
    assert [c["body"] for c in cliente.llamadas if c["path"] == PATH_KEYWORDS] == [
        {},
        {"nextToken": "p2"},
    ]


def test_snapshot_total_results_inconsistente_explota():
    """El guard de listar_todo (totalResults vs acumulado) vive en el camino
    que el tool usa: un Amazon que declara 5 y manda 2 NO produce snapshot."""
    paginas = {
        PATH_KEYWORDS: [
            {"keywords": [KW1], "nextToken": "p2", "totalResults": 1},
            {"keywords": [KW2], "totalResults": 5},
        ],
        PATH_NEGATIVE_KEYWORDS: [{"negativeKeywords": [], "totalResults": 0}],
        PATH_TARGETS: [{"targetingClauses": [], "totalResults": 0}],
    }
    with pytest.raises(AdsStructureError, match="paginacion incompleta"):
        sl.snapshot([_perfil_mx()], _ClienteFalso(paginas))


# ---------------------------------------------------------------------------
# 6. main sin secrets: fail-closed SIN red
# ---------------------------------------------------------------------------


def test_main_sin_secrets_fail_closed_sin_red(monkeypatch, capsys, tmp_path):
    class _ClienteSinRed:
        def __init__(self, *a, **kw):
            raise AssertionError("AdsClient construido antes de las credenciales")

        def get(self, *a, **kw):
            raise AssertionError("llamada de red inesperada")

        def list_objects(self, *a, **kw):
            raise AssertionError("llamada de red inesperada")

    monkeypatch.setattr(sl, "AdsClient", _ClienteSinRed)
    for env in (None, str(tmp_path / "sin_secrets")):
        if env is None:
            monkeypatch.delenv("ORBIT_SECRETS_DIR", raising=False)
        else:
            monkeypatch.setenv("ORBIT_SECRETS_DIR", env)
        rc = sl.main(["--solo-conteos"])
        assert rc != 0, f"ORBIT_SECRETS_DIR={env!r}: debe fallar cerrado"
        assert capsys.readouterr().err.strip(), "mensaje claro en stderr"
    # Subcaso determinista (dir explicito que no existe): el mensaje nombra
    # credenciales y el exit es el fail-closed de la puerta.
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path / "sin_secrets"))
    assert sl.main(["--solo-conteos"]) == 2
    assert "credenciales" in capsys.readouterr().err


def test_main_sin_flags_error_de_uso(monkeypatch, capsys, tmp_path):
    """Sin --out y sin --solo-conteos no hay corrida: el tool siempre o
    imprime o escribe, explicito (exit != 0 antes de tocar nada)."""
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path / "sin_secrets"))
    rc = sl.main([])
    assert rc != 0
    err = capsys.readouterr().err
    assert "--out" in err and "--solo-conteos" in err


# ---------------------------------------------------------------------------
# 7-9. main fakeado: --solo-conteos, --out, --platform
# ---------------------------------------------------------------------------


def test_main_solo_conteos_imprime_resumen_y_no_escribe(monkeypatch, capsys, tmp_path):
    _fakea_main(monkeypatch)
    monkeypatch.chdir(tmp_path)
    rc = sl.main(["--solo-conteos"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "amazon_us/keywords: 1" in out
    assert "amazon_mx/keywords: 1" in out
    assert "negativeKeywords" in out and "targetingClauses" in out
    assert list(tmp_path.iterdir()) == [], "--solo-conteos JAMAS escribe archivo"


def test_main_out_crea_dir_y_escribe_json(monkeypatch, capsys, tmp_path):
    _fakea_main(monkeypatch)
    destino = tmp_path / "salida" / "listas"  # falta completo: hay que crearlo
    rc = sl.main(["--out", str(destino)])
    assert rc == 0
    archivo = destino / sl.ARCHIVO
    assert archivo.is_file()
    assert json.loads(archivo.read_text(encoding="utf-8")) == SNAPSHOT_FALSO, (
        "el JSON escrito es EXACTAMENTE el snapshot (shape sellado)"
    )
    assert "snapshot escrito" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "posix", reason="el umask 077 no aplica en Windows")
def test_main_out_deja_archivo_600_y_dir_700_en_posix(monkeypatch, tmp_path):
    """El tool FUERZA umask 077 el mismo (hallazgo reviewer 1.3): la prueba
    corre con una umask PERMISIVA (0o022) — si el tool no la forzara, el
    archivo naceria 644 y el test revienta; con 0o077 heredada era tautologico."""
    _fakea_main(monkeypatch)
    destino = tmp_path / "salida"
    umask_vieja = os.umask(0o022)
    try:
        assert sl.main(["--out", str(destino)]) == 0
        assert stat.S_IMODE(destino.stat().st_mode) == 0o700
        assert stat.S_IMODE((destino / sl.ARCHIVO).stat().st_mode) == 0o600
    finally:
        os.umask(umask_vieja)


def test_main_platform_filtra_a_ese_perfil(monkeypatch, capsys):
    _fakea_main(monkeypatch)
    recibidos: list[PerfilAds] = []

    def _snapshot_espia(perfiles, cliente):
        recibidos.extend(perfiles)
        return SNAPSHOT_FALSO

    monkeypatch.setattr(sl, "snapshot", _snapshot_espia)
    rc = sl.main(["--solo-conteos", "--platform", "amazon_mx"])
    assert rc == 0
    assert [p.platform for p in recibidos] == ["amazon_mx"], "filtra al perfil MX"
    assert "amazon_mx" in capsys.readouterr().out


def test_main_platform_sin_perfiles_fail_closed(monkeypatch, capsys):
    """--platform que no deja ningun perfil aceptado: mensaje claro y
    exit != 0, JAMAS un exito vacio (regla 3)."""
    _fakea_main(monkeypatch, perfiles=[_perfil_us()])
    corridas: list[int] = []
    monkeypatch.setattr(
        sl, "snapshot", lambda perfiles, cliente: corridas.append(1) or SNAPSHOT_FALSO
    )
    rc = sl.main(["--solo-conteos", "--platform", "amazon_mx"])
    assert rc != 0
    assert "amazon_mx" in capsys.readouterr().err
    assert corridas == [], "sin perfiles no se corre snapshot"


def test_main_platform_fuera_del_catalogo_rechaza():
    with pytest.raises(SystemExit):
        sl.parse_args(["--solo-conteos", "--platform", "amazon_de"])


# ---------------------------------------------------------------------------
# 10. El fuente del tool: jamas mutacion ni DB (test puro de texto/ast)
# ---------------------------------------------------------------------------


def test_tool_sin_imports_de_mutacion_ni_db():
    fuente = (Path(__file__).resolve().parent.parent / "tools" / "snapshot_listas.py").read_text(
        encoding="utf-8"
    )
    importados: set[str] = set()
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Import):
            importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)
            importados.update(f"{nodo.module}.{alias.name}" for alias in nodo.names)
    prohibidos = ("app.ads.write", "smoke_apply", "psycopg", "app.db")
    violaciones = sorted(
        i for i in importados if any(i == p or i.startswith(p + ".") for p in prohibidos)
    )
    assert not violaciones, (
        f"el snapshot es SOLO lectura (decision sellada 3): imports de "
        f"mutacion/DB prohibidos en tools/snapshot_listas.py: {violaciones}"
    )
