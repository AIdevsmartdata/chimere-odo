# Chimère Skills — Anthropic Agent Skills 2026 Format

Skills canoniques pour Chimère ODO, format inspiré de [Anthropic Agent Skills](https://www.anthropic.com/news/agent-skills).

Chaque skill vit dans son propre dossier, avec un `SKILL.md` (YAML frontmatter + instructions Markdown) et optionnellement un `scripts/` contenant les binaires exécutables.

## Structure

```
~/.chimere/skills/
├── README.md                    # cet index
├── architect/SKILL.md           # architecture design
├── brainstorm/SKILL.md          # multi-turn brainstorming
├── datascience/SKILL.md         # CSV analysis
├── debate/SKILL.md              # multi-perspective reasoning
├── deepthink/SKILL.md           # deep reflection + web
├── ioc-analyze/SKILL.md         # threat intel (IoCs)
├── mcporter/SKILL.md            # MCP CLI tool
├── ml-training/SKILL.md         # GPU training jobs
├── project/SKILL.md             # project management
├── research/SKILL.md            # deep web research
├── review/SKILL.md              # code review
└── verify/SKILL.md              # self-consistency voting
```

## YAML Frontmatter Schema

Chaque `SKILL.md` commence par un bloc YAML avec les champs suivants :

| Field | Required | Description |
|---|---|---|
| `name` | Oui | Identifiant unique du skill (slug) |
| `description` | Oui | Description 1-2 lignes (utilisée pour routing / auto-discovery) |
| `trigger_patterns` | Oui | Mots-clés/patterns qui déclenchent le skill (routing heuristique) |
| `tools_required` | Oui | Liste des tools/services nécessaires (exec, llm_qwen35, chromadb_rag, etc.) |
| `examples` | Oui | Liste de 3-5 exemples d'invocation en langage naturel |
| `model` | Non | Modèle LLM ciblé (défaut: qwen3.5-35b-a3b) |
| `execution` | Non | Bloc d'exécution : command/timeout_ms/arg_mode ou `type: instructions_only` |

Le corps du Markdown contient les instructions détaillées, exemples d'usage, tableaux de performance.

## Skills Index

### Research & Reasoning

| Skill | Description | Duration | Model |
|---|---|---|---|
| [research](research/SKILL.md) | Deep web research, 4 modes (standard/deep/marathon/ultra) | 2-50 min | Qwen3.5-35B |
| [deepthink](deepthink/SKILL.md) | Web-grounded reflection, thinking mode | 30-120s | Qwen3.5-35B |
| [debate](debate/SKILL.md) | Multi-perspective (advocate/critic/synth) — general/code/medical | 60-100s | Qwen3.5-35B |
| [brainstorm](brainstorm/SKILL.md) | Multi-turn brainstorming with RAG, 6 domain profiles | multi-turn | Claude Opus / Qwen3.5 |
| [verify](verify/SKILL.md) | Self-consistency voting (N=3), -46% hallucinations | 3-5s | Qwen3.5-35B |

### Engineering

| Skill | Description | Duration | Model |
|---|---|---|---|
| [architect](architect/SKILL.md) | Architecture design (ASCII diagrams, phased plan) | 3-6 min | Qwen3.5-35B |
| [review](review/SKILL.md) | Code review (OWASP + best practices + tests + consistency) | 30-120s | Qwen3.5-35B |
| [project](project/SKILL.md) | Workspace/file/git/phase management, smart routing | <5s | Qwen3.5-35B |
| [ml-training](ml-training/SKILL.md) | GPU training launch/monitoring rules (instructions-only) | N/A | N/A |

### Data & Security

| Skill | Description | Duration | Model |
|---|---|---|---|
| [datascience](datascience/SKILL.md) | CSV analysis (2-pass SOTA, score 54/55) | 30-90s | Qwen3.5-35B |
| [ioc-analyze](ioc-analyze/SKILL.md) | IoC threat intel (CyberBro 33 engines + Brave CTI) | 30-90s | Qwen3.5-35B |

### Infrastructure

| Skill | Description | Duration | Model |
|---|---|---|---|
| [mcporter](mcporter/SKILL.md) | MCP CLI — list/call/auth/config MCP servers & tools | <10s | N/A (CLI) |

## Loading

Skills sont chargés dynamiquement par `~/.openclaw/odo/skills_loader.py` qui :

1. Scanne `~/.chimere/skills/*/SKILL.md`
2. Parse le YAML frontmatter
3. Expose chaque skill comme :
   - Entrée dans `/skill/list` endpoint ODO (HTTP GET)
   - Tool MCP (via chimere-mcp) pour utilisation par clients Claude/OpenAI
   - Injection optionnelle dans `TOOL_DEFINITIONS` d'ODO pour routage LLM

## Testing

```bash
# List all skills via ODO HTTP
curl http://127.0.0.1:8084/skill/list

# Get skill details
curl http://127.0.0.1:8084/skill/get/research

# Python loader sanity check
python3 -c "from skills_loader import load_all_skills; \
            skills = load_all_skills(); \
            print(f'Loaded {len(skills)} skills:'); \
            [print(f'  - {s[\"name\"]}: {s[\"description\"][:60]}...') for s in skills]"
```

## Adding a New Skill

1. Créer le dossier : `mkdir ~/.chimere/skills/<name>`
2. Rédiger `SKILL.md` avec YAML frontmatter complet + instructions Markdown
3. (Optionnel) Ajouter `scripts/run.sh` pour l'exécution
4. Redémarrer ODO : `systemctl --user restart odo.service`
5. Vérifier : `curl http://127.0.0.1:8084/skill/list | jq '.skills[] | .name'`

## References

- [Anthropic Agent Skills](https://www.anthropic.com/news/agent-skills) (2026)
- [MCP specification](https://modelcontextprotocol.io)
- Original OpenClaw skills : `~/.openclaw/workspaces/main/skills/` (legacy)
