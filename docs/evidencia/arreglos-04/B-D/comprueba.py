"""Lectura final B-D.3: contraste de solo lectura del bloqueo reparado.

Recibe el inventario inicial (`antes.txt`), el final (`lee_amazon.py` de hoy),
el volcado del ledger (`lee_prod.sh`, solo SELECT), y los dos registros
(`reparacion.txt`, `revisar.txt`). No escribe nada.

Cierra en cero solo si:
- el recibo `FIN` de ambos inventarios cuadra con sus filas;
- cada veredicto inicial quedo reparado o documentado con su razon;
- cada origen reparado tiene `revertido` en el ledger, un veredicto nuevo
  enlazado a la senal con efecto `aplicado` y su id ENABLED en Amazon;
- ninguna clave ilegible al inicio aparece ahora PAUSED, ARCHIVED o AUSENTE
  sin su linea de relectura en `revisar.txt`;
- no aparecen vivos nuevos ni intentos pendientes sin explicar.
"""

from __future__ import annotations

import sys
from pathlib import Path

ESTADOS_RETIRADOS = frozenset({"PAUSED", "ARCHIVED", "AUSENTE"})


def _lineas(ruta: Path) -> list[str]:
    return [
        linea.strip() for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()
    ]


def _inventario(ruta: Path, errores: list[str]):
    filas: dict[str, dict] = {}
    reversas: list[str] = []
    fin = None
    for linea in _lineas(ruta):
        partes = linea.split("|")
        if partes[0] == "FIN":
            if len(partes) != 4:
                errores.append(f"{ruta.name}: recibo FIN malformado: {linea}")
            else:
                fin = tuple(int(x) for x in partes[1:])
            continue
        if partes[0] == "reversa_fallida":
            if len(partes) == 2:
                reversas.append(partes[1])
            else:
                errores.append(f"{ruta.name}: reversa_fallida malformada: {linea}")
            continue
        if partes[0] == "DUPLICADO" or partes[0].startswith("LECTURA_perfil"):
            errores.append(f"{ruta.name}: inventario invalido: {linea}")
            continue
        if len(partes) != 7 or partes[0] not in ("amazon_mx", "amazon_us"):
            errores.append(f"{ruta.name}: fila malformada: {linea}")
            continue
        vid = partes[1]
        if vid in filas:
            errores.append(f"{ruta.name}: veredicto duplicado: {vid}")
            continue
        filas[vid] = {
            "plataforma": partes[0],
            "grupo": partes[2],
            "kid": partes[3],
            "estado": partes[4].upper(),
            "senal": partes[5],
            "otro": partes[6],
        }
    _revisa_fin(ruta, filas, reversas, fin, errores)
    return filas, reversas, fin


def _revisa_fin(
    ruta: Path, filas: dict[str, dict], reversas: list[str], fin, errores: list[str]
) -> None:
    if fin is None:
        errores.append(f"{ruta.name}: falta el recibo FIN")
        return
    conteos = (
        sum(1 for f in filas.values() if f["plataforma"] == "amazon_mx"),
        sum(1 for f in filas.values() if f["plataforma"] == "amazon_us"),
        len(reversas),
    )
    if conteos != fin:
        errores.append(f"{ruta.name}: FIN {fin} no cuadra con filas unicas y reversas {conteos}")


def _registros(base: Path, errores: list[str]):
    reparados: dict[str, dict] = {}
    cabecera: dict[str, list[str]] = {}
    fin = None
    for linea in _lineas(base / "reparacion.txt"):
        partes = linea.split("|")
        if partes[0] == "REPARADO":
            if len(partes) != 8:
                errores.append(f"reparacion.txt: fila REPARADO malformada: {linea}")
                continue
            reparados[partes[2]] = {
                "plataforma": partes[1],
                "origen": partes[2],
                "senal": partes[3],
                "nuevo": partes[4],
                "kid": partes[5],
            }
        elif partes[0] in ("INVENTARIO", "CANDIDATOS"):
            cabecera[partes[0]] = partes[1:]
        elif partes[0] == "FIN_REPARACION":
            fin = partes[1:]
    if fin is None:
        errores.append("reparacion.txt: falta FIN_REPARACION")
    return reparados, cabecera, fin


def _revisar(base: Path, errores: list[str]):
    por_clave: dict[str, dict] = {}
    relecturas: dict[str, str] = {}
    for linea in _lineas(base / "revisar.txt"):
        partes = linea.split("|")
        if partes[0] == "relectura":
            if len(partes) != 6:
                errores.append(f"revisar.txt: relectura malformada: {linea}")
                continue
            relecturas[partes[2]] = partes[4]
            continue
        if len(partes) != 5:
            errores.append(f"revisar.txt: fila malformada: {linea}")
            continue
        por_clave[partes[1]] = {
            "plataforma": partes[0],
            "grupo": partes[2],
            "clase": partes[3],
            "motivo": partes[4],
        }
    return por_clave, relecturas


