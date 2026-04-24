---
name: project
description: Unified project management — workspace creation, file ops, code execution, git, phase workflow (analyse→design→code→test→done). Smart routing via Qwen3.5 for free-form requests.
trigger_patterns:
  - "créer projet"
  - "créer workspace"
  - "git commit"
  - "git status"
  - "run file"
  - "execute script"
  - "phase"
  - "workspace"
tools_required:
  - exec
  - git
  - bash
  - llm_qwen35
examples:
  - "create my-api --template python --description 'REST API for kiné patients'"
  - "phase --set design"
  - "files write src/main.py 'from fastapi import FastAPI'"
  - "run src/main.py"
  - "git commit -m 'feat: initial API scaffold'"
  - "context"
  - "list"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/project_wrapper.sh '$args'"
  timeout_ms: 120000
  arg_mode: raw
---

# project — Unified Project Management

Gestion de projet unifiée : création, fichiers, exécution, tests, git, phases.

Délègue à file_manager.py, code_runner.py, git_ops.py, workspace_manager.py.
Smart routing via Qwen3.5-35B pour requêtes en langage naturel.

## Commands

### Workspace Management

```
create <name> [--template python|rust|node|web] [--description "..."]
switch <name>
list
status
```

### File Operations

```
files <subcommand> [args...]
```

Délègue à file_manager.py (read, write, list, search, tree).

### Code Execution

```
run <code_or_file> [--lang python|rust|node|bash]
test [path]
```

Délègue à code_runner.py.

### Git Operations

```
git <subcommand> [args...]
```

Délègue à git_ops.py (status, commit, push, log, diff, branch).

### Phase Workflow

```
phase                    # Show current phase
phase --set <phase>      # Set phase
```

Phases: `analyse` → `design` → `code` → `test` → `done`
Chaque phase auto-crée un template dans `.openclaw/phases/`.

### Context Generation

```
context
```

Génère un contexte LLM : arbre fichiers, README, git log, docs de phase, config.

### Smart Routing

```
"add authentication to my app"
```

Si l'input n'est pas une commande reconnue, Qwen3.5 interprète l'intention et suggère la commande appropriée.

## Phase Details

| Phase | Description | Auto-creates |
|-------|-------------|--------------|
| analyse | Requirements gathering | `.openclaw/phases/analyse.md` |
| design | Architecture design | `.openclaw/phases/design.md` |
| code | Implementation | (none) |
| test | Testing | `.openclaw/phases/test.md` |
| done | Project complete | (none) |

## Performance

- Most commands: < 5s
- Smart routing (LLM): ~5-10s
- Code execution: up to 120s (timeout)
