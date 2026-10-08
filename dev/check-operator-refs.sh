#!/usr/bin/env bash
# Fail when the docs and examples name an operator version that dev/Makefile's
# OPERATOR_REF and the CI integration matrix don't agree on.
#
#   - Links into nebari-operator (blob, tree, releases/download, releases/tag)
#     must use OPERATOR_REF. `main` is allowed: those links point at the
#     nebari-app chart, which is versioned separately.
#   - "Operator version this page tracks" pins must equal OPERATOR_REF.
#   - Prose that names an operator version ("in v0.1.1", "operator v0.1.1",
#     "operator version: `v0.1.1`") must name one the integration matrix tests.
#
# Every match on a line is checked, not just the first. Run from the repo root.
set -euo pipefail

ref=$(sed -n 's/^OPERATOR_REF ?= //p' dev/Makefile)
tested=$(yq '.jobs.*.strategy.matrix.operator-ref[]' .github/workflows/test-integration.yaml | sort -u)
echo "OPERATOR_REF=$ref; integration matrix: $(echo $tested)"

fail=0
err() { echo "::error::$1"; fail=1; }

[ -n "$ref" ] || err "could not read OPERATOR_REF from dev/Makefile"
grep -qxF "$ref" <<<"$tested" || err "OPERATOR_REF $ref is not in the integration matrix"

files=(-- '*.md' '*.yaml' '*.yml')
version='v[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]*[a-z0-9])?'

# git grep -o prints one line per match, so a correct link can't hide a wrong one.
while IFS= read -r hit; do
  v=${hit##*/}
  [ "$v" = "$ref" ] || [ "$v" = main ] || err "$hit: links operator $v, not OPERATOR_REF ($ref)"
done < <(git grep -noE "nebari-operator/(blob|tree|releases/download|releases/tag)/[A-Za-z0-9._-]+" "${files[@]}" || true)

while IFS= read -r hit; do
  v=$(grep -oiE "$version" <<<"$hit")
  [ "$v" = "$ref" ] || err "$hit: page pin is $v, not OPERATOR_REF ($ref)"
done < <(git grep -noE "Operator version this page tracks:\*\* \`$version" "${files[@]}" || true)

while IFS= read -r hit; do
  v=$(grep -oiE "$version" <<<"$hit")
  grep -qxF "$v" <<<"$tested" || err "$hit: names operator $v, which the integration matrix does not test"
done < <(git grep -noiE "(\bin|operator( version:)?) \`?$version" "${files[@]}" || true)

exit $fail
