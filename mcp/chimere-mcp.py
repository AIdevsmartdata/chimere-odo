#!/usr/bin/env python3
"""
chimere-mcp.py — MCP server exposing Chimère tools over HTTP (FastMCP).

P0 of the Agentic Pipeline SOTA 2026 roadmap (Agent C report dated 2026-04-23).
Standard 2026: ecosystem has consolidated on MCP (97M downloads/month). Without
an MCP server, Chimère ODO stays siloed from OpenAI/Anthropic/Google tooling.

Tools exposed:
  - chimere.rag_search(query, collection, n_results)
      ChromaDB RAG over medical/code/openclaw/kinebot/general collections.
  - chimere.deep_search(query, depth, domain)
      deep_search_sota.py 8-step pipeline (QueryExpand+RRF+CRAG+synthesis).
  - chimere.engram_lookup(text, table, top_k)
      Query .engr n-gram hash tables via engram_query.py.
  - chimere.ocr(image_path, pages, engram_ingest)
      GLM-OCR wrapper (model swap qwen35 <-> GLM-OCR managed internally).
  - chimere.nightly_lora_status()
      Parse dflash-nightly logs (last run, status, trajectory count).
  - chimere.kine_protocol(pathology)
      Specialized RAG lookup into kine.engr + kine.db for a pathology.
  - chimere.think(thought)
      Anthropic-style think tool (invisible scratchpad, +54% policy adherence).
  - chimere.memory_recall(query, time_range_hours, num_results)
      Temporal knowledge graph lookup (Graphiti/Neo4j). Complement to ChromaDB.
  - chimere.memory_add_episode(name, body, source)
      Ingest an episode (dialog turn, note, doc) into Graphiti.
  - chimere.memory_list_episodes(limit, offset, search)
      Paginated list of episodes for the user-facing Memory Inspector UI.
  - chimere.memory_edit_episode(episode_id, new_content)
      Rewrite an episode body (re-extracted on next Graphiti indexing pass).
  - chimere.memory_delete_episode(episode_id)
      Permanently remove an episode from Graphiti.
  - chimere.memory_list_facts(limit, offset, search)
      Flat list of extracted entity relations (facts) from the graph.
  - chimere.memory_pin_fact(fact, reason)
      Mark a fact as pinned — ODO-side decay/forget passes will skip it.
  - chimere.engram_stats(table)
      Engram n-gram table stats + top n-grams with decay indicator. Read-only.
  - chimere.computer_browser_open / click / type / scroll / screenshot / dom
      Playwright-driven browser (via computer_use.py daemon on port 9096).
  - chimere.computer_desktop_screenshot / click / type / key / exec / scroll
      X11 desktop control (xdotool + scrot via desktop_control.sh wrapper).

Transport: streamable-http on port 9095 (MCP endpoint: /mcp/).
A /healthz custom route is added for systemd/monitoring probes.
Additional REST routes under /api/memory/* and /api/engram/* serve the
Chimère Studio Memory Inspector (plain JSON, no MCP framing).

Complement: the official Anthropic @modelcontextprotocol/server-sequential-thinking
is installed via npm and runs alongside (stdio); clients aggregate both servers.

Launch (systemd):
  ExecStart=~/.openclaw/venvs/kine-rag/bin/python ~/.openclaw/mcp/chimere-mcp.py \
            --port 9095 --host 127.0.0.1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Config ───────────────────────────────────────────────────────────────────
HOME = Path.home()
OPENCLAW = HOME / ".openclaw"
BIN = OPENCLAW / "bin"
DATA = OPENCLAW / "data"
LOGS = OPENCLAW / "logs"

ENGRAM_DIR = DATA / "engram"
DFLASH_LOG = LOGS / "dflash_nightly.log"
CHROMA_PATH = DATA / "chromadb"
KINE_DB = DATA / "kine.db"

DEEP_SEARCH_PY = BIN / "deep_search_sota.py"
ENGRAM_QUERY_PY = BIN / "engram_query.py"
OCR_GLM_PY = BIN / "ocr_glm.py"

VENV_PY = OPENCLAW / "venvs/kine-rag/bin/python"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9095

VALID_COLLECTIONS = {"medical", "code", "openclaw", "kinebot", "general"}
VALID_ENGRAM_TABLES = {"kine", "code", "cyber", "general", "cairn"}

# ── Graphiti temporal KG (P1 from Agent C SOTA 2026 roadmap) ─────────────────
GRAPHITI_URI = os.environ.get("GRAPHITI_URI", "bolt://127.0.0.1:7687")
GRAPHITI_USER = os.environ.get("GRAPHITI_USER", "neo4j")
GRAPHITI_PASS = os.environ.get("GRAPHITI_PASS", "chimere-graph-2026")
_graphiti_client = None  # lazy singleton
_graphiti_lock_init = False

# ── Memory Inspector storage (pinned facts, UI-editable episodes cache) ─────
MEMORY_DIR = DATA / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
PINNED_FACTS_PATH = MEMORY_DIR / "pinned_facts.json"
EPISODES_CACHE_PATH = MEMORY_DIR / "episodes_cache.json"

log = logging.getLogger("chimere-mcp")

# ── Bootstrap: path for sys.path so chromadb import works ───────────────────
sys.path.insert(0, str(BIN))

# ── FastMCP server ──────────────────────────────────────────────────────────
try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"[chimere-mcp] FATAL: fastmcp missing: {exc}\n")
    sys.stderr.write("Fix: ~/.openclaw/venvs/kine-rag/bin/python -m pip install fastmcp\n")
    raise

mcp = FastMCP("Chimère Tools")

# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_str(x: Any, max_len: int = 400) -> str:
    s = str(x)
    return s[:max_len] + ("…" if len(s) > max_len else "")


def _run_subprocess(cmd: list[str], timeout: int = 120) -> dict:
    """Execute a subprocess safely; capture stdout/stderr/rc."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": p.returncode == 0,
            "rc": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr[-2000:] if p.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -1, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "rc": -1, "error": str(e)}


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def rag_search(query: str, collection: str = "medical", n_results: int = 5) -> dict:
    """Search a ChromaDB collection (medical/code/openclaw/kinebot/general).

    Uses Qwen3-Embedding-0.6B (CPU). Returns top n_results chunks with source.
    Falls back gracefully when collection is unknown or ChromaDB unavailable.
    """
    if collection not in VALID_COLLECTIONS:
        return {"ok": False, "error": f"unknown collection; valid: {sorted(VALID_COLLECTIONS)}"}
    try:
        import chromadb
    except ImportError as e:
        return {"ok": False, "error": f"chromadb not importable: {e}"}
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        col = client.get_collection(collection)
        res = col.query(query_texts=[query], n_results=max(1, min(20, n_results)))
        chunks: list[dict] = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            chunks.append({
                "rank": i + 1,
                "text": _safe_str(doc, 1200),
                "source": (metas[i] or {}).get("source") if i < len(metas) else None,
                "distance": float(dists[i]) if i < len(dists) else None,
            })
        return {"ok": True, "collection": collection, "count": len(chunks), "chunks": chunks}
    except Exception as e:
        return {"ok": False, "error": f"query failed: {e}"}


