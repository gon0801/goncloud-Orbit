# Prueba roja de BASE.1 — falla en el SHA previo al cambio y discrimina

## 1. Rojo en el SHA previo (sin el arreglo)

- SHA previo (sin el arreglo): `0d6f92c` (origin/master; `tools/despliegue/comun.sh`
  trae `_(auto03|orbit07|claridad01|arreglos01|arreglos02)_`, sin `arreglos03`).
- Estado: solo la prueba nueva agregada a `tests/test_despliegue_motor.py`;
  ni `comun.sh` ni `desplegar.sh` tocados aún.
- Comando (el Acepta de BASE.1):

```
CI=1 PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs \
  tests/test_despliegue_motor.py -k arreglos03_base_
```

- Entorno de esta PC: `ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55433/postgres`;
  la guarda del módulo exige `bash git psql pg_dump flock md5sum tar` en el PATH, y
  aquí solo existen bash/git/tar/md5sum (Git for Windows). Para la corrida se
  anteponen al PATH `Git\usr\bin` (md5sum real) y tres stubs que siempre salen 0
  (`psql`, `pg_dump`, `flock`): esta prueba no los invoca (solo `bash` y `git`
  reales); los stubs solo satisfacen la guarda de colección. En CI (Linux) esas
  herramientas existen de verdad.

- Salida (fallo):

```
>       assert "0089_arreglos03_prueba.sql\tSELECT to_regclass" in r.stdout
E       assert '0089_arreglos03_prueba.sql\tSELECT to_regclass' in "0087_arreglos01_prueba.sql\tSELECT to_regclass('public.arreglos01_prueba') IS NOT NULL\n0088_arreglos02_prueba.sql\tSELECT to_regclass('public.arreglos02_prueba') IS NOT NULL\n"
E        +  where "0087_arreglos01_prueba.sql\tSELECT to_regclass('public.arreglos01_prueba') IS NOT NULL\n0088_arreglos02_prueba.sql\tSELECT to_regclass('public.arreglos02_prueba') IS NOT NULL\n" = CompletedProcess(args=['bash', '-c', '. tools/despliegue/comun.sh && migraciones_de HEAD'], returncode=0, stdout="0087...ba') IS NOT NULL\n0088_arreglos02_prueba.sql\tSELECT to_regclass('public.arreglos02_prueba') IS NOT NULL\n", stderr='').stdout

tests\test_despliegue_motor.py:1225: AssertionError
1 failed, 47 deselected in 1.65s
```

La lista de `migraciones_de` solo trae las de `arreglos01` y `arreglos02`: la
migración `_arreglos03_` queda fuera del despliegue. Es el fallo que BASE.1 corrige.

# Discriminacion: borrar una etiqueta ajena pone la prueba en rojo

Con el arreglo ya aplicado (`_(auto03|orbit07|claridad01|arreglos01|arreglos02|arreglos03)_`,
commit `4e221c6`) se quitó del grep la etiqueta ajena `arreglos01`:

```
sed -i 's/arreglos01|arreglos02|arreglos03/arreglos02|arreglos03/' tools/despliegue/comun.sh
```

y la misma corrida del Acepta falla, ahora en la aserción de la migración ajena
(salida completa en `ajena.txt`):

```
>       assert "0087_arreglos01_prueba.sql\tSELECT to_regclass" in r.stdout
E       assert '0087_arreglos01_prueba.sql\tSELECT to_regclass' in "0088_arreglos02_prueba.sql\tSELECT to_regclass('public.arreglos02_prueba') IS NOT NULL\n0089_arreglos03_prueba.sql\tSELECT to_regclass('public.arreglos03_prueba') IS NOT NULL\n"

tests\test_despliegue_motor.py:1223: AssertionError
1 failed, 47 deselected in 1.37s
```

Sin `arreglos01`, la migración `0087_arreglos01_prueba.sql` desaparece de la lista y la
prueba lo detecta: el parche no puede borrar etiquetas ajenas. La salida completa está en
`ajena.txt`; `comun.sh` se restauró con `git checkout` después de la demostración.


## 2. Discrimina: borrar una etiqueta ajena la pone en rojo

- SHA con el arreglo ya commiteado: `4e221c6`.
- Mutación temporal (solo para la demostración, luego revertida con
  `git checkout -- tools/despliegue/comun.sh`): quitar `arreglos01|` del grupo
  de etiquetas deja la línea como
  `grep -E '_(auto03|orbit07|claridad01|arreglos02|arreglos03)_'`.
- Mismo comando del Acepta. Salida (fallo):

```
>       assert "0087_arreglos01_prueba.sql\tSELECT to_regclass" in r.stdout
E       assert '0087_arreglos01_prueba.sql\tSELECT to_regclass' in "0088_arreglos02_prueba.sql\tSELECT to_regclass('public.arreglos02_prueba') IS NOT NULL\n0089_arreglos03_prueba.sql\tSELECT to_regclass('public.arreglos03_prueba') IS NOT NULL\n"
E        +  where "0088_arreglos02_prueba.sql\tSELECT to_regclass('public.arreglos02_prueba') IS NOT NULL\n0089_arreglos03_prueba.sql\tSELECT to_regclass('public.arreglos03_prueba') IS NOT NULL\n" = CompletedProcess(args=['bash', '-c', '. tools/despliegue/comun.sh && migraciones_de HEAD'], returncode=0, stdout="0088...ba') IS NOT NULL\n0089_arreglos03_prueba.sql\tSELECT to_regclass('public.arreglos03_prueba') IS NOT NULL\n", stderr='').stdout

tests\test_despliegue_motor.py:1223: AssertionError
1 failed, 47 deselected in 1.37s
```

Sin `arreglos01`, la migración ajena `0087_arreglos01_prueba.sql` desaparece de la
lista y la prueba falla en su aserción (línea 1223): el parche no puede borrar una
etiqueta ajena. Tras `git checkout`, el mismo comando da `1 passed`.
