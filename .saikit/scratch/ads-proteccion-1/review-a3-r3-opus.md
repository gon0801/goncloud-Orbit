# Review A.3-R3 — diff obs4 784ae5f...6aed71b (opus, independiente)

Revisor: `claude -p --model opus`, NO autor del cambio. Fecha: 2026-09-24.
Objeto: solo `tests/test_ads_salud.py` (+36/-6), un commit, ancestro
directo. Metodo: copia via `git archive` en /tmp, `.venv` del repo,
Postgres local; repo intacto.

## Alcance

`git diff --name-only` = solo `tests/test_ads_salud.py`. No toca `app/`
ni migraciones.

## (1) Determinismo: SI

- Omitido (09:00 fijo): exige exit 0 + linea exacta
  `ads-salud chequeo=omitido motivo=pre_1030 ahora=...09:00:00+00:00`.
  Lista de envios vacia: si main() enviara algo, truena.
- Ejecutado (11:00 fijo): exige exit 0 + linea exacta con
  `unidades=0 episodios_abiertos=1`. Con base vacia, el now() SQL no
  influye. Corrido a las 02:38 UTC (real pre-10:30) y pasa: manda el
  reloj fijo.
- Parche: `salud.dt` es el modulo datetime global; monkeypatch lo
  revierte por test. Verificado sin fuga en ambos ordenes.
- 10 passed, 0 skipped (PG disponible: verde real).
- Mutantes (umbral 08:00/12:00, time.time, renombrar clave, exit 1):
  todos mueren en el test que corresponde.

## (2) Asserts: mas estrictos, ninguno debil

Viejo: startswith + `"ahora=" in out` + exit 0. Nuevos: linea completa
por camino + exit 0. Omitido ademas prohibe envios. Lineas 1-417
identicas; DBs temporales con nombres propios (main1/main2).

## Observaciones (no bloqueantes)

1. `definiciones.md:56` nombra `test_a3d_main_imprime_heartbeat_y_sale_cero`,
   que ya no existe. Fix de 1 linea: poner los 2 nombres nuevos. Los
   formatos de linea del doc coinciden con los tests.
2. El test de ejecutado no revisa envio real (`len(textos) == 1` bastaria).
   Nada se debilito (el viejo tampoco); un 2o envio haria salir 1 a main().

VEREDICTO: APROBADO CON OBSERVACIONES
