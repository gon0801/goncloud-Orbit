"""Reparacion B-D.2 de los bloqueos que Amazon no confirma (camino normal).

Corre dentro del contenedor con el codigo desplegado (ver `repara.sh`). Lee el
inventario de `antes.txt` por `B_D_ANTES_B64`, concilia cada candidato con el
registro durable y con una relectura completa de su ad group, y solo entonces
usa `bloqueo_dueno_efecto.quitar` y `.aprobar` con sus precondiciones de
reparacion (B-B). Nunca manda un borrado ni un POST que el servicio no haya
releido y confirmado.

Salida por stdout, una linea por evento, para que el wrapper llene los dos
archivos del bloque:

  R|<fila de reparacion.txt>        resultados por veredicto
  V|<fila de revisar.txt>           omitidos y su razon
  E|<detalle>                       error que impide cerrar (stderr tambien)

Cero candidatos es valido: se registran los conteos y `revisar.txt` queda con
la razon de cada veredicto no tocado.
"""

from __future__ import annotations

import base64
import os
import sys
from dataclasses import dataclass
from uuid import UUID

from app import bloqueo_dueno_efecto as bde
from app.db import connect

CANDIDATOS = frozenset({"PAUSED", "ARCHIVED", "AUSENTE"})


@dataclass(frozen=True)
class Fila:
    plataforma: str
    vid: str
    grupo: int
    kid: str
    estado: str
    senal: str
    otro: str


def parsea(texto: str) -> tuple[list[Fila], tuple[int, int, int] | None]:
    filas: list[Fila] = []
    fin = None
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = linea.split("|")
        if partes[0] == "FIN" and len(partes) == 4:
            fin = (int(partes[1]), int(partes[2]), int(partes[3]))
            continue
        if len(partes) != 7 or partes[0] not in ("amazon_mx", "amazon_us"):
            continue
        filas.append(
            Fila(
                plataforma=partes[0],
                vid=partes[1],
                grupo=int(partes[2]),
                kid=partes[3],
                estado=partes[4].upper(),
                senal=partes[5],
                otro=partes[6],
            )
        )
    return filas, fin


def clasifica(fila: Fila) -> tuple[str, str]:
    """("candidato", "") o ("omitido", motivo). Solo lo que Amazon no bloquea."""
    if fila.estado.startswith("LECTURA_") or fila.estado == "SIN_ID":
        return ("omitido", f"lectura_{fila.estado}")
    if fila.estado == "ENABLED":
        return ("omitido", "amazon_enabled")
    if fila.estado not in CANDIDATOS:
        return ("omitido", f"estado_desconocido_{fila.estado.lower() or 'vacio'}")
    if fila.senal == "-":
        return ("omitido", "sin_senal_vigente")
    if fila.otro != "-":
        return ("omitido", "otro_enabled_en_inventario")
    return ("candidato", "")


def _senal_valida(conn, fila: Fila, origen) -> bool:
    """La senal del inventario sigue siendo la vigente ajena de esta clave."""
    try:
        senal = bde._senal(conn, UUID(fila.senal))
    except ValueError:
        return False
    return senal is not None and bde._misma_clave(senal, origen) and bde._vigente_ajena(conn, senal)


def _otro_enabled_texto(items: list[dict], grupo_ext: str, termino: str, kid: str) -> str | None:
    """Guardia extra: un ENABLED del mismo texto que difiera solo en mayusculas.

    `_coincidencias` compara el texto literal; si Amazon devuelve otra caja,
    el servicio no lo ve y aprobaria un segundo negative del mismo termino.
    """
    for item in items:
        if str(item.get("adGroupId")) != str(grupo_ext):
            continue
        if str(item.get("state", "")).upper() != "ENABLED":
            continue
        if str(item.get("keywordId")) == str(kid):
            continue
        if str(item.get("keywordText", "")).casefold() != termino.casefold():
            continue
        if str(item.get("matchType", "")).casefold().replace("negative_", "") != "exact":
            continue
        return str(item.get("keywordId"))
    return None


def _estado(items: list[dict], kid: str) -> tuple[str, dict | None]:
    item = bde._por_id(items, kid)
    return ("AUSENTE" if item is None else str(item.get("state", "")).upper(), item)


def _identidad(item: dict, grupo_ext: str, termino: str) -> bool:
    return (
        str(item.get("adGroupId", "")) == str(grupo_ext)
        and item.get("keywordText") == termino
        and str(item.get("matchType", "")).casefold().replace("negative_", "") == "exact"
    )


def _lectura(cliente, grupo_ext: str) -> tuple[list, str]:
    return bde._lista_o(cliente, grupo_ext)


