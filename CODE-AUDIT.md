# CODE-AUDIT.md -- Chimere ODO Public Repository Audit

**Date**: 2026-04-01
**Repo**: `~/github-repos/chimere-odo/`
**Auditor**: Claude Opus 4.6 (automated)
**Scope**: Python code quality, security, documentation, dependencies, integration, tests

---

## Executive Summary

The repo is a substantial (~5000 LoC) inference orchestrator with intent classification, adaptive routing, quality gating, search pipelines, and self-improvement. Code quality is generally good -- clean separation of concerns, consistent patterns, thorough docstrings. However, there are **security issues to fix before public visibility** and several code quality improvements.

**Critical findings**: 4 | **Warnings**: 8 | **Suggestions**: 10

---

## 1. Security

### CRITICAL: Hardcoded `~/.openclaw/` paths leak private system layout

Four pipeline YAMLs reference `~/.openclaw/` paths, which is the private OpenClaw installation directory. These paths do not exist in the public `~/.chimere/` namespace and leak internal system details.

| File | Line | Path |
|------|------|------|
| `odo/pipelines/code.yaml` | 4 | `~/.openclaw/data/engram/code.engr` |
| `odo/pipelines/kine.yaml` | 43 | `~/.openclaw/data/engram/kine.engr` |
| `odo/pipelines/cyber.yaml` | 4 | `~/.openclaw/data/engram/cyber.engr` |
| `odo/pipelines/research.yaml` | 36 | `~/.openclaw/data/engram/general.engr` |

**Fix**: Replace all `~/.openclaw/` with `~/.chimere/` in pipeline YAMLs. The code already uses `CHIMERE_HOME` everywhere else, so the YAMLs are inconsistent.

### CRITICAL: `knowledge/ingest_pipeline.py` hardcodes local paths

Lines 31-37 hardcode paths to personal infrastructure:
- `~/.chimere/workspaces/main/knowledge`
- `~/.chimere/bin/whisper_gpu.sh`
- `~/.chimere/venvs/pipeline/bin/python`
- `~/.chimere/cookies/youtube.txt`

These should use `CHIMERE_HOME` env var consistently, and the cookies path should be documented as user-provided (not committed).

### CRITICAL: `brave_search.py` loads API key from `~/.chimere/.env` at import time

Line 41: `_load_env()` is called at module import, reading `.env` from disk. While `.env` is gitignored, the mechanism reads arbitrary environment files. The `BRAVE_API_KEY` is properly fetched via `os.environ.get()` (not hardcoded), so this is safe -- but the `.env` loading should be documented.

### WARNING: `odo.py` listens on `0.0.0.0` by default

Line 63: `LISTEN_ADDR = os.environ.get("ODO_LISTEN", "0.0.0.0")` -- the default binds to all interfaces. For a local inference proxy, this should default to `127.0.0.1`. The Docker Dockerfile already expects `0.0.0.0`, but bare-metal deployments are exposed.

### WARNING: `soul_improver.py` imports `judge_lib` which is not in this repo

Line 38-41 of `quality/soul_improver.py` imports from `judge_lib` which references `CHIMERE_HOME`, `AGENTS_DIR`, `JUDGE_DIR`, `CLAUDE_TIMEOUT`, etc. This module does not exist in the repository and will cause ImportError.

### OK: No hardcoded secrets, API keys, tokens, or patient data found

- Brave API key is loaded from env var only
- No passwords, OAuth tokens, or personal data in committed files
- `.gitignore` properly excludes `.env`, `.env.*`, `credentials/`, `*.db`, `data/`, `logs/`
- SOUL.md contains only generic system prompt

---

## 2. Python Code Quality

### `odo/odo.py` (~1600 lines) -- Core orchestrator

**Issues:**
- **`datetime.utcnow()` is deprecated** (line 584). Use `datetime.now(timezone.utc)` instead. Python 3.12+ emits a DeprecationWarning.
- **`import math` is used** but only for `math.log` and `math.exp` in ABF/entropy -- consider these are appropriately used.
- **`import sqlite3` is a top-level import** but the DB is only used for logging. Could be lazy-loaded but this is minor.
- **`CODE_RE` (line 220) is duplicated from classifier.py**. The classifier already handles routing; having a separate regex in odo.py for `is_code_request()` creates drift risk.
- **`GREETING_RE` (line 239) is duplicated** across odo.py, entropy_router.py, and classifier.py. Should be a shared constant.