def _ledger(ruta: Path, errores: list[str]) -> dict[str, dict]:
    filas: dict[str, dict] = {}
    for linea in _lineas(ruta):
        partes = linea.split("|")
        if len(partes) != 10:
            errores.append(f"ledger: fila malformada: {linea}")
            continue
        vid, tipo, plat, grupo, sha, senal, paso, kid, motivo, origen = partes
        filas[vid] = {
            "tipo": tipo,
            "plataforma": plat,
            "grupo": grupo,
            "sha": sha,
            "senal": senal,
            "paso": paso,
            "kid": kid,
            "motivo": motivo,
            "origen": origen,
        }
    return filas


def _revisa_cabecera(cabecera: dict, inicial: dict, reparados: dict, errores: list[str]) -> None:
    mx = str(sum(1 for f in inicial.values() if f["plataforma"] == "amazon_mx"))
    us = str(sum(1 for f in inicial.values() if f["plataforma"] == "amazon_us"))
    if cabecera.get("INVENTARIO", [None, None])[:2] != [mx, us]:
        errores.append(f"reparacion.txt: INVENTARIO {cabecera.get('INVENTARIO')} no cuadra")
    candidatos = int(cabecera.get("CANDIDATOS", ["-1"])[0])
    if len(reparados) > candidatos:
        errores.append("reparacion.txt: mas reparados que candidatos")


def _revisa_reparado(reg: dict, ledger: dict, final: dict, errores: list[str]) -> None:
    origen = ledger.get(reg["origen"])
    if origen is None or origen["tipo"] != "aprobado":
        errores.append(f"origen ausente en ledger: {reg['origen']}")
        return
    if origen["paso"] != "revertido":
        errores.append(f"origen sin revertido: {reg['origen']} ({origen['paso']})")
    nuevo = ledger.get(reg["nuevo"])
    if nuevo is None:
        errores.append(f"reemplazo ausente en ledger: {reg['nuevo']}")
        return
    if nuevo["tipo"] != "aprobado" or nuevo["paso"] != "aplicado":
        errores.append(f"reemplazo sin aplicado: {reg['nuevo']} ({nuevo['tipo']}/{nuevo['paso']})")
    if (
        nuevo["plataforma"] != origen["plataforma"]
        or nuevo["grupo"] != origen["grupo"]
        or nuevo["sha"] != origen["sha"]
        or nuevo["senal"] != reg["senal"]
    ):
        errores.append(f"reemplazo no enlazado a la senal de la reparacion: {reg['nuevo']}")
    if str(nuevo["kid"]) != str(reg["kid"]):
        errores.append(f"reemplazo con otro id: {reg['nuevo']} {nuevo['kid']} != {reg['kid']}")
    en_final = final.get(reg["nuevo"])
    if en_final is None:
        errores.append(f"reemplazo no releido en Amazon: {reg['nuevo']}")
    elif en_final["estado"] != "ENABLED" or en_final["kid"] != str(reg["kid"]):
        errores.append(f"reemplazo no ENABLED en Amazon: {reg['nuevo']} ({en_final['estado']})")
    if reg["origen"] in final:
        errores.append(f"origen retirado sigue entre los vivos: {reg['origen']}")


def _revisa_retiros(
    inicial: dict, final: dict, reparados: dict, ledger: dict, errores: list[str]
) -> None:
    for reg in reparados.values():
        _revisa_reparado(reg, ledger, final, errores)
    retirados = {fila["origen"] for fila in ledger.values() if fila["tipo"] == "quitado"}
    for vid in inicial:
        if vid in final or vid in reparados:
            continue
        dueno = ledger.get(vid)
        if dueno is None:
            errores.append(f"origen desaparecido sin registro durable: {vid}")
        elif dueno["paso"] != "revertido":
            errores.append(f"origen desaparecido sin revertido: {vid} ({dueno['paso']})")
        elif vid not in retirados:
            errores.append(f"origen desaparecido sin retiro registrado: {vid}")


def _revisa_omitidos(inicial: dict, revisar: dict, ledger: dict, errores: list[str]) -> None:
    for vid, reg in revisar.items():
        fila = inicial.get(vid)
        if fila is None:
            dueno = ledger.get(vid)
            if dueno is None or dueno["paso"] != "reversa_fallida":
                errores.append(
                    f"omitido que no es un veredicto del inventario ni una reversa: {vid}"
                )
                continue
            if dueno["plataforma"] != reg["plataforma"] or dueno["grupo"] != reg["grupo"]:
                errores.append(f"reversa anotada con clave distinta al ledger: {vid}")
        elif reg["plataforma"] != fila["plataforma"] or reg["grupo"] != fila["grupo"]:
            errores.append(f"omitido con clave distinta a la inicial: {vid}")
        if reg["clase"] == "pendiente":
            errores.append(f"intento pendiente sin cerrar: {vid} ({reg['motivo']})")
        if not reg["motivo"].strip():
            errores.append(f"omitido sin razon: {vid}")


