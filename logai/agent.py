"""The agent.

Three rules are enforced in code, not in the prompt:
  1. It only ever sees clusters the deterministic scorer ranked highest.
  2. Every finding must cite the event IDs it was derived from, or it is
     marked unverified.
  3. It proposes. It never executes. There is no code path from a finding
     to a device.
"""
import asyncio
import json

from . import db, providers
from .config import settings

SYSTEM = """You are a senior network and systems operations engineer reviewing \
grouped log data. You are triaging, not chatting.

Rules:
- Base every claim on the events provided. Do not invent hostnames, interfaces, \
versions, or error codes that are not present.
- If the evidence is insufficient to determine a cause, say so and set \
confidence to "low". That is a valid and useful answer.
- Remediation must be concrete diagnostic or corrective STEPS a human will \
review and run. Never claim you have performed an action.
- Cite the event IDs you used.

Respond with ONLY a JSON object, no prose and no code fences:
{
  "title": "short imperative summary, under 70 characters",
  "severity": "critical|high|medium|low",
  "summary": "2-3 sentences on what is happening",
  "probable_cause": "most likely cause, or 'insufficient evidence' ",
  "remediation": ["step 1", "step 2", "step 3"],
  "confidence": "high|medium|low",
  "cited_event_ids": [1, 2, 3]
}"""


def build_prompt(cluster: dict, events: list[dict], why: dict | None = None) -> str:
    lines = [
        "## Log cluster under review",
        f"Template: {cluster['template']}",
        f"Application/process: {cluster.get('app') or 'unknown'}",
        f"Syslog severity: {db.SEVERITY_NAMES.get(cluster['min_severity'], '?')} "
        f"({cluster['min_severity']})",
        f"Occurrences: {cluster['count']}",
        f"Affected hosts ({len(cluster['hosts'])}): {', '.join(cluster['hosts'][:12])}",
        f"First seen: {cluster['first_seen']}   Last seen: {cluster['last_seen']}",
    ]
    if why:
        lines.append(f"Deterministic rank inputs: {json.dumps(why)}")
    lines.append("\n## Sample events (cite these IDs)")
    for e in events:
        lines.append(f"[id={e['id']}] {e['received_at']} {e['host']} "
                     f"{e.get('app') or '-'} ({e['severity_name']}): {e['message']}")
    lines.append("\nReturn the JSON object described in your instructions.")
    return "\n".join(lines)


async def analyze_cluster(fingerprint: str) -> dict:
    """Analyze one cluster. Always writes a finding row, success or failure,
    so the dashboard can show what happened."""
    rows = [c for c in db.top_clusters(limit=500) if c["fingerprint"] == fingerprint]
    if not rows:
        return {"ok": False, "error": "cluster not found"}
    cluster = rows[0]

    events = db.cluster_events(fingerprint, limit=settings.samples_per_cluster)
    if not events:
        return {"ok": False, "error": "no events for cluster"}

    prompt = build_prompt(cluster, events)
    resp = await providers.complete(SYSTEM, prompt)

    if not resp.ok:
        db.save_finding({
            "fingerprint": fingerprint, "provider": resp.provider, "model": resp.model,
            "title": "Analysis failed", "severity": "low",
            "summary": "The model backend could not be reached or returned an error.",
            "probable_cause": "", "remediation": "", "confidence": "low",
            "citations": [], "ok": False, "error": resp.error,
        })
        return {"ok": False, "error": resp.error}

    data = providers.parse_json(resp.text)
    if not data:
        db.save_finding({
            "fingerprint": fingerprint, "provider": resp.provider, "model": resp.model,
            "title": "Unparseable model response", "severity": "low",
            "summary": resp.text[:600], "probable_cause": "", "remediation": "",
            "confidence": "low", "citations": [], "ok": False,
            "error": "response was not valid JSON",
        })
        return {"ok": False, "error": "response was not valid JSON"}

    # --- citation enforcement -------------------------------------------
    valid_ids = {e["id"] for e in events}
    cited = [i for i in (data.get("cited_event_ids") or []) if i in valid_ids]
    unverified = not cited

    remediation = data.get("remediation") or []
    if isinstance(remediation, list):
        remediation = "\n".join(f"{i}. {s}" for i, s in enumerate(remediation, 1))

    title = str(data.get("title") or "Untitled finding")[:200]
    if unverified:
        title = f"[unverified] {title}"

    finding = {
        "fingerprint": fingerprint,
        "provider": resp.provider, "model": resp.model,
        "title": title,
        "severity": str(data.get("severity") or "low").lower(),
        "summary": str(data.get("summary") or ""),
        "probable_cause": str(data.get("probable_cause") or ""),
        "remediation": remediation,
        "confidence": "low" if unverified else str(data.get("confidence") or "low").lower(),
        "citations": cited,
        "ok": True,
        "error": "no valid citations returned" if unverified else None,
    }
    db.save_finding(finding)
    return {"ok": True, "finding": finding}


async def run_triage(limit: int | None = None) -> dict:
    """Analyze the top N unanalyzed clusters, ranked deterministically."""
    limit = limit or settings.analyze_top_n
    targets = db.unanalyzed(limit)
    if not targets:
        return {"analyzed": 0, "results": [], "note": "nothing new to analyze"}

    results = []
    for c in targets:
        r = await analyze_cluster(c["fingerprint"])
        results.append({"fingerprint": c["fingerprint"],
                        "template": c["template"][:90],
                        "ok": r.get("ok"), "error": r.get("error")})
        await asyncio.sleep(0.2)  # be polite to local model servers
    return {"analyzed": len(results), "results": results}
