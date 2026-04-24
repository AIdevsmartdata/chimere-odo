# Architecture

A deeper look at ODO internals for contributors and curious operators.

## Module map

```
odo.py                  (~2465 LoC)  main HTTP handler, thinking resolution,
                                     ABF / CGRS monitoring, reflection,
                                     systemd entrypoint (main()).
classifier.py           ( 324 LoC)   3-strategy cascade: keyword/file/LLM.
pipeline_executor.py    ( 436 LoC)   execute_pipeline() + F2 parallel groups.
dvts.py                 ( 426 LoC)   Diverse Verifier Tree Search + F1 parallel.
enricher.py             ( 803 LoC)   RAG / web / CSV / IoC context injection.
entropy_router.py       ( 411 LoC)   pre-gen complexity heuristic classifier.
quality_gate.py         ( 545 LoC)   Qwen3.5 + ThinkPRM dual scorer, reflection.
security_gate.py        ( 263 LoC)   auth (H1) + rate limit (H2) + tool safety (H8).
skills_loader.py        ( 522 LoC)   Anthropic Agent Skills catalog.
pre_act.py              ( 190 LoC)   plan-first prompting (arXiv 2505.09970).
xgrammar_helper.py      ( 193 LoC)   structured output grammar compilation.
backend_router.py       ( 393 LoC)   local → Mistral → Gemini escalation (opt-in, disabled by default).
dynamic_engram.py       ( 169 LoC)   build engram from search results (quality/ultra modes).
semantic_fewshot.py     ( 329 LoC)   FAISS few-shot retrieval, warmed up at boot.
confidence_rag_trigger.py ( 240 LoC) trigger RAG on low-confidence responses.
pipelines/*.yaml        (8 files)    route configurations.
```

Total: ~7 700 LoC of Python + ~20 kB YAML (as of 2026-04-24).

## Class / module hierarchy

```
main()
 └─ ThreadedHTTPServer(ThreadingMixIn, HTTPServer)
     └─ ODOHandler(BaseHTTPRequestHandler)
         ├─ do_GET()
         │   ├─ /health, /stats, /routes, /v1/status, /v1/models
         │   └─ /skill/{list,get,match}
         │
         ├─ do_POST()
         │   ├─ /skill/invoke/<name>        → skills_loader.exec_skill()
         │   ├─ non-/v1/* paths             → _proxy_post()   (pass-through)
         │   └─ /v1/chat/completions        → the main pipeline:
         │         1. security gate          (security_gate.check_auth + rate_limit)
         │         2. sanitize_messages       (role mapping + system consolidation)
         │         3. _apply_response_format  (OpenAI structured output)
         │         4. _apply_tool_choice      (OpenAI tool policy)
         │         5. classifier.classify()   (route + confidence + strategy)
         │         6. load_pipeline()         (mtime-cached YAML)
         │         7. _trigger_memory_hook()  (Graphiti MCP, fire-and-forget)
         │         8. mode resolution         (fast / quality / ultra)
         │         9. pre_act.run()           (optional plan-first)
         │        10. enricher.enrich()       (RAG / web / CSV / IoC)
         │        11. if should_use_pipeline()
         │              → pipeline_executor.execute_pipeline()
         │              → (optional) F2 parallel group → _execute_parallel_group()
         │        12. else:
         │              entropy_router.estimate_entropy()
         │              _decide_thinking()     (priority chain → sampler profile)
         │              _forward_with_params():
         │                - if pipeline.dvts.enabled → dvts.dvts_generate()   (F1)
         │                - elif needs_abf          → _abf_monitor()
         │                - else                    → _forward_raw() + _stream/_buffer
         │        13. post-response: quality_gate → (optional) reflect_and_retry
         │              (F6 — SPECULATIVE_REFLECTION=1 runs them concurrently)
         │        14. log_decision() → SQLite (async thread)
         │
         └─ do_OPTIONS() — CORS preflight (allow everything, local use).
```

## Request lifecycle (ASCII)

