"""Tests de `app/jev_ads.py`: tipos puros y `componer` (JEV ADS 01, 1.1).

Reglas que fijan (docs/superpowers/specs/2026-10-03-jev-ads-design.md,
"Catalogo y reglas de composicion"):

- Un producto compatible observado permite HayCompatible aun con cobertura
  parcial; la limitacion viaja en el resultado.
- NingunoCompatible exige universo no vacio, exhaustivo, fichas de todos los
  miembros y no_satisface en cada par.
- En el resto de casos, Indeterminado con motivos explicitos: vacio no prueba
  exclusion, un fallo del proveedor jamas es incompatibilidad y una ficha de
  otra variante no acredita un producto.
- El modulo es PURO: no importa red ni DB.
"""

from __future__ import annotations

import ast
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.jev_ads import (
    CensoCongelado,
    ClavePar,
    EstadoAnuncio,
    FalloProveedor,
    FichaFaltante,
    HayCompatible,
    Indeterminado,
    Juicio,
    MiembroCenso,
    NingunoCompatible,
    NoAplicaTexto,
    componer,
    es_asin_like,
)

RAIZ = Path(__file__).resolve().parents[1]

# Fichas del censo: versiones fijas por test (los UUID dan igualdad literal).
F1 = uuid.uuid4()
F2 = uuid.uuid4()

OBS = datetime(2026, 10, 3, tzinfo=UTC)


def _juicio(ficha: uuid.UUID, relacion: str) -> Juicio:
    return Juicio(
        intento_id=uuid.uuid4(),
        clave=ClavePar(
            termino_literal_sha256="a" * 64,
            ficha_version_id=ficha,
            contrato_sha256="b" * 64,
        ),
        relacion=relacion,  # type: ignore[arg-type]
        probabilidades={
            "satisface": Decimal("0.70"),
            "no_satisface": Decimal("0.20"),
            "informacion_insuficiente": Decimal("0.10"),
        },
        confidence=Decimal("0.80"),
        observado_at=OBS,
    )


def _miembro(
    producto_id: int | None,
    ficha: uuid.UUID | None,
    *,
    anuncios: tuple[int, ...] = (1,),
    listings: frozenset[int] = frozenset({1}),
    estados: tuple[EstadoAnuncio, ...] | None = None,
) -> MiembroCenso:
    """Por defecto el miembro trae estado activo conocido (ENABLED): los
    tests que prueban estados especiales los pasan explicitos."""
    if estados is None:
        estados = tuple(EstadoAnuncio("ENABLED", OBS) for _ in anuncios)
    return MiembroCenso(
        anuncio_ids=anuncios,
        producto_id=producto_id,
        listing_ids=listings,
        estados=estados,
        ficha_version_id=ficha,
    )


# ---------------------------------------------------------------------------
# Casos del DoD de 1.1
# ---------------------------------------------------------------------------


def test_un_compatible_con_hueco_da_hay_compatible():
    censo = CensoCongelado(
        miembros=(
            _miembro(11, F1),
            _miembro(12, F2),
            _miembro(None, None, anuncios=(3,), listings=frozenset()),
        ),
        exhaustivo=False,
    )
    pares = (
        _juicio(F1, "satisface"),
        _juicio(F2, "no_satisface"),
        FichaFaltante(producto_id=None),
    )
    resultado = componer(censo, pares)
    assert resultado == HayCompatible(
        producto_ids=(11,),
        miembros_con_juicio=2,
        miembros_totales=3,
    )


def test_todos_negativos_con_universo_desconocido_da_indeterminado():
    censo = CensoCongelado(
        miembros=(_miembro(11, F1), _miembro(12, F2)),
        exhaustivo=False,
    )
    pares = (_juicio(F1, "no_satisface"), _juicio(F2, "no_satisface"))
    assert componer(censo, pares) == Indeterminado(frozenset({"universo_desconocido"}))


def test_todos_negativos_con_hueco_de_ficha_da_indeterminado():
    censo = CensoCongelado(
        miembros=(_miembro(11, F1), _miembro(12, None)),
        exhaustivo=True,
    )
    pares = (_juicio(F1, "no_satisface"), FichaFaltante(producto_id=12))
    assert componer(censo, pares) == Indeterminado(frozenset({"ficha_ausente"}))


def test_todos_negativos_completo_y_con_ficha_da_ninguno_compatible():
    censo = CensoCongelado(
        miembros=(_miembro(11, F1), _miembro(12, F2)),
        exhaustivo=True,
    )
    pares = (_juicio(F1, "no_satisface"), _juicio(F2, "no_satisface"))
    assert componer(censo, pares) == NingunoCompatible(miembros_totales=2)


