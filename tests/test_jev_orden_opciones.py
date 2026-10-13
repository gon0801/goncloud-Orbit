"""Muestra, cupo y contrato invertido de tools/jev_orden_opciones.py."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_jev_catalogo import (
    _dsn_jev,
    _ficha,
    _listing,
    _postgres_obligatorio_ausente,
    _producto,
    _revision,
    db_jev,
)

from app.jev_ads import ClavePar, Juicio
from app.jev_juicios import FalloPar, ResultadoPar, contrato_por_defecto
from tools import jev_orden_opciones as orden
from tools.jev_orden_opciones import (
    Par,
    accion_del_par,
    aviso_muestra_congelada,
    clasificar,
    contrato_invertido,
    cupos,
    elegir_muestra,
    ids_de_fichas,
    listo_para_pagar,
    pares_de_censos,
    permite_aplicar,
)


def _pares(relacion: str, n: int, prefijo: str) -> list[Par]:
    return [Par(f"{prefijo}{i:04d}", "ficha", relacion) for i in range(n)]


def test_contrato_invertido_cambia_orden_y_sha():
    base = contrato_por_defecto()
    invertido = contrato_invertido(base)
    assert invertido.opciones == tuple(reversed(base.opciones))
    assert invertido.criterios == tuple(reversed(base.criterios))
    assert [criterio[0] for criterio in invertido.criterios] == list(invertido.opciones)
    assert invertido.sha256() != base.sha256()
    assert base.sha256() == "0fe322c8aa063997c65e101a8835e616cf00275dd022f0061e30ef227c7443e6"
    assert invertido.sha256() == "9a155982ddacdb4445fa2f3444a56a3fc6b814e9a23bef520da55753e6b62700"


def test_mitad_y_mitad_cuando_hay_de_las_dos():
    pares = _pares("satisface", 20, "s") + _pares("no_satisface", 20, "n")
    muestra = elegir_muestra(pares, 10)
    assert sum(par.relacion == "satisface" for par in muestra) == 5
    assert sum(par.relacion == "no_satisface" for par in muestra) == 5


def test_el_impar_da_el_extra_a_satisface():
    pares = _pares("satisface", 10, "s") + _pares("no_satisface", 10, "n")
    muestra = elegir_muestra(pares, 5)
    assert sum(par.relacion == "satisface" for par in muestra) == 3
    assert sum(par.relacion == "no_satisface" for par in muestra) == 2


def test_rellena_con_la_clase_que_sobra():
    pares = _pares("satisface", 3, "s") + _pares("no_satisface", 20, "n")
    muestra = elegir_muestra(pares, 10)
    assert sum(par.relacion == "satisface" for par in muestra) == 3
    assert sum(par.relacion == "no_satisface" for par in muestra) == 7


def test_no_inventa_pares_si_no_alcanzan():
    assert elegir_muestra([Par("a", "1", "satisface")], 100) == (Par("a", "1", "satisface"),)


def test_ignora_la_abstencion():
    pares = [Par("a", "1", "informacion_insuficiente"), Par("b", "1", "satisface")]
    assert elegir_muestra(pares, 10) == (Par("b", "1", "satisface"),)


def test_reparte_entre_terminos_y_el_orden_es_estable():
    pares = [Par("aa", f"{i:04d}", "satisface") for i in range(30)]
    pares += [Par(f"{i:02x}", "0000", "satisface") for i in range(1, 6)]
    muestra = elegir_muestra(pares, 10)
    assert len(muestra) == 10
    assert len({par.termino_sha256 for par in muestra}) == 6
    assert elegir_muestra(pares, 10) == muestra
    ordenados = [
        Par("bb", "2", "satisface"),
        Par("aa", "2", "satisface"),
        Par("aa", "1", "satisface"),
    ]
    assert elegir_muestra(ordenados, 3) == (
        Par("aa", "1", "satisface"),
        Par("bb", "2", "satisface"),
        Par("aa", "2", "satisface"),
    )


def test_cupos_de_cien_son_mitad_y_mitad():
    assert cupos(100, 1419, 1015) == (50, 50)


def test_permite_aplicar_solo_con_tope_agotado():
    assert permite_aplicar(50, 50) is True
    assert permite_aplicar(80, 50) is True
    assert permite_aplicar(49, 50) is False
    assert permite_aplicar(0, 0) is False
    assert permite_aplicar(10, 0) is False


def test_sin_clave_o_con_cupo_abierto_no_paga():
    assert (
        listo_para_pagar(aplicar=True, api_key="", intenciones=50, tope=50, pendientes=100)
        == "sin_api_key"
    )
    assert (
        listo_para_pagar(aplicar=True, api_key="x", intenciones=0, tope=50, pendientes=100)
        == "cupo_abierto"
    )
    assert (
        listo_para_pagar(aplicar=True, api_key="x", intenciones=0, tope=None, pendientes=100)
        == "apagado"
    )
    assert (
        listo_para_pagar(aplicar=False, api_key="", intenciones=0, tope=50, pendientes=100)
        == "seco"
    )
    assert (
        listo_para_pagar(aplicar=True, api_key="x", intenciones=0, tope=50, pendientes=0)
        == "completo"
    )
    assert (
        listo_para_pagar(aplicar=True, api_key="x", intenciones=50, tope=50, pendientes=4)
        == "pagar"
    )


def test_aviso_cuando_n_no_es_la_muestra_congelada():
    assert aviso_muestra_congelada(100, 100) is None
    assert aviso_muestra_congelada(200, 100) == "muestra congelada=100 n_pedido=200"


def test_una_intencion_abierta_no_se_vuelve_a_pagar():
    assert accion_del_par(False, False, True) == "abierta"
    assert accion_del_par(True, False, True) == "saltar"
    assert accion_del_par(True, True, False) == "saltar"
    assert accion_del_par(False, True, False) == "fallo"
    assert accion_del_par(False, False, False) == "pagar"


def test_clasificar_separa_cambio_abstencion_y_fallo():
    assert clasificar("satisface", "no_satisface", fallo=False) == "cambio"
    assert clasificar("no_satisface", "satisface", fallo=False) == "cambio"
    assert clasificar("satisface", "satisface", fallo=False) == "igual"
    assert clasificar("satisface", "informacion_insuficiente", fallo=False) == "abstencion"
    assert clasificar("satisface", None, fallo=True) == "fallo"
    assert clasificar("satisface", None, fallo=False) == "sin_respuesta"


def test_ids_de_fichas_sin_duplicados():
    pares = (
        Par("a", "f2", "satisface"),
        Par("b", "f1", "no_satisface"),
        Par("c", "f2", "satisface"),
    )
    assert ids_de_fichas(pares) == ["f1", "f2"]


def test_pares_de_censos_rechaza_otro_experimento():
    with pytest.raises(ValueError, match="orden_opciones"):
        pares_de_censos({"experimento": "otro", "pares": [{"a": 1}]})


def test_pares_de_censos_lee_la_muestra_congelada():
    censos = {
        "experimento": "orden_opciones",
        "pares": [
            {
                "termino_sha256": "aa",
                "ficha_version_id": "f1",
                "relacion_normal": "no_satisface",
            }
        ],
    }
    assert pares_de_censos(censos) == (Par("aa", "f1", "no_satisface"),)


_REQUIERE_POSTGRES = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


class _Pedido:
    """Juicio falso. crash=True simula la muerte entre la intencion y el resultado."""

    def __init__(self, relacion: str, crash: bool = False, fallo: bool = False):
        self.relacion = relacion
        self.crash = crash
        self.fallo = fallo
        self.llamados: list[str] = []

    def __call__(self, termino, ficha, contrato, api_key=""):
        self.llamados.append(termino)
        if self.crash:
            raise RuntimeError("crash tras la intencion")
        if self.fallo:
            return FalloPar(codigo="timeout", detalle="se agoto", duracion_ms=12)
        return ResultadoPar(
            juicio=Juicio(
                intento_id=uuid.uuid4(),
                clave=ClavePar("a" * 64, ficha.id, "b" * 64),
                relacion=self.relacion,
                probabilidades={
                    "satisface": Decimal("0.10"),
                    "no_satisface": Decimal("0.80"),
                    "informacion_insuficiente": Decimal("0.10"),
                },
                confidence=Decimal("0.90"),
                observado_at=datetime(2026, 10, 13, tzinfo=UTC),
            ),
            usage={"input_tokens": 5, "output_tokens": 1},
            duracion_ms=7,
        )


def _sembrar_par(conn, termino: str, relacion: str) -> None:
    producto = _producto(conn)
    listing = _listing(conn, producto)
    # El guion filtra con el reloj real. VENCE del catalogo (2026-11-02) caduca.
    ficha = _ficha(conn, producto, (listing,), vence=datetime(2099, 1, 1, tzinfo=UTC))
    solicitud = _revision(conn, censos=json.dumps({"terminos": [termino]}))
    digest = hashlib.sha256(termino.encode("utf-8")).hexdigest()
    intencion = uuid.uuid4()
    normal = contrato_por_defecto().sha256()
    conn.execute(
        "INSERT INTO jev_par_evento (id, revision_id, termino_sha256, ficha_version_id,"
        " contrato_sha256, ordinal, tipo, request_sha256)"
        " VALUES (%s, %s, %s, %s, %s, 1, 'intencion', %s)",
        (intencion, solicitud, digest, ficha, normal, "d" * 64),
    )
    conn.execute(
        "INSERT INTO jev_par_evento (id, revision_id, termino_sha256, ficha_version_id,"
        " contrato_sha256, ordinal, tipo, respuesta, intencion_id)"
        " VALUES (%s, %s, %s, %s, %s, 1, 'resultado', %s::jsonb, %s)",
        (
            uuid.uuid4(),
            solicitud,
            digest,
            ficha,
            normal,
            json.dumps({"relacion": relacion}),
            intencion,
        ),
    )


def _tope_agotado(conn) -> None:
    conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s::jsonb)",
        (json.dumps({"jev.senales": True, "jev.tope_diario": 1, "jev.min_clics": 1}),),
    )


def _resultados(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM jev_par_evento"
        " WHERE revision_id = %s AND tipo = 'resultado' AND respuesta IS NOT NULL",
        (orden.SOLICITUD,),
    ).fetchone()[0]


@_REQUIERE_POSTGRES
def test_segunda_corrida_no_vuelve_a_pagar(monkeypatch):
    pedido = _Pedido("no_satisface")
    monkeypatch.setattr(orden, "pedir_juicio", pedido)
    monkeypatch.setattr(orden, "leer_api_key", lambda: "clave-de-prueba")
    with db_jev() as conn:
        _sembrar_par(conn, "mesa", "satisface")
        _tope_agotado(conn)
        lineas: list[str] = []
        assert orden.main(["--n", "1", "--aplicar"], dsn=_dsn_jev(), imprimir=lineas.append) == 0
        assert pedido.llamados == ["mesa"]
        assert _resultados(conn) == 1
        lineas.clear()
        assert orden.main(["--n", "5", "--aplicar"], dsn=_dsn_jev(), imprimir=lineas.append) == 0
        assert pedido.llamados == ["mesa"]
        assert "muestra congelada=1 n_pedido=5" in lineas
        assert any("saltadas=1" in linea for linea in lineas)
        assert _resultados(conn) == 1


@_REQUIERE_POSTGRES
def test_intencion_abierta_no_se_repite(monkeypatch):
    pedido = _Pedido("no_satisface", crash=True)
    monkeypatch.setattr(orden, "pedir_juicio", pedido)
    monkeypatch.setattr(orden, "leer_api_key", lambda: "clave-de-prueba")
    with db_jev() as conn:
        _sembrar_par(conn, "mesa", "satisface")
        _tope_agotado(conn)
        with pytest.raises(RuntimeError, match="crash tras la intencion"):
            orden.main(["--n", "1", "--aplicar"], dsn=_dsn_jev())
        assert pedido.llamados == ["mesa"]
        assert _resultados(conn) == 0
        pedido.crash = False
        lineas: list[str] = []
        assert orden.main(["--aplicar"], dsn=_dsn_jev(), imprimir=lineas.append) == 0
        assert pedido.llamados == ["mesa"]
        assert any("abiertas=1" in linea for linea in lineas)
        assert "sin pendientes; abiertas=1 fallos=0" in lineas
        assert _resultados(conn) == 0


@_REQUIERE_POSTGRES
def test_resultado_con_error_no_se_cuenta_como_saltada(monkeypatch):
    pedido = _Pedido("no_satisface", fallo=True)
    monkeypatch.setattr(orden, "pedir_juicio", pedido)
    monkeypatch.setattr(orden, "leer_api_key", lambda: "clave-de-prueba")
    with db_jev() as conn:
        _sembrar_par(conn, "mesa", "satisface")
        _tope_agotado(conn)
        assert orden.main(["--n", "1", "--aplicar"], dsn=_dsn_jev()) == 0
        assert pedido.llamados == ["mesa"]
        lineas: list[str] = []
        assert orden.main(["--aplicar"], dsn=_dsn_jev(), imprimir=lineas.append) == 0
        assert pedido.llamados == ["mesa"]
        assert any("fallos=1" in linea and "saltadas=0" in linea for linea in lineas)
        assert "sin pendientes; abiertas=0 fallos=1" in lineas
        assert _resultados(conn) == 0