```
 client
   │ POST /v1/chat/completions
   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ODOHandler.do_POST()                                                     │
│                                                                          │
│  1. security_gate.check_auth(headers)  ──▶ 401 if bad token             │
│  2. security_gate.rate_limit(uid)      ──▶ 429 if over RPM              │
│                                                                          │
│  3. sanitize_messages(messages)                                          │
│        tool_result → tool | ipython → user | consolidate system@0       │
│                                                                          │
│  4. _apply_response_format(payload) ──▶ XGrammar | suffix | no-op       │
│  5. _apply_tool_choice(payload)     ──▶ "required" / "none" / specific  │
│                                                                          │
│  6. classifier.classify(user_text, files, has_image)                     │
│     ┌────────────────────────────────────────────────┐                   │
│     │ fastpath greetings (0 ms)                      │                   │
│     │ keyword regex     (0 ms)  confidence ≥ 0.5     │                   │
│     │ file-type         (0 ms)  ext/image heuristic  │                   │
│     │ LLM GBNF fallback (~50 ms via port 8086)       │                   │
│     └────────────────────────────────────────────────┘                   │
│        → { route, confidence, strategy }                                 │
│                                                                          │
│  7. load_pipeline(route_id)                                              │
│     ┌────────────────────────────────────────────────┐                   │
│     │ mtime-cached YAML reader                       │                   │
│     │ _pipeline_cache[route_id]                      │                   │
│     └────────────────────────────────────────────────┘                   │
│                                                                          │
│  8. _trigger_memory_hook()  ───▶  MCP :9095  (fire-and-forget)          │
│                                                                          │
│  9. mode resolution (fast/quality/ultra) — overlays pipeline config      │
│ 10. pre_act.run()           ───▶  port 8081 (plan, +300-800 ms)         │
│ 11. enricher.enrich()       ───▶  RAG / web / CSV / IoC (parallel)      │
│                                                                          │
│ 12a. multi-step pipeline ?                                               │
│     ┌────────────────────────────────────────────────┐                   │
│     │ for step in steps:                             │                   │
│     │   if step.parallel:                            │                   │
│     │     _probe_backend_slots() ≥ 2 → fanout (F2)   │                   │
│     │   else:                                        │                   │
│     │     _execute_one_step() ──▶ port 8081           │                   │
│     │   accumulated_context.append(step.output)      │                   │
│     └────────────────────────────────────────────────┘                   │
│     return last_step.output                                              │
│                                                                          │
│ 12b. single-shot ?                                                       │
│     entropy_router.estimate_entropy() ─▶ low/medium/high + action        │
│     _decide_thinking() → (decision, params, probe_entropy, probe_ms)     │
│     _forward_with_params():                                              │
│         if pipeline.dvts.enabled:                                        │
│             dvts_generate() → K candidates (F1 parallel) → ThinkPRM      │
│         elif needs_abf and thinking:                                     │
│             _abf_monitor() → streaming Ct, retry up to ABF_MAX_RETRIES   │
│         else:                                                            │
│             _forward_raw() → _stream_response() or _buffer_response()    │
│                                                                          │
│ 13. post-response (buffered path):                                       │
│         strip reasoning_content                                          │
│         validate JSON / repair (response_format)                         │
│         tool_choice compliance check                                     │
│         if route in REFLECT_ROUTES and score ≤ 2:                        │
│            reflect_and_retry() → patch choices[0].message.content        │
│            (F6 SPECULATIVE_REFLECTION=1: score ‖ reflect, cancel reflect │
│             if score ≥ 3)                                                │
│                                                                          │
│ 14. log_decision() → SQLite (async thread)                               │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
   │ 200 OK {choices, usage, x_odo_route, odo}
   ▼
 client
```

## Parallel fan-out mechanism

All five parallel paths share the same skeleton (stdlib only):

```python
def _probe_backend_slots(url: str) -> int:
    """HEAD-like probe of /health. Reads slots_available|slots_idle|n_slots_idle."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=2.0)
        conn.request("GET", "/health")
        data = json.loads(conn.getresponse().read())
        for k in ("slots_available", "slots_idle", "n_slots_idle"):
            if k in data:
                return max(1, int(data[k]))
        return DEFAULT_SLOTS
    except Exception:
        return DEFAULT_SLOTS  # be tolerant

use_parallel = FEATURE_PARALLEL and k >= 2
if use_parallel:
    if _probe_backend_slots(url) < 2:
        use_parallel = False  # fallback to sequential

if use_parallel:
    with ThreadPoolExecutor(max_workers=min(k, FEATURE_WORKERS)) as pool:
        futures = {pool.submit(_one, arg): i for i, arg in enumerate(args)}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
else:
    for arg in args:
        results.append(_one(arg))
```

