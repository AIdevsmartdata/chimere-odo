---
name: debate
description: Multi-perspective reasoning (advocate/critic/synthesizer). Three modes - general, --code for architecture decisions, --medical for EBM clinical analysis.
trigger_patterns:
  - "débat"
  - "pour ou contre"
  - "faut-il"
  - "vs"
  - "arguments"
  - "trade-offs"
tools_required:
  - exec
  - llm_qwen35
examples:
  - "Faut-il courir un marathon sans entrainement ?"
  - "--code Should we use PostgreSQL or MongoDB for user session storage?"
  - "--code REST vs GraphQL for a mobile-first API with 50+ clients?"
  - "--medical Le TENS est-il indiqué pour la lombalgie chronique ?"
  - "--medical Faut-il prescrire de la vitamine D systématiquement en hiver ?"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/debate_wrapper.sh '$args'"
  timeout_ms: 300000
  arg_mode: raw
---

# debate — Multi-perspective Reasoning

Débat structuré en 3 passes séquentielles sur Qwen3.5-35B. Trois modes disponibles.

## Modes

### General (default)

Trois passes en français :
1. **Avocat** — argumente EN FAVEUR (~15s, no-think)
2. **Critique** — trouve les failles (~30s, thinking)
3. **Synthétiseur** — réconcilie, produit la réponse finale (~40s, thinking)

### --code — Architecture Decision

Trois passes en anglais, optimisées pour la prise de décision technique :
1. **Software Architect** — proposes solution with stack rationale, scalability plan, testing strategy (no-think, temp 0.6)
2. **Senior Code Reviewer** — critiques: security risks, performance bottlenecks, technical debt, alternatives (thinking, temp 0.6)
3. **Tech Lead** — final architecture decision with trade-offs acknowledged, phased roadmap (thinking, temp 0.6)

### --medical — EBM Clinical Analysis

Trois passes en français, optimisées pour l'analyse clinique basée sur les preuves :
1. **Clinicien défenseur** — présente les preuves EN FAVEUR (RCT, guidelines, HAS/Cochrane) (no-think)
2. **Expert EBM critique** — évalue la qualité des preuves, risques sous-rapportés, alternatives (thinking)
3. **Synthétiseur clinique** — recommandation nuancée avec niveau de preuve, contre-indications, avertissement (thinking)

## CLI Options

```
debate_router.py [--code|--medical] [--verbose] [--json] [--context TEXT] <question>
```

- `--verbose` / `-v` : affiche les 3 passes complètes + timings
- `--json` : output JSON `{mode, debate: {advocate, critic, synthesis}, elapsed_s}`
- `--context` / `-c` : injecte un contexte RAG dans chaque passe

## Performance

| Mode | Pass 1 | Pass 2 | Pass 3 | Total |
|------|--------|--------|--------|-------|
| general | ~15s (no-think) | ~30s (think) | ~40s (think) | 60-90s |
| --code | ~15s (no-think) | ~30s (think) | ~45s (think) | 70-100s |
| --medical | ~15s (no-think) | ~30s (think) | ~40s (think) | 60-90s |

Timeout: 300s (120s par passe). Passes 2 et 3 toujours en thinking mode.
