#!/usr/bin/env python3
"""Expediente READ-ONLY de decisiones APLICADAS (ORBIT 05 tarea 2.1a).

Arma el insumo saneado para la verificacion adversarial triple (codex/grok/
qwen): decision + inputs congelados + ledger + readback. Cero mutaciones.
DSN: ORBIT_DSN_READ via app.db.connect. SOLO SELECT.

USO:
  python tools/dossier_adversarial.py --ciclos 33 34 --out DIR
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import stat
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cycle import reproduce  # noqa: E402
from app.db import connect  # noqa: E402
from app.redaction import scrub  # noqa: E402

# Preconfirmacion plan orbit-05 2.1: JAMAS tokens/headers/profile ids/ids de cuenta;
# SI viajan datos comerciales (keyword, gasto, ventas, bids, ids keyword/target Amazon).
CLAVES_REGISTRO = (
    "decision",
    "entidad",
    "apply_attempts",
    "decision_application",
    "readback",
    "ciclo",
    "replay",
)
CLAVES_DECISION = (
    "id",
    "cycle_id",
    "kind",
    "decided_at",
    "config_version_id",
    "data_observed_at",
    "window_start",
    "window_end",
    "old_value",
    "new_value",
    "value_currency",
    "inputs",
)
CLAVES_ENTIDAD = (
    "id",
    "kind",
    "match_type",
    "keyword_text",
    "name",
    "external_id",
    "ad_group",
    "campana",
)
CLAVES_AD_GROUP = ("id", "name")
CLAVES_CAMPANA = ("id", "name", "external_id", "status")
CLAVES_ATTEMPT = (
    "id",
    "seq",
    "tipo",
    "quota_cobrada",
    "started_at",
    "finished_at",
    "resultado",
    "request_payload",
    "ack",
)
CLAVES_APPLICATION = (
    "attempted_at",
    "confirmed_at",
    "verify_ok",
    "platform_ack",
    "applied_cycle_id",
    "error",
)
CLAVES_READBACK = ("current_bid", "bid_currency", "synced_at")
CLAVES_CICLO = ("mode", "started_at")
CLAVES_REPLAY = ("kind", "new_value", "currency", "replay_coincide")

_TEXTOS_SECRETO = (
    "Atza|",
    "amzn1.",
    "client_secret",
    "refresh_token",
    "Authorization",
    "Bearer ",
    "profileId",
    "Amazon-Advertising-API-",
)
PATRONES_SECRETO: tuple[re.Pattern[str], ...] = tuple(
    re.compile(re.escape(texto)) for texto in _TEXTOS_SECRETO
)

_SQL_APLICADAS = """
SELECT
  d.id AS decision_id,
  d.cycle_id,
  d.kind AS decision_kind,
  d.decided_at,
  d.config_version_id,
  d.data_observed_at,
  d.window_start,
  d.window_end,
  d.old_value,
  d.new_value,
  d.value_currency,
  d.inputs,
  e.id AS entidad_id,
  e.kind AS entidad_kind,
  e.match_type,
  e.keyword_text,
  e.name AS entidad_name,
  e.external_id AS entidad_external_id,
  padre.id AS padre_id,
  padre.kind AS padre_kind,
  padre.name AS padre_name,
  padre.external_id AS padre_external_id,
  abuelo.id AS abuelo_id,
  abuelo.name AS abuelo_name,
  abuelo.external_id AS abuelo_external_id,
  oc.mode AS ciclo_mode,
  oc.started_at AS ciclo_started_at
FROM decision d
JOIN ad_entity e ON e.id = d.ad_entity_id
JOIN optimizer_cycle oc ON oc.id = d.cycle_id
LEFT JOIN ad_entity padre ON padre.id = e.parent_id
LEFT JOIN ad_entity abuelo ON abuelo.id = padre.parent_id
WHERE d.cycle_id = ANY(%s)
  AND EXISTS (
    SELECT 1 FROM apply_attempt a
    WHERE a.decision_id = d.id
      AND a.tipo = 'normal'
      AND a.resultado = 'ok'
  )
ORDER BY d.id
"""

_SQL_INTENTOS = """
SELECT id, decision_id, seq, tipo, quota_cobrada, started_at, finished_at,
       resultado, request_payload, ack
FROM apply_attempt
WHERE decision_id = ANY(%s)
ORDER BY decision_id, seq
"""

_SQL_APPLICATION = """
SELECT decision_id, attempted_at, confirmed_at, verify_ok, platform_ack,
       applied_cycle_id, error
