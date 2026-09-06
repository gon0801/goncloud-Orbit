from __future__ import annotations

from pathlib import Path

VERSION = "1.0.0"

_CASES_DIR = Path(__file__).with_name("cases")


def _files(case_id: str, *names: str) -> dict[str, str]:
    directory = _CASES_DIR / case_id
    return {name: (directory / name).read_text(encoding="utf-8") for name in names}


CASES: dict[str, dict] = {
    "codigo": {
        "title": "Corte maduro con dinero por moneda",
        "category": "codigo",
        "prompt": """Implementa `solution.py` como programa JSON por stdin/stdout.

Recibe una observacion de Sponsored Products con `platform`, `clicks`, `orders`,
`cost`, `currency`, `metric_date` y `decided_at`. Decide `pause` solo cuando no
hay ordenes, hay al menos 100 clics, el costo alcanza 40.0000 USD para amazon_us
o 500.0000 MXN para amazon_mx, y el dato tiene al menos 10 dias completos de
maduracion. D-10 entra; D-9 no. Dinero llega como string decimal y no se debe
convertir a float. Una moneda que no corresponde a la plataforma se rechaza.
Un costo ausente se trata como dato faltante, no como cero.

La salida es exactamente un objeto con `action` (`pause` o `none`) y `reason`:
`corte`, `evidencia_insuficiente`, `moneda_invalida` o `dato_faltante`.

Ejemplo minimo ejecutable:
```sh
echo '{
  "platform":"amazon_us", "clicks":100, "orders":0, "cost":"40.0000",
  "currency":"USD", "metric_date":"2026-08-20", "decided_at":"2026-08-30"
}' | python solution.py
```
debe imprimir `{"action": "pause", "reason": "corte"}`. Ejecuta tambien
`python test_public.py`. Entrega `NOTAS.md` con decisiones, fronteras probadas y
comandos ejecutados. No uses red, base de datos ni archivos externos.
""",
        "files": _files("codigo", "solution.py", "test_public.py"),
        "artifacts": ["solution.py", "NOTAS.md"],
        "dimensions": ["razonamiento", "correccion", "limpieza", "autonomia"],
        "automatic": True,
        "extension_of": None,
    },
    "depuracion": {
        "title": "Replay fiel de politicas historicas",
        "category": "depuracion",
        "prompt": """Corrige `solution.py`. El programa decide cortes actuales bien,
pero el replay de decisiones viejas aplica por error los umbrales actuales.

En `mode=current` usa los umbrales actuales incluidos en el archivo. En
`mode=replay` debe usar exclusivamente `frozen_policy` (`min_clicks`, `min_cost`,
`currency`). Si el marcador historico falta, debe abstenerse con
`{"action":"none","reason":"politica_historica_ausente"}`; nunca debe
inventar ni tomar la politica vigente. Conserva el contrato JSON y la validacion
de moneda. Dinero sigue siendo decimal, no float. Entrega `solution.py` y
`NOTAS.md` con causa raiz, demostracion del fallo anterior y evidencia verde
posterior. Deja `python test_public.py` en verde.
""",
        "files": _files("depuracion", "solution.py", "test_public.py"),
        "artifacts": ["solution.py", "NOTAS.md"],
        "dimensions": ["razonamiento", "correccion", "limpieza", "autonomia"],
        "automatic": True,
        "extension_of": None,
    },
    "arquitectura": {
        "title": "Repricing seguro con inventario",
        "category": "arquitectura",
        "prompt": """Propone en `arquitectura.md` una arquitectura pequena para repricing
con inventario usando el stack existente de Orbit y la situacion dada. Define
propietarios, limites de componentes, modelo de datos, flujo de propuesta,
aprobacion/aplicacion, reversa, reconciliacion, fallos y verificacion. Amazon y
Mercado Libre tienen capacidades distintas: conserva proposal-only donde no hay
escritura autorizada. Puedes incluir datos bitemporales si apoyan la decision,
pero el foco es la capacidad de repricing. No escribas codigo.
""",
        "files": _files("arquitectura", "situacion.md"),
        "artifacts": ["arquitectura.md"],
        "dimensions": ["razonamiento", "arquitectura", "mantenibilidad"],
        "automatic": False,
        "extension_of": None,
    },
    "diseno": {
        "title": "Panel local de decisiones auditables",
        "category": "diseno",
        "prompt": """Crea `index.html`, una UI local funcional y autocontenida con CSS y
JavaScript sin red, para la pantalla descrita en `brief.md` y el dataset de
ejemplo. Debe hacer visibles moneda, huecos, inmadurez y procedencia sin afirmar
precision inexistente. Implementa navegacion por teclado, foco visible, estados
vacio/error/carga y detalle sin perder contexto. Entrega tambien `diseno.md` con
jerarquia, decisiones, interacciones, accesibilidad y criterios de aceptacion.
""",
        "files": _files("diseno", "brief.md", "datos.json"),
        "artifacts": ["index.html", "diseno.md"],
        "dimensions": ["razonamiento", "diseno", "autonomia"],
        "automatic": False,
        "extension_of": None,
    },
    "revision": {
        "title": "Revision de resumen de campanas",
        "category": "revision",
        "prompt": """Revisa `cambio.diff` como si bloquearas o aprobaras un PR de Orbit.
Usa el contrato de datos provisto en `contrato.md`.
Entrega `revision.md` con hallazgos priorizados y referencias concretas a lineas,
impacto observable y una correccion sugerida. Separa bloqueantes de mejoras.
Evita falsas alarmas: reconoce tambien las decisiones correctas relevantes.
No implementes cambios.
""",
        "files": _files("revision", "cambio.diff", "contrato.md"),
        "artifacts": ["revision.md"],
        "dimensions": ["razonamiento", "revision", "correccion"],
        "automatic": False,
        "extension_of": None,
    },
    "criterio": {
        "title": "Incidente sin tipo de cambio",
        "category": "criterio",
        "prompt": """Lee `incidente.md` y responde en `decision.md` que harias ahora.
Debes decidir explicitamente si aceptas el cambio solicitado, justificarlo con
los invariantes afectados, describir el estado visible al operador y proponer
los siguientes pasos verificables. Cambiar codigo no es un entregable.
""",
        "files": _files("criterio", "incidente.md"),
        "artifacts": ["decision.md"],
        "dimensions": ["razonamiento", "correccion", "autonomia"],
        "automatic": False,
        "extension_of": None,
    },
    "mantenibilidad": {
        "title": "Opt-out de campana sin regresiones",
        "category": "mantenibilidad",
        "prompt": """Esta es una extension sorpresa de la entrega congelada de `codigo`.
Agrega el campo booleano `goal_enabled`: cuando sea `false`, la salida debe ser
`{"action":"none","reason":"goal_deshabilitado"}` antes de evaluar un corte.
Cuando sea `true` aplica las reglas existentes. Los payloads historicos que no
traen el campo deben conservar exactamente el comportamiento anterior. Mantiene
el mismo contrato de proceso y no reescribas la solucion desde cero sin necesidad.
Entrega `MANTENIMIENTO.md` con el cambio, estrategia de compatibilidad y replay
ejecutado sobre los casos anteriores.
""",
        "files": {},
        "artifacts": ["solution.py", "MANTENIMIENTO.md"],
        "dimensions": ["correccion", "limpieza", "mantenibilidad", "autonomia"],
        "automatic": True,
        "extension_of": "codigo",
    },
}
