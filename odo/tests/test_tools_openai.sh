#!/usr/bin/env bash
# Test OpenAI-compatible tool-calling through ODO → chimere-server.
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
  echo "$resp" | jq -c '{content: .choices[0].message.content, tool_calls: .choices[0].message.tool_calls, finish: .choices[0].finish_reason, odo: .odo}' 2>/dev/null || echo "RAW: $resp"
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

WEATHER_TOOL='{
  "type": "function",
  "function": {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "parameters": {
      "type": "object",
      "properties": {
        "city": {"type": "string", "description": "City name"},
        "unit": {"type": "string", "enum": ["c", "f"]}
      },
      "required": ["city"]
    }
  }
}'

CALC_TOOL='{
  "type": "function",
  "function": {
    "name": "calculate",
    "description": "Evaluate a math expression.",
    "parameters": {
      "type": "object",
      "properties": {"expression": {"type": "string"}},
      "required": ["expression"]
    }
  }
}'

# ── T1: tool_choice=auto — model may call ──────────────────────────────────
run "tool_choice=auto emits tool_call for weather query" \
"{
  \"messages\": [{\"role\": \"user\", \"content\": \"What's the weather in Tokyo right now? Use the tool.\"}],
  \"max_tokens\": 256,
  \"mode\": \"fast\",
  \"tools\": [$WEATHER_TOOL],
  \"tool_choice\": \"auto\",
  \"chat_template_kwargs\": {\"enable_thinking\": false}
}" \
'(.choices[0].message.tool_calls // []) | length >= 1'

# ── T2: tool_choice=none — no tool call ────────────────────────────────────
run "tool_choice=none prevents tool_calls" \
"{
  \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Tokyo?\"}],
  \"max_tokens\": 256,
  \"mode\": \"fast\",
  \"tools\": [$WEATHER_TOOL],
  \"tool_choice\": \"none\",
  \"chat_template_kwargs\": {\"enable_thinking\": false}
}" \
'(.choices[0].message.tool_calls // []) | length == 0'

# ── T3: tool_choice=required — forces ≥1 tool call ─────────────────────────
run "tool_choice=required forces a tool_call" \
"{
  \"messages\": [{\"role\": \"user\", \"content\": \"Check the weather in Rome.\"}],
  \"max_tokens\": 256,
  \"mode\": \"fast\",
  \"tools\": [$WEATHER_TOOL, $CALC_TOOL],
  \"tool_choice\": \"required\",
  \"chat_template_kwargs\": {\"enable_thinking\": false}
}" \
'(.choices[0].message.tool_calls // []) | length >= 1'

# ── T4: tool_choice=function(name) — specific tool ─────────────────────────
run "tool_choice=function targets calculator" \
"{
  \"messages\": [{\"role\": \"user\", \"content\": \"Compute 42 + 8\"}],
  \"max_tokens\": 256,
  \"mode\": \"fast\",
  \"tools\": [$WEATHER_TOOL, $CALC_TOOL],
  \"tool_choice\": {\"type\": \"function\", \"function\": {\"name\": \"calculate\"}},
  \"chat_template_kwargs\": {\"enable_thinking\": false}
}" \
'(.choices[0].message.tool_calls // []) | map(.function.name) | index("calculate") != null'

# ── T5: parallel_tool_calls=true — multiple calls possible ─────────────────
run "parallel_tool_calls emits multiple calls when relevant" \
"{
  \"messages\": [{\"role\": \"user\", \"content\": \"Get the weather in BOTH Tokyo AND Paris. Issue the two tool calls in parallel now.\"}],
  \"max_tokens\": 512,
  \"mode\": \"fast\",
  \"tools\": [$WEATHER_TOOL],
  \"tool_choice\": \"required\",
  \"parallel_tool_calls\": true,
  \"chat_template_kwargs\": {\"enable_thinking\": false}
}" \
'(.choices[0].message.tool_calls // []) | length >= 1'

# ── T6: odo metadata surfaces tool_choice info ────────────────────────────
run "odo metadata exposes tool_choice kind" \
"{
  \"messages\": [{\"role\": \"user\", \"content\": \"Compute 2+2\"}],
  \"max_tokens\": 128,
  \"mode\": \"fast\",
  \"tools\": [$CALC_TOOL],
  \"tool_choice\": \"required\",
  \"chat_template_kwargs\": {\"enable_thinking\": false}
}" \
'.odo.tool_choice.kind == "required"'

echo ""
echo "═══════════════════════════════"
echo "RESULTS: $PASS passed, $FAIL failed"
echo "═══════════════════════════════"
exit $FAIL