def _candidato(conn, fila: Fila) -> tuple[str, str, dict]:
    """Devuelve (paso, motivo, datos): paso en {listo, omitido, pendiente}."""
    try:
        origen = bde._aprobado(conn, UUID(fila.vid))
    except ValueError:
        return ("omitido", "veredicto_ilegible", {})
    if origen is None:
        return ("omitido", "origen_ausente", {})
    if (
        origen.plataforma != fila.plataforma
        or origen.ad_group_id != fila.grupo
        or not origen.termino
        or not origen.termino_sha256
    ):
        return ("omitido", "identidad_cambio", {})
    kid = bde._id_del_ledger(conn, origen.id)
    if kid is None or str(kid) != str(fila.kid):
        return ("omitido", "identidad_cambio", {})
    senal = fila.senal
    if not _senal_valida(conn, fila, origen):
        return ("omitido", "senal_cambio", {})
    externos = bde._externos(conn, fila.grupo)
    if externos is None:
        return ("omitido", "sin_externos", {})
    grupo_ext = externos[0]
    try:
        cliente = bde._cliente(fila.plataforma, None)
    except Exception:
        return ("omitido", "sin_respuesta", {})
    datos = {
        "origen": origen,
        "kid": kid,
        "senal": senal,
        "grupo_ext": grupo_ext,
        "cliente": cliente,
    }
    ultimo = bde._ultimo(conn, origen.id)
    if ultimo is not None and ultimo[0] in ("intencion", "reversa_intencion"):
        try:
            conciliado = bde.reconciliar(
                conn, ad_group_id=fila.grupo, termino_sha256=origen.termino_sha256
            )
        except Exception:
            return ("pendiente", "reconciliacion_error", datos)
        if conciliado.motivo in ("candado", "pendiente"):
            return ("omitido", f"reconciliacion_{conciliado.motivo}", datos)
        ultimo = bde._ultimo(conn, origen.id)
    if ultimo is not None and ultimo[0] == "revertido":
        return ("listo", "ya_revertido", datos)
    if ultimo is not None and ultimo[0] == "reversa_fallida":
        return ("omitido", "reversa_fallida", datos)
    if ultimo is None or ultimo[0] != "aplicado" or str(ultimo[1]) != str(kid):
        return ("omitido", "origen_no_aplicado", datos)
    items, estado = _lectura(cliente, grupo_ext)
    if estado != "ok":
        return ("pendiente", f"lectura_{estado}", datos)
    estado_actual, item = _estado(items, kid)
    if item is not None and not _identidad(item, grupo_ext, origen.termino):
        return ("omitido", "identidad_cambio", datos)
    if estado_actual != fila.estado:
        return ("omitido", "estado_cambio", datos)
    if bde._negative_que_bloquea(items, grupo_ext, origen.termino) is not None:
        return ("omitido", "otro_enabled", datos)
    otro = _otro_enabled_texto(items, grupo_ext, origen.termino, kid)
    if otro is not None:
        return ("omitido", f"otro_enabled_texto_{otro}", datos)
    return ("listo", "", datos)


def _repara(conn, fila: Fila) -> tuple[str, str, dict]:
    paso, motivo, datos = _candidato(conn, fila)
    if paso != "listo":
        return (paso, motivo, datos)
    try:
        return _ejecuta_reparacion(conn, fila, motivo, datos)
    except Exception as exc:
        return ("pendiente", f"error_inesperado_{type(exc).__name__}", datos)


def _ejecuta_reparacion(conn, fila: Fila, motivo: str, datos: dict) -> tuple[str, str, dict]:
    origen = datos["origen"]
    kid = datos["kid"]
    senal = datos["senal"]
    grupo_ext = datos["grupo_ext"]
    cliente = datos["cliente"]
    if motivo != "ya_revertido":
        pre = bde.PrecondicionQuitar(
            plataforma=fila.plataforma,
            ad_group_id=fila.grupo,
            termino=origen.termino,
            negative_keyword_id=str(kid),
            estado_observado=fila.estado,
            senal_id=UUID(senal),
        )
        quitado = bde.quitar(conn, origen.id, precondicion=pre)
        if quitado.paso != "revertido":
            paso_final = "pendiente" if quitado.paso == "reversa_intencion" else "omitido"
            motivo_final = f"quitar_{quitado.paso or 'sin_paso'}:{quitado.motivo}"
            return (paso_final, motivo_final, datos)
    pre_ap = bde.PrecondicionAprobar(
        origen_id=origen.id,
        plataforma=fila.plataforma,
        ad_group_id=fila.grupo,
        termino=origen.termino,
    )
    nuevo = bde.aprobar(conn, UUID(senal), precondicion=pre_ap)
    if nuevo.paso != "aplicado":
        return ("pendiente", f"aprobar_{nuevo.paso}:{nuevo.motivo}", datos)
    kid_nuevo = bde._id_del_ledger(conn, nuevo.veredicto_id)
    if kid_nuevo is None:
        return ("pendiente", "reemplazo_sin_id", datos)
    items, estado = _lectura(cliente, grupo_ext)
    if estado != "ok":
        return ("pendiente", f"reemplazo_lectura_{estado}", datos)
    vivo = bde._negative_por_id(items, grupo_ext, origen.termino, kid_nuevo)
    if vivo is None:
        otro = _otro_enabled_texto(items, grupo_ext, origen.termino, kid_nuevo)
        motivo = f"reemplazo_no_enabled_otro_{otro}" if otro else "reemplazo_no_enabled"
        return ("pendiente", motivo, datos)
    datos["nuevo_vid"] = str(nuevo.veredicto_id)
    datos["kid_nuevo"] = kid_nuevo
    return ("reparado", "", datos)


