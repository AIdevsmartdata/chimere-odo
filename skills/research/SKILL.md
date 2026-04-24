---
name: research
description: Deep web research with structured report — from quick 30s summary to ultra-exhaustive 50min analysis. SearXNG + Brave + ChromaDB + academic sources.
trigger_patterns:
  - "research"
  - "recherche"
  - "rapport sur"
  - "étudie"
  - "fais une synthèse"
  - "documente"
tools_required:
  - exec
  - search_router
  - chromadb_rag
  - llm_qwen35
examples:
  - "impact de l'IA sur l'emploi en 2025"
  - "--deep Qwen3.5 vs Llama4 benchmark 2026"
  - "--marathon lombalgie chronique traitements evidence-based"
  - "--ultra géopolitique de l'IA semiconducteurs 2025-2030"
  - "--domain medical --deep TENS lombalgie chronique"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/research_wrapper.sh '$args'"
  timeout_ms: 3600000
  arg_mode: raw
---

# research — Deep Web Research

Recherche web structurée avec rapport. Du mode rapide au mode ultra-exhaustif.

Backend: Qwen3.5-35B Q4_K_M + SearXNG + Brave + ChromaDB + trafilatura/offpunk.

## Modes

```
<question>                    # Standard (~3-5 min, rapport complet)
--deep <question>             # Deep (3 itérations, ~2 min, rapport structuré)
--marathon <question>         # Marathon (8 itérations, ~15 min, comme Claude App)
--ultra <question>            # Ultra (15 itérations, ~30-50 min, exhaustif)
```

## Options

```
--domain medical <question>   # Domaine médical (grade HAS, RAG kiné)
--domain code <question>      # Domaine technique
```

## Mode Comparison

| Mode | Iterations | Duration | Sources | Report |
|------|-----------|----------|---------|--------|
| standard | 1 passe | ~3-5 min | 3-8 | Synthèse + biblio |
| --deep | 3 | ~2 min | 5-15 | .md structuré |
| --marathon | 8 | ~15 min | 20-50 | .md complet avec sections |
| --ultra | 15 | ~30-50 min | 50-100 | .md exhaustif multi-sections |

## Pipeline

1. **Planning** — Décomposition en sous-questions + axes de recherche
2. **Iterative Search** — SearXNG + Brave + académique + ChromaDB
3. **Reflection** — Analyse des lacunes, reformulation des queries
4. **Report** — .md structuré avec TOC, abstract, sections, citations, références

## Output Structure

```
# Title
## Abstract
## Table of Contents
## Sections (per sub-question)
  ### Key Findings
  ### Evidence
  ### Sources
## Conclusion
## Bibliography
```
