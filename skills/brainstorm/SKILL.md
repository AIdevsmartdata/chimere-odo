---
name: brainstorm
description: Multi-turn brainstorming session with RAG, domain profiles (code/tech/btp/cyber/finance), Claude Opus or local Qwen3.5 backend.
trigger_patterns:
  - "brainstorm"
  - "session de réflexion"
  - "explorer idées"
  - "options pour"
  - "stratégie"
tools_required:
  - exec
  - chromadb_rag
  - search_router
  - llm_qwen35
examples:
  - "start"
  - "start tech --local"
  - "--code Hexagonal vs Clean Architecture pour un LLM gateway"
  - "--profile code Comment structurer un pipeline RAG avec ChromaDB ?"
  - "Quelle stratégie pour le Projet Chimere ?"
model: qwen3.5-35b-a3b
execution:
  command: "/home/remondiere/.openclaw/bin/brainstorm.sh '$args'"
  timeout_ms: 300000
  arg_mode: raw
---

# brainstorm — Multi-turn Brainstorming Sessions

Session de brainstorming avec mémoire persistante, RAG code/web, et profils de domaine.

Default engine: Claude Opus (via `claude -p`). Add `--local` for Qwen3.5-35B-A3B.

## Commands

- `start [profile]` — démarre session (profils: default, btp, cyber, finance, tech, **code**)
- `stop` — termine session (export automatique en markdown)
- `status` — état de la session courante
- `export` — exporte en markdown
- `<message>` — tour de brainstorming (RAG web + ChromaDB + LLM)

## Code Profile — Architecture & Documentation

Le profil `code` active :
- Recherche web en mode deep (Perplexica balanced → documentation libs/frameworks)
- ChromaDB RAG sur la collection `code` (1704 chunks : llama.cpp, OpenClaw, Python, Rust, Docker)
- Thinking mode Qwen3.5 (`enable_thinking=true`, temp=0.8, top_p=0.95, top_k=20)
- System prompt orienté architecte senior : diagrammes ASCII, trade-offs, design patterns

## Flags

- `--code` — raccourci pour `--profile code`
- `--profile NAME` — profil explicite (default, btp, cyber, finance, tech, code)
- `--local` — utilise Qwen3.5-35B-A3B local au lieu de Claude Opus

## Profiles

| Profile | Domain | Search | RAG | Thinking |
|---|---|---|---|---|
| `default` | Brainstorming général | auto | auto | true |
| `tech` | Architecture logicielle | auto | auto | true |
| `code` | Code, patterns, libs, docs | deep | code | true, temp=0.8 |
| `btp` | BTP / construction | auto | auto | true |
| `cyber` | Cybersécurité | auto | auto | true |
| `finance` | Finance / stratégie | auto | auto | true |
