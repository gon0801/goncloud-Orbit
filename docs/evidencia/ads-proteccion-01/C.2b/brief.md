# C.2b — brief de decision para David (PENDIENTE, 2026-09-24)

Parametros prefijados (`propuesta.md`): horizonte 10 dias, tolerancia 0
falsos positivos, sin piso. Reporte: `docs/evidencia/ads/2026-09-24-
proteccion-economica-replay.md` (3291 filas, 79 senales, 11 entidades —
reproducido exacto el 24-sep-2026 con `tools/replay_ads_economico.py`
de master sobre prod, solo lectura).

## Conteo a horizonte 10d (primera senal por entidad vs fila con target a +10d)

| Entidad | 1a senal | +10d | Madura | Veredicto |
| --- | --- | --- | --- | --- |
| AU2 3926 camp US | 11-sep | 21-sep | 24-sep sigue cruzando | confirmada |
| KW 4925 US | 11-sep | 21-sep | 24-sep sigue cruzando (rev 0) | confirmada |
| KW 4926 US | 11-sep | 21-sep | 24-sep sigue cruzando | confirmada |
| A1U 3909 camp US | 12-sep | 22-sep | 24-sep sigue cruzando | confirmada |
| KW 5347 US (A1U) | 12-sep | 22-sep | 24-sep sigue cruzando | confirmada |
| Camp 3920 US | 11-sep | 21-sep | sin target a +10d | INDETERMINADA |
| KW 4919 US | 11-sep | 21-sep | sin target a +10d | INDETERMINADA |
| PT 3859 MX | 14-sep | 24-sep | sin target a +10d | INDETERMINADA |
| Camp 3919 US | 18-sep | 28-sep | fuera de datos (terminan 24-sep) | INDETERMINADA |
| PT 5896 US | 18-sep | 28-sep | fuera de datos | INDETERMINADA |
| KW 4786 US | 18-sep | 28-sep | fuera de datos | INDETERMINADA |

**Resultado: 0 FP medidos, 5 confirmadas, 6 indeterminadas.**

## Matices que condicionan la aceptacion

1. 3920/4919 DEJARON de cruzar al 13-sep (FP transitorio documentado en
   el reporte, seccion veto): a 48h el veto los cancela; a 10d son
   indeterminadas por falta de target, no por confirmacion.
2. 3919 tambien se cancela al liberar veto (reporte).
3. La fila madura usa ventana corrida y a veces target distinto: no es la
   misma ventana madurada, es la mejor aproximacion disponible.
4. A1U/AU2 en PAUSED el 24-sep: su senal de ese ciclo no es aplicable.
5. Cobertura parcial en MX; revenue=0 medido cruza por diseno (C.0-2).

## Lo que se pide a David (literal para cerrar C.2b)

Aceptar: "C.2b: acepto la regla economica con horizonte 10 dias,
tolerancia 0 y sin piso, sobre 0 FP medidos / 6 indeterminados; el
riesgo de corte con ventas persiste y lo asumo".

O cambiar: "C.2b: cambio la regla: <nuevo horizonte / tolerancia /
piso / condicion>" (obliga a re-medir antes de live).

Sin uno de esos literales no hay merge C.3/C.4 ni live de C.
