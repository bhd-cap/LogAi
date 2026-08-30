"""REST API + dashboard host."""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent, db, providers, scoring
from .config import settings

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="LogAi", version="1.0",
              description="Syslog ingestion, deterministic triage, and AI-assisted review.")


@app.on_event("startup")
def _startup():
    db.init_db()


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "syslog": {"udp": settings.syslog_udp_port, "tcp": settings.syslog_tcp_port},
        "ai": await providers.health(),
        "stats": db.stats(),
    }


@app.get("/api/stats")
def stats():
    return db.stats()


@app.get("/api/clusters")
def clusters(limit: int = Query(50, ge=1, le=500), state: str = ""):
    scoring.rescore_all(settings.alert_score_threshold)
    return db.top_clusters(limit=limit, state=state or None)


@app.get("/api/clusters/{fingerprint}")
def cluster_detail(fingerprint: str):
    rows = [c for c in db.top_clusters(limit=1000) if c["fingerprint"] == fingerprint]
    if not rows:
        raise HTTPException(404, "cluster not found")
    c = rows[0]
    c["events"] = db.cluster_events(fingerprint, limit=50)
    for row in db.all_clusters():
        if row["fingerprint"] == fingerprint:
            _, c["score_breakdown"] = scoring.score_cluster(row)
            break
    return c


@app.post("/api/clusters/{fingerprint}/state")
def set_state(fingerprint: str, state: str = Query(..., pattern="^(new|triaged|acked|resolved)$")):
    db.set_state(fingerprint, state)
    return {"ok": True, "fingerprint": fingerprint, "state": state}


@app.get("/api/events")
def events(limit: int = Query(200, ge=1, le=2000), severity_max: int = Query(7, ge=0, le=7),
           q: str = "", host: str = ""):
    return db.recent_events(limit=limit, severity_max=severity_max, q=q, host=host)


@app.get("/api/alerts")
def alerts():
    return db.open_alerts()


@app.post("/api/alerts/{alert_id}/ack")
def ack(alert_id: int):
    db.ack_alert(alert_id)
    return {"ok": True}


@app.post("/api/analyze")
async def analyze(limit: int = Query(0, ge=0, le=25)):
    """Run the agent across the top-ranked unanalyzed clusters."""
    scoring.rescore_all(settings.alert_score_threshold)
    return await agent.run_triage(limit or None)


@app.post("/api/analyze/{fingerprint}")
async def analyze_one(fingerprint: str):
    result = await agent.analyze_cluster(fingerprint)
    if not result.get("ok"):
        return JSONResponse(status_code=502, content=result)
    return result


@app.get("/api/config")
def config():
    return {
        "provider": settings.provider,
        "model": settings.model,
        "base_url": settings.resolved_base_url(),
        "ai_enabled": settings.ai_enabled(),
        "analyze_top_n": settings.analyze_top_n,
        "alert_threshold": settings.alert_score_threshold,
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
