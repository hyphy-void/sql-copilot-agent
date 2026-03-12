#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"

echo "[1/3] health"
curl -sS "${BASE_URL}/health" | jq .

echo "[2/3] schema tables"
curl -sS "${BASE_URL}/schema/tables" | jq .

echo "[3/3] autocomplete"
curl -sS "${BASE_URL}/autocomplete" \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT u. FROM users u","cursor":9,"use_llm":false}' | jq .