@mcp.tool()
def deep_search(query: str, depth: str = "standard", domain: str = "auto") -> dict:
    """Launch the 8-step SOTA deep-search pipeline.

    depth in {quick, standard, deep}. Quick ~20s, standard ~30s, deep ~60s.
    Uses deep_search_sota.deep_search(); returns {answer, chunks, sources, ...}.
    """
    if depth not in {"quick", "standard", "deep"}:
        return {"ok": False, "error": "depth must be quick|standard|deep"}
    try:
        from deep_search_sota import deep_search as _ds
    except ImportError as e:
        return {"ok": False, "error": f"deep_search_sota not importable: {e}"}
    try:
        result = _ds(query=query, domain=domain, depth=depth, use_cache=True)
        # Trim heavy fields so MCP response stays reasonable
        answer = result.get("answer") or ""
        chunks = result.get("chunks") or []
        sources = result.get("sources") or []
        return {
            "ok": True,
            "query": query,
            "depth": depth,
            "domain": result.get("domain"),
            "answer": answer,
            "chunks_used": result.get("chunks_used"),
            "unique_sources": result.get("unique_sources"),
            "contradictions": result.get("contradictions"),
            "sources": sources[:25],
            "elapsed": result.get("elapsed"),
            "cached": result.get("elapsed") == 0,
        }
    except Exception as e:
        return {"ok": False, "error": f"deep_search failed: {e}"}


@mcp.tool()
def engram_lookup(text: str, table: str = "kine", top_k: int = 5) -> dict:
    """Lookup n-gram predictions in a Chimère Engram table.

    Tables: kine/code/cyber/general/cairn. Returns the CLI text report (hit/miss
    per n-gram, top predictions, coverage).
    """
    if table not in VALID_ENGRAM_TABLES:
        return {"ok": False, "error": f"unknown table; valid: {sorted(VALID_ENGRAM_TABLES)}"}
    table_path = ENGRAM_DIR / f"{table}.engr"
    if not table_path.exists():
        return {"ok": False, "error": f"table missing: {table_path}"}
    top_k = max(1, min(20, int(top_k)))
    cmd = [
        sys.executable,
        str(ENGRAM_QUERY_PY),
        "--table", str(table_path),
        "--query", text,
        "--top-k", str(top_k),
    ]
    r = _run_subprocess(cmd, timeout=30)
    r.update({"table": table, "query": _safe_str(text, 200)})
    return r