def test_conjunto_vacio_no_prueba_exclusion():
    resultado = componer(CensoCongelado(miembros=(), exhaustivo=True), ())
    assert resultado == Indeterminado(frozenset({"universo_vacio"}))


def test_ficha_de_otra_variante_no_acredita_producto():
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    resultado = componer(censo, (_juicio(F2, "satisface"),))
    assert resultado == Indeterminado(frozenset({"ficha_ausente", "juicio_ausente"}))


def test_miembro_sin_ficha_bloquea_y_juicio_ausente_se_reporta():
    censo = CensoCongelado(miembros=(_miembro(11, None),), exhaustivo=True)
    con_par = componer(censo, (FichaFaltante(producto_id=11),))
    assert con_par == Indeterminado(frozenset({"ficha_ausente"}))
    sin_par = componer(censo, ())
    assert sin_par == Indeterminado(frozenset({"ficha_ausente"}))


def test_miembro_con_ficha_pero_sin_juicio_da_juicio_ausente():
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    assert componer(censo, ()) == Indeterminado(frozenset({"juicio_ausente"}))


def test_juicio_insuficiente_da_indeterminado():
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    pares = (_juicio(F1, "informacion_insuficiente"),)
    assert componer(censo, pares) == Indeterminado(frozenset({"juicio_insuficiente"}))


def test_fallo_proveedor_da_indeterminado_y_no_incompatibilidad():
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    pares = (FalloProveedor(producto_id=11, motivo="timeout"),)
    assert componer(censo, pares) == Indeterminado(frozenset({"fallo_proveedor", "juicio_ausente"}))


def test_asin_like_no_entra_al_clasificador():
    assert es_asin_like("B0CX4ABCD9")
    assert es_asin_like("b0cx4abcd9")
    assert not es_asin_like("kit b0abcdefgh rojo")
    assert not es_asin_like("soporte para mesa de aluminio")
    assert not es_asin_like("1234567890")
    assert not es_asin_like("ABCDEFGHIJ")
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    pares = (NoAplicaTexto(motivo="asin_like"),)
    assert componer(censo, pares) == Indeterminado(frozenset({"texto_no_aplica", "juicio_ausente"}))


def test_regla_asin_like_coincide_con_fabrica_plan():
    from app.fabrica_plan import PATRON_ASIN

    muestras = (
        "B0CX4ABCD9",
        "b0cx4abcd9",
        "kit b0abcdefgh rojo",
        "soporte para mesa",
        "1234567890",
        "ABCDEFGHIJ",
    )
    for muestra in muestras:
        assert es_asin_like(muestra) == bool(PATRON_ASIN.match(muestra)), muestra


# ---------------------------------------------------------------------------
# Estado del anuncio en la composicion (regresion VEREDICTO-B2-r1 B1)
# ---------------------------------------------------------------------------


def test_anuncio_archived_no_acredita_compatible():
    censo = CensoCongelado(
        miembros=(_miembro(11, F1, estados=(EstadoAnuncio("ARCHIVED", OBS),)),),
        exhaustivo=True,
    )
    resultado = componer(censo, (_juicio(F1, "satisface"),))
    assert resultado == Indeterminado(frozenset({"no_anunciado"}))


def test_todos_los_anuncios_archived_no_da_ninguno_compatible():
    censo = CensoCongelado(
        miembros=(_miembro(11, F1, estados=(EstadoAnuncio("ARCHIVED", OBS),)),),
        exhaustivo=True,
    )
    resultado = componer(censo, (_juicio(F1, "no_satisface"),))
    assert resultado == Indeterminado(frozenset({"no_anunciado"}))


def test_estado_ausente_bloquea_negativo_universal_y_no_el_compatible():
    censo_negativo = CensoCongelado(
        miembros=(_miembro(11, F1, estados=()),),
        exhaustivo=True,
    )
    assert componer(censo_negativo, (_juicio(F1, "no_satisface"),)) == Indeterminado(
        frozenset({"missing_state"})
    )
    censo_mixto = CensoCongelado(
        miembros=(
            _miembro(11, F1),
            _miembro(12, F2, estados=()),
        ),
        exhaustivo=True,
    )
    pares = (_juicio(F1, "satisface"), _juicio(F2, "no_satisface"))
    assert componer(censo_mixto, pares) == HayCompatible(
        producto_ids=(11,),
        miembros_con_juicio=2,
        miembros_totales=2,
    )


