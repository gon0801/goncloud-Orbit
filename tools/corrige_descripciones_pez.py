"""P13: nueve descripciones PEZ aprobadas, con respaldo, readback y reversa.

python -m tools.corrige_descripciones_pez lote.json [--dry-run]
python -m tools.corrige_descripciones_pez lote.json --apply --respaldo /ruta/nueva.json
El filtro --item MLM... es repetible; preflight y respaldo siempre cubren nueve.
python -m tools.corrige_descripciones_pez --revertir /ruta/respaldo.json
Solo usa el access_token de ORBIT_SECRETS_DIR, sin refrescarlo ni reintentarlo.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx

from app.redaction import register_secret, scrub
from app.reputacion_clientes import MeliCredentials

ITEMS = frozenset(
    {
        "MLM2163407464",
        "MLM1890653227",
        "MLM4735255462",
        "MLM2724304859",
        "MLM2727437671",
        "MLM2727363007",
        "MLM2727328203",
        "MLM4749600210",
        "MLM4763716652",
    }
)
NOTA_PEZ = "Variante Sello cristiano (PEZ): arras de 22 mm de di\u00e1metro."


class ErrorDescripcion(ValueError):
    """Se detiene el lote sin pisar un estado desconocido."""


def _exige(condicion, mensaje):
    if not condicion:
        raise ErrorDescripcion(mensaje)


class ClienteDescripciones:
    """Allowlist puntual, host fijo, timeout 15s, cero redirects/retries/refresh."""

    def __init__(self, creds, *, transport=None):
        register_secret(creds.access_token)
        self._http = httpx.Client(
            base_url="https://api.mercadolibre.com",
            headers={"Authorization": f"Bearer {creds.access_token}"},
            transport=transport,
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self):
        self._http.close()

    def _ruta(self, item):
        _exige(item in ITEMS, "item fuera de los nueve autorizados")
        return f"/items/{item}"

    def _get(self, ruta, **kwargs):
        try:
            respuesta = self._http.get(ruta, **kwargs)
            _exige(respuesta.status_code == 200, f"GET {ruta}: HTTP {respuesta.status_code}")
            datos = respuesta.json()
            _exige(isinstance(datos, dict), f"GET {ruta}: respuesta ilegible")
            return datos
        except (httpx.HTTPError, ValueError) as exc:
            raise ErrorDescripcion(scrub(str(exc))) from None

    def usuario(self):
        return self._get("/users/me")

    def item(self, item):
        return self._get(self._ruta(item), params={"include_attributes": "all"})

    def descripcion(self, item):
        datos = self._get(self._ruta(item) + "/description")
        texto = datos.get("plain_text")
        _exige(isinstance(texto, str), f"{item}: plain_text ausente")
        return texto

    def poner(self, item, texto):
        """Un PUT exacto; timeout/5xx solo habilitan lectura de reconciliacion."""
        _exige(isinstance(texto, str) and texto.strip(), "plain_text vacio")
        try:
            respuesta = self._http.put(
                self._ruta(item) + "/description", json={"plain_text": texto}
            )
        except httpx.HTTPError:
            return "incierto"
        if respuesta.status_code >= 500:
            return "incierto"
        _exige(
            200 <= respuesta.status_code < 300,
            f"{item}: PUT HTTP {respuesta.status_code}, sin reintento",
        )
        return "aceptado"


def _hash(datos):
    return hashlib.sha256(
        json.dumps(datos, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def validar_lote(lote):
    _exige(
        isinstance(lote, dict)
        and lote.get("version") == 1
        and lote.get("confirmacion22mm") is True,
        "falta confirmacion explicita de 22 mm",
    )
    filas = lote.get("filas")
    _exige(isinstance(filas, list) and len(filas) == 9, "se requieren los nueve items exactos")
    _exige(all(isinstance(f, dict) for f in filas), "fila ilegible")
    _exige(
        {f.get("item_id") for f in filas} == ITEMS, "items repetidos o fuera del lote autorizado"
    )
    for f in filas:
        for campo in ("variante_pez", "seller_sku", "status", "antes", "despues"):
            _exige(
                isinstance(f.get(campo), str) and f[campo].strip() and "\x00" not in f[campo],
                f"{f['item_id']}: {campo} invalido",
            )
        _exige(f["variante_pez"].isascii() and f["variante_pez"].isdigit(), "variante PEZ invalida")
        _exige("PEZ" in f["seller_sku"].upper(), "SKU no identifica PEZ")
        _exige(type(f.get("seller_id")) is int and f["seller_id"] > 0, "seller_id invalido")
        _exige(f["status"] in ("active", "under_review", "paused"), "status fuera del alcance")
        _exige(
            f["antes"] != f["despues"] and f["despues"].count(NOTA_PEZ) == 1,
            "texto aprobado debe agregar la nota PEZ 22 mm una sola vez",
        )
    return sorted(filas, key=lambda f: f["item_id"])


def _coincide_texto(actual, esperado):
    """Solo admite la conversion completa TAB -> espacio observada en MeLi.

    No recorta ni colapsa whitespace. La reversa puede recuperar el original
    con sus TAB normalizados por el servidor; el respaldo conserva los bytes.
    La creacion de un respaldo nuevo sigue exigiendo el antes exacto.
    """
    return actual == esperado or actual == esperado.replace("\t", " ")


def _preflight(cliente, filas):
    usuario = cliente.usuario().get("id")
    actuales = {}
    for f in filas:
        item = f["item_id"]
        _exige(usuario == f["seller_id"], f"{item}: usuario vendedor distinto")
        datos = cliente.item(item)
        _exige(
            datos.get("id") == item
            and datos.get("seller_id") == f["seller_id"]
            and datos.get("status") == f["status"],
            f"{item}: identidad, vendedor o estado distintos",
        )
        variantes = [
            v for v in datos.get("variations", []) if str(v.get("id")) == f["variante_pez"]
        ]
        _exige(len(variantes) == 1, f"{item}: variante PEZ ausente o repetida")
        skus = [
            a.get("value_name")
            for a in variantes[0].get("attributes", [])
            if a.get("id") == "SELLER_SKU"
        ]
        _exige(skus == [f["seller_sku"]], f"{item}: SELLER_SKU distinto")
        actual = cliente.descripcion(item)
        _exige(
            _coincide_texto(actual, f["antes"]) or _coincide_texto(actual, f["despues"]),
            f"{item}: descripcion editada por tercero",
        )
        actuales[item] = actual
    return actuales


def _leer_respaldo(archivo):
    archivo.seek(0)
    datos = json.load(archivo)
    digest = datos.pop("sha256", None)
    _exige(digest == _hash(datos), "respaldo corrupto")
    validar_lote(datos["lote"])
    _exige(
        datos.get("version") == 1 and datos.get("lote_sha256") == _hash(datos["lote"]),
        "respaldo de otro lote",
    )
    return datos


@contextmanager
def _respaldo(ruta, lote=None, actuales=None):
    """Exclusivo, durable y cerrado; reanudar exige el mismo lote literal."""
    ruta = Path(ruta)
    nuevo = False
    if lote is None:
        fd = os.open(ruta, os.O_RDWR | os.O_NOFOLLOW)
    else:
        try:
            fd = os.open(ruta, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            nuevo = True
        except FileExistsError:
            fd = os.open(ruta, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "r+", encoding="utf-8") as archivo:
        fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _exige(os.fstat(archivo.fileno()).st_mode & 0o077 == 0, "respaldo debe ser privado 0600")
        if nuevo:
            _exige(lote is not None, "respaldo no existe para revertir")
            datos = {
                "version": 1,
                "lote": lote,
                "lote_sha256": _hash(lote),
                "antes_actual": actuales,
            }
            datos["sha256"] = _hash(datos)
            try:
                _exige(
                    all(actuales[f["item_id"]] == f["antes"] for f in lote["filas"]),
                    "respaldo nuevo exige nueve textos originales; use el respaldo original",
                )
                os.fchmod(archivo.fileno(), 0o600)
                json.dump(datos, archivo, ensure_ascii=False, indent=2)
                archivo.write("\n")
                archivo.flush()
                os.fsync(archivo.fileno())
                directorio = os.open(ruta.resolve().parent, os.O_RDONLY)
                try:
                    os.fsync(directorio)
                finally:
                    os.close(directorio)
            except BaseException:
                ruta.unlink(missing_ok=True)
                raise
        datos = _leer_respaldo(archivo)
        if lote is not None:
            _exige(datos["lote_sha256"] == _hash(lote), "respaldo pertenece a otro lote")
        yield datos


@contextmanager
def _ledger(respaldo):
    ruta = Path(str(respaldo) + ".jsonl")
    fd = os.open(ruta, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as archivo:
        _exige(os.fstat(archivo.fileno()).st_mode & 0o077 == 0, "ledger debe ser privado 0600")

        def escribir(**evento):
            archivo.write(json.dumps(evento, ensure_ascii=False) + "\n")
            archivo.flush()
            os.fsync(archivo.fileno())

        directorio = os.open(ruta.resolve().parent, os.O_RDONLY)
        try:
            os.fsync(directorio)
        finally:
            os.close(directorio)
        yield escribir


def _cambiar(cliente, filas, respaldo, reversa=False):
    origen, destino = ("despues", "antes") if reversa else ("antes", "despues")
    cambiados = ya = 0
    with _ledger(respaldo) as registrar:
        for f in filas:
            item = f["item_id"]
            actual = cliente.descripcion(item)
            if _coincide_texto(actual, f[destino]):
                ya += 1
                continue
            _exige(
                _coincide_texto(actual, f[origen]),
                f"{item}: deriva antes del PUT; detenido sin pisarla",
            )
            registrar(evento="intento", item_id=item, reversa=reversa)
            try:
                acuse = cliente.poner(item, f[destino])
                leido = cliente.descripcion(item)
                _exige(
                    _coincide_texto(leido, f[destino]),
                    f"{item}: resultado no confirmado ({acuse}); detener y revisar respaldo",
                )
            except Exception:
                registrar(evento="no_confirmado", item_id=item, reversa=reversa)
                raise
            registrar(evento="confirmado", item_id=item, reversa=reversa, acuse=acuse)
            cambiados += 1
    return {
        "estado": "revertido" if reversa else "aplicado",
        "cambiados": cambiados,
        "ya_revertidos" if reversa else "ya_aplicados": ya,
    }


def aplicar(cliente, lote, *, respaldo=None, apply=False, items=None):
    filas = validar_lote(lote)
    seleccion = ITEMS if items is None else frozenset(items)
    _exige(seleccion and seleccion <= ITEMS, "filtro item fuera del lote autorizado")
    actuales = _preflight(cliente, filas)
    elegidas = [f for f in filas if f["item_id"] in seleccion]
    if not apply:
        return {
            "estado": "dry-run",
            "pendientes": sum(
                not _coincide_texto(actuales[f["item_id"]], f["despues"]) for f in elegidas
            ),
            "ya_aplicados": sum(
                _coincide_texto(actuales[f["item_id"]], f["despues"]) for f in elegidas
            ),
        }
    _exige(respaldo is not None, "apply exige --respaldo")
    with _respaldo(respaldo, lote, actuales):
        return _cambiar(cliente, elegidas, respaldo)


def revertir(cliente, respaldo):
    _exige(Path(respaldo).is_file(), "respaldo inexistente")
    with _respaldo(respaldo) as datos:
        filas = validar_lote(datos["lote"])
        _preflight(cliente, filas)
        return _cambiar(cliente, filas, respaldo, reversa=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lote", nargs="?", type=Path)
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument("--dry-run", action="store_true")
    modos.add_argument("--apply", action="store_true")
    modos.add_argument("--revertir", type=Path)
    parser.add_argument("--respaldo", type=Path)
    parser.add_argument("--item", action="append", dest="items")
    args = parser.parse_args(argv)
    if args.revertir:
        if args.lote or args.respaldo or args.items:
            parser.error("--revertir solo recibe respaldo existente")
    elif args.lote is None or (args.apply and args.respaldo is None):
        parser.error("lote requerido; --apply exige --respaldo")
    elif args.respaldo is not None and not args.apply:
        parser.error("--respaldo solo se usa con --apply")
    cliente = None
    try:
        cliente = ClienteDescripciones(MeliCredentials.from_secrets_dir())
        resultado = (
            revertir(cliente, args.revertir)
            if args.revertir
            else aplicar(
                cliente,
                json.loads(args.lote.read_text()),
                respaldo=args.respaldo,
                apply=args.apply,
                items=args.items,
            )
        )
        print(json.dumps(resultado, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"operacion detenida: {scrub(str(exc))}", file=sys.stderr)
        return 1
    finally:
        if cliente is not None:
            cliente.close()


if __name__ == "__main__":
    raise SystemExit(main())
