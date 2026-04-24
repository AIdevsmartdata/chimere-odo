"""Smoke tests for the new _apply_response_format / _apply_tool_choice helpers."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".openclaw" / "odo"))

import odo  # noqa: E402

print("=== Module load ===")
print("_XG_HELPER_OK=", odo._XG_HELPER_OK)
print("XGRAMMAR_OK=", odo.XGRAMMAR_OK)


def test(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {extra}" if extra else ""))
    if not cond:
        sys.exit(1)


# --- response_format: text ---
p = {"messages": [{"role": "user", "content": "hi"}], "response_format": {"type": "text"}}
new_p, info = odo._apply_response_format(p)
test("text mode is no-op", info is None)

# --- response_format: json_object ---
p = {"messages": [{"role": "user", "content": "hi"}], "response_format": {"type": "json_object"}}
new_p, info = odo._apply_response_format(p)
test("json_object returns info", info is not None and info["mode"] == "json_object")
test("json_object strips field", "response_format" not in new_p)
test("json_object injects system", new_p["messages"][0]["role"] == "system" and "JSON" in new_p["messages"][0]["content"])

# --- response_format: json_schema (OpenAI nested) ---
schema = {"type": "object", "properties": {"city": {"type": "string"}, "temp_c": {"type": "number"}}, "required": ["city", "temp_c"]}
p = {"messages": [{"role": "user", "content": "Paris weather?"}],
     "response_format": {"type": "json_schema", "json_schema": {"name": "weather", "schema": schema, "strict": True}}}
new_p, info = odo._apply_response_format(p)
test("json_schema mode", info is not None and info["mode"] == "json_schema")
test("json_schema extracted", info["schema"] == schema)
test("json_schema strict", info["strict"] is True)
test("json_schema system", any("SCHEMA" in m.get("content", "") for m in new_p["messages"] if m.get("role") == "system"))

# --- response_format: json_schema (flat alias) ---
p2 = {"messages": [{"role": "user", "content": "..."}], "response_format": {"type": "json_schema", "schema": schema}}
_, info2 = odo._apply_response_format(p2)
test("json_schema flat alias", info2["schema"] == schema)

# --- tool_choice: auto (default, tools present) ---
tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
p = {"messages": [{"role": "user", "content": "search for X"}], "tools": tools}
new_p, info = odo._apply_tool_choice(p)
test("auto default with tools", info["kind"] == "auto" and info["tools_count"] == 1)
test("auto keeps tools", "tools" in new_p and len(new_p["tools"]) == 1)
test("auto strips tool_choice field", "tool_choice" not in new_p and "parallel_tool_calls" not in new_p)

# --- tool_choice: none ---
p = {"messages": [{"role": "user", "content": "hi"}], "tools": tools, "tool_choice": "none"}
new_p, info = odo._apply_tool_choice(p)
test("none kind", info["kind"] == "none")
test("none strips tools", "tools" not in new_p)
test("none injects policy", any("Do NOT call any tool" in m.get("content", "") for m in new_p["messages"]))

# --- tool_choice: required ---
p = {"messages": [{"role": "user", "content": "do it"}], "tools": tools, "tool_choice": "required"}
new_p, info = odo._apply_tool_choice(p)
test("required kind", info["kind"] == "required")
test("required keeps tools", len(new_p["tools"]) == 1)
test("required injects REQUIRED", any("REQUIRED" in m.get("content", "") for m in new_p["messages"]))

# --- tool_choice: specific function ---
p = {"messages": [{"role": "user", "content": "do it"}],
     "tools": tools + [{"type": "function", "function": {"name": "calculator", "parameters": {"type": "object"}}}],
     "tool_choice": {"type": "function", "function": {"name": "calculator"}}}
new_p, info = odo._apply_tool_choice(p)
test("function kind", info["kind"] == "function" and info["name"] == "calculator")
test("function narrows tools", len(new_p["tools"]) == 1 and new_p["tools"][0]["function"]["name"] == "calculator")
test("function injects SPECIFIC", any("SPECIFIC" in m.get("content", "") for m in new_p["messages"]))

# --- parallel_tool_calls: false disables parallel hint ---
p = {"messages": [{"role": "user", "content": "do"}], "tools": tools,
     "tool_choice": "required", "parallel_tool_calls": False}
new_p, info = odo._apply_tool_choice(p)
test("parallel=False recorded", info["parallel"] is False)
test("parallel=False hint absent", all("parallel" not in m.get("content", "").lower() or "disabled" in m.get("content", "").lower() for m in new_p["messages"]))

# --- _validate_json_payload ---
ok, err, parsed = odo._validate_json_payload('{"city":"Paris","temp_c":15.5}', schema)
test("valid JSON parses", ok and parsed == {"city": "Paris", "temp_c": 15.5})

ok, err, parsed = odo._validate_json_payload('```json\n{"city":"Paris","temp_c":15.5}\n```', schema)
test("JSON inside ```json fence", ok)

ok, err, parsed = odo._validate_json_payload('The answer is: {"city":"Paris","temp_c":15.5} done.', schema)
test("JSON inside prose (first-balanced fallback)", ok and parsed["city"] == "Paris")

ok, err, _ = odo._validate_json_payload('not json', schema)
test("invalid JSON returns error", not ok and err is not None)

# --- _extract_first_json_object ---
out = odo._extract_first_json_object('prose {"a":1,"b":[1,{"c":"d\\"e"}]} suffix')
test("balanced JSON with nested + escaped quote", out == '{"a":1,"b":[1,{"c":"d\\"e"}]}')

print("\nAll smoke tests passed.")
