#!/usr/bin/env python3
"""
skills_loader.py — Scan ~/.chimere/skills/ and expose canonical SKILL.md files.

Purpose
-------
Since 2026-04 Chimère has adopted the Anthropic Agent Skills format:
each skill lives in ~/.chimere/skills/<name>/SKILL.md with a YAML frontmatter
block. This loader

  1. Scans ~/.chimere/skills/ recursively for SKILL.md files
  2. Parses YAML frontmatter + Markdown body
  3. Validates the schema (name/description/trigger_patterns/tools_required/examples)
  4. Exposes helpers used by:
       - odo.py HTTP endpoints (GET /skill/list, /skill/get/<name>)
       - chimere-mcp.py (@mcp.tool wrapping each skill as an MCP tool)
       - routing layer (trigger_patterns as fallback heuristic)

The loader is pure Python stdlib (no PyYAML dependency) — it implements a
minimal YAML parser sufficient for the 5-field frontmatter schema.

Public API
----------
  load_all_skills() -> list[dict]           : scan + parse all SKILL.md files
  get_skill(name: str) -> dict | None        : lookup by name
  match_skill_by_trigger(text: str) -> list[dict] : match user prompt to skills
  render_skill_as_mcp_tool(skill: dict) -> dict   : MCP tool schema
  exec_skill(skill: dict, args: str) -> dict      : synchronous exec (if runnable)

Environment
-----------
  CHIMERE_SKILLS_DIR : override scan root (default: ~/.chimere/skills)

Exit codes
----------
When run directly (CLI): 0 on success, 1 on parse error.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_SKILLS_DIR = Path.home() / ".chimere" / "skills"
SKILLS_DIR = Path(
    os.environ.get("CHIMERE_SKILLS_DIR", str(DEFAULT_SKILLS_DIR))
).expanduser()

REQUIRED_FIELDS = {"name", "description"}
RECOMMENDED_FIELDS = {"trigger_patterns", "tools_required", "examples"}

# In-memory cache keyed by SKILL.md mtime (lazy reload)
_CACHE: dict[str, Any] = {"mtime": 0.0, "skills": []}


# ── Minimal YAML frontmatter parser ─────────────────────────────────────────
#
# Supports:
#   - scalar strings (quoted or unquoted)
#   - integers (timeout_ms)
#   - flow-style lists (`[a, b, c]`) and block-style (`- item`)
#   - nested mappings one level deep (execution.command, execution.timeout_ms)
#
# Intentionally tiny so we don't drag PyYAML into ODO's hot path.

_YAML_LIST_BLOCK = re.compile(r"^\s*-\s+(.*)$")
_YAML_KEY_VALUE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.*)$")


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _parse_flow_list(raw: str) -> list[Any]:
    """`[a, "b c", 3]` → ['a', 'b c', 3]"""
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return [_coerce_scalar(inner)]
    body = inner[1:-1].strip()
    if not body:
        return []
    # Split on commas respecting simple quotes
    out: list[Any] = []
    buf = ""
    in_quote = ""
    for ch in body:
        if in_quote:
            if ch == in_quote:
                in_quote = ""
            buf += ch
        elif ch in ('"', "'"):
            in_quote = ch
            buf += ch
        elif ch == ",":
            out.append(_coerce_scalar(_unquote(buf.strip())))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(_coerce_scalar(_unquote(buf.strip())))
    return out


def _coerce_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "null" or s == "~" or s == "":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    # int
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    # float
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except ValueError:
            pass
    return _unquote(s)


def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Return dict from a leading `---`-delimited YAML block.

    Supports scalars, flow lists, block lists (`- item`), and nested maps
    one level deep. Good enough for Agent Skills frontmatter.
    """
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    # Find the closing ---
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}

    block = lines[1:end]
    data: dict[str, Any] = {}
    i = 0
    while i < len(block):
        line = block[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue

        m = _YAML_KEY_VALUE.match(line)
        if not m:
            i += 1
            continue

        indent = len(m.group(1))
        key = m.group(2)
        raw_val = m.group(3).strip()

        # Top-level key only
        if indent != 0:
            i += 1
            continue

        if raw_val == "":
            # Either a block list or a nested mapping below
            j = i + 1
            child_lines: list[str] = []
            while j < len(block):
                nxt = block[j]
                if not nxt.strip():
                    j += 1
                    continue
                # stop when dedent to zero
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent == 0:
                    break
                child_lines.append(nxt)
                j += 1

            # Decide list vs map
            if child_lines and child_lines[0].lstrip().startswith("-"):
                items: list[Any] = []
                for cl in child_lines:
                    mm = _YAML_LIST_BLOCK.match(cl)
                    if mm:
                        items.append(_coerce_scalar(_unquote(mm.group(1).strip())))
                data[key] = items
            else:
                submap: dict[str, Any] = {}
                for cl in child_lines:
                    sm = _YAML_KEY_VALUE.match(cl)
                    if sm:
                        submap[sm.group(2)] = _coerce_scalar(sm.group(3).strip())
                data[key] = submap
            i = j
            continue

        if raw_val.startswith("["):
            data[key] = _parse_flow_list(raw_val)
        else:
            data[key] = _coerce_scalar(raw_val)
        i += 1

    return data


def _split_frontmatter_body(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, markdown_body)."""
    if not text.startswith("---"):
        return {}, text
    fm = _parse_yaml_frontmatter(text)
    # Remove frontmatter block from body
    try:
        second = text.index("\n---", 3)
        body = text[second + len("\n---"):].lstrip("\r\n")
    except ValueError:
        body = ""
    return fm, body


# ── Scanner ────────────────────────────────────────────────────────────────

def _parse_skill_file(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {"_error": f"read failed: {e}", "_path": str(path)}
    fm, body = _split_frontmatter_body(text)
    if not fm:
        return {"_error": "missing YAML frontmatter", "_path": str(path)}
    # Validate
    missing = REQUIRED_FIELDS - set(fm.keys())
    skill: dict[str, Any] = {
        "name": fm.get("name") or path.parent.name,
        "description": fm.get("description", ""),
        "trigger_patterns": fm.get("trigger_patterns", []) or [],
        "tools_required": fm.get("tools_required", []) or [],
        "examples": fm.get("examples", []) or [],
        "model": fm.get("model"),
        "execution": fm.get("execution") or {},
        "homepage": fm.get("homepage"),
        "install": fm.get("install"),
        "instructions": body.strip(),
        "path": str(path),
        "dir": str(path.parent),
        "valid": not missing,
        "missing_fields": sorted(missing),
    }
    return skill


def load_all_skills(skills_dir: Path | None = None,
                    use_cache: bool = True) -> list[dict[str, Any]]:
    """Scan <skills_dir>/*/SKILL.md and return a list of skill dicts."""
    root = skills_dir or SKILLS_DIR
    if not root.exists():
        return []

    # Cheap cache: max(mtime) across all SKILL.md
    md_files = sorted(root.glob("*/SKILL.md"))
    if not md_files:
        return []

    mtime = max(f.stat().st_mtime for f in md_files)
    if use_cache and _CACHE["skills"] and _CACHE["mtime"] == mtime:
        return _CACHE["skills"]

    skills: list[dict[str, Any]] = []
    for f in md_files:
        parsed = _parse_skill_file(f)
        if parsed:
            skills.append(parsed)

    if use_cache:
        _CACHE["mtime"] = mtime
        _CACHE["skills"] = skills

    return skills


def get_skill(name: str) -> dict[str, Any] | None:
    for s in load_all_skills():
        if s.get("name") == name:
            return s
    return None


def match_skill_by_trigger(text: str) -> list[dict[str, Any]]:
    """Return skills whose trigger_patterns match the given text (case-insensitive)."""
    if not text:
        return []
    lower = text.lower()
    matches: list[dict[str, Any]] = []
    for s in load_all_skills():
        for pat in s.get("trigger_patterns", []) or []:
            if pat and str(pat).lower() in lower:
                matches.append(s)
                break
    return matches


# ── MCP bridge ──────────────────────────────────────────────────────────────

def render_skill_as_mcp_tool(skill: dict[str, Any]) -> dict[str, Any]:
    """Return an MCP-style tool schema from a skill dict.

    The returned dict is suitable for @mcp.tool() registration or manual
    insertion into a FastMCP server. `inputSchema` accepts a single `args`
    string (raw arguments passed to the underlying command).
    """
    name = skill.get("name") or "unknown"
    desc = skill.get("description") or ""
    examples = skill.get("examples") or []
    if examples:
        desc = f"{desc}\n\nExamples:\n" + "\n".join(f"  - {e}" for e in examples[:5])
    return {
        "name": f"skill_{name.replace('-', '_')}",
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": (
                        "Raw arguments string, exactly what the user typed "
                        "after the skill name."
                    ),
                }
            },
            "required": ["args"],
        },
    }