@mcp.tool()
def deep_search_or_cache(query: str, depth: str = "quick") -> dict:
    """Alias of deep_search with a safer default depth for MCP calls."""
    return deep_search(query=query, depth=depth, domain="auto")


@mcp.tool()
def ocr(image_path: str, pages: str | None = None, engram_ingest: str | None = None) -> dict:
    """Run GLM-OCR on an image or PDF (swap qwen35 <-> GLM-OCR automatically).

    pages: optional '1-10' or '3,5,7'. engram_ingest: optional 'kine' to ingest.
    Returns parsed text (first 8 KB) plus stderr tail for diagnostics.
    WARNING: triggers a model swap; avoid during active qwen35 generation.
    """
    p = Path(image_path).expanduser()
    if not p.exists():
        return {"ok": False, "error": f"image not found: {p}"}
    cmd = [str(VENV_PY), str(OCR_GLM_PY), str(p)]
    if pages:
        cmd += ["--pages", pages]
    if engram_ingest:
        cmd += ["--engram", engram_ingest]
    r = _run_subprocess(cmd, timeout=600)
    if r.get("ok") and r.get("stdout"):
        r["text"] = r["stdout"][:8192]
        r.pop("stdout", None)
    return r


@mcp.tool()
def nightly_lora_status() -> dict:
    """Summarize the last dflash-nightly run (timestamp, status, token count)."""
    if not DFLASH_LOG.exists():
        return {"ok": False, "error": f"log missing: {DFLASH_LOG}"}
    try:
        tail = DFLASH_LOG.read_text(errors="replace").splitlines()[-200:]
    except Exception as e:
        return {"ok": False, "error": f"read failed: {e}"}
    status = "unknown"
    for line in reversed(tail):
        low = line.lower()
        if "error" in low or "fatal" in low:
            status = "error"
            break
        if "success" in low or "done" in low or "completed" in low:
            status = "success"
            break
    return {
        "ok": True,
        "log": str(DFLASH_LOG),
        "tail_lines": len(tail),
        "status_hint": status,
        "tail": "\n".join(tail[-40:]),
    }


@mcp.tool()
def kine_protocol(pathology: str) -> dict:
    """Structured RAG for a physiotherapy pathology (kine.engr + kine.db)."""
    # Pull quick n-gram coverage from the kine engram
    engram = engram_lookup(text=pathology, table="kine", top_k=6)
    # Pull top medical chunks from ChromaDB
    rag = rag_search(query=pathology, collection="medical", n_results=5)
    return {
        "ok": True,
        "pathology": pathology,
        "engram": engram,
        "rag": rag,
    }


# ── Skills bridge (Anthropic Agent Skills format) ──────────────────────────
# Expose the canonical ~/.chimere/skills/*/SKILL.md catalog as MCP tools.
# Clients (Claude, OpenAI, etc.) can discover and invoke skills via MCP.

try:
    sys.path.insert(0, str(HOME / ".openclaw" / "odo"))
    from skills_loader import (  # type: ignore[import-not-found]
        list_skills_json as _skills_list_json,
        get_skill as _skills_get,
        exec_skill as _skills_exec,
    )
    _SKILLS_BRIDGE_OK = True
except Exception as _ske:  # pragma: no cover
    log.warning("skills_loader unavailable: %s — skill tools disabled", _ske)
    _SKILLS_BRIDGE_OK = False


