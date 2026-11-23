"""TEMPORADA 01, bloque B: el episodio de temporada y el origen de los cambios.

Tres pruebas nombradas por la fila B.2 del plan + la migracion:

- `test_temporada01_b_pendiente` (dinero): entra una vez; cambiar mayo a
  normal con junio en baja no recorta 5.60 -> 3.92; 30 -> 20 da 6.40; mano de
  David no regresa; el regreso nunca baja.
- `test_temporada01_b_episodio_reparado_tras_caida` (escribe en produccion,
  base de pruebas): entrada confirmada y efecto abierto como tras una caida;
  el ciclo siguiente cierra el efecto, deja un episodio y un efecto, y no
  decide otra entrada.
- `test_temporada01_b_fin_de_reduccion` (escribe en produccion, base de
  pruebas): los casos 1 a 9 de la fila B.2, con base, hasta la confirmacion y
  el ciclo siguiente. Las subidas del motor entre medio (7 -> 14 -> 16.10) y
  los recortes con la temporada apagada se siembran como historia confirmada:
  el sujeto de la prueba es el fin de la reduccion, la exclusion de la mano y
  la entrada nueva, no la exactitud de R6/R3 (esa la cubren las pruebas de
  politica); la cadena confirmada es la fuente del rejuego.
- `test_temporada01_b_migracion_sonda_y_permisos` y
  `test_temporada01_b_migracion_reversa`: la sonda de una linea, permisos,
  triggers solo-cierre, vista con origen temporada y las dos reversas.

Corrida en MX con moneda MXN (piso 1.00, techo 45.00 del goal) para que los
bids del plan quepan. Calendario de tres bloques de baja (enero, mayo y
julio) para tres episodios E1/E2/E3. Metricas sin ventas ni gasto real: el
juicio normal da Mantener y el unico movimiento en juego es el de temporada.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_cycle import _config_version, _entidad, _estado, _metrica, _run

from app import cycle as ciclo
from app import temporada_io
from app.apply import MOTIVOS_SIN_APLICAR
from app.optimizer import recuperacion, temporada
from app.optimizer.caso import (
    BidVigente,
    CambioBid,
    CambioFechado,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    HistoriaBid,
    InsumosPausa,
    PrecioVentana,
    TemporadaCaso,
    Trayectoria,
)
from app.optimizer.temporada import (
    AjustePendiente,
    Estacion,
    FronteraHoja,
    RestauracionPendiente,
    SinPendiente,
)

RAIZ = Path(__file__).resolve().parents[1]
SONDA_0093 = (
    [
        line
        for line in (RAIZ / "migrations" / "0093_temporada01_episodio.sql")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("-- sonda-aplicada:")
    ][0]
    .removeprefix("-- sonda-aplicada:")
    .strip()
)
REVERSA_0093 = (RAIZ / "migrations" / "0093_reversa_temporada01_episodio.sql").read_text(
    encoding="utf-8"
)

# Calendario de la prueba: bajas en enero, mayo y julio (tres episodios).
MESES_PRUEBA = [
    "baja",  # enero
    "normal",  # febrero
    "alta",  # marzo
    "alta",  # abril
    "baja",  # mayo
    "normal",  # junio
    "baja",  # julio
    "normal",
    "normal",
    "alta",
    "alta",
    "normal",
]

D_E1 = dt.date(2027, 1, 4)  # primer ciclo de enero: E1 abre aqui
D_SALE_E1 = dt.date(2027, 2, 1)  # 1 feb con la temporada APAGADA (regreso retenido)
D_E2 = dt.date(2027, 5, 3)  # primer ciclo de mayo: E2 abre aqui
D_E2_B = dt.date(2027, 5, 4)
D_SOMBRA = dt.date(2027, 5, 28)  # el ciclo de practica del caso 6
D_SALE_E2 = dt.date(2027, 6, 1)  # fase normal: E2 cierra aqui
D_E3 = dt.date(2027, 7, 1)  # primer ciclo de julio: E3 abre aqui
D_C9 = dt.date(2027, 7, 11)  # el dia 11 de la baja de julio (caso 9)

RANGO_METRICAS = (dt.date(2026, 10, 1), dt.date(2027, 8, 31))
_PISO, _TECHO = Decimal("1.00"), Decimal("45.00")


def _medianoche(dia: dt.date) -> dt.datetime:
    return dt.datetime(dia.year, dia.month, dia.day, 12, 0, tzinfo=dt.UTC)


_SYNC = _medianoche(D_E1) - dt.timedelta(hours=4)


def _settings(modo_temporada: str | None = "live") -> dict:
    """Settings del ciclo: politica v3 live y temporada encendida (None =
    apagada: sin la clave el ciclo y TX3 corren sin temporada), shadow =
    practica."""
    base = {
        "ads_optimizer_mode": "live",
        "ads_bid_politica_amazon_mx": "niveles_v3",
        "ads_apply_cap_amazon_mx_bid": "100000",
        "ads_apply_cap_amazon_mx_pause": "100000",
        "ads_apply_cap_amazon_mx_negative": "100000",
        "ads_apply_cap_amazon_mx_harvest": "100000",
        "ads_temporada": {
            "version": temporada.VERSION_TEMPORADA,
            "meses": list(MESES_PRUEBA),
            "reduccion_baja_pct": "30",
            "dias_preparacion": 21,
            "mult_cortes_baja": "2",
            "aumento_presupuesto_alta_pct": "25",
            "fin_anticipado": None,
            "mantener_pausadas": [],
        },
        "ads_temporada_meta_alta_amazon_mx": "25",
    }
    if modo_temporada is not None:
        base["ads_temporada_amazon_mx"] = modo_temporada
    return base


def _goal_mx(conn) -> int:
    """Goal de plataforma MX: target 20, piso 1.00, techo 45.00, MXN, live."""
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode)"
        " VALUES ('platform', 'amazon_mx', 20, 1.00, 45.00, 'MXN', '9002', '9102',"
        " 1.00, true, 'live') RETURNING id"
    ).fetchone()[0]


def _siembra_metricas(conn, hojas: list[int]) -> None:
    """Metricas diarias sin ventas ni gasto real (1 clic, 0.01): el juicio
    normal da Mantener, la hoja no es inerte y la pausa no dispara."""
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-b') RETURNING id"
    ).fetchone()[0]
    desde, hasta = RANGO_METRICAS
    dia = desde
    while dia <= hasta:
        for hoja in hojas:
            _metrica(
                conn,
                run_id,
                hoja,
                dia,
                dt.datetime(dia.year, dia.month, dia.day, 1, tzinfo=dt.UTC),
                moneda="MXN",
                cost="0.01",
                ad_revenue="0.01",
                clicks=1,
                orders=0,
                impressions=10,
            )
        dia += dt.timedelta(days=1)


def _kw(conn, ag: int, external: str, texto: str, bid: str) -> int:
    kw = _entidad(
        conn,
        "amazon_mx",
        "keyword",
        external,
        parent=ag,
        match_type="EXACT",
        keyword_text=texto,
    )
    _estado(conn, kw, synced_at=_SYNC, current_bid=Decimal(bid), bid_currency="MXN")
    return kw


def _escenario(conn) -> dict:
    """Campana + ad group + goal MX + config encendida. Las hojas se agregan
    con `_kw` y sus metricas con `_siembra_metricas`."""
    _run(conn)
    config_id = _config_version(conn, _settings())
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "b-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "b-ag", parent=camp)
    _estado(conn, ag, synced_at=_SYNC)
    _estado(conn, camp, synced_at=_SYNC)
    return {"camp": camp, "ag": ag, "config_id": config_id}


def _handler_mx(remoto: dict[str, str]):
    """Amazon simulado para amazon_mx: LWA + /v2/profiles MX + PUT de bid +
    readback por LIST que refleja el bid remoto."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(
                200,
                json={
                    "profiles": [
                        {
                            "profileId": 414141,
                            "countryCode": "MX",
                            "currencyCode": "MXN",
                            "accountInfo": {"type": "seller", "name": "MX"},
                        }
                    ]
                },
            )
        if request.method == "PUT":
            body = json.loads(request.content)
            obj = body["keywords"][0] if "keywords" in body else body["targetingClauses"][0]
            ext = str(obj.get("keywordId") or obj.get("targetId"))
            remoto[ext] = obj["bid"]
            return httpx.Response(207, json={"ack": obj})
        if request.method == "POST" and request.url.path.endswith("/list"):
            contenedor, campo = (
                ("targetingClauses", "targetId")
                if request.url.path == "/sp/targets/list"
                else ("keywords", "keywordId")
            )
            filas = [{campo: ext, "bid": remoto[ext]} for ext in remoto]
            return httpx.Response(200, json={contenedor: filas})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    return handler


def _fabrica(handler):
    """Fabrica del Aplicador con Amazon simulado (credenciales falsas)."""
    from app.ads.config import AdsCredentials
    from app.apply import Aplicador

    def fabrica(conn, *, platform, cycle_id_ejecutor, owner, job_key, tick, transport=None):
        return Aplicador(
            conn,
            platform=platform,
            profile_id=414141,
            credentials=AdsCredentials(
                client_id="fake-client",
                client_secret="fake-secret",
                refresh_token="fake-refresh",
            ),
            cycle_id_ejecutor=cycle_id_ejecutor,
            owner=owner,
            job_key=job_key,
            tick=tick,
            guard_http=tick,
            transport=httpx.MockTransport(handler),
            sleep=lambda seconds: None,
        )

    return fabrica


def _corre(conn, dia: dt.date, handler):
    """Corre UN ciclo real el `dia` (12:00 UTC) con Amazon simulado. El acta
    del sync de estructura se refresca a 2 horas antes: la guarda de frescura
    (ARREGLOS 01 J) degrade el ciclo con el acta vieja."""
    instante = _medianoche(dia)
    run_id = conn.execute(
        "INSERT INTO ingest_run (source, started_at, ok)"
        " VALUES ('amazon_ads_structure_v2', %s, true) RETURNING id",
        (instante - dt.timedelta(hours=2),),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ads_listado_plataforma (ingest_run_id, platform,"
        " ad_groups_recibidos, product_ads_recibidos, product_ads_sin_grupo)"
        " VALUES (%s, 'amazon_mx', 0, 0, 0)",
        (run_id,),
    )
    conn.execute(
        "UPDATE ad_entity_state SET synced_at = %s WHERE synced_at < %s",
        (instante - dt.timedelta(hours=2), instante - dt.timedelta(hours=2)),
    )
    return ciclo.corre_ciclo(
        conn,
        platform="amazon_mx",
        owner=f"test-b:{dia}",
        decided_at=_medianoche(dia),
        heartbeat_cada=1,
        aplicador_factory=_fabrica(handler),
    )


def _cambio_confirmado(
    conn,
    kw: int,
    config_id: int,
    *,
    dia: dt.date | None = None,
    instante: dt.datetime | None = None,
    antes: str,
    despues: str,
    motivo: str,
    episodio_id: int | None = None,
) -> int:
    """Historia confirmada en v_cambio_bid (motor o temporada). `instante` fija
    el confirmed_at EXACTO (los ciclos reales confirman con now() de la base,
    hoy: los cambios relativos a una entrada se siembran con horas relativas a
    su confirmed_at para que la cadena quede en orden)."""
    if instante is None:
        assert dia is not None
        instante = _medianoche(dia)
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (instante, instante),
    ).fetchone()[0]
    inputs: dict = {"motor": "bid", "motivo": motivo}
    if episodio_id is not None:
        inputs["temporada_episodio_id"] = episodio_id
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s,%s,'bid',%s,%s,%s,%s,%s,%s,%s,%s,'MXN') RETURNING id",
        (
            ciclo_id,
            kw,
            instante,
            config_id,
            instante,
            (instante - dt.timedelta(days=90)).date(),
            (instante - dt.timedelta(days=10)).date(),
            Json(inputs),
            Decimal(antes),
            Decimal(despues),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack,"
        " verify_ok, applied_cycle_id) VALUES (%s,%s,'{}',true,%s)",
        (dec, instante, ciclo_id),
    )
    return dec


