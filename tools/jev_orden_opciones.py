"""Mide si invertir el orden de las opciones Choice cambia la respuesta.

Seco por omision. `--aplicar` paga una muestra de pares con Libro.pagar
bajo un Contrato con opciones y criterios al reves. El lado normal ya esta
en jev_par_evento y no se vuelve a pagar.

No consulta el tope diario del job. Libro.pagar tampoco lo tiene. Las
intenciones si cuentan para ese tope; el tope del job es otro ajuste.

DSN: ORBIT_DSN_ADMIN. Ese login necesita el grupo app_jev para escribir
revisiones (docs/DEPLOY.md: GRANT app_jev TO orbit_admin).

No esta en la imagen. En el host, con el archivo ya copiado, el guion
entra por stdin (docs/DEPLOY.md, tools fuera de la imagen). --n y
--aplicar van antes del redirect:

  docker exec -i orbit-app-1 python - < tools/jev_orden_opciones.py
  docker exec -i orbit-app-1 python - --aplicar < tools/jev_orden_opciones.py
  docker exec -i orbit-app-1 python - --n 100 --aplicar < tools/jev_orden_opciones.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import NamedTuple

import psycopg

from app.db import OrbitDbError, connect
from app.jev_ads import FalloProveedor, _canonico
from app.jev_catalogo import fichas_por_id
from app.jev_juicios import (
    contrato_por_defecto,
    leer_api_key,
    pedir_juicio,
)
from app.jev_lectura import Ajustes, ajustes_desde_settings
from app.jev_libro import Libro, intenciones_del_dia
from app.redaction import scrub

SALIDA_ERROR = 1
SALIDA_CONFIGURACION = 2
SOLICITUD = uuid.uuid5(uuid.NAMESPACE_URL, "orbit:jev-ads-02:0.5:orden-opciones")

_SQL_ELEGIBLES = """
SELECT e.termino_sha256, e.ficha_version_id, e.relacion
  FROM (
    SELECT DISTINCT ON (termino_sha256, ficha_version_id)
           termino_sha256,
           ficha_version_id::text AS ficha_version_id,
           respuesta->>'relacion' AS relacion
      FROM jev_par_evento
     WHERE tipo = 'resultado'
       AND respuesta IS NOT NULL
       AND contrato_sha256 = %s
     ORDER BY termino_sha256, ficha_version_id, created_at, id
  ) e
  JOIN (
    SELECT v.id::text AS id
      FROM jev_ficha_version v
      LEFT JOIN jev_ficha_revocacion rv ON rv.ficha_version_id = v.id
     WHERE rv.id IS NULL
       AND v.revisar_antes_de >= %s
       AND NOT EXISTS (
         SELECT 1
           FROM unnest(v.listings) AS l(id)
           JOIN jev_ficha_version o
             ON o.producto_id = v.producto_id
            AND o.plataforma = v.plataforma
            AND o.id <> v.id
            AND o.listings @> ARRAY[l.id]::bigint[]
            AND o.revisar_antes_de >= %s
            AND NOT EXISTS (
              SELECT 1 FROM jev_ficha_revocacion ro
               WHERE ro.ficha_version_id = o.id
            )
            AND (o.observado_at, o.created_at) > (v.observado_at, v.created_at)
       )
  ) g ON g.id = e.ficha_version_id
 WHERE e.relacion IN ('satisface', 'no_satisface')
"""

_SQL_LITERALES = """
SELECT h, min(texto) AS texto
  FROM (
    SELECT encode(sha256(convert_to(t.t, 'UTF8')), 'hex') AS h, t.t AS texto
      FROM jev_revision rv
      CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(rv.censos->'terminos', '[]'::jsonb)
      ) AS t(t)
    UNION ALL
    SELECT termino_sha256, termino FROM jev_senal
    UNION ALL
    SELECT encode(sha256(convert_to(d.search_term, 'UTF8')), 'hex'), d.search_term
      FROM jev_revision rv
      JOIN decision d ON d.id = rv.decision_id
     WHERE d.search_term IS NOT NULL
  ) textos
 WHERE h = ANY(%s)
 GROUP BY h