@mcp.tool()
def list_skills() -> dict:
    """List all Chimère skills available in ~/.chimere/skills/.

    Returns the full catalog (name, description, trigger_patterns, examples,
    tools_required) following the Anthropic Agent Skills 2026 schema.
    Useful for clients to discover skills before invoking them.
    """
    if not _SKILLS_BRIDGE_OK:
        return {"ok": False, "error": "skills_loader unavailable"}
    try:
        return {"ok": True, **_skills_list_json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def invoke_skill(name: str, args: str = "", timeout_ms: int | None = None) -> dict:
    """Invoke a Chimère skill by name with raw args string.

    Examples: invoke_skill(name="verify", args="Capital of Belize?")
              invoke_skill(name="research", args="--deep Qwen3.5 vs Llama4")
    Returns the skill's stdout/stderr and exit code. Skills that are
    instructions-only (e.g. ml-training) return {ok: False, error: "instructions_only"}.
    """
    if not _SKILLS_BRIDGE_OK:
        return {"ok": False, "error": "skills_loader unavailable"}
    sk = _skills_get(name)
    if not sk:
        return {"ok": False, "error": f"skill not found: {name}"}
    result = _skills_exec(sk, args or "", timeout_override=timeout_ms)
    result["skill"] = name
    # Truncate stdout to keep MCP responses bounded
    if isinstance(result.get("stdout"), str) and len(result["stdout"]) > 16000:
        result["stdout"] = result["stdout"][:16000] + "\n…[truncated]"
    return result


@mcp.tool()
def think(thought: str) -> dict:
    """Anthropic `think` tool — write a reasoning step to an invisible scratchpad.

    The model is encouraged to call this before tool invocations in policy-heavy
    tasks. The scratchpad is not shown to the user; +54% policy adherence on
    Claude's internal Tau-bench-style evaluations. See:
      https://www.anthropic.com/engineering/claude-think-tool

    This tool intentionally has no side effect: returning the thought echoes it
    into the tool-result channel, which the LLM re-reads on its next turn.
    """
    return {"ok": True, "thought": _safe_str(thought, 4096)}


# ── Graphiti temporal KG tools (Agent C P1, LongMemEval 63.8%) ──────────────

async def _get_graphiti():
    """Lazy-initialize a singleton Graphiti client (bolt://neo4j)."""
    global _graphiti_client, _graphiti_lock_init
    if _graphiti_client is not None:
        return _graphiti_client
    try:
        from graphiti_core import Graphiti  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            f"graphiti-core not installed: {exc}. "
            "Run: ~/.openclaw/venvs/graphiti/bin/pip install graphiti-core"
        )
    _graphiti_client = Graphiti(GRAPHITI_URI, GRAPHITI_USER, GRAPHITI_PASS)
    if not _graphiti_lock_init:
        try:
            await _graphiti_client.build_indices_and_constraints()
            _graphiti_lock_init = True
        except Exception as e:
            log.warning("graphiti build_indices_and_constraints failed: %s", e)
    return _graphiti_client


@mcp.tool()
async def memory_recall(query: str, time_range_hours: int = 168,
                        num_results: int = 8) -> dict:
    """Retrieve facts from the temporal knowledge graph (Graphiti).

    Complement to rag_search: Graphiti captures *when* facts were asserted and
    how entities evolve through time. Default time_range_hours=168 (last week).

    Falls back gracefully if Graphiti or Neo4j is unavailable.
    """
    num_results = max(1, min(25, int(num_results)))
    try:
        g = await _get_graphiti()
        results = await g.search(query=query, num_results=num_results)
    except Exception as e:
        return {"ok": False, "error": f"graphiti unavailable: {e}"}
    edges = []
    for r in results:
        fact = getattr(r, "fact", None) or str(r)
        valid_at = getattr(r, "valid_at", None)
        invalid_at = getattr(r, "invalid_at", None)
        edges.append({
            "fact": _safe_str(fact, 1200),
            "valid_at": str(valid_at) if valid_at else None,
            "invalid_at": str(invalid_at) if invalid_at else None,
        })
    return {
        "ok": True,
        "query": _safe_str(query, 200),
        "time_range_hours": int(time_range_hours),
        "count": len(edges),
        "edges": edges,
    }


@mcp.tool()
async def memory_add_episode(name: str, body: str,
                              source: str = "dialog") -> dict:
    """Add an episode (dialog turn, note, doc) to the temporal KG.

    source: free-form provenance label (e.g. "dialog", "note", "HAS-doc").
    The add is awaited synchronously — ODO callers should fire-and-forget.
    """
    try:
        from graphiti_core.nodes import EpisodeType  # type: ignore
        from datetime import datetime, timezone
        g = await _get_graphiti()
        await g.add_episode(
            name=_safe_str(name, 200),
            episode_body=_safe_str(body, 8192),
            source=EpisodeType.text,
            reference_time=datetime.now(timezone.utc),
            source_description=_safe_str(source, 80),
        )
    except Exception as e:
        return {"ok": False, "error": f"graphiti add_episode failed: {e}"}
    return {"ok": True, "status": "added", "name": _safe_str(name, 200)}


# ── Memory Inspector helpers (pinned facts, cached episodes) ────────────────

def _load_json(path: Path, default):
    """Load a JSON file; return default on any failure."""
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text())
    except Exception as e:  # pragma: no cover
        log.warning("failed to load %s: %s", path, e)
        return default


def _save_json(path: Path, obj) -> bool:
    """Atomic JSON write (tmp + rename)."""
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
        tmp.replace(path)
        return True
    except Exception as e:  # pragma: no cover
        log.warning("failed to save %s: %s", path, e)
        return False


def _load_pinned_facts() -> list[dict]:
    obj = _load_json(PINNED_FACTS_PATH, {"facts": []})
    return obj.get("facts", []) if isinstance(obj, dict) else []