FROM decision_application
WHERE decision_id = ANY(%s)
"""

_SQL_ESTADO = """
SELECT ad_entity_id, current_bid, bid_currency, status, synced_at
FROM ad_entity_state
WHERE ad_entity_id = ANY(%s)
"""


def escanear_secretos(texto: str) -> list[str]:
    """Patrones que aparecen en texto. Lista vacia = limpio (fail-closed si no)."""
    if not texto:
        return []
    return [p.pattern for p in PATRONES_SECRETO if p.search(texto)]


def _jsonable(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dt.datetime):
        return valor.isoformat()
    if isinstance(valor, dt.date):
        return valor.isoformat()
    if isinstance(valor, dict):
        return {k: _jsonable(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_jsonable(v) for v in valor]
    return valor


def _nodo(claves: tuple[str, ...], fuente: dict) -> dict:
    return {k: _jsonable(fuente.get(k)) for k in claves}


def _filas_dict(conn, sql: str, params) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, fila, strict=True)) for fila in cur.fetchall()]


def _cadena(fila: dict) -> tuple[dict, dict, int | None]:
    kind = fila["entidad_kind"]
    if kind == "campaign":
        grupo = {"id": None, "name": None}
        campana = {
            "id": fila["entidad_id"],
            "name": fila["entidad_name"],
            "external_id": fila["entidad_external_id"],
        }
        return grupo, campana, fila["entidad_id"]
    if kind == "ad_group":
        grupo = {"id": fila["entidad_id"], "name": fila["entidad_name"]}
        campana = {
            "id": fila["padre_id"],
            "name": fila["padre_name"],
            "external_id": fila["padre_external_id"],
        }
        return grupo, campana, fila["padre_id"]
    grupo = {"id": fila["padre_id"], "name": fila["padre_name"]}
    campana = {
        "id": fila["abuelo_id"],
        "name": fila["abuelo_name"],
        "external_id": fila["abuelo_external_id"],
    }
    return grupo, campana, fila["abuelo_id"]


def _misma_plata(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return Decimal(str(a)) == Decimal(str(b))


def _replay(inputs: dict, kind: str, new_value, currency) -> dict:
    r_kind, r_nuevo, r_moneda = reproduce(inputs)
    return _nodo(
        CLAVES_REPLAY,
        {
            "kind": r_kind,
            "new_value": None if r_nuevo is None else str(r_nuevo),
            "currency": r_moneda,
            "replay_coincide": (
                r_kind == kind and _misma_plata(r_nuevo, new_value) and r_moneda == currency
            ),
        },
    )


def construir_registros(conn, ciclos: list[int]) -> list[dict]:
    """Decisiones APLICADAS de esos ciclos; un dict por allowlist, nunca el row."""
    if not ciclos:
        return []
    ids = [int(c) for c in ciclos]
    filas = _filas_dict(conn, _SQL_APLICADAS, (ids,))
    if not filas:
        return []
    dec_ids = [f["decision_id"] for f in filas]
    intentos_por: dict[int, list[dict]] = {i: [] for i in dec_ids}
    for att in _filas_dict(conn, _SQL_INTENTOS, (dec_ids,)):
        intentos_por[att["decision_id"]].append(_nodo(CLAVES_ATTEMPT, att))
    apps = {a["decision_id"]: a for a in _filas_dict(conn, _SQL_APPLICATION, (dec_ids,))}
    entidad_ids = set()
    cadenas: list[tuple[dict, dict, int | None]] = []
    for fila in filas:
        grupo, campana, camp_id = _cadena(fila)
        cadenas.append((grupo, campana, camp_id))
        entidad_ids.add(fila["entidad_id"])
        if camp_id is not None:
            entidad_ids.add(camp_id)
    estados = {e["ad_entity_id"]: e for e in _filas_dict(conn, _SQL_ESTADO, (list(entidad_ids),))}
    registros: list[dict] = []
    for fila, (grupo, campana, camp_id) in zip(filas, cadenas, strict=True):
        st_camp = estados.get(camp_id) or {}
        campana["status"] = st_camp.get("status")
        st_ent = estados.get(fila["entidad_id"]) or {}
        inputs = fila["inputs"] if isinstance(fila["inputs"], dict) else {}
        registros.append(
            _nodo(
                CLAVES_REGISTRO,
                {
                    "decision": _nodo(
                        CLAVES_DECISION,
                        {
                            "id": fila["decision_id"],
                            "cycle_id": fila["cycle_id"],
                            "kind": fila["decision_kind"],
                            "decided_at": fila["decided_at"],
                            "config_version_id": fila["config_version_id"],
                            "data_observed_at": fila["data_observed_at"],
                            "window_start": fila["window_start"],
                            "window_end": fila["window_end"],
                            "old_value": fila["old_value"],
                            "new_value": fila["new_value"],
                            "value_currency": fila["value_currency"],
                            "inputs": inputs,
                        },
                    ),
                    "entidad": _nodo(
                        CLAVES_ENTIDAD,
                        {
                            "id": fila["entidad_id"],
                            "kind": fila["entidad_kind"],
                            "match_type": fila["match_type"],
                            "keyword_text": fila["keyword_text"],
                            "name": fila["entidad_name"],
                            "external_id": fila["entidad_external_id"],
                            "ad_group": _nodo(CLAVES_AD_GROUP, grupo),
                            "campana": _nodo(CLAVES_CAMPANA, campana),
                        },
                    ),
                    "apply_attempts": intentos_por[fila["decision_id"]],
                    "decision_application": _nodo(
                        CLAVES_APPLICATION, apps.get(fila["decision_id"]) or {}
                    ),
                    "readback": _nodo(CLAVES_READBACK, st_ent),
                    "ciclo": _nodo(
                        CLAVES_CICLO,
                        {"mode": fila["ciclo_mode"], "started_at": fila["ciclo_started_at"]},
                    ),
                    "replay": _replay(
                        inputs,
                        fila["decision_kind"],
                        fila["new_value"],
                        fila["value_currency"],
                    ),
                },
            )
        )
    return registros


def _ack_id(ack) -> str | None:
    if not isinstance(ack, dict):
        return None
    for contenedor in ("keywords", "targetingClauses"):
        bloque = ack.get(contenedor)
        if isinstance(bloque, dict):
            for item in bloque.get("success") or []:
                if isinstance(item, dict):
                    encontrado = item.get("keywordId") or item.get("targetId")
                    if encontrado is not None:
                        return str(encontrado)
    for clave in ("keywordId", "targetId"):
        if ack.get(clave) is not None:
            return str(ack[clave])
    return None


def _acos_ventana(inputs: dict) -> str:
    bids = ((inputs or {}).get("ventanas") or {}).get("bids") or {}
    cost, rev = bids.get("cost"), bids.get("ad_revenue")
    if cost is None or rev is None:
        return "sin dato"
    costo, venta = Decimal(str(cost)), Decimal(str(rev))
    if venta == 0:
        return f"sin dato ({costo}/{venta})"
    pct = (costo / venta * Decimal(100)).quantize(Decimal("0.01"))
    return f"{pct} ({costo}/{venta})"


def _celda(valor) -> str:
    return str(valor if valor is not None else "").replace("|", "/")


def _fila_md(reg: dict) -> str:
    dec = reg["decision"]
    ent = reg["entidad"]
    inputs = dec.get("inputs") or {}
    keyword = ent.get("keyword_text") or ent.get("name")
    campana = (ent.get("campana") or {}).get("name")
    intentos = reg.get("apply_attempts") or []
    payload = intentos[-1]["request_payload"] if intentos else {}
    ack = intentos[-1]["ack"] if intentos else {}
    bid_req = payload.get("bid") if isinstance(payload, dict) else None
    cadena = (
        f"{dec.get('old_value')} → {dec.get('new_value')} → {bid_req} → "
        f"{_ack_id(ack)} → {(reg.get('readback') or {}).get('current_bid')}"
    )
    motivo = inputs.get("motivo")
    factor = inputs.get("factor")
    return (
        f"| {_celda(dec.get('id'))} | {_celda(inputs.get('platform'))} | "
        f"{_celda(campana)} | {_celda(keyword)} | {_celda(cadena)} | "
        f"{_celda(motivo)} / {_celda(factor)} | {_celda(_acos_ventana(inputs))} | "
        f"{_celda(inputs.get('target_acos_pct_usado'))} |"
    )


def _render_md(registros: list[dict], fecha: str, ciclos: list[int]) -> str:
    lineas = [
        f"# Expediente adversarial {fecha}",
        "",
        f"Ciclos: {', '.join(str(c) for c in ciclos)}",
        f"Decisiones aplicadas: {len(registros)}",
        "",
        "| id | pais/plataforma | campana | keyword/target |"
        " bid antes → decidido → request → ack (id) → readback |"
        " motivo/factor | ACoS ventana bids | target |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lineas.extend(_fila_md(reg) for reg in registros)
    for reg in registros:
        lineas.extend(
            [
                "",
                f"## Decision {reg['decision']['id']}",
                "",
                "```json",
                json.dumps(reg["decision"]["inputs"], ensure_ascii=False, indent=2),
                "```",
            ]
        )
    return "\n".join(lineas) + "\n"


def _render_prompt(n: int, fecha: str, ciclos: list[int]) -> str:
    nombres = f"dossier_{fecha}.md, dossier_{fecha}.json"
    ciclos_txt = ", ".join(str(c) for c in ciclos)
    return (
        f"# Verificador adversarial — primeras {n} mutaciones aplicadas\n"
        f"\n"
        f"Fecha UTC de corrida: {fecha}. Ciclos: {ciclos_txt}.\n"
        f"\n"
        f"## Rol\n"
        f"Verificador adversarial de las primeras {n} mutaciones aplicadas "
        f"(fecha {fecha}, ciclos {ciclos_txt}). Sin elogios. Solo lectura. "
        f"Sin red. No modificar archivos.\n"
        f"\n"
        f"## Insumos\n"
        f"- {nombres}\n"
        f"- este repo (solo lectura)\n"
        f"\n"
        f"## Reglas\n"
        f"- docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md\n"
        f"- docs/CONTEXTO.md\n"
        f"- app/optimizer/bid.py bandas (ACoS>1.35× → −25%, >1.15× → −12%,"
        f" <0.85× → +15%; piso/techo; quantize 2 dec)\n"
        f"- app/optimizer/goals.py\n"
        f"- app/apply.py\n"
        f"- cooldown 7d\n"
        f"- quota\n"
        f"\n"
        f"## Tarea por decision\n"
        f"Recalcular ACoS, banda, bid esperado vs new_value vs request vs ack"
        f" vs readback; madurez; data_observed_at≤decided_at; goal scope;"
        f" pause NO aplicaba; replay_coincide true; moneda del goal.\n"
        f"\n"
        f"## Formato\n"
        f"Tabla OK/DIVERGE, hallazgos por severidad, lo no verificable,"
        f" veredicto de 1 linea.\n"
    )


def _publicar(out_dir: Path, archivos: dict[str, str]) -> None:
    vieja = os.umask(0o077)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if out_dir.is_symlink():
            raise PermissionError(f"{out_dir} es un symlink: el dossier exige un directorio real")
        info = out_dir.stat()
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PermissionError(f"{out_dir} no es del usuario del proceso (uid {info.st_uid})")
        if stat.S_IMODE(info.st_mode) != 0o700:
            out_dir.chmod(0o700)
        for nombre, contenido in archivos.items():
            destino = out_dir / nombre
            fd, temporal = tempfile.mkstemp(dir=out_dir, prefix=".dossier-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(contenido)
                os.replace(temporal, destino)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temporal)
                raise
    finally:
        os.umask(vieja)


def escribir_salidas(
    registros: list[dict],
    out_dir: Path,
    ciclos: list[int],
    *,
    generado: dt.datetime | None = None,
) -> int:
    """Construye json/md/prompt en memoria, escanea, y solo entonces publica."""
    momento = generado or dt.datetime.now(dt.UTC)
    fecha = momento.strftime("%Y%m%d")
    payload = {
        "generado_utc": momento.isoformat(timespec="seconds"),
        "ciclos": [int(c) for c in ciclos],
        "registros": registros,
    }
    json_text = scrub(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    md_text = scrub(_render_md(registros, fecha, ciclos))
    prompt_text = scrub(_render_prompt(len(registros), fecha, ciclos))
    hits = escanear_secretos(json_text + md_text + prompt_text)
    if hits:
        print(
            "secretos detectados, no se escribe nada: " + scrub(", ".join(hits)),
            file=sys.stderr,
        )
        return 1
    _publicar(
        out_dir,
        {
            f"dossier_{fecha}.json": json_text,
            f"dossier_{fecha}.md": md_text,
            "prompt_revisor.md": prompt_text,
        },
    )
    print(f"dossier escrito: {out_dir / f'dossier_{fecha}.json'}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python tools/dossier_adversarial.py",
        description=(
            "Expediente READ-ONLY de decisiones aplicadas (ORBIT 05 2.1a). "
            "Cero mutaciones; SOLO SELECT; salida saneada fail-closed."
        ),
    )
    parser.add_argument(
        "--ciclos",
        nargs="+",
        type=int,
        required=True,
        metavar="ID",
        help="ids de optimizer_cycle (uno o mas)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="DIR",
        help="directorio de salida (lo crea si falta, umask 077; 700/600)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        print(
            "ORBIT_DSN_READ no esta definido: fail-closed, cero lecturas",
            file=sys.stderr,
        )
        return 2
    try:
        conn = connect(dsn)
    except Exception as exc:
        print(f"no se pudo conectar: {scrub(str(exc))}", file=sys.stderr)
        return 1
    try:
        registros = construir_registros(conn, args.ciclos)
        return escribir_salidas(registros, args.out, args.ciclos)
    except Exception as exc:
        print(f"dossier adversarial fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
