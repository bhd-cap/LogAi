"""SQLite storage. One file, no server, survives a laptop reboot."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .config import settings

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  TEXT NOT NULL,
    event_at     TEXT,
    host         TEXT,
    app          TEXT,
    facility     INTEGER,
    severity     INTEGER NOT NULL,
    message      TEXT NOT NULL,
    raw          TEXT,
    fingerprint  TEXT NOT NULL,
    source_ip    TEXT,
    transport    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_fp   ON events(fingerprint);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_sev  ON events(severity);

CREATE TABLE IF NOT EXISTS clusters (
    fingerprint  TEXT PRIMARY KEY,
    template     TEXT NOT NULL,
    app          TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    min_severity INTEGER NOT NULL DEFAULT 7,
    hosts        TEXT NOT NULL DEFAULT '[]',
    score        REAL NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'new',
    sample_event INTEGER
);
CREATE INDEX IF NOT EXISTS idx_clusters_score ON clusters(score DESC);

CREATE TABLE IF NOT EXISTS findings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    provider     TEXT,
    model        TEXT,
    title        TEXT,
    severity     TEXT,
    summary      TEXT,
    probable_cause TEXT,
    remediation  TEXT,
    confidence   TEXT,
    citations    TEXT NOT NULL DEFAULT '[]',
    ok           INTEGER NOT NULL DEFAULT 1,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_fp ON findings(fingerprint);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    score       REAL,
    reason      TEXT,
    acked       INTEGER NOT NULL DEFAULT 0
);
"""

SEVERITY_NAMES = {
    0: "emerg", 1: "alert", 2: "crit", 3: "err",
    4: "warning", 5: "notice", 6: "info", 7: "debug",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    """Thread-local connection. The syslog listener and API run in different threads."""
    if not hasattr(_local, "conn"):
        c = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        _local.conn = c
    return _local.conn


def init_db() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.commit()


def insert_event(ev: dict) -> int:
    """Insert one parsed event and roll it up into its cluster. Returns event id."""
    c = conn()
    cur = c.execute(
        """INSERT INTO events
           (received_at, event_at, host, app, facility, severity, message, raw,
            fingerprint, source_ip, transport)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ev["received_at"], ev.get("event_at"), ev.get("host"), ev.get("app"),
         ev.get("facility"), ev["severity"], ev["message"], ev.get("raw"),
         ev["fingerprint"], ev.get("source_ip"), ev.get("transport")),
    )
    event_id = cur.lastrowid

    row = c.execute("SELECT hosts, count FROM clusters WHERE fingerprint=?",
                    (ev["fingerprint"],)).fetchone()
    host = ev.get("host") or "unknown"

    if row is None:
        c.execute(
            """INSERT INTO clusters
               (fingerprint, template, app, first_seen, last_seen, count,
                min_severity, hosts, sample_event)
               VALUES (?,?,?,?,?,1,?,?,?)""",
            (ev["fingerprint"], ev["template"], ev.get("app"), ev["received_at"],
             ev["received_at"], ev["severity"], json.dumps([host]), event_id),
        )
    else:
        hosts = json.loads(row["hosts"])
        if host not in hosts:
            hosts.append(host)
            hosts = hosts[:50]
        c.execute(
            """UPDATE clusters
               SET last_seen=?, count=count+1,
                   min_severity=MIN(min_severity, ?), hosts=?
               WHERE fingerprint=?""",
            (ev["received_at"], ev["severity"], json.dumps(hosts), ev["fingerprint"]),
        )
    c.commit()
    return event_id


def recent_count(fingerprint: str, minutes: int = 5) -> int:
    c = conn()
    row = c.execute(
        """SELECT COUNT(*) AS n FROM events
           WHERE fingerprint=? AND received_at >= datetime('now', ?)""",
        (fingerprint, f"-{minutes} minutes"),
    ).fetchone()
    return row["n"] if row else 0


def all_clusters() -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM clusters").fetchall()


def update_score(fingerprint: str, score: float) -> None:
    c = conn()
    c.execute("UPDATE clusters SET score=? WHERE fingerprint=?", (score, fingerprint))
    c.commit()


def top_clusters(limit: int = 50, state: Optional[str] = None) -> list[dict]:
    q = "SELECT * FROM clusters"
    params: list[Any] = []
    if state:
        q += " WHERE state=?"
        params.append(state)
    q += " ORDER BY score DESC, count DESC LIMIT ?"
    params.append(limit)
    rows = conn().execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["hosts"] = json.loads(d["hosts"])
        d["severity_name"] = SEVERITY_NAMES.get(d["min_severity"], "?")
        d["finding"] = latest_finding(d["fingerprint"])
        out.append(d)
    return out


def cluster_events(fingerprint: str, limit: int = 25) -> list[dict]:
    rows = conn().execute(
        """SELECT id, received_at, host, app, severity, message
           FROM events WHERE fingerprint=?
           ORDER BY id DESC LIMIT ?""",
        (fingerprint, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["severity_name"] = SEVERITY_NAMES.get(d["severity"], "?")
        out.append(d)
    return out


def recent_events(limit: int = 200, severity_max: int = 7,
                  q: str = "", host: str = "") -> list[dict]:
    sql = "SELECT * FROM events WHERE severity <= ?"
    params: list[Any] = [severity_max]
    if q:
        sql += " AND message LIKE ?"
        params.append(f"%{q}%")
    if host:
        sql += " AND host = ?"
        params.append(host)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn().execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["severity_name"] = SEVERITY_NAMES.get(d["severity"], "?")
        out.append(d)
    return out


def save_finding(f: dict) -> int:
    c = conn()
    cur = c.execute(
        """INSERT INTO findings
           (fingerprint, created_at, provider, model, title, severity, summary,
            probable_cause, remediation, confidence, citations, ok, error)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f["fingerprint"], now(), f.get("provider"), f.get("model"), f.get("title"),
         f.get("severity"), f.get("summary"), f.get("probable_cause"),
         f.get("remediation"), f.get("confidence"),
         json.dumps(f.get("citations", [])), 1 if f.get("ok", True) else 0,
         f.get("error")),
    )
    c.execute("UPDATE clusters SET state='triaged' WHERE fingerprint=?",
              (f["fingerprint"],))
    c.commit()
    return cur.lastrowid


