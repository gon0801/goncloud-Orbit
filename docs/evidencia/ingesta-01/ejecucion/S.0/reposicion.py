"""Reposicion S.0 (2026-10-21, ~19:30 UTC): repara las medidas que la corrida
de 19:00 perdio por el bug de truncamiento (reportId al final del cuerpo).

- (r1) targets MX/US de (a) y targets MX de (d): mismos cfg, ventana corrida
  1-2 dias (Amazon deduplica por configuracion+ventana, no por name: probado
  en vivo con los 425 de (b)/(c) de la corrida original).
- (r2) rafaga de 24 POST con ventana unica cada uno (corrida 3..26 dias):
  la rafaga original cayo entera en dedup 425 y no midio throttling; esta
  si mide los 429 reales. Corre con (r1) en cola, como en el diseno.
- (r3) GET de los reportId que la corrida original dejo sin sondear (los
  425 de (c) los habian revelado): status final como evidencia.

No escribe en la DB. Corre como la sonda: docker exec -i orbit-app-1 python -
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
TOPE_S = 40 * 60
PATH = "/reporting/reports"
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
SECRETO = re.compile(r"(Bearer\s+\S+|Atza\|\S+|Atzr\|\S+)", re.IGNORECASE)
CFG_NOMBRE = {c["nombre"]: c for c in (*REPORTES_CFG, PRODUCTOS_CFG, PLACEMENTS_CFG)}
TODOS = ("campanas", "keywords", "targets", "search_terms", "productos", "placements")
# Revelados por los 425 de (c) en la corrida original (targets sin id parseado).
ID_A_TARGETS_MX = "b3bdeec5-ef1d-4461-8adc-344ccd230512"
ID_A_TARGETS_US = "f0bedaa2-fc84-4b94-bee7-983f4a49bc30"


def log(*partes) -> None:
    linea = " ".join(str(p) for p in partes)
    print(SECRETO.sub("[REDACTADO]", linea), flush=True)


def fmt_min(minutos: float | None) -> str:
    return f"{minutos:.1f}" if minutos is not None else "NO"


def cuerpo_de(cfg: dict, pid, ini: dt.date, fin: dt.date) -> dict:
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
        "name": f"orbit-{cfg['nombre']}-{pid}-{ini.isoformat()}_{fin.isoformat()}",
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
            rid = None
            if r.status_code in (200, 202):
                try:
                    rid = r.json().get("reportId")
                except (ValueError, AttributeError):
                    rid = None
            return r.status_code, r.text, time.monotonic() - t0, rid
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"[:600], time.monotonic() - t0, None

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


def main() -> int:
    if os.path.exists(LOCK):
        log(f"ABORTA: {LOCK} tomado (ingesta diaria en curso)")
        return 3
    ahora = dt.datetime.now(dt.UTC)
    if ahora.hour < 19 and os.environ.get("SONDA_FORZAR") != "1":
        log(f"ABORTA: son las {ahora:%H:%M} UTC; la reposicion corre desde las 19:00 UTC")
        return 3
    hoy = ahora.date()
    ini, fin = hoy - dt.timedelta(days=31), hoy - dt.timedelta(days=1)
    s = Sonda(AdsClient(AdsCredentials.from_secrets_dir()))
    perfiles = [p for p in evaluar_perfiles(s.c) if p.aceptado]
    mx = next((p for p in perfiles if p.platform == "amazon_mx"), None)
    us = next((p for p in perfiles if p.platform == "amazon_us"), None)
    if mx is None or us is None:
        log("ABORTA: se necesitan los perfiles amazon_mx y amazon_us aceptados")
        return 4
    log(f"reposicion inicio {ahora:%Y-%m-%d %H:%M:%S} UTC")

    # (r1) los tres targets perdidos, ventanas corridas 1-2 dias
    pend: dict = {}
    un_dia = dt.timedelta(days=1)
    repos = [
        (mx, "(a) amazon_mx/targets", un_dia),
        (us, "(a) amazon_us/targets", un_dia),
        (mx, "(d) MX/targets", 2 * un_dia),
    ]
    for p, etq, corrimiento in repos:
        cfg = CFG_NOMBRE["targets"]
        cuerpo = cuerpo_de(cfg, p.profile_id, ini - corrimiento, fin - corrimiento)
        cod, txt, seg, rid = s.post(p.profile_id, cuerpo)
        log(
            f"(r1) POST {etq} {ini - corrimiento}..{fin - corrimiento}: HTTP {cod} "
            f"en {seg:.1f}s id={rid} cuerpo={txt[:200]!r}"
        )
        if rid:
            pend[rid] = {"pid": p.profile_id, "etq": etq, "t0": time.monotonic(), "min": None}

    # (r2) rafaga de 24 con ventana unica (corrida 3..26 dias), cola viva
    codigos: dict = {}
    n = 0
    for _vuelta in (1, 2):
        for p in perfiles:
            for nombre in TODOS:
                n += 1
                d = dt.timedelta(days=n + 2)
                cuerpo = cuerpo_de(CFG_NOMBRE[nombre], p.profile_id, ini - d, fin - d)
                cod, txt, seg, _ = s.post(p.profile_id, cuerpo)
                codigos[cod] = codigos.get(cod, 0) + 1
                log(
                    f"(r2) #{n} {p.platform}/{nombre} {ini - d}..{fin - d}: HTTP {cod} "
                    f"en {seg:.2f}s cuerpo={txt[:300]!r}"
                )
    log(f"(r2) resumen: {n} POST; codigos={codigos}; 429={codigos.get(429, 0)}")

    # sondeo de (r1) hasta 40 min
    t0 = time.monotonic()
    while any(v["min"] is None for v in pend.values()) and time.monotonic() - t0 < TOPE_S:
        time.sleep(POLL_S)
        for rid, v in pend.items():
            if v["min"] is not None:
                continue
            d = s.get(v["pid"], rid)
            if d.get("status") in ("COMPLETED", "FAILURE", "FAILED"):
                v["min"] = (time.monotonic() - v["t0"]) / 60
                log(f"(r1) {v['etq']} {d.get('status')} a los {v['min']:.1f} min")
    log(
        "(r1) resumen minutos hasta COMPLETED: "
        + ", ".join(f"{v['etq']}={fmt_min(v['min'])}" for v in pend.values())
    )

    # (r3) status final de los targets de la corrida original (sin sondeo exacto)
    for etq, pid, rid in (
        ("(a) amazon_mx/targets", mx.profile_id, ID_A_TARGETS_MX),
        ("(a) amazon_us/targets", us.profile_id, ID_A_TARGETS_US),
    ):
        d = s.get(pid, rid)
        log(f"(r3) GET {etq} id={rid}: {d}")

    log(f"reposicion fin {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