def _relee_desconocidas(conn, filas: list[Fila]) -> list[str]:
    """Relee al final cada clave del inventario que no se pudo leer."""
    lineas: list[str] = []
    for fila in filas:
        if not fila.estado.startswith("LECTURA_"):
            continue
        try:
            origen = bde._aprobado(conn, UUID(fila.vid))
        except ValueError:
            lineas.append(f"{fila.plataforma}|{fila.vid}|{fila.grupo}|veredicto_ilegible|sin_tocar")
            continue
        if origen is None or not origen.termino_sha256:
            lineas.append(f"{fila.plataforma}|{fila.vid}|{fila.grupo}|origen_ausente|sin_tocar")
            continue
        externos = bde._externos(conn, fila.grupo)
        if externos is None:
            lineas.append(f"{fila.plataforma}|{fila.vid}|{fila.grupo}|sin_externos|sin_tocar")
            continue
        try:
            cliente = bde._cliente(fila.plataforma, None)
        except Exception:
            lineas.append(f"{fila.plataforma}|{fila.vid}|{fila.grupo}|sin_respuesta|sin_tocar")
            continue
        items, estado = _lectura(cliente, externos[0])
        if estado != "ok":
            lineas.append(f"{fila.plataforma}|{fila.vid}|{fila.grupo}|LECTURA_{estado}|sin_tocar")
            continue
        estado_actual, _item = _estado(items, fila.kid)
        lineas.append(f"{fila.plataforma}|{fila.vid}|{fila.grupo}|{estado_actual}|sin_tocar")
    return lineas


def main() -> int:
    texto = base64.b64decode(os.environ["B_D_ANTES_B64"]).decode("utf-8")
    filas, fin = parsea(texto)
    if fin is None:
        print("E|inventario sin recibo FIN", file=sys.stderr)
        return 2
    conteos = {"amazon_mx": 0, "amazon_us": 0}
    for fila in filas:
        conteos[fila.plataforma] = conteos.get(fila.plataforma, 0) + 1
    if (conteos["amazon_mx"], conteos["amazon_us"]) != fin[:2]:
        print(f"E|inventario no cuadra con FIN {fin} vs {conteos}", file=sys.stderr)
        return 2

    reparados = pendientes = omitidos = 0
    clasificadas = [(f, *clasifica(f)) for f in filas]
    candidatos = sorted(
        (f for f, paso, _motivo in clasificadas if paso == "candidato"),
        key=lambda f: (f.plataforma, f.grupo, f.vid),
    )
    orden = {f.vid: motivo for f, paso, motivo in clasificadas if paso == "omitido"}

    with connect(os.environ["ORBIT_DSN_ADMIN"]) as conn:
        print(f"R|INVENTARIO|{conteos['amazon_mx']}|{conteos['amazon_us']}|{fin[2]}")
        print(f"R|CANDIDATOS|{len(candidatos)}")
        for fila in candidatos:
            paso, motivo, datos = _repara(conn, fila)
            senal = datos.get("senal", fila.senal)
            if paso == "reparado":
                reparados += 1
                print(
                    f"R|REPARADO|{fila.plataforma}|{fila.vid}|{senal}|"
                    f"{datos['nuevo_vid']}|{datos['kid_nuevo']}|revertido|aplicado"
                )
            elif paso == "pendiente":
                pendientes += 1
                print(f"V|{fila.plataforma}|{fila.vid}|{fila.grupo}|pendiente|{motivo}")
            else:
                omitidos += 1
                print(f"V|{fila.plataforma}|{fila.vid}|{fila.grupo}|omitido|{motivo}")
        for fila in filas:
            motivo = orden.get(fila.vid)
            if motivo is not None:
                omitidos += 1
                print(f"V|{fila.plataforma}|{fila.vid}|{fila.grupo}|omitido|{motivo}")
        for linea in _relee_desconocidas(conn, filas):
            print(f"V|relectura|{linea}")
        print(f"R|FIN_REPARACION|{reparados}|{omitidos}|{pendientes}")
    return 0 if pendientes == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
