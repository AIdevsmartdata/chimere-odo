"""
pre_act.py — Plan-first prompting for Chimère ODO (arXiv 2505.09970).

Pattern: before any heavy generation (routes kine / research), produce a short
numbered plan (3-5 steps) in no-think mode, then prepend it to the user's
system context so the main LLM call executes against an explicit plan rather
than free-form ReAct.

Empirical gain (Pre-Act paper): +7pp task completion over vanilla ReAct on
policy-heavy benchmarks. Combined with the Anthropic `think` tool (+54% policy
adherence) we get "Plan-then-Think-then-Act" which is the 2026 SOTA.

Activation: `pre_act.enabled: true` in the pipeline YAML (kine.yaml,
research.yaml). ODO reads the flag and calls `plan()` before the main request.

Backend: the no-think proxy on 127.0.0.1:8086 (forwards to llama-server 8081
with `enable_thinking=false`). If unavailable, falls back to the regular
backend with `enable_thinking=false` in the payload.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
from urllib.parse import urlparse

log = logging.getLogger("odo.pre_act")

# ── Config ──────────────────────────────────────────────────────────────────
DEFAULT_NOTHINK_URL = os.environ.get("PRE_ACT_BACKEND", "http://127.0.0.1:8086")
FALLBACK_URL = os.environ.get("ODO_BACKEND", "http://127.0.0.1:8081")
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMP = 0.3
DEFAULT_TIMEOUT = 30  # seconds; plan must be cheap

PLAN_PROMPT = (
    "Before answering, produce a numbered plan of 3 to 5 concrete steps needed "
    "to solve the user's task. Keep it terse (one sentence per step). Do NOT "
    "execute the steps yet; only plan.\n\n"
    "Format EXACTLY:\n"
    "1. <step one>\n"
    "2. <step two>\n"
    "3. <step three>\n"
    "...\n\n"
    "Do not add preamble, commentary, or epilogue outside the numbered list."
)


# ── HTTP helper ─────────────────────────────────────────────────────────────

def _post_chat(url: str, payload: dict, timeout: int) -> dict | None:
    try:
        parsed = urlparse(url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        body = json.dumps(payload).encode()
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            log.warning("pre_act: backend %s returned %s: %s", url, resp.status, raw[:200])
            return None
        return json.loads(raw)
    except Exception as e:
        log.warning("pre_act: backend %s error: %s", url, e)
        return None
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass


# ── Public API ──────────────────────────────────────────────────────────────

def plan(
    user_query: str,
    *,
    system_prompt: str | None = None,
    backend_url: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMP,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Generate a numbered plan for `user_query` (3-5 steps).

    Returns the raw plan text (empty string if planning failed — caller should
    fall back to the original pipeline without a plan). The plan is produced
    in no-think mode for latency; the main generation afterwards can still
    think while executing the plan.
    """
    if not user_query or len(user_query) < 8:
        # Tiny queries don't benefit from planning
        return ""

    sys_parts = []
    if system_prompt:
        sys_parts.append(system_prompt.strip())
    sys_parts.append(PLAN_PROMPT)
    system_content = "\n\n".join(sys_parts)

    payload = {
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_query},
        ],
        "temperature": temperature,
        "top_p": 0.9,
        "top_k": 20,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    for url in [backend_url or DEFAULT_NOTHINK_URL, FALLBACK_URL]:
        result = _post_chat(url, payload, timeout)
        if result is None:
            continue
        try:
            content = result["choices"][0]["message"]["content"] or ""
            content = content.strip()
            if content:
                return content
        except (KeyError, IndexError, TypeError) as e:
            log.warning("pre_act: malformed response from %s: %s", url, e)
            continue
    return ""


def inject_plan_into_payload(payload: dict, plan_text: str) -> dict:
    """Return a new payload with the plan prepended to the system message.

    Does not mutate the input. If the plan is empty, returns payload unchanged.
    """
    if not plan_text:
        return payload
    out = dict(payload)
    messages = list(out.get("messages", []))
    plan_block = (
        "[Pre-Act plan — follow these steps to answer]\n"
        f"{plan_text}\n\n"
        "Execute the plan. If a step requires a tool, call it; otherwise "
        "reason directly. State which step you are on as you work."
    )
    if messages and messages[0].get("role") == "system":
        messages[0] = {
            **messages[0],
            "content": f"{messages[0].get('content', '').rstrip()}\n\n{plan_block}",
        }
    else:
        messages.insert(0, {"role": "system", "content": plan_block})
    out["messages"] = messages
    return out


def should_pre_act(pipeline: dict) -> bool:
    """Check the pipeline YAML for `pre_act.enabled: true`."""
    pa = pipeline.get("pre_act") if isinstance(pipeline, dict) else None
    if not isinstance(pa, dict):
        return False
    return bool(pa.get("enabled", False))


def pre_act_params(pipeline: dict) -> dict:
    """Extract planner params from the pipeline YAML (with defaults)."""
    pa = pipeline.get("pre_act", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(pa, dict):
        pa = {}
    return {
        "max_tokens": int(pa.get("planner_max_tokens", DEFAULT_MAX_TOKENS)),
        "temperature": float(pa.get("planner_temp", DEFAULT_TEMP)),
        "timeout": int(pa.get("planner_timeout", DEFAULT_TIMEOUT)),
    }


def run(user_query: str, pipeline: dict, *, system_prompt: str | None = None) -> str:
    """One-shot: if pre-act enabled in `pipeline`, generate a plan; else ''."""
    if not should_pre_act(pipeline):
        return ""
    params = pre_act_params(pipeline)
    return plan(user_query, system_prompt=system_prompt, **params)