def _temporada_del_ciclo(conn, cycle_id: int) -> list:
    """Las decisiones de temporada del ciclo `cycle_id`: (hoja, new_value,
    motivo, episodio_id estampado)."""
    return conn.execute(
        "SELECT d.ad_entity_id, d.new_value, d.inputs->>'motivo',"
        "       d.inputs->>'temporada_episodio_id'"
        "  FROM decision d"
        " WHERE d.cycle_id = %s"
        "   AND d.inputs->>'motor' = 'bid'"
        "   AND d.inputs->>'motivo' IN ('entra_temporada_baja','ajusta_temporada_baja',"
        "       'sale_temporada_baja')"
        " ORDER BY d.id",
        (cycle_id,),
    ).fetchall()


def _bid_cache(conn, kw: int) -> Decimal:
    return conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw,)
    ).fetchone()[0]


def _mano(conn, episodio_id: int, kw: int, detectada: dt.datetime, bid: str) -> None:
    """La exclusion de la mano ya registrada (la escribe TX3 o el aplicador)."""
    conn.execute(
        "INSERT INTO temporada_exclusion (episodio_id, ad_entity_id, motivo, bid_visto,"
        " bid_visto_moneda, detectada_el) VALUES (%s, %s, 'mano_del_dueno', %s, 'MXN', %s)",
        (episodio_id, kw, Decimal(bid), detectada),
    )


def _reactivacion_manual(conn, kw: int, detectada: dt.datetime) -> None:
    conn.execute(
        "INSERT INTO reactivacion_manual (ad_entity_id, detectada_en) VALUES (%s, %s)"
        " ON CONFLICT DO NOTHING",
        (kw, detectada),
    )


def _episodio_abierto(conn) -> int:
    return conn.execute("SELECT id FROM temporada_episodio WHERE cerrado_el IS NULL").fetchone()[0]


def _fin_de(conn, kw: int, episodio_id: int) -> str | None:
    fila = conn.execute(
        "SELECT motivo FROM temporada_fin_reduccion WHERE ad_entity_id = %s AND episodio_id = %s",
        (kw, episodio_id),
    ).fetchone()
    return None if fila is None else fila[0]


# ---------------------------------------------------------------------------
# 1. test_temporada01_b_pendiente (dinero): los casos del diseno.
# ---------------------------------------------------------------------------


def _caso(
    historia: HistoriaBid, valor: str, estacion: Estacion, episodio_id: int | None, **kw
) -> CasoHoja:
    return CasoHoja(
        plataforma="amazon_mx",
        hoja_id=kw.get("hoja_id", 1),
        ad_group_id=None,
        bid=BidVigente(valor=Decimal(valor), moneda="MXN", piso=_PISO, techo=_TECHO),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="MXN",
                equilibrio_acos_pct=None,
                gasto_para_concluir=Decimal("350"),
                confianza_recorte=Decimal("0.80"),
                confianza_subida=Decimal("0.70"),
            ),
            target_acos_pct=Decimal("20"),
        ),
        propia=None,
        pedidos_inmaduros=None,
        grupo=None,
        cuenta=None,
        precio=PrecioVentana(gasto=None, clics=None),
        trayectoria=Trayectoria(cambios=(), efecto=None),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=Decimal("500"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=D_E1,
        ventana_hasta=D_E1,
        observado_al=None,
        temporada=TemporadaCaso(
            estacion=estacion,
            episodio=(
                None
                if episodio_id is None
                else temporada.EpisodioVigente(id=episodio_id, abierto_el=_medianoche(D_E1))
            ),
            excluida_en=kw.get("excluida_en", frozenset()),
            meta_alta_pct=Decimal("25"),
            meta_manual=False,
            frontera=FronteraHoja(
                fase_hoja=estacion.fase,
                mult_cortes=estacion.mult_cortes_baja,
                cerca_hoja=None,
                cerca_plataforma=None,
                episodio_id=episodio_id,
            ),
        ),
        historia=historia,
    )


def test_temporada01_b_pendiente():
    """Entra una vez; cambiar mayo a normal con junio en baja no recorta
    5.60 -> 3.92; 30 -> 20 da 6.40; mano de David no regresa; el regreso nunca
    baja."""
    entrada = (
        CambioFechado(
            _medianoche(D_E1), 2, Decimal("8.00"), Decimal("5.60"), "temporada", "entra", 1
        ),
    )
    historia = HistoriaBid(desde=D_E1, cambios=entrada)

    baja = Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=D_E1,
        fase="baja",
        previa_alta=False,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=D_E1,
    )

    # Entra una vez: con la reduccion viva del episodio abierto, ninguna otra
    # entrada; el recorte 5.60 -> 3.92 (aplicar 30 % sobre el bid ya reducido)
    # jamas sale.
    p = temporada.pendiente(_caso(historia, "5.60", baja, 1))
    assert p == SinPendiente("ya_entro"), p

    # Cambiar el calendario a media baja (junio en baja con otro episodio
    # abierto) NO re-recorta: la hoja ya entro en SU episodio y la reduccion
    # vigente nunca recibe otra entrada (decision 1 del diseno).
    baja_junio = Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=dt.date(2027, 6, 15),
        fase="baja",
        previa_alta=False,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=dt.date(2027, 6, 1),
    )
    p = temporada.pendiente(_caso(historia, "5.60", baja_junio, 2))
    assert p == SinPendiente("ya_entro"), p

    # 30 -> 20 con base 8.00: AjustePendiente(6.40), nunca 5.60 x 0.80 = 4.48.
    baja20 = Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=D_E1,
        fase="baja",
        previa_alta=False,
        reduccion_baja=Decimal("0.20"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=D_E1,
    )
    p = temporada.pendiente(_caso(historia, "5.60", baja20, 1))
    assert isinstance(p, AjustePendiente) and p.destino == Decimal("6.40"), p

    # Mano de David no regresa: el bid vigente 7.00 ya no es el ultimo de
    # Orbit (dueno_al_final) -> dueno_decidio, sin entrada y sin regreso.
    p = temporada.pendiente(_caso(historia, "7.00", baja, 1))
    assert p == SinPendiente("dueno_decidio"), p

    # El regreso nunca baja: fuera de la baja, 5.60 regresa a la base 8.00; y
    # con el bid ya arriba de su nivel, ya_arriba (nada que hacer).
    normal = Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=D_SALE_E1,
        fase="normal",
        previa_alta=True,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=D_SALE_E1,
    )
    p = temporada.pendiente(_caso(historia, "5.60", normal, None))
    assert isinstance(p, RestauracionPendiente) and p.nivel == Decimal("8.00"), p
    assert p.nivel >= Decimal("5.60")
    # Con el bid ya arriba de su nivel por subidas CONFIRMADAS de Orbit (R6),
    # ya_arriba: el regreso nunca baja el bid.
    subidas = (
        CambioFechado(
            _medianoche(D_E1), 2, Decimal("8.00"), Decimal("5.60"), "temporada", "entra", 1
        ),
        CambioFechado(
            _medianoche(D_E1 + dt.timedelta(days=3)),
            3,
            Decimal("5.60"),
            Decimal("6.44"),
            "motor",
            None,
            None,
        ),
        CambioFechado(
            _medianoche(D_E1 + dt.timedelta(days=6)),
            4,
            Decimal("6.44"),
            Decimal("7.41"),
            "motor",
            None,
            None,
        ),
        CambioFechado(
            _medianoche(D_E1 + dt.timedelta(days=9)),
            5,
            Decimal("7.41"),
            Decimal("8.52"),
            "motor",
            None,
            None,
        ),
    )
    arriba = HistoriaBid(desde=D_E1, cambios=subidas)
    p = temporada.pendiente(_caso(arriba, "8.52", normal, None))
    # El regreso nunca baja: arriba del nivel, nada que restaurar (el motivo
    # informativo lo resuelve reduccion_vigente con el fin ya_arriba).
    assert isinstance(p, SinPendiente) and p.motivo in ("ya_arriba", "fuera_de_temporada"), p
    # Y una mano del dueno (9.00 != 5.60) deja la hoja en su terreno fuera de
    # la baja: sin pendiente de temporada.
    p = temporada.pendiente(_caso(historia, "9.00", normal, None))
    assert p == SinPendiente("fuera_de_temporada"), p

    # Destino de la entrada en centavos (los ejemplos del diseno).
    assert temporada.destino_entrada(
        Decimal("0.15"), Decimal("0.30"), Decimal("0.10"), Decimal("2.50")
    ) == Decimal("0.11")
    assert temporada.destino_entrada(
        Decimal("1.55"), Decimal("0.30"), Decimal("1.00"), Decimal("45.00")
    ) == Decimal("1.09")
    assert (
        temporada.destino_entrada(
            Decimal("0.10"), Decimal("0.30"), Decimal("0.10"), Decimal("2.50")
        )
        is None
    )

    # Con limites FRACCIONARIOS del goal el destino vive en centavos del
    # intervalo valido (revision: Amazon recibe centavos y un clamp sin
    # redondeo enviaria una puja fuera del rango).
    assert temporada.destino_entrada(
        Decimal("8.00"), Decimal("0.30"), Decimal("5.6049"), Decimal("45.00")
    ) == Decimal("5.61"), "el primer centavo que cumple el piso fraccionario"
    assert temporada.destino_entrada(
        Decimal("20.00"), Decimal("0.30"), Decimal("1.00"), Decimal("13.999")
    ) == Decimal("13.99"), "el ultimo centavo que cumple el techo fraccionario"
    assert (
        temporada.destino_entrada(
            Decimal("5.60"), Decimal("0.30"), Decimal("5.6049"), Decimal("45.00")
        )
        is None
    ), "sin centavo factible: el piso fraccionario deja fuera a 5.60"

    # El regreso tambien queda en centavos del intervalo valido.
    red = recuperacion.ReduccionVigente(
        episodio_id=1,
        base=Decimal("20.00"),
        primer_el=_medianoche(D_E1),
        ultimo_bid=Decimal("14.00"),
        ultimo_el=_medianoche(D_E1 + dt.timedelta(days=5)),
        solo_temporada_despues=True,
    )
    assert temporada.nivel_de_regreso(
        red, Decimal("14.00"), Decimal("1.00"), Decimal("15.999")
    ) == Decimal("15.99"), "el regreso clampeado al techo fraccionario baja al centavo"
    assert temporada.nivel_de_regreso(
        red, Decimal("14.00"), Decimal("1.00"), Decimal("45.00")
    ) == Decimal("20.00")

    # Rango del goal SIN ningun centavo factible: nivel None (jamás una
    # excepcion de plataforma), la reduccion queda pendiente sin fin falso y
    # el veredicto de la hoja es dato_faltante (revision).
    rango_roto = temporada._centavos_del_rango(Decimal("5.6050"), Decimal("5.6090"))
    assert rango_roto is None, "5.6050..5.6090 no contiene centavos"
    assert (
        temporada.nivel_de_regreso(red, Decimal("5.60"), Decimal("5.6050"), Decimal("5.6090"))
        is None
    )
    cadena_kw = HistoriaBid(
        desde=D_E1,
        cambios=(
            CambioFechado(
                _medianoche(D_E1), 2, Decimal("8.00"), Decimal("5.60"), "temporada", "entra", 1
            ),
        ),
    )
    normal_rota = Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=D_SALE_E1,
        fase="normal",
        previa_alta=True,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=D_SALE_E1,
    )
    cad_rota = recuperacion._cadena_desde(cadena_kw, Decimal("5.60"))
    red_out, fin_out = temporada.reduccion_vigente(
        cad_rota,
        en_baja=False,
        dia=D_SALE_E1,
        episodio=None,
        bid=Decimal("5.60"),
        piso=Decimal("5.6050"),
        techo=Decimal("5.6090"),
    )
    assert fin_out is None, "sin fin falso: el regreso queda pendiente"
    assert red_out is not None and red_out.episodio_id == 1
    p = temporada._pendiente_desde(
        normal_rota,
        cad_rota,
        episodio=None,
        excluida_en=frozenset(),
        bid=Decimal("5.60"),
        piso=Decimal("5.6050"),
        techo=Decimal("5.6090"),
    )
    assert p == SinPendiente("dato_faltante"), p
    frontera_rota = temporada.frontera(
        normal_rota,
        cad_rota,
        episodio=None,
        excluida_en=frozenset(),
        cerca_plataforma=None,
        bid=Decimal("5.60"),
        piso=Decimal("5.6050"),
        techo=Decimal("5.6090"),
    )
    assert frontera_rota.fin is None and frontera_rota.fase_hoja == "baja", (
        "la hoja con regreso imposible sigue en baja (conservadora): "
        f"{frontera_rota.fase_hoja}, fin={frontera_rota.fin}"
    )