def exec_skill(skill: dict[str, Any], args: str, timeout_override: int | None = None) -> dict[str, Any]:
    """Synchronously execute a skill's command with given args.

    Returns {ok, rc, stdout, stderr, elapsed_ms}. If the skill has no
    `execution.command` (e.g. `ml-training` is instructions-only), returns
    {ok: False, error: "instructions_only"}.
    """
    import time as _t

    exe = skill.get("execution") or {}
    if not isinstance(exe, dict):
        return {"ok": False, "error": "invalid execution block"}
    cmd_tmpl = exe.get("command")
    if not cmd_tmpl:
        return {"ok": False, "error": "instructions_only"}

    # Substitute $args. Use shell-quoted args to be safe.
    # The canonical SKILL.md commands already wrap $args in single quotes,
    # so we simply interpolate the raw string.
    cmd = str(cmd_tmpl).replace("$args", args or "")

    timeout_ms = timeout_override or exe.get("timeout_ms") or 120000
    try:
        timeout_s = max(1, int(timeout_ms) // 1000)
    except (TypeError, ValueError):
        timeout_s = 120

    t0 = _t.time()
    try:
        # Use shell=True because skills' commands are bash snippets with
        # quoted args. All skills live under trusted ~/.openclaw/bin scripts.
        p = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        elapsed = int((_t.time() - t0) * 1000)
        return {
            "ok": p.returncode == 0,
            "rc": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr[-4000:] if p.stderr else "",
            "elapsed_ms": elapsed,
        }
    except subprocess.TimeoutExpired:
        elapsed = int((_t.time() - t0) * 1000)
        return {
            "ok": False,
            "rc": -1,
            "error": f"timeout after {timeout_s}s",
            "elapsed_ms": elapsed,
        }
    except Exception as e:
        return {"ok": False, "rc": -1, "error": str(e)}


# ── HTTP helpers (for ODO integration) ──────────────────────────────────────

def list_skills_json() -> dict[str, Any]:
    """Payload for GET /skill/list."""
    skills = load_all_skills()
    return {
        "count": len(skills),
        "skills_dir": str(SKILLS_DIR),
        "skills": [
            {
                "name": s.get("name"),
                "description": s.get("description"),
                "trigger_patterns": s.get("trigger_patterns"),
                "tools_required": s.get("tools_required"),
                "examples": s.get("examples"),
                "model": s.get("model"),
                "has_command": bool((s.get("execution") or {}).get("command")),
                "timeout_ms": (s.get("execution") or {}).get("timeout_ms"),
                "valid": s.get("valid", False),
                "missing_fields": s.get("missing_fields", []),
            }
            for s in skills
        ],
    }


def get_skill_json(name: str) -> dict[str, Any] | None:
    """Payload for GET /skill/get/<name> — full body included."""
    s = get_skill(name)
    if not s:
        return None
    return {
        "name": s.get("name"),
        "description": s.get("description"),
        "trigger_patterns": s.get("trigger_patterns"),
        "tools_required": s.get("tools_required"),
        "examples": s.get("examples"),
        "model": s.get("model"),
        "execution": s.get("execution"),
        "instructions": s.get("instructions"),
        "path": s.get("path"),
        "valid": s.get("valid", False),
        "missing_fields": s.get("missing_fields", []),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli_main(argv: list[str]) -> int:
    """python skills_loader.py [list|get <name>|match <text>|exec <name> <args>]"""
    if len(argv) <= 1 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        print()
        print("Usage:")
        print("  skills_loader.py list")
        print("  skills_loader.py get <name>")
        print("  skills_loader.py match <text>")
        print("  skills_loader.py exec <name> [args...]")
        return 0

    cmd = argv[1]
    if cmd == "list":
        payload = list_skills_json()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        # Exit 1 if any skill is invalid (for CI)
        return 0 if all(s.get("valid") for s in payload["skills"]) else 1

    if cmd == "get":
        if len(argv) < 3:
            print("usage: skills_loader.py get <name>", file=sys.stderr)
            return 2
        data = get_skill_json(argv[2])
        if data is None:
            print(f"skill not found: {argv[2]}", file=sys.stderr)
            return 1
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if cmd == "match":
        if len(argv) < 3:
            print("usage: skills_loader.py match <text>", file=sys.stderr)
            return 2
        text = " ".join(argv[2:])
        matches = match_skill_by_trigger(text)
        print(json.dumps(
            {"query": text, "matches": [m["name"] for m in matches]},
            indent=2, ensure_ascii=False,
        ))
        return 0

    if cmd == "exec":
        if len(argv) < 3:
            print("usage: skills_loader.py exec <name> [args...]", file=sys.stderr)
            return 2
        sk = get_skill(argv[2])
        if not sk:
            print(f"skill not found: {argv[2]}", file=sys.stderr)
            return 1
        args_str = " ".join(argv[3:])
        result = exec_skill(sk, args_str)
        print(json.dumps(
            {k: v for k, v in result.items() if k != "stdout"},
            indent=2, ensure_ascii=False,
        ))
        if result.get("stdout"):
            print("\n--- stdout ---\n" + result["stdout"])
        return 0 if result.get("ok") else 1

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli_main(sys.argv))