def _revisa_sin_registro(inicial: dict, reparados: dict, revisar: dict, errores: list[str]) -> None:
    for vid in inicial:
        if vid not in reparados and vid not in revisar:
            errores.append(f"veredicto inicial sin registro: {vid}")


def _revisa_ilegibles(inicial: dict, final: dict, relecturas: dict, errores: list[str]) -> None:
    for vid, fila in inicial.items():
        if not fila["estado"].startswith("LECTURA_"):
            continue
        actual = final.get(vid)
        if actual is None or actual["estado"] not in ESTADOS_RETIRADOS:
            continue
        if relecturas.get(vid) != actual["estado"]:
            errores.append(f"clave ilegible ahora {actual['estado']} sin relectura: {vid}")


def _revisa_nuevos(inicial: dict, final: dict, reparados: dict, errores: list[str]) -> None:
    nuevos = {reg["nuevo"] for reg in reparados.values()}
    for vid in final:
        if vid not in inicial and vid not in nuevos:
            errores.append(f"vivo nuevo sin reparacion registrada: {vid}")


def _revisa_reversas(
    reversas_ini: list[str], reversas_fin: list[str], revisar: dict, errores: list[str]
) -> None:
    for vid in reversas_ini:
        if vid not in revisar:
            errores.append(f"reversa_fallida inicial sin registro en revisar.txt: {vid}")
    for vid in reversas_fin:
        if vid not in reversas_ini and vid not in revisar:
            errores.append(f"reversa_fallida nueva sin registro: {vid}")


def _revisa_conteos(
    fin_rep: list[str] | None, reparados: dict, revisar: dict, errores: list[str]
) -> None:
    if fin_rep is None:
        return
    reparados_n, omitidos_n, pendientes_n = (int(x) for x in fin_rep)
    escritos = sum(1 for r in revisar.values() if r["clase"] in ("omitido", "pendiente"))
    if reparados_n != len(reparados):
        errores.append(f"FIN_REPARACION reparados {reparados_n} != {len(reparados)}")
    if omitidos_n + pendientes_n != escritos:
        errores.append(f"FIN_REPARACION omitidos {omitidos_n}+{pendientes_n} != revisar {escritos}")
    if pendientes_n != 0:
        errores.append(f"FIN_REPARACION deja {pendientes_n} pendientes")


def comprueba(base: Path, final_path: Path, ledger_path: Path) -> list[str]:
    errores: list[str] = []
    inicial, reversas_ini, _fin_ini = _inventario(base / "antes.txt", errores)
    final, reversas_fin, _fin_fin = _inventario(final_path, errores)
    reparados, cabecera, fin_rep = _registros(base, errores)
    revisar, relecturas = _revisar(base, errores)
    ledger = _ledger(ledger_path, errores)
    if errores:
        return errores
    _revisa_cabecera(cabecera, inicial, reparados, errores)
    _revisa_retiros(inicial, final, reparados, ledger, errores)
    _revisa_omitidos(inicial, revisar, ledger, errores)
    _revisa_sin_registro(inicial, reparados, revisar, errores)
    _revisa_ilegibles(inicial, final, relecturas, errores)
    _revisa_nuevos(inicial, final, reparados, errores)
    _revisa_reversas(reversas_ini, reversas_fin, revisar, errores)
    _revisa_conteos(fin_rep, reparados, revisar, errores)
    return errores


def main(argv: list[str]) -> int:
    base = Path(argv[1]) if len(argv) > 1 else Path("docs/evidencia/arreglos-04/B-D")
    final_path = Path(argv[2]) if len(argv) > 2 else base / "final.txt"
    ledger_path = Path(argv[3]) if len(argv) > 3 else base / "ledger.txt"
    errores = comprueba(base, final_path, ledger_path)
    for error in errores:
        print(f"FALLA|{error}")
    conteos = {"mx": "-", "us": "-", "reversas": "-"}
    for linea in _lineas(final_path):
        partes = linea.split("|")
        if partes[0] == "FIN" and len(partes) == 4:
            conteos = {"mx": partes[1], "us": partes[2], "reversas": partes[3]}
    estado = "falla" if errores else "ok"
    print(
        f"COMPRUEBA|{estado}|mx={conteos['mx']}|us={conteos['us']}|reversas={conteos['reversas']}"
    )
    return 1 if errores else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
