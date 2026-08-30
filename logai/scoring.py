"""Deterministic ranking.

This decides WHICH issues rise to the top. No model is consulted.
That matters for three reasons: it is instant, it is free, and it is
explainable to an auditor. The model only ever sees what this ranks highest.
"""
import json
import math
from datetime import datetime, timezone

from . import db

# A failed power supply should not have to wait behind 4,000 info messages.
SEVERITY_WEIGHT = {0: 100, 1: 92, 2: 84, 3: 62, 4: 34, 5: 16, 6: 5, 7: 1}


def _age_minutes(iso: str) -> float:
    try:
        ts = datetime.fromisoformat(iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    except Exception:
        return 9999.0


def score_cluster(row) -> tuple[float, dict]:
    """Return (score, explanation). Explanation is shown in the UI —
    every number on the dashboard can be traced back to a rule."""
    sev = SEVERITY_WEIGHT.get(row["min_severity"], 5)
    count = row["count"] or 1
    hosts = json.loads(row["hosts"]) if isinstance(row["hosts"], str) else row["hosts"]

    volume = min(20.0, math.log10(count + 1) * 12)

    recent = db.recent_count(row["fingerprint"], minutes=5)
    burst = min(25.0, recent * 2.5)

    spread = min(15.0, (len(hosts) - 1) * 5.0) if hosts else 0.0

    age = _age_minutes(row["first_seen"])
    novelty = 12.0 if age <= 60 else (6.0 if age <= 360 else 0.0)

    staleness = -8.0 if _age_minutes(row["last_seen"]) > 1440 else 0.0

    total = sev + volume + burst + spread + novelty + staleness
    total = max(0.0, round(total, 1))

    return total, {
        "severity": sev, "volume": round(volume, 1), "burst": round(burst, 1),
        "host_spread": round(spread, 1), "novelty": novelty,
        "staleness": staleness, "recent_5m": recent,
    }


def rescore_all(alert_threshold: float = 80.0) -> int:
    """Rescore every cluster and raise alerts on anything above threshold."""
    n = 0
    for row in db.all_clusters():
        score, why = score_cluster(row)
        db.update_score(row["fingerprint"], score)
        n += 1
        if score >= alert_threshold and row["state"] != "resolved":
            reason = (f"score {score} "
                      f"(sev={why['severity']}, burst={why['burst']}, "
                      f"hosts={why['host_spread']})")
            db.raise_alert(row["fingerprint"], score, reason)
    return n