def test_temporada01_b_trayectoria_con_temporada_rejuega():
    """Revision: un cambio de temporada dentro de `Trayectoria` (la de 90
    dias) sobrevive el freeze/desfreeze del caso — `_cambio_desde` acepta el
    origen `temporada` y el rejugado y el panel de ruido no descartan la
    decision."""
    entrada = CambioBid(
        fecha=D_E1,
        bid_antes=Decimal("8.00"),
        bid_despues=Decimal("5.60"),
        origen="temporada",
    )
    motor = CambioBid(
        fecha=D_E1 + dt.timedelta(days=3),
        bid_antes=Decimal("5.60"),
        bid_despues=Decimal("5.10"),
        origen="motor",
    )
    trayectoria = Trayectoria(
        cambios=(entrada, motor),
        efecto=EfectoCambio(
            dias_post=2,
            impresiones_pre7=None,
            clics_pre7=None,
            impresiones_post=None,
            dias_post_trafico=0,
            clics_post=None,
            gasto_post=None,
            clics_pre=None,
            gasto_pre=None,
            vendia=None,
        ),
    )
    estacion = Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=D_E1,
        fase="baja",
        previa_alta=False,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=D_E1,
    )
    caso = _caso(HistoriaBid(desde=D_E1, cambios=()), "5.60", estacion, 1)
    caso_con_trayectoria = replace(caso, trayectoria=trayectoria)
    datos = caso_con_trayectoria.como_json()
    rejuego = CasoHoja.desde_json(datos)
    assert rejuego.trayectoria.cambios[0].origen == "temporada"
    assert rejuego.trayectoria == trayectoria
    assert rejuego.trayectoria.piso_aprendido is None, "la entrada de temporada no ensena piso"


# ---------------------------------------------------------------------------
# 2. test_temporada01_b_episodio_reparado_tras_caida (escribe en produccion).
# ---------------------------------------------------------------------------


def test_temporada01_b_episodio_reparado_tras_caida(db_template):
    """Entrada confirmada y efecto abierto como tras una caida; el ciclo
    siguiente cierra el efecto, deja un episodio y un efecto, y no decide otra
    entrada."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "5.60"})

    # El estado tras una caida, sembrado a mano (el trigger solo-cierre
    # impide reproducirlo con UPDATEs): la entrada LLEGO a Amazon (el PUT del
    # ciclo anterior) y el proceso cayo antes de sellar: decision SIN resumen,
    # ledger SIN sello, episodio y efecto ABIERTOS, y el cache todavia en el
    # bid viejo. La reconciliacion del ciclo siguiente lo resuelve.
    config_id = ids["config_id"]
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (_medianoche(D_E1), _medianoche(D_E1)),
    ).fetchone()[0]
    e1 = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (_medianoche(D_E1), ciclo_id, config_id),
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s,%s,'bid',%s,%s,%s,%s,%s,%s,8.00,5.60,'MXN') RETURNING id",
        (
            ciclo_id,
            kw1,
            _medianoche(D_E1),
            config_id,
            _medianoche(D_E1),
            D_E1 - dt.timedelta(days=90),
            D_E1 - dt.timedelta(days=10),
            Json({"motor": "bid", "motivo": "entra_temporada_baja", "temporada_episodio_id": e1}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO temporada_efecto (episodio_id, ad_entity_id, decision_id, tipo,"
        " abierto_el) VALUES (%s, %s, %s, 'entra', %s)",
        (e1, kw1, dec, _medianoche(D_E1)),
    )
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " started_at) VALUES (%s, 1, 'normal', '{}', true, %s)",
        (dec, _medianoche(D_E1)),
    )
    assert (
        conn.execute("SELECT count(*) FROM temporada_efecto WHERE cerrado_el IS NULL").fetchone()[0]
        == 1
    )

    # El ciclo siguiente: la reconciliacion del ledger confirma la entrada
    # (Amazon ya la aplico), cierra el efecto 'confirmado' en la misma
    # transaccion, queda un episodio y un efecto, y no decide otra entrada.
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done"
    assert (
        conn.execute("SELECT count(*) FROM temporada_episodio WHERE cerrado_el IS NULL").fetchone()[
            0
        ]
        == 1
    )
    efectos = conn.execute(
        "SELECT cerrado_el IS NOT NULL, desenlace FROM temporada_efecto ORDER BY id"
    ).fetchall()
    assert efectos == [(True, "confirmado")], efectos
    assert _temporada_del_ciclo(conn, res.cycle_id) == []
    # La cadena confirmada dice «reducida»: la vista clasifica la entrada con
    # su episodio y el cache quedo con lo leido de Amazon.
    assert conn.execute(
        "SELECT origen, motivo_temporada, episodio_id FROM v_cambio_bid WHERE hoja_id = %s",
        (kw1,),
    ).fetchone() == ("temporada", "entra", e1)
    assert _bid_cache(conn, kw1) == Decimal("5.60")

    # Revision: al salir la baja, TX3 cierra el episodio y emite el regreso; el
    # efecto del regreso se abre con el episodio ESTAMPADO (E1, ya cerrado) y
    # su desenlace divergente deja la exclusion con el bid leido. Amazon
    # "tiene" 7.00 y NO aplica el PUT del regreso (el dueno gano la carrera).
    listas = {"n": 0}

    def handler_div(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(
                200,
                json={
                    "profiles": [
                        {
                            "profileId": 414141,
                            "countryCode": "MX",
                            "currencyCode": "MXN",
                            "accountInfo": {"type": "seller", "name": "MX"},
                        }
                    ]
                },
            )
        if request.method == "PUT":
            body = json.loads(request.content)
            obj = body["keywords"][0] if "keywords" in body else body["targetingClauses"][0]
            return httpx.Response(207, json={"ack": obj})
        if request.method == "POST" and request.url.path.endswith("/list"):
            contenedor, campo = (
                ("targetingClauses", "targetId")
                if request.url.path == "/sp/targets/list"
                else ("keywords", "keywordId")
            )
            listas["n"] += 1
            # La lectura fresca ve el 5.60 que Orbit esperaba; el readback del
            # PUT ve 7.00 (el dueno gano la carrera DESPUES de la lectura).
            bid = "5.60" if listas["n"] == 1 else "7.00"
            return httpx.Response(200, json={contenedor: [{campo: "b-kw1", "bid": bid}]})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    res = _corre(conn, D_SALE_E1, handler_div)
    assert res.status == "done"
    regreso = [row for row in _temporada_del_ciclo(conn, res.cycle_id) if row[0] == kw1]
    assert (
        regreso and regreso[0][1] == Decimal("8.00") and regreso[0][2] == "sale_temporada_baja"
    ), regreso
    filas = conn.execute(
        "SELECT te.episodio_id, te.cerrado_el IS NOT NULL, te.desenlace,"
        "       te.episodio_id = (d.inputs->>'temporada_episodio_id')::bigint"
        "  FROM temporada_efecto te"
        "  JOIN decision d ON d.id = te.decision_id"
        " ORDER BY te.id",
    ).fetchall()
    assert len(filas) == 2
    assert filas[0][:1] == (e1,) and filas[0][2:4] == ("confirmado", True), filas
    assert filas[1] == (e1, True, "divergencia", True), filas
    exclusion = conn.execute(
        "SELECT motivo, bid_visto, decision_id FROM temporada_exclusion WHERE ad_entity_id = %s",
        (kw1,),
    ).fetchone()
    dec_regreso = conn.execute(
        "SELECT id FROM decision WHERE inputs->>'motivo' = 'sale_temporada_baja'"
        "   AND ad_entity_id = %s ORDER BY id DESC LIMIT 1",
        (kw1,),
    ).fetchone()[0]
    assert exclusion == ("divergencia", Decimal("7.00"), dec_regreso), exclusion
    assert (
        conn.execute("SELECT cerrado_el FROM temporada_episodio WHERE id = %s", (e1,)).fetchone()[0]
        is not None
    )


def test_temporada01_b_efecto_con_intento_sellado_se_cierra(db_template):
    """Revision (bloqueo persistente): una entrada SIN confirmar cuyo intento
    quedo SELLADO sin desenlace (fallo:readback_ambiguo) deja el efecto
    abierto; sin el cierre de TX3 el indice unico revienta el ciclo entero en
    cada corrida. El ciclo siguiente reconcilia el sellado-ambiguo con GET
    fresco: Amazon sigue en 8.00 -> NO aplico ('perdida', efecto
    'sin_aplicar', hoja libre); el ciclo posterior decide la entrada de nuevo
    y la confirma."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "8.00"})
    config_id = ids["config_id"]

    # El estado de la caida ambigua, sembrado a mano: la decision de entrada
    # 8 -> 5.60 SIN resumen (Amazon no respondio concluyente), su intento
    # SELLADO, el episodio y el efecto ABIERTOS, y el cache todavia en 8.00.
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (_medianoche(D_E1), _medianoche(D_E1)),
    ).fetchone()[0]
    e1 = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (_medianoche(D_E1), ciclo_id, config_id),
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s,%s,'bid',%s,%s,%s,%s,%s,%s,8.00,5.60,'MXN') RETURNING id",
        (
            ciclo_id,
            kw1,
            _medianoche(D_E1),
            config_id,
            _medianoche(D_E1),
            D_E1 - dt.timedelta(days=90),
            D_E1 - dt.timedelta(days=10),
            Json({"motor": "bid", "motivo": "entra_temporada_baja", "temporada_episodio_id": e1}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " started_at, ack, resultado, finished_at)"
        " VALUES (%s, 1, 'normal', '{}', true, %s, NULL, 'fallo:readback_ambiguo', now())",
        (dec, _medianoche(D_E1)),
    )
    conn.execute(
        "INSERT INTO temporada_efecto (episodio_id, ad_entity_id, decision_id, tipo,"
        " abierto_el) VALUES (%s, %s, %s, 'entra', %s)",
        (e1, kw1, dec, _medianoche(D_E1)),
    )

    # El ciclo siguiente reconcilia el sellado-ambiguo con GET fresco (jamás
    # reenvía el PUT): Amazon sigue en 8.00 == old_value -> NO aplico
    # ('perdida', efecto 'sin_aplicar', la hoja queda libre con el cache en el
    # bid real); SIN decisiones de temporada en este ciclo.
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    assert _temporada_del_ciclo(conn, res.cycle_id) == []
    desenlaces = conn.execute(
        "SELECT cerrado_el IS NOT NULL, desenlace FROM temporada_efecto ORDER BY id"
    ).fetchall()
    assert desenlaces == [(True, "sin_aplicar")], desenlaces
    assert conn.execute(
        "SELECT motivo FROM decision_sin_aplicar WHERE decision_id = %s", (dec,)
    ).fetchone() == ("perdida",), "no aplico: Amazon conserva el bid anterior"
    probe = conn.execute(
        "SELECT resultado FROM apply_attempt WHERE tipo = 'probe' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert probe == ("ok:no_aplicado",), probe
    assert _bid_cache(conn, kw1) == Decimal("8.00"), "el cache queda con el bid real"

    # El ciclo siguiente decide la entrada de nuevo (sobre el bid real) y la
    # confirma.
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    nueva = [row for row in _temporada_del_ciclo(conn, res.cycle_id) if row[0] == kw1]
    assert nueva == [(kw1, Decimal("5.60"), "entra_temporada_baja", str(e1))], nueva
    assert _bid_cache(conn, kw1) == Decimal("5.60")


def _siembra_ambigua(
    conn, ids: dict, kw: int, *, cache: str = "5.60", sellado: bool = True
) -> dict:
    """El estado de la caida ambigua (revision): la decision de entrada
    8 -> 5.60 SIN resumen, su intento SELLADO con lectura AMBIGUA, el episodio
    y el efecto ABIERTOS. `cache` es el bid que la sincronizacion trajo de
    Amazon (por defecto, el destino ya aplicado)."""
    config_id = ids["config_id"]
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (_medianoche(D_E1), _medianoche(D_E1)),
    ).fetchone()[0]
    e1 = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (_medianoche(D_E1), ciclo_id, config_id),
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s,%s,'bid',%s,%s,%s,%s,%s,%s,8.00,5.60,'MXN') RETURNING id",
        (
            ciclo_id,
            kw,
            _medianoche(D_E1),
            config_id,
            _medianoche(D_E1),
            D_E1 - dt.timedelta(days=90),
            D_E1 - dt.timedelta(days=10),
            Json({"motor": "bid", "motivo": "entra_temporada_baja", "temporada_episodio_id": e1}),
        ),
    ).fetchone()[0]
    if sellado:
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, started_at, ack, resultado, finished_at)"
            " VALUES (%s, 1, 'normal', '{}', true, %s, NULL, 'fallo:readback_ambiguo', now())",
            (dec, _medianoche(D_E1)),
        )
    else:
        # El crash fue entre el PUT y el sello: el intento queda SIN sello.
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, started_at) VALUES (%s, 1, 'normal', '{}', true, %s)",
            (dec, _medianoche(D_E1)),
        )
    conn.execute(
        "INSERT INTO temporada_efecto (episodio_id, ad_entity_id, decision_id, tipo,"
        " abierto_el) VALUES (%s, %s, %s, 'entra', %s)",
        (e1, kw, dec, _medianoche(D_E1)),
    )
    conn.execute(
        "UPDATE ad_entity_state SET current_bid = %s WHERE ad_entity_id = %s", (Decimal(cache), kw)
    )
    return {"ciclo": ciclo_id, "e1": e1, "dec": dec}


