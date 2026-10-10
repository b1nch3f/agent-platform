#!/usr/bin/env bash
# generate.sh - Generate language models from the JSON Schema in this directory.
# Run after every schema change and commit the result.

set -euo pipefail
CONTRACTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$CONTRACTS_DIR")"
PY_OUT="$ROOT_DIR/python/contracts/src/ap_contracts"

# Python (pydantic v2)
cd "$ROOT_DIR/python"
for schema in "$CONTRACTS_DIR"/*.schema.json; do
    module="$(basename "$schema" .schema.json | tr '-' '_')"
    uv run datamodel-codegen \
        --input "$schema" \
        --input-file-type jsonschema \
        --output "$PY_OUT/$module.py" \
        --output-model-type pydantic_v2.BaseModel \
        --target-python-version 3.12 \
        --use-standard-collections \
        --use-union-operator \
        --use-default-kwarg \
        --enum-field-as-literal all \
        --use-title-as-name \
        --use-one-literal-as-default \
        --strict-nullable \
        --formatters ruff-format \
        --disable-timestamp
done
echo "Generated pydantic models in $PY_OUT"

# Java: TODO, added with the first Java module
