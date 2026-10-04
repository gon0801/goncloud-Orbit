"""Tests de `AsesorAds.evaluar` y del CLI de lote (JEV ADS 01, 1.4).

DoD que fijan (HTTP falso y base de prueba; cero TypeSafe real):

- Revision e intencion quedan CONFIRMADAS antes del HTTP (el transporte
  falso consulta la base al invocarse) y el resultado llega tras el HTTP.
- Reanudacion tras crash: el exito previo se REUTILIZA (sin HTTP), el par
  interrumpido registra una intencion nueva con ordinal nuevo.
- La misma solicitud con otro payload se rechaza.
- Reutilizacion SOLO de exitos validos: un fallo no se reutiliza y el
  exito de OTRA revision tampoco.
- Presupuesto acotado: agotado deja estado visible y retomable.
- Jev apagado (sin clave) produce estados visibles sin una sola llamada;
  y cycle/apply_cola/apply_harvest no importan al asesor.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from test_jev_catalogo import (
    _dsn_jev,
    _ficha,
    _grupo,
    _listing,
    _postgres_obligatorio_ausente,
    _producto,
    db_jev,
)

from app.jev_ads import (
    CensoCongelado,
    DecisionARevisar,
    HayCompatible,
    Indeterminado,
    MiembroCenso,
    SemillasARevisar,
)
from app.jev_asesor import AsesorAds
from app.jev_juicios import Contrato, FalloPar, ResultadoPar, contrato_por_defecto

pytestmark = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

RAIZ = Path(__file__).resolve().parents[1]
AHORA = datetime(2026, 10, 4, tzinfo=UTC)


class _Pedido:
    """Juicio falso: tabla termino -> (relacion | fallo | crash)."""

    def __init__(self, guion=None, crash_en=None):
        self.guion = guion or {}
        self.crash_en = crash_en
        self.llamados: list[str] = []
        self.observado_antes_del_http: list[dict] = []

    def __call__(self, termino, ficha):
        self.llamados.append(termino)
        if self.crash_en is not None and termino == self.crash_en:
            raise RuntimeError("crash simulado tras la intencion")
        devuelto = self.guion.get(termino, "no_satisface")
        if isinstance(devuelto, FalloPar):
            return devuelto
        return ResultadoPar(
            juicio=self._juicio(termino, ficha, devuelto),
            usage={"input_tokens": 5},
            duracion_ms=7,
        )

    def _juicio(self, termino, ficha, relacion):
        from app.jev_ads import ClavePar, Juicio

        return Juicio(
            intento_id=uuid.uuid4(),
            clave=ClavePar("a" * 64, ficha.id, "b" * 64),
            relacion=relacion,
            probabilidades={
                "satisface": Decimal("0.10"),
                "no_satisface": Decimal("0.80"),
                "informacion_insuficiente": Decimal("0.10"),
            },
            confidence=Decimal("0.90"),
            observado_at=AHORA,
        )


def _fallo(codigo="timeout", detalle="se agoto la espera"):
    return FalloPar(codigo=codigo, detalle=detalle, duracion_ms=12)


def _sujeto(censo, terminos=("soporte mesa",), plan_extra=None):
    canonico = {
        "grupo": censo.miembros[0].anuncio_ids[0] if censo.miembros else 0,
        "terminos": list(terminos),
        **(plan_extra or {}),
    }
    canon = json.dumps(canonico, sort_keys=True, ensure_ascii=False)
    plan_sha = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return SemillasARevisar(
        plan_sha256=plan_sha,
        plan_canonico=canonico,
        fuentes_semillas={"origen": "lote-de-prueba"},
        terminos=tuple(terminos),
        censo=censo,
        plataforma="amazon_mx",
    )


def _grupo_con_fichas(conn, con_ficha_p2=True):
    p1 = _producto(conn, "P1")
    l1 = _listing(conn, p1, asin="B0P1LISTA1")
    p2 = _producto(conn, "P2")
    l2 = _listing(conn, p2, asin="B0P2LISTA2")
    grupo = _grupo(conn, "amazon_mx", (l1, l2))
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
        " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ENABLED', now())",
        (f"ad-{grupo}-0",),
    )
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
        " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ENABLED', now())",
        (f"ad-{grupo}-1",),
    )
    ficha1 = _ficha(conn, p1, (l1,))
    ficha2 = _ficha(conn, p2, (l2,)) if con_ficha_p2 else None
    from app.jev_catalogo import censo_grupo

    censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=grupo)
    return censo, ficha1, ficha2, grupo


def _eventos(conn, solicitud):
    return conn.execute(
        "SELECT tipo, ordinal, termino_sha256, respuesta IS NOT NULL AS exito,"
        " error, reutiliza_id FROM jev_par_evento"
        " WHERE revision_id = %s ORDER BY termino_sha256, ordinal",
        (solicitud,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Orden de escritura: revision + intencion antes del HTTP; resultado despues
# ---------------------------------------------------------------------------


def test_revision_e_intencion_confirmadas_antes_del_http():
    """El orden se observa desde OTRA sesion: el asesor corre SIN autocommit
    y el pedir espia consulta con una SEGUNDA conexion, que solo ve lo
    CONFIRMADO. Ver la revision y la intencion (y no el resultado) desde
    esa segunda sesion demuestra el commit previo al HTTP."""
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        estado_al_http = {}

        conn.autocommit = False  # el asesor confirma de verdad con commit()
        observador = psycopg.connect(_dsn_jev())  # otra sesion: solo confirmado
        try:

            def pedir_espia(termino, ficha):
                revision = observador.execute(
                    "SELECT sujeto_tipo, captured_at FROM jev_revision WHERE solicitud = %s",
                    (solicitud,),
                ).fetchone()
                intenciones = observador.execute(
                    "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s"
                    " AND tipo = 'intencion'",
                    (solicitud,),
                ).fetchone()[0]
                resultados = observador.execute(
                    "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s"
                    " AND tipo = 'resultado'",
                    (solicitud,),
                ).fetchone()[0]
                estado_al_http["revision"] = revision
                estado_al_http["intenciones"] = intenciones
                estado_al_http["resultados"] = resultados
                return _Pedido({termino: "satisface"})(termino, ficha)

            asesor = AsesorAds(
                conn, pedir=pedir_espia, api_key="k", presupuesto=5, ahora=lambda: AHORA
            )
            sujeto = _sujeto(censo)
            revision = asesor.evaluar(sujeto, solicitud_id=solicitud)
        finally:
            observador.rollback()
            observador.close()
            conn.rollback()
            conn.autocommit = True
        assert estado_al_http["revision"] is not None
        assert estado_al_http["revision"][0] == "semillas"
        assert estado_al_http["intenciones"] == 1
        assert estado_al_http["resultados"] == 0
        # El resultado del par llego DESPUES: fila de resultado confirmada.
        assert (
            conn.execute(
                "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s"
                " AND tipo = 'resultado' AND respuesta IS NOT NULL",
                (solicitud,),
            ).fetchone()[0]
            == 1
        )
        # La revision congela captured_at y el contrato.
        captured = conn.execute(
            "SELECT captured_at, contrato->>'modelo' FROM jev_revision WHERE solicitud = %s",
            (solicitud,),
        ).fetchone()
        assert captured[0] == AHORA
        assert captured[1] == "jev-1.13.0"
        assert isinstance(revision.resultados[0][1], HayCompatible)


@pytest.mark.parametrize("cubre_ambos", [True, False], ids=["ficha-cubre-ambos", "cubre-uno"])
def test_producto_con_dos_listings_se_evalua_si_la_ficha_cubre_todos(cubre_ambos):
    """R8: un producto anunciado con 2 listings se evalua cuando UNA ficha
    vigente cubre todos sus listings; si la ficha cubre solo uno, el miembro
    queda con ficha faltante (sin HTTP), nunca acreditado a medias."""
    with db_jev() as conn:
        producto = _producto(conn, "MULTI")
        l1 = _listing(conn, producto, asin="B0MULTI001")
        l2 = _listing(conn, producto, asin="B0MULTI002")
        grupo = _grupo(conn, "amazon_mx", (l1, l2))
        for i in range(2):
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ENABLED', now())",
                (f"ad-{grupo}-{i}",),
            )
        _ficha(conn, producto, (l1, l2) if cubre_ambos else (l1,))
        from app.jev_catalogo import censo_grupo

        censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=grupo)
        assert len(censo.miembros) == 1
        assert censo.miembros[0].listing_ids == frozenset({l1, l2})
        pedido = _Pedido({"soporte mesa": "satisface"})
        revision = AsesorAds(
            conn, pedir=pedido, api_key="k", presupuesto=5, ahora=lambda: AHORA
        ).evaluar(_sujeto(censo), solicitud_id=uuid.uuid4())
        resultado = revision.resultados[0][1]
        if cubre_ambos:
            assert pedido.llamados == ["soporte mesa"]
            assert resultado == HayCompatible((producto,), 1, 1)
        else:
            assert pedido.llamados == []
            assert isinstance(resultado, Indeterminado)
            assert "ficha_ausente" in resultado.motivos


def test_producto_sin_ficha_queda_como_ficha_faltante():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        asesor = AsesorAds(
            conn,
            pedir=_Pedido(guion={"soporte mesa": "satisface"}),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        revision = asesor.evaluar(_sujeto(censo), solicitud_id=uuid.uuid4())
        termino, resultado = revision.resultados[0]
        assert isinstance(resultado, HayCompatible)
        assert resultado.miembros_totales == 2


# ---------------------------------------------------------------------------
# Crash y reanudacion: exito reutilizado, intencion nueva con ordinal nuevo
# ---------------------------------------------------------------------------


def test_reanudacion_tras_crash_la_intencion_huerfana_esta_confirmada():
    """Igual que el orden sellado: al crash, la intencion del par interrumpido
    YA esta visible desde OTRA sesion (confirmada), sin su resultado."""
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        sujeto = _sujeto(censo, terminos=("t1", "t2"))
        visto_al_crash = {}

        conn.autocommit = False
        observador = psycopg.connect(_dsn_jev())
        try:

            def pedir_con_espia(termino, ficha):
                if termino == "t2":
                    visto_al_crash["intenciones"] = observador.execute(
                        "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s"
                        " AND tipo = 'intencion'",
                        (solicitud,),
                    ).fetchone()[0]
                    visto_al_crash["resultados"] = observador.execute(
                        "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s"
                        " AND tipo = 'resultado'",
                        (solicitud,),
                    ).fetchone()[0]
                    raise RuntimeError("crash simulado tras la intencion")
                return _Pedido({termino: "satisface"})(termino, ficha)

            asesor1 = AsesorAds(
                conn, pedir=pedir_con_espia, api_key="k", presupuesto=5, ahora=lambda: AHORA
            )
            with pytest.raises(RuntimeError):
                asesor1.evaluar(sujeto, solicitud_id=solicitud)
        finally:
            observador.rollback()
            observador.close()
            conn.rollback()
            conn.autocommit = True
        # t1 y t2 confirmados como intenciones; solo t1 tiene resultado.
        assert visto_al_crash["intenciones"] == 2
        assert visto_al_crash["resultados"] == 1
        # Reanudacion normal: t1 reutilizado (cero HTTP) y t2 completa.
        pedido2 = _Pedido(guion={"t2": "no_satisface"})
        asesor2 = AsesorAds(conn, pedir=pedido2, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        revision = asesor2.evaluar(sujeto, solicitud_id=solicitud)
        assert pedido2.llamados == ["t2"]
        por_termino = dict(revision.resultados)
        assert isinstance(por_termino["t1"], HayCompatible)


def test_reanudacion_con_ficha_revocada_no_es_otro_payload_ni_la_reutiliza():
    """R9: entre el crash y la reanudacion se revoca la ficha. La revision
    ya congelo su contexto: retomar con la misma solicitud NO es "otro
    payload". Pero el par de esa ficha ya no se reutiliza ni se consulta
    (spec: reutilizar "solo si la ficha sigue aprobada"; ai-review #400):
    queda como ficha faltante y sin HTTP. Un censo crudo distinto si sigue
    siendo otro payload."""
    from app.jev_catalogo import revocar_ficha

    with db_jev() as conn:
        censo, ficha1, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        sujeto = _sujeto(censo, terminos=("t1", "t2"))
        pedido1 = _Pedido(guion={"t1": "satisface"}, crash_en="t2")
        with pytest.raises(RuntimeError):
            AsesorAds(conn, pedir=pedido1, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
                sujeto, solicitud_id=solicitud
            )
        revocar_ficha(conn, ficha_version_id=ficha1, autor="a", motivo="cambio el material")
        pedido2 = _Pedido(guion={"t2": "no_satisface"})
        revision = AsesorAds(
            conn,
            pedir=pedido2,
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA + timedelta(hours=1),
        ).evaluar(sujeto, solicitud_id=solicitud)
        assert pedido2.llamados == []
        por_termino = dict(revision.resultados)
        for termino in ("t1", "t2"):
            assert isinstance(por_termino[termino], Indeterminado)
            assert "ficha_ausente" in por_termino[termino].motivos
        assert (
            conn.execute(
                "SELECT count(*) FROM jev_par_evento"
                " WHERE revision_id = %s AND tipo = 'reutilizacion'",
                (solicitud,),
            ).fetchone()[0]
            == 0
        )
        sin_un_miembro = replace(
            sujeto, censo=CensoCongelado(miembros=censo.miembros[:1], exhaustivo=censo.exhaustivo)
        )
        asesor = AsesorAds(conn, pedir=_Pedido(), api_key="k", presupuesto=5, ahora=lambda: AHORA)
        with pytest.raises(ValueError, match="otro payload"):
            asesor.evaluar(sin_un_miembro, solicitud_id=solicitud)


def test_reanudacion_reutiliza_el_primer_exito_validado():
    """R10: con dos exitos de la misma clave en la revision, se reutiliza el
    PRIMERO (spec: "Se reutiliza el primer exito validado") y la vista
    muestra ese mismo."""
    from app.jev_vista import _evento_del_par

    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        sujeto = _sujeto(censo, terminos=("t1",))
        asesor = AsesorAds(
            conn,
            pedir=_Pedido({"t1": "satisface"}),
            api_key="k",
            presupuesto=5,
            ahora=lambda: AHORA,
        )
        asesor.evaluar(sujeto, solicitud_id=solicitud)
        primero = conn.execute(
            "SELECT id, termino_sha256, ficha_version_id, contrato_sha256, respuesta"
            " FROM jev_par_evento WHERE revision_id = %s AND tipo = 'resultado'",
            (solicitud,),
        ).fetchone()
        intencion = uuid.uuid4()
        conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256, ficha_version_id,"
            " contrato_sha256, ordinal, tipo, request_sha256)"
            " VALUES (%s, %s, %s, %s, %s, 2, 'intencion', %s)",
            (intencion, solicitud, primero[1], primero[2], primero[3], "c" * 64),
        )
        segunda = {**primero[4], "relacion": "no_satisface"}
        conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256, ficha_version_id,"
            " contrato_sha256, ordinal, tipo, respuesta, intencion_id)"
            " VALUES (%s, %s, %s, %s, %s, 2, 'resultado', %s::jsonb, %s)",
            (
                uuid.uuid4(),
                solicitud,
                primero[1],
                primero[2],
                primero[3],
                json.dumps(segunda),
                intencion,
            ),
        )
        pedido = _Pedido()
        revision = AsesorAds(
            conn, pedir=pedido, api_key="k", presupuesto=5, ahora=lambda: AHORA
        ).evaluar(sujeto, solicitud_id=solicitud)
        assert pedido.llamados == []
        reutilizado = conn.execute(
            "SELECT reutiliza_id FROM jev_par_evento WHERE revision_id = %s"
            " AND tipo = 'reutilizacion'",
            (solicitud,),
        ).fetchone()[0]
        assert reutilizado == primero[0]
        assert isinstance(revision.resultados[0][1], HayCompatible)
        eventos = [
            dict(zip(("id", "termino_sha256", "ficha_version_id", "respuesta"), fila, strict=True))
            for fila in conn.execute(
                "SELECT id, termino_sha256, ficha_version_id, respuesta FROM jev_par_evento"
                " WHERE revision_id = %s AND tipo = 'resultado' ORDER BY ordinal",
                (solicitud,),
            ).fetchall()
        ]
        assert _evento_del_par(eventos, primero[1], primero[2])["id"] == primero[0]


def test_reanudacion_tras_crash_reutiliza_exito():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        sujeto = _sujeto(censo, terminos=("t1", "t2"))
        pedido1 = _Pedido(guion={"t1": "satisface"}, crash_en="t2")
        asesor1 = AsesorAds(conn, pedir=pedido1, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        with pytest.raises(RuntimeError):
            asesor1.evaluar(sujeto, solicitud_id=solicitud)
        # Quedo: resultado de t1, intencion SIN resultado de t2.
        filas = _eventos(conn, solicitud)
        assert sum(1 for f in filas if f[0] == "resultado" and f[3]) == 1
        huertanas = conn.execute(
            "SELECT count(*) FROM jev_par_evento e WHERE revision_id = %s"
            " AND tipo = 'intencion' AND NOT EXISTS ("
            "  SELECT 1 FROM jev_par_evento r WHERE r.intencion_id = e.id)",
            (solicitud,),
        ).fetchone()[0]
        assert huertanas == 1
        # Reanudacion: t1 se REUTILIZA (cero HTTP) y t2 reintenta con ordinal 2.
        pedido2 = _Pedido(guion={"t2": "no_satisface"})
        asesor2 = AsesorAds(conn, pedir=pedido2, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        revision = asesor2.evaluar(sujeto, solicitud_id=solicitud)
        assert pedido2.llamados == ["t2"]
        reutilizaciones = conn.execute(
            "SELECT count(*) FROM jev_par_evento WHERE revision_id = %s"
            " AND tipo = 'reutilizacion' AND reutiliza_id IS NOT NULL",
            (solicitud,),
        ).fetchone()[0]
        assert reutilizaciones == 1
        ordinal_nuevo_t2 = conn.execute(
            "SELECT COALESCE(max(ordinal), 0) FROM jev_par_evento"
            " WHERE revision_id = %s AND tipo = 'intencion'",
            (solicitud,),
        ).fetchone()[0]
        assert ordinal_nuevo_t2 >= 2
        por_termino = dict(revision.resultados)
        assert isinstance(por_termino["t1"], HayCompatible)
        assert isinstance(por_termino["t2"], Indeterminado)


def test_misma_solicitud_otro_payload_rechazado():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        asesor = AsesorAds(conn, pedir=_Pedido(), api_key="k", presupuesto=5, ahora=lambda: AHORA)
        asesor.evaluar(_sujeto(censo, terminos=("t1",)), solicitud_id=solicitud)
        # Otro payload (otros terminos) con la MISMA solicitud: rechazado.
        with pytest.raises(ValueError):
            asesor.evaluar(_sujeto(censo, terminos=("t1", "t2")), solicitud_id=solicitud)
        # La MISMA huella retoma sin rechazar.
        revision = asesor.evaluar(_sujeto(censo, terminos=("t1",)), solicitud_id=solicitud)
        assert revision.resultados[0][0] == "t1"


def test_misma_solicitud_otro_contrato_rechazado():
    """B5-r2 (B1): el contrato es parte del payload congelado de la
    revision; retomar la MISMA solicitud con OTRO contrato debe rechazarse
    en vez de mezclar juicios de dos contratos bajo una revision."""
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        pedido = _Pedido()
        base = contrato_por_defecto()
        otro = Contrato(
            modelo=base.modelo,
            opciones=base.opciones,
            criterios=base.criterios,
            instrucciones=base.instrucciones,
            version=base.version + "-OTRA",
            max_bytes_termino=base.max_bytes_termino,
            max_bytes_ficha=base.max_bytes_ficha,
        )
        solicitud = uuid.uuid4()
        AsesorAds(conn, pedir=pedido, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
            _sujeto(censo, terminos=("t1",)), solicitud_id=solicitud
        )
        with pytest.raises(ValueError, match="misma solicitud"):
            AsesorAds(
                conn,
                pedir=pedido,
                api_key="k",
                presupuesto=5,
                ahora=lambda: AHORA,
                contrato=otro,
            ).evaluar(_sujeto(censo, terminos=("t1",)), solicitud_id=solicitud)
        # El contrato NO cambio: retomar sigue siendo valido.
        AsesorAds(conn, pedir=pedido, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
            _sujeto(censo, terminos=("t1",)), solicitud_id=solicitud
        )
        contratos = conn.execute(
            "SELECT count(DISTINCT contrato_sha256) FROM jev_par_evento WHERE revision_id = %s",
            (solicitud,),
        ).fetchone()[0]
        assert contratos == 1


def test_reutilizacion_solo_de_exitos_validos():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        # Corrida 1: fallo del proveedor para t1.
        pedido1 = _Pedido(guion={"t1": _fallo()})
        AsesorAds(conn, pedir=pedido1, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
            _sujeto(censo, terminos=("t1",)), solicitud_id=solicitud
        )
        # Corrida 2: el FALLO no se reutiliza: hay nueva intencion y HTTP.
        pedido2 = _Pedido(guion={"t1": "no_satisface"})
        AsesorAds(conn, pedir=pedido2, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
            _sujeto(censo, terminos=("t1",)), solicitud_id=solicitud
        )
        assert pedido2.llamados == ["t1"]
        # El exito de OTRA revision no se reutiliza.
        pedido3 = _Pedido(guion={"t1": "no_satisface"})
        AsesorAds(conn, pedir=pedido3, api_key="k", presupuesto=5, ahora=lambda: AHORA).evaluar(
            _sujeto(censo, terminos=("t1",)), solicitud_id=uuid.uuid4()
        )
        assert pedido3.llamados == ["t1"]
        # El fallo quedo registrado como resultado con error, jamas como exito.
        errores = conn.execute(
            "SELECT count(*) FROM jev_par_evento WHERE tipo = 'resultado' AND error IS NOT NULL"
        ).fetchone()[0]
        assert errores >= 1


def test_presupuesto_agotado_deja_estado_visible_y_retomable():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        pedido1 = _Pedido(guion={"t1": "satisface"})
        asesor1 = AsesorAds(conn, pedir=pedido1, api_key="k", presupuesto=1, ahora=lambda: AHORA)
        revision1 = asesor1.evaluar(_sujeto(censo, terminos=("t1", "t2")), solicitud_id=solicitud)
        assert revision1.presupuesto_agotado is True
        assert pedido1.llamados == ["t1"]
        # Retomar con presupuesto termina el lote.
        pedido2 = _Pedido(guion={"t2": "satisface"})
        asesor2 = AsesorAds(conn, pedir=pedido2, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        revision2 = asesor2.evaluar(_sujeto(censo, terminos=("t1", "t2")), solicitud_id=solicitud)
        assert revision2.presupuesto_agotado is False
        assert pedido2.llamados == ["t2"]  # t1 reutilizado, cero HTTP


# ---------------------------------------------------------------------------
# Jev apagado y sin red
# ---------------------------------------------------------------------------


def test_sin_clave_jev_apagado_cero_llamadas_y_estados_visibles():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn)

        def transporte_contado(*a, **kw):
            raise AssertionError("no debe haber HTTP con Jev apagado")

        asesor = AsesorAds(
            conn, transporte=transporte_contado, api_key="", presupuesto=5, ahora=lambda: AHORA
        )
        revision = asesor.evaluar(_sujeto(censo), solicitud_id=uuid.uuid4())
        _, resultado = revision.resultados[0]
        assert isinstance(resultado, Indeterminado)
        assert "fallo_proveedor" in resultado.motivos
        eventos = conn.execute("SELECT count(*) FROM jev_par_evento").fetchone()[0]
        assert eventos >= 1  # intenciones registradas; el fallo visible en resultados


def _decision(conn, ad_entity_id: int) -> int:
    """Decision real minima (negative) para atar la revision por FK."""
    from psycopg.types.json import Json

    cfg = conn.execute(
        "INSERT INTO config_version (settings, label) VALUES (%s, 'prueba') RETURNING id",
        (Json({}),),
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('shadow', 'amazon_mx', 'done', now(), now()) RETURNING id"
    ).fetchone()[0]
    hoy = datetime(2026, 9, 1).date()
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
        " config_version_id, data_observed_at, window_start, window_end, inputs,"
        " search_term)"
        " VALUES (%s, %s, 'negative', now(), %s, now() - interval '1 hour', %s, %s,"
        " %s, 'soporte mesa') RETURNING id",
        (
            ciclo,
            ad_entity_id,
            cfg,
            hoy - timedelta(days=30),
            hoy,
            Json({"prueba": True}),
        ),
    ).fetchone()[0]


def test_decision_misma_solicitud_otro_termino_rechazado():
    """Regresion VEREDICTO-B3-r1 B1: para DecisionARevisar el rechazo de la
    misma solicitud con otro payload compara el CONTEXTO congelado completo
    (termino + censo + plataforma), no solo decision_id."""
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        decision_id = _decision(conn, grupo)
        asesor = AsesorAds(conn, pedir=_Pedido(), api_key="k", presupuesto=5, ahora=lambda: AHORA)
        solicitud = uuid.uuid4()
        asesor.evaluar(
            DecisionARevisar(decision_id, "soporte mesa", censo, "amazon_mx", AHORA),
            solicitud_id=solicitud,
        )
        with pytest.raises(ValueError):
            asesor.evaluar(
                DecisionARevisar(decision_id, "lampara de pie", censo, "amazon_mx", AHORA),
                solicitud_id=solicitud,
            )


def test_semillas_mismo_plan_sha_otro_censo_rechazado():
    """Regresion VEREDICTO-B3-r1 B1 (rama semillas): con el MISMO plan_sha256
    pero OTRO censo congelado (otra ficha resuelta), la misma solicitud se
    rechaza por el contexto, no por el hash del plan."""
    with db_jev() as conn:
        censo, ficha1, _, _ = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = uuid.uuid4()
        asesor = AsesorAds(conn, pedir=_Pedido(), api_key="k", presupuesto=5, ahora=lambda: AHORA)
        asesor.evaluar(_sujeto(censo), solicitud_id=solicitud)
        censo_otro = CensoCongelado(
            miembros=tuple(
                MiembroCenso(
                    anuncio_ids=m.anuncio_ids,
                    producto_id=m.producto_id,
                    listing_ids=m.listing_ids,
                    estados=m.estados,
                    ficha_version_id=uuid.uuid4(),  # OTRA ficha (otra variante)
                )
                for m in censo.miembros
            ),
            exhaustivo=censo.exhaustivo,
        )
        mismo_plan = _sujeto(censo_otro)
        assert mismo_plan.plan_sha256 == _sujeto(censo).plan_sha256
        with pytest.raises(ValueError):
            asesor.evaluar(mismo_plan, solicitud_id=solicitud)


def test_cli_decision_id_combinado_con_grupo_se_rechaza(capsys):
    """R2: --decision-id trae su propio termino y grupo; combinarlo con
    --grupo-id/--termino es configuracion invalida (exit 2) y no escribe."""
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        _, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        argv = ["evaluar", "--decision-id", "12", "--grupo-id", str(grupo), "--termino", "t"]
        argv += ["--solicitud", str(uuid.uuid4()), "--aplicar"]
        assert cli_jev(argv, pedir=_Pedido(), dsn=_dsn_jev()) == 2
        assert "no se combina" in capsys.readouterr().err
        assert conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0] == 0


def test_los_consumidores_no_importan_al_asesor():
    """Guarda del contrato: cycle/apply_cola/apply_harvest no importan al
    asesor ni a sus modulos, en NINGUNA forma de import (B5-r2, B3):
    resuelve `from app import X` -> "app.X", `from .x import y` ->
    "app.x.y" y los imports anidados en cualquier profundidad."""
    prohibidos = {
        "app.jev_ads",
        "app.jev_asesor",
        "app.jev_vista",
        "app.jev_juicios",
        "app.jev_catalogo",
        "tools.jev_ads",
    }
    for nombre in ("cycle.py", "apply_cola.py", "apply_harvest.py"):
        arbol = ast.parse((RAIZ / "app" / nombre).read_text(encoding="utf-8"))
        importados: set[str] = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                importados.update(alias.name for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom):
                # Relativo (level>=1): los modulos guardados viven en app/.
                base = (("app." if nodo.level else "") + (nodo.module or "")).rstrip(".")
                importados.add(base)
                importados.update(f"{base}.{alias.name}" for alias in nodo.names)
        assert not (importados & prohibidos), (nombre, importados & prohibidos)


def test_termino_asin_like_queda_fuera_del_clasificador_sin_http():
    with db_jev() as conn:
        censo, _, _, _ = _grupo_con_fichas(conn, con_ficha_p2=False)
        pedido = _Pedido()
        asesor = AsesorAds(conn, pedir=pedido, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        revision = asesor.evaluar(
            _sujeto(censo, terminos=("B0CX4ABCD9",)), solicitud_id=uuid.uuid4()
        )
        _, resultado = revision.resultados[0]
        assert isinstance(resultado, Indeterminado)
        assert "texto_no_aplica" in resultado.motivos
        assert pedido.llamados == []


def test_miembro_solo_archived_no_paga_http():
    """Correccion VEREDICTO-B3-r1: un miembro cuyo unico anuncio esta en
    estado conocido no activo (ARCHIVED) no recibe intencion ni HTTP, aun
    con ficha vigente: componer lo excluye como no_anunciado."""
    with db_jev() as conn:
        p1 = _producto(conn, "A1")
        l1 = _listing(conn, p1, asin="B0A1LISTA1")
        p2 = _producto(conn, "A2")
        l2 = _listing(conn, p2, asin="B0A2LISTA2")
        grupo = _grupo(conn, "amazon_mx", (l1, l2))
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ARCHIVED', now())",
            (f"ad-{grupo}-0",),
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES ((SELECT id FROM ad_entity WHERE external_id = %s), 'ENABLED', now())",
            (f"ad-{grupo}-1",),
        )
        _ficha(conn, p1, (l1,))
        _ficha(conn, p2, (l2,))
        from app.jev_catalogo import censo_grupo

        censo = censo_grupo(conn, plataforma="amazon_mx", ad_group_id=grupo)
        pedido = _Pedido(guion={"t": "satisface"})
        asesor = AsesorAds(conn, pedir=pedido, api_key="k", presupuesto=5, ahora=lambda: AHORA)
        revision = asesor.evaluar(_sujeto(censo, terminos=("t",)), solicitud_id=uuid.uuid4())
        assert pedido.llamados == ["t"]  # solo el ENABLED; el ARCHIVED no paga
        _, resultado = revision.resultados[0]
        assert isinstance(resultado, HayCompatible)
        assert resultado.miembros_totales == 1
        intenciones = conn.execute(
            "SELECT count(*) FROM jev_par_evento WHERE tipo = 'intencion'"
        ).fetchone()[0]
        assert intenciones == 1


# ---------------------------------------------------------------------------
# Sujeto decision: FK muerde con decision inexistente
# ---------------------------------------------------------------------------


def test_decision_inexistente_la_fk_muerde():
    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn)
        sujeto = DecisionARevisar(
            decision_id=999999,
            termino="soporte mesa",
            censo=censo,
            plataforma="amazon_mx",
            decided_at=AHORA,
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            asesor = AsesorAds(
                conn, pedir=_Pedido(), api_key="k", presupuesto=5, ahora=lambda: AHORA
            )
            asesor.evaluar(sujeto, solicitud_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# CLI de lote acotado
# ---------------------------------------------------------------------------


def test_cli_evaluar_seco_no_escribe_ni_llama(tmp_path):
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn)
        pedido = _Pedido()
        salida = []

        codigo = cli_jev(
            [
                "evaluar",
                "--plataforma",
                "amazon_mx",
                "--grupo-id",
                str(grupo),
                "--termino",
                "soporte mesa",
                "--solicitud",
                str(uuid.uuid4()),
                "--presupuesto",
                "3",
            ],
            pedir=pedido,
            dsn=_dsn_jev(),
            imprimir=salida.append,
        )
        assert codigo == 0
        assert pedido.llamados == []
        assert conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0] == 0
        assert any("seco" in linea for linea in salida)


def test_cli_seco_cuenta_fichas_con_la_regla_de_evaluar():
    """Nota de codex en B9b: el seco contaba fichas solo con un listing; un
    producto con 2 listings cubiertos por UNA ficha si se evalua al aplicar,
    y el seco debe decir lo mismo."""
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        producto = _producto(conn, "MULTI")
        l1 = _listing(conn, producto, asin="B0MULTI101")
        l2 = _listing(conn, producto, asin="B0MULTI102")
        grupo = _grupo(conn, "amazon_mx", (l1, l2))
        _ficha(conn, producto, (l1, l2))
        argv = ["evaluar", "--plataforma", "amazon_mx", "--grupo-id", str(grupo)]
        argv += ["--termino", "t", "--solicitud", str(uuid.uuid4())]
        salida = []
        assert cli_jev(argv, pedir=_Pedido(), dsn=_dsn_jev(), imprimir=salida.append) == 0
        assert any("fichas vigentes 1;" in linea for linea in salida), salida


def test_cli_evaluar_aplica_y_retoma(tmp_path):
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
        solicitud = str(uuid.uuid4())
        argv = [
            "evaluar",
            "--plataforma",
            "amazon_mx",
            "--grupo-id",
            str(grupo),
            "--termino",
            "soporte mesa",
            "--solicitud",
            solicitud,
            "--presupuesto",
            "3",
            "--aplicar",
        ]
        pedido = _Pedido(guion={"soporte mesa": "satisface"})
        salida = []
        assert cli_jev([*argv], pedir=pedido, dsn=_dsn_jev(), imprimir=salida.append) == 0
        assert pedido.llamados == ["soporte mesa"]
        assert conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0] == 1
        assert any("HayCompatible" in linea for linea in salida)
        # Retomar la misma solicitud: reutiliza el exito (cero HTTP nuevo).
        salida.clear()
        pedido2 = _Pedido()
        assert cli_jev(argv, pedir=pedido2, dsn=_dsn_jev(), imprimir=salida.append) == 0
        assert pedido2.llamados == []
        assert any("HayCompatible" in linea for linea in salida)


def test_cli_sin_dsn_da_error_config(monkeypatch):
    from tools.jev_ads import main as cli_jev

    argv = [
        "evaluar",
        "--plataforma",
        "amazon_mx",
        "--grupo-id",
        "1",
        "--termino",
        "t",
        "--solicitud",
        str(uuid.uuid4()),
    ]
    # Aislado (F5): sin ORBIT_DSN_ADMIN, dsn="" no puede caer al entorno.
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    codigo = cli_jev(argv, dsn="")
    assert codigo == 2


def test_cli_modulo_ejecutable_da_error_config_y_no_es_no_op():
    """B5-r2 (B2): `python -m tools.jev_ads ...` DEBE ejecutar main(); sin
    ORBIT_DSN_ADMIN sale 2 con el mensaje de configuracion, jamas un no-op
    silencioso con exit 0. Va por subprocess: el bug era justamente que el
    modulo importado no llamaba a nada."""
    argv = [
        sys.executable,
        "-m",
        "tools.jev_ads",
        "evaluar",
        "--plataforma",
        "amazon_mx",
        "--grupo-id",
        "1",
        "--termino",
        "t",
        "--solicitud",
        "00000000-0000-0000-0000-000000000001",
        "--aplicar",
    ]
    entorno = {k: v for k, v in os.environ.items() if k != "ORBIT_DSN_ADMIN"}
    corrida = subprocess.run(
        argv,
        cwd=RAIZ,
        env=entorno,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert corrida.returncode == 2, (corrida.returncode, corrida.stdout, corrida.stderr)
    assert "falta ORBIT_DSN_ADMIN" in corrida.stderr


# ---------------------------------------------------------------------------
# R2: revision de una decision guardada (camino de produccion)
# ---------------------------------------------------------------------------


def _decision_guardada(conn, kind, grupo, termino, inputs=None):
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode) VALUES ('shadow') RETURNING id"
    ).fetchone()[0]
    config = conn.execute(
        "INSERT INTO config_version (settings) VALUES ('{}') RETURNING id"
    ).fetchone()[0]
    dinero = kind in ("harvest", "bid", "budget")
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value,"
        " value_currency, inputs)"
        " VALUES (%s, %s, %s, %s, now() - interval '1 hour', current_date - 30,"
        " current_date - 12, %s, %s, %s, %s::jsonb) RETURNING id",
        (
            ciclo,
            grupo,
            kind,
            config,
            termino,
            Decimal("2.50") if dinero else None,
            "MXN" if dinero else None,
            json.dumps(inputs or {}),
        ),
    ).fetchone()[0]


def test_decision_negative_se_lee_con_su_termino_y_su_grupo():
    from app.jev_catalogo import decision_a_revisar

    with db_jev() as conn:
        censo, _, _, grupo = _grupo_con_fichas(conn)
        decision = _decision_guardada(conn, "negative", grupo, "funda barata")
        sujeto = decision_a_revisar(conn, decision)
        assert isinstance(sujeto, DecisionARevisar)
        assert (sujeto.decision_id, sujeto.termino, sujeto.plataforma) == (
            decision,
            "funda barata",
            "amazon_mx",
        )
        assert sujeto.censo == censo
        assert sujeto.destino_censo is None
        assert sujeto.decided_at is not None


def test_decision_harvest_trae_el_destino_congelado():
    from app.jev_catalogo import censo_grupo, decision_a_revisar

    with db_jev() as conn:
        _, _, _, origen = _grupo_con_fichas(conn)
        p3 = _producto(conn, "P3")
        destino = _grupo(conn, "amazon_mx", (_listing(conn, p3, asin="B0DESTINO1"),))
        externo = conn.execute(
            "SELECT external_id FROM ad_entity WHERE id = %s", (destino,)
        ).fetchone()[0]
        goal = {"goal": {"harvest": {"ad_group_id": externo, "campaign_id": "c-1"}}}
        decision = _decision_guardada(conn, "harvest", origen, "soporte mesa", goal)
        sujeto = decision_a_revisar(conn, decision)
        assert sujeto.destino_censo == censo_grupo(
            conn, plataforma="amazon_mx", ad_group_id=destino
        )
        sin_destino = _decision_guardada(conn, "harvest", origen, "soporte mesa")
        with pytest.raises(ValueError, match="destino"):
            decision_a_revisar(conn, sin_destino)
        perdido = {"goal": {"harvest": {"ad_group_id": "no-existe"}}}
        with pytest.raises(ValueError, match="no-existe"):
            decision_a_revisar(conn, _decision_guardada(conn, "harvest", origen, "x", perdido))


def test_decision_de_otro_kind_o_inexistente_se_rechaza():
    from app.jev_catalogo import decision_a_revisar

    with db_jev() as conn:
        _, _, _, grupo = _grupo_con_fichas(conn)
        with pytest.raises(ValueError, match="bid"):
            decision_a_revisar(conn, _decision_guardada(conn, "bid", grupo, None))
        with pytest.raises(ValueError, match="no existe"):
            decision_a_revisar(conn, 999_999)


def _argv_decision(decision, solicitud, *extra):
    return ["evaluar", "--decision-id", str(decision), "--solicitud", str(solicitud), *extra]


def test_cli_decision_harvest_seco_coincide_con_aplicar_y_aparece_en_cortes():
    """R2: el camino de produccion. El seco dice cuantos pares pagaria (un
    par por clave: el destino reutiliza el exito del origen cuando es el
    mismo producto); --aplicar paga exactamente eso y la asesoria queda
    legible por decision, como la lee /cortes."""
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        _, _, _, origen = _grupo_con_fichas(conn)
        externo = conn.execute(
            "SELECT external_id FROM ad_entity WHERE id = %s", (origen,)
        ).fetchone()[0]
        goal = {"goal": {"harvest": {"ad_group_id": externo}}}
        decision = _decision_guardada(conn, "harvest", origen, "soporte mesa", goal)
        solicitud = uuid.uuid4()
        salida: list[str] = []
        assert (
            cli_jev(
                _argv_decision(decision, solicitud),
                pedir=_Pedido(),
                dsn=_dsn_jev(),
                imprimir=salida.append,
            )
            == 0
        )
        assert any("pares nuevos 2, reutilizables 0; pagaria 2" in linea for linea in salida), (
            salida
        )
        assert conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0] == 0
        pedido = _Pedido({"soporte mesa": "satisface"})
        salida.clear()
        codigo = cli_jev(
            _argv_decision(decision, solicitud, "--aplicar"),
            pedir=pedido,
            dsn=_dsn_jev(),
            imprimir=salida.append,
        )
        assert codigo == 0
        assert len(pedido.llamados) == 2
        assert any(
            linea.startswith("resultado origen soporte mesa HayCompatible") for linea in salida
        )
        assert any(
            linea.startswith("resultado destino soporte mesa HayCompatible") for linea in salida
        )
        vista = AsesorAds(conn).leer([decision], ahora=AHORA)[decision]
        assert vista is not None and vista.decision_id == decision
        salida.clear()
        assert (
            cli_jev(
                _argv_decision(decision, solicitud),
                pedir=_Pedido(),
                dsn=_dsn_jev(),
                imprimir=salida.append,
            )
            == 0
        )
        assert any("pares nuevos 0, reutilizables 2; pagaria 0" in linea for linea in salida), (
            salida
        )


def test_cli_presupuesto_agotado_sale_3_y_el_seco_lo_anticipa():
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        _, _, _, grupo = _grupo_con_fichas(conn)
        decision = _decision_guardada(conn, "negative", grupo, "funda")
        solicitud = uuid.uuid4()
        salida: list[str] = []
        seco = _argv_decision(decision, solicitud, "--presupuesto", "1")
        assert cli_jev(seco, pedir=_Pedido(), dsn=_dsn_jev(), imprimir=salida.append) == 0
        assert any("pagaria 1 de presupuesto 1; se agotaria" in linea for linea in salida), salida
        assert (
            cli_jev([*seco, "--aplicar"], pedir=_Pedido(), dsn=_dsn_jev(), imprimir=salida.append)
            == 3
        )


@pytest.mark.parametrize(
    ("extra", "motivo"),
    [
        (["--termino", " "], "texto"),
        (["--termino", "a", "--termino", "a"], "repetido"),
        ([], "hacen falta"),
    ],
)
def test_cli_entradas_invalidas_salen_2(capsys, extra, motivo):
    from tools.jev_ads import main as cli_jev

    argv = ["evaluar", "--plataforma", "amazon_mx", "--grupo-id", "1", *extra]
    argv += ["--solicitud", str(uuid.uuid4())]
    if not extra:
        argv = ["evaluar", "--plataforma", "amazon_mx", "--solicitud", str(uuid.uuid4())]
    with db_jev():
        assert cli_jev(argv, pedir=_Pedido(), dsn=_dsn_jev()) == 2
    assert motivo in capsys.readouterr().err


def test_cli_presupuesto_cero_se_rechaza():
    from tools.jev_ads import main as cli_jev

    with pytest.raises(SystemExit) as salida:
        cli_jev(
            [
                "evaluar",
                "--decision-id",
                "1",
                "--solicitud",
                str(uuid.uuid4()),
                "--presupuesto",
                "0",
            ]
        )
    assert salida.value.code == 2


def test_cli_error_de_datos_sale_1_redactado(capsys):
    from tools.jev_ads import main as cli_jev

    with db_jev():
        argv = _argv_decision(999_999, uuid.uuid4())
        assert cli_jev(argv, pedir=_Pedido(), dsn=_dsn_jev()) == 1
    assert "decision 999999 no existe" in capsys.readouterr().err


@pytest.mark.parametrize("con_app_jev", [True, False], ids=["admin+app_jev", "solo-app_admin"])
def test_cli_escribe_con_el_rol_real_de_prod(con_app_jev, capsys):
    """G3-6 (R2): en prod ORBIT_DSN_ADMIN es orbit_admin, NO superusuario. Con
    app_admin solamente el INSERT de la revision se niega (exit 1, sin
    traceback); con app_jev ademas (docs/DEPLOY.md) el lote se escribe."""
    from psycopg.conninfo import make_conninfo

    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        rol = f"jev_cli_{os.getpid()}_{int(con_app_jev)}"
        try:
            conn.execute(f"CREATE ROLE {rol} LOGIN PASSWORD 'clave' NOSUPERUSER")
        except psycopg.errors.InsufficientPrivilege:
            pytest.fail(
                "ORBIT_TEST_DSN sin CREATEROLE: esta prueba es la unica evidencia del"
                " GRANT app_jev TO orbit_admin y no puede saltarse en silencio"
            )
        try:
            conn.execute(f"GRANT app_admin TO {rol}")
            if con_app_jev:
                conn.execute(f"GRANT app_jev TO {rol}")
            _, _, _, grupo = _grupo_con_fichas(conn, con_ficha_p2=False)
            decision = _decision_guardada(conn, "negative", grupo, "funda")
            dsn = make_conninfo(_dsn_jev(), user=rol, password="clave")
            argv = _argv_decision(decision, uuid.uuid4(), "--aplicar")
            codigo = cli_jev(argv, pedir=_Pedido(), dsn=dsn, imprimir=lambda *a, **k: None)
            revisiones = conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0]
            if con_app_jev:
                assert (codigo, revisiones) == (0, 1)
            else:
                assert (codigo, revisiones) == (1, 0)
                assert "permission denied" in capsys.readouterr().err
        finally:
            conn.execute(f"DROP OWNED BY {rol}")
            conn.execute(f"DROP ROLE {rol}")


def test_destino_modificado_marca_la_revision_obsoleta():
    """R3 (spec: "Un destino modificado marca revision obsoleta"): un anuncio
    nuevo en el grupo destino de un harvest ya revisado hace la revision
    Obsoleta al leerla; una resincronizacion que solo mueve synced_at no."""
    from psycopg.rows import dict_row, tuple_row

    from app.jev_ads import Obsoleta, Vigente

    with db_jev() as conn:
        _, _, _, origen = _grupo_con_fichas(conn)
        p3 = _producto(conn, "P3")
        destino = _grupo(conn, "amazon_mx", (_listing(conn, p3, asin="B0DESTINO3"),))
        externo = conn.execute(
            "SELECT external_id FROM ad_entity WHERE id = %s", (destino,)
        ).fetchone()[0]
        goal = {"goal": {"harvest": {"ad_group_id": externo}}}
        decision = _decision_guardada(conn, "harvest", origen, "soporte mesa", goal)
        cli = ["--solicitud", str(uuid.uuid4()), "--aplicar"]
        from tools.jev_ads import main as cli_jev

        assert (
            cli_jev(
                ["evaluar", "--decision-id", str(decision), *cli],
                pedir=_Pedido(),
                dsn=_dsn_jev(),
                imprimir=lambda *a, **k: None,
            )
            == 0
        )

        def vigencia():
            conn.row_factory = dict_row  # como la conexion del dashboard
            try:
                return AsesorAds(conn).leer([decision], ahora=AHORA)[decision].vigencia
            finally:
                conn.row_factory = tuple_row

        assert vigencia() == Vigente()
        conn.execute("UPDATE ad_entity_state SET synced_at = now() + interval '1 minute'")
        assert vigencia() == Vigente()
        p4 = _producto(conn, "P4")
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_mx', 'product_ad', %s, %s, %s)",
            (f"ad-{destino}-nuevo", destino, _listing(conn, p4, asin="B0DESTINO4")),
        )
        assert vigencia() == Obsoleta()


def test_un_fallo_no_se_repaga_en_el_mismo_lote_y_el_seco_lo_dice():
    """Bloqueante de codex en B10: con el mismo producto en origen y destino,
    un fallo del proveedor en el origen se pagaba OTRA vez en el destino y el
    seco no lo anticipaba. V1 no reintenta dentro del lote: la clave ya
    intentada reutiliza su resultado y el seco coincide con lo pagado."""
    from tools.jev_ads import main as cli_jev

    with db_jev() as conn:
        _, _, _, origen = _grupo_con_fichas(conn)
        externo = conn.execute(
            "SELECT external_id FROM ad_entity WHERE id = %s", (origen,)
        ).fetchone()[0]
        goal = {"goal": {"harvest": {"ad_group_id": externo}}}
        decision = _decision_guardada(conn, "harvest", origen, "soporte mesa", goal)
        argv = _argv_decision(decision, uuid.uuid4(), "--presupuesto", "2")
        salida: list[str] = []
        assert cli_jev(argv, pedir=_Pedido(), dsn=_dsn_jev(), imprimir=salida.append) == 0
        assert any(
            "pagaria 2 de presupuesto 2" in linea and "agotaria" not in linea for linea in salida
        ), salida
        pedido = _Pedido({"soporte mesa": _fallo()})
        assert (
            cli_jev([*argv, "--aplicar"], pedir=pedido, dsn=_dsn_jev(), imprimir=salida.append) == 0
        )
        assert len(pedido.llamados) == 2
