"""Laboratorio B-D.2: el procedimiento de reparacion antes de tocar Amazon.

Usa el ledger en memoria y el transporte simulado del arnes de B-B, con el
codigo real de `bloqueo_dueno_efecto.quitar/aprobar` y las precondiciones de
reparacion. No toca base ni red. Comprueba, por caso:

  A  origen PAUSED confirmado -> revertido y reemplazo aplicado ENABLED.
  A2 reanudar el mismo origen no repite borrado ni POST.
  B  ENABLED entre inventario y accion -> cero borrados.
  C  otro ENABLED del mismo termino -> cero borrados.
  D  borrado sin relectura concluyente -> pendiente, cero POST.
  E  tabla de clasificacion del inventario.

Correr:
  PYTHONPATH=.:tests uv run --frozen --python 3.12 \
    python docs/evidencia/arreglos-04/B-D/laboratorio.py
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import test_bloqueo_dueno_efecto as tb

import app.apply as apply_mod
import app.bloqueo_dueno_efecto as bde
from app.ads.config import AdsCredentials

RAIZ = Path(__file__).resolve().parents[4]
B_D = RAIZ / "docs/evidencia/arreglos-04/B-D"


def _carga_repara():
    spec = importlib.util.spec_from_file_location("b_d_repara", B_D / "repara.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["b_d_repara"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


repara = _carga_repara()


def _credenciales() -> None:
    cred = AdsCredentials("cid", "csecret", "rtok")
    AdsCredentials.from_secrets_dir = classmethod(lambda cls, *a, **k: cred)


def _usa_transporte(transport) -> None:
    """Sustituye solo el transporte: el resto del camino es el real."""

    def cliente(plataforma, _transport):
        return apply_mod._cliente_ajuste(plataforma, transport=transport)

    bde._cliente = cliente


def _escenario(transport_factory):
    conn = tb._MemBloqueo()
    senal_vieja, grupo, grupo_ext, _campana, termino, roster = tb._prepara(conn)
    tp, vistos, store = transport_factory(grupo_ext, termino)
    _usa_transporte(tp)
    origen = bde.aprobar(conn, senal_vieja, transport=tp)
    assert origen.paso == "aplicado", origen
    kid = tb._pasos(conn, origen.veredicto_id)[-1][2]
    senal_vigente = tb._senal_ajena(
        conn,
        grupo,
        termino,
        tb.SHA,
        roster,
        plataforma="amazon_mx",
        gasto=Decimal("9.5000"),
        lote=tb._lote(conn),
        miembros=1,
    )
    vistos.clear()
    return conn, grupo, grupo_ext, termino, origen, kid, senal_vigente, tp, vistos, store


def _fila_real(vid, grupo, kid, estado, senal, otro="-"):
    return repara.Fila("amazon_mx", str(vid), grupo, kid, estado, senal, otro)


def _borrados(vistos):
    return [r for r in vistos if r.url.path == "/sp/negativeKeywords/delete"]


def _posts(vistos):
    return [r for r in vistos if r.method == "POST" and r.url.path == "/sp/negativeKeywords"]


def caso_a():
    conn, grupo, _ext, _termino, origen, kid, senal, _tp, vistos, store = _escenario(tb._transporte)
    next(x for x in store if x["keywordId"] == kid)["state"] = "PAUSED"
    paso, motivo, datos = repara._repara(
        conn, _fila_real(origen.veredicto_id, grupo, kid, "PAUSED", str(senal))
    )
    assert (paso, motivo) == ("reparado", ""), (paso, motivo)
    assert tb._pasos(conn, origen.veredicto_id)[-1][0] == "revertido"
    assert tb._pasos(conn, uuid.UUID(datos["nuevo_vid"]))[-1][0] == "aplicado"
    assert len(_borrados(vistos)) == 1 and len(_posts(vistos)) == 1, (vistos,)
    nuevo = next(x for x in store if x["keywordId"] == datos["kid_nuevo"])
    assert nuevo["state"] == "ENABLED"
    print("LAB|A|reparado_revertido_y_reemplazo_enabled|ok")
    return conn, grupo, origen, kid, senal, vistos


def caso_a2(estado_previo):
    conn, grupo, origen, kid, senal, vistos_previos = estado_previo
    antes_veredictos = len(conn.veredictos)
    paso, motivo, datos = repara._repara(
        conn, _fila_real(origen.veredicto_id, grupo, kid, "PAUSED", str(senal))
    )
    assert (paso, motivo) == ("reparado", ""), (paso, motivo)
    assert len(_borrados(vistos_previos)) == 1, "reanudar no vuelve a borrar"
    assert len(_posts(vistos_previos)) == 1, "reanudar no vuelve a postear"
    assert len(conn.veredictos) == antes_veredictos, "el reemplazo se reusa"
    print("LAB|A2|reanudar_es_idempotente|ok")


def caso_b():
    conn, grupo, _ext, _termino, origen, kid, senal, _tp, vistos, store = _escenario(tb._transporte)
    next(x for x in store if x["keywordId"] == kid)["state"] = "ENABLED"
    paso, motivo, _datos = repara._repara(
        conn, _fila_real(origen.veredicto_id, grupo, kid, "PAUSED", str(senal))
    )
    assert (paso, motivo) == ("omitido", "estado_cambio"), (paso, motivo)
    assert _borrados(vistos) == [] and _posts(vistos) == []
    print("LAB|B|enabled_entre_inventario_y_accion_cero_borrados|ok")


def caso_c():
    conn, grupo, ext, termino, origen, kid, senal, _tp, vistos, store = _escenario(tb._transporte)
    next(x for x in store if x["keywordId"] == kid)["state"] = "PAUSED"
    store.append(
        {
            "keywordId": "kid-ajeno",
            "adGroupId": ext,
            "keywordText": termino,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
    )
    paso, motivo, _datos = repara._repara(
        conn, _fila_real(origen.veredicto_id, grupo, kid, "PAUSED", str(senal))
    )
    assert (paso, motivo) == ("omitido", "otro_enabled"), (paso, motivo)
    assert _borrados(vistos) == [] and _posts(vistos) == []
    print("LAB|C|otro_enabled_cero_borrados|ok")


def caso_d():
    import json

    import httpx

    conn, grupo, ext, termino, origen, kid, senal, _tp, _vistos0, store = _escenario(tb._transporte)
    vistos: list[httpx.Request] = []
    borrado = {"si": False}

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        path = request.url.path
        if path == "/v2/profiles":
            return httpx.Response(200, json=tb._perfiles())
        if path == "/sp/negativeKeywords/list":
            if borrado["si"]:
                return httpx.Response(200, json={"negativeKeywords": "no"})
            return httpx.Response(200, json={"negativeKeywords": list(store)})
        if path == "/sp/negativeKeywords/delete":
            body = json.loads(request.content)
            objetivo = body["negativeKeywordIdFilter"]["include"][0]
            for item in store:
                if item["keywordId"] == str(objetivo):
                    item["state"] = "ARCHIVED"
            borrado["si"] = True
            return httpx.Response(207, json={"negativeKeywords": {"success": [], "error": []}})
        return httpx.Response(400, json={"message": path})

    _usa_transporte(httpx.MockTransport(handler))
    next(x for x in store if x["keywordId"] == kid)["state"] = "PAUSED"
    paso, motivo, _datos = repara._repara(
        conn, _fila_real(origen.veredicto_id, grupo, kid, "PAUSED", str(senal))
    )
    assert paso == "pendiente", (paso, motivo)
    assert motivo.startswith("quitar_reversa_intencion"), motivo
    assert tb._pasos(conn, origen.veredicto_id)[-1][0] == "reversa_intencion"
    assert len(_borrados(vistos)) == 1 and _posts(vistos) == [], "un envio, cero POST"
    print("LAB|D|borrado_dudoso_pendiente_sin_post|ok")


def caso_e():
    def fila(vid, estado, senal, otro="-"):
        return repara.Fila("amazon_mx", vid, 1, "k", estado, senal, otro)

    tabla = [
        (fila("v", "ENABLED", "s"), ("omitido", "amazon_enabled")),
        (fila("v", "PAUSED", "-"), ("omitido", "sin_senal_vigente")),
        (fila("v", "PAUSED", "s", "k2"), ("omitido", "otro_enabled_en_inventario")),
        (fila("v", "LECTURA_AMBIGUO", "-"), ("omitido", "lectura_LECTURA_AMBIGUO")),
        (fila("v", "BORRADO", "s"), ("omitido", "estado_desconocido_borrado")),
        (fila("v", "ARCHIVED", "s"), ("candidato", "")),
        (fila("v", "AUSENTE", "s"), ("candidato", "")),
    ]
    for item, esperado in tabla:
        assert repara.clasifica(item) == esperado, (item, repara.clasifica(item), esperado)
    print("LAB|E|clasificacion_inventario|ok")


def caso_f():
    valido = repara.parsea("amazon_mx|v1|1|k1|ENABLED|s|-\nreversa_fallida|r1\nFIN|1|0|1")
    assert not valido.errores, valido.errores
    assert valido.reversas == ["r1"] and valido.fin == (1, 0, 1)
    casos = {
        "duplicado": (
            "amazon_mx|v1|1|k|ENABLED|s|-\namazon_mx|v1|1|k|ENABLED|s|-\nFIN|2|0|0",
            "duplicado",
        ),
        "diagnostico": ("DUPLICADO|amazon_mx|v1\nFIN|0|0|0", "diagnostico"),
        "fin_terminal": ("FIN|0|0|0\namazon_mx|v1|1|k|ENABLED|s|-", "no terminal"),
        "sin_fin": ("amazon_mx|v1|1|k|ENABLED|s|-", "falta el recibo"),
        "conteos": ("amazon_mx|v1|1|k|ENABLED|s|-\nFIN|2|0|0", "no cuadra"),
    }
    for nombre, (texto, aguja) in casos.items():
        inv = repara.parsea(texto)
        assert any(aguja in error for error in inv.errores), (nombre, inv.errores)
    print("LAB|F|inventario_rechaza_duplicados_diagnosticos_y_fin_invalido|ok")


def caso_g():
    class _Origen:
        plataforma = "amazon_mx"
        ad_group_id = 297

    original = bde._aprobado
    bde._aprobado = lambda _conn, _vid: _Origen()
    vid = str(uuid.uuid4())
    try:
        lineas = repara._lineas_reversas(None, [vid])
    finally:
        bde._aprobado = original
    assert lineas == [f"amazon_mx|{vid}|297|omitido|reversa_fallida_sin_tocar"], lineas
    print("LAB|G|reversa_fallida_anotada_sin_tocar|ok")


def main() -> int:
    _credenciales()
    caso_e()
    caso_f()
    caso_g()
    caso_b()
    caso_c()
    caso_d()
    estado = caso_a()
    caso_a2(estado)
    print("LAB|FIN|8|0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
