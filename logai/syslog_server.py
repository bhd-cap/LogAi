"""Syslog listeners (UDP + TCP) with RFC3164 and RFC5424 parsing.

Nothing here is AI. This is the boring, deterministic collection layer —
and it is the part that has to be reliable.
"""
import asyncio
import re
from datetime import datetime, timezone

from . import db
from .fingerprint import fingerprint

# <PRI>VERSION TIMESTAMP HOST APP PROCID MSGID [SD] MSG   (RFC5424)
_RFC5424 = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ver>\d)\s+(?P<ts>\S+)\s+(?P<host>\S+)\s+"
    r"(?P<app>\S+)\s+(?P<procid>\S+)\s+(?P<msgid>\S+)\s+"
    r"(?P<sd>-|\[.*?\](?:\[.*?\])*)\s*(?P<msg>.*)$",
    re.DOTALL,
)

# <PRI>MMM DD HH:MM:SS HOST TAG: MSG   (RFC3164 / BSD)
_RFC3164 = re.compile(
    r"^<(?P<pri>\d{1,3})>\s*(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})?\s*"
    r"(?P<rest>.*)$",
    re.DOTALL,
)

_TAG = re.compile(r"^(?P<host>[\w.\-]+)\s+(?P<app>[\w\-./]+?)(?:\[(?P<pid>\d+)\])?:\s*(?P<msg>.*)$",
                  re.DOTALL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(line: str, source_ip: str = "", transport: str = "udp") -> dict | None:
    """Parse a raw syslog line into an event dict. Returns None for blank input."""
    raw = line.strip()
    if not raw:
        return None

    received = _now()
    pri = 13  # user.notice fallback for non-conforming senders
    host = source_ip or "unknown"
    app = "-"
    msg = raw
    event_at = None

    m = _RFC5424.match(raw)
    if m:
        pri = int(m.group("pri"))
        host = m.group("host") if m.group("host") != "-" else host
        app = m.group("app") if m.group("app") != "-" else "-"
        msg = m.group("msg").strip()
        ts = m.group("ts")
        event_at = None if ts == "-" else ts
    else:
        m = _RFC3164.match(raw)
        if m:
            pri = int(m.group("pri"))
            event_at = m.group("ts")
            rest = (m.group("rest") or "").strip()
            t = _TAG.match(rest)
            if t:
                host = t.group("host")
                app = t.group("app")
                msg = t.group("msg").strip()
            else:
                msg = rest
        else:
            # No PRI at all — still ingest it rather than dropping data.
            msg = raw

    facility, severity = divmod(pri, 8)
    if not 0 <= severity <= 7:
        severity = 6

    fp, template = fingerprint(app, msg)
    return {
        "received_at": received, "event_at": event_at, "host": host, "app": app,
        "facility": facility, "severity": severity, "message": msg[:8000],
        "raw": raw[:8000], "fingerprint": fp, "template": template,
        "source_ip": source_ip, "transport": transport,
    }


class UDPHandler(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr):
        try:
            text = data.decode("utf-8", "replace")
        except Exception:
            return
        for line in text.splitlines():
            ev = parse(line, source_ip=addr[0], transport="udp")
            if ev:
                try:
                    db.insert_event(ev)
                except Exception as exc:  # never let one bad line kill the listener
                    print(f"[syslog] insert failed: {exc}")


async def _tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername")
    ip = peer[0] if peer else ""
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            ev = parse(line.decode("utf-8", "replace"), source_ip=ip, transport="tcp")
            if ev:
                try:
                    db.insert_event(ev)
                except Exception as exc:
                    print(f"[syslog] insert failed: {exc}")
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_listeners(bind: str, udp_port: int, tcp_port: int):
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(UDPHandler, local_addr=(bind, udp_port))
    server = await asyncio.start_server(_tcp_client, bind, tcp_port)
    print(f"[syslog] UDP {bind}:{udp_port}  TCP {bind}:{tcp_port}")
    return server
