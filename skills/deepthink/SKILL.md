---
name: deepthink
description: Deep reflection with web search — Brave/Perplexica pipeline fed into Qwen3.5-35B thinking mode for grounded, well-reasoned answers.
trigger_patterns:
  - "réfléchis"
  - "analyse approfondie"
  - "deep think"
  - "pense longuement"
  - "explique en détail"
tools_required:
  - exec
  - search_router
  - llm_qwen35
examples:
  - "Pourquoi la fusion froide est-elle rejetée par la communauté scientifique ?"
  - "Comment fonctionne réellement la mémoire Engram dans Chimère ?"
  - "Quels sont les trade-offs de Mamba-2 vs Transformer pour l'inférence mobile ?"
model: qwen3.5-35b-a3b
execution:
  command: "/home/remondiere/.openclaw/bin/think.sh '$args'"
  timeout_ms: 300000
  arg_mode: raw
---

# deepthink — Deep Reflection with Web Search

Pipeline : recherche web (Brave/Perplexica via search_router) → inférence Qwen3.5-35B-A3B (thinking natif).

## Process

1. **Web Search** — Brave/Perplexica (search_router) pour contextes factuels à jour
2. **Context Injection** — Top résultats injectés dans le prompt système
3. **Thinking Inference** — Qwen3.5-35B-A3B avec `enable_thinking=true`
4. **Grounded Answer** — Réponse synthétisée avec citations implicites

## When to use

- Questions requérant des faits récents (post-training cutoff Qwen3.5)
- Sujets techniques profonds (trade-offs, comparaisons, historique)
- Analyses demandant structuration longue
- Questions où le modèle pourrait halluciner sans grounding

## Performance

- Temps total : 30-120s selon profondeur recherche
- Timeout : 300s