All concurrency lives in worker threads; the main HTTP handler stays
thread-per-connection via `ThreadingMixIn`.

Each feature gate has three fail-safes:

1. **Env toggle** — set `*_PARALLEL=0` to revert without a restart cycle
   (just restart the affected request).
2. **Health probe** — if the backend reports `< 2` idle slots, fall back
   to sequential.
3. **Per-future exception** — a thrown future populates the corresponding
   slot with `{ error: str, ok: false }`; the caller decides whether
   partial results are acceptable.

## FORCE_THINK and Qwen3.5 Jinja

Qwen3.5's chat template requires:

1. System message at **position 0** (multiple system messages or a user
   message at position 0 blow up the Jinja render).
2. Roles in `{system, user, assistant, tool}` only.
3. `chat_template_kwargs: { enable_thinking: bool }` to gate the
   `<think>...</think>` prelude.

`sanitize_messages()` in `odo.py` fixes all three:

```python
def sanitize_messages(messages: list) -> list:
    sanitized = []
    for msg in messages:
        role = msg.get("role", "")
        if role in {"system", "user", "assistant", "tool"}:
            sanitized.append(msg)
        elif role in ("tool_result", "tool_response", "function"):
            sanitized.append({**msg, "role": "tool"})
        elif role == "ipython":
            sanitized.append({**msg, "role": "user"})
        # ... fallback to user
    # merge all system messages into a single one at position 0:
    system_msgs = [m for m in sanitized if m["role"] == "system"]
    other_msgs = [m for m in sanitized if m["role"] != "system"]
    if len(system_msgs) > 1:
        merged = "\n\n".join(m["content"] for m in system_msgs)
        sanitized = [{"role": "system", "content": merged}] + other_msgs
    return sanitized
```

This single function is what made `aider` architect mode work — it sends
multiple system messages by design.

`FORCE_THINK=1` is separate — it sets `chat_template_kwargs.enable_thinking=True`
and enforces `max_tokens >= THINK_MIN_TOKENS (4096)` so the think block
does not starve the visible output. The priority chain in
`_decide_thinking()` (see lines 1615-1663 of `odo.py`) overrides
`FORCE_THINK` only for:

1. Tool calls present (`no-think`).
2. Explicit caller `chat_template_kwargs`.
3. Image input (`think` unconditionally).
4. Very short prompts (`< 20 chars`) or greeting regex.

ABF (Adaptive Budget Forcing) runs in parallel with thinking: it watches
the token logprobs stream, computes `Ct = α·Conf + β·(1-H)`, and either
accepts the generation (when `Ct ≥ threshold`) or injects
`Wait, let me reconsider this step by step.` as an assistant prefill and
retries. Max 3 retries by default. Streaming ABF is informational only
(logged to stderr).

## State stores

| Path                                           | Purpose                                        | Retention          |
|------------------------------------------------|------------------------------------------------|--------------------|
| `~/.openclaw/logs/odo.db`                      | SQLite: `decisions` table (routing log)        | Unbounded (vacuum manually) |
| `~/.openclaw/logs/training_pairs.jsonl`        | `{prompt, reasoning, response}` for DFlash LoRA | Unbounded — rotated by nightly |
| `~/.openclaw/logs/quality_scores.jsonl`        | `{route, score, reason, scorer}` from quality_gate | Unbounded (tail-scanned by entropy_router) |
| `~/.openclaw/data/chromadb/`                   | Knowledge RAG embeddings                       | Refreshed 6 h      |
| `~/.openclaw/data/engram/*.engr`               | Per-route n-gram tables (binary format)        | Built overnight    |
| `~/.openclaw/data/hipporag/`                   | HippoRAG 2 PPR graph                           | Manual rebuild     |
| `~/.chimere/skills/<name>/SKILL.md`            | Anthropic Agent Skills                         | Manual deploy      |
| `pipelines/*.yaml`                             | Route config (hot-reloaded via mtime)          | Git-tracked        |
| `few_shot/<route>.jsonl`                       | Auto-fed high-quality examples (score ≥ 4)     | Cap 10/route       |