### `odo/enricher.py` -- Context enrichment

**Issues:**
- **`import re` inside `_is_boilerplate()` and inside `build_dynamic_engram()`** in `dynamic_engram.py` (lines 76, 118, 147). These should be top-level imports, not inside functions called repeatedly.
- **`sys.path.insert(0, str(BIN))`** is called in multiple places (lines 211, 325) to import `engram_query`. This is fragile and path-dependent.

### `odo/classifier.py` -- Intent classification

**Clean code.** Well-documented 3-tier cascade (regex -> filetype -> LLM). No issues found.

### `search/brave_search.py` -- Brave API wrapper

**Issue:**
- **Duplicate `global _last_request_time`** declarations (lines 57 and 77). The first `global` on line 57 is at function scope before the `with` block, then repeated inside the `with` block. Python handles this correctly but it is confusing. The outer `global` is unnecessary since the inner one covers it within the `with` context.

### `search/web_deep_fetch.py` -- Content extraction

**Issue:**
- **`_estimate_tokens()` function is defined but never called.** Lines 168-170 define a token estimation function that is unused -- `chunk_text()` uses `chars_per_token = 4` directly instead. Dead code.

### `odo/orchestrator.py` -- Legacy orchestrator (port 8085)

**WARNING: This file appears to be dead code.** The unified `odo/odo.py` (port 8084) has absorbed all orchestrator functionality. The `orchestrator.py` still references the old 3-hop architecture (`Client -> ODO (8085) -> think_router (8084) -> chimere/llama (8081)`). The Dockerfile runs `odo/odo.py`, not `orchestrator.py`. Should be removed or clearly marked as deprecated.

### `think_router.py` (root) -- Legacy think router (~1000 lines)

**WARNING: Also appears to be dead code.** The unified `odo.py` absorbs all think_router functionality (ABF, CGRS, FORCE_THINK, sampling profiles, system consolidation). The Dockerfile copies it but runs `odo/odo.py`. Should be removed or marked as deprecated/reference.

### `quality/soul_improver.py` -- Missing dependency

Imports `from judge_lib import (...)` on line 38. `judge_lib.py` does not exist in this repository. This file will not run standalone. Either include `judge_lib.py` or remove `soul_improver.py` from the public repo.

---

## 3. Documentation

### README.md -- Generally good, minor issues

**Good:**
- Clear architecture diagram
- Docker quick start
- Environment variable documentation
- Links to related projects (chimere, Chimere Distilled GGUF)

**Issues:**
- **No `docker-compose.yml` in repo** despite `docker compose up -d` instructions. Either add the compose file or change the instructions to use `docker run`.
- **Missing Python version requirement** -- code uses `list[str]` (PEP 604), `tuple[str | None, float]` syntax requiring Python 3.10+. Should state `Python >= 3.10` in README.
- **`pip install -r requirements.txt`** instructions refer to `odo.py` directly, but the entrypoint is `odo/odo.py`. The standalone command should be `python odo/odo.py`.
- **No mention of optional heavy deps** (faiss-cpu, sentence-transformers) which are needed for semantic few-shot and neural reranking.

---

## 4. Dependencies

### `requirements.txt` -- Minimal, reasonable

```
pyyaml>=6.0
requests>=2.31
trafilatura>=1.8
chromadb-client>=0.5
tokenizers>=0.20
numpy>=1.26
```

