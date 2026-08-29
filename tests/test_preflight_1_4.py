"""Tests del preflight 1.4 (plans/orbit-05-preflight.md, sellado 4): quota
visible + alerta fail-silent de cap agotado, ANTES del primer cobro real.

DB temporal con los patrones existentes (test_apply / test_cycle_apply /
test_api_dashboard; corre contra el Postgres real del tunel con
ORBIT_TEST_DSN, skip fail-closed si no) + canal Telegram 100% mock (fixtures
locales que envuelven el helper ``_canal`` de test_notifica; el autouse del
conftest aisla el canal: cero HTTP real).

DoD de la tarea, un candado por test (regla 9 en cada uno):

1. KINDS_QUOTA: la lista en app ESPEJA del CASE del trigger
   apply_cap_de_config (0002) — una sola lista, el endpoint la consume.
2. estado_quota: las TRES fuentes (fila_del_dia con cap INMUTABLE aunque la
   config cambie / config_vigente / sin_clave fail-closed explicito).
3. consume_quota_y_sello: la transicion used == cap dispara UNA vez; el
   rechazo por tope NO es evento; el wrapper consume_quota NO cambia.
4. /salud expone "quota" por plataforma y forma, coherente con la fila
   sembrada (rojo sin el campo).
5. Plumbing: el camino de bids recolecta UN CapSaturado; el consumo
   rechazado NO agrega; _fase_notifica manda UN aviso con latido.
6. Canal caido: NOTA notes['telegram']['cap_agotado'] y el ciclo live
   termina 'done' (el canal jamas degrada).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from psycopg.types.json import Json
from test_api_dashboard import _cliente
from test_api_dashboard import _db_temporal as _db_dash
from test_apply import (
    _aplicador as _aplicador_bids,
)
from test_apply import (
    _db_temporal as _db_apply,
)
from test_apply import (
    _decision_bid,
    _handler_api,
)
from test_apply import (
    _semilla as _semilla_bids,
)
from test_cycle import _config_version, _siembra_maestra
from test_cycle_apply import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_REFRESH_TOKEN,
    _fabrica_real_mock,
    _handler,
)
from test_cycle_apply import (
    _corre as _corre_ciclo,
)
from test_cycle_apply import (
    _db_temporal as _db_ciclo,
)
from test_notifica import _canal
from test_schema import _postgres_obligatorio_ausente

from app import cycle as ciclo
from app import notifica
from app.ads.config import AdsCredentials
from app.apply import (
    KINDS_QUOTA,
    CapSaturado,
    bids_del_ciclo,
    consume_quota,
    consume_quota_y_sello,
    estado_quota,
)
from app.optimizer.bid import PLATAFORMAS_MONEDA

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# Fixtures LOCALES que envuelven los helpers de test_notifica/test_cycle_apply:
# importar la FIXTURE entre modulos de tests no es fiable (pytest no siempre
# la registra en el modulo que la importa); los HELPERS planos si viajan.


@pytest.fixture
def canal_ok(tmp_path, monkeypatch):
    """Canal Telegram configurado y OK; captura los mensajes (patron 3.3)."""
    with _canal(tmp_path, monkeypatch) as mensajes:
        yield mensajes


@pytest.fixture
def canal_fail(tmp_path, monkeypatch):
    """Canal Telegram configurado pero el envio responde 500 (canal caido)."""
    with _canal(tmp_path, monkeypatch, status=500) as mensajes:
        yield mensajes


@pytest.fixture
def secrets_falsos(monkeypatch):
    """from_secrets_dir devuelve credenciales FALSAS (patron test_cycle_apply):
    la fabrica REAL del ciclo corre sin tocar el secrets dir."""
    monkeypatch.setattr(
        AdsCredentials,
        "from_secrets_dir",
        classmethod(
            lambda cls, _dir=None: AdsCredentials(
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
                refresh_token=FAKE_REFRESH_TOKEN,
            )
        ),
    )


def _config(conn, settings: dict) -> int:
    """Config VIGENTE nueva (gana por id DESC, patron de test_cycle)."""
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('pf-14', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. KINDS_QUOTA: espejo del CASE del trigger apply_cap_de_config (0002)
# ---------------------------------------------------------------------------


def test_kinds_quota_es_el_espejo_del_trigger():
    """La UNICA lista de kinds de quota en app: el endpoint de /salud la
    recorre y consume_quota/motor_quota la respetan. kind nuevo = decision
    del dueno (sellado 8); desalinearla del trigger de 0002 rompe el mapeo."""
    assert KINDS_QUOTA == ("bid", "pause", "negative", "harvest")


def test_kinds_quota_es_el_espejo_real_del_trigger():
    """ADV-4 (adversary): el espejo se prueba contra el FUENTE del trigger
    (el CASE de apply_cap_de_config en migrations/0002_apply.sql), no contra
    una tupla escrita a mano: un kind nuevo en el trigger sin KINDS_QUOTA
    deja este test rojo (y viceversa)."""
    fuente = (Path(__file__).resolve().parent.parent / "migrations" / "0002_apply.sql").read_text(
        encoding="utf-8"
    )
    bloque = fuente.split("CREATE FUNCTION apply_cap_de_config", 1)[1].split("$$;", 1)[0]
    motores = set(re.findall(r"WHEN 'ads_optimizer:([a-z_]+):([a-z_]+)'", bloque))
    assert motores, "el CASE del trigger no se encontro: revisar el parseo"
    assert {p for p, _k in motores} == set(PLATAFORMAS_MONEDA), (
        "las MISMAS plataformas que resuelve el motor"
    )
    assert {k for _p, k in motores} == set(KINDS_QUOTA), (
        "los MISMOS kinds: espejo real del trigger, no redeclorado"
    )


# ---------------------------------------------------------------------------
# 2. estado_quota: fila_del_dia (cap INMUTABLE) / config_vigente / sin_clave
# ---------------------------------------------------------------------------


@_skip_db
def test_estado_quota_tres_fuentes_y_cap_inmutable_de_la_fila():
    """DoD 1.4 (codex plan r1): tras cambiar la config, la fila del dia
    conserva SU cap (el que realmente rige hoy) y la fuente lo declara —
    leer la config vigente haria mentir al dashboard."""
    with _db_apply("orbit_pf14_eq") as conn:
        _config(conn, {"ads_apply_cap_amazon_us_bid": 10, "ads_apply_cap_amazon_us_pause": 3})

        # Sin fila del dia: cap de la config VIGENTE, used 0.
        assert estado_quota(conn, "amazon_us", "bid") == {
            "used": 0,
            "cap": 10,
            "fuente": "config_vigente",
        }

        # Nace la fila del dia CONSUMIENDO (el trigger la valida contra la
        # config vigente): used 1, cap 10.
        assert consume_quota(conn, "amazon_us", "bid") is True

        # Config NUEVA append-only con cap menor: la fila del dia NO se toca.
        _config(conn, {"ads_apply_cap_amazon_us_bid": 5})

        assert estado_quota(conn, "amazon_us", "bid") == {
            "used": 1,
            "cap": 10,
            "fuente": "fila_del_dia",
        }, "el cap de la fila es INMUTABLE: rige hoy aunque la config cambie"

        # Sin clave en la config vigente: fail-closed EXPLICITO, no 0/0.
        assert estado_quota(conn, "amazon_us", "harvest") == {
            "used": 0,
            "cap": None,
            "fuente": "sin_clave",
        }


# ---------------------------------------------------------------------------
# 3. consume_quota_y_sello: la transicion used == cap es el evento
# ---------------------------------------------------------------------------


@_skip_db
def test_consume_quota_y_sello_transicion_de_saturacion():
    """DoD (D3a): el sello dispara EXACTAMENTE en el consumo que lleva used a
    cap; los intentos posteriores rechazados NO son evento nuevo. El wrapper
    consume_quota mantiene su contrato bool (tests viejos intactos)."""
    with _db_apply("orbit_pf14_sello") as conn:
        _config(conn, {"ads_apply_cap_amazon_us_bid": 2})

        assert consume_quota_y_sello(conn, "amazon_us", "bid") == (True, False)
        assert consume_quota_y_sello(conn, "amazon_us", "bid") == (True, True)
        assert consume_quota_y_sello(conn, "amazon_us", "bid") == (False, False), (
            "el rechazo por tope NO es evento de saturacion"
        )
        # Wrapper: mismo camino, contrato bool intacto (True ya cobrada hoy
        # seria falso — consume_quota debe seguir devolviendo False al tope).
        assert consume_quota(conn, "amazon_us", "bid") is False
        assert consume_quota(conn, "amazon_us", "negative") is False, "sin clave: False"


@_skip_db
def test_consume_quota_y_sello_satura_contra_el_cap_de_la_fila():
    """Regresion (revision del lead 1.4): el sello compara contra el cap de la
    PROPIA fila (el que rige hoy, inmutable por el trigger de 0002), no contra
    el cap de la config del cobro: con la fila nacida cap 10 y la config
    cambiada a 5 a mitad de dia, used=5 NO es saturacion (la fila sigue
    rigiendo 10) y used=10 SI."""
    with _db_apply("orbit_pf14_sello_fila") as conn:
        _config(conn, {"ads_apply_cap_amazon_us_bid": 10})
        assert consume_quota(conn, "amazon_us", "bid") is True  # nace la fila (cap 10)
        _config(conn, {"ads_apply_cap_amazon_us_bid": 5})
        for _ in range(4):
            assert consume_quota_y_sello(conn, "amazon_us", "bid") == (True, False), (
                "used 2..5 con fila cap 10: la config nueva NO rige la fila ya nacida"
            )
        for _ in range(4):
            assert consume_quota_y_sello(conn, "amazon_us", "bid") == (True, False)
        assert consume_quota_y_sello(conn, "amazon_us", "bid") == (True, True), (
            "used=10 == cap de la fila: ahi y solo ahi hay transicion"
        )
        assert consume_quota_y_sello(conn, "amazon_us", "bid") == (False, False), (
            "tope agotado: sin evento nuevo"
        )


# ---------------------------------------------------------------------------
# 4. /salud expone quota por plataforma y forma
# ---------------------------------------------------------------------------


@_skip_db
def test_salud_expone_quota_coherente_con_la_fila_del_dia(monkeypatch):
    """DoD (rojo sin el campo): cada plataforma gana clave "quota" con
    {kind: {used, cap, fuente}} para TODOS los KINDS_QUOTA, coherente con la
    fila sembrada de apply_quota_state (cap INMUTABLE incluido)."""
    with _db_dash("orbit_pf14_salud") as (conn, dsn):
        _config(conn, {"ads_apply_cap_amazon_us_bid": 10, "ads_apply_cap_amazon_us_pause": 2})
        consume_quota(conn, "amazon_us", "bid")  # nace la fila del dia (cap 10, used 1)
        # Config nueva: la fila del dia conserva su cap 10.
        _config(conn, {"ads_apply_cap_amazon_us_bid": 5, "ads_apply_cap_amazon_us_pause": 2})

        plataformas = _cliente(dsn, monkeypatch).get("/api/dashboard/salud").json()["plataformas"]

        quota_us = plataformas["amazon_us"]["quota"]
        assert set(quota_us) == set(KINDS_QUOTA), "las cuatro formas de la rampa"
        assert quota_us["bid"] == {"used": 1, "cap": 10, "fuente": "fila_del_dia"}
        assert quota_us["pause"] == {"used": 0, "cap": 2, "fuente": "config_vigente"}
        assert quota_us["harvest"] == {"used": 0, "cap": None, "fuente": "sin_clave"}

        quota_mx = plataformas["amazon_mx"]["quota"]
        assert set(quota_mx) == set(KINDS_QUOTA)
        assert quota_mx["bid"] == {"used": 0, "cap": None, "fuente": "sin_clave"}


@_skip_db
def test_salud_con_cap_corrupto_en_config_no_tumba_la_pantalla(monkeypatch):
    """ADV-1 (adversary): un cap NO numerico en la config vigente (p. ej.
    "10x") revienta estado_quota — deliberado en el camino de COBRO (ruidoso,
    jamas disfraz de cap infinito) — pero la pantalla de LECTURA no puede
    morir entera por una forma rota: /salud responde 200 y la forma rota
    queda VISIBLE con fuente="config_rota" (no "sin_clave": la clave SI
    existe, mentirla seria regla 3). Las formas sanas siguen legibles."""
    with _db_dash("orbit_pf14_rota") as (conn, dsn):
        _config(conn, {"ads_apply_cap_amazon_us_bid": "10x"})
        cliente = _cliente(dsn, monkeypatch)
        resp = cliente.get("/api/dashboard/salud")
        assert resp.status_code == 200, "una config rota no tumba la pantalla"
        quota_us = resp.json()["plataformas"]["amazon_us"]["quota"]
        assert quota_us["bid"] == {"used": 0, "cap": None, "fuente": "config_rota"}
        assert quota_us["pause"] == {"used": 0, "cap": None, "fuente": "sin_clave"}, (
            "las formas sanas de la MISMA plataforma siguen legibles"
        )


# ---------------------------------------------------------------------------
# 5. Plumbing: el camino de bids recolecta el evento de saturacion
# ---------------------------------------------------------------------------


@_skip_db
def test_bids_saturan_cap_un_evento_y_rechazo_no_agrega():
    """cap 1 con dos elegibles: el apply que lleva used a cap produce UN
    CapSaturado con la fila real (used/cap); el segundo bid rechazado por
    tope NO agrega evento (D3a: el rechazo no es transicion)."""
    with _db_apply("orbit_pf14_bids") as conn:
        ids = _semilla_bids(conn, caps={"ads_apply_cap_amazon_us_bid": 1})
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], cost="120.50")
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw2"], cost="80.00")
        handler, _vistos = _handler_api({"7201": "0.85", "7202": "0.85"})
        ap = _aplicador_bids(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 1
        assert res.descartadas == ["fuera_de_cap"], "el segundo queda fuera de cap"
        assert res.caps_saturados == (
            CapSaturado(platform="amazon_us", kind="bid", used=1, cap=1),
        ), "UN evento con los numeros de la propia fila de quota"


# ---------------------------------------------------------------------------
# 6. notifica: builder + sender fail-silent del aviso de cap agotado
# ---------------------------------------------------------------------------


def test_aviso_cap_agotado_builder_texto():
    """Texto plano estilo "[Orbit] ALERTA", con plataforma/kind/used/cap. La
    fecha NO se inventa: ni datetime.now() del cliente ni parametro — el dia
    es la quota_date de la fila (visible en /salud), declarado en docs."""
    texto = notifica.aviso_cap_agotado("amazon_us", "bid", 10, 10)
    assert texto.startswith("[Orbit] ALERTA cap agotado")
    assert "plataforma: amazon_us" in texto
    assert "kind: bid" in texto
    assert "used: 10" in texto
    assert "cap: 10" in texto


def test_notifica_cap_agotado_canal_deshabilitado_no_es_fallo():
    """Mismo contrato de los otros senders: canal deshabilitado -> True y NO
    es fallo (el autouse del conftest deja el canal apagado)."""
    assert notifica.notifica_cap_agotado("amazon_us", "bid", 10, 10) is True


def test_notifica_cap_agotado_envio_explota_false_sin_subir(tmp_path, monkeypatch):
    """Cualquier excepcion del envio -> False + warning, JAMAS levanta
    (fail-silent; el caller decide la NOTA). Canal CONFIGURADO (telegram.json
    falso, patron de test_notifica) para que el envio se intente de verdad."""
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "telegram.json").write_text(
        json.dumps({"bot_token": "7700000001:AAF-fake", "chat_id": "555001"}), encoding="utf-8"
    )
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
    notifica._reset()
    monkeypatch.setattr(
        notifica,
        "_envia_texto",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom canal")),
    )
    try:
        assert notifica.notifica_cap_agotado("amazon_us", "bid", 10, 10) is False
    finally:
        notifica._reset()


# ---------------------------------------------------------------------------
# 7. _fase_notifica: UN aviso por evento, latido, y NOTA si el canal cae
# ---------------------------------------------------------------------------


def test_fase_notifica_un_aviso_por_cap_y_latido(canal_ok):
    """UN notifica_cap_agotado por evento (con tick/latido por mensaje, mismo
    patron de los avisos de encola); envio ok -> sin NOTA."""
    caps = (
        CapSaturado(platform="amazon_us", kind="bid", used=10, cap=10),
        CapSaturado(platform="amazon_us", kind="harvest", used=2, cap=2),
    )
    latidos: list[int] = []
    notas = ciclo._fase_notifica(
        (),
        (),
        cycle_id=1,
        platform="amazon_us",
        modo="live",
        status="done",
        decisions_count=0,
        notas_apply={},
        caps_saturados=caps,
        tick=lambda: latidos.append(1),
    )
    assert notas == {}, "envio ok: sin NOTA"
    avisos = [m["text"] for m in canal_ok if m["text"].startswith("[Orbit] ALERTA cap agotado")]
    assert len(avisos) == 2, "UN aviso por evento (bid + harvest)"
    assert any("kind: bid" in t for t in avisos) and any("kind: harvest" in t for t in avisos)
    assert len(latidos) == len(canal_ok), "un latido por mensaje (avisos + digest)"


def test_fase_notifica_cap_agotado_canal_caido_deja_nota(canal_fail):
    """Envio fallido -> la NOTA con el detalle del cap (plataforma/kind y
    used/cap); JAMAS rompe la fase (mismo try/except de los demas avisos)."""
    caps = (CapSaturado(platform="amazon_mx", kind="pause", used=2, cap=2),)
    notas = ciclo._fase_notifica(
        (),
        (),
        cycle_id=1,
        platform="amazon_mx",
        modo="live",
        status="done",
        decisions_count=0,
        notas_apply={},
        caps_saturados=caps,
    )
    assert notas["cap_agotado"] == (
        "fallo: aviso de cap agotado no enviado por Telegram (cap amazon_mx/pause agotado: 2/2)"
    )


def test_fase_notifica_dos_caps_agotados_canal_caido_la_nota_acumula(canal_fail):
    """Hallazgo verifier: varios eventos fallidos en el MISMO ciclo (p. ej. el
    bid y el harvest del mismo motor) — la NOTA ACUMULA los detalles, no pisa
    (regla 3: la evidencia de cada aviso perdido queda en notes)."""
    caps = (
        CapSaturado(platform="amazon_us", kind="bid", used=10, cap=10),
        CapSaturado(platform="amazon_us", kind="harvest", used=2, cap=2),
    )
    notas = ciclo._fase_notifica(
        (),
        (),
        cycle_id=1,
        platform="amazon_us",
        modo="live",
        status="done",
        decisions_count=0,
        notas_apply={},
        caps_saturados=caps,
    )
    assert "cap amazon_us/bid agotado: 10/10" in notas["cap_agotado"]
    assert "cap amazon_us/harvest agotado: 2/2" in notas["cap_agotado"]


def test_fase_notifica_cap_agotado_excepcion_no_pisa_la_nota_acumulada(monkeypatch):
    """ADV-3 (adversary): el except del bloque de avisos TAMBIEN acumula, no
    pisa — el detalle del primer cap perdido queda junto al fallo del
    segundo (regla 3: la evidencia de cada aviso perdido queda en notes)."""
    caps = (
        CapSaturado(platform="amazon_us", kind="bid", used=10, cap=10),
        CapSaturado(platform="amazon_us", kind="harvest", used=2, cap=2),
    )
    llamadas = {"n": 0}

    def _canal_oscilante(*_a, **_k):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return False  # primer aviso: el canal reporta fallo (False)
        raise RuntimeError("boom en el segundo envio")

    monkeypatch.setattr(notifica, "notifica_cap_agotado", _canal_oscilante)
    notas = ciclo._fase_notifica(
        (),
        (),
        cycle_id=1,
        platform="amazon_us",
        modo="live",
        status="done",
        decisions_count=0,
        notas_apply={},
        caps_saturados=caps,
    )
    assert "cap amazon_us/bid agotado: 10/10" in notas["cap_agotado"], (
        "el detalle del primer aviso perdido NO se pisa"
    )
    assert "no salio" in notas["cap_agotado"], "la excepcion del segundo TAMBIEN queda"


# ---------------------------------------------------------------------------
# 8. E2E live: cap agotado + canal caido -> ciclo 'done' con la NOTA
# ---------------------------------------------------------------------------


@_skip_db
def test_ciclo_live_cap_agotado_canal_caido_termina_done_con_nota(canal_fail, secrets_falsos):
    """El ciclo LIVE que agota el cap de bids (1 decision, cap 1: la unica
    aplicacion ES la transicion) con el canal CAIDO: termina 'done' (un fallo
    de Telegram JAMAS degrada), la NOTA cap_agotado queda persistida en
    notes['telegram'] con el detalle, y el digest no pisa la clave."""
    with _db_ciclo("orbit_pf14_live") as (conn, _c):
        _siembra_maestra(conn, escalera="live")
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                "ads_apply_cap_amazon_us_bid": 1,
                "ads_apply_cap_amazon_us_pause": 2,
                "ads_apply_cap_amazon_us_negative": 5,
                "ads_apply_cap_amazon_us_harvest": 2,
            },
        )
        handler, _vistos = _handler({"9201": "0.75"})
        res = _corre_ciclo(conn, factory=_fabrica_real_mock(handler))

        assert res.status == "done", "el fallo del canal no degrada el ciclo"
        notas = json.loads(res.notes)
        assert notas["apply"]["bids_aplicados"] == 1, "la unica unidad satura el cap"
        assert notas["telegram"]["cap_agotado"] == (
            "fallo: aviso de cap agotado no enviado por Telegram (cap amazon_us/bid agotado: 1/1)"
        )
        assert notas["telegram"]["digest"].startswith("fallo:"), (
            "las claves de la NOTA conviven (una por tipo de aviso)"
        )
