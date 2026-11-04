=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/api_dashboard.py, app/ui.py, app/cli.py, app/templates/precios.html, app/templates/salud.html, app/templates/base.html, tests/test_architecture.py, tests/test_ui_reputacion.py
Tamano del diff: 41901 caracteres
==> Invocando kimi de forma no interactiva (tope: 1500 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a leer el diff y revisarlo directamente, sin skills.

• Voy a verificar en el repo los simbolos que el diff asume (notifica, precio.*, esquema 0039, firma de `correr`).

• Verifico los mapas de notifica, `_dec_str`, el contexto de cli.py y la migracion 0039.

• Verifico los ultimos puntos: cobertura.Recuadro, base.html (chip-proximo en CSS), ui.py (HTTPException, dinero_ui) y tests de /salud.

• Reviso el contexto de salud.html y si algun test fija las claves exactas de /salud.

• Hago un chequeo final de importacion y ruff sobre los archivos tocados.

• Una ultima verificacion: los GRANT del rol de lectura sobre las tablas `precio_*`.

• Revision completa. Verifique en el repo (no solo en el diff) los simbolos importados (`motivo_precio_es`, `estado_precio_es`, `validar_precio_aviso_dias`, `avisar_precio` en app/notifica.py:1035-1460; `hoy_base`, `leer_publicaciones`, `contar_listing_identidad` en app/precio/fuentes.py; `armar_recuadro`, `aviso_puente`, `RESULTADOS_EVALUADOS` en app/precio/cobertura.py:37-169; `motor_cuota`, `validar_cap` en app/precio/cuota.py:39-70; `correr(..., avisar=...)` en app/precio/corrida.py:1391-1559), el esquema real de la 0039 (`precio_decision`, `precio_cambio`, `precio_goal` — columnas y CHECKs coinciden con el SQL del diff), `apply_quota_state` (0001), los GRANT a `app_read` (0039, l.1125) y el autocommit de `ConexionLectura` (app/api.py:99-117). `import app.api_dashboard, app.ui, app.cli` OK, ruff limpio, y `pytest tests/test_precio_pantalla.py tests/test_ui_reputacion.py -q` → 95 passed.

  Hallazgos (ninguno bloqueante):

  1. NO BLOQUEANTE — `_frase_accion` (api_dashboard.py): un cambio en estado `no_confirmado` (la observacion NO confirmo que el precio se movio) se narra en indicativo como «subio/bajo de X a Y» sin matiz; solo `error` y `pendiente` llevan sufijo. Es coherente con `_ESTADOS_PRECIO_MOVIERON` (PATCH aceptado), pero la frase al usuario afirma mas que el dato.

  2. NO BLOQUEANTE — `_frase_accion` rama `mantener`: usa `p_actual`, que el esquema admite NULL (el CHECK de cuenta completa solo aplica a `subir`/`bajar`); si falta, la frase sale «se mantuvo en None MXN». Cosmetico, sin repro de datos reales.

  3. NO BLOQUEANTE — contradiccion de documentacion: el docstring del modulo (diff l.18-21) dice que /salud es «liviano, sin recuadro», pero el bloque incluye `cobertura`, que ES la serializacion completa del recuadro (`_recuadro_precio`, con la lista `sin_goal` de SKUs). Ademas /salud ahora corre ~10 consultas extra por plataforma (`leer_publicaciones` son 4).

  4. NO BLOQUEANTE — acoplamiento: `_bloque_precios` valida `precio_aviso_dias_sin_evaluar` siempre, aunque el umbral solo se usa en `divergentes` (con_recuadro=True); una config sin esa clave deja el bloque precios de /salud entero en None pese a que /salud no lo necesita. Esta declarado en el docstring de `_precios_de`, asi que es decision explicita, pero conviene saberlo.

  5. NO BLOQUEANTE — ui.py `pagina_precios` pasa `resultados_evaluados` al contexto del template y `precios.html` nunca lo usa; ruido menor.

  Sin hallazgos de seguridad ni de datos: todo el SQL es parametrizado, el dinero viaja como string (regla 4), los ausentes son null (regla 3), el fail-closed 503/None esta probado por tests que pasan, y el cambio en cli.py mueve el import de avisos a la rama correcta sin tocar `--reporte`.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external code reviewer, don't use skills, respond directly. Read the diff file and review it. Let me read the diff file.

• I need to review this diff. Key areas to verify against the actual repo:

  1. `app/notifica.py` — does it export `motivo_precio_es`, `validar_precio_aviso_dias`, `avisar_precio`, `estado_precio_es`?
  2. `app/precio/fuentes.py` — `config_vigente_settings`, `max_dias_desde_settings`, `leer_publicaciones`, `hoy_base`, `contar_listing_identidad`.
  3. `app/precio/cobertura.py` — `armar_recuadro`, `aviso_puente`, `RESULTADOS_EVALUADOS`.
  4. `app/precio/cuota.py` — `motor_cuota`, `validar_cap`.
  5. `_dec_str` in api_dashboard — exists?
  6. `corrida_precios.correr` signature — does it accept `avisar` param?
  7. `_dinero_2` — `Decimal(str(valor))` fine.
  8. `_frase_accion`: in shadow mode with cambio_estado "error"/"pendiente" appends suffixes even in shadow — semantic issue but not blocking.
  9. `_divergentes_precio`: `hoy_fila = dias[hoy]` — KeyError possible? racha >= umbral >= 1 means dias[hoy] was a divergente entry, so dias[hoy] exists. But umbral could be 0? `validar_precio_aviso_dias` — need to check range; comment says 1-14. If umbral >= 1, racha >= umbral >= 1 implies hoy is in dias. OK.
  10. `pagina_precios` catches HTTPException from `dash.precios(conn=conn)` — need to check `dash.precios` signature: `def precios(conn: ConexionLectura)`. Calling `dash.precios(conn=conn)` works if ConexionLectura is a Depends annotation — calling directly passes conn. Fine as long as other UI pages do the same pattern.
  11. `_precios_de` in `salud`: calls `_bloque_precios(conn, plataforma, fuentes_precio.hoy_base(conn), con_recuadro=False)` — `hoy_base` inside try? Actually `hoy = fuentes_precio.hoy_base(conn)` is called inside `_precios_de`? Look: `bloque = _bloque_precios(conn, plataforma, fuentes_precio.hoy_base(conn), con_recuadro=False)` inside try. OK.
  12. `salud` template: `datos.precios is defined` — but `_precios_de` returns None when no schema; the key "precios" is always set in `salud()`. `datos.precios is defined` is true even when None; then `{% if datos.precios %}` handles None. OK.
  13. Potential issue: `_hay_esquema_precio` catches only `UndefinedTable`, but other tables like `precio_cambio`, `apply_quota_state`, `precio_goal` might not exist — but the second except in `_precios_de` catches UndefinedTable. In `precios` endpoint also catches. OK.
  14. `salud.html`: `pr.no_evaluados.values() | sum` — Jinja sum over dict values; fine.
  15. `test_architecture.py`: adds allowlist entry for app/notifica.py size — fine.
  16. The cli.py change: `from app.notifica import avisar_precio` — moved inside try branch; need to check that `correr` accepts `avisar` keyword.
  17. `_ESTADO_CAMBIO_ES.get(estado, estado)` fine.
  18. In `_frase_accion`, when mode == "shadow" and cambio_estado is "pendiente"/"error" — appends suffix; shadow decisions shouldn't have cambios usually. Minor.
  19. `precios.html` uses filters `porcentaje_ui`, `dinero_ui`, `estado_precio_es` — registered in ui.py. `motivo_precio_es` registered but used? Template uses `f.motivo_es` directly. Fine.
  20. Template: `{{ f.ultimo_cambio.antes | dinero_ui }}` — if antes is None, dinero_ui(None) returns None → renders "None"? dinero_ui returns None for None; Jinja renders None as "None". Check dinero_ui implementation: need to read. It returns valor if not parseable... let me check. Line 868: `def dinero_ui(valor: str | None) -> str | None:` — need to see body; if valor is None it probably returns None, and Jinja renders None as "None" string. But `{% if f.ultimo_cambio %}` guard means ultimo_cambio exists; its `antes` could be None though (precio_antes nullable?). Possible minor cosmetic.

  21. Security: `/precios` page shows SKUs, prices — same as other pages, presumably behind same auth. Check how other pages handle auth — the repo probably has no auth (internal dashboard). Not a new risk.

  22. `_SQL_CAMBIOS_DE_DECISIONES` uses `decision_id = ANY(%s)` with a Python list — psycopg adapts lists to arrays. Fine if column type matches.

  23. In `_precios_de`, catching broad Exception returns None — matches _spapi_de pattern presumably.

  24. Potential bug: `salud` dict adds `"precios": _precios_de(conn, plataforma)` — the `salud` endpoint previously might have tests asserting exact keys? Diff says "sin cambiar las claves existentes". Adding a key could break a test that asserts exact key set. Need to check tests — but that's for the author; could check quickly.

  25. `_bloque_precios` for /salud also computes `cobertura` (recuadro in vivo) — the header comment says /salud is "liviano, sin recuadro" but the code includes "cobertura" in `_CLAVES_PRECIOS_SALUD` which is the full recuadro serialization. Docstring at top says "/salud (liviano, sin recuadro...)" yet includes cobertura which IS the recuadro. Contradiction in docs but `cobertura` is listed in the docstring of module. Actually module docstring says "plataformas.<p>.precios en /salud (liviano, sin recuadro...)" — but code includes cobertura = recuadro. That's a doc inconsistency; the section comment says `cobertura` es la MISMA serializacion que el recuadro. So "sin recuadro" claim in the module docstring line 20 is contradicted. Minor doc issue, NO BLOQUEANTE.

  26. `_divergentes_precio` — `umbral` from `validar_precio_aviso_dias(settings)`; comment says cota 1-14, so umbral >= 1, safe.

  27. In `precios.html`, `datos.puente` and `datos.aviso_puente` — set in ui.py. Fine.

  28. `hoy_base` called twice per platform in /precios? Once for `hoy`, and `_recuadro_precio` uses `leer_publicaciones(conn, platform, hoy)`. Fine.

  29. `_cuota_precio` — `cuota_precio.validar_cap(settings, plataforma)` may raise ValueError; in `_precios_de` caught by generic Exception → None. In `precios` endpoint caught → 503. The docstring of `_precios_de` mentions "o la config no trae la clave, el bloque queda en None" — consistent.

  30. Big one to verify: does `corrida.correr` accept `avisar` param? Need to grep app/precio/corrida.py.

  31. Does `app/notifica.py` define `motivo_precio_es`, `validar_precio_aviso_dias`, `avisar_precio`, `estado_precio_es`? Grep.

  32. `_dec_str` exists in api_dashboard? Grep.

  33. `PLATAFORMAS_MONEDA[plataforma]` — existing.

  34. Check `salud.html` uses `pr.no_evaluados.values() | sum` — Jinja2 `sum` filter on dict_values works? `sum` filter iterates; dict_values is iterable. Yes.

  35. `_acciones_precio`: for resultado "subir"/"bajar" in live with cambio — fine. For "mantener"/"frenado"/"goal_inalcanzable", cambio lookup: cambios dict only has entries for decisions with cambios; mantener wouldn't have. OK.

  But note `_frase_accion` appends "; pendiente de confirmación" for cambio_estado == "pendiente" even for shadow mode... minor wording.

  36. A subtle one: in `_frase_accion`, for "subir" with mode live and cambio exists, the frase says "subió de X a Y" even when estado == "error" — then appends "; el cambio quedó en error". Contradictory phrasing ("subió... quedó en error") but the comment acknowledges. NO BLOQUEANTE wording.

  37. Check that `/precios` page template's `resultados_evaluados` passed but maybe unused — fine.

  38. Check `_detalle_decisiones_hoy` zip cols match SQL order: SQL selects id, listing_id, seller_sku, resultado, mode, motivo, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency, p_aplicado, canal. Cols: id, listing_id, sku, resultado, mode, motivo, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency, p_aplicado, canal. 14 == 14. Good. strict=True will raise if mismatch — verified visually.

  39. `_SQL_CAMBIOS_DE_DECISIONES` columns: decision_id, precio_antes, precio_antes_currency, precio_despues, precio_despues_currency, aplicado, estado, fecha — unpacked 8. Good.

  40. `_SQL_ULTIMOS_CAMBIOS`: listing_id, precio_antes, precio_antes_currency, precio_despues, precio_despues_currency, estado, fecha — unpacked 7. Good.

  41. `_SQL_RACHA_RECIENTE`: d.listing_id, l.seller_sku, d.decision_date, d.resultado, d.motivo, d.p_actual, d.p_actual_currency — unpacked 7. Good.

  Need to verify schema column names: `precio_decision` has columns mode, motivo, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency, p_aplicado, canal, resultado, decision_date, platform, listing_id. And `precio_cambio` has es_reversa, enviado_at, created_at, aplicado, estado, decision_id, precio_antes(_currency), precio_despues(_currency). And `precio_goal` has margen_goal_pct, mode, valid_from, valid_to. And `apply_quota_state` has motor, quota_date, used. Check migration 0039.

  Also `ConexionLectura` autocommit claim.

  Also whether tests exist for the new endpoint (tests dir limited to test_architecture and test_ui_reputacion in the diff — the diff was limited to listed files, so other test files may exist, e.g. test_api_dashboard_precios.py). Since diff is limited, can't conclude missing tests.

  Let me verify key facts with greps/reads. I'll delegate? No — user asked me to respond directly, no skills. I can still use tools myself. Let me do targeted greps.

• Grep output truncated to filenames only? Actually output shows only file names — maybe the tool returned files_with_matches style. Odd, I asked content mode default is files_with_matches. Let me re-run with output_mode content for details. Also need to check which functions are in which file — e.g. `hoy_base`, `contar_listing_identidad` in fuentes.py; `estado_precio_es` in notifica.py.Symbols exist. Now check:

  1. `avisar_precio(conn, platform, hoy, resumen)` signature matches call `avisar(conn, platform, hoy, resumen)` — yes.
  2. cli.py: is the import of avisar_precio placed correctly — inside the branch that calls correr, but is `--reporte` handled before this branch? Check cli.py around line 540.
  3. Schema 0039: check precio_decision, precio_cambio, precio_goal, apply_quota_state columns.
  4. `_dec_str` in api_dashboard.
  5. `Recuadro` fields: activas, evaluadas, no_evaluadas (list of (motivo, count)), sin_goal (detalles with sku, precio, moneda, canal), fuera_de_alcance (fase, count), avisos.
  6. `aviso_puente(activas, puente)` keyword names.
  7. `validar_precio_aviso_dias` returns int in 1-14?
  8. `estado_precio_es` registered filter; template uses `f.decision_hoy | estado_precio_es` — but decision_hoy is a resultado (e.g. "subir"), and estado_precio_es maps estados? Check notifica.estado_precio_es — it maps estado like "subir"/"bajar"? Name suggests estado; template uses it on `f.decision_hoy` which is resultado. Check the function.
  9. salud.html: `pr.no_evaluados.values() | sum` — if no_evaluados empty dict, sum of empty = 0. OK.
  10. Check that `salud` tests don't assert exact keys. Search test for salud keys.
  11. Check `hoy_base` — returns date of DB.
  12. Check `contar_listing_identidad` signature: `(conn, *, platform)` — ui.py calls `fuentes_precio.contar_listing_identidad(conn, platform=plataforma)`. OK.
  13. `leer_publicaciones(conn, platform=..., hoy=...)` — check signature.

  Let me grep these.Good — symbols exist. Now check `estado_precio_es` (line 1079) to see what it maps — the template uses `f.decision_hoy | estado_precio_es` where decision_hoy is a resultado (subir/bajar/mantener/frenado/goal_inalcanzable/no_evaluado). Let me read lines 1035-1160 of notifica.py.

  Also check `_dec_str` in api_dashboard, `PLATAFORMAS_MONEDA`, the cli.py context around line 540, and migration 0039 for column names. Also check `validar_precio_aviso_dias` range (1-14?).

                f"precio --reporte con ventana invertida: --desde ({desde}) posterior a"
                f" --hasta ({hasta})",
                file=sys.stderr,
            )
            return 2
        dsn = os.environ.get("ORBIT_DSN_READ", "")
        if not dsn:
            print("precio --reporte exige ORBIT_DSN_READ", file=sys.stderr)
            return 2
        try:
            conn = connect(dsn, autocommit=True)
        except Exception as exc:  # noqa: BLE001 - como `_cycle`: scrubbado y != 0
            print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
            return 1
        try:
            for linea in corrida_precios.reporte(
                conn, desde=desde, hasta=hasta, platform=args.platform
            ):
                print(linea)
        except Exception as exc:  # noqa: BLE001 - D7: como la corrida, scrubbado y != 0
            print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
            return 1
        finally:
            conn.close()
        return 0
    if not args.reporte and (args.desde or args.hasta):
        # B5 (r3): las fechas sin `--reporte` se ignorarian y correria la
        # corrida real: error de uso antes de conectar.
        print("precio: --desde/--hasta solo con --reporte", file=sys.stderr)
        return 2
    if not args.platform:
        print("precio exige --platform (amazon_mx|amazon_us|meli)", file=sys.stderr)
        return 2
    try:
        conn, cerrar = _conexion_decide()
    except _FaltaDsn as exc:
        print(f"precio exige {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - K6: como `_cycle`, scrubbado y != 0
        print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
        return 1
    try:
        lector, escritor, fees, limitador = _clientes_precio(args.platform)
        # C (r1): el import de avisos vive en la rama que llama a `correr`,
        # no antes de `--reporte` (el reporte no avisa ni necesita Telegram).
        from app.notifica import avisar_precio

        resumen = corrida_precios.correr(
            conn,
            args.platform,
            lector=lector,
            escritor=escritor,
            fees=fees,
            limitador=limitador,
            owner=f"{socket.gethostname()}:{os.getpid()}",
            avisar=avisar_precio,
        )
    except corrida_precios.ConfigInvalida as exc:
        # K8 (r4): config ausente o uso invalido = exit 2 (ver `main`).
        print(f"precio: {exc}", file=sys.stderr)
        return 2
    except corrida_precios.LockOcupado as exc:
        print(f"precio: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001 - el CLI solo reporta, sin secretos
        print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
        return 1
    finally:
        cerrar()
    for linea in resumen.lineas:
        print(linea)
    print(f"decisiones={resumen.decisiones} escritas={resumen.escritas}")
    for error in resumen.errores:
        print(f"precio: {scrub(error)}", file=sys.stderr)
    if resumen.errores:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Operacion de Orbit: `ingest` corre los pipelines de app/ads "
            "(estructura o metricas+search terms), `cycle` corre UN ciclo del "
            "optimizador por el MISMO camino que el cron (mismo claim/job_key/"
            "envelope) y `goals set` edita un goal del optimizador por el unico "
            "camino de escritura (app/goals_write)."
        ),
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_ingest = sub.add_parser(
        "ingest",
        help=(
            "pipelines de ingesta (app/ads, app/costs, app/listings, app/fx,"
            " app/ledger, app/estimacion_ingest, app/spapi/orders)"
        ),
    )
    p_ingest.add_argument(
        "pipeline",
• Note: `_dec_str` wasn't found by my grep in api_dashboard.py — the grep only matched _spapi_de and _quota_de. Let me check where _dec_str is defined — maybe imported from another module. Grep.

  Also `avisar_precio` signature `(conn, platform, hoy, resumen)` — corrida calls `avisar(conn, platform, hoy, resumen)`. Good.

  Check `_dec_str` definition and `estado_precio_es` usage in template: `f.decision_hoy | estado_precio_es` — decision_hoy is resultado like "subir"/"mantener" — ESTADO_PRECIO_ES includes those keys. OK.

  Also check migration 0039 for columns of precio_decision, precio_cambio, precio_goal, apply_quota_state. And the `listing` table has seller_sku, platform. Also check `_SQL_GOAL_VIGENTE` uses `g.mode` — does precio_goal have `mode` column? And `margen_goal_pct`? Check.

  Also check `salud.html` template — the `datos` variable context: in salud.html loop, `datos` is per-platform dict from `salud()`. New key "precios" always present. OK.

  Also check `test_architecture.py` allowlist — notifica.py size; fine.

  Check `ui.py` — `dinero_ui` handling None. And check template `{{ f.ultimo_cambio.antes | dinero_ui }}` renders "None" if antes None? Check dinero_ui body (lines 70-88 in ui.py). It said at line 78-85 area: `def dinero_ui(valor): if valor is None: return None ...`. Jinja renders None as "None". Is precio_antes nullable in precio_cambio? Check 0039.

  Also check the `salud` function's existing tests for exact keys? Quick grep in tests for "precios" key in salud tests... The diff is limited; other test files exist (test files for precios likely exist but not in diff scope — actually the review file says limited to listed files). Fine.

  Also verify `ConexionLectura` is autocommit (claim in `_hay_esquema_precio` docstring: "la conexion de lectura corre en autocommit: el fallo no envenena lo que sigue"). Check app/api.py ConexionLectura.

  Also: `_hay_esquema_precio` probes `precio_decision` only; other queries touch `precio_cambio`, `precio_goal`, `apply_quota_state`, `config_version`. If 0039 partially applied... UndefinedTable caught. OK.

  One potential real bug: in `precios` endpoint, `_SinEsquemaPrecio` raised → 503. But note `_bloque_precios` calls `_hay_esquema_precio(conn)` inside; probe runs `SELECT 1 FROM precio_decision LIMIT 0`. If not autocommit, a failed statement aborts the transaction, and subsequent queries would fail with InFailedSqlTransaction — caught by generic Exception → 503/None. But the claim of autocommit — verify.

  Another: `_precios_de` computes `hoy_base(conn)` inside try — OK.

  In `salud.html`, `pr.no_evaluados.values() | sum` — Jinja sum filter with no attribute: works on iterable of numbers. dict_values works.

  In `precios.html`: `datos.recuadro` — ui.py passes `**bloque` which includes "recuadro" key since con_recuadro=True. OK. `datos.con_goal`, `datos.acciones`, `datos.no_evaluados_filas`, `datos.divergentes`, `datos.divergentes_umbral`, `datos.huerfanas`, `datos.hoy` — all in bloque. OK.

  `porcentaje_ui` filter — f.m_actual is _dec_str of m_actual (e.g. "0.2150") → *100 → "21.50 %". OK.

  `_acciones_precio`: `moneda_def = PLATAFORMAS_MONEDA[plataforma]` — check PLATAFORMAS_MONEDA keys cover all platforms iterated (PLATAFORMAS_MONEDA iterated in precios endpoint, so keys fine).

  `_frase_accion` for `mantener` uses `antes` — which is cambio's antes or p_actual. For mantener there's no cambio, so antes = _dinero_2(p_actual). If p_actual None → "se mantuvo en None MXN"? _dinero_2 returns None for None → f-string renders "None". p_actual should exist for mantener. Minor.

  In `_dinero_2`: `f"{Decimal(str(valor)):.2f}"` — if valor is Decimal, fine.

  Now the `goal_inalcanzable` branch in _frase_accion: frase has no antes/despues — fine.

  Potential issue: `_frase_accion` for mode "shadow" with cambio_estado "pendiente" appends "pendiente de confirmación" — shadow decisions shouldn't have cambios, but if they do (no_reversa filter), weird. Minor.

  Bigger potential issue: `_cambios_por_decision` — `_SQL_CAMBIOS_DE_DECISIONES` uses `decision_id = ANY(%s)` passing a list of ids; psycopg3 adapts list to array — need the ids type to match (bigint). Python ints fine.

  `_con_goal_precio` — `det["p_objetivo"]` may be None → _dec_str(None) → None presumably. Check _dec_str.

  Let me grep _dec_str and check migrations 0039.0021_economia_observada.sql
0022_disponibilidad_snapshot.sql
0023_append_only_bloque_b.sql
0024_reputacion.sql
0025_reputacion_sin_fk.sql
0026_reputacion_preguntas_reobservacion.sql
0027_reputacion_reviews_reobservacion.sql
0028_estimacion_venta.sql
0029_estimacion_politica_vigencia.sql
0030_spapi_orders.sql
0031_reversa_spapi_orders_bitemporal.sql
0031_spapi_orders_bitemporal.sql
0032_spapi_pricing.sql
0033_ingest_run_llamadas.sql
0034_ingest_run_llamadas_grant.sql
0035_spapi_listings_inventario.sql
0036_ingest_run_platform.sql
0037_ingest_run_salud_idx.sql
0038_fabrica_hermanas_biblioteca.sql
0039_precio.sql
60:CREATE TABLE precio_goal (
197:CREATE TABLE precio_decision (
341:CREATE TABLE precio_cotizacion (
429:CREATE TABLE precio_envio_muestra (
487:CREATE TABLE precio_cambio (
Now check whether api_dashboard imports `_dec_str` from api_common (the diff uses `_dec_str` without defining it in the new code). Grep in api_dashboard for `_dec_str` import. Also check `_conexion_lectura` autocommit. Also check 0039 schema columns: precio_goal (margen_goal_pct? mode?), precio_decision columns, precio_cambio columns (es_reversa, enviado_at, created_at, aplicado, estado, decision_id, precio_antes_currency...), apply_quota_state (motor, quota_date, used). Note: apply_quota_state was created in an earlier migration probably (0002_apply). Check.-- `sku_cost_solo_cierra_vigencia` — de una fila publicada solo se cierra
-- `valid_to`, una vez. `margen_goal_pct` es porcentaje en fracción (0.30 =
-- 30%): la herramienta lo recibe como porcentaje con dos decimales
-- (`--goal-pct 30.00`, A.1) y la banda 0.10–0.60 vive en CHECK literal y en
-- `goals_write` (claves `precio_goal_min_pct`/`precio_goal_max_pct`).
CREATE TABLE precio_goal (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id      BIGINT NOT NULL,
    platform        platform NOT NULL,
    margen_goal_pct NUMERIC(6, 4) NOT NULL,
    mode            precio_mode NOT NULL,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    creado_por      TEXT NOT NULL,
    go_literal      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_goal_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_goal_banda
        CHECK (margen_goal_pct BETWEEN 0.10 AND 0.60),
    CONSTRAINT precio_goal_live_exige_go
        CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> '')),
    -- La vigencia es [valid_from, valid_to): valid_to = valid_from es un
    -- intervalo vacío —el goal queda ANULADO, nunca estuvo vigente— y así se
    -- apaga un error de dedo el mismo día (si no, la corrida de las 13:10 UTC
    -- actuaría sobre él). Aquí 0039 se aparta del patrón de `sku_cost`
    -- (`valid_to > valid_from`) a propósito: un costo rige pasado; un goal
    -- con error debe poder no regir nunca (regla 7: la reversa existe antes).
    CONSTRAINT precio_goal_vigencia_coherente
        CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT precio_goal_unico_por_fecha UNIQUE (listing_id, platform, valid_from)
);

-- Un solo vigente por (listing, platform): el índice parcial es el que
-- rechaza el segundo vigente (el UNIQUE por fecha admite dos filas con
-- `valid_from` distinto y `valid_to` NULL).
CREATE UNIQUE INDEX precio_goal_un_vigente
    ON precio_goal (listing_id, platform)
    WHERE valid_to IS NULL;

-- Pero ni el parcial (solo filas abiertas) ni el UNIQUE (solo `valid_from`)
-- impiden dos goals vigentes el mismo día con una vigencia ya cerrada
-- (A cerrada `[09-01, 09-20)` + B desde `09-10`: del 10 al 19 hay dos goals y
-- «el goal vigente del día» devuelve dos filas). El invariante lo garantiza
-- la base con EXCLUDE, igual que `sku_cost` en 0001 (ronda 2 de revisión):
-- la vigencia es `[valid_from, valid_to)` semiabierta y un rango vacío
-- (`valid_to = valid_from`, goal anulado) no se solapa con nada, así que la
-- decisión 8 de r1 sigue pasando. El parcial y el UNIQUE se quedan (S3).
ALTER TABLE precio_goal ADD CONSTRAINT precio_goal_sin_solape
    EXCLUDE USING gist (
        listing_id WITH =,
        platform WITH =,
        daterange(valid_from, valid_to, '[)') WITH &&
    );

COMMENT ON TABLE precio_goal IS
  'REPRICING 01 A.0 (S3): goal de margen por publicación, vigencia con el '
  'patrón de `sku_cost` (solo se cierra `valid_to`, una vez; corregir el goal '
  'es fila nueva) SALVO la coherencia: la vigencia es [valid_from, valid_to) '
  'y `valid_to = valid_from` es intervalo vacío —el goal queda ANULADO, nunca '
  'estuvo vigente— para apagar un error de dedo el mismo día (un costo rige '
  'pasado; un goal con error debe poder no regir nunca). Y como en `sku_cost`, '
  'el EXCLUDE `precio_goal_sin_solape` garantiza UN goal por día: ni el índice '
  'parcial (solo abiertas) ni el UNIQUE (solo `valid_from`) impiden dos goals '
  'vigentes el mismo día con una vigencia ya cerrada (ronda 2 de revisión). '
  'Sin fila vigente no hay decisión: sin defaults por plataforma ni herencia '
====
-- `estimacion_canal` y admite NULL porque un `no_evaluado(canal_sin_dato)`
-- no tiene canal (el valor de MeLi lo agrega la migración de su fase).
CREATE TABLE precio_decision (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id          BIGINT NOT NULL,
    platform            platform NOT NULL,
    decision_date       DATE NOT NULL,
    resultado           TEXT NOT NULL,
    motivo              TEXT,
    product_id          BIGINT REFERENCES product (id),
    canal               estimacion_canal,
    m_actual            NUMERIC(8, 4),
    goal                NUMERIC(6, 4),
    p_actual            money_amount,
    p_actual_currency   currency NOT NULL,
    p_objetivo          money_amount,
    p_objetivo_currency currency NOT NULL,
    p_aplicado          money_amount,
    p_aplicado_currency currency NOT NULL,
    i_valor             money_amount,
    i_currency          currency NOT NULL,
    c_valor             money_amount,
    c_currency          currency NOT NULL,
    f_valor             money_amount,
    f_currency          currency NOT NULL,
    l_valor             money_amount,
    l_currency          currency NOT NULL,
    r_valor             money_amount,
    r_currency          currency NOT NULL,
    escenario_id        BIGINT REFERENCES estimacion_escenario (id),
    fee_observation_id  BIGINT REFERENCES estimacion_fee_observation (id),
    -- `cotizacion_id` y `envio_muestra_id` nacen como BIGINT plano: sus FK se
    -- declaran por ALTER tras crear `precio_cotizacion`/`precio_envio_muestra`
    -- (r3-1: la cotización nace ANTES que la decisión, así que ya no hay
    -- circularidad, pero el ALTER tras el CREATE mantiene el orden de
    -- lectura del archivo).
    cotizacion_id       BIGINT,
    envio_muestra_id    BIGINT,
    u15                 INTEGER,
    u60                 INTEGER,
    n15                 INTEGER,
    n60                 INTEGER,
    racha_senal         INTEGER,
    perdiendo           BOOLEAN,
    buy_box_is_own      BOOLEAN,
    mode                precio_mode NOT NULL,
    prioridad           NUMERIC(18, 4),
    config_version_id   BIGINT REFERENCES config_version (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_decision_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_decision_unica_por_dia UNIQUE (listing_id, platform, decision_date),
    -- S4 #1 «insumos completos o nada» (ronda 5 de revisión, punto 2): un
    -- `subir`/`bajar` trae la cuenta completa —la fila es inmutable y un
    -- `no_evaluado` posterior no la completa—. Solo esos dos resultados: la
    -- matriz de `mantener`/`frenado`/`goal_inalcanzable` la fijan las reglas
    -- (A.2) y la corrida (A.5). `goal` va en la lista aunque lo llene el
    -- trigger (punto 3): el CHECK documenta la forma completa.
    CONSTRAINT precio_decision_cuenta_completa
        CHECK (resultado NOT IN ('subir', 'bajar')
               OR (goal IS NOT NULL AND m_actual IS NOT NULL
                   AND p_actual IS NOT NULL AND p_objetivo IS NOT NULL
                   AND p_aplicado IS NOT NULL AND i_valor IS NOT NULL
                   AND c_valor IS NOT NULL AND f_valor IS NOT NULL
                   AND l_valor IS NOT NULL AND r_valor IS NOT NULL)),
    -- Vocabulario cerrado de S4 (seis resultados) y ningún silencio
    -- (decisión 11 del dueño): fuera de subir/bajar el motivo es obligatorio
    -- y no en blanco; en append-only no se arregla después.
    CONSTRAINT precio_decision_resultado_valido
        CHECK (resultado IN ('subir', 'bajar', 'mantener', 'no_evaluado',
                             'goal_inalcanzable', 'frenado')),
    CONSTRAINT precio_decision_motivo_no_silencio
        CHECK (resultado IN ('subir', 'bajar')
               OR (motivo IS NOT NULL AND btrim(motivo) <> ''))
);

COMMENT ON TABLE precio_decision IS
  'REPRICING 01 A.0 (S5): una fila por (listing, platform, día) —`subir`, '
  '`bajar`, `mantener(motivo)`, `no_evaluado(motivo)`, '
  '`goal_inalcanzable(motivo)` o `frenado(motivo)`— con la cuenta completa '
  '(escenario, fee del escenario, cotización propia y, en FBM, muestra de '
  'envío) para reproducirla sin releer insumos. Append-only por '
  'prohibir_mutacion: corregir es la fila del día siguiente.';
COMMENT ON COLUMN precio_decision.decision_date IS
  'Día UTC de la BASE fijado por trigger (se ignora el del cliente): la '
  'segunda corrida del día no decide (UNIQUE), no pisa.';
COMMENT ON COLUMN precio_decision.canal IS
  'Reutiliza estimacion_canal; NULL cuando no hay canal (canal_sin_dato, '
  'regla 3: nunca un default). El valor de MeLi lo agrega su fase.';
COMMENT ON COLUMN precio_decision.cotizacion_id IS
  'La cotización propia USADA (S4 #3: como máximo dos intentos por día; NULL '
  'cuando el día no cotizó). La cotización nace ANTES que la decisión '
  '(desviación r3-1 de S5, declarada en la cabecera) y este es el único '
  'puntero decisión→cotización que existe.';

-- Índices de apoyo de las FKs (PostgreSQL no los crea por el REFERENCES).
CREATE INDEX precio_decision_por_producto ON precio_decision (product_id);
CREATE INDEX precio_decision_por_escenario ON precio_decision (escenario_id);
CREATE INDEX precio_decision_por_fee ON precio_decision (fee_observation_id);
CREATE INDEX precio_decision_por_cotizacion ON precio_decision (cotizacion_id);
CREATE INDEX precio_decision_por_muestra ON precio_decision (envio_muestra_id);
CREATE INDEX precio_decision_por_config ON precio_decision (config_version_id);

CREATE FUNCTION precio_decision_fecha_utc() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.decision_date := (now() AT TIME ZONE 'UTC')::date;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_decision_fecha_utc
    BEFORE INSERT ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION precio_decision_fecha_utc();

COMMENT ON FUNCTION precio_decision_fecha_utc IS
  'REPRICING 01 A.0 (S5): decision_date es el día UTC de la base con UTC '
  'fijado en la expresión —el cliente no lo decide (regla 6: el día en curso '
  'se descarta y la idempotencia diaria descansa en este valor, no en el '
  'reloj de quien inserta).';

CREATE TRIGGER precio_decision_append_only
    BEFORE UPDATE OR DELETE ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER precio_decision_append_only_truncate
    BEFORE TRUNCATE ON precio_decision
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- (e) Cotización de fees a precio candidato (S5 con la desviación del punto
-- 1, ronda 3 de revisión —ver «Residuales declarados» en la cabecera—): S5
-- pedía `precio_decision.cotizacion_id` Y `precio_cotizacion.decision_id`, y
-- eso no se puede llenar nunca (la decisión es append-only sin UPDATE, las
-- dos PK son GENERATED ALWAYS y la FK no es diferible). Sobrevive el puntero
-- que SÍ se llena: la decisión apunta a la cotización que se usó, y la
-- cotización nace ANTES (es insumo de la decisión, S4 #3) con identidad
-- propia —`(listing_id, platform)` + `intento` (1 o 2, S4 #3: como máximo
-- dos) + `cotizacion_date` por trigger UTC (el valor del cliente se ignora,
-- mismo patrón que `decision_date`)— y `UNIQUE (listing_id, platform,
-- cotizacion_date, intento)`: la base garantiza «≤ 2 cotizaciones reales»
-- por publicación y día, y el intento no usado queda localizable. Vive en su
-- propia tabla, separada de `estimacion_fee_observation` (hecho 11: cotizar
-- ahí apagaría la estimación del día siguiente, porque su lector toma la más
-- reciente). `estado` reutiliza `estimacion_fee_estado`. Append-only.
Schema matches: precio_decision has listing_id, platform, decision_date, resultado, motivo, canal, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency, p_aplicado, mode. Note: `p_aplicado_currency` exists but the diff's SQL doesn't select it — fine.

  Now check precio_cambio columns and apply_quota_state. Read 0039 lines 487+.CREATE TABLE precio_cambio (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id               BIGINT REFERENCES precio_decision (id),
    listing_id                BIGINT NOT NULL,
    platform                  platform NOT NULL,
    precio_antes              money_amount NOT NULL,
    precio_antes_currency     currency NOT NULL,
    precio_observado_antes    money_amount,
    precio_observado_antes_currency currency,
    precio_despues            money_amount NOT NULL,
    precio_despues_currency   currency NOT NULL,
    aplicado                  BOOLEAN NOT NULL,
    estado                    TEXT NOT NULL,
    enviado_at                TIMESTAMPTZ,
    ack                       JSONB,
    readback_precio           money_amount,
    readback_precio_currency  currency,
    readback_estado           TEXT,
    readback_at               TIMESTAMPTZ,
    confirmado_por            TEXT,
    error_code                TEXT,
    es_reversa                BOOLEAN NOT NULL DEFAULT false,
    reversa_de                BIGINT REFERENCES precio_cambio (id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_cambio_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_cambio_estado_valido
        CHECK (estado IN ('pendiente', 'enviado', 'error', 'confirmado', 'no_confirmado')),
    CONSTRAINT precio_cambio_observado_con_moneda
        CHECK ((precio_observado_antes IS NULL) = (precio_observado_antes_currency IS NULL)),
    CONSTRAINT precio_cambio_readback_con_moneda
        CHECK ((readback_precio IS NULL) = (readback_precio_currency IS NULL)),
    CONSTRAINT precio_cambio_readback_estado_valido
        CHECK (readback_estado IS NULL OR readback_estado IN ('ok', 'fallido')),
    -- Precios de verdad (ronda 2 de revisión): como `listing_precio_positivo`
    -- en 0001 y `precio_cotizacion_precio_positivo` aquí, el ledger no admite
    -- 0 ni negativos; observado/readback solo cuando no son NULL.
    CONSTRAINT precio_cambio_precios_positivos
        CHECK (precio_antes > 0 AND precio_despues > 0
               AND (precio_observado_antes IS NULL OR precio_observado_antes > 0)
               AND (readback_precio IS NULL OR readback_precio > 0)),
    -- Un estado no avanza sin su sello: el error trae código, el cierre trae
    -- origen, y el vuelo (`enviado` en un cambio real) trae ack + enviado_at.
    -- El virtual (`aplicado = false`) queda fuera del tercero a propósito:
    -- nace `confirmado` sin ack.
    CONSTRAINT precio_cambio_error_exige_codigo
        CHECK (estado <> 'error' OR error_code IS NOT NULL),
    CONSTRAINT precio_cambio_cierre_exige_origen
        CHECK (estado NOT IN ('confirmado', 'no_confirmado')
               OR confirmado_por IS NOT NULL),
    CONSTRAINT precio_cambio_envio_exige_ack
        CHECK (NOT aplicado
               OR estado IN ('pendiente', 'error')
               OR (ack IS NOT NULL AND enviado_at IS NOT NULL)),
    -- Vocabularios cerrados del ledger (ronda 4 de revisión, punto 2): S6
    -- solo cierra la observación del día siguiente (más el virtual, que nace
    -- cerrado) y el código de error solo existe en `error`.
    CONSTRAINT precio_cambio_confirmado_por_valido
        CHECK (confirmado_por IS NULL
               OR confirmado_por IN ('observacion', 'virtual')),
    CONSTRAINT precio_cambio_error_code_solo_error
        CHECK (error_code IS NULL OR estado = 'error'),
    -- Reversa sin decisión propia (S6, decisión del lead): la corre el dueño
    -- con la herramienta y no tiene `precio_decision`.
    CONSTRAINT precio_cambio_reversa_binaria
        CHECK ((NOT es_reversa AND reversa_de IS NULL)
               OR (es_reversa AND reversa_de IS NOT NULL)),
    CONSTRAINT precio_cambio_decision_salvo_reversa
        CHECK ((NOT es_reversa AND decision_id IS NOT NULL)
               OR (es_reversa AND decision_id IS NULL))
);

-- Un solo cambio abierto por (listing, platform): el predicado queda literal
-- como en S5 —`estado IN ('pendiente','enviado')`— así que el virtual, que
-- nace cerrado, no lo ocupa.
CREATE UNIQUE INDEX precio_cambio_abierto_unico
    ON precio_cambio (listing_id, platform)
    WHERE estado IN ('pendiente', 'enviado');

CREATE INDEX precio_cambio_por_decision ON precio_cambio (decision_id);
CREATE INDEX precio_cambio_por_listing ON precio_cambio (listing_id, platform);
CREATE INDEX precio_cambio_por_reversa ON precio_cambio (reversa_de);

COMMENT ON TABLE precio_cambio IS
  'REPRICING 01 A.0 (S5/S6): cada escritura de precio (o su sombra virtual) —'
  'fila `pendiente` + COMMIT, escritura externa, sello por ack, cierre por '
  'observación del día siguiente. Sombra fiel (S4 #13, decisión del lead): '
  'el virtual (`aplicado = false`) nace CERRADO (`confirmado`/`virtual`, '
  '`enviado_at` puesto, sin ack ni readback) y consume cooldown y freno igual '
  'que uno real sin ocupar el índice de abierto. Reversa (S6): `es_reversa` '
  'con `precio_despues = precio_antes` del revertido, por el mismo camino y '
  'con el mismo cierre; sin decisión propia; nunca automática (un '
  '`no_confirmado` frena y avisa).';
COMMENT ON COLUMN precio_cambio.precio_antes IS
  'Precio vivo leído por GET del item inmediatamente antes de escribir (S6).';
COMMENT ON COLUMN precio_cambio.readback_estado IS
  'Lectura informativa tras el ack: `ok` o `fallido` —nunca `no_confirmado` '
  '(S6): el readback no decide el estado.';

-- Nacimiento (decisión del lead sobre los huecos de S5, más r3-4): con
-- decisión, `aplicado = (decision.mode = 'live')` en las dos direcciones
-- (S4 #13: en sombra el cambio es virtual y sin PATCH; un virtual bajo `live`
-- también es incoherente); en reversa (sin decisión), `aplicado = true`.
-- Con `aplicado = true` SOLO se nace `pendiente`; con `aplicado = false`
-- (virtual) se nace cerrado —`confirmado`/`virtual` con `enviado_at` y sin
-- ack, readback ni error— para consumir cooldown y freno sin ocupar el
-- índice de abierto.
CREATE FUNCTION precio_cambio_nacimiento() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_mode precio_mode;
BEGIN
====
migrations/0001_initial.sql:819:CREATE TABLE apply_quota_state (
migrations/0001_initial.sql:828:COMMENT ON TABLE apply_quota_state IS
migrations/0001_initial.sql:1487:-- se sella; apply_quota_state solo consume (used).
migrations/0001_initial.sql:1495:    optimizer_cycle, harvest_job, apply_quota_state
migrations/0001_initial.sql:1500:GRANT UPDATE (used) ON apply_quota_state TO app_decide;
migrations/0001_initial.sql:1512:GRANT INSERT ON apply_quota_state TO app_admin;
migrations/0001_initial.sql:1684:  'ads_optimizer_lock, harvest_job, apply_quota_state, catálogo. sku_cost '
migrations/0002_apply.sql:17:--  - sellos sobre apply_quota_state (que existe desde 0001): la fila del día
migrations/0002_apply.sql:443:--  20. SELLOS DE QUOTA  —  apply_quota_state: la fila del día nace SOLO de
migrations/0002_apply.sql:508:            'apply_quota_state: motor % fuera del vocabulario '
====
migrations/0001_initial.sql:819:CREATE TABLE apply_quota_state (
migrations/0001_initial.sql-820-    motor      TEXT     NOT NULL,
migrations/0001_initial.sql-821-    quota_date DATE     NOT NULL,
migrations/0001_initial.sql-822-    used       INTEGER  NOT NULL DEFAULT 0 CHECK (used >= 0),
migrations/0001_initial.sql-823-    cap        INTEGER  NOT NULL CHECK (cap > 0),
migrations/0001_initial.sql-824-    CONSTRAINT quota_no_excedida CHECK (used <= cap),
migrations/0001_initial.sql-825-    PRIMARY KEY (motor, quota_date)
migrations/0001_initial.sql-826-);
migrations/0001_initial.sql-827-
migrations/0001_initial.sql-828-COMMENT ON TABLE apply_quota_state IS
migrations/0001_initial.sql-829-  'Live automático CON TOPES: el cap diario de applies por motor. El consumo '
migrations/0001_initial.sql-830-  'es INSERT ON CONFLICT ... DO UPDATE ... WHERE used < cap (atómico, en la '
migrations/0001_initial.sql-831-  'app) — el CHECK quota_no_excedida es un backstop: impone que el ORDEN '
migrations/0001_initial.sql-832-  'importe en la app (consumir SIEMPRE con WHERE used < cap, nunca UPDATE '
migrations/0001_initial.sql-833-  'ciego). El motor solo puede UPDATE (used): cap y quota_date se fijan en el '
migrations/0001_initial.sql-834-  'INSERT — EL TOPE DE SEGURIDAD NO PUEDE ANULARSE A SÍ MISMO (si app_decide '
migrations/0001_initial.sql-835-  'pudiera subir cap, el candado sería decorativo). "used nunca decrece" NO es '
migrations/0001_initial.sql-836-  'enforceable por CHECK (compararía contra el valor viejo del UPDATE): queda '
migrations/0001_initial.sql-837-  'cubierto por el patrón de consumo atómico + la auditoría de decision '
migrations/0001_initial.sql-838-  '(cada apply tiene su fila append-only). Caps bajos el día 1 del cutover; '
migrations/0001_initial.sql-839-  'subir el tope es un INSERT de admin, no una decisión del motor.';
99:def _conexion_lectura():
100-    """Dependency: conexion como rol de lectura (ORBIT_DSN_READ).
101-
102-    Fail-closed: sin DSN -> 503 con mensaje claro (la API no puede operar sin
103-    config); fallo de conexion -> 503 con el DSN ya redactado por
104-    `app.db.connect` (jamas el DSN crudo en la respuesta). La conexion corre
105-    en AUTCOMMIT: la API es de SOLO LECTURA y una transaccion implicita
106-    ociosa por request (que psycopg abriria con la primera consulta y solo
107-    cerraria el rollback de close()) no aporta nada y mantiene un snapshot
108-    vivo de mas: cada SELECT es una lectura autonoma.
109-    """
110-    dsn = os.environ.get("ORBIT_DSN_READ")
111-    if not dsn:
112-        raise HTTPException(
113-            status_code=503,
114-            detail="ORBIT_DSN_READ no esta definido: la API de lectura no puede conectar",
115-        )
116-    try:
117-        conn = connect(dsn, autocommit=True)
118-    except OrbitDbError as exc:
119-        logger.warning("no se pudo conectar la API de lectura: %s", exc)
--
127:ConexionLectura = Annotated[psycopg.Connection, Depends(_conexion_lectura)]
128-
129-
130-def _filtros_decision(cycle_id, ad_entity_id, kind) -> tuple[str, list]:
131-    """Fragmentos SQL FIJOS + parametros de los filtros de /audit (ningun
132-    texto del usuario se interpola: solo clausulas literales de este codigo)."""
133-    clausulas: list[str] = []
134-    params: list = []
135-    if cycle_id is not None:
136-        clausulas.append("cycle_id = %s")
137-        params.append(cycle_id)
138-    if ad_entity_id is not None:
139-        clausulas.append("ad_entity_id = %s")
140-        params.append(ad_entity_id)
141-    if kind is not None:
142-        clausulas.append("kind = %s::decision_kind")
143-        params.append(kind)
144-    return (" AND ".join(clausulas) or "true", params)
====
183-"""
184-
185-
186:def cerrar_huerfanas(conn: psycopg.Connection, platform: str) -> int:
187-    """Cierra huerfanas; devuelve cuantas (van al resumen y al log)."""
188-    filas = conn.execute(_SQL_HUERFANAS, (platform,)).fetchall()
189-    if filas:
190-        logger.info("corrida %s huerfanas=%s", platform, len(filas))
191-    return len(filas)
192-
193-
194-def freno_por_error(
195-    conn: psycopg.Connection,
196-    listing_id: int,
197-    platform: str,
198-    hoy: date,
199-    *,
200-    dias: int,
201-    desde: date,
--
1439-        # D2 (r3): el lock se toma DENTRO del `try` (si el advisory falla,
1440-        # el `finally` suelta: el claim no queda tomado una hora).
1441-        _tomar_lock(conn, platform, owner)
1442:        huerfanas = cerrar_huerfanas(conn, platform)
1443-        cerrados = cerrar_por_observacion(conn, hoy)
1444-        goals = conn.execute(
1445-            "SELECT g.listing_id, g.platform, g.mode::text, g.margen_goal_pct,"
1446-            " g.valid_from, l.product_id"
1447-            " FROM precio_goal g JOIN listing l"
1448-            " ON l.id = g.listing_id AND l.platform = g.platform"
1449-            " WHERE g.platform = %s AND g.valid_from <= %s"
1450-            " AND (g.valid_to IS NULL OR g.valid_to > %s)"
1451-            " ORDER BY g.listing_id",
1452-            (platform, hoy, hoy),
1453-        ).fetchall()
1454-        decididas = {
1455-            f[0]
1456-            for f in conn.execute(
1457-                "SELECT listing_id FROM precio_decision WHERE platform = %s AND decision_date = %s",
Schema confirmed: columns match. `_SQL_HUERFANAS` in corrida — check its WHERE to confirm the mirror claim. Also `apply_quota_state` exists from 0001 — good.

  Now check `cerrar_huerfanas`'s `_SQL_HUERFANAS`:177-# no afirma que el precio no se movio (`revertir` cubre ese caso). Solo
178-# no-reversas (la `pendiente` reversa es del dueno) y SIN GET previo.
179:_SQL_HUERFANAS = """
180-UPDATE precio_cambio SET estado = 'error', error_code = 'huerfana_sin_patch'
181- WHERE platform = %s AND estado = 'pendiente' AND NOT es_reversa
182-RETURNING id
183-"""
184-
185-
186-def cerrar_huerfanas(conn: psycopg.Connection, platform: str) -> int:
187-    """Cierra huerfanas; devuelve cuantas (van al resumen y al log)."""
188:    filas = conn.execute(_SQL_HUERFANAS, (platform,)).fetchall()
189-    if filas:
190-        logger.info("corrida %s huerfanas=%s", platform, len(filas))
191-    return len(filas)
192-
193-
194-def freno_por_error(
195-    conn: psycopg.Connection,
196-    listing_id: int,
197-    platform: str,
198-    hoy: date,
199-    *,
200-    dias: int,
====
1460:def avisar_precio(conn, platform: str, hoy, resumen) -> int:
1461-    """Gancho `avisar` de la corrida A.5 (`correr(..., avisar=...)` lo llama
1462-    con `(conn, platform, hoy, resumen)` una vez al final, fail-silent).
1463-    Lee hoy y dias previos y manda en flanco por racha: devuelve cuantos
1464-    avisos salieron; jamas levanta (fallo -> warning con scrub + 0).
1465-
1466-    B2: los avisos por grupo y por producto solo salen si la corrida
1467-    persistio >= 1 decision (`resumen.decisiones > 0`): una reejecucion el
1468-    mismo dia no reenvia. Residuo declarado: si una corrida cae despues de
1469-    persistir todo y antes de avisar, ese dia no avisa (queda el log).
1470-    Residuo declarado (r2): una segunda corrida del mismo dia que persiste
1471-    >= 1 decision nueva (un goal agregado despues de la corrida de la
1472-    manana) reenvia todos los avisos del dia; no se corrige en A.6.
1473-
1474-    B4: un umbral `precio_aviso_dias_sin_evaluar` invalido o ausente solo
1475-    apaga el aviso `no_evaluado` (warning); los demas tipos salen igual.
1476-    """
1477-    try:
1478-        fila = conn.execute(_SQL_PRECIO_CONFIG).fetchone()
1479-        if fila is None:
1480-            return 0
1481-        try:
1482-            umbral = validar_precio_aviso_dias(fila[0])
1483-        except ValueError as exc:
1484-            logger.warning("telegram: umbral de aviso de precio invalido: %s", scrub(str(exc)))
1485-            umbral = None
1486-        ayer = hoy - dt.timedelta(days=1)
1487-        enviados = 0
1488-        if resumen.decisiones > 0:
1489-            enviados += _avisar_grupos_precio(conn, platform, hoy, ayer, umbral)
1490-            enviados += _avisar_buybox_precio(conn, platform, hoy, ayer)
====
39:def motor_cuota(platform: str) -> str:
40-    """Clave del motor en `apply_quota_state`."""
41-    return f"precio:{platform}"
42-
43-
44-def _numero(settings: Mapping, clave: str) -> Decimal:
45-    """Replica de `app/precio/config.py::_numero` (duplicacion deliberada:
61:def validar_cap(settings: Mapping, platform: str) -> int:
62-    """`precio_cap_<platform>` entero 0-20; falta o fuera de cota = `ValueError`."""
63-    clave = f"precio_cap_{platform}"
64-    numero = _numero(settings, clave)
65-    if numero != numero.to_integral_value():
66-        raise ValueError(f"setting {clave}: debe ser entero: {numero}")
67-    cap = int(numero)
68-    if not 0 <= cap <= 20:
69-        raise ValueError(f"setting {clave}: fuera de cota [0, 20]: {cap}")
70-    return cap
71-
72-
73-def reversas_hoy(conn: psycopg.Connection, platform: str, hoy) -> int:
All symbols check out. Now remaining verifications:

  1. `salud.html` — `datos.precios is defined` — the `salud()` endpoint now always sets "precios" key (None or dict). Template guard fine. But: `datos` in salud.html — check the loop variable name is `datos`. Quick check of salud.html around the insertion.

  2. `precios.html` uses `porcentaje_ui` and `estado_precio_es` filters — registered. `estado_precio_es` maps resultado to words. decision_hoy values: subir/bajar/mantener/no_evaluado/goal_inalcanzable/frenado — all in ESTADO_PRECIO_ES. OK.

  3. `_acciones_precio` — `PLATAFORMAS_MONEDA[plataforma]` — check PLATAFORMAS_MONEDA values: presumably {"amazon_mx": "MXN", ...}. If a platform not in dict — KeyError, but iteration is over PLATAFORMAS_MONEDA keys themselves, so fine.

  4. `_divergentes_precio`: `umbral` from validar_precio_aviso_dias — 1-14 guaranteed or ValueError. In `_bloque_precios`, `validar_precio_aviso_dias(settings)` raises ValueError if missing → in /salud caught by generic → None; in /precios → 503. Note the docstring of _precios_de says "o la config no trae la clave, el bloque queda en None". OK.

  But wait — there's a subtlety: `validar_precio_aviso_dias` raising ValueError means the ENTIRE bloque fails, even though umbral is only needed for divergentes (con_recuadro=True). For /salud (con_recuadro=False), umbral is computed but unused... actually it's used only in divergentes. So a missing `precio_aviso_dias_sin_evaluar` key makes /salud's precios block None entirely, even though salud doesn't need the umbral. That's a behavior choice; documented ("o la config no trae la clave, el bloque queda en None"). Not a bug per se.

  5. `_frase_accion` — for "mantener" with mode "shadow": no special handling; text "se mantuvo en X MXN (motivo)" — in shadow mode, "se mantuvo" is indicativo but it's shadow... minor wording, not blocking.

  6. Potential real issue in `_frase_accion`: for live subir/bajar with cambio real estado="confirmado" — frase says "subió de antes a despues". Good. With cambio estado "enviado"/"no_confirmado" — frase says "subió..." but no suffix appended (only error/pendiente get suffixes). "enviado" = PATCH accepted, fine. "no_confirmado" = observation didn't confirm the price moved — saying "subió de X a Y" would be false-ish. Hmm: `no_confirmado` means readback/observation did not confirm. The frase "subió de 116 a 127 MXN" without qualification could mislead. The `_ESTADOS_PRECIO_MOVIERON` includes "no_confirmado" as "prueban PATCH aceptado". The frase treats subió as indicative for any non-None cambio_estado except appending suffix for error/pendiente. For no_confirmado, no suffix. Debatable wording — NO BLOQUEANTE at most.

  7. Check `_recuadro_precio`: `rec.no_evaluadas` — list of (motivo, count) tuples; `rec.sin_goal` — list of detalle with sku, precio, moneda, canal; `rec.fuera_de_alcance` — (fase, count). Need to verify Recuadro fields in cobertura.py.

  8. Check `aviso_puente(activas=..., puente=...)` signature: `def aviso_puente(*, activas: int, puente: int) -> str | None`. ui.py calls `cobertura_precio.aviso_puente(activas=recuadro["activas"], puente=puente)`. OK.

  9. Template precios.html: `{% set n_noev = rec.no_evaluadas | sum(attribute="count") %}` — no_evaluadas is a list of dicts with "count". OK.

  10. In precios.html cobertura equation: activas = evaluadas + n_noev + sin_goal + fuera_de_alcance. Is that the actual recuadro equation? cobertura.cuadra_exact — the comment says "la ecuacion cuadra: cobertura.cuadra_exact". Presumably fine; verify Recuadro structure quickly.

  11. `salud.html`: `pr.no_evaluados.values() | sum` and `pr.frenados.values() | sum` — dicts motivo->count. OK. `pr.cuota.used`, `pr.cuota.cap`. OK.

  12. `pagina_precios` calls `dash.precios(conn=conn)` — signature `def precios(conn: ConexionLectura)`. Calling with keyword `conn=conn` works since parameter named conn. OK.

  13. HTTPException import in ui.py — check that ui.py imports HTTPException. Grep.

  14. `test_ui_reputacion.py` change: asserts "chip-proximo" not in html and "nav-proximo" not in html — base.html removed the only nav-proximo usage; but is there a `chip-proximo` CSS class definition in base.html style block? If the CSS still defines `.chip-proximo` class in a <style> tag, the string "chip-proximo" would appear in html → test fails. Check base.html for chip-proximo in style.

  15. Also check base.html macro `nav` — 'precios' added to the dict but is that dict used for anything (page titles?). The nav dict maps pantalla -> (titulo, descripcion); the header uses it. Added. OK.

  16. Check whether `/precios` route conflicts: FastAPI router with prefix "" — `/precios` GET HTML in ui.py and `/precios` GET JSON in api_dashboard (prefix /api/dashboard presumably). Fine.

  17. `_SQL_CUOTA_USED` selects `used` from apply_quota_state where motor = 'precio:<p>' — matches cuota.motor_cuota. OK.

  18. Missing: `_ESTADOS_PRECIO_MOVIERON` includes "no_confirmado" — comment says PATCH accepted. Schema: estado no_confirmado requires confirmado_por. Fine.

  19. Check `install_scrub_filter(logger)` — api_dashboard already imports install_scrub_filter and has its own logger with scrub filter presumably (existing). Fine.

  20. Check test_architecture.py ALLOWLIST_TAMANO — added entry justifies notifica.py size. There's presumably a size limit test; notifica.py now 1496 lines. The allowlist entry says "estaba en 899 antes de A.6" — whatever, it's an allowlist entry with justification. Fine.

  21. One more: `_precios_de` calls `fuentes_precio.hoy_base(conn)` inside the try — if the DB lacks... fine.

  22. `salud()` — the added line `"precios": _precios_de(conn, plataforma)` — verify that existing consumers/tests of /salud don't assert exact key sets. Check tests/test_api_dashboard.py for salud assertions.

  23. ui.py imports `estado_precio_es, motivo_precio_es` from app.notifica — ui.py now imports notifica which imports httpx and app.db — fine.

  But wait: does importing `app.notifica` at module import time create a cycle? notifica imports app.db, app.ads.config, app.redaction — no cycle with ui.

  24. Check `precios.html` `datos.puente is none` — Jinja `none` test. OK.

  25. `pagina_precios` — the try only wraps `dash.precios`; if template rendering fails, 500. Fine.

  Let me verify items 1, 7, 10, 13, 14, 22.

73:class Recuadro:
74-    """Recuadro S10 por plataforma. `no_evaluadas` y `fuera_de_alcance`
75-    son tuplas de pares `(motivo_o_fase, conteo)`."""
76-
77-    platform: str
78-    activas: int
79-    evaluadas: int
80-    no_evaluadas: tuple[tuple[str, int], ...]
81-    sin_goal: tuple[DetalleSinGoal, ...]
82-    fuera_de_alcance: tuple[tuple[str, int], ...]
83-    avisos: tuple[str, ...]
84-
85-
86-def _fase_fuera_de_alcance(fila: FilaPublicacion) -> str | None:
87-    if fila.platform == "meli":
88-        return FASE_MELI
89-    if fila.canal == "fbm":
90-        return FASE_FBM
91-    return None
92-
93-
94-def _motivo_no_evaluada(fila: FilaPublicacion, max_dias: int) -> str | None:
95-    if fila.dias_sin_reportar > max_dias:
96-        return "catalogo_desactualizado"
97-    if fila.listing_id is None:
98-        return "sin_listing"
99-    if fila.canal is None:
100-        return "canal_sin_dato"
101-    if not fila.tiene_goal:
102-        return None
103-    if fila.resultado_hoy is None:
====
33:    "cuadra_exact",
34-    "aviso_puente",
35-]
36-
37-RESULTADOS_EVALUADOS = ("subir", "bajar", "mantener", "frenado", "goal_inalcanzable")
38-FASE_FBM = "fase_E_envio_fbm"
39-FASE_MELI = "fase_M_meli"
40-
41-
42-@dataclass(frozen=True)
43-class FilaPublicacion:
44-    """Una publicacion activa de la plataforma, ya cruzada y resuelta.
45-
--
159:def cuadra_exact(rec: Recuadro) -> bool:
160-    """La ecuacion S10 cierra al entero, sin tolerancia."""
161-    return rec.activas == (
162-        rec.evaluadas
163-        + sum(n for _, n in rec.no_evaluadas)
164-        + len(rec.sin_goal)
165-        + sum(n for _, n in rec.fuera_de_alcance)
166-    )
167-
168-
169-def aviso_puente(*, activas: int, puente: int) -> str | None:
170-    """Contraste con la cuenta del bridge: diferencia mayor al 5 % se avisa."""
171-    if activas == 0:
====
====
38:from fastapi import APIRouter, HTTPException, Query, Request
39:from fastapi.responses import HTMLResponse
40:from fastapi.templating import Jinja2Templates
210:        raise HTTPException(status_code=422, detail=f"{nombre} fuera de vocabulario")
468:    except HTTPException as exc:
====
78:def dinero_ui(valor: str | None) -> str | None:
79-    """Presentacion a 2 decimales. None queda None; si no parsea, se deja igual."""
80-    if valor is None:
81-        return None
82-    try:
83-        return f"{Decimal(valor):.2f}"
84-    except (InvalidOperation, ValueError):
85-        return valor
86-
87-
88-def porcentaje_ui(valor: str | None) -> str | None:
89-    """Tanto por uno a porcentaje («0.2150» -> «21.50 %»). None queda None;
90-    si no parsea, se deja igual."""
91-    if valor is None:
92-        return None
====
====
test_precio_cobertura.py
test_precio_corrida.py
test_precio_goals.py
test_precio_migracion.py
test_precio_pantalla.py
test_precio_reglas.py
test_precio_write.py
• Notes:
  - `chip-proximo`/`nav-proximo` no longer in base.html and no CSS file match (grep found nothing in static css either — but wait, the grep output for base.html/static was empty, meaning the strings are gone entirely; the test asserting absence will pass). But the test comment says "hoy el unico «pronto» vive en un comentario Jinja" — there's still a comment in base.html mentioning "pronto" (line 668-669: `{# REPUTACION 01 A.6 ... #}` mentions "pronto" in a Jinja comment, which doesn't render). Test asserts "chip-proximo" not in html and "nav-proximo" not in html — Jinja comments don't render. OK.

  - There's a dedicated test file test_precio_pantalla.py (exists, not in the diff scope). Good — tests exist.

  - HTTPException imported in ui.py. Good.

  - dinero_ui: Decimal(valor) where valor is str. In template `{{ f.ultimo_cambio.antes | dinero_ui }}` — ultimo_cambio.antes is `_dec_str(antes)`; precio_antes is NOT NULL in schema, so non-None. OK.

  - `porcentaje_ui`: m_actual NUMERIC(8,4) as fraction → *100. Good.

  Now remaining checks:

  a) `_acciones_precio` in shadow mode with cambio: shadow cambios are virtual (`aplicado=false`), `_SQL_CAMBIOS_DE_DECISIONES` does NOT filter on aplicado — it includes virtual shadow changes. `cambio["real"] = bool(aplicado)` false for shadow. In `_acciones_precio`, for shadow decision with virtual cambio, antes/despues come from the cambio row — fine. In `_frase_accion` for shadow, the suffix logic: cambio_estado for virtual is "confirmado" — no suffix. Fine.

  b) `movidos` in `_bloque_precios`: `cambio.get("real") is True and estado in _ESTADOS_PRECIO_MOVIERON`. Virtual shadow cambios excluded (real=False). Good.

  c) `_SQL_DETALLE_DECISIONES_HOY` ORDER BY l.seller_sku — fine.

  d) `salud.html`: check surrounding context — where is this block inserted, is `datos` the per-platform variable? Check salud.html structure.

  e) One potential issue in `/salud`: the salud endpoint is supposed "liviano" but now for each platform runs: probe + config_vigente_settings + validar + detalle decisiones + cambios por decision + decisiones count + huerfanas + recuadro (leer_publicaciones: 4 queries) + cuota — that's ~10 queries per platform on every /salud load. Not a bug; perf note. The module docstring says "liviano, sin recuadro" but `cobertura` IS the recuadro (same serialization) — the docstring at top of the diff (lines 18-21) says "`plataformas.<p>.precios` en /salud (liviano, sin recuadro...)" while the block includes `cobertura` = full recuadro serialization. Documentation inconsistency — NO BLOQUEANTE.

  Wait, actually re-reading: line 20: "`plataformas.<p>.precios` en /salud (liviano, sin recuadro, sin cambiar las claves existentes)". But `_CLAVES_PRECIOS_SALUD` includes "cobertura" which is built by `_recuadro_precio` — the full recuadro. So "sin recuadro" is wrong: /salud does compute and include the recuadro (under key `cobertura`). The later comment (line 75-76) says "cobertura es la MISMA serializacion que el recuadro de /precios (misma funcion)". So the module docstring line contradicts. Minor doc inconsistency, NO BLOQUEANTE.

  f) Check salud.html context — `datos` loop variable. Read salud.html top.

  g) `_hay_esquema_precio` probes `precio_decision` but `_cuota_precio` queries `apply_quota_state` (exists since 0001) and `_SQL_GOAL_VIGENTE` queries `precio_goal`, `_SQL_HUERFANAS_PRECIO` queries `precio_cambio` — all from 0039. If 0039 applied partially (it's one migration file, atomic presumably), fine.

  h) `precios.html` renders `{{ f.modo }}` raw (live/shadow) — fine.

  i) In `precios.html`, huerfanas alert text and "sin cambios pendientes" — fine.

  j) In `_con_goal_precio`: `"moneda": (det["p_actual_currency"] if det is not None else None) or PLATAFORMAS_MONEDA[plataforma]` — p_actual_currency is NOT NULL in schema. Fine. But rule 3: "Dato faltante = None, nunca constante inventada" — falling back to PLATAFORMAS_MONEDA default when det is None: moneda is platform-level fact, not invented. Acceptable.

  k) `_divergentes_precio` — `p_moneda` from `d.p_actual_currency` NOT NULL. OK. Edge: if racha >= umbral but umbral counts days including today; comment says "ultimos umbral dias seguidos hasta hoy". OK.

  One subtle bug candidate in `_divergentes_precio`: a listing with decision rows where a day has `no_evaluado` with motivo `precio_divergente` — counted. Days with no decision row break the streak. OK.

  l) `_SQL_RACHA_RECIENTE` BETWEEN desde AND hoy — covers 30 days. `dias` dict keyed by date — decision_date is date. OK.

  m) psycopg `ANY(%s)` with empty list — `_cambios_por_decision` guards `if not ids`. `_con_goal_precio` guards `if filas`. OK.

  n) Type of ids list for ANY: ids are ints; decision_id BIGINT — fine.

  o) In `salud()`, adding "precios" key — check tests that assert exact keys of plataformas.<p>. Search tests for salud platform keys.

  p) `pagina_precios` — passes "resultados_evaluados" to template but template doesn't use it. Harmless.

  q) ui.py: `dash.precios(conn=conn)` — but `dash.precios` signature is `def precios(conn: ConexionLectura)` — ConexionLectura is Annotated[psycopg.Connection, Depends(...)]; calling directly with conn keyword works.

  r) Check `test_precio_pantalla.py` exists and covers this — it's not in the diff but exists in repo (maybe from same commit, diff was limited). Fine.

  s) Security: /precios exposes margins/goals — same as other internal dashboard pages; check whether UI requires any auth token. Other pages (reputacion, contribucion) show similar data; consistent. Not new risk.

  t) `_frase_accion` mantener: `{sku} se mantuvo en {antes} {moneda}` — for mantener, `antes` = p_actual. If p_actual None (mantener can have NULL p_actual per CHECK — cuenta_completa only for subir/bajar) → "se mantuvo en None MXN". Possible cosmetic issue. NO BLOQUEANTE.

  u) Bigger one: in `_acciones_precio`, for `frenado`/`goal_inalcanzable`/`mantener` — if a cambio exists for that decision (shouldn't), antes/despues from cambio. Fine.

  v) Check that `salud.html`'s `datos` variable: in the loop `{% for plataforma, datos in plataformas.items() %}` presumably. The guard `{% if datos.precios is defined %}` — since "precios" key is always added, `is defined` is always true. Fine.

  w) One more check: `app/ui.py` imports `from app.notifica import estado_precio_es, motivo_precio_es` — but does ui.py use motivo_precio_es only as filter? Registered. OK.

  x) Does `app/api_dashboard.py` already have `install_scrub_filter(logger)`? It imports it (line in imports existed before). Check logger has scrub filter — existing pattern. Fine.

  y) Now potential real bug: `_precios_de` catches `_SinEsquemaPrecio` and `psycopg.errors.UndefinedTable`, then generic Exception. But `_SinEsquemaPrecio` subclasses Exception — order fine.

  z) In `precios` endpoint: `except ValueError` → 503 with `detail=str(exc)` — str(exc) of a config ValueError includes the offending value, e.g. "setting precio_cap_amazon_mx: fuera de cota [0, 20]: 25". No secrets. OK. But it's not scrubbed — config values aren't secrets. Fine.

  aa) `test_ui_reputacion.py` — asserts 'href="/precios"' in html. base.html has it. OK.

  bb) Check the sidebar macro in base.html: 'precios' added to the nav dict — check how the dict is used (title of page?). Line 28-30 shows a dict of pantalla -> (nombre, desc). Adding 'precios' entry. And the nav link added. Consistent.

  cc) `salud.html` — check the exact placement: the diff inserts before "<h3>Historico (14d)</h3>". Need `plataforma` and `datos` variables in scope — the surrounding template uses `datos.precios` and `plataforma`; presumably inside the per-platform loop. Let me verify quickly.

  dd) One more possible issue: `_SQL_CAMBIOS_DE_DECISIONES` returns latest cambio per decision ("el primero de id DESC gana"). Since `precio_cambio_abierto_unico` allows only one open cambio per listing, but multiple closed cambios per decision? A decision is UNIQUE per (listing, platform, day); cambios per decision could be multiple (error then retry? retries create new cambio rows?). Taking the latest is sensible.

  ee) Check `_ESTADOS_PRECIO_MOVIERON` includes 'no_confirmado' — that's the observation not confirming; the comment justifies PATCH accepted. Debatable but documented.

  ff) `import psycopg.errors` — psycopg 3 exposes psycopg.errors. OK.

  gg) Verify /salud template variable scope and that no test asserts exact keys. Let me grep tests for salud keys assertions.

5:{% for plataforma, datos in plataformas.items() %}
8:    {% if datos.ultimo_ciclo %}
9:      <span class="chip {% if datos.ultimo_ciclo.status == 'done' %}ok{% elif datos.ultimo_ciclo.status in ('degraded', 'failed') %}alerta{% endif %}">{{ datos.ultimo_ciclo.status }}</span>
10:      <span class="chip">{{ datos.ultimo_ciclo.mode }}</span>
13:  <p class="mutado">Watermark (v_metric_latest): {{ datos.watermark or "—" }} ·
14:     Sync estructura: {{ datos.synced_at or "—" }}</p>
20:  <script type="application/json" id="datos-skips-{{ plataforma }}">{{ datos.skips | tojson }}</script>
22:  {% if datos.ultimo_ciclo %}
26:  <p>#{{ datos.ultimo_ciclo.id }} · {{ datos.ultimo_ciclo.started_at }} ·
27:     decisiones: {{ datos.ultimo_ciclo.decisions_count }} ·
28:     applies: {% if datos.ultimo_ciclo.applied_count is none %}<span class="mutado">—</span>{% else %}{{ datos.ultimo_ciclo.applied_count }}{% endif %}</p>
29:  {% if datos.ultimo_ciclo.notes and datos.ultimo_ciclo.notes.telegram %}
33:  <p class="alerta">telegram: fallo del canal ({{ datos.ultimo_ciclo.notes.telegram.keys() | join(", ") }} sin entregar — ver notes)</p>
37:  {% if datos.harvest_destino %}
40:  <p>Destino de harvest: por grupo {{ datos.harvest_destino.resueltos.grupo }} · por excepcion {{ datos.harvest_destino.resueltos.excepcion }} · por terna {{ datos.harvest_destino.resueltos.terna }} ({{ datos.harvest_destino.terna_es }})</p>
41:  {% if datos.harvest_destino.saltos_grupo %}
43:    {% for salto in datos.harvest_destino.saltos_grupo %}
50:  {% if datos.quota %}
60:      {% for forma, q in datos.quota.items() %}
73:  {% if datos.spapi %}
====
tests/test_spapi_salud.py
tests/test_preflight_1_4.py
tests/test_reputacion_alertas.py
tests/test_target_margen.py
tests/test_ui.py
tests/test_api_dashboard.py
tests/test_precio_pantalla.py
====
538:# plataformas.<p>.precios en /salud. El recuadro se arma EN VIVO con
540:# /salud suma la clave sin cambiar las existentes. Base real
796:# 6. /salud suma precios SIN cambiar las claves existentes
801:def test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves():
804:        data = dash.salud(conn)
813:# 7. /salud.precios EXACTO vs SELECT (decisiones del dia + huerfanas)
818:def test_precio_pantalla_salud_precios_exactos_vs_select():
830:        precios = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
837:# 8. sin 0039: /salud None+warning y /precios error claro, nunca 500
847:            data = dash.salud(conn)
• There's a test file test_precio_pantalla.py with tests including "/salud suma precios SIN cambiar las claves existentes". Good.

  Now let me run the focused tests quickly? The review task says verify; running the test file requires DB (they use _skip_db presumably; many will skip without ORBIT_TEST_DSN). Running won't hurt but may just skip. Not strictly needed for review. Maybe a quick syntax/import check: `python -c "import app.api_dashboard, app.ui"` to confirm imports resolve. Let me do that plus ruff check on changed files.

  Also check `salud.html` — `datos.precios is defined` inside loop where datos always has "precios" key now. Fine.

  One more check on template: `{{ f.ultimo_cambio.antes | dinero_ui }}` — if ultimo_cambio dict exists, antes is _dec_str(precio_antes) — precio_antes NOT NULL. OK.

  And `_frase_accion` mantener with p_actual None → "None". Check: can mantener have p_actual NULL? CHECK cuenta_completa only applies to subir/bajar. So mantener could have NULL p_actual → frase "se mantuvo en None MXN". _dinero_2 returns None → f-string "None". Cosmetic. NO BLOQUEANTE.

  Also `goal_inalcanzable` mode — fine.

  Also check `_recuadro_precio` — Recuadro has `platform` field not serialized; template doesn't use it. OK.

  One thing: `_bloque_precios` for /salud computes `cobertura` (full recuadro with sin_goal details list) — the /salud JSON now embeds the full sin_goal SKU list per platform. "Liviano" claim is false-ish but not a bug.

  Potential perf concern: /salud per platform runs leer_publicaciones (4 queries) + others — ~10 extra queries; /salud previously light. Not blocking.

  Let me quickly run a smoke: compile all changed py files and run pytest on test_precio_pantalla.py (will skip without DSN maybe). Actually running the suite partially is verification; the instructions say bloqueante needs repro command. If tests skip without DB, I can't repro much. Let me at least byte-compile and import.

imports ok
All checks passed!
  /Users/dn/dev/_wt/f11-kimi-r1/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
95 passed, 1 warning in 9.42s
• Tests pass (95 passed — includes DB tests? They passed, so ORBIT_TEST_DSN is set or tests are non-DB). Good.

  Now compile findings. Everything verified. Remaining candidate findings:

  1. NO BLOQUEANTE: `_frase_accion` — un cambio en estado `no_confirmado` (la observacion del dia siguiente NO confirmo el movimiento) se narra como «subio/bajo de X a Y» en indicativo sin matiz (solo `error` y `pendiente` llevan sufijo). `no_confirmado` justamente significa que el precio no se confirmo movido — la frase puede mentir. En `_ESTADOS_PRECIO_MOVIERON` el propio codigo lo cuenta como "movido" (PATCH aceptado), asi que hay coherencia interna discutible, pero la frase al usuario es mas fuerte que el dato. NO BLOQUEANTE (redaccion, no rompe comportamiento pedido — aunque... el comportamiento pedido incluye frases veraces; pero sin repro clara de requisito lo dejo no bloqueante).

  2. NO BLOQUEANTE: `_frase_accion` para `mantener` usa `p_actual`, que el esquema admite NULL (el CHECK de cuenta completa solo aplica a subir/bajar) → la frase puede salir «se mantuvo en None MXN». Cosmetico.

  3. NO BLOQUEANTE: docstring del modulo (lineas 18-21 del diff) dice que /salud es "liviano, sin recuadro", pero el bloque incluye `cobertura`, que ES la serializacion completa del recuadro (misma funcion `_recuadro_precio`, con la lista `sin_goal` de SKUs incluida). Documentacion contradictoria + /salud ahora corre ~10 queries extra por plataforma (leer_publicaciones son 4).

  4. NO BLOQUEANTE: en `pagina_precios` (ui.py) se pasa `resultados_evaluados` al contexto del template pero precios.html no lo usa. Ruido menor.

  5. NO BLOQUEANTE: `validar_precio_aviso_dias` (umbral solo usado por el bloque divergentes de /precios) se valida siempre en `_bloque_precios`, tambien para /salud donde no se usa: una config sin `precio_aviso_dias_sin_evaluar` apaga TODO el bloque precios de /salud (None) aunque ese dato no lo necesita. Documentado en el docstring ("o la config no trae la clave, el bloque queda en None"), asi que es decision declarada, pero acopla /salud a una clave que no usa.

  6. En `_frase_accion`, en modo shadow con cambio virtual (aplicado=false) y estado confirmado, no hay sufijo — correcto. Y el caso live + cambio estado "enviado": «subio» — PATCH aceptado, ok.

  7. `_dinero_2` — Decimal(str(valor)) fine.

  Anything else? `_acciones_precio` — for `subir`/`bajar` in shadow, cambio lookup finds virtual cambio (aplicado=false) and moneda = cambio despues_moneda — fine.

  `_con_goal_precio` — goals vigentes: `g.valid_from <= hoy AND (valid_to IS NULL OR valid_to > hoy)` — matches fuentes._SQL_GOALS. Consistent.

  Security: no user input interpolated into SQL; all parameterized. The 503 detail includes str(exc) of ValueError from config validation — no secrets (config values). Fine.

  One more possible: `precios` endpoint raises HTTPException from within loop — first platform failure aborts all. Design choice (fail-closed 503). Documented.

  Also, `salud.html` — `pr.no_evaluados.values() | sum`: if no_evaluados is {}, sum → 0. OK.

  Also check: `_SQL_CUOTA_USED` — apply_quota_state has GRANT SELECT for read role? The read role is `orbit_read`; check grants in 0001/0039 for apply_quota_state and precio_* tables to orbit_read. If the read role lacks SELECT on precio_* tables, /salud would... be caught and return None (degraded), and /precios 503. Tests passed though (95 passed) — test_precio_pantalla includes DB tests that passed, implying grants OK or tests ran as superuser. Grants: quickly check 0039 for GRANT to read role.

152:    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
183:  'NULL -> fecha una vez—; el DELETE jamás, sin depender de qué GRANT tenga '
792:    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
1118:-- (i) GRANTs por columna (S5 y hecho 2 del brief): el motor (`app_decide`)
1125:GRANT SELECT ON precio_goal, precio_decision, precio_cotizacion,
1129:GRANT INSERT ON precio_decision, precio_cotizacion, precio_envio_muestra
1131:GRANT INSERT ON precio_cambio TO app_decide;
1132:GRANT UPDATE (estado, enviado_at, ack, readback_precio, readback_precio_currency,
1136:GRANT INSERT ON precio_goal TO app_admin;
1137:GRANT UPDATE (valid_to) ON precio_goal TO app_admin;
====
517:    CONSTRAINT precio_cambio_readback_con_moneda
519:    CONSTRAINT precio_cambio_readback_estado_valido
1121:-- `app_read` solo lee (SELECT explícito en las cinco, como en 0028 —además
1127:    TO app_read, app_ingest, app_decide, app_admin;
1190:-- `enviado`; el admin inserta el goal— y el rechazo de `app_read`, todo bajo
1305:        -- Negativo de lectura: `app_read` no inserta en ninguna de las cinco.
1306:        SET ROLE app_read;
1311:            RAISE EXCEPTION '0039: INSERT de app_read en precio_envio_muestra NO fue rechazado';
• Grants exist for app_read (SELECT on all five). Everything verified. Tests pass (95 in the two files). Imports fine, ruff fine.

  Now write findings. Verdict: no blocking findings. List non-blocking ones:

  1. `_frase_accion`: `no_confirmado` narrated as "subió/bajó" without qualifier.
  2. `_frase_accion` mantener with NULL p_actual → "en None MXN".
  3. Module docstring contradiction: "liviano, sin recuadro" but cobertura = recuadro completo incl. sin_goal SKU list in /salud payload.
  4. `_bloque_precios` validates `precio_aviso_dias_sin_evaluar` always, even for /salud where umbral is unused → a missing key degrades the whole /salud precios block. Documented, so declared decision, but coupling worth noting.
  5. ui.py passes `resultados_evaluados` to template, unused.

  Keep it concise, plain text, Spanish, no accents where avoidable.

To resume this session: kimi -r session_694418de-f1ee-48b9-b974-b147e95ee907


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=510beda33015edaba4c0aaddf2471974851ffc15 archivos=app/api_dashboard.py,app/ui.py,app/cli.py,app/templates/precios.html,app/templates/salud.html,app/templates/base.html,tests/test_architecture.py,tests/test_ui_reputacion.py inicio=2026-09-19T02:45:56Z fin=2026-09-19T02:54:54Z exit=0
