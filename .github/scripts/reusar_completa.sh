#!/usr/bin/env bash
# Reuso de la bateria `completa` por arbol de git -- una sola vez por bloque.
#
# En push a main/master: si el ARBOL del commit empujado (`git rev-parse
# HEAD^{tree}`) es identico al arbol de la cabeza del PR que lo produjo y
# esa cabeza tiene TODAS las partes de la bateria en success, el contenido
# ya fue validado: mismo arbol = mismo contenido, asi que se reusa el
# veredicto y el job sale success sin repetir pytest. Desde #481 la bateria
# corre repartida en 6 partes en paralelo y sus check runs se llaman
# "completa (1, 6)" ... "completa (6, 6)" (i = parte, N = total): se exige
# success en TODAS, de la 1 a la N sin repetir; con alguna parte faltante o
# sin success (o nombres que ya no cuadran con la matriz) se CORRE. La
# cabeza del PR se consulta con la API (commits/{sha}/pulls -> head.sha),
# por lo que sirve tanto para merge commit como para squash.
#
# FAIL-CLOSED hacia CORRER, nunca hacia saltar: si el arbol difiere (la base
# avanzo y no se mezclo), no hay PR asociado (push directo), o cualquier
# consulta falla (gh ausente, red, rate limit), el script cae al mismo
# fallback y el job corre la bateria completa. Este script JAMAS termina en
# error: su unica salida es reusar=true (salta la bateria) o reusar=false
# (la corre completa).

set -u

reusar=false
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  trap 'printf "reusar=%s\n" "$reusar" >> "$GITHUB_OUTPUT"' EXIT
fi

terminar() {
  # $1: razon por la que NO se reusa. Sale 0 para que el job siga y corra.
  echo "reuso: $1 -- se corre la bateria completa" >&2
  exit 0
}

command -v gh >/dev/null 2>&1 || terminar "gh no esta disponible"

repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
[ -n "$repo" ] || terminar "sin repositorio (GH_REPO/GITHUB_REPOSITORY)"

sha="$(git rev-parse HEAD)" || terminar "no hay HEAD"
arbol="$(git rev-parse 'HEAD^{tree}')" || terminar "no se pudo leer el arbol de HEAD"
echo "reuso: arbol empujado $arbol (commit $sha)"

# Cabeza del PR asociado al commit empujado (merge commit o squash).
if ! cuerpo="$(gh api "repos/$repo/commits/$sha/pulls" 2>/dev/null)"; then
  terminar "la consulta del PR fallo"
fi
cabeza="$(printf '%s' "$cuerpo" | jq -r '([.[] | select(.head.sha != null)][0].head.sha) // empty' 2>/dev/null)" \
  || terminar "la respuesta del PR no se pudo leer"
[ -n "$cabeza" ] || terminar "sin PR asociado al commit empujado"

# Arbol de la cabeza: por API porque el checkout de push puede no traer el
# objeto de la cabeza del PR (fetch-depth por defecto).
arbol_cabeza="$(gh api "repos/$repo/commits/$cabeza" 2>/dev/null | jq -r '.commit.tree.sha // empty' 2>/dev/null)" \
  || terminar "la consulta del arbol de la cabeza fallo"
[ -n "$arbol_cabeza" ] || terminar "la respuesta del arbol de la cabeza vino vacia"
if [ "$arbol" != "$arbol_cabeza" ]; then
  terminar "el arbol difiere de la cabeza del PR ($cabeza, arbol $arbol_cabeza)"
fi

if ! cuerpo="$(gh api "repos/$repo/commits/$cabeza/check-runs?per_page=100" 2>/dev/null)"; then
  terminar "la consulta de check runs fallo"
fi

# Veredicto por partes: "success" SOLO si los check runs de la matriz
# ("completa (i, N)") cubren 1..N sin repetir y TODOS estan en success.
# Cualquier otra cosa (una parte en failure o cancelled, una faltante,
# totales mezclados, un solo `completa` viejo sin matriz, ninguna parte) cae
# a CORRER: fail-closed, igual que el resto del script.
veredicto="$(printf '%s' "$cuerpo" | jq -c '
  [.check_runs[]?
    | select(.name | startswith("completa (") and endswith(")"))
    | .name as $nombre
    | ($nombre | ltrimstr("completa (") | rtrimstr(")") | split(", ")) as $trozos
    | select(($trozos | length) == 2)
    | {
        parte: ($trozos[0] | tonumber),
        total: ($trozos[1] | tonumber),
        ok: (.conclusion == "success"),
        url: .html_url
      }
  ] as $partes
  | if ($partes | length) == 0 then {v: "ninguna-parte"}
    elif (($partes | map(.total) | unique) | length) != 1 then {v: "totales-mezclados"}
    else
      ($partes[0].total) as $n
      | (($partes | map(.parte) | unique) | length) as $distintas
      | if (($partes | map(.ok) | all) | not) then
          {v: "alguna-sin-success", malas: [$partes[] | select(.ok == false) | .parte]}
        elif $distintas != $n or (($partes | map(.parte) | min) != 1)
          or (($partes | map(.parte) | max) != $n) then
          {v: "partes-faltantes", presente: [$partes[].parte]}
        else
          {v: "success", urls: [$partes[].url]}
        end
    end
' 2>/dev/null)" || terminar "la respuesta de check runs no se pudo leer"

caso="$(printf '%s' "$veredicto" | jq -r '.v // empty' 2>/dev/null)" \
  || terminar "el veredicto de las partes no se pudo leer"
[ "$caso" = "success" ] || terminar "la cabeza $cabeza no tiene TODAS las partes de completa en success (veredicto: ${veredicto:-vacio})"

reusar=true
echo "reuso: ARBOL IDENTICO ya validado (todas las partes de completa en success) -- se reusa el veredicto; cabeza del PR: $cabeza"
printf '%s' "$veredicto" | jq -r '.urls[]?' 2>/dev/null | while read -r url; do
  [ -n "$url" ] && echo "reuso: parte reusada: $url"
done
