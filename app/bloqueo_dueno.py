"""Propuestas de bloqueo del dueno: huella, rechazo recordado y pausa.

La parte pura no abre conexion. Las lecturas arman el caso y llaman a
proponer. No escribe en Amazon y no importa modulos jev_*.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

PASOS_VIVOS = ("intencion", "aplicado", "reversa_intencion", "reversa_fallida")
_PASOS_QUITAR = ("aplicado", "reversa_fallida")
PLATAFORMAS_BLOQUEO = ("amazon_mx", "amazon_us")
ESTADOS_QUE_OCULTAN = frozenset({"pending_veto", "released", "applying"})
MINIMO_PAUSA = 20
VENTANA_PAUSA = 30
UMBRAL_PAUSA = Decimal("0.70")

_SQL_CANDIDATAS = """
SELECT v.senal_id, v.plataforma::text, v.ad_group_id, v.termino, v.lectura, v.vigente,
       s.termino_sha256, s.gasto, s.clics, s.moneda::text, r.miembros
  FROM jev_senal_vigente v
  JOIN jev_senal s ON s.id = v.senal_id
  JOIN jev_roster r ON r.sha256 = s.roster_sha256
 WHERE v.vigente AND v.lectura = 'ajena'
"""

_SQL_FICHAS = """
SELECT id::text, hechos, desconocidos
  FROM jev_ficha_version
 WHERE id = ANY(%s::uuid[])
"""

_SQL_VIVOS = """
SELECT v.plataforma::text, v.ad_group_id, v.termino_sha256
  FROM bloqueo_veredicto v
  JOIN LATERAL (
        SELECT e.paso
          FROM bloqueo_efecto e
         WHERE e.veredicto_id = v.id
         ORDER BY e.created_at DESC, e.id DESC
         LIMIT 1
       ) ult ON true
 WHERE v.tipo = 'aprobado'
   AND ult.paso = ANY(%s)
"""

_SQL_RECHAZOS = """
SELECT COALESCE(r.plataforma, o.plataforma)::text,
       COALESCE(r.ad_group_id, o.ad_group_id),
       COALESCE(r.termino_sha256, o.termino_sha256),
       COALESCE(r.huella_catalogo, o.huella_catalogo)
  FROM bloqueo_veredicto r
  LEFT JOIN bloqueo_veredicto o ON o.id = r.origen_id
 WHERE r.tipo IN ('rechazado', 'quitado')
"""

_SQL_MOTOR = """
SELECT q.platform::text, q.ad_entity_id, q.search_term, q.estado, q.vence_el,
       EXISTS (
           SELECT 1
             FROM apply_attempt t
            WHERE t.decision_id = q.decision_id
              AND t.tipo = 'reversa'
              AND t.resultado = 'ok'
       )
  FROM apply_queue q
 WHERE q.familia = 'term_cut'
"""

_SQL_JUICIOS = """
SELECT v.id, v.plataforma::text, v.tipo, v.created_at, v.origen_id
  FROM bloqueo_veredicto v
 WHERE NOT (v.tipo = 'aprobado' AND EXISTS (
           SELECT 1 FROM bloqueo_efecto e
            WHERE e.veredicto_id = v.id AND e.paso IN ('anulado', 'fallido')))
 ORDER BY v.created_at, v.id
"""

_SQL_PANTALLA_SENAL = """
SELECT s.id, s.miembros, s.motivos_lectura, s.roster_prueba,
       g.name, g.external_id, c.name, c.external_id
  FROM jev_senal s
  JOIN ad_entity g ON g.id = s.ad_group_id
  LEFT JOIN ad_entity c ON c.id = g.parent_id
 WHERE s.id = ANY(%s::uuid[])
"""

_SQL_BLOQUEOS_PANTALLA = """
SELECT v.id, v.plataforma::text, v.termino, ult.paso, ult.motivo,
       COALESCE(g.name, g.external_id),
       COALESCE(c.name, c.external_id)
  FROM bloqueo_veredicto v
  JOIN LATERAL (
        SELECT e.paso, e.motivo, e.created_at
          FROM bloqueo_efecto e
         WHERE e.veredicto_id = v.id
         ORDER BY e.created_at DESC, e.id DESC
         LIMIT 1
       ) ult ON true
  LEFT JOIN ad_entity g ON g.id = v.ad_group_id
  LEFT JOIN ad_entity c ON c.id = g.parent_id
 WHERE v.tipo = 'aprobado'
   AND (
        ult.paso = ANY(%s)
        OR (
            ult.paso IN ('fallido', 'anulado')
            AND ult.created_at > now() - interval '7 days'
        )
   )
 ORDER BY v.plataforma::text, v.termino