def test_temporada01_b_entrada_ambigua_se_confirma_al_reconciliar(db_template):
    """Revision (doble recorte): la entrada SIN confirmar cuyo intento quedo
    SELLADO con lectura AMBIGUA (Amazon YA la aplico: el sync trajo el destino
    al cache). El ciclo siguiente la BLOCKEA (en_vuelo: no recorta otra vez) y
    la fase de apply la reconcilia con GET fresco SIN reenviar el PUT: puja ==
    destino -> CONFIRMA la entrada original (sin decision_sin_aplicar falso).
    El ciclo posterior rejuega la cadena confirmada: jamas otra entrada."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "5.60"})

    amb = _siembra_ambigua(conn, ids, kw1)
    e1 = amb["e1"]

    # El ciclo siguiente: TX2 BLOCKEA la hoja (en_vuelo: no doble recorte) y
    # la fase de apply confirma la entrada con GET fresco (5.60 == destino).
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    assert _temporada_del_ciclo(conn, res.cycle_id) == [], "no doble recorte"
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision d"
        " JOIN decision_application da ON da.decision_id = d.id"
        " WHERE d.ad_entity_id = %s AND d.inputs->>'motivo' = 'entra_temporada_baja'",
        (kw1,),
    ).fetchone()
    assert resumen == (True,), resumen
    filas = conn.execute(
        "SELECT te.episodio_id, te.cerrado_el IS NOT NULL, te.desenlace"
        "  FROM temporada_efecto te ORDER BY te.id"
    ).fetchall()
    assert filas == [(e1, True, "confirmado")], filas
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_sin_aplicar WHERE decision_id IN"
            " (SELECT id FROM decision WHERE inputs->>'motivo' = 'entra_temporada_baja')"
        ).fetchone()[0]
        == 0
    ), "sin 'sin_aplicar' falso: la entrada quedo confirmada"
    probe = conn.execute(
        "SELECT resultado FROM apply_attempt WHERE tipo = 'probe' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert probe == ("ok:reconciliado",), probe
    assert conn.execute(
        "SELECT origen, motivo_temporada, episodio_id FROM v_cambio_bid WHERE hoja_id = %s",
        (kw1,),
    ).fetchone() == ("temporada", "entra", e1)

    # El ciclo posterior rejuega la cadena confirmada: jamas otra entrada.
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    assert _temporada_del_ciclo(conn, res.cycle_id) == []
    assert _bid_cache(conn, kw1) == Decimal("5.60")


def _evidencia_recorte(conn, kw: int) -> None:
    """Metricas de la ventana madura con gasto alto sin ventas y pocos clicks
    (hora 2: pisa la observacion de hora 1 en v_metric_latest, misma key
    dia+hoja): R9 produce un recorte -25 % (movimiento) sin disparar la
    pausa economica."""
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-b-gasto') RETURNING id"
    ).fetchone()[0]
    desde, hasta = RANGO_METRICAS
    dia = desde
    while dia <= hasta:
        _metrica(
            conn,
            run_id,
            kw,
            dia,
            dt.datetime(dia.year, dia.month, dia.day, 2, tzinfo=dt.UTC),
            moneda="MXN",
            cost="10.00",
            ad_revenue="0.01",
            clicks=1,
            orders=0,
            impressions=200,
        )
        dia += dt.timedelta(days=1)


def test_temporada01_b_pendiente_ambigua_bloquea_nuevas_pujas(db_template):
    """Revision: mientras la lectura ambigua esta pendiente de conciliar (sin
    desenlace), la hoja NO genera otra puja: las reglas normales producen su
    movimiento (R9) y el ciclo lo SALTA con bid_en_vuelo; la fase de apply
    confirma la entrada original con GET fresco (puja == destino) y jamas
    envia el movimiento dudoso."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "5.60"})
    _siembra_ambigua(conn, ids, kw1)
    _evidencia_recorte(conn, kw1)

    # Ciclo con la conciliacion pendiente: las reglas normales deciden un
    # movimiento (R9 sobre 5.60 -> 4.20) y el gate bid_en_vuelo lo SALTA.
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    skips = json.loads(res.notes).get("skips", {}).get("entidad", {})
    assert skips.get("bid_en_vuelo") == 1, skips
    assert _temporada_del_ciclo(conn, res.cycle_id) == [], "sin doble recorte"
    nueva = conn.execute(
        "SELECT new_value FROM decision WHERE ad_entity_id = %s AND cycle_id = %s AND kind = 'bid'",
        (kw1, res.cycle_id),
    ).fetchall()
    assert nueva == [], nueva
    # La fase de apply confirma la entrada original (puja == destino).
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision d"
        " JOIN decision_application da ON da.decision_id = d.id"
        " WHERE d.ad_entity_id = %s AND d.inputs->>'motivo' = 'entra_temporada_baja'",
        (kw1,),
    ).fetchone()
    assert resumen == (True,), resumen
    assert _bid_cache(conn, kw1) == Decimal("5.60")

    # El ciclo posterior rejuega la cadena confirmada: sin pendiente, las
    # reglas normales deciden normal (sin recorte doble de temporada).
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    assert _temporada_del_ciclo(conn, res.cycle_id) == []


def test_temporada01_b_tercer_valor_divergencia_definitiva(db_template):
    """Revision: una TERCERA puja legible es divergencia DEFINITIVA: el
    resumen verify_ok FALSE deja de calificar la decision para futuras
    conciliaciones (aunque Amazon muestre despues el destino original, la
    lectura jamas confirma la decision equivocada) y el efecto cierra con
    exclusion del bid divergente."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    remoto = {"b-kw1": "7.00"}
    handler = _handler_mx(remoto)
    amb = _siembra_ambigua(conn, ids, kw1)

    # Primera conciliacion valida: 7.00 (tercer valor) -> divergencia
    # definitiva con resumen FALSE y exclusion del bid divergente.
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision_application da WHERE da.decision_id = %s",
        (amb["dec"],),
    ).fetchone()
    assert resumen == (False,), resumen
    exclusion = conn.execute(
        "SELECT motivo, bid_visto FROM temporada_exclusion WHERE ad_entity_id = %s",
        (kw1,),
    ).fetchone()
    assert exclusion == ("divergencia", Decimal("7.00")), exclusion

    # Amazon muestra DESPUES el destino original: la decision YA tiene
    # desenlace y NO se vuelve a conciliar (jamas confirma la decision
    # equivocada).
    remoto["b-kw1"] = "5.60"
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    assert conn.execute(
        "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (amb["dec"],)
    ).fetchone() == (False,), "la divergencia es definitiva"
    probes = conn.execute(
        "SELECT count(*) FROM apply_attempt WHERE tipo = 'probe' AND decision_id = %s",
        (amb["dec"],),
    ).fetchone()[0]
    assert probes == 1, probes


def test_temporada01_b_ambigua_campana_pausa_no_descarta(db_template):
    """Revision: con la campana PAUSA, la conciliacion del sellado-ambiguo NO
    consulta Amazon y NO escribe un desenlace terminal (Amazon puede haber
    aplicado la reduccion: descartarla perderia la base de regreso). Al
    reactivar la campana, el ciclo confirma la reduccion aplicada SIN
    reenviar el PUT."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "5.60"})
    amb = _siembra_ambigua(conn, ids, kw1)
    camp = ids["camp"]

    # Campana pausada: la conciliacion NO consulta Amazon y deja el intento
    # pendiente (sin desenlace terminal).
    conn.execute("UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (camp,))

    def handler_prohibido(request: httpx.Request) -> httpx.Response:
        raise AssertionError("ninguna consulta a Amazon con la campana pausada")

    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler_prohibido)
    assert res.status == "done", res.notes
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_application WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_sin_aplicar WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT cerrado_el FROM temporada_efecto WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        is None
    ), "el efecto queda abierto: la reduccion no se descarta"

    # Campana reactivada: el ciclo confirma la reduccion aplicada sin
    # reenviar el PUT.
    conn.execute("UPDATE ad_entity_state SET status = 'ENABLED' WHERE ad_entity_id = %s", (camp,))
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision_application da WHERE da.decision_id = %s",
        (amb["dec"],),
    ).fetchone()
    assert resumen == (True,), resumen
    reenvios = conn.execute(
        "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'probe'"
        " AND resultado = 'ok:no_aplicado'",
        (amb["dec"],),
    ).fetchone()[0]
    assert reenvios == 0, "jamás se reenvía el PUT"