`init_db()` runs on boot and lazily migrates the `decisions` table
(adds `entropy_class`, `entropy_score` columns if missing). All writes
to the logs are wrapped in `threading.Thread(daemon=True)` — never block
a response.

## Error model

| Failure                          | Behaviour                                         |
|----------------------------------|---------------------------------------------------|
| Backend `/v1/chat/completions` 5xx | Return 502 to caller, log decision with `decision=error`. |
| Backend timeout                   | Same as 5xx.                                      |
| Pipeline step fails                | Return partial result (last successful step's output) with `odo.partial=true`. |
| Parallel child future throws       | That child returns `{ok:false, error}`; others continue. If all fail, fall back to last-successful-step. |
| Classifier LLM fallback times out  | Default to `general` route → remapped to `default` pipeline. |
| Enricher subprocess times out      | Log, skip that enrichment tool, continue generation. |
| Quality-gate scorer fails          | Default score=3 ("neutral"), skip reflection.      |
| ThinkPRM unreachable               | Fall back to heuristic scoring (structure + keyword overlap). |
| ChromaDB / Hybrid RAG unavailable  | Skip, log, continue.                              |
| MCP memory hook fails              | Swallowed silently — must never perturb generation. |
| Client disconnects mid-stream      | BrokenPipeError caught, decision still logged.    |
| pipelines/*.yaml syntax error      | Log warning, treat as empty → route falls back to `default`. |
| Auth token mismatch                | `401 Unauthorized`.                               |
| Rate limit exceeded                | `429 Too Many Requests`.                          |

## Hot paths

Profiling on Qwen3.5-35B-A3B @ 90 tok/s (RTX 5060 Ti):

- classification: 0–1 ms (keyword) or 50-80 ms (LLM fallback, < 1 % of requests)
- pipeline YAML load: 0.1 ms (mtime-cached, else ~2 ms)
- enricher RAG: 500–2000 ms (CPU embeddings via Qwen3-Embedding-0.6B)
- entropy probe: 80–100 ms on port 8081
- FORCE_THINK single-shot: `max_tokens=4096`, ~4 s on kine short queries
- 4-step pipeline (kine): ~28 s total (evidence 4 s + diag 4 s + parallel F2 4 s + dosage 2 s + overhead)
- DVTS k=4 with F1: ~12 s (4 candidates parallel, 6 s ThinkPRM scoring)
- Reflection (worst case): +15 s (score ~2 s + retry ~13 s)
- F6 speculative: +6 s worst case (parallelised)

Open avenues (contributor backlog): `asyncio` rewrite of the main handler
(current `ThreadingMixIn` is limited ~50 concurrent connections),
`prometheus_client` endpoint, pipeline streaming (stream the last-step
partial while earlier steps finalise), first-class MTP via DFlash hook.

<!-- reviewer-notes
Changes applied vs v1:
- Module map LoC cross-checked against `wc -l` on every file in ~/.openclaw/odo/
  (2026-04-24). odo.py now shown as 2465 LoC (was "~2500" — kept ~2500 in
  prose below as a rounded number). classifier.py 324 (was 325).
  pipeline_executor.py 436 (was 437). dvts.py 426 (was 427). All others
  matched exactly. Small enough that ~7700 LoC total remains accurate.
- Priority chain in the "FORCE_THINK and Qwen3.5 Jinja" section: reordered
  to match the actual execution order in `_decide_thinking()` (odo.py:1615+).
  v1 had "Explicit caller" before "Tool calls"; actual code puts tools first.
  Fixed both lists.
- `_decide_thinking()` line range corrected from 1615-1664 to 1615-1663
  (matches the chain's last return at line 1663).
- "pipelines/*.yaml (8 files)" retained — verified: 8 non-backup YAML files
  in ~/.openclaw/odo/pipelines/ on 2026-04-24.
-->