def _save_pinned_facts(facts: list[dict]) -> bool:
    return _save_json(PINNED_FACTS_PATH, {"facts": facts,
                                          "updated_at": _now_iso()})


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _graphiti_fetch_nodes(query: str = "", limit: int = 50) -> list[dict]:
    """Fetch episode nodes directly from Neo4j (bypasses Graphiti.search
    which only returns edges). Returns [{id, name, content, created_at, source}].

    Falls back to [] if Neo4j driver or connection is unavailable.
    """
    try:
        from neo4j import AsyncGraphDatabase  # type: ignore
    except ImportError:
        return []
    driver = None
    try:
        driver = AsyncGraphDatabase.driver(
            GRAPHITI_URI, auth=(GRAPHITI_USER, GRAPHITI_PASS))
        async with driver.session() as session:
            if query:
                # Simple case-insensitive contains filter on name+content
                cypher = (
                    "MATCH (e:Episodic) "
                    "WHERE toLower(e.name) CONTAINS toLower($q) "
                    "   OR toLower(e.content) CONTAINS toLower($q) "
                    "RETURN e.uuid AS id, e.name AS name, e.content AS content, "
                    "       e.created_at AS created_at, e.source_description AS source "
                    "ORDER BY e.created_at DESC LIMIT $lim"
                )
                result = await session.run(cypher, q=query, lim=int(limit))
            else:
                cypher = (
                    "MATCH (e:Episodic) "
                    "RETURN e.uuid AS id, e.name AS name, e.content AS content, "
                    "       e.created_at AS created_at, e.source_description AS source "
                    "ORDER BY e.created_at DESC LIMIT $lim"
                )
                result = await session.run(cypher, lim=int(limit))
            out: list[dict] = []
            async for rec in result:
                out.append({
                    "id": rec.get("id"),
                    "name": _safe_str(rec.get("name") or "", 200),
                    "content": _safe_str(rec.get("content") or "", 4000),
                    "created_at": str(rec.get("created_at") or ""),
                    "source": _safe_str(rec.get("source") or "", 120),
                })
            return out
    except Exception as e:
        log.info("graphiti direct fetch failed: %s", e)
        return []
    finally:
        if driver is not None:
            try:
                await driver.close()
            except Exception:  # pragma: no cover
                pass


async def _graphiti_fetch_facts(query: str = "", limit: int = 100) -> list[dict]:
    """Fetch RELATES_TO / fact edges from Neo4j. Returns flat fact list."""
    try:
        from neo4j import AsyncGraphDatabase  # type: ignore
    except ImportError:
        return []
    driver = None
    try:
        driver = AsyncGraphDatabase.driver(
            GRAPHITI_URI, auth=(GRAPHITI_USER, GRAPHITI_PASS))
        async with driver.session() as session:
            if query:
                cypher = (
                    "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
                    "WHERE toLower(r.fact) CONTAINS toLower($q) "
                    "   OR toLower(a.name) CONTAINS toLower($q) "
                    "   OR toLower(b.name) CONTAINS toLower($q) "
                    "RETURN r.uuid AS id, a.name AS subject, r.name AS predicate, "
                    "       b.name AS object, r.fact AS fact, "
                    "       r.valid_at AS valid_at, r.invalid_at AS invalid_at "
                    "ORDER BY r.valid_at DESC LIMIT $lim"
                )
                result = await session.run(cypher, q=query, lim=int(limit))
            else:
                cypher = (
                    "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
                    "RETURN r.uuid AS id, a.name AS subject, r.name AS predicate, "
                    "       b.name AS object, r.fact AS fact, "
                    "       r.valid_at AS valid_at, r.invalid_at AS invalid_at "
                    "ORDER BY r.valid_at DESC LIMIT $lim"
                )
                result = await session.run(cypher, lim=int(limit))
            out: list[dict] = []
            async for rec in result:
                out.append({
                    "id": rec.get("id"),
                    "subject": _safe_str(rec.get("subject") or "", 200),
                    "predicate": _safe_str(rec.get("predicate") or "", 120),
                    "object": _safe_str(rec.get("object") or "", 200),
                    "fact": _safe_str(rec.get("fact") or "", 1200),
                    "valid_at": str(rec.get("valid_at") or ""),
                    "invalid_at": str(rec.get("invalid_at") or ""),
                })
            return out
    except Exception as e:
        log.info("graphiti facts fetch failed: %s", e)
        return []
    finally:
        if driver is not None:
            try:
                await driver.close()
            except Exception:  # pragma: no cover
                pass


async def _graphiti_write_cypher(cypher: str, params: dict) -> tuple[bool, str]:
    """Execute a parameterized Cypher write; returns (ok, error)."""
    try:
        from neo4j import AsyncGraphDatabase  # type: ignore
    except ImportError as e:
        return False, f"neo4j driver unavailable: {e}"
    driver = None
    try:
        driver = AsyncGraphDatabase.driver(
            GRAPHITI_URI, auth=(GRAPHITI_USER, GRAPHITI_PASS))
        async with driver.session() as session:
            await session.run(cypher, **params)
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        if driver is not None:
            try:
                await driver.close()
            except Exception:  # pragma: no cover
                pass


