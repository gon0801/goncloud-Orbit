"""Sonda S.0: cuanto tarda Amazon en la cuenta real (solo lectura de la DB: no la toca).

Correr (a partir de las 19:00 UTC, sin /tmp/orbit-ingesta-diaria.lock):
    docker exec -i orbit-app-1 python - < sonda.py > salida.txt

(a) 12 reportes a la vez (4 metricas + productos + placements, por perfil,
    D-31..D-1), sondeo cada 20 s hasta 2 h 30 min.
(b) con la cola viva: campanas MX identico, y luego con name + "-r1".
(c) rafaga de 24 POST mas; se cuentan los 429.
(d) al terminar, los 4 de metricas MX uno tras otro.
Los POST van con httpx directo para guardar codigo y cuerpo crudos.
"""

import datetime as dt
import os
import re
import sys
import time

import httpx

from app.ads.client import DEFAULT_BASE_URL, AdsClient
from app.ads.config import AdsCredentials
from app.ads.placements import PLACEMENTS_CFG
from app.ads.reports import PRODUCTOS_CFG, REPORTES_CFG
from app.ads.structure_api import evaluar_perfiles

LOCK = "/tmp/orbit-ingesta-diaria.lock"
POLL_S = 20
TOPE_A_S = 150 * 60
TOPE_D_S = 40 * 60
PATH = "/reporting/reports"
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
SECRETO = re.compile(r"(Bearer\s+\S+|Atza\|\S+|Atzr\|\S+)", re.IGNORECASE)
CFG_NOMBRE = {c["nombre"]: c for c in (*REPORTES_CFG, PRODUCTOS_CFG, PLACEMENTS_CFG)}
METRICAS = ("campanas", "keywords", "targets", "search_terms")


def log(*partes) -> None:
    linea = " ".join(str(p) for p in partes)
    print(SECRETO.sub("[REDACTADO]", linea), flush=True)


def cuerpo_de(cfg: dict, pid, ini: dt.date, fin: dt.date, sufijo: str = "") -> dict:
    conf = {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": list(cfg["groupBy"]),
        "columns": list(cfg["columns"]),
        "reportTypeId": cfg["reportTypeId"],
        "timeUnit": "DAILY",
        "format": "GZIP_JSON",
    }
    if "filters" in cfg:
        conf["filters"] = cfg["filters"]
    return {
        "name": f"orbit-{cfg['nombre']}-{pid}-{ini.isoformat()}_{fin.isoformat()}{sufijo}",
        "startDate": ini.isoformat(),
        "endDate": fin.isoformat(),
        "configuration": conf,
    }


class Sonda:
    def __init__(self, client: AdsClient) -> None:
        self.c = client
        self.http = httpx.Client(timeout=TIMEOUT, follow_redirects=False)

    def headers(self, pid, path, method):
        return self.c._build_headers(self.c._ensure_token(), pid, path=path, method=method)

    def post(self, pid, body):
        t0 = time.monotonic()
        try:
            r = self.http.post(
                DEFAULT_BASE_URL + PATH, headers=self.headers(pid, PATH, "POST"), json=body
            )
            return r.status_code, r.text[:600], time.monotonic() - t0
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"[:600], time.monotonic() - t0

    def get(self, pid, rid):
        path = f"{PATH}/{rid}"
        try:
            r = self.http.get(DEFAULT_BASE_URL + path, headers=self.headers(pid, path, "GET"))
            if r.status_code != 200:
                return {"_http": r.status_code, "_texto": r.text[:300]}
            d = r.json()
            return {k: d.get(k) for k in ("status", "failureReason")}
        except (httpx.HTTPError, ValueError) as exc:
            return {"_http": None, "_texto": f"{type(exc).__name__}: {exc}"[:300]}


def rid_de(texto: str):
    m = re.search(r'"reportId"\s*:\s*"([^"]+)"', texto or "")
    if m:
        return m.group(1)
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", texto or "")
    return m.group(1) if m else None


