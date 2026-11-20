"""Revision en Amazon de los bloqueos aplicados (B-D.1). Solo lectura.

Correr:
  bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-D/lee_amazon.py

Lee la base con `ORBIT_DSN_READ` en transaccion read_only y Amazon con
`AdsClient` (guard que rechaza PUT, PATCH y DELETE). Por cada veredicto
aprobado cuyo ultimo paso durable es `aplicado` imprime:

  plataforma|veredicto|grupo|id|estado|senal_vigente|otro_enabled

donde `estado` es el state que Amazon devuelve para el id del ledger
(ENABLED, PAUSED, ARCHIVED o AUSENTE), `senal_vigente` es el `senal_id` de
la senal vigente con lectura `ajena` de esa clave (`-` si no hay) y
`otro_enabled` es el id de otro negative ENABLED del mismo termino en el
mismo ad group (`-` si no hay). Una lectura fallida se imprime como
`LECTURA_*` y deja esa fila fuera de la reparacion.

Cierra con `FIN|n_mx|n_us|n_reversa_fallida`. Un perfil ausente, un id
duplicado o una lectura incompleta dejan el inventario pendiente y salen
distinto de cero: cero filas es un inventario valido.
"""

from __future__ import annotations

import os
import time

from app import bloqueo_dueno_efecto as bde
from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import perfiles_aceptados
from app.db import connect

_ULTIMO = """select v.id, v.plataforma::text, v.ad_group_id, v.termino, v.termino_sha256,
                    ult.negative_keyword_id
               from bloqueo_veredicto v
               join lateral (select e.paso, e.negative_keyword_id from bloqueo_efecto e
                              where e.veredicto_id = v.id
                              order by e.created_at desc, e.id desc limit 1) ult on true
              where v.tipo = 'aprobado' and ult.paso = %s
              order by v.plataforma, v.created_at"""
_SENAL = """select g.senal_id from jev_senal_vigente g join jev_senal s on s.id = g.senal_id
             where g.ad_group_id = %s and s.termino_sha256 = %s
               and g.vigente and g.lectura = 'ajena'"""


class _Sellado:
    """Adapta el cliente de lectura al lector unico de B-C (`list_sellado`)."""

    def __init__(self, cliente: AdsClient, pid: str | int):
        self.cliente = cliente
        self.pid = pid

    def list_sellado(self, path: str, body: dict):
        return self.cliente.list_objects(path, body, profile_id=self.pid)


def _lee(cliente: AdsClient, pid: str | int, grupo_ext: str) -> tuple[list, str]:
    sellado = _Sellado(cliente, pid)
    items, estado = bde._lista_o(sellado, grupo_ext)
    if estado != "ok":
        time.sleep(30)
        items, estado = bde._lista_o(sellado, grupo_ext)
    return items, estado


def main() -> int:
    cliente = AdsClient(AdsCredentials.from_secrets_dir())
    perfiles = {p.platform: p.profile_id for p in perfiles_aceptados(cliente)}
    esperados = {"amazon_mx", "amazon_us"}
    if set(perfiles) != esperados:
        print("LECTURA_perfil: inventario incompleto")
        return 1

    conteos = dict.fromkeys(sorted(esperados), 0)
    fallidas = 0
    vistos: set[str] = set()
    invalidas = 0
    with connect(os.environ["ORBIT_DSN_READ"]) as conn:
        conn.read_only = True
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        filas = conn.execute(_ULTIMO, ("aplicado",)).fetchall()
        for vid, plat, grupo, termino, sha, kid in filas:
            vid_txt = str(vid)
            if vid_txt in vistos:
                print(f"DUPLICADO|{plat}|{vid_txt}")
                invalidas += 1
                continue
            vistos.add(vid_txt)
            if plat not in conteos:
                print(f"PLATAFORMA_FUERA|{plat}|{vid_txt}")
                invalidas += 1
                continue
            conteos[plat] += 1
            senal = conn.execute(_SENAL, (grupo, sha)).fetchone()
            senal = str(senal[0]) if senal else "-"
            externos = bde._externos(conn, grupo)
            if externos is None:
                print(f"{plat}|{vid_txt}|{grupo}|{kid}|LECTURA_sin_externos|{senal}|-")
                continue
            items, estado = _lee(cliente, perfiles[plat], externos[0])
            if estado != "ok":
                print(f"{plat}|{vid_txt}|{grupo}|{kid}|LECTURA_{estado}|{senal}|-")
                continue
            item = bde._por_id(items, kid)
            estado_id = "AUSENTE" if item is None else str(item.get("state", "")).upper()
            otro = bde._negative_que_bloquea(items, externos[0], termino)
            otro = (
                str(otro["keywordId"]) if otro and str(otro.get("keywordId")) != str(kid) else "-"
            )
            print(f"{plat}|{vid_txt}|{grupo}|{kid}|{estado_id}|{senal}|{otro}")
        for vid, *_resto in conn.execute(_ULTIMO, ("reversa_fallida",)).fetchall():
            print(f"reversa_fallida|{vid}")
            fallidas += 1
    print(f"FIN|{conteos['amazon_mx']}|{conteos['amazon_us']}|{fallidas}")
    return 1 if invalidas else 0


if __name__ == "__main__":
    raise SystemExit(main())