def test_temporada01_b_ambigua_sin_puja_legible_reintenta(db_template):
    """Revision: un listado SIN la fila buscada no es concluyente: el efecto
    queda ABIERTO (sin divergencia) y el ciclo siguiente, con la fila
    legible, confirma la entrada original (jamás otro recorte sobre el bid
    ya reducido)."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    remoto: dict[str, str] = {}
    handler = _handler_mx(remoto)
    amb = _siembra_ambigua(conn, ids, kw1)

    # Ciclo 1: el listado NO trae la fila de la hoja (sin puja legible): el
    # efecto queda ABIERTO, sin desenlace ni resumen.
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    assert (
        conn.execute(
            "SELECT cerrado_el FROM temporada_efecto WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        is None
    ), "sin lectura concluyente: el efecto queda abierto"
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_application WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'probe'",
            (amb["dec"],),
        ).fetchone()[0]
        == 0
    ), "sin rastro de divergencia sin valor legible"

    # Ciclo 2: la fila aparece con el DESTINO: la lectura confirma la entrada
    # original (jamás otro recorte sobre el bid ya reducido).
    remoto["b-kw1"] = "5.60"
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision_application da WHERE da.decision_id = %s",
        (amb["dec"],),
    ).fetchone()
    assert resumen == (True,), resumen
    assert _bid_cache(conn, kw1) == Decimal("5.60")
    doble = [
        row
        for row in _temporada_del_ciclo(conn, res.cycle_id)
        if row[0] == kw1 and row[1] == Decimal("3.92")
    ]
    assert doble == [], "jamás otro recorte 5.60 -> 3.92"


def test_temporada01_b_sin_sello_campana_pausa_reconcilia_al_reactivar(db_template):
    """Revision (el flujo COMIENZA sin sello): la caida deja el intento SIN
    sello y la campana aparece PAUSED en la primera reconciliacion: sin
    desenlace terminal (Amazon puede haber aplicado la reduccion) y sin
    consulta a Amazon; al reactivar la campana, el ciclo confirma la entrada
    con GET fresco (jamás otro recorte sobre el bid ya reducido)."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "5.60"})
    config_id = ids["config_id"]
    camp = ids["camp"]

    # El estado de la caida, sembrado a mano: la decision de entrada SIN
    # resumen, su intento SIN sello (el crash fue entre el PUT y el sello), el
    # episodio y el efecto ABIERTOS, el cache YA sincronizado a 5.60 y la
    # campana PAUSED.
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (_medianoche(D_E1), _medianoche(D_E1)),
    ).fetchone()[0]
    e1 = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (_medianoche(D_E1), ciclo_id, config_id),
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s,%s,'bid',%s,%s,%s,%s,%s,%s,8.00,5.60,'MXN') RETURNING id",
        (
            ciclo_id,
            kw1,
            _medianoche(D_E1),
            config_id,
            _medianoche(D_E1),
            D_E1 - dt.timedelta(days=90),
            D_E1 - dt.timedelta(days=10),
            Json({"motor": "bid", "motivo": "entra_temporada_baja", "temporada_episodio_id": e1}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " started_at) VALUES (%s, 1, 'normal', '{}', true, %s)",
        (dec, _medianoche(D_E1)),
    )
    conn.execute(
        "INSERT INTO temporada_efecto (episodio_id, ad_entity_id, decision_id, tipo,"
        " abierto_el) VALUES (%s, %s, %s, 'entra', %s)",
        (e1, kw1, dec, _medianoche(D_E1)),
    )
    conn.execute("UPDATE ad_entity_state SET current_bid = 5.60 WHERE ad_entity_id = %s", (kw1,))
    conn.execute("UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (camp,))
    conn.commit()

    def handler_prohibido(request: httpx.Request) -> httpx.Response:
        raise AssertionError("ninguna consulta a Amazon con la campana pausada")

    # Ciclo 1 (campana pausada): SIN consulta a Amazon, SIN sello del intento
    # y SIN desenlace terminal (el efecto queda abierto: la reduccion no se
    # descarta).
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler_prohibido)
    assert res.status == "done", res.notes
    assert (
        conn.execute(
            "SELECT finished_at FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        is None
    ), "el intento queda sin sello (pendiente)"
    assert (
        conn.execute(
            "SELECT cerrado_el FROM temporada_efecto WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        is None
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_sin_aplicar WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        == 0
    ), "sin descarte de la reduccion aplicada"

    # Campana reactivada: la reconciliacion confirma la entrada con GET fresco
    # (5.60 == destino) sin reenviar el PUT.
    conn.execute("UPDATE ad_entity_state SET status = 'ENABLED' WHERE ad_entity_id = %s", (camp,))
    conn.commit()
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision_application da WHERE da.decision_id = %s",
        (dec,),
    ).fetchone()
    assert resumen == (True,), resumen
    assert conn.execute(
        "SELECT desenlace FROM temporada_efecto WHERE decision_id = %s", (dec,)
    ).fetchone() == ("confirmado",), "la reduccion queda confirmada"

    # El ciclo posterior rejuega la cadena confirmada: jamás otro recorte.
    res = _corre(conn, D_E1 + dt.timedelta(days=3), handler)
    assert res.status == "done", res.notes
    doble = [
        row
        for row in _temporada_del_ciclo(conn, res.cycle_id)
        if row[0] == kw1 and row[1] == Decimal("3.92")
    ]
    assert doble == [], "jamás otro recorte 5.60 -> 3.92"
    assert _bid_cache(conn, kw1) == Decimal("5.60")


def test_temporada01_b_cerca_plataforma_no_se_reinicia_con_ajuste(db_template):
    """Revision: la cerca de la plataforma nace de la PRIMERA confirmacion de
    la ultima TRANSICION (entrada o salida). Un ajuste de la reduccion a media
    baja no es transicion: `cerca_plataforma` conserva el dia siguiente a la
    entrada, aunque el ajuste se confirme dos dias despues."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    handler = _handler_mx({"b-kw1": "8.00"})

    # Ciclo de E1: la entrada se confirma (esta es la transicion).
    res = _corre(conn, D_E1, handler)
    assert res.status == "done"
    e1 = _episodio_abierto(conn)
    entrada_el = conn.execute(
        "SELECT da.confirmed_at FROM decision d"
        " JOIN decision_application da ON da.decision_id = d.id"
        " WHERE d.ad_entity_id = %s AND d.inputs->>'motivo' = 'entra_temporada_baja'",
        (kw1,),
    ).fetchone()[0]
    cerca_esperada = (entrada_el.astimezone(dt.UTC) + dt.timedelta(days=1)).date()

    # Un ajuste de la reduccion confirmado DOS dias despues (misma baja, el
    # dueño edito la reduccion).
    _cambio_confirmado(
        conn,
        kw1,
        ids["config_id"],
        instante=entrada_el + dt.timedelta(hours=48),
        antes="5.60",
        despues="4.50",
        motivo="ajusta_temporada_baja",
        episodio_id=e1,
    )

    estado = temporada_io.lee_estado(conn, "amazon_mx", D_E1 + dt.timedelta(days=5))
    assert estado.cerca_plataforma == cerca_esperada, (
        f"cerca_plataforma {estado.cerca_plataforma} != la de la entrada {cerca_esperada}: "
        "el ajuste no reinicia la cerca"
    )


# ---------------------------------------------------------------------------
# 3. test_temporada01_b_fin_de_reduccion (escribe en produccion): casos 1-9.
# ---------------------------------------------------------------------------


def test_temporada01_b_fin_de_reduccion(db_template):
    """Los casos 1 a 9 de la fila B.2, con base, hasta la confirmacion y el
    ciclo siguiente."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    config_id = ids["config_id"]

    # Hojas de los casos (todas del mismo ad group y episodios por plataforma):
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw caso 2", "8.00")  # casos 2 y 7
    kw2 = _kw(conn, ids["ag"], "b-kw2", "kw caso 3a", "8.00")  # caso 3a (mano el mismo dia)
    kw3 = _kw(conn, ids["ag"], "b-kw3", "kw caso 3b", "5.00")  # caso 3b (mano ya registrada)
    kw4 = _kw(conn, ids["ag"], "b-kw4", "kw caso 4", "3.00")  # caso 4 (inerte) y 7a
    kw5 = _kw(conn, ids["ag"], "b-kw5", "kw caso 8", "8.00")  # caso 8
    kw_a = _kw(conn, ids["ag"], "b-c1", "kw caso 1", "10.00")  # caso 1
    _kw10 = _kw(conn, ids["ag"], "b-c7b", "kw caso 7b", "3.00")  # caso 7b
    hojas_e1 = [kw1, kw2, kw3, kw5, kw_a, _kw10]
    _siembra_metricas(conn, hojas_e1)
    # kw4 queda INERTE desde antes de E1 (sin metricas).
    conn.execute(
        "DELETE FROM ads_metric_observation WHERE ad_entity_id = %s AND metric_date >= %s",
        (kw4, dt.date(2026, 12, 20)),
    )
    # - caso 5a: la reactivacion manual de la manana del dia de apertura de E2.
    kw7 = _kw(conn, ids["ag"], "b-c5a", "kw caso 5a", "9.00")
    _siembra_metricas(conn, [kw7])
    _reactivacion_manual(conn, kw7, dt.datetime(2027, 5, 3, 8, 0, tzinfo=dt.UTC))
    # - caso 5b: la reactivacion manual del dia ANTERIOR (mes normal).
    kw8 = _kw(conn, ids["ag"], "b-c5b", "kw caso 5b", "9.00")
    _siembra_metricas(conn, [kw8])
    _reactivacion_manual(conn, kw8, dt.datetime(2027, 5, 2, 9, 0, tzinfo=dt.UTC))

    # La historia PREVIA a E1 se ancla al reloj REAL menos horas (los ciclos
    # confirman con now() de la base): asi la cadena queda en orden sin
    # depender de la hora de la corrida.
    def _ahora_menos(horas: int) -> dt.datetime:
        return dt.datetime.now(dt.UTC) - dt.timedelta(hours=horas)

    # - caso 1: R3 dejo 20 -> 10 (motor, antes de la entrada).
    _cambio_confirmado(
        conn,
        kw_a,
        config_id,
        instante=_ahora_menos(6),
        antes="20.00",
        despues="10.00",
        motivo="pierde_dinero",
    )
    # - casos 4/7a: kw4 y kw10 tuvieron un recorte del motor (5 -> 4) y el
    #   dueno puso 3.00 a mano (cambio SIN registro): el bid vigente ya no es
    #   el ultimo de Orbit.
    for hoja in (kw4, _kw10):
        _cambio_confirmado(
            conn,
            hoja,
            config_id,
            instante=_ahora_menos(12),
            antes="5.00",
            despues="4.00",
            motivo="pierde_dinero",
        )

    remoto = {
        "b-kw1": "8.00",
        "b-kw2": "8.00",
        "b-kw3": "5.00",
        "b-kw4": "3.00",
        "b-kw5": "8.00",
        "b-c1": "10.00",
        "b-c7b": "3.00",
        "b-c5a": "9.00",
        "b-c5b": "9.00",
    }
    handler = _handler_mx(remoto)

    # ================= E1 (4 ene, temporada live): episodio abre; las hojas
    # con bid propio entran 30 %; kw4 (inerte) y la hoja del caso 7b
    # (dueno_al_final) NO reciben entrada y sus manos quedan excluidas de E1
    # con bid_visto (casos 4b y 7a).
    res_e1 = _corre(conn, D_E1, handler)
    assert res_e1.status == "done"
    e1 = conn.execute("SELECT id FROM temporada_episodio WHERE cerrado_el IS NULL").fetchone()[0]
    assert _temporada_del_ciclo(conn, res_e1.cycle_id), "las hojas entraron en E1"
    entradas_e1 = {row[0]: row[1] for row in _temporada_del_ciclo(conn, res_e1.cycle_id)}
    assert entradas_e1[kw_a] == Decimal("7.00") and entradas_e1[kw1] == Decimal("5.60")
    assert all(k not in entradas_e1 for k in (kw4, _kw10))

    # La historia POSTERIOR a E1 se ancla al CONFIRMADO REAL de la entrada de
    # kw1 (los ciclos reales confirman con now() de la base, hoy): los cambios
    # posteriores van con horas DESPUES, para que la cadena quede en orden.
    entrada_kw1_el = conn.execute(
        "SELECT da.confirmed_at FROM decision d"
        " JOIN decision_application da ON da.decision_id = d.id"
        " WHERE d.ad_entity_id = %s AND d.inputs->>'motivo' = 'entra_temporada_baja'"
        "   AND d.inputs->>'temporada_episodio_id' = %s",
        (kw1, str(e1)),
    ).fetchone()[0]

    def _rel(horas: int) -> dt.datetime:
        return entrada_kw1_el + dt.timedelta(hours=horas)

    assert conn.execute(
        "SELECT motivo, bid_visto FROM temporada_exclusion WHERE ad_entity_id = %s"
        " AND episodio_id = %s",
        (kw4, e1),
    ).fetchone() == ("mano_del_dueno", Decimal("3.00")), "exclusion de la inerte (caso 4)"
    assert conn.execute(
        "SELECT bid_visto, decision_id FROM temporada_exclusion WHERE ad_entity_id = %s"
        " AND episodio_id = %s",
        (_kw10, e1),
    ).fetchone() == (Decimal("3.00"), None), "exclusion con bid_visto sin decision (7a)"
    skips = json.loads(res_e1.notes).get("skips", {}).get("entidad", {})
    assert skips and any("inerte" in str(m) for m in skips), skips

    # ================= Caso 1: R6 sube 7 -> 14 (historia confirmada del
    # motor; la exactitud de R6 la cubren las pruebas de politica).
    _cambio_confirmado(
        conn,
        kw_a,
        config_id,
        instante=_rel(6),
        antes="7.00",
        despues="14.00",
        motivo="bajo_target",
    )
    conn.execute("UPDATE ad_entity_state SET current_bid = 14.00 WHERE ad_entity_id = %s", (kw_a,))
    remoto["b-c1"] = "14.00"  # el mock de Amazon espeja la historia confirmada

    # ================= Caso 2 (apagado): la temporada se apaga antes de la
    # salida natural de E1: el regreso de kw1 queda RETENIDO y E1 sigue
    # abierto. El ciclo del 1 feb corre sin temporada y no escribe nada.
    _config_version(conn, _settings(None))
    res_apagado = _corre(conn, D_SALE_E1, handler)
    assert res_apagado.status == "done"
    assert _temporada_del_ciclo(conn, res_apagado.cycle_id) == []
    assert _bid_cache(conn, kw1) == Decimal("5.60"), "regreso retenido"
    assert (
        conn.execute("SELECT cerrado_el FROM temporada_episodio WHERE id = %s", (e1,)).fetchone()[0]
        is None
    )

    # Caso 8 (historia): con la temporada apagada Orbit recorto
    # 5.60 -> 4.20 -> 3.15 (historia confirmada del motor).
    _cambio_confirmado(
        conn,
        kw5,
        config_id,
        instante=_rel(12),
        antes="5.60",
        despues="4.20",
        motivo="pierde_dinero",
    )
    _cambio_confirmado(
        conn,
        kw5,
        config_id,
        instante=_rel(18),
        antes="4.20",
        despues="3.15",
        motivo="pierde_dinero",
    )
    conn.execute("UPDATE ad_entity_state SET current_bid = 3.15 WHERE ad_entity_id = %s", (kw5,))
    remoto["b-kw5"] = "3.15"
    # Caso 7b: Orbit movio la hoja DESPUES de la mano sin registro (mes
    # normal): la discontinuidad del recorte FECHA la mano el 15 de abril.
    _cambio_confirmado(
        conn,
        _kw10,
        config_id,
        instante=_rel(30),
        antes="3.00",
        despues="2.10",
        motivo="pierde_dinero",
    )
    conn.execute("UPDATE ad_entity_state SET current_bid = 2.10 WHERE ad_entity_id = %s", (_kw10,))
    remoto["b-c7b"] = "2.10"
    _config_version(conn, _settings())  # re-encender para E2

    # Caso 1 (continua): R6 sube 14 -> 16.10 (historia confirmada del motor,
    # durante la alta de marzo-abril).
    _cambio_confirmado(
        conn,
        kw_a,
        config_id,
        instante=_rel(24),
        antes="14.00",
        despues="16.10",
        motivo="bajo_target",
    )
    conn.execute("UPDATE ad_entity_state SET current_bid = 16.10 WHERE ad_entity_id = %s", (kw_a,))
    remoto["b-c1"] = "16.10"
    # ================= E2 (3 may, temporada live): un episodio de otra baja
    # (E1) se cierra y se abre E2 (caso 8). Entradas de la baja; kw2 con la
    # mano del dueno el MISMO dia (caso 3a): sin entrada y exclusion de E2 con
    # bid_visto; kw10 (mano fechada en abril): la entrada SI sale (caso 7b).
    conn.execute("UPDATE ad_entity_state SET current_bid = 7.00 WHERE ad_entity_id = %s", (kw2,))
    remoto["b-kw2"] = "7.00"
    res_e2 = _corre(conn, D_E2, handler)
    assert res_e2.status == "done"
    e2 = _episodio_abierto(conn)
    assert e2 != e1
    assert (
        conn.execute(
            "SELECT count(*) FROM temporada_episodio WHERE id = %s AND cerrado_el IS NOT NULL",
            (e1,),
        ).fetchone()[0]
        == 1
    ), "E1 de otra baja se cerro al abrir E2 (caso 8)"
    assert (
        conn.execute("SELECT count(*) FROM temporada_episodio WHERE cerrado_el IS NULL").fetchone()[
            0
        ]
        == 1
    )
    # Caso 1: al salir E1 kw_a NO recibe regreso (ya_arriba, bid 16.10 >= su
    # nivel 10), el fin queda guardado y la entrada de E2 da 11.27
    # (16.10 x 0.70) estampada en E2; confirmada, el ciclo siguiente no decide
    # otra entrada.
    assert conn.execute(
        "SELECT motivo FROM temporada_fin_reduccion WHERE ad_entity_id = %s AND episodio_id = %s",
        (kw_a, e1),
    ).fetchone() == ("ya_arriba",)
    entrada_c1 = conn.execute(
        "SELECT d.new_value, da.verify_ok FROM decision d"
        " JOIN decision_application da ON da.decision_id = d.id"
        " WHERE d.ad_entity_id = %s AND d.inputs->>'motivo' = 'entra_temporada_baja'"
        "   AND d.inputs->>'temporada_episodio_id' = %s",
        (kw_a, str(e2)),
    ).fetchone()
    assert entrada_c1 == (Decimal("11.27"), True), entrada_c1
    res_e2c = _corre(conn, dt.date(2027, 5, 5), handler)
    assert res_e2c.status == "done"
    assert all(k != kw_a for k, _d, _m, _e in _temporada_del_ciclo(conn, res_e2c.cycle_id))
    # Caso 3a (kw2): sin entrada el dia de la apertura y exclusion guardada.
    assert all(k != kw2 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_e2.cycle_id))
    assert conn.execute(
        "SELECT motivo, bid_visto FROM temporada_exclusion WHERE ad_entity_id = %s"
        " AND episodio_id = %s",
        (kw2, e2),
    ).fetchone() == ("mano_del_dueno", Decimal("7.00"))
    # Caso 7b (la hoja del recorte fechado): la entrada sale con la mano ya
    # vieja: 2.10 x 0.70 = 1.47.
    entradas_7b = [row for row in _temporada_del_ciclo(conn, res_e2.cycle_id) if row[0] == _kw10]
    assert entradas_7b and entradas_7b[0][1] == Decimal("1.47"), entradas_7b
    # Caso 5a (kw7): la reactivacion manual de la manana de la apertura no
    # recibe entrada ese dia ni el siguiente.
    assert all(k != kw7 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_e2.cycle_id))
    assert all(k != kw7 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_e2c.cycle_id))
    # Caso 5b (kw8): la reactivacion del dia anterior (mes normal) SI deja la
    # entrada: 9 x 0.70 = 6.30.
    entradas_5b = [row for row in _temporada_del_ciclo(conn, res_e2.cycle_id) if row[0] == kw8]
    assert entradas_5b == [(kw8, Decimal("6.30"), "entra_temporada_baja", str(e2))], entradas_5b

    # ================= Caso 2 (continua): el dueno pone 7 hoy (4 may) a kw1:
    # no hay entrada, se guarda la exclusion de E2 con bid_visto y el bid
    # sigue en 7. Caso 3b (kw3): con la mano YA REGISTRADA hoy (exclusion de
    # la mano sembrada el dia de la apertura) tampoco hay entrada el dia
    # siguiente ni el posterior, aunque el bid vigente ya coincida con el
    # bid_visto (dueno_al_final False).
    conn.execute("UPDATE ad_entity_state SET current_bid = 7.00 WHERE ad_entity_id = %s", (kw1,))
    remoto["b-kw1"] = "7.00"
    _mano(conn, e2, kw3, dt.datetime(2027, 5, 3, 9, 30, tzinfo=dt.UTC), "7.00")
    conn.execute("UPDATE ad_entity_state SET current_bid = 7.00 WHERE ad_entity_id = %s", (kw3,))
    remoto["b-kw3"] = "7.00"
    res_5 = _corre(conn, dt.date(2027, 5, 6), handler)
    assert res_5.status == "done"
    assert all(k != kw1 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_5.cycle_id))
    assert conn.execute(
        "SELECT motivo, bid_visto FROM temporada_exclusion WHERE ad_entity_id = %s"
        " AND episodio_id = %s",
        (kw1, e2),
    ).fetchone() == ("mano_del_dueno", Decimal("7.00"))
    assert _bid_cache(conn, kw1) == Decimal("7.00")
    res_6 = _corre(conn, dt.date(2027, 5, 7), handler)
    assert res_6.status == "done"
    assert all(k != kw3 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_6.cycle_id))
    assert all(k != kw1 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_6.cycle_id))

    # ================= Caso 6: en practica no se escribe ningun fin ni
    # exclusion (modo shadow de temporada, ciclo live).
    antes = (
        conn.execute("SELECT count(*) FROM temporada_fin_reduccion").fetchone()[0],
        conn.execute("SELECT count(*) FROM temporada_exclusion").fetchone()[0],
        conn.execute("SELECT count(*) FROM temporada_efecto").fetchone()[0],
    )
    _config_version(conn, _settings("shadow"))
    res_sombra = _corre(conn, D_SOMBRA, handler)
    assert res_sombra.status == "done"
    despues = (
        conn.execute("SELECT count(*) FROM temporada_fin_reduccion").fetchone()[0],
        conn.execute("SELECT count(*) FROM temporada_exclusion").fetchone()[0],
        conn.execute("SELECT count(*) FROM temporada_efecto").fetchone()[0],
    )
    assert despues == antes, (antes, despues)
    _config_version(conn, _settings())

    # ================= Salida de E2 (1 jun): E2 cierra. La mano de kw1/kw2
    # mata la reduccion de E1 con un FIN (mano del dueno, sin regreso); kw5
    # (caso 8) regresa a 4.50 (8 x 3.15 / 5.60), no a 8.00.
    res_salida = _corre(conn, D_SALE_E2, handler)
    assert res_salida.status == "done"
    assert _fin_de(conn, kw1, e1) == "mano_del_dueno"
    assert _fin_de(conn, kw2, e1) == "mano_del_dueno"
    regreso_8 = [row for row in _temporada_del_ciclo(conn, res_salida.cycle_id) if row[0] == kw5]
    assert regreso_8 and regreso_8[0][1] == Decimal("4.50"), regreso_8
    assert regreso_8[0][2] == "sale_temporada_baja"
    assert (
        conn.execute("SELECT cerrado_el FROM temporada_episodio WHERE id = %s", (e2,)).fetchone()[0]
        is not None
    )

    # ================= Caso 7: en la siguiente baja (E3, 1 jul) la mano de E2
    # ya cuenta como vieja: kw1 y kw2 reciben la entrada sobre el bid del
    # dueno (7 x 0.70 = 4.90).
    res_e3 = _corre(conn, D_E3, handler)
    assert res_e3.status == "done"
    e3 = _episodio_abierto(conn)
    for hoja in (kw1, kw2):
        entradas = [row for row in _temporada_del_ciclo(conn, res_e3.cycle_id) if row[0] == hoja]
        assert entradas == [(hoja, Decimal("4.90"), "entra_temporada_baja", str(e3))], (
            hoja,
            entradas,
        )

    # ================= Caso 9: Orbit pauso la keyword en la baja (en el ciclo
    # de E3) y el dueno la reactivo el dia 10: el dia 11 no hay entrada,
    # porque la reactivacion manual es su mano, fechada.
    kw9 = _kw(conn, ids["ag"], "b-c9", "kw caso 9", "6.00")
    _siembra_metricas(conn, [kw9])
    conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, %s, NULL, NULL, NULL)",
        (
            res_e3.cycle_id,
            kw9,
            _medianoche(D_E3),
            config_id,
            _medianoche(D_E3),
            D_E3 - dt.timedelta(days=90),
            D_E3 - dt.timedelta(days=10),
            Json({"motor": "bid", "motivo": "gasto_sin_venta"}),
        ),
    )
    conn.execute(
        "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack,"
        " verify_ok) SELECT id, now(), '{}', true FROM decision WHERE ad_entity_id = %s"
        " AND kind = 'pause'",
        (kw9,),
    )
    _reactivacion_manual(conn, kw9, dt.datetime(2027, 7, 10, 8, 0, tzinfo=dt.UTC))
    res_c9 = _corre(conn, D_C9, handler)
    assert res_c9.status == "done"
    assert all(k != kw9 for k, _d, _m, _e in _temporada_del_ciclo(conn, res_c9.cycle_id))


