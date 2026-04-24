---
name: architect
description: Code architecture design with web-informed debate between Architect and Reviewer agents — produces ASCII diagrams, justified stack, patterns (SOLID/DDD/CQRS), phased implementation plan.
trigger_patterns:
  - "architecture"
  - "design pattern"
  - "structure microservice"
  - "structurer"
  - "stack technique"
  - "event-driven"
  - "graphql"
tools_required:
  - exec
  - search_router
  - llm_qwen35
examples:
  - "Comment structurer un microservice Rust avec gRPC + PostgreSQL + Redis cache ?"
  - "Architecture event-driven avec Kafka pour un pipeline ML en Python ?"
  - "Quels patterns utiliser pour une API GraphQL multi-tenant avec Prisma ?"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/architect_wrapper.sh '$args'"
  timeout_ms: 600000
  arg_mode: raw
---

# architect — Architecture Design

Session de conception architecturale avec recherche de documentation et débat structuré.

Backend: Qwen3.5-35B-A3B (thinking mode) + search_router (Brave/Perplexica).

## Process

1. **Extraction** — Identification des technologies et frameworks mentionnés
2. **Recherche** — Documentation actuelle via search_router
3. **Architecte** — Proposition détaillée avec diagramme ASCII, stack justifié, patterns
4. **Reviewer** — Critique (sécurité, performance, maintenabilité, alternatives)
5. **Tech Lead** — Synthèse finale avec architecture retenue, trade-offs, plan d'implémentation

## Output Structure

- Diagramme ASCII de l'architecture
- Stack technologique justifié
- Patterns utilisés (SOLID, DDD, CQRS, etc.)
- Critique structurée (sécurité, performance, maintenabilité)
- Plan d'implémentation en phases
- Points de vigilance

## Performance

- Temps total : ~3-6 minutes (3 passes thinking + recherche web)
- Timeout : 600s
- Recherche : jusqu'à 3 technologies, 1500 chars chacune