def latest_finding(fingerprint: str) -> Optional[dict]:
    row = conn().execute(
        "SELECT * FROM findings WHERE fingerprint=? ORDER BY id DESC LIMIT 1",
        (fingerprint,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d["citations"])
    return d


def unanalyzed(limit: int) -> list[dict]:
    rows = conn().execute(
        """SELECT c.* FROM clusters c
           LEFT JOIN findings f ON f.fingerprint = c.fingerprint
           WHERE f.id IS NULL
           ORDER BY c.score DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["hosts"] = json.loads(d["hosts"])
        out.append(d)
    return out


def raise_alert(fingerprint: str, score: float, reason: str) -> None:
    c = conn()
    existing = c.execute(
        """SELECT id FROM alerts WHERE fingerprint=? AND acked=0
           AND created_at >= datetime('now','-30 minutes')""",
        (fingerprint,),
    ).fetchone()
    if existing:
        return
    c.execute(
        "INSERT INTO alerts (created_at, fingerprint, score, reason) VALUES (?,?,?,?)",
        (now(), fingerprint, score, reason),
    )
    c.commit()


def open_alerts() -> list[dict]:
    rows = conn().execute(
        """SELECT a.*, c.template, c.app FROM alerts a
           JOIN clusters c ON c.fingerprint=a.fingerprint
           WHERE a.acked=0 ORDER BY a.id DESC LIMIT 50"""
    ).fetchall()
    return [dict(r) for r in rows]


def ack_alert(alert_id: int) -> None:
    c = conn()
    c.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
    c.commit()


def set_state(fingerprint: str, state: str) -> None:
    c = conn()
    c.execute("UPDATE clusters SET state=? WHERE fingerprint=?", (state, fingerprint))
    c.commit()


def stats() -> dict:
    c = conn()
    ev = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
    cl = c.execute("SELECT COUNT(*) n FROM clusters").fetchone()["n"]
    al = c.execute("SELECT COUNT(*) n FROM alerts WHERE acked=0").fetchone()["n"]
    fi = c.execute("SELECT COUNT(*) n FROM findings").fetchone()["n"]
    last5 = c.execute(
        "SELECT COUNT(*) n FROM events WHERE received_at >= datetime('now','-5 minutes')"
    ).fetchone()["n"]
    crit = c.execute(
        "SELECT COUNT(*) n FROM events WHERE severity <= 3"
    ).fetchone()["n"]
    return {"events": ev, "clusters": cl, "open_alerts": al, "findings": fi,
            "events_5m": last5, "err_or_worse": crit}