**Issues:**
- **Upper bounds missing.** `>=` only pins minimums. A `trafilatura>=1.8,<2.0` style pin would prevent breaking changes. Acceptable for a non-library project but risky for Docker reproducibility.
- **`chromadb-client>=0.5`**: The actual ChromaDB API changed significantly between 0.5 and 0.6. Should pin more tightly.
- **Missing optional deps documentation**: `faiss-cpu`, `sentence-transformers`, `torch`, `pymupdf`, `offpunk` are all used conditionally but not listed even as optional.
- **Missing `pyproject.toml`**: No modern Python packaging. Not required for a service, but a `pyproject.toml` with `[project.optional-dependencies]` would be cleaner.
- **`quality/` deps not listed**: `lora_mezo_nightly.py` imports `torch`, `transformers`, `peft` at top level (will crash on import if not installed). `grpo_nightly.py` references `trl`. These should be clearly separated as optional heavy deps.

---

## 5. DVTS Integration

`odo/dvts.py` is **properly integrated** and well-structured:

- Called from `odo.py` line 1046 when pipeline has `dvts.enabled: true`
- Generates K candidates sequentially (respects `np=1` GPU constraint)
- Scores candidates with ThinkPRM (parallel, CPU)
- Falls back to heuristic scoring when ThinkPRM unavailable
- `FORCE_THINK` guard prevents infinite retry loop (line 209: enforces min 8192 tokens)
- Entropy router can dynamically enable DVTS for high-entropy queries (line 884)

**One issue:** The `CHIMERE_URL` env var (line 32) differs from `CHIMERE_BACKEND` used elsewhere. The main odo.py uses `ODO_BACKEND`. This inconsistency means DVTS could target a different backend than the main proxy.

---

## 6. Search Pipeline

All search components are present and functional:

| Module | Status | Notes |
|--------|--------|-------|
| `search/search_router.py` | Present | Full SQLite cache, budget management, CRAG |
| `search/deep_search_sota.py` | Present | 7-stage pipeline, query expansion, RRF fusion |
| `search/brave_search.py` | Present | Rate limiting, file cache, thread-safe |
| `search/searxng_search.py` | Present | Dual backend (direct + Perplexica), file cache |
| `search/perplexica_search.py` | Present | Provider config for local Qwen3.5 |
| `search/web_deep_fetch.py` | Present | Trafilatura + offpunk fallback, PDF support, chunking |

**Issues:**
- `search_router.py` line 41 does `sys.path.insert(0, str(CHIMERE_HOME / "bin"))` to find backends. In Docker, the backends are under `/app/search/`, not `bin/`. This may break in Docker context (though the Dockerfile structure should work since the `search/` dir is copied alongside).
- `deep_search_sota.py` line 42 points `LLAMA_URL` to port 8084 (ODO itself), which is correct for the rewrite but creates a self-loop if ODO calls deep_search which calls ODO. The enricher runs this as a subprocess, so it is a separate HTTP connection, not recursive -- but worth noting.

---

## 7. Engram

The engram subsystem is present and clean:

| Module | Status |
|--------|--------|
| `engram/engram_ingest.py` | Binary .engr builder, FNV-1a hash, matches Rust format |
| `engram/engram_query.py` | Query tool, stats mode, tokenizer integration |
| `engram/engram_write_nightly.py` | Nightly update from quality-gated responses |
| `engram/engram_semantic.py` | FAISS semantic tier 2 |
| `odo/dynamic_engram.py` | Query-time engram from web search results |

**Issues:**
- **`engram/` directory not included in Dockerfile COPY**. The Dockerfile copies `engram/` but the glob `engram/*` returned no files initially -- this may be a directory traversal issue or the files are in a subdirectory.
  - Actually, files exist (confirmed by grep/read). The Dockerfile line `COPY engram/ engram/` is correct.
- **Hardcoded paths in pipeline YAMLs** (see Security section) -- engram table paths reference `~/.openclaw/` instead of `~/.chimere/`.
- **`TOKENIZER_PATH`** in `dynamic_engram.py` (line 36) hardcodes `models/Qwen3.5-35B-A3B-BF16/tokenizer.json`. This should be configurable or documented as a required file.

---

## 8. Tests

**No tests exist in this repository.**

There are self-test blocks (`if __name__ == "__main__"`) in several modules, which is useful for manual testing:
- `classifier.py` -- CLI test
- `dvts.py` -- self-test with query
- `entropy_router.py` -- built-in test cases
- `semantic_fewshot.py` -- warmup + query test
- `confidence_rag_trigger.py` -- probe test queries

