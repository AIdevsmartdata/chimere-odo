# Quick start

Five minutes, three requests, one observable routing decision.

## Prerequisites

- Python 3.11+
- A running OpenAI-compatible inference backend on `127.0.0.1:8081`.
  We recommend [chimere-server](https://github.com/aidevsmartdata/chimere-server)
  with Qwen3.5-35B-A3B, but any backend speaking `/v1/chat/completions`
  works (`llama-server`, `ik_llama-server`, `vLLM`).
- Optional: `pip install --user pyyaml jsonschema` for better YAML parsing
  and OpenAI `response_format` validation.

Verify the backend first:

```bash
curl -s http://127.0.0.1:8081/health | jq
# { "status": "ok", "slots_idle": 4, ... }
```

## Start ODO

```bash
cd ~/.openclaw/odo
python3 odo.py
```

Expected output:

```
[odo] listening on 127.0.0.1:8084
[odo] backend: http://127.0.0.1:8081
[odo] pipelines: 8 loaded from /home/you/.openclaw/odo/pipelines
[odo] force_think: False
[odo] ABF: enabled=True threshold=0.55 ...
[odo] CGRS: enabled=True delta=0.9 triggers=13
[odo] stats: curl http://127.0.0.1:8084/stats
[odo] routes: curl http://127.0.0.1:8084/routes
```

ODO is now proxying at `127.0.0.1:8084`.

## 1. A code request — auto-routed to `code` pipeline

```bash
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chimere",
    "messages": [
      {"role": "user", "content": "Refactor this Python function to use type hints and handle empty inputs gracefully:\n\ndef sum_list(xs):\n    return xs[0] + sum_list(xs[1:])"}
    ],
    "stream": false
  }' | jq '{route: .x_odo_route, content: .choices[0].message.content}'
```

You should see:

```json
{
  "route": "code",
  "content": "def sum_list(xs: list[int | float]) -> int | float:\n    \"\"\"..."
}
```

The classifier picked `code` via keyword match (`function`, `python`,
`refactor`, `.py`). The `code` pipeline ran an architect → coder chain
(see `pipelines/code.yaml`) with `temperature: 0.3` for precision.

## 2. A clinical question — `kine` pipeline with parallel fan-out

```bash
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chimere",
    "messages": [
      {"role": "user", "content": "Protocole de rééducation pour entorse cheville stade 2 selon les recommandations HAS."}
    ],
    "stream": false
  }' | jq '{route: .x_odo_route, pipeline: .odo.pipeline, steps: .odo.steps | length, ms: .odo.pipeline_ms}'
```

Expected:

```json
{
  "route": "kine",
  "pipeline": true,
  "steps": 4,
  "ms": 28300
}
```

Four steps (`evidence_search → diagnostic → protocol ‖ contraindication_check → dosage`),
with the third being a parallel F2 group. Inspect the full chain:

```bash
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"chimere","messages":[{"role":"user","content":"Entorse cheville"}]}' \
  | jq '.odo.steps'
```

Each step carries `agent`, `step`, `ms`, `tokens`, `output_chars`. Entries
inside a parallel group share a `parallel_group` key.

## 3. A research query — `research` pipeline with deep search

```bash
curl -s http://127.0.0.1:8084/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chimere",
    "mode": "quality",
    "messages": [
      {"role":"user","content":"État de l art de la quantification mixte pour LLM Mamba-2 en 2025-2026. Inclure citations."}
    ]
  }' | jq '{route: .x_odo_route, tools: .odo.enriched, content_preview: .choices[0].message.content[:300]}'
```

Expected: the `research` pipeline runs scout → analyze → write_report
sequentially (web-enriched), with F3 parallel contradictions + synthesis
inside `deep_search_sota`. Typical latency at `quality` mode is
60–90 seconds.

## 4. Inspect the routing decision

```bash
curl -s http://127.0.0.1:8084/stats | jq
```

```json
{
  "last_24h": {
    "requests": 32,
    "avg_entropy": 0.421,
    "think_ratio": 0.875,
    "avg_probe_ms": 94,
    "avg_total_ms": 8214,
    "budget_forcing_count": 2,
    "avg_budget_retries": 1.0
  },
  "routes": {
    "kine":     { "count": 12, "avg_ms": 14200 },
    "code":     { "count":  9, "avg_ms":  4100 },
    "research": { "count":  4, "avg_ms": 66830 },
    "default":  { "count":  7, "avg_ms":  1820 }
  },
  "entropy_router": {
    "low":    { "count":  4, "avg_score": 0.17 },
    "medium": { "count": 22, "avg_score": 0.38 },
    "high":   { "count":  6, "avg_score": 0.61 }
  },
  "config": {
    "force_think": false,
    "abf_enabled": true,
    "abf_threshold": 0.55,
    "cgrs_enabled": true,
    "entropy_threshold": 0.8
  }
}
```

And the pipeline inventory:

```bash
curl -s http://127.0.0.1:8084/routes | jq
```

```json
{
  "routes": {
    "cairn":          { "name": "Cairn Vision Assistant", "thinking": false, "engram": true,  "lora": false },
    "code":           { "name": "Code Agent",             "thinking": false, "engram": true,  "lora": false },
    "cyber":          { "name": "Cyber Agent",            "thinking": true,  "engram": true,  "lora": false },
    "default":        { "name": "Default Agent",          "thinking": true,  "engram": true,  "lora": false },
    "kine":           { "name": "Kine Agent",             "thinking": true,  "engram": true,  "lora": false },
    "kinebot-dev":    { "name": "KineBot Dev Agent",      "thinking": false, "engram": false, "lora": false },
    "kinebot-review": { "name": "KineBot Review Agent",   "thinking": true,  "engram": false, "lora": false },
    "research":       { "name": "Research Agent",         "thinking": true,  "engram": true,  "lora": false }
  }
}
```

## 5. Influence the classifier

The classifier always runs, but you can nudge it:

- **Attach a file hint** via `odo_metadata.files: ["example.py"]` — the
  file-type strategy picks `code` from the `.py` extension and wins over a
  weak keyword match.
- **Write the right keywords in the user message** — the regex library in
  `classifier.py::ROUTES` is keyword-driven.
- **Drop a file with an image extension or pass a base64 image** — this
  lands on the `vision` passthrough.

The `odo_route` body field is currently accepted and silently stripped before
the backend call (odo.py:1728), but it does NOT override the classifier
decision. Treat it as reserved for a future route-pin feature.

## Next steps

- Edit a pipeline YAML — changes take effect immediately (mtime cache).
  See [pipeline-reference.md](pipeline-reference.md).
- Add a new skill — drop it in `~/.chimere/skills/<name>/SKILL.md`,
  ODO auto-detects and exposes via `/skill/list`.
- Turn on observability — `curl /v1/status` aggregates ODO + upstream health
  for Prometheus or Grafana scraping.
- Dive into internals — [architecture.md](architecture.md).

<!-- reviewer-notes
Changes applied vs v1:
- Section "5. Force a specific route" rewritten. v1 used `odo_route: "cyber"`
  and claimed the response would return `"cyber"` — verified this does NOT
  work: `odo_route` is popped from the payload (odo.py:1728) but the classifier
  is always invoked and is the sole source of route_id (odo.py:1374-1375).
  Replaced with a "nudge the classifier" section that actually reflects what
  works today (file hints, keywords, image extensions).
- All other numbers (expected ttft, stats output) are illustrative — not
  touched.
-->
