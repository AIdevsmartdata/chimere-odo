---
name: datascience
description: CSV analysis via 2-pass SOTA orchestrator (score 54/55 on 7 CSVs). Statistics, data profiling, visualizations, insights.
trigger_patterns:
  - "analyse csv"
  - "analyser fichier"
  - ".csv"
  - "data analysis"
  - "statistiques"
tools_required:
  - exec
  - pandas
  - llm_qwen35
examples:
  - "/chemin/vers/fichier.csv"
  - "/tmp/patients.csv"
  - "~/data/sales_2026.csv"
model: qwen3.5-35b-a3b
execution:
  command: "/home/remondiere/.openclaw/bin/analyze_csv.sh '$args'"
  timeout_ms: 360000
  arg_mode: raw
---

# datascience — CSV Analysis

Dispatch direct vers `analyze_csv.sh` : orchestrateur 2-pass, score 54/55 sur 7 CSVs de référence.

Bypasse le modèle principal : le gateway appelle exec directement.

## Process

1. **Pass 1** — Data profiling (dtypes, null counts, unique values, distributions)
2. **Pass 2** — LLM insights (patterns, anomalies, recommandations analytiques)

## Output

- Shape et dtypes du dataset
- Statistiques descriptives (mean, std, min, max, quartiles)
- Null analysis + outliers
- Cardinalité + corrélations
- Insights LLM synthétisés en français