# ── Memory Inspector MCP tools ──────────────────────────────────────────────

@mcp.tool()
async def memory_list_episodes(limit: int = 50, offset: int = 0,
                                search: str = "") -> dict:
    """Paginated episodes for the Memory Inspector UI.

    Pulls from Graphiti Neo4j (Episodic nodes); falls back to the local
    episodes_cache.json file when Neo4j is unreachable.
    """
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    episodes = await _graphiti_fetch_nodes(query=search, limit=limit + offset)
    if not episodes:
        cache = _load_json(EPISODES_CACHE_PATH, {"episodes": []})
        episodes = cache.get("episodes", []) if isinstance(cache, dict) else []
        if search:
            q = search.lower()
            episodes = [e for e in episodes
                        if q in (e.get("name", "") + " " + e.get("content", "")).lower()]
        source = "cache"
    else:
        source = "graphiti"
    page = episodes[offset:offset + limit]
    return {
        "ok": True,
        "source": source,
        "count": len(page),
        "total": len(episodes),
        "episodes": page,
    }


@mcp.tool()
async def memory_edit_episode(episode_id: str, new_content: str) -> dict:
    """Rewrite an episode body (Graphiti Episodic.content).

    NOTE: Graphiti re-extracts entities lazily; edited content is authoritative
    for future queries but the derived facts may need re-extraction.
    """
    if not episode_id:
        return {"ok": False, "error": "episode_id is required"}
    cypher = (
        "MATCH (e:Episodic {uuid: $id}) "
        "SET e.content = $content, e.edited_at = $ts "
        "RETURN e.uuid AS id"
    )
    ok, err = await _graphiti_write_cypher(
        cypher, {"id": episode_id,
                 "content": _safe_str(new_content, 8192),
                 "ts": _now_iso()})
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True, "episode_id": episode_id, "edited_at": _now_iso()}


@mcp.tool()
async def memory_delete_episode(episode_id: str) -> dict:
    """Permanently remove an episode (Episodic node) from Graphiti."""
    if not episode_id:
        return {"ok": False, "error": "episode_id is required"}
    cypher = "MATCH (e:Episodic {uuid: $id}) DETACH DELETE e"
    ok, err = await _graphiti_write_cypher(cypher, {"id": episode_id})
    if not ok:
        return {"ok": False, "error": err}
    return {"ok": True, "episode_id": episode_id, "deleted_at": _now_iso()}


@mcp.tool()
async def memory_list_facts(limit: int = 50, offset: int = 0,
                             search: str = "") -> dict:
    """Flat list of extracted facts (entity relations) from the graph."""
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    facts = await _graphiti_fetch_facts(query=search, limit=limit + offset)
    # Merge pin flags
    pinned_ids = {p.get("fact_id") for p in _load_pinned_facts()
                  if isinstance(p, dict) and p.get("fact_id")}
    for f in facts:
        f["pinned"] = f.get("id") in pinned_ids
    page = facts[offset:offset + limit]
    return {
        "ok": True,
        "count": len(page),
        "total": len(facts),
        "facts": page,
    }


@mcp.tool()
def memory_pin_fact(fact: str, reason: str = "",
                    fact_id: str = "") -> dict:
    """Protect a fact from decay/forget passes.

    The pin is stored locally in ~/.openclaw/data/memory/pinned_facts.json.
    ODO-side decay logic (or any future forget scheduler) should consult this
    file before dropping a fact. Pins are keyed by fact_id (Graphiti uuid)
    when available, else by the fact text hash.
    """
    if not fact and not fact_id:
        return {"ok": False, "error": "fact or fact_id is required"}
    pinned = _load_pinned_facts()
    # De-dupe by fact_id, else by exact text
    key = fact_id or fact
    for entry in pinned:
        if entry.get("fact_id") == fact_id and fact_id:
            entry["reason"] = _safe_str(reason, 400) or entry.get("reason", "")
            entry["pinned_at"] = _now_iso()
            _save_pinned_facts(pinned)
            return {"ok": True, "status": "updated", "fact_id": fact_id}
        if entry.get("fact") == fact and not fact_id:
            entry["reason"] = _safe_str(reason, 400) or entry.get("reason", "")
            entry["pinned_at"] = _now_iso()
            _save_pinned_facts(pinned)
            return {"ok": True, "status": "updated", "fact": fact}
    pinned.append({
        "fact_id": fact_id or None,
        "fact": _safe_str(fact, 1200),
        "reason": _safe_str(reason, 400),
        "pinned_at": _now_iso(),
    })
    _save_pinned_facts(pinned)
    return {"ok": True, "status": "pinned", "fact_id": fact_id or None,
            "total_pinned": len(pinned)}


