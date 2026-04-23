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

Transport: streamable-http on port 9095 (MCP endpoint: /mcp/).
A /healthz custom route is added for systemd/monitoring probes.

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
        ],
    })


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
