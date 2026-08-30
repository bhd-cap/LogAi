"""One interface, five backends.

Ollama, vLLM, OpenRouter and OpenAI all speak the OpenAI chat-completions
shape, so they share a client. Anthropic uses its own /v1/messages shape.
Swapping providers is an env var, not a code change — which is the whole
point when the model you deploy today is obsolete in nine months.
"""
import json
import re
from dataclasses import dataclass

import httpx

from .config import settings

OPENAI_COMPATIBLE = {"ollama", "vllm", "openrouter", "openai"}


def _offline_finding(user: str) -> str:
    """Deterministic stand-in for a model.

    Exists so the agent path can be demonstrated on a laptop with no GPU,
    no API key, and no network — a conference room, for example. It is
    obviously not intelligence; it is a fixture. Findings produced this way
    are labelled 'offline' in the dashboard so nobody mistakes them for
    model output.
    """
    ids = [int(m) for m in re.findall(r"\[id=(\d+)\]", user)][:3]
    low = user.lower()

    if "power supply" in low or "psu" in low:
        title, sev = "Redundant power supply failed — chassis running unprotected", "critical"
        cause = "PSU hardware failure or loss of the upstream feed."
        steps = ["Run: show environment power",
                 "Check the B-side feed, breaker, and PDU outlet",
                 "Reseat the module; RMA if it still reports FAIL"]
    elif "rx power" in low or "transceiver" in low:
        title, sev = "Optical receive power below threshold on multiple links", "high"
        cause = "Contaminated or bent fibre, or an aging transceiver."
        steps = ["Run: show interfaces transceiver detail",
                 "Clean and reseat both ends of the affected fibre",
                 "Compare against the same optic type on a healthy port"]
    elif "failed password" in low or "invalid user" in low:
        title, sev = "Repeated failed authentication from external address", "high"
        cause = "Credential stuffing or scanning against an exposed SSH service."
        steps = ["Confirm whether the source should reach this host at all",
                 "Verify password auth is disabled and keys are enforced",
                 "Rate-limit or block the source at the edge"]
    elif "degraded" in low or "latency" in low:
        title, sev = "Storage array degraded — rebuild or latency impact", "high"
        cause = "Failed member disk, or a rebuild consuming array throughput."
        steps = ["Identify the failed member and confirm a spare is in use",
                 "Check host-side latency against the array's own counters",
                 "Schedule replacement before a second disk fails"]
    elif "hold time expired" in low or "bgp" in low:
        title, sev = "BGP session dropped on hold timer", "high"
        cause = "Link instability, MTU mismatch, or control-plane load on a peer."
        steps = ["Run: show ip bgp neighbors <peer>",
                 "Check interface errors and CPU on both ends",
                 "Verify hold/keepalive timers match the peer"]
    else:
        title, sev = "Recurring condition needs review", "medium"
        cause = "Insufficient evidence to determine a cause from these events."
        steps = ["Review the cited events against change records",
                 "Confirm whether the pattern correlates with a maintenance window"]

    return json.dumps({
        "title": title, "severity": sev,
        "summary": "Generated offline from the deterministic ranking inputs. "
                   "No model was consulted for this finding.",
        "probable_cause": cause, "remediation": steps,
        "confidence": "low", "cited_event_ids": ids,
    })


@dataclass
class LLMResponse:
    ok: bool
    text: str = ""
    error: str = ""
    provider: str = ""
    model: str = ""


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    p = settings.provider
    if p == "anthropic":
        h["x-api-key"] = settings.api_key
        h["anthropic-version"] = "2023-06-01"
    elif p == "openrouter":
        h["Authorization"] = f"Bearer {settings.api_key}"
        h["HTTP-Referer"] = "https://github.com/bhd-cap/LogAi"
        h["X-Title"] = "LogAi"
    elif settings.api_key:
        h["Authorization"] = f"Bearer {settings.api_key}"
    return h


async def complete(system: str, user: str) -> LLMResponse:
    """Send one prompt. Returns raw text; callers parse it."""
    p, model = settings.provider, settings.model

    if p == "offline":
        return LLMResponse(True, _offline_finding(user), provider="offline", model="rules")

    if not settings.ai_enabled():
        return LLMResponse(False, error=f"provider '{p}' not configured", provider=p, model=model)

    base = settings.resolved_base_url().rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            if p == "anthropic":
                payload = {
                    "model": model,
                    "max_tokens": settings.max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
                r = await client.post(f"{base}/messages", headers=_headers(), json=payload)
                r.raise_for_status()
                data = r.json()
                parts = [b.get("text", "") for b in data.get("content", [])
                         if b.get("type") == "text"]
                return LLMResponse(True, "\n".join(parts).strip(), provider=p, model=model)

            if p in OPENAI_COMPATIBLE:
                payload = {
                    "model": model,
                    "max_tokens": settings.max_tokens,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                }
                r = await client.post(f"{base}/chat/completions",
                                      headers=_headers(), json=payload)
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                return LLMResponse(True, (text or "").strip(), provider=p, model=model)

            return LLMResponse(False, error=f"unknown provider '{p}'", provider=p, model=model)

    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return LLMResponse(False, error=f"HTTP {e.response.status_code}: {body}",
                           provider=p, model=model)
    except httpx.ConnectError:
        return LLMResponse(False, error=f"cannot reach {base} — is the model server running?",
                           provider=p, model=model)
    except Exception as e:
        return LLMResponse(False, error=f"{type(e).__name__}: {e}", provider=p, model=model)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_json(text: str) -> dict | None:
    """Models wrap JSON in prose and code fences no matter how you ask.
    Try fenced content, then the first balanced object."""
    if not text:
        return None
    for candidate in ([m.group(1) for m in _FENCE.finditer(text)] + [text]):
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        start = candidate.find("{")
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(candidate[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


async def health() -> dict:
    """Is the configured backend reachable? Shown in the dashboard header."""
    p = settings.provider
    base = settings.resolved_base_url().rstrip("/")
    if p == "offline":
        return {"provider": "offline", "model": "rules", "ok": True,
                "detail": "deterministic fixture — no model, no network"}
    if not settings.ai_enabled():
        return {"provider": p, "model": settings.model, "ok": False,
                "detail": "not configured (set LOGAI_PROVIDER / LOGAI_API_KEY)"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if p in ("ollama", "vllm"):
                r = await client.get(f"{base}/models")
                return {"provider": p, "model": settings.model, "ok": r.status_code < 400,
                        "detail": f"{base} reachable"}
            return {"provider": p, "model": settings.model, "ok": True,
                    "detail": f"{base} (key present)"}
    except Exception as e:
        return {"provider": p, "model": settings.model, "ok": False,
                "detail": f"{type(e).__name__}: {e}"}
