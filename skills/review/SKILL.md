---
name: review
description: Intelligent code review agent — security (OWASP Top 10), best practices, test coverage, consistency. Auto-detects stack (Django/FastAPI/Node/Rust) and adapts checklists.
trigger_patterns:
  - "review"
  - "code review"
  - "audit code"
  - "security review"
  - "owasp"
  - "audit projet"
tools_required:
  - exec
  - filesystem
  - llm_qwen35
examples:
  - "/path/to/project"
  - "/path/to/project --auto-fix"
  - "/path/to/project --categories security,best_practices"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/code_reviewer_wrapper.sh review '$args'"
  timeout_ms: 300000
  arg_mode: raw
---

# review — Intelligent Code Review

Analyse adaptative d'un projet via Qwen3.5 avec thinking enabled.
Détecte automatiquement le stack (Django, FastAPI, Node, Rust...) et adapte les checklists.

## Usage

- `/path/to/project` — review complet (security + best_practices + test_coverage + consistency)
- `/path/to/project --auto-fix` — review + correction automatique des patches
- `/path/to/project --categories security,best_practices` — catégories spécifiques

## Categories

| Category | Description |
|---|---|
| security | OWASP Top 10, secrets, auth, XSS, injection |
| best_practices | Anti-patterns framework-specifiques, N+1, race conditions |
| test_coverage | Gaps de tests, endpoints non-testés, edge cases |
| consistency | Cohérence spec↔code, deps inutilisées, code mort |

## Output

Rapport structuré avec findings par sévérité (critical/high/medium/low/info).
Chaque finding inclut :
- **Location** — fichier:ligne
- **Description** — problème identifié
- **Patch** — correctif proposé (quand applicable)
- **References** — OWASP ID, CWE, docs framework

## Auto-Fix Mode

Avec `--auto-fix`, le skill applique les patches validés (high-confidence uniquement).
Les patches critiques sont toujours présentés pour validation manuelle.

## Performance

- Timeout : 300s
- Projets typiques : 30-120s selon taille codebase
