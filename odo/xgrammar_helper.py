"""
xgrammar_helper.py — Drop-in replacement for static GBNF grammars using XGrammar-2.

XGrammar-2 (arXiv 2601.04426) is the SOTA 2026 structured-decoding library for
agentic LLMs: 6x faster grammar compilation vs llama.cpp's built-in GBNF, ~0
runtime overhead, and it accepts JSON Schemas / EBNF / regex / structural tags
(OpenAI-compatible tool_calls format) directly.

This helper exposes three things:

  1. `grammar_from_json_schema(schema_dict)`
       Compile a Grammar object from an OpenAI-style parameters JSON Schema.
       Returns a xgrammar.Grammar that the inference backend can consume.

  2. `grammar_from_tool_defs(tool_list)`
       Given a list of OpenAI tool definitions (same format ODO uses for
       web_search / calculator / think), compile a single Grammar that
       constrains the model output to ONE valid tool-call JSON.
       Useful for the `code` route where we want guaranteed-valid function
       calls.

  3. `ebnf_tool_call_grammar(tool_list) -> str`
       Fallback: emits an EBNF grammar string compatible with llama.cpp's
       `--grammar` or server `grammar` field. This lets us keep using the
       current gbnf pipeline of ik_llama / llama-server without touching the
       inference binary; XGrammar only replaces the *generator* of grammars
       (offline), not the runtime masker.

Integration plan:

  * Phase 1 (now): use `ebnf_tool_call_grammar()` to produce grammars and feed
    them as `payload["grammar"]` to ik_llama-server. Zero backend change.
  * Phase 2 (later): swap ik_llama-server's GBNF masker for XGrammar's runtime
    token-bitmask masker (requires patching ik_llama). Saves 6x compile cost.

Note on ik_llama compatibility: llama.cpp's grammar syntax is a GBNF variant
close to EBNF. XGrammar's `from_ebnf` accepts standard EBNF; for the textual
round-trip we stick to GBNF semantics (root := ...) so ik_llama parses the
output cleanly.

Graceful degradation: if xgrammar isn't installed, the module still imports
and `XGRAMMAR_OK` is False — callers can fall back to static GBNF files.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("odo.xgrammar")

try:
    import xgrammar as xg  # type: ignore
    from xgrammar import Grammar  # type: ignore
    XGRAMMAR_OK = True
except Exception as _e:  # pragma: no cover
    log.warning("xgrammar not available (%s) — structured decoding disabled", _e)
    XGRAMMAR_OK = False
    xg = None  # type: ignore
    Grammar = None  # type: ignore


# ── High-level factory APIs ─────────────────────────────────────────────────

def grammar_from_json_schema(schema: dict) -> "Grammar | None":
    """Compile a xgrammar.Grammar from an OpenAI-style JSON Schema.

    Returns None if xgrammar is unavailable or the schema is invalid.
    """
    if not XGRAMMAR_OK:
        return None
    try:
        return Grammar.from_json_schema(json.dumps(schema))
    except Exception as e:
        log.warning("xgrammar: schema compile failed: %s", e)
        return None


def grammar_from_tool_defs(tools: list[dict]) -> "Grammar | None":
    """Compile a single Grammar allowing exactly one tool call.

    Expected format (OpenAI):
        [{"type": "function", "function": {"name": "...", "parameters": {...}}}, ...]

    The output grammar enforces a JSON object shape like:
        {"name": "<tool_name>", "arguments": <tool_args_conforming_to_schema>}
    with `name` restricted to the allowed tools and `arguments` constrained by
    the tool's parameters schema.
    """
    if not XGRAMMAR_OK or not tools:
        return None
    # Build a discriminated-union JSON Schema per tool, then union them.
    union_branches: list[dict] = []
    for t in tools:
        fn = t.get("function") or {}
        name = fn.get("name")
        params_schema = fn.get("parameters") or {"type": "object"}
        if not name:
            continue
        union_branches.append({
            "type": "object",
            "properties": {
                "name": {"const": name},
                "arguments": params_schema,
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        })
    if not union_branches:
        return None
    union_schema = {"oneOf": union_branches}
    try:
        return Grammar.from_json_schema(json.dumps(union_schema))
    except Exception as e:
        log.warning("xgrammar: tool-union compile failed: %s", e)
        # Fallback: first branch only
        try:
            return Grammar.from_json_schema(json.dumps(union_branches[0]))
        except Exception:
            return None


# ── GBNF text fallback (for drop-in into llama-server `grammar` field) ──────

def _escape_gbnf(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def ebnf_tool_call_grammar(tools: list[dict]) -> str:
    """Return a GBNF grammar (llama.cpp dialect) that accepts any valid
    tool-call JSON for the given tools.

    This is a simple dialect that's broadly compatible with `--grammar` in
    ik_llama / stock llama.cpp. It does NOT fully validate arguments against
    each tool's parameters schema — it only constrains the top-level JSON to
    {"name": "<allowed_name>", "arguments": <any-object>}.

    For strict per-tool argument validation, use `grammar_from_tool_defs`
    with a backend that supports runtime XGrammar masking.
    """
    names = [t.get("function", {}).get("name") for t in tools if t.get("function", {}).get("name")]
    if not names:
        return ""
    name_alts = " | ".join(f'"{_escape_gbnf(n)}"' for n in names)

    # Minimal JSON grammar + constrained name.
    return rf"""root ::= "{{" ws "\"name\"" ws ":" ws "\"" name "\"" ws "," ws "\"arguments\"" ws ":" ws object ws "}}"
name ::= {name_alts}
value ::= object | array | string | number | "true" | "false" | "null"
object ::= "{{" ws ( pair ( ws "," ws pair )* )? ws "}}"
pair ::= string ws ":" ws value
array ::= "[" ws ( value ( ws "," ws value )* )? ws "]"
string ::= "\"" ( [^"\\] | "\\" ["\\/bfnrt] | "\\u" hex hex hex hex )* "\""
hex ::= [0-9A-Fa-f]
number ::= "-"? ( "0" | [1-9] [0-9]* ) ( "." [0-9]+ )? ( [eE] [+-]? [0-9]+ )?
ws ::= [ \t\n\r]*
"""


# ── Convenience: build a payload field ready for llama-server ───────────────

def inject_grammar_into_payload(payload: dict, tools_allowed: list[dict]) -> dict:
    """Return a new payload with `grammar` field set from the allowed tools.

    If XGrammar is available and the caller requested strict mode via
    `payload['xgrammar_strict'] = True`, the grammar will be stored as
    `_xgrammar` (the Grammar object) for a downstream masker to pick up.
    Otherwise, a GBNF text grammar is placed in `grammar` (compatible with
    ik_llama's runtime).
    """
    if not tools_allowed:
        return payload
    out = dict(payload)
    strict = bool(out.pop("xgrammar_strict", False))
    if strict and XGRAMMAR_OK:
        g = grammar_from_tool_defs(tools_allowed)
        if g is not None:
            out["_xgrammar"] = g
            return out
    gbnf = ebnf_tool_call_grammar(tools_allowed)
    if gbnf:
        out["grammar"] = gbnf
    return out


__all__ = [
    "XGRAMMAR_OK",
    "grammar_from_json_schema",
    "grammar_from_tool_defs",
    "ebnf_tool_call_grammar",
    "inject_grammar_into_payload",
]