def main() -> int:
    if os.path.exists(LOCK):
        log(f"ABORTA: {LOCK} tomado (ingesta diaria en curso)")
        return 3
    ahora = dt.datetime.now(dt.UTC)
    if ahora.hour < 19 and os.environ.get("SONDA_FORZAR") != "1":
        log(f"ABORTA: son las {ahora:%H:%M} UTC; la sonda corre desde las 19:00 UTC")
        return 3
    hoy = ahora.date()
    ini, fin = hoy - dt.timedelta(days=31), hoy - dt.timedelta(days=1)
    s = Sonda(AdsClient(AdsCredentials.from_secrets_dir()))
    perfiles = [p for p in evaluar_perfiles(s.c) if p.aceptado]
    log(f"inicio {ahora:%Y-%m-%d %H:%M:%S} UTC; rango {ini}..{fin}; "
        f"perfiles aceptados: {[(p.profile_id, p.platform) for p in perfiles]}")

    # (a) 12 reportes a la vez
    pend: dict[str, dict] = {}
    t_a = time.monotonic()
    for p in perfiles:
        for nombre in (*METRICAS, "productos", "placements"):
            cod, txt, seg = s.post(p.profile_id, cuerpo_de(CFG_NOMBRE[nombre], p.profile_id, ini, fin))
            rid = rid_de(txt) if cod in (200, 202) else None
            log(f"(a) POST {p.platform}/{nombre}: HTTP {cod} en {seg:.1f}s id={rid} cuerpo={txt[:200]!r}")
            if rid:
                pend[rid] = {"pid": p.profile_id, "etq": f"{p.platform}/{nombre}", "t0": time.monotonic(), "min": None, "st": None}

    mx = next((p for p in perfiles if p.platform == "MX"), None) or (perfiles[0] if perfiles else None)
    if mx is None:
        log("ABORTA: sin perfiles aceptados")
        return 4
    cfg_mx = CFG_NOMBRE["campanas"]

    def cola_viva() -> bool:
        return any(v["min"] is None for v in pend.values())

    def sondear_una_vez() -> None:
        for rid, v in pend.items():
            if v["min"] is not None:
                continue
            d = s.get(v["pid"], rid)
            v["st"] = d.get("status") or d.get("_http")
            if d.get("status") in ("COMPLETED", "FAILURE", "FAILED"):
                v["min"] = (time.monotonic() - v["t0"]) / 60
                log(f"(a) {v['etq']} {d.get('status')} a los {v['min']:.1f} min "
                    f"razon={d.get('failureReason')!r}")

    sondear_una_vez()
    # (b) repetidos con la cola viva
    if cola_viva():
        for etq, suf in (("identico", ""), ("name+-r1", "-r1")):
            cod, txt, seg = s.post(mx.profile_id, cuerpo_de(cfg_mx, mx.profile_id, ini, fin, suf))
            log(f"(b) campanas MX {etq}: HTTP {cod} en {seg:.1f}s cuerpo={txt!r}")
        # (c) rafaga de 24 mas (2 vueltas de los 12, name unico por POST)
        codigos: dict = {}
        n = 0
        for vuelta in (1, 2):
            for p in perfiles:
                for nombre in (*METRICAS, "productos", "placements"):
                    n += 1
                    cod, txt, seg = s.post(
                        p.profile_id,
                        cuerpo_de(CFG_NOMBRE[nombre], p.profile_id, ini, fin, f"-rafaga{vuelta}"),
                    )
                    codigos[cod] = codigos.get(cod, 0) + 1
                    log(f"(c) #{n} {p.platform}/{nombre}: HTTP {cod} en {seg:.2f}s cuerpo={txt!r}")
        log(f"(c) resumen: {n} POST; codigos={codigos}; 429={codigos.get(429, 0)}")
    else:
        log("(b)/(c) omitidos: la cola ya estaba vacia al primer sondeo")

    while cola_viva() and time.monotonic() - t_a < TOPE_A_S:
        time.sleep(POLL_S)
        sondear_una_vez()
    for v in pend.values():
        if v["min"] is None:
            log(f"(a) {v['etq']} SIN COMPLETED tras {(time.monotonic() - v['t0']) / 60:.1f} min "
                f"(ultimo status {v['st']})")
    log("(a) resumen minutos hasta COMPLETED: " + ", ".join(
        f"{v['etq']}={'%.1f' % v['min'] if v['min'] is not None else 'NO'}" for v in pend.values()))

    # (d) los 4 de metricas MX uno tras otro
    resumen_d = []
    for nombre in METRICAS:
        cod, txt, seg = s.post(mx.profile_id, cuerpo_de(CFG_NOMBRE[nombre], mx.profile_id, ini, fin, "-d"))
        rid = rid_de(txt) if cod in (200, 202) else None
        log(f"(d) POST MX/{nombre}: HTTP {cod} en {seg:.1f}s id={rid} cuerpo={txt[:200]!r}")
        if not rid:
            resumen_d.append(f"{nombre}=NO_CREADO")
            continue
        t0 = time.monotonic()
        minutos = None
        while time.monotonic() - t0 < TOPE_D_S:
            time.sleep(POLL_S)
            d = s.get(mx.profile_id, rid)
            if d.get("status") in ("COMPLETED", "FAILURE", "FAILED"):
                minutos = (time.monotonic() - t0) / 60
                log(f"(d) MX/{nombre} {d.get('status')} a los {minutos:.1f} min")
                break
        if minutos is None:
            log(f"(d) MX/{nombre} SIN COMPLETED tras {TOPE_D_S / 60:.0f} min")
        resumen_d.append(f"{nombre}={'%.1f' % minutos if minutos is not None else 'NO'}")
    log("(d) resumen minutos: " + ", ".join(resumen_d))
    log(f"fin {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC")
    return 0


sys.exit(main())