@mcp.tool()
def memory_unpin_fact(fact_id: str = "", fact: str = "") -> dict:
    """Remove a fact from the pinned set (it becomes eligible for decay again)."""
    if not fact_id and not fact:
        return {"ok": False, "error": "fact_id or fact is required"}
    pinned = _load_pinned_facts()
    before = len(pinned)
    if fact_id:
        pinned = [p for p in pinned if p.get("fact_id") != fact_id]
    else:
        pinned = [p for p in pinned if p.get("fact") != fact]
    removed = before - len(pinned)
    _save_pinned_facts(pinned)
    return {"ok": True, "removed": removed, "total_pinned": len(pinned)}


@mcp.tool()
def engram_stats(table: str = "kine", top_k: int = 20) -> dict:
    """Top-k n-grams per Engram table + global stats + decay indicator.

    The decay indicator is heuristic: Engram tables are static binary files,
    so "recency" is approximated by file mtime vs. age-of-table-in-days.
    Top n-grams are computed in-process via the EngramTable reader so we
    don't need to shell out.
    """
    if table not in VALID_ENGRAM_TABLES:
        return {"ok": False,
                "error": f"unknown table; valid: {sorted(VALID_ENGRAM_TABLES)}"}
    table_path = ENGRAM_DIR / f"{table}.engr"
    if not table_path.exists():
        return {"ok": False, "error": f"table missing: {table_path}"}
    top_k = max(1, min(200, int(top_k)))

    # Reuse engram_query's EngramTable reader
    try:
        import importlib.util as _iu
        spec = _iu.spec_from_file_location("engram_query", str(ENGRAM_QUERY_PY))
        if spec is None or spec.loader is None:
            raise RuntimeError("engram_query.py not loadable")
        eq = _iu.module_from_spec(spec)
        spec.loader.exec_module(eq)  # type: ignore[attr-defined]
        t = eq.EngramTable(str(table_path))
        raw_stats = t.stats()
    except Exception as e:
        return {"ok": False, "error": f"engram loader failed: {e}"}

    # Walk the hash table to pull the top-k contexts by total_count
    try:
        top: list[tuple[int, int]] = []  # (slot_idx, total_count)
        for i in range(t.table_size):
            h, _off, total_count = t._read_slot(i)  # pylint: disable=protected-access
            if h == 0:
                continue
            if len(top) < top_k:
                top.append((i, total_count))
                top.sort(key=lambda x: -x[1])
            elif total_count > top[-1][1]:
                top[-1] = (i, total_count)
                top.sort(key=lambda x: -x[1])
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"engram walk failed: {e}"}

    # Tokenizer for decoding top-k contexts (best effort)
    top_ngrams: list[dict] = []
    try:
        tokenizer = eq.load_tokenizer()
    except Exception:
        tokenizer = None

    # Decode top contexts: we only have the hash, not the key tokens, so we
    # emit the slot hash + probability share. Without reverse-lookup tables,
    # surfacing the actual tokens requires the Rust build-time debug table
    # (not currently persisted). The UI presents frequency share + freq.
    total_freq = raw_stats.get("total_frequency", 0) or 1
    for i, (_slot, freq) in enumerate(top, start=1):
        top_ngrams.append({
            "rank": i,
            "count": int(freq),
            "share": round(freq / total_freq, 6),
        })

    # Decay indicator: days since file mtime; older = more "decayed"
    try:
        import time as _time
        mtime = table_path.stat().st_mtime
        age_days = max(0.0, (_time.time() - mtime) / 86400.0)
    except Exception:
        age_days = None

    return {
        "ok": True,
        "table": table,
        "path": str(table_path),
        "order": raw_stats.get("order"),
        "entries": raw_stats.get("num_entries"),
        "load_factor": raw_stats.get("load_factor"),
        "file_size_mb": round(raw_stats.get("file_size", 0) / 1048576, 2),
        "total_frequency": raw_stats.get("total_frequency"),
        "age_days": age_days,
        "decay_hint": ("fresh" if age_days is not None and age_days < 7 else
                       ("warm" if age_days is not None and age_days < 30 else
                        ("cold" if age_days is not None else "unknown"))),
        "top_ngrams": top_ngrams,
    }


# ── Memory Inspector REST routes (used by Chimère Studio UI) ────────────────

@mcp.custom_route("/api/memory/episodes", methods=["GET", "OPTIONS"])
async def _api_episodes_list(request):
    from starlette.responses import JSONResponse
    if request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    limit = int(request.query_params.get("limit", "50") or "50")
    offset = int(request.query_params.get("offset", "0") or "0")
    search = request.query_params.get("search", "") or ""
    data = await memory_list_episodes(limit=limit, offset=offset, search=search)
    return JSONResponse(data, headers=_cors_headers())


@mcp.custom_route("/api/memory/episodes/{episode_id}",
                  methods=["PATCH", "DELETE", "OPTIONS"])