def test_plan_sin_anuncios_con_todo_no_satisface_da_ninguno_compatible():
    """Regresion VEREDICTO-B2-r2 (B3): el universo explicito del plan de
    fabrica puede tener miembros sin anuncios todavia (anuncio_ids vacio).
    Sin anuncios no hay estado que falte: missing_state no aplica y el
    negativo universal, con fichas y no_satisface en cada par, si llega."""
    plan = CensoCongelado(
        miembros=(
            _miembro(11, F1, anuncios=(), listings=frozenset({1}), estados=()),
            _miembro(12, F2, anuncios=(), listings=frozenset({2}), estados=()),
        ),
        exhaustivo=True,
    )
    pares = (_juicio(F1, "no_satisface"), _juicio(F2, "no_satisface"))
    assert componer(plan, pares) == NingunoCompatible(miembros_totales=2)


def test_anuncio_mixto_archived_y_activo_cuenta_como_anunciado():
    censo = CensoCongelado(
        miembros=(
            _miembro(
                11,
                F1,
                estados=(
                    EstadoAnuncio("ARCHIVED", OBS),
                    EstadoAnuncio("ENABLED", OBS),
                ),
                anuncios=(1, 2),
            ),
        ),
        exhaustivo=True,
    )
    assert componer(censo, (_juicio(F1, "no_satisface"),)) == NingunoCompatible(miembros_totales=1)


def test_estados_no_paralelos_a_anuncios_es_error_estructural():
    roto = CensoCongelado(
        miembros=(
            MiembroCenso(
                anuncio_ids=(1, 2),
                producto_id=11,
                listing_ids=frozenset({1}),
                estados=(EstadoAnuncio("ENABLED", OBS),),
                ficha_version_id=F1,
            ),
        ),
        exhaustivo=True,
    )
    with pytest.raises(ValueError):
        componer(roto, ())


# ---------------------------------------------------------------------------
# Estructural y pureza
# ---------------------------------------------------------------------------


def test_la_misma_ficha_en_dos_miembros_es_error_estructural():
    censo = CensoCongelado(
        miembros=(_miembro(11, F1), _miembro(12, F1)),
        exhaustivo=True,
    )
    with pytest.raises(ValueError):
        componer(censo, ())


def test_dos_juicios_para_la_misma_ficha_es_error_estructural():
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    with pytest.raises(ValueError):
        componer(censo, (_juicio(F1, "no_satisface"), _juicio(F1, "satisface")))


def test_relacion_no_reconocida_es_error_estructural():
    """Regresion revision automatica B2-r4 (F3): una relacion fuera del
    contrato no puede contar como juicio y menos producir NingunoCompatible
    silencioso."""
    censo = CensoCongelado(miembros=(_miembro(11, F1),), exhaustivo=True)
    raro = Juicio(
        intento_id=uuid.uuid4(),
        clave=ClavePar(
            termino_literal_sha256="a" * 64,
            ficha_version_id=F1,
            contrato_sha256="b" * 64,
        ),
        relacion="desconocida",  # type: ignore[arg-type]
        probabilidades={"desconocida": Decimal("1.00")},
        confidence=Decimal("0.90"),
        observado_at=OBS,
    )
    with pytest.raises(ValueError):
        componer(censo, (raro,))


_PROHIBIDOS_MODULOS_PUROS = {
    "httpx",
    "psycopg",
    "requests",
    "urllib",
    "socket",
    "ssl",
    "app.db",
    "app.ads",
    "app.jev_catalogo",
    "app.jev_juicios",
}
_PROHIBIDOS_NOMBRES_PUROS = {"psycopg", "httpx", "requests", "socket", "ssl", "conn"}
_PERMITIDOS_PUROS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "decimal",
    "hashlib",
    "json",
    "re",
    "typing",
    "uuid",
}


def _candidatos_import(nodo: ast.Import | ast.ImportFrom) -> set[str]:
    """Nombres ABSOLUTOS que trae un nodo de import (B5-r3): los relativos
    se resuelven contra su paquete (`from . import db` es "app.db" y
    `from .x import Y` trae "app.x" y "app.x.y"; misma semantica que la
    guarda B3 de test_jev_cli.py)."""
    if isinstance(nodo, ast.Import):
        return {alias.name for alias in nodo.names}
    base = (("app." if nodo.level else "") + (nodo.module or "")).rstrip(".")
    return {base, *(f"{base}.{alias.name}" for alias in nodo.names)}


def _violacion_import(nombre: str, permitidos: set[str]) -> bool:
    """Un nombre esta vedado si cae en la lista negra o si NO es stdlib puro
    ni esta concedido (mismo criterio para top-level y anidados, B5-r3)."""
    if any(nombre == p or nombre.startswith(p + ".") for p in _PROHIBIDOS_MODULOS_PUROS):
        return True
    return not (
        nombre.split(".")[0] in sys.stdlib_module_names
        or any(nombre == p or nombre.startswith(p.rstrip(".") + ".") for p in permitidos)
    )