**Recommended test coverage (priority order):**
1. `classifier.py` -- Unit tests for regex matching, route normalization, edge cases (empty string, Unicode, very long input). This is the routing foundation.
2. `odo.py` -- Integration tests for `sanitize_messages()`, `extract_user_text()`, `apply_pipeline()`, `_decide_thinking()`.
3. `enricher.py` -- Unit tests for `detect_csv()`, `detect_ioc()`, `needs_web_search()`, `needs_deep_research()`.
4. `quality_gate.py` -- Unit tests for `_extract_steps()`, `_extract_step_labels()`, `_extract_prefix_score()`, `_v2_to_v1()`.
5. `entropy_router.py` -- Unit tests for `_query_complexity()` with edge cases.
6. `engram_ingest.py` / `engram_query.py` -- Round-trip test (ingest -> query -> verify).

---

## 9. Linting (ruff)

Ruff could not be run due to shell permission restrictions. Based on manual review:

**Likely ruff findings:**
- `import re` inside functions in `dynamic_engram.py` (lines 76, 147) and `dvts.py` (line 118) -- E402/module-level-import
- Duplicate `global _last_request_time` in `brave_search.py` -- cosmetic
- `datetime.utcnow()` deprecation in `odo.py` line 584
- Unused variable `target_chars` in `web_deep_fetch.py` line 191
- Unused function `_estimate_tokens` in `web_deep_fetch.py` line 168

---

## 10. Additional Findings

### `__pycache__/` directories present on disk

24 `.pyc` files exist in `__pycache__/` directories across the repo. The `.gitignore` excludes them, so they should not be committed. Verify with `git ls-files --cached '*.pyc'` that none are tracked.

### `orchestrator.py` and `think_router.py` are dead code

Both are superseded by the unified `odo.py`. They add ~1500 lines of unneeded code. Either:
- Remove them entirely (preferred for a public repo)
- Move to a `legacy/` directory with a note

### Missing `docker-compose.yml`

The README references `docker compose up -d` but no compose file exists. Either add one or update the documentation.

### Potential race condition in quality gate

`quality_gate.py` line 153: `QUALITY_LOG.parent.mkdir(parents=True, exist_ok=True)` is called inside the scoring thread. If two threads score simultaneously and the directory does not exist, both may race on `mkdir`. The `exist_ok=True` handles this safely, so this is a non-issue in practice.

### `odo.py` is too large

At ~1600 lines, `odo.py` is doing too much. Consider extracting:
- ABF monitoring logic (~150 lines) into `abf.py`
- Stream/buffer response handling (~150 lines) into `response_handler.py`
- The `_decide_thinking()` cascade (~80 lines) into `think_decider.py`

---

## Summary of Action Items

### Must fix (security/correctness):
1. Replace `~/.openclaw/` with `~/.chimere/` in all 4 pipeline YAMLs
2. Make `knowledge/ingest_pipeline.py` paths use `CHIMERE_HOME` consistently
3. Change `ODO_LISTEN` default from `0.0.0.0` to `127.0.0.1` in `odo.py`
4. Either include `judge_lib.py` or remove `quality/soul_improver.py`

### Should fix (code quality):
5. Remove or deprecate `orchestrator.py` and root `think_router.py`
6. Fix `datetime.utcnow()` deprecation in `odo.py`
7. Remove dead `_estimate_tokens()` from `web_deep_fetch.py`
8. Move `import re` to top-level in `dynamic_engram.py` and `dvts.py`
9. Add `docker-compose.yml` or fix README instructions
10. Document Python >= 3.10 requirement
11. Unify env var names: `CHIMERE_BACKEND` vs `ODO_BACKEND` vs `CHIMERE_URL`
12. Extract shared regex constants (GREETING_RE, CODE_RE) into a shared module

### Nice to have:
13. Add basic unit tests for classifier, enricher detection, quality gate helpers
14. Add `pyproject.toml` with optional-dependencies sections
15. Pin dependency upper bounds in `requirements.txt`
16. Refactor `odo.py` into smaller modules (ABF, response handling, think decision)
