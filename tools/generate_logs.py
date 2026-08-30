#!/usr/bin/env python3
"""Demo traffic generator.

Sends realistic network + server syslog to LogAi. The mix is deliberate:
a large volume of routine noise, and a handful of planted real problems
buried inside it. That is the point of the demo — the planted issues are
the ones nobody gets to until they become an outage.

Usage:
    python tools/generate_logs.py                 # 400 events, then a burst
    python tools/generate_logs.py --count 2000
    python tools/generate_logs.py --host 10.0.0.5 --port 514
    python tools/generate_logs.py --stream        # continuous
"""
import argparse
import random
import socket
import time
from datetime import datetime

HOSTS = ["core-sw-01", "core-sw-02", "dist-sw-11", "dist-sw-12", "edge-fw-01",
         "edge-fw-02", "esx-host-03", "esx-host-04", "app-web-01", "app-web-02",
         "db-prod-01", "wlc-01"]

# (facility.severity pri, app, message template, weight)
NOISE = [
    (189, "sshd", "Accepted publickey for svc_monitor from 10.20.{a}.{b} port {p} ssh2", 14),
    (190, "cron", "(root) CMD (/usr/lib/sysstat/sa1 1 1)", 12),
    (190, "systemd", "Started Session {n} of user backup.", 10),
    (189, "snmpd", "Connection from UDP: [10.20.30.{b}]:{p}->[10.20.30.9]:161", 12),
    (189, "kernel", "TCP: request_sock_TCP: Possible SYN flooding on port 443. Sending cookies.", 3),
    (190, "dhcpd", "DHCPACK on 10.42.{a}.{b} to {mac} via vlan{v}", 11),
    (189, "named", "client 10.20.{a}.{b}#{p}: query: internal.corp IN A + (10.20.30.9)", 9),
    (188, "LINEPROTO", "Line protocol on Interface GigabitEthernet1/0/{n}, changed state to up", 6),
    (190, "nginx", "10.20.{a}.{b} - - \"GET /healthz HTTP/1.1\" 200 12 \"-\" \"kube-probe/1.29\"", 13),
    (189, "vpxa", "Completed heartbeat to vCenter, latency {n}ms", 7),
]

# The planted problems. Low volume, high consequence.
ISSUES = [
    (185, "PLATFORM", "Power supply 2 in slot 1 has failed. Redundancy lost.", 2),
    (187, "SFF8472", "Transceiver in GigabitEthernet1/0/{n}: Rx power {rx} dBm below "
                     "low warning threshold -14.4 dBm", 3),
    (187, "kernel", "megaraid_sas: VD 00/0 is now DEGRADED, rebuild required", 2),
    (188, "BGP", "neighbor 172.16.{a}.{b} Down - hold time expired", 3),
    (187, "hostd", "Device naa.60060160{hx} performance has deteriorated. "
                   "I/O latency increased from 4 ms to {lat} ms", 3),
    (186, "auditd", "Failed password for invalid user admin from 203.0.113.{b} port {p} ssh2", 5),
    (188, "STP", "Received BPDU on port GigabitEthernet1/0/{n} with inconsistent VLAN", 2),
    (187, "ntpd", "no servers reachable, clock unsynchronised for {n} minutes", 2),
]


def fill(tpl: str) -> str:
    return (tpl.replace("{a}", str(random.randint(1, 40)))
               .replace("{b}", str(random.randint(2, 250)))
               .replace("{p}", str(random.randint(20000, 65000)))
               .replace("{n}", str(random.randint(1, 48)))
               .replace("{v}", str(random.choice([10, 20, 30, 100, 300])))
               .replace("{rx}", f"-{random.uniform(15.2, 22.8):.1f}")
               .replace("{lat}", str(random.randint(180, 2400)))
               .replace("{hx}", "%012x" % random.getrandbits(48))
               .replace("{mac}", ":".join("%02x" % random.randint(0, 255) for _ in range(6))))


def build(pri: int, app: str, tpl: str, host: str | None = None) -> bytes:
    host = host or random.choice(HOSTS)
    ts = datetime.now().strftime("%b %e %H:%M:%S")
    return f"<{pri}>{ts} {host} {app}[{random.randint(100, 9999)}]: {fill(tpl)}".encode()


def weighted(pool):
    return random.choices(pool, weights=[w for *_, w in pool], k=1)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5514)
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--rate", type=float, default=400.0, help="events per second")
    ap.add_argument("--stream", action="store_true", help="run until interrupted")
    ap.add_argument("--no-burst", action="store_true")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    delay = 1.0 / max(args.rate, 1)
    target = (args.host, args.port)
    sent = 0

    def emit(n, pool):
        nonlocal sent
        for _ in range(n):
            pri, app, tpl, _ = weighted(pool)
            sock.sendto(build(pri, app, tpl), target)
            sent += 1
            time.sleep(delay)

    print(f"→ {args.host}:{args.port}")
    try:
        if args.stream:
            print("streaming (ctrl-c to stop)…")
            while True:
                emit(40, NOISE)
                emit(2, ISSUES)
        else:
            print(f"sending {args.count} routine events…")
            emit(int(args.count * 0.9), NOISE)
            print("seeding planted issues…")
            emit(max(8, int(args.count * 0.1)), ISSUES)

            if not args.no_burst:
                # A burst on one host — this is what the burst score is for.
                print("firing a correlated burst on core-sw-01…")
                for _ in range(28):
                    sock.sendto(build(185, "PLATFORM",
                                      "Power supply 2 in slot 1 has failed. Redundancy lost.",
                                      host="core-sw-01"), target)
                    sent += 1
                    time.sleep(delay)
                # Routine traffic keeps flowing after the burst — which is exactly
                # why the burst scrolls off the screen before anyone reads it.
                emit(60, NOISE)
            print(f"done — {sent} events sent")
    except KeyboardInterrupt:
        print(f"\nstopped — {sent} events sent")


if __name__ == "__main__":
    main()
