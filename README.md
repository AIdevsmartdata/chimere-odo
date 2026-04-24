# Chimère ODO

**Unified inference orchestrator for local-first LLM deployments.**
Intent classification, adaptive compute routing, quality-gated
self-improvement, and SOTA 8-step web search — powered by the
[Chimère](https://github.com/AIdevsmartdata/chimere) Rust runtime.

ODO sits between user requests and a local llama-server (chimere-server or
vanilla), intelligently routing, enriching, and quality-gating every
interaction.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![Backend: chimere-server](https://img.shields.io/badge/Backend-chimere--server-green.svg)](https://github.com/AIdevsmartdata/chimere)

## The Chimère family

| Repo | Role |
|---|---|
| [`chimere`](https://github.com/AIdevsmartdata/chimere) | Rust inference runtime, 94 tok/s on 16 GB consumer GPU |
| **`chimere-odo`** (this repo) | Python orchestrator |
| [`chimere-studio`](https://github.com/AIdevsmartdata/chimere-studio) | Tauri 2 desktop UI |
| [`ramp-quant`](https://github.com/AIdevsmartdata/ramp-quant) | RAMP / TQ3 quantisation pipeline |
| Models on HF | <https://huggingface.co/Kevletesteur> |

## Powered by Chimère Distilled

ODO is designed to run on top of [Chimère Distilled](https://huggingface.co/Kevletesteur/Qwen3.5-35B-A3B-Chimere-Distilled-GGUF)
— a Claude Opus 4.6 distillation of Qwen3.5-35B-A3B (35B total, 3.5B active,
hybrid GDN + MoE).

| Metric | Score |
|--------|-------|
| HumanEval | **97 %** |
| BFCL tool-calling | **85 %** (+18 pts vs base) |
| IFEval | **80 %** |
| GGUF size | **15 GB** (fits 16 GB VRAM) |
| Gen tok/s (RTX 5060 Ti) | **~90** |

## Architecture

```
User
  │
  ▼
┌──────────────────────────────────────────────────────┐
│  ODO  (port 8084)                                    │
│  ───────────────────────────────────────────────     │
│  1. Intent classify  (regex → filetype → LLM)        │
│  2. Context enrich   (ChromaDB RAG + web search)     │
│  3. Adaptive route   (think vs no-think, profile)    │
│  4. Forward to chimere-server                        │
│  5. Quality assess + log training pair               │
└──────────────────────────────────────────────────────┘
  │
  ▼
chimere-server (port 8081) — Rust FFI over ik_llama.cpp
  │
  ▼
GGUF model (Qwen3.5 / Nemotron-H / Mamba-2)
```

### Pipeline stages

1. **Intent classification** — 3-strategy cascade (regex → filetype → local LLM)
2. **Context enrichment** — ChromaDB RAG, web search, tool injection,
   SOUL.md consolidation
3. **Adaptive routing** — entropy-based compute profiles (think vs no-think)
4. **Quality assessment** — scoring + nightly LoRA + Engram ingestion
5. **Search pipeline** — 8-stage SOTA (QueryExpand → [ChromaDB + WebSearch]
   parallel → RRF → DeepFetch → Diversity → CRAG → Contradictions → Synthesis)

## Features

- Zero-config intent handling
- YAML pipeline definitions (hot-reloaded)
- Adaptive compute allocation (think / no-think per route)
- Autonomous self-improvement (overnight LoRA + DSPy)
- Engram memory management (semantic few-shot, n-gram logit bias driver)
- DVTS tree search with PRM scoring
- Knowledge ingestion (YouTube, Instagram, GLM-OCR, arXiv)
- MCP server exposing deep-search, RAG, Engram, OCR as tools

## Quick start

### Docker

```bash
docker compose up -d
# ODO on port 8084, llama-server on port 8081
```

### Standalone

```bash
pip install -r requirements.txt
export ODO_BACKEND=http://127.0.0.1:8081
python odo.py
```

## Configuration

All via environment variables:

- `ODO_BACKEND` — llama-server URL (default: `http://127.0.0.1:8081`)
- `ODO_PORT` — ODO listening port (default: `8084`)
- `CHIMERE_HOME` — data directory (default: `~/.chimere`)

Pipeline YAMLs live in `pipelines/` for per-route customisation (code, kine,
cyber, research, default, vision, doc_qa, general).

## Stats + routes

```bash
curl http://127.0.0.1:8084/stats    # request counters, routes, latency
curl http://127.0.0.1:8084/routes   # active routing rules
```

## Related

- **Chimère Studio** — [chimere-studio](https://github.com/AIdevsmartdata/chimere-studio) — native desktop UI pointing at ODO by default
- **Runtime** — [chimere](https://github.com/AIdevsmartdata/chimere) — Rust FFI over ik_llama.cpp
- **Models** — [Chimère Distilled GGUF](https://huggingface.co/Kevletesteur/Qwen3.5-35B-A3B-Chimere-Distilled-GGUF) — 15 GB, fits 16 GB VRAM

## License

Apache 2.0 — Kevin Rémondière.

---

**Part of the Chimère local-first stack.**
Everything runs on your machine. Your corpus, your vocabulary, your GPU.