def _fugas_pureza(codigo: str) -> list[tuple[str, str]]:
    """Guarda de pureza del nucleo: devuelve (donde, hallazgo) por cada
    import o nombre IO que la viole. Vacio = puro."""
    arbol = ast.parse(codigo)
    fugas: list[tuple[str, str]] = []

    # 1) imports de ALCANCE DE MODULO (B5-r3: un `try:`/`if:` top-level
    #    sigue siendo alcance de modulo, asi que se recorre su cuerpo; los
    #    def/class no, porque sus imports son perezosos y los juzga el
    #    punto 2): solo la biblioteca estandar pura.
    pendientes = list(arbol.body)
    while pendientes:
        nodo = pendientes.pop()
        if isinstance(nodo, (ast.Import, ast.ImportFrom)):
            for candidato in _candidatos_import(nodo):
                if _violacion_import(candidato, _PERMITIDOS_PUROS):
                    fugas.append(("top-level", candidato))
        elif not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pendientes.extend(ast.iter_child_nodes(nodo))

    # 2) cada nodo top-level EXCEPTO AsesorAds: puro incluso por dentro
    #    (sin imports vedados ni fuera de permitidos a cualquier
    #    profundidad, sin nombres IO). Los imports se RESUELVEN antes de
    #    comparar (B5-r2, B5a): `from app import db` es "app.db" e
    #    `import urllib.request` cae bajo "urllib"; valen igualdad o prefijo
    #    con punto.
    for nodo in arbol.body:
        if isinstance(nodo, (ast.Import, ast.ImportFrom)):
            continue
        nombre = getattr(nodo, "name", None) or getattr(nodo, "id", "")
        if nombre == "AsesorAds":
            continue
        for sub in ast.walk(nodo):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                for candidato in _candidatos_import(sub):
                    if _violacion_import(candidato, _PERMITIDOS_PUROS):
                        fugas.append((nombre or "<modulo>", candidato))
            elif isinstance(sub, ast.Name) and sub.id in _PROHIBIDOS_NOMBRES_PUROS:
                fugas.append((nombre, sub.id))
    return sorted(set(fugas))


def test_modulo_puro_sin_red_ni_db_en_top_level():
    """El nucleo (tipos + componer y sus helpers) es PURO: red y DB solo
    entran con AsesorAds (1.4) y por imports PEREZOSOS dentro de sus
    metodos. La guarda cubre TODO nodo top-level excepto AsesorAds, con
    imports anidados a cualquier profundidad y nombres IO (CodeRabbit
    B3-r3); en alcance de modulo (incluido el cuerpo de un try/if
    top-level, B5-r3) tampoco hay red, ni DB, ni modulos Jev de IO; los
    relativos se resuelven a absolutos y `permitidos` rige tambien los
    anidados (B5-r3)."""
    assert _fugas_pureza((RAIZ / "app" / "jev_ads.py").read_text(encoding="utf-8")) == []


def test_guarda_pureza_caza_relativo_anidado():
    """B5-r3 mutante (a): `from . import db` anidado en una funcion es
    `app.db` (el relativo se resuelve antes de juzgar) y sale rojo."""
    codigo = "import json\n\ndef componer():\n    from . import db\n    return db\n"
    assert any(hallazgo == "app.db" for _, hallazgo in _fugas_pureza(codigo))


def test_guarda_pureza_caza_import_en_try_de_modulo():
    """B5-r3 mutante (b): un `try: import boto3` a nivel de modulo no
    escapa a la guarda: el cuerpo del Try sigue siendo alcance de modulo y
    boto3 no es stdlib ni esta concedido."""
    codigo = "import json\ntry:\n    import boto3\nexcept ImportError:\n    boto3 = None\n"
    assert any(hallazgo == "boto3" for _, hallazgo in _fugas_pureza(codigo))


def test_guarda_pureza_deja_pasar_permitido_anidado():
    """B5-r3 control positivo (c): un import anidado de la lista permitidos
    sigue pasando en cualquier profundidad (la guarda no se vuelve
    paranoica)."""
    codigo = (
        "import json\n"
        "\n"
        "def componer(pares):\n"
        "    from decimal import Decimal\n"
        "    import uuid\n"
        "\n"
        "    return Decimal(0) + uuid.UUID(int=0) if pares else None\n"
    )
    assert _fugas_pureza(codigo) == []
