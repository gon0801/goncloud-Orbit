"""PEZ 01: nueve correspondencias MeLi verificadas, con reversa y sin HTTP.

Uso: python -m tools.corrige_meli_skus lote.json [--dry-run]
     python -m tools.corrige_meli_skus lote.json --apply --respaldo /ruta/nueva.json
     python -m tools.corrige_meli_skus --revertir /ruta/nueva.json
DSN exclusivamente en ORBIT_DSN_ADMIN; el ensayo es el modo predeterminado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.db import connect
from app.redaction import redact_dsn, scrub


class ErrorLote(ValueError):
    """La identidad, el costo o el estado esperado dejaron de ser validos."""


def _exige(condicion, mensaje):
    if not condicion:
        raise ErrorLote(mensaje)


def _entero(valor):
    return type(valor) is int and 0 < valor < 2**63


def _texto(valor, vacio=False):
    return isinstance(valor, str) and (vacio or bool(valor.strip())) and "\x00" not in valor


def validar_lote(lote):
    """Valida la frontera JSON antes de consultar o bloquear Postgres."""
    _exige(isinstance(lote, dict) and lote.get("version") == 1, "version de lote invalida")
    filas = lote.get("filas")
    _exige(isinstance(filas, list) and len(filas) == 9, "el lote exige exactamente nueve filas")
    claves, skus = set(), set()
    for f in filas:
        _exige(isinstance(f, dict), "fila ilegible")
        for campo in ("listing_id", "product_id_destino"):
            _exige(_entero(f.get(campo)), f"{campo} invalido")
        _exige(
            "product_id_antes" in f and f["product_id_antes"] is None,
            "la reparacion exige product_id_antes NULL explicito",
        )
        _exige(type(f.get("activo_antes")) is bool, "activo_antes exige booleano explicito")
        for campo in ("item_id", "seller_sku", "odoo_sku", "evidencia"):
            _exige(_texto(f.get(campo)), f"{campo} ausente o invalido")
        _exige(f["seller_sku"] == f["seller_sku"].strip(), "seller_sku debe estar limpio")
        _exige(_texto(f.get("variante_ext"), vacio=True), "variante_ext invalida")
        clave = (f["listing_id"], f["variante_ext"])
        _exige(clave not in claves and f["seller_sku"] not in skus, "identidad o SKU repetido")
        claves.add(clave)
        skus.add(f["seller_sku"])
        c = f.get("costo")
        _exige(isinstance(c, dict) and _entero(c.get("id")), "costo esperado ausente")
        _exige(isinstance(c.get("importe"), str), "importe exige cadena decimal")
        try:
            importe = Decimal(c["importe"])
            _exige(
                importe.is_finite()
                and 0 < importe < Decimal("10000000000")
                and importe == importe.quantize(Decimal("0.0001")),
                "importe invalido",
            )
            date.fromisoformat(c["valid_from"])
            _exige("valid_to" in c, "valid_to esperado ausente")
            if c["valid_to"] is not None:
                date.fromisoformat(c["valid_to"])
        except (InvalidOperation, ValueError, TypeError, KeyError):
            raise ErrorLote("costo o vigencia ilegible") from None
        _exige(
            c.get("moneda") == "MXN" and type(c.get("includes_tax")) is bool,
            "moneda o tratamiento fiscal invalido",
        )
    return sorted(filas, key=lambda f: (f["listing_id"], f["variante_ext"]))


def _bloquear(conn):
    """Locks breves: no hay fila que bloquear para un alias todavia ausente.

    El orden fijo impide phantoms de SKU, miembros y costos mientras se
    comprueban y cambian nueve filas. Los timeouts hacen abortar ante el cron.
    No se escribe fuera del lote aunque las tablas se bloqueen completas.
    """
    conn.execute("SET LOCAL lock_timeout = '5s'")
    conn.execute("SET LOCAL statement_timeout = '15s'")
    conn.execute("SET LOCAL TIME ZONE 'UTC'")
    conn.execute("SELECT public.meli_sku_alias_bloquear()")


def _estado(conn, f):
    listing = conn.execute(
        "SELECT to_jsonb(l) || jsonb_build_object('listing_price',listing_price::text) "
        "FROM listing l WHERE id=%s",
        (f["listing_id"],),
    ).fetchone()
    miembros = conn.execute(
        "SELECT to_jsonb(m) FROM listing_miembro m WHERE seller_sku=%s "
        "OR (listing_id=%s AND variante_ext=%s) ORDER BY listing_id,variante_ext FOR UPDATE",
        (f["seller_sku"], f["listing_id"], f["variante_ext"]),
    ).fetchall()
    _exige(listing is not None and len(miembros) == 1, "cardinalidad de listing/miembro distinta")
    ancla, m = listing[0], miembros[0][0]
    _exige(
        ancla["platform"] == "meli" and ancla["external_id"] == f["item_id"],
        "identidad item distinta",
    )
    _exige(
        m["platform"] == "meli"
        and m["listing_id"] == f["listing_id"]
        and m["variante_ext"] == f["variante_ext"]
        and m["seller_sku"] == f["seller_sku"]
        and m["activo"] == f["activo_antes"],
        "identidad o actividad del miembro distinta",
    )
    alias = conn.execute(
        "SELECT to_jsonb(a) FROM meli_sku_alias a WHERE seller_sku=%s FOR UPDATE",
        (f["seller_sku"],),
    ).fetchone()
    return {"listing": ancla, "miembro": m, "alias": None if alias is None else alias[0]}


def _costo(conn, f):
    producto = conn.execute(
        "SELECT odoo_sku,active FROM product WHERE id=%s", (f["product_id_destino"],)
    ).fetchone()
    _exige(producto == (f["odoo_sku"], True), "producto destino distinto o inactivo")
    conflicto = conn.execute(
        "SELECT id FROM product WHERE odoo_sku=%s", (f["seller_sku"],)
    ).fetchone()
    _exige(
        conflicto is None or conflicto[0] == f["product_id_destino"], "SKU canonico contradictorio"
    )
    costos = conn.execute(
        "SELECT id,cost_amount,cost_currency,includes_tax,valid_from,valid_to FROM sku_cost "
        "WHERE product_id=%s AND valid_from <= (now() AT TIME ZONE 'UTC')::date "
        "AND (valid_to IS NULL OR valid_to > (now() AT TIME ZONE 'UTC')::date)",
        (f["product_id_destino"],),
    ).fetchall()
    c = f["costo"]
    esperado = (
        c["id"],
        Decimal(c["importe"]),
        c["moneda"],
        c["includes_tax"],
        date.fromisoformat(c["valid_from"]),
        None if c["valid_to"] is None else date.fromisoformat(c["valid_to"]),
    )
    _exige(costos == [esperado], "costo vigente UTC distinto del esperado")


def _huella(datos):
    return hashlib.sha256(
        json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _guardar(ruta, datos):
    """Respaldo durable exclusivo y privado, antes del COMMIT; nunca sobrescribe."""
    datos["sha256"] = _huella(datos)
    contenido = (json.dumps(datos, indent=2, ensure_ascii=False) + "\n").encode()
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as archivo:
            os.fchmod(archivo.fileno(), 0o600)
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        directorio = os.open(Path(ruta).resolve().parent, os.O_RDONLY)
        try:
            os.fsync(directorio)
        finally:
            os.close(directorio)
    except BaseException:
        Path(ruta).unlink(missing_ok=True)
        raise
    return datos["sha256"]


def aplicar(conn, lote, *, respaldo=None, apply=False):
    """Valida todo antes de escribir; cualquier deriva revierte la transaccion."""
    _exige(conn.autocommit, "la herramienta exige conexion propia autocommit")
    filas = validar_lote(lote)
    _exige(not apply or respaldo is not None, "apply exige ruta de respaldo nueva")
    with conn.transaction():
        _bloquear(conn)
        antes = []
        for f in filas:
            estado = _estado(conn, f)
            _exige(
                estado["miembro"]["product_id"] is None and estado["alias"] is None,
                "estado previo distinto: lote ya aplicado o deriva; no se escribe",
            )
            _costo(conn, f)
            antes.append(estado)
        if not apply:
            return {"estado": "dry-run", "miembros": 9, "aliases": 9}
        despues = []
        for f in filas:
            conn.execute(
                "INSERT INTO meli_sku_alias(seller_sku,product_id,evidencia) VALUES (%s,%s,%s)",
                (f["seller_sku"], f["product_id_destino"], f["evidencia"]),
            )
            cambiado = conn.execute(
                "UPDATE listing_miembro SET product_id=%s WHERE listing_id=%s "
                "AND variante_ext=%s AND product_id IS NULL",
                (f["product_id_destino"], f["listing_id"], f["variante_ext"]),
            )
            _exige(cambiado.rowcount == 1, "cardinalidad inesperada al actualizar")
            despues.append(_estado(conn, f))
        digest = _guardar(
            respaldo, {"version": 1, "lote": lote, "antes": antes, "despues": despues}
        )
    return {"estado": "aplicado", "miembros": 9, "aliases": 9, "respaldo_sha256": digest}


def revertir(conn, respaldo):
    """Restaura solo si cada miembro y alias siguen exactamente como se aplicaron."""
    _exige(conn.autocommit, "la herramienta exige conexion propia autocommit")
    datos = json.loads(Path(respaldo).read_text())
    digest = datos.pop("sha256", None)
    _exige(digest == _huella(datos) and datos.get("version") == 1, "respaldo corrupto")
    filas = validar_lote(datos["lote"])
    _exige(len(datos["antes"]) == len(datos["despues"]) == 9, "respaldo incompleto")
    with conn.transaction():
        _bloquear(conn)
        for f, antes, despues in zip(filas, datos["antes"], datos["despues"], strict=True):
            _exige(
                antes["alias"] is None and antes["miembro"]["product_id"] is None,
                "respaldo no corresponde a una reparacion inicial",
            )
            esperado = json.loads(json.dumps(antes))
            esperado["miembro"]["product_id"] = f["product_id_destino"]
            esperado["alias"] = despues["alias"]
            _exige(
                esperado == despues
                and despues["alias"]["product_id"] == f["product_id_destino"]
                and despues["alias"]["seller_sku"] == f["seller_sku"]
                and despues["alias"]["evidencia"] == f["evidencia"],
                "respaldo incoherente",
            )
            _exige(
                _estado(conn, f) == despues,
                "estado aplicado distinto: deriva o lote ya revertido; no se escribe",
            )
        for f in filas:
            cambio = conn.execute(
                "UPDATE listing_miembro SET product_id=NULL WHERE listing_id=%s "
                "AND variante_ext=%s AND product_id=%s",
                (f["listing_id"], f["variante_ext"], f["product_id_destino"]),
            )
            borrado = conn.execute(
                "DELETE FROM meli_sku_alias WHERE seller_sku=%s AND product_id=%s AND evidencia=%s",
                (f["seller_sku"], f["product_id_destino"], f["evidencia"]),
            )
            _exige(cambio.rowcount == borrado.rowcount == 1, "cardinalidad inesperada en reversa")
    return {"estado": "revertido", "miembros": 9, "aliases": 9, "respaldo_sha256": digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lote", nargs="?", type=Path)
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument("--dry-run", action="store_true")
    modos.add_argument("--apply", action="store_true")
    modos.add_argument("--revertir", type=Path)
    parser.add_argument("--respaldo", type=Path)
    args = parser.parse_args(argv)
    if args.revertir:
        if args.lote or args.respaldo:
            parser.error("--revertir solo recibe el respaldo existente")
    elif args.lote is None or (args.apply and args.respaldo is None):
        parser.error("se requiere lote; --apply exige --respaldo nuevo")
    elif args.respaldo is not None and not args.apply:
        parser.error("--respaldo solo corresponde a --apply")
    dsn = os.environ.get("ORBIT_DSN_ADMIN", "")
    if not dsn:
        print("falta ORBIT_DSN_ADMIN", file=sys.stderr)
        return 2
    redact_dsn(dsn)
    try:
        with connect(dsn, autocommit=True, connect_timeout=15) as conn:
            resultado = (
                revertir(conn, args.revertir)
                if args.revertir
                else aplicar(
                    conn,
                    json.loads(args.lote.read_text()),
                    respaldo=args.respaldo,
                    apply=args.apply,
                )
            )
        print(json.dumps(resultado, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"operacion abortada: {scrub(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
