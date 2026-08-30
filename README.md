# LogAi

A working syslog server, triage dashboard, and AI review agent — in about 1,200 lines
of Python and one HTML file. Built as a walkthrough demo for the Midsize Enterprise
Summit session *"The Work Your Team Is Never Going to Do."*

It ingests syslog, groups it deterministically, ranks issues without any model
involved, and then asks an AI agent to review only the top of that list and draft
remediation steps. It runs against a local model, so no log data has to leave
your network.

```
devices ──syslog──▶ parser ──▶ fingerprint ──▶ SQLite
                                                 │
                                     deterministic scoring
                                                 │
                                    ┌────────────┴────────────┐
                                    │                         │
                              dashboard              AI agent (top N only)
                                    │                         │
                                    └────── findings ◀────────┘
                                            (cited, never executed)
```

---

## The design argument

Three things are enforced in code rather than in a prompt, and they are the point
of the demo:

**Deterministic first, model second.** Fingerprinting, grouping, and ranking are
plain Python and regex. They run on every event, cost nothing, and are fully
explainable — the dashboard shows the score breakdown for every issue. The model
never decides what is important. It only reviews what the ranking already surfaced.

**Bounded model exposure.** The agent sees the top N clusters (default 5) with a
handful of sample events each. Not the raw log stream. This is what keeps the
workload affordable and what makes a small local model viable.

**Propose, never execute.** Findings contain draft remediation steps for a human
to review. There is no code path from a finding to a device. Every finding must
cite the event IDs it was derived from; anything that cannot cite is stored and
labelled `[unverified]` with confidence forced to low.

---

## Quick start

```bash
git clone https://github.com/bhd-cap/LogAi.git
cd LogAi
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # optional — defaults work
python run.py
```

Open <http://localhost:8080>.

In a second terminal, generate demo traffic:

```bash
python tools/generate_logs.py --count 600
```

You should see ~600 events collapse into roughly 20 issues, with a failed power
supply, an SSH brute-force attempt, and a degrading fiber optic transceiver ranked
above several thousand routine log lines.

Click **Run AI triage** to have the agent review the top of the list.

### Sending real logs

Point any device at the host on UDP or TCP 5514:

```
! Cisco IOS
logging host 10.0.0.50 transport udp port 5514

# Linux rsyslog — /etc/rsyslog.d/99-logai.conf
*.* @10.0.0.50:5514
```

To listen on the standard port 514 you need privileges:

```bash
sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))
SYSLOG_UDP_PORT=514 SYSLOG_TCP_PORT=514 python run.py
```

---

## AI providers

One env var switches backends. The model is a file behind a stable API — swapping
it should never be a code change.

| Provider | `LOGAI_PROVIDER` | Key needed | Notes |
|---|---|---|---|
| Ollama | `ollama` | no | Default. Easiest local path. |
| vLLM | `vllm` | no | Higher throughput local serving. |
| OpenRouter | `openrouter` | yes | Many models, one key. |
| OpenAI | `openai` | yes | |
| Anthropic | `anthropic` | yes | Uses `/v1/messages`. |
| Offline | `offline` | no | Built-in rules fixture. No model, no network. |
| Disabled | `none` | — | Ranking and dashboard still work. |

### Offline mode

`LOGAI_PROVIDER=offline` runs the entire agent path — prompt assembly, JSON
parsing, citation enforcement, finding storage, dashboard rendering — using a
deterministic fixture instead of a model. Nothing is downloaded and nothing is
called.

It exists so the demo survives a conference room with no wifi and a laptop with
no GPU. Findings produced this way are labelled `offline:rules` in the dashboard
and their confidence is forced to `low`, so they can never be mistaken for model
output.

```bash
LOGAI_PROVIDER=offline python run.py
```

```bash
# Local — nothing leaves the building
ollama pull qwen2.5:14b
LOGAI_PROVIDER=ollama LOGAI_MODEL=qwen2.5:14b python run.py

# Hosted, for comparison during the demo
LOGAI_PROVIDER=anthropic LOGAI_MODEL=claude-sonnet-4-6 LOGAI_API_KEY=sk-ant-... python run.py
```

Models in the 8–14B class handle this workload well. The task is summarization and
correlation over short text, not deep reasoning — which is exactly why it is a
reasonable local candidate.

---

## Walkthrough — what to look at, in order

1. **`logai/fingerprint.py`** — the whole "AI is not magic" argument in 40 lines.
   Regex normalization turns 600 log lines into 20 templates. No GPU.
2. **`logai/scoring.py`** — why an issue is at the top. Severity, volume, burst,
   host spread, novelty. Every number is traceable, and the dashboard shows the
   breakdown. This is what a threshold alert cannot do and does not need a model for.
3. **`logai/agent.py`** — the citation enforcement block. The model is asked for
   `cited_event_ids`; anything it returns that is not in the sample set is dropped,
   and a finding with no valid citations is marked unverified.
4. **`logai/providers.py`** — five backends, two request shapes, one interface.
5. **The dashboard** — expand any issue. The score bar shows the deterministic
   inputs; the finding below it shows what the model added on top.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status, syslog ports, AI backend reachability |
| GET | `/api/clusters` | Ranked issues (rescores on call) |
| GET | `/api/clusters/{fp}` | Detail, events, score breakdown |
| POST | `/api/clusters/{fp}/state` | `new` / `triaged` / `acked` / `resolved` |
| GET | `/api/events` | Raw event stream, filterable |
| GET | `/api/alerts` | Open alerts above threshold |
| POST | `/api/alerts/{id}/ack` | Acknowledge |
| POST | `/api/analyze` | Run agent over top N unanalyzed |
| POST | `/api/analyze/{fp}` | Analyze one issue |
| GET | `/api/config` | Effective configuration |

---

## Docker

```bash
docker compose up --build
python tools/generate_logs.py --port 5514
```

Compose assumes Ollama is running on the host and reaches it via
`host.docker.internal`.

---

## Scoring

```
score = severity_weight        # emerg 100 → debug 1
      + volume                 # log10(count), capped at 20
      + burst                  # events in last 5 min, capped at 25
      + host_spread            # distinct hosts affected, capped at 15
      + novelty                # first seen within the hour: +12
      + staleness              # nothing for 24h: −8
```

Anything at or above `ALERT_SCORE_THRESHOLD` (default 80) raises an alert,
deduplicated to one per fingerprint per 30 minutes.

Tune the weights in `logai/scoring.py`. They are opinions, not physics — a
transceiver degrading slowly matters more in some environments than others.

---

## Limitations — read before deploying anywhere real

- **No authentication.** The dashboard and API are wide open. Put it behind
  something before exposing it.
- **Syslog is unauthenticated by design.** Anything that can reach the port can
  inject events. Treat every ingested field as untrusted input — it reaches a
  model, and a crafted log line is a prompt injection vector. That is the reason
  the agent has no write path.
- **SQLite suits a demo and a small fleet.** Past a few million events, move to
  Postgres and add retention.
- **No TLS, no RELP, no log forwarding.**
- **Findings are drafts.** A model can be confidently wrong about a root cause.
  The citations exist so a human can check the evidence in about ten seconds.

## Licence

MIT.
