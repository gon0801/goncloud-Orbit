# X.1: encendido del motor en MX y US (2026-10-11)

Medicion (despues del ciclo de las 08:40 UTC, antes de encender):
- `checklist-0903Z.txt`: exit=0. 0 fallas. Ciclos posteriores 117 MX
  (08:41) y 116 US (08:40) en done, 0 decisiones bid, notes con
  `politica_apagada`, y PAUSE/negative/harvest como antes (0=0).
  Cubre el ciclo posterior que D.1 dejo pendiente.
- `clave-0905Z.txt`: f|f (politica ausente en las dos plataformas).
- `fraccion-0908Z.txt`: 0.8.
- `rejuego-mx-0905Z.txt` y `rejuego-us-0906Z.txt`: exit=0.
- `rejuego-amazon_mx.txt` y `rejuego-amazon_us.txt`: cumple: true en
  ambas (MX: 31 ciclos live, 2028 casos, 0 recortes sobre danadas;
  US: 30 ciclos live, 1629 casos, 0 recortes sobre danadas).
- `aviso.md`: lo que haria el motor (MX 1 vendedora que recortaria,
  US 4).

Encendido (interlock OK en las dos, actor de los regresos abajo):
- `encender-amazon_mx-0951Z.txt`: exit=0, SIMULACRO OK.
- `encender-amazon_mx-real-0951Z.txt`: exit=0. Config 25,
  created:true, "motor bid ausente -> niveles_v3".
- `encender-amazon_us-0952Z.txt`: exit=0, SIMULACRO OK.
- `encender-amazon_us-real-0952Z.txt`: exit=0. Config 26,
  created:true, "motor bid ausente -> niveles_v3".
- Claves: niveles_v3|niveles_v3. Fraccion US: 0.8.

Regreso de danadas (actor `plan bids-02 X.1`, 9 regresos, 0 fallas):
- `regresar-sim-0952Z.txt`: exit=0 (N=6 MX, N=3 US).
- `regresar-real-0952Z.txt`: exit=127. El POST de MX dio HTTP 200 y
  guardo sus 6 regresos, pero el script murio en su linea 151 porque
  el arbol de despliegue no tiene `.venv` para verificar la
  respuesta. Nada se duplico: la corrida siguiente relee N.
- `regresar-real2-0953Z.txt`: exit=0 con
  `ORBIT_SIM_PYTHON=python3` (MX N=0 HTTP 200 idempotente, US N=3
  HTTP 200 ok=3).
- `respuesta-regresar-amazon_mx-20261011-095229.txt`: 6 ok, hojas
  2963, 2871, 2880, 2969, 3857 y 2917, cada una con su
  regreso_confirmado_at.
- `respuesta-regresar-amazon_mx-20261011-095310.txt`: N=0, resultados
  vacios (las 6 ya estaban regresadas).
- `respuesta-regresar-amazon_us-20261011-095310.txt`: 3 ok, hojas
  4919, 5894 y 4924, cada una con su regreso_confirmado_at.

Pendiente (no frena este cierre): el primer ciclo con la politica
encendida es el del dia siguiente a las 08:40 UTC; despues va
`lectura.sql` (criterio 7 de X.1), con brief de lectura aparte.