# ---------------------------------------------------------------------------
# 4. La migracion: sonda, permisos, triggers solo-cierre y reversas.
# ---------------------------------------------------------------------------


def test_temporada01_b_rango_sin_centavos_no_tumba_plataforma(db_template):
    """Revision: un goal con rango SIN ningun centavo factible (5.6050 a
    5.6090) no tumba el ciclo al salir la baja: la hoja con el rango roto deja
    su regreso pendiente (dato faltante, sin fin falso) y OTRA hoja de la
    plataforma sigue produciendo decisiones."""
    conn, _dsn = db_template
    ids = _escenario(conn)

    # Camp B con goal de campana de rango roto (5.6050 a 5.6090, cuatro
    # decimales: la validacion de goals los acepta).
    camp_b = _entidad(conn, "amazon_mx", "campaign", "b-camp-rota")
    ag_b = _entidad(conn, "amazon_mx", "ad_group", "b-ag-roto", parent=camp_b)
    _estado(conn, ag_b, synced_at=_SYNC)
    _estado(conn, camp_b, synced_at=_SYNC)
    kw_rota = _kw(conn, ag_b, "b-kw-rota", "kw rango roto", "8.00")
    kw_buena = _kw(conn, ids["ag"], "b-kw-buena", "kw rango sano", "8.00")
    _siembra_metricas(conn, [kw_rota, kw_buena])

    remoto = {"b-kw-rota": "8.00", "b-kw-buena": "8.00"}
    handler = _handler_mx(remoto)
    res = _corre(conn, D_E1, handler)
    assert res.status == "done"
    e1 = _episodio_abierto(conn)
    entradas = {row[0]: row[1] for row in _temporada_del_ciclo(conn, res.cycle_id)}
    assert entradas.get(kw_rota) == Decimal("5.60") and entradas.get(kw_buena) == Decimal("5.60")

    # El dueno guarda el rango SIN centavos factibles a MEDIA baja (la
    # validacion de goals acepta cuatro decimales).
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode)"
        " VALUES ('campaign', %s, 20, 5.6050, 5.6090, 'MXN', '9002', '9102',"
        " 1.00, true, 'live')",
        (camp_b,),
    )

    # Salida de la baja: kw_buena regresa a 8.00; kw_rota (rango roto) deja su
    # regreso pendiente SIN fin falso, y el ciclo corre completo.
    handler_out = _handler_mx({"b-kw-rota": "5.60", "b-kw-buena": "5.60"})
    res = _corre(conn, D_SALE_E1, handler_out)
    assert res.status == "done", res.notes
    salidas = {row[0]: row[1] for row in _temporada_del_ciclo(conn, res.cycle_id)}
    assert salidas.get(kw_buena) == Decimal("8.00"), "la hoja sana regresa"
    assert kw_rota not in salidas, "la hoja con rango roto no recibe regreso imposible"
    assert (
        conn.execute(
            "SELECT count(*) FROM temporada_fin_reduccion WHERE ad_entity_id = %s", (kw_rota,)
        ).fetchone()[0]
        == 0
    ), "sin fin falso para la hoja con rango roto"
    assert (
        conn.execute("SELECT cerrado_el FROM temporada_episodio WHERE id = %s", (e1,)).fetchone()[0]
        is not None
    )


