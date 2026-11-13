"""Reproducciones de la revision del apply (SOLO LECTURA del codigo).

Cada prueba FALLA contra master a4644aad por la asercion que describe la
falla; pasan el dia que la falla se corrija. Reusan los bancos de las
pruebas existentes (test_apply, test_apply_cola, test_apply_harvest).
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_apply as ta  # noqa: E402
import test_apply_cola as tc  # noqa: E402
import test_apply_harvest as th  # noqa: E402

from app.apply import bids_del_ciclo, reversa_bid  # noqa: E402
from app.apply_cola import claves_bloqueadas  # noqa: E402


# ---------------------------------------------------------------------------
# F1. El bid del motor pisa el cambio manual del dueno (cache de las 06:45)
# ---------------------------------------------------------------------------


@ta._skip_db
def test_bid_no_pisa_un_cambio_manual_hecho_despues_del_sync():
    """La decision se calculo con el bid del cache (1.00, sync de las 06:45).
    El dueno subio la keyword a 1.50 a mano en Amazon antes del ciclo. El
    aplicador manda el PUT 0.85 sin mirar el bid vivo: el cambio del dueno
    se pierde y Orbit lo da por bueno (verify_ok)."""
    with ta._db_temporal("rev_apply_f1") as conn:
        ids = ta._semilla(conn)
        ta._decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], old="1.00", new="0.85")
        handler, vistos = ta._handler_api({"7201": 1.50})  # lo que Amazon tiene HOY
        ap = ta._aplicador(conn, handler, ids["ciclo_ejec"])

        ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert ta._puts(vistos) == [], (
            "con el bid vivo (1.50) distinto del old_value decidido (1.00) no debe"
            f" salir el PUT; salieron {[ta._obj_de_put(p) for p in ta._puts(vistos)]}"
        )


@ta._skip_db
def test_reversa_de_bid_regresa_al_bid_que_habia_en_amazon():
    """Mismo escenario: la reversa (y el regreso del dueno, que la reusa)
    escribe decision.old_value = 1.00, un bid que la keyword no tenia (el
    dueno la tenia en 1.50)."""
    with ta._db_temporal("rev_apply_f1b") as conn:
        ids = ta._semilla(conn)
        ta._decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], old="1.00", new="0.85")
        handler, vistos = ta._handler_api({"7201": 1.50})
        ap = ta._aplicador(conn, handler, ids["ciclo_ejec"])
        ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        reversa_bid(conn, ta._write_client(handler), bids_del_ciclo(conn, ids["ciclo_dec"])[0])

        bids = [ta._obj_de_put(p)["bid"] for p in ta._puts(vistos)]
        assert bids[-1] == 1.50, f"la reversa deberia volver a 1.50; escribio {bids[-1]}"


# ---------------------------------------------------------------------------
# F2. Una practica de veto (fila shadow) vencida congela la hoja para siempre
# ---------------------------------------------------------------------------


@tc._skip_db
def test_fila_shadow_vencida_no_bloquea_la_clave_para_siempre():
    """Fila shadow de una pausa nacida en un ciclo o goal en sombra: jamas
    se libera ni se descarta sola. Treinta dias despues de vencer su
    ventana sigue en claves_bloqueadas, asi que el ciclo salta la hoja
    ENTERA (bids incluidos, cycle.py:1914) aunque el goal ya sea live."""
    with tc._db_temporal("rev_cola_f2") as conn:
        ids = tc._semilla(conn)
        dec = tc._decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        ahora = ids["ahora"]
        tc._encola_fila(
            conn,
            dec,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(days=30),
            encolado=ahora - dt.timedelta(days=32),
            payload=tc._payload_pause("7201"),
        )

        bloqueadas = claves_bloqueadas(conn, "amazon_us", ahora)

        assert (ids["kw"], "entity_cut", None) not in bloqueadas, (
            "una practica de veto vencida hace 30 dias sigue congelando la hoja"
        )


# ---------------------------------------------------------------------------
# F3. Reconciliacion de un negative huerfano con LIST truncado: re-POST
# ---------------------------------------------------------------------------


@th._skip_db
def test_negative_huerfano_no_se_repostea_si_la_lista_no_se_leyo_completa():
    """El POST del negative salio y el proceso murio antes del sello (fila
    applying + ledger abierto). El negative SI existe en Amazon, pero cae
    despues de la pagina 20 del LIST de toda la cuenta. `_lista_todos` corta
    en 20 paginas SIN avisar y la reconciliacion lo da por ausente: vuelve a
    postear (segundo negative, o rechazo por duplicado sellado como fallo)."""
    with th._db_temporal("rev_har_f3") as conn:
        ids = th._semilla(conn)
        dec = th._decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = th._encola_fila(conn, dec, ids["ag"], kind="negative", term=th.TERMINO)
        th._libera_fila(conn, q)
        th._claim_fila(conn, q)
        th._fila_ledger_abierta(conn, dec)
        relleno = [
            {
                "adGroupId": f"5{i:03d}",
                "campaignId": "5000",
                "keywordId": f"n-r{i}",
                "keywordText": f"otro termino {i}",
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
            for i in range(20)
        ]
        propio = {
            "adGroupId": th.ORIGEN_GRUPO,
            "campaignId": th.ORIGEN_CAMPANA,
            "keywordId": "n-propio",
            "keywordText": th.TERMINO,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        handler, vistos = th._handler_harvest(negatives=[*relleno, propio], page_size=1)

        th._reconcilia(conn, handler, ids["ciclo_ejec"])

        posts = [r for r in th._mutaciones(vistos) if r.url.path == "/sp/negativeKeywords"]
        assert posts == [], (
            "el negative ya existia (pagina 21): una lectura incompleta no autoriza"
            f" otro POST; salieron {len(posts)}"
        )


# ---------------------------------------------------------------------------
# F4. Harvest: la keyword recien creada se borra si el LIST no la trae
# ---------------------------------------------------------------------------


@th._skip_db
def test_harvest_no_revierte_una_keyword_creada_si_el_list_es_incompleto():
    """POST de la keyword con ack e id (Amazon la creo). El readback de la
    misma corrida lee /sp/keywords/list de TODA la cuenta con tope de 20
    paginas; si la keyword cae despues, `_paso_readback` la da por ausente y
    la reversa automatica BORRA la keyword y el negative recien creados."""
    with th._db_temporal("rev_har_f4") as conn:
        ids = th._semilla(conn)
        dec = th._decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = th._encola_fila(conn, dec, ids["ag"], term=th.TERMINO)
        relleno = [
            {
                "adGroupId": f"6{i:03d}",
                "campaignId": "6000",
                "keywordId": f"k-r{i}",
                "keywordText": f"otra keyword {i}",
                "matchType": "EXACT",
                "state": "ENABLED",
                "bid": 0.5,
            }
            for i in range(20)
        ]
        handler, vistos = th._handler_harvest(keywords=relleno, page_size=1)
        from app.apply_cola import fila_cola
        from app.apply_harvest import aplica_harvest

        th._libera_fila(conn, q)
        res = aplica_harvest(
            conn,
            th._aplicador(conn, handler, ids["ciclo_ejec"]),
            fila_cola(conn, q),
            platform="amazon_us",
        )

        borrados = [r.url.path for r in th._mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert borrados == [], (
            f"la keyword SI se creo (ack con id); se revirtio igual: {borrados}; estado {res.estado}"
        )



# ---------------------------------------------------------------------------
# F5. Un negative confirmado por la reconciliacion no tiene reversa ejecutable
# ---------------------------------------------------------------------------


@th._skip_db
def test_negative_confirmado_por_reconciliacion_se_puede_revertir(monkeypatch):
    """Crash entre el POST del negative y su sello: la reconciliacion lo
    encuentra en Amazon y lo confirma (cola applied, ledger 'ok:reconciliado').
    La reversa manual busca el id SOLO en un intento normal con resultado
    'ok' exacto (apply._SQL_ACK_OK): no lo halla y responde 422
    NegativeIdNoResoluble. Regla 7: ese corte vivo queda sin reversa."""
    from app import apply

    with th._db_temporal("rev_har_f5") as conn:
        ids = th._semilla(conn)
        dec = th._decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = th._encola_fila(conn, dec, ids["ag"], kind="negative", term=th.TERMINO)
        th._libera_fila(conn, q)
        th._claim_fila(conn, q)
        th._fila_ledger_abierta(conn, dec)
        handler, vistos = th._handler_harvest(
            negatives=[
                {
                    "adGroupId": th.ORIGEN_GRUPO,
                    "campaignId": th.ORIGEN_CAMPANA,
                    "keywordId": "n-3",
                    "keywordText": th.TERMINO,
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                }
            ]
        )
        th._reconcilia(conn, handler, ids["ciclo_ejec"])
        assert conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (q,)).fetchone()[0] == (
            "applied"
        )
        ap = th._aplicador(conn, handler, ids["ciclo_ejec"])
        monkeypatch.setattr(apply, "_cliente_reversa", lambda platform, transport: ap._cliente())

        try:
            res = apply.reversa_manual(conn, tipo="negative", queue_id=q)
        except apply.NegativeIdNoResoluble as exc:
            raise AssertionError(f"el negative n-3 esta vivo y aplicado pero sin reversa: {exc}")
        assert res["confirmada"] is True