HAVING count(DISTINCT texto) = 1
"""


class _Configuracion(Exception):
    """Argumento o estado que impide medir: exit 2 con el motivo."""


class Par(NamedTuple):
    termino_sha256: str
    ficha_version_id: str
    relacion: str


def contrato_invertido(base=None):
    """El mismo contrato con opciones y criterios al reves.

    Cada par criterio sigue junto. El sha256 cambia, asi que el lado
    invertido no reutiliza juicios del lado normal.
    """
    base = contrato_por_defecto() if base is None else base
    return base.__class__(
        modelo=base.modelo,
        opciones=tuple(reversed(base.opciones)),
        criterios=tuple(reversed(base.criterios)),
        instrucciones=base.instrucciones,
        version=base.version,
        max_bytes_termino=base.max_bytes_termino,
        max_bytes_ficha=base.max_bytes_ficha,
    )


def listo_para_pagar(
    *,
    aplicar: bool,
    api_key: str,
    tope: int | None,
    pendientes: int,
) -> str:
    """seco, completo, sin_api_key, apagado o pagar.

    apagado es el job sin ajustes. El tope diario no bloquea esta medicion.
    """
    if pendientes == 0:
        return "completo"
    if not aplicar:
        return "seco"
    if not api_key:
        return "sin_api_key"
    if tope is None:
        return "apagado"
    return "pagar"


def accion_del_par(tiene_respuesta: bool, tiene_error: bool, tiene_intencion: bool) -> str:
    """saltar, fallo, abierta o pagar.

    Un resultado con error ya se pago: no se repite, y no se cuenta como
    saltada. Una intencion sin resultado tampoco se repite.
    """
    if tiene_respuesta:
        return "saltar"
    if tiene_error:
        return "fallo"
    if tiene_intencion:
        return "abierta"
    return "pagar"


def clasificar(normal: str, invertido: str | None, *, fallo: bool) -> str:
    """cambio, igual, abstencion, fallo o sin_respuesta."""
    if fallo:
        return "fallo"
    if invertido is None:
        return "sin_respuesta"
    if invertido == "informacion_insuficiente":
        return "abstencion"
    if invertido != normal:
        return "cambio"
    return "igual"


def aviso_muestra_congelada(n_pedido: int, n_muestra: int) -> str | None:
    """Aviso si --n no es el tamano de la muestra ya escrita. La revision
    es append-only: no se puede re-muestrear."""
    if n_muestra == n_pedido:
        return None
    return f"muestra congelada={n_muestra} n_pedido={n_pedido}"


def ids_de_fichas(pares: tuple[Par, ...] | list[Par]) -> list[str]:
    """Ids unicos. jev_revision rechaza duplicados en ficha_version_ids."""
    return sorted({par.ficha_version_id for par in pares})


def cupos(n: int, n_si: int, n_no: int) -> tuple[int, int]:
    """Cuantos satisface y no_satisface entran en una muestra de n.

    La mitad, con el extra del impar para satisface. Si una clase no
    alcanza, el resto sale de la otra. No inventa pares.
    """
    if n < 1:
        raise ValueError("n debe ser al menos 1")
    base_si = (n + 1) // 2
    base_no = n // 2
    toma_si = min(n_si, base_si)
    toma_no = min(n_no, base_no)
    sobra = n - toma_si - toma_no
    extra_si = min(sobra, n_si - toma_si)
    toma_si += extra_si
    sobra -= extra_si
    toma_no += min(sobra, n_no - toma_no)
    return toma_si, toma_no


def _repartir(pares: list[Par], cupo: int) -> list[Par]:
    """Round-robin por termino, fichas en orden de id. Un termino con
    muchos pares no se queda con la muestra."""
    if cupo < 1 or not pares:
        return []
    por_termino: dict[str, list[Par]] = {}
    for par in pares:
        por_termino.setdefault(par.termino_sha256, []).append(par)
    for grupo in por_termino.values():
        grupo.sort(key=lambda par: par.ficha_version_id)
    elegidos: list[Par] = []
    while len(elegidos) < cupo and any(por_termino.values()):
        for termino in sorted(por_termino):
            if len(elegidos) >= cupo:
                break
            grupo = por_termino[termino]
            if grupo:
                elegidos.append(grupo.pop(0))
    return elegidos


def elegir_muestra(pares: list[Par] | tuple[Par, ...], n: int) -> tuple[Par, ...]:
    """Muestra de hasta n pares decisivos, mezcla de las dos respuestas."""
    decisivos = [par for par in pares if par.relacion in ("satisface", "no_satisface")]
    si = [par for par in decisivos if par.relacion == "satisface"]
    no = [par for par in decisivos if par.relacion == "no_satisface"]
    toma_si, toma_no = cupos(n, len(si), len(no))
    return tuple(_repartir(si, toma_si) + _repartir(no, toma_no))


def pares_de_censos(censos: object) -> tuple[Par, ...]:
    """La muestra congelada en la revision. Otra forma es otro experimento."""
    if not isinstance(censos, dict) or censos.get("experimento") != "orden_opciones":
        raise ValueError("revision sin experimento orden_opciones")
    crudos = censos.get("pares")
    if not isinstance(crudos, list) or not crudos:
        raise ValueError("revision sin pares congelados")
    pares: list[Par] = []
    for fila in crudos:
        if not isinstance(fila, dict):
            raise ValueError("par congelado ilegible")
        try:
            par = Par(fila["termino_sha256"], fila["ficha_version_id"], fila["relacion_normal"])
        except KeyError as error:
            raise ValueError("par congelado incompleto") from error
        if par.relacion not in ("satisface", "no_satisface"):
            raise ValueError("par congelado sin respuesta decisiva")
        pares.append(par)
    return tuple(pares)


def _n(valor: str) -> int:
    numero = int(valor)
    if numero < 1:
        raise argparse.ArgumentTypeError("n debe ser al menos 1")
    return numero


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev_orden_opciones", description=__doc__)
    parser.add_argument("--n", type=_n, default=100, help="pares a pagar, mitad y mitad")
    parser.add_argument("--aplicar", action="store_true", help="sin esta bandera es seco")
    return parser


def _dominante(conn) -> str:
    """Contrato con mas exitos fuera de esta medicion.

    Los resultados invertidos viven en SOLICITUD. Si contaran, un empate
    con el contrato normal (la prueba siembra uno y uno) rechazaria la
    reanudacion.
    """
    filas = conn.execute(
        "SELECT contrato_sha256, count(*) FROM jev_par_evento"
        " WHERE tipo = 'resultado' AND respuesta IS NOT NULL"
        " AND revision_id <> %s"
        " GROUP BY contrato_sha256 ORDER BY count(*) DESC",
        (SOLICITUD,),
    ).fetchall()
    if not filas:
        raise _Configuracion("no hay resultados con respuesta")
    if len(filas) >= 2 and filas[0][1] == filas[1][1]:
        raise _Configuracion("empate entre contratos con exito")
    return filas[0][0]


def _elegibles(conn, contrato_sha: str, ahora: datetime) -> list[Par]:
    filas = conn.execute(_SQL_ELEGIBLES, (contrato_sha, ahora, ahora)).fetchall()
    return [Par(termino, ficha, relacion) for termino, ficha, relacion in filas]


def _literales(conn, hashes: list[str]) -> dict[str, str]:
    if not hashes:
        return {}
    filas = conn.execute(_SQL_LITERALES, (list(set(hashes)),)).fetchall()
    return {h: texto for h, texto in filas}


def _ajustes(conn):
    fila = conn.execute(
        "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if fila is None:
        return None
    return ajustes_desde_settings(fila[0], fila[1])


def _revision(conn):
    return conn.execute(
        "SELECT censos, contrato FROM jev_revision WHERE solicitud = %s",
        (SOLICITUD,),
    ).fetchone()


def _marca(conn, par: Par, contrato_sha: str) -> tuple[bool, bool, bool]:
    """respuesta, error, intencion. Sin filas, los tres en falso."""
    fila = conn.execute(
        "SELECT"
        " bool_or(tipo = 'resultado' AND respuesta IS NOT NULL),"
        " bool_or(tipo = 'resultado' AND respuesta IS NULL),"
        " bool_or(tipo = 'intencion')"
        " FROM jev_par_evento"
        " WHERE revision_id = %s AND termino_sha256 = %s"
        " AND ficha_version_id = %s AND contrato_sha256 = %s"
        " AND tipo IN ('intencion', 'resultado')",
        (SOLICITUD, par.termino_sha256, par.ficha_version_id, contrato_sha),
    ).fetchone()
    if fila is None or fila == (None, None, None):
        return False, False, False
    return bool(fila[0]), bool(fila[1]), bool(fila[2])


def _insertar_revision(conn, muestra: tuple[Par, ...], contrato, ahora: datetime) -> None:
    normal = contrato_por_defecto().sha256()
    pares = [
        {
            "ficha_version_id": par.ficha_version_id,
            "relacion_normal": par.relacion,
            "termino_sha256": par.termino_sha256,
        }
        for par in muestra
    ]
    censos = {
        "contrato_invertido": contrato.sha256(),
        "contrato_normal": normal,
        "experimento": "orden_opciones",
        "pares": pares,
    }
    plan = {
        "contrato_invertido": contrato.sha256(),
        "contrato_normal": normal,
        "experimento": "orden_opciones",
    }
    canon = _canonico(plan)
    conn.execute(
        "INSERT INTO jev_revision ("
        " solicitud, sujeto_tipo, plan_canonico, plan_sha256, fuentes_semillas,"
        " censos, ficha_version_ids, contrato, captured_at"
        ") VALUES (%s, 'semillas', %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s)",
        (
            SOLICITUD,
            canon,
            hashlib.sha256(canon.encode("utf-8")).hexdigest(),
            _canonico({"experimento": "orden_opciones", "tarea": "0.5"}),
            _canonico(censos),
            ids_de_fichas(muestra),
            _canonico(
                {
                    "modelo": contrato.modelo,
                    "opciones": list(contrato.opciones),
                    "sha256": contrato.sha256(),
                    "version": contrato.version,
                }
            ),
            ahora,
        ),
    )
    conn.commit()


def _resumen(
    imprimir, *, contrato, muestra, poblacion, pendientes, saltadas, abiertas, fallos, hoy, tope
):
    si = sum(par.relacion == "satisface" for par in muestra)
    no = sum(par.relacion == "no_satisface" for par in muestra)
    pob_si = sum(par.relacion == "satisface" for par in poblacion)
    pob_no = sum(par.relacion == "no_satisface" for par in poblacion)
    imprimir(
        f"contrato_normal={contrato_por_defecto().sha256()}"
        f" contrato_invertido={contrato.sha256()} solicitud={SOLICITUD}"
    )
    imprimir(f"elegibles={len(poblacion)} si={pob_si} no={pob_no}")
    imprimir(
        f"muestra={len(muestra)} si={si} no={no} pendientes={pendientes}"
        f" saltadas={saltadas} abiertas={abiertas} fallos={fallos}"
    )
    imprimir(f"intenciones_hoy={hoy} tope={tope if tope is not None else 'apagado'}")


def _correr(conn, args, imprimir) -> int:
    ahora = datetime.now(UTC)
    contrato = contrato_invertido()
    dominante = _dominante(conn)
    if dominante != contrato_por_defecto().sha256():
        raise _Configuracion("el contrato dominante no es el del codigo")
    poblacion = _elegibles(conn, dominante, ahora)
    guardada = _revision(conn)
    if guardada is None:
        muestra = elegir_muestra(poblacion, args.n)
        if not muestra:
            raise _Configuracion("no hay pares elegibles")
    else:
        muestra = pares_de_censos(guardada[0])
        invertido_guardado = (guardada[0] or {}).get("contrato_invertido")
        if invertido_guardado != contrato.sha256():
            raise _Configuracion("la revision congelada usa otro contrato")
        aviso = aviso_muestra_congelada(args.n, len(muestra))
        if aviso:
            imprimir(aviso)
    literales = _literales(conn, [par.termino_sha256 for par in muestra])
    faltan = sum(1 for par in muestra if par.termino_sha256 not in literales)
    if faltan:
        raise _Configuracion(f"faltan literales para {faltan} pares")
    fichas = fichas_por_id(conn, [uuid.UUID(par.ficha_version_id) for par in muestra])
    marcas = [_marca(conn, par, contrato.sha256()) for par in muestra]
    acciones = [
        accion_del_par(respuesta, error, intencion) for respuesta, error, intencion in marcas
    ]
    pendientes = sum(accion == "pagar" for accion in acciones)
    saltadas = sum(accion == "saltar" for accion in acciones)
    abiertas = sum(accion == "abierta" for accion in acciones)
    fallos = sum(accion == "fallo" for accion in acciones)
    hoy = intenciones_del_dia(conn, ahora)
    ajustes = _ajustes(conn)
    tope = ajustes.tope_diario if isinstance(ajustes, Ajustes) else None
    api_key = leer_api_key() if args.aplicar else ""
    decision = listo_para_pagar(
        aplicar=args.aplicar,
        api_key=api_key,
        tope=tope,
        pendientes=pendientes,
    )
    _resumen(
        imprimir,
        contrato=contrato,
        muestra=muestra,
        poblacion=poblacion,
        pendientes=pendientes,
        saltadas=saltadas,
        abiertas=abiertas,
        fallos=fallos,
        hoy=hoy,
        tope=tope,
    )
    if decision == "seco":
        imprimir("seco: nada escrito ni llamado; agrega --aplicar")
        return 0
    if decision == "completo":
        if abiertas or fallos:
            imprimir(f"sin pendientes; abiertas={abiertas} fallos={fallos}")
        else:
            imprimir("muestra completa, nada por pagar")
        return 0
    if decision == "sin_api_key":
        raise _Configuracion("falta la clave de TypeSafe")
    if decision == "apagado":
        raise _Configuracion("job apagado o sin ajustes de jev")
    if guardada is None:
        _insertar_revision(conn, muestra, contrato, ahora)

    def pedir(termino: str, ficha):
        return pedir_juicio(termino, ficha, contrato, api_key=api_key)

    libro = Libro(conn, pedir=pedir, contrato=contrato, ahora=lambda: datetime.now(UTC))
    conteo: Counter[str] = Counter()
    for indice, (par, accion) in enumerate(zip(muestra, acciones, strict=True), start=1):
        if accion != "pagar":
            conteo[accion] += 1
            continue
        devuelto = libro.pagar(
            literales[par.termino_sha256],
            fichas[uuid.UUID(par.ficha_version_id)],
            revision=SOLICITUD,
        )
        fallo = isinstance(devuelto, FalloProveedor)
        relacion = None if fallo else devuelto.relacion
        conteo[clasificar(par.relacion, relacion, fallo=fallo)] += 1
        if indice % 10 == 0 or indice == len(muestra):
            imprimir(f"avance {indice}/{len(muestra)}")
    imprimir("conteo " + " ".join(f"{clave}={conteo[clave]}" for clave in sorted(conteo)))
    return 0


def main(argv: list[str] | None = None, *, dsn: str | None = None, imprimir=print) -> int:
    args = _parser().parse_args(argv)
    dsn = dsn or os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print("falta ORBIT_DSN_ADMIN", file=sys.stderr)
        return SALIDA_CONFIGURACION
    try:
        with connect(dsn) as conn:
            return _correr(conn, args, imprimir)
    except _Configuracion as error:
        print(f"configuracion rechazada: {scrub(str(error))}", file=sys.stderr)
        return SALIDA_CONFIGURACION
    except (ValueError, psycopg.Error, OrbitDbError) as error:
        print(scrub(str(error)), file=sys.stderr)
        return SALIDA_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
