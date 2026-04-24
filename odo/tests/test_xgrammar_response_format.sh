#!/usr/bin/env bash
# Test XGrammar / response_format end-to-end through ODO → chimere-server.
set -euo pipefail

ODO="http://127.0.0.1:8084"
PASS=0
FAIL=0

run() {
  local name=$1
  local body=$2
  local check=$3  # jq expression that must return 'true'
  echo ""
  echo "── $name ──"
  local resp
  resp=$(curl -sS "$ODO/v1/chat/completions" \
           -H 'Content-Type: application/json' \
           -d "$body")
  echo "$resp" | jq -c '{content: .choices[0].message.content, odo: .odo}' 2>/dev/null || echo "RAW: $resp"
  local ok
  ok=$(echo "$resp" | jq -r "$check" 2>/dev/null || echo "false")
  if [ "$ok" = "true" ]; then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name — check '$check' returned $ok"
    FAIL=$((FAIL + 1))
  fi
}

# ── T1: json_schema (weather) ─────────────────────────────────────────────
run "json_schema: weather object parses" \
'{
  "messages": [
    {"role": "user", "content": "Return exactly the weather in Paris as JSON: the city name and temperature in Celsius (any reasonable value)."}
  ],
  "max_tokens": 256,
  "mode": "fast",
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "weather",
      "schema": {
        "type": "object",
        "properties": {
          "city": {"type": "string"},
          "temp_c": {"type": "number"}
        },
        "required": ["city", "temp_c"],
        "additionalProperties": false
      },
      "strict": true
    }
  },
  "chat_template_kwargs": {"enable_thinking": false}
}' \
'.choices[0].message.content | fromjson | (.city | type == "string") and (.temp_c | type == "number")'

# ── T2: json_object (simple) ─────────────────────────────────────────────
run "json_object: reply is parseable JSON" \
'{
  "messages": [
    {"role": "user", "content": "Return a JSON object with a single key `greeting` set to `hello`."}
  ],
  "max_tokens": 128,
  "mode": "fast",
  "response_format": {"type": "json_object"},
  "chat_template_kwargs": {"enable_thinking": false}
}' \
'.choices[0].message.content | fromjson | (.greeting != null)'

# ── T3: odo metadata surfaces structured info ─────────────────────────────
run "odo metadata exposes response_format.mode" \
'{
  "messages": [{"role": "user", "content": "Return {\"ok\": true}"}],
  "max_tokens": 64,
  "mode": "fast",
  "response_format": {"type": "json_object"},
  "chat_template_kwargs": {"enable_thinking": false}
}' \
'.odo.response_format.mode == "json_object"'

echo ""
echo "═══════════════════════════════"
echo "RESULTS: $PASS passed, $FAIL failed"
echo "═══════════════════════════════"
exit $FAIL
