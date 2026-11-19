"""Medicion de solo lectura de paginas de listas SP en Amazon Ads (B-C.0).

Correr:
  bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-C/mide_paginas.py
"""

from __future__ import annotations

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import perfiles_aceptados
from app.apply_harvest import (
    _CONTENEDORES_LIST,
    TOPE_PAGINAS_LIST,
    _valida_pagina_list,
    _valida_token_list,
)

TOPE_MEDICION = 500


def main() -> None:
    cliente = AdsClient(AdsCredentials.from_secrets_dir())
    esperados = {
        (pais, path)
        for pais in ("amazon_mx", "amazon_us")
        for path in ("/sp/keywords/list", "/sp/negativeKeywords/list")
    }
    completos: set[tuple[str, str]] = set()
    for perfil in perfiles_aceptados(cliente):
        for path, contenedor in _CONTENEDORES_LIST.items():
            paginas = 0
            items = 0
            primera: int | None = None
            token: str | None = None
            fin = False
            vistos: set[str] = set()
            while paginas < TOPE_MEDICION:
                body = {"nextToken": token} if token is not None else {}
                resp = cliente.list_objects(path, body, profile_id=perfil.profile_id)
                paginas += 1
                try:
                    datos = resp.json()
                except ValueError:
                    break
                pagina, valida = _valida_pagina_list(datos, contenedor)
                if not valida:
                    break
                n = len(pagina)
                items += n
                if primera is None:
                    primera = n
                estado = _valida_token_list(datos.get("nextToken"))
                if estado == "fin":
                    fin = True
                    break
                if estado == "ambiguo":
                    break
                token = datos["nextToken"]
                if token in vistos:
                    break
                vistos.add(token)
            marca_fin = "fin" if fin else "sin_fin"
            print(f"{perfil.platform}|{path}|{paginas}|{items}|{primera}|{marca_fin}")
            par = (perfil.platform, path)
            if not fin or par not in esperados or par in completos:
                raise SystemExit(f"medicion incompleta o repetida: {par}")
            completos.add(par)
            if paginas > TOPE_PAGINAS_LIST:
                print(
                    f"corte_callado|{perfil.platform}|{path}|"
                    f"{paginas} paginas contra el tope de {TOPE_PAGINAS_LIST}"
                )
    if completos != esperados:
        raise SystemExit(f"faltan listas: {sorted(esperados - completos)}")


if __name__ == "__main__":
    main()