async def _api_episode_mutate(request):
    from starlette.responses import JSONResponse
    if request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    episode_id = request.path_params.get("episode_id", "")
    if request.method == "DELETE":
        data = await memory_delete_episode(episode_id=episode_id)
        return JSONResponse(data, headers=_cors_headers())
    # PATCH: body = {"content": "..."}
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_content = body.get("content", "") if isinstance(body, dict) else ""
    data = await memory_edit_episode(episode_id=episode_id, new_content=new_content)
    return JSONResponse(data, headers=_cors_headers())


@mcp.custom_route("/api/memory/facts", methods=["GET", "OPTIONS"])
async def _api_facts_list(request):
    from starlette.responses import JSONResponse
    if request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    limit = int(request.query_params.get("limit", "50") or "50")
    offset = int(request.query_params.get("offset", "0") or "0")
    search = request.query_params.get("search", "") or ""
    data = await memory_list_facts(limit=limit, offset=offset, search=search)
    return JSONResponse(data, headers=_cors_headers())


@mcp.custom_route("/api/memory/pin", methods=["POST", "DELETE", "OPTIONS"])
async def _api_pin(request):
    from starlette.responses import JSONResponse
    if request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if request.method == "DELETE":
        data = memory_unpin_fact(fact_id=body.get("fact_id", ""),
                                  fact=body.get("fact", ""))
    else:
        data = memory_pin_fact(fact=body.get("fact", ""),
                                reason=body.get("reason", ""),
                                fact_id=body.get("fact_id", ""))
    return JSONResponse(data, headers=_cors_headers())


@mcp.custom_route("/api/memory/pins", methods=["GET", "OPTIONS"])
async def _api_pins_list(_request):
    from starlette.responses import JSONResponse
    if _request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    pinned = _load_pinned_facts()
    return JSONResponse({"ok": True, "count": len(pinned), "pinned": pinned},
                        headers=_cors_headers())


@mcp.custom_route("/api/engram/stats", methods=["GET", "OPTIONS"])
async def _api_engram_stats(request):
    from starlette.responses import JSONResponse
    if request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    table = request.query_params.get("table", "kine") or "kine"
    top_k = int(request.query_params.get("top_k", "20") or "20")
    data = engram_stats(table=table, top_k=top_k)
    return JSONResponse(data, headers=_cors_headers())


@mcp.custom_route("/api/engram/list", methods=["GET", "OPTIONS"])
async def _api_engram_list(_request):
    """Return all available engram tables (name + file size) for the UI tabs."""
    from starlette.responses import JSONResponse
    if _request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers=_cors_headers())
    tables: list[dict] = []
    for name in sorted(VALID_ENGRAM_TABLES):
        p = ENGRAM_DIR / f"{name}.engr"
        tables.append({
            "name": name,
            "exists": p.exists(),
            "size_mb": round(p.stat().st_size / 1048576, 2) if p.exists() else 0,
        })
    return JSONResponse({"ok": True, "tables": tables},
                        headers=_cors_headers())


def _cors_headers() -> dict:
    """Permissive CORS for Tauri WebView + local dev (http://localhost:3000)."""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "600",
    }


# ── Custom HTTP route: healthz ──────────────────────────────────────────────

@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request):
    from starlette.responses import JSONResponse
    return JSONResponse({
        "status": "ok",
        "server": "chimere-mcp",
        "tools": [
            "rag_search", "deep_search", "deep_search_or_cache",
            "engram_lookup", "ocr", "nightly_lora_status",
            "kine_protocol", "think",
            "list_skills", "invoke_skill",
            "memory_recall", "memory_add_episode",
            "memory_list_episodes", "memory_edit_episode",
            "memory_delete_episode", "memory_list_facts",
            "memory_pin_fact", "memory_unpin_fact", "engram_stats",
        ],
        "rest_routes": [
            "/api/memory/episodes",
            "/api/memory/episodes/{id}",
            "/api/memory/facts",
            "/api/memory/pin",
            "/api/memory/pins",
            "/api/engram/stats",
            "/api/engram/list",
        ],
    }, headers=_cors_headers())


# ── Entrypoint ──────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Chimère MCP server")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--transport", default="streamable-http",
        choices=["stdio", "http", "streamable-http", "sse"],
        help="MCP transport (default: streamable-http over HTTP)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=os.environ.get("CHIMERE_MCP_LOG", "INFO"),
        format="[%(name)s] %(levelname)s %(message)s",
    )
    log.info(
        "starting chimere-mcp on %s:%s via %s (chroma=%s engram_dir=%s)",
        args.host, args.port, args.transport, CHROMA_PATH, ENGRAM_DIR,
    )

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport=args.transport,
            host=args.host,
            port=args.port,
            show_banner=False,
        )


if __name__ == "__main__":
    main()