def test_temporada01_b_migracion_sonda_y_permisos():
    """La sonda es UNA linea dentro de las primeras 10 y empieza con el CASE de
    temporada_episodio (patron 0081); MOTIVOS_SIN_APLICAR suma
    bid_cambiado_fuera y el CHECK de la migracion lo espeja."""
    lineas = (
        (RAIZ / "migrations" / "0093_temporada01_episodio.sql")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    sondas = [
        (i, line)
        for i, line in enumerate(lineas[:10], start=1)
        if line.startswith("-- sonda-aplicada:")
    ]
    assert len(sondas) == 1, sondas
    assert sondas[0][1].removeprefix("-- sonda-aplicada:").strip() == SONDA_0093
    assert "\n" not in SONDA_0093
    assert SONDA_0093.startswith(
        "SELECT CASE WHEN to_regclass('public.temporada_episodio') IS NULL THEN false ELSE"
    )
    assert MOTIVOS_SIN_APLICAR[-1] == "bid_cambiado_fuera"
    bloque = (
        (RAIZ / "migrations" / "0093_temporada01_episodio.sql")
        .read_text(encoding="utf-8")
        .split("ADD CONSTRAINT decision_sin_aplicar_motivo_check", 1)[1]
        .split("motivo IN (", 1)[1]
        .split("))", 1)[0]
    )
    assert tuple(re.findall(r"'([a-z_]+)'", bloque)) == MOTIVOS_SIN_APLICAR


def test_temporada01_b_migracion_reversa(db_template):
    """La reversa aborta con episodios; limpia, restaura la vista de 0067
    (7 columnas) y el CHECK sin bid_cambiado_fuera; los triggers solo-cierre
    rechazan cualquier UPDATE que no cierre y las tablas de fin/exclusion son
    append-only."""
    conn, _dsn = db_template
    assert conn.execute(SONDA_0093).fetchone()[0] is True

    config_id = conn.execute(
        "INSERT INTO config_version (settings) VALUES ('{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status) VALUES"
        " ('live','amazon_mx','done') RETURNING id"
    ).fetchone()[0]
    ep = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', now(), %s, %s) RETURNING id",
        (ciclo, config_id),
    ).fetchone()[0]
    hoja = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES ('amazon_mx', 'campaign', 'b-rev') RETURNING id"
    ).fetchone()[0]
    conn.commit()  # la siembra sobrevive los rollbacks de los casos de error
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE temporada_episodio SET abierto_el = abierto_el WHERE id = %s", (ep,))
    conn.rollback()
    conn.execute(
        "UPDATE temporada_episodio SET cerrado_el = now(), cerrado_en_ciclo = %s WHERE id = %s",
        (ciclo, ep),
    )
    conn.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE temporada_episodio SET cerrado_el = now() WHERE id = %s", (ep,))
    conn.rollback()
    # Append-only por trigger: el DELETE rechaza (con una fila; sin filas el
    # trigger FOR EACH ROW ni dispara). La exclusion va sin commit: el
    # rollback la deshace y el episodio queda limpio para la reversa final.
    conn.execute(
        "INSERT INTO temporada_exclusion (episodio_id, ad_entity_id, motivo)"
        " VALUES (%s, %s, 'mano_del_dueno')",
        (ep, hoja),
    )
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("DELETE FROM temporada_exclusion")
    conn.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match="episodios de temporada"):
        conn.execute(REVERSA_0093)
    conn.rollback()
    conn.execute("DELETE FROM temporada_episodio WHERE id = %s", (ep,))
    conn.commit()
    conn.execute(REVERSA_0093)
    assert conn.execute(SONDA_0093).fetchone()[0] is False
    assert (
        conn.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'v_cambio_bid'"
        ).fetchone()[0]
        == 7
    )
    _, defin = conn.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'decision_sin_aplicar'::regclass AND contype = 'c'"
        "   AND conname = 'decision_sin_aplicar_motivo_check'"
    ).fetchone()
    assert "bid_cambiado_fuera" not in defin


def test_temporada01_b_sin_sello_sin_bid_reintenta(db_template):
    """Revision nativa (el flujo COMIENZA sin sello): la caida deja el intento
    SIN sello y la primera lectura de reconciliacion no trae la fila de la
    hoja (sin puja legible): NO concluyente. El efecto queda ABIERTO, sin
    sello y sin resumen (la hoja no genera otra entrada sobre el bid ya
    reducido); el ciclo siguiente, con la fila legible, confirma la entrada
    original con GET fresco."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    remoto: dict[str, str] = {}
    handler = _handler_mx(remoto)
    amb = _siembra_ambigua(conn, ids, kw1, cache="5.60", sellado=False)
    # El intento COMIENZA SIN sello (el crash fue entre el PUT y el sello) y
    # el cache ya quedo sincronizado al destino.

    # Ciclo 1: la primera lectura NO trae la fila: el intento queda SIN sello
    # y el efecto ABIERTO, sin resumen y sin decision_sin_aplicar (la hoja no
    # genera otra entrada sobre el bid ya reducido).
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    assert (
        conn.execute(
            "SELECT finished_at FROM apply_attempt WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        is None
    ), "el intento queda sin sello (pendiente)"
    assert (
        conn.execute(
            "SELECT cerrado_el FROM temporada_efecto WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        is None
    ), "sin lectura concluyente: el efecto queda abierto"
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_application WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_sin_aplicar WHERE decision_id = %s", (amb["dec"],)
        ).fetchone()[0]
        == 0
    )
    assert _temporada_del_ciclo(conn, res.cycle_id) == []

    # Ciclo 2: la fila aparece con el DESTINO: la lectura confirma la entrada
    # original con GET fresco (jamás otro recorte 5.60 -> 3.92).
    remoto["b-kw1"] = "5.60"
    res = _corre(conn, D_E1 + dt.timedelta(days=2), handler)
    assert res.status == "done", res.notes
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision_application da WHERE da.decision_id = %s",
        (amb["dec"],),
    ).fetchone()
    assert resumen == (True,), resumen
    assert conn.execute(
        "SELECT desenlace FROM temporada_efecto WHERE decision_id = %s", (amb["dec"],)
    ).fetchone() == ("confirmado",), "la reduccion queda confirmada"
    assert _bid_cache(conn, kw1) == Decimal("5.60")
    doble = [
        row
        for row in _temporada_del_ciclo(conn, res.cycle_id)
        if row[0] == kw1 and row[1] == Decimal("3.92")
    ]
    assert doble == [], "jamás otro recorte 5.60 -> 3.92"


def test_temporada01_b_bid_ordinario_ambiguo_bloqueado(db_template):
    """Revision nativa: la guarda del ciclo incluye TAMBIEN los bids
    ORDINARIOS con sellado ambiguo sin desenlace (no solo temporada): con el
    cache ya sincronizado al destino, TX2 SALTA la hoja (bid_en_vuelo) en vez
    de crear una decision obsoleta que pisaria el valor externo, y la fase de
    apply confirma la decision original con GET fresco."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw1", "kw 1", "8.00")
    _siembra_metricas(conn, [kw1])
    _evidencia_recorte(conn, kw1)
    remoto = {"b-kw1": "5.60"}
    handler = _handler_mx(remoto)
    config_id = ids["config_id"]

    # Decision ORDINARIA (sin temporada) 8 -> 5.60 SIN resumen, su intento
    # SELLADO con lectura ambigua, y el cache ya sincronizado a 5.60.
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s) RETURNING id",
        (_medianoche(D_E1), _medianoche(D_E1)),
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs, old_value, new_value,"
        " value_currency)"
        " VALUES (%s,%s,'bid',%s,%s,%s,%s,%s,%s,8.00,5.60,'MXN') RETURNING id",
        (
            ciclo_id,
            kw1,
            _medianoche(D_E1),
            config_id,
            _medianoche(D_E1),
            D_E1 - dt.timedelta(days=90),
            D_E1 - dt.timedelta(days=10),
            Json({"motor": "bid", "motivo": "pierde_dinero"}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " started_at, ack, resultado, finished_at)"
        " VALUES (%s, 1, 'normal', '{}', true, %s, NULL, 'fallo:readback_ambiguo', now())",
        (dec, _medianoche(D_E1)),
    )

    # Ciclo con la conciliacion pendiente: la guardsa ampliada cubre la hoja
    # ORDINARIA (sin efecto de temporada): TX2 la salta con bid_en_vuelo y la
    # fase de apply confirma la decision original (5.60 == destino).
    res = _corre(conn, D_E1 + dt.timedelta(days=1), handler)
    assert res.status == "done", res.notes
    skips = json.loads(res.notes).get("skips", {}).get("entidad", {})
    assert skips.get("bid_en_vuelo") == 1, skips
    nueva = conn.execute(
        "SELECT new_value FROM decision WHERE ad_entity_id = %s AND cycle_id = %s AND kind = 'bid'",
        (kw1, res.cycle_id),
    ).fetchall()
    assert nueva == [], nueva
    resumen = conn.execute(
        "SELECT da.verify_ok FROM decision_application da WHERE da.decision_id = %s",
        (dec,),
    ).fetchone()
    assert resumen == (True,), resumen
    assert _bid_cache(conn, kw1) == Decimal("5.60")


def test_shadow_global_no_abre_temporada_efecto_ni_falla_segundo_ciclo(db_template):
    """Hallazgo #1: con ads_optimizer_mode='shadow' y temporada='live' sobre un
    episodio abierto previo, TX3 no abre temporada_efecto y dos ciclos seguidos
    terminan en done sin chocar temporada_efecto_uno_pendiente."""
    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw-shadow", "kw shadow", "8.00")
    _siembra_metricas(conn, [kw1])

    ciclo_previo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live', 'amazon_mx', 'done', %s, %s) RETURNING id",
        (_medianoche(D_E1), _medianoche(D_E1)),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s)",
        (_medianoche(D_E1), ciclo_previo, ids["config_id"]),
    )
    settings_shadow = {**_settings("live"), "ads_optimizer_mode": "shadow"}
    _config_version(conn, settings_shadow)
    conn.commit()

    def _sin_http(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"shadow global no debe llamar HTTP: {request.method} {request.url}")

    res1 = _corre(conn, D_E1 + dt.timedelta(days=1), _sin_http)
    res2 = _corre(conn, D_E1 + dt.timedelta(days=2), _sin_http)
    assert res1.status == "done", res1.notes
    assert res2.status == "done", res2.notes
    abiertos = conn.execute("SELECT count(*) FROM temporada_efecto").fetchone()[0]
    assert abiertos == 0, f"shadow global no debe abrir temporada_efecto, hubo {abiertos}"


