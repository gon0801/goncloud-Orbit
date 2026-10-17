# C.6 — checklist de ejecucion (DRAFT BLOQUEADO, 2026-09-26)

NO ejecuta nada. No crea `H6/inicio.txt`, no flipea `mode`, no enciende nada.
Derivado del runbook H6 paso 4. El arranque real lo hace el lead con los dos
gos del dueno, en ventana, tras A.4 + B.4 cerrados.

## Puertas (todas tienen que estar en verde antes del go de deploy)

- [ ] A.4 cerrada con evidencia (observacion 7 dias + readbacks H4).
- [ ] B.4 cerrada con evidencia (5/5 ciclos sombra + live H5.4).
- [ ] `C.6 deploy GO` literal del dueno con efecto cero-applies + acuerdo D.3.
- [ ] `C.6 live GO` literal del dueno citando el riesgo C.2b (solo despues del deploy).
- [ ] Ventana runbook 0.2 + backup + rollback listo (regla 2/3).

## Recorrido (cuando las puertas esten en verde)

1. Crear `H6/inicio.txt` propio (INICIO_SHADOW C.6; nunca el de H5) + definir
   `IDS_C6` acotado con antes/despues (pendiente de definir en el go de deploy).
2. Flip a `shadow` acotado a `IDS_C6` + encendido con el literal de aislamiento
   off que C.3 dejo registrado.
3. 5 ciclos con paradas (a)(b)(c) de H5.3; log por ciclo (slots sin crear):
   `H6/ciclo-1.txt` … `H6/ciclo-5.txt`.
4. Flip de vuelta acotado a IDs (solo con `C.6 live GO`).
5. Cierre: comparacion candidatos vs applies sin lookahead; si H4 cerro parcial,
   registrar "senal A.3 sin recovery observado" como riesgo.

## Linea base verificada 2026-09-26 ~00:50 UTC (solo lectura)

- Prod `03faa24` (= codigo de `ee50332`); 9 goals en `shadow`; `applied` desde
  INICIO_SHADOW H5 = 0; `ads_campaign_proposal open` = 0.
- `config_version` 21: `ads_pause_sin_cooldown_bid=true`,
  `ads_pause_economica` ausente (fail-closed: el vivo economico sigue apagado).
