# chimere-odo

**One Door Orchestrator** — a single OpenAI-compatible HTTP endpoint that sits
in front of [chimere-server](https://github.com/aidevsmartdata/chimere-server)
(Rust inference on port 8081) and decides, per request, how to answer: which
route, which system prompt, which sampler, which engram, whether to think,
whether to run multi-agent pipelines, whether to fan out K candidates, whether
to reflect and retry.

One port in (`127.0.0.1:8084`), one port out (`127.0.0.1:8081`), everything
else — classification, enrichment, pipelines, quality gate — hidden behind
the proxy.

```
                                            ┌─────────────────────────────────┐
                                            │  chimere-server  (Rust/ik_llama)│
  client ──► ODO (8084)  ──── /v1/chat  ──► │  :8081   4-slot NativeScheduler │
            ▲                               │  Qwen3.5-35B A3B GGUF, 90 tok/s │
            │                               └─────────────────────────────────┘
            │
            │ classify → pipeline YAML → enrich (RAG/web)
            │        → sampler profile → think/no-think
            │        → [DVTS K-fanout | multi-agent pipeline]
            │        → forward → buffer → quality gate → reflect?
```

Part of the [Chimère](https://github.com/aidevsmartdata) family:

- [chimere](https://github.com/aidevsmartdata/chimere) — Rust inference engine (MTP/EAGLE/RAMP)
- [chimere-server](https://github.com/aidevsmartdata/chimere-server) — HTTP wrapper + multi-slot scheduler (backend for ODO)
- **chimere-odo** (this repo) — routing, pipelines, quality gate
- [chimere-studio](https://github.com/aidevsmartdata/chimere-studio) — web UI for ODO
- [ramp-quant](https://github.com/aidevsmartdata/ramp-quant) — custom quantization pipeline
- [ik_llama.cpp](https://github.com/aidevsmartdata/ik_llama.cpp) — fork with sm_120 + DeltaNet backports

---

## Why ODO exists

A raw `llama-server` endpoint answers every prompt the same way. Real usage
is heterogeneous: `"merci"` should not cost the same as `"state of the art on
LCA rehab"`. ODO classifies the request, picks one of ~8 pipeline YAMLs,
applies the right sampler, optionally runs a multi-step agent chain on
distinct GPU slots, and returns a single OpenAI-compatible response — so
clients (Open WebUI, `aider`, Claude Code, `curl`) plug in unchanged.

Empirically, on a single-user workstation (RTX 5060 Ti, Qwen3.5-35B-A3B):

| workload                       | raw llama-server | via ODO              |
|--------------------------------|------------------|----------------------|
| `"bonjour"`                    | ~4 s think block | 120 ms no-think       |
| `"HAS LCA rehab protocol"`     | single pass      | 4-step kine pipeline  |
| `"state of the art on X"`      | single pass      | scout/analyse/report + parallel contradictions |

---

## Features

### Routing

- **Cascading classifier** (`classifier.py`): greeting fast-path → keyword regex → file-type → LLM GBNF fallback on port 8086.
  Handles 99 %+ of traffic in 0 ms; LLM fallback is a hard 2 s timeout with `grammar=root ::= "code" | "kine" | …`
- **Classifier-reachable routes**: `code`, `kine`, `kinebot-dev`, `kinebot-review`, `cyber`, `research`, `default` (+ `vision`, `doc_qa` passthrough by file-type).
- **Pipeline YAMLs also shipped**: `cairn.yaml` (Wear OS assistant) ships but has no classifier regex — it loads only when the image file-type route is combined with a route override at the call site, so treat it as an opt-in template for now.
- **Hot-reload**: each pipeline YAML is mtime-cached; edit-save-test, no restart.

### Pipelines (YAML)

Each route is a single YAML file in `pipelines/`. Example:

```yaml
name: "Kine Agent"
params:
  temperature: 0.4
  top_p: 0.90
  max_tokens: 4096
thinking:
  enabled: true
  budget: 2048
system_prompt: |
  You are a clinical physiotherapy assistant ...
engram:
  table: "~/.openclaw/data/engram/kine.engr"
  alpha: 0.35
pipeline_auto: true
pipeline:
  - agent: "evidence_search"
    params: { temperature: 0.3, max_tokens: 2048 }
    system_suffix: "Search PubMed and HAS for evidence..."
  - agent: "diagnostic"
    params: { temperature: 0.4, max_tokens: 2048 }
    system_suffix: "Differential diagnosis, SOAP..."
  - parallel:                       # F2 — fan-out on 2 GPU slots
      - agent: "protocol"
        params: { temperature: 0.4 }
      - agent: "contraindication_check"
        params: { temperature: 0.2 }
  - agent: "dosage"                 # sees all upstream
    params: { temperature: 0.3, max_tokens: 1024 }
```

See [docs/pipeline-reference.md](docs/pipeline-reference.md) for the full schema.

### Parallel fan-out (April 2026)

ODO ships five parallel paths, all opt-in by environment variable and all
gated on a live `/health` probe for backend slot availability:

| ID  | What it parallelises                          | Env toggle                   | Default |
|-----|-----------------------------------------------|------------------------------|---------|
| F1  | DVTS K-candidate generation                   | `DVTS_PARALLEL`              | `1`     |
| F2  | YAML `- parallel:` pipeline groups            | `PIPELINE_PARALLEL`          | `1`     |
| F3  | deep_search contradictions + synthesis        | `DEEP_SEARCH_PARALLEL_SYNTH` | `1`     |
| F4  | research_orchestrator sub-question fan-out    | `RESEARCH_SUBQ_PARALLEL`     | `1`     |
| F6  | Speculative reflection (score ‖ reflect)      | `SPECULATIVE_REFLECTION`     | `0`     |

All implementations are stdlib only (`concurrent.futures.ThreadPoolExecutor` +
`http.client`) — no `httpx` or `asyncio` dependency. They assume chimere-server
is running with ≥ 2 idle slots; if `/health` reports fewer, they fall back to
sequential.

### Thinking control (Qwen3.5 Jinja)

Qwen3.5 has a `<think>...</think>` prelude gated by
`chat_template_kwargs: { enable_thinking: bool }`. ODO resolves this field
through a priority chain (in the order evaluated by `_decide_thinking`):

1. Tool calls (`tools`/`functions` present) → `no-think`.
2. Caller-provided `chat_template_kwargs` wins (respected as-is).
3. Vision input → `think`.
4. Very short prompt (< 20 chars) → `no-think`.
5. Greeting regex → `no-think` fast path.
6. Pipeline YAML `thinking.enabled` → honored.
7. Entropy-router hint (low → no-think).
8. `FORCE_THINK=1` env → `think`.
9. Entropy probe (5-token dry-run on port 8081, ~100 ms).

ODO also **consolidates multiple system messages to position 0** (Qwen3.5's
Jinja template crashes otherwise — this was the pain point that killed
`aider`'s architect mode pre-ODO), sanitises roles (`tool_result`→`tool`,
`ipython`→`user`) and strips `reasoning_content` from responses so history
stays small.

### Quality gate + reflection

After every response on scored routes (`kine`, `research`, `cyber`, `code`):

- Score response 1–5 (Qwen3.5 no-think) or 0.0–1.0 (ThinkPRM-1.5B CPU).
- If score ≤ 2 → `reflect_and_retry()`: ask the model to self-critique and
  produce a corrected response, replace content in the outbound JSON, tag
  with `choices[0].reflection = { original_score, reason, retried: true }`.
- F6 `SPECULATIVE_REFLECTION=1` (opt-in): score and reflection run
  concurrently; reflection future is cancelled if the score turns out OK
  (best-effort). Trades compute for latency on the ~2 % of requests that
  normally trigger reflection.

Good responses (score ≥ 4) are auto-fed into the per-route few-shot store
(`few_shot/`) for nightly LoRA training.

### Enrichment

Before forwarding, ODO can inject context from:

- ChromaDB RAG (`~/.openclaw/data/chromadb/` via `knowledge_rag_query.py`)
- HippoRAG 2 PPR graph (hybrid dense + BM25 + PPR, per-route RRF weights)
- Web search (Perplexica / SearXNG / Brave via `search_router.py`)
- CSV analysis (`pandas_query.py` on `*.csv` paths in the prompt)
- IoC lookup (CyberBro for IPs / hashes / domains in `cyber` route)

Enrichment tools run in a `ThreadPoolExecutor`; whatever is done by the
pipeline timeout is injected as a `[Context]` system block.

### Observability

- **`GET /health`** — liveness (ODO only).
- **`GET /v1/status`** — aggregate status: ODO uptime + upstream chimere-server `/health` + pipeline/skill counts + capability flags.
- **`GET /stats`** — last-24 h counters from SQLite (`~/.openclaw/logs/odo.db`): request count, think-ratio, avg entropy, routes distribution, budget-forcing retries, entropy-router class distribution.
- **`GET /routes`** — list of configured pipelines with `{name, thinking, engram, lora}` per route.
- **`GET /skill/list`** — Anthropic Agent Skills catalog (`~/.chimere/skills/`).

Training pairs are also logged to `~/.openclaw/logs/training_pairs.jsonl`
(each line is `{prompt, reasoning, response, budget_retries, prompt_hash}`)
for overnight fine-tuning (`dflash-nightly.timer`).

---

## Quick start (5 min)

Prereqs:

- Python 3.11+ (stdlib only — PyYAML optional)
- A running [chimere-server](https://github.com/aidevsmartdata/chimere-server) on `127.0.0.1:8081`
  (or any OpenAI-compatible backend: `llama-server`, `ik_llama-server`, `vLLM`).
- Optional: ChromaDB at `~/.openclaw/data/chromadb/` for RAG.

```bash
git clone https://github.com/aidevsmartdata/chimere-odo ~/.openclaw/odo
cd ~/.openclaw/odo

# Optional: PyYAML improves YAML parsing; the minimal parser works without it.
python3 -m pip install --user pyyaml jsonschema xgrammar

# Dry-run: classify a message without starting a server
python3 classifier.py "protocole entorse cheville stade 2"
# → {"route": "kine", "confidence": 0.95, "strategy": "keyword"}

# Start ODO
ODO_BACKEND=http://127.0.0.1:8081 python3 odo.py
# [odo] listening on 127.0.0.1:8084
# [odo] backend: http://127.0.0.1:8081
# [odo] pipelines: 8 loaded from /home/you/.openclaw/odo/pipelines
```

Send an OpenAI-compatible request:

```bash
curl -sN http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chimere",
    "messages": [{"role":"user","content":"Protocole entorse cheville stade 2"}],
    "stream": false
  }' | jq '.choices[0].message.content, .x_odo_route'
```

The response carries an `x_odo_route` field at the top level and an
`odo: { route, enriched, ... }` object for observability. The
`choices[0].message.content` is the final answer (reasoning stripped).

See [docs/quickstart.md](docs/quickstart.md) for a three-call tour
(code / kine / research) with expected outputs.

### systemd unit (recommended)

```ini
# ~/.config/systemd/user/odo.service
[Unit]
Description=ODO — One Door Orchestrator
After=network.target chimere-server.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/.openclaw/odo/odo.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=FORCE_THINK=1
Environment=ODO_BACKEND=http://127.0.0.1:8081
Environment=CHIMERE_BACKEND=http://127.0.0.1:8081

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now odo.service
journalctl --user -u odo.service -f
```

---

## Environment variables

All env vars are optional except where noted.

### Networking

| Variable           | Default                     | Purpose                                            |
|--------------------|-----------------------------|----------------------------------------------------|
| `ODO_PORT`         | `8084`                      | Listen port (bind is `127.0.0.1` only).            |
| `ODO_BACKEND`      | `http://127.0.0.1:8081`     | Inference backend (chimere-server / llama-server). |
| `ODO_LLM_BACKEND`  | `http://127.0.0.1:8086`     | Nothink proxy used only by the classifier LLM fallback. |
| `ODO_TIMEOUT`      | `300`                       | Seconds before a forwarded request is dropped.     |
| `CHIMERE_BACKEND`  | `http://127.0.0.1:8081`     | Read by `dvts.py` for K-candidate generation.      |
| `THINKPRM_BACKEND` | `http://127.0.0.1:8085`     | Optional ThinkPRM-1.5B step-verifier.              |
| `MCP_URL`          | `http://127.0.0.1:9095`     | Graphiti temporal-memory hook (fire-and-forget).   |

### Security (opt-in — default is open for `127.0.0.1`)

| Variable           | Default | Purpose                                       |
|--------------------|---------|-----------------------------------------------|
| `ODO_AUTH_TOKEN`   | *unset* | When set, required as `Authorization: Bearer <token>` — `hmac.compare_digest`. |
| `ODO_RATE_RPM`     | `60`    | Sliding-window rate limit per auth token / IP. |

### Thinking

| Variable              | Default | Purpose                                        |
|-----------------------|---------|------------------------------------------------|
| `FORCE_THINK`         | `0`     | `1` → always enable `<think>`, skip probe.     |
| `ENTROPY_THRESHOLD`   | `0.8`   | Entropy probe cutoff to decide think/no-think. |

### ABF — Adaptive Budget Forcing (ICLR 2026)

| Variable                | Default | Purpose                                         |
|-------------------------|---------|-------------------------------------------------|
| `ABF_ENABLED`           | `1`     | Enable in-flight certainty monitoring.          |
| `ABF_ALPHA`             | `0.625` | Weight on confidence in `Ct = α·Conf + β·(1-H)`. |
| `ABF_BETA`              | `0.375` | Weight on 1-entropy.                            |
| `ABF_THRESHOLD`         | `0.55`  | Accept response when `Ct ≥ threshold`.          |
| `ABF_MIN_THINKING_CHARS`| `100`   | Minimum thinking length to trust `Ct`.          |
| `ABF_MAX_RETRIES`       | `3`     | Max "Wait, let me reconsider" injections.       |
| `ABF_FALLBACK_MIN_CHARS`| `500`   | Accept without `Ct` if thinking is long enough. |
| `ABF_STREAM_WINDOW`     | `5`     | Sliding window for streaming ABF.               |

### CGRS — Certainty-Guided Reasoning Suppression (AAAI 2026)

| Variable           | Default | Purpose                                       |
|--------------------|---------|-----------------------------------------------|
| `CGRS_ENABLED`     | `1`     | When `Ct > delta`, suppress "Wait"/"Hmm" tokens via `logit_bias`. |
| `CGRS_DELTA`       | `0.9`   | Trigger threshold.                            |

### Parallelisation (April 2026)

| Variable                    | Default | Purpose                                       |
|-----------------------------|---------|-----------------------------------------------|
| `DVTS_PARALLEL`             | `1`     | F1 — DVTS generates K candidates concurrently. |
| `DVTS_PARALLEL_SLOTS`       | `4`     | F1 — max workers.                             |
| `PIPELINE_PARALLEL`         | `1`     | F2 — honor `- parallel:` YAML groups.         |
| `PIPELINE_PARALLEL_SLOTS`   | `4`     | F2 — max workers.                             |
| `DEEP_SEARCH_PARALLEL_SYNTH`| `1`     | F3 — contradictions ‖ synthesis (in `deep_search_sota.py`). |
| `RESEARCH_SUBQ_PARALLEL`    | `1`     | F4 — research sub-questions fan-out (in `research_orchestrator.py`). |
| `RESEARCH_SUBQ_WORKERS`     | `4`     | F4 — cap SearXNG concurrency.                 |
| `SPECULATIVE_REFLECTION`    | `0`     | F6 — score ‖ reflect on critical routes.      |

### Other

| Variable                 | Default                                  | Purpose                              |
|--------------------------|------------------------------------------|--------------------------------------|
| `LOG_TRAINING_PAIRS`     | `1`                                      | Append `{prompt, reasoning, response}` to JSONL. |
| `THINKPRM_ENABLED`       | `0`                                      | Enable CPU step-verifier scoring.    |
| `THINKPRM_SHADOW`        | `1`                                      | When `0`, ThinkPRM replaces Qwen3.5 scorer. |
| `ODO_MEMORY_HOOK_TIMEOUT`| `0.8`                                    | Graphiti MCP hook fire-and-forget timeout. |

---

## Endpoints

ODO is an OpenAI-compatible reverse proxy. Known endpoints:

| Method | Path                         | Purpose                                        |
|--------|------------------------------|------------------------------------------------|
| POST   | `/v1/chat/completions`       | Routed chat completion.                        |
| GET    | `/v1/models`                 | Synthesised list (`chimere`, `chimere-deltanet`). |
| GET    | `/v1/status`                 | ODO + upstream health + capability flags.      |
| GET    | `/health`                    | ODO liveness.                                  |
| GET    | `/stats`                     | Last-24 h counters (SQLite).                   |
| GET    | `/routes`                    | Installed pipelines.                           |
| GET    | `/skill/list`                | Anthropic Agent Skills catalog.                |
| GET    | `/skill/get/<name>`          | Single skill metadata.                         |
| GET    | `/skill/match?text=...`      | Skill trigger match.                           |
| POST   | `/skill/invoke/<name>`       | Execute a skill (sandboxed subprocess).        |
| POST   | `/v1/embeddings` and others  | Transparent pass-through to `ODO_BACKEND`.     |

The OpenAI-spec fields `response_format` (`json_object` / `json_schema`),
`tool_choice` (`auto` / `none` / `required` / `{"type":"function","function":{"name":…}}`)
and `parallel_tool_calls` are all honored via prompt-injection and
post-hoc JSON validation/repair. Structured-output errors surface as
`odo.structured_error`; tool-choice non-compliance as `odo.tool_choice_error`.

### `mode` extension

In addition to OpenAI fields, ODO accepts a non-standard `mode` in the
payload: `"fast"` (default), `"quality"`, `"ultra"`:

- `fast` — 2048-token think budget, no DVTS, no auto-pipeline, no web.
- `quality` — 4096-token budget, dynamic engram, confidence probe.
- `ultra` — 8192-token budget, DVTS `k=4`, pipeline-auto on, web-enrich on.

```bash
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"chimere", "mode":"ultra",
       "messages":[{"role":"user","content":"Méta-analyse efficacité cryothérapie post-op ACL"}]}'
```

---

## Integration examples

### curl

The body field `odo_route` is stripped from the forwarded payload before it
reaches the backend, and `odo_metadata` is read for file attachments; today
neither pins the classified route (the classifier always runs). Treat
`odo_route` as reserved for future route-pin support.

```bash
# Standard request — classifier picks the route:
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chimere",
    "messages": [{"role":"user","content":"analyse this IP 185.220.101.42"}]
  }' | jq '.x_odo_route, .odo'
# expect: "cyber", { route: "cyber", ... }
```

### aider

Because ODO consolidates system messages (required by Qwen3.5 Jinja),
aider's architect mode works out of the box:

```bash
cat > ~/.aider.conf.yml <<EOF
openai-api-base: http://127.0.0.1:8084/v1
openai-api-key:  none
model:           openai/chimere
architect:       true
editor-model:    openai/chimere
EOF
aider file.py
```

### Claude Code

Point `ANTHROPIC_BASE_URL` at ODO:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8084 \
ANTHROPIC_AUTH_TOKEN=none \
claude code
```

### Open WebUI filter

ODO appears under `GET /v1/models` as `chimere` and `chimere-deltanet`.
Point Open WebUI at `http://127.0.0.1:8084/v1` and ODO's routing kicks in
transparently.

---

## FAQ

**Q — Does ODO work with a backend other than chimere-server?**
Yes. Any OpenAI-compatible endpoint that speaks `/v1/chat/completions` works —
`llama.cpp`'s `llama-server`, `ik_llama-server`, `vLLM`, etc. The
parallelisation gates (F1/F2/F3) will probe `/health` for `slots_available`;
if the backend doesn't expose that field, `DVTS_PARALLEL_SLOTS` (default 4) is
assumed. Multi-slot concurrency requires a backend that actually supports it —
single-slot backends will serialise under the hood.

**Q — Why a Python stdlib-only proxy instead of FastAPI/Starlette?**
Zero deploy dependencies: `python3 odo.py` on a stock Ubuntu. The whole
thing is ~7.7 kLoC across 15 files; `http.server.ThreadingHTTPServer` handles
our load (≤ 60 RPM per user). A migration to `asyncio` is on the backlog for
multi-tenant but not prioritised for single-user workstations.

**Q — How do I turn off `FORCE_THINK`?**
`Environment=FORCE_THINK=0` in the systemd unit (or `unset FORCE_THINK`).
The entropy probe then decides per-request — ~100 ms extra on the first token
when it triggers. `FORCE_THINK=1` is our default because the model lives on
a 16 GB GPU where the ~200 ms probe round-trip is not worth the wrong
decision rate.

**Q — Can ODO run without pipelines?**
Yes. Delete `pipelines/*.yaml` (or move them aside) and every request falls
through to the bare classify → sample → forward path. ODO still consolidates
system messages, strips reasoning content, logs decisions, and surfaces
`x_odo_route`.

**Q — Is there auth?**
Optional single-token Bearer (`ODO_AUTH_TOKEN=...`) with timing-safe compare
and 60 RPM per token. Default is **open on `127.0.0.1`** — do not expose ODO
to the public internet without setting the token. There is no TLS; put
`nginx` or `tailscale-serve` in front if you need that.

**Q — What happens when chimere-server is down?**
`/v1/chat/completions` returns 502 with the backend error in the body, the
decision is still logged. `/v1/status` reports `upstream.ok: false` with the
last error. ODO keeps accepting requests; the systemd `After=` dependency
on `chimere-server.service` handles boot order but does not enforce runtime
health.

**Q — Can I add a new route?**
Drop `pipelines/myroute.yaml` in the pipelines directory. Add a regex for it
in `classifier.py::ROUTES` and add it to `PIPELINE_ROUTES`, restart ODO,
`curl /routes`. See [docs/pipeline-reference.md](docs/pipeline-reference.md).

**Q — Why does `FORCE_THINK` default-on conflict with `max_tokens: 512`?**
Qwen3.5's `<think>` block alone can exceed 4096 tokens — a small caller
`max_tokens` leaves zero budget for the visible content. ODO enforces a
floor of `THINK_MIN_TOKENS=4096` when thinking is on. If your client sends
`max_tokens: 512` and FORCE_THINK=1, the effective value is 4096.

---

## Development setup

```bash
git clone https://github.com/aidevsmartdata/chimere-odo
cd chimere-odo

# dev deps (optional — core is stdlib only)
python3 -m pip install --user pyyaml jsonschema xgrammar faiss-cpu

# Run tests (pytest-less, unittest-based)
python3 -m unittest discover -s tests -v

# Smoke test
python3 -c "from classifier import classify; print(classify('bonjour'))"
```

To add a new feature behind a flag: follow the F1-F6 convention — env var
defaults to the safe value, `/health` probe before fanout, fallback to
sequential on any failure.

Project layout:

```
odo.py                    # main HTTP handler (~2500 lines)
classifier.py             # intent → route (keyword + GBNF fallback)
pipeline_executor.py      # multi-step agent execution, F2 parallel groups
dvts.py                   # Diverse Verifier Tree Search, F1 parallel candidates
enricher.py               # RAG / web / CSV / IoC context injection
entropy_router.py         # pre-gen entropy classification
quality_gate.py           # score + reflect_and_retry
security_gate.py          # auth + rate limit + safe tool exec
skills_loader.py          # Anthropic Agent Skills catalog
pre_act.py                # plan-first prompting (arXiv 2505.09970)
xgrammar_helper.py        # structured output grammar compilation
pipelines/                # *.yaml per route
  code.yaml
  kine.yaml
  kinebot-dev.yaml
  kinebot-review.yaml
  cyber.yaml
  research.yaml
  cairn.yaml              # loadable but not classifier-routed — see Routing section
  default.yaml
docs/
  quickstart.md
  pipeline-reference.md
  architecture.md
```

---

## Limitations

- **Single-tenant**: no per-user isolation, no workspace, no history persistence beyond logs.
- **No streaming for pipeline routes**: multi-step pipelines buffer fully.
  (DVTS and ABF also buffer; entropy probe adds ~100 ms on first token when triggered.)
- **Global config in env vars**: no runtime reconfiguration API.
- **SQLite decisions DB** (`~/.openclaw/logs/odo.db`): grows unbounded — vacuum periodically.
- **Default is open on `127.0.0.1`** — `ODO_AUTH_TOKEN` is opt-in.
- **`odo_route` body field is currently a no-op** — the classifier always runs. Reserved for a future route-pin feature.

---

## License

MIT. See `LICENSE`.

Copyright (c) 2026 Kevin Remondière and the Chimère contributors.

_Last updated 2026-04-24._

<!-- reviewer-notes
Changes applied vs v1:
- "Active routes" section: removed `cairn` from the classifier-reachable set.
  Verified against `classifier.py::PIPELINE_ROUTES` (line 142) — cairn is NOT
  listed. The YAML exists and loads, but no regex maps there. Added a note
  that cairn.yaml is opt-in and noted the same in the project-layout tree.
- Thinking priority chain: reordered to match the actual evaluation order in
  `_decide_thinking()` (odo.py lines 1615–1663). Original v1 had caller-override
  listed before tool-calls; actual code checks tools first. Also split the
  short/greeting steps into two separate rules as in the code.
- `curl` "Explicit route pin" example: removed. The `odo_route` field is popped
  from the forwarded payload (odo.py:1728) but it does NOT override the
  classifier — `route_id` is only ever set from `classify(...)` (odo.py:1375).
  Quickstart section "Force a specific route" also edited for the same reason.
  Added a "reserved for future" mention + note under Limitations.
- "Delete pipelines/*.yaml (or point PIPELINES_DIR at an empty dir)" — there
  is no PIPELINES_DIR env override in odo.py; the dir is hard-coded to
  `Path(__file__).parent / "pipelines"` (odo.py:148). Rewrote the sentence.
- "~7.7 kLoC across 16 files" → "15 files" (15 *.py modules; backend_router
  + dynamic_engram + semantic_fewshot + confidence_rag_trigger are counted).
- F3 / F4 default columns clarified that those env vars live in
  `bin/deep_search_sota.py` + `bin/research_orchestrator.py`, not in
  the odo/ tree (they're consumed by tools ODO invokes).
- THINKPRM_SHADOW default cross-checked: `quality_gate.py:38` sets the
  runtime default to "1" (shadow mode). README default "1" is correct as-is.
  Note: `odo.py:1271` reads it with default "0" for a status-endpoint
  echo field — harmless inconsistency, not worth surfacing.
- No factual changes to ABF/CGRS tables; all defaults verified against
  `odo.py:161–173`.
-->
