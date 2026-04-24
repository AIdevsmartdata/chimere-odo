# Pipeline reference

Every ODO route is a single YAML file in `pipelines/`. Drop a file named
`<route_id>.yaml`, add a matching regex in `classifier.py::ROUTES` plus
`route_id` in `classifier.py::PIPELINE_ROUTES`, restart ODO, and the route
is live. YAML files are hot-reloaded on mtime change — no restart needed
for edits after boot. The classifier module itself does need a restart
when you add a new route.

## Top-level keys

```yaml
name:          "Human-readable label"
description:   "One-line purpose"

# Injected before the user's system message.
system_prompt: |
  You are ...

# Sampler overrides (merged into the outbound payload).
params:
  temperature:        0.4
  top_p:              0.90
  top_k:              20
  min_p:              0.05
  presence_penalty:   0.0
  max_tokens:         4096

# Qwen3.5 <think>...</think> control.
thinking:
  enabled:        true          # null = let ODO decide (entropy/FORCE_THINK)
  budget:         2048          # tokens; overridden by `mode=quality|ultra`
  abf_threshold:  0.55          # per-route ABF cutoff

# Engram n-gram table (boosted in backend logit bias when engram= true).
engram:
  table:  "~/.openclaw/data/engram/kine.engr"
  alpha:  0.35                  # bias weight
  mode:   "read"                # or "write" (research ingests discovered n-grams)

# LoRA adapter (hot-swap via chimere-server).
lora:
  path: null                    # "/path/to/adapter" or null

# GBNF grammar constraint on final output.
grammar: null

# Enrichment — runs BEFORE generation, appends [Context] system block.
enrich:
  rag:            true
  rag_collection: "medical"     # or "auto" for auto-detect by route
  web:            false
  dynamic_engram: false         # built up from search results (mode=quality|ultra)

# Hybrid retrieval weights (RRF). Only honored when `rag.hipporag_enabled`.
rag:
  hipporag_enabled: true
  hipporag_path:    "~/.openclaw/data/hipporag"
  rrf_weights:
    chromadb: 0.4
    web:      0.2
    hipporag: 0.4

# Tools exposed to the model (OpenAI function-calling spec).
tools_allowed: ["web_search", "calculator", "think"]

# Pre-Act planner (arXiv 2505.09970) — short numbered plan prepended to system.
pre_act:
  enabled:           true
  planner_max_tokens: 384
  planner_temp:       0.3
  planner_timeout:    20        # seconds

# DVTS — Diverse Verifier Tree Search. Generate K candidates, score, return best.
dvts:
  enabled: false
  k:       3

# Temporal knowledge graph hook (Graphiti via MCP). Fire-and-forget.
memory_enabled: true

# Multi-step pipeline (see below). `null` = single-shot.
pipeline_auto: true             # run pipeline even without `mode=ultra`
pipeline: null                  # or a list of steps
```

## Multi-step pipelines

A `pipeline` value is a list of **steps**. Each step is either:

1. A **sequential step** (legacy): `{ agent, params, system_suffix, tools_allowed, iterations }`.
2. A **parallel group** (F2): `{ parallel: [ <step>, <step>, ... ] }`.

Steps run in YAML order. Each step sees the accumulated output of all
previous steps as a `[Context from previous steps]` system block. The
last step's output is the response returned to the caller.

### Example: purely sequential

```yaml
pipeline:
  - agent: "architect"
    params: { temperature: 0.7, max_tokens: 2048 }
    system_suffix: |
      Plan the implementation before coding. Outline modules, interfaces,
      data flow.

  - agent: "coder"
    params: { temperature: 0.2, max_tokens: 4096 }
    system_suffix: |
      Implement the architect's plan. Write production-ready code with
      full type annotations and error handling.

  - agent: "reviewer"
    params: { temperature: 0.3, max_tokens: 2048 }
    system_suffix: |
      Check the implementation for thread safety, memory leaks, and
      security issues. Provide corrected code if needed.
```

Wall-clock: steps run one after the other.

### Example: parallel group (F2)

```yaml
pipeline:
  - agent: "evidence_search"
    params: { temperature: 0.3, max_tokens: 2048 }
    system_suffix: "Search PubMed and HAS for evidence..."

  - agent: "diagnostic"
    params: { temperature: 0.4, max_tokens: 2048 }
    system_suffix: "Differential diagnosis. SOAP. Red flags."

  # F2 parallel group — children run concurrently on distinct GPU slots.
  - parallel:
      - agent: "protocol"
        params: { temperature: 0.4, max_tokens: 2048 }
        system_suffix: "Design HAS-aligned treatment protocol..."
      - agent: "contraindication_check"
        params: { temperature: 0.2, max_tokens: 1024 }
        system_suffix: "List absolute + relative contraindications..."

  - agent: "dosage"               # sees evidence + diagnostic + BOTH siblings
    params: { temperature: 0.3, max_tokens: 1024 }
    system_suffix: "Calculate exercise dosage parameters..."
```

Semantics:

- Children run on `ThreadPoolExecutor(max_workers=min(n, PIPELINE_PARALLEL_SLOTS))`.
- Each child sees the same `accumulated_context` as existed **before** the
  parallel group started (i.e., siblings cannot read each other's output).
- After all children complete, outputs are appended to `accumulated_context`
  **in YAML order** (not completion order) — downstream steps see a
  deterministic context.
- If all children fail, the pipeline returns `partial: true` with what
  was accumulated so far.

Gate: `PIPELINE_PARALLEL=1` (default) and chimere-server `/health` reports
≥ 2 `slots_available`. Fallback: the group is flattened to sequential.

### Step keys

| Key              | Type      | Purpose                                                    |
|------------------|-----------|------------------------------------------------------------|
| `agent`          | string    | Label used in logs (`[pipeline] step 2/4 agent=coder ...`). |
| `params`         | dict      | Same keys as top-level `params` — step-local override.     |
| `system_suffix`  | string    | Appended to the root `system_prompt` for this step.        |
| `tools_allowed`  | list[str] | Narrow tool set for this step (default: inherit).          |
| `iterations`     | int       | Repeat the step N times (research scout uses this for 5 search rounds). |

Inside a `parallel:` group, children use the same schema; a child's
`system_suffix` is applied to its own invocation only.

## Activation rules

A pipeline executes when **all** of these are true:

- The YAML has a non-empty `pipeline:` list with ≥ 2 steps.
- AND one of:
  - The request body has `"pipeline": true`, **or**
  - The YAML has `pipeline_auto: true`, **or**
  - The request has `"mode": "ultra"` (which forces `pipeline_auto=true`).

Otherwise the request runs single-shot through the pipeline's top-level
`params`, `engram`, `tools_allowed`, etc.

## Deploying a new pipeline

```bash
# 1. Create the YAML.
$EDITOR ~/.openclaw/odo/pipelines/legal.yaml

# 2. Edit the classifier.
$EDITOR ~/.openclaw/odo/classifier.py
# a) Add a regex in ROUTES:
#      "legal": re.compile(r"(?i)(contrat|clause|GDPR|RGPD|jurisprudence|...)"),
# b) Add "legal" to PIPELINE_ROUTES so the classifier stops remapping it to
#    "default" in _normalize_route().

# 3. Restart ODO — the pipelines/ directory is hot-reloaded via mtime,
#    but changes to classifier.py (new route list) need a full restart.
systemctl --user restart odo.service

# 4. Verify.
curl -s http://127.0.0.1:8084/routes | jq '.routes.legal'
# { "name": "Legal Agent", "thinking": true, "engram": false, "lora": false }

# 5. Test.
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"chimere","messages":[{"role":"user","content":"Analyse cette clause de non-concurrence..."}]}' \
  | jq '.x_odo_route'
# "legal"
```

## Shipped pipelines

| Route ID         | Purpose                                               | Steps | Classifier regex? |
|------------------|-------------------------------------------------------|-------|-------------------|
| `default`        | General-purpose assistant                             | 1     | fallback          |
| `code`           | Software development (architect → coder)              | 2     | yes               |
| `kine`           | Physiotherapy clinical assistant (HAS evidence-based) | 5 (with F2) | yes       |
| `cyber`          | Threat intel (triage → correlate → remediate)         | 3     | yes               |
| `research`       | Deep research (scout → analyze → write_report)        | 3     | yes               |
| `cairn`          | Wear OS Cairn running-app UI/code audit               | 1     | **no**            |
| `kinebot-dev`    | KineBot Android development (architect → coder → reviewer) | 3 | yes       |
| `kinebot-review` | KineBot security audit (triage → deep_review → report)| 3     | yes               |

`cairn.yaml` loads successfully but no regex in `classifier.py::ROUTES`
maps to it and it is not in `PIPELINE_ROUTES`. The pipeline ships as an
opt-in template for building vision-assist workflows; add a classifier
entry if you want ODO to route to it automatically.

## Testing a pipeline locally

Before enabling `pipeline_auto: true`, test the YAML explicitly:

```bash
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chimere",
    "pipeline": true,
    "messages": [{"role":"user","content":"<test prompt>"}]
  }' | jq '.odo.steps'
```

Each step logs to stderr as:

```
[pipeline] step 1/4 agent=evidence_search 2831ms 1203tok 5421chars
[pipeline] parallel group 3/4 — 2 children
[pipeline] parallel[0] agent=protocol 4122ms 1811tok 6230chars
[pipeline] parallel[1] agent=contraindication_check 1950ms 812tok 2841chars
[pipeline] parallel group 3/4 complete 4130ms
[pipeline] step 4/4 agent=dosage 1421ms 511tok 1842chars
[pipeline] complete: 5 sub-steps 12404ms 5237tok
```

If a step fails with `partial: true`, inspect stderr:

```
[pipeline] Step 3 (protocol) failed: HTTPConnectionPool timeout
```

The response still contains the most recent successful step's output under
`choices[0].message.content`.

## Gotchas

- **Thinking budget per step**: each pipeline step enforces `max_tokens >= 4096`
  when thinking is enabled, else the `<think>` block consumes the whole
  budget and the caller sees empty content.
- **System prompt growth**: after 4-5 steps, the accumulated context can
  exceed 20 k tokens — chimere-server's default 32 k context still fits,
  but truncation on your backend may bite. Use `max_tokens: 2048` on
  intermediate steps.
- **Parallel group with 1 child**: falls through to sequential (no
  `ThreadPoolExecutor` overhead).
- **`tools_allowed` on parallel children**: honored per-child but remember
  both children can now request tool calls independently — ensure your
  tool runners are idempotent / thread-safe.
- **`iterations`** on research scout: runs the step N times with the
  accumulated context growing each time; useful for multi-round retrieval.

<!-- reviewer-notes
Changes applied vs v1:
- "Deploying a new pipeline" now mentions editing PIPELINE_ROUTES in
  classifier.py (not just adding a regex). Without that, any new route is
  remapped to "default" by `_normalize_route()` — see classifier.py:265-272.
- Shipped pipelines table added a "Classifier regex?" column and a note
  underneath that cairn is not currently reachable via automatic routing
  (see README.md "Routing" section for the detail).
- Table cell `list\[str]` escape token was rendered as literal backslash
  in GitHub markdown — replaced with `list[str]`.
-->