"""


@dataclass(frozen=True)
class FichaCatalogo:
    hechos: tuple[object, ...]
    desconocidos: tuple[str, ...]
    ficha_id: str | None = None


@dataclass(frozen=True)
class ProductoCatalogo:
    producto_id: int
    ficha: FichaCatalogo | None
    estados: tuple[str, ...] = ()


@dataclass(frozen=True)
class FilaMotor:
    estado: str
    vence_el: datetime
    reversa_aplicada: bool


@dataclass(frozen=True)
class Candidata:
    senal_id: UUID
    plataforma: str
    ad_group_id: int
    termino: str
    termino_sha256: str
    vigente: bool
    lectura: str
    gasto: Decimal | None
    clics: int | None
    moneda: str
    catalogo: tuple[ProductoCatalogo, ...]
    bloqueo_vivo: bool
    huellas_rechazo: frozenset[str]
    motor: tuple[FilaMotor, ...]


@dataclass(frozen=True)
class Propuesta:
    senal_id: UUID
    plataforma: str
    ad_group_id: int
    termino: str
    termino_sha256: str
    huella_catalogo: str
    gasto: Decimal | None
    moneda: str
    clics: int | None


@dataclass(frozen=True)
class JuicioDueno:
    tipo: str
    creado_en: datetime
    id: UUID
    origen_id: UUID | None = None


@dataclass(frozen=True)
class Pausa:
    pausada: bool
    aprobados: int
    muestra: int


def huella_catalogo(productos: tuple[ProductoCatalogo, ...]) -> str:
    vistos: set[int] = set()
    distintos: list[ProductoCatalogo] = []
    for producto in productos:
        if producto.producto_id in vistos:
            continue
        vistos.add(producto.producto_id)
        distintos.append(producto)
    lista = []
    for producto in sorted(distintos, key=lambda item: item.producto_id):
        if producto.ficha is None:
            lista.append([producto.producto_id, None])
            continue
        lista.append(
            [
                producto.producto_id,
                {
                    "desconocidos": list(producto.ficha.desconocidos),
                    "hechos": list(producto.ficha.hechos),
                },
            ]
        )
    canonico = json.dumps(lista, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def catalogo_de_miembros(
    miembros: dict, fichas: dict[str, FichaCatalogo]
) -> tuple[ProductoCatalogo, ...] | None:
    censo = miembros["censo"]
    paralelas = miembros["fichas"]
    vistos: set[int] = set()
    salida: list[ProductoCatalogo] = []
    for indice, miembro in enumerate(censo):
        producto_id = int(miembro[1])
        if producto_id in vistos:
            continue
        vistos.add(producto_id)
        ficha_id = paralelas[indice]
        if ficha_id is not None and str(ficha_id) not in fichas:
            return None
        estados = tuple(miembro[3])
        ficha = None if ficha_id is None else fichas[str(ficha_id)]
        salida.append(ProductoCatalogo(producto_id, ficha, estados))
    return tuple(salida)


def motor_oculta(filas: tuple[FilaMotor, ...], ahora: datetime) -> bool:
    for fila in filas:
        if fila.estado in ESTADOS_QUE_OCULTAN:
            return True
        if fila.estado == "applied" and not fila.reversa_aplicada:
            return True
        if fila.estado == "vetoed" and fila.vence_el > ahora:
            return True
    return False


def pausa_de(juicios: tuple[JuicioDueno, ...]) -> Pausa:
    ordenados = sorted(juicios, key=lambda juicio: (juicio.creado_en, str(juicio.id)))
    ultima = None
    for juicio in ordenados:
        if juicio.tipo == "reanudacion":
            ultima = juicio.creado_en
    quitados = {
        juicio.origen_id
        for juicio in ordenados
        if juicio.tipo == "quitado" and juicio.origen_id is not None
    }
    votos: list[str] = []
    for juicio in ordenados:
        if juicio.tipo not in ("aprobado", "rechazado"):
            continue
        if ultima is not None and not juicio.creado_en > ultima:
            continue
        if juicio.tipo == "aprobado" and juicio.id in quitados:
            votos.append("rechazado")
        else:
            votos.append(juicio.tipo)
    ventana = votos[-VENTANA_PAUSA:]
    muestra = len(ventana)
    aprobados = sum(1 for voto in ventana if voto == "aprobado")
    pausada = muestra >= MINIMO_PAUSA and Decimal(aprobados) / Decimal(muestra) < UMBRAL_PAUSA
    return Pausa(pausada, aprobados, muestra)


def proponer(
    candidatas: tuple[Candidata, ...], *, ahora: datetime, pausadas: frozenset[str]
) -> tuple[Propuesta, ...]:
    salidas: list[Propuesta] = []
    for candidata in candidatas:
        if not candidata.vigente or candidata.lectura != "ajena":
            continue
        if candidata.bloqueo_vivo or candidata.plataforma in pausadas:
            continue
        if motor_oculta(candidata.motor, ahora):
            continue
        huella = huella_catalogo(candidata.catalogo)
        if huella in candidata.huellas_rechazo:
            continue
        salidas.append(
            Propuesta(
                senal_id=candidata.senal_id,
                plataforma=candidata.plataforma,
                ad_group_id=candidata.ad_group_id,
                termino=candidata.termino,
                termino_sha256=candidata.termino_sha256,
                huella_catalogo=huella,
                gasto=candidata.gasto,
                moneda=candidata.moneda,
                clics=candidata.clics,
            )
        )
    salidas.sort(key=lambda item: (item.gasto is None, _gasto_desc(item.gasto), str(item.senal_id)))
    return tuple(salidas)


def interruptor_encendido(conn) -> bool:
    fila = conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()
    if fila is None:
        return False
    settings = fila[0]
    if isinstance(settings, str):
        settings = json.loads(settings)
    if not isinstance(settings, dict):
        return False
    return settings.get("jev.bloqueo") is True


def lee_pantalla(conn) -> dict:
    from psycopg.rows import tuple_row

    fabrica = conn.row_factory
    conn.row_factory = tuple_row
    try:
        return _arma_pantalla(conn)
    finally:
        conn.row_factory = fabrica


def lee_pausas(conn) -> dict[str, Pausa]:
    por_plataforma: dict[str, list[JuicioDueno]] = {}
    for vid, plataforma, tipo, creado, origen in conn.execute(_SQL_JUICIOS).fetchall():
        por_plataforma.setdefault(plataforma, []).append(JuicioDueno(tipo, creado, vid, origen))
    return {plataforma: pausa_de(tuple(juicios)) for plataforma, juicios in por_plataforma.items()}


def lee_propuestas(conn, ahora: datetime | None = None) -> tuple[Propuesta, ...]:
    if ahora is None:
        ahora = conn.execute("SELECT now()").fetchone()[0]
    filas = conn.execute(_SQL_CANDIDATAS).fetchall()
    miembros_por_senal = [_objeto(fila[10]) for fila in filas]
    ids = [
        ficha_id
        for miembros in miembros_por_senal
        for ficha_id in (miembros.get("fichas") or [])
        if ficha_id is not None
    ]
    fichas = _lee_fichas(conn, ids)
    vivos = {
        (plataforma, grupo, sha)
        for plataforma, grupo, sha in conn.execute(_SQL_VIVOS, (list(PASOS_VIVOS),)).fetchall()
    }
    rechazos: dict[tuple, set[str]] = {}
    for plataforma, grupo, sha, huella in conn.execute(_SQL_RECHAZOS).fetchall():
        if None in (plataforma, grupo, sha, huella):
            continue
        rechazos.setdefault((plataforma, grupo, sha), set()).add(huella)
    motor: dict[tuple, list[FilaMotor]] = {}
    for plataforma, grupo, termino, estado, vence, reversa in conn.execute(_SQL_MOTOR).fetchall():
        motor.setdefault((plataforma, grupo, termino), []).append(
            FilaMotor(estado, vence, bool(reversa))
        )
    pausadas = frozenset(
        plataforma for plataforma, pausa in lee_pausas(conn).items() if pausa.pausada
    )
    candidatas = []
    for fila, miembros in zip(filas, miembros_por_senal, strict=True):
        catalogo = catalogo_de_miembros(miembros, fichas)
        if catalogo is None:
            continue
        senal_id, plataforma, grupo, termino, lectura, vigente, sha, gasto, clics, moneda, _ = fila
        candidatas.append(
            Candidata(
                senal_id=senal_id,
                plataforma=plataforma,
                ad_group_id=grupo,
                termino=termino,
                termino_sha256=sha,
                vigente=bool(vigente),
                lectura=lectura,
                gasto=gasto,
                clics=clics,
                moneda=moneda,
                catalogo=catalogo,
                bloqueo_vivo=(plataforma, grupo, sha) in vivos,
                huellas_rechazo=frozenset(rechazos.get((plataforma, grupo, sha), ())),
                motor=tuple(motor.get((plataforma, grupo, termino), ())),
            )
        )
    return proponer(tuple(candidatas), ahora=ahora, pausadas=pausadas)


def _arma_pantalla(conn) -> dict:
    encendido = interruptor_encendido(conn)
    pausas = lee_pausas(conn)
    propuestas = lee_propuestas(conn) if encendido else ()
    por_plataforma = {plataforma: [] for plataforma in PLATAFORMAS_BLOQUEO}
    for fila in _enriquece(conn, propuestas):
        por_plataforma[fila["plataforma"]].append(fila)
    plataformas = []
    for plataforma in PLATAFORMAS_BLOQUEO:
        pausa = pausas.get(plataforma) or Pausa(False, 0, 0)
        filas = [] if pausa.pausada else por_plataforma[plataforma]
        plataformas.append(
            {
                "plataforma": plataforma,
                "pausada": pausa.pausada,
                "aprobados": pausa.aprobados,
                "muestra": pausa.muestra,
                "porcentaje": _porcentaje(pausa.aprobados, pausa.muestra),
                "propuestas": filas,
            }
        )
    return {
        "interruptor": encendido,
        "plataformas": plataformas,
        "bloqueos": _filas_bloqueo(conn),
    }


def _enriquece(conn, propuestas) -> list[dict]:
    if not propuestas:
        return []
    extra = {}
    for fila in conn.execute(
        _SQL_PANTALLA_SENAL, ([propuesta.senal_id for propuesta in propuestas],)
    ).fetchall():
        extra[fila[0]] = fila
    salida = []
    for propuesta in propuestas:
        fila = extra.get(propuesta.senal_id)
        miembros, motivos, prueba = 0, [], None
        grupo, campana = "—", "—"
        if fila is not None:
            miembros = fila[1]
            motivos = list(fila[2] or [])
            prueba = fila[3]
            grupo = _nombre(fila[4], fila[5])
            campana = _nombre(fila[6], fila[7])
        salida.append(
            {
                "senal_id": str(propuesta.senal_id),
                "plataforma": propuesta.plataforma,
                "termino": propuesta.termino,
                "grupo": grupo,
                "campana": campana,
                "gasto": None if propuesta.gasto is None else str(propuesta.gasto),
                "moneda": propuesta.moneda,
                "clics": propuesta.clics,
                "miembros": miembros,
                "motivos": motivos,
                "listado_de": _listado(prueba),
            }
        )
    return salida


def _filas_bloqueo(conn) -> list[dict]:
    filas = []
    for vid, plataforma, termino, paso, motivo, grupo, campana in conn.execute(
        _SQL_BLOQUEOS_PANTALLA, (list(PASOS_VIVOS),)
    ).fetchall():
        filas.append(
            {
                "veredicto_id": str(vid),
                "plataforma": plataforma,
                "termino": termino or "—",
                "grupo": grupo or "—",
                "campana": campana or "—",
                "paso": paso,
                "motivo": motivo,
                "quitar": paso in _PASOS_QUITAR,
            }
        )
    return filas


def _porcentaje(aprobados: int, muestra: int) -> int:
    if muestra == 0:
        return 0
    return (aprobados * 100) // muestra


def _nombre(name, external_id) -> str:
    if name:
        return name
    if external_id:
        return external_id
    return "—"


def _listado(prueba) -> str | None:
    if isinstance(prueba, str):
        prueba = json.loads(prueba)
    if not isinstance(prueba, dict):
        return None
    valor = prueba.get("listado_de")
    if valor is None:
        return None
    return str(valor)


def _gasto_desc(gasto: Decimal | None) -> Decimal:
    if gasto is None:
        return Decimal(0)
    return -gasto


def _objeto(valor: object) -> dict:
    if isinstance(valor, str):
        return json.loads(valor)
    return valor


def _lee_fichas(conn, ids: list) -> dict[str, FichaCatalogo]:
    if not ids:
        return {}
    fichas = {}
    for ficha_id, hechos, desconocidos in conn.execute(_SQL_FICHAS, (ids,)).fetchall():
        hechos_obj = _objeto(hechos) if isinstance(hechos, str) else hechos
        fichas[str(ficha_id)] = FichaCatalogo(
            hechos=tuple(hechos_obj),
            desconocidos=tuple(desconocidos or ()),
            ficha_id=str(ficha_id),
        )
    return fichas