def test_estampa_no_contamina_decision_normal_con_episodio():
    """Hallazgo #12: temporada_io.estampa solo sella temporada_episodio_id en
    decisiones con motivo de temporada o pausas con fase_hoja == 'baja'."""
    ahora = _medianoche(D_E1)

    def _pend(kind: str, inputs: dict) -> ciclo._Pendiente:
        return ciclo._Pendiente(
            ad_entity_id=1,
            kind=kind,
            data_observed_at=ahora,
            window_start=D_E1,
            window_end=D_E1,
            search_term=None,
            old_value=Decimal("8.00") if kind == "bid" else None,
            new_value=Decimal("9.00") if kind == "bid" else None,
            value_currency="MXN" if kind == "bid" else None,
            inputs=inputs,
        )

    bid_normal = temporada_io.estampa(_pend("bid", {"motor": "bid", "motivo": "bajo_target"}), 42)
    assert "temporada_episodio_id" not in bid_normal.inputs

    pausa_normal = temporada_io.estampa(
        _pend(
            "pause",
            {
                "motor": "bid",
                "motivo": "pause_economica",
                "caso": {"temporada": {"frontera": {"fase_hoja": "normal"}}},
            },
        ),
        42,
    )
    assert "temporada_episodio_id" not in pausa_normal.inputs

    pausa_sin_temp = temporada_io.estampa(
        _pend("pause", {"motor": "bid", "motivo": "pause_economica", "caso": {"temporada": None}}),
        42,
    )
    assert "temporada_episodio_id" not in pausa_sin_temp.inputs

    bid_entra = temporada_io.estampa(
        _pend("bid", {"motor": "bid", "motivo": "entra_temporada_baja"}), 42
    )
    assert bid_entra.inputs["temporada_episodio_id"] == 42

    bid_sale_prev = temporada_io.estampa(
        _pend(
            "bid",
            {"motor": "bid", "motivo": "sale_temporada_baja", "temporada_episodio_id": 7},
        ),
        42,
    )
    assert bid_sale_prev.inputs["temporada_episodio_id"] == 7

    pausa_baja = temporada_io.estampa(
        _pend(
            "pause",
            {
                "motor": "bid",
                "motivo": "pause_economica",
                "caso": {"temporada": {"frontera": {"fase_hoja": "baja"}}},
            },
        ),
        42,
    )
    assert pausa_baja.inputs["temporada_episodio_id"] == 42


def test_historia_temporada_bitemporal_filtra_manos_y_fines_futuros(db_template):
    """Hallazgo #10a: lee_plataforma(..., visto_el=...) filtra temporada_exclusion,
    reactivacion_manual y temporada_fin_reduccion con timestamps > visto_el."""
    from app.lecturas_caso import lee_plataforma

    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw-bit", "kw bit", "5.60")
    _siembra_metricas(conn, [kw1])

    t_entra = _medianoche(D_E1)
    visto_el = t_entra + dt.timedelta(hours=1)
    t_futuro = visto_el + dt.timedelta(hours=2)

    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live', 'amazon_mx', 'done', %s, %s) RETURNING id",
        (t_entra, t_entra),
    ).fetchone()[0]
    ep_id = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) VALUES ('amazon_mx', %s, %s, %s) RETURNING id",
        (t_entra, ciclo_id, ids["config_id"]),
    ).fetchone()[0]
    _cambio_confirmado(
        conn,
        kw1,
        ids["config_id"],
        instante=t_entra,
        antes="8.00",
        despues="5.60",
        motivo="entra_temporada_baja",
        episodio_id=ep_id,
    )
    _mano(conn, ep_id, kw1, t_futuro, "7.50")
    _reactivacion_manual(conn, kw1, t_futuro)
    conn.execute(
        "INSERT INTO temporada_fin_reduccion (episodio_id, ad_entity_id, motivo, cycle_id,"
        " cerrado_el) VALUES (%s, %s, 'mano_del_dueno', %s, %s)",
        (ep_id, kw1, ciclo_id, t_futuro),
    )
    conn.commit()

    cfg = temporada.desde_settings(_settings("live"))
    assert cfg is not None
    dia_lect = D_E1 + dt.timedelta(days=1)
    plat_t = temporada_io.TemporadaPlataforma(
        estacion=temporada.resuelve(cfg, dia_lect),
        modo="live",
        config=cfg,
        meta_alta_pct=Decimal("25"),
        estado=temporada_io.lee_estado(conn, "amazon_mx", dia_lect),
    )
    economia = EconomiaPlataforma(
        moneda="MXN",
        equilibrio_acos_pct=Decimal("30"),
        gasto_para_concluir=Decimal("350"),
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
    )

    lect_corte = lee_plataforma(
        conn,
        "amazon_mx",
        _medianoche(dia_lect),
        economia=economia,
        temporada_plat=plat_t,
        visto_el=visto_el,
    )
    assert lect_corte._historia is not None
    hist_corte = lect_corte._historia[kw1]
    assert hist_corte.manos_externas == (), f"fugo mano futura: {hist_corte.manos_externas}"
    assert hist_corte.fines == frozenset(), f"fugo fin futuro: {hist_corte.fines}"

    # Control positivo: sin visto_el si aparecen los 3 eventos posteriores.
    lect_ahora = lee_plataforma(
        conn,
        "amazon_mx",
        _medianoche(dia_lect),
        economia=economia,
        temporada_plat=plat_t,
    )
    assert lect_ahora._historia is not None
    hist_ahora = lect_ahora._historia[kw1]
    assert {m.origen for m in hist_ahora.manos_externas} == {"exclusion", "reactivacion_manual"}
    assert ep_id in hist_ahora.fines


def test_historia_cubre_episodio_cerrado_mayor_365d_sin_fin_de_reduccion(db_template):
    """Hallazgo #10b: un episodio cerrado hace >365 dias con hoja reducida y sin
    temporada_fin_reduccion ni regreso confirmado extiende desde_historia hasta
    abierto_el y lecturas.pendiente(...) devuelve RestauracionPendiente."""
    from app.lecturas_caso import lee_plataforma

    conn, _dsn = db_template
    ids = _escenario(conn)
    kw1 = _kw(conn, ids["ag"], "b-kw-365", "kw 365", "5.60")
    _siembra_metricas(conn, [kw1])

    hoy = dt.date(2027, 2, 10)
    dia_abierto = hoy - dt.timedelta(days=400)
    dia_entra = hoy - dt.timedelta(days=399)
    dia_cerrado = hoy - dt.timedelta(days=370)

    ciclo_viejo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live', 'amazon_mx', 'done', %s, %s) RETURNING id",
        (_medianoche(dia_abierto), _medianoche(dia_abierto)),
    ).fetchone()[0]
    ep_viejo = conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id, cerrado_el, cerrado_en_ciclo)"
        " VALUES ('amazon_mx', %s, %s, %s, %s, %s) RETURNING id",
        (
            _medianoche(dia_abierto),
            ciclo_viejo,
            ids["config_id"],
            _medianoche(dia_cerrado),
            ciclo_viejo,
        ),
    ).fetchone()[0]
    _cambio_confirmado(
        conn,
        kw1,
        ids["config_id"],
        dia=dia_entra,
        antes="8.00",
        despues="5.60",
        motivo="entra_temporada_baja",
        episodio_id=ep_viejo,
    )
    conn.commit()

    cfg = temporada.desde_settings(_settings("live"))
    assert cfg is not None
    plat_t = temporada_io.TemporadaPlataforma(
        estacion=temporada.resuelve(cfg, hoy),
        modo="live",
        config=cfg,
        meta_alta_pct=Decimal("25"),
        estado=temporada_io.lee_estado(conn, "amazon_mx", hoy),
    )
    economia = EconomiaPlataforma(
        moneda="MXN",
        equilibrio_acos_pct=Decimal("30"),
        gasto_para_concluir=Decimal("350"),
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
    )

    lect = lee_plataforma(
        conn,
        "amazon_mx",
        _medianoche(hoy),
        economia=economia,
        temporada_plat=plat_t,
    )
    assert lect._historia_desde is not None and lect._historia_desde <= dia_abierto, (
        f"esperaba desde_historia <= {dia_abierto}, obtuvo {lect._historia_desde}"
    )
    pend = lect.pendiente(kw1, Decimal("5.60"), piso=_PISO, techo=_TECHO)
    assert pend == RestauracionPendiente(nivel=Decimal("8.00"), episodio_id=ep_viejo), pend

    # Si se registra fin despues de visto_el, el replay en visto_el aun cubre el episodio,
    # pero la lectura actual (sin visto_el) ya no extiende mas alla de 365d.
    visto_antes_fin = _medianoche(hoy)
    conn.execute(
        "INSERT INTO temporada_fin_reduccion (episodio_id, ad_entity_id, motivo, cycle_id,"
        " cerrado_el) VALUES (%s, %s, 'ya_arriba', %s, %s)",
        (ep_viejo, kw1, ciclo_viejo, visto_antes_fin + dt.timedelta(hours=2)),
    )
    conn.commit()
    lect_replay = lee_plataforma(
        conn,
        "amazon_mx",
        _medianoche(hoy),
        economia=economia,
        temporada_plat=plat_t,
        visto_el=visto_antes_fin,
    )
    assert lect_replay._historia_desde is not None and lect_replay._historia_desde <= dia_abierto
    assert lect_replay.pendiente(kw1, Decimal("5.60"), piso=_PISO, techo=_TECHO) == (
        RestauracionPendiente(nivel=Decimal("8.00"), episodio_id=ep_viejo)
    )
    lect_cerrado = lee_plataforma(
        conn,
        "amazon_mx",
        _medianoche(hoy),
        economia=economia,
        temporada_plat=plat_t,
    )
    assert lect_cerrado._historia_desde == hoy - dt.timedelta(days=365)
