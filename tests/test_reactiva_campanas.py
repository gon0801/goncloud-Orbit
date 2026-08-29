"""Tests del modo --solo-campana de tools/reactiva_campanas (ORBIT 05
preflight 1.6a, paso 1: reactivar SOLO la campana 3919 — USPerNog, external
251723662158466, hoy PAUSED — sin tocar las pausas de dedup ni las otras
campanas del camino sellado de PR #37).

PUROS: sin red, sin base, sin psycopg — la base es una conn falsa que sirve
filas enlatadas y graba cada (sql, params); el "Amazon" del guard es un
AdsClient falso que responde el /sp/campaigns/list canned; la frontera del
main se fakea igual que en tests/test_snapshot_listas.py (_fakea_main). El
http falso REVIENTA en .put/.post: que el test llegue al final demuestra que
no hubo NI mutacion NI token LWA. Cubren:

1. --solo-campana reduce el plan a ESA campana (external_id, platform,
   status y nombre resuelto de e.name) con CERO pausas de dedup:
   exactamente 1 query contra la base y el log campana_resuelta con el
   nombre resuelto.
2. campana inexistente -> Abortar fail-closed con el id en el mensaje.
3. camino original sin flag INTACTO: len(CAMPANAS_REACTIVAR) campanas (cada
   una con su nombre en el dict) y len(PAUSAS_DEDUP) keywords de dedup —
   el comportamiento de master queda igual.
4. main dry-run --solo-campana 3919: campana_resuelta (external
   251723662158466), estado_vivo PAUSED y dry_run con pausas=0 resumes=1;
   sin ningun intento de PUT.
5. main --solo-campana con la campana YA ENABLED en Amazon: Abortar "no se
   muta" (guard del runbook 1.6a, tambien dispara en dry-run); sin PUT.
6. main --solo-campana --acepto-mutacion-real: la fase 2 COMPLETA el resume
   de la 3919 (regresion del review r2: con el orden hardcodeado
   [3909]+... reventia KeyError 3909 ANTES del PUT) con el shape sellado
   exacto: UN PUT a /sp/campaigns, id STRING, state ENABLED, vendor v3 en
   Content-Type Y Accept, readback ENABLED y reconciliacion 0/1.
7. _orden_resumes: 3909 encabeza SOLO si esta en el plan; --solo-campana es
   la unica fila del orden.
8. e.name NULL -> el nombre declarado es el external_id (regla 3: jamas
   constante inventada).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import reactiva_campanas as rc  # noqa: E402

from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.structure import PerfilAds  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures canned: la campana del preflight 1.6a tal como vive en la base.
# ---------------------------------------------------------------------------

SOLO_CAMPANA = 3919  # id interno ad_entity
SOLO_EXTERNAL = "251723662158466"  # 'USPerNog - Category Exact - US'
SOLO_NOMBRE = "USPerNog - Category Exact - US"


class _CursorFalso:
    """Lo unico que _resolver_plan pide: fetchone()/fetchall()."""

    def __init__(self, filas: list):
        self._filas = filas

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


class _ConnFalsa:
    """Conexion enlatada: cada execute() consume el SIGUIENTE resultado de la
    cola (en el orden del camino original: campanas primero, keywords despues)
    y graba (sql, params) para los asserts de superficie."""

    def __init__(self, resultados: list):
        self._resultados = list(resultados)
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if not self._resultados:
            raise AssertionError("execute inesperado: no quedan resultados enlatados")
        return _CursorFalso(self._resultados.pop(0))


def _conn_solo_campana(status: str = "PAUSED", nombre: str | None = SOLO_NOMBRE):
    """Una sola fila enlatada: (external_id, platform, name, status)."""
    return _ConnFalsa([[(SOLO_EXTERNAL, "amazon_us", nombre, status)]])


def _conn_camino_original() -> _ConnFalsa:
    """Cola del camino original: una fila por campana de CAMPANAS_REACTIVAR y
    una por pausa de PAUSAS_DEDUP, con los campos que pide cada SQL."""
    resultados = [[(f"camp-ext-{cid}", "amazon_us", "PAUSED")] for cid in rc.CAMPANAS_REACTIVAR]
    resultados += [[(f"kw-ext-{i}", "ENABLED", "amazon_us")] for i in range(len(rc.PAUSAS_DEDUP))]
    return _ConnFalsa(resultados)


# ---------------------------------------------------------------------------
# Fakes de la frontera del main (patron _fakea_main de test_snapshot_listas).
# ---------------------------------------------------------------------------


class _Respuesta:
    """Lo minimo que _readback_estado y _put piden: status_code, json() y
    text (para el scrub del cuerpo crudo)."""

    def __init__(self, status_code: int, datos: dict, texto: str = ""):
        self.status_code = status_code
        self._datos = datos
        self.text = texto

    def json(self):
        return self._datos


class _ClienteAdsFalso:
    """AdsClient canned: /sp/campaigns/list responde la campana con el estado
    indicado; cualquier otro path (o un GET) revienta — el guard del 1.6a es
    el UNICO consumidor vivo del cliente en estos tests. Con `secuencia`, cada
    llamada consume el siguiente estado (guard PAUSED y readback post-PUT
    ENABLED en la misma corrida)."""

    def __init__(self, estado_vivo: str = "PAUSED", secuencia: list[str] | None = None):
        self._estado = estado_vivo
        self._secuencia = list(secuencia or [])
        self.llamadas: list[dict] = []

    def list_objects(self, path, body, *, profile_id=None):
        self.llamadas.append({"path": path, "body": dict(body), "profile_id": profile_id})
        assert path == "/sp/campaigns/list", f"path inesperado: {path}"
        estado = self._secuencia.pop(0) if self._secuencia else self._estado
        return _Respuesta(
            200,
            {"campaigns": [{"campaignId": SOLO_EXTERNAL, "state": estado}]},
            texto='{"state": "ok"}',
        )

    def get(self, *a, **kw):
        raise AssertionError("GET inesperado en el camino del guard")


class _HttpFalso:
    """httpx.Client falso: en dry-run JAMAS abre red. Un .put (mutacion) o un
    .post (token LWA) REVIENTA — asi el test demuestra que NO hubo intento."""

    def put(self, *a, **kw):
        raise AssertionError("PUT a Amazon inesperado")

    def post(self, *a, **kw):
        raise AssertionError("POST inesperado (token LWA) donde no correspondia")

    def close(self):
        pass


class _HttpQueResume:
    """httpx.Client falso para la fase 2 de --solo-campana: CONTESTA los PUT
    con ack 200 sin errores del 207 y graba cada llamada (url, headers,
    payload) para afirmar el shape sellado."""

    def __init__(self):
        self.puts: list[dict] = []

    def put(self, url, headers=None, json=None):
        self.puts.append({"url": url, "headers": dict(headers or {}), "payload": json})
        return _Respuesta(200, {}, texto='{"campaigns": []}')

    def close(self):
        pass


_CREDS_FALSAS = AdsCredentials(
    client_id="fake-client-id",
    client_secret="fake-client-secret",
    refresh_token="fake-refresh-token",
)


def _perfil_us() -> PerfilAds:
    return PerfilAds(
        profile_id=101,
        country="US",
        currency_code="USD",
        account_type="seller",
        valid_payment_method=True,
        account_name="cuenta de prueba",
        aceptado=True,
        platform="amazon_us",
        moneda="USD",
        motivo=None,
    )


def _fakea_main(
    monkeypatch,
    conn: _ConnFalsa,
    estado_vivo: str = "PAUSED",
    http=None,
    secuencia: list[str] | None = None,
) -> _ClienteAdsFalso:
    """Fakea la frontera del main: credenciales, DSN+connect, AdsClient,
    perfiles aceptados y httpx (todo canned, cero red). `http` permite
    inyectar el _HttpQueResume de la fase 2."""
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(lambda cls: _CREDS_FALSAS))
    monkeypatch.setattr(rc, "_dsn_read", lambda: "dsn-falso")
    monkeypatch.setattr(rc, "connect", lambda dsn: conn)
    cliente = _ClienteAdsFalso(estado_vivo, secuencia=secuencia)
    monkeypatch.setattr(rc, "AdsClient", lambda cred: cliente)
    monkeypatch.setattr(rc, "evaluar_perfiles", lambda cliente_lectura: [_perfil_us()])
    monkeypatch.setattr(
        rc,
        "httpx",
        SimpleNamespace(Client=lambda **kw: http or _HttpFalso(), Timeout=lambda **kw: None),
    )
    return cliente


def _eventos(capsys) -> list[dict]:
    """Las lineas JSON que _log imprime (scrub no toca estos valores)."""
    return [
        json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith("{")
    ]


# ---------------------------------------------------------------------------
# 1-3. _resolver_plan: modo --solo-campana y camino original intacto
# ---------------------------------------------------------------------------


def test_solo_campana_reduce_el_plan_a_una_campana_y_cero_pausas(capsys):
    conn = _conn_solo_campana()
    campanas, keywords = rc._resolver_plan(conn, solo_campana=SOLO_CAMPANA)

    assert list(campanas) == [SOLO_CAMPANA]
    assert campanas[SOLO_CAMPANA] == {
        "external_id": SOLO_EXTERNAL,
        "platform": "amazon_us",
        "status": "PAUSED",
        "nombre": SOLO_NOMBRE,
    }
    assert keywords == [], "modo 1.6a: CERO pausas de dedup"
    assert len(conn.queries) == 1, "UN solo SELECT contra la base"
    sql, params = conn.queries[0]
    assert params == (SOLO_CAMPANA,)
    assert "keyword" not in sql.lower(), "ni se consultan las keywords de dedup"
    # El log usa el nombre RESUELTO de la base (e.name), no una constante.
    resueltas = [e for e in _eventos(capsys) if e["evento"] == "campana_resuelta"]
    assert len(resueltas) == 1
    assert resueltas[0]["id"] == SOLO_CAMPANA
    assert resueltas[0]["nombre"] == SOLO_NOMBRE


def test_solo_campana_inexistente_aborta_fail_closed():
    conn = _ConnFalsa([[]])  # fetchone() -> None: la campana no existe
    with pytest.raises(rc.Abortar, match=str(SOLO_CAMPANA)):
        rc._resolver_plan(conn, solo_campana=SOLO_CAMPANA)


def test_camino_original_sin_flag_intacto():
    conn = _conn_camino_original()
    campanas, keywords = rc._resolver_plan(conn)

    assert set(campanas) == set(rc.CAMPANAS_REACTIVAR)
    for cid, nombre in rc.CAMPANAS_REACTIVAR.items():
        assert set(campanas[cid]) == {"external_id", "platform", "status", "nombre"}
        assert campanas[cid]["nombre"] == nombre, (
            "el dict lleva el nombre: el resume ya no indexa CAMPANAS_REACTIVAR"
        )
    assert len(keywords) == len(rc.PAUSAS_DEDUP)
    assert [(k["camp_id"], k["texto"], k["match"]) for k in keywords] == [
        tuple(pausa) for pausa in rc.PAUSAS_DEDUP
    ]
    assert len(conn.queries) == len(rc.CAMPANAS_REACTIVAR) + len(rc.PAUSAS_DEDUP), (
        "mismas queries que master: una por campana y una por keyword"
    )


# ---------------------------------------------------------------------------
# 4-5. main --solo-campana: dry-run declara, guard de ya-ENABLED no muta
# ---------------------------------------------------------------------------


def test_main_dry_run_solo_campana_imprime_plan_resuelto(monkeypatch, capsys):
    _fakea_main(monkeypatch, _conn_solo_campana("PAUSED"), estado_vivo="PAUSED")
    monkeypatch.setattr(sys, "argv", ["reactiva_campanas.py", "--solo-campana", str(SOLO_CAMPANA)])

    assert rc.main() == 0

    eventos = _eventos(capsys)
    resueltas = [e for e in eventos if e["evento"] == "campana_resuelta"]
    assert len(resueltas) == 1
    assert resueltas[0]["id"] == SOLO_CAMPANA
    assert resueltas[0]["external_id"] == SOLO_EXTERNAL
    vivos = [e for e in eventos if e["evento"] == "estado_vivo"]
    assert len(vivos) == 1 and vivos[0]["estado"] == "PAUSED"
    secos = [e for e in eventos if e["evento"] == "dry_run"]
    assert len(secos) == 1
    assert secos[0]["pausas"] == 0 and secos[0]["resumes"] == 1
    # Sin intento de PUT/token: el _HttpFalso REVIENTA si algo lo llama, y el
    # main llego a return 0 — la demostracion es haber llegado hasta aca.


def test_main_solo_campana_ya_enabled_no_muta(monkeypatch, capsys):
    _fakea_main(monkeypatch, _conn_solo_campana("PAUSED"), estado_vivo="ENABLED")
    monkeypatch.setattr(sys, "argv", ["reactiva_campanas.py", "--solo-campana", str(SOLO_CAMPANA)])

    with pytest.raises(rc.Abortar, match="no se muta"):
        rc.main()

    eventos = _eventos(capsys)
    assert [e for e in eventos if e["evento"] == "dry_run"] == [], (
        "el guard vive ANTES del dry-run: una campana ya ENABLED aborta tambien en seco"
    )
    assert any(e["evento"] == "estado_vivo" and e["estado"] == "ENABLED" for e in eventos), (
        "se declara el estado vivo que disparo el abort"
    )
    # Sin PUT: el _HttpFalso reventaria; el Abortar salio del guard, no de red.


# ---------------------------------------------------------------------------
# 6-8. Review r2: fase 2 completa en solo-campana, orden de resumes, name NULL
# ---------------------------------------------------------------------------


def test_main_solo_campana_resume_completa_la_fase_2(monkeypatch, capsys):
    """Regresion del review (ALTA): con --solo-campana 3919 la fase 2 reventia
    con KeyError 3909 (orden = [3909] + ... asume que 3909 esta en el plan)
    despues del token y ANTES de cualquier PUT. Aqui la mutacion COMPLETA: un
    solo PUT con el shape sellado, readback ENABLED, reconciliacion 0/1."""
    http = _HttpQueResume()
    _fakea_main(
        monkeypatch,
        _conn_solo_campana("PAUSED"),
        secuencia=["PAUSED", "ENABLED"],  # guard -> PAUSED; readback post-PUT -> ENABLED
        http=http,
    )
    monkeypatch.setattr(rc, "_token_lwa", lambda cred, client: "token-falso")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reactiva_campanas.py",
            "--solo-campana",
            str(SOLO_CAMPANA),
            "--esperado-external",
            SOLO_EXTERNAL,
            "--acepto-mutacion-real",
        ],
    )

    assert rc.main() == 0

    assert len(http.puts) == 1, "UN solo PUT: el resume de la 3919 (cero pausas)"
    put = http.puts[0]
    assert put["url"].endswith("/sp/campaigns")
    assert put["payload"] == {"campaigns": [{"campaignId": SOLO_EXTERNAL, "state": "ENABLED"}]}, (
        "id como STRING y enum UPPER: el shape sellado de PR #37"
    )
    vendor = "application/vnd.spcampaign.v3+json"
    assert put["headers"]["Content-Type"] == vendor and put["headers"]["Accept"] == vendor, (
        "vendor v3 EXACTO en Content-Type Y Accept"
    )
    eventos = _eventos(capsys)
    resumes = [e for e in eventos if e["evento"] == "resume"]
    assert len(resumes) == 1
    assert resumes[0]["camp_id"] == SOLO_CAMPANA
    assert resumes[0]["readback"] == "ENABLED" and resumes[0]["ok"] is True
    fin = [e for e in eventos if e["evento"] == "reconciliacion_final"]
    assert len(fin) == 1
    assert fin[0]["pausas_ok"] == 0 and fin[0]["resumes_ok"] == 1


def test_orden_resumes_solo_campana_es_esa_campana():
    assert rc._orden_resumes({SOLO_CAMPANA: {}}) == [SOLO_CAMPANA]


def test_orden_resumes_camino_original_3909_encabeza():
    campanas = {cid: {} for cid in rc.CAMPANAS_REACTIVAR}
    esperado = [3909] + [c for c in campanas if c != 3909]
    orden = rc._orden_resumes(campanas)
    assert orden == esperado, "el camino original conserva el orden sellado de master"
    assert orden[0] == 3909, "A1U Exact 3909 sigue PRIMERA (shape nuevo de PR #37)"


def test_nombre_null_cae_al_external_id_sin_inventar(capsys):
    conn = _conn_solo_campana(nombre=None)  # e.name NULL
    campanas, _ = rc._resolver_plan(conn, solo_campana=SOLO_CAMPANA)
    assert campanas[SOLO_CAMPANA]["nombre"] == SOLO_EXTERNAL, (
        "regla 3: sin nombre en la base, el declarado es el external_id"
    )


# ---------------------------------------------------------------------------
# 9-12. Cross-review grok (media): la mutacion de una sola campana exige
# --esperado-external (anti-typo: el plan resuelto DEBE coincidir con el
# external que autorizo el dueno en el dry-run).
# ---------------------------------------------------------------------------


def test_main_solo_campana_mutacion_sin_esperado_external_aborta(monkeypatch, capsys):
    """Con --acepto-mutacion-real + --solo-campana y SIN --esperado-external
    el tool NO puede mutar: el flag es obligatorio para atar la mutacion al
    external que el dueno autorizo (cross-review grok, hallazgo media: un
    typo en el id interno reactivaria otra campana y gastaria plata real)."""
    http = _HttpQueResume()
    _fakea_main(
        monkeypatch,
        _conn_solo_campana("PAUSED"),
        secuencia=["PAUSED", "ENABLED"],
        http=http,
    )
    monkeypatch.setattr(rc, "_token_lwa", lambda cred, client: "token-falso")
    monkeypatch.setattr(
        sys,
        "argv",
        ["reactiva_campanas.py", "--solo-campana", str(SOLO_CAMPANA), "--acepto-mutacion-real"],
    )

    with pytest.raises(rc.Abortar, match="esperado-external"):
        rc.main()
    assert http.puts == [], "sin el external esperado no hay NI UN PUT"


def test_main_esperado_external_desacuerdo_aborta_fail_closed(monkeypatch, capsys):
    """--esperado-external que NO coincide con lo resuelto por la base aborta
    ANTES de tocar Amazon (tambien en dry-run): fail-cleared contra el typo
    en cualquiera de los dos extremos (id interno o external)."""
    http = _HttpQueResume()
    _fakea_main(monkeypatch, _conn_solo_campana("PAUSED"), http=http)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reactiva_campanas.py",
            "--solo-campana",
            str(SOLO_CAMPANA),
            "--esperado-external",
            "999999999999999",
        ],
    )

    with pytest.raises(rc.Abortar, match="251723662158466"):
        rc.main()
    assert http.puts == []
    eventos = _eventos(capsys)
    assert [e for e in eventos if e["evento"] == "dry_run"] == [], (
        "el desacuerdo aborta antes del dry-run: ni se consulta el estado vivo"
    )


def test_main_esperado_external_sin_solo_campana_aborta(monkeypatch):
    """El flag solo tiene sentido en modo --solo-campana; en el camino
    original (5 campanas) es un error de uso."""
    _fakea_main(monkeypatch, _conn_camino_original())
    monkeypatch.setattr(
        sys,
        "argv",
        ["reactiva_campanas.py", "--esperado-external", SOLO_EXTERNAL],
    )

    with pytest.raises(rc.Abortar, match="solo-campana"):
        rc.main()
