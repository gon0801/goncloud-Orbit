# Lector fresco, pasada 2 (solo el diff de 3db714ec)

## Bloqueante
1. Tras un rollback los interruptores no se aplican nunca más: `rollback.sh` no regresaba `$SRV/despliegue/sha`, y el paso (6) compara contra ese archivo. Modelado en git en el scratchpad del lector (E → F → F2): tras el rollback de F, F2 no converge ninguna clave y `meta_live` (3) da `f` en cada reintento.

## No bloqueantes
2. Un despliegue fuera de orden (G más viejo después de F) regresaba claves en vivo a sombra.
3. `productos-modo` con el valor vigente falla con "edicion vacia" (`config_write.py:353`), en la convergencia y en el rollback.
4. La salida 75 obliga a reentregar algo distinto (`effects.py:452-456`) y el plan no decía qué.
5. `--ensayo` corría el paso (0) y escribía `ultimo.env` en producción.
6. `ultimo.env` se escribía antes del `flock`; una repetición tras una caída perdía lo anotado.
7. El rollback restauraba solo `app/`, pero (5) despliega también `tools/`, Dockerfile, .dockerignore, pyproject.toml y uv.lock.
8. F.2: si falla `lee_entradas_productos` no se sabe qué grupos son aptos.
9. D.6: `DROP VIEW v_margen_producto` falla sin CASCADE (depende `v_economia_producto`, 0021:161).
10. E.4 contradecía "S6 solo aplica a la pausa".

## Verificado y correcto (resumen)
Rollback no hace nada con guarda agotada, muerte en guardas o `ultimo.env` ajeno; checklist respeta el apagado manual; sonda estricta aplicable con `psql -q -tA`; F.1, I.2 y E salen 1 si `concilia amazon_us` falla; el peor reporte de productos de MX en 30 días tardó 1117 s, dentro del corte de 1500 s y del tope de 3600 s; tolerancia 0.01 viable (1 cambio en 16 090 pares maduros); `orbit-verify launch/drive/cleanup` corre en copia limpia; S1 medido: 9 goals, ninguno con target; afirmaciones sobre el código ciertas en master.
